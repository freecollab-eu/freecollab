"""A bounded, current-authority view. This is not a second content index."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import select

from ..models.channel import Channel, ChannelMember
from ..models.chat_productivity import MessageReminder, SavedItemCompletion
from ..models.collaboration import Bookmark
from ..models.integration_content import IntegrationContent
from ..models.message import Message
from ..models.notification import Notification
from ..models.project_tasks import ProjectBoard, TaskCard
from ..models.workspace import Workspace
from .access import require_channel_member
from .connect import channel_view_workspace_id
from .matrix_bridge import is_secure_channel
from .project_tasks import revision_for, serialize as task_data
from .request_guard import recheck_actor
from .workspaces import require_direct_message_writable


def snapshot(session, user_id):
    # Scan only the caller's seats, then check the complete current boundary.
    # A stale/dangling seat must not reveal even the channel's display name.
    recheck_actor(session, user_id)
    from ..api.integrations_content import latest_revision, serialize as calendar_data
    from ..api.notifications import notification_channel, notification_visible
    channels, incomplete = {}, False
    seats = session.exec(select(ChannelMember).where(ChannelMember.user_id == user_id)
                         .order_by(ChannelMember.channel_id).limit(251)).all()
    incomplete = len(seats) > 250
    for seat in seats[:250]:
        try:
            require_channel_member(session, seat.channel_id, user_id)
            require_direct_message_writable(session, seat.channel_id, user_id)
        except HTTPException as error:
            if error.status_code == 401:
                raise
            continue
        workspace_id = channel_view_workspace_id(session, seat.channel_id, user_id)
        workspace, channel = session.get(Workspace, workspace_id), session.get(Channel, seat.channel_id)
        if workspace and channel:
            channels[channel.id] = {"id": channel.id, "name": channel.name, "workspace_id": workspace.id,
                "workspace_name": workspace.name, "secure": is_secure_channel(session, channel.id)}

    result = {"generated_at": datetime.now(timezone.utc), "channels": list(channels.values()),
              "tasks": [], "calendar": [], "notifications": [], "reminders": [], "saved": [], "incomplete": incomplete}
    if not channels:
        return result
    identifiers = list(channels)

    tasks = session.exec(select(TaskCard).join(ProjectBoard, ProjectBoard.id == TaskCard.project_id)
        .where(TaskCard.channel_id.in_(identifiers), TaskCard.archived == False, ProjectBoard.archived == False,
               ProjectBoard.channel_id == TaskCard.channel_id)
        .order_by(TaskCard.updated_at.desc(), TaskCard.id).limit(1001)).all()
    result["incomplete"] |= len(tasks) > 1000
    for task in tasks[:1000]:
        revision = revision_for(session, "task", task.id, task.revision)
        if not revision or revision.archived:
            continue
        try:
            record = task_data(session, "task", task, revision)
        except HTTPException as error:
            if error.status_code == 401:
                raise
            result["incomplete"] = True
            continue
        # Matrix assignments and titles are never sent to this service. Return
        # the verified opaque revision; only the trusted client may filter it.
        fields = record.get("fields")
        if fields and (fields["assignee_user_id"] != user_id or fields["status"] == "done"):
            continue
        result["tasks"].append(record)

    events = session.exec(select(IntegrationContent).where(IntegrationContent.channel_id.in_(identifiers),
        IntegrationContent.kind == "calendar").order_by(IntegrationContent.created_at.desc(), IntegrationContent.id)
        .limit(501)).all()
    result["incomplete"] |= len(events) > 500
    for event in events[:500]:
        revision = latest_revision(session, event.id)
        if not revision or revision.deleted:
            continue
        try:
            result["calendar"].append(calendar_data(session, event, revision))
        except HTTPException as error:
            if error.status_code == 401:
                raise
            result["incomplete"] = True

    notifications = session.exec(select(Notification).where(Notification.user_id == user_id, Notification.read == False)
        .order_by(Notification.created_at.desc(), Notification.id).limit(501)).all()
    result["incomplete"] |= len(notifications) > 500
    for item in notifications[:500]:
        if item.entity_type == "message_reminder":
            continue  # The actionable reminder already appears once below.
        channel_id = notification_channel(session, item)
        if channel_id in channels and notification_visible(session, item, user_id):
            # The existing inbox remains the source for text. My Day only needs
            # a current authorized type/reference, never a retained body copy.
            result["notifications"].append({"id": item.id, "channel_id": channel_id, "type": item.type,
                "entity_type": item.entity_type, "entity_id": item.entity_id, "created_at": item.created_at})

    reminders = session.exec(select(MessageReminder).where(MessageReminder.user_id == user_id,
        MessageReminder.channel_id.in_(identifiers), MessageReminder.status.in_(["pending", "notified"]))
        .order_by(MessageReminder.due_at, MessageReminder.id).limit(101)).all()
    result["incomplete"] |= len(reminders) > 100
    for item in reminders[:100]:
        if channels[item.channel_id]["secure"] != (item.kind == "matrix"):
            continue
        if item.kind == "message":
            message = session.get(Message, item.item_id)
            if not message or message.deleted or message.channel_id != item.channel_id:
                continue
        result["reminders"].append({"id": item.id, "channel_id": item.channel_id, "item_id": item.item_id,
            "kind": item.kind, "due_at": item.due_at.replace(tzinfo=timezone.utc), "status": item.status})

    bookmarks = session.exec(select(Bookmark).where(Bookmark.user_id == user_id, Bookmark.channel_id.in_(identifiers))
        .order_by(Bookmark.created_at.desc(), Bookmark.item_id).limit(201)).all()
    result["incomplete"] |= len(bookmarks) > 200
    for item in bookmarks[:200]:
        if session.get(SavedItemCompletion, (user_id, item.channel_id, item.item_id)):
            continue
        if channels[item.channel_id]["secure"] != (item.kind == "matrix"):
            continue
        if item.kind == "message":
            message = session.get(Message, item.item_id)
            if not message or message.deleted or message.channel_id != item.channel_id:
                continue
        result["saved"].append({"channel_id": item.channel_id, "item_id": item.item_id, "kind": item.kind,
                                "created_at": item.created_at})
    recheck_actor(session, user_id)
    return result
