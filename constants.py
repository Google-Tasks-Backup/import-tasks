# Any value, especially strings, that is referenced in more than one location.

class ImportJobStatus(object):
    # CAUTION: The Django progress.html template uses string literals when checking the status value. 
    # If these values are changed, then the progress.html must also be changed
    STARTING = 'Starting' # Job has been created (request places on task queue)
    INITIALISING = 'Initialising' # Pre-job initialisation (e.g., retrieving credentials)
    IMPORTING = 'Importing'
    IMPORT_COMPLETED = 'Import completed'
    ERROR = 'Error'
    
    ALL_VALUES = [STARTING, INITIALISING, IMPORTING, IMPORT_COMPLETED, ERROR]
    PROGRESS_VALUES = [STARTING, INITIALISING, IMPORTING]
    STOPPED_VALUES = [IMPORT_COMPLETED, ERROR]

    
class ImportMethod(object):
    APPEND_TIMESTAMP = "Append timestamp to tasklist name"
    USE_OWN_SUFFIX = "Append own suffix"
    IMPORT_AS_IS = "Import tasklists, original tasklist names"
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
    DAILY_LIMIT_EXCEEDED = "Daily limit exceeded"
    USER_INTERRUPTED = "User interrupted import job"
    
    ALL_VALUES = [NONE, DAILY_LIMIT_EXCEEDED, USER_INTERRUPTED]
    
    
# Max blob size is just under 1MB (~2^20), so use 1000000 to allow some margin for overheads
MAX_BLOB_SIZE = 1000000


# Name of folder containg templates. Do not include path separator characters, as they are inserted by os.path.join()
PATH_TO_TEMPLATES = "templates"