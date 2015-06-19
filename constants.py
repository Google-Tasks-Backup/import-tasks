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

# Portions based on Dwight Guth's Google Tasks Porter

# This module contains any value, especially strings, that is referenced in more than one location.


class ImportJobStatus(object):
    # CAUTION: The Django progress.html template uses string literals when checking the status value. 
    # If these values are changed, then the progress.html must also be changed
    STARTING = 'Starting' # Job has been created (request places on task queue)
    INITIALISING = 'Initialising' # Pre-job initialisation (e.g., retrieving credentials)
    IMPORTING = 'Importing'
    IMPORT_COMPLETED = 'Import completed'
    ERROR = 'Error'
    STALLED = 'Stalled'
    
    ALL_VALUES = [STARTING, INITIALISING, IMPORTING, IMPORT_COMPLETED, ERROR]
    PROGRESS_VALUES = [STARTING, INITIALISING, IMPORTING]
    STOPPED_VALUES = [IMPORT_COMPLETED, ERROR, STALLED]

    
class ImportMethod(object):
    # These constants are used in many places, including;
    #       model.py
    #           ImportTasksJobV1.import_method
    #       welcome.html
    #       main.html
    #       import_tasks.py
    #       worker.py
    APPEND_TIMESTAMP = "Append timestamp to tasklist name"
    USE_OWN_SUFFIX = "Append own suffix"
    IMPORT_AS_IS = "Create new tasklists"
    ADD_TO_EXISTING_TASKLIST = "Add tasks to existing tasklists"
    REPLACE_TASKLIST_CONTENT = "Replace tasklist contents"
    SKIP_DUPLICATE_TASKLIST = "Skip duplicate tasklists"
    DELETE_BEFORE_IMPORT = "Delete all tasklists before import"
    
    ALL_VALUES = [APPEND_TIMESTAMP, USE_OWN_SUFFIX, IMPORT_AS_IS, ADD_TO_EXISTING_TASKLIST, REPLACE_TASKLIST_CONTENT, SKIP_DUPLICATE_TASKLIST, DELETE_BEFORE_IMPORT]
    
    # These methods require the tasklist suffix value to be appended to new tasklist names
    USE_SUFFIX_VALUES = [APPEND_TIMESTAMP, USE_OWN_SUFFIX]
    
    # These methods require the list of existing tasklists to be retrieved and stored
    RETRIEVE_EXISTING_TASKLISTS_VALUES = [ADD_TO_EXISTING_TASKLIST, REPLACE_TASKLIST_CONTENT, SKIP_DUPLICATE_TASKLIST]
    
    # Any of these methods require a new tasklist to be created
    CREATE_NEW_TASKLIST_VALUES = [APPEND_TIMESTAMP, USE_OWN_SUFFIX, IMPORT_AS_IS, REPLACE_TASKLIST_CONTENT, DELETE_BEFORE_IMPORT, SKIP_DUPLICATE_TASKLIST]
    
    
class PauseReason(object):
    NONE = ''
    DAILY_LIMIT_EXCEEDED = "daily limit was exceeded"
    USER_INTERRUPTED = "user interrupted the import job"
    JOB_STALLED = "import job stalled"
    
    ALL_VALUES = [NONE, DAILY_LIMIT_EXCEEDED, USER_INTERRUPTED, JOB_STALLED]
    
    
# Max blob size is just under 1MB (~2^20), so use 1000000 to allow some margin for overheads
MAX_BLOB_SIZE = 1000000


# Name of folder containg templates. Do not include path separator characters, as they are inserted by os.path.join()
PATH_TO_TEMPLATES = "templates"


# For parsing using strptime()
# Dashes are used as date separator by GTB, but many Europeans use period, so support both.
# Don't allow slash, because Americans who tend to use slash often reverse month and day, which
# results in a potentially ambiguous date.
COMPLETED_DATETIME_FORMATS = ["UTC %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "UTC %Y-%m-%d", "%Y-%m-%d",
    "UTC %Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M:%S", "UTC %Y.%m.%d", "%Y.%m.%d"]
# For display to user
COMPLETED_DATETIME_FORMATS_DISPLAY = '"UTC yyyy-mm-dd HH:MM:SS", "yyyy-mm-dd HH:MM:SS", "UTC yyyy-mm-dd" and "yyyy-mm-dd"'

#DUE_DATE_FORMATS = ["UTC %Y-%m-%d", "%Y-%m-%d", "UTC %Y.%m.%d", "%Y.%m.%d"]
DUE_DATE_FORMATS = ["UTC %Y-%m-%d", "%Y-%m-%d", "UTC %Y.%m.%d", "%Y.%m.%d",
    "UTC %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "UTC %Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"]
DUE_DATE_FORMATS_DISPLAY = '"UTC yyyy-mm-dd", "yyyy-mm-dd"'

# Sometimes the server returns an RFC-3339 timestamp as '0000-01-01T00:00:00.000Z', which cannot be converted
# to a datetime object, so we handle that separately. Zero-datetime is stored in files from GTB as 
# '0000-01-01 00:00:00' for 'completed' or '0000-01-01' for 'due'.
ZERO_RFC3339_DATETIME_STRING = '0000-01-01T00:00:00.000Z'

# This is how the ZERO_RFC3339_DATETIME_STRING for datetime fields such as 'completed' is stored in files from GTB
ZERO_DATETIME_STRING = '0000-01-01 00:00:00'

# This is how the ZERO_RFC3339_DATETIME_STRING for date-only fields such as 'due' is stored in files from GTB
ZERO_DATE_STRING = '0000-01-01'

# This is a list of the possible ways that zero-date may be stored in the import file,
# allowing additional formats for files that users may have edited themselves.
ZERO_DATETIME_STRINGS = [
    '0000-01-01 00:00:00', '0000-01-01',
    'UTC 0000-01-01 00:00:00', 'UTC 0000-01-01',
    '0000.01.01 00:00:00', '0000.01.01',
    'UTC 0000.01.01 00:00:00', 'UTC 0000.01.01' ]

    
# All possible columns, used when using GTB Import/Export CSV format
FULL_CSV_ELEMENT_LIST = ['tasklist_name', 'title', 'notes', 'status', 'due', 'completed', 'deleted', 'hidden', 'depth']

# The minimum GTBak elements required to recreate a task
#   GTBak should always have depth (because it was exported by GTB)
#   CSV may omit depth; worker will default to depth = 0
MINIMUM_GTBAK_ELEMENT_LIST = [u'tasklist_name', u'title', u'status', u'depth']
MINIMUM_CSV_ELEMENT_LIST = [u'tasklist_name', u'title', u'status']
    
    
    

VALID_FILE_FORMAT_MSG = "<div>Only <a href='/static/info.html#import_export_csv'>properly formatted CSV</a> or GTBak files are supported. " + \
            "CSV files must be plain ASCII, or UTF-8 without BOM, and must use Windows (CR/LF) line endings.</div>" + \
            "<div class='comment'>If your tasks contain international or extended characters, it is strongly recommended that you use the GTBak format when exporting from GTB. The GTBak file format supports characters which may not be supported by CSV.</div><br />" + \
            "<div>If manually editing a CSV file, use an editor such as <a href='http://notepad-plus-plus.org/'>Notepad++</a> and set the encoding to 'UTF-8 without BOM' when saving the file.</div>" + \
            "<div class='description'>" + \
            "The first line of a CSV file must contain (at a minimum) the following columns: " + \
            "<br /><span class='fixed-font'>" + \
            ','.join(MINIMUM_CSV_ELEMENT_LIST) + "</span><br /><br />" + \
            "The full list of supported columns is; <br /><span class='fixed-font'>" + \
            ','.join(FULL_CSV_ELEMENT_LIST) + "</span></div>"

# This is the standard Outlook header row. We use this to determine if user is trying to upload an Outlook file.
OUTLOOK_HEADER_ROW = ["Subject","Start Date","Due Date","Reminder On/Off","Reminder Date","Reminder Time","Date Completed","% Complete","Total Work","Actual Work","Billing Information","Categories","Companies","Contacts","Mileage","Notes","Priority","Private","Role","Schedule+ Priority","Sensitivity","Status"]

INVALID_FORMAT_LOG_LABEL = "INVALID FORMAT: "

DAILY_LIMIT_EXCEEDED_LOG_LABEL = "DAILY LIMIT EXCEEDED: "

INVALID_LINE_TERMINATOR_MSG = "Please ensure that the file uses CR/LF (Windows/DOS) or LF (Unix/OS X) line endings.<br /><br /> " \
    "Unfortunately, Mac-style CR line endings are not supported.<br /> " \
    "Try converting line endings (EOL), or using 'Save as ...' and specify Windows or Unix."