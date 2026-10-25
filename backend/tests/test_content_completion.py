"""Real collection references, private-calendar ownership and latent retrieval."""
from datetime import datetime
import pytest
from sqlmodel import Session, select
from app.api import content_completion, document_organization, shared_documents
from app.models.content_completion import ChannelSemanticIndex, ContentCollectionItem, PersonalCalendarEvent
from app.models.channel import ChannelMember
from app.models.message import Message
from app.models.workspace import WorkspaceMember
from app.services import semantic_index
from app.services.content_completion import export_personal_calendar, erase_personal_calendar
from tests.test_project_tasks import board, headers, project, task


@pytest.fixture
def content(board):
    for router in (content_completion.router, document_organization.router, shared_documents.router):
        board.client.app.include_router(router)
    return board


EVENT = {'title': 'A private appointment', 'description': 'Private notes', 'location': 'Private room',
    'starts_at': '2026-11-02T09:00:00Z', 'ends_at': '2026-11-02T10:00:00Z', 'timezone': 'Europe/Stockholm',
    'all_day': False, 'external_uid': None, 'reminder_minutes': None}


def test_private_calendar_is_owned_encrypted_and_revision_guarded(content):
    d = content; path = '/workspaces/wa/personal-calendar'
    created = d.client.post(path, headers=headers(d), json={'id': 'private-one', 'fields': EVENT})
    assert created.status_code == 200, created.text
    assert created.json()['revision'] == 1
    assert d.client.get(path, headers=headers(d, 'peer')).json() == []
    assert d.client.post(path, headers=headers(d, 'peer'), json={'id': 'private-one', 'expected_revision': 1, 'fields': EVENT}).status_code == 404
    assert d.client.delete(path + '/private-one?expected_revision=1', headers=headers(d, 'peer')).status_code == 404
    assert d.client.post(path, headers=headers(d), json={'id': 'private-one', 'fields': EVENT}).json()['revision'] == 1
    changed = {**EVENT, 'title': 'A later private appointment'}
    assert d.client.post(path, headers=headers(d), json={'id': 'private-one', 'expected_revision': 0, 'fields': changed}).status_code == 409
    saved = d.client.post(path, headers=headers(d), json={'id': 'private-one', 'expected_revision': 1, 'fields': changed})
    assert saved.status_code == 200 and saved.json()['revision'] == 2
    assert d.client.delete(path + '/private-one?expected_revision=1', headers=headers(d)).status_code == 409
    with Session(d.engine) as session:
        row = session.get(PersonalCalendarEvent, 'private-one')
        assert EVENT['description'] not in row.encrypted_payload and changed['title'] not in row.encrypted_payload
    assert d.client.delete(path + '/private-one?expected_revision=2', headers=headers(d)).status_code == 204
    assert d.client.get(path, headers=headers(d)).json() == []


def test_private_calendar_rejects_foreign_workspace_and_invalid_dates(content):
    d = content
    assert d.client.post('/workspaces/wb/personal-calendar', headers=headers(d), json={'fields': EVENT}).status_code == 403
    assert d.client.post('/workspaces/wa/personal-calendar', headers=headers(d), json={'fields': {**EVENT, 'ends_at': EVENT['starts_at']}}).status_code == 422
    with Session(d.engine) as session:
        membership = session.get(WorkspaceMember, ('wa', 'owner')); session.delete(membership); session.commit()
    assert d.client.get('/workspaces/wa/personal-calendar', headers=headers(d)).status_code == 403


def test_collection_atomic_create_validates_real_current_items_and_cas(content):
    d = content; project(d); task(d)
    path = '/channels/a/content-collections'
    body = {'id': 'all-material', 'title': 'Materials', 'items': [{'kind': 'file', 'id': 'file-a'}, {'kind': 'task', 'id': 'task-a', 'parent_id': 'project-a'}]}
    created = d.client.post(path, headers=headers(d), json=body)
    assert created.status_code == 201, created.text
    row = created.json(); assert len(row['items']) == 2
    assert d.client.post(path, headers=headers(d), json=body).json()['generation'] == row['generation']
    assert d.client.post(path, headers=headers(d), json={**body, 'id': 'foreign', 'title': 'Foreign material', 'items': [{'kind': 'file', 'id': 'file-b'}]}).status_code == 404
    with Session(d.engine) as session:
        assert session.exec(select(ContentCollectionItem).where(ContentCollectionItem.stack_id == 'foreign')).all() == []
    change = {'expected_version': row['version'], 'generation': row['generation'], 'item': {'kind': 'project', 'id': 'project-a'}}
    updated = d.client.post(path + '/all-material/items', headers=headers(d, 'peer'), json=change)
    assert updated.status_code == 200 and updated.json()['version'] == 2
    assert d.client.request('DELETE', path + '/all-material/items', headers=headers(d), json=change).status_code == 409
    version = updated.json()
    assert d.client.delete('/channels/a/shared-document-organization/stacks/all-material?expected_version=2&expected_generation=' + row['generation'], headers=headers(d)).status_code == 409
    change.update(expected_version=2)
    removed = d.client.request('DELETE', path + '/all-material/items', headers=headers(d), json=change)
    assert removed.status_code == 200 and len(removed.json()['items']) == 2


def test_collection_member_revocation_blocks_reads_and_writes(content):
    d = content
    created = d.client.post('/channels/a/content-collections', headers=headers(d), json={'title': 'Private', 'items': [{'kind': 'file', 'id': 'file-a'}]})
    assert created.status_code == 201, created.text
    with Session(d.engine) as session:
        member = session.get(ChannelMember, ('a', 'owner')); session.delete(member); session.commit()
    assert d.client.get('/channels/a/content-collections', headers=headers(d)).status_code == 403


def test_latent_projection_finds_indirect_context_without_literal_query_match():
    texts = ['cat pet kitten', 'dog pet puppy', 'cat pet home', 'dog pet home', 'railway train station', 'train station travel']
    items = [{'id': str(index), 'kind': 'message', 'title': '', 'text': text} for index, text in enumerate(texts)]
    results = semantic_index.rank_items(items, semantic_index.latent_model(items), 'kitten')
    assert any(row['text'] == 'cat pet home' and row['score'] > 0 for row in results)
    assert all('train' not in row['text'] for row in results)


def test_semantic_index_persists_encrypted_reuses_unchanged_sources_and_prunes_deleted(content, monkeypatch):
    d = content
    with Session(d.engine) as session:
        for index, text in enumerate(['cat pet kitten', 'cat pet home', 'railway train station', 'train station travel']):
            session.add(Message(id='meaning-' + str(index), channel_id='a', author_id='owner', content=text, encrypted=False))
        session.commit()
    path = '/workspaces/wa/semantic-search'; query = {'query': 'kitten', 'channel_ids': ['a'], 'limit': 10}
    first = d.client.post(path, headers=headers(d), json=query)
    assert first.status_code == 200, first.text
    assert first.json()['engine'] == 'local-lsa-v1'
    assert any(row['id'] == 'meaning-1' for row in first.json()['items'])
    with Session(d.engine) as session:
        index = session.get(ChannelSemanticIndex, 'a'); assert 'kitten' not in index.encrypted_payload
        initial = index.revision
    def fail_extract(*args):
        pytest.fail('unchanged message was re-extracted instead of using its revision')
    original = semantic_index.search_sources.source
    def guarded(session, channel, kind, row):
        if kind == 'message': fail_extract()
        return original(session, channel, kind, row)
    monkeypatch.setattr(semantic_index.search_sources, 'source', guarded)
    assert d.client.post(path, headers=headers(d), json=query).status_code == 200
    with Session(d.engine) as session:
        assert session.get(ChannelSemanticIndex, 'a').revision == initial
        message = session.get(Message, 'meaning-1'); session.delete(message); session.commit()
    monkeypatch.setattr(semantic_index.search_sources, 'source', original)
    latest = d.client.post(path, headers=headers(d), json=query)
    assert latest.status_code == 200 and all(item['id'] != 'meaning-1' for item in latest.json()['items'])
    with Session(d.engine) as session:
        assert session.get(ChannelSemanticIndex, 'a').revision > initial
        member = session.get(ChannelMember, ('a', 'owner')); session.delete(member); session.commit()
    assert d.client.post(path, headers=headers(d), json=query).status_code == 403


def test_private_export_and_erasure_are_transaction_neutral_and_owner_scoped(content):
    d = content
    for who in ['owner', 'peer']:
        assert d.client.post('/workspaces/wa/personal-calendar', headers=headers(d, who), json={'id': who + '-private', 'fields': EVENT}).status_code == 200
    with Session(d.engine) as session:
        session.add(WorkspaceMember(workspace_id='wb',user_id='owner',role='member'));session.commit()
    assert d.client.post('/workspaces/wb/personal-calendar',headers=headers(d),json={'id':'owner-other-workspace','fields':EVENT}).status_code==200
    with Session(d.engine) as session:
        assert [row['id'] for row in export_personal_calendar(session, 'wa', 'owner')] == ['owner-private']
        with pytest.raises(Exception): export_personal_calendar(session,'missing-workspace','owner')
        assert erase_personal_calendar(session, 'owner') == 2
        session.rollback()
        assert session.get(PersonalCalendarEvent, 'owner-private') is not None
        assert erase_personal_calendar(session, 'owner') == 2
        session.commit()
        assert session.get(PersonalCalendarEvent, 'owner-private') is None
        assert session.get(PersonalCalendarEvent,'owner-other-workspace') is None
        assert session.get(PersonalCalendarEvent, 'peer-private') is not None


def test_calendar_context_requires_current_same_workspace_channel(content):
    d = content; path = '/workspaces/wa/personal-calendar'
    for context in ['b', 'missing']:
        assert d.client.post(path, headers=headers(d), json={'fields': EVENT, 'context_channel_id': context}).status_code == 403
    assert d.client.post(path, headers=headers(d, 'outsider'), json={'fields': EVENT, 'context_channel_id': 'a'}).status_code == 403
    assert d.client.post(path, headers=headers(d), json={'id':'context','fields':EVENT,'context_channel_id':'a'}).status_code == 200
    with Session(d.engine) as session:
        session.delete(session.get(ChannelMember, ('a', 'owner'))); session.commit()
    saved = d.client.post(path, headers=headers(d), json={'id':'context','expected_revision':1,'fields':{**EVENT,'title':'Own updated appointment'},'context_channel_id':'a'})
    assert saved.status_code == 200, saved.text


def test_canonical_source_rechecks_edits_and_preserves_caller_transaction(content):
    from app.services import search_sources
    from app.services.account_security import seal_private
    import json
    d = content
    with Session(d.engine) as session:
        row = Message(id='canonical',channel_id='a',author_id='owner',content='Reviewed message',encrypted=False)
        session.add(row);session.commit(); source=search_sources.source(session,'a','message',row);citation=search_sources.reference('owner',source)
        session.add(Message(id='uncommitted',channel_id='a',author_id='owner',content='Caller transaction',encrypted=False))
        assert search_sources.resolve(session,'owner',citation,commit=False)['version']==source['version']
        session.rollback();assert session.get(Message,'uncommitted') is None
        expired=seal_private(json.dumps({'actor':'owner','channel':'a','kind':'message','id':'canonical','version':source['version'],'expires':0}),'enterprise-search-source','owner')
        with pytest.raises(Exception) as expired_error:search_sources.resolve(session,'owner',expired,commit=False)
        assert expired_error.value.status_code==404
        assert search_sources.resolve_canonical(session,'owner','message','canonical','a',source['version'])['text']=='Reviewed message'
        row=session.get(Message,'canonical');row.content='Changed after review';row.edited_at=datetime.utcnow();session.add(row);session.commit()
        with pytest.raises(Exception) as stale:search_sources.resolve_canonical(session,'owner','message','canonical','a',source['version'])
        assert stale.value.status_code==409
        row.deleted=True;session.add(row);session.commit()
        with pytest.raises(Exception) as deleted:search_sources.resolve_canonical(session,'owner','message','canonical','a',source['version'])
        assert deleted.value.status_code==404


def test_semantic_cache_purged_before_durable_encryption_fence(content,monkeypatch):
    from app.api import secure_messaging
    from app.models.channel import Channel
    from app.models.workspace import WorkspaceChannel
    from app.models.secure_channel import SecureChannel
    from types import SimpleNamespace
    d=content
    with Session(d.engine) as session:
        session.add(Channel(id='empty-native',name='Empty',type='private'));session.commit()
        session.add(WorkspaceChannel(channel_id='empty-native',workspace_id='wa'));session.add(ChannelMember(channel_id='empty-native',user_id='owner',role='admin'))
        session.add(ChannelSemanticIndex(channel_id='empty-native',encrypted_payload='old native cache'));session.commit()
    monkeypatch.setattr(secure_messaging,'require_matrix',lambda:None)
    def unavailable(session,binding):raise RuntimeError('Synthetic room outage after durable fence')
    monkeypatch.setattr(secure_messaging,'create_secure_room',unavailable)
    with Session(d.engine) as session:
        with pytest.raises(RuntimeError):secure_messaging.enable_channel('empty-native',user=SimpleNamespace(id='owner'),session=session)
    with Session(d.engine) as session:
        assert session.get(ChannelSemanticIndex,'empty-native') is None
        assert session.get(SecureChannel,'empty-native').status=='preparing'


def test_comment_anchor_stays_encrypted_and_rejects_wrong_document_or_revision(content):
    from app.models.shared_documents import SharedDocumentComment
    d=content
    assert d.client.post('/channels/a/shared-documents',headers=headers(d),json={'id':'anchored','update_id':'first-anchor','update_base64':'AAA='}).status_code==201
    # Positions are opaque transport; the browser's actual Yjs decoder/resolver has separate real-CRDT regression coverage.
    anchor={'version':1,'document_id':'anchored','field':'body','start_relative':'AA==','end_relative':'AQ==','quote':'Private passage','source_sequence':1}
    path='/channels/a/shared-documents/anchored/comments'
    created=d.client.post(path,headers=headers(d),json={'id':'passage-comment','text':'Review grain','anchor':anchor})
    assert created.status_code==201,created.text
    assert created.json()['fields']['anchor']==anchor
    for invalid in [{**anchor,'document_id':'other'},{**anchor,'source_sequence':2}]:
        assert d.client.post(path,headers=headers(d),json={'id':'invalid','text':'Review','anchor':invalid}).status_code==409
    assert d.client.post(path,headers=headers(d),json={'id':'sidecar','matrix_event_id':'$opaque','anchor':anchor}).status_code==422
    with Session(d.engine) as session:
        stored=session.get(SharedDocumentComment,'passage-comment').encrypted_payload
        assert 'Private passage' not in stored and 'AA==' not in stored


def test_semantic_reply_source_denies_deleted_foreign_or_cyclic_parent_even_from_cached_index(content):
    from app.services import search_sources
    d=content
    with Session(d.engine) as session:
        parent=Message(id='parent',channel_id='a',author_id='owner',content='Parent',encrypted=False)
        session.add(parent);session.commit()
        child=Message(id='child',channel_id='a',author_id='owner',content='Child context',encrypted=False,parent_id='parent')
        session.add(child);session.commit();source=search_sources.source(session,'a','message',child)
        parent.deleted=True;session.add(parent);session.commit()
        assert not search_sources.still_current(session,source)
        with pytest.raises(Exception) as denied:search_sources.resolve_canonical(session,'owner','message','child','a',source['version'])
        assert denied.value.status_code==404
        parent.deleted=False;parent.channel_id='b';session.add(parent);session.commit()
        assert not search_sources.still_current(session,source)
        parent.channel_id='a';parent.parent_id='child';session.add(parent);session.commit()
        assert not search_sources.still_current(session,source)


from tests.test_data_controls import controls


def test_actual_private_event_insert_waits_for_http_account_erasure_fence(controls, monkeypatch):
    """Pause the real final INSERT; real account closure must wait, then erase it."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    from app.models.user import User
    from tests.test_data_controls import PASSWORD, headers as closure_headers
    client,engine,tokens,_=controls
    client.app.include_router(content_completion.router)
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member',lambda *_:None)
    preview=client.get('/account/data-closure',headers=closure_headers(controls)).json()
    assert preview['can_erase_identity']
    held,release,closure_attempt,closure_wrote=Event(),Event(),Event(),Event()
    def before(connection,cursor,statement,parameters,context,many):
        lowered=statement.lower()
        if lowered.startswith('insert into personalcalendarevent'):
            held.set();assert release.wait(5),'held private calendar insert was not released'
        elif held.is_set() and lowered.startswith('delete from accountratebucket'):
            closure_attempt.set()
    def after(connection,cursor,statement,parameters,context,many):
        if held.is_set() and statement.lower().startswith('delete from accountratebucket'):
            closure_wrote.set()
    event.listen(engine,'before_cursor_execute',before);event.listen(engine,'after_cursor_execute',after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            saving=pool.submit(client.post,'/workspaces/a/personal-calendar',headers=closure_headers(controls),json={'id':'fenced-private','fields':EVENT})
            assert held.wait(5)
            closing=pool.submit(client.post,'/account/data-closure',headers=closure_headers(controls),json={'password':PASSWORD,'confirmation':'ERASE MY ACCOUNT','preview_fingerprint':preview['fingerprint'],'acknowledge_retained_shared_authorship_and_backups':True})
            assert closure_attempt.wait(5)
            assert not closure_wrote.wait(.2),'Closure wrote through the held owner/calendar fence'
            release.set();saved=saving.result(timeout=10);closed=closing.result(timeout=10)
        assert saved.status_code==200,saved.text
        assert closed.status_code==200,closed.text
        with Session(engine) as session:
            assert session.get(User,'owner').is_blocked
            assert session.get(PersonalCalendarEvent,'fenced-private') is None
        denied=client.post('/workspaces/a/personal-calendar',headers=closure_headers(controls),json={'id':'after-closure','fields':EVENT})
        assert denied.status_code==401
    finally:
        release.set();event.remove(engine,'before_cursor_execute',before);event.remove(engine,'after_cursor_execute',after)


def test_semantic_cache_erasure_during_result_ranking_does_not_expire_response_metadata(content, monkeypatch):
    d=content
    with Session(d.engine) as session:
        session.add(Message(id='cache-independent',channel_id='a',author_id='owner',content='Meaningful retained source',encrypted=False));session.commit()
    rank=semantic_index.rank_items
    def erase_cache(items,model,query):
        result=rank(items,model,query)
        with Session(d.engine) as other:
            row=other.get(ChannelSemanticIndex,'a');assert row is not None;other.delete(row);other.commit()
        return result
    monkeypatch.setattr(semantic_index,'rank_items',erase_cache)
    result=d.client.post('/workspaces/wa/semantic-search',headers=headers(d),json={'query':'meaningful','channel_ids':['a']})
    assert result.status_code==200,result.text
    assert any(row['id']=='cache-independent' for row in result.json()['items'])
    assert result.json()['index_revision']['a']>=1
