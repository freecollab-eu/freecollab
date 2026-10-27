"""Read-only, bounded changes since an already captured channel read receipt."""
from datetime import datetime
import json

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import select

from ..models.collaboration import Decision, DecisionRevision
from ..models.message import Message
from ..models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from ..models.shared_documents import SharedDocument
from .access import require_channel_member
from .chat_notifications import message_mentions, require_native_channel

SCAN_LIMIT = 200
DISPLAY_LIMIT = 5


def latest_decisions(session, channel_id: str, since: datetime):
    newest = (select(DecisionRevision.decision_id.label("decision_id"),
                     func.max(DecisionRevision.revision).label("revision"))
              .join(Decision, Decision.id == DecisionRevision.decision_id)
              .where(Decision.channel_id == channel_id)
              .group_by(DecisionRevision.decision_id).subquery())
    return session.exec(select(Decision, DecisionRevision)
        .join(newest, newest.c.decision_id == Decision.id)
        .join(DecisionRevision, (DecisionRevision.decision_id == Decision.id)
              & (DecisionRevision.revision == newest.c.revision))
        .where(Decision.channel_id == channel_id, DecisionRevision.created_at > since)
        .order_by(DecisionRevision.created_at.desc(), Decision.id).limit(SCAN_LIMIT + 1)).all()


def encrypted_return_decisions(session, channel_id: str, actor_id: str, since: datetime):
    require_channel_member(session, channel_id, actor_id)
    from .matrix_bridge import is_secure_channel
    if not is_secure_channel(session, channel_id):
        raise HTTPException(409, "Use the native return summary for this channel")
    rows = latest_decisions(session, channel_id, since)
    incomplete, items = len(rows) > SCAN_LIMIT, []
    for decision, revision in rows[:SCAN_LIMIT]:
        if revision.content_mode != "matrix" or not revision.matrix_event_id:
            incomplete = True
            continue
        # Deliberately do not call revision_data: no stored payload may be
        # decrypted in this secure metadata path, including old native rows.
        items.append({"id": decision.id, "channel_id": channel_id, "created_by": decision.created_by,
            "created_at": decision.created_at, "revision": revision.revision,
            "revision_id": revision.id, "revised_by": revision.created_by, "revised_at": revision.created_at,
            "content_mode": "matrix", "matrix_event_id": revision.matrix_event_id})
    return {"channel_id": channel_id, "since": since, "items": items, "incomplete": incomplete}


def channel_return_summary(session, channel_id: str, actor_id: str, since: datetime):
    require_channel_member(session, channel_id, actor_id)
    # This route never decrypts or substitutes native content in a secure room.
    require_native_channel(session, channel_id)
    from ..api.decisions import revision_data
    from .project_tasks import serialize
    from .shared_documents import metadata

    groups = {key: {"count": 0, "items": [], "incomplete": False}
              for key in ("decisions", "documents", "attention")}

    def add(group, kind, identifier, title, occurred_at, reason, **extra):
        row = groups[group]
        row["count"] += 1
        row["items"].append({"source": {"kind": kind, "id": identifier, **extra},
                             "title": title, "occurredAt": occurred_at, "reason": reason})

    decisions = latest_decisions(session, channel_id, since)
    groups["decisions"]["incomplete"] = len(decisions) > SCAN_LIMIT
    for decision, revision in decisions[:SCAN_LIMIT]:
        try:
            record = revision_data(session, decision, revision)
            if record["content_mode"] != "server":
                raise HTTPException(409, "Encrypted record unavailable in native summary")
            add("decisions", "decision", decision.id, record["title"], revision.created_at, "decision")
        except HTTPException as error:
            if error.status_code != 409:
                raise
            groups["decisions"]["incomplete"] = True

    documents = session.exec(select(SharedDocument).where(SharedDocument.channel_id == channel_id,
        SharedDocument.archived == False, SharedDocument.updated_at > since)
        .order_by(SharedDocument.updated_at.desc(), SharedDocument.id).limit(SCAN_LIMIT + 1)).all()
    groups["documents"]["incomplete"] = len(documents) > SCAN_LIMIT
    for document in documents[:SCAN_LIMIT]:
        try:
            record = metadata(document, session)
            add("documents", "document", document.id, record.get("title"), document.updated_at, "document")
        except HTTPException as error:
            if error.status_code != 409:
                raise
            groups["documents"]["incomplete"] = True

    parent = aliased(Message)
    messages = session.exec(select(Message, parent).outerjoin(parent, parent.id == Message.parent_id)
        .where(Message.channel_id == channel_id, Message.deleted == False,
               Message.author_id != actor_id, Message.created_at > since,
               Message.mentions.contains(json.dumps(actor_id), autoescape=True)
               | ((parent.author_id == actor_id) & (parent.channel_id == channel_id) & (parent.deleted == False)))
        .order_by(Message.created_at.desc(), Message.id).limit(SCAN_LIMIT + 1)).all()
    groups["attention"]["incomplete"] = len(messages) > SCAN_LIMIT
    for message, root in messages[:SCAN_LIMIT]:
        reason = "mention" if actor_id in message_mentions(message) else (
            "reply" if root and root.channel_id == channel_id and not root.deleted
            and root.author_id == actor_id else None)
        if reason:
            add("attention", "message", message.id, None, message.created_at, reason)

    tasks = session.exec(select(TaskCard, ProjectTaskRevision)
        .join(ProjectBoard, (ProjectBoard.id == TaskCard.project_id)
              & (ProjectBoard.channel_id == channel_id))
        .join(ProjectTaskRevision, (ProjectTaskRevision.item_id == TaskCard.id)
              & (ProjectTaskRevision.kind == "task") & (ProjectTaskRevision.revision == TaskCard.revision))
        .where(TaskCard.channel_id == channel_id, TaskCard.archived == False,
               ProjectBoard.archived == False, TaskCard.updated_at > since)
        .order_by(TaskCard.updated_at.desc(), TaskCard.id).limit(SCAN_LIMIT + 1)).all()
    groups["attention"]["incomplete"] |= len(tasks) > SCAN_LIMIT
    for task, revision in tasks[:SCAN_LIMIT]:
        try:
            record = serialize(session, "task", task, revision)
            fields = record.get("fields") if record["content_mode"] == "server" else None
            if fields is None:
                groups["attention"]["incomplete"] = True
            elif fields["assignee_user_id"] == actor_id and fields["status"] != "done":
                add("attention", "task", task.id, fields["title"], task.updated_at,
                    "assignment", projectId=task.project_id)
        except HTTPException as error:
            if error.status_code != 409:
                raise
            groups["attention"]["incomplete"] = True

    for group in groups.values():
        group["items"].sort(key=lambda row: (row["occurredAt"], row["source"]["id"]), reverse=True)
        group["items"] = group["items"][:DISPLAY_LIMIT]
    return {"channel_id": channel_id, "since": since, "generated_at": datetime.utcnow(),
            "groups": groups, "incomplete": any(row["incomplete"] for row in groups.values())}
