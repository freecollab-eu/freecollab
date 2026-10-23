#!/usr/bin/env python3
"""Actual conversation-to-work continuity with isolated production services.

Production App/Chat/WorkHub run from an unedited private source snapshot. A fresh
SQLite database supplies two accounts, two workspaces and independent native
channels. The optional document gate holds the real update request before its
production handler; no API response, document state or navigation state is mocked.
"""
from ui_navigation import choose_chat_channel, close_channels
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
REPORT = Path(os.getenv('CONVERSATION_FLOW_REPORT', str(ROOT.parent / '.freecollab/proof-results/conversation-flow-browser.json')))
WORK = re.compile(r'^(Open workspace tools|Öppna arbetsytans verktyg)$')
DIALOG = re.compile(r'^(Workspace tools|Arbetsytans verktyg)$')
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


def native(base, token, path):
    with urlopen(Request(base + path, headers={'Authorization': 'Bearer ' + token}), timeout=10) as response:
        return json.load(response)


def work(page):
    close_channels(page)
    button = page.get_by_role('button', name=WORK)
    button.click()
    dialog = page.get_by_role('dialog', name=DIALOG)
    expect(dialog).to_be_visible()
    return dialog, button


def selected(dialog, value):
    expect(dialog.locator('.fc-work-hub-tool-switch select')).to_have_value(value)
    expect(dialog.get_by_role('tab', selected=True, include_hidden=True)).to_have_count(1)
    expect(dialog.locator('#work-tab-' + value)).to_have_attribute('aria-selected', 'true')


def select(dialog, value):
    picker = dialog.locator('.fc-work-hub-tool-switch select')
    if picker.is_visible():
        picker.select_option(value)
    else:
        dialog.locator('#work-tab-' + value).click()
    selected(dialog, value)


def leave(page, dialog, button=None):
    dialog.get_by_role('button', name=RETURN).click()
    expect(dialog).to_have_count(0)
    if button is not None:
        expect(button).to_be_focused()


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
        relevant = ['components/Chat.tsx', 'components/WorkHub.tsx', 'account/AccountProvider.tsx', 'i18n/index.tsx', 'i18n/core.ts', 'i18n/demo-natural-flow.sv.ts', 'lib/api.ts', 'lib/useModalFocus.ts', 'App.tsx']
        relevant += [str(path.relative_to(source)) for path in source.glob('lib/*work*hub*')]
        relevant += [str(path.relative_to(source)) for path in source.glob('i18n/*work*hub*')]
        for name in sorted(set(relevant)):
            source_hashes['frontend/src/' + name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
        source_hashes['frontend/tests/conversation-flow-browser.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
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
                    expect(page.locator('.fc-chat-message')).to_have_count(50)
                    assert timeline.evaluate('(node)=>node.scrollHeight>node.clientHeight+400')
                    timeline.evaluate('(node)=>{node.scrollTop=Math.min(300,node.scrollHeight-node.clientHeight);node.dispatchEvent(new Event("scroll"));window.flowTimeline=node;window.flowScroll=node.scrollTop}')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    dialog.evaluate('(node)=>{window.flowDialog=node}')
                    select(dialog, 'documents')
                    assert dialog.evaluate('(node)=>node===window.flowDialog'), 'Changing tools remounted the dialog'
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
                    select(dialog, 'feed')
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
                    dialog = page.get_by_role('dialog', name=DIALOG)
                    selected(dialog, 'documents')
                    leave(page, dialog)
                    checks['explicit_production_resource_navigation_overrides_remembered_generic_view'] = True

                    stage = 'tool memory stays in this visit and is not restored after reload'
                    page.reload(wait_until='domcontentloaded')
                    choose_chat_channel(page, 'Design')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    select(dialog, 'documents')
                    leave(page, dialog, button)
                    checks['reload_starts_default_view_instead_of_persisting_tool_memory'] = True

                    stage = 'workspace switch clears previous workspace view memory'
                    switch_workspace(page, 'Research Studio')
                    choose_chat_channel(page, 'Research')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    select(dialog, 'feed')
                    leave(page, dialog, button)
                    switch_workspace(page, 'Nord Studio')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    select(dialog, 'documents')
                    leave(page, dialog, button)
                    checks['workspace_changes_drop_old_tool_memory'] = True

                    stage = 'credential rotation and another account cannot inherit a tool view'
                    page.evaluate('window.switchFlowAccount("alpha2")')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    select(dialog, 'documents')
                    leave(page, dialog, button)
                    page.evaluate('window.switchFlowAccount("beta")')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    select(dialog, 'feed')
                    leave(page, dialog, button)
                    page.evaluate('window.switchFlowAccount("alpha2")')
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'tasks')
                    checks['same_user_new_credential_and_account_roundtrip_start_without_old_memory'] = True

                    stage = 'actual unsynchronized document guards both return and tool changes'
                    select(dialog, 'documents')
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
                    def decline(value):
                        confirmations.append(value.message)
                        value.dismiss()
                    page.once('dialog', decline)
                    dialog.get_by_role('button', name=RETURN).click()
                    assert len(confirmations) == 1 and 'Changes have not saved' in confirmations[0]
                    expect(dialog).to_be_visible()
                    expect(body).to_have_value('This real pending document edit must stay here until synchronized.')
                    dialog.evaluate('(node)=>{window.flowGuardedDialog=node}')
                    page.once('dialog', decline)
                    dialog.locator('#work-tab-tasks').click()
                    assert len(confirmations) == 2
                    selected(dialog, 'documents')
                    assert dialog.evaluate('(node)=>node===window.flowGuardedDialog')
                    (private / 'update.release').touch()
                    expect(dialog.locator('.fc-shared-sync')).to_have_text('All edits synchronized', timeout=15000)
                    updates = native(base, sessions['alpha2']['access_token'], '/channels/shared/shared-documents/' + document['id'] + '/updates')
                    assert len(updates['items']) >= 2, 'Actual update did not reach the production server'
                    leave(page, dialog)
                    dialog, button = work(page)
                    selected(dialog, 'documents')
                    leave(page, dialog, button)
                    checks['real_document_pending_update_blocks_return_and_tab_change_then_synchronizes'] = True

                    stage = 'Swedish 320px return, resumed tool and keyboard focus'
                    picker = page.locator('.proof-flow-controls select')
                    picker.select_option('sv')
                    expect(page.locator('.proof-flow-controls')).to_contain_text('Språket har sparats', timeout=10000)
                    page.set_viewport_size({'width': 320, 'height': 900})
                    choose_chat_channel(page, 'Courtyard')
                    dialog, button = work(page)
                    selected(dialog, 'documents')
                    expect(dialog.get_by_role('button', name='Tillbaka till samtalet', exact=True)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert dialog.evaluate('(node)=>node.scrollWidth<=node.clientWidth')
                    page.keyboard.press('Escape')
                    expect(dialog).to_have_count(0)
                    expect(button).to_be_focused()
                    dialog, button = work(page)
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
    REPORT.write_text(json.dumps({'scope': 'Unedited actual App/Chat/WorkHub source snapshot, private real API/SQLite/Vite, two accounts and workspaces, actual production navigation events, gated real native document update; no Matrix or provider acceptance claim. Imported product-demo frame dependencies were not visited; the actual example-data installation has a separate product-demo-browser proof.', 'checks': checks, 'source_sha256': source_hashes, 'unvisited_demo_compile_dependency_sha256': demo_dependencies, 'cleanup': 'Own API/Vite/browser/database/session keys/private source snapshot removed; no Docker or original pilot service used.'}, indent=2) + '\n')
    print('Conversation-flow browser proof passed: ' + str(len(checks)) + ' groups; scoped tool resume, same conversation, actual pending-document guards, navigation overrides, account isolation and Swedish 320px.')


if __name__ == '__main__':
    main()
