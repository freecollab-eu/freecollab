"""Poll explicitly requested integration work without sharing content keys.

Run one instance per supported single-replica installation. Each queue uses its
own database session and durable claim, so a provider failure does not prevent
the other integration from running. Nothing is submitted when its flag is off.
"""
import argparse
from datetime import datetime
import logging
import signal
from threading import Event

from sqlmodel import Session

from ..config import settings
from ..models.operations import WorkerHeartbeat
from ..models import avatar  # noqa: F401 -- additive private photo erasure queue
from ..models import content_completion, encrypted_work, slack_livebridge, ai_runtime, media_capture  # noqa: F401 -- additive collaboration queues
from ..models import app_platform, calendar_delivery, chat_productivity, file, file_version, integration_content, notification, native_calls, native_push, personal_oauth, remote_sync, slack_import, user, channel, workspace  # noqa: F401 -- register queue tables before schema creation

log = logging.getLogger("freecollab.integrations")


def run_once(engine, *, webhook_batch=None, proof_batch=None, sync_batch=None, calendar_batch=None, workflow_batch=None, data_batch=None, push_batch=None, productivity_batch=None, apps_batch=None, recaps_batch=None):
    demo_instance = bool(getattr(settings, "demo_instance", False))
    if demo_instance:
        # The public example uses the same native worker, on its own database.
        # Always-on app outboxes can perform HTTP even when provider flags are
        # off, so the demo needs an explicit lane boundary as well as API guards.
        from .public_demo import require_demo_instance
        with Session(engine) as session:
            require_demo_instance(session)
    from .webhooks import deliver_pending
    from .otrust import process_pending
    from .remote_sync import process_pending as sync_pending
    from .workflow import run_pending as workflow_pending
    from .data_control import run_pending as data_pending
    from .push import run_pending as push_pending
    from .native_push import run_pending as native_push_pending
    from .native_calls import expire_devices as expire_native_call_devices
    from .slack_import import expire_previews as expire_slack_previews
    from .personal_oauth import reap_grants as reap_personal_oauth
    from .calendar_delivery import run_pending as calendar_pending
    from .chat_productivity import run_pending as productivity_pending
    from .app_platform import run_pending as apps_pending, run_recaps
    from .avatars import erase_pending_avatar_blobs
    from .file_erasure import erase_pending_file_blobs
    from .slack_livebridge import run_pending as bridge_pending
    from .encrypted_work import run_pending as encrypted_work_pending

    batches = (
        ("avatar-cleanup", True, erase_pending_avatar_blobs),
        ("file-cleanup", True, erase_pending_file_blobs),
        ("webhooks", settings.webhooks_enabled, webhook_batch or (lambda session: deliver_pending(session, limit=2))),
        ("otrust", settings.otrust_enabled, proof_batch or (lambda session: process_pending(session, limit=2))),
        ("sync", getattr(settings, "sync_enabled", False), sync_batch or (lambda session: sync_pending(session, limit=2))),
        ("calendar-delivery", settings.sync_enabled, calendar_batch or (lambda session: calendar_pending(session, limit=2))),
        ("workflows", settings.workflows_enabled, workflow_batch or (lambda session: workflow_pending(session, limit=5))),
        ("data", settings.data_controls_enabled, data_batch or (lambda session: data_pending(session, limit=1))),
        ("push", settings.push_enabled, push_batch or (lambda session: push_pending(session, limit=5))),
        ("native_push", settings.native_push_enabled, lambda session: native_push_pending(session, limit=5)),
        ("native-call-cleanup", True, lambda session: expire_native_call_devices(session, limit=100)),
        ("slack-import-cleanup", True, lambda session: expire_slack_previews(session, limit=10)),
        ("personal-oauth-cleanup", True, lambda session: reap_personal_oauth(session, limit=100)),
        ("chat", True, productivity_batch or (lambda session: productivity_pending(session, limit=10))),
        ("apps", True, apps_batch or (lambda session: apps_pending(session, limit=2))),
        ("recaps", settings.copilot_enabled, recaps_batch or (lambda session: run_recaps(session, limit=1))),
        ("slack-livebridge", True, lambda session: bridge_pending(session, limit=10)),
        ("encrypted-work", getattr(settings, "connect_federation_enabled", False), lambda session: encrypted_work_pending(session, limit=2)),
    )
    with Session(engine) as session:
        heartbeat = session.get(WorkerHeartbeat, "integrations") or WorkerHeartbeat(name="integrations")
        heartbeat.last_seen_at = datetime.utcnow()
        session.add(heartbeat)
        session.commit()
    result = {}
    demo_lanes = {"chat", "workflows", "native-call-cleanup", "slack-import-cleanup", "personal-oauth-cleanup", "avatar-cleanup", "file-cleanup"}
    for name, enabled, batch in batches:
        if not enabled or demo_instance and name not in demo_lanes:
            result[name] = "disabled"
            continue
        try:
            with Session(engine) as session:
                result[name] = batch(session)
        except Exception as exc:
            # Provider exceptions can contain credentials or destination URLs.
            log.error("%s batch failed (%s)", name, type(exc).__name__)
            result[name] = "error"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if not 1 <= args.interval <= 60:
        parser.error("interval must be between 1 and 60 seconds")
    # Register the complete additive SQLModel graph before creating tables.
    from .. import main as application  # noqa: F401
    from ..database import engine, init_db
    init_db()
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        run_once(engine)
        if args.once:
            return
        stop.wait(args.interval)


if __name__ == "__main__":
    main()
