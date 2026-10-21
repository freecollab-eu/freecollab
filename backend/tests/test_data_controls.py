"""Private owner exports and explicit retention erase real rows and file bytes."""
import base64
from datetime import datetime, timedelta
import importlib
import io
import json
from pathlib import Path
import pkgutil
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import Column, ForeignKey, String, Table, event, insert
from sqlmodel import Session, SQLModel, create_engine, select

import app.models
for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module("app.models." + module.name)

from app.api import auth, data_control
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import Decision, DecisionRevision, MessageChange, ThreadSubscription
from app.models.data_control import AccountIdentityErasure, DataControlJob, WorkspaceRetentionPolicy
from app.models.file import File
from app.models.file_version import FileDocument, FileVersion
from app.models.message import Message
from app.models.push import OfflineMessageReceipt
from app.models.reaction import Reaction
from app.models.read_receipt import ReadReceipt
from app.models.secure_channel import SecureChannel
from app.models.user import User
from app.models.workspace import PersonalWorkspace, Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import data_control as service
from app.services.auth import create_token, hash_password, token_digest
from app.services.crypto import encrypt_message

PASSWORD = "Private-data-controls!2026"
FILE_ID = "d" * 32
OLD = datetime.utcnow() - timedelta(days=30)


@pytest.fixture
def controls(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "account_security_key", SecretStr(base64.b64encode(b"\x9b" * 32).decode()))
    monkeypatch.setattr(settings, "message_encryption_keys", {"data-test": SecretStr(base64.b64encode(b"\x9c" * 32).decode())})
    monkeypatch.setattr(settings, "message_encryption_active_key_id", "data-test")
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "files"))
    monkeypatch.setattr(settings, "matrix_enabled", False)
    monkeypatch.setattr(settings, "mail_delivery_mode", "disabled")
    Path(settings.storage_path).mkdir()
    engine = create_engine("sqlite:///" + str(tmp_path / "data.db"), connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _): connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ("owner", "peer", "member", "outsider"):
            session.add(User(id=name, name="Private " + name, email=name + "@example.com", password_hash=hash_password(PASSWORD)))
        session.add_all([Workspace(id="a", name="Private team"), Workspace(id="b", name="Other team")])
        session.commit()
        session.add_all([WorkspaceMember(workspace_id="a", user_id="owner", role="owner"), WorkspaceMember(workspace_id="a", user_id="peer", role="owner"),
            WorkspaceMember(workspace_id="a", user_id="member"), WorkspaceMember(workspace_id="b", user_id="outsider", role="owner")])
        for name, workspace in (("native", "a"), ("hidden", "a"), ("secure", "a"), ("other", "b")):
            session.add(Channel(id=name, name="Private " + name, type="group"))
            session.flush()
            session.add(WorkspaceChannel(channel_id=name, workspace_id=workspace))
        session.add_all([ChannelMember(channel_id="native", user_id="owner", role="admin"), ChannelMember(channel_id="native", user_id="peer", role="admin"),
            ChannelMember(channel_id="native", user_id="member"), ChannelMember(channel_id="secure", user_id="owner", role="admin"),
            ChannelMember(channel_id="secure", user_id="peer", role="admin"), ChannelMember(channel_id="hidden", user_id="peer", role="admin"),
            ChannelMember(channel_id="other", user_id="outsider", role="admin")])
        session.add(SecureChannel(channel_id="secure", matrix_room_id="!private:test", created_by="owner", status="active"))
        session.add(WorkspaceOIDCProvider(workspace_id="a", name="Private customer", issuer_url="https://identity.example", client_id="private-client", created_by="owner", enabled=True))
        session.commit()
        session.add_all([Message(id="old-root", channel_id="native", author_id="owner", content=encrypt_message("OLD PRIVATE CONTENT", "native"), created_at=OLD, thread_count=1),
            Message(id="fresh", channel_id="native", author_id="member", content=encrypt_message("FRESH PRIVATE CONTENT", "native")),
            Message(id="hidden-message", channel_id="hidden", author_id="peer", content=encrypt_message("HIDDEN MUST STAY PRIVATE", "hidden"), created_at=OLD)])
        session.flush()
        session.add(Message(id="old-reply", channel_id="native", author_id="member", content=encrypt_message("OLD REPLY", "native"), parent_id="old-root", created_at=OLD))
        session.add_all([Reaction(id="reaction", message_id="old-root", user_id="member", emoji="ok"),
            ReadReceipt(channel_id="native", user_id="member", last_read_message_id="old-root"),
            ThreadSubscription(user_id="member", message_id="old-root"), MessageChange(channel_id="native", message_id="old-root", kind="edit")])
        session.add(OfflineMessageReceipt(user_id="owner", client_id="offline-retry", channel_id="native", message_id="old-root", payload_digest="private-digest"))
        session.add(File(id=FILE_ID, channel_id="native", uploader_id="owner", filename="private.txt", content_type="text/plain", size=12,
                         storage_path="NEVER-EXPORT-SERVER-PATH", created_at=OLD))
        session.flush()
        session.add(FileDocument(id=FILE_ID, channel_id="native", owner_id="owner", current_file_id=FILE_ID, created_at=OLD))
        session.flush()
        session.add(FileVersion(file_id=FILE_ID, document_id=FILE_ID, number=1))
        session.add(Decision(id="decision", channel_id="native", created_by="owner", created_at=OLD))
        session.flush()
        session.add(DecisionRevision(id="decision-rev", decision_id="decision", revision=1, created_by="owner", created_at=OLD,
            encrypted_payload=encrypt_message(json.dumps({"decision_id": "decision", "revision": 1, "title": "Private decision", "content": "Decision text", "status": "accepted", "source_message_id": None}), "native")))
        session.commit()
        tokens = {name: create_token(name, session) for name in ("owner", "peer", "member", "outsider")}
        tokens["scoped"] = create_token("owner", session, auth_methods=["sso"], workspace_scope={"workspace_id": "a", "provider_revision": 1})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(tokens["scoped"]), workspace_id="a", provider_revision=1)); session.commit()
    (Path(settings.storage_path) / FILE_ID).write_bytes(b"PRIVATE FILE")
    app = FastAPI()
    app.include_router(data_control.router); app.include_router(data_control.account_router); app.include_router(auth.router)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, engine, tokens, tmp_path
    engine.dispose()


def headers(controls, name="owner"):
    return {"Authorization": "Bearer " + controls[2][name]}


def post(controls, path, body=None, name="owner"):
    return controls[0].post("/workspaces/a/data-controls" + path, headers=headers(controls, name), json={"password": PASSWORD, **(body or {})})


def enable(controls, *, hold=False):
    client = controls[0]
    current = client.get("/workspaces/a/data-controls", headers=headers(controls)).json()["policy"]
    response = client.put("/workspaces/a/data-controls/policy", headers=headers(controls), json={"password": PASSWORD,
        "enabled": True, "hold": hold, "retention_days": 7, "expected_revision": current["revision"]})
    assert response.status_code == 200


def preview(controls):
    result = post(controls, "/previews", {"channel_ids": ["native"]})
    assert result.status_code == 200, result.text
    return result.json()


def queue_purge(controls, dry):
    return post(controls, "/purges/" + dry["id"], {"confirmation": dry["confirmation"], "acknowledge_backups_and_provider_copies": True})


@pytest.mark.parametrize("identity", ["member", "outsider", "scoped"])
def test_data_controls_are_native_owner_only(controls, identity):
    client = controls[0]
    assert client.get("/workspaces/a/data-controls", headers=headers(controls, identity)).status_code == 403
    assert post(controls, "/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True}, name=identity).status_code == 403


def test_default_hold_no_private_channel_enumeration_and_wrong_password_retains_session(controls):
    client, engine, _, _ = controls
    state = client.get("/workspaces/a/data-controls", headers=headers(controls))
    assert state.status_code == 200 and state.headers["cache-control"] == "private, no-store"
    assert state.json()["policy"]["hold"] and not state.json()["policy"]["enabled"]
    assert {row["id"] for row in state.json()["channels"]} == {"native", "secure"}
    assert "hidden" not in state.text
    assert post(controls, "/exports", {"password": "wrong", "channel_ids": ["native"], "acknowledge_plaintext_export": True}).status_code == 403
    assert client.get("/auth/me", headers=headers(controls)).status_code == 200
    assert post(controls, "/exports", {"channel_ids": ["hidden"], "acknowledge_plaintext_export": True}).status_code == 403
    with Session(engine) as session: assert not session.exec(select(DataControlJob)).all()


def test_actual_export_is_private_encrypted_at_rest_and_download_exact(controls):
    client, engine, _, _ = controls
    queued = post(controls, "/exports", {"channel_ids": ["native", "secure"], "acknowledge_plaintext_export": True})
    assert queued.status_code == 202
    identifier = queued.json()["id"]
    with Session(engine) as session: assert service.run_pending(session) == 1
    artifact = service._artifact_path(identifier).read_bytes()
    assert b"OLD PRIVATE CONTENT" not in artifact and b"PRIVATE FILE" not in artifact and artifact.startswith(service.MAGIC)
    assert post(controls, "/exports/" + identifier + "/download", name="peer").status_code == 404
    downloaded = post(controls, "/exports/" + identifier + "/download")
    assert downloaded.status_code == 200 and downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert archive.read("files/" + FILE_ID) == b"PRIVATE FILE"
        data = json.loads(archive.read("export.json"))
        assert any(row["content"] == "OLD PRIVATE CONTENT" for row in data["message"])
        assert not any(row.get("channel_id") == "hidden" for values in data.values() if isinstance(values, list) for row in values if isinstance(row, dict))
        text = json.dumps(data)
        for secret in (PASSWORD, "password_hash", "encrypted_private_key", "NEVER-EXPORT-SERVER-PATH", "private-client", controls[2]["owner"], "HIDDEN MUST STAY PRIVATE"):
            assert secret not in text
        assert next(row for row in data["channel"] if row["id"] == "secure")["name"] is None
    assert not list(service._archive_root().glob("download-*.zip"))


def test_export_stops_after_membership_or_session_revocation(controls):
    client, engine, _, _ = controls
    queued = post(controls, "/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True}).json()
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ("native", "owner"))); session.commit()
        assert service.run_pending(session) == 0
        assert session.get(DataControlJob, queued["id"]).status == "failed"
    assert not service._artifact_path(queued["id"]).exists()


def test_preview_hold_confirmation_one_use_and_actual_fk_safe_file_erasure(controls):
    client, engine, _, _ = controls
    assert not preview(controls)["counts"]  # Default keeps 365 days.
    enable(controls, hold=True)
    dry = preview(controls)
    assert dry["counts"]["message"] == 2 and dry["counts"]["file"] == 1
    assert queue_purge(controls, dry).status_code == 409
    enable(controls)
    dry = preview(controls)
    assert post(controls, "/purges/" + dry["id"], {"confirmation": "PURGE wrong", "acknowledge_backups_and_provider_copies": True}).status_code == 400
    queued = queue_purge(controls, dry)
    assert queued.status_code == 202
    assert queue_purge(controls, dry).status_code == 409
    with Session(engine) as session:
        assert service.run_pending(session) == 1
        assert session.get(DataControlJob, queued.json()["id"]).status == "completed"
        assert session.get(Message, "old-root") is None and session.get(Message, "old-reply") is None
        assert session.get(Message, "fresh") is not None and session.get(Message, "hidden-message") is not None
        assert session.get(File, FILE_ID) is None and session.get(FileDocument, FILE_ID) is None
        assert not session.exec(select(Reaction)).all()
        assert not session.exec(select(ReadReceipt)).all()
        assert session.get(OfflineMessageReceipt, ("owner", "offline-retry")).message_id is None
    assert not (Path(settings.storage_path) / FILE_ID).exists()


def test_new_thread_reply_and_retained_decision_source_protect_older_content(controls):
    _, engine, _, _ = controls
    with Session(engine) as session:
        session.add(Message(id="new-reply", channel_id="native", author_id="member", parent_id="old-root", content=encrypt_message("new", "native")))
        session.commit()
    enable(controls)
    dry = preview(controls)
    assert dry["counts"]["message"] == 1  # old reply only; root survives new reply
    queued = queue_purge(controls, dry)
    with Session(engine) as session:
        assert service.run_pending(session) == 1
        assert session.get(Message, "old-root") is not None
        assert session.get(Message, "old-root").thread_count == 1


def test_changed_content_policy_and_secure_channel_fail_closed(controls):
    _, engine, _, _ = controls
    enable(controls)
    dry = preview(controls)
    with Session(engine) as session:
        message = session.get(Message, "old-root"); message.content = encrypt_message("Changed content", "native"); session.add(message); session.commit()
    queued = queue_purge(controls, dry)
    assert queued.status_code == 202
    with Session(engine) as session:
        assert service.run_pending(session) == 0
        assert session.get(DataControlJob, queued.json()["id"]).status == "failed"
        assert session.get(Message, "old-root") is not None
    assert post(controls, "/previews", {"channel_ids": ["secure"]}).status_code == 409


def test_account_identity_erasure_preserves_shared_authorship_and_revokes_old_session(controls, monkeypatch):
    client, engine, _, _ = controls
    # Owner has a successor in every workspace/channel; remote revocation is a
    # stub only because this test installation has no homeserver enabled.
    monkeypatch.setattr("app.services.matrix_bridge.remove_secure_member", lambda *_: None)
    dry = client.get("/account/data-closure", headers=headers(controls)).json()
    assert dry["can_erase_identity"]
    result = client.post("/account/data-closure", headers=headers(controls), json={"password": PASSWORD,
        "confirmation": "ERASE MY ACCOUNT", "preview_fingerprint": dry["fingerprint"],
        "acknowledge_retained_shared_authorship_and_backups": True})
    assert result.status_code == 200, result.text
    assert result.json()["identity_erased"] and not result.json()["shared_content_and_backups_erased"]
    assert client.get("/auth/me", headers=headers(controls)).status_code == 401
    with Session(engine) as session:
        actor = session.get(User, "owner")
        assert actor.is_blocked and actor.name == "Deleted account" and actor.email.endswith("@identity.invalid")
        assert actor.public_key is None and actor.encrypted_private_key is None
        assert session.get(Message, "old-root").author_id == "owner"
        assert session.get(AccountIdentityErasure, "owner")
        assert not session.exec(select(WorkspaceMember).where(WorkspaceMember.user_id == "owner")).all()


def test_last_owner_and_remote_revocation_failure_never_erase_identity(controls, monkeypatch):
    client, engine, _, _ = controls
    with Session(engine) as session:
        member = session.get(WorkspaceMember, ("a", "peer")); member.role = "member"; session.add(member); session.commit()
    dry = client.get("/account/data-closure", headers=headers(controls)).json()
    assert not dry["can_erase_identity"] and dry["blocking_workspaces"] == ["a"]
    result = client.post("/account/data-closure", headers=headers(controls), json={"password": PASSWORD,
        "confirmation": "ERASE MY ACCOUNT", "preview_fingerprint": dry["fingerprint"],
        "acknowledge_retained_shared_authorship_and_backups": True})
    assert result.status_code == 409
    with Session(engine) as session:
        assert session.get(User, "owner").email == "owner@example.com"


def test_corrupt_private_archive_cannot_publish_partial_plaintext(controls):
    _, engine, _, _ = controls
    queued = post(controls, "/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True}).json()
    with Session(engine) as session: assert service.run_pending(session) == 1
    path = service._artifact_path(queued["id"]); content = bytearray(path.read_bytes()); content[-1] ^= 1; path.write_bytes(content)
    result = post(controls, "/exports/" + queued["id"] + "/download")
    assert result.status_code == 409
    assert not list(service._archive_root().glob("download-*.zip"))


def test_physical_file_cleanup_retries_after_committed_metadata_erasure(controls, monkeypatch):
    _, engine, _, _ = controls
    enable(controls)
    queued = queue_purge(controls, preview(controls)).json()
    original = Path.unlink
    def fail_only_fixture_blob(path, *args, **kwargs):
        if path.name == FILE_ID: raise PermissionError('private fixture unlink denied')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(Path, 'unlink', fail_only_fixture_blob)
        with Session(engine) as session:
            assert service.run_pending(session) == 0
            assert session.get(Message, 'old-root') is None
            assert session.get(File, FILE_ID) is None
            assert session.get(DataControlJob, queued['id']).status == 'erasing_files'
    assert (Path(settings.storage_path) / FILE_ID).exists()
    monkeypatch.setattr(settings, 'data_controls_enabled', False)
    with Session(engine) as session:
        row = session.get(DataControlJob, queued['id']); row.lease_expires_at = OLD; session.add(row); session.commit()
        assert service.run_pending(session) == 1
        assert session.get(DataControlJob, queued['id']).status == 'completed'
    assert not (Path(settings.storage_path) / FILE_ID).exists()


def test_completed_export_rechecks_session_and_expires_artifact(controls):
    client, engine, tokens, _ = controls
    queued = post(controls, '/exports', {'channel_ids': ['native'], 'acknowledge_plaintext_export': True}).json()
    with Session(engine) as session: assert service.run_pending(session) == 1
    from app.models.session import ActiveSession
    with Session(engine) as session:
        row = session.exec(select(ActiveSession).where(ActiveSession.token_hash == token_digest(tokens['owner']))).one()
        row.revoked_at = datetime.utcnow(); session.add(row); session.commit()
    assert post(controls, '/exports/' + queued['id'] + '/download').status_code == 401
    with Session(engine) as session:
        row = session.get(DataControlJob, queued['id']); row.expires_at = OLD; session.add(row); session.commit()
        assert service.run_pending(session) == 0
        assert session.get(DataControlJob, queued['id']).status == 'expired'
    assert not service._artifact_path(queued['id']).exists()


def test_remote_matrix_revocation_failure_stops_native_identity_erasure(controls, monkeypatch):
    client, engine, _, _ = controls
    from fastapi import HTTPException
    def unavailable(*_): raise HTTPException(503, 'Private fixture remote unavailable')
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member', unavailable)
    dry = client.get('/account/data-closure', headers=headers(controls)).json()
    result = client.post('/account/data-closure', headers=headers(controls), json={'password': PASSWORD,
        'confirmation': 'ERASE MY ACCOUNT', 'preview_fingerprint': dry['fingerprint'],
        'acknowledge_retained_shared_authorship_and_backups': True})
    assert result.status_code == 503
    with Session(engine) as session:
        assert session.get(User, 'owner').email == 'owner@example.com'
        assert session.get(WorkspaceMember, ('a', 'owner'))
        assert not session.get(AccountIdentityErasure, 'owner')
    assert client.get('/auth/me', headers=headers(controls)).status_code == 200


def test_unknown_future_foreign_dependency_blocks_preview_without_cascade(controls):
    _, engine, _, _ = controls
    future = Table('private_future_dependency', SQLModel.metadata,
        Column('id', String, primary_key=True), Column('message_id', String, ForeignKey('message.id')))
    future.create(engine)
    try:
        with engine.begin() as connection: connection.execute(insert(future).values(id='future', message_id='old-root'))
        enable(controls)
        assert post(controls, '/previews', {'channel_ids': ['native']}).status_code == 409
        with Session(engine) as session: assert session.get(Message, 'old-root')
    finally:
        future.drop(engine); SQLModel.metadata.remove(future)


def test_tasks_shared_document_and_terminal_workflow_history_purge_together(controls):
    _, engine, _, _ = controls
    from app.models.project_tasks import ProjectBoard, TaskCard, ProjectTaskRevision
    from app.models.shared_documents import SharedDocument, SharedDocumentUpdate, SharedDocumentComment
    from app.models.workflow import WorkflowDefinition, WorkflowRun, WorkflowVote, WorkflowActivity, WorkflowNotice
    from app.services.shared_documents import seal
    with Session(engine) as session:
        session.add(ProjectBoard(id='project', channel_id='native', created_by='owner', created_at=OLD, updated_at=OLD)); session.flush()
        session.add(TaskCard(id='task', project_id='project', channel_id='native', created_by='owner', created_at=OLD, updated_at=OLD))
        session.add(ProjectTaskRevision(id='task-revision', kind='task', item_id='task', revision=1, created_by='owner', created_at=OLD,
            content_mode='server', encrypted_payload=encrypt_message(json.dumps({'id':'task','kind':'task','revision':1,'fields':{'title':'Private old task'}}), 'native')))
        session.add(SharedDocument(id='shared', channel_id='native', created_by='owner', created_at=OLD, updated_at=OLD)); session.flush()
        session.add(SharedDocumentUpdate(document_id='shared', update_id='update', sequence=1, created_by='owner', created_at=OLD,
            content_mode='server', payload_hash='private-hash', encrypted_payload=seal('native', 'update', 'shared', 'update', {'update_base64':'AAA='})))
        session.add(SharedDocumentComment(id='comment', document_id='shared', created_by='owner', created_at=OLD, content_mode='server',
            payload_hash='comment-hash', encrypted_payload=seal('native', 'comment', 'shared', 'comment', {'text':'Private comment'})))
        session.add(WorkflowDefinition(id='workflow', workspace_id='a', channel_id='native', created_by='owner', granted_by='owner', trigger='decision.accepted', enabled=False)); session.flush()
        session.add(WorkflowRun(id='run', workflow_id='workflow', workspace_id='a', channel_id='native', workflow_revision=1, event_id='event',
            event_kind='decision.accepted', status='completed', created_at=OLD, finished_at=OLD)); session.flush()
        session.add_all([WorkflowVote(run_id='run', step_index=0, user_id='owner', choice='approve'),
            WorkflowActivity(id='activity', run_id='run', kind='completed', step_index=0),
            WorkflowNotice(run_id='run', step_index=0, user_id='owner', kind='approval', notification_id='removed-notice')]); session.commit()
    queued = post(controls, '/exports', {'channel_ids': ['native'], 'acknowledge_plaintext_export': True}).json()
    with Session(engine) as session: assert service.run_pending(session) == 1
    downloaded = post(controls, '/exports/' + queued['id'] + '/download')
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        data = json.loads(archive.read('export.json'))
        assert data['projecttaskrevision'][0]['fields']['fields']['title'] == 'Private old task'
        assert data['shareddocumentcomment'][0]['fields']['text'] == 'Private comment'
        assert data['workflowrun'][0]['status'] == 'completed'
    enable(controls)
    dry = preview(controls)
    assert dry['counts']['taskcard'] == dry['counts']['shareddocument'] == dry['counts']['workflowrun'] == 1
    queued = queue_purge(controls, dry).json()
    with Session(engine) as session:
        assert service.run_pending(session) == 1
        for model, identity in [(ProjectBoard,'project'), (TaskCard,'task'), (SharedDocument,'shared'), (WorkflowRun,'run')]:
            assert session.get(model, identity) is None
        assert session.get(WorkflowDefinition, 'workflow')  # Paused configuration remains.
        assert not session.exec(select(WorkflowVote)).all()
        assert not session.exec(select(WorkflowActivity)).all()
        assert not session.exec(select(WorkflowNotice)).all()


def test_workspace_member_profiles_are_included_in_archive_record_bound(controls, monkeypatch):
    _, engine, _, _ = controls
    with Session(engine) as session:
        password_hash = session.get(User, 'owner').password_hash
        for number in range(45):
            actor = 'bounded-member-' + str(number)
            session.add(User(id=actor, name='Synthetic member', email=actor+'@example.com', password_hash=password_hash))
            session.flush()
            session.add(WorkspaceMember(workspace_id='a', user_id=actor))
        session.commit()
    monkeypatch.setattr(service, 'MAX_ROWS', 40)
    queued = post(controls, '/exports', {'channel_ids':['native'], 'acknowledge_plaintext_export':True}).json()
    with Session(engine) as session:
        assert service.run_pending(session) == 0
        assert session.get(DataControlJob, queued['id']).status == 'failed'
    assert not service._artifact_path(queued['id']).exists()


def test_workspace_feature_flag_stops_queued_metadata_but_keeps_identity_privacy_route(controls, monkeypatch):
    client, engine, _, _ = controls
    queued = post(controls, '/exports', {'channel_ids':['native'], 'acknowledge_plaintext_export':True}).json()
    monkeypatch.setattr(settings, 'data_controls_enabled', False)
    assert client.get('/workspaces/a/data-controls', headers=headers(controls)).status_code == 404
    assert post(controls, '/exports', {'channel_ids':['native'], 'acknowledge_plaintext_export':True}).status_code == 404
    assert client.get('/account/data-closure', headers=headers(controls)).status_code == 200
    with Session(engine) as session:
        assert service.run_pending(session) == 0
        assert session.get(DataControlJob, queued['id']).status == 'failed'
        assert session.get(Message, 'old-root')
    assert not service._artifact_path(queued['id']).exists()


def test_meeting_metadata_export_excludes_routing_secrets_and_old_routing_purges(controls):
    _, engine, _, _ = controls
    from app.models.meetings import Meeting, MeetingParticipant, MeetingInvitation, MeetingSignal
    with Session(engine) as session:
        session.add(Meeting(id='old-meeting', channel_id='native', host_user_id='owner', created_at=OLD, expires_at=OLD, ended_at=OLD))
        session.add(Meeting(id='live-meeting', channel_id='native', host_user_id='owner', expires_at=datetime.utcnow()+timedelta(hours=1)))
        session.flush()
        session.add(MeetingParticipant(id='participant', meeting_id='old-meeting', user_id='owner', name='PRIVATE GUEST NAME',
            credential_hash='PRIVATE CAPABILITY', auth_token_hash='PRIVATE SESSION HASH', created_at=OLD, last_seen_at=OLD, expires_at=OLD))
        session.add(MeetingInvitation(id='invite', meeting_id='old-meeting', token_hash='PRIVATE INVITE', created_at=OLD, expires_at=OLD)); session.flush()
        session.add(MeetingSignal(id='signal', meeting_id='old-meeting', sequence=1, sender_id='participant', recipient_id='participant',
            encrypted_payload='PRIVATE ENCRYPTED ICE', created_at=OLD)); session.commit()
    queued=post(controls,'/exports',{'channel_ids':['native'],'acknowledge_plaintext_export':True}).json()
    with Session(engine) as session: assert service.run_pending(session)==1
    response=post(controls,'/exports/'+queued['id']+'/download')
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        data=json.loads(archive.read('export.json'))
        assert next(row for row in data['meeting'] if row['id']=='old-meeting')['participant_count']==1
        assert not any(secret in json.dumps(data) for secret in ['PRIVATE GUEST NAME','PRIVATE CAPABILITY','PRIVATE SESSION HASH','PRIVATE INVITE','PRIVATE ENCRYPTED ICE'])
    enable(controls)
    dry=preview(controls); assert dry['counts']['meeting']==1 and dry['counts']['meetingsignal']==1
    queue_purge(controls,dry)
    with Session(engine) as session:
        assert service.run_pending(session)==1
        assert not session.get(Meeting,'old-meeting') and session.get(Meeting,'live-meeting')
        assert not session.get(MeetingParticipant,'participant') and not session.get(MeetingInvitation,'invite') and not session.get(MeetingSignal,'signal')


def _feedback(controls, user="owner", task="first_message", note="PRIVATE voluntary note", workspace="a", deleted=False):
    from app.models.pilot_feedback import PilotFeedback
    from app.services import pilot_feedback as feedback_service
    with Session(controls[1]) as session:
        row=PilotFeedback(workspace_id=workspace,user_id=user,task=task,outcome="completed",rating=4)
        row.encrypted_note=feedback_service.seal_private(note,"pilot-feedback-note",feedback_service.note_subject(row))
        session.add(row);session.commit()
        if deleted:
            # Match real deletion's UPDATE: SQLAlchemy insert defaults otherwise
            # replace explicit None timestamp values with their default factories.
            row.deleted=True;row.outcome=row.rating=row.encrypted_note=row.created_at=row.updated_at=None
            session.add(row);session.commit()


def test_export_feedback_includes_only_requester_notes_and_minimal_own_tombstones(controls):
    _feedback(controls,note="PRIVATE OWN feedback export")
    _feedback(controls,task="file_share",deleted=True)
    _feedback(controls,user="peer",note="PRIVATE PEER MUST NOT EXPORT")
    queued=post(controls,"/exports",{"channel_ids":["native"],"acknowledge_plaintext_export":True})
    assert queued.status_code==202,queued.text
    with Session(controls[1]) as session: assert service.run_pending(session)==1
    artifact=service._artifact_path(queued.json()["id"]).read_bytes()
    assert b"PRIVATE OWN feedback export" not in artifact
    downloaded=post(controls,"/exports/"+queued.json()["id"]+"/download")
    assert downloaded.status_code==200,downloaded.text
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        data=json.loads(archive.read("export.json"))
    rows=data["pilot_feedback"]
    assert len(rows)==2
    assert next(row for row in rows if row["task"]=="first_message")["note"]=="PRIVATE OWN feedback export"
    assert next(row for row in rows if row["task"]=="file_share")=={"task":"file_share","revision":1,"deleted":True}
    assert "PRIVATE PEER MUST NOT EXPORT" not in json.dumps(data)
    assert all("user_id" not in row and "encrypted_note" not in row for row in rows)


def _close_feedback_account(controls,token):
    client=controls[0];authorization={"Authorization":"Bearer "+token}
    dry=client.get("/account/data-closure",headers=authorization)
    assert dry.status_code==200,dry.text
    response=client.post("/account/data-closure",headers=authorization,json={"password":PASSWORD,
        "confirmation":"ERASE MY ACCOUNT","preview_fingerprint":dry.json()["fingerprint"],
        "acknowledge_retained_shared_authorship_and_backups":True})
    return dry.json(),response


def test_actual_account_closure_erases_own_feedback_across_workspaces_without_decrypting_peers(controls,monkeypatch):
    from app.models.pilot_feedback import PilotFeedback
    _feedback(controls,note="PRIVATE OWN must erase")
    _feedback(controls,task="file_share",deleted=True)
    _feedback(controls,task="find_content",workspace="b",note="Former-workspace own note must erase")
    _feedback(controls,user="peer",note="PRIVATE PEER must survive")
    monkeypatch.setattr("app.services.matrix_bridge.remove_secure_member",lambda *_:None)
    monkeypatch.setattr("app.services.pilot_feedback.private_note",lambda *_:(_ for _ in ()).throw(AssertionError("closure must not decrypt")))
    dry,response=_close_feedback_account(controls,controls[2]["owner"])
    assert dry["can_erase_identity"] and response.status_code==200,response.text
    with Session(controls[1]) as session:
        assert not session.exec(select(PilotFeedback).where(PilotFeedback.user_id=="owner")).all()
        assert session.get(PilotFeedback,("a","peer","first_message")).encrypted_note


@pytest.mark.parametrize("deleted",[False,True])
def test_actual_personal_workspace_closure_allows_only_own_feedback_or_tombstone(controls,deleted):
    from app.models.pilot_feedback import PilotFeedback
    client,engine,tokens,_=controls
    with Session(engine) as session:
        session.add(User(id="feedback-only",name="Feedback author",email="feedback-only@example.invalid",password_hash=hash_password(PASSWORD)))
        session.add(Workspace(id="feedback-home",name="Personal workspace"));session.commit()
        session.add(PersonalWorkspace(user_id="feedback-only",workspace_id="feedback-home"))
        session.add(WorkspaceMember(workspace_id="feedback-home",user_id="feedback-only",role="owner"));session.commit()
        token=create_token("feedback-only",session)
    _feedback(controls,user="feedback-only",workspace="feedback-home",deleted=deleted)
    dry,response=_close_feedback_account(controls,token)
    assert dry["can_erase_identity"] and dry["empty_personal_workspaces"]==["feedback-home"]
    assert response.status_code==200,response.text
    with Session(engine) as session:
        assert not session.get(Workspace,"feedback-home")
        assert not session.exec(select(PilotFeedback).where(PilotFeedback.user_id=="feedback-only")).all()
        assert session.get(User,"feedback-only").is_blocked


def test_personal_workspace_peer_feedback_blocks_closure_and_preserves_both_private_notes(controls):
    from app.models.pilot_feedback import PilotFeedback
    client,engine,tokens,_=controls
    with Session(engine) as session:
        session.add(User(id="feedback-only",name="Feedback author",email="feedback-only@example.invalid",password_hash=hash_password(PASSWORD)))
        session.add(Workspace(id="feedback-home",name="Personal workspace"));session.commit()
        session.add(PersonalWorkspace(user_id="feedback-only",workspace_id="feedback-home"))
        session.add(WorkspaceMember(workspace_id="feedback-home",user_id="feedback-only",role="owner"));session.commit()
        token=create_token("feedback-only",session)
    _feedback(controls,user="feedback-only",workspace="feedback-home",note="Own stays on blocked closure")
    # A former participant's retained private note is still a real dependency;
    # lack of a current membership must not authorize erasing that person's row.
    _feedback(controls,user="peer",workspace="feedback-home",note="Former participant must survive")
    dry,response=_close_feedback_account(controls,token)
    assert not dry["can_erase_identity"] and dry["blocking_workspaces"]==["feedback-home"]
    assert response.status_code==409
    with Session(engine) as session:
        assert session.get(Workspace,"feedback-home") and not session.get(User,"feedback-only").is_blocked
        assert session.get(PilotFeedback,("feedback-home","feedback-only","first_message")).encrypted_note
        assert session.get(PilotFeedback,("feedback-home","peer","first_message")).encrypted_note
