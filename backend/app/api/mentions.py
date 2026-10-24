"""Explicit member-ID mentions for ordinary chat; no plaintext name parsing."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from sqlalchemy import and_, or_
from sqlmodel import select

from ..database import get_session
from ..models.message import Message
from ..models.user import User
from ..models.channel import ChannelMember
from ..services.auth import get_current_user
from ..services.access import require_channel_member
from ..services.chat_notifications import message_mentions, notify_chat_message, require_native_channel
from ..models.message_references import MentionGroup, MentionGroupMember
from ..services import message_references as refs

def private_response(response: Response):
    response.headers['Cache-Control'] = 'private, no-store'


router = APIRouter(prefix="/mentions", tags=["mentions"], dependencies=[Depends(private_response)])


class ResolveReferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    references: list[refs.TargetInput] = Field(max_length=50)


class CreateGroup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    member_ids: list[str] = Field(min_length=1, max_length=50)


class UpdateGroup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    member_ids: list[str] | None = Field(default=None, min_length=1, max_length=50)


@router.get('/channels/{channel_id}/suggestions')
def reference_suggestions(channel_id: str, query: str = Query('', max_length=80), kinds: str | None = Query(default=None, max_length=100),
    user=Depends(get_current_user), session=Depends(get_session)):
    selected = refs.KINDS if kinds is None else tuple(dict.fromkeys(kinds.split(',')))
    if not selected or any(kind not in refs.KINDS for kind in selected):
        raise HTTPException(422, 'Choose supported reference kinds')
    return refs.suggestions(session, channel_id, user.id, query, selected)


@router.post('/channels/{channel_id}/references/resolve')
def resolve_references(channel_id: str, data: ResolveReferences, user=Depends(get_current_user), session=Depends(get_session)):
    refs.context(session, channel_id, user.id)
    people = refs.eligible_people(session, channel_id)
    result = []
    for target in data.references:
        candidate = refs.resolve_target(session, channel_id, user.id, target.kind, target.id, people=people)
        result.append({**candidate, 'available': True} if candidate else {'kind': target.kind, 'id': target.id, 'available': False})
    return {'items': result}


@router.get('/channels/{channel_id}/groups')
def list_reference_groups(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    refs.context(session, channel_id, user.id)
    people = refs.eligible_people(session, channel_id)
    groups = session.exec(select(MentionGroup).where(MentionGroup.channel_id == channel_id).order_by(MentionGroup.name_key).limit(100)).all()
    return [refs.group_data(session, group, people) for group in groups]


@router.post('/channels/{channel_id}/groups', status_code=201)
def create_reference_group(channel_id: str, data: CreateGroup, user=Depends(get_current_user), session=Depends(get_session)):
    refs.context(session, channel_id, user.id, write=True, admin=True)
    if len(session.exec(select(MentionGroup.id).where(MentionGroup.channel_id == channel_id).limit(100)).all()) >= 100:
        raise HTTPException(409, 'This channel reached its one hundred notification group limit')
    name = refs.valid_name(data.name)
    members = refs.group_members(session, channel_id, data.member_ids)
    refs.context(session, channel_id, user.id, write=True, admin=True)
    members = refs.group_members(session, channel_id, members)
    group = MentionGroup(channel_id=channel_id, name=name, name_key=name.casefold(), created_by=user.id)
    session.add(group)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'A notification group already uses this name') from None
    for person_id in members:
        session.add(MentionGroupMember(group_id=group.id, user_id=person_id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'A notification group already uses this name') from None
    return refs.group_data(session, group)


@router.patch('/channels/{channel_id}/groups/{group_id}')
def update_reference_group(channel_id: str, group_id: str, data: UpdateGroup, user=Depends(get_current_user), session=Depends(get_session)):
    refs.context(session, channel_id, user.id, write=True, admin=True)
    group = session.get(MentionGroup, group_id)
    if not group or group.channel_id != channel_id:
        raise HTTPException(404, 'Notification group not found')
    if group.version != data.expected_version:
        raise HTTPException(409, 'This group changed; reload before editing it')
    if data.name is None and data.member_ids is None:
        raise HTTPException(422, 'Change the group name or selected members')
    name = refs.valid_name(data.name) if data.name is not None else group.name
    members = refs.group_members(session, channel_id, data.member_ids) if data.member_ids is not None else None
    refs.context(session, channel_id, user.id, write=True, admin=True)
    group = session.get(MentionGroup, group_id)
    if not group or group.channel_id != channel_id or group.version != data.expected_version:
        raise HTTPException(409, 'This group changed; reload before editing it')
    if members is not None:
        members = refs.group_members(session, channel_id, members)
        session.execute(delete(MentionGroupMember).where(MentionGroupMember.group_id == group.id))
        for person_id in members:
            session.add(MentionGroupMember(group_id=group.id, user_id=person_id))
    group.name = name; group.name_key = name.casefold()
    group.version += 1; group.updated_at = datetime.utcnow(); session.add(group)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'A notification group already uses this name') from None
    return refs.group_data(session, group)


@router.delete('/channels/{channel_id}/groups/{group_id}', status_code=204)
def delete_reference_group(channel_id: str, group_id: str, expected_version: int = Query(ge=1),
    user=Depends(get_current_user), session=Depends(get_session)):
    refs.context(session, channel_id, user.id, write=True, admin=True)
    group = session.get(MentionGroup, group_id)
    if not group or group.channel_id != channel_id:
        raise HTTPException(404, 'Notification group not found')
    if group.version != expected_version:
        raise HTTPException(409, 'This group changed; reload before deleting it')
    session.execute(delete(MentionGroupMember).where(MentionGroupMember.group_id == group.id))
    session.delete(group); session.commit()
    return Response(status_code=204)


@router.get("/me")
def get_my_mentions(limit: int = Query(50, ge=1, le=100), before: str | None = None,
                    user=Depends(get_current_user), session=Depends(get_session)):
    query = session.query(Message).join(ChannelMember, ChannelMember.channel_id == Message.channel_id).filter(
        ChannelMember.user_id == user.id, Message.deleted == False,
        Message.mentions.contains('"' + user.id + '"'),
    )
    if before:
        cursor = session.get(Message, before)
        if not cursor:
            raise HTTPException(404, "Mention cursor not found")
        require_channel_member(session, cursor.channel_id, user.id)
        require_native_channel(session, cursor.channel_id)
        if user.id not in message_mentions(cursor):
            raise HTTPException(404, "Mention cursor not found")
        query = query.filter(or_(Message.created_at < cursor.created_at,
                                 and_(Message.created_at == cursor.created_at, Message.id < cursor.id)))
    result = []
    from .messages import message_data
    for message in query.order_by(Message.created_at.desc(), Message.id.desc()):
        if user.id not in message_mentions(message):
            continue
        try:
            require_channel_member(session, message.channel_id, user.id)
            require_native_channel(session, message.channel_id)
        except HTTPException:
            continue
        result.append(message_data(message))
        if len(result) >= limit:
            break
    return result


@router.get("/channels/{channel_id}/members")
def get_mentionable_members(channel_id: str, query: str = Query("", max_length=80),
                            user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    users = session.exec(select(User).join(ChannelMember, ChannelMember.user_id == User.id).where(
        ChannelMember.channel_id == channel_id, User.is_blocked == False,
    ).order_by(User.name, User.id)).all()
    needle = query.casefold().strip()
    result = []
    for member in users:
        if needle and needle not in member.name.casefold():
            continue
        try:
            require_channel_member(session, channel_id, member.id)
        except HTTPException:
            continue
        result.append({"id": member.id, "name": member.name, "username": member.name})
        if len(result) >= 50:
            break
    return result


async def process_message_mentions(message, channel, author, session, mention_ids=None):
    """The client supplies validated IDs; encrypted storage is not name parsed."""
    await notify_chat_message(session, message, mention_ids=mention_ids)
