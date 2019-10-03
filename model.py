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
# Some parts based on work done by Dwight Guth's Google Tasks Porter

"""Classes to represent Task import job"""

from google.appengine.ext import db
from oauth2client.contrib import appengine

import constants


class Credentials(db.Model):
    """Represents the credentials of a particular user."""
    credentials = appengine.CredentialsProperty(indexed=False)



class ImportTasksJobV1(db.Model): # pylint: disable=too-many-instance-attributes
    """ Container used to pass User info to taskqueue task, and return tasks progress
        back to foreground process to be returned to the user.
    
        When creating an instance, the user's email address is used as the key
        
    """
    
    user = db.UserProperty(indexed=False)
    
    is_paused = db.BooleanProperty(indexed=False, default=False)
    
    # Set to true if this job is waiting to be continued.
    # If the job could not be completed withing the 10 minute limit, the worker sets
    # this value to True and places the job back on the taskqueue to be continued by another worker.
    # When a new worker takes on the job, the worker sets this to False.
    is_waiting_to_continue = db.BooleanProperty(indexed=False, default=False)
    
    pause_reason = db.StringProperty(indexed=False, default=constants.PauseReason.NONE)
    
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
    
    # Type of uploaded file. Only csv and gtbak are supported
    file_type = db.StringProperty(indexed=False, default=None)
    
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
    
    # Total number of tasks backed up. Used to display progress to user.
    total_progress = db.IntegerProperty(indexed=False, default=0) 
    
    # Current data file row number. Used to display progress to user.
    # This will be different from total_progress if any tasks have been skipped,
    # e.g. if user chooses 'Skip duplicate tasklists' import method
    data_row_num = db.IntegerProperty(indexed=False, default=0) 
    
    num_tasklists = db.IntegerProperty(indexed=False, default=0) 
    
    # When job progress was last updated. Used to ensure that backup job hasn't stalled
    job_progress_timestamp = db.DateTimeProperty(auto_now_add=True, indexed=False) 
    
    # 1st error message (human friendly)
    error_message = db.StringProperty(indexed=False, default='')

    # 2nd and subsequent error messages (technical detail)
    error_message_extra = db.StringProperty(indexed=False, default='')
    
    message = db.StringProperty(indexed=False, default='')
    
    # The cumulative amount of time spent running worker.py to processing this job.
    # This has to be cumulative, because an import job may span more than one run of worker.py, and runs
    # may have an unknown amount of non-processing time between runs (e.g., if a user later continues a paused job,
    # or there are other jobs in the taskqueue)
    total_processing_seconds = db.FloatProperty(indexed=False, default=0.0)

    # Set to true if tasks were imported into the @default tasklist
    used_default_tasklist = db.BooleanProperty(indexed=False, default=False)
    

    
class UsageStats(db.Model):
    """ Used to track GTB usage.
    
        user_hash and start_time together form a unique key
        
        In order for the stats to be useful, all 5 properties must be set.
    """
    
    # ID is used so that total number of retrievals, retrievals per user, and number of users can be determined 
    # To provide a measure of privacy, use hash to uniquely identify user, rather than the user's actual email address
    # Note that there is a very small probability that multiple users may be hashed to the same value
    user_hash  = db.IntegerProperty(indexed=True) # hash(user_email)
    
    # Datestamp indicating when the import job was initially started
    job_start_timestamp = db.DateTimeProperty(indexed=True) # from ImportTasksJob.job_start_timestamp
    
    # Number of tasks imported
    number_of_tasks = db.IntegerProperty(indexed=False)
    
    # Number of tasklists imported
    number_of_tasklists = db.IntegerProperty(indexed=False)
    
    # The cumulative amount of time spent running worker.py to processing this job.
    # This has to be cumulative, because an import job may span more than one run of worker.py, and runs
    # may have an unknown amount of non-processing time between runs (e.g., if a user later continues a paused job,
    # or there are other jobs in the taskqueue)
    total_processing_seconds = db.FloatProperty(indexed=False) # from ImportTasksJob.total_processing_seconds
    
    # Type of uploaded file. Only csv and gtbak are supported
    file_type = db.StringProperty(indexed=False, default=None) # from ImportTasksJob.file_type



# End of DB models
