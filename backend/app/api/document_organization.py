"""Native channel organization with fresh authorization and compare-and-set moves.

Secure channels fail closed: their encrypted pages/comments remain available in
shared_documents, but this metadata protocol has no Matrix implementation yet.
"""
from datetime import datetime
from uuid import uuid4
from hashlib import sha256
from fastapi import APIRouter, Depends, HTTPException, Response, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from ..database import get_session
from ..models.shared_documents import SharedDocument, SharedDocumentComment
from ..models.document_organization import SharedDocumentStack, SharedDocumentPlacement, SharedDocumentRelation, SharedDocumentStackReference
from ..services.auth import get_current_user, security
from ..services import shared_documents as service
from ..services.chat_notifications import require_native_channel
from ..services.field_references import normalize_field_references

MAX_STACKS = 30
MAX_RELATIONS = 200
MAX_REFERENCES = 200

def no_store(response: Response):
    response.headers['Cache-Control'] = 'no-store'

router = APIRouter(prefix='/channels/{channel_id}/shared-document-organization',
    tags=['shared document organization'], dependencies=[Depends(no_store)])

class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

class StackCreate(StrictInput):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    title: str = Field(min_length=1, max_length=100)
    @field_validator('title')
    @classmethod
    def title_nonempty(cls, value):
        if not value.strip(): raise ValueError('Name the stack')
        return value.strip()

class StackRename(StrictInput):
    expected_generation: str = Field(pattern=r'^[a-f0-9]{32}$')
    title: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)
    _title = field_validator('title')(StackCreate.title_nonempty.__func__)

class MoveInput(StrictInput):
    stack_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,80}$')
    expected_version: int = Field(ge=0)
    stack_generation: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    @model_validator(mode='after')
    def target_generation(self):
        if (self.stack_id is None) != (self.stack_generation is None):
            raise ValueError('A destination stack requires its current generation')
        return self

class ReferenceCreate(StrictInput):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    message_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    stack_generation: str = Field(pattern=r'^[a-f0-9]{32}$')
    expected_text_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

class RelationCreate(StrictInput):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    document_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    related_document_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


def commit(session):
    try: session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'Document organization changed; reload before trying again') from None


def native_member(session, channel_id, user_id, token=None):
    member = service.locked_member(session, channel_id, user_id, token) if token else service.require_channel_member(session, channel_id, user_id)
    require_native_channel(session, channel_id)
    return member


def stack_for(session, channel_id, identifier):
    row = session.get(SharedDocumentStack, identifier)
    if row is None or row.channel_id != channel_id: raise HTTPException(404, 'Document stack not found')
    return row


def stack_record(session, row):
    # A committed SQLModel is expired; load attributes before model_dump.
    _ = row.id
    return {**row.model_dump(exclude={'encrypted_payload'}),
        'title': service.open_payload(session, row.channel_id, 'stack', row.id, row.generation, row.encrypted_payload)['title']}


def manage_stack(row, member, actor):
    if row.created_by != actor and member.role != 'admin':
        raise HTTPException(403, 'Only the stack creator or channel administrator can change its name or delete it')


def active_document(session, channel_id, document_id, actor):
    row = service.document_for(session, channel_id, document_id, actor)
    if row.archived: raise HTTPException(409, 'Restore the archived document before changing its organization')
    return row


def unique_title(session, channel_id, title, exclude=None):
    for row in session.exec(select(SharedDocumentStack).where(SharedDocumentStack.channel_id == channel_id)).all():
        if row.id != exclude and stack_record(session, row)['title'].casefold() == title.casefold():
            raise HTTPException(409, 'A stack with this name already exists')


@router.get('')
def organization(channel_id: str, archived: bool = False, user=Depends(get_current_user), session=Depends(get_session)):
    native_member(session, channel_id, user.id)
    documents = session.exec(select(SharedDocument).where(SharedDocument.channel_id == channel_id, SharedDocument.archived == archived)).all()
    identifiers = {row.id for row in documents}
    stacks = session.exec(select(SharedDocumentStack).where(SharedDocumentStack.channel_id == channel_id).order_by(SharedDocumentStack.created_at, SharedDocumentStack.id)).all()
    placements = session.exec(select(SharedDocumentPlacement).where(SharedDocumentPlacement.channel_id == channel_id)).all()
    relations = session.exec(select(SharedDocumentRelation).where(SharedDocumentRelation.channel_id == channel_id).order_by(SharedDocumentRelation.created_at, SharedDocumentRelation.id)).all()
    references = session.exec(select(SharedDocumentStackReference).where(SharedDocumentStackReference.channel_id == channel_id).order_by(SharedDocumentStackReference.created_at, SharedDocumentStackReference.id)).all()
    comments = session.exec(select(SharedDocumentComment).join(SharedDocument, SharedDocument.id == SharedDocumentComment.document_id).where(
        SharedDocument.channel_id == channel_id, SharedDocument.archived == archived).order_by(SharedDocumentComment.created_at.desc(), SharedDocumentComment.id).limit(101)).all()
    by_document = {row.document_id: row.stack_id for row in placements}
    stack_comments = []
    for row in comments[:100]:
        if row.content_mode != 'server': raise HTTPException(409, 'Document comment encryption changed; reload before organizing')
        fields = service.open_payload(session, channel_id, 'comment', row.document_id, row.id, row.encrypted_payload)
        try:
            comment_references = normalize_field_references({'text': fields['text']}, {'text': fields.get('references', [])}).get('text')
        except (KeyError, ValueError, TypeError):
            raise HTTPException(409, 'Stored document comment references could not be verified') from None
        stack_comments.append({'id': row.id, 'channel_id': channel_id, 'document_id': row.document_id, 'stack_id': by_document.get(row.document_id),
            'text': fields['text'], 'created_by': row.created_by, 'created_at': row.created_at, 'resolved': row.resolved, 'version': row.version,
            **({'references': comment_references} if comment_references else {})})
    return {'documents': [service.metadata(row, session) for row in documents], 'stacks': [stack_record(session, row) for row in stacks],
        'stack_comments': stack_comments, 'comments_incomplete': len(comments) > 100,
        'message_references': [reference_record(session, row, user.id) for row in references],
        'placements': [row for row in placements if row.document_id in identifiers],
        'relations': [row for row in relations if row.first_document_id in identifiers and row.second_document_id in identifiers]}


@router.post('/stacks', status_code=201)
def create_stack(channel_id: str, data: StackCreate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    native_member(session, channel_id, actor, credentials.credentials)
    previous = session.get(SharedDocumentStack, data.id)
    if previous:
        if previous.channel_id != channel_id: raise HTTPException(404, 'Document stack not found')
        if previous.created_by != actor or stack_record(session, previous)['title'] != data.title:
            raise HTTPException(409, 'This stack identifier has already been used')
        return stack_record(session, previous)
    count = session.exec(select(func.count()).select_from(SharedDocumentStack).where(SharedDocumentStack.channel_id == channel_id)).one()
    if count >= MAX_STACKS: raise HTTPException(409, 'This channel reached its 30-stack limit')
    unique_title(session, channel_id, data.title)
    from ..services.enterprise_governance import enforce_native_document
    enforce_native_document(session, channel_id, actor, {'title': data.title})
    generation = uuid4().hex
    row = SharedDocumentStack(id=data.id, channel_id=channel_id, created_by=actor, generation=generation,
        encrypted_payload=service.seal(channel_id, 'stack', data.id, generation, {'title': data.title}))
    session.add(row); commit(session)
    return stack_record(session, row)


@router.patch('/stacks/{stack_id}')
def rename_stack(channel_id: str, stack_id: str, data: StackRename, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    member = native_member(session, channel_id, actor, credentials.credentials)
    row = stack_for(session, channel_id, stack_id); manage_stack(row, member, actor)
    if row.version != data.expected_version or row.generation != data.expected_generation: raise HTTPException(409, 'The stack changed; reload before trying again')
    unique_title(session, channel_id, data.title, row.id)
    from ..services.enterprise_governance import enforce_native_document
    enforce_native_document(session, channel_id, actor, {'title': data.title})
    row.encrypted_payload = service.seal(channel_id, 'stack', row.id, row.generation, {'title': data.title})
    row.version += 1; row.updated_at = datetime.utcnow()
    session.add(row); commit(session)
    return stack_record(session, row)


@router.delete('/stacks/{stack_id}', status_code=204)
def delete_stack(channel_id: str, stack_id: str, expected_version: int = Query(ge=1), expected_generation: str = Query(pattern=r'^[a-f0-9]{32}$'), user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    member = native_member(session, channel_id, actor, credentials.credentials)
    row = stack_for(session, channel_id, stack_id); manage_stack(row, member, actor)
    if row.version != expected_version or row.generation != expected_generation: raise HTTPException(409, 'The stack changed; reload before trying again')
    if (session.exec(select(SharedDocumentPlacement).where(SharedDocumentPlacement.stack_id == row.id)).first()
            or session.exec(select(SharedDocumentStackReference).where(SharedDocumentStackReference.stack_id == row.id)).first()):
        raise HTTPException(409, 'Move every document, including archived documents, and remove saved messages before deleting this stack')
    session.delete(row); commit(session)
    return Response(status_code=204)


@router.put('/documents/{document_id}/placement')
def move_document(channel_id: str, document_id: str, data: MoveInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    native_member(session, channel_id, actor, credentials.credentials)
    active_document(session, channel_id, document_id, actor)
    if data.stack_id is not None:
        target = stack_for(session, channel_id, data.stack_id)
        if target.generation != data.stack_generation: raise HTTPException(409, 'The destination stack was replaced; reload before moving or undoing')
    previous = session.get(SharedDocumentPlacement, document_id)
    if (previous.version if previous else 0) != data.expected_version:
        raise HTTPException(409, 'The document moved since you last loaded it; reload before moving or undoing')
    if previous and previous.stack_id == data.stack_id: return previous
    if previous:
        previous.stack_id = data.stack_id; previous.version += 1
        previous.updated_by = actor; previous.updated_at = datetime.utcnow()
    else:
        previous = SharedDocumentPlacement(document_id=document_id, channel_id=channel_id, stack_id=data.stack_id, updated_by=actor)
    session.add(previous); commit(session); session.refresh(previous)
    return previous


@router.post('/relations', status_code=201)
def create_relation(channel_id: str, data: RelationCreate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    native_member(session, channel_id, actor, credentials.credentials)
    if data.document_id == data.related_document_id: raise HTTPException(422, 'Choose two different documents')
    for identifier in (data.document_id, data.related_document_id): active_document(session, channel_id, identifier, actor)
    first, second = sorted((data.document_id, data.related_document_id))
    previous = session.get(SharedDocumentRelation, data.id)
    if previous:
        if previous.channel_id != channel_id: raise HTTPException(404, 'Document relation not found')
        if (previous.first_document_id, previous.second_document_id, previous.created_by) != (first, second, actor):
            raise HTTPException(409, 'This relation identifier has already been used')
        return previous
    if session.exec(select(SharedDocumentRelation).where(SharedDocumentRelation.first_document_id == first, SharedDocumentRelation.second_document_id == second)).first():
        raise HTTPException(409, 'These documents are already related')
    count = session.exec(select(func.count()).select_from(SharedDocumentRelation).where(SharedDocumentRelation.channel_id == channel_id)).one()
    if count >= MAX_RELATIONS: raise HTTPException(409, 'This channel reached its 200-relation limit')
    row = SharedDocumentRelation(id=data.id, channel_id=channel_id, first_document_id=first, second_document_id=second, created_by=actor)
    session.add(row); commit(session); session.refresh(row)
    return row


@router.delete('/relations/{relation_id}', status_code=204)
def delete_relation(channel_id: str, relation_id: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    native_member(session, channel_id, user.id, credentials.credentials)
    row = session.get(SharedDocumentRelation, relation_id)
    if row is None or row.channel_id != channel_id: raise HTTPException(404, 'Document relation not found')
    for identifier in (row.first_document_id, row.second_document_id): active_document(session, channel_id, identifier, user.id)
    session.delete(row); commit(session)
    return Response(status_code=204)



def message_preview(session, channel_id, message_id, actor):
    from ..models.message import Message
    from ..services.crypto import decrypt_message
    from nacl.exceptions import CryptoError
    native_member(session, channel_id, actor)
    row = session.get(Message, message_id, populate_existing=True)
    if row is None or row.channel_id != channel_id or row.deleted:
        raise HTTPException(404, 'The source message is unavailable')
    # A reply whose parent was deleted is not reachable through the thread API.
    if row.parent_id:
        parent = session.get(Message, row.parent_id)
        if parent is None or parent.channel_id != channel_id or parent.deleted:
            raise HTTPException(404, 'The source message is unavailable')
    try: text = decrypt_message(row.content, channel_id) if row.encrypted else row.content
    except (CryptoError, ValueError): raise HTTPException(409, 'The source message could not be verified') from None
    if not isinstance(text, str) or not 1 <= len(text) <= 20000:
        raise HTTPException(409, 'The source message exceeds the supported review limit')
    return {'id': row.id, 'channel_id': channel_id, 'text': text, 'author_id': row.author_id,
        'created_at': row.created_at, 'edited_at': row.edited_at, 'expected_text_sha256': sha256(text.encode('utf-8')).hexdigest()}


def reference_record(session, row, actor):
    value = row.model_dump()
    try:
        preview = message_preview(session, row.channel_id, row.message_id, actor)
        return {**value, 'available': True, 'excerpt': preview['text'][:240],
            'author_id': preview['author_id'], 'source_edited_at': preview['edited_at']}
    except HTTPException as failure:
        if failure.status_code not in {404, 409}: raise
        return {**value, 'available': False, 'excerpt': None}


@router.get('/message-preview/{message_id}')
def preview_message(channel_id: str, message_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    return message_preview(session, channel_id, message_id, user.id)


@router.post('/stacks/{stack_id}/message-references', status_code=201)
def add_message_reference(channel_id: str, stack_id: str, data: ReferenceCreate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    native_member(session, channel_id, actor, credentials.credentials)
    target = stack_for(session, channel_id, stack_id)
    if target.generation != data.stack_generation:
        raise HTTPException(409, 'The destination stack was replaced; review it again')
    source = message_preview(session, channel_id, data.message_id, actor)
    if source['expected_text_sha256'] != data.expected_text_sha256:
        raise HTTPException(409, 'The source message changed; review it again before saving')
    previous = session.get(SharedDocumentStackReference, data.id)
    if previous:
        if previous.channel_id != channel_id: raise HTTPException(404, 'Saved message not found')
        if (previous.stack_id, previous.message_id, previous.created_by) != (stack_id, data.message_id, actor):
            raise HTTPException(409, 'This saved-message identifier has already been used')
        return reference_record(session, previous, actor)
    if session.exec(select(SharedDocumentStackReference).where(SharedDocumentStackReference.stack_id == stack_id, SharedDocumentStackReference.message_id == data.message_id)).first():
        raise HTTPException(409, 'This message is already saved in this stack')
    count = session.exec(select(func.count()).select_from(SharedDocumentStackReference).where(SharedDocumentStackReference.channel_id == channel_id)).one()
    if count >= MAX_REFERENCES: raise HTTPException(409, 'This channel reached its 200 saved-message limit')
    row = SharedDocumentStackReference(id=data.id,channel_id=channel_id,stack_id=stack_id,message_id=data.message_id,created_by=actor)
    session.add(row); commit(session); session.refresh(row)
    return reference_record(session, row, actor)


@router.delete('/message-references/{reference_id}', status_code=204)
def remove_message_reference(channel_id: str, reference_id: str, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    native_member(session, channel_id, user.id, credentials.credentials)
    row = session.get(SharedDocumentStackReference, reference_id)
    if row is None or row.channel_id != channel_id: raise HTTPException(404, 'Saved message not found')
    # Removal must remain possible when the original source has been deleted.
    session.delete(row); commit(session)
    return Response(status_code=204)
