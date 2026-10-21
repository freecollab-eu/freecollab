from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..database import get_session
from ..models.reaction import Reaction
from ..models.message import Message
from ..services.auth import get_current_user
from ..services.access import require_channel_member, lock_channel_writes
from ..services.workspaces import require_direct_message_writable
from ..services.chat_notifications import require_native_channel
from ..realtime.socket import broadcast_to_channel
from uuid import uuid4

router = APIRouter(prefix="/reactions", tags=["reactions"])

ALLOWED_EMOJIS = {"👍", "❤️", "😂", "😮", "😢", "😡", "🎉", "🚀", "👀", "💯"}


class AddReaction(BaseModel):
    emoji: str


@router.post("/messages/{message_id}")
async def add_reaction(
    message_id: str,
    data: AddReaction,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Add a reaction to a message."""
    # Validate emoji
    if data.emoji not in ALLOWED_EMOJIS:
        raise HTTPException(400, f"Invalid emoji. Allowed: {ALLOWED_EMOJIS}")
    
    # Get message and verify channel membership
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    
    lock_channel_writes(session, message.channel_id)
    require_channel_member(session, message.channel_id, user.id)
    require_direct_message_writable(session, message.channel_id, user.id)
    require_native_channel(session, message.channel_id)
    
    # Check if user already reacted with this emoji
    existing = (
        session.query(Reaction)
        .filter(
            Reaction.message_id == message_id,
            Reaction.user_id == user.id,
            Reaction.emoji == data.emoji,
        )
        .first()
    )
    if existing:
        raise HTTPException(400, "Already reacted with this emoji")
    
    # Create reaction
    reaction = Reaction(
        id=uuid4().hex,
        message_id=message_id,
        user_id=user.id,
        emoji=data.emoji,
    )
    session.add(reaction)
    session.commit()
    
    # Broadcast to channel
    await broadcast_to_channel(
        message.channel_id,
        "reaction_added",
        {
            "message_id": message_id,
            "user_id": user.id,
            "emoji": data.emoji,
        },
    )
    
    return {"id": reaction.id, "emoji": data.emoji}


@router.delete("/messages/{message_id}/{emoji}")
async def remove_reaction(
    message_id: str,
    emoji: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Remove a reaction from a message."""
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    lock_channel_writes(session, message.channel_id)
    require_channel_member(session, message.channel_id, user.id)
    require_direct_message_writable(session, message.channel_id, user.id)
    require_native_channel(session, message.channel_id)
    reaction = (
        session.query(Reaction)
        .filter(
            Reaction.message_id == message_id,
            Reaction.user_id == user.id,
            Reaction.emoji == emoji,
        )
        .first()
    )
    if not reaction:
        raise HTTPException(404, "Reaction not found")
    
    session.delete(reaction)
    session.commit()
    
    # Broadcast to channel
    if message:
        await broadcast_to_channel(
            message.channel_id,
            "reaction_removed",
            {
                "message_id": message_id,
                "user_id": user.id,
                "emoji": emoji,
            },
        )
    
    return {"removed": True}


@router.get("/messages/{message_id}")
def get_reactions(
    message_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get all reactions for a message."""
    message = session.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    
    require_channel_member(session, message.channel_id, user.id)
    require_native_channel(session, message.channel_id)
    
    reactions = session.query(Reaction).filter(Reaction.message_id == message_id).all()
    
    # Group by emoji with user list
    grouped = {}
    for r in reactions:
        if r.emoji not in grouped:
            grouped[r.emoji] = {"emoji": r.emoji, "count": 0, "users": [], "me": False}
        grouped[r.emoji]["count"] += 1
        grouped[r.emoji]["users"].append(r.user_id)
        if r.user_id == user.id:
            grouped[r.emoji]["me"] = True
    
    return list(grouped.values())
