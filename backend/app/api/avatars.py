"""Native account photo upload, erasure and authenticated current-peer reads."""
from hashlib import sha256

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import update
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..models.avatar import UserAvatar
from ..models.user import User
from ..services import avatars, storage
from ..services.account_security import account_limit
from ..services.auth import get_current_user
from ..services.request_guard import recheck_actor
from ..services.sso_scope import require_unrestricted
from ..services.workforce_access import require_personal_account

router = APIRouter(tags=["profile photos"])


def writable_account(session, user_id):
    if settings.demo_instance:
        raise HTTPException(403, "Use your own installation to upload a personal profile photo. Public examples use sample photos.")
    require_unrestricted(session)
    require_personal_account(session, user_id)


def lock_account(session, user_id):
    session.execute(update(User).where(User.id == user_id).values(name=User.name))
    recheck_actor(session, user_id)
    writable_account(session, user_id)


@router.get("/users/{user_id}/avatar")
def get_avatar(user_id: str, workspace_id: str | None = None,
               user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    actor_id = user.id
    avatars.require_avatar_reader(session, actor_id, user_id, workspace_id)
    row = session.get(UserAvatar, user_id)
    if not row: raise HTTPException(404, "Profile photo not found")
    blob_id, expected_hash = row.blob_id, row.sha256
    try:
        data = storage.storage_service.read_file(blob_id, avatars.MAX_AVATAR_BYTES, expected_size=row.size)
    except (ValueError, OSError) as error:
        raise HTTPException(404, "Profile photo not found") from error
    # A remote blob read can outlast sign-out or membership revocation. Start a
    # fresh DB snapshot and check the actual grant and photo again before send.
    session.rollback()
    avatars.require_avatar_reader(session, actor_id, user_id, workspace_id)
    current = session.exec(select(UserAvatar.blob_id, UserAvatar.sha256).where(UserAvatar.user_id == user_id)).first()
    if (not current or current[0] != blob_id or current[1] != expected_hash
            or data is None or sha256(data).hexdigest() != expected_hash):
        raise HTTPException(404, "Profile photo not found")
    return Response(data, media_type="image/png", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="profile-photo.png"',
    })


@router.post("/account/avatar")
def upload_avatar(file: UploadFile = File(...), user: User = Depends(get_current_user),
                  session: Session = Depends(get_session)):
    writable_account(session, user.id)
    data = file.file.read(avatars.MAX_AVATAR_BYTES + 1)
    # Validate before spending a persistent write bucket or holding account lock.
    avatars.normalize_avatar_png(data)
    account_limit(session, "account-profile-photo", user.id, 30, 3600)
    lock_account(session, user.id)
    new_blob = None
    try:
        row = avatars.set_avatar_bytes(session, user.id, data)
        new_blob = row.blob_id
        recheck_actor(session, user.id)
        session.commit()
    except BaseException:
        session.rollback()
        if new_blob: storage.storage_service.delete_file_sync(new_blob)
        raise
    avatars.erase_pending_avatar_blobs(session)
    return {"has_avatar": True}


@router.delete("/account/avatar", status_code=204)
def delete_avatar(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    writable_account(session, user.id)
    account_limit(session, "account-profile-photo", user.id, 30, 3600)
    lock_account(session, user.id)
    avatars.remove_avatar(session, user.id)
    recheck_actor(session, user.id)
    session.commit()
    avatars.erase_pending_avatar_blobs(session)
    return Response(status_code=204)
