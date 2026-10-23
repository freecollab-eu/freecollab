#!/usr/bin/env python3
"""Actual-App app install -> command -> signed receiver -> callback card proof.

All services, accounts, keys and databases are disposable loopback fixtures.
Production public-HTTPS transport is replaced only in this private launcher with
a fixed receiver; no browser API response is mocked and no external call runs.
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = os.getenv("BACKEND_PYTHON") or (str(ROOT/"backend/.venv/bin/python") if (ROOT/"backend/.venv/bin/python").exists() else sys.executable)
spec = importlib.util.spec_from_file_location("app_proof_helpers", ROOT / "frontend/tests/connection-setup-browser.py")
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
sys.path.insert(0, str(ROOT/"backend"))
from tests.oidc_mock import tls_fixture
REPORT = Path(os.getenv("APP_PLATFORM_REPORT", str(ROOT.parent / ".freecollab/proof-results/app-platform-browser.json")))
AI_KEY = "synthetic-app-ai-browser-provider-key"
PROOF_SOURCES = ("frontend/src/components/ChannelApps.tsx", "frontend/src/lib/app-platform-api.ts", "frontend/src/lib/copilot-api.ts", "frontend/src/lib/copilot-intents.ts", "frontend/src/styles/app-platform.css", "frontend/src/i18n/app-ai-review.sv.ts", "backend/app/services/app_platform.py", "backend/app/api/app_platform.py", "backend/app/services/copilot.py", "frontend/tests/app-platform-browser.py")


def main():
    processes, streams, checks, received, receiver_secret, ai_received = [], [], f.ProofChecks(), [], [], []
    with tempfile.TemporaryDirectory(prefix="freecollab-app-browser-") as directory:
        private = Path(directory)
        source_hashes = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in PROOF_SOURCES}
        certificate, ai_key = tls_fixture(private/"tls")
        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                assert self.headers["Authorization"] == "Bearer "+AI_KEY
                ai_received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                packet = {"choices": [{"index": 0, "delta": {"content": "Reviewed app summary [S1]."}, "finish_reason": "stop"}]}
                body = ("data: "+json.dumps(packet)+"\n\ndata: [DONE]\n\n").encode()
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers()
                for offset in range(0, len(body), 7): self.wfile.write(body[offset:offset+7]); self.wfile.flush()
            def log_message(self, *_): pass
        ai_server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(certificate, ai_key); ai_server.socket = tls.wrap_socket(ai_server.socket, server_side=True)
        threading.Thread(target=ai_server.serve_forever, daemon=True).start()
        ai_endpoint = "https://localhost:"+str(ai_server.server_port)+"/v1/chat/completions"
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                stamp = self.headers["X-FreeCollab-Timestamp"]
                expected = "v1="+hmac.new(receiver_secret[0].encode(), stamp.encode()+b"."+body, hashlib.sha256).hexdigest()
                assert hmac.compare_digest(self.headers["X-FreeCollab-Signature"], expected)
                packet = json.loads(body)
                assert self.headers["Idempotency-Key"] == packet["id"]
                received.append(packet)
                self.send_response(204); self.end_headers()
            def log_message(self, *_): pass
        receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        threading.Thread(target=receiver.serve_forever, daemon=True).start()
        try:
            api_port, web_port = f.port(), f.port()
            base, origin = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
            environment = os.environ.copy()
            environment.update(ENVIRONMENT="test", DATABASE_URL="sqlite:///"+str(private/"proof.db"), STORAGE_PATH=str(private/"files"),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({"proof": base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID="proof",
                ACCOUNT_SECURITY_KEY_FILE=str(private/"account.key"), MESSAGE_ENCRYPTION_KEY_FILE=str(private/"message.keys"),
                MATRIX_ENABLED="false", MAIL_DELIVERY_MODE="disabled", PUBLIC_APP_URL=origin, CORS_ORIGINS=json.dumps([origin]),
                OIDC_ENABLED="false", WEBHOOKS_ENABLED="false", OTRUST_ENABLED="false", SYNC_ENABLED="false", COPILOT_ENABLED="true", SSL_CERT_FILE=certificate, PROOF_AI_ENDPOINT=ai_endpoint,
                CLOUD_FILES_ENABLED="false", EXTERNAL_MEETINGS_ENABLED="false", PUSH_ENABLED="false", PYTHONPATH=str(ROOT/"backend"))
            environment["NO_PROXY"] = ",".join(filter(None, [environment.get("NO_PROXY", ""), "127.0.0.1", "localhost", "::1"]))
            f.SENSITIVE.extend([AI_KEY]+[environment[key] for key in ("JWT_SECRET", "ACCOUNT_SECURITY_KEY", "MESSAGE_ENCRYPTION_KEYS")])
            launcher = private/"api.py"
            launcher.write_text('''import os, uvicorn, httpx
from urllib.parse import urlsplit
from sqlmodel import Session
from app.main import app, socket_app
from app.database import engine
from app.models.app_platform import ChannelRecapSchedule
from app.services import app_platform, webhooks, copilot
from datetime import datetime,timedelta
fixture_endpoint=os.environ['PROOF_AI_ENDPOINT']
normalize=copilot.transport.normalize_url
addresses=copilot.transport.public_addresses
proxy=copilot.transport._proxy
completion=copilot.completion
def fixture_completion(provider,payload):
    if provider.endpoint!=fixture_endpoint: raise RuntimeError('Private proof refuses non-fixture AI destinations')
    return completion(provider,payload)
copilot.completion=fixture_completion
copilot.transport.normalize_url=lambda value,**kw:(urlsplit(value),'localhost') if value==fixture_endpoint else normalize(value,**kw)
copilot.transport.public_addresses=lambda host,**kw:['127.0.0.1'] if host=='localhost' else addresses(host,**kw)
copilot.transport._proxy=lambda host,port:None if host=='localhost' else proxy(host,port)
normal = webhooks.public_addresses
webhooks.public_addresses = lambda host: ['8.8.8.8'] if host=='apps.fixture.example' else normal(host)
@app.post('/__proof/app-worker')
def worker():
    def sender(url, body, headers):
        if url!='https://apps.fixture.example/hook': raise ValueError('Fixture endpoint only')
        with httpx.Client(trust_env=False) as client:
            response=client.post('http://127.0.0.1:''' + str(receiver.server_port) + '''/',content=body,headers=headers)
        return response.status_code, None
    with Session(engine) as session: return {'delivered':app_platform.run_pending(session,send=sender)}
@app.post('/__proof/recap-worker/{schedule_id}')
def recap_worker(schedule_id:str):
    with Session(engine) as session:
        row=session.get(ChannelRecapSchedule,schedule_id)
        if not row: raise ValueError('Private fixture schedule only')
        row.next_run_at=datetime.utcnow()-timedelta(seconds=1)
        session.add(row);session.commit()
        return {'drafts':app_platform.run_recaps(session)}
uvicorn.run(socket_app,host='127.0.0.1',port=''' + str(api_port) + ''',proxy_headers=False,access_log=False)
''')
            web, vite = f.frontend_snapshot(private, base, web_port)
            for command, name, cwd in [([PYTHON, str(launcher)], "api.log", ROOT/"backend"), (["node", str(vite)], "vite.log", web)]:
                stream = (private/name).open("w"); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            f.ready(base+"/ready", processes); f.ready(origin, processes)
            email = "app-owner-"+secrets.token_hex(5)+"@example.com"
            owner = f.api(base, "POST", "/auth/register", data={"name": "App proof owner", "email": email, "password": f.PASSWORD})
            f.SENSITIVE.append(owner["access_token"])
            workspace = f.api(base, "POST", "/workspaces", owner, {"name": "App proof studio"})
            room = f.api(base, "POST", "/channels", owner, {"name": "App proof room", "workspace_id": workspace["id"]})
            f.api(base, "POST", "/messages/channels/"+room["id"], owner, {"content": "CHANNEL HISTORY MUST NEVER REACH THE APP"})
            errors, blocked, ai_posts = [], [], []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or shutil.which("chromium"), headless=True, args=["--no-sandbox"])
                context = browser.new_context(viewport={"width": 1440, "height": 1050}, reduced_motion="reduce")
                def route(request):
                    parsed = urlsplit(request.request.url)
                    if parsed.scheme in {"data", "blob"} or parsed.hostname in {"127.0.0.1", "localhost", "::1"}: request.continue_()
                    else: blocked.append(parsed.hostname); request.abort()
                context.route("**/*", route)
                page = context.new_page(); page.set_default_timeout(30000)
                page.on("pageerror", lambda error: errors.append(type(error).__name__))
                page.on("request", lambda request: ai_posts.append(request.post_data_json) if request.method == 'POST' and request.url.endswith('/apps/ai/drafts') else None)
                dialog, _ = f.login(page, origin, owner, email, workspace["id"])
                dialog.get_by_role("button", name="Apps", exact=True).click()
                panel = dialog.get_by_role("region", name="Workspace apps", exact=True)
                expect(panel.get_by_text("Waiting for integration worker", exact=True)).to_be_visible()
                panel.get_by_role("button", name="Setup recipe Signed app / n8n", exact=False).click()
                panel.get_by_label("Customer-owned HTTPS endpoint", exact=True).fill("https://apps.fixture.example/hook")
                panel.get_by_label("App proof room", exact=True).check()
                panel.get_by_label("I reviewed this manifest, endpoint, exact scopes and channel grants. Only explicitly invoked arguments may leave FreeCollab.").check()
                panel.get_by_role("button", name="Install with these grants", exact=True).click()
                secret_input = panel.get_by_label("Signing secret", exact=True)
                expect(secret_input).to_be_visible(); receiver_secret.append(secret_input.input_value())
                f.SENSITIVE.append(receiver_secret[0])
                callback_path = panel.get_by_label("Callback path", exact=True).input_value()
                dialog.get_by_role("button", name="Overview", exact=True).click()
                expect(secret_input).to_be_visible()
                panel.get_by_role("button", name="I stored the secret securely", exact=True).click()
                expect(secret_input).to_have_count(0)
                checks["real_install_once_only_secret_and_navigation_guard"] = True
                dialog.get_by_role("button", name="Done", exact=True).click()
                page.get_by_role("button", name="Open workspace tools", exact=True).click()
                page.locator("#work-tab-apps").click()
                channel_panel = page.get_by_role("region", name="Channel apps and automation", exact=True)
                expect(channel_panel).to_be_visible(timeout=30000)
                page.screenshot(path="/tmp/freecollab-app-platform-channel.png")
                expect(channel_panel.get_by_role("combobox", name="Slash command", exact=True)).to_have_value("invoke")
                expect(channel_panel.get_by_role("button", name="Approve and run command", exact=True)).to_be_disabled()
                channel_panel.get_by_label("Text", exact=True).fill("REVIEWED EXPLICIT APP ARGUMENT")
                channel_panel.get_by_label("Share only these reviewed arguments with this installed app and approve this action. Channel history is not included.").check()
                channel_panel.get_by_role("button", name="Approve and run command", exact=True).click()
                expect(channel_panel.get_by_role("status")).to_contain_text("Command queued.")
                assert f.api(base, "POST", "/__proof/app-worker", owner)["delivered"] == 1
                assert len(received) == 1 and received[0]["arguments"] == {"text": "REVIEWED EXPLICIT APP ARGUMENT"}
                assert "CHANNEL HISTORY MUST NEVER REACH THE APP" not in json.dumps(received)
                checks["actual_workhub_command_review_delivers_exact_arguments_signed_to_real_receiver"] = True
                packet = {"event_id": "browser_callback_123", "generation": received[0]["generation"], "channel_id": room["id"],
                    "card": {"title": "Receiver completed", "text": "Literal <script>alert('never')</script>", "url": "https://example.com/review"}}
                body, stamp = json.dumps(packet, separators=(",", ":")).encode(), str(int(time.time()))
                signature = "v1="+hmac.new(receiver_secret[0].encode(), stamp.encode()+b"."+body, hashlib.sha256).hexdigest()
                request = Request(base+callback_path, data=body, headers={"Content-Type": "application/json", "X-FreeCollab-Timestamp": stamp, "X-FreeCollab-Signature": signature}, method="POST")
                with urlopen(request, timeout=20) as response: assert response.status == 201
                channel_panel.get_by_role("button", name="App cards", exact=True).click()
                expect(channel_panel.get_by_role("heading", name="Receiver completed", exact=True)).to_be_visible()
                expect(channel_panel.get_by_text("Literal <script>alert('never')</script>", exact=True)).to_be_visible()
                link = channel_panel.get_by_role("link", name="Open external link", exact=False)
                expect(link).to_have_attribute("rel", "noopener noreferrer")
                assert not blocked
                checks["real_signed_callback_renders_text_only_and_no_unfurl_fetch"] = True
                channel_panel.get_by_role("button", name="AI drafts & recaps", exact=True).click()
                expect(channel_panel.get_by_text("Configure and enable your own workspace AI provider in the copilot settings before creating drafts.", exact=True)).to_be_visible()
                assert ai_received == []
                checks["unconfigured_ai_state_is_honest_and_never_contacts_provider"] = True
                selected_text = "Reviewed message: "+"ä"*300+"\nVISIBLE SELECTED TAIL 🧭"
                selected_message = f.api(base, "POST", "/messages/channels/"+room["id"], owner, {"content": selected_text})
                provider_config = {"expected_revision": 0, "password": f.PASSWORD, "enabled": True, "endpoint": ai_endpoint, "model": "fixture-app-ai", "api_key": AI_KEY, "agents": [{"name": "helper", "tools": []}]}
                f.api(base, "PUT", "/workspaces/"+workspace["id"]+"/copilot/config", owner, provider_config)
                f.api(base, "PUT", "/workspaces/"+workspace["id"]+"/copilot/consent", owner, {"config_revision": 1, "enabled": True})
                channel_panel.get_by_role("button", name="AI drafts & recaps", exact=True).click()
                expect(channel_panel.get_by_text("fixture-app-ai", exact=True)).to_be_visible()
                channel_panel.get_by_role("button", name="Choose native message context", exact=True).click()
                selected_row = channel_panel.locator('.fc-app-source-list label').filter(has_text='VISIBLE SELECTED TAIL 🧭')
                expect(selected_row).to_contain_text(selected_text); selected_row.get_by_role('checkbox').check()
                warning = channel_panel.get_by_role('complementary', name='Before sending to AI', exact=True)
                expect(warning).to_contain_text('1 selected sources will be sent to localhost using fixture-app-ai')
                expect(warning).to_contain_text('API use may incur charges. A chat subscription is separate.')
                assert ai_received == []
                channel_panel.get_by_label('Send these instructions and selected context to the configured provider. Create a private draft only; I will review any publication or action separately.').check()
                channel_panel.get_by_role('button', name='Generate private draft', exact=True).click()
                draft = channel_panel.locator('.fc-app-draft').filter(has_text='Reviewed app summary [S1].')
                expect(draft).to_be_visible(timeout=60000)
                assert len(ai_received) == len(ai_posts) == 1, 'Expected one explicitly requested provider completion'
                assert ai_posts[0]['expected_config_revision'] == 1, 'Browser did not bind the reviewed provider revision'
                assert ai_posts[0]['sources'] == [{'kind': 'message', 'id': selected_message['id'], 'expected_text_sha256': hashlib.sha256(selected_text.encode()).hexdigest()}], 'Browser did not bind the exact reviewed UTF-8 message'
                assert selected_text in ai_received[0]['messages'][-1]['content'] and 'CHANNEL HISTORY MUST NEVER REACH THE APP' not in json.dumps(ai_received), 'Provider context differs from explicitly selected text'
                with sqlite3.connect(private/'proof.db') as database: assert database.execute('SELECT count(*) FROM appcard').fetchone()[0] == 1
                draft.get_by_label('I reviewed this exact draft, its sources and destinations.').check()
                draft.get_by_role('button', name='Publish reviewed recap', exact=True).click()
                expect(draft.get_by_text('Approved', exact=True)).to_be_visible()
                with sqlite3.connect(private/'proof.db') as database: assert database.execute('SELECT count(*) FROM appcard').fetchone()[0] == 2
                checks['actual_https_ai_reviews_full_text_digest_recipient_costs_and_requires_separate_publication'] = True
                channel_panel.get_by_text('Recurring private recap', exact=True).click()
                future = channel_panel.get_by_label('I explicitly authorize future native-message disclosure for this recurring recap and this provider revision.')
                future.check()
                updated_config = {key: value for key, value in provider_config.items() if key != 'api_key'}
                f.api(base, 'PUT', '/workspaces/'+workspace['id']+'/copilot/config', owner, {**updated_config, 'expected_revision': 1, 'model': 'fixture-app-updated'})
                f.api(base, 'PUT', '/workspaces/'+workspace['id']+'/copilot/consent', owner, {'config_revision': 2, 'enabled': True})
                channel_panel.get_by_role('button', name='Schedule private recap', exact=True).click()
                expect(channel_panel.get_by_role('alert')).to_contain_text('Provider configuration changed; review the current destination before scheduling')
                with sqlite3.connect(private/'proof.db') as database: assert database.execute('SELECT count(*) FROM channelrecapschedule').fetchone()[0] == 0
                assert len(ai_received) == 1
                channel_panel.get_by_role('button', name='Refresh', exact=True).click()
                expect(channel_panel.get_by_text('fixture-app-updated', exact=True)).to_be_visible(); expect(future).not_to_be_checked()
                expect(channel_panel.get_by_role('button', name='Schedule private recap', exact=True)).to_be_disabled()
                future.check(); channel_panel.get_by_role('button', name='Schedule private recap', exact=True).click()
                expect(channel_panel.get_by_role('status')).to_contain_text('Recurring private recap scheduled.')
                with sqlite3.connect(private/'proof.db') as database:
                    assert database.execute('SELECT provider_revision FROM channelrecapschedule').fetchall() == [(2,)]
                    schedule_id = database.execute('SELECT id FROM channelrecapschedule').fetchone()[0]
                assert len(ai_received) == 1
                checks['stale_schedule_review_cannot_bind_a_reconsented_provider_until_fresh_explicit_review'] = True
                # Only this private schedule's due timestamp advances; actual source
                # selection, provider HTTPS, persistence and approval stay real.
                assert f.api(base, 'POST', '/__proof/recap-worker/'+schedule_id, owner)['drafts'] == 0
                assert len(ai_received) == 1
                future_text = 'NATIVE MESSAGE WRITTEN AFTER FUTURE CONSENT'
                f.api(base, 'POST', '/messages/channels/'+room['id'], owner, {'content': future_text})
                assert f.api(base, 'POST', '/__proof/recap-worker/'+schedule_id, owner)['drafts'] == 1
                assert len(ai_received) == 2 and ai_received[-1]['model'] == 'fixture-app-updated'
                assert future_text in ai_received[-1]['messages'][-1]['content']
                assert selected_text not in ai_received[-1]['messages'][-1]['content'] and 'CHANNEL HISTORY MUST NEVER REACH THE APP' not in json.dumps(ai_received[-1])
                with sqlite3.connect(private/'proof.db') as database:
                    assert database.execute('SELECT count(*) FROM appcard').fetchone()[0] == 2
                    assert database.execute('SELECT count(*) FROM appaidraft').fetchone()[0] == 2
                checks['recap_worker_discloses_only_postconsent_messages_and_never_publishes_automatically'] = True
                f.api(base, "PUT", "/preferences/language", owner, {"locale": "sv"})
                page.reload(wait_until="domcontentloaded"); expect(page.locator(".fc-chat")).to_be_visible()
                page.get_by_role("button", name="Öppna arbetsytans verktyg", exact=True).click()
                page.locator("#work-tab-apps").click()
                channel_panel = page.get_by_role("region", name="Kanalappar och automatisering", exact=True)
                expect(channel_panel.get_by_role("heading", name="Små steg. Tydliga behörigheter.", exact=True)).to_be_visible()
                for width in (390, 320):
                    page.set_viewport_size({"width": width, "height": 844})
                    page.screenshot(path="/tmp/freecollab-app-platform-"+str(width)+".png")
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), "Page horizontal overflow"
                    assert channel_panel.evaluate("el=>el.scrollWidth<=el.clientWidth+1"), "App panel overflow"
                REPORT.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(REPORT.parent/"app-platform-mobile-sv.png"))
                checks["saved_swedish_and_320px_layout_without_overflow"] = True
                assert not errors and not blocked
                checks["no_page_errors_or_external_browser_requests"] = True
                context.close(); browser.close()
            assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest for name, digest in source_hashes.items()), 'App AI proof sources changed during run'
            REPORT.write_text(json.dumps({"ok": True, "checks": checks, "source_hashes": source_hashes}, indent=2)+"\n")
            print(json.dumps({"ok": True, "checks": list(checks), "report": str(REPORT)}))
        except Exception as failure:
            print(json.dumps({'failure_locations': [{'file': Path(frame.filename).name, 'line': frame.lineno} for frame in traceback.extract_tb(failure.__traceback__)]}))
            if "page" in locals():
                try: page.screenshot(path="/tmp/freecollab-app-platform-failure.png")
                except Exception: pass
            # Never emit credentials, server logs or account response bodies.
            print(json.dumps({"failure": f.redacted(failure)[:9000], "completed": list(checks)}))
            raise AssertionError("Private app platform browser proof failed: "+type(failure).__name__) from None
        finally:
            f.stop(processes)
            for stream in streams: stream.close()
            receiver.shutdown(); receiver.server_close()
            ai_server.shutdown(); ai_server.server_close()


if __name__ == "__main__":
    main()
