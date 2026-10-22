"""SCIM2 tenant authority, lifecycle, groups, concurrency versions and credential isolation."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import account, auth, channels, scim, workspaces
from app.config import settings
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.scim import SCIMAudit, SCIMChannelGrant, SCIMDirectory, SCIMUser
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workforce import WorkforcePrincipal, WorkspaceIdentityPolicy
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import account_security, matrix_bridge
from app.services.auth import create_token, hash_password, token_digest
from app.services.scim import USER_SCHEMA, GROUP_SCHEMA, PATCH_SCHEMA, ERROR_SCHEMA
from tests.test_oidc import sso, callback, finish, PASSWORD as OIDC_PASSWORD
from tests.test_workspace_oidc import tenants, enable, begin

PASSWORD = "native-customer-owner-password"


@pytest.fixture
def directory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'scim.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "public_app_url", "https://collab.example.test")
    monkeypatch.setattr(settings, "account_security_key", base64.b64encode(bytes(range(32))).decode())
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", lambda *args: None)
    monkeypatch.setattr(matrix_bridge, "sync_secure_membership", lambda *args: None)
    monkeypatch.setattr(workspaces, "broadcast_to_channel", _nothing)
    monkeypatch.setattr(workspaces, "send_to_user", _nothing)
    with Session(engine) as session:
        session.add_all([User(id="owner-a", name="Owner A", email="owner-a@example.com", password_hash=hash_password(PASSWORD)),
                         User(id="owner-b", name="Owner B", email="owner-b@example.com", password_hash=hash_password(PASSWORD)),
                         User(id="admin-a", name="Admin A", email="admin-a@example.com", password_hash=hash_password(PASSWORD)),
                         User(id="personal", name="Personal", email="employee@example.com", password_hash=hash_password(PASSWORD))])
        for wid, owner in (("a", "owner-a"), ("b", "owner-b")):
            session.add(Workspace(id=wid, name="Customer " + wid))
            session.flush()
            session.add(WorkspaceMember(workspace_id=wid, user_id=owner, role="owner"))
            session.add(WorkspaceMember(workspace_id=wid, user_id="personal", role="member"))
            session.add(WorkspaceOIDCProvider(workspace_id=wid, name="Customer IdP", issuer_url="https://idp.example.test",
                client_id="client-" + wid, created_by=owner, enabled=True))
            session.add(WorkspaceIdentityPolicy(workspace_id=wid, mode="provisioned", identity_claim="sub"))
        session.add(WorkspaceMember(workspace_id="a", user_id="admin-a", role="admin"))
        for cid, wid, owner in (("a-public", "a", "owner-a"), ("a-private", "a", "personal"), ("b-public", "b", "owner-b")):
            session.add(Channel(id=cid, name=cid))
            session.flush()
            session.add(WorkspaceChannel(channel_id=cid, workspace_id=wid))
            session.add(ChannelMember(channel_id=cid, user_id=owner, role="admin"))
        session.commit()
        native = {name: create_token(name, session) for name in ("owner-a", "owner-b", "admin-a", "personal")}
    app = FastAPI()
    for api in (auth, account, workspaces, channels): app.include_router(api.router)
    app.include_router(scim.admin_router)
    app.include_router(scim.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        response = client.post("/workspaces/a/scim/token", headers=headers(native["owner-a"]), json={"password": PASSWORD})
        assert response.status_code == 200
        first = response.json()
        second = client.post("/workspaces/b/scim/token", headers=headers(native["owner-b"]), json={"password": PASSWORD}).json()
        yield {"client": client, "engine": engine, "native": native, "token": first["token"],
               "base": "/scim/v2/" + first["directory_id"], "status": first,
               "other_token": second["token"], "other_base": "/scim/v2/" + second["directory_id"]}
    engine.dispose()


async def _nothing(*args, **kwargs): pass


def headers(token):
    return {"Authorization": "Bearer " + token}


def create(d, name="employee@example.com", external_id="employee-subject", **data):
    return d["client"].post(d["base"] + "/Users", headers=headers(d["token"]), json={
        "schemas": [USER_SCHEMA], "userName": name, "externalId": external_id,
        "displayName": "Managed employee", "emails": [{"value": name, "primary": True, "type": "work"}], **data})


def group(d, members, name="Engineering"):
    return d["client"].post(d["base"] + "/Groups", headers=headers(d["token"]), json={
        "schemas": [GROUP_SCHEMA], "displayName": name, "externalId": "group-" + name,
        "members": [{"value": member} for member in members]})


def patch(d, resource, operations, **extra):
    return d["client"].patch(d["base"] + resource, headers={**headers(d["token"]), **extra},
        json={"schemas": [PATCH_SCHEMA], "Operations": operations})


def mapping(d, group_id, *, role="member", channel_role="member", channels_list=None, owner="owner-a"):
    return d["client"].put("/workspaces/a/scim/mappings", headers=headers(d["native"][owner]), json={
        "password": PASSWORD, "mappings": [{"group_id": group_id, "workspace_role": role,
        "channels": channels_list if channels_list is not None else [{"channel_id": "a-public", "role": channel_role}]}]})


def test_hash_only_credentials_rotate_revoke_and_cannot_authenticate_native_api(directory):
    d = directory; client = d["client"]
    assert d["status"]["enabled"] and d["status"]["configured"]
    assert d["status"]["endpoint_url"].startswith("https://collab.example.test/api/scim/v2/")
    assert client.get("/auth/me", headers=headers(d["token"])).status_code in {401, 403}
    assert client.get(d["other_base"] + "/Users", headers=headers(d["token"])).status_code == 401
    assert client.get(d["base"] + "/Users", headers=headers(d["native"]["owner-a"])).status_code == 401
    with Session(d["engine"]) as session:
        saved = session.get(SCIMDirectory, "a")
        assert saved.token_hash == token_digest(d["token"])
        assert d["token"] not in repr(saved)
    status = client.get("/workspaces/a/scim", headers=headers(d["native"]["owner-a"]))
    assert d["token"] not in status.text and "token_hash" not in status.text
    old = d["token"]
    rotated = client.post("/workspaces/a/scim/token", headers=headers(d["native"]["owner-a"]), json={"password": PASSWORD})
    d["token"] = rotated.json()["token"]
    assert d["token"] != old
    assert client.get(d["base"] + "/Users", headers=headers(old)).status_code == 401
    assert client.get(d["base"] + "/Users", headers=headers(d["token"])).status_code == 200
    assert client.post("/workspaces/a/scim/disable", headers=headers(d["native"]["owner-a"]), json={"password": PASSWORD}).status_code == 200
    assert client.get(d["base"] + "/Users", headers=headers(d["token"])).status_code == 401


def test_simultaneous_first_status_and_mappings_share_one_inactive_directory(directory, monkeypatch):
    d = directory
    workspace_id = "first-scim-load"
    status_path = f"/workspaces/{workspace_id}/scim"
    mappings_path = status_path + "/mappings"
    with Session(d["engine"]) as session:
        session.add(Workspace(id=workspace_id, name="Unconfigured directory"))
        session.flush()
        session.add(WorkspaceMember(workspace_id=workspace_id, user_id="owner-a", role="owner"))
        session.commit()
        previous = session.get(SCIMDirectory, "a")
        previous_credential = previous.token_hash, previous.token_version, previous.enabled

    status_missing, mappings_entered, status_finished = Event(), Event(), Event()
    session_ids, observed_directories = {}, {}
    real_get, real_lock = Session.get, scim.lock_workspace

    def sessions(request: Request):
        with Session(d["engine"]) as session:
            session.info["first_scim_route"] = request.url.path
            session_ids[request.url.path] = id(session)
            yield session

    def gated_lock(session, target):
        if target == workspace_id and session.info.get("first_scim_route") == mappings_path:
            # The fixed request reaches its real lock before reading the row.
            mappings_entered.set()
        return real_lock(session, target)

    def gated_get(session, model, key, *args, **kwargs):
        row = real_get(session, model, key, *args, **kwargs)
        if model is SCIMDirectory and key == workspace_id:
            path = session.info.get("first_scim_route")
            observed_directories[path] = row.directory_id if row else None
            if path == status_path and row is None:
                # Status holds its real write lock and has really read no row.
                status_missing.set()
                assert mappings_entered.wait(10), "Mappings did not enter initialization"
            elif path == mappings_path and row is None:
                # The old route reads before locking. Preserve that real None
                # until status commits, reproducing its duplicate INSERT.
                mappings_entered.set()
                assert status_finished.wait(10), "Status did not finish initialization"
        return row

    monkeypatch.setitem(d["client"].app.dependency_overrides, get_session, sessions)
    monkeypatch.setattr(scim, "lock_workspace", gated_lock)
    monkeypatch.setattr(Session, "get", gated_get)

    def read_status():
        try:
            with TestClient(d["client"].app) as client:
                return client.get(status_path, headers=headers(d["native"]["owner-a"]))
        finally:
            status_finished.set()

    def read_mappings():
        with TestClient(d["client"].app) as client:
            return client.get(mappings_path, headers=headers(d["native"]["owner-a"]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        status_request = pool.submit(read_status)
        assert status_missing.wait(10), "Status did not reach its missing-row read"
        mappings_request = pool.submit(read_mappings)
        status = status_request.result(timeout=20)
        mappings = mappings_request.result(timeout=20)

    assert status.status_code == 200, status.text
    assert mappings.status_code == 200, mappings.text
    assert session_ids[status_path] != session_ids[mappings_path]
    assert observed_directories[status_path] is None
    assert observed_directories[mappings_path] == status.json()["directory_id"]
    assert not status.json()["configured"] and not status.json()["enabled"]
    assert mappings.json() == {"default_role": "member", "mappings": [], "groups": [], "channels": []}
    assert status.headers["cache-control"] == mappings.headers["cache-control"] == "no-store"
    with Session(d["engine"]) as session:
        rows = session.exec(select(SCIMDirectory).where(SCIMDirectory.workspace_id == workspace_id)).all()
        assert len(rows) == 1 and rows[0].directory_id == status.json()["directory_id"]
        assert rows[0].token_hash is None and rows[0].token_issued_by is None
        assert rows[0].token_version == 0 and not rows[0].enabled
        previous = session.get(SCIMDirectory, "a")
        assert (previous.token_hash, previous.token_version, previous.enabled) == previous_credential
        assert session.get(Workspace, workspace_id).name == "Unconfigured directory"
        assert session.get(WorkspaceMember, (workspace_id, "owner-a")).role == "owner"
        assert not session.exec(select(SCIMAudit).where(SCIMAudit.workspace_id == workspace_id)).all()


def test_admin_controls_require_native_workspace_role_password_and_mfa(directory, monkeypatch):
    d = directory; client = d["client"]
    for person in ("owner-b", "personal"):
        assert client.get("/workspaces/a/scim", headers=headers(d["native"][person])).status_code == 403
        assert client.post("/workspaces/a/scim/token", headers=headers(d["native"][person]), json={"password": PASSWORD}).status_code == 403
    assert client.post("/workspaces/a/scim/token", headers=headers(d["native"]["owner-a"]), json={"password": "wrong"}).status_code == 403
    with Session(d["engine"]) as session:
        from app.models.account_security import AccountSecurity
        state = session.get(AccountSecurity, "owner-a"); state.totp_secret = "factor-enabled"; session.add(state); session.commit()
    monkeypatch.setattr(account_security, "verify_factor", lambda session, state, code: code == "current-factor")
    assert client.post("/workspaces/a/scim/token", headers=headers(d["native"]["owner-a"]), json={"password": PASSWORD}).status_code == 403
    assert client.post("/workspaces/a/scim/token", headers=headers(d["native"]["owner-a"]), json={"password": PASSWORD, "code": "current-factor"}).status_code == 200


def test_real_scim_schema_resource_types_and_errors(directory):
    d = directory; client = d["client"]
    for path in ("/ServiceProviderConfig", "/ResourceTypes", "/ResourceTypes/User", "/Schemas", "/Schemas/" + USER_SCHEMA):
        response = client.get(d["base"] + path, headers=headers(d["token"]))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/scim+json")
        assert response.headers["cache-control"] == "no-store"
        assert "schemas" in response.json()
    invalid = client.get(d["base"] + "/Users?filter=userName%20co%20%22employee%22", headers=headers(d["token"]))
    assert invalid.status_code == 400 and invalid.json()["schemas"] == [ERROR_SCHEMA]
    assert invalid.json()["scimType"] == "invalidFilter" and "detail" in invalid.json()
    invalid_escape = client.get(d["base"] + "/Users", params={"filter": r'userName eq "\x"'}, headers=headers(d["token"]))
    assert invalid_escape.status_code == 400 and invalid_escape.json()["scimType"] == "invalidFilter"
    assert client.get(d["base"] + "/Users?startIndex=0", headers=headers(d["token"])).status_code == 400


def test_managed_provisioning_never_claims_personal_email_and_is_tenant_local(directory):
    d = directory; response = create(d)
    assert response.status_code == 201
    resource = response.json()
    assert resource["userName"] == "employee@example.com"
    assert resource["meta"]["resourceType"] == "User" and response.headers["etag"] == 'W/"1"'
    assert response.headers["location"].endswith("/Users/" + resource["id"])
    repeat = create(d); assert repeat.status_code == 200 and repeat.json()["id"] == resource["id"]
    with Session(d["engine"]) as session:
        row = session.get(SCIMUser, resource["id"])
        user = session.get(User, row.user_id)
        assert user.id != "personal" and user.email.endswith("@identity.invalid") and user.role == "user"
        assert session.get(User, "personal").email == "employee@example.com"
        assert session.get(WorkforcePrincipal, user.id).managed_email == "employee@example.com"
        assert session.get(WorkspaceMember, ("a", user.id)).role == "member"
        assert session.get(WorkspaceMember, ("b", user.id)) is None
    other = dict(d, base=d["other_base"], token=d["other_token"])
    second = create(other).json()
    assert second["id"] != resource["id"]
    assert d["client"].get(d["other_base"] + "/Users/" + resource["id"], headers=headers(d["other_token"])).status_code == 404
    assert create(d, "different@example.com", "employee-subject").status_code == 409
    assert create(d, "employee@example.com", "other-subject").status_code == 409


def test_filter_pagination_casefold_and_count_zero(directory):
    d = directory
    first = create(d).json()
    create(d, "second@example.com", "second-subject")
    client = d["client"]
    for expression in ('userName eq "EMPLOYEE@example.com"', 'externalId eq "employee-subject"', 'id eq "' + first["id"] + '"'):
        response = client.get(d["base"] + "/Users", params={"filter": expression}, headers=headers(d["token"]))
        assert response.status_code == 200 and response.json()["totalResults"] == 1
        assert response.json()["Resources"][0]["id"] == first["id"]
    response = client.get(d["base"] + "/Users", params={"startIndex": 2, "count": 1}, headers=headers(d["token"])).json()
    assert response["totalResults"] == 2 and len(response["Resources"]) == 1 and response["startIndex"] == 2
    response = client.get(d["base"] + "/Users?count=0", headers=headers(d["token"])).json()
    assert response["totalResults"] == 2 and response["Resources"] == []


def test_user_patch_version_atomic_rejection_and_profile(directory):
    d = directory; row = create(d).json()
    changed = patch(d, "/Users/" + row["id"], [{"op": "Replace", "path": "displayName", "value": "New name"}], **{"If-Match": 'W/"1"'})
    assert changed.status_code == 200 and changed.json()["displayName"] == "New name"
    assert changed.headers["etag"] == 'W/"2"'
    stale = patch(d, "/Users/" + row["id"], [{"op": "replace", "path": "active", "value": False}], **{"If-Match": 'W/"1"'})
    assert stale.status_code == 412
    invalid = patch(d, "/Users/" + row["id"], [{"op": "replace", "path": "displayName", "value": "Should rollback"},
        {"op": "replace", "path": "roles", "value": ["owner"]}])
    assert invalid.status_code == 400
    assert d["client"].get(d["base"] + "/Users/" + row["id"], headers=headers(d["token"])).json()["displayName"] == "New name"
    assert create(d, roles=[{"value": "admin"}]).status_code == 400


def test_group_mapping_applies_roles_channels_and_removes_only_scim_grants(directory):
    d = directory; one, two = create(d).json(), create(d, "two@example.com", "two").json()
    g = group(d, [one["id"], two["id"]]).json()
    with Session(d["engine"]) as session:
        native = session.get(SCIMUser, one["id"]).user_id
        session.add(ChannelMember(channel_id="a-public", user_id=native, role="member")); session.commit()
    response = mapping(d, g["id"], role="admin", channel_role="admin")
    assert response.status_code == 200
    with Session(d["engine"]) as session:
        for resource in (one, two):
            uid = session.get(SCIMUser, resource["id"]).user_id
            assert session.get(WorkspaceMember, ("a", uid)).role == "admin"
            assert session.get(ChannelMember, ("a-public", uid)).role == "admin"
            assert session.get(User, uid).role == "user"
    removed = patch(d, "/Groups/" + g["id"], [{"op": "Remove", "path": 'members[value eq "' + one["id"] + '"]'}])
    assert removed.status_code == 200
    with Session(d["engine"]) as session:
        uid = session.get(SCIMUser, one["id"]).user_id
        assert session.get(ChannelMember, ("a-public", uid)).role == "member"
        assert session.get(WorkspaceMember, ("a", uid)).role == "member"
    removed = patch(d, "/Groups/" + g["id"], [{"op": "remove", "path": "members"}])
    assert removed.status_code == 200
    with Session(d["engine"]) as session:
        uid = session.get(SCIMUser, two["id"]).user_id
        assert session.get(ChannelMember, ("a-public", uid)) is None
        assert session.get(WorkspaceMember, ("a", uid)).role == "member"


def test_mappings_cannot_escalate_owner_cross_workspace_or_private_channel(directory):
    d = directory; user = create(d).json(); g = group(d, [user["id"]]).json()
    with Session(d["engine"]) as session:
        managed_user_id = session.get(SCIMUser, user["id"]).user_id
    promoted = d["client"].patch("/workspaces/a/members/" + managed_user_id,
        headers=headers(d["native"]["owner-a"]), json={"role": "owner"})
    assert promoted.status_code == 403
    assert mapping(d, g["id"], role="admin", channels_list=[], owner="admin-a").status_code == 403
    assert mapping(d, g["id"], role="owner", channels_list=[]).status_code == 422
    assert mapping(d, g["id"], channels_list=[{"channel_id": "b-public", "role": "member"}]).status_code == 404
    assert mapping(d, g["id"], channels_list=[{"channel_id": "a-private", "role": "member"}]).status_code == 403
    assert mapping(d, g["id"], channels_list=[{"channel_id": "a-public", "role": "admin"}], owner="admin-a").status_code == 403
    other = dict(d, base=d["other_base"], token=d["other_token"])
    other_user = create(other).json()
    assert group(d, [other_user["id"]]).status_code == 404


def test_deprovision_remote_first_revokes_only_managed_workspace_and_keeps_history(directory, monkeypatch):
    d = directory; result = create(d).json(); g = group(d, [result["id"]]).json()
    assert mapping(d, g["id"]).status_code == 200
    with Session(d["engine"]) as session:
        row = session.get(SCIMUser, result["id"])
        uid = row.user_id
        scoped = create_token(uid, session, auth_methods=["sso"], workspace_scope={"workspace_id": "a", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="a", provider_revision=1)); session.commit()
    observations = []
    def remove(session, channel_id, user_id):
        observations.append((channel_id, session.get(ChannelMember, (channel_id, user_id)) is not None,
                             session.get(WorkspaceMember, ("a", user_id)) is not None))
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", remove)
    response = patch(d, "/Users/" + result["id"], [{"op": "Replace", "path": "active", "value": False}])
    assert response.status_code == 200 and response.json()["active"] is False
    assert observations == [("a-public", True, True)]
    with Session(d["engine"]) as session:
        assert session.get(WorkspaceMember, ("a", uid)) is None
        assert session.get(ChannelMember, ("a-public", uid)) is None
        assert session.get(WorkforcePrincipal, uid).active is False
        assert session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(scoped))).one().revoked_at
        assert session.get(User, uid) is not None and not session.get(User, uid).is_blocked
        assert session.get(WorkspaceMember, ("a", "personal")) and session.get(WorkspaceMember, ("b", "personal"))
    assert d["client"].get("/auth/me", headers=headers(scoped)).status_code == 401
    assert d["client"].get("/auth/me", headers=headers(d["native"]["personal"])).status_code == 200
    restored = patch(d, "/Users/" + result["id"], [{"op": "replace", "path": "active", "value": True}])
    assert restored.status_code == 200 and restored.json()["active"]
    with Session(d["engine"]) as session:
        assert session.get(WorkspaceMember, ("a", uid)).role == "member"
        assert session.get(ChannelMember, ("a-public", uid)) is not None


def test_remote_deprovision_failure_does_not_commit_local_disable(directory, monkeypatch):
    d = directory; result = create(d).json(); g = group(d, [result["id"]]).json(); assert mapping(d, g["id"]).status_code == 200
    def fail(*args): raise HTTPException(503, "Matrix unavailable")
    monkeypatch.setattr(matrix_bridge, "remove_secure_member", fail)
    response = patch(d, "/Users/" + result["id"], [{"op": "replace", "path": "active", "value": False}])
    assert response.status_code == 503
    with Session(d["engine"]) as session:
        row = session.get(SCIMUser, result["id"])
        assert row.active and session.get(WorkforcePrincipal, row.user_id).active
        assert session.get(WorkspaceMember, ("a", row.user_id))


def test_delete_retains_managed_identity_and_reprovision_reuses_own_history_identity(directory):
    d = directory; row = create(d).json()
    with Session(d["engine"]) as session: uid = session.get(SCIMUser, row["id"]).user_id
    assert d["client"].delete(d["base"] + "/Users/" + row["id"], headers=headers(d["token"])).status_code == 204
    assert d["client"].get(d["base"] + "/Users/" + row["id"], headers=headers(d["token"])).status_code == 404
    assert d["client"].get(d["base"] + "/Users", headers=headers(d["token"])).json()["totalResults"] == 0
    revived = create(d); assert revived.status_code == 201 and revived.json()["id"] == row["id"]
    with Session(d["engine"]) as session: assert session.get(SCIMUser, row["id"]).user_id == uid
    events = d["client"].get("/workspaces/a/scim/audit", headers=headers(d["native"]["owner-a"])).json()["events"]
    assert {event["action"] for event in events} >= {"credential_rotated", "user_provisioned", "user_deleted"}
    assert d["token"] not in json.dumps(events)


def test_existing_personal_subject_is_not_silently_claimed_by_scim(directory):
    d = directory
    from app.models.oidc import OIDCIdentity
    from app.models.workspace_oidc import WorkspaceOIDCIdentity
    identity_id = token_digest("workspace\0a\0https://idp.example.test\0client-a\0employee-subject")
    with Session(d["engine"]) as session:
        session.add(OIDCIdentity(id=identity_id, user_id="personal", issuer="https://idp.example.test", subject="employee-subject", provider_name="Customer"))
        session.add(WorkspaceOIDCIdentity(identity_id=identity_id, workspace_id="a")); session.commit()
    response = create(d)
    assert response.status_code == 409 and response.json()["scimType"] == "uniqueness"
    with Session(d["engine"]) as session:
        assert session.get(WorkforcePrincipal, "personal") is None
        assert session.get(WorkspaceMember, ("a", "personal"))


@pytest.mark.parametrize("change", ["demote", "remove", "block"])
def test_service_token_is_bound_to_current_active_owner_and_new_owner_can_rotate(directory, change):
    d = directory; client = d["client"]
    assert client.post("/workspaces/a/scim/token", headers=headers(d["native"]["admin-a"]), json={"password": PASSWORD}).status_code == 403
    with Session(d["engine"]) as session:
        session.get(WorkspaceMember, ("a", "personal")).role = "owner"
        # A directory may initially be discovered by an admin; that is not its token issuer.
        session.get(SCIMDirectory, "a").created_by = "admin-a"
        if change == "remove": session.delete(session.get(WorkspaceMember, ("a", "owner-a")))
        elif change == "demote": session.get(WorkspaceMember, ("a", "owner-a")).role = "admin"
        else: session.get(User, "owner-a").is_blocked = True
        session.commit()
    response = client.get(d["base"] + "/Users", headers=headers(d["token"]))
    assert response.status_code == 401
    current = client.get("/workspaces/a/scim", headers=headers(d["native"]["personal"]))
    assert current.status_code == 200 and current.json()["credential"]["requires_rotation"] is True
    rotated = client.post("/workspaces/a/scim/token", headers=headers(d["native"]["personal"]), json={"password": PASSWORD})
    assert rotated.status_code == 200
    assert client.get(d["base"] + "/Users", headers=headers(rotated.json()["token"])).status_code == 200


def test_nonowner_can_keep_existing_admin_mapping_but_cannot_change_or_remove_it(directory):
    d = directory; person = create(d).json(); g = group(d, [person["id"]]).json()
    assert mapping(d, g["id"], role="admin", channels_list=[]).status_code == 200
    assert mapping(d, g["id"], role="admin", channels_list=[], owner="admin-a").status_code == 200
    assert mapping(d, g["id"], role="member", channels_list=[], owner="admin-a").status_code == 403
    removed = d["client"].put("/workspaces/a/scim/mappings", headers=headers(d["native"]["admin-a"]),
                            json={"password": PASSWORD, "mappings": []})
    assert removed.status_code == 403


@pytest.mark.parametrize("change", ["channel_admin", "channel_membership", "workspace_admin", "blocked"])
def test_stale_channel_approver_cannot_add_recipients_and_current_admin_can_reapprove(directory, change):
    d = directory; first = create(d).json(); second = create(d, "two@example.com", "two").json()
    g = group(d, [first["id"]]).json()
    assert mapping(d, g["id"]).status_code == 200
    with Session(d["engine"]) as session:
        session.get(WorkspaceMember, ("a", "personal")).role = "owner"
        session.add(ChannelMember(channel_id="a-public", user_id="admin-a", role="admin"))
        session.commit()
    # The service token's owner is independent of the channel grant's approver.
    rotated = d["client"].post("/workspaces/a/scim/token", headers=headers(d["native"]["personal"]), json={"password": PASSWORD})
    assert rotated.status_code == 200
    d["token"] = rotated.json()["token"]
    with Session(d["engine"]) as session:
        if change == "channel_admin": session.get(ChannelMember, ("a-public", "owner-a")).role = "member"
        elif change == "channel_membership": session.delete(session.get(ChannelMember, ("a-public", "owner-a")))
        elif change == "workspace_admin": session.get(WorkspaceMember, ("a", "owner-a")).role = "member"
        else: session.get(User, "owner-a").is_blocked = True
        session.commit()
    changed = patch(d, "/Groups/" + g["id"], [{"op": "add", "path": "members", "value": [{"value": second["id"]}]}])
    assert changed.status_code == 200
    with Session(d["engine"]) as session:
        uid = session.get(SCIMUser, second["id"]).user_id
        assert session.get(ChannelMember, ("a-public", uid)) is None
    state = d["client"].get("/workspaces/a/scim/mappings", headers=headers(d["native"]["personal"])).json()
    assert state["mappings"][0]["channels"][0]["requires_reapproval"] is True
    assert mapping(d, g["id"], owner="admin-a").status_code == 200
    with Session(d["engine"]) as session:
        uid = session.get(SCIMUser, second["id"]).user_id
        assert session.get(ChannelMember, ("a-public", uid)) is not None


def test_actual_oidc_first_signin_scim_deactivation_and_reactivation(tenants):
    t = tenants; client = t["client"]
    client.app.include_router(scim.router)
    client.app.include_router(scim.admin_router)
    provider = enable(t)
    t["first"].email = "member@example.com"
    with Session(t["engine"]) as session:
        session.add(WorkspaceIdentityPolicy(workspace_id="tenant-one", mode="provisioned", identity_claim="sub"))
        session.commit()
    issued = client.post("/workspaces/tenant-one/scim/token", headers=headers(t["token1"]), json={"password": OIDC_PASSWORD})
    assert issued.status_code == 200
    directory = issued.json()
    base = "/scim/v2/" + directory["directory_id"]
    scim_headers = headers(directory["token"])
    provisioned = client.post(base + "/Users", headers=scim_headers, json={"schemas": [USER_SCHEMA],
        "userName": "member@example.com", "externalId": t["first"].subject, "displayName": "SCIM Member"})
    assert provisioned.status_code == 201
    record = provisioned.json()
    signed_in = finish(client, begin(t, provider))
    assert signed_in["user_id"] != t["member"]
    native_headers = headers(signed_in["access_token"])
    assert client.get("/auth/me", headers=native_headers).json()["session_scope"] == "workspace"
    assert [row["id"] for row in client.get("/workspaces", headers=native_headers).json()] == ["tenant-one"]
    assert client.get("/account/security", headers=native_headers).status_code == 403
    deactivated = client.patch(base + "/Users/" + record["id"], headers=scim_headers,
        json={"schemas": [PATCH_SCHEMA], "Operations": [{"op": "replace", "path": "active", "value": False}]})
    assert deactivated.status_code == 200
    assert client.get("/auth/me", headers=native_headers).status_code == 401
    failed = callback(client, begin(t, provider))
    assert "#sso_error=" in failed.headers["location"]
    assert client.get("/auth/me", headers=headers(t["member_token"])).status_code == 200
    reactivated = client.patch(base + "/Users/" + record["id"], headers=scim_headers,
        json={"schemas": [PATCH_SCHEMA], "Operations": [{"op": "replace", "path": "active", "value": True}]})
    assert reactivated.status_code == 200
    returned = finish(client, begin(t, provider))
    assert returned["user_id"] == signed_in["user_id"]
