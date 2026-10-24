"""Actual widget transport and lifecycle proof against an owned loopback server.

No customer account, API or external origin is contacted. The HTTP fixture uses
intentionally cacheable responses and a host cookie to observe browser behavior.
WIDGET_BUNDLE selects a built bundle; WIDGET_SECURITY_REPORT is optional.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(os.getenv('WIDGET_BUNDLE', ROOT / 'frontend/dist-widget/widget.iife.js')).resolve()
REPORT = os.getenv('WIDGET_SECURITY_REPORT')
requests = []
lock = threading.Lock()
old_send_started = threading.Event()
release_old_send = threading.Event()
callback_rebind_started = threading.Event()
release_callback_rebind = threading.Event()
checks = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, value, status=200, headers=None):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html' if isinstance(value, bytes) else 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=3600')
        for key, item in (headers or {}).items():
            self.send_header(key, item)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A correctly cancelled old widget request can disconnect first.

    def record(self, content=None):
        with lock:
            requests.append({'path': self.path, 'method': self.command,
                             'cookie': self.headers.get('Cookie'),
                             'authorization': self.headers.get('Authorization'),
                             'content': content})

    def do_GET(self):
        self.record()
        if self.path == '/':
            self.respond(b'<!doctype html><html><head></head><body></body></html>',
                         headers={'Set-Cookie': 'host_session=private-host-cookie; Path=/; SameSite=Lax'})
        elif self.path == '/api/auth/me':
            if self.headers.get('Authorization') == 'Bearer beta-callback-fixture':
                callback_rebind_started.set()
                release_callback_rebind.wait(15)
            self.respond({'id': 'beta' if self.headers.get('Authorization', '').startswith('Bearer beta-') else 'alpha'})
        elif self.path.startswith('/api/messages/channels/'):
            who = 'beta' if self.headers.get('Authorization', '').startswith('Bearer beta-') else 'alpha'
            channel = urlsplit(self.path).path.rsplit('/', 1)[-1]
            if who == 'alpha' and channel == 'callback-source':
                self.respond([{'id': 'callback-' + part, 'author_id': 'peer',
                               'content': 'Old callback private ' + part,
                               'created_at': '2026-10-22T12:00:00Z'} for part in ('second', 'first')])
                return
            self.respond([{'id': who + '-' + channel, 'author_id': who,
                           'content': who + ' private history in ' + channel,
                           'created_at': '2026-10-22T12:00:00Z'}])
        elif self.path == '/redirect-api/auth/me':
            self.respond({}, 302, {'Location': '/redirect-target'})
        elif self.path == '/redirect-target':
            self.respond({'id': 'unexpected-redirect'})
        else:
            self.respond({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.record(body['content'])
        if body['content'] == 'Old pending send':
            old_send_started.set()
            release_old_send.wait(15)
        self.respond({'id': 'stored-' + body['content'], 'author_id': 'self',
                      'content': body['content'], 'created_at': '2026-10-22T12:00:00Z'})


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
server.daemon_threads = True
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
origin = 'http://127.0.0.1:' + str(server.server_port)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or None,
                                    headless=True, args=['--no-sandbox'])
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin)
            assert 'host_session=private-host-cookie' in page.evaluate('document.cookie')
            page.add_script_tag(path=str(BUNDLE))
            page.evaluate("""apiUrl => {window.received=[];window.widget=FreeCollab.init({apiUrl,channelId:'one',userToken:'alpha-fixture',onMessage:value=>received.push(value.content)});widget.open()}""", origin + '/api')
            expect(page.locator('.freecollab-messages')).to_contain_text('alpha private history in one')
            with lock:
                assert all(not row['cookie'] for row in requests if row['path'].startswith('/api/'))
            checks['host_cookie_is_present_but_never_sent_to_widget_api'] = True

            page.evaluate("widget.setUser('alpha-fixture')")
            expect(page.locator('.freecollab-messages')).to_contain_text('alpha private history in one')
            with lock:
                assert sum(row['path'] == '/api/auth/me' for row in requests) >= 2
                assert sum(row['path'].startswith('/api/messages/channels/one') for row in requests) >= 2
            checks['explicitly_cacheable_private_responses_are_requested_again'] = True

            page.locator('.freecollab-input').fill('Private unsent channel draft')
            page.evaluate("widget.setChannel('two')")
            expect(page.locator('.freecollab-input')).to_have_value('')
            expect(page.locator('.freecollab-messages')).to_contain_text('alpha private history in two')
            expect(page.locator('.freecollab-messages')).not_to_contain_text('history in one')
            checks['channel_rebind_clears_draft_and_history'] = True

            page.locator('.freecollab-input').fill('Old pending send')
            page.locator('.freecollab-send-btn').click()
            assert old_send_started.wait(5)
            page.evaluate("window.afterRebind=received.length;widget.setUser('beta-fixture')")
            expect(page.locator('.freecollab-input')).to_have_value('')
            expect(page.locator('.freecollab-messages')).to_contain_text('beta private history in two')
            expect(page.locator('.freecollab-messages')).not_to_contain_text('alpha private')
            page.locator('.freecollab-input').fill('A new private choice')
            release_old_send.set()
            expect(page.locator('.freecollab-input')).to_have_value('A new private choice')
            expect(page.locator('.freecollab-send-btn')).to_be_enabled()
            page.locator('.freecollab-send-btn').click()
            expect(page.locator('.freecollab-input')).to_have_value('')
            expect(page.locator('.freecollab-messages')).to_contain_text('A new private choice')
            assert page.evaluate("received.slice(afterRebind).every(value=>!value.includes('alpha')&&!value.includes('Old pending send'))")
            with lock:
                assert all(row['authorization'] == 'Bearer beta-fixture' for row in requests if row['content'] == 'A new private choice')
            checks['account_rebind_clears_draft_and_old_send_cannot_repaint_or_erase_new_draft'] = True

            page.evaluate('widget.destroy()')
            with lock:
                before = len(requests)
            page.evaluate("widget.setUser('never-send-this-fixture');widget.setChannel('three');widget.sendMessage()")
            page.wait_for_timeout(150)
            with lock:
                assert len(requests) == before
            expect(page.locator('.freecollab-widget-container')).to_have_count(0)
            checks['destroyed_widget_cannot_restart_authenticated_requests'] = True

            page.evaluate("""apiUrl=>{
                window.callbackMessages=[];
                window.widget=FreeCollab.init({apiUrl,channelId:'callback-source',userToken:'alpha-fixture',welcomeMessage:'',
                    onMessage:value=>{
                        callbackMessages.push(value.content);
                        if(callbackMessages.length===1){
                            widget.setUser('beta-callback-fixture');
                            document.querySelector('.freecollab-input').value='Next binding private draft';
                        }
                    }});
                widget.open();
            }""", origin + '/api')
            assert callback_rebind_started.wait(5)
            expect(page.locator('.freecollab-messages')).to_be_empty()
            expect(page.locator('.freecollab-input')).to_have_value('Next binding private draft')
            expect(page.locator('.freecollab-status')).to_have_text('Connecting…')
            assert page.evaluate('callbackMessages.length') == 1
            release_callback_rebind.set()
            expect(page.locator('.freecollab-messages')).to_contain_text('beta private history in callback-source')
            expect(page.locator('.freecollab-messages')).not_to_contain_text('Old callback private')
            page.evaluate('widget.destroy()')
            checks['synchronous_host_callback_rebind_stops_old_private_history_and_status_mutations'] = True

            page.evaluate("apiUrl=>{window.redirectWidget=FreeCollab.init({apiUrl,channelId:'one',userToken:'alpha-fixture'});redirectWidget.open()}", origin + '/redirect-api')
            expect(page.locator('.freecollab-status')).not_to_have_text('Connecting…')
            with lock:
                assert any(row['path'] == '/redirect-api/auth/me' for row in requests)
                assert not any(row['path'] == '/redirect-target' for row in requests)
            page.evaluate('redirectWidget.destroy()')
            checks['browser_refuses_same_origin_credentialed_redirect'] = True
            assert not errors, errors
            checks['zero_unexpected_browser_page_errors'] = True
            context.close()

            # Separate context: interception guarantees no external request even
            # if a future regression accepts one of these invalid destinations.
            denied = browser.new_context()
            escaped = []
            denied.route('**/*', lambda route: (escaped.append(route.request.url), route.abort()))
            probe = denied.new_page()
            probe.set_content('<!doctype html><html><head></head><body></body></html>')
            probe.add_script_tag(path=str(BUNDLE))
            invalid = ['http://insecure.widget.test/api', 'https://user:password@widget.test/api',
                       'https://widget.test/api?token=private', 'https://widget.test/api#private', 'javascript:alert(1)']
            for api_url in invalid:
                assert probe.evaluate("""apiUrl=>{try{FreeCollab.init({apiUrl,channelId:'one',userToken:'private-fixture'});return false}catch{return true}}""", api_url)
            assert not escaped
            expect(probe.locator('.freecollab-widget-container')).to_have_count(0)
            checks['insecure_or_credential_bearing_api_urls_fail_before_ui_or_network'] = True
            denied.close()
        finally:
            browser.close()
finally:
    release_old_send.set()
    release_callback_rebind.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)

result = {'scope': 'Actual built widget and Chromium against an owned loopback HTTP fixture; no external/customer/provider/backend authority claim.',
          'checks': checks, 'fixture_stopped': not thread.is_alive(),
          'source_sha256': hashlib.sha256((ROOT / 'frontend/src/widget.ts').read_bytes()).hexdigest(),
          'bundle_sha256': hashlib.sha256(BUNDLE.read_bytes()).hexdigest()}
assert len(checks) == 9 and all(checks.values()) and result['fixture_stopped']
if REPORT:
    path = Path(REPORT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n')
print('PASS: all9 widget security browser groups; owned HTTP fixture stopped')
