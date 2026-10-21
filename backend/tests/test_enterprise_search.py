"""Search uses real current sources, personal delegations and live ACL checks."""
import base64
from datetime import datetime, timedelta
import io
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from sqlmodel import Session, select
import y_py

from app.api import activity_feed, enterprise_search, shared_documents
from app.models.channel import ChannelMember
from app.models.enterprise_search import EnterpriseSearchConnection
from app.models.file import File
from app.models.file_version import FileDocument, FileVersion
from app.models.secure_channel import SecureChannel
from app.models.shared_documents import SharedDocument
from app.services import search_providers, search_sources
from app.services.auth import revoke_token
from tests.test_project_tasks import board, headers, project, task


@pytest.fixture
def search(board, tmp_path, monkeypatch):
    for router in (enterprise_search.router, shared_documents.router, activity_feed.router):
        board.client.app.include_router(router)
    paths = {}
    def read_file(identifier, max_bytes, expected_size=None):
        path = paths.get(identifier)
        if path is None: return None
        with path.open("rb") as handle: return handle.read(max_bytes + 1)
    monkeypatch.setattr(search_sources, "storage_service", SimpleNamespace(read_file=read_file))
    board.paths, board.directory = paths, tmp_path
    return board


def add_file(search, content, content_type="text/plain", identifier=None, name="Design.txt"):
    identifier = identifier or "1" * 32
    path = search.directory / identifier; path.write_bytes(content); search.paths[identifier] = path
    with Session(search.engine) as session:
        session.add(File(id=identifier, channel_id="a", uploader_id="owner", filename=name,
            content_type=content_type, size=len(content), storage_path="unused")); session.commit()
    return identifier


def add_document(search, body="Scandinavian search needle"):
    doc = y_py.YDoc()
    with doc.begin_transaction() as transaction:
        doc.get_text("title").insert(transaction, 0, "Living design")
        doc.get_text("body").insert(transaction, 0, body)
    update = base64.b64encode(y_py.encode_state_as_update(doc)).decode()
    response = search.client.post("/channels/a/shared-documents", headers=headers(search),
        json={"id":"doc-a", "update_id":"first", "title":"Living design", "update_base64":update})
    assert response.status_code == 201, response.text


def sources(search, who="owner"):
    return search.client.get("/channels/a/search/sources", headers=headers(search, who))


def test_current_documents_tasks_feed_and_real_file_body_are_searchable(search):
    add_document(search); project(search); task(search)
    add_file(search, b"File body cobalt. Filename contains no keyword.")
    response = search.client.post("/channels/a/activity-feed", headers=headers(search),
        json={"id":"a" * 32, "expected_revision":0, "fields":{"title":"Studio", "body":"Feed body pine", "poll":None}})
    assert response.status_code == 201, response.text
    response = sources(search)
    assert response.status_code == 200, response.text
    data = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert data["persistent_plaintext_index"] is False
    by_kind = {item["kind"]:item for item in data["items"]}
    assert "Scandinavian search needle" == by_kind["document"]["text"]
    assert "Private checklist" in by_kind["task"]["text"]
    assert "Feed body pine" == by_kind["feed"]["text"]
    assert "cobalt" in by_kind["file-content"]["text"]
    assert "Scandinavian" not in by_kind["document"]["reference"]
    citation = search.client.post("/search/resolve", headers=headers(search), json={"reference":by_kind["document"]["reference"]})
    assert citation.status_code == 200 and citation.json()["text"] == by_kind["document"]["text"]


def test_citation_is_actor_bound_and_rejects_current_revocation_and_stale_sources(search):
    add_document(search)
    reference = sources(search).json()["items"][0]["reference"]
    assert search.client.post("/search/resolve", headers=headers(search,"peer"), json={"reference":reference}).status_code == 404
    assert sources(search,"outsider").status_code == 403
    with Session(search.engine) as session:
        document = session.get(SharedDocument,"doc-a"); document.archived = True; session.add(document); session.commit()
    assert search.client.post("/search/resolve", headers=headers(search),json={"reference":reference}).status_code == 404
    with Session(search.engine) as session:
        member = session.get(ChannelMember,("a","owner")); session.delete(member); session.commit()
    assert sources(search).status_code == 403


def test_file_replacement_and_deletion_remove_old_results_immediately(search):
    original = add_file(search,b"old-secret",identifier="1"*32)
    current = add_file(search,b"new-secret",identifier="2"*32)
    with Session(search.engine) as session:
        session.add(FileDocument(id="document",channel_id="a",owner_id="owner",current_file_id=original,version_count=2)); session.commit()
        session.add_all([FileVersion(file_id=original,document_id="document",number=1),FileVersion(file_id=current,document_id="document",number=2)]); session.commit()
    first = sources(search).json()["items"]
    assert [item["id"] for item in first] == [original]
    with Session(search.engine) as session:
        document=session.get(FileDocument,"document");document.current_file_id=current;session.add(document);session.commit()
    assert [item["id"] for item in sources(search).json()["items"]] == [current]
    assert search.client.post("/search/resolve",headers=headers(search),json={"reference":first[0]["reference"]}).status_code == 404


def test_matrix_channels_never_enter_server_extraction(search,monkeypatch):
    with Session(search.engine) as session:
        session.add(SecureChannel(channel_id="a",matrix_room_id="!a:test",created_by="owner"));session.commit()
    monkeypatch.setattr(search_sources,"isolated_extract",lambda *_:pytest.fail("E2EE content reached extractor"))
    assert sources(search).status_code == 409


def test_real_yjs_and_passive_office_and_pdf_extraction_are_bounded(search):
    add_document(search)
    package=io.BytesIO()
    with ZipFile(package,"w") as archive:
        archive.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Office body spruce</w:t></w:r></w:p></w:body></w:document>')
    add_file(search,package.getvalue(),"application/vnd.openxmlformats-officedocument.wordprocessingml.document",identifier="3"*32,name="Design.docx")
    # A real PDF with a literal text operator, no external parser fixture.
    chunks=[b"%PDF-1.4\n"];offsets=[0]
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    stream=b"BT /F1 12 Tf 20 350 Td (PDF body birch) Tj ET"
    objects.append(b"<< /Length "+str(len(stream)).encode()+b" >>\nstream\n"+stream+b"\nendstream")
    for index,obj in enumerate(objects,1):
        offsets.append(sum(map(len,chunks)));chunks.append(str(index).encode()+b" 0 obj\n"+obj+b"\nendobj\n")
    start=sum(map(len,chunks));chunks.append(b"xref\n0 6\n0000000000 65535 f \n"+b"".join(f"{value:010d} 00000 n \n".encode() for value in offsets[1:])+b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"+str(start).encode()+b"\n%%EOF\n")
    add_file(search,b"".join(chunks),"application/pdf",identifier="4"*32,name="Brief.pdf")
    texts="\n".join(item["text"] for item in sources(search).json()["items"])
    assert all(value in texts for value in ("spruce","birch","Scandinavian"))
    assert search_sources.isolated_extract({"kind":"yjs","updates":["AAA="]})["text"] == ""
    with pytest.raises(Exception): search_sources.isolated_extract({"kind":"pdf","content":base64.b64encode(b"not pdf").decode()})


def connect(search,provider="google",config=None,who="owner"):
    response=search.client.post("/workspaces/wa/enterprise-search/connections",headers=headers(search,who),json={
        "name":"My provider","provider":provider,"config":config or {"folder_id":"root"},"access_token":"private-personal-token","delegated_access_acknowledged":True})
    assert response.status_code==200,response.text
    assert "private-personal-token" not in response.text
    return response.json()


def test_google_search_uses_personal_provider_acl_and_rechecks_on_open(search,monkeypatch):
    connection=connect(search);calls=[]; revoked=False
    def request(method,url,token,body=None):
        calls.append((method,url,token,body))
        assert token=="private-personal-token"
        if "/files?" in url: return {"files":[{"id":"allowed","name":"Design","webViewLink":"https://docs.google.com/document/d/allowed"}]}
        if revoked: raise search_providers.ProviderError("permission_denied",status=403)
        return {"id":"allowed","name":"Design","webViewLink":"https://docs.google.com/document/d/allowed","parents":["root"]}
    monkeypatch.setattr(search_providers,"request",request)
    base="/workspaces/wa/enterprise-search/connections/"+connection["id"]
    assert search.client.post(base+"/query",headers=headers(search),json={"query":"design","connection_revision":1,"share_query_with_provider":False}).status_code==422
    assert search.client.post(base+"/query",headers=headers(search,"peer"),json={"query":"design","connection_revision":1,"share_query_with_provider":True}).status_code==404
    result=search.client.post(base+"/query",headers=headers(search),json={"query":"design","connection_revision":1,"share_query_with_provider":True})
    assert result.status_code==200,result.text
    assert len(calls)==2 and "fullText" in calls[0][1]
    reference=result.json()["items"][0]["reference"]
    revoked=True
    assert search.client.post("/enterprise-search/resolve",headers=headers(search),json={"reference":reference}).status_code==403
    with Session(search.engine) as session:
        row=session.get(EnterpriseSearchConnection,connection["id"])
        assert "private-personal-token" not in row.encrypted_credentials
    assert search.client.get("/workspaces/wa/enterprise-search/connections",headers=headers(search,"peer")).json()==[]


def test_disconnect_and_session_revocation_during_provider_io_return_no_results(search,monkeypatch):
    connection=connect(search)
    def removed(*_):
        with Session(search.engine) as session:
            row=session.get(EnterpriseSearchConnection,connection["id"]);session.delete(row);session.commit()
        return {"items":[{"id":"secret"}],"complete":True}
    monkeypatch.setattr(search_providers,"search",removed)
    response=search.client.post("/workspaces/wa/enterprise-search/connections/"+connection["id"]+"/query",headers=headers(search),
        json={"query":"secret","connection_revision":1,"share_query_with_provider":True})
    assert response.status_code==404 and "secret" not in response.text


def test_session_revocation_during_provider_io_returns_no_private_results(search,monkeypatch):
    connection=connect(search)
    def removed(*_):
        with Session(search.engine) as session:
            revoke_token(search.tokens["owner"],session);session.commit()
        return {"items":[{"id":"private-remote-content"}],"complete":True}
    monkeypatch.setattr(search_providers,"search",removed)
    response=search.client.post("/workspaces/wa/enterprise-search/connections/"+connection["id"]+"/query",headers=headers(search),
        json={"query":"query","connection_revision":1,"share_query_with_provider":True})
    assert response.status_code==401 and "private-remote-content" not in response.text


def test_native_source_access_and_dlp_are_rechecked_after_extraction(search,monkeypatch):
    add_document(search)
    real=search_sources.source
    def removed(session,channel_id,kind,row):
        item=real(session,channel_id,kind,row)
        if kind=="document":
            member=session.get(ChannelMember,(channel_id,"owner"));session.delete(member);session.commit()
        return item
    monkeypatch.setattr(search_sources,"source",removed)
    response=sources(search)
    assert response.status_code==403 and "Scandinavian" not in response.text


def test_graph_folder_scope_and_custom_subject_acl_protocol(search,monkeypatch):
    calls=[]
    def request(method,url,token,body=None):
        calls.append((method,url,body))
        if "search(q=" in url:return {"value":[{"id":"file","name":"Brief","webUrl":"https://tenant.sharepoint.com/Brief"}]}
        if "/items/file" in url:return {"id":"file","name":"Brief","webUrl":"https://tenant.sharepoint.com/Brief","parentReference":{"driveId":"drive","id":"root"}}
        if url.endswith("/search"):return {"items":[{"id":"x","title":"Wrong account","url":"https://example.com/x","acl":{"subject":"different","allowed":True}}],"has_more":False}
        raise AssertionError(url)
    monkeypatch.setattr(search_providers,"request",request)
    result=search_providers.search("microsoft",{"drive_id":"drive","folder_id":"root"},"own-token","hello ' world",10)
    assert len(result["items"])==1 and "%27%27" in calls[0][1]
    result=search_providers.search("custom",{"search_url":"https://search.example.com/search","resolve_url":"https://search.example.com/resolve","external_subject":"own"},"own-token","query",10)
    assert result["items"]==[] and calls[-1][2]["subject"]=="own"
    with pytest.raises(search_providers.ProviderError):search_providers.config("custom",{"search_url":"http://localhost/search","resolve_url":"http://localhost/resolve","external_subject":"own"})


def test_invalid_personal_secret_is_not_echoed_in_validation_errors(search):
    token="private-submitted-secret-"*1000
    response=search.client.post("/workspaces/wa/enterprise-search/connections",headers=headers(search),json={
        "name":"My provider","provider":"google","config":{"folder_id":"root"},"access_token":token,"delegated_access_acknowledged":True})
    assert response.status_code==422 and "private-submitted-secret" not in response.text
    assert response.headers["cache-control"]=="no-store"


def test_truncated_passive_text_is_explicitly_incomplete(search):
    add_file(search,b"x"*100001)
    data=sources(search).json()
    assert not data["complete"]
    assert data["items"][0]["truncated"] and len(data["items"][0]["text"])==100000
