"""Personal search delegation lifecycle follows account erasure/export privacy."""
import io
import json
import zipfile
from sqlmodel import Session, select
from app.models.enterprise_search import EnterpriseSearchConnection
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, PersonalWorkspace
from app.services.account_security import seal_private
from app.services.auth import create_token, hash_password
from app.services import data_control as service
from tests.test_data_controls import controls, PASSWORD, _close_feedback_account, post


def test_personal_search_tokens_are_erased_with_empty_home_account_closure(controls):
    client,engine,_,_=controls
    with Session(engine) as session:
        session.add(User(id="search-only",name="Search author",email="search-only@example.invalid",password_hash=hash_password(PASSWORD)))
        session.add(Workspace(id="search-home",name="Personal workspace"));session.commit()
        session.add(PersonalWorkspace(user_id="search-only",workspace_id="search-home"))
        session.add(WorkspaceMember(workspace_id="search-home",user_id="search-only",role="owner"))
        session.add(EnterpriseSearchConnection(id="connection",workspace_id="search-home",user_id="search-only",name="Mine",provider="google",config_json='{"folder_id":"root"}',encrypted_credentials="opaque-delegation"));session.commit()
        token=create_token("search-only",session)
    dry,response=_close_feedback_account(controls,token)
    assert dry["can_erase_identity"] and response.status_code==200,response.text
    with Session(engine) as session:
        assert not session.get(EnterpriseSearchConnection,"connection")
        assert not session.get(Workspace,"search-home")


def test_export_only_own_search_metadata_never_credentials_or_peer_connections(controls,monkeypatch):
    from app.config import settings
    from app.services import storage
    monkeypatch.setattr(storage,"storage_service",storage.StorageService(settings.storage_path))
    client,engine,_,_=controls
    with Session(engine) as session:
        for actor in ("owner","peer"):
            row=EnterpriseSearchConnection(id=actor+"-search",workspace_id="a",user_id=actor,name="Private "+actor,provider="google",config_json='{"folder_id":"private-root"}',encrypted_credentials="")
            row.encrypted_credentials=seal_private("private-provider-token-"+actor,"enterprise-search-credentials",row.id)
            session.add(row)
        session.commit()
    response=post(controls,"/exports",{"channel_ids":["native"],"acknowledge_plaintext_export":True})
    assert response.status_code==202,response.text
    with Session(engine) as session:assert service.run_pending(session)==1
    download=post(controls,"/exports/"+response.json()["id"]+"/download")
    assert download.status_code==200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        data=json.loads(archive.read("export.json"))
    assert [row["id"] for row in data["personal_search_connections"]]==["owner-search"]
    serialized=json.dumps(data)
    assert all(secret not in serialized for secret in ("private-provider-token","private-root","peer-search","encrypted_credentials"))
