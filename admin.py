# -*- coding: utf-8 -*-
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
# ============================================================================

"""Web application handler for admin tasks"""

__author__ = "julie.smith.1999@gmail.com (Julie Smith)"

import logging
import os
import datetime
import urllib

import webapp2


from google.appengine.api import urlfetch
from google.appengine.api import logservice # To flush logs
from google.appengine.api.app_identity import get_application_id
from google.appengine.ext.webapp import template
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers

from oauth2client.appengine import OAuth2Decorator


# Project-specific imports
import model
import settings
import appversion # appversion.version is set before the upload process to keep the version number consistent
import shared # Code which is common between classes, modules or projects
import constants
import host_settings

logservice.AUTOFLUSH_EVERY_SECONDS = 5
logservice.AUTOFLUSH_EVERY_BYTES = None
logservice.AUTOFLUSH_EVERY_LINES = 5
logservice.AUTOFLUSH_ENABLED = True

# Fix for DeadlineExceeded, because "Pre-Call Hooks to UrlFetch Not Working"
#     Based on code from https://groups.google.com/forum/#!msg/google-appengine/OANTefJvn0A/uRKKHnCKr7QJ
real_fetch = urlfetch.fetch # pylint: disable=invalid-name
def fetch_with_deadline(url, *args, **argv):
    argv['deadline'] = settings.URL_FETCH_TIMEOUT
    logservice.flush()
    return real_fetch(url, *args, **argv)
urlfetch.fetch = fetch_with_deadline


auth_err_msg = "Authorisation error. Please report this error to " + settings.url_issues_page # pylint: disable=invalid-name

auth_decorator = OAuth2Decorator( # pylint: disable=invalid-name
                                 client_id=host_settings.CLIENT_ID,
                                 client_secret=host_settings.CLIENT_SECRET,
                                 scope=host_settings.SCOPE,
                                 user_agent=host_settings.USER_AGENT,
                                 message=auth_err_msg)
                            

class DownloadStatsHandler(webapp2.RequestHandler):
    """Returns statistics as a CSV file"""

    @auth_decorator.oauth_required
    def get(self):

        fn_name = "DisplayStatsHandler.get(): "

        logging.debug(fn_name + "<Start> (app version %s)" % appversion.version )
        logservice.flush()
        
        stats_query = model.UsageStats.all()
        # stats_query.order('start_time')
        # stats_query.order('user_hash')
        stats = stats_query.run()
        
        try:
            stats_filename = "stats_" + get_application_id() + "_" + datetime.datetime.now().strftime("%Y-%m-%d") + ".csv"
              
            template_values = {'stats' : stats}
            self.response.headers["Content-Type"] = "text/csv"
            self.response.headers.add_header(
                "Content-Disposition", "attachment; filename=%s" % stats_filename)

                               
            path = os.path.join(os.path.dirname(__file__), constants.PATH_TO_TEMPLATES, "stats.csv")
            self.response.out.write(template.render(path, template_values))
            logging.debug(fn_name + "<End>" )
            logservice.flush()
            
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            
            self.response.headers["Content-Type"] = "text/html; charset=utf-8"
            try:
                # Clear "Content-Disposition" so user will see error in browser.
                # If not removed, output goes to file (if error generated after "Content-Disposition" was set),
                # and user would not see the error message!
                del self.response.headers["Content-Disposition"]
            except Exception, ex1: # pylint: disable=broad-except
                logging.debug(fn_name + "Unable to delete 'Content-Disposition' from headers (may not be a problem, because header may not have had it set): " + shared.get_exception_msg(ex1))
            self.response.clear() 
            
            self.response.out.write("""Oops! Something went terribly wrong.<br />%s<br />Please report this error to <a href="http://code.google.com/p/tasks-backup/issues/list">code.google.com/p/tasks-backup/issues/list</a>""" % shared.get_exception_msg(e))
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
    
    

        
class BulkDeleteBlobstoreHandler(webapp2.RequestHandler):
    """ List all blobstores, with option to delete each one """
    
    @auth_decorator.oauth_required
    def post(self):
        """ Delete a selection of Blobstores (selected in form, and posted """
        fn_name = "BulkDeleteBlobstoreHandler.post(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            self.response.out.write('<html><body>')
            blobstores_to_delete = self.request.get_all('blob_key')
            del_count = 0
            for blob_key in blobstores_to_delete:
                blob_info = blobstore.BlobInfo.get(blob_key)
                
                if blob_info:
                    try:
                        blob_info.delete()
                        del_count = del_count + 1
                    except Exception, e: # pylint: disable=broad-except
                        logging.exception(fn_name + "Exception deleting blobstore [" + str(del_count) + "] " + str(blob_key))
                        self.response.out.write("""<div>Error deleting blobstore %s</div>%s""" % (blob_key, shared.get_exception_msg(e)))
                else:
                    self.response.out.write("""<div>Blobstore %s doesn't exist</div>""" % blob_key)
                
            self.response.out.write('Deleted ' + str(del_count) + ' blobstores')
            self.response.out.write('<br /><br /><a href="' + settings.ADMIN_MANAGE_BLOBSTORE_URL + '">Back to Blobstore Management</a><br /><br />')
            self.response.out.write("""<br /><br /><a href=""" + settings.MAIN_PAGE_URL + """>Home page</a><br /><br />""")
            self.response.out.write('</body></html>')
            
            logging.debug(fn_name + "<End>")
            logservice.flush()
            
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()
            return

    

class ManageBlobstoresHandler(webapp2.RequestHandler):
    """ List all blobstores, with option to delete each one """
    
    @auth_decorator.oauth_required
    def get(self):
        fn_name = "ManageBlobstoresHandler.post(): "
        
        logging.debug(fn_name + "<Start>")
        logservice.flush()
        
        try:
            self.response.out.write("""
                <html>
                    <head>
                        <title>Blobstore management</title>
                        <link rel="stylesheet" type="text/css" href="/static/tasks_backup.css" />
                        <script type="text/javascript">
                            function toggleCheckboxes(source) {
                                checkboxes = document.getElementsByName('blob_key');
                                for(var i in checkboxes)
                                    checkboxes[i].checked = source.checked;
                            }
                        </script>
                    </head>
                    <body>
                        <br />""")
            
            if blobstore.BlobInfo.all().count(1) > 0:
                # There is at least one BlobInfo in the blobstore
                content_types_cnt = {}
                
                sorted_blob_infos = sorted(blobstore.BlobInfo.all(), key=lambda k: k.creation) 
                
                self.response.out.write('<div>Found ' + str(len(sorted_blob_infos)) +
                    ' blobstores</div><br />')
                
                self.response.out.write('<form method="POST" action = "' + settings.ADMIN_BULK_DELETE_BLOBSTORE_URL + '">')
                self.response.out.write('<table cellpadding="5">')
                self.response.out.write('<tr><th>Filename</th><th>Upload timestamp (PST?)</th><th>Size</th><th>Type</th><th colspan="3">Actions</th></tr>')
                self.response.out.write('<tr><td colspan="4">')
                self.response.out.write('<td style="white-space: nowrap"><input type="checkbox" onClick="toggleCheckboxes(this);" /> Toggle All</td></tr>')

                #for blob_info in blobstore.BlobInfo.all():
                for blob_info in sorted_blob_infos:
                    if blob_info.content_type in content_types_cnt:
                        content_types_cnt[blob_info.content_type] += 1
                    else:
                        content_types_cnt[blob_info.content_type] = 1
                    self.response.out.write('<tr>')
                    self.response.out.write('<td style="white-space: nowrap">' + blob_info.filename + 
                        '</td><td>' + str(blob_info.creation) +
                        '</td><td>' + str(blob_info.size) +
                        '</td><td>' + str(blob_info.content_type) +
                        '</td><td><input type="checkbox" name="blob_key" value="' + str(blob_info.key()) + '" ></td>')
                    self.response.out.write('</tr>')
                self.response.out.write('</table>')
                self.response.out.write('<div>')
                self.response.out.write('<h3>Totals:</h3>')
                for key, val in content_types_cnt.iteritems():
                    self.response.out.write('<p>{} {}</p>'.format(val, key))
                self.response.out.write('</div>')
                self.response.out.write("""<input type="submit" value="Delete selected blobstores" class="big-button" ></input>""")
                self.response.out.write('<form>')
            else:
                self.response.out.write("""<h4>No blobstores</h4>""")
            self.response.out.write("""<br /><br /><a href=""" + settings.MAIN_PAGE_URL + """>Home page</a><br /><br />""")
            self.response.out.write("""</body></html>""")
            
            logging.debug(fn_name + "<End>")
            logservice.flush()
            
        except Exception, e: # pylint: disable=broad-except
            logging.exception(fn_name + "Caught top-level exception")
            shared.serve_outer_exception_message(self, e)
            logging.debug(fn_name + "<End> due to exception" )
            logservice.flush()



class DeleteBlobstoreHandler(blobstore_handlers.BlobstoreDownloadHandler):
    """ Delete specified blobstore """
    
    @auth_decorator.oauth_required
    def get(self, blob_key):
        blob_key = str(urllib.unquote(blob_key))
        blob_info = blobstore.BlobInfo.get(blob_key)
        
        msg = ''
        if blob_info:
            try:
                blob_info.delete()
                self.redirect(settings.MAIN_PAGE_URL)
                return
            except Exception, e: # pylint: disable=broad-except                
                msg = """Error deleting blobstore %s<br />%s""" % (blob_key, shared.get_exception_msg(e))
        else:
            msg = """Blobstore %s doesn't exist""" % blob_key
        
        self.response.out.write('<html><body>')
        self.response.out.write(msg)
        self.response.out.write('<br /><br /><a href="' + settings.MAIN_PAGE_URL + '">Home</a><br /><br />')
        self.response.out.write('</body></html>')

        


app = webapp2.WSGIApplication( # pylint: disable=invalid-name
    [
        (settings.ADMIN_MANAGE_BLOBSTORE_URL,       ManageBlobstoresHandler),
        (settings.ADMIN_DELETE_BLOBSTORE_URL,       DeleteBlobstoreHandler),
        (settings.ADMIN_BULK_DELETE_BLOBSTORE_URL,  BulkDeleteBlobstoreHandler),
        (settings.ADIMN_RETRIEVE_STATS_CSV_URL,     DownloadStatsHandler),
    ], debug=False)
