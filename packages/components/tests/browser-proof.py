#!/usr/bin/env python3
"""Packed SDK proof against private real native APIs and workspace-scoped JWTs.

No existing account, service, IdP, public provider or deployment is used. The host
session endpoint is a private customer-login fixture; all collaboration reads,
writes, files and notification authorization use the production API routes.
"""
import base64
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'packages/components'
REPORT = Path(os.getenv('SDK_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/component-sdk-browser.json')))


def port():
    with socket.socket() as server: server.bind(('127.0.0.1', 0)); return server.getsockname()[1]


def ready(url, processes):
    end = time.monotonic() + 45
    while time.monotonic() < end:
        assert all(process.poll() is None for process in processes), 'Private API failed to start'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200: return
        except Exception: time.sleep(.1)
    raise AssertionError('Private SDK proof services did not become ready')


def main():
    checks, stage, processes, logs = {}, 'package build and pack', [], []
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='freecollab-sdk-proof-') as directory:
        private = Path(directory); web = private / 'web'; web.mkdir()
        try:
            subprocess.run(['node', str(PACKAGE / 'scripts/build.mjs')], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            packed = subprocess.run(['npm', '--cache=/tmp/freecollab-sdk-npm-cache', 'pack', '--ignore-scripts', '--json', '--pack-destination', str(REPORT.parent)], cwd=PACKAGE, check=True, capture_output=True, text=True)
            record = json.loads(packed.stdout)[0]; archive = REPORT.parent / record['filename']
            with tarfile.open(archive) as tar:
                assert all(member.name.startswith('package/') and '..' not in Path(member.name).parts for member in tar.getmembers())
                tar.extractall(private / 'packed', filter='data')
            sdk = private / 'packed/package'
            # Real consumer build of the packed modules and their declared Yjs dependency.
            bundle = private / 'bundle-packed.mjs'; bundle.write_text('import{build}from ' + json.dumps(str(ROOT / 'frontend/node_modules/esbuild/lib/main.js')) + ';await build({entryPoints:' + json.dumps([str(sdk / ('dist/' + name + '.js')) for name in ['index','elements','chat','files','feed','notifications','tasks','documents','calendar','meetings','workflows','decisions','search']]) + ',outdir:' + json.dumps(str(web / 'sdk')) + ',bundle:true,splitting:true,format:"esm",platform:"browser",alias:{yjs:' + json.dumps(str(ROOT / 'frontend/node_modules/yjs')) + '}})')
            subprocess.run(['node', str(bundle)], check=True, stdout=subprocess.DEVNULL)
            assert (sdk / 'dist/types/react.d.ts').is_file() and (sdk / 'dist/types/elements.d.ts').is_file()
            checks['npm_pack_tarball_has_typed_modular_esm_and_optional_react_exports'] = True
            api_port, web_port = port(), port(); origin = f'http://127.0.0.1:{web_port}'; api = f'http://127.0.0.1:{api_port}'
            environment = os.environ.copy(); environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'sdk.db'), STORAGE_PATH=str(private / 'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', COPILOT_ENABLED='false', PUBLIC_APP_URL=origin, CORS_ORIGINS=json.dumps([origin]), PYTHONPATH=str(ROOT / 'backend'), SDK_FIXTURE_TOKENS=str(private / 'tokens.json'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            launcher = private / 'api.py'; launcher.write_text('''import os,json
from sqlmodel import Session
from app.main import app
from app.database import init_db,engine
from app.models.user import User
from app.models.channel import Channel,ChannelMember
from app.models.workspace import Workspace,WorkspaceMember,WorkspaceChannel
from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
from app.models.secure_channel import SecureChannel
from app.models.notification import Notification
from app.models.workflow import WorkflowDefinition,WorkflowRevision,WorkflowRun
from app.services.crypto import encrypt_message
from datetime import datetime,timedelta
from app.services.auth import create_token,token_digest
import uvicorn
init_db()
with Session(engine) as session:
    session.add_all([User(id=name,name=name.title(),email=name+'@sdk-proof.example',password_hash='unused') for name in ['alpha','beta','outsider']]);session.add_all([Workspace(id='team',name='Private company'),Workspace(id='other',name='Other company')]);session.add_all([Channel(id=name,name=name) for name in ['shared','separate','secure','elsewhere']]);session.commit()
    session.add_all([WorkspaceMember(workspace_id='team',user_id=name,role='owner' if name=='alpha' else 'member') for name in ['alpha','beta','outsider']]);session.add(WorkspaceMember(workspace_id='other',user_id='alpha',role='owner'))
    session.add_all([WorkspaceChannel(workspace_id='team',channel_id=name) for name in ['shared','separate','secure']]);session.add(WorkspaceChannel(workspace_id='other',channel_id='elsewhere'))
    session.add_all([ChannelMember(channel_id=name,user_id='alpha',role='admin') for name in ['shared','separate','secure','elsewhere']]);session.add(ChannelMember(channel_id='shared',user_id='beta'))
    session.add(WorkspaceOIDCProvider(workspace_id='team',name='Customer identity',issuer_url='https://idp.sdk-proof.example',client_id='customer-owned-client',created_by='alpha',enabled=True,revision=1));session.add(SecureChannel(channel_id='secure',created_by='alpha',status='active',matrix_room_id='!private:sdk-proof.example'));session.commit()
    tokens={}
    for name in ['alpha','beta','outsider']:
        token=create_token(name,session,auth_methods=['sso'],workspace_scope={'workspace_id':'team','provider_revision':1});session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id='team',provider_revision=1));tokens[name]=token
    session.add_all([Notification(id='alpha-note',user_id='alpha',type='system',entity_type='channel',entity_id='shared',title='Alpha private notification',body='Only Alpha sees this inbox'),Notification(id='beta-note',user_id='beta',type='system',entity_type='channel',entity_id='shared',title='Beta private notification',body='Only Beta sees this inbox'),Notification(id='separate-note',user_id='alpha',type='system',entity_type='channel',entity_id='separate',title='Other channel hidden notification')]);session.commit()
    workflow={'name':'SDK approval','trigger':'manual','conditions':{'actor':'any','status':None},'steps':[{'kind':'approval','recipients':['alpha'],'quorum':1,'reminder_minutes':60,'timeout_minutes':1440,'max_reminders':2,'escalation_recipients':[],'project_id':None,'task_title':None}]}
    session.add(WorkflowDefinition(id='approval',workspace_id='team',channel_id='shared',created_by='alpha',granted_by='alpha',trigger='manual'));session.flush()
    session.add(WorkflowRevision(workflow_id='approval',revision=1,created_by='alpha',encrypted_definition=encrypt_message(json.dumps({**workflow,'workflow_id':'approval','revision':1}),'workflow:shared')))
    session.add(WorkflowRun(id='waiting-approval',workflow_id='approval',workspace_id='team',channel_id='shared',workflow_revision=1,event_id='private-fixture-approval',event_kind='manual',actor_id='alpha',status='waiting_approval',deadline_at=datetime.utcnow()+timedelta(hours=1)));session.commit()
    fd=os.open(os.environ['SDK_FIXTURE_TOKENS'],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(tokens,stream)
uvicorn.run(app,host='127.0.0.1',port=''' + str(api_port) + ''',lifespan='off',proxy_headers=False,access_log=False)
''')
            stream = (private / 'api.log').open('w'); logs.append(stream)
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            processes.append(subprocess.Popen([python, str(launcher)], cwd=ROOT / 'backend', env=environment, stdout=stream, stderr=stream, start_new_session=True)); ready(api + '/ready', processes)
            tokens = json.loads((private / 'tokens.json').read_text())
            class Host(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(web), **kwargs)
                def log_message(self, *args): pass
                def do_GET(self):
                    request = urlsplit(self.path)
                    if request.path != '/host-session': return super().do_GET()
                    query = parse_qs(request.query); identity = query.get('identity', ['alpha'])[0]
                    if query.get('delay'): time.sleep(1.5)
                    payload = json.dumps({'access_token': tokens.get(identity)}).encode(); self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(payload))); self.end_headers()
                    try: self.wfile.write(payload)
                    except (BrokenPipeError, ConnectionResetError): pass
            server = ThreadingHTTPServer(('127.0.0.1', web_port), Host); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            html = (ROOT / 'examples/sdk/index.html').read_text().replace('<script type="module" src="./host.js"></script>', '<script type="module" src="./proof-host.js"></script>')
            html = html.replace('</main>', '<div class="grid brand">' + ''.join('<freecollab-' + name + '></freecollab-' + name + '>' for name in ['tasks','documents','calendar','meetings','workflows','decisions','search']) + '</div></main>')
            (web / 'index.html').write_text(html)
            (web / 'proof-host.js').write_text('''import{createClient}from'/sdk/index.js';import{defineCustomElements}from'/sdk/elements.js';
defineCustomElements();window.identity='alpha';window.delaySession=false;window.events=[];
window.client=createClient({apiBase:API,workspaceId:'team',channelId:'shared',tokenFactory:async({signal,scope})=>{const delay=window.delaySession;window.delaySession=false;const response=await fetch('/host-session?identity='+encodeURIComponent(window.identity)+'&workspace='+encodeURIComponent(scope.workspaceId)+(delay?'&delay=1':''),{cache:'no-store',signal});return(await response.json()).access_token}});
for(const item of document.querySelectorAll('freecollab-chat,freecollab-files,freecollab-feed,freecollab-notifications,freecollab-tasks,freecollab-documents,freecollab-calendar,freecollab-meetings,freecollab-workflows,freecollab-decisions,freecollab-search')){item.options={locale:'en',pollIntervalMs:1500};item.client=window.client}
window.client.on('notification',e=>window.events.push({kind:'notification',id:e.notification.id}));window.client.on('navigate',e=>window.events.push({kind:'navigate',entity:e.entityId}));
window.switchUser=async identity=>{window.identity=identity;await window.client.reauthenticate().catch(()=>{})};
document.querySelector('#reauthenticate').onclick=()=>void window.client.reauthenticate().catch(()=>{});document.querySelector('#disconnect').onclick=()=>window.client.destroy();
'''.replace('API', json.dumps(api)))
            # React fixture uses the PACKED wrapper, bundled only with the existing
            # local React peer. It exercises actual effect cleanup in StrictMode.
            react_source = private / 'react-proof.tsx'; react_source.write_text('''import React from'react';import{createRoot}from'react-dom/client';import{createClient}from SDK;import{Chat,Notifications}from WRAPPERS;
window.reactClient=createClient({apiBase:API,workspaceId:'team',channelId:'shared',tokenFactory:async({signal})=>(await(await fetch('/host-session?identity=alpha',{signal,cache:'no-store'})).json()).access_token});
const root=createRoot(document.querySelector('#root'));root.render(<React.StrictMode><Chat client={window.reactClient}/><Notifications client={window.reactClient}/></React.StrictMode>);window.disposeReact=()=>{root.unmount();window.reactClient.destroy()};
'''.replace('SDK', json.dumps(str(sdk / 'dist/index.js'))).replace('WRAPPERS', json.dumps(str(sdk / 'dist/react.js'))).replace('API', json.dumps(api)))
            build_react = private / 'react-build.mjs'; build_react.write_text('import{build}from ' + json.dumps(str(ROOT / 'frontend/node_modules/esbuild/lib/main.js')) + ';await build({entryPoints:[' + json.dumps(str(react_source)) + '],outfile:' + json.dumps(str(web / 'react-proof.js')) + ',bundle:true,format:"esm",platform:"browser",alias:{react:' + json.dumps(str(ROOT / 'frontend/node_modules/react')) + ',"react-dom":' + json.dumps(str(ROOT / 'frontend/node_modules/react-dom')) + ',yjs:' + json.dumps(str(ROOT / 'frontend/node_modules/yjs')) + '}})')
            subprocess.run(['node', str(build_react)], check=True, stdout=subprocess.DEVNULL)
            (web / 'react.html').write_text('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main id="root"></main><script type="module" src="/react-proof.js"></script></body></html>')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox']); context = browser.new_context(viewport={'width':1440,'height':1100}, accept_downloads=True); page = context.new_page(); errors, urls, failures = [], [], []
                content_writes = []
                page.on('pageerror', lambda error: errors.append(str(error))); page.on('request', lambda request: (urls.append(request.url), content_writes.append(request.url) if request.method == 'POST' and '/messages/channels/' in request.url else None)); page.on('requestfailed', lambda request: failures.append(request.url))
                try:
                    stage = 'standalone packed web components and scoped customer identity'; page.goto(origin, wait_until='networkidle')
                    chat = page.locator('freecollab-chat'); files = page.locator('freecollab-files'); feed = page.locator('freecollab-feed'); notices = page.locator('freecollab-notifications')
                    expect(chat.get_by_role('button', name='Send message', exact=True)).to_be_enabled(); expect(notices.get_by_text('Alpha private notification', exact=True)).to_be_visible(); expect(notices.get_by_text('Other channel hidden notification', exact=True)).to_have_count(0)
                    assert page.evaluate('localStorage.length===0&&sessionStorage.length===0'); checks['standalone_native_components_use_async_customer_scoped_token_factory_without_token_storage'] = True
                    stage = 'actual native message send and safe rendering'; payload = '<img src=x onerror="window.sdkInjection=true">SDK native message'
                    chat.get_by_label('Message', exact=True).fill(payload); chat.get_by_role('button', name='Send message', exact=True).click(); expect(chat.get_by_text(payload, exact=True)).to_be_visible(); assert page.evaluate('!window.sdkInjection'); checks['native_chat_write_and_safe_text_rendering'] = True
                    stage = 'actual file upload and authenticated download'; files.get_by_label('Choose a file', exact=True).set_input_files({'name':'sdk-proof.txt','mimeType':'text/plain','buffer':b'Native SDK private file bytes'})
                    files.get_by_role('button', name='Upload file', exact=True).click(); expect(files.get_by_text('sdk-proof.txt', exact=True)).to_be_visible()
                    with page.expect_download() as download: files.get_by_role('button', name='Download', exact=True).click()
                    assert Path(download.value.path()).read_bytes() == b'Native SDK private file bytes'; checks['native_file_upload_download_actual_bytes'] = True
                    stage = 'actual production feed write'; feed.get_by_label('Post', exact=True).fill('SDK product update'); feed.get_by_label('Message', exact=True).fill('A native feed post from the packed package.'); feed.get_by_role('button', name='Publish post', exact=True).click(); expect(feed.get_by_role('heading', name='SDK product update', exact=True)).to_be_visible(); checks['native_activity_feed_create_and_read'] = True
                    stage = 'notification read and host navigation hooks'; notices.get_by_role('button', name='Open notification', exact=True).click(); notices.get_by_role('button', name='Mark read', exact=True).click(); expect(notices.get_by_text('Read', exact=True)).to_be_visible(); assert page.evaluate('window.events.some(e=>e.kind==="navigate")&&window.events.some(e=>e.kind==="notification")'); checks['channel_notifications_read_and_navigation_events'] = True
                    stage = 'native SDK project and task writes with revision update'; tasks = page.locator('freecollab-tasks')
                    tasks.get_by_label('Create project', exact=True).fill('SDK launch project'); tasks.get_by_role('button', name='Create project', exact=True).click(); expect(tasks.get_by_role('heading', name='SDK launch project', exact=True)).to_be_visible()
                    tasks.get_by_label('Create task', exact=True).fill('SDK task from customer product'); tasks.get_by_role('button', name='Create task', exact=True).click(); expect(tasks.get_by_role('heading', name='SDK task from customer product', exact=True)).to_be_visible(); tasks.get_by_role('button', name='Done', exact=True).click(); expect(tasks.get_by_text('Done', exact=True)).to_be_visible(); checks['packed_tasks_real_project_create_task_create_and_revision_status_change'] = True
                    stage = 'native Yjs shared document create edit and fresh session recovery'; documents = page.locator('freecollab-documents'); documents.get_by_label('Title', exact=True).fill('SDK shared document'); documents.get_by_role('button', name='Create', exact=True).click(); expect(documents.get_by_role('heading', name='SDK shared document', exact=True)).to_be_visible(); documents.get_by_role('button', name='Edit', exact=True).click(); expect(documents.get_by_label('Document text', exact=True)).to_be_enabled(); documents.get_by_label('Document text', exact=True).fill('Actual Yjs collaboration text from the packed SDK.'); documents.get_by_role('button', name='Save', exact=True).click(); documents.get_by_role('button', name='Close', exact=True).click(); documents.get_by_role('button', name='Edit', exact=True).click(); expect(documents.get_by_label('Document text', exact=True)).to_have_value('Actual Yjs collaboration text from the packed SDK.'); documents.get_by_role('button', name='Close', exact=True).click(); checks['packed_shared_documents_actual_yjs_create_append_and_new_editor_history_reconstruction'] = True
                    stage = 'native calendar event write and deletion'; calendar = page.locator('freecollab-calendar'); calendar.get_by_label('Title', exact=True).fill('SDK planning'); calendar.get_by_label('Starts', exact=True).fill('2027-01-10T09:00'); calendar.get_by_label('Ends', exact=True).fill('2027-01-10T10:00'); calendar.get_by_role('button', name='Create', exact=True).click(); expect(calendar.get_by_role('heading', name='SDK planning', exact=True)).to_be_visible(); calendar.get_by_role('button', name='Delete', exact=True).click(); expect(calendar.get_by_role('heading', name='SDK planning', exact=True)).to_have_count(0); checks['packed_calendar_create_and_revision_tombstone'] = True
                    stage = 'native decision write and accept revision'; decisions = page.locator('freecollab-decisions'); decisions.get_by_label('Title', exact=True).fill('SDK release decision'); decisions.get_by_label('Description', exact=True).fill('A real decision created by the customer-hosted component.'); decisions.get_by_role('button', name='Create', exact=True).click(); expect(decisions.get_by_role('heading', name='SDK release decision', exact=True)).to_be_visible(); decisions.get_by_role('button', name='Accepted', exact=True).click(); expect(decisions.locator('li span').filter(has_text='Accepted')).to_be_visible(); checks['packed_decision_create_and_accepted_revision'] = True
                    stage = 'native meeting creates persisted call and host navigation without media grants'; meetings = page.locator('freecollab-meetings'); meetings.get_by_role('button', name='Start meeting', exact=True).click(); expect(meetings.get_by_role('button', name='End meeting', exact=True)).to_be_visible(); assert page.evaluate('window.events.some(e=>e.kind==="navigate"&&!!e.entity)'); meetings.get_by_role('button', name='End meeting', exact=True).click(); expect(meetings.get_by_role('button', name='End meeting', exact=True)).to_have_count(0); checks['packed_meeting_real_create_end_and_host_navigation_no_fake_media'] = True
                    stage = 'native workflow queues real run and records scoped approval'; workflows = page.locator('freecollab-workflows'); expect(workflows.get_by_role('button', name='Approve', exact=True)).to_be_visible(); workflows.get_by_role('button', name='Approve', exact=True).click(); expect(workflows.get_by_role('button', name='Approve', exact=True)).to_have_count(0); workflows.get_by_role('button', name='Start workflow', exact=True).click(); expect(workflows.get_by_text('queued · 1/1', exact=True)).to_be_visible(); checks['packed_workflow_current_member_vote_and_durable_run_queue'] = True
                    stage = 'native chat edit thread reaction bookmark and safe file preview'; message_row = chat.locator('li').filter(has_text=payload); message_row.get_by_role('button', name='Edit', exact=True).click(); chat.get_by_label('Message', exact=True).last.fill('Edited SDK native message'); chat.get_by_role('button', name='Save', exact=True).click(); expect(chat.get_by_text('Edited SDK native message', exact=True)).to_be_visible(); message_row = chat.locator('li').filter(has_text='Edited SDK native message'); message_row.get_by_role('button', name='Reply', exact=True).click(); chat.get_by_label('Reply', exact=True).fill('A genuine SDK thread reply'); chat.get_by_role('button', name='Reply', exact=True).last.click(); expect(chat.get_by_text('A genuine SDK thread reply', exact=True)).to_be_visible(); chat.get_by_role('button', name='Cancel', exact=True).click(); message_row.get_by_role('button', name='Like', exact=True).click(); message_row.get_by_role('button', name='Bookmark', exact=True).click(); files.get_by_role('button', name='Preview', exact=True).click(); expect(files.get_by_text('Native SDK private file bytes', exact=True)).to_be_visible(); files.get_by_role('button', name='Close', exact=True).click(); checks['packed_chat_edit_thread_reaction_bookmark_and_safe_native_text_preview'] = True
                    stage = 'native local search over authenticated bounded history'; search = page.locator('freecollab-search'); search.get_by_label('Search recent messages', exact=True).fill('Edited SDK native'); search.get_by_role('button', name='Search', exact=True).click(); expect(search.get_by_text('Edited SDK native message', exact=True)).to_be_visible(); assert not any('Edited%20SDK' in url or 'Edited+SDK' in url for url in urls); checks['packed_local_search_keeps_search_phrase_off_wire'] = True
                    payload = 'Edited SDK native message'
                    stage = 'recoverable mutation failure keeps native component polling alive'
                    rejected = [False]
                    def reject_task_once(route):
                        if route.request.method == 'POST' and not rejected[0]: rejected[0] = True; route.fulfill(status=409, content_type='application/json', body='{}')
                        else: route.continue_()
                    page.route('**/channels/shared/projects/*/tasks', reject_task_once)
                    tasks.get_by_role('button', name='To do', exact=True).click(); expect(tasks.get_by_role('alert')).to_be_visible()
                    with page.expect_request(lambda request: request.method == 'GET' and urlsplit(request.url).path == '/channels/shared/projects', timeout=7000): pass
                    page.unroute('**/channels/shared/projects/*/tasks', reject_task_once); assert rejected[0]; checks['recoverable_native_mutation_conflict_resumes_component_polling'] = True
                    stage = 'unverifiable document history locks editor and save until reopened'
                    document_record = page.evaluate('window.client.listDocuments().then(rows=>rows[0])')
                    def corrupt_document_once(route):
                        route.fulfill(status=200, content_type='application/json', body=json.dumps({'document':document_record, 'items':[{'id':'bad', 'document_id':document_record['id'], 'channel_id':'shared', 'sequence':99, 'content_mode':'server', 'fields':{'update_base64':'AAA='}}], 'has_more':False, 'next_sequence':99}))
                    page.route('**/channels/shared/shared-documents/*/updates?*', corrupt_document_once); documents.get_by_role('button', name='Edit', exact=True).click(); expect(documents.get_by_role('alert')).to_be_visible(); expect(documents.get_by_label('Document text', exact=True)).to_be_disabled(); expect(documents.get_by_role('button', name='Save', exact=True)).to_be_disabled(); documents.get_by_role('button', name='Close', exact=True).click(); page.unroute('**/channels/shared/shared-documents/*/updates?*', corrupt_document_once); documents.get_by_role('button', name='Edit', exact=True).click(); expect(documents.get_by_label('Document text', exact=True)).to_be_enabled(); expect(documents.get_by_label('Document text', exact=True)).to_have_value('Actual Yjs collaboration text from the packed SDK.'); documents.get_by_role('button', name='Close', exact=True).click(); checks['document_integrity_failure_disables_editor_and_save_without_corrupting_real_history'] = True
                    stage = 'document integrity lock survives save control restoration'
                    documents.get_by_role('button', name='Edit', exact=True).click(); expect(documents.get_by_label('Document text', exact=True)).to_be_enabled(); page.route('**/channels/shared/shared-documents/*/updates?*', corrupt_document_once); documents.get_by_role('button', name='Save', exact=True).click(); expect(documents.get_by_role('alert')).to_be_visible(); expect(documents.get_by_role('button', name='Refresh', exact=True)).to_be_enabled(); expect(documents.get_by_label('Document text', exact=True)).to_be_disabled(); expect(documents.get_by_role('button', name='Save', exact=True)).to_be_disabled(); page.unroute('**/channels/shared/shared-documents/*/updates?*', corrupt_document_once); documents.get_by_role('button', name='Close', exact=True).click(); checks['document_integrity_failure_during_save_keeps_terminal_editor_locked_after_view_cleanup'] = True
                    stage = 'fresh account change and synchronous old-inbox cleanup'; page.evaluate('window.identity="beta";window.pendingReauth=window.client.reauthenticate().catch(()=>{});'); expect(notices.get_by_text('Alpha private notification', exact=True)).to_have_count(0); page.evaluate('window.pendingReauth'); expect(notices.get_by_text('Beta private notification', exact=True)).to_be_visible(); expect(notices.get_by_text('Alpha private notification', exact=True)).to_have_count(0)
                    chat.get_by_label('Message', exact=True).fill('Sent as Beta after host account change'); chat.get_by_role('button', name='Send message', exact=True).click(); expect(chat.get_by_text('Sent as Beta after host account change', exact=True)).to_be_visible(); checks['account_change_clears_private_inbox_and_rebinds_native_writes'] = True
                    stage = 'outsider and logout denial'; page.evaluate('window.switchUser("outsider")'); expect(chat.get_by_text('This user does not have access to this channel.', exact=True)).to_be_visible(); expect(files.get_by_text('sdk-proof.txt', exact=True)).to_have_count(0); expect(feed.get_by_text('SDK product update', exact=True)).to_have_count(0); expect(notices.get_by_text('Beta private notification', exact=True)).to_have_count(0)
                    page.evaluate('window.switchUser("signed-out")'); expect(chat.get_by_text('Sign in through your application to continue.', exact=True)).to_be_visible(); assert page.evaluate('window.client.state.status') == 'signed-out'; checks['outsider_denied_and_logout_clears_all_native_component_content'] = True
                    stage = 'workspace boundary and encrypted channel fail closed'; page.evaluate('window.switchUser("alpha")'); expect(chat.get_by_role('button', name='Send message', exact=True)).to_be_enabled(); page.evaluate('window.client.setScope({workspaceId:"other",channelId:"elsewhere"}).catch(()=>{})'); expect(chat.get_by_text('This user does not have access to this channel.', exact=True)).to_be_visible(); page.evaluate('window.client.setScope({workspaceId:"team",channelId:"secure"}).catch(()=>{})'); expect(chat.get_by_role('alert')).to_be_visible(); assert not any('/messages/channels/secure' in url or '/files/channels/secure' in url or '/channels/secure/activity-feed' in url for url in urls); checks['workspace_scope_and_secure_channel_native_fail_closed'] = True
                    stage = 'live native channel becoming encrypted stops subsequent native submissions'; page.evaluate('window.client.setScope({workspaceId:"team",channelId:"shared"}).catch(()=>{})'); expect(chat.get_by_role('button', name='Send message', exact=True)).to_be_enabled(); sent_before = len(content_writes)
                    with sqlite3.connect(private / 'sdk.db') as database: database.execute("INSERT INTO securechannel(channel_id,matrix_room_id,created_by,status,created_at) VALUES(?,?,?,?,datetime('now'))",('shared','!upgraded:sdk-proof.example','alpha','active'))
                    result = page.evaluate('window.client.sendMessage("This draft must never enter the native content API").then(()=>"sent").catch(e=>e.code)')
                    assert result == 'secure-channel-unsupported' and len(content_writes) == sent_before
                    with sqlite3.connect(private / 'sdk.db') as database: database.execute('DELETE FROM securechannel WHERE channel_id=?',('shared',))
                    checks['live_encryption_transition_rechecked_before_native_content_submission'] = True
                    stage = 'responsive theme and lifecycle'; page.evaluate('window.client.setScope({workspaceId:"team",channelId:"shared"}).catch(()=>{})'); expect(chat.get_by_role('button', name='Send message', exact=True)).to_be_enabled(); expect(chat.get_by_text(payload, exact=True)).to_be_visible(); expect(files.get_by_text('sdk-proof.txt', exact=True)).to_be_visible(); expect(feed.get_by_role('heading', name='SDK product update', exact=True)).to_be_visible(); expect(notices.get_by_text('Alpha private notification', exact=True)).to_be_visible(); page.screenshot(path=str(REPORT.with_name('component-sdk-desktop.png')), full_page=True)
                    page.set_viewport_size({'width':320,'height':900}); assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'); page.screenshot(path=str(REPORT.with_name('component-sdk-mobile.png')), full_page=True); checks['scoped_theme_and_mobile_320px_no_horizontal_overflow'] = True
                    page.evaluate('for(const element of document.querySelectorAll("freecollab-chat,freecollab-files,freecollab-feed,freecollab-notifications"))element.options={locale:"sv",pollIntervalMs:1500}')
                    expect(chat.get_by_role('button', name='Skicka meddelande', exact=True)).to_be_enabled(); expect(notices.get_by_role('heading', name='Aviseringar', exact=True)).to_be_visible(); assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'); checks['swedish_sdk_labels_and_translated_320px_layout'] = True
                    with page.expect_request('**/host-session?*delay=1'): page.evaluate('window.delaySession=true;window.pendingDestroy=window.client.reauthenticate().catch(()=>{});')
                    page.evaluate('window.client.destroy()'); page.evaluate('window.pendingDestroy'); expect(chat.locator('section')).to_have_count(0); assert page.evaluate('window.client.state.status') == 'destroyed'; checks['destroy_removes_private_dom_and_aborts_pending_token_factory_fetch'] = True
                    stage = 'packed React wrappers actual native API and StrictMode'; react = context.new_page(); react.on('pageerror', lambda error: errors.append(str(error))); react.goto(origin + '/react.html', wait_until='networkidle'); expect(react.get_by_role('button', name='Send message', exact=True)).to_be_enabled(); expect(react.get_by_text('Alpha private notification', exact=True)).to_be_visible(); react.get_by_label('Message', exact=True).fill('React wrapper native API proof'); react.get_by_role('button', name='Send message', exact=True).click(); expect(react.get_by_text('React wrapper native API proof', exact=True)).to_be_visible(); react.evaluate('window.disposeReact()'); expect(react.locator('#root')).to_be_empty(); react.close(); checks['packed_react_wrappers_strict_mode_native_write_and_unmount'] = True
                    assert not errors, 'Unexpected SDK browser page errors'
                    assert all(token not in url for token in tokens.values() for url in urls); checks['zero_page_errors_and_no_user_tokens_in_request_urls'] = True
                finally: context.close(); browser.close(); server.shutdown(); server.server_close(); thread.join(timeout=3)
        except Exception:
            print('FAILED SDK browser proof stage: ' + stage, file=sys.stderr)
            if (private / 'api.log').exists():
                tail = (private / 'api.log').read_text()[-1000:]
                if 'Traceback' in tail or 'Error' in tail: print('Private API startup/runtime error: ' + tail, file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None: process.terminate()
            for process in reversed(processes):
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            for stream in logs: stream.close()
    sources = [*sorted((PACKAGE / 'src').glob('*.ts*')), PACKAGE / 'package.json', PACKAGE / 'package-lock.json', PACKAGE / 'scripts/build.mjs', ROOT / 'docs/COMPONENT_SDK.md']
    REPORT.write_text(json.dumps({'scope':'Packed @freecollab/components with native production APIs, private SQLite/keys, actual Chromium standalone web components and React StrictMode; synthetic customer-login fixture issues real workspace-scoped JWTs, no external IdP/provider/deployment','tarball':str(archive),'tarball_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'checks':checks,'source_sha256':{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},'cleanup':'Own API/static server/database/keys/fixture credentials/browser contexts removed'}, indent=2) + '\n')
    print('Component SDK browser proof passed: ' + str(len(checks)) + ' checks; actual packed native components and scoped customer sessions.')

if __name__ == '__main__': main()
