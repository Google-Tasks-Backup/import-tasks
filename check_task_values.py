
import logging
from google.appengine.api import logservice # To flush logs
import shared

logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True




def depth_is_valid( #pylint: disable=too-many-arguments,too-many-return-statements
                   task_row_data, row_num, is_test_user, compare_with_previous_row = False, 
                   is_first_task_in_tasklist = False, prev_row_depth = 0):
    """
        params
            task_row_data               a dictionary representing a row from a CSV file
            row_num                     1-based data row number
            is_test_user                True if this is a test user, used to provide additional logging
            compare_with_previous_row   [OPTIONAL] If True, check that depth of first task in tasklist is zero, and compare depth value with value of previous task
            is_first_task_in_tasklist   [OPTIONAL] True if this task is the first task in a new tasklist
            prev_row_depth              [OPTIONAL] depth value of the previous data row
            
            
        returns
            result                      True if the depth value is valid
            depth                       The depth value of this task
            err_msg1                    Error message set if result is False
            err_msg2                    Error message set if result is False
    """
    
    fn_name = "depth_is_valid: "

    # ---------------------
    #   Check depth value
    # ---------------------
    # Depth is optional (worker will use depth = 0 if missing)
    # If depth exists;
    #   it must be numeric OR a blank string (which is interpretted as 0)
    #   it must be zero for first task in each tasklist
    
    err_msg1 = ""
    err_msg2 = ""
    depth = 0
    
    # Check if task has a depth value
    if not task_row_data.has_key('depth'):
        # OK: No depth value, so worker will use depth = 0
        return True, 0, "", ""
    
    # Check for missing depth field
    if task_row_data['depth'] is None:
        err_msg1 = "Missing depth field for data row " + str(row_num)
        err_msg2 = "Either the number of fields does not match the number of header columns, or there may be a newline in a non-quoted field"
        # ERROR: No depth
        return False, 0, err_msg1, err_msg2
        
    # Check for blank depth value
    depth_str = unicode(task_row_data['depth']).strip()
    if not depth_str:
        if is_test_user:
            logging.debug(fn_name + "TEST: Blank 'depth' property in data row " + 
                str(row_num) + ". Will set depth = 0 and import task as root")
            logservice.flush()
        # OK: Blank depth value, so worker will use depth = 0
        return True, 0, "", ""
    
    # Interpret the depth value
    try:
        depth = int(depth_str)
    except Exception, e: # pylint: disable=broad-except
        err_msg1 = ("Invalid 'depth' value [" + unicode(task_row_data['depth']) + 
            "] for data row " + str(row_num))
        err_msg2 = "The 'depth' value can be blank, or any whole number from -1 upwards"
        if is_test_user:
            logging.info(fn_name + "TEST: " + err_msg1 + ": " + shared.get_exception_msg(e)) 
            logservice.flush()
        return False, 0, err_msg1, err_msg2
        
    # Negative depth will be converted to zero by the worker
    # Files exported from GTB with -1 depth indicates a hidden or deleted task with no parent
    # From GTB: "This usually happens if parent is deleted whilst child is hidden or deleted (see below)"
    if depth < 0:
        if is_test_user:
            logging.info(fn_name + "TEST: Depth value [" + str(depth) + "] in data row " + 
                str(row_num) + " is less than zero. Will set depth = 0 and import task as root") 
            logservice.flush()
        depth = 0
        
    # Optionally compare this task with the previous task
    if compare_with_previous_row:
        # First task in a tasklist must have zero depth 
        if is_first_task_in_tasklist and depth != 0:
            err_msg1 = "Invalid 'depth' value " + str(depth) + " for data row " + str(row_num)
            err_msg2 = "The depth of the first task in each tasklist must be 0 or blank"
            return False, 0, err_msg1, err_msg2
        
        # Depth can never be more than 1 greater than previous depth value
        if depth > (prev_row_depth+1):
            err_msg1 = "Invalid 'depth' value for data row " + str(row_num)
            err_msg2 = "Depth value " + str(depth) + " is more than 1 greater than previous depth value " + str(prev_row_depth)
            return False, 0, err_msg1, err_msg2

    # OK: Passed all tests
    return True, depth, "", ""    
        
