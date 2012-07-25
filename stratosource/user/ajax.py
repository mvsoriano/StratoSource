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
from datetime import datetime
from stratosource.admin.models import Release, Story, Branch, DeployableObject, DeployableTranslation, ReleaseTask
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils import simplejson
from django.db import transaction
import logging
import traceback

logger = logging.getLogger('console')

def createrelease(request):
    results = {'success':False}
    
    if request.method == u'GET':
        try:
            release = Release()
            release.name = request.GET['name']
            release.est_release_date = request.GET['estRelDate']
            release.save()
            results = {'success':True}
        except Exception as ex:
            results = {'success':False, 'error':ex}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def updatereleasedate(request):
    results = {'success':False}

    if request.method == u'GET':
        try:
            release = Release.objects.get(id=request.GET['id'])
            date = request.GET['date']

            release.est_release_date = date
            release.save()
            results = {'success':True}
        except Exception as ex:
            results = {'success':False, 'error':ex}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def deleterelease(request):
    results = {'success':False}

    if request.method == u'GET':
        try:
            release = Release.objects.get(id=request.GET['id'])
            release.delete()
            results = {'success':True}
        except Exception as ex:
            results = {'success':False, 'error':ex}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def markreleased(request):
    results = {'success':False}

    if request.method == u'GET':
        try:
            release = Release.objects.get(id=request.GET['id'])
            release.released = True
            release.release_date = datetime.now()
            release.save()
            for story in release.stories.all():
#                story.done_on_branches.add(release.branch)
                objects = DeployableObject.objects.filter(pending_stories=story)
                for object in objects:
                    object.pending_stories.remove(story)
                    object.released_stories.add(story)

                    non_releasing_stories = set()
                    for story in object.pending_stories.all():
                        if story not in release.stories.all():
                            non_releasing_stories.add(story)
                    
                    if (len(non_releasing_stories) == 0):
                        object.release_status = 'r';
                    object.save()
                translations = DeployableTranslation.objects.filter(pending_stories=story)
                for trans in translations:
                    trans.pending_stories.remove(story)
                    trans.released_stories.add(story)

                    non_releasing_stories = set()
                    for story in trans.pending_stories.all():
                        if story not in release.stories.all():
                            non_releasing_stories.add(story)
                    
                    if (len(non_releasing_stories) == 0):
                        trans.release_status = 'r';
                    trans.save()
            results = {'success':True}
        except Exception as ex:
            results = {'success':False, 'error':ex.message}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def releases(request):
    if request.method == u'GET':
        releases = Release.objects.filter(hidden=False).order_by('released', 'est_release_date', 'release_date', 'name')
        data = {'releases': releases}

        return render_to_response('ajax_releases.html', data, context_instance=RequestContext(request) )

def ignoreitem(request, object_id):
    results = {'success':False}
    ok = request.GET['ok']
    try:
        object = DeployableObject.objects.get(id=object_id)
        if ok == 'true':
            if len(object.pending_stories.all()) > 0:
                object.release_status = 'p'
            else:
                object.release_status = 'c'
            object.save()
            results = {'success':True}
        else:
            if len(object.pending_stories.all()) == 0:
                object.release_status = 'r'
                object.save()
                results = {'success':True}
            else:
                results = {'success': False, 'error': 'Object is assigned to a story'}
    except Exception as ex:
        results = {'success':False, 'error':ex}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def ignoretranslation(request, trans_id):
    results = {'success':False}
    ok = request.GET['ok']
    try:
        trans = DeployableTranslation.objects.get(id=trans_id)
        if ok == 'true':
            if len(trans.pending_stories.all()) > 0:
                trans.release_status = 'p'
            else:
                trans.release_status = 'c'
            trans.save()
            results = {'success':True}
        else:
            if len(trans.pending_stories.all()) == 0:
                trans.release_status = 'r'
                trans.save()
                results = {'success':True}
            else:
                results = {'success': False, 'error': 'At least one object is assigned to a story'}
    except Exception as ex:
        results = {'success':False, 'error':ex}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

@transaction.commit_on_success
def ignoreselected(request):
    results = {'success':False}

    try:
        objectIds = request.GET.getlist('ii');

        objects = DeployableObject.objects.filter(id__in=objectIds)
        for object in objects:
            if len(object.pending_stories.all()) == 0:
                object.release_status = 'r'
                object.save()

        transIds = request.GET.getlist('ti');

        translations = DeployableTranslation.objects.filter(id__in=transIds)
        for translation in translations:
            if len(translation.pending_stories.all()) == 0:
                translation.release_status = 'r'
                translation.save()

        results = {'success':True}

    except Exception as ex:
        results = {'success':False, 'error':ex}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def getstories(request):
    results = {'success':False}
    try:
        storyList = ['None|']
        stories = []
        sprint = 'All'
        if request.GET.__contains__('sprintName'):
            sprint = request.GET['sprintName'];
        if sprint != 'None':
            if len(sprint) > 0 and sprint != 'All':
                stories = Story.objects.filter(sprint=sprint).order_by('rally_id', 'name')
            else:
                stories = Story.objects.all().order_by('rally_id', 'name')
    
            for story in stories:
                if len(story.rally_id) > 0:
                    name = story.rally_id + ': '
    
                storyList.append(name + story.name + '|' + str(story.id))
        results = {'success':True, 'stories': storyList, 'numStories': len(stories)}
    except Exception as ex:
        results = {'success':False, 'error':ex.message}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def getsprints(request):
    results = {'success':False}
    try:
        sprintList = ['None','All']
        stories = Story.objects.values('sprint').filter(sprint__isnull=False).order_by('sprint').distinct()

        for story in stories:
            if len(story['sprint']) > 0 and not sprintList.__contains__(story['sprint']):
                sprintList.append(story['sprint'])
        
        results = {'success':True, 'sprints': sprintList, 'numSprints': len(sprintList)}
    except Exception as ex:
        results = {'success':False, 'error':ex.message}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')


def addtostory(request):

    log = logging.getLogger('user')

    results = {'success':False}

    if request.method == u'GET':
        story = None
        try:
            storyId = request.GET['storyId']
            if (storyId == 'null'):
                storyId = ''
            storyName = request.GET['storyName']
            storyRallyId = request.GET['storyRallyId']
            storyURL = request.GET['storyURL']

            if len(storyId) > 0 or len(storyRallyId) > 0:

                if len(storyId) > 0:
                    try:
                        story = Story.objects.get(id=storyId)
                    except ObjectDoesNotExist:
                        pass

                if not story and len(storyRallyId) > 0:
                    try:
                        story = Story.objects.get(rally_id=storyRallyId)
                    except ObjectDoesNotExist:
                        pass

                if not story:
                    story = Story()
                    if len(storyRallyId) > 0:
                        story.rally_id = storyRallyId
                        story.url = "https://rally1.rallydev.com/slm/detail/" + story.rally_id
                    if len(storyURL) > 0:
                        story.url = storyURL

                if len(storyName) > 0:
                    story.name = storyName
                    
                story.save()
                
            objectIds = request.GET.getlist('itemid');

            objects = DeployableObject.objects.filter(id__in=objectIds)
            for object in objects:
                if story:
                    object.pending_stories.add(story)
                    object.release_status = 'p'
                object.save()

            transIds = request.GET.getlist('transid');

            translations = DeployableTranslation.objects.filter(id__in=transIds)
            for translation in translations:
                if story:
                    translation.pending_stories.add(story)
                    translation.release_status = 'p'
                translation.save()

            results = {'success':True}
        except Exception as ex:
            tb = traceback.format_exc()
            results = {'success':False, 'error':'ERROR: ' + tb}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')

def get_release_tasks(request, release_id):
    release = Release.objects.get(id=release_id)
    branches = Branch.objects.all()

    tasks = ReleaseTask.objects.filter(release=release).order_by('order')
    
    for task in tasks:
        task.done_in_branch_list = task.done_in_branch.split(',')
        
    for branch in branches:
        branch.tid = str(branch.id)
    
    data = {'success':True, 'tasks': tasks, 'branches': branches, 'readonly' : request.GET.__contains__('readonly')}

    return render_to_response('release_tasks_ajax.html', data, context_instance=RequestContext(request))
    
def add_release_task(request):
    results = {'success':False}
    try:
        release_id = request.GET['rel_id']
        release = Release.objects.get(id=release_id)
        task = ReleaseTask()
        task.release = release
        task.order = 999
        task.name = request.GET['task']
        task.save()
        
        results = {'success':True}
    except Exception as ex:
        results = {'success':False, 'error':ex.message}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')
    
def edit_release_task(request):
    task_id = request.GET['task_id']
    
    done_on_branch = request.GET['branch_id']
    
    task = ReleaseTask.objects.get(id=task_id)
    
    if request.GET.__contains__('newVal'):
        newVal = request.GET['newVal']
        task.name = newVal

    if request.GET.__contains__('done'):
        is_done = request.GET['done'] == 'true'
        task.done_in_branch_list = task.done_in_branch.split(',')
        try:
            task.done_in_branch_list.remove(done_on_branch)
        except Exception:
            logger.debug('Not in list')
    
        if is_done:
            task.done_in_branch_list.append(done_on_branch)
    
        str = ''
        for id in task.done_in_branch_list:
            if id != '':
                if str == '':
                    str = id
                else:
                    str = str + ',' + id
    
        task.done_in_branch = str
        logger.debug('task.done_in_branch ' + task.done_in_branch)
    
    task.save()

    results = {'success':True}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')    

@transaction.commit_on_success   
def reorder_release_tasks(request):
    order = request.GET['order']
    id_list = order.split(',')
    i = 0
    for id in id_list:
        task = ReleaseTask.objects.get(id=id)
        task.order = i
        i = i + 1
        task.save()

    results = {'success':True}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')    

def delete_release_task(request):
    release_id = request.GET['rel_id']
    task_id = request.GET['task_id']
    
    task = ReleaseTask.objects.get(id=task_id)
    task.delete()
    
    results = {'success':True}

    json = simplejson.dumps(results)
    return HttpResponse(json, mimetype='application/json')    
