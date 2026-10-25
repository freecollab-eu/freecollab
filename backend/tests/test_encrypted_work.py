"""Actual opaque HTTP contracts, current grants and metadata-only notifications."""
import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.api import encrypted_work, notifications
from app.config import settings
from app.models.channel import ChannelMember
from app.models.encrypted_work import EncryptedWorkNotice, EncryptedWorkPointer
from app.models.notification import Notification, NotificationPreference
from app.models.secure_channel import MatrixAccount, SecureChannel
from app.services import matrix_bridge
from app.services.auth import revoke_token
from tests.test_shared_documents import documents, headers

PATH = '/channels/room/encrypted-work'


@pytest.fixture
def encrypted(documents, monkeypatch):
    d = documents
    d.client.app.include_router(encrypted_work.router)
    d.client.app.include_router(notifications.router)
    monkeypatch.setattr(settings, 'matrix_enabled', True)
    with Session(d.engine) as session:
        session.add(SecureChannel(channel_id='room', matrix_room_id='!private:example.test', status='active', created_by='owner'))
        session.add_all([MatrixAccount(user_id=actor, matrix_user_id='@fc_' + actor + ':example.test', encrypted_password='unread', provisioned=True)
            for actor in ['owner', 'member', 'peer']])
        session.commit()
    def verify(session, channel, event, actor):
        if event == '$wrong' or channel != 'room':
            raise HTTPException(400, 'Wrong encrypted room or sender')
        return {'sender': '@fc_' + actor + ':example.test', 'room_id': '!private:example.test', 'type': 'm.room.encrypted', 'content': {'ciphertext': 'opaque'}}
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', verify)
    return d


def publish(d, event='$owned', actor='member', **extra):
    return d.client.post(PATH + '/index', headers=headers(actor), json={'matrix_event_id': event, **extra})


def test_context_advertises_typed_protocol_and_exact_current_identities(encrypted):
    d = encrypted
    result = d.client.get(PATH + '/context', headers=headers())
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['mode'] == 'local' and value['encrypted_work_supported'] is True
    assert value['encrypted_work_protocol'] == 'freecollab-encrypted-work/2'
    assert value['room_protocols'] == ['freecollab-matrix-meeting/1']
    assert {row['matrix_user_id'] for row in value['members']} == {'@fc_member:example.test', '@fc_owner:example.test', '@fc_peer:example.test'}
    assert 'password' not in result.text and 'unread' not in result.text
    assert result.headers['cache-control'] == 'private, no-store'
    assert d.client.get(PATH + '/context', headers=headers('outsider')).status_code == 403
    assert d.client.get(PATH + '/context').status_code == 401


def test_index_has_only_opaque_receipts_is_idempotent_and_paginates(encrypted):
    d = encrypted
    first = publish(d); second = publish(d)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert publish(d, '$next').status_code == 201
    view = d.client.get(PATH + '/index', headers=headers()).json()
    assert len(view['items']) == 2
    assert set(view['items'][0]) == {'sequence', 'matrix_event_id', 'matrix_user_id'}
    tail = d.client.get(PATH + '/index?after=' + str(first.json()['sequence']), headers=headers()).json()
    assert [row['matrix_event_id'] for row in tail['items']] == ['$next']
    with Session(d.engine) as session:
        rows = session.exec(select(EncryptedWorkPointer)).all()
        assert len(rows) == 2
        assert set(rows[0].model_dump()) == {'sequence', 'channel_id', 'matrix_event_id', 'matrix_user_id', 'published_by', 'created_at'}
    assert d.client.get(PATH + '/index?after=-1', headers=headers()).status_code == 422
    assert d.client.get(PATH + '/index', headers=headers('outsider')).status_code == 403


def test_plaintext_extra_fields_native_rooms_and_wrong_events_are_rejected(encrypted):
    d = encrypted
    assert publish(d, title='Never store this private title').status_code == 422
    assert publish(d, '$wrong').status_code == 400
    assert publish(d, 'not-a-matrix-event').status_code == 422
    assert d.client.post('/channels/other/encrypted-work/index', headers=headers('outsider'), json={'matrix_event_id': '$foreign'}).status_code == 409
    with Session(d.engine) as session:
        assert session.exec(select(EncryptedWorkPointer)).all() == []


@pytest.mark.parametrize('change', ['membership', 'room', 'session'])
def test_remote_event_validation_cannot_keep_an_old_grant(encrypted, monkeypatch, change):
    d = encrypted; credential = headers(); original = matrix_bridge.validate_secure_event
    def changed(session, *args):
        event = original(session, *args)
        with Session(d.engine) as other:
            if change == 'membership': other.delete(other.get(ChannelMember, ('room', 'member')))
            elif change == 'room':
                binding = other.get(SecureChannel, 'room'); binding.matrix_room_id = '!replacement:example.test'; other.add(binding)
            else: revoke_token(credential['Authorization'].split()[1], other)
            other.commit()
        return event
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', changed)
    result = d.client.post(PATH + '/index', headers=credential, json={'matrix_event_id': '$late'})
    assert result.status_code in {401, 403, 409}, result.text
    with Session(d.engine) as session:
        assert session.exec(select(EncryptedWorkPointer)).all() == []
        assert session.exec(select(Notification)).all() == []


def test_preparing_channel_never_accepts_or_lists_work(encrypted):
    d = encrypted
    with Session(d.engine) as session:
        row = session.get(SecureChannel, 'room'); row.status = 'preparing'; session.add(row); session.commit()
    assert publish(d).status_code == 409
    assert d.client.get(PATH + '/index', headers=headers()).status_code == 409


def test_coalesced_notifications_are_generic_current_and_preference_aware(encrypted):
    d = encrypted
    assert publish(d).status_code == 201
    assert publish(d, '$another').status_code == 201
    with Session(d.engine) as session:
        rows = session.exec(select(Notification)).all()
        assert len(rows) == 2 and {row.user_id for row in rows} == {'peer', 'owner'}
        assert all(row.title == 'Encrypted work updated' and row.body is None and row.actor_id is None for row in rows)
        assert all(row.entity_type == 'encrypted_work' for row in rows)
        assert len(session.exec(select(EncryptedWorkNotice)).all()) == 2
        session.delete(session.get(ChannelMember, ('room', 'peer'))); session.commit()
    response = d.client.get('/notifications', headers=headers('peer'))
    assert response.status_code == 200 and response.json() == [], response.text
    with Session(d.engine) as session:
        session.add(NotificationPreference(user_id='owner', system=False)); session.commit()
        old = session.exec(select(Notification).where(Notification.user_id == 'owner')).first(); old.read = True; session.add(old); session.commit()
    assert publish(d, '$quiet').status_code == 201
    with Session(d.engine) as session:
        row = session.exec(select(Notification).where(Notification.user_id == 'owner')).first()
        assert row.read is True


def test_private_erasure_keeps_other_members_shared_history(encrypted):
    d = encrypted; assert publish(d).status_code == 201
    from app.services.encrypted_work import erase_user_progress, purge_channel
    with Session(d.engine) as session:
        erase_user_progress(session, 'member'); session.commit()
        pointer = session.exec(select(EncryptedWorkPointer)).one()
        assert pointer.published_by is None and pointer.matrix_event_id == '$owned'
        assert len(session.exec(select(Notification)).all()) == 2
        erase_user_progress(session, 'peer'); session.commit()
        assert [row.user_id for row in session.exec(select(Notification)).all()] == ['owner']
        purge_channel(session, 'room'); session.commit()
        assert session.exec(select(EncryptedWorkPointer)).all() == []
        assert session.exec(select(EncryptedWorkNotice)).all() == []


def test_admin_authority_is_attested_at_publication_not_claimed_or_retroactive(encrypted):
    d = encrypted
    assert publish(d, '$ordinary', 'member').status_code == 201
    first = d.client.get(PATH + '/authority?event_id=$ordinary', headers=headers('peer'))
    assert first.status_code == 200, first.text
    assert first.json()['role'] == 'member' and first.json()['matrix_user_id'] == '@fc_member:example.test'
    with Session(d.engine) as session:
        member = session.get(ChannelMember, ('room', 'member')); member.role = 'admin'; session.add(member); session.commit()
    assert d.client.get(PATH + '/authority?event_id=$ordinary', headers=headers('peer')).json()['role'] == 'member'
    assert publish(d, '$admin-now', 'member').status_code == 201
    assert d.client.get(PATH + '/authority?event_id=$admin-now', headers=headers('peer')).json()['role'] == 'admin'
    with Session(d.engine) as session:
        member = session.get(ChannelMember, ('room', 'member')); member.role = 'member'; session.add(member); session.commit()
    assert d.client.get(PATH + '/authority?event_id=$admin-now', headers=headers('peer')).json()['role'] == 'admin'
    assert publish(d, '$forged-claim', 'member', role='admin').status_code == 422
    assert d.client.get(PATH + '/authority?event_id=$missing', headers=headers()).status_code == 404
    assert d.client.get(PATH + '/authority?event_id=$admin-now', headers=headers('outsider')).status_code == 403
    with Session(d.engine) as session:
        from app.models.encrypted_work import EncryptedWorkAuthority
        rows = session.exec(select(EncryptedWorkAuthority)).all()
        assert len(rows) == 2 and all(set(row.model_dump()) == {'id', 'channel_id', 'matrix_event_id', 'room_id', 'matrix_user_id', 'role', 'published_by', 'created_at'} for row in rows)
