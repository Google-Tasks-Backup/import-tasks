#
# Copyright 2012  Julie Smith.  All Rights Reserved.
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

# Code that is common between import_tasks.py & worker.py

import logging
import datetime
import json

from google.appengine.api import urlfetch
from google.appengine.api import logservice # To flush logs

# Import from error so that we can process HttpError
from apiclient import errors as apiclient_errors

import unicodecsv

# Project-specific setting
import settings
import host_settings
import shared
import constants

logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True


# Fix for DeadlineExceeded, because "Pre-Call Hooks to UrlFetch Not Working"
#     Based on code from https://groups.google.com/forum/#!msg/google-appengine/OANTefJvn0A/uRKKHnCKr7QJ
real_fetch = urlfetch.fetch # pylint: disable=invalid-name
def fetch_with_deadline(url, *args, **argv):
    argv['deadline'] = settings.URL_FETCH_TIMEOUT
    logging.debug("DEBUG: import_tasks_shared.fetch_with_deadline(): deadline = " + str(settings.URL_FETCH_TIMEOUT))
    logservice.flush()
    return real_fetch(url, *args, **argv)
urlfetch.fetch = fetch_with_deadline


def serve_invalid_file_format_page(self, file_name, *args, **kwargs):
    """Display messages on the Invalid File Format page"""
    
    kwargs['template_file'] = "invalid_import_file.html"
    
    # Additional template values to be used on the Invalid File Format page
    kwargs['extra_template_values'] = { 
        'outlook_instructions_url' : settings.OUTLOOK_INSTRUCTIONS_URL,
        'url_GTB' : settings.url_GTB,
        'eot_executable_name' : settings.EOT_EXECUTABLE_NAME, 
        'file_name' : file_name}
    
    shared.serve_message_page(self, *args, **kwargs)
   

def parse_datetime_string(datetime_string, timestamp_formats):
    """Parse the datetime string, trying all allowable formats.
       
       Returns a datetime object if string can be parsed, using the first matching format.
       
       Returns None if the string cannot be parsed using any of the supplied formats.
    """
    
    # fn_name = "parse_datetime_string: " # Only used for debugging

    parsed_dt = None
    for timestamp_format in timestamp_formats:
        try:
            parsed_dt = datetime.datetime.strptime(datetime_string, timestamp_format)
            return parsed_dt # Successfuly parsed the datetime string
        except Exception: # pylint: disable=broad-except
            pass
            # logging.debug(fn_name + "DEBUG: Unable to parse '" + str(datetime_string) + 
                # "' as '" + timestamp_format + "': " + shared.get_exception_msg(e))

    # logging.debug(fn_name + "DEBUG: Unable to parse '" + str(datetime_string) + 
                # "' as a datetime using any of the supplied formats")
    return None


def convert_datetime_string_to_RFC3339( # pylint: disable=invalid-name
                                       datetime_string, field_name, timestamp_formats):
    """ Convert datetime_string to an RFC-3339 datetime string.
    
            datetime_string     The datetime string that was stored in the file
            field_name          The name of the field to be set. Only used for logging
            timestamp_formats   A list of format strings to try to apply to the datetime_string
       
        Returns the corresponding RFC-3339 string if datetime_string can be parsed, using the first matching format.
       
        Returns None if the string cannot be parsed using any of the supplied formats.
    """
    
    fn_name = "convert_datetime_string_to_RFC3339: "

    try:
    
        if not datetime_string:
            # Nothing to convert
            return None
            
        if datetime_string in constants.ZERO_DATETIME_STRINGS:
            # There are significant number of instances where 'completed' returned from the server has a zero-date,
            # value of '0000-01-01T00:00:00.000Z' which is stored by GTB in the CSV or GTBak files as 
            # '0000-01-01 00:00:00' or '0000-01-01'.
            # Zero dates cannot be parsed or converted to a datetime object, so we can't use strftime() to format,
            # so we check for zero-date and return the corresponding RFC-3339 string.
            # CAUTION: Attempting to create a task with a zero 'due' value results in strange behaviour;
            #   insert() returns a task object with an 'id', however attemping to get() that 'id' returns 
            #   "404 Not found"
            return constants.ZERO_RFC3339_DATETIME_STRING
        
        parsed_dt = None
        for timestamp_format in timestamp_formats:
            try:
                # Try creating a datetime object from a string using the supplied timestamp_format
                parsed_dt = datetime.datetime.strptime(datetime_string, timestamp_format)
                try:
                    # Successfuly parsed the datetime string, so return the corresponding RFC-3339 format
                    rfc_3339_str = parsed_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    return rfc_3339_str
                except Exception, e: # pylint: disable=broad-except
                    try:
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                            "Unable to convert '" + str(field_name) + 
                            "' value '" + str(datetime_string) + "' to an RFC-3339 datetime string: " +
                            shared.get_exception_msg(e))
                    except Exception, e: # pylint: disable=broad-except
                        logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                            "Unable to convert '" + str(field_name) + 
                            "' value to an RFC-3339 datetime string, and unable to log value: " +
                            shared.get_exception_msg(e))
                    
            except Exception, e: # pylint: disable=broad-except
                pass # Try the next format
                # logging.debug(fn_name + "DEBUG: Unable to parse '" + str(datetime_string) + 
                    # "' as '" + timestamp_format + "': " + shared.get_exception_msg(e))

        # Could not parse datetime_string with any of the supplied format strings
        try:
            logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                "Unable to parse '" + str(field_name) + "' value '" + str(datetime_string) + 
                "' as a datetime using any of the supplied formats")
        except Exception, e: # pylint: disable=broad-except
            # Just in case logging the datetime string causes an exception
            logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + 
                "Unable to parse '" + str(field_name) + "' datetime string as a datetime using any of the supplied formats, and unable to log datetime string: " + shared.get_exception_msg(e))
        
        return None

    except Exception, e: # pylint: disable=broad-except
        # Major error!
        try:
            logging.error(fn_name + "Error attempting to parse '" + str(field_name) + "' value '" + 
                str(datetime_string) + "': " + shared.get_exception_msg(e))
        except Exception, e: # pylint: disable=broad-except
            # Just in case logging the datetime string causes an exception
            logging.error(fn_name + 
                "Error attempting to parse '" + str(field_name) + 
                 "'datetime string, and unable to log datetime string: " + shared.get_exception_msg(e))
    
    return None
    
    
def set_RFC3339_timestamp( # pylint: disable=invalid-name
                          task_row_data, field_name, formats):
    """ Parse timestamp field value and replace with an RFC-3339 datetime string. 
    
            task_row_data   A row of data, as a dictionary object
            field_name      The name of the field to be set
            formats         A list of possible datetime string formats, as used by strptime()
        
        Parses the specified field and replaces the value with an RFC-3339 datetime string as required
        by the Google server.

        CAUTION: Attempting to create a task with a zero 'due' value results in strange behaviour;
                 insert() returns a task object with an 'id', however attemping to get() that 'id' returns 
                 "404 Not found"
        
        If there is a major problem, such as the field no being able to be parsed at all, we delete
        the field from the task_row_data.
    """
    
    fn_name = "set_RFC3339_timestamp: "
    try:
        if field_name in task_row_data:
            if task_row_data[field_name] is None:
                # If we find a None value, we restore the "Zero" RFC-3339 date value here.
                # GTB stores '0000-01-01T00:00:00.000Z' datetime values (aka constants.ZERO_RFC3339_DATETIME_STRING)
                # as None, because there is no way to store dates earlier than 1900 in a datetime object
                logging.debug(fn_name + "DEBUG: '" + field_name + "' value is None; setting value to '" +
                    constants.ZERO_RFC3339_DATETIME_STRING + "'")
                task_row_data[field_name] = constants.ZERO_RFC3339_DATETIME_STRING
                return
                
            # Field exists, so convert the value to an RFC-3339 datetime string
            datetime_str = task_row_data[field_name].strip()
            rfc_datetime_str = convert_datetime_string_to_RFC3339(datetime_str, field_name, formats)
            
            if rfc_datetime_str:
                task_row_data[field_name] = rfc_datetime_str
            else:
                # Unable to parse the datetime value, so delete the field which has the un-parseable value.
                # The server's behaviour for a missing field depends on the field and task state;
                #   If the 'due' field is missing, the created task will not have a due date
                #   If the 'completed' field is missing;
                #       If the 'status' is 'completed', the server uses the current date and time
                #       If the 'status' is 'needsAction', the created task will not have a completed date 
                try:
                    del task_row_data[field_name]
                except Exception, e: # pylint: disable=broad-except
                    logging.error(fn_name + "Unable to delete '" + field_name + "': " + 
                        shared.get_exception_msg(e))
                logservice.flush()
                
    except Exception, e: # pylint: disable=broad-except
        logging.exception(fn_name + "Error attempting to set '" + field_name + "' datetime field value, so deleting field")
        # Delete the field which caused the exception
        try:
            del task_row_data[field_name]
        except Exception, e: # pylint: disable=broad-except
            logging.error(fn_name + "Unable to delete '" + field_name + "': " + 
                shared.get_exception_msg(e))
        logservice.flush()

        
def _file_contains_valid_columns(file_obj, valid_column_names):
    """Returns True if file is a CSV that contains (at least) all the valid_column_names
    
            file_obj              A file object
            valid_column_names    A list of valid column names
    
        Returns a tuple;
            msg
                'OK' if the header row is valid.
                Returns a string describing the problem if the header is not valid.
            display_line
                Returns True if the probline is with the data in the first row.
                    i.e., the calling method should display the line to the user
        
        Uses unicodecsv so that unicode CSV files can be parsed.
        There may be extra columns, but that doesn't matter 
        because we are processing the CSV as a dictionary.
    """
    
    fn_name = "_file_contains_valid_columns: "
    
    try:
        file_obj.seek(0)
        dict_reader=unicodecsv.DictReader(file_obj,dialect='excel')
        
        # Check if uploaded file appears to be an Outlook file
        num_outlook_column_names = 0
        for col_name in constants.OUTLOOK_HEADER_ROW:
            if col_name in dict_reader.fieldnames:
                num_outlook_column_names += 1
                # logging.debug(fn_name + "DEBUG: Found '" + col_name + "' in header row")
            # else:
                # logging.debug(fn_name + "DEBUG: '" + col_name + "' not found in header row")
        # logging.debug(fn_name + "DEBUG: Found " + str(num_outlook_column_names) + " of " + 
            # str(len(constants.OUTLOOK_HEADER_ROW)) + " Outlook column names")
        if num_outlook_column_names == len(constants.OUTLOOK_HEADER_ROW):
            return (host_settings.APP_TITLE + 
                " cannot directly import an Outlook export file. Please refer to the <a href='" +
                settings.OUTLOOK_INSTRUCTIONS_URL + "'>instructions for exporting tasks from outlook</a>."), False
        
        
        for col_name in valid_column_names:
            if not col_name in dict_reader.fieldnames:
                return "Missing '" + col_name + "' column", True
                
        # All columns found (there may be extra columns, but that doesn't matter)
        return 'OK', False
        
    except Exception, e: # pylint: disable=broad-except
        # 2013-02-19: Kludge to check if the exception is caused by a file that doesn't end in CR/LF
        # At present, processing Mac files returns
        #   "Error: new-line character seen in unquoted field - do you need to open the file in universal-newline mode?"
        # We check for "new-line" or "newline" and hope that future version of Python will contain the same keywords.
        display_line = True
        kwds = ["new-line", "newline", "line-feed", "linefeed"]
        err_msg_list = str(e).split(' ')
        if any([kwd in kwds for kwd in err_msg_list]):
            msg = "Unable to process file. " + constants.INVALID_LINE_TERMINATOR_MSG
            logging.info(fn_name + constants.INVALID_FORMAT_LOG_LABEL + shared.get_exception_msg(e))
            # Don't display the offending line, because it could be the entire file because there was no line terminator!
            display_line = False
        else:
            msg = "Error checking column names - " + shared.get_exception_msg(e)
            logging.warning(fn_name + constants.INVALID_FORMAT_LOG_LABEL + msg)
        
        return msg, display_line
        

def file_has_valid_encoding(file_obj):
    """ Checks that file doesn't have a BOM
    
        Arguments:
            file_obj    A file object that refers to the CSV file to be processed by unicodcsv
            
        Returns a tuple;
            msg
                'OK' if the file encoding is supported (either UTF-8 without BOM, or ASCII)
                Returns a string describing the problem if the header is not valid.
    """
    
    try:
        file_obj.seek(0)
        header_row = file_obj.readline()
        # From the chardet.universaldetector package, to detect files with a BOM
        BOMs = ( # pylint: disable=invalid-name
                # EF BB BF  UTF-8 with BOM
                ([0xEF, 0xBB, 0xBF], "UTF-8 with BOM"),
                # FF FE 00 00  UTF-32, little-endian BOM
                ([0xFF, 0xFE, 0x00, 0x00], "UTF-32LE"),
                # 00 00 FE FF  UTF-32, big-endian BOM
                ([0x00, 0x00, 0xFE, 0xFF], "UTF-32BE"),
                # FE FF 00 00  UCS-4, unusual octet order BOM (3412)
                ([0xFE, 0xFF, 0x00, 0x00], "X-ISO-10646-UCS-4-3412"),
                # 00 00 FF FE  UCS-4, unusual octet order BOM (2143)
                ([0x00, 0x00, 0xFF, 0xFE], "X-ISO-10646-UCS-4-2143"),
                # FF FE  UTF-16, little endian BOM
                ([0xFF, 0xFE], "UTF-16LE"),
                # FE FF  UTF-16, big endian BOM
                ([0xFE, 0xFF], "UTF-16BE"),
            )

        header_row_len = len(header_row)

        # If the data starts with BOM, we know it is UTF
        # Testing character by character because comparing strings results in 
        #   "Unicode equal comparison failed to convert both arguments to Unicode - interpreting them as being unequal"
        for chunk, result in BOMs:
            chunk_len = len(chunk)
            if header_row_len >= chunk_len:
                i = 0
                match = 0
                while i < chunk_len:
                    if ord(header_row[i:i+1]) == chunk[i]:
                        #print "** Match"
                        match += 1
                    i += 1
                    if match == chunk_len:
                        # File is using one of the unsupported encoding, so return an error message
                        return result + " file encoding is not supported. File must be ASCII or UTF-8 without BOM"

        
        # File is not using one of the unsupported encoding, so return "OK"
        # File could still be another encoding (e.g., a binary file such as MS Excel or Doc)
        return 'OK'
        
    except Exception, e: # pylint: disable=broad-except
        return "Error processing first row of file: " + shared.get_exception_msg(e)
                
                
def file_has_valid_header_row(file_obj, valid_column_names):
    """ Checks if the header row is valid.
            - File doesn't have a BOM
            - Header row is plain ASCII
            - Header row contains the minimumm set of valid column names
    
        Arguments:
            file_obj            A file object that refers to the CSV file to be processed by unicodcsv
            valid_column_names  A list of valid column names
            
        Returns a tuple;
            msg
                'OK' if the header row is valid.
                Returns a string describing the problem if the header is not valid.
            display_line
                Returns True if the probline is with the data in the first row.
                    i.e., the calling method should display the line to the user
                Returns False if the problem is with the file
                    No point displaying the first row, because the problem is with the file itself
                    e.g., the file uses incorrect encoding (not UTF-8 without BOM and not ASCII)
        Calls _file_contains_valid_columns() to ensure that the file can be parsed by the unicodecsv package
    """
    
    try:
        file_obj.seek(0)
        header_row = file_obj.readline()
        if not header_row.strip():
            return "First row must contain column headers", False
            
        # Check if header row is plain ASCII
        try:
            _ = header_row.encode('ascii')
        except Exception, e: # pylint: disable=broad-except
            return "The header row may only contain plain ASCII characters: " + shared.get_exception_msg(e), True
                                
        # Check if the file can be parsed by unicodecsv, and that it has the minimum required columns
        # NOTE: _file_contains_valid_columns() returns a tuple
        return _file_contains_valid_columns(file_obj, valid_column_names)

    except Exception, e: # pylint: disable=broad-except
        return "Unable to process header row: " + shared.get_exception_msg(e), True

        
def delete_blobstore(blob_info):
    """ Delete specified blobstore """
    
    fn_name = "delete_blobstore(): "
    
    # logging.debug(fn_name + "<Start>")
    # logservice.flush()
    
    if blob_info:
        # -------------------------------------
        #       Delete the Blobstore item
        # -------------------------------------
        try:
            blob_info.delete()
            logging.debug(fn_name + "Blobstore deleted")
            logservice.flush()
        except Exception: # pylint: disable=broad-except
            logging.exception(fn_name + "Exception deleting %s, key = %s" % (blob_info.filename, blob_info.key()))
            logservice.flush()
    else:
        logging.warning(fn_name + "No blobstore to delete")
        logservice.flush()

    # logging.debug(fn_name + "<End>")
    # logservice.flush()

    
def get_tasklist(tasklists_svc, tasklist_id):
    """ Retrieve the specified tasklist.
    
        Returns the tasklist if it exists.
        
        The get() throws an Exception if task does not exist
    """

    return tasklists_svc.get(tasklist=tasklist_id).execute()
    
    
def tasklist_exists(tasklists_svc, tasklist_id):
    """ Returns True if specified tasklist exists, else False. """
    
    fn_name = "tasklist_exists: "
    
    try:
        result = get_tasklist(tasklists_svc, tasklist_id)
        if result.get('kind') == 'tasks#taskList' and result.get('id') == tasklist_id:
            return True
            
        logging.debug(fn_name + "DEBUG: Returned data does not appear to be a tasklist, or ID doesn't match " + 
            tasklist_id + " ==>")
        logging.debug(result)
        return False

    except apiclient_errors.HttpError, e:
        # logging.debug(fn_name + "Status = [" + str(e.resp.status) + "]")
        # 404 is expected if tasklist does not exist
        if e.resp.status == 404 or e.resp.status == 400:
            return False
        else:
            logging.exception(fn_name + "HttpError retrieving tasklist")
            raise e
        
    except Exception, e: # pylint: disable=broad-except
        logging.exception(fn_name + "Exception retrieving tasklist")
        raise e
            
            
def get_task(tasks_svc, tasklist_id, task_id):
    """ Retrieve specified task from specified tasklist.
    
        Returns the task if task exists.
        
        The get() throws an Exception if task does not exist
    """

    return tasks_svc.get(tasklist=tasklist_id, task=task_id).execute()
    

def get_task_safe(tasks_svc, tasklist_id, task_id):
    """ Retrieve specified task from specified tasklist. 
    
        Returns None if task does not exist (404). 
        
        Throws exception on any other errors.
        
    """

    fn_name = "get_task_safe: "
    
    try:
        result = tasks_svc.get(tasklist=tasklist_id, task=task_id).execute()
        
        if result.get('kind') == 'tasks#task' and result.get('id') == task_id:
            return result
            
        logging.warning(fn_name + "DEBUG: Returned data does not appear to be a task, or ID doesn't match " + task_id + " ==>")
        logging.debug(result)
        return None

    except apiclient_errors.HttpError, e:
        # logging.debug(fn_name + "DEBUG: Status = [" + str(e.resp.status) + "]")
        # 404 is expected if task does not exist
        if e.resp.status == 404:
            return None
        else:
            logging.exception(fn_name + "HttpError retrieving task, not a 404")
            raise e
        
    except Exception, e: # pylint: disable=broad-except
        logging.exception(fn_name + "Exception retrieving task")
        raise e
            

def task_exists(tasks_svc, tasklist_id, task_id):
    """ Returns True if specified task exists, else False. """
    
    fn_name = "task_exists: "
    
    try:
        logging.debug(fn_name + "DEBUG: Retrieving task {}".format(task_id))
        result = get_task(tasks_svc, tasklist_id, task_id)
        
        # Try logging the returned result to enable debugging.
        # 'result' should be a JSON object
        try:
            try:
                logging.debug("{}DEBUG:     result (json) = {}".format(
                    fn_name, json.dumps(result, indent=4)))
            except Exception as json_parse_ex: # pylint: disable=broad-except
                logging.info("{}DEBUG: Unable to log result as JSON: {}".format(
                    fn_name, 
                    shared.get_exception_msg(json_parse_ex)))
        except: # pylint: disable=bare-except
            pass
        try:
            logging.debug("{}DEBUG:     result (repr) = {}".format(
                fn_name, repr(result)))
        except: # pylint: disable=bare-except
            pass
        try:
            logging.debug("{}DEBUG:     result (raw) = {}".format(
                fn_name, result))
        except: # pylint: disable=bare-except
            pass
        
        if result.get('kind') == 'tasks#task' and result.get('id') == task_id:
            try:
                if 'parent' in result:
                    logging.debug("{}DEBUG:     Parent = {}".format(fn_name, result['parent']))
            except: # pylint: disable=bare-except
                logging.exception(fn_name + "DEBUG: Unable to log parent ID")
                
            try:
                if 'previous' in result:
                    logging.debug("{}DEBUG:     Previous = {}".format(fn_name, result['previous']))                
            except: # pylint: disable=bare-except
                logging.exception(fn_name + "DEBUG: Unable to log previous ID")
                
            logging.debug(fn_name + "DEBUG:     Returning True")
            return True
            
        logging.debug(fn_name + "DEBUG:     Returning False; Returned data does not appear to be a task, or ID doesn't match " + 
            task_id + ". Result ==>")
        logging.debug(result)
        return False

    except apiclient_errors.HttpError as ex:
        # logging.debug(fn_name + "Status = [" + str(ex.resp.status) + "]")
        # 404 is expected if task does not exist
        if ex.resp and (ex.resp.status == 404 or ex.resp.status == 400):
            logging.debug("{}DEBUG:     Returning False; HttpError {} retrieving task. Task does not exist: {}".format(
                fn_name, 
                ex.resp.status,
                ex))
            return False
        else:
            logging.exception(fn_name + "Raising exception; HttpError retrieving task, not a 404 or 400")
            raise ex
        
    except Exception, ex: # pylint: disable=broad-except
        logging.exception(fn_name + "Raising exception; Exception retrieving task")
        raise ex

        
def check_task_params_exist(tasklists_svc, tasks_svc, tasklist_id, parent_id, sibling_id):
    fn_name = "check_task_params_exist: "
    
    logging.debug(fn_name + "DEBUG: Checking tasklist [" + str(tasklist_id) + 
        "], parent [" + str(parent_id) +
        "], sibling [" + str(sibling_id) + "]")
        
    error_results = []
    
    # Check if tasklist exists
    if tasklist_id:
        if tasklist_exists(tasklists_svc, tasklist_id):
            logging.debug(fn_name + "Tasklist {} exists".format(tasklist_id))
            logservice.flush()
        else:
            # logging.error(fn_name + "ERROR: Tasklist [" + str(tasklist_id) + "] doesn't exist")
            # logservice.flush()
            error_results.append("Tasklist [" + str(tasklist_id) + "] doesn't exist")
    else:
        # logging.error(fn_name + "ERROR: No tasklist ID!")
        # logservice.flush()
        error_results.append("No tasklist ID")
                
    # Check if parent task exists (if it was specified)
    if parent_id:
        if task_exists(tasks_svc, tasklist_id, parent_id):
            logging.debug(fn_name + "Parent task '{}' exists".format(parent_id))
            logservice.flush()
        else:
            # logging.error(fn_name + "ERROR: Parent task [" + str(parent_id) + "] doesn't exist")
            # logservice.flush()
            error_results.append("Parent task [" + str(parent_id) + "] doesn't exist")
    # else:
        # logging.debug(fn_name + "DEBUG: No parent ID")
        # logservice.flush()
                
    # Check if sibling task exists (if it was specified)
    if sibling_id:
        if task_exists(tasks_svc, tasklist_id, sibling_id):
            logging.debug(fn_name + "Sibling task '{}' exists".format(sibling_id))
            logservice.flush()
        else:
            # logging.error(fn_name + "ERROR: Sibling task [" +  str(sibling_id) + "] doesn't exist")
            # logservice.flush()
            error_results.append("Sibling task [" +  str(sibling_id) + "] doesn't exist")
    # else:
        # logging.debug(fn_name + "DEBUG: No sibling ID")
        # logservice.flush()

    if error_results:
        logging.error(fn_name + "ERROR: " + ', '.join(error_results))
    else:
        logging.debug(fn_name + "DEBUG: All task params exist")
    logservice.flush()
