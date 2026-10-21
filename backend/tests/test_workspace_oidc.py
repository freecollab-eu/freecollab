"""Customer-owned providers: real local OIDC, explicit native linking, tenant scope."""
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit

from fastapi import HTTPException
import httpx
import pytest
from sqlmodel import Session, select

from app.api import workspace_oidc
from app.models.account_security import AccountSecurity
from app.models.oidc import OIDCIdentity, OIDCLoginAttempt
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.models.workspace_oidc import (WorkspaceOIDCProvider, WorkspaceOIDCAttempt, WorkspaceOIDCIdentity,
                                       WorkspaceOIDCSession, WorkspaceOIDCMFAChallenge)
from app.services import oidc, oidc_transport
from app.services.account_security import digest, seal_private, totp_at, unseal_private
from app.services.auth import decode_token_claims
from tests.oidc_mock import LocalProvider
from tests.test_oidc import sso, native_user, callback, finish, provider_redirect, PASSWORD


@pytest.fixture
def tenants(sso, monkeypatch):
    client, first, engine = sso
    second = LocalProvider(client_id="second-customer", client_secret="second-secret")
    from app.config import settings
    monkeypatch.setattr(settings, "oidc_enabled", False)
    client.app.include_router(workspace_oidc.router)
    allowed = {first.issuer, second.issuer}
    original_validate = oidc_transport.validate_public_url

    def validate(value, *, issuer=False):
        # Test-only exact fixture origins; production has no allow-private setting.
        if any(value == origin or value.startswith(origin + "/") for origin in allowed):
            return value
        return original_validate(value, issuer=issuer)

    def request(method, url, *, data=None, auth=None, **kwargs):
        validate(url)
        return oidc.request_json(method, url, data=data, auth=httpx.BasicAuth(*auth) if auth else None)

    monkeypatch.setattr(oidc_transport, "validate_public_url", validate)
    monkeypatch.setattr(oidc_transport, "request_json", request)
    owner1, token1 = native_user(engine, "owner-one@example.com")
    owner2, token2 = native_user(engine, "owner-two@example.com")
    member, member_token = native_user(engine, "member@example.com")
    with Session(engine) as session:
        session.add(Workspace(id="tenant-one", name="First customer"))
        session.add(Workspace(id="tenant-two", name="Second customer"))
        session.flush()
        for wid, uid, role in [("tenant-one", owner1, "owner"), ("tenant-two", owner2, "owner"),
                               ("tenant-one", member, "member"), ("tenant-two", owner1, "member")]:
            session.add(WorkspaceMember(workspace_id=wid, user_id=uid, role=role))
        session.commit()
    yield {"client": client, "first": first, "second": second, "engine": engine,
           "owner1": owner1, "token1": token1, "owner2": owner2, "token2": token2,
           "member": member, "member_token": member_token}
    second.close()


def headers(token):
    return {"Authorization": "Bearer " + token}


def save(t, wid="tenant-one", token=None, provider=None, **changes):
    provider = provider or t["first"]
    data = {"name": "Customer identity", "issuer_url": provider.issuer, "client_id": provider.client_id,
            "client_secret": provider.client_secret, "scopes": "openid profile email", "password": PASSWORD}
    data.update(changes)
    return t["client"].put(f"/workspaces/{wid}/sso", headers=headers(token or t["token1"]), json=data)


def action(t, action_name, wid="tenant-one", token=None, **data):
    return t["client"].post(f"/workspaces/{wid}/sso/{action_name}", headers=headers(token or t["token1"]),
                            json={"password": PASSWORD, **data})


def enable(t, wid="tenant-one", token=None, provider=None):
    response = save(t, wid, token, provider)
    assert response.status_code == 200, response.text
    assert action(t, "test", wid, token).status_code == 200
    response = action(t, "enable", wid, token)
    assert response.status_code == 200, response.text
    return response.json()["provider"]


def begin(t, provider, *, token=None, workspace_id="tenant-one", **body):
    if token:
        response = t["client"].post("/auth/oidc/link/start", headers=headers(token),
                                    json={"workspace_id": workspace_id, "password": PASSWORD, **body})
    else:
        response = t["client"].post("/auth/oidc/start", json={"login_id": provider["login_id"]})
    assert response.status_code == 200, response.text
    return response.json()["authorization_url"]


def link(t, provider, token=None, workspace_id="tenant-one", **body):
    result = finish(t["client"], begin(t, provider, token=token or t["token1"], workspace_id=workspace_id, **body))
    assert result["linked"] and result["workspace_id"] == workspace_id
    return result


def test_two_customer_configs_are_admin_isolated_secret_private_and_discovery_tested(tenants):
    t = tenants; client = t["client"]
    initial = client.get("/workspaces/tenant-one/sso", headers=headers(t["token1"]))
    assert initial.status_code == 200 and not initial.json()["configured"]
    assert initial.json()["callback_url"].endswith("/api/auth/oidc/callback")
    for token in [t["token2"], t["member_token"]]:
        assert client.get("/workspaces/tenant-one/sso", headers=headers(token)).status_code == 403
        assert save(t, token=token).status_code == 403
    assert save(t, password="wrong").status_code == 403
    draft = save(t)
    assert draft.status_code == 200, draft.text
    assert draft.json()["provider"]["has_secret"] and not draft.json()["provider"]["enabled"]
    assert "client-secret" not in draft.text and "encrypted_client_secret" not in draft.text
    assert action(t, "enable").status_code == 409
    first = enable(t)
    second = enable(t, "tenant-two", t["token2"], t["second"])
    assert first["login_id"] != second["login_id"] and first["tested"]
    assert client.get("/auth/oidc/providers").json() == {"providers": []}
    for provider in [first, second]:
        found = client.get("/auth/oidc/providers", params={"login_id": provider["login_id"]}).json()["providers"]
        assert len(found) == 1 and found[0]["workspace_id"] == provider["workspace_id"]
        assert "client_id" not in found[0]
    assert client.get("/auth/oidc/providers", params={"workspace_id": "tenant-one"}).status_code == 401
    assert client.get("/auth/oidc/providers", params={"workspace_id": "tenant-one"}, headers=headers(t["token2"])).status_code == 403
    found = client.get("/auth/oidc/providers", params={"workspace_id": "tenant-one"}, headers=headers(t["member_token"]))
    assert found.json()["providers"][0]["login_id"] == first["login_id"]


def test_secret_retain_rotation_clear_and_purpose_workspace_binding(tenants):
    t = tenants
    assert save(t).status_code == 200
    with Session(t["engine"]) as session:
        ciphertext = session.get(WorkspaceOIDCProvider, "tenant-one").encrypted_client_secret
    assert ciphertext != "client-secret"
    assert unseal_private(ciphertext, "workspace-oidc-client-secret", "tenant-one") == "client-secret"
    with pytest.raises(HTTPException):
        unseal_private(ciphertext, "workspace-oidc-client-secret", "tenant-two")
    with pytest.raises(HTTPException):
        unseal_private(ciphertext, "oidc-pkce", "tenant-one")
    assert save(t, client_secret="").status_code == 200
    with Session(t["engine"]) as session:
        assert session.get(WorkspaceOIDCProvider, "tenant-one").encrypted_client_secret == ciphertext
    assert save(t, client_secret="rotated-secret").status_code == 200
    with Session(t["engine"]) as session:
        rotated = session.get(WorkspaceOIDCProvider, "tenant-one").encrypted_client_secret
        assert rotated != ciphertext
        assert unseal_private(rotated, "workspace-oidc-client-secret", "tenant-one") == "rotated-secret"
    assert not save(t, client_secret=None).json()["provider"]["has_secret"]


@pytest.mark.parametrize("changed_identity", ["issuer", "client", "both"])
@pytest.mark.parametrize("replacement_secret", ["", "omitted"])
def test_other_native_admin_cannot_retarget_retained_provider_secret(tenants, monkeypatch, changed_identity, replacement_secret):
    t = tenants
    assert save(t).status_code == 200
    admin, admin_token = native_user(t["engine"], "other-admin@example.com")
    with Session(t["engine"]) as session:
        session.add(WorkspaceMember(workspace_id="tenant-one", user_id=admin, role="admin"))
        session.commit()
        provider = session.get(WorkspaceOIDCProvider, "tenant-one")
        original_secret, original_revision = provider.encrypted_client_secret, provider.revision
    config_read = t["client"].get("/workspaces/tenant-one/sso", headers=headers(admin_token))
    assert config_read.status_code == 200 and config_read.json()["provider"]["has_secret"]
    assert t["first"].client_secret not in config_read.text and "encrypted_client_secret" not in config_read.text
    data = {"name": "Replacement identity", "issuer_url": t["first"].issuer,
            "client_id": t["first"].client_id, "password": PASSWORD}
    if changed_identity in {"issuer", "both"}:
        data["issuer_url"] = t["second"].issuer
    if changed_identity in {"client", "both"}:
        data["client_id"] = t["second"].client_id
    if replacement_secret != "omitted":
        data["client_secret"] = replacement_secret
    failed = t["client"].put("/workspaces/tenant-one/sso", headers=headers(admin_token),
                             json={**data, "password": "wrong-password"})
    assert failed.status_code == 403
    with Session(t["engine"]) as session:
        provider = session.get(WorkspaceOIDCProvider, "tenant-one")
        assert provider.encrypted_client_secret == original_secret and provider.revision == original_revision
    changed = t["client"].put("/workspaces/tenant-one/sso", headers=headers(admin_token), json=data)
    assert changed.status_code == 200 and not changed.json()["provider"]["has_secret"]
    with Session(t["engine"]) as session:
        assert session.get(WorkspaceOIDCProvider, "tenant-one").encrypted_client_secret is None
    assert action(t, "test", token=admin_token).status_code == 200
    enabled = action(t, "enable", token=admin_token)
    assert enabled.status_code == 200
    calls = []
    original_request = oidc_transport.request_json
    def observed_request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("auth"), "client_secret" in (kwargs.get("data") or {})))
        return original_request(method, url, **kwargs)
    monkeypatch.setattr(oidc_transport, "request_json", observed_request)
    # These confidential-client fixtures cannot exchange without replacement
    # credentials. In particular no old credential reaches either token endpoint.
    result = callback(t["client"], begin(t, enabled.json()["provider"]))
    assert "sso_error" in result.headers["location"]
    assert not any(method == "POST" for method, *_ in calls)


def test_other_native_admin_can_replace_secret_for_changed_provider_identity(tenants):
    t = tenants
    assert save(t).status_code == 200
    admin, admin_token = native_user(t["engine"], "replacement-admin@example.com")
    with Session(t["engine"]) as session:
        session.add(WorkspaceMember(workspace_id="tenant-one", user_id=admin, role="admin"))
        session.commit()
    changed = save(t, token=admin_token, provider=t["second"])
    assert changed.status_code == 200 and changed.json()["provider"]["has_secret"]
    with Session(t["engine"]) as session:
        provider = session.get(WorkspaceOIDCProvider, "tenant-one")
        assert unseal_private(provider.encrypted_client_secret, "workspace-oidc-client-secret", "tenant-one") == t["second"].client_secret
    assert action(t, "test", token=admin_token).status_code == 200
    assert action(t, "enable", token=admin_token).status_code == 200


def test_secret_retention_canonicalizes_only_issuer_network_aliases(tenants, monkeypatch):
    t = tenants
    issuers = {"https://IDP.example:443/tenant", "https://idp.example/tenant",
               "https://idp.example/Tenant", "https://idp.example/tenant/"}
    original_validate = oidc_transport.validate_public_url
    def exact_fixture_urls(value, *, issuer=False):
        # Saving configuration performs no HTTP requests. These exact synthetic
        # aliases bypass only DNS validation, with no production policy change.
        if value in issuers:
            return value
        return original_validate(value, issuer=issuer)
    monkeypatch.setattr(oidc_transport, "validate_public_url", exact_fixture_urls)
    assert save(t, issuer_url="https://IDP.example:443/tenant").status_code == 200
    with Session(t["engine"]) as session:
        ciphertext = session.get(WorkspaceOIDCProvider, "tenant-one").encrypted_client_secret
    same = save(t, issuer_url="https://idp.example/tenant", client_secret="")
    assert same.status_code == 200 and same.json()["provider"]["has_secret"]
    with Session(t["engine"]) as session:
        assert session.get(WorkspaceOIDCProvider, "tenant-one").encrypted_client_secret == ciphertext
    # OIDC paths are identifiers: neither path case nor its trailing slash is
    # an authority alias that can reuse a different tenant's credential.
    for issuer in ["https://idp.example/Tenant", "https://idp.example/tenant/"]:
        assert save(t, issuer_url="https://idp.example/tenant").status_code == 200
        result = save(t, issuer_url=issuer, client_secret="")
        assert result.status_code == 200 and not result.json()["provider"]["has_secret"]


def test_customer_claims_never_create_global_user_or_autolink_email(tenants):
    t = tenants; provider = enable(t)
    for email in ["victim-not-registered@example.com", "owner-one@example.com"]:
        t["first"].email = email
        response = callback(t["client"], begin(t, provider))
        assert "#sso_error=workspace_link_required" in response.headers["location"]
    with Session(t["engine"]) as session:
        assert len(session.exec(select(User)).all()) == 3
        assert not session.exec(select(OIDCIdentity)).all()
        assert session.get(AccountSecurity, t["owner1"]).email_verified_at
    # No trust is placed in the IdP's email_verified claim even for explicit links.
    t["first"].claim_overrides = {"email_verified": False, "email": "someone-else@example.com"}
    link(t, provider)
    result = finish(t["client"], begin(t, provider))
    assert result["user_id"] == t["owner1"] and result["workspace_id"] == "tenant-one"
    claims = decode_token_claims(result["access_token"])
    assert claims["amr"] == ["sso"] and claims["sso_workspace_id"] == "tenant-one"
    with Session(t["engine"]) as session:
        assert session.exec(select(WorkspaceOIDCSession)).one().provider_revision == provider["revision"]
        assert len(session.exec(select(User)).all()) == 3


def test_same_issuer_subject_different_tenants_require_separate_explicit_native_links(tenants):
    t = tenants
    first = enable(t)
    second = enable(t, "tenant-two", t["token2"], t["first"])
    link(t, first)
    assert "workspace_link_required" in callback(t["client"], begin(t, second)).headers["location"]
    link(t, second, t["token2"], "tenant-two")
    first_result = finish(t["client"], begin(t, first))
    second_result = finish(t["client"], begin(t, second))
    assert first_result["user_id"] == t["owner1"]
    assert second_result["user_id"] == t["owner2"]
    with Session(t["engine"]) as session:
        identities = session.exec(select(OIDCIdentity)).all()
        assert len(identities) == 2 and identities[0].id != identities[1].id
        assert {b.workspace_id for b in session.exec(select(WorkspaceOIDCIdentity)).all()} == {"tenant-one", "tenant-two"}
    # A tenant owner cannot attach another native account to its already linked subject.
    response = callback(t["client"], begin(t, second, token=t["token1"], workspace_id="tenant-two"))
    assert "identity_already_linked" in response.headers["location"]


def test_independent_customer_provider_clients_jwks_and_scoped_workspaces(tenants):
    t = tenants; first = enable(t); second = enable(t, "tenant-two", t["token2"], t["second"])
    link(t, first); link(t, second, t["token2"], "tenant-two")
    one = finish(t["client"], begin(t, first)); two = finish(t["client"], begin(t, second))
    assert one["user_id"] == t["owner1"] and two["user_id"] == t["owner2"]
    for result, wid in [(one, "tenant-one"), (two, "tenant-two")]:
        token = result["access_token"]
        assert [w["id"] for w in t["client"].get("/workspaces", headers=headers(token)).json()] == [wid]
        assert t["client"].get(f"/workspaces/{wid}/sso", headers=headers(token)).status_code == 403
        assert t["client"].post("/auth/oidc/link/start", headers=headers(token), json={"workspace_id": wid, "password": PASSWORD}).status_code == 403
    t["second"].claim_overrides = {"aud": t["first"].client_id}
    assert "sign_in_failed" in callback(t["client"], begin(t, second)).headers["location"]


def test_revision_change_invalidates_callback_handoff_and_issued_session(tenants):
    t = tenants; provider = enable(t); link(t, provider)
    active = finish(t["client"], begin(t, provider))["access_token"]
    pending = begin(t, provider)
    ready = callback(t["client"], begin(t, provider)).headers["location"].split("#sso=", 1)[1]
    assert action(t, "disable").status_code == 200
    assert "sso_error" in callback(t["client"], pending).headers["location"]
    assert t["client"].post("/auth/oidc/exchange", json={"code": ready}).status_code == 400
    assert t["client"].get("/auth/me", headers=headers(active)).status_code == 401
    assert t["client"].get("/auth/me", headers=headers(t["token1"])).status_code == 200
    assert t["client"].post("/auth/oidc/start", json={"login_id": provider["login_id"]}).status_code == 404
    # Restoring the same provider cannot revive old attempts or sessions.
    assert action(t, "test").status_code == 200
    assert action(t, "enable").status_code == 200
    assert t["client"].post("/auth/oidc/exchange", json={"code": ready}).status_code == 400
    assert t["client"].get("/auth/me", headers=headers(active)).status_code == 401


def test_client_switch_does_not_reuse_old_identity_and_rotating_secret_invalidates_test(tenants):
    t = tenants; provider = enable(t); link(t, provider)
    assert save(t, client_secret="").status_code == 200
    assert action(t, "enable").status_code == 409
    # A new customer client cannot reuse an old client's subject binding.
    assert save(t, provider=t["second"]).status_code == 200
    assert action(t, "test").status_code == 200
    updated = action(t, "enable").json()["provider"]
    assert updated["login_id"] == provider["login_id"]
    assert "workspace_link_required" in callback(t["client"], begin(t, updated)).headers["location"]


def test_customer_login_preserves_native_mfa_and_disable_invalidates_pending_mfa(tenants):
    t = tenants; provider = enable(t); link(t, provider)
    secret = "JBSWY3DPEHPK3PXP"
    with Session(t["engine"]) as session:
        state = session.get(AccountSecurity, t["owner1"])
        state.totp_secret = seal_private(secret, "totp", t["owner1"])
        session.add(state); session.commit()
    challenge = finish(t["client"], begin(t, provider))
    assert challenge["mfa_required"] and challenge["workspace_id"] == "tenant-one"
    code = totp_at(secret, int(datetime.now(timezone.utc).timestamp() // 30))
    verified = t["client"].post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": code})
    assert verified.status_code == 200, verified.text
    assert verified.json()["workspace_id"] == "tenant-one"
    assert decode_token_claims(verified.json()["access_token"])["amr"] == ["sso", "otp"]
    assert t["client"].post("/auth/mfa/verify", json={"challenge_token": challenge["challenge_token"], "code": code}).status_code == 401
    another = finish(t["client"], begin(t, provider))
    assert action(t, "disable", code=code).status_code == 200
    assert t["client"].post("/auth/mfa/verify", json={"challenge_token": another["challenge_token"], "code": code}).status_code == 401
    assert t["client"].get("/auth/me", headers=headers(verified.json()["access_token"])).status_code == 401


def test_current_membership_required_at_link_callback_exchange_and_mfa(tenants):
    t = tenants; provider = enable(t)
    assert t["client"].post("/auth/oidc/link/start", headers=headers(t["token2"]),
                            json={"workspace_id": "tenant-one", "password": PASSWORD}).status_code == 403
    pending = begin(t, provider, token=t["member_token"])
    with Session(t["engine"]) as session:
        session.delete(session.get(WorkspaceMember, ("tenant-one", t["member"]))); session.commit()
    assert "sso_error" in callback(t["client"], pending).headers["location"]
    link(t, provider)
    ready = callback(t["client"], begin(t, provider)).headers["location"].split("#sso=", 1)[1]
    with Session(t["engine"]) as session:
        session.delete(session.get(WorkspaceMember, ("tenant-one", t["owner1"]))); session.commit()
    assert t["client"].post("/auth/oidc/exchange", json={"code": ready}).status_code == 400


def test_issuer_and_discovered_endpoints_use_customer_public_https_guard(tenants):
    t = tenants
    assert save(t, issuer_url="http://169.254.169.254/metadata").status_code == 422
    assert save(t).status_code == 200
    t["first"].redirect_endpoint = "https://127.0.0.1/private"
    assert action(t, "test").status_code == 422
    assert action(t, "enable").status_code == 409


def test_discovery_test_cannot_stamp_a_config_changed_during_network_wait(tenants, monkeypatch):
    t = tenants
    assert save(t).status_code == 200
    original = oidc.discovery

    def changing(config):
        document = original(config)
        with Session(t["engine"]) as session:
            provider = session.get(WorkspaceOIDCProvider, "tenant-one")
            provider.revision += 1
            provider.client_id = "changed-while-discovering"
            provider.tested_revision = provider.tested_at = None
            session.add(provider); session.commit()
        return document

    monkeypatch.setattr(oidc, "discovery", changing)
    result = action(t, "test")
    assert result.status_code == 409, result.text
    with Session(t["engine"]) as session:
        provider = session.get(WorkspaceOIDCProvider, "tenant-one")
        assert not provider.enabled and provider.tested_revision is None


def test_enable_rechecks_discovery_and_customer_handoff_is_single_use(tenants):
    t = tenants
    assert save(t).status_code == 200
    assert action(t, "test").status_code == 200
    t["first"].discovery_issuer = "https://changed-provider.example"
    assert action(t, "enable").status_code == 422
    t["first"].discovery_issuer = None
    provider = action(t, "enable").json()["provider"]
    link(t, provider)
    authorization = begin(t, provider)
    path = provider_redirect(authorization)
    callback_response = t["client"].get(path, follow_redirects=False)
    handoff = callback_response.headers["location"].split("#sso=", 1)[1]
    assert "sso_error" in t["client"].get(path, follow_redirects=False).headers["location"]
    t["client"].cookies.clear()
    assert t["client"].post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400
    # A fresh browser-bound flow succeeds once and cannot be replayed.
    callback_response = callback(t["client"], begin(t, provider))
    handoff = callback_response.headers["location"].split("#sso=", 1)[1]
    result = t["client"].post("/auth/oidc/exchange", json={"code": handoff})
    assert result.status_code == 200 and result.json()["workspace_id"] == "tenant-one"
    assert t["client"].post("/auth/oidc/exchange", json={"code": handoff}).status_code == 400
