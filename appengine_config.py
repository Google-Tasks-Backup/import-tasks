#
# Copyright 2011 Google Inc.  All Rights Reserved.
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

# Original __author__ = "dwightguth@google.com (Dwight Guth)"

"""Google Tasks Import configuration file."""

__author__ = "julie.smith.1999@gmail.com (Julie Smith)"


from google.appengine.ext.webapp import template

template.register_template_library("common.customdjango")


# Start FIX1: 2012-06-16
# This fixes mangled unicode in form input when POSTED to blobstore_handlers.BlobstoreUploadHandler,
# but does not fix the mangled filename in blob_info.filename
#     From http://code.google.com/p/googleappengine/issues/detail?id=2749#c21

import base64
import quopri

from webob import multidict


def from_fieldstorage(cls, fs):
    """
    Create a dict from a cgi.FieldStorage instance
    """
    obj = cls()
    if fs.list:
        # fs.list can be None when there's nothing to parse
        for field in fs.list:
            if field.filename:
                obj.add(field.name, field)
            else:

                # first, set a common charset to utf-8.
                common_charset = 'utf-8'

                # second, check Content-Transfer-Encoding and decode
                # the value appropriately
                field_value = field.value
                transfer_encoding = field.headers.get(
                  'Content-Transfer-Encoding', None)

                if transfer_encoding == 'base64':
                    field_value = base64.b64decode(field_value)

                if transfer_encoding == 'quoted-printable':
                    field_value = quopri.decodestring(field_value)

                if field.type_options.has_key('charset') and \
                      field.type_options['charset'] != common_charset:
                    # decode with a charset specified in each
                    # multipart, and then encode it again with a
                    # charset specified in top level FieldStorage
                    field_value = field_value.decode(
                      field.type_options['charset']).encode(common_charset)

                # JS 2012-09-15: Indented as suggested by http://code.google.com/p/googleappengine/issues/detail?id=2749#c54
                # TODO: Should we take care of field.name here?
                obj.add(field.name, field_value)

    return obj

multidict.MultiDict.from_fieldstorage = classmethod(from_fieldstorage)

# End FIX1

