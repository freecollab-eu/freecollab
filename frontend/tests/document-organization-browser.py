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
from playwright.sync_api import expect, sync_playwright
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('document_helpers',Path(__file__).with_name('shared-documents-browser.py'))
doc=importlib.util.module_from_spec(spec);spec.loader.exec_module(doc)
helpers=doc.helpers
REPORT=Path(os.getenv('DOCUMENT_ORGANIZATION_PROOF_REPORT',str(ROOT.parent/'.freecollab/proof-results/feature-audit-20261023/document-organization-browser.json')))


def main():
    results={}; processes=[];streams=[];stage='startup'
    with tempfile.TemporaryDirectory(prefix='freecollab-document-organization-') as directory:
        private=Path(directory)
        try:
            api_port,app_port=helpers.port(),helpers.port();base='http://127.0.0.1:'+str(api_port);app='http://127.0.0.1:'+str(app_port)
            environment=os.environ.copy();environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MESSAGE_ENCRYPTION_KEY_FILE=str(private/'message-keys.json'),ACCOUNT_SECURITY_KEY_FILE=str(private/'account.key'),MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),OIDC_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',COPILOT_ENABLED='false',REDIS_URL='',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT/'backend'))
            environment['NO_PROXY']=','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            python=os.getenv('BACKEND_PYTHON') or '/workspace/core/backend/.venv/bin/python'
            server=private/'api.py';server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port='+str(api_port)+',proxy_headers=False,access_log=False)\n')
            frontend=private/'frontend';shutil.copytree(ROOT/'frontend',frontend,ignore=shutil.ignore_patterns('node_modules','dist','__pycache__','.git'));(frontend/'node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
            bound_sources=['frontend/src/components/SharedDocumentOrganization.tsx','frontend/src/components/SharedDocumentsPanel.tsx','frontend/src/components/Chat.tsx','frontend/src/components/WorkHub.tsx','frontend/src/components/ContextualCopilot.tsx','frontend/src/lib/document-organization-api.ts','frontend/src/lib/shared-documents-api.ts','frontend/src/lib/socket.ts','frontend/src/styles/document-organization.css','frontend/src/styles/thread-history.css','frontend/src/lib/thread-history.ts','frontend/src/i18n/document-organization.sv.ts','backend/app/api/document_organization.py','backend/app/models/document_organization.py','backend/app/services/data_control.py','backend/app/api/shared_documents.py','backend/app/services/shared_documents.py','backend/app/api/secure_messaging.py','frontend/tests/document-organization-browser.py']
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
                        panel.get_by_role('button',name='New shared document',exact=True).click();panel.get_by_label('Document title',exact=True).fill(title);panel.get_by_role('button',name='Create document',exact=True).click();expect(panel.get_by_label('Document text',exact=True)).to_be_editable();panel.get_by_label('Document text',exact=True).fill(body);expect(panel.get_by_text('All edits synchronized',exact=True)).to_be_visible();panel.get_by_role('button',name='All documents',exact=True).click();expect(panel.get_by_role('region',name='Document stacks',exact=True)).to_be_visible()
                    create_page('Research brief','Current source material.');create_page('Research notes','Evidence and observations.');create_page('Budget','Current costs.')
                    docs=helpers.api(base,'GET','/channels/'+channel+'/shared-documents',alice);by_title={row['title']:row for row in docs};brief=by_title['Research brief']['id'];notes=by_title['Research notes']['id']
                    results['real_ui_create_and_reloadable_three_documents']=True
                    stage='stack create and keyboard move'
                    for title in ['Underlag','Decisions']:
                        panel.get_by_role('button',name='New stack',exact=True).click();panel.get_by_label('Stack name',exact=True).fill(title);panel.get_by_role('button',name='Create stack',exact=True).click();expect(panel.get_by_role('region',name=title,exact=True)).to_be_visible()
                    data=helpers.api(base,'GET',endpoint,alice);stacks={row['title']:row for row in data['stacks']};underlag=stacks['Underlag'];decisions=stacks['Decisions']
                    panel.get_by_label('Move Research brief to stack',exact=True).select_option(underlag['id']);expect(panel.get_by_role('region',name='Underlag',exact=True).get_by_role('button',name=re.compile('^Research brief'))).to_be_visible()
                    expect(panel.get_by_label('Move Research brief to stack',exact=True)).to_be_focused()
                    page.reload(wait_until='domcontentloaded');panel=doc.open_documents(page);expect(panel.get_by_role('region',name='Underlag',exact=True).get_by_role('button',name=re.compile('^Research brief'))).to_be_visible()
                    results['authenticated_stack_create_keyboard_move_focus_and_reload_persistence']=True
                    stage='actual pointer drag moves a page and keyboard selector returns it'
                    budget_card=panel.locator('li[draggable=true]').filter(has_text=re.compile('^Budget'))
                    budget_card.drag_to(panel.get_by_role('region',name='Decisions',exact=True))
                    expect(panel.get_by_role('region',name='Decisions',exact=True).get_by_role('button',name=re.compile('^Budget'))).to_be_visible()
                    panel.get_by_label('Move Budget to stack',exact=True).select_option('')
                    expect(panel.get_by_role('region',name='Unstacked',exact=True).get_by_role('button',name=re.compile('^Budget'))).to_be_visible()
                    results['actual_pointer_drag_and_keyboard_return_update_real_placement']=True
                    stage='document text edits survive guarded move Undo'
                    panel.get_by_role('button',name=re.compile('^Research brief')).click();expect(panel.get_by_label('Document text',exact=True)).to_be_editable()
                    panel.get_by_label('Move Research brief to stack',exact=True).select_option(decisions['id']);expect(panel.get_by_role('button',name='Undo move',exact=True)).to_be_visible()
                    panel.get_by_label('Document text',exact=True).fill('Newer synchronized source material.');expect(panel.get_by_text('All edits synchronized',exact=True)).to_be_visible();panel.get_by_role('button',name='Undo move',exact=True).click();expect(panel.get_by_label('Move Research brief to stack',exact=True)).to_have_value(underlag['id']);expect(panel.get_by_label('Document text',exact=True)).to_have_value('Newer synchronized source material.')
                    results['undo_moves_metadata_only_and_preserves_later_yjs_text']=True
                    stage='second member changes invalidate stale Undo'
                    panel.get_by_label('Move Research brief to stack',exact=True).select_option(decisions['id']);expect(panel.get_by_role('button',name='Undo move',exact=True)).to_be_visible()
                    data=helpers.api(base,'GET',endpoint,bob);placement=next(row for row in data['placements'] if row['document_id']==brief)
                    helpers.api(base,'PUT',endpoint+'/documents/'+brief+'/placement',bob,{'stack_id':underlag['id'],'stack_generation':underlag['generation'],'expected_version':placement['version']})
                    panel.get_by_role('button',name='Undo move',exact=True).click();expect(panel.get_by_role('alert').filter(has_text='moved since')).to_be_visible();expect(panel.get_by_label('Document text',exact=True)).to_have_value('Newer synchronized source material.');panel.get_by_role('button',name='Reload organization',exact=True).click();expect(panel.get_by_label('Move Research brief to stack',exact=True)).to_have_value(underlag['id'])
                    results['stale_undo_rejected_after_other_member_move_without_text_loss']=True
                    stage='source-title suggestion explicit save and document navigation'
                    proposals=panel.locator('.fc-document-proposals');expect(proposals.get_by_text('Research notes',exact=True)).to_be_visible();assert helpers.api(base,'GET',endpoint,alice)['relations']==[]
                    proposals.get_by_role('button',name='Save relation to Research notes',exact=True).click();related=panel.get_by_role('region',name='Related documents',exact=True);expect(related.get_by_role('button',name='Research notes',exact=True)).to_be_visible();assert len(helpers.api(base,'GET',endpoint,bob)['relations'])==1
                    related.get_by_role('button',name='Research notes',exact=True).click();expect(panel.get_by_label('Title',exact=True)).to_have_value('Research notes');expect(panel.get_by_role('region',name='Related documents',exact=True).get_by_role('button',name='Research brief',exact=True)).to_be_visible()
                    results['visible_title_proposal_requires_save_and_bidirectional_relation_opens_document']=True
                    stage='manual relation and removal'
                    panel.get_by_label('Relate another document',exact=True).select_option(by_title['Budget']['id']);panel.get_by_role('region',name='Related documents',exact=True).get_by_role('button',name='Save relation',exact=True).click();expect(panel.get_by_role('button',name='Budget',exact=True)).to_be_visible();panel.get_by_role('button',name='Remove relation to Budget',exact=True).click();expect(panel.get_by_role('button',name='Budget',exact=True)).to_have_count(0)
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
                    panel.get_by_role('button',name='All documents',exact=True).click()
                    expect(panel.get_by_role('region',name='Unstacked',exact=True).get_by_text('Submitted budget comment',exact=True)).to_be_visible()
                    panel.get_by_label('Move Research notes to stack',exact=True).select_option(underlag['id'])
                    underlag_region=panel.get_by_role('region',name='Underlag',exact=True)
                    expect(underlag_region.get_by_text('Submitted budget comment',exact=True)).to_be_visible()
                    expect(panel.get_by_role('region',name='Unstacked',exact=True).get_by_text('Submitted budget comment',exact=True)).to_have_count(0)
                    underlag_region.get_by_role('button',name='Comment in Research notes',exact=True).click()
                    expect(panel.get_by_label('Title',exact=True)).to_have_value('Research notes')
                    comment_row=panel.locator('.fc-shared-comments li').filter(has_text='Submitted budget comment');expect(comment_row).to_be_focused()
                    budget_proposal=panel.locator('.fc-document-proposals');expect(budget_proposal.get_by_text('Budget',exact=True)).to_be_visible();expect(budget_proposal.get_by_role('button',name='View source comment',exact=True)).to_be_visible()
                    panel.get_by_role('button',name='Archive document',exact=True).click()
                    expect(panel.get_by_role('region',name='Underlag',exact=True).get_by_text('Submitted budget comment',exact=True)).to_have_count(0)
                    panel.get_by_label('Show archived',exact=True).check();expect(panel.get_by_text('Submitted budget comment',exact=True)).to_be_visible()
                    panel.get_by_role('button',name=re.compile('^Research notes')).click();expect(panel.get_by_label('Title',exact=True)).to_have_value('Research notes');panel.get_by_role('button',name='Restore document',exact=True).click()
                    panel.get_by_label('Show archived',exact=True).uncheck()
                    results['derived_original_comments_follow_move_focus_original_and_respect_archive_view']=True
                    stage='inline stack AI uses real reviewed document flow without an automatic provider request'
                    stack_ai=panel.get_by_role('region',name='Underlag',exact=True).locator('.fc-document-stack-ai');stack_ai.locator('summary').click()
                    expect(stack_ai.get_by_role('group',name='Pages included in AI review',exact=True)).to_be_visible()
                    expect(stack_ai.get_by_role('checkbox',name='Research brief',exact=True)).to_be_checked();expect(stack_ai.get_by_role('checkbox',name='Research notes',exact=True)).to_be_checked()
                    expect(stack_ai.get_by_text('2 of 2 pages selected. The provider review enforces the 24,000-character total limit.',exact=True)).to_be_visible()
                    stack_ai.get_by_role('checkbox',name='Research notes',exact=True).uncheck();expect(stack_ai.get_by_text('1 of 2 pages selected. The provider review enforces the 24,000-character total limit.',exact=True)).to_be_visible()
                    expect(stack_ai.locator('.fc-contextual-copilot')).to_be_visible()
                    results['inline_stack_ai_selects_actual_pages_and_explicit_review_no_auto_send']=True
                    stage='actual Chat message action reviews and saves a source then reveals it without losing composer draft'
                    panel.get_by_role('button',name='Close workspace tools',exact=True).click()
                    sent=helpers.api(base,'POST','/messages/channels/'+channel,alice,{'content':'Keep this research observation with the source pages.'})
                    message_row=page.locator('.fc-chat-message').filter(has_text='Keep this research observation with the source pages.')
                    expect(message_row).to_be_visible()
                    composer=page.locator('textarea.fc-chat-input')
                    if not composer.count(): composer=page.locator('.fc-chat-composer textarea').first
                    composer.fill('Unsent conversation draft')
                    message_row.hover();message_row.get_by_role('button',name='Save in document stack',exact=True).click()
                    panel=page.get_by_role('dialog',name='Workspace tools',exact=True);review=panel.get_by_role('region',name='Save message in a document stack',exact=True)
                    expect(review.get_by_text('Keep this research observation with the source pages.',exact=True)).to_be_visible()
                    assert helpers.api(base,'GET',endpoint,alice)['message_references']==[]
                    review.get_by_label('Destination stack',exact=True).select_option(underlag['id']);review.get_by_role('button',name='Save message reference',exact=True).click()
                    source_region=panel.get_by_role('region',name='Underlag',exact=True).get_by_role('region',name='Saved conversation sources',exact=True)
                    expect(source_region.get_by_text('Keep this research observation with the source pages.',exact=True)).to_be_visible()
                    refs=helpers.api(base,'GET',endpoint,bob)['message_references'];assert len(refs)==1 and refs[0]['message_id']==sent['id']
                    source_region.get_by_role('button',name='Open source message',exact=True).click();expect(panel).to_have_count(0)
                    expect(page.locator('.fc-chat-message').filter(has_text='Keep this research observation with the source pages.')).to_be_visible();expect(composer).to_have_value('Unsent conversation draft')
                    panel=doc.open_documents(page);expect(panel.get_by_role('region',name='Underlag',exact=True).get_by_role('region',name='Saved conversation sources',exact=True)).to_be_visible()
                    results['actual_chat_to_stack_explicit_reference_persistence_reveal_and_composer_draft_retained']=True
                    stage='actual thread reply source saves and reveals while preserving its unsent reply draft'
                    panel.get_by_role('button',name='Close workspace tools',exact=True).click()
                    reply=helpers.api(base,'POST','/threads/messages/'+sent['id']+'/reply',bob,{'content':'A thread observation worth keeping with the research.'})
                    message_row=page.locator('.fc-chat-message').filter(has_text='Keep this research observation with the source pages.');message_row.hover();message_row.get_by_role('button',name='Reply in thread',exact=True).click()
                    thread=page.get_by_role('dialog',name='Thread',exact=True);reply_row=thread.locator('[data-thread-reply="'+reply['id']+'"]');expect(reply_row).to_be_visible()
                    thread.get_by_label('Reply in thread',exact=True).fill('Unsent thread perspective')
                    reply_row.get_by_role('button',name='Save in document stack',exact=True).click();expect(thread).to_have_count(0)
                    panel=page.get_by_role('dialog',name='Workspace tools',exact=True);review=panel.get_by_role('region',name='Save message in a document stack',exact=True)
                    expect(review.get_by_text('A thread observation worth keeping with the research.',exact=True)).to_be_visible()
                    review.get_by_label('Destination stack',exact=True).select_option(underlag['id']);review.get_by_role('button',name='Save message reference',exact=True).click()
                    source_region=panel.get_by_role('region',name='Underlag',exact=True).get_by_role('region',name='Saved conversation sources',exact=True)
                    saved_reply=source_region.locator('li').filter(has_text='A thread observation worth keeping with the research.');expect(saved_reply).to_be_visible()
                    saved_reply.get_by_role('button',name='Open source message',exact=True).click();expect(panel).to_have_count(0)
                    thread=page.get_by_role('dialog',name='Thread',exact=True);expect(thread.locator('[data-thread-reply="'+reply['id']+'"]')).to_be_visible();expect(thread.get_by_label('Reply in thread',exact=True)).to_have_value('Unsent thread perspective')
                    thread.get_by_role('button',name='Close thread',exact=True).click();expect(composer).to_have_value('Unsent conversation draft')
                    panel=doc.open_documents(page);refs=helpers.api(base,'GET',endpoint,alice)['message_references'];assert len(refs)==2 and any(row['message_id']==reply['id'] for row in refs)
                    results['actual_thread_reply_reference_save_reveal_and_per_thread_draft_retained']=True
                    stage='empty-stack rename delete and scoped authorization'
                    if panel.get_by_role('button',name='All documents',exact=True).count(): panel.get_by_role('button',name='All documents',exact=True).click()
                    panel.get_by_role('button',name='Rename stack Decisions',exact=True).click();panel.get_by_label('Stack name',exact=True).fill('DECISIONS');panel.get_by_role('button',name='Save name',exact=True).click();expect(panel.get_by_role('region',name='DECISIONS',exact=True)).to_be_visible();panel.get_by_role('button',name='Delete empty stack DECISIONS',exact=True).click();expect(panel.get_by_role('region',name='DECISIONS',exact=True)).to_have_count(0)
                    assert helpers.rejects(base,'PATCH',endpoint+'/stacks/'+underlag['id'],bob,{'title':'Unauthorized rename','expected_version':underlag['version'],'expected_generation':underlag['generation']},statuses=(403,))==403
                    other_workspace=helpers.api(base,'POST','/workspaces',bob,{'name':'Other isolated workspace'});other=helpers.api(base,'POST','/channels',bob,{'name':'Other isolated channel','workspace_id':other_workspace['id']})['id'];assert helpers.rejects(base,'PUT','/channels/'+other+'/shared-document-organization/documents/'+brief+'/placement',bob,{'stack_id':None,'stack_generation':None,'expected_version':0},statuses=(404,))==404
                    results['case_only_rename_empty_delete_peer_management_guard_and_cross_channel_rejection']=True
                    stage='native encrypted title storage and mobile keyboard layout'
                    with sqlite3.connect(private/'proof.db') as connection:
                        ciphertexts=[row[0] for row in connection.execute('SELECT encrypted_payload FROM shareddocumentstack')];assert ciphertexts and all('Underlag' not in text and 'Research' not in text for text in ciphertexts)
                    page.set_viewport_size({'width':320,'height':950});expect(panel.get_by_role('region',name='Underlag',exact=True)).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');assert panel.evaluate('el=>el.scrollWidth<=el.clientWidth+1');page.evaluate("async()=>{(await import('/src/i18n/core.ts')).applyLocale('sv')}");panel=page.get_by_role('dialog',name='Arbetsytans verktyg',exact=True);expect(panel.get_by_role('button',name='Ny hög',exact=True)).to_be_visible();expect(panel.get_by_role('button',name=re.compile('^Research brief'))).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');REPORT.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(REPORT.with_suffix('.png')));assert not errors
                    results['swedish_mobile_controls_keep_user_document_titles_literal']=True
                    results['encrypted_native_titles_320px_no_overflow_and_no_browser_exceptions']=True
                    print('PASS document organization: '+str(len(results))+' real browser groups',flush=True)
                finally:context.close();browser.close()
        except Exception as failure:
            detail=str(failure).replace(doc.PASSWORD,'[redacted]');detail=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+','[redacted]',detail)
            print('FAIL document organization at '+stage+': '+type(failure).__name__+' '+detail[:2500],file=sys.stderr,flush=True)
            for name in ['api.log','vite.log']:
                if (private/name).exists():print(name+':\n'+(private/name).read_text()[-2500:],file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams:stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'scope':'Actual isolated authenticated API/Vite/Chromium + SQLite; native document organization only, no Matrix/provider acceptance','results':results,'source_hashes':source_hashes,'cleanup':'Own processes, temporary DB/keys/files/contexts removed in finally'},indent=2)+'\n')
if __name__=='__main__':main()
