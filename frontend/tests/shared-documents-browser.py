#!/usr/bin/env python3
"""Actual two-user Chromium CRDT, offline merge, comments and recovery proof.

CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python frontend/tests/shared-documents-browser.py
Uses only private API/Vite processes, temporary SQLite/keys and native accounts.
It never modifies the retained preview or Matrix homeserver.
"""
import argparse
import base64
import hashlib
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

from playwright.sync_api import expect, sync_playwright
from ui_navigation import activate_chat_feature, capture_work_card, conversation_work_region, close_work_card

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('document_proof_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
PASSWORD = 'Private-shared-document!2026'
REPORT = Path(os.getenv('SHARED_DOCUMENTS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/shared-documents-browser.json')))
TITLE = 'A considered shared page'
BASELINE = 'Shared baseline.\n'


def poll(callback, predicate, description):
    for _ in range(200):
        result = callback()
        if predicate(result): return result
        time.sleep(.2)
    raise AssertionError(description)


def browser_json(page, expression, arguments):
    # Matrix/Rust errors may carry internal object graphs which Chromium's
    # remote-object serializer cannot cross. Return only the public JSON value
    # or the bounded public error message, never SDK objects or key material.
    result = json.loads(page.evaluate('async args=>{try{return JSON.stringify({value:await (' + expression + ')(args)})}catch(error){return JSON.stringify({error:String(error?.message||error)})}}', arguments))
    assert 'error' not in result, result.get('error', '')[:500]
    return result.get('value')


def fixture_matrix_environment():
    """Use an explicitly configured disposable fixture; never inspect a service."""
    names = ['MATRIX_INTERNAL_URL', 'MATRIX_SERVER_NAME', 'MATRIX_REGISTRATION_SHARED_SECRET']
    if all(os.environ.get(name) for name in names):
        return {'MATRIX_ENABLED': 'true', **{name: os.environ[name] for name in names}}
    raise SystemExit('Secure proof requires explicit MATRIX_INTERNAL_URL, MATRIX_SERVER_NAME and MATRIX_REGISTRATION_SHARED_SECRET for an owned disposable fixture.')


def secure_proof(base, private, workspace, users, pages, results, environment, python):
    alice, bob = users; left, right = pages
    for page in pages: page.set_default_timeout(90000)
    assert helpers.api(base, 'GET', '/secure/status', alice)['enabled'], 'The real retained Matrix service is required'
    channel = helpers.api(base, 'POST', '/channels', alice, {'name': 'Encrypted working pages', 'workspace_id': workspace['id'], 'member_ids': [bob['user_id']]})['id']
    for page in pages:
        page.evaluate("""async()=>{
            window.documentProofModule=await import('/src/lib/shared-documents-api.ts');
            window.documentProofApi=documentProofModule.sharedDocumentsApi;
            window.projectProofApi=(await import('/src/lib/project-tasks-api.ts')).projectTasksApi;
            window.workflowProofApi=(await import('/src/lib/workflows-api.ts')).workflowsApi;
            window.documentProofSecure=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();
            // Retry only an explicitly rejected Matrix transaction. Reuse the
            // exact URL, transaction ID and ciphertext; never replay a product
            // mutator or infer success after a network/authorization failure.
            const originalFetch=window.fetch.bind(window);
            window.matrixFixtureRetries=[];
            window.fetch=async(input,init)=>{
                const url=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url,location.href);
                const method=init?.method||(input instanceof Request?input.method:'GET');
                const match=/\\/send\\/m\\.room\\.encrypted\\/([^/]+)$/.exec(url.pathname);
                if(method!=='PUT'||!match)return originalFetch(input,init);
                if(typeof init?.body!=='string')throw Error('Fixture Matrix retries require a replayable ciphertext body.');
                const transaction=decodeURIComponent(match[1]);
                if(!transaction)throw Error('Fixture Matrix retries require an explicit transaction ID.');
                let waited=0;
                for(let attempt=0;;attempt++){
                    const response=await originalFetch(input,init);
                    if(response.status!==429)return response;
                    const failure=await response.clone().json();
                    const delay=failure.retry_after_ms;
                    if(failure.errcode!=='M_LIMIT_EXCEEDED'||!Number.isInteger(delay)||delay<1||delay>30000||attempt>=12||waited+delay>90000)return response;
                    window.matrixFixtureRetries.push({transaction,attempt,delay});
                    waited+=delay;
                    await new Promise(resolve=>setTimeout(resolve,delay));
                }
            };
            const send=window.documentProofSecure.sendEvent;
            window.documentProofSecure.sendEvent=(channel,type,content,transaction)=>send(channel,type,content,transaction||'document-fixture-'+crypto.randomUUID());
        }""")
    left.evaluate('channel=>documentProofSecure.enableChannel(channel,"Private shared work")', channel)
    right.evaluate('channel=>documentProofSecure.getMessages(channel)', channel)
    for page, other in [(left, bob), (right, alice)]:
        devices = poll(lambda: page.evaluate('channel=>documentProofSecure.getDevices(channel)', channel),
                       lambda rows: any(row['user_id'] == other['user_id'] for row in rows), 'Recipient cryptographic device was not published')
        for device in devices:
            if device['user_id'] == other['user_id']: page.evaluate('device=>documentProofSecure.verifyDevice(device,device.fingerprint)', device)
    print('Matrix proof: both real cryptographic devices trusted.', flush=True)
    transaction_requests = {}
    synthetic_limit = {"sent": False}
    def limit_one_transaction(route):
        if route.request.method == 'PUT' and not synthetic_limit['sent']:
            synthetic_limit['sent'] = True
            route.fulfill(status=429, content_type='application/json', body=json.dumps({"errcode": "M_LIMIT_EXCEEDED", "error": "Owned fixture rate limit", "retry_after_ms": 20}))
        else:
            route.continue_()
    left.route('**/api/_matrix/client/v3/rooms/**/send/m.room.encrypted/*', limit_one_transaction)
    def record_transaction(request):
        if request.method == 'PUT' and '/send/m.room.encrypted/' in request.url:
            transaction_requests.setdefault(request.url, []).append(hashlib.sha256((request.post_data or '').encode()).hexdigest())
    for page in pages:
        page.on('request', record_transaction)
    requests = []
    for page in pages:
        page.on('request', lambda request: requests.append(request.post_data or '') if request.method == 'POST' and '/api/channels/' + channel + '/' in request.url else None)
    secret_title, comment_text = 'Private encrypted shared page', 'Only verified channel devices read this review.'
    row = left.evaluate('args=>documentProofApi.create(...args)', [channel, secret_title])
    for page in pages:
        page.evaluate('args=>{window.documentProofSession=new documentProofModule.SharedDocumentSession(...args);documentProofSession.start();}', [channel, row])
        page.wait_for_function('documentProofSession.state().ready', timeout=90000)
    print('Matrix proof: encrypted document verified in both browsers.', flush=True)
    left.evaluate('()=>documentProofSession.replace("body","Alice encrypted contribution.\\n"+"a".repeat(18000))')
    right.evaluate('()=>documentProofSession.replace("body","Bob encrypted contribution.\\n"+"b".repeat(18000))')
    for page in pages:
        page.wait_for_function('()=>{const s=documentProofSession.state();return s.pending===0&&s.body.includes("Alice encrypted contribution.")&&s.body.includes("Bob encrypted contribution.")}', timeout=90000)
    merged = left.evaluate('()=>documentProofSession.state().body')
    assert merged == right.evaluate('()=>documentProofSession.state().body')
    print('Matrix proof: concurrent large encrypted updates converged.', flush=True)
    right.evaluate('args=>documentProofApi.comment(...args)', [channel, row['id'], comment_text])
    poll(lambda: left.evaluate('args=>documentProofApi.comments(...args)', [channel, row['id']]),
         lambda rows: any(value['fields']['text'] == comment_text and value['created_by'] == bob['user_id'] for value in rows), 'Verified peer comment did not decrypt')
    print('Matrix proof: authenticated peer comment decrypted.', flush=True)
    left.evaluate('()=>documentProofSession.saveSnapshot("Private merged milestone")')
    print('Matrix proof: chunked encrypted snapshot saved.', flush=True)
    snapshots = right.evaluate('args=>documentProofApi.snapshots(...args)', [channel, row['id']]); assert len(snapshots) == 1
    right.evaluate('()=>{documentProofSession.stop();documentProofSession=new documentProofModule.SharedDocumentSession(documentProofSession.channelId,documentProofSession.metadata);documentProofSession.start();}')
    right.wait_for_function('()=>documentProofSession.state().ready&&documentProofSession.state().body.includes("Alice encrypted contribution.")&&documentProofSession.state().body.includes("Bob encrypted contribution.")', timeout=90000)
    assert merged == right.evaluate('()=>documentProofSession.state().body')
    print('Matrix proof: encrypted snapshot and append history rebuilt.', flush=True)
    results['real_matrix_two_trusted_devices_concurrent_docs_comments_chunked_snapshot_reload'] = True

    project = browser_json(left, 'args=>projectProofApi.saveProject(...args)', [channel, {'title': 'Private project title', 'description': 'Encrypted project purpose.', 'color': 'sage'}])
    print('Matrix proof: encrypted project created.', flush=True)
    task = browser_json(left, 'args=>projectProofApi.saveTask(...args)', [channel, project['id'], {'title': 'Private assigned task', 'description': 'Only trusted devices read this task.',
        'status': 'todo', 'priority': 'high', 'assignee_user_id': bob['user_id'], 'due_date': '2026-11-12',
        'checklist': [{'id': 'review', 'title': 'Verify the proposal', 'done': False}], 'file_ids': [], 'decision_ids': []}])
    projects = browser_json(right, 'channel=>projectProofApi.projects(channel)', channel); assert projects[0]['fields']['title'] == 'Private project title'
    tasks = browser_json(right, 'args=>projectProofApi.tasks(...args)', [channel, project['id']]); assert tasks[0]['fields']['assignee_user_id'] == bob['user_id']
    fields = {**tasks[0]['fields'], 'status': 'done', 'checklist': [{'id': 'review', 'title': 'Verify the proposal', 'done': True}]}
    revised = browser_json(right, 'args=>projectProofApi.saveTask(...args)', [channel, project['id'], fields, tasks[0]])
    history = browser_json(left, 'args=>projectProofApi.history(...args)', [channel, 'task', project['id'], task['id']])
    assert len(history) == 2 and {record['revision'] for record in history} == {1, 2}
    assert any(record['fields']['status'] == 'done' for record in history)
    results['real_matrix_projects_tasks_assignee_checklist_peer_edit_immutable_history'] = True
    print('Matrix proof: project/task peer edit and immutable revisions verified.', flush=True)

    def workflow_tick():
        source = 'from app import main\nfrom app.database import engine\nfrom sqlmodel import Session\nfrom app.services.workflow import run_pending\nwith Session(engine) as session: run_pending(session)\n'
        result = subprocess.run([python, '-c', source], cwd=ROOT / 'backend', env=environment, capture_output=True, timeout=45)
        assert result.returncode == 0, 'The private workflow worker must complete successfully'
    workflow_path = '/workspaces/' + workspace['id'] + '/channels/' + channel + '/workflows'
    definition = helpers.api(base, 'POST', workflow_path, alice, {'password': PASSWORD, 'fields': {
        'name': 'Private secure approval', 'trigger': 'manual', 'conditions': {'actor': 'any', 'status': None},
        'steps': [{'kind': 'approval', 'recipients': [bob['user_id']], 'quorum': 1, 'reminder_minutes': 60,
                   'timeout_minutes': 1440, 'max_reminders': 0, 'escalation_recipients': [], 'project_id': None, 'task_title': None}]}})
    run = helpers.api(base, 'POST', workflow_path + '/' + definition['id'] + '/run', alice, {})
    workflow_tick()
    approval = right.evaluate('args=>workflowProofApi.run(...args)', [workspace['id'], channel, run['id']])
    assert approval['may_vote'] and approval['status'] == 'waiting_approval'
    note = 'Only verified devices can read this approval note.'
    vote_requests = []
    right.on('request', lambda request: vote_requests.append(request.post_data or '') if request.method == 'POST' and request.url.endswith('/runs/' + run['id'] + '/vote') else None)
    right.evaluate('args=>workflowProofApi.vote(...args)', [workspace['id'], channel, approval, 'approve', note])
    received = left.evaluate('args=>workflowProofApi.run(...args)', [workspace['id'], channel, run['id']])
    assert received['votes'][0]['note'] == note and received['votes'][0]['user_id'] == bob['user_id']
    native_run = helpers.api(base, 'GET', workflow_path + '/runs/' + run['id'], alice)
    assert native_run['votes'][0]['note'] is None and native_run['votes'][0]['matrix_event_id']
    assert len(vote_requests) == 1 and set(json.loads(vote_requests[0])) == {'choice', 'matrix_event_id'} and note not in vote_requests[0]
    approval_wire = left.evaluate('''async args=>{const event=await documentProofSecure.getDecryptedEvent(args.channel,args.id);return {type:event.getWireType(),wire:JSON.stringify(event.getWireContent())}}''', {'channel': channel, 'id': native_run['votes'][0]['matrix_event_id']})
    assert approval_wire['type'] == 'm.room.encrypted' and note not in approval_wire['wire']
    workflow_tick()
    assert helpers.api(base, 'GET', workflow_path + '/runs/' + run['id'], alice)['status'] == 'completed'
    results['real_matrix_workflow_approval_note_peer_decrypt_opaque_api_wire_worker_completion'] = True

    endpoint = '/channels/' + channel + '/shared-documents/' + row['id']
    updates = helpers.api(base, 'GET', endpoint + '/updates', alice)['items']
    comments = helpers.api(base, 'GET', endpoint + '/comments', bob)
    saved = helpers.api(base, 'GET', endpoint + '/snapshots', alice)
    native_projects = helpers.api(base, 'GET', '/channels/' + channel + '/projects', alice)
    native_tasks = helpers.api(base, 'GET', '/channels/' + channel + '/projects/' + project['id'] + '/tasks', bob)['items']
    native_history = helpers.api(base, 'GET', '/channels/' + channel + '/projects/' + project['id'] + '/tasks/' + task['id'] + '/revisions', alice)
    all_records = updates + comments + saved + native_projects + native_tasks + native_history
    assert all(record['content_mode'] == 'matrix' and record['matrix_event_id'] and 'fields' not in record for record in all_records)
    sentinels = [secret_title, comment_text, 'Alice encrypted contribution.', 'Bob encrypted contribution.', 'Private merged milestone', 'Private project title', 'Private assigned task', 'Verify the proposal']
    assert requests and all(all(secret not in body for secret in sentinels) and 'fields' not in json.loads(body) for body in requests)
    part_counts = []
    for record in all_records:
        wire = right.evaluate('''async args=>{const event=await documentProofSecure.getDecryptedEvent(args.channel,args.id);const fields=event.getContent().fields;return {type:event.getWireType(),wire:JSON.stringify(event.getWireContent()),parts:fields?.parts||[]}}''', {'channel': channel, 'id': record['matrix_event_id']})
        assert wire['type'] == 'm.room.encrypted' and all(secret not in wire['wire'] for secret in sentinels)
        part_counts.append(len(wire['parts']))
        for event_id in wire['parts']:
            part = right.evaluate('''async args=>{const event=await documentProofSecure.getDecryptedEvent(args.channel,args.id);return {type:event.getWireType(),wire:JSON.stringify(event.getWireContent())}}''', {'channel': channel, 'id': event_id})
            assert part['type'] == 'm.room.encrypted' and all(secret not in part['wire'] for secret in sentinels)
    assert sum(count > 0 for count in part_counts) >= 3, 'Both large updates and the merged full-state snapshot must use encrypted parts'
    with sqlite3.connect(private / 'proof.db') as connection:
        for table in ['shareddocumentupdate', 'shareddocumentcomment', 'shareddocumentsnapshot']:
            values = connection.execute('SELECT content_mode, encrypted_payload, matrix_event_id FROM ' + table + ' WHERE document_id=?', (row['id'],)).fetchall()
            assert values and all(mode == 'matrix' and payload is None and event for mode, payload, event in values)
        values = connection.execute('SELECT content_mode, encrypted_payload, matrix_event_id FROM projecttaskrevision WHERE item_id IN (?,?)', (project['id'], task['id'])).fetchall()
        assert len(values) == 3 and all(mode == 'matrix' and payload is None and event for mode, payload, event in values)
        summary = connection.execute('SELECT summary_encrypted_payload,summary_event_id FROM shareddocument WHERE id=?', (row['id'],)).fetchone()
        assert summary[0] is None and summary[1]
        vote = connection.execute('SELECT encrypted_note,matrix_event_id FROM workflowvote WHERE run_id=?', (run['id'],)).fetchone()
        assert vote[0] is None and vote[1] == native_run['votes'][0]['matrix_event_id']
    for page in pages: page.evaluate('()=>documentProofSession.stop()')
    retries = [retry for page in pages for retry in page.evaluate('matrixFixtureRetries')]
    repeated = [hashes for hashes in transaction_requests.values() if len(hashes) > 1]
    assert synthetic_limit['sent'] and retries and repeated, 'The transport must exercise an explicitly rejected transaction'
    assert all(len(set(hashes)) == 1 for hashes in repeated), 'A retry changed its encrypted transaction body'
    results['matrix_429_retries_preserve_exact_transaction_and_ciphertext'] = True
    print('PASS Matrix rate limits: ' + str(len(retries)) + ' bounded declared-delay retries retain the exact transaction URL and ciphertext; no product mutator replay', flush=True)
    results['matrix_all_native_api_db_opaque_and_actual_encrypted_wire_including_parts'] = True
    print('PASS real Matrix: two trusted Rust devices, concurrent large documents, encrypted chunks/comments/snapshot, rebuilt history, private project/task assignment/checklist/peer edit/history, workflow approval note/worker completion, opaque API/DB and m.room.encrypted wire', flush=True)


def open_documents(page):
    from ui_navigation import open_conversation_work
    panel = open_conversation_work(page, 'documents')
    expect(panel).to_have_attribute('data-work-kind', 'documents')
    expect(panel.get_by_role('tablist')).to_have_count(0)
    return panel


def login(page, app, email, workspace_id, user_id):
    page.goto(app, wait_until='domcontentloaded')
    page.evaluate('([key,value]) => localStorage.setItem(key,value)', ['freecollab.workspace:' + user_id, workspace_id])
    page.get_by_role('navigation').get_by_role('button', name='Open App', exact=True).click()
    page.get_by_label('Email', exact=True).fill(email)
    page.get_by_label('Password', exact=True).fill(PASSWORD)
    page.get_by_role('button', name='Sign In', exact=True).click()
    return open_documents(page)


def select_document(panel):
    panel.get_by_role('button', name=re.compile('^' + re.escape(TITLE))).click()
    expect(panel.locator('.fc-shared-reading-page')).to_be_visible()
    expect(panel.locator('.fc-shared-reading-title')).to_have_text(TITLE)
    expect(panel.get_by_label('Document text', exact=True)).to_be_hidden()
    panel.get_by_role('button', name='Edit document', exact=True).click()
    expect(panel.get_by_label('Document text', exact=True)).to_be_editable()
    expect(panel.get_by_text('All edits synchronized', exact=True)).to_be_visible()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--secure', action='store_true', help='Also prove actual Matrix documents/projects/tasks using an explicitly configured disposable homeserver.')
    options = parser.parse_args()
    stage, results, processes, streams = 'private services startup', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-shared-documents-') as directory:
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
                OIDC_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', PYTHONPATH=str(ROOT / 'backend'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            if options.secure: environment.update(fixture_matrix_environment())
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').is_file() else sys.executable)
            server = private / 'api.py'
            server.write_text('import uvicorn\nfrom app.main import socket_app\nuvicorn.run(socket_app,host="127.0.0.1",port=' + str(api_port) + ',proxy_headers=False,access_log=False)\n')
            # Freeze the current UI tree so unrelated panel edits cannot trigger
            # a hot reload that discards the proof's intentionally offline edits.
            frontend = private / 'frontend'
            shutil.copytree(ROOT / 'frontend', frontend, ignore=shutil.ignore_patterns('node_modules', 'dist', '__pycache__', '.git'))
            (frontend / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
            vite = helpers.vite_launcher(private, base, app_port)
            vite_source = vite.read_text().replace('root:' + json.dumps(str(ROOT / 'frontend')), 'root:' + json.dumps(str(frontend)))
            vite_source = vite_source.replace(json.dumps(str(private / 'vite-cache')), json.dumps(str(frontend / '.vite-cache')))
            vite_source = vite_source.replace("server:{host:", 'server:{fs:{allow:' + json.dumps([str(private), str(ROOT / 'frontend')]) + '},host:')
            vite.write_text(vite_source)
            for command, filename, cwd in [([python, str(server)], 'api.log', ROOT / 'backend'), (['node', str(vite)], 'vite.log', frontend)]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(base + '/ready', processes); helpers.ready(app + '/', processes)
            stage = 'two private members and shared native channel'
            users, emails = [], []
            for name in ['Alice document proof', 'Bob document proof']:
                email = 'document-' + secrets.token_hex(6) + '@example.com'; emails.append(email)
                users.append(helpers.api(base, 'POST', '/auth/register', data={'name': name, 'email': email, 'password': PASSWORD}))
            alice, bob = users
            workspace = helpers.api(base, 'POST', '/workspaces', alice, {'name': 'Shared writing studio'})
            channel = helpers.api(base, 'POST', '/channels', alice, {'name': 'Living documents', 'workspace_id': workspace['id']})
            invitation = helpers.api(base, 'POST', '/channels/' + channel['id'] + '/invites', alice, {})
            helpers.api(base, 'POST', '/channels/invites/accept', bob, {'token': invitation['token']})
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox'])
                contexts = [browser.new_context(accept_downloads=True, viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce') for _ in users]
                pages = [context.new_page() for context in contexts]; errors = []
                for page in pages:
                    page.set_default_timeout(30000)
                    page.on('pageerror', lambda failure: errors.append(type(failure).__name__))
                try:
                    stage = 'actual native sign-in and document creation'
                    first = login(pages[0], app, emails[0], workspace['id'], alice['user_id'])
                    first.get_by_role('button', name='New shared document', exact=True).click()
                    first.get_by_label('Document title', exact=True).fill(TITLE)
                    first.get_by_role('button', name='Create document', exact=True).click()
                    expect(first.get_by_label('Document text', exact=True)).to_be_editable()
                    first.get_by_label('Document text', exact=True).fill(BASELINE)
                    expect(first.get_by_text('All edits synchronized', exact=True)).to_be_visible()
                    first.locator('details.fc-shared-flow-options > summary').click()
                    first.get_by_role('button', name='Named revisions', exact=True).click()
                    first.get_by_label('Revision name', exact=True).fill('Before concurrent edits')
                    first.get_by_role('button', name='Save revision', exact=True).click()
                    expect(first.get_by_text('Before concurrent edits', exact=True)).to_be_visible()
                    second = login(pages[1], app, emails[1], workspace['id'], bob['user_id']); select_document(second)
                    expect(second.get_by_label('Document text', exact=True)).to_have_value(BASELINE)
                    records = helpers.api(base, 'GET', '/channels/' + channel['id'] + '/shared-documents', alice)
                    assert len(records) == 1
                    document_id = records[0]['id']; endpoint = '/channels/' + channel['id'] + '/shared-documents/' + document_id
                    results['real_two_member_editor_and_named_baseline'] = True

                    stage = 'read/edit retains the actual shared editor without publishing a view change'
                    editor = first.get_by_label('Document text', exact=True)
                    retained = editor.element_handle()
                    editor.focus(); editor.press('Home'); editor.press('Shift+ArrowRight'); editor.press('Shift+ArrowRight')
                    selection = editor.evaluate('node=>({start:node.selectionStart,end:node.selectionEnd,top:node.scrollTop})')
                    before_view_updates = helpers.api(base, 'GET', endpoint + '/updates', alice)
                    first.get_by_role('button', name='Done editing', exact=True).click()
                    expect(first.locator('.fc-shared-reading-page')).to_be_visible()
                    expect(first.locator('.fc-shared-reading-body')).to_have_text(BASELINE)
                    assert retained.evaluate('node=>node.isConnected'), 'Reading view remounted the real Yjs editor'
                    expect(editor).to_be_hidden()
                    first.get_by_role('button', name='Edit document', exact=True).click()
                    assert editor.evaluate('(node,previous)=>node===previous', retained), 'Edit reopened a different textarea'
                    expect(editor).to_be_focused()
                    expect(editor).to_have_js_property('selectionStart', selection['start'])
                    expect(editor).to_have_js_property('selectionEnd', selection['end'])
                    expect(editor).to_have_js_property('scrollTop', selection['top'])
                    after_view_updates = helpers.api(base, 'GET', endpoint + '/updates', alice)
                    assert after_view_updates == before_view_updates, 'Read/edit controls published a document update'
                    results['saved_reading_page_and_same_editor_selection_without_view_writes'] = True

                    stage = 'simultaneous disconnected edits with no last-write-wins replacement'
                    for context in contexts: context.set_offline(True)
                    first.get_by_label('Document text', exact=True).fill(BASELINE + 'Alice concurrent note.\n')
                    second.get_by_label('Document text', exact=True).fill(BASELINE + 'Bob concurrent note.\n')
                    for panel in [first, second]:
                        expect(panel.get_by_label('Document text', exact=True)).to_be_editable()
                        expect(panel.get_by_text(re.compile('edit[s]? waiting to sync'))).to_be_visible()
                        expect(panel.get_by_role('button', name='Retry synchronization', exact=True)).to_be_visible()
                    first_editor = first.get_by_label('Document text', exact=True).element_handle()
                    second_editor = second.get_by_label('Document text', exact=True).element_handle()
                    queued_before = helpers.api(base, 'GET', endpoint + '/updates', alice)
                    sibling_writes, sibling_dialogs = [], []
                    def record_sibling_write(request):
                        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and any(part in request.url for part in ('/projects', '/messages/channels/', '/mobile/offline/channels/', '/threads/messages/')): sibling_writes.append(request.url)
                    def reject_sibling_discard(dialog):
                        sibling_dialogs.append(dialog.message); dialog.dismiss()
                    pages[0].on('request', record_sibling_write)
                    pages[0].on('dialog', reject_sibling_discard)
                    activate_chat_feature(pages[0], 'task')
                    sibling = conversation_work_region(pages[0], kind='tasks')
                    assert capture_work_card(pages[0], sibling).get_attribute('data-collaboration-work-id') != capture_work_card(pages[0], first).get_attribute('data-collaboration-work-id')
                    expect(first).to_have_attribute('data-work-kind', 'documents')
                    expect(first.get_by_label('Document text', exact=True)).to_have_value(BASELINE + 'Alice concurrent note.\n')
                    expect(second.get_by_label('Document text', exact=True)).to_have_value(BASELINE + 'Bob concurrent note.\n')
                    assert first.get_by_label('Document text', exact=True).evaluate('(node,original)=>node===original&&original.isConnected', first_editor)
                    assert second.get_by_label('Document text', exact=True).evaluate('(node,original)=>node===original&&original.isConnected', second_editor)
                    close_work_card(pages[0], sibling)
                    assert not sibling_dialogs, 'Opening and closing a clean sibling must retain both queued document editors'
                    assert not sibling_writes, 'Opening a sibling must not create native tasks or deliver messages'
                    assert helpers.api(base, 'GET', endpoint + '/updates', alice) == queued_before, 'Opening a sibling must not flush either real offline document edit'
                    pages[0].remove_listener('request', record_sibling_write)
                    pages[0].remove_listener('dialog', reject_sibling_discard)
                    results['offline_editing_and_queued_yjs_survive_independent_sibling_work'] = True
                    for context in reversed(contexts): context.set_offline(False)
                    for panel in [first, second]:
                        editor = panel.get_by_label('Document text', exact=True)
                        expect(editor).to_have_value(re.compile('Alice concurrent note\.'))
                        expect(editor).to_have_value(re.compile('Bob concurrent note\.'))
                        expect(panel.get_by_text('All edits synchronized', exact=True)).to_be_visible()
                    merged = first.get_by_label('Document text', exact=True).input_value()
                    assert merged == second.get_by_label('Document text', exact=True).input_value()
                    assert merged.count('Shared baseline.') == 1
                    results['two_disconnected_yjs_edits_converge_without_loss'] = True

                    stage = 'authenticated comment and exact local Markdown export'
                    second.get_by_label('Add a comment', exact=True).fill('Bob reviewed both concurrent notes.')
                    second.get_by_role('button', name='Post comment', exact=True).click()
                    expect(second.get_by_text('Bob reviewed both concurrent notes.', exact=True)).to_be_visible()
                    expect(first.get_by_text('Bob reviewed both concurrent notes.', exact=True)).to_be_visible()
                    expect(first.get_by_role('region', name='Document comments', exact=True).get_by_text('Bob document proof', exact=True)).to_be_visible()
                    assert first.get_by_role('button', name='Resolve comment', exact=True).count() == 1
                    with pages[0].expect_download() as exported:
                        first.get_by_role('button', name='Export Markdown', exact=True).click()
                    target = private / 'export.md'; exported.value.save_as(str(target))
                    assert target.read_text() == '# ' + TITLE + '\n\n' + merged + '\n'
                    results['authenticated_comment_and_exact_local_markdown_export'] = True

                    stage = 'fresh browser reload reconstructs concurrent history'
                    pages[1].reload(wait_until='domcontentloaded'); second = open_documents(pages[1]); select_document(second)
                    expect(second.get_by_label('Document text', exact=True)).to_have_value(merged)
                    expect(second.get_by_text('Bob reviewed both concurrent notes.', exact=True)).to_be_visible()
                    before = helpers.api(base, 'GET', endpoint + '/updates', alice)
                    results['fresh_reload_reconstructs_both_concurrent_changes'] = True

                    stage = 'immutable named revision restored as new edits'
                    first.get_by_role('button', name='Restore text', exact=True).click()
                    first.get_by_role('button', name='Restore revision text', exact=True).click()
                    expect(first.get_by_label('Document text', exact=True)).to_have_value(BASELINE)
                    expect(second.get_by_label('Document text', exact=True)).to_have_value(BASELINE)
                    expect(first.get_by_text('All edits synchronized', exact=True)).to_be_visible()
                    after = helpers.api(base, 'GET', endpoint + '/updates', alice)
                    assert after['document']['update_count'] > before['document']['update_count']
                    assert after['items'][:len(before['items'])] == before['items']
                    assert len(helpers.api(base, 'GET', endpoint + '/snapshots', bob)) == 1
                    expect(first.get_by_text('Bob reviewed both concurrent notes.', exact=True)).to_be_visible()
                    results['restore_appends_new_crdt_edits_preserving_updates_comments_snapshot'] = True

                    stage = 'native at-rest content and 320px layout'
                    with sqlite3.connect(private / 'proof.db') as connection:
                        ciphertexts = [value for row in connection.execute('SELECT encrypted_payload FROM shareddocumentupdate UNION ALL SELECT encrypted_payload FROM shareddocumentcomment UNION ALL SELECT encrypted_payload FROM shareddocumentsnapshot UNION ALL SELECT summary_encrypted_payload FROM shareddocument') for value in row if value]
                        assert ciphertexts and all('Bob reviewed' not in value and TITLE not in value and BASELINE not in value for value in ciphertexts)
                    pages[0].set_viewport_size({'width': 320, 'height': 900})
                    assert pages[0].evaluate('() => document.documentElement.scrollWidth <= innerWidth + 1')
                    assert first.evaluate('element => element.scrollWidth <= element.clientWidth + 1')
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    pages[0].screenshot(path=str(REPORT.with_name('shared-documents-mobile.png')))
                    assert not errors, 'Unexpected Chromium page exception'
                    results['native_encrypted_storage_mobile_320px_zero_browser_errors'] = True
                    print('PASS shared documents: two real members, offline concurrent Yjs merge, reload, authenticated comments, local export, immutable revision recovery, native encrypted storage and 320px UI', flush=True)
                    if options.secure:
                        stage = 'actual retained Matrix documents, comments, snapshots, projects and tasks'
                        secure_proof(base, private, workspace, users, pages, results, environment, python)
                finally:
                    for context in contexts: context.close()
                    browser.close()
        except Exception as failure:
            detail = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+', '[redacted]', str(failure).replace(PASSWORD, '[redacted]'))
            print('FAIL shared-documents stage: ' + stage + '; exception=' + type(failure).__name__ + '\n' + detail[:2500], file=sys.stderr, flush=True)
            if (private / 'vite.log').exists():
                diagnostics = (private / 'vite.log').read_text()[-4000:]
                print('Private Vite diagnostics:\n' + diagnostics, file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        finally:
            helpers.stop(processes)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'scope': 'Actual private API/Vite/Chromium with two users and real network disconnection' + ('; explicitly configured disposable Matrix with isolated identities and real trusted devices' if options.secure else '; native only') + '; no external provider', 'results': results,
        'cleanup': 'Own API/Vite processes, SQLite, cryptographic keys, files, browser contexts and temporary exports removed in finally'}, indent=2) + '\n')


if __name__ == '__main__': main()
