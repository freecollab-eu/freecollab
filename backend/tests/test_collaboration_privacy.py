"""Actual owner exports/erasure retain authority without leaving private copies."""
from datetime import datetime, timedelta
import io
import json
import zipfile
import pytest
from sqlmodel import Session, select

from app.models.ai_runtime import AIModelProfile, AIRun, AIRunSource, AIRunStep, AIUsage
from app.models.app_platform import AppForegroundReceipt, AppInstallation
from app.models.clips import ChannelClip
from app.models.content_completion import ChannelSemanticIndex, ContentCollectionItem, PersonalCalendarEvent
from app.models.encrypted_work import EncryptedWorkNotice, EncryptedWorkPointer
from app.models.media_capture import MeetingCapture, MeetingCaptureConsent, MeetingCaptureReceipt
from app.models.document_organization import SharedDocumentStack
from app.models.slack_livebridge import SlackBridge, SlackBridgeMessage
from app.models.message import Message
from app.services import collaboration_privacy as privacy, data_control as service
from app.services.account_security import seal_private
from tests.test_data_controls import controls, headers, post, preview, queue_purge, enable, PASSWORD, OLD


def event_record(identifier, actor, title, workspace='a'):
    value={'fields':{'title':title,'starts_at':'2026-10-25T10:00:00+00:00','ends_at':'2026-10-25T11:00:00+00:00'},'context_channel_id':None}
    return PersonalCalendarEvent(id=identifier,workspace_id=workspace,user_id=actor,
        encrypted_payload=seal_private(json.dumps(value),'personal-calendar:'+workspace+':'+identifier,actor))


def run_record(identifier='cross-run', actor='peer', channel='hidden', status='completed'):
    return AIRun(id=identifier,workspace_id='a',channel_id=channel,user_id=actor,request_id=identifier,
        request_digest='digest',profile_id='default',profile_revision=1,operation='summarize',status=status,
        encrypted_payload='SEALED PRIVATE COPIED CONTEXT',encrypted_authority='SEALED AUTHORITY',created_at=OLD,updated_at=OLD)


def usage(identifier, actor='peer', workspace='a', execution='cross-run', status='reported'):
    return AIUsage(id=identifier,workspace_id=workspace,user_id=actor,execution_id=execution,profile_id='default',profile_revision=1,
        reserved_tokens=80,reserved_cost_micros=800,status=status,charged_tokens=40,estimated_cost_micros=400)


def test_actual_export_adds_only_own_calendar_usage_and_opaque_progress(controls):
    with Session(controls[1]) as session:
        session.add_all([event_record('own','owner','OWNER PRIVATE CALENDAR'),event_record('peer','peer','PEER NEVER EXPORT'),
            usage('own-ledger','owner'),usage('peer-ledger','peer',execution='peer'),usage('other-ledger','owner','b','other'),
            EncryptedWorkPointer(channel_id='secure',matrix_event_id='$opaque:test',matrix_user_id='@owner:test',published_by='owner'),
            EncryptedWorkNotice(id='own-progress',channel_id='secure',user_id='owner',source='local'),
            EncryptedWorkNotice(id='peer-progress',channel_id='secure',user_id='peer',source='local')]);session.commit()
    queued=post(controls,'/exports',{'channel_ids':['native','secure'],'acknowledge_plaintext_export':True})
    assert queued.status_code==202,queued.text
    with Session(controls[1]) as session:assert service.run_pending(session)==1
    response=post(controls,'/exports/'+queued.json()['id']+'/download');assert response.status_code==200,response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:data=json.loads(archive.read('export.json'))
    assert [row['id'] for row in data['personal_calendar']]==['own']
    assert 'PEER NEVER EXPORT' not in json.dumps(data)
    assert len(data['private_ai_runtime']['usage'])==1
    assert [row['id'] for row in data['own_encrypted_work_progress']]==['own-progress']
    assert data['encrypted_work_pointers'][0]['matrix_event_id']=='$opaque:test'
    assert 'encrypted_payload' not in json.dumps(data['private_ai_runtime'])


def test_source_channel_purge_blocks_pending_cross_channel_ai_then_anonymizes_finished_copies(controls,monkeypatch):
    called=[];monkeypatch.setattr('app.services.semantic_opensearch.purge_channel',lambda session,channel:called.append(channel))
    with Session(controls[1]) as session:
        session.add(run_record(status='queued'));session.flush()
        session.add_all([AIRunSource(run_id='cross-run',index=0,channel_id='native',kind='message',source_id='old-root'),
            AIRunStep(run_id='cross-run',index=0),usage('ledger'),ChannelSemanticIndex(channel_id='native',encrypted_payload='SEALED CACHE')]);session.commit()
    enable(controls)
    blocked=post(controls,'/previews',{'channel_ids':['native']});assert blocked.status_code==409 and 'cross-channel' in blocked.text
    with Session(controls[1]) as session:
        run=session.get(AIRun,'cross-run');run.status='completed';session.add(run);session.commit()
    dry=preview(controls);assert dry['counts']['airun']==1 and dry['counts']['channelsemanticindex']==1
    queued=queue_purge(controls,dry);assert queued.status_code==202,queued.text
    with Session(controls[1]) as session:
        assert service.run_pending(session)==1
        assert session.get(AIRun,'cross-run') is None and session.get(ChannelSemanticIndex,'native') is None
        ledger=session.get(AIUsage,'ledger');assert ledger.user_id is None and ledger.execution_id=='erased:ledger' and ledger.charged_tokens==40
        assert session.get(Message,'hidden-message') is not None
    assert called==['native']


def test_active_capture_and_live_foreground_receipt_block_but_expired_receipt_is_uncertain(controls):
    with Session(controls[1]) as session:
        session.add(MeetingCapture(id='capture',transport='mesh',meeting_id='meeting',channel_id='native',created_by='owner',
            roster_digest='digest',recorder_participant_id='participant'));session.commit()
    blocked=post(controls,'/previews',{'channel_ids':['native']});assert blocked.status_code==409 and 'capture' in blocked.text
    with Session(controls[1]) as session:
        capture=session.get(MeetingCapture,'capture');capture.state='stopped';session.add(capture)
        session.add(AppInstallation(id='installed',workspace_id='a',app_id='customer.app',granted_by='owner',channel_ids_json='[]',
            manifest_json='{}',encrypted_config='',encrypted_secret='',origin='https://example.test',enabled=False));session.flush()
        session.add(AppForegroundReceipt(id='foreground',installation_id='installed',generation=1,channel_id='native',actor_id='owner',
            command='invoke',request_id='same-request',request_digest='HMAC'));session.commit()
    blocked=post(controls,'/previews',{'channel_ids':['native']});assert blocked.status_code==409 and 'foreground' in blocked.text
    with Session(controls[1]) as session:
        row=session.get(AppForegroundReceipt,'foreground');row.created_at=datetime.utcnow()-timedelta(seconds=91);session.add(row);session.commit()
    assert post(controls,'/previews',{'channel_ids':['native']}).status_code==200
    with Session(controls[1]) as session:assert session.get(AppForegroundReceipt,'foreground').status=='uncertain'


def test_clip_purge_keeps_consumed_receipt_tombstone_and_slack_message_authority(controls,monkeypatch):
    monkeypatch.setattr('app.services.semantic_opensearch.purge_channel',lambda *_:None)
    with Session(controls[1]) as session:
        session.add_all([ChannelClip(id='clip',channel_id='native',created_by='owner',created_at=OLD,content_mode='server',encrypted_payload='sealed',payload_hash='digest'),
            MeetingCapture(id='capture',transport='mesh',meeting_id='meeting',channel_id='native',created_by='owner',roster_digest='digest',recorder_participant_id='participant',state='stopped'),
            SlackBridge(id='bridge',workspace_id='a',channel_id='native',granted_by='owner',slack_team_id='T123',slack_channel_id='C123',
                slack_bot_user_id='U123',encrypted_config='',enabled=False)]);session.flush()
        session.add_all([MeetingCaptureReceipt(id='receipt',capture_id='capture',generation=1,channel_id='native',user_id='owner',expires_at=datetime.utcnow()+timedelta(minutes=5),clip_id='clip'),
            SlackBridgeMessage(bridge_id='bridge',slack_ts='1700000000.000001',message_id='old-root',origin='slack',latest_event_ts='1700000000.000001')]);session.commit()
    enable(controls);dry=preview(controls);queued=queue_purge(controls,dry);assert queued.status_code==202,queued.text
    with Session(controls[1]) as session:
        assert service.run_pending(session)==1
        assert session.get(ChannelClip,'clip') is None
        receipt=session.get(MeetingCaptureReceipt,'receipt');assert receipt.clip_id=='clip' and receipt.erased_at
        authority=session.get(SlackBridgeMessage,('bridge','1700000000.000001'));assert authority.message_id is None and authority.erased_at


def test_actual_account_closure_wipes_new_private_state_and_grants_without_refunding_ledger(controls,monkeypatch):
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member',lambda *_:None)
    with Session(controls[1]) as session:
        session.add_all([event_record('own','owner','OWNER PRIVATE'),event_record('peer','peer','KEEP PEER PRIVATE'),
            run_record('own-run','owner','native','running'),AIModelProfile(workspace_id='a',profile_id='shared',encrypted_config='sealed',updated_by='owner'),
            EncryptedWorkPointer(channel_id='secure',matrix_event_id='$historical',matrix_user_id='@owner:test',published_by='owner'),
            EncryptedWorkNotice(id='own-progress',channel_id='secure',user_id='owner',source='local'),
            SlackBridge(id='bridge',workspace_id='a',channel_id='native',granted_by='owner',slack_team_id='T123',slack_channel_id='C123',slack_bot_user_id='U123',encrypted_config='SEALED CUSTOMER KEY')]);session.flush()
        session.add(usage('reserved','owner',execution='own-run',status='reserved'));session.commit()
    client=controls[0];dry=client.get('/account/data-closure',headers=headers(controls)).json();assert dry['can_erase_identity']
    result=client.post('/account/data-closure',headers=headers(controls),json={'password':PASSWORD,'confirmation':'ERASE MY ACCOUNT',
        'preview_fingerprint':dry['fingerprint'],'acknowledge_retained_shared_authorship_and_backups':True})
    assert result.status_code==200,result.text
    with Session(controls[1]) as session:
        assert session.get(PersonalCalendarEvent,'own') is None and session.get(PersonalCalendarEvent,'peer')
        assert session.get(AIRun,'own-run') is None
        ledger=session.get(AIUsage,'reserved');assert ledger.user_id is None and ledger.status=='unknown' and ledger.charged_tokens==80
        assert session.get(AIModelProfile,('a','shared')).updated_by is None
        assert session.exec(select(EncryptedWorkPointer)).one().published_by is None
        assert session.get(EncryptedWorkNotice,'own-progress') is None
        bridge=session.get(SlackBridge,'bridge');assert not bridge.enabled and not bridge.encrypted_config and bridge.revoked_at


def test_collection_keeps_current_source_and_removes_purged_source_pointer(controls,monkeypatch):
    monkeypatch.setattr('app.services.semantic_opensearch.purge_channel',lambda *_:None)
    with Session(controls[1]) as session:
        session.add(SharedDocumentStack(id='stack',channel_id='native',created_by='owner',encrypted_payload='sealed',created_at=OLD,updated_at=OLD));session.flush()
        session.add_all([ContentCollectionItem(id='old-pointer',stack_id='stack',channel_id='native',kind='message',resource_id='old-root',created_by='owner',created_at=OLD),
            ContentCollectionItem(id='fresh-pointer',stack_id='stack',channel_id='native',kind='message',resource_id='fresh',created_by='owner')]);session.commit()
    enable(controls);dry=preview(controls);assert dry['counts']['contentcollectionitem']==1
    queued=queue_purge(controls,dry);assert queued.status_code==202,queued.text
    with Session(controls[1]) as session:
        assert service.run_pending(session)==1
        assert session.get(SharedDocumentStack,'stack') and session.get(ContentCollectionItem,'fresh-pointer')
        assert session.get(ContentCollectionItem,'old-pointer') is None


def test_configured_vector_erasure_failure_never_claims_native_purge_completed(controls,monkeypatch):
    from fastapi import HTTPException
    from app.models.data_control import DataControlJob
    def unavailable(*_):raise HTTPException(503,'Configured vector backend unavailable')
    monkeypatch.setattr('app.services.semantic_opensearch.purge_channel',unavailable)
    with Session(controls[1]) as session:
        session.add(ChannelSemanticIndex(channel_id='native',encrypted_payload='sealed'));session.commit()
    enable(controls);dry=preview(controls);queued=queue_purge(controls,dry)
    with Session(controls[1]) as session:
        assert service.run_pending(session)==0
        assert session.get(DataControlJob,queued.json()['id']).status=='failed'
        assert session.get(Message,'old-root') and session.get(ChannelSemanticIndex,'native')


def test_empty_personal_home_erases_own_calendar_policy_but_retains_anonymous_financial_receipt(controls):
    from app.models.ai_runtime import AIBudget
    from app.models.user import User
    from app.models.workspace import Workspace,WorkspaceMember,PersonalWorkspace
    from app.services.auth import create_token,hash_password
    with Session(controls[1]) as session:
        session.add(User(id='private-person',name='Private person',email='private-person@example.invalid',password_hash=hash_password(PASSWORD)))
        session.add(Workspace(id='private-home',name='Private home'));session.commit()
        session.add_all([PersonalWorkspace(user_id='private-person',workspace_id='private-home'),WorkspaceMember(workspace_id='private-home',user_id='private-person',role='owner')]);session.commit()
        session.add_all([event_record('home-event','private-person','Private calendar','private-home'),
            AIBudget(workspace_id='private-home',updated_by='private-person'),AIModelProfile(workspace_id='private-home',profile_id='profile',encrypted_config='sealed',updated_by='private-person'),
            usage('home-ledger','private-person','private-home','home-action','reserved')]);session.commit()
        token=create_token('private-person',session)
    actor={'Authorization':'Bearer '+token};client=controls[0]
    dry=client.get('/account/data-closure',headers=actor).json();assert dry['can_erase_identity'],dry
    result=client.post('/account/data-closure',headers=actor,json={'password':PASSWORD,'confirmation':'ERASE MY ACCOUNT',
        'preview_fingerprint':dry['fingerprint'],'acknowledge_retained_shared_authorship_and_backups':True})
    assert result.status_code==200,result.text
    with Session(controls[1]) as session:
        assert session.get(Workspace,'private-home') is None and session.get(PersonalCalendarEvent,'home-event') is None
        assert session.get(AIBudget,'private-home') is None
        ledger=session.get(AIUsage,'home-ledger');assert ledger.workspace_id is None and ledger.user_id is None and ledger.status=='unknown' and ledger.charged_tokens==80


def test_source_copy_and_identity_cleanup_preserve_actual_legal_holds(controls,monkeypatch):
    from app.models.enterprise_governance import GovernanceCase
    from app.models.user import User
    remote=[]
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member',lambda *args:remote.append(args))
    with Session(controls[1]) as session:
        session.add_all([run_record(),run_record('own-run','owner','native')]);session.flush()
        session.add(AIRunSource(run_id='cross-run',index=0,channel_id='native',kind='message',source_id='old-root'))
        session.add(GovernanceCase(id='hidden-hold',workspace_id='a',encrypted_title='sealed',channel_ids_json='["hidden"]',created_by='peer'));session.commit()
    enable(controls)
    blocked=post(controls,'/previews',{'channel_ids':['native']});assert blocked.status_code==423
    with Session(controls[1]) as session:
        hold=session.get(GovernanceCase,'hidden-hold');hold.channel_ids_json='["native"]';session.add(hold);session.commit()
    client=controls[0];dry=client.get('/account/data-closure',headers=headers(controls)).json()
    response=client.post('/account/data-closure',headers=headers(controls),json={'password':PASSWORD,'confirmation':'ERASE MY ACCOUNT',
        'preview_fingerprint':dry['fingerprint'],'acknowledge_retained_shared_authorship_and_backups':True})
    assert response.status_code==423,response.text
    assert remote==[]  # Preservation is checked before irreversible remote revocation.
    with Session(controls[1]) as session:
        assert session.get(AIRun,'own-run') and session.get(AIRun,'cross-run') and not session.get(User,'owner').is_blocked
