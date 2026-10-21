"""Reviewed clip transcripts are explicit, revocable provider context."""
import json
from sqlmodel import Session
from app.api import clips
from app.models.clips import ChannelClip
from app.models.copilot import CopilotTurn
from app.models.file import File
from app.services import copilot as service
from tests.test_copilot import ai, provider, ask, conversation, use_provider, headers
from tests.test_data_controls import controls


def test_native_clip_notes_use_only_selected_transcript_through_real_private_https(ai, provider):
    ai[0].app.include_router(clips.router)
    with Session(ai[1]) as session:
        session.add(File(id='c' * 32, channel_id='native', uploader_id='owner', filename='clip.webm', content_type='audio/webm', size=1000, storage_path='unused')); session.commit()
    result = ai[0].post('/channels/native/clips', headers=headers(ai), json={'id': 'meeting-notes', 'file_id': 'c' * 32, 'title': 'Reviewed meeting', 'duration': 20, 'transcript': 'APPROVED CLIP DECISION: release on Friday'})
    assert result.status_code == 201, result.text
    endpoint, state = provider; use_provider(ai, endpoint); row = conversation(ai)
    response = ask(ai, row, sources=[{'kind': 'clip', 'id': 'meeting-notes'}]); assert response.status_code == 200, response.text
    sent = json.dumps(state['requests']); assert 'APPROVED CLIP DECISION' in sent and 'OLD PRIVATE CONTENT' not in sent and 'HIDDEN MUST STAY PRIVATE' not in sent
    citation = response.json()['citations'][0]; assert citation['kind'] == 'clip' and citation['id'] == 'meeting-notes'
    ai[0].delete('/channels/native/clips/meeting-notes', headers=headers(ai))
    assert ask(ai, conversation(ai), sources=[{'kind': 'clip', 'id': 'meeting-notes'}]).status_code == 409


def test_secure_clip_notes_require_explicit_disclosure_and_never_retain_decrypted_transcript(ai, provider, monkeypatch):
    with Session(ai[1]) as session:
        session.add(ChannelClip(id='secure-notes', channel_id='secure', created_by='owner', content_mode='matrix', matrix_event_id='$clip-event', payload_hash='opaque')); session.commit()
    monkeypatch.setattr(service, 'validate_secure_event', lambda *_: None)
    endpoint, state = provider; use_provider(ai, endpoint); row = conversation(ai, 'secure')
    sources = [{'kind': 'clip', 'id': 'secure-notes', 'event_ids': ['$clip-event'], 'text': 'EXPLICITLY DISCLOSED SECURE TRANSCRIPT'}]
    assert ask(ai, row, sources=sources).status_code == 422 and not state['requests']
    response = ask(ai, row, sources=sources, disclose_decrypted_content=True); assert response.status_code == 200, response.text
    assert response.json()['ephemeral'] and 'EXPLICITLY DISCLOSED SECURE TRANSCRIPT' in json.dumps(state['requests'])
    with Session(ai[1]) as session:
        turn = session.get(CopilotTurn, response.json()['id']); assert turn.encrypted_result is None
        assert 'EXPLICITLY DISCLOSED' not in service.unseal_private(turn.encrypted_sources, 'copilot-sources', turn.id)
