#
# Copyright 2012 Julie Smith.  All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Some code used from Dwight Garth's Google Tasks Porter

# #################################################################################################
# CAUTION: The Google Tasks server regularly re-calculates the 'position' values of existing tasks
# in a tasklist when new tasks are inserted, presumably to ensure an even distribution of 
# 'position' values. Unfortunately, if there are more than a few hundred tasks, and the tasks are
# inserted too quickly, it seems that the server is unable to keep tasks in order.




import logging
import os
import pickle
import sys


# Fix for DeadlineExceeded, because "Pre-Call Hooks to UrlFetch Not Working"
#     Based on code from https://groups.google.com/forum/#!msg/google-appengine/OANTefJvn0A/uRKKHnCKr7QJ
from google.appengine.api import urlfetch
real_fetch = urlfetch.fetch
def fetch_with_deadline(url, *args, **argv):
    argv['deadline'] = settings.URL_FETCH_TIMEOUT
    logservice.flush()
    return real_fetch(url, *args, **argv)
urlfetch.fetch = fetch_with_deadline


from apiclient import discovery
import httplib2
from oauth2client.appengine import OAuth2Decorator

# JS 2012-09-16: Imports to enable credentials = StorageByKeyName()
from oauth2client.appengine import StorageByKeyName
from oauth2client.appengine import CredentialsModel
from oauth2client.appengine import CredentialsProperty


# To allow catching initial "error" : "invalid_grant" and logging as Info
# rather than as a Warning or Error, because AccessTokenRefreshError seems
# to happen quite regularly
from oauth2client.client import AccessTokenRefreshError

import webapp2

from google.appengine.api import apiproxy_stub_map
from google.appengine.api import mail
from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.api import users
from google.appengine.ext import db
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
from google.appengine.api import urlfetch_errors



logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True

import httplib2

import datetime
from datetime import timedelta
import time
import math
# import csv
import unicodecsv # Used instead of csv, supports unicode

# Project-specific imports
import model
import settings
import host_settings
import appversion # appversion.version is set before the upload process to keep the version number consistent
import shared # Code whis is common between classes, modules or projects
import import_tasks_shared  # Code which is common between classes or modules
import check_task_values

import constants
from shared import DailyLimitExceededError

# Orig __author__ = "dwightguth@google.com (Dwight Guth)"
__author__ = "julie.smith.1999@gmail.com (Julie Smith)"

class ImportJobState(object):
    """ Stores the current state of the import job.

        This class is pickled and stored in the job record for the user, so that the
        next worker can continue the job where the previous worker left off.
    """
    
    
    def __init__(self):
        self.prev_tasklist_ids = {}
        self.parents_ids = [''] # Level 0 tasks don't have a parent, so level zero parent ID is always an empty string
        self.sibling_ids = []
        
        self.prev_tasklist_name = None
        self.prev_depth = 0
        
        self.sibling_id = ''
        self.parent_id = ''
        self.tasklist_id = ''

        self.data_row_num = 0
        self.num_of_imported_tasks = 0
        self.num_tasklists = 0    
    
        self.num_tasks_in_list = 0
        
        self.default_tasklist_id = None
        self.default_tasklist_was_renamed = False
        self.default_tasklist_orig_name = ""
    
        # Previous tasks data, used to recreate a 'missing' task (to fix BUG 2012-05-08 01:02)
        self.prev_tasks_data = { 
            'task_row_data' : {}, 'tasklist_id' : '', 'parent_id' : '', 'sibling_id' : '', 'id' : '' }

    
        
def _safe_str(str):
    if str:
        return unicode(str)
    else:
        return ''
    
    
def _log_job_state(import_job_state):
    if import_job_state:
        logging.debug("Job state:" +
            # "\n    prev_tasklist_ids = " + str(import_job_state.prev_tasklist_ids) +
            "\n    parents_ids = " + str(import_job_state.parents_ids) +
            "\n    sibling_ids = " + str(import_job_state.sibling_ids) +
            # "\n    prev_tasklist_name = '" + _safe_str(import_job_state.prev_tasklist_name) + "'" +
            "\n    prev_depth = " + str(import_job_state.prev_depth) +
            "\n    sibling_id = " + str(import_job_state.sibling_id) +
            "\n    parent_id = " + str(import_job_state.parent_id) +
            "\n    tasklist_id = " + str(import_job_state.tasklist_id) +
            "\n    data_row_num = " + str(import_job_state.data_row_num) +
            "\n    num_of_imported_tasks = " + str(import_job_state.num_of_imported_tasks) +
            "\n    num_tasklists = " + str(import_job_state.num_tasklists) +
            "\n    num_tasks_in_list = " + str(import_job_state.num_tasks_in_list))
    else:
        logging.debug("Job state not (yet) set")
    logservice.flush()
    
    

class ProcessTasksWorker(webapp2.RequestHandler):
    """ Process tasks according to data in the ImportTasksJobV1 entity """

    
    def __init_variables(self):
        self.prev_progress_timestamp = datetime.datetime.now()
    
        # Start time for this run
        self.run_start_time = datetime.datetime.now()
    
        self.credentials = None
        self.user_email = None
        self.is_test_user = False
        self.tasks_svc = None
        self.tasklists_svc = None
        self.blob_info = None
    
        self.process_tasks_job = None
        self.import_job_state = None
    
    
    def post(self):
        fn_name = "ProcessTasksWorker.post(): "
        
        try:
            logging.debug(fn_name + "<start> (app version %s)" %appversion.version)
            logservice.flush()
            
            self.__init_variables()
            
            self.run_start_time = datetime.datetime.now()
            
            self.user_email = self.request.get(settings.TASKS_QUEUE_KEY_NAME)
            
            self.is_test_user = shared.is_test_user(self.user_email)
            
            if self.user_email:
                
                # ============================================================
                #       Retrieve the import job DB record for this user
                # ============================================================
                self.process_tasks_job = model.ImportTasksJobV1.get_by_key_name(self.user_email)
                
                if self.process_tasks_job is None:
                    logging.error(fn_name + "<End> (No DB record)")
                    logservice.flush()
                    return
                    # TODO: Find some way of notifying the user?????
                    #   Which user? If we don't have a record, how was the job started? Who started it?
                else:
                    logging.debug(fn_name + "Retrieved process tasks job for " + self.user_email)
                    logservice.flush()
                    
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: Uploaded filename = '" + 
                            self.process_tasks_job.file_name + "'")
                        logservice.flush()                    
                    
                    if self.process_tasks_job.status in constants.ImportJobStatus.STOPPED_VALUES:
                        logging.warning(fn_name + "<End> Nothing to do (Job Status: " + self.process_tasks_job.status + ")")
                        logservice.flush()
                        return
                        
                    # When a job is initially started, the status should be STARTING, as set by the frontend.
                    # When a job is continued, the status may be any value other than one of the STOPPED_VALUES
                    # Occasionally, GAE starts a second worker, so when a worker is started, we need to check
                    # whether this is;
                    #   a new job (status == STARTING), or a continuing job, OR
                    #   a continuing job (status != STARTING and is_waiting_to_continue == True)
                    # So, if status != STARTING and is_waiting_to_continue == False, then there is already 
                    # another worker running, so this worker should report an error and exit without doing anything.
                    if self.process_tasks_job.status != constants.ImportJobStatus.STARTING:
                        # Check if this is a valid continuation job
                        if not self.process_tasks_job.is_waiting_to_continue:
                            logging.error(fn_name + "<End> Worker started for job with '" + 
                                str(self.process_tasks_job.status) + 
                                "' status, but job is not waiting to continue. Job is probably already running in another worker.")
                            return

                    # Update DB record to indicate that this worker is now handling this job
                    # This needs to be set ASAP, in case another worker is started, so other workers know that this
                    # job is not waiting to be executed.
                    self.process_tasks_job.is_waiting_to_continue = False
                    self.process_tasks_job.put()
                    
                    user = self.process_tasks_job.user
                    
                    if not user:
                        logging.error(fn_name + "No user object in DB record for " + self.user_email)
                        logservice.flush()
                        self._report_error("Problem with user details. Please restart.")
                        logging.warning(fn_name + "<End> (No user object)")
                        return
                        
                    # Retrieve credentials for user
                    # self.credentials = appengine.StorageByKeyName(
                        # model.Credentials, user.user_id(), "credentials").get()
                    self.credentials = StorageByKeyName(CredentialsModel, user.user_id(), 'credentials').get()
                        
                    if not self.credentials:
                        logging.error(fn_name + "No credentials in DB record for " + self.user_email)
                        logservice.flush()
                        self._report_error("Problem with credentials. Please restart and re-authenticate.")
                        logging.warning(fn_name + "<End> (No credentials)")
                        return
                  
                    if self.credentials.invalid:
                        logging.error(fn_name + "Invalid credentials in DB record for " + self.user_email)
                        logservice.flush()
                        self._report_error("Invalid credentials. Please restart and re-authenticate.")
                        logging.warning(fn_name + "<End> (Invalid credentials)")
                        return
                  
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: User is test user %s" % self.user_email)
                        logservice.flush()
                    
                    retry_count = settings.NUM_API_TRIES
                    while retry_count > 0:
                        retry_count = retry_count - 1
                        # Accessing tasklists & tasks services may take some time (especially if retries due to 
                        # DeadlineExceeded), so update progress so that job doesn't stall
                        self._update_progress("Connecting to server ...")  # Update progress so that job doesn't stall
                        try:
                            # ---------------------------------------------------------
                            #       Connect to the tasks and tasklists services
                            # ---------------------------------------------------------
                            http = httplib2.Http()
                            http = self.credentials.authorize(http)
                            service = discovery.build("tasks", "v1", http=http)
                            self.tasklists_svc = service.tasklists()
                            self.tasks_svc = service.tasks()
                            
                            # JS 2012-09-22: A new problem surface today, where the result of inserting 
                            # (creating) a new tasklist is 
                            #       u'kind': u'discovery#restDescription'
                            # instead of
                            #       u'kind': u'tasks#taskList'
                            # The tasklist IS created, it is just the returned object that is not correct.
                            # This means that we don't get the ID of the newly created tasklist, so we
                            # can't insert any tasks!
                            # Retrieving a list of tasklists before the first insert seems to prevent that from happening.
                            # This will also throw DailyLimitExceededError BEFORE processing starts if no quota available.
                            logging.debug(fn_name + "DEBUG: Retrieving dummy list of tasklists, to 'prep' the service")
                            dummy_list = self.tasklists_svc.list().execute()

                            
                            break # Success
                    
                        except apiclient_errors.HttpError, e:
                            self._handle_http_error(fn_name, e, retry_count, "Error connecting to Tasks services")
                            
                        except Exception, e:
                            self._handle_general_error(fn_name, e, retry_count, "Error connecting to Tasks services")
                    
                    if self.process_tasks_job.status == constants.ImportJobStatus.STARTING:
                        # Only change status to initialising the first time
                        # On subsequent calls, we keep the status of the previous run 
                        self.process_tasks_job.status = constants.ImportJobStatus.INITIALISING
                        self.process_tasks_job.message = "Preparing for import ..."
                    if self.process_tasks_job.is_paused:
                        logging.debug(fn_name + "Continuing paused job: " + self.process_tasks_job.pause_reason)
                        logservice.flush()
                        self.process_tasks_job.is_paused = False # Un-pause paused job
                    self.process_tasks_job.pause_reason = constants.PauseReason.NONE
                    self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    self._log_job_progress()
                    self.process_tasks_job.put()
                    
                    
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
            self.process_tasks_job.pause_reason = constants.PauseReason.DAILY_LIMIT_EXCEEDED
            
            if not self.import_job_state:
                # Job hasn't started yet
                logging.warning(fn_name + "Job not started - Daily limit has been exceeded")
                logservice.flush()
            elif self.process_tasks_job.is_paused:
                # User tried to continue a paused job, but daily limit has been exceeded
                #   Can happen if user tries to continue before quota has been refreshed, 
                #   or if someone else has already used all the quota.
                logging.warning(fn_name + "Unable to start paused job, as daily limit has been exceeded")
                logservice.flush()
            else:
                # Current job cannot continue because daily API quota has been used
                self.process_tasks_job.is_paused = True
                if self.process_tasks_job.status == constants.ImportJobStatus.IMPORTING:
                    # Current data row was NOT processed, so change data_row_num so it now refers to 
                    # the last successfully imported row.
                    #     data_row_num is incremented at the start of the task processing loop
                    self.import_job_state.data_row_num = self.import_job_state.data_row_num - 1
                self.process_tasks_job.pickled_import_state = pickle.dumps(self.import_job_state)
                self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
                self.process_tasks_job.data_row_num = self.import_job_state.data_row_num
                self.process_tasks_job.message = "Processed " + str(self.import_job_state.data_row_num) + " of " + \
                    str(self.process_tasks_job.total_num_rows_to_process) + " data rows."
                logging.warning(fn_name + "Paused import job at data row " + str(self.import_job_state.data_row_num) +
                    "; daily limit exceeded.")
                logservice.flush()
            self._log_job_progress()
            _log_job_state(self.import_job_state) # Log job state info
            self.process_tasks_job.put()
        
        except Exception, e:
            logging.exception(fn_name + "Caught outer Exception:") 
            logservice.flush()
            self._report_error("System Error: " + shared.get_exception_msg(e))
            

        logging.debug(fn_name + "<End>, user = " + self.user_email)
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
            # file_name = unicode(self.blob_info.filename)
            # logging.debug(fn_name + "Filename = " + file_name + ", key = " + str(blob_key))
            # logservice.flush()
            
            if self.is_test_user:
                try:
                    logging.debug(fn_name + "TEST: Filename = " + unicode(self.blob_info.filename))
                except Exception, e:
                    logging.warning(fn_name + "TEST: Unable to log filename: " + shared.get_exception_msg(e))
            logging.debug(fn_name + "Filetype: '" + str(self.process_tasks_job.file_type) + "'")
            logservice.flush()
            if self.process_tasks_job.file_type == 'gtbak': 
                # Data file contains two pickled values; file_format_version & tasks_data
                # We need to read file_format_version first, so we can get to tasks_data, but we can 
                # ignore the value of file_format_version here, because it was already checked in import_tasks.py
                file_format_version = pickle.load(blob_reader) 
                tasks_data = pickle.load(blob_reader)
            else:
                tasks_data=unicodecsv.DictReader(blob_reader,dialect='excel')
            
            import_method = self.process_tasks_job.import_method
            
            if import_method in constants.ImportMethod.USE_SUFFIX_VALUES:
                # Use the suffix which was set when the import job was created
                # This is either the datetime, or the suffix entered by the user (from the webpage)
                tasklist_suffix = self.process_tasks_job.import_tasklist_suffix
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
                logservice.flush()
                # Load state from previous run
                self.import_job_state = pickle.loads(self.process_tasks_job.pickled_import_state)
                
                _log_job_state(self.import_job_state)
                
                # -----------------------------------------------------------------------
                #       Skip first 'n' lines that were processed in a previous job
                # -----------------------------------------------------------------------
                logging.debug(fn_name + "Skipping first " + str(self.import_job_state.data_row_num) + " data rows")
                logservice.flush()
                dummy_data_row_num = 0
                while dummy_data_row_num < self.import_job_state.data_row_num:
                    if self.process_tasks_job.file_type == 'gtbak':
                        # Pop the task from the start of the list
                        tasks_data.pop(0)
                    else:
                        # Skip the row in the CSV file
                        tasks_data.next()
                    
                    dummy_data_row_num += 1
            else:
                # -----------------------
                #       First run
                # -----------------------
                logging.debug(fn_name + "First run, so using initial state values")
                logservice.flush()
                self.import_job_state = ImportJobState()
                self.import_job_state.prev_tasklist_ids = {} # "Tasklist name" : "Tasklist ID"
                
                # Stores the ID and name of the default tasklist in import_job_state
                try:
                    default_tasklist = self._get_default_tasklist()
                    self.import_job_state.default_tasklist_id = default_tasklist['id'] # Store for later use by others
                    self.import_job_state.default_tasklist_orig_name = default_tasklist['title'] # Store for later use by others
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: Stored original tasklist name [" + 
                            self.import_job_state.default_tasklist_orig_name + "]")
                        logservice.flush()
                except Exception, e:
                    # This should never fail, but catch it just in case
                    logging.exception(fn_name + "Exception retrieving default tasklist ID and title")
                    logservice.flush()
                    self._report_error("Error retrieving default tasklist. Please report this error using the link below")
                    return
                
                
                if import_method == constants.ImportMethod.DELETE_BEFORE_IMPORT:
                    logging.debug(fn_name + "import_method == '" + import_method + 
                        "', so deleting all existing tasklists")
                    logservice.flush()
                    
                    # Delete all existing tasklists
                    self._update_progress("Deleting existing tasklists", force=True)
                    self._delete_tasklists(self._get_tasklists())
                    self._update_progress('')
                elif import_method in constants.ImportMethod.RETRIEVE_EXISTING_TASKLISTS_VALUES:
                    logging.debug(fn_name + "import_method == " + import_method + ", so retrieving existing tasklists")
                    logservice.flush()
                    
                    # Retrieve and store the IDs of existing tasklists.
                    self._update_progress("Retrieving existing tasklists", force=True)
                    tasklists = self._get_tasklists()
                    self._update_progress('')

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
            self.process_tasks_job.message = ''
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            self.process_tasks_job.status = constants.ImportJobStatus.IMPORTING
            self._log_job_progress()
            self.process_tasks_job.put()
            
            skip_this_list = False
        
            
            
            for task_row_data in tasks_data:
                self.import_job_state.data_row_num += 1
                self.import_job_state.num_tasks_in_list += 1
                
                # -------------------------------------------
                #       Check for valid 'status' value
                # -------------------------------------------
                status = task_row_data.get('status')
                if status not in ('completed', 'needsAction'):
                    self._report_error("Invalid status value '" + unicode(status) + 
                        "' in data row " + str(self.import_job_state.data_row_num),
                        log_as_invalid_data=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                        "<End> due to invalid 'status' value")
                    logservice.flush()
                    return 
                    
                    
                # ---------------------------------
                #       Retrieve depth value. 
                # ---------------------------------
                # Use the same method that is used to check for valid depth values when the user uploads the file, 
                # but don't compare with previous row, because that was already done when file was uploaded.
                result, depth, err_msg1, err_msg2 = check_task_values.depth_is_valid(task_row_data, self.import_job_state.data_row_num, self.is_test_user)
                if not result:
                    self._report_error(err_msg1 + ": " + err_msg2, log_as_invalid_data=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                        "<End> due to invalid 'depth' value")
                    logservice.flush()
                    return False
                
                if task_row_data.has_key('depth'):
                    # Delete the depth property, because it is not used by the server
                    del(task_row_data['depth'])
                
                # ---------------------------------------------------
                #            Process tasklist for this task
                # ---------------------------------------------------
                # We assume that the 'tasklist_name' column exists, because it was checked when the file was uploaded
                tasklist_name = task_row_data['tasklist_name']
                if not tasklist_name:
                    self._report_error('Missing value for "tasklist_name" column in data row ' + 
                        str(self.import_job_state.data_row_num),
                        log_as_invalid_data=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL +
                        "<End> due to missing 'tasklist_name' value")
                    logservice.flush()
                    return 
                # 'tasklist_name' is not part of the Tasks resource, so delete it from the dictionary
                del(task_row_data['tasklist_name'])
                
                if tasklist_name != self.import_job_state.prev_tasklist_name:
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: Found first task of new tasklist '" + unicode(tasklist_name) + 
                            "' at data row " + str(self.import_job_state.data_row_num) + ", previous tasklist was '" + 
                            unicode(self.import_job_state.prev_tasklist_name) + "'")
                        logservice.flush()
                    self.import_job_state.num_tasks_in_list = 1 # Found new tasklist name, so this is 1st task
                    
                    # -------------------------------------------------------------------
                    #      Processing a new/different tasklist in the imported data
                    # -------------------------------------------------------------------
                    if tasklist_name == "@default":
                        # Import tasks into default tasklist
                        # Tasks will be inserted at the top of the default tasklist
                        self.import_job_state.tasklist_id = self.import_job_state.default_tasklist_id
                        existing_tasklist_id = self.import_job_state.default_tasklist_id
                        logging.debug(fn_name + "DEBUG: Inserting task(s) into default tasklist")
                        logservice.flush()
                        if not self.process_tasks_job.used_default_tasklist:
                            self.process_tasks_job.used_default_tasklist = True
                            self.process_tasks_job.put()

                    else:
                        existing_tasklist_id = self.import_job_state.prev_tasklist_ids.get(tasklist_name)
                        if existing_tasklist_id:
                            if import_method == constants.ImportMethod.REPLACE_TASKLIST_CONTENT:
                                if self.is_test_user:
                                    logging.debug(fn_name + "Deleting existing tasklist '" + tasklist_name + 
                                        "' before creating new tasklist")
                                else:
                                    logging.debug(fn_name + "Deleting existing tasklist before creating new tasklist")
                                logservice.flush()
                                # It is much quicker to delete an entire tasklist than it is to individually delete
                                # all the tasks in an existing list. So we delete the existing tasklist before creating new tasklist
                                # TODO: Handle potential duplicate tasklist names; delete ALL tasklists with tasklist_name
                                #       Would require significant change because the prev_tasklist_ids dict only allows
                                #       one instance of a given tasklist name (contains last entry to be stored)
                                self._delete_tasklist_by_id(existing_tasklist_id, tasklist_name)
                            else:
                                # Add tasks to existing tasklist
                                self.import_job_state.tasklist_id = existing_tasklist_id
                            
                            if import_method == constants.ImportMethod.SKIP_DUPLICATE_TASKLIST:
                                # Tasklist with this name already exists, so skip all tasks in this list.
                                # Set skip_this_list variable so that we will continue to skip rows (until tasklist
                                # name in import data changes).
                                skip_this_list = True
                                self.import_job_state.prev_tasklist_name = tasklist_name
                                if self.is_test_user:
                                    logging.debug(fn_name + "TEST: Skip importing tasks in tasklist '" + 
                                        tasklist_name + "' from data row " + str(self.import_job_state.data_row_num) + 
                                        " because it already exists")
                                    logservice.flush()
                                continue
                                
                        if import_method in constants.ImportMethod.CREATE_NEW_TASKLIST_VALUES or not existing_tasklist_id:
                            # ----------------------------------
                            #       Create new tasklist
                            # ----------------------------------
                            tasklist = { 'title': tasklist_name + tasklist_suffix }
                            
                            # Update progress so the user sees some progress, and so that job doesn't stall, as creating
                            # a new tasklist sometimes takes several seconds
                            self._update_progress(force=True)
                            
                            retry_count = settings.NUM_API_TRIES
                            while retry_count > 0:
                                retry_count = retry_count - 1
                                try:
                                    result = self.tasklists_svc.insert(body=tasklist).execute()
                                    self.import_job_state.tasklist_id = result['id']
                                    self.import_job_state.prev_tasklist_ids[tasklist_name] = self.import_job_state.tasklist_id
                                    if self.is_test_user:
                                        logging.debug(fn_name + "TEST: Created new Tasklist [" + unicode(result['title']) + 
                                           "], ID = " + self.import_job_state.tasklist_id)
                                        logging.debug(fn_name + "TEST: DEBUG: result ==>")
                                        logging.debug(result)
                                        logservice.flush()
                                    break # Success
                                        
                                except apiclient_errors.HttpError, e:
                                    if retry_count == 0 and e.resp.status == 400:
                                        # There have been 2 types of 400 error; "Bad Request" and "Invalid Value"
                                        # "Invalid Value" can be caused by having a taklist name > 256 character,
                                        # which is being checked in the frontend as of 2013-06-05.
                                        # Log details of the tasklist body that caused the HttpError 400 error in order
                                        # to allow analysis of other possible causes of this error.
                                        logging.warning(fn_name + "DEBUG: HttpError 400 creating new tasklist on retry 0: " +
                                            shared.get_exception_msg(e))
                                        logging.debug("DEBUG: Tasklist body ==>")
                                        logservice.flush()
                                        try:
                                            logging.debug(tasklist)
                                        except Exception, e:
                                            logging.exception(fn_name + "DEBUG: Exception logging tasklist body")
                                        logservice.flush()    
                                        
                                        # Report 1st part of message to user using _report_error()
                                        if "invalid value" in e._get_reason().lower():
                                            logging.debug(fn_name + "DEBUG: Invalid value, so advising user")
                                            # If it was an "Invalid Value" error, return a meaningful message to the user
                                            self._report_error(
                                                "Invalid tasklist name. Please check the tasklist name at data row " + 
                                                str(self.import_job_state.data_row_num), log_as_invalid_data=True)
                                        else:
                                            logging.debug(fn_name + "DEBUG: Reason = " + e._get_reason())
                                            self._report_error("Error creating tasklist for task at data row " + 
                                                str(self.import_job_state.data_row_num), log_as_invalid_data=False)
                                        logservice.flush()
                                        
                                        # Report 2nd part of message to user using _handle_http_error(), which will
                                        # also log the HttpError, and cleanly terminate the worker
                                        self._handle_http_error(fn_name, e, retry_count, 
                                            "Invalid tasklist name [" + unicode(tasklist_name) + "]")
                                    else:
                                        logging.warning(fn_name + "DEBUG: Error creating new tasklist, either not 400 or retry > 0: " +
                                            shared.get_exception_msg(e))
                                        self._handle_http_error(fn_name, e, retry_count, 
                                            "Error creating new tasklist for task at data row " + 
                                            str(self.import_job_state.data_row_num))
                                    
                                except Exception, e:
                                    self._handle_general_error(fn_name, e, retry_count, 
                                        "Error creating new tasklist for task at data row " + 
                                        str(self.import_job_state.data_row_num))
                                
                                
                    # Importing into a new list, or new tasks into an existing list, so start with no parents or siblings
                    self.import_job_state.parents_ids = [''] 
                    self.import_job_state.sibling_ids = [] # New list, so start with no siblings
                    self.import_job_state.prev_depth = 0
                    
                    self.import_job_state.num_tasklists += 1
                    self.import_job_state.prev_tasklist_name = tasklist_name
                    skip_this_list = False

                    
                if import_method == constants.ImportMethod.SKIP_DUPLICATE_TASKLIST and skip_this_list:
                    # Tasklist exists, so do not import tasks in this tasklist
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: Skipping data row " + str(self.import_job_state.data_row_num) + " because tasklist '" + 
                            tasklist_name + "' already exists")
                        logservice.flush()
                    continue
                
                # -------------------------------------------------------
                #       Convert date/time strings to RFC-3339 format
                # -------------------------------------------------------
                import_tasks_shared.set_RFC3339_timestamp(task_row_data, 'due', constants.DUE_DATE_FORMATS)
                import_tasks_shared.set_RFC3339_timestamp(task_row_data, 'completed', constants.COMPLETED_DATETIME_FORMATS)
                
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
                # List of self.import_job_state.parents_ids is updated after task has been added, 
                # because the current task may be the parent of the next task
                if depth > self.import_job_state.prev_depth+1:
                    # Child can only be immediate child of previous task (i.e., previous depth + 1)
                    self._report_error("Invalid depth value [" + str(depth) + "] in data row " + 
                        str(self.import_job_state.data_row_num) + 
                        " is more than 1 greater than previous task's depth [" + 
                        str(self.import_job_state.prev_depth) + "]",
                        log_as_invalid_data=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + "<End> (invalid depth value)")
                    logservice.flush()
                    return 
                
                # -------------------------
                #    Find parent task ID
                # -------------------------
                if depth > len(self.import_job_state.parents_ids)-1:
                    # This could be because 1st task in a new tasklist has a depth > 0
                    self._report_error("Invalid depth [" + str(depth) + "] for task in data row " + 
                        str(self.import_job_state.data_row_num) + "; No parent task at depth " + str(depth-1),
                        log_as_invalid_data=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + "<End> (No immmediate parent)")
                    logservice.flush()
                    return 
                
                try:
                    # parent_id will be empty string for root tasks (depth = 0)
                    self.import_job_state.parent_id = self.import_job_state.parents_ids[depth]
                except Exception, e:
                    self._report_error("Unable to find parent ID for task in data row " + 
                        str(self.import_job_state.data_row_num) + " with depth [" + str(depth) + 
                        "]; Unable to determine parent. Previous task's depth was " + 
                        str(self.import_job_state.prev_depth) + 
                        " Exception: " + shared.get_exception_msg(e))
                    logging.warning(fn_name + "<End> (Error retrieving parent ID)")
                    logservice.flush()
                    return 
                
                # Find sibling (previous) ID
                if depth == self.import_job_state.prev_depth + 1:
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
                    try:
                        if not v or len(v) == 0:
                            empty_keys.append(k)
                    except Exception, e:
                        # This exception handling caters for values which have a value, but no length
                        # As far as I know, only int values can cause this, but there may be other types too
                        
                        column_str = ''
                        value_str = ''
                        try:
                            # logging.error(fn_name + "Exception checking if [" + unicode(k) + 
                                # "] property is empty in data row " + str(self.import_job_state.data_row_num) + 
                                # ". Value is [" + unicode(v) + "] : " + shared.get_exception_msg(e))
                            # Try to tell the user what went wrong
                            column_str = " for '" + unicode(k) + "' column"
                            value_str = "[" + unicode(v) + "]"
                            err_msg = "Found unexpected value " + value_str + column_str + \
                                " whilst checking for empty properties in data row " + \
                                str(self.import_job_state.data_row_num)
                                
                            logging.warning(fn_name + "Error checking for empty properties: " + err_msg)
                            
                        except Exception, e1:
                            err_msg = "Found unexpected value " + value_str + column_str + \
                                " whilst checking for empty properties in data row " + \
                                str(self.import_job_state.data_row_num) + ". Orig exception = " + shared.get_exception_msg(e)
                            # Unable to log details of what caused the error, so log/return a more generic error message
                            logging.exception(fn_name + 
                                "Exception logging exception whilst trying to delete empty keys. " + err_msg)
                                
                        # self._report_error(err_msg)
                        # logging.warning(fn_name + "<End> (Exception deleting empty properties)")
                        # logservice.flush()
                        # return
                        
                        # ??? Continue processing. If this exception happens, it may not matter
                        # because the property is presumably not empty, so shouldn't cause a server error.
                        
                for k in empty_keys:
                    del(task_row_data[k])
                
                # ================================================================
                #               Insert the task into the tasklist
                # ================================================================
                result = None
                
                # Retry, to handle occasional API timeout
                retry_count = settings.NUM_API_TRIES
                while retry_count > 0:
                    retry_count = retry_count - 1
                    # ------------------------------------------------------
                    #       Update progress so that job doesn't stall
                    # ------------------------------------------------------
                    try:
                        self._update_progress() # Update progress so that job doesn't stall if insert takes a long time
                                                    
                        # ========================
                        #       Insert task
                        # ========================
                        result = self.tasks_svc.insert(tasklist=self.import_job_state.tasklist_id,
                                                       body=task_row_data, 
                                                       parent=self.import_job_state.parent_id, 
                                                       previous=self.import_job_state.sibling_id).execute()
                        
                        task_id = result['id']
                        
                        if not task_id:
                            logging.error(fn_name + "No id returned for task insert ==>")
                            try:
                                logging.error(result)
                            except Exception, e:
                                logging.error(fn_name + "Unable to log insert result:" + shared.get_exception_msg(e))
                            logservice.flush()
                            raise Exception("No id returned for task insert")
                            
                        self.import_job_state.num_of_imported_tasks += 1
                        
                        # msg = "Imported " + str(self.import_job_state.num_of_imported_tasks) + " of " + str(self.process_tasks_job.total_num_rows_to_process) + " data rows."
                        # if self.import_job_state.num_of_imported_tasks == 1:
                            # self._update_progress(msg, force=True) # Force 1st update so user sees some progress
                        # else:
                            # self._update_progress(msg)

                        if self.import_job_state.num_of_imported_tasks == 1:
                            self._update_progress(force=True) # Force 1st update so user sees some progress
                        else:
                            self._update_progress()

                        # Succeeded, so continue
                        break
                    
                    except KeyError, e:
                        # This usually indicates that the result did not return a valid task object
                        logging.exception(fn_name + "KeyError inserting task for data row" + 
                            str(self.import_job_state.data_row_num) + ". result ==>")
                        try:
                            logging.error(result)
                        except Exception, e:
                            logging.error(fn_name + "Unable to log insert result:" + shared.get_exception_msg(e))
                        logging.debug(fn_name +     "  Retry count = " + str(retry_count))
                        logservice.flush()
                    
                    except apiclient_errors.HttpError, e:
                        if self.is_test_user:
                            # DEBUG
                            logging.debug(fn_name + "TEST: DEBUG: HttpError inserting task: " + shared.get_exception_msg(e))
                            logging.debug(fn_name + "TEST: DEBUG: task data ==>")
                            logging.debug(task_row_data)
                            logging.debug(fn_name + "TEST: DEBUG: result ==>")
                            logging.debug(result)
                            
                            
                        created_missing_task = False
                        if retry_count == 0 and e.resp.status == 404:
                            # We've tried to insert the task 3 times, but the parent or sibling task doesn't exist,
                            # so we need to recreate the missing task.
                            # See http://code.google.com/p/import-tasks/issues/detail?id=1
                            #   BUG 2012-05-08 01:02
                            #       This happens very occassionaly, where insert() returns 404 ("Not found") when creating a task,
                            #       even though we have an ID for the previously created sibling &/or parent task.
                            #       i.e., We have the ID returned from a previous insert(), but we get a 404 when we try to
                            #             access that task.
                            logging.error(fn_name + "INSERT FAILED - missing parent or sibling: " + 
                                shared.get_exception_msg(e))
                            logging.error(fn_name + "    Inserting task number " + str(self.import_job_state.num_tasks_in_list) +
                                " in tasklist number " + str(self.process_tasks_job.num_tasklists))
                            logservice.flush()
                            
                            if self.import_job_state.parent_id:
                                # Check if parent task exists
                                if not import_tasks_shared.task_exists(self.tasks_svc, self.import_job_state.tasklist_id, self.import_job_state.parent_id):
                                    # -------------------------------------------
                                    #   Attempt to recreate missing parent task
                                    # -------------------------------------------
                                    logging.debug(fn_name + "Attempting to recreate missing parent")
                                    logservice.flush()
                                    new_parent_id = self._insert_missing_task(self.import_job_state.prev_tasks_data)
                                    if new_parent_id:
                                        logging.info(fn_name + "Recreated missing parent for data row " + str(self.import_job_state.data_row_num))
                                        self.import_job_state.parent_id = new_parent_id
                                        created_missing_task = True
                                        # Reset the retry count so that we can create the 'current' task 
                                        # after we've recreated the missing parent task
                                        retry_count = settings.NUM_API_TRIES
                                    else:
                                        self._report_error("Unable to recreate missing parent task for data row " + 
                                            str(self.import_job_state.data_row_num))
                                        logging.error(fn_name + "<End> (Unable to recreate missing parent task)")
                                        logservice.flush()
                                        return
                                
                            if self.import_job_state.sibling_id:
                                # Check if sibling task exists
                                if not import_tasks_shared.task_exists(self.tasks_svc, self.import_job_state.tasklist_id, self.import_job_state.sibling_id):
                                    # ---------------------------------------------
                                    #   Attempt to recreate missing sibling task
                                    # ---------------------------------------------
                                    logging.debug(fn_name + "Attempting to recreate missing sibling")
                                    logservice.flush()
                                    new_sibling_id = self._insert_missing_task(self.import_job_state.prev_tasks_data)
                                    if new_sibling_id:
                                        logging.info(fn_name + "Recreated missing sibling for data row " + str(self.import_job_state.data_row_num))
                                        self.import_job_state.sibling_id = new_sibling_id
                                        created_missing_task = True
                                        # Reset the retry count so that we can create the 'current' 
                                        # task after we've recreated the missing previous sibling task
                                        retry_count = settings.NUM_API_TRIES
                                    else:
                                        self._report_error("Unable to recreate missing previous task for data row " + 
                                            str(self.import_job_state.data_row_num))
                                        logging.error(fn_name + "<End> (Unable to recreate missing previous task)")
                                        logservice.flush()
                                        return
                                
                        # Don't report error if we successfully created a missing task
                        if not created_missing_task:
                            # Report non-404 error, or error where we were unable to recreate missing task
                            if retry_count == 0:
                                logging.debug(fn_name + "DEBUG: HttpError " + str(e.resp.status) + ": Failed task:" +
                                    "\n    tasklist_id = [" + str(self.import_job_state.tasklist_id) +
                                    "]\n    parent_id = [" + str(self.import_job_state.parent_id) +
                                    "]\n    previous_id = [" + str(self.import_job_state.sibling_id) +
                                    "]\n    Task num = " + str(self.import_job_state.data_row_num) +
                                    "\n    Depth = " + str(depth))
                                logservice.flush()
                                
                                import_tasks_shared.check_task_params_exist(
                                    self.tasklists_svc,
                                    self.tasks_svc,
                                    self.import_job_state.tasklist_id, 
                                    self.import_job_state.parent_id, 
                                    self.import_job_state.sibling_id)
                                    
                            self._handle_http_error(fn_name, e, retry_count, 
                                "Http error " + str(e.resp.status) + " creating task from data row " + 
                                str(self.import_job_state.data_row_num))
                        
                    except Exception, e:
                        if retry_count == 0:
                            logging.debug(fn_name + "DEBUG: Exception: Failed task:" +
                                "\n    tasklist_id = [" + str(self.import_job_state.tasklist_id) +
                                "]\n    parent_id = [" + str(self.import_job_state.parent_id) +
                                "]\n    previous_id = [" + str(self.import_job_state.sibling_id) +
                                "]\n    Task num = " + str(self.import_job_state.data_row_num) +
                                "\n    Depth = " + str(depth))
                            logservice.flush()
                                
                            import_tasks_shared.check_task_params_exist(
                                self.tasklists_svc,
                                self.tasks_svc,
                                self.import_job_state.tasklist_id, 
                                self.import_job_state.parent_id, 
                                self.import_job_state.sibling_id)
                                
                        self._handle_general_error(fn_name, e, retry_count, 
                            "System error creating task from data row " + str(self.import_job_state.data_row_num))

                            
                # Save the details of the task we just created.
                # This is required because very occassionally, Google apparently doesn't create a task, 
                # even though .insert() returns a task ID. See BUG 2012-06-09 15:43:47.771 and BUG 2012-05-08 01:02
                # This manifests as the next .insert() failing with "Not Found"
                #
                # We only need to save the last task that we created, because that is the only one that could have failed;
                # each subsequent tasks only depends on its most recent previous task (parent or sibling).
                # For example, if we have the following task structure;
                #       A
                #           B
                #               C
                #               D
                #                   E
                #           F
                #
                #       Task  Depends on  As
                #       A     Nothing
                #       B     A           parent
                #       C     B           parent
                #       D     B,C         parent, sibling [1]
                #       E     D           parent
                #       F     A           parent [2]
                # [1] B has already been confirmed to exist, because it was used by C
                # [2] A has already been confirmed to exist, because it was used by B
                # 
                # If an .insert() fails, we need to re-create the missing task
                # We then need to update the parents_ids and sibling_ids with the new task ID 
                
                self.import_job_state.prev_tasks_data = { 'id' : task_id, 
                    'tasklist_id' : self.import_job_state.tasklist_id, 
                    'task_row_data' : task_row_data, 
                    'parent_id' : self.import_job_state.parent_id, 
                    'sibling_id' : self.import_job_state.sibling_id }
                
                
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

                # --------------------------------------------------------------------------
                #       Store ID of this task as sibling for next task at this depth
                # --------------------------------------------------------------------------
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
                # If the job cannot be finished within the maximum allowable time, we need to stop this
                # worker and start another worker to continue the process.
                run_time = datetime.datetime.now() - self.run_start_time
                run_seconds = run_time.seconds
                if run_seconds > settings.MAX_WORKER_RUN_TIME:
                    self._start_another_worker(run_time, run_seconds)
                    return
            
            logging.debug(fn_name + "Finished importing tasks")
            logservice.flush()
            
            if self.import_job_state.default_tasklist_was_renamed: 
                # Try to rename the default tasklist back to its original name
                # Note that sometimes the Google Tasks Server appears to cache tasklist name, so the test for an existing
                # tasklist name may return stale results.
                # Note also that the Google Tasks web page caches data, so tasklist names may appear to revert to older names! 
                self._update_progress(force=True)
                try:
                    logging.debug(fn_name + "DEBUG: Original tasklist was renamed. Attempting to rename default tasklist back to original tasklist name")
                    
                    # Check if original tasklist name exists in the current list of tasknames
                    tasklists = self._get_tasklists()
                    found_orig_name = False
                    
                    for tasklist in tasklists:
                        tasklist_name = tasklist['title']
                        if tasklist_name.strip() == self.import_job_state.default_tasklist_orig_name:
                            found_orig_name = True
                    
                    # Rename default tasklist back to orig name
                    if found_orig_name:
                        if self.is_test_user:
                            logging.info(fn_name + 
                                "Unable to rename default tasklist back to original name, because a tasklist exists with that name [" + 
                                self.import_job_state.default_tasklist_orig_name + "]")
                        else:
                            logging.info(fn_name + "Unable to rename default tasklist back to original name, because a tasklist exists with that name")
                        logservice.flush()
                    else:
                        if self.is_test_user:
                            logging.debug(fn_name + "TEST: Renaming default tasklist to original tasklist name [" + 
                                self.import_job_state.default_tasklist_orig_name + "]")
                            logservice.flush()
                        default_tasklist = self._get_default_tasklist()
                        default_tasklist['title'] = self.import_job_state.default_tasklist_orig_name
                        
                        while retry_count > 0:
                            retry_count = retry_count - 1
                            self._update_progress()  # Update progress so that job doesn't stall
                            
                            try:
                                result = self.tasklists_svc.update(tasklist=self.import_job_state.default_tasklist_id, 
                                    body=default_tasklist).execute()
                                self._update_progress()
                                self.import_job_state.default_tasklist_was_renamed = False
                                if self.is_test_user:
                                    logging.info(fn_name + "Renamed default tasklist back to original tasklist name [" +
                                    self.import_job_state.default_tasklist_orig_name + "]")
                                else:
                                    logging.info(fn_name + "Renamed default tasklist back to original tasklist name")
                                logservice.flush()
                                break
                                
                            except Exception, e:
                                # Don't handling this with the global error handlers, because it is not critical if this fails.
                                # Renaming the default tasklist is icing. If it fails, that does not mean that the import failed,
                                # so don't report it as an error to the user
                                logging.exception(fn_name + 
                                    "NOT CRITICAL: Exception renaming default tasklist back to original tasklist name. retry_count = " +
                                    str(retry_count))
                                logservice.flush()
                        
                except Exception, e:
                    # Don't handling this with the global error handlers, because it is not critical if this fails.
                    # Renaming the default tasklist is icing. If it fails, that does not mean that the import failed,
                    # so don't report it as an error to the user
                    logging.exception(fn_name + "NOT CRITICAL: Unable to rename default tasklist back to original tasklist name")
                    logservice.flush()
                
            logging.debug(fn_name + "Finalising import job")
            logservice.flush()
            self._finalise_job()
            
            
        except DailyLimitExceededError, e:
            raise e
            
        except Exception, e:
            logging.exception(fn_name + "Caught outer Exception:") 
            logservice.flush()
            self._report_error("System Error: " + shared.get_exception_msg(e))
            
        logging.debug(fn_name + "<End>")
        logservice.flush()

        
    def _start_another_worker(self, run_time, run_seconds):
    
        fn_name = "_start_another_worker: "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
    
        try:
            self.process_tasks_job.pickled_import_state = pickle.dumps(self.import_job_state)
            self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
            self.process_tasks_job.data_row_num = self.import_job_state.data_row_num
            processing_seconds = run_time.seconds + run_time.microseconds / 1000000.0
            self.process_tasks_job.total_processing_seconds = \
                self.process_tasks_job.total_processing_seconds + processing_seconds
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            
            total_processing_time_str = "%.1f seconds" % self.process_tasks_job.total_processing_seconds
            logging.info(fn_name + "Current worker has run for " + str(run_seconds) + 
                " seconds since starting at " + str(self.run_start_time))
            logging.info(fn_name + "Total import job run time " + total_processing_time_str + 
                " since job was started at " + str(self.process_tasks_job.job_start_timestamp))
            logservice.flush()
            
            if self.import_job_state.num_of_imported_tasks == self.import_job_state.data_row_num:
                self.process_tasks_job.message = ''
            else:
                self.process_tasks_job.message = "Imported " + \
                    str(self.import_job_state.num_of_imported_tasks) + \
                    " tasks from " + str(self.import_job_state.data_row_num) + " data rows."
            self._log_job_progress()
            _log_job_state(self.import_job_state) # Log job state info
            self.process_tasks_job.is_waiting_to_continue = True # Job is waiting for the next worker to take over
            self.process_tasks_job.put()
            
            try:
                # Add the request to the tasks queue, passing in the user's email 
                # so that the worker can access the database record
                q = taskqueue.Queue(settings.PROCESS_TASKS_REQUEST_QUEUE_NAME)
                t = taskqueue.Task(url=settings.WORKER_URL, 
                    countdown=5, # Wait 5 seconds before starting next worker, to prevent multiple instances
                    params={settings.TASKS_QUEUE_KEY_NAME : self.user_email}, method='POST')
                logging.debug(fn_name + "Continue import in another job. Adding task to " + 
                    str(settings.PROCESS_TASKS_REQUEST_QUEUE_NAME) + 
                    " queue, for " + self.user_email)
                logservice.flush()
                q.add(t)
                logging.debug(fn_name + "<End> Added follow on task to taskqueue")
                logservice.flush()
                return
            except Exception, e:
                # Don't mark as error, so Blobstore doesn't get deleted. This allows user to
                # choose to continue job next time they use the app.
                logging.exception(fn_name + "Exception adding task to taskqueue.")
                logservice.flush()
                self._report_error("Error continuing import job: " + shared.get_exception_msg(e))
                logging.error(fn_name + "<End> (error adding job to taskqueue)")
                logservice.flush()
                return
                
        except Exception, e:
            logging.exception(fn_name + "Caught outer Exception:") 
            logservice.flush()
            self._report_error("System Error: " + shared.get_exception_msg(e))
            
                
        logging.debug(fn_name + "<End>")
        logservice.flush()
        
        
    def _finalise_job(self):
        
        fn_name = "_finalise_job: "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            end_time = datetime.datetime.now()
            processing_time = end_time - self.run_start_time
            
            processing_seconds = processing_time.seconds + processing_time.microseconds / 1000000.0
            
            
            # ---------------------------------------
            #       Mark import job complete
            # ---------------------------------------
            self.process_tasks_job.pickled_import_state = None
            self.process_tasks_job.status = constants.ImportJobStatus.IMPORT_COMPLETED
            self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
            self.process_tasks_job.data_row_num = self.import_job_state.data_row_num
            self.process_tasks_job.num_tasklists = self.import_job_state.num_tasklists
            self.process_tasks_job.total_processing_seconds = self.process_tasks_job.total_processing_seconds + processing_seconds
            total_processing_time_str = "%.1f seconds" % self.process_tasks_job.total_processing_seconds
            self.process_tasks_job.message = "Imported " + str(self.import_job_state.num_of_imported_tasks) + \
                " tasks into " +  str(self.import_job_state.num_tasklists) + " tasklists from " + \
                str(self.import_job_state.data_row_num) + \
                " data rows. Total processing time " + total_processing_time_str
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()

            self._log_job_progress()
            self.process_tasks_job.put()
        
            logging.info(fn_name + "COMPLETED: Imported " + str(self.import_job_state.num_of_imported_tasks) + 
                " tasks for " + self.user_email + " into " + str(self.import_job_state.num_tasklists) + 
                " tasklists from " +  str(self.import_job_state.data_row_num) + " data rows. Total processing time " + total_processing_time_str)
            if self.is_test_user:
                try:
                    logging.debug(fn_name + "TEST: Filename = " + self.blob_info.filename)
                except Exception, e:
                    logging.warning(fn_name + "TEST: Unable to log filename: " + shared.get_exception_msg(e))
            logservice.flush()
            
            # We've imported all the data, so now delete the Blobstore
            import_tasks_shared.delete_blobstore(self.blob_info)
            
            try:
                usage_stats = model.UsageStats(
                    user_hash = hash(self.user_email),
                    number_of_tasks = self.import_job_state.num_of_imported_tasks,
                    number_of_tasklists = self.import_job_state.num_tasklists,
                    job_start_timestamp = self.process_tasks_job.job_start_timestamp,
                    total_processing_seconds = self.process_tasks_job.total_processing_seconds,
                    file_type = self.process_tasks_job.file_type)
                usage_stats.put()
                logging.debug(fn_name + "Saved stats")
                logservice.flush()
            except Exception, e:
                logging.exception(fn_name + "Unable to save stats")
                logservice.flush()
            
            try:
                # sender = "import@" + get_application_id() + ".appspotmail.com"
                sender = host_settings.APP_TITLE + " <noreply@" + get_application_id() + ".appspotmail.com>"
                #dbg_to = "Julie.Smith.1999@gmail.com"
                
                # Using revision as words so that Gmail doesn't put all messages from all the different
                # versions in one conversation
                # dbg_msg = get_application_id() + u" " + revision_as_words() + u", '" + \
                    # tasklist_name + self.process_tasks_job.import_tasklist_suffix + \
                    # u"', DataRow " + str(self.import_job_state.data_row_num) + \
                    # u", NumTasks " + str(self.import_job_state.num_tasks_in_list) + \
                    # u" from '" + self.process_tasks_job.file_name + "'"
                    
                subject = (host_settings.APP_TITLE + u" - Import complete" +
                    u" from '" + self.process_tasks_job.file_name + "'")
                    
                # dbg_msg = ("Imported " + str(self.import_job_state.num_of_imported_tasks) + 
                    # " tasks into " + str(self.import_job_state.num_tasklists) + 
                    # " tasklists from " + str(self.import_job_state.data_row_num) + 
                    # " data rows. Total processing time " + total_processing_time_str)
                msg = ("Imported " + str(self.import_job_state.num_of_imported_tasks) + 
                    " tasks into " + str(self.import_job_state.num_tasklists) + 
                    " tasklists from " + str(self.import_job_state.data_row_num) + 
                    " data rows from " + unicode(self.process_tasks_job.file_name) +
                    "\nTotal processing time " + total_processing_time_str)
                
                
                mail.send_mail(sender=sender,
                    to=self.user_email,
                    subject=subject,
                    body=msg)
                
                logging.debug(fn_name + "Sent 'import complete' email")
                logservice.flush()
                
            except Exception, e:
                logging.exception(fn_name + "Unable to send 'import complete' email")
                logservice.flush()
            
        except DailyLimitExceededError, e:
            raise e
            
        except Exception, e:
            logging.exception(fn_name + "Caught outer Exception:") 
            logservice.flush()
            self._report_error("System Error: " + shared.get_exception_msg(e))
            
        logging.debug(fn_name + "<End>")
        logservice.flush()

        
    def _insert_missing_task(self, prev_tasks_data):
        """ Insert task into specified tasklist.
        
            args:
                prev_tasks_data     Dictionary containing data about the missing task
                    'task_row_data'     Dict containing data used to create the task (min: 'title' & 'status')
                    'tasklist_id'       ID of the tasklist to insert task into
                    'parent_id'         ID of the direct parent of the missing task. Empty string if this is a root task.
                    'sibling_id'        ID Of the previous task at the same depth. Empty string if this is the first task at this depth.
                    'id'                Original ID of the missing task
                
            This procedure will retry settings.NUM_API_TRIES time on error.
            
            There have been occassional instance where Google has returned an ID for an inserted tsks, but later attempts to use
            that ID result in "Not Found". To handle that, if the previous task does not exist, this method can be used to recreate
            the missing task.
                
        """
        
        fn_name = "_insert_missing_task(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        # Data row number is one less than the current row number, because we are trying to recreate the
        # current row's missing parent/sibling
        data_row_num = self.import_job_state.data_row_num - 1
        
        task_row_data = prev_tasks_data.get('task_row_data')
        tasklist_id = prev_tasks_data.get('tasklist_id','')
        parent_id = prev_tasks_data.get('parent_id','')
        sibling_id = prev_tasks_data.get('sibling_id','')
        old_id = prev_tasks_data.get('id','')
        
        # Retry, to handle occasional API timeout
        retry_count = settings.NUM_API_TRIES
        while retry_count > 0:
            retry_count = retry_count - 1
            self._update_progress()  # Update progress so that job doesn't stall

            try:
            
                # ==================================
                #       Recreate missing task
                # ==================================
                result = self.tasks_svc.insert(tasklist=tasklist_id, 
                                               body=task_row_data, 
                                               parent=parent_id, 
                                               previous=sibling_id).execute()
                
                new_task_id = result.get('id','')
                
                if not new_task_id:
                    self._report_error("Error creating missing task for data row " + str(data_row_num))
                    logging.error(fn_name + "<End> (No id returned - missing task was not created)")
                    logservice.flush()
                    return None
                    
                logging.debug(fn_name + "Recreated task. New task ID = " + new_task_id)
                logservice.flush()

                replaced_old_id = False
                # Replace the missing task ID in the parent and/or sibling collections,
                # with the new ID of the recreated task.
                
                i = 0
                while i < len(self.import_job_state.parents_ids):
                    if self.import_job_state.parents_ids[i] == old_id:
                        self.import_job_state.parents_ids[i] = new_task_id
                        logging.debug(fn_name + "Replaced missing parent " + old_id +
                            " with " + new_task_id + " at index " + str(i))
                        logservice.flush()
                        replaced_old_id = True
                        break
                    i = i + 1
                else:
                    logging.debug(fn_name + "Old task ID not found in previous parent IDs")
                    logservice.flush()
                    
                i = 0
                while i < len(self.import_job_state.sibling_ids):
                    if self.import_job_state.sibling_ids[i] == old_id:
                        self.import_job_state.sibling_ids[i] = new_task_id
                        logging.debug(fn_name + "Replaced missing sibling " + old_id +
                            " with " + new_task_id + " at index " + str(i))
                        replaced_old_id = True
                        break
                    i = i + 1
                else:
                    logging.debug(fn_name + "Old task ID not found in previous sibling IDs")
                    logservice.flush()

                if replaced_old_id:
                    # Success, so return new task ID for the recreated tasks
                    logging.debug(fn_name + "<End> (Success)")
                    logservice.flush()
                    return new_task_id
                else:
                    logging.error(fn_name + 
                        "Old task ID not found in previous sibling or parent IDs. We now have an orphaned task with ID " +
                        new_task_id)
                    logservice.flush()
                    self._report_error("Error updating details for task at data row " + str(data_row_num))
                    logging.error(fn_name + "<End> (Old task ID not found)")
                    logservice.flush()
                    return None
            
            except apiclient_errors.HttpError, e:
                self._handle_http_error(fn_name, e, retry_count, "Error recreating missing task for data row " + 
                    str(data_row_num))
                
            except Exception, e:
                self._handle_general_error(fn_name, e, retry_count, "Error recreating missing task for data row " + 
                    str(data_row_num))

                    
        # We should never get here, because _handle_general_error() and _handle_http_error() are both set to raise
        # exception once retry == 0
        
        # Still haven't been able to insert task, even after n retries, so nothing to return
        logging.warning(fn_name + "<End> (unable to insert task, even after n retries)")
        logservice.flush()
        return None
    
    
    def _get_tasklists(self):
        """ Get a list of all the user's tasklists """
        fn_name = "_get_tasklists(): "
        
        # logging.debug(fn_name + "<Start>")
        # logservice.flush()
        
        self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
        self._log_job_progress()
        self.process_tasks_job.put()
        
        # This list will contain zero or more tasklist dictionaries
        tasklists = [] 
        
        total_num_tasklists = 0
        
        # ----------------------------------------------------
        #       Retrieve all the tasklists for the user
        # ----------------------------------------------------
        # logging.debug(fn_name + "Retrieve all the tasklists for the user")
        # logservice.flush()
        
        next_tasklists_page_token = None
        more_tasklists_data_to_retrieve = True
        while more_tasklists_data_to_retrieve:
            retry_count = settings.NUM_API_TRIES
            while retry_count > 0:
                retry_count = retry_count - 1
                # ------------------------------------------------------
                #       Update progress so that job doesn't stall
                # ------------------------------------------------------
                self._update_progress("Retrieved " + str(total_num_tasklists) + " tasklists")
                
                try:
                    if next_tasklists_page_token:
                        tasklists_data = self.tasklists_svc.list(pageToken=next_tasklists_page_token).execute()
                    else:
                        tasklists_data = self.tasklists_svc.list().execute()
                    # Successfully retrieved data, so break out of retry loop
                    break
                
                except apiclient_errors.HttpError, e:
                    self._handle_http_error(fn_name, e, retry_count, "Error retrieving list of tasklists")
                    
                except Exception, e:
                    self._handle_general_error(fn_name, e, retry_count, "Error retrieving list of tasklists")
        
            if self.is_test_user and settings.DUMP_DATA:
                logging.debug(fn_name + "TEST: tasklists_data ==>")
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
                # logging.debug(fn_name + "TEST: tasklists_list ==>")
                # logging.debug(tasklists_list)


            # ---------------------------------------
            # Process all the tasklists for this user
            # ---------------------------------------
            for tasklist_data in tasklists_list:
                total_num_tasklists = total_num_tasklists + 1
              
                if self.is_test_user and settings.DUMP_DATA:
                    logging.debug(fn_name + "TEST: tasklist_data ==>")
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
                    # logging.debug(fn_name + "TEST: Adding %d tasks to tasklist" % len(tasklist_dict[u'tasks']))
                    
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
                    # logging.debug(fn_name + "TEST: There is (at least) one more page of tasklists to be retrieved")
            else:
                # This is the last (or only) page of results (list of tasklists)
                more_tasklists_data_to_retrieve = False
                next_tasklists_page_token = None
              
        # *** end while more_tasks_data_to_retrieve ***
        
        self._update_progress("Retrieved " + str(total_num_tasklists) + " tasklists")
        logging.debug(fn_name + "Retrieved list of " + str(total_num_tasklists) + " tasklists")
        # logging.debug(fn_name + "<End>")
        logservice.flush()
        return tasklists

        
    def _get_default_tasklist(self):
        """Retrieve the default tasklist"""
        
        fn_name = "_get_default_tasklist: "
        
        retry_count = settings.NUM_API_TRIES
        while retry_count > 0:
            # Find ID of default tasklist
            self._update_progress() # Update progress so that job doesn't stall
            retry_count = retry_count - 1
            try:
                default_tasklist = self.tasklists_svc.get(tasklist='@default').execute()
                return default_tasklist # Success

            except apiclient_errors.HttpError, e:
                self._handle_http_error(fn_name, e, retry_count, "Error retrieving default tasklist ID")
                
            except Exception, e:
                self._handle_general_error(fn_name, e, retry_count, "Error retrieving default tasklist ID")
        
        return None
  
  
    def _delete_tasklist_by_id(self, tasklist_id, tasklist_name = ''):
        """ Delete specified tasklist.
        
            If tasklist_id is the default tasklist, rename it (because default list cannot be deleted).
            
            The tasklist_name parameter is only passed in to make logging easier.
        """
        fn_name = "_delete_tasklist_by_id(): "
        
        retry_count = settings.NUM_API_TRIES
        while retry_count > 0:
            retry_count = retry_count - 1
            self._update_progress()  # Update progress so that job doesn't stall
            try:
                if tasklist_id == self.import_job_state.default_tasklist_id:
                    # ------------------------------------
                    #       Rename default tasklist
                    # ------------------------------------
                    action_str = "renaming default"
                    # Google does not allow default tasklist to be deleted, so just change the title
                    # Use Unix timestamp to create a unique title for the undeletable default tasklist
                    default_tasklist = self._get_default_tasklist()
                    self.import_job_state.default_tasklist_orig_name = default_tasklist['title'] # Save orig name
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: Stored original tasklist name [" + 
                            self.import_job_state.default_tasklist_orig_name + "]")
                        logservice.flush()
                    default_tasklist['title'] = 'Undeletable default ' + \
                        str(int(time.mktime(datetime.datetime.now().timetuple())))
                    result = self.tasklists_svc.update(tasklist=self.import_job_state.default_tasklist_id, 
                        body=default_tasklist).execute()
                    self.import_job_state.default_tasklist_was_renamed = True
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: Renamed default tasklist from [" + 
                            self.import_job_state.default_tasklist_orig_name + "] to [" +
                            default_tasklist['title'] + "]")
                    else:
                        logging.debug(fn_name + "Renamed default tasklist")
                    logservice.flush()
                    return False
                else:
                    # ----------------------------
                    #       Delete tasklist
                    # ----------------------------
                    action_str = "deleting"
                    self.tasklists_svc.delete(tasklist=tasklist_id).execute()
                    if self.is_test_user:
                        logging.debug(fn_name + "TEST: Deleted tasklist, id = " + str(tasklist_id))
                    logservice.flush()
                    return True
                break # Success
                
            except apiclient_errors.HttpError, e:
                self._handle_http_error(fn_name, e, retry_count, "Error " + action_str + " tasklist, id = " + 
                    str(tasklist_id))
                
            except Exception, e:
                self._handle_general_error(fn_name, e, retry_count, "Error " + action_str + " tasklist, id = " + 
                    str(tasklist_id))
        
        # We should never get here, because _handle_general_error() and _handle_http_error() are both set to raise
        # exception once retry == 0
        
        logging.error(fn_name + "Failed to delete tasklist after " + str(settings.NUM_API_TRIES) + " attempts")
        logservice.flush()
        return False
        
        
    def _delete_tasklists(self, tasklists):
        """ Delete all existing tasklists. """
        
        fn_name = "_delete_tasklists: "
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        
        # ------------------------------------------------------
        #       Update progress so that job doesn't stall
        # ------------------------------------------------------
        self._update_progress("Deleting existing tasklists", force=True)

        
        num_deleted_tasklists = 0
        num_tasklists = len(tasklists)
        for tasklist in tasklists:
            tasklist_name = tasklist['title']
            tasklist_id = tasklist['id']
            
            if self._delete_tasklist_by_id(tasklist_id, tasklist_name):
                # Only count number of deleted tasklists (default is renamed, not deleted)
                num_deleted_tasklists += 1

            # ------------------------------------------------------
            #       Update progress so that job doesn't stall
            # ------------------------------------------------------
            force_update = (num_deleted_tasklists == 1)
            self._update_progress("Deleted " + str(num_deleted_tasklists) + " of " +
                str(num_tasklists) + " tasklists ...", force_update)
        
        self._update_progress("Deleted " + str(num_deleted_tasklists) + " tasklists", force=True)
        logging.debug(fn_name + "Deleted " + str(num_deleted_tasklists) + " of " + str(num_tasklists)  + 
            " tasklists; default tasklist was renamed.")
        logging.debug(fn_name + "<End>")
        logservice.flush()            
        
    
    def _report_error(self, err_msg, log_as_invalid_data=False):
        """ Log error message, and update Job record to advise user of error """
        
        fn_name = "_report_error(): "
        
        if log_as_invalid_data:
            err_msg = constants.INVALID_FORMAT_LOG_LABEL + err_msg
            logging.info(fn_name + err_msg)
        else:
            logging.warning(fn_name + "Error: " + err_msg)
        logservice.flush()
        
        self.process_tasks_job.status = constants.ImportJobStatus.ERROR
        self.process_tasks_job.message = ''

        if self.process_tasks_job.error_message:
            logging.warning(fn_name + "Existing error: " + self.process_tasks_job.error_message)
            logservice.flush()
            # Subsequent error message(s), usually technical detail
            if self.process_tasks_job.error_message_extra:
                # 3rd and subsequent error messages, so append to existing 2nd error message
                self.process_tasks_job.error_message_extra = self.process_tasks_job.error_message_extra + "; " + err_msg
            else:
                # 2nd error message
                self.process_tasks_job.error_message_extra = err_msg
            
        else:
            # 1st error message
            self.process_tasks_job.error_message = err_msg
            
        self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
        
        # import_job_state may be None, depending on when the error occurs, so only access the variable if it has a value
        if self.import_job_state:
            # Current data row was NOT processed, so change data_row_num so it now refers to last successfully imported row
            # data_row_num is incremented at the start of the task processing loop
            self.process_tasks_job.data_row_num = self.import_job_state.data_row_num - 1
            self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
        _log_job_state(self.import_job_state) # Log job state info
        self.process_tasks_job.pickled_import_state = pickle.dumps(self.import_job_state)
        self._log_job_progress()
        self.process_tasks_job.put()
        # Import process terminated, so delete the blobstore
        import_tasks_shared.delete_blobstore(self.blob_info)


    def _handle_http_error(self, fn_name, e, retry_count, err_msg):
        self._update_progress(force=True)
        # TODO: Find a reliable way to detect daily limit exceeded that doesn't rely on text
        if e._get_reason().lower() == "daily limit exceeded":
            logging.warning(fn_name + "HttpError: " + err_msg + ": " + shared.get_exception_msg(e))
            logservice.flush()
            raise DailyLimitExceededError()
            
        if retry_count == settings.NUM_API_TRIES-1 and e.resp.status == 503:
            # Log first 503 as an Info level, because 
            #   (a) There are a frequent 503 errors
            #   (b) Almost all 503 errors recover after a single retry
            logging.info(fn_name + "HttpError: " + err_msg + ": " + shared.get_exception_msg(e) + 
                "\nFirst attempt, so logged as info. " + str(retry_count) + " attempts remaining")
            logservice.flush()
        else:
            if retry_count > 0:
                logging.warning(fn_name + "HttpError: " + err_msg + ": " + shared.get_exception_msg(e) + "\n" +
                    str(retry_count) + " attempts remaining")
                logservice.flush()
            else:
                logging.exception(fn_name + "HttpError: " + err_msg + ": " + shared.get_exception_msg(e) + "\n" +
                    "Giving up after " +  str(settings.NUM_API_TRIES) + " attempts")
                logservice.flush()
                self._report_error(err_msg)
                raise e

        # Last chances - sleep to give the server some extra time before re-requesting
        if retry_count <= 2:
            logging.debug(fn_name + "Giving server an extra chance; Sleeping for " + 
                str(settings.API_RETRY_SLEEP_DURATION) + 
                " seconds before retrying")
            logservice.flush()
            time.sleep(settings.API_RETRY_SLEEP_DURATION)
            

    def _handle_general_error(self, fn_name, e, retry_count, err_msg):
        self._update_progress(force=True)
        if retry_count > 0:
            if isinstance(e, AccessTokenRefreshError):
                # Log first 'n' AccessTokenRefreshError as Info, because they are reasonably common,
                # and the system usually continues normally after the 2nd instance of
                # "new_request: Refreshing due to a 401"
                # Occassionally, the system seems to need a 3rd attempt 
                # (i.e., success after waiting 45 seconds)
                logging.info(fn_name + 
                    "Access Token Refresh Error: " + err_msg + " (not yet an error). " + 
                    str(retry_count) + " attempts remaining: " + shared.get_exception_msg(e))
            else:
                logging.warning(fn_name + "Error: " + err_msg + ": " + shared.get_exception_msg(e) + "\n" +
                    str(retry_count) + " attempts remaining")
            logservice.flush()
        else:
            logging.exception(fn_name + "Error: " + err_msg + ": " + shared.get_exception_msg(e) + "\n" +
                "Giving up after " + str(settings.NUM_API_TRIES) + " attempts")
            logservice.flush()
            self._report_error(err_msg)
            raise e

        # Last chances - sleep to give the server some extra time before re-requesting
        if retry_count <= 2:
            logging.debug(fn_name + "Giving server an extra chance; Sleeping for " + 
                str(settings.API_RETRY_SLEEP_DURATION) + 
                " seconds before retrying")
            logservice.flush()
            time.sleep(settings.API_RETRY_SLEEP_DURATION)
            
            
    def _update_progress(self, msg=None, force=False):
        """ Update progress so that job doesn't stall """
        
        if force or (datetime.datetime.now() - self.prev_progress_timestamp).seconds > settings.PROGRESS_UPDATE_INTERVAL:
            if msg:
                self.process_tasks_job.message = msg
            self.process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            if self.import_job_state:
                self.process_tasks_job.total_progress = self.import_job_state.num_of_imported_tasks
                self.process_tasks_job.data_row_num = self.import_job_state.data_row_num
            self._log_job_progress()
            self.process_tasks_job.put()
            self.prev_progress_timestamp = datetime.datetime.now()
        
        
    def _log_job_progress(self):
        """ Write a debug message showing current progress """
        if self.process_tasks_job:
            msg1 = "Job status: '" + self.process_tasks_job.status + \
                "', data row " + str(self.process_tasks_job.data_row_num) + \
                ", progress: " + str(self.process_tasks_job.total_progress)
            msg2 = ", msg: '" + self.process_tasks_job.message + "'" if self.process_tasks_job.message else ''
            msg3 = ", err msg: '" + self.process_tasks_job.error_message + "'" if self.process_tasks_job.error_message else ''
            msg4 = " (Paused: " + self.process_tasks_job.pause_reason + ")" if self.process_tasks_job.is_paused else ''
            logging.debug(msg1 + msg2 + msg3 + msg4)
        else:
            logging.debug("No job record")
        logservice.flush()
    


     
app = webapp2.WSGIApplication(
    [
        (settings.WORKER_URL, ProcessTasksWorker),
    ], debug=True)
