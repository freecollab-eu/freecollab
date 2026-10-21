from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..database import get_session
from ..models.read_receipt import ReadReceipt
from ..models.message import Message
from ..services.auth import get_current_user
from ..services.access import require_channel_member
from ..realtime.socket import broadcast_to_channel
from datetime import datetime

router = APIRouter(prefix="/read-receipts", tags=["read-receipts"])


class MarkRead(BaseModel):
    message_id: str


@router.post("/channels/{channel_id}")
async def mark_as_read(
    channel_id: str,
    data: MarkRead,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Mark messages as read up to a specific message."""
    require_channel_member(session, channel_id, user.id)
    
    # Verify message exists in channel
    message = session.get(Message, data.message_id)
    if not message or message.channel_id != channel_id:
        raise HTTPException(404, "Message not found in channel")
    
    # Upsert read receipt
    receipt = (
        session.query(ReadReceipt)
        .filter(
            ReadReceipt.channel_id == channel_id,
            ReadReceipt.user_id == user.id,
        )
        .first()
    )
    
    if receipt:
        receipt.last_read_message_id = data.message_id
        receipt.read_at = datetime.utcnow()
    else:
        receipt = ReadReceipt(
            channel_id=channel_id,
            user_id=user.id,
            last_read_message_id=data.message_id,
        )
        session.add(receipt)
    
    session.commit()
    
    # Broadcast read receipt to channel
    await broadcast_to_channel(
        channel_id,
        "read_receipt",
        {
            "user_id": user.id,
            "message_id": data.message_id,
            "read_at": receipt.read_at.isoformat(),
        },
    )
    
    return {"marked_read": True, "message_id": data.message_id}


@router.get("/channels/{channel_id}")
def get_read_receipts(
    channel_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get read receipts for all members in a channel."""
    require_channel_member(session, channel_id, user.id)
    
    receipts = (
        session.query(ReadReceipt)
        .filter(ReadReceipt.channel_id == channel_id)
        .all()
    )
    
    return [
        {
            "user_id": r.user_id,
            "last_read_message_id": r.last_read_message_id,
            "read_at": r.read_at.isoformat(),
        }
        for r in receipts
    ]


@router.get("/channels/{channel_id}/unread-count")
def get_unread_count(
    channel_id: str,
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    """Get count of unread messages for current user."""
    require_channel_member(session, channel_id, user.id)
    
    # Get last read message
    receipt = (
        session.query(ReadReceipt)
        .filter(
            ReadReceipt.channel_id == channel_id,
            ReadReceipt.user_id == user.id,
        )
        .first()
    )
    
    # Count messages after last read
    query = session.query(Message).filter(
        Message.channel_id == channel_id,
        Message.deleted == False,
    )
    
    if receipt:
        # Get the created_at of last read message
        last_read = session.get(Message, receipt.last_read_message_id)
        if last_read:
            query = query.filter(Message.created_at > last_read.created_at)
    
    count = query.count()
    
    return {"unread_count": count}
