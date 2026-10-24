#!/usr/bin/env python3
"""Persistent work-area modes in actual private native/demo products.

Starts only owned loopback API/Vite processes with fresh SQLite, files and keys.
All actions use the real product UI, native APIs and the unchanged demo boundary.
No customer workspace, external provider, existing container or service is used.
"""
from contextlib import contextmanager
from pathlib import Path
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
from ui_navigation import choose_chat_channel, reveal_message_actions, edit_saved_document, show_channel_introduction, capture_work_card, work_surface, work_resume_button, parked_work_close_button


ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('WORKSPACE_MODES_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/workspace-modes-20261023/workspace-modes-browser.json')))
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
PASSWORD = 'Private-workspace-modes-proof!2026'
RUN = secrets.token_hex(6)
checks, snapshots, observations, screenshots, stage = {}, {}, [], {}, 'startup'


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def api(base, method, path, user=None, data=None):
    headers = {'Content-Type': 'application/json'}
    if user:
        headers['Authorization'] = 'Bearer ' + user['access_token']
    with urlopen(Request(base + path, method=method, headers=headers,
                         data=json.dumps(data).encode() if data is not None else None), timeout=45) as response:
        return json.load(response) if response.status != 204 else None


def ready(url, processes):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert all(process.poll() is None for process in processes), 'An owned workspace-mode fixture exited'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(.1)
    raise AssertionError('Owned workspace-mode fixture readiness timed out')


def source_hashes(private):
    paths = [*list((private / 'backend/app').rglob('*')), *list((private / 'frontend/src').rglob('*'))]
    paths += [private / 'frontend' / name for name in (
        'package.json', 'package-lock.json', 'vite.config.ts', 'vite.demo.config.ts',
        'postcss.config.js', 'tailwind.config.js', 'index.html', 'demo/index.html',
        'tests/workspace-modes-browser.py', 'tests/ui_navigation.py')]
    return {str(path.relative_to(private)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


@contextmanager
def fixture(mode):
    processes, streams = [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-workspace-modes-' + mode + '-') as directory:
        private = Path(directory)
        frontend, backend, data = private / 'frontend', private / 'backend', private / 'public-demo'
        data.mkdir(); backend.mkdir()
        shutil.copytree(ROOT / 'backend/app', backend / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copytree(ROOT / 'frontend', frontend, ignore=shutil.ignore_patterns('node_modules', 'dist*', '.env*', '*.log', '*.tsbuildinfo', '__pycache__', '.vite*'))
        (frontend / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
        snapshots[mode] = source_hashes(private)
        api_port, app_port = port(), port()
        base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
        environment = os.environ.copy()
        environment.update(ENVIRONMENT='test', DEMO_INSTANCE='true' if mode == 'demo' else 'false',
            DATABASE_URL='sqlite:///' + str(data / 'public-demo.sqlite'), DEMO_DATA_PATH=str(data),
            STORAGE_BACKEND='local', STORAGE_PATH=str(data / 'files'), REDIS_URL='',
            JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
            ACCOUNT_SECURITY_KEY_FILE=str(data / 'account-security.key'),
            MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}),
            MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MESSAGE_ENCRYPTION_KEY_FILE=str(data / 'message-keys.json'),
            PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), MAIL_DELIVERY_MODE='disabled',
            MEETINGS_SFU_PROVIDER='disabled', MEETINGS_ICE_SERVERS='[]', MEETINGS_TURN_URLS='[]',
            PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(backend))
        for flag in ('MATRIX_ENABLED', 'CONNECT_FEDERATION_ENABLED', 'OIDC_ENABLED', 'WEBHOOKS_ENABLED',
                     'SYNC_ENABLED', 'OTRUST_ENABLED', 'CLOUD_FILES_ENABLED', 'EXTERNAL_MEETINGS_ENABLED',
                     'COPILOT_ENABLED', 'PUSH_ENABLED', 'NATIVE_PUSH_ENABLED', 'NATIVE_CALLS_ENABLED',
                     'ENABLE_EXPERIMENTAL_FEATURES', 'ENTERPRISE_SEARCH_ENABLED', 'DATA_CONTROLS_ENABLED'):
            environment[flag] = 'false'
        environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
        environment['no_proxy'] = ','.join(filter(None, [environment.get('no_proxy', ''), environment['NO_PROXY']]))
        vite = private / 'vite.mjs'
        if mode == 'demo':
            config = 'configFile:' + json.dumps(str(frontend / 'vite.demo.config.ts')) + ','
        else:
            config = 'configFile:false,root:' + json.dumps(str(frontend)) + ',plugins:[react()],optimizeDeps:{exclude:["@matrix-org/matrix-sdk-crypto-wasm"]},'
        vite.write_text('import{createServer}from ' + json.dumps(str(ROOT / 'frontend/node_modules/vite/dist/node/index.js')) +
            ';import react from ' + json.dumps(str(ROOT / 'frontend/node_modules/@vitejs/plugin-react/dist/index.js')) +
            ';const server=await createServer({' + config + 'cacheDir:' + json.dumps(str(private / 'vite-cache')) +
            ',server:{host:"127.0.0.1",port:' + str(app_port) + ',strictPort:true,fs:{allow:' +
            json.dumps([str(private), str(ROOT / 'frontend/node_modules')]) + '},proxy:{"/api":{target:' + json.dumps(base) +
            ',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")},"/socket.io":{target:' + json.dumps(base) +
            ',ws:true}}}});await server.listen();')
        try:
            for command, name, cwd in [([PYTHON, '-m', 'uvicorn', 'app.main:socket_app', '--host', '127.0.0.1', '--port', str(api_port), '--no-proxy-headers'], 'api', backend),
                                       (['node', str(vite)], 'vite', frontend)]:
                stream = (private / (name + '.log')).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            ready(base + '/ready', processes); ready(app + ('/product-demo/' if mode == 'demo' else '/'), processes)
            yield base, app, private
        except Exception:
            print('Workspace-mode proof failed in ' + mode + ' during ' + stage, flush=True)
            for name in ('api', 'vite'):
                path = private / (name + '.log')
                if path.is_file():
                    print(name + ' tail:\n' + path.read_text()[-2500:], flush=True)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            for stream in streams:
                stream.close()
            observations.append({'ownedFixtureStopped': mode, 'processExitCodes': [process.returncode for process in processes]})


def mark(name):
    checks[name] = True
    print('PASS: ' + name, flush=True)


def screenshot(page, name):
    target = REPORT.with_name(REPORT.stem + '-' + name + '.png')
    page.screenshot(path=str(target), full_page=True)
    screenshots[name] = {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


def capture_failure(page, mode):
    try:
        screenshot(page, mode + '-failure')
        observations.append({'failedMode': mode, 'visibleProductText': page.locator('body').inner_text()[:18000]})
    except Exception:
        pass  # A failed browser must not hide the original assertion or stop cleanup.


def browser_context(browser, app, user=None, workspace=None, viewport=None):
    context = browser.new_context(viewport=viewport or {'width': 1440, 'height': 1100}, locale='en-US', reduced_motion='reduce')
    if user:
        # Real API-issued fixture sessions; no API or product handler is mocked.
        # Seed once per tab so a genuine account transition survives later reloads.
        context.add_init_script('if(!sessionStorage.getItem("workspaceModesFixtureSeeded")){' +
            'localStorage.setItem("freecollab_token",' + json.dumps(user['access_token']) + ');localStorage.setItem("freecollab_user_id",' + json.dumps(user['user_id']) + ');' +
            'localStorage.setItem(' + json.dumps('freecollab.workspace:' + user['user_id']) + ',' + json.dumps(workspace['id']) + ');' +
            'sessionStorage.setItem("workspaceModesFixtureSeeded","1");}')
    errors, unexpected, writes = [], [], []
    def boundary(route):
        parsed = urlsplit(route.request.url)
        if parsed.scheme not in ('blob', 'data') and parsed.netloc != urlsplit(app).netloc:
            unexpected.append(parsed.scheme + '://' + parsed.netloc + parsed.path); route.abort(); return
        route.continue_()
    context.route('**/*', boundary)
    page = context.new_page(); page.set_default_timeout(40000)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('response', lambda response: observations.append({'status': 429, 'path': urlsplit(response.url).path}) if response.status == 429 else None)
    page.on('request', lambda request: writes.append({'method': request.method, 'path': urlsplit(request.url).path}) if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else None)
    return context, page, errors, unexpected, writes


def menu(page):
    return page.locator('.fc-slash-commands')


def no_resource_writes(writes, since):
    return not any(row['method'] == 'POST' and (
        '/messages/channels/' in row['path'] or '/mobile/offline/channels/' in row['path'] or '/threads/messages/' in row['path'] or '/shared-documents' in row['path'] or '/projects' in row['path'] or '/decisions' in row['path'] or '/groups' in row['path'] or '/copilot/' in row['path'] or row['path'].endswith('/invoke'))
        for row in writes[since:])



def command(page, composer, value):
    composer.fill(value)
    expect(menu(page)).to_be_visible()
    expect(composer).to_be_focused()
    composer.press('Enter')


def references_menu(page):
    return page.locator('.fc-mention-references')


def reference_option(page, kind, identifier):
    panel = references_menu(page)
    list_id = panel.get_by_role('listbox').get_attribute('id')
    from urllib.parse import quote
    target = list_id + '-' + kind + '-' + quote(identifier, safe="~()*!.'-_")
    return panel.locator('[role="option"][id=' + json.dumps(target) + ']')


def choose_reference(page, composer, kind, identifier, query, keyboard=False):
    # Every caller continues at its actual current caret; End is also a real
    # slash-menu navigation command and must not be used as a cursor shortcut.
    composer.type('@' + query)
    panel = references_menu(page)
    expect(panel).to_be_visible()
    target = reference_option(page, kind, identifier)
    expect(target).to_be_visible()
    if keyboard:
        wanted = target.get_attribute('id')
        for _ in range(panel.get_by_role('option').count() + 1):
            if composer.get_attribute('aria-activedescendant') == wanted:
                break
            composer.press('ArrowDown')
        assert composer.get_attribute('aria-activedescendant') == wanted
        composer.press('Enter')
    else:
        target.click()
    expect(panel).to_have_count(0)
    expect(composer).to_be_focused()


def utf16_slice(value, start, end):
    return value.encode('utf-16-le')[start * 2:end * 2].decode('utf-16-le')


def assert_reference_bindings(message, expected):
    references = message.get('references') or []
    assert [(item['kind'], item['id']) for item in references] == expected, 'Reference identities changed or disappeared'
    previous = 0
    for item in references:
        assert item['start'] >= previous and item['end'] > item['start']
        assert utf16_slice(message['content'], item['start'], item['end']).startswith('@')
        previous = item['end']
    return references


def last_message(base, channel_id, user, content):
    messages = api(base, 'GET', '/messages/channels/' + channel_id, user)
    matches = [row for row in messages if row['content'] == content]
    assert len(matches) == 1, 'Expected exactly one durable native delivery'
    return matches[0]


def register(base, name, suffix):
    return api(base, 'POST', '/auth/register', data={'name': name, 'email': suffix + '-' + RUN + '@example.com', 'password': PASSWORD})


def workspace_pane(page, mode=None, work=None):
    pane = work_surface(page, work)
    expect(pane).to_have_count(1)
    if mode:
        expect(pane).to_have_attribute('data-workspace-mode', mode)
    if mode == 'minimized':
        expect(pane).to_be_hidden()
    else:
        expect(pane).to_be_visible()
    return pane


def install_dialog_answers(page):
    recorded, choice = [], {'accept': False}
    def answer(dialog):
        recorded.append(dialog.message)
        dialog.accept() if choice['accept'] else dialog.dismiss()
    page.on('dialog', answer)
    return recorded, choice


def field_snapshot(field):
    return field.evaluate('node=>({value:node.value,start:node.selectionStart,end:node.selectionEnd,scrollTop:node.scrollTop,scrollLeft:node.scrollLeft})')


def select_real_text(field):
    """Create a genuine keyboard selection and textarea scroll, without DOM edits."""
    field.focus()
    field.press('Control+Home')
    for _ in range(22):
        field.press('ArrowDown')
    field.press('Home')
    for _ in range(8):
        field.press('Shift+ArrowRight')
    return field.element_handle(), field_snapshot(field)


def assert_same_field(field, previous, expected):
    assert field.evaluate('(node,previous)=>node===previous&&previous.isConnected', previous), 'Changing work layout remounted its live editor'
    expect(field).to_have_value(expected['value'])
    expect(field).to_have_js_property('selectionStart', expected['start'])
    expect(field).to_have_js_property('selectionEnd', expected['end'])
    expect(field).to_have_js_property('scrollTop', expected['scrollTop'])
    expect(field).to_have_js_property('scrollLeft', expected['scrollLeft'])


def expand_work(page):
    pane = workspace_pane(page, 'split')
    pane.locator('[data-work-expand]').click()
    pane = workspace_pane(page, 'expanded')
    expect(pane).to_have_attribute('role', 'dialog')
    expect(pane).to_have_attribute('aria-modal', 'true')
    expect(pane).to_have_attribute('aria-label', re.compile(r'^(Full screen work area|Arbetsyta i helskärm)(: .+)?$'))
    expect(pane.locator('[data-work-expand]')).to_have_attribute('aria-pressed', 'true')
    assert pane.evaluate('node=>node.contains(document.activeElement)'), 'Expanded work must own the active focus'
    return pane


def restore_work(page):
    pane = workspace_pane(page, 'expanded')
    pane.locator('[data-work-expand]').click()
    pane = workspace_pane(page, 'split')
    expect(pane.locator('[data-work-expand]')).to_have_attribute('aria-pressed', 'false')
    return pane


def minimize_work(page, footer=False):
    pane = workspace_pane(page)
    previous_mode = pane.get_attribute('data-workspace-mode')
    if footer:
        pane.get_by_role('button', name=re.compile(r'^(Back to conversation|Tillbaka till samtalet)$'), exact=True).click()
    else:
        pane.locator('[data-work-minimize]').click()
    workspace_pane(page, 'minimized', pane)
    expect(work_resume_button(page, pane)).to_be_visible()
    return previous_mode


def continue_work(page, previous_mode, work=None):
    owner = capture_work_card(page, work)
    chip = work_resume_button(page, owner)
    chip.click()
    pane = workspace_pane(page, previous_mode, owner)
    expect(chip).to_have_count(0)
    return pane


def close_work_area(page):
    pane = workspace_pane(page)
    resume = work_resume_button(page, pane)
    pane.locator('[data-work-close]').click()
    expect(pane.locator('.fc-conversation-work, .fc-reference-groups')).to_have_count(0)
    expect(resume).to_have_count(0)


def cycle_live_editor(page, field, dialogs, footer=False):
    """The same real node survives both layouts and both parking return modes."""
    before = len(dialogs)
    node, state = select_real_text(field)
    pane = workspace_pane(page, 'split')
    pane_node = pane.element_handle()
    scrollers = [(scroll, scroll.evaluate('node=>({top:node.scrollTop,left:node.scrollLeft})'))
                 for scroll in pane.locator('.fc-conversation-work-body, .fc-reference-groups-body').all()]
    def same_scroll():
        for scroll, saved in scrollers:
            expect(scroll).to_have_js_property('scrollTop', saved['top'])
            expect(scroll).to_have_js_property('scrollLeft', saved['left'])
    expand_work(page)
    assert_same_field(field, node, state)
    same_scroll()
    assert pane.evaluate('(node,previous)=>node===previous', pane_node)
    restore_work(page)
    assert_same_field(field, node, state)
    same_scroll()
    old_mode = minimize_work(page, footer=footer)
    assert old_mode == 'split'
    assert node.evaluate('node=>node.isConnected'), 'Minimizing must park the actual editor'
    continue_work(page, old_mode)
    expect(field).to_be_focused()
    assert_same_field(field, node, state)
    same_scroll()
    expand_work(page)
    assert_same_field(field, node, state)
    old_mode = minimize_work(page)
    assert old_mode == 'expanded'
    assert node.evaluate('node=>node.isConnected')
    continue_work(page, old_mode)
    expect(field).to_be_focused()
    assert_same_field(field, node, state)
    same_scroll()
    field.press('Escape')
    workspace_pane(page, 'split')
    assert_same_field(field, node, state)
    assert len(dialogs) == before, 'Expand, restore, minimize and resume must not discard or warn about the draft'
    return node, state


class ActualWriteGate:
    """Pause only the delivery of one actual native request, never its payload."""
    def __init__(self, page, path):
        self.page, self.path, self.routes = page, path, []
        self.released = False
    def __enter__(self):
        self.pattern = '**' + self.path
        def gate(route):
            if route.request.method == 'POST' and not self.routes:
                self.routes.append(route)
            else:
                route.fallback()
        self.handler = gate
        self.page.route(self.pattern, gate)
        return self
    def release(self):
        assert len(self.routes) == 1, 'Expected one held native write'
        self.routes[0].continue_()
        self.released = True
    def __exit__(self, *_failure):
        if self.routes and not self.released:
            try:
                self.routes[0].abort()
            except Exception:
                pass
        self.page.unroute(self.pattern, self.handler)


def read_document(page, channel_id, document_id):
    return page.evaluate('''async ({channel,document})=>{
      const {readSharedDocumentForSearch}=await import('/src/lib/shared-documents-api.ts');
      return await readSharedDocumentForSearch(channel,document);
    }''', {'channel': channel_id, 'document': document_id})


def assert_bounded(page, pane, expanded=False, controls=True):
    geometry = pane.evaluate('node=>{const r=node.getBoundingClientRect();return{left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,viewportWidth:innerWidth,viewportHeight:innerHeight}}')
    assert geometry['left'] >= -1 and geometry['right'] <= geometry['viewportWidth'] + 1
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    if expanded:
        assert geometry['width'] >= geometry['viewportWidth'] * .9
        assert geometry['height'] >= geometry['viewportHeight'] * .7
    for control in ('[data-work-expand]', '[data-work-minimize]', '[data-work-close]') if controls else []:
        button = pane.locator(control)
        expect(button).to_be_visible()
        size = button.bounding_box()
        assert size and size['width'] >= 44 and size['height'] >= 44
    return geometry


def native(browser):
    global stage
    stage = 'owned native workspace-mode fixture startup'
    with fixture('native') as (base, app, _private):
        owner = register(base, 'Workspace owner', 'workspace-owner')
        first = register(base, 'Maja Review', 'workspace-maja')
        second = register(base, 'Noor Review', 'workspace-noor')
        workspace = api(base, 'GET', '/workspaces', owner)[0]
        for member in (first, second):
            invitation = api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
            api(base, 'POST', '/workspaces/invites/accept', member, {'token': invitation['token']})
        channel = api(base, 'POST', '/channels', owner, {'name': 'Workspace studio', 'workspace_id': workspace['id'], 'member_ids': [first['user_id'], second['user_id']]})
        other = api(base, 'POST', '/channels', owner, {'name': 'Other workspace studio', 'workspace_id': workspace['id'], 'member_ids': [first['user_id'], second['user_id']]})
        project = api(base, 'POST', '/channels/' + channel['id'] + '/projects', owner,
            {'id': 'workspace-project-' + RUN, 'expected_revision': 0, 'archived': False,
             'fields': {'title': 'Workspace project', 'description': 'Actual native work-mode proof', 'color': 'sage'}})
        source = api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'A genuine source for our workspace review ' + RUN})
        context, page, errors, unexpected, writes = browser_context(browser, app, owner, workspace)
        dialogs, answer = install_dialog_answers(page)
        try:
            page.goto(app, wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            composer = page.locator('.fc-chat-composer textarea')
            long_body = '\n'.join('Working line %02d — materials, people and a calm workspace 🧭 åäö.' % index for index in range(80))
            document_title = 'Workspace document ' + RUN

            stage = 'unpublished document is one live editor through all three modes'
            since = len(writes)
            command(page, composer, '/document "' + document_title + '"\n' + long_body)
            pane = workspace_pane(page, 'split')
            title = pane.get_by_label('Document title', exact=True)
            body = pane.get_by_label('Document text', exact=True)
            expect(title).to_have_value(document_title)
            expect(body).to_have_value(long_body)
            cycle_live_editor(page, body, dialogs, footer=True)
            assert no_resource_writes(writes, since), 'Changing draft presentation must not publish a document or message'
            assert not api(base, 'GET', '/channels/' + channel['id'] + '/shared-documents', owner)
            screenshot(page, 'native-document-restored')
            mark('native_document_draft_keeps_same_dom_text_selection_scroll_and_focus_across_split_expanded_and_parked')

            stage = 'explicit creation and real Yjs editing survive expanded and split presentation'
            expand_work(page)
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/shared-documents') as created:
                pane.get_by_role('button', name='Create document', exact=True).click()
            assert created.value.ok
            document = created.value.json()
            expect(pane.locator('[id$="shared-document-new-title"]')).to_have_count(0)
            body = pane.get_by_label('Document text', exact=True)
            expect(body).to_have_value(long_body)
            expect(pane.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            restore_work(page)
            edited_body = long_body + '\nA durable native edit from the same open workspace.'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/shared-documents/' + document['id'] + '/updates') as edited:
                body.fill(edited_body)
            assert edited.value.ok
            expect(pane.locator('.fc-shared-sync')).to_have_text('All edits synchronized', timeout=30000)
            canonical = read_document(page, channel['id'], document['id'])
            assert canonical['text'] == edited_body and canonical['title'] == document_title
            mark('native_document_create_and_yjs_body_edit_are_durable_in_the_real_canonical_document')

            stage = 'pending real Yjs update stays alive while expanded parked and guarded'
            update_path = '/api/channels/' + channel['id'] + '/shared-documents/' + document['id'] + '/updates'
            before_count = api(base, 'GET', update_path.removeprefix('/api') + '?after_sequence=0&limit=200', owner)['document']['update_count']
            pending_body = edited_body + '\nThe real network write remains pending across parking.'
            before_dialogs = len(dialogs)
            with ActualWriteGate(page, update_path) as gate:
                with page.expect_request(lambda request: request.method == 'POST' and urlsplit(request.url).path == update_path):
                    body.fill(pending_body)
                expect(pane.locator('.fc-shared-sync')).to_contain_text('waiting to sync')
                assert len(gate.routes) == 1
                assert api(base, 'GET', update_path.removeprefix('/api') + '?after_sequence=0&limit=200', owner)['document']['update_count'] == before_count
                node, state = select_real_text(body)
                expand_work(page)
                assert_same_field(body, node, state)
                old_mode = minimize_work(page)
                expect(work_resume_button(page, pane)).to_contain_text(document_title)
                parked_work_close_button(page, pane).click()
                workspace_pane(page, 'minimized')
                assert node.evaluate('node=>node.isConnected')
                choose_chat_channel(page, other['name'])
                workspace_pane(page, 'minimized')
                expect(page.locator('.fc-chat-topbar-channel strong')).to_have_text(channel['name'])
                from ui_navigation import close_channels
                close_channels(page)
                continue_work(page, old_mode)
                assert_same_field(body, node, state)
                body.press('Escape')
                workspace_pane(page, 'split')
                pane.locator('[data-work-close]').click()
                workspace_pane(page, 'split')
                assert_same_field(body, node, state)
                assert len(dialogs) == before_dialogs, 'A pending write must refuse navigation without offering discard'
                with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == update_path) as delivered:
                    gate.release()
                assert delivered.value.ok
                expect(pane.locator('.fc-shared-sync')).to_have_text('All edits synchronized', timeout=30000)
            assert read_document(page, channel['id'], document['id'])['text'] == pending_body
            mark('actual_inflight_yjs_write_survives_all_modes_and_blocks_close_or_navigation_until_real_delivery')

            stage = 'parked native document allows actual rich chat without losing either draft'
            node, state = select_real_text(body)
            minimize_work(page, footer=True)
            composer.fill('While reviewing the document ')
            choose_reference(page, composer, 'person', first['user_id'], 'Maja')
            composer.type('see ')
            choose_reference(page, composer, 'document', document['id'], document_title)
            rich_text = composer.input_value()
            continue_work(page, 'split')
            assert_same_field(body, node, state)
            expect(composer).to_have_value(rich_text)
            minimize_work(page)
            composer.press('Enter')
            expect(composer).to_have_value('')
            sent = last_message(base, channel['id'], owner, rich_text)
            assert_reference_bindings(sent, [('person', first['user_id']), ('document', document['id'])])
            assert sent['mentions'] == [first['user_id']]
            continue_work(page, 'split')
            assert_same_field(body, node, state)
            close_work_area(page)
            page.reload(wait_until='domcontentloaded')
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            page.locator('#message-' + sent['id']).locator('button[data-reference-kind="document"][data-reference-id=' + json.dumps(document['id']) + ']').click()
            pane = workspace_pane(page, 'split')
            edit_saved_document(pane, title=document_title, text=pending_body)
            expect(pane.get_by_label('Document text', exact=True)).to_have_value(pending_body)
            close_work_area(page)
            mark('parked_real_document_and_typed_chat_references_coexist_and_send_without_replacing_the_work_editor')

            stage = 'task review controls and genuine selection survive every work mode'
            task_title = 'Workspace task ' + RUN
            since = len(writes)
            command(page, composer, '/task "' + task_title + '"\n' + long_body)
            pane = workspace_pane(page, 'split')
            description = pane.get_by_label('Description', exact=True)
            expect(pane.get_by_label('Task title', exact=True)).to_have_value(task_title)
            pane.get_by_label('Responsible person', exact=True).select_option(second['user_id'])
            pane.get_by_label('Priority', exact=True).select_option('high')
            pane.get_by_label('Due date', exact=True).fill('2026-11-05')
            pane.get_by_role('button', name='Add checklist item', exact=True).click()
            pane.get_by_label('Checklist item 1', exact=True).fill('Review actual workspace behavior')
            pane.get_by_label('Complete checklist item 1', exact=True).check()
            cycle_live_editor(page, description, dialogs, footer=True)
            expect(pane.get_by_label('Responsible person', exact=True)).to_have_value(second['user_id'])
            expect(pane.get_by_label('Priority', exact=True)).to_have_value('high')
            expect(pane.get_by_label('Due date', exact=True)).to_have_value('2026-11-05')
            expect(pane.get_by_label('Checklist item 1', exact=True)).to_have_value('Review actual workspace behavior')
            expect(pane.get_by_label('Complete checklist item 1', exact=True)).to_be_checked()
            assert no_resource_writes(writes, since)
            mark('native_task_review_keeps_assignee_date_priority_checklist_draft_and_original_dom_when_parked_or_expanded')

            stage = 'explicit task save remains in flight while the same busy editor is parked'
            task_path = '/api/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks'
            before_dialogs = len(dialogs)
            task_node = description.element_handle()
            expand_work(page)
            with ActualWriteGate(page, task_path) as gate:
                with page.expect_request(lambda request: request.method == 'POST' and urlsplit(request.url).path == task_path):
                    pane.get_by_role('button', name='Save task', exact=True).click()
                expect(description).to_be_disabled()
                minimize_work(page)
                parked_work_close_button(page, pane).click()
                workspace_pane(page, 'minimized')
                assert task_node.evaluate('node=>node.isConnected')
                continue_work(page, 'expanded')
                assert description.evaluate('(node,previous)=>node===previous', task_node)
                assert len(dialogs) == before_dialogs
                with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == task_path) as saved:
                    gate.release()
                assert saved.value.ok
                expect(pane.get_by_label('Task title', exact=True)).to_have_count(0)
            task = next(item for item in api(base, 'GET', task_path.removeprefix('/api'), owner)['items'] if item['fields']['title'] == task_title)
            assert task['fields']['description'] == long_body and task['fields']['assignee_user_id'] == second['user_id']
            assert task['fields']['priority'] == 'high' and task['fields']['due_date'] == '2026-11-05'
            assert task['fields']['checklist'][0]['done'] is True
            restore_work(page)
            close_work_area(page)
            mark('real_task_save_is_single_and_durable_and_busy_work_cannot_be_discarded_through_the_resume_chip')

            stage = 'decision review parks and Escape restores before any dirty-close choice'
            decision_title = 'Workspace decision ' + RUN
            since = len(writes)
            command(page, composer, '/decision "' + decision_title + '"\n' + long_body)
            pane = workspace_pane(page, 'split')
            decision_text = pane.get_by_label('Decision and context', exact=True)
            expect(pane.get_by_label('Title', exact=True)).to_have_value(decision_title)
            pane.get_by_label('Responsible person', exact=True).select_option(first['user_id'])
            cycle_live_editor(page, decision_text, dialogs)
            expect(pane.get_by_label('Responsible person', exact=True)).to_have_value(first['user_id'])
            assert no_resource_writes(writes, since)
            before_dialogs = len(dialogs)
            decision_text.press('Escape')
            workspace_pane(page, 'split')
            assert len(dialogs) == before_dialogs + 1, 'The split-view Escape must retain the real dirty-draft warning'
            expect(decision_text).to_have_value(long_body)
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/decisions') as recorded:
                pane.get_by_role('button', name='Record decision', exact=True).click()
            assert recorded.value.ok
            expect(pane.locator('.fc-decision-card h3').filter(has_text=decision_title)).to_be_visible()
            saved_decision = next(item for item in api(base, 'GET', '/channels/' + channel['id'] + '/decisions', owner) if item['title'] == decision_title)
            assert saved_decision['content'] == long_body and saved_decision['responsible_user_id'] == first['user_id']
            close_work_area(page)
            mark('native_decision_draft_survives_all_modes_and_escape_restores_before_the_single_real_discard_warning')

            stage = 'channel group draft uses the same work controls and retains actual member IDs'
            group_name = 'Workspace reviewers ' + RUN
            since = len(writes)
            command(page, composer, '/group "' + group_name + '"')
            pane = workspace_pane(page, 'split')
            group_field = pane.get_by_label('Group name', exact=True)
            expect(group_field).to_have_value(group_name)
            for member in (first, second):
                pane.locator('input[data-reference-member-id=' + json.dumps(member['user_id']) + ']').check()
            cycle_live_editor(page, group_field, dialogs)
            for member in (first, second):
                expect(pane.locator('input[data-reference-member-id=' + json.dumps(member['user_id']) + ']')).to_be_checked()
            assert no_resource_writes(writes, since)
            assert not api(base, 'GET', '/mentions/channels/' + channel['id'] + '/groups', owner)
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/mentions/channels/' + channel['id'] + '/groups') as grouped:
                pane.get_by_role('button', name='Create group', exact=True).click()
            assert grouped.value.ok
            group = next(item for item in api(base, 'GET', '/mentions/channels/' + channel['id'] + '/groups', owner) if item['name'] == group_name)
            assert set(group['member_ids']) == {first['user_id'], second['user_id']}
            close_work_area(page)
            mark('real_channel_group_name_member_checkboxes_and_same_editor_survive_expand_restore_and_parking')

            stage = 'expanded work reveals its actual source without unmounting the draft'
            source_row = page.locator('#message-' + source['id'])
            reveal_message_actions(source_row)
            source_row.get_by_role('button', name='Continue in a document', exact=True).click()
            pane = workspace_pane(page, 'split')
            source_body = pane.get_by_label('Document text', exact=True)
            expect(source_body).to_have_value(source['content'])
            source_node = source_body.element_handle()
            expand_work(page)
            before_dialogs = len(dialogs)
            pane.get_by_role('button', name='Show source message', exact=True).click()
            workspace_pane(page, 'split')
            assert source_body.evaluate('(node,previous)=>node===previous&&previous.isConnected', source_node)
            expect(source_body).to_have_value(source['content'])
            expect(source_row).to_be_focused()
            assert len(dialogs) == before_dialogs, 'Showing the real source must preserve its live unpublished document'
            answer['accept'] = True
            close_work_area(page)
            answer['accept'] = False
            assert len(dialogs) == before_dialogs + 1
            mark('showing_the_real_source_from_expanded_work_restores_chat_and_preserves_the_same_document_draft')

            stage = 'actual channel membership revocation removes expanded and parked work authority'
            member_context, member_page, member_errors, member_external, _member_writes = browser_context(browser, app, first, workspace)
            member_dialogs, _member_answer = install_dialog_answers(member_page)
            try:
                member_page.goto(app, wait_until='domcontentloaded')
                expect(member_page.locator('.fc-chat')).to_be_visible(timeout=90000)
                choose_chat_channel(member_page, channel['name'])
                member_page.wait_for_function("async()=>{const{getSocket}=await import('/src/lib/socket.ts');return getSocket().connected;}")
                member_composer = member_page.locator('.fc-chat-composer textarea')
                command(member_page, member_composer, '/task "Revoked draft ' + RUN + '"\nOnly this current channel may show it.')
                member_pane = workspace_pane(member_page, 'split')
                member_field = member_pane.get_by_label('Description', exact=True)
                expect(member_field).to_have_value('Only this current channel may show it.')
                revoked_node = member_field.element_handle()
                expand_work(member_page)
                minimize_work(member_page)
                api(base, 'DELETE', '/channels/' + channel['id'] + '/members/' + first['user_id'], owner)
                expect(member_page.locator('.fc-work-resume')).to_have_count(0)
                member_page.wait_for_function('node=>!node.isConnected', arg=revoked_node)
                expect(member_page.locator('[data-work-expand]')).to_have_count(0)
                assert not member_dialogs, 'Authoritative access removal must not ask an old dirty guard for permission'
                assert not member_errors and not member_external
            finally:
                member_context.close()
            mark('actual_membership_revocation_removes_the_parked_live_editor_and_resume_chip_without_old_draft_authority')

            stage = 'genuine account transition clears old work layout and parked draft'
            command(page, composer, '/task "Owner private work ' + RUN + '"\nThe owner account owns this unsaved review.')
            pane = workspace_pane(page, 'split')
            private_field = pane.get_by_label('Description', exact=True)
            expect(private_field).to_have_value('The owner account owns this unsaved review.')
            private_node = private_field.element_handle()
            expand_work(page)
            minimize_work(page)
            before_dialogs = len(dialogs)
            page.evaluate('''async({session,workspaceId})=>{
              const{api}=await import('/src/lib/api.ts');
              localStorage.setItem('freecollab.workspace:'+session.user_id,workspaceId);
              api.applySession(session);
            }''', {'session': second, 'workspaceId': workspace['id']})
            expect(page.locator('.fc-work-resume')).to_have_count(0)
            page.wait_for_function('node=>!node.isConnected', arg=private_node)
            choose_chat_channel(page, channel['name'])
            expect(composer).to_have_value('')
            assert len(dialogs) == before_dialogs
            command(page, composer, '/task "Current account review ' + RUN + '"')
            pane = workspace_pane(page, 'split')
            expect(pane.get_by_label('Task title', exact=True)).to_have_value('Current account review ' + RUN)
            answer['accept'] = True
            close_work_area(page)
            answer['accept'] = False
            mark('real_account_transition_drops_old_editor_resume_chip_and_expanded_state_before_new_authorized_work')

            stage = 'saved Swedish and 320px keep actual modes usable with keyboard and touch-sized controls'
            api(base, 'PUT', '/preferences/language', second, {'locale': 'sv'})
            page.reload(wait_until='domcontentloaded')
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            expect(composer).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            page.set_viewport_size({'width': 320, 'height': 800})
            mobile_title = 'Mobil arbetsyta ' + RUN
            command(page, composer, '/dokument "' + mobile_title + '"\n' + long_body)
            pane = workspace_pane(page, 'split')
            mobile_body = pane.get_by_label('Dokumenttext', exact=True)
            expect(mobile_body).to_have_value(long_body)
            cycle_live_editor(page, mobile_body, dialogs)
            expand = pane.locator('[data-work-expand]')
            expand.focus()
            expand.press('Enter')
            workspace_pane(page, 'expanded')
            expect(expand).to_be_focused()
            geometry = assert_bounded(page, pane, expanded=True)
            screenshot(page, 'native-swedish-expanded-320')
            expand.press('Escape')
            workspace_pane(page, 'split')
            expect(mobile_body).to_have_value(long_body)
            mobile_node, mobile_state = select_real_text(mobile_body)
            minimize_work(page)
            expect(work_resume_button(page, pane)).to_contain_text(re.compile(r'Mobil arbetsyta|Dokument'))
            assert_bounded(page, work_resume_button(page, pane), controls=False)
            screenshot(page, 'native-swedish-parked-320')
            composer.fill('Mobil @')
            picker = references_menu(page)
            expect(picker.get_by_role('option').first).to_be_visible()
            composer.press('Escape')
            expect(picker).to_have_count(0)
            workspace_pane(page, 'minimized')
            composer.fill('')
            continue_work(page, 'split')
            expect(mobile_body).to_be_focused()
            assert_same_field(mobile_body, mobile_node, mobile_state)
            observations.append({'nativeSwedish320Expanded': geometry})
            answer['accept'] = True
            close_work_area(page)
            answer['accept'] = False
            mark('native_swedish_320px_modes_keyboard_escape_reference_popup_and_resume_preserve_the_real_mobile_draft')
            assert not errors and not unexpected, 'Unexpected runtime or external request: ' + json.dumps([errors, unexpected])
        except Exception:
            capture_failure(page, 'native')
            raise
        finally:
            context.close()


def demo(browser):
    global stage
    stage = 'owned actual-product demo workspace fixture startup'
    with fixture('demo') as (base, app, _private):
        context, page, errors, unexpected, writes = browser_context(browser, app)
        dialogs, answer = install_dialog_answers(page)
        visit = None
        try:
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as response:
                page.goto(app + '/product-demo/?locale=en', wait_until='domcontentloaded')
            assert response.value.ok
            visit = response.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            expect(page.locator('.fc-product-demo-banner')).to_be_visible()
            composer = page.locator('.fc-chat-composer textarea')
            channel_id = visit['channel_id']
            long_body = '\n'.join('Example line %02d — real product workspace, calm material review.' % index for index in range(65))

            stage = 'genuine demo native document is the same live product in all three modes'
            title = 'Demo workspace document ' + RUN
            since = len(writes)
            command(page, composer, '/document "' + title + '"\n' + long_body)
            pane = workspace_pane(page, 'split')
            body = pane.get_by_label('Document text', exact=True)
            expect(body).to_have_value(long_body)
            cycle_live_editor(page, body, dialogs, footer=True)
            assert no_resource_writes(writes, since)
            expand_work(page)
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel_id + '/shared-documents') as created:
                pane.get_by_role('button', name='Create document', exact=True).click()
            assert created.value.ok
            document = created.value.json()
            expect(pane.locator('[id$="shared-document-new-title"]')).to_have_count(0)
            body = pane.get_by_label('Document text', exact=True)
            expect(body).to_have_value(long_body)
            demo_body = long_body + '\nA genuine synchronized edit in the example installation.'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel_id + '/shared-documents/' + document['id'] + '/updates') as edited:
                body.fill(demo_body)
            assert edited.value.ok
            expect(pane.locator('.fc-shared-sync')).to_have_text('All edits synchronized', timeout=30000)
            restore_work(page)
            node, saved = select_real_text(body)
            minimize_work(page)
            expect(work_resume_button(page, pane)).to_contain_text(title)
            composer.fill('Review the actual example ')
            choose_reference(page, composer, 'document', document['id'], title)
            text = composer.input_value()
            composer.press('Enter')
            expect(composer).to_have_value('')
            message = last_message(base, channel_id, visit['session'], text)
            assert_reference_bindings(message, [('document', document['id'])])
            continue_work(page, 'split')
            assert_same_field(body, node, saved)
            expect(body).to_have_value(demo_body)
            expand_work(page)
            screenshot(page, 'actual-demo-document-expanded')
            restore_work(page)
            close_work_area(page)
            page.locator('#message-' + message['id']).locator('button[data-reference-kind="document"][data-reference-id=' + json.dumps(document['id']) + ']').click()
            pane = workspace_pane(page, 'split')
            edit_saved_document(pane, title=title, text=demo_body)
            expect(pane.get_by_label('Document text', exact=True)).to_have_value(demo_body)
            close_work_area(page)
            mark('actual_product_demo_document_uses_same_dom_modes_real_yjs_delivery_and_canonical_reopen_after_parking')

            stage = 'genuine demo task decision and group reviews remain unpublished across modes'
            titles = [('task', 'Demo task ' + RUN, 'Task title', 'Description'),
                      ('decision', 'Demo decision ' + RUN, 'Title', 'Decision and context')]
            for command_name, draft_title, title_label, body_label in titles:
                since = len(writes)
                command(page, composer, '/' + command_name + ' "' + draft_title + '"\n' + long_body)
                pane = workspace_pane(page, 'split')
                expect(pane.get_by_label(title_label, exact=True)).to_have_value(draft_title)
                field = pane.get_by_label(body_label, exact=True)
                expect(field).to_have_value(long_body)
                cycle_live_editor(page, field, dialogs)
                assert no_resource_writes(writes, since)
                before = len(dialogs)
                answer['accept'] = True
                close_work_area(page)
                answer['accept'] = False
                assert len(dialogs) == before + 1
            since = len(writes)
            command(page, composer, '/groups')
            pane = workspace_pane(page, 'split')
            groups = api(base, 'GET', '/mentions/channels/' + channel_id + '/groups', visit['session'])
            assert groups, 'The actual example installation needs its seeded group'
            target_group = groups[0]
            pane.locator('[data-reference-group-id=' + json.dumps(target_group['id']) + ']').click()
            group_name = pane.get_by_label('Group name', exact=True)
            expect(group_name).to_have_value(target_group['name'])
            cycle_live_editor(page, group_name, dialogs)
            for member_id in target_group['member_ids']:
                expect(pane.locator('input[data-reference-member-id=' + json.dumps(member_id) + ']')).to_be_checked()
            assert no_resource_writes(writes, since)
            close_work_area(page)
            mark('actual_demo_task_decision_and_current_group_share_mode_controls_without_automatic_record_or_message_writes')

            stage = 'actual demo Swedish mobile full screen and parked state preserve one live editor'
            page.get_by_role('button', name='Your account', exact=True).click()
            account = page.locator('.fc-account-panel')
            expect(account).to_be_visible()
            with page.expect_response(lambda response: response.request.method == 'PUT' and urlsplit(response.url).path == '/api/preferences/language') as language:
                account.get_by_role('combobox', name='Language', exact=True).select_option('sv')
            assert language.value.ok
            expect(page.locator('html')).to_have_attribute('lang', 'sv')
            account.locator('.fc-account-header button').click()
            expect(account).to_have_count(0)
            expect(composer).to_be_visible(timeout=90000)
            page.set_viewport_size({'width': 320, 'height': 800})
            title = 'Demo mobil arbetsyta ' + RUN
            since = len(writes)
            command(page, composer, '/dokument "' + title + '"\n' + long_body)
            pane = workspace_pane(page, 'split')
            body = pane.get_by_label('Dokumenttext', exact=True)
            expect(body).to_have_value(long_body)
            cycle_live_editor(page, body, dialogs)
            expand_work(page)
            geometry = assert_bounded(page, pane, expanded=True)
            screenshot(page, 'actual-demo-swedish-expanded-320')
            old_mode = minimize_work(page)
            expect(work_resume_button(page, pane)).to_be_visible()
            screenshot(page, 'actual-demo-swedish-parked-320')
            continue_work(page, old_mode)
            expect(body).to_have_value(long_body)
            body.press('Escape')
            workspace_pane(page, 'split')
            assert no_resource_writes(writes, since)
            observations.append({'actualDemoSwedish320Expanded': geometry})
            answer['accept'] = True
            close_work_area(page)
            answer['accept'] = False
            mark('actual_demo_swedish_320px_expanded_and_minimized_work_keep_native_controls_and_the_same_live_draft')
            assert not errors and not unexpected, 'Unexpected runtime or external request: ' + json.dumps([errors, unexpected])
            assert len([row for row in writes if row['method'] == 'POST' and row['path'] == '/api/demo/session']) == 1, 'Use exactly one genuine owned demo visit'
            assert not any(row.get('status') == 429 for row in observations), 'Workspace mode checks must respect unchanged demo limits'
        except Exception:
            capture_failure(page, 'demo')
            raise
        finally:
            if visit:
                api(base, 'DELETE', '/demo/session', visit['session'])
                observations.append({'ownedDemoVisitRevoked': 204})
            context.close()


def material_reading_anchor(page, control):
    """Capture the source position on the genuine click, after native scrolling."""
    control.evaluate('''element => {
      window.__workspaceMaterialReading = null;
      element.addEventListener('click', () => {
        const timeline = document.querySelector('.fc-chat-timeline');
        window.__workspaceMaterialReading = {
          documentId: element.closest('[data-document-id]').dataset.documentId,
          top: timeline.scrollTop,
          gap: timeline.scrollHeight - timeline.clientHeight - timeline.scrollTop,
          offset: element.getBoundingClientRect().top - timeline.getBoundingClientRect().top
        };
      }, {capture: true, once: true});
    }''')
    control.click()
    value = page.evaluate('window.__workspaceMaterialReading')
    assert value is not None
    return value


def same_material_reading_anchor(page, previous):
    # Wait beyond the close/layout commit: its composer ResizeObserver queues
    # another frame, which must not replace a restored near-bottom reading choice.
    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))')
    page.wait_for_function('''previous => {
      const timeline = document.querySelector('.fc-chat-timeline');
      const card = [...timeline.querySelectorAll('[data-document-id]')]
        .find(node => node.dataset.documentId === previous.documentId);
      const footer = card?.querySelector('.fc-chat-material-footer button');
      return footer && Math.abs(footer.getBoundingClientRect().top - timeline.getBoundingClientRect().top - previous.offset) <= 2;
    }''', arg=previous)
    actual = page.locator('.fc-chat-material-document[data-document-id=' + json.dumps(previous['documentId']) + '] .fc-chat-material-footer button').evaluate('''footer => {
      const timeline = document.querySelector('.fc-chat-timeline');
      return {documentId: footer.closest('[data-document-id]').dataset.documentId, top: timeline.scrollTop,
        offset: footer.getBoundingClientRect().top - timeline.getBoundingClientRect().top};
    }''')
    assert actual['documentId'] == previous['documentId'] and abs(actual['offset'] - previous['offset']) <= 2
    assert abs(actual['top'] - previous['top']) <= 2
    return actual


def demo_reading_return(browser):
    """A source close must preserve reading while explicit chat actions still win."""
    global stage
    stage = 'owned actual demo near-bottom source reading fixture startup'
    with fixture('demo') as (base, app, _private):
        snapshots['demo-reading-return'] = snapshots['demo'].copy()
        context, page, errors, unexpected, writes = browser_context(browser, app)
        dialogs, _answer = install_dialog_answers(page)
        visit = None
        try:
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as response:
                page.goto(app + '/product-demo/?locale=en', wait_until='domcontentloaded')
            assert response.value.ok
            visit = response.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            channel_id = visit['channel_id']
            document_id = visit['examples']['document_ids'][0]
            show_channel_introduction(page)
            card = page.locator('.fc-chat-material-document[data-document-id=' + json.dumps(document_id) + ']')
            card.get_by_role('button', name='Open document', exact=True).click()
            pane = workspace_pane(page, 'split')
            edit_saved_document(pane)
            field = pane.get_by_label(re.compile(r'^(Document text|Dokumenttext)$'), exact=True)
            expect(field).to_be_editable()
            long_body = field.input_value() + '\n\n' + '\n'.join('Reading line %02d — actual shared materials and decisions 🧭 åäö.' % index for index in range(80))
            update_path = '/api/channels/' + channel_id + '/shared-documents/' + document_id + '/updates'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == update_path) as saved:
                field.fill(long_body)
            assert saved.value.ok
            expect(pane.locator('.fc-shared-sync')).to_have_text('All edits synchronized')
            close_work_area(page)
            page.locator('.fc-chat-account-trigger').click()
            account = page.locator('.fc-account-panel')
            with page.expect_response(lambda response: response.request.method == 'PUT' and urlsplit(response.url).path == '/api/preferences/language') as language:
                account.get_by_role('combobox', name='Language', exact=True).select_option('sv')
            assert language.value.ok
            page.keyboard.press('Escape')
            expect(account).to_have_count(0)
            page.set_viewport_size({'width': 320, 'height': 850})
            composer = page.locator('.fc-chat-composer textarea')
            draft = 'Ett oskickat utkast som ska finnas kvar.'
            composer.fill(draft)
            action = card.locator('.fc-chat-material-footer button')
            action.scroll_into_view_if_needed()
            stage = 'actual near-bottom material source survives mode cycles footer parking and explicit close'
            before = material_reading_anchor(page, action)
            assert 0 < before['gap'] < 96, 'Exercise a reading choice inside the real near-bottom follow threshold'
            pane = workspace_pane(page, 'split')
            edit_saved_document(pane, text=long_body)
            field = pane.get_by_label('Dokumenttext', exact=True)
            final_body = long_body + '\n\nEn liten verklig anteckning i mobilvyn.'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == update_path) as saved:
                field.fill(final_body)
            assert saved.value.ok
            expect(pane.locator('.fc-shared-sync')).to_have_text('Alla ändringar är synkroniserade')
            since = len(writes)
            node, state = cycle_live_editor(page, field, dialogs, footer=True)
            assert no_resource_writes(writes, since)
            minimize_work(page, footer=True)
            assert node.evaluate('node=>node.isConnected')
            organization_path = '/api/channels/' + channel_id + '/shared-document-organization'
            with page.expect_response(lambda response: response.request.method == 'GET' and urlsplit(response.url).path == organization_path) as refreshed:
                parked_work_close_button(page, pane).click()
            assert refreshed.value.ok
            expect(pane.locator('.fc-conversation-work')).to_have_count(0)
            assert not node.evaluate('node=>node.isConnected')
            expect(composer).to_have_value(draft)
            actual = same_material_reading_anchor(page, before)
            action.click()
            pane = workspace_pane(page, 'split')
            edit_saved_document(pane, text=final_body)
            fresh_body = pane.get_by_label('Dokumenttext', exact=True)
            expect(fresh_body).to_have_value(final_body)
            assert fresh_body.evaluate('(current,previous)=>current!==previous&&!previous.isConnected', node)
            close_work_area(page)
            same_material_reading_anchor(page, before)
            observations.append({'nearBottomSourceReadingReturn': {'before': before, 'after': actual}})
            screenshot(page, 'actual-demo-swedish-reading-return-320')
            mark('actual_demo_near_bottom_material_reading_anchor_survives_same_editor_mode_cycles_footer_parking_and_explicit_close')

            stage = 'explicit send latest and deliberate parked conversation movement override a source origin'
            sent_text = draft + ' En verklig leverans ' + RUN
            composer.fill(sent_text)
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/mobile/offline/channels/' + channel_id + '/messages') as sent:
                composer.press('Enter')
            assert sent.value.ok
            expect(composer).to_have_value('')
            assert last_message(base, channel_id, visit['session'], sent_text)['content'] == sent_text
            bottom = "()=>{const n=document.querySelector('.fc-chat-timeline');return n.scrollHeight-n.clientHeight-n.scrollTop<=1}"
            page.wait_for_function(bottom)
            timeline = page.locator('.fc-chat-timeline')
            rectangle = timeline.bounding_box()
            page.mouse.move(rectangle['x'] + 40, rectangle['y'] + 30)
            page.mouse.wheel(0, -400)
            expect(page.locator('.fc-chat-latest')).to_be_visible()
            page.locator('.fc-chat-latest').click()
            page.wait_for_function(bottom)

            action.scroll_into_view_if_needed()
            opening = material_reading_anchor(page, action)
            pane = workspace_pane(page, 'split')
            edit_saved_document(pane, text=final_body)
            expect(pane.get_by_label('Dokumenttext', exact=True)).to_have_value(final_body)
            minimize_work(page)
            rectangle = timeline.bounding_box()
            page.mouse.move(rectangle['x'] + 40, rectangle['y'] + 30)
            page.mouse.wheel(0, -1200)
            page.wait_for_function('''()=>{const n=document.querySelector('.fc-chat-timeline'),r=n.getBoundingClientRect();return [...n.querySelectorAll('.fc-chat-message-content')].some(m=>{const b=m.getBoundingClientRect();return b.top<r.bottom&&b.bottom>r.top+1})}''')
            visible_id = timeline.evaluate('''n=>{const r=n.getBoundingClientRect();return [...n.querySelectorAll('.fc-chat-message')].find(m=>{const b=m.getBoundingClientRect();return b.top<r.bottom&&b.bottom>r.top+1}).id}''')
            page.locator('#' + visible_id + ' .fc-chat-message-content').click(position={'x': 8, 'y': 8})
            page.mouse.wheel(0, -120)
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            moved = timeline.evaluate('''n=>{const r=n.getBoundingClientRect(),m=[...n.querySelectorAll('.fc-chat-message')].find(m=>m.getBoundingClientRect().bottom>r.top+1);return{top:n.scrollTop,id:m.id,offset:m.getBoundingClientRect().top-r.top}}''')
            assert abs(moved['top'] - opening['top']) > 100, 'Exercise an actual different reading choice while work is parked'
            parked_work_close_button(page, pane).click()
            expect(pane.locator('.fc-conversation-work')).to_have_count(0)
            page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))')
            after = timeline.evaluate('''n=>{const r=n.getBoundingClientRect(),m=[...n.querySelectorAll('.fc-chat-message')].find(m=>m.getBoundingClientRect().bottom>r.top+1);return{top:n.scrollTop,id:m.id,offset:m.getBoundingClientRect().top-r.top}}''')
            assert after['id'] == moved['id'] and abs(after['offset'] - moved['offset']) <= 2, 'Closing parked work replaced the deliberate current conversation reading position'
            observations.append({'explicitParkedReadingChoice': {'sourceOpening': opening, 'beforeClose': moved, 'afterClose': after}, 'explicitOwnSendAndJumpLatestStillFollow': True})
            assert not dialogs, 'Synced work and mode changes must not ask to discard'
            mark('actual_demo_explicit_send_and_jump_latest_still_follow_and_closing_parked_work_respects_deliberate_conversation_movement')
            assert not errors and not unexpected
            assert not any(row.get('status') == 429 for row in observations)
        except Exception:
            capture_failure(page, 'demo-reading-return')
            raise
        finally:
            if visit:
                api(base, 'DELETE', '/demo/session', visit['session'])
                observations.append({'ownedReadingReturnDemoVisitRevoked': 204})
            context.close()


def main():
    def interrupt(_signal, _frame):
        raise KeyboardInterrupt('Owned workspace-mode proof interrupted')
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    complete = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            try:
                native(browser)
                demo(browser)
                demo_reading_return(browser)
                complete = True
            finally:
                browser.close()
    finally:
        mismatches = {mode: [name for name, digest in hashes.items() if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
                      for mode, hashes in snapshots.items()}
        REPORT.write_text(json.dumps({'ok': complete and all(not rows for rows in mismatches.values()), 'scope': __doc__, 'stage': stage,
            'checks': checks, 'source_sha256': snapshots, 'mismatchedCurrentSources': mismatches,
            'observations': observations, 'screenshots': screenshots,
            'cleanup': 'Owned browser contexts, API/Vite processes, source copies, SQLite, files and ephemeral keys removed.'}, indent=2) + '\n')
    assert all(not rows for rows in mismatches.values()), 'Product sources changed while the private proof was running'
    print(json.dumps({'checks': checks, 'sourceCounts': {mode: len(rows) for mode, rows in snapshots.items()}, 'mismatches': mismatches}), flush=True)


if __name__ == '__main__':
    main()

