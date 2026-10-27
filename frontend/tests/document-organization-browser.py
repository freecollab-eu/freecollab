#!/usr/bin/env python3
"""Actual authenticated document stacks, guarded moves, links and comment draft proof.

Own temporary API/Vite/SQLite/keys only; no external providers or retained services.
"""
import base64
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright
from ui_navigation import reveal_message_actions, edit_saved_document, capture_work_card
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('document_helpers',Path(__file__).with_name('shared-documents-browser.py'))
doc=importlib.util.module_from_spec(spec);spec.loader.exec_module(doc)
helpers=doc.helpers
REPORT=Path(os.getenv('DOCUMENT_ORGANIZATION_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/feature-audit-20261023/document-organization-browser.json')))


def disclose(panel, selector):
    details=panel.locator(selector)
    expect(details).to_have_count(1)
    if details.get_attribute('open') is None: details.locator(':scope > summary').click()
    expect(details).to_have_attribute('open','')
    return details


def organize(panel):
    return disclose(panel,'details.fc-document-flow-organize')


def options(panel):
    return disclose(panel,'details.fc-shared-flow-options')


def start_drag_diagnostics(page, app, source_id, target_id, stack_ids, evidence):
    page.evaluate('''({source,target,stacks})=>{
      const knownStacks=new Set(['',...stacks]);
      const describe=node=>{if(!node||!node.closest)return null;const stack=node.closest('[data-document-stack]')?.dataset.documentStack;
        const documentId=node.closest('li[draggable]')?.querySelector('[data-document-move]')?.dataset.documentMove;
        return {tag:node.tagName,classes:typeof node.className==='string'?node.className:'',stack:knownStacks.has(stack)?stack:null,document:documentId===source?source:null};};
      const rect=node=>{if(!node)return null;const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scrollTop:node.scrollTop,clientHeight:node.clientHeight,scrollHeight:node.scrollHeight};};
      const sourceNode=()=>document.querySelector('[data-document-move="'+CSS.escape(source)+'"]')?.closest('li');
      const targetNode=()=>document.querySelector('[data-document-stack="'+CSS.escape(target)+'"]');
      const scrollAncestors=()=>{const rows=[];for(let node=sourceNode();node&&rows.length<5;node=node.parentElement){const style=getComputedStyle(node);if(/auto|scroll/.test(style.overflowY))rows.push({node:describe(node),bounds:rect(node),overflowY:style.overflowY});}return rows;};
      const listeners=[],events=[];
      const snapshot=name=>({name,viewport:{width:innerWidth,height:innerHeight},fonts:document.fonts.status,source:rect(sourceNode()),target:rect(targetNode()),scrollAncestors:scrollAncestors(),events:[...events]});
      for(const type of ['dragstart','dragenter','dragover','drop','dragend'])for(const capture of [true,false]){
        const listener=event=>{if(events.length>=100)return;const payload=event.dataTransfer?.getData('application/x-freecollab-document')||'';
          events.push({type,phase:capture?'capture':'bubble',trusted:event.isTrusted,x:event.clientX,y:event.clientY,defaultPrevented:event.defaultPrevented,
            target:describe(event.target),hit:describe(document.elementFromPoint(event.clientX,event.clientY)),types:[...(event.dataTransfer?.types||[])].slice(0,8),
            payload:payload===source?source:payload?'[unexpected]':'',effectAllowed:event.dataTransfer?.effectAllowed,dropEffect:event.dataTransfer?.dropEffect,
            source:rect(sourceNode()),stack:rect(targetNode()),scrollAncestors:scrollAncestors()});};
        document.addEventListener(type,listener,capture);listeners.push({type,listener,capture});
      }
      window.__documentOrganizationDrag={snapshot,stop:()=>{for(const item of listeners)document.removeEventListener(item.type,item.listener,item.capture);}};
    }''',{'source':source_id,'target':target_id,'stacks':stack_ids})
    evidence.update(sourceDocumentId=source_id,targetStackId=target_id)
    pending=[]
    def placement(request):
        url=urlsplit(request.url)
        if request.method!='PUT' or url.netloc!=urlsplit(app).netloc or not url.path.endswith('/documents/'+source_id+'/placement'): return None
        body=request.post_data_json
        if not isinstance(body,dict): return None
        destination=body.get('stack_id')
        return {'documentId':source_id,'destinationStackId':destination if destination is None or destination in stack_ids else '[unexpected]',
                'expectedVersion':body.get('expected_version') if isinstance(body.get('expected_version'),int) else None,'status':None}
    def requested(request):
        row=placement(request)
        if row is not None and len(evidence['placements'])<8:
            evidence['placements'].append(row);pending.append((request,row))
    def responded(response):
        for request,row in pending:
            if request==response.request: row['status']=response.status;break
    page.on('request',requested);page.on('response',responded)
    return requested,responded


def capture_drag_diagnostics(page, evidence, name, stop=False):
    try:
        value=page.evaluate('''({name,stop})=>{const state=window.__documentOrganizationDrag;if(!state)return null;const result=state.snapshot(name);if(stop)state.stop();return result}''',{'name':name,'stop':stop})
        if value:
            evidence['events']=value.pop('events')
            if len(evidence['samples'])<6:evidence['samples'].append(value)
    except Exception as failure:
        evidence['captureErrorType']=type(failure).__name__


def main():
    results={}; processes=[];streams=[];stage='startup';source_hashes={};complete=False
    drag_evidence={'events':[],'samples':[],'placements':[]}
    with tempfile.TemporaryDirectory(prefix='freecollab-document-organization-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=helpers.port(),helpers.port();base='http://127.0.0.1:'+str(api_port);app='http://127.0.0.1:'+str(app_port)
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message-keys.json'),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),OIDC_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',COPILOT_ENABLED='false',REDIS_URL='',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT/'backend'))
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            python=os.getenv('BACKEND_PYTHON') or '/workspace/core/backend/.venv/bin/python'
            server=private/'api.py';server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',proxy_headers=False,access_log=False)\n')
            frontend=private/'frontend';shutil.copytree(ROOT/'frontend',frontend,ignore=shutil.ignore_patterns('node_modules','dist','__pycache__','.git'));(frontend/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
            bound_sources=['frontend/src/components/SharedDocumentOrganization.tsx','frontend/src/components/SharedDocumentsPanel.tsx','frontend/src/components/Chat.tsx','frontend/src/components/WorkHub.tsx','frontend/src/components/ContextualCopilot.tsx','frontend/src/lib/document-organization-api.ts','frontend/src/lib/document-organization-assist.ts','frontend/src/lib/shared-documents-api.ts','frontend/src/lib/socket.ts','frontend/src/styles/document-organization.css','frontend/src/styles/thread-history.css','frontend/src/lib/thread-history.ts','frontend/src/i18n/document-organization.sv.ts','backend/app/api/document_organization.py','backend/app/models/document_organization.py','backend/app/services/data_control.py','backend/app/api/shared_documents.py','backend/app/services/shared_documents.py','backend/app/api/secure_messaging.py','frontend/tests/document-organization-browser.py']
            bound_sources+=['frontend/tests/ui_navigation.py','frontend/tests/shared-documents-browser.py','frontend/src/styles/conversation-work.css','frontend/src/styles/flow-first.css','backend/app/api/channels.py']
            source_hashes={name:hashlib.sha256(((frontend/Path(name).relative_to('frontend')) if name.startswith('frontend/src/') else ROOT/name).read_bytes()).hexdigest() for name in bound_sources}
            vite=helpers.vite_launcher(private,base,app_port);vite_source=vite.read_text().replace('root:'+json.dumps(str(ROOT/'frontend')),'root:'+json.dumps(str(frontend))).replace(json.dumps(str(private/'vite-cache')),json.dumps(str(frontend/'.vite-cache'))).replace('server:{host:','server:{fs:{allow:'+json.dumps([str(private),str(ROOT/'frontend')])+'},host:');vite.write_text(vite_source)
            for command,name,cwd in [([python,str(server)],'api.log',ROOT/'backend'),(['node',str(vite)],'vite.log',frontend)]:
                stream=(private/name).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(base+'/ready',processes);helpers.ready(app+'/',processes)
            users=[];emails=[]
            for name in ['Organization author','Organization peer']:
                email='organization-'+secrets.token_hex(6)+'@example.com';emails.append(email);users.append(helpers.api(base,'POST','/auth/register',data={'name':name,'email':email,'password':doc.PASSWORD}))
            alice,bob=users;workspace=helpers.api(base,'POST','/workspaces',alice,{'name':'Organized writing'})
            channel=helpers.api(base,'POST','/channels',alice,{'name':'Research pages','workspace_id':workspace['id']})['id']
            invite=helpers.api(base,'POST','/channels/'+channel+'/invites',alice,{});helpers.api(base,'POST','/channels/invites/accept',bob,{'token':invite['token']})
            endpoint='/channels/'+channel+'/shared-document-organization'
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'),args=['--no-sandbox']);context=browser.new_context(viewport={'width':1440,'height':1050},reduced_motion='reduce');page=context.new_page();page.set_default_timeout(30000);errors=[];page.on('pageerror',lambda failure:errors.append(type(failure).__name__))
                try:
                    stage='real sign-in and shared document creation';panel=doc.login(page,app,emails[0],workspace['id'],alice['user_id'])
                    def create_page(title,body):
                        panel.get_by_role('button',name='New shared document',exact=True).click();panel.get_by_label('Document title',exact=True).fill(title);panel.get_by_role('button',name='Create document',exact=True).click();expect(panel.get_by_label('Document text',exact=True)).to_be_editable();panel.get_by_label('Document text',exact=True).fill(body);expect(panel.get_by_text('All edits synchronized',exact=True)).to_be_visible();panel.get_by_role('button',name='Documents',exact=True).click();expect(panel.get_by_role('region',name='Document stacks',exact=True)).to_be_visible()
                    create_page('Research brief','Current source material.');create_page('Research notes','Evidence and observations.');create_page('Budget','Current costs.')
                    docs=helpers.api(base,'GET','/channels/'+channel+'/shared-documents',alice);by_title={row['title']:row for row in docs};brief=by_title['Research brief']['id'];notes=by_title['Research notes']['id']
                    results['real_ui_create_and_reloadable_three_documents']=True
                    stage='quiet local stack suggestion with explicit accept dismiss and visible guarded Undo'
                    organize(panel).get_by_role('button',name='New stack',exact=True).click();organize(panel).get_by_label('Stack name',exact=True).fill('Research material');organize(panel).get_by_role('button',name='Create stack',exact=True).click()
                    expect(organize(panel).get_by_role('region',name='Research material',exact=True)).to_be_visible()
                    research=next(row for row in helpers.api(base,'GET',endpoint,alice)['stacks'] if row['title']=='Research material')
                    expect(panel.locator('.fc-document-stack-suggestion')).to_have_count(0)
                    organize(panel).get_by_label('Move Research brief to stack',exact=True).focus()
                    suggestion=panel.get_by_role('complementary',name='Suggested document stack',exact=True)
                    expect(suggestion).to_have_count(1);expect(suggestion.get_by_text('Shared words: research',exact=True)).to_be_visible()
                    assert not helpers.api(base,'GET',endpoint,alice)['placements']
                    suggestion.get_by_role('button',name='Dismiss suggestion',exact=True).click()
                    organize(panel).get_by_label('Move Research notes to stack',exact=True).focus();expect(suggestion).to_have_count(1)
                    organize(panel).get_by_label('Move Research brief to stack',exact=True).focus();expect(suggestion).to_have_count(0)
                    organize(panel).get_by_label('Move Research notes to stack',exact=True).focus();expect(suggestion).to_have_count(1)
                    suggestion.get_by_role('button',name='Move to Research material',exact=True).click()
                    expect(organize(panel).get_by_label('Move Research notes to stack',exact=True)).to_have_value(research['id'])
                    organize(panel).locator(':scope > summary').click()
                    expect(panel.locator('.fc-document-undo').get_by_role('button',name='Undo move',exact=True)).to_be_visible()
                    panel.locator('.fc-document-undo').get_by_role('button',name='Undo move',exact=True).click()
                    expect(organize(panel).get_by_label('Move Research notes to stack',exact=True)).to_have_value('')
                    organize(panel).get_by_role('button',name='Rename stack Research material',exact=True).click();organize(panel).get_by_label('Stack name',exact=True).fill('Research source');organize(panel).get_by_role('button',name='Save name',exact=True).click()
                    expect(organize(panel).get_by_role('region',name='Research source',exact=True)).to_be_visible()
                    organize(panel).locator(':scope > summary').click();panel.locator('.fc-document-undo').get_by_role('button',name='Undo rename',exact=True).click()
                    expect(organize(panel).get_by_role('region',name='Research material',exact=True)).to_be_visible()
                    organize(panel).get_by_role('button',name='Delete empty stack Research material',exact=True).click();expect(organize(panel).get_by_role('region',name='Research material',exact=True)).to_have_count(0)
                    organize(panel).get_by_role('button',name='New stack',exact=True).click();organize(panel).get_by_label('Stack name',exact=True).fill('Temporary group');organize(panel).get_by_role('button',name='Create stack',exact=True).click()
                    expect(organize(panel).get_by_role('region',name='Temporary group',exact=True)).to_be_visible()
                    organize(panel).locator(':scope > summary').click();panel.locator('.fc-document-undo').get_by_role('button',name='Undo stack creation',exact=True).click()
                    expect(organize(panel).get_by_role('region',name='Temporary group',exact=True)).to_have_count(0)
                    results['one_local_suggestion_explicit_dismiss_accept_and_metadata_only_move']=True
                    results['move_rename_empty_stack_creation_undo_visible_outside_collapsed_controls']=True
                    stage='stack create and keyboard move'
                    for title in ['Underlag','Decisions']:
                        organize(panel).get_by_role('button',name='New stack',exact=True).click();organize(panel).get_by_label('Stack name',exact=True).fill(title);organize(panel).get_by_role('button',name='Create stack',exact=True).click();expect(organize(panel).get_by_role('region',name=title,exact=True)).to_be_visible()
                    data=helpers.api(base,'GET',endpoint,alice);stacks={row['title']:row for row in data['stacks']};underlag=stacks['Underlag'];decisions=stacks['Decisions']
                    organize(panel).get_by_label('Move Research brief to stack',exact=True).select_option(underlag['id']);expect(organize(panel).get_by_role('region',name='Underlag',exact=True).get_by_role('button',name=re.compile('^Research brief'))).to_be_visible()
                    expect(organize(panel).get_by_label('Move Research brief to stack',exact=True)).to_be_focused()
                    page.reload(wait_until='domcontentloaded');panel=doc.open_documents(page);expect(organize(panel).get_by_role('region',name='Underlag',exact=True).get_by_role('button',name=re.compile('^Research brief'))).to_be_visible()
                    results['authenticated_stack_create_keyboard_move_focus_and_reload_persistence']=True
                    stage='actual pointer drag moves a page and keyboard selector returns it'
                    budget_card=organize(panel).locator('li[draggable=true]').filter(has_text=re.compile('^Budget'))
                    drag_listeners=start_drag_diagnostics(page,app,by_title['Budget']['id'],decisions['id'],[row['id'] for row in data['stacks']],drag_evidence)
                    capture_drag_diagnostics(page,drag_evidence,'before_original_drag')
                    budget_card.drag_to(organize(panel).get_by_role('region',name='Decisions',exact=True),source_position={'x':8,'y':8},target_position={'x':20,'y':20})
                    capture_drag_diagnostics(page,drag_evidence,'after_original_drag')
                    expect(organize(panel).get_by_role('region',name='Decisions',exact=True).get_by_role('button',name=re.compile('^Budget'))).to_be_visible()
                    organize(panel).get_by_label('Move Budget to stack',exact=True).select_option('')
                    expect(organize(panel).get_by_role('region',name='Unstacked',exact=True).get_by_role('button',name=re.compile('^Budget'))).to_be_visible()
                    capture_drag_diagnostics(page,drag_evidence,'after_original_keyboard_return',stop=True)
                    page.remove_listener('request',drag_listeners[0]);page.remove_listener('response',drag_listeners[1])
                    results['actual_pointer_drag_and_keyboard_return_update_real_placement']=True
                    stage='document text edits survive guarded move Undo'
                    organize(panel).get_by_role('button',name=re.compile('^Research brief')).click();edit_saved_document(panel);expect(panel.get_by_label('Document text',exact=True)).to_be_editable()
                    organize(panel).get_by_label('Move Research brief to stack',exact=True).select_option(decisions['id']);expect(panel.locator('.fc-document-undo').get_by_role('button',name='Undo move',exact=True)).to_be_visible()
                    panel.get_by_label('Document text',exact=True).fill('Newer synchronized source material.');expect(panel.get_by_text('All edits synchronized',exact=True)).to_be_visible();panel.locator('.fc-document-undo').get_by_role('button',name='Undo move',exact=True).click();expect(organize(panel).get_by_label('Move Research brief to stack',exact=True)).to_have_value(underlag['id']);expect(panel.get_by_label('Document text',exact=True)).to_have_value('Newer synchronized source material.')
                    results['undo_moves_metadata_only_and_preserves_later_yjs_text']=True
                    stage='second member changes invalidate stale Undo'
                    organize(panel).get_by_label('Move Research brief to stack',exact=True).select_option(decisions['id']);expect(panel.locator('.fc-document-undo').get_by_role('button',name='Undo move',exact=True)).to_be_visible()
                    data=helpers.api(base,'GET',endpoint,bob);placement=next(row for row in data['placements'] if row['document_id']==brief)
                    helpers.api(base,'PUT',endpoint+'/documents/'+brief+'/placement',bob,{'stack_id':underlag['id'],'stack_generation':underlag['generation'],'expected_version':placement['version']})
                    panel.locator('.fc-document-undo').get_by_role('button',name='Undo move',exact=True).click();expect(panel.get_by_role('alert').filter(has_text='moved since')).to_be_visible();expect(panel.get_by_label('Document text',exact=True)).to_have_value('Newer synchronized source material.');panel.get_by_role('button',name='Reload organization',exact=True).click();expect(organize(panel).get_by_label('Move Research brief to stack',exact=True)).to_have_value(underlag['id'])
                    results['stale_undo_rejected_after_other_member_move_without_text_loss']=True
                    stage='source-title suggestion explicit save and document navigation'
                    proposals=organize(panel).locator('.fc-document-proposals');expect(proposals.get_by_text('Research notes',exact=True)).to_be_visible();assert helpers.api(base,'GET',endpoint,alice)['relations']==[]
                    proposals.get_by_role('button',name='Save relation to Research notes',exact=True).click();related=organize(panel).get_by_role('region',name='Related documents',exact=True);expect(related.get_by_role('button',name='Research notes',exact=True)).to_be_visible();assert len(helpers.api(base,'GET',endpoint,bob)['relations'])==1
                    related.get_by_role('button',name='Research notes',exact=True).click();expect(panel.get_by_label('Title',exact=True)).to_have_value('Research notes');expect(organize(panel).get_by_role('region',name='Related documents',exact=True).get_by_role('button',name='Research brief',exact=True)).to_be_visible()
                    results['visible_title_proposal_requires_save_and_bidirectional_relation_opens_document']=True
                    stage='manual relation and removal'
                    organize(panel).get_by_label('Relate another document',exact=True).select_option(by_title['Budget']['id']);organize(panel).get_by_role('region',name='Related documents',exact=True).get_by_role('button',name='Save relation',exact=True).click();expect(organize(panel).get_by_role('button',name='Budget',exact=True)).to_be_visible();organize(panel).get_by_role('button',name='Remove relation to Budget',exact=True).click();expect(organize(panel).get_by_role('button',name='Budget',exact=True)).to_have_count(0)
                    results['manual_relation_creation_and_explicit_removal_persist']=True
                    stage='pending comment preserves a newer draft'
                    held=[]
                    def hold_comment(route):
                        if route.request.method=='POST':held.append(route)
                        else:route.continue_()
                    page.route('**/shared-documents/*/comments',hold_comment)
                    panel.get_by_label('Add a comment',exact=True).fill('Submitted budget comment');panel.get_by_role('button',name='Post comment',exact=True).click();expect(panel.get_by_role('button',name='Post comment',exact=True)).to_be_disabled();
                    for _ in range(100):
                        if held: break
                        page.wait_for_timeout(25)
                    assert len(held)==1
                    panel.get_by_label('Add a comment',exact=True).fill('Keep my newer draft');held.pop().continue_();expect(panel.get_by_text('Submitted budget comment',exact=True)).to_be_visible();expect(panel.get_by_label('Add a comment',exact=True)).to_have_value('Keep my newer draft');page.unroute('**/shared-documents/*/comments',hold_comment)
                    results['comment_post_never_clears_text_typed_while_request_pending']=True
                    stage='automatic stack discussion follows original document and focuses source'
                    page.once('dialog',lambda dialog:dialog.accept())
                    panel.get_by_role('button',name='Documents',exact=True).click()
                    expect(organize(panel).get_by_role('region',name='Unstacked',exact=True).get_by_text('Submitted budget comment',exact=True)).to_be_visible()
                    organize(panel).get_by_label('Move Research notes to stack',exact=True).select_option(underlag['id'])
                    underlag_region=organize(panel).get_by_role('region',name='Underlag',exact=True)
                    expect(underlag_region.get_by_text('Submitted budget comment',exact=True)).to_be_visible()
                    expect(organize(panel).get_by_role('region',name='Unstacked',exact=True).get_by_text('Submitted budget comment',exact=True)).to_have_count(0)
                    underlag_region.get_by_role('button',name='Comment in Research notes',exact=True).click()
                    expect(panel.get_by_label('Title',exact=True)).to_have_value('Research notes')
                    comment_row=panel.locator('.fc-shared-comments li').filter(has_text='Submitted budget comment');expect(comment_row).to_be_focused()
                    budget_proposal=organize(panel).locator('.fc-document-proposals');expect(budget_proposal.get_by_text('Budget',exact=True)).to_be_visible();expect(budget_proposal.get_by_role('button',name='View source comment',exact=True)).to_be_visible()
                    options(panel).get_by_role('button',name='Archive document',exact=True).click()
                    expect(panel.get_by_label('Title',exact=True)).to_have_count(0)
                    expect(organize(panel).get_by_role('region',name='Underlag',exact=True).get_by_text('Submitted budget comment',exact=True)).to_have_count(0)
                    options(panel).get_by_label('Show archived',exact=True).check();expect(organize(panel).get_by_text('Submitted budget comment',exact=True)).to_be_visible()
                    organize(panel).get_by_role('button',name=re.compile('^Research notes')).click();expect(panel.get_by_label('Title',exact=True)).to_have_value('Research notes');options(panel).get_by_role('button',name='Restore document',exact=True).click();expect(panel.get_by_label('Title',exact=True)).to_have_count(0)
                    options(panel).get_by_label('Show archived',exact=True).uncheck()
                    results['derived_original_comments_follow_move_focus_original_and_respect_archive_view']=True
                    stage='inline stack AI uses real reviewed document flow without an automatic provider request'
                    stack_ai=organize(panel).get_by_role('region',name='Underlag',exact=True).locator('.fc-document-stack-ai');stack_ai.locator('summary').click()
                    expect(stack_ai.get_by_role('group',name='Pages included in AI review',exact=True)).to_be_visible()
                    expect(stack_ai.get_by_role('checkbox',name='Research brief',exact=True)).to_be_checked();expect(stack_ai.get_by_role('checkbox',name='Research notes',exact=True)).to_be_checked()
                    expect(stack_ai.get_by_text('2 of 2 pages selected. The provider review enforces the 24,000-character total limit.',exact=True)).to_be_visible()
                    stack_ai.get_by_role('checkbox',name='Research notes',exact=True).uncheck();expect(stack_ai.get_by_text('1 of 2 pages selected. The provider review enforces the 24,000-character total limit.',exact=True)).to_be_visible()
                    expect(stack_ai.locator('.fc-contextual-copilot')).to_be_visible()
                    results['inline_stack_ai_selects_actual_pages_and_explicit_review_no_auto_send']=True
                    stage='actual Chat message action reviews and saves a source then reveals it without losing composer draft'
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    sent=helpers.api(base,'POST','/messages/channels/'+channel,alice,{'content':'Keep this research observation with the source pages.'})
                    message_row=page.locator('.fc-chat-message').filter(has_text='Keep this research observation with the source pages.')
                    expect(message_row).to_be_visible()
                    composer=page.locator('textarea.fc-chat-input')
                    if not composer.count(): composer=page.locator('.fc-chat-composer textarea').first
                    composer.fill('Unsent conversation draft')
                    reveal_message_actions(message_row);message_row.get_by_role('button',name='Save in document stack',exact=True).click()
                    panel=page.get_by_role('region',name='Work in this conversation',exact=True);review=panel.get_by_role('region',name='Save message in a document stack',exact=True)
                    expect(review.get_by_text('Keep this research observation with the source pages.',exact=True)).to_be_visible()
                    assert helpers.api(base,'GET',endpoint,alice)['message_references']==[]
                    review.get_by_label('Destination stack',exact=True).select_option(underlag['id']);review.get_by_role('button',name='Save message reference',exact=True).click()
                    source_region=organize(panel).get_by_role('region',name='Underlag',exact=True).get_by_role('region',name='Saved conversation sources',exact=True)
                    expect(source_region.get_by_text('Keep this research observation with the source pages.',exact=True)).to_be_visible()
                    refs=helpers.api(base,'GET',endpoint,bob)['message_references'];assert len(refs)==1 and refs[0]['message_id']==sent['id']
                    panel.evaluate('element=>{window.savedSourceWork=element}')
                    source_region.get_by_role('button',name='Open source message',exact=True).click();expect(panel).to_be_visible()
                    assert panel.evaluate('element=>element===window.savedSourceWork'),'Showing the source replaced the inline work region'
                    expect(page.locator('.fc-chat-message').filter(has_text='Keep this research observation with the source pages.')).to_be_visible();expect(composer).to_have_value('Unsent conversation draft')
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    panel=doc.open_documents(page);expect(organize(panel).get_by_role('region',name='Underlag',exact=True).get_by_role('region',name='Saved conversation sources',exact=True)).to_be_visible()
                    results['actual_chat_to_stack_explicit_reference_persistence_reveal_and_composer_draft_retained']=True
                    stage='actual thread reply source saves and reveals while preserving its unsent reply draft'
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    reply=helpers.api(base,'POST','/threads/messages/'+sent['id']+'/reply',bob,{'content':'A thread observation worth keeping with the research.'})
                    message_row=page.locator('.fc-chat-message').filter(has_text='Keep this research observation with the source pages.');reveal_message_actions(message_row);message_row.get_by_role('button',name='Reply in thread',exact=True).click()
                    thread=page.get_by_role('dialog',name='Thread',exact=True);reply_row=thread.locator('[data-thread-reply="'+reply['id']+'"]');expect(reply_row).to_be_visible()
                    thread.get_by_label('Reply in thread',exact=True).fill('Unsent thread perspective')
                    reply_row.get_by_role('button',name='Save in document stack',exact=True).click();expect(thread).to_have_count(0)
                    panel=page.get_by_role('region',name='Work in this conversation',exact=True);review=panel.get_by_role('region',name='Save message in a document stack',exact=True)
                    expect(review.get_by_text('A thread observation worth keeping with the research.',exact=True)).to_be_visible()
                    review.get_by_label('Destination stack',exact=True).select_option(underlag['id']);review.get_by_role('button',name='Save message reference',exact=True).click()
                    source_region=organize(panel).get_by_role('region',name='Underlag',exact=True).get_by_role('region',name='Saved conversation sources',exact=True)
                    saved_reply=source_region.locator('li').filter(has_text='A thread observation worth keeping with the research.');expect(saved_reply).to_be_visible()
                    panel.evaluate('element=>{window.savedThreadSourceWork=element}')
                    saved_reply.get_by_role('button',name='Open source message',exact=True).click()
                    thread=page.get_by_role('dialog',name='Thread',exact=True);expect(thread.locator('[data-thread-reply="'+reply['id']+'"]')).to_be_visible();expect(thread.get_by_label('Reply in thread',exact=True)).to_have_value('Unsent thread perspective')
                    retained_work=page.locator('.fc-conversation-work[data-work-kind="documents"]')
                    expect(retained_work).to_have_count(1)
                    assert retained_work.evaluate('element=>element===window.savedThreadSourceWork'),'Showing the thread source replaced the inline work region'
                    thread.get_by_role('button',name='Close thread',exact=True).click();expect(panel).to_be_visible();expect(composer).to_have_value('Unsent conversation draft')
                    assert panel.evaluate('element=>element===window.savedThreadSourceWork'),'Closing the thread replaced the retained inline work region'
                    panel=doc.open_documents(page);refs=helpers.api(base,'GET',endpoint,alice)['message_references'];assert len(refs)==2 and any(row['message_id']==reply['id'] for row in refs)
                    results['actual_thread_reply_reference_save_reveal_and_per_thread_draft_retained']=True
                    stage='empty-stack rename delete and scoped authorization'
                    if panel.get_by_role('button',name='Documents',exact=True).count(): panel.get_by_role('button',name='Documents',exact=True).click()
                    organize(panel).get_by_role('button',name='Rename stack Decisions',exact=True).click();organize(panel).get_by_label('Stack name',exact=True).fill('DECISIONS');organize(panel).get_by_role('button',name='Save name',exact=True).click();expect(organize(panel).get_by_role('region',name='DECISIONS',exact=True)).to_be_visible();organize(panel).get_by_role('button',name='Delete empty stack DECISIONS',exact=True).click();expect(organize(panel).get_by_role('region',name='DECISIONS',exact=True)).to_have_count(0)
                    assert helpers.rejects(base,'PATCH',endpoint+'/stacks/'+underlag['id'],bob,{'title':'Unauthorized rename','expected_version':underlag['version'],'expected_generation':underlag['generation']},statuses=(403,))==403
                    other_workspace=helpers.api(base,'POST','/workspaces',bob,{'name':'Other isolated workspace'});other=helpers.api(base,'POST','/channels',bob,{'name':'Other isolated channel','workspace_id':other_workspace['id']})['id'];assert helpers.rejects(base,'PUT','/channels/'+other+'/shared-document-organization/documents/'+brief+'/placement',bob,{'stack_id':None,'stack_generation':None,'expected_version':0},statuses=(404,))==404
                    results['case_only_rename_empty_delete_peer_management_guard_and_cross_channel_rejection']=True
                    stage='native encrypted title storage and mobile keyboard layout'
                    with sqlite3.connect(private/'proof.db') as connection:
                        ciphertexts=[row[0] for row in connection.execute('SELECT encrypted_payload FROM shareddocumentstack')];assert ciphertexts and all('Underlag' not in text and 'Research' not in text for text in ciphertexts)
                    page.set_viewport_size({'width':320,'height':950});expect(organize(panel).get_by_role('region',name='Underlag',exact=True)).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');assert panel.evaluate('el=>el.scrollWidth<=el.clientWidth+1');locale_owner=capture_work_card(page,panel);page.evaluate("async()=>{(await import('/src/i18n/core.ts')).applyLocale('sv')}");panel=locale_owner.get_by_role('region',name='Arbete i det här samtalet',exact=True);expect(organize(panel).get_by_role('button',name='Ny hög',exact=True)).to_be_visible();expect(organize(panel).get_by_role('button',name=re.compile('^Research brief'))).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.with_suffix('.png')));assert not errors
                    results['swedish_mobile_controls_keep_user_document_titles_literal']=True
                    results['encrypted_native_titles_320px_no_overflow_and_no_browser_exceptions']=True
                    for name in ('frontend/src/components/Chat.tsx','frontend/src/styles/flow-first.css'):
                        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==source_hashes[name], 'The bound runtime changed during the organization proof'
                    complete=True
                    print('PASS document organization: '+str(len(results))+' real browser groups',flush=True)
                except Exception:
                    capture_drag_diagnostics(page,drag_evidence,'failure',stop=True)
                    REPORT.parent.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('document-organization-failure.png')),full_page=True)
                    raise
                finally:context.close();browser.close()
        except Exception as failure:
            detail=str(failure).replace(doc.PASSWORD,'[redacted]');detail=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+','[redacted]',detail)
            print('FAIL document organization at '+stage+': '+type(failure).__name__+' '+detail[:2500],file=sys.stderr,flush=True)
            for name in ['api.log','vite.log']:
                if (private/name).exists():print(name+':\n'+(private/name).read_text()[-2500:],file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            try:
                helpers.stop(processes)
            finally:
                for stream in streams:stream.close()
                runtime_binding={name:{'before':source_hashes.get(name),'after':hashlib.sha256((ROOT/name).read_bytes()).hexdigest()} for name in ('frontend/src/components/Chat.tsx','frontend/src/styles/flow-first.css')}
                REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'ok':complete,'stage':stage,'scope':'Actual isolated authenticated API/Vite/Chromium + SQLite; native document organization only, no Matrix/provider acceptance','results':results,'drag_diagnostics':drag_evidence,'source_hashes':source_hashes,'runtime_source_binding':runtime_binding,'cleanup':'Own processes, temporary DB/keys/files/contexts removed in finally'},indent=2)+'\n')
if __name__=='__main__':main()
