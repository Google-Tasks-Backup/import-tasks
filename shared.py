""" Methods shared across modules """

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
#
# Portions of this code are from Dwight Guth's Google Tasks Porter

# This module contains code whis is common between classes, modules or related projects
# Can't use the name common, because there is already a module named common

import sys
import os
import logging
import traceback
import Cookie
import cgi
import json
from datetime import datetime


from google.appengine.api import urlfetch
from google.appengine.api import logservice # To flush logs
from google.appengine.api import mail
from google.appengine.api.app_identity import get_application_id
from google.appengine.ext.webapp import template



# Project-specific imports
import settings
import constants
import appversion # appversion.version is set before the upload process to keep the version number consistent
import host_settings


logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True

# Fix for DeadlineExceeded, because "Pre-Call Hooks to UrlFetch Not Working"
#     Based on code from https://groups.google.com/forum/#!msg/google-appengine/OANTefJvn0A/uRKKHnCKr7QJ
real_fetch = urlfetch.fetch # pylint: disable=invalid-name
def fetch_with_deadline(url, *args, **argv):
    """ Fetch URL with deadline set by URL_FETCH_TIMEOUT """
    
    argv['deadline'] = settings.URL_FETCH_TIMEOUT
    return real_fetch(url, *args, **argv)
urlfetch.fetch = fetch_with_deadline



class DailyLimitExceededError(Exception):
    """ Thrown by get_credentials() when HttpError indicates that daily limit has been exceeded """
    
    # msg = "Daily limit exceeded. Please try again after midnight Pacific Standard Time."
    
    def __init__(self, msg=None): # pylint: disable=super-init-not-called
        if msg:
            self.msg = msg
        else:
            self.msg = "Daily limit exceeded. Please try again after midnight Pacific Standard Time."
        super(DailyLimitExceededError, self).__init__(self.msg)
            


class TaskInsertError(Exception):
    """ Indicates that a task could not be inserted. 
        msg is a message to the user
    """
    
    def __init__(self, msg): # pylint: disable=super-init-not-called
        self.msg = msg
        super(TaskInsertError, self).__init__(msg)



class WorkerNearMaxRunTime(Exception):
    """ Indicates that this worker needs to end, and job needs to be passed to a new worker.
    
        This is because there is a maximum 10 minute limit for a background process.
        
        :param run_seconds:
            The number of seconds that the worker has been running. Used in log messages.
    """
    
    def __init__(self, run_seconds):
        self.run_seconds = run_seconds
        msg = "Worker has been running for {} seconds".format(run_seconds)
        super(WorkerNearMaxRunTime, self).__init__(msg)




def set_cookie( # pylint: disable=too-many-arguments
               res, key, value='', max_age=None,
               path='/', domain=None, secure=None, httponly=False,
               version=None, comment=None):
    """
    Set (add) a cookie for the response
    """
    
    fn_name = "set_cookie(): "
    
    cookies = Cookie.SimpleCookie()
    cookies[key] = value
    for var_name, var_value in [
        ('max-age', max_age),
        ('path', path),
        ('domain', domain),
        ('secure', secure),
        ('HttpOnly', httponly),
        ('version', version),
        ('comment', comment),
        ]:
        if var_value is not None and var_value is not False:
            cookies[key][var_name] = str(var_value)
        if max_age is not None:
            cookies[key]['expires'] = max_age
    header_value = cookies[key].output(header='').lstrip()
    res.headers.add_header("Set-Cookie", header_value)
    logging.debug(fn_name + "Writing cookie: '" + str(key) + "' = '" + str(value) + "', max age = '" + str(max_age) + "'")
    logservice.flush()
    
    
def delete_cookie(res, key):
    """ Delete cookie """
    
    logging.debug("Deleting cookie: " + str(key))
    set_cookie(res, key, '', -1)
    
  
def format_exception_info(max_tb_level=5):
    """ Return exception name, args and traceback """
    
    cla, exc, trbk = sys.exc_info()
    exc_name = cla.__name__
    try:
        exc_args = exc.__dict__["args"]
    except KeyError:
        exc_args = "<no args>"
    exc_tb = traceback.format_tb(trbk, max_tb_level)
    return (exc_name, exc_args, exc_tb)
         

def get_exception_name():
    """ Return the exception name as a string """
    
    cla, _, _ = sys.exc_info()
    exc_name = cla.__name__
    return str(exc_name)
         

# def get_exception_msg(msg):
    # cla, exc, trbk = sys.exc_info()
    # exc_name = cla.__name__
    # return str(exc_name) + ": " + str(msg)
  
def get_exception_msg(e=None):
    """ Return string containing exception type and message
    
        args:
            e       [OPTIONAL] An exception type
            
        If e is specified, and is of an Exception type, this method returns a 
        string in the format "Type: Msg" 
        
        If e is not specified, or cannot be parsed, "Type: Msg" is
        returned for the most recent exception
    """
    
    line_num = u''
    msg = u''
    ex_msg = u"No exception occured"
        
    # Store current exception msg, in case building msg for e causes an exception
    cla, exc, trbk = sys.exc_info()
    try:
        line_num = trbk.tb_lineno
    except: # pylint: disable=bare-except
        pass
    if cla:
        exc_name = cla.__name__
        if line_num:
            ex_msg = u"{}: {} at line {}".format(exc_name, exc.message, line_num)
        else:
            ex_msg = u"{}: {}".format(exc_name, exc.message)
    
    if e:
        try:
            e_msg = unicode(e)
            exc_name = e.__class__.__name__
            
            msg = u"{}: {}".format(exc_name, e_msg)
            
        except: # pylint: disable=bare-except
            # Unable to parse passed-in exception 'e', so returning the most recent
            # exception when this method was called
            msg = u"Unable to process 'e'. Most recent exception = " + ex_msg

    if msg:
        return msg
    return ex_msg
        
         
def is_test_user(user_email):
    """ Returns True if user_email is one of the defined settings.TEST_ACCOUNTS 
  
        Used when testing to ensure that only test user's details and sensitive data are logged.
    """
    return user_email.lower() in (email.lower() for email in settings.TEST_ACCOUNTS)
  

def dump_obj(obj):
    """ Log attribute names and values """
    for attr in dir(obj):
        logging.debug("    obj.%s = %s" % (attr, getattr(obj, attr)))
    logservice.flush()

    
def escape_html(text):
    """Ensure that text is properly escaped as valid HTML"""
    if text is None:
        return None
    # From http://docs.python.org/howto/unicode.html
    #   .encode('ascii', 'xmlcharrefreplace')
    #   'xmlcharrefreplace' uses XML's character references, e.g. &#40960;
    return cgi.escape(text).encode('ascii', 'xmlcharrefreplace').replace('\n', '<br />')
    #return cgi.escape(text.decode('unicode_escape')).replace('\n', '<br />')
    #return "".join(html_escape_table.get(c,c) for c in text)

    
def serve_quota_exceeded_page(self):
    """ Display a message indicating that the daily limit has been exceeded """
    
    msg1 = "Daily limit exceeded"
    msg2 = "The daily quota is reset at midnight Pacific Standard Time (5:00pm Australian Eastern Standard Time, 07:00 UTC)."
    msg3 = "Please rerun " + host_settings.APP_TITLE + " any time after midnight PST to continue importing your file."
    serve_message_page(self, msg1, msg2, msg3)
    
    
def serve_message_page(self, # pylint: disable=too-many-arguments,too-many-locals 
        msg1, msg2=None, msg3=None, 
        show_back_button=False, 
        back_button_text="Back to previous page",
        show_custom_button=False, custom_button_text='Try again', custom_button_url=settings.MAIN_PAGE_URL,
        show_heading_messages=True,
        template_file="message.html",
        extra_template_values=None):
    """ Serve message.html page to user with message, with an optional button (Back, or custom URL)
    
        self                    A webapp.RequestHandler or similar
        msg1, msg2, msg3        Text to be displayed.msg2 and msg3 are option. Each msg is displayed in a separate div
        show_back_button        If True, a [Back] button is displayed, to return to previous page
        show_custom_button      If True, display button to jump to any URL. title set by custom_button_text
        custom_button_text      Text label for custom button
        custom_button_url       URL to go to when custom button is pressed
        show_heading_messages   If True, display app_title and (optional) host_msg
        template_file           Specify an alternate HTML template file
        extra_template_values   A dictionary containing values that will be merged with the existing template values
                                    They may be additional parameters, or overwrite existing parameters.
                                    These new values will be available to the HTML template.
                                    
        All args except self and msg1 are optional.
    """
    fn_name = "serve_message_page: "

    logging.debug(fn_name + "<Start>")
    logservice.flush()
    
    try:
        if msg1:
            logging.debug(fn_name + "Msg1: " + msg1)
        if msg2:
            logging.debug(fn_name + "Msg2: " + msg2)
        if msg3:
            logging.debug(fn_name + "Msg3: " + msg3)
            
        path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, template_file)
          
        template_values = {'app_title' : host_settings.APP_TITLE,
                           'host_msg' : host_settings.HOST_MSG,
                           'msg1': msg1,
                           'msg2': msg2,
                           'msg3': msg3,
                           'show_heading_messages' : show_heading_messages,
                           'show_back_button' : show_back_button,
                           'back_button_text' : back_button_text,
                           'show_custom_button' : show_custom_button,
                           'custom_button_text' : custom_button_text,
                           'custom_button_url' : custom_button_url,
                           'product_name' : host_settings.PRODUCT_NAME,
                           'url_discussion_group' : settings.url_discussion_group,
                           'email_discussion_group' : settings.email_discussion_group,
                           'SUPPORT_EMAIL_ADDRESS' : settings.SUPPORT_EMAIL_ADDRESS,
                           'url_main_page' : settings.MAIN_PAGE_URL,
                           'url_issues_page' : settings.url_issues_page,
                           'url_source_code' : settings.url_source_code,
                           'app_version' : appversion.version,
                           'upload_timestamp' : appversion.upload_timestamp}
                           
        if extra_template_values:
            # Add/update template values
            # logging.debug(fn_name + "DEBUG: Updating template values ==>")
            # logging.debug(extra_template_values)
            # logservice.flush()
            template_values.update(extra_template_values)
            
        self.response.out.write(template.render(path, template_values))
        logging.debug(fn_name + "<End>")
        logservice.flush()
    except Exception, e: # pylint: disable=broad-except
        logging.exception(fn_name + "Caught top-level exception")
        serve_outer_exception_message(self, e)
        logging.debug(fn_name + "<End> due to exception")
        logservice.flush()
    

def serve_outer_exception_message(self, ex):
    """ Display an Oops message when something goes very wrong. 
    
        This is called from the outer exception handler of major methods (such as get/post handlers)
    """
    fn_name = "serve_outer_exception_message: "
    
    # self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br /><br />This system is in beta, and is being activeley developed.<br />Please report any errors to <a href="http://%s">%s</a> so that they can be fixed. Thank you.""" % 
        # ( get_exception_msg(ex), settings.url_issues_page, settings.url_issues_page))
        
    self.response.out.write("""
        Oops! Something went terribly wrong:<br />
        {exception_msg}<br />
        <br />
        Please report this error
        <ul>
            <li>via Github at <a href="http://{url_issues_page}">{url_issues_page}</a></li>
            <li>or via the discussion group at <a href="http://{url_discussion_group}">{url_discussion_group}</a></li> 
            <li>or via email to <a href="mailto:{email_discussion_group}">{email_discussion_group}</a></li>
        </ul>
        so that it can be fixed. Thank you.
            """.format(
                    exception_msg=get_exception_msg(ex),
                    url_issues_page=settings.url_issues_page,
                    url_discussion_group=settings.url_discussion_group,
                    email_discussion_group=settings.email_discussion_group))
        
    logging.error(fn_name + get_exception_msg(ex))
    logservice.flush()
    
    send_email_to_support("Served outer exception message", get_exception_msg(ex))
        
    
def reject_non_test_user(self):
    """ Display a message page indicating that this is a test server """
    
    fn_name = "reject_non_test_user: "
    
    logging.debug(fn_name + "Rejecting non-test user on limited access server")
    logservice.flush()
    
    # self.response.out.write("<html><body><h2>This is a test server. Access is limited to test users.</h2>" +
                    # "<br /><br /><div>Please use the production server at <href='http://tasks-backup.appspot.com'>tasks-backup.appspot.com</a></body></html>")
                    # logging.debug(fn_name + "<End> (restricted access)" )
    serve_message_page(self, 
        "This is a test server. Access is limited to test users.",
        "Please click the button to go to the production server at " + settings.PRODUCTION_SERVERS[0],
        show_custom_button=True, 
        custom_button_text='Go to live server', 
        custom_button_url='http://' + settings.PRODUCTION_SERVERS[0],
        show_heading_messages=False)
                    
                    
def send_email_to_support(subject, msg, job_start_timestamp=None):
    """ Send an email to SUPPORT_EMAIL_ADDRESS.

    """
    
    fn_name = "send_email_to_support: "
    
    msg = msg.replace('<br>', '\n')    
    
    try:
        # Ideally, 'subject' should be common to a job, so we try to get the job start time.
        # This ensures that Gmail doesn't put all GTI error reports in one conversation        
        if job_start_timestamp:
            subject = u"{} v{} ({}) - ERROR for job started at {} - {}".format(
                host_settings.APP_TITLE,
                appversion.version,
                appversion.app_yaml_version,
                job_start_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                subject)
        else:
            subject = u"{} v{} ({}) - ERROR at {} - {}".format(
                host_settings.APP_TITLE,
                appversion.version,
                appversion.app_yaml_version,
                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                subject)
        
        if not settings.SUPPORT_EMAIL_ADDRESS:
            logging.info(fn_name + u"No support email address, so email not sent:")
            logging.info(fn_name + u"    Subject = {}".format(subject))
            logging.info(fn_name + u"    Msg = {}".format(msg))
            return
            
        logging.info(fn_name + u"Sending support email:")
        logging.info(fn_name + u"    Subject = {}".format(subject))
        logging.info(fn_name + u"    Msg = {}".format(msg))
        
        sender = host_settings.APP_TITLE + " <noreply@" + get_application_id() + ".appspotmail.com>"
        
        mail.send_mail(sender=sender,
            to=settings.SUPPORT_EMAIL_ADDRESS,
            subject=subject,
            body=msg)
    
    except: # pylint: disable=bare-except
        logging.exception(fn_name + "Error sending support email")
        # logging.info(fn_name + "    Subject = {}".format(subject))
        # logging.info(fn_name + "    Msg = {}".format(msg))


def log_content_as_json(label, content):
    """ Log the content as JSON.
    
        If content is not JSON, try to convert content to JSON, then log it.
    """
    
    fn_name = "log_content_as_json: "
    
    try:
        # Try to de-serialise content to JSON
        # Strip trailing newlines
        parsed_json = json.loads(content.rstrip())
        logging.info(u"{}{} as JSON = {}".format(
            fn_name,
            label,
            json.dumps(parsed_json, indent=4)))
        return
    except: # pylint: disable=bare-except
        # No need to log anything here, as the content may be serialised JSON
        pass
        
    try:
        logging.info(u"{}{} = {}".format(
            fn_name,
            label,
            json.dumps(content, indent=4)))
        return
    except Exception as ex: # pylint: disable=broad-except
        logging.warning("{}Unable to log '{}' as JSON: {}".format(
            fn_name, 
            label,
            get_exception_msg(ex)))
            
    # Try logging repr() of content
    try:
        logging.info(u"{}{} (repr) = {}".format(
            fn_name,
            label,
            repr(content)))
        return
    except Exception as ex: # pylint: disable=broad-except
        logging.warning(u"{}Unable to log repr of '{}': {}".format(
            fn_name, 
            label,
            get_exception_msg(ex)))

    # Try logging content, letting format() handle possible conversion
    try:
        logging.info(u"{}{} (raw) = {}".format(
            fn_name,
            label,
            content))
        return
    except Exception as ex: # pylint: disable=broad-except
        logging.warning(u"{}Unable to log '{}' as raw: {}".format(
            fn_name, 
            label,
            get_exception_msg(ex)))


def is_truthy(val):
    """ Returns True if val has a value that could be interpreted as True.

    An empty string returns False.

    Note that for checkbox inputs in HTML forms;
        If the field element has a value attribute specified,
            then let value be the value of that attribute;
        otherwise,
            let value be the string "on".
        If checkbox isn't checked then it doesn't contribute to the data sent on form submission.

        So when getting the checkbox value in a POST handler, if value hasn't been set
            val = self.request.get('element_name', '')
        val will be 'on' if checkbox is checked, or empty string if checkbox is unchecked
    """
    
    fn_name = "is_truthy: "

    if isinstance(val, bool):
        return val
        
    try:
        # Try to interpret val as an integer
        # Types/Values that are converted to non-zero values by int():
        #   Float (e.g. 1.23)
        #   String representation of an int (e.g. "1")
        #   Boolean (e.g. True)
        i = int(val)
        # Return True if the value is non-zero
        return i != 0
    except: # pylint: disable=bare-except
        # val is not a number (float or int), bool or string representation of an int
        pass
        
    try:
        # Try to interpret val as a string
        return val.lower() in ['true', 'yes', 'y', 't', 'on', 'enable', 'enabled', 'checked', '1']
    except Exception as ex: # pylint: disable=broad-except
        logging.warning("{}Unable to parse '{}', so returning False: {}".format(
            fn_name,
            val,
            get_exception_msg(ex)))
        return False


def get_http_error_reason(http_err):
    """ Returns the reason text (if possible) for an HTTP error 
    
    Returns an empty string if there is no reason
    """
    
    reason = ''
    try:
        reason = http_err._get_reason() # pylint: disable=protected-access
    except: # pylint: disable=bare-except
        reason = ''
        
    return reason


def get_http_error_status(http_err):
    """ Returns the HTTP status (if possible) for an HTTP error.
    
    This is safer than calling http_err.resp.status, as there have been rare cases where 
    http_err.resp has been None, in whicjh case accessing http_err.resp.status raises an error.
    
    Returns -1 if there is no resp
    """
    
    status = -1
    try:
        status = http_err.resp.status
    except: # pylint: disable=bare-except
        status = -1
        
    return status


def log_job_state_summary(process_tasks_job):
    """ Logs a summary of the job status, start time and time since last update """
    
    job_start_timestamp = process_tasks_job.job_start_timestamp # UTC
    time_since_start = datetime.now() - job_start_timestamp
    time_since_last_update = datetime.now() - process_tasks_job.job_progress_timestamp
    seconds_since_last_update = time_since_last_update.total_seconds()
    
    pause_reason_str = ''
    if process_tasks_job.pause_reason:
        pause_reason_str = "\nPause reason = {}".format(process_tasks_job.pause_reason)
        
    waiting_to_continue_str = ''
    if process_tasks_job.is_waiting_to_continue:
        waiting_to_continue_str = " (waiting to continue)"
        
    logging.info(("Status = %s%s\n" +
        "Last job progress update was %s seconds ago at %s UTC\n" +
        "Job was started %s seconds ago at %s UTC%s"),
            process_tasks_job.status,
            waiting_to_continue_str,
            seconds_since_last_update,
            process_tasks_job.job_progress_timestamp,
            time_since_start.total_seconds(),
            job_start_timestamp,
            pause_reason_str)                    
    


def job_has_stalled(process_tasks_job):
    """ Returns True if the job has not been updated for at least MAX_JOB_PROGRESS_INTERVAL seconds """
    fn_name = "job_has_stalled: "
        
    if not process_tasks_job.status in constants.ImportJobStatus.STOPPED_VALUES:
        # Check if the job has stalled (no progress timestamp updates)
        
        time_since_last_update = datetime.now() - process_tasks_job.job_progress_timestamp
        seconds_since_last_update = time_since_last_update.total_seconds()
        
        if seconds_since_last_update > settings.MAX_JOB_PROGRESS_INTERVAL:
            logging.info("%sJob appears to have stalled", fn_name)
            log_job_state_summary(process_tasks_job)
            return True
            
    return False
