"""Per-API local delivery of committed worker effects, without storing tokens.

Each replica reads its own bounded database cursor. Socket delivery rechecks its
local browsers; this relay never republishes its batch across the broker. HTTP
history/inbox remain authoritative after restarts or lost realtime connections.
"""
import asyncio
from datetime import datetime, timedelta
import logging

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from .. import database
from ..models.chat_productivity import ScheduledMessage
from ..models.message import Message
from ..models.notification import Notification

log = logging.getLogger("freecollab.productivity")


def after(column, key_column, cursor):
    timestamp, identifier = cursor
    return or_(column > timestamp, and_(column == timestamp, key_column > identifier))


def read_batch(message_cursor, notification_cursor):
    from .matrix_bridge import is_secure_channel
    from ..api.messages import message_data
    messages, notices = [], []
    with Session(database.engine) as session:
        jobs = session.exec(select(ScheduledMessage).where(ScheduledMessage.status == "sent",
            after(ScheduledMessage.finished_at, ScheduledMessage.id, message_cursor))
            .order_by(ScheduledMessage.finished_at, ScheduledMessage.id).limit(100)).all()
        for job in jobs:
            message_cursor = (job.finished_at, job.id)
            message = session.get(Message, job.message_id)
            if message is not None and not message.deleted and not is_secure_channel(session, message.channel_id):
                data = message_data(message)
                data["created_at"] = message.created_at.isoformat() + "Z"
                messages.append((message.channel_id, data))
        records = session.exec(select(Notification).where(after(Notification.created_at, Notification.id, notification_cursor))
            .order_by(Notification.created_at, Notification.id).limit(100)).all()
        for row in records:
            notification_cursor = (row.created_at, row.id)
            notices.append((row.user_id, {**row.model_dump(), "created_at": row.created_at.isoformat() + "Z",
                                         "read_at": row.read_at.isoformat() + "Z" if row.read_at else None}))
    return messages, notices, message_cursor, notification_cursor


async def run():
    from ..realtime.socket import broadcast_to_channel, send_to_user
    start = datetime.utcnow() - timedelta(seconds=5)
    message_cursor, notification_cursor = (start, ""), (start, "")
    while True:
        try:
            messages, notices, next_message, next_notice = await asyncio.to_thread(read_batch, message_cursor, notification_cursor)
            for channel_id, data in messages:
                await broadcast_to_channel(channel_id, "message", data, local_only=True)
            for user_id, data in notices:
                await send_to_user(user_id, "notification", data, local_only=True)
            message_cursor, notification_cursor = next_message, next_notice
        except asyncio.CancelledError:
            raise
        except Exception as failure:
            log.warning("Worker effects relay could not complete (%s)", type(failure).__name__)
        await asyncio.sleep(2)
