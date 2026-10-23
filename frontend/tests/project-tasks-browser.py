#!/usr/bin/env python3
"""Two real private browsers exercise projects, tasks, conflicts and history.

Uses an isolated API/Vite/database/keys and native fixture accounts. No existing
installation, homeserver, provider credentials or external mail is used.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
from ui_navigation import open_conversation_work

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('private_task_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PASSWORD = 'Private-project-tasks-proof!2026'
REPORT = Path(os.getenv('PROJECT_TASKS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/project-tasks-browser.json')))


def login(page, app, user, workspace):
    page.goto(app, wait_until='domcontentloaded', timeout=90000)
    page.evaluate('([key,value]) => localStorage.setItem(key,value)', ['freecollab.workspace:' + user['user_id'], workspace['id']])
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(user['email']); page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    panel = open_conversation_work(page, 'tasks')
    expect(panel).to_be_visible()
    return panel


def upload(base, channel, owner):
    boundary = 'private-' + secrets.token_hex(16)
    content = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="Project source.txt"\r\nContent-Type: text/plain\r\n\r\nSource material\r\n--' + boundary + '--\r\n').encode()
    request = Request(base + '/files/channels/' + channel['id'], method='POST', data=content,
        headers={'Authorization': 'Bearer ' + owner['access_token'], 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def main():
    stage, results, processes, streams = 'private service startup', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-project-tasks-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port()
            base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            env = os.environ.copy()
            env.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]),
                OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            env['NO_PROXY'] = ','.join(filter(None, [env.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').exists() else sys.executable)
            launcher = private / 'api.py'
            launcher.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            for command, filename, cwd in [([python, str(launcher)], 'api.log', ROOT / 'backend'), (['node', str(helpers.vite_launcher(private, base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app + '/', processes)
            stage = 'private accounts, channel and linked material'
            run = secrets.token_hex(6)
            users = []
            for name in ['Owner', 'Peer', 'Outsider']:
                email = 'tasks-' + name.lower() + '-' + run + '@example.com'
                user = helpers.api(base, 'POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD}); user['email'] = email; users.append(user)
            owner, peer, outsider = users
            workspace = helpers.api(base, 'POST', '/workspaces', owner, {'name': 'Studio projects'})
            channel = helpers.api(base, 'POST', '/channels', owner, {'name': 'Launch work', 'workspace_id': workspace['id']})
            seed = '''import os
from sqlmodel import Session
from app import main
from app.database import engine
from app.models.channel import ChannelMember
from app.models.workspace import WorkspaceMember
with Session(engine) as session:
    session.add(WorkspaceMember(workspace_id=os.environ['PROOF_WORKSPACE'],user_id=os.environ['PROOF_PEER'],role='member'))
    session.add(ChannelMember(channel_id=os.environ['PROOF_CHANNEL'],user_id=os.environ['PROOF_PEER']))
    session.commit()
'''
            seeded = subprocess.run([python, '-c', seed], cwd=ROOT / 'backend', env={**env, 'PROOF_WORKSPACE': workspace['id'], 'PROOF_CHANNEL': channel['id'], 'PROOF_PEER': peer['user_id']}, capture_output=True, text=True, timeout=45)
            assert seeded.returncode == 0, 'Private membership fixture failed'
            file = upload(base, channel, owner)
            decision = helpers.api(base, 'POST', '/channels/' + channel['id'] + '/decisions', owner,
                {'title': 'Launch approval', 'content': 'Approved direction', 'status': 'accepted'})
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                contexts = [browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce') for _ in range(2)]
                page, peer_page = [context.new_page() for context in contexts]
                errors, urls = [], []
                for tab in [page, peer_page]:
                    tab.set_default_timeout(30000); tab.on('pageerror', lambda failure: errors.append(type(failure).__name__)); tab.on('request', lambda request: urls.append(request.url))
                try:
                    stage = 'native project creation and design controls'
                    panel = login(page, app, owner, workspace)
                    panel.get_by_role('button', name='New project', exact=True).click()
                    form = panel.get_by_role('form', name='New project', exact=True)
                    form.get_by_label('Project title', exact=True).fill('Launch project')
                    form.get_by_label('Description', exact=True).fill('Shared next steps')
                    form.get_by_label('Project color', exact=True).select_option('blue')
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/projects') as saved:
                        form.get_by_role('button', name='Save project', exact=True).click()
                    assert saved.value.status == 200; project = saved.value.json()
                    assert project['fields']['color'] == 'blue'
                    expect(panel.get_by_role('button', name='Launch project', exact=True)).to_have_attribute('aria-pressed', 'true')
                    results['actual_project_create_color_and_channel_scope'] = True
                    stage = 'task assignment, deadline, checklist and real links'
                    panel.get_by_role('button', name='New task', exact=True).click()
                    form = panel.get_by_role('form', name='New task', exact=True)
                    form.get_by_label('Task title', exact=True).fill('Prepare launch')
                    form.get_by_label('Description', exact=True).fill('Coordinate the release')
                    form.get_by_label('Responsible person', exact=True).select_option(peer['user_id'])
                    form.get_by_label('Due date', exact=True).fill('2026-12-12')
                    form.get_by_label('Priority', exact=True).select_option('high')
                    form.get_by_role('button', name='Add checklist item', exact=True).click()
                    form.get_by_label('Checklist item 1', exact=True).fill('Review launch brief')
                    form.get_by_label('Project source.txt', exact=True).check(); form.get_by_label('Launch approval', exact=True).check()
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/tasks')) as saved:
                        form.get_by_role('button', name='Save task', exact=True).click()
                    assert saved.value.status == 200; first = saved.value.json()
                    assert first['fields']['assignee_user_id'] == peer['user_id'] and first['fields']['due_date'] == '2026-12-12'
                    assert first['fields']['file_ids'] == [file['id']] and first['fields']['decision_ids'] == [decision['id']]
                    card = panel.locator('.fc-project-card').filter(has=page.get_by_role('heading', name='Prepare launch', exact=True))
                    expect(card).to_contain_text('Peer'); expect(card).to_contain_text('Project source.txt'); expect(card).to_contain_text('Launch approval')
                    results['actual_task_assignee_due_checklist_file_and_decision_links'] = True
                    stage = 'task links open exact file bytes and current decision text'
                    with page.expect_download() as downloaded:
                        card.get_by_role('button', name='Download file Project source.txt', exact=True).click()
                    assert downloaded.value.suggested_filename == 'Project source.txt'
                    assert Path(downloaded.value.path()).read_bytes() == b'Source material'
                    helpers.api(base, 'POST', '/decisions/' + decision['id'] + '/revisions', owner,
                        {'expected_revision': 1, 'title': 'Launch approval', 'content': 'Approved direction, confirmed for launch', 'status': 'accepted'})
                    card.get_by_role('button', name='Open decision Launch approval', exact=True).click()
                    detail = panel.get_by_role('region', name='Linked decision details', exact=True)
                    expect(detail.get_by_role('heading', name='Launch approval', exact=True)).to_be_visible()
                    expect(detail).to_contain_text('Approved direction, confirmed for launch'); expect(detail).to_contain_text('accepted'); expect(detail).to_contain_text('Version 2')
                    expect(detail).to_be_focused()
                    detail.get_by_role('button', name='Close linked decision', exact=True).click()
                    expect(card.get_by_role('button', name='Open decision Launch approval', exact=True)).to_be_focused()
                    results['linked_file_download_bytes_and_current_decision_details'] = True
                    stage = 'native task stage move and acknowledged checklist edit'
                    card.get_by_label('Move task Prepare launch', exact=True).select_option('doing')
                    expect(card.get_by_label('Move task Prepare launch', exact=True)).to_have_value('doing')
                    with page.expect_response(lambda response: response.request.method == 'POST' and urlsplit(response.url).path.endswith('/tasks')) as completed:
                        card.get_by_label('Review launch brief', exact=True).click()
                    assert completed.value.status == 200
                    expect(card.get_by_label('Review launch brief', exact=True)).to_be_checked()
                    card.get_by_role('button', name='History', exact=True).click()
                    history = panel.get_by_role('region', name='Project or task revision history', exact=True)
                    expect(history).to_contain_text('Version 3'); expect(history).to_contain_text('Version 1')
                    panel.get_by_role('button', name='Close revision history', exact=True).click()
                    results['native_stage_and_checklist_updates_append_immutable_history'] = True
                    stage = 'two browsers edit concurrently without silent overwrite'
                    card.get_by_role('button', name='Edit', exact=True).click()
                    owner_editor = panel.get_by_role('form', name='Edit task', exact=True)
                    owner_editor.get_by_label('Task title', exact=True).fill('Unsaved owner draft')
                    peer_panel = login(peer_page, app, peer, workspace)
                    peer_panel.get_by_role('button', name='Launch project', exact=True).click()
                    peer_panel.locator('.fc-project-flow-task').filter(has_text='Prepare launch').click()
                    peer_card = peer_panel.locator('.fc-project-card').filter(has=peer_page.get_by_role('heading', name='Prepare launch', exact=True))
                    peer_card.get_by_role('button', name='Edit', exact=True).click()
                    peer_panel.get_by_label('Task title', exact=True).fill('Peer revision')
                    peer_panel.get_by_role('button', name='Save task', exact=True).click()
                    expect(peer_panel.get_by_role('heading', name='Peer revision', exact=True)).to_be_visible()
                    owner_editor.get_by_role('button', name='Save task', exact=True).click()
                    expect(panel.get_by_role('alert')).to_contain_text('changed')
                    expect(owner_editor.get_by_label('Task title', exact=True)).to_have_value('Unsaved owner draft')
                    page.once('dialog', lambda prompt: prompt.accept())
                    panel.get_by_role('button', name='Reload current revisions', exact=True).click()
                    expect(panel.get_by_role('form', name='Edit task', exact=True)).to_have_count(0)
                    card = panel.locator('.fc-project-card').filter(has=page.get_by_role('heading', name='Peer revision', exact=True))
                    expect(card).to_be_visible(); card.get_by_label('Move task Peer revision', exact=True).select_option('done')
                    expect(card.get_by_label('Move task Peer revision', exact=True)).to_have_value('done')
                    results['two_browser_stale_revision_refused_until_explicit_reload'] = True
                    stage = 'archive and reopen keep task history'
                    card.get_by_role('button', name='Archive', exact=True).click()
                    panel.get_by_role('group', name='Confirm archive change', exact=True).get_by_role('button', name='Archive item', exact=True).click()
                    panel.get_by_role('button', name='All tasks', exact=True).click()
                    expect(panel.get_by_role('heading', name='Peer revision', exact=True)).to_have_count(0)
                    panel.locator('details.fc-project-flow-options > summary').click()
                    panel.get_by_label('Show archived tasks', exact=True).check()
                    panel.locator('.fc-project-flow-task').filter(has_text='Peer revision').click()
                    card = panel.locator('.fc-project-card').filter(has=page.get_by_role('heading', name='Peer revision', exact=True))
                    expect(card.get_by_label('Move task Peer revision', exact=True)).to_be_disabled()
                    card.get_by_role('button', name='Reopen', exact=True).click()
                    panel.get_by_role('group', name='Confirm archive change', exact=True).get_by_role('button', name='Reopen item', exact=True).click()
                    expect(card.get_by_label('Move task Peer revision', exact=True)).to_be_enabled()
                    results['archive_reopen_preserves_history_and_prevents_archived_edits'] = True
                    stage = 'private access, encrypted storage and mobile focus'
                    try:
                        helpers.api(base, 'GET', '/channels/' + channel['id'] + '/projects', outsider)
                        raise AssertionError('Outsider read project')
                    except HTTPError as error:
                        assert error.code == 403
                    latest = helpers.api(base, 'GET', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks/' + first['id'] + '/revisions', owner)
                    assert len(latest) == 7 and latest[0]['fields']['title'] == 'Peer revision' and latest[-1]['fields']['title'] == 'Prepare launch'
                    scan = '''import os
from sqlmodel import Session,select
from app import main
from app.database import engine
from app.models.project_tasks import ProjectTaskRevision
with Session(engine) as session:
    values=' '.join(row.encrypted_payload or '' for row in session.exec(select(ProjectTaskRevision)).all())
    assert all(value not in values for value in ['Launch project','Prepare launch','Peer revision','Review launch brief'])
'''
                    checked = subprocess.run([python, '-c', scan], cwd=ROOT / 'backend', env=env, capture_output=True, text=True, timeout=45)
                    assert checked.returncode == 0, 'Native project/task storage was not encrypted'
                    results['outsider_denied_and_native_task_snapshots_encrypted_at_rest'] = True
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('project-tasks-desktop.png')))
                    page.set_viewport_size({'width': 320, 'height': 900})
                    expect(panel).to_be_visible(); assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert panel.evaluate('element => element.scrollWidth <= element.clientWidth + 1')
                    panel.get_by_label('Show archived tasks', exact=True).focus()
                    for key in ['Tab', 'Tab', 'Shift+Tab']:
                        page.keyboard.press(key)
                        assert page.evaluate('document.activeElement!==document.body&&!document.activeElement.closest("[inert]")'), 'Inline task keyboard navigation lost an interactive control'
                    composer = page.locator('.fc-chat-composer textarea')
                    composer.focus(); expect(composer).to_be_focused()
                    expect(panel.get_by_role('tablist')).to_have_count(0)
                    page.screenshot(path=str(REPORT.with_name('project-tasks-mobile.png')))
                    results['responsive_320px_inline_task_and_interactive_conversation_focus'] = True
                    stage = 'stale task links recheck revoked channel membership'
                    revoked = '''import os
from sqlmodel import Session
from app import main
from app.database import engine
from app.models.channel import ChannelMember
with Session(engine) as session:
    session.delete(session.get(ChannelMember,(os.environ['PROOF_CHANNEL'],os.environ['PROOF_OWNER'])))
    session.commit()
'''
                    checked = subprocess.run([python, '-c', revoked], cwd=ROOT / 'backend', env={**env, 'PROOF_CHANNEL': channel['id'], 'PROOF_OWNER': owner['user_id']}, capture_output=True, text=True, timeout=45)
                    assert checked.returncode == 0, 'Private membership revocation fixture failed'
                    downloads_after_revocation = []
                    page.on('download', lambda download: downloads_after_revocation.append(download.suggested_filename))
                    with page.expect_response(lambda response: response.request.method == 'GET' and urlsplit(response.url).path == '/api/channels/' + channel['id'] + '/decisions') as denied_decision:
                        card.get_by_role('button', name='Open decision Launch approval', exact=True).click()
                    assert denied_decision.value.status == 403
                    expect(panel.get_by_role('alert')).to_be_visible()
                    expect(panel.get_by_role('region', name='Linked decision details', exact=True)).to_have_count(0)
                    with page.expect_response(lambda response: response.request.method == 'GET' and urlsplit(response.url).path == '/api/files/channels/' + channel['id']) as denied_file:
                        card.get_by_role('button', name='Download file Project source.txt', exact=True).click()
                    assert denied_file.value.status == 403
                    expect(panel.get_by_role('alert')).to_be_visible()
                    assert not downloads_after_revocation, 'Revoked task link downloaded a file'
                    results['stale_link_actions_recheck_current_channel_membership'] = True
                    assert not errors, 'Browser page errors'
                    for value in [PASSWORD, owner['access_token'], peer['access_token']]:
                        assert all(value not in url for url in urls), 'Credential in URL'
                    results['zero_browser_errors_and_no_credential_urls'] = True
                finally:
                    for context in contexts: context.close()
                    browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+', '[redacted]', str(failure).replace(PASSWORD, '[redacted]'))
            print('FAIL project/task browser stage: ' + stage + '; exception=' + type(failure).__name__ + '\n' + detail[:2500], file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Two actual private API/Vite/Chromium accounts with native tasks and synthetic linked files/decisions; no existing data/providers',
        'results': results, 'cleanup': 'Own processes, private database, file bytes, keys and browser contexts removed'}, indent=2) + '\n')
    print('PASS project/task browser: ' + str(len(results)) + ' checks', flush=True)


if __name__ == '__main__':
    main()
