"""A customer IdP can own its workforce namespace, never personal accounts."""
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.api import workforce as workforce_api
from app.models.account_security import AccountSecurity
from app.models.oidc import OIDCIdentity
from app.models.user import User
from app.models.workforce import WorkforcePrincipal, WorkspaceIdentityPolicy
from app.models.workspace import WorkspaceInvitation, WorkspaceMember
from app.services.auth import decode_token_claims
from app.services.workforce import ensure_principal
from app.services.workspaces import lock_workspace
from tests.test_oidc import PASSWORD, callback, finish, sso
from tests.test_workspace_oidc import tenants, enable, headers, begin, link


@pytest.fixture
def workforce(tenants):
    tenants["client"].app.include_router(workforce_api.router)
    yield tenants


def policy(t, mode="invited", claim="sub", **changes):
    return t["client"].put("/workspaces/tenant-one/identity-policy", headers=headers(t["token1"]),
        json={"mode": mode, "identity_claim": claim, "password": PASSWORD, **changes})


def invite(t, **changes):
    response = t["client"].post("/workspaces/tenant-one/workforce/invites", headers=headers(t["token1"]),
        json={"password": PASSWORD, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def managed_start(t, provider, token=None):
    response = t["client"].post("/auth/oidc/start", json={"login_id": provider["login_id"],
        **({"invitation_token": token} if token else {})})
    assert response.status_code == 200, response.text
    return response.json()["authorization_url"]


def test_default_linked_policy_preserves_no_jit_and_admin_reauthentication(workforce):
    t = workforce; client = t["client"]; provider = enable(t)
    response = client.get("/workspaces/tenant-one/identity-policy", headers=headers(t["token1"]))
    assert response.json()["mode"] == "linked"
    for token in [t["token2"], t["member_token"]]:
        assert client.get("/workspaces/tenant-one/identity-policy", headers=headers(token)).status_code == 403
        assert client.put("/workspaces/tenant-one/identity-policy", headers=headers(token),
            json={"mode": "jit", "password": PASSWORD}).status_code == 403
    assert policy(t, "jit", password="wrong").status_code == 403
    assert "workspace_link_required" in callback(client, managed_start(t, provider)).headers["location"]
    with Session(t["engine"]) as session:
        assert session.exec(select(WorkforcePrincipal)).all() == []


def test_invited_first_signin_needs_no_native_account_and_cannot_claim_personal_email(workforce):
    t = workforce; client = t["client"]; provider = enable(t)
    assert policy(t).status_code == 200
    assert "workforce_access_required" in callback(client, managed_start(t, provider)).headers["location"]
    t["first"].email = "owner-one@example.com"
    t["first"].claim_overrides["email_verified"] = False
    invitation = invite(t, role="guest")
    result = finish(client, managed_start(t, provider, invitation["token"]))
    claims = decode_token_claims(result["access_token"])
    assert claims["sso_workspace_id"] == "tenant-one" and claims["amr"] == ["sso"]
    assert result["user_id"] != t["owner1"]
    with Session(t["engine"]) as session:
        principal = session.get(WorkforcePrincipal, result["user_id"])
        assert principal.workspace_id == "tenant-one" and principal.managed_email == "owner-one@example.com"
        managed = session.get(User, principal.user_id)
        assert managed.email.endswith("@identity.invalid") and managed.role == "user"
        assert session.get(User, t["owner1"]).email == "owner-one@example.com"
        assert session.get(AccountSecurity, principal.user_id).email_verified_at is None
        assert session.get(WorkspaceMember, ("tenant-one", principal.user_id)).role == "guest"
        assert session.get(WorkspaceInvitation, invitation["invitation_id"]).accepted_by == principal.user_id
    # A returning binding needs no invitation and still never becomes a personal identity.
    again = finish(client, managed_start(t, provider))
    assert again["user_id"] == result["user_id"]
    assert client.get("/account/security", headers=headers(result["access_token"])).status_code == 403
    assert client.get("/workspaces/tenant-two", headers=headers(result["access_token"])).status_code == 403


def test_two_tenants_using_same_email_and_subject_receive_distinct_managed_principals(workforce):
    t = workforce; client = t["client"]
    first = enable(t); second = enable(t, "tenant-two", t["token2"], t["second"])
    assert policy(t, "jit").status_code == 200
    response = client.put("/workspaces/tenant-two/identity-policy", headers=headers(t["token2"]),
        json={"mode": "jit", "password": PASSWORD})
    assert response.status_code == 200
    t["first"].email = t["second"].email = "shared-victim@example.com"
    t["first"].subject = t["second"].subject = "same-subject"
    a = finish(client, managed_start(t, first)); b = finish(client, managed_start(t, second))
    assert a["user_id"] != b["user_id"]
    with Session(t["engine"]) as session:
        people = session.exec(select(WorkforcePrincipal)).all()
        assert len(people) == 2 and {row.workspace_id for row in people} == {"tenant-one", "tenant-two"}
        assert session.exec(select(User).where(User.email == "shared-victim@example.com")).first() is None
    assert client.get("/workspaces", headers=headers(a["access_token"])).json()[0]["id"] == "tenant-one"


def test_scim_preprovisioned_identity_maps_only_explicit_tenant_claim_and_never_rebinds_subject(workforce):
    t = workforce; client = t["client"]; provider = enable(t)
    assert policy(t, "provisioned", "oid").status_code == 200
    with Session(t["engine"]) as session:
        lock_workspace(session, "tenant-one")
        principal = ensure_principal(session, "tenant-one", "directory-object", "Managed worker", "person@example.com")
        user_id = principal.user_id; session.commit()
    t["first"].claim_overrides["oid"] = "directory-object"
    result = finish(client, managed_start(t, provider))
    assert result["user_id"] == user_id
    t["first"].subject = "attacker-reassigned-subject"
    assert "workforce_access_required" in callback(client, managed_start(t, provider)).headers["location"]
    with Session(t["engine"]) as session:
        assert len(session.exec(select(WorkforcePrincipal)).all()) == 1
        assert session.get(WorkforcePrincipal, user_id).subject == "external-subject"


@pytest.mark.parametrize("change", ["revoke", "expired", "issuer_removed", "issuer_blocked"])
def test_workforce_invitation_rechecked_after_provider_authentication(workforce, change):
    t = workforce; client = t["client"]; provider = enable(t); assert policy(t).status_code == 200
    invitation = invite(t)
    authorization = managed_start(t, provider, invitation["token"])
    with Session(t["engine"]) as session:
        row = session.get(WorkspaceInvitation, invitation["invitation_id"])
        if change == "revoke": row.revoked_at = datetime.utcnow(); session.add(row)
        elif change == "expired": row.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(row)
        elif change == "issuer_removed": session.delete(session.get(WorkspaceMember, ("tenant-one", t["owner1"])))
        else:
            owner = session.get(User, t["owner1"]); owner.is_blocked = True; session.add(owner)
        session.commit()
    assert "sso_error" in callback(client, authorization).headers["location"]
    with Session(t["engine"]) as session:
        assert session.exec(select(WorkforcePrincipal)).all() == []


def test_policy_revision_invitation_replay_and_wrong_workspace_are_fail_closed(workforce):
    t = workforce; client = t["client"]; first = enable(t); second = enable(t, "tenant-two", t["token2"], t["second"])
    assert policy(t).status_code == 200
    invitation = invite(t)
    wrong = client.post("/auth/oidc/start", json={"login_id": second["login_id"], "invitation_token": invitation["token"]})
    assert wrong.status_code == 403
    authorization = managed_start(t, first, invitation["token"])
    assert policy(t, "jit").status_code == 200
    assert "sso_error" in callback(client, authorization).headers["location"]
    assert policy(t).status_code == 200
    result = finish(client, managed_start(t, first, invitation["token"]))
    assert result["access_token"]
    replay = client.post("/auth/oidc/start", json={"login_id": first["login_id"], "invitation_token": invitation["token"]})
    assert replay.status_code == 403


def test_managed_invite_list_never_reveals_token_and_revocation_needs_native_owner(workforce):
    t = workforce; client = t["client"]; enable(t); assert policy(t).status_code == 200
    invitation = invite(t)
    listed = client.get("/workspaces/tenant-one/workforce/invites", headers=headers(t["token1"]))
    assert listed.status_code == 200 and invitation["token"] not in listed.text
    assert listed.json()["invitations"][0]["id"] == invitation["invitation_id"]
    endpoint = "/workspaces/tenant-one/workforce/invites/" + invitation["invitation_id"] + "/revoke"
    assert client.post(endpoint, headers=headers(t["token2"]), json={"password": PASSWORD}).status_code == 403
    assert client.post(endpoint, headers=headers(t["token1"]), json={"password": "wrong"}).status_code == 403
    assert client.post(endpoint, headers=headers(t["token1"]), json={"password": PASSWORD}).json() == {"revoked": True}


def test_inactive_or_removed_managed_identity_never_rejoins_via_jit(workforce):
    t = workforce; client = t["client"]; provider = enable(t); assert policy(t, "jit").status_code == 200
    result = finish(client, managed_start(t, provider))
    with Session(t["engine"]) as session:
        principal = session.get(WorkforcePrincipal, result["user_id"]); principal.active = False
        session.add(principal); session.delete(session.get(WorkspaceMember, ("tenant-one", result["user_id"]))); session.commit()
    assert "workforce_access_required" in callback(client, managed_start(t, provider)).headers["location"]
    with Session(t["engine"]) as session:
        assert len(session.exec(select(WorkforcePrincipal)).all()) == 1


def test_scim_shadow_cannot_bypass_deactivation_through_old_personal_oidc_link(workforce):
    t = workforce; client = t["client"]; provider = enable(t)
    # Personal linking was explicitly authorized before workforce provisioning.
    link(t, provider)
    assert policy(t, "provisioned", "email").status_code == 200
    with Session(t["engine"]) as session:
        lock_workspace(session, "tenant-one")
        shadow = ensure_principal(session, "tenant-one", t["first"].email,
                                   "Directory worker", t["first"].email, active=False)
        assert shadow.user_id != t["owner1"]
        session.commit()
    response = callback(client, managed_start(t, provider))
    assert "workforce_identity_conflict" in response.headers["location"]
    # The original account is still a personal account, with its normal password.
    native = client.post("/auth/login", json={"email": "owner-one@example.com", "password": PASSWORD})
    assert native.status_code == 200 and native.json()["user_id"] == t["owner1"]
    with Session(t["engine"]) as session:
        assert session.get(WorkforcePrincipal, t["owner1"]) is None
