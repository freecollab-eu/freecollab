"""Private enterprise management; preservation does not grant content access."""
from datetime import datetime
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, update
from sqlmodel import select

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.data_control import DataControlJob
from ..models.enterprise_governance import (
    GovernanceAudit, GovernanceCase, GovernanceCaseExport, NativeDLPPolicy,
    Organization, OrganizationMember, OrganizationWorkspace,
)
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from ..services import enterprise_governance as service
from ..services.enterprise_kms import status as kms_status
from ..services.account_security import account_limit, ensure_reauthentication, seal_private, state_for, unseal_private
from ..services.auth import active_user_for_token, get_current_user, security
from ..services.data_control import policy_for, require_owner, selected_channels
from ..services.request_guard import recheck_actor
from ..services.sso_scope import require_unrestricted
from ..services.workforce_access import require_personal_account
from ..services.workspaces import lock_workspace


def private(response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(tags=["enterprise governance"], dependencies=[Depends(private)])


class Reauthenticate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)


class OrganizationInput(Reauthenticate):
    name: str = Field(min_length=1, max_length=100)
    country_code: str = Field(default="", pattern=r"^(?:[A-Z]{2})?$")
    region_label: str = Field(default="", max_length=120)


class OrganizationPolicyInput(Reauthenticate):
    require_sso: bool
    expected_revision: int = Field(ge=1)
    country_code: str = Field(default="", pattern=r"^(?:[A-Z]{2})?$")
    region_label: str = Field(default="", max_length=120)


class MemberInput(Reauthenticate):
    role: Literal["owner", "admin", "auditor", "remove"]


class DLPInput(Reauthenticate):
    name: str = Field(min_length=1, max_length=100)
    detector: Literal["literal", "credit_card", "aws_access_key", "email"]
    literal: str | None = Field(default=None, min_length=3, max_length=200)
    action: Literal["warn", "block"]
    surfaces: list[Literal["message", "file", "app", "assistant", "search", "document"]] = Field(min_length=1, max_length=6)
    block_uninspectable_files: bool = False
    enabled: bool = True
    expected_revision: int | None = Field(default=None, ge=1)


class CaseInput(Reauthenticate):
    title: str = Field(min_length=1, max_length=200)
    channel_ids: list[str] = Field(default_factory=list, max_length=100)
    custodian_ids: list[str] = Field(default_factory=list, max_length=100)
    acknowledge_native_scope: Literal[True]


class CaseRelease(Reauthenticate):
    expected_revision: int = Field(ge=1)
    confirmation: Literal["RELEASE PRESERVATION"]


class CaseExport(Reauthenticate):
    channel_ids: list[str] = Field(min_length=1, max_length=20)
    acknowledge_plaintext_export: Literal[True]


class ReviewInput(Reauthenticate):
    review_state: Literal["reviewed", "relevant", "excluded"]


def reauthenticate(session, user, credentials, data):
    require_unrestricted(session)
    require_personal_account(session, user.id)
    account_limit(session, "enterprise-governance", user.id, 60, 3600)
    state_for(session, user.id)
    session.execute(update(User).where(User.id == user.id).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    actor = active_user_for_token(credentials.credentials, session)
    if actor is None:
        raise HTTPException(401, "Account access changed. Please sign in again.")
    ensure_reauthentication(session, actor, data.password, data.code)
    recheck_actor(session, actor.id)
    return actor


def workspace_mutation(session, workspace_id, user, credentials, data):
    require_owner(session, workspace_id, user.id)
    actor = reauthenticate(session, user, credentials, data)
    lock_workspace(session, workspace_id)
    service.lock_governed_channels(session, workspace_id)
    require_owner(session, workspace_id, actor.id)
    recheck_actor(session, actor.id)
    return actor


def organization_payload(session, row, role):
    links = session.exec(select(OrganizationWorkspace.workspace_id, Workspace.name).join(
        Workspace, Workspace.id == OrganizationWorkspace.workspace_id).where(
        OrganizationWorkspace.organization_id == row.id)).all()
    return {"id": row.id, "name": row.name, "role": role, "require_sso": row.require_sso,
        "revision": row.revision, "country_code": row.country_code, "region_label": row.region_label,
        "hosting_metadata_only": True, "workspaces": [{"id": identifier, "name": name} for identifier, name in links],
        "content_access_granted": False}


@router.get("/organizations")
def organizations(user=Depends(get_current_user), session=Depends(get_session)):
    require_unrestricted(session)
    require_personal_account(session, user.id)
    pairs = session.exec(select(Organization, OrganizationMember.role).join(
        OrganizationMember, OrganizationMember.organization_id == Organization.id).where(OrganizationMember.user_id == user.id)).all()
    return {"organizations": [organization_payload(session, row, role) for row, role in pairs]}


@router.post("/organizations", status_code=201)
def create_organization(data: OrganizationInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = reauthenticate(session, user, credentials, data)
    count = session.exec(select(func.count()).select_from(OrganizationMember).where(OrganizationMember.user_id == actor.id)).one()
    if count >= 20:
        raise HTTPException(409, "Organization membership limit reached")
    row = Organization(name=data.name.strip(), country_code=data.country_code, region_label=data.region_label.strip(), created_by=actor.id)
    if not row.name:
        raise HTTPException(400, "Organization name is required")
    session.add(row); session.flush()
    session.add(OrganizationMember(organization_id=row.id, user_id=actor.id, role="owner"))
    service.audit(session, actor.id, "organization.create", row.id, organization_id=row.id)
    session.commit()
    return organization_payload(session, row, "owner")


@router.get("/organizations/{organization_id}")
def organization(organization_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    row, role = service.require_organization(session, organization_id, user.id)
    members = session.exec(select(OrganizationMember.user_id, OrganizationMember.role).where(OrganizationMember.organization_id == organization_id)).all()
    return {**organization_payload(session, row, role), "members": [{"user_id": identifier, "role": grant} for identifier, grant in members]}


@router.put("/organizations/{organization_id}/policy")
def organization_policy(organization_id: str, data: OrganizationPolicyInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_organization(session, organization_id, user.id, owner=True)
    actor = reauthenticate(session, user, credentials, data)
    service.lock_organization(session, organization_id)
    row, role = service.require_organization(session, organization_id, actor.id, owner=True)
    ids = session.exec(select(OrganizationWorkspace.workspace_id).where(OrganizationWorkspace.organization_id == organization_id)).all()
    from ..models.workspace_oidc import WorkspaceOIDCProvider
    for identifier in sorted(ids):
        lock_workspace(session, identifier)
        if data.require_sso:
            provider = session.exec(select(WorkspaceOIDCProvider.enabled).where(WorkspaceOIDCProvider.workspace_id == identifier)).first()
            if not provider:
                raise HTTPException(409, "Enable and test each workspace's own SSO before requiring organization SSO")
    if row.revision != data.expected_revision:
        raise HTTPException(409, "Organization policy changed; reload it")
    row.require_sso, row.country_code, row.region_label = data.require_sso, data.country_code, data.region_label.strip()
    row.revision += 1
    session.add(row); service.audit(session, actor.id, "organization.policy", row.id, organization_id=row.id)
    session.commit()
    return organization_payload(session, row, role)


@router.put("/organizations/{organization_id}/members/{member_id}")
def organization_member(organization_id: str, member_id: str, data: MemberInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_organization(session, organization_id, user.id, owner=True)
    actor = reauthenticate(session, user, credentials, data)
    service.lock_organization(session, organization_id)
    service.require_organization(session, organization_id, actor.id, owner=True)
    target = session.get(User, member_id, populate_existing=True)
    if not target or target.is_blocked:
        raise HTTPException(404, "Active account not found")
    require_personal_account(session, member_id)
    row = session.get(OrganizationMember, (organization_id, member_id), populate_existing=True)
    if row and row.role == "owner" and data.role != "owner":
        owners = session.exec(select(OrganizationMember.user_id).join(User, User.id == OrganizationMember.user_id).where(
            OrganizationMember.organization_id == organization_id, OrganizationMember.role == "owner", User.is_blocked == False)).all()
        if len(owners) <= 1:
            raise HTTPException(409, "Keep at least one active organization owner")
    if data.role == "remove":
        if row: session.delete(row)
    else:
        if row is None:
            count = session.exec(select(func.count()).select_from(OrganizationMember).where(OrganizationMember.organization_id == organization_id)).one()
            if count >= 1000: raise HTTPException(409, "Organization management membership limit reached")
        row = row or OrganizationMember(organization_id=organization_id, user_id=member_id)
        row.role = data.role; session.add(row)
    service.audit(session, actor.id, "organization.member", member_id, organization_id=organization_id)
    session.commit()
    return {"user_id": member_id, "role": data.role, "content_access_granted": False}


@router.post("/organizations/{organization_id}/workspaces/{workspace_id}")
def link_workspace(organization_id: str, workspace_id: str, data: Reauthenticate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    _, role = service.require_organization(session, organization_id, user.id)
    if role == "auditor": raise HTTPException(403, "Organization administrator required")
    actor = reauthenticate(session, user, credentials, data)
    service.lock_organization(session, organization_id)
    org, role = service.require_organization(session, organization_id, actor.id)
    if role == "auditor": raise HTTPException(403, "Organization administrator required")
    lock_workspace(session, workspace_id)
    require_owner(session, workspace_id, actor.id)
    existing = session.get(OrganizationWorkspace, workspace_id, populate_existing=True)
    if existing and existing.organization_id != organization_id:
        raise HTTPException(409, "Workspace already belongs to an organization")
    if not existing:
        count = session.exec(select(func.count()).select_from(OrganizationWorkspace).where(OrganizationWorkspace.organization_id == organization_id)).one()
        if count >= 200: raise HTTPException(409, "Organization workspace limit reached")
    if org.require_sso:
        from ..models.workspace_oidc import WorkspaceOIDCProvider
        if not session.exec(select(WorkspaceOIDCProvider.enabled).where(WorkspaceOIDCProvider.workspace_id == workspace_id)).first():
            raise HTTPException(409, "Enable this workspace's own SSO before attaching it")
    session.add(existing or OrganizationWorkspace(workspace_id=workspace_id, organization_id=organization_id, linked_by=actor.id))
    service.audit(session, actor.id, "organization.workspace_link", workspace_id, organization_id=organization_id, workspace_id=workspace_id)
    session.commit()
    return organization_payload(session, org, role)


@router.delete("/organizations/{organization_id}/workspaces/{workspace_id}")
def unlink_workspace(organization_id: str, workspace_id: str, data: Reauthenticate, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    service.require_organization(session, organization_id, user.id, owner=True)
    actor = reauthenticate(session, user, credentials, data)
    service.lock_organization(session, organization_id)
    service.require_organization(session, organization_id, actor.id, owner=True)
    lock_workspace(session, workspace_id); require_owner(session, workspace_id, actor.id)
    row = session.get(OrganizationWorkspace, workspace_id, populate_existing=True)
    if not row or row.organization_id != organization_id:
        raise HTTPException(404, "Workspace link not found")
    session.delete(row); service.audit(session, actor.id, "organization.workspace_unlink", workspace_id, organization_id=organization_id, workspace_id=workspace_id)
    session.commit()
    return {"unlinked": True}


@router.get("/organizations/{organization_id}/usage")
def usage(organization_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_organization(session, organization_id, user.id)
    return {"workspaces": service.organization_usage(session, organization_id), "contains_plaintext": False, "secure_history_inspected": False}


@router.get("/organizations/{organization_id}/audit")
def organization_audit(organization_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    service.require_organization(session, organization_id, user.id)
    rows = session.exec(select(GovernanceAudit).where(GovernanceAudit.organization_id == organization_id).order_by(
        GovernanceAudit.created_at.desc(), GovernanceAudit.id.desc()).limit(200)).all()
    return {"events": [{"id": row.id, "actor_id": row.actor_id, "action": row.action, "target_id": row.target_id, "created_at": row.created_at} for row in rows],
        "external_immutable_attestation": False}


def dlp_payload(row):
    return {"id": row.id, "name": row.name, "detector": row.detector, "action": row.action,
        "surfaces": json.loads(row.surfaces_json), "enabled": row.enabled, "revision": row.revision,
        "block_uninspectable_files": row.block_uninspectable_files, "has_literal": bool(row.encrypted_literal), "scope": "native_only"}


def case_payload(row):
    return {"id": row.id, "title": unseal_private(row.encrypted_title, "governance-case", row.id),
        "channel_ids": json.loads(row.channel_ids_json), "custodian_ids": json.loads(row.custodian_ids_json),
        "status": row.status, "revision": row.revision, "created_at": row.created_at, "released_at": row.released_at,
        "scope": "native_server_content", "matrix_content_preserved": False}


@router.get("/workspaces/{workspace_id}/governance")
def workspace_status(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_owner(session, workspace_id, user.id)
    policies = session.exec(select(NativeDLPPolicy).where(NativeDLPPolicy.workspace_id == workspace_id)).all()
    cases = session.exec(select(GovernanceCase).where(GovernanceCase.workspace_id == workspace_id).order_by(GovernanceCase.created_at.desc()).limit(200)).all()
    retention = policy_for(session, workspace_id)
    link = session.get(OrganizationWorkspace, workspace_id)
    return {"organization_id": link.organization_id if link else None, "policies": [dlp_payload(row) for row in policies],
        "cases": [case_payload(row) for row in cases], "retention_hold": retention.hold,
        "case_hold_active": any(row.status == "active" for row in cases),
        "encryption": {"native": "server_processed", "secure": "client_held_matrix", "dlp_scope": "native_only"},
        "kms": kms_status()}


def save_dlp(session, row, data, actor):
    if not data.name.strip():
        raise HTTPException(400, "A content policy name is required")
    if len(set(data.surfaces)) != len(data.surfaces):
        raise HTTPException(400, "Select distinct native content surfaces")
    if data.detector == "literal" and not data.literal and not row.encrypted_literal:
        raise HTTPException(400, "A literal detector requires a protected phrase")
    if data.detector != "literal" and data.literal:
        raise HTTPException(400, "Only literal detectors accept a phrase")
    row.name, row.detector, row.action, row.enabled = data.name.strip(), data.detector, data.action, data.enabled
    row.surfaces_json = json.dumps(sorted(data.surfaces)); row.block_uninspectable_files = data.block_uninspectable_files
    row.updated_at = datetime.utcnow()
    if data.literal:
        row.encrypted_literal = seal_private(data.literal, "native-dlp", row.id)
    elif data.detector != "literal":
        row.encrypted_literal = None
    session.add(row); service.audit(session, actor.id, "dlp.configure", row.id, workspace_id=row.workspace_id)


@router.post("/workspaces/{workspace_id}/governance/dlp", status_code=201)
def create_dlp(workspace_id: str, data: DLPInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = workspace_mutation(session, workspace_id, user, credentials, data)
    count = session.exec(select(func.count()).select_from(NativeDLPPolicy).where(NativeDLPPolicy.workspace_id == workspace_id)).one()
    if count >= 50: raise HTTPException(409, "Native content policy limit reached")
    row = NativeDLPPolicy(workspace_id=workspace_id, name=data.name, detector=data.detector, created_by=actor.id)
    save_dlp(session, row, data, actor); session.commit()
    return dlp_payload(row)


@router.put("/workspaces/{workspace_id}/governance/dlp/{policy_id}")
def update_dlp(workspace_id: str, policy_id: str, data: DLPInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = workspace_mutation(session, workspace_id, user, credentials, data)
    row = session.get(NativeDLPPolicy, policy_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id: raise HTTPException(404, "Native content policy not found")
    if data.expected_revision != row.revision: raise HTTPException(409, "Native content policy changed; reload it")
    row.revision += 1; save_dlp(session, row, data, actor); session.commit()
    return dlp_payload(row)


@router.post("/workspaces/{workspace_id}/governance/cases", status_code=201)
def create_case(workspace_id: str, data: CaseInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = workspace_mutation(session, workspace_id, user, credentials, data)
    if not data.title.strip(): raise HTTPException(400, "A preservation case title is required")
    count = session.exec(select(func.count()).select_from(GovernanceCase).where(GovernanceCase.workspace_id == workspace_id)).one()
    if count >= 200: raise HTTPException(409, "Preservation case limit reached")
    if len(set(data.channel_ids)) != len(data.channel_ids) or len(set(data.custodian_ids)) != len(data.custodian_ids):
        raise HTTPException(400, "Select distinct preservation scopes")
    for identifier in data.channel_ids:
        mapping = session.get(WorkspaceChannel, identifier)
        if not mapping or mapping.workspace_id != workspace_id: raise HTTPException(404, "Channel scope not found in this workspace")
    for identifier in data.custodian_ids:
        if not session.get(WorkspaceMember, (workspace_id, identifier)): raise HTTPException(404, "Custodian not found in this workspace")
    row = GovernanceCase(workspace_id=workspace_id, encrypted_title="", channel_ids_json=json.dumps(sorted(data.channel_ids)),
        custodian_ids_json=json.dumps(sorted(data.custodian_ids)), created_by=actor.id)
    row.encrypted_title = seal_private(data.title.strip(), "governance-case", row.id)
    session.add(row); service.audit(session, actor.id, "case.preserve", row.id, workspace_id=workspace_id)
    session.commit()
    return case_payload(row)


def owned_case(session, workspace_id, case_id, actor_id):
    require_owner(session, workspace_id, actor_id)
    row = session.get(GovernanceCase, case_id, populate_existing=True)
    if not row or row.workspace_id != workspace_id: raise HTTPException(404, "Preservation case not found")
    return row


@router.post("/workspaces/{workspace_id}/governance/cases/{case_id}/release")
def release_case(workspace_id: str, case_id: str, data: CaseRelease, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    actor = workspace_mutation(session, workspace_id, user, credentials, data)
    row = owned_case(session, workspace_id, case_id, actor.id)
    if row.status != "active" or row.revision != data.expected_revision:
        raise HTTPException(409, "Preservation case changed; reload it")
    row.status = "released"; row.released_at = datetime.utcnow(); row.revision += 1
    session.add(row); service.audit(session, actor.id, "case.release", row.id, workspace_id=workspace_id)
    session.commit()
    return case_payload(row)


@router.post("/workspaces/{workspace_id}/governance/cases/{case_id}/exports", status_code=202)
def case_export(workspace_id: str, case_id: str, data: CaseExport, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    from .data_control import queue_job
    actor = workspace_mutation(session, workspace_id, user, credentials, data)
    row = owned_case(session, workspace_id, case_id, actor.id)
    if row.status != "active": raise HTTPException(409, "Activate preservation before collecting evidence")
    selected_channels(session, workspace_id, actor.id, data.channel_ids)
    from ..models.secure_channel import SecureChannel
    if any(session.get(SecureChannel, identifier) for identifier in data.channel_ids):
        raise HTTPException(409, "This evidence collector cannot decrypt or preserve client-held Matrix history")
    held = json.loads(row.channel_ids_json)
    if held and any(identifier not in held for identifier in data.channel_ids):
        raise HTTPException(400, "Evidence collection must remain within the case's preserved channels")
    # Custodian cases preserve only the selected authors but collect the full
    # authorized channel context, explicitly reported in the result.
    state = state_for(session, actor.id)
    def bind(job):
        session.add(GovernanceCaseExport(job_id=job.id, case_id=row.id, requested_by=actor.id, case_revision=row.revision))
        service.audit(session, actor.id, "case.export_queue", job.id, workspace_id=workspace_id)
    result = queue_job(session, actor, state, credentials, workspace_id, "export",
        {"channel_ids": sorted(data.channel_ids), "governance_case_id": row.id}, before_commit=bind)
    return {**result, "case_id": row.id, "collection_scope": "authorized_native_channel_context"}


@router.get("/workspaces/{workspace_id}/governance/cases/{case_id}/exports")
def case_exports(workspace_id: str, case_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    from .data_control import job_payload
    owned_case(session, workspace_id, case_id, user.id)
    pairs = session.exec(select(GovernanceCaseExport, DataControlJob).join(DataControlJob, DataControlJob.id == GovernanceCaseExport.job_id).where(
        GovernanceCaseExport.case_id == case_id, GovernanceCaseExport.requested_by == user.id).order_by(DataControlJob.created_at.desc())).all()
    return {"exports": [{**job_payload(job), "review_state": record.review_state, "reviewed_at": record.reviewed_at} for record, job in pairs]}


@router.post("/workspaces/{workspace_id}/governance/cases/{case_id}/exports/{job_id}/review")
def review_export(workspace_id: str, case_id: str, job_id: str, data: ReviewInput, user=Depends(get_current_user), credentials=Depends(security), session=Depends(get_session)):
    from ..services.account_security import unseal_private
    actor = workspace_mutation(session, workspace_id, user, credentials, data)
    owned_case(session, workspace_id, case_id, actor.id)
    record = session.get(GovernanceCaseExport, job_id, populate_existing=True)
    job = session.get(DataControlJob, job_id, populate_existing=True)
    if not record or record.case_id != case_id or record.requested_by != actor.id or not job or job.workspace_id != workspace_id:
        raise HTTPException(404, "Evidence collection not found")
    if job.status != "ready": raise HTTPException(409, "Collect and inspect evidence before recording review")
    request = json.loads(unseal_private(job.encrypted_request, "data-job", job.id))
    selected_channels(session, workspace_id, actor.id, request["channel_ids"])
    record.review_state, record.reviewed_by, record.reviewed_at = data.review_state, actor.id, datetime.utcnow()
    session.add(record); service.audit(session, actor.id, "case.review", job_id, workspace_id=workspace_id)
    session.commit()
    return {"job_id": job_id, "review_state": record.review_state, "reviewed_at": record.reviewed_at}


@router.get("/workspaces/{workspace_id}/governance/audit")
def workspace_audit(workspace_id: str, user=Depends(get_current_user), session=Depends(get_session)):
    require_owner(session, workspace_id, user.id)
    rows = session.exec(select(GovernanceAudit).where(GovernanceAudit.workspace_id == workspace_id).order_by(
        GovernanceAudit.created_at.desc(), GovernanceAudit.id.desc()).limit(200)).all()
    return {"events": [{"id": row.id, "actor_id": row.actor_id, "action": row.action, "target_id": row.target_id, "created_at": row.created_at} for row in rows],
        "external_immutable_attestation": False}
