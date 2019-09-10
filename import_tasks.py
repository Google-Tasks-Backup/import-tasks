#!/usr/bin/python2.7
# -*- coding: utf-8 -*-

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
# Some parts based on work done by Dwight Guth's Google Tasks Porter

"""Main web application handler for Google Tasks Import."""

# pylint: disable=too-many-lines

__author__ = "julie.smith.1999@gmail.com (Julie Smith)"

import logging
import os
import pickle
# import gc
import time # For sleep
import datetime


from google.appengine.api import urlfetch
from google.appengine.api import taskqueue
from google.appengine.api import users
from google.appengine.api import logservice # To flush logs
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp import blobstore_handlers

import webapp2

from oauth2client.contrib.appengine import OAuth2Decorator

import unicodecsv # Used instead of csv, supports unicode

# Project-specific imports
import model
import settings
import appversion # appversion.version is set before the upload process to keep the version number consistent
import shared # Code which is common between classes, modules or projects
import import_tasks_shared  # Code which is common between classes or modules
import check_task_values
import constants
import host_settings


logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True


# Fix for DeadlineExceeded, because "Pre-Call Hooks to UrlFetch Not Working"
#     Based on code from https://groups.google.com/forum/#!msg/google-appengine/OANTefJvn0A/uRKKHnCKr7QJ
real_fetch = urlfetch.fetch # pylint: disable=invalid-name
def fetch_with_deadline(url, *args, **argv):
    """ Fetch URL with deadline set by URL_FETCH_TIMEOUT """
    argv['deadline'] = settings.URL_FETCH_TIMEOUT
    logservice.flush()
    return real_fetch(url, *args, **argv)
urlfetch.fetch = fetch_with_deadline


msg = "Authorisation error. Please report this error to " + settings.url_issues_page # pylint: disable=invalid-name
auth_decorator = OAuth2Decorator( # pylint: disable=invalid-name
                                 client_id=host_settings.CLIENT_ID,
                                 client_secret=host_settings.CLIENT_SECRET,
                                 scope=host_settings.SCOPE,
                                 user_agent=host_settings.USER_AGENT,
                                 message=msg)
                            

def _send_job_to_worker(self, process_tasks_job): # pylint: disable=too-many-statements
    """ Place the import job details on the taskqueue, so that the worker can process the uploaded data.
    
        This method may be called from;
            ContinueImportJob.get() or .post()
                continuation of a previously started job (using previously uploaded file).
            BlobstoreUploadHandler.get() or .post()
                a new import using the just-uploaded file
    
        When job has been added to taskqueue, the user is redirected to the Progress page.
    
    """

    fn_name = "_send_job_to_worker() "
    
    logging.debug(fn_name + "<Start>")
    logservice.flush()
    
    try:
        user_email = process_tasks_job.user.email()
        
        # ==========================================================
        #       Create a Taskqueue entry to start the import
        # ==========================================================
        if _job_has_stalled(process_tasks_job):
            # Reset job_progress_timestamp on a stalled job, so that Progress handler doesn't see the
            # job as stalled if the worker hasn't yet started processing the re-started job.
            # The downside is that the time in the "Last job progress update was NNNNN seconds ago" message will be lost.
            logging.warning(fn_name + "Resetting job_progress_timestamp. Previous job_progress_timestamp: " +
                str(process_tasks_job.job_progress_timestamp))
            process_tasks_job.job_progress_timestamp = datetime.datetime.now() #UTC
            
        process_tasks_job.put()
        
        # Add the request to the tasks queue, passing in the user's email so that the task can access the database record
        tq_queue = taskqueue.Queue(settings.PROCESS_TASKS_REQUEST_QUEUE_NAME)
        tq_task = taskqueue.Task(url=settings.WORKER_URL, 
                                 params={settings.TASKS_QUEUE_KEY_NAME : user_email}, 
                                 method='POST')
        logging.debug(fn_name + "Adding task to %s queue, for %s" % 
            (settings.PROCESS_TASKS_REQUEST_QUEUE_NAME, user_email))
        logservice.flush()
        
        retry_count = settings.NUM_API_TRIES
        while retry_count > 0:
            retry_count = retry_count - 1
            try:
                tq_queue.add(tq_task)
                break
                
            except Exception, e: # pylint: disable=broad-except
                # Ensure that the job doesn't time out
                process_tasks_job.job_progress_timestamp = datetime.datetime.now() # UTC
                process_tasks_job.message = 'Waiting for server ...'
                
                if retry_count > 0:
                    
                    logging.warning(fn_name + "Exception adding job to taskqueue, " +
                        str(retry_count) + " attempts remaining: "  + shared.get_exception_msg(e))
                    logservice.flush()
                    
                    # Give taskqueue some time before trying again
                    if retry_count <= 2:
                        sleep_time = settings.FRONTEND_API_RETRY_SLEEP_DURATION
                    else:
                        sleep_time = 1
                    
                    logging.info(fn_name + "Sleeping for " + str(sleep_time) + 
                        " seconds before retrying")
                    logservice.flush()
                    # Update job_progress_timestamp so that job doesn't time out
                    process_tasks_job.job_progress_timestamp = datetime.datetime.now()
                    process_tasks_job.put()
                    
                    time.sleep(sleep_time)
                    
                else:
                    logging.exception(fn_name + "Exception adding job to taskqueue")
                    logservice.flush()
            
                    process_tasks_job.status = constants.ImportJobStatus.ERROR
                    process_tasks_job.message = ''
                    process_tasks_job.error_message = "Error starting import process"
                    process_tasks_job.error_message_extra = shared.get_exception_msg(e)
                    
                    logging.debug(fn_name + "Job status: '" + unicode(process_tasks_job.status) + ", progress: " + 
                        unicode(process_tasks_job.total_progress) + ", msg: '" + 
                        unicode(process_tasks_job.message) + "', err msg: '" + unicode(process_tasks_job.error_message) +
                        unicode(process_tasks_job.error_message_extra))
                    logservice.flush()
                    process_tasks_job.put()
                    
                    # Import process terminated, so delete the blobstore
                    blob_key = process_tasks_job.blobstore_key
                    blob_info = blobstore.BlobInfo.get(blob_key)
                    import_tasks_shared.delete_blobstore(blob_info)
                    
                    shared.serve_message_page(self, "Error creating tasks import job.",
                        "Please report the following error using the link below",
                        shared.get_exception_msg(e),
                        show_custom_button=True, custom_button_text="Return to main menu")
                    
                    logging.debug(fn_name + "<End> (error adding job to taskqueue)")
                    logservice.flush()
                    return

        logging.debug(fn_name + "Import job added to taskqueue. Redirecting to " + settings.PROGRESS_URL)
        logservice.flush()
        
        # Redirect to Progress page
        self.redirect(settings.PROGRESS_URL)
        
    except shared.DailyLimitExceededError, e:
        logging.warning(fn_name + constants.DAILY_LIMIT_EXCEEDED_LOG_LABEL + e.msg)
        shared.serve_quota_exceeded_page(self)
        logging.debug(fn_name + "<End> (Daily Limit Exceeded)")
        logservice.flush()
        
    except Exception, e: # pylint: disable=broad-except
        logging.exception(fn_name + "Caught top-level exception")
        shared.serve_outer_exception_message(self, e)
        logging.debug(fn_name + "<End> due to exception")
        logservice.flush()

    logging.debug(fn_name + "<End>")
    logservice.flush()
    
    

class WelcomeHandler(webapp2.RequestHandler):
    """ Displays an introductory web page, explaining what the app does and providing link to authorise.
    
        This page can be viewed even if the user is not logged in.
    """

    # Do not add auth_decorator to Welcome page handler, because we want anyone to be able to view the welcome page
    def get(self):
        """ Handles GET requests for settings.WELCOME_PAGE_URL """

        fn_name = "WelcomeHandler.get(): "

        logging.debug(fn_name + "<Start>")
        logservice.flush()
        

        try:
            display_link_to_production_server = False # pylint: disable=invalid-name
            if not self.request.host in settings.PRODUCTION_SERVERS:
                logging.info("%sRunning on non-production server %s",
                            fn_name, self.request.host)
                if settings.DISPLAY_LINK_TO_PRODUCTION_SERVER:
                    display_link_to_production_server = True # pylint: disable=invalid-name
            
            user = users.get_current_user()
            user_email = None
            is_admin_user = False
            if user:
                user_email = user.email()
                
                if not self.request.host in settings.PRODUCTION_SERVERS:
                    if shared.is_test_user(user_email):
                        # Allow test user to see normal page content
                        display_link_to_production_server = False # pylint: disable=invalid-name
                    else:
                        logging.info("%sRejecting non-test user [%s] on limited access server %s",
                            fn_name, user_email, self.request.host)
                
                logging.debug(fn_name + "User is logged in, so displaying username and logout link")
                is_admin_user = users.is_current_user_admin()
            else:
                logging.debug(fn_name + "User is not logged in, so won't display logout link")
            logservice.flush()
            
            
            template_values = {'app_title' : host_settings.APP_TITLE,
                               'display_link_to_production_server' : display_link_to_production_server,
                               'production_server' : settings.PRODUCTION_SERVERS[0],
                               'host_msg' : host_settings.HOST_MSG,
                               'url_home_page' : settings.MAIN_PAGE_URL,
                               'url_GTB' : settings.url_GTB,
                               'eot_executable_name' : settings.EOT_EXECUTABLE_NAME,
                               'product_name' : host_settings.PRODUCT_NAME,
                               'is_admin_user' : is_admin_user,
                               'user_email' : user_email,
                               'url_main_page' : settings.MAIN_PAGE_URL,
                               'manage_blobstore_url' : settings.ADMIN_MANAGE_BLOBSTORE_URL,
                               'retrieve_stats_csv_url' : settings.ADIMN_RETRIEVE_STATS_CSV_URL,
                               'outlook_instructions_url' : settings.OUTLOOK_INSTRUCTIONS_URL,
                               'msg': self.request.get('msg'),
                               'APPEND_TIMESTAMP' : constants.ImportMethod.APPEND_TIMESTAMP,
                               'USE_OWN_SUFFIX' : constants.ImportMethod.USE_OWN_SUFFIX,
                               'IMPORT_AS_IS' : constants.ImportMethod.IMPORT_AS_IS,
                               'ADD_TO_EXISTING_TASKLIST' : constants.ImportMethod.ADD_TO_EXISTING_TASKLIST,
                               'REPLACE_TASKLIST_CONTENT' : constants.ImportMethod.REPLACE_TASKLIST_CONTENT,
                               'SKIP_DUPLICATE_TASKLIST' : constants.ImportMethod.SKIP_DUPLICATE_TASKLIST,
                               'DELETE_BEFORE_IMPORT' : constants.ImportMethod.DELETE_BEFORE_IMPORT,
                               'logout_url': users.create_logout_url(settings.WELCOME_PAGE_URL),
                               'url_discussion_group' : settings.url_discussion_group,
                               'email_discussion_group' : settings.email_discussion_group,
                               'SUPPORT_EMAIL_ADDRESS' : settings.SUPPORT_EMAIL_ADDRESS,
                               'url_issues_page' : settings.url_issues_page,
                               'url_source_code' : settings.url_source_code,
                               'app_version' : appversion.version,
                               'upload_timestamp' : appversion.upload_timestamp}
                               
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "welcome.html")
            self.response.out.write(template.render(path, template_values))
            logging.debug(fn_name + "<End>")
            logservice.flush()

        except shared.DailyLimitExceededError, e:
            logging.warning(fn_name + constants.DAILY_LIMIT_EXCEEDED_LOG_LABEL + e.msg)
            shared.serve_quota_exceeded_page(self)
            logging.debug(fn_name + "<End> (Daily Limit Exceeded)")
            logservice.flush()
            
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception")
            logservice.flush()
    
    
    
class MainHandler(webapp2.RequestHandler):
    """ Displays Main web page.
    
        If user has an existing paused job, user can choose to continue existing job, or start a new job.
        
        If no paused job, user can start an import job.
    """

    @auth_decorator.oauth_required
    def get(self): # pylint: disable=too-many-locals,too-many-statements
        """ Main page, once user has been authenticated """

        fn_name = "MainHandler.get(): "

        logging.debug(fn_name + "<Start>")
        logservice.flush()
        

        try:
            user = users.get_current_user()
            
            user_email = user.email()
            is_admin_user = users.is_current_user_admin()
            
            display_link_to_production_server = False # pylint: disable=invalid-name
            if not self.request.host in settings.PRODUCTION_SERVERS:
                # logging.debug(fn_name + "Running on limited-access server")
                if settings.DISPLAY_LINK_TO_PRODUCTION_SERVER:
                    display_link_to_production_server = True # pylint: disable=invalid-name
                if shared.is_test_user(user_email):
                    # Allow test user to see normal page
                    display_link_to_production_server = False # pylint: disable=invalid-name
                else:
                    logging.info("%sRejecting non-test user [%s] on limited access server %s",
                        fn_name, user_email, self.request.host)
                    logservice.flush()
                    shared.reject_non_test_user(self)
                    logging.debug(fn_name + "<End> (Non test user on limited access server)")
                    logservice.flush()
                    return
            
            
            # Retrieve the DB record for this user
            process_tasks_job = model.ImportTasksJobV1.get_by_key_name(user_email)
            
            # file_upload_time = None
            file_name = None
            job_status = None
            total_progress = 0
            total_num_rows_to_process = 0
            remaining_tasks = 0
            import_in_progress = False
            found_paused_job = False
            job_start_timestamp = ''
            pause_reason = None
            if process_tasks_job:
                job_status = process_tasks_job.status
                
                logging.debug(fn_name + "DEBUG: Retrieved import tasks job for " + user_email + ", status = " + job_status)
                logservice.flush()
                
                file_name = process_tasks_job.file_name
                job_start_timestamp = process_tasks_job.job_start_timestamp # UTC
                total_num_rows_to_process = process_tasks_job.total_num_rows_to_process
                total_progress = process_tasks_job.total_progress
                remaining_tasks = total_num_rows_to_process - total_progress
                
                if process_tasks_job.is_paused:
                    logging.debug(fn_name + "Found paused import job for " + user_email)
                    logservice.flush()
                    found_paused_job = True
                    
                    pause_reason = process_tasks_job.pause_reason
                else:
                    if _job_has_stalled(process_tasks_job):
                        found_paused_job = True
                        job_status = constants.ImportJobStatus.STALLED
                        pause_reason = constants.PauseReason.JOB_STALLED
                    else:
                        # Found existing, non paused job in progress
                        if job_status in constants.ImportJobStatus.PROGRESS_VALUES:
                            import_in_progress = True
                            logging.debug(fn_name + "Import job for " + user_email + " is " + job_status +
                                ", so redirecting to " + settings.PROGRESS_URL) 
                            logservice.flush()
                            self.redirect(settings.PROGRESS_URL)
                            logging.warning(fn_name + "<End> Import already in progress")
                            logservice.flush()
                            return
                
                
                # DEBUG
                # found_paused_job = True
                # file_name = "Dummy file name.csv"
                # total_num_rows_to_process = 8197
                # total_progress = 1943
                # logging.debug(fn_name + "DEBUG: Found paused import job")
                # logservice.flush()
            
            else:
                logging.debug(fn_name + "New user = " + user_email)
                logservice.flush()
                
            template_values = {'app_title' : host_settings.APP_TITLE,
                               'display_link_to_production_server' : display_link_to_production_server,
                               'production_server' : settings.PRODUCTION_SERVERS[0],
                               'host_msg' : host_settings.HOST_MSG,
                               'url_home_page' : settings.MAIN_PAGE_URL,
                               'url_GTB' : settings.url_GTB,
                               'eot_executable_name' : settings.EOT_EXECUTABLE_NAME,
                               'outlook_instructions_url' : settings.OUTLOOK_INSTRUCTIONS_URL,
                               'new_blobstore_url' : settings.GET_NEW_BLOBSTORE_URL,
                               'continue_job_url' : settings.CONTINUE_IMPORT_JOB_URL,
                               'product_name' : host_settings.PRODUCT_NAME,
                               'is_admin_user' : is_admin_user,
                               'job_status' : job_status,
                               'STALLED' : constants.ImportJobStatus.STALLED,
                               'pause_reason' : pause_reason,
                               'import_in_progress' : import_in_progress,
                               'found_paused_job' : found_paused_job,
                               # 'file_upload_time' : file_upload_time, # Pacific Standard Time
                               'total_num_rows_to_process' : total_num_rows_to_process,
                               'total_progress' : total_progress,
                               'remaining_tasks' : remaining_tasks,
                               'file_name' : file_name,
                               'job_start_timestamp' : job_start_timestamp, # UTC
                               'manage_blobstore_url' : settings.ADMIN_MANAGE_BLOBSTORE_URL,
                               'user_email' : user_email,
                               'msg': self.request.get('msg'),
                               'APPEND_TIMESTAMP' : constants.ImportMethod.APPEND_TIMESTAMP,
                               'USE_OWN_SUFFIX' : constants.ImportMethod.USE_OWN_SUFFIX,
                               'IMPORT_AS_IS' : constants.ImportMethod.IMPORT_AS_IS,
                               'ADD_TO_EXISTING_TASKLIST' : constants.ImportMethod.ADD_TO_EXISTING_TASKLIST,
                               'REPLACE_TASKLIST_CONTENT' : constants.ImportMethod.REPLACE_TASKLIST_CONTENT,
                               'SKIP_DUPLICATE_TASKLIST' : constants.ImportMethod.SKIP_DUPLICATE_TASKLIST,
                               'DELETE_BEFORE_IMPORT' : constants.ImportMethod.DELETE_BEFORE_IMPORT,
                               'logout_url': users.create_logout_url(settings.WELCOME_PAGE_URL),
                               'url_discussion_group' : settings.url_discussion_group,
                               'email_discussion_group' : settings.email_discussion_group,
                               'SUPPORT_EMAIL_ADDRESS' : settings.SUPPORT_EMAIL_ADDRESS,
                               'url_issues_page' : settings.url_issues_page,
                               'url_source_code' : settings.url_source_code,
                               'app_version' : appversion.version,
                               'upload_timestamp' : appversion.upload_timestamp}
                               
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "main.html")
            self.response.out.write(template.render(path, template_values))
            # logging.debug(fn_name + "Calling garbage collection")
            # gc.collect()
            logging.debug(fn_name + "<End>")
            logservice.flush()
            
        except shared.DailyLimitExceededError, e:
            logging.warning(fn_name + constants.DAILY_LIMIT_EXCEEDED_LOG_LABEL + e.msg)
            shared.serve_quota_exceeded_page(self)
            logging.debug(fn_name + "<End> (Daily Limit Exceeded)")
            logservice.flush()
                        
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
    
    

class ContinueImportJob(webapp2.RequestHandler):
    """ Continue importing a previously paused import job """
 
 
    @auth_decorator.oauth_required
    def get(self):
        """ Continue importing a previously paused import job """
        fn_name = "ContinueImportJob.get(): "
            
        logging.debug(fn_name + "<Start>")
        logservice.flush()
    

        try:
            self._continue_import_job()
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception")
            logservice.flush()

    
        logging.debug(fn_name + "<End>")
        logservice.flush()
        

    @auth_decorator.oauth_required
    def post(self):
        """ Continue importing a previously paused import job """
        
        fn_name = "ContinueImportJob.post(): "
            
        logging.debug(fn_name + "<Start>")
        logservice.flush()
    
        try:
            self._continue_import_job()
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception")
            logservice.flush()

    
        logging.debug(fn_name + "<End>")
        logservice.flush()
        
        
    def _continue_import_job(self):
        """If a paused job record exists for this user, then send job to worker.
        
           If job exists, but hasn't been paused, redirect to progress page
           
           If job doesn't exist, display message with button to go to main page.
        """
        
        fn_name = "_continue_import_job(): "
        
        user = users.get_current_user()
        user_email = user.email()
        
        if not self.request.host in settings.PRODUCTION_SERVERS:
            if not shared.is_test_user(user_email):
                logging.info("%sRejecting non-test user [%s] on limited access server %s",
                    fn_name, user_email, self.request.host)
                logservice.flush()
                shared.reject_non_test_user(self)
                logging.debug(fn_name + "<End> (Non test user on limited access server)")
                logservice.flush()
                return
        
        # Retrieve the import job record for this user
        process_tasks_job = model.ImportTasksJobV1.get_by_key_name(user_email)
        
        if process_tasks_job is None:
            logging.error(fn_name + "No DB record for " + user_email)
            shared.serve_message_page(self, "No import job found.",
                "If you believe this to be an error, please report this at the link below",
                show_custom_button=True, custom_button_text='Main Menu', 
                custom_button_url=settings.MAIN_PAGE_URL)
            logging.warning(fn_name + "<End> No DB record")
            logservice.flush()
            return
        
        
        if process_tasks_job.is_paused or _job_has_stalled(process_tasks_job):
            # ========================================================
            #       Add job to taskqueue for worker to process
            # ========================================================
            # Worker will continue the paused job
            logging.debug(fn_name + "Continuing paused job")
            _send_job_to_worker(self, process_tasks_job)
            
        else:
            # Redirect to /progress
            logging.warning(fn_name + "Job is not paused. Status = " + process_tasks_job.status +
                ", so rediredting to " + settings.PROGRESS_URL)
            logservice.flush()
            self.redirect(settings.PROGRESS_URL)
        
        
        
class BlobstoreUploadHandler(blobstore_handlers.BlobstoreUploadHandler):
    """ Handle Blobstore uploads """

    num_data_rows = 0
    
    def get(self):
        """ Handles redirect from authorisation, which can happen if authorisation is required 
            when handling the POST request.
            
            The POST handler is decorated by @auth_decorator.oauth_required, so the job will never have
            been set up within the POST handler if the user was not authenticated, so just redirect to /main
            so the user can start again.
            
            Also handles user going direct to this URL, in which case we also want to redirect to /main
        """
        
        fn_name = "BlobstoreUploadHandler.get(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            logging.warning(fn_name + "Unexpected GET, so redirecting to " + settings.MAIN_PAGE_URL)
            logservice.flush()
            self.redirect(settings.MAIN_PAGE_URL)
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception")
            logservice.flush()

        logging.debug(fn_name + "<End>")
        logservice.flush()

    
    @auth_decorator.oauth_required
    def post(self): # pylint: disable=too-many-locals,too-many-statements,too-many-return-statements,too-many-branches
        """ Get the blob_info of the uploaded file, store the details of the import job, and try to start the import job
        
            The Blobstore upload lands at this URL as a POST
        """
        
        fn_name = u"BlobstoreUploadHandler.post(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try: # pylint: disable=too-many-nested-blocks
            user = users.get_current_user()
            
            user_email = user.email()
            
            display_link_to_production_server = False # pylint: disable=invalid-name,unused-variable
            if not self.request.host in settings.PRODUCTION_SERVERS:
                # logging.debug(fn_name + "Running on limited-access server")
                if settings.DISPLAY_LINK_TO_PRODUCTION_SERVER:
                    display_link_to_production_server = True # pylint: disable=invalid-name
                if shared.is_test_user(user_email):
                    # Allow test user to see normal page
                    display_link_to_production_server = False # pylint: disable=invalid-name
                else:
                    logging.info("%sRejecting non-test user [%s] on limited access server %s",
                        fn_name, user_email, self.request.host)
                    logservice.flush()
                    shared.reject_non_test_user(self)
                    logging.debug(fn_name + "<End> (Non test user on limited access server)")
                    logservice.flush()
                    return
        
            # =================================================
            #   Don't start a new job if job already running
            #       unless user chose 'force_new_upload'
            # =================================================
            # Check if worker is already processing an import job
            #   If job is paused, 
            #       User must have chosen to start a new job, so ignore existing job state (i.e., start new job)
            #   If job is IMPORT_COMPLETED or ERROR
            #       Start a new job
            #   If job is STARTING, INITIALISING, IMPORTING
            #       Tell user "job in progress"
            
            force_new_upload = (self.request.get('force_new_upload') == 'True')
            
            if force_new_upload:
                logging.debug(fn_name + "User chose to force new upload")
                logservice.flush()
            else:
                # Retrieve the import job record for this user (if there is an existing job)
                process_tasks_job = model.ImportTasksJobV1.get_by_key_name(user_email)
                
                if process_tasks_job is not None:
                    logging.debug(fn_name + "DEBUG: Found job record for " + user_email)
                    logservice.flush()
                    # If there is a paused job, user must have chosen to start a new job, 
                    # so ignore existing job state (i.e., start new job)
                    if not process_tasks_job.is_paused:
                        # Found existing, non paused job in progress
                        if process_tasks_job.status in constants.ImportJobStatus.PROGRESS_VALUES:
                            shared.serve_message_page(self, "Import already in progress",
                                "Please wait until the current import has completed before starting a new import",
                                "If you believe this to be an error, please report this at the link below",
                                show_custom_button=True, custom_button_text='View import progress', 
                                custom_button_url=settings.PROGRESS_URL)
                            logging.warning(fn_name + "<End> Import already in progress")
                            logservice.flush()
                            return
                
            # Determine the chosen import method (and optional suffix)
            import_method = self.request.get('import_method') # How to process the import
            if import_method == constants.ImportMethod.APPEND_TIMESTAMP:
                # Put space between tasklist name and timestamp
                import_tasklist_suffix = " " + self.request.get('import_timestamp_suffix')
            elif import_method == constants.ImportMethod.USE_OWN_SUFFIX:
                # Don't include spaces at the end. It is up to user to include space at the start.
                user_suffix = self.request.get('user_suffix')
                if user_suffix:
                    import_tasklist_suffix = user_suffix.rstrip()
                else:
                    import_tasklist_suffix = ''
            else:
                import_tasklist_suffix = ''
                
            upload_files = self.get_uploads('file') # 'file' is the name of the file upload field in the form
            if upload_files:
                blob_info = upload_files[0]
                blob_key = str(blob_info.key())
                # Even with the fix suggested by http://code.google.com/p/googleappengine/issues/detail?id=2749#c21
                # if the filename contains unicode characters, it is returned as ?? quotedprintable or base64 ??
                #file_name = blob_info.filename
                # This doesn't work for non-ASCII filenames, which are returned as 
                #   "=?ISO-8859-1?B?dGFza3NfaW1wb3J0X2V4cG9ydCBMYW5n?="
                # Even decoding the Base-64 portion doesn't work, becaus the filename is cut off
                # at the first non-ASCII character
                
                # WORKAROUND: 2012-11-06
                # Thanks to http://code.google.com/p/googleappengine/issues/detail?id=2749#c58
                # Note that this may load the entire blob into memory, so very large files could
                # potentially be a problem (but shouldn't be too bad in GTI, because tasks files 
                # are generally only a few MB at most.
                file_name = "Unknown"
                try:
                    blobs = blobstore.BlobInfo.get(blob_key)
                    file_name = blobs.filename
                except Exception, e: # pylint: disable=broad-except
                    logging.error(fn_name + "Unable to determine the filename of the uploaded file: " + 
                        shared.get_exception_msg(e))
                    logservice.flush()
                
                logging.debug(fn_name + "DEBUG: Uploaded filename = '%s' for %s" % (file_name, user_email))
                logservice.flush()
                
                if blob_info.size == 0:
                    err_msg = "Uploaded file is empty"
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg)
                    # Import process terminated, so delete the blobstore
                    import_tasks_shared.delete_blobstore(blob_info)
                    logging.debug(fn_name + "<End> due to empty upload file")
                    logservice.flush()
                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg, 
                        show_custom_button=True)
                    return
                
                if shared.is_test_user(user_email):
                    logging.debug(fn_name + "TEST: Filename = %s" % file_name)
                    logservice.flush()
                
                file_type = None
                    
                # CHANGE: JS 2012-08-03, Wrapped file type determination in exception handler to provide
                #                        a more useful message to user.
                try:
                    test_reader = blobstore.BlobReader(blob_key)
                    
                    # ----------------------------------------------------------------
                    #   Check if file uses one of the unsupported Unicode encodings
                    # ----------------------------------------------------------------
                    result = import_tasks_shared.file_has_valid_encoding(test_reader)
                    if result != 'OK':
                        err_msg1 = result
                        err_msg2 = "Editors such as Notepad and other Windows programs such as Excel generally do not save files with the required encoding."
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1 + ": " + err_msg2)
                        # Import process terminated, so delete the blobstore
                        import_tasks_shared.delete_blobstore(blob_info)
                        logging.debug(fn_name + "<End> (Invalid unicode file encoding)")
                        logservice.flush()
                        import_tasks_shared.serve_invalid_file_format_page(self, file_name, 
                            err_msg1, err_msg2, constants.VALID_FILE_FORMAT_MSG, show_custom_button=True)
                        return

                    # File is not one of the unsupported Unicode formats, but could still be another unsupported encoding
                    # (e.g., a binary file such as MS PST, Excel or Doc)
                    
                    # -------------------------------------------
                    #   Test if first row can be read as UTF-8
                    # -------------------------------------------
                    try:
                        test_reader.seek(0)
                        line1 = unicode(test_reader.readline().strip(), "utf-8")
                    except Exception, e: # pylint: disable=broad-except
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                            "Unable to read first line of file as UTF-8")
                        err_msg1 = "Error processing header row of file: " + shared.get_exception_msg(e)
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1)
                        # Import process terminated, so delete the blobstore
                        import_tasks_shared.delete_blobstore(blob_info)
                        logging.debug(fn_name + "<End> (non-UTF-8 file encoding)")
                        logservice.flush()
                        import_tasks_shared.serve_invalid_file_format_page(self, file_name, 
                            err_msg1, None, constants.VALID_FILE_FORMAT_MSG, show_custom_button=True)
                        return
                        
                    if shared.is_test_user(user_email):
                        try:
                            logging.debug(fn_name + u"TEST: First line = '%s'" % line1)
                        except Exception, e: # pylint: disable=broad-except
                            logging.debug(fn_name + u"TEST: Unable to log first line: " + shared.get_exception_msg(e))
                    
                    # ========================
                    #   Determine file type
                    # ========================
                    # Check filename extension. Check lowercase in case user has changed case of extension. 
                    if file_name.lower().endswith('.gtbak'):
                        file_type = 'gtbak'
                    else:
                        # -------------------------------------------
                        #   Check if CSV file header row is valid
                        # -------------------------------------------
                        # Call file_has_valid_header_row() to check if the header row is valid.
                        # This checks;
                        #   File doesn't have a BOM
                        #   Header row is plain ASCII
                        #   Header row contains the minimumm set of valid column names
                        # 
                        # The Import/Export CSV format contains all 9 columns, but users may 
                        # import files with fewer columns, as per MINIMUM_CSV_ELEMENT_LIST.
                        test_reader.seek(0)
                        result, display_line = import_tasks_shared.file_has_valid_header_row(test_reader, 
                            constants.MINIMUM_CSV_ELEMENT_LIST)
                        if result == 'OK':
                            file_type = 'csv'
                        else:
                            # ----------------------------------------
                            #     Unsupported file type or layout
                            # ----------------------------------------
                            err_msg1 = result
                            if display_line:
                                try:
                                    err_msg2 = "Invalid header row: &nbsp; <span class='fixed-font'>" + line1 + "</span>"
                                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                                        err_msg1 + ": Header row = '" + line1 + "'")
                                except Exception, e: # pylint: disable=broad-except
                                    err_msg2 = "Invalid header row in file" 
                                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                                        err_msg1 + " and unable to log header row: " +
                                        shared.get_exception_msg(e))
                            else:
                                err_msg2 = None
                                logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1)
                            logservice.flush()
                            # Import process terminated, so delete the blobstore
                            import_tasks_shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> (Unknown data format)")
                            logservice.flush()
                            import_tasks_shared.serve_invalid_file_format_page(self, file_name, 
                                err_msg1, err_msg2, constants.VALID_FILE_FORMAT_MSG, 
                                show_custom_button=True)
                            return
                            
                except Exception, e: # pylint: disable=broad-except
                    logging.exception(fn_name + "Error reading file (determining file type)")
                    # Don't log/display file name or content, as non-ASCII string may cause another exception
                    err_msg1 = "Unable to import tasks - Unknown data format in file"
                    err_msg2 = "Error: " + shared.get_exception_msg(e)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1 + ": " + err_msg2)
                    # Import process terminated, so delete the blobstore
                    import_tasks_shared.delete_blobstore(blob_info)
                    logging.debug(fn_name + "<End> (Exception determining file type)")
                    logservice.flush()
                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                        constants.VALID_FILE_FORMAT_MSG, show_custom_button=True)
                    
                    # Try to log the offending data
                    try:
                        logging.debug(fn_name + u"First line = '%s'" % line1)
                    except Exception, e: # pylint: disable=broad-except
                        logging.exception(fn_name + "Unable to log first line")
                    
                    return
                        
                    
                finally:
                    test_reader.close()
                
                logging.debug(fn_name + "Filetype: '%s'" % file_type)
                logservice.flush()

                # ===================================================
                #     Check the contents of the uploaded CSV file
                # ===================================================
                if file_type == 'csv':
                    # -----------------------------------------------
                    #       Check if CSV file contains valid data
                    # -----------------------------------------------
                    if not self._csv_file_contains_valid_data(blobstore.BlobReader(blob_key), import_tasklist_suffix, file_name):
                        # The testing routine will have displayed message to the user.
                        # We just need to clean up here
                        # Import process terminated, so delete the blobstore
                        import_tasks_shared.delete_blobstore(blob_info)
                        logging.debug(fn_name + "<End> (Invalid CSV data)")
                        logservice.flush()
                        return
                    
                elif file_type == 'gtbak':
                    try:
                        # --------------------------------------------------
                        #       Check if GTBak file contains valid data
                        # --------------------------------------------------
                        self.num_data_rows = 0
                        # There are two pickles in the GTBak blob; a file format version string, and the actual tasks data
                        
                        # Read the first pickle; file version number string
                        blob_reader = blobstore.BlobReader(blob_key)
                        file_format_version = None
                        try:
                            file_format_version = pickle.load(blob_reader)
                        except Exception, e: # pylint: disable=broad-except
                            logging.exception(fn_name + "Error unpickling file format version string")
                            err_msg1 = "This is not a valid GTBak file, or the file contents is corrupted"
                            logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1)
                            # Import process terminated, so delete the blobstore
                            import_tasks_shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> (error unpickling file version)")
                            logservice.flush()
                            import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, 
                                show_custom_button=True)
                            return
                            
                            
                        if file_format_version != 'v1.0':
                            err_msg1 = "Unable to import file"
                            err_msg2 = "This file was created with an incompatible version of GTB"
                            logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg2)
                            # Import process terminated, so delete the blobstore
                            import_tasks_shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> (invalid GTBak file format version)")
                            logservice.flush()
                            import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                                show_custom_button=True)
                            return
                        
                        # Read the 2nd pickle; tasks data
                        tasks_data = None
                        try:
                            tasks_data = pickle.load(blob_reader)
                        except Exception, e: # pylint: disable=broad-except
                            logging.exception(fn_name + "Error unpickling tasks data")
                            err_msg1 = "This is not a valid GTBak file, or the file contents is corrupted"
                            err_msg2 = shared.get_exception_msg(e)
                            logging.warning(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1 + ": " + err_msg2)
                            # Import process terminated, so delete the blobstore
                            import_tasks_shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> (error unpickling tasks data)")
                            logservice.flush()
                            import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2,
                                show_custom_button=True)
                            return
                            
                        for task in tasks_data:
                            self.num_data_rows = self.num_data_rows + 1
                            # Check that the minimal requirements for each task exist;
                            #   tasklist_name, title, status, depth
                            for k in constants.MINIMUM_GTBAK_ELEMENT_LIST:
                                if not task.has_key(k):
                                    err_msg1 = "Unable to import file"
                                    err_msg2 = "Task " + str(self.num_data_rows) + " is missing '" + k + "'"
                                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg2)
                                    # Import process terminated, so delete the blobstore
                                    import_tasks_shared.delete_blobstore(blob_info)
                                    logging.debug(fn_name + "<End> (missing task element in GTBak file)")
                                    logservice.flush()
                                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2,
                                        show_custom_button=True)
                                    return
                            if self.num_data_rows == 1:
                                # Depth of 1st task must be zero)
                                depth = int(task[u'depth'])
                                if depth != 0:
                                    # First task imported must have depth of zero; it must be a root task
                                    err_msg1 = "Unable to import file"
                                    err_msg2 = "Invalid depth [" + str(depth) + \
                                        "] of first task; First task must have depth = 0"
                                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg2)
                                    logservice.flush()
                                    # Import process terminated, so delete the blobstore
                                    import_tasks_shared.delete_blobstore(blob_info)
                                    logging.debug(fn_name + "<End> (non-zero depth of first task in GTBak file)")
                                    logservice.flush()
                                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2,
                                        show_custom_button=True)
                                    return                                 
                                
                        if self.num_data_rows == 0:
                            err_msg1 = "Nothing to import"
                            err_msg2 = "Import file contains no tasks"
                            logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg2)
                            # Import process terminated, so delete the blobstore
                            import_tasks_shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> (no tasks in GTBak file)")
                            logservice.flush()
                            import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                                show_custom_button=True)
                            return
                            
                    except Exception, e: # pylint: disable=broad-except
                        logging.exception(fn_name + "Exception parsing GTBak file")
                        err_msg1 = "Error reading import file"
                        err_msg2 = "The data is not in the correct GTBak format: " + shared.get_exception_msg(e)
                        logging.warning(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg2)
                        # Import process terminated, so delete the blobstore
                        import_tasks_shared.delete_blobstore(blob_info)
                        logging.debug(fn_name + "<End> (exception parsing GTBak file)")
                        logservice.flush()
                        import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                            constants.VALID_FILE_FORMAT_MSG, show_custom_button=True)
                        return

                else:
                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, 
                        "Unable to process uploaded file - unknown file type",
                        constants.VALID_FILE_FORMAT_MSG, show_custom_button=True)
                    return

                logging.debug(fn_name + "Import data file (potentially) contains " + str(self.num_data_rows) + " tasks")
                    
                logging.debug(fn_name + "Import method: " + str(import_method) + ", tasklist suffix = '" + import_tasklist_suffix + "'")
                
                # Create a DB record, using the user's email address as the key
                process_tasks_job = model.ImportTasksJobV1(key_name=user_email)
                process_tasks_job.user = user
                process_tasks_job.total_num_rows_to_process = self.num_data_rows
                process_tasks_job.blobstore_key = blob_key
                process_tasks_job.file_type = file_type
                process_tasks_job.file_name = file_name
                # process_tasks_job.file_upload_time = file_upload_time # Pacific Standard Time
                process_tasks_job.import_tasklist_suffix = import_tasklist_suffix
                process_tasks_job.import_method = import_method
                process_tasks_job.job_start_timestamp = datetime.datetime.now() #UTC
                process_tasks_job.status = constants.ImportJobStatus.STARTING
                process_tasks_job.message = 'Waiting for server ...'
                process_tasks_job.put()

                # ========================================================
                #       Add job to taskqueue for worker to process
                # ========================================================
                logging.debug(fn_name + "Starting new import job")
                _send_job_to_worker(self, process_tasks_job)
                
    
            else:
                logging.debug(fn_name + "<End> due to no file uploaded")
                logservice.flush()
                shared.serve_message_page(self, 'No file uploaded, please try again.', show_custom_button=True)
                
                
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception")
            logservice.flush()

        logging.debug(fn_name + "<End>")
        logservice.flush()
        
        
    def _all_fields_have_values(self, task_row_data, required_column_names, file_name, row_num):
        """Returns true if each data row has a value for each column in the header row
        
                task_row_data               Row of data from a DictReader
                required_column_names       List of columns to be checked
                file_name                   Name of file, for reporting error to user
                row_num                     Data row number, for reporting error to user
                
            If a DictReader row has fewer values than the number of columns, the missing field values are None,
            so if any fields are None, then that may indicate;
            (1) a missing field or
            (2) a mismatched or missing double quote, or
            (3) a newline in a non-double-quote delimited field 
                -- newline is allowed in text fields as long as the field is double-quote delimited
        """
    
        fn_name = "_all_fields_have_values: "
        
        for col_name in required_column_names:
            if task_row_data[col_name] is None:
                err_msg1 = "Missing '" + col_name + "' value in data row " + str(row_num)
                err_msg2 = """Possible causes: 
                    <ol> 
                        <li>The number of values in data row %d does not match the number of header columns</li> 
                        <li>A field may be missing a double-quote</li>
                        <li>There may be a line break in a non-double-quoted field</li>
                    </ol>""" % row_num
                logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1)
                import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                    show_custom_button=True)
                logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL +
                    "<End> " + err_msg1)
                logservice.flush()
                return False
            
        # logging.info(fn_name + "DEBUG: <End> All tasks have values")
        # logservice.flush()
        return True
        
        
        
    def _csv_file_contains_valid_data( # pylint: disable=too-many-locals,too-many-return-statements,too-many-branches,too-many-statements
                                      self, file_obj, import_tasklist_suffix, file_name=None):
        """Returns true if the file contains data in a format that can be parsed by the worker.
            
                file_obj                A file object referring to a CSV file
                import_tasklist_suffix  The optional tasklist name suffix chosen by the user
                                            This is need to check that the overall tasklist name length is below the limit
                file_name               Name of file, for reporting error to user
        """
        
        fn_name = "_csv_file_contains_valid_data: "
        
        dict_reader = None
        is_test_user = shared.is_test_user(users.get_current_user().email())
        
        try:
            prev_depth = 0
            prev_tasklist_name = ""
            self.num_data_rows = 0
            dict_reader = unicodecsv.DictReader(file_obj, dialect='excel')
            
            column_names = dict_reader.fieldnames
            
            for task_row_data in dict_reader:
                self.num_data_rows += 1
                
                # --------------------------------------
                #   Check that each field has a value
                # --------------------------------------
                if not self._all_fields_have_values(task_row_data, column_names, file_name, self.num_data_rows):
                    if is_test_user:
                        logging.debug(fn_name + u"TEST: "  + constants.INVALID_FORMAT_LOG_LABEL + " task_row_data ==>")
                        try:
                            logging.debug(task_row_data)
                        except Exception, e: # pylint: disable=broad-except
                            logging.debug(fn_name + u"TEST: Unable to log task_row_data: " + shared.get_exception_msg(e))
                            
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL +
                        "<End> (Missing task values)")
                    logservice.flush()
                    return False

                # -----------------------
                #   Check tasklist name
                # -----------------------
                tasklist_name = task_row_data.get('tasklist_name').strip()
                if not tasklist_name:
                    err_msg1 = 'Misssing "tasklist_name" value in data row ' + str(self.num_data_rows)
                    err_msg2 = 'The "tasklist_name" value is required'
                    err_msg3 = 'The tasklist name can be the name of an existing tasklist, or a new tasklist that will be created, or "@default" to add a task to the default (primary) tasklist'
                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, err_msg3,
                        show_custom_button=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                        "<End> (Missing 'tasklist_name' value)")
                    logservice.flush()
                    return False
                    
                if len(tasklist_name + import_tasklist_suffix) > settings.MAX_TASKLIST_NAME_LEN:
                    # As at 2013-06-04, the Google Tasks server returns HttpError 400 "Invalid Value"
                    # if a tasklist name is > 256 characters (not bytes)
                    
                    # The tasklist name may contain a suffix, which may push the final tasklist name over the limit
                    if len(tasklist_name) <= settings.MAX_TASKLIST_NAME_LEN:
                        # The base name is below the limit, so it is the suffix that is pushing the
                        # composite tasklist name over the limit
                        err_msg1 = "The tasklist name in data row " + str(self.num_data_rows) + \
                            ", when combined with the suffix, is too long. Maximum overall tasklist name length is " + str(settings.MAX_TASKLIST_NAME_LEN)
                    else:
                        err_msg1 = "The tasklist name in data row " + str(self.num_data_rows) + \
                            " is too long. Maximum length is " + str(settings.MAX_TASKLIST_NAME_LEN) + " characters"
                    err_msg2 = "Found " + str(len(tasklist_name)) + " character tasklist name on data row " + \
                        str(self.num_data_rows) + ":<br />" + shared.escape_html(unicode(tasklist_name))
                    err_msg3 = "If this does not look like your tasklist name, then your data file may be missing one or more double-quotes. Note that multi-line notes, and any field that contains commas, must be enclosed in double-quotes."
                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, err_msg3,
                        show_custom_button=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                        "<End> (Tasklist name too long)")
                    logservice.flush()
                    return False
                    
                # ---------------------
                #   Check depth value
                # ---------------------
                is_first_task_in_tasklist = (tasklist_name != prev_tasklist_name)
                result, depth, err_msg1, err_msg2 = check_task_values.depth_is_valid(task_row_data, self.num_data_rows, is_test_user, True, is_first_task_in_tasklist, prev_depth)
                
                if not result:
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + err_msg1 + ": " + err_msg2)
                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                        show_custom_button=True)
                    logging.info(fn_name + "<End> (Invalid depth value)")
                    logservice.flush()
                    return False
                
                            
                # -------------------------
                #   Check due date format
                # -------------------------
                date_due_str = task_row_data.get(u'due')
                if date_due_str:
                    test_due_date = import_tasks_shared.convert_datetime_string_to_RFC3339(
                        date_due_str, 'due', constants.DUE_DATE_FORMATS)
                    if not test_due_date:
                        # Unable to parse with any of our possible formats
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                            "Unable to parse '" + unicode(date_due_str) + 
                            "' as 'due' timestamp in data row " + str(self.num_data_rows))
                        logservice.flush()
                        err_msg1 = "Invalid 'due' date value &nbsp; '<span class='fixed-font'>" + unicode(date_due_str) + \
                            "</span>' &nbsp; in data row " + str(self.num_data_rows)
                        err_msg2 = "Supported formats %s or blank if no due date" % constants.DUE_DATE_FORMATS_DISPLAY
                        logging.debug(fn_name + "<End> (Invalid 'due' date format)")
                        logservice.flush()
                        import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                            show_custom_button=True)
                        return False
                
                # -------------------------------
                #   Check completed date format 
                # -------------------------------
                datetime_completed_str = task_row_data.get(u'completed')
                if datetime_completed_str:
                
                    test_completed_datetime = import_tasks_shared.convert_datetime_string_to_RFC3339(
                        datetime_completed_str, 'completed', constants.COMPLETED_DATETIME_FORMATS)
                    if not test_completed_datetime:
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                            "Unable to parse '" + unicode(datetime_completed_str) + 
                            "' in data row " + str(self.num_data_rows) + " as 'completed' timestamp")
                        logservice.flush()
                        err_msg1 = "Invalid 'completed' date value &nbsp; '<span class='fixed-font'>" + \
                            unicode(datetime_completed_str) + \
                            "</span>' &nbsp; in data row " + str(self.num_data_rows)
                        err_msg2 = "Supported formats; %s or blank if no completed date" % \
                            constants.COMPLETED_DATETIME_FORMATS_DISPLAY
                        logging.debug(fn_name + "<End> (Invalid 'completed' date format)")
                        logservice.flush()
                        import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2,
                            show_custom_button=True)
                        return False

                # ----------------------
                #   check status value
                # ----------------------
                status = task_row_data.get('status')
                if status not in ('completed', 'needsAction'):
                    try:
                        err_msg1 = "Invalid status value &nbsp; '<span class='fixed-font'>" + str(status) + \
                            "</span>' &nbsp; in data row " + str(self.num_data_rows)
                    except Exception, e: # pylint: disable=broad-except
                        err_msg1 = "Invalid status value in data row " + str(self.num_data_rows)
                    err_msg2 = 'The "status" value can only be "completed" or "needsAction"'
                    import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, show_custom_button=True)
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                        "<End> (Invalid 'status' value)")
                    logservice.flush()
                    return False
    
                # ----------------------
                #   Check hidden value
                # ----------------------
                # NOTE:
                #   From https://developers.google.com/google-apps/tasks/v1/reference/tasks#resource
                #   Retrieved 2012-11-17
                #       Flag indicating whether the task is hidden. 
                #       This is the case if the task had been marked completed when the task list was last cleared. 
                #       The default is False. This field is read-only.
                # The worker does not parse the 'hidden' field; it just passes it straight on to the server.
                # So, even though this field is ignored when importing, we still check the values, in case the
                # user enters an unexpected value which could cause the server to reject the insert.
                hidden = task_row_data.get('hidden')
                if hidden:
                    if not hidden in ['True', 'true', 'False', 'false']:
                        try:
                            err_msg1 = "Invalid 'hidden' value &nbsp; '<span class='fixed-font'>" + unicode(hidden) + \
                                "</span>' &nbsp; in data row " + str(self.num_data_rows)
                        except Exception, e: # pylint: disable=broad-except
                            err_msg1 = "Invalid 'hidden' value in data row " + str(self.num_data_rows)
                        err_msg2 = 'The "hidden" value can only be "True", "False" or blank'
                        import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                            show_custom_button=True)
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                            "<End> (Invalid 'hidden' value)")
                        logservice.flush()
                        return False
                        
                # -----------------------
                #   Check deleted value
                # -----------------------
                # The worker does not parse the 'deleted' field; it just passes it straight on to the server,
                # so we make sure here that it is a valid value. The server accepts 'true' and 'True' as True,
                # and all other values as False. However, we only allow 'False', 'false' or blank.
                # We don't allow other values through, because there is the potential for a user to enter
                # something such as 'Yes', which would be interpretted by the server as False!
                deleted = task_row_data.get('deleted')
                if deleted:
                    if not deleted in ['True', 'true', 'False', 'false']:
                        try:
                            err_msg1 = "Invalid 'deleted' value &nbsp; '<span class='fixed-font'>" + unicode(deleted) + \
                                "</span>' &nbsp; in data row " + str(self.num_data_rows)
                        except Exception, e: # pylint: disable=broad-except
                            err_msg1 = "Invalid 'deleted' value in data row " + str(self.num_data_rows)
                        err_msg2 = 'The "deleted" value can only be "True", "False" or blank'
                        import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                            show_custom_button=True)
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                            "<End> (Invalid 'deleted' value)")
                        logservice.flush()
                        return False
            
                prev_depth = depth
                prev_tasklist_name = tasklist_name
                
            # Allow file with no tasks, as it would allow a clever person to delete all their tasklists
            # (except default), by using a file with no tasks and choosing "Delete all tasklists before import"
            # import method
            # if self.num_data_rows < 1:
                # import_tasks_shared.serve_invalid_file_format_page(self, file_name, 
                #     "No tasks in file", show_custom_button=True)
                # logging.info(fn_name + "<End> (no tasks in file)")
                # logservice.flush()
                # return False
            
            logging.info(fn_name + "All " + str(self.num_data_rows) + " data rows in CSV file appears to be valid")
            logservice.flush()
            return True
            
        except Exception, e: # pylint: disable=broad-except
            # Invalid user files are generally caused by;
            #   incompatible encoding (i.e., not UTF-8 or ASCII), or 
            #   unsupported line endings (i.e., not using Windows-style CR/LF)
            # We check for those and only log as Info, because they are not program errors
            # Log any other types of errors as Exception, because they may be a program error
            
            # Log (num_data_rows + 1) because this exception usually occurs at
            #   for task_row_data in dict_reader
            # which is before the counter is incremented.
            err_msg1 = "Error processing CSV file at data row " + str(self.num_data_rows + 1)
            
            err_msg2 = shared.get_exception_msg(e)
            
            if isinstance(e, UnicodeDecodeError):
                # Log Unicode errors as warning, because that just indicates an incorrectly
                # encoded user uploaded file
                logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                    "Invalid content or layout at data row " + 
                    str(self.num_data_rows + 1) + ": " + shared.get_exception_msg(e))
            else:
                kwds = ["new-line", "newline", "line-feed", "linefeed"]
                err_msg_list = str(e).split(' ')
                if any([kwd in kwds for kwd in err_msg_list]):
                    # the csv reader supports LF or CR/LF line terminators, so this error 
                    # most likely means that the user has uploaded a file with Mac style CR line endings.
                    err_msg2 = constants.INVALID_LINE_TERMINATOR_MSG
                    logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + "Data row " + str(self.num_data_rows + 1) +
                        ", " + shared.get_exception_msg(e))
                else:
                    # Log any other error types as an exception to aid debugging. 
                    # User files shouldn't generate other types of errors.
                    logging.exception(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                        "Invalid content or layout at data row " + str(self.num_data_rows + 1))
            
            logging.debug(fn_name + "<End> (invalid content or layout)")
            logservice.flush()
            import_tasks_shared.serve_invalid_file_format_page(self, file_name, err_msg1, err_msg2, 
                constants.VALID_FILE_FORMAT_MSG, show_custom_button=True)
            return False
        
    

class ShowProgressHandler(webapp2.RequestHandler):
    """Handler to display progress to the user """
    
    @auth_decorator.oauth_required
    def get(self): # pylint: disable=too-many-statements,too-many-branches,too-many-locals
        """Display the progress page, which includes a refresh meta-tag to recall this page every n seconds"""
        fn_name = "ShowProgressHandler.get(): "
    
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            user = users.get_current_user()
            
            user_email = user.email()
            
            display_link_to_production_server = False # pylint: disable=invalid-name
            if not self.request.host in settings.PRODUCTION_SERVERS:
                # logging.debug(fn_name + "Running on limited-access server")
                if settings.DISPLAY_LINK_TO_PRODUCTION_SERVER:
                    display_link_to_production_server = True # pylint: disable=invalid-name
                if shared.is_test_user(user_email):
                    # Allow test user to see normal page
                    display_link_to_production_server = False # pylint: disable=invalid-name
                else:
                    logging.info("%sRejecting non-test user [%s] on limited access server %s",
                        fn_name, user_email, self.request.host)
                    logservice.flush()
                    shared.reject_non_test_user(self)
                    logging.debug(fn_name + "<End> (Non test user on limited access server)")
                    logservice.flush()
                    return
            
            job_msg = None
            error_message = None
            error_message_extra = None
            display_tasklist_suffix_msg = False
            
            
            # Retrieve the DB record for this user
            process_tasks_job = model.ImportTasksJobV1.get_by_key_name(user_email)
                
            if process_tasks_job is None:
                logging.error(fn_name + "No DB record for " + user_email)
                status = 'no-record'
                progress = 0
                job_start_timestamp = None
                error_message = None
                error_message_extra = None
                data_row_num = 0
                total_num_rows_to_process = 0
                job_start_timestamp = None
                # job_execution_time = None
                import_tasklist_suffix = None
                job_msg = None
                import_method = None
                is_paused = None
                pause_reason = None
                file_name = None
                used_default_tasklist = False
            else:            
                # total_progress is updated every settings.PROGRESS_UPDATE_INTERVAL seconds 
                status = process_tasks_job.status
                error_message = process_tasks_job.error_message
                error_message_extra = process_tasks_job.error_message_extra
                progress = process_tasks_job.total_progress
                data_row_num = process_tasks_job.data_row_num
                total_num_rows_to_process = process_tasks_job.total_num_rows_to_process
                job_start_timestamp = process_tasks_job.job_start_timestamp # UTC
                # job_execution_time = datetime.datetime.now() - job_start_timestamp
                import_tasklist_suffix = process_tasks_job.import_tasklist_suffix
                job_msg = process_tasks_job.message
                import_method = process_tasks_job.import_method
                is_paused = process_tasks_job.is_paused
                pause_reason = process_tasks_job.pause_reason
                file_name = process_tasks_job.file_name
                used_default_tasklist = process_tasks_job.used_default_tasklist
                
                if is_paused:
                    logging.warning(fn_name + "Job is paused because " + pause_reason + 
                        ", redirecting to " + settings.MAIN_PAGE_URL)
                    self.redirect(settings.MAIN_PAGE_URL)
                    logging.debug(fn_name + "<End> Paused job")
                    logservice.flush()
                    return
                else:
                    if _job_has_stalled(process_tasks_job):
                        status = constants.ImportJobStatus.STALLED
                        error_message = "Job appears to have stalled. Status was " + process_tasks_job.status
                        if process_tasks_job.error_message:
                            error_message_extra = "Previous error was " + process_tasks_job.error_message
                
                if import_tasklist_suffix:
                    if process_tasks_job.used_default_tasklist and process_tasks_job.num_tasklists == 1:
                        # Only imported into default tasklist, so don't display "Appended ... to tasklist name" message
                        display_tasklist_suffix_msg = False
                        
                    else:
                        # More than one tasklist, or single tasklist was not "@default"
                        display_tasklist_suffix_msg = True

                if process_tasks_job.used_default_tasklist and process_tasks_job.num_tasklists == 1:
                    # Only imported into default tasklist, so don't display import method
                    if process_tasks_job.import_method == constants.ImportMethod.DELETE_BEFORE_IMPORT:
                        import_method = "All tasks imported into default tasklist, and all other tasklists deleted"
                    else:
                        import_method = "All tasks imported into default tasklist"
                    
            # if status == constants.ImportJobStatus.IMPORT_COMPLETED:
                # logging.info(fn_name + "COMPLETED: Imported " + str(progress) + " tasks for " + user_email)
                # logservice.flush()
            # else:
                # logging.debug(fn_name + "Status = " + status + ", data row num = " + str(data_row_num) + 
                    # " for " + user_email + ", started at " + str(job_start_timestamp) + " UTC")
                # logservice.flush()
            # logging.debug(fn_name + "Import method = " + str(import_method))
            
            # display_separate_progress = False
            # if data_row_num > 0:
                # if abs(data_row_num - progress) <= 1:
                    # display_separate_progress = False
                    # logging.debug(fn_name + "Imported " + str(progress) + " of " + 
                        # str(total_num_rows_to_process) + " rows of data.")
                # else:
                    # display_separate_progress = True
                    # logging.debug(fn_name + "Processed " + str(data_row_num) + " of " + 
                        # str(total_num_rows_to_process) + " rows of data. Imported " + str(progress) + " tasks.")

            
            logging.debug(fn_name + "Status = '" + status + "' for " + user_email + ", started at " + 
                str(job_start_timestamp) + " UTC. Import method = '" + str(import_method) + "'")
            
            logging.debug(fn_name + "Processed " + str(data_row_num) + " of " + 
                str(total_num_rows_to_process) + " rows of data. Imported " + str(progress) + " tasks.")
            logservice.flush()            
            
            display_separate_progress = False
            if data_row_num > (progress + 1):
                # Only display 'progress' separately from 'data_row_num' if there is more than 1 difference.
                # This is because 'data_row_num' is incremented at the start of processing a data row,
                # whereas 'progress' is incremented only once the task has actually been inserted.
                # This means that threre is always a significant period during which 'data_row_num'
                # is one greater than 'progress'.
                display_separate_progress = True
                        
            if job_msg:
                logging.debug(fn_name + "Job message = '" + job_msg + "'")
                logservice.flush()
                
            is_invalid_format = False
            if error_message:
                if error_message.startswith(constants.INVALID_FORMAT_LOG_LABEL):
                    is_invalid_format = True
                    logging.info(fn_name + error_message)
                    if error_message_extra:
                        logging.info(fn_name + error_message_extra)
                else:
                    logging.warning(fn_name + "Error message: " + error_message)
                    if error_message_extra:
                        logging.warning(fn_name + "Extra error message: " + error_message_extra)
                logservice.flush()
                
                
            # We don't want to use the 'safe' filter in Django to allow HTML content in
            # error messages, in case that leads to an exploit. 
            # But we will split content on <br>
            # Can't split on newline, as newline in error_message raises
            #       BadValueError: Property error_message is not multi-line
            error_message_lines = []
            if '<br>' in error_message:
                # This is a multi-line error message
                error_message_lines = error_message.split('<br>')
                
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "progress.html")
            
            template_values = {'app_title' : host_settings.APP_TITLE,
                               'display_link_to_production_server' : display_link_to_production_server,
                               'production_server' : settings.PRODUCTION_SERVERS[0],
                               'host_msg' : host_settings.HOST_MSG,
                               'url_home_page' : settings.WELCOME_PAGE_URL,
                               'url_GTB' : settings.url_GTB,
                               'eot_executable_name' : settings.EOT_EXECUTABLE_NAME,
                               'outlook_instructions_url' : settings.OUTLOOK_INSTRUCTIONS_URL,
                               'product_name' : host_settings.PRODUCT_NAME,
                               'status' : status,
                               'is_paused' : is_paused,
                               'pause_reason' : pause_reason,
                               'display_separate_progress' : display_separate_progress,
                               'progress' : progress,
                               'data_row_num' : data_row_num,
                               'total_num_rows_to_process' : total_num_rows_to_process,
                               'file_name' : file_name,
                               'import_method' : import_method,
                               'import_tasklist_suffix' : import_tasklist_suffix,
                               'used_default_tasklist' : used_default_tasklist, 
                               'display_tasklist_suffix_msg' : display_tasklist_suffix_msg,
                               'job_msg' : job_msg,
                               'error_message' : error_message,
                               'error_message_lines' : error_message_lines, # Only set if error_message is multi-line 
                               'error_message_extra' : error_message_extra,
                               'is_invalid_format' : is_invalid_format,
                               'job_start_timestamp' : job_start_timestamp, # UTC
                               'refresh_interval' : settings.PROGRESS_PAGE_REFRESH_INTERVAL,
                               'user_email' : user_email,
                               'display_technical_options' : shared.is_test_user(user_email),
                               'url_main_page' : settings.MAIN_PAGE_URL,
                               'continue_job_url' : settings.CONTINUE_IMPORT_JOB_URL,
                               'PROGRESS_URL' : settings.PROGRESS_URL,
                               'msg': self.request.get('msg'),
                               'logout_url': users.create_logout_url(settings.WELCOME_PAGE_URL),
                               'STARTING' : constants.ImportJobStatus.STARTING,
                               'INITIALISING' : constants.ImportJobStatus.INITIALISING,
                               'IMPORTING' : constants.ImportJobStatus.IMPORTING,
                               'IMPORT_COMPLETED' : constants.ImportJobStatus.IMPORT_COMPLETED,
                               'DAILY_LIMIT_EXCEEDED' : constants.PauseReason.DAILY_LIMIT_EXCEEDED,
                               'USER_INTERRUPTED' : constants.PauseReason.USER_INTERRUPTED,
                               'ERROR' : constants.ImportJobStatus.ERROR,
                               'STALLED' : constants.ImportJobStatus.STALLED,
                               'url_discussion_group' : settings.url_discussion_group,
                               'email_discussion_group' : settings.email_discussion_group,
                               'SUPPORT_EMAIL_ADDRESS' : settings.SUPPORT_EMAIL_ADDRESS,
                               'url_issues_page' : settings.url_issues_page,
                               'url_source_code' : settings.url_source_code,
                               'app_version' : appversion.version,
                               'upload_timestamp' : appversion.upload_timestamp}
            self.response.out.write(template.render(path, template_values))
            
            if error_message:
                if error_message_extra:
                    shared.send_email_to_support("Progress - error msg to user", 
                        error_message + '\n' + error_message_extra,
                        job_start_timestamp=job_start_timestamp)
                else:
                    shared.send_email_to_support("Progress - error msg to user", 
                        error_message,
                        job_start_timestamp=job_start_timestamp)
                    
            logging.debug(fn_name + "<End>")
            logservice.flush()
            
            
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception")
            logservice.flush()

       
      
class GetNewBlobstoreUrlHandler(webapp2.RequestHandler):
    """ Provides a new Blobstore URL. 
    
        This is used when the user submits a file from an HTML form.
        We don't fill in a Blobstore URL when we build the page, because the URL can expire.
        
        The submitting page uses JavaScript to retrieve the Blobstore URL from this handler,
        and then sets the 'action' attribute of the form to that URL when the user clicks the 
        file upload button.
        
        Google provides a unique URL to allow the user to upload the contents of large files. 
        The file contents are then accessible to the applicion through a unique Blobstore key.
    """
    
    @auth_decorator.oauth_required
    def get(self):
        """ Return a new Blobstore URL, as a string """
        fn_name = "GetNewBlobstoreUrlHandler.get(): "
    
        # logging.debug(fn_name + "<Start>")
        # logservice.flush()
        
        upload_url = blobstore.create_upload_url(settings.BLOBSTORE_UPLOAD_URL)
        self.response.out.write(upload_url)
    
        logging.debug(fn_name + "upload_url = " + upload_url)
        # logging.debug(fn_name + "<End>")
        logservice.flush()


        
class RobotsHandler(webapp2.RequestHandler):
    """ Handle requests for robots.txt """
    
    def get(self):
        """If not running on production server, return robots.txt with disallow all
           to prevent search engines indexing test servers.
        """
        # Return contents of robots.txt
        self.response.out.write("User-agent: *\n")
        if self.request.host == settings.PRODUCTION_SERVERS[0]:
            logging.debug("Returning robots.txt with allow all")
            self.response.out.write("Disallow:\n")
        else:
            logging.debug("Returning robots.txt with disallow all")
            self.response.out.write("Disallow: /\n")
        

        
def _job_has_stalled(process_tasks_job):

    fn_name = "_job_has_stalled: "
        
    if not process_tasks_job.status in constants.ImportJobStatus.STOPPED_VALUES:
        # Check if the job has stalled (no progress timestamp updates)
        
        time_since_last_update = datetime.datetime.now() - process_tasks_job.job_progress_timestamp
        if time_since_last_update.seconds > settings.MAX_JOB_PROGRESS_INTERVAL:
            job_start_timestamp = process_tasks_job.job_start_timestamp # UTC
            time_since_start = datetime.datetime.now() - job_start_timestamp
            logging.error(fn_name + "Job appears to have stalled. Status = " + 
                process_tasks_job.status + ". Last job progress update was " + 
                str(time_since_last_update.seconds) +
                " seconds ago at " + str(process_tasks_job.job_progress_timestamp) + 
                " UTC. Job was started " + str(time_since_start.seconds) + 
                " seconds ago at " + str(job_start_timestamp) + " UTC")
            return True
            
    return False


app = webapp2.WSGIApplication( # pylint: disable=invalid-name,bad-whitespace
    [
        ("/robots.txt",                             RobotsHandler),
        (settings.WELCOME_PAGE_URL,                 WelcomeHandler),
        (settings.MAIN_PAGE_URL,                    MainHandler),
        (settings.GET_NEW_BLOBSTORE_URL,            GetNewBlobstoreUrlHandler),
        (settings.BLOBSTORE_UPLOAD_URL,             BlobstoreUploadHandler),
        (settings.PROGRESS_URL,                     ShowProgressHandler),
        (settings.CONTINUE_IMPORT_JOB_URL,          ContinueImportJob),
        (auth_decorator.callback_path,              auth_decorator.callback_handler()),
    ], debug=False)
