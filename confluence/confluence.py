#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from requests.auth import HTTPBasicAuth

import copy
import functools
import json
import logging
import os
import re
import requests
import ssl
import sys
import warnings

try:
    import xmlrpclib
except ImportError:
    import xmlrpc.client as xmlrpclib

try:
    import ConfigParser
except ImportError:
    import configparser as ConfigParser

try:  # Python 2.7+
    from logging import NullHandler
except ImportError:
    class NullHandler(logging.Handler):
        """Returns a new instance of the NullHandler class."""

        def emit(self, record):
            """This method does nothing."""
            pass

logging.getLogger('confluence').addHandler(NullHandler())


def deprecate_xmlrpc_notification(func):
    """Decorator for xmlrpc deprecation warnings."""

    @functools.wraps(func)
    def new_func(*args, **kwargs):
        # warnings.simplefilter('always', PendingDeprecationWarning)  # Un-comment this line to always display warnings.
        warnings.warn(
            "{} will be rewritten to use the Atlassian REST API in future versions.\n"
            "This change will introduce changes in the module method responses.".format(func.__name__),
            category=PendingDeprecationWarning,
            stacklevel=2)
        warnings.simplefilter('default', PendingDeprecationWarning)
        return func(*args, **kwargs)

    return new_func


class Confluence(object):
    DEFAULT_OPTIONS = {
        "server": "http://localhost:8090",
        "verify": True
    }

    def __init__(self, profile=None, url="http://localhost:8090/", username=None, password=None, appid=None):
        """
        Returns a Confluence object by loading the connection details from the `config.ini` file.

        :param profile: The name of the section from config.ini file that stores server config url/username/password
        :type  profile: ``str``

        :param url: URL of the Confluence server
        :type  url: ``str``

        :param username: username to use for authentication
        :type  username: ``str``

        :param password: password to use for authentication
        :type  password: ``str``

        :return: Confluence -- an instance to a Confluence object.
        :raises: EnvironmentError

        Usage:

            >>> from confluence import Confluence
            >>>
            >>> conf = Confluence(profile='confluence')
            >>> conf.get_page("ds","Welcome to Confluence")

        Also create a `config.ini` like this and put it in current directory, user home directory or PYTHONPATH.

        .. code-block:: nonek

            [confluence]
            url=https://confluence.atlassian.com
            # only the `url` is mandatory
            user=...
            pass=...

        """
        self.logging = logging

        def find_file(path):
            """
            Find the file named path in the sys.path.
            Returns the full path name if found, None if not found
            """
            paths = [os.getcwd(), '.', os.path.expanduser('~')]
            paths.extend(sys.path)
            for dir_name in paths:
                possible = os.path.abspath(os.path.join(dir_name, path))
                if os.path.isfile(possible):
                    return possible
            return None

        config = ConfigParser.ConfigParser(defaults={'user': username, 'pass': password, 'appid': appid})

        config_file = find_file('config.ini')
        if config_file:
            logging.debug("Found %s config file" % config_file)

        if not profile:
            if config_file:
                config.read(config_file)
                try:
                    profile = config.get('general', 'default-confluence-profile')
                except ConfigParser.NoOptionError:
                    pass

        if profile:
            if config_file:
                config.read(config_file)
                url = config.get(profile, 'url')
                username = config.get(profile, 'user')
                password = config.get(profile, 'pass')
                appid = config.get(profile, 'appid')
            else:
                raise EnvironmentError(
                    '%s was not able to locate the config.ini file.'
                    'Place config.ini in current directory, user home directory or PYTHONPATH.' % __name__)

        options = Confluence.DEFAULT_OPTIONS
        options['server'] = url
        options['username'] = username
        options['password'] = password

        self.base_url = url

        # Set the base URL for REST calls.
        self.rest_url = self.base_url + '/rest/api/'

        # Create a request session.
        self.connection = requests.Session()
        self.connection.auth = HTTPBasicAuth(options['username'], options['password'])

        if not self.connection_valid():
            self.logging.critical('Connection is None.')

        self.logging.debug("Instantiated Confluence object.")

    def connection_valid(self):
        """Checks if a connection to the Confluence server can be established.

        :rtype: Boolean
        """
        if self.connection is None:
            return False
        try:
            result = self.connection.get(self.rest_url + 'content')
            result.raise_for_status()

        except requests.exceptions.RequestException as err:
            self.logging.exception('Connection Error: {err}'.format(err=err))
            print(err)
            raise err

        if not result.ok:
            self.connection = None
        return self.connection is not None

    def check_response(self, response):
        """Checks if the response from REST calls is valid.

        :rtype Boolean.
        """

        if response is None:
            self.logging.debug('Retrieved a NoneObject')
            return response is not None

        elif not response.ok:
            self.logging.debug('Response not ok: {}'.format(response.reason))
            return response.ok
        else:
            try:
                len(response.json()['results'][0]) < 1
            except IndexError:  # exception caused by lists of 0 length
                self.logging.debug('Retrieved an empty list:\n'
                                   'Request: {}'.format(response.request.url))
                return False

        return response is not None

    def get_user_by_key(self, key, timeout=10):
        request = self.rest_url + 'user?key={key}'.format(key=key)

        response = self.connection.get(request, timeout=timeout)
        if not response.ok:
            self.logging.debug('Response check failed, returning None')
            response = None
            return response
        else:
            response = response.json()

        return response['username']

    def get_page(self, space, page, full=False, timeout=10):
        """Returns a page object as a dictionary.

        :param page: The page name
        :type  page: ``str``

        :param space: The space name
        :type  space: ``str``

        :param full: Return pretty results or full JSON response.
        :type  full: ``bool``

        :param timeout: Timeout in seconds
        :type  timeout: ``int`` or ``float``

        :return: dictionary. result['content'] contains the body of the page.
        """
        request = self.rest_url + 'content?&spaceKey={space}&title={page}&expand=body.storage,version,history'.format(
            space=space, page=page
        )

        response = self.connection.get(request, timeout=timeout)
        if not response.ok:
            self.logging.debug('Response check failed, returning None')
            response = None
            return response
        else:
            response = response.json()

        if full:
            self.logging.info('Returning full JSON response.')
            return response

        base_url = response['_links']['base']
        response = response['results'][0]
        clean_results = {
            'content': response.get('body').get('storage').get('value'),
            'status': response.get('status'),
            'created': response.get('history').get('createdDate'),
            'creator': response.get('history').get('createdBy').get('username'),
            'current': response.get('history').get('latest'),
            'id': response.get('id'),
            'modified': response.get('version').get('when'),
            'modifier': response.get('version').get('by').get('username'),
            'space': space,
            'title': response.get('title'),
            'url': base_url + response.get('_links').get('webui'),
            'version': str(response.get('version').get('number'))
        }

        self.logging.info('Returning pretty response.')
        return clean_results

    @deprecate_xmlrpc_notification
    def getPage(self, page, space):
        """Deprecated method. Use get_page()

        :returns new method, get_page().
        """
        logging.info('Call to deprecated method, returning new method.')
        return self.get_page(space, page)

    def get_pages(self, space, limit=None, full=False, timeout=10):
        """
        Gets all pages in a space. Returns full REST response as JSON object.

        :param space: The space name.
        :type  space: ``str``

        :param limit: The maximum number of pages returned.
        :type  limit: ``int``

        :param full: Return pretty results or full JSON response.
        :type  full: ``bool``

        :param timeout: Timeout in seconds
        :type  timeout: ``int`` or ``float``

        :return:
        """
        request = self.rest_url + 'content?spaceKey={space}&expand=version'.format(
            space=space
        )
        if limit:
            request = request + '&limit={limit}'.format(limit=limit)

        response = self.connection.get(request, timeout=timeout)
        if not self.check_response(response):
            self.logging.debug('Response check failed, returning None')
            response = None
            return response
        else:
            response = response.json()

        if full:
            self.logging.info('Returning full JSON response.')
            return response

        base_url = response['_links']['base']
        clean_results = [
            {'id': entry['id'],
             # 'parent': entry['ancestors'],  # TODO get ancestors working
             'space': space,
             'title': entry['title'],
             'url': base_url + entry['_links']['webui'],
             'version': str(entry['version']['number'])}
            for entry in response['results']]

        self.logging.info('Returning pretty response.')
        return clean_results

    @deprecate_xmlrpc_notification
    def getPages(self, space):
        """Deprecated method. Use get_pages().

        :return: New method: get_pages().
        """
        logging.info('Call to deprecated method, returning new method.')
        return self.get_pages(space)

    @deprecate_xmlrpc_notification
    def getPageId(self, page, space):
        """Deprecated method. Use get_page_id()

        :returns new method: get_page_id()
        """
        logging.info('Call to deprecated method, returning new method.')
        return self.get_page_id(space, page)

    def get_page_id(self, space, page, content_type=None, timeout=10):
        """
        Returns the numeric id of a Confluence page or blog post.

        :param space: The space name.
        :type  space: ``str``

        :param page: The page name.
        :type  page: ``str``

        :param content_type: Confluence content type. Defaults to 'page'.
                             Possible values are 'page' and 'blogpost'.
        :type  content_type: ``str``

        :param timeout: Timeout in seconds.
        :type  timeout: ``int`` or ``float``

        :rtype ``int``
        :return: Page numeric id, or None if lookup fails.
        """
        request = self.rest_url + 'content?spaceKey={space}&title={page}'.format(
            space=space, page=page
        )
        self.logging.debug('get_page_id request is:\n {request}'.format(request=request))
        if content_type is not None:
            request = request + '&type={}'.format(content_type.lower())
        response = self.connection.get(request, timeout=timeout)

        if not self.check_response(response):
            response = None
        else:
            response = int(response.json()['results'][0]['id'])

        return response

    @deprecate_xmlrpc_notification
    def getSpaces(self):
        """Deprecated method. Use get_spaces().

        :returns new method: get_spaces().
        """
        logging.info('Call to deprecated method, returning new method.')
        return self.get_spaces(limit=1000)

    def get_spaces(self, limit=None, full=False, timeout=10):
        """Get all spaces on the server.

        :param limit: The maximum number of pages returned.
                        Default is None, and gets the server default limit.
        :type  limit: ``int``

        :param full: Return pretty results or full JSON response.
        :type  full: ``bool``

        :param timeout: Timeout in seconds
        :type  timeout: ``int`` or ``float``

        :rtype:
                full=False: ``list``
                full=True: ``dict``
        """
        request = self.rest_url + 'space'
        if limit:
            request = request + '&limit={limit}'.format(limit=limit)
        response = self.connection.get(request, timeout=timeout)
        if not self.check_response(response):
            logging.debug('Response check failed, returning None')
            response = None
            return response
        else:
            response = response.json()

        if full:
            logging.info('Returning full JSON response.')
            return response

        base_url = response['_links']['base'] + '/display/'
        clean_results = [
            {'key': entry['key'],
             'name': entry['name'],
             'type': entry['type'],
             'url': base_url + entry['key']}
            for entry in response['results']]

        logging.info('Returning pretty response.')
        return clean_results

    @deprecate_xmlrpc_notification
    def getBlogEntry(self, pageId):
        """
        Returns a blog page as a BlogEntry object.

        :param pageId:
        """
        return self.get_blog_entry(pageId)

    def get_blog_entry(self, space, title, post_date=None, full=False, timeout=10):
        """Get a blog page from the server.

        :param space: The space containing the blog.
        :type  space: ``str``

        :param title: The blog title.
        :type  title: ``str``

        :param post_date: The date the blog was posted.
        :type  post_date: ``str`` in date format: 1970-01-31

        :param timeout: Timeout in seconds.
        :type  timeout: ``int`` or ``float`

        :rtype: ``dict``
        """
        if '~' in space:  # make sure we get spaces containing tilde character, for personal spaces
            logging.debug('Replacing ~ in space name with &#126.')
            space = space.replace('~', '&#126')
        request = self.rest_url + 'content?type=blogpost&spaceKey={space}&title={title}' \
                                  '&expand=space,history,body.view,version' \
            .format(space=space, title=title)
        if post_date:
            request = request + '&postingDay={post_date}'.format(post_date=post_date)

        logging.debug('Sending request: {request}'.format(request=request))
        response = self.connection.get(request, timeout=timeout)
        logging.debug('Got response:\n{response}'.format(response=response.json()))

        # if not self.check_response(response):
        #     logging.debug('Response check failed, returning None')
        #     response = None
        #     return response

        response = response.json()

        if full:
            logging.info('Returning full JSON response.')
            return response

        return response  # TODO: Figure out why full=None causes check_response() to fail

        # base_url = response['_links']['base']
        # response = response['results'][0]
        # clean_results = {
        #     'author': response.get('history').get('createdBy').get('username'),
        #     'content': response.get('body').get('storage').get('value'),
        #     'id': response.get('id'),
        #     'created': response.get('history').get('createdDate'),
        #     'space': space,
        #     'title': response.get('title'),
        #     'url': base_url + response.get('_links').get('webui'),
        #     'version': str(response.get('version').get('number'))
        # }

        # TODO: Add pretty response:

        # logging.info('Returning pretty response.')
        # return clean_results

    @deprecate_xmlrpc_notification
    def getBlogEntries(self, space):
        """
        Returns a page object as a Vector.

        :param space: The space name
        :type  space: ``str``
        """
        pass

    def get_blog_entries(self, space, full=False, timeout=10):
        """Get a list of blogposts in a space.

        :param space: The space name
        :type  space: ``str``

        :param full: Return pretty results or full JSON response.
        :type  full: ``bool``

        :param timeout: Timeout in seconds
        :type  timeout: ``int`` or ``float``

        :rtype: TODO missing rtype.
        """

        #  content?type=blogpost&start=0&limit=10&expand=space,history,body.view,metadata.labels
        pass

    @deprecate_xmlrpc_notification
    def getAttachments(self, page, space):
        """
        Returns a attachments as a dictionary.

        :param page: The page name
        :type  page: ``str``

        :param space: The space name
        :type  space: ``str``

        :return: dictionary. result['content'] contains the body of the page.
        """
        if self._token2:
            server = self._server.confluence2
            token = self._token2
        else:
            server = self._server.confluence1
            token = self._token1
        existing_page = server.getPage(token, space, page)
        try:
            attachments = server.getAttachments(token, existing_page["id"])
        except xmlrpclib.Fault:
            logging.info("No existing attachment")
            attachments = None
        return attachments

    @deprecate_xmlrpc_notification
    def getAttachedFile(self, page, space, fileName):
        """
        Returns a attachment data as byte[].

        :param page: The page name
        :type  page: ``str``

        :param space: The space name
        :type  space: ``str``

        :param fileName: The attached file name
        :type  fileName: ``str``
        """
        if self._token2:
            server = self._server.confluence2
            token = self._token2
        else:
            server = self._server.confluence1
            token = self._token1
        existing_page = server.getPage(token, space, page)
        try:
            DATA = server.getAttachmentData(token, existing_page["id"], fileName, "0")
        except xmlrpclib.Fault:
            logging.info("No existing attachment")
            DATA = None
        return DATA

    @deprecate_xmlrpc_notification
    def attachFile(self, page, space, files):
        """
        Attach (upload) a file to a page

        :param page: The page name
        :type  page: ``str``

        :param space: The space name
        :type  space: ``str``

        :param files: The files to upload
        :type  files: ``dict`` where `key` is filename and `value` is the comment.
        """
        if self._token2:
            server = self._server.confluence2
            token = self._token2
        else:
            server = self._server.confluence1
            token = self._token1
        existing_page = server.getPage(token, space, page)
        for filename in files.keys():
            try:
                server.removeAttachment(token, existing_page["id"], filename)
            except xmlrpclib.Fault:
                logging.info("No existing attachment to replace")
            content_types = {
                "gif": "image/gif",
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "pdf": "application/pdf",
            }
            extension = os.path.splitext(filename)[1:]
            ty = content_types.get(extension, "application/binary")
            attachment = {"fileName": filename, "contentType": ty, "comment": files[filename]}
            f = open(filename, "rb")
            try:
                byts = f.read()
                logging.info("calling addAttachment(%s, %s, %s, ...)", token, existing_page["id"], repr(attachment))
                server.addAttachment(token, existing_page["id"], attachment, xmlrpclib.Binary(byts))
                logging.info("done")
            except xmlrpclib.Error:
                logging.exception("Unable to attach %s", filename)
            finally:
                f.close()

    @deprecate_xmlrpc_notification
    def storeBlogEntry(self, entry):
        """
        Store or update blog content.
        (The BlogEntry given as an argument should have space, title and content fields at a minimum.)

        :param entry: Blog entry
        :type  entry: ``str``

        :rtype: ``bool``
        :return: `true` if succeeded
        """
        if self._token2:
            blogEntry = self._server.confluence2.storeBlogEntry(self._token2, entry)
        else:
            blogEntry = self._server.confluence1.storeBlogEntry(self._token2, entry)
        return blogEntry

    @deprecate_xmlrpc_notification
    def addLabelByName(self, labelName, objectId):
        """
        Adds label(s) to the object.

        :param labelName: Tag Name
        :type  labelName: ``str``

        :param objectId: Such as pageId
        :type  objectId: ``str``

        :rtype: ``bool``
        :return: True if succeeded
        """
        if self._token2:
            ret = self._server.confluence2.addLabelByName(self._token2, labelName, objectId)
        else:
            ret = self._server.confluence1.addLabelByName(self._token, labelName, objectId)
        return ret

    @deprecate_xmlrpc_notification
    def movePage(self, sourcePageIds, targetPageId, space, position='append'):
        """
        Moves sourcePage to be a child of targetPage by default.  Modify 'position' to either 'above' or 'below'
        to make sourcePage a sibling of targetPage, and place it either directly above or directly below targetPage
        in the hierarchy, respectively.

        :param sourcePageIds: List of pages to be moved to targetPageId
        :param targetPageId: Name or PageID of new parent or target sibling
        :param position: Defaults to move page to be child of targetPageId \
                         Setting 'above' or 'below' instead sets sourcePageId \
                         to be a sibling of targetPageId of targetPageId instead
        """

        if not targetPageId.isdigit():
            targetPageId = self.getPageId(targetPageId, space)
        for sourcePageId in sourcePageIds:
            if not sourcePageId.isdigit():
                sourcePageId = self.getPageId(sourcePageId, space)

            if self._token2:
                self._server.confluence2.movePage(self._token2,
                                                  str(sourcePageId), str(targetPageId), position)
            else:
                self._server.confluence1.movePage(self._token2,
                                                  str(sourcePageId), str(targetPageId), position)

    @deprecate_xmlrpc_notification
    def storePageContent(self, page, space, content, convert_wiki=True, parent_page=None):
        """
        Modifies the content of a Confluence page.

        :param page: The page name
        :type  page: ``str``

        :param space: The space name
        :type  space: ``str``

        :param content: The page content (wiki markup or rich text)
        :type  content: ``str``

        :param convert_wiki: Convert content as wiki markup before updating
        :type  convert_wiki: ``bool``

        :param parent_page: The parent page name (optional)
        :type  parent_page: ``str``

        :rtype: ``bool``
        :return: `True` if succeeded
        """

        try:
            data = self.getPage(page, space)
        except xmlrpclib.Fault:
            data = {
                "space": space,
                "title": page
            }

        data['content'] = content

        if parent_page:
            parent_id = self.getPageId(parent_page, space)
            data["parentId"] = parent_id

        if self._token2:
            if convert_wiki:
                content = self._server.confluence2.convertWikiToStorageFormat(self._token2, content)
            data['content'] = content
            return self._server.confluence2.storePage(self._token2, data)
        else:
            return self._server.confluence1.storePage(self._token, data)

    @deprecate_xmlrpc_notification
    def renderContent(self, space, page, a='', b={'style': 'clean'}):
        """
        Obtains the HTML content of a wiki page.

        :param page: The page name
        :type  page: ``str``

        :param space: The space name
        :type  space: ``str``

        :return: string: HTML content
        """

        try:
            if not page.isdigit():
                page = self.getPageId(page=page, space=space)
            if self._token2:
                return self._server.confluence2.renderContent(self._token2, space, page, a, b)
            else:
                return self._server.confluence1.renderContent(self._token, space, page, a, b)
        except ssl.SSLError as err:
            logging.error("%s while retrieving page %s", err, page)
            return None
        except xmlrpclib.Fault as err:
            logging.error("Failed call to renderContent('%s','%s') : %d : %s", space, page, err.faultCode,
                          err.faultString)
            raise err

    @deprecate_xmlrpc_notification
    def convertWikiToStorageFormat(self, markup):
        """
        Converts a wiki text to it's XML/HTML format. Useful if you prefer to generate pages using wiki syntax instead of XML.

        Still, remember that once you cannot retrieve the original wiki text, as confluence is not storing it anymore. \
        Due to this wiki syntax is usefull only for computer generated pages.

        Warning: this works only with Conflucence 4.0 or newer, on older versions it will raise an error.

        :param markup: The wiki markup
        :type  markup: ``str``

        :rtype: ``str``
        :return: the text to store (HTML)
        """
        if self._token2:
            return self._server.confluence2.convertWikiToStorageFormat(self._token2, markup)
        else:
            return self._server.confluence.convertWikiToStorageFormat(self._token2, markup)

    def convert_wiki_to_storage_format(self):
        """
        POST -H 'Content-Type: application/json' -d'
        {
        "value":"{cheese}",
        "representation":"wiki"
        }'
        https://your-domain.atlassian.net/wiki/rest/api/contentbody/convert/storage
        """
        pass

    @deprecate_xmlrpc_notification
    def getPagesWithErrors(self, stdout=True, caching=True):
        """
        Get pages with formatting errors
        """
        result = []
        cnt = 0
        cnt_err = 0
        stats = {}
        data = {}
        pages = {}
        if caching:
            try:
                data = json.load(open('pages.json', 'r'))
                pages = copy.deepcopy(data)
                logging.info("%s pages loaded from cache.", len(pages.keys()))
            except IOError:
                pass
        if not data:
            spaces = self.getSpaces()
            for space in spaces:
                logging.debug("Space %s", space['key'])
                for page in self.getPages(space=space['key']):
                    pages[page['id']] = page['url']
            logging.info("%s pages loaded from confluence.", len(pages.keys()))

        for page in sorted(pages.keys()):
            cnt += 1

            renderedPage = self.renderContent(None, page, '', {'style': 'clean'})

            if not renderedPage:
                if "Render failed" in stats:
                    stats['Render failed'] += 1
                else:
                    stats['Render failed'] = 1
                if stdout:
                    print("\n%s" % page['url'])
                cnt_err += 1
                result.insert(-1, page['url'])
                data[page] = pages[page]
                continue
            if renderedPage.find('<div class="error">') > 0:
                t = re.findall('<div class="error">(.*?)</div>', renderedPage, re.IGNORECASE | re.MULTILINE)
                for x in t:
                    print("\n    %s" % t)
                    if x not in stats:
                        stats[x] = 1
                    else:
                        stats[x] += 1
                if stdout:
                    print("\n%s" % pages[page])
                cnt_err += 1
                result.insert(-1, pages[page])
                data[page] = pages[page]
            elif stdout:
                print("\r [%s/%s]" % (cnt_err, cnt), end='')

        json.dump(data, open('pages.json', 'w+'), indent=1)

        if stdout:
            print("-- stats --")
            for x in stats:
                print("'%s' : %s" % (x, stats[x]))
        return result

    def get_child_pages(self, root_page_id):
        """Get all child nodes of a page.

        :param root_page_id: The numeric page id of the root node.
        """
        child_page_list = []
        start = 0
        limit = 25

        response = self.connection.get(self.rest_url + 'content/{root_page_id}/child/page?start={start}&limit={limit}'
                                       .format(root_page_id=root_page_id, start=start, limit=limit)).json()

        out = {}
        for item in response['results']:
            out['title'] = item.get('title')
            out['id'] = item.get('id')
            child_page_list.append(out)
        # start = len(response)

        # while len(response) >= response['size']:
        #     # $Script: ChildPageList += $ChildPageList
        #     #
        #     # foreach($Item in $ChildPageList)
        #     #     Get - ChildPages - RootPageId:$Item.id
        #     pass
        return out


# TODO: replace all of these with object methods. Leaving for backwards compatibility for now
@deprecate_xmlrpc_notification
def attach_file(server, token, space, title, files):
    existing_page = server.confluence1.getPage(token, space, title)

    for filename in files.keys():
        try:
            server.confluence1.removeAttachment(token, existing_page["id"], filename)
        except Exception as e:
            logging.exception("Skipping %s exception in removeAttachment" % e)
        content_types = {
            "gif": "image/gif",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
        }
        extension = os.path.spl(filename)[1]
        ty = content_types.get(extension, "application/binary")
        attachment = {"fileName": filename, "contentType": ty, "comment": files[filename]}
        f = open(filename, "rb")
        try:
            byts = f.read()
            logging.info("calling addAttachment(%s, %s, %s, ...)", token, existing_page["id"], repr(attachment))
            server.confluence1.addAttachment(token, existing_page["id"], attachment, xmlrpclib.Binary(byts))
            logging.info("done")
        except Exception:
            logging.exception("Unable to attach %s", filename)
        finally:
            f.close()


@deprecate_xmlrpc_notification
def remove_all_attachments(server, token, space, title):
    existing_page = server.confluence1.getPage(token, space, title)

    # Get a list of attachments
    files = server.confluence1.getAttachments(token, existing_page["id"])

    # Iterate through them all, removing each
    numfiles = len(files)
    i = 0
    for f in files:
        filename = f['fileName']
        print("Removing %d of %d (%s)..." % (i, numfiles, filename))
        server.confluence1.removeAttachment(token, existing_page["id"], filename)
        i += 1


@deprecate_xmlrpc_notification
def write_page(server, token, space, title, content, parent=None):
    parent_id = None
    if parent is not None:
        try:
            # Find out the ID of the parent page
            parent_id = server.confluence1.getPage(token, space, parent)['id']
            print("parent page id is %s" % parent_id)
        except:
            print("couldn't find parent page; ignoring error...")

    try:
        existing_page = server.confluence1.getPage(token, space, title)
    except:
        # In case it doesn't exist
        existing_page = {"space": space, "title": title}

    if parent_id is not None:
        existing_page["parentId"] = parent_id

    existing_page["content"] = content
    existing_page = server.confluence1.storePage(token, existing_page)
