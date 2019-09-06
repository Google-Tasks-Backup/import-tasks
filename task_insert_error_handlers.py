""" Condition handler methods.

Each method in this module is designed to handle a possible error condition when inserting a task.

Each method must have the following signature:
    handle_XXXXX(self, http_err, retry_count, task_row_data)
where:
    self            The ProcessTasksWorker
    http_err        The HttpError exception caught whilst inserting a task
    retry_count     The error retry counter. Zero if this is the last retry.
                    Starts at settings.NUM_API_TRIES
    task_row_data   The data used to insert the current task
returns a tuple;
    condition_handled, reset_retry_count
where:
    condition_handled
        False if handler could not handle the condition
        True if handle was able to handle the condition
    reset_retry_count
        True if retry_count should be reset
            To allow the full number of retries after the condition has been handled
        False if retry_count should not be changed
Raises:
    DailyLimitExceededError     If the daily limit has been exceeded
    TaskInsertError             If the handler has a specific message to pass to the user
"""

__author__ = "julie.smith.1999@gmail.com (Julie Smith)"

import logging

from google.appengine.api import logservice # To flush logs

import constants
import settings
import shared
from shared import DailyLimitExceededError, TaskInsertError
from shared import get_http_error_status, get_http_error_reason
import import_tasks_shared
from import_tasks_shared import tasklist_exists

logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True



def handle_daily_limit_exceeded(self, http_err, retry_count, task_row_data): # pylint: disable=unused-argument
    """ Handle possible "daily limit exceeded" error

    Raises DailyLimitExceededError if the daily limit has been exceeded
    """
    fn_name = "handle_daily_limit_exceeded(): "
    
    reason = get_http_error_reason(http_err)
    if "daily limit exceeded" in reason.lower():
        logging.warning("%sRaising 'Daily limit exceeded' error", fn_name)
        raise DailyLimitExceededError()

    return False, False


def handle_notes_too_long(self, http_err, retry_count, task_row_data): # pylint: disable=unused-argument
    """ Handle possible notes too long.

    Raises TaskInsertError if the notes are longer than constants.MAX_NOTES_LEN
    """

    fn_name = "handle_notes_too_long(): "

    reason = get_http_error_reason(http_err)
    if "invalid" in reason.lower():
        # The Tasks API doesn't indicate which value is invalid,
        # so try to log the 'body', in case that contains an invalid value.
        # We first try to log as JSON, because that is the format that
        # the Tasks API expects.
        # We also log the repr and the raw values, to give us the maximum
        # possible representations to help find the cause of the
        # "Invalid Value" message.
        # JS 2019-07-28; Was checking for "invalid value", but today GTI
        # received reason code "invalid" instead of "invalid value",
        # so now logging row data if reason contains "invalid"
        # JS 2019-07-31; Receiving "Invalid Value" if 'notes' are too long
        notes_len = 0
        if 'notes' in task_row_data:
            notes_len = len(task_row_data['notes'])
            if notes_len > constants.MAX_NOTES_LEN:
                err_msg = (
                    "The Google Tasks server returned error {status} '{reason}'<br>"
                    "This may be because the length of 'notes' in row {row_num:,} "
                    "is {notes_len:,} "
                    "which exceeds the new limit of {max_notes_len:,} "
                    "allowed by Google Tasks"
                ).format(
                    status=get_http_error_status(http_err),
                    reason=reason,
                    row_num=self.import_job_state.data_row_num,
                    notes_len=notes_len,
                    max_notes_len=constants.MAX_NOTES_LEN
                )
                logging.warning("%sNotes may be too long. Raising TaskInsertError", fn_name)
                raise TaskInsertError(err_msg) # Don't bother retrying for over-sized notes

        if retry_count == 0:
            # This the last retry, and reason contains 'invalid', so log the task data
            logging.warning("%sHttpError %s '%s' not due to 'notes' length (%d < %d), so logging task_row_data ==>",
                            fn_name,
                            get_http_error_status(http_err),
                            reason,
                            notes_len,
                            constants.MAX_NOTES_LEN)
            shared.log_content_as_json('task_row_data', task_row_data)

        # We received an "invalid" or "invalid value", but not due to too long 'notes',
        # so return False so that another handler (e.g. Retry or Retry with sleep) can have a go
        return False, False

    # No reason, so this condition cannot be "invalid" or "invalid value"
    # Return False so that another handler (e.g. Retry or Retry with sleep) can have a go
    return False, False


def handle_quota_error(self, http_err, retry_count, task_row_data): # pylint: disable=unused-argument
    """ Handle possible quota error.

    If reason indicates a quota error:
        Sleeps for settings.QUOTA_EXCEEDED_API_RETRY_SLEEP_DURATION if retry_count > 0
    """

    fn_name = "handle_quota_error(): "

    reason = get_http_error_reason(http_err)
    if "quota" in reason.lower():
        if retry_count <= 0:
            # We've reached our last retry
            err_msg = "The Google Tasks server reports {}<br>{}".format(
                reason,
                shared.get_exception_msg(http_err))
            logging.warning("%sFound '%s', so raising TaskInsertError", fn_name, reason)
            raise TaskInsertError(err_msg)

        # JS 2019-08-17: There has been an increase in the number of
        # 403 "Quota Exceeded" errors in the past 2 days, but there is no
        # indication that quotas have been exceeded in
        #   https://console.cloud.google.com/apis/api/tasks.googleapis.com/quotas
        # or
        #   https://console.cloud.google.com/appengine/quotadetails
        # The logs show that GTI sometimes recovers from a "Quota Exceeded"
        # error after the 55 second "Giving server an extra chance" handler,
        # so we sleep even longer for a "Quota Exceeded" error.
        # Google has a "limit per user per 100 seconds", so we wait at least 100 seconds
        logging.warning("%sSleeping for %d seconds due to HttpError %s '%s'",
                        fn_name,
                        settings.QUOTA_EXCEEDED_API_RETRY_SLEEP_DURATION,
                        get_http_error_status(http_err),
                        reason)
        logservice.flush()
        self.sleep_with_updates(settings.QUOTA_EXCEEDED_API_RETRY_SLEEP_DURATION)
        return True, False # Retry after sleeping

    # No reason, so this condition cannot be a quota error
    # Return False so that another handler (e.g. Retry or Retry with sleep) can have a go
    return False, False


def handle_404_last_retry(self, http_err, retry_count, task_row_data): # pylint: disable=unused-argument, too-many-branches
    """ Handle possible 404 error, on the last retry.

    There are many possible causes for the server returning a 404;
        Tasklist doesn't exist (possibly because user deleted it whilst GTI was running)
        Parent or sibling doesn't exist.
    This method handles a specific BUG 2012-05-08 01:02
        This happens very occassionaly, where insert() returns 404 "Not found" when creating a task,
        even though we have an ID for the previously created sibling &/or parent task.
        i.e., We have the ID returned from a previous insert(), but we get a 404 when we try to
            access that task.
        If this is the last retry, and the parent and/or sibling still doesn't exist, recreate
        the missing parent and/or sibling.

    Raises TaskInsertError if a missing parent or sibling cannot be recreated.
    Returns:
        False, False if the condition cannot be handled by this handler
        True, True if missing parent and/orsibling were recreated
    """

    fn_name = "handle_404_last_retry(): "

    if get_http_error_status(http_err) != 404:
        # Not a 404, so try other handlers
        return False, False

    if retry_count > 0:
        # Not the last retry, so try other handlers
        return False, False

    if not tasklist_exists(self.tasklists_svc, self.import_job_state.tasklist_id):
        # A missing task list generates 404 "Task list not found."
        # User could have deleted task list after it was created.
        # No point trying to recreate missing parent or sibling if tasklist doesn't exists.
        logging.error("%sTasklist %s does not exist. Raising TaskInsertError",
                      fn_name,
                      self.import_job_state.tasklist_id)
        raise TaskInsertError("Unable to insert task - tasklist doesn't exist")

    # We've tried to insert the task 3 times, but the parent or sibling task
    # doesn't exist, so we need to recreate the missing task.

    logging.error("%sINSERT FAILED for row %s - possibly missing parent or sibling: %s",
                  fn_name,
                  self.import_job_state.data_row_num,
                  get_http_error_reason(http_err))
    logging.error("%s    Failed inserting task number %s in tasklist number %s for %s",
                  fn_name,
                  self.import_job_state.num_tasks_in_list,
                  self.process_tasks_job.num_tasklists,
                  self.user_email)
    logservice.flush()

    created_missing_task = False

    if self.import_job_state.parent_id:
        # Check if parent task exists
        if import_tasks_shared.task_exists(self.tasks_svc,
                                           self.import_job_state.tasklist_id,
                                           self.import_job_state.parent_id):
            logging.info("%s      Parent task '%s' exists",
                         fn_name,
                         self.import_job_state.parent_id)
        else:
            # -------------------------------------------
            #   Attempt to recreate missing parent task
            # -------------------------------------------
            logging.info("%sAttempting to recreate missing parent", fn_name)
            logservice.flush()
            new_parent_id = self.insert_missing_task(self.import_job_state.prev_tasks_data)
            if new_parent_id:
                logging.info("%sRecreated missing parent for data row %s",
                             fn_name,
                             self.import_job_state.data_row_num)
                self.import_job_state.parent_id = new_parent_id
                created_missing_task = True
            else:
                err_msg = "Unable to recreate missing parent task for data row {}".format(
                    self.import_job_state.data_row_num)
                logging.error(fn_name + err_msg)
                logservice.flush()
                raise TaskInsertError(err_msg)

    if self.import_job_state.sibling_id:
        # Check if sibling task exists
        if import_tasks_shared.task_exists(self.tasks_svc,
                                           self.import_job_state.tasklist_id,
                                           self.import_job_state.sibling_id):
            logging.info("%s      Sibling task '%s' exists",
                         fn_name,
                         self.import_job_state.sibling_id)
        else:
            # ---------------------------------------------
            #   Attempt to recreate missing sibling task
            # ---------------------------------------------
            logging.info("%sAttempting to recreate missing sibling", fn_name)
            logservice.flush()
            new_sibling_id = self.insert_missing_task(self.import_job_state.prev_tasks_data)
            if new_sibling_id:
                logging.info("%sRecreated missing sibling for data row %s",
                             fn_name,
                             self.import_job_state.data_row_num)
                self.import_job_state.sibling_id = new_sibling_id
                created_missing_task = True
            else:
                err_msg = "Unable to recreate missing previous task for data row {}".format(
                    self.import_job_state.data_row_num)
                logging.error(fn_name + err_msg)
                logservice.flush()
                raise TaskInsertError(err_msg)

    if created_missing_task:
        logging.info("%sRecreated missing task(s)", fn_name)
        # Reset the retry count so that we can create the 'current' task
        # after we've recreated the missing parent or sibling task
        return True, True # Success

    # No missing parent/sibling, or couldn't recreate missing tasks
    return False, False


def handle_first_503(self, http_err, retry_count, task_row_data): # pylint: disable=unused-argument
    """ Log the first 503 as info """
    fn_name = "handle_first_503(): "
    if retry_count == settings.NUM_API_TRIES-1 and get_http_error_status(http_err) == 503:
        # Log first 503 as an Info level, because
        #   (a) There are a frequent 503 errors
        #   (b) Almost all 503 errors recover after a single retry
        logging.info("%sFirst 503 HTTP error, so may not be an error (yet). %s attempt remaining",
                     fn_name, retry_count)
        logservice.flush()
        return True, False

    return False, False


def handle_sleep_retry(self, http_err, retry_count, task_row_data): # pylint: disable=unused-argument
    """ Last chance - sleep to give the server some extra time before re-requesting.

    NOTE: This should be the last handler.
    """

    fn_name = "handle_sleep_retry(): "

    if retry_count <= 0:
        logging.error("%sGiving up after %s attempts",
                      fn_name,
                      settings.NUM_API_TRIES)
        # We have reached the last retry, so no point sleeping
        return False, False

    if retry_count <= 2:
        logging.info("%sGiving server an extra chance; Sleeping for %s seconds before retrying",
                     fn_name, settings.API_RETRY_SLEEP_DURATION)
        logservice.flush()
        self.sleep_with_updates(settings.API_RETRY_SLEEP_DURATION)

    logging.warning("%s%s retry attempts remaining",
                    fn_name,
                    retry_count)
    logservice.flush()

    # We (may have) slept, so we have handled the condition
    return True, False


# The handlers must be listed in order of preferred execution
HANDLERS = [
    handle_daily_limit_exceeded,
    handle_first_503,
    handle_notes_too_long,
    handle_quota_error,
    handle_404_last_retry,
    handle_sleep_retry # Last handler
]
