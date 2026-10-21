"""CRDT transport guards, immutable content, idempotency and encrypted storage."""
import base64
from types import SimpleNamespace
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from app.api import shared_documents
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.post import Post  # noqa: F401
from app.models.secure_channel import SecureChannel
from app.models.shared_documents import SharedDocument, SharedDocumentComment, SharedDocumentSnapshot, SharedDocumentUpdate
from app.models.user import User
from app.services import shared_documents as service
from app.services.auth import create_token, revoke_token
EMPTY='AAA='
CHANGE=base64.b64encode(b'private-native-update').decode()
def headers(user='member'):return {'Authorization':'Bearer '+create_token(user)}
@pytest.fixture
def documents():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([User(id=n,name=n,email=n+'@example.com',password_hash='unused') for n in ['owner','member','peer','outsider']]);session.add_all([Channel(id='room',name='Room'),Channel(id='other',name='Other')]);session.commit()
        session.add_all([ChannelMember(channel_id='room',user_id=n,role='admin' if n=='owner' else 'member') for n in ['owner','member','peer']]);session.add(ChannelMember(channel_id='other',user_id='outsider',role='admin'));session.commit()
    app=FastAPI();app.include_router(shared_documents.router)
    def sessions():
        with Session(engine) as session:yield session
    app.dependency_overrides[get_session]=sessions
    with TestClient(app) as client:yield SimpleNamespace(client=client,engine=engine)
    engine.dispose()
def create(d,identifier='doc',**extra):return d.client.post('/channels/room/shared-documents',json={'id':identifier,'update_id':'first-'+identifier,'update_base64':EMPTY,**extra},headers=headers())
def test_native_updates_are_append_only_idempotent_encrypted_and_scoped(documents):
    d=documents;path='/channels/room/shared-documents';assert create(d).status_code==201
    update={'update_id':'second','update_base64':CHANGE}
    for _ in range(2):
        result=d.client.post(path+'/doc/updates',json=update,headers=headers('peer'));assert result.status_code==200 and result.json()['sequence']==2
    assert d.client.post(path+'/doc/updates',json={**update,'update_base64':EMPTY},headers=headers('peer')).status_code==409
    assert d.client.post(path+'/doc/updates',json=update,headers=headers()).status_code==409
    page=d.client.get(path+'/doc/updates?limit=1',headers=headers()).json();assert page['has_more'] and page['next_sequence']==1
    tail=d.client.get(path+'/doc/updates?after_sequence=1',headers=headers()).json();assert tail['items'][0]['fields']['update_base64']==CHANGE
    with Session(d.engine) as session:
        rows=session.exec(select(SharedDocumentUpdate)).all();assert len(rows)==2 and all(CHANGE not in r.encrypted_payload for r in rows);assert session.get(SharedDocument,'doc').update_count==2
    for suffix in ['', '/doc/updates','/doc/comments','/doc/snapshots']:assert d.client.get(path+suffix,headers=headers('outsider')).status_code==403
    assert d.client.post('/channels/other/shared-documents',json={'id':'doc','update_id':'x','update_base64':EMPTY},headers=headers('outsider')).status_code==404
    assert d.client.get(path+'/doc/updates?limit=201',headers=headers()).status_code==422

def test_comments_authorship_immutable_text_and_resolution_cas(documents):
    d=documents;assert create(d).status_code==201;path='/channels/room/shared-documents/doc/comments';data={'id':'comment','text':'Private discussion'}
    result=d.client.post(path,json=data,headers=headers('peer'));assert result.status_code==201 and result.json()['created_by']=='peer'
    assert d.client.post(path,json=data,headers=headers('peer')).status_code==201
    assert d.client.post(path,json={**data,'text':'Spoof'},headers=headers()).status_code==409
    assert d.client.patch(path+'/comment',json={'expected_version':1,'resolved':True},headers=headers()).status_code==403
    result=d.client.patch(path+'/comment',json={'expected_version':1,'resolved':True},headers=headers('owner'));assert result.status_code==200 and result.json()['version']==2 and result.json()['fields']['text']=='Private discussion'
    assert d.client.patch(path+'/comment',json={'expected_version':1,'resolved':False},headers=headers('peer')).status_code==409
    assert d.client.patch(path+'/comment',json={'expected_version':2,'resolved':False},headers=headers('peer')).status_code==200
    with Session(d.engine) as session:assert 'Private discussion' not in session.get(SharedDocumentComment,'comment').encrypted_payload

def test_named_snapshots_are_immutable_and_require_current_sequence(documents):
    d=documents;assert create(d).status_code==201;path='/channels/room/shared-documents/doc/snapshots';data={'id':'snap','covers_sequence':1,'name':'Private milestone','update_base64':EMPTY}
    assert d.client.post(path,json={**data,'covers_sequence':2},headers=headers()).status_code==409
    result=d.client.post(path,json=data,headers=headers('peer'));assert result.status_code==201 and result.json()['fields']['name']=='Private milestone'
    assert d.client.post(path,json={**data,'name':'Overwrite'},headers=headers()).status_code==409
    with Session(d.engine) as session:assert 'Private milestone' not in session.get(SharedDocumentSnapshot,'snap').encrypted_payload
    assert len(d.client.get(path,headers=headers()).json())==1

def test_archive_author_admin_and_version_guards(documents):
    d=documents;assert create(d).status_code==201;path='/channels/room/shared-documents/doc'
    assert d.client.patch(path,json={'expected_version':1,'archived':True},headers=headers('peer')).status_code==403
    assert d.client.patch(path,json={'expected_version':1,'archived':True},headers=headers('owner')).status_code==200
    assert d.client.get('/channels/room/shared-documents',headers=headers()).json()==[]
    assert len(d.client.get('/channels/room/shared-documents?archived=true',headers=headers()).json())==1
    assert d.client.post(path+'/updates',json={'update_id':'blocked','update_base64':EMPTY},headers=headers()).status_code==409
    assert d.client.patch(path,json={'expected_version':1,'archived':False},headers=headers()).status_code==409
    assert d.client.patch(path,json={'expected_version':2,'archived':False},headers=headers()).status_code==200
    assert d.client.post(path+'/updates',json={'update_id':'restored','update_base64':EMPTY},headers=headers()).status_code==200

@pytest.mark.parametrize('status',['preparing','active'])
def test_secure_channel_only_accepts_validated_opaque_events(documents,monkeypatch,status):
    d=documents
    with Session(d.engine) as session:session.add(SecureChannel(channel_id='room',created_by='owner',status=status));session.commit()
    assert create(d).status_code==409;calls=[]
    def verify(session,channel,event,user):
        calls.append((channel,event,user))
        if event=='$wrong':raise HTTPException(403,'Wrong room or sender')
    monkeypatch.setattr(service,'validate_secure_event',verify)
    path='/channels/room/shared-documents';payload={'id':'secure','update_id':'first','matrix_event_id':'$owned'}
    assert d.client.post(path,json={**payload,'matrix_event_id':'$wrong'},headers=headers()).status_code==403
    assert d.client.post(path,json=payload,headers=headers()).status_code==201
    assert d.client.post(path+'/secure/updates',json={'update_id':'next','update_base64':EMPTY},headers=headers()).status_code==409
    assert d.client.post(path+'/secure/updates',json={'update_id':'next','matrix_event_id':'$update'},headers=headers('peer')).status_code==200
    assert d.client.post(path+'/secure/comments',json={'id':'c','text':'Never plaintext'},headers=headers()).status_code==409
    assert d.client.post(path+'/secure/comments',json={'id':'c','matrix_event_id':'$comment'},headers=headers()).status_code==201
    assert d.client.post(path+'/secure/snapshots',json={'id':'s','covers_sequence':2,'matrix_event_id':'$snapshot'},headers=headers()).status_code==201
    result=d.client.get(path+'/secure/updates',headers=headers()).json();assert all('fields' not in r and r['content_mode']=='matrix' for r in result['items'])
    with Session(d.engine) as session:
        assert all(r.encrypted_payload is None for r in session.exec(select(SharedDocumentUpdate)).all());assert session.get(SharedDocumentComment,'c').encrypted_payload is None;assert session.get(SharedDocumentSnapshot,'s').encrypted_payload is None
    assert len(calls)>=5

@pytest.mark.parametrize('bad',['','!!!!','AAAA\n','AA==',base64.b64encode(b'x'*(service.MAX_UPDATE_BYTES+1)).decode()])
def test_invalid_or_oversized_updates_rejected(documents,bad):assert create(documents,update_base64=bad).status_code==422

def test_account_rechecked_after_lock(documents,monkeypatch):
    d=documents;assert create(d).status_code==201;real=service.lock_channel_writes
    def changed(session,channel):
        real(session,channel);u=session.get(User,'member');u.is_blocked=True;session.add(u);session.commit()
    monkeypatch.setattr(service,'lock_channel_writes',changed)
    assert d.client.post('/channels/room/shared-documents/doc/updates',json={'update_id':'blocked','update_base64':EMPTY},headers=headers()).status_code==401
    with Session(d.engine) as session:assert session.get(SharedDocument,'doc').update_count==1

def test_removed_membership_and_revoked_session_denied(documents):
    d=documents;assert create(d).status_code==201;token=create_token('member')
    with Session(d.engine) as session:revoke_token(token,session)
    assert d.client.get('/channels/room/shared-documents',headers={'Authorization':'Bearer '+token}).status_code==401
    with Session(d.engine) as session:session.delete(session.get(ChannelMember,('room','member')));session.commit()
    assert d.client.get('/channels/room/shared-documents/doc/updates',headers=headers()).status_code==403
    assert d.client.post('/channels/room/shared-documents/doc/updates',json={'update_id':'late','update_base64':EMPTY},headers=headers()).status_code==403

def test_retained_limits_never_drop_old_content(documents,monkeypatch):
    d=documents;monkeypatch.setattr(service,'MAX_DOCUMENTS',1);assert create(d).status_code==201;assert create(d,identifier='second').status_code==409;monkeypatch.setattr(service,'MAX_UPDATES',1)
    assert d.client.post('/channels/room/shared-documents/doc/updates',json={'update_id':'limit','update_base64':EMPTY},headers=headers()).status_code==409
    assert len(d.client.get('/channels/room/shared-documents/doc/updates',headers=headers()).json()['items'])==1


@pytest.mark.parametrize('change',['blocked','revoked','removed'])
def test_secure_write_rechecks_authority_after_matrix_validation(documents,monkeypatch,change):
    d=documents
    with Session(d.engine) as session:session.add(SecureChannel(channel_id='room',created_by='owner',status='active'));session.commit()
    monkeypatch.setattr(service,'validate_secure_event',lambda *args:None)
    token=create_token('member');auth={'Authorization':'Bearer '+token};path='/channels/room/shared-documents'
    assert d.client.post(path,json={'id':'secure-race','update_id':'first','matrix_event_id':'$first'},headers=auth).status_code==201
    def changed(session,*_):
        if change=='blocked':
            user=session.get(User,'member');user.is_blocked=True;session.add(user);session.commit()
        elif change=='revoked':revoke_token(token,session)
        else:session.delete(session.get(ChannelMember,('room','member')));session.commit()
    monkeypatch.setattr(service,'validate_secure_event',changed)
    result=d.client.post(path+'/secure-race/updates',json={'update_id':'late','matrix_event_id':'$late'},headers=auth)
    assert result.status_code==(403 if change=='removed' else 401)
    with Session(d.engine) as session:
        assert session.get(SharedDocument,'secure-race').update_count==1
        assert session.get(SharedDocumentUpdate,('secure-race','late')) is None


def test_native_title_is_encrypted_and_ciphertext_cannot_move_between_documents(documents):
    d=documents;assert create(d,title='Private title').status_code==201;assert create(d,identifier='other-doc',title='Other private title').status_code==201
    path='/channels/room/shared-documents'
    listed=d.client.get(path,headers=headers()).json();assert {row['title'] for row in listed}=={'Private title','Other private title'}
    with Session(d.engine) as session:
        original=session.get(SharedDocument,'doc');other=session.get(SharedDocument,'other-doc')
        assert 'Private title' not in original.summary_encrypted_payload
        update=session.get(SharedDocumentUpdate,('doc','first-doc'));assert 'Private title' not in update.encrypted_payload
        other.summary_encrypted_payload=original.summary_encrypted_payload;session.add(other);session.commit()
    result=d.client.get(path,headers=headers());assert result.status_code==409 and 'Private title' not in result.text
