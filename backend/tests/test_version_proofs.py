"""Hash-only timestamps bind immutable versions, enforce access and parse real OTS."""
from datetime import datetime, timedelta
import hashlib
import json

import httpx
import pytest
from sqlmodel import Session, select

from app.api import decisions, proofs
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import DecisionRevision
from app.models.secure_channel import SecureChannel
from app.models.version_proof import VersionProof
from app.services import otrust
from tests.test_file_security import file_api, upload


@pytest.fixture
def proof_api(file_api, monkeypatch):
    file_api.app.include_router(proofs.router)
    file_api.app.include_router(decisions.router)
    monkeypatch.setattr(proofs, "storage_service", file_api.storage)
    monkeypatch.setattr(otrust.settings, "otrust_enabled", True)
    monkeypatch.setattr(otrust.settings, "otrust_base_url", "https://timestamp.example")
    return file_api


def request(api, target, kind="file", **fields):
    return api.client.post('/channels/channel/proofs', json={"target_kind": kind, "target_id": target, "consent": True, **fields})


def ots(digest, bitcoin=False):
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
    from opentimestamps.core.serialize import BytesSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
    stamp = Timestamp(bytes.fromhex(digest))
    stamp.attestations.add(BitcoinBlockHeaderAttestation(123) if bitcoin else PendingAttestation("https://calendar.example"))
    detached = DetachedTimestampFile(OpSHA256(), stamp)
    context = BytesSerializationContext()
    detached.serialize(context)
    return context.getbytes()


def due(session, identifier):
    row = session.get(VersionProof, identifier)
    row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
    session.add(row); session.commit()


def test_exact_file_bytes_deduplicate_and_changed_version_gets_separate_receipt(proof_api):
    api = proof_api
    first = upload(api, b"original bytes")
    result = request(api, first['id'])
    assert result.status_code == 200, result.text
    row = result.json()
    assert row['sha256'] == hashlib.sha256(b'original bytes').hexdigest()
    assert row['content_binding'] == 'server_bytes' and row['state'] == 'queued'
    assert not row['bitcoin_verified'] and not row['commitment_checked']
    assert request(api, first['id']).json()['id'] == row['id']
    assert request(api, first['id'], sha256='0'*64).status_code == 409
    revised = api.client.post('/files/' + first['id'] + '/versions', files={'file': ('next.txt', b'new bytes', 'text/plain')}).json()
    new = request(api, revised['id']).json()
    assert new['id'] != row['id'] and new['sha256'] == hashlib.sha256(b'new bytes').hexdigest()
    assert request(api, first['id']).json()['id'] == row['id']
    assert 'original bytes' not in json.dumps(row) and first['filename'] not in json.dumps(row)


def test_consent_disabled_provider_and_plaintext_fields_fail_closed(proof_api, monkeypatch):
    api = proof_api; file = upload(api)
    assert api.client.post('/channels/channel/proofs', json={'target_kind': 'file', 'target_id': file['id']}).status_code == 422
    assert api.client.post('/channels/channel/proofs', json={'target_kind': 'file', 'target_id': file['id'], 'consent': False}).status_code == 422
    assert request(api, file['id'], filename='secret.txt').status_code == 422
    monkeypatch.setattr(otrust.settings, 'otrust_enabled', False)
    assert request(api, file['id']).status_code == 503
    assert api.client.get('/proofs/status').json()['enabled'] is False


def test_membership_download_retry_and_removed_member_boundaries(proof_api):
    api = proof_api; file = upload(api)
    row = request(api, file['id']).json()
    with Session(api.engine) as session:
        stored = session.get(VersionProof, row['id']); stored.proof_data = ots(stored.sha256); stored.commitment_checked = True
        stored.state = 'proof_available'; session.add(stored); session.commit()
    api.app.state.user_id = 'member'
    assert api.client.get('/channels/channel/proofs').status_code == 200
    assert api.client.get('/proofs/' + row['id'] + '/download').status_code == 200
    assert api.client.post('/proofs/' + row['id'] + '/refresh').status_code == 403
    api.app.state.user_id = 'outsider'
    assert request(api, file['id']).status_code == 403
    assert api.client.get('/channels/channel/proofs').status_code == 403
    assert api.client.get('/proofs/' + row['id'] + '/download').status_code == 403
    with Session(api.engine) as session:
        session.delete(session.get(ChannelMember, ('channel', 'owner'))); session.commit()
    api.app.state.user_id = 'owner'
    assert api.client.get('/proofs/' + row['id'] + '/download').status_code == 403


def test_request_does_not_trust_storage_path_or_symlink(proof_api):
    api = proof_api; file = upload(api)
    path = api.storage.get_file_path(file['id']); path.unlink()
    outside = api.tmp_path / 'outside'; outside.write_bytes(b'other private data'); path.symlink_to(outside)
    assert request(api, file['id']).status_code == 409


def test_decision_commits_to_immutable_canonical_revision(proof_api):
    api = proof_api
    fields = {'title': 'Återställning 🌲', 'content': 'Exact\nwords', 'status': 'accepted', 'decision_date': '2026-10-20'}
    first = api.client.post('/channels/channel/decisions', json=fields).json()
    row = request(api, first['revision_id'], 'decision').json()
    canonical = otrust.decision_bytes({**fields, 'decision_id': first['id'], 'revision': 1})
    assert row['sha256'] == hashlib.sha256(canonical).hexdigest()
    second = api.client.post('/decisions/' + first['id'] + '/revisions', json={**fields, 'title': 'New title', 'expected_revision': 1}).json()
    assert request(api, second['revision_id'], 'decision').json()['sha256'] != row['sha256']
    assert request(api, first['revision_id'], 'decision').json()['id'] == row['id']


def test_foreign_channel_file_and_revision_references_rejected(proof_api):
    api = proof_api; file = upload(api)
    with Session(api.engine) as session:
        session.add(Channel(id='other', name='Other')); session.add(ChannelMember(channel_id='other', user_id='owner')); session.commit()
    assert api.client.post('/channels/other/proofs', json={'target_kind': 'file', 'target_id': file['id'], 'consent': True}).status_code == 404
    assert request(api, 'nonexistent', 'decision').status_code == 404


def test_encrypted_digest_is_client_asserted_and_opaque_event_bound(proof_api, monkeypatch):
    api = proof_api; seen = []
    with Session(api.engine) as session:
        session.add(SecureChannel(channel_id='channel', created_by='owner', status='active', matrix_room_id='!room:example'))
        session.commit()
    def validate(session, channel_id, target_id, user_id, require_sender):
        seen.append((channel_id, target_id, user_id, require_sender))
        if target_id != '$encrypted-file':
            from fastapi import HTTPException
            raise HTTPException(400, 'Wrong room')
    monkeypatch.setattr(proofs, 'validate_secure_event', validate)
    assert request(api, '$encrypted-file').status_code == 400
    result = request(api, '$encrypted-file', sha256='a'*64)
    assert result.status_code == 200, result.text
    assert result.json()['content_binding'] == 'client_asserted'
    assert result.json()['sha256'] == 'a'*64
    assert not result.json()['bitcoin_verified']
    assert seen[-1] == ('channel', '$encrypted-file', 'owner', False)
    assert request(api, '$foreign-room', sha256='a'*64).status_code == 400


def test_durable_worker_hash_only_claim_pending_then_real_ots_download(proof_api):
    api = proof_api; file = upload(api, b'Content never sent to provider')
    row = request(api, file['id']).json(); seen = []; reads = 0
    def handle(request):
        nonlocal reads
        seen.append(request)
        if request.method == 'POST':
            payload = json.loads(request.content)
            assert payload == {'hash': row['sha256'], 'source': 'api'}
            return httpx.Response(200, json={'receipt_id': 'receipt_123', 'hash': payload['hash'], 'timestamp': '2026-10-20T12:00:00Z'})
        reads += 1
        if reads == 1:
            return httpx.Response(202, headers={'Retry-After': '120'})
        return httpx.Response(200, content=ots(row['sha256']))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client, Session(api.engine) as session:
        assert otrust.process_pending(session, client=client) == 1
        pending = session.get(VersionProof, row['id'])
        assert pending.receipt_id == 'receipt_123' and pending.state == 'pending'
        assert seen[0].headers['Idempotency-Key'] == 'freecollab-proof-' + row['id']
        due(session, row['id']); assert otrust.process_pending(session, client=client) == 1
        pending = session.get(VersionProof, row['id'])
        assert pending.state == 'pending' and pending.next_attempt_at > datetime.utcnow() + timedelta(seconds=110)
        due(session, row['id']); assert otrust.process_pending(session, client=client) == 1
        stored = session.get(VersionProof, row['id'])
        assert stored.state == 'proof_available' and stored.commitment_checked and not stored.bitcoin_verified
        assert len([request for request in seen if request.method == 'POST']) == 1
    downloaded = api.client.get('/proofs/' + row['id'] + '/download')
    assert downloaded.content == ots(row['sha256']) and downloaded.headers['cache-control'] == 'private, no-store'


def test_lost_claim_response_reuses_idempotency_key_and_bad_proof_never_gets_badge(proof_api):
    api = proof_api; file = upload(api)
    row = request(api, file['id']).json(); keys = []
    def handle(request):
        if request.method == 'POST':
            keys.append(request.headers['Idempotency-Key'])
            if len(keys) == 1:
                raise httpx.ReadTimeout('simulated lost response')
            return httpx.Response(200, json={'receipt_id': 'retained', 'hash': row['sha256']})
        return httpx.Response(200, content=ots('f'*64))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client, Session(api.engine) as session:
        assert otrust.process_pending(session, client=client) == 0
        due(session, row['id']); assert otrust.process_pending(session, client=client) == 1
        due(session, row['id']); assert otrust.process_pending(session, client=client) == 0
        stored = session.get(VersionProof, row['id'])
        assert stored.state == 'failed' and not stored.proof_data and not stored.commitment_checked and not stored.bitcoin_verified
        assert keys[0] == keys[1]
    assert api.client.get('/proofs/' + row['id'] + '/download').status_code == 409


@pytest.mark.parametrize('content', [b'', b'fake proof', b'x' * (1024 * 1024 + 1)])
def test_ots_parser_rejects_malformed_or_unbounded_proofs(content):
    with pytest.raises(Exception):
        otrust.validate_proof(content, 'a'*64)


def test_bitcoin_attestation_alone_is_never_independent_chain_verification():
    digest = hashlib.sha256(b'test').hexdigest()
    result = otrust.validate_proof(ots(digest, bitcoin=True), digest)
    assert result == {'commitment_checked': True, 'bitcoin_attestation_present': True, 'bitcoin_verified': False}
    with pytest.raises(ValueError):
        otrust.validate_proof(ots(digest), 'a'*64)


def test_provider_origin_changes_fail_without_leaking_digest_to_new_provider(proof_api, monkeypatch):
    api = proof_api; row = request(api, upload(api)['id']).json(); seen = []
    monkeypatch.setattr(otrust.settings, 'otrust_base_url', 'https://different.example')
    with httpx.Client(transport=httpx.MockTransport(lambda request: seen.append(request))) as client, Session(api.engine) as session:
        assert otrust.process_pending(session, client=client) == 0
        assert session.get(VersionProof, row['id']).state == 'failed'
    assert not seen


def test_refresh_preserves_provider_backoff_and_receipt_id(proof_api):
    api = proof_api; row = request(api, upload(api)['id']).json()
    with Session(api.engine) as session:
        stored = session.get(VersionProof, row['id']); stored.state = 'pending'; stored.receipt_id = 'kept'
        stored.next_attempt_at = datetime.utcnow() + timedelta(hours=1); session.add(stored); session.commit()
    refreshed = api.client.post('/proofs/' + row['id'] + '/refresh')
    assert refreshed.status_code == 200 and refreshed.json()['receipt_id'] == 'kept'
    assert refreshed.json()['state'] == 'pending' and refreshed.json()['next_attempt_at'] > datetime.utcnow().isoformat()


def test_removed_requester_cannot_start_queued_digest_disclosure(proof_api):
    api = proof_api; row = request(api, upload(api)['id']).json(); seen = []
    with Session(api.engine) as session:
        session.delete(session.get(ChannelMember, ('channel', 'owner'))); session.commit()
    with httpx.Client(transport=httpx.MockTransport(lambda request: seen.append(request))) as client, Session(api.engine) as session:
        assert otrust.process_pending(session, client=client) == 0
        assert session.get(VersionProof, row['id']).state == 'failed'
    assert not seen
