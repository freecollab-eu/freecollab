"""Independent adversarial checks for data-control lease and erasure boundaries."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from app.config import settings
from app.models.data_control import AccountIdentityErasure, DataControlJob, DataFileErasure
from app.models.user import User
from app.models.workflow import WorkflowDefinition, WorkflowRun
from app.services import data_control as service
from tests.test_data_controls import FILE_ID, OLD, PASSWORD, controls, enable, headers, post, preview, queue_purge


def test_expired_attempt_cannot_fail_a_reclaimed_export_or_remove_its_artifact(controls, monkeypatch):
    _, engine, _, _ = controls
    queued = post(controls, '/exports', {'channel_ids': ['native'], 'acknowledge_plaintext_export': True}).json()
    artifact = service._artifact_path(queued['id'])
    successor_token = 'a' * 32
    def replaced_attempt(session, job):
        # Model the exact handoff boundary: the old attempt rolled back after
        # expiry and a successor claimed the job before old failure cleanup.
        job.lease_token = successor_token
        job.lease_expires_at = datetime.utcnow() + timedelta(minutes=5)
        job.status = 'processing'
        session.add(job); session.commit()
        artifact.write_bytes(b'private successor attempt fixture')
        raise RuntimeError('old attempt lost its processing lease')
    monkeypatch.setattr(service, 'process_job', replaced_attempt)
    with Session(engine) as session:
        assert service.run_pending(session) == 0
    with Session(engine) as session:
        current = session.get(DataControlJob, queued['id'])
        assert current.status == 'processing'
        assert current.lease_token == successor_token
        assert current.failure is None
    assert artifact.read_bytes() == b'private successor attempt fixture'


def test_export_build_has_no_named_plaintext_archive_even_when_sealing_fails(controls, monkeypatch):
    _, engine, _, _ = controls
    queued = post(controls, '/exports', {'channel_ids': ['native'], 'acknowledge_plaintext_export': True}).json()
    descriptors = []
    def interrupted_seal(source, destination, identifier):
        # A real complete ZIP exists only in an anonymous/unlinked descriptor.
        # Killing the process cannot leave a named workspace.zip behind.
        assert hasattr(source, 'read') and hasattr(source, 'seek')
        assert isinstance(source.name, int)
        descriptors.append(source)
        source.seek(0)
        assert source.read(2) == b'PK'
        assert not list(service._archive_root().rglob('*.zip'))
        assert not list(service._archive_root().glob('build-*'))
        raise RuntimeError('simulated interruption before sealing private export')
    monkeypatch.setattr(service, 'seal_archive', interrupted_seal)
    with Session(engine) as session:
        assert service.run_pending(session) == 0
        assert session.get(DataControlJob, queued['id']).status == 'failed'
    assert len(descriptors) == 1 and descriptors[0].closed
    assert not service._artifact_path(queued['id']).exists()
    assert not list(service._archive_root().rglob('*.zip'))


@pytest.mark.parametrize('operation', ['export', 'purge'])
def test_stored_file_symlink_cannot_read_or_erase_an_outside_target(controls, operation):
    _, engine, _, private = controls
    outside = private / 'outside-private-storage.txt'
    outside.write_bytes(b'PRIVATE FILE')
    leaf = Path(settings.storage_path) / FILE_ID
    leaf.unlink(); leaf.symlink_to(outside)
    if operation == 'export':
        queued = post(controls, '/exports', {'channel_ids': ['native'], 'acknowledge_plaintext_export': True}).json()
    else:
        enable(controls)
        queued = queue_purge(controls, preview(controls)).json()
    with Session(engine) as session:
        assert service.run_pending(session) == (0 if operation == 'export' else 1)
        current = session.get(DataControlJob, queued['id'])
        assert current.status == ('failed' if operation == 'export' else 'completed')
    assert outside.read_bytes() == b'PRIVATE FILE'
    if operation == 'export':
        assert not service._artifact_path(queued['id']).exists()
    else:
        assert not leaf.is_symlink()


def test_paused_workflow_still_protects_its_unfinished_approval_from_purge(controls):
    _, engine, _, _ = controls
    with Session(engine) as session:
        session.add(WorkflowDefinition(id='paused-review-flow', workspace_id='a', channel_id='native', created_by='owner',
            granted_by='owner', trigger='manual', enabled=False)); session.flush()
        session.add(WorkflowRun(id='pending-review-run', workflow_id='paused-review-flow', workspace_id='a', channel_id='native',
            workflow_revision=1, event_id='private-review-event', event_kind='manual', status='waiting_approval', created_at=OLD)); session.commit()
    enable(controls)
    assert post(controls, '/previews', {'channel_ids': ['native']}).status_code == 409
    with Session(engine) as session:
        run = session.get(WorkflowRun, 'pending-review-run')
        assert run.status == 'waiting_approval'
        run.status = 'cancelled'; run.finished_at = OLD; session.add(run); session.commit()
    assert preview(controls)['counts']['workflowrun'] == 1


def test_failed_job_with_pending_file_erasure_still_blocks_account_closure(controls):
    client, engine, _, _ = controls
    with Session(engine) as session:
        session.add(DataControlJob(id='b' * 32, workspace_id='a', requested_by='owner', token_hash='private-digest', auth_version=0,
            kind='purge', encrypted_request='unused private fixture', status='failed', expires_at=datetime.utcnow() + timedelta(hours=1)))
        session.flush()
        session.add(DataFileErasure(job_id='b' * 32, file_id=FILE_ID)); session.commit()
    dry = client.get('/account/data-closure', headers=headers(controls)).json()
    assert dry['active_data_jobs'] == ['b' * 32] and not dry['can_erase_identity']
    response = client.post('/account/data-closure', headers=headers(controls), json={'password': PASSWORD,
        'confirmation': 'ERASE MY ACCOUNT', 'preview_fingerprint': dry['fingerprint'],
        'acknowledge_retained_shared_authorship_and_backups': True})
    assert response.status_code == 409
    with Session(engine) as session:
        assert session.get(User, 'owner').email == 'owner@example.com'
        assert session.get(AccountIdentityErasure, 'owner') is None
