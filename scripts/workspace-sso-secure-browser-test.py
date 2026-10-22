#!/usr/bin/env python3
"""Real Rust E2EE proof for a customer-scoped sign-in and native crypto unlock.

Requires a running private Matrix deployment, backend dev dependencies, Chromium
and the frontend Vite server. Pass Matrix connection settings in the environment.
Creates its own private API/database and synthetic accounts. OIDC protocol proof
lives in frontend/tests/customer-sso-browser.py; this proof seeds its scoped JWT.
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


with tempfile.TemporaryDirectory(prefix='freecollab-scoped-rust-') as temporary:
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
        passwords = [secrets.token_urlsafe(24), secrets.token_urlsafe(24)]
        users = [request(base, '/auth/register', {'name': name, 'email': name.lower() + secrets.token_hex(8) + '@example.com', 'password': password})
                 for name, password in zip(('ScopedAlice', 'ScopedBob'), passwords)]
        workspace = request(base, '/workspaces', {'name': 'Customer A'}, users[0])['id']
        invitation = request(base, f'/workspaces/{workspace}/invites', {}, users[0])
        request(base, '/workspaces/invites/accept', {'token': invitation['token']}, users[1])
        other_workspace = request(base, '/workspaces', {'name': 'Private B'}, users[0])['id']
        channel = request(base, '/channels', {'name': 'Encrypted A', 'workspace_id': workspace, 'member_ids': [users[1]['user_id']]}, users[0])['id']
        other_channel = request(base, '/channels', {'name': 'Encrypted B', 'workspace_id': other_workspace}, users[0])['id']
        other_binding = request(base, '/secure/channels/' + other_channel + '/enable', {}, users[0])
        fixture_env = dict(env, SSO_PROOF_ACTOR=users[0]['user_id'], SSO_PROOF_WORKSPACE=workspace)
        fixture = '''import os
from app.main import app
from app.database import engine
from sqlmodel import Session
from app.models.workspace_oidc import WorkspaceOIDCProvider,WorkspaceOIDCSession
from app.services.auth import create_token,token_digest
with Session(engine) as session:
    workspace,user=os.environ['SSO_PROOF_WORKSPACE'],os.environ['SSO_PROOF_ACTOR']
    if session.get(WorkspaceOIDCProvider,workspace) is None:
        session.add(WorkspaceOIDCProvider(workspace_id=workspace,name='Synthetic customer IdP',issuer_url='https://idp.example.invalid',client_id='fixture',created_by=user,enabled=True))
    session.commit()
    token=create_token(user,session,auth_methods=['sso'],workspace_scope={'workspace_id':workspace,'provider_revision':1})
    session.add(WorkspaceOIDCSession(token_hash=token_digest(token),workspace_id=workspace,provider_revision=1))
    session.commit()
    print(token)
'''
        issued = subprocess.run([BACKEND_PYTHON, '-c', fixture], cwd=ROOT / 'backend', env=fixture_env,
                                capture_output=True, text=True, check=True)
        scoped = {**users[0], 'access_token': issued.stdout.strip()}
        assert request(base, '/secure/status', auth=scoped)['requires_encryption_unlock'] is True
        try: request(base, '/secure/session', {}, scoped)
        except HTTPError as error:
            assert error.code == 403 and json.load(error)['detail']['code'] == 'requires_encryption_unlock'
        else: raise AssertionError('The IdP-only session obtained global encryption credentials')
        print('PASS customer IdP alone cannot initialize the shared encryption identity', flush=True)
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
                alice, bob = page_for(scoped), page_for(users[1])
                denied = alice.evaluate('''async()=>{try{await secureProof.ensureSession();return ''}catch(e){return e.code}}''')
                assert denied == 'requires_encryption_unlock'
                request(base, '/secure/unlock', {'password': passwords[0]}, scoped)
                assert request(base, '/secure/status', auth=scoped)['requires_encryption_unlock'] is False
                binding = alice.evaluate('channel=>secureProof.enableChannel(channel,"Private customer A")', channel)
                bob.evaluate('channel=>secureProof.getMessages(channel)', channel)
                for page, other in ((alice, users[1]), (bob, users[0])):
                    devices = poll(lambda: page.evaluate('channel=>secureProof.getDevices(channel)', channel), lambda rows: any(d['user_id'] == other['user_id'] for d in rows), 'Real Rust recipient device was not published')
                    for device in devices:
                        if device['user_id'] == other['user_id']: page.evaluate('device=>secureProof.verifyDevice(device,device.fingerprint)', device)
                message = 'Customer A message after explicit native encryption unlock'
                sent = bob.evaluate('args=>secureProof.sendMessage(...args)', [channel, message])
                poll(lambda: alice.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == sent['id'] and row['content'] == message for row in rows), 'Scoped Rust browser could not decrypt its workspace message')
                reply = alice.evaluate('args=>secureProof.sendMessage(...args)', [channel, 'Scoped encrypted reply'])
                poll(lambda: bob.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == reply['id'] and row['content'] == 'Scoped encrypted reply' for row in rows), 'Native peer could not decrypt the scoped sender reply')
                wire = alice.evaluate('''async ({channel,event})=>{const e=await secureProof.getDecryptedEvent(channel,event);return {type:e.getWireType(),wire:JSON.stringify(e.getWireContent())}}''', {'channel': channel, 'event': sent['id']})
                assert wire['type'] == 'm.room.encrypted' and message not in wire['wire']
                denied_other = alice.evaluate('''async ({room,base})=>{const c=await secureProof.ensureSession();return (await fetch(base+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/messages?dir=b',{headers:{Authorization:'Bearer '+c.getAccessToken()}})).status}''', {'room': other_binding['room_id'], 'base': base})
                assert denied_other == 403
                assert request(base, '/auth/me', auth=scoped)['session_scope'] == 'workspace'
                print('PASS native unlock initializes actual Matrix Rust crypto; verified peers decrypt both directions; wire stays encrypted', flush=True)
                print('PASS a known room in the same user\'s other workspace remains denied after crypto unlock', flush=True)
                # Recovery uses explicit encrypted SSSS reads after native unlock;
                # stripping global account_data from scoped sync must not break it.
                recovery = alice.evaluate('()=>secureProof.setupRecovery()')
                assert len(recovery) > 32
                issued_again = subprocess.run([BACKEND_PYTHON, '-c', fixture], cwd=ROOT / 'backend', env=fixture_env,
                                              capture_output=True, text=True, check=True)
                restored_auth = {**users[0], 'access_token': issued_again.stdout.strip()}
                request(base, '/secure/unlock', {'password': passwords[0]}, restored_auth)
                restored = page_for(restored_auth)
                restored.evaluate('key=>secureProof.unlockRecovery(key)', recovery)
                poll(lambda: restored.evaluate('channel=>secureProof.getMessages(channel)', channel), lambda rows: any(row['id'] == sent['id'] and row['content'] == message for row in rows), 'Fresh scoped Rust browser could not restore its workspace history')
                restored_denied = restored.evaluate('''async ({room,base})=>{const c=await secureProof.ensureSession();return (await fetch(base+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/messages?dir=b',{headers:{Authorization:'Bearer '+c.getAccessToken()}})).status}''', {'room': other_binding['room_id'], 'base': base})
                assert restored_denied == 403
                print('PASS fresh scoped browser restores encrypted history using its client-only recovery key; other workspace stays denied', flush=True)
            finally: browser.close()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
