#!/usr/bin/env python3
"""Two managed organizations, real Connect HTTP and trusted-frame Matrix crypto.

The identities use real signed workspace-scoped JWTs issued by this isolated
fixture against independent customer-provider registrations. This proof does not
claim two live IdP popup round trips; embed-browser-proof.py covers actual HTTPS
OIDC popup transport separately. No Matrix operation or browser crypto is mocked.
"""
import argparse
import base64
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright
import requests
import socketio

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'packages/components'
PYTHON = os.getenv('BACKEND_PYTHON') or str(ROOT / 'backend/.venv/bin/python')
REPORT = Path(os.getenv('CONNECT_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/connect-browser-0.12.0.json')))
EXPECTED_PACKAGE = '2aaf40a5bcfb4b0a184ec88f92c1a1711f5a6792daf3d343b0203ad10380b91d'
SPEC = importlib.util.spec_from_file_location('owned_embed_proof_helpers', Path(__file__).with_name('embed-browser-proof.py'))
helpers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helpers)


def request(base, path, body=None, token=None, *, method=None, origin=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if origin:
        headers['Origin'] = origin
    payload = json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(Request(base + path, data=payload, headers=headers, method=method), timeout=90) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None
    except HTTPError as error:
        # Return only status and the server's bounded response, never request
        # headers or the credential-bearing Request object.
        content = error.read()
        return error.code, json.loads(content) if content else None


def succeeds(base, path, body=None, token=None, **kwargs):
    status, result = request(base, path, body, token, **kwargs)
    assert status in {200, 201}, 'An owned production API operation was denied: ' + path + ' (' + str(status) + ')'
    return result


def write_launcher(private, api_port, host_origins):
    launcher = private / 'api.py'
    launcher.write_text('''import os,json
import uvicorn
from sqlmodel import Session
from app.main import socket_app
from app.database import init_db,engine
from app.models.user import User
from app.models.workspace import Workspace,WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
from app.models.workforce import WorkspaceIdentityPolicy
from app.models.embed import EmbedApplication
from app.models.enterprise_governance import Organization,OrganizationWorkspace
from app.services.auth import create_token,token_digest,hash_password
from app.services.workforce import ensure_principal
init_db()
with Session(engine) as session:
    session.add(User(id='operator',name='Fixture installation operator',email='operator@connect-proof.example',password_hash=hash_password('Owned-connect-proof!2026')))
    session.add_all([Workspace(id=identifier,name=name)for identifier,name in [('org-a','Organization A'),('org-b','Organization B')]])
    session.commit()
    for workspace in ['org-a','org-b']:
        session.add(WorkspaceMember(workspace_id=workspace,user_id='operator',role='owner'))
        session.add(Organization(id='governance-'+workspace,name='Governed '+workspace,require_sso=True,created_by='operator'))
        session.flush()
        session.add(OrganizationWorkspace(workspace_id=workspace,organization_id='governance-'+workspace,linked_by='operator'))
        session.add(WorkspaceOIDCProvider(workspace_id=workspace,name='Customer-owned '+workspace,issuer_url='https://'+workspace+'.connect-proof.example',client_id='customer-client-'+workspace,created_by='operator',enabled=True,revision=1,tested_revision=1))
        session.add(WorkspaceIdentityPolicy(workspace_id=workspace,mode='provisioned',identity_claim='sub'))
    session.commit()
    identities={}
    for label,workspace,role in [('alpha','org-a','admin'),('beta','org-b','member'),('b-admin','org-b','admin')]:
        row=ensure_principal(session,workspace,'employee-'+label,'Managed '+label,label+'@connect-proof.example',role=role)
        identities[label]={'user_id':row.user_id,'workspace_id':workspace}
    for workspace,application,parent in ''' + repr(list(zip(('org-a', 'org-b'), ('app-a', 'app-b'), host_origins))) + ''':
        session.add(EmbedApplication(id=application,workspace_id=workspace,name='Customer product '+workspace,parent_origin=parent,created_by='operator',enabled=True,revision=1))
    session.commit()
    for label,value in identities.items():
        token=create_token(value['user_id'],session,auth_methods=['sso'],workspace_scope={'workspace_id':value['workspace_id'],'provider_revision':1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id=value['workspace_id'],provider_revision=1))
        value['access_token']=token
    session.commit()
    with os.fdopen(os.open(os.environ['CONNECT_FIXTURE_TOKENS'],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w')as output:json.dump(identities,output)
uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',proxy_headers=False,access_log=False)
''')
    return launcher


def make_host(web, api_base, app_base, host_origin, identity, application, channel_id):
    workspace, source_token = identity['workspace_id'], identity['access_token']

    class Host(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web), **kwargs)

        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/grant':
                return self.send_error(404)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 4096:
                return self.send_error(400)
            data = json.loads(self.rfile.read(length))
            if data.get('channelId') != channel_id:
                return self.send_error(403)
            status, result = request(api_base, '/workspaces/' + workspace + '/embed/launch',
                {'application_id': application, 'channel_id': channel_id, 'parent_origin': host_origin,
                 'code_challenge': data['codeChallenge']}, source_token, origin=host_origin)
            if status != 200:
                return self.send_error(403, 'The own-organization grant is not active')
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'private, no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    (web / 'index.html').write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Own organization product</title><style>body{margin:0;font:14px system-ui}header{padding:12px}#collaboration{height:860px}iframe{border:0;max-width:100%}</style></head><body><header><h1>Own organization product</h1><button id="mount">Open shared channel</button></header><main id="collaboration"></main><script type="module" src="/host.js"></script></body></html>')
    config = {'appUrl': app_base, 'applicationId': application, 'workspaceId': workspace,
              'channelId': channel_id, 'locale': 'en', 'theme': {'accent': '#884455', 'font': 'Georgia, serif'}}
    (web / 'host.js').write_text("import{mountSecureChat}from'/sdk/embed.js';\n"
        + 'window.events=[];window.bridgeMessages=[];window.fixtureApp=' + json.dumps(app_base) + ';\n'
        + "window.addEventListener('message',event=>{if(event.origin===window.fixtureApp)window.bridgeMessages.push(event.data)});\n"
        + 'const config=' + json.dumps(config) + ';\n'
        + "window.mount=async()=>{window.view?.destroy();window.view=mountSecureChat(document.querySelector('#collaboration'),{...config,onState:state=>window.events.push(state),onError:error=>window.events.push({error:error.code}),grantFactory:async context=>{const response=await fetch('/grant',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...context,signal:undefined}),signal:context.signal});if(!response.ok)throw Error('Own-organization access denied');return response.json()}});await window.view.ready};\n"
        + "document.querySelector('#mount').onclick=()=>void window.mount();\n")
    return Host


def open_devices(frame):
    frame.get_by_role('button', name='End-to-end encrypted', exact=True).click()
    dialog = frame.get_by_role('dialog', name='Encryption devices and recovery', exact=True)
    dialog.get_by_role('button', name='Channel devices', exact=True).click()
    expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
    return dialog


def wait_peer(dialog, peer_id, page):
    row = dialog.locator('.fc-secure-device-list li').filter(has_text='Account ' + peer_id)
    deadline = time.monotonic() + 45
    while row.count() != 1 and time.monotonic() < deadline:
        dialog.get_by_role('button', name='Refresh encryption devices', exact=True).click()
        expect(dialog.locator('.fc-secure-fingerprint code')).to_be_visible(timeout=90000)
        page.wait_for_timeout(250)
    expect(row).to_have_count(1)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='Use the explicitly approved frozen frontend and SDK release artifact')
    args = parser.parse_args()
    if not args.skip_build:
        parser.error('This bounded proof requires approved frozen artifacts; pass --skip-build')
    checks, stage = {}, 'packed artifact and source snapshot'
    processes, streams, servers, contexts, sockets, http_sessions = [], [], [], [], [], []
    browser = None
    container = 'freecollab-connect-proof-' + secrets.token_hex(6)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    archive = PACKAGE / 'artifacts/freecollab-components-0.12.0.tgz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == EXPECTED_PACKAGE
    frontend_dist = ROOT / 'frontend/dist'
    frontend_manifest = {str(path.relative_to(frontend_dist)): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted(frontend_dist.rglob('*')) if path.is_file()}
    sources = [*sorted((ROOT / 'backend/app').rglob('*.py')),
               *sorted((ROOT / 'frontend/src').rglob('*.ts')),
               *sorted((ROOT / 'frontend/src').rglob('*.tsx')),
               *sorted((ROOT / 'frontend/src').rglob('*.css')),
               *sorted((PACKAGE / 'src').glob('*.ts*'))]
    source_snapshot = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    with tempfile.TemporaryDirectory(prefix='freecollab-connect-proof-') as temporary:
        private = Path(temporary)
        try:
            with tarfile.open(archive) as tar:
                assert all(member.name.startswith('package/') and '..' not in Path(member.name).parts for member in tar.getmembers())
                tar.extractall(private / 'packed', filter='data')
            release = json.loads((private / 'packed/package/dist/release.json').read_text())
            assert release['version'] == '0.12.0' and release['apiLine'] == '0.12'
            checks['exact_packed_0_12_sdk_and_frozen_trusted_frontend'] = True
            api_port, app_port, host_a_port, host_b_port = (helpers.port() for _ in range(4))
            api_base, app_base, host_a, host_b = ('http://127.0.0.1:' + str(port) for port in (api_port, app_port, host_a_port, host_b_port))
            environment = dict(os.environ)
            environment.update(ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private / 'proof.db'),
                STORAGE_BACKEND='local', STORAGE_PATH=str(private / 'files'), REDIS_URL='',
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({'connect-proof': base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='connect-proof', MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'),
                MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false',
                SYNC_ENABLED='false', CLOUD_FILES_ENABLED='false', EXTERNAL_MEETINGS_ENABLED='false', COPILOT_ENABLED='false',
                MEETINGS_SFU_PROVIDER='disabled', ENTERPRISE_VAULT_URL='', PUBLIC_APP_URL=app_base,
                CORS_ORIGINS=json.dumps([app_base, host_a, host_b]), PYTHONPATH=str(ROOT / 'backend'),
                CONNECT_FIXTURE_TOKENS=str(private / 'tokens.json'))
            environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            environment['no_proxy'] = ','.join(filter(None, [environment.get('no_proxy', ''), environment['NO_PROXY']]))
            stage = 'new owned pinned private Synapse'
            environment.update(helpers.start_matrix(private, container))
            stage = 'isolated production API and built trusted frontend'
            for command, filename, cwd in [([PYTHON, str(write_launcher(private, api_port, [host_a, host_b]))], 'api.log', ROOT / 'backend'),
                                          (['node', str(helpers.write_preview(private, api_base, app_port))], 'frontend.log', ROOT / 'frontend')]:
                stream = (private / filename).open('w'); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(api_base + '/ready', processes)
            helpers.ready(app_base + '/', processes)
            identities = json.loads((private / 'tokens.json').read_text())
            alpha, beta, admin_b = (identities[name] for name in ('alpha', 'beta', 'b-admin'))
            stage = 'real bilateral Connect invitation and no automatic recipient seat'
            connection = succeeds(api_base, '/connect/workspaces/org-a/connections',
                {'name': 'Cross-organization encrypted proof', 'target_workspace_id': 'org-b'}, alpha['access_token'])
            channel_id, connection_id = connection['channel_id'], connection['id']
            assert connection['secure'] and not connection['secure_sync_pending']
            accepted = succeeds(api_base, '/connect/workspaces/org-b/accept',
                {'token': connection['invitation_token']}, admin_b['access_token'])
            assert accepted['status'] == 'active'
            assert request(api_base, '/channels/' + channel_id, token=admin_b['access_token'])[0] == 403
            assert request(api_base, '/channels/' + channel_id, token=beta['access_token'])[0] == 403
            checks['actual_matrix_creation_bilateral_acceptance_does_not_auto_enroll_partner'] = True
            succeeds(api_base, '/connect/workspaces/org-b/connections/' + connection_id + '/members',
                {'user_id': beta['user_id']}, admin_b['access_token'])
            assert request(api_base, '/connect/workspaces/org-a/connections/' + connection_id + '/members',
                {'user_id': beta['user_id']}, alpha['access_token'])[0] == 403
            projected = succeeds(api_base, '/channels/' + channel_id, token=beta['access_token'])
            assert projected['workspace_id'] == 'org-b' and projected['owner_workspace_id'] == 'org-a' and projected['secure']
            assert request(api_base, '/workspaces/org-a/members', token=beta['access_token'])[0] == 403
            assert request(api_base, '/messages/channels/' + channel_id, {'content': 'Forbidden native fallback'}, beta['access_token'])[0] == 409
            with sqlite3.connect(private / 'proof.db') as database:
                assert database.execute('SELECT workspace_id FROM workspacemember WHERE user_id=?', (beta['user_id'],)).fetchall() == [('org-b',)]
                assert database.execute('SELECT workspace_id FROM workspacemember WHERE user_id=?', (alpha['user_id'],)).fetchall() == [('org-a',)]
                assert database.execute('SELECT count(*) FROM organization WHERE require_sso=1').fetchone()[0] == 2
            checks['own_org_explicit_grant_preserves_recipient_sso_and_canonical_owner'] = True
            checks['partner_directory_and_native_plaintext_fallback_denied'] = True
            for name, origin, host_port, identity, application in [('a', host_a, host_a_port, alpha, 'app-a'), ('b', host_b, host_b_port, beta, 'app-b')]:
                web = private / ('web-' + name); web.mkdir()
                shutil.copytree(private / 'packed/package/dist', web / 'sdk')
                server = ThreadingHTTPServer(('127.0.0.1', host_port), make_host(web, api_base, app_base, origin, identity, application, channel_id))
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); servers.append((server, thread))
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
                pages, frames, traffic, children, matrix_tokens, errors = [], [], [], {}, {}, []
                for label, origin, application in [('alpha', host_a, 'app-a'), ('beta', host_b, 'app-b')]:
                    stage = 'actual own-organization trusted encrypted frame: ' + label
                    context = browser.new_context(viewport={'width':1440,'height':1000}, reduced_motion='reduce'); contexts.append(context)
                    traffic.append([])
                    captured = traffic[-1]
                    context.on('request', lambda request, records=captured: records.append((request.url, request.method, request.post_data or '')))
                    def capture(response, label=label, application=application):
                        if response.status != 200:
                            return
                        path = urlsplit(response.url).path
                        if path == '/api/embed/' + application + '/exchange':
                            children[label] = response.json()['access_token']
                        elif path == '/api/secure/session':
                            matrix_tokens[label] = response.json()['access_token']
                    context.on('response', capture)
                    page = context.new_page(); pages.append(page)
                    page.on('pageerror', lambda error: errors.append(type(error).__name__))
                    page.set_default_timeout(45000)
                    page.goto(origin, wait_until='networkidle')
                    page.get_by_role('button', name='Open shared channel', exact=True).click()
                    frame = page.frame_locator('#collaboration iframe:visible'); frames.append(frame)
                    expect(frame.locator('[data-embed-state="ready"]')).to_be_visible(timeout=90000)
                    expect(frame.locator('.fc-chat')).to_be_visible(timeout=90000)
                assert all(label in children for label in ('alpha','beta'))
                checks['both_organizations_launch_real_trusted_frames_with_own_registered_apps'] = True
                assert succeeds(api_base, '/auth/me', token=children['beta'])['workspace_id'] == 'org-b'
                assert request(api_base, '/workspaces/org-a', token=children['beta'])[0] == 403
                dialogs = [open_devices(frame) for frame in frames]
                fingerprints = [dialog.locator('.fc-secure-fingerprint code').inner_text().replace(' ', '') for dialog in dialogs]
                stage = 'actual cross-organization Matrix device publication and fingerprint verification'
                for index, (dialog, peer) in enumerate(zip(dialogs, (beta, alpha))):
                    row = wait_peer(dialog, peer['user_id'], pages[index])
                    row.get_by_role('button', name='Compare and verify device', exact=True).click()
                    dialog.get_by_label('Fingerprint from the other device', exact=True).fill(fingerprints[1-index])
                    dialog.get_by_label('I checked this fingerprint through a separate trusted channel.', exact=True).check()
                    dialog.get_by_role('button', name='Verify device', exact=True).click()
                    expect(row.get_by_text('Verified', exact=True)).to_be_visible(timeout=90000)
                    dialog.get_by_role('button', name='Done', exact=True).click()
                checks['real_matrix_cross_org_devices_verified_through_shipped_fingerprint_ui'] = True
                plaintexts = ['Organization A encrypted cross-organization proof', 'Organization B encrypted reply from its own SSO and frame']
                for index, plaintext in enumerate(plaintexts):
                    stage = 'real cross-organization Rust send and peer decrypt: ' + str(index + 1)
                    frames[index].locator('textarea').first.fill(plaintext)
                    frames[index].get_by_role('button', name='Send message', exact=True).click()
                    for frame in frames:
                        expect(frame.locator('.fc-chat-message').filter(has_text=plaintext)).to_be_visible(timeout=90000)
                checks['bidirectional_real_rust_matrix_encryption_and_cross_org_peer_decryption'] = True
                records = [record for group in traffic for record in group]
                encrypted = [json.loads(body) for url, method, body in records if '/send/m.room.encrypted/' in url and method in {'PUT','POST'}]
                assert len(encrypted) >= 2 and all(record.get('algorithm') == 'm.megolm.v1.aes-sha2' and record.get('ciphertext') for record in encrypted)
                assert all(plaintext not in body for plaintext in plaintexts for url, _, body in records if '/_matrix/' in url)
                assert not any('/messages/channels/' + channel_id in url for url, _, _ in records)
                for page in pages:
                    assert all(plaintext not in page.evaluate('JSON.stringify(window.bridgeMessages)') for plaintext in plaintexts)
                    assert page.evaluate('localStorage.length===0&&sessionStorage.length===0')
                checks['no_plaintext_matrix_wire_native_fallback_parent_bridge_or_browser_token_storage'] = True
                stage = 'current recipient child and Matrix credentials before revocation'
                assert all(label in matrix_tokens for label in ('alpha','beta'))
                assert request(api_base, '/_matrix/client/v3/sync?timeout=0', token=matrix_tokens['beta'])[0] == 200
                # This session has one fixture-owned loopback destination. Keep
                # the cloud proxy outside that local transport while retaining
                # normal certificate verification and proxy behavior elsewhere.
                assert urlsplit(api_base).hostname == '127.0.0.1'
                socket_http = requests.Session(); socket_http.trust_env = False; http_sessions.append(socket_http)
                peer_socket = socketio.Client(logger=False, engineio_logger=False, http_session=socket_http)
                sockets.append(peer_socket)
                peer_socket.connect(api_base, auth={'token':children['beta']}, transports=['polling'],
                                    headers={'Origin':app_base}, wait_timeout=15)
                assert peer_socket.call('join', {'channel_id':channel_id}, timeout=10).get('ok')
                checks['recipient_child_matrix_gateway_and_actual_socket_join_authorized_before_revoke'] = True
                stage = 'own-organization recipient grant revocation'
                succeeds(api_base, '/connect/workspaces/org-b/connections/' + connection_id + '/members/' + beta['user_id'],
                    token=admin_b['access_token'], method='DELETE')
                assert request(api_base, '/auth/me', token=children['beta'])[0] == 401
                assert request(api_base, '/embed/app-b/renew', {}, children['beta'], origin=app_base)[0] == 401
                assert request(api_base, '/channels/' + channel_id, token=beta['access_token'])[0] == 403
                assert request(api_base, '/_matrix/client/v3/sync?timeout=0', token=matrix_tokens['beta'])[0] == 401
                assert peer_socket.call('join', {'channel_id':channel_id}, timeout=10).get('error')
                if frames[1].locator('[data-embed-state="ready"]').count():
                    frames[1].get_by_role('button', name='Refresh messages', exact=True).click()
                expect(frames[1].locator('[data-embed-state="signed-out"]')).to_be_visible(timeout=45000)
                expect(frames[1].locator('.fc-chat')).to_have_count(0)
                checks['recipient_revoke_denies_existing_child_renewal_matrix_and_socket_and_clears_private_dom'] = True
                assert succeeds(api_base, '/auth/me', token=beta['access_token'])['workspace_id'] == 'org-b'
                assert succeeds(api_base, '/channels/' + channel_id, token=alpha['access_token'])['workspace_id'] == 'org-a'
                assert request(api_base, '/auth/me', token=children['alpha'])[0] == 200
                stage = 'source retains its own content seat after recipient-only revocation'
                after = 'Source organization continues after recipient member access ends'
                frames[0].locator('textarea').first.fill(after)
                frames[0].get_by_role('button', name='Send message', exact=True).click()
                expect(frames[0].locator('.fc-chat-message').filter(has_text=after)).to_be_visible(timeout=90000)
                checks['recipient_revocation_preserves_own_sso_and_source_organization_content_seat'] = True
                stage = 'either administrator can revoke the entire bilateral connection without a seat'
                succeeds(api_base, '/connect/workspaces/org-b/connections/' + connection_id,
                    token=admin_b['access_token'], method='DELETE')
                assert request(api_base, '/auth/me', token=children['alpha'])[0] == 401
                assert request(api_base, '/_matrix/client/v3/sync?timeout=0', token=matrix_tokens['alpha'])[0] == 401
                assert request(api_base, '/channels/' + channel_id, token=alpha['access_token'])[0] == 403
                checks['partner_admin_without_content_seat_can_end_entire_connection_and_source_child'] = True
                assert not errors, 'A browser page error occurred'
                records = [record for group in traffic for record in group]
                assert all(plaintext not in body for plaintext in plaintexts + [after]
                           for url, _, body in records if '/_matrix/' in url)
                secret_values = [value['access_token'] for value in identities.values()] + list(children.values()) + list(matrix_tokens.values())
                assert all(secret not in url for secret in secret_values for url, _, _ in records)
                with sqlite3.connect(private / 'proof.db') as database:
                    assert database.execute('SELECT count(*) FROM message WHERE channel_id=?', (channel_id,)).fetchone()[0] == 0
                checks['zero_browser_page_errors_no_bearer_urls_and_no_native_message_rows'] = True
        except Exception as error:
            # Metadata only: retain completed assertions and exact baseline, not
            # fixture logs, tokens, ciphertext, recovery keys or browser state.
            REPORT.with_name(REPORT.stem + '-failure.json').write_text(json.dumps({
                'stage':stage,'exception_type':type(error).__name__,'checks':checks,
                'packed_sha256':EXPECTED_PACKAGE,
                'source_files':len(source_snapshot),
                'source_aggregate_sha256':hashlib.sha256(json.dumps(source_snapshot,sort_keys=True).encode()).hexdigest(),
                'frontend_dist_sha256':hashlib.sha256(json.dumps(frontend_manifest,sort_keys=True).encode()).hexdigest(),
            },indent=2)+'\n')
            print('FAILED Connect browser proof stage: ' + stage, file=sys.stderr)
            raise
        finally:
            for client in reversed(sockets):
                try: client.disconnect()
                except Exception: pass
            for http_session in reversed(http_sessions):
                http_session.close()
            for context in reversed(contexts):
                try: context.close()
                except Exception: pass
            if browser:
                try: browser.close()
                except Exception: pass
            for server, thread in reversed(servers):
                server.shutdown(); server.server_close(); thread.join(timeout=3)
            for process in reversed(processes):
                if process.poll() is None:
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
            for process in reversed(processes):
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            for stream in streams:
                stream.close()
            try: helpers.docker(['rm','-f',container])
            except subprocess.CalledProcessError: pass
    assert source_snapshot == {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}, 'Product source changed during this proof'
    assert frontend_manifest == {str(path.relative_to(frontend_dist)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(frontend_dist.rglob('*')) if path.is_file()}, 'Trusted frontend artifacts changed during this proof'
    REPORT.write_text(json.dumps({'scope':'Same-installation bilateral Connect across two separately managed organization workspaces, actual production APIs, packed trusted embeds, actual Rust/Matrix crypto and current revocation',
        'identity_fixture':'Real signed workspace-scoped SSO JWTs with current WorkspaceOIDCSession rows and independent enabled customer-provider registrations; not two IdP popup round trips',
        'transport':'One new owned private Synapse; no cross-installation federation or customer services',
        'packed_sha256':EXPECTED_PACKAGE,'package_release':release,'checks':checks,
        'frontend_dist_files':len(frontend_manifest),
        'frontend_dist_sha256':hashlib.sha256(json.dumps(frontend_manifest,sort_keys=True).encode()).hexdigest(),
        'source_files':len(source_snapshot),
        'source_aggregate_sha256':hashlib.sha256(json.dumps(source_snapshot,sort_keys=True).encode()).hexdigest(),
        'source_sha256':source_snapshot,'cleanup':'Owned Synapse, API, preview, two customer host servers, sockets, browser contexts, database, keys and fixture credentials removed; original services retained'},indent=2)+'\n')
    print('Connect browser proof passed: ' + str(len(checks)) + ' checks; actual two-organization Matrix encryption and trusted recipient embed.')


if __name__ == '__main__':
    main()
