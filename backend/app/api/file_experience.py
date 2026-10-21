"""File comments, bounded passive previews and a single-resource Office DAV server."""
from datetime import datetime, timedelta
import json
import re
import secrets
from urllib.parse import quote
from uuid import uuid4
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.file import File
from ..models.file_version import FileDocument, FileVersion
from ..models.file_experience import FileComment, OfficeEditLock, OfficeEditSession
from ..services import file_experience as service
from ..services.account_security import account_limit, ensure_reauthentication
from ..services.auth import get_current_user, security
from ..services.crypto import decrypt_message, encrypt_message
from ..services.request_guard import recheck_actor
from ..services.storage import storage_service
from ..services.remote_sync import restore_synced_file

router = APIRouter(tags=["file experience"])
BASE = "/channels/{channel_id}/file-experience/{file_id}"
DAV = "/office-edit/{session_id}/{secret}/{filename}"
PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommentInput(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    text: str = Field(min_length=1, max_length=2000)


class Confirmation(Strict):
    password: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=128)


def comment_record(row):
    try:
        content = json.loads(decrypt_message(row.encrypted_text, row.channel_id))
        if (content["document_id"], content["id"], content["file_id"]) != (row.document_id, row.id, row.file_id): raise ValueError
        text = content["text"]
        if not isinstance(text, str) or len(text) > 2000: raise ValueError
    except Exception:
        raise HTTPException(409, "File comment could not be verified") from None
    return {"id": row.id, "document_id": row.document_id, "file_id": row.file_id, "created_by": row.created_by, "created_at": row.created_at, "text": text}


@router.get(BASE + "/comments")
def comments(channel_id: str, file_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    response.headers.update(PRIVATE)
    record, document = service.native_file(session, channel_id, file_id, user.id)
    rows = session.exec(select(FileComment).where(FileComment.channel_id == channel_id, FileComment.document_id == (document.id if document else record.id))
                        .order_by(FileComment.created_at, FileComment.id).limit(100)).all()
    return [comment_record(row) for row in rows]


@router.post(BASE + "/comments", status_code=201)
def add_comment(channel_id: str, file_id: str, data: CommentInput, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    response.headers.update(PRIVATE)
    actor = user.id
    service.lock_channel_writes(session, channel_id); session.expire_all(); recheck_actor(session, actor)
    record, document = service.native_file(session, channel_id, file_id, actor)
    from ..services.workspaces import require_direct_message_writable
    require_direct_message_writable(session, channel_id, actor)
    document_id = document.id if document else record.id
    text = data.text.strip()
    if not text: raise HTTPException(422, "Write a comment")
    previous = session.get(FileComment, data.id)
    if previous:
        if previous.channel_id != channel_id or previous.document_id != document_id: raise HTTPException(404, "Comment not found")
        result = comment_record(previous)
        if previous.created_by != actor or result["text"] != text or previous.file_id != record.id: raise HTTPException(409, "Comment identifier already used")
        return result
    count = session.exec(select(func.count()).select_from(FileComment).where(FileComment.document_id == document_id)).one()
    if count >= 100: raise HTTPException(409, "This document reached its retained comment limit")
    row = FileComment(id=data.id, channel_id=channel_id, document_id=document_id, file_id=record.id, created_by=actor,
        encrypted_text=encrypt_message(json.dumps({"id":data.id,"document_id":document_id,"file_id":record.id,"text":text}, separators=(",", ":")), channel_id))
    session.add(row); session.commit()
    return comment_record(row)


@router.get(BASE + "/preview")
async def preview(channel_id: str, file_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    response.headers.update(PRIVATE); actor = user.id
    record, _ = service.native_file(session, channel_id, file_id, actor)
    if record.size > service.MAX_PREVIEW_FILE: raise HTTPException(422, "Office preview limit is 10 MiB")
    content = service.bounded_file(record)
    result = service.office_preview(content, record.content_type)
    session.expire_all(); recheck_actor(session, actor); service.native_file(session, channel_id, file_id, actor)
    return result


@router.post(BASE + "/office-sessions", status_code=201)
async def office_session(channel_id: str, file_id: str, data: Confirmation, response: Response, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    response.headers.update(PRIVATE); actor = user.id
    account_limit(session, "office-edit", actor, 12, 3600)
    # state_for may initialize/commit, so reauthenticate before taking the fence.
    ensure_reauthentication(session, user, data.password, data.code)
    service.lock_channel_writes(session, channel_id); session.expire_all(); recheck_actor(session, actor)
    record, document = service.native_file(session, channel_id, file_id, actor, writing=True)
    if record.content_type not in service.OFFICE_TYPES: raise HTTPException(422, "Desktop Office editing supports DOCX, XLSX and PPTX")
    if document and document.current_file_id != record.id: raise HTTPException(409, "Open the current version before editing in Office")
    content = service.bounded_file(record)
    service.office_preview(content, record.content_type)
    if document is None:
        document = FileDocument(id=record.id, channel_id=channel_id, owner_id=actor, current_file_id=record.id, created_at=record.created_at)
        session.add(document); session.flush(); session.add(FileVersion(file_id=record.id, document_id=document.id, number=1))
    # Bound active capabilities without deleting another desktop's history.
    count = session.exec(select(func.count()).select_from(OfficeEditSession).where(OfficeEditSession.user_id == actor, OfficeEditSession.revoked_at.is_(None), OfficeEditSession.expires_at > datetime.utcnow())).one()
    if count >= 5: raise HTTPException(409, "Close an existing Office session before opening another")
    raw = secrets.token_urlsafe(32); identifier = uuid4().hex; token_hash, claims = service.saved_claims(credentials.credentials)
    row = OfficeEditSession(id=identifier, secret_hash=service.digest(raw), document_id=document.id, channel_id=channel_id, user_id=actor,
        native_session_hash=token_hash, claims_json=claims, expires_at=datetime.utcnow()+timedelta(minutes=15), last_file_id=record.id,
        filename=record.filename, content_type=record.content_type)
    session.add(row); session.commit()
    return {"id":identifier,"path":"/office-edit/"+identifier+"/"+raw+"/"+quote(record.filename,safe=""),
            "handler":service.OFFICE_TYPES[record.content_type],"expires_at":row.expires_at}


@router.delete(BASE + "/office-sessions/{identifier}")
def revoke_office(channel_id: str, file_id: str, identifier: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    response.headers.update(PRIVATE); actor = user.id
    service.lock_channel_writes(session, channel_id); session.expire_all(); recheck_actor(session, actor)
    record, document = service.native_file(session, channel_id, file_id, actor, writing=True)
    row = session.get(OfficeEditSession, identifier)
    if row is None or row.user_id != actor or row.channel_id != channel_id or row.document_id != (document.id if document else record.id): raise HTTPException(404, "Office session unavailable")
    row.revoked_at = datetime.utcnow(); session.add(row); session.commit()
    return {"revoked":True}


def xml_response(root, status=200, extra=None):
    return Response(ET.tostring(root, encoding="utf-8", xml_declaration=True), status_code=status, media_type="application/xml", headers={**PRIVATE, **(extra or {})})


def supplied_lock(request):
    value = request.headers.get("lock-token", "") + " " + request.headers.get("if", "")
    if re.search(r"\bNot\b", value, re.IGNORECASE): raise HTTPException(400, "Negated DAV conditions are unsupported")
    matches = re.findall(r"<?opaquelocktoken:([A-Za-z0-9_-]{43})>?", value)
    if len(set(matches)) > 1: raise HTTPException(400, "Ambiguous Office lock token")
    return matches[0] if matches else None


def live_lock(session, document_id):
    row = session.get(OfficeEditLock, document_id, populate_existing=True)
    return row if row and row.expires_at > datetime.utcnow() else None


def check_lock(request, lock, capability):
    raw = supplied_lock(request)
    if lock and (lock.session_id != capability.id or raw is None or not secrets.compare_digest(lock.token_hash, service.digest(raw))):
        raise HTTPException(423, "This document is locked by another Office editor")
    return raw


@router.api_route(DAV, methods=["OPTIONS","PROPFIND","GET","HEAD","PUT","LOCK","UNLOCK"])
async def office_dav(session_id: str, secret: str, filename: str, request: Request, session=Depends(get_session)):
    method = request.method
    cap, record, document = service.capability(session, session_id, secret, writing=method in {"PUT","LOCK","UNLOCK"})
    if filename != cap.filename: raise HTTPException(404, "Office file unavailable")
    headers = {**PRIVATE,"DAV":"1, 2","Allow":"OPTIONS, PROPFIND, GET, HEAD, PUT, LOCK, UNLOCK","ETag":service.etag(record),"MS-Author-Via":"DAV"}
    if method == "OPTIONS": return Response(status_code=200,headers=headers)
    if method in {"GET","HEAD"}:
        content = service.bounded_file(record)
        # GET establishes the baseline a lock-token-only Office PUT must match.
        if method=="GET":
            cap.last_file_id = record.id; session.add(cap); session.commit()
        return Response(content if method=="GET" else b"",media_type=record.content_type,headers={**headers,"Content-Length":str(len(content)),"Content-Disposition":"attachment; filename*=UTF-8''"+quote(cap.filename,safe="")})
    if method == "PROPFIND":
        if request.headers.get("depth", "0") != "0": raise HTTPException(403,"Only this document is exposed")
        data = await request.body()
        if len(data)>16384: raise HTTPException(413,"DAV request too large")
        if data: service.safe_xml(data)
        session.expire_all(); cap,record,document=service.capability(session,session_id,secret)
        root=ET.Element("{DAV:}multistatus"); response=ET.SubElement(root,"{DAV:}response")
        ET.SubElement(response,"{DAV:}href").text=request.url.path
        propstat=ET.SubElement(response,"{DAV:}propstat"); prop=ET.SubElement(propstat,"{DAV:}prop")
        for key,value in [("displayname",cap.filename),("getcontentlength",str(record.size)),("getcontenttype",record.content_type),("getetag",service.etag(record))]: ET.SubElement(prop,"{DAV:}"+key).text=value
        ET.SubElement(prop,"{DAV:}resourcetype"); ET.SubElement(propstat,"{DAV:}status").text="HTTP/1.1 200 OK"
        supported=ET.SubElement(prop,"{DAV:}supportedlock"); entry=ET.SubElement(supported,"{DAV:}lockentry")
        ET.SubElement(ET.SubElement(entry,"{DAV:}lockscope"),"{DAV:}exclusive"); ET.SubElement(ET.SubElement(entry,"{DAV:}locktype"),"{DAV:}write")
        ET.SubElement(prop,"{DAV:}lockdiscovery")
        return xml_response(root,207,headers)
    lock=live_lock(session,document.id)
    if method == "UNLOCK":
        if lock is None: raise HTTPException(409,"No active Office lock")
        check_lock(request,lock,cap); session.delete(lock); session.commit(); return Response(status_code=204,headers=headers)
    if method == "LOCK":
        if request.headers.get("depth","0") != "0": raise HTTPException(403,"Only this document may be locked")
        body=await request.body()
        if len(body)>16384: raise HTTPException(413,"DAV request too large")
        if body:
            xml=service.safe_xml(body)
            if xml.tag!="{DAV:}lockinfo" or xml.find("{DAV:}lockscope/{DAV:}exclusive") is None or xml.find("{DAV:}locktype/{DAV:}write") is None: raise HTTPException(422,"Only exclusive write locks are supported")
        session.expire_all(); cap,record,document=service.capability(session,session_id,secret,writing=True)
        lock=live_lock(session,document.id)
        if lock:
            raw=check_lock(request,lock,cap)
            if body: raise HTTPException(423,"Document is already locked")
        else:
            if not body: raise HTTPException(409,"There is no lock to refresh")
            raw=secrets.token_urlsafe(32)
            lock=OfficeEditLock(document_id=document.id,session_id=cap.id,token_hash=service.digest(raw),expires_at=cap.expires_at)
        lock.expires_at=min(cap.expires_at,datetime.utcnow()+timedelta(minutes=10)); session.add(lock); session.commit()
        root=ET.Element("{DAV:}prop"); active=ET.SubElement(ET.SubElement(root,"{DAV:}lockdiscovery"),"{DAV:}activelock")
        ET.SubElement(ET.SubElement(active,"{DAV:}lockscope"),"{DAV:}exclusive"); ET.SubElement(ET.SubElement(active,"{DAV:}locktype"),"{DAV:}write")
        seconds=max(1,int((lock.expires_at-datetime.utcnow()).total_seconds()))
        ET.SubElement(active,"{DAV:}depth").text="0"; ET.SubElement(active,"{DAV:}timeout").text="Second-"+str(seconds)
        ET.SubElement(ET.SubElement(active,"{DAV:}lockroot"),"{DAV:}href").text=request.url.path
        ET.SubElement(ET.SubElement(active,"{DAV:}locktoken"),"{DAV:}href").text="opaquelocktoken:"+raw
        return xml_response(root,200,{**headers,"Lock-Token":"<opaquelocktoken:"+raw+">","Timeout":"Second-"+str(seconds)})
    # A PUT never replaces a blob. It allocates one immutable version only after
    # current authority, lock and base identity are checked under the writer fence.
    raw=check_lock(request,lock,cap); expected=request.headers.get("if-match")
    if expected is None:
        if lock is None or raw is None: raise HTTPException(428,"Use If-Match or a valid exclusive DAV lock")
        expected='"'+cap.last_file_id+'"'
    if expected != service.etag(record): raise HTTPException(412,"A newer version exists; reopen before saving")
    if request.headers.get("content-type",record.content_type).split(";",1)[0] != cap.content_type: raise HTTPException(415,"Document type cannot change in Office")
    content=bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content)>service.MAX_PREVIEW_FILE: raise HTTPException(413,"Office edit limit is 10 MiB")
    service.office_preview(bytes(content),cap.content_type)
    # Request body consumption is awaited; logout/removal may have happened.
    session.expire_all(); cap,record,document=service.capability(session,session_id,secret,writing=True)
    check_lock(request,live_lock(session,document.id),cap)
    if expected != service.etag(record): raise HTTPException(412,"A newer version exists; reopen before saving")
    new_id=uuid4().hex; path=None
    try:
        path=await storage_service.save_file(new_id,bytes(content))
        session.expire_all(); cap,record,document=service.capability(session,session_id,secret,writing=True)
        check_lock(request,live_lock(session,document.id),cap)
        if expected != service.etag(record): raise HTTPException(412,"A newer version exists; reopen before saving")
        replacement=File(id=new_id,channel_id=cap.channel_id,uploader_id=cap.user_id,filename=cap.filename,content_type=cap.content_type,size=len(content),storage_path=path,encrypted=False)
        session.add(replacement); session.flush()
        session.add(FileVersion(file_id=new_id,document_id=document.id,number=document.version_count+1))
        document.current_file_id=new_id; document.version_count+=1; cap.last_file_id=new_id
        session.add(document); session.add(cap); restore_synced_file(session,document.id)
        from ..services.webhooks import enqueue_channel_event
        enqueue_channel_event(session,cap.channel_id,"file.version.created",replacement.id)
        session.commit()
    except Exception:
        session.rollback()
        if path: await storage_service.delete_file(new_id)
        raise
    return Response(status_code=204,headers={**headers,"ETag":service.etag(replacement),"X-FreeCollab-Version":str(document.version_count)})
