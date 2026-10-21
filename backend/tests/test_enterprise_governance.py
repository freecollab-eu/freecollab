"""Private API, current grants and serialized native preservation/DLP."""
import json
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi import HTTPException
from sqlmodel import Session, select
import pytest

from app.api import admin, enterprise_governance, files, messages, push, threads
from app.models.enterprise_governance import GovernanceAudit, GovernanceCase, NativeDLPPolicy, OrganizationMember
from app.models.message import Message
from app.models.channel import ChannelMember
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.services import enterprise_governance as service
from app.services.auth import create_token

from tests.test_data_controls import PASSWORD, FILE_ID, controls, headers, enable, preview, queue_purge, post


@pytest.fixture
def governance(controls, monkeypatch):
    from app.services import storage
    monkeypatch.setattr(files, "storage_service", storage.storage_service)
    client = controls[0]
    for router in (enterprise_governance.router, files.router, messages.router, threads.router, push.router, admin.router):
        client.app.include_router(router)
    return controls


def mutate(state, path, body=None, *, name="owner", method="post"):
    return getattr(state[0], method)(path, headers=headers(state, name), json={"password": PASSWORD, **(body or {})})


def dlp(state, **changes):
    body = {"name": "Protected values", "detector": "literal", "literal": "PRIVATE-RESTRICTED",
        "action": "block", "surfaces": ["message", "file", "app", "assistant", "search", "document"], **changes}
    return mutate(state, "/workspaces/a/governance/dlp", body)


def case(state, **changes):
    return mutate(state, "/workspaces/a/governance/cases", {"title": "Confidential investigation", "acknowledge_native_scope": True, **changes})


def org(state):
    result = mutate(state, "/organizations", {"name": "Example organization", "country_code": "SE", "region_label": "Customer operated Stockholm"})
    assert result.status_code == 201, result.text
    return result.json()["id"]


@pytest.mark.parametrize("name", ["member", "outsider", "scoped"])
def test_private_governance_requires_current_native_workspace_owner(governance, name):
    result = governance[0].get("/workspaces/a/governance", headers=headers(governance, name))
    assert result.status_code == 403
    assert case(governance, title="Sensitive", **{}).status_code == 201
    denied = mutate(governance, "/workspaces/a/governance/cases", {"title": "Other", "acknowledge_native_scope": True}, name=name)
    assert denied.status_code == 403


def test_organization_roles_do_not_grant_workspace_or_channel_content(governance):
    identifier = org(governance)
    assert mutate(governance, f"/organizations/{identifier}/workspaces/a").status_code == 200
    assert mutate(governance, f"/organizations/{identifier}/members/outsider", {"role": "admin"}, method="put").status_code == 200
    status = governance[0].get(f"/organizations/{identifier}", headers=headers(governance, "outsider"))
    assert status.status_code == 200 and status.json()["content_access_granted"] is False
    audit = governance[0].get(f"/organizations/{identifier}/audit", headers=headers(governance, "outsider"))
    assert audit.status_code == 200 and "organization.create" in audit.text
    assert governance[0].get(f"/organizations/{identifier}/audit", headers=headers(governance, "member")).status_code == 403
    assert governance[0].get("/messages/channels/native", headers=headers(governance, "outsider")).status_code == 403
    assert governance[0].get("/workspaces/a/governance", headers=headers(governance, "outsider")).status_code == 403
    assert mutate(governance, f"/organizations/{identifier}/workspaces/b").status_code == 403
    counts = governance[0].get(f"/organizations/{identifier}/usage", headers=headers(governance, "outsider"))
    assert counts.status_code == 200 and counts.json()["workspaces"][0]["native_messages"] == 4
    assert all(secret not in counts.text for secret in ("HIDDEN MUST", "OLD PRIVATE", "private.txt", "private-client", "matrix_room_id"))


def test_organization_uses_fresh_roles_and_last_active_owner_protection(governance):
    identifier = org(governance)
    denied = mutate(governance, f"/organizations/{identifier}/members/owner", {"role": "remove"}, method="put")
    assert denied.status_code == 409
    with Session(governance[1]) as cached:
        service.require_organization(cached, identifier, "owner")
        cached.get(OrganizationMember, (identifier, "owner"))
        cached.commit()
        with Session(governance[1]) as other:
            row = other.get(OrganizationMember, (identifier, "owner")); row.role = "auditor"
            other.add(row); other.commit()
        with pytest.raises(HTTPException) as failure:
            service.require_organization(cached, identifier, "owner", owner=True)
        assert failure.value.status_code == 403


def test_organization_sso_is_each_workspaces_own_provider_with_owner_emergency_account(governance):
    identifier = org(governance)
    assert mutate(governance, f"/organizations/{identifier}/workspaces/a").status_code == 200
    result = mutate(governance, f"/organizations/{identifier}/policy", {"expected_revision": 1, "require_sso": True, "country_code": "SE"}, method="put")
    assert result.status_code == 200, result.text
    assert governance[0].get("/messages/channels/native", headers=headers(governance, "member")).status_code == 403
    assert governance[0].get("/messages/channels/native", headers=headers(governance)).status_code == 200
    # Another workspace lacking its own enabled provider cannot be attached.
    assert mutate(governance, f"/organizations/{identifier}/members/outsider", {"role": "admin"}, method="put").status_code == 200
    assert mutate(governance, f"/organizations/{identifier}/workspaces/b", name="outsider").status_code == 409


def test_dlp_blocks_real_message_thread_offline_and_keeps_phrase_protected_at_rest(governance):
    result = dlp(governance)
    assert result.status_code == 201 and "PRIVATE-RESTRICTED" not in result.text
    client = governance[0]
    for path, payload in [
        ("/messages/channels/native", {"content": "Send PRIVATE-RESTRICTED"}),
        ("/threads/messages/old-root/reply", {"content": "Send PRIVATE-RESTRICTED"}),
        ("/mobile/offline/channels/native/messages", {"content": "Send PRIVATE-RESTRICTED", "client_id": "client-with-sensitive-phrase"}),
    ]:
        response = client.post(path, headers=headers(governance), json=payload)
        assert response.status_code == 423, (path, response.status_code, response.text)
    assert client.post("/messages/channels/native", headers=headers(governance), json={"content": "Allowed team update"}).status_code == 200
    with Session(governance[1]) as session:
        row = session.get(NativeDLPPolicy, result.json()["id"])
        assert "PRIVATE-RESTRICTED" not in row.encrypted_literal
        assert len(session.exec(select(Message)).all()) == 5
        assert not service.native_content_allowed(session, "native", "PRIVATE-RESTRICTED", surface="search")


def test_warn_returns_metadata_without_persisting_matched_plaintext(governance):
    assert dlp(governance, action="warn").status_code == 201
    response = governance[0].post("/messages/channels/native", headers=headers(governance), json={"content": "PRIVATE-RESTRICTED"})
    assert response.status_code == 200 and response.json()["policy_warnings"][0]["code"] == "native_dlp_warning"
    audit = governance[0].get("/workspaces/a/governance/audit", headers=headers(governance))
    assert audit.status_code == 200 and "dlp.warn" in audit.text and "PRIVATE-RESTRICTED" not in audit.text


def test_luhn_classifier_does_not_block_random_identifiers(governance):
    assert dlp(governance, detector="credit_card", literal=None).status_code == 201
    client = governance[0]
    assert client.post("/messages/channels/native", headers=headers(governance), json={"content": "4111 1111 1111 1111"}).status_code == 423
    assert client.post("/messages/channels/native", headers=headers(governance), json={"content": "4111 1111 1111 1112"}).status_code == 200


def test_file_policy_scans_real_text_and_explicitly_blocks_uninspectable_binary(governance):
    assert dlp(governance, block_uninspectable_files=True).status_code == 201
    client = governance[0]
    for name, content, mime in [("notes.txt", b"PRIVATE-RESTRICTED", "text/plain"), ("report.pdf", b"%PDF uninspectable", "application/pdf")]:
        response = client.post("/files/channels/native", headers=headers(governance), files={"file": (name, content, mime)})
        assert response.status_code == 423, response.text
    result = client.post("/files/channels/native", headers=headers(governance), files={"file": ("notes.txt", b"Clear team notes", "text/plain")})
    assert result.status_code == 200, result.text


def test_native_dlp_never_claims_matrix_ciphertext_inspection(governance):
    assert dlp(governance).status_code == 201
    with Session(governance[1]) as session:
        with pytest.raises(HTTPException) as failure:
            service.enforce_native_content(session, "secure", "owner", "ciphertext")
        assert failure.value.status_code == 409
    state = governance[0].get("/workspaces/a/governance", headers=headers(governance))
    assert state.status_code == 200 and state.json()["encryption"]["dlp_scope"] == "native_only"


def test_custodian_hold_fences_overwriting_and_file_deletion_but_preserves_other_authors(governance):
    held = case(governance, channel_ids=["native"], custodian_ids=["owner"])
    assert held.status_code == 201, held.text
    client = governance[0]
    assert client.delete("/messages/old-root", headers=headers(governance)).status_code == 423
    assert client.put("/messages/old-root", headers=headers(governance), json={"content": "Overwrite evidence"}).status_code == 423
    assert client.delete("/files/" + FILE_ID, headers=headers(governance)).status_code == 423
    assert client.put("/messages/fresh", headers=headers(governance, "member"), json={"content": "Other author's ordinary edit"}).status_code == 200
    state = client.get("/workspaces/a/governance", headers=headers(governance)).json()
    assert state["retention_hold"] is True and state["case_hold_active"] is True
    released = mutate(governance, f"/workspaces/a/governance/cases/{held.json()['id']}/release", {"expected_revision": 1, "confirmation": "RELEASE PRESERVATION"})
    assert released.status_code == 200
    assert client.delete("/messages/old-root", headers=headers(governance)).status_code == 200


def test_late_case_blocks_previously_queued_retention_purge_and_file_bytes_remain(governance):
    enable(governance)
    dry = preview(governance)
    job = queue_purge(governance, dry)
    assert job.status_code == 202
    assert case(governance, custodian_ids=["member"]).status_code == 201
    run = post(governance, "/jobs/" + job.json()["id"] + "/run")
    assert run.status_code == 200 and run.json()["status"] == "failed", run.text
    with Session(governance[1]) as session:
        assert session.get(Message, "old-root") is not None


def test_case_collection_requires_preservation_current_channel_access_and_real_review(governance):
    held = case(governance, channel_ids=["native"]).json()
    path = f"/workspaces/a/governance/cases/{held['id']}/exports"
    assert mutate(governance, path, {"channel_ids": ["hidden"], "acknowledge_plaintext_export": True}).status_code == 403
    response = mutate(governance, path, {"channel_ids": ["native"], "acknowledge_plaintext_export": True})
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    run = post(governance, "/jobs/" + identifier + "/run")
    assert run.status_code == 200 and run.json()["status"] == "ready", run.text
    reviewed = mutate(governance, path + "/" + identifier + "/review", {"review_state": "relevant"})
    assert reviewed.status_code == 200 and reviewed.json()["review_state"] == "relevant"
    collection = governance[0].get(path, headers=headers(governance))
    assert collection.status_code == 200 and collection.json()["exports"][0]["sha256"]
    downloaded = post(governance, "/exports/" + identifier + "/download")
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"PK")
    with Session(governance[1]) as session:
        assert session.exec(select(GovernanceCase)).first().status == "active"
        assert "Confidential investigation" not in session.exec(select(GovernanceCase)).first().encrypted_title


def test_released_case_cannot_collect_and_queued_export_rechecks_hold(governance):
    held = case(governance).json()
    path = f"/workspaces/a/governance/cases/{held['id']}"
    queued = mutate(governance, path + "/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True}).json()
    assert mutate(governance, path + "/release", {"expected_revision": 1, "confirmation": "RELEASE PRESERVATION"}).status_code == 200
    assert mutate(governance, path + "/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True}).status_code == 409
    run = post(governance, "/jobs/" + queued["id"] + "/run")
    assert run.status_code == 200 and run.json()["status"] == "failed"


def test_case_creation_serializes_against_concurrent_message_delete(governance, monkeypatch):
    preserving, release = Event(), Event()
    original = service.audit
    def pause(session, actor_id, action, target_id, **kwargs):
        original(session, actor_id, action, target_id, **kwargs)
        if action == "case.preserve":
            preserving.set()
            assert release.wait(5)
    monkeypatch.setattr(service, "audit", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        preserve = pool.submit(case, governance, channel_ids=["native"])
        assert preserving.wait(5)
        delete = pool.submit(governance[0].delete, "/messages/old-root", headers=headers(governance))
        release.set()
        assert preserve.result(10).status_code == 201
        assert delete.result(10).status_code == 423
    with Session(governance[1]) as session:
        assert not session.get(Message, "old-root").deleted


def test_moderation_cannot_erase_preserved_message(governance):
    with Session(governance[1]) as session:
        owner = session.get(User, "owner"); owner.role = "admin"; session.add(owner); session.commit()
    assert case(governance, channel_ids=["native"]).status_code == 201
    response = governance[0].delete("/admin/messages/old-root", headers=headers(governance))
    assert response.status_code == 423
    with Session(governance[1]) as session:
        assert not session.get(Message, "old-root").deleted


def test_collection_rechecks_channel_membership_when_worker_runs(governance):
    held = case(governance).json()
    queued = mutate(governance, f"/workspaces/a/governance/cases/{held['id']}/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True})
    assert queued.status_code == 202
    with Session(governance[1]) as session:
        session.delete(session.get(ChannelMember, ("native", "owner"))); session.commit()
    result = post(governance, "/jobs/" + queued.json()["id"] + "/run")
    assert result.status_code == 200 and result.json()["status"] == "failed"
    assert post(governance, "/exports/" + queued.json()["id"] + "/download").status_code != 200


def test_blocking_document_policy_denies_uninspectable_yjs_and_reads_current_policy(governance):
    result = dlp(governance, surfaces=["document"])
    assert result.status_code == 201
    with Session(governance[1]) as session:
        with pytest.raises(HTTPException) as failure:
            service.enforce_native_document(session, "native", "owner", {"update_base64": "AA==", "title": "Plain title"}, opaque_update=True)
        assert failure.value.status_code == 423
        session.rollback()
        service.enforce_native_document(session, "native", "owner", {"text": "Clear text"})
        session.commit()
        with Session(governance[1]) as other:
            row = other.get(NativeDLPPolicy, result.json()["id"]); row.enabled = False; other.add(row); other.commit()
        service.enforce_native_document(session, "native", "owner", {"text": "PRIVATE-RESTRICTED"})


def test_workspace_owner_role_is_fresh_even_when_membership_object_was_cached(governance):
    from app.services.data_control import require_owner
    with Session(governance[1]) as cached:
        cached.get(WorkspaceMember, ("a", "owner")); cached.commit()
        with Session(governance[1]) as other:
            row = other.get(WorkspaceMember, ("a", "owner")); row.role = "member"; other.add(row); other.commit()
        with pytest.raises(HTTPException) as failure: require_owner(cached, "a", "owner")
        assert failure.value.status_code == 403


def test_native_clip_and_shared_bot_card_evidence_export_excludes_sfu_credentials(governance):
    from app.models.clips import ChannelClip
    from app.models.sfu import SFURoom, SFUParticipant
    from app.models.app_platform import AppCard
    from app.services.account_security import seal_private
    from app.services.shared_documents import seal
    from datetime import datetime, timedelta
    with Session(governance[1]) as session:
        session.add(ChannelClip(id="clip", channel_id="native", created_by="owner", content_mode="server", payload_hash="fixture",
            file_id=FILE_ID, encrypted_payload=seal("native", "clip", "clip", "clip", {"title": "Private recording", "transcript": "Private transcript"})))
        session.add(AppCard(id="bot-card", workspace_id="a", channel_id="native", bot_id="assistant", bot_name="Team assistant",
            encrypted_card=seal_private(json.dumps({"title": "Shared bot evidence", "body": "Native shared summary"}), "app-card", "bot-card")))
        session.add(SFURoom(id="room", channel_id="native", host_user_id="owner", expires_at=datetime.utcnow() + timedelta(hours=1), max_participants=10))
        session.flush()
        session.add(SFUParticipant(meeting_id="room", user_id="owner", name="Owner", auth_token_hash="DO-NOT-EXPORT-TOKEN-HASH", auth_version=77,
            expires_at=datetime.utcnow() + timedelta(minutes=10)))
        session.commit()
    held = case(governance).json()
    queued = mutate(governance, f"/workspaces/a/governance/cases/{held['id']}/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True}).json()
    assert post(governance, "/jobs/" + queued["id"] + "/run").json()["status"] == "ready"
    downloaded = post(governance, "/exports/" + queued["id"] + "/download")
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        exported = json.loads(archive.read("export.json"))
    assert exported["channelclip"][0]["fields"]["transcript"] == "Private transcript"
    assert exported["appcard"][0]["card"]["title"] == "Shared bot evidence"
    assert exported["sfuroom"][0]["participant_count"] == 1
    assert "DO-NOT-EXPORT-TOKEN-HASH" not in json.dumps(exported)
    assert exported["preservation_case"]["id"] == held["id"]


def test_last_organization_owner_must_transfer_before_identity_erasure(governance):
    identifier = org(governance)
    result = governance[0].get("/account/data-closure", headers=headers(governance))
    assert result.status_code == 200
    assert identifier in result.json()["blocking_organizations"] and not result.json()["can_erase_identity"]
    assert mutate(governance, f"/organizations/{identifier}/members/peer", {"role": "owner"}, method="put").status_code == 200
    state = governance[0].get("/account/data-closure", headers=headers(governance)).json()
    assert state["blocking_organizations"] == []


def test_identity_erasure_cleans_native_matrix_sidecars_before_session_foreign_keys(governance, monkeypatch):
    from app.models.native_clients import NativeClientGrant, NativeClientSession, NativeClientMatrixBinding
    from app.models.secure_channel import MatrixSession
    from app.models.session import ActiveSession
    from datetime import datetime, timedelta
    monkeypatch.setattr("app.services.matrix_bridge.remove_secure_member", lambda *_: None)
    with Session(governance[1]) as session:
        session.add(ActiveSession(user_id="member", token_hash="isolated-native-child", expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.flush()
        session.add(NativeClientGrant(code_hash="isolated-native-code", user_id="member", parent_token_hash="isolated-parent", source_claims='{"sub":"member"}',
            code_challenge="challenge", redirect_uri="freecollab://auth/callback", client_kind="desktop", expires_at=datetime.utcnow() + timedelta(minutes=1)))
        session.add(NativeClientSession(token_hash="isolated-native-child", user_id="member", parent_token_hash="isolated-parent", source_claims='{"sub":"member"}', client_kind="desktop"))
        session.add(MatrixSession(matrix_token_hash="isolated-matrix-token", user_id="member", auth_token_hash="isolated-native-child", auth_expires_at=datetime.utcnow() + timedelta(hours=1), device_id="isolated-device", encrypted_access_token="isolated-ciphertext"))
        session.flush()
        session.add(NativeClientMatrixBinding(matrix_token_hash="isolated-matrix-token", native_client_id="isolated-native-client")); session.commit()
    dry = governance[0].get("/account/data-closure", headers=headers(governance, "member")).json()
    result = mutate(governance, "/account/data-closure", {"confirmation": "ERASE MY ACCOUNT", "preview_fingerprint": dry["fingerprint"], "acknowledge_retained_shared_authorship_and_backups": True}, name="member")
    assert result.status_code == 200, result.text
    with Session(governance[1]) as session:
        assert not session.exec(select(NativeClientGrant).where(NativeClientGrant.user_id == "member")).all()
        assert session.get(NativeClientSession, "isolated-native-child") is None
        assert session.get(NativeClientMatrixBinding, "isolated-matrix-token") is None
        assert session.get(MatrixSession, "isolated-matrix-token") is None


def test_requester_identity_erasure_expires_private_evidence_copy_but_retains_shared_case_provenance(governance, monkeypatch):
    from app.models.data_control import DataControlJob
    from app.models.enterprise_governance import GovernanceCaseExport
    from app.services.account_security import unseal_private
    monkeypatch.setattr("app.services.matrix_bridge.remove_secure_member", lambda *_: None)
    held = case(governance).json()
    queued = mutate(governance, f"/workspaces/a/governance/cases/{held['id']}/exports", {"channel_ids": ["native"], "acknowledge_plaintext_export": True}).json()
    assert post(governance, "/jobs/" + queued["id"] + "/run").json()["status"] == "ready"
    dry = governance[0].get("/account/data-closure", headers=headers(governance)).json()
    assert dry["can_erase_identity"], dry
    result = mutate(governance, "/account/data-closure", {"confirmation": "ERASE MY ACCOUNT", "preview_fingerprint": dry["fingerprint"], "acknowledge_retained_shared_authorship_and_backups": True})
    assert result.status_code == 200, result.text
    with Session(governance[1]) as session:
        job = session.get(DataControlJob, queued["id"])
        assert job.status == "expired" and job.token_hash == "erased" and job.artifact_sha256
        assert unseal_private(job.encrypted_request, "data-job", job.id) == "{}"
        assert session.get(GovernanceCaseExport, job.id)
        assert session.get(GovernanceCase, held["id"]).status == "active"


def test_custodian_hold_protects_older_versions_when_current_file_uploader_differs(governance):
    from app.models.file import File
    from app.models.file_version import FileDocument, FileVersion
    from app.services.storage import storage_service
    new_id = "e" * 32
    location = storage_service.save_file_sync(new_id, b"New imported context")
    with Session(governance[1]) as session:
        session.add(File(id=new_id, channel_id="native", uploader_id="peer", filename="new-version.txt", content_type="text/plain", size=20, storage_path=location))
        session.flush()
        session.add(FileVersion(file_id=new_id, document_id=FILE_ID, number=2))
        document = session.get(FileDocument, FILE_ID); document.current_file_id = new_id; document.version_count = 2
        session.add(document); session.commit()
    assert case(governance, channel_ids=["native"], custodian_ids=["owner"]).status_code == 201
    response = governance[0].delete("/files/" + new_id, headers=headers(governance))
    assert response.status_code == 423
    with Session(governance[1]) as session:
        assert session.get(File, FILE_ID) and session.get(File, new_id)
