"""Dedicated SFU authorization/revocation loop; run separately from slow integrations."""
from threading import Event
import logging
import signal
from sqlmodel import Session
from .sfu import reconcile


def main():
    from .. import main as application  # noqa: F401 - register complete model graph
    from ..database import engine, init_db
    init_db(); stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            with Session(engine) as session: reconcile(session)
        except Exception as error:
            logging.error('SFU reconciliation failed (%s)', type(error).__name__)
        stopped.wait(5)


if __name__ == '__main__': main()
