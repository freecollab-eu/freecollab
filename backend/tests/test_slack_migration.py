"""Resumable historical migration, current grants and encrypted provenance."""
from datetime import datetime, timedelta
import json
import pytest
from sqlmodel import Session, select
from app.models.channel import ChannelMember
from app.models.message import Message
from app.models.reaction import Reaction
from app.models.slack_import import SlackImport, SlackImportDetail
from app.services import slack_import as service
from app.services.crypto import decrypt_message
from tests.test_slack_import import importer, archive, preview, confirm, BASE
from tests.test_webhooks import team


def choices(**changes):
    return dict({'channel_ids':['C1'],'user_mapping':{'U1':'owner','U2':'member'},'channel_prefix':'history-','acknowledge_native_archive':True},**changes)
def begin(f,job,**changes):return f[0].post(BASE+'/'+job['id']+'/begin',headers=f[2]['owner'],json=choices(**changes))
def step(f,job,n):return f[0].post(BASE+'/'+job['id']+'/step',headers=f[2]['owner'],json={'expected_sequence':n})
def large_archive(file=False):
    def change(data):
        data['general/2024-01-01.json']=[{'type':'message','user':'U1','ts':str(1704067200+i)+'.000001','text':'Historical message '+str(i),**({'thread_ts':'1704067200.000001'} if i==250 else {})} for i in range(251)]
        if file:data['general/2024-01-01.json'][-1]['files']=[{'id':'F1','name':'final.txt','mimetype':'text/plain'}]
    return archive(changes=change)


def test_reactions_edits_and_current_text_are_encrypted_historical_facts(importer):
    def change(data):data['general/2024-01-01.json'][0].update(edited={'ts':'1704067300.000001'},edit_history=[{'ts':'1704067200.000001','user':'U1','text':'Earlier private text'}],reactions=[{'name':'thumbsup','count':2,'users':['U1','U2']}])
    result=confirm(importer,preview(importer,archive(changes=change))).json();cid=result['channels'][0]['id']
    with Session(importer[1]) as s:
        message=s.exec(select(Message).where(Message.channel_id==cid).order_by(Message.created_at)).first();mid=message.id
        detail=s.get(SlackImportDetail,mid);assert 'Earlier private text' not in detail.encrypted_details
        assert not s.exec(select(Reaction)).all()
    route='/channels/'+cid+'/messages/'+mid+'/slack-history';data=importer[0].get(route,headers=importer[2]['member']).json()
    assert data['history'][0]['text']=='Earlier private text' and data['current_text']=='Private original Slack content'
    assert data['reactions'][0]['users'][1]['name']=='Bob' and data['author']['name']=='Alice'
    assert importer[0].get(route,headers=importer[2]['outsider']).status_code==403
    with Session(importer[1]) as s:s.delete(s.get(ChannelMember,(cid,'member')));s.commit()
    assert importer[0].get(route,headers=importer[2]['member']).status_code==403


def test_message_changed_envelope_retains_original_snapshot_once(importer):
    def change(data):
        old=data['general/2024-01-01.json'][0].copy()
        data['general/2024-01-01.json'].append({'type':'message','subtype':'message_changed','message':{**old,'text':'Newest source body','edited':{'ts':'1704067400.000001'}},'previous_message':old})
    job=preview(importer,archive(changes=change));assert job['channels'][0]['message_count']==3
    cid=confirm(importer,job).json()['channels'][0]['id']
    with Session(importer[1]) as s:
        message=s.exec(select(Message).where(Message.channel_id==cid).order_by(Message.created_at)).first()
        data=json.loads(decrypt_message(s.get(SlackImportDetail,message.id).encrypted_details,cid))
        assert data['current_text']=='Newest source body' and len(data['history'])==1 and data['history'][0]['text']=='Private original Slack content'


@pytest.mark.parametrize('update',[{'reactions':[{'name':'thumbsup','count':1,'users':['U1','U1']}]},{'reactions':[{'name':'thumbsup','count':0,'users':['U1']}]},{'reactions':[{'name':'thumbsup','count':True,'users':[]}]},{'edit_history':[{'ts':'invalid','text':'bad'}]},{'edit_history':[{'ts':'1704067200.000001','text':'a'}]*26}])
def test_invalid_historical_records_rejected_before_writes(importer,update):
    raw=archive(changes=lambda data:data['general/2024-01-01.json'][0].update(update))
    assert importer[0].post(BASE+'/preview',headers=importer[2]['owner'],files={'file':('source.zip',raw)}).status_code==422


def test_batch_lost_ack_exactly_once_and_cross_batch_threads(importer):
    job=preview(importer,large_archive());opened=begin(importer,job);assert opened.status_code==200 and opened.json()['total']==251
    first=step(importer,job,0);assert first.status_code==200,first.text
    assert first.json()['processed']==250 and first.json()['sequence']==1 and step(importer,job,0).json()==first.json()
    changed=choices();changed['channel_prefix']='different-'
    assert importer[0].post(BASE+'/'+job['id']+'/begin',headers=importer[2]['owner'],json=changed).status_code==409
    final=step(importer,job,1);assert final.status_code==200,final.text
    assert final.json()['status']=='completed' and final.json()['result']['messages']==251 and step(importer,job,1).json()==final.json()
    cid=final.json()['result']['channels'][0]['id']
    with Session(importer[1]) as s:
        rows=s.exec(select(Message).where(Message.channel_id==cid).order_by(Message.created_at)).all()
        assert len(rows)==251 and rows[-1].parent_id==rows[0].id and rows[0].thread_count==1
        assert s.get(SlackImport,job['id']).encrypted_archive==''


def test_expired_partial_zip_reselection_preserves_destination_and_cursor(importer):
    raw=large_archive();job=preview(importer,raw);begin(importer,job);first=step(importer,job,0).json();cid=first['result']['channels'][0]['id']
    with Session(importer[1]) as s:
        row=s.get(SlackImport,job['id']);row.expires_at=datetime.utcnow()-timedelta(seconds=1);s.add(row);s.commit();assert service.expire_previews(s)==1
        row=s.get(SlackImport,job['id']);assert row.status=='paused' and row.encrypted_archive=='' and row.result_json
    assert step(importer,job,1).status_code==409
    assert preview(importer,raw)['progress']['processed']==250
    final=step(importer,job,1).json()['result'];assert final['channels'][0]['id']==cid and final['messages']==251


def test_changed_destination_roster_pauses_next_batch(importer):
    job=preview(importer,large_archive());begin(importer,job);first=step(importer,job,0).json();cid=first['result']['channels'][0]['id']
    with Session(importer[1]) as s:s.delete(s.get(ChannelMember,(cid,'member')));s.commit()
    assert step(importer,job,1).status_code==409
    with Session(importer[1]) as s:assert len(s.exec(select(Message).where(Message.channel_id==cid)).all())==250


def test_later_batch_storage_failure_preserves_prior_commit_and_retries_cleanly(importer,monkeypatch):
    job=preview(importer,large_archive(file=True));begin(importer,job);first=step(importer,job,0).json();cid=first['result']['channels'][0]['id']
    original=service.storage_service.save_file_sync
    def fail(*_):raise RuntimeError('private fixture storage failure')
    monkeypatch.setattr(service.storage_service,'save_file_sync',fail)
    with pytest.raises(RuntimeError,match='fixture storage'):step(importer,job,1)
    with Session(importer[1]) as s:
        assert len(s.exec(select(Message).where(Message.channel_id==cid)).all())==250
        assert service.resume_status(s.get(SlackImport,job['id']))['sequence']==1
    monkeypatch.setattr(service.storage_service,'save_file_sync',original);assert step(importer,job,1).json()['result']['messages']==251


def test_secure_reservation_current_roster_and_opaque_stable_destination(importer,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'matrix_enabled',True)
    data={'archive_digest':'a'*64,'options_digest':'b'*64,'source_digest':'c'*64,'member_ids':['member']};route=BASE+'/secure-reserve'
    first=importer[0].post(route,headers=importer[2]['owner'],json=data);assert first.status_code==200,first.text
    assert importer[0].post(route,headers=importer[2]['owner'],json=data).json()==first.json()
    assert importer[0].post(route,headers=importer[2]['owner'],json={**data,'options_digest':'d'*64}).status_code==409
    with Session(importer[1]) as s:
        row=s.get(SlackImport,first.json()['id']);assert row.encrypted_archive==''
        s.delete(s.get(ChannelMember,(first.json()['channel_id'],'member')));s.commit()
    assert importer[0].post(route,headers=importer[2]['owner'],json=data).status_code==409


def test_secure_completion_is_bound_to_original_archive_and_choices_and_frees_pending_status(importer,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'matrix_enabled',True)
    data={'archive_digest':'e'*64,'options_digest':'f'*64,'source_digest':'a'*64,'member_ids':[]}
    reserved=importer[0].post(BASE+'/secure-reserve',headers=importer[2]['owner'],json=data).json()
    endpoint=BASE+'/'+reserved['id']+'/complete-secure'
    assert importer[0].post(endpoint,headers=importer[2]['owner'],json={'archive_digest':'b'*64,'options_digest':'f'*64}).status_code==409
    fields={'archive_digest':'e'*64,'options_digest':'f'*64}
    assert importer[0].post(endpoint,headers=importer[2]['owner'],json=fields).json()=={'completed':True}
    assert importer[0].post(endpoint,headers=importer[2]['owner'],json=fields).json()=={'completed':True}
    with Session(importer[1]) as s:assert s.get(SlackImport,reserved['id']).status=='secure_completed'
    assert importer[0].post(BASE+'/secure-reserve',headers=importer[2]['owner'],json=data).status_code==409


def test_real_preview_accepts_more_than_ten_thousand_messages(importer):
    import hashlib
    def change(data):
        data['general/2024-01-01.json']=[{'type':'message','user':'U1','ts':str(1704067200+i)+'.000001','text':hashlib.sha256(str(i).encode()).hexdigest()} for i in range(10001)]
    result=preview(importer,archive(changes=change));assert result['channels'][0]['message_count']==10001


def test_actual_native_import_supports_more_than_twenty_five_selected_channels(importer):
    def change(data):data['channels.json']=[{'id':'C'+str(i),'name':'room'+str(i),'members':[]} for i in range(26)]
    job=preview(importer,archive(changes=change))
    result=confirm(importer,job,channel_ids=['C'+str(i) for i in range(26)],user_mapping={})
    assert result.status_code==200 and len(result.json()['channels'])==26


def test_actual_directory_scan_rejects_excess_records_before_zipinfo_allocation(monkeypatch):
    import io,zipfile,struct
    from fastapi import HTTPException
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as writer:
        for i in range(6):writer.writestr('entry'+str(i),b'')
    raw=bytearray(output.getvalue());struct.pack_into('<H',raw,len(raw)-22+8,1);struct.pack_into('<H',raw,len(raw)-22+10,1)
    monkeypatch.setattr(service,'MAX_ENTRIES',5)
    calls=[]
    monkeypatch.setattr(service.zipfile,'ZipFile',lambda *_:calls.append('allocated'))
    with pytest.raises(HTTPException):service.Archive(bytes(raw))
    assert calls==[]


def test_zip_comment_cannot_redirect_zipfile_to_an_unchecked_directory(importer):
    import struct
    raw=archive()
    # Ordinary comments are accepted and stripped for the decoder's EOCD scan.
    value=bytearray(raw);struct.pack_into('<H',value,len(value)-2,23);value.extend(b'comment PK\x05\x06 nonsense!  ')
    # Exact length is deliberately enforced; a malformed declaration is rejected.
    response=importer[0].post(BASE+'/preview',headers=importer[2]['owner'],files={'file':('bad.zip',bytes(value))})
    assert response.status_code==422
