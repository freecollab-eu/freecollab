"""Immutable decision revisions with explicit server/Matrix content modes."""
from datetime import date
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from nacl.exceptions import CryptoError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.collaboration import Decision, DecisionRevision
from ..models.message import Message
from ..services.access import require_channel_member, lock_channel_writes
from ..services.workspaces import require_direct_message_writable
from ..services.auth import get_current_user
from ..services.crypto import encrypt_message, decrypt_message
from ..services.chat_notifications import require_native_channel

router = APIRouter(tags=["decisions"])
Reference = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class DecisionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    status: Literal["proposed", "accepted", "superseded"] | None = None
    responsible_user_id: Reference | None = None
    decision_date: date | None = None
    source_message_id: Reference | None = None
    matrix_event_id: Reference | None = None

    @model_validator(mode="after")
    def content_mode(self):
        if self.matrix_event_id is not None:
            if any(getattr(self, field) is not None for field in ("title", "content", "status", "responsible_user_id", "decision_date", "source_message_id")):
                raise ValueError("Encrypted decisions accept only an opaque event reference")
        elif self.title is None or not self.title.strip() or self.content is None or not self.content.strip():
            raise ValueError("A decision needs a title and content")
        return self


class ReviseDecision(DecisionContent):
    expected_revision: int = Field(ge=1)


def decision_record(session, decision_id: str, user_id: str):
    decision = session.get(Decision, decision_id)
    if not decision:
        raise HTTPException(404, "Decision not found")
    member = require_channel_member(session, decision.channel_id, user_id)
    return decision, member


def validate_content(session, channel_id: str, data: DecisionContent, user_id: str):
    from ..services.matrix_bridge import is_secure_channel
    secure = is_secure_channel(session, channel_id)
    if data.matrix_event_id is not None:
        if not secure:
            raise HTTPException(400, "Encrypted decisions require an encrypted channel")
        from ..services.matrix_bridge import validate_secure_event
        validate_secure_event(session, channel_id, data.matrix_event_id, user_id)
        return
    if secure:
        raise HTTPException(409, "This channel requires an encrypted decision event")
    from ..services.enterprise_governance import enforce_native_document
    enforce_native_document(session, channel_id, user_id, data.model_dump(mode="json", exclude={"matrix_event_id"}))
    if data.responsible_user_id:
        try:
            require_channel_member(session, channel_id, data.responsible_user_id)
        except HTTPException:
            raise HTTPException(400, "The responsible person must be a current channel member") from None
    if data.source_message_id:
        source = session.get(Message, data.source_message_id)
        if not source or source.deleted or source.channel_id != channel_id:
            raise HTTPException(404, "Source message not found in channel")


def new_revision(decision: Decision, data: DecisionContent, number: int, actor_id: str):
    payload = data.model_dump(mode="json", exclude={"expected_revision", "matrix_event_id"})
    payload["status"] = payload["status"] or "proposed"
    payload["decision_id"] = decision.id
    payload["revision"] = number
    return DecisionRevision(
        decision_id=decision.id, revision=number, created_by=actor_id,
        content_mode="matrix" if data.matrix_event_id else "server",
        matrix_event_id=data.matrix_event_id,
        encrypted_payload=None if data.matrix_event_id else encrypt_message(json.dumps(payload, ensure_ascii=False), decision.channel_id),
    )


def revision_data(session, decision: Decision, revision: DecisionRevision):
    result = {"id": decision.id, "channel_id": decision.channel_id, "created_by": decision.created_by,
              "created_at": decision.created_at, "revision": revision.revision,
              "revision_id": revision.id, "revised_by": revision.created_by, "revised_at": revision.created_at,
              "content_mode": revision.content_mode, "matrix_event_id": revision.matrix_event_id}
    if revision.content_mode == "server":
        require_native_channel(session, decision.channel_id)
        try:
            payload = json.loads(decrypt_message(revision.encrypted_payload, decision.channel_id))
            if payload.get("decision_id") != decision.id or payload.get("revision") != revision.revision:
                raise ValueError("Decision context mismatch")
        except (CryptoError, ValueError, TypeError, AttributeError):
            raise HTTPException(409, "Stored decision could not be decrypted") from None
        result.update({key: payload.get(key) for key in ("title", "content", "status", "responsible_user_id", "decision_date", "source_message_id")})
    return result


@router.get("/channels/{channel_id}/decisions")
def get_decisions(channel_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_channel_member(session, channel_id, user.id)
    decisions = session.exec(select(Decision).where(Decision.channel_id == channel_id).order_by(Decision.created_at.desc())).all()
    result = []
    for decision in decisions:
        latest = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == decision.id).order_by(DecisionRevision.revision.desc()).limit(1)).first()
        if latest:
            result.append(revision_data(session, decision, latest))
    return result


@router.post("/channels/{channel_id}/decisions")
def create_decision(channel_id: str, data: DecisionContent, user=Depends(get_current_user), session=Depends(get_session)):
    lock_channel_writes(session, channel_id)
    require_channel_member(session, channel_id, user.id)
    require_direct_message_writable(session, channel_id, user.id)
    validate_content(session, channel_id, data, user.id)
    decision = Decision(channel_id=channel_id, created_by=user.id)
    revision = new_revision(decision, data, 1, user.id)
    session.add(decision)
    session.flush()
    session.add(revision)
    from ..services.webhooks import enqueue_channel_event
    enqueue_channel_event(session, channel_id, "decision.created", decision.id)
    session.commit()
    return revision_data(session, decision, revision)


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    decision, _ = decision_record(session, decision_id, user.id)
    latest = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == decision.id).order_by(DecisionRevision.revision.desc()).limit(1)).first()
    if not latest:
        raise HTTPException(404, "Decision revision not found")
    return revision_data(session, decision, latest)


@router.get("/decisions/{decision_id}/revisions")
def decision_revisions(decision_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    decision, _ = decision_record(session, decision_id, user.id)
    revisions = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == decision.id).order_by(DecisionRevision.revision.desc())).all()
    return [revision_data(session, decision, revision) for revision in revisions]


@router.post("/decisions/{decision_id}/revisions")
def revise_decision(decision_id: str, data: ReviseDecision, user=Depends(get_current_user), session=Depends(get_session)):
    decision, member = decision_record(session, decision_id, user.id)
    lock_channel_writes(session, decision.channel_id)
    member = require_channel_member(session, decision.channel_id, user.id)
    require_direct_message_writable(session, decision.channel_id, user.id)
    if decision.created_by != user.id and member.role != "admin":
        raise HTTPException(403, "Only the author or channel administrator can revise this decision")
    validate_content(session, decision.channel_id, data, user.id)
    latest = session.exec(select(DecisionRevision).where(DecisionRevision.decision_id == decision.id).order_by(DecisionRevision.revision.desc()).limit(1)).first()
    if not latest or latest.revision != data.expected_revision:
        raise HTTPException(409, "Decision has changed; reload its current revision")
    revision = new_revision(decision, data, latest.revision + 1, user.id)
    session.add(revision)
    try:
        from ..services.webhooks import enqueue_channel_event
        enqueue_channel_event(session, decision.channel_id, "decision.updated", revision.id)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Decision has changed; reload its current revision") from None
    return revision_data(session, decision, revision)
