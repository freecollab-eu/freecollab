#!/usr/bin/env python3
"""Actual first-SSO, workforce People UI and SCIM proof against private fixtures.

Reuses the existing customer proof's TLS/provider/process helpers. No root preview
service or real provider is modified. Its disposable API alone allows the exact
synthetic HTTPS fixture origins; production retains public-address pinning.
"""
from ui_navigation import open_chat_tool
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("customer_proof_helpers", Path(__file__).with_name("customer-sso-browser.py"))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
REPORT = Path(os.getenv("ENTERPRISE_ONBOARDING_PROOF_REPORT", str(ROOT.parent / ".freecollab/proof-results/enterprise-onboarding-browser.json")))
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def credentials(user, correct=True):
    return {"password": p.PASSWORD if correct else "Incorrect-private-fixture-password!",
            **({"code": p.totp_at(user["mfa_secret"], int(time.time() // 30))} if user.get("mfa_secret") else {})}


def confirmation(panel, user, title, correct=True):
    form = panel.get_by_role("form", name=title, exact=True)
    data = credentials(user, correct)
    form.get_by_label("FreeCollab password", exact=True).fill(data["password"])
    if "code" in data:
        form.get_by_label("Authentication or recovery code", exact=True).fill(data["code"])
    form.get_by_role("button", name="Confirm", exact=True).click()
    if correct:
        expect(form).to_have_count(0)
    else:
        expect(panel.get_by_role("alert")).to_contain_text(re.compile("password", re.I))
        expect(form.get_by_label("FreeCollab password", exact=True)).to_have_value("")


def people(page):
    panel = p.integrations(page)
    panel.get_by_role("button", name="People", exact=True).click()
    expect(panel.get_by_role("heading", name="Organization onboarding", exact=True)).to_be_visible()
    return panel


def policy_ui(panel, owner, mode, correct=True):
    panel.locator('input[name="workforce-onboarding-mode"][value="' + mode + '"]').check()
    save = panel.get_by_role("button", name="Save onboarding policy", exact=True)
    if mode == "jit":
        expect(save).to_be_disabled()
        panel.get_by_role("checkbox", name=re.compile("I have restricted who can access")).check()
    save.click()
    confirmation(panel, owner, "Confirm onboarding policy", correct)


def main():
    results, processes, streams, providers = {}, [], [], []
    stage = "private fixture startup"
    with tempfile.TemporaryDirectory(prefix="freecollab-enterprise-onboarding-") as directory:
        private = Path(directory)
        try:
            cert, key = p.tls_fixture(private / "tls")
            providers = [p.LocalProvider(cert_path=cert, key_path=key, client_id="workforce-a-" + p.RUN,
                                        client_secret=secrets.token_urlsafe(32)),
                         p.LocalProvider(cert_path=cert, key_path=key, client_id="workforce-b-" + p.RUN,
                                        client_secret=secrets.token_urlsafe(32))]
            api_port, app_port = p.port(), p.port()
            api_base, app_base = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{app_port}"
            database = private / "proof.db"
            trust = private / "ca-bundle.pem"
            inherited = os.getenv("SSL_CERT_FILE") or ssl.get_default_verify_paths().cafile
            trust.write_bytes((Path(inherited).read_bytes() if inherited else b"") + b"\n" + Path(cert).read_bytes())
            environment = os.environ.copy()
            environment.update(ENVIRONMENT="test", DATABASE_URL="sqlite:///" + str(database), STORAGE_PATH=str(private / "files"),
                JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),
                MESSAGE_ENCRYPTION_KEYS=json.dumps({"enterprise-proof": base64.b64encode(secrets.token_bytes(32)).decode()}),
                MESSAGE_ENCRYPTION_ACTIVE_KEY_ID="enterprise-proof", MESSAGE_ENCRYPTION_KEY_FILE=str(private / "keys.json"),
                ACCOUNT_SECURITY_KEY_FILE=str(private / "account.key"), MATRIX_ENABLED="false", MAIL_DELIVERY_MODE="disabled",
                PUBLIC_APP_URL=app_base, CORS_ORIGINS=json.dumps([app_base]), OIDC_ENABLED="false", OTRUST_ENABLED="false",
                SSL_CERT_FILE=str(trust), PYTHONPATH=str(ROOT / "backend"))
            environment["NO_PROXY"] = ",".join(filter(None, [environment.get("NO_PROXY", ""), "127.0.0.1", "localhost", "::1"]))
            local_python = ROOT / "backend/.venv/bin/python"
            python = os.getenv("BACKEND_PYTHON") or (str(local_python) if local_python.exists() else sys.executable)
            for command, filename, cwd in [([python, str(p.create_launcher(private, providers, api_port))], "api.log", ROOT / "backend"),
                (["node", str(p.create_vite(private, api_base, app_port))], "vite.log", ROOT / "frontend")]:
                stream = (private / filename).open("w"); streams.append(stream)
                processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, start_new_session=True))
            p.ready(api_base + "/ready", processes); p.ready(app_base + "/", processes)

            stage = "native owners and customer providers"
            owner_a = p.native_user(api_base, database, "Enterprise A Owner")
            setup = p.api(api_base, "POST", "/account/mfa/setup", owner_a, {"password": p.PASSWORD})
            response = p.api(api_base, "POST", "/account/mfa/confirm", owner_a,
                             {"code": p.totp_at(setup["secret"], int(time.time() // 30))})
            owner_a.update(response["session"])
            owner_a.update(mfa_secret=setup["secret"], codes=response["recovery_codes"])
            owner_b = p.native_user(api_base, database, "Enterprise B Owner")
            workspace_a = p.api(api_base, "POST", "/workspaces", owner_a, {"name": "Enterprise A"})
            workspace_b = p.api(api_base, "POST", "/workspaces", owner_b, {"name": "Enterprise B"})
            channel = p.api(api_base, "POST", "/channels", owner_a,
                {"name": "Onboarding room", "workspace_id": workspace_a["id"], "member_ids": []})
            configs = []
            for index, (owner, workspace, provider) in enumerate(zip((owner_a, owner_b), (workspace_a, workspace_b), providers)):
                endpoint = "/workspaces/" + workspace["id"] + "/sso"
                p.api(api_base, "PUT", endpoint, owner, {**credentials(owner), "name": "Enterprise " + ("A" if index == 0 else "B") + " SSO",
                    "issuer_url": provider.issuer, "client_id": provider.client_id, "client_secret": provider.client_secret, "scopes": "openid profile email"})
                p.api(api_base, "POST", endpoint + "/test", owner, credentials(owner))
                configs.append(p.api(api_base, "POST", endpoint + "/enable", owner, credentials(owner)))

            with sync_playwright() as playwright:
                launch = {"headless": True, "args": ["--no-sandbox"]}
                executable = os.getenv("CHROMIUM_PATH") or shutil.which("chromium")
                if executable: launch["executable_path"] = executable
                browser = playwright.chromium.launch(**launch)
                contexts, page_errors, urls = [], [], []
                try:
                    def new_page(width=1440):
                        context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000},
                            reduced_motion="reduce", permissions=["clipboard-read", "clipboard-write"])
                        contexts.append(context)
                        page = context.new_page(); page.set_default_timeout(30000)
                        page.on("pageerror", lambda error: page_errors.append(type(error).__name__))
                        page.on("request", lambda request: urls.append(request.url))
                        return page

                    stage = "People onboarding UI and password/MFA change confirmation"
                    admin = new_page(); p.login(admin, app_base, owner_a, workspace_a)
                    panel = people(admin)
                    policy_ui(panel, owner_a, "invited", correct=False)
                    assert p.api(api_base, "GET", "/auth/me", {"access_token": p.token(admin)})["id"] == owner_a["user_id"]
                    confirmation(panel, owner_a, "Confirm onboarding policy")
                    assert p.api(api_base, "GET", "/workspaces/" + workspace_a["id"] + "/identity-policy", owner_a)["mode"] == "invited"
                    results["native_admin_mfa_policy_change_and_failed_password_session_retention"] = True

                    stage = "one-use organization invitation in actual UI"
                    panel.get_by_label("New person’s role", exact=True).select_option("guest")
                    panel.get_by_role("button", name="Create organization invitation", exact=True).click()
                    confirmation(panel, owner_a, "Create organization invitation?")
                    invitation_url = panel.get_by_label("Organization invitation link", exact=True).input_value()
                    invitation_token = p.parse_qs(p.urlsplit(invitation_url).fragment)["workspace-invite"][0]
                    panel.get_by_role("button", name="Copy organization invitation", exact=True).click()
                    assert admin.evaluate("() => navigator.clipboard.readText()") == invitation_url
                    panel.get_by_role("button", name="Hide invitation link", exact=True).click()
                    assert invitation_token not in json.dumps(p.api(api_base, "GET", "/workspaces/" + workspace_a["id"] + "/workforce/invites", owner_a))
                    results["one_use_organization_invite_copy_and_no_token_in_list"] = True

                    stage = "managed first SSO with conflicting personal email and no native account"
                    providers[0].subject, providers[0].email, providers[0].name = "invited-worker-" + p.RUN, owner_a["email"], "Asta Berg"
                    providers[0].claim_overrides = {"email_verified": False}
                    worker_page = new_page(width=320)
                    p.sso(worker_page, invitation_url, "Enterprise A SSO")
                    worker = {"access_token": p.token(worker_page)}
                    me = p.api(api_base, "GET", "/auth/me", worker); worker["user_id"] = me["id"]
                    assert me["account_type"] == "managed" and me["session_scope"] == "workspace"
                    assert me["id"] != owner_a["user_id"] and me["email"] == owner_a["email"] and me["role"] == "user"
                    records = p.api(api_base, "GET", "/workspaces", worker)
                    assert len(records) == 1 and records[0]["id"] == workspace_a["id"] and records[0]["role"] == "guest"
                    p.rejects(api_base, "GET", "/workspaces/" + workspace_b["id"], worker, statuses=(403,))
                    p.rejects(api_base, "GET", "/account/security", worker, statuses=(403,))
                    assert p.api(api_base, "GET", "/secure/status", worker)["requires_encryption_unlock"] is False
                    assert not worker_page.evaluate("() => sessionStorage.getItem('freecollab_pending_workforce_invite')")
                    p.no_saved_secrets(worker_page, [invitation_token, providers[0].client_secret])
                    open_chat_tool(worker_page, 'Account settings')
                    expect(worker_page.get_by_role("region", name="Organization-managed account", exact=True)).to_be_visible()
                    assert worker_page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                    with sqlite3.connect(database) as connection:
                        native_email = connection.execute('SELECT email FROM user WHERE id=?', (worker["user_id"],)).fetchone()[0]
                        assert native_email.endswith("@identity.invalid")
                        assert connection.execute('SELECT COUNT(*) FROM personalworkspace WHERE user_id=?', (worker["user_id"],)).fetchone()[0] == 0
                        assert connection.execute('SELECT email_verified_at FROM accountsecurity WHERE user_id=?', (worker["user_id"],)).fetchone()[0] is None
                    results["actual_first_sso_without_native_password_or_global_email_verification"] = True
                    results["same_email_cannot_merge_or_claim_personal_account"] = True
                    results["managed_account_scope_and_passwordless_crypto_eligibility"] = True
                    results["managed_account_ui_mobile_320px"] = True

                    stage = "SCIM token UI and one-time credential guard"
                    panel.get_by_role("button", name="Directory & groups", exact=True).click()
                    panel.get_by_role("button", name="Create provisioning token", exact=True).click()
                    confirmation(panel, owner_a, "Create provisioning token?")
                    secret_region = panel.get_by_role("region", name="Save your provisioning token", exact=True)
                    provision_token = secret_region.get_by_label("Provisioning bearer token", exact=True).input_value()
                    expect(secret_region.get_by_label("Provisioning bearer token", exact=True)).to_have_attribute("type", "password")
                    panel.get_by_role("button", name="Done", exact=True).click()
                    expect(panel).to_be_visible()
                    expect(secret_region.get_by_role("button", name="Hide stored token", exact=True)).to_be_disabled()
                    secret_region.get_by_role("checkbox", name="I stored this token in my identity provider’s secure settings.", exact=True).check()
                    secret_region.get_by_role("button", name="Hide stored token", exact=True).click()
                    expect(secret_region).to_have_count(0)
                    endpoint_url = panel.get_by_label("SCIM endpoint URL", exact=True).input_value()
                    scim_base = api_base + p.urlsplit(endpoint_url).path.removeprefix("/api")
                    scim_auth = {"access_token": provision_token}
                    p.no_saved_secrets(admin, [provision_token, providers[0].client_secret])
                    results["scim_token_native_mfa_one_time_display_and_leave_guard"] = True

                    stage = "provisioned onboarding policy and real SCIM user/group requests"
                    panel.get_by_role("button", name="Onboarding", exact=True).click()
                    policy_ui(panel, owner_a, "provisioned")
                    directory_user = p.api(scim_base, "POST", "/Users", scim_auth,
                        {"schemas": [USER_SCHEMA], "userName": "asta-" + p.RUN + "@example.com", "externalId": providers[0].subject,
                         "displayName": "Asta Berg", "active": True, "emails": [{"value": owner_a["email"], "primary": True}]})
                    directory_group = p.api(scim_base, "POST", "/Groups", scim_auth,
                        {"schemas": [GROUP_SCHEMA], "displayName": "Project team", "externalId": "project-" + p.RUN,
                         "members": [{"value": directory_user["id"]}]})
                    assert p.api(api_base, "GET", "/auth/me", worker)["id"] == worker["user_id"]
                    p.rejects(api_base, "GET", "/auth/me", scim_auth, statuses=(401,))
                    other_directory = p.api(api_base, "POST", "/workspaces/" + workspace_b["id"] + "/scim/token", owner_b, credentials(owner_b))
                    other_scim = api_base + p.urlsplit(other_directory["endpoint_url"]).path.removeprefix("/api")
                    p.rejects(other_scim, "GET", "/Users", scim_auth, statuses=(401,))
                    p.rejects(other_scim, "GET", "/Users/" + directory_user["id"], {"access_token": other_directory["token"]}, statuses=(404,))
                    results["real_scim_users_groups_and_existing_managed_history_preserved"] = True
                    results["scim_bearer_is_directory_scoped_and_not_native_login"] = True

                    stage = "actual UI group-to-channel role mapping"
                    panel.get_by_role("button", name="Directory & groups", exact=True).click()
                    expect(panel.get_by_role("button", name="Add group rule", exact=True)).to_be_enabled()
                    panel.get_by_role("button", name="Add group rule", exact=True).click()
                    group_rule = panel.get_by_role("group", name="Project team", exact=True)
                    group_rule.get_by_label("Workspace role granted by this group", exact=True).select_option("member")
                    group_rule.get_by_role("checkbox", name="Onboarding room", exact=True).check()
                    panel.get_by_role("button", name="Save group access rules", exact=True).click()
                    confirmation(panel, owner_a, "Confirm group access rules")
                    channels = p.api(api_base, "GET", "/channels?workspace_id=" + workspace_a["id"], worker)
                    assert any(item["id"] == channel["id"] for item in channels)
                    assert p.api(api_base, "GET", "/auth/me", worker)["role"] == "user"
                    p.api(scim_base, "PATCH", "/Groups/" + directory_group["id"], scim_auth,
                        {"schemas": [PATCH_SCHEMA], "Operations": [{"op": "remove", "path": 'members[value eq "' + directory_user["id"] + '"]'}]})
                    p.rejects(api_base, "GET", "/messages/channels/" + channel["id"], worker, statuses=(403,))
                    p.api(scim_base, "PATCH", "/Groups/" + directory_group["id"], scim_auth,
                        {"schemas": [PATCH_SCHEMA], "Operations": [{"op": "add", "path": "members", "value": [{"value": directory_user["id"]}]}]})
                    assert p.api(api_base, "GET", "/messages/channels/" + channel["id"], worker) == []
                    results["group_mapping_ui_grants_and_revokes_only_selected_channel"] = True

                    stage = "SCIM deactivation revokes issued workspace session and preserves personal native account"
                    p.api(scim_base, "PATCH", "/Users/" + directory_user["id"], scim_auth,
                        {"schemas": [PATCH_SCHEMA], "Operations": [{"op": "replace", "path": "active", "value": False}]})
                    p.rejects(api_base, "GET", "/auth/me", worker, statuses=(401,))
                    assert p.api(api_base, "GET", "/auth/me", owner_a)["id"] == owner_a["user_id"]
                    results["directory_deactivation_revokes_existing_customer_session"] = True
                    results["same_email_personal_owner_and_other_workspace_survive_offboarding"] = True

                    stage = "credential rotation invalidates old SCIM token"
                    panel.get_by_role("button", name="Rotate provisioning token", exact=True).click()
                    confirmation(panel, owner_a, "Replace the provisioning token?")
                    replacement_region = panel.get_by_role("region", name="Save your provisioning token", exact=True)
                    replacement = replacement_region.get_by_label("Provisioning bearer token", exact=True).input_value()
                    assert replacement != provision_token
                    p.rejects(scim_base, "GET", "/Users", scim_auth, statuses=(401,))
                    assert p.api(scim_base, "GET", "/Users", {"access_token": replacement})["totalResults"] == 1
                    replacement_region.get_by_role("checkbox", name="I stored this token in my identity provider’s secure settings.", exact=True).check()
                    replacement_region.get_by_role("button", name="Hide stored token", exact=True).click()
                    p.no_saved_secrets(admin, [provision_token, replacement])
                    results["scim_credential_rotation_and_no_browser_secret_storage"] = True

                    stage = "JIT explicit consent and managed SSO at320px"
                    panel.get_by_role("button", name="Onboarding", exact=True).click()
                    policy_ui(panel, owner_a, "jit")
                    providers[0].subject, providers[0].email, providers[0].name = "jit-worker-" + p.RUN, owner_b["email"], "Elias Strand"
                    jit_page = new_page(width=320)
                    p.sso(jit_page, configs[0]["login_url"], "Enterprise A SSO")
                    jit_user = p.api(api_base, "GET", "/auth/me", {"access_token": p.token(jit_page)})
                    assert jit_user["account_type"] == "managed" and jit_user["id"] != owner_b["user_id"]
                    assert jit_user["workspace_id"] == workspace_a["id"] and jit_user["role"] == "user"
                    providers[0].subject = "invited-worker-" + p.RUN
                    denied_page = new_page()
                    denied_page.goto(configs[0]["login_url"], wait_until="domcontentloaded")
                    p.rejected_sso(denied_page, "Enterprise A SSO", "Your organization must provision or invite this identity.")
                    assert not p.token(denied_page)
                    results["jit_requires_explicit_admin_consent_and_only_creates_workspace_member"] = True
                    results["deactivated_person_cannot_rejoin_through_jit"] = True

                    stage = "safe mobile screenshots and native fallback"
                    panel.get_by_role("button", name="Refresh organization onboarding", exact=True).click()
                    expect(panel.get_by_role("heading", name="Managed organization accounts", exact=True)).to_be_visible()
                    REPORT.parent.mkdir(parents=True, exist_ok=True)
                    admin.screenshot(path=str(REPORT.parent / "enterprise-people-desktop.png"))
                    admin.set_viewport_size({"width": 320, "height": 1000})
                    assert admin.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                    admin.screenshot(path=str(REPORT.parent / "enterprise-people-mobile.png"))
                    fallback = new_page(width=320); p.login(fallback, app_base, owner_a, workspace_a)
                    assert not page_errors, "Unexpected browser page exception"
                    assert not any("access_token=" in url or re.search(r"eyJ[A-Za-z0-9_-]+\.", url) for url in urls)
                    for stream in streams: stream.flush()
                    sensitive = [provider.client_secret for provider in providers] + [provision_token, replacement]
                    assert not any(secret in url for secret in sensitive for url in urls)
                    assert all(secret not in filename.read_text(errors="replace") for secret in sensitive for filename in [private / "api.log", private / "vite.log"])
                    results["people_admin_mobile_320px_and_native_mfa_fallback"] = True
                    results["no_bearer_or_provider_secret_in_urls_or_fixture_logs"] = True
                    results["zero_browser_page_errors"] = True
                    print("PASS actual invited/JIT first SSO, personal-email isolation, admin People/MFA and320px", flush=True)
                    print("PASS actual SCIM user/group provisioning, mapped channel access, activefalse session revocation and token rotation", flush=True)
                finally:
                    for context in contexts: context.close()
                    browser.close()
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps({"scope": "Real isolated API/Vite/Chromium, two synthetic HTTPS IdPs and real SCIM HTTP requests; Matrix disabled in this UI proof",
                "results": results, "cleanup": "Own services, databases, keys, CA bundle and browser contexts removed in finally"}, indent=2) + "\n")
            REPORT.chmod(0o600)
        except Exception as failure:
            # Never emit Playwright call logs, input values, tokens or authcodes.
            print("FAIL enterprise onboarding proof stage:", stage, "(" + type(failure).__name__ + ")", file=sys.stderr)
            for line in str(failure).splitlines():
                if "waiting for get_by_role(" in line:
                    print("Safe locator diagnostic:", line.strip()[:180], file=sys.stderr); break
            raise SystemExit(1) from None
        finally:
            p.stop(processes)
            for stream in streams: stream.close()
            for provider in providers: provider.close()


if __name__ == "__main__":
    main()
