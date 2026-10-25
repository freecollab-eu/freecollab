"""Real meeting admission, consent generations, revocation and atomic receipts."""
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import pytest
import importlib
import pkgutil
import app.models
for _model in pkgutil.iter_modules(app.models.__path__): importlib.import_module("app.models."+_model.name)
from fastapi import HTTPException
from sqlmodel import Session, select
from app.api import media_capture, clips, secure_messaging  # register clip tables before isolated fixture schema
from app.models.media_capture import MeetingCapture, MeetingCaptureConsent, MeetingCaptureReceipt
from app.models.channel import ChannelMember
from app.services import media_capture as capture_service
from app.services import workflow as _workflow, webhooks as _webhooks  # register real upload-event tables before fixture creation
_spec = importlib.util.spec_from_file_location("media_capture_meeting_fixture", Path(__file__).with_name("test_meetings.py"))
_meetings = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_meetings)
team, native, start, join = _meetings.team, _meetings.native, _meetings.start, _meetings.join


def setup(team):
    client, engine, tokens, _ = team
    client.app.include_router(media_capture.router)
    meeting = start(team); member = join(team, meeting)
    identifier = meeting["meeting"]["id"]
    return client, engine, tokens, meeting, member, "/meetings/mesh/" + identifier + "/capture"


def request(client, tokens, path):
    response = client.post(path, headers=native(tokens["owner"]), json={"video": False, "captions": False, "language": "auto"})
    assert response.status_code == 201, response.text
    return response.json()["capture"]


def vote(client, tokens, path, capture, granted=True):
    return client.post(path + "/consent", headers=native(tokens["member"]), json={
        "capture_id": capture["id"], "generation": capture["generation"], "granted": granted})


def control(client, tokens, path, capture, action):
    return client.post(path + "/control", headers=native(tokens["owner"]), json={
        "capture_id": capture["id"], "generation": capture["generation"], "action": action})


def test_all_current_participants_consent_before_real_capture_and_receipt(team):
    client, engine, tokens, _, _, path = setup(team)
    capture = request(client, tokens, path)
    assert not capture["all_consented"] and len(capture["awaiting"]) == 1
    assert control(client, tokens, path, capture, "start").status_code == 409
    assert vote(client, tokens, path, capture).status_code == 200
    assert control(client, tokens, path, capture, "start").json()["capture"]["state"] == "recording"
    receipt = control(client, tokens, path, capture, "segment").json()["publication_receipt"]
    with Session(engine) as session:
        capture_service.authorize_publication(session, receipt, "shared", "owner", "clip-one"); session.commit()
        capture_service.authorize_publication(session, receipt, "shared", "owner", "clip-one")
        with pytest.raises(HTTPException): capture_service.authorize_publication(session, receipt, "shared", "owner", "another-clip")
        with pytest.raises(HTTPException): capture_service.authorize_publication(session, receipt, "elsewhere", "owner", "clip-one")
        assert session.get(MeetingCaptureReceipt, receipt).clip_id == "clip-one"


def test_consent_withdrawal_invalidates_old_segment_and_late_control(team):
    client, engine, tokens, _, _, path = setup(team)
    capture = request(client, tokens, path); vote(client, tokens, path, capture)
    control(client, tokens, path, capture, "start")
    receipt = control(client, tokens, path, capture, "segment").json()["publication_receipt"]
    result = vote(client, tokens, path, capture, False).json()["capture"]
    assert result["state"] == "paused" and result["generation"] > capture["generation"]
    assert control(client, tokens, path, capture, "resume").status_code == 409
    with Session(engine) as session:
        with pytest.raises(HTTPException): capture_service.authorize_publication(session, receipt, "shared", "owner", "clip")


def test_a_new_device_pauses_capture_and_requires_fresh_generation_for_everyone(team):
    client, _, tokens, meeting, _, path = setup(team)
    capture = request(client, tokens, path); vote(client, tokens, path, capture); control(client, tokens, path, capture, "start")
    join(team, meeting, "other")
    result = client.get(path, headers=native(tokens["owner"])).json()["capture"]
    assert result["state"] == "paused" and result["generation"] > capture["generation"]
    assert len(result["awaiting"]) == 3 and not result["consented"]
    assert control(client, tokens, path, capture, "segment").status_code == 409


def test_frozen_stopped_receipt_is_private_and_survives_call_ending_but_not_permission_removal(team):
    client, engine, tokens, meeting, _, path = setup(team)
    capture = request(client, tokens, path); vote(client, tokens, path, capture); control(client, tokens, path, capture, "start")
    result = control(client, tokens, path, capture, "stop").json(); receipt = result["publication_receipt"]
    assert result["capture"]["state"] == "stopped"
    assert client.get(path, headers=native(tokens["owner"])).json()["capture"] is None
    assert client.post("/meetings/" + meeting["meeting"]["id"] + "/end", headers=native(tokens["owner"]), json={}).status_code == 200
    with Session(engine) as session:
        capture_service.authorize_publication(session, receipt, "shared", "owner", "clip")
        with pytest.raises(HTTPException): capture_service.authorize_publication(session, receipt, "shared", "member", "clip")
        row = session.get(MeetingCaptureReceipt, receipt); row.expires_at = datetime.utcnow() - timedelta(seconds=1); session.add(row); session.commit()
        with pytest.raises(HTTPException): capture_service.authorize_publication(session, receipt, "shared", "owner", "clip")


def test_only_host_can_request_and_current_meeting_member_can_vote(team):
    client, _, tokens, _, _, path = setup(team)
    assert client.post(path, headers=native(tokens["member"]), json={}).status_code == 403
    assert client.get(path, headers=native(tokens["outsider"])).status_code == 403
    capture = request(client, tokens, path)
    assert client.post(path, headers=native(tokens["owner"]), json={}).status_code == 409
    assert client.post(path + "/control", headers=native(tokens["member"]), json={"capture_id": capture["id"],"generation":1,"action":"start"}).status_code == 403
    with Session(team[1]) as session:
        row = session.exec(select(ChannelMember).where(ChannelMember.channel_id == "shared", ChannelMember.user_id == "member")).one()
        session.delete(row); session.commit()
    assert vote(client, tokens, path, capture).status_code in (401,403)


def test_caption_configuration_never_uses_a_remote_model_or_fabricates_signing(monkeypatch):
    monkeypatch.setenv("MEETING_CAPTION_MODEL","whisper-tiny")
    monkeypatch.setenv("MEETING_CAPTION_MODEL_BASE","/models/")
    config=capture_service.configuration()
    assert config["captions"] and config["audio_destination"]=="this device only"
    monkeypatch.setenv("MEETING_CAPTION_MODEL_BASE","https://remote.example/")
    assert not capture_service.configuration()["captions"]


def test_stopped_publication_reloads_generation_under_the_capture_lock(team, monkeypatch):
    client, engine, tokens, _, _, path = setup(team)
    capture = request(client, tokens, path); vote(client, tokens, path, capture); control(client, tokens, path, capture, "start")
    receipt = control(client, tokens, path, capture, "stop").json()["publication_receipt"]
    original = capture_service.lock_capture; observed = []
    def concurrent_withdrawal(session, row):
        observed.append(row.state)
        from sqlalchemy import update
        session.execute(update(MeetingCapture).where(MeetingCapture.id == row.id).values(generation=row.generation + 1))
        return original(session, row)
    monkeypatch.setattr(capture_service, "lock_capture", concurrent_withdrawal)
    with Session(engine) as session:
        with pytest.raises(HTTPException): capture_service.authorize_publication(session, receipt, "shared", "owner", "clip")
    assert observed == ["stopped"]


def test_native_file_publication_rechecks_after_private_storage_and_removes_rejected_blob(team, monkeypatch, tmp_path):
    import asyncio
    from io import BytesIO
    from starlette.datastructures import UploadFile, Headers
    from app.api import files
    from app.models.file import File
    from app.models.user import User
    from app.services.storage import StorageService
    from app.config import settings
    client, engine, tokens, _, _, path = setup(team)
    capture = request(client, tokens, path); vote(client, tokens, path, capture); control(client, tokens, path, capture, "start")
    receipt = control(client, tokens, path, capture, "segment").json()["publication_receipt"]
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "recordings"))
    store = StorageService(); monkeypatch.setattr(files, "storage_service", store)
    original = store.save_file; created = []
    with Session(engine) as session:
        async def save_then_withdraw(identifier, content):
            result = await original(identifier, content); created.append(identifier)
            row = session.get(MeetingCapture, capture["id"]); row.generation += 1; session.add(row); session.flush()
            return result
        monkeypatch.setattr(store, "save_file", save_then_withdraw)
        upload = UploadFile(BytesIO(b"real recorded media fixture"), filename="clip.webm", headers=Headers({"content-type": "audio/webm"}))
        with pytest.raises(HTTPException) as error:
            asyncio.run(files.upload_file("shared", upload, session.get(User,"owner"), session, "recorded-clip-00001", receipt, "recorded-clip-00001"))
        assert error.value.status_code == 409
        assert session.exec(select(File)).all() == []
    assert len(created) == 1 and not store.get_file_path(created[0]).exists()


def test_native_recording_file_retry_cannot_acknowledge_after_withdrawal(team, monkeypatch, tmp_path):
    from app.api import files
    from app.services.storage import StorageService
    from app.config import settings
    client, engine, tokens, _, _, path = setup(team)
    client.app.include_router(files.router)
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "recordings")); monkeypatch.setattr(files,"storage_service",StorageService())
    capture = request(client, tokens, path); vote(client, tokens, path, capture); control(client, tokens, path, capture, "start")
    receipt = control(client, tokens, path, capture, "segment").json()["publication_receipt"]
    headers = {**native(tokens["owner"]), "Idempotency-Key":"recorded-clip-00002", "X-Meeting-Capture-Receipt":receipt, "X-Meeting-Capture-Clip-ID":"recorded-clip-00002"}
    def send(): return client.post("/files/channels/shared", headers=headers, files={"file":("clip.webm",b"real-media-bytes","audio/webm")})
    first = send(); assert first.status_code == 200, first.text
    assert send().json()["id"] == first.json()["id"]
    vote(client,tokens,path,capture,False)
    assert send().status_code == 409
    with Session(engine) as session:
        assert session.get(MeetingCaptureReceipt,receipt).clip_id == "recorded-clip-00002"
        row=session.get(MeetingCaptureReceipt,receipt); row.erased_at=datetime.utcnow(); session.add(row); session.commit()
        with pytest.raises(HTTPException): capture_service.authorize_publication(session,receipt,"shared","owner","recorded-clip-00002")


def test_http_consent_withdrawal_commits_while_private_blob_storage_is_awaiting(team, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from starlette.concurrency import run_in_threadpool
    from app.api import files
    from app.config import settings
    from app.services.storage import StorageService
    from app.models.file import File
    client, engine, tokens, _, _, path = setup(team); client.app.include_router(files.router)
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "private-blob")); storage=StorageService(); monkeypatch.setattr(files,"storage_service",storage)
    capture=request(client,tokens,path);vote(client,tokens,path,capture);control(client,tokens,path,capture,"start")
    receipt=control(client,tokens,path,capture,"segment").json()["publication_receipt"]
    started,released=Event(),Event(); written=[]; original=storage.save_file
    async def held_storage(identifier,content):
        result=await original(identifier,content);written.append(identifier);started.set()
        if not await run_in_threadpool(lambda:released.wait(10)):raise RuntimeError("Owned storage fixture was not released")
        return result
    monkeypatch.setattr(storage,"save_file",held_storage)
    headers={**native(tokens["owner"]),"Idempotency-Key":"real-concurrent-clip","X-Meeting-Capture-Receipt":receipt,"X-Meeting-Capture-Clip-ID":"real-concurrent-clip"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(lambda:client.post("/files/channels/shared",headers=headers,files={"file":("capture.webm",b"actual-private-file-fixture","audio/webm")}))
        try:
            assert started.wait(5),"Upload did not enter actual private storage"
            withdrawn=vote(client,tokens,path,capture,False)
            assert withdrawn.status_code==200,withdrawn.text
            assert not released.is_set(),"Consent did not commit until after upload publication"
        finally:released.set()
        result=future.result(timeout=10)
    assert result.status_code==409,result.text
    with Session(engine) as session:assert session.exec(select(File)).all()==[]
    assert written and all(not storage.get_file_path(identifier).exists() for identifier in written)


def test_demo_recording_limits_fit_real_sample_upload_budget(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,"demo_instance",True)
    config=capture_service.configuration()
    assert config["max_part_bytes"]==1536*1024 and config["max_part_seconds"]==15
    assert config["max_part_bytes"]<config["sample_upload_limit_bytes"]==2*1024*1024
    assert config["sample_session_limit_bytes"]==8*1024*1024


def test_control_reauthorizes_roster_after_committing_rate_limiter(team, monkeypatch):
    client, _, tokens, meeting, _, path=setup(team)
    capture=request(client,tokens,path);vote(client,tokens,path,capture)
    original=media_capture.account_limit;called=False
    def limiter(session,name,*args):
        nonlocal called
        original(session,name,*args)
        if name=="meeting-capture-control" and not called:
            called=True
            result=client.post('/meetings/'+meeting['meeting']['id']+'/join',headers=native(tokens['other']),json={})
            assert result.status_code==200,result.text
    monkeypatch.setattr(media_capture,'account_limit',limiter)
    result=control(client,tokens,path,capture,'start')
    assert called and result.status_code==409,result.text


def test_vote_rechecks_native_session_after_rate_limiter_commit(team, monkeypatch):
    from app.models.session import ActiveSession
    from app.services.auth import token_digest
    client, engine, tokens, _, _, path=setup(team);capture=request(client,tokens,path)
    original=media_capture.account_limit
    def limiter(session,name,*args):
        original(session,name,*args)
        if name=='meeting-capture-consent':
            with Session(engine) as current:
                row=current.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(tokens['member']))).one();row.revoked_at=datetime.utcnow();current.add(row);current.commit()
    monkeypatch.setattr(media_capture,'account_limit',limiter)
    assert vote(client,tokens,path,capture).status_code==401


def test_guest_vote_rechecks_admission_after_rate_limiter_commit(team, monkeypatch):
    from app.models.meetings import MeetingParticipant
    client, engine, tokens, meeting, _, path=setup(team)
    guest,_=_meetings.guest(team,meeting)
    identifier=meeting['meeting']['id']
    assert client.post('/meetings/'+identifier+'/participants/'+guest['participant']['id']+'/admit',headers=native(tokens['owner']),json={}).status_code==200
    capture=request(client,tokens,path)
    original=media_capture.account_limit
    def limiter(session,name,*args):
        original(session,name,*args)
        if name=='meeting-capture-consent':
            with Session(engine) as current:
                row=current.get(MeetingParticipant,guest['participant']['id']);row.state='removed';current.add(row);current.commit()
    monkeypatch.setattr(media_capture,'account_limit',limiter)
    response=client.post('/meeting-access/'+identifier+'/capture/consent',headers={'X-Meeting-Token':guest['meeting_token']},json={'capture_id':capture['id'],'generation':capture['generation'],'granted':True})
    assert response.status_code in (401,403)


def test_deleting_recorded_clip_keeps_consumed_receipt_tombstone_and_rejects_recreation(team, monkeypatch, tmp_path):
    from app.api import files,clips
    from app.services.storage import StorageService
    from app.config import settings
    client, engine, tokens, _, _, path=setup(team);client.app.include_router(files.router);client.app.include_router(clips.router)
    monkeypatch.setattr(settings,'storage_path',str(tmp_path/'clips'));monkeypatch.setattr(files,'storage_service',StorageService())
    capture=request(client,tokens,path);vote(client,tokens,path,capture);control(client,tokens,path,capture,'start')
    receipt=control(client,tokens,path,capture,'segment').json()['publication_receipt'];clip_id='consented-recorded-clip'
    headers={**native(tokens['owner']),'Idempotency-Key':clip_id,'X-Meeting-Capture-Receipt':receipt,'X-Meeting-Capture-Clip-ID':clip_id}
    file=client.post('/files/channels/shared',headers=headers,files={'file':('recording.webm',b'recorded-fixture','audio/webm')});assert file.status_code==200,file.text
    body={'id':clip_id,'title':'Consented recording','duration':2,'file_id':file.json()['id'],'publication_receipt':receipt}
    created=client.post('/channels/shared/clips',headers=native(tokens['owner']),json=body);assert created.status_code==201,created.text
    assert client.delete('/channels/shared/clips/'+clip_id,headers=native(tokens['owner'])).status_code==200
    recreated=client.post('/channels/shared/clips',headers=native(tokens['owner']),json=body)
    assert recreated.status_code==409,recreated.text
    with Session(engine) as current:
        stored=current.get(MeetingCaptureReceipt,receipt);assert stored.clip_id==clip_id and stored.erased_at is not None


@pytest.mark.parametrize("mode",["matrix","unknown"])
def test_capture_time_mode_cannot_fall_back_to_native_plaintext_after_binding_removal(team,monkeypatch,tmp_path,mode):
    from app.api import files
    from app.models.secure_channel import SecureChannel
    from app.models.file import File
    from app.services.storage import StorageService
    from app.config import settings
    client,engine,tokens,_,_,path=setup(team);client.app.include_router(files.router)
    monkeypatch.setattr(settings,'storage_path',str(tmp_path/'private-mode'));storage=StorageService();monkeypatch.setattr(files,'storage_service',storage)
    if mode=='matrix':
        with Session(engine)as s:s.add(SecureChannel(channel_id='shared',created_by='owner'));s.commit()
    capture=request(client,tokens,path);vote(client,tokens,path,capture);control(client,tokens,path,capture,'start');receipt=control(client,tokens,path,capture,'stop').json()['publication_receipt']
    with Session(engine)as s:
        row=s.get(MeetingCapture,capture['id']);assert row.content_mode==('matrix'if mode=='matrix'else'server')
        if mode=='matrix':s.delete(s.get(SecureChannel,'shared'))
        else:row.content_mode='unknown';s.add(row)
        s.commit()
    identifier='immutable-mode-recording'
    response=client.post('/files/channels/shared',headers={**native(tokens['owner']),'Idempotency-Key':identifier,'X-Meeting-Capture-Receipt':receipt,'X-Meeting-Capture-Clip-ID':identifier},files={'file':('recording.webm',b'private-recorded-fixture','audio/webm')})
    assert response.status_code==409,response.text
    with Session(engine)as s:assert s.exec(select(File)).all()==[]
    assert list(storage.storage_path.glob('*'))==[] if hasattr(storage,'storage_path')else not list((tmp_path/'private-mode').glob('*'))


@pytest.mark.parametrize('stopped',[False,True])
def test_actual_enable_endpoint_invalidates_active_and_stopped_native_capture_receipts(team,monkeypatch,stopped):
    client,engine,tokens,_,_,path=setup(team);client.app.include_router(secure_messaging.router)
    monkeypatch.setattr(secure_messaging,'require_matrix',lambda:None)
    def provision(session,binding):binding.matrix_room_id='!owned-mode-fixture:example.test';binding.status='active';session.add(binding)
    monkeypatch.setattr(secure_messaging,'create_secure_room',provision);monkeypatch.setattr(secure_messaging,'sync_secure_membership',lambda *_:None)
    capture=request(client,tokens,path);vote(client,tokens,path,capture);control(client,tokens,path,capture,'start')
    receipt=control(client,tokens,path,capture,'stop'if stopped else'segment').json()['publication_receipt']
    enabled=client.post('/secure/channels/shared/enable',headers=native(tokens['owner']))
    assert enabled.status_code==200,enabled.text
    with Session(engine)as s:
        row=s.get(MeetingCapture,capture['id']);assert row.content_mode=='server'and row.policy_invalidated and row.generation>capture['generation']
        with pytest.raises(HTTPException):capture_service.authorize_publication(s,receipt,'shared','owner','recorded-mode-fixture')
    if not stopped:assert control(client,tokens,path,capture,'resume').status_code==409


def test_caption_request_requires_explicit_exact_audience_before_consent(team):
    client,_,tokens,_,_,path=setup(team)
    for value in [None,'channel']:
        data={'captions':True,**({'caption_audience':value}if value else{})}
        assert client.post(path,headers=native(tokens['owner']),json=data).status_code==422
    response=client.post(path,headers=native(tokens['owner']),json={'captions':True,'caption_audience':'admitted'})
    assert response.status_code==201 and response.json()['capture']['caption_audience']=='admitted'
