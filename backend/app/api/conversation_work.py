"""Share already-saved native work at the message where it belongs."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.conversation_work import ConversationWorkReference
from ..services.auth import get_current_user
from ..services import conversation_work as service


def no_store(response: Response): response.headers['Cache-Control'] = 'private, no-store'


router = APIRouter(prefix='/channels/{channel_id}/conversation-work', tags=['conversation work'], dependencies=[Depends(no_store)])


class ReferenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_message_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    resource_kind: Literal['document', 'task', 'decision']
    resource_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')


@router.get('')
def list_references(channel_id: str, source_message_id: str | None = Query(default=None, pattern=r'^[A-Za-z0-9_-]{1,128}$'),
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=2000),
    user=Depends(get_current_user), session=Depends(get_session)):
    return service.list_references(session, channel_id, user.id, source_message_id=source_message_id, limit=limit, offset=offset)


@router.post('', status_code=201)
def attach_reference(channel_id: str, data: ReferenceInput, response: Response,
    user=Depends(get_current_user), session=Depends(get_session)):
    actor_id = user.id
    try:
        row, created, project_id = service.attach_reference(session, channel_id, actor_id, **data.model_dump())
        result = service.reference_data(row, project_id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'The conversation changed; reload before sharing this work item') from None
    response.status_code = 201 if created else 200
    return result


@router.delete('/{reference_id}', status_code=204)
def remove_reference(channel_id: str, reference_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    actor_id = user.id
    service.require_native_context(session, channel_id, actor_id, write=True)
    row = session.exec(select(ConversationWorkReference).where(ConversationWorkReference.id == reference_id,
        ConversationWorkReference.channel_id == channel_id)).first()
    if not row: raise HTTPException(404, 'Conversation work reference not found')
    member = service.require_native_context(session, channel_id, actor_id, write=True)
    if row.created_by != actor_id and member.role != 'admin':
        raise HTTPException(403, 'Only the person who shared this item or a channel administrator can remove its reference')
    session.delete(row)
    session.commit()
    return Response(status_code=204)
