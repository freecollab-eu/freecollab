"""Real version-proof controls against an isolated API and local OTRUST fixture.

Requires the normal Vite client (CHAT_BASE_URL, default3000), Chromium and
backend test dependencies. Starts/stops only its own loopback API/provider and
private temporary DB/keys/files. No real timestamp, user content or email is sent.
"""
from ui_navigation import open_chat_tool
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = os.getenv('CHAT_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
seen = []; receipts = {}; downloads = {}


def proof_bytes(digest):
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.serialize import BytesSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
    stamp = Timestamp(bytes.fromhex(digest)); stamp.attestations.add(BitcoinBlockHeaderAttestation(123))
    detached = DetachedTimestampFile(OpSHA256(), stamp); out = BytesSerializationContext(); detached.serialize(out)
    return out.getbytes()


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        assert self.path == '/claim/simple'
        value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert set(value) == {'hash', 'source'} and value['source'] == 'api' and len(value['hash']) == 64
        seen.append((value, self.headers['Idempotency-Key']))
        receipt = 'local_' + value['hash'][:20]; receipts[receipt] = value['hash']
        body = json.dumps({'receipt_id': receipt, 'hash': value['hash']}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        receipt = self.path.removeprefix('/proof/'); assert receipt in receipts
        downloads[receipt] = downloads.get(receipt, 0) + 1
        if downloads[receipt] == 1:
            self.send_response(202); self.send_header('Retry-After', '60'); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type', 'application/octet-stream'); self.end_headers()
        self.wfile.write(proof_bytes(receipts[receipt]))


def unused_port():
    with socket.socket() as candidate:
        candidate.bind(('127.0.0.1', 0)); return candidate.getsockname()[1]


with tempfile.TemporaryDirectory(prefix='freecollab-proof-browser-') as folder:
    path = Path(folder)
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True); thread.start()
    port = unused_port(); API = 'http://127.0.0.1:' + str(port)
    env = os.environ.copy(); env.update(
        ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(path / 'proofs.db'), STORAGE_PATH=str(path / 'files'),
        JWT_SECRET=secrets.token_urlsafe(40), MATRIX_ENABLED='false', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false',
        MAIL_DELIVERY_MODE='disabled', OTRUST_ENABLED='true', OTRUST_BASE_URL='http://127.0.0.1:' + str(provider.server_port),
        MESSAGE_ENCRYPTION_KEY_FILE=str(path / 'message-keys.json'), ACCOUNT_SECURITY_KEY_FILE=str(path / 'account.key'),
        PUBLIC_APP_URL=BASE,
    )
    os.environ.update(env); sys.path.insert(0, str(ROOT / 'backend'))
    log = (path / 'api.log').open('w+')
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:socket_app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=ROOT / 'backend', env=env, stdout=log, stderr=log)
    try:
        for attempt in range(150):
            try:
                if httpx.get(API + '/health', timeout=1).is_success: break
            except httpx.HTTPError: pass
            if process.poll() is not None:
                raise AssertionError('Isolated proof API exited; inspect local fixture log')
            time.sleep(.1)
        else: raise AssertionError('Isolated proof API did not become ready')
        def api(method, endpoint, token=None, **kwargs):
            response = httpx.request(method, API + endpoint, headers={'Authorization': 'Bearer ' + token} if token else {}, timeout=45, **kwargs)
            assert response.is_success, f'Fixture API failed {endpoint}: {response.status_code}'
            return response.json()
        password = secrets.token_urlsafe(24)
        auth = api('POST', '/auth/register', json={'name': 'Proof tester', 'email': 'proof-' + secrets.token_hex(8) + '@example.com', 'password': password})
        token = auth['access_token']; workspace = api('GET', '/workspaces', token)[0]
        channel = api('POST', '/channels', token, json={'name': 'Version proofs', 'workspace_id': workspace['id']})
        first = api('POST', '/files/channels/' + channel['id'], token, files={'file': ('original.txt', b'Original exact bytes', 'text/plain')})
        latest = api('POST', '/files/' + first['id'] + '/versions', token, files={'file': ('revised.txt', b'Revised exact bytes', 'text/plain')})
        decision = api('POST', '/channels/' + channel['id'] + '/decisions', token,
            json={'title': 'A deliberate decision', 'content': 'Keep the exact record.', 'status': 'accepted'})
        from sqlmodel import Session, select
        from app.database import engine
        from app.models.version_proof import VersionProof
        from app.services.otrust import process_pending
        def advance():
            with Session(engine) as session:
                for record in session.exec(select(VersionProof)).all():
                    if record.state != 'proof_available':
                        record.next_attempt_at = datetime.utcnow() - timedelta(seconds=1); session.add(record)
                session.commit(); process_pending(session)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            context = browser.new_context(accept_downloads=True)
            context.add_init_script(f"localStorage.setItem('freecollab_token', {json.dumps(token)}); localStorage.setItem('freecollab_user_id', {json.dumps(auth['user_id'])});")
            page = context.new_page(); page.set_default_timeout(60000); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', lambda route: route.fulfill(response=route.fetch(url=API + route.request.url.split('/api', 1)[1])))
            page.goto(BASE, wait_until='domcontentloaded', timeout=90000)
            try:
                expect(page.get_by_role('textbox', name='Message #Version proofs', exact=True)).to_be_visible(timeout=90000)
            except Exception:
                raise AssertionError('Proof fixture app failed to render: ' + '; '.join(errors)) from None
            open_chat_tool(page, 'Channel files')
            row = page.locator('.fc-file-row').filter(has=page.get_by_text('revised.txt', exact=True))
            row.get_by_role('button', name='Timestamp proof', exact=True).click()
            panel = row.get_by_label('Timestamp proof for this file version', exact=True)
            panel.get_by_role('button', name='Check this version’s hash locally', exact=True).click()
            digest = hashlib.sha256(b'Revised exact bytes').hexdigest()
            expect(panel.locator('.fc-version-proof-consent code')).to_have_text(digest)
            assert not seen, 'Preparing a hash contacted the provider before explicit consent'
            expect(panel.get_by_role('button', name='Request timestamp', exact=True)).to_be_disabled()
            panel.get_by_label('I want to share this hash with the timestamp provider.', exact=True).check()
            panel.get_by_role('button', name='Request timestamp', exact=True).click()
            expect(panel.get_by_text('Queued for timestamping', exact=True)).to_be_visible()
            assert not seen, 'API request should queue durable work, not publish directly'
            advance(); panel.get_by_role('button', name='Refresh', exact=True).click()
            expect(panel.get_by_text('Waiting for timestamp proof', exact=True)).to_be_visible()
            advance(); advance(); panel.get_by_role('button', name='Refresh', exact=True).click()
            expect(panel.get_by_text('Proof available · commitment checked', exact=True)).to_be_visible()
            expect(panel.get_by_text('The OpenTimestamps file commits to this digest. Independent Bitcoin-chain verification has not been performed.', exact=True)).to_be_visible()
            with page.expect_download() as pending:
                panel.get_by_role('button', name='Download .ots proof', exact=True).click()
            downloaded = path / 'download.ots'; pending.value.save_as(downloaded)
            assert downloaded.read_bytes() == proof_bytes(digest)
            assert len(seen) == 1 and seen[0][0] == {'hash': digest, 'source': 'api'}
            print('PASS exact-version consent, durable hash-only provider delivery, pending/OTS lifecycle and honest Bitcoin status', flush=True)
            row.get_by_role('button', name='2 versions', exact=True).click()
            history = row.get_by_label('Version history for revised.txt', exact=True)
            original = history.locator('li').filter(has=page.get_by_text('original.txt', exact=True))
            original.get_by_role('button', name='Timestamp proof', exact=True).click()
            oldpanel = original.get_by_label('Timestamp proof for this file version', exact=True)
            expect(oldpanel.get_by_text('Proof available · commitment checked', exact=True)).to_have_count(0)
            oldpanel.get_by_role('button', name='Check this version’s hash locally', exact=True).click()
            expect(oldpanel.locator('.fc-version-proof-consent code')).to_have_text(hashlib.sha256(b'Original exact bytes').hexdigest())
            page.set_viewport_size({'width': 320, 'height': 768})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Proof controls overflow mobile viewport'
            print('PASS immutable old-version proof isolation and320px layout', flush=True)
            page.get_by_role('button', name='Close channel panel', exact=True).click()
            page.set_viewport_size({'width': 1440, 'height': 1000})
            open_chat_tool(page, 'Saved items and decisions')
            notebook = page.get_by_role('region', name='Channel notebook', exact=True)
            views = notebook.locator('details.fc-collaboration-flow-options')
            views.locator(':scope > summary').click()
            views.get_by_role('button', name='Decisions', exact=True).click()
            notebook = page.get_by_role('region', name='Decisions in this conversation', exact=True)
            card = notebook.locator('.fc-decision-card').filter(has=page.get_by_role('heading', name='A deliberate decision', exact=True))
            card.get_by_role('button', name='Timestamp proof', exact=True).click()
            decisionpanel = card.get_by_label('Timestamp proof for this decision version', exact=True)
            decisionpanel.get_by_role('button', name='Check this version’s hash locally', exact=True).click()
            canonical = json.dumps(['freecollab-decision-v1', decision['id'], 1, 'A deliberate decision', 'Keep the exact record.', 'accepted', None, None, None], ensure_ascii=False, separators=(',', ':')).encode()
            expect(decisionpanel.locator('.fc-version-proof-consent code')).to_have_text(hashlib.sha256(canonical).hexdigest())
            decisionpanel.get_by_label('I want to share this hash with the timestamp provider.', exact=True).check()
            decisionpanel.get_by_role('button', name='Request timestamp', exact=True).click()
            expect(decisionpanel.get_by_text('Queued for timestamping', exact=True)).to_be_visible()
            assert len(seen) == 1, 'Decision request must remain queued until worker delivery'
            print('PASS real decision canonical bytes, explicit consent and separate durable revision record', flush=True)
            assert not errors, 'Unexpected page errors: ' + '; '.join(errors)
            context.close(); browser.close()
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        provider.shutdown(); provider.server_close(); log.close()
