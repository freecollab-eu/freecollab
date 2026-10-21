"""Authorization tests for the private Matrix gateway (no homeserver required)."""
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine
from app.database import get_session
from app.main import app
from app.config import settings
from app.models.channel import ChannelMember
from app.models.secure_channel import SecureChannel, MatrixSession, MatrixAccount
from app.models.direct_message import UserBlock
from app.services.auth import token_digest, decode_token_claims
from app.services.crypto import encrypt_message
from app.services.rate_limit import AuthRateLimiter


@pytest.fixture
def secure(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    def override():
        with Session(engine) as session:
            yield session
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = override
    monkeypatch.setattr(settings, 'matrix_enabled', True)
    monkeypatch.setattr(app.state, 'auth_rate_limiter', AuthRateLimiter(), raising=False)
    client = TestClient(app)
    users = [client.post('/auth/register', json={'name': name, 'email': name+'@example.com', 'password': 'secure-test-password'}).json() for name in ['alice','bob','outsider']]
    headers = [{'Authorization': 'Bearer '+u['access_token']} for u in users]
    channel = client.post('/channels', headers=headers[0], json={'name': 'Private', 'member_ids': [users[1]['user_id']]}).json()['id']
    with Session(engine) as session:
        session.add(SecureChannel(channel_id=channel, matrix_room_id='!bound:local.test', created_by=users[0]['user_id'], status='active'))
        for index, user in enumerate(users):
            session.add(MatrixAccount(user_id=user['user_id'],matrix_user_id='@fc_'+user['user_id']+':local.test',encrypted_password=encrypt_message('matrix-password','matrix-auth:'+user['user_id']),provisioned=True))
            token = 'matrix-test-'+str(index)
            session.add(MatrixSession(matrix_token_hash=token_digest(token), user_id=user['user_id'], auth_token_hash=token_digest(user['access_token']), auth_expires_at=datetime.utcnow()+timedelta(days=1), device_id='D'+str(index), auth_version=decode_token_claims(user['access_token']).get('av',0), encrypted_access_token=encrypt_message(token,'matrix-session:'+token_digest(token))))
        session.commit()
    yield client, engine, users, headers, channel
    app.dependency_overrides.clear(); app.dependency_overrides.update(previous); engine.dispose()


def test_secure_bindings_are_private_and_plaintext_writes_rejected(secure):
    client, _, _, headers, channel = secure
    assert client.get('/secure/channels',headers=headers[2]).json() == []
    assert client.get('/secure/channels',headers=headers[0]).json()[0]['channel_id'] == channel
    response = client.post('/messages/channels/'+channel, headers=headers[0],json={'content':'must never persist'})
    assert response.status_code == 409
    assert client.post('/files/channels/'+channel,headers=headers[0],files={'file':('secret.txt',b'private','text/plain')}).status_code == 409


def test_proxy_rejects_unknown_token_outsider_plaintext_and_membership_mutations(secure):
    client, _, _, _, _ = secure
    base='/_matrix/client/v3/rooms/%21bound%3Alocal.test'
    assert client.get(base+'/messages',headers={'Authorization':'Bearer unknown'}).status_code == 401
    assert client.get(base+'/messages',headers={'Authorization':'Bearer matrix-test-2'}).status_code == 403
    headers={'Authorization':'Bearer matrix-test-0'}
    assert client.put(base+'/send/m.room.message/t',headers=headers,json={'body':'plaintext'}).status_code == 403
    assert client.post(base+'/invite',headers=headers,json={'user_id':'@outsider:local.test'}).status_code == 403
    assert client.put(base+'/state/m.room.encryption/',headers=headers,json={'algorithm':'none'}).status_code == 403
    assert client.post('/_matrix/client/v3/createRoom',headers=headers,json={}).status_code == 403
    assert client.post('/_matrix/media/v3/upload?filename=private.pdf',headers={**headers,'Content-Type':'application/octet-stream'},content=b'cipher').status_code == 400
    assert client.post('/_matrix/media/v3/upload',headers={**headers,'Content-Type':'text/plain'},content=b'plaintext').status_code == 400


def test_native_logout_revokes_matrix_token_and_removed_members_cannot_send(secure):
    client, engine, users, headers, channel = secure
    base='/_matrix/client/v3/rooms/%21bound%3Alocal.test'
    assert client.post('/auth/logout',headers=headers[0]).status_code == 200
    assert client.get(base+'/messages',headers={'Authorization':'Bearer matrix-test-0'}).status_code == 401
    with Session(engine) as session:
        session.delete(session.get(ChannelMember,(channel,users[1]['user_id'])));session.commit()
    assert client.put(base+'/send/m.room.encrypted/t',headers={'Authorization':'Bearer matrix-test-1'},json={'ciphertext':'opaque'}).status_code == 403


def test_dm_block_is_checked_at_proxy_send_boundary(secure):
    from app.models.channel import Channel
    client, engine, users, _, channel=secure
    with Session(engine) as session:
        room=session.get(Channel,channel);room.type='direct';session.add(room)
        session.add(UserBlock(blocker_id=users[0]['user_id'],blocked_id=users[1]['user_id']));session.commit()
    response=client.put('/_matrix/client/v3/rooms/%21bound%3Alocal.test/send/m.room.encrypted/t',headers={'Authorization':'Bearer matrix-test-1'},json={'ciphertext':'opaque'})
    assert response.status_code == 403


def test_encrypted_envelope_requires_current_membership_epoch(secure, monkeypatch):
    import httpx
    from app.services.matrix_bridge import membership_epoch
    client, engine, _, _, channel = secure
    path='/_matrix/client/v3/rooms/%21bound%3Alocal.test/send/m.room.encrypted/txn'
    headers={'Authorization':'Bearer matrix-test-0'}
    body={'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'opaque-ciphertext','session_id':'opaque-session'}
    assert client.put(path,headers=headers,json=body).status_code == 409
    assert client.put(path,headers={**headers,'X-FreeCollab-Membership-Epoch':'stale'},json=body).status_code == 409
    assert client.put(path,headers=headers,json={**body,'body':'accidental plaintext'}).status_code == 400
    with Session(engine) as session:
        epoch=membership_epoch(session,channel)
    seen=[]
    class Upstream:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def request(self,method,url,**kwargs):
            seen.append(kwargs['content'])
            return httpx.Response(200,json={'event_id':'$opaque'})
    monkeypatch.setattr('app.api.secure_messaging.httpx.Client',Upstream)
    # TestClient also uses httpx.Client; retain its already-created transport.
    response=client.put(path,headers={**headers,'X-FreeCollab-Membership-Epoch':epoch},json=body)
    assert response.status_code == 200
    assert response.json()=={'event_id':'$opaque'}
    assert len(seen)==1 and b'accidental plaintext' not in seen[0]
