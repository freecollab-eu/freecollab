"""All-content collections, local semantic retrieval and owner-only calendars."""
from datetime import datetime
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.user import User
from ..models.account_security import AccountSecurity
from ..models.content_completion import ContentCollectionItem, PersonalCalendarEvent
from ..models.document_organization import SharedDocumentStack, SharedDocumentPlacement
from ..models.file import File
from ..models.integration_content import IntegrationContent
from ..models.workspace import WorkspaceChannel
from ..services import shared_documents, search_sources, semantic_index
from ..services.account_security import account_limit, seal_private, unseal_private
from ..services.auth import get_current_user, security
from ..services.integration_content import CalendarFields
from ..services.request_guard import recheck_actor
from ..services.workspaces import lock_workspace, require_workspace_member
from . import document_organization as organization


def private(response: Response):
    response.headers.update({'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})


router = APIRouter(tags=['content collections and personal calendar'], dependencies=[Depends(private)])


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Item(Strict):
    kind: Literal['document', 'file', 'linked-document', 'task', 'feed', 'message', 'project', 'decision', 'calendar', 'clip', 'workflow']
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    parent_id: str | None = Field(default=None, max_length=128)


class CollectionCreate(Strict):
    id: str = Field(default_factory=lambda: str(uuid4()), pattern=r'^[A-Za-z0-9_-]{1,80}$')
    title: str = Field(min_length=1, max_length=100)
    items: list[Item] = Field(default_factory=list, max_length=100)


class CollectionChange(Strict):
    expected_version: int = Field(ge=1)
    generation: str = Field(pattern=r'^[a-f0-9]{32}$')
    item: Item


def commit(session):
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'The collection changed; reload it') from None


def item_record(session, channel_id, actor_id, item):
    if item.kind == 'document':
        row = organization.active_document(session, channel_id, item.id, actor_id)
        return {**item.model_dump(), 'title': shared_documents.metadata(row, session).get('title') or 'Untitled document'}
    if item.kind == 'file':
        from ..services.remote_sync import is_file_deleted
        from ..models.file_version import FileVersion, FileDocument
        row = session.get(File, item.id, populate_existing=True)
        if not row or row.channel_id != channel_id:
            raise HTTPException(404, 'File is not available in this channel')
        version = session.get(FileVersion, row.id)
        document = session.get(FileDocument, version.document_id) if version else None
        if document and (is_file_deleted(session, document.id) or document.current_file_id != row.id):
            raise HTTPException(404, 'File is no longer current')
        return {**item.model_dump(), 'title': row.filename}
    if item.kind == 'linked-document':
        from .integrations_content import latest_revision, serialize
        row = session.get(IntegrationContent, item.id, populate_existing=True)
        if not row or row.channel_id != channel_id or row.kind != 'documents':
            raise HTTPException(404, 'Document link is not available in this channel')
        revision = latest_revision(session, row.id)
        if not revision or revision.deleted:
            raise HTTPException(404, 'Document link is no longer current')
        return {**item.model_dump(), 'title': serialize(session, row, revision)['fields']['title']}
    kind = item.kind
    model = search_sources.KINDS[kind]
    row = session.get(model, item.id, populate_existing=True)
    if not row or row.channel_id != channel_id:
        raise HTTPException(404, 'Collection item is not available in this channel')
    source = search_sources.source(session, channel_id, kind, row)
    if not search_sources.allowed(session, channel_id, source):
        raise HTTPException(403, 'Collection source is restricted by policy')
    if item.parent_id is not None and item.parent_id != source['parent_id']:
        raise HTTPException(409, 'Collection item parent changed')
    return {**item.model_dump(), 'parent_id': source['parent_id'], 'title': source['title']}


def collection(session, stack, actor_id):
    base = organization.stack_record(session, stack)
    rows = session.exec(select(ContentCollectionItem).where(ContentCollectionItem.stack_id == stack.id)
        .order_by(ContentCollectionItem.created_at, ContentCollectionItem.id)).all()
    items = []
    for row in rows:
        item = Item(kind=row.kind, id=row.resource_id, parent_id=row.parent_id)
        try:
            items.append({**item_record(session, stack.channel_id, actor_id, item), 'available': True})
        except HTTPException as error:
            if error.status_code not in (403, 404, 409, 422):
                raise
            items.append({**item.model_dump(), 'title': None, 'available': False})
    return {key: base[key] for key in ['id', 'channel_id', 'title', 'version', 'generation', 'created_by', 'created_at']} | {'items': items}


@router.get('/channels/{channel_id}/content-collections')
def list_collections(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    organization.native_member(session, channel_id, user.id)
    rows = session.exec(select(SharedDocumentStack).where(SharedDocumentStack.channel_id == channel_id)
        .order_by(SharedDocumentStack.created_at, SharedDocumentStack.id)).all()
    result = [collection(session, row, user.id) for row in rows]
    session.expire_all(); recheck_actor(session, user.id); organization.native_member(session, channel_id, user.id)
    return result


@router.post('/channels/{channel_id}/content-collections', status_code=201)
def create_collection(channel_id: str, data: CollectionCreate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = user.id
    organization.native_member(session, channel_id, actor, credentials.credentials)
    title = data.title.strip()
    if not title:
        raise HTTPException(422, 'Name the collection')
    previous = session.get(SharedDocumentStack, data.id)
    if previous:
        if previous.channel_id != channel_id:
            raise HTTPException(404, 'Collection unavailable')
        old = collection(session, previous, actor)
        expected = sorted((item.kind, item.id, item.parent_id or '') for item in data.items)
        actual = sorted((item['kind'], item['id'], item.get('parent_id') or '') for item in old['items'])
        if previous.created_by != actor or old['title'] != title or expected != actual:
            raise HTTPException(409, 'This collection identifier is already used')
        return old
    if len({(item.kind, item.id) for item in data.items}) != len(data.items):
        raise HTTPException(422, 'Choose each item once')
    if session.exec(select(func.count()).select_from(SharedDocumentStack).where(SharedDocumentStack.channel_id == channel_id)).one() >= organization.MAX_STACKS:
        raise HTTPException(409, 'This channel reached its collection limit')
    organization.unique_title(session, channel_id, title)
    from ..services.enterprise_governance import enforce_native_document
    enforce_native_document(session, channel_id, actor, {'title': title})
    verified = [item_record(session, channel_id, actor, item) for item in data.items]
    organization.native_member(session, channel_id, actor, credentials.credentials)
    row = SharedDocumentStack(id=data.id, channel_id=channel_id, created_by=actor)
    row.encrypted_payload = shared_documents.seal(channel_id, 'stack', row.id, row.generation, {'title': title})
    session.add(row); session.flush()
    for item in verified:
        session.add(ContentCollectionItem(stack_id=row.id, channel_id=channel_id, kind=item['kind'], resource_id=item['id'], parent_id=item.get('parent_id'), created_by=actor))
    commit(session)
    return collection(session, row, actor)


@router.post('/channels/{channel_id}/content-collections/{stack_id}/items')
def add_collection_item(channel_id: str, stack_id: str, data: CollectionChange, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    organization.native_member(session, channel_id, user.id, credentials.credentials)
    row = organization.stack_for(session, channel_id, stack_id)
    if row.version != data.expected_version or row.generation != data.generation:
        raise HTTPException(409, 'The collection changed; reload it')
    item = item_record(session, channel_id, user.id, data.item)
    if session.exec(select(ContentCollectionItem).where(ContentCollectionItem.stack_id == row.id, ContentCollectionItem.kind == item['kind'], ContentCollectionItem.resource_id == item['id'])).first():
        return collection(session, row, user.id)
    count = session.exec(select(func.count()).select_from(ContentCollectionItem).where(ContentCollectionItem.stack_id == row.id)).one()
    if count >= 100:
        raise HTTPException(409, 'This collection reached its 100-item limit')
    organization.native_member(session, channel_id, user.id, credentials.credentials)
    row.version += 1; row.updated_at = datetime.utcnow()
    session.add(row); session.add(ContentCollectionItem(stack_id=row.id, channel_id=channel_id, kind=item['kind'], resource_id=item['id'], parent_id=item.get('parent_id'), created_by=user.id)); commit(session)
    return collection(session, row, user.id)


@router.delete('/channels/{channel_id}/content-collections/{stack_id}/items')
def remove_collection_item(channel_id: str, stack_id: str, data: CollectionChange, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    member = organization.native_member(session, channel_id, user.id, credentials.credentials)
    row = organization.stack_for(session, channel_id, stack_id)
    if row.version != data.expected_version or row.generation != data.generation:
        raise HTTPException(409, 'The collection changed; reload it')
    item = session.exec(select(ContentCollectionItem).where(ContentCollectionItem.stack_id == row.id,
        ContentCollectionItem.kind == data.item.kind, ContentCollectionItem.resource_id == data.item.id)).first()
    if item:
        if item.created_by != user.id and row.created_by != user.id and member.role != 'admin':
            raise HTTPException(403, 'Only the reference author, collection owner or administrator can remove it')
        session.delete(item); row.version += 1; row.updated_at = datetime.utcnow(); session.add(row); commit(session)
    return collection(session, row, user.id)


class SemanticQuery(Strict):
    query: str = Field(min_length=1, max_length=1000)
    channel_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=10, ge=1, le=25)


@router.post('/workspaces/{workspace_id}/semantic-search')
def semantic_search(workspace_id: str, data: SemanticQuery, user=Depends(get_current_user), session=Depends(get_session)):
    require_workspace_member(session, workspace_id, user.id); recheck_actor(session, user.id)
    account_limit(session, 'semantic-search', user.id, 60, 3600)
    from ..models.channel import ChannelMember
    ids = session.exec(select(WorkspaceChannel.channel_id).join(ChannelMember, ChannelMember.channel_id == WorkspaceChannel.channel_id)
        .where(WorkspaceChannel.workspace_id == workspace_id, ChannelMember.user_id == user.id).order_by(WorkspaceChannel.channel_id)).all()
    if data.channel_ids is not None:
        if set(data.channel_ids) - set(ids):
            raise HTTPException(403, 'Search only channels you can currently access')
        ids = list(dict.fromkeys(data.channel_ids))
    if len(ids) > 50:
        raise HTTPException(422, 'Choose up to 50 channels for one semantic query')
    results, revisions, complete, encrypted, engines = [], {}, True, [], set()
    from ..services.matrix_bridge import is_secure_channel
    for channel_id in ids:
        if is_secure_channel(session, channel_id):
            encrypted.append(channel_id); continue
        result = semantic_index.retrieve(session, workspace_id, channel_id, user.id, data.query, data.limit)
        engines.add(result['engine']); results.extend(result['items']); revisions[channel_id] = result['index_revision']; complete &= result['complete']
    # Recheck every source at the final response boundary, not only each channel.
    session.commit(); session.expire_all(); recheck_actor(session, user.id); require_workspace_member(session, workspace_id, user.id)
    fresh = []
    for item in sorted(results, key=lambda row: (-row['score'], row['kind'], row['id'])):
        search_sources.authorize(session, item['channel_id'], user.id)
        if search_sources.still_current(session, item) and search_sources.allowed(session, item['channel_id'], item):
            fresh.append(item)
        if len(fresh) >= data.limit:
            break
    return {'items': fresh, 'complete': complete and not encrypted, 'engine': 'opensearch-lsa-knn-v1' if 'opensearch-lsa-knn-v1' in engines else 'local-lsa-v1',
        'index_revision': revisions, 'device_only_channel_ids': encrypted}


class PersonalEventInput(Strict):
    id: str = Field(default_factory=lambda: str(uuid4()), pattern=r'^[A-Za-z0-9_-]{1,128}$')
    expected_revision: int = Field(default=0, ge=0)
    fields: CalendarFields
    context_channel_id: str | None = Field(default=None, max_length=128)


def lock_calendar_owner(session, workspace_id, actor_id):
    # Account erasure and workers use User -> Auth -> Workspace order. Keep this
    # fence through event mutation/commit; rate limiting commits before it.
    session.execute(update(User).where(User.id == actor_id).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == actor_id).values(auth_version=AccountSecurity.auth_version))
    lock_workspace(session, workspace_id); session.expire_all()
    recheck_actor(session, actor_id); require_workspace_member(session, workspace_id, actor_id)


def private_event(session, row):
    from ..services.content_completion import personal_event_record
    return personal_event_record(row)

@router.get('/workspaces/{workspace_id}/personal-calendar')
def personal_calendar(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    recheck_actor(session, user.id); require_workspace_member(session, workspace_id, user.id)
    rows = session.exec(select(PersonalCalendarEvent).where(PersonalCalendarEvent.workspace_id == workspace_id,
        PersonalCalendarEvent.user_id == user.id).order_by(PersonalCalendarEvent.created_at.desc()).limit(1000)).all()
    result = [private_event(session, row) for row in rows]
    session.expire_all(); recheck_actor(session, user.id); require_workspace_member(session, workspace_id, user.id)
    return result


@router.post('/workspaces/{workspace_id}/personal-calendar')
def save_personal_event(workspace_id: str, data: PersonalEventInput, user=Depends(get_current_user), session=Depends(get_session)):
    recheck_actor(session, user.id); require_workspace_member(session, workspace_id, user.id)
    account_limit(session, 'personal-calendar-write', user.id, 120, 3600)
    lock_calendar_owner(session, workspace_id, user.id)
    row = session.get(PersonalCalendarEvent, data.id, populate_existing=True)
    if row and (row.workspace_id != workspace_id or row.user_id != user.id):
        raise HTTPException(404, 'Private calendar event not found')
    payload = {'fields': data.fields.model_dump(), 'context_channel_id': data.context_channel_id}
    old = private_event(session, row) if row else None
    unchanged_references = bool(old and old['context_channel_id'] == data.context_channel_id
        and old['fields'].get('field_references') == payload['fields'].get('field_references')
        and all(old['fields'].get(key) == payload['fields'].get(key) for key in ('title', 'description', 'location')))
    if data.context_channel_id:
        binding = session.get(WorkspaceChannel, data.context_channel_id, populate_existing=True)
        if not binding or binding.workspace_id != workspace_id:
            raise HTTPException(403, 'Calendar reference context is outside this workspace')
        if not old or old['context_channel_id'] != data.context_channel_id:
            from ..services.access import require_channel_member
            require_channel_member(session, data.context_channel_id, user.id)
    if data.fields.field_references and not unchanged_references:
        if not data.context_channel_id:
            raise HTTPException(422, 'Referenced calendar fields need their original channel')
        binding = session.get(WorkspaceChannel, data.context_channel_id)
        if not binding or binding.workspace_id != workspace_id:
            raise HTTPException(403, 'Calendar reference context is outside this workspace')
        from ..services.field_references import require_field_reference_targets
        require_field_reference_targets(session, data.context_channel_id, user.id, data.fields.model_dump())
    if row and data.expected_revision == 0 and old['fields'] == payload['fields'] and old['context_channel_id'] == data.context_channel_id:
        return old
    if (row.revision if row else 0) != data.expected_revision:
        raise HTTPException(409, 'The private event changed; reload it before saving')
    if not row and session.exec(select(func.count()).select_from(PersonalCalendarEvent).where(PersonalCalendarEvent.user_id == user.id,
        PersonalCalendarEvent.workspace_id == workspace_id)).one() >= 1000:
        raise HTTPException(409, 'This private calendar reached its 1000-event limit')
    recheck_actor(session, user.id); require_workspace_member(session, workspace_id, user.id)
    row = row or PersonalCalendarEvent(id=data.id, workspace_id=workspace_id, user_id=user.id, encrypted_payload='', revision=0)
    row.revision += 1; row.updated_at = datetime.utcnow()
    row.encrypted_payload = seal_private(json.dumps(payload, ensure_ascii=False), 'personal-calendar:' + workspace_id + ':' + row.id, user.id)
    session.add(row); result = private_event(session, row); commit(session)
    return result


@router.delete('/workspaces/{workspace_id}/personal-calendar/{event_id}', status_code=204)
def delete_personal_event(workspace_id: str, event_id: str, expected_revision: int, user=Depends(get_current_user), session=Depends(get_session)):
    lock_calendar_owner(session, workspace_id, user.id)
    row = session.get(PersonalCalendarEvent, event_id)
    if not row or row.user_id != user.id or row.workspace_id != workspace_id:
        raise HTTPException(404, 'Private calendar event not found')
    if row.revision != expected_revision:
        raise HTTPException(409, 'The private event changed; reload it before deleting')
    session.delete(row); commit(session)
    return Response(status_code=204)
