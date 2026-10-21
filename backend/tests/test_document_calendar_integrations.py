"""Real content storage, access boundaries and encrypted channel fail-closed behavior."""
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.api import integrations_content, secure_messaging
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.direct_message import UserBlock
from app.models.integration_content import IntegrationContentRevision
from app.models.post import Post  # Register the legacy table used by secure enable's content fence.
from app.models.secure_channel import SecureChannel
from app.models.user import User
from app.services.auth import create_token
from app.services.integration_content import CalendarFields, document_url


@pytest.fixture
def content():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name, email=name + "@example.com", password_hash="unused") for name in ("owner", "member", "peer", "outsider")])
        session.add_all([Channel(id="room", name="Room"), Channel(id="other", name="Other")])
        session.commit()
        session.add_all([ChannelMember(channel_id="room", user_id=name, role="admin" if name == "owner" else "member") for name in ("owner", "member", "peer")])
        session.add(ChannelMember(channel_id="other", user_id="outsider", role="admin"))
        session.commit()
    app = FastAPI(); app.include_router(integrations_content.router); app.include_router(secure_messaging.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client: yield SimpleNamespace(client=client, engine=engine)
    engine.dispose()


def headers(user="member"):
    return {"Authorization": "Bearer " + create_token(user)}


DOC = {"title": "Private budget", "provider": "nextcloud", "url": "https://cloud.example.com/s/Abc123", "notes": "Private document note"}
EVENT = {"title": "Private planning", "description": "Team agenda", "location": "Studio",
         "starts_at": "2026-10-25T02:30:00+02:00", "ends_at": "2026-10-25T02:45:00+02:00",
         "all_day": False, "timezone": "Europe/Stockholm", "reminder_minutes": 15, "external_uid": None}


def test_native_documents_are_encrypted_revisioned_scoped_and_deleted(content):
    path = "/channels/room/integrations/documents"
    response = content.client.post(path, json={"id": "doc1", "fields": DOC}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["fields"] == DOC
    with Session(content.engine) as session:
        stored = session.exec(select(IntegrationContentRevision)).one()
        assert DOC["title"] not in stored.encrypted_payload and DOC["url"] not in stored.encrypted_payload
        first_ciphertext = stored.encrypted_payload
    assert content.client.get(path, headers=headers("outsider")).status_code == 403
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 1, "fields": DOC}, headers=headers("peer")).status_code == 403
    fields = {**DOC, "title": "Revised budget"}
    revised = content.client.post(path, json={"id": "doc1", "expected_revision": 1, "fields": fields}, headers=headers("owner"))
    assert revised.status_code == 200 and revised.json()["revision"] == 2
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 1, "fields": DOC}, headers=headers()).status_code == 409
    history = content.client.get(path + "/doc1/revisions", headers=headers("peer")).json()
    assert [row["fields"]["title"] for row in history] == ["Revised budget", "Private budget"]
    with Session(content.engine) as session:
        assert session.exec(select(IntegrationContentRevision).where(IntegrationContentRevision.revision == 1)).one().encrypted_payload == first_ciphertext
    assert content.client.post("/channels/other/integrations/documents", json={"id": "doc1", "fields": DOC}, headers=headers("outsider")).status_code == 404
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 2, "deleted": True}, headers=headers()).status_code == 200
    assert content.client.get(path, headers=headers()).json() == []
    assert content.client.post(path, json={"id": "doc1", "expected_revision": 3, "fields": DOC}, headers=headers()).status_code == 409
    assert content.client.get(path + "/doc1/revisions", headers=headers()).json()[0]["deleted"] is True


@pytest.mark.parametrize("provider,url", [
    ("google_drive", "https://docs.google.com.evil.example/document/d/secret"),
    ("google_drive", "javascript:alert(1)"),
    ("sharepoint", "https://team.sharepoint.com.evil.example/doc"),
    ("nextcloud", "https://user:password@cloud.example.com/s/doc"),
    ("nextcloud", "https://cloud.example.com/s/doc?access_token=secret"),
    ("nextcloud", "https://cloud.example.com/s/doc#secret"),
    ("nextcloud", "https://127.0.0.1/s/doc"),
    ("nextcloud", "https://cloud.example.com/s/doc%0aInjected"),
    ("nextcloud", "https://cloud.example.com:8443/s/doc"),
])
def test_document_validation_rejects_spoofing_credentials_and_non_navigation_urls(provider, url):
    with pytest.raises(ValueError): document_url(provider, url)


@pytest.mark.parametrize("provider,url", [
    ("google_drive", "https://docs.google.com/document/d/abc123/edit"),
    ("google_drive", "https://drive.google.com/drive/folders/abc_123"),
    ("sharepoint", "https://team.sharepoint.com/sites/studio/Document.docx"),
    ("nextcloud", "https://cloud.example.com/index.php/s/abc123"),
])
def test_real_provider_link_shapes_are_supported(provider, url):
    assert document_url(provider, url) == url


def test_calendar_normalizes_explicit_dst_offset_and_preserves_all_day_dates(content):
    path = "/channels/room/integrations/calendar"
    response = content.client.post(path, json={"id": "event1", "fields": EVENT}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["starts_at"] == "2026-10-25T00:30:00Z"
    assert response.json()["fields"]["timezone"] == "Europe/Stockholm"
    day = {**EVENT, "all_day": True, "starts_at": "2026-10-25", "ends_at": "2026-10-26"}
    assert content.client.post(path, json={"id": "event2", "fields": day}, headers=headers()).json()["fields"]["ends_at"] == "2026-10-26"
    for bad in ({"ends_at": EVENT["starts_at"]}, {"starts_at": "2026-10-25T02:30:00"}, {"timezone": "Bad/Zone"}, {"external_uid": "uid\r\nATTENDEE:mailto:evil@example.com"}):
        assert content.client.post(path, json={"id": "bad", "fields": {**EVENT, **bad}}, headers=headers()).status_code == 422


def test_secure_content_only_accepts_verified_opaque_events_even_while_preparing(content, monkeypatch):
    with Session(content.engine) as session:
        session.add(SecureChannel(channel_id="room", created_by="owner", status="preparing"))
        session.commit()
    path = "/channels/room/integrations/documents"
    assert content.client.post(path, json={"id": "private", "fields": DOC}, headers=headers()).status_code == 409
    def validate(session, channel, event, user):
        assert channel == "room" and user == "member"
        if event != "$owned": raise HTTPException(403, "Wrong sender or room")
    monkeypatch.setattr(integrations_content, "validate_secure_event", validate)
    assert content.client.post(path, json={"id": "private", "matrix_event_id": "$foreign"}, headers=headers()).status_code == 403
    response = content.client.post(path, json={"id": "private", "matrix_event_id": "$owned"}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["matrix_event_id"] == "$owned" and "fields" not in response.json()
    assert content.client.get(path, headers=headers()).json()[0]["matrix_event_id"] == "$owned"
    assert content.client.post(path, json={"id": "other", "fields": DOC, "matrix_event_id": "$owned"}, headers=headers()).status_code == 422
    with Session(content.engine) as session:
        stored = session.exec(select(IntegrationContentRevision)).one()
        assert stored.encrypted_payload is None


def test_native_channel_rejects_secure_event_and_deleted_plaintext(content):
    path = "/channels/room/integrations/calendar"
    assert content.client.post(path, json={"id": "event", "matrix_event_id": "$opaque"}, headers=headers()).status_code == 409
    assert content.client.post(path, json={"id": "event", "fields": EVENT, "deleted": True}, headers=headers()).status_code == 422


def test_secure_enable_cannot_relabel_native_document_or_calendar_history(content, monkeypatch):
    monkeypatch.setattr(secure_messaging, "require_matrix", lambda: None)
    for kind, fields in (("documents", DOC), ("calendar", EVENT)):
        path = "/channels/room/integrations/" + kind
        assert content.client.post(path, json={"id": "item-" + kind, "fields": fields}, headers=headers()).status_code == 200
    # Even removing visible items retains immutable native revisions.
    for kind in ("documents", "calendar"):
        path = "/channels/room/integrations/" + kind
        assert content.client.post(path, json={"id": "item-" + kind, "expected_revision": 1, "deleted": True}, headers=headers()).status_code == 200
    assert content.client.post("/secure/channels/room/enable", headers=headers("owner")).status_code == 409


def test_calendar_model_rejects_nonexistent_dates_and_unsupported_reminder():
    for bad in ({"all_day": True, "starts_at": "2026-02-30", "ends_at": "2026-03-01"}, {"reminder_minutes": 10081}):
        with pytest.raises(ValueError): CalendarFields.model_validate({**EVENT, **bad})
