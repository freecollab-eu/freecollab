"""Bilateral consent and current tenant grants, without real external services."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.api import channels, connect, messages, secure_messaging, workspaces
from app.database import get_session
from app.models.channel import ChannelMember
from app.models.connect import ConnectConnection, ConnectGrant
from app.models.enterprise_governance import Organization, OrganizationWorkspace
from app.models.secure_channel import SecureChannel
from app.models.user import User
from app.models.workforce import WorkforcePrincipal
from app.models.workspace import Workspace, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services.auth import create_token, token_digest, active_user_for_token
from app.services.connect import shared_channel_allows


@pytest.fixture
def shared_api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    tokens = {}
    with Session(engine) as session:
        for name in ("a-admin", "a-member", "b-admin", "b-member", "c-admin", "dual"):
            managed = name in {"b-admin", "b-member"}
            session.add(User(id=name, name=name, password_hash="unused",
                email=f"workforce-{name}@identity.invalid" if managed else name + "@example.com"))
        for workspace in "abc":
            session.add(Workspace(id=workspace, name="Organization " + workspace.upper()))
        session.flush()
        for workspace in "abc":
            session.add(WorkspaceMember(workspace_id=workspace, user_id=workspace + "-admin",
                role="admin" if workspace == "b" else "owner"))
        for workspace in "ab":
            session.add(WorkspaceMember(workspace_id=workspace, user_id=workspace + "-member", role="member"))
            session.add(WorkspaceMember(workspace_id=workspace, user_id="dual", role="member"))
            session.add(WorkspaceOIDCProvider(workspace_id=workspace, name=workspace,
                issuer_url="https://" + workspace + ".example.com", client_id=workspace,
                created_by=workspace + "-admin", enabled=True, revision=1))
        for name in ("b-admin", "b-member"):
            session.add(WorkforcePrincipal(user_id=name, workspace_id="b", external_id=name, active=True))
        session.commit()
        for name in ("a-admin", "a-member", "c-admin", "dual"):
            tokens[name] = create_token(name, session)
        for name, workspace in (("b-admin", "b"), ("b-member", "b"), ("a-admin", "a"), ("dual", "a"), ("dual", "b")):
            token = create_token(name, session, auth_methods=["sso"],
                workspace_scope={"workspace_id": workspace, "provider_revision": 1})
            session.add(WorkspaceOIDCSession(token_hash=token_digest(token), workspace_id=workspace, provider_revision=1))
            tokens[name + "-scoped-" + workspace] = token
        session.commit()
    def provision(session, binding):
        binding.matrix_room_id = "!test-" + binding.channel_id + ":private.invalid"
        binding.status = "active"
        session.add(binding)
    monkeypatch.setattr(connect, "require_matrix", lambda: None)
    monkeypatch.setattr(connect, "create_secure_room", provision)
    monkeypatch.setattr(connect, "sync_secure_membership", lambda *args: None)
    monkeypatch.setattr(connect, "remove_secure_member", lambda *args: None)
    monkeypatch.setattr(connect.settings, "matrix_enabled", True)
    from app.realtime import socket
    monkeypatch.setattr(socket, "engine", engine)
    api = FastAPI()
    for module in (connect, channels, messages, secure_messaging, workspaces):
        api.include_router(module.router)
    def sessions():
        with Session(engine) as session:
            yield session
    api.dependency_overrides[get_session] = sessions
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, engine=engine, tokens=tokens,
            headers=lambda name: {"Authorization": "Bearer " + tokens[name]})
    engine.dispose()


def create(api):
    response = api.client.post("/connect/workspaces/a/connections", headers=api.headers("a-admin"),
        json={"name": "Partner project", "target_workspace_id": "b"})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def activate(api):
    connection = create(api)
    response = api.client.post("/connect/workspaces/b/accept", headers=api.headers("b-admin-scoped-b"),
        json={"token": connection["invitation_token"]})
    assert response.status_code == 200, response.text
    return connection


def grant(api, connection, name="b-member", actor="b-admin-scoped-b", workspace="b"):
    response = api.client.post(f"/connect/workspaces/{workspace}/connections/{connection['id']}/members",
        headers=api.headers(actor), json={"user_id": name})
    assert response.status_code == 201, response.text


def test_bilateral_acceptance_never_auto_enrolls_recipient_admin(shared_api):
    api = shared_api
    connection = activate(api)
    channel_id = connection["channel_id"]
    assert api.client.get(f"/channels/{channel_id}", headers=api.headers("b-admin-scoped-b")).status_code == 403
    with Session(api.engine) as session:
        assert session.get(ChannelMember, (channel_id, "b-admin")) is None
        assert session.get(WorkspaceMember, ("a", "b-admin")) is None
        assert session.get(ConnectConnection, connection["id"]).status == "active"
        assert session.get(SecureChannel, channel_id).status == "active"


def test_pending_connection_and_member_roles_require_both_orgs(shared_api):
    api = shared_api
    connection = create(api)
    response = api.client.post(f"/connect/workspaces/b/connections/{connection['id']}/members",
        headers=api.headers("b-admin-scoped-b"), json={"user_id": "b-member"})
    assert response.status_code == 409
    assert api.client.get(f"/channels/{connection['channel_id']}", headers=api.headers("b-member-scoped-b")).status_code == 403
    assert api.client.post("/connect/workspaces/a/connections", headers=api.headers("a-member"),
        json={"name": "No authority", "target_workspace_id": "b"}).status_code == 403


def test_invitation_is_targeted_single_use_and_hash_only(shared_api):
    api = shared_api
    connection = create(api)
    token = connection["invitation_token"]
    assert api.client.post("/connect/workspaces/c/accept", headers=api.headers("c-admin"), json={"token": token}).status_code == 404
    for expected in (200, 404):
        assert api.client.post("/connect/workspaces/b/accept", headers=api.headers("b-admin-scoped-b"),
            json={"token": token}).status_code == expected
    with Session(api.engine) as session:
        row = session.get(ConnectConnection, connection["id"])
        assert row.token_hash != token and len(row.token_hash) == 64
    records = api.client.get("/connect/workspaces/a/connections", headers=api.headers("a-admin")).json()
    assert "invitation_token" not in records[0] and token not in str(records)


@pytest.mark.parametrize("change", ["expired", "issuer_demoted", "issuer_blocked"])
def test_invite_rechecks_current_source_authority(shared_api, change):
    api = shared_api
    connection = create(api)
    with Session(api.engine) as session:
        if change == "expired":
            row = session.get(ConnectConnection, connection["id"])
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        elif change == "issuer_demoted":
            row = session.get(WorkspaceMember, ("a", "a-admin")); row.role = "member"
        else:
            row = session.get(User, "a-admin"); row.is_blocked = True
        session.add(row); session.commit()
    assert api.client.post("/connect/workspaces/b/accept", headers=api.headers("b-admin-scoped-b"),
        json={"token": connection["invitation_token"]}).status_code == 404


def test_managed_recipient_sees_only_explicit_channel_in_own_scope(shared_api):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    channel_id = connection["channel_id"]
    response = api.client.get("/channels?workspace_id=b", headers=api.headers("b-member-scoped-b"))
    assert response.status_code == 200, response.text
    room = response.json()[0]
    assert room["id"] == channel_id and room["workspace_id"] == "b"
    assert room["owner_workspace_id"] == "a" and room["shared"] and room["secure"]
    assert api.client.get(f"/channels/{channel_id}", headers=api.headers("b-member-scoped-b")).status_code == 200
    assert api.client.get("/workspaces/a", headers=api.headers("b-member-scoped-b")).status_code == 403
    assert api.client.get("/workspaces/a/members", headers=api.headers("b-member-scoped-b")).status_code == 403
    assert api.client.post(f"/messages/channels/{channel_id}", headers=api.headers("b-member-scoped-b"),
        json={"content": "No plaintext fallback"}).status_code == 409
    peers = api.client.get(f"/channels/{channel_id}/members", headers=api.headers("b-member-scoped-b")).json()
    assert {peer["user_id"] for peer in peers} == {"a-admin", "b-member"}
    with Session(api.engine) as session:
        assert active_user_for_token(api.tokens["b-member-scoped-b"], session) is not None
        assert session.get(WorkspaceMember, ("a", "b-member")) is None


def test_each_org_can_grant_and_revoke_only_its_own_users(shared_api):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    path = f"/connect/workspaces/a/connections/{connection['id']}/members"
    assert api.client.post(path, headers=api.headers("a-admin"), json={"user_id": "b-member"}).status_code == 403
    assert api.client.delete(path + "/b-member", headers=api.headers("a-admin")).status_code == 404
    assert api.client.post(f"/connect/workspaces/b/connections/{connection['id']}/members",
        headers=api.headers("b-admin-scoped-b"), json={"user_id": "a-member"}).status_code == 404
    assert api.client.get(f"/connect/workspaces/c/connections/{connection['id']}/members",
        headers=api.headers("c-admin")).status_code == 404


def test_unrelated_scope_cannot_reuse_an_account_grant(shared_api):
    api = shared_api
    connection = activate(api)
    grant(api, connection, "dual")
    path = f"/channels/{connection['channel_id']}"
    assert api.client.get(path, headers=api.headers("dual-scoped-b")).status_code == 200
    assert api.client.get(path, headers=api.headers("dual-scoped-a")).status_code == 403


def test_generic_invites_and_roles_cannot_bypass_org_control(shared_api):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    path = f"/channels/{connection['channel_id']}"
    assert api.client.post(path + "/invites", headers=api.headers("a-admin"), json={}).status_code == 409
    assert api.client.patch(path + "/members/b-member", headers=api.headers("a-admin"), json={"role": "admin"}).status_code == 409
    assert api.client.delete(path + "/members/b-member", headers=api.headers("a-admin")).status_code == 409
    assert api.client.post(path + "/leave", headers=api.headers("b-member-scoped-b")).status_code == 200
    assert api.client.get(path, headers=api.headers("b-member-scoped-b")).status_code == 403


@pytest.mark.parametrize("workspace,actor", [("a", "a-admin"), ("b", "b-admin-scoped-b")])
def test_either_org_revokes_rest_socket_matrix_and_long_lived_grants(shared_api, workspace, actor):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    channel_id = connection["channel_id"]
    from app.realtime import socket
    from app.services.matrix_bridge import membership_epoch
    with Session(api.engine) as retained:
        assert shared_channel_allows(retained, channel_id, "b-member", "b")
        before = membership_epoch(retained, channel_id)
        assert socket._is_member("b-member", channel_id, api.tokens["b-member-scoped-b"])
        response = api.client.delete(f"/connect/workspaces/{workspace}/connections/{connection['id']}", headers=api.headers(actor))
        assert response.status_code == 200, response.text
        assert not shared_channel_allows(retained, channel_id, "b-member", "b")
        assert membership_epoch(retained, channel_id) != before
    assert not socket._is_member("b-member", channel_id, api.tokens["b-member-scoped-b"])
    assert api.client.get(f"/channels/{channel_id}", headers=api.headers("b-member-scoped-b")).status_code == 403
    assert api.client.get("/secure/channels", headers=api.headers("b-member-scoped-b")).json() == []
    assert api.client.get(f"/channels/{channel_id}", headers=api.headers("a-admin")).status_code == 403


def test_remote_cleanup_outage_does_not_restore_access_and_can_be_retried(shared_api, monkeypatch):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    def unavailable(*args):
        raise HTTPException(503, "Matrix unavailable")
    monkeypatch.setattr(connect, "remove_secure_member", unavailable)
    base = f"/connect/workspaces/b/connections/{connection['id']}"
    response = api.client.delete(base, headers=api.headers("b-admin-scoped-b"))
    assert response.status_code == 200 and response.json()["secure_sync_pending"] is True
    assert api.client.get(f"/channels/{connection['channel_id']}", headers=api.headers("b-member-scoped-b")).status_code == 403
    assert api.client.post(base + "/retry-sync", headers=api.headers("b-admin-scoped-b")).status_code == 503
    monkeypatch.setattr(connect, "remove_secure_member", lambda *args: None)
    assert api.client.post(base + "/retry-sync", headers=api.headers("b-admin-scoped-b")).json()["secure_sync_pending"] is False


def test_operator_matrix_unavailable_never_creates_plaintext_channel(shared_api, monkeypatch):
    api = shared_api
    def disabled():
        raise HTTPException(503, "Encrypted messaging is not configured")
    monkeypatch.setattr(connect, "require_matrix", disabled)
    response = api.client.post("/connect/workspaces/a/connections", headers=api.headers("a-admin"),
        json={"name": "Private", "target_workspace_id": "b"})
    assert response.status_code == 503
    with Session(api.engine) as session:
        assert session.exec(select(ConnectConnection)).all() == []


def test_governed_recipient_keeps_its_own_sso_and_peers_remain_visible(shared_api):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    with Session(api.engine) as session:
        org = Organization(id="org-b", name="B", require_sso=True, created_by="a-admin")
        session.add(org); session.flush()
        session.add(OrganizationWorkspace(workspace_id="b", organization_id="org-b", linked_by="a-admin"))
        session.commit()
    response = api.client.get(f"/channels/{connection['channel_id']}/members", headers=api.headers("a-admin"))
    assert response.status_code == 200 and {peer["user_id"] for peer in response.json()} == {"a-admin", "b-member"}
    assert api.client.get(f"/channels/{connection['channel_id']}", headers=api.headers("b-member-scoped-b")).status_code == 200


def test_recipient_sso_can_synchronize_matrix_without_owner_workspace_authority(shared_api, monkeypatch):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    from app.models.secure_channel import MatrixAccount
    from app.services import matrix_bridge
    from app.services.sso_scope import current_workspace_scope
    calls = []
    def ensure_account(session, user_id):
        row = session.get(MatrixAccount, user_id)
        if not row:
            row = MatrixAccount(user_id=user_id, matrix_user_id="@" + user_id + ":private.invalid",
                encrypted_password="opaque-test-password", provisioned=True)
            session.add(row); session.flush()
        return row
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"chunk": []}
    monkeypatch.setattr(matrix_bridge, "ensure_account", ensure_account)
    monkeypatch.setattr(matrix_bridge, "service_token", lambda *args, **kwargs: ("@operator:private.invalid", "opaque-test-token"))
    monkeypatch.setattr(matrix_bridge, "matrix_request", request)
    with Session(api.engine) as session:
        assert active_user_for_token(api.tokens["b-member-scoped-b"], session) is not None
        matrix_bridge.sync_secure_membership(session, connection["channel_id"])
        assert current_workspace_scope(session) == "b"
        assert session.get(WorkspaceMember, ("a", "b-member")) is None
        invited = {item[2]["data"]["user_id"] for item in calls if item[1].endswith("/invite")}
        assert invited == {"@a-admin:private.invalid", "@b-member:private.invalid"}


def test_revoked_recipient_gets_own_workspace_removal_event_without_content_grant(shared_api, monkeypatch):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    events = []
    async def receive(user_id, event, data):
        events.append((user_id, event, data))
    monkeypatch.setattr(connect, "send_to_user", receive)
    response = api.client.delete(f"/connect/workspaces/b/connections/{connection['id']}/members/b-member",
        headers=api.headers("b-admin-scoped-b"))
    assert response.status_code == 200
    from app.realtime.socket import _scoped_delivery_allowed
    with Session(api.engine) as session:
        assert active_user_for_token(api.tokens["b-member-scoped-b"], session) is not None
        payload = next(data for user_id, event, data in events if user_id == "b-member" and event == "channel_removed")
        assert payload["workspace_id"] == "b"
        assert _scoped_delivery_allowed(session, "b-member", "channel_removed", payload)
        assert not shared_channel_allows(session, connection["channel_id"], "b-member", "b")
