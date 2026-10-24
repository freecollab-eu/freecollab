"""Verified small native profile photos; never external image URLs.

Only bounded, 8-bit non-interlaced RGB/RGBA PNG is accepted. All chunks and
scanline filters are checked and the pixels are re-encoded without metadata.
The browser prepares JPEG/WebP/PNG photos locally before submitting this format.
"""
from datetime import datetime
from hashlib import sha256
import logging
import struct
from uuid import uuid4
import zlib

from fastapi import HTTPException
from sqlmodel import select

from ..models.avatar import AvatarErasure, UserAvatar
from ..models.channel import ChannelMember
from ..models.user import User
from ..models.workspace import WorkspaceMember
from . import storage
from .access import require_channel_member
from .request_guard import recheck_actor
from .sso_scope import current_workspace_scope
from .workspaces import require_workspace_member, users_blocked, visible_workspace_user_ids

MAX_AVATAR_BYTES = 1024 * 1024
MAX_AVATAR_SIDE = 512
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _invalid():
    return HTTPException(422, "Choose a valid PNG profile photo, up to 512 × 512 pixels and 1 MB.")


def _chunk(kind, data):
    return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)


def normalize_avatar_png(data: bytes):
    if not isinstance(data, bytes) or len(data) > MAX_AVATAR_BYTES or not data.startswith(PNG_SIGNATURE):
        raise _invalid()
    offset, dimensions, compressed = 8, None, bytearray()
    idat_started = idat_ended = ended = False
    while offset < len(data):
        if offset + 12 > len(data): raise _invalid()
        length = struct.unpack_from("!I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        if (length > MAX_AVATAR_BYTES or offset + 12 + length > len(data)
                or any(not (65 <= c <= 90 or 97 <= c <= 122) for c in kind)
                or kind[2] & 32): raise _invalid()
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from("!I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc: raise _invalid()
        offset += 12 + length
        if dimensions is None:
            if kind != b"IHDR" or length != 13: raise _invalid()
            width, height, depth, color, compression, filtering, interlace = struct.unpack("!IIBBBBB", payload)
            if not (1 <= width <= MAX_AVATAR_SIDE and 1 <= height <= MAX_AVATAR_SIDE
                    and depth == 8 and color in (2, 6) and compression == filtering == interlace == 0):
                raise _invalid()
            dimensions = (width, height, 3 if color == 2 else 4)
        elif kind == b"IDAT":
            if idat_ended: raise _invalid()
            idat_started = True
            compressed.extend(payload)
        elif kind == b"IEND":
            if length or not idat_started or offset != len(data): raise _invalid()
            ended = True
            break
        else:
            if idat_started: idat_ended = True
            # No alternate pixel interpretation, palettes, animations or
            # unknown critical chunks. Ancillary metadata is never retained.
            if not kind[0] & 32 or kind in (b"acTL", b"fcTL", b"fdAT", b"tRNS"):
                raise _invalid()
    if not ended or not dimensions: raise _invalid()
    width, height, bpp = dimensions
    stride = width * bpp
    expected = (stride + 1) * height
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, expected + 1)
        if (len(raw) != expected or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail): raise _invalid()
    except zlib.error as error:
        raise _invalid() from error
    pixels, prior = bytearray(), bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        method = raw[start]
        if method > 4: raise _invalid()
        row = bytearray(raw[start + 1:start + 1 + stride])
        for x in range(stride):
            left, above = (row[x - bpp] if x >= bpp else 0), prior[x]
            upper_left = prior[x - bpp] if x >= bpp else 0
            if method == 1: predictor = left
            elif method == 2: predictor = above
            elif method == 3: predictor = (left + above) // 2
            elif method == 4:
                p = left + above - upper_left
                a, b, c = abs(p - left), abs(p - above), abs(p - upper_left)
                predictor = left if a <= b and a <= c else above if b <= c else upper_left
            else: predictor = 0
            row[x] = (row[x] + predictor) & 255
        pixels.append(0)
        pixels.extend(row)
        prior = row
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2 if bpp == 3 else 6, 0, 0, 0)
    normalized = PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(pixels)) + _chunk(b"IEND", b"")
    if len(normalized) > MAX_AVATAR_BYTES: raise _invalid()
    return normalized, width, height


def set_avatar_bytes(session, user_id: str, data: bytes) -> UserAvatar:
    """Seed/upload a verified private blob in the caller's transaction; no commit."""
    data, width, height = normalize_avatar_png(data)
    actor = session.exec(select(User.id, User.is_blocked).where(User.id == user_id)).first()
    if not actor or actor[1]: raise HTTPException(404, "Account not found")
    existing = session.get(UserAvatar, user_id)
    old_blob = existing.blob_id if existing else None
    blob_id = uuid4().hex
    storage.storage_service.save_file_sync(blob_id, data)
    try:
        if old_blob:
            session.add(AvatarErasure(blob_id=old_blob, user_id=user_id))
        row = existing or UserAvatar(user_id=user_id, blob_id=blob_id, size=len(data), width=width, height=height,
                                      sha256=sha256(data).hexdigest())
        row.blob_id, row.size, row.width, row.height = blob_id, len(data), width, height
        row.sha256, row.updated_at = sha256(data).hexdigest(), datetime.utcnow()
        session.add(row)
        session.flush()
        return row
    except BaseException:
        storage.storage_service.delete_file_sync(blob_id)
        raise


def remove_avatar(session, user_id: str):
    row = session.get(UserAvatar, user_id)
    if row:
        session.add(AvatarErasure(blob_id=row.blob_id, user_id=user_id))
        session.delete(row)
    return row is not None


def erase_pending_avatar_blobs(session, maximum=20):
    """Return the count of committed erasure records completed in this batch."""
    rows = session.exec(select(AvatarErasure).order_by(AvatarErasure.created_at).limit(min(20, max(1, maximum)))).all()
    erased = 0
    for row in rows:
        if session.exec(select(UserAvatar.user_id).where(UserAvatar.blob_id == row.blob_id)).first():
            continue
        try:
            storage.storage_service.delete_file_sync(row.blob_id)
        except Exception:
            logging.getLogger(__name__).warning("A removed profile photo awaits private storage erasure")
            continue  # Durable tombstone is retried by the normal worker.
        session.delete(row)
        erased += 1
    session.commit()
    return erased


def require_avatar_reader(session, actor_id, target_id, workspace_id=None):
    """Fresh grants only: a historical/dangling channel member conveys no access."""
    recheck_actor(session, actor_id)
    target = session.exec(select(User.id, User.is_blocked).where(User.id == target_id)).first()
    if not target or target[1]: raise HTTPException(404, "Profile photo not found")
    if actor_id == target_id: return
    if users_blocked(session, actor_id, target_id): raise HTTPException(404, "Profile photo not found")
    scope = current_workspace_scope(session)
    if workspace_id and scope and workspace_id != scope:
        raise HTTPException(404, "Profile photo not found")
    chosen_workspace = workspace_id or scope
    other = WorkspaceMember.__table__.alias("avatar_peer")
    query = select(WorkspaceMember.workspace_id).join(other, other.c.workspace_id == WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == actor_id, other.c.user_id == target_id)
    if chosen_workspace: query = query.where(WorkspaceMember.workspace_id == chosen_workspace)
    for identifier in session.exec(query).all():
        try:
            require_workspace_member(session, identifier, actor_id)
            require_workspace_member(session, identifier, target_id)
            if target_id not in visible_workspace_user_ids(session, identifier, actor_id): continue
            return
        except HTTPException as error:
            if error.status_code not in (403, 404): raise
    # Channel-only installations and explicitly shared Connect channels retain
    # their normal current access rules. A supplied workspace is a hard scope.
    other_channel = ChannelMember.__table__.alias("avatar_channel_peer")
    channels = session.exec(select(ChannelMember.channel_id).join(other_channel,
        other_channel.c.channel_id == ChannelMember.channel_id).where(
        ChannelMember.user_id == actor_id, other_channel.c.user_id == target_id)).all()
    for identifier in channels:
        try:
            if chosen_workspace:
                from ..models.workspace import WorkspaceChannel
                mapping = session.get(WorkspaceChannel, identifier)
                if not mapping or mapping.workspace_id != chosen_workspace: continue
            require_channel_member(session, identifier, actor_id)
            require_channel_member(session, identifier, target_id)
            return
        except HTTPException as error:
            if error.status_code not in (403, 404): raise
    raise HTTPException(404, "Profile photo not found")
