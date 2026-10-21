"""Explicit opt-in version timestamps with current channel access enforcement."""
from datetime import datetime
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.collaboration import Decision, DecisionRevision
from ..models.file import File
from ..models.version_proof import VersionProof
from ..services.access import require_channel_member, lock_channel_writes
from ..services.auth import get_current_user
from ..services.crypto import decrypt_message
from ..services.matrix_bridge import is_secure_channel, validate_secure_event
from ..services.otrust import available, provider_origin, require_available, decision_bytes
from ..services.storage import storage_service
from ..services.workspaces import require_direct_message_writable

router = APIRouter(tags=["version-proofs"])


class RequestProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: Literal["file", "decision"]
    target_id: str = Field(min_length=1, max_length=1024)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    consent: Literal[True]


def digest_for(session, channel_id, data, user_id):
    secure = is_secure_channel(session, channel_id)
    if data.target_kind == "file":
        if secure:
            validate_secure_event(session, channel_id, data.target_id, user_id, require_sender=False)
        else:
            record = session.get(File, data.target_id)
            if not record or record.channel_id != channel_id:
                raise HTTPException(404, "File version not found in this channel")
            try:
                content = storage_service.read_file(record.id, max_bytes=50 * 1024 * 1024, expected_size=record.size)
                if content is None:
                    raise ValueError("Missing exact version")
                digest = hashlib.sha256(content).hexdigest()
            except (OSError, ValueError):
                raise HTTPException(409, "The exact file version is unavailable") from None
    else:
        revision = session.get(DecisionRevision, data.target_id)
        decision = session.get(Decision, revision.decision_id) if revision else None
        if not revision or not decision or decision.channel_id != channel_id:
            raise HTTPException(404, "Decision revision not found in this channel")
        if secure:
            if revision.content_mode != "matrix" or not revision.matrix_event_id:
                raise HTTPException(409, "Encrypted revision is unavailable")
            validate_secure_event(session, channel_id, revision.matrix_event_id, user_id, require_sender=False)
        else:
            if revision.content_mode != "server" or not revision.encrypted_payload:
                raise HTTPException(409, "Decision revision is unavailable")
            try:
                payload = json.loads(decrypt_message(revision.encrypted_payload, channel_id))
                if payload.get("decision_id") != decision.id or payload.get("revision") != revision.revision:
                    raise ValueError("Revision context mismatch")
                digest = hashlib.sha256(decision_bytes(payload)).hexdigest()
            except Exception:
                raise HTTPException(409, "The exact decision revision is unavailable") from None
    if secure:
        if not data.sha256:
            raise HTTPException(400, "Encrypted versions require a digest computed on your device")
        return data.sha256, "client_asserted"
    if data.sha256 and data.sha256 != digest:
        raise HTTPException(409, "The supplied digest does not match the exact stored version")
    return digest, "server_bytes"


def record_data(row):
    return {name: getattr(row, name) for name in (
        "id", "channel_id", "target_kind", "target_id", "sha256", "content_binding", "requested_by",
        "provider_origin", "state", "receipt_id", "provider_timestamp", "attempts", "failure",
        "created_at", "updated_at", "next_attempt_at", "commitment_checked", "bitcoin_attestation_present", "bitcoin_verified",
    )} | {"proof_available": row.proof_data is not None}


def proof_record(session, proof_id, user_id):
    row = session.get(VersionProof, proof_id)
    if not row:
        raise HTTPException(404, "Timestamp record not found")
    member = require_channel_member(session, row.channel_id, user_id)
    return row, member


@router.get("/proofs/status")
def status(user=Depends(get_current_user)):
    return {"enabled": available(), "provider_url": provider_origin(), "hash_algorithm": "SHA-256",
            "automatic_bitcoin_verification": False}


@router.get("/channels/{channel_id}/proofs")
def list_proofs(channel_id: str, target_kind: Literal["file", "decision"] | None = None,
                target_id: str | None = Query(None, max_length=1024), user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    query = select(VersionProof).where(VersionProof.channel_id == channel_id)
    if target_kind:
        query = query.where(VersionProof.target_kind == target_kind)
    if target_id:
        query = query.where(VersionProof.target_id == target_id)
    rows = session.exec(query.order_by(VersionProof.created_at.desc()).limit(200)).all()
    return [record_data(row) for row in rows]


@router.post("/channels/{channel_id}/proofs")
def request_proof(channel_id: str, data: RequestProof, user=Depends(get_current_user), session=Depends(get_session)):
    origin = require_available()
    lock_channel_writes(session, channel_id)
    require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    digest, binding = digest_for(session, channel_id, data, user.id)
    existing = session.exec(select(VersionProof).where(VersionProof.channel_id == channel_id,
        VersionProof.target_kind == data.target_kind, VersionProof.target_id == data.target_id, VersionProof.sha256 == digest)).first()
    if existing:
        return record_data(existing)
    pending = session.exec(select(VersionProof.id).where(VersionProof.requested_by == user.id,
        VersionProof.state.in_(["queued", "pending", "processing"])).limit(50)).all()
    if len(pending) >= 50:
        raise HTTPException(429, "Finish existing timestamp requests before creating more")
    row = VersionProof(channel_id=channel_id, target_kind=data.target_kind, target_id=data.target_id,
        sha256=digest, content_binding=binding, requested_by=user.id, provider_origin=origin)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(select(VersionProof).where(VersionProof.channel_id == channel_id,
            VersionProof.target_kind == data.target_kind, VersionProof.target_id == data.target_id, VersionProof.sha256 == digest)).first()
        if not existing:
            raise HTTPException(409, "Another request completed first; refresh timestamps") from None
        row = existing
    return record_data(row)


@router.post("/proofs/{proof_id}/refresh")
def refresh_proof(proof_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_available()
    row, member = proof_record(session, proof_id, user.id)
    lock_channel_writes(session, row.channel_id)
    member = require_channel_member(session, row.channel_id, user.id)
    require_direct_message_writable(session, row.channel_id, user.id)
    if row.requested_by != user.id and member.role != "admin":
        raise HTTPException(403, "Only the requester or channel administrator can retry this timestamp")
    if row.state == "processing":
        raise HTTPException(409, "This timestamp request is already processing")
    now = datetime.utcnow()
    if row.state in {"queued", "pending"} and row.next_attempt_at > now:
        return record_data(row)  # Keep provider Retry-After/backoff intact.
    row.state = "queued"
    row.next_attempt_at = now
    row.updated_at = now
    row.failure_count = 0
    row.failure = None
    session.add(row)
    session.commit()
    return record_data(row)


@router.get("/proofs/{proof_id}/download")
def download_proof(proof_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    row, _ = proof_record(session, proof_id, user.id)
    if not row.proof_data or not row.commitment_checked:
        raise HTTPException(409, "The timestamp proof is not available yet")
    return Response(row.proof_data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="freecollab-version-{row.id}.ots"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })
