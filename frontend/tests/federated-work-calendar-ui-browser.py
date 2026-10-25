#!/usr/bin/env python3
"""Real React/Intl presentation regression only; no Matrix/protocol claim.

Only the client calendar-data load is replaced with owned records for this one
channel. The real module exports, client scope and cleanup remain intact.
Actual channel authorization and encryption are covered separately
by connect-federation-browser.py against independent homeservers.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / 'frontend'
PROOFS = ROOT.parent / '.freecollab/proof-results'
REPORT_DIR = Path(os.environ.get('FREECOLLAB_PROOF_REPORT_DIR', str(PROOFS))).resolve()
CRYPTO_PROOF = Path(os.environ.get('FREECOLLAB_CALENDAR_CRYPTO_PROOF', str(PROOFS / 'connect-federation-browser-013.json'))).resolve()


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    entry = FRONTEND / ('.fc-calendar-ui-' + secrets.token_hex(6) + '.html')
    source = FRONTEND / 'src/components/FederatedWorkPanel.tsx'
    initial_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    crypto_proof = json.loads(CRYPTO_PROOF.read_text())
    baseline = crypto_proof['runtime_source_sha256']
    assert all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest for path, digest in baseline.items() if path != str(source.relative_to(ROOT))), 'A non-presentation protocol source changed'
    with tempfile.TemporaryDirectory(prefix='fc-calendar-ui-') as directory:
        private = Path(directory); (private / 'node_modules').symlink_to(FRONTEND / 'node_modules', target_is_directory=True)
        process = None
        try:
            entry.write_text('''<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module">
import React from 'react';import{createRoot}from'react-dom/client';import{api}from'/src/lib/api.ts';import{FederatedWorkClient}from'/src/lib/federated-work-api.ts';import{FederatedWorkPanel}from'/src/components/FederatedWorkPanel.tsx';
const actualLoad=FederatedWorkClient.prototype.load;
FederatedWorkClient.prototype.load=function(){return this.channelId==='owned-calendar-presentation'?Promise.resolve(structuredClone(window.presentationCalendarSnapshot)):actualLoad.call(this)};
api.getAccessToken=()=>'owned-calendar-presentation';createRoot(document.getElementById('root')).render(React.createElement(FederatedWorkPanel,{channelId:'owned-calendar-presentation'}));window.presentationReady=true;
</script></body></html>''')
            config = private / 'vite.config.mjs'
            config.write_text('import{mergeConfig}from' + json.dumps(str(FRONTEND / 'node_modules/vite/dist/node/index.js')) + ';import product from' + json.dumps(str(FRONTEND / 'vite.config.ts')) + ';export default mergeConfig(product,' + json.dumps({'cacheDir': str(private / 'cache'), 'optimizeDeps': {'entries': [str(entry)]}, 'server': {'hmr': False, 'fs': {'allow': [str(FRONTEND), str(private / 'cache')]}}}) + ');')
            with (private / 'vite.log').open('wb') as output:
                process = subprocess.Popen(['node', str(FRONTEND / 'node_modules/vite/bin/vite.js'), '--config', str(config), '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=FRONTEND, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                origin = 'http://127.0.0.1:' + str(port)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    assert process.poll() is None, 'Owned UI server exited'
                    try:
                        with urlopen(origin, timeout=2) as response:
                            if response.status == 200: break
                    except Exception: time.sleep(.2)
                fields = {'description': '', 'location': '', 'timezone': 'Europe/Stockholm', 'reminder_minutes': None, 'external_uid': None}
                definitions = [
                    ('all-day', {'title': 'All-day design review', 'starts_at': '2026-10-23', 'ends_at': '2026-10-24', 'all_day': True}),
                    ('dst-day', {'title': 'All-day November review', 'starts_at': '2026-11-01', 'ends_at': '2026-11-02', 'all_day': True}),
                    ('timed', {'title': 'Stockholm timed review', 'starts_at': '2026-10-23T08:00:00.000Z', 'ends_at': '2026-10-23T09:00:00.000Z', 'all_day': False}),
                ]
                records = []
                for identifier, values in definitions:
                    revision = {'event_id': '$' + identifier, 'sender': '@fc_member:matrix.example.com', 'timestamp': 1, 'id': identifier, 'kind': 'calendar', 'parents': [], 'archived': False, 'fields': {**fields, **values}}
                    records.append({'id': identifier, 'kind': 'calendar', 'root_event_id': revision['event_id'], 'created_by': revision['sender'], 'heads': [revision], 'selected': revision, 'history': [revision], 'conflicts': False, 'invalid': []})
                snapshot = {'context': {'members': []}, 'records': records, 'warnings': [], 'pending_pointers': 0, 'quarantined_event_ids': []}
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
                    failures = []; page_errors = []
                    try:
                        context = browser.new_context(timezone_id='America/Los_Angeles', locale='en-US')
                        context.add_init_script('window.presentationCalendarSnapshot=' + json.dumps(snapshot) + ';')
                        page = context.new_page()
                        page.on('requestfailed', lambda request: failures.append(request.failure))
                        page.on('pageerror', lambda error: page_errors.append(str(error)))
                        for attempt in range(3):
                            failures.clear()
                            try:
                                page.goto(origin + '/' + entry.name, wait_until='networkidle')
                                page.wait_for_function('Boolean(window.presentationReady)', timeout=20000)
                                break
                            except Exception:
                                if attempt == 2 or not any('ERR_NETWORK_CHANGED' in str(item) for item in failures): raise
                        page.get_by_role('button', name='Calendar', exact=True).click()
                        first = page.get_by_role('button', name=re.compile('All-day design review')).inner_text()
                        second = page.get_by_role('button', name=re.compile('All-day November review')).inner_text()
                        timed = page.get_by_role('button', name=re.compile('Stockholm timed review')).inner_text()
                        assert 'Oct 23, 2026' in first and 'Oct 22' not in first, first
                        assert 'Nov 1, 2026' in second and 'Oct 31' not in second, second
                        assert '10:00 AM' in timed and '1:00 AM' not in timed, timed
                        assert page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == 'America/Los_Angeles'
                    except Exception:
                        REPORT_DIR.mkdir(parents=True, exist_ok=True)
                        (REPORT_DIR / 'federated-work-calendar-ui-errors.private.json').write_text(json.dumps({'page_errors':page_errors,'request_failures':failures,'panel_sha256':initial_hash},indent=2)+'\n')
                        raise
                    finally: browser.close()
                assert initial_hash == hashlib.sha256(source.read_bytes()).hexdigest(), 'Panel source changed during UI verification'
                REPORT_DIR.mkdir(parents=True, exist_ok=True)
                (REPORT_DIR / 'federated-work-calendar-ui-013.json').write_text(json.dumps({'checks': {'all_day_date_preserved_west_of_utc': True, 'all_day_date_preserved_on_dst_boundary': True, 'timed_entry_uses_declared_iana_zone': True}, 'browser_timezone': 'America/Los_Angeles', 'crypto_proof_sha256':hashlib.sha256(CRYPTO_PROOF.read_bytes()).hexdigest(), 'crypto_proof_panel_sha256': baseline[str(source.relative_to(ROOT))], 'ui_verified_panel_sha256': initial_hash, 'non_presentation_runtime_sources_match_crypto_proof': True, 'scope': 'Actual React panel and Intl rendering with owned client-derived calendar fixtures. Only the channel calendar-data load is mocked; real module exports, scope and cleanup remain intact. Encryption/auth policy remain covered by the separate independent-homeserver proof.'}, indent=2) + '\n')
                print('Shared calendar real React/Intl proof passed: three timezone presentation checks.')
        finally:
            entry.unlink(missing_ok=True)
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)


if __name__ == '__main__': main()
