"""Cross-feature authority and encryption boundaries for the product release."""
import base64
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event, update
from sqlmodel import Session, SQLModel, create_engine

from app import main as application  # Register the complete additive model graph.
from app.api import notifications, secure_messaging
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.notification import Notification
from app.models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from app.models.secure_channel import SecureChannel
from app.models.session import ActiveSession, RevokedToken
from app.models.shared_documents import SharedDocument, SharedDocumentSnapshot, SharedDocumentUpdate
from app.models.user import User
from app.models.workflow import WorkflowDefinition, WorkflowRevision, WorkflowRun
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import request_guard
from app.services.auth import create_token, decode_token_claims, token_digest
from app.services.sso_scope import current_workspace_scope, require_unrestricted


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "matrix_enabled", True)
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"r" * 32).decode()))
    engine = create_engine(f"sqlite:///{tmp_path / 'release.db'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name, email=name + "@example.com", password_hash="unused") for name in ("owner", "peer", "outsider")])
        session.add(Workspace(id="workspace", name="Private workspace"))
        session.add_all([Channel(id="channel", name="Private project"), Channel(id="empty", name="Empty")])
        session.commit()
        session.add_all([WorkspaceChannel(workspace_id="workspace", channel_id=name) for name in ("channel", "empty")])
        session.add_all([WorkspaceMember(workspace_id="workspace", user_id=name, role="owner" if name == "owner" else "member") for name in ("owner", "peer", "outsider")])
        session.add_all([ChannelMember(channel_id=channel, user_id=name, role="admin" if name == "owner" else "member") for channel in ("channel", "empty") for name in ("owner", "peer")])
        session.add(WorkspaceOIDCProvider(workspace_id="workspace", name="Customer IdP", issuer_url="https://idp.example.com", client_id="client", created_by="owner", enabled=True))
        session.commit()
        tokens = {name: create_token(name, session) for name in ("owner", "peer", "outsider")}
        tokens["scoped"] = create_token("owner", session, auth_methods=["sso"], workspace_scope={"workspace_id": "workspace", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(tokens["scoped"]), workspace_id="workspace", provider_revision=1))
        session.commit()
    app = FastAPI()
    app.include_router(secure_messaging.router)
    app.include_router(notifications.router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield SimpleNamespace(engine=engine, client=client, tokens=tokens, headers=lambda who="owner": {"Authorization": "Bearer " + tokens[who]})
    engine.dispose()


def remember(session, token):
    claims = decode_token_claims(token)
    assert claims
    digest = token_digest(token)
    request_guard.remember_request(session, digest, claims)
    assert token not in repr(session.info)
    return digest


@pytest.mark.parametrize("change", ["version", "revoked", "expired", "tombstone", "blocked", "deleted"])
def test_fresh_request_guard_rejects_native_authority_changed_after_identity_load(release, change):
    with Session(release.engine) as waiting:
        digest = remember(waiting, release.tokens["owner"])
        cached_user = waiting.get(User, "owner")
        cached_security = waiting.get(AccountSecurity, "owner")
        cached_session = waiting.query(ActiveSession).filter(ActiveSession.token_hash == digest).one()
        request_guard.recheck_actor(waiting, "owner")
        with Session(release.engine) as changed:
            if change == "version":
                changed.exec(update(AccountSecurity).where(AccountSecurity.user_id == "owner").values(auth_version=1))
            elif change == "revoked":
                changed.exec(update(ActiveSession).where(ActiveSession.token_hash == digest).values(revoked_at=datetime.utcnow()))
            elif change == "expired":
                changed.exec(update(ActiveSession).where(ActiveSession.token_hash == digest).values(expires_at=datetime.utcnow() - timedelta(seconds=1)))
            elif change == "tombstone":
                changed.add(RevokedToken(token_hash=digest, expires_at=datetime.utcnow() + timedelta(days=1)))
            elif change == "blocked":
                changed.exec(update(User).where(User.id == "owner").values(is_blocked=True))
            else:
                changed.delete(changed.query(ActiveSession).filter(ActiveSession.token_hash == digest).one())
            changed.commit()
        # Cached ORM objects retain old authority; guard scalar queries must not.
        assert cached_user.is_blocked is False and cached_security.auth_version == 0 and cached_session.revoked_at is None
        with pytest.raises(HTTPException) as rejected:
            request_guard.recheck_actor(waiting, "owner")
        assert rejected.value.status_code == 401


@pytest.mark.parametrize("change", ["revision", "disabled", "sidecar", "membership"])
def test_fresh_guard_rejects_changed_customer_provider_and_never_widens_scope(release, change):
    with Session(release.engine) as waiting:
        digest = remember(waiting, release.tokens["scoped"])
        provider = waiting.get(WorkspaceOIDCProvider, "workspace")
        request_guard.recheck_actor(waiting, "owner")
        assert current_workspace_scope(waiting) == "workspace"
        with Session(release.engine) as changed:
            if change == "revision":
                changed.exec(update(WorkspaceOIDCProvider).where(WorkspaceOIDCProvider.workspace_id == "workspace").values(revision=2))
            elif change == "disabled":
                changed.exec(update(WorkspaceOIDCProvider).where(WorkspaceOIDCProvider.workspace_id == "workspace").values(enabled=False))
            elif change == "sidecar":
                changed.delete(changed.get(WorkspaceOIDCSession, digest))
            else:
                changed.delete(changed.get(WorkspaceMember, ("workspace", "owner")))
            changed.commit()
        assert provider.enabled and provider.revision == 1
        for _ in range(2):
            with pytest.raises(HTTPException) as rejected:
                request_guard.recheck_actor(waiting, "owner")
            assert rejected.value.status_code == 401
            # A caller handling an authorization failure must not accidentally
            # turn the scoped request into a personal-account request later.
            with pytest.raises(HTTPException) as follow_on:
                require_unrestricted(waiting)
            assert follow_on.value.status_code == 401


def test_request_guard_rechecks_verified_claim_expiry_and_subject(release, monkeypatch):
    with Session(release.engine) as session:
        remember(session, release.tokens["owner"])
        with pytest.raises(HTTPException) as rejected:
            request_guard.recheck_actor(session, "peer")
        assert rejected.value.status_code == 401
        claims = decode_token_claims(release.tokens["owner"])
        monkeypatch.setattr(request_guard.time, "time", lambda: claims["exp"] + 1)
        with pytest.raises(HTTPException) as rejected:
            request_guard.recheck_actor(session, "owner")
        assert rejected.value.status_code == 401


def add_native_history(session, kind):
    if kind in {"project", "task"}:
        session.add(ProjectBoard(id="project", channel_id="channel", created_by="owner", revision=1, archived=True))
        session.flush()
        item_id = "project"
        if kind == "task":
            session.add(TaskCard(id="task", project_id="project", channel_id="channel", created_by="owner", revision=1, archived=True))
            item_id = "task"
        session.add(ProjectTaskRevision(kind=kind, item_id=item_id, revision=1, created_by="owner", content_mode="server", encrypted_payload="retained-native-history", archived=True))
    elif kind == "document":
        session.add(SharedDocument(id="doc", channel_id="channel", created_by="owner", archived=True, update_count=1))
        session.flush()
        session.add(SharedDocumentUpdate(document_id="doc", update_id="u", sequence=1, created_by="owner", content_mode="server", payload_hash="hash", encrypted_payload="retained-native-update"))
        session.add(SharedDocumentSnapshot(document_id="doc", covers_sequence=1, created_by="owner", content_mode="server", encrypted_payload="retained-native-snapshot"))
    else:
        session.add(WorkflowDefinition(id="workflow", workspace_id="workspace", channel_id="channel", created_by="owner", granted_by="owner", trigger="manual", enabled=False))
        session.flush()
        session.add(WorkflowRevision(workflow_id="workflow", revision=1, encrypted_definition="retained-native-workflow", created_by="owner"))
        if kind == "workflow_run":
            session.add(WorkflowRun(id="run", workflow_id="workflow", workspace_id="workspace", channel_id="channel", workflow_revision=1, event_id="event", event_kind="manual", status="completed"))


@pytest.mark.parametrize("kind", ["project", "task", "document", "workflow", "workflow_run"])
def test_archived_native_product_histories_block_e2ee_enable(release, monkeypatch, kind):
    monkeypatch.setattr(secure_messaging, "require_matrix", lambda: None)
    def never_provision(*_):
        pytest.fail("Native history must be fenced before provisioning a Matrix room")
    monkeypatch.setattr(secure_messaging, "create_secure_room", never_provision)
    monkeypatch.setattr(secure_messaging, "sync_secure_membership", never_provision)
    with Session(release.engine) as session:
        add_native_history(session, kind)
        session.commit()
        assert secure_messaging.has_native_product_history(session, "channel")
        assert not secure_messaging.has_native_product_history(session, "empty")
    response = release.client.post("/secure/channels/channel/enable", headers=release.headers())
    assert response.status_code == 409 and "empty channel" in response.json()["detail"]
    with Session(release.engine) as session:
        assert session.get(SecureChannel, "channel") is None
        assert session.get(Channel, "channel").name == "Private project"
        assert secure_messaging.has_native_product_history(session, "channel")


def test_genuinely_empty_private_channel_can_enable_encryption(release, monkeypatch):
    monkeypatch.setattr(secure_messaging, "require_matrix", lambda: None)
    def provision(session, binding):
        binding.matrix_room_id = "!empty:test"
        binding.status = "active"
        session.add(binding)
    monkeypatch.setattr(secure_messaging, "create_secure_room", provision)
    monkeypatch.setattr(secure_messaging, "sync_secure_membership", lambda *_: None)
    response = release.client.post("/secure/channels/empty/enable", headers=release.headers())
    assert response.status_code == 200, response.text
    assert response.json() == {"channel_id": "empty", "room_id": "!empty:test", "status": "active"}


@pytest.mark.parametrize("revocation", ["channel", "workspace", "run"])
def test_secure_workflow_notice_is_visible_only_while_referenced_grants_exist(release, revocation):
    with Session(release.engine) as session:
        add_native_history(session, "workflow_run")
        session.add(SecureChannel(channel_id="channel", created_by="owner", status="active", matrix_room_id="!private:test"))
        session.add(Notification(id="workflow-notice", user_id="peer", type="system", title="Workflow needs attention", entity_type="workflow_run", entity_id="run"))
        session.add(Notification(id="native-channel-notice", user_id="peer", type="system", title="Native text must stay hidden", entity_type="channel", entity_id="channel"))
        session.add(Notification(id="outsider-notice", user_id="outsider", type="system", title="Workflow needs attention", entity_type="workflow_run", entity_id="run"))
        session.commit()
    before = release.client.get("/notifications", headers=release.headers("peer"))
    assert before.status_code == 200 and [n["id"] for n in before.json()] == ["workflow-notice"]
    assert release.client.get("/notifications/unread-count", headers=release.headers("peer")).json() == {"unread_count": 1}
    assert release.client.get("/notifications", headers=release.headers("outsider")).json() == []
    with Session(release.engine) as session:
        if revocation == "channel":
            session.delete(session.get(ChannelMember, ("channel", "peer")))
        elif revocation == "workspace":
            session.delete(session.get(WorkspaceMember, ("workspace", "peer")))
        else:
            session.delete(session.get(WorkflowRun, "run"))
        session.commit()
    assert release.client.get("/notifications", headers=release.headers("peer")).json() == []
    assert release.client.get("/notifications/unread-count", headers=release.headers("peer")).json() == {"unread_count": 0}
    assert release.client.get("/notifications?before=workflow-notice", headers=release.headers("peer")).status_code == 404
