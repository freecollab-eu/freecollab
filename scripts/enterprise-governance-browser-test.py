#!/usr/bin/env python3
"""Render real governance UI in Chromium against explicit UI protocol fixtures.

The backend security suite tests real SQL/API behavior separately. This harness
creates temporary Vite entry files, intercepts only its own browser API traffic,
and removes the entries/server on exit. It never signs in to a live account.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); port = listener.getsockname()[1]
    stamp = str(time.time_ns())
    entry = frontend / (".governance-browser-" + stamp + ".tsx")
    page_file = frontend / ("governance-browser-" + stamp + ".html")
    entry.write_text("""import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {EnterpriseGovernancePanel} from './src/components/EnterpriseGovernancePanel';
import {api} from './src/lib/api';
import './src/index.css'; import './src/styles/account.css';
api.useMemorySession(); api.applySession({access_token:'isolated-ui-fixture',token_type:'bearer',user_id:'owner',public_key:'',encrypted_private_key:null});
function Test(){const [workspace,setWorkspace]=useState('a'); return <main style={{maxWidth:900,margin:'20px auto',padding:20}}><button type="button" onClick={()=>setWorkspace('b')}>Switch fixture workspace</button><EnterpriseGovernancePanel workspaceId={workspace} mfaEnabled /></main>};
createRoot(document.getElementById('root')!).render(<Test/>);
""")
    page_file.write_text('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/' + entry.name + '"></script></body></html>')
    checks = []
    try:
        with tempfile.TemporaryFile(mode="w+") as log:
            server = subprocess.Popen([str(frontend / "node_modules/.bin/vite"), "--host", "127.0.0.1", "--port", str(port), "--strictPort"], cwd=frontend, stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f"http://127.0.0.1:{port}/" + page_file.name
                for _ in range(100):
                    if server.poll() is not None:
                        log.seek(0); raise RuntimeError("Isolated Vite failed: " + log.read()[-2000:])
                    try:
                        with urlopen(url, timeout=1): break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError("Isolated Vite did not start")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=["--no-sandbox"])
                    context = browser.new_context(viewport={"width": 1280, "height": 960}, locale="en-US")
                    requests, errors = [], []
                    state = {"organization_id": "org", "policies": [], "cases": [{"id": "case", "title": "Customer evidence case", "channel_ids": ["native"], "custodian_ids": [], "status": "active", "revision": 1, "created_at": "2026-10-21T12:00:00", "released_at": None}], "retention_hold": True, "case_hold_active": True, "kms": {"provider": "local_versioned_keyring", "enabled": False, "configured": False, "automatic_rotation": False}}
                    organization = {"id": "org", "name": "Example organization", "role": "owner", "require_sso": False, "revision": 1, "country_code": "SE", "region_label": "Customer region", "workspaces": [{"id": "a", "name": "Product team"}]}
                    def fixture(route):
                        request = route.request; path = request.url.split('/api', 1)[1]; payload = request.post_data_json if request.method not in {"GET", "HEAD"} else None
                        requests.append({"method": request.method, "path": path, "body": payload})
                        if path == "/organizations": result = {"organizations": [organization]}
                        elif path == "/organizations/org/usage": result = {"workspaces": [{"workspace_id": "a", "members": 12, "channels": 3, "secure_channels": 1, "native_messages": 47, "stored_file_bytes": 1048576}]}
                        elif path.endswith("/data-controls"): result = {"policy": {"enabled": False, "hold": True, "retention_days": 365, "revision": 1, "automatic_purge": False}, "channels": [{"id": "native", "name": "Product", "secure": False, "is_admin": True}], "jobs": [], "limits": {"channels": 20, "records": 10000, "archive_bytes": 134217728}}
                        elif path.endswith("/members"): result = [{"user_id": "owner", "name": "A. Owner", "role": "owner"}]
                        elif path.endswith("/governance/audit"): result = {"events": [{"id": "event", "actor_id": "owner", "action": "case.preserve", "target_id": "case", "created_at": "2026-10-21T12:00:00"}]}
                        elif path == "/workspaces/a/governance/dlp" and request.method == "POST":
                            assert payload["password"] == "fixture-password" and payload["code"] == "123456"
                            result = {key: value for key, value in payload.items() if key not in {"password", "code", "literal"}}
                            result.update(id="policy", revision=1, has_literal=True); state["policies"].append(result)
                        elif path.endswith("/cases/case/exports"): result = {"exports": []}
                        elif path.endswith("/cases/case/release"):
                            assert payload["confirmation"] == "RELEASE PRESERVATION"
                            state["cases"][0].update(status="released", revision=2); state["case_hold_active"] = False; result = state["cases"][0]
                        elif path == "/workspaces/b/governance": result = {**state, "organization_id": None, "policies": [], "cases": [], "case_hold_active": False}
                        elif path == "/workspaces/a/governance": result = state
                        else: route.fulfill(status=404, json={"detail": "Unknown isolated UI fixture"}); return
                        route.fulfill(status=200, json=result)
                    context.route("**/api/**", fixture)
                    page = context.new_page(); page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url); page.get_by_role("heading", name="Shared standards. Clear boundaries.").wait_for()
                    page.get_by_text("47", exact=True).wait_for(); checks.append("organization aggregate counts render")
                    page.get_by_role("button", name="Content policies", exact=True).click()
                    page.get_by_label("Policy name", exact=True).fill("Protected project phrase")
                    page.get_by_label("Detect", exact=True).select_option("literal")
                    page.get_by_label("Protected phrase", exact=True).fill("CONFIDENTIAL-FIXTURE")
                    page.get_by_role("button", name="Add policy", exact=True).click()
                    assert not any(item["method"] == "POST" for item in requests)
                    page.get_by_label("FreeCollab password", exact=True).fill("fixture-password")
                    page.get_by_label("Authentication or recovery code", exact=True).fill("123456")
                    page.get_by_role("button", name="Confirm", exact=True).click()
                    page.get_by_text("Governance settings updated.").wait_for()
                    assert page.get_by_label("Protected phrase", exact=True).input_value() == ""
                    checks.append("policy requires password and MFA and clears protected phrase")
                    page.get_by_role("button", name="Preservation cases", exact=True).click()
                    page.get_by_text("Customer evidence case", exact=True).wait_for()
                    page.get_by_role("button", name="Release", exact=True).click()
                    page.get_by_label("FreeCollab password", exact=True).fill("fixture-password")
                    page.get_by_label("Authentication or recovery code", exact=True).fill("123456")
                    page.get_by_role("button", name="Confirm", exact=True).click()
                    page.get_by_text("No active case hold", exact=True).wait_for()
                    assert page.get_by_text("Workspace retention hold active", exact=True).is_visible()
                    checks.append("case release preserves independent retention hold display")
                    page.get_by_role("button", name="Audit trail", exact=True).click()
                    page.get_by_text("case.preserve", exact=True).wait_for()
                    assert "CONFIDENTIAL-FIXTURE" not in page.locator("body").inner_text()
                    checks.append("audit renders metadata without matched phrase")
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    checks.append("mobile viewport has no horizontal overflow")
                    page.get_by_role("button", name="Switch fixture workspace", exact=True).click()
                    page.get_by_role("button", name="Preservation cases", exact=True).click()
                    page.get_by_text("No active case hold", exact=True).wait_for()
                    assert not page.get_by_text("Customer evidence case", exact=True).count()
                    checks.append("workspace switch clears old case state")
                    assert not errors, errors
                    if args.output:
                        args.output.parent.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.output.with_suffix(".png")), full_page=True)
                    browser.close()
            finally:
                server.terminate()
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired: server.kill(); server.wait()
    finally:
        entry.unlink(missing_ok=True); page_file.unlink(missing_ok=True)
    result = {"passed": len(checks), "checks": checks, "ui_protocol_fixture": True, "live_provider_credentials_used": False}
    if args.output: args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__": main()
