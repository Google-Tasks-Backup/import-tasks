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
#
# Portions of this code are from Dwight Guth's Google Tasks Porter

# This module contains code whis is common between classes, modules or related projects
# Can't use the name common, because there is already a module named common


# Fix for DeadlineExceeded, because "Pre-Call Hooks to UrlFetch Not Working"
#     Based on code from https://groups.google.com/forum/#!msg/google-appengine/OANTefJvn0A/uRKKHnCKr7QJ
from google.appengine.api import urlfetch
real_fetch = urlfetch.fetch
def fetch_with_deadline(url, *args, **argv):
    argv['deadline'] = settings.URL_FETCH_TIMEOUT
    return real_fetch(url, *args, **argv)
urlfetch.fetch = fetch_with_deadline

import webapp2

from oauth2client import appengine
from google.appengine.api import users
from google.appengine.api import logservice # To flush logs
from google.appengine.ext import db
from google.appengine.api import memcache
from google.appengine.ext.webapp import template

from oauth2client import client
from apiclient import discovery

# Import from error so that we can process HttpError
from apiclient import errors as apiclient_errors
from google.appengine.api import urlfetch_errors

import httplib2
import Cookie
import cgi
import sys
import os
import traceback
import logging
import pickle
import time


# Project-specific imports
import model
import settings
import constants
import appversion # appversion.version is set before the upload process to keep the version number consistent
import host_settings


logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True

class DailyLimitExceededError(Exception):
    """ Thrown by get_credentials() when HttpError indicates that daily limit has been exceeded """
    
    msg = "Daily limit exceeded. Please try again after midnight Pacific Standard Time."
    
    def __init__(self, msg = None):
        if msg:
            self.msg = msg
            
    def __repr__(self):
        return msg;
    

def set_cookie(res, key, value='', max_age=None,
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
    logging.debug("Deleting cookie: " + str(key))
    set_cookie(res, key, '', -1)
    
  
def format_exception_info(maxTBlevel=5):
    cla, exc, trbk = sys.exc_info()
    excName = cla.__name__
    try:
        excArgs = exc.__dict__["args"]
    except KeyError:
        excArgs = "<no args>"
    excTb = traceback.format_tb(trbk, maxTBlevel)
    return (excName, excArgs, excTb)
         

def get_exception_name():
    cla, exc, trbk = sys.exc_info()
    excName = cla.__name__
    return str(excName)
         

# def get_exception_msg(msg):
    # cla, exc, trbk = sys.exc_info()
    # excName = cla.__name__
    # return str(excName) + ": " + str(msg)
  
def get_exception_msg(e = None):
    """ Return string containing exception type and message
    
        args:
            e       [OPTIONAL] An exception type
            
        If e is specified, and is of an Exception type, this method returns a 
        string in the format "Type: Msg" 
        
        If e is not specified, or cannot be parsed, "Type: Msg" is
        returned for the most recent exception
    """
        
    # Store current exception msg, in case building msg for e causes an exception
    cla, exc, trbk = sys.exc_info()
    if cla:
        excName = cla.__name__
        ex_msg = unicode(excName) + ": " + unicode(exc.message)
    else:
        ex_msg = "No exception occured"
    
    if e:
        msg = ''
        try:
            msg = unicode(e)
            excName = e.__class__.__name__
            
            return str(excName) + ": " + msg
            
        except Exception, e1:
            # Unable to parse passed-in exception 'e', so returning the most recent
            # exception when this method was called
            return "Most recent exception = " + ex_msg + " (" + msg + ")"
            
    else:
         return ex_msg           

         
def is_test_user(user_email):
    """ Returns True if user_email is one of the defined settings.TEST_ACCOUNTS 
  
        Used when testing to ensure that only test user's details and sensitive data are logged.
    """
    return (user_email.lower() in (email.lower() for email in settings.TEST_ACCOUNTS))
  

def dump_obj(obj):
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
    return cgi.escape(text).encode('ascii', 'xmlcharrefreplace').replace('\n','<br />')
    #return cgi.escape(text.decode('unicode_escape')).replace('\n', '<br />')
    #return "".join(html_escape_table.get(c,c) for c in text)

    
def serve_quota_exceeded_page(self):
    msg1 = "Daily limit exceeded"
    msg2 = "The daily quota is reset at midnight Pacific Standard Time (5:00pm Australian Eastern Standard Time, 07:00 UTC)."
    msg3 = "Please rerun " + host_settings.APP_TITLE + " any time after midnight PST to continue importing your file."
    serve_message_page(self, msg1, msg2, msg3)
    
    
def serve_message_page(self, 
        msg1, msg2 = None, msg3 = None, 
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
        logging.debug(fn_name + "<End>" )
        logservice.flush()
    except Exception, e:
        logging.exception(fn_name + "Caught top-level exception")
        serve_outer_exception_message(self, e)
        logging.debug(fn_name + "<End> due to exception" )
        logservice.flush()
    

def serve_outer_exception_message(self, e):
    """ Display an Oops message when something goes very wrong. 
    
        This is called from the outer exception handler of major methods (such as get/post handlers)
    """
    fn_name = "serve_outer_exception_message: "
    
    self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br /><br />This system is in beta, and is being activeley developed.<br />Please report any errors to <a href="http://%s">%s</a> so that they can be fixed. Thank you.""" % 
        ( get_exception_msg(e), settings.url_issues_page, settings.url_issues_page))
        
        
    logging.error(fn_name + get_exception_msg(e))
    logservice.flush()
    
    
def reject_non_test_user(self):
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
                    
                    
                    
