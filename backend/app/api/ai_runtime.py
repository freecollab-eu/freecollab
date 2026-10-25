"""Reviewed model profiles and private, resumable AI work."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field, field_validator
from sqlmodel import select

from ..database import get_session
from ..models.ai_runtime import AIBudget, AIModelProfile, AIPersonalKey, AIProfileConsent, AIReadTool, AIRun
from ..services import ai_providers, ai_read_tools, ai_runtime as service, copilot
from ..services.account_security import account_limit, ensure_reauthentication
from ..services.auth import get_current_user
from ..services.request_guard import recheck_actor
from ..services.workspaces import lock_workspace, require_workspace_member

router = APIRouter(prefix="/runtime", tags=["ai-runtime"])


class Reauthentication(copilot.Strict):
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)


class ProfileInput(Reauthentication):
    expected_revision: int = Field(ge=0, strict=True)
    name: str = Field(min_length=1, max_length=80)
    protocol: Literal["openai", "xai", "anthropic", "azure", "gemini", "ollama"] = "openai"
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=100)
    instructions: str = Field(default="", max_length=2000)
    api_version: str = Field(default="2024-10-21", pattern=r"^[A-Za-z0-9.-]{1,50}$")
    api_key: str | None = Field(default=None, min_length=8, max_length=2048)
    key_source: Literal["workspace", "personal"] = "workspace"
    enabled: bool = True
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    input_cost_per_million_micros: int = Field(default=0, ge=0, le=100000000000)
    output_cost_per_million_micros: int = Field(default=0, ge=0, le=100000000000)


class ConsentInput(copilot.Strict):
    expected_revision: int = Field(ge=1, strict=True)
    enabled: bool


class PersonalKeyInput(Reauthentication):
    expected_profile_revision: int = Field(ge=1, strict=True)
    api_key: str = Field(min_length=8, max_length=2048)

    @field_validator("api_key")
    @classmethod
    def valid_header(cls, value):
        return ai_providers.Provider.header_key(value)


class BudgetInput(Reauthentication):
    expected_revision: int = Field(ge=0, strict=True)
    max_tokens: int = Field(ge=1, le=1000000000, strict=True)
    max_cost_micros: int = Field(ge=0, le=1000000000000, strict=True)
    default_input_cost_per_million_micros: int | None = Field(default=None, ge=0, le=100000000000)
    default_output_cost_per_million_micros: int | None = Field(default=None, ge=0, le=100000000000)


class VersionInput(copilot.Strict):
    expected_revision: int = Field(ge=1, strict=True)


class ResumeInput(VersionInput):
    acknowledge_possible_duplicate_charge: bool


class ApproveInput(VersionInput):
    proposal_id: str = Field(min_length=1, max_length=20)
    confirm: bool


class ToolInput(Reauthentication):
    expected_revision: int = Field(ge=0, strict=True)
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["web_search", "http_read"]
    endpoint: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "POST"] = "GET"
    api_key: str | None = Field(default=None, min_length=8, max_length=2048)
    auth_header: Literal["bearer", "x-api-key"] = "bearer"
    query_field: str = Field(default="q", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    result_path: list[str] = Field(default_factory=lambda: ["results"], max_length=5)
    title_field: str = Field(default="title", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    url_field: str = Field(default="url", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    text_field: str = Field(default="content", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    max_results: int = Field(default=5, ge=1, le=10)
    enabled: bool = True


class AllowedProfile(copilot.Strict):
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    expected_profile_revision: int = Field(ge=1, strict=True)


class PlanInput(service.RunInput):
    allowed_profiles: list[AllowedProfile] = Field(min_length=1, max_length=8)


def administrator(session, workspace, user, data):
    from .copilot import owner
    owner(session, workspace, user, data)


@router.get("")
def status(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    member = require_workspace_member(session, workspace_id, user.id)
    rows = session.exec(select(AIRun).where(AIRun.workspace_id == workspace_id, AIRun.user_id == user.id).order_by(AIRun.created_at.desc()).limit(20)).all()
    visible = []
    for row in rows:
        try:
            service.load_run(session, workspace_id, row.id, user.id)
            visible.append(service.serialize(session, row, user.id, summary=True))
        except HTTPException:
            continue
    return {"globally_enabled": copilot.enabled(), "role": member.role, "profiles": service.profile_summaries(session, workspace_id, user.id),
        "budget": service.budget_status(session, workspace_id), "runs": visible}


@router.get("/executions")
def executions(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services.ai_execution_timeline import timeline
    return timeline(session, workspace_id, user.id)


@router.get("/tools")
def tools(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services import ai_read_tools
    member = require_workspace_member(session, workspace_id, user.id)
    rows = session.exec(select(AIReadTool).where(AIReadTool.workspace_id == workspace_id).order_by(AIReadTool.tool_id)).all()
    return {"items": [ai_read_tools.public(row) for row in rows], "role": member.role}


@router.put("/tools/{tool_id}")
def configure_tool(workspace_id: str, tool_id: str, data: ToolInput, user=Depends(get_current_user), session=Depends(get_session)):
    import re
    from ..services import ai_read_tools
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", tool_id):
        raise HTTPException(422, "Choose a stable registered-tool identifier")
    administrator(session, workspace_id, user, data)
    current = session.get(AIReadTool, (workspace_id, tool_id), populate_existing=True)
    if data.expected_revision != (current.revision if current else 0):
        raise HTTPException(409, "Registered read tool changed; reload it")
    if not current and len(session.exec(select(AIReadTool).where(AIReadTool.workspace_id == workspace_id)).all()) >= 16:
        raise HTTPException(409, "A workspace supports at most sixteen registered read tools")
    previous = copilot.opened(current.encrypted_config, "read-tool", workspace_id + ":" + tool_id) if current else None
    key = data.api_key
    if key is None and previous and previous["endpoint"] == data.endpoint and previous["auth_header"] == data.auth_header:
        key = previous.get("api_key", "")
    try:
        tool = ai_read_tools.Tool(**data.model_dump(exclude={"password", "code", "expected_revision", "enabled", "api_key"}), api_key=key or "")
    except ValueError:
        raise HTTPException(422, "Use a registered public HTTPS JSON search or GET read endpoint") from None
    row = current or AIReadTool(workspace_id=workspace_id, tool_id=tool_id, revision=0, encrypted_config="", updated_by=user.id)
    row.revision += 1; row.enabled = data.enabled; row.updated_by = user.id; row.updated_at = datetime.utcnow()
    row.encrypted_config = copilot.seal(tool.model_dump(), "read-tool", workspace_id + ":" + tool_id)
    session.add(row); recheck_actor(session, user.id); session.commit()
    return ai_read_tools.public(row)


@router.post("/tools/{tool_id}/read")
def read_tool(workspace_id: str, tool_id: str, data: ai_read_tools.ReadInput, user=Depends(get_current_user), session=Depends(get_session)):
    from ..services import ai_read_tools
    account_limit(session, "ai-registered-read", user.id, 30, 3600)
    return ai_read_tools.read(session, workspace_id, user.id, tool_id, data)


@router.put("/profiles/{profile_id}")
def configure_profile(workspace_id: str, profile_id: str, data: ProfileInput, user=Depends(get_current_user), session=Depends(get_session)):
    import re
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", profile_id) or profile_id == "default":
        raise HTTPException(422, "Choose a named model profile; the legacy default retains its existing configuration")
    administrator(session, workspace_id, user, data)
    current = session.get(AIModelProfile, (workspace_id, profile_id), populate_existing=True)
    if data.expected_revision != (current.revision if current else 0):
        raise HTTPException(409, "Model profile changed; reload it")
    if not current and len(session.exec(select(AIModelProfile).where(AIModelProfile.workspace_id == workspace_id)).all()) >= 16:
        raise HTTPException(409, "A workspace supports at most sixteen named model profiles")
    old = copilot.opened(current.encrypted_config, "profile", service.subject(workspace_id, profile_id)) if current else None
    key = data.api_key
    if data.key_source == "workspace" and key is None and old and old["endpoint"] == data.endpoint and old["protocol"] == data.protocol:
        key = old.get("api_key", "")
    values = data.model_dump(exclude={"password", "code", "expected_revision", "enabled", "api_key"})
    try:
        provider = ai_providers.Provider(**values, api_key=key or "")
    except ValueError:
        raise HTTPException(422, "Use a valid native provider endpoint, model and reviewed credential source") from None
    row = current or AIModelProfile(workspace_id=workspace_id, profile_id=profile_id, revision=0, encrypted_config="", updated_by=user.id)
    row.revision += 1; row.enabled = data.enabled; row.updated_by = user.id; row.updated_at = datetime.utcnow()
    row.encrypted_config = copilot.seal(provider.model_dump(), "profile", service.subject(workspace_id, profile_id))
    session.add(row); copilot.audit(session, workspace_id, user.id, "model_profile_configured"); recheck_actor(session, user.id); session.commit()
    return {"id": profile_id, "revision": row.revision, "enabled": row.enabled}


@router.post("/profiles/{profile_id}/consent")
def consent(workspace_id: str, profile_id: str, data: ConsentInput, user=Depends(get_current_user), session=Depends(get_session)):
    service.lock_actor_workspace(session, workspace_id, user.id); require_workspace_member(session, workspace_id, user.id)
    if profile_id == "default":
        from .copilot import ConsentInput as LegacyConsent, consent as legacy_consent
        return legacy_consent(workspace_id, LegacyConsent(enabled=data.enabled, config_revision=data.expected_revision), user, session)
    row = session.get(AIModelProfile, (workspace_id, profile_id), populate_existing=True)
    if not row or row.revision != data.expected_revision:
        raise HTTPException(409, "Model profile changed; review it again")
    if data.enabled and (not copilot.enabled() or not row.enabled):
        raise HTTPException(409, "The model profile is disabled")
    record = session.get(AIProfileConsent, (workspace_id, profile_id, user.id)) or AIProfileConsent(workspace_id=workspace_id, profile_id=profile_id, user_id=user.id, revision=row.revision)
    record.enabled = data.enabled; record.revision = row.revision; session.add(record); session.commit()
    return {"enabled": record.enabled, "revision": record.revision}


@router.put("/personal-keys/{profile_id}")
def personal_key(workspace_id: str, profile_id: str, data: PersonalKeyInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "ai-personal-key", user.id, 20, 3600)
    service.lock_actor_workspace(session, workspace_id, user.id); require_workspace_member(session, workspace_id, user.id)
    ensure_reauthentication(session, user, data.password, data.code)
    revision, provider = service.profile_record(session, workspace_id, profile_id)
    if revision != data.expected_profile_revision or provider.key_source != "personal":
        raise HTTPException(409, "Choose the current personal-key model profile")
    row = session.get(AIPersonalKey, (workspace_id, profile_id, user.id)) or AIPersonalKey(workspace_id=workspace_id, profile_id=profile_id, user_id=user.id, profile_revision=revision, encrypted_key="")
    row.profile_revision = revision; row.updated_at = datetime.utcnow()
    row.encrypted_key = copilot.seal({"api_key": data.api_key}, "personal-key", service.subject(workspace_id, profile_id) + ":" + user.id)
    recheck_actor(session, user.id); session.add(row); session.commit()
    return {"configured": True, "profile_revision": revision}


@router.delete("/personal-keys/{profile_id}", status_code=204)
def delete_personal_key(workspace_id: str, profile_id: str, data: Reauthentication, user=Depends(get_current_user), session=Depends(get_session)):
    service.lock_actor_workspace(session, workspace_id, user.id); require_workspace_member(session, workspace_id, user.id)
    ensure_reauthentication(session, user, data.password, data.code)
    row = session.get(AIPersonalKey, (workspace_id, profile_id, user.id))
    if row:
        session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.put("/budget")
def configure_budget(workspace_id: str, data: BudgetInput, user=Depends(get_current_user), session=Depends(get_session)):
    administrator(session, workspace_id, user, data)
    row = session.get(AIBudget, workspace_id, populate_existing=True)
    if data.expected_revision != (row.revision if row else 0):
        raise HTTPException(409, "AI budget changed; reload it")
    row = row or AIBudget(workspace_id=workspace_id, revision=0, updated_by=user.id)
    row.revision += 1; row.max_tokens = data.max_tokens; row.max_cost_micros = data.max_cost_micros; row.updated_by = user.id
    if data.default_input_cost_per_million_micros is not None:
        row.default_input_cost_per_million_micros = data.default_input_cost_per_million_micros
    if data.default_output_cost_per_million_micros is not None:
        row.default_output_cost_per_million_micros = data.default_output_cost_per_million_micros
    session.add(row); recheck_actor(session, user.id); session.commit()
    return service.budget_status(session, workspace_id)


@router.post("/runs", status_code=201)
def create_run(workspace_id: str, data: service.RunInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "ai-runtime", user.id, 60, 3600)
    return service.create_run(session, workspace_id, user.id, data)


@router.get("/runs/{run_id}")
def read_run(workspace_id: str, run_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    row = service.load_run(session, workspace_id, run_id, user.id)
    return service.serialize(session, row, user.id)


@router.post("/runs/{run_id}/cancel")
def cancel(workspace_id: str, run_id: str, data: VersionInput, user=Depends(get_current_user), session=Depends(get_session)):
    row = service.load_run(session, workspace_id, run_id, user.id, authorize=False)
    return service.change_run(session, row, user.id, data.expected_revision)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(workspace_id: str, run_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    row = service.load_run(session, workspace_id, run_id, user.id, authorize=False)
    service.delete_run(session, row, user.id)
    return Response(status_code=204)


@router.post("/runs/{run_id}/resume")
def resume(workspace_id: str, run_id: str, data: ResumeInput, user=Depends(get_current_user), session=Depends(get_session)):
    row = service.load_run(session, workspace_id, run_id, user.id)
    return service.change_run(session, row, user.id, data.expected_revision, resume=True, acknowledge=data.acknowledge_possible_duplicate_charge)


@router.post("/runs/{run_id}/approve")
def approve(workspace_id: str, run_id: str, data: ApproveInput, user=Depends(get_current_user), session=Depends(get_session)):
    row = service.load_run(session, workspace_id, run_id, user.id)
    return service.approve(session, row, user.id, data.expected_revision, data.proposal_id, data.confirm)


@router.post("/complete")
def foreground(workspace_id: str, data: service.RunInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "ai-runtime-foreground", user.id, 60, 3600)
    return service.foreground(session, workspace_id, user.id, data)


@router.post("/plans")
def plan(workspace_id: str, data: PlanInput, user=Depends(get_current_user), session=Depends(get_session)):
    account_limit(session, "ai-planner", user.id, 30, 3600)
    return service.propose_plan(session, workspace_id, user.id, data, data.allowed_profiles)
