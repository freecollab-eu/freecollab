"""Exercise the actual packaged desktop, using only a generated local HTTPS fixture.

The explicit --container-no-sandbox flag is for isolated CI containers that cannot
provide Chromium's OS sandbox; it is never added to the product or its launchers.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
from threading import Thread
import time
import urllib.request
import websocket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.sync_api import sync_playwright

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--container-no-sandbox', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    with tempfile.TemporaryDirectory(prefix='freecollab-desktop-proof-') as directory:
        root = Path(directory)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'FreeCollab isolated desktop fixture')])
        now = datetime.now(timezone.utc)
        cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(
            x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1)).add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False).sign(key, hashes.SHA256())
        (root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        spki = base64.b64encode(hashlib.sha256(key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'<!doctype html><html><head><title>FreeCollab fixture</title></head><body><h1>Private fixture workspace</h1><iframe title="Unprivileged frame" src="/frame"></iframe></body></html>' if self.path != '/frame' else b'<html><body>Unprivileged frame</body></html>'
                self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(root/'cert.pem', root/'key.pem')
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        origin = 'https://127.0.0.1:'+str(server.server_port)
        with socket.socket() as temporary: temporary.bind(('127.0.0.1',0)); port = temporary.getsockname()[1]
        env = dict(os.environ, XDG_CONFIG_HOME=str(root/'config'), XDG_CACHE_HOME=str(root/'cache'))
        command = [str(binary), '--ozone-platform=headless', '--disable-gpu', '--use-fake-device-for-media-stream', '--remote-debugging-address=127.0.0.1',
            '--remote-debugging-port='+str(port), '--ignore-certificate-errors-spki-list='+spki]
        if args.container_no_sandbox: command.append('--no-sandbox')
        log = (root/'desktop.log').open('w')
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        checks = []
        def check(name, condition):
            assert condition, name
            checks.append(name)
        try:
            with sync_playwright() as playwright:
                deadline = time.monotonic()+25
                while True:
                    try: browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:'+str(port)); break
                    except Exception:
                        if process.poll() is not None or time.monotonic()>deadline:
                            log.flush(); raise RuntimeError('Desktop did not start: '+(root/'desktop.log').read_text()[-3000:])
                        time.sleep(.1)
                context = browser.contexts[0]
                page = context.pages[0]
                page.get_by_role('heading', name='A little closer to your team.').wait_for()
                check('actual packaged setup visible', 'setup.html' in page.url)
                check('setup has no Node process or require', page.evaluate("typeof window.require==='undefined' && typeof window.process==='undefined'"))
                page.get_by_label('Installation address').fill('http://127.0.0.1:3000')
                page.get_by_role('button', name='Connect to your workspace').click()
                page.get_by_role('alert').filter(has_text='HTTPS').wait_for()
                check('insecure installation rejected', 'setup.html' in page.url)
                page.get_by_label('Installation address').fill('https://127.0.0.1:1')
                page.get_by_role('button', name='Connect to your workspace').click()
                page.get_by_role('alert').filter(has_text='could not be opened').wait_for()
                check('unavailable HTTPS deployment returns to usable setup', 'setup.html' in page.url and page.get_by_role('button', name='Connect to your workspace').is_enabled())
                page.get_by_label('Installation address').fill(origin)
                page.get_by_role('button', name='Connect to your workspace').click()
                deadline = time.monotonic()+20
                def targets():
                    with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/json/list') as response: return json.load(response)
                while True:
                    matching = [target for target in targets() if target.get('type')=='page' and target.get('url','').startswith(origin)]
                    if matching: target = matching[0]; break
                    if time.monotonic()>deadline:
                        log.flush(); raise RuntimeError('Installation did not open: '+str(targets())+' '+(root/'desktop.log').read_text()[-2000:])
                    time.sleep(.1)
                # Electron deliberately places each installation in a separate
                # browser context. Playwright's generic CDP connection ignores
                # contexts it did not create, so attach the real page directly.
                wire = websocket.create_connection(target['webSocketDebuggerUrl'], suppress_origin=True, timeout=10)
                sequence = 0
                def evaluate(expression):
                    nonlocal sequence
                    sequence += 1; wire.send(json.dumps({'id':sequence,'method':'Runtime.evaluate','params':{'expression':expression,'returnByValue':True,'awaitPromise':True}}))
                    while True:
                        result = json.loads(wire.recv())
                        if result.get('id')==sequence:
                            assert 'error' not in result and 'exceptionDetails' not in result.get('result',{}), result
                            return result.get('result',{}).get('result',{}).get('value')
                try:
                    while not evaluate("document.querySelector('iframe')?.contentDocument?.body?.textContent.includes('Unprivileged frame')"):
                        if time.monotonic()>deadline: raise RuntimeError('Fixture frame did not load')
                        time.sleep(.05)
                    check('selected HTTPS installation opens', evaluate('location.origin')==origin)
                    check('remote main frame has only narrow native bridge', evaluate("window.freecollabNative.kind==='desktop' && typeof window.freecollabNative.signIn==='function' && typeof window.freecollabNative.cancelSignIn==='function' && typeof window.freecollabSetup==='undefined'"))
                    check('remote main frame cannot access Node or Electron', evaluate("typeof window.require==='undefined' && typeof window.process==='undefined' && typeof window.ipcRenderer==='undefined'"))
                    check('microphone is not granted before explicit user approval', evaluate("(async()=> (await navigator.permissions.query({name:'microphone'})).state!=='granted')()"))
                    devices = evaluate("(async()=> {const devices=await navigator.mediaDevices.enumerateDevices();return {count:devices.length,private:devices.every(device=>!device.label&&!device.deviceId&&!device.groupId)};})()")
                    check('actual fake media devices have no unapproved labels or stable identifiers', devices['count']>0 and devices['private'])
                    check('notifications are not granted before explicit user approval', evaluate("typeof Notification==='undefined' || Notification.permission!=='granted'"))
                    check('same-origin subframe does not receive its own native bridge', evaluate("typeof document.querySelector('iframe').contentWindow.freecollabNative==='undefined' && typeof document.querySelector('iframe').contentWindow.require==='undefined'"))
                    evaluate("window.location.assign('data:text/html,<h1>Unexpected navigation</h1>')")
                    time.sleep(.2)
                    check('non-HTTPS top navigation blocked', evaluate('location.origin')==origin)
                    evaluate("window.open('javascript:document.body.textContent=\"\"')")
                    time.sleep(.1)
                    check('untrusted popup is not created', len([t for t in targets() if t.get('type')=='page'])==1)
                    check('renderer remained live after rejected navigation', evaluate("document.querySelector('h1').textContent==='Private fixture workspace'"))
                finally: wire.close()
                browser.close()
            report = {'checks':checks,'passed':len(checks),'artifact_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                'asar_sha256':hashlib.sha256((binary.parent/'resources/app.asar').read_bytes()).hexdigest(),
                'os_sandbox_exercised':not args.container_no_sandbox,'fixture_certificate_trust':'generated exact SPKI only'}
            if args.report:
                args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report,indent=2))
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            log.close(); server.shutdown(); server.server_close()
if __name__=='__main__': main()

