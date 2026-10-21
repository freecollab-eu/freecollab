"""Operator commands; run from backend with the service's DATABASE_URL."""
import argparse
import json

from sqlmodel import Session, select

from .database import engine
from .models.user import User
from . import models


def bootstrap_admin(email: str, session: Session) -> None:
    """Promote one registered account, using operator access to the database."""
    if session.exec(select(User).where(User.role == "admin")).first():
        raise ValueError("An admin already exists; use the admin API to manage roles")
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        raise ValueError("Register the intended account before bootstrapping an admin")
    if user.is_blocked:
        raise ValueError("A blocked account cannot become the first admin")
    user.role = "admin"
    session.add(user)
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin", help="Set the first admin locally")
    bootstrap.add_argument("--email", required=True, help="Email of the registered account")
    migration = commands.add_parser("migrate-messages", help="Audit/rotate stored message encryption; stop writers and back up first")
    migration.add_argument("--apply", action="store_true", help="Commit only after every live message authenticates (default: dry run)")
    args = parser.parse_args()
    try:
        with Session(engine) as session:
            if args.command == "bootstrap-admin":
                bootstrap_admin(args.email, session)
            elif args.command == "migrate-messages":
                from .crypto_migration import migrate_messages
                print(json.dumps(migrate_messages(session, apply=args.apply), sort_keys=True))
                return
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print("First admin configured")


if __name__ == "__main__":
    main()
