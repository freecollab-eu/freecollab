"""Durable reviewed native AI work, explicit profiles and bounded usage.

Provider calls are outside transactions. Each attempt has a durable reservation
and a lease; an interrupted call is uncertain and needs explicit retry consent.
No secure-channel prompt, selected text or answer is written to this queue.
"""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import math
import re
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import case, delete, func, or_, update
from sqlmodel import Session, select

from ..models.ai_runtime import AIBudget, AIModelProfile, AIPersonalKey, AIProfileConsent, AIReadTool, AIRun, AIRunApproval, AIRunSource, AIRunStep, AIUsage
from ..models.copilot import CopilotConsent
from ..models.user import User
from ..models.account_security import AccountSecurity
from . import ai_providers, copilot, request_guard
from .account_security import _secret_key
from .workspaces import lock_workspace, require_workspace_member

LEASE_SECONDS = 180
MAX_RUNS = 1000
TERMINAL = {"completed", "failed", "cancelled"}
OPERATIONS = Literal["draft", "summarize", "task_plan", "group", "compare", "answer"]


class ResourceSource(copilot.Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    kind: Literal["resource"]
    id: str = Field(min_length=1, max_length=1024)
    reference: str = Field(min_length=1, max_length=10000)
    expected_text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class PlanStep(copilot.Strict):
    operation: OPERATIONS
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    expected_profile_revision: int = Field(ge=1, strict=True)
    instructions: str = Field(default="", max_length=1000)


class DisclosureScope(copilot.Strict):
    kind: Literal["meeting_recording"]
    id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, strict=True)


class RunInput(copilot.Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    channel_id: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(default="default", pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    expected_profile_revision: int = Field(ge=1, strict=True)
    operation: OPERATIONS = "draft"
    prompt: str = Field(min_length=1, max_length=4000)
    sources: list[copilot.Source | ResourceSource] = Field(default_factory=list, max_length=8)
    selected_text: str = Field(default="", max_length=24000)
    max_steps: int = Field(default=1, ge=1, le=3, strict=True)
    max_tokens: int = Field(default=2048, ge=256, le=8192, strict=True)
    disclose_to_provider: bool
    disclose_decrypted_content: bool = False
    disclose_cross_channel_sources: bool = False
    target_project_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    plan: list[PlanStep] = Field(default_factory=list, max_length=3)
    disclosure_scope: DisclosureScope | None = None
    reviewed_source_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("reviewed_source_ids")
    @classmethod
    def device_catalog(cls, values):
        if len(set(values)) != len(values) or any(not 1 <= len(value) <= 1024 or any(ord(char) < 32 or ord(char) == 127 for char in value) for value in values):
            raise ValueError("Use unique bounded reviewed device source identifiers")
        return values


def subject(workspace, profile):
    return workspace + ":" + profile


def provider_identity(provider):
    return hmac.new(_secret_key(), copilot.dump(provider.model_dump()).encode(), hashlib.sha256).hexdigest()


def native_policy(session, channel, actor, text):
    from .enterprise_governance import enforce_native_content
    enforce_native_content(session, channel, actor, text, surface="assistant")


def lock_actor_workspace(session, workspace, actor):
    """Match worker/privacy lock order before user-bound FK writes."""
    session.execute(update(User).where(User.id == actor).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == actor).values(auth_version=AccountSecurity.auth_version))
    lock_workspace(session, workspace); session.expire_all(); request_guard.recheck_actor(session, actor)


def lock_source_channels(session, channel, refs):
    from .access import lock_channel_writes
    for identifier in sorted({channel, *(ref.get("channel_id", channel) for ref in refs)}):
        lock_channel_writes(session, identifier)
    session.expire_all()


def lock_input_sources(session, workspace, channel, actor, sources):
    """Discover scope, then fence every source before a fresh extraction.

    Discovery is not a source snapshot: source_material resolves again while
    these locks remain held until the caller commits its sealed job/receipt.
    """
    lock_actor_workspace(session, workspace, actor)
    secure = copilot.grant(session, workspace, channel, actor)
    descriptors = []
    for source in sources:
        if not isinstance(source, ResourceSource):
            continue
        if secure:
            raise HTTPException(422, "Search citations cannot disclose encrypted-channel text")
        from .search_sources import resolve
        from ..models.workspace import WorkspaceChannel
        item = resolve(session, actor, source.reference, commit=False)
        mapping = session.get(WorkspaceChannel, item["channel_id"])
        if item["id"] != source.id or not mapping or mapping.workspace_id != workspace:
            raise HTTPException(422, "Select sources from this workspace")
        descriptors.append({"channel_id": item["channel_id"]})
    lock_source_channels(session, channel, descriptors)
    if copilot.grant(session, workspace, channel, actor) != secure:
        raise HTTPException(409, "The selected channel encryption changed")
    return secure


def fields(row):
    return ai_providers.Provider.model_validate(copilot.opened(row.encrypted_config, "profile", subject(row.workspace_id, row.profile_id)))


def profile_record(session, workspace, profile_id):
    if not copilot.enabled():
        raise HTTPException(409, "AI is disabled by this installation")
    if profile_id == "default":
        config = copilot.configuration(session, workspace)
        old = copilot.config_fields(config)
        budget = session.get(AIBudget, workspace, populate_existing=True)
        return config.revision, ai_providers.Provider(name="Workspace default", endpoint=old.endpoint, model=old.model, api_key=old.api_key, instructions=old.agents[0].instructions if old.agents else "",
            input_cost_per_million_micros=budget.default_input_cost_per_million_micros if budget else 0,
            output_cost_per_million_micros=budget.default_output_cost_per_million_micros if budget else 0)
    row = session.get(AIModelProfile, (workspace, profile_id), populate_existing=True)
    if not row or not row.enabled:
        raise HTTPException(409, "The selected model profile is unavailable")
    try:
        return row.revision, fields(row)
    except ValueError:
        raise HTTPException(409, "The model destination is no longer permitted by installation policy") from None


def resolve_profile(session, workspace, profile_id, user_id, revision, *, consent=True):
    actual, provider = profile_record(session, workspace, profile_id)
    if actual != revision:
        raise HTTPException(409, "Model profile changed; review its destination again")
    if consent:
        if profile_id == "default":
            copilot.consent_for(session, workspace, user_id, revision)
        else:
            row = session.get(AIProfileConsent, (workspace, profile_id, user_id), populate_existing=True)
            if not row or not row.enabled or row.revision != revision:
                raise HTTPException(403, "Opt in to this exact model profile revision")
    if provider.key_source == "personal":
        key = session.get(AIPersonalKey, (workspace, profile_id, user_id), populate_existing=True)
        if not key or key.profile_revision != revision:
            raise HTTPException(409, "Add your personal key for the reviewed model profile")
        private = copilot.opened(key.encrypted_key, "personal-key", subject(workspace, profile_id) + ":" + user_id)
        try:
            provider = ai_providers.Provider.model_validate({**provider.model_dump(), "api_key": private["api_key"]})
        except ValueError:
            raise HTTPException(409, "Replace the invalid personal model credential") from None
    return provider


def profile_summaries(session, workspace, user_id):
    records = []
    config = copilot.configuration(session, workspace, active=False)
    if config:
        old = copilot.config_fields(config)
        budget = session.get(AIBudget, workspace)
        consent = session.get(CopilotConsent, (workspace, user_id))
        records.append({"id": "default", "name": "Workspace default", "revision": config.revision, "protocol": "openai", "endpoint": old.endpoint,
            "instructions": old.agents[0].instructions if old.agents else "", "api_version": "2024-10-21",
            "model": old.model, "enabled": config.enabled, "key_source": "workspace", "configured": True, "consented": bool(consent and consent.enabled and consent.config_revision == config.revision),
            "personal_key_configured": False, "editable": False, "max_output_tokens": 2048,
            "input_cost_per_million_micros": budget.default_input_cost_per_million_micros if budget else 0,
            "output_cost_per_million_micros": budget.default_output_cost_per_million_micros if budget else 0})
    for row in session.exec(select(AIModelProfile).where(AIModelProfile.workspace_id == workspace).order_by(AIModelProfile.profile_id)).all():
        try:
            provider = fields(row)
        except ValueError:
            raw = copilot.opened(row.encrypted_config, "profile", subject(row.workspace_id, row.profile_id))
            records.append({"id": row.profile_id, **{key: value for key, value in raw.items() if key != "api_key"}, "revision": row.revision,
                "enabled": False, "configured": False, "consented": False, "personal_key_configured": False, "editable": True,
                "configuration_error": "destination_policy_changed"})
            continue
        consent = session.get(AIProfileConsent, (workspace, row.profile_id, user_id))
        key = session.get(AIPersonalKey, (workspace, row.profile_id, user_id))
        personal = bool(key and key.profile_revision == row.revision)
        records.append({"id": row.profile_id, **provider.model_dump(exclude={"api_key"}), "revision": row.revision,
            "enabled": row.enabled, "configured": provider.key_source == "workspace" or personal, "consented": bool(consent and consent.enabled and consent.revision == row.revision),
            "personal_key_configured": personal, "editable": True})
    return records


def period_start():
    return datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def budget_status(session, workspace):
    budget = session.get(AIBudget, workspace, populate_existing=True)
    totals = session.exec(select(func.coalesce(func.sum(AIUsage.charged_tokens), 0),
        func.coalesce(func.sum(case((AIUsage.status == "reserved", AIUsage.reserved_tokens), else_=0)), 0),
        func.coalesce(func.sum(AIUsage.estimated_cost_micros), 0),
        func.coalesce(func.sum(case((AIUsage.status == "reserved", AIUsage.reserved_cost_micros), else_=0)), 0),
        func.coalesce(func.sum(case((AIUsage.status == "unknown", 1), else_=0)), 0))
        .where(AIUsage.workspace_id == workspace, AIUsage.created_at >= period_start())).one()
    return {"revision": budget.revision if budget else 0, "period": "month", "max_tokens": budget.max_tokens if budget else 200000,
        "default_input_cost_per_million_micros": budget.default_input_cost_per_million_micros if budget else 0,
        "default_output_cost_per_million_micros": budget.default_output_cost_per_million_micros if budget else 0,
        "max_cost_micros": budget.max_cost_micros if budget else 0, "used_tokens": int(totals[0]), "reserved_tokens": int(totals[1]),
        "used_cost_micros": int(totals[2]), "reserved_cost_micros": int(totals[3]),
        "currency": "USD", "cost_is_estimate": True, "unknown_attempts": int(totals[4])}


def cost(provider, input_tokens, output_tokens):
    return (input_tokens * provider.input_cost_per_million_micros + output_tokens * provider.output_cost_per_million_micros + 999999) // 1000000


def reserve(session, workspace, actor, execution_id, step_index, attempt, profile_id, revision, provider, messages, max_tokens):
    lock_workspace(session, workspace)
    if session.exec(select(AIUsage.id).where(AIUsage.execution_id == execution_id, AIUsage.step_index == step_index, AIUsage.attempt == attempt)).first():
        raise HTTPException(409, "This provider attempt was already reserved; review a new request instead of retrying it blindly")
    current = budget_status(session, workspace)
    input_bound = len(copilot.dump(messages).encode("utf-8")) + 1024
    tokens = input_bound + max_tokens
    predicted = cost(provider, input_bound, max_tokens)
    if current["max_cost_micros"] and provider.protocol != "ollama" and not (provider.input_cost_per_million_micros or provider.output_cost_per_million_micros):
        raise HTTPException(409, "Set reviewed token prices on a named profile before using the monetary budget")
    if current["used_tokens"] + current["reserved_tokens"] + tokens > current["max_tokens"]:
        raise HTTPException(409, "The workspace AI token budget is exhausted")
    if current["max_cost_micros"] and current["used_cost_micros"] + current["reserved_cost_micros"] + predicted > current["max_cost_micros"]:
        raise HTTPException(409, "The workspace estimated AI spending budget is exhausted")
    row = AIUsage(workspace_id=workspace, user_id=actor, execution_id=execution_id, step_index=step_index, attempt=attempt,
        profile_id=profile_id, profile_revision=revision, reserved_tokens=tokens, reserved_cost_micros=predicted)
    session.add(row)
    session.flush()
    return row


def settle(session, identifier, provider, measured=None, *, sent=True):
    row = session.get(AIUsage, identifier, populate_existing=True)
    if not row or row.status not in {"reserved", "unknown"}:
        return
    reported = measured and measured.get("usage_reported") is True
    if reported:
        row.input_tokens = measured["input_tokens"]; row.output_tokens = measured["output_tokens"]
        row.charged_tokens = row.input_tokens + row.output_tokens
        row.estimated_cost_micros = cost(provider, row.input_tokens, row.output_tokens)
        row.usage_reported = True; row.status = "settled"
    elif sent:
        row.charged_tokens = row.reserved_tokens; row.estimated_cost_micros = row.reserved_cost_micros; row.status = "unknown"
    else:
        row.status = "released"
    session.add(row)


def reserve_external_call(session, workspace_id, channel_id, actor_id, execution_id, provider, messages, *, expected_revision, max_tokens=2048, attempt=1):
    """Existing AI engines share this reservation in their own transaction.

    Returns (ledger_id, runtime_provider). Caller MUST commit before networking,
    then call settle(...answer.get('usage')) and commit even on an uncertain
    provider error. No prompt, key or source text is retained in the ledger.
    """
    if copilot.grant(session, workspace_id, channel_id, actor_id):
        raise HTTPException(409, "Persistent app AI does not accept encrypted plaintext")
    actual = resolve_profile(session, workspace_id, "default", actor_id, expected_revision)
    if (actual.endpoint, actual.model, actual.api_key) != (provider.endpoint, provider.model, provider.api_key):
        raise HTTPException(409, "Reviewed provider configuration changed before reservation")
    ledger = reserve(session, workspace_id, actor_id, execution_id, 0, attempt, "default", expected_revision, actual, messages, max_tokens)
    return ledger.id, actual


def source_material(session, workspace, channel, actor, sources, *, secure=False, cross_channel=False):
    refs, pieces = [], []
    conversation = SimpleNamespace(channel_id=channel, secure=secure)
    for source in sources:
        if (source.kind, source.id) in {(item["kind"], item["id"]) for item in refs}:
            raise HTTPException(422, "Select each source once")
        if isinstance(source, ResourceSource):
            if secure:
                raise HTTPException(422, "Search citations cannot disclose encrypted-channel text")
            from .search_sources import resolve
            item = resolve(session, actor, source.reference, commit=False)
            from ..models.workspace import WorkspaceChannel
            mapping = session.get(WorkspaceChannel, item["channel_id"])
            if item["id"] != source.id or not mapping or mapping.workspace_id != workspace:
                raise HTTPException(422, "Select sources from this workspace")
            if item["channel_id"] != channel and not cross_channel:
                raise HTTPException(422, "Explicitly review disclosure of sources from other channels")
            text = item["title"] + "\n" + item["text"]
            digest = hashlib.sha256(text.encode()).hexdigest()
            if source.expected_text_sha256 and source.expected_text_sha256 != digest:
                raise HTTPException(409, "Selected search content changed")
            native_policy(session, item["channel_id"], actor, text)
            # Store a server-derived canonical descriptor, never a client
            # capability whose ten-minute lifetime would break durable jobs.
            ref = {"kind": "resource", "id": item["id"], "source_kind": item["kind"], "channel_id": item["channel_id"], "version": item["version"], "fingerprint": digest}
        else:
            ref, text = copilot.source_text(session, conversation, source, actor)
        ref["citation"] = "S" + str(len(refs) + 1)
        refs.append(ref); pieces.append("[" + ref["citation"] + "] " + source.kind + " " + source.id + "\n" + text)
    value = "\n\n".join(pieces)
    if len(value) > copilot.MAX_CONTEXT:
        raise HTTPException(413, "Select at most 24,000 characters of AI context")
    return refs, value


def check_refs(session, workspace, channel, actor, refs, *, read_only=False):
    simple = []; public_refs = [dict(ref) for ref in refs]
    for index, ref in enumerate(refs):
        if ref["kind"] == "resource":
            from .search_sources import resolve_canonical
            from ..models.workspace import WorkspaceChannel
            item = resolve_canonical(session, actor, ref["source_kind"], ref["id"], ref["channel_id"], ref["version"], commit=False)
            mapping = session.get(WorkspaceChannel, item["channel_id"])
            if (item["id"] != ref["id"] or item["channel_id"] != ref.get("channel_id", channel) or not mapping or mapping.workspace_id != workspace
                    or item["version"] != ref["version"] or hashlib.sha256((item["title"] + "\n" + item["text"]).encode()).hexdigest() != ref["fingerprint"]):
                raise HTTPException(409, "Selected search source changed")
            if read_only:
                from .enterprise_governance import native_content_allowed
                if not native_content_allowed(session, item["channel_id"], item["title"] + "\n" + item["text"], surface="assistant"):
                    raise HTTPException(403, "The selected source is restricted by native policy")
            else:
                native_policy(session, item["channel_id"], actor, item["title"] + "\n" + item["text"])
            public_refs[index]["reference"] = item["reference"]
        else:
            simple.append(ref)
    copilot.check_sources(session, SimpleNamespace(channel_id=channel, secure=False), simple, actor)
    return public_refs


def create_run(session, workspace, actor, data):
    secure = lock_input_sources(session, workspace, data.channel_id, actor, data.sources)
    if secure:
        raise HTTPException(409, "Encrypted AI runs execute in this device; use the foreground completion route")
    if data.reviewed_source_ids:
        raise HTTPException(422, "Native queued work requires actual authorized source references, not a device-only catalog")
    if data.disclosure_scope and len(data.sources) == 1:
        canonical = "meeting_" + hashlib.sha256(copilot.dump([workspace, actor, data.disclosure_scope.id, data.disclosure_scope.revision, data.sources[0].id]).encode()).hexdigest()
        data = data.model_copy(update={"request_id": canonical})
    provider = resolve_profile(session, workspace, data.profile_id, actor, data.expected_profile_revision)
    specs = data.plan or [PlanStep(operation=data.operation, profile_id=data.profile_id, expected_profile_revision=data.expected_profile_revision) for _ in range(data.max_steps)]
    if len(specs) > data.max_steps:
        raise HTTPException(422, "The reviewed plan exceeds its explicit maximum step count")
    if (specs[0].profile_id, specs[0].expected_profile_revision, specs[0].operation) != (data.profile_id, data.expected_profile_revision, data.operation):
        raise HTTPException(422, "The first planned recipient and operation must match the reviewed primary selection")
    plan = []
    for spec in specs:
        recipient = resolve_profile(session, workspace, spec.profile_id, actor, spec.expected_profile_revision)
        plan.append({**spec.model_dump(), "provider_identity": provider_identity(recipient)})
    if data.disclosure_scope and (len(plan) != 1 or data.operation != "summarize" or data.selected_text or any(source.kind != "clip" for source in data.sources) or len(data.sources) != 1):
        raise HTTPException(422, "Meeting disclosure grants authorize one summary of one recording clip")
    if not data.disclose_to_provider:
        raise HTTPException(422, "Review the actual provider and explicitly approve this disclosure")
    authority = session.info.get(request_guard._KEY)
    if not authority:
        raise HTTPException(401, "A current authenticated session is required for queued AI work")
    digest = hmac.new(_secret_key(), (workspace + actor + copilot.dump(data.model_dump())).encode(), hashlib.sha256).hexdigest()
    existing = session.exec(select(AIRun).where(AIRun.workspace_id == workspace, AIRun.user_id == actor, AIRun.request_id == data.request_id)).first()
    if existing:
        if existing.request_digest != digest:
            raise HTTPException(409, "This request identifier belongs to different reviewed work")
        return serialize(session, existing, actor)
    count = session.exec(select(func.count()).select_from(AIRun).where(AIRun.workspace_id == workspace)).one()
    if count >= MAX_RUNS:
        raise HTTPException(409, "Remove finished AI runs before starting more work")
    if data.target_project_id:
        from ..models.project_tasks import ProjectBoard
        board = session.get(ProjectBoard, data.target_project_id, populate_existing=True)
        if not board or board.channel_id != data.channel_id or board.archived:
            raise HTTPException(409, "Choose an active project in this conversation")
    refs, context = source_material(session, workspace, data.channel_id, actor, data.sources, cross_channel=data.disclose_cross_channel_sources)
    if len(context) + len(data.selected_text) > copilot.MAX_CONTEXT:
        raise HTTPException(413, "Selected sources and writing must fit within 24,000 characters")
    native_policy(session, data.channel_id, actor, data.prompt + "\n" + data.selected_text + "\n" + context)
    check_refs(session, workspace, data.channel_id, actor, refs)
    if data.disclosure_scope:
        from .media_capture import validate_ai_summary_scope
        validate_ai_summary_scope(session, workspace, data.channel_id, actor, data.disclosure_scope.model_dump(), data.profile_id, data.expected_profile_revision, [ref["id"] for ref in refs])
    row = AIRun(workspace_id=workspace, channel_id=data.channel_id, user_id=actor, request_id=data.request_id, request_digest=digest,
        profile_id=data.profile_id, profile_revision=data.expected_profile_revision, operation=data.operation,
        max_steps=len(plan), max_tokens=data.max_tokens, encrypted_payload="", encrypted_authority="")
    row.encrypted_payload = copilot.seal({"prompt": data.prompt, "context": context, "selected_text": data.selected_text, "refs": refs, "target_project_id": data.target_project_id, "provider_identity": provider_identity(provider), "plan": plan, "disclosure_scope": data.disclosure_scope.model_dump() if data.disclosure_scope else None}, "run-payload", row.id)
    row.encrypted_authority = copilot.seal(authority, "run-authority", row.id)
    session.add(row); session.flush()
    for index, ref in enumerate(refs):
        session.add(AIRunSource(run_id=row.id, index=index, channel_id=ref.get("channel_id", row.channel_id), kind=ref.get("source_kind", ref["kind"]), source_id=ref["id"]))
    for index in range(row.max_steps):
        session.add(AIRunStep(run_id=row.id, index=index))
    copilot.audit(session, workspace, actor, "run_queued", len(refs)); request_guard.recheck_actor(session, actor); session.commit()
    return serialize(session, row, actor)


def load_run(session, workspace, identifier, actor, *, authorize=True):
    row = session.get(AIRun, identifier, populate_existing=True)
    if not row or row.workspace_id != workspace or row.user_id != actor:
        raise HTTPException(404, "Private AI run unavailable")
    if authorize:
        if copilot.grant(session, workspace, row.channel_id, actor):
            raise HTTPException(409, "Channel encryption changed; cancel the old native run")
    return row


def serialize(session, row, actor, *, summary=False):
    value = {"id": row.id, "revision": row.revision, "status": row.status, "operation": row.operation, "profile_id": row.profile_id,
        "profile_revision": row.profile_revision, "channel_id": row.channel_id, "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
        "error": row.error_code, "can_resume": row.status in {"failed", "cancelled"}}
    if summary:
        return value
    payload = copilot.opened(row.encrypted_payload, "run-payload", row.id)
    value["plan"] = [{key: item[key] for key in ("operation", "profile_id", "expected_profile_revision", "instructions")} for item in payload.get("plan", [])]
    steps = session.exec(select(AIRunStep).where(AIRunStep.run_id == row.id).order_by(AIRunStep.index)).all()
    value["steps"] = [{"index": step.index, "status": step.status, "attempts": step.attempts,
        "usage": json.loads(step.usage_json) if step.usage_json else None, "error": step.error_code} for step in steps]
    value["can_resume"] = value["can_resume"] and any(step.status != "completed" and step.attempts < 2 for step in steps)
    if row.encrypted_result:
        resolve_run_profiles(session, row, payload)
        public_refs = check_refs(session, row.workspace_id, row.channel_id, actor, payload["refs"])
        value["result"] = copilot.opened(row.encrypted_result, "run-result", row.id)
        value["result"]["citations"] = public_refs
    return value


def change_run(session, row, actor, revision, *, resume=False, acknowledge=False):
    lock_actor_workspace(session, row.workspace_id, actor); session.refresh(row)
    if row.revision != revision:
        raise HTTPException(409, "AI run changed; refresh its current state")
    if resume:
        if row.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "Only a stopped or failed run can be resumed")
        if copilot.grant(session, row.workspace_id, row.channel_id, actor):
            raise HTTPException(409, "Do not resume a native run after encryption changed")
        payload = copilot.opened(row.encrypted_payload, "run-payload", row.id)
        lock_source_channels(session, row.channel_id, payload["refs"])
        resolve_run_profiles(session, row, payload)
        check_disclosure_scope(session, row, payload)
        check_refs(session, row.workspace_id, row.channel_id, actor, payload["refs"])
        unfinished = session.exec(select(AIRunStep).where(AIRunStep.run_id == row.id, AIRunStep.status != "completed").order_by(AIRunStep.index)).all()
        if not unfinished or any(step.attempts >= 2 for step in unfinished):
            raise HTTPException(409, "This run reached its bounded retry limit; review a new request")
        previous_calls = session.exec(select(AIUsage.id).where(AIUsage.execution_id == row.id, AIUsage.step_index.in_([step.index for step in unfinished]), AIUsage.status.in_(["settled", "unknown"]))).first()
        if previous_calls and not acknowledge:
            raise HTTPException(422, "Explicitly acknowledge that a previous provider attempt may already have incurred a charge")
        authority = session.info.get(request_guard._KEY)
        if not authority:
            raise HTTPException(401, "A current session is required")
        row.encrypted_authority = copilot.seal(authority, "run-authority", row.id)
        for step in unfinished:
            step.status = "queued"; step.error_code = None; session.add(step)
        row.status = "queued"; row.error_code = None
    else:
        if row.status == "completed":
            return serialize(session, row, actor)
        row.status = "cancelled"; row.error_code = None; row.encrypted_result = None
        for step in session.exec(select(AIRunStep).where(AIRunStep.run_id == row.id, AIRunStep.status == "running")).all():
            step.status = "cancelled"; step.error_code = None; session.add(step)
        for ledger in session.exec(select(AIUsage).where(AIUsage.execution_id == row.id, AIUsage.status == "reserved")).all():
            ledger.status = "unknown"; ledger.charged_tokens = ledger.reserved_tokens; ledger.estimated_cost_micros = ledger.reserved_cost_micros; session.add(ledger)
    row.generation += 1; row.revision += 1; row.claim_token = None; row.claim_until = None; row.updated_at = datetime.utcnow()
    session.add(row); session.commit()
    return serialize(session, row, actor)


def delete_run(session, row, actor):
    lock_actor_workspace(session, row.workspace_id, actor); session.refresh(row)
    if row.status in {"queued", "running"}:
        raise HTTPException(409, "Cancel pending work before removing its private history")
    session.execute(delete(AIRunApproval).where(AIRunApproval.run_id == row.id))
    session.execute(delete(AIRunStep).where(AIRunStep.run_id == row.id))
    session.execute(delete(AIRunSource).where(AIRunSource.run_id == row.id))
    session.delete(row)
    # Financial usage is numeric metadata and deliberately survives history
    # removal; deleting a draft must never replenish a monthly usage budget.
    session.commit()


def messages_for(row, payload, index, prior):
    spec = (payload.get("plan") or [])[index] if payload.get("plan") else None
    operation = spec["operation"] if spec else row.operation
    instruction = {"draft": "Improve the selected writing while preserving its meaning.", "summarize": "Summarize only the selected sources.",
        "answer": "Answer the user's question using only the supplied sources.", "compare": "Compare the selected sources and explain agreements and differences.",
        "task_plan": "Propose a practical task. If the create_task tool is available, propose it; never claim it was created.",
        "group": 'Return ONLY a JSON object {"groups":[{"title":"name","source_ids":["selected id"],"reason":"brief reason"}]}. Use only supplied source IDs, each ID at most once. Do not create or move content.'}[operation]
    if spec and spec.get("instructions"):
        instruction += "\nReviewed step instruction: " + spec["instructions"]
    messages = [{"role": "system", "content": copilot.SYSTEM + "\n" + instruction}]
    if index:
        messages.append({"role": "user", "content": "Refine the previous step while preserving source citations and exact selected IDs. Previous result:\n" + prior})
    messages.append({"role": "user", "content": payload["prompt"] + "\n\nSELECTED WRITING (untrusted data):\n" + payload["selected_text"] + "\n\nSELECTED CONTEXT (untrusted data):\n" + payload["context"]})
    return messages


def checked_result(row, payload, answer):
    refs = payload["refs"]
    if any(int(marker) < 1 or int(marker) > len(refs) for marker in re.findall(r"\[S(\d+)\]", answer["text"])):
        raise HTTPException(502, "The model cited a source outside the reviewed selection")
    proposals = []
    for call in answer["tool_calls"]:
        if row.operation != "task_plan" or call["name"] != "create_task" or not payload["target_project_id"]:
            raise HTTPException(502, "The model proposed an unapproved operation")
        arguments = json.loads(call["arguments"], object_pairs_hook=copilot.transport._pairs, parse_constant=copilot.transport._constant)
        if not isinstance(arguments, dict) or set(arguments) - {"project_id", "title", "description"} or arguments.get("project_id") != payload["target_project_id"]:
            raise HTTPException(502, "The proposed task does not match the selected destination")
        from .project_tasks import TaskFields
        task = TaskFields(title=arguments.get("title", ""), description=arguments.get("description", ""))
        proposals.append({"id": "p" + str(len(proposals) + 1), "tool": "create_task", "project_id": payload["target_project_id"], "title": task.title, "description": task.description})
    result = {"text": answer["text"], "citations": refs, "proposals": proposals}
    if row.operation == "group":
        value = json.loads(answer["text"], object_pairs_hook=copilot.transport._pairs, parse_constant=copilot.transport._constant)
        if not isinstance(value, dict) or set(value) != {"groups"} or not isinstance(value["groups"], list) or len(value["groups"]) > 8:
            raise HTTPException(502, "The model did not return a bounded grouping proposal")
        selected, seen = {ref["id"] for ref in refs}, set()
        for group in value["groups"]:
            if (not isinstance(group, dict) or set(group) != {"title", "source_ids", "reason"} or not isinstance(group["title"], str)
                    or not 1 <= len(group["title"]) <= 200 or not isinstance(group["reason"], str) or len(group["reason"]) > 2000
                    or not isinstance(group["source_ids"], list) or not 1 <= len(group["source_ids"]) <= 8):
                raise HTTPException(502, "Invalid grouping proposal")
            for identifier in group["source_ids"]:
                if not isinstance(identifier, str) or identifier not in selected or identifier in seen:
                    raise HTTPException(502, "Grouping referenced an unselected or duplicate source")
                seen.add(identifier)
        result["groups"] = value["groups"]
    return result


def resolve_run_profiles(session, row, payload):
    profiles = []
    specs = payload.get("plan") or [{"profile_id": row.profile_id, "expected_profile_revision": row.profile_revision, "provider_identity": payload.get("provider_identity")}]
    for item in specs:
        provider = resolve_profile(session, row.workspace_id, item["profile_id"], row.user_id, item["expected_profile_revision"])
        if item.get("provider_identity") != provider_identity(provider):
            raise HTTPException(409, "The selected model credential or destination changed")
        profiles.append(provider)
    return profiles


def check_disclosure_scope(session, row, payload):
    if payload.get("disclosure_scope"):
        from .media_capture import validate_ai_summary_scope
        validate_ai_summary_scope(session, row.workspace_id, row.channel_id, row.user_id, payload["disclosure_scope"], row.profile_id, row.profile_revision, [ref["id"] for ref in payload["refs"]])


def authorize_job(session, row):
    authority = copilot.opened(row.encrypted_authority, "run-authority", row.id)
    session.info[request_guard._KEY] = authority
    lock_actor_workspace(session, row.workspace_id, row.user_id)
    payload = copilot.opened(row.encrypted_payload, "run-payload", row.id)
    lock_source_channels(session, row.channel_id, payload["refs"])
    if copilot.grant(session, row.workspace_id, row.channel_id, row.user_id):
        raise HTTPException(409, "The channel encryption mode changed")
    profiles = resolve_run_profiles(session, row, payload)
    check_disclosure_scope(session, row, payload)
    step = session.exec(select(AIRunStep).where(AIRunStep.run_id == row.id, AIRunStep.status != "completed").order_by(AIRunStep.index)).first()
    provider = profiles[min(step.index if step else row.max_steps - 1, len(profiles) - 1)]
    check_refs(session, row.workspace_id, row.channel_id, row.user_id, payload["refs"])
    native_policy(session, row.channel_id, row.user_id, payload["prompt"] + "\n" + payload["selected_text"] + "\n" + payload["context"])
    return provider, payload


def recover_expired(session):
    now = datetime.utcnow()
    rows = session.exec(select(AIRun).where(AIRun.status == "running", AIRun.claim_until <= now).limit(20)).all()
    for row in rows:
        changed = session.execute(update(AIRun).where(AIRun.id == row.id, AIRun.status == "running", AIRun.claim_until <= now).values(
            status="failed", error_code="interrupted_attempt_review_required", claim_token=None, claim_until=None, revision=AIRun.revision + 1, generation=AIRun.generation + 1, updated_at=now)).rowcount
        if not changed:
            continue
        for step in session.exec(select(AIRunStep).where(AIRunStep.run_id == row.id, AIRunStep.status == "running")).all():
            step.status = "failed"; step.error_code = "interrupted_attempt_review_required"; session.add(step)
        for ledger in session.exec(select(AIUsage).where(AIUsage.execution_id == row.id, AIUsage.status == "reserved")).all():
            ledger.status = "unknown"; ledger.charged_tokens = ledger.reserved_tokens; ledger.estimated_cost_micros = ledger.reserved_cost_micros; session.add(ledger)
    session.commit()
    return len(rows)


def run_pending(engine, *, limit=2):
    """One bounded step per claim, with no external call inside a DB lock."""
    with Session(engine) as session:
        recover_expired(session)
        identifiers = session.exec(select(AIRun.id).where(AIRun.status == "queued").order_by(AIRun.created_at).limit(min(max(limit, 1), 5))).all()
    results = {}
    for identifier in identifiers:
        claim = uuid4().hex; ledger_id = None; sent = False; provider = None
        with Session(engine) as session:
            try:
                changed = session.execute(update(AIRun).where(AIRun.id == identifier, AIRun.status == "queued").values(
                    status="running", claim_token=claim, claim_until=datetime.utcnow() + timedelta(seconds=LEASE_SECONDS), revision=AIRun.revision + 1, updated_at=datetime.utcnow())).rowcount
                session.commit()
                if not changed:
                    continue
                row = session.get(AIRun, identifier); generation = row.generation
                provider, payload = authorize_job(session, row)
                step = session.exec(select(AIRunStep).where(AIRunStep.run_id == identifier, AIRunStep.status != "completed").order_by(AIRunStep.index)).first()
                if not step or step.attempts >= 2:
                    raise HTTPException(409, "Run attempt budget exhausted")
                prior = session.get(AIRunStep, (identifier, step.index - 1)) if step.index else None
                prior_text = copilot.opened(prior.encrypted_result, "step-result", identifier + ":" + str(prior.index))["text"] if prior else ""
                messages = messages_for(row, payload, step.index, prior_text)
                if provider.instructions:
                    messages[0]["content"] += "\nConfigured model-profile instructions: " + provider.instructions
                spec = payload["plan"][step.index] if payload.get("plan") else {"operation": row.operation, "profile_id": row.profile_id, "expected_profile_revision": row.profile_revision}
                operation_row = SimpleNamespace(operation=spec["operation"])
                ledger = reserve(session, row.workspace_id, row.user_id, identifier, step.index, step.attempts + 1, spec["profile_id"], spec["expected_profile_revision"], provider, messages, min(row.max_tokens, provider.max_output_tokens))
                ledger_id = ledger.id; step.attempts += 1; step.status = "running"; step.started_at = datetime.utcnow(); session.add(step); session.commit()
                # A consent/profile/source/actor change during extraction or
                # reservation cannot authorize the forthcoming external call.
                row = session.get(AIRun, identifier, populate_existing=True)
                if row.claim_token != claim or row.generation != generation or row.status != "running":
                    settle(session, ledger_id, provider, sent=False); session.commit(); results[identifier] = "discarded"; continue
                provider, payload = authorize_job(session, row)
                session.commit()
                tools = copilot.tools_for(copilot.Agent(name="runtime", tools=["create_task"])) if spec["operation"] == "task_plan" and payload["target_project_id"] else []
                if tools:
                    tools[0]["function"]["parameters"]["properties"]["project_id"]["enum"] = [payload["target_project_id"]]
                sent = True
                answer = ai_providers.complete(provider, messages, max_tokens=row.max_tokens, tools=tools)
                settle(session, ledger_id, provider, answer.get("usage")); session.commit()
                row = session.get(AIRun, identifier, populate_existing=True)
                if not row or row.claim_token != claim or row.generation != generation or row.status != "running":
                    results[identifier] = "discarded"; continue
                authorize_job(session, row); session.refresh(row)
                if row.claim_token != claim or row.generation != generation or row.status != "running":
                    results[identifier] = "discarded"; continue
                result = checked_result(operation_row, payload, answer)
                native_policy(session, row.channel_id, row.user_id, result["text"])
                step = session.get(AIRunStep, (identifier, step.index), populate_existing=True)
                step.status = "completed"; step.completed_at = datetime.utcnow(); step.error_code = None
                step.encrypted_result = copilot.seal(result, "step-result", identifier + ":" + str(step.index))
                measured = answer.get("usage", {}); step.usage_json = copilot.dump({**measured, "total_tokens": measured.get("input_tokens", 0) + measured.get("output_tokens", 0), "estimated_cost_micros": cost(provider, measured.get("input_tokens", 0), measured.get("output_tokens", 0)), "cost_is_estimate": True})
                row.status = "awaiting_review" if step.index + 1 == row.max_steps else "queued"
                if row.status == "awaiting_review" and row.max_steps > 1:
                    proposals = []
                    for previous in session.exec(select(AIRunStep).where(AIRunStep.run_id == row.id, AIRunStep.index < step.index).order_by(AIRunStep.index)).all():
                        previous_result = copilot.opened(previous.encrypted_result, "step-result", row.id + ":" + str(previous.index))
                        proposals.extend([{**item, "id": "s" + str(previous.index) + "-" + item["id"], "step_index": previous.index} for item in previous_result["proposals"]])
                    proposals.extend([{**item, "id": "s" + str(step.index) + "-" + item["id"], "step_index": step.index} for item in result["proposals"]])
                    result["proposals"] = proposals
                row.encrypted_result = copilot.seal(result, "run-result", row.id) if row.status == "awaiting_review" else None
                row.revision += 1; row.claim_token = None; row.claim_until = None; row.updated_at = datetime.utcnow()
                session.add(step); session.add(row); request_guard.recheck_actor(session, row.user_id); session.commit()
                results[identifier] = row.status
            except Exception as failure:
                session.rollback()
                if ledger_id and provider:
                    settle(session, ledger_id, provider, sent=sent); session.commit()
                row = session.get(AIRun, identifier, populate_existing=True)
                if row and row.claim_token == claim and row.status == "running":
                    row.status = "failed"; row.error_code = "budget_exhausted" if isinstance(failure, HTTPException) and failure.status_code == 409 and "budget" in str(failure.detail).lower() else "authorization_or_source_changed" if isinstance(failure, HTTPException) and failure.status_code in {401, 403, 404, 409} else "provider_or_output_failed"
                    row.revision += 1; row.claim_token = None; row.claim_until = None; row.updated_at = datetime.utcnow()
                    session.add(row)
                    for step in session.exec(select(AIRunStep).where(AIRunStep.run_id == identifier, AIRunStep.status == "running")).all():
                        step.status = "failed"; step.error_code = row.error_code; session.add(step)
                    session.commit()
                results[identifier] = "failed"
    return results


def approve(session, row, actor, revision, proposal_id, confirm):
    lock_actor_workspace(session, row.workspace_id, actor)
    session.refresh(row)
    receipt = session.get(AIRunApproval, (row.id, proposal_id))
    if receipt:
        if proposal_id == "accept":
            return {"completed": True, "already_approved": True}
        return {"task_id": receipt.resource_id, "already_approved": True}
    if not confirm or row.status != "awaiting_review" or row.revision != revision or not row.encrypted_result:
        raise HTTPException(409, "Review the current result before approving it")
    payload = copilot.opened(row.encrypted_payload, "run-payload", row.id)
    lock_source_channels(session, row.channel_id, payload["refs"])
    if copilot.grant(session, row.workspace_id, row.channel_id, actor):
        raise HTTPException(409, "Encrypted work cannot use a native task write")
    resolve_run_profiles(session, row, payload)
    check_disclosure_scope(session, row, payload)
    check_refs(session, row.workspace_id, row.channel_id, actor, payload["refs"])
    result = copilot.opened(row.encrypted_result, "run-result", row.id)
    if proposal_id == "accept" and not result["proposals"]:
        session.add(AIRunApproval(run_id=row.id, proposal_id="accept", user_id=actor, resource_id=""))
        row.status = "completed"; row.revision += 1; row.updated_at = datetime.utcnow(); session.add(row); session.commit()
        return {"completed": True, "already_approved": False}
    proposal = next((item for item in result["proposals"] if item["id"] == proposal_id), None)
    if not proposal:
        raise HTTPException(404, "Proposal unavailable")
    from .project_tasks import create_workflow_task
    task = create_workflow_task(session, row.channel_id, actor, proposal["project_id"], proposal["title"], "ai-run:" + row.id + ":" + proposal_id, proposal["description"])
    session.add(AIRunApproval(run_id=row.id, proposal_id=proposal_id, user_id=actor, resource_id=task.id))
    session.flush()
    if session.exec(select(func.count()).select_from(AIRunApproval).where(AIRunApproval.run_id == row.id)).one() >= len(result["proposals"]):
        row.status = "completed"; row.revision += 1; row.updated_at = datetime.utcnow(); session.add(row)
    request_guard.recheck_actor(session, actor); session.commit()
    return {"task_id": task.id, "already_approved": False}


def foreground(session, workspace, actor, data):
    secure = lock_input_sources(session, workspace, data.channel_id, actor, data.sources)
    provider = resolve_profile(session, workspace, data.profile_id, actor, data.expected_profile_revision)
    if not data.disclose_to_provider or secure and not data.disclose_decrypted_content:
        raise HTTPException(422, "Explicitly review provider disclosure and any decrypted content")
    if data.max_steps != 1 or data.plan or data.disclosure_scope or secure and data.target_project_id:
        raise HTTPException(422, "Foreground requests use one step and do not write encrypted task proposals")
    if data.reviewed_source_ids and not secure:
        raise HTTPException(422, "A reviewed device-only catalog belongs to encrypted foreground work")
    refs, context = source_material(session, workspace, data.channel_id, actor, data.sources, secure=secure, cross_channel=data.disclose_cross_channel_sources)
    output_refs = list(refs)
    for identifier in data.reviewed_source_ids:
        if identifier not in {ref["id"] for ref in output_refs}:
            output_refs.append({"kind": "device_catalog", "id": identifier, "citation": "S" + str(len(output_refs) + 1)})
    if len(output_refs) > 8:
        raise HTTPException(422, "Select at most eight formal and device-reviewed sources in total")
    if data.reviewed_source_ids:
        context += "\nREVIEWED DEVICE SOURCE IDS (device verifies content/membership):\n" + copilot.dump(data.reviewed_source_ids)
    if len(context) + len(data.selected_text) > copilot.MAX_CONTEXT:
        raise HTTPException(413, "Selected sources and text exceed the reviewed context limit")
    if not secure:
        native_policy(session, data.channel_id, actor, data.prompt + "\n" + data.selected_text + "\n" + context)
    fake = SimpleNamespace(operation=data.operation)
    payload = {"prompt": data.prompt, "context": context, "selected_text": data.selected_text, "refs": output_refs, "target_project_id": data.target_project_id}
    messages = messages_for(fake, payload, 0, "")
    if provider.instructions:
        messages[0]["content"] += "\nConfigured model-profile instructions: " + provider.instructions
    execution = "foreground:" + hmac.new(_secret_key(), (workspace + ":" + actor + ":" + data.request_id).encode(), hashlib.sha256).hexdigest()
    ledger = reserve(session, workspace, actor, execution, 0, 1, data.profile_id, data.expected_profile_revision, provider, messages, min(data.max_tokens, provider.max_output_tokens))
    identifier = ledger.id; identity = provider_identity(provider); sent = False; session.commit()
    try:
        if copilot.grant(session, workspace, data.channel_id, actor) != secure or provider_identity(resolve_profile(session, workspace, data.profile_id, actor, data.expected_profile_revision)) != identity:
            raise HTTPException(409, "Selected model or channel changed before disclosure")
        if secure:
            copilot.check_sources(session, SimpleNamespace(channel_id=data.channel_id, secure=True), refs, actor)
        else:
            check_refs(session, workspace, data.channel_id, actor, refs)
        session.commit(); sent = True
        answer = ai_providers.complete(provider, messages, max_tokens=data.max_tokens)
        settle(session, identifier, provider, answer.get("usage")); session.commit()
        lock_actor_workspace(session, workspace, actor)
        lock_source_channels(session, data.channel_id, refs)
        current_mode = copilot.grant(session, workspace, data.channel_id, actor)
        if provider_identity(resolve_profile(session, workspace, data.profile_id, actor, data.expected_profile_revision)) != identity:
            raise HTTPException(409, "Selected model credential changed during the request")
        if current_mode != secure:
            raise HTTPException(409, "Channel encryption changed during the request")
        if secure:
            copilot.check_sources(session, SimpleNamespace(channel_id=data.channel_id, secure=True), refs, actor)
            public_refs = output_refs
        else:
            public_refs = check_refs(session, workspace, data.channel_id, actor, refs)
        result = checked_result(fake, payload, answer)
        result["citations"] = public_refs
        if not secure:
            native_policy(session, data.channel_id, actor, result["text"])
        session.commit()
        return {**result, "usage": answer.get("usage"), "ephemeral": True}
    except Exception:
        session.rollback(); settle(session, identifier, provider, sent=sent); session.commit(); raise


def propose_plan(session, workspace, actor, data, allowed_profiles):
    catalog, identities = [], {}
    for selected in allowed_profiles:
        provider = resolve_profile(session, workspace, selected.profile_id, actor, selected.expected_profile_revision)
        identities[selected.profile_id] = (selected.expected_profile_revision, provider_identity(provider))
        catalog.append({"profile_id": selected.profile_id, "name": provider.name, "model": provider.model})
    if len(identities) != len(allowed_profiles):
        raise HTTPException(422, "Choose each permitted model profile once")
    instructions = ('Propose a bounded plan, but do not execute it. Return ONLY JSON {"plan":[{"operation":"summarize","profile_id":"chosen-id","instructions":"short instruction"}]}.'
        ' Use1–' + str(data.max_steps) + ' steps and only operations draft,summarize,task_plan,group,compare,answer. Profile IDs must come from this reviewed catalog: '
        + copilot.dump(catalog) + '. Never include URLs, credentials, code, HTTP calls or unselected tools. User goal:\n' + data.prompt)
    if len(instructions) > 8000:
        raise HTTPException(413, "The reviewed planner catalog and goal exceed their bounded size")
    query = data.model_copy(update={"operation": "answer", "prompt": instructions, "max_steps": 1, "plan": [], "target_project_id": None})
    result = foreground(session, workspace, actor, query)
    try:
        parsed = json.loads(result["text"], object_pairs_hook=copilot.transport._pairs, parse_constant=copilot.transport._constant)
        if not isinstance(parsed, dict) or set(parsed) != {"plan"} or not isinstance(parsed["plan"], list) or not 1 <= len(parsed["plan"]) <= data.max_steps:
            raise ValueError()
        plan = []
        for item in parsed["plan"]:
            if not isinstance(item, dict) or set(item) - {"operation", "profile_id", "instructions"} or item.get("profile_id") not in identities:
                raise ValueError()
            revision, fingerprint = identities[item["profile_id"]]
            current = resolve_profile(session, workspace, item["profile_id"], actor, revision)
            if provider_identity(current) != fingerprint:
                raise HTTPException(409, "A proposed recipient changed while planning")
            step = PlanStep(operation=item["operation"], profile_id=item["profile_id"], expected_profile_revision=revision, instructions=item.get("instructions", ""))
            plan.append(step.model_dump())
        return {"plan": plan, "planner_profile_id": data.profile_id, "planner_profile_revision": data.expected_profile_revision, "usage": result["usage"], "ephemeral": True}
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError):
        raise HTTPException(502, "The planner proposed steps outside the reviewed operation/model catalog; nothing was executed") from None


def cleanup_actor(session, actor_id, channel_ids=None):
    """Caller-owned transaction; preserve shared profile/budget policy on erase."""
    query = select(AIRun.id).where(AIRun.user_id == actor_id)
    if channel_ids is not None:
        query = query.where(AIRun.channel_id.in_(channel_ids))
    identifiers = session.exec(query).all()
    if identifiers:
        cleanup_run_ids(session, identifiers)
    if channel_ids is None:
        anonymize_usage(session, actor_id=actor_id)
        session.execute(delete(AIPersonalKey).where(AIPersonalKey.user_id == actor_id))
        session.execute(delete(AIProfileConsent).where(AIProfileConsent.user_id == actor_id))
        session.execute(update(AIModelProfile).where(AIModelProfile.updated_by == actor_id).values(updated_by=None))
        session.execute(update(AIReadTool).where(AIReadTool.updated_by == actor_id).values(updated_by=None))
        session.execute(update(AIBudget).where(AIBudget.updated_by == actor_id).values(updated_by=None))
    return len(identifiers)


def cleanup_channels(session, channel_ids):
    identifiers = list(set(session.exec(select(AIRun.id).where(AIRun.channel_id.in_(channel_ids))).all()) |
        set(session.exec(select(AIRunSource.run_id).where(AIRunSource.channel_id.in_(channel_ids))).all()))
    cleanup_run_ids(session, identifiers)
    return len(identifiers)


def cleanup_run_ids(session, identifiers):
    if not identifiers:
        return 0
    anonymize_usage(session, execution_ids=identifiers)
    session.execute(delete(AIRunApproval).where(AIRunApproval.run_id.in_(identifiers)))
    session.execute(delete(AIRunStep).where(AIRunStep.run_id.in_(identifiers)))
    session.execute(delete(AIRunSource).where(AIRunSource.run_id.in_(identifiers)))
    return session.execute(delete(AIRun).where(AIRun.id.in_(identifiers))).rowcount


def pending_in_channels(session, channel_ids):
    sources = select(AIRunSource.run_id).where(AIRunSource.channel_id.in_(channel_ids))
    return bool(session.exec(select(AIRun.id).where(or_(AIRun.channel_id.in_(channel_ids), AIRun.id.in_(sources)),
        AIRun.status.in_(["queued", "running", "awaiting_review"])).limit(1)).first())


def anonymize_usage(session, *, actor_id=None, execution_ids=None, workspace_id=None, drop_workspace=False):
    query = select(AIUsage)
    if workspace_id is not None:
        query = query.where(AIUsage.workspace_id == workspace_id)
    elif actor_id is not None:
        query = query.where(AIUsage.user_id == actor_id)
    elif execution_ids:
        query = query.where(AIUsage.execution_id.in_(execution_ids))
    else:
        return
    for ledger in session.exec(query).all():
        if ledger.status == "reserved":
            ledger.status = "unknown"; ledger.charged_tokens = ledger.reserved_tokens; ledger.estimated_cost_micros = ledger.reserved_cost_micros
        ledger.user_id = None; ledger.execution_id = "erased:" + ledger.id; ledger.profile_id = "erased"; ledger.profile_revision = 0
        if drop_workspace:
            ledger.workspace_id = None
        session.add(ledger)


def cleanup_workspace(session, workspace_id):
    identifiers = session.exec(select(AIRun.id).where(AIRun.workspace_id == workspace_id)).all()
    cleanup_run_ids(session, identifiers)
    anonymize_usage(session, workspace_id=workspace_id, drop_workspace=True)
    for model in (AIPersonalKey, AIProfileConsent, AIModelProfile, AIReadTool, AIBudget):
        session.execute(delete(model).where(model.workspace_id == workspace_id))


def export_actor(session, actor_id, channel_ids, *, workspace_id=None):
    result = []
    for row in session.exec(select(AIRun).where(AIRun.user_id == actor_id, AIRun.channel_id.in_(channel_ids)).limit(MAX_RUNS)).all():
        try:
            if workspace_id is not None and row.workspace_id != workspace_id:
                continue
            source_channels = session.exec(select(AIRunSource.channel_id).where(AIRunSource.run_id == row.id)).all()
            if not set(source_channels).issubset(set(channel_ids)):
                continue
            if copilot.grant(session, row.workspace_id, row.channel_id, actor_id):
                continue
            payload = copilot.opened(row.encrypted_payload, "run-payload", row.id)
            check_refs(session, row.workspace_id, row.channel_id, actor_id, payload["refs"], read_only=True)
            item = serialize(session, row, actor_id, summary=True)
            item.update(prompt=payload["prompt"], selected_text=payload["selected_text"], citations=payload["refs"])
            if row.encrypted_result:
                item["result"] = copilot.opened(row.encrypted_result, "run-result", row.id)
            result.append(item)
        except HTTPException:
            continue
    from ..models.workspace import WorkspaceChannel
    workspaces = set(session.exec(select(WorkspaceChannel.workspace_id).where(WorkspaceChannel.channel_id.in_(channel_ids))).all())
    if workspace_id is not None:
        workspaces &= {workspace_id}
    ledger = session.exec(select(AIUsage).where(AIUsage.user_id == actor_id, AIUsage.workspace_id.in_(workspaces)).order_by(AIUsage.created_at.desc()).limit(10000)).all() if workspaces else []
    return {"runs": result, "usage": [{key: getattr(row, key) for key in ("profile_id", "profile_revision", "status", "input_tokens", "output_tokens", "charged_tokens", "estimated_cost_micros", "usage_reported")} for row in ledger], "credentials_exported": False}
