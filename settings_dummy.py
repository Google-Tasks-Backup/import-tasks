#!/usr/bin/python2.5
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

# Rename this file as settings.py and set the URLs and email addresses
# in the "APP-SPECIFIC SETTINGS" and "DEBUG SETTINGS" sections

# Rename this file as 'settings.py' and set appropriate values for
#   MY-APP-ID
#   MY-OTHER-APP-ID
#   MY-GROUP-NAME
#   MY.EMAIL.ADDRESS
#   TEST.EMAIL.ADDRESS


# ============================================
#     +++ Change these settings: Start +++
# ============================================

# ---------------------------------
#   APP-SPECIFIC SETTINGS: Start
# ---------------------------------
url_discussion_group = "groups.google.com/group/MY-GROUP-NAME" # pylint: disable=invalid-name

email_discussion_group = "MY-GROUP-NAME@googlegroups.com" # pylint: disable=invalid-name

url_issues_page = "code.google.com/p/MY-APP-ID/issues/list" # pylint: disable=invalid-name

url_source_code = "code.google.com/p/MY-APP-ID/source/browse/" # pylint: disable=invalid-name

# URL to direct people to if they wish to backup their tasks
url_GTB = "MY-OTHER-APP-ID.appspot.com" # pylint: disable=invalid-name

# Email address to which critical errors are emailed
# If blank, no email will be sent
SUPPORT_EMAIL_ADDRESS = ""

# -------------------------------
#   APP-SPECIFIC SETTINGS: End
# -------------------------------

# ---------------------------
#    DEBUG SETTINGS: Start
# ---------------------------

# Extra detailed and/or personal details may be logged when user is one of the test accounts
TEST_ACCOUNTS = ["MY.EMAIL.ADDRESS@gmail.com", "TEST.EMAIL.ADDRESS@gmail.com"]

# List of hostname(s) of the production server(s). Used to identify if app is running on production server.
# The first in the list is used as the URL to direct user to production server.
# This is primarily used when testing GTI on a non-production server, but we want to see what it
# would look like on the production server. It is also used when using a subdomain of the main server,
# e.g. test1.import-tasks.appspot.com
PRODUCTION_SERVERS = ['MY-APP-ID.appspot.com', 'test.MY-APP-ID.appspot.com']

# If True, and app is not running on one of the PRODUCTION_SERVERS, display message to user which includes
# link to the production server, and do not display any active content to the user.
DISPLAY_LINK_TO_PRODUCTION_SERVER = True



# Logs dumps of raw data for test users when True
DUMP_DATA = False

# -------------------------
#    DEBUG SETTINGS: End
# -------------------------

# ==========================================
#     --- Change these settings: End ---
# ==========================================


# Must match name in queue.yaml
PROCESS_TASKS_REQUEST_QUEUE_NAME = 'import-tasks-request'

# The string used used with the params dictionary argument to the taskqueue, 
# used as the key to retrieve the value from the task queue
TASKS_QUEUE_KEY_NAME = 'user_email'

WELCOME_PAGE_URL = '/'

MAIN_PAGE_URL = '/main'

PROGRESS_URL = '/progress'

GET_NEW_BLOBSTORE_URL = '/getnewblobstoreurl'

BLOBSTORE_UPLOAD_URL = '/fileupload'

CONTINUE_IMPORT_JOB_URL = '/continue'

ADMIN_MANAGE_BLOBSTORE_URL = '/admin/blobstore/manage'

ADMIN_DELETE_BLOBSTORE_URL = '/admin/blobstore/delete'

ADMIN_BULK_DELETE_BLOBSTORE_URL = '/admin/blobstore/bulkdelete'

OUTLOOK_INSTRUCTIONS_URL = '/static/instructions_import_from_outlook.html'

EOT_EXECUTABLE_NAME = 'ExportOutlookTasks.exe'

WORKER_URL = '/worker'


# The maximum length, in characters (not bytes) of the tasklist name
# 2013-06-04; Attempting to create a tasklist with a length greater than 256 returns HTTP Error 400 "Invalid value"
MAX_TASKLIST_NAME_LEN = 256


# The number of seconds to wait before a URL fetch times out.
# The default GAE timeout is 5 seconds, but that results in significant numbers
# of DeadlineExceededError, especially when retrieving credentials.
# Hopefully, increasing the timeout will reduce the number of DeadlineExceededError
#   Used as a parameter to fetch_with_deadline()
URL_FETCH_TIMEOUT = 10


# Number of times to try server actions
# Exceptions are usually due to DeadlineExceededError on individual API calls
# The first (NUM_API_TRIES - 2) retries are immediate. The app sleeps for 
# API_RETRY_SLEEP_DURATION seconds before trying the 2nd last and last retries.
NUM_API_TRIES = 4

# Number of seconds to sleep for the last 2 API retries
API_RETRY_SLEEP_DURATION = 45
# API_RETRY_SLEEP_DURATION = 5 # DEBUG:


# Number of seconds to sleep for the last 2 API retries for frontend (client-facing) apps.
# This is lower than API_RETRY_SLEEP_DURATION, because frontent must completed within 60 seconds.
FRONTEND_API_RETRY_SLEEP_DURATION = 18

# Number of seconds to sleep for a "quota exceeded" error
# Google has a "limit per user per 100 seconds", so we wait at least 100 seconds
QUOTA_EXCEEDED_API_RETRY_SLEEP_DURATION = 120



# If the import hasn't finished within MAX_WORKER_RUN_TIME seconds, we stop the current import run, and then
# add the job to the taskqueue to continue the import process in a new worker.
# We allow 8 minutes (480 seconds), to allow for 2 x API_RETRY_SLEEP_DURATION second retries on DeadlineExceededError, 
# and to give some margin, from the maximum allowed 10 minutes.
#MAX_WORKER_RUN_TIME = 480
MAX_WORKER_RUN_TIME = 600 - 2 * API_RETRY_SLEEP_DURATION - 30
#MAX_WORKER_RUN_TIME = 20 # DEBUG

# If the job hasn't been updated in MAX_JOB_PROGRESS_INTERVAL seconds, assume that the job has stalled, 
# and display error message and stop refreshing progress.html
# - Longest observed time between job added to taskqueue, and worker starting, is 89.5 seconds
# - Max time between updates would be when API fails multiple times, when we sleep API_RETRY_SLEEP_DURATION seconds
#   for the last 2 retries + 10 seconds per API access
#MAX_JOB_PROGRESS_INTERVAL = 2 * API_RETRY_SLEEP_DURATION + NUM_API_TRIES * 10 + 30
MAX_JOB_PROGRESS_INTERVAL = 2 * API_RETRY_SLEEP_DURATION + NUM_API_TRIES * 10 + 30

# Update number of tasks in tasklist every PROGRESS_UPDATE_INTERVAL seconds
# This prevents excessive Datastore Write Operations which can quickly exceed quota
PROGRESS_UPDATE_INTERVAL = 5

# Refresh progress page every PROGRESS_PAGE_REFRESH_INTERVAL seconds
PROGRESS_PAGE_REFRESH_INTERVAL = 6
