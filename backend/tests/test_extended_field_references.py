"""Real REST persistence for explicit IDs in every newly shared writing surface."""
from datetime import datetime, timezone
import json
import pytest
from sqlmodel import Session, select

from app.api import clips, file_experience, integrations_content, workflows
from app.models.channel import ChannelMember
from app.models.clips import ChannelClip
from app.models.file_experience import FileComment
from app.models.integration_content import IntegrationContentRevision
from app.models.secure_channel import SecureChannel
from app.models.user import User
from app.models.workflow import WorkflowVote
from app.services import calendar_planning, workflow as workflow_service
from app.services.auth import revoke_token
from app.services.integration_content import CalendarFields
from tests.test_clips import setup as setup_clips
from tests.test_document_calendar_integrations import content, DOC, EVENT, headers
from tests.test_file_experience import api as file_api, path as file_path
from tests.test_shared_documents import documents
from tests.test_workflows import flows, BASE, create, start, process, read, headers as flow_headers, definition


def reference(text, label='@peer', identifier='peer'):
    start = len(text[:text.index(label)].encode('utf-16-le')) // 2
    return {'kind': 'person', 'id': identifier, 'start': start, 'end': start + len(label.encode('utf-16-le')) // 2}


@pytest.mark.parametrize('kind,base,detail', [('documents', DOC, 'notes'), ('calendar', EVENT, 'description')])
def test_linked_documents_and_calendar_revisions_keep_exact_reference_fields(content, kind, base, detail):
    title, text = '  @peer  ', '🌿 Ask @peer before the next review.  '
    fields = {**base, 'title': title, detail: text, 'field_references': {'title': [reference(title)], detail: [reference(text)]}}
    if kind == 'calendar':
        fields['location'] = 'Beside @peer'
        fields['field_references']['location'] = [reference(fields['location'])]
    endpoint = '/channels/room/integrations/' + kind
    response = content.client.post(endpoint, json={'id': 'explicit', 'fields': fields}, headers=headers())
    assert response.status_code == 200, response.text
    expected = CalendarFields.model_validate(fields).model_dump(mode='json') if kind == 'calendar' else fields
    assert response.json()['fields'] == expected
    with Session(content.engine) as session:
        session.delete(session.get(ChannelMember, ('room', 'peer'))); session.commit()
    # Unchanged historical selected IDs survive ordinary revisions; they are not grants.
    response = content.client.post(endpoint, json={'id': 'explicit', 'expected_revision': 1, 'fields': fields}, headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()['fields']['field_references'] == fields['field_references']
    edited = {**fields, detail: text + 'Changed'}
    assert content.client.post(endpoint, json={'id': 'explicit', 'expected_revision': 2, 'fields': edited}, headers=headers()).status_code == 404
    history = content.client.get(endpoint + '/explicit/revisions', headers=headers()).json()
    assert len(history) == 2 and all(row['fields']['field_references'] == fields['field_references'] for row in history)
    with Session(content.engine) as session:
        ciphertexts = [row.encrypted_payload for row in session.exec(select(IntegrationContentRevision)).all()]
        assert all(text not in raw and 'field_references' not in raw for raw in ciphertexts)


@pytest.mark.parametrize('kind,base,field', [('documents', DOC, 'notes'), ('calendar', EVENT, 'location')])
@pytest.mark.parametrize('invalid', ['foreign', 'split-surrogate', 'unknown-field', 'extra-metadata'])
def test_integration_reference_bounds_and_targets_fail_without_revision(content, kind, base, field, invalid):
    text = '🌿 @peer'
    refs = {field: [reference(text)]}
    expected = 422
    if invalid == 'foreign': refs[field][0]['id'] = 'outsider'; expected = 404
    elif invalid == 'split-surrogate': refs[field][0]['start'] = 1
    elif invalid == 'unknown-field': refs = {'url': []}
    else: refs[field][0]['guessed_label'] = '@peer'
    response = content.client.post('/channels/room/integrations/' + kind, json={'id': 'bad', 'fields': {**base, field: text, 'field_references': refs}}, headers=headers())
    assert response.status_code == expected, response.text
    with Session(content.engine) as session:
        assert not session.exec(select(IntegrationContentRevision)).all()


def test_calendar_occurrence_overrides_drop_only_changed_field_bindings():
    value = {**EVENT, 'title': 'Ask @peer', 'description': 'With @peer', 'field_references': {'title': [reference('Ask @peer')], 'description': [reference('With @peer')]},
             'recurrence': {'frequency': 'daily', 'interval': 1, 'count': 2},
             'exceptions': [{'original_start': '2026-10-25T00:30:00Z', 'title': 'Another title'}]}
    fields = CalendarFields.model_validate(value)
    rows = calendar_planning.expand_occurrences(fields, datetime(2026, 10, 24, tzinfo=timezone.utc), datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert len(rows) == 2
    assert rows[0]['field_references'] == {'description': [reference('With @peer')]}
    assert rows[1]['field_references'] == value['field_references']
    assert fields.model_dump()['field_references'] == value['field_references']


def test_clip_reference_ids_survive_long_transcript_and_opaque_mode_rejects_sidecars(documents, monkeypatch):
    d = documents; setup_clips(d)
    transcript = 'x' * 10010 + ' @peer'
    fields = {'id': 'with-ids', 'file_id': 'a' * 32, 'title': 'Ask @peer', 'duration': 5, 'transcript': transcript,
              'field_references': {'title': [reference('Ask @peer')], 'transcript': [reference(transcript)]}}
    response = d.client.post('/channels/room/clips', json=fields, headers=headers())
    assert response.status_code == 201, response.text
    assert response.json()['fields']['field_references'] == fields['field_references']
    assert d.client.get('/channels/room/clips', headers=headers()).json()[0]['fields']['field_references'] == fields['field_references']
    with Session(d.engine) as session:
        raw = session.get(ChannelClip, 'with-ids').encrypted_payload
        assert transcript not in raw and 'field_references' not in raw
        session.add(SecureChannel(channel_id='room', created_by='owner', status='active')); session.commit()
    monkeypatch.setattr(clips.content, 'validate_secure_event', lambda *args: None)
    for refs in [{}, {'title': [reference('Ask @peer')]}]:
        response = d.client.post('/channels/room/clips', json={'id': 'leak', 'matrix_event_id': '$opaque', 'field_references': refs}, headers=headers())
        assert response.status_code == 422
    response = d.client.post('/channels/room/clips', json={'id': 'opaque', 'matrix_event_id': '$opaque'}, headers=headers())
    assert response.status_code == 201 and 'fields' not in response.json()


def test_file_comment_exact_referenced_text_is_encrypted_and_idempotent(file_api):
    d = file_api; text = '  🌿 Ask @member  '
    refs = {'text': [reference(text, '@member', 'member')]}
    data = {'id': 'with-ids', 'text': text, 'field_references': refs}
    endpoint = file_path(d) + '/comments'
    response = d.client.post(endpoint, json=data, headers=d.headers)
    assert response.status_code == 201, response.text
    assert response.json()['text'] == text and response.json()['field_references'] == refs
    assert d.client.post(endpoint, json=data, headers=d.headers).status_code == 201
    changed = {**data, 'field_references': {'text': [{**refs['text'][0], 'id': 'owner'}]}}
    assert d.client.post(endpoint, json=changed, headers=d.headers).status_code == 409
    assert d.client.get(endpoint, headers=d.headers).json()[0]['field_references'] == refs
    with Session(d.engine) as session:
        raw = session.get(FileComment, 'with-ids').encrypted_text
        assert text not in raw and 'field_references' not in raw


def test_approval_note_references_roundtrip_with_legacy_raw_notes_and_context_binding(flows):
    client, engine, tokens = flows
    config = definition(recipients=['approver', 'member'], quorum=2)
    run_id = start(flows, create(flows, config).json()); process(engine)
    note = '  Ask @member  '
    refs = {'note': [reference(note, '@member', 'member')]}
    endpoint = BASE + '/runs/' + run_id + '/vote'
    payload = {'choice': 'approve', 'note': note, 'field_references': refs}
    response = client.post(endpoint, json=payload, headers=flow_headers(tokens, 'approver'))
    assert response.status_code == 200, response.text
    vote = response.json()['votes'][0]
    assert vote['note'] == note and vote['field_references'] == refs
    assert client.post(endpoint, json=payload, headers=flow_headers(tokens, 'approver')).status_code == 200
    changed = {**payload, 'field_references': {'note': [{**refs['note'][0], 'id': 'owner'}]}}
    assert client.post(endpoint, json=changed, headers=flow_headers(tokens, 'approver')).status_code == 409
    legacy = json.dumps({'note': 'Literal JSON, never interpreted as a new envelope'})
    response = client.post(endpoint, json={'choice': 'approve', 'note': legacy}, headers=flow_headers(tokens, 'member'))
    assert response.status_code == 200
    assert next(v for v in response.json()['votes'] if v['user_id'] == 'member')['note'] == legacy
    with Session(engine) as session:
        row = session.get(WorkflowVote, (run_id, 0, 'approver'))
        assert row.encrypted_note.startswith(workflow_service.VOTE_REFERENCE_PREFIX)
        assert note not in row.encrypted_note and 'field_references"' not in row.encrypted_note
        # Referenced payloads bind the immutable actor/step as well as the run.
        other = session.get(WorkflowVote, (run_id, 0, 'member')); other.encrypted_note = row.encrypted_note
        session.add(other); session.commit()
    assert client.get(BASE + '/runs/' + run_id, headers=flow_headers(tokens)).status_code == 409


def test_secure_approval_rejects_native_reference_sidecars_even_when_empty(flows, monkeypatch):
    client, engine, tokens = flows
    with Session(engine) as session:
        session.add(SecureChannel(channel_id='channel-a', created_by='owner', status='active')); session.commit()
    run_id = start(flows, create(flows).json()); process(engine)
    endpoint = BASE + '/runs/' + run_id + '/vote'
    response = client.post(endpoint, json={'choice': 'approve', 'field_references': {}}, headers=flow_headers(tokens, 'approver'))
    assert response.status_code == 409
    from app.services import matrix_bridge
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', lambda *args: None)
    response = client.post(endpoint, json={'choice': 'approve', 'matrix_event_id': '$opaque'}, headers=flow_headers(tokens, 'approver'))
    assert response.status_code == 200, response.text
    assert response.json()['votes'][0]['note'] is None
    assert 'field_references' not in response.json()['votes'][0]


def test_file_comment_rechecks_revoked_credential_after_new_target_lookup(file_api, monkeypatch):
    d = file_api
    original = file_experience.require_field_reference_targets
    def changed(session, *args, **kwargs):
        original(session, *args, **kwargs)
        revoke_token(d.token, session)
    monkeypatch.setattr(file_experience, 'require_field_reference_targets', changed)
    text = 'Ask @member'
    response = d.client.post(file_path(d) + '/comments', json={'id': 'stale', 'text': text, 'field_references': {'text': [reference(text, '@member', 'member')]}}, headers=d.headers)
    assert response.status_code == 401
    with Session(d.engine) as session:
        assert session.get(FileComment, 'stale') is None


def test_clip_rechecks_channel_member_after_new_target_lookup(documents, monkeypatch):
    d = documents; setup_clips(d)
    original = clips.require_field_reference_targets
    def changed(session, *args, **kwargs):
        original(session, *args, **kwargs)
        session.delete(session.get(ChannelMember, ('room', 'member'))); session.commit()
    monkeypatch.setattr(clips, 'require_field_reference_targets', changed)
    text = 'Ask @peer'
    response = d.client.post('/channels/room/clips', json={'id': 'stale', 'file_id': 'a' * 32, 'duration': 5, 'title': text, 'field_references': {'title': [reference(text)]}}, headers=headers())
    assert response.status_code == 403
    with Session(d.engine) as session:
        assert session.get(ChannelClip, 'stale') is None


def test_integration_rechecks_native_mode_after_new_target_lookup(content, monkeypatch):
    original = integrations_content.require_field_reference_targets
    def changed(session, *args, **kwargs):
        original(session, *args, **kwargs)
        session.add(SecureChannel(channel_id='room', created_by='owner', status='active')); session.commit()
    monkeypatch.setattr(integrations_content, 'require_field_reference_targets', changed)
    text = 'Ask @peer'
    response = content.client.post('/channels/room/integrations/documents', json={'id': 'stale', 'fields': {**DOC, 'title': text, 'field_references': {'title': [reference(text)]}}}, headers=headers())
    assert response.status_code == 409
    with Session(content.engine) as session:
        assert not session.exec(select(IntegrationContentRevision)).all()


@pytest.mark.parametrize('secure', [False, True])
def test_approval_rechecks_session_after_reference_or_matrix_verification(flows, monkeypatch, secure):
    client, engine, tokens = flows
    if secure:
        with Session(engine) as session:
            session.add(SecureChannel(channel_id='channel-a', created_by='owner', status='active')); session.commit()
    run_id = start(flows, create(flows).json()); process(engine)
    if secure:
        from app.services import matrix_bridge
        def changed(session, *args, **kwargs): revoke_token(tokens['approver'], session)
        monkeypatch.setattr(matrix_bridge, 'validate_secure_event', changed)
        payload = {'choice': 'approve', 'matrix_event_id': '$verified-before-revoke'}
    else:
        original = workflows.require_field_reference_targets
        def changed(session, *args, **kwargs):
            original(session, *args, **kwargs); revoke_token(tokens['approver'], session)
        monkeypatch.setattr(workflows, 'require_field_reference_targets', changed)
        text = 'Ask @member'
        payload = {'choice': 'approve', 'note': text, 'field_references': {'note': [reference(text, '@member', 'member')]}}
    response = client.post(BASE + '/runs/' + run_id + '/vote', json=payload, headers=flow_headers(tokens, 'approver'))
    assert response.status_code == 401, response.text
    with Session(engine) as session:
        assert not session.exec(select(WorkflowVote)).all()


def test_late_reference_lookup_cannot_apply_a_vote_to_a_different_approval_step(flows, monkeypatch):
    from app.models.workflow import WorkflowRun
    client, engine, tokens = flows
    config = definition()
    config['steps'].append(dict(config['steps'][0]))
    run_id = start(flows, create(flows, config).json()); process(engine)
    original = workflows.require_field_reference_targets
    def changed(session, *args, **kwargs):
        original(session, *args, **kwargs)
        row = session.get(WorkflowRun, run_id); row.step_index = 1
        session.add(row); session.commit()
    monkeypatch.setattr(workflows, 'require_field_reference_targets', changed)
    text = 'Ask @member'
    response = client.post(BASE + '/runs/' + run_id + '/vote', json={'choice': 'approve', 'note': text, 'field_references': {'note': [reference(text, '@member', 'member')]}}, headers=flow_headers(tokens, 'approver'))
    assert response.status_code == 409, response.text
    with Session(engine) as session:
        assert not session.exec(select(WorkflowVote)).all()
