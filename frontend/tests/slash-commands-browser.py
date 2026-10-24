#!/usr/bin/env python3
"""Actual composer feature discovery in isolated native and demo products.

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
from ui_navigation import choose_chat_channel, capture_work_card, conversation_work_region, close_work_card


ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('SLASH_COMMANDS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/slash-commands-browser.json')))
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
PASSWORD = 'Private-slash-commands-proof!2026'
RUN = secrets.token_hex(6)
FEATURES = re.compile(r'^(Show all features|Visa alla funktioner)$')
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
        assert all(process.poll() is None for process in processes), 'An owned slash-command fixture exited'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(.1)
    raise AssertionError('Owned slash-command fixture readiness timed out')


def source_hashes(private):
    paths = [*list((private / 'backend/app').rglob('*')), *list((private / 'frontend/src').rglob('*'))]
    paths += [private / 'frontend' / name for name in (
        'package.json', 'package-lock.json', 'vite.config.ts', 'vite.demo.config.ts',
        'postcss.config.js', 'tailwind.config.js', 'index.html', 'demo/index.html',
        'tests/slash-commands-browser.py', 'tests/ui_navigation.py')]
    return {str(path.relative_to(private)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


@contextmanager
def fixture(mode):
    processes, streams = [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-slash-' + mode + '-') as directory:
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
            print('Slash proof failed in ' + mode + ' during ' + stage, flush=True)
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


def mark(name):
    checks[name] = True
    print('PASS: ' + name, flush=True)


def screenshot(page, name):
    target = REPORT.with_name(REPORT.stem + '-' + name + '.png')
    page.screenshot(path=str(target), full_page=True)
    screenshots[name] = {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


def browser_context(browser, app, user=None, workspace=None, viewport=None):
    context = browser.new_context(viewport=viewport or {'width': 1440, 'height': 1100}, locale='en-US', reduced_motion='reduce')
    if user:
        # Real API-issued fixture sessions; no API or product handler is mocked.
        context.add_init_script('localStorage.setItem("freecollab_token",' + json.dumps(user['access_token']) + ');localStorage.setItem("freecollab_user_id",' + json.dumps(user['user_id']) + ');' +
            'localStorage.setItem(' + json.dumps('freecollab.workspace:' + user['user_id']) + ',' + json.dumps(workspace['id']) + ');')
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


def option(page, action):
    list_id = menu(page).get_by_role('listbox').get_attribute('id')
    return menu(page).locator('[role="option"][id=' + json.dumps(list_id + '-' + action) + ']')


def typed(page, composer, query):
    composer.fill('/' + query)
    expect(menu(page)).to_be_visible()
    expect(composer).to_be_focused()
    return menu(page)


def active_is_visible(page):
    active = menu(page).locator('[role="option"][aria-selected="true"]')
    expect(active).to_have_count(1)
    geometry = active.evaluate('element=>{const row=element.getBoundingClientRect(),list=element.closest("[role=listbox]").getBoundingClientRect();return{row:{top:row.top,bottom:row.bottom,left:row.left,right:row.right},list:{top:list.top,bottom:list.bottom,left:list.left,right:list.right},width:innerWidth,height:innerHeight}}')
    assert geometry['row']['top'] >= geometry['list']['top'] - 2
    assert geometry['row']['bottom'] <= geometry['list']['bottom'] + 2
    assert geometry['row']['left'] >= -1 and geometry['row']['right'] <= geometry['width'] + 1
    return geometry


def no_resource_writes(writes, since):
    return not any(row['method'] == 'POST' and (
        '/messages/channels/' in row['path'] or '/shared-documents' in row['path'] or '/projects' in row['path'] or '/decisions' in row['path'])
        for row in writes[since:])


def native(browser):
    global stage
    with fixture('native') as (base, app, private):
        owner = api(base, 'POST', '/auth/register', data={'name': 'Slash owner', 'email': 'slash-owner-' + RUN + '@example.com', 'password': PASSWORD})
        member = api(base, 'POST', '/auth/register', data={'name': 'Slash member', 'email': 'slash-member-' + RUN + '@example.com', 'password': PASSWORD})
        workspace = api(base, 'GET', '/workspaces', owner)[0]
        invite = api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
        api(base, 'POST', '/workspaces/invites/accept', member, {'token': invite['token']})
        channel = api(base, 'POST', '/channels', owner, {'name': 'Slash studio', 'workspace_id': workspace['id'], 'member_ids': [member['user_id']]})
        other = api(base, 'POST', '/channels', owner, {'name': 'Quiet studio', 'workspace_id': workspace['id'], 'member_ids': [member['user_id']]})
        api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'Real shared context for feature discovery.'})
        context, page, errors, unexpected, writes = browser_context(browser, app, owner, workspace)
        try:
            page.goto(app, wait_until='domcontentloaded'); expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            composer = page.locator('.fc-chat-composer textarea')
            stage = 'complete native catalogue and keyboard selection'
            since = len(writes)
            typed(page, composer, '')
            ids = menu(page).get_by_role('option').evaluate_all('rows=>rows.map(row=>row.id)')
            assert len(ids) >= 25 and len(ids) == len(set(ids)), 'Available native features are missing or duplicated'
            for action in ('document', 'task', 'decision', 'meetings', 'files', 'members', 'search', 'activity', 'apps', 'copilot', 'schedule', 'focus', 'appearance', 'account', 'integrations', 'status'):
                expect(option(page, action)).to_have_count(1)
            screenshot(page, 'native-desktop')
            first = composer.get_attribute('aria-activedescendant')
            composer.press('ArrowDown'); assert composer.get_attribute('aria-activedescendant') != first
            active_is_visible(page)
            composer.fill('/document'); expect(option(page, 'document')).to_have_attribute('aria-selected', 'true')
            composer.press('Enter')
            expect(menu(page)).to_have_count(0)
            work = conversation_work_region(page, kind='documents')
            expect(work).to_be_visible(); expect(composer).to_have_value('')
            assert no_resource_writes(writes, since), 'Discovery or opening a tool must not publish content'
            assert api(base, 'GET', '/channels/' + channel['id'] + '/shared-documents', owner) == []
            mark('typed_full_catalogue_keyboard_opens_actual_document_flow_without_publishing')

            stage = 'sibling feature selection retains the document and composer until its own guarded close'
            work.get_by_role('button', name='New shared document', exact=True).click()
            title = work.get_by_label('Document title', exact=True)
            title.fill('Unsaved document remains here')
            document_node = title.element_handle()
            draft = 'Keep this message draft while I choose a feature.'
            composer.fill(draft)
            page.get_by_role('button', name=FEATURES).click()
            search = menu(page).get_by_role('combobox', name='Search features', exact=True)
            search.fill('task')
            dialogs = []
            def dismiss(dialog):
                dialogs.append(dialog.message); dialog.dismiss()
            page.on('dialog', dismiss)
            option(page, 'task').click()
            expect(menu(page)).to_have_count(0)
            sibling = conversation_work_region(page, kind='tasks')
            assert capture_work_card(page, sibling).get_attribute('data-collaboration-work-id') != capture_work_card(page, work).get_attribute('data-collaboration-work-id')
            assert not dialogs, 'Opening a retained sibling must not discard the original document'
            expect(title).to_have_value('Unsaved document remains here'); expect(composer).to_have_value(draft)
            assert title.evaluate('(node,original)=>node===original&&original.isConnected', document_node)
            page.get_by_role('button', name=FEATURES).click()
            search = menu(page).get_by_role('combobox', name='Search features', exact=True)
            search.fill('next step')
            expect(option(page, 'task')).to_be_visible(); expect(composer).to_have_value(draft)
            assert no_resource_writes(writes, since), 'Discovery and retained sibling navigation must not create native content'
            search.press('Escape')
            close_work_card(page, sibling)
            assert not dialogs, "Closing the clean sibling must not invoke another card's dirty guard"
            expect(title).to_have_value('Unsaved document remains here')
            work.get_by_role('button', name='Close this work', exact=True).click()
            assert len(dialogs) == 1, 'Exactly one draft warning should protect the unsaved document'
            expect(title).to_have_value('Unsaved document remains here')
            assert title.evaluate('(node,original)=>node===original&&original.isConnected', document_node)
            page.remove_listener('dialog', dismiss)
            work.get_by_role('button', name='Cancel', exact=True).click()
            close_work_card(page, work)
            page.get_by_role('button', name=FEATURES).click()
            menu(page).get_by_role('combobox', name='Search features', exact=True).fill('task')
            option(page, 'task').click()
            sibling = conversation_work_region(page, kind='tasks')
            expect(composer).to_have_value(draft)
            close_work_card(page, sibling)
            mark('explicit_search_mouse_selection_retains_sibling_composer_and_own_guarded_document_draft')

            stage = 'unknown commands, escape and literal text delivery'
            before = len(api(base, 'GET', '/messages/channels/' + channel['id'], owner))
            typed(page, composer, 'not-real-feature')
            expect(menu(page).get_by_role('option')).to_have_count(0)
            composer.press('Enter')
            expect(menu(page)).to_be_visible()
            assert len(api(base, 'GET', '/messages/channels/' + channel['id'], owner)) == before
            composer.press('Escape'); expect(menu(page)).to_have_count(0)
            expect(composer).to_have_value('/not-real-feature')
            composer.press('Enter')
            expect(composer).to_have_value('')
            expect(page.locator('.fc-chat-message-content').filter(has_text='/not-real-feature')).to_be_visible()
            assert any(row['content'] == '/not-real-feature' for row in api(base, 'GET', '/messages/channels/' + channel['id'], owner))
            for literal in ('Use /docs/reference after lunch.', 'https://example.com/a/b', '//a literal leading slash'):
                composer.fill(literal); expect(menu(page)).to_have_count(0)
                composer.press('Enter'); expect(composer).to_have_value('')
                assert any(row['content'] == literal for row in api(base, 'GET', '/messages/channels/' + channel['id'], owner))
            typed(page, composer, '')
            composer.press('Shift+Enter'); expect(menu(page)).to_have_count(0); expect(composer).to_have_value('/\n')
            mark('escape_allows_intentional_literal_send_urls_prose_and_multiline_are_unchanged')

            stage = 'channel navigation invalidates the feature chooser'
            typed(page, composer, 'document')
            choose_chat_channel(page, other['name'])
            expect(menu(page)).to_have_count(0); expect(composer).to_have_value('')
            choose_chat_channel(page, channel['name'])
            expect(menu(page)).to_have_count(0); expect(composer).to_have_value('/document')
            composer.fill('')
            mark('channel_change_closes_chooser_and_preserves_channel_scoped_drafts')

            stage = 'saved Swedish preference and narrow-screen feature navigation'
            api(base, 'PUT', '/preferences/language', owner, {'locale': 'sv'})
            page.reload(wait_until='domcontentloaded'); expect(page.locator('html')).to_have_attribute('lang', 'sv')
            expect(composer).to_be_visible(timeout=90000)
            page.set_viewport_size({'width': 320, 'height': 800})
            typed(page, composer, '')
            expect(option(page, 'document')).to_contain_text('Dokument')
            for _ in range(menu(page).get_by_role('option').count() - 1):
                composer.press('ArrowDown')
            geometry = active_is_visible(page)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert menu(page).evaluate('element=>{const r=element.getBoundingClientRect();return r.top>=-1&&r.bottom<=innerHeight+1}')
            screenshot(page, 'swedish-320')
            composer.fill('/typsnitt')
            expect(option(page, 'appearance')).to_be_visible()
            composer.press('Escape')
            observations.append({'swedish320SelectedRowGeometry': geometry})
            mark('account_saved_swedish_search_and_all_features_scroll_at_320px')

            stage = 'real product session transition while the chooser is open'
            typed(page, composer, 'document')
            page.evaluate('''async ({session,workspaceId})=>{
              const {api}=await import('/src/lib/api.ts');
              localStorage.setItem('freecollab.workspace:'+session.user_id,workspaceId);
              api.applySession(session);
            }''', {'session': member, 'workspaceId': workspace['id']})
            expect(menu(page)).to_have_count(0)
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            page.set_viewport_size({'width': 1440, 'height': 1100})
            choose_chat_channel(page, channel['name'])
            expect(composer).not_to_have_value('/document')
            typed(page, composer, '')
            expect(option(page, 'get-started')).to_have_count(0)
            expect(option(page, 'admin')).to_have_count(0)
            expect(option(page, 'data')).to_have_count(0)
            expect(option(page, 'document')).to_have_count(1)
            expect(option(page, 'task')).to_have_count(1)
            assert api(base, 'GET', '/channels/' + channel['id'] + '/shared-documents', member) == []
            mark('genuine_account_transition_closes_old_chooser_isolates_drafts_and_applies_member_permissions')
            assert not errors, 'Native browser errors: ' + json.dumps(errors)
            assert not unexpected, 'Unexpected external request: ' + json.dumps(unexpected)
        finally:
            context.close()


def demo(browser):
    global stage
    stage = 'dedicated demo fixture startup'
    with fixture('demo') as (base, app, private):
        stage = 'actual signed sample visit and shared product slash chooser'
        context, page, errors, unexpected, writes = browser_context(browser, app)
        visit = None
        try:
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as response:
                page.goto(app + '/product-demo/?locale=en', wait_until='domcontentloaded')
            assert response.value.ok
            visit = response.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            expect(page.locator('.fc-product-demo-banner')).to_be_visible()
            composer = page.locator('.fc-chat-composer textarea')
            since = len(writes)
            typed(page, composer, 'task')
            screenshot(page, 'product-demo')
            option(page, 'task').click()
            work = conversation_work_region(page, kind='tasks')
            expect(work).to_be_visible()
            tasks = api(base, 'GET', '/channels/' + visit['channel_id'] + '/projects/' + visit['examples']['project_id'] + '/tasks', visit['session'])['items']
            assert tasks, 'The actual demo API must provide its seeded tasks'
            for task in tasks:
                expect(work.get_by_text(task['fields']['title'], exact=True)).to_be_visible(timeout=40000)
            assert no_resource_writes(writes, since)
            work.get_by_role('button', name='Close this work', exact=True).click()
            draft = 'An unsent sample message remains mine.'
            composer.fill(draft)
            page.get_by_role('button', name=FEATURES).click()
            expect(menu(page)).to_be_visible()
            menu(page).get_by_role('combobox', name='Search features', exact=True).fill('document')
            option(page, 'document').click()
            expect(conversation_work_region(page, kind='documents')).to_be_visible()
            expect(composer).to_have_value(draft)
            assert not errors and not unexpected
            assert not any(row.get('status') == 429 for row in observations)
            mark('real_demo_uses_same_slash_actions_native_seeded_tasks_and_preserved_drafts')
        finally:
            if visit:
                api(base, 'DELETE', '/demo/session', visit['session'])
            context.close()


def main():
    # Allow an orchestrator to cancel this owned fixture without leaving its
    # child services or temporary storage behind, including with piped stdin.
    def interrupt(_signal, _frame):
        raise KeyboardInterrupt('Owned slash proof interrupted')
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    complete = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            try:
                native(browser); demo(browser)
                complete = True
            finally:
                browser.close()
    finally:
        mismatches = {mode: [name for name, digest in hashes.items() if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
                      for mode, hashes in snapshots.items()}
        REPORT.write_text(json.dumps({'ok': complete, 'scope': __doc__, 'stage': stage, 'checks': checks, 'source_sha256': snapshots,
            'mismatchedCurrentSources': mismatches, 'observations': observations, 'screenshots': screenshots,
            'cleanup': 'Owned browser contexts, API/Vite processes, source copies, SQLite, files and ephemeral keys removed.'}, indent=2) + '\n')
    assert all(not rows for rows in mismatches.values()), 'Product sources changed while the private proof was running'
    print(json.dumps({'checks': checks, 'sourceCounts': {mode: len(rows) for mode, rows in snapshots.items()}, 'mismatches': mismatches}), flush=True)


if __name__ == '__main__':
    main()
