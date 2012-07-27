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
import json
import urllib2
import urllib
from stratosource.admin.management import ConfigCache
from stratosource.admin.models import Story
from stratosource import settings
from operator import attrgetter
import logging
from django.db import transaction

logger = logging.getLogger('console')

class RallyProject():
    def __init__(self, a_name, a_url, some_children, some_sprints):
        self.name = a_name
        self.url = a_url
        url_pieces = a_url.split('/')
        jsFileName = url_pieces[len(url_pieces) - 1]
        jsFileNameParts = jsFileName.split('.')
        self.id = jsFileNameParts[0]
        self.children = some_children
        self.sprints = some_sprints

def print_proj_tree(pList):
    for p in pList:
        if len(p.children) == 0:
            logger.debug(p.name + ' - ' + p.id + ' (' + str(len(p.sprints)) + ' sprints)')
        else:
            print_proj_tree(p.children)

def find_leaves(pList, level, leaves):
    for p in pList:

        if len(p.children) == 0:
            if not leaves.has_key(p.id) or level > leaves[p.id]:
                leaves[p.id] = level
        else:
            find_leaves(p.children, level + 1, leaves)
    return leaves

def leaf_list(pList, llist):

    for p in pList:
        if len(p.children) == 0:
            llist.append(p)
        else:
            leaf_list(p.children, llist)

    return llist

def load_projects(urllib2, name, projectList):
    projects = []
    if len(name) > 0:
        name = name + ': '
        
    if len(projectList) > 0:
        for project in projectList:
            projectDetailJs = urllib2.urlopen(project['_ref']).read()
            projectDetail = json.loads(projectDetailJs)

            proj = RallyProject(name + projectDetail['Project']['_refObjectName'], projectDetail['Project']['_ref'], load_projects(urllib2, name + projectDetail['Project']['_refObjectName'], projectDetail['Project']['Children']), projectDetail['Project']['Iterations'])
            if len(proj.children) > 0 or len(proj.sprints) > 0:
                projects.append(proj)

    leaves = find_leaves(projects, 0, {})
    for p in projects:
        if  leaves.has_key(p.id) and leaves[p.id] > 0:
            projects.remove(p)

    projects.sort()

    return projects

def connect():
    rally_user = ConfigCache.get_config_value('rally.login')
    rally_pass = ConfigCache.get_config_value('rally.password')
    
    logger.debug('Logging in with username ' + rally_user)

    password_manager = urllib2.HTTPPasswordMgrWithDefaultRealm()
    password_manager.add_password( None, 'https://' + settings.RALLY_SERVER + '/', rally_user, rally_pass )
    auth_handler = urllib2.HTTPBasicAuthHandler(password_manager)
    opener = urllib2.build_opener(auth_handler)
    urllib2.install_opener(opener)

    return urllib2

def get_projects(leaves):
    logger.debug('Start getting projects')
    urllib2 = connect()

    # Get workspace:
    projects = []
    wsjs = urllib2.urlopen('https://' + settings.RALLY_SERVER + '/slm/webservice/' + settings.RALLY_REST_VERSION + '/workspace.js').read()
    workspaces = json.loads(wsjs)

    logger.debug('Workspaces returned: ' + str(workspaces['QueryResult']['TotalResultCount']))

    if workspaces['QueryResult']['TotalResultCount'] > 0:
        for ws in workspaces['QueryResult']['Results']:
            logger.debug('Workspace: ' + ws['_refObjectName'] + ' url "' + ws['_ref'] + '"')
            wsDetailsJs = urllib2.urlopen(ws['_ref']).read()
            wsDetails = json.loads(wsDetailsJs)
            projects.extend(load_projects(urllib2, ws['_refObjectName'], wsDetails['Workspace']['Projects']))

    print_proj_tree(projects)
    if leaves:
        return sorted(leaf_list(projects,[]), key=attrgetter('name'))
    
    return projects

def get_stories(projectIds):
    urllib2 = connect()

    stories = {}
    querystring = '('
    for projId in projectIds:
        if len(querystring) > 1:
            querystring += ' or '
        querystring += '(Project = https://' + settings.RALLY_SERVER + '/slm/webservice/' + settings.RALLY_REST_VERSION + '/project/' + projId + ')'
    querystring += ')'
    
    print 'QueryString is ' + querystring
    
    start = 1
    pagesize = 200
    lastPage = False
    while not(lastPage):
        url = 'https://' + settings.RALLY_SERVER + '/slm/webservice/' + settings.RALLY_REST_VERSION + '/hierarchicalrequirement.js?query=' + urllib.quote(querystring) + '&fetch=true&start=' + str(start) + '&pagesize=' + str(pagesize)

        print 'Fetching url ' + url
        queryresultjson = urllib2.urlopen(url).read()
        queryresult = json.loads(queryresultjson)

        for result in queryresult['QueryResult']['Results']:
            story = Story()
            story.rally_id = result['FormattedID']
            story.name = result['Name']
            if result['Iteration']:
                story.sprint = result['Iteration']['_refObjectName']
            stories[story.rally_id] = story
            print story.rally_id + ' from ' + story.sprint
        
        print 'results: ' + str(queryresult['QueryResult']['TotalResultCount']) + 'start ' + str(start) + ' for pagesize ' + str(pagesize)
        if queryresult['QueryResult']['TotalResultCount'] <= start + pagesize:
            lastPage = True

        start += pagesize
    
    return stories

@transaction.commit_on_success    
def refresh():
        projectList = ConfigCache.get_config_value('rally.pickedprojects')
        if len(projectList) > 0:
            rallyStories = get_stories(projectList.split(';'))
            dbstories = Story.objects.filter(rally_id__in=rallyStories.keys())
            dbStoryMap = {}
            for dbstory in dbstories:
                dbStoryMap[dbstory.rally_id] = dbstory

            for story in rallyStories.values():
                dbstory = story
                if story.rally_id in dbStoryMap:
                    #logger.debug('Updating [' + story.rally_id + ']')
                    # Override with database version if it exists
                    dbstory = dbStoryMap[story.rally_id]
                    dbstory.name = story.name
                #else:
                    #logger.debug('Creating [' + story.rally_id + ']')
                    
                dbstory.sprint = story.sprint
                dbstory.save()
