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
# Modified by Julie Smith, 2012

"""Classes to represent Task import job"""

from apiclient.oauth2client import appengine

from google.appengine.ext import db
import datetime

import constants


class Credentials(db.Model):
  """Represents the credentials of a particular user."""
  credentials = appengine.CredentialsProperty(indexed=False)



class ImportTasksJob(db.Model):
    """ Container used to pass User info (including credentials) to taskqueue task, and return tasks progress
        back to foreground process to be returned to the user.
    
        When creating an instance, the user's email address is used as the key
        
    """
    
    user = db.UserProperty(indexed=False)
    
    is_paused = db.BooleanProperty(indexed=False, default=False)
    
    pause_reason = db.StringProperty(indexed=False, choices=(constants.PauseReason.ALL_VALUES), default=constants.PauseReason.NONE)
    
    # Blobstore key for import job. The key is retrievable in the uploadhandler (i.e., when user uploads file)
    #       class UploadHandler(blobstore_handlers.BlobstoreUploadHandler):
    #           def post(self):
    #               upload_files = self.get_uploads('file') # Assuming form uses <input type="file" name="file">
    #               blobstore_info = upload_files[0]
    #               blobstore_key = blobstore_info.key
    # CAUTION: The Blobstore is global across the app. The only way to tie a Blobstore to a user is through this Model!
    blobstore_key = db.StringProperty(indexed=False, default=None)
    
    # Name of the user's uploaded file. Used to display file details to user when giving the user the option to continue a job
    file_name = db.StringProperty(indexed=False, default=None)
    
    # Time that the user uploaded the file. Used to display file details to user when giving the user the option to continue a job
    file_upload_time = db.DateTimeProperty(indexed=False, auto_now_add=True)
    
    # The suffix is added to imported tasklist names to ensure unique tasklist names
    import_tasklist_suffix = db.StringProperty(indexed=False, default='')
    
    # Specifies how to handle the data import
    import_method = db.StringProperty(indexed=False, choices=(constants.ImportMethod.ALL_VALUES), 
        default=constants.ImportMethod.APPEND_TIMESTAMP)
    
    # Total number of rows to process
    total_num_rows_to_process = db.IntegerProperty(indexed=False, default=0)
    
    # Holds a pickled version of the import job's stae (i.e., tasklists, siblings, prev_sibling, etc)
    pickled_import_state = db.BlobProperty(indexed=False, default=None)
    
    # Set automatically when entity is created. Indicates when job was started (i.e., snapshot time)
    # Also used to track if task exceeds maximum allowed, so we can display message to user
    job_start_timestamp = db.DateTimeProperty(indexed=False, auto_now_add=True)
    
    # Job status, to display to user and control web page and foreground app behaviour
    # status = db.StringProperty(indexed=False, choices=('starting', 'initialising', 'building', 'completed', 'importing', 'import_completed', 'error'), default='starting')
    status = db.StringProperty(indexed=False, choices=(constants.ImportJobStatus.ALL_VALUES), default=constants.ImportJobStatus.STARTING)
    
    # Total number of tasks backed up. Used to display progress to user. Updated when an entire tasklist has been backed up
    total_progress = db.IntegerProperty(indexed=False, default=0) 
    
    # When job progress was last updated. Used to ensure that backup job hasn't stalled
    job_progress_timestamp = db.DateTimeProperty(auto_now_add=True, indexed=False) 
    
    error_message = db.StringProperty(indexed=False, default='')

    message = db.StringProperty(indexed=False, default='')


