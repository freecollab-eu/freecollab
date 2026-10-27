"""Actual authorized return summaries inspect revisions without updating receipts."""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import read_receipts
from app.api.decisions import DecisionContent, new_revision
from app.database import get_session
from app.models.channel import Channel, ChannelMember
from app.models.collaboration import Decision
from app.models.message import Message
from app.models.post import Post  # noqa: F401 -- additive table registration
from app.models.project_tasks import ProjectBoard, TaskCard
from app.models.read_receipt import ReadReceipt
from app.models.secure_channel import SecureChannel
from app.models.shared_documents import SharedDocument
from app.models.user import User
from app.services.auth import get_current_user
from app.services.project_tasks import SnapshotWrite, new_revision as task_revision
from app.services.shared_documents import seal


@pytest.fixture
def returning():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    before, after = datetime.utcnow() - timedelta(days=2), datetime.utcnow() - timedelta(days=1)
    with Session(engine) as session:
        session.add_all([User(id=name, name=name, email=name+'@example.com', password_hash='unused') for name in ['viewer', 'peer', 'outsider']])
        session.add_all([Channel(id='room', name='Room'), Channel(id='other', name='Other')]); session.commit()
        session.add_all([ChannelMember(channel_id='room', user_id=name) for name in ['viewer', 'peer']])
        session.add(ChannelMember(channel_id='other', user_id='outsider'))
        session.add(Message(id='read', channel_id='room', author_id='viewer', content='old', created_at=before))
        session.add(ReadReceipt(user_id='viewer', channel_id='room', last_read_message_id='read', read_at=before))
        session.commit()
    app = FastAPI(); app.include_router(read_receipts.router)
    actor = SimpleNamespace(id='viewer')
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_current_user] = lambda: actor
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, actor=actor, before=before, after=after,
                              path='/read-receipts/channels/room/return-summary', params={'since': before.isoformat()})
    engine.dispose()


def decision(session, identifier, at, title='A real decision'):
    row = Decision(id=identifier, channel_id='room', created_by='peer', created_at=at)
    session.add(row); session.flush()
    revision = new_revision(row, DecisionContent(title=title, content='Reviewed agreement', status='accepted'), 1, 'peer')
    revision.created_at = at; session.add(revision)
    return row


def test_current_native_decisions_documents_and_explicit_attention_without_mark_read(returning):
    r = returning
    with Session(r.engine) as session:
        row = decision(session, 'agreement', r.after)
        revision = new_revision(row, DecisionContent(title='Current title', content='Revised agreement'), 2, 'peer')
        revision.created_at = r.after + timedelta(seconds=1); session.add(revision)
        decision(session, 'old', r.before - timedelta(seconds=1))
        session.add(SharedDocument(id='doc', channel_id='room', created_by='peer', created_at=r.before,
            updated_at=r.after, summary_encrypted_payload=seal('room', 'summary', 'doc', 'doc', {'title': 'Updated plan'})))
        session.add(SharedDocument(id='archived', channel_id='room', created_by='peer', updated_at=r.after, archived=True))
        session.add_all([
            Message(id='mention', channel_id='room', author_id='peer', content='No punctuation', mentions=json.dumps(['viewer']), created_at=r.after),
            Message(id='reply', channel_id='room', author_id='peer', content='Confirmed', parent_id='read', created_at=r.after),
            Message(id='guess', channel_id='room', author_id='peer', content='viewer, can you help?', created_at=r.after),
            Message(id='own', channel_id='room', author_id='viewer', content='mine', mentions='["viewer"]', created_at=r.after),
            Message(id='deleted', channel_id='room', author_id='peer', content='deleted', mentions='["viewer"]', deleted=True, created_at=r.after),
            Message(id='other', channel_id='other', author_id='outsider', content='other', mentions='["viewer"]', created_at=r.after),
        ])
        # Ordinary traffic must not consume the explicit-attention scan budget.
        session.add_all([Message(id='ordinary-'+str(index), channel_id='room', author_id='peer',
            content='viewer, a question?', created_at=r.after+timedelta(seconds=1)) for index in range(205)])
        session.commit()
    response = r.client.get(r.path, params=r.params)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    data = response.json(); groups = data['groups']
    assert groups['decisions']['count'] == 1 and groups['decisions']['items'][0]['title'] == 'Current title'
    assert groups['documents']['count'] == 1 and groups['documents']['items'][0]['source'] == {'kind': 'document', 'id': 'doc'}
    assert groups['attention']['count'] == 2
    assert {row['reason'] for row in groups['attention']['items']} == {'mention', 'reply'}
    assert not data['incomplete']
    with Session(r.engine) as session:
        receipt = session.query(ReadReceipt).one()
        assert receipt.read_at == r.before and receipt.last_read_message_id == 'read'
        receipt.read_at = datetime.utcnow(); session.add(receipt); session.commit()
    # Chat may advance its receipt after taking the entry snapshot. Inspection
    # still uses the original supplied baseline and cannot erase that summary.
    assert r.client.get(r.path, params=r.params).json()['groups'] == groups


def test_authorization_native_fence_limits_and_invalid_baselines(returning, monkeypatch):
    r = returning
    r.actor.id = 'outsider'
    assert r.client.get(r.path, params=r.params).status_code == 403
    r.actor.id = 'viewer'
    assert r.client.get(r.path, params={'since': (datetime.utcnow()+timedelta(days=1)).isoformat()}).status_code == 422
    assert r.client.get(r.path, params={'since': 'not-a-time'}).status_code == 422
    from app.services import channel_return
    monkeypatch.setattr(channel_return, 'SCAN_LIMIT', 2)
    with Session(r.engine) as session:
        for index in range(4):
            decision(session, 'decision-'+str(index), r.after+timedelta(seconds=index))
        session.commit()
    result = r.client.get(r.path, params=r.params).json()
    assert result['groups']['decisions']['count'] == 2
    assert result['groups']['decisions']['incomplete'] and result['incomplete']
    with Session(r.engine) as session:
        session.add(SecureChannel(channel_id='room', created_by='viewer', status='preparing')); session.commit()
    assert r.client.get(r.path, params=r.params).status_code == 409


def test_only_current_open_assigned_tasks_enter_attention(returning):
    r = returning
    fields = {'title': 'Assigned task', 'description': '', 'status': 'todo', 'priority': 'normal',
              'assignee_user_id': 'viewer', 'due_date': None, 'checklist': [], 'file_ids': [], 'decision_ids': []}
    with Session(r.engine) as session:
        session.add(ProjectBoard(id='project', channel_id='room', created_by='peer', revision=1)); session.commit()
        for identifier, status, assignee in [('open', 'doing', 'viewer'), ('done', 'done', 'viewer'), ('peer', 'todo', 'peer')]:
            task = TaskCard(id=identifier, project_id='project', channel_id='room', created_by='peer', revision=0, updated_at=r.after)
            payload = {**fields, 'status': status, 'assignee_user_id': assignee}
            data = SnapshotWrite(id=identifier, expected_revision=0, fields=payload)
            revision = task_revision('task', task, data, payload, 'peer')
            task.revision = revision.revision
            session.add(task); session.add(revision)
        session.commit()
    response = r.client.get(r.path, params=r.params)
    assert response.status_code == 200, response.text
    attention = response.json()['groups']['attention']
    assert attention['count'] == 1
    assert attention['items'][0]['source'] == {'kind': 'task', 'id': 'open', 'projectId': 'project'}


def test_secure_decision_metadata_is_opaque_bounded_and_never_decrypts_old_native_rows(returning, monkeypatch):
    r = returning; path = r.path+'/encrypted-decisions'
    assert r.client.get(path, params=r.params).status_code == 409
    with Session(r.engine) as session:
        decision(session, 'old-native', r.after, title='Must never be decrypted by secure metadata')
        row = Decision(id='encrypted', channel_id='room', created_by='peer', created_at=r.after)
        session.add(row); session.flush()
        revision = new_revision(row, DecisionContent(matrix_event_id='$opaque-decision'), 1, 'peer')
        revision.created_at = r.after; session.add(revision)
        session.add(SecureChannel(channel_id='room', created_by='viewer', status='active')); session.commit()
    from app.api import decisions
    monkeypatch.setattr(decisions, 'revision_data', lambda *_args, **_kwargs: pytest.fail('Secure metadata must never invoke a plaintext serializer'))
    result = r.client.get(path, params=r.params)
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    data = result.json()
    assert data['incomplete'] and len(data['items']) == 1
    assert data['items'][0]['id'] == 'encrypted' and data['items'][0]['matrix_event_id'] == '$opaque-decision'
    assert not {'title', 'content', 'encrypted_payload'} & data['items'][0].keys()
    r.actor.id = 'outsider'
    assert r.client.get(path, params=r.params).status_code == 403
