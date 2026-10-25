#!/usr/bin/env python3
"""Command arguments and typed references in actual private native/demo products.

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
from ui_navigation import choose_chat_channel, reveal_message_actions, saved_document_reading


ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(os.getenv('COMMANDS_REFERENCES_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/commands-references-browser.json')))
PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
PASSWORD = 'Private-command-reference-proof!2026'
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
        assert all(process.poll() is None for process in processes), 'An owned command/reference fixture exited'
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(.1)
    raise AssertionError('Owned command/reference fixture readiness timed out')


def source_hashes(private):
    paths = [*list((private / 'backend/app').rglob('*')), *list((private / 'frontend/src').rglob('*'))]
    paths += [private / 'frontend' / name for name in (
        'package.json', 'package-lock.json', 'vite.config.ts', 'vite.demo.config.ts',
        'postcss.config.js', 'tailwind.config.js', 'index.html', 'demo/index.html',
        'tests/commands-references-browser.py', 'tests/ui_navigation.py')]
    return {str(path.relative_to(private)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


@contextmanager
def fixture(mode):
    processes, streams = [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-command-reference-' + mode + '-') as directory:
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
            print('Command/reference proof failed in ' + mode + ' during ' + stage, flush=True)
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
        context.add_init_script('if(!sessionStorage.getItem("commandReferenceFixtureSeeded")){' +
            'localStorage.setItem("freecollab_token",' + json.dumps(user['access_token']) + ');localStorage.setItem("freecollab_user_id",' + json.dumps(user['user_id']) + ');' +
            'localStorage.setItem(' + json.dumps('freecollab.workspace:' + user['user_id']) + ',' + json.dumps(workspace['id']) + ');' +
            'sessionStorage.setItem("commandReferenceFixtureSeeded","1");}')
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
        '/messages/channels/' in row['path'] or '/mobile/offline/channels/' in row['path'] or '/threads/messages/' in row['path'] or '/shared-documents' in row['path'] or '/projects' in row['path'] or '/decisions' in row['path'] or '/copilot/' in row['path'] or row['path'].endswith('/invoke'))
        for row in writes[since:])



def command(page, composer, value):
    composer.fill(value)
    expect(menu(page)).to_be_visible()
    expect(composer).to_be_focused()
    composer.press('Enter')


def work(page, kind):
    panel = page.locator('.fc-conversation-work[data-work-kind=' + json.dumps(kind) + ']')
    expect(panel).to_be_visible()
    return panel


def close_work(page):
    panels = page.locator('.fc-conversation-work')
    if panels.count():
        panels.get_by_role('button', name=re.compile(r'^(Close this work|Stäng det här arbetet)$'), exact=True).click()
        expect(panels).to_have_count(0)


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


def upload(base, channel, owner):
    boundary = 'private-command-reference-' + secrets.token_hex(12)
    filename = 'Review-material-' + RUN + '.txt'
    payload = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' + filename +
               '"\r\nContent-Type: text/plain\r\n\r\nVerified native reference material\r\n--' + boundary + '--\r\n').encode()
    request = Request(base + '/files/channels/' + channel['id'], method='POST', data=payload,
        headers={'Authorization': 'Bearer ' + owner['access_token'], 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def register(base, name, suffix):
    return api(base, 'POST', '/auth/register', data={'name': name, 'email': suffix + '-' + RUN + '@example.com', 'password': PASSWORD})


def native(browser):
    global stage
    with fixture('native') as (base, app, private):
        stage = 'real command/reference account and material setup'
        owner = register(base, 'Commands owner', 'commands-owner')
        first = register(base, 'Alex Rivera', 'commands-alex-first')
        second = register(base, 'Alex Rivera', 'commands-alex-second')
        workspace = api(base, 'GET', '/workspaces', owner)[0]
        for member in (first, second):
            invitation = api(base, 'POST', '/workspaces/' + workspace['id'] + '/invites', owner, {'role': 'member'})
            api(base, 'POST', '/workspaces/invites/accept', member, {'token': invitation['token']})
        channel = api(base, 'POST', '/channels', owner, {'name': 'Reference studio', 'workspace_id': workspace['id'], 'member_ids': [first['user_id'], second['user_id']]})
        other = api(base, 'POST', '/channels', owner, {'name': 'Other reference studio', 'workspace_id': workspace['id'], 'member_ids': [first['user_id']]})
        project = api(base, 'POST', '/channels/' + channel['id'] + '/projects', owner,
            {'id': 'command-project-' + RUN, 'expected_revision': 0, 'archived': False,
             'fields': {'title': 'Review project', 'description': 'Real native command review', 'color': 'sage'}})
        file = upload(base, channel, owner)
        api(base, 'POST', '/messages/channels/' + channel['id'], owner, {'content': 'Real reference studio context ' + RUN})
        context, page, errors, unexpected, writes = browser_context(browser, app, owner, workspace)
        try:
            page.goto(app, wait_until='domcontentloaded'); expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            composer = page.locator('.fc-chat-composer textarea')
            stage = 'Swedish task command arguments become a real review draft'
            title = 'Granska budget ' + RUN
            before_messages = len(api(base, 'GET', '/messages/channels/' + channel['id'], owner))
            since = len(writes)
            command(page, composer, '/uppgift ' + title)
            tasks = work(page, 'tasks')
            expect(tasks.get_by_label('Task title', exact=True)).to_have_value(title)
            assert no_resource_writes(writes, since), 'Command discovery must not save its draft'
            assert not api(base, 'GET', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', owner)['items']
            tasks.get_by_label('Description', exact=True).fill('Actual native task from a command.')
            tasks.get_by_role('button', name='Save task', exact=True).click()
            expect(tasks.get_by_text(title, exact=True)).to_be_visible()
            saved_tasks = api(base, 'GET', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', owner)['items']
            task = next(row for row in saved_tasks if row['fields']['title'] == title)
            assert task['fields']['description'] == 'Actual native task from a command.'
            assert len(api(base, 'GET', '/messages/channels/' + channel['id'], owner)) == before_messages
            close_work(page)
            mark('swedish_task_command_opens_prefilled_review_and_explicit_save_persists_native_task')

            stage = 'quoted document title and multiline arguments preserve actual content'
            doc_title = 'Mötesanteckningar ' + RUN
            doc_body = 'Första raden.\nAndra raden med 🧭 och åäö.'
            since = len(writes)
            command(page, composer, '/dokument "' + doc_title + '"\n' + doc_body)
            documents = work(page, 'documents')
            expect(documents.get_by_label('Document title', exact=True)).to_have_value(doc_title)
            expect(documents.get_by_label('Document text', exact=True)).to_have_value(doc_body)
            assert no_resource_writes(writes, since), 'Document arguments must remain an unpublished draft'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/shared-documents') as created:
                documents.get_by_role('button', name='Create document', exact=True).click()
            assert created.value.ok
            expect(documents.locator('#shared-document-new-title')).to_have_count(0)
            expect(documents.get_by_label('Document text', exact=True)).to_have_value(doc_body)
            docs = api(base, 'GET', '/channels/' + channel['id'] + '/shared-documents', owner)
            document = next(row for row in docs if row['title'] == doc_title)
            close_work(page)
            mark('quoted_multiline_document_command_is_reviewed_then_created_as_real_yjs_document')

            stage = 'decision and search arguments use their actual forms'
            decision_title = 'Vi lanserar ' + RUN
            since = len(writes)
            command(page, composer, '/beslut ' + decision_title)
            notebook = page.get_by_role('region', name='Decisions in this conversation', exact=True)
            expect(notebook.get_by_label('Title', exact=True)).to_have_value(decision_title)
            expect(notebook.get_by_label('Decision and context', exact=True)).to_have_value(decision_title)
            assert no_resource_writes(writes, since)
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/decisions') as recorded:
                notebook.get_by_role('button', name='Record decision', exact=True).click()
            assert recorded.value.ok
            expect(notebook.locator('.fc-decision-card h3').filter(has_text=decision_title)).to_be_visible()
            assert any(row['title'] == decision_title for row in api(base, 'GET', '/channels/' + channel['id'] + '/decisions', owner))
            close_work(page)
            command(page, composer, '/sök ' + RUN)
            search = page.get_by_role('dialog', name='Search all history', exact=True)
            expect(search.get_by_label('Search messages', exact=True)).to_have_value(RUN)
            expect(search.locator('.fc-history-result').first).to_be_visible()
            search.get_by_role('button', name='Close history search', exact=True).click()
            mark('decision_and_search_arguments_prefill_real_native_interfaces_without_message_delivery')

            stage = 'unsupported arguments remain an honest editable command'
            since = len(writes)
            composer.fill('/appearance ignore-this-argument')
            expect(menu(page)).to_be_visible()
            composer.press('Enter')
            expect(menu(page)).to_be_visible()
            expect(composer).to_have_value('/appearance ignore-this-argument')
            assert no_resource_writes(writes, since)
            composer.press('Escape'); composer.fill('')
            mark('unsupported_arguments_do_not_silently_execute_or_publish')

            stage = 'a command task draft survives creating its first project'
            choose_chat_channel(page, other['name'])
            queued_title = 'Projektlös uppgift ' + RUN
            composer.fill('/task "' + queued_title + '" Context remains with the task ')
            choose_reference(page, composer, 'person', first['user_id'], 'Alex')
            observations.append({'commandAfterPersonSelection': {
                'content': composer.input_value(), 'activeDescendant': composer.get_attribute('aria-activedescendant'),
                'selectedCommands': menu(page).locator('[role="option"][aria-selected="true"]').all_text_contents()}})
            assert composer.input_value().count('/task') == 1, 'Choosing a person must not duplicate the command draft'
            composer.press('Enter')
            queued_tasks = work(page, 'tasks')
            expect(queued_tasks.get_by_role('status').filter(has_text='Review this task draft after choosing or creating a project.')).to_be_visible()
            warnings = []
            def keep_queued(dialog):
                warnings.append(dialog.message); dialog.dismiss()
            page.on('dialog', keep_queued)
            queued_tasks.get_by_role('button', name='Close this work', exact=True).click()
            page.remove_listener('dialog', keep_queued)
            assert len(warnings) == 1, 'The pending command draft needs one actual discard choice'
            expect(queued_tasks).to_be_visible()
            before_project = len(writes)
            queued_tasks.get_by_role('button', name='New project', exact=True).click()
            queued_tasks.get_by_label('Project title', exact=True).fill('First command project ' + RUN)
            queued_tasks.get_by_role('button', name='Save project', exact=True).click()
            expect(queued_tasks.get_by_label('Task title', exact=True)).to_have_value(queued_title)
            expect(queued_tasks.get_by_label('Description', exact=True)).to_have_value(re.compile('Context remains with the task'))
            expect(queued_tasks.get_by_label('Responsible person', exact=True)).to_have_value(first['user_id'])
            assert not any(item['method'] == 'POST' and '/tasks' in item['path'] for item in writes[before_project:])
            first_project = next(item for item in api(base, 'GET', '/channels/' + other['id'] + '/projects', owner) if item['fields']['title'] == 'First command project ' + RUN)
            assert not api(base, 'GET', '/channels/' + other['id'] + '/projects/' + first_project['id'] + '/tasks', owner)['items']
            queued_tasks.get_by_role('button', name='Save task', exact=True).click()
            expect(queued_tasks.get_by_text(queued_title, exact=True)).to_be_visible()
            first_task = api(base, 'GET', '/channels/' + other['id'] + '/projects/' + first_project['id'] + '/tasks', owner)['items'][0]
            assert first_task['fields']['title'] == queued_title and first_task['fields']['assignee_user_id'] == first['user_id']
            close_work(page); choose_chat_channel(page, channel['name'])
            visible_candidates = api(base, 'GET', '/mentions/channels/' + channel['id'] + '/suggestions', owner)
            assert not any(item['kind'] == 'task' and item['id'] == first_task['id'] for item in visible_candidates)
            mark('pending_command_task_keeps_context_and_selected_person_through_real_first_project_creation')

            # The group editor is the same contextual product entry used by @.
            stage = 'real notification group creation from the command'
            command(page, composer, '/grupp "Review team ' + RUN + '"')
            groups = page.locator('.fc-reference-groups')
            expect(groups).to_be_visible()
            expect(groups.get_by_label('Group name', exact=True)).to_have_value('Review team ' + RUN)
            named_field = groups.get_by_role('textbox', name='Group name', exact=True)
            field_node = named_field.element_handle()
            assert named_field.evaluate('node => !node.closest("label") && node.labels.length === 1 && node.labels[0].control === node && node.labels[0].textContent.trim() === "Group name"')
            ai_help = groups.get_by_role('button', name='AI help for this text', exact=True)
            ai_help.click()
            expect(ai_help).to_have_attribute('aria-expanded', 'true')
            expect(groups.get_by_role('textbox', name='Group name', exact=True)).to_have_value('Review team ' + RUN)
            assert groups.locator('label button, label [data-collaboration-field]').count() == 0
            page.evaluate("async()=>{const {applyLocale}=await import('/src/i18n/core.ts');applyLocale('sv')}")
            translated_field = groups.get_by_role('textbox', name='Gruppnamn', exact=True)
            expect(translated_field).to_have_value('Review team ' + RUN)
            assert translated_field.evaluate('(node, previous) => node === previous && previous.isConnected', field_node)
            translated_help = groups.get_by_role('button', name='AI-hjälp för den här texten', exact=True)
            expect(translated_help).to_have_attribute('aria-expanded', 'true')
            translated_help.click()
            expect(translated_help).to_have_attribute('aria-expanded', 'false')
            expect(translated_field).to_be_focused()
            page.evaluate("async()=>{const {applyLocale}=await import('/src/i18n/core.ts');applyLocale('en')}")
            expect(groups.get_by_role('textbox', name='Group name', exact=True)).to_have_value('Review team ' + RUN)
            mark('group_exact_accessible_name_and_same_field_survive_AI_open_locale_change_and_focus_restore')
            for user in (first, second):
                groups.locator('input[type="checkbox"][data-reference-member-id=' + json.dumps(user['user_id']) + ']').check()
            groups.get_by_role('button', name='Create group', exact=True).click()
            expect(groups.get_by_text('Review team ' + RUN, exact=True)).to_be_visible()
            saved_groups = api(base, 'GET', '/mentions/channels/' + channel['id'] + '/groups', owner)
            group = next(row for row in saved_groups if row['name'] == 'Review team ' + RUN)
            assert set(group['member_ids']) == {first['user_id'], second['user_id']}
            groups.get_by_role('button', name='Close groups', exact=True).click()
            mark('command_creates_real_channel_group_with_explicit_current_member_selection')

            stage = 'typed people groups and real material references persist exact identities'
            since = len(writes)
            composer.fill('Review 🧭 ')
            choose_reference(page, composer, 'person', second['user_id'], 'Alex', keyboard=True)
            composer.type('with ')
            choose_reference(page, composer, 'group', group['id'], 'Review team')
            composer.type('in ')
            choose_reference(page, composer, 'document', document['id'], 'Mötesanteckningar')
            composer.type('for ')
            choose_reference(page, composer, 'task', task['id'], 'Granska budget')
            composer.type('using ')
            choose_reference(page, composer, 'file', file['id'], 'Review-material')
            composer.type('here ')
            choose_reference(page, composer, 'channel', channel['id'], channel['name'])
            assert no_resource_writes(writes, since), 'Reference selection must not send the draft or invoke a provider'
            rich_text = composer.input_value()
            expected = [('person', second['user_id']), ('group', group['id']), ('document', document['id']), ('task', task['id']), ('file', file['id']), ('channel', channel['id'])]
            composer.press('Enter'); expect(composer).to_have_value('')
            sent = last_message(base, channel['id'], owner, rich_text)
            stored_bindings = assert_reference_bindings(sent, expected)
            expected_labels = ['Alex Rivera', group['name'], doc_title, title, file['filename'], channel['name']]
            assert [utf16_slice(sent['content'], item['start'], item['end']) for item in stored_bindings] == ['@' + label for label in expected_labels], 'Stored UTF-16 spans must retain the exact labels selected in the real picker'
            assert set(sent['mentions']) == {first['user_id'], second['user_id']}, 'Group expansion must deduplicate the selected person and use current members'
            row = page.locator('#message-' + sent['id'])
            expect(row.locator('button.fc-message-reference')).to_have_count(len(expected))
            screenshot(page, 'native-reference-message')
            mark('actual_same_name_person_group_document_task_file_channel_bindings_and_recipient_ids_persist')

            stage = 'actual thread reply and message edit keep typed references'
            reveal_message_actions(row)
            row.get_by_role('button', name='Reply in thread', exact=True).click()
            thread = page.get_by_role('dialog', name='Thread', exact=True)
            reply = thread.get_by_label('Reply in thread', exact=True)
            reply.fill('Thread 🧭 ')
            choose_reference(page, reply, 'person', second['user_id'], 'Alex')
            reply.type('see ')
            choose_reference(page, reply, 'document', document['id'], 'Mötesanteckningar', keyboard=True)
            reply_text = reply.input_value()
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/threads/messages/' + sent['id'] + '/reply') as posted:
                thread.get_by_role('button', name='Send reply', exact=True).click()
            assert posted.value.ok
            reply_record = posted.value.json()
            stored_reply = api(base, 'GET', '/messages/' + reply_record['id'], owner)
            assert stored_reply['parent_id'] == sent['id'] and stored_reply['content'] == reply_text
            assert_reference_bindings(stored_reply, [('person', second['user_id']), ('document', document['id'])])
            expect(thread.locator('#thread-reply-' + reply_record['id']).locator('.fc-message-reference')).to_have_count(2)
            thread.get_by_role('button', name='Close thread', exact=True).click()
            composer.fill('Editable ')
            choose_reference(page, composer, 'person', second['user_id'], 'Alex')
            composer.type('see ')
            choose_reference(page, composer, 'document', document['id'], 'Mötesanteckningar')
            editable_text = composer.input_value(); composer.press('Enter'); expect(composer).to_have_value('')
            editable = last_message(base, channel['id'], owner, editable_text)
            edit_row = page.locator('#message-' + editable['id']); reveal_message_actions(edit_row)
            edit_row.get_by_role('button', name='Edit', exact=True).click()
            edit = edit_row.get_by_label('Edit message', exact=True)
            edit.press('Home'); edit.type('Updated ')
            updated = edit.input_value()
            edit_row.get_by_role('button', name='Save edit', exact=True).click()
            expect(edit_row.get_by_label('Edit message', exact=True)).to_have_count(0)
            kept = api(base, 'GET', '/messages/' + editable['id'], owner)
            assert kept['content'] == updated
            assert_reference_bindings(kept, [('person', second['user_id']), ('document', document['id'])])
            reveal_message_actions(edit_row); edit_row.get_by_role('button', name='Edit', exact=True).click()
            edit = edit_row.get_by_label('Edit message', exact=True)
            edit.press('Home')
            for _ in range(len('Updated Editable @Alex Rivera')):
                edit.press('ArrowRight')
            edit.press('Backspace'); edit.type('X')
            expect(references_menu(page)).to_be_visible()
            edit.press('Escape')
            expect(references_menu(page)).to_have_count(0)
            expect(edit).to_be_visible()
            edit_row.get_by_role('button', name='Save edit', exact=True).click()
            expect(edit_row.get_by_label('Edit message', exact=True)).to_have_count(0)
            changed = api(base, 'GET', '/messages/' + editable['id'], owner)
            assert_reference_bindings(changed, [('document', document['id'])])
            assert not changed['mentions']
            mark('real_thread_reply_and_message_edits_preserve_untouched_ids_and_drop_changed_person_binding')

            stage = 'persisted references reopen canonical content after a real reload'
            page.reload(wait_until='domcontentloaded'); expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            row = page.locator('#message-' + sent['id'])
            expect(row).to_be_visible()
            row.locator('button[data-reference-kind="document"][data-reference-id=' + json.dumps(document['id']) + ']').click()
            documents = work(page, 'documents')
            saved_document_reading(documents, title=document['title'], text=doc_body)
            expect(documents.get_by_label('Document text', exact=True)).to_have_value(doc_body)
            close_work(page)
            row.locator('button[data-reference-kind="task"][data-reference-id=' + json.dumps(task['id']) + ']').click()
            task_view = work(page, 'tasks')
            expect(task_view.locator('[data-task-id=' + json.dumps(task['id']) + ']')).to_be_visible()
            close_work(page)
            mark('reloaded_message_references_open_the_genuine_document_and_exact_task')

            stage = 'a deleted resource cannot be reopened through an old message chip'
            api(base, 'DELETE', '/files/' + file['id'], owner)
            page.reload(wait_until='domcontentloaded'); expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            row = page.locator('#message-' + sent['id'])
            unavailable = row.locator('[data-reference-kind="file"][data-reference-id=' + json.dumps(file['id']) + ']')
            expect(unavailable).to_have_class(re.compile(r'\bis-unavailable\b'))
            expect(row.locator('button[data-reference-kind="file"][data-reference-id=' + json.dumps(file['id']) + ']')).to_have_count(0)
            assert_reference_bindings(last_message(base, channel['id'], owner, rich_text), expected)
            mark('deleted_reference_target_keeps_literal_history_but_cannot_reopen_a_resource')

            stage = 'rich message draft survives reload and channel navigation'
            composer.fill('Retained 🧭 ')
            choose_reference(page, composer, 'person', second['user_id'], 'Alex')
            composer.type('see ')
            choose_reference(page, composer, 'document', document['id'], 'Mötesanteckningar')
            draft = composer.input_value()
            # Channel navigation awaits the actual serialized draft writes.
            choose_chat_channel(page, other['name']); expect(composer).to_have_value('')
            choose_chat_channel(page, channel['name']); expect(composer).to_have_value(draft)
            page.reload(wait_until='domcontentloaded'); expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name']); expect(composer).to_have_value(draft)
            encrypted_draft = page.evaluate('''({actor,channel})=>new Promise((resolve,reject)=>{
              const opened=indexedDB.open('freecollab-mobile-v1',1);
              opened.onerror=()=>reject(new Error('Actual draft vault could not be opened'));
              opened.onsuccess=()=>{
                const db=opened.result,transaction=db.transaction(['keys','vault'],'readonly');
                const key=transaction.objectStore('keys').get(actor);
                const record=transaction.objectStore('vault').get(actor+':draft:'+channel);
                transaction.onerror=()=>{db.close();reject(new Error('Actual draft vault could not be read'));};
                transaction.oncomplete=()=>{
                  const row=record.result,cryptoKey=key.result;
                  resolve({owner:row?.owner,kind:row?.kind,fields:row?Object.keys(row).sort():[],
                    ivBytes:row?.iv?.byteLength,cipherBytes:row?.ciphertext?.byteLength,
                    keyAlgorithm:cryptoKey?.algorithm?.name,keyBits:cryptoKey?.algorithm?.length,
                    keyExtractable:cryptoKey?.extractable});db.close();
                };
              };
            })''', {'actor': owner['user_id'], 'channel': channel['id']})
            assert encrypted_draft['owner'] == owner['user_id'] and encrypted_draft['kind'] == 'draft'
            assert encrypted_draft['fields'] == ['ciphertext', 'id', 'iv', 'kind', 'owner']
            assert encrypted_draft['ivBytes'] == 12 and encrypted_draft['cipherBytes'] > len(draft.encode())
            assert encrypted_draft['keyAlgorithm'] == 'AES-GCM' and encrypted_draft['keyBits'] == 256 and encrypted_draft['keyExtractable'] is False
            observations.append({'actualRichDraftVault': encrypted_draft})
            composer.press('Enter'); expect(composer).to_have_value('')
            draft_sent = last_message(base, channel['id'], owner, draft)
            assert_reference_bindings(draft_sent, [('person', second['user_id']), ('document', document['id'])])
            assert draft_sent['mentions'] == [second['user_id']]
            mark('encrypted_rich_draft_reload_and_channel_resume_keep_exact_reference_metadata')

            stage = 'edited mention text and current group membership cannot target the wrong people'
            api(base, 'PATCH', '/mentions/channels/' + channel['id'] + '/groups/' + group['id'], owner,
                {'expected_version': group['version'], 'member_ids': [second['user_id']]})
            composer.fill('Current ')
            choose_reference(page, composer, 'group', group['id'], 'Review team')
            changed_group_text = composer.input_value()
            composer.press('Enter'); expect(composer).to_have_value('')
            group_sent = last_message(base, channel['id'], owner, changed_group_text)
            assert set(group_sent['mentions']) == {second['user_id']}
            composer.fill('Literal ')
            choose_reference(page, composer, 'person', second['user_id'], 'Alex')
            composer.press('Backspace'); composer.press('Backspace'); composer.type('X ')
            literal = composer.input_value(); composer.press('Escape'); composer.press('Enter')
            expect(composer).to_have_value('')
            literal_sent = last_message(base, channel['id'], owner, literal)
            assert not literal_sent.get('references') and not literal_sent.get('mentions'), 'Editing a selected label must drop the ID binding rather than guess a person'
            mark('group_delivery_uses_current_members_and_edited_label_drops_its_identity')

            stage = 'real offline queue retains references and resolves group recipients when delivered'
            composer.fill('Queued current group ')
            choose_reference(page, composer, 'group', group['id'], 'Review team')
            queued_text = composer.input_value()
            current_group = next(item for item in api(base, 'GET', '/mentions/channels/' + channel['id'] + '/groups', owner) if item['id'] == group['id'])
            context.set_offline(True)
            composer.press('Enter'); expect(composer).to_have_value('')
            assert not any(item['content'] == queued_text for item in api(base, 'GET', '/messages/channels/' + channel['id'], owner))
            api(base, 'PATCH', '/mentions/channels/' + channel['id'] + '/groups/' + group['id'], owner,
                {'expected_version': current_group['version'], 'member_ids': [first['user_id']]})
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/mobile/offline/channels/' + channel['id'] + '/messages', timeout=45000) as delivered:
                context.set_offline(False)
            assert delivered.value.ok
            queued_record = last_message(base, channel['id'], owner, queued_text)
            assert_reference_bindings(queued_record, [('group', group['id'])])
            assert set(queued_record['mentions']) == {first['user_id']}, 'Queued group delivery used a stale membership snapshot'
            expect(page.locator('#message-' + queued_record['id'])).to_be_visible()
            mark('actual_offline_retry_preserves_rich_bindings_and_rechecks_current_group_recipients')

            stage = 'emails normal prose and the actual caret keep natural typing'
            for text in ('Write to alex@example.com today.', 'https://example.com/@name', 'A literal escaped \\@reference'):
                composer.fill(text); expect(references_menu(page)).to_have_count(0)
            composer.fill('Before  after')
            composer.press('Home')
            for _ in range(len('Before ')):
                composer.press('ArrowRight')
            composer.type('@Alex')
            expect(references_menu(page)).to_be_visible()
            reference_option(page, 'person', second['user_id']).click()
            expect(composer).to_have_value(re.compile(r'^Before @Alex Rivera  after$'))
            before = composer.input_value(); composer.press('Escape'); expect(composer).to_have_value(before)
            since = len(writes)
            composer.fill('Keyboard @Alex')
            keyboard_picker = references_menu(page)
            expect(keyboard_picker.get_by_role('option')).to_have_count(2)
            initial_active = composer.get_attribute('aria-activedescendant')
            composer.press('ArrowDown')
            assert composer.get_attribute('aria-activedescendant') != initial_active
            composer.press('ArrowUp')
            assert composer.get_attribute('aria-activedescendant') == initial_active
            keyboard_text = composer.input_value()
            composer.press('Tab')
            expect(keyboard_picker).to_have_count(0)
            expect(composer).to_have_value(keyboard_text)
            assert no_resource_writes(writes, since), 'Tab must leave reference discovery without sending or saving'
            composer.fill('')
            mark('reference_search_respects_the_actual_caret_and_leaves_emails_urls_and_escaped_text_alone')

            stage = 'genuine account change does not restore the prior rich draft'
            composer.fill('Owner private reference draft ')
            choose_reference(page, composer, 'document', document['id'], 'Mötesanteckningar')
            composer.type('@Alex'); expect(references_menu(page)).to_be_visible()
            page.evaluate('''async ({session,workspaceId})=>{
              const {api}=await import('/src/lib/api.ts');
              localStorage.setItem('freecollab.workspace:'+session.user_id,workspaceId);
              api.applySession(session);
            }''', {'session': first, 'workspaceId': workspace['id']})
            expect(references_menu(page)).to_have_count(0)
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name']); expect(composer).to_have_value('')
            command(page, composer, '/grupp')
            groups = page.locator('.fc-reference-groups'); expect(groups).to_be_visible()
            expect(groups.get_by_role('button', name='Create group', exact=True)).to_have_count(0)
            groups.get_by_role('button', name='Close groups', exact=True).click()
            mark('account_transition_closes_reference_search_and_isolates_rich_drafts_and_group_management')

            stage = 'saved Swedish language and actual narrow-screen reference navigation'
            api(base, 'PUT', '/preferences/language', first, {'locale': 'sv'})
            page.reload(wait_until='domcontentloaded'); expect(page.locator('html')).to_have_attribute('lang', 'sv')
            expect(composer).to_be_visible(timeout=90000)
            choose_chat_channel(page, channel['name'])
            page.set_viewport_size({'width': 320, 'height': 800})
            since = len(writes)
            narrow_title = 'Mobil utkast ' + RUN
            narrow_command = '/uppgift "' + narrow_title + '"\nKort sammanhang med 🧭 och åäö.'
            composer.fill(narrow_command)
            expect(menu(page).locator('.fc-slash-commands-usage')).to_be_visible()
            expect(menu(page).locator('.fc-slash-commands-preview')).to_contain_text(narrow_title)
            expect(menu(page).locator('.fc-slash-commands-error')).to_have_count(0)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            screenshot(page, 'swedish-command-help-320')
            composer.press('Escape')
            expect(menu(page)).to_have_count(0)
            expect(composer).to_have_value(narrow_command)
            assert no_resource_writes(writes, since), 'Narrow-screen command help must keep the unpublished draft intact'
            composer.fill('Mobil 🧭 @')
            picker = references_menu(page); expect(picker).to_be_visible()
            expect(picker.get_by_role('option').nth(3)).to_be_visible()
            count = picker.get_by_role('option').count(); assert count >= 4
            for _ in range(count - 1):
                composer.press('ArrowDown')
            active = picker.locator('[role="option"][aria-selected="true"]'); expect(active).to_have_count(1)
            geometry = active.evaluate('element=>{const r=element.getBoundingClientRect(),l=element.closest("[role=listbox]").getBoundingClientRect();return{top:r.top,bottom:r.bottom,left:r.left,right:r.right,listTop:l.top,listBottom:l.bottom,width:innerWidth}}')
            assert geometry['top'] >= geometry['listTop'] - 2 and geometry['bottom'] <= geometry['listBottom'] + 2
            assert geometry['left'] >= -1 and geometry['right'] <= 321
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            screenshot(page, 'swedish-reference-320')
            observations.append({'swedish320ReferenceGeometry': geometry})
            composer.press('Escape'); composer.fill('Mobil 🧭 ')
            choose_reference(page, composer, 'document', document['id'], 'Mötesanteckningar', keyboard=True)
            mobile_text = composer.input_value(); composer.press('Enter'); expect(composer).to_have_value('')
            mobile = last_message(base, channel['id'], first, mobile_text)
            assert_reference_bindings(mobile, [('document', document['id'])])
            mark('saved_swedish_320px_reference_list_is_reachable_and_dispatches_a_real_typed_reference')
            assert not errors and not unexpected, 'Unexpected runtime or external request: ' + json.dumps([errors, unexpected])
        except Exception:
            capture_failure(page, 'native')
            raise
        finally:
            context.close()


def demo(browser):
    global stage
    stage = 'dedicated command/reference demo fixture startup'
    with fixture('demo') as (base, app, private):
        context, page, errors, unexpected, writes = browser_context(browser, app)
        visit = None
        try:
            stage = 'actual example data and command-led task review'
            with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/demo/session') as response:
                page.goto(app + '/product-demo/?locale=en', wait_until='domcontentloaded')
            assert response.value.ok; visit = response.value.json()
            expect(page.locator('.fc-chat')).to_be_visible(timeout=90000)
            expect(page.locator('.fc-product-demo-banner')).to_be_visible()
            composer = page.locator('.fc-chat-composer textarea')
            since = len(writes)
            title = 'Demo command review ' + RUN
            command(page, composer, '/task ' + title)
            tasks = work(page, 'tasks')
            expect(tasks.get_by_label('Task title', exact=True)).to_have_value(title)
            assert no_resource_writes(writes, since)
            discarded = []
            def discard_demo_review(dialog):
                discarded.append(dialog.message); dialog.accept()
            page.on('dialog', discard_demo_review)
            tasks.get_by_role('button', name='Cancel', exact=True).click()
            page.remove_listener('dialog', discard_demo_review)
            assert len(discarded) == 1, 'Discarding the reviewed command draft needs one explicit warning'
            expect(tasks.get_by_label('Task title', exact=True)).to_have_count(0)
            close_work(page)
            mark('actual_product_demo_command_arguments_open_unpublished_native_review')

            stage = 'actual seeded groups and material in the demo reference picker'
            channel_id = visit['channel_id']
            candidates = api(base, 'GET', '/mentions/channels/' + channel_id + '/suggestions', visit['session'])
            kinds = {item['kind'] for item in candidates}
            assert {'person', 'group', 'document', 'task', 'file', 'channel'} <= kinds, 'Example installation needs actual useful reference data'
            group = next(item for item in candidates if item['kind'] == 'group')
            document = next(item for item in candidates if item['kind'] == 'document')
            composer.fill('Demo context ')
            choose_reference(page, composer, 'group', group['id'], group['label'])
            composer.type('see ')
            choose_reference(page, composer, 'document', document['id'], document['label'], keyboard=True)
            text = composer.input_value()
            screenshot(page, 'actual-product-demo-references')
            composer.press('Enter'); expect(composer).to_have_value('')
            sent = last_message(base, channel_id, visit['session'], text)
            assert_reference_bindings(sent, [('group', group['id']), ('document', document['id'])])
            resolved = api(base, 'POST', '/mentions/channels/' + channel_id + '/references/resolve', visit['session'],
                           {'references': [{'kind': 'group', 'id': group['id']}]})['items'][0]
            assert resolved['available'] and set(sent['mentions']) == set(resolved['user_ids'])
            expect(page.locator('#message-' + sent['id']).locator('.fc-message-reference')).to_have_count(2)
            mark('actual_demo_groups_documents_and_notification_recipients_use_real_product_apis')
            assert not errors and not unexpected
            assert not any(row.get('status') == 429 for row in observations), 'A command/reference proof exceeded unchanged demo limits'
        except Exception:
            capture_failure(page, 'demo')
            raise
        finally:
            if visit:
                api(base, 'DELETE', '/demo/session', visit['session'])
                observations.append({'ownedDemoVisitRevoked': 204})
            context.close()


def main():
    def interrupt(_signal, _frame):
        raise KeyboardInterrupt('Owned command/reference proof interrupted')
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
        REPORT.write_text(json.dumps({'ok': complete and all(not rows for rows in mismatches.values()), 'scope': __doc__, 'stage': stage, 'checks': checks, 'source_sha256': snapshots,
            'mismatchedCurrentSources': mismatches, 'observations': observations, 'screenshots': screenshots,
            'cleanup': 'Owned browser contexts, API/Vite processes, source copies, SQLite, files and ephemeral keys removed.'}, indent=2) + '\n')
    assert all(not rows for rows in mismatches.values()), 'Product sources changed while the private proof was running'
    print(json.dumps({'checks': checks, 'sourceCounts': {mode: len(rows) for mode, rows in snapshots.items()}, 'mismatches': mismatches}), flush=True)


if __name__ == '__main__':
    main()
