<a href='Hidden comment: 
This page shows revisions that affect the user. For full source code revision notes, refer to the commit log messages at http://code.google.com/p/import-tasks/source/list or use hg.
'></a>

## Google Tasks Import version history ##


---


#### 0.4.151 (2013-06-05 14:11) ####

Usability improvements;
  * Retain original name of default tasklist (if possible)
    * When deleting all tasklists before import, or attempting to replace the contents of the default tasklist, GTI was renaming the default tasklist to "Undeletable default nnnnnnnnnn"
    * Now, GTI tries to keep the original name of the default tasklist, unless the import created a new tasklist with the same name. In that case, the default tasklist will still be renamed "Undeletable default nnnnnnnnnn"
  * Attempt to prevent the very occasional `HttpError 400 "Invalid value"` error when creating a tasklist.
    * This appeared to be due to incorrectly formatted data in the import file, which results in an apparently invalid tasklist name.
    * The most likely cause is missing or unpaired double-quotes, which means that the entire task is being interpreted as the tasklist name. This exceeds the maximum 256 character tasklist name length, thus causing a 400 "Invalid value" error.
    * Now GTI checks for tasklist name longer than 256 characters and reports that immediately to the user.



---


#### 0.4.077 (2013-04-24 18:47) ####
Info;
  * Removed warning about out-of-order or duplicate tasks
    * It appears that Google have finally fixed the tasks sorting algorithm. As at 2013-04-23, importing works correctly for very large tasklists, and has been tested successfully with 20,000 tasks. Previously, lists with more than 600-800 tasks would end up with tasks out of order and/or duplicate tasks

Bug fixes, code changes;
  * Fixed occasional `TypeError: object of type 'int' has no len()`
  * Other background coding changes. Refer to `[Changes since last commit.txt](https://code.google.com/p/import-tasks/source/browse/Changes+since+last+commit.txt)' in the [source](https://code.google.com/p/import-tasks/source/browse/)




---

#### 0.4.024 (2013-04-19 21:47) ####
Usability improvements;
  * Improved message to user if file has invalid line terminators
  * Now checks for invalid depth values as soon as file is uploaded. This means that potentially invalid depth values are displayed immediately to the user. (Was previously only checking depth as each task was uploaded to server, which could take some time for large lists).

Technical;
  * Updated to use Python 2.7 and webapp2, because Google will deprecate Python 2.5 on App Engine in 2014


---

#### 0.3.105 (2013-02-19 17:19) ####
UI improvement;
  * Display prompt to user if file doesn't end with Windows-style CR/LF line ending
  * Added CR/LF requirement to information page


---

#### 2013-01-15 03:37, beta 0.3.091 ####
Bug fixes;
  * Fix _TypeError: 'int' object is not callable_
  * Fix _AttributeError: 'module' object has no attribute 'WORKER\_API\_RETRY\_SLEEP\_DURATION'_


---


#### 2013-01-08 00:48, beta 0.3.088 ####
Bug fix;
  * Fix _global name 'AccessTokenRefreshError' is not defined_


---


#### 2013-01-04 00:03, beta 0.3.082 ####
Minor bug fixes;
  * Try to improve handling of backend (Google server) issues.
  * Improvements to wording of some messages to users


---


#### 2012-11-27 01:44, beta 0.2.328 ####
Minor bug fix;
  * Fix issues page URL at the bottom of web pages

Other;
  * Display "...cannot directly import an Outlook export file..." message if Outlook export file is uploaded, with a link to a page with instructions for exporting from Outlook.
    * GTI doesn't import from Outlook export files, because those files do not support international characters.
    * Use the utilities at http://import-tasks.appspot.com/static/instructions_import_from_outlook.html to export tasks from Outlook in a format suitable for importing using GTI, with full support for international and extended characters.



---


#### 0.2.320 (2012-11-17 02:42) ####

Minor bug fixes;
  * Fixed bug where invalid 'hidden' or 'deleted' value displayed the value of 'status' instead
  * Fixed `TypeError: object of type 'NoneType'`
  * Fixed robots.txt handler to return "allow all" for production server

Other;
  * Display invalid field values using fixed font, to make it clearer  what the invalid value was
  * Allow blank string as depth (interpreted as depth=0)
  * Allow `'True'`, `'true'`, `'False'`, `'false'` or blank for 'hidden' and 'deleted' fields


---


#### 0.2.312 (2012-11-12 00:05) ####

Minor bug fixes;
  * File upload Browse field now fits on screen when running Firefox on smaller screens.
  * Fixed bug where the number of data rows being processed was not always displaying correctly on the progress page.


---


#### 0.2.295 (2012-11-10 04:09) ####

Bug fix;
  * Fixed bug in 'Skip duplicate tasklists' method where tasks after the skipped tasklist were not imported

Other;
  * Non-ASCII (Unicode) filenames now displayed correctly
  * Improved feedback on errors


---


#### 0.2.227 (2012-11-05 04:50) ####

Enhancements;
  * There are now 2 ways of exporting tasks from Outlook in a format suitable for importing using Google Tasks Import ([Issue 2](https://code.google.com/p/import-tasks/issues/detail?id=2)).
    * Go to https://import-tasks.appspot.com/static/instructions_import_from_outlook.html for details and a link to download the application.
  * Much wider range of supported date/time formats for 'due' and 'completed' date formats
    * The 'UTC' prefix is now optional
    * Date separators may now also be period (i.e. both YYYY-MM-DD and YYYY.MM.DD are valid)
  * Can now import tasks from a CSV with a minimal set of columns;
    * 'tasklist\_name','title','status'
  * Can import tasks into the default tasklist by using '@default' as the tasklist name

Bug fixes;
  * Added code to handle "404 Not Found" errors from Google Tasks server ([issue 1](https://code.google.com/p/import-tasks/issues/detail?id=1), [issue 3](https://code.google.com/p/import-tasks/issues/detail?id=3), [issue 4](https://code.google.com/p/import-tasks/issues/detail?id=4))
  * Changed authentication handling to prevent 'invalid\_grant' errors ([issue 4](https://code.google.com/p/import-tasks/issues/detail?id=4))
  * Improved server timeout handling

Other;
  * Improved handling of dates before 1900
  * Added special handling of '0001-01-01 00:00:00' completed date
    * This value appears to be used by some third-party tasks apps
  * Updated icon
  * Many minor code and usability tweaks. Refer to 'Changes since last commit.txt' in the source