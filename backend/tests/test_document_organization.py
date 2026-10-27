"""Real native organization: scoped encryption, idempotency, CAS moves and links."""
from datetime import datetime, timedelta
import pytest
from fastapi import HTTPException
from sqlmodel import Session, select
from app.api import document_organization as org
from app.models.document_organization import SharedDocumentStack, SharedDocumentPlacement, SharedDocumentRelation
from app.models.channel import ChannelMember
from app.models.shared_documents import SharedDocument
from app.models.secure_channel import SecureChannel
from app.services.auth import create_token, revoke_token
from tests.test_shared_documents import documents, create, headers, EMPTY

PATH = '/channels/room/shared-document-organization'

@pytest.fixture
def organized(documents):
    documents.client.app.include_router(org.router)
    for identifier in ('brief', 'notes', 'sketch'): assert create(documents, identifier, title='Private '+identifier).status_code == 201
    return documents

def stack(d, identifier='materials', title='Private materials', actor='member'):
    return d.client.post(PATH+'/stacks', json={'id':identifier,'title':title}, headers=headers(actor))

def generation(d, identifier='materials'):
    with Session(d.engine) as session:
        row=session.get(SharedDocumentStack,identifier)
        return row.generation if row else '0'*32

def stack_url(d,version=1,identifier='materials',expected_generation=None):
    return PATH+'/stacks/'+identifier+'?expected_version='+str(version)+'&expected_generation='+(expected_generation or generation(d,identifier))

def move(d, doc='brief', destination='materials', version=0, actor='member'):
    return d.client.put(PATH+'/documents/'+doc+'/placement', json={'stack_id':destination,'expected_version':version,'stack_generation':generation(d,destination) if destination else None},headers=headers(actor))

def relate(d, identifier='relation', left='brief', right='notes', actor='member'):
    return d.client.post(PATH+'/relations', json={'id':identifier,'document_id':left,'related_document_id':right},headers=headers(actor))


def test_stack_create_retry_encryption_rename_permissions_case_only_and_delete(organized):
    d=organized
    assert stack(d).status_code==201
    assert stack(d).json()['version']==1
    assert stack(d,title='Changed').status_code==409
    assert stack(d,identifier='other',title='PRIVATE MATERIALS').status_code==409
    with Session(d.engine) as session:
        assert 'Private materials' not in session.get(SharedDocumentStack,'materials').encrypted_payload
    assert d.client.patch(PATH+'/stacks/materials',json={'title':'PRIVATE MATERIALS','expected_version':1,'expected_generation':generation(d)},headers=headers('peer')).status_code==403
    renamed=d.client.patch(PATH+'/stacks/materials',json={'title':'PRIVATE MATERIALS','expected_version':1,'expected_generation':generation(d)},headers=headers()).json()
    assert renamed['title']=='PRIVATE MATERIALS' and renamed['version']==2
    assert d.client.delete(stack_url(d,1),headers=headers()).status_code==409
    assert d.client.delete(stack_url(d,2),headers=headers('owner')).status_code==204
    assert d.client.get(PATH,headers=headers()).json()['stacks']==[]


def test_move_and_guarded_undo_preserve_later_crdt_edits_and_reject_another_move(organized):
    d=organized; assert stack(d).status_code==201
    assert move(d).json()['version']==1
    # Text edits are independent of placement version: Undo must never restore
    # an old document snapshot or reject a legitimate newer Yjs update.
    response=d.client.post('/channels/room/shared-documents/brief/updates',json={'update_id':'later','update_base64':EMPTY,'title':'Edited after move'},headers=headers('peer'))
    assert response.status_code==200
    assert move(d,destination=None,version=1).json()['version']==2
    assert d.client.get('/channels/room/shared-documents',headers=headers()).json()[0]['title']=='Edited after move'
    assert move(d,version=2).json()['version']==3
    assert move(d,destination=None,version=2).status_code==409
    with Session(d.engine) as session:
        assert session.get(SharedDocumentPlacement,'brief').stack_id=='materials'
        assert session.get(SharedDocument,'brief').update_count==2


def test_stack_name_undo_compensates_only_its_own_revision(organized):
    d=organized; original=stack(d).json()
    endpoint=PATH+'/stacks/'+original['id']
    renamed=d.client.patch(endpoint,json={'title':'Research materials','expected_version':original['version'],
        'expected_generation':original['generation']},headers=headers()).json()
    undo={'title':original['title'],'expected_version':renamed['version'],'expected_generation':renamed['generation']}
    restored=d.client.patch(endpoint,json=undo,headers=headers())
    assert restored.status_code==200 and restored.json()['title']==original['title']
    current=restored.json()
    later=d.client.patch(endpoint,json={'title':'A colleague’s current name','expected_version':current['version'],
        'expected_generation':current['generation']},headers=headers('owner'))
    assert later.status_code==200
    assert d.client.patch(endpoint,json=undo,headers=headers()).status_code==409
    assert d.client.get(PATH,headers=headers()).json()['stacks'][0]['title']=='A colleague’s current name'


@pytest.mark.parametrize('later_change',['renamed','populated','membership_revoked'])
def test_new_stack_undo_keeps_later_work_and_rechecks_current_permission(organized,later_change):
    d=organized; created=stack(d).json()
    if later_change=='renamed':
        assert d.client.patch(PATH+'/stacks/materials',json={'title':'Current group','expected_version':created['version'],
            'expected_generation':created['generation']},headers=headers()).status_code==200
    elif later_change=='populated':
        assert move(d,actor='peer').status_code==200
    else:
        with Session(d.engine) as session:
            session.delete(session.get(ChannelMember,('room','member')));session.commit()
    undone=d.client.delete(stack_url(d,created['version'],expected_generation=created['generation']),headers=headers())
    assert undone.status_code==(403 if later_change=='membership_revoked' else 409)
    with Session(d.engine) as session:
        assert session.get(SharedDocumentStack,'materials') is not None
        if later_change=='populated': assert session.get(SharedDocumentPlacement,'brief').stack_id=='materials'


def test_nonempty_stack_cannot_delete_even_when_its_document_is_archived(organized):
    d=organized; stack(d); move(d)
    assert d.client.delete(stack_url(d,1),headers=headers()).status_code==409
    assert d.client.patch('/channels/room/shared-documents/brief',json={'expected_version':1,'archived':True},headers=headers()).status_code==200
    assert d.client.get(PATH,headers=headers()).json()['placements']==[]
    archived=d.client.get(PATH+'?archived=true',headers=headers()).json()
    assert archived['placements'][0]['document_id']=='brief'
    assert d.client.delete(stack_url(d,1),headers=headers()).status_code==409
    assert move(d,destination=None,version=1).status_code==409


def test_relations_are_explicit_canonical_idempotent_and_filtered_with_archive(organized):
    d=organized
    first=relate(d); assert first.status_code==201
    assert relate(d,left='notes',right='brief').json()['id']=='relation'
    assert relate(d,identifier='duplicate',left='notes',right='brief').status_code==409
    assert relate(d,identifier='self',right='brief').status_code==422
    assert relate(d,left='brief',right='sketch').status_code==409
    assert relate(d,identifier='spoof',actor='peer').status_code==409
    assert len(d.client.get(PATH,headers=headers()).json()['relations'])==1
    d.client.patch('/channels/room/shared-documents/brief',json={'expected_version':1,'archived':True},headers=headers())
    assert d.client.get(PATH,headers=headers()).json()['relations']==[]
    assert d.client.delete(PATH+'/relations/relation',headers=headers()).status_code==409
    d.client.patch('/channels/room/shared-documents/brief',json={'expected_version':2,'archived':False},headers=headers())
    assert d.client.delete(PATH+'/relations/relation',headers=headers('peer')).status_code==204
    assert d.client.get(PATH,headers=headers()).json()['relations']==[]


def test_current_channel_membership_and_cross_channel_ids_are_required(organized):
    d=organized; stack(d)
    assert d.client.get(PATH,headers=headers('outsider')).status_code==403
    other='/channels/other/shared-document-organization'
    assert d.client.post(other+'/stacks',json={'id':'foreign-stack','title':'Foreign'},headers=headers('outsider')).status_code==201
    assert d.client.post('/channels/other/shared-documents',json={'id':'foreign-doc','update_id':'first','update_base64':EMPTY},headers=headers('outsider')).status_code==201
    assert move(d,destination='foreign-stack').status_code==404
    assert move(d,doc='foreign-doc').status_code==404
    assert relate(d,right='foreign-doc').status_code==404
    assert d.client.post(other+'/stacks',json={'id':'materials','title':'Foreign'},headers=headers('outsider')).status_code==404
    with Session(d.engine) as session:
        session.delete(session.get(ChannelMember,('room','member')));session.commit()
    assert move(d).status_code==403
    assert relate(d).status_code==403
    assert d.client.get(PATH,headers=headers()).status_code==403


@pytest.mark.parametrize('status',['preparing','active'])
def test_secure_organization_is_explicitly_unsupported_without_storing_plaintext(organized,status):
    d=organized
    with Session(d.engine) as session:session.add(SecureChannel(channel_id='room',created_by='owner',status=status));session.commit()
    for result in [d.client.get(PATH,headers=headers()),stack(d),move(d),relate(d)]:assert result.status_code==409
    with Session(d.engine) as session:
        assert session.exec(select(SharedDocumentStack)).all()==[]
        assert session.exec(select(SharedDocumentPlacement)).all()==[]
        assert session.exec(select(SharedDocumentRelation)).all()==[]


def test_rechecks_revoked_session_after_writer_fence_before_any_mutation(organized,monkeypatch):
    d=organized; original=org.service.lock_channel_writes; token=create_token('member')
    def revoke(session,channel):
        original(session,channel);revoke_token(token,session);session.commit()
    monkeypatch.setattr(org.service,'lock_channel_writes',revoke)
    result=d.client.post(PATH+'/stacks',json={'id':'blocked','title':'Never stored'},headers={'Authorization':'Bearer '+token})
    assert result.status_code==401
    with Session(d.engine) as session:assert session.get(SharedDocumentStack,'blocked') is None


def test_title_input_bounds_and_retained_stack_limit(organized,monkeypatch):
    d=organized
    for title in (' '*3,'x'*101):assert stack(d,title=title).status_code==422
    monkeypatch.setattr(org,'MAX_STACKS',1)
    assert stack(d).status_code==201
    assert stack(d,identifier='second',title='Second').status_code==409
    assert stack(d).status_code==201
    monkeypatch.setattr(org,'MAX_RELATIONS',1)
    assert relate(d).status_code==201
    assert relate(d,identifier='second',right='sketch').status_code==409
    assert relate(d).status_code==201

# These exercise the production export/purge worker with SQLite FK enforcement,
# not the transport fixture's permissive in-memory SQLite connection.
from tests.test_data_controls import controls, post, enable, preview, queue_purge, OLD
from app.models.channel_metadata import ChannelMetadata
from app.models.data_control import DataControlJob
from app.services import data_control as data_service
from app.services import channel_metadata as metadata_service
import io
import json
import zipfile


def organization_content(engine, *, recent_relation=False):
    with Session(engine) as session:
        session.add_all([SharedDocument(id=name,channel_id='native',created_by='owner',created_at=OLD,updated_at=OLD) for name in ['retained-brief','retained-notes']]);session.flush()
        session.add(SharedDocumentStack(id='retained-stack',channel_id='native',created_by='owner',created_at=OLD,updated_at=OLD,generation='a'*32,
            encrypted_payload=org.service.seal('native','stack','retained-stack','a'*32,{'title':'Private stack export'})));session.flush()
        session.add(SharedDocumentPlacement(document_id='retained-brief',channel_id='native',stack_id='retained-stack',updated_by='owner',updated_at=OLD))
        session.add(SharedDocumentRelation(id='retained-link',channel_id='native',first_document_id='retained-brief',second_document_id='retained-notes',created_by='owner',created_at=datetime.utcnow() if recent_relation else OLD))
        session.add(ChannelMetadata(channel_id='native',version=1,content_mode='server',created_by='owner',updated_by='owner',created_at=OLD,updated_at=OLD,
            encrypted_payload=metadata_service.seal_payload('native',1,{'name':'Private metadata export','description':'Verified description'})))
        session.add(ChannelMetadata(channel_id='secure',version=1,content_mode='matrix',created_by='owner',updated_by='owner',matrix_event_id='$opaque-only'))
        session.commit()


def test_owner_export_opens_verified_stack_and_channel_titles_and_keeps_secure_metadata_opaque(controls):
    _,engine,_,_=controls;organization_content(engine)
    queued=post(controls,'/exports',{'channel_ids':['native','secure'],'acknowledge_plaintext_export':True}).json()
    with Session(engine) as session:assert data_service.run_pending(session)==1
    downloaded=post(controls,'/exports/'+queued['id']+'/download');assert downloaded.status_code==200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:data=json.loads(archive.read('export.json'))
    assert data['shareddocumentstack'][0]['fields']=={'title':'Private stack export'}
    assert data['shareddocumentplacement'][0]['document_id']=='retained-brief'
    assert data['shareddocumentrelation'][0]['second_document_id']=='retained-notes'
    native=next(row for row in data['channelmetadata'] if row['channel_id']=='native')
    secure=next(row for row in data['channelmetadata'] if row['channel_id']=='secure')
    assert native['fields']=={'name':'Private metadata export','description':'Verified description'}
    assert secure['matrix_event_id']=='$opaque-only' and 'fields' not in secure
    assert 'encrypted_payload' not in json.dumps(data)


def test_retention_erases_complete_old_organization_without_foreign_key_orphans(controls):
    _,engine,_,_=controls;organization_content(engine);enable(controls)
    dry=preview(controls)
    for name in ['shareddocumentstack','shareddocumentplacement','shareddocumentrelation']:assert dry['counts'][name]>=1
    assert queue_purge(controls,dry).status_code==202
    with Session(engine) as session:
        assert data_service.run_pending(session)==1
        assert session.get(SharedDocumentStack,'retained-stack') is None
        assert session.get(SharedDocumentPlacement,'retained-brief') is None
        assert session.get(SharedDocumentRelation,'retained-link') is None
        assert session.get(SharedDocument,'retained-brief') is None
        assert session.get(ChannelMetadata,'native').version==1
        assert session.get(ChannelMetadata,'secure') is not None


def test_recent_saved_relation_protects_both_document_histories_and_their_stack(controls):
    _,engine,_,_=controls;organization_content(engine,recent_relation=True);enable(controls)
    dry=preview(controls)
    for name in ['shareddocument','shareddocumentstack','shareddocumentplacement','shareddocumentrelation']:assert name not in dry['counts']


def test_export_rejects_unverified_stack_payload_instead_of_dumping_ciphertext(controls):
    _,engine,_,_=controls;organization_content(engine)
    with Session(engine) as session:
        stack=session.get(SharedDocumentStack,'retained-stack')
        stack.encrypted_payload=org.service.seal('native','stack','different-stack','different-stack',{'title':'Wrong context'})
        session.add(stack);session.commit()
    queued=post(controls,'/exports',{'channel_ids':['native'],'acknowledge_plaintext_export':True}).json()
    with Session(engine) as session:
        assert data_service.run_pending(session)==0
        assert session.get(DataControlJob,queued['id']).status=='failed'
    assert not data_service._artifact_path(queued['id']).exists()


def test_recreated_stack_id_cannot_accept_stale_rename_delete_move_or_undo(organized):
    d=organized; assert stack(d).status_code==201
    old_generation=generation(d)
    assert move(d).status_code==200
    assert move(d,destination=None,version=1).status_code==200
    assert d.client.delete(stack_url(d,1),headers=headers()).status_code==204
    replacement=stack(d,title='Replacement stack').json();new_generation=replacement['generation']
    assert new_generation!=old_generation and replacement['version']==1
    rename={'title':'Stale rename','expected_version':1,'expected_generation':old_generation}
    assert d.client.patch(PATH+'/stacks/materials',json=rename,headers=headers()).status_code==409
    assert d.client.delete(stack_url(d,1,expected_generation=old_generation),headers=headers()).status_code==409
    # The document's placement version is still current. Only the destination
    # generation detects that an old Undo points at a different stack entity.
    stale_undo={'stack_id':'materials','stack_generation':old_generation,'expected_version':2}
    assert d.client.put(PATH+'/documents/brief/placement',json=stale_undo,headers=headers()).status_code==409
    assert move(d,version=2).status_code==200
    assert d.client.patch(PATH+'/stacks/materials',json={**rename,'title':'Current rename','expected_generation':new_generation},headers=headers()).status_code==200
    with Session(d.engine) as session:assert session.get(SharedDocumentPlacement,'brief').version==3

from app.models.message import Message
from app.models.shared_documents import SharedDocumentComment
from app.models.document_organization import SharedDocumentStackReference
from app.services.crypto import encrypt_message


def source_message(d,identifier='source',text='Reviewed native research',channel='room',parent=None):
    with Session(d.engine) as session:
        session.add(Message(id=identifier,channel_id=channel,author_id='member' if channel=='room' else 'outsider',content=encrypt_message(text,channel),parent_id=parent));session.commit()
    return d.client.get(PATH+'/message-preview/'+identifier,headers=headers())


def save_source(d,preview,identifier='saved-source',stack_id='materials',**changes):
    return d.client.post(PATH+'/stacks/'+stack_id+'/message-references',headers=headers(),json={'id':identifier,'message_id':preview['id'],
        'expected_text_sha256':preview['expected_text_sha256'],'stack_generation':generation(d,stack_id),**changes})


def test_saved_message_reference_is_exact_reviewed_routing_not_a_text_copy_and_rechecks_edits(organized):
    d=organized;stack(d);review=source_message(d).json()
    assert review['text']=='Reviewed native research'
    assert save_source(d,review).status_code==201
    assert save_source(d,review).status_code==201
    assert save_source(d,review,identifier='duplicate').status_code==409
    with Session(d.engine) as session:
        row=session.get(SharedDocumentStackReference,'saved-source')
        assert set(row.model_dump())=={'id','channel_id','stack_id','message_id','created_by','created_at'}
        message=session.get(Message,'source');message.content=encrypt_message('Changed current source','room');message.edited_at=datetime.utcnow();session.add(message);session.commit()
    assert save_source(d,review,identifier='stale').status_code==409
    data=d.client.get(PATH,headers=headers()).json();assert data['message_references'][0]['excerpt']=='Changed current source'
    assert d.client.delete(stack_url(d),headers=headers()).status_code==409
    with Session(d.engine) as session:
        message=session.get(Message,'source');message.deleted=True;session.add(message);session.commit()
    unavailable=d.client.get(PATH,headers=headers()).json()['message_references'][0]
    assert not unavailable['available'] and unavailable['excerpt'] is None and 'author_id' not in unavailable
    assert d.client.delete(PATH+'/message-references/saved-source',headers=headers('peer')).status_code==204
    assert d.client.delete(stack_url(d),headers=headers()).status_code==204


def test_message_preview_and_reference_guard_channel_parent_generation_limits_and_secure_mode(organized,monkeypatch):
    d=organized;stack(d);review=source_message(d).json()
    assert source_message(d,'foreign-source',channel='other').status_code==404
    assert d.client.get(PATH+'/message-preview/source',headers=headers('outsider')).status_code==403
    assert save_source(d,review,stack_generation='0'*32).status_code==409
    monkeypatch.setattr(org,'MAX_REFERENCES',1)
    assert save_source(d,review).status_code==201
    other=source_message(d,'second-source').json();assert save_source(d,other,identifier='second').status_code==409
    parent=source_message(d,'parent');assert parent.status_code==200
    reply=source_message(d,'reply',parent='parent');assert reply.status_code==200
    with Session(d.engine) as session:
        row=session.get(Message,'parent');row.deleted=True;session.add(row);session.commit()
    assert d.client.get(PATH+'/message-preview/reply',headers=headers()).status_code==404
    with Session(d.engine) as session:session.add(SecureChannel(channel_id='room',created_by='owner',status='preparing'));session.commit()
    assert d.client.get(PATH+'/message-preview/source',headers=headers()).status_code==409
    assert save_source(d,review).status_code==409


def test_stack_discussion_is_derived_from_current_placement_and_archive_view(organized):
    d=organized;stack(d)
    comment=d.client.post('/channels/room/shared-documents/brief/comments',json={'id':'original-comment','text':'Budget evidence belongs here'},headers=headers('peer'));assert comment.status_code==201
    initial=d.client.get(PATH,headers=headers()).json()['stack_comments']
    assert initial[0]['stack_id'] is None and initial[0]['document_id']=='brief'
    assert move(d).status_code==200
    moved=d.client.get(PATH,headers=headers()).json()['stack_comments']
    assert moved[0]['stack_id']=='materials' and moved[0]['id']=='original-comment' and moved[0]['text']==initial[0]['text']
    d.client.patch('/channels/room/shared-documents/brief',json={'expected_version':1,'archived':True},headers=headers())
    assert d.client.get(PATH,headers=headers()).json()['stack_comments']==[]
    assert d.client.get(PATH+'?archived=true',headers=headers()).json()['stack_comments'][0]['id']=='original-comment'
    assert d.client.get(PATH,headers=headers('outsider')).status_code==403


def test_stack_discussion_discloses_bounded_partial_results(organized):
    d=organized
    with Session(d.engine) as session:
        for index in range(101):
            identifier='bounded-'+str(index);document='brief' if index<100 else 'notes'
            session.add(SharedDocumentComment(id=identifier,document_id=document,created_by='member',content_mode='server',payload_hash='fixture',
                encrypted_payload=org.service.seal('room','comment',document,identifier,{'text':'Bounded comment '+str(index)})))
        session.commit()
    data=d.client.get(PATH,headers=headers()).json();assert data['comments_incomplete'] and len(data['stack_comments'])==100


def test_saved_message_export_and_retention_remove_known_children_and_protect_recent_source_ancestors(controls):
    _,engine,_,_=controls;organization_content(engine)
    with Session(engine) as session:
        session.add(SharedDocumentStackReference(id='old-reference',channel_id='native',stack_id='retained-stack',message_id='old-reply',created_by='owner',created_at=OLD));session.commit()
    queued=post(controls,'/exports',{'channel_ids':['native'],'acknowledge_plaintext_export':True}).json()
    with Session(engine) as session:assert data_service.run_pending(session)==1
    downloaded=post(controls,'/exports/'+queued['id']+'/download')
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:data=json.loads(archive.read('export.json'))
    assert data['shareddocumentstackreference'][0]['message_id']=='old-reply' and 'excerpt' not in data['shareddocumentstackreference'][0]
    enable(controls);dry=preview(controls);assert dry['counts']['shareddocumentstackreference']==1
    assert queue_purge(controls,dry).status_code==202
    with Session(engine) as session:
        assert data_service.run_pending(session)==1
        assert session.get(SharedDocumentStackReference,'old-reference') is None
        assert session.get(SharedDocumentStack,'retained-stack') is None
    # A new routing reference protects an old reply and its root even though
    # neither old source text nor an excerpt is copied into the reference.
    with Session(engine) as session:
        session.add(Message(id='new-root',channel_id='native',author_id='owner',content=encrypt_message('Parent','native'),created_at=OLD));session.flush()
        session.add(Message(id='new-reply',channel_id='native',author_id='owner',parent_id='new-root',content=encrypt_message('Reply','native'),created_at=OLD));session.flush()
        session.add(SharedDocumentStack(id='new-stack',channel_id='native',created_by='owner',generation='b'*32,created_at=OLD,updated_at=OLD,encrypted_payload=org.service.seal('native','stack','new-stack','b'*32,{'title':'Retained source'})));session.flush()
        session.add(SharedDocumentStackReference(id='recent-reference',channel_id='native',stack_id='new-stack',message_id='new-reply',created_by='owner'));session.commit()
        plan=data_service.build_plan(session,'a','owner',['native'],datetime.utcnow()-timedelta(days=7))
        assert 'new-root' not in {row['id'] for row in plan.get('message',[])}
        assert 'new-reply' not in {row['id'] for row in plan.get('message',[])}
        assert 'new-stack' not in {row['id'] for row in plan.get('shareddocumentstack',[])}


def test_timed_purge_keeps_current_channel_metadata_revision_and_rejects_a_stale_editor(controls):
    from app.api import channel_metadata
    from tests.test_data_controls import headers as control_headers
    client,engine,_,_=controls;client.app.include_router(channel_metadata.router);organization_content(engine)
    with Session(engine) as session:
        row=session.get(ChannelMetadata,'native');row.version=2;row.encrypted_payload=metadata_service.seal_payload('native',2,{'name':'Current name','description':'Current profile'});session.add(row);session.commit()
    enable(controls);dry=preview(controls);assert queue_purge(controls,dry).status_code==202
    with Session(engine) as session:
        assert data_service.run_pending(session)==1
        assert session.get(ChannelMetadata,'native').version==2
    result=client.patch('/channels/native/metadata',headers=control_headers(controls),json={'expected_version':1,'name':'Stale editor','description':'Do not overwrite'})
    assert result.status_code==409
