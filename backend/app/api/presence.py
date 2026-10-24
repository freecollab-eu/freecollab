from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..database import get_session
from ..models.channel import Channel, ChannelMember
from ..models.user import User
from ..services.auth import get_current_user
from ..realtime.socket import get_user_presence, get_online_users
from ..services.access import require_channel_member
from ..services.workspaces import visible_user_ids, workspace_allows_channel

router = APIRouter(prefix="/presence", tags=["presence"])


@router.get("/users/{user_id}")
def get_presence(
    user_id: str,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get presence only for a person in the caller's authorized directory."""
    if user_id not in visible_user_ids(session, user.id):
        raise HTTPException(404, "Person is unavailable")
    presence = get_user_presence(user_id)
    return {
        "user_id": user_id,
        "status": presence["status"],
        "last_seen": presence["last_seen"],
        **({"custom_status": presence["custom_status"]} if presence.get("custom_status") else {}),
    }


@router.get("/online")
def list_online_users(
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Return online people visible to this account."""
    visible = visible_user_ids(session, user.id)
    online = [person for person in get_online_users(visible) if person in visible]
    return {"online_users": online, "count": len(online)}


@router.get("/channel/{channel_id}")
def get_channel_presence(
    channel_id: str,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Return online channel members to another member of that channel."""
    require_channel_member(session, channel_id, user.id)
    member_ids = set(session.exec(
        select(ChannelMember.user_id)
        .join(User, User.id == ChannelMember.user_id)
        .where(ChannelMember.channel_id == channel_id, User.is_blocked == False)
    ).all())
    visible = visible_user_ids(session, user.id)
    online = [user_id for user_id in get_online_users(member_ids & visible)
              if user_id in member_ids and user_id in visible and workspace_allows_channel(session, channel_id, user_id)]
    return {
        "channel_id": channel_id,
        "online_users": online,
        "count": len(online),
    }
