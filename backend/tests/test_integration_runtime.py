"""Optional queues stay private when disabled and fail independently."""
from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, settings
from app.models.operations import WorkerHeartbeat
from app.services.integration_worker import run_once


@pytest.fixture(autouse=True)
def local_queues_disabled(monkeypatch):
    for name in ("workflows_enabled", "data_controls_enabled", "push_enabled", "native_push_enabled", "copilot_enabled", "connect_federation_enabled"):
        monkeypatch.setattr(settings, name, False)


def test_disabled_integration_worker_never_contacts_a_provider(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "queues.db"))
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "webhooks_enabled", False)
    monkeypatch.setattr(settings, "otrust_enabled", False)
    monkeypatch.setattr(settings, "sync_enabled", False)

    def unexpected(_):
        raise AssertionError("Disabled queue contacted a provider")

    assert run_once(engine, webhook_batch=unexpected, proof_batch=unexpected, sync_batch=unexpected) == {"avatar-cleanup": 0, "file-cleanup": 0, "webhooks": "disabled", "otrust": "disabled", "sync": "disabled", "calendar-delivery": "disabled", "workflows": "disabled", "data": "disabled", "push": "disabled", "native_push": "disabled", "native-call-cleanup": 0, "slack-import-cleanup": 0, "personal-oauth-cleanup": 0, "chat": 0, "apps": 0, "recaps": "disabled", "slack-livebridge": 0, "encrypted-work": "disabled"}
    with Session(engine) as session:
        assert session.get(WorkerHeartbeat, "integrations").last_seen_at <= datetime.utcnow()


def test_failed_queue_rolls_back_without_blocking_other_provider(tmp_path, monkeypatch, caplog):
    engine = create_engine("sqlite:///" + str(tmp_path / "queues.db"))
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(settings, "webhooks_enabled", True)
    monkeypatch.setattr(settings, "otrust_enabled", True)
    monkeypatch.setattr(settings, "sync_enabled", True)

    def failed(session):
        session.add(WorkerHeartbeat(name="partial-write"))
        session.flush()
        raise RuntimeError("https://private-credential@example.com/provider")

    def remaining(session):
        assert session.get(WorkerHeartbeat, "partial-write") is None
        session.add(WorkerHeartbeat(name="proof-work-completed"))
        session.commit()
        return 1

    assert run_once(engine, webhook_batch=failed, proof_batch=remaining, sync_batch=lambda session: 2) == {"avatar-cleanup": 0, "file-cleanup": 0, "webhooks": "error", "otrust": 1, "sync": 2, "calendar-delivery": 0, "workflows": "disabled", "data": "disabled", "push": "disabled", "native_push": "disabled", "native-call-cleanup": 0, "slack-import-cleanup": 0, "personal-oauth-cleanup": 0, "chat": 0, "apps": 0, "recaps": "disabled", "slack-livebridge": 0, "encrypted-work": "disabled"}
    with Session(engine) as session:
        assert session.get(WorkerHeartbeat, "proof-work-completed")
        assert session.get(WorkerHeartbeat, "partial-write") is None
    assert "private-credential" not in caplog.text


@pytest.mark.parametrize("changed", [
    {"oidc_issuer_url": "http://issuer.example"},
    {"oidc_redirect_uri": "https://user:secret@app.example/api/auth/oidc/callback"},
    {"oidc_scopes": "profile email"},
    {"oidc_client_id": ""},
    {"oidc_redirect_uri": ""},
])
def test_enabled_sso_rejects_unsafe_or_incomplete_configuration(changed):
    values = dict(_env_file=None, oidc_enabled=True, oidc_issuer_url="https://id.example/realms/team",
                  oidc_client_id="freecollab", oidc_redirect_uri="https://app.example/api/auth/oidc/callback")
    with pytest.raises(ValueError):
        Settings(**(values | changed))


def test_oidc_accepts_explicit_loopback_mock_only_outside_production():
    values = dict(_env_file=None, oidc_enabled=True, oidc_issuer_url="http://127.0.0.1:18101/realm",
                  oidc_client_id="freecollab", oidc_redirect_uri="http://127.0.0.1:18102/api/auth/oidc/callback")
    assert Settings(**values).oidc_enabled
    with pytest.raises(ValueError, match="OIDC_ISSUER_URL"):
        Settings(**(values | {"environment": "production"}))


@pytest.mark.parametrize("origin", ["https://user:secret@otrust.example", "https://otrust.example/path", "https://otrust.example/?token=secret", "http://otrust.example"])
def test_timestamp_provider_is_an_explicit_safe_origin(origin):
    with pytest.raises(ValueError):
        Settings(_env_file=None, otrust_enabled=True, otrust_base_url=origin)


def test_local_jobs_run_without_external_integrations_and_fail_independently(tmp_path, monkeypatch, caplog):
    engine = create_engine("sqlite:///" + str(tmp_path / "local-queues.db"))
    SQLModel.metadata.create_all(engine)
    for name in ("webhooks_enabled", "otrust_enabled", "sync_enabled"):
        monkeypatch.setattr(settings, name, False)
    for name in ("workflows_enabled", "data_controls_enabled", "push_enabled"):
        monkeypatch.setattr(settings, name, True)

    def failed(session):
        session.add(WorkerHeartbeat(name="rolled-back-workflow"))
        session.flush()
        raise RuntimeError("secret-customer-document")

    def export(session):
        assert session.get(WorkerHeartbeat, "rolled-back-workflow") is None
        session.add(WorkerHeartbeat(name="export-completed"))
        session.commit()
        return 1

    assert run_once(engine, workflow_batch=failed, data_batch=export, push_batch=lambda session: 2) == {
        "avatar-cleanup": 0, "file-cleanup": 0, "webhooks": "disabled", "otrust": "disabled", "sync": "disabled", "calendar-delivery": "disabled",
        "workflows": "error", "data": 1, "push": 2, "native_push": "disabled", "native-call-cleanup": 0, "slack-import-cleanup": 0, "personal-oauth-cleanup": 0, "chat": 0, "apps": 0, "recaps": "disabled", "slack-livebridge": 0, "encrypted-work": "disabled"}
    with Session(engine) as session:
        assert session.get(WorkerHeartbeat, "export-completed")
    assert "secret-customer-document" not in caplog.text
