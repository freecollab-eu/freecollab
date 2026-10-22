#!/usr/bin/env python3
"""Real Rust E2EE proof for a separate, passwordless managed SSO identity.

Requires a running private Matrix deployment, backend dev dependencies, Chromium
and the frontend Vite server. Pass Matrix connection settings in the environment.
Creates its own private API/database and synthetic accounts. OIDC protocol proof
lives in frontend/tests/enterprise-onboarding-browser.py; this proof seeds its managed JWT.
Credentials and recovery keys are never logged; disposable fixtures are removed.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BACKEND_PYTHON = os.getenv('BACKEND_PYTHON') or (str(ROOT / 'backend/.venv/bin/python') if (ROOT / 'backend/.venv/bin/python').exists() else sys.executable)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--app-base', default='http://127.0.0.1:3000')
parser.add_argument('--chromium', default=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'))
args = parser.parse_args()


def request(base, path, body=None, auth=None):
    req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                  headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + auth['access_token']} if auth else {})})
    with urlopen(req, timeout=90) as response:
        return json.load(response)


def poll(callback, predicate, description):
    for _ in range(200):
        value = callback()
        if predicate(value): return value
        time.sleep(.2)
    raise AssertionError(description)


with tempfile.TemporaryDirectory(prefix='freecollab-managed-rust-') as temporary:
    private = Path(temporary)
    env = os.environ.copy()
    assert env.get('MATRIX_REGISTRATION_SHARED_SECRET'), 'Provide private Matrix connection configuration'
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0)); port = server.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env.update(ENVIRONMENT='test', DATABASE_URL=f'sqlite:///{private / "proof.db"}',
               STORAGE_PATH=str(private / 'files'), JWT_SECRET=secrets.token_urlsafe(48),
               MESSAGE_ENCRYPTION_KEY_FILE=str(private / 'message-keys.json'),
               ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
               PUBLIC_APP_URL=args.app_base, CORS_ORIGINS=json.dumps([args.app_base]),
               MAIL_DELIVERY_MODE='disabled', MATRIX_ENABLED='true', OIDC_ENABLED='false')
    with (private / 'api.log').open('wb') as log:
        process = subprocess.Popen([BACKEND_PYTHON, '-m', 'uvicorn', 'app.main:socket_app', '--host', '127.0.0.1', '--port', str(port), '--no-proxy-headers'],
                                   cwd=ROOT / 'backend', env=env, stdout=log, stderr=log, start_new_session=True)
    try:
        for _ in range(200):
            assert process.poll() is None, 'Disposable API exited before readiness'
            try:
                request(base, '/health'); break
            except (URLError, HTTPError): time.sleep(.1)
        else: raise AssertionError('Disposable API did not become ready')
        password = secrets.token_urlsafe(24)
        owner_email = 'owner' + secrets.token_hex(8) + '@example.com'
        owner = request(base, '/auth/register', {'name': 'Native owner', 'email': owner_email, 'password': password})
        workspace = request(base, '/workspaces', {'name': 'Managed customer A'}, owner)['id']
        other_workspace = request(base, '/workspaces', {'name': 'Personal private B'}, owner)['id']
        fixture_env = dict(env, SSO_PROOF_ACTOR=owner['user_id'], SSO_PROOF_WORKSPACE=workspace,
                           SSO_PROOF_EMAIL=owner_email)
        fixture = """import json,os
from app.main import app
from app.database import engine
from sqlmodel import Session
from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
from app.services.workforce import ensure_principal
from app.services.workspaces import lock_workspace
from app.services.auth import create_token,token_digest
with Session(engine) as session:
    workspace,actor=os.environ['SSO_PROOF_WORKSPACE'],os.environ['SSO_PROOF_ACTOR']
    lock_workspace(session,workspace)
    if session.get(WorkspaceOIDCProvider,workspace) is None:
        session.add(WorkspaceOIDCProvider(workspace_id=workspace,name='Synthetic customer IdP',issuer_url='https://idp.example.invalid',client_id='fixture',created_by=actor,enabled=True))
    principal=ensure_principal(session,workspace,'employee-123','Managed colleague',os.environ['SSO_PROOF_EMAIL'])
    user_id=principal.user_id
    session.commit()
    token=create_token(user_id,session,auth_methods=['sso'],workspace_scope={'workspace_id':workspace,'provider_revision':1})
    session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id=workspace,provider_revision=1))
    session.commit()
    print(json.dumps({'user_id':user_id,'access_token':token}))
"""
        def managed_session():
            result = subprocess.run([BACKEND_PYTHON, '-c', fixture], cwd=ROOT / 'backend', env=fixture_env,
                                    capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        scoped = managed_session()
        assert scoped['user_id'] != owner['user_id']
        assert request(base, '/auth/me', auth=scoped)['account_type'] == 'managed'
        assert request(base, '/secure/status', auth=scoped)['requires_encryption_unlock'] is False
        channel = request(base, '/channels', {'name': 'Encrypted A', 'workspace_id': workspace,
                          'member_ids': [scoped['user_id']]}, owner)['id']
        other_channel = request(base, '/channels', {'name': 'Encrypted B', 'workspace_id': other_workspace}, owner)['id']
        print('PASS customer-managed SSO has a separate workspace identity even with the personal owner email; no native-password unlock', flush=True)
        with sync_playwright() as p:
            launch = {'headless': True, 'args': ['--no-sandbox']}
            if args.chromium: launch['executable_path'] = args.chromium
            browser = p.chromium.launch(**launch)
            try:
                def page_for(user):
                    page = browser.new_context().new_page(); page.set_default_timeout(90000)
                    page.goto(args.app_base, wait_until='domcontentloaded')
                    page.evaluate('''async ({auth,base})=>{const {getSecureMessaging}=await import('/src/lib/secure-client.ts');const adapter=await getSecureMessaging();adapter.configure({baseUrl:base,getAccessToken:()=>auth.access_token,getUserId:()=>auth.user_id});window.secureProof=adapter;}''', {'auth': user, 'base': base})
                    return page
                alice, bob = page_for(scoped), page_for(owner)
                binding = bob.evaluate('channel=>secureProof.enableChannel(channel,"Managed customer A")', channel)
                other_binding = bob.evaluate('channel=>secureProof.enableChannel(channel,"Personal private B")', other_channel)
                alice.evaluate('channel=>secureProof.getMessages(channel)', channel)
                for page, other in ((alice, owner), (bob, scoped)):
                    devices = poll(lambda: page.evaluate('channel=>secureProof.getDevices(channel)', channel), lambda rows: any(d['user_id'] == other['user_id'] for d in rows), 'Real Rust recipient device was not published')
                    for device in devices:
                        if device['user_id'] == other['user_id']: page.evaluate('device=>secureProof.verifyDevice(device,device.fingerprint)', device)
                message = 'Customer A message read using managed SSO without a native password'
                sent = bob.evaluate('args=>secureProof.sendMessage(...args)', [channel, message])
                poll(lambda: alice.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == sent['id'] and row['content'] == message for row in rows), 'Scoped Rust browser could not decrypt its workspace message')
                reply = alice.evaluate('args=>secureProof.sendMessage(...args)', [channel, 'Scoped encrypted reply'])
                poll(lambda: bob.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == reply['id'] and row['content'] == 'Scoped encrypted reply' for row in rows), 'Native peer could not decrypt the scoped sender reply')
                wire = alice.evaluate('''async ({channel,event})=>{const e=await secureProof.getDecryptedEvent(channel,event);return {type:e.getWireType(),wire:JSON.stringify(e.getWireContent())}}''', {'channel': channel, 'event': sent['id']})
                assert wire['type'] == 'm.room.encrypted' and message not in wire['wire']
                denied_other = alice.evaluate('''async ({room,base})=>{const c=await secureProof.ensureSession();return (await fetch(base+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/messages?dir=b',{headers:{Authorization:'Bearer '+c.getAccessToken()}})).status}''', {'room': other_binding['room_id'], 'base': base})
                assert denied_other == 403
                assert request(base, '/auth/me', auth=scoped)['session_scope'] == 'workspace'
                print('PASS managed SSO initializes actual Matrix Rust crypto; verified peers decrypt both directions; wire stays encrypted', flush=True)
                print('PASS the separate personal account room remains denied to managed SSO', flush=True)
                private_message = bob.evaluate('args=>secureProof.sendMessage(...args)', [other_channel, 'Personal B content must never become managed keys'])
                private_wire = bob.evaluate('async ({channel,event})=>{const e=await secureProof.getDecryptedEvent(channel,event);return {event_id:e.getId(),room_id:e.getRoomId(),sender:e.getSender(),origin_server_ts:e.getTs(),type:e.getWireType(),content:e.getWireContent()}}', {'channel': other_channel, 'event': private_message['id']})
                def cannot_decrypt(page):
                    return page.evaluate('async wire=>{const c=await secureProof.ensureSession();const e=c.getEventMapper()(wire);try{await c.decryptEventIfNeeded(e);}catch{}return e.isDecryptionFailure();}', private_wire)
                assert cannot_decrypt(alice), 'Managed identity obtained the personal room key'
                print('PASS personal-room ciphertext cannot decrypt in the managed Rust identity', flush=True)
                recovery = alice.evaluate('()=>secureProof.setupRecovery()')
                assert len(recovery) > 32
                restored_auth = managed_session()
                restored = page_for(restored_auth)
                restored.evaluate('key=>secureProof.unlockRecovery(key)', recovery)
                poll(lambda: restored.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == sent['id'] and row['content'] == message for row in rows), 'Fresh scoped Rust browser could not restore its workspace history')
                restored_denied = restored.evaluate('''async ({room,base})=>{const c=await secureProof.ensureSession();return (await fetch(base+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/messages?dir=b',{headers:{Authorization:'Bearer '+c.getAccessToken()}})).status}''', {'room': other_binding['room_id'], 'base': base})
                assert restored_denied == 403
                assert cannot_decrypt(restored)
                print('PASS fresh managed device restores its own encrypted history using its separate recovery key; personal keys remain unavailable', flush=True)
                old_device = alice.evaluate('async()=>(await secureProof.ensureSession()).getDeviceId()')
                revoked = restored.evaluate('id=>secureProof.revokeDevice(id,"",undefined,true)', old_device)
                assert revoked == {'revoked': True, 'current': False}
                try: request(base, '/auth/me', auth=scoped)
                except HTTPError as error: assert error.code == 401
                else: raise AssertionError('Revoked managed device retained its sign-in')
                assert request(base, '/auth/me', auth=restored_auth)['account_type'] == 'managed'
                print('PASS recent organization SSO revokes its own lost encryption device and linked session without a native password', flush=True)
            finally: browser.close()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
