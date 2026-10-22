"""Real call admission/OS capability authority and opaque vendor protocols.

No native microphone, customer provider, or physical phone is substituted by
these HTTP/DB tests; the Android compilation and device checklist are separate.
"""
from datetime import datetime, timedelta
import json
import secrets

import jwt
from pydantic import SecretStr
import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models.native_calls import NativeCallDevice, NativeCallSession
from app.models.native_push import NativePushDelivery, NativePushDevice
from app.models.secure_channel import SecureChannel
from app.models.sfu import SFURoom, SFUParticipant, SFUWorkerLease
from app.models.channel import ChannelMember
from app.services import native_calls, native_push, sfu
from app.services.auth import decode_token_claims, revoke_token, token_digest
from app.services.meetings import bind_participant_authority, participant_valid
from app.services.remote_transport import Response, ProviderError
from tests.test_native_push import native, enable, bearer, REAL_DISPATCH, TOKEN


@pytest.fixture
def calling(native,monkeypatch):
    for key,value in {'native_calls_enabled':True,'meetings_sfu_provider':'livekit','meetings_sfu_url':'wss://media.example.test',
        'meetings_sfu_api_url':'https://control.example.test','meetings_sfu_api_key':'fixture-key','meetings_sfu_api_secret':SecretStr('c'*32)}.items():
        monkeypatch.setattr(settings,key,value)
    push,_=enable(native)
    with Session(native[1]) as session:
        session.add(SFUWorkerLease(configuration_hash=sfu.configuration_hash(),scan_budget_exhausted=False))
        room=SFURoom(channel_id='shared',host_user_id='bob',expires_at=datetime.utcnow()+timedelta(hours=1),max_participants=20,provisioned=True)
        session.add(room);session.flush()
        claims=decode_token_claims(native[3]['bob'])
        host=SFUParticipant(meeting_id=room.id,user_id='bob',name='Bob',auth_token_hash=token_digest(native[3]['bob']),auth_version=0,expires_at=room.expires_at)
        session.add(host);session.flush();bind_participant_authority(session,host,claims,transport='sfu');session.commit()
        room_id=room.id
    result=native[0].post('/mobile/native/calling/devices',headers=bearer(native[3]['alice']),json={'push_device_id':push,'consent':True})
    assert result.status_code==201,result.text
    credential=result.json()
    headers=bearer(credential['call_secret'])|{'X-FreeCollab-Call-Device':credential['device_id']}
    return native,push,room_id,credential,headers


def join(d):
    return d[0][0].post('/mobile/native/calling/join',headers=d[4],json={'room_id':d[2],'client_call_id':'1'*32,'explicit_answer':True})


def test_opt_in_original_mobile_source_call_only_capability_and_private_storage(calling):
    native,push,room,cap,headers=calling
    assert cap['audio_e2ee'] is False and cap['heartbeat_seconds']==5
    assert native[0].get('/auth/me',headers=headers).status_code==401
    with Session(native[1]) as session:
        row=session.get(NativeCallDevice,cap['device_id'])
        assert cap['call_secret'] not in row.credential_hash
        assert native[3]['alice'] not in row.encrypted_claims
    result=join(calling); assert result.status_code==200,result.text
    access=result.json();claims=jwt.decode(access['token'],'c'*32,algorithms=['HS256'])
    assert claims['sub']==access['participant_id'] and claims['video']['room']=='freecollab_'+room
    assert claims['exp']-claims['iat']==60 and claims['video']['canPublishData'] is False
    assert claims['video']['canPublishSources']==['microphone']
    assert join(calling).json()['session_id']==access['session_id']
    assert native[0].post('/mobile/native/calling/join',headers=headers,json={'room_id':room,'client_call_id':'2'*32,'explicit_answer':False}).status_code==422
    assert native[0].post('/mobile/native/calling/sessions/'+access['session_id']+'/heartbeat',headers=headers,json={'muted':True}).json()['muted']


@pytest.mark.parametrize('mutation',['logout','parent_logout','membership','push_remove','call_revoke','missed_heartbeat'])
def test_current_authority_revokes_os_answer_controls_and_worker_participant(calling,mutation):
    native,push,room,cap,headers=calling
    joined=join(calling);assert joined.status_code==200,joined.text
    access=joined.json()
    with Session(native[1]) as session:
        if mutation in ['logout','parent_logout']: revoke_token(native[3]['alice'] if mutation=='logout' else native[2]['alice'],session)
        elif mutation=='membership':session.delete(session.get(ChannelMember,('shared','alice')));session.commit()
        elif mutation=='push_remove':
            result=native[0].delete('/mobile/native/devices/'+push,headers=bearer(native[3]['alice']));assert result.status_code==204,result.text
        elif mutation=='call_revoke':native_calls.revoke_devices(session,device_id=cap['device_id']);session.commit()
        else:
            call=session.get(NativeCallSession,access['session_id']);call.heartbeat_at=datetime.utcnow()-timedelta(seconds=16);session.add(call);session.commit()
        participant=session.get(SFUParticipant,access['participant_id'],populate_existing=True)
        assert not participant_valid(session,participant,session.get(SFURoom,room))
    result=native[0].post('/mobile/native/calling/sessions/'+access['session_id']+'/heartbeat',headers=headers,json={})
    assert result.status_code in [401,403,410],result.text


def test_incoming_offer_never_admits_and_secure_channel_needs_foreground_media_ack(calling):
    native,push,room,cap,headers=calling
    with Session(native[1]) as session:
        session.add(SecureChannel(channel_id='shared',matrix_room_id='!private:example.test',status='active',created_by='bob'))
        delivery=NativePushDelivery(device_id=push,source_key='call:livekit:'+room,channel_id='shared',meeting_id=room,transport='livekit',kind='incoming_call',expires_at=datetime.utcnow()+timedelta(minutes=1))
        session.add(delivery);session.commit();identifier=delivery.id
    path='/mobile/native/calling/offers/'+identifier
    assert native[0].get(path,headers=headers).status_code==409
    assert join(calling).status_code==409
    # Call-only secret cannot acknowledge a different audio transport boundary.
    consent='/mobile/native/calling/devices/'+cap['device_id']+'/channels/shared/media-consent'
    assert native[0].post(consent,headers=headers,json={'acknowledge_customer_sfu_audio_not_e2ee':True}).status_code==401
    ack=native[0].post(consent,headers=bearer(native[3]['alice']),json={'acknowledge_customer_sfu_audio_not_e2ee':True});assert ack.status_code==200,ack.text
    result=native[0].get(path,headers=headers);assert result.status_code==200,result.text
    assert 'token' not in result.json()
    with Session(native[1]) as session: assert not session.exec(select(NativeCallSession)).all()
    answer=native[0].post(path+'/answer',headers=headers,json={'explicit_answer':True,'client_call_id':identifier})
    assert answer.status_code==200,answer.text
    with Session(native[1]) as session:
        secure=session.get(SecureChannel,'shared');secure.matrix_room_id='!replacement:example.test';session.add(secure);session.commit()
    # A replacement encrypted channel requires a new explicit foreground review.
    assert native[0].get(path,headers=headers).status_code==409


def test_worker_android_data_only_call_payload_and_revocation_during_oauth(calling,monkeypatch):
    native,push,room,cap,headers=calling
    sent=[]
    def request(method,url,**kwargs):
        sent.append((url,kwargs))
        if 'oauth2' in url:return Response(200,{},json.dumps({'access_token':'fixture-access-token-'+'x'*24}).encode())
        return Response(200,{},b'{}')
    monkeypatch.setattr(native_push,'request',request)
    monkeypatch.setattr(native_push,'dispatch',REAL_DISPATCH)
    with Session(native[1]) as session:
        assert native_push.run_pending(session)==1
        delivery=session.exec(select(NativePushDelivery)).one()
        assert delivery.status=='delivered'
    message=json.loads(sent[-1][1]['body'])['message']
    assert 'notification' not in message and 'notification' not in message['android']
    assert message['data']=={'type':'native_call','id':delivery.id,'device_id':cap['device_id']}
    assert 'Private' not in str(message) and 'Bob' not in str(message)


def test_native_device_rotation_during_fcm_oauth_prevents_old_call_push(calling,monkeypatch):
    native,push,room,cap,headers=calling
    calls=[]
    def request(method,url,**kwargs):
        calls.append(url)
        assert 'oauth2' in url
        replacement=native[0].post('/mobile/native/calling/devices',headers=bearer(native[3]['alice']),json={'push_device_id':push,'consent':True})
        assert replacement.status_code==201,replacement.text
        assert replacement.json()['device_id']!=cap['device_id']
        return Response(200,{},json.dumps({'access_token':'fixture-access-token-'+'x'*24}).encode())
    monkeypatch.setattr(native_push,'request',request);monkeypatch.setattr(native_push,'dispatch',REAL_DISPATCH)
    with Session(native[1]) as session:
        assert native_push.run_pending(session)==0
        delivery=session.exec(select(NativePushDelivery)).one();assert delivery.status=='cancelled'
    assert len(calls)==1


def test_voip_token_expiry_preserves_independent_standard_apns_alerts(calling,monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    native,_,room,_,_=calling
    key=ec.generate_private_key(ec.SECP256R1())
    for name,value in {'native_push_apns_team_id':'ABCDEFGHIJ','native_push_apns_key_id':'1234567890','native_push_apns_topic':'eu.customer.collab',
        'native_push_apns_private_key':SecretStr(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode())}.items():monkeypatch.setattr(settings,name,value)
    result=native[0].post('/mobile/native/devices',headers=bearer(native[3]['alice']),json={'platform':'ios','token':'a'*64,'calls_enabled':True,'consent':True})
    assert result.status_code==202,result.text
    push=result.json()['id'];proof=native[4][-1][2]['challenge']
    assert native[0].post('/mobile/native/devices/'+push+'/confirm',headers=bearer(native[3]['alice']),json={'challenge':proof}).status_code==200
    result=native[0].post('/mobile/native/calling/devices',headers=bearer(native[3]['alice']),json={'push_device_id':push,'consent':True})
    assert result.status_code==201,result.text
    cap=result.json();headers=bearer(cap['call_secret'])|{'X-FreeCollab-Call-Device':cap['device_id']}
    with Session(native[1]) as session:
        delivery=NativePushDelivery(device_id=push,source_key='call:livekit:'+room,channel_id='shared',meeting_id=room,transport='livekit',kind='incoming_call',expires_at=datetime.utcnow()+timedelta(minutes=1))
        session.add(delivery);session.commit()
        assert native_calls.delivery_target(session,push,delivery.id) is None
    assert native[0].put('/mobile/native/calling/device/voip',headers=headers,json={'token':'b'*64}).status_code==200
    def dispatch(platform,token,payload,**kwargs):
        if platform=='ios':
            assert token=='b'*64 and kwargs['mode']=='voip' and payload=={'type':'native_call','id':delivery.id,'device_id':cap['device_id']}
            assert kwargs['authorize']()
            raise ProviderError('subscription_expired',status=410)
    monkeypatch.setattr(native_push,'dispatch',dispatch)
    with Session(native[1]) as session:
        native_push.run_pending(session)
        assert session.get(NativePushDevice,push,populate_existing=True).active
        assert session.get(NativeCallDevice,cap['device_id'],populate_existing=True).encrypted_voip_token==''


def test_same_origin_api_gateway_prefix_reaches_real_native_call_authority(calling):
    """The shipped gateway sends /api/* to backend (stripping /api).

    Exercise the same route mount with the real call-only API, not the SPA root.
    The actual Android/iOS endpoint builders are verified separately on JVM/Swift.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_session
    from app.api.native_push import router
    native,push,room,cap,headers=calling
    edge=FastAPI();edge.include_router(router,prefix='/api')
    edge.dependency_overrides[get_session]=native[0].app.dependency_overrides[get_session]
    with TestClient(edge) as client:
        listing=client.get('/api/mobile/native/calling/device',headers=bearer(native[3]['alice']))
        assert listing.status_code==200 and listing.json()['items'][0]['id']==cap['device_id']
        assert client.get('/mobile/native/calling/device',headers=bearer(native[3]['alice'])).status_code==404
        admitted=client.post('/api/mobile/native/calling/join',headers=headers,json={'room_id':room,'client_call_id':'e'*32,'explicit_answer':True})
        assert admitted.status_code==200,admitted.text
        session_id=admitted.json()['session_id']
        assert client.post('/api/mobile/native/calling/sessions/'+session_id+'/heartbeat',headers=headers,json={'muted':True}).json()['muted']
        assert cap['call_secret'] not in str(admitted.request.url)


def test_expired_call_capability_cleanup_erases_private_children_without_provider(calling,monkeypatch):
    native,push,room,cap,headers=calling
    result=join(calling);assert result.status_code==200,result.text
    participant=result.json()['participant_id']
    with Session(native[1]) as session:
        row=session.get(NativeCallDevice,cap['device_id']);row.expires_at=datetime.utcnow()-timedelta(seconds=1)
        row.encrypted_voip_token='PRIVATE FIXTURE TOKEN';session.add(row);session.commit()
        monkeypatch.setattr(settings,'native_calls_enabled',False)
        assert native_calls.expire_devices(session,limit=1)==1
        assert session.get(NativeCallDevice,cap['device_id']) is None
        assert not session.exec(select(NativeCallSession)).all()
        assert session.get(SFUParticipant,participant).state=='left'
        assert session.get(NativePushDevice,push) is not None
