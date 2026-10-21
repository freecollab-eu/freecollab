"""Socket authorization uses database state, including after connection."""
import asyncio
from unittest.mock import AsyncMock, Mock, call

import pytest
import socketio
from sqlmodel import SQLModel, Session, create_engine

from app.models.channel import Channel, ChannelMember
from app.models.user import User
from app.realtime import socket
from app.services.auth import create_token, revoke_token


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def realtime(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'socket.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id='member', name='Member', email='member@example.com', password_hash='unused'),
            User(id='outsider', name='Outsider', email='outsider@example.com', password_hash='unused'),
            User(id='blocked', name='Blocked', email='blocked@example.com', password_hash='unused', is_blocked=True),
            Channel(id='private', name='Private'),
            ChannelMember(channel_id='private', user_id='member'),
        ])
        session.commit()
    monkeypatch.setattr(socket, 'engine', engine)
    sessions = {sid: {'user_id': sid, 'token': create_token(sid)} for sid in ['member', 'outsider', 'blocked', 'deleted']}
    async def get_session(sid):
        return sessions[sid]
    sio = Mock()
    sio.get_session = AsyncMock(side_effect=get_session)
    sio.save_session = AsyncMock()
    sio.enter_room = AsyncMock()
    sio.leave_room = AsyncMock()
    sio.emit = AsyncMock()
    sio.manager.get_participants.return_value = []
    monkeypatch.setattr(socket, 'sio', sio)
    monkeypatch.setattr(socket, 'presence_store', {})
    yield engine, sio, sessions
    engine.dispose()


@pytest.mark.parametrize('auth', [None, [], {'token': []}, {'token': 'invalid'}, {'token': create_token('deleted')}, {'token': create_token('blocked')}])
def test_connect_rejects_invalid_deleted_or_blocked_users(realtime, auth):
    _, sio, _ = realtime
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        run(socket.connect('new', {}, auth))
    sio.enter_room.assert_not_called()
    sio.save_session.assert_not_called()


def test_connect_joins_only_own_notification_room(realtime):
    _, sio, _ = realtime
    token = create_token('member')
    run(socket.connect('new', {}, {'token': token}))
    sio.enter_room.assert_awaited_once_with('new', 'user:member')
    sio.save_session.assert_awaited_once_with('new', {'user_id': 'member', 'token': token})


@pytest.mark.parametrize('data', ['private', {'channel_id': 'private'}])
def test_member_can_join_browser_and_legacy_payloads(realtime, data):
    _, sio, _ = realtime
    assert run(socket.join('member', data)) == {'ok': True}
    sio.enter_room.assert_awaited_once_with('member', 'private')


@pytest.mark.parametrize('data', [None, {}, [], {'channel_id': []}, {'channel_id': ''}, {'channel_id': 'user:outsider'}, 'user:member', 'missing'])
def test_join_rejects_invalid_and_personal_rooms(realtime, data):
    _, sio, _ = realtime
    assert 'error' in run(socket.join('member', data))
    sio.enter_room.assert_not_called()


@pytest.mark.parametrize('event', ['join', 'typing', 'stop_typing'])
@pytest.mark.parametrize('sid', ['outsider', 'blocked', 'deleted'])
def test_nonmembers_and_inactive_users_cannot_use_channel_events(realtime, event, sid):
    _, sio, _ = realtime
    assert 'error' in run(getattr(socket, event)(sid, {'channel_id': 'private'}))
    sio.enter_room.assert_not_called()
    sio.emit.assert_not_called()


@pytest.mark.parametrize('event', ['typing', 'stop_typing'])
def test_member_typing_is_delivered_to_correct_channel(realtime, event):
    _, sio, sessions = realtime
    sessions['peer-device'] = sessions['member']
    sio.manager.get_participants.return_value=[('member','1'),('peer-device','2')]
    assert run(getattr(socket, event)('member', {'channel_id': 'private'})) == {'ok': True}
    sio.emit.assert_awaited_once_with(event, {'user_id': 'member', 'channel_id': 'private'}, room='peer-device')


def test_blocking_member_revokes_events_and_existing_room_delivery(realtime):
    engine, sio, _ = realtime
    assert run(socket.join('member', {'channel_id': 'private'})) == {'ok': True}
    with Session(engine) as session:
        user = session.get(User, 'member')
        user.is_blocked = True
        session.add(user)
        session.commit()
    assert 'error' in run(socket.typing('member', {'channel_id': 'private'}))
    sio.manager.get_participants.return_value = [('member', 'engine-sid')]
    run(socket.broadcast_to_channel('private', 'message', {'content': 'secret'}))
    sio.leave_room.assert_awaited_once_with('member', 'private')


def test_removing_membership_revokes_existing_room_delivery(realtime):
    engine, sio, _ = realtime
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ('private', 'member')))
        session.commit()
    sio.manager.get_participants.return_value = [('member', 'engine-sid')]
    run(socket.broadcast_to_channel('private', 'message', {'content': 'secret'}))
    sio.leave_room.assert_awaited_once_with('member', 'private')


def test_expired_session_token_cannot_join(realtime):
    _, sio, sessions = realtime
    sessions['member']['token'] = 'expired-or-invalid'
    assert 'error' in run(socket.join('member', 'private'))
    sio.enter_room.assert_not_called()


@pytest.mark.parametrize('data', [None, {}, {'status': []}, {'status': 'admin'}, 'anything'])
def test_presence_status_is_validated(realtime, data):
    _, sio, _ = realtime
    assert 'error' in run(socket.set_status('member', data))
    sio.emit.assert_not_called()


def test_browser_presence_payload_is_normalized(realtime):
    _, sio, _ = realtime
    socket.presence_store['member'] = {'sid': 'member', 'status': 'online'}
    sio.manager.get_participants.return_value=[('member','engine-sid')]
    assert run(socket.set_status('member', {'status': 'away'})) == {'ok': True}
    assert socket.presence_store['member']['status'] == 'away'
    sio.emit.assert_awaited_once_with('presence', {'user_id': 'member', 'status': 'away'}, room='member')


def test_notification_delivery_prunes_inactive_or_wrong_user_sessions(realtime):
    _, sio, _ = realtime
    sio.manager.get_participants.return_value = [('member', '1'), ('blocked', '2'), ('outsider', '3')]
    run(socket.send_to_user('member', 'notification', {'message': 'private'}))
    assert sio.leave_room.await_args_list == [call('blocked', 'user:member'), call('outsider', 'user:member')]


def test_native_notification_body_is_not_delivered_after_channel_revocation(realtime):
    from app.models.notification import Notification
    engine,sio,_=realtime
    with Session(engine) as session:
        session.add(Notification(id='notice',user_id='member',type='mention',entity_type='channel',entity_id='private',title='Retained private title'))
        session.commit()
    sio.manager.get_participants.return_value=[('member','engine-sid')]
    run(socket.send_to_user('member','notification',{'id':'notice','title':'Retained private title'}))
    sio.emit.assert_awaited_once_with('notification',{'id':'notice','title':'Retained private title'},room='member')
    sio.emit.reset_mock()
    with Session(engine) as session:
        session.delete(session.get(ChannelMember,('private','member')));session.commit()
    run(socket.send_to_user('member','notification',{'id':'notice','title':'Retained private title'}))
    sio.emit.assert_not_awaited()


def test_empty_authorized_recipient_set_never_falls_back_to_private_room(realtime):
    _,sio,_=realtime
    run(socket.send_to_user('member','notification',{'title':'Must not be emitted'}))
    sio.emit.assert_not_awaited()


def test_identifier_only_channel_revocation_signal_reaches_current_account(realtime):
    engine,sio,_=realtime
    with Session(engine) as session:
        session.delete(session.get(ChannelMember,('private','member')));session.commit()
    sio.manager.get_participants.return_value=[('member','engine-sid')]
    run(socket.send_to_user('member','channel_removed',{'channel_id':'private'}))
    sio.emit.assert_awaited_once_with('channel_removed',{'channel_id':'private'},room='member')
    sio.emit.reset_mock()
    run(socket.send_to_user('member','channel_removed',{'channel_id':'private','title':'Old private title'}))
    sio.emit.assert_not_awaited()


def test_local_multiple_devices_remain_online_until_last_disconnect(realtime):
    engine,sio,sessions=realtime
    with Session(engine) as session:token=create_token('member',session)
    sessions['left']=sessions['right']={'user_id':'member','token':token}
    run(socket.connect('left',{}, {'token':token}));run(socket.connect('right',{}, {'token':token}))
    assert socket.get_user_presence('member')['status']=='online'
    run(socket.disconnect('left'))
    assert socket.get_user_presence('member')['status']=='online'
    run(socket.disconnect('right'))
    assert socket.get_user_presence('member')['status']=='offline'


def test_committed_mutation_delivery_does_not_raise_when_broker_is_unavailable(realtime,monkeypatch):
    from fastapi import HTTPException
    _,sio,_=realtime
    shared=Mock();shared.publish=AsyncMock(side_effect=HTTPException(503,'Shared coordination unavailable'));shared.ready=True
    monkeypatch.setattr(socket,'broker',lambda:shared)
    sio.manager.get_participants.return_value=[('member','engine-sid')]
    run(socket.broadcast_to_channel('private','new_message',{'id':'committed-message'}))
    assert shared.ready is False
    sio.emit.assert_awaited_once_with('new_message',{'id':'committed-message'},room='member')


def test_logout_revokes_connected_socket_before_private_delivery(realtime):
    engine, sio, sessions = realtime
    token = sessions['member']['token']
    assert run(socket.join('member', {'channel_id': 'private'})) == {'ok': True}
    with Session(engine) as session:
        revoke_token(token, session)
    assert 'error' in run(socket.typing('member', {'channel_id': 'private'}))
    sio.manager.get_participants.return_value = [('member', 'engine-sid')]
    run(socket.broadcast_to_channel('private', 'message', {'content': 'secret'}))
    sio.leave_room.assert_awaited_once_with('member', 'private')


def test_logged_out_token_cannot_reconnect(realtime):
    engine, sio, sessions = realtime
    token = sessions['member']['token']
    with Session(engine) as session:
        revoke_token(token, session)
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        run(socket.connect('new', {}, {'token': token}))
    sio.save_session.assert_not_called()
    sio.enter_room.assert_not_called()
