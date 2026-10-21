"""Actual native PKCE, device possession, current authority and vendor protocols."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from hashlib import sha256
import json
import secrets
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from pydantic import SecretStr
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app as registered
from app.api import auth, meetings, native_clients, native_push
from app.config import settings
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import NotificationDelivery
from app.models.meetings import Meeting
from app.models.native_clients import NativeClientSession
from app.models.native_push import NativePushDevice, NativePushDelivery
from app.models.notification import Notification, NotificationPreference
from app.models.session import ActiveSession
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.services import native_push as service
from app.services.auth import create_token, revoke_token, token_digest
from app.services.remote_transport import Response, ProviderError

VERIFIER = 'v' * 43
CALLBACK = 'freecollab://native/complete'
CHALLENGE = base64.urlsafe_b64encode(sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()
TOKEN = 'android_device_token_' + 'x' * 80
REAL_DISPATCH = service.dispatch


def bearer(token): return {'Authorization': 'Bearer ' + token}


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'test')
    monkeypatch.setattr(settings, 'jwt_secret', secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, 'matrix_enabled', False)
    monkeypatch.setattr(settings, 'native_push_enabled', True)
    monkeypatch.setattr(settings, 'account_security_key', SecretStr(base64.b64encode(b'n' * 32).decode()))
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, 'native_push_fcm_project_id', 'customer-project')
    account = {'type': 'service_account', 'project_id': 'customer-project', 'client_email': 'sender@customer-project.iam.gserviceaccount.com',
               'private_key': signing.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}
    monkeypatch.setattr(settings, 'native_push_fcm_service_account', SecretStr(json.dumps(account)))
    engine = create_engine(f'sqlite:///{tmp_path / "native-push.db"}', connect_args={'check_same_thread': False, 'timeout':20})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ['alice', 'bob', 'outsider']:
            session.add(User(id=name, name=name.title(), email=name+'@example.test', password_hash='unused'))
        session.add(Workspace(id='team', name='Sensitive workspace'))
        session.add(Channel(id='shared', name='Private channel title'))
        session.flush()
        session.add(WorkspaceChannel(workspace_id='team', channel_id='shared'))
        for name in ['alice', 'bob']:
            session.add(WorkspaceMember(workspace_id='team', user_id=name, role='member'))
            session.add(ChannelMember(channel_id='shared', user_id=name, role='admin' if name == 'bob' else 'member'))
        session.commit()
        parents = {name:create_token(name, session=session) for name in ['alice','bob','outsider']}
    app = FastAPI()
    for route in [auth.router,native_clients.router,native_push.router,meetings.router]: app.include_router(route)
    def sessions():
        with Session(engine) as session: yield session
    app.dependency_overrides[get_session] = sessions
    calls = []
    monkeypatch.setattr(service, 'dispatch', lambda platform, token, payload, **options: calls.append((platform,token,payload,options)))
    with TestClient(app) as client:
        tokens = {}
        for name in parents:
            response = client.post('/native-clients/connect', headers=bearer(parents[name]), json={'code_challenge':CHALLENGE,'redirect_uri':CALLBACK,'client_kind':'mobile'})
            assert response.status_code == 200, response.text
            result = client.post('/native-clients/exchange', json={'code':response.json()['code'],'code_verifier':VERIFIER,'redirect_uri':CALLBACK})
            assert result.status_code == 200, result.text
            tokens[name] = result.json()['access_token']
        yield client,engine,parents,tokens,calls,signing
    engine.dispose()


def register(native, *, name='alice', token=TOKEN, calls=True):
    return native[0].post('/mobile/native/devices',headers=bearer(native[3][name]),json={'platform':'android','token':token,'calls_enabled':calls,'consent':True})


def enable(native, **options):
    response=register(native,**options); assert response.status_code == 202,response.text
    identifier=response.json()['id']; nonce=native[4][-1][2]['challenge']; name=options.get('name','alice')
    confirmed=native[0].post('/mobile/native/devices/'+identifier+'/confirm',headers=bearer(native[3][name]),json={'challenge':nonce})
    assert confirmed.status_code == 200,confirmed.text
    return identifier,nonce


def activity(native, user='alice'):
    with Session(native[1]) as session:
        row=Notification(user_id=user,type='mention',title='Secret sender',body='Confidential message body',created_at=datetime.utcnow()+timedelta(seconds=1))
        session.add(row);session.commit()
        session.add(NotificationDelivery(event_id=row.id,user_id=user,channel_id='shared',notification_id=row.id));session.commit()
        return row.id


def test_disabled_configuration_and_no_secrets(native,monkeypatch):
    client,_,parents,tokens,_,_=native
    response=client.get('/mobile/native/config',headers=bearer(tokens['alice']))
    assert response.headers['cache-control']=='no-store'
    assert response.json()['platforms']==['android'] and not response.json()['callkit']
    assert 'private_key' not in response.text and 'client_email' not in response.text
    monkeypatch.setattr(settings,'native_push_enabled',False)
    assert register(native).status_code==503
    assert client.get('/mobile/native/config',headers=bearer(tokens['alice'])).json()['platforms']==[]


def test_only_linked_mobile_session_can_register(native):
    response=native[0].post('/mobile/native/devices',headers=bearer(native[2]['alice']),json={'platform':'android','token':TOKEN,'consent':True})
    assert response.status_code==403
    assert native[4]==[]


def test_actual_device_proof_private_storage_one_use_and_session_bound(native):
    client,engine,_,tokens,calls,_=native
    response=register(native); identifier=response.json()['id']; nonce=calls[-1][2]['challenge']
    assert nonce not in response.text and TOKEN not in response.text
    with Session(engine) as session:
        row=session.get(NativePushDevice,identifier)
        assert TOKEN not in row.encrypted_token and tokens['alice'] not in row.encrypted_claims and row.challenge_hash!=nonce
    path='/mobile/native/devices/'+identifier+'/confirm'
    assert client.post(path,headers=bearer(tokens['bob']),json={'challenge':nonce}).status_code==404
    assert client.post(path,headers=bearer(tokens['alice']),json={'challenge':'x'*43}).status_code==400
    assert client.post(path,headers=bearer(tokens['alice']),json={'challenge':nonce}).status_code==200
    assert client.post(path,headers=bearer(tokens['alice']),json={'challenge':nonce}).status_code==400
    listing=client.get('/mobile/native/devices',headers=bearer(tokens['alice']))
    assert TOKEN not in listing.text and nonce not in listing.text
    calls.clear(); assert register(native).json()['status']=='enabled' and calls==[]


def test_concurrent_proof_is_one_use(native):
    response=register(native); identifier=response.json()['id']; nonce=native[4][-1][2]['challenge']
    def confirm(_):return native[0].post('/mobile/native/devices/'+identifier+'/confirm',headers=bearer(native[3]['alice']),json={'challenge':nonce}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(confirm,range(2)))==[200,400]


@pytest.mark.parametrize('mutation',['logout','parent_logout','membership','sidecar','preference','expired','blocked'])
def test_worker_cancels_stale_authority_and_activity(native,mutation):
    identifier,_=enable(native);activity(native);native[4].clear()
    with Session(native[1]) as session:
        assert service.queue_activity(session)==1
        if mutation in ['logout','parent_logout']:revoke_token(native[3]['alice'] if mutation=='logout' else native[2]['alice'],session)
        elif mutation=='membership':session.delete(session.get(ChannelMember,('shared','alice')));session.commit()
        elif mutation=='sidecar':session.delete(session.get(NativeClientSession,token_digest(native[3]['alice'])));session.commit()
        elif mutation=='preference':row=session.exec(select(NotificationPreference).where(NotificationPreference.user_id=='alice')).one();row.push=False;session.add(row);session.commit()
        elif mutation=='expired':row=session.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(native[3]['alice']))).one();row.expires_at=datetime.utcnow()-timedelta(seconds=1);session.add(row);session.commit()
        elif mutation=='blocked':row=session.get(User,'alice');row.is_blocked=True;session.add(row);session.commit()
        assert service.run_pending(session)==0
        assert session.exec(select(NativePushDelivery)).one().status=='cancelled'
    assert native[4]==[]


def test_worker_generic_delivery_current_resolution_and_revocation(native):
    enable(native); notification=activity(native);native[4].clear()
    with Session(native[1]) as session:
        assert service.run_pending(session)==1
        row=session.exec(select(NativePushDelivery)).one();identifier=row.id
        assert service.run_pending(session)==0
    payload=native[4][0][2]
    assert payload=={'type':'activity','id':identifier}
    assert all(secret not in json.dumps(payload) for secret in [TOKEN,'Secret','Confidential','shared',native[3]['alice']])
    path='/mobile/native/deliveries/'+identifier
    response=native[0].get(path,headers=bearer(native[3]['alice']))
    assert response.status_code==200 and response.json()['workspace_id']=='team'
    assert native[0].get(path,headers=bearer(native[3]['bob'])).status_code==404
    with Session(native[1]) as session:
        row=session.get(Notification,notification);row.read=True;session.add(row);session.commit()
    assert native[0].get(path,headers=bearer(native[3]['alice'])).status_code==404


def test_incoming_call_alert_is_opaque_and_ended_call_cannot_open(native):
    enable(native,calls=True);native[4].clear()
    response=native[0].post('/channels/shared/meetings',headers=bearer(native[2]['bob']),json={})
    assert response.status_code==201,response.text
    meeting=response.json()['meeting']['id']
    with Session(native[1]) as session:
        assert service.run_pending(session)==1
        row=session.exec(select(NativePushDelivery)).one();identifier=row.id
    assert native[4][-1][2]=={'type':'incoming_call','id':identifier}
    response=native[0].get('/mobile/native/deliveries/'+identifier,headers=bearer(native[3]['alice']))
    assert response.status_code==200 and response.json()['meeting_id']==meeting
    with Session(native[1]) as session:
        row=session.get(Meeting,meeting);row.ended_at=datetime.utcnow();session.add(row);session.commit()
    assert native[0].get('/mobile/native/deliveries/'+identifier,headers=bearer(native[3]['alice'])).status_code==404


def test_call_opt_out_and_foreign_channel_do_not_ring(native):
    enable(native,calls=False);native[4].clear()
    assert native[0].post('/channels/shared/meetings',headers=bearer(native[2]['bob']),json={}).status_code==201
    with Session(native[1]) as session:assert service.run_pending(session)==0
    assert native[4]==[]


def test_device_removal_and_token_rotation(native):
    identifier,_=enable(native);replacement,_=enable(native,token=TOKEN+'new')
    with Session(native[1]) as session:
        assert session.get(NativePushDevice,identifier) is None
        assert session.get(NativePushDevice,replacement).active
    assert native[0].delete('/mobile/native/devices/'+replacement,headers=bearer(native[3]['bob'])).status_code==404
    assert native[0].delete('/mobile/native/devices/'+replacement,headers=bearer(native[3]['alice'])).status_code==204


def test_provider_expiration_disables_device_bounded_retry(native,monkeypatch):
    identifier,_=enable(native);activity(native)
    monkeypatch.setattr(service,'dispatch',lambda *args,**kwargs:(_ for _ in ()).throw(ProviderError('subscription_expired',status=410)))
    with Session(native[1]) as session:
        assert service.run_pending(session)==0
        assert not session.get(NativePushDevice,identifier).active
        assert session.exec(select(NativePushDelivery)).one().status=='failed'


def test_fcm_adapter_signs_customer_assertion_and_uses_fixed_destinations(native,monkeypatch):
    outgoing=[]
    def request(method,url,**options):
        outgoing.append((method,url,options))
        return Response(200,{},json.dumps({'access_token':'short_lived_provider_token_12345'} if 'oauth2' in url else {'name':'projects/customer-project/messages/id'}).encode())
    monkeypatch.setattr(service,'request',request)
    REAL_DISPATCH('android',TOKEN,{'type':'incoming_call','id':'a'*32},ttl=90)
    assert [item[1] for item in outgoing]==['https://oauth2.googleapis.com/token','https://fcm.googleapis.com/v1/projects/customer-project/messages:send']
    assertion=parse_qs(outgoing[0][2]['body'].decode())['assertion'][0]
    claims=jwt.decode(assertion,native[5].public_key(),algorithms=['RS256'],audience='https://oauth2.googleapis.com/token')
    assert claims['scope']=='https://www.googleapis.com/auth/firebase.messaging' and claims['exp']-claims['iat']==300
    sent=json.loads(outgoing[1][2]['body'])['message']
    assert sent['data']=={'type':'incoming_call','id':'a'*32} and sent['android']['ttl']=='90s'
    assert TOKEN not in sent['notification']['body'] and 'shared' not in json.dumps(sent)


def test_apns_adapter_requires_http2_customer_topic_and_es256(native,monkeypatch):
    key=ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(settings,'native_push_apns_team_id','ABCDEFGHIJ');monkeypatch.setattr(settings,'native_push_apns_key_id','1234567890')
    monkeypatch.setattr(settings,'native_push_apns_topic','eu.customer.collab')
    monkeypatch.setattr(settings,'native_push_apns_private_key',SecretStr(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()))
    outgoing=[]
    monkeypatch.setattr(service,'request',lambda method,url,**options:outgoing.append((method,url,options)) or Response(200,{},b''))
    REAL_DISPATCH('ios','a'*64,{'type':'activity','id':'b'*32})
    options=outgoing[0][2]
    assert options['http2'] is True and outgoing[0][1]=='https://api.push.apple.com/3/device/'+'a'*64
    bearer_token=options['headers']['Authorization'].split(' ')[1]
    assert jwt.decode(bearer_token,key.public_key(),algorithms=['ES256'])['iss']=='ABCDEFGHIJ'
    assert jwt.get_unverified_header(bearer_token)['kid']=='1234567890'
    assert options['headers']['apns-topic']=='eu.customer.collab'
    assert json.loads(options['body'])['aps']['alert']=={'title':'FreeCollab','body':'New FreeCollab activity'}


def test_bad_provider_payload_never_sends(native,monkeypatch):
    outgoing=[];monkeypatch.setattr(service,'request',lambda *args,**kwargs:outgoing.append(args))
    with pytest.raises(ProviderError):REAL_DISPATCH('android',TOKEN,{'type':'activity','id':'a'*32,'message':'secret'})
    assert outgoing==[]


def test_actual_apns_tls_http2_through_connect_tunnel(native,tmp_path,monkeypatch):
    """Real pinned transport, TLS hostname verification, ALPN h2 and vendor JWT."""
    import select as sockets_select
    import socket
    import socketserver
    import ssl
    import subprocess
    import threading
    from h2.config import H2Configuration
    from h2.connection import H2Connection
    from h2.events import RequestReceived, DataReceived, StreamEnded
    from app.services import remote_transport
    key=ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(settings,'native_push_apns_team_id','ABCDEFGHIJ');monkeypatch.setattr(settings,'native_push_apns_key_id','1234567890')
    monkeypatch.setattr(settings,'native_push_apns_topic','eu.customer.collab')
    monkeypatch.setattr(settings,'native_push_apns_private_key',SecretStr(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()))
    key_path,cert_path=tmp_path/'receiver-key.pem',tmp_path/'receiver-cert.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key_path),'-out',str(cert_path),'-days','1','-subj','/CN=api.push.apple.com','-addext','subjectAltName=DNS:api.push.apple.com'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert_path,key_path);tls.set_alpn_protocols(['h2'])
    observed=[]
    class Receiver(socketserver.BaseRequestHandler):
        def handle(self):
            with tls.wrap_socket(self.request,server_side=True) as connection:
                connection.settimeout(5)
                assert connection.selected_alpn_protocol()=='h2'
                protocol=H2Connection(config=H2Configuration(client_side=False,header_encoding='utf-8'))
                protocol.initiate_connection();connection.sendall(protocol.data_to_send())
                headers={};body=bytearray()
                while True:
                    raw=connection.recv(65536)
                    if not raw:return
                    for event in protocol.receive_data(raw):
                        if isinstance(event,RequestReceived):headers=dict(event.headers)
                        elif isinstance(event,DataReceived):
                            body.extend(event.data);protocol.acknowledge_received_data(event.flow_controlled_length,event.stream_id)
                        elif isinstance(event,StreamEnded):
                            token=headers['authorization'].split(' ')[1]
                            claims=jwt.decode(token,key.public_key(),algorithms=['ES256'])
                            observed.append({'authority':headers[':authority'],'path':headers[':path'],'claims':claims,'topic':headers['apns-topic'],'payload':json.loads(body)})
                            protocol.send_headers(event.stream_id,[(':status','200'),('content-length','0')],end_stream=True)
                            connection.sendall(protocol.data_to_send());return
                    outgoing=protocol.data_to_send()
                    if outgoing:connection.sendall(outgoing)
    receiver=socketserver.ThreadingTCPServer(('127.0.0.1',0),Receiver);receiver.daemon_threads=True
    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5);header=b''
            while b'\r\n\r\n' not in header:
                chunk=self.request.recv(4096)
                if not chunk or len(header)>8192:return
                header+=chunk
            assert header.split(b'\r\n',1)[0]==b'CONNECT 8.8.8.8:443 HTTP/1.1'
            with socket.create_connection(('127.0.0.1',receiver.server_address[1]),timeout=5) as upstream:
                self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                try:
                    while True:
                        ready,_,_=sockets_select.select([self.request,upstream],[],[],5)
                        if not ready:return
                        for source in ready:
                            data=source.recv(65536)
                            if not data:return
                            (upstream if source is self.request else self.request).sendall(data)
                except (OSError,TimeoutError):pass
    proxy=socketserver.ThreadingTCPServer(('127.0.0.1',0),Proxy);proxy.daemon_threads=True
    threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in [receiver,proxy]]
    for thread in threads:thread.start()
    monkeypatch.setenv('SSL_CERT_FILE',str(cert_path))
    monkeypatch.setattr(remote_transport.pinned,'public_addresses',lambda host,timeout=5:['8.8.8.8'])
    monkeypatch.setattr(remote_transport.pinned,'getproxies',lambda:{'https':'http://127.0.0.1:'+str(proxy.server_address[1])})
    monkeypatch.setattr(service,'request',remote_transport.request)
    try:
        REAL_DISPATCH('ios','a'*64,{'type':'incoming_call','id':'c'*32},ttl=60)
        assert len(observed)==1
        result=observed[0]
        assert result['authority']=='api.push.apple.com' and result['path']=='/3/device/'+'a'*64
        assert result['topic']=='eu.customer.collab' and result['claims']['iss']=='ABCDEFGHIJ'
        assert result['payload']['type']=='incoming_call' and result['payload']['id']=='c'*32
        assert result['payload']['aps']['alert']['body']=='Incoming FreeCollab call'
    finally:
        for server in [proxy,receiver]:server.shutdown();server.server_close()
        for thread in threads:thread.join(timeout=3)


@pytest.mark.parametrize('mutation',['provider','scope_sidecar','workspace_member','outside_scope'])
def test_worker_retains_original_customer_sso_scope(native,mutation):
    client,engine,parents,tokens,_,_=native
    with Session(engine) as session:
        session.add(WorkspaceOIDCProvider(workspace_id='team',name='Customer IdP',issuer_url='https://idp.example.test',client_id='own-registration',enabled=True,revision=7,created_by='alice'))
        scoped=create_token('alice',session=session,auth_methods=['sso'],workspace_scope={'workspace_id':'team','provider_revision':7})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(scoped),workspace_id='team',provider_revision=7));session.commit()
    grant=client.post('/native-clients/connect',headers=bearer(scoped),json={'code_challenge':CHALLENGE,'redirect_uri':CALLBACK,'client_kind':'mobile'})
    assert grant.status_code==200,grant.text
    exchange=client.post('/native-clients/exchange',json={'code':grant.json()['code'],'code_verifier':VERIFIER,'redirect_uri':CALLBACK})
    assert exchange.status_code==200,exchange.text
    tokens['alice']=exchange.json()['access_token'];enable(native);activity(native);native[4].clear()
    with Session(engine) as session:
        assert service.queue_activity(session)==1
        if mutation=='provider':
            row=session.get(WorkspaceOIDCProvider,'team');row.revision=8;session.add(row)
        elif mutation=='scope_sidecar':session.delete(session.get(WorkspaceOIDCSession,token_digest(tokens['alice'])))
        elif mutation=='workspace_member':session.delete(session.get(WorkspaceMember,('team','alice')))
        else:
            session.add(Workspace(id='foreign',name='Foreign'));session.add(Channel(id='foreign-channel',name='Foreign channel'));session.flush()
            session.add(WorkspaceChannel(workspace_id='foreign',channel_id='foreign-channel'))
            session.add(WorkspaceMember(workspace_id='foreign',user_id='alice',role='member'))
            session.add(ChannelMember(channel_id='foreign-channel',user_id='alice'))
            delivery=session.exec(select(NativePushDelivery)).one();delivery.channel_id='foreign-channel';session.add(delivery)
        session.commit()
        assert service.run_pending(session)==0
        assert session.exec(select(NativePushDelivery)).one().status=='cancelled'
    assert native[4]==[]


def test_logged_out_registration_can_transfer_only_after_new_device_proof(native):
    identifier,_=enable(native)
    assert register(native,name='bob').status_code==409
    with Session(native[1]) as session:revoke_token(native[3]['alice'],session)
    response=register(native,name='bob');assert response.status_code==202,response.text
    with Session(native[1]) as session:
        assert session.get(NativePushDevice,identifier) is None
        new=session.get(NativePushDevice,response.json()['id'])
        assert new.user_id=='bob' and not new.active and new.confirmed_at is None
    nonce=native[4][-1][2]['challenge']
    assert native[0].post('/mobile/native/devices/'+response.json()['id']+'/confirm',headers=bearer(native[3]['bob']),json={'challenge':nonce}).status_code==200


def test_revocation_during_fcm_oauth_exchange_prevents_message_send(native,monkeypatch):
    identifier,_=enable(native);activity(native);outgoing=[]
    monkeypatch.setattr(service,'dispatch',REAL_DISPATCH)
    def request(method,url,**options):
        outgoing.append(url)
        assert url=='https://oauth2.googleapis.com/token'
        with Session(native[1]) as revoker:revoke_token(native[2]['alice'],revoker)
        return Response(200,{},json.dumps({'access_token':'temporary_provider_credential_12345'}).encode())
    monkeypatch.setattr(service,'request',request)
    with Session(native[1]) as session:
        assert service.run_pending(session)==0
        row=session.exec(select(NativePushDelivery)).one()
        assert row.status=='cancelled' and row.failure_code=='access_or_activity_changed'
    assert outgoing==['https://oauth2.googleapis.com/token']


def test_organization_scope_cannot_enumerate_remove_or_rebind_personal_device(native):
    identifier,_=enable(native)
    client,engine,_,tokens,_,_=native
    with Session(engine) as session:
        session.add(WorkspaceOIDCProvider(workspace_id='team',name='Customer IdP',issuer_url='https://idp.example.test',client_id='customer-owned',enabled=True,revision=3,created_by='alice'))
        source=create_token('alice',session=session,auth_methods=['sso'],workspace_scope={'workspace_id':'team','provider_revision':3})
        session.add(WorkspaceOIDCSession(token_hash=token_digest(source),workspace_id='team',provider_revision=3));session.commit()
    grant=client.post('/native-clients/connect',headers=bearer(source),json={'code_challenge':CHALLENGE,'redirect_uri':CALLBACK,'client_kind':'mobile'})
    child=client.post('/native-clients/exchange',json={'code':grant.json()['code'],'code_verifier':VERIFIER,'redirect_uri':CALLBACK}).json()['access_token']
    for token in [source,child]:
        assert client.get('/mobile/native/devices',headers=bearer(token)).json()=={'items':[]}
        assert client.delete('/mobile/native/devices/'+identifier,headers=bearer(token)).status_code==404
    original=tokens['alice'];tokens['alice']=child
    assert register(native).status_code==403
    scoped,_=enable(native,token=TOKEN+'scoped')
    rows=client.get('/mobile/native/devices',headers=bearer(child)).json()['items']
    assert len(rows)==1 and rows[0]['id']==scoped
    assert len(client.get('/mobile/native/devices',headers=bearer(original)).json()['items'])==2
    assert client.delete('/mobile/native/devices/'+scoped,headers=bearer(child)).status_code==204
    # An expired old source must not strand a managed SSO-only user. Rebinding
    # still requires a newly delivered proof after the old source is invalid.
    with Session(engine) as session:revoke_token(original,session)
    reenrolled,_=enable(native,token=TOKEN)
    assert client.get('/mobile/native/devices',headers=bearer(child)).json()['items'][0]['id']==reenrolled
    assert client.delete('/mobile/native/devices/'+reenrolled,headers=bearer(native[2]['alice'])).status_code==204


def test_removal_during_fcm_oauth_cancels_cleanly_without_stale_orm(native,monkeypatch):
    identifier,_=enable(native);activity(native);outgoing=[]
    monkeypatch.setattr(service,'dispatch',REAL_DISPATCH)
    def request(method,url,**options):
        outgoing.append(url)
        assert url=='https://oauth2.googleapis.com/token'
        response=native[0].delete('/mobile/native/devices/'+identifier,headers=bearer(native[3]['alice']))
        assert response.status_code==204,response.text
        return Response(200,{},json.dumps({'access_token':'temporary_provider_credential_12345'}).encode())
    monkeypatch.setattr(service,'request',request)
    with Session(native[1]) as session:
        assert service.run_pending(session)==0
        assert session.exec(select(NativePushDelivery)).all()==[]
    assert outgoing==['https://oauth2.googleapis.com/token']


def test_reregistration_during_fcm_oauth_cannot_deliver_old_source_reference(native,monkeypatch):
    identifier,_=enable(native);activity(native);outgoing=[]
    monkeypatch.setattr(service,'dispatch',REAL_DISPATCH)
    def request(method,url,**options):
        outgoing.append(url)
        assert url=='https://oauth2.googleapis.com/token'
        # Simulate a separately confirmed current registration on this device.
        # Registration always removes the old delivery before changing authority.
        with Session(native[1]) as editor:
            from sqlalchemy import delete
            editor.exec(delete(NativePushDelivery).where(NativePushDelivery.device_id==identifier))
            device=editor.get(NativePushDevice,identifier)
            device.confirmed_at=datetime.utcnow()+timedelta(microseconds=1)
            editor.add(device);editor.commit()
        return Response(200,{},json.dumps({'access_token':'temporary_provider_credential_12345'}).encode())
    monkeypatch.setattr(service,'request',request)
    with Session(native[1]) as session:
        assert service.run_pending(session)==0
        assert session.exec(select(NativePushDelivery)).all()==[]
    assert outgoing==['https://oauth2.googleapis.com/token']
