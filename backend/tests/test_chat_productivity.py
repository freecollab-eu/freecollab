"""Delivery/revocation races and privacy of the new daily chat surfaces."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

from sqlmodel import Session, select
from app.api import chat_productivity, saved_items
from app.models.chat_productivity import MessageReminder, ScheduledMessage
from app.models.collaboration import Bookmark
from app.models.message import Message
from app.models.notification import Notification
from app.models.channel import ChannelMember
from app.models.secure_channel import SecureChannel
from app.models.session import RevokedToken
from app.services.auth import token_digest
from app.services.chat_productivity import run_pending
from app.services.crypto import decrypt_message
from .test_workspaces import workspace_team


def prepare(team):
    client, engine, headers = team
    client.app.include_router(chat_productivity.router)
    client.app.include_router(saved_items.router)
    return client, engine, headers


def due():
    return (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()


def schedule(client, headers, **overrides):
    return client.post('/channels/shared/scheduled-messages', headers=headers['owner'], json={
        'client_id':'request-identifier-0001', 'content':'A carefully scheduled thought', 'due_at':due(), **overrides})


def test_scheduled_send_is_encrypted_private_and_exactly_once_across_workers(workspace_team):
    client, engine, headers = prepare(workspace_team)
    when=due()
    response = schedule(client, headers, mentions=['member'],due_at=when)
    assert response.status_code == 201, response.text
    identifier = response.json()['id']
    assert schedule(client, headers,mentions=['member'],due_at=when).json()['id'] == identifier
    assert schedule(client, headers,content='Different message',due_at=when).status_code == 409
    assert client.get('/channels/shared/scheduled-messages', headers=headers['member']).json() == []
    with Session(engine) as session:
        row = session.get(ScheduledMessage, identifier)
        assert row.encrypted_content != 'A carefully scheduled thought'
        assert decrypt_message(row.encrypted_content, row.channel_id) == 'A carefully scheduled thought'
        assert 'Bearer' not in row.claims_json and 'access_token' not in row.claims_json
        assert session.get(Message, identifier) is None
    def worker(_):
        with Session(engine) as session:
            return run_pending(session, now=datetime.utcnow()+timedelta(minutes=2))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(worker, range(2))) == 1
    with Session(engine) as session:
        row = session.get(ScheduledMessage, identifier)
        assert row.status == 'sent' and row.encrypted_content == '' and row.claims_json == '{}'
        message = session.get(Message, identifier)
        assert decrypt_message(message.content, message.channel_id) == 'A carefully scheduled thought'
        assert json.loads(message.mentions) == ['member']
        assert len(session.exec(select(Notification).where(Notification.id=='schedule-'+identifier)).all()) == 1


def test_logout_and_membership_change_cancel_without_copying_content(workspace_team):
    client, engine, headers = prepare(workspace_team)
    first = schedule(client, headers).json()['id']
    with Session(engine) as session:
        digest = token_digest(headers['owner']['Authorization'][7:])
        session.add(RevokedToken(token_hash=digest, expires_at=datetime.utcnow()+timedelta(days=1)))
        session.commit()
        run_pending(session, now=datetime.utcnow()+timedelta(minutes=2))
        row = session.get(ScheduledMessage, first)
        assert row.status == 'cancelled' and row.encrypted_content == ''
        assert session.get(Message, first) is None


def test_encryption_transition_and_cancel_fence_delivery(workspace_team):
    client, engine, headers = prepare(workspace_team)
    first = schedule(client, headers).json()['id']
    second = schedule(client, headers, client_id='request-identifier-0002').json()['id']
    assert client.delete('/scheduled-messages/'+second, headers=headers['member']).status_code == 404
    assert client.delete('/scheduled-messages/'+second, headers=headers['owner']).json()['cancelled']
    with Session(engine) as session:
        session.add(SecureChannel(channel_id='shared', created_by='owner'))
        session.commit()
        run_pending(session, now=datetime.utcnow()+timedelta(minutes=2))
        assert session.get(ScheduledMessage, first).status == 'cancelled'
        assert session.get(Message, first) is None and session.get(Message, second) is None
    assert schedule(client, headers, client_id='request-identifier-0003').status_code == 409


def test_schedule_validates_timezone_targets_and_current_membership(workspace_team):
    client, engine, headers = prepare(workspace_team)
    assert schedule(client, headers, due_at='2030-01-01T10:00:00').status_code == 422
    assert schedule(client, headers, due_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()).status_code == 422
    assert schedule(client, headers, mentions=['outsider']).status_code == 400
    assert schedule(client, headers, parent_id='missing').status_code == 404
    first=schedule(client, headers).json()['id']
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ('shared','owner')))
        session.commit()
        run_pending(session, now=datetime.utcnow()+timedelta(minutes=2))
        assert session.get(ScheduledMessage, first).status == 'cancelled'
        assert session.get(Message, first) is None


def test_personal_reminders_notify_once_snooze_complete_and_saved_completion(workspace_team):
    client, engine, headers = prepare(workspace_team)
    message=client.post('/messages/channels/shared',headers=headers['owner'],json={'content':'Keep the context private'}).json()
    body={'message_id':message['id'],'due_at':due()}
    response=client.post('/channels/shared/reminders',headers=headers['member'],json=body)
    assert response.status_code == 201, response.text
    identifier=response.json()['id']
    assert client.get('/channels/shared/reminders',headers=headers['owner']).json() == []
    assert client.patch('/reminders/'+identifier,headers=headers['owner'],json={'completed':True}).status_code == 404
    with Session(engine) as session:
        assert run_pending(session,now=datetime.utcnow()+timedelta(minutes=2)) == 1
        assert run_pending(session,now=datetime.utcnow()+timedelta(minutes=2)) == 0
        notification=session.get(Notification,'reminder-'+identifier)
        assert notification.user_id == 'member' and 'Keep the context' not in (notification.body or '')
    snoozed=client.patch('/reminders/'+identifier,headers=headers['member'],json={'due_at':due()})
    assert snoozed.status_code == 200 and snoozed.json()['status'] == 'pending'
    assert client.patch('/reminders/'+identifier,headers=headers['member'],json={'completed':True}).json()['status'] == 'completed'
    client.post('/bookmarks',headers=headers['member'],json={'channel_id':'shared','message_id':message['id']})
    route='/bookmarks/shared/'+message['id']+'/completion'
    assert client.patch(route,headers=headers['owner'],json={'completed':True}).status_code == 404
    assert client.patch(route,headers=headers['member'],json={'completed':True}).status_code == 200
    assert client.get('/bookmarks',headers=headers['member']).json()[0]['completed_at']
    assert client.get('/bookmarks',headers=headers['owner']).json() == []


def test_group_conversation_scope_canonical_identity_immutable_roster_and_leave(workspace_team):
    client, engine, headers = prepare(workspace_team)
    body={'workspace_id':'team','participant_ids':['member','hidden']}
    response=client.post('/group-conversations',headers=headers['owner'],json=body)
    assert response.status_code == 200, response.text
    identifier=response.json()['id']
    assert response.json()['type'] == 'group_direct'
    assert client.post('/group-conversations',headers=headers['member'],json={'workspace_id':'team','participant_ids':['owner','hidden']}).json()['id'] == identifier
    assert client.get('/group-conversations?workspace_id=team',headers=headers['outsider']).status_code == 403
    assert client.get('/group-conversations?workspace_id=team',headers=headers['guest']).json() == []
    assert client.post('/channels/'+identifier+'/invites',headers=headers['owner'],json={}).status_code == 409
    assert client.post('/group-conversations',headers=headers['owner'],json={'workspace_id':'team','participant_ids':['member','outsider']}).status_code == 404
    assert client.post('/group-conversations/'+identifier+'/leave',headers=headers['hidden']).status_code == 200
    assert client.get('/channels/'+identifier,headers=headers['hidden']).status_code == 403
    replacement=client.post('/group-conversations',headers=headers['owner'],json=body)
    assert replacement.status_code == 200 and replacement.json()['id'] != identifier


def test_history_reconciliation_head_exposes_only_authorized_native_cursor(workspace_team):
    client, engine, headers = prepare(workspace_team)
    assert client.get('/messages/channels/shared/sync/head',headers=headers['outsider']).status_code == 403
    head=client.get('/messages/channels/shared/sync/head',headers=headers['owner']).json()
    response=client.post('/messages/channels/shared',headers=headers['member'],json={'content':'Recover a missed event'})
    assert response.status_code == 200
    changes=client.get('/messages/channels/shared/sync?after='+str(head['cursor']),headers=headers['owner']).json()
    assert [message['id'] for message in changes['messages']] == [response.json()['id']]
    with Session(engine) as session:
        session.add(SecureChannel(channel_id='shared',created_by='owner'))
        session.commit()
    assert client.get('/messages/channels/shared/sync/head',headers=headers['owner']).status_code == 409
