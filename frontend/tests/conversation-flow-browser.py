#!/usr/bin/env python3
"""Actual conversation-to-work continuity with isolated production services.

Production App/Chat/WorkHub run from an unedited private source snapshot. A fresh
SQLite database supplies two accounts, two workspaces and independent native
channels. The optional document gate holds the real update request before its
production handler; no API response, document state or navigation state is mocked.
"""
from ui_navigation import choose_chat_channel, close_channels, activate_chat_feature, capture_work_card, close_work_card, work_surface, work_resume_button
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
REPORT = Path(os.getenv('CONVERSATION_FLOW_REPORT', str(ROOT.parent / '.freecollab/proof-results/conversation-flow-browser.json')))
WORK = re.compile(r'^(Work in this conversation|Arbete i det här samtalet)$')
DIALOG = WORK
RETURN = re.compile(r'^(Back to conversation|Tillbaka till samtalet)$')


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def ready(url, processes):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'Private conversation-flow service failed to start'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(.1)
    raise AssertionError('Private conversation-flow readiness timed out')


def native(base, token, path, data=None, method='GET'):
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    with urlopen(Request(base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers, method=method), timeout=10) as response:
        return json.load(response)


def profile_png(path, color):
    """A real private RGB image, deliberately non-square for the actual UI crop."""
    width, height = 320, 192
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    pixels = b''.join(b'\x00' + bytes(color) * width for _ in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'tEXt', b'Description\x00Private browser profile fixture') + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def native_photo(base, token):
    with urlopen(Request(base + '/users/alpha/avatar?workspace_id=team', headers={'Authorization': 'Bearer ' + token}), timeout=10) as response:
        assert response.headers.get_content_type() == 'image/png'
        photo = response.read()
    assert struct.unpack('>II', photo[16:24]) == (256, 256), 'The real account picker did not crop to its square product format'
    return hashlib.sha256(photo).hexdigest()


def same_photo(page, image, expected):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if image.count() == 1:
            actual = image.evaluate('async(image)=>{if(!image.complete||!image.naturalWidth||!image.src.startsWith("blob:"))return null;const bytes=await(await fetch(image.src)).arrayBuffer();const digest=await crypto.subtle.digest("SHA-256",bytes);return Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,"0")).join("")}')
            if actual == expected:
                return
        page.wait_for_timeout(50)
    raise AssertionError('A product portrait did not display the authenticated native photo bytes')


def work(page):
    close_channels(page)
    button = page.get_by_role('button', name=WORK)
    button.evaluate('(node)=>{window.flowExpectedReturn=node}')
    button.click()
    dialog = capture_work_card(page).get_by_role('region', name=DIALOG)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role('tablist')).to_have_count(0)
    expect(dialog.get_by_role('combobox', name='Workspace tool', exact=True)).to_have_count(0)
    assert not dialog.evaluate('(node)=>node.closest("[inert]")||node.getAttribute("aria-modal")==="true"')
    assert page.locator('.fc-chat-composer textarea').is_enabled()
    return dialog, button


def selected(dialog, value):
    expect(dialog).to_have_attribute('data-work-kind', value)
    expect(dialog.get_by_role('tablist')).to_have_count(0)


def select(dialog, value):
    """Observe both clean catalogs, then explicitly close the old fixture view."""
    page = dialog.page
    original = dialog.element_handle()
    original_card = capture_work_card(page, dialog)
    feature_id = {'tasks': 'task', 'documents': 'document', 'meetings': 'meetings', 'feed': 'activity', 'workflows': 'workflows', 'cloud': 'cloud'}[value]
    target = activate_chat_feature(page, feature_id)
    target.evaluate('(node)=>{window.flowExpectedReturn=node}')
    sibling = capture_work_card(page).get_by_role('region', name=DIALOG)
    selected(sibling, value)
    assert original_card.get_attribute('data-collaboration-work-id') != capture_work_card(page, sibling).get_attribute('data-collaboration-work-id')
    assert dialog.evaluate('(node,old)=>node===old&&old.isConnected', original), 'A new feature replaced the previous live work card'
    close_work_card(page, dialog)
    expect(sibling).to_be_visible()
    work_surface(page, sibling).focus()
    return sibling


def open_sibling(page, feature_id):
    """A sibling opens normally while existing dirty or busy work stays mounted."""
    activate_chat_feature(page, feature_id)
    expect(page.locator('.fc-slash-commands')).to_have_count(0)
    sibling = capture_work_card(page).get_by_role('region', name=DIALOG)
    expect(sibling).to_be_visible()
    return sibling


def leave(page, dialog, button=None):
    dialog.get_by_role('button', name=re.compile(r'^(Close this work|Stäng arbetet)$'), exact=True).click()
    expect(dialog).to_have_count(0)
    if button is not None:
        # Closing unmounts the editor before the next-frame focus restoration.
        # Keep the same exact connected, active and non-inert return target.
        page.wait_for_function('window.flowExpectedReturn?.isConnected&&document.activeElement===window.flowExpectedReturn&&!window.flowExpectedReturn.closest("[inert]")', timeout=5000)


def switch_workspace(page, name):
    close_channels(page)
    page.locator('.fc-workspace-switch-trigger').click()
    page.get_by_role('menuitemradio', name=name, exact=True).click()


def main():
    checks, stage, processes, streams = {}, 'private production snapshot', [], []
    source_hashes, private_path = {}, None
    with tempfile.TemporaryDirectory(prefix='freecollab-conversation-flow-') as directory:
        private = Path(directory)
        private_path = str(private)
        web = private / 'web'
        web.mkdir()
        source = web / 'src'
        shutil.copytree(ROOT / 'frontend/src', source)
        shutil.copytree(ROOT / 'frontend/public', web / 'public')
        for name in ('package.json', 'postcss.config.js', 'tailwind.config.js'):
            shutil.copyfile(ROOT / 'frontend' / name, web / name)
        (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
        relevant = ['components/Chat.tsx', 'components/WorkHub.tsx', 'account/AccountProvider.tsx', 'i18n/index.tsx', 'i18n/core.ts', 'i18n/demo-natural-flow.sv.ts', 'lib/api.ts', 'lib/useModalFocus.ts', 'App.tsx', 'account/AccountPanel.tsx', 'account/AccountPhoto.tsx', 'components/Avatar.tsx', 'components/ChannelTools.tsx', 'lib/avatar-api.ts', 'styles/avatar.css']
        relevant += [str(path.relative_to(source)) for path in source.glob('lib/*work*hub*')]
        relevant += [str(path.relative_to(source)) for path in source.glob('i18n/*work*hub*')]
        relevant += ['components/SharedDocumentsPanel.tsx', 'components/SharedDocumentOrganization.tsx', 'components/ProjectTasksPanel.tsx', 'components/CollaborationPanel.tsx', 'lib/conversation-navigation.ts', 'lib/conversation-work-api.ts', 'lib/shared-documents-api.ts', 'i18n/conversation-work.sv.ts', 'styles/conversation-work.css']
        relevant += [str(path.relative_to(source)) for path in source.rglob('*') if path.is_file()]
        for name in sorted(set(relevant)):
            source_hashes['frontend/src/' + name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
        source_hashes['frontend/tests/conversation-flow-browser.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        for name in ['backend/app/api/avatars.py', 'backend/app/services/avatars.py', 'backend/app/models/avatar.py']:
            source_hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for path in (ROOT / 'backend/app').rglob('*.py'):
            source_hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name in ['frontend/package.json', 'frontend/postcss.config.js', 'frontend/tailwind.config.js', 'frontend/tests/ui_navigation.py']:
            source_hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        demo_dependencies = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ('components/Demo.tsx', 'styles/product-demo-frame.css', 'lib/product-demo-url.ts') if (source / name).is_file()}
        api_port, app_port = port(), port()
        base, origin = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
        (private / 'controls.json').write_text('{}')
        try:
            environment = os.environ.copy()
            # This remains the normal installed product proof, separate from
            # the dedicated sample-data backend exercised by product-demo-browser.
            environment['DEMO_INSTANCE'] = 'false'
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'flow.db'), STORAGE_PATH=str(private / 'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', COPILOT_ENABLED='false', PUSH_ENABLED='false', PUBLIC_APP_URL=origin, CORS_ORIGINS=json.dumps([origin]), PYTHONPATH=str(ROOT / 'backend'), FLOW_FIXTURE_PRIVATE=str(private))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            launcher = private / 'api.py'
            launcher.write_text('''import asyncio,json,os
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
private=Path(os.environ['FLOW_FIXTURE_PRIVATE']);init_db()
with Session(engine) as session:
    session.add_all([User(id=name,name=label,email=name+'@conversation-flow.example',password_hash='unused')for name,label in [('alpha','Linnea Berg'),('beta','Oskar Lind')]])
    session.add_all([Workspace(id='team',name='Nord Studio'),Workspace(id='other',name='Research Studio')])
    session.add_all([Channel(id='shared',name='Courtyard'),Channel(id='design',name='Design'),Channel(id='research',name='Research')]);session.commit()
    session.add_all([WorkspaceMember(workspace_id=workspace,user_id=name,role='owner'if name=='alpha'else'member')for workspace in ['team','other']for name in ['alpha','beta']])
    session.add_all([WorkspaceChannel(workspace_id=workspace,channel_id=channel)for workspace,channel in [('team','shared'),('team','design'),('other','research')]])
    session.add_all([ChannelMember(channel_id=channel,user_id=name,role='admin'if name=='alpha'else'member')for channel in ['shared','design','research']for name in ['alpha','beta']])
    session.add_all([UserLanguage(user_id=name,locale='en')for name in ['alpha','beta']])
    for index in range(70):
        text='Courtyard discussion '+str(index)+'. Keep the shared materials close to the conversation. We will review the material samples and decide the next practical step together.'
        session.add(Message(id='flow-message-'+str(index),channel_id='shared',author_id='alpha'if index%2==0 else'beta',content=encrypt_message(text,'shared'),encrypted=True,created_at=datetime(2026,10,20,10)+timedelta(seconds=index)))
    session.commit()
    sessions={name:{'user_id':name,'access_token':create_token(name,session)}for name in ['alpha','beta']}
    sessions['alpha2']={'user_id':'alpha','access_token':create_token('alpha',session)}
    fd=os.open(private/'sessions.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w')as stream:json.dump(sessions,stream)
@app.middleware('http')
async def hold_actual_document_update(request:Request,call_next):
    try:config=json.loads((private/'controls.json').read_text())
    except (OSError,ValueError):config={}
    if config.get('hold_document')and request.method=='POST'and request.url.path.endswith('/updates')and'/shared-documents/'in request.url.path:
        (private/'update.captured').touch()
        for _ in range(600):
            if(private/'update.release').exists():break
            await asyncio.sleep(.05)
    return await call_next(request)
uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',lifespan='off',access_log=False,proxy_headers=False)
''')
            (web / 'index.html').write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>FreeCollab conversation flow</title></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
            (web / 'base.css').write_text('\n'.join(line for line in (source / 'index.css').read_text().splitlines() if not line.lstrip().startswith(('@import "tailwindcss"', '@config '))) + '\n.proof-flow-controls{padding:8px 20px;min-height:86px;background:var(--fc-surface)}.proof-flow-controls .fc-language{margin:0;max-width:600px}.fc-chat{height:calc(100dvh - 86px)}\n')
            (web / 'entry.tsx').write_text('''import React from'react';import{createRoot}from'react-dom/client';import{LocaleProvider,LanguagePicker}from'./src/i18n';import{AppearanceProvider}from'./src/appearance/AppearanceProvider';import{AccountProvider}from'./src/account/AccountProvider';import App from'./src/App';import{api}from'./src/lib/api';import'./base.css';
(window as any).switchFlowAccount=(id:string)=>{const session=(window as any).__flowSessions[id];if(!session){api.logout();return}api.applySession({...session,token_type:'bearer',public_key:'',encrypted_private_key:null})};
createRoot(document.querySelector('#root')!).render(<React.StrictMode><LocaleProvider><AppearanceProvider><AccountProvider><div className="proof-flow-controls"><LanguagePicker/></div><App/></AccountProvider></AppearanceProvider></LocaleProvider></React.StrictMode>);
''')
            aliases = {name: str(ROOT / 'frontend/node_modules' / name) for name in json.loads((ROOT / 'frontend/package.json').read_text())['dependencies']}
            vite = private / 'vite.mjs'
            vite.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) + ';const server=await createServer({configFile:false,root:' + json.dumps(str(web)) + ',plugins:[react()],resolve:{alias:' + json.dumps(aliases) + '},cacheDir:' + json.dumps(str(private / 'vite-cache')) + ',server:{host:"127.0.0.1",port:' + str(app_port) + ',strictPort:true,fs:{allow:[' + json.dumps(str(private)) + ',' + json.dumps(str(ROOT / 'frontend')) + ']},proxy:{"/api":{target:' + json.dumps(base) + ',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:' + json.dumps(base) + ',ws:true}}}});await server.listen();')
            for command, name, cwd in [([PYTHON, str(launcher)], 'api.log', ROOT / 'backend'), (['node', str(vite)], 'vite.log', ROOT / 'frontend')]:
                stream = (private / name).open('w')
                streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            ready(base + '/ready', processes)
            ready(origin, processes)
            try:
                with urlopen(Request(base + '/demo/session', data=b'{}', headers={'Content-Type': 'application/json'}, method='POST'), timeout=10):
                    raise AssertionError('The ordinary product exposed public sample-workspace bootstrap')
            except HTTPError as failure:
                assert failure.code == 404, failure.code
            checks['ordinary_installation_does_not_expose_public_demo_bootstrap'] = True
            sessions = json.loads((private / 'sessions.json').read_text())
            native(base, sessions['alpha']['access_token'], '/channels/shared/projects', {'id': 'flow-project', 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Courtyard next steps', 'description': 'Actual native work', 'color': 'sage'}}, 'POST')
            native(base, sessions['alpha']['access_token'], '/channels/shared/projects/flow-project/tasks', {'id': 'flow-task', 'expected_revision': 0, 'archived': False, 'fields': {'title': 'Review material samples', 'description': 'Existing task for exact-target navigation', 'status': 'todo', 'priority': 'normal', 'assignee_user_id': None, 'due_date': None, 'checklist': [], 'file_ids': [], 'decision_ids': []}}, 'POST')
            decision = native(base, sessions['alpha']['access_token'], '/channels/shared/decisions', {'title': 'Keep the courtyard open', 'content': 'Actual decision target', 'status': 'accepted'}, 'POST')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1050}, locale='en-US')
                context.add_init_script('window.__flowSessions=' + json.dumps(sessions) + ';for(const name of["alpha","beta"])if(!localStorage.getItem("freecollab.workspace:"+name))localStorage.setItem("freecollab.workspace:"+name,"team");if(!localStorage.getItem("freecollab_token")){localStorage.setItem("freecollab_token",window.__flowSessions.alpha.access_token);localStorage.setItem("freecollab_user_id","alpha")}')
                page = context.new_page()
                page.set_default_timeout(30000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    stage = 'conversation draft, reading position and return focus'
                    page.goto(origin, wait_until='domcontentloaded')
                    choose_chat_channel(page, 'Courtyard')
                    composer = page.locator('.fc-chat-composer textarea')
                    expect(composer).to_be_visible()
                    draft = 'Keep this unsent thought while I look at the materials. Åäö.'
                    composer.fill(draft)
                    composer.evaluate('(node)=>{window.flowComposer=node;node.setSelectionRange(3,11)}')
                    timeline = page.locator('.fc-chat-timeline')
                    expect(page.locator('.fc-chat-message')).to_have_count(70)
                    expect(page.locator('#message-flow-message-1')).to_be_in_viewport(ratio=.7)
                    assert timeline.evaluate('(node)=>node.scrollHeight>node.clientHeight+400')
                    timeline.evaluate('(node)=>{node.scrollTop=Math.min(300,node.scrollHeight-node.clientHeight);node.dispatchEvent(new Event("scroll"));window.flowTimeline=node;window.flowScroll=node.scrollTop}')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    page.locator('.fc-chat').evaluate('(node)=>{window.flowShell=node}')
                    dialog = select(dialog, 'documents')
                    assert page.locator('.fc-chat').evaluate('(node)=>node===window.flowShell'), 'Changing work remounted the conversation shell'
                    composer.focus()
                    expect(composer).to_be_focused()
                    assert not composer.evaluate('(node)=>!!node.closest("[inert]")'), 'Inline work disabled the conversation'
                    leave(page, dialog, button)
                    expect(composer).to_have_value(draft)
                    assert composer.evaluate('(node)=>node===window.flowComposer&&node.selectionStart===3&&node.selectionEnd===11')
                    assert timeline.evaluate('(node)=>node===window.flowTimeline&&Math.abs(node.scrollTop-window.flowScroll)<2')
                    checks['same_conversation_dom_draft_caret_reading_position_and_visible_return_focus'] = True

                    stage = 'same channel resumes and different channel owns its own tool view'
                    dialog, button = work(page)
                    selected(dialog, 'documents')
                    leave(page, dialog, button)
                    choose_chat_channel(page, 'Design')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    dialog = select(dialog, 'feed')
                    leave(page, dialog, button)
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'documents')
                    leave(page, dialog, button)
                    choose_chat_channel(page, 'Design')
                    dialog, button = work(page)
                    selected(dialog, 'feed')
                    leave(page, dialog, button)
                    checks['same_channel_resumes_last_view_and_channels_keep_distinct_views'] = True

                    stage = 'explicit resource navigation overrides a remembered generic tool'
                    channel = native(base, sessions['alpha']['access_token'], '/channels/design')
                    page.evaluate('(channel)=>window.dispatchEvent(new CustomEvent("freecollab:open-search-source",{detail:{channel_id:channel.id,kind:"document",id:"navigation-intent"}}))', channel)
                    dialog = capture_work_card(page).get_by_role('region', name=DIALOG)
                    selected(dialog, 'documents')
                    leave(page, dialog)
                    checks['explicit_production_resource_navigation_overrides_remembered_generic_view'] = True

                    stage = 'tool memory stays in this visit and is not restored after reload'
                    page.reload(wait_until='domcontentloaded')
                    choose_chat_channel(page, 'Design')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    dialog = select(dialog, 'documents')
                    leave(page, dialog, button)
                    checks['reload_starts_default_view_instead_of_persisting_tool_memory'] = True

                    stage = 'workspace switch clears previous workspace view memory'
                    switch_workspace(page, 'Research Studio')
                    choose_chat_channel(page, 'Research')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    dialog = select(dialog, 'feed')
                    leave(page, dialog, button)
                    switch_workspace(page, 'Nord Studio')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    dialog = select(dialog, 'documents')
                    leave(page, dialog, button)
                    checks['workspace_changes_drop_old_tool_memory'] = True

                    stage = 'credential rotation and another account cannot inherit a tool view'
                    page.evaluate('window.switchFlowAccount("alpha2")')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    dialog = select(dialog, 'documents')
                    leave(page, dialog, button)
                    page.evaluate('window.switchFlowAccount("beta")')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    dialog = select(dialog, 'feed')
                    leave(page, dialog, button)
                    page.evaluate('window.switchFlowAccount("alpha2")')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    checks['same_user_new_credential_and_account_roundtrip_start_without_old_memory'] = True

                    stage = 'actual unsynchronized document guards both return and tool changes'
                    dialog = select(dialog, 'documents')
                    dialog.get_by_role('button', name='New shared document', exact=True).click()
                    dialog.get_by_label('Document title', exact=True).fill('Material choices')
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/shared-documents')) as created:
                        dialog.get_by_role('button', name='Create document', exact=True).click()
                    document = created.value.json()
                    body = dialog.get_by_label('Document text', exact=True)
                    expect(body).to_be_editable()
                    (private / 'controls.json').write_text(json.dumps({'hold_document': True}))
                    body.fill('This real pending document edit must stay here until synchronized.')
                    expect(dialog.locator('.fc-shared-sync')).to_contain_text('waiting to sync')
                    deadline = time.monotonic() + 15
                    while not (private / 'update.captured').exists():
                        if time.monotonic() >= deadline:
                            raise AssertionError('The actual pending document update was not captured')
                        page.wait_for_timeout(25)
                    confirmations = []
                    def accept_unexpected(value):
                        confirmations.append(value.message)
                        value.accept()
                    page.on('dialog', accept_unexpected)
                    dialog.get_by_role('button', name=re.compile(r'^(Close this work|Stäng arbetet)$'), exact=True).click()
                    assert not confirmations, 'Outstanding Yjs work must block leaving before a discard confirmation'
                    expect(dialog).to_be_visible()
                    expect(body).to_have_value('This real pending document edit must stay here until synchronized.')
                    dialog.evaluate('(node)=>{window.flowGuardedWork=node}')
                    task_sibling = open_sibling(page, 'task')
                    selected(task_sibling, 'tasks')
                    assert not confirmations
                    selected(dialog, 'documents')
                    assert dialog.evaluate('(node)=>node===window.flowGuardedWork')
                    close_work_card(page, task_sibling)
                    assert not confirmations
                    choose_chat_channel(page, 'Design')
                    assert not confirmations
                    close_channels(page)
                    expect(page.get_by_role('textbox', name='Message #Courtyard', exact=True)).to_be_visible()
                    switch_workspace(page, 'Research Studio')
                    assert not confirmations
                    expect(page.locator('.fc-workspace-switch-trigger')).to_contain_text('Nord Studio')
                    page.evaluate('(id)=>window.dispatchEvent(new CustomEvent("freecollab:open-decision",{detail:{channel_id:"shared",decision_id:id}}))', decision['id'])
                    decision_target = page.locator('[id$="decision-' + decision['id'] + '"]')
                    expect(decision_target).to_be_visible()
                    decision_sibling = capture_work_card(page, decision_target)
                    expect(decision_sibling.locator('[id$="decision-' + decision['id'] + '"]')).to_be_visible()
                    assert decision_sibling.get_attribute('data-collaboration-work-id') != capture_work_card(page, dialog).get_attribute('data-collaboration-work-id')
                    assert not confirmations
                    close_work_card(page, decision_sibling)
                    assert not confirmations
                    selected(dialog, 'documents')
                    expect(body).to_have_value('This real pending document edit must stay here until synchronized.')
                    assert dialog.evaluate('(node)=>node===window.flowGuardedWork')
                    checks['pending_native_document_guards_true_navigation_but_retains_task_and_exact_decision_siblings'] = True

                    stage = 'conversation remains usable while a real document update is pending'
                    body.evaluate('(node)=>{window.flowPendingEditor=node}')
                    composer = page.locator('.fc-chat-composer textarea')
                    composer.fill('A real message while the document is still synchronizing.')
                    expect(composer).to_be_focused()
                    assert not composer.evaluate('(node)=>!!node.closest("[inert]")')
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/mobile/offline/channels/shared/messages')) as sent:
                        page.get_by_role('button', name='Send message', exact=True).click()
                    assert sent.value.ok
                    expect(page.locator('.fc-chat-message-content').filter(has_text=re.compile(r'^A real message while the document is still synchronizing\.$'))).to_be_visible()
                    expect(body).to_have_value('This real pending document edit must stay here until synchronized.')
                    assert body.evaluate('(node)=>node===window.flowPendingEditor'), 'Sending a message remounted the document editor'
                    composer.fill(draft)
                    composer.press('Escape')
                    expect(dialog).to_be_visible()
                    expect(composer).to_be_focused()
                    checks['real_message_send_and_composer_escape_preserve_pending_inline_document'] = True
                    page.remove_listener('dialog', accept_unexpected)
                    (private / 'update.release').touch()
                    expect(dialog.locator('.fc-shared-sync')).to_have_text('All edits synchronized', timeout=15000)
                    updates = native(base, sessions['alpha2']['access_token'], '/channels/shared/shared-documents/' + document['id'] + '/updates')
                    assert len(updates['items']) >= 2, 'Actual update did not reach the production server'
                    comment_draft = dialog.get_by_label('Add a comment', exact=True)
                    comment_draft.fill('Keep this unsent native document comment.')
                    draft_confirmations = []
                    def decline_document_draft(value):
                        draft_confirmations.append(value.message); value.dismiss()
                    page.on('dialog', decline_document_draft)
                    dialog.get_by_role('button', name=re.compile(r'^(Close this work|Stäng arbetet)$'), exact=True).click()
                    expect(comment_draft).to_have_value('Keep this unsent native document comment.')
                    comment_editor = comment_draft.element_handle()
                    task_sibling = open_sibling(page, 'task')
                    selected(task_sibling, 'tasks')
                    # Recall follows the last used catalog while older drafts stay live.
                    last_catalog_kind = task_sibling.get_attribute('data-work-kind')
                    assert len(draft_confirmations) == 1 and all('Changes have not saved' in value for value in draft_confirmations)
                    expect(comment_draft).to_have_value('Keep this unsent native document comment.')
                    assert comment_draft.evaluate('(node,old)=>node===old&&old.isConnected', comment_editor)
                    close_work_card(page, task_sibling)
                    assert len(draft_confirmations) == 1
                    page.remove_listener('dialog', decline_document_draft)
                    comment_draft.fill('')
                    leave(page, dialog)
                    dialog, button = work(page)
                    selected(dialog, last_catalog_kind)
                    leave(page, dialog, button)
                    checks['real_document_pending_update_and_comment_survive_siblings_with_own_close_guard_then_sync'] = True

                    stage = 'unsaved native task guards close work target channel and workspace'
                    dialog, button = work(page)
                    dialog = select(dialog, 'tasks')
                    dialog.get_by_role('button', name='New task', exact=True).click()
                    task_form = dialog.get_by_role('form', name='New task', exact=True)
                    task_form.get_by_label('Task title', exact=True).fill('Choose the courtyard material')
                    task_form.get_by_label('Description', exact=True).fill('Keep this actual native task draft through navigation attempts.')
                    task_form.evaluate('(node)=>{window.flowTaskEditor=node}')
                    task_confirmations = []
                    def decline_task(value):
                        task_confirmations.append(value.message)
                        value.dismiss()
                    page.on('dialog', decline_task)
                    dialog.get_by_role('button', name=re.compile(r'^(Close this work|Stäng arbetet)$'), exact=True).click()
                    document_sibling = open_sibling(page, 'document')
                    selected(document_sibling, 'documents')
                    assert len(task_confirmations) == 1
                    expect(task_form.get_by_label('Task title', exact=True)).to_have_value('Choose the courtyard material')
                    assert task_form.evaluate('(node)=>node===window.flowTaskEditor')
                    close_work_card(page, document_sibling)
                    assert len(task_confirmations) == 1
                    choose_chat_channel(page, 'Design')
                    close_channels(page)
                    switch_workspace(page, 'Research Studio')
                    assert len(task_confirmations) == 3 and all('Changes have not saved' in value for value in task_confirmations)
                    page.remove_listener('dialog', decline_task)
                    expect(task_form.get_by_label('Task title', exact=True)).to_have_value('Choose the courtyard material')
                    assert task_form.evaluate('(node)=>node===window.flowTaskEditor')
                    expect(page.locator('.fc-workspace-switch-trigger')).to_contain_text('Nord Studio')
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/projects/flow-project/tasks')) as task_saved:
                        task_form.get_by_role('button', name='Save task', exact=True).click()
                    assert task_saved.value.ok
                    saved_task = task_saved.value.json()
                    expect(task_form).to_have_count(0)
                    expect(dialog.locator('[data-task-id="' + saved_task['id'] + '"]')).to_contain_text('Choose the courtyard material')
                    actual_tasks = native(base, sessions['alpha2']['access_token'], '/channels/shared/projects/flow-project/tasks')['items']
                    assert any(row['id'] == saved_task['id'] and row['fields']['title'] == 'Choose the courtyard material' for row in actual_tasks)
                    leave(page, dialog)
                    checks['real_task_draft_survives_sibling_and_guards_own_close_channel_workspace_then_saves_native_record'] = True

                    stage = 'unsaved native decision guards work target channel and workspace'
                    activate_chat_feature(page, 'decision')
                    notebook = capture_work_card(page).get_by_role('region', name='Decisions in this conversation', exact=True)
                    expect(notebook).to_be_visible()
                    expect(notebook.get_by_role('tablist')).to_have_count(0)
                    notebook.get_by_role('button', name='Record a decision', exact=True).click()
                    decision_title = notebook.get_by_label('Title', exact=True)
                    decision_title.fill('Use local materials')
                    notebook.get_by_label('Decision and context', exact=True).fill('A deliberate native decision without leaving the conversation.')
                    decision_title.evaluate('(node)=>{window.flowDecisionEditor=node}')
                    decision_confirmations = []
                    def decline_decision(value):
                        decision_confirmations.append(value.message)
                        value.dismiss()
                    page.on('dialog', decline_decision)
                    task_sibling = open_sibling(page, 'task')
                    assert not decision_confirmations
                    expect(decision_title).to_have_value('Use local materials')
                    assert decision_title.evaluate('(node)=>node===window.flowDecisionEditor')
                    close_work_card(page, task_sibling)
                    assert not decision_confirmations
                    choose_chat_channel(page, 'Design')
                    close_channels(page)
                    switch_workspace(page, 'Research Studio')
                    assert len(decision_confirmations) == 2
                    page.remove_listener('dialog', decline_decision)
                    expect(decision_title).to_have_value('Use local materials')
                    assert decision_title.evaluate('(node)=>node===window.flowDecisionEditor')
                    composer.fill('My conversation draft stays editable beside the decision.')
                    expect(composer).to_be_focused()
                    expect(decision_title).to_have_value('Use local materials')
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/channels/shared/decisions')) as decision_saved:
                        notebook.get_by_role('button', name='Record decision', exact=True).click()
                    assert decision_saved.value.ok
                    saved_decision = decision_saved.value.json()
                    expect(notebook.get_by_role('heading', name='Use local materials', exact=True)).to_be_visible()
                    actual_decisions = native(base, sessions['alpha2']['access_token'], '/channels/shared/decisions')
                    assert any(row['id'] == saved_decision['id'] and row['content'] == 'A deliberate native decision without leaving the conversation.' for row in actual_decisions)
                    notebook.focus(); notebook.press('Escape')
                    expect(notebook).to_have_count(0)
                    expect(composer).to_have_value('My conversation draft stays editable beside the decision.')
                    checks['real_decision_draft_retains_sibling_and_guards_channel_workspace_then_saves_with_live_composer'] = True
                    dialog, button = work(page)
                    dialog = select(dialog, 'documents')
                    leave(page, dialog, button)

                    stage = 'normal account uploads replaces and removes an actual profile photo'
                    first_photo, replacement_photo = private / 'profile-first.png', private / 'profile-replacement.png'
                    profile_png(first_photo, (128, 69, 42)); profile_png(replacement_photo, (35, 103, 133))
                    token = sessions['alpha2']['access_token']
                    page.get_by_role('button', name='Your account', exact=True).click()
                    account = page.get_by_role('dialog', name='Account settings', exact=True)
                    expect(account.get_by_role('heading', name='Profile photo', exact=True)).to_be_visible()
                    with page.expect_file_chooser() as choose_photo:
                        account.get_by_role('button', name='Upload photo', exact=True).click()
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/account/avatar')) as uploaded:
                        choose_photo.value.set_files(str(first_photo))
                    assert uploaded.value.ok and uploaded.value.json() == {'has_avatar': True}
                    expect(account.get_by_role('status')).to_have_text('Profile photo updated.')
                    first_digest = native_photo(base, token)
                    same_photo(page, account.locator('.fc-account-photo .fc-profile-avatar img'), first_digest)
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('normal-product-profile-photo.png')))
                    account.get_by_role('button', name='Close account settings', exact=True).click()
                    expect(account).to_have_count(0)
                    header_photo = page.locator('.fc-chat-account-trigger .fc-profile-avatar[data-user-id="alpha"] img')
                    message_photo = page.locator('.fc-chat-message .fc-profile-avatar[data-user-id="alpha"] img').first
                    same_photo(page, header_photo, first_digest); same_photo(page, message_photo, first_digest)
                    page.get_by_role('button', name='More channel tools', exact=True).click()
                    page.get_by_role('button', name='Channel members', exact=True).click()
                    members_photo = page.locator('.fc-member-list .fc-profile-avatar[data-user-id="alpha"] img')
                    same_photo(page, members_photo, first_digest)
                    page.get_by_role('button', name='Close channel panel', exact=True).click()

                    page.get_by_role('button', name='Your account', exact=True).click()
                    account = page.get_by_role('dialog', name='Account settings', exact=True)
                    with page.expect_file_chooser() as choose_photo:
                        account.get_by_role('button', name='Change photo', exact=True).click()
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/account/avatar')) as uploaded:
                        choose_photo.value.set_files(str(replacement_photo))
                    assert uploaded.value.ok
                    replacement_digest = native_photo(base, token)
                    assert replacement_digest != first_digest, 'Replacing the photo left the old native bytes'
                    same_photo(page, account.locator('.fc-account-photo .fc-profile-avatar img'), replacement_digest)
                    account.get_by_role('button', name='Close account settings', exact=True).click()
                    same_photo(page, header_photo, replacement_digest); same_photo(page, message_photo, replacement_digest)
                    page.get_by_role('button', name='Your account', exact=True).click()
                    account = page.get_by_role('dialog', name='Account settings', exact=True)
                    with page.expect_response(lambda response: response.request.method == 'DELETE' and response.url.endswith('/account/avatar')) as removed:
                        account.get_by_role('button', name='Use initials instead', exact=True).click()
                    assert removed.value.status == 204
                    expect(account.get_by_role('status')).to_have_text('Profile photo removed.')
                    expect(account.locator('.fc-account-photo .fc-profile-avatar img')).to_have_count(0)
                    expect(account.locator('.fc-account-photo .fc-profile-avatar')).to_have_text('LB')
                    try:
                        native_photo(base, token)
                        raise AssertionError('The deleted profile photo remained available from its native API')
                    except HTTPError as failure:
                        assert failure.code == 404, failure.code
                    account.get_by_role('button', name='Close account settings', exact=True).click()
                    expect(header_photo).to_have_count(0); expect(message_photo).to_have_count(0)
                    expect(page.locator('.fc-chat-account-trigger .fc-profile-avatar')).to_have_text('LB')
                    checks['normal_profile_picker_canvas_upload_replace_delete_and_authenticated_portraits'] = True

                    stage = 'Swedish 320px return, resumed tool and keyboard focus'
                    picker = page.locator('.proof-flow-controls select')
                    picker.select_option('sv')
                    expect(page.locator('.proof-flow-controls')).to_contain_text('Språket har sparats', timeout=10000)
                    page.set_viewport_size({'width': 320, 'height': 900})
                    choose_chat_channel(page, 'Courtyard')
                    button = activate_chat_feature(page, 'document', keyboard=True)
                    button.evaluate('(node)=>{window.flowExpectedReturn=node}')
                    dialog = capture_work_card(page).get_by_role('region', name=DIALOG)
                    selected(dialog, 'documents')
                    expect(dialog.get_by_role('button', name='Tillbaka till samtalet', exact=True)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert dialog.evaluate('(node)=>node.scrollWidth<=node.clientWidth')
                    retained = dialog.element_handle()
                    retained_card = capture_work_card(page, dialog)
                    dialog.get_by_role('button', name='Tillbaka till samtalet', exact=True).click()
                    expect(work_surface(page, retained_card)).to_have_attribute('data-workspace-mode', 'minimized')
                    resume = work_resume_button(page, retained_card)
                    expect(resume).to_have_accessible_name('Fortsätt arbeta')
                    resume.focus(); resume.press('Enter')
                    expect(dialog).to_be_visible()
                    assert dialog.evaluate('(node,previous)=>node===previous&&previous.isConnected', retained)
                    selected(dialog, 'documents')
                    dialog.focus()
                    page.keyboard.press('Escape')
                    expect(dialog).to_have_count(0)
                    assert page.evaluate('window.flowExpectedReturn?.isConnected&&document.activeElement===window.flowExpectedReturn')
                    button = activate_chat_feature(page, 'document', keyboard=True)
                    button.evaluate('(node)=>{window.flowExpectedReturn=node}')
                    dialog = capture_work_card(page).get_by_role('region', name=DIALOG)
                    selected(dialog, 'documents')
                    leave(page, dialog, button)
                    checks['swedish_320px_resume_return_escape_and_focus_without_overflow'] = True
                    assert not errors, 'Unexpected actual conversation-flow browser JavaScript error'
                    checks['zero_browser_page_errors'] = True
                except Exception:
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('conversation-flow-failure.png')), full_page=True)
                    raise
                finally:
                    context.close()
                    browser.close()
        except Exception:
            print('FAILED conversation-flow stage: ' + stage, file=sys.stderr)
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            for name in ['api.log', 'vite.log']:
                if (private / name).is_file(): shutil.copyfile(private / name, REPORT.with_name('conversation-flow-' + name))
            raise
        finally:
            (private / 'update.release').touch()
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for stream in streams:
                stream.close()
    assert private_path is not None and not Path(private_path).exists()
    for name, value in source_hashes.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value, 'Production source changed during the proof: ' + name
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Unedited actual App/Chat/inline WorkHub source snapshot, private real API/SQLite/Vite, two accounts and workspaces, actual production navigation events, gated real native document update, unsaved native task/decision target/channel/workspace guards, simultaneous real message/composer and editor continuity, and actual account-photo picker/canvas/upload/replace/delete with authenticated portrait bytes; no Matrix or provider acceptance claim. Imported product-demo frame dependencies were not visited; the actual example-data installation has a separate product-demo-browser proof.', 'checks': checks, 'source_sha256': source_hashes, 'unvisited_demo_compile_dependency_sha256': demo_dependencies, 'cleanup': 'Own API/Vite/browser/database/session keys/private source snapshot removed; no Docker or original pilot service used.'}, indent=2) + '\n')
    print('Conversation-flow browser proof passed: ' + str(len(checks)) + ' groups; inline work without modal/tabs, scoped resume, same interactive conversation, native document/task/decision guards and saves, profile-photo UI, account isolation and Swedish 320px.')


if __name__ == '__main__':
    main()
