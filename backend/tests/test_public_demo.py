"""A real disposable product workspace, not an alternate demonstration API."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event, func
from sqlmodel import Session, SQLModel, create_engine, select

from app import database
from app.config import settings
from app.database import get_session
from app import main as _register_product_models  # noqa: F401
from app.api import (auth, channels, messages, threads, reactions, read_receipts, workspaces,
    shared_documents, document_organization, project_tasks, activity_feed, decisions,
    integrations_content, files, file_experience, preferences, public_demo)
from app.models.public_demo import PublicDemoVisit
from app.models.message import Message
from app.models.session import ActiveSession, RevokedToken
from app.models.avatar import UserAvatar
from app.models.user import User
from app.models.workspace import Workspace
from app.services import public_demo as service
from app.services.auth import active_user_for_token, decode_token_claims, verify_password
from app.services.storage import StorageService


@pytest.fixture
def sample(tmp_path, monkeypatch):
    root = tmp_path / "public-demo"
    root.mkdir()
    values = {
        "demo_instance": True, "demo_data_path": str(root), "environment": "test",
        "database_url": "sqlite:///" + str(root / "public-demo.sqlite"),
        "storage_path": str(root / "files"), "storage_backend": "local",
        "message_encryption_key_file": str(root / "message-keys.json"),
        "account_security_key_file": str(root / "account-security.key"),
        "message_encryption_keys": {"sample": SecretStr(base64.b64encode(b"M" * 32).decode())},
        "message_encryption_active_key_id": "sample",
        "account_security_key": SecretStr(base64.b64encode(b"A" * 32).decode()),
        "jwt_secret": "synthetic-public-demo-signing-material-32bytes",
        "demo_max_visits": 40, "demo_session_minutes": 60,
        "mail_delivery_mode": "disabled", "redis_url": None,
        "matrix_enabled": False, "connect_federation_enabled": False, "oidc_enabled": False,
        "webhooks_enabled": False, "sync_enabled": False, "otrust_enabled": False,
        "cloud_files_enabled": False, "external_meetings_enabled": False, "copilot_enabled": False,
        "push_enabled": False, "native_push_enabled": False, "native_calls_enabled": False,
        "enable_experimental_features": False, "enterprise_search_enabled": False,
        "data_controls_enabled": False, "workflows_enabled": True,
        "meetings_sfu_provider": "disabled", "meetings_ice_servers": [], "meetings_turn_urls": [],
    }
    for name, value in values.items(): monkeypatch.setattr(settings, name, value)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 20})
    @event.listens_for(engine, "connect")
    def foreign_keys(dbapi, _): dbapi.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    storage = StorageService(settings.storage_path)
    monkeypatch.setattr("app.services.storage.storage_service", storage)
    monkeypatch.setattr(files, "storage_service", storage)
    monkeypatch.setattr(file_experience, "storage_service", storage)
    app = FastAPI()
    for route in (public_demo, auth, channels, messages, threads, reactions, read_receipts, workspaces,
                  shared_documents, document_organization, project_tasks, activity_feed, decisions,
                  integrations_content, files, file_experience, preferences):
        app.include_router(route.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, root=root, storage=storage)
    engine.dispose()


def begin(sample):
    response = sample.client.post("/demo/session", json={})
    assert response.status_code == 201, response.text
    return response.json()


def headers(visit): return {"Authorization": "Bearer " + visit["session"]["access_token"]}


def test_bootstrap_issues_unique_normal_tracked_sessions_and_no_shared_password(sample):
    first, other = begin(sample), begin(sample)
    assert first["workspace_id"] != other["workspace_id"]
    assert first["session"]["user_id"] != other["session"]["user_id"]
    assert first["session"]["public_key"] == "" and first["session"]["encrypted_private_key"] is None
    token = first["session"]["access_token"]
    claims = decode_token_claims(token)
    assert claims["demo_visit_id"] == first["visit_id"] and claims["amr"] == []
    with Session(sample.engine) as session:
        visit = session.get(PublicDemoVisit, first["visit_id"])
        tracked = session.exec(select(ActiveSession).where(ActiveSession.user_id == visit.user_id)).one()
        assert tracked.expires_at == visit.expires_at == datetime.utcfromtimestamp(claims["exp"])
        assert active_user_for_token(token, session).id == visit.user_id
        users = [session.get(User, identifier) for identifier in json.loads(visit.participant_ids_json)]
        assert len(users) == 4 and all(user.role == "user" for user in users)
        assert all(not verify_password("any-shared-demo-password", user.password_hash) for user in users)
        assert session.exec(select(func.count()).select_from(ActiveSession)).one() == 2
    for visit in (first, other):
        workspaces_list = sample.client.get("/workspaces", headers=headers(visit)).json()
        assert [row["id"] for row in workspaces_list] == [visit["workspace_id"]]
        me = sample.client.get("/auth/me", headers=headers(visit)).json()
        assert me["id"] == visit["session"]["user_id"] and me["role"] == "user"


def test_seeded_content_uses_real_readers_and_writes_with_cross_visit_isolation(sample):
    one, two = begin(sample), begin(sample)
    h, channel = headers(one), one["channel_id"]
    other = headers(two)
    records = sample.client.get("/channels?workspace_id=" + one["workspace_id"], headers=h).json()
    assert {row["name"] for row in records} == {"Courtyard", "Design", "Planning"}
    timeline = sample.client.get(f"/messages/channels/{channel}?roots_only=true", headers=h).json()
    assert len(timeline) == 6 and "Shall we open up to the courtyard?" == timeline[-1]["content"]
    assert sample.client.get(f"/messages/channels/{channel}", headers=other).status_code == 403
    sent = sample.client.post(f"/messages/channels/{channel}", json={"content": "An actual product message."}, headers=h)
    assert sent.status_code == 200, sent.text
    assert sent.json()["content"] == "An actual product message."
    for path in (f"/channels/{channel}/shared-documents", f"/channels/{channel}/projects",
                 f"/channels/{channel}/activity-feed", f"/channels/{channel}/decisions",
                 f"/channels/{channel}/integrations/calendar", f"/files/channels/{channel}"):
        result = sample.client.get(path, headers=h)
        assert result.status_code == 200, (path, result.text)
        assert result.json(), path
        assert sample.client.get(path, headers=other).status_code == 403, path
    docs = sample.client.get(f"/channels/{channel}/shared-documents", headers=h).json()
    assert {row["title"] for row in docs} == {"Project brief", "Studio notes", "Materials & sketches"}
    org = sample.client.get(f"/channels/{channel}/shared-document-organization", headers=h).json()
    assert org["stacks"][0]["title"] == "Reference material" and len(org["placements"]) == 2
    assert org["relations"] and org["message_references"] and org["stack_comments"]
    project = sample.client.get(f"/channels/{channel}/projects", headers=h).json()[0]
    tasks = sample.client.get(f"/channels/{channel}/projects/{project['id']}/tasks", headers=h).json()
    assert project["fields"]["title"] == "Courtyard project" and len(tasks["items"]) == 3
    files_list = sample.client.get(f"/files/channels/{channel}", headers=h).json()
    for file in files_list:
        downloaded = sample.client.get("/files/" + file["id"], headers=h)
        assert downloaded.status_code == 200 and len(downloaded.content) == file["size"]
        assert sample.client.get("/files/" + file["id"], headers=other).status_code == 403


def test_reset_revokes_only_own_normal_rest_and_socket_authority(sample):
    first, survivor = begin(sample), begin(sample)
    result = sample.client.delete("/demo/session", headers=headers(first))
    assert result.status_code == 204
    assert sample.client.get("/auth/me", headers=headers(first)).status_code == 401
    assert sample.client.get("/auth/me", headers=headers(survivor)).status_code == 200
    with Session(sample.engine) as session:
        assert active_user_for_token(first["session"]["access_token"], session) is None
        assert active_user_for_token(survivor["session"]["access_token"], session) is not None


def test_expiry_and_physical_cleanup_preserve_another_visits_rows_and_files(sample):
    old, survivor = begin(sample), begin(sample)
    h = headers(survivor)
    with Session(sample.engine) as session:
        visit = session.get(PublicDemoVisit, old["visit_id"])
        old_users = json.loads(visit.participant_ids_json)
        visit.expires_at = datetime.utcnow() - timedelta(minutes=1)
        session.add(visit); session.commit()
        with pytest.raises(HTTPException) as ended:
            service.get_active_demo_visit(session, visit.user_id, visit.id)
        assert ended.value.status_code == 401
    with Session(sample.engine) as session:
        assert service.cleanup_expired_visits(session) == 1
        assert session.get(PublicDemoVisit, old["visit_id"]) is None
        assert session.get(Workspace, old["workspace_id"]) is None
        assert all(session.get(User, identifier) is None for identifier in old_users)
        assert session.get(PublicDemoVisit, survivor["visit_id"]) is not None
    remaining = sample.client.get(f"/files/channels/{survivor['channel_id']}", headers=h).json()
    with Session(sample.engine) as session:
        avatars = session.exec(select(UserAvatar)).all()
        assert len(avatars) == 4 and not any(row.user_id in old_users for row in avatars)
    assert {path.name for path in (sample.root / "files").iterdir()} == {file["id"] for file in remaining} | {row.blob_id for row in avatars}
    assert sample.client.get(f"/messages/channels/{survivor['channel_id']}", headers=h).status_code == 200


def test_expiry_cleans_new_calendar_collections_semantic_and_capture_state(sample):
    from app.api import content_completion, meetings, media_capture
    from app.models.content_completion import PersonalCalendarEvent, ChannelSemanticIndex, ContentCollectionItem
    from app.models.media_capture import MeetingCapture, MeetingCaptureConsent
    for module in (content_completion, meetings, media_capture): sample.client.app.include_router(module.router)
    old, survivor = begin(sample), begin(sample)
    first, other = headers(old), headers(survivor)
    fields = {"title": "Visit-only appointment", "description": "Temporary personal notes", "location": "",
        "starts_at": "2026-11-02T09:00:00Z", "ends_at": "2026-11-02T10:00:00Z", "all_day": False,
        "timezone": "UTC", "reminder_minutes": None, "external_uid": None}
    for visit, auth in ((old, first), (survivor, other)):
        assert sample.client.post(f"/workspaces/{visit['workspace_id']}/personal-calendar", headers=auth,
            json={"id": "event-" + visit["visit_id"], "fields": fields}).status_code == 200
    sources = sample.client.get(f"/channels/{old['channel_id']}/shared-documents", headers=first).json()
    collection = sample.client.post(f"/channels/{old['channel_id']}/content-collections", headers=first,
        json={"id": "visit-material", "title": "Temporary collection", "items": [{"kind": "document", "id": sources[0]["id"]}]})
    assert collection.status_code == 201, collection.text
    search = sample.client.post(f"/workspaces/{old['workspace_id']}/semantic-search", headers=first, json={"query": "courtyard"})
    assert search.status_code == 200, search.text
    joined = sample.client.post(f"/channels/{old['channel_id']}/meetings", headers=first, json={})
    assert joined.status_code == 201, joined.text
    capture = sample.client.post(f"/meetings/mesh/{joined.json()['meeting']['id']}/capture", headers=first, json={})
    assert capture.status_code == 201, capture.text
    with Session(sample.engine) as session:
        visit = session.get(PublicDemoVisit, old["visit_id"]); visit.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(visit); session.commit()
    with Session(sample.engine) as session:
        assert service.cleanup_expired_visits(session) == 1
        assert not session.exec(select(PersonalCalendarEvent).where(PersonalCalendarEvent.workspace_id == old["workspace_id"])).all()
        assert not session.exec(select(ChannelSemanticIndex).where(ChannelSemanticIndex.channel_id == old["channel_id"])).all()
        assert not session.exec(select(ContentCollectionItem).where(ContentCollectionItem.channel_id == old["channel_id"])).all()
        assert not session.exec(select(MeetingCapture).where(MeetingCapture.channel_id == old["channel_id"])).all()
        assert not session.exec(select(MeetingCaptureConsent)).all()
    assert sample.client.get(f"/workspaces/{survivor['workspace_id']}/personal-calendar", headers=other).json()[0]["fields"]["title"] == fields["title"]


def test_capacity_is_atomic_and_revoked_visits_are_reclaimed(sample, monkeypatch):
    monkeypatch.setattr(settings, "demo_max_visits", 2)
    def create():
        with Session(sample.engine) as session:
            try: return service.create_demo_session(session)
            except HTTPException as failure: return failure.status_code
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: create(), range(3)))
    assert sum(isinstance(result, dict) for result in results) == 2 and results.count(429) == 1
    victim = next(result for result in results if isinstance(result, dict))
    assert sample.client.delete("/demo/session", headers=headers(victim)).status_code == 204
    assert sample.client.post("/demo/session", json={}).status_code == 201
    with Session(sample.engine) as session:
        assert session.get(PublicDemoVisit, victim["visit_id"]) is None
        assert session.exec(select(func.count()).select_from(PublicDemoVisit)).one() == 2


def test_normal_product_logout_is_reclaimed_without_waiting_for_expiry(sample):
    old, survivor = begin(sample), begin(sample)
    assert sample.client.post("/auth/logout", headers=headers(old)).status_code == 200
    with Session(sample.engine) as session:
        assert service.cleanup_expired_visits(session) == 1
        assert session.get(PublicDemoVisit, old["visit_id"]) is None
        assert session.get(PublicDemoVisit, survivor["visit_id"]) is not None
        assert session.exec(select(func.count()).select_from(RevokedToken)).one() == 0


def test_orphan_collection_never_deletes_an_inflight_normal_upload(sample):
    visit = begin(sample)
    orphan = "a" * 32
    sample.storage.save_file_sync(orphan, b"normal upload bytes before metadata commits")
    with Session(sample.engine) as session: service.cleanup_expired_visits(session)
    file = sample.root / "files" / orphan
    assert file.exists()
    os.utime(file, (time.time() - 700, time.time() - 700))
    with Session(sample.engine) as session: service.cleanup_expired_visits(session)
    assert not file.exists()
    assert len(list((sample.root / "files").iterdir())) == 7


def test_cleanup_deletes_large_threads_leaf_first_across_bounded_fk_batches(sample):
    old, survivor = begin(sample), begin(sample)
    channel, user_id = old["channel_id"], old["session"]["user_id"]
    with Session(sample.engine) as session:
        root = session.exec(select(Message).where(Message.channel_id == channel, Message.parent_id.is_(None))).first()
        for number in range(150):
            session.add(Message(id="reply" + str(number).zfill(5), channel_id=channel,
                                author_id=user_id, parent_id=root.id, content="example reply", encrypted=False))
        session.commit()
    assert sample.client.delete("/demo/session", headers=headers(old)).status_code == 204
    with Session(sample.engine) as session:
        assert service.cleanup_expired_visits(session) == 1
        assert session.exec(select(func.count()).select_from(Message).where(Message.channel_id == channel)).one() == 0
        assert session.get(PublicDemoVisit, survivor["visit_id"]) is not None
    assert sample.client.get("/messages/channels/" + survivor["channel_id"], headers=headers(survivor)).status_code == 200


def test_revoke_requires_the_signed_visit_claim_even_if_a_router_is_mounted_without_boundary(sample):
    visit = begin(sample)
    with Session(sample.engine) as session:
        with pytest.raises(HTTPException) as failure:
            service.revoke_demo_session(session, visit["session"]["user_id"], None)
        assert failure.value.status_code == 401
        assert service.get_active_demo_visit(session, visit["session"]["user_id"]).revoked_at is None


@pytest.mark.parametrize("enabled", [False, True])
def test_fresh_actual_application_registers_only_the_dedicated_demo_surface(sample, enabled):
    env = os.environ.copy()
    env.update(ENVIRONMENT="test", DEMO_INSTANCE=str(enabled).lower(),
        DEMO_DATA_PATH=str(sample.root), DATABASE_URL=settings.database_url,
        STORAGE_PATH=settings.storage_path, MESSAGE_ENCRYPTION_KEY_FILE=settings.message_encryption_key_file,
        ACCOUNT_SECURITY_KEY_FILE=settings.account_security_key_file,
        MAIL_DELIVERY_MODE="disabled", ENTERPRISE_SEARCH_ENABLED="false", DATA_CONTROLS_ENABLED="false",
        JWT_SECRET=settings.jwt_secret, MESSAGE_ENCRYPTION_ACTIVE_KEY_ID="sample",
        MESSAGE_ENCRYPTION_KEYS=json.dumps({"sample": base64.b64encode(b"M" * 32).decode()}),
        ACCOUNT_SECURITY_KEY=base64.b64encode(b"A" * 32).decode())
    script = """
import json
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    capability = client.get('/capabilities').json()
    response = client.post('/demo/session', json={})
    if capability['demo_instance']:
        assert response.status_code == 201, response.text
        data=response.json(); token={'Authorization':'Bearer '+data['session']['access_token']}
        records=client.get('/channels?workspace_id='+data['workspace_id'],headers=token)
        assert records.status_code == 200 and len(records.json()) == 3,records.text
        assert client.post('/auth/register',json={}).status_code == 403
        assert client.get('/admin/users',headers=token).status_code == 403
    else:
        assert response.status_code == 404
print('actual application and normal channel API passed')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_demo_off_and_wrong_engine_fail_before_touching_data(sample, monkeypatch):
    wrong = create_engine("sqlite://")
    with Session(wrong) as session:
        with pytest.raises(HTTPException) as denied: service.create_demo_session(session)
        assert denied.value.status_code == 503
    wrong.dispose()
    monkeypatch.setattr(settings, "demo_instance", False)
    assert sample.client.post("/demo/session", json={}).status_code == 404
    with Session(sample.engine) as session:
        assert session.exec(select(func.count()).select_from(User)).one() == 0


@pytest.mark.parametrize("change", [
    {"database_url": "sqlite:////tmp/operator.sqlite"}, {"storage_path": "/tmp/operator-files"},
    {"mail_delivery_mode": "smtp"}, {"matrix_enabled": True}, {"copilot_enabled": True},
    {"oidc_enabled": True}, {"data_controls_enabled": True},
])
def test_dedicated_instance_configuration_rejects_operator_resources(sample, monkeypatch, change):
    for name, value in change.items(): monkeypatch.setattr(settings, name, value)
    with pytest.raises((ValueError, RuntimeError)):
        service.require_demo_instance()
