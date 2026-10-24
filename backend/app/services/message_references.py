"""Authorized typed references; never infer recipients from displayed names."""
from datetime import datetime
import unicodedata

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.orm import object_session
from sqlmodel import select
from typing import Literal

from ..models.channel import Channel, ChannelMember
from ..models.file import File
from ..models.file_version import FileVersion
from ..models.message_references import MessageReference, ScheduledMessageReference, MentionGroup, MentionGroupMember
from ..models.project_tasks import ProjectBoard, ProjectTaskRevision, TaskCard
from ..models.shared_documents import SharedDocument
from ..models.user import User
from .access import lock_channel_writes, require_channel_member
from .chat_notifications import validate_mentions
from .request_guard import recheck_actor
from .workspaces import require_direct_message_writable

KINDS = ('person', 'group', 'document', 'task', 'file', 'channel')


def display_label(value, fallback='Untitled item'):
    """A single-line editor label within its 200 UTF-16-unit bound."""
    value = ' '.join(str(value or '').split())
    value = ''.join(char for char in value if not unicodedata.category(char).startswith('C'))
    return value.encode('utf-16-le')[:400].decode('utf-16-le', errors='ignore').strip() or fallback


class TargetInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['person', 'group', 'document', 'task', 'file', 'channel']
    id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:\-]+$')


class ReferenceInput(TargetInput):
    start: int = Field(ge=0, le=10000)
    end: int = Field(ge=1, le=10000)


def context(session, channel_id, actor_id, *, write=False, admin=False):
    if write:
        lock_channel_writes(session, channel_id)
        session.expire_all()
    recheck_actor(session, actor_id)
    member = require_channel_member(session, channel_id, actor_id)
    if write:
        require_direct_message_writable(session, channel_id, actor_id)
    if admin and member.role != 'admin':
        raise HTTPException(403, 'Channel administrator access required')
    return member


def eligible_people(session, channel_id):
    candidates = session.exec(select(User).join(ChannelMember, ChannelMember.user_id == User.id).where(
        ChannelMember.channel_id == channel_id, User.is_blocked == False).order_by(User.name, User.id)).all()
    result = {}
    for person in candidates:
        try:
            require_channel_member(session, channel_id, person.id)
        except HTTPException:
            continue
        result[person.id] = person
    return result


def group_people(session, row, people=None):
    people = people if people is not None else eligible_people(session, row.channel_id)
    ids = session.exec(select(MentionGroupMember.user_id).where(MentionGroupMember.group_id == row.id)
        .order_by(MentionGroupMember.user_id)).all()
    return [identifier for identifier in ids if identifier in people]


def group_data(session, row, people=None):
    members = group_people(session, row, people)
    return {'id': row.id, 'channel_id': row.channel_id, 'name': row.name, 'member_ids': members,
        'member_count': len(members), 'version': row.version, 'created_by': row.created_by,
        'created_at': row.created_at, 'updated_at': row.updated_at}


def valid_name(name):
    value = unicodedata.normalize('NFKC', name).strip()
    if not value or len(value) > 80 or any(unicodedata.category(char).startswith('C') or char in '\r\n\u2028\u2029' for char in value):
        raise HTTPException(422, 'Choose a group name of one to eighty visible characters')
    return value


def group_members(session, channel_id, ids):
    if not ids or len(ids) > 50 or len(set(ids)) != len(ids):
        raise HTTPException(422, 'Choose one to fifty distinct current channel members')
    return validate_mentions(session, channel_id, ids)


def native_resources(session, channel_id, actor_id):
    from .conversation_work import require_native_context
    try:
        require_native_context(session, channel_id, actor_id)
        return True
    except HTTPException as failure:
        if failure.status_code == 409:
            return False
        raise


def resolve_target(session, channel_id, actor_id, kind, identifier, *, people=None):
    """Return a current authorized candidate or None, never private metadata."""
    people = people if people is not None else eligible_people(session, channel_id)
    if kind == 'person':
        person = people.get(identifier)
        return {'kind': kind, 'id': identifier, 'label': display_label(person.name), 'channel_id': channel_id,
            'user_ids': [identifier]} if person else None
    if kind == 'group':
        row = session.get(MentionGroup, identifier)
        if not row or row.channel_id != channel_id:
            return None
        members = group_people(session, row, people)
        if not members:
            return None
        return {'kind': kind, 'id': identifier, 'label': display_label(row.name), 'channel_id': channel_id,
            'member_count': len(members), 'user_ids': members}
    if kind == 'channel':
        row = session.get(Channel, identifier) if identifier == channel_id else None
        from .matrix_bridge import is_secure_channel
        label = 'Encrypted conversation' if is_secure_channel(session, channel_id) else row.name if row else None
        return {'kind': kind, 'id': identifier, 'label': display_label(label, 'This conversation'), 'channel_id': channel_id} if row else None
    if not native_resources(session, channel_id, actor_id):
        from .matrix_bridge import is_secure_channel
        # These APIs keep opaque encrypted document/task index rows. Verify
        # their room-local identity without opening or returning content.
        if is_secure_channel(session, channel_id) and kind == 'document':
            row = session.get(SharedDocument, identifier)
            if row and row.channel_id == channel_id and not row.archived and row.summary_event_id and not row.summary_encrypted_payload:
                return {'kind': kind, 'id': identifier, 'channel_id': channel_id}
        if is_secure_channel(session, channel_id) and kind == 'task':
            row = session.get(TaskCard, identifier)
            board = session.get(ProjectBoard, row.project_id) if row else None
            if row and row.channel_id == channel_id and not row.archived and board and board.channel_id == channel_id and not board.archived:
                revision = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == 'task',
                    ProjectTaskRevision.item_id == identifier, ProjectTaskRevision.revision == row.revision)).first()
                parent_revision = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == 'project',
                    ProjectTaskRevision.item_id == board.id, ProjectTaskRevision.revision == board.revision)).first()
                if all(rev and rev.content_mode == 'matrix' and rev.matrix_event_id and not rev.encrypted_payload for rev in (revision, parent_revision)):
                    return {'kind': kind, 'id': identifier, 'channel_id': channel_id, 'project_id': board.id}
        return None
    if kind == 'document':
        row = session.get(SharedDocument, identifier)
        if not row or row.channel_id != channel_id or row.archived:
            return None
        from .conversation_work import require_resource
        from .shared_documents import metadata
        try:
            require_resource(session, channel_id, 'document', identifier)
            label = metadata(row, session).get('title')
        except HTTPException:
            return None
        return {'kind': kind, 'id': identifier, 'label': display_label(label, 'Untitled document'), 'channel_id': channel_id}
    if kind == 'task':
        row = session.get(TaskCard, identifier)
        if not row or row.channel_id != channel_id or row.archived:
            return None
        from .conversation_work import require_resource
        from .project_tasks import serialize
        try:
            project_id = require_resource(session, channel_id, 'task', identifier)
            revision = session.exec(select(ProjectTaskRevision).where(ProjectTaskRevision.kind == 'task',
                ProjectTaskRevision.item_id == identifier, ProjectTaskRevision.revision == row.revision)).first()
            fields = serialize(session, 'task', row, revision).get('fields') or {}
        except HTTPException:
            return None
        return {'kind': kind, 'id': identifier, 'label': display_label(fields.get('title'), 'Untitled task'),
            'channel_id': channel_id, 'project_id': project_id}
    if kind == 'file':
        row = session.get(File, identifier)
        if not row or row.channel_id != channel_id:
            return None
        from ..models.remote_sync import RemoteSyncFileState
        version = session.get(FileVersion, identifier)
        state = session.get(RemoteSyncFileState, version.document_id) if version else None
        if state and state.deleted:
            return None
        return {'kind': kind, 'id': identifier, 'label': display_label(row.filename), 'channel_id': channel_id}
    return None


def validate_references(session, channel_id, actor_id, content, references, mentions=()):
    """Validate UTF-16 boundaries and expand current group members atomically."""
    if len(references) > 50:
        raise HTTPException(422, 'A message supports at most fifty references')
    # Called only after the transport's channel write fence. Forget identities
    # loaded by dependencies before that fence, including group membership.
    session.expire_all()
    context(session, channel_id, actor_id)
    # Python indexes Unicode scalars; the editor and transport use UTF-16.
    offsets, cursor = {0: 0}, 0
    for index, char in enumerate(content):
        if 0xD800 <= ord(char) <= 0xDFFF:
            raise HTTPException(422, 'Message text contains an invalid Unicode scalar')
        cursor += 2 if ord(char) > 0xFFFF else 1
        offsets[cursor] = index + 1
    result, recipients, previous = [], list(mentions), -1
    people = eligible_people(session, channel_id)
    for source in references:
        ref = source if isinstance(source, ReferenceInput) else ReferenceInput.model_validate(source)
        if ref.start < previous or ref.end <= ref.start or ref.start not in offsets or ref.end not in offsets:
            raise HTTPException(422, 'Reference spans must be ordered, disjoint and follow UTF-16 character boundaries')
        label = content[offsets[ref.start]:offsets[ref.end]]
        if len(label) < 2 or len(label) > 201 or not label.startswith('@') or any(char in label for char in '\r\n'):
            raise HTTPException(422, 'A reference must point to its displayed @name')
        candidate = resolve_target(session, channel_id, actor_id, ref.kind, ref.id, people=people)
        if not candidate:
            raise HTTPException(404, 'A referenced item is no longer available in this conversation')
        if ref.kind == 'group' and not candidate['user_ids']:
            raise HTTPException(409, 'This group has no current channel members')
        recipients.extend(candidate.get('user_ids', []))
        result.append(ref.model_dump()); previous = ref.end
    # No stale actor or roster can survive a resource lookup, including
    # callbacks/extensions that touch authority before returning a candidate.
    session.expire_all()
    context(session, channel_id, actor_id)
    require_direct_message_writable(session, channel_id, actor_id)
    recipients = list(dict.fromkeys(recipients))
    if len(recipients) > 50:
        raise HTTPException(422, 'A message can mention at most fifty people')
    return result, validate_mentions(session, channel_id, recipients)


def stored_references(row, session=None, *, scheduled=False):
    session = session or object_session(row)
    if session is None:
        return []
    model, key = (ScheduledMessageReference, 'schedule_id') if scheduled else (MessageReference, 'message_id')
    records = session.exec(select(model).where(getattr(model, key) == row.id).order_by(model.start)).all()
    return [{'kind': record.kind, 'id': record.target_id, 'start': record.start, 'end': record.end} for record in records]


def store_references(session, row, references, *, scheduled=False):
    model, key = (ScheduledMessageReference, 'schedule_id') if scheduled else (MessageReference, 'message_id')
    session.execute(delete(model).where(getattr(model, key) == row.id))
    session.flush()
    for ref in references:
        session.add(model(**{key: row.id}, kind=ref['kind'], target_id=ref['id'], start=ref['start'], end=ref['end']))


def suggestions(session, channel_id, actor_id, query='', kinds=KINDS):
    context(session, channel_id, actor_id)
    people = eligible_people(session, channel_id)
    ids = {'person': list(people), 'group': session.exec(select(MentionGroup.id).where(MentionGroup.channel_id == channel_id).order_by(MentionGroup.name_key).limit(100)).all(),
           'channel': [channel_id]}
    if native_resources(session, channel_id, actor_id):
        ids.update(document=session.exec(select(SharedDocument.id).where(SharedDocument.channel_id == channel_id,
            SharedDocument.archived == False).order_by(SharedDocument.updated_at.desc()).limit(100)).all(),
            task=session.exec(select(TaskCard.id).where(TaskCard.channel_id == channel_id, TaskCard.archived == False)
                .order_by(TaskCard.updated_at.desc()).limit(100)).all(),
            file=session.exec(select(File.id).where(File.channel_id == channel_id).order_by(File.created_at.desc()).limit(100)).all())
    needle = unicodedata.normalize('NFKD', query).casefold().strip()
    fold = lambda value: ''.join(char for char in unicodedata.normalize('NFKD', value).casefold() if not unicodedata.combining(char))
    needle = fold(needle)
    result = []
    for kind in KINDS:
        if kind not in kinds:
            continue
        kind_count = 0
        for identifier in ids.get(kind, []):
            candidate = resolve_target(session, channel_id, actor_id, kind, identifier, people=people)
            if candidate and (not needle or needle in fold(candidate['label'])):
                candidate.pop('user_ids', None)
                result.append(candidate)
                kind_count += 1
                if len(result) >= 50:
                    return result
                if not needle and kind_count >= 8:
                    break
    return result
