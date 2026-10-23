#!/usr/bin/env python3
"""Real product UI/API with disposable demo visits, never mocked business routes.

Runs only owned loopback services, synthetic keys and a dedicated temporary
public-demo SQLite database. Does not use retained installations or providers.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
from ui_navigation import choose_chat_channel, open_chat_tool, reveal_message_actions

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('PRODUCT_DEMO_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/product-demo-20261023/product-demo-browser.json')))
SOURCES = [
    'frontend/src/demo-entry.tsx', 'frontend/demo/index.html', 'frontend/vite.demo.config.ts',
    'frontend/src/components/ProductDemoSession.tsx', 'frontend/src/lib/product-demo-session.ts',
    'frontend/src/styles/product-demo.css', 'frontend/src/i18n/product-demo.sv.ts', 'frontend/src/components/Chat.tsx',
    'frontend/src/components/WorkHub.tsx', 'frontend/src/components/SharedDocumentsPanel.tsx',
    'frontend/src/components/SharedDocumentOrganization.tsx', 'frontend/src/components/ProjectTasksPanel.tsx',
    'frontend/src/components/ChannelMetadataPanel.tsx', 'frontend/src/components/ChannelFiles.tsx',
    'frontend/src/lib/api.ts', 'frontend/src/lib/socket.ts', 'frontend/src/lib/federated-work-pointers.ts', 'frontend/src/account/AccountProvider.tsx',
    'frontend/src/appearance/AppearanceProvider.tsx', 'frontend/src/i18n/index.tsx',
    'frontend/src/mobile/storage.ts', 'frontend/src/styles/chat.css',
    'backend/app/main.py', 'backend/app/config.py', 'backend/app/api/public_demo.py',
    'backend/app/services/public_demo.py', 'backend/app/services/public_demo_seed.py', 'backend/app/services/public_demo_boundary.py',
    'backend/app/models/public_demo.py', 'backend/app/services/integration_worker.py',
    'backend/app/services/workflow.py', 'backend/app/api/workflows.py', 'frontend/src/components/WorkflowsPanel.tsx',
    'frontend/tests/product-demo-browser.py',
]


def runtime_hashes(root):
    names = set(SOURCES)
    for directory in ('frontend/src', 'backend/app'):
        names.update(str(path.relative_to(root)) for path in (root / directory).rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def request(base, method, path, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(data).encode() if data is not None else None
    with urlopen(Request(base + path, method=method, headers=headers, data=body), timeout=45) as response:
        value = response.read()
        return json.loads(value) if value else None


def rejected(base, path, token):
    try:
        request(base, 'GET', path, token)
    except HTTPError as failure:
        assert failure.code in (401, 403, 404), failure.code
        return
    raise AssertionError('A different demo visit could read this resource')


def ready(url, processes):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'Owned demo fixture exited'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError):
            pass
        time.sleep(.2)
    raise AssertionError('Owned demo fixture did not become ready')


def stop(processes):
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


INSTRUMENTATION = r"""(() => {
  const original = Object.fromEntries(['getItem','setItem','removeItem','clear','key'].map(key => [key,Storage.prototype[key]]));
  original.setItem.call(localStorage,'freecollab_token','retained-real-token-sentinel');
  original.setItem.call(localStorage,'freecollab_user_id','retained-real-user-sentinel');
  original.setItem.call(localStorage,'freecollab_public_key','retained-real-public-key-sentinel');
  original.setItem.call(localStorage,'freecollab_encrypted_private_key','retained-real-encrypted-key-sentinel');
  original.setItem.call(sessionStorage,'retained-session','retained-real-session-sentinel');
  const calls = [], databaseCalls = []; let databases = 0, workers = 0;
  for (const name of Object.keys(original)) Storage.prototype[name] = function(...args) {
    calls.push({method:name,key:String(args[0]??''),area:this===localStorage?'local':'session'});
    return original[name].apply(this,args);
  };
  const open = indexedDB.open.bind(indexedDB), remove = indexedDB.deleteDatabase.bind(indexedDB);
  indexedDB.open = (...args) => { databases++; databaseCalls.push({method:"open",name:String(args[0])}); return open(...args); };
  indexedDB.deleteDatabase = (...args) => { databases++; databaseCalls.push({method:"deleteDatabase",name:String(args[0])}); return remove(...args); };
  if (navigator.serviceWorker) {
    const register = navigator.serviceWorker.register.bind(navigator.serviceWorker);
    navigator.serviceWorker.register = (...args) => { workers++; return register(...args); };
  }
  window.__demoStorageProof = () => ({calls:[...calls],databases,databaseCalls:[...databaseCalls],workers,retained:{
    token:original.getItem.call(localStorage,'freecollab_token'),
    user:original.getItem.call(localStorage,'freecollab_user_id'),
    publicKey:original.getItem.call(localStorage,'freecollab_public_key'),
    privateKey:original.getItem.call(localStorage,'freecollab_encrypted_private_key'),
    session:original.getItem.call(sessionStorage,'retained-session'),
  }});
})()"""


def storage_untouched(page):
    state = page.evaluate('window.__demoStorageProof()')
    assert state['retained'] == {
        'token': 'retained-real-token-sentinel', 'user': 'retained-real-user-sentinel',
        'publicKey': 'retained-real-public-key-sentinel', 'privateKey': 'retained-real-encrypted-key-sentinel',
        'session': 'retained-real-session-sentinel',
    }
    private = [call for call in state['calls'] if call['key'].startswith(('freecollab_', 'freecollab.', 'freecollab:')) or call['method'] == 'clear']
    assert not private, 'Product demo touched retained account storage: ' + json.dumps(private)
    assert state['databases'] == 0 and state['workers'] == 0, 'Product demo started persistent/offline infrastructure: ' + json.dumps({'databases':state['databaseCalls'],'workers':state['workers']})


def main():
    results, processes, streams = {}, [], []
    stage = 'owned source snapshot and isolated services'
    observed = []
    with tempfile.TemporaryDirectory(prefix='freecollab-product-demo-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = port(), port()
            base, app = 'http://127.0.0.1:' + str(api_port), 'http://127.0.0.1:' + str(app_port)
            frontend, backend, data = private / 'frontend', private / 'backend', private / 'public-demo'
            data.mkdir(); backend.mkdir()
            shutil.copytree(ROOT / 'backend/app', backend / 'app', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(ROOT / 'frontend', frontend, ignore=shutil.ignore_patterns('node_modules', 'dist*', '.vite*', '.git', '__pycache__'))
            (frontend / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            source_hashes = runtime_hashes(private)
            env = os.environ.copy()
            env.update(ENVIRONMENT='test', DEMO_INSTANCE='true', DEMO_DATA_PATH=str(data), DATABASE_URL='sqlite:///' + str(data / 'public-demo.sqlite'),
                STORAGE_PATH=str(data / 'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(data / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(data / 'account-security.key'),
                STORAGE_BACKEND='local', MAIL_DELIVERY_MODE='disabled', DATA_CONTROLS_ENABLED='false', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), REDIS_URL='',
                MEETINGS_SFU_PROVIDER='disabled', MEETINGS_ICE_SERVERS='[]', MEETINGS_TURN_URLS='[]', VITE_API_URL='/api',
                PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(backend))
            for flag in ['MATRIX_ENABLED','CONNECT_FEDERATION_ENABLED','OIDC_ENABLED','WEBHOOKS_ENABLED','SYNC_ENABLED','OTRUST_ENABLED',
                         'CLOUD_FILES_ENABLED','EXTERNAL_MEETINGS_ENABLED','COPILOT_ENABLED','PUSH_ENABLED','NATIVE_PUSH_ENABLED','NATIVE_CALLS_ENABLED',
                         'ENABLE_EXPERIMENTAL_FEATURES','ENTERPRISE_SEARCH_ENABLED']:
                env[flag] = 'false'
            env['NO_PROXY'] = ','.join(filter(None,[env.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').exists() else sys.executable)
            if os.sep in python:
                python = os.path.abspath(python)
            server = private / 'api.py'
            server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            vite = private / 'vite.mjs'
            vite.write_text('import {createServer} from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) + ';\n'
                'const server=await createServer({configFile:' + json.dumps(str(frontend / 'vite.demo.config.ts')) + ',cacheDir:' + json.dumps(str(private / 'vite-cache')) +
                ',server:{host:"127.0.0.1",port:' + str(app_port) + ',strictPort:true,fs:{allow:' + json.dumps([str(private), str(ROOT / 'frontend')]) + '},proxy:{"/api":{target:' + json.dumps(base) +
                ',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:' + json.dumps(base) + ',ws:true}}}});await server.listen();\n')
            for command, filename, cwd in [([python, str(server)], 'api.log', backend), (['node', str(vite)], 'vite.log', frontend)]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=stream, start_new_session=True))
            ready(base + '/ready', processes); ready(app + '/product-demo/', processes)
            worker_log = (private / 'worker.log').open('w'); streams.append(worker_log)
            processes.append(subprocess.Popen([python, '-m', 'app.services.integration_worker', '--interval', '1'], cwd=backend, env=env, stdout=worker_log, stderr=worker_log, start_new_session=True))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                contexts = []; errors, unexpected, credentials, sockets = [], [], [], []
                def context(locale='en-US'):
                    value = browser.new_context(viewport={'width':1440,'height':1000}, locale=locale, reduced_motion='reduce')
                    value.add_init_script(INSTRUMENTATION)
                    def network(route):
                        url = urlsplit(route.request.url)
                        if url.scheme not in ('data','blob') and (url.hostname != '127.0.0.1' or url.port != app_port):
                            unexpected.append(url.scheme + '://' + url.netloc + url.path); route.abort(); return
                        if 'retained-real-' in route.request.headers.get('authorization',''):
                            credentials.append('retained account credential used')
                        route.continue_()
                    value.route('**/*', network); contexts.append(value)
                    tab = value.new_page(); tab.set_default_timeout(30000)
                    tab.on('pageerror', lambda failure: errors.append(type(failure).__name__))
                    tab.on('response', lambda response: observed.append({'method':response.request.method,'path':urlsplit(response.url).path,'status':response.status}) if urlsplit(response.url).path.startswith('/api/') else None)
                    tab.on('websocket', lambda socket: sockets.append(socket.url))
                    return tab
                page = context()
                def bootstrap(tab, action):
                    with tab.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as response:
                        action()
                    assert response.value.ok, 'Actual demo session bootstrap failed'
                    visit = response.value.json(); expect(tab.locator('.fc-chat')).to_be_visible(timeout=90000)
                    expect(tab.locator('.fc-product-demo-banner')).to_be_visible()
                    return visit
                try:
                    stage = 'normal signed demo visit and actual product shell'
                    visit = bootstrap(page, lambda: page.goto(app + '/product-demo/?locale=en',wait_until='domcontentloaded'))
                    token, channel = visit['session']['access_token'], visit['channel_id']
                    me = request(base,'GET','/auth/me',token); assert me['id'] == visit['session']['user_id']
                    channels = request(base,'GET','/channels?workspace_id=' + visit['workspace_id'],token)
                    selected = next(row for row in channels if row['id'] == channel)
                    expect(page.locator('#chat-channel-name')).to_have_text(selected['name'])
                    messages = request(base,'GET','/messages/channels/' + channel + '?limit=100',token)
                    assert len(channels) >= 2 and len(messages) >= 3 and len({row['author_id'] for row in messages}) >= 2
                    assert not page.locator('.demo-showroom,.demo-document-desk,.demo-inline-assist').count()
                    results['normal_signed_session_actual_Chat_and_seeded_product_API'] = True; print('PASS group: normal_signed_session_actual_Chat_and_seeded_product_API',flush=True)

                    stage = 'same channel switching and channel-local composer drafts'
                    draft = 'Keep my real product draft.'; composer = page.locator('.fc-chat-composer textarea')
                    composer.fill(draft); other = next(row for row in channels if row['id'] != channel)
                    choose_chat_channel(page,other['name']); expect(page.locator('#chat-channel-name')).to_have_text(other['name']); expect(composer).to_have_value('')
                    choose_chat_channel(page,selected['name']); expect(composer).to_have_value(draft)
                    results['same_product_channel_switch_and_retained_drafts'] = True; print('PASS group: same_product_channel_switch_and_retained_drafts',flush=True)

                    stage = 'real message send edit reaction and thread reply'
                    text = 'A real sample conversation contribution.'
                    composer.fill(text)
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/mobile/offline/channels/' + channel + '/messages') as sent:
                        page.get_by_role('button',name='Send message',exact=True).click()
                    assert sent.value.ok; message = sent.value.json(); article = page.locator('#message-' + message['id']); expect(article).to_contain_text(text)
                    reveal_message_actions(article); article.get_by_role('button',name='Edit',exact=True).click(); article.get_by_label('Edit message',exact=True).fill(text + ' Revised.'); article.get_by_role('button',name='Save edit',exact=True).click(); expect(article).to_contain_text(text + ' Revised.')
                    reveal_message_actions(article); article.get_by_role('button',name='Add reaction',exact=True).click(); article.get_by_role('button',name='React 👍',exact=True).click(); expect(article.locator('.fc-chat-reactions')).to_contain_text('👍')
                    reveal_message_actions(article); article.get_by_role('button',name='Reply in thread',exact=True).click(); thread = page.get_by_role('dialog',name='Thread',exact=True)
                    thread.get_by_label('Reply in thread',exact=True).fill('A reply stored by the normal thread API.'); thread.get_by_role('button',name='Send reply',exact=True).click(); expect(thread).to_contain_text('A reply stored by the normal thread API.'); thread.get_by_role('button',name='Close thread',exact=True).click()
                    assert request(base,'GET','/messages/' + message['id'],token)['content'].endswith(' Revised.')
                    results['normal_message_edit_reaction_and_thread_writes'] = True; print('PASS group: normal_message_edit_reaction_and_thread_writes',flush=True)

                    stage = 'actual channel details CAS save'
                    open_chat_tool(page,'Channel details'); details = page.get_by_role('dialog',name='Manage channel',exact=True)
                    details.get_by_label('Channel name',exact=True).fill('Demo studio reviewed'); details.get_by_label('Channel description',exact=True).fill('Saved with the product channel metadata API.'); details.get_by_role('button',name='Save channel',exact=True).click()
                    expect(details).to_have_count(0); expect(page.locator('#chat-channel-name')).to_have_text('Demo studio reviewed')
                    assert request(base,'GET','/channels/' + channel + '/metadata',token)['description'] == 'Saved with the product channel metadata API.'
                    results['normal_channel_metadata_save_and_header_update'] = True; print('PASS group: normal_channel_metadata_save_and_header_update',flush=True)

                    def work(tab='Documents'):
                        open_chat_tool(page,'Open workspace tools'); panel = page.get_by_role('dialog',name='Workspace tools',exact=True)
                        panel.get_by_role('tab',name=tab,exact=True).click(); return panel
                    stage = 'actual sample documents and Yjs editing'
                    panel = work(); seeded_docs = request(base,'GET','/channels/' + channel + '/shared-documents',token); assert len(seeded_docs) >= 2
                    panel.get_by_role('button',name='New shared document',exact=True).click(); panel.get_by_label('Document title',exact=True).fill('Product demo verification page'); panel.get_by_role('button',name='Create document',exact=True).click()
                    editor = panel.get_by_label('Document text',exact=True); expect(editor).to_be_editable(); editor.fill('Real Yjs sample edits, not a parallel Markdown mock.'); expect(panel.get_by_text('All edits synchronized',exact=True)).to_be_visible()
                    document = next(row for row in request(base,'GET','/channels/' + channel + '/shared-documents',token) if row['title'] == 'Product demo verification page')
                    panel.get_by_label('Add a comment',exact=True).fill('A comment on the actual shared page.'); panel.get_by_role('button',name='Post comment',exact=True).click(); expect(panel.get_by_text('A comment on the actual shared page.',exact=True)).to_be_visible()
                    results['normal_Yjs_document_editor_and_comment_write'] = True; print('PASS group: normal_Yjs_document_editor_and_comment_write',flush=True)

                    stage = 'actual document stack move and guarded Undo'
                    panel.get_by_role('button',name='All documents',exact=True).click()
                    panel.get_by_role('button',name='New stack',exact=True).click(); panel.get_by_label('Stack name',exact=True).fill('Reviewed examples'); panel.get_by_role('button',name='Create stack',exact=True).click()
                    expect(panel.get_by_role('region',name='Reviewed examples',exact=True)).to_be_visible()
                    organization = '/channels/' + channel + '/shared-document-organization'
                    stack = next(row for row in request(base,'GET',organization,token)['stacks'] if row['title'] == 'Reviewed examples')
                    panel.get_by_label('Move Product demo verification page to stack',exact=True).select_option(stack['id']); region = panel.get_by_role('region',name='Reviewed examples',exact=True); expect(region.get_by_role('button',name=re.compile('^Product demo verification page'))).to_be_visible()
                    panel.get_by_role('button',name='Undo move',exact=True).click(); expect(panel.get_by_label('Move Product demo verification page to stack',exact=True)).to_have_value('')
                    panel.get_by_label('Move Product demo verification page to stack',exact=True).select_option(stack['id']); expect(region.get_by_text('A comment on the actual shared page.',exact=True)).to_be_visible()
                    region.get_by_role('button',name='Comment in Product demo verification page',exact=True).click(); expect(panel.get_by_label('Document text',exact=True)).to_have_value('Real Yjs sample edits, not a parallel Markdown mock.')
                    results['normal_stack_placement_Undo_and_derived_discussion_source'] = True; print('PASS group: normal_stack_placement_Undo_and_derived_discussion_source',flush=True)
                    panel.get_by_role('button',name='Close workspace tools',exact=True).click()

                    stage = 'message reference and return to original with composer intact'
                    composer.fill('Unsent original conversation draft.'); reveal_message_actions(article); article.get_by_role('button',name='Save in document stack',exact=True).click(); panel = page.get_by_role('dialog',name='Workspace tools',exact=True)
                    review = panel.get_by_role('region',name='Save message in a document stack',exact=True); expect(review.locator('blockquote')).to_have_text(text + ' Revised.'); review.get_by_label('Destination stack',exact=True).select_option(stack['id']); review.get_by_role('button',name='Save message reference',exact=True).click()
                    saved_region = panel.get_by_role('region',name='Reviewed examples',exact=True); saved_region.get_by_role('button',name='Open source message',exact=True).click(); expect(panel).to_have_count(0); expect(article).to_be_focused(); expect(composer).to_have_value('Unsent original conversation draft.')
                    results['normal_stack_message_reference_reveals_exact_original'] = True; print('PASS group: normal_stack_message_reference_reveals_exact_original',flush=True)

                    stage = 'actual project task and checked state'
                    held_boards = []
                    def delay_initial_boards(route):
                        if route.request.method == 'GET' and urlsplit(route.request.url).path == '/api/channels/' + channel + '/projects': held_boards.append(route)
                        else: route.continue_()
                    page.route('**/api/channels/' + channel + '/projects*',delay_initial_boards)
                    panel = work('Tasks'); expect(panel.get_by_role('button',name='New project',exact=True)).to_be_disabled()
                    for _ in range(100):
                        if held_boards: break
                        page.wait_for_timeout(25)
                    assert held_boards
                    held_boards.pop(0).continue_(); page.unroute('**/api/channels/' + channel + '/projects*',delay_initial_boards)
                    panel.get_by_role('button',name='New project',exact=True).click(); form = panel.get_by_role('form',name='New project',exact=True)
                    form.get_by_label('Project title',exact=True).fill('Demo verification'); form.get_by_label('Description',exact=True).fill('Real product task workflow.'); form.get_by_role('button',name='Save project',exact=True).click()
                    panel.get_by_role('button',name='New task',exact=True).click(); form = panel.get_by_role('form',name='New task',exact=True); form.get_by_label('Task title',exact=True).fill('Review the product demo'); form.get_by_label('Description',exact=True).fill('Use the same task model.'); form.get_by_role('button',name='Add checklist item',exact=True).click(); form.get_by_label('Checklist item 1',exact=True).fill('Confirm UI parity'); form.get_by_role('button',name='Save task',exact=True).click()
                    card = panel.locator('.fc-project-card').filter(has=page.get_by_role('heading',name='Review the product demo',exact=True)); card.get_by_label('Confirm UI parity',exact=True).click(); expect(card.get_by_label('Confirm UI parity',exact=True)).to_be_checked(); card.get_by_label('Move task Review the product demo',exact=True).select_option('done'); expect(panel.get_by_role('region',name='Done',exact=True).get_by_role('heading',name='Review the product demo',exact=True)).to_be_visible()
                    results['normal_project_task_checklist_and_status_writes'] = True; print('PASS group: normal_project_task_checklist_and_status_writes',flush=True)
                    panel.get_by_role('button',name='Close workspace tools',exact=True).click()

                    stage = 'normal seeded workflow approval and real background task creation'
                    panel = work('Workflows'); workflow_card = panel.locator('.fc-workflow-card').filter(has=page.get_by_role('heading',name='From review to next step',exact=True))
                    workflow_card.get_by_role('button',name='Start workflow',exact=True).click()
                    panel.get_by_role('button',name='Approve',exact=True).wait_for(state='visible',timeout=45000)
                    panel.get_by_label('Approval note (optional)',exact=True).fill('Reviewed from the real product demo.')
                    panel.get_by_role('button',name='Approve',exact=True).click()
                    expect(panel.locator('.fc-workflow-runs').get_by_text('Completed',exact=True)).to_be_visible(timeout=45000)
                    prefix = '/workspaces/' + visit['workspace_id'] + '/channels/' + channel + '/workflows'
                    runs = request(base,'GET',prefix + '/runs/history',token)['runs']; assert any(row['status'] == 'completed' for row in runs)
                    generated = request(base,'GET','/channels/' + channel + '/projects/' + visit['examples']['project_id'] + '/tasks',token)['items']
                    assert any(row.get('fields',{}).get('title') == 'Follow up after the launch review' for row in generated)
                    panel.get_by_role('button',name='Close workspace tools',exact=True).click()
                    results['normal_workflow_Run_Approve_and_real_background_task_creation'] = True; print('PASS group: normal_workflow_Run_Approve_and_real_background_task_creation',flush=True)

                    stage = 'normal file upload and exact download'
                    open_chat_tool(page,'Channel files'); files = page.get_by_role('dialog',name='Channel files',exact=True); content = b'Real demo file upload through the normal product endpoint.\n'
                    files.get_by_label('Choose a file',exact=True).set_input_files({'name':'Product demo sample.txt','mimeType':'text/plain','buffer':content})
                    expect(files.get_by_role('button',name='Download Product demo sample.txt',exact=True)).to_be_visible()
                    with page.expect_download() as downloaded:
                        files.get_by_role('button',name='Download Product demo sample.txt',exact=True).click()
                    assert Path(downloaded.value.path()).read_bytes() == content; files.get_by_role('button',name='Close channel panel',exact=True).click()
                    results['normal_XHR_file_upload_and_exact_authenticated_download'] = True; print('PASS group: normal_XHR_file_upload_and_exact_authenticated_download',flush=True)

                    stage = 'real saved appearance and truthful unconfigured AI'
                    open_chat_tool(page,'Appearance settings'); appearance = page.get_by_role('dialog',name='Make space your own.',exact=True); appearance.get_by_role('radio',name='Dark',exact=True).check(); appearance.get_by_role('button',name='Save changes',exact=True).click(); expect(appearance).to_have_count(0)
                    assert request(base,'GET','/preferences/appearance',token)['mode'] == 'dark'
                    panel = work('Copilot'); expect(panel).to_contain_text(re.compile('not enabled|not configured|disabled',re.I)); assert not page.get_by_role('button',name='Send to AI',exact=True).is_enabled() if page.get_by_role('button',name='Send to AI',exact=True).count() else True
                    panel.get_by_role('button',name='Close workspace tools',exact=True).click()
                    results['normal_appearance_save_and_truthful_external_AI_boundary'] = True; print('PASS group: normal_appearance_save_and_truthful_external_AI_boundary',flush=True)

                    stage = 'actual Microsoft and Google guides show disabled public integrations'
                    open_chat_tool(page,'Integrations'); integrations = page.get_by_role('dialog',name='Integrations',exact=True)
                    integrations.get_by_role('button',name='Connection setup',exact=True).click()
                    integrations.locator('[data-connection-recipe="files.microsoft"]').click()
                    guide = integrations.get_by_role('region',name='Connection setup guide',exact=True)
                    for recipe in ('files.microsoft','files.google'):
                        guide.get_by_label('Connection recipe',exact=True).select_option(recipe)
                        expect(guide.locator('[data-connection-step="configure"]')).to_be_disabled()
                        expect(guide.locator('.fc-connection-status')).to_have_text('Unavailable')
                        expect(guide.get_by_role('region',name='Before you can connect',exact=True)).to_be_visible()
                    integrations.get_by_role('button',name='Close integrations',exact=True).click()
                    results['actual_Microsoft_and_Google_guides_truthfully_disabled_without_authorization'] = True; print('PASS group: actual_Microsoft_and_Google_guides_truthfully_disabled_without_authorization',flush=True)

                    stage = 'separate visit cannot read first visitor content'
                    peer = context(); peer_visit = bootstrap(peer,lambda: peer.goto(app + '/product-demo/?locale=en',wait_until='domcontentloaded'))
                    assert peer_visit['workspace_id'] != visit['workspace_id'] and peer_visit['session']['user_id'] != visit['session']['user_id']
                    rejected(base,'/messages/' + message['id'],peer_visit['session']['access_token']); rejected(base,'/channels/' + channel + '/shared-documents',peer_visit['session']['access_token'])
                    assert peer.locator('.fc-chat-timeline').get_by_text(text + ' Revised.',exact=True).count() == 0
                    storage_untouched(peer)
                    results['normal_auth_isolates_independent_sample_visitors'] = True; print('PASS group: normal_auth_isolates_independent_sample_visitors',flush=True)

                    stage = 'reset creates new isolated visit without changing stored real credentials'
                    page.once('dialog',lambda dialog:dialog.accept())
                    reset = bootstrap(page,lambda:page.get_by_role('button',name='Reset examples',exact=True).click())
                    assert reset['workspace_id'] != visit['workspace_id'] and reset['session']['user_id'] != visit['session']['user_id']
                    rejected(base,'/messages/' + message['id'],reset['session']['access_token']); expect(page.locator('.fc-chat-composer textarea')).to_have_value('')
                    storage_untouched(page); results['explicit_reset_replaces_only_the_temporary_sample_session'] = True; print('PASS group: explicit_reset_replaces_only_the_temporary_sample_session',flush=True)

                    stage = 'Swedish320px same product controls and literal sample content'
                    swedish = context('sv-SE'); sv_visit = bootstrap(swedish,lambda:swedish.goto(app + '/product-demo/?locale=sv',wait_until='domcontentloaded')); swedish.set_viewport_size({'width':320,'height':850})
                    expect(swedish.get_by_role('button',name='Skicka meddelande',exact=True)).to_be_visible(); assert swedish.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    open_chat_tool(swedish,'Öppna arbetsytans verktyg'); sv_panel = swedish.get_by_role('dialog',name='Arbetsytans verktyg',exact=True); sv_panel.get_by_label('Verktyg',exact=True).select_option('documents'); expect(sv_panel.get_by_role('button',name='Nytt delat dokument',exact=True)).to_be_visible(); expect(sv_panel.get_by_role('button',name=re.compile('^Launch brief'))).to_be_visible(); assert sv_panel.evaluate('node=>node.scrollWidth <= node.clientWidth + 1')
                    REPORT.parent.mkdir(parents=True,exist_ok=True); swedish.screenshot(path=str(REPORT.with_name('product-demo-sv-320.png'))); page.screenshot(path=str(REPORT.with_name('product-demo-en-1440.png'))); storage_untouched(swedish)
                    results['Swedish320px_uses_the_same_actual_product_and_documents_panel'] = True; print('PASS group: Swedish320px_uses_the_same_actual_product_and_documents_panel',flush=True)

                    stage = 'privacy network and exact source checks'
                    assert not errors and not unexpected and not credentials, json.dumps({'pageErrors':errors,'unexpectedDestinations':unexpected,'credentialUse':credentials})
                    assert all(urlsplit(url).hostname == '127.0.0.1' and urlsplit(url).port == app_port for url in sockets)
                    assert source_hashes == runtime_hashes(ROOT), 'Runtime source changed during the proof'
                    results['no_external_providers_retained_credentials_persistent_vault_or_service_worker'] = True; print('PASS group: no_external_providers_retained_credentials_persistent_vault_or_service_worker',flush=True)
                except Exception:
                    REPORT.parent.mkdir(parents=True,exist_ok=True)
                    if not page.is_closed():
                        page.screenshot(path=str(REPORT.with_name('product-demo-failure.png')))
                        print('Visible alerts: '+json.dumps(page.get_by_role('alert').all_text_contents()),file=sys.stderr)
                    raise
                finally:
                    for value in contexts:
                        value.close()
                    browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+','[redacted]',str(failure))
            print('FAIL product demo at ' + stage + ': ' + type(failure).__name__ + ' ' + detail[:2200],file=sys.stderr,flush=True)
            print('Recent real API responses: '+json.dumps(observed[-16:]),file=sys.stderr)
            for name in ['api.log','vite.log','worker.log']:
                if (private/name).exists():
                    print(name + ':\n' + (private/name).read_text()[-1700:],file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            stop(processes)
            for stream in streams:
                stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps({'scope':'Actual product Chat/providers/WorkHub and normal backend API with isolated temporary demo accounts/data; no mocked business responses',
        'results':results,'source_hashes':source_hashes,'cleanup':'Own processes, temporary source snapshots, keys, SQLite/files and browser contexts removed',
        'limits':['Native sample installation with explicitly unavailable external integrations; no live provider or Matrix service acceptance.',
                  'This private loopback proof does not attest Railway deployment, public TLS, scalability or physical device behavior.']},indent=2)+'\n')
    print('PASS product demo: ' + str(len(results)) + ' real product/browser groups',flush=True)


if __name__ == '__main__':
    main()
