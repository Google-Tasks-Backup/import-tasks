import logging
import os
import pickle
import sys
#import urllib

from apiclient import discovery
from apiclient.oauth2client import appengine
from apiclient.oauth2client import client

from google.appengine.api import apiproxy_stub_map
from google.appengine.api import mail
from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp import util
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.runtime import apiproxy_errors
from google.appengine.runtime import DeadlineExceededError
from google.appengine.api import urlfetch_errors
from google.appengine.api import mail
from google.appengine.api.app_identity import get_application_id
from google.appengine.api import logservice # To flush logs
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers

# Import from error so that we can process HttpError
from apiclient import errors as apiclient_errors



logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True

import httplib2

import datetime
from datetime import timedelta
import time
import math
import csv

import model
import settings
import appversion # appversion.version is set before the upload process to keep the version number consistent
import shared # Code whis is common between import-tasks.py and worker.py
import constants
from shared import DailyLimitExceededError

# Orig __author__ = "dwightguth@google.com (Dwight Guth)"
__author__ = "julie.smith.1999@gmail.com (Julie Smith)"

class ImportJobState(object):

    prev_tasklist_ids = {}
    parents_ids = [''] # Level 0 tasks don't have a parent, so level zero parent ID is always an empty string
    sibling_ids = []
    
    prev_tasklist_name = None
    prev_depth = 0
    
    sibling_id = ''
    parent_id = ''
    tasklist_id = ''

    data_row_num = 0
    num_of_imported_tasks = 0
    num_tasklists = 0    

    
def _log_job_state(import_job_state):
    # DEBUG: TODO: Only log job state for test users
    logging.debug("Job state:" +
        "\n    prev_tasklist_ids = " + str(import_job_state.prev_tasklist_ids) +
        "\n    parents_ids = " + str(import_job_state.parents_ids) +
        "\n    sibling_ids = " + str(import_job_state.sibling_ids) +
        "\n    prev_tasklist_name = '" + str(import_job_state.prev_tasklist_name) +
        "'\n    prev_depth = " + str(import_job_state.prev_depth) +
        "\n    sibling_id = " + str(import_job_state.sibling_id) +
        "\n    parent_id = " + str(import_job_state.parent_id) +
        "\n    tasklist_id = " + str(import_job_state.tasklist_id) +
        "\n    data_row_num = " + str(import_job_state.data_row_num) +
        "\n    num_of_imported_tasks = " + str(import_job_state.num_of_imported_tasks) +
        "\n    num_tasklists = " + str(import_job_state.num_tasklists))
    logservice.flush()
    
    

class ProcessTasksWorker(webapp.RequestHandler):
    """ Process tasks according to data in the ImportTasksJob entity """

    prev_progress_timestamp = datetime.datetime.now()
    
    credentials = None
    user_email = None
    is_test_user = False
    tasks_svc = None
    tasklists_svc = None
    blob_info = None
    default_tasklist_id = None
    
    process_tasks_job = None
    import_job_state = None
    
    
    def post(self):
        fn_name = "ProcessTasksWorker.post(): "
        
        try:
            logging.debug(fn_name + "<start> (app version %s)" %appversion.version)
            logservice.flush()

            client_id, client_secret, user_agent, app_title, project_name, host_msg = shared.get_settings(self.request.host)
            
            
            self.user_email = self.request.get(settings.TASKS_QUEUE_KEY_NAME)
            
            self.is_test_user = shared.isTestUser(self.user_email)
            
            if self.user_email:
                
                # ============================================================
                #       Retrieve the import job DB record for this user
                # ============================================================
                self.process_tasks_job = model.ImportTasksJob.get_by_key_name(self.user_email)
                
                if self.process_tasks_job is None:
                    logging.error(fn_name + "<End> No DB record")
                    logservice.flush()
                    return
                    # TODO: Find some way of notifying the user?????
                else:
                    logging.debug(fn_name + "Retrieved process tasks job for " + str(self.user_email))
                    logservice.flush()

                    if self.process_tasks_job.status in constants.ImportJobStatus.STOPPED_VALUES:
                        logging.warning(fn_name + "<End> Nothing to do (Job Status: " + self.process_tasks_job.status + ")")
                        logservice.flush()
                        return
                        
                    if self.process_tasks_job.status == constants.ImportJobStatus.STARTING:
                        # Only change status to initialising the first time
                        # On subsequent calls, we keep the status of the previous run 
                        self.process_tasks_job.status = constants.ImportJobStatus.INITIALISING
                        self.process_tasks_job.message = "Validating background job ..."
                    self.process_tasks_job.is_paused = False # Un-pause job (if it was paused)
                    self.process_tasks_job.pause_reason = constants.PauseReason.NONE
                    self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    self._log_job_progress()
                    self.process_tasks_job.put()
                    
                    user = self.process_tasks_job.user
                    
                    if not user:
                        logging.error(fn_name + "No user object in DB record for " + str(self.user_email))
                        logservice.flush()
                        self._report_error("Problem with user details. Please restart.")
                        logging.debug(fn_name + "<End> No user object")
                        return
                        
                    # Retrieve credentials for user
                    self.credentials = appengine.StorageByKeyName(
                        model.Credentials, user.user_id(), "credentials").get()
                        
                    if not self.credentials:
                        logging.error(fn_name + "No credentials in DB record for " + str(self.user_email))
                        logservice.flush()
                        self._report_error("Problem with credentials. Please restart and re-authenticate.")
                        logging.debug(fn_name + "<End> No self.credentials")
                        return
                  
                    if self.credentials.invalid:
                        logging.error(fn_name + "Invalid credentials in DB record for " + str(self.user_email))
                        logservice.flush()
                        self._report_error("Invalid credentials. Please restart and re-authenticate.")
                        logging.debug(fn_name + "<End> Invalid self.credentials")
                        return
                  
                    if self.is_test_user:
                        logging.debug(fn_name + "User is test user %s" % self.user_email)
                        logservice.flush()
                    
                    try:
                        # ---------------------------------------------------------
                        #       Connect to the tasks and tasklists services
                        # ---------------------------------------------------------
                        http = httplib2.Http()
                        http = self.credentials.authorize(http)
                        service = discovery.build("tasks", "v1", http)
                        self.tasklists_svc = service.tasklists()
                        self.tasks_svc = service.tasks()
                
                    except apiclient_errors.HttpError, e:
                        self._handle_http_error(e, retry_count, "Error connecting to Tasks services")
                        
                    except Exception, e:
                        self._handle_general_error(e, retry_count, "Error connecting to Tasks services")
                    
                    # ===================================
                    #           Import tasks
                    # ===================================
                    # Exceptions raised in _import_tasks() will be caught in this method's outer exception handlers
                    self._import_tasks()
            else:
                logging.error(fn_name + "No processing, as there was no user_email key")
                logservice.flush()
                
        except DailyLimitExceededError:
            # Here we handle any Daily Limit Exceeded error that was raised anywhere 'below' this level
            self.process_tasks_job.is_paused = True
            self.process_tasks_job.pause_reason = constants.PauseReason.DAILY_LIMIT_EXCEEDED
            if self.process_tasks_job.status == constants.ImportJobStatus.IMPORTING:
                # Current data row was NOT processed, so change data_row_num so it now refers to last successfully imported row.
                # data_row_num is incremented at the start of the task processing loop
                self.import_job_state.data_row_num = self.import_job_state.data_row_num - 1
            self.process_tasks_job.pickled_import_state = pickle.dumps(self.import_job_state)
            self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
            self.process_tasks_job.message = "Processed " + str(self.import_job_state.data_row_num) + " of " + \
                str(self.process_tasks_job.total_num_rows_to_process) + " data rows."
            # self.process_tasks_job.error_message = "Daily limit exceeded. Please try again after midnight Pacific Standard Time."
            logging.warning(fn_name + "Paused import job; daily limit exceeded.")
            logservice.flush()
            self._log_job_progress()
            _log_job_state(self.import_job_state) # Log job state info
            self.process_tasks_job.put()
        
        except Exception, e:
            logging.exception(fn_name + "Caught outer Exception:") 
            logservice.flush()
            self._report_error("System Error: " + shared.get_exception_msg(e))
            

        logging.debug(fn_name + "<End>, user = " + str(self.user_email))
        logservice.flush()


    def _import_tasks(self):
        """ Read data from supplied CSV file, and create a task for each row.
        
            The self.process_tasks_job entity contains the key to the Blobstore which holds a CSV file containing tasks to be imported.
            
            Format of CSV file:
                "tasklist_name","title","notes","status","due","completed","deleted","hidden",depth
                every row must have 9 columns
                CSV file is ordered;
                    Grouped by tasklist
                    Subtasks immediately follow tasks parent tasks
                        A
                        B
                            C
                            D
                                E
                                    F
                            G
                                H
                        I

        """
        
        fn_name = "_import_tasks: "
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        prev_progress_timestamp = datetime.datetime.now()
        start_time = datetime.datetime.now()
        
        try:
            if self.process_tasks_job.status == constants.ImportJobStatus.STARTING:
                # Only change status to initialising the first time
                # On subsequent calls, we keep the status of the previous run 
                self.process_tasks_job.status = constants.ImportJobStatus.INITIALISING
                self.process_tasks_job.message = "Processing uploaded file ..."
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            self._log_job_progress()
            self.process_tasks_job.put()

            # logging.debug(fn_name + "Retrieving data from Blobstore")
            # logservice.flush()
            blob_key = self.process_tasks_job.blobstore_key
            blob_reader = blobstore.BlobReader(blob_key)
            self.blob_info = blobstore.BlobInfo.get(blob_key)
            # file_name = str(self.blob_info.filename)
            # logging.debug(fn_name + "Filename = " + file_name + ", key = " + str(blob_key))
            # logservice.flush()
            logging.debug(fn_name + "Filetype: '" + str(self.process_tasks_job.file_type) + "'")
            logservice.flush()
            if self.process_tasks_job.file_type == 'gtbak': 
                # Data file contains two pickled values; file_format_version & tasks_data
                # We need to read file_format_version first, so we can get to tasks_data, but we can 
                # ignore the value of file_format_version here, because it was already checked in import_tasks.py
                file_format_version = pickle.load(blob_reader) 
                tasks_data = pickle.load(blob_reader)
            else:
                tasks_data=csv.DictReader(blob_reader,dialect='excel')
            
            import_method = self.process_tasks_job.import_method
            
            if import_method in constants.ImportMethod.USE_SUFFIX_VALUES:
                # Use the suffix which was set when the import job was created
                # This is either the datetime, or the suffix entered by the user (from the webpage)
                tasklist_suffix = str(self.process_tasks_job.import_tasklist_suffix).rstrip()
            else:
                tasklist_suffix = ''
                
            logging.debug(fn_name + "Import method '" + import_method + "', suffix '" + tasklist_suffix + "'")
            
            # ==============================================
            #       Check if this is a continuing job
            # ==============================================
            if self.process_tasks_job.pickled_import_state:
                # ----------------------------
                #       Continuing job
                # ----------------------------
                logging.debug(fn_name + "Continuing previous run, so using stored state values")
                # Load state from previous run
                self.import_job_state = pickle.loads(self.process_tasks_job.pickled_import_state)
                
                _log_job_state(self.import_job_state)
                
                # -----------------------------------------------------------------------
                #       Skip first 'n' lines that were processed in a previous job
                # -----------------------------------------------------------------------
                logging.debug(fn_name + "Skipping first " + str(self.import_job_state.data_row_num) + " data rows")
                dummy_data_row_num = 0
                while dummy_data_row_num < self.import_job_state.data_row_num:
                    if self.process_tasks_job.file_type == 'gtbak':
                        # Pop the task from the start of the list
                        tasks_data.pop(0)
                    else:
                        # Skip the row in the CSV file
                        tasks_data.next()
                    
                    dummy_data_row_num = dummy_data_row_num + 1
            else:
                # -----------------------
                #       First run
                # -----------------------
                logging.debug(fn_name + "First run, so using initial state values")
                self.import_job_state = ImportJobState()
                self.import_job_state.prev_tasklist_ids = {} # "Tasklist name" : "Tasklist ID"
                if import_method == constants.ImportMethod.DELETE_BEFORE_IMPORT:
                    logging.debug(fn_name + "import_method == '" + import_method + "', so deleting all existing tasklists")
                    # Delete all existing tasklists
                    self._delete_tasklists(self._get_tasklists())
                elif import_method in constants.ImportMethod.RETRIEVE_EXISTING_TASKLISTS_VALUES:
                    logging.debug(fn_name + "import_method == " + import_method + ", so retrieving existing tasklists")
                    # Retrieve and store the IDs of existing tasklists.
                    tasklists = self._get_tasklists()
                    for tasklist in tasklists:
                        tasklist_title = tasklist['title']
                        tasklist_id = tasklist['id']
                        # Store existing "Tasklist name" : "Tasklist ID"
                        # NOTE: If user has duplicate tasklist names, ID of last processed tasklist is used
                        # IDs of any new tasklists created will be added to the list.
                        self.import_job_state.prev_tasklist_ids[tasklist_title] = tasklist_id
                
                _log_job_state(self.import_job_state)
            
            # ---------------------------------------------------------------------------------------------------------
            #       Update progress to let the user know that importing has started, and so that job doesn't stall
            # ---------------------------------------------------------------------------------------------------------
            self.process_tasks_job.message = 'Importing ...'
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            self.process_tasks_job.status = constants.ImportJobStatus.IMPORTING
            self._log_job_progress()
            self.process_tasks_job.put()
            
            prev_progress_timestamp = datetime.datetime.now()
        
            for task_row_data in tasks_data:
                self.import_job_state.data_row_num = self.import_job_state.data_row_num + 1
                
                if self.process_tasks_job.file_type == 'csv':
                    # ----------------------------------------------------------
                    #       Check that CSV row has valid number of columns
                    # ----------------------------------------------------------
                    if len(task_row_data) != 9 or task_row_data.get('restkey'):
                        # Invalid number of columns
                        self._report_error("Data row " + str(self.import_job_state.data_row_num) + " has " + str(len(task_row_data)) + 
                            " columns, expected 9")
                        logging.debug(fn_name + "<End> due to invalid number of columns")
                        logservice.flush()
                        return 

                # -------------------------------------------
                #       Check for valid 'status' value
                # -------------------------------------------
                status = task_row_data['status']
                if status not in ('completed', 'needsAction'):
                    self._report_error("Invalid status value '" + str(status) + "' in data row " + str(self.import_job_state.data_row_num))
                    logging.debug(fn_name + "<End> due to invalid 'status' value")
                    logservice.flush()
                    return 
                
                # -----------------------------------------------
                #        Check depth value for this task
                # -----------------------------------------------
                err_msg = None
                if task_row_data.has_key('depth'):
                    try:
                        depth = int(task_row_data['depth'])
                    except Exception, e:
                        err_msg = "Invalid depth value [" + str(task_row_data['depth']) + "] for data row " + \
                            str(self.import_job_state.data_row_num) + ", Exception: " + str(e)
                    # 'depth' is not part of the Tasks resource, so delete it from the dictionary
                    del(task_row_data['depth'])
                else:
                    err_msg = "No 'depth' property in data row " + str(self.import_job_state.data_row_num)
                if err_msg:
                    self._report_error(err_msg)
                    logging.debug(fn_name + "<End> due to missing/invalid depth column")
                    logservice.flush()
                    return 
                if depth < 0:
                    logging.debug(fn_name + "Depth value [" + str(depth) + "] in data row " + str(self.import_job_state.data_row_num) + 
                        " is less than zero. Setting depth = 0") 
                    depth = 0
                
                # ---------------------------------------------------
                #            Process tasklist for this task
                # ---------------------------------------------------
                # We assume that the 'tasklist_name' column exists, because it was checked when the file was uploaded
                tasklist_name = task_row_data['tasklist_name']
                if not tasklist_name:
                    self._report_error('Missing value for "tasklist_name" column in data row ' + str(self.import_job_state.data_row_num))
                    logging.debug(fn_name + "<End> due to missing 'tasklist_name' value")
                    logservice.flush()
                    return 
                # 'tasklist_name' is not part of the Tasks resource, so delete it from the dictionary
                del(task_row_data['tasklist_name'])
                
                if import_method == constants.ImportMethod.SKIP_DUPLICATE_TASKLIST and self.import_job_state.prev_tasklist_ids.get(tasklist_name):
                    # Tasklist exists, so do not import tasks in this tasklist
                    if self.is_test_user:
                        logging.debug(fn_name + "Skipping data row " + str(self.import_job_state.data_row_num) + " because tasklist '" + 
                            tasklist_name + "' already exists")
                        logservice.flush()
                    continue
                    
                if tasklist_name != self.import_job_state.prev_tasklist_name:
                    # Processing a new/different tasklist in the imported data
                    self.import_job_state.num_tasklists = self.import_job_state.num_tasklists + 1
                    existing_tasklist_id = self.import_job_state.prev_tasklist_ids.get(tasklist_name)
                    if existing_tasklist_id:
                        if import_method == constants.ImportMethod.REPLACE_TASKLIST_CONTENT:
                            logging.debug(fn_name + "Deleting existing tasklist '" + tasklist_name + "' before creating new tasklist")
                            # It is much quicker to delete an entire tasklist than it is to individually delete
                            # all the tasks in an existing list. So we delete the existing tasklist before creating new tasklist
                            # TODO: Handle potential duplicate tasklist names; delete ALL tasklists with tasklist_name
                            self._delete_tasklist_by_id(existing_tasklist_id, tasklist_name)
                        else:
                            # Add tasks to existing tasklist
                            self.import_job_state.tasklist_id = existing_tasklist_id
                            
                    if import_method in constants.ImportMethod.CREATE_NEW_TASKLIST_VALUES or not existing_tasklist_id:
                        # ----------------------------------
                        #       Create new tasklist
                        # ----------------------------------
                        tasklist = { 'title': tasklist_name + tasklist_suffix }
                        # TODO: try-except
                        result = self.tasklists_svc.insert(body=tasklist).execute()
                        # TODO: Catch HTTP error & check & Raise DailyLimitExceededError() and catch at top level?
                        self.import_job_state.tasklist_id = result['id']
                        self.import_job_state.prev_tasklist_ids[tasklist_name] = self.import_job_state.tasklist_id
                        if self.is_test_user:
                            logging.debug(fn_name + "Created new Tasklist [" + tasklist['title'] + "], ID = " + self.import_job_state.tasklist_id)
                            logservice.flush()
                            
                            
                    # Importing into a new list, or new tasks into an existing list, so start with no parents or siblings
                    self.import_job_state.parents_ids = [''] 
                    self.import_job_state.sibling_ids = [] # New list, so start with no siblings
                    self.import_job_state.prev_depth = 0
                    
                    self.import_job_state.prev_tasklist_name = tasklist_name
                
                # ---------------------------------------------------
                #               Adjust date/time formats
                # ---------------------------------------------------
                # Calculate due date
                date_due_str = task_row_data.get(u'due')
                if date_due_str:
                    try:
                        # Due date value is stored in CSV file as "UTC %Y-%m-%d"
                        new_due_date = datetime.datetime.strptime(date_due_str, "UTC %Y-%m-%d").date()
                    except ValueError, e:
                        new_due_date = datetime.date(1900, 1, 1)
                        logging.exception(fn_name + "Invalid 'due' timestamp format, so using " + str(new_due_date))
                        logging.debug(fn_name + "Invalid value was " + str(date_due_str))
                        logservice.flush()
                    # Store due date as  "%Y-%m-%dT00:00:00.000Z"
                    task_row_data[u'due'] = new_due_date.strftime("%Y-%m-%dT00:00:00.000Z")
                
                
                # Calculate completed date
                datetime_completed_str = task_row_data.get(u'completed')
                if datetime_completed_str:
                    try:
                        # Completed datetime value is stored in CSV file as "UTC %Y-%m-%d %H:%M:%S"
                        new_datetime_completed = datetime.datetime.strptime(datetime_completed_str, "UTC %Y-%m-%d %H:%M:%S")
                    except ValueError, e:
                        new_datetime_completed = datetime.datetime(1900, 1, 1, 0, 0, 0)
                        logging.exception(fn_name + "Invalid 'completed' timestamp format, so using " + str(new_datetime_completed))
                        logging.debug(fn_name + "Invalid value was " + str(datetime_completed_str))
                        logservice.flush()
                    # Store completed timestamp as "%Y-%m-%dT%H:%M:%S.000Z"
                    task_row_data[u'completed'] = new_datetime_completed.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                
                # ------------------------------------------------------
                #       Replace "\n" string in notes with newline
                # ------------------------------------------------------
                notes = task_row_data.get('notes')
                if notes:
                    # Replace \n with an actual newline
                    # '\\n' matches '\n' in the imported string 
                    task_row_data['notes'] = notes.replace('\\n', '\n')
                
                # -----------------------------------------------------------
                #       Check depth and find current task's parent ID
                # -----------------------------------------------------------
                # Check for valid depth value
                # Valid depth values:
                #   depth = 0                   Root task
                #   depth < self.import_job_state.prev_depth          Task moved back up the task tree
                #   depth == self.import_job_state.prev_depth         Sibling task (same parent as previous task)
                #   depth == self.import_job_state.prev_depth + 1     New child task
                # Task depth must not be more than self.import_job_state.prev_depth + 1
                # List of self.import_job_state.parents_ids is updated after task has been added, because the current task may be the parent of the next task
                if depth > self.import_job_state.prev_depth+1:
                    # Child can only be immediate child of previous task (i.e., previous depth + 1)
                    self._report_error("Depth value [" + str(depth) + "] in data row " + str(self.import_job_state.data_row_num) + " is more than 1 below greater than previous task's depth [" + str(self.import_job_state.prev_depth) + "]")
                    logging.debug(fn_name + "<End> due to invalid depth value")
                    logservice.flush()
                    return 
                    
                # Find parent task ID
                # Will be empty string for root tasks (depth = 0)
                try:
                    parent_id = self.import_job_state.parents_ids[depth]
                except Exception, e:
                    self._report_error("Invalid depth value [" + str(depth) + "] in data row " + str(self.import_job_state.data_row_num))
                    logging.info(fn_name + "Invalid depth value [" + str(depth) + "] in data row " + str(self.import_job_state.data_row_num) + 
                        "; Unable to determine parent. Previous task's depth was " + str(self.import_job_state.prev_depth) + 
                        " Exception: " + shared.get_exception_msg(e))
                    logging.debug(self.import_job_state.parents_ids)
                    logging.debug(fn_name + "<End> due to invalid depth value")
                    logservice.flush()
                    return 
                
                # Find sibling (previous) ID
                if depth == self.import_job_state.prev_depth+1:
                    # Going deeper, so this is the first child at the new depth, within this branch of the tree.
                    # There is nowhere else that this task can go, so use a blank sibling ID (otherwise insert() throws an error)
                    self.import_job_state.sibling_id = ''
                elif depth+1 > len(self.import_job_state.sibling_ids):
                    # First task at this depth
                    self.import_job_state.sibling_id = ''
                else:
                    # Previous task at this depth
                    self.import_job_state.sibling_id = self.import_job_state.sibling_ids[depth]
                
                # -------------------------------------------------------------------------------------
                #           Delete any empty properties, to prevent server throwing an error
                # -------------------------------------------------------------------------------------
                empty_keys = []
                for k,v in task_row_data.iteritems():
                    if len(v) == 0:
                        #logging.debug(fn_name + "'" + k + "' property is empty. Deleting")
                        empty_keys.append(k)
                        
                for k in empty_keys:
                    #logging.debug(fn_name + "Deleting empty '" + k + "' property")
                    del(task_row_data[k])
                
                # ================================================================
                #               Insert the task into the tasklist
                # ================================================================
                # logging.debug(fn_name + "Inserting task from data row " + str(self.import_job_state.data_row_num) + " with depth " + str(depth) + " ==>")
                # logging.debug(task)
                # logservice.flush()
                
                # DEBUG:
                # if self.import_job_state.num_of_imported_tasks % 10 == 9:
                    # logging.debug(fn_name + "DEBUG: Generating shared.DailyLimitExceededError()")
                    # raise DailyLimitExceededError()
                
                # Retry, to handle occassional API timeout
                retry_count = settings.NUM_API_TRIES
                while retry_count > 0:
                    retry_count = retry_count - 1
                    # ------------------------------------------------------
                    #       Update progress so that job doesn't stall
                    # ------------------------------------------------------
                    msg = "Imported " + str(self.import_job_state.num_of_imported_tasks) + " of " + str(self.process_tasks_job.total_num_rows_to_process) + " data rows."
                    self._update_progress(msg)

                    try:
                        # DEBUG
                        # ts = datetime.datetime.now()
                        # =======================
                        #       Create task
                        # =======================
                        result = self.tasks_svc.insert(tasklist=self.import_job_state.tasklist_id, body=task_row_data, parent=parent_id, previous=self.import_job_state.sibling_id).execute()
                        
                        # DEBUG
                        # logging.debug(fn_name + "Inserted task ==>")
                        # logging.debug(result)
                        # te = datetime.datetime.now()
                        # pt = te - ts
                        # logging.debug(fn_name + "Task create time = " + str(pt.seconds) + "." + str(pt.microseconds)[:3] + " seconds")

                        task_id = result['id']
                        # logging.debug(fn_name + "Created new Task ID = " + task_id)
                        # logservice.flush()
                        self.import_job_state.num_of_imported_tasks = self.import_job_state.num_of_imported_tasks + 1
                        # Succeeded, so continue
                        break
                    
                    except apiclient_errors.HttpError, e:
                        if retry_count == 0:    
                            # DEBUG
                            logging.debug(fn_name + "Failed task:" +
                                "\n    tasklist = " + str(self.import_job_state.tasklist_id) +
                                "\n    parent = " + str(parent_id) +
                                "\n    previous = " + str(self.import_job_state.sibling_id) +
                                "\n    Data row num = " + str(self.import_job_state.data_row_num) +
                                "\n    Depth = " + str(depth) +
                                "\n    body = " + str(task_row_data))
                            logservice.flush()
                        self._handle_http_error(e, retry_count, "Error creating task from data row " + str(self.import_job_state.data_row_num))
                        
                    except Exception, e:
                        if retry_count == 0:    
                            # DEBUG
                            logging.debug(fn_name + "Failed task:" +
                                "\n    tasklist = " + str(self.import_job_state.tasklist_id) +
                                "\n    parent = " + str(parent_id) +
                                "\n    previous = " + str(self.import_job_state.sibling_id) +
                                "\n    Task num = " + str(self.import_job_state.data_row_num) +
                                "\n    Depth = " + str(depth) +
                                "\n    body = " + str(task_row_data))
                            logservice.flush()
                        self._handle_general_error(e, retry_count, "Error creating task from data row " + str(self.import_job_state.data_row_num))

                            
                    if retry_count <= 2:
                        logging.debug(fn_name + "Last chance; Sleeping for " + str(settings.API_RETRY_SLEEP_DURATION) + " seconds")
                        logservice.flush()
                        time.sleep(settings.API_RETRY_SLEEP_DURATION)
                
                    
                    
                # --------------------------------------------
                #           Update list of parent IDs
                # --------------------------------------------
                # List of self.import_job_state.parents_ids is updated after task has been added, because the current task may be the parent of the next task
                if depth < self.import_job_state.prev_depth:
                    # Child of an earlier task, so we've moved back up the task tree
                    # Delete ID of 'deeper' tasks, because those tasks cannot be parents anymore
                    del(self.import_job_state.parents_ids[depth+1:])
                # Store ID of current task in at this depth, as it could be the parent of a future task
                if len(self.import_job_state.parents_ids) == depth+2:
                    self.import_job_state.parents_ids[depth+1] = task_id
                else:
                    self.import_job_state.parents_ids.append(task_id)
                
                # Store ID of this task as sibling for next task at this depth
                if len(self.import_job_state.sibling_ids) < depth+1:
                    # First task at this depth
                    self.import_job_state.sibling_ids.append(task_id)
                else:
                    # There was a previous task at this depth
                    self.import_job_state.sibling_ids[depth] = task_id

                self.import_job_state.prev_depth = depth
                
                    
                
                # ==============================================================================
                #       Check if we need to terminate this worker and start another worker
                # ==============================================================================
                # If the task cannot be finished within the maximum allowable time, we need to stop this
                # task and start another task to continue the process.
                run_time = (datetime.datetime.now() - start_time).seconds
                if run_time > settings.MAX_WORKER_RUN_TIME:
                    logging.debug(fn_name + "Job has run for " + str(run_time) + " seconds.")
                    logservice.flush()
                    _log_job_state(self.import_job_state)
                    self.process_tasks_job.pickled_import_state = pickle.dumps(self.import_job_state)
                    self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
                    self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    if self.import_job_state.num_of_imported_tasks == self.import_job_state.data_row_num:
                        self.process_tasks_job.message = ''
                    else:
                        self.process_tasks_job.message = "Imported " + str(self.import_job_state.num_of_imported_tasks) + " tasks from " + str(self.import_job_state.data_row_num) + " data rows."
                    self._log_job_progress()
                    _log_job_state(self.import_job_state) # Log job state info
                    self.process_tasks_job.put()
                    
                    # Add the request to the tasks queue, passing in the user's email so that the task can access the database record
                    q = taskqueue.Queue(settings.PROCESS_TASKS_REQUEST_QUEUE_NAME)
                    t = taskqueue.Task(url=settings.WORKER_URL, params={settings.TASKS_QUEUE_KEY_NAME : self.user_email}, method='POST')
                    logging.debug(fn_name + "Continue import in another job. Adding task to " + str(settings.PROCESS_TASKS_REQUEST_QUEUE_NAME) + 
                        " queue, for " + str(self.user_email))
                    
                    try:
                        q.add(t)
                        logservice.flush()
                        logging.debug(fn_name + "<End> Added follow on task to taskqueue")
                        logservice.flush()
                        return
                    except Exception, e:
                        # TODO: Enable job to be continued {Don't mark as error, so Blobstore doesn't get deleted}
                        logging.exception(fn_name + "Exception adding task to taskqueue.")
                        logservice.flush()
                        self._report_error("Error continuing import job: " + shared.get_exception_msg(e))
                        logging.debug(fn_name + "<End> (error adding job to taskqueue)")
                        logservice.flush()
                        return
                        
                    
                
            end_time = datetime.datetime.now()
            process_time = end_time - self.process_tasks_job.job_start_timestamp
            proc_time_str = str(process_time.seconds) + "." + str(process_time.microseconds)[:3] + " seconds"
            
            # ---------------------------------------
            #       Mark import job complete
            # ---------------------------------------
            self.process_tasks_job.pickled_import_state = None
            self.process_tasks_job.status = constants.ImportJobStatus.IMPORT_COMPLETED
            self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
            if self.import_job_state.num_of_imported_tasks == self.import_job_state.data_row_num:
                self.process_tasks_job.message = "Imported " + str(self.import_job_state.num_of_imported_tasks) + " tasks into " + \
                    str(self.import_job_state.num_tasklists) + " tasklists, in " + proc_time_str
            else:
                self.process_tasks_job.message = "Imported " + str(self.import_job_state.num_of_imported_tasks) + " tasks from " + \
                    str(self.import_job_state.data_row_num) + " data rows into " + str(self.import_job_state.num_tasklists) + \
                    " tasklists, in " + proc_time_str
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            self._log_job_progress()
            self.process_tasks_job.put()
            # logging.debug(fn_name + "Marked import job complete for " + str(self.user_email) + ", with progress = " + 
                # str(self.process_tasks_job.total_progress) + " " + str(self.process_tasks_job.message))
            logservice.flush()
        
            logging.info(fn_name + "COMPLETED: Imported " + str(self.import_job_state.num_of_imported_tasks) + " tasks for " + str(self.user_email) +
                ", from " + str(self.import_job_state.data_row_num) + 
                " data rows into " + str(self.import_job_state.num_tasklists) + " tasklists, in " + proc_time_str)
            
            # We've imported all the data, so now delete the Blobstore
            shared.delete_blobstore(self.blob_info)
            
        except DailyLimitExceededError, e:
            raise e
            
        except Exception, e:
            logging.exception(fn_name + "Caught outer Exception:") 
            logservice.flush()
            self._report_error("System Error: " + shared.get_exception_msg(e))
            
        logging.debug(fn_name + "<End>")
        logservice.flush()

        
    def _get_tasklists(self):
        """ Get a list of all the user's tasklists """
        fn_name = "_get_tasklists(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        self.process_tasks_job.message = "Retrieving existing tasklists"
        self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
        self._log_job_progress()
        self.process_tasks_job.put()
        
        prev_progress_timestamp = datetime.datetime.now()
        
        # This list will contain zero or more tasklist dictionaries, which each contain tasks
        tasklists = [] 
        
        total_num_tasklists = 0
        
        # ----------------------------------------------------
        #       Retrieve all the tasklists for the user
        # ----------------------------------------------------
        logging.debug(fn_name + "Retrieve all the tasklists for the user")
        logservice.flush()
        next_tasklists_page_token = None
        more_tasklists_data_to_retrieve = True
        while more_tasklists_data_to_retrieve:
            retry_count = settings.NUM_API_TRIES
            while retry_count > 0:
                retry_count = retry_count - 1
                # ------------------------------------------------------
                #       Update progress so that job doesn't stall
                # ------------------------------------------------------
                self._update_progress("Initialising. Loaded " + str(total_num_tasklists) + " tasklists")
                
                try:
                    if next_tasklists_page_token:
                        tasklists_data = self.tasklists_svc.list(pageToken=next_tasklists_page_token).execute()
                    else:
                        tasklists_data = self.tasklists_svc.list().execute()
                    # Successfully retrieved data, so break out of retry loop
                    break
                
                except apiclient_errors.HttpError, e:
                    self._handle_http_error(e, retry_count, "Error retrieving list of tasklists")
                    
                except Exception, e:
                    self._handle_general_error(e, retry_count, "Error retrieving list of tasklists")
        
            if self.is_test_user and settings.DUMP_DATA:
                logging.debug(fn_name + "tasklists_data ==>")
                logging.debug(tasklists_data)
                logservice.flush()

            if tasklists_data.has_key(u'items'):
                tasklists_list = tasklists_data[u'items']
            else:
                # If there are no tasklists, then there will be no 'items' element. This could happen if
                # the user has deleted all their tasklists. Not sure if this is even possible, but
                # checking anyway, since it is possible to have a tasklist without 'items' (see issue #9)
                logging.debug(fn_name + "User has no tasklists.")
                logservice.flush()
                tasklists_list = []
          
            # tasklists_list is a list containing the details of the user's tasklists. 
            # We are only interested in the title
          
            # if self.is_test_user and settings.DUMP_DATA:
                # logging.debug(fn_name + "tasklists_list ==>")
                # logging.debug(tasklists_list)


            # ---------------------------------------
            # Process all the tasklists for this user
            # ---------------------------------------
            for tasklist_data in tasklists_list:
                total_num_tasklists = total_num_tasklists + 1
              
                if self.is_test_user and settings.DUMP_DATA:
                    logging.debug(fn_name + "tasklist_data ==>")
                    logging.debug(tasklist_data)
                    logservice.flush()
              
                """
                    Example of a tasklist entry;
                        u'id': u'MDAxNTkzNzU0MzA0NTY0ODMyNjI6MDow',
                        u'kind': u'tasks#taskList',
                        u'selfLink': u'https://www.googleapis.com/tasks/v1/users/@me/lists/MDAxNTkzNzU0MzA0NTY0ODMyNjI6MDow',
                        u'title': u'Default List',
                        u'updated': u'2012-01-28T07:30:18.000Z'},
                """ 
           
                tasklist_title = tasklist_data[u'title']
                tasklist_id = tasklist_data[u'id']
                        
                # if self.is_test_user:
                    # logging.debug(fn_name + "Adding %d tasks to tasklist" % len(tasklist_dict[u'tasks']))
                    
                # Add the data for this tasklist (including all the tasks) into the collection of tasklists
                tasklists.append(tasklist_data)
          
            # Check if there is another page of tasklists to be retrieved
            if tasklists_data.has_key('nextPageToken'):
                # There is another page of tasklists to be retrieved for this user, 
                # which we'll retrieve next time around the while loop.
                # This happens if there is more than 1 page of tasklists.
                # It seems that each page contains 20 tasklists.
                more_tasklists_data_to_retrieve = True # Go around while loop again
                next_tasklists_page_token = tasklists_data['nextPageToken']
                # if self.is_test_user:
                    # logging.debug(fn_name + "There is (at least) one more page of tasklists to be retrieved")
            else:
                # This is the last (or only) page of results (list of tasklists)
                more_tasklists_data_to_retrieve = False
                next_tasklists_page_token = None
              
        # *** end while more_tasks_data_to_retrieve ***
        
        logging.debug(fn_name + "Retrieved " + str(total_num_tasklists) + " tasklists")
        logging.debug(fn_name + "<End>")
        logservice.flush()
        return tasklists

        
    def _delete_tasklist_by_id(self, tasklist_id, tasklist_name = None):
        """ Delete specified tasklist.
        
            If tasklist_id is the default tasklist, rename it (because default list cannot be deleted).
            
            The tasklist_name parameter is only passed in to make logging easier.
        """
        fn_name = "_delete_tasklist_by_id(): "
        
        if not self.default_tasklist_id:
            # TODO: try-except around .get()
            # Get the ID of the default tasklist
            default_tasklist = self.tasklists_svc.get(tasklist='@default').execute()
            self.default_tasklist_id = default_tasklist['id']
            # TODO: Catch HTTP error & check & Raise DailyLimitExceededError() and catch at top level?

        retry_count = settings.NUM_API_TRIES
        while retry_count > 0:
            retry_count = retry_count - 1
            # ------------------------------------------------------
            #       Update progress so that job doesn't stall
            # ------------------------------------------------------
            self._update_progress()
            try:
                if tasklist_id == self.default_tasklist_id:
                    # ------------------------------------
                    #       Rename default tasklist
                    # ------------------------------------
                    action_str = "renaming default"
                    # Google does not allow default tasklist to be deleted, so just change the title
                    # Use Unix timestamp to create a unique title for the undeletable default tasklist
                    default_tasklist['title'] = 'Undeletable default ' + str(int(time.mktime(datetime.datetime.now().timetuple())))
                    result = self.tasklists_svc.update(tasklist=self.default_tasklist_id, body=default_tasklist).execute()
                    logging.debug(fn_name + "Renamed default tasklist '" + str(tasklist_name) + "' to '" + str(default_tasklist['title']))
                    logservice.flush()
                    #logging.debug(result)
                else:
                    # ----------------------------
                    #       Delete tasklist
                    # ----------------------------
                    action_str = "deleting"
                    self.tasklists_svc.delete(tasklist=tasklist_id).execute()
                    # DEBUG: TODO: Only log tasklist_name & ID for test users
                    logging.debug(fn_name + "Deleted tasklist '" + str(tasklist_name) + "', id = " + str(tasklist_id))
                    logservice.flush()
                break
                
            except apiclient_errors.HttpError, e:
                # DEBUG: TODO: Only log tasklist_name & ID for test users
                self._handle_http_error(e, retry_count, "Error " + action_str + " tasklist '" + str(tasklist_name) + "', id = " + str(tasklist_id))
                
            except Exception, e:
                # DEBUG: TODO: Only log tasklist_name & ID for test users
                self._handle_general_error(e, retry_count, "Error " + action_str + " tasklist '" + str(tasklist_name) + "', id = " + str(tasklist_id))
                    
                    
    def _delete_tasklists(self, tasklists):
        """ Delete all existing tasklists. """
        
        fn_name = "_delete_tasklists: "
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        
        # ------------------------------------------------------
        #       Update progress so that job doesn't stall
        # ------------------------------------------------------
        self._update_progress("Deleting existing tasklists", force=True)

        
        prev_progress_timestamp = datetime.datetime.now()
        
        num_deleted_tasklists = 0
        for tasklist in tasklists:
            tasklist_name = tasklist['title']
            tasklist_id = tasklist['id']
            
            self._delete_tasklist_by_id(tasklist_id, tasklist_name)
            num_deleted_tasklists = num_deleted_tasklists + 1

            # ------------------------------------------------------
            #       Update progress so that job doesn't stall
            # ------------------------------------------------------
            self._update_progress("Initialising. Deleted " + str(num_deleted_tasklists) + " tasklists")
            
        logging.debug(fn_name + "Deleted " + str(num_deleted_tasklists) + " tasklists")
        logging.debug(fn_name + "<End>")
        logservice.flush()            
        
    
    def _report_error(self, err_msg):
        """ Log error message, and update Job record to advise user of error """
        
        fn_name = "_report_error(): "
        
        logging.warning(fn_name + "Error: " + err_msg)
        logservice.flush()
        self.process_tasks_job.status = constants.ImportJobStatus.ERROR
        self.process_tasks_job.message = ''
        self.process_tasks_job.error_message = err_msg
        self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
        # Current data row was NOT processed, so change data_row_num so it now refers to last successfully imported row
        # data_row_num is incremented at the start of the task processing loop
        if self.import_job_state:
            self.import_job_state.data_row_num = self.import_job_state.data_row_num - 1
        self.process_tasks_job.pickled_import_state = pickle.dumps(self.import_job_state)
        self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
        self._log_job_progress()
        _log_job_state(self.import_job_state) # Log job state info
        self.process_tasks_job.put()
        # Import process terminated, so delete the blobstore
        shared.delete_blobstore(self.blob_info)


    def _handle_http_error(self, e, retry_count, err_msg):
        if e._get_reason().lower() == "daily limit exceeded":
            logging.warning("HttpError: " + err_msg + ": " + shared.get_exception_msg(e))
            logservice.flush()
            raise DailyLimitExceededError()
        if retry_count > 0:
            logging.warning("HttpError: " + err_msg + ", " + str(retry_count) + " retries remaining: " + shared.get_exception_msg(e))
            logservice.flush()
        else:
            logging.exception("HttpError: " + err_msg + ". Giving up after " + str(settings.NUM_API_TRIES) + " retries")
            logservice.flush()
            self._report_error(err_msg)
            raise e


    def _handle_general_error(self, e, retry_count, err_msg):
        if retry_count > 0:
            logging.warning("Error: " + err_msg + ", " + str(retry_count) + " retries remaining: " + shared.get_exception_msg(e))
            logservice.flush()
        else:
            logging.exception("Error: " + err_msg + ". Giving up after " + str(settings.NUM_API_TRIES) + " retries")
            logservice.flush()
            self._report_error(err_msg)
            raise e
        
    def _update_progress(self, msg=None, force=False):
        """ Update progress so that job doesn't stall """
        
        if force or (datetime.datetime.now() - self.prev_progress_timestamp).seconds > settings.TASK_COUNT_UPDATE_INTERVAL:
            if msg:
                self.process_tasks_job.message = msg
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
            self._log_job_progress()
            self.process_tasks_job.put()
            self.prev_progress_timestamp = datetime.datetime.now()
        
        
        
    def _log_job_progress(self):
        """ Write a debug message showing current progress """
        
        msg1 = "Job status: '" + str(self.process_tasks_job.status) +"', progress: " + str(self.process_tasks_job.total_progress)
        msg2 = ", msg: '" + str(self.process_tasks_job.message) + "'" if self.process_tasks_job.message else ''
        msg3 = ", err msg: '" + str(self.process_tasks_job.error_message) + "'" if self.process_tasks_job.error_message else ''
        msg4 = " (Paused: " + str(self.process_tasks_job.pause_reason) + ")" if self.process_tasks_job.is_paused else ''
        logging.debug(msg1 + msg2 + msg3 + msg4)
        logservice.flush()
    
        
def urlfetch_timeout_hook(service, call, request, response):
    if call != 'Fetch':
        return

    # Make the default deadline 30 seconds instead of 5.
    if not request.has_deadline():
        request.set_deadline(30.0)



def real_main():
    logging.debug("main(): Starting worker")
    
    apiproxy_stub_map.apiproxy.GetPreCallHooks().Append(
        'urlfetch_timeout_hook', urlfetch_timeout_hook, 'urlfetch')
    run_wsgi_app(webapp.WSGIApplication([
        (settings.WORKER_URL, ProcessTasksWorker),
    ], debug=True))
    logging.debug("main(): <End>")

def profile_main():
    # This is the main function for profiling
    # We've renamed our original main() above to real_main()
    import cProfile, pstats, StringIO
    prof = cProfile.Profile()
    prof = prof.runctx("real_main()", globals(), locals())
    stream = StringIO.StringIO()
    stats = pstats.Stats(prof, stream=stream)
    stats.sort_stats("time")  # Or cumulative
    stats.print_stats(80)  # 80 = how many to print
    # The rest is optional.
    stats.print_callees()
    stats.print_callers()
    logging.info("Profile data:\n%s", stream.getvalue())
    
main = real_main

if __name__ == '__main__':
    main()
    
    
