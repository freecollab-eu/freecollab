"""IDs-only native message/work references; canonical resources keep all content.

A reference is not a snapshot or a retention hold. It follows the ordinary
source/target lifecycle and never creates, copies or edits either resource.
"""
from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlmodel import select

from ..models.channel import ChannelMember
from ..models.collaboration import Decision, DecisionRevision
from ..models.connect import ConnectConnection
from ..models.connect_federation import FederatedConnection
from ..models.conversation_work import ConversationWorkReference
from ..models.message import Message
from ..models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from ..models.shared_documents import SharedDocument, SharedDocumentUpdate
from .access import lock_channel_writes, require_channel_member
from .chat_notifications import require_native_channel
from .request_guard import recheck_actor
from .message_discussion import native_discussion_root
from .workspaces import require_direct_message_writable

MAX_CHANNEL_REFERENCES = 2000
MAX_SOURCE_REFERENCES = 20
TARGET_COLUMNS = {'document': 'document_id', 'task': 'task_id', 'decision': 'decision_id'}


def require_native_context(session, channel_id, user_id, *, write=False):
    if write:
        lock_channel_writes(session, channel_id)
    session.expire_all()
    recheck_actor(session, user_id)
    member = require_channel_member(session, channel_id, user_id)
    require_native_channel(session, channel_id)
    connected = session.exec(select(ConnectConnection.id).where(ConnectConnection.channel_id == channel_id)).first()
    federated = session.exec(select(FederatedConnection.id).where(FederatedConnection.channel_id == channel_id)).first()
    legacy = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel_id,
        ChannelMember.encrypted_channel_key.is_not(None), ChannelMember.encrypted_channel_key != '').limit(1)).first()
    if connected or federated or legacy:
        raise HTTPException(409, 'Conversation work references require an ordinary native channel')
    if write:
        require_direct_message_writable(session, channel_id, user_id)
    return member


def require_source(session, channel_id, message_id, *, cache=None):
    root = native_discussion_root(session, channel_id, message_id, cache=cache)
    if not root:
        raise HTTPException(404, 'Source message is unavailable in this channel')
    return root


def _native_revision(session, kind, item_id, revision):
    row = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == kind,
        ProjectTaskRevision.item_id == item_id, ProjectTaskRevision.revision == revision)).first()
    if row is None or row.content_mode != 'server' or row.matrix_event_id is not None or not row.encrypted_payload:
        return False
    # Use the normal revision/context verifier; the index keeps none of the
    # decrypted fields and cannot make corrupt content appear usable.
    from .project_tasks import serialize
    item = session.get(ProjectBoard if kind == 'project' else TaskCard, item_id)
    if item is None: return False
    serialize(session, kind, item, row)
    return True


def require_resource(session, channel_id, kind, resource_id):
    """Return the current task's routing parent, without reading content."""
    if kind == 'document':
        row = session.exec(select(SharedDocument.channel_id, SharedDocument.archived,
            SharedDocument.summary_event_id, SharedDocument.update_count).where(SharedDocument.id == resource_id)).first()
        if not row or row[0] != channel_id: raise HTTPException(404, 'Work item not found in this channel')
        if row[1]: raise HTTPException(409, 'Restore this work item before sharing it')
        modes = session.exec(select(SharedDocumentUpdate.content_mode, SharedDocumentUpdate.matrix_event_id).where(
            SharedDocumentUpdate.document_id == resource_id).distinct()).all()
        if row[2] or not row[3] or not modes or any(mode != 'server' or event is not None for mode, event in modes):
            raise HTTPException(409, 'This reference requires an existing native work item')
        from .shared_documents import metadata
        document = session.get(SharedDocument, resource_id)
        if document is None: raise HTTPException(404, 'Work item not found in this channel')
        metadata(document, session)
        return None
    if kind == 'task':
        row = session.exec(select(TaskCard.channel_id, TaskCard.project_id, TaskCard.archived, TaskCard.revision,
            ProjectBoard.channel_id, ProjectBoard.archived, ProjectBoard.revision).join(
            ProjectBoard, ProjectBoard.id == TaskCard.project_id).where(TaskCard.id == resource_id)).first()
        if not row or row[0] != channel_id or row[4] != channel_id:
            raise HTTPException(404, 'Work item not found in this channel')
        if row[2] or row[5]: raise HTTPException(409, 'Restore this work item before sharing it')
        if not _native_revision(session, 'task', resource_id, row[3]) or not _native_revision(session, 'project', row[1], row[6]):
            raise HTTPException(409, 'This reference requires an existing native work item')
        return row[1]
    if kind == 'decision':
        row = session.exec(select(Decision.channel_id).where(Decision.id == resource_id)).first()
        if not row or row != channel_id: raise HTTPException(404, 'Work item not found in this channel')
        latest = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == resource_id)
            .order_by(DecisionRevision.revision.desc()).limit(1)).first()
        if not latest or latest.content_mode != 'server' or latest.matrix_event_id is not None or not latest.encrypted_payload:
            raise HTTPException(409, 'This reference requires an existing native work item')
        from ..api.decisions import revision_data
        decision = session.get(Decision, resource_id)
        if decision is None: raise HTTPException(404, 'Work item not found in this channel')
        revision_data(session, decision, latest)
        return None
    raise HTTPException(422, 'Unsupported work item kind')


def reference_data(row, project_id=None, discussion_message_id=None):
    return {'id': row.id, 'channel_id': row.channel_id, 'source_message_id': row.source_message_id,
        'discussion_message_id': discussion_message_id,
        'resource_kind': row.resource_kind, 'resource_id': getattr(row, TARGET_COLUMNS[row.resource_kind]),
        'project_id': project_id, 'created_by': row.created_by, 'created_at': row.created_at}


def attach_reference(session, channel_id, user_id, source_message_id, resource_kind, resource_id):
    """Attach an already-saved resource in the caller's transaction; no commit."""
    require_native_context(session, channel_id, user_id, write=True)
    require_source(session, channel_id, source_message_id)
    project_id = require_resource(session, channel_id, resource_kind, resource_id)
    column = getattr(ConversationWorkReference, TARGET_COLUMNS[resource_kind])
    existing = session.exec(select(ConversationWorkReference).where(ConversationWorkReference.channel_id == channel_id,
        ConversationWorkReference.source_message_id == source_message_id, column == resource_id)).first()
    if existing:
        require_native_context(session, channel_id, user_id, write=True)
        return existing, False, project_id
    total = session.exec(select(func.count()).select_from(ConversationWorkReference).where(
        ConversationWorkReference.channel_id == channel_id)).one()
    source_total = session.exec(select(func.count()).select_from(ConversationWorkReference).where(
        ConversationWorkReference.source_message_id == source_message_id)).one()
    if total >= MAX_CHANNEL_REFERENCES or source_total >= MAX_SOURCE_REFERENCES:
        raise HTTPException(409, 'This conversation reached its retained work-reference limit')
    row = ConversationWorkReference(channel_id=channel_id, source_message_id=source_message_id,
        resource_kind=resource_kind, created_by=user_id, **{TARGET_COLUMNS[resource_kind]: resource_id})
    session.add(row)
    session.flush()
    require_native_context(session, channel_id, user_id, write=True)
    return row, True, project_id


def list_references(session, channel_id, user_id, *, source_message_id=None, limit=50, offset=0):
    require_native_context(session, channel_id, user_id)
    if source_message_id:
        require_source(session, channel_id, source_message_id)
    r = ConversationWorkReference
    query = select(r).join(Message, Message.id == r.source_message_id).outerjoin(
        SharedDocument, SharedDocument.id == r.document_id).outerjoin(TaskCard, TaskCard.id == r.task_id).outerjoin(
        ProjectBoard, ProjectBoard.id == TaskCard.project_id).outerjoin(Decision, Decision.id == r.decision_id).where(
        r.channel_id == channel_id, Message.channel_id == channel_id, Message.deleted.is_(False), or_(
            and_(r.resource_kind == 'document', SharedDocument.channel_id == channel_id, SharedDocument.archived.is_(False)),
            and_(r.resource_kind == 'task', TaskCard.channel_id == channel_id, TaskCard.archived.is_(False),
                ProjectBoard.channel_id == channel_id, ProjectBoard.archived.is_(False)),
            and_(r.resource_kind == 'decision', Decision.channel_id == channel_id)))
    if source_message_id: query = query.where(r.source_message_id == source_message_id)
    rows = session.exec(query.order_by(r.created_at.desc(), r.id.desc()).offset(offset).limit(limit + 1)).all()
    result = []
    source_roots = {}
    for row in rows[:limit]:
        try:
            discussion_id = require_source(session, channel_id, row.source_message_id, cache=source_roots)
            project_id = require_resource(session, channel_id, row.resource_kind, getattr(row, TARGET_COLUMNS[row.resource_kind]))
        except HTTPException as error:
            if error.status_code not in (404, 409): raise
            continue
        result.append(reference_data(row, project_id, discussion_id))
    # The page scans a bounded set of routing records; inaccessible/deleted
    # sources or native-ineligible targets never enter the returned items.
    require_native_context(session, channel_id, user_id)
    return {'items': result, 'next_offset': offset + limit if len(rows) > limit else None}
