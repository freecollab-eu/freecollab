"""Operations must expose real retained work without inspecting private payloads."""
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

from app.config import settings
from app.models.app_platform import AppAIDraft, AppInstallation, AppJob, ChannelRecapSchedule
from app.models.avatar import AvatarErasure
from app.models.calendar_delivery import CalendarProviderJob, CalendarProviderPublication
from app.models.channel import Channel
from app.models.chat_productivity import MessageReminder, ScheduledMessage
from app.models.integration_content import IntegrationContent
from app.models.native_calls import NativeCallDevice
from app.models.native_push import NativePushDelivery, NativePushDevice
from app.models.personal_oauth import PersonalOAuthClient, PersonalOAuthGrant
from app.models.remote_sync import RemoteSyncConnection
from app.models.slack_import import SlackImport
from app.models.workspace import Workspace
from app.services.maildiagnostics import integration_queue_status, integration_worker_required
from tests.test_operations import headers, operator


FLAGS = ("webhooks_enabled", "otrust_enabled", "sync_enabled", "workflows_enabled",
         "data_controls_enabled", "push_enabled", "native_push_enabled", "copilot_enabled")
GROUPS = {"webhooks", "otrust", "sync", "calendar-delivery", "workflows", "data", "push",
          "native_push", "native-call-cleanup", "slack-import-cleanup", "personal-oauth-cleanup",
          "chat", "apps", "recaps", "avatar-cleanup"}


def disable_features(monkeypatch):
    for flag in FLAGS:
        monkeypatch.setattr(settings, flag, False)


def seed_new_groups(engine, now):
    past, future = now - timedelta(minutes=7), now + timedelta(hours=1)
    with Session(engine) as session:
        session.add(Workspace(id="private-team", name="Private workspace"))
        session.add(Channel(id="private-channel", name="Private channel"))
        session.commit()
        session.add(AppInstallation(id="private-app", workspace_id="private-team", app_id="private-provider", granted_by="operator",
            channel_ids_json='["private-channel"]', manifest_json="private-manifest", encrypted_config="private-config",
            encrypted_secret="private-secret", origin="https://private-provider.invalid"))
        session.add(IntegrationContent(id="private-event", channel_id="private-channel", kind="calendar", created_by="operator"))
        session.add(RemoteSyncConnection(id="private-sync", workspace_id="private-team", channel_id="private-channel", created_by="operator",
            grant_user_id="operator", name="private-name", provider="google", kind="calendar", config_json="private-config"))
        session.add(PersonalOAuthClient(id="private-oauth-client", workspace_id="private-team", provider="google", purpose="search",
            client_id="private-client", configured_by="operator"))
        for identifier in ("expired", "future"):
            session.add(NativePushDevice(id="private-device-" + identifier, user_id="operator", platform="ios", token_hash=identifier,
                encrypted_token="private-token", auth_token_hash="private-token", encrypted_claims="private-claims"))
        session.commit()
        session.add(CalendarProviderPublication(id="private-publication", workspace_id="private-team", channel_id="private-channel",
            item_id="private-event", connection_id="private-sync", user_id="operator", connection_revision=1))
        for index, status in enumerate(("pending", "uncertain", "delivered")):
            session.add(AppJob(installation_id="private-app", generation=1, channel_id="private-channel", actor_id="operator",
                command="private-command", request_id=str(index), request_digest="private-digest", encrypted_payload="private-body",
                status=status, next_attempt_at=future, created_at=past - timedelta(days=2)))
        for index, (enabled, deadline) in enumerate(((True, past), (True, future), (False, past))):
            session.add(ChannelRecapSchedule(workspace_id="private-team", channel_id="private-channel", owner_id="operator",
                provider_revision=1, interval_hours=24, enabled=enabled, next_run_at=deadline, last_status="draft_ready"))
        session.add(AppAIDraft(workspace_id="private-team", channel_id="private-channel", owner_id="operator", kind="recap",
            provider_revision=1, encrypted_result="private-recap-body"))
        for identifier, deadline in (("expired", past), ("future", future)):
            session.add(NativeCallDevice(user_id="operator", push_device_id="private-device-" + identifier,
                source_hash="private-source", push_confirmed_at=now, encrypted_claims="private-claims",
                credential_hash=identifier, platform="ios", expires_at=deadline))
        for index, (status, deadline) in enumerate((("preview", past), ("in_progress", future), ("completed", past))):
            session.add(SlackImport(workspace_id="private-team", actor_id="operator", archive_digest=str(index),
                encrypted_archive="private-archive", status=status, expires_at=deadline))
        for index, (status, created, checked) in enumerate((("pending", past, datetime(1970, 1, 1)),
                ("active", past, now), ("disconnected", past, datetime(1970, 1, 1)))):
            session.add(PersonalOAuthGrant(workspace_id="private-team", user_id="operator", client_id="private-oauth-client",
                client_revision=1, provider="google", purpose="search", target_id=str(index), name="private-grant",
                source_hash="private-source", encrypted_source_claims="private-claims", encrypted_tokens="private-token",
                status=status, created_at=created, last_checked_at=checked))
        session.add(NativePushDelivery(device_id="private-device-expired", source_key="private-source", channel_id="private-channel",
            expires_at=future, next_attempt_at=past, created_at=past - timedelta(days=2)))
        session.add(ScheduledMessage(user_id="operator", channel_id="private-channel", client_id="private-scheduled",
            request_digest="private-digest", encrypted_content="private-body", due_at=future, session_hash="private-session", claims_json="private-claims"))
        session.add(ScheduledMessage(user_id="operator", channel_id="private-channel", client_id="private-notifications",
            request_digest="private-digest", encrypted_content="private-body", due_at=past, session_hash="private-session", claims_json="private-claims",
            status="sent", notifications_delivered=False))
        session.add(MessageReminder(user_id="operator", channel_id="private-channel", item_id="private-message", kind="message",
            due_at=past, session_hash="private-session", claims_json="private-claims", status="notified"))
        session.add(MessageReminder(user_id="operator", channel_id="private-channel", item_id="private-completed", kind="message",
            due_at=past, session_hash="private-session", claims_json="{}", status="completed", completed_at=now))
        session.add(AvatarErasure(blob_id="private-removed-profile-photo", user_id="operator", created_at=past))
        session.commit()
        for index, status in enumerate(("queued", "running", "succeeded", "uncertain")):
            session.add(CalendarProviderJob(client_id=str(index), request_hash="private-request", user_id="operator", channel_id="private-channel",
                item_id="private-event", publication_id="private-publication", action="invite", event_revision=1, publication_revision=1,
                connection_revision=1, session_hash="private-session", claims_json="private-claims", status=status, created_at=past))
        session.commit()


def test_all_worker_groups_report_real_states_due_lag_and_no_private_data(operator, monkeypatch):
    client, engine, _ = operator
    now = datetime.utcnow()
    seed_new_groups(engine, now)
    # Status is metadata-only: even malformed synthetic encrypted payloads must not be opened.
    from app.services import account_security
    monkeypatch.setattr(account_security, "unseal_private", lambda *args: pytest.fail("diagnostics decrypted private content"))
    with Session(engine) as session:
        queues = integration_queue_status(session, now)
    assert set(queues) == GROUPS
    assert queues["calendar-delivery"] == {"pending": 1, "processing": 1, "completed": 1, "uncertain": 1,
        "failed": 0, "cancelled": 0, "oldest_pending_seconds": 420}
    assert queues["native_push"]["pending"] == 1 and queues["native_push"]["oldest_pending_seconds"] == 420
    assert queues["apps"]["pending"] == queues["apps"]["uncertain"] == queues["apps"]["delivered"] == 1
    assert queues["apps"]["oldest_pending_seconds"] == 0  # A future retry is not two days overdue.
    assert queues["chat"] == {"pending": 1, "processing": 0, "sent": 0, "notified": 1, "completed": 1, "cancelled": 0,
        "notification_pending": 1, "oldest_pending_seconds": 420}
    assert queues["recaps"] == {"pending": 1, "scheduled": 1, "disabled": 1, "oldest_pending_seconds": 420}
    assert "published" not in queues["recaps"] and "completed" not in queues["recaps"]
    assert queues["native-call-cleanup"] == {"pending": 1, "scheduled": 1, "oldest_pending_seconds": 420}
    assert queues["avatar-cleanup"] == {"pending": 1, "oldest_pending_seconds": 420}
    for group in ("slack-import-cleanup", "personal-oauth-cleanup"):
        assert queues[group] == {"pending": 1, "scheduled": 1, "inactive": 1, "oldest_pending_seconds": 420}
    response = client.get("/operations/status", headers=headers())
    metrics = client.get("/operations/metrics", headers=headers())
    assert response.status_code == metrics.status_code == 200
    assert set(response.json()["integration_queues"]) == GROUPS
    assert 'queue="calendar-delivery",status="uncertain"} 1' in metrics.text
    assert 'queue="chat",status="notification_pending"} 1' in metrics.text
    assert 'queue="recaps",status="scheduled"} 1' in metrics.text
    assert 'queue="avatar-cleanup",status="pending"} 1' in metrics.text
    assert "private-" not in response.text + metrics.text


@pytest.mark.parametrize("flag", FLAGS)
def test_every_enabled_worker_feature_requires_a_heartbeat(operator, monkeypatch, flag):
    client, _, _ = operator
    disable_features(monkeypatch)
    assert not client.get("/operations/status", headers=headers()).json()["integration_worker"]["enabled"]
    monkeypatch.setattr(settings, flag, True)
    assert client.get("/operations/status", headers=headers()).json()["integration_worker"]["enabled"]
    assert "freecollab_integrations_enabled 1" in client.get("/operations/metrics", headers=headers()).text


def test_always_on_work_requires_worker_but_terminal_or_disabled_queue_records_do_not(operator, monkeypatch):
    _, engine, _ = operator
    disable_features(monkeypatch)
    seed_new_groups(engine, datetime.utcnow())
    with Session(engine) as session:
        queues = integration_queue_status(session)
    assert integration_worker_required(queues)
    for group in ("apps", "chat", "native-call-cleanup", "slack-import-cleanup", "personal-oauth-cleanup", "avatar-cleanup"):
        assert integration_worker_required({group: queues[group]})
    assert not integration_worker_required({"webhooks": {"pending": 2}, "recaps": {"scheduled": 1}})
    assert not integration_worker_required({"apps": {"delivered": 3, "uncertain": 1, "failed": 1},
        "chat": {"sent": 1, "notified": 2, "completed": 1}, "personal-oauth-cleanup": {"inactive": 2}, "avatar-cleanup": {"pending": 0}})


def test_native_photo_erasure_worker_clears_pending_status_and_worker_requirement(operator, monkeypatch):
    from app.services import storage
    from app.services.integration_worker import run_once
    client, engine, root = operator
    disable_features(monkeypatch)
    store = storage.StorageService(root / "private-photos")
    monkeypatch.setattr(storage, "storage_service", store)
    blob = "e" * 32
    store.save_file_sync(blob, b"old private profile photo")
    with Session(engine) as session:
        session.add(AvatarErasure(blob_id=blob, user_id="operator"))
        session.commit()
    before = client.get("/operations/status", headers=headers()).json()
    assert before["integration_queues"]["avatar-cleanup"]["pending"] == 1
    assert before["integration_worker"]["enabled"]
    assert run_once(engine)["avatar-cleanup"] == 1
    after = client.get("/operations/status", headers=headers()).json()
    assert after["integration_queues"]["avatar-cleanup"]["pending"] == 0
    assert not after["integration_worker"]["enabled"]
    assert store.read_file(blob) is None
