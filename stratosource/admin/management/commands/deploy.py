#    Copyright 2010, 2011 Red Hat Inc.
#
#    This file is part of StratoSource.
#
#    StratoSource is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    StratoSource is distributed in the hope that it will be useful.
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with StratoSource.  If not, see <http://www.gnu.org/licenses/>.
#    
import logging
import logging.config
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ObjectDoesNotExist
from stratosource.admin.models import Story, Branch, DeployableObject
from admin.management import Utils
import subprocess
import os
from zipfile import ZipFile
from lxml import etree
import admin.management.CSBase # used to initialize logging


__author__="masmith"
__date__ ="$Sep 22, 2010 2:11:52 PM$"

typeMap = {'fields': 'CustomField','validationRules': 'ValidationRule',
           'listViews': 'ListView','namedFilters': 'NamedFilter',
           'searchLayouts': 'SearchLayout','recordTypes': 'RecordType',
           'objects': 'CustomObject', 'classes' : 'ApexClass', 'labels' : 'CustomLabel',
           'triggers': 'ApexTrigger',
            'pages': 'ApexPage', 'weblinks': 'CustomPageWebLink',
            'components': 'ApexComponent'}
SF_NAMESPACE='{http://soap.sforce.com/2006/04/metadata}'
_API_VERSION = "24.0"


def createFileCache(map):
    cache = {}
    for type,list in map.items():
        if type == 'objects' or type == 'labels':
            for object in list:
                try:
                    path = os.path.join('unpackaged',type,object.filename)
                    f = open(path)
                    cache[object.filename] = f.read()
                    f.close()
                except IOError:
                    print '** not able to load ' + path
                    pass    # caused by a new file added, not present on current branch
        else:
            for object in list:
                path = os.path.join('unpackaged',type,object.filename)
                f = open(path)
                cache[object.filename] = f.read()
                f.close()
    return cache

def findXmlNode(doc, object):

    if object.type == 'objectTranslation':
        nodeName = SF_NAMESPACE + 'fields'
        nameKey = SF_NAMESPACE + 'name'
    if object.type == 'labels':
        nodeName = SF_NAMESPACE + 'labels'
        nameKey = SF_NAMESPACE + 'fullName'
    elif object.type == 'translationChange':
        nodeName = SF_NAMESPACE + 'fields'
        nameKey = SF_NAMESPACE + 'name'
    elif object.el_type == 'listViews':
        nodeName = SF_NAMESPACE + 'listViews'
        nameKey = SF_NAMESPACE + 'fullName'
    else:
        nodeName = SF_NAMESPACE + 'fields'
        nameKey = SF_NAMESPACE + 'fullName'

    children = doc.findall(nodeName)
    for child in children:
        node = child.find(nameKey)
        #print 'el_name=' + object.el_name
        #print 'node name=' + node.text
        if node is not None and node.text == object.el_name:
            return etree.tostring(child)
    return None

#
# this is an exception to the usual pattern of 2-level-deep xml nodes
#
def findXmlSubnode(doc, object):
    if object.type == 'objects':
        node_names = object.el_name.split(':')
#        print '>>> node_names[0] = %s, node_names[1] = %s' % (node_names[0], node_names[1])
#        print '>>> subtype=' + object.el_subtype
        children = doc.findall(SF_NAMESPACE + object.el_type)
#        print '>>> looking for ' + object.el_type
        for child in children:
#            print '>>> processing child'
            node = child.find(SF_NAMESPACE + 'fullName')
            if node is not None:
#                print '>>> processing node ' + node.text
#                print '>>> comparing [%s] to [%s]' % (node.text, node_names[0])
                if node.text == node_names[0]:
                    if object.el_subtype == 'picklists':
                        plvalues = child.findall(SF_NAMESPACE + 'picklistValues')
                        for plvalue in plvalues:
#                            print '>>> processing plvalue'
                            if plvalue.find(SF_NAMESPACE + 'picklist').text == node_names[1]:
#                                print '>>> plvalue found'
                                #
                                # due to difficulty we have to return everything from the root node
                                #
                                return etree.tostring(child)
    else:
        
        logging.getLogger('deploy').info('Unknown object type: ' + object.type)
    return None

def generateObjectChanges(doc,  cache, object):
    if object.status == 'd': return None
    doc = etree.XML(cache[object.filename])
#    print 'looking for %s' % object.el_name
    if object.el_name.find(':') >= 0:
        # recordType node
        xml = findXmlSubnode(doc, object)
    else:
        xml = findXmlNode(doc, object)

    if not xml:
        logging.getLogger('deploy').info("Did not find XML node for %s.%s.%s.%s" % (object.filename,object.el_type,object.el_name,object.el_subtype))
        return None
        
    return xml


def getMetaForFile(filename):
    with file(filename+'-meta.xml') as f:
        return f.read()

def buildCustomObjectDefinition(filepath, itemlist):
    # break into submap of objects and fields
    m = {}
    for item in itemlist:
        if not m.has_key(item.filename): m[item.filename] = []
        m[item.filename].append(m.el_name)
    for objname,fieldlist in m.items():
        doc = etree.XML(cache[objname])
        # make a list of existing fields from the object
        existingfields = []

def hasDuplicate(objectlist, obj):
    for o in objectlist:
        if o.el_name == obj.el_name and o.el_subtype == obj.el_subtype and o.filename == obj.filename:
            logger = logging.getLogger('deploy')
            logger.info('Rejected duplicate ' + obj.filename + '/' + obj.el_name)
            return True
    return False

def generatePackage(objectList, from_branch, to_branch):
    logger = logging.getLogger('deploy')

    defaultNS = { None: 'http://soap.sforce.com/2006/04/metadata'}
    doc = etree.Element('Package') #, nsmap=defaultNS)
    etree.SubElement(doc, 'version').text = "{0}".format(_API_VERSION)

    destructive = etree.Element('Package') #, nsmap=defaultNS)
    etree.SubElement(destructive, 'version').text = "{0}".format(_API_VERSION)

    output_name = '/tmp/deploy_%s_%s.zip' % (from_branch.name, to_branch.name)
    myzip = ZipFile(output_name, 'w')

    logger.info('building %s', output_name)
    # create map and group by type
    map = {}
    for object in objectList:
        if not map.has_key(object.type): map[object.type] = []
        olist = map[object.type]
        if not hasDuplicate(olist, object): olist.append(object)
    cache = createFileCache(map)
    
    objectPkgMap = {}   # holds all nodes to be added/updated, keyed by object/file name

    for type,itemlist in map.items():
        if not typeMap.has_key(type):
            logger.error('** Unhandled type {0} - skipped'.format(type))
            continue

        logger.info('PROCESSING TYPE %s', type)

        if type == 'objects':
            #
            # For objects we need to collect a list of all field/list/recordtype/et.al changes
            # then process them at the end
            #
            for object in itemlist:
                if object.status == 'd':
                    registerChange(destructive, object, type)
                    logger.info('removing: %s %s %s', object.filename, object.el_name, object.el_subtype)
                else:
                    if not objectPkgMap.has_key(object.filename): objectPkgMap[object.filename] = []
                    changes = objectPkgMap[object.filename]
                    registerChange(doc, object, type);
                    if object.el_name is None:
                        pass
                    else:
                        fragment = generateObjectChanges(doc, cache, object)
                        changes.append(fragment)
        elif type == 'labels':
            for obj in itemlist:
                if object.status == 'd':
                    registerChange(destructive, obj, type)
                    logger.info('removing: %s %s', object.filename, object.el_name)
                else:
                    registerChange(doc, obj, type)
                    fragment = generateObjectChanges(doc, cache, obj)
                    writeLabelDefinitions(obj.filename, fragment, myzip)
        elif type in ['pages','classes','triggers']:
            writeFileDefinitions(doc, destructive, type, itemlist, cache, myzip)

    writeObjectDefinitions(objectPkgMap, cache, myzip)

    xml = etree.tostring(doc, xml_declaration=True, encoding='UTF-8', pretty_print=True)
    myzip.writestr('package.xml', xml)

    xml = etree.tostring(destructive, xml_declaration=True, encoding='UTF-8', pretty_print=True)
    myzip.writestr('destructiveChanges.xml', xml)
    myzip.close()
    return output_name

#
# register an item to the package.xml or destructive.xml document
#
def registerChange(doc, member, filetype):
    logger = logging.getLogger('deploy')

    el = etree.SubElement(doc, 'types')
    object_name = member.filename[0:member.filename.find('.')]
    if member.el_name is None:
        etree.SubElement(el, 'members').text = object_name
        etree.SubElement(el, 'name').text = typeMap[filetype]
        logger.info('registering: %s', object_name)
    else:
        el_name = member.el_name
        if filetype == 'objects':
            if el_name.find(':') > 0:
                el_name = el_name.split(':')[0]
                filetype = 'recordTypes'
            else:
                filetype = 'fields'
        etree.SubElement(el, 'members').text = object_name + '.' + el_name
        etree.SubElement(el, 'name').text = typeMap[filetype]
        logger.info('registering: %s - %s', object_name + '.' + el_name, typeMap[filetype])


def writeFileDefinitions(packageDoc, destructiveDoc, filetype, filelist, cache, zipfile):
    logger = logging.getLogger('deploy')
    for member in filelist:
        print 'member filename=%s, el_type=%s' % (member.filename, member.el_type)
        if member.filename.find('.') > 0:
            object_name = member.filename[0:member.filename.find('.')]
#                object_name = member.filename[:-(len(member.el_type) + 1)]
        ## !! assumes the right-side branch is still current in git !!
        if os.path.isfile(os.path.join('unpackaged',filetype,member.filename)):
            zipfile.writestr(filetype+'/'+member.filename, cache.get(member.filename))
            zipfile.writestr(filetype+'/'+member.filename+'-meta.xml', getMetaForFile(os.path.join('unpackaged',filetype,member.filename)))
            registerChange(packageDoc, member, filetype)
            logger.info('storing: %s', member.filename)
        else:
            logger.info('removing: %s', member.filename)
            registerChange(destructiveDoc, member, filetype)

def writeLabelDefinitions(filename, element, zipfile):
    xml = '<?xml version="1.0" encoding="UTF-8"?>'\
            '<CustomLabels xmlns="http://soap.sforce.com/2006/04/metadata">'
    xml += element
    xml += '</CustomLabels>'
    zipfile.writestr('labels/'+filename, xml)

def writeObjectDefinitions(objectMap, filecache, zipfile):
    for objectName in objectMap.keys():
        elementList = objectMap[objectName]
        if len(elementList) == 0:
            objectxml = cache.get(objectName)
        else:
            objectxml = '<?xml version="1.0" encoding="UTF-8"?>'\
                            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">'
            objectxml += '\n'.join(elementList)
            objectxml += '</CustomObject>'
        zipfile.writestr('objects/'+objectName, objectxml)

class Command(BaseCommand):

    def deploy(self, objectList, from_branch, to_branch):
        for object in objectList:
            print object.status, object.filename, object.type, object.el_name, object.el_subtype
        output_name = generatePackage(objectList, from_branch, to_branch)
        agent = Utils.getAgentForBranch(to_branch, logger=logging.getLogger('deploy'));
        return agent.deploy(output_name)

    def deploy_stories(self, stories, from_branch, to_branch):
        # get all release objects associated with our story
        logger = logging.getLogger('deploy')
        rolist = DeployableObject.objects.filter(pending_stories__in=stories)
        resetLocalRepo(from_branch.name)
        deployResult = self.deploy(set(rolist), from_branch, to_branch)
        if deployResult is not None:
            if not deployResult.success:
                for dm in deployResult.messages:
                    if not dm.success:
                        logger.info('fail: {0} - {1} - {2}'.format(dm.fileName, dm.fullName, dm.problem))
                    else:
                        logger.info('pass: {0} - {1}'.format(dm.fileName, dm.fullName))
            raise CommandError('deployment failed')


    def handle(self, *args, **options):
        if len(args) < 6: raise CommandError('usage: deploy <source repo> <source branch> <dest repo> <dest branch> story <storyid>')

        if args[4] == 'story':
            story = Story.objects.get(rally_id=args[5])
            if not story: raise CommandException("invalid story")
            from_branch = Branch.objects.get(repo__name__exact=args[0], name__exact=args[1])
            if not from_branch: raise CommandException("invalid source branch")
            to_branch = Branch.objects.get(repo__name__exact=args[2], name__exact=args[3])
            if not to_branch: raise CommandException("invalid destination branch")
            os.chdir(from_branch.repo.location)
            self.deploy_stories([story], from_branch, to_branch)



def resetLocalRepo(branch_name):
    subprocess.check_call(["git","checkout",branch_name])
#    subprocess.check_call(["git","reset","--hard","{0}".format(branch_name)])

