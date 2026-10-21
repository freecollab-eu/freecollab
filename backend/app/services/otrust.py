"""Durable, hash-only OTRUST adapter. No provider request occurs in an API call.

OpenTimestamps validates the serialized proof and its commitment locally. A
provider's Bitcoin attestation is deliberately not treated as a verified chain.
"""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
import time
from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, or_, update
from sqlmodel import Session, select

from ..config import settings
from ..models.version_proof import VersionProof

MAX_PROOF_BYTES = 1024 * 1024
MAX_JSON_BYTES = 16 * 1024


def available() -> bool:
    return bool(getattr(settings, "otrust_enabled", False))


def provider_origin() -> str:
    value = getattr(settings, "otrust_base_url", "https://www.otrust.eu").rstrip("/")
    parsed = urlsplit(value)
    local_http = getattr(settings, "environment", "production") != "production" and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in value):
        raise ValueError("OTRUST_BASE_URL must be an HTTPS origin without credentials")
    return value


def require_available():
    if not available():
        raise HTTPException(503, "Timestamp integration is not configured on this server")
    return provider_origin()


def decision_bytes(payload: dict) -> bytes:
    """Versioned canonical format shared with the client (ordered JSON array).

    SHA-256 commits to these UTF-8 bytes, not a screen rendering or a mutable
    document. JSON scalar array order avoids object-key/number canonicalization.
    """
    return json.dumps([
        "freecollab-decision-v1", payload["decision_id"], payload["revision"],
        payload["title"], payload["content"], payload["status"],
        payload.get("responsible_user_id"), payload.get("decision_date"),
        payload.get("source_message_id"),
    ], ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def validate_proof(content: bytes, digest: str) -> dict:
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.serialize import BytesDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile
    if not content or len(content) > MAX_PROOF_BYTES:
        raise ValueError("Timestamp proof has an invalid size")
    detached = DetachedTimestampFile.deserialize(BytesDeserializationContext(content))
    if not isinstance(detached.file_hash_op, OpSHA256) or detached.file_digest != bytes.fromhex(digest):
        raise ValueError("Timestamp proof does not commit to this version digest")
    has_bitcoin = any(isinstance(attestation, BitcoinBlockHeaderAttestation)
                      for _, attestation in detached.timestamp.all_attestations())
    return {"commitment_checked": True, "bitcoin_attestation_present": has_bitcoin, "bitcoin_verified": False}


class ProviderFailure(Exception):
    def __init__(self, delay: int = 60):
        self.delay = min(86400, max(30, delay))


def retry_delay(value: str | None) -> int:
    try:
        return min(86400, max(30, int(value)))
    except (ValueError, TypeError):
        try:
            point = parsedate_to_datetime(value)
            if point.tzinfo is None:
                point = point.replace(tzinfo=timezone.utc)
            return min(86400, max(30, int((point - datetime.now(timezone.utc)).total_seconds())))
        except (ValueError, TypeError, OverflowError):
            return 60


def bounded_response(client, method, url, *, maximum, **kwargs):
    with client.stream(method, url, **kwargs) as response:
        if response.status_code in (202, 429, 503):
            return response.status_code, b"", retry_delay(response.headers.get("Retry-After"))
        if response.status_code != 200:
            raise ProviderFailure(retry_delay(response.headers.get("Retry-After")))
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > maximum:
                raise ValueError("Timestamp provider response is too large")
        return response.status_code, bytes(content), 60


def advance(row: VersionProof, client) -> int:
    """One finite provider interaction; the stable local ID is its retry key."""
    origin = provider_origin()
    if row.provider_origin != origin:
        raise ValueError("Provider configuration changed; an existing receipt stays bound to its original provider")
    if not row.receipt_id:
        status, content, delay = bounded_response(client, "POST", origin + "/claim/simple", maximum=MAX_JSON_BYTES,
            json={"hash": row.sha256, "source": "api"}, headers={"Idempotency-Key": "freecollab-proof-" + row.id})
        if status != 200:
            raise ProviderFailure(delay)
        result = json.loads(content)
        receipt = result.get("receipt_id")
        if not isinstance(receipt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", receipt):
            raise ValueError("Timestamp provider returned an invalid receipt")
        if result.get("hash") != row.sha256:
            raise ValueError("Timestamp provider returned a different digest")
        row.receipt_id = receipt
        timestamp = result.get("timestamp")
        row.provider_timestamp = timestamp if isinstance(timestamp, str) and len(timestamp) <= 100 else None
        row.state = "pending"
        row.failure = None
        row.failure_count = 0
        return 60
    status, content, delay = bounded_response(client, "GET", origin + "/proof/" + quote(row.receipt_id, safe=""), maximum=MAX_PROOF_BYTES)
    if status != 200:
        if status != 202:
            raise ProviderFailure(delay)
        row.state = "pending"
        row.failure = None
        row.failure_count = 0
        return delay
    result = validate_proof(content, row.sha256)
    row.proof_data = content
    for name, value in result.items():
        setattr(row, name, value)
    row.state = "proof_available"
    row.failure = None
    row.failure_count = 0
    return 3600


def authorize_queued_request(session, row):
    """A queued disclosure cannot start after its requesting access was removed."""
    if row.receipt_id:
        return  # Fetching an already-published proof discloses no new digest.
    from .access import require_channel_member
    from .workspaces import require_direct_message_writable
    from ..models.user import User
    user = session.get(User, row.requested_by)
    if not user or user.is_blocked:
        raise ValueError("Timestamp requester is no longer active")
    try:
        require_channel_member(session, row.channel_id, row.requested_by)
        require_direct_message_writable(session, row.channel_id, row.requested_by)
    except HTTPException:
        raise ValueError("Timestamp request access has ended") from None


def process_pending(session: Session, limit: int = 20, client=None) -> int:
    if not available():
        return 0
    now = datetime.utcnow()
    eligible = or_(VersionProof.state.in_(["queued", "pending"]), and_(VersionProof.state == "processing", VersionProof.claimed_at < now - timedelta(minutes=5)))
    identifiers = session.exec(select(VersionProof.id).where(eligible, VersionProof.next_attempt_at <= now)
        .order_by(VersionProof.created_at).limit(max(1, min(100, limit)))).all()
    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=min(30, max(1, getattr(settings, "otrust_timeout_seconds", 10))), follow_redirects=False, trust_env=True)
    completed = 0
    try:
        for identifier in identifiers:
            claim = session.exec(update(VersionProof).where(VersionProof.id == identifier, eligible, VersionProof.next_attempt_at <= now)
                .values(state="processing", claimed_at=now, attempts=VersionProof.attempts + 1))
            session.commit()
            if claim.rowcount != 1:
                continue
            row = session.get(VersionProof, identifier)
            try:
                authorize_queued_request(session, row)
                delay = advance(row, client)
            except Exception as error:
                row.failure_count += 1
                row.state = "failed" if row.failure_count >= 8 or isinstance(error, ValueError) else "pending" if row.receipt_id else "queued"
                row.failure = "Timestamp provider request or proof validation failed. No document content was sent."
                delay = error.delay if isinstance(error, ProviderFailure) else min(3600, 30 * 2 ** min(row.failure_count, 7))
            else:
                completed += 1
            row.updated_at = datetime.utcnow()
            row.next_attempt_at = row.updated_at + timedelta(seconds=delay)
            session.add(row)
            session.commit()
    finally:
        if owned_client:
            client.close()
    return completed


def main():
    parser = argparse.ArgumentParser(description="Process explicitly requested hash-only timestamp receipts")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    from ..database import engine, init_db
    # All models must be registered for additive metadata/foreign keys.
    from ..main import socket_app  # noqa: F401
    init_db()
    while True:
        with Session(engine) as session:
            process_pending(session)
        if args.once:
            return
        time.sleep(max(1, min(60, args.interval)))


if __name__ == "__main__":
    main()
