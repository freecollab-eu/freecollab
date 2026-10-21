"""Own-device deletion confirms Matrix first and fences stale recipient snapshots."""
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlmodel import Session, select
from app.models.secure_channel import MatrixSession, MatrixDeviceRevocation
from app.models.session import ActiveSession
from app.models.account_security import AccountRecoveryCode, AccountSecurity
from app.services.auth import create_token, token_digest
from app.services.account_security import recovery_hash, seal_private
from app.services.crypto import encrypt_message
from app.services.matrix_bridge import membership_epoch
from .test_secure_messaging import secure

PASSWORD = 'secure-test-password'


def add_device(engine, user, device='OTHER'):
    token = create_token(user['user_id'])
    digest = token_digest(token)
    now = datetime.utcnow()
    with Session(engine) as session:
        session.add(ActiveSession(user_id=user['user_id'], token_hash=digest, expires_at=now+timedelta(days=1)))
        session.add(MatrixSession(matrix_token_hash=token_digest('matrix-'+device),user_id=user['user_id'],auth_token_hash=digest,auth_expires_at=now+timedelta(days=1),device_id=device,encrypted_access_token=encrypt_message('matrix-'+device,'matrix-session:'+token_digest('matrix-'+device))))
        session.commit()
    return {'Authorization':'Bearer '+token}


def test_revoke_requires_fresh_password_and_own_target(secure, monkeypatch):
    client,engine,users,headers,_ = secure
    calls=[]
    monkeypatch.setattr('app.api.secure_messaging.delete_owned_device',lambda *args:calls.append(args[-1]))
    assert client.post('/secure/devices/D0/revoke',headers=headers[0],json={'password':'incorrect-password'}).status_code == 403
    assert client.post('/secure/devices/D1/revoke',headers=headers[0],json={'password':PASSWORD}).status_code == 404
    assert not calls
    assert client.get('/auth/me',headers=headers[0]).status_code == 200
    with Session(engine) as session:
        assert not session.exec(select(MatrixDeviceRevocation)).all()


def test_remote_failure_preserves_target_then_retry_revokes_only_linked_login(secure,monkeypatch):
    client,engine,users,headers,channel = secure
    other=add_device(engine,users[0])
    def failure(*args): raise HTTPException(503,'Not confirmed')
    monkeypatch.setattr('app.api.secure_messaging.delete_owned_device',failure)
    body={'password':PASSWORD}
    assert client.post('/secure/devices/OTHER/revoke',headers=headers[0],json=body).status_code == 503
    assert client.get('/auth/me',headers=other).status_code == 200
    with Session(engine) as session:
        assert not session.get(MatrixDeviceRevocation,(users[0]['user_id'],'OTHER'))
        before=membership_epoch(session,channel)
    monkeypatch.setattr('app.api.secure_messaging.delete_owned_device',lambda *args:None)
    response=client.post('/secure/devices/OTHER/revoke',headers=headers[0],json=body)
    assert response.status_code == 200,response.text
    assert response.json() == {'revoked':True,'current':False}
    assert client.get('/auth/me',headers=other).status_code == 401
    assert client.get('/auth/me',headers=headers[0]).status_code == 200
    with Session(engine) as session:
        assert membership_epoch(session,channel) != before
    assert client.post('/secure/devices/OTHER/revoke',headers=headers[0],json=body).json()=={'revoked':True,'current':False}
    # Old ciphertext prepared for the deleted device can never cross the gateway.
    response=client.put('/_matrix/client/v3/rooms/%21bound%3Alocal.test/send/m.room.encrypted/old',headers={'Authorization':'Bearer matrix-test-0','X-FreeCollab-Membership-Epoch':before},json={'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'opaque','session_id':'s'})
    assert response.status_code == 409


def test_current_revoke_and_account_local_device_scope(secure,monkeypatch):
    client,engine,users,headers,_=secure
    # Matrix IDs are account-local. Bob's identically named device is unaffected.
    with Session(engine) as session:
        row=session.get(MatrixSession,token_digest('matrix-test-1'));row.device_id='D0';session.add(row);session.commit()
    monkeypatch.setattr('app.api.secure_messaging.delete_owned_device',lambda *args:None)
    response=client.post('/secure/devices/D0/revoke',headers=headers[0],json={'password':PASSWORD})
    assert response.json()=={'revoked':True,'current':True}
    assert client.get('/auth/me',headers=headers[0]).status_code == 401
    assert client.get('/auth/me',headers=headers[1]).status_code == 200
    assert client.get('/_matrix/client/v3/devices',headers={'Authorization':'Bearer matrix-test-0'}).status_code == 401
    assert client.get('/secure/devices/revocations',headers=headers[1]).json()=={'revoked_devices':[]}


def test_revoke_requires_current_mfa_and_consumes_code_only_with_confirmed_delete(secure,monkeypatch):
    client,engine,users,headers,_=secure
    other=add_device(engine,users[0])
    code='one-use-device-recovery'
    with Session(engine) as session:
        state=session.get(AccountSecurity,users[0]['user_id'])
        state.totp_secret=seal_private('JBSWY3DPEHPK3PXP','totp',users[0]['user_id']);session.add(state)
        session.add(AccountRecoveryCode(code_hash=recovery_hash(code),user_id=users[0]['user_id']));session.commit()
    called=[]
    monkeypatch.setattr('app.api.secure_messaging.delete_owned_device',lambda *args:called.append(True))
    assert client.post('/secure/devices/OTHER/revoke',headers=headers[0],json={'password':PASSWORD}).status_code == 403
    assert not called
    def failure(*args): raise HTTPException(503,'Not confirmed')
    monkeypatch.setattr('app.api.secure_messaging.delete_owned_device',failure)
    body={'password':PASSWORD,'code':code}
    assert client.post('/secure/devices/OTHER/revoke',headers=headers[0],json=body).status_code == 503
    with Session(engine) as session:
        assert session.get(AccountRecoveryCode,recovery_hash(code)).used_at is None
    monkeypatch.setattr('app.api.secure_messaging.delete_owned_device',lambda *args:None)
    assert client.post('/secure/devices/OTHER/revoke',headers=headers[0],json=body).status_code == 200
    assert client.get('/auth/me',headers=other).status_code == 401
    with Session(engine) as session:
        assert session.get(AccountRecoveryCode,recovery_hash(code)).used_at is not None


def test_synapse_uia_deletes_only_requested_device(monkeypatch):
    import httpx
    from types import SimpleNamespace
    from app.services import matrix_bridge as bridge
    monkeypatch.setattr(bridge.settings,'matrix_enabled',True)
    account=SimpleNamespace(user_id='own',matrix_user_id='@fc_own:local.test',encrypted_password=encrypt_message('independent-server-secret','matrix-auth:own'))
    calls=[]
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,**kwargs):
            assert url.endswith('/devices/LOST');return httpx.Response(200,json={'device_id':'LOST'})
        def request(self,method,url,**kwargs):
            calls.append(kwargs['json'])
            if not kwargs['json']:return httpx.Response(401,json={'session':'uia-session','flows':[{'stages':['m.login.password']}]})
            assert kwargs['json']['auth']=={'type':'m.login.password','identifier':{'type':'m.id.user','user':'@fc_own:local.test'},'password':'independent-server-secret','session':'uia-session'}
            return httpx.Response(200,json={})
    monkeypatch.setattr(bridge.httpx,'Client',Client)
    bridge.delete_owned_device(account,'controller-token','LOST')
    assert len(calls)==2


def test_uncertain_current_device_retry_confirms_absence_despite_cleanup_outage(monkeypatch):
    import httpx
    from types import SimpleNamespace
    from app.services import matrix_bridge as bridge
    monkeypatch.setattr(bridge.settings,'matrix_enabled',True)
    account=SimpleNamespace(user_id='own',matrix_user_id='@fc_own:local.test',encrypted_password=encrypt_message('independent-server-secret','matrix-auth:own'))
    seen=[]
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,headers):
            seen.append((url.rsplit('/',1)[-1],headers['Authorization']))
            if headers['Authorization']=='Bearer old-invalid':return httpx.Response(401,json={'errcode':'M_UNKNOWN_TOKEN'})
            if url.endswith('/devices/LOST'):return httpx.Response(404,json={'errcode':'M_NOT_FOUND'})
            raise httpx.ConnectError('Temporary management cleanup unavailable')
    monkeypatch.setattr(bridge.httpx,'Client',Client)
    monkeypatch.setattr(bridge,'login_account',lambda *args,**kwargs:{'access_token':'temporary-token','device_id':'NEW-KEYLESS'})
    bridge.delete_owned_device(account,'old-invalid','LOST')
    assert [item[0] for item in seen]==['LOST','LOST','NEW-KEYLESS']
