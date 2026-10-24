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
from ui_navigation import choose_chat_channel, close_channels, open_chat_tool, reveal_message_actions, activate_chat_feature, saved_document_reading, edit_saved_document, reveal_discussion_work, show_channel_introduction

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('PRODUCT_DEMO_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/conversation-flow-20261023/product-demo-browser.json')))
PRIVATE_API_REQUEST_TIMES = []

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


FLOW_NAME = re.compile(r'^(Work in this conversation|Arbete i det här samtalet)$')
CLOSE_FLOW = re.compile(r'^(Close this work|Stäng arbetet)$')


def flow_region(page):
    return page.get_by_role('region',name=FLOW_NAME,exact=True)


def live_conversation(page, region):
    expect(region).to_be_visible()
    expect(page.locator('.fc-chat-message-list')).to_be_visible()
    expect(page.locator('.fc-chat-composer textarea')).to_be_editable()
    assert page.locator('.fc-chat-composer textarea').evaluate('node=>!node.closest("[inert]")')
    assert page.locator('.fc-chat-message-list').evaluate('node=>!node.closest("[inert]")')
    assert region.get_attribute('aria-modal') is None
    expect(region.locator('[role="tablist"], .fc-work-hub-tool-switch')).to_have_count(0)


def open_flow(page, kind='Documents'):
    close_channels(page)
    feature = {'Documents': 'document', 'Tasks': 'task', 'Channel activity': 'activity', 'Workflows': 'workflows', 'Cloud files': 'cloud', 'AI settings': 'copilot'}[kind]
    activate_chat_feature(page, feature)
    region = flow_region(page)
    live_conversation(page,region)
    return region


def organize_documents(panel):
    details = panel.locator('details.fc-document-flow-organize')
    expect(details).to_have_count(1)
    if details.get_attribute('open') is None:
        details.locator(':scope > summary').click()
    return details


def send_actual_message(page, channel, content):
    page.locator('.fc-chat-composer textarea').fill(content)
    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/mobile/offline/channels/' + channel + '/messages') as sent:
        page.get_by_role('button',name='Send message',exact=True).click()
    assert sent.value.ok
    message = sent.value.json()
    expect(page.locator('#message-' + message['id'])).to_contain_text(content)
    return message


def toggle_focus_mode(page):
    page.get_by_role('button', name='Message options', exact=True).click()
    page.get_by_role('menuitemcheckbox', name='Focus mode', exact=True).click()


def room_geometry(page):
    """Record rendered layout without changing the product or its DOM."""
    return page.evaluate("""() => {
      const rect = selector => { const node = document.querySelector(selector); if (!node) return null;
        const box = node.getBoundingClientRect(); return {x:box.x,y:box.y,width:box.width,height:box.height}; };
      const room = document.querySelector('.fc-chat-room-content');
      const message = document.querySelector('.fc-chat-message-content');
      return {viewport:{width:innerWidth,height:innerHeight},conversation:rect('.fc-chat-message-list'),
        material:rect('.fc-chat-shared-material'),photo:rect('.fc-chat-material-preview img'),
        columns:room ? getComputedStyle(room).gridTemplateColumns : null,
        messageFont:message ? getComputedStyle(message).fontSize : null,introduction:rect('.fc-chat-channel-introduction')};
    }""")


def reading_anchor(page):
    return page.locator('.fc-chat-timeline').evaluate("""node => {
      const top = node.getBoundingClientRect().top;
      const visible = [...node.querySelectorAll('.fc-chat-message')].find(message => message.getBoundingClientRect().bottom > top + 1);
      const footer = [...node.querySelectorAll('.fc-chat-material-document[data-document-id] .fc-chat-material-footer button')]
        .find(button => button.getBoundingClientRect().bottom > top + 1 && button.getBoundingClientRect().top < node.getBoundingClientRect().bottom);
      const material = !visible && footer ? {documentId:footer.closest('[data-document-id]').dataset.documentId,
        offset:footer.getBoundingClientRect().top-top} : null;
      return {top:node.scrollTop,id:visible?.id ?? null,offset:visible ? visible.getBoundingClientRect().top-top : null,material};
    }""")


def same_reading_anchor(page, previous):
    try:
        page.wait_for_function("""previous => {
          const node = document.querySelector('.fc-chat-timeline');
          const top = node.getBoundingClientRect().top;
          const visible = [...node.querySelectorAll('.fc-chat-message')].find(message => message.getBoundingClientRect().bottom > top + 1);
          if (previous.material) {
            const card = [...node.querySelectorAll('.fc-chat-material-document[data-document-id]')]
              .find(item => item.dataset.documentId === previous.material.documentId);
            const footer = card?.querySelector('.fc-chat-material-footer button');
            return !!footer && Math.abs(footer.getBoundingClientRect().top-top-previous.material.offset) <= 2;
          }
          return (visible?.id ?? null) === previous.id && Math.abs(node.scrollTop - previous.top) <= 3;
        }""",arg=previous)
    except Exception:
        print('Reading return mismatch: '+json.dumps({'before':previous,'after':reading_anchor(page)}),file=sys.stderr)
        raise
    current = reading_anchor(page)
    if previous.get('material'):
        assert current['material'] and current['material']['documentId'] == previous['material']['documentId'] and abs(current['material']['offset'] - previous['material']['offset']) <= 2, json.dumps({'before':previous,'after':current})
    else:
        assert current['id'] == previous['id'] and abs(current['top'] - previous['top']) <= 3, json.dumps({'before':previous,'after':current})


def click_with_reading_anchor(page, control):
    """Observe the actual trusted click after the browser scrolls/focuses a card."""
    control.evaluate("""element => {
      window.__demoOpeningReading = null;
      element.addEventListener('click', () => {
        const node = document.querySelector('.fc-chat-timeline'), top = node.getBoundingClientRect().top;
        const visible = [...node.querySelectorAll('.fc-chat-message')].find(message => message.getBoundingClientRect().bottom > top + 1);
        const card = element.closest('.fc-chat-material-document[data-document-id]');
        const footer = card?.querySelector('.fc-chat-material-footer button');
        const material = !visible && footer ? {documentId:card.dataset.documentId,offset:footer.getBoundingClientRect().top-top} : null;
        window.__demoOpeningReading = {top:node.scrollTop,id:visible?.id ?? null,offset:visible ? visible.getBoundingClientRect().top-top : null,material};
      }, {capture:true,once:true});
    }""")
    control.click()
    value = page.evaluate('window.__demoOpeningReading')
    assert value is not None
    return value


def newest_above_composer(page, message_id):
    page.wait_for_function("""id => {
      const text = document.querySelector('#message-' + id + ' .fc-chat-message-content');
      const composer = document.querySelector('.fc-chat-composer-area');
      if (!text || !composer) return false;
      return text.getBoundingClientRect().bottom <= composer.getBoundingClientRect().top + 1;
    }""",arg=message_id)


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def request(base, method, path, token=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(data).encode() if data is not None else None
    PRIVATE_API_REQUEST_TIMES.append(time.monotonic())
    with urlopen(Request(base + path, method=method, headers=headers, data=body), timeout=45) as response:
        value = response.read()
        return json.loads(value) if value else None


def downloaded_bytes(base, path, token):
    PRIVATE_API_REQUEST_TIMES.append(time.monotonic())
    with urlopen(Request(base + path, headers={'Authorization':'Bearer ' + token}), timeout=45) as response:
        return response.read()


def rejected_bytes(base, path, token):
    try:
        downloaded_bytes(base,path,token)
    except HTTPError as failure:
        assert failure.code in (401,403,404), failure.code
        return
    raise AssertionError('A different demo visit could read this private image')


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
  if (!['http:','https:'].includes(location.protocol)) return;
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
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    results, processes, streams, visual_geometry = {}, [], [], {}
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
                    value = browser.new_context(viewport={'width':1440,'height':1100}, locale=locale, reduced_motion='reduce')
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
                    tab.on('response', lambda response: observed.append({'method':response.request.method,'path':urlsplit(response.url).path,'status':response.status,'at':time.monotonic()}) if urlsplit(response.url).path.startswith('/api/') else None)
                    tab.on('websocket', lambda socket: sockets.append(socket.url))
                    return tab
                page = context()
                pacing_waits = []
                def passed(name):
                    assert not any(row['status'] == 429 for row in observed), 'The real public API budget was exceeded'
                    # Reserve room for the next ~100-request native flow and
                    # count direct HTTP verification as well as browser reads.
                    # Real production limits remain unchanged.
                    page.wait_for_timeout(6000)
                    started = time.monotonic(); deadline = started + 60
                    while True:
                        cutoff = time.monotonic() - 60
                        recent = sum(row['at'] > cutoff for row in observed) + sum(at > cutoff for at in PRIVATE_API_REQUEST_TIMES)
                        if recent <= 160:
                            break
                        assert time.monotonic() < deadline, 'Private automation did not reach its reserved API headroom'
                        page.wait_for_timeout(1000)
                    pacing_waits.append({'after_group':name,'extra_wait_seconds':round(time.monotonic()-started,2)})
                    assert not any(row['status'] == 429 for row in observed), 'The real public API budget was exceeded'
                    results[name] = True
                    print('PASS group: ' + name,flush=True)
                def bootstrap(tab, action):
                    with tab.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as response:
                        action()
                    assert response.value.ok, 'Actual demo session bootstrap failed'
                    visit = response.value.json(); expect(tab.locator('.fc-chat')).to_be_visible(timeout=90000)
                    expect(tab.locator('.fc-product-demo-banner')).to_be_visible()
                    return visit
                try:
                    stage = 'normal signed demo visit and actual product shell'
                    with page.expect_response(lambda response: response.request.method == 'GET' and re.fullmatch(r'/api/read-receipts/channels/[^/]+',urlsplit(response.url).path) is not None) as opening_receipts:
                        visit = bootstrap(page, lambda: page.goto(app + '/product-demo/?locale=en',wait_until='domcontentloaded'))
                    token, channel = visit['session']['access_token'], visit['channel_id']
                    assert urlsplit(opening_receipts.value.url).path == '/api/read-receipts/channels/' + channel
                    me = request(base,'GET','/auth/me',token); assert me['id'] == visit['session']['user_id']
                    channels = request(base,'GET','/channels?workspace_id=' + visit['workspace_id'],token)
                    selected = next(row for row in channels if row['id'] == channel)
                    expect(page.locator('#chat-channel-name')).to_have_text(selected['name'])
                    messages = request(base,'GET','/messages/channels/' + channel + '?limit=100',token)
                    assert len(channels) >= 2 and len(messages) >= 3 and len({row['author_id'] for row in messages}) >= 2
                    assert not page.locator('.demo-showroom,.demo-document-desk,.demo-inline-assist').count()
                    passed('normal_signed_session_actual_Chat_and_seeded_product_API')

                    stage = 'approved courtyard story and real authenticated reference image'
                    source = request(base,'GET','/messages/' + visit['examples']['message_id'],token)
                    assert source['content'] == 'Shall we open up to the courtyard?'
                    # The actual visit has no receipt for its visitor. Its first
                    # incoming root is independently known from the native seed,
                    # rather than from the frontend entry-policy implementation.
                    initial_receipts = opening_receipts.value.json()
                    assert not any(row['user_id'] == visit['session']['user_id'] for row in initial_receipts)
                    roots = sorted((row for row in messages if not row.get('parent_id') and not row.get('deleted')),key=lambda row:row['created_at'])
                    assert next(row for row in roots if row['author_id'] != visit['session']['user_id'])['id'] == source['id']
                    expect(page.locator('#message-' + source['id'])).to_be_in_viewport(ratio=.7)
                    expect(page.locator('.fc-chat-message.is-lead')).to_have_count(0)
                    expect(page.get_by_role('region',name='Channel introduction',exact=True)).to_have_count(0)
                    visual_geometry['initial_entry'] = {'kind':'first-unread','message_id':source['id'],'receipt_user_ids':[row['user_id'] for row in initial_receipts],'anchor':reading_anchor(page)}
                    introduction = show_channel_introduction(page)
                    expect(introduction.get_by_role('heading',level=1)).to_have_text(selected['name'])
                    seeded_files = request(base,'GET','/files/channels/' + channel,token)
                    courtyard = next(row for row in seeded_files if row['filename'] == 'courtyard.png')
                    reference_photo = (ROOT / 'frontend/src/assets/open-studio/courtyard.png').read_bytes()
                    assert downloaded_bytes(base,'/files/' + courtyard['id'],token) == reference_photo
                    photo = page.locator('.fc-chat-material-preview img'); expect(photo).to_be_visible(timeout=45000)
                    expect(photo).to_have_js_property('complete',True); assert photo.evaluate('node=>node.naturalWidth > 0')
                    visual_geometry['opening_desktop'] = room_geometry(page)
                    visual_geometry['initial_reading_anchor'] = reading_anchor(page)
                    geometry = visual_geometry['opening_desktop']
                    assert geometry['conversation']['x'] < geometry['material']['x'] and geometry['material']['width'] > 320
                    assert geometry['photo']['width'] > geometry['photo']['height'], 'The courtyard photo must retain its landscape composition'
                    passed('approved_courtyard_story_and_actual_native_reference_photo')

                    stage = 'approved portraits are real private native profile images'
                    avatar_path = '/users/' + source['author_id'] + '/avatar?workspace_id=' + visit['workspace_id']
                    assert downloaded_bytes(base,avatar_path,token).startswith(b'\x89PNG\r\n\x1a\n')
                    avatar = page.locator('#message-' + source['id'] + ' .fc-profile-avatar img')
                    expect(avatar).to_be_visible(); expect(avatar).to_have_js_property('complete',True)
                    assert avatar.evaluate('node=>node.naturalWidth > 0 && node.src.startsWith("blob:")')
                    passed('approved_profile_portraits_use_authenticated_native_private_images')

                    stage = 'first unread entry and explicit native history and newest navigation'
                    assert visual_geometry['initial_entry']['message_id'] == source['id'] and visual_geometry['initial_entry']['kind'] == 'first-unread'
                    # Give the compact conversation genuine history to navigate.
                    # Each setup update is a normal native UI send, never a DOM
                    # height, synthetic scroll position or fabricated API reply.
                    for index in range(16):
                        if page.locator('.fc-chat-timeline').evaluate('node=>node.scrollHeight-node.clientHeight>600'):
                            break
                        send_actual_message(page,channel,'A working studio update ' + str(index + 1) + '. Keeping materials, people and decisions close to the conversation.')
                    assert page.locator('.fc-chat-timeline').evaluate('node=>node.scrollHeight-node.clientHeight>600')
                    history_start = page.locator('#message-' + source['id']).get_by_role('button',name='More message actions',exact=True)
                    history_start.focus(); history_start.press('Control+Home')
                    page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollTop === 0")
                    expect(page.locator('#message-' + source['id'])).to_be_in_viewport(ratio=.7)
                    stage = 'explicit newest navigation from an ordinary earlier message'
                    page.get_by_role('button',name='Jump to latest messages',exact=True).click()
                    newest_seed = max(request(base,'GET','/messages/channels/' + channel + '?limit=100',token),key=lambda row:row['created_at'])
                    expect(page.locator('#message-' + newest_seed['id'])).to_be_in_viewport(ratio=.7)
                    stage = 'native keyboard returns from newest to earlier conversation history'
                    history_start.focus(); history_start.press('Control+Home')
                    page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollTop === 0")
                    expect(page.locator('#message-' + source['id'])).to_be_in_viewport(ratio=.7)
                    passed('room_overview_and_explicit_newest_navigation_are_both_usable')

                    stage = 'keyboard reading survives workspace-tool return and native material refresh'
                    page.locator('#message-' + source['id']).get_by_role('button',name='More message actions',exact=True).focus()
                    page.keyboard.press('PageDown')
                    page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollTop > 60")
                    page.wait_for_timeout(600)  # Chromium's native keyboard-scroll animation.
                    keyboard_anchor = reading_anchor(page)
                    activate_chat_feature(page,'task',keyboard=True)
                    keyboard_tools = flow_region(page)
                    expect(keyboard_tools.locator('.fc-project-flow-tasks')).to_be_visible()
                    live_conversation(page,keyboard_tools)
                    update_path = '/api/channels/' + channel + '/shared-documents/' + visit['examples']['document_ids'][0] + '/updates'
                    task_path = '/api/channels/' + channel + '/projects/' + visit['examples']['project_id'] + '/tasks'
                    with page.expect_response(lambda response: response.request.method == 'GET' and urlsplit(response.url).path == update_path) as refreshed_page, page.expect_response(lambda response: response.request.method == 'GET' and urlsplit(response.url).path == task_path) as refreshed_tasks:
                        page.keyboard.press('Escape')
                    assert refreshed_page.value.ok and refreshed_tasks.value.ok
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    expect(keyboard_tools).to_have_count(0); same_reading_anchor(page,keyboard_anchor)
                    history_start.focus(); history_start.press('Control+Home')
                    page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollTop === 0")
                    passed('keyboard_reading_anchor_survives_workspace_return_and_real_material_API_refresh')

                    stage = 'inline native brief and stack open the real document tools and return in place'
                    seeded_documents = request(base,'GET','/channels/' + channel + '/shared-documents',token)
                    brief = next(row for row in seeded_documents if row['id'] == visit['examples']['document_ids'][0])
                    brief_card = page.locator('.fc-chat-material-document[data-document-id="' + brief['id'] + '"]')
                    expect(brief_card.get_by_role('heading',name=brief['title'],exact=True)).to_be_visible(timeout=45000)
                    stack_data = request(base,'GET','/channels/' + channel + '/shared-document-organization',token)
                    seed_stack = next(row for row in stack_data['stacks'] if row['id'] == visit['examples']['stack_id'])
                    expect(brief_card.locator('.fc-chat-material-stack')).to_contain_text(seed_stack['title'])
                    composer = page.locator('.fc-chat-composer textarea'); composer.fill('A draft that stays with this conversation.')
                    brief_card.get_by_role('button',name='Open document',exact=True).scroll_into_view_if_needed()
                    anchor = click_with_reading_anchor(page,brief_card.get_by_role('button',name='Open document',exact=True))
                    inline_tools = flow_region(page)
                    edit_saved_document(inline_tools, title=brief['title'])
                    expect(inline_tools.get_by_label('Title',exact=True)).to_have_value(brief['title'])
                    expect(inline_tools.get_by_label('Document text',exact=True)).to_be_editable()
                    live_conversation(page,inline_tools)
                    page.screenshot(path=str(REPORT.with_name('flow-document-en-1440.png')))
                    inline_tools.locator('.fc-conversation-work-header').get_by_role('button',name=CLOSE_FLOW,exact=True).click()
                    expect(inline_tools).to_have_count(0); expect(composer).to_have_value('A draft that stays with this conversation.'); same_reading_anchor(page,anchor)
                    brief_card.locator('.fc-chat-material-stack').scroll_into_view_if_needed()
                    anchor = click_with_reading_anchor(page,brief_card.locator('.fc-chat-material-stack'))
                    inline_tools = flow_region(page)
                    expect(inline_tools.locator('h4[data-document-stack="' + seed_stack['id'] + '"]')).to_be_visible()
                    inline_tools.locator('.fc-conversation-work-header').get_by_role('button',name=CLOSE_FLOW,exact=True).click()
                    expect(inline_tools).to_have_count(0); same_reading_anchor(page,anchor)
                    passed('inline_native_brief_and_stack_return_with_draft_and_reading_anchor')

                    stage = 'inline real next step opens its exact native task and preserves the conversation'
                    next_step = page.locator('.fc-chat-material-next-step'); expect(next_step).to_be_visible()
                    task_id = next_step.get_attribute('data-material-task-id')
                    seed_tasks = request(base,'GET','/channels/' + channel + '/projects/' + visit['examples']['project_id'] + '/tasks',token)['items']
                    seed_task = next(row for row in seed_tasks if row['id'] == task_id)
                    expect(next_step).to_contain_text(seed_task['fields']['title'])
                    next_step.scroll_into_view_if_needed(); anchor = click_with_reading_anchor(page,next_step)
                    inline_tools = flow_region(page)
                    focused_task = inline_tools.locator('.fc-project-card[data-task-id="' + task_id + '"]')
                    expect(focused_task).to_be_focused(); expect(focused_task).to_contain_text(seed_task['fields']['title'])
                    live_conversation(page,inline_tools)
                    focused_task.locator('input[type="checkbox"]').click()
                    expect(focused_task.locator('input[type="checkbox"]')).to_be_checked()
                    focused_task.get_by_label('Move task ' + seed_task['fields']['title'],exact=True).select_option('done')
                    expect(focused_task.get_by_label('Move task ' + seed_task['fields']['title'],exact=True)).to_have_value('done')
                    composer.fill('I can keep talking while this task stays open.')
                    expect(focused_task).to_be_visible(); expect(composer).to_have_value('I can keep talking while this task stays open.')
                    updated_task = next(row for row in request(base,'GET','/channels/' + channel + '/projects/' + visit['examples']['project_id'] + '/tasks',token)['items'] if row['id'] == task_id)
                    assert updated_task['fields']['status'] == 'done' and updated_task['fields']['checklist'][0]['done']
                    passed('native_task_checklist_and_status_write_while_conversation_remains_enabled')
                    composer.fill('A draft that stays with this conversation.')
                    inline_tools.locator('.fc-conversation-work-header').get_by_role('button',name=CLOSE_FLOW,exact=True).click()
                    expect(inline_tools).to_have_count(0); expect(composer).to_have_value('A draft that stays with this conversation.'); same_reading_anchor(page,anchor)
                    passed('inline_next_step_opens_exact_native_task_and_returns_in_place')

                    stage = 'inline authentic file opens normal preview and returns to its reading position'
                    photo_button = page.get_by_role('button',name='Open courtyard.png',exact=True)
                    photo_button.scroll_into_view_if_needed(); anchor = click_with_reading_anchor(page,photo_button)
                    preview = page.get_by_role('dialog',name='Preview courtyard.png',exact=True)
                    expect(preview.get_by_role('img',name='courtyard.png',exact=True)).to_be_visible()
                    preview.get_by_role('button',name='Close file preview',exact=True).click()
                    expect(preview).to_have_count(0); expect(composer).to_have_value('A draft that stays with this conversation.'); same_reading_anchor(page,anchor)
                    passed('inline_authenticated_courtyard_file_uses_normal_preview_and_return')

                    page.locator('.fc-chat-timeline').hover(); page.mouse.wheel(0,-10000)
                    page.wait_for_function("document.querySelector('.fc-chat-timeline').scrollTop === 0")
                    page.evaluate('document.fonts.ready'); page.mouse.move(5,5)
                    REPORT.parent.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('approved-product-en-1440.png')))
                    page.locator('.fc-chat').screenshot(path=str(REPORT.with_name('approved-product-component.png')))
                    visual_geometry['opening_capture'] = {'action':'Ordinary scroll to the first conversation message; no content or layout changes','geometry':room_geometry(page)}

                    stage = 'material document source reveal clears its target before ordinary message save'
                    brief_card.get_by_role('button',name='Open document',exact=True).click()
                    inline_tools = flow_region(page)
                    edit_saved_document(inline_tools, title=brief['title'])
                    expect(inline_tools.get_by_label('Title',exact=True)).to_have_value(brief['title'])
                    stack_context = inline_tools.locator('details[data-document-stack="' + seed_stack['id'] + '"]')
                    stack_context.locator(':scope > summary').click()
                    stack_context.get_by_role('button',name='Open source message',exact=True).click()
                    expect(inline_tools).to_be_visible()
                    expect(inline_tools.get_by_label('Document text',exact=True)).to_be_editable()
                    source_article = page.locator('#message-' + source['id']); expect(source_article).to_be_focused()
                    reveal_message_actions(source_article); source_article.get_by_role('button',name='Save in document stack',exact=True).click()
                    inline_tools = flow_region(page)
                    expect(inline_tools.get_by_role('region',name='Save message in a document stack',exact=True)).to_be_visible()
                    expect(inline_tools.get_by_label('Document text',exact=True)).to_have_count(0)
                    inline_tools.locator('.fc-conversation-work-header').get_by_role('button',name=CLOSE_FLOW,exact=True).click()
                    expect(composer).to_have_value('A draft that stays with this conversation.')
                    action_toggle = source_article.get_by_role('button',name='More message actions',exact=True)
                    if action_toggle.get_attribute('aria-expanded') == 'true': action_toggle.click()
                    passed('material_source_reveal_does_not_leak_old_editor_target_into_message_save')

                    stage = 'same channel switching and channel-local composer drafts'
                    draft = 'Keep my real product draft.'; composer = page.locator('.fc-chat-composer textarea')
                    composer.fill(draft); other = next(row for row in channels if row['id'] != channel)
                    choose_chat_channel(page,other['name']); expect(page.locator('#chat-channel-name')).to_have_text(other['name']); expect(composer).to_have_value('')
                    choose_chat_channel(page,selected['name']); expect(composer).to_have_value(draft)
                    passed('same_product_channel_switch_and_retained_drafts')

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
                    passed('normal_message_edit_reaction_and_thread_writes')

                    stage = 'room and live material remain stable beyond twelve messages with newest usable'
                    before_growth = room_geometry(page)
                    latest_message = message
                    for index in range(max(0,13-page.locator('.fc-chat-message').count())):
                        composer.fill('Another small courtyard update ' + str(index + 1) + '.')
                        with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/mobile/offline/channels/' + channel + '/messages') as delivered:
                            page.get_by_role('button',name='Send message',exact=True).click()
                        assert delivered.value.ok
                        latest_message = delivered.value.json()
                        expect(page.locator('#message-' + latest_message['id'])).to_be_visible()
                    assert page.locator('.fc-chat-message').count() >= 13
                    expect(page.locator('#message-' + latest_message['id'])).to_be_in_viewport(ratio=.7)
                    expect(page.locator('.fc-chat-shared-material')).to_have_count(1)
                    expect(page.locator('.fc-chat-material-document')).to_have_count(1)
                    expect(page.locator('.fc-chat-material-next-step')).to_have_count(1)
                    expect(page.locator('.fc-chat-message.is-lead')).to_have_count(0)
                    ordinary_sizes = page.locator('#message-' + source['id'] + ' .fc-chat-message-content, #message-' + latest_message['id'] + ' .fc-chat-message-content').evaluate_all('nodes=>nodes.map(node=>parseFloat(getComputedStyle(node).fontSize))')
                    assert len(ordinary_sizes) == 2 and abs(ordinary_sizes[0] - ordinary_sizes[1]) < .01, 'Ordinary history and the newest message use the same readable message typography'
                    after_growth = room_geometry(page); visual_geometry['after_thirteen_messages'] = after_growth
                    assert abs(after_growth['material']['width']-before_growth['material']['width']) <= 3
                    assert after_growth['conversation']['x'] < after_growth['material']['x']
                    passed('room_material_and_readable_newest_message_survive_more_than_twelve_messages')

                    stage = 'multiline composer arrangements and focus mode keep newest text unobscured'
                    newest_above_composer(page,latest_message['id'])
                    baseline_composer = page.locator('.fc-chat-composer-area').bounding_box()
                    baseline_height = baseline_composer['height']
                    multiline = '\n'.join('A line for the shared courtyard draft ' + str(index + 1) for index in range(8))
                    composer.fill(multiline)
                    page.wait_for_function('height=>document.querySelector(".fc-chat-composer-area").getBoundingClientRect().height > height',arg=baseline_height)
                    newest_above_composer(page,latest_message['id'])
                    open_chat_tool(page,'Shared material on the left')
                    page.get_by_role('button',name='More channel tools',exact=True).click()
                    left_geometry = room_geometry(page); left_composer = page.locator('.fc-chat-composer-area').bounding_box()
                    assert left_geometry['material']['x'] < left_geometry['conversation']['x']
                    center = left_composer['x'] + left_composer['width']/2
                    assert left_geometry['conversation']['x'] <= center <= left_geometry['conversation']['x'] + left_geometry['conversation']['width']
                    newest_above_composer(page,latest_message['id'])
                    open_chat_tool(page,'Shared material on the right')
                    page.get_by_role('button',name='More channel tools',exact=True).click()
                    toggle_focus_mode(page)
                    expect(page.locator('.fc-chat')).to_have_class(re.compile(r'\bis-focus-mode\b'))
                    focused_composer = page.locator('.fc-chat-composer-area').bounding_box()
                    assert focused_composer['width'] > baseline_composer['width'] + 80
                    assert abs(focused_composer['x'] + focused_composer['width']/2 - 720) <= 3
                    expect(page.locator('.fc-chat-shared-material')).to_be_hidden()
                    expect(composer).to_have_value(multiline); newest_above_composer(page,latest_message['id'])
                    toggle_focus_mode(page)
                    expect(page.locator('.fc-chat-shared-material')).to_be_visible()
                    visual_geometry['multiline_arrangement'] = {'left':left_geometry,'left_composer':left_composer,'restored':room_geometry(page)}
                    composer.fill('')
                    passed('multiline_composer_left_right_and_focus_keep_newest_content_visible')

                    stage = 'actual channel details CAS save'
                    open_chat_tool(page,'Channel details'); details = page.get_by_role('dialog',name='Manage channel',exact=True)
                    details.get_by_label('Channel name',exact=True).fill('Demo studio reviewed'); details.get_by_label('Channel description',exact=True).fill('Saved with the product channel metadata API.'); details.get_by_role('button',name='Save channel',exact=True).click()
                    expect(details).to_have_count(0); expect(page.locator('#chat-channel-name')).to_have_text('Demo studio reviewed')
                    assert request(base,'GET','/channels/' + channel + '/metadata',token)['description'] == 'Saved with the product channel metadata API.'
                    selected = next(row for row in request(base,'GET','/channels?workspace_id=' + visit['workspace_id'],token) if row['id'] == channel)
                    passed('normal_channel_metadata_save_and_header_update')

                    def work(tab='Documents'):
                        return open_flow(page,tab)
                    stage = 'actual sample documents and Yjs editing'
                    panel = work(); seeded_docs = request(base,'GET','/channels/' + channel + '/shared-documents',token); assert len(seeded_docs) >= 2
                    panel.get_by_role('button',name='New shared document',exact=True).click(); panel.get_by_label('Document title',exact=True).fill('Product demo verification page'); panel.get_by_role('button',name='Create document',exact=True).click()
                    editor = panel.get_by_label('Document text',exact=True); expect(editor).to_be_editable(); editor.fill('Real Yjs sample edits, not a parallel Markdown mock.'); expect(panel.get_by_text('All edits synchronized',exact=True)).to_be_visible()
                    document = next(row for row in request(base,'GET','/channels/' + channel + '/shared-documents',token) if row['title'] == 'Product demo verification page')
                    panel.get_by_label('Add a comment',exact=True).fill('A comment on the actual shared page.'); panel.get_by_role('button',name='Post comment',exact=True).click(); expect(panel.get_by_text('A comment on the actual shared page.',exact=True)).to_be_visible()
                    passed('normal_Yjs_document_editor_and_comment_write')

                    stage = 'real Yjs document and real conversation remain live at the same time'
                    editor.evaluate('node=>{window.__simultaneousEditor=node}')
                    simultaneous_body = 'Real Yjs sample edits, not a parallel Markdown mock.\nThe conversation stays open while we write.'
                    editor.fill(simultaneous_body)
                    sent_in_document = send_actual_message(page,channel,'A message sent while the shared document stays open.')
                    expect(editor).to_have_value(simultaneous_body)
                    assert editor.evaluate('node=>node===window.__simultaneousEditor'), 'Sending remounted the real Yjs editor'
                    expect(panel.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
                    live_conversation(page,panel)
                    expect(page.locator('#message-' + sent_in_document['id'])).to_be_visible()
                    page.screenshot(path=str(REPORT.with_name('flow-simultaneous-document-en-1440.png')))
                    passed('real_Yjs_editor_and_message_send_are_simultaneous_without_editor_remount')

                    stage = 'actual document stack move and guarded Undo'
                    panel.get_by_role('button',name='Documents',exact=True).click()
                    management = organize_documents(panel)
                    management.get_by_role('button',name='New stack',exact=True).click(); management.get_by_label('Stack name',exact=True).fill('Reviewed examples'); management.get_by_role('button',name='Create stack',exact=True).click()
                    expect(management.get_by_role('region',name='Reviewed examples',exact=True)).to_be_visible()
                    organization = '/channels/' + channel + '/shared-document-organization'
                    stack = next(row for row in request(base,'GET',organization,token)['stacks'] if row['title'] == 'Reviewed examples')
                    management.get_by_label('Move Product demo verification page to stack',exact=True).select_option(stack['id']); region = management.get_by_role('region',name='Reviewed examples',exact=True); expect(region.get_by_role('button',name=re.compile('^Product demo verification page'))).to_be_visible()
                    management.get_by_role('button',name='Undo move',exact=True).click(); expect(management.get_by_label('Move Product demo verification page to stack',exact=True)).to_have_value('')
                    management.get_by_label('Move Product demo verification page to stack',exact=True).select_option(stack['id']); expect(region.get_by_text('A comment on the actual shared page.',exact=True)).to_be_visible()
                    region.get_by_role('button',name='Comment in Product demo verification page',exact=True).click(); edit_saved_document(panel,title='Product demo verification page',text=simultaneous_body); expect(panel.get_by_label('Document text',exact=True)).to_have_value(simultaneous_body)
                    passed('normal_stack_placement_Undo_and_derived_discussion_source')
                    panel.get_by_role('button',name='Close this work',exact=True).click()

                    stage = 'message reference and return to original with composer intact'
                    composer.fill('Unsent original conversation draft.'); reveal_message_actions(article); article.get_by_role('button',name='Save in document stack',exact=True).click(); panel = flow_region(page)
                    review = panel.get_by_role('region',name='Save message in a document stack',exact=True); expect(review.locator('blockquote')).to_have_text(text + ' Revised.'); review.get_by_label('Destination stack',exact=True).select_option(stack['id']); review.get_by_role('button',name='Save message reference',exact=True).click()
                    management = organize_documents(panel)
                    saved_region = management.get_by_role('region',name='Reviewed examples',exact=True); saved_region.get_by_role('button',name='Open source message',exact=True).click(); expect(panel).to_be_visible(); expect(article).to_be_focused(); expect(composer).to_have_value('Unsent original conversation draft.')
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    passed('normal_stack_message_reference_reveals_exact_original')

                    stage = 'actual project task and checked state'
                    held_boards = []
                    hold_boards = True
                    def delay_initial_boards(route):
                        if hold_boards and route.request.method == 'GET' and urlsplit(route.request.url).path == '/api/channels/' + channel + '/projects': held_boards.append(route)
                        else: route.continue_()
                    page.route('**/api/channels/' + channel + '/projects*',delay_initial_boards)
                    panel = work('Tasks'); expect(panel.get_by_role('button',name='New project',exact=True)).to_be_disabled()
                    for _ in range(100):
                        if held_boards: break
                        page.wait_for_timeout(25)
                    assert held_boards
                    # The real room overview may refresh the same endpoint too.
                    # Release every held real request; never fabricate a reply.
                    # Keep interception installed until those pending requests
                    # are continued: removing it can resume them in the driver.
                    hold_boards = False
                    while held_boards: held_boards.pop(0).continue_()
                    page.unroute('**/api/channels/' + channel + '/projects*',delay_initial_boards)
                    panel.get_by_role('button',name='New project',exact=True).click(); form = panel.get_by_role('form',name='New project',exact=True)
                    form.get_by_label('Project title',exact=True).fill('Demo verification'); form.get_by_label('Description',exact=True).fill('Real product task workflow.'); form.get_by_role('button',name='Save project',exact=True).click()
                    panel.get_by_role('button',name='New task',exact=True).click(); form = panel.get_by_role('form',name='New task',exact=True); form.get_by_label('Task title',exact=True).fill('Review the product demo'); form.get_by_label('Description',exact=True).fill('Use the same task model.'); form.get_by_role('button',name='Add checklist item',exact=True).click(); form.get_by_label('Checklist item 1',exact=True).fill('Confirm UI parity'); form.get_by_role('button',name='Save task',exact=True).click()
                    card = panel.locator('.fc-project-card').filter(has=page.get_by_role('heading',name='Review the product demo',exact=True)); card.get_by_label('Confirm UI parity',exact=True).click(); expect(card.get_by_label('Confirm UI parity',exact=True)).to_be_checked(); card.get_by_label('Move task Review the product demo',exact=True).select_option('done'); expect(card.get_by_label('Move task Review the product demo',exact=True)).to_have_value('done')
                    passed('normal_project_task_checklist_and_status_writes')
                    panel.get_by_role('button',name='Close this work',exact=True).click()

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
                    assert any(row.get('fields',{}).get('title') == 'Bring material samples to the walkthrough' for row in generated)
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    passed('normal_workflow_Run_Approve_and_real_background_task_creation')

                    stage = 'normal file upload and exact download'
                    open_chat_tool(page,'Channel files'); files = page.get_by_role('dialog',name='Channel files',exact=True); content = b'Real demo file upload through the normal product endpoint.\n'
                    files.get_by_label('Choose a file',exact=True).set_input_files({'name':'Product demo sample.txt','mimeType':'text/plain','buffer':content})
                    expect(files.get_by_role('button',name='Download Product demo sample.txt',exact=True)).to_be_visible()
                    with page.expect_download() as downloaded:
                        files.get_by_role('button',name='Download Product demo sample.txt',exact=True).click()
                    assert Path(downloaded.value.path()).read_bytes() == content; files.get_by_role('button',name='Close channel panel',exact=True).click()
                    passed('normal_XHR_file_upload_and_exact_authenticated_download')

                    stage = 'message becomes an actual shared document with a persisted native routing reference'
                    source_article = page.locator('#message-' + message['id'])
                    reveal_message_actions(source_article); source_article.get_by_role('button',name='Continue in a document',exact=True).click()
                    panel = flow_region(page); live_conversation(page,panel)
                    expect(panel.get_by_label('Document text',exact=True)).to_have_value(text + ' Revised.')
                    panel.get_by_label('Document title',exact=True).fill('A document from our conversation')
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel + '/conversation-work') as linked_doc:
                        panel.get_by_role('button',name='Create document',exact=True).click()
                    assert linked_doc.value.ok; doc_reference = linked_doc.value.json()
                    assert doc_reference['source_message_id'] == message['id'] and doc_reference['resource_kind'] == 'document'
                    source_editor = panel.get_by_label('Document text',exact=True); expect(source_editor).to_have_value(text + ' Revised.')
                    source_editor.evaluate('node=>{window.__sourceEditor=node}')
                    panel.get_by_role('button',name='Show source message',exact=True).click()
                    expect(source_article).to_be_focused(); expect(panel).to_be_visible(); assert source_editor.evaluate('node=>node===window.__sourceEditor')
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    reveal_discussion_work(source_article)
                    doc_card = source_article.locator('[data-work-reference="' + doc_reference['id'] + '"]')
                    expect(doc_card).to_contain_text('A document from our conversation')
                    choose_chat_channel(page,other['name']); choose_chat_channel(page,selected['name'])
                    reveal_discussion_work(source_article)
                    expect(doc_card).to_contain_text('A document from our conversation'); doc_card.click()
                    panel = flow_region(page); edit_saved_document(panel,title='A document from our conversation',text=text + ' Revised.'); expect(panel.get_by_label('Document text',exact=True)).to_have_value(text + ' Revised.')
                    index_rows = request(base,'GET','/channels/' + channel + '/conversation-work?source_message_id=' + message['id'],token)['items']
                    assert any(row['id'] == doc_reference['id'] and row['resource_id'] == doc_reference['resource_id'] for row in index_rows)
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    passed('message_created_document_uses_real_persisted_index_and_reopens_exact_source_work')

                    stage = 'message becomes an actual task with a persisted native routing reference'
                    reveal_message_actions(source_article); source_article.get_by_role('button',name='Make a next step',exact=True).click()
                    panel = flow_region(page); panel.get_by_role('button',name='New task',exact=True).click()
                    task_form = panel.get_by_role('form',name='New task',exact=True)
                    expect(task_form.get_by_label('Description',exact=True)).to_have_value(text + ' Revised.')
                    task_form.get_by_label('Task title',exact=True).fill('A next step from our conversation')
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel + '/conversation-work') as linked_task:
                        task_form.get_by_role('button',name='Save task',exact=True).click()
                    assert linked_task.value.ok; task_reference = linked_task.value.json()
                    assert task_reference['source_message_id'] == message['id'] and task_reference['resource_kind'] == 'task'
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    reveal_discussion_work(source_article)
                    task_card = source_article.locator('[data-work-reference="' + task_reference['id'] + '"]')
                    expect(task_card).to_contain_text('A next step from our conversation')
                    choose_chat_channel(page,other['name']); choose_chat_channel(page,selected['name']); reveal_discussion_work(source_article); task_card.click()
                    panel = flow_region(page); expect(panel.locator('.fc-project-card[data-task-id="' + task_reference['resource_id'] + '"]')).to_contain_text('A next step from our conversation')
                    assert any(row['id'] == task_reference['id'] for row in request(base,'GET','/channels/' + channel + '/conversation-work?source_message_id=' + message['id'],token)['items'])
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    passed('message_created_task_uses_real_persisted_index_and_reopens_exact_native_card')

                    stage = 'source decision uses canonical native provenance and persists its conversation card'
                    reveal_message_actions(source_article); source_article.get_by_role('button',name='Record decision from message',exact=True).click()
                    decisions_flow = page.get_by_role('region',name='Decisions in this conversation',exact=True)
                    live_conversation(page,decisions_flow)
                    decisions_flow.get_by_label('Title',exact=True).fill('A decision from our conversation')
                    expect(decisions_flow.get_by_label('Decision and context',exact=True)).to_have_value(text + ' Revised.')
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel + '/decisions') as recorded_decision:
                        decisions_flow.get_by_role('button',name='Record decision',exact=True).click()
                    assert recorded_decision.value.ok; source_decision = recorded_decision.value.json()
                    assert source_decision['source_message_id'] == message['id']
                    decisions_flow.get_by_role('button',name='Source',exact=True).click(); expect(decisions_flow).to_be_visible(); expect(source_article).to_be_focused()
                    page.get_by_role('button',name='Close this work',exact=True).click()
                    reveal_discussion_work(source_article)
                    decision_card = source_article.locator('.fc-chat-linked-work button').filter(has=page.get_by_text('A decision from our conversation',exact=True))
                    expect(decision_card).to_be_visible(); decision_card.click()
                    decisions_flow = page.get_by_role('region',name='Decisions in this conversation',exact=True)
                    expect(decisions_flow.locator('#decision-' + source_decision['id'])).to_be_visible()
                    canonical_decisions = request(base,'GET','/channels/' + channel + '/decisions',token)
                    assert any(row['id'] == source_decision['id'] and row['source_message_id'] == message['id'] for row in canonical_decisions)
                    page.get_by_role('button',name='Close this work',exact=True).click()
                    passed('source_decision_persists_canonical_provenance_and_native_conversation_card')

                    stage = 'dirty native task refuses target and channel changes until explicit accepted navigation'
                    task_card.click(); panel = flow_region(page); panel.get_by_role('button',name='Edit',exact=True).click()
                    dirty_form = panel.get_by_role('form',name='Edit task',exact=True); dirty_form.get_by_label('Description',exact=True).fill('An unsaved task draft to keep.')
                    dirty_form.evaluate('node=>{window.__guardedTaskEditor=node}')
                    page.once('dialog',lambda dialog:dialog.dismiss())
                    activate_chat_feature(page,'document',expect_closed=False)
                    expect(dirty_form.get_by_label('Description',exact=True)).to_have_value('An unsaved task draft to keep.'); assert dirty_form.evaluate('node=>node===window.__guardedTaskEditor')
                    expect(page.locator('.fc-slash-commands')).to_be_visible(); page.locator('.fc-slash-commands').get_by_role('combobox').press('Escape')
                    page.once('dialog',lambda dialog:dialog.dismiss()); choose_chat_channel(page,other['name']); close_channels(page)
                    expect(page.locator('#chat-channel-name')).to_have_text(selected['name']); expect(dirty_form.get_by_label('Description',exact=True)).to_have_value('An unsaved task draft to keep.')
                    live_conversation(page,panel)
                    page.once('dialog',lambda dialog:dialog.accept()); choose_chat_channel(page,other['name'])
                    expect(page.locator('#chat-channel-name')).to_have_text(other['name']); expect(flow_region(page)).to_have_count(0)
                    choose_chat_channel(page,selected['name']); reveal_discussion_work(source_article); task_card.click(); panel = flow_region(page)
                    expect(panel.locator('.fc-project-card[data-task-id="' + task_reference['resource_id'] + '"]')).to_contain_text(text + ' Revised.')
                    assert panel.get_by_role('form',name='Edit task',exact=True).count() == 0
                    panel.get_by_role('button',name='Close this work',exact=True).click()
                    passed('dirty_task_target_and_channel_navigation_decline_preserves_editor_accept_leaves_without_publishing')

                    stage = 'real saved appearance and truthful unconfigured AI'
                    open_chat_tool(page,'Appearance settings'); appearance = page.get_by_role('dialog',name='Make space your own.',exact=True); appearance.get_by_role('radio',name='Dark',exact=True).check(); appearance.get_by_role('button',name='Save changes',exact=True).click(); expect(appearance).to_have_count(0)
                    assert request(base,'GET','/preferences/appearance',token)['mode'] == 'dark'
                    page.get_by_role('button',name='Channel menu',exact=True).click()
                    page.get_by_role('menuitem',name='Recap this channel',exact=True).click()
                    ai = page.get_by_role('region',name=re.compile(r'^AI help for Loaded conversation in #'))
                    expect(ai).to_be_visible()
                    expect(ai).to_have_class(re.compile(r'\bfc-contextual-copilot-card\b'))
                    expect(ai.get_by_role('heading',name='Recap this channel',exact=True)).to_be_visible()
                    expect(ai).to_contain_text(re.compile('not enabled|not configured|disabled|must enable and connect',re.I))
                    expect(ai.get_by_role('button',name='Send to provider',exact=True)).to_have_count(0)
                    ai.get_by_role('button',name='Close AI help',exact=True).click()
                    passed('normal_appearance_save_and_truthful_external_AI_boundary')

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
                    passed('actual_Microsoft_and_Google_guides_truthfully_disabled_without_authorization')

                    stage = 'separate visit cannot read first visitor content'
                    peer = context(); peer_visit = bootstrap(peer,lambda: peer.goto(app + '/product-demo/?locale=en',wait_until='domcontentloaded'))
                    assert peer_visit['workspace_id'] != visit['workspace_id'] and peer_visit['session']['user_id'] != visit['session']['user_id']
                    rejected(base,'/messages/' + message['id'],peer_visit['session']['access_token']); rejected(base,'/channels/' + channel + '/shared-documents',peer_visit['session']['access_token'])
                    rejected_bytes(base,avatar_path,peer_visit['session']['access_token'])
                    assert peer.locator('.fc-chat-timeline').get_by_text(text + ' Revised.',exact=True).count() == 0
                    storage_untouched(peer)
                    passed('normal_auth_isolates_independent_sample_visitors')

                    stage = 'reset creates new isolated visit without changing stored real credentials'
                    page.once('dialog',lambda dialog:dialog.accept())
                    reset = bootstrap(page,lambda:page.get_by_role('button',name='Reset examples',exact=True).click())
                    assert reset['workspace_id'] != visit['workspace_id'] and reset['session']['user_id'] != visit['session']['user_id']
                    rejected(base,'/messages/' + message['id'],reset['session']['access_token']); expect(page.locator('.fc-chat-composer textarea')).to_have_value('')
                    rejected_bytes(base,avatar_path,reset['session']['access_token'])
                    storage_untouched(page); passed('explicit_reset_replaces_only_the_temporary_sample_session')

                    stage = 'Swedish320px same product controls and literal sample content'
                    swedish = context('sv-SE'); sv_visit = bootstrap(swedish,lambda:swedish.goto(app + '/product-demo/?locale=sv',wait_until='domcontentloaded')); swedish.set_viewport_size({'width':320,'height':850})
                    expect(swedish.get_by_role('button',name='Skicka meddelande',exact=True)).to_be_visible(); assert swedish.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    sv_documents = request(base,'GET','/channels/' + sv_visit['channel_id'] + '/shared-documents',sv_visit['session']['access_token'])
                    sv_seed = next(row for row in sv_documents if row['id'] == sv_visit['examples']['document_ids'][0])
                    show_channel_introduction(swedish)
                    sv_card = swedish.locator('.fc-chat-material-document[data-document-id="' + sv_seed['id'] + '"]')
                    expect(sv_card).to_be_visible(timeout=45000)
                    REPORT.parent.mkdir(parents=True,exist_ok=True); swedish.mouse.move(1,1)
                    swedish.screenshot(path=str(REPORT.with_name('approved-product-sv-320.png')))
                    visual_geometry['Swedish320px'] = room_geometry(swedish)
                    sv_composer = swedish.locator('.fc-chat-composer textarea'); sv_composer.fill('Ett oskickat utkast som ska finnas kvar.')
                    sv_card.locator('.fc-chat-material-footer button').scroll_into_view_if_needed()
                    sv_anchor = click_with_reading_anchor(swedish,sv_card.locator('.fc-chat-material-footer button'))
                    sv_inline = flow_region(swedish)
                    edit_saved_document(sv_inline, title=sv_seed['title'])
                    expect(sv_inline.locator('#shared-document-title')).to_have_value(sv_seed['title'])
                    sv_body = sv_inline.get_by_label('Dokumenttext',exact=True); expect(sv_body).to_be_editable()
                    swedish.wait_for_function("""() => {
                      const editor=document.querySelector('#shared-document-body'), timeline=document.querySelector('.fc-chat-timeline'), composer=document.querySelector('.fc-chat-composer-area');
                      if (!editor || !timeline || !composer) return false;
                      const box=editor.getBoundingClientRect();
                      return Math.min(box.bottom,composer.getBoundingClientRect().top)-Math.max(box.top,timeline.getBoundingClientRect().top)>=120;
                    }""")
                    saved_body = sv_body.input_value() + '\n\nEn liten anteckning från mobilvyn.'
                    sv_body.fill(saved_body); expect(sv_inline.locator('.fc-shared-sync')).to_have_text('Alla ändringar är synkroniserade')
                    live_conversation(swedish,sv_inline)
                    swedish.screenshot(path=str(REPORT.with_name('flow-document-sv-320.png')))
                    sv_organization_path = '/api/channels/' + sv_visit['channel_id'] + '/shared-document-organization'
                    def return_from_sv_document():
                        with swedish.expect_response(lambda response: response.request.method == 'GET' and urlsplit(response.url).path == sv_organization_path) as refreshed:
                            sv_inline.locator('.fc-conversation-work-header').get_by_role('button',name=CLOSE_FLOW,exact=True).click()
                        assert refreshed.value.ok
                        # Let the actual refreshed material commit its layout;
                        # preserve the visible source control even if it grows.
                        swedish.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    return_from_sv_document()
                    expect(sv_inline).to_have_count(0); expect(sv_composer).to_have_value('Ett oskickat utkast som ska finnas kvar.'); same_reading_anchor(swedish,sv_anchor)
                    sv_reopen_anchor = click_with_reading_anchor(swedish,sv_card.locator('.fc-chat-material-footer button'))
                    sv_inline = flow_region(swedish)
                    edit_saved_document(sv_inline, title=sv_seed['title'], text=saved_body)
                    expect(sv_inline.get_by_label('Dokumenttext',exact=True)).to_have_value(saved_body)
                    assert sv_inline.evaluate('node=>node.scrollWidth <= node.clientWidth + 1')
                    return_from_sv_document()
                    expect(sv_composer).to_have_value('Ett oskickat utkast som ska finnas kvar.'); same_reading_anchor(swedish,sv_reopen_anchor)
                    passed('Swedish320px_inline_native_document_edit_reopens_saved_text_and_preserves_draft')
                    sv_panel = open_flow(swedish,'Documents')
                    expect(sv_panel.get_by_role('button',name='Nytt delat dokument',exact=True)).to_be_visible()
                    expect(sv_panel.locator('[data-document-id="' + sv_seed['id'] + '"]')).to_be_visible()
                    assert sv_panel.evaluate('node=>node.scrollWidth <= node.clientWidth + 1')
                    REPORT.parent.mkdir(parents=True,exist_ok=True); swedish.screenshot(path=str(REPORT.with_name('product-demo-sv-320.png'))); page.screenshot(path=str(REPORT.with_name('product-demo-en-1440.png'))); storage_untouched(swedish)
                    passed('Swedish320px_uses_the_same_actual_product_and_documents_panel')

                    stage = 'privacy network and exact source checks'
                    assert not errors and not unexpected and not credentials, json.dumps({'pageErrors':errors,'unexpectedDestinations':unexpected,'credentialUse':credentials})
                    assert all(urlsplit(url).hostname == '127.0.0.1' and urlsplit(url).port == app_port for url in sockets)
                    assert source_hashes == runtime_hashes(ROOT), 'Runtime source changed during the proof'
                    passed('no_external_providers_retained_credentials_persistent_vault_or_service_worker')
                except Exception:
                    REPORT.parent.mkdir(parents=True,exist_ok=True)
                    if not page.is_closed():
                        page.screenshot(path=str(REPORT.with_name('product-demo-failure.png')))
                        print('Visible alerts: '+json.dumps(page.get_by_role('alert').all_text_contents()),file=sys.stderr)
                        print('Rendered room geometry: '+json.dumps(room_geometry(page)),file=sys.stderr)
                    raise
                finally:
                    for value in contexts:
                        value.close()
                    browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+','[redacted]',str(failure))
            print('FAIL product demo at ' + stage + ': ' + type(failure).__name__ + ' ' + detail[:2200],file=sys.stderr,flush=True)
            if 'source_hashes' in locals(): print('Snapshot Chat SHA256: ' + source_hashes['frontend/src/components/Chat.tsx'],file=sys.stderr)
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
        'results':results,'private_automation_pacing':{'minimum_group_seconds':6,'rolling_window_seconds':60,'next_group_request_target':160,'extra_waits':pacing_waits,'direct_HTTP_requests_counted':True,'HTTP_429_responses':sum(row['status']==429 for row in observed)},'visual_geometry':visual_geometry,'source_hashes':source_hashes,'cleanup':'Own processes, temporary source snapshots, keys, SQLite/files and browser contexts removed',
        'limits':['Native sample installation with explicitly unavailable external integrations; no live provider or Matrix service acceptance.',
                  'This private loopback proof does not attest Railway deployment, public TLS, scalability or physical device behavior.']},indent=2)+'\n')
    print('PASS product demo: ' + str(len(results)) + ' real product/browser groups',flush=True)


if __name__ == '__main__':
    main()
