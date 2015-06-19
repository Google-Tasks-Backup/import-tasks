GTI allows users to import tasks into Google Tasks. It was developed 
specifically to import tasks in th GTBak format exported by GTB, but now
also supports importing from specifically formated CSV files,
  see https://import-tasks.appspot.com/static/info.html#import_export_csv
or by using a tool to export from MS Outlook.
  see https://import-tasks.appspot.com/static/instructions_import_from_outlook.html
  
=====================================

This project extends the tasks import functionality of google-tasks-porter.
It appears that support of google-tasks-porter stalled in the latter part of 2011.
In 2012, Julie Smith modified google-tasks-porter to create Google Tasks Backup,
which is now running at
    tasks-backup.appspot.com
and also created Google Tasks Import (this project) which is now running at
    import-tasks.appspot.com
    
There is a discussion group for GTI at groups.google.com/group/import-tasks

19 Jun 2015, Julie Smith
------------------------


If you wish to fork GTB and run it under your own appspot.com account, you will
need to create/modify the following files with your own values;

  Copy settings-dummy.py to settings.py and change (at least) the following values;
    url_discussion_group
    email_discussion_group
    url_issues_page    
    url_source_code
    TEST_ACCOUNTS
    PRODUCTION_SERVERS
    
  Copy host_settings-dummy.py to host_settings.py and follow the instructions
    in that file for obtaining and inserting your own client ID and secret values.

  Copy app-dummy.yaml to app.yaml and set values for 'version' and 'application'
  
  Set the 'version' and 'upload_timestamp' values in appversion.py
    This is the version information that is displayed to the user on every webpage.
    
  Change the Google Analytics ID in /templates/inc_google_analytics.html {line 4}
    If you do not wish to use Google Analytics, delete everything in that file
