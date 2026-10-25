"""Independent bounded worker lane for reviewed AI jobs.

Run alongside the API and normal integration worker, never instead of them.
Public example installations deliberately perform no external AI work.
"""
import argparse
import signal
from threading import Event

from ..config import settings
from .ai_runtime import run_pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--batch", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.interval <= 60 or not 1 <= args.batch <= 5:
        parser.error("Use interval1–60 seconds and batch1–5")
    from .. import main as application  # noqa: F401 -- register full additive graph
    from ..database import engine, init_db
    from ..models.operations import WorkerHeartbeat
    from datetime import datetime
    from sqlmodel import Session
    init_db()
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        if getattr(settings, "copilot_enabled", False) and not getattr(settings, "demo_instance", False):
            with Session(engine) as session:
                heartbeat = session.get(WorkerHeartbeat, "ai-runtime") or WorkerHeartbeat(name="ai-runtime")
                heartbeat.last_seen_at = datetime.utcnow(); session.add(heartbeat); session.commit()
            run_pending(engine, limit=args.batch)
        if args.once:
            return
        stop.wait(args.interval)


if __name__ == "__main__":
    main()
