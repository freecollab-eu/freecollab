"""Managed identities cannot inherit personal account or crypto authority."""
from datetime import datetime, timedelta
from unittest.mock import Mock

import jwt
import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.api import account, secure_messaging, workspaces
from app.config import settings
from app.models.account_security import AccountToken
from app.models.channel import ChannelMember
from app.models.secure_channel import MatrixSession, MatrixDeviceRevocation
from app.models.user import User
from app.models.workforce import WorkforceInvitation, WorkforcePrincipal
from app.models.workspace import PersonalWorkspace, WorkspaceInvitation, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCSession
from app.services.account_security import digest, issue_account_token, valid_account_token
from app.services.auth import create_token, token_digest, track_session
from app.services.crypto import encrypt_message
from app.services.workspaces import ensure_legacy_member, ensure_personal_workspace_membership
from tests.test_workspace_sso_crypto import scoped_crypto, headers


@pytest.fixture
def managed(scoped_crypto):
    client, engine, scoped, native = scoped_crypto
    with Session(engine) as session:
        actor = session.get(User, "actor")
        actor.email, actor.role = "workforce-actor@identity.invalid", "user"
        session.add(actor)
        member = session.get(WorkspaceMember, ("a", "actor"))
        member.role = "member"
        session.add(member)
        session.delete(session.get(WorkspaceMember, ("b", "actor")))
        session.delete(session.get(ChannelMember, ("b", "actor")))
        session.add(WorkforcePrincipal(user_id="actor", workspace_id="a", external_id="employee-123",
                                       managed_email="real-person@example.com"))
        session.commit()
    return client, engine, scoped, native


def test_managed_sso_uses_workspace_crypto_without_personal_password(managed):
    client, engine, scoped, native = managed
    me = client.get("/auth/me", headers=headers(scoped))
    assert me.status_code == 200
    assert me.json()["email"] == "real-person@example.com"
    assert me.json()["account_type"] == "managed"
    assert me.json()["session_scope"] == "workspace"
    assert client.get("/auth/me", headers=headers(native)).status_code == 401
    assert client.get("/account/security", headers=headers(scoped)).status_code == 403
    assert client.get("/secure/status", headers=headers(scoped)).json()["requires_encryption_unlock"] is False
    assert client.post("/secure/session", headers=headers(scoped), json={}).json()["access_token"] == "scoped-matrix"
    with Session(engine) as session:
        assert session.get(User, "actor").email == "workforce-actor@identity.invalid"
        for methods, scope in ((None, None), (["pwd"], {"workspace_id":"a", "provider_revision":1}),
                               (["sso"], {"workspace_id":"b", "provider_revision":1})):
            with pytest.raises(HTTPException):
                create_token("actor", session, auth_methods=methods, workspace_scope=scope)


def test_managed_accounts_cannot_gain_native_recovery_or_default_directory(managed, monkeypatch):
    client, engine, _, _ = managed
    queued = Mock()
    monkeypatch.setattr(account, "mail_available", lambda: True)
    monkeypatch.setattr(account, "account_mail_base_url", lambda: "https://app.example")
    monkeypatch.setattr(account, "queue_account_email", queued)
    client.post("/auth/forgot-password", json={"email":"workforce-actor@identity.invalid"})
    queued.assert_not_called()
    with Session(engine) as session:
        actor = session.get(User, "actor")
        with pytest.raises(HTTPException):
            issue_account_token(session, actor, "password-reset", 30)
        session.add(AccountToken(token_hash=digest("old-native-link"), user_id="actor", purpose="password-reset",
                           auth_version=0, target_email=actor.email, expires_at=datetime.utcnow()+timedelta(minutes=5)))
        session.commit()
        with pytest.raises(HTTPException):
            valid_account_token(session, "old-native-link", "password-reset")
        assert ensure_personal_workspace_membership(session, "actor").workspace_id == "a"
        assert session.get(PersonalWorkspace, "actor") is None
        with pytest.raises(HTTPException):
            ensure_legacy_member(session, "actor")


@pytest.mark.parametrize("change", ["inactive","outside_membership","outside_channel","personal_home","wrong_native_identity","owner_role"])
def test_managed_scope_fails_closed_if_isolation_invariant_changes(managed, change):
    client, engine, scoped, _ = managed
    with Session(engine) as session:
        if change == "inactive":
            principal=session.get(WorkforcePrincipal,"actor"); principal.active=False; session.add(principal)
        elif change == "outside_membership": session.add(WorkspaceMember(workspace_id="b",user_id="actor"))
        elif change == "outside_channel": session.add(ChannelMember(channel_id="b",user_id="actor"))
        elif change == "personal_home": session.add(PersonalWorkspace(user_id="actor",workspace_id="a"))
        elif change == "owner_role":
            member=session.get(WorkspaceMember,("a","actor")); member.role="owner"; session.add(member)
        else:
            actor=session.get(User,"actor"); actor.email="personal@example.com"; session.add(actor)
        session.commit()
    assert client.get("/auth/me",headers=headers(scoped)).status_code == 401
    assert client.get("/_matrix/client/v3/sync",headers=headers("scoped-matrix")).status_code == 401


def test_managed_device_removal_is_own_recent_and_remote_first(managed, monkeypatch):
    client, engine, scoped, _ = managed
    with Session(engine) as session:
        other=create_token("actor",session,auth_methods=["sso"],workspace_scope={"workspace_id":"a","provider_revision":1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(other),workspace_id="a",provider_revision=1))
        session.add(MatrixSession(matrix_token_hash=token_digest("lost-matrix"),user_id="actor",
            auth_token_hash=token_digest(other),auth_expires_at=datetime.utcnow()+timedelta(hours=1),device_id="LOST",
            encrypted_access_token=encrypt_message("lost-matrix","matrix-session:"+token_digest("lost-matrix"))))
        session.commit()
    def fail(*args): raise HTTPException(503,"Matrix unavailable")
    monkeypatch.setattr(secure_messaging,"delete_owned_device",fail)
    response=client.post("/secure/devices/LOST/revoke-managed",headers=headers(scoped),json={"confirmed":True})
    assert response.status_code == 503
    assert client.get("/auth/me",headers=headers(other)).status_code == 200
    with Session(engine) as session: assert session.get(MatrixDeviceRevocation,("actor","LOST")) is None
    removed=Mock()
    monkeypatch.setattr(secure_messaging,"delete_owned_device",removed)
    response=client.post("/secure/devices/LOST/revoke-managed",headers=headers(scoped),json={"confirmed":True})
    assert response.json() == {"revoked":True,"current":False}
    removed.assert_called_once()
    assert client.get("/auth/me",headers=headers(other)).status_code == 401
    assert client.get("/auth/me",headers=headers(scoped)).status_code == 200
    assert client.post("/secure/devices/UNKNOWN/revoke-managed",headers=headers(scoped),json={"confirmed":True}).status_code == 404
    assert client.post("/secure/devices/SCOPED/revoke-managed",headers=headers(scoped),json={"confirmed":False}).status_code == 400


def test_managed_device_removal_rejects_old_signin(managed):
    client, engine, scoped, _ = managed
    payload=jwt.decode(scoped,settings.jwt_secret,algorithms=["HS256"])
    payload["iat"]=int((datetime.utcnow()-timedelta(minutes=6)).timestamp())
    old=jwt.encode(payload,settings.jwt_secret,algorithm="HS256")
    with Session(engine) as session:
        track_session(old,session)
        session.add(WorkspaceOIDCSession(token_hash=token_digest(old),workspace_id="a",provider_revision=1)); session.commit()
    response=client.post("/secure/devices/SCOPED/revoke-managed",headers=headers(old),json={"confirmed":True})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "requires_recent_organization_signin"
    assert client.get("/auth/me",headers=headers(old)).status_code == 200


def test_personal_account_cannot_use_managed_device_controls(scoped_crypto):
    client, _, scoped, native = scoped_crypto
    for token in (scoped, native):
        response=client.post("/secure/devices/NATIVE/revoke-managed",headers=headers(token),json={"confirmed":True})
        assert response.status_code == 403


def test_managed_invitation_cannot_be_consumed_through_native_invite_endpoint(scoped_crypto):
    client, engine, _, _ = scoped_crypto
    client.app.include_router(workspaces.router)
    managed_token = "managed-invitation-" + "x" * 32
    normal_token = "normal-invitation-" + "x" * 32
    with Session(engine) as session:
        token = create_token("b-peer", session)
        session.add(WorkspaceInvitation(id="managed-invite", workspace_id="a", token_hash=digest(managed_token),
            role="member", created_by="actor", expires_at=datetime.utcnow()+timedelta(hours=1)))
        session.add(WorkforceInvitation(invitation_id="managed-invite", workspace_id="a"))
        session.add(WorkspaceInvitation(id="normal-invite", workspace_id="a", token_hash=digest(normal_token),
            role="member", created_by="actor", expires_at=datetime.utcnow()+timedelta(hours=1)))
        session.commit()
    response = client.post("/workspaces/invites/accept", headers=headers(token), json={"token":managed_token})
    assert response.status_code == 403
    with Session(engine) as session:
        assert session.get(WorkspaceMember, ("a", "b-peer")) is None
        assert session.get(WorkspaceInvitation, "managed-invite").accepted_at is None
    assert client.post("/workspaces/invites/accept", headers=headers(token), json={"token":normal_token}).status_code == 200
