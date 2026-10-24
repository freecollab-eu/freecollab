"""Disposable visits backed by the normal product database and authorization.

This service is usable only in a separately configured sample-data instance.
There are no shared demonstration credentials and no alternate content API.
"""
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import time
from uuid import uuid4

from fastapi import HTTPException
import jwt
from sqlalchemy import and_, delete, func, or_, select as sql_select
from sqlmodel import SQLModel, select

from ..config import settings
from ..models.public_demo import PublicDemoVisit
from ..models.session import ActiveSession
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from .auth import track_session
from .storage import StorageService


def require_demo_instance(session=None):
    """Fail before any query if a caller points at an operator's installation."""
    if not settings.demo_instance:
        raise HTTPException(404, "Not found")
    # Settings is normally validated before engine creation; repeat this guard
    # for direct callers and tests that intentionally mutate process settings.
    from .public_demo_boundary import assert_public_demo_safe_configuration
    assert_public_demo_safe_configuration()
    expected = Path(settings.demo_data_path).resolve() / "public-demo.sqlite"
    if session is not None:
        url = session.get_bind().url
        if (url.drivername != "sqlite" or not url.database or url.query
                or Path(url.database).resolve() != expected):
            raise HTTPException(503, "The sample-data installation is unavailable")
    return expected


def get_active_demo_visit(session, user_id, visit_id=None, now=None):
    require_demo_instance(session)
    now = now or datetime.utcnow()
    row = session.exec(select(PublicDemoVisit).where(PublicDemoVisit.user_id == user_id)
                       .execution_options(populate_existing=True)).first()
    actor = session.exec(select(User.role, User.is_blocked).where(User.id == user_id)).first()
    if (row is None or row.revoked_at is not None or row.expires_at <= now
            or visit_id is not None and row.id != visit_id
            or actor is None or actor[0] != "user" or actor[1]):
        raise HTTPException(401, "This example session ended. Start a new example workspace.")
    return row


def _owned_rows(session, visit):
    """Find this visit's graph, including descendants without ownership columns.

    Every collaborator belongs only to its own visit. Generic FK traversal keeps
    additive product tables in the cleanup graph, without selecting other visits
    or disabling database constraints. Only identity/routing columns are read.
    """
    users = set(json.loads(visit.participant_ids_json))
    if visit.user_id not in users or len(users) > 8 or any(not re.fullmatch(r"[a-f0-9]{32}", x) for x in users):
        raise HTTPException(503, "The sample workspace could not be cleaned safely")
    channels = set(session.exec(select(WorkspaceChannel.channel_id).where(
        WorkspaceChannel.workspace_id == visit.workspace_id)).all())
    session_tokens = set(session.exec(select(ActiveSession.token_hash).where(ActiveSession.user_id.in_(users))).all())
    tables = list(SQLModel.metadata.sorted_tables)
    rows = {table.name: {} for table in tables}
    columns = {}
    for table in tables:
        names = set(column.name for column in table.primary_key.columns)
        names.update(fk.parent.name for fk in table.foreign_keys)
        names.update(fk.column.name for target in tables for fk in target.foreign_keys if fk.column.table is table)
        if table.name in {"useravatar", "avatarerasure"}: names.add("blob_id")
        columns[table.name] = [column for column in table.columns if column.name in names]

    def collect(table, conditions):
        if not conditions:
            return False
        changed = False
        records = session.execute(sql_select(*columns[table.name]).where(or_(*conditions))).mappings()
        keys = [column.name for column in table.primary_key.columns]
        for record in records:
            identity = tuple(record[key] for key in keys)
            if identity not in rows[table.name]:
                rows[table.name][identity] = dict(record)
                changed = True
        return changed

    actor_columns = {"user_id", "actor_id", "author_id", "created_by", "updated_by", "owner_id", "uploader_id"}
    for table in tables:
        conditions = []
        if table.name == "user": conditions.append(table.c.id.in_(users))
        if table.name == "workspace": conditions.append(table.c.id == visit.workspace_id)
        if table.name == "channel": conditions.append(table.c.id.in_(channels))
        if table.name == "revokedtoken" and session_tokens:
            conditions.append(table.c.token_hash.in_(session_tokens))
        for column in table.columns:
            if column.name == "workspace_id": conditions.append(column == visit.workspace_id)
            elif column.name == "channel_id" and channels: conditions.append(column.in_(channels))
            elif column.name in actor_columns: conditions.append(column.in_(users))
        collect(table, conditions)

    for _ in range(len(tables)):
        changed = False
        for table in tables:
            conditions = []
            for fk in table.foreign_keys:
                values = {record.get(fk.column.name) for record in rows[fk.column.table.name].values()}
                values.discard(None)
                if values: conditions.append(fk.parent.in_(values))
            changed = collect(table, conditions) or changed
        if not changed:
            break
    else:
        raise HTTPException(503, "The sample workspace could not be cleaned safely")
    return tables, rows


def _purge_visit(session, visit):
    tables, rows = _owned_rows(session, visit)
    file_ids = [record["id"] for record in rows.get("file", {}).values()]
    file_ids.extend(record["blob_id"] for name in ("useravatar", "avatarerasure")
                    for record in rows.get(name, {}).values())
    for table in reversed(tables):
        pending = dict(rows[table.name])
        keys = list(table.primary_key.columns)
        self_keys = [fk for fk in table.foreign_keys if fk.column.table is table]
        while pending:
            referenced = set()
            if self_keys:
                index = {}
                for identity, record in pending.items():
                    for fk in self_keys:
                        index.setdefault((fk.column.name, record[fk.column.name]), set()).add(identity)
                for record in pending.values():
                    for fk in self_keys:
                        referenced.update(index.get((fk.column.name, record[fk.parent.name]), ()))
            leaves = [identity for identity in pending if identity not in referenced]
            if not leaves:
                # An impossible/corrupt cycle is not permission to disable FKs
                # or erase unrelated rows. Roll back this complete visit batch.
                raise HTTPException(503, "The sample workspace could not be cleaned safely")
            for offset in range(0, len(leaves), 100):
                terms = [and_(*(column == value for column, value in zip(keys, identity)))
                         for identity in leaves[offset:offset + 100]]
                session.execute(delete(table).where(or_(*terms)))
            for identity in leaves:
                pending.pop(identity)
    return file_ids


def _remove_orphan_files(session, maximum=200):
    root = Path(settings.storage_path)
    if not root.exists():
        return
    retained = set(session.execute(sql_select(SQLModel.metadata.tables["file"].c.id)).scalars())
    for name in ("useravatar", "avatarerasure"):
        if name in SQLModel.metadata.tables:
            retained.update(session.execute(sql_select(SQLModel.metadata.tables[name].c.blob_id)).scalars())
    storage = StorageService(root)
    count = 0
    for path in root.iterdir():
        if count >= maximum:
            break
        # A normal upload writes the blob before committing its metadata. Never
        # mistake an in-flight upload from another visit for an abandoned file.
        try:
            old_enough = path.stat(follow_symlinks=False).st_mtime <= time.time() - 600
        except FileNotFoundError:
            continue
        if re.fullmatch(r"[a-f0-9]{32}", path.name) and path.name not in retained and old_enough:
            storage.delete_file_sync(path.name)
            count += 1


def cleanup_expired_visits(session, now=None, limit=40):
    """Commit bounded, FK-aware cleanup using a fresh dedicated-demo session."""
    require_demo_instance(session)
    if session.new or session.dirty or session.deleted:
        raise ValueError("Demo cleanup requires a fresh session")
    now = now or datetime.utcnow()
    live_session = sql_select(ActiveSession.id).where(
        ActiveSession.user_id == PublicDemoVisit.user_id,
        ActiveSession.revoked_at.is_(None), ActiveSession.expires_at > now,
    ).exists()
    visits = list(session.exec(select(PublicDemoVisit).where(or_(
        PublicDemoVisit.expires_at <= now, PublicDemoVisit.revoked_at.is_not(None), ~live_session,
    )).order_by(PublicDemoVisit.created_at).limit(min(40, max(1, limit)))).all())
    files = []
    try:
        for visit in visits:
            files.extend(_purge_visit(session, visit))
        session.commit()
    except BaseException:
        session.rollback()
        raise
    storage = StorageService(settings.storage_path)
    for file_id in files:
        storage.delete_file_sync(file_id)
    _remove_orphan_files(session)
    session.commit()
    return len(visits)


def create_demo_session(session, request=None):
    require_demo_instance(session)
    cleanup_expired_visits(session)
    # Serialize capacity and seeding with other creators/cleanup on SQLite.
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    total = session.exec(select(func.count()).select_from(PublicDemoVisit)).one()
    if total >= settings.demo_max_visits:
        session.rollback()
        raise HTTPException(429, "The example workspace is busy. Please try again shortly.")
    now = datetime.utcnow().replace(microsecond=0)
    expires_at = now + timedelta(minutes=settings.demo_session_minutes)
    ids = [uuid4().hex for _ in range(4)]
    visit_id, workspace_id = uuid4().hex, uuid4().hex
    for identifier, name in zip(ids, ("Alex Morgan", "Mira Sol", "Elias Vik", "Noa Berg")):
        session.add(User(id=identifier, name=name, email=identifier + "@demo.invalid",
                         password_hash="!public-demo-no-password", role="user", created_at=now))
    session.add(Workspace(id=workspace_id, name="Studio Nord", created_at=now))
    session.flush()
    for index, identifier in enumerate(ids):
        session.add(WorkspaceMember(workspace_id=workspace_id, user_id=identifier,
                                    role="owner" if index == 0 else "member"))
    session.flush()
    files = []
    try:
        from .public_demo_seed import seed_demo_workspace
        seeded = seed_demo_workspace(session, visit_id=visit_id, user_id=ids[0],
                                     workspace_id=workspace_id, participant_ids=ids, now=now)
        files = seeded.get("file_ids", [])
        visit = PublicDemoVisit(id=visit_id, user_id=ids[0], workspace_id=workspace_id,
                                channel_id=seeded["channel_id"], participant_ids_json=json.dumps(ids),
                                created_at=now, expires_at=expires_at)
        session.add(visit)
        session.flush()
        token = jwt.encode({"sub": ids[0], "iat": now, "exp": expires_at,
                            "jti": uuid4().hex, "av": 0, "amr": [], "demo_visit_id": visit_id},
                           settings.jwt_secret, algorithm="HS256")
        track_session(token, session, request=request, info={"user_agent": "Public product example"})
        return {"session": {"access_token": token, "token_type": "bearer", "user_id": ids[0],
                            "public_key": "", "encrypted_private_key": None},
                "workspace_id": workspace_id, "channel_id": seeded["channel_id"],
                "visit_id": visit_id, "expires_at": expires_at.replace(tzinfo=timezone.utc).isoformat(),
                "examples": seeded.get("example_ids", {})}
    except BaseException:
        session.rollback()
        storage = StorageService(settings.storage_path)
        for file_id in files:
            storage.delete_file_sync(file_id)
        raise


def revoke_demo_session(session, user_id, visit_id):
    if not isinstance(visit_id, str) or not re.fullmatch(r"[a-f0-9]{32}", visit_id):
        raise HTTPException(401, "This example session is unavailable")
    visit = get_active_demo_visit(session, user_id, visit_id)
    now = datetime.utcnow()
    visit.revoked_at = now
    session.add(visit)
    actor = session.get(User, user_id)
    actor.is_blocked = True
    session.add(actor)
    for row in session.exec(select(ActiveSession).where(ActiveSession.user_id == user_id)).all():
        row.revoked_at = now
        session.add(row)
    session.commit()
