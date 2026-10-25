"""Coalesced encrypted-work notifications: opaque sequences and generic text only."""
from datetime import datetime, timedelta
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import delete, update, or_
from sqlmodel import select

from ..models.channel import ChannelMember
from ..models.connect_federation import FederatedConnection
from ..models.encrypted_work import EncryptedWorkAuthority, EncryptedWorkNotice, EncryptedWorkPointer, EncryptedWorkRemoteCursor
from ..models.notification import Notification, NotificationPreference
from ..models.secure_channel import SecureChannel
from ..services.access import require_channel_member
from ..services.workspaces import require_direct_message_writable, lock_workspace

AUTHORITY_PROTOCOL = "freecollab-work-authority/1"


def authority_id(channel_id, event_id):
    return sha256((channel_id + "\0" + event_id).encode()).hexdigest()


def record_authority(session, channel_id, event, actor_id):
    """Caller holds the channel write lock and has rechecked the current actor."""
    from ..models.secure_channel import MatrixAccount
    member = require_channel_member(session, channel_id, actor_id)
    binding = session.get(SecureChannel, channel_id)
    identity = session.get(MatrixAccount, actor_id)
    event_id = event.get("event_id")
    if (not binding or binding.status != "active" or not identity or event.get("room_id") != binding.matrix_room_id
            or event.get("sender") != identity.matrix_user_id or not isinstance(event_id, str) or not event_id.startswith("$") or len(event_id) > 1024):
        raise HTTPException(409, "The verified encrypted event or authority scope changed")
    key = authority_id(channel_id, event_id)
    previous = session.get(EncryptedWorkAuthority, key)
    if previous:
        if previous.room_id != binding.matrix_room_id or previous.matrix_user_id != identity.matrix_user_id:
            raise HTTPException(409, "The encrypted authority receipt changed identity")
        return previous
    row = EncryptedWorkAuthority(id=key, channel_id=channel_id, matrix_event_id=event_id,
        room_id=binding.matrix_room_id, matrix_user_id=identity.matrix_user_id,
        role="admin" if member.role == "admin" else "member", published_by=actor_id)
    session.add(row)
    return row


def authority_payload(session, channel_id, event_id, *, issuer):
    row = session.get(EncryptedWorkAuthority, authority_id(channel_id, event_id))
    binding = session.get(SecureChannel, channel_id)
    if not row or not binding or binding.status != "active" or binding.matrix_room_id != row.room_id:
        raise HTTPException(404, "No verified publication-time authority exists for this event")
    return {"protocol": AUTHORITY_PROTOCOL, "event_id": row.matrix_event_id, "room_id": row.room_id,
        "matrix_user_id": row.matrix_user_id, "role": row.role, "issuer": issuer,
        "published_at": row.created_at.isoformat()}


def notice(session, channel_id, user_id, sequence, source, *, actor_id=None):
    if user_id == actor_id or type(sequence) is not int or sequence < 1 or source not in {"local", "remote"}:
        return False
    try:
        require_channel_member(session, channel_id, user_id)
        require_direct_message_writable(session, channel_id, user_id)
    except HTTPException:
        return False
    identifier = sha256(("encrypted-work:" + channel_id + "\0" + user_id + "\0" + source).encode()).hexdigest()
    progress = session.get(EncryptedWorkNotice, identifier)
    if progress and progress.sequence >= sequence:
        return False
    if progress is None:
        progress = EncryptedWorkNotice(id=identifier, channel_id=channel_id, user_id=user_id, source=source)
    progress.sequence = sequence; session.add(progress)
    preference = session.exec(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).first()
    if preference and (not preference.in_app or not preference.system):
        return False
    row = session.get(Notification, "encrypted-work-" + identifier)
    if row is None:
        row = Notification(id="encrypted-work-" + identifier, user_id=user_id, type="system",
            entity_type="encrypted_work", entity_id=identifier, title="Encrypted work updated")
    row.read = False; row.read_at = None; row.created_at = datetime.utcnow()
    row.body = None; row.actor_id = None; row.action_url = None
    session.add(row)
    return True


def notify_local(session, channel_id, sequence, actor_id):
    for user_id in session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel_id)).all():
        notice(session, channel_id, user_id, sequence, "local", actor_id=actor_id)


def run_pending(session, limit=2):
    """Poll pinned peers for opaque index progress; no content or provider keys."""
    from . import connect_federation as federation, federated_work
    try:
        federation.configuration()
    except HTTPException:
        return 0
    connections = session.exec(select(FederatedConnection).outerjoin(EncryptedWorkRemoteCursor,
        EncryptedWorkRemoteCursor.connection_id == FederatedConnection.id)
        .where(FederatedConnection.status == "active")
        .where(or_(EncryptedWorkRemoteCursor.connection_id.is_(None), EncryptedWorkRemoteCursor.checked_at <= datetime.utcnow() - timedelta(seconds=15)))
        .order_by(EncryptedWorkRemoteCursor.checked_at.asc().nullsfirst(), FederatedConnection.id).limit(max(1, min(limit, 5)))).all()
    processed = 0
    for connection in connections:
        if not federation.authority_valid(session, connection):
            continue
        identifier, workspace, peer = connection.id, connection.workspace_id, connection.peer_origin
        binding = session.get(SecureChannel, connection.channel_id)
        room = binding.matrix_room_id if binding and binding.status == "active" else None
        if not room:
            continue
        cursor = session.get(EncryptedWorkRemoteCursor, connection.id)
        after = cursor.sequence if cursor else 0
        try:
            page = federation.call_peer(session, connection.peer_origin, "work-index", {
                "connection_id": connection.id, "after": after, "limit": federated_work.PAGE_SIZE})
            page = federated_work.validate_remote_page(page, connection, after)
        except HTTPException:
            # A failing peer is revisited later; never log its response or keys.
            lock_workspace(session, workspace); session.expire_all()
            current = session.get(FederatedConnection, identifier)
            binding = session.get(SecureChannel, current.channel_id) if current else None
            if not current or current.status != "active" or current.workspace_id != workspace or current.peer_origin != peer or not federation.authority_valid(session, current) or not binding or binding.status != "active" or binding.matrix_room_id != room:
                session.rollback(); continue
            cursor = session.get(EncryptedWorkRemoteCursor, identifier)
            if cursor and cursor.sequence != after:
                session.rollback(); continue
            cursor = cursor or EncryptedWorkRemoteCursor(connection_id=identifier)
            cursor.checked_at = datetime.utcnow(); cursor.last_error = "peer_unavailable"; session.add(cursor); session.commit()
            continue
        lock_workspace(session, workspace); session.expire_all()
        current = session.get(FederatedConnection, identifier)
        binding = session.get(SecureChannel, current.channel_id) if current else None
        if not current or current.status != "active" or current.workspace_id != workspace or current.peer_origin != peer or not federation.authority_valid(session, current) or not binding or binding.status != "active" or binding.matrix_room_id != room:
            session.rollback(); continue
        cursor = session.get(EncryptedWorkRemoteCursor, connection.id)
        if cursor and cursor.sequence != after:
            session.rollback(); continue
        cursor = cursor or EncryptedWorkRemoteCursor(connection_id=connection.id)
        if page["next_after"] > after:
            for actor in session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == current.channel_id)).all():
                if federation.grant_valid(session, current, actor):
                    notice(session, current.channel_id, actor, page["next_after"], "remote")
        cursor.sequence = page["next_after"]; cursor.checked_at = datetime.utcnow(); cursor.last_error = None
        session.add(cursor); session.commit(); processed += 1
    return processed


def queue_status(session):
    """Bounded operator metadata; checking health never contacts/decrypts a peer."""
    now = datetime.utcnow()
    result = {"pending": 0, "processing": 0, "failed": 0, "scheduled": 0, "oldest_pending_seconds": 0}
    for connection in session.exec(select(FederatedConnection).where(FederatedConnection.status == "active").limit(10000)).all():
        cursor = session.get(EncryptedWorkRemoteCursor, connection.id)
        if cursor and cursor.last_error:
            result["failed"] += 1
        elif cursor and (now - cursor.checked_at).total_seconds() < 15:
            result["scheduled"] += 1
        else:
            result["pending"] += 1
            age = (now - (cursor.checked_at if cursor else connection.created_at)).total_seconds()
            result["oldest_pending_seconds"] = max(result["oldest_pending_seconds"], max(0, int(age)))
    return result


def erase_user_progress(session, user_id):
    """Private notices can be erased without removing other members' shared work."""
    for row in session.exec(select(EncryptedWorkNotice).where(EncryptedWorkNotice.user_id == user_id)).all():
        notification = session.get(Notification, "encrypted-work-" + row.id)
        if notification:
            session.delete(notification)
        session.delete(row)
    # Keep other members' verifiable shared history, without a live account FK.
    session.execute(update(EncryptedWorkPointer).where(EncryptedWorkPointer.published_by == user_id).values(published_by=None))
    session.execute(update(EncryptedWorkAuthority).where(EncryptedWorkAuthority.published_by == user_id).values(published_by=None))


def purge_channel(session, channel_id):
    for row in session.exec(select(EncryptedWorkNotice).where(EncryptedWorkNotice.channel_id == channel_id)).all():
        notification = session.get(Notification, "encrypted-work-" + row.id)
        if notification:
            session.delete(notification)
        session.delete(row)
    session.execute(delete(EncryptedWorkPointer).where(EncryptedWorkPointer.channel_id == channel_id))
    session.execute(delete(EncryptedWorkAuthority).where(EncryptedWorkAuthority.channel_id == channel_id))
    for connection in session.exec(select(FederatedConnection).where(FederatedConnection.channel_id == channel_id)).all():
        cursor = session.get(EncryptedWorkRemoteCursor, connection.id)
        if cursor:
            session.delete(cursor)
