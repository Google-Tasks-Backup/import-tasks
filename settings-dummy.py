# Rename this file as settings.py and set the client ID and secrets values 
# according to the values from https://code.google.com/apis/console/

# Client IDs, secrets, user-agents and host-messages are indexed by host name, 
# to allow the application to be run on different hosts, (e.g., test and production),
# without having to change these values each time.

client_ids = { 'import-tasks.appspot.com'                  : '123456789012.apps.googleusercontent.com',
               'import-tasks-test.appspot.com'             : '987654321987.apps.googleusercontent.com',
               'localhost:8084'                            : '999999999999.apps.googleusercontent.com'}
                
client_secrets = { 'import-tasks.appspot.com'              : 'MyVerySecretKeyForProdSvr',
                   'import-tasks-test.appspot.com'         : 'MyVerySecretKeyForTestSvr',
                   'localhost:8084'                        : 'MyVerySecretKeyForLocalSvr'}
                
user_agents = { 'import-tasks.appspot.com'                 : 'import-tasks/1.0',
                'import-tasks-test.appspot.com'            : 'import-tasks-test/1.0',
                'localhost:8084'                           : 'import-tasks-local/1.0'}

# User agent value used if no entry found for specified host                
DEFAULT_USER_AGENT = 'import-tasks/2.0'

# This should match the "Application Title:" value set in "Application Settings" in the App Engine
# administration for the server that the app will be running on. This value is displyed in the app,
# but the value from the admin screen is "Displayed if users authenticate to use your application."
app_titles = {'import-tasks-test.appspot.com'              : "Test - Import Google Tasks", 
              'localhost:8084'                             : "Local - Import Google Tasks",
              'import-tasks.appspot.com'                   : "Import Google Tasks" }
                
# Title used when host name is not found, or not yet known
DEFAULT_APP_TITLE = "Import Google Tasks"

# According to the "Application Settings" admin page 
#   (e.g., https://appengine.google.com/settings?app_id=s~js-tasks&version_id=4.356816042992321979)
# "Application Title:" is "Displayed if users authenticate to use your application."
# However, the valiue that is shown under "Authorised Access" appears to be the value 
# set on the "API Access" page

# This is the value displayed under "Authorised Access to your Google Account"
# at https://www.google.com/accounts/IssuedAuthSubTokens
# The product name is set in the API Access page as "Product Name", at
# https://code.google.com/apis/console and is linked to the client ID
product_names = { '123456789012.apps.googleusercontent.com'    : "Import Google Tasks", 
                  '987654321987.apps.googleusercontent.com'    : "Import Tasks Test",
                  '999999999999.apps.googleusercontent.com'    : "GTB Local"}

# Product name used if no matching client ID found in product_names 
DEFAULT_PRODUCT_NAME = "Import Google Tasks"

# Host messages are optional
host_msgs = { 'import-tasks-test.appspot.com'              : "*** Running on test AppEngine server ***", 
              'localhost:8084'                             : "*** Running on local host ***",
              'import-tasks.appspot.com'                   : "Beta" }

url_discussion_group = "groups.google.com/group/import-tasks"

email_discussion_group = "import-tasks@googlegroups.com"

url_issues_page = "code.google.com/p/import-tasks/issues/list"

url_source_code = "code.google.com/p/import-tasks/source/browse/"

# URL to direct people to if they wish to backup their tasks
url_GTB = "tasks-backup.appspot.com"

# Must match name in queue.yaml
PROCESS_TASKS_REQUEST_QUEUE_NAME = 'import-tasks-request'

# The string used used with the params dictionary argument to the taskqueue, 
# used as the key to retrieve the value from the task queue
TASKS_QUEUE_KEY_NAME = 'user_email'

WELCOME_PAGE_URL = '/'

MAIN_PAGE_URL = '/main'

PROGRESS_URL = '/progress'

INVALID_CREDENTIALS_URL = '/invalidcredentials'

GET_NEW_BLOBSTORE_URL = '/getnewblobstoreurl'

BLOBSTORE_UPLOAD_URL = '/fileupload'

CONTINUE_IMPORT_JOB_URL = '/continue'

ADMIN_MANAGE_BLOBSTORE_URL = '/admin/blobstore/manage'

ADMIN_DELETE_BLOBSTORE_URL = '/admin/blobstore/delete'

ADMIN_BULK_DELETE_BLOBSTORE_URL = '/admin/blobstore/bulkdelete'

WORKER_URL = '/worker'

# Maximum number of consecutive authorisation requests
# Redirect user to Invalid Credentials page if there are more than this number of tries
MAX_NUM_AUTH_RETRIES = 3

# Number of times to try server actions
# Exceptions are usually due to DeadlineExceededError on individual API calls
# The first (NUM_API_TRIES - 2) retries are immediate. The app sleeps for 
# API_RETRY_SLEEP_DURATION seconds before trying the 2nd last and last retries.
NUM_API_TRIES = 4

# Number of seconds to sleep for the last 2 API retries
API_RETRY_SLEEP_DURATION = 45

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
MAX_JOB_PROGRESS_INTERVAL = 2 * API_RETRY_SLEEP_DURATION + NUM_API_TRIES * 10 + 30

# Update number of tasks in tasklist every TASK_COUNT_UPDATE_INTERVAL seconds
# This prevents excessive Datastore Write Operations which can exceed quota
TASK_COUNT_UPDATE_INTERVAL = 5

# Refresh progress page every PROGRESS_PAGE_REFRESH_INTERVAL seconds
PROGRESS_PAGE_REFRESH_INTERVAL = 6


# Auth count cookie expires after 'n' seconds. 
# That is, we count the number of authorisation attempts within 'n' seconds, and then we reset the count back to zero.
# This prevents the total number of authorisations over a user session being counted (and hence max exceeded) if the user has a vey long session
AUTH_RETRY_COUNT_COOKIE_EXPIRATION_TIME = 60

# ###############################################
#                  Debug settings
# ###############################################

# Extra detailed and/or personal details may be logged when user is one of the test accounts
TEST_ACCOUNTS = ["My.Email.Address@gmail.com", "Test.Email.Address@gmail.com"]


# When the app is running on one of these servers, users will be rejected unless they are in TEST_ACCOUNTS list
# If there is/are no limited-access servers, set this to an empty list []
LIMITED_ACCESS_SERVERS = []
#LIMITED_ACCESS_SERVERS = ['my-test-server.appspot.com']

# Logs dumps of raw data for test users when True
DUMP_DATA = False

