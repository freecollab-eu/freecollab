#!/usr/bin/env python3
"""Real App text-to-task review and expanded document/source continuity.

Own API/SQLite/Vite/browser only. The document update gate waits before the real
production handler; it never substitutes an API response or editing state.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import expect, sync_playwright
from browser_conditions import wait_for_awaited_condition
from ui_navigation import choose_chat_channel, close_work_card, capture_work_card, work_surface

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
REPORT = Path(os.getenv('NATURAL_COLLABORATION_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/natural-collaboration-browser.json')))


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


flow = module('natural_flow_helpers', 'conversation-flow-browser.py')
meeting = module('natural_frontend_helpers', 'meetings-browser.py')


def select_excerpt(locator, quote):
    locator.scroll_into_view_if_needed()
    locator.evaluate('''(root,quote)=>{
      const start=root.textContent.indexOf(quote);if(start<0)throw Error('Exact source quote missing');
      const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node,offset=0,begin,end;
      while(node=walker.nextNode()){const next=offset+node.length;
        if(!begin&&start>=offset&&start<next)begin=[node,start-offset];
        if(start+quote.length>offset&&start+quote.length<=next){end=[node,start+quote.length-offset];break;}offset=next;}
      if(!begin||!end)throw Error('Exact source range unavailable');
      const range=document.createRange();range.setStart(...begin);range.setEnd(...end);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      if(selection.toString()!==quote)throw Error('Browser selection changed the quote');
    }''', quote)


def wait_file(page, path):
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline: raise AssertionError('The real document update did not reach its private gate')
        page.wait_for_timeout(30)


def wait_relation(page, base, token, source, kind, identifier):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        rows = flow.native(base, token, '/channels/shared/conversation-work?source_message_id=' + source)['items']
        found = [row for row in rows if row['resource_kind'] == kind and row['resource_id'] == identifier]
        if found: return found[0]
        page.wait_for_timeout(50)
    raise AssertionError('The saved work was not linked to its actual source message')


def main():
    checks, stage, processes, streams = {}, 'private actual product services', [], []
    source_hashes, document_probe = {}, {}
    with tempfile.TemporaryDirectory(prefix='freecollab-natural-collaboration-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = flow.port(), flow.port()
            base, origin = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            launcher = meeting.create_frontend(private, base, app_port)
            web = private / 'web'
            shutil.copytree(ROOT / 'frontend/src', web / 'src')
            shutil.copytree(ROOT / 'frontend/public', web / 'public')
            shutil.copyfile(ROOT / 'frontend/package.json', web / 'package.json')
            (web / 'base.css').write_text('\n'.join(line for line in (web / 'src/index.css').read_text().splitlines() if not line.lstrip().startswith(('@import "tailwindcss"', '@config '))) + '\n.natural-proof-language{padding:4px 16px;background:var(--fc-surface)}.natural-proof-language .fc-language{margin:0}.fc-chat{height:calc(100dvh - 68px)}\n')
            (web / 'entry.tsx').write_text('''import React from'react';import{createRoot}from'react-dom/client';
import{LocaleProvider,LanguagePicker}from'./src/i18n';import{AppearanceProvider}from'./src/appearance/AppearanceProvider';
import{AccountProvider}from'./src/account/AccountProvider';import App from'./src/App';import'./base.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><LocaleProvider><AppearanceProvider><AccountProvider><div className="natural-proof-language"><LanguagePicker/></div><App/></AccountProvider></AppearanceProvider></LocaleProvider></React.StrictMode>);
''')
            # Use the existing committed optimizer barrier against this unedited
            # App snapshot, including the same dynamically imported secure roots.
            vite_source = launcher.read_text().replace('/@fs' + str(ROOT / 'frontend/src'), '/src')
            assert 'proxy:{"/api":' in vite_source
            launcher.write_text(vite_source.replace('proxy:{"/api":', 'proxy:{"/socket.io":{target:' + json.dumps(base) + ',ws:true},"/api":'))
            for path in (web / 'src').rglob('*'):
                if path.is_file(): source_hashes['frontend/src/' + str(path.relative_to(web / 'src'))] = hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (ROOT / 'backend/app').rglob('*.py'):
                source_hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            for name in ['frontend/package.json', 'frontend/tests/natural-collaboration-browser.py', 'frontend/tests/meetings-browser.py', 'frontend/tests/conversation-flow-browser.py', 'frontend/tests/ui_navigation.py', 'frontend/tests/browser_conditions.py']:
                source_hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DEMO_INSTANCE='false', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', COPILOT_ENABLED='false', PUSH_ENABLED='false', PUBLIC_APP_URL=origin, CORS_ORIGINS=json.dumps([origin]), PYTHONPATH=str(ROOT / 'backend'), NATURAL_PROOF_PRIVATE=str(private))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            api = private / 'api.py'
            api.write_text('''import asyncio,json,os
from datetime import datetime,timedelta
from pathlib import Path
from fastapi import Request
from sqlmodel import Session
from app.main import app,socket_app
from app.database import init_db,engine
from app.models.user import User
from app.models.channel import Channel,ChannelMember
from app.models.workspace import Workspace,WorkspaceMember,WorkspaceChannel
from app.models.preference import UserLanguage
from app.models.message import Message
from app.services.auth import create_token
from app.services.crypto import encrypt_message
import uvicorn
private=Path(os.environ['NATURAL_PROOF_PRIVATE']);init_db()
with Session(engine) as session:
 session.add_all([User(id=name,name=label,email=name+'@natural-proof.example',password_hash='unused')for name,label in [('alpha','Linnea Berg'),('beta','Oskar Lind')]])
 session.add(Workspace(id='team',name='Nord Studio'));session.add(Channel(id='shared',name='Courtyard'));session.commit()
 session.add_all([WorkspaceMember(workspace_id='team',user_id=name,role='owner'if name=='alpha'else'member')for name in ['alpha','beta']])
 session.add(WorkspaceChannel(workspace_id='team',channel_id='shared'))
 session.add_all([ChannelMember(channel_id='shared',user_id=name,role='admin'if name=='alpha'else'member')for name in ['alpha','beta']])
 session.add(UserLanguage(user_id='alpha',locale='en'))
 examples=[('task-source','Before this selected next step. Oskar reviews the timber samples on 2026-11-05. Keep this closing context outside the draft.',None,'beta'),('thread-source','Let us review the planting plan in this thread.',None,'alpha'),('thread-reply','Some context. Linnea prepares the planting plan on 2026-11-08. More context.', 'thread-source','beta'),('document-source','The courtyard table can stay beside the doors. Keep room for people to pause.',None,'beta')]
 for index,(identifier,text,parent,author)in enumerate(examples):
  session.add(Message(id=identifier,channel_id='shared',author_id=author,content=encrypt_message(text,'shared'),encrypted=True,parent_id=parent,thread_count=1 if identifier=='thread-source' else 0,created_at=datetime.utcnow()+timedelta(seconds=index)))
 session.commit()
 fd=os.open(private/'session.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w')as stream:json.dump({'user_id':'alpha','access_token':create_token('alpha',session)},stream)
@app.middleware('http')
async def hold_document(request:Request,call_next):
 if(private/'hold').exists()and request.method=='POST'and'/shared-documents/'in request.url.path and request.url.path.endswith('/updates'):
  (private/'captured').touch()
  for _ in range(600):
   if(private/'release').exists():break
   await asyncio.sleep(.05)
 return await call_next(request)
uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',lifespan='off',access_log=False,proxy_headers=False)
''')
            for command, name, cwd in [([PYTHON, str(api)], 'api.log', ROOT / 'backend'), (['node', str(launcher)], 'vite.log', ROOT / 'frontend')]:
                stream = (private / name).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            flow.ready(base + '/ready', processes); meeting.ready_frontend(origin, processes)
            session = json.loads((private / 'session.json').read_text()); token = session['access_token']
            flow.native(base, token, '/channels/shared/projects', {'id': 'natural-project', 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Courtyard next steps', 'description': '', 'color': 'sage'}}, 'POST')
            tasks = lambda: flow.native(base, token, '/channels/shared/projects/natural-project/tasks')['items']
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1050}, locale='en-US', timezone_id='Europe/Stockholm')
                context.add_init_script('localStorage.setItem("freecollab_token",' + json.dumps(token) + ');localStorage.setItem("freecollab_user_id","alpha");localStorage.setItem("freecollab.workspace:alpha","team")')
                page = context.new_page(); page.set_default_timeout(30000)
                errors, console, posts, failed_http = [], [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda value: console.append({'type': value.type, 'text': value.text[:1200]}))
                page.on('response', lambda response: failed_http.append({'path': response.url.split('?')[0].replace(origin, '').replace(base, ''), 'status': response.status}) if response.status >= 400 else None)
                page.on('request', lambda request: posts.append(request.url.split('/api')[-1]) if request.method == 'POST' and '/projects/' in request.url and '/tasks' in request.url else None)
                try:
                    stage = 'actual full-App entry and channel navigation'
                    page.goto(origin, wait_until='domcontentloaded'); choose_chat_channel(page, 'Courtyard')
                    composer = page.locator('.fc-chat-composer textarea'); expect(composer).to_be_visible()
                    expect(page.locator('.fc-chat-connection')).to_have_count(0)
                    checks['actual_full_app_connects_live_through_its_socket_io_proxy'] = True
                    chat_draft = 'Keep this unsent conversation draft. Åäö.'; composer.fill(chat_draft)
                    stage = 'real main message selection, local member/date suggestion and explicit native save'
                    select_excerpt(page.locator('[data-message-source-id="task-source"] [data-message-source-text]'), 'Oskar reviews the timber samples on 2026-11-05.')
                    task_action = page.locator('.fc-message-task-selection'); expect(task_action).to_have_text('Make a next step')
                    assert not tasks() and not posts
                    task_action.click()
                    form = page.get_by_role('form', name='New task', exact=True); expect(form).to_be_visible()
                    expect(form.get_by_label('Task title', exact=True)).to_have_value('Oskar reviews the timber samples on 2026-11-05.')
                    expect(form.get_by_label('Description', exact=True)).to_have_value('Oskar reviews the timber samples on 2026-11-05.')
                    expect(form.get_by_label('Responsible person', exact=True)).to_have_value('beta')
                    expect(form.get_by_label('Due date', exact=True)).to_have_value('2026-11-05')
                    expect(form.get_by_role('status')).to_contain_text('Review the suggested title, person and date.')
                    assert not tasks() and not posts
                    form.get_by_label('Task title', exact=True).fill('Review timber samples together')
                    form.get_by_label('Responsible person', exact=True).select_option('alpha')
                    form.get_by_label('Due date', exact=True).fill('2026-11-06')
                    assert not tasks() and not posts
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/projects/natural-project/tasks')) as saved:
                        form.get_by_role('button', name='Save task', exact=True).click()
                    task = saved.value.json(); assert saved.value.ok
                    assert task['fields']['title'] == 'Review timber samples together' and task['fields']['assignee_user_id'] == 'alpha' and task['fields']['due_date'] == '2026-11-06'
                    relation = wait_relation(page, base, token, 'task-source', 'task', task['id']); assert relation['project_id'] == 'natural-project'
                    expect(composer).to_have_value(chat_draft); assert len(posts) == 1
                    checks['main_text_selection_real_member_date_review_no_write_before_save_actual_task_and_source_link'] = True
                    print('Natural collaboration: main selection and reviewed native task publication passed.', flush=True)
                    close_work_card(page, form if form.count() else capture_work_card(page))

                    stage = 'actual thread reply selection keeps original reply linkage'
                    parent = page.locator('[data-message-source-id="thread-source"]').first
                    parent.get_by_role('button', name='More message actions', exact=True).click()
                    parent.get_by_role('button', name='Reply in thread', exact=True).click()
                    thread = page.get_by_role('dialog', name='Thread', exact=True); expect(thread).to_be_visible()
                    select_excerpt(thread.locator('[data-message-source-id="thread-reply"] [data-message-source-text]'), 'Linnea prepares the planting plan on 2026-11-08.')
                    selection_action = page.locator('.fc-message-task-selection'); expect(selection_action).to_be_visible()
                    assert selection_action.evaluate('(node,dialog)=>node.closest(\'[role="dialog"], [aria-modal="true"]\')===dialog', thread.element_handle())
                    selection_action.focus(); expect(selection_action).to_be_focused()
                    selection_action.press('Escape'); expect(selection_action).to_have_count(0); expect(thread).to_be_visible()
                    thread_quote = 'Linnea prepares the planting plan on 2026-11-08'
                    select_excerpt(thread.locator('[data-message-source-id="thread-reply"] [data-message-source-text]'), thread_quote)
                    selection_action = page.locator('.fc-message-task-selection'); expect(selection_action).to_be_visible()
                    selection_action.focus(); expect(selection_action).to_be_focused()
                    selection_action.press('Enter')
                    form = page.get_by_role('form', name='New task', exact=True); expect(form).to_be_visible()
                    expect(form.get_by_label('Description', exact=True)).to_have_value(thread_quote)
                    expect(form.get_by_label('Responsible person', exact=True)).to_have_value('alpha')
                    expect(form.get_by_label('Due date', exact=True)).to_have_value('2026-11-08')
                    assert len(tasks()) == 1 and len(posts) == 1
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/projects/natural-project/tasks')) as saved:
                        form.get_by_role('button', name='Save task', exact=True).click()
                    reply_task = saved.value.json(); assert saved.value.ok
                    relation = wait_relation(page, base, token, 'thread-reply', 'task', reply_task['id'])
                    assert relation['discussion_message_id'] == 'thread-source'
                    checks['thread_text_selection_reviewed_native_task_has_exact_reply_and_discussion_relation'] = True
                    checks['thread_selection_toolbar_is_modal_keyboard_reachable_and_first_escape_preserves_thread'] = True
                    print('Natural collaboration: thread selection and actual reply/discussion relation passed.', flush=True)
                    close_work_card(page)
                    thread = page.get_by_role('dialog', name='Thread', exact=True)
                    expect(thread).to_be_visible()
                    thread.get_by_role('button', name='Close thread', exact=True).click()
                    expect(thread).to_have_count(0)

                    stage = 'source-created document and real pending Yjs update'
                    message = page.locator('[data-message-source-id="document-source"]').first
                    message.get_by_role('button', name='More message actions', exact=True).click()
                    message.get_by_role('button', name='Continue in a document', exact=True).click()
                    work = capture_work_card(page).get_by_role('region', name='Work in this conversation', exact=True)
                    title = work.get_by_label('Document title', exact=True); expect(title).to_be_editable(); title.fill('Courtyard layout notes')
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/channels/shared/shared-documents')) as created:
                        work.get_by_role('button', name='Create document', exact=True).click()
                    document = created.value.json(); assert created.value.ok
                    body = work.get_by_label('Document text', exact=True); expect(body).to_be_editable()
                    wait_relation(page, base, token, 'document-source', 'document', document['id'])
                    composer.fill(chat_draft); composer.evaluate('(node)=>{window.naturalChat=node;node.setSelectionRange(5,12)}')
                    original_body = body.element_handle()
                    (private / 'hold').touch()
                    document_text = 'Actual pending Yjs edit: keep the table beside the doors, with room to pause.'
                    body.fill(document_text); body.evaluate('(node)=>node.setSelectionRange(7,19)')
                    wait_file(page, private / 'captured')
                    work.get_by_role('button', name='Expand work area', exact=True).click()
                    surface = work_surface(page, work); expect(surface).to_have_attribute('data-workspace-mode', 'expanded')
                    ribbon = work.get_by_role('region', name='Source conversation', exact=True); expect(ribbon).to_be_visible()
                    expect(ribbon).to_contain_text('Courtyard'); expect(ribbon).to_contain_text('Oskar Lind')
                    expect(ribbon).to_contain_text('The courtyard table can stay beside the doors.')
                    assert body.evaluate('(node,old)=>node===old&&old.isConnected', original_body)
                    body.focus(); body.evaluate('(node)=>node.setSelectionRange(7,19)')
                    ribbon.get_by_role('button', name='Back to source message', exact=True).click()
                    expect(surface).to_have_attribute('data-workspace-mode', 'split')
                    expect(page.locator('#message-document-source')).to_be_focused()
                    expect(body).to_have_value(document_text)
                    assert body.evaluate('(node,old)=>node===old&&old.isConnected&&node.selectionStart===7&&node.selectionEnd===19', original_body)
                    expect(composer).to_have_value(chat_draft)
                    assert composer.evaluate('(node)=>node===window.naturalChat&&node.selectionStart===5&&node.selectionEnd===12')
                    checks['expanded_source_ribbon_returns_to_exact_message_preserving_editor_dom_caret_pending_yjs_and_chat_draft'] = True
                    print('Natural collaboration: source return retained actual pending editor, caret and chat draft.', flush=True)
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/shared-documents/' + document['id'] + '/updates')) as committed:
                        (private / 'release').touch()
                    assert committed.value.ok, 'The real retained document update was not accepted'
                    document_probe = page.evaluate('''async id=>{const{readSharedDocumentForSearch}=await import('/src/lib/shared-documents-api.ts');const state=await readSharedDocumentForSearch('shared',id);return{text:state.text,sequence:state.sequence}}''', document['id'])
                    wait_for_awaited_condition(page, '''async({id,expected})=>{const{readSharedDocumentForSearch}=await import('/src/lib/shared-documents-api.ts');const state=await readSharedDocumentForSearch('shared',id);return state.text===expected&&state.sequence>=2}''', arg={'id': document['id'], 'expected': document_text}, timeout=30000)
                    state = page.evaluate('''async id=>{const{readSharedDocumentForSearch}=await import('/src/lib/shared-documents-api.ts');return readSharedDocumentForSearch('shared',id)}''', document['id'])
                    assert state['text'] == document_text and state['sequence'] >= 2
                    checks['retained_real_yjs_update_reaches_native_api_and_reconstructs_exact_saved_text'] = True

                    stage = 'Swedish source return and 320px document continuity'
                    card = capture_work_card(page, work)
                    page.get_by_role('combobox', name='Language', exact=True).select_option('sv')
                    page.set_viewport_size({'width': 320, 'height': 850})
                    work = card.get_by_role('region', name='Arbete i det här samtalet', exact=True)
                    body = work.get_by_label('Dokumenttext', exact=True)
                    work.get_by_role('button', name='Förstora arbetsytan', exact=True).click()
                    ribbon = work.get_by_role('region', name='Ursprungligt samtal', exact=True); expect(ribbon).to_be_visible()
                    returning = ribbon.get_by_role('button', name='Tillbaka till ursprungsmeddelandet', exact=True); expect(returning).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    REPORT.parent.mkdir(parents=True, exist_ok=True); page.screenshot(path=str(REPORT.with_name('expanded-source-sv-320.png')))
                    returning.click(); expect(page.locator('#message-document-source')).to_be_focused()
                    expect(body).to_have_value(document_text); expect(composer).to_have_value(chat_draft)
                    assert body.evaluate('(node,old)=>node===old&&old.isConnected', original_body)
                    checks['swedish_320px_source_ribbon_has_no_overflow_and_retains_actual_document_and_draft'] = True
                    assert not errors, 'Actual App emitted browser errors'
                    checks['zero_browser_page_errors'] = True
                except Exception:
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('natural-failure.png')))
                    diagnostic = json.dumps({'stage': stage, 'page_errors': errors, 'console': console[-40:], 'failed_http': failed_http[-40:], 'body': page.locator('body').inner_text()[-18000:], 'first_post_ack_document_state': document_probe, 'checks': checks}, indent=2)
                    for secret in [token, environment['JWT_SECRET'], environment['ACCOUNT_SECURITY_KEY'], environment['MESSAGE_ENCRYPTION_KEYS']]:
                        diagnostic = diagnostic.replace(secret, '[redacted]')
                    REPORT.with_name('natural-failure.json').write_text(diagnostic)
                    raise
                finally:
                    context.close(); browser.close()
        except Exception:
            print('FAILED natural collaboration stage: ' + stage, file=sys.stderr)
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            for name in ['api.log', 'vite.log']:
                if (private / name).exists(): shutil.copyfile(private / name, REPORT.with_name('natural-' + name))
            raise
        finally:
            (private / 'release').touch()
            for process in reversed(processes):
                if process.poll() is None: os.killpg(process.pid, signal.SIGTERM)
            for process in reversed(processes):
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            for stream in streams: stream.close()
    drift = [name for name, digest in source_hashes.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Actual unedited App/Chat/WorkHub/ProjectTasks/Yjs snapshot, real private production API/SQLite, native members/messages/task publication/source links, genuine browser text selection, actual pending update gate, EN/SV/320px; no UI/API responses mocked, no external providers.', 'checks': checks, 'first_post_ack_document_state': document_probe, 'source_sha256': source_hashes, 'current_checkout_mismatches': drift, 'runtime_source_unchanged': not drift, 'cleanup': 'Own API/Vite/browser/session keys/database/private source snapshot removed; existing services untouched.'}, indent=2) + '\n')
    assert not drift, 'Product source changed during this proof; rerun the final frozen source: ' + ', '.join(drift[:5])
    print('Natural collaboration browser proof passed: ' + str(len(checks)) + ' groups.')


if __name__ == '__main__': main()
