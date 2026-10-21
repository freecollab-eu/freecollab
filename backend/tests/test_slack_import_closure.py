"""Identity erasure immediately removes private ZIP copies under enforced FKs."""
from datetime import datetime, timedelta
import pytest
from sqlmodel import Session
from app.models.slack_import import SlackImport, SlackImportMessage
from app.models.user import User
from app.models.workspace import PersonalWorkspace, Workspace, WorkspaceMember
from app.services.auth import create_token, hash_password
from tests.test_data_controls import controls, PASSWORD, headers, _close_feedback_account


def job(identifier, actor='owner', workspace='a', status='preview'):
    return SlackImport(id=identifier,workspace_id=workspace,actor_id=actor,archive_digest=identifier,
        encrypted_archive='private-fixture-archive-copy',status=status,expires_at=datetime.utcnow()+timedelta(hours=1))


def test_actual_closure_erases_private_previews_and_preserves_shared_import_provenance(controls,monkeypatch):
    client,engine,tokens,_=controls
    with Session(engine) as session:
        session.add_all([job('pending'),job('former-workspace',workspace='b'),job('completed',status='completed'),job('peer-preview',actor='peer')]);session.flush()
        session.add(SlackImportMessage(message_id='old-root',import_id='completed',source_channel='C1',source_timestamp='1704067200.000001'))
        session.commit()
    monkeypatch.setattr('app.services.matrix_bridge.remove_secure_member',lambda *_:None)
    dry,response=_close_feedback_account(controls,tokens['owner'])
    assert dry['can_erase_identity'] and response.status_code==200,response.text
    with Session(engine) as session:
        assert session.get(SlackImport,'pending') is None
        assert session.get(SlackImport,'former-workspace') is None
        completed=session.get(SlackImport,'completed')
        assert completed.status=='completed' and completed.encrypted_archive==''
        assert session.get(SlackImportMessage,'old-root').import_id=='completed'
        assert session.get(SlackImport,'peer-preview').encrypted_archive=='private-fixture-archive-copy'


@pytest.mark.parametrize('status',['preview','expired','discarded','completed'])
def test_own_unreferenced_zip_metadata_does_not_trap_empty_private_workspace_closure(controls,status):
    client,engine,tokens,_=controls
    with Session(engine) as session:
        session.add(User(id='importer-only',name='Importer',email='importer-only@example.invalid',password_hash=hash_password(PASSWORD)))
        session.add(Workspace(id='import-home',name='Private home'));session.flush()
        session.add(PersonalWorkspace(user_id='importer-only',workspace_id='import-home'))
        session.add(WorkspaceMember(workspace_id='import-home',user_id='importer-only',role='owner'));session.flush()
        session.add(job('own-preview',actor='importer-only',workspace='import-home',status=status));session.commit()
        token=create_token('importer-only',session)
    dry,response=_close_feedback_account(controls,token)
    assert dry['can_erase_identity'] and dry['empty_personal_workspaces']==['import-home']
    assert response.status_code==200,response.text
    with Session(engine) as session:
        assert session.get(Workspace,'import-home') is None
        assert session.get(SlackImport,'own-preview') is None
        assert session.get(User,'importer-only').is_blocked


def test_foreign_private_zip_in_home_is_not_erased_by_another_identity(controls):
    client,engine,tokens,_=controls
    with Session(engine) as session:
        session.add(User(id='importer-only',name='Importer',email='importer-only@example.invalid',password_hash=hash_password(PASSWORD)))
        session.add(Workspace(id='import-home',name='Private home'));session.flush()
        session.add(PersonalWorkspace(user_id='importer-only',workspace_id='import-home'))
        session.add(WorkspaceMember(workspace_id='import-home',user_id='importer-only',role='owner'));session.flush()
        session.add(job('foreign-preview',actor='peer',workspace='import-home'));session.commit()
        token=create_token('importer-only',session)
    dry,response=_close_feedback_account(controls,token)
    assert not dry['can_erase_identity'] and dry['blocking_workspaces']==['import-home']
    assert response.status_code==409,response.text
    with Session(engine) as session:
        assert session.get(SlackImport,'foreign-preview').encrypted_archive=='private-fixture-archive-copy'
