"""Native capability erasure under enforced FKs and an actual closure race."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import threading
import pytest

from sqlalchemy import event
from sqlmodel import Session,select

from app.api import native_clients,native_push
from app.models.native_push import NativePushDevice,NativePushDelivery
from app.models.native_calls import NativeCallDevice,NativeCallConsent,NativeCallSession
from app.models.sfu import SFURoom,SFUParticipant
from app.models.notification import Notification
from app.models.user import User
from app.services import native_push as service
from app.services.account_security import seal_private
from tests.test_data_controls import controls,PASSWORD,headers
from tests.test_native_push import CHALLENGE,CALLBACK,VERIFIER,TOKEN,bearer


def close(client,authorization,dry):
    return client.post('/account/data-closure',headers=authorization,json={'password':PASSWORD,
        'confirmation':'ERASE MY ACCOUNT','preview_fingerprint':dry['fingerprint'],
        'acknowledge_retained_shared_authorship_and_backups':True})


def test_account_closure_erases_native_capabilities_before_notifications_with_foreign_keys(controls,monkeypatch):
    client,engine,_,_=controls
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member',lambda *_:None)
    with Session(engine) as session:
        assert session.connection().exec_driver_sql('PRAGMA foreign_keys').scalar()==1
        for name in ['owner','peer']:
            device=NativePushDevice(id='device-'+name,user_id=name,platform='android',token_hash='hash-'+name,
                encrypted_token=seal_private('PRIVATE TOKEN '+name,'native-push-token','device-'+name),auth_token_hash='source-'+name,
                encrypted_claims=seal_private('PRIVATE CLAIMS '+name,'native-push-authority','device-'+name),active=True,confirmed_at=datetime.utcnow())
            session.add(device)
            session.add(Notification(id='notice-'+name,user_id=name,type='mention',title='Private activity'))
        session.commit()
        for name in ['owner','peer']:
            session.add(NativePushDelivery(id='delivery-'+name,device_id='device-'+name,source_key='notice:'+name,
                channel_id='native',notification_id='notice-'+name,expires_at=datetime.utcnow()+timedelta(hours=1)))
        session.add(SFURoom(id='native-room',channel_id='native',host_user_id='peer',expires_at=datetime.utcnow()+timedelta(hours=1),max_participants=20,provisioned=True));session.flush()
        for name in ['owner','peer']:
            session.add(NativeCallDevice(id='call-device-'+name,user_id=name,push_device_id='device-'+name,
                source_hash='source-'+name,push_confirmed_at=datetime.utcnow(),encrypted_claims='PRIVATE CALL CLAIMS '+name,
                credential_hash='call-hash-'+name,platform='android',encrypted_voip_token='PRIVATE VOIP TOKEN '+name,expires_at=datetime.utcnow()+timedelta(hours=1)))
            session.add(SFUParticipant(id='native-participant-'+name,meeting_id='native-room',user_id=name,name=name,
                auth_token_hash='source-'+name,auth_version=0,expires_at=datetime.utcnow()+timedelta(hours=1)))
        session.flush()
        for name in ['owner','peer']:
            session.add(NativeCallConsent(device_id='call-device-'+name,channel_id='native',matrix_room_id='!fixture:example.test',expires_at=datetime.utcnow()+timedelta(hours=1)))
            session.add(NativeCallSession(id='native-session-'+name,device_id='call-device-'+name,participant_id='native-participant-'+name,
                room_id='native-room',channel_id='native',client_call_id='client-'+name,expires_at=datetime.utcnow()+timedelta(hours=1)))
        # A malformed legacy cross-reference must not block deleting the user's
        # own notification, or cause the other user's capability to be erased.
        session.add(NativePushDelivery(id='cross-reference',device_id='device-peer',source_key='cross-notice',
            channel_id='native',notification_id='notice-owner',expires_at=datetime.utcnow()+timedelta(hours=1)))
        session.commit()
    dry=client.get('/account/data-closure',headers=headers(controls)).json()
    assert dry['can_erase_identity']
    response=close(client,headers(controls),dry)
    assert response.status_code==200,response.text
    with Session(engine) as session:
        assert session.get(NativePushDevice,'device-owner') is None
        assert session.get(NativePushDelivery,'delivery-owner') is None
        assert session.get(NativePushDelivery,'cross-reference') is None
        assert session.get(Notification,'notice-owner') is None
        assert session.get(NativePushDevice,'device-peer') is not None
        assert session.get(NativePushDelivery,'delivery-peer') is not None
        assert session.get(User,'owner').is_blocked
        assert session.get(NativeCallDevice,'call-device-owner') is None
        assert session.get(NativeCallSession,'native-session-owner') is None
        assert session.get(NativeCallConsent,('call-device-owner','native')) is None
        assert session.get(SFUParticipant,'native-participant-owner').state in {'left','removed'}
        assert session.get(NativeCallDevice,'call-device-peer') is not None
        assert session.get(NativeCallSession,'native-session-peer') is not None


@pytest.mark.parametrize("kind", ["push", "calling"])
def test_concurrent_native_registration_cannot_survive_account_closure(controls,monkeypatch,kind):
    client,engine,tokens,_=controls
    client.app.include_router(native_clients.router);client.app.include_router(native_push.router)
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member',lambda *_:None)
    monkeypatch.setattr(service,'require_platform',lambda _:None)
    monkeypatch.setattr(service,'dispatch',lambda *args,**kwargs:None)
    grant=client.post('/native-clients/connect',headers=headers(controls),json={'code_challenge':CHALLENGE,'redirect_uri':CALLBACK,'client_kind':'mobile'})
    assert grant.status_code==200,grant.text
    child=client.post('/native-clients/exchange',json={'code':grant.json()['code'],'code_verifier':VERIFIER,'redirect_uri':CALLBACK})
    assert child.status_code==200,child.text
    child_token=child.json()['access_token']
    call_payload=None
    if kind=='calling':
        from app.services import native_calls
        monkeypatch.setattr(native_calls,'require_configured',lambda:None)
        delivered=[];monkeypatch.setattr(service,'dispatch',lambda platform,token,payload,**options:delivered.append(payload))
        pending=client.post('/mobile/native/devices',headers=bearer(child_token),json={'platform':'android','token':TOKEN,'calls_enabled':True,'consent':True})
        assert pending.status_code==202,pending.text
        identifier=pending.json()['id']
        confirmed=client.post('/mobile/native/devices/'+identifier+'/confirm',headers=bearer(child_token),json={'challenge':delivered[-1]['challenge']})
        assert confirmed.status_code==200,confirmed.text
        call_payload={'push_device_id':identifier,'consent':True}
    dry=client.get('/account/data-closure',headers=headers(controls)).json();assert dry['can_erase_identity']
    acquired,release,closure_waiting=threading.Event(),threading.Event(),threading.Event()
    registering=[]
    original=native_push.lock_actor
    def locked(session,user_id):
        original(session,user_id)
        registering.append(threading.get_ident());acquired.set()
        assert release.wait(5),'The proof did not release registration'
    monkeypatch.setattr(native_push,'lock_actor',locked)
    # SQLite serializes every writer, so closure can wait on its rate bucket
    # before reaching User. Observe its first write; the production lock order
    # remains User -> AccountSecurity on databases with per-row write locks.
    @event.listens_for(engine,'before_cursor_execute')
    def closure_attempt(_connection,_cursor,statement,_parameters,_context,_executemany):
        if acquired.is_set() and statement.lstrip().lower().startswith(('update ','insert ','delete ')) and threading.get_ident() not in registering:
            closure_waiting.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        registration=pool.submit(lambda:client.post('/mobile/native/calling/devices' if kind=='calling' else '/mobile/native/devices',headers=bearer(child_token),
            json=call_payload if kind=='calling' else {'platform':'android','token':TOKEN,'calls_enabled':False,'consent':True}))
        assert acquired.wait(5),'Registration did not acquire the user lock'
        closing=pool.submit(lambda:close(client,headers(controls),dry))
        assert closure_waiting.wait(5),'Closure did not attempt a concurrent database write'
        release.set()
        response=registration.result(timeout=15);erased=closing.result(timeout=20)
    assert response.status_code in {201,202,401},response.text
    assert erased.status_code==200,erased.text
    with Session(engine) as session:
        assert not session.exec(select(NativePushDevice).where(NativePushDevice.user_id=='owner')).all()
        assert not session.exec(select(NativePushDelivery)).all()
        assert not session.exec(select(NativeCallDevice).where(NativeCallDevice.user_id=='owner')).all()
        assert session.get(User,'owner').is_blocked
