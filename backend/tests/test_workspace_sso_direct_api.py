"""A customer's IdP may act only inside that customer's workspace."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import (
    account, admin, auth, channels, direct_messages, files, integrations_content,
    mentions, messages, notifications, presence, saved_items, workspaces,
)
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import Bookmark, ChannelNotificationSetting
from app.models.direct_message import DirectMessage
from app.models.file import File
from app.models.message import Message
from app.models.notification import Notification, NotificationPreference
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services.auth import create_token, token_digest


@pytest.fixture
def scoped_api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id=name, name=name, email=f"{name}@example.com", password_hash="unused",
                 role="admin" if name == "actor" else "user")
            for name in ("actor", "a-peer", "b-peer", "legacy-peer")
        ])
        session.add_all([Workspace(id=workspace, name=workspace) for workspace in ("a", "b")])
        session.flush()
        session.add_all([
            WorkspaceMember(workspace_id=workspace, user_id=user, role="owner" if user == "actor" else "member")
            for workspace in ("a", "b") for user in ("actor", f"{workspace}-peer")
        ])
        for channel in ("a", "b", "legacy"):
            session.add(Channel(id=channel, name=channel))
            session.flush()
            if channel != "legacy":
                session.add(WorkspaceChannel(channel_id=channel, workspace_id=channel))
            session.add_all([
                ChannelMember(channel_id=channel, user_id=user)
                for user in ("actor", f"{channel}-peer")
            ])
            session.add(Message(id=f"message-{channel}", channel_id=channel, author_id=f"{channel}-peer",
                                content=f"Private {channel} content", encrypted=False, mentions='["actor"]'))
            session.add(Bookmark(user_id="actor", channel_id=channel, item_id=f"message-{channel}"))
            session.add(File(id=f"file-{channel}", channel_id=channel, uploader_id="actor",
                             filename=f"Private {channel} file.txt", content_type="text/plain", size=4,
                             storage_path=f"unused-{channel}"))
            session.add(Notification(id=f"notification-{channel}", user_id="actor", type="mention",
                                     title=f"Private {channel} title", entity_type="channel", entity_id=channel))
        session.add(Notification(id="global", user_id="actor", type="system", title="Account security notice"))
        for workspace in ("a", "b"):
            channel_id = f"dm-{workspace}"
            session.add(Channel(id=channel_id, type="direct"))
            session.flush()
            session.add(WorkspaceChannel(channel_id=channel_id, workspace_id=workspace))
            session.add_all([ChannelMember(channel_id=channel_id, user_id=user) for user in ("actor", f"{workspace}-peer")])
            low, high = sorted(("actor", f"{workspace}-peer"))
            session.add(DirectMessage(channel_id=channel_id, workspace_id=workspace, user_low=low, user_high=high))
        session.add(NotificationPreference(user_id="actor", email=False))
        session.add(ChannelNotificationSetting(user_id="actor", channel_id="a"))
        session.add(WorkspaceOIDCProvider(workspace_id="a", name="Customer A", issuer_url="https://a.example.com",
                                          client_id="client-a", created_by="actor", enabled=True, revision=3,
                                          tested_revision=3, tested_at=datetime.utcnow()))
        session.commit()
        native = create_token("actor", session)
        scoped = create_token("actor", session, auth_methods=["sso"],
                              workspace_scope={"workspace_id": "a", "provider_revision": 3})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped), workspace_id="a", provider_revision=3))
        session.commit()
    monkeypatch.setattr(presence, "get_online_users", lambda *_: ["actor", "a-peer", "b-peer", "legacy-peer"])
    monkeypatch.setattr(presence, "get_user_presence", lambda user_id: {"status": "online", "last_seen": None})
    api = FastAPI()
    for module in (account, admin, auth, channels, direct_messages, files, integrations_content,
                   mentions, messages, notifications, presence, saved_items, workspaces):
        api.include_router(module.router)

    def sessions():
        with Session(engine) as session:
            yield session

    api.dependency_overrides[get_session] = sessions
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, engine=engine,
                              scoped={"Authorization": f"Bearer {scoped}"},
                              native={"Authorization": f"Bearer {native}"})
    engine.dispose()


def test_scoped_inbox_and_unread_count_hide_other_workspace_and_account_records(scoped_api):
    api = scoped_api
    response = api.client.get("/notifications", headers=api.scoped)
    assert response.status_code == 200
    assert [record["id"] for record in response.json()] == ["notification-a"]
    assert api.client.get("/notifications/unread-count", headers=api.scoped).json() == {"unread_count": 1}
    for cursor in ("notification-b", "notification-legacy", "global"):
        assert api.client.get(f"/notifications?before={cursor}", headers=api.scoped).status_code == 404
    assert len(api.client.get("/notifications", headers=api.native).json()) == 4


@pytest.mark.parametrize("notification_id", ["notification-b", "notification-legacy", "global"])
def test_scoped_known_notification_ids_cannot_mutate_account_inbox(scoped_api, notification_id):
    api = scoped_api
    assert api.client.post(f"/notifications/{notification_id}/read", headers=api.scoped).status_code == 404
    assert api.client.delete(f"/notifications/{notification_id}", headers=api.scoped).status_code == 404
    with Session(api.engine) as session:
        assert session.get(Notification, notification_id).read is False


def test_scoped_bulk_notification_actions_preserve_every_other_scope(scoped_api):
    api = scoped_api
    assert api.client.post("/notifications/read-all", headers=api.scoped).status_code == 200
    with Session(api.engine) as session:
        assert {row.id for row in session.exec(select(Notification)).all() if row.read} == {"notification-a"}
    assert api.client.delete("/notifications", headers=api.scoped).status_code == 200
    with Session(api.engine) as session:
        assert {row.id for row in session.exec(select(Notification)).all()} == {"notification-b", "notification-legacy", "global"}
    assert api.client.delete("/notifications", headers=api.native).status_code == 200
    with Session(api.engine) as session:
        assert session.exec(select(Notification)).all() == []


def test_scoped_channel_digest_does_not_change_account_preferences(scoped_api):
    api = scoped_api
    assert api.client.put("/notifications/preferences", json={"email": True}, headers=api.scoped).status_code == 403
    response = api.client.put("/notifications/channels/a/settings", json={"email_digest": "daily"}, headers=api.scoped)
    assert response.status_code == 200
    with Session(api.engine) as session:
        assert session.get(ChannelNotificationSetting, ("actor", "a")).email_digest == "daily"
        assert session.exec(select(NotificationPreference)).one().email is False
    assert api.client.put("/notifications/preferences", json={"email": True}, headers=api.native).status_code == 200


def test_scoped_saved_references_mentions_and_presence_have_no_other_workspace_data(scoped_api):
    api = scoped_api
    assert [row["channel_id"] for row in api.client.get("/bookmarks", headers=api.scoped).json()] == ["a"]
    assert [row["channel_id"] for row in api.client.get("/mentions/me", headers=api.scoped).json()] == ["a"]
    assert set(api.client.get("/presence/online", headers=api.scoped).json()["online_users"]) == {"actor", "a-peer"}
    for channel in ("b", "legacy"):
        assert api.client.delete(f"/bookmarks/{channel}/message-{channel}", headers=api.scoped).status_code == 403
        assert api.client.get(f"/presence/users/{channel}-peer", headers=api.scoped).status_code == 404
        assert api.client.get(f"/presence/channel/{channel}", headers=api.scoped).status_code == 403
    with Session(api.engine) as session:
        assert len(session.exec(select(Bookmark)).all()) == 3
    # A native session retains account-wide cleanup, including inaccessible references.
    assert api.client.delete("/bookmarks/b/message-b", headers=api.native).status_code == 200


def test_customer_idp_cannot_inherit_global_admin_identity(scoped_api):
    api = scoped_api
    scoped = api.client.get("/auth/me", headers=api.scoped)
    assert scoped.status_code == 200
    assert {key: scoped.json()[key] for key in ("role", "session_scope", "workspace_id")} == {
        "role": "user", "session_scope": "workspace", "workspace_id": "a",
    }
    native = api.client.get("/auth/me", headers=api.native)
    assert native.json()["role"] == "admin" and native.json()["session_scope"] == "account"
    assert api.client.get("/admin/me", headers=api.scoped).json()["is_admin"] is False
    assert api.client.get("/admin/me", headers=api.native).json()["is_admin"] is True
    with Session(api.engine) as session:
        assert session.get(User, "actor").role == "admin"


@pytest.mark.parametrize("endpoint", ["/account/security", "/account/sessions", "/admin/stats", "/admin/users"])
def test_customer_idp_cannot_read_global_security_or_admin_metadata(scoped_api, endpoint):
    api = scoped_api
    assert api.client.get(endpoint, headers=api.scoped).status_code == 403
    assert api.client.get(endpoint, headers=api.native).status_code == 200


@pytest.mark.parametrize("channel", ["b", "legacy"])
def test_customer_idp_cannot_read_other_channel_history_files_or_integrations(scoped_api, channel):
    api = scoped_api
    endpoints = (
        f"/channels/{channel}", f"/channels/{channel}/members", f"/channels/{channel}/pins",
        f"/messages/channels/{channel}", f"/messages/channels/{channel}/sync", f"/messages/message-{channel}",
        f"/files/channels/{channel}", f"/files/file-{channel}/versions",
        f"/channels/{channel}/integrations/calendar", f"/channels/{channel}/integrations/documents",
    )
    for endpoint in endpoints:
        assert api.client.get(endpoint, headers=api.scoped).status_code == 403, endpoint
        assert api.client.get(endpoint, headers=api.native).status_code == 200, endpoint
    assert api.client.get(f"/files/file-{channel}", headers=api.scoped).status_code == 403


def test_customer_idp_cannot_list_open_or_archive_direct_messages_in_other_workspace(scoped_api):
    api = scoped_api
    assert [row["id"] for row in api.client.get("/direct-messages", headers=api.scoped).json()] == ["dm-a"]
    assert len(api.client.get("/direct-messages", headers=api.native).json()) == 2
    assert api.client.get("/direct-messages?workspace_id=b", headers=api.scoped).status_code == 403
    assert api.client.patch("/direct-messages/dm-b", json={"archived": True}, headers=api.scoped).status_code == 403
    assert api.client.post("/direct-messages", json={"workspace_id": "b", "user_id": "b-peer"}, headers=api.scoped).status_code == 403
    assert api.client.get("/direct-messages/blocks", headers=api.scoped).status_code == 403
    assert api.client.get("/direct-messages/blocks", headers=api.native).status_code == 200
    assert api.client.patch("/direct-messages/dm-a", json={"archived": True}, headers=api.scoped).status_code == 200
    assert [row["id"] for row in api.client.get("/workspaces", headers=api.scoped).json()] == ["a"]
    assert api.client.get("/workspaces/b", headers=api.scoped).status_code == 403
    assert api.client.get("/workspaces/b/members", headers=api.scoped).status_code == 403


@pytest.mark.parametrize("change", ["disable", "revision", "remove-provider", "remove-member", "remove-sidecar"])
def test_customer_provider_revocation_cannot_upgrade_scoped_token_to_account_access(scoped_api, change):
    api = scoped_api
    with Session(api.engine) as session:
        provider = session.get(WorkspaceOIDCProvider, "a")
        if change == "disable":
            provider.enabled = False
            session.add(provider)
        elif change == "revision":
            provider.revision += 1
            session.add(provider)
        elif change == "remove-provider":
            session.delete(provider)
        elif change == "remove-member":
            session.delete(session.get(WorkspaceMember, ("a", "actor")))
        else:
            session.delete(session.exec(select(WorkspaceOIDCSession)).one())
        session.commit()
    for endpoint in ("/auth/me", "/notifications", "/bookmarks", "/mentions/me", "/presence/online"):
        assert api.client.get(endpoint, headers=api.scoped).status_code == 401
    assert api.client.get("/auth/me", headers=api.native).status_code == 200
