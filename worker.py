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
import shared # Code whis is common between tasks-backup.py and worker.py


# Orig __author__ = "dwightguth@google.com (Dwight Guth)"
__author__ = "julie.smith.1999@gmail.com (Julie Smith)"



class ProcessTasksWorker(webapp.RequestHandler):
    """ Process tasks according to data in the ProcessTasksJob entity """
    
    def post(self):
        fn_name = "ProcessTasksWorker.post(): "
        
        logging.debug(fn_name + "<start> (app version %s)" %appversion.version)
        logservice.flush()

        client_id, client_secret, user_agent, app_title, project_name, host_msg = shared.get_settings(self.request.host)
        
        
        user_email = self.request.get(settings.TASKS_QUEUE_KEY_NAME)
        
        is_test_user = shared.isTestUser(user_email)
        
        
        if user_email:
            
            # Retrieve the DB record for this user
            process_tasks_job = model.ProcessTasksJob.get_by_key_name(user_email)
            
            if process_tasks_job is None:
                logging.error(fn_name + "No DB record for " + user_email)
                logservice.flush()
                # TODO: Find some way of notifying the user?????
            else:
                logging.info(fn_name + "Retrieved process tasks job for " + str(user_email))
                logservice.flush()
                
                user = process_tasks_job.user
                if not user:
                    logging.error(fn_name + "No user object in DB record for " + str(user_email))
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = "Problem with user details. Please restart."
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    self.response.set_status(401, "No user object")
                    return
                      
                credentials = process_tasks_job.credentials
                if not credentials:
                    logging.error(fn_name + "No credentials in DB record for " + str(user_email))
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = "Problem with user credentials. Please restart."
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    self.response.set_status(401, "No credentials")
                    return
              
                if credentials.invalid:
                    logging.error(fn_name + "Invalid credentials in DB record for " + str(user_email))
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = "Invalid credentials. Please restart and re-authenticate."
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    self.response.set_status(401, "Invalid credentials")
                    return
              
                if is_test_user:
                    logging.debug(fn_name + "User is test user %s" % user_email)
                    logservice.flush()
                    
                if process_tasks_job.job_type == 'import':
                    self.import_tasks(credentials, user_email, is_test_user, process_tasks_job)
                else:
                    self.export_tasks(credentials, user_email, is_test_user, process_tasks_job)
                # logging.info(fn_name + "Finished processing. Total progress = " + 
                    # str(process_tasks_job.total_progress) + " for " + str(user_email))
        else:
            logging.error(fn_name + "No processing, as there was no user_email key")
            logservice.flush()
            
        logging.debug(fn_name + "<End>, user = " + str(user_email))
        logservice.flush()

        
    def import_tasks(self, credentials, user_email, is_test_user, process_tasks_job):
        """ Read data from supplied CSV file, and create a task for each row.
        
            The process_tasks_job entity contains the key to the Blobstore which holds a CSV file containing tasks to be imported.
            
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
        
        fn_name = "import_tasks: "
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        prev_progress_timestamp = datetime.datetime.now()
        start_time = datetime.datetime.now()
        
        try:
            # tasklist_suffix = "yyyy-mm-d hh:mm:ss"
            # Store in tasklist_title_dict as {'title' : 'id'}
            # For each row in CSV file:
            #   If tasklist_title in tasklist_title_dict: 
            #       tasklist_id = ID from tasklist_title_dict
            #   else: 
            #       Create tasklist (title = tasklist_title + tasklist_suffix
            #       tasklist_id = ID of new tasklist
            #       Add tasklist_title {excluding suffix) & tasklist_id to tasklist_title_dict
            #   Insert task into tasklist using;
            #       tasklist_id
            #       previous_id (id of previous sibling at same depth, else '')
            #           "From https://developers.google.com/google-apps/tasks/v1/reference/tasks/insert
            #               OPTIONAL: Previous sibling task identifier. 
            #               If the task is created at the first position among its siblings, this parameter is omitted."
            #       parent_id (if depth > 0, else '')
            #   prev_tasklist = tasklist
            # Retrieve list of tasklists
            #   % Handling non-unique tasklist names:
            #   %   Rename non-unique tasklist on server? {Check that rename doesn't clash}
            #   %   Only store ID of 1st tasklist
            # Store in tasklist_title_dict as {'title' : 'id'}
            # For each row in CSV file:
            #   If tasklist_title != prev_tasklist_title
            #       If tasklist_title in tasklist_title_dict: 
            #           tasklist_id = ID from tasklist_title_dict
            #       else: 
            #           Create tasklist (title = tasklist_title + suffix)
            #           tasklist_id = ID of new tasklist
            #           Add tasklist_title {excluding suffix) & tasklist_id to tasklist_title_dict
            #       prev_tasklist_title = tasklist
            #   Insert task into tasklist using;
            #       tasklist_id
            #       previous_id (id of previous sibling at same depth)
            #       parent_id (if depth > 0)
            #   
            #   previous_id = task_id
            
            # Option: Ignore duplicates
            #   Read all tasks in tasklist
            #   Sort existing tasks (in memory) by title O(n log n)
            #   Sort new tasks (in memory) by title O(n log n)
            #   Compare side-by-side O(n)
            # Problem: Requires a lot of memory - size of existing PLUS size of new PLUS sorting overhead
            
            # OPTIONS:
            #   Allow user to specify new tasklist, into which ALL tasks are inserted
            #   Allow user to specify prefix/suffix for import tasklist names, and import tasks into new tasklists
            #     Need to check for name clashes. 
            # Could just append YYYY-MM-DD_HH-MM-SS for each import job
            #       (similar to the auto-added group when inserting contacts into Google
            
                
            process_tasks_job.status = 'importing'
            process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            process_tasks_job.put()

            logging.debug(fn_name + "Retrieving data from Blobstore")
            logservice.flush()
            blob_key = process_tasks_job.blobstore_key
            blobdata = blobstore.BlobReader(blob_key)
            blob_info = blobstore.BlobInfo.get(blob_key)
            file_name = str(blob_info.filename)
            logging.debug(fn_name + "Filename = " + file_name + ", key = " + blob_key)
            logservice.flush()
            f=csv.DictReader(blobdata,dialect='excel')
            
            http = httplib2.Http()
            http = credentials.authorize(http)
            service = discovery.build("tasks", "v1", http)
            
            # Set suffix once, so that all tasklists created in this session have the same suffix (timestamp)
            tasklist_suffix = datetime.datetime.now().strftime(" %Y-%m-%d %H:%M:%S")
            
            prev_tasklists = {} # "Tasklist name" : "Tasklist ID"
            prev_tasklist_name = None
            parents_ids = [''] # Level 0 tasks don't have a parent, so level zero parent ID is always an empty string
            sibling_ids = []
            prev_depth = 0
            sibling_id = ''
            tasklist_id = ''

            task_num = 0
            
            for task in f:
                task_num = task_num + 1
                
                if len(task) != 9 or task.get('restkey'):
                    # Invalid number of columns
                    msg = "Task " + str(task_num) + " has " + str(len(task)) + " columns, expected 9"
                    logging.warning(fn_name + msg)
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = msg
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    logging.debug(fn_name + "<End> due to invalid number of columns")
                    logservice.flush()
                    return 

                # ---------------------------------------------------
                #            Process tasklist for this task
                # ---------------------------------------------------
                if task.has_key('tasklist_name'):
                    tasklist_name = task['tasklist_name']
                    if not tasklist_name:
                        tasklist_name = "<Unnamed list>"
                        logging.debug(fn_name + "No tasklist name for imported task number " + str(task_num) + ", using " + tasklist_name)
                        logservice.flush()
                    # 'tasklist_name' is not part of the Tasks resource, so delete it from the dictionary
                    del(task['tasklist_name'])
                else:
                    msg = "Missing required 'tasklist_name' column in task number " + str(task_num)
                    logging.warning(fn_name + msg)
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = msg
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    logging.debug(fn_name + "<End> due to missing 'tasklist_name' column")
                    logservice.flush()
                    return 
                
                if tasklist_name != prev_tasklist_name:
                    existing_tasklist_id = prev_tasklists.get(tasklist_name)
                    if existing_tasklist_id:
                        # The tasklist name for this task was used in a previous task (but not immediately previous task)
                        # This indicates that tasks are out of order, OR user has duplicate tasklist names
                        # OPTION1: Add task to existing tasklist
                        # OPTION2: Create new tasklist
                        #   - duplicate name OR
                        #   - add another suffix; would need to check that suffix doesn' clash if user has > 2 taskklists with same name
                        msg = "The tasklist name [" + str(tasklist_name) + "] for task " + str(task_num) + " has already been used. It appears that tasks are not in order, or the original account had duplicate tasklist names"
                        logging.warning(fn_name + msg)
                        logservice.flush()
                        process_tasks_job.status = 'error'
                        process_tasks_job.error_message = msg
                        process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                        process_tasks_job.put()
                        logging.debug(fn_name + "<End> due to invalid task order")
                        logservice.flush()
                        return 
                        # OPTION1: Add task to existing tasklist
                        #tasklist_id = existing_tasklist_id
                    else:
                        # ----------------------------------
                        #       Create new tasklist
                        # ----------------------------------
                        tasklist = { 'title': tasklist_name + tasklist_suffix }
                        result = service.tasklists().insert(body=tasklist).execute()
                        tasklist_id = result['id']
                        logging.debug(fn_name + "Created new Tasklist [" + (tasklist_name + tasklist_suffix) + "], ID = " + tasklist_id)
                        logservice.flush()
                        prev_tasklist_name = None
                        parents_ids = [''] # New list, so start with no parents
                        sibling_ids = [] # New list, so start with no siblings
                        prev_depth = 0
                        prev_tasklist_name = tasklist_name
                
                
                # ---------------------------------------------------
                #               Adjust date/time formats
                # ---------------------------------------------------
                # Calculate due date
                date_due_str = task.get(u'due')
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
                    task[u'due'] = new_due_date.strftime("%Y-%m-%dT00:00:00.000Z")
                
                
                # Calculate completed date
                datetime_completed_str = task.get(u'completed')
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
                    task[u'completed'] = new_datetime_completed.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                
                # -----------------------------------------------
                #        Check depth value for this task
                # -----------------------------------------------
                err_msg = None
                if task.has_key('depth'):
                    try:
                        depth = int(task['depth'])
                    except Exception, e:
                        err_msg = "Invalid depth value [" + str(task['depth']) + "] for task " + str(task_num) + ", Exception: " + str(e)
                    # 'depth' is not part of the Tasks resource, so delete it from the dictionary
                    del(task['depth'])
                else:
                    err_msg = "No 'depth' property for imported task number " + str(task_num)
                if err_msg:
                    logging.warning(fn_name + err_msg)
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = err_msg
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    logging.debug(fn_name + "<End> due to missing/invalid depth column")
                    logservice.flush()
                    return 
                if depth < 0:
                    logging.debug(fn_name + "Depth of task " + str(task_num) + " [" + str(depth) + "] is less than zero. Setting depth = 0") 
                    depth = 0
                
                # -----------------------------------------------------------
                #       Check depth and find current task's parent ID
                # -----------------------------------------------------------
                if task_num == 0 and depth != 0:
                    # First task imported must have depth of zero; it must be a root task
                    err_msg = "Invalid depth [" + str(depth) + "] of first task; First task must have depth = 0"
                    logging.warning(fn_name + err_msg)
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = err_msg
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    logging.debug(fn_name + "<End> due to invalid depth value")
                    logservice.flush()
                    return 
                    
                # Check for valid depth value
                # Valid depth values:
                #   depth = 0                   Root task
                #   depth < prev_depth          Task moved back up the task tree
                #   depth == prev_depth         Sibling task (same parent as previous task)
                #   depth == prev_depth + 1     New child task
                # Task depth must not be more than prev_depth + 1
                # List of parents_ids is updated after task has been added, because the current task may be the parent of the next task
                if depth > prev_depth+1:
                    # Child can only be immediate child of previous task (i.e., previous depth + 1)
                    err_msg = "Depth [" + str(depth) + "] of task " + str(task_num) + " is more than 1 below greater than previous task's depth [" + str(prev_depth) + "]"
                    logging.warning(fn_name + err_msg)
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = err_msg
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    logging.debug(fn_name + "<End> due to invalid depth value")
                    logservice.flush()
                    return 
                    
                # Find parent task ID
                # Will be empty string for root tasks (depth = 0)
                try:
                    parent_id = parents_ids[depth]
                except:
                    logging.warning(fn_name + "Invalid depth [" + str(depth) + "] for task " + str(task_num) + "; Unable to determine parent. Previous task's depth was " + str(prev_depth) + " Exception: " + shared.get_exception_msg())
                    logservice.flush()
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = "Invalid depth [" + str(depth) + "] for task " + str(task_num)
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    logging.debug(fn_name + "<End> due to invalid depth value")
                    logservice.flush()
                    return 
                
                # # Find sibling (previous) ID
                # if depth != prev_depth:
                    # # Depth change indicates that this is the "first position among its siblings"
                    # #      "From https://developers.google.com/google-apps/tasks/v1/reference/tasks/insert
                    # #          OPTIONAL: Previous sibling task identifier. 
                    # #          If the task is created at the first position among its siblings, this parameter is omitted."
                    # sibling_id = ''
                    
                # # Find ID of most recent sibling at this depth
                # if len(sibling_ids) < depth+1:
                    # # First task at this depth
                    # sibling_id = ''
                # else:
                    # # Previous task at this depth
                    # sibling_id = sibling_ids[depth]
                    
                if depth == prev_depth+1:
                    # Going deeper, so this is the first child at the new depth, within this branch of the tree.
                    # There is nowhere else that this task can go, so use a blank sibling ID (otherwise insert() throws an error)
                    sibling_id = ''
                elif depth+1 > len(sibling_ids):
                    # First task at this depth
                    sibling_id = ''
                else:
                    # Previous task at this depth
                    sibling_id = sibling_ids[depth]
                
                # -------------------------------------------------------------------------------------
                #           Delete any empty properties, to prevent server throwing an error
                # -------------------------------------------------------------------------------------
                empty_keys = []
                for k,v in task.iteritems():
                    if len(v) == 0:
                        #logging.debug(fn_name + "'" + k + "' property is empty. Deleting")
                        empty_keys.append(k)
                        
                for k in empty_keys:
                    #logging.debug(fn_name + "Deleting empty '" + k + "' property")
                    del(task[k])
                
                # ================================================================
                #               Insert the task into the tasklist
                # ================================================================
                logging.debug(fn_name + "Inserting task " + str(task_num) + " with depth " + str(depth) + " ==>")
                logging.debug(task)
                
                # TODO: Put insert within a retry loop for timeouts
                try:
                    result = service.tasks().insert(tasklist=tasklist_id, body=task, parent=parent_id, previous=sibling_id).execute()
                except Exception, e:
                    logging.exception(fn_name + "Exception inserting task:")
                    logging.debug(fn_name + "Task num = " + str(task_num))
                    logging.debug(fn_name + "Depth = " + str(depth))
                    logging.debug(fn_name + "tasklist = " + str(tasklist_id))
                    logging.debug(fn_name + "parent = " + str(parent_id))
                    logging.debug(fn_name + "previous = " + str(sibling_id))
                    logging.debug(fn_name + "body = " + str(task))
                    logservice.flush()
                    
                    process_tasks_job.status = 'error'
                    process_tasks_job.error_message = "Error creating task number " + str(task_num) + ": " + shared.get_exception_msg(e)
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    logging.debug(fn_name + "<End> due to exception inserting tasks")
                    logservice.flush()
                    return
                
                task_id = result['id']
                logging.debug(fn_name + "Created new Task ID = " + task_id)
                logservice.flush()
                
                # --------------------------------------------
                #           Update list of parent IDs
                # --------------------------------------------
                # List of parents_ids is updated after task has been added, because the current task may be the parent of the next task
                if depth < prev_depth:
                    # Child of an earlier task, so we've moved back up the task tree
                    # Delete ID of 'deeper' tasks, because those tasks cannot be parents anymore
                    del(parents_ids[depth+1:])
                # Store ID of current task in at this depth, as it could be the parent of a future task
                if len(parents_ids) == depth+2:
                    parents_ids[depth+1] = task_id
                else:
                    parents_ids.append(task_id)
                
                # Store ID of this task (for this depth)
                if len(sibling_ids) < depth+1:
                    # First task at this depth
                    sibling_ids.append(task_id)
                else:
                    # There was a previous task at this depth
                    sibling_ids[depth] = task_id

                    
                # ---------------------------------------
                #       Provide feedback to user
                # ---------------------------------------
                if (datetime.datetime.now() - prev_progress_timestamp).seconds > settings.TASK_COUNT_UPDATE_INTERVAL:
                    # DEBUG
                    logging.info(fn_name + "Updating progress = " + str(task_num))
                    logservice.flush()
                    process_tasks_job.total_progress = task_num
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    prev_progress_timestamp = datetime.datetime.now()
                    
                prev_depth = depth
                
            # ---------------------------------------
            #       Mark import job complete
            # ---------------------------------------
            process_tasks_job.status = 'import_completed'
            process_tasks_job.total_progress = task_num
            process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            process_tasks_job.put()
            logging.info(fn_name + "Marked import job complete for " + str(user_email) + ", with progress = " + 
                str(process_tasks_job.total_progress))
            logservice.flush()
        
            # -------------------------------------
            #       Delete the Blobstore item
            # -------------------------------------
            try:
                # We've imported all the data, so now delete the Blobstore
                blob_info.delete()
                logging.debug(fn_name + "Blobstore deleted")
                logservice.flush()
            except Exception, e:
                logging.exception(fn_name + "Exception deleting " + file_name + ", key = " + blob_key)
                logservice.flush()
                process_tasks_job.status = 'error'
                process_tasks_job.error_message = "Exception: " + shared.get_exception_msg(e)
                process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                process_tasks_job.put()
                
        except Exception, e:
            logging.exception(fn_name + "Exception:") 
            logservice.flush()
            
            process_tasks_job.status = 'error'
            process_tasks_job.error_message = shared.get_exception_msg(e)
            process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            process_tasks_job.put()
        
            
        logging.debug(fn_name + "<End>")
        logservice.flush()
        
        
        
    def export_tasks(self, credentials, user_email, is_test_user, process_tasks_job):
        fn_name = "export_tasks: "
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        start_time = datetime.datetime.now()
        
        process_tasks_job.status = 'building'
        process_tasks_job.job_progress_timestamp = datetime.datetime.now()
        process_tasks_job.put()
        
        include_hidden = process_tasks_job.include_hidden
        include_completed = process_tasks_job.include_completed
        include_deleted = process_tasks_job.include_deleted
        
        
        # Retrieve all tasks for the user
        try:

            logging.info(fn_name + "include_hidden = " + str(include_hidden) +
                ", include_completed = " + str(include_completed) +
                ", include_deleted = " + str(include_deleted))
            logservice.flush()
            
            #logging.debug(fn_name + "setting up http")
            http = httplib2.Http()
            http = credentials.authorize(http)
            service = discovery.build("tasks", "v1", http)
            
            # Services to retrieve tasklists and tasks
            #logging.debug(fn_name + "Setting up services to retrieve tasklists and tasks")
            tasklists_svc = service.tasklists()
            tasks_svc = service.tasks() 
            
            
            
            # ##############################################
            # FLOW
            # ----------------------------------------------
            # For each page of taskslists
            #   For each tasklist
            #     For each page of tasks
            #       For each task
            #         Fix date format
            #       Add tasks to tasklist collection
            #     Add tasklist to tasklists collection
            # Use tasklists collection to return tasks backup to user
            
            # This list will contain zero or more tasklist dictionaries, which each contain tasks
            tasklists = [] 
            
            total_num_tasklists = 0
            total_num_tasks = 0
            tasks_per_list = []
            
            # ---------------------------------------
            # Retrieve all the tasklists for the user
            # ---------------------------------------
            logging.debug(fn_name + "Retrieve all the tasklists for the user")
            logservice.flush()
            next_tasklists_page_token = None
            more_tasklists_data_to_retrieve = True
            while more_tasklists_data_to_retrieve:
                if is_test_user:
                    logging.debug(fn_name + "calling tasklists.list().execute() to create tasklists list")
                    logservice.flush()
            
                retry_count = 3
                while retry_count > 0:
                  try:
                    if next_tasklists_page_token:
                       tasklists_data = tasklists_svc.list(pageToken=next_tasklists_page_token).execute()
                    else:
                       tasklists_data = tasklists_svc.list().execute()
                    # Successfully retrieved data, so break out of retry loop
                    break
                  except Exception, e:
                    retry_count = retry_count - 1
                    if retry_count > 0:
                        logging.warning(fn_name + "Error retrieving list of tasklists. " + 
                            str(retry_count) + " retries remaining")
                        logservice.flush()
                    else:
                        logging.exception(fn_name + "Still error retrieving list of tasklists after retries. Giving up")
                        logservice.flush()
                        raise e
            
                if is_test_user and settings.DUMP_DATA:
                    logging.debug(fn_name + "tasklists_data ==>")
                    logging.debug(tasklists_data)
                    logservice.flush()

                if tasklists_data.has_key(u'items'):
                  tasklists_list = tasklists_data[u'items']
                else:
                  # If there are no tasklists, then there will be no 'items' element. This could happen if
                  # the user has deleted all their tasklists. Not sure if this is even possible, but
                  # checking anyway, since it is possible to have a tasklist without 'items' (see issue #9)
                  logging.warning(fn_name + "User has no tasklists.")
                  logservice.flush()
                  tasklists_list = []
              
                # tasklists_list is a list containing the details of the user's tasklists. 
                # We are only interested in the title
              
                # if is_test_user and settings.DUMP_DATA:
                    # logging.debug(fn_name + "tasklists_list ==>")
                    # logging.debug(tasklists_list)


                # ---------------------------------------
                # Process all the tasklists for this user
                # ---------------------------------------
                for tasklist_data in tasklists_list:
                    total_num_tasklists = total_num_tasklists + 1
                  
                    if is_test_user and settings.DUMP_DATA:
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
                  
                    if is_test_user and settings.DUMP_DATA:
                        logging.debug(fn_name + "Process all the tasks in " + str(tasklist_title))
                        logservice.flush()
                            
                    # Process all the tasks in this task list
                    tasklist_dict, num_tasks = self.get_tasks_in_tasklist(tasks_svc, tasklist_title, tasklist_id, is_test_user,
                        process_tasks_job, include_hidden, include_completed, include_deleted)
                    # Track number of tasks per tasklist
                    tasks_per_list.append(num_tasks)
                    
                    total_num_tasks = total_num_tasks + num_tasks
                    process_tasks_job.total_progress = total_num_tasks
                    process_tasks_job.tasklist_progress = 0 # Because total_progress now includes num_tasks for current tasklist
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    
                    # if is_test_user:
                        # logging.debug(fn_name + "Adding %d tasks to tasklist" % len(tasklist_dict[u'tasks']))
                        
                    # Add the data for this tasklist (including all the tasks) into the collection of tasklists
                    tasklists.append(tasklist_dict)
              
                # Check if there is another page of tasklists to be retrieved
                if tasklists_data.has_key('nextPageToken'):
                    # There is another page of tasklists to be retrieved for this user, 
                    # which we'll retrieve next time around the while loop.
                    # This happens if there is more than 1 page of tasklists.
                    # It seems that each page contains 20 tasklists.
                    more_tasklists_data_to_retrieve = True # Go around while loop again
                    next_tasklists_page_token = tasklists_data['nextPageToken']
                    # if is_test_user:
                        # logging.debug(fn_name + "There is (at least) one more page of tasklists to be retrieved")
                else:
                    # This is the last (or only) page of results (list of tasklists)
                    more_tasklists_data_to_retrieve = False
                    next_tasklists_page_token = None
                  
            # *** end while more_tasks_data_to_retrieve ***
            
            # These values are also sent by email at the end of this method
            summary_msg = "Retrieved %d tasks from %d tasklists" % (total_num_tasks, total_num_tasklists)
            breakdown_msg = "Tasks per list: " + str(tasks_per_list)
            logging.info(fn_name + summary_msg + " - " + breakdown_msg)
            logservice.flush()
            
            # ------------------------------------------------------
            #   Store the data, so we can return it to the user
            # ------------------------------------------------------
              

            """
                Structure used in Django CSV templates
                    {% for tasklist in tasklists %}
                        {% for task in tasklist.tasks %}
                        
                Structure to pass to django
                {
                    "now": datetime.datetime.now(),  # Timestamp for the creation of this report/backup
                    "tasklists": [ tasklist ]        # List of tasklist items
                }

                structure of tasklist
                { 
                    "title" : tasklist.title,        # Name of this tasklist
                    "tasks"  : [ task ]              # List of task items in this tasklist
                }

                structure of task
                {
                    "title" : title, # Free text
                    "status" : status, # "completed" | "needsAction"
                    "id" : id, # Used when determining parent-child relationships
                    "parent" : parent, # OPT: ID of the parent of this task (only if this is a sub-task)
                    "notes" : notes, # OPT: Free text
                    "due" : due, # OPT: Date due, e.g. 2012-01-30T00:00:00.000Z NOTE time = 0
                    "updated" : updated, # Timestamp, e.g., 2012-01-26T07:47:18.000Z
                    "completed" : completed # Timestamp, e.g., 2012-01-27T10:38:56.000Z
                }

            """
            
            # Delete existing backup data records
            tasklist_data_records = model.TasklistsData.gql("WHERE ANCESTOR IS :1",
                                                        db.Key.from_path(settings.DB_KEY_TASKS_BACKUP_DATA, user_email))

            num_records = tasklist_data_records.count()
            logging.debug(fn_name + "Deleting " + str(num_records) + " old blobs")
            logservice.flush()
            
            for tasklists_data_record in tasklist_data_records:
                tasklists_data_record.delete()

            
            # logging.debug(fn_name + "Pickling tasks data ...")
            pickled_tasklists = pickle.dumps(tasklists)
            # logging.debug(fn_name + "Pickled data size = " + str(len(pickled_tasklists)))
            data_len = len(pickled_tasklists)
            
            # Multiply by 1.0 float value so that we can use ceiling to find number of Blobs required
            num_of_blobs = int(math.ceil(data_len * 1.0 / settings.MAX_BLOB_SIZE))
            logging.debug(fn_name + "Calculated " + str(num_of_blobs) + " blobs required to store " + str(data_len) + " bytes")
            logservice.flush()
            
            try:
                for i in range(num_of_blobs):
                    # Write backup data records
                    tasklist_rec = model.TasklistsData(db.Key.from_path(settings.DB_KEY_TASKS_BACKUP_DATA, user_email))
                    slice_start = int(i*settings.MAX_BLOB_SIZE)
                    slice_end = int((i+1)*settings.MAX_BLOB_SIZE)
                    # logging.debug(fn_name + "Creating part " + str(i+1) + " of " + str(num_of_blobs) + 
                        # " using slice " + str(slice_start) + " to " + str(slice_end))
                    
                    pkl_part = pickled_tasklists[slice_start : slice_end]
                    tasklist_rec.pickled_tasks_data = pkl_part
                    tasklist_rec.idx = i
                    tasklist_rec.put()
                    
                # logging.debug(fn_name + "Marking backup job complete")
                
                # Mark backup completed
                process_tasks_job.status = 'completed'
                process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                process_tasks_job.put()
                logging.info(fn_name + "Marked job complete for " + str(user_email) + ", with progress = " + 
                    str(process_tasks_job.total_progress))
                logservice.flush()
            except apiproxy_errors.RequestTooLargeError, e:
                logging.exception(fn_name + "Error putting results in DB")
                logservice.flush()
                process_tasks_job.status = 'error'
                process_tasks_job.error_message = "Tasklists data is too large - Unable to store tasklists in DB: " + shared.get_exception_msg(e)
                process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                process_tasks_job.put()
            
            except Exception, e:
                logging.exception(fn_name + "Error putting results in DB")
                logservice.flush()
                process_tasks_job.status = 'error'
                process_tasks_job.error_message = "Unable to store tasklists in DB: " + shared.get_exception_msg(e)
                process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                process_tasks_job.put()


              
              

        except urlfetch_errors.DeadlineExceededError, e:
            logging.exception(fn_name + "urlfetch_errors.DeadlineExceededError:")
            logservice.flush()
            process_tasks_job.status = 'error'
            process_tasks_job.error_message = "urlfetch_errors.DeadlineExceededError: " + shared.get_exception_msg(e)
            process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            process_tasks_job.put()
      
        except apiproxy_errors.DeadlineExceededError, e:
            logging.exception(fn_name + "apiproxy_errors.DeadlineExceededError:")
            logservice.flush()
            process_tasks_job.status = 'error'
            process_tasks_job.error_message = "apiproxy_errors.DeadlineExceededError: " + shared.get_exception_msg(e)
            process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            process_tasks_job.put()
        
        except DeadlineExceededError, e:
            logging.exception(fn_name + "DeadlineExceededError:")
            logservice.flush()
            process_tasks_job.status = 'error'
            process_tasks_job.error_message = "DeadlineExceededError: " + shared.get_exception_msg(e)
            process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            process_tasks_job.put()
        
        except Exception, e:
            logging.exception(fn_name + "Exception:") 
            logservice.flush()
            process_tasks_job.status = 'error'
            process_tasks_job.error_message = shared.get_exception_msg(e)
            process_tasks_job.job_progress_timestamp = datetime.datetime.now()
            process_tasks_job.put()
        
        end_time = datetime.datetime.now()
        process_time = end_time - start_time
        proc_time_str = "Processing time = " + str(process_time.seconds) + "." + str(process_time.microseconds) + " seconds"
        logging.info(fn_name + proc_time_str)
        logservice.flush()
        
        included_options_str = "Includes: Completed = %s, Deleted = %s, Hidden = %s" % (str(include_completed), str(include_deleted), str(include_hidden))
        try:
            # sender = "stats@" + os.environ['APPLICATION_ID'] + ".appspotmail.com"
            sender = "stats@" + get_application_id() + ".appspotmail.com"
            subject = "[" + get_application_id() + "] " + summary_msg
            #logging.info(fn_name + "Send stats email from " + sender)
            mail.send_mail(sender=sender,
                to="Julie.Smith.1999@gmail.com",
                subject=subject,
                body="Started: %s UTC\nFinished: %s UTC\n%s\n%s\n%s" % (str(start_time), str(end_time), proc_time_str, breakdown_msg, included_options_str ))
        except Exception, e:
            logging.exception(fn_name + "Unable to send email")

        logging.debug(fn_name + "<End>")
        logservice.flush()
            
    
    def get_tasks_in_tasklist(self, tasks_svc, tasklist_title, tasklist_id, is_test_user, process_tasks_job, 
                           include_hidden, include_completed, include_deleted):
        """ Returns all the tasks in the tasklist 
        
            arguments:
              tasks_svc                -- reference to the common service used to retrieve tasks ( e.g., service.tasks() )
              tasklist_title           -- Name of the tasklist
              tasklist_id              -- ID used to retrieve tasks from this tasklist
                                          MUST match the ID returned in the tasklist data
              is_test_user             -- True if the user is a test user, to enable more detailed logging
              process_tasks_job         -- DB entity for this backup job. Passed in so this method can updated timestamp and progress
              include_hidden           -- If true, include hidden tasks in the backup
              include_completed        -- If true, include completed tasks in the backup
              include_deleted          -- If true, include deleted tasks in the backup
              
            returns a tuple;
              two-element dictionary;
                'title' is a string, the name of the tasklist
                'tasks' is a list. Each element in the list is dictionary representing 1 task
              number of tasks
        """        
        fn_name = "CreateBackupHandler.get_tasks_in_tasklist(): "
        
        
        tasklist_dict = {} # Blank dictionary for this tasklist
        tasklist_dict[u'title'] = tasklist_title # Store the tasklist name in the dictionary
        
        num_tasks = 0

        more_tasks_data_to_retrieve = True
        next_tasks_page_token = None
        
        # Keep track of when last updated, to prevent excessive DB access which could exceed quota
        prev_progress_timestamp = datetime.datetime.now()
        
        if is_test_user and settings.DUMP_DATA:
          logging.debug(fn_name + "include_hidden = " + str(include_hidden) +
                            ", include_completed = " + str(include_completed) +
                            ", include_deleted = " + str(include_deleted))
          logservice.flush()
        # ---------------------------------------------------------------------------
        # Retrieve the tasks in this tasklist, and store as "tasks" in the dictionary
        # ---------------------------------------------------------------------------
        while more_tasks_data_to_retrieve:
        
          retry_count = 3
          while retry_count > 0:
            try:
              # Retrieve a page of (up to 100) tasks
              if next_tasks_page_token:
                # Get the next page of results
                # This happens if there are more than 100 tasks in the list
                # See http://code.google.com/apis/tasks/v1/using.html#api_params
                #     "Maximum allowable value: maxResults=100"
                tasks_data = tasks_svc.list(tasklist = tasklist_id, pageToken=next_tasks_page_token, 
                    showHidden=include_hidden, showCompleted=include_completed, showDeleted=include_deleted).execute()
              else:
                # Get the first (or only) page of results for this tasklist
                tasks_data = tasks_svc.list(tasklist = tasklist_id, 
                    showHidden=include_hidden, showCompleted=include_completed, showDeleted=include_deleted).execute()
              # Succeeded, so continue
              break
            except Exception, e:
              retry_count = retry_count - 1
              if retry_count > 0:
                logging.warning(fn_name + "Error retrieving tasks, " + 
                      str(retry_count) + " retries remaining")
                logservice.flush()
              else:
                logging.exception(fn_name + "Still error retrieving tasks for tasklist after retrying. Giving up")
                logservice.flush()
                raise e
              
          if is_test_user and settings.DUMP_DATA:
            logging.debug(fn_name + "tasks_data ==>")
            logging.debug(tasks_data)
          
          if not tasks_data.has_key(u'items'):
            # When using the Google Tasks webpage at https://mail.google.com/tasks/canvas, there will always
            # be at least one task in any tasklist, because when deleting the last task, a new blank task is
            # automatically created.
            # However, a third-party app (e.g., Calengoo on Android) CAN delete all the tasks in a task list,
            # which results in a tasklist without an 'items' element.
            logging.info(fn_name + "No tasks in tasklist")
            logservice.flush()
          else:
            try:
              tasks = tasks_data[u'items'] # Store all the tasks (List of Dict)
            except Exception, e:
              logging.exception(fn_name, "Exception extracting items from tasks_data. Dump follows ==>")
              logging.error(tasks_data)
              logservice.flush()
              raise e
            
            # if is_test_user and settings.DUMP_DATA:
              # logging.debug(fn_name + "tasks ==>")
              # logging.debug(tasks)
              # logservice.flush()
            
            # ------------------------------------------------------------------------------------------------
            # Fix date/time format for each task, so that the date/time values can be used in Django templates
            # Convert the yyyy-mm-ddThh:mm:ss.dddZ format to a datetime object, and store that
            # There have been occassional format errors in the 'completed' property, 
            # due to 'completed' value such as "-1701567-04-26T07:12:55.000Z"
            # so if any date/timestamp value is invalid, set the property to a sensible default value
            # ------------------------------------------------------------------------------------------------
            for t in tasks:
                num_tasks = num_tasks + 1
              
                date_due = t.get(u'due')
                if date_due:
                    try:
                        new_due_date = datetime.datetime.strptime(date_due, "%Y-%m-%dT00:00:00.000Z").date()
                    except ValueError, e:
                        new_due_date = datetime.date(1900, 1, 1)
                        logging.exception(fn_name + "Invalid 'due' timestamp format, so using " + str(new_due_date))
                        logging.debug(fn_name + "Invalid value was " + str(date_due))
                        logservice.flush()
                    t[u'due'] = new_due_date
                
                datetime_updated = t.get(u'updated')
                if datetime_updated:
                    try:
                        new_datetime_updated = datetime.datetime.strptime(datetime_updated, "%Y-%m-%dT%H:%M:%S.000Z")
                    except ValueError, e:
                        new_datetime_updated = datetime.datetime(1900, 1, 1, 0, 0, 0)
                        logging.exception(fn_name + "Invalid 'updated' timestamp format, so using " + str(new_datetime_updated))
                        logging.debug(fn_name + "Invalid value was " + str(datetime_updated))
                        logservice.flush()
                    t[u'updated'] = new_datetime_updated
                
                datetime_completed = t.get(u'completed')
                if datetime_completed:
                    try:
                        new_datetime_completed = datetime.datetime.strptime(datetime_completed, "%Y-%m-%dT%H:%M:%S.000Z")
                    except ValueError, e:
                        new_datetime_completed = datetime.datetime(1900, 1, 1, 0, 0, 0)
                        logging.exception(fn_name + "Invalid 'completed' timestamp format, so using " + str(new_datetime_completed))
                        logging.debug(fn_name + "Invalid value was " + str(datetime_completed))
                        logservice.flush()
                    t[u'completed'] = new_datetime_completed
                
            if tasklist_dict.has_key(u'tasks'):
                # This is the n'th page of task data for this taslkist, so extend the existing list of tasks
                tasklist_dict[u'tasks'].extend(tasks)
            else:
                # This is the first (or only) list of task for this tasklist
                tasklist_dict[u'tasks'] = tasks
            
            # if is_test_user:
                # logging.debug(fn_name + "Adding %d items for %s" % (len(tasks), tasklist_title))
            # else:
                # logging.debug(fn_name + "Adding %d items to tasklist" % len(tasks))

        
          # ---------------------------------------------------------------------
          # Check if there is another page of data (more tasks for this tasklist)
          # ---------------------------------------------------------------------
          if tasks_data.has_key('nextPageToken'):
            # There is another page of tasks to be retrieved for this tasklist, 
            # which we'll retrieve next time around the while loop.
            # This happens if there are more than 100 tasks in the list
            # See http://code.google.com/apis/tasks/v1/using.html#api_params
            #     "Maximum allowable value: maxResults=100"
            more_tasks_data_to_retrieve = True # Go around while loop again
            next_tasks_page_token = tasks_data['nextPageToken']
            # if is_test_user:
              # logging.debug(fn_name + "There is (at least) one more page of data to be retrieved")
              
            # More than one page, so update progress
            if (datetime.datetime.now() - prev_progress_timestamp).seconds > settings.TASK_COUNT_UPDATE_INTERVAL:
              process_tasks_job.tasklist_progress = num_tasks
              process_tasks_job.job_progress_timestamp = datetime.datetime.now()
              process_tasks_job.put()
              prev_progress_timestamp = datetime.datetime.now()
          else:
            # This is the last (or only) page of results (list of tasks) for this task lists
            # Don't need to update here if no more pages, because calling method updates
            more_tasks_data_to_retrieve = False
            next_tasks_page_token = None
            
        if is_test_user:
          logging.debug(fn_name + "Retrieved " + str(num_tasks) + " tasks from " + tasklist_title)
        else:
          logging.debug(fn_name + "Retrieved " + str(num_tasks) + " tasks from task list")
        logservice.flush()  
        return tasklist_dict, num_tasks
        

        
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
    
    
