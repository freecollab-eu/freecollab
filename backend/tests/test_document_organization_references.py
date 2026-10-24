"""Stack discussions retain the existing encrypted document comment identities."""
import pytest
from sqlmodel import Session

from app.models.channel import ChannelMember
from app.models.shared_documents import SharedDocumentComment
from app.services import shared_documents as service
from tests.test_document_organization import PATH, move, organized, stack
from tests.test_shared_documents import documents, headers


def peer_reference(text):
    index = text.index('@peer')
    start = len(text[:index].encode('utf-16-le')) // 2
    return {'kind': 'person', 'id': 'peer', 'start': start, 'end': start + 5}


def test_stack_discussion_reuses_encrypted_comment_references_without_a_new_write_path(organized):
    d = organized
    text = '😀 Ask @peer about the brief'
    refs = [peer_reference(text)]
    path = '/channels/room/shared-documents/brief/comments'
    payload = {'id': 'bound-comment', 'text': text, 'references': refs}
    for _ in range(2):
        posted = d.client.post(path, json=payload, headers=headers())
        assert posted.status_code == 201, posted.text
        assert posted.json()['fields'] == {'text': text, 'references': refs}
    comments = d.client.get(PATH, headers=headers()).json()['stack_comments']
    assert len(comments) == 1 and comments[0]['references'] == refs and comments[0]['stack_id'] is None
    assert stack(d).status_code == 201 and move(d).status_code == 200
    moved = d.client.get(PATH, headers=headers()).json()['stack_comments'][0]
    assert moved['references'] == refs and moved['stack_id'] == 'materials'
    with Session(d.engine) as session:
        row = session.get(SharedDocumentComment, 'bound-comment')
        assert text not in row.encrypted_payload and 'references' not in row.encrypted_payload
        session.delete(session.get(ChannelMember, ('room', 'peer'))); session.commit()
    retained = d.client.get(PATH, headers=headers()).json()['stack_comments'][0]
    assert retained['references'] == refs  # Historical identity is not a fresh grant.
    assert d.client.get(PATH, headers=headers('peer')).status_code == 403
    assert d.client.get(PATH, headers=headers('outsider')).status_code == 403


@pytest.mark.parametrize('references', [
    [{'kind': 'person', 'id': 'peer', 'start': 1, 'end': 8}],
    [{'kind': 'person', 'id': 'peer', 'start': 3, 'end': 8, 'label': 'spoof'}],
    [{'kind': 'person', 'id': 'peer', 'start': 3, 'end': 8}] * 2,
    None,
])
def test_organization_rejects_invalid_encrypted_comment_reference_ranges(organized, references):
    d = organized
    path = '/channels/room/shared-documents/brief/comments'
    assert d.client.post(path, json={'id': 'corrupt', 'text': 'Original text'}, headers=headers()).status_code == 201
    with Session(d.engine) as session:
        row = session.get(SharedDocumentComment, 'corrupt')
        row.encrypted_payload = service.seal('room', 'comment', 'brief', row.id,
            {'text': '😀 @peer', 'references': references})
        session.add(row); session.commit()
    response = d.client.get(PATH, headers=headers())
    assert response.status_code == 409 and 'could not be verified' in response.text


def test_organization_legacy_comment_payload_omits_empty_reference_metadata(organized):
    d = organized
    response = d.client.post('/channels/room/shared-documents/brief/comments', headers=headers(),
        json={'id': 'legacy', 'text': 'Legacy comment', 'references': []})
    assert response.status_code == 201
    assert response.json()['fields'] == {'text': 'Legacy comment'}
    comment = d.client.get(PATH, headers=headers()).json()['stack_comments'][0]
    assert comment['text'] == 'Legacy comment' and 'references' not in comment
