"""Current grants, transaction-bound triggers and bounded approval automation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import base64
import importlib
import json
import pkgutil
import secrets
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app import models
for module in pkgutil.iter_modules(models.__path__):
    importlib.import_module('app.models.' + module.name)
from app.api import auth, notifications, project_tasks, workflows
from app.config import settings
from app.database import get_session
from app.models.account_security import AccountSecurity
from app.models.channel import Channel, ChannelMember
from app.models.file import File
from app.models.notification import Notification
from app.models.project_tasks import ProjectBoard, TaskCard
from app.models.secure_channel import SecureChannel
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from app.models.workspace_oidc import WorkspaceOIDCProvider, WorkspaceOIDCSession
from app.models.workflow import WorkflowActivity, WorkflowDefinition, WorkflowNotice, WorkflowRevision, WorkflowRun, WorkflowVote
from app.services import workflow as service
from app.services.auth import create_token, hash_password, revoke_token, token_digest
from app.services.account_security import seal_private, totp_at

PASSWORD = 'Private-workflow-test!2026'
BASE = '/workspaces/a/channels/channel-a/workflows'


@pytest.fixture
def flows(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'test')
    monkeypatch.setattr(settings, 'matrix_enabled', False)
    monkeypatch.setattr(settings, 'mail_delivery_mode', 'disabled')
    monkeypatch.setattr(settings, 'account_security_key', SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()))
    monkeypatch.setattr(settings, 'message_encryption_keys', {'workflow-test': SecretStr(base64.b64encode(secrets.token_bytes(32)).decode())})
    monkeypatch.setattr(settings, 'message_encryption_active_key_id', 'workflow-test')
    if hasattr(settings, 'workflows_enabled'):
        monkeypatch.setattr(settings, 'workflows_enabled', True)
    engine = create_engine('sqlite:///' + str(tmp_path / 'workflows.db'), connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        password_hash = hash_password(PASSWORD)
        for identity in ['owner', 'other-admin', 'approver', 'member', 'escalation', 'outsider']:
            session.add(User(id=identity, name=identity, email=identity + '@example.com', password_hash=password_hash))
        session.add_all([Workspace(id='a', name='A'), Workspace(id='b', name='B'),
                         Channel(id='channel-a', name='A'), Channel(id='channel-b', name='B')])
        session.commit()
        for identity in ['owner', 'other-admin', 'approver', 'member', 'escalation']:
            role = 'owner' if identity == 'owner' else 'admin' if identity == 'other-admin' else 'member'
            session.add(WorkspaceMember(workspace_id='a', user_id=identity, role=role))
            session.add(ChannelMember(channel_id='channel-a', user_id=identity, role='admin' if identity in ['owner', 'other-admin'] else 'member'))
        session.add_all([WorkspaceMember(workspace_id='b', user_id='outsider', role='owner'),
                         ChannelMember(channel_id='channel-b', user_id='outsider', role='admin'),
                         WorkspaceChannel(workspace_id='a', channel_id='channel-a'), WorkspaceChannel(workspace_id='b', channel_id='channel-b'),
                         WorkspaceOIDCProvider(workspace_id='a', name='Customer', issuer_url='https://id.example.com', client_id='client', enabled=True, created_by='owner')])
        session.commit()
        tokens = {identity: create_token(identity, session) for identity in ['owner', 'other-admin', 'approver', 'member', 'escalation', 'outsider']}
        tokens['scoped-owner'] = create_token('owner', session, auth_methods=['sso'], workspace_scope={'workspace_id': 'a', 'provider_revision': 1})
        tokens['scoped-approver'] = create_token('approver', session, auth_methods=['sso'], workspace_scope={'workspace_id': 'a', 'provider_revision': 1})
        for identity in ['scoped-owner', 'scoped-approver']:
            session.add(WorkspaceOIDCSession(token_hash=token_digest(tokens[identity]), workspace_id='a', provider_revision=1))
        session.commit()
    app = FastAPI()
    for router in [workflows.router, auth.router, project_tasks.router, notifications.router]:
        app.include_router(router)
    def sessions():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, engine, tokens
    engine.dispose()


def headers(tokens, identity='owner'):
    return {'Authorization': 'Bearer ' + tokens[identity]}


def definition(*, trigger='manual', recipients=None, quorum=1, steps=None, **options):
    action = {'kind': 'approval', 'recipients': recipients or ['approver'], 'quorum': quorum,
              'reminder_minutes': 1, 'timeout_minutes': 10, 'max_reminders': 2,
              'escalation_recipients': [], **options}
    return {'name': 'Private document approval', 'trigger': trigger, 'conditions': {'actor': 'any', 'status': None},
            'steps': steps or [action]}


def create(flows, fields=None, identity='owner', password=PASSWORD):
    client, _, tokens = flows
    return client.post(BASE, headers=headers(tokens, identity), json={'fields': fields or definition(), 'password': password})


def start(flows, row):
    client, _, tokens = flows
    result = client.post(BASE + '/' + row['id'] + '/run', headers=headers(tokens, 'member'))
    assert result.status_code == 202, result.text
    return result.json()['id']


def process(engine, *, now=None):
    with Session(engine) as session:
        return service.run_pending(session, now=now)


def read(flows, identifier, identity='owner'):
    client, _, tokens = flows
    response = client.get(BASE + '/runs/' + identifier, headers=headers(tokens, identity))
    assert response.status_code == 200, response.text
    return response.json()


def test_configuration_requires_both_admin_roles_native_reauth_and_keeps_secret_fields_encrypted(flows):
    client, engine, tokens = flows
    for identity in ['member', 'outsider', 'scoped-owner']:
        assert create(flows, identity=identity).status_code in {403, 404}
    assert create(flows, password='wrong-password').status_code == 403
    assert client.get('/auth/me', headers=headers(tokens)).status_code == 200
    row = create(flows).json()
    assert row['fields']['name'] == 'Private document approval'
    assert client.get(BASE, headers=headers(tokens, 'member')).json()['can_manage'] is False
    assert client.get(BASE, headers=headers(tokens)).headers['cache-control'] == 'no-store'
    with Session(engine) as session:
        encrypted = session.exec(select(WorkflowRevision)).one().encrypted_definition
        assert 'Private document approval' not in encrypted and PASSWORD not in encrypted
    update = {'fields': definition(trigger='file_uploaded'), 'password': PASSWORD, 'expected_revision': 0}
    assert client.put(BASE + '/' + row['id'], headers=headers(tokens), json=update).status_code == 409
    update['expected_revision'] = 1
    assert client.put(BASE + '/' + row['id'], headers=headers(tokens), json=update).json()['revision'] == 2
    assert len(client.get(BASE + '/' + row['id'] + '/revisions', headers=headers(tokens)).json()['revisions']) == 2


def test_native_configuration_rechecks_the_accounts_current_mfa_factor(flows):
    client, engine, tokens = flows
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
    with Session(engine) as session:
        state = session.get(AccountSecurity, 'owner')
        state.totp_secret = seal_private(secret, 'totp', 'owner')
        session.add(state); session.commit()
    assert create(flows).status_code == 403
    response = client.post(BASE, headers=headers(tokens), json={'fields': definition(), 'password': PASSWORD,
        'code': totp_at(secret, int(time.time() // 30))})
    assert response.status_code == 201


def test_channel_admin_without_workspace_admin_or_reverse_cannot_configure(flows):
    _, engine, _ = flows
    with Session(engine) as session:
        workspace = session.get(WorkspaceMember, ('a', 'other-admin')); workspace.role = 'member'; session.add(workspace); session.commit()
    assert create(flows, identity='other-admin').status_code == 403
    with Session(engine) as session:
        workspace = session.get(WorkspaceMember, ('a', 'other-admin')); workspace.role = 'admin'; session.add(workspace)
        channel = session.get(ChannelMember, ('channel-a', 'other-admin')); channel.role = 'member'; session.add(channel); session.commit()
    assert create(flows, identity='other-admin').status_code == 403


def test_manual_trigger_only_starts_the_selected_workflow(flows):
    _, engine, _ = flows
    a, b = create(flows).json(), create(flows, definition(recipients=['member'])).json()
    identifier = start(flows, a)
    with Session(engine) as session:
        entries = session.exec(select(WorkflowRun)).all()
        assert len(entries) == 1 and entries[0].id == identifier and entries[0].workflow_id == a['id']


def test_producer_transaction_and_event_identity_prevent_duplicate_runs(flows):
    _, engine, _ = flows
    create(flows, definition(trigger='file_uploaded'))
    with Session(engine) as session:
        assert service.queue_event(session, 'upload:one', 'a', 'channel-a', 'file_uploaded', 'file') == 1
        assert service.queue_event(session, 'upload:one', 'a', 'channel-a', 'file_uploaded', 'file') == 0
        session.rollback()
    with Session(engine) as session:
        assert not session.exec(select(WorkflowRun)).all()
        assert service.queue_event(session, 'upload:one', 'a', 'channel-a', 'file_uploaded', 'file') == 1
        session.commit()
    assert process(engine) == 1
    assert process(engine) == 0
    with Session(engine) as session:
        run = session.exec(select(WorkflowRun)).one()
        assert run.status == 'waiting_approval'
        assert len(session.exec(select(Notification)).all()) == len(session.exec(select(WorkflowNotice)).all()) == 1


def test_creator_and_status_conditions_use_bounded_native_metadata(flows):
    _, engine, _ = flows
    fields = definition(trigger='task_updated'); fields['conditions'] = {'actor': 'creator', 'status': 'done'}
    create(flows, fields)
    with Session(engine) as session:
        assert service.queue_event(session, 'a', 'a', 'channel-a', 'task_updated', 'task', actor_id='member', metadata={'status': 'done'}) == 0
        assert service.queue_event(session, 'b', 'a', 'channel-a', 'task_updated', 'task', actor_id='owner', metadata={'status': 'doing'}) == 0
        assert service.queue_event(session, 'c', 'a', 'channel-a', 'task_updated', 'task', actor_id='owner', metadata={'status': 'done'}) == 1
        with pytest.raises(ValueError):
            service.queue_event(session, 'd', 'a', 'channel-a', 'task_updated', 'task', metadata={'status': 'done', 'document': 'private'})
        with pytest.raises(ValueError):
            service.queue_event(session, 'e', 'b', 'channel-a', 'task_updated', 'task')


@pytest.mark.parametrize('accept_at_creation', [False, True])
def test_native_decision_status_producer_drives_only_matching_workflows(flows, monkeypatch, accept_at_creation):
    from app.api import decisions
    from app.config import settings
    from app.services import webhooks
    client, engine, tokens = flows
    client.app.include_router(decisions.router)
    monkeypatch.setattr(webhooks, 'settings', settings)
    monkeypatch.setattr(settings, 'webhooks_enabled', False)
    fields = definition(trigger='decision_updated', steps=[{'kind': 'notify', 'recipients': ['approver']}])
    fields['conditions'] = {'actor': 'creator', 'status': 'accepted'}
    created = create(flows, fields); assert created.status_code == 201, created.text
    draft = {'title': 'Reviewed native decision', 'content': 'Decision content stays out of workflow metadata', 'status': 'accepted' if accept_at_creation else 'proposed'}
    response = client.post('/channels/channel-a/decisions', headers=headers(tokens), json=draft)
    assert response.status_code == 200, response.text
    decision = response.json()
    expected_resource = decision['id']
    if not accept_at_creation:
        with Session(engine) as session:
            assert session.exec(select(WorkflowRun)).all() == []
        updated = client.post('/decisions/'+decision['id']+'/revisions', headers=headers(tokens), json={**draft, 'status': 'accepted', 'expected_revision': 1})
        assert updated.status_code == 200, updated.text
        expected_resource = updated.json()['revision_id']
    assert process(engine) == 1
    assert process(engine) == 0
    with Session(engine) as session:
        run = session.exec(select(WorkflowRun)).one()
        assert run.status == 'completed' and run.actor_id == 'owner'
        assert run.resource_id == expected_resource
        assert 'Decision content' not in json.dumps(run.model_dump(mode='json'))
        notices = session.exec(select(WorkflowNotice)).all()
        assert {(notice.user_id, notice.kind) for notice in notices} == {('approver', 'notify'), ('owner', 'finished')}
        assert len(notices) == len(session.exec(select(Notification)).all()) == 2


def test_file_metadata_recovers_creator_without_reading_file_bytes(flows):
    _, engine, _ = flows
    fields = definition(trigger='file_uploaded'); fields['conditions']['actor'] = 'creator'
    create(flows, fields)
    with Session(engine) as session:
        session.add(File(id='file', channel_id='channel-a', uploader_id='owner', filename='Private filename', content_type='text/plain', size=1, storage_path='never-open-this-path'))
        session.flush()
        assert service.queue_event(session, 'file:one', 'a', 'channel-a', 'file_uploaded', 'file') == 1
        run = session.exec(select(WorkflowRun)).one()
        assert run.actor_id == 'owner'
        assert 'Private filename' not in json.dumps(run.model_dump(mode='json'))


def test_quorum_choice_is_idempotent_and_next_action_waits_for_current_approvers(flows):
    client, engine, tokens = flows
    fields = definition(recipients=['approver', 'member'], quorum=2)
    fields['steps'].append({'kind': 'notify', 'recipients': ['owner']})
    identifier = start(flows, create(flows, fields).json()); process(engine)
    endpoint = BASE + '/runs/' + identifier + '/vote'
    first = {'choice': 'approve', 'note': 'Private approval note'}
    assert client.post(endpoint, headers=headers(tokens, 'approver'), json=first).status_code == 200
    assert client.post(endpoint, headers=headers(tokens, 'approver'), json=first).status_code == 200
    assert client.post(endpoint, headers=headers(tokens, 'approver'), json={'choice': 'reject'}).status_code == 409
    process(engine)
    run = read(flows, identifier)
    assert run['status'] == 'waiting_approval' and run['reminder_count'] == 0
    assert client.post(endpoint, headers=headers(tokens, 'member'), json={'choice': 'approve'}).status_code == 200
    process(engine)
    result = read(flows, identifier)
    assert result['status'] == 'completed' and result['step_index'] == 2
    with Session(engine) as session:
        assert len(session.exec(select(WorkflowVote)).all()) == 2
        assert 'Private approval note' not in session.exec(select(WorkflowVote).where(WorkflowVote.user_id == 'approver')).one().encrypted_note
        assert len(session.exec(select(WorkflowActivity).where(WorkflowActivity.kind == 'notification_sent')).all()) == 1


def test_scoped_member_can_approve_but_cannot_configure_workflows(flows):
    client, engine, tokens = flows
    identifier = start(flows, create(flows).json()); process(engine)
    response = client.post(BASE + '/runs/' + identifier + '/vote', headers=headers(tokens, 'scoped-approver'), json={'choice': 'approve'})
    assert response.status_code == 200
    process(engine)
    assert read(flows, identifier)['status'] == 'completed'


def test_rejection_stops_later_actions_and_removed_approver_cannot_vote(flows):
    client, engine, tokens = flows
    fields = definition(); fields['steps'].append({'kind': 'notify', 'recipients': ['owner']})
    identifier = start(flows, create(flows, fields).json()); process(engine)
    assert client.post(BASE + '/runs/' + identifier + '/vote', headers=headers(tokens, 'member'), json={'choice': 'approve'}).status_code == 403
    assert client.post(BASE + '/runs/' + identifier + '/vote', headers=headers(tokens, 'approver'), json={'choice': 'reject'}).status_code == 200
    process(engine)
    assert read(flows, identifier)['status'] == 'rejected'
    with Session(engine) as session:
        assert not session.exec(select(WorkflowActivity).where(WorkflowActivity.kind == 'notification_sent')).all()


@pytest.mark.parametrize('change', ['workspace', 'channel', 'blocked'])
def test_creator_grant_loss_cancels_queued_actions_before_notice(flows, change):
    _, engine, _ = flows
    identifier = start(flows, create(flows).json())
    with Session(engine) as session:
        if change == 'workspace':
            member = session.get(WorkspaceMember, ('a', 'owner')); member.role = 'member'; session.add(member)
        elif change == 'channel':
            member = session.get(ChannelMember, ('channel-a', 'owner')); member.role = 'member'; session.add(member)
        else:
            user = session.get(User, 'owner'); user.is_blocked = True; session.add(user)
        session.commit()
    process(engine)
    with Session(engine) as session:
        row = session.get(WorkflowRun, identifier)
        assert (row.status, row.failure) == ('cancelled', 'grant_revoked')
        assert not session.exec(select(Notification)).all()


def test_removed_approvers_votes_do_not_authorize_later_actions(flows):
    client, engine, tokens = flows
    identifier = start(flows, create(flows, definition(recipients=['approver', 'member'], quorum=2)).json()); process(engine)
    assert client.post(BASE + '/runs/' + identifier + '/vote', headers=headers(tokens, 'approver'), json={'choice': 'approve'}).status_code == 200
    with Session(engine) as session:
        session.delete(session.get(ChannelMember, ('channel-a', 'approver'))); session.commit()
    assert client.post(BASE + '/runs/' + identifier + '/vote', headers=headers(tokens, 'approver'), json={'choice': 'approve'}).status_code == 403
    process(engine)
    assert read(flows, identifier)['status'] == 'cancelled'


def test_reminders_escalation_timeout_are_bounded_and_restart_safe(flows):
    _, engine, _ = flows
    identifier = start(flows, create(flows, definition(escalation_recipients=['escalation'])).json())
    now = datetime.utcnow(); process(engine, now=now)
    for number in [1, 2, 3, 4, 5]:
        process(engine, now=now + timedelta(minutes=number, seconds=1))
    result = read(flows, identifier)
    assert result['reminder_count'] == 2 and result['escalated']
    with Session(engine) as session:
        assert len(session.exec(select(WorkflowNotice).where(WorkflowNotice.kind == 'reminder')).all()) == 2
        assert len(session.exec(select(WorkflowNotice).where(WorkflowNotice.kind == 'escalation')).all()) == 1
    process(engine, now=now + timedelta(minutes=11))
    assert read(flows, identifier)['status'] == 'timed_out'


def test_escalation_does_not_grant_early_or_cross_channel_approval(flows):
    client, engine, tokens = flows
    identifier = start(flows, create(flows, definition(escalation_recipients=['escalation'], max_reminders=1)).json())
    now = datetime.utcnow(); process(engine, now=now)
    endpoint = BASE + '/runs/' + identifier + '/vote'
    assert client.post(endpoint, headers=headers(tokens, 'escalation'), json={'choice': 'approve'}).status_code == 403
    assert client.post(endpoint, headers=headers(tokens, 'outsider'), json={'choice': 'approve'}).status_code in {403, 404}
    process(engine, now=now + timedelta(minutes=1, seconds=1))
    assert client.post(endpoint, headers=headers(tokens, 'escalation'), json={'choice': 'approve'}).status_code == 200
    process(engine)
    assert read(flows, identifier)['status'] == 'completed'


def test_disable_and_definition_revision_cancel_pending_history_without_undoing_actions(flows):
    client, engine, tokens = flows
    workflow = create(flows).json(); identifier = start(flows, workflow); process(engine)
    saved = client.put(BASE + '/' + workflow['id'], headers=headers(tokens), json={
        'fields': definition(recipients=['member']), 'expected_revision': 1, 'password': PASSWORD})
    assert saved.status_code == 200
    assert read(flows, identifier)['failure'] == 'configuration_changed'
    second = start(flows, saved.json())
    assert client.post(BASE + '/' + workflow['id'] + '/disable', headers=headers(tokens), json={'password': PASSWORD}).status_code == 200
    assert read(flows, second)['status'] == 'cancelled'
    assert client.post(BASE + '/' + workflow['id'] + '/run', headers=headers(tokens, 'member')).status_code == 409


def test_processing_lease_recovery_and_duplicate_workers_do_not_duplicate_notices(flows):
    _, engine, _ = flows
    identifier = start(flows, create(flows).json())
    with Session(engine) as session:
        row = session.get(WorkflowRun, identifier); row.status = 'processing'; row.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.add(row); session.commit()
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(lambda _: process(engine), range(2))) == 1
    with Session(engine) as session:
        assert len(session.exec(select(WorkflowNotice)).all()) == len(session.exec(select(Notification)).all()) == 1
        assert session.get(WorkflowRun, identifier).status == 'waiting_approval'


def test_retry_budget_counts_consecutive_failures_and_rolls_back_partial_effects(flows, monkeypatch):
    client, engine, tokens = flows
    row = create(flows, definition(steps=[{'kind': 'notify', 'recipients': ['approver']}])).json()
    run = start(flows, row)
    real_notice = service.notice
    def unavailable(*args, **kwargs):
        real_notice(*args, **kwargs)
        raise RuntimeError('simulated private worker failure')
    monkeypatch.setattr(service, 'notice', unavailable)
    now = datetime.utcnow()
    for attempt in range(1, 3):
        with Session(engine) as session:
            service.run_pending(session, now=now)
        with Session(engine) as session:
            saved = session.get(WorkflowRun, run)
            assert saved.status == 'queued' and saved.attempts == attempt and saved.failure == 'action_retry'
            assert not session.exec(select(Notification)).all()
            now = saved.due_at + timedelta(seconds=1)
    monkeypatch.setattr(service, 'notice', real_notice)
    with Session(engine) as session:
        assert service.run_pending(session, now=now) == 1
    with Session(engine) as session:
        saved = session.get(WorkflowRun, run)
        assert saved.status == 'completed' and saved.attempts == 0 and saved.failure is None
        assert len(session.exec(select(Notification).where(Notification.user_id == 'approver')).all()) == 1


def test_matrix_boundaries_reject_server_notes_content_conditions_and_file_classification(flows, monkeypatch):
    client, engine, tokens = flows
    with Session(engine) as session:
        session.add(SecureChannel(channel_id='channel-a', created_by='owner', status='preparing')); session.commit()
    status = definition(trigger='task_updated'); status['conditions']['status'] = 'done'
    assert create(flows, status).status_code == 409
    assert create(flows, definition(trigger='file_uploaded')).status_code == 409
    assert create(flows, definition(steps=[{'kind': 'create_task', 'project_id': 'project', 'task_title': 'Do not export'}])).status_code == 409
    row = create(flows).json(); identifier = start(flows, row); process(engine)
    assert client.post(BASE + '/runs/' + identifier + '/vote', headers=headers(tokens, 'approver'), json={'choice': 'approve', 'note': 'must-not-store'}).status_code == 409
    from app.services import matrix_bridge
    monkeypatch.setattr(matrix_bridge, 'validate_secure_event', lambda *args, **kwargs: {'event_id': '$opaque'})
    assert client.post(BASE + '/runs/' + identifier + '/vote', headers=headers(tokens, 'approver'), json={'choice': 'approve', 'matrix_event_id': '$opaque'}).status_code == 200
    with Session(engine) as session:
        vote = session.exec(select(WorkflowVote)).one()
        assert vote.encrypted_note is None and vote.matrix_event_id == '$opaque'
        note = session.exec(select(Notification)).one()
        assert note.body is None and note.title == 'Workflow approval requested'
        assert notifications.notification_visible(session, note, 'approver') is True
        session.delete(session.get(ChannelMember, ('channel-a', 'approver'))); session.commit()
        assert notifications.notification_visible(session, note, 'approver') is False


def test_create_task_action_uses_native_idempotent_helper_and_never_loops(flows):
    client, engine, tokens = flows
    project = client.post('/channels/channel-a/projects', headers=headers(tokens), json={'id': 'project', 'fields': {
        'title': 'Delivery', 'description': '', 'color': 'sage'}})
    assert project.status_code == 200, project.text
    fields = definition(steps=[{'kind': 'create_task', 'project_id': 'project', 'task_title': 'Approved follow-up'}])
    identifier = start(flows, create(flows, fields).json()); process(engine)
    assert read(flows, identifier)['status'] == 'completed'
    process(engine)
    with Session(engine) as session:
        assert len(session.exec(select(TaskCard)).all()) == 1
        assert len(session.exec(select(WorkflowRun)).all()) == 1


def test_logged_out_actor_after_lock_wait_cannot_change_workflow(flows, monkeypatch):
    client, engine, tokens = flows
    row = create(flows).json()
    original = service.lock_context
    def after_wait(session, workspace_id, channel_id):
        # Represents a logout committed before this writer acquired its locks.
        with Session(engine) as other:
            revoke_token(tokens['owner'], other)
        return original(session, workspace_id, channel_id)
    # Fire the revocation before acquiring User's writerlock to avoid a fixture
    # deadlock; the stored request context is still from the earlier dependency.
    from app.services import request_guard
    original_recheck = request_guard.recheck_actor
    def recheck(session, identity):
        from app.models.session import RevokedToken
        from app.services.auth import decode_token_claims
        claims = decode_token_claims(tokens['owner'])
        session.add(RevokedToken(token_hash=token_digest(tokens['owner']), expires_at=datetime.utcfromtimestamp(claims['exp'])))
        session.flush()
        return original_recheck(session, identity)
    monkeypatch.setattr(request_guard, 'recheck_actor', recheck)
    response = client.put(BASE + '/' + row['id'], headers=headers(tokens), json={
        'fields': definition(recipients=['member']), 'expected_revision': 1, 'password': PASSWORD})
    assert response.status_code == 401
    with Session(engine) as session:
        assert session.get(WorkflowDefinition, row['id']).current_revision == 1


@pytest.mark.parametrize('fields', [
    definition(steps=[{'kind': 'approval', 'recipients': ['approver'], 'quorum': 2}]),
    definition(steps=[{'kind': 'approval', 'recipients': ['approver'], 'max_reminders': 4}]),
    definition(steps=[{'kind': 'notify', 'recipients': ['approver'], 'url': 'https://external.example.com'}]),
    definition(steps=[{'kind': 'notify', 'recipients': ['approver']}] * 6),
])
def test_workflow_bounds_and_no_outbound_action_surface(flows, fields):
    assert create(flows, fields).status_code == 422
