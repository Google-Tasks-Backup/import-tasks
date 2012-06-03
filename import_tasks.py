#!/usr/bin/python2.5
#
# Copyright 2011 Google Inc.  All Rights Reserved.
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
# Original Google Tasks Porter, modified by Julie Smith 2012

"""Main web application handler for Import Google Tasks."""

# Orig __author__ = "dwightguth@google.com (Dwight Guth)"
__author__ = "julie.smith.1999@gmail.com (Julie Smith)"

from google.appengine.dist import use_library
use_library("django", "1.2")

import logging
import os
import pickle
import sys
import gc
import cgi

from apiclient import discovery
from apiclient.oauth2client import appengine
from apiclient.oauth2client import client

from google.appengine.api import mail
from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp import util
from google.appengine.runtime import apiproxy_errors
from google.appengine.runtime import DeadlineExceededError
from google.appengine.api import urlfetch_errors
from google.appengine.api import logservice # To flush logs
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers

logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True

import httplib2
import urllib
import Cookie

import datetime
from datetime import timedelta
import csv

import model
import settings
import appversion # appversion.version is set before the upload process to keep the version number consistent
import shared # Code whis is common between classes, or between import-tasks.py and worker.py
import constants


    
class WelcomeHandler(webapp.RequestHandler):
    """ Displays an introductory web page, explaining what the app does and providing link to authorise.
    
        This page can be viewed even if the user is not logged in.
    """

    def get(self):
        """ Handles GET requests for settings.WELCOME_PAGE_URL """

        fn_name = "WelcomeHandler.get(): "

        logging.debug(fn_name + "<Start> (app version %s)" %appversion.version )
        logservice.flush()
        
        try:
            client_id, client_secret, user_agent, app_title, product_name, host_msg = shared.get_settings(self.request.host)

            ok, user, credentials, fail_msg, fail_reason = shared.get_credentials(self)
            if not ok:
                is_authorized = False
                is_admin_user = False
            else:
                is_authorized = True
                is_admin_user = users.is_current_user_admin()
                
            user_email = None
            if user:
                user_email = user.email()
            
            template_values = {'app_title' : app_title,
                               'host_msg' : host_msg,
                               'url_home_page' : settings.MAIN_PAGE_URL,
                               'url_GTB' : settings.url_GTB,
                               'product_name' : product_name,
                               'is_authorized': is_authorized,
                               'is_admin_user' : is_admin_user,
                               'user_email' : user_email,
                               'url_main_page' : settings.MAIN_PAGE_URL,
                               'manage_blobstore_url' : settings.ADMIN_MANAGE_BLOBSTORE_URL,
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
                               'url_issues_page' : settings.url_issues_page,
                               'url_source_code' : settings.url_source_code,
                               'app_version' : appversion.version,
                               'upload_timestamp' : appversion.upload_timestamp}
                               
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "welcome.html")
            self.response.out.write(template.render(path, template_values))
            logging.debug(fn_name + "<End>" )
            logservice.flush()

        except shared.DailyLimitExceededError, e:
            logging.warning(fn_name + e.msg)
            # self.response.out.write(e.msg)
            msg1 = "Daily limit exceeded"
            msg2 = "This application is running on my personal AppSpot account, which is limited by Google to a total of 5000 task updates per day."
            msg3 = "The daily quota is reset at midnight Pacific Standard Time (5:00pm Australian Eastern Standard Time, 07:00 UTC). " + \
                "Please rerun any time after midnight PST to finish your import."
            shared.serve_message_page(msg1, msg2, msg3)
            logging.debug(fn_name + "<End> (Daily Limit Exceeded)")
            logservice.flush()
            
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
    
    
    
class MainHandler(webapp.RequestHandler):
    """ Handles Main web page, which provides user with option to start/continue an import job. """

    def get(self):
        """ Main page, once user has been authenticated """

        fn_name = "MainHandler.get(): "

        logging.debug(fn_name + "<Start> (app version %s)" %appversion.version )
        logservice.flush()
        
        try:
            client_id, client_secret, user_agent, app_title, product_name, host_msg = shared.get_settings(self.request.host)

            # Make sure that we can get the user's credentials before we allow them to start an import job
            ok, user, credentials, fail_msg, fail_reason = shared.get_credentials(self)
            if not ok:
                # User not logged in, or no or invalid credentials
                logging.info(fn_name + "Get credentials error: " + fail_msg)
                logservice.flush()
                # self.redirect(settings.WELCOME_PAGE_URL)
                shared.redirect_for_auth(self, user)
                return
                
            user_email = user.email()
            
            # Retrieve the DB record for this user
            process_tasks_job = model.ImportTasksJob.get_by_key_name(user_email)
            
            file_upload_time = None
            file_name = None
            job_status = None
            total_progress = 0
            total_num_rows_to_process = 0
            import_in_progress = False
            found_paused_job = False
            if process_tasks_job:
                job_status = process_tasks_job.status
                logging.debug(fn_name + "Retrieved import tasks job for " + str(user_email) +
                    ", status = " + str(job_status))
                logservice.flush()
                
                if job_status in constants.ImportJobStatus.PROGRESS_VALUES:
                    import_in_progress = True
                    logging.debug(fn_name + "Import job in progress")
                    logservice.flush()
                
                if process_tasks_job.is_paused:
                    found_paused_job = True
                    file_upload_time = process_tasks_job.file_upload_time
                    file_name = process_tasks_job.file_name
                    total_num_rows_to_process = process_tasks_job.total_num_rows_to_process
                    total_progress = process_tasks_job.total_progress
                    logging.debug(fn_name + "Found paused import job")
                    logservice.flush()

                # DEBUG
                # found_paused_job = True
                # file_upload_time = datetime.datetime.now()
                # file_name = "Dummy file name.csv"
                # total_num_rows_to_process = 8197
                # total_progress = 1943
                # logging.debug(fn_name + "DEBUG: Found paused import job")
                # logservice.flush()
            
            
            
            
            is_admin_user = users.is_current_user_admin()
            
            is_authorized = True
            if self.request.host in settings.LIMITED_ACCESS_SERVERS:
                logging.debug(fn_name + "Running on limited-access server")
                if not shared.isTestUser(user_email):
                    logging.info(fn_name + "Rejecting non-test user [" + str(user_email) + "] on limited access server")
                    self.response.out.write("<h2>This is a test server. Access is limited to test users.</h2>")
                    logging.debug(fn_name + "<End> (restricted access)" )
                    logservice.flush()
                    return

            
            
            # if shared.isTestUser(user_email):
                # logging.debug(fn_name + "Started by test user %s" % user_email)
                
                # try:
                    # headers = self.request.headers
                    # for k,v in headers.items():
                        # logging.debug(fn_name + "browser header: " + str(k) + " = " + str(v))
                        
                # except Exception, e:
                    # logging.exception(fn_name + "Exception retrieving request headers")
                    
              
            template_values = {'app_title' : app_title,
                               'host_msg' : host_msg,
                               'url_home_page' : settings.MAIN_PAGE_URL,
                               'new_blobstore_url' : settings.GET_NEW_BLOBSTORE_URL,
                               'product_name' : product_name,
                               'is_authorized': is_authorized,
                               'is_admin_user' : is_admin_user,
                               'job_status' : job_status,
                               'import_in_progress' : import_in_progress,
                               'found_paused_job' : found_paused_job,
                               'file_upload_time' : file_upload_time,
                               'total_num_rows_to_process' : total_num_rows_to_process,
                               'total_progress' : total_progress,
                               'file_name' : file_name,
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
                               'url_issues_page' : settings.url_issues_page,
                               'url_source_code' : settings.url_source_code,
                               'app_version' : appversion.version,
                               'upload_timestamp' : appversion.upload_timestamp}
                               
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "main.html")
            self.response.out.write(template.render(path, template_values))
            # logging.debug(fn_name + "Calling garbage collection")
            # gc.collect()
            logging.debug(fn_name + "<End>" )
            logservice.flush()
            
        except shared.DailyLimitExceededError, e:
            logging.warning(fn_name + e.msg)
            # self.response.out.write(e.msg)
            msg1 = "Daily limit exceeded"
            msg2 = "This application is running on my personal AppSpot account, which is limited by Google to a total of 5000 task updates per day."
            msg3 = "The daily quota is reset at midnight Pacific Standard Time (5:00pm Australian Eastern Standard Time, 07:00 UTC). " + \
                "Please rerun any time after midnight PST to finish your import."
            shared.serve_message_page(msg1, msg2, msg3)
            logging.debug(fn_name + "<End> (Daily Limit Exceeded)")
            logservice.flush()
                        
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
    
    

class ContinueImportJob(webapp.RequestHandler):

    def get(self):
        
        fn_name = "ContinueImportJob.get(): "
            
        logging.debug(fn_name + "<Start>")
        logservice.flush()
    
        # Check that job record exists, and is in correct state to continue
        try:
            # Only get the user here. The credentials are retrieved within send_job_to_worker()
            # Don't need to check if user is logged in, because all pages (except '/') are set as secure in app.yaml
            user = users.get_current_user()
            user_email = user.email()
            # Retrieve the import job record for this user
            process_tasks_job = model.ImportTasksJob.get_by_key_name(user_email)
            
            if process_tasks_job is None:
                logging.error(fn_name + "No DB record for " + user_email)
                shared.serve_message_page(self, "No import job found.",
                    "If you believe this to be an error, please report this at the link below, otherwise",
                    """<a href="/main">start an import</a>""")
                logging.warning(fn_name + "<End> No DB record")
                logservice.flush()
                return
            
            if not process_tasks_job.is_paused:
                shared.serve_message_page(self, "No paused import job found",
                    "If you believe this to be an error, please report this at the link below, otherwise",
                    "<a href=" + settings.MAIN_PAGE_URL + ">Go to main menu</a>")
                logging.warning(fn_name + "<End> Job is not paused. Status: " + str(process_tasks_job.status))
                logservice.flush()
                return
                
            # --------------------------------------------------------
            #       Add job to taskqueue for worker to process
            # --------------------------------------------------------
            # Worker will continue the paused job
            logging.debug(fn_name + "Continuing paused job")
            shared.send_job_to_worker(self, process_tasks_job)
            
            
            
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()

    
        logging.debug(fn_name + "<End>")
        logservice.flush()
        

    def post(self):
        
        fn_name = "ContinueImportJob.post(): "
            
        logging.debug(fn_name + "<Start>")
        logservice.flush()
    
        # Check that job record exists, and is in correct state to continue
        try:
            # Only get the user here. The credentials are retrieved within send_job_to_worker()
            # Don't need to check if user is logged in, because all pages (except '/') are set as secure in app.yaml
            user = users.get_current_user()
            user_email = user.email()
            # Retrieve the import job record for this user
            process_tasks_job = model.ImportTasksJob.get_by_key_name(user_email)
            
            if process_tasks_job is None:
                logging.error(fn_name + "No DB record for " + user_email)
                shared.serve_message_page(self, "No import job found.",
                    "If you believe this to be an error, please report this at the link below, otherwise",
                    """<a href="/main">start an import</a>""")
                logging.warning(fn_name + "<End> No DB record")
                logservice.flush()
                return
            
            if not process_tasks_job.is_paused:
                shared.serve_message_page(self, "No paused import job found",
                    "If you believe this to be an error, please report this at the link below, otherwise",
                    "<a href=" + settings.MAIN_PAGE_URL + ">Go to main menu</a>")
                logging.warning(fn_name + "<End> Job is not paused. Status: " + str(process_tasks_job.status))
                logservice.flush()
                return
            
            # Ensure that credentials are as fresh as possible before starting the worker
            # The auth handler calls GET on this URL, so the import job is initiated in the GET handler for this URL
            logging.debug(fn_name + "Refreshing auth")
            shared.redirect_for_auth(self, user)
            
            
            
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()

    
        logging.debug(fn_name + "<End>")
        logservice.flush()
        
        

class BlobstoreUploadHandler(blobstore_handlers.BlobstoreUploadHandler):
    """ Handle Blobstore uploads """
    
    def get(self):
        """ Handles redirect from authorisation.
        
            This can happen if retrieving credentials fails when handling the Blobstore upload.
            
            In that case, shared.redirect_for_auth() stores the URL for the BlobstoreUploadHandler()
            When OAuthCallbackHandler() redirects to here (on successful authorisation), it comes in as a GET
        """
        
        fn_name = "BlobstoreUploadHandler.get(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            # Only get the user here. The credentials are retrieved within send_job_to_worker()
            # Don't need to check if user is logged in, because all pages (except '/') are set as secure in app.yaml
            user = users.get_current_user()
            user_email = user.email()
            # Retrieve the import job record for this user
            process_tasks_job = model.ImportTasksJob.get_by_key_name(user_email)
            
            if process_tasks_job is None:
                logging.error(fn_name + "No DB record for " + user_email)
                shared.serve_message_page(self, "No import job found.",
                    "If you believe this to be an error, please report this at the link below, otherwise",
                    """<a href="/main">start an import</a>""")
                logging.warning(fn_name + "<End> No DB record")
                logservice.flush()
                return
            
            if process_tasks_job.status != constants.ImportJobStatus.STARTING:
                # The only time we should get here is if the credentials failed, and we were redirected after
                # successfully authorising. In that case, the jab status should still be STARTING
                shared.serve_message_page(self, "Invalid job status: " + str(process_tasks_job.status),
                    "Please report this error (see link below)")
                logging.warning(fn_name + "<End> Invalid job status: " + str(process_tasks_job.status))
                logservice.flush()
                return
                
            # --------------------------------------------------------
            #       Add job to taskqueue for worker to process
            # --------------------------------------------------------
            shared.send_job_to_worker(self, process_tasks_job)
            
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()

        logging.debug(fn_name + "<End>")
        logservice.flush()
            
            
    def post(self):
        """ Get the blob_info of the uploaded file, store the details of import job, and try to start the import job
        
            The Blobstore upload lands at the URL for the BlobstoreUploadHandler as a POST
        """
        
        fn_name = "BlobstoreUploadHandler.post(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            # Only get the user here, because if getting credentials fails here, then the upload fails
            # We get the credentials AFTER we've handled the upload!
            user = users.get_current_user()
        
            user_email = user.email()
            
            client_id, client_secret, user_agent, app_title, product_name, host_msg = shared.get_settings(self.request.host)

            upload_files = self.get_uploads('file') # 'file' is the name of the file upload field in the form
            if upload_files:
                blob_info = upload_files[0]
                blob_key = str(blob_info.key())
                logging.debug(fn_name + "key = " + blob_key + ", filename = " + str(blob_info.filename) +
                    ", for " + str(user_email))
                logservice.flush()
                
                if blob_info.size == 0:
                    err_msg = "Uploaded file is empty"
                    logging.info(fn_name + err_msg)
                    # Import process terminated, so delete the blobstore
                    shared.delete_blobstore(blob_info)
                    logging.debug(fn_name + "<End> due to empty upload file")
                    logservice.flush()
                    shared.serve_message_page(self, err_msg, show_custom_button=True)
                    return
                
                file_upload_time = blob_info.creation
                file_name = str(blob_info.filename)
                
                if shared.isTestUser(user_email):
                    logging.debug(fn_name + "Filename: '" + str(file_name) + "'")
                
                file_type = None
                if file_name.lower().endswith('.csv'):
                    file_type = 'csv'
                elif file_name.lower().endswith('.gtbak'):
                    file_type = 'gtbak'
                else:
                    # -----------------------------------------
                    #       Invalid file name extension
                    # -----------------------------------------
                    err_msg = str(file_name) + " is an unsupported file type. Only .csv or .GTBak is supported."
                    logging.info(fn_name + err_msg)
                    # Import process terminated, so delete the blobstore
                    shared.delete_blobstore(blob_info)
                    logging.debug(fn_name + "<End> (invalid file extension)")
                    logservice.flush()
                    shared.serve_message_page(self, err_msg, show_custom_button=True)
                    return
                
                logging.debug(fn_name + "Filetype: '" + str(file_type) + "'")
                logservice.flush()

                
                if file_type == 'csv':
                    # -----------------------------------------------
                    #       Check if CSV file format is valid
                    # -----------------------------------------------
                    try:
                        num_data_rows = 0
                        blob_reader = blobstore.BlobReader(blob_key)
                        csv_reader=csv.reader(blob_reader,dialect='excel')
                        
                        # -----------------------------
                        #       Check header row
                        # -----------------------------
                        header_row = csv_reader.next() # Header row
                        for c in header_row:
                            # Check that column names are correct
                            if c not in ("tasklist_name","title","notes","status","due","completed","deleted","hidden","depth"):
                                err_msg1 = "Error in uploaded file - invalid column name '" + c + "' in header row; " + str(header_row)
                                err_msg2 = """Header row must be: "tasklist_name","title","notes","status","due","completed","deleted","hidden","depth" """
                                logging.info(fn_name + err_msg1)
                                # Import process terminated, so delete the blobstore
                                shared.delete_blobstore(blob_info)
                                logging.debug(fn_name + "<End> due to invalid CSV header column names")
                                logservice.flush()
                                shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                                return
                                
                        if len(header_row) != 9:
                            err_msg1 = "Error in uploaded file - found " + len(header_row) + " columns in header row, expected 9; " + str(header_row)
                            err_msg2 = """Header row must be: "tasklist_name","title","notes","status","due","completed","deleted","hidden","depth" """
                            logging.info(fn_name + err_msg1)
                            # Import process terminated, so delete the blobstore
                            shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> due to invalid number of CSV header columns")
                            logservice.flush()
                            shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                            return
                        
                        # --------------------------------
                        #       Check first data row
                        # --------------------------------
                        first_data_row = csv_reader.next() # First data row
                        num_data_rows = 1
                        if len(first_data_row) != 9:
                            err_msg1 = "Error in uploaded file - found " + len(first_data_row) + " columns in first data row, expected 9; " + str(first_data_row)
                            err_msg2 = """Data rows must contain 9 columns: "tasklist_name","title","notes","status","due","completed","deleted","hidden",depth """
                            err_msg3 = """Values for "tasklist_name", "title", "status" and "depth" are mandatory. Other columns may be empty."""
                            logging.info(fn_name + err_msg1)
                            # Import process terminated, so delete the blobstore
                            shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> due to invalid number of CSV data columns")
                            logservice.flush()
                            shared.serve_message_page(self, err_msg1, err_msg2, err_msg3, show_custom_button=True)
                            return
                        
                        
                        # Check if depth value of first data row is valid (must be zero, as first task must be a root task)
                        err_msg = None
                        try:
                            depth = int(first_data_row[8])
                            if depth != 0:
                                # First task imported must have depth of zero; it must be a root task
                                err_msg = "Invalid depth [" + str(depth) + "] of first task; First task must have depth = 0"
                        except Exception, e:
                            err_msg = "Invalid depth value [" + str(task['depth']) + "] for first data row: " + str(e)
                        if err_msg:    
                            err_msg1 = "Unable to import file"
                            logging.info(fn_name + err_msg)
                            logservice.flush()
                            # Import process terminated, so delete the blobstore
                            shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> due to invalid depth value in CSV data")
                            logservice.flush()
                            shared.serve_message_page(self, err_msg1, err_msg, show_custom_button=True)
                            return 
                            
                            
                    except StopIteration:
                        err_msg1 = "Unable to import file"
                        err_msg2 = "Uploaded CSV file does not contain sufficient data"
                        logging.info(fn_name + err_msg2)
                        # Import process terminated, so delete the blobstore
                        shared.delete_blobstore(blob_info)
                        logging.debug(fn_name + "<End> due to insufficient data in CSV file" )
                        logservice.flush()
                        shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                        return

                    except Exception, e:
                        logging.exception(fn_name + "Error testing uploaded CSV file")
                        err_msg1 = "Error processing uploaded CSV file"
                        err_msg2 = shared.get_exception_msg(e)
                        # Import process terminated, so delete the blobstore
                        shared.delete_blobstore(blob_info)
                        logging.debug(fn_name + "<End> due to error parsing CSV file" )
                        logservice.flush()
                        shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                        return
                    
                    # Read the rest of the file so we know how many data rows there are
                    num_data_rows = 1 # We already read and checked the first data row
                    for data_row in csv_reader:
                        num_data_rows = num_data_rows + 1
                elif file_type == 'gtbak':
                    try:
                        # -----------------------------------------
                        #       Check if GTBak file is valid
                        # -----------------------------------------
                        minimum_element_list = [u'tasklist_name', u'title', u'status', u'depth']
                        num_data_rows = 0
                        blob_reader = blobstore.BlobReader(blob_key)
                        file_format_version = pickle.load(blob_reader)
                        if file_format_version != 'v1.0':
                            err_msg1 = "Unable to import file"
                            err_msg2 = "This file was created with an incompatible version of GTB"
                            logging.info(fn_name + err_msg2)
                            # Import process terminated, so delete the blobstore
                            shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> (invalid GTBak file format version)")
                            logservice.flush()
                            shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                            return
                        
                        tasks_data = pickle.load(blob_reader)
                        for task in tasks_data:
                            num_data_rows = num_data_rows + 1
                            # Check minimal requirements for each task; tasklist_name, title, status, depth
                            for k in minimum_element_list:
                                if not task.has_key(k):
                                    err_msg1 = "Unable to import file"
                                    err_msg2 = "Task " + str(num_data_rows) + " is missing '" + k + "'"
                                    logging.info(fn_name + err_msg2)
                                    # Import process terminated, so delete the blobstore
                                    shared.delete_blobstore(blob_info)
                                    logging.debug(fn_name + "<End> (missing task element in GTBak file)")
                                    logservice.flush()
                                    shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                                    return
                            if num_data_rows == 1:
                                # Depth of 1st task must be zero)
                                depth = int(task['depth'])
                                if depth != 0:
                                    # First task imported must have depth of zero; it must be a root task
                                    err_msg1 = "Unable to import file"
                                    err_msg2 = "Invalid depth [" + str(depth) + "] of first task; First task must have depth = 0"
                                    logging.info(fn_name + err_msg2)
                                    logservice.flush()
                                    # Import process terminated, so delete the blobstore
                                    shared.delete_blobstore(blob_info)
                                    logging.debug(fn_name + "<End> (non-zero depth of first task in GTBak file)")
                                    logservice.flush()
                                    shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                                    return                                 
                                
                        if num_data_rows == 0:
                            err_msg1 = "Unable to import file"
                            err_msg2 = "Import file contains no tasks"
                            logging.info(fn_name + err_msg2)
                            # Import process terminated, so delete the blobstore
                            shared.delete_blobstore(blob_info)
                            logging.debug(fn_name + "<End> (no tasks in GTBak file)")
                            logservice.flush()
                            shared.serve_message_page(self, err_msg1, err_msg2, show_custom_button=True)
                            return
                    except Exception, e:
                        err_msg1 = "Error reading import file"
                        err_msg2 = "The data is not in the correct GTBak format: " + shared.get_exception_msg(e)
                        logging.info(fn_name + err_msg2)
                        # Import process terminated, so delete the blobstore
                        shared.delete_blobstore(blob_info)
                        logging.debug(fn_name + "<End> (exception parsing GTBak file)")
                        logservice.flush()
                        shared.serve_message_page(self, err_msg, show_custom_button=True)
                        return

                logging.debug(fn_name + "Import data file (potentially) contains " + str(num_data_rows) + " tasks")
                    
                # Determine the chosen import method (and optional suffix)
                import_method = self.request.get('import_method') # How to process the import
                if import_method == constants.ImportMethod.APPEND_TIMESTAMP:
                    # Put space between tasklist name and timestamp
                    import_tasklist_suffix = " " + self.request.get('import_timestamp_suffix')
                elif import_method == constants.ImportMethod.USE_OWN_SUFFIX:
                    # Don't include spaces at the end. It is up to user to include space at the start.
                    import_tasklist_suffix = str(self.request.get('user_suffix')).rstrip()
                else:
                    import_tasklist_suffix = ''
                logging.debug(fn_name + "Import method: " + str(import_method) + ", tasklist suffix = '" + import_tasklist_suffix + "'")
                
                # Create a DB record, using the user's email address as the key
                # We store the job details here, but we add the credentials just before we add the
                # job to the taskqueue in send_job_to_worker(), so that the credentials are as fresh as possible.
                process_tasks_job = model.ImportTasksJob(key_name=user_email)
                process_tasks_job.user = user
                process_tasks_job.total_num_rows_to_process = num_data_rows
                process_tasks_job.blobstore_key = blob_key
                process_tasks_job.file_type = file_type
                process_tasks_job.file_name = file_name
                process_tasks_job.file_upload_time = file_upload_time
                process_tasks_job.import_tasklist_suffix = import_tasklist_suffix
                process_tasks_job.import_method = import_method
                process_tasks_job.status = constants.ImportJobStatus.STARTING
                process_tasks_job.put()

                # Forcing updated auth, so that worker has as much time as possible (i.e. one hour)
                # This is to combat situations where person authorises (e.g. when they start), but then does something
                # else for just under 1 hour before starting the import. In that case, auth expires during the (max) 10 minutes 
                # that the worker is running (causing AccessTokenRefreshError: invalid_grant)
                # After authorisation, this URL will be called again as a GET, so we start the import from the GET handler.
                logging.debug(fn_name + "Forcing auth, to get the freshest possible authorisation token")
                shared.redirect_for_auth(self, users.get_current_user())
                
                
                
                # # --------------------------------------------------------
                # #       Add job to taskqueue for worker to process
                # # --------------------------------------------------------
                # # Try to start the import job now.
                # # send_job_to_worker() will attempt to retrieve the user's credentials. If that fails, then
                # # this URL will be called again as a GET, and we retry send_job_to_worker() then
                # shared.send_job_to_worker(self, process_tasks_job)
    
            else:
                logging.debug(fn_name + "<End> due to no file uploaded" )
                logservice.flush()
                shared.serve_message_page(self, 'No file uploaded, please try again.', show_custom_button=True)
                
                
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()

        logging.debug(fn_name + "<End>")
        logservice.flush()
        
    

class ShowProgressHandler(webapp.RequestHandler):
    """Handler to display progress to the user """
    
    def get(self):
        """Display the progress page, which includes a refresh meta-tag to recall this page every n seconds"""
        fn_name = "ShowProgressHandler.get(): "
    
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            user = users.get_current_user()
            if not user:
                # User not logged in
                logging.info(fn_name + "No user information")
                logservice.flush()
                shared.redirect_for_auth(self, user)
                return
                
            client_id, client_secret, user_agent, app_title, product_name, host_msg = shared.get_settings(self.request.host)
          
            user_email = user.email()
            if self.request.host in settings.LIMITED_ACCESS_SERVERS:
                logging.debug(fn_name + "Running on limited-access server")
                if not shared.isTestUser(user_email):
                    logging.info(fn_name + "Rejecting non-test user on limited access server")
                    self.response.out.write("<h2>This is a test server. Access is limited to test users.</h2>")
                    logging.debug(fn_name + "<End> (restricted access)" )
                    logservice.flush()
                    return
            
            
            # Retrieve the DB record for this user
            process_tasks_job = model.ImportTasksJob.get_by_key_name(user_email)
                
            if process_tasks_job is None:
                logging.error(fn_name + "No DB record for " + user_email)
                status = 'no-record'
                progress = 0
                job_start_timestamp = None
            else:            
                # total_progress is updated every settings.TASK_COUNT_UPDATE_INTERVAL seconds 
                status = process_tasks_job.status
                error_message = process_tasks_job.error_message
                progress = process_tasks_job.total_progress
                total_num_rows_to_process = process_tasks_job.total_num_rows_to_process
                job_start_timestamp = process_tasks_job.job_start_timestamp
                job_execution_time = datetime.datetime.now() - job_start_timestamp
                import_tasklist_suffix = process_tasks_job.import_tasklist_suffix
                job_msg = process_tasks_job.message
                import_method = process_tasks_job.import_method
                is_paused = process_tasks_job.is_paused
                pause_reason = process_tasks_job.pause_reason
                
                if is_paused:
                    logging.error(fn_name + "Job is paused: " + pause_reason)
                else:
                    if not status in constants.ImportJobStatus.STOPPED_VALUES:
                        # Check if the job has stalled (no progress timestamp updates)
                        time_since_last_update = datetime.datetime.now() - process_tasks_job.job_progress_timestamp
                        if time_since_last_update.seconds > settings.MAX_JOB_PROGRESS_INTERVAL:
                            logging.error(fn_name + "Last job progress update was " + str(time_since_last_update.seconds) +
                                " seconds ago. Job appears to have stalled. Job was started " + str(job_execution_time.seconds) + 
                                " seconds ago at " + str(job_start_timestamp) + " UTC")
                            error_message = "Job appears to have stalled. Status was " + process_tasks_job.status
                            if process_tasks_job.error_message:
                                error_message = error_message + ", previous error was " + process_tasks_job.error_message
                            status = 'job_stalled'
            
            if status == constants.ImportJobStatus.IMPORT_COMPLETED:
                logging.info(fn_name + "Imported " + str(progress) + " tasks for " + str(user_email))
            else:
                logging.debug(fn_name + "Status = " + str(status) + ", progress = " + str(progress) + 
                    " for " + str(user_email) + ", started at " + str(job_start_timestamp) + " UTC")
            
            if error_message:
                logging.warning(fn_name + "Error message: " + str(error_message))
            
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "progress.html")
            
            template_values = {'app_title' : app_title,
                               'host_msg' : host_msg,
                               'url_home_page' : settings.WELCOME_PAGE_URL,
                               'product_name' : product_name,
                               'status' : status,
                               'is_paused' : is_paused,
                               'pause_reason' : pause_reason,
                               'progress' : progress,
                               'total_num_rows_to_process' : total_num_rows_to_process,
                               'import_method' : import_method,
                               'import_tasklist_suffix' : import_tasklist_suffix,
                               'job_msg' : job_msg,
                               'error_message' : error_message,
                               'job_start_timestamp' : job_start_timestamp,
                               'refresh_interval' : settings.PROGRESS_PAGE_REFRESH_INTERVAL,
                               'user_email' : user_email,
                               'display_technical_options' : shared.isTestUser(user_email),
                               'url_main_page' : settings.MAIN_PAGE_URL,
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
                               'url_discussion_group' : settings.url_discussion_group,
                               'email_discussion_group' : settings.email_discussion_group,
                               'url_issues_page' : settings.url_issues_page,
                               'url_source_code' : settings.url_source_code,
                               'app_version' : appversion.version,
                               'upload_timestamp' : appversion.upload_timestamp}
            self.response.out.write(template.render(path, template_values))
            logging.debug(fn_name + "<End>")
            logservice.flush()
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
        
        

class InvalidCredentialsHandler(webapp.RequestHandler):
    """Handler for /invalidcredentials"""

    def get(self):
        """Handles GET requests for /invalidcredentials"""

        fn_name = "InvalidCredentialsHandler.get(): "

        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            # DEBUG
            # if self.request.cookies.has_key('auth_retry_count'):
                # logging.debug(fn_name + "Cookie: auth_retry_count = " + str(self.request.cookies['auth_retry_count']))
            # else:
                # logging.debug(fn_name + "No auth_retry_count cookie found")
            # logservice.flush()            
                
            client_id, client_secret, user_agent, app_title, product_name, host_msg = shared.get_settings(self.request.host)
            
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "invalid_credentials.html")

            template_values = {  'app_title' : app_title,
                                 'app_version' : appversion.version,
                                 'upload_timestamp' : appversion.upload_timestamp,
                                 'rc' : self.request.get('rc'),
                                 'nr' : self.request.get('nr'),
                                 'err' : self.request.get('err'),
                                 'AUTH_RETRY_COUNT_COOKIE_EXPIRATION_TIME' : settings.AUTH_RETRY_COUNT_COOKIE_EXPIRATION_TIME,
                                 'host_msg' : host_msg,
                                 'url_main_page' : settings.MAIN_PAGE_URL,
                                 'url_home_page' : settings.WELCOME_PAGE_URL,
                                 'product_name' : product_name,
                                 'url_discussion_group' : settings.url_discussion_group,
                                 'email_discussion_group' : settings.email_discussion_group,
                                 'url_issues_page' : settings.url_issues_page,
                                 'url_source_code' : settings.url_source_code,
                                 'logout_url': users.create_logout_url(settings.WELCOME_PAGE_URL)}
                         
            self.response.out.write(template.render(path, template_values))
            logging.debug(fn_name + "<End>")
            logservice.flush()
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
       
       
      
class GetNewBlobstoreUrlHandler(webapp.RequestHandler):
    """ Provides a new Blobstore URL. 
        This is used when the user submits a file from an HTML form.
        We don't fill in a Blobstore URL when we build the page, because the URL can expire.
        
        Google provides a unique URL to allow the user to upload the contents of large files. 
        The file contents are then accessible to the applicion through a unique Blobstore key.
    """
    
    def get(self):
        """ Return a new Blobstore URL, as a string """
        upload_url = blobstore.create_upload_url(settings.BLOBSTORE_UPLOAD_URL)
        self.response.out.write(upload_url)


        
class BulkDeleteBlobstoreHandler(webapp.RequestHandler):
    """ List all blobstores, with option to delete each one """
    
    def post(self):
        """ Delete a selection of Blobstores (selected in form, and posted """
        fn_name = "BulkDeleteBlobstoreHandler.post(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            self.response.out.write('<html><body>')
            blobstores_to_delete = self.request.get_all('blob_key')
            del_count = 0
            for blob_key in blobstores_to_delete:
                blob_info = blobstore.BlobInfo.get(blob_key)
                
                if blob_info:
                    try:
                        blob_info.delete()
                        del_count = del_count + 1
                    except Exception, e:
                        logging.exception(fn_name + "Exception deleting blobstore [" + str(del_count) + "] " + str(blob_key))
                        self.response.out.write("""<div>Error deleting blobstore %s</div>%s""" % (blob_key, str(e)))
                else:
                    self.response.out.write("""<div>Blobstore %s doesn't exist</div>""" % blob_key)
                
            self.response.out.write('Deleted ' + str(del_count) + ' blobstores')
            self.response.out.write('<br /><br /><a href="' + settings.ADMIN_MANAGE_BLOBSTORE_URL + '">Back to Blobstore Management</a><br /><br />')
            self.response.out.write("""<br /><br /><a href=""" + settings.MAIN_PAGE_URL + """>Home page</a><br /><br />""")
            self.response.out.write('</body></html>')
            
            logging.debug(fn_name + "<End>")
            logservice.flush()
            
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
            return

    

class ManageBlobstoresHandler(webapp.RequestHandler):
    """ List all blobstores, with option to delete each one """
    
    def get(self):
        fn_name = "ManageBlobstoresHandler.post(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            self.response.out.write("""
                <html>
                    <head>
                        <title>Blobstore management</title>
                        <link rel="stylesheet" type="text/css" href="/static/tasks_backup.css" />
                        <script type="text/javascript">
                            function toggleCheckboxes(source) {
                                checkboxes = document.getElementsByName('blob_key');
                                for(var i in checkboxes)
                                    checkboxes[i].checked = source.checked;
                            }
                        </script>
                    </head>
                    <body>
                        <br />""")
            
            if blobstore.BlobInfo.all().count(1) > 0:
                # There is at least one BlobInfo in the blobstore
                sorted_blobstores = sorted(blobstore.BlobInfo.all(), key=lambda k: k.creation) 
                
                self.response.out.write('<form method="POST" action = "' + settings.ADMIN_BULK_DELETE_BLOBSTORE_URL + '">')
                self.response.out.write('<table cellpadding="5">')
                self.response.out.write('<tr><th>Filename</th><th>Upload timestamp (UTC)</th><th>Size</th><th>Type</th><th colspan="3">Actions</th></tr>')
                self.response.out.write('<tr><td colspan="4">')
                self.response.out.write('<td style="white-space: nowrap"><input type="checkbox" onClick="toggleCheckboxes(this);" /> Toggle All</td></tr>')

                #for b in blobstore.BlobInfo.all():
                for b in sorted_blobstores:
                    self.response.out.write('<tr>')
                    self.response.out.write('<td style="white-space: nowrap">' + str(b.filename) + 
                        '</td><td>' + str(b.creation) +
                        '</td><td>' + str(b.size) +
                        '</td><td>' + str(b.content_type) +
                        '</td><td><input type="checkbox" name="blob_key" value="' + str(b.key()) + '" ></td>')
                    self.response.out.write('</tr>')
                self.response.out.write('</table>')
                self.response.out.write("""<input type="submit" value="Delete selected blobstores" class="big-button" ></input>""")
                self.response.out.write('<form>')
            else:
                self.response.out.write("""<h4>No blobstores</h4>""")
            self.response.out.write("""<br /><br /><a href=""" + settings.MAIN_PAGE_URL + """>Home page</a><br /><br />""")
            self.response.out.write("""</body></html>""")
            
            logging.debug(fn_name + "<End>")
            logservice.flush()
            
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()



class DeleteBlobstoreHandler(blobstore_handlers.BlobstoreDownloadHandler):
    """ Delete specified blobstore """
    
    def get(self, blob_key):
        blob_key = str(urllib.unquote(blob_key))
        blob_info = blobstore.BlobInfo.get(blob_key)
        
        if blob_info:
            try:
                blob_info.delete()
                self.redirect(settings.MAIN_PAGE_URL)
                return
            except Exception, e:
                
                msg = """Error deleting blobstore %s<br />%s""" % (blob_key, str(e))
        else:
            msg = """Blobstore %s doesn't exist""" % blob_key
        
        self.response.out.write('<html><body>')
        self.response.out.write(msg)
        self.response.out.write('<br /><br /><a href="' + settings.MAIN_PAGE_URL + '">Home</a><br /><br />')
        self.response.out.write('</body></html>')

        

class AuthHandler(webapp.RequestHandler):
    """Handler for /auth."""

    def get(self):
        """Handles GET requests for /auth."""
        fn_name = "AuthHandler.get() "
        
        logging.debug(fn_name + "<Start>" )
        logservice.flush()
        
        try:
                
            ok, user, credentials, fail_msg, fail_reason = shared.get_credentials(self)
            if ok:
                if shared.isTestUser(user.email()):
                    logging.debug(fn_name + "Existing credentials for " + str(user.email()) + ", expires " + 
                        str(credentials.token_expiry ) + " UTC")
                else:
                    logging.debug(fn_name + "Existing credentials expire " + str(credentials.token_expiry) + " UTC")
                logging.debug(fn_name + "User is authorised. Redirecting to " + settings.MAIN_PAGE_URL)
                self.redirect(settings.MAIN_PAGE_URL)
            else:
                shared.redirect_for_auth(self, user)
            
            logging.debug(fn_name + "<End>" )
            logservice.flush()
            
        except shared.DailyLimitExceededError, e:
            logging.warning(fn_name + e.msg)
            # self.response.out.write(e.msg)
            msg1 = "Daily limit exceeded"
            msg2 = "This application is running on my personal AppSpot account, which is limited by Google to a total of 5000 task updates per day."
            msg3 = "The daily quota is reset at midnight Pacific Standard Time (5:00pm Australian Eastern Standard Time, 07:00 UTC). " + \
                "Please rerun any time after midnight PST to finish your import."
            shared.serve_message_page(msg1, msg2, msg3)
            logging.debug(fn_name + "<End> (Daily Limit Exceeded)")
            logservice.flush()
            
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()

            
    
class OAuthCallbackHandler(webapp.RequestHandler):
    """Handler for /oauth2callback."""

    # TODO: Simplify - Compare with orig in GTP
    def get(self):
        """Handles GET requests for /oauth2callback."""
        
        fn_name = "OAuthCallbackHandler.get() "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            if not self.request.get("code"):
                logging.debug(fn_name + "No 'code', so redirecting to " + str(settings.WELCOME_PAGE_URL))
                logservice.flush()
                self.redirect(settings.WELCOME_PAGE_URL)
                logging.debug(fn_name + "<End> (no code)")
                logservice.flush()
                return
                
            user = users.get_current_user()
            logging.debug(fn_name + "Retrieving flow for " + str(user.user_id()))
            flow = pickle.loads(memcache.get(user.user_id()))
            if flow:
                logging.debug(fn_name + "Got flow. Retrieving credentials")
                error = False
                retry_count = settings.NUM_API_TRIES
                while retry_count > 0:
                    retry_count = retry_count - 1
                    try:
                        credentials = flow.step2_exchange(self.request.params)
                        # Success!
                        error = False
                        
                        if shared.isTestUser(user.email()):
                            logging.debug(fn_name + "Retrieved credentials for " + str(user.email()) + ", expires " + 
                                str(credentials.token_expiry) + " UTC")
                        else:    
                            logging.debug(fn_name + "Retrieved credentials, expires " + str(credentials.token_expiry) + " UTC")
                        
                        break
                        
                    except client.FlowExchangeError, e:
                        logging.warning(fn_name + "FlowExchangeError " + str(e))
                        error = True
                        credentials = None
                        break
                        
                    except Exception, e:
                        error = True
                        credentials = None
                        
                    if retry_count > 0:
                        logging.info(fn_name + "Error retrieving credentials. " + 
                                str(retry_count) + " retries remaining: " + shared.get_exception_msg(e))
                        logservice.flush()
                    else:
                        logging.exception(fn_name + "Unable to retrieve credentials after 3 retries. Giving up")
                        logservice.flush()
                            
                    
                appengine.StorageByKeyName(
                    model.Credentials, user.user_id(), "credentials").put(credentials)
                    
                if error:
                    # TODO: Redirect to retry or invalid_credentials page, with more meaningful message
                    logging.warning(fn_name + "Error retrieving credentials from flow. Redirecting to " + settings.WELCOME_PAGE_URL + "'/?msg=ACCOUNT_ERROR'")
                    logservice.flush()
                    self.redirect(settings.WELCOME_PAGE_URL + "/?msg=ACCOUNT_ERROR")
                    logging.debug(fn_name + "<End> (Error retrieving credentials)")
                    logservice.flush()
                else:
                    # Redirect to the URL stored in the "state" param, when shared.redirect_for_auth was called
                    # This should be the URL that the user was on when authorisation failed
                    logging.debug(fn_name + "Success. Redirecting to " + str(self.request.get("state")))
                    self.redirect(self.request.get("state"))
                    logging.debug(fn_name + "<End>")
                    logservice.flush()
                        
        except Exception, e:
            logging.exception(fn_name + "Caught top-level exception")
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/import-tasks/issues/list">code.google.com/p/import-tasks/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()

        

def real_main():
    logging.debug("main(): Starting import-tasks (app version %s)" %appversion.version)
    template.register_template_library("common.customdjango")

    application = webapp.WSGIApplication(
        [
            (settings.MAIN_PAGE_URL,                    MainHandler),
            (settings.WELCOME_PAGE_URL,                 WelcomeHandler),
            (settings.PROGRESS_URL,                     ShowProgressHandler),
            (settings.INVALID_CREDENTIALS_URL,          InvalidCredentialsHandler),
            (settings.GET_NEW_BLOBSTORE_URL,            GetNewBlobstoreUrlHandler),
            (settings.BLOBSTORE_UPLOAD_URL,             BlobstoreUploadHandler),
            (settings.ADMIN_MANAGE_BLOBSTORE_URL,       ManageBlobstoresHandler),
            (settings.ADMIN_DELETE_BLOBSTORE_URL,       DeleteBlobstoreHandler),
            (settings.ADMIN_BULK_DELETE_BLOBSTORE_URL,  BulkDeleteBlobstoreHandler),
            (settings.CONTINUE_IMPORT_JOB_URL,          ContinueImportJob),
            ("/auth",                                   AuthHandler),
            # '/oauth2callback' is specified "API Access" on the page at https://code.google.com/apis/console/
            ("/oauth2callback",                         OAuthCallbackHandler), 
        ], debug=False)
    util.run_wsgi_app(application)
    logging.debug("main(): <End>")

def profile_main():
    # From https://developers.google.com/appengine/kb/commontasks#profiling
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

if __name__ == "__main__":
    main()
