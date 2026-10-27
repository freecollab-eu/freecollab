"""Admin API - Role-based management system
Roles:
- admin: Manage users and roles, review channels, pause access and moderate messages
- moderator: Pause/block regular users and moderate messages, without role management
- user: Regular user, no admin access
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, func
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from typing import Optional
import uuid

from ..database import get_session
from ..services.sso_scope import current_workspace_scope, require_unrestricted
from ..services.auth import get_current_user, hash_password, validate_password, normalize_email
from ..services.request_guard import lock_current_session, recheck_actor
from ..models.account_security import AccountSecurity
from ..models.user import User
from ..models.channel import Channel
from ..models.message import Message
from ..models.post import Post
from ..models.file import File
from ..realtime.socket import broadcast_to_channel

router = APIRouter(prefix="/admin", tags=["admin"])

MAX_ADMINS = 5


class CreateUserRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=12)
    role: str = "user"

    _validate_password = field_validator("password")(validate_password)
    _normalize_email = field_validator("email", mode="before")(normalize_email)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() if isinstance(value, str) else value


def require_staff(current_user: User = Depends(get_current_user), session=Depends(get_session)):
    """Requires admin or moderator role"""
    require_unrestricted(session)
    if current_user.role not in ["admin", "moderator"]:
        raise HTTPException(status_code=403, detail="Staff access required")
    return current_user


def require_admin(current_user: User = Depends(get_current_user), session=Depends(get_session)):
    """Requires admin role only"""
    require_unrestricted(session)
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def _lock_user_management(session, actor_id: str, *, target_id: str | None = None, admin_only: bool = True):
    """Serialize installation privilege edits, then verify current authority.

    Use the same ordered administrator locks as account closure. This also
    keeps concurrent creations/promotions inside the installation admin limit.
    Hashing a new password must finish before these locks and checks.
    """
    administrators = session.exec(select(User.id).where(User.role == "admin")).all()
    identifiers = set(administrators) | {actor_id}
    if target_id is not None:
        identifiers.add(target_id)
    for identifier in sorted(identifiers):
        session.execute(update(User).where(User.id == identifier).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == actor_id)
                    .values(auth_version=AccountSecurity.auth_version))
    lock_current_session(session, actor_id)
    session.expire_all()
    recheck_actor(session, actor_id)
    actor = session.get(User, actor_id, populate_existing=True)
    return require_admin(actor, session) if admin_only else require_staff(actor, session)


@router.get("/me")
def get_my_role(current_user: User = Depends(get_current_user), session=Depends(get_session)):
    """Get current users role info"""
    role = "user" if current_workspace_scope(session) is not None else current_user.role
    return {
        "id": current_user.id,
        "name": current_user.name,
        "role": role,
        "is_staff": role in ["admin", "moderator"],
        "is_admin": role == "admin"
    }


@router.get("/stats")
def get_stats(
    session: Session = Depends(get_session),
    staff: User = Depends(require_staff)
):
    """Get system statistics"""
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    
    total_users = session.exec(select(func.count(User.id))).one()
    users_today = session.exec(
        select(func.count(User.id)).where(User.created_at >= day_ago)
    ).one()
    blocked_users = session.exec(
        select(func.count(User.id)).where(User.is_blocked == True)
    ).one()
    admin_count = session.exec(
        select(func.count(User.id)).where(User.role == "admin")
    ).one()
    moderator_count = session.exec(
        select(func.count(User.id)).where(User.role == "moderator")
    ).one()
    
    total_channels = session.exec(select(func.count(Channel.id))).one()
    total_messages = session.exec(select(func.count(Message.id))).one()
    messages_today = session.exec(
        select(func.count(Message.id)).where(Message.created_at >= day_ago)
    ).one()
    messages_week = session.exec(
        select(func.count(Message.id)).where(Message.created_at >= week_ago)
    ).one()
    total_posts = session.exec(select(func.count(Post.id))).one()
    total_files = session.exec(select(func.count(File.id))).one()
    total_storage = session.exec(select(func.sum(File.size))).one() or 0
    
    return {
        "users": {
            "total": total_users,
            "today": users_today,
            "blocked": blocked_users,
            "admins": admin_count,
            "moderators": moderator_count,
            "max_admins": MAX_ADMINS
        },
        "channels": {"total": total_channels},
        "messages": {
            "total": total_messages,
            "today": messages_today,
            "week": messages_week
        },
        "posts": {"total": total_posts},
        "files": {
            "total": total_files,
            "storage_bytes": total_storage
        }
    }


@router.get("/users")
def list_users(
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    session: Session = Depends(get_session),
    staff: User = Depends(require_staff)
):
    """List all users"""
    query = select(User)
    if search:
        query = query.where(
            (User.name.contains(search)) | (User.email.contains(search))
        )
    query = query.offset(skip).limit(limit)
    users = session.exec(query).all()
    
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "is_blocked": u.is_blocked,
            "created_at": u.created_at.isoformat()
        }
        for u in users
    ]


@router.post("/users")
def create_user(
    data: CreateUserRequest,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin)
):
    """Create a new user - admin only"""
    if data.role not in ["user", "moderator", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    # Password work can outlast a logout, block or demotion. Check again under
    # the same locks as role/block changes before committing a new identity.
    password_hash = hash_password(data.password)
    _lock_user_management(session, admin.id)

    # Check if email already exists
    existing = session.exec(select(User).where(func.lower(User.email) == data.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Check admin limit
    if data.role == "admin":
        current_admins = session.exec(
            select(func.count(User.id)).where(User.role == "admin")
        ).one()
        if current_admins >= MAX_ADMINS:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_ADMINS} admins allowed")
    
    user = User(
        id=str(uuid.uuid4()),
        name=data.name,
        email=data.email,
        password_hash=password_hash,
        role=data.role
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.exec(select(User).where(func.lower(User.email) == data.email)).first():
            raise HTTPException(status_code=400, detail="Email already registered")
        raise
    
    return {
        "message": f"User {data.name} created with role {data.role}",
        "id": user.id
    }


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: str,
    role: str,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin)
):
    """Update user role - only admins can do this"""
    if role not in ["user", "moderator", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be: user, moderator, or admin")

    admin = _lock_user_management(session, admin.id, target_id=user_id)
    
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    
    # Check admin limit
    if role == "admin" and user.role != "admin":
        current_admins = session.exec(
            select(func.count(User.id)).where(User.role == "admin")
        ).one()
        if current_admins >= MAX_ADMINS:
            raise HTTPException(
                status_code=400, 
                detail=f"Maximum {MAX_ADMINS} admins allowed. Demote an existing admin first."
            )
    
    old_role = user.role
    user.role = role
    session.add(user)
    session.commit()
    
    return {"message": f"User role changed from {old_role} to {role}"}


@router.patch("/users/{user_id}/block")
def toggle_block_user(
    user_id: str,
    session: Session = Depends(get_session),
    staff: User = Depends(require_staff)
):
    """Block/unblock (pause) a user - staff can do this"""
    staff = _lock_user_management(session, staff.id, target_id=user_id, admin_only=False)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.id == staff.id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    
    # Moderators cannot block admins or other moderators
    if staff.role == "moderator" and user.role in ["admin", "moderator"]:
        raise HTTPException(status_code=403, detail="Moderators cannot block staff members")
    
    user.is_blocked = not user.is_blocked
    session.add(user)
    session.commit()
    
    status = "blocked" if user.is_blocked else "unblocked"
    return {"message": f"User {status}", "is_blocked": user.is_blocked}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin)
):
    """Refuse irreversible removal until a complete retention workflow exists."""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    raise HTTPException(
        status_code=409,
        detail="Account deletion requires a retention workflow. Pause the account to revoke access.",
    )


@router.get("/channels")
def list_channels(
    skip: int = 0,
    limit: int = 50,
    session: Session = Depends(get_session),
    staff: User = Depends(require_staff)
):
    """List all channels"""
    channels = session.exec(select(Channel).offset(skip).limit(limit)).all()
    
    return [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "created_at": c.created_at.isoformat()
        }
        for c in channels
    ]


@router.delete("/channels/{channel_id}")
def delete_channel(
    channel_id: str,
    session: Session = Depends(get_session),
    staff: User = Depends(require_staff)
):
    """Refuse incomplete deletion that could orphan membership and content."""
    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    
    raise HTTPException(
        status_code=409,
        detail="Channel deletion requires a retention workflow. Remove member access instead.",
    )


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: str,
    session: Session = Depends(get_session),
    staff: User = Depends(require_staff)
):
    """Hide moderated content without removing a referenced database row."""
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    from ..services.enterprise_governance import assert_deletion_allowed
    from ..services.request_guard import recheck_actor
    assert_deletion_allowed(session, message.channel_id, author_id=message.author_id)
    recheck_actor(session, staff.id)
    staff = session.get(User, staff.id, populate_existing=True)
    require_staff(staff, session)
    message.deleted = True
    from ..services.message_references import store_references
    store_references(session, message, [])
    message.mentions = '[]'
    message.content = "[Message deleted]"
    message.encrypted = False
    session.add(message)
    session.commit()
    await broadcast_to_channel(message.channel_id, "message_deleted", {"id": message.id})
    
    return {"message": "Message deleted"}
