#!/usr/bin/env python3
"""Real visual builder, file trigger, worker restart/reminder, approval and task proof."""
import base64
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('private_workflow_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PASSWORD = 'Private-workflows-proof!2026'
REPORT = Path(os.getenv('WORKFLOWS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/workflows-browser.json')))


def eventually(read, condition, message):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        value = read()
        if condition(value): return value
        time.sleep(.25)
    raise AssertionError(message)


def main():
    stage, results, processes, streams = 'private service startup', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-workflows-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port()
            base, app = 'http://127.0.0.1:' + str(api_port), 'http://127.0.0.1:' + str(app_port)
            environment = os.environ.copy()
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'), STORAGE_PATH=str(private / 'files'),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',
                MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(private / 'account.key'),
                MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]),
                OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', DATA_CONTROLS_ENABLED='false',
                PUSH_ENABLED='false', WORKFLOWS_ENABLED='true', PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            server = private / 'api.py'
            server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            for command, filename, cwd in [([python, str(server)], 'api.log', ROOT / 'backend'), (['node', str(helpers.vite_launcher(private, base, app_port))], 'vite.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app + '/', processes)
            stage = 'private account/channel/project fixtures'
            email = 'workflow-proof-' + secrets.token_hex(6) + '@example.com'
            user = helpers.api(base, 'POST', '/auth/register', data={'name': 'Workflow owner', 'email': email, 'password': PASSWORD})
            member = helpers.api(base, 'POST', '/auth/register', data={'name': 'Workflow teammate', 'email': 'workflow-member-' + secrets.token_hex(6) + '@example.com', 'password': PASSWORD})
            workspace = helpers.api(base, 'POST', '/workspaces', user, {'name': 'Workflow studio'})
            channel = helpers.api(base, 'POST', '/channels', user, {'name': 'Review studio', 'workspace_id': workspace['id']})
            invitation = helpers.api(base, 'POST', '/channels/' + channel['id'] + '/invites', user, {})
            helpers.api(base, 'POST', '/channels/invites/accept', member, {'token': invitation['token']})
            project = helpers.api(base, 'POST', '/channels/' + channel['id'] + '/projects', user,
                {'id': 'private-workflow-project', 'fields': {'title': 'Delivery', 'description': '', 'color': 'sage'}})
            prefix = '/workspaces/' + workspace['id'] + '/channels/' + channel['id'] + '/workflows'
            def worker():
                stream = (private / ('worker-' + str(len(processes)) + '.log')).open('w'); streams.append(stream)
                process = subprocess.Popen([python, '-m', 'app.services.integration_worker', '--interval', '1'], cwd=ROOT / 'backend', env=environment,
                    stdout=stream, stderr=stream, start_new_session=True)
                processes.append(process); return process
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1100}, reduced_motion='reduce')
                page = context.new_page(); page.set_default_timeout(30000); page_errors = []
                page.on('pageerror', lambda error: page_errors.append(type(error).__name__))
                try:
                    stage = 'native sign-in and visual workflow builder'
                    page.goto(app, wait_until='domcontentloaded')
                    page.evaluate('([key,value]) => localStorage.setItem(key,value)', ['freecollab.workspace:' + user['user_id'], workspace['id']])
                    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
                    page.get_by_label('Email', exact=True).fill(email); page.get_by_label('Password', exact=True).fill(PASSWORD)
                    page.get_by_role('button', name='Sign In', exact=True).click()
                    page.get_by_role('button', name='Open workspace tools', exact=True).click()
                    panel = page.get_by_role('dialog', name='Workspace tools', exact=True)
                    panel.get_by_role('tab', name='Workflows', exact=True).click()
                    panel.get_by_role('button', name='Create workflow', exact=True).click()
                    editor = page.get_by_role('form', name='Workflow editor', exact=True)
                    editor.get_by_label('Workflow name', exact=True).fill('File review and delivery')
                    editor.get_by_label('When', exact=True).select_option('file_uploaded')
                    editor.get_by_label('Reminder every (minutes)', exact=True).fill('1')
                    editor.get_by_label('Expire after (minutes)', exact=True).fill('10')
                    editor.get_by_label('Maximum reminders', exact=True).fill('1')
                    editor.get_by_role('group', name='Escalate after the last reminder (optional)', exact=True).get_by_label('Workflow teammate', exact=True).check()
                    editor.get_by_role('button', name='Add action', exact=True).click()
                    editor.get_by_label('Action 2 type', exact=True).select_option('notify')
                    editor.get_by_role('button', name='Add action', exact=True).click()
                    editor.get_by_label('Action 3 type', exact=True).select_option('create_task')
                    editor.get_by_label('Project', exact=True).select_option(project['id'])
                    editor.get_by_label('Task title', exact=True).fill('Deliver approved file')
                    editor.get_by_role('button', name='Save workflow', exact=True).click()
                    confirmation = page.get_by_role('form', name='Confirm workflow configuration', exact=True)
                    confirmation.get_by_label('FreeCollab password', exact=True).fill(PASSWORD)
                    with page.expect_response(lambda response: urlsplit(response.url).path == '/api' + prefix and response.request.method == 'POST') as saved:
                        confirmation.get_by_role('button', name='Confirm', exact=True).click()
                    assert saved.value.status == 201
                    definition = saved.value.json()
                    expect(panel.get_by_text('File review and delivery', exact=True)).to_be_visible()
                    results['visual_three_step_builder_password_confirmed'] = True

                    stage = 'actual file picker queues metadata without enabled webhooks'
                    panel.get_by_role('button', name='Close workspace tools', exact=True).click()
                    page.get_by_role('button', name='Channel files', exact=True).click()
                    files = page.get_by_role('dialog', name='Channel files', exact=True)
                    with page.expect_response(lambda response: urlsplit(response.url).path == '/api/files/channels/' + channel['id'] and response.request.method == 'POST') as uploaded:
                        page.get_by_label('Choose a file', exact=True).set_input_files({'name': 'Review.txt', 'mimeType': 'text/plain', 'buffer': b'Private review attachment'})
                    assert uploaded.value.status in (200, 201)
                    file = uploaded.value.json()
                    files.get_by_role('button', name='Close channel panel', exact=True).click()
                    run = eventually(lambda: helpers.api(base, 'GET', prefix + '/runs/history', user)['runs'], lambda rows: len(rows) == 1,
                        'Native upload did not enqueue a workflow')[0]
                    assert run['resource_id'] == file['id'] and run['status'] == 'queued'
                    process = worker()
                    run = eventually(lambda: helpers.api(base, 'GET', prefix + '/runs/' + run['id'], user),
                        lambda value: value['status'] == 'waiting_approval', 'Worker did not request approval')
                    results['actual_file_picker_metadata_trigger_while_webhooks_disabled'] = True

                    stage = 'real worker restart and bounded reminder/escalation dispatch'
                    helpers.stop([process])
                    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat(' ')
                    with sqlite3.connect(private / 'proof.db', timeout=20) as database:
                        database.execute('UPDATE workflowrun SET due_at=?, next_reminder_at=? WHERE id=?', (past, past, run['id']))
                    worker()
                    run = eventually(lambda: helpers.api(base, 'GET', prefix + '/runs/' + run['id'], user),
                        lambda value: value['reminder_count'] == 1 and value['escalated'], 'Restarted worker did not dispatch reminder and escalation')
                    assert sum(item['kind'] == 'reminder_sent' for item in run['activity']) == 1
                    results['durable_worker_restart_reminder_and_escalation'] = True

                    stage = 'actual approval and in-app/task actions with retained run history'
                    page.get_by_role('button', name='Open workspace tools', exact=True).click()
                    panel.get_by_role('tab', name='Workflows', exact=True).click()
                    expect(panel.get_by_role('button', name='Approve', exact=True)).to_be_visible()
                    panel.get_by_label('Approval note (optional)', exact=True).fill('Approved from the private browser proof')
                    panel.get_by_role('button', name='Approve', exact=True).click()
                    run = eventually(lambda: helpers.api(base, 'GET', prefix + '/runs/' + run['id'], user),
                        lambda value: value['status'] == 'completed', 'Approval did not complete workflow actions')
                    assert run['votes'][0]['note'] == 'Approved from the private browser proof'
                    tasks = helpers.api(base, 'GET', '/channels/' + channel['id'] + '/projects/' + project['id'] + '/tasks', user)['items']
                    assert len(tasks) == 1 and tasks[0]['fields']['title'] == 'Deliver approved file'
                    with sqlite3.connect(private / 'proof.db', timeout=20) as database:
                        stored_note = database.execute('SELECT encrypted_note FROM workflowvote WHERE run_id=?', (run['id'],)).fetchone()[0]
                        assert 'Approved from' not in stored_note
                        notices = database.execute('SELECT title,body FROM notification WHERE entity_type=? AND entity_id=?', ('workflow_run', run['id'])).fetchall()
                        assert len(notices) >= 5 and all(body is None and 'File review' not in title for title, body in notices)
                    panel.get_by_role('button', name='Refresh workflows', exact=True).click()
                    expect(panel.get_by_text('Completed', exact=True)).to_be_visible()
                    panel.get_by_role('button', name='Run details', exact=True).click()
                    details = panel.get_by_role('region', name='Workflow run details', exact=True)
                    expect(details.get_by_text('Approved from the private browser proof', exact=True)).to_be_visible()
                    expect(details.get_by_text('Reminder sent', exact=True)).to_be_visible()
                    panel.get_by_role('button', name='Versions', exact=True).click()
                    expect(panel.get_by_role('region', name='Workflow version history', exact=True).get_by_text('Revision 1', exact=True)).to_be_visible()
                    results['approval_note_encrypted_at_rest_generic_current_member_notices'] = True
                    results['real_task_action_and_run_choice_activity_revision_history'] = True

                    stage = '320px responsive workflow history and editor'
                    page.set_viewport_size({'width': 320, 'height': 900})
                    panel.get_by_role('button', name='Create workflow', exact=True).click()
                    expect(editor).to_be_visible()
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    assert panel.evaluate('element => element.scrollWidth <= element.clientWidth + 1')
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(REPORT.with_name('workflows-mobile.png')))
                    assert not page_errors, 'Browser page exception'
                    results['mobile_320px_workflow_editor_history_zero_browser_errors'] = True
                    print('PASS visual workflow builder, native file trigger, restarted durable worker reminder/escalation, approval, task and history, 320px mobile', flush=True)
                finally: context.close(); browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+', '[redacted]', str(failure).replace(PASSWORD, '[redacted]'))
            print('FAIL workflows browser stage: ' + stage + '; exception=' + type(failure).__name__ + '\n' + detail[:2500], file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Actual disposable native API/Vite/Chromium and integration worker. Private scheduler timestamp advancement only; no Matrix/external services.',
        'results': results, 'cleanup': 'Own API/Vite/worker processes, browser contexts, SQLite storage, files and keys removed in finally'}, indent=2) + '\n')


if __name__ == '__main__': main()
