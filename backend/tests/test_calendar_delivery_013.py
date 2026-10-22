"""Reviewed provider effect queue and revocable native public calendar grants."""
import base64
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine, select

from app import main as application
from app.api import calendar_delivery, integrations_content
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.calendar_delivery import CalendarProviderJob, CalendarProviderPublication, CalendarSubscription
from app.models.channel import Channel, ChannelMember
from app.models.remote_sync import RemoteSyncConnection
from app.models.secure_channel import SecureChannel
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.user import User
from app.services import calendar_delivery as delivery, calendar_subscriptions, remote_sync, provider_calendar
from app.services.auth import create_token, hash_password
from app.services.remote_transport import ProviderError

PASSWORD='Calendar-delivery-fixture!2026'
FIELDS={'title':'Private planning title','description':'Never put this private discussion in a public feed','location':'Private office',
        'starts_at':(datetime.utcnow()+timedelta(days=1)).replace(hour=9,minute=0,second=0,microsecond=0).isoformat()+'Z',
        'ends_at':(datetime.utcnow()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0).isoformat()+'Z',
        'all_day':False,'timezone':'UTC','reminder_minutes':15,'external_uid':None,
        'recurrence':{'frequency':'weekly','interval':1,'count':4}}


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'environment','test');monkeypatch.setattr(settings,'matrix_enabled',False)
    monkeypatch.setattr(settings,'sync_enabled',True);monkeypatch.setattr(settings,'webhooks_enabled',False)
    monkeypatch.setattr(settings,'public_app_url','http://localhost:3000')
    monkeypatch.setattr(settings,'account_security_key',SecretStr(base64.b64encode(b'c'*32).decode()))
    engine=create_engine('sqlite:///'+str(tmp_path/'calendar.db'),connect_args={'check_same_thread':False})
    @event.listens_for(engine,'connect')
    def fks(connection,_):connection.execute('PRAGMA foreign_keys=ON')
    SQLModel.metadata.create_all(engine)
    tokens={}
    with Session(engine) as session:
        for who in ['owner','peer','outsider']:
            session.add(User(id=who,name=who,email=who+'@example.com',password_hash=hash_password(PASSWORD)))
        session.add_all([Workspace(id='workspace',name='Studio'),Workspace(id='other',name='Other'),Channel(id='room',name='Room'),Channel(id='elsewhere',name='Other')]);session.commit()
        session.add_all([WorkspaceMember(workspace_id='workspace',user_id='owner',role='owner'),WorkspaceMember(workspace_id='workspace',user_id='peer',role='member'),WorkspaceMember(workspace_id='other',user_id='outsider',role='owner'),WorkspaceChannel(workspace_id='workspace',channel_id='room'),WorkspaceChannel(workspace_id='other',channel_id='elsewhere'),ChannelMember(channel_id='room',user_id='owner',role='admin'),ChannelMember(channel_id='room',user_id='peer',role='member'),ChannelMember(channel_id='elsewhere',user_id='outsider',role='admin')]);session.commit()
        for who in ['owner','peer','outsider']:tokens[who]=create_token(who,session)
        row=RemoteSyncConnection(id='google',workspace_id='workspace',channel_id='room',created_by='owner',grant_user_id='owner',name='Customer Google calendar',provider='google',kind='calendar',config_json='{"client_id":"customer-client","calendar_id":"primary"}',enabled=True,tested_revision=1,status='ready')
        remote_sync.save_credentials(row,{'access_token':'fixture-access','refresh_token':'fixture-refresh','scope':'https://www.googleapis.com/auth/calendar.events'})
        session.add(row);session.commit()
    app=FastAPI();app.include_router(calendar_delivery.router);app.include_router(integrations_content.router)
    def sessions():
        with Session(engine) as session:yield session
    app.dependency_overrides[get_session]=sessions
    with TestClient(app) as client:
        value=SimpleNamespace(client=client,engine=engine,tokens=tokens)
        response=client.post('/channels/room/integrations/calendar',json={'id':'event','fields':FIELDS},headers=headers(value))
        assert response.status_code==200,response.text
        yield value
    engine.dispose()


def headers(f,who='owner'):return {'Authorization':'Bearer '+f.tokens[who]}


def feed(f,**changes):
    response=f.client.post('/channels/room/calendar/subscriptions',headers=headers(f),json={'password':PASSWORD,'public_disclosure':True,**changes})
    assert response.status_code==201,response.text
    return response.json()


def path(feed):return urlsplit(feed['subscription_url']).path.removeprefix('/api')


def review(f,action='publish',**changes):
    body={'connection_id':'google','connection_revision':1,'event_revision':1,'publication_revision':0,'action':action,'attendees':[{'email':'Invitee@example.net','name':'Invited person'}],**changes}
    result=f.client.post('/channels/room/calendar/event/provider/preview',headers=headers(f),json=body)
    assert result.status_code==200,result.text
    return body,result.json()


def queue(f,body,reviewed,**changes):
    return f.client.post('/channels/room/calendar/event/provider/actions',headers=headers(f),json={**body,'request_id':'unique-calendar-action-001','review_digest':reviewed['review_digest'],'provider_disclosure':True,'send_invitations':True,**changes})


def tick(f):
    with Session(f.engine) as session:return delivery.run_pending(session)


def test_feed_is_explicit_minimal_no_emails_and_headers_are_private(fixture):
    f=fixture
    assert f.client.post('/channels/room/calendar/subscriptions',headers=headers(f),json={'password':PASSWORD}).status_code==422
    item=feed(f)
    response=f.client.get(path(item))
    assert response.status_code==200 and response.text.count('BEGIN:VEVENT')==4
    assert 'SUMMARY:Busy' in response.text
    assert FIELDS['title'] not in response.text and FIELDS['description'] not in response.text and FIELDS['location'] not in response.text
    assert '@example' not in response.text and 'ATTENDEE' not in response.text
    assert response.headers['cache-control']=='private, no-store' and response.headers['referrer-policy']=='no-referrer'
    assert f.client.get(path(item),headers={'If-None-Match':response.headers['etag']}).status_code==304
    with Session(f.engine) as session:
        stored=session.get(CalendarSubscription,item['id'])
        assert item['subscription_url'].split('/')[-1].removesuffix('.ics') not in stored.token_hash


def test_optional_titles_fold_and_escape_without_calendar_injection(fixture):
    f=fixture
    fields={**FIELDS,'title':'Möte\r\nATTENDEE:mailto:secret@example.com '+('Å'*80)}
    assert f.client.post('/channels/room/integrations/calendar',headers=headers(f),json={'id':'event','expected_revision':1,'fields':fields}).status_code==200
    item=feed(f,include_titles=True)
    response=f.client.get(path(item));assert response.status_code==200
    assert '\\nATTENDEE:mailto:secret@example.com' in response.text
    assert '\r\nATTENDEE:' not in response.text
    assert all(len(line.encode())<=75 for line in response.text.split('\r\n'))


@pytest.mark.parametrize('change',['member','role','authversion','blocked','expired','revoked','secure'])
def test_feed_rechecks_every_capability_fetch_even_conditional(fixture,change):
    f=fixture;item=feed(f);first=f.client.get(path(item));assert first.status_code==200
    with Session(f.engine) as session:
        if change=='member':session.delete(session.get(ChannelMember,('room','owner')))
        elif change=='role':session.get(ChannelMember,('room','owner')).role='member';session.add(session.get(ChannelMember,('room','owner')))
        elif change=='authversion':session.get(AccountSecurity,'owner').auth_version+=1;session.add(session.get(AccountSecurity,'owner'))
        elif change=='blocked':session.get(User,'owner').is_blocked=True;session.add(session.get(User,'owner'))
        elif change=='expired':session.get(CalendarSubscription,item['id']).expires_at=datetime.utcnow()-timedelta(seconds=1);session.add(session.get(CalendarSubscription,item['id']))
        elif change=='revoked':session.get(CalendarSubscription,item['id']).revoked_at=datetime.utcnow();session.add(session.get(CalendarSubscription,item['id']))
        else:session.add(SecureChannel(channel_id='room',created_by='owner',status='preparing'))
        session.commit()
    denied=f.client.get(path(item),headers={'If-None-Match':first.headers['etag']})
    assert denied.status_code==404 and denied.headers['Referrer-Policy']=='no-referrer' and 'no-store' in denied.headers['Cache-Control']


def test_feed_management_rejects_outsiders_members_and_secure_plaintext(fixture):
    f=fixture
    for who in ['peer','outsider']:
        assert f.client.post('/channels/room/calendar/subscriptions',headers=headers(f,who),json={'password':PASSWORD,'public_disclosure':True}).status_code==403
    with Session(f.engine) as session:session.add(SecureChannel(channel_id='room',created_by='owner',status='preparing'));session.commit()
    assert f.client.post('/channels/room/calendar/subscriptions',headers=headers(f),json={'password':PASSWORD,'public_disclosure':True}).status_code==409


def test_feed_token_redaction_preserves_structured_access_formatter():
    import logging
    from app.services.capability_logs import CapabilityLogFilter
    secret='test-secret-calendar-capability'
    record=logging.LogRecord('uvicorn.access',logging.INFO,'',0,'%s - "%s %s HTTP/%s" %d',('127.0.0.1','GET','/api/calendar/subscriptions/abc/'+secret+'.ics','1.1',200),None)
    CapabilityLogFilter().filter(record)
    assert len(record.args)==5 and secret not in record.getMessage()


def test_revoke_reauthentication_precedes_channel_locks_and_access_is_rechecked(fixture,monkeypatch):
    f=fixture;item=feed(f);steps=[]
    original_grant=calendar_subscriptions.grant
    original_reauth=calendar_delivery.ensure_reauthentication
    def grant(*args,**kwargs):
        if kwargs.get('lock'):steps.append('channel-lock')
        return original_grant(*args,**kwargs)
    def reauth(*args,**kwargs):
        result=original_reauth(*args,**kwargs);steps.append('reauthentication');return result
    monkeypatch.setattr(calendar_subscriptions,'grant',grant);monkeypatch.setattr(calendar_delivery,'ensure_reauthentication',reauth)
    response=f.client.request('DELETE','/channels/room/calendar/subscriptions/'+item['id'],headers=headers(f),json={'password':PASSWORD})
    assert response.status_code==200 and steps==['reauthentication','channel-lock']
    assert f.client.get(path(item)).status_code==404


def test_preview_is_authoritative_and_queue_requires_exact_consent_and_review(fixture):
    f=fixture;body,reviewed=review(f)
    assert reviewed['attendees'][0]['email']=='invitee@example.net' and reviewed['fields']['title']==FIELDS['title']
    assert queue(f,body,reviewed,provider_disclosure=False).status_code==422
    assert queue(f,body,reviewed,send_invitations=False).status_code==422
    assert queue(f,body,reviewed,attendees=[{'email':'different@example.net'}]).status_code==409
    result=queue(f,body,reviewed);assert result.status_code==202,result.text
    again=queue(f,body,reviewed);assert again.status_code==202 and again.json()['id']==result.json()['id']
    with Session(f.engine) as session:
        stored=session.get(CalendarProviderJob,result.json()['id'])
        assert 'invitee@example.net' not in stored.encrypted_payload and 'access_token' not in stored.claims_json


def test_worker_delivers_once_links_sync_receipt_and_scrubs_job(fixture,monkeypatch):
    f=fixture;body,reviewed=review(f);result=queue(f,body,reviewed);assert result.status_code==202,result.text
    effects=[]
    def publish(provider,config,secret,fields,**kwargs):
        effects.append(kwargs);assert secret['access_token']=='fixture-access'
        return {'id':'remote-created','etag':'"one"','fields':fields,'attendees':[{'email':'invitee@example.net','response':'needs-action'}],'organizer_self':True}
    monkeypatch.setattr(provider_calendar,'calendar_publish',publish)
    assert tick(f)==1 and tick(f)==0 and len(effects)==1
    state=f.client.get('/channels/room/calendar/provider-delivery',headers=headers(f)).json()
    assert state['publications'][0]['state']=='ready' and state['jobs'][0]['status']=='succeeded'
    assert queue(f,body,reviewed).status_code==202
    with Session(f.engine) as session:
        stored=session.get(CalendarProviderJob,result.json()['id']);assert stored.encrypted_payload is None and stored.claims_json=='{}'


@pytest.mark.parametrize('change',['source_revision','event_revision','source_owner','logout','secure'])
def test_worker_rechecks_source_event_session_and_encryption_before_dispatch(fixture,monkeypatch,change):
    f=fixture;body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    if change=='event_revision':
        assert f.client.post('/channels/room/integrations/calendar',headers=headers(f),json={'id':'event','expected_revision':1,'fields':{**FIELDS,'title':'Changed'}}).status_code==200
    else:
        with Session(f.engine) as session:
            if change=='source_revision':session.get(RemoteSyncConnection,'google').revision+=1;session.add(session.get(RemoteSyncConnection,'google'))
            elif change=='source_owner':session.get(RemoteSyncConnection,'google').grant_user_id='peer';session.add(session.get(RemoteSyncConnection,'google'))
            elif change=='logout':session.get(AccountSecurity,'owner').auth_version+=1;session.add(session.get(AccountSecurity,'owner'))
            else:session.add(SecureChannel(channel_id='room',created_by='owner',status='preparing'))
            session.commit()
    monkeypatch.setattr(provider_calendar,'calendar_publish',lambda *args,**kwargs:pytest.fail('Revoked source reached external provider'))
    assert tick(f)==0
    with Session(f.engine) as session:assert session.exec(select(CalendarProviderJob)).one().status=='cancelled'


def test_uncertain_effect_never_automatically_resends_and_readonly_reconcile_recovers(fixture,monkeypatch):
    f=fixture;body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    effects=[]
    def uncertain(*args,**kwargs):effects.append(1);raise ProviderError('network_error')
    monkeypatch.setattr(provider_calendar,'calendar_publish',uncertain)
    assert tick(f)==0 and tick(f)==0 and effects==[1]
    state=f.client.get('/channels/room/calendar/provider-delivery',headers=headers(f)).json();assert state['publications'][0]['state']=='uncertain'
    body,reviewed=review(f,action='reconcile',publication_revision=1)
    assert queue(f,body,reviewed,request_id='unique-reconcile-action-002').status_code==202
    monkeypatch.setattr(provider_calendar,'calendar_reconcile',lambda *args,**kwargs:{'id':'recovered','etag':'"recovered"','fields':FIELDS,'attendees':[]})
    assert tick(f)==1 and effects==[1]


def test_account_cleanup_revokes_feeds_and_scrubs_external_addresses(fixture):
    f=fixture;item=feed(f);body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    with Session(f.engine) as session:delivery.erase_for_account(session,'owner');session.commit()
    assert f.client.get(path(item)).status_code==404
    with Session(f.engine) as session:
        job=session.exec(select(CalendarProviderJob)).one();assert job.status=='cancelled' and job.encrypted_payload is None and job.claims_json=='{}'


def test_definite_401_refresh_saves_rotated_token_before_single_effect_retry(fixture,monkeypatch):
    f=fixture;body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    effects=[]
    def publish(provider,config,secret,fields,**kwargs):
        effects.append(secret['access_token'])
        if len(effects)==1:raise ProviderError('unauthorized',status=401)
        with Session(f.engine) as session:
            assert remote_sync.credentials(session.get(RemoteSyncConnection,'google'))['refresh_token']=='rotated-refresh'
        return {'id':'remote-refreshed','etag':'"refreshed"','fields':fields,'attendees':[]}
    monkeypatch.setattr(provider_calendar,'calendar_publish',publish)
    monkeypatch.setattr(delivery.remote_providers,'refresh_tokens',lambda *args,**kwargs:{'access_token':'rotated-access','refresh_token':'rotated-refresh','scope':'https://www.googleapis.com/auth/calendar.events'})
    assert tick(f)==1 and effects==['fixture-access','rotated-access'] and tick(f)==0


def test_cancel_and_rsvp_review_uses_fenced_provider_schedule_and_roster(fixture,monkeypatch):
    f=fixture
    body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    provider_fields={**FIELDS,'title':'Actual provider schedule'}
    monkeypatch.setattr(provider_calendar,'calendar_publish',lambda *args,**kwargs:{'id':'provider-reviewed','etag':'"reviewed"','fields':provider_fields,'attendees':[{'email':'actual-recipient@example.net','response':'needs-action'}]})
    assert tick(f)==1
    for action in ['cancel','respond']:
        _,value=review(f,action=action,publication_revision=2,response='accepted' if action=='respond' else None)
        assert value['fields']['title']=='Actual provider schedule' and value['attendees'][0]['email']=='actual-recipient@example.net'
    with Session(f.engine) as session:
        row=session.exec(select(CalendarProviderPublication)).one();row.encrypted_result=None;session.add(row);session.commit()
    denied=f.client.post('/channels/room/calendar/event/provider/preview',headers=headers(f),json={**body,'action':'cancel','publication_revision':2})
    assert denied.status_code==409 and 'Read provider status' in denied.text


def test_revocation_during_refresh_prevents_effect_retry(fixture,monkeypatch):
    f=fixture;body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    attempts=[]
    def publish(*args,**kwargs):attempts.append(1);raise ProviderError('unauthorized',status=401)
    def refresh(*args,**kwargs):
        with Session(f.engine) as session:
            row=session.get(RemoteSyncConnection,'google');row.enabled=False;session.add(row);session.commit()
        return {'access_token':'revoked-rotated','scope':'https://www.googleapis.com/auth/calendar.events'}
    monkeypatch.setattr(provider_calendar,'calendar_publish',publish);monkeypatch.setattr(delivery.remote_providers,'refresh_tokens',refresh)
    assert tick(f)==0 and attempts==[1]
    with Session(f.engine) as session:assert session.exec(select(CalendarProviderJob)).one().status=='uncertain'


def test_worker_binds_each_provider_wire_request_to_original_session(fixture,monkeypatch):
    f=fixture;body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    wire=[]
    def transport(*args,**kwargs):
        wire.append(args[0]);return {'provider':'accepted-first-resource'},{}
    with Session(f.engine) as worker:
        def publish(*args,**kwargs):
            provider_calendar._json('POST','https://www.googleapis.com/owned-fixture',{})
            # Inject the new database authorization version at the response
            # boundary. This uses the worker transaction because SQLite blocks
            # concurrent writers; the actual fresh scalar authority check still
            # runs before the second wire call, just as on PostgreSQL.
            state=worker.get(AccountSecurity,'owner');state.auth_version+=1;worker.add(state);worker.flush()
            provider_calendar._json('PATCH','https://www.googleapis.com/owned-fixture/instance',{})
            pytest.fail('A revoked recurring action continued')
        monkeypatch.setattr(delivery.remote_providers,'_json',transport);monkeypatch.setattr(provider_calendar,'calendar_publish',publish)
        assert delivery.run_pending(worker)==0 and wire==['POST']
    with Session(f.engine) as session:assert session.exec(select(CalendarProviderJob)).one().status=='uncertain'


@pytest.mark.parametrize('code',['combined_series_edit_requires_separate_review','cancelled_occurrence_requires_new_series'])
def test_definite_before_effect_limit_is_actionable_and_never_automatically_retried(fixture,monkeypatch,code):
    f=fixture;body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    effects=[]
    def refuse(*args,**kwargs):effects.append(1);raise ProviderError(code)
    monkeypatch.setattr(provider_calendar,'calendar_publish',refuse)
    assert tick(f)==0 and tick(f)==0 and effects==[1]
    with Session(f.engine) as session:
        job=session.exec(select(CalendarProviderJob)).one()
        assert job.status=='failed' and job.failure_code==code and job.encrypted_payload is None


@pytest.mark.parametrize('status',[401,403,412])
def test_later_rejected_series_request_after_accepted_effect_stays_uncertain_without_retry(fixture,monkeypatch,status):
    f=fixture;body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    wire=[];refresh=[]
    def transport(method,*args,**kwargs):
        wire.append(method)
        if len(wire)>1:raise ProviderError('later_request_refused',status=status)
        return {'id':'master-accepted'},{}
    def publish(*args,**kwargs):
        provider_calendar._json('POST','https://www.googleapis.com/owned-fixture',{})
        provider_calendar._json('PATCH','https://www.googleapis.com/owned-fixture/instance',{})
    monkeypatch.setattr(delivery.remote_providers,'_json',transport);monkeypatch.setattr(provider_calendar,'calendar_publish',publish)
    monkeypatch.setattr(delivery.remote_providers,'refresh_tokens',lambda *args,**kwargs:refresh.append(1))
    assert tick(f)==0 and tick(f)==0 and wire==['POST','PATCH'] and not refresh
    with Session(f.engine) as session:
        job=session.exec(select(CalendarProviderJob)).one()
        assert job.status=='uncertain' and session.get(CalendarProviderPublication,job.publication_id).state=='uncertain'


def test_metadata_export_never_contains_feed_or_effect_authority_and_retention_orders_dependents(fixture):
    import json
    from app.models.data_control import DataControlJob
    from app.services.data_control import export_data,build_plan
    f=fixture;link=feed(f);body,reviewed=review(f);assert queue(f,body,reviewed).status_code==202
    with Session(f.engine) as session:
        job=DataControlJob(workspace_id='workspace',requested_by='owner',token_hash='export-fixture',auth_version=1,kind='export',encrypted_request='fixture',expires_at=datetime.utcnow()+timedelta(hours=1))
        exported,_,_=export_data(session,job,{'channel_ids':['room']})
        text=json.dumps(exported,default=str)
        assert len(exported['calendarproviderjob'])==1 and len(exported['calendarsubscription'])==1
        for forbidden in ['token_hash','session_hash','claims_json','encrypted_payload','encrypted_result','invitee@example.net',link['subscription_url']]:
            assert forbidden not in json.dumps({name:exported[name] for name in ['calendarproviderpublication','calendarproviderjob','calendarsubscription']},default=str)
        connection=session.get(RemoteSyncConnection,'google');connection.enabled=False;session.add(connection);session.commit()
        plan=build_plan(session,'workspace','owner',['room'],datetime.utcnow()+timedelta(days=1))
        assert len(plan['calendarproviderjob'])==1 and len(plan['calendarproviderpublication'])==1
