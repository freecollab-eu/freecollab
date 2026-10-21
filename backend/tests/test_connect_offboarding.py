"""Offboarding permanently removes only the terminating tenant's shared seats."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.models.channel import Channel, ChannelMember
from app.models.connect import ConnectConnection, ConnectGrant
from app.models.enterprise_governance import Organization, OrganizationWorkspace
from app.models.scim import SCIMUser
from app.models.secure_channel import SecureChannel
from app.models.workforce import WorkforcePrincipal
from app.models.workspace import WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCSession
from app.services import matrix_bridge
from app.services.auth import create_token, token_digest
from app.services.connect import shared_channel_allows
from tests.test_connect import shared_api, activate, grant
from tests.test_scim import directory, create, group, mapping, patch, headers


@pytest.mark.parametrize("method", ["remove", "leave"])
def test_recipient_offboarding_requires_fresh_grant_after_real_reinvite(shared_api, monkeypatch, method):
    api = shared_api
    connection = activate(api)
    grant(api, connection, "dual")
    cid = connection["channel_id"]
    observed = []
    def remove(session, channel_id, user_id):
        current = session.get(ConnectGrant, (connection["id"], user_id))
        observed.append((channel_id, current.revoked_at, session.get(ChannelMember, (channel_id, user_id)) is not None))
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", remove)
    if method == "remove":
        response = api.client.delete("/workspaces/b/members/dual", headers=api.headers("b-admin-scoped-b"))
    else:
        response = api.client.post("/workspaces/b/leave", headers=api.headers("dual-scoped-b"))
    assert response.status_code == 200, response.text
    assert observed == [(cid, None, True)]
    with Session(api.engine) as session:
        assert session.get(ConnectGrant, (connection["id"], "dual")).revoked_at is not None
        assert session.get(ChannelMember, (cid, "dual")) is None
        assert session.get(WorkspaceMember, ("b", "dual")) is None
        assert session.get(WorkspaceMember, ("a", "dual")) is not None
        assert session.get(ChannelMember, (cid, "a-admin")).role == "admin"
        assert session.get(ConnectGrant, (connection["id"], "a-admin")).revoked_at is None
    invitation = api.client.post("/workspaces/b/invites", headers=api.headers("b-admin-scoped-b"), json={})
    assert invitation.status_code == 201, invitation.text
    accepted = api.client.post("/workspaces/invites/accept", headers=api.headers("dual"), json={"token": invitation.json()["token"]})
    assert accepted.status_code == 200, accepted.text
    assert api.client.get(f"/channels/{cid}", headers=api.headers("dual-scoped-b")).status_code == 403
    grant(api, connection, "dual")
    assert api.client.get(f"/channels/{cid}", headers=api.headers("dual-scoped-b")).status_code == 200


def test_remote_failure_keeps_workspace_seat_and_grant_atomic(shared_api, monkeypatch):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    def unavailable(*args):
        raise HTTPException(503, "Matrix unavailable")
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", unavailable)
    response = api.client.delete("/workspaces/b/members/b-member", headers=api.headers("b-admin-scoped-b"))
    assert response.status_code == 503, response.text
    with Session(api.engine) as session:
        assert session.get(WorkspaceMember, ("b", "b-member"))
        assert session.get(ChannelMember, (connection["channel_id"], "b-member"))
        assert session.get(ConnectGrant, (connection["id"], "b-member")).revoked_at is None
        assert shared_channel_allows(session, connection["channel_id"], "b-member", "b")


def test_offboarding_kicks_stale_remote_seat_when_native_channel_member_missing(shared_api, monkeypatch):
    api = shared_api
    connection = activate(api)
    grant(api, connection, "dual")
    cid = connection["channel_id"]
    with Session(api.engine) as session:
        session.delete(session.get(ChannelMember, (cid, "dual")))
        session.commit()
    observed = []
    def remove(session, channel_id, user_id):
        observed.append((channel_id, user_id))
        assert session.get(ConnectGrant, (connection["id"], user_id)).revoked_at is None
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", remove)
    response = api.client.delete("/workspaces/b/members/dual", headers=api.headers("b-admin-scoped-b"))
    assert response.status_code == 200, response.text
    assert observed == [(cid, "dual")]
    with Session(api.engine) as session:
        assert session.get(ConnectGrant, (connection["id"], "dual")).revoked_at is not None
        assert session.get(WorkspaceMember, ("b", "dual")) is None


def test_source_admin_offboarding_never_promotes_recipient(shared_api, monkeypatch):
    api = shared_api
    connection = activate(api)
    grant(api, connection)
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", lambda *args: None)
    with Session(api.engine) as session:
        session.get(WorkspaceMember, ("a", "a-member")).role = "owner"
        session.commit()
    response = api.client.post("/workspaces/a/leave", headers=api.headers("a-admin"))
    assert response.status_code == 200, response.text
    assert response.json()["admin_handovers"] == []
    with Session(api.engine) as session:
        assert session.get(ChannelMember, (connection["channel_id"], "b-member")).role == "member"
        assert session.get(ConnectGrant, (connection["id"], "a-admin")).revoked_at is not None
        assert session.get(ConnectGrant, (connection["id"], "b-member")).revoked_at is None


def test_source_offboarding_keeps_dual_accounts_recipient_home_grant(shared_api, monkeypatch):
    api = shared_api
    connection = activate(api)
    grant(api, connection, "dual")
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", lambda *args: pytest.fail("Foreign-home seat must remain"))
    response = api.client.delete("/workspaces/a/members/dual", headers=api.headers("a-admin"))
    assert response.status_code == 200, response.text
    with Session(api.engine) as session:
        assert session.get(WorkspaceMember, ("a", "dual")) is None
        assert session.get(WorkspaceMember, ("b", "dual"))
        assert session.get(ChannelMember, (connection["channel_id"], "dual"))
        assert session.get(ConnectGrant, (connection["id"], "dual")).revoked_at is None
    assert api.client.get(f"/channels/{connection['channel_id']}", headers=api.headers("dual-scoped-b")).status_code == 200


def test_subject_lookup_does_not_bypass_governed_actor_signin(shared_api, monkeypatch):
    api = shared_api
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", lambda *args: None)
    with Session(api.engine) as session:
        session.get(WorkspaceMember, ("a", "a-member")).role = "admin"
        session.add(Organization(id="org-a", name="Governed A", created_by="a-admin", require_sso=True))
        session.flush()
        session.add(OrganizationWorkspace(workspace_id="a", organization_id="org-a", linked_by="a-admin"))
        scoped = create_token("a-member", session, auth_methods=["sso"],
                              workspace_scope={"workspace_id": "a", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="a", provider_revision=1))
        session.commit()
    response = api.client.delete("/workspaces/a/members/dual", headers=api.headers("a-member"))
    assert response.status_code == 403, response.text
    with Session(api.engine) as session:
        assert session.get(WorkspaceMember, ("a", "dual"))
    response = api.client.delete("/workspaces/a/members/dual", headers={"Authorization": "Bearer " + scoped})
    assert response.status_code == 200, response.text


def _scim_shared_recipient(d, *, orphan=False, require_sso=False):
    resource = create(d).json()
    with Session(d["engine"]) as session:
        uid = session.get(SCIMUser, resource["id"]).user_id
        # Directory A is the recipient of a canonical B-owned encrypted room.
        session.add(Channel(id="shared-ba", name="Shared room"))
        session.flush()
        session.add(WorkspaceChannel(channel_id="shared-ba", workspace_id="b"))
        session.add(SecureChannel(channel_id="shared-ba", created_by="owner-b", status="active",
                                  matrix_room_id="!shared:private.invalid"))
        session.add(ConnectConnection(id="ba", channel_id="shared-ba", source_workspace_id="b",
            target_workspace_id="a", created_by="owner-b", accepted_by="owner-a", status="active",
            token_hash="private-invitation-hash", expires_at=datetime.utcnow() + timedelta(days=1)))
        session.flush()
        for user_id, home in (("owner-b", "b"), (uid, "a")):
            session.add(ConnectGrant(connection_id="ba", user_id=user_id, workspace_id=home, created_by="owner-" + home))
            session.add(ChannelMember(channel_id="shared-ba", user_id=user_id, role="admin" if home == "b" else "member"))
        if orphan:
            session.delete(session.get(WorkspaceMember, ("a", uid)))
        if require_sso:
            session.add(Organization(id="org-a", name="SSO organization", created_by="owner-a", require_sso=True))
            session.flush()
            session.add(OrganizationWorkspace(workspace_id="a", organization_id="org-a", linked_by="owner-a"))
        session.commit()
    return resource, uid


@pytest.mark.parametrize("operation", ["deactivate", "delete", "orphan", "required_sso"])
def test_scim_offboarding_revokes_shared_grant_even_after_reprovision(directory, monkeypatch, operation):
    d = directory
    resource, uid = _scim_shared_recipient(d, orphan=operation == "orphan", require_sso=operation == "required_sso")
    observed = []
    def remove(session, channel_id, user_id):
        observed.append(channel_id)
        assert session.get(ConnectGrant, ("ba", uid)).revoked_at is None
        assert session.get(ChannelMember, (channel_id, uid)) is not None
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", remove)
    if operation == "delete":
        response = d["client"].delete(d["base"] + "/Users/" + resource["id"], headers=headers(d["token"]))
        assert response.status_code == 204, response.text
    else:
        response = patch(d, "/Users/" + resource["id"], [{"op": "replace", "path": "active", "value": False}])
        assert response.status_code == 200, response.text
    assert observed == ["shared-ba"]
    with Session(d["engine"]) as session:
        assert session.get(ConnectGrant, ("ba", uid)).revoked_at is not None
        assert session.get(ChannelMember, ("shared-ba", uid)) is None
        assert session.get(ConnectGrant, ("ba", "owner-b")).revoked_at is None
        assert session.get(WorkspaceMember, ("a", uid)) is None
        assert not session.get(WorkforcePrincipal, uid).active
    if operation == "delete":
        restored = create(d)
        assert restored.status_code == 201, restored.text
    else:
        restored = patch(d, "/Users/" + resource["id"], [{"op": "replace", "path": "active", "value": True}])
        assert restored.status_code == 200, restored.text
    with Session(d["engine"]) as session:
        assert session.get(WorkspaceMember, ("a", uid))
        assert session.get(ConnectGrant, ("ba", uid)).revoked_at is not None
        assert session.get(ChannelMember, ("shared-ba", uid)) is None
        assert not shared_channel_allows(session, "shared-ba", uid, "a")


def test_ordinary_scim_group_unlink_preserves_independent_shared_grant(directory):
    d = directory
    resource, uid = _scim_shared_recipient(d)
    g = group(d, [resource["id"]]).json()
    assert mapping(d, g["id"]).status_code == 200
    response = patch(d, "/Groups/" + g["id"], [{"op": "remove", "path": "members"}])
    assert response.status_code == 200, response.text
    with Session(d["engine"]) as session:
        assert session.get(ChannelMember, ("a-public", uid)) is None
        assert session.get(ChannelMember, ("shared-ba", uid))
        assert session.get(ConnectGrant, ("ba", uid)).revoked_at is None
        assert shared_channel_allows(session, "shared-ba", uid, "a")


@pytest.mark.parametrize("orphan", [False, True])
def test_scim_shared_remote_failure_rolls_back_disable_and_grants(directory, monkeypatch, orphan):
    d = directory
    resource, uid = _scim_shared_recipient(d, orphan=orphan)
    def unavailable(*args):
        raise HTTPException(503, "Matrix unavailable")
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", unavailable)
    response = patch(d, "/Users/" + resource["id"], [{"op": "replace", "path": "active", "value": False}])
    assert response.status_code == 503, response.text
    with Session(d["engine"]) as session:
        assert session.get(SCIMUser, resource["id"]).active
        assert session.get(WorkforcePrincipal, uid).active
        assert session.get(ConnectGrant, ("ba", uid)).revoked_at is None
        assert session.get(ChannelMember, ("shared-ba", uid))
        assert bool(session.get(WorkspaceMember, ("a", uid))) is not orphan


def test_scim_group_admin_handover_never_promotes_other_tenant(directory):
    d = directory
    resource, uid = _scim_shared_recipient(d)
    with Session(d["engine"]) as session:
        connection = session.get(ConnectConnection, "ba")
        connection.source_workspace_id, connection.target_workspace_id = "a", "b"
        session.get(WorkspaceChannel, "shared-ba").workspace_id = "a"
        session.get(ChannelMember, ("shared-ba", "owner-b")).role = "member"
        session.delete(session.get(ChannelMember, ("shared-ba", uid)))
        session.add(ConnectGrant(connection_id="ba", user_id="owner-a", workspace_id="a", created_by="owner-a"))
        session.add(ChannelMember(channel_id="shared-ba", user_id="owner-a", role="admin"))
        session.commit()
    g = group(d, [resource["id"]]).json()
    response = mapping(d, g["id"], channels_list=[{"channel_id": "shared-ba", "role": "admin"}])
    assert response.status_code == 200, response.text
    with Session(d["engine"]) as session:
        assert session.get(ChannelMember, ("shared-ba", uid)).role == "admin"
        session.delete(session.get(ChannelMember, ("shared-ba", "owner-a")))
        session.commit()
    response = patch(d, "/Groups/" + g["id"], [{"op": "remove", "path": "members"}])
    assert response.status_code == 200, response.text
    with Session(d["engine"]) as session:
        assert session.get(ChannelMember, ("shared-ba", uid)) is None
        assert session.get(ChannelMember, ("shared-ba", "owner-b")).role == "member"
        assert session.get(ConnectGrant, ("ba", uid)).revoked_at is None


@pytest.mark.parametrize("require_sso", [False, True])
def test_directory_offboarding_promotes_active_ordinary_channel_successor(directory, require_sso):
    d = directory
    removed = create(d).json()
    successor = create(d, name="successor@example.com", external_id="successor-subject").json()
    with Session(d["engine"]) as session:
        removed_uid = session.get(SCIMUser, removed["id"]).user_id
        successor_uid = session.get(SCIMUser, successor["id"]).user_id
        session.delete(session.get(ChannelMember, ("a-public", "owner-a")))
        session.add(ChannelMember(channel_id="a-public", user_id=removed_uid, role="admin"))
        session.add(ChannelMember(channel_id="a-public", user_id=successor_uid, role="member"))
        if require_sso:
            session.add(Organization(id="org-a", name="Governed A", created_by="owner-a", require_sso=True))
            session.flush()
            session.add(OrganizationWorkspace(workspace_id="a", organization_id="org-a", linked_by="owner-a"))
        session.commit()
    response = patch(d, "/Users/" + removed["id"], [{"op": "replace", "path": "active", "value": False}])
    assert response.status_code == 200, response.text
    with Session(d["engine"]) as session:
        assert session.get(ChannelMember, ("a-public", removed_uid)) is None
        assert session.get(WorkspaceMember, ("a", successor_uid)).role == "member"
        assert session.get(WorkforcePrincipal, successor_uid).active
        assert session.get(ChannelMember, ("a-public", successor_uid)).role == "admin"
