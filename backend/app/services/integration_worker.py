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

log = logging.getLogger("freecollab.integrations")


def run_once(engine, *, webhook_batch=None, proof_batch=None, sync_batch=None, workflow_batch=None, data_batch=None, push_batch=None):
    from .webhooks import deliver_pending
    from .otrust import process_pending
    from .remote_sync import process_pending as sync_pending
    from .workflow import run_pending as workflow_pending
    from .data_control import run_pending as data_pending
    from .push import run_pending as push_pending

    batches = (
        ("webhooks", settings.webhooks_enabled, webhook_batch or (lambda session: deliver_pending(session, limit=2))),
        ("otrust", settings.otrust_enabled, proof_batch or (lambda session: process_pending(session, limit=2))),
        ("sync", getattr(settings, "sync_enabled", False), sync_batch or (lambda session: sync_pending(session, limit=2))),
        ("workflows", settings.workflows_enabled, workflow_batch or (lambda session: workflow_pending(session, limit=5))),
        ("data", settings.data_controls_enabled, data_batch or (lambda session: data_pending(session, limit=1))),
        ("push", settings.push_enabled, push_batch or (lambda session: push_pending(session, limit=5))),
    )
    with Session(engine) as session:
        heartbeat = session.get(WorkerHeartbeat, "integrations") or WorkerHeartbeat(name="integrations")
        heartbeat.last_seen_at = datetime.utcnow()
        session.add(heartbeat)
        session.commit()
    result = {}
    for name, enabled, batch in batches:
        if not enabled:
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
