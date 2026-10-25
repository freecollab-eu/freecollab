"""Explicit secure-device app relay keeps no plaintext outbox or output copy."""
from datetime import datetime, timedelta
import hashlib
import json

from sqlmodel import Session, select
from app.models.app_platform import AppCard, AppForegroundReceipt, AppInstallation, AppJob
from app.models.channel import ChannelMember
from app.models.secure_channel import MatrixAccount, MatrixDeviceRevocation, MatrixSession, SecureChannel
from app.services import app_foreground as service, app_platform
from app.services.auth import decode_token_claims, token_digest
from app.services.remote_transport import Response
from tests.test_webhooks import team
from tests.test_app_platform import platform, install


def secure_install(platform):
    token = platform[2]['owner']['Authorization'].split(' ', 1)[1]
    claims = decode_token_claims(token)
    with Session(platform[1]) as session:
        binding = session.get(SecureChannel, 'secure'); binding.status = 'active'; binding.matrix_room_id = '!secure:customer.test'; session.add(binding)
        session.add(MatrixAccount(user_id='owner', matrix_user_id='@owner:customer.test', encrypted_password='synthetic-sealed', provisioned=True))
        session.add(MatrixSession(matrix_token_hash='synthetic-matrix-token-digest', user_id='owner', auth_token_hash=token_digest(token),
            auth_expires_at=datetime.utcfromtimestamp(claims['exp']), auth_version=claims.get('av', 0), device_id='DEVICE', encrypted_access_token='synthetic-sealed'))
        session.commit()
    manifest = app_platform.catalog()[0]['manifest']; manifest['secure_foreground'] = True; manifest['scopes'] = ['commands:invoke']
    return install(platform, manifest=manifest, channel_ids=['secure'])


def invoke(platform, made, **changes):
    body = dict(request_id='secure_review_request_123', expected_generation=made['installation']['generation'],
        command='invoke', arguments={'text':'PRIVATE DEVICE SELECTED TEXT'}, confirm=True, disclose_to_app=True,
        disclose_decrypted_content=True, matrix_device_id='DEVICE')
    body.update(changes)
    return platform[0].post('/workspaces/team/channels/secure/apps/foreground/'+made['installation']['id'],
        headers=platform[2]['owner'], json=body)


def test_secure_foreground_requires_manifest_device_and_per_request_disclosure(platform, monkeypatch):
    made = secure_install(platform); calls = []
    monkeypatch.setattr(service.remote_transport, 'request', lambda *args, **kwargs: calls.append(kwargs) or Response(200, {}, b'Private output'))
    catalogue = platform[0].get('/workspaces/team/channels/secure/apps/commands', headers=platform[2]['owner'])
    assert catalogue.status_code == 200, catalogue.text
    command = catalogue.json()['commands'][0]
    assert command['supports_secure_foreground'] is True and command['delivery'] == 'foreground'
    assert invoke(platform, made, disclose_decrypted_content=False).status_code == 422
    assert invoke(platform, made, matrix_device_id='UNOWNED').status_code == 403
    assert invoke(platform, made, expected_generation=2).status_code == 409
    assert not calls
    result = invoke(platform, made); assert result.status_code == 200, result.text
    assert result.json()['status'] == 'delivered' and result.json()['preview'] == 'Private output'
    assert len(calls) == 1 and json.loads(calls[0]['body'])['arguments']['text'] == 'PRIVATE DEVICE SELECTED TEXT'
    assert invoke(platform, made).json()['preview'] is None and len(calls) == 1
    history=platform[0].get('/workspaces/team/apps/'+made['installation']['id']+'/jobs',headers=platform[2]['owner'])
    assert history.status_code==200 and history.json()['jobs'][0]['foreground'] is True
    assert history.json()['jobs'][0]['preview'] is None and 'PRIVATE DEVICE' not in history.text and 'Private output' not in history.text
    assert invoke(platform, made, arguments={'text':'Changed'}).status_code == 409
    with Session(platform[1]) as session:
        receipt = session.exec(select(AppForegroundReceipt)).one()
        serialized = repr(receipt.model_dump())
        assert 'PRIVATE DEVICE' not in serialized and 'Private output' not in serialized
        assert receipt.request_digest != hashlib.sha256(b'PRIVATE DEVICE SELECTED TEXT').hexdigest()
        assert not session.exec(select(AppJob)).all() and not session.exec(select(AppCard)).all()
    # Neither native endpoint is permitted to queue encrypted-channel text.
    queued = platform[0].post('/workspaces/team/channels/secure/apps/commands/'+made['installation']['id'], headers=platform[2]['owner'], json={
        'request_id':'not_a_secure_queue','expected_generation':1,'command':'invoke','arguments':{'text':'Do not persist'},'confirm':True,'disclose_to_app':True})
    assert queued.status_code == 409


def test_unknown_external_outcome_never_reexecutes_or_retains_exception(platform, monkeypatch):
    made = secure_install(platform); calls = []
    def ambiguous(*args, **kwargs): calls.append(1); raise TimeoutError('PRIVATE DEVICE SELECTED TEXT / secret output')
    monkeypatch.setattr(service.remote_transport, 'request', ambiguous)
    first = invoke(platform, made); assert first.status_code == 200 and first.json()['status'] == 'uncertain'
    assert invoke(platform, made).json()['id'] == first.json()['id'] and calls == [1]
    assert 'PRIVATE' not in first.text and 'secret output' not in first.text
    with Session(platform[1]) as session:
        assert 'PRIVATE' not in repr(session.exec(select(AppForegroundReceipt)).one().model_dump())


def test_membership_or_device_revocation_during_transport_discards_private_preview(platform, monkeypatch):
    made = secure_install(platform)
    def revoke(*args, **kwargs):
        with Session(platform[1]) as session:
            session.add(MatrixDeviceRevocation(user_id='owner', device_id='DEVICE')); session.commit()
        return Response(200, {}, b'DO NOT REVEAL THIS OUTPUT')
    monkeypatch.setattr(service.remote_transport, 'request', revoke)
    response = invoke(platform, made)
    assert response.status_code == 409 and 'DO NOT REVEAL' not in response.text
    with Session(platform[1]) as session:
        receipt = session.exec(select(AppForegroundReceipt)).one()
        assert receipt.status == 'delivered' and 'Access changed' in receipt.failure
        assert 'DO NOT REVEAL' not in repr(receipt.model_dump())
    assert invoke(platform, made).status_code == 403


def test_retired_processing_receipt_is_uncertain_and_never_replayed(platform, monkeypatch):
    made = secure_install(platform)
    monkeypatch.setattr(service.remote_transport, 'request', lambda *args, **kwargs: Response(200, {}, b'First result'))
    result = invoke(platform, made); assert result.status_code == 200
    with Session(platform[1]) as session:
        receipt = session.exec(select(AppForegroundReceipt)).one(); receipt.status = 'processing'; receipt.created_at = datetime.utcnow()-timedelta(minutes=5)
        session.add(receipt); session.commit()
    calls=[]
    monkeypatch.setattr(service.remote_transport, 'request', lambda *args, **kwargs: calls.append(1) or Response(200, {}, b'Unexpected'))
    retried = invoke(platform, made)
    assert retried.json()['status'] == 'uncertain' and retried.json()['preview'] is None and not calls


def test_native_actor_revoked_while_external_action_runs_cannot_receive_preview(platform, monkeypatch):
    from app.models.session import RevokedToken
    made=secure_install(platform)
    token=platform[2]['owner']['Authorization'].split(' ',1)[1]
    def revoke(*args, **kwargs):
        with Session(platform[1]) as session:
            session.add(RevokedToken(token_hash=token_digest(token),expires_at=datetime.utcnow()+timedelta(hours=1)));session.commit()
        return Response(200,{},b'SECRET RESPONSE AFTER REVOKE')
    monkeypatch.setattr(service.remote_transport,'request',revoke)
    response=invoke(platform,made)
    assert response.status_code==409 and 'SECRET RESPONSE' not in response.text
    with Session(platform[1]) as session:
        receipt=session.exec(select(AppForegroundReceipt)).one()
        assert receipt.status=='delivered' and 'Access changed' in receipt.failure


def test_actual_foreground_relay_preserves_reviewed_whitespace_and_commits_no_copy(platform,monkeypatch):
    made=secure_install(platform);literal='  private indentation\nsecond line  \n';calls=[]
    def receiver(*args,**kwargs):
        calls.append(json.loads(kwargs['body']))
        return Response(200,{},b'Private response')
    monkeypatch.setattr(service.remote_transport,'request',receiver)
    response=invoke(platform,made,arguments={'text':literal})
    assert response.status_code==200,response.text
    assert calls[0]['arguments']['text']==literal
    assert invoke(platform,made,arguments={'text':literal.strip()}).status_code==409 and len(calls)==1
    with Session(platform[1]) as session:
        assert 'private indentation' not in repr(session.exec(select(AppForegroundReceipt)).one().model_dump())
