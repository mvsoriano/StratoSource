#    Copyright 2010, 2011 Red Hat Inc.
#
#    This file is part of StratoSource.
#
#    StratoSource is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    StratoSource is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with StratoSource.  If not, see <http://www.gnu.org/licenses/>.
#    

from suds.client import Client
import suds
import binascii
import datetime
import time
import logging
import httplib, urllib
import json
from urlparse import urlparse
import admin.management.CSBase # used to initialize logging


__author__="mark"
__date__ ="$Aug 15, 2010 9:48:38 PM$"

_API_VERSION = 23.0
_DEFAULT_LOGNAME = '/tmp/agent.log'
_METADATA_TIMEOUT=60 * 80
_METADATA_POLL_SLEEP=10
_DEPLOY_TIMEOUT=60 * 10
_DEPLOY_POLL_SLEEP=10

class LoginError(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)

class Bag:
    def __str__(self):
        return repr(self.__dict__)


class SalesforceAgent:

    def __init__(self, partner_wsdl_url, metadata_wsdl_url = None, clientLogger = None):
        if clientLogger is None:
#            logging.basicConfig(level=logging.DEBUG)
#            self.logname = _DEFAULT_LOGNAME
            self.logger = logging.getLogger(__file__)
        else:
            self.logger = clientLogger
        self.login_result = None
        if metadata_wsdl_url:
            self.meta = Client(metadata_wsdl_url)
        else:
            self.meta = None
        self.partner = Client(partner_wsdl_url)

    def set_logname(self, name):
        self.logname = name
        logging.basicConfig(filename=name,level=logging.DEBUG)
        self.logger = logging.getLogger(__file__)

    def getSessionId(self):
        return self.sid.sessionId
        
    def getServerLocation(self):
        return urlparse(self.login_result.serverUrl).netloc

    def login(self, user, password, server_url = None):
        if server_url: self.partner.set_options(location=server_url)
        try:
            self.login_result = self.partner.service.login(user, password)
        except suds.WebFault as sf:
            raise LoginError(str(sf))
        self.sid = self.partner.factory.create('SessionHeader')
        self.sid.sessionId = self.login_result.sessionId
        self.partner.set_options(soapheaders=self.sid)
        self.partner.set_options(location=self.login_result.serverUrl)

        self.meta.set_options(soapheaders=self.sid)
        self.meta.set_options(location=self.login_result.metadataServerUrl)

    def close(self):
        if not self.login_result:
            raise Exception('Initialization error: not logged in')
    #
    # try to avoid timeouts by not logging out, which invalidates shared session of other
    # jobs using the same salesforce api credentials.
    #
#        self.partner.service.logout()
        self.login_result = None

    def _buildEmailTemplatesPackage(self, pod):
        rest_conn = self.setupForRest(pod)
        params = urllib.urlencode({'q': "select Id, Name from Folder where Type = 'Email'"})
        data = self._invokeGetREST(rest_conn, "query/?%s" % params)

        if not data == None:
            records = data['records']
            folders = [record['Name'] for record in records]
        else:
            folders = []
        query = self.meta.factory.create('ListMetadataQuery')
        query.type = 'EmailTemplate'
        emailpaths = []
        for folder in folders:
            query.folder = folder
            self.logger.debug('listing contents of email folder %s' % folder)
            props = self.meta.service.listMetadata([query], _API_VERSION)
            for prop in props:
                emailpaths.append(prop.fullName)
        ptm = self.meta.factory.create('PackageTypeMembers')
        ptm.name = 'EmailTemplate'
        ptm.members = [emailpath for emailpath in emailpaths]
        return ptm

    def _buildCustomObjectsPackage(self):
        self.logger.info('loading Salesforce catalog for custom field discovery')
        query = self.meta.factory.create('ListMetadataQuery')
        query.type = 'CustomObject'
        props = self.meta.service.listMetadata([query], _API_VERSION)
        self.logger.info('catalog contains %d objects' % len(props))
        ptm = self.meta.factory.create('PackageTypeMembers')
        ptm.name = 'CustomObject'
        ptm.members = [prop.fullName for prop in props]
        return ptm

    def retrieve_objectchanges(self):
        query = self.meta.factory.create('ListMetadataQuery')
        query.type = 'CustomObject'
        props = self.meta.service.listMetadata([query], _API_VERSION)
        return props

    def retrieve_changesaudit(self, types, pod):
        supportedtypelist = ['ApexClass','ApexPage','ApexTrigger','CustomObject','Workflow']
        self.logger.info('loading changes for %s' % ','.join(supportedtypelist))

        # get intersection of requested types and those we support
        typelist = list(set(supportedtypelist) & set(types))
        for atype in typelist:
            if atype == 'CustomObject': typelist.append('CustomField')
        results = {}
        query = self.meta.factory.create('ListMetadataQuery')
        for aType in typelist:
            query.type = aType
            results[aType] = self.meta.service.listMetadata([query], _API_VERSION)
            self.logger.debug('Loaded %d records for type %s' % (len(results[aType]), aType))
        
        #
        # now do the types that diverge from the norm
        #
        rest_conn = self.setupForRest(pod)
        self.logger.info('loading changes for EmailTemplate')
        tmpemails = self._getEmailChangesMap(rest_conn)
        etemplates = []
        for tmpemail in tmpemails:
            template = Bag()
            template.__dict__['fullName'] = tmpemail['DeveloperName'] + '.email'
            template.__dict__['lastModifiedById'] = tmpemail['LastModifiedById']
            template.__dict__['lastModifiedByName'] = tmpemail['LastModifiedBy']['Name']
            template.__dict__['id'] = tmpemail['Id']
            lmd = tmpemail['LastModifiedDate'][0:-9]
            template.__dict__['lastModifiedDate'] = datetime.datetime.strptime(lmd, '%Y-%m-%dT%H:%M:%S')
            etemplates.append(template)
        results['EmailTemplate'] = etemplates
        self.logger.debug('Loaded %d EmailTemplate records' % len(etemplates))
        return results

    def setupForRest(self, pod):
        self.rest_headers = {"Authorization": "OAuth %s" % self.getSessionId(), "Content-Type": "application/json" }
        serverloc = pod + '.salesforce.com'
        self.logger.info('connecting to REST endpoint at %s' % serverloc)
        return httplib.HTTPSConnection(serverloc)

    #### DEFUNCT (I think) ####
    def retrieve_userchanges(self, pod):
        classes = {}
        triggers = {}
        pages = {}
        self.setupForRest(pod)
        classes = self._getChangesMap(rest_conn, 'ApexClass')
        for clazz in classes: clazz['Name'] += '.cls'
        triggers = self._getChangesMap(rest_conn, 'ApexTrigger')
        for trigger in triggers: trigger['Name'] += '.trigger'
        pages = self._getChangesMap(rest_conn, 'ApexPage', withstatus=False)
        for page in pages: page['Name'] += '.page'
        emails = self._getEmailChangesMap(rest_conn, withstatus=False)
        for email in emails: email['Name'] = email['DeveloperName'] + '.email'
#        rest_conn.close()
        return (classes, triggers, pages, email)

    def _getChangesMap(self, rest_conn, sfobject, withstatus=True):
        if withstatus:
            params = urllib.urlencode({'q': "select Id, Name, LastModifiedById, LastModifiedBy.Name, LastModifiedBy.Email, LastModifiedDate from %s where Status = 'Active' and NamespacePrefix = '' order by name" % sfobject})
        else:
            params = urllib.urlencode({'q': "select Id, Name, LastModifiedById, LastModifiedBy.Name, LastModifiedBy.Email, LastModifiedDate from %s where NamespacePrefix = '' order by name" % sfobject})
        data = self._invokeGetREST(rest_conn, "query/?%s" % params)
        if not data == None:
            return data['records']
        return None

    def _getEmailChangesMap(self, rest_conn):
        params = urllib.urlencode({'q': "select Id, Name, DeveloperName, LastModifiedById, LastModifiedBy.Name, LastModifiedBy.Email, LastModifiedDate from EmailTemplate where NamespacePrefix = '' order by name"})
        data = self._invokeGetREST(rest_conn, "query/?%s" % params)
        if not data == None:
            return data['records']
        return None

    def _invokePostREST(self, rest_conn, url, payload):
        rest_conn.request("POST", '/services/data/v%s/%s' % (_API_VERSION, url), payload, headers=self.rest_headers)
        response = rest_conn.getresponse()
        resultPayload = response.read()
        if response.status != 201:
            print response.status, response.reason
            return None
        data = json.loads(resultPayload)
        return data

    def _invokeGetREST(self,rest_conn,  url):
        self.logger.debug('invoking /services/data/v%s/%s' % (_API_VERSION, url))
        rest_conn.request("GET", '/services/data/v%s/%s' % (_API_VERSION, url), headers=self.rest_headers)
        response = rest_conn.getresponse()
        resultPayload = response.read()
        if response.status != 200:
            print response.status, response.reason
            print resultPayload
            return None
        data = json.loads(resultPayload)
        return data

    def retrieve_meta(self, types, pod, outputname='/tmp/retrieve.zip'):
        if not self.login_result:
            raise Exception('Initialization error: not logged in')
        if not self.meta:
            raise Exception('metadata API not initialized')
        request = self.meta.factory.create('RetrieveRequest')
        request.apiVersion = _API_VERSION
        pkg = self.meta.factory.create('Package')
        pkg.fullName = ['*']
        pkg.version = _API_VERSION
        pkg.apiAccessLevel = 'Unrestricted'
        pkgtypes = []
        for type in types:
            if type == 'CustomObject':
                pkgtypes.append(self._buildCustomObjectsPackage())
            elif type == 'EmailTemplate':
                pkgtypes.append(self._buildEmailTemplatesPackage(pod))
            else:
                pkgtype = self.meta.factory.create('PackageTypeMembers')
                pkgtype.members = ['*']
                pkgtype.name = type
                pkgtypes.append(pkgtype)
        pkg.types = pkgtypes
        request.unpackaged = [pkg]
        request.singlePackage = False

        countdown = _METADATA_TIMEOUT
        asyncResult = self.meta.service.retrieve(request)
        while not asyncResult.done:
            self.logger.info('polling.. %s' % str(countdown))
            time.sleep(_METADATA_POLL_SLEEP)
            countdown -= _METADATA_POLL_SLEEP
            if countdown <= 0: break
            asyncResult = self.meta.service.checkStatus([asyncResult.id])[0]

        if asyncResult.state != 'Completed':
            self.logger.error('Retrieving package: ' + asyncResult.message)
            raise Exception(asyncResult.message)

        result = self.meta.service.checkRetrieveStatus([asyncResult.id])

    #    if result.messages != None and len(result.messages) > 0:
    #        print 'Error: ' + '\n'.join([r.problem for r in result.messages])
    #        raise Exception('Retrieval error: ' + result.messages[0].problem)

        zip = binascii.a2b_base64(result.zipFile)
        out = file(outputname, 'w')
        out.write(zip)
        out.close()

    def deploy(self, zipfilename,  checkOnly = False):
        zipfile = file(zipfilename, 'rb')
        zip = zipfile.read()
        zipfile.close()
        zip64 = binascii.b2a_base64(zip)
        deploy_options = self.meta.factory.create('DeployOptions')
        deploy_options.allowMissingFiles = 'false'
        deploy_options.autoUpdatePackage = 'true'
        deploy_options.checkOnly = 'true' if checkOnly else 'false'
        deploy_options.ignoreWarnings = 'false'
        deploy_options.performRetrieve = 'false'
        deploy_options.purgeOnDelete = 'false'
        deploy_options.rollbackOnError = 'true'
        deploy_options.runAllTests = 'false'
        deploy_options.singlePackage = 'true'

        result = self.meta.service.deploy(zip64, deploy_options)
        countdown = _DEPLOY_TIMEOUT
        while not result.done:
            self.logger.info('polling..%s' % str(countdown))
            time.sleep(_DEPLOY_POLL_SLEEP)
            countdown -= _DEPLOY_POLL_SLEEP
            if countdown <= 0: raise Exception('Deployment timed out')
            result = self.meta.service.checkStatus([result.id])[0]
            print result
            print "Status is: %s" % result.state
        if result.state != 'Completed':
            raise Exception(result.message)
        deployResult = self.meta.service.checkDeployStatus(result.id)
        return deployResult


if __name__ == "__main__":
    print "Hello World"
