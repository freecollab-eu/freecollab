"""Passive preview adversaries and actual conditional single-resource WebDAV."""
from datetime import datetime, timedelta
import io
import json
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import files, file_experience
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.post import Post  # noqa: F401
from app.models.user import User
from app.models.secure_channel import SecureChannel
from app.models.account_security import AccountSecurity
from app.models.file import File as StoredFile
from app.models.file_version import FileDocument
from app.models.remote_sync import RemoteSyncFileState
from app.models.file_experience import FileComment, OfficeEditLock, OfficeEditSession
from app.services import file_experience as service, storage
from app.services.auth import create_token, hash_password, revoke_token

MIME='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
PASSWORD='Private-office-proof!2026'
HASH=hash_password(PASSWORD)
LOCK=b'<d:lockinfo xmlns:d="DAV:"><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype></d:lockinfo>'


def package(parts):
    stream=io.BytesIO()
    with ZipFile(stream,'w',ZIP_DEFLATED) as archive:
        for name,value in parts.items(): archive.writestr(ZipInfo(name,date_time=(2026,1,1,0,0,0)),value,compress_type=ZIP_DEFLATED)
    return stream.getvalue()


def docx(text='Original private text',extra=None):
    return package({'[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>',**(extra or {})})


@pytest.fixture
def api(tmp_path,monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool); SQLModel.metadata.create_all(engine)
    store=storage.StorageService();store.storage_path=tmp_path
    monkeypatch.setattr(storage,'storage_service',store);monkeypatch.setattr(files,'storage_service',store);monkeypatch.setattr(file_experience,'storage_service',store)
    with Session(engine) as session:
        session.add_all([User(id=who,name=who,email=who+'@example.com',password_hash=HASH) for who in ['owner','member','outsider']]);session.add_all([Channel(id='room',name='Room'),Channel(id='other',name='Other')]);session.commit()
        session.add_all([ChannelMember(channel_id='room',user_id='owner',role='admin'),ChannelMember(channel_id='room',user_id='member'),ChannelMember(channel_id='other',user_id='outsider',role='admin')]);session.commit()
    app=FastAPI();app.include_router(files.router);app.include_router(file_experience.router)
    def sessions():
        with Session(engine) as session:yield session
    app.dependency_overrides[get_session]=sessions
    token=create_token('owner');headers={'Authorization':'Bearer '+token}
    with TestClient(app) as client:
        record=client.post('/files/channels/room',files={'file':('brief.docx',docx(),MIME)},headers=headers).json()
        yield SimpleNamespace(client=client,engine=engine,store=store,record=record,headers=headers,token=token)
    engine.dispose()


def path(api):return '/channels/room/file-experience/'+api.record['id']
def opened(api):
    result=api.client.post(path(api)+'/office-sessions',json={'password':PASSWORD},headers=api.headers);assert result.status_code==201,result.text
    return result.json()


def test_passive_ooxml_preview_extracts_text_without_fetching_external_relationships(api):
    result=api.client.get(path(api)+'/preview',headers=api.headers);assert result.status_code==200 and result.json()['blocks']==['Original private text'];assert result.headers['cache-control']=='private, no-store'
    data=docx('safe',{'word/_rels/document.xml.rels':'<Relationships><Relationship Target="https://never-fetch.invalid/secret" TargetMode="External"/></Relationships>'})
    assert service.office_preview(data,MIME)['blocks']==['safe']


@pytest.mark.parametrize('extra',[{'word/vbaProject.bin':b'macro'}, {'../evil.xml':'escape'}, {'huge.xml':'a'*(service.MAX_XML+1)}, {'word/document.xml':'<!DOCTYPE a [<!ENTITY x "expanded">]><a>&x;</a>'}, {'word/document.xml':'<a/>'.encode('utf-16')}])
def test_office_preview_rejects_active_escaping_bomb_and_dtd_archives(extra):
    with pytest.raises(Exception) as failure:service.office_preview(docx(extra=extra),MIME)
    assert getattr(failure.value,'status_code',None)==422


def test_bounded_xlsx_and_pptx_previews_never_evaluate_formulas():
    xlsx='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    data=package({'[Content_Types].xml':'<Types/>','xl/sharedStrings.xml':'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Caption</t></si></sst>',
        'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="s"><v>0</v></c><c><f>WEBSERVICE("https://never.invalid")</f><v>42</v></c></row></sheetData></worksheet>'})
    assert service.office_preview(data,xlsx)['blocks']==['sheet1.xml','Caption\t42']
    pptx='application/vnd.openxmlformats-officedocument.presentationml.presentation'
    slides=package({'[Content_Types].xml':'<Types/>','ppt/slides/slide1.xml':'<p:sld xmlns:p="urn:p" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Safe title</a:t></p:sld>'})
    assert service.office_preview(slides,pptx)['blocks']==['Slide 1','Safe title']


def test_comments_are_authenticated_encrypted_idempotent_and_document_scoped(api):
    data={'id':'note','text':'Private file review'};endpoint=path(api)+'/comments'
    assert api.client.post(endpoint,json=data,headers=api.headers).status_code==201
    assert api.client.post(endpoint,json=data,headers=api.headers).status_code==201
    assert api.client.post(endpoint,json={**data,'text':'Changed'},headers=api.headers).status_code==409
    peer={'Authorization':'Bearer '+create_token('member')};result=api.client.get(endpoint,headers=peer).json();assert result[0]['created_by']=='owner' and result[0]['text']==data['text']
    assert api.client.post(endpoint,json=data,headers=peer).status_code==409
    with Session(api.engine) as session:assert data['text'] not in session.get(FileComment,'note').encrypted_text
    outsider={'Authorization':'Bearer '+create_token('outsider')};assert api.client.get(endpoint,headers=outsider).status_code==403


def test_webdav_real_open_lock_conditional_saveback_retains_exact_versions(api):
    grant=opened(api);url=grant['path'];assert api.token not in url and grant['handler']=='ms-word'
    assert api.client.options(url).headers['dav']=='1, 2'
    assert api.client.request('PROPFIND',url,headers={'Depth':'0'}).status_code==207
    assert api.client.request('PROPFIND',url,headers={'Depth':'1'}).status_code==403
    original=api.client.get(url);assert original.content==docx();etag=original.headers['etag'];assert api.client.head(url).headers['etag']==etag
    assert api.client.put(url,content=docx('Updated')).status_code==428
    locked=api.client.request('LOCK',url,content=LOCK,headers={'Depth':'0'});assert locked.status_code==200
    lock=locked.headers['lock-token'];changed=docx('Saved back by desktop Office')
    assert api.client.put(url,content=changed,headers={'If-Match':etag}).status_code==423
    saved=api.client.put(url,content=changed,headers={'If-Match':etag,'If':'('+lock+')','Content-Type':MIME});assert saved.status_code==204 and saved.headers['etag']!=etag and saved.headers['x-freecollab-version']=='2'
    assert api.client.put(url,content=changed,headers={'If-Match':etag,'If':'('+lock+')'}).status_code==412
    assert api.client.get(url).content==changed
    history=api.client.get('/files/'+api.record['id']+'/versions',headers=api.headers).json();assert len(history)==2
    assert api.client.get('/files/'+history[1]['id'],headers=api.headers).content==docx()
    assert api.client.request('UNLOCK',url,headers={'Lock-Token':lock}).status_code==204
    with Session(api.engine) as session:
        row=session.get(OfficeEditSession,grant['id']);assert row.secret_hash not in url and api.token not in row.claims_json;assert row.native_session_hash!=api.token


def test_office_lock_conflict_and_manual_version_conflict(api):
    first,second=opened(api),opened(api);one=api.client.request('LOCK',first['path'],content=LOCK);assert one.status_code==200
    assert api.client.request('LOCK',second['path'],content=LOCK).status_code==423
    assert api.client.put(second['path'],content=docx('No takeover'),headers={'If-Match':'"'+api.record['id']+'"'}).status_code==423
    manual=api.client.post('/files/'+api.record['id']+'/versions',files={'file':('brief.docx',docx('Manual latest'),MIME)},headers=api.headers);assert manual.status_code==200
    assert api.client.head(first['path']).status_code==200  # HEAD must not advance a desktop's downloaded baseline.
    stale=api.client.put(first['path'],content=docx('Stale Office'),headers={'If':'('+one.headers['lock-token']+')'});assert stale.status_code==412


@pytest.mark.parametrize('change',['expired','revoked','logout','blocked','membership','secure','preparing'])
def test_office_capability_fails_closed_on_current_authority_changes(api,change):
    grant=opened(api)
    with Session(api.engine) as session:
        row=session.get(OfficeEditSession,grant['id'])
        if change=='expired':row.expires_at=datetime.utcnow()-timedelta(seconds=1);session.add(row)
        elif change=='revoked':row.revoked_at=datetime.utcnow();session.add(row)
        elif change=='logout':revoke_token(api.token,session)
        elif change=='blocked':user=session.get(User,'owner');user.is_blocked=True;session.add(user)
        elif change=='membership':session.delete(session.get(ChannelMember,('room','owner')))
        else:session.add(SecureChannel(channel_id='room',created_by='owner',status='preparing' if change=='preparing' else 'active'))
        session.commit()
    assert api.client.get(grant['path']).status_code in {401,403,404,409}
    assert api.client.put(grant['path'],content=docx('Denied'),headers={'If-Match':'"'+api.record['id']+'"'}).status_code in {401,403,404,409}


def test_office_reauth_owner_path_and_macro_guards(api):
    assert api.client.post(path(api)+'/office-sessions',json={'password':'wrong'},headers=api.headers).status_code==403
    assert api.client.post(path(api)+'/office-sessions',json={'password':PASSWORD},headers={'Authorization':'Bearer '+create_token('member')}).status_code==403
    grant=opened(api);assert api.client.get(grant['path'].replace('brief.docx','foreign.docx')).status_code==404
    assert api.client.put(grant['path'],content=docx(extra={'word/vbaProject.bin':b'active'}),headers={'If-Match':'"'+api.record['id']+'"'}).status_code==422
    assert api.client.request('DELETE',grant['path']).status_code==405
    assert api.client.delete(path(api)+'/office-sessions/'+grant['id'],headers=api.headers).status_code==200
    assert api.client.get(grant['path']).status_code==404


@pytest.mark.parametrize('stage',['body','storage'])
@pytest.mark.parametrize('change',['revoked','blocked','auth_version'])
@pytest.mark.parametrize('version',[False,True])
def test_native_upload_authority_changes_during_await_never_publish_bytes_or_metadata(api,monkeypatch,stage,change,version):
    """Simulate authority changing at each awaited I/O boundary, including logout.

    The isolated shared-connection fixture commits the competing state change;
    fresh post-I/O guards must observe it rather than a cached get_current_user.
    """
    def invalidate():
        with Session(api.engine) as session:
            if change=='revoked':revoke_token(api.token,session)
            elif change=='blocked':
                actor=session.get(User,'owner');actor.is_blocked=True;session.add(actor);session.commit()
            else:
                state=session.get(AccountSecurity,'owner');state.auth_version+=1;session.add(state);session.commit()
    if stage=='body':
        real=files.validated_content
        async def read_then_changed(file):
            value=await real(file);invalidate();return value
        monkeypatch.setattr(files,'validated_content',read_then_changed)
    else:
        real=api.store.save_file
        async def stored_then_changed(identifier,content):
            value=await real(identifier,content);invalidate();return value
        monkeypatch.setattr(api.store,'save_file',stored_then_changed)
    endpoint='/files/'+api.record['id']+'/versions' if version else '/files/channels/room'
    result=api.client.post(endpoint,files={'file':('race.txt',b'Never publish these new bytes','text/plain')},headers=api.headers)
    assert result.status_code==401
    with Session(api.engine) as session:
        assert len(session.exec(select(StoredFile)).all())==1
        assert session.get(FileDocument,api.record['document_id']).version_count==1
    assert {item.name for item in api.store.storage_path.iterdir()}=={api.record['id']}


def test_synced_file_tombstone_permanently_revokes_existing_office_capability_and_lock(api):
    grant=opened(api)
    assert api.client.request('LOCK',grant['path'],content=LOCK).status_code==200
    with Session(api.engine) as session:
        session.add(RemoteSyncFileState(document_id=api.record['document_id']));session.commit()
    removed=api.client.delete('/files/'+api.record['id'],headers=api.headers)
    assert removed.status_code==200 and removed.json()['retained_versions'] is True
    with Session(api.engine) as session:
        row=session.get(OfficeEditSession,grant['id'])
        assert row is None or row.revoked_at is not None
        assert session.get(OfficeEditLock,api.record['document_id']) is None
        assert session.get(FileDocument,api.record['document_id']) is not None
        from app.services.remote_sync import restore_synced_file
        restore_synced_file(session,api.record['document_id']);session.commit()
    assert api.client.get(grant['path']).status_code==404
