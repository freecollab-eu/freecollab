"""Opt-in larger channel meetings through the customer's LiveKit service."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, update
from sqlmodel import select
from ..database import get_session
from ..models.sfu import SFURoom, SFUParticipant
from ..services import sfu as service
from ..services.auth import get_current_user, security, active_user_for_token, decode_token_claims, token_digest
from ..services.shared_documents import locked_member
from ..services.access import require_channel_member
from ..services.meetings import bind_participant_authority


def no_store(response: Response): response.headers['Cache-Control'] = 'private, no-store'
router = APIRouter(tags=['large meetings'], dependencies=[Depends(no_store)])
class Empty(BaseModel): model_config = ConfigDict(extra='forbid')


@router.get('/channels/{channel_id}/large-meetings')
def list_rooms(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    rooms = session.exec(select(SFURoom).where(SFURoom.channel_id == channel_id, SFURoom.provisioned == True, SFURoom.ended_at == None, SFURoom.expires_at > datetime.utcnow()).order_by(SFURoom.created_at.desc()).limit(10)).all()  # noqa: E712, E711
    return {'status': service.status(session), 'rooms': [service.public(room) for room in rooms]}


def require_ready(session):
    status = service.status(session)
    if not status['ready']: raise HTTPException(503, status['reason'])


@router.post('/channels/{channel_id}/large-meetings', status_code=201)
def create_room(channel_id: str, data: Empty, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    locked_member(session, channel_id, user.id, credentials.credentials); require_ready(session)
    active = session.exec(select(SFURoom).where(SFURoom.channel_id == channel_id, SFURoom.ended_at == None, SFURoom.expires_at > datetime.utcnow())).first()  # noqa: E711
    if active: raise HTTPException(409, 'This channel already has a large meeting; join it instead')
    room = SFURoom(channel_id=channel_id, host_user_id=user.id, expires_at=datetime.utcnow() + timedelta(seconds=service.ROOM_SECONDS), max_participants=service.settings.meetings_sfu_max_participants)
    session.add(room); session.commit()
    try:
        service.rpc('CreateRoom', {'name': service.room_name(room.id), 'empty_timeout': 300, 'departure_timeout': 300, 'max_participants': room.max_participants}, {'roomCreate': True})
        locked_member(session, channel_id, user.id, credentials.credentials)
    except HTTPException:
        room.ended_at = datetime.utcnow(); session.add(room); session.commit(); raise
    room.provisioned = True; session.add(room); session.commit()
    return service.public(room)


@router.post('/large-meetings/{room_id}/join')
def join(room_id: str, data: Empty, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    room = service.active_room(session, room_id)
    locked_member(session, room.channel_id, user.id, credentials.credentials); require_ready(session)
    session.execute(update(SFURoom).where(SFURoom.id == room_id).values(provisioned=SFURoom.provisioned)); session.expire_all()
    service.lock_admission(session)
    room = service.active_room(session, room_id)
    all_rows = session.exec(select(SFUParticipant).where(SFUParticipant.meeting_id == room_id)).all()
    if any(row.user_id == user.id and row.state == 'removed' for row in all_rows):
        raise HTTPException(403, 'The host removed this account from the meeting')
    rows = [row for row in all_rows if row.state == 'admitted']
    if any(row.user_id == user.id and row.auth_token_hash == token_digest(credentials.credentials) and service.participant_valid(session, row, room) for row in rows):
        raise HTTPException(409, 'This sign-in already has a device in this meeting; leave it first')
    if sum(service.participant_valid(session, row, room) for row in rows) >= room.max_participants:
        raise HTTPException(409, 'This meeting is full')
    total = session.exec(select(func.count()).select_from(SFUParticipant).where(SFUParticipant.meeting_id == room_id)).one()
    if total >= room.max_participants * 10: raise HTTPException(409, 'This room reached its device lease limit; start a new room')
    claims = decode_token_claims(credentials.credentials)
    row = SFUParticipant(meeting_id=room_id, user_id=user.id, name=user.name[:80], auth_token_hash=token_digest(credentials.credentials), auth_version=claims.get('av', 0), scope_workspace_id=claims.get('sso_workspace_id'), scope_provider_revision=claims.get('sso_provider_revision'), expires_at=min(room.expires_at, datetime.utcfromtimestamp(claims['exp'])))
    session.add(row); session.flush()
    bind_participant_authority(session, row, claims, transport='sfu'); session.flush()
    access = service.encode({'roomJoin': True, 'room': service.room_name(room_id), 'canPublish': True, 'canSubscribe': True, 'canPublishData': False}, subject=row.id, name=row.name)
    session.commit()
    return {'meeting': service.public(room), 'participant_id': row.id, 'url': service.configuration()[0], 'token': access, 'token_expires_in': 60}


@router.post('/large-meetings/{room_id}/participants/{participant_id}/{action}')
def participant_action(room_id: str, participant_id: str, action: str, data: Empty, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    room = service.active_room(session, room_id)
    member = locked_member(session, room.channel_id, user.id, credentials.credentials)
    row = session.get(SFUParticipant, participant_id, populate_existing=True)
    if action == 'remove':
        if room.host_user_id != user.id and member.role != 'admin':
            raise HTTPException(403, 'Only the host or a channel administrator can remove participants')
        if not row or row.meeting_id != room_id: raise HTTPException(404, 'Meeting device not found')
        if row.user_id == room.host_user_id: raise HTTPException(409, 'End the meeting to remove its host')
        row.state = 'removed'; session.add(row); session.commit()
        service.remove(room, row); session.add(row); session.commit()
        return {'removed': True}
    if not row or row.meeting_id != room_id or row.user_id != user.id or row.auth_token_hash != token_digest(credentials.credentials):
        raise HTTPException(404, 'Your meeting device was not found')
    if action == 'heartbeat':
        require_ready(session)
        if not service.participant_valid(session, row, room) or row.disconnected: raise HTTPException(410, 'Your meeting access ended')
        row.last_seen_at = datetime.utcnow(); session.add(row); session.commit()
        return {'active': True, 'expires_at': room.expires_at}
    if action != 'leave': raise HTTPException(404, 'Unknown meeting action')
    row.state = 'left'; session.add(row); session.commit()
    service.remove(room, row); session.add(row); session.commit()
    return {'left': True}


@router.post('/large-meetings/{room_id}/end')
def end_room(room_id: str, data: Empty, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    room = service.active_room(session, room_id)
    member = locked_member(session, room.channel_id, user.id, credentials.credentials)
    if room.host_user_id != user.id and member.role != 'admin': raise HTTPException(403, 'Only the host or a channel administrator can end this meeting')
    room.ended_at = datetime.utcnow(); session.add(room); session.commit()
    service.rpc('DeleteRoom', {'room': service.room_name(room.id)}, {'roomCreate': True})
    return {'ended': True}
