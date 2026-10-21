"""Workspace-owned SCIM 2.0 and native-admin credential/mapping controls."""
from datetime import datetime
import hmac
import json
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.channel import Channel, ChannelMember
from ..models.scim import (SCIMAudit, SCIMChannelGrant, SCIMChannelMapping, SCIMDirectory,
                          SCIMGroup, SCIMGroupMapping, SCIMGroupMember, SCIMUser)
from ..models.secure_channel import SecureChannel
from ..models.user import User
from ..models.workforce import WorkforcePrincipal
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from ..services import scim as service
from ..services.account_security import account_limit, digest, ensure_reauthentication
from ..services.auth import active_user_for_token, get_current_user, security
from ..services.rate_limit import request_address
from ..services.sso_scope import require_unrestricted
from ..services.workforce import ensure_principal
from ..services.workspaces import lock_workspace, require_workspace_admin


class SCIMResponse(JSONResponse):
    media_type = "application/scim+json"


class SCIMRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def handle(request):
            try:
                result = await handler(request)
                result.headers["Cache-Control"] = "no-store"
                return result
            except (HTTPException, RequestValidationError) as exc:
                if isinstance(exc, HTTPException):
                    status, detail, headers = exc.status_code, exc.detail, exc.headers or {}
                    body = detail if isinstance(detail, dict) and detail.get("schemas") == [service.ERROR_SCHEMA] else {
                        "schemas": [service.ERROR_SCHEMA], "status": str(status),
                        "detail": detail if isinstance(detail, str) else "This SCIM request could not be completed"}
                else:
                    status, headers = 400, {}
                    body = {"schemas": [service.ERROR_SCHEMA], "status": "400", "scimType": "invalidValue",
                            "detail": "Invalid SCIM request parameters"}
                return SCIMResponse(body, status_code=status, headers={**headers, "Cache-Control": "no-store"})
        return handle


router = APIRouter(prefix="/scim/v2/{directory_id}", tags=["customer provisioning"],
                   route_class=SCIMRoute, default_response_class=SCIMResponse)
admin_router = APIRouter(prefix="/workspaces/{workspace_id}/scim", tags=["customer provisioning administration"])


class Reauth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=72)
    code: str | None = Field(default=None, max_length=64)


class ChannelMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str = Field(min_length=1, max_length=128)
    role: str = Field(pattern="^(member|admin)$")


class GroupMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(min_length=1, max_length=128)
    workspace_role: str | None = Field(default=None, pattern="^(guest|member|admin)$")
    channels: list[ChannelMapping] = Field(default_factory=list, max_length=100)


class Mappings(Reauth):
    default_role: str | None = Field(default=None, pattern="^(guest|member)$")
    mappings: list[GroupMapping] = Field(max_length=200)


def _directory(session, workspace_id, creator=None):
    row = session.get(SCIMDirectory, workspace_id)
    if row is None and creator:
        row = SCIMDirectory(workspace_id=workspace_id, created_by=creator)
        session.add(row)
        session.flush()
    return row


def _admin_read(session, workspace_id, user):
    require_unrestricted(session)
    return require_workspace_admin(session, workspace_id, user.id)


def _admin_write(session, workspace_id, user, credentials, data):
    require_unrestricted(session)
    account_limit(session, "scim-admin", user.id, 30, 300)
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id == user.id).values(auth_version=AccountSecurity.auth_version))
    session.expire_all()
    actor = active_user_for_token(credentials.credentials, session)
    if actor is None:
        raise HTTPException(401, "Account security changed. Sign in again.")
    require_unrestricted(session)
    lock_workspace(session, workspace_id)
    member = require_workspace_admin(session, workspace_id, user.id)
    ensure_reauthentication(session, actor, data.password, data.code)
    return member


def _status(session, directory):
    users = session.exec(select(func.count()).select_from(SCIMUser).where(
        SCIMUser.workspace_id == directory.workspace_id, SCIMUser.deleted == False)).one()
    active = session.exec(select(func.count()).select_from(SCIMUser).where(
        SCIMUser.workspace_id == directory.workspace_id, SCIMUser.deleted == False, SCIMUser.active == True)).one()
    groups = session.exec(select(func.count()).select_from(SCIMGroup).where(SCIMGroup.workspace_id == directory.workspace_id)).one()
    return {"directory_id": directory.directory_id, "endpoint_url": service.endpoint(directory),
            "enabled": directory.enabled, "configured": directory.token_hash is not None,
            "default_role": directory.default_role,
            "credential": {"version": directory.token_version, "rotated_at": service.utc(directory.rotated_at),
                           "last_used_at": service.utc(directory.last_used_at),
                           "requires_rotation": not _issuer_active(session, directory)} if directory.token_version else None,
            "counts": {"users": users, "active_users": active, "groups": groups}}


@admin_router.get("")
def status(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _admin_read(session, workspace_id, user)
    lock_workspace(session, workspace_id)
    directory = _directory(session, workspace_id, user.id)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return _status(session, directory)


@admin_router.post("/token")
def rotate_token(workspace_id: str, data: Reauth, response: Response, user=Depends(get_current_user),
                 credentials=Depends(security), session=Depends(get_session)):
    actor = _admin_write(session, workspace_id, user, credentials, data)
    if actor.role != "owner":
        raise HTTPException(403, "Only a workspace owner can create or rotate provisioning credentials")
    directory = _directory(session, workspace_id, user.id)
    token = "fc_scim_" + secrets.token_urlsafe(48)
    directory.token_hash, directory.enabled = digest(token), True
    directory.token_issued_by = user.id
    directory.token_version += 1
    directory.rotated_at, directory.last_used_at = datetime.utcnow(), None
    session.add(directory)
    service.audit(session, workspace_id, "credential_rotated", "Directory", directory.directory_id)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {**_status(session, directory), "token": token}


@admin_router.post("/disable")
def disable(workspace_id: str, data: Reauth, response: Response, user=Depends(get_current_user),
            credentials=Depends(security), session=Depends(get_session)):
    _admin_write(session, workspace_id, user, credentials, data)
    directory = _directory(session, workspace_id, user.id)
    directory.enabled, directory.token_hash = False, None
    session.add(directory)
    service.audit(session, workspace_id, "credential_disabled", "Directory", directory.directory_id)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return _status(session, directory)


def _mappings(session, directory, actor_id):
    groups = session.exec(select(SCIMGroup).where(SCIMGroup.workspace_id == directory.workspace_id).order_by(SCIMGroup.display_name)).all()
    mappings = []
    for group in groups:
        config = session.get(SCIMGroupMapping, group.id)
        channel_rows = session.exec(select(SCIMChannelMapping).where(SCIMChannelMapping.group_id == group.id)).all()
        if config or channel_rows:
            mappings.append({"group_id": group.id, "group_name": group.display_name,
                             "workspace_role": config.workspace_role if config else None,
                             "channels": [{"channel_id": row.channel_id, "role": row.role,
                                           "requires_reapproval": not service.channel_approval_active(session, directory.workspace_id, row)}
                                          for row in channel_rows]})
    channels = session.exec(select(Channel).join(WorkspaceChannel, WorkspaceChannel.channel_id == Channel.id).join(
        ChannelMember, ChannelMember.channel_id == Channel.id).where(WorkspaceChannel.workspace_id == directory.workspace_id,
        ChannelMember.user_id == actor_id, ChannelMember.role == "admin", Channel.type != "direct")).all()
    return {"default_role": directory.default_role, "mappings": mappings,
            "groups": [{"id": group.id, "display_name": group.display_name, "member_count": session.exec(
                select(func.count()).select_from(SCIMGroupMember).where(SCIMGroupMember.group_id == group.id)).one()} for group in groups],
            "channels": [{"id": channel.id, "name": channel.name if session.get(SecureChannel, channel.id) is None else None,
                          "secure": session.get(SecureChannel, channel.id) is not None} for channel in channels]}


@admin_router.get("/mappings")
def mappings(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _admin_read(session, workspace_id, user)
    directory = _directory(session, workspace_id, user.id)
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return _mappings(session, directory, user.id)


@admin_router.put("/mappings")
def save_mappings(workspace_id: str, data: Mappings, response: Response, user=Depends(get_current_user),
                  credentials=Depends(security), session=Depends(get_session)):
    actor = _admin_write(session, workspace_id, user, credentials, data)
    directory = _directory(session, workspace_id, user.id)
    groups = {group.id: group for group in session.exec(select(SCIMGroup).where(SCIMGroup.workspace_id == workspace_id)).all()}
    if len({mapping.group_id for mapping in data.mappings}) != len(data.mappings):
        raise HTTPException(422, "A group can be mapped only once")
    supplied = {mapping.group_id: mapping for mapping in data.mappings}
    previous_channels = {(mapping.group_id, mapping.channel_id): mapping for mapping in session.exec(
        select(SCIMChannelMapping).where(SCIMChannelMapping.group_id.in_(list(groups)))).all()} if groups else {}
    for mapping in data.mappings:
        if mapping.group_id not in groups:
            raise HTTPException(404, "Group not found")
        previous = session.get(SCIMGroupMapping, mapping.group_id)
        was_admin = bool(previous and previous.workspace_role == "admin")
        if (mapping.workspace_role == "admin") != was_admin and actor.role != "owner":
            raise HTTPException(403, "Only an owner can change a workspace administrator mapping")
        if len({channel.channel_id for channel in mapping.channels}) != len(mapping.channels):
            raise HTTPException(422, "A channel can be mapped only once per group")
        old = {item.channel_id: item.role for item in session.exec(select(SCIMChannelMapping).where(
            SCIMChannelMapping.group_id == mapping.group_id)).all()}
        new = {item.channel_id: item.role for item in mapping.channels}
        for channel_id in set(old) | set(new):
            if old.get(channel_id) == new.get(channel_id):
                continue
            channel = session.get(Channel, channel_id)
            scope = session.get(WorkspaceChannel, channel_id)
            member = session.get(ChannelMember, (channel_id, user.id))
            if not channel or not scope or scope.workspace_id != workspace_id:
                raise HTTPException(404, "Channel not found")
            if channel.type == "direct" or not member or member.role != "admin":
                raise HTTPException(403, "Current channel administrator access is required for every changed grant")
    # Removing a mapping also requires authority over its channel grants.
    for group_id in set(groups) - set(supplied):
        old_mapping = session.get(SCIMGroupMapping, group_id)
        if old_mapping and old_mapping.workspace_role == "admin" and actor.role != "owner":
            raise HTTPException(403, "Only an owner can remove a workspace administrator mapping")
        for old in session.exec(select(SCIMChannelMapping).where(SCIMChannelMapping.group_id == group_id)).all():
            member = session.get(ChannelMember, (old.channel_id, user.id))
            if not member or member.role != "admin":
                raise HTTPException(403, "Current channel administrator access is required to remove this grant")
    old_default = directory.default_role
    if data.default_role:
        directory.default_role = data.default_role
        session.add(directory)
    for group_id in groups:
        session.execute(delete(SCIMChannelMapping).where(SCIMChannelMapping.group_id == group_id))
        previous = session.get(SCIMGroupMapping, group_id)
        if previous:
            session.delete(previous)
    session.flush()
    for mapping in data.mappings:
        session.add(SCIMGroupMapping(group_id=mapping.group_id, workspace_role=mapping.workspace_role))
        for channel in mapping.channels:
            old = previous_channels.get((mapping.group_id, channel.channel_id))
            current_member = session.get(ChannelMember, (channel.channel_id, user.id))
            approver = user.id if current_member and current_member.role == "admin" else old.granted_by if old else user.id
            session.add(SCIMChannelMapping(group_id=mapping.group_id, channel_id=channel.channel_id,
                                          role=channel.role, granted_by=approver))
    session.flush()
    changed_channels = set()
    for row in session.exec(select(SCIMUser).where(SCIMUser.workspace_id == workspace_id, SCIMUser.deleted == False)).all():
        if old_default != directory.default_role and row.base_role == old_default:
            row.base_role = directory.default_role
            session.add(row)
        changed_channels.update(service.reconcile_user(session, directory, row))
    service.audit(session, workspace_id, "group_mappings_updated", "Directory", directory.directory_id)
    session.commit()
    service.sync_channels_after_commit(session, directory, changed_channels)
    response.headers["Cache-Control"] = "no-store"
    return _mappings(session, directory, user.id)


@admin_router.get("/audit")
def audit(workspace_id: str, response: Response, user=Depends(get_current_user), session=Depends(get_session)):
    _admin_read(session, workspace_id, user)
    rows = session.exec(select(SCIMAudit).where(SCIMAudit.workspace_id == workspace_id).order_by(SCIMAudit.created_at.desc()).limit(100)).all()
    response.headers["Cache-Control"] = "no-store"
    return {"events": [{"id": row.id, "action": row.action, "resource_type": row.resource_type,
                        "resource_id": row.resource_id, "created_at": service.utc(row.created_at)} for row in rows]}


def _issuer_active(session, directory):
    if not directory.token_issued_by:
        return False
    issuer = session.get(User, directory.token_issued_by, populate_existing=True)
    role = session.exec(select(WorkspaceMember.role).where(WorkspaceMember.workspace_id == directory.workspace_id,
        WorkspaceMember.user_id == directory.token_issued_by)).first()
    return bool(issuer and not issuer.is_blocked and role == "owner")


def authenticate(directory_id: str, request: Request, session=Depends(get_session)):
    account_limit(session, "scim-address", request_address(request), 600, 60)
    authorization = request.headers.get("authorization", "")
    parts = authorization.split()
    token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    token_hash = digest(token) if len(token) <= 512 else digest("")
    directory = session.exec(select(SCIMDirectory).where(SCIMDirectory.directory_id == directory_id)).first()
    expected = directory.token_hash if directory and directory.token_hash else "0" * 64
    valid = hmac.compare_digest(token_hash, expected)
    if (not valid or not directory or not directory.enabled or not session.get(Workspace, directory.workspace_id)
            or not _issuer_active(session, directory)):
        raise HTTPException(401, "A valid workspace provisioning token is required", headers={"WWW-Authenticate": "Bearer"})
    directory.last_used_at = datetime.utcnow()
    session.add(directory)
    session.commit()
    from ..services.security_audit import authenticated_actor
    authenticated_actor(request, directory.directory_id, kind="directory", workspace_id=directory.workspace_id)
    return directory, token_hash


def lock_directory(session, authentication):
    directory, token_hash = authentication
    lock_workspace(session, directory.workspace_id)
    session.expire_all()
    current = session.get(SCIMDirectory, directory.workspace_id, populate_existing=True)
    if (not current or not current.enabled or not hmac.compare_digest(current.token_hash or "", token_hash)
            or not _issuer_active(session, current)):
        raise HTTPException(401, "The provisioning credential is no longer valid")
    return current


async def body(request):
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        service.fail(413, "SCIM payload is too large", "tooMany")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        service.fail(400, "A valid JSON object is required", "invalidSyntax")


def resource_headers(response, resource):
    response.headers["ETag"] = resource["meta"]["version"]
    response.headers["Location"] = resource["meta"]["location"]


@router.get("/ServiceProviderConfig")
def provider_config(authentication=Depends(authenticate)):
    directory, _ = authentication
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 100}, "changePassword": {"supported": False},
            "sort": {"supported": False}, "etag": {"supported": True},
            "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Workspace bearer token",
                "description": "A dedicated, revocable customer-workspace provisioning credential", "primary": True}],
            "meta": {"resourceType": "ServiceProviderConfig", "location": service.endpoint(directory) + "/ServiceProviderConfig"}}


def _resource_types(directory):
    base = service.endpoint(directory)
    return [{"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User", "endpoint": "/Users",
             "schema": service.USER_SCHEMA, "schemaExtensions": [{"schema": service.ENTERPRISE_SCHEMA, "required": False}],
             "meta": {"resourceType": "ResourceType", "location": base + "/ResourceTypes/User"}},
            {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "Group", "name": "Group", "endpoint": "/Groups",
             "schema": service.GROUP_SCHEMA, "meta": {"resourceType": "ResourceType", "location": base + "/ResourceTypes/Group"}}]


@router.get("/ResourceTypes")
def resource_types(authentication=Depends(authenticate)):
    rows = _resource_types(authentication[0])
    return {"schemas": [service.LIST_SCHEMA], "totalResults": len(rows), "startIndex": 1, "itemsPerPage": len(rows), "Resources": rows}


@router.get("/ResourceTypes/{resource_type}")
def resource_type(resource_type: str, authentication=Depends(authenticate)):
    row = next((row for row in _resource_types(authentication[0]) if row["id"] == resource_type), None)
    if row is None:
        service.fail(404, "Resource type not found")
    return row


def _schemas():
    def attribute(name, kind="string", multi=False, required=False, mutability="readWrite", **extra):
        return {"name": name, "type": kind, "multiValued": multi, "required": required, "caseExact": False,
                "mutability": mutability, "returned": "default", "uniqueness": "none", **extra}
    return [{"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"], "id": service.USER_SCHEMA, "name": "User",
             "attributes": [attribute("userName", required=True, uniqueness="server"), attribute("externalId"),
                attribute("displayName"), attribute("active", "boolean"),
                attribute("name", "complex", subAttributes=[attribute("givenName"), attribute("familyName"), attribute("formatted")]),
                attribute("emails", "complex", True, subAttributes=[attribute("value"), attribute("type"), attribute("primary", "boolean")]),
                attribute("groups", "complex", True, mutability="readOnly", subAttributes=[attribute("value"), attribute("display"), attribute("type")])]},
            {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"], "id": service.GROUP_SCHEMA, "name": "Group",
             "attributes": [attribute("displayName", required=True), attribute("externalId"),
                attribute("members", "complex", True, subAttributes=[attribute("value", required=True), attribute("display", mutability="readOnly")])]},
            {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"], "id": service.ENTERPRISE_SCHEMA, "name": "EnterpriseUser",
             "attributes": [attribute(name) for name in ("employeeNumber", "costCenter", "organization", "division", "department")] +
                [attribute("manager", "complex", subAttributes=[attribute("value")])]}]


@router.get("/Schemas")
def schemas(authentication=Depends(authenticate)):
    rows = _schemas()
    return {"schemas": [service.LIST_SCHEMA], "totalResults": len(rows), "startIndex": 1, "itemsPerPage": len(rows), "Resources": rows}


@router.get("/Schemas/{schema_id}")
def schema(schema_id: str, authentication=Depends(authenticate)):
    result = next((row for row in _schemas() if row["id"] == schema_id), None)
    if result is None:
        service.fail(404, "Schema not found")
    return result


@router.get("/Users")
def users(filter: str | None = Query(default=None, max_length=2048), startIndex: int = Query(default=1, ge=1),
          count: int = Query(default=100, ge=0, le=100), authentication=Depends(authenticate), session=Depends(get_session)):
    directory, _ = authentication
    statement = select(SCIMUser).where(SCIMUser.workspace_id == directory.workspace_id, SCIMUser.deleted == False)
    rows = session.exec(service.filter_statement(statement, SCIMUser, filter).order_by(SCIMUser.created_at, SCIMUser.id)).all()
    page = rows[startIndex - 1:startIndex - 1 + count]
    return {"schemas": [service.LIST_SCHEMA], "totalResults": len(rows), "startIndex": startIndex,
            "itemsPerPage": len(page), "Resources": [service.user_payload(session, directory, row) for row in page]}


@router.get("/Users/{resource_id}")
def user(resource_id: str, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    directory, _ = authentication
    row = service.scoped_user(session, directory, resource_id)
    result = service.user_payload(session, directory, row)
    resource_headers(response, result)
    return result


@router.post("/Users", status_code=201)
async def create_user(request: Request, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    directory = lock_directory(session, authentication)
    data = service.profile(await body(request))
    matches = session.exec(select(SCIMUser).where(SCIMUser.workspace_id == directory.workspace_id,
        ((SCIMUser.user_name_normalized == data["userName"].casefold()) | (SCIMUser.external_id == data["externalId"])))).all()
    if matches:
        row = matches[0]
        if len(matches) != 1 or row.external_id != data["externalId"] or row.user_name_normalized != data["userName"].casefold():
            service.fail(409, "userName or externalId already exists", "uniqueness")
        if not row.deleted:
            response.status_code = 200
            result = service.user_payload(session, directory, row)
            resource_headers(response, result)
            return result
        row.deleted = False
        service.update_profile(session, directory, row, data)
        if data["active"]:
            service.activate(session, directory, row, data)
        else:
            row.active = False
        service.touch(row)
    else:
        count = session.exec(select(func.count()).select_from(SCIMUser).where(SCIMUser.workspace_id == directory.workspace_id)).one()
        if count >= 10000:
            service.fail(409, "Directory user limit reached", "tooMany")
        # ensure_principal never looks up or mutates a global account by email.
        service.reject_personal_binding(session, directory, data["externalId"])
        previous_principal = session.exec(select(WorkforcePrincipal).where(WorkforcePrincipal.workspace_id == directory.workspace_id,
            WorkforcePrincipal.external_id == data["externalId"])).first()
        initial_active = data["active"] or bool(previous_principal and previous_principal.active)
        principal = ensure_principal(session, directory.workspace_id, data["externalId"], service.display_name(data),
                                     service.email_address(data), active=initial_active, role=directory.default_role)
        if session.exec(select(SCIMUser.id).where(SCIMUser.user_id == principal.user_id)).first():
            service.fail(409, "This managed identity already has a directory record", "uniqueness")
        row = SCIMUser(workspace_id=directory.workspace_id, user_id=principal.user_id, user_name=data["userName"],
                       user_name_normalized=data["userName"].casefold(), external_id=data["externalId"], active=initial_active,
                       base_role=directory.default_role, applied_role=directory.default_role)
        session.add(row)
        service.update_profile(session, directory, row, data)
        if initial_active and not data["active"]:
            await service.deactivate(session, directory, row)
    session.add(row)
    service.audit(session, directory.workspace_id, "user_provisioned", "User", row.id)
    session.commit()
    result = service.user_payload(session, directory, row)
    resource_headers(response, result)
    return result


def patch_user(data, initial):
    operations = data.get("Operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        service.fail(400, "Operations must contain between 1 and 100 operations", "invalidSyntax")
    result = json.loads(json.dumps(initial))
    canonical = {value.lower(): value for value in ("userName", "externalId", "displayName", "nickName", "title", "userType", "preferredLanguage", "locale", "timezone", "active", "emails", "name")}
    for operation in operations:
        if not isinstance(operation, dict) or str(operation.get("op", "")).lower() not in {"add", "replace", "remove"}:
            service.fail(400, "Invalid patch operation", "invalidSyntax")
        action, path, value = operation["op"].lower(), operation.get("path"), operation.get("value")
        if not path:
            if action == "remove" or not isinstance(value, dict):
                service.fail(400, "A value object is required", "invalidValue")
            result = service.profile(value, result)
            continue
        if not isinstance(path, str):
            service.fail(400, "Invalid patch path", "invalidPath")
        if path.lower() in canonical:
            key = canonical[path.lower()]
            if action == "remove":
                if key in {"userName", "active"}:
                    service.fail(400, "This attribute cannot be removed", "mutability")
                result.pop(key, None)
            else:
                result[key] = value
        elif path.lower().startswith("name."):
            key = next((key for key in ("givenName", "familyName", "formatted", "middleName") if key.lower() == path[5:].lower()), None)
            if key is None:
                service.fail(400, "Unsupported name attribute", "invalidPath")
            result.setdefault("name", {})
            if action == "remove": result["name"].pop(key, None)
            else: result["name"][key] = value
        elif path.startswith(service.ENTERPRISE_SCHEMA + ":"):
            key = path[len(service.ENTERPRISE_SCHEMA) + 1:]
            if key not in {"employeeNumber", "costCenter", "organization", "division", "department"}:
                service.fail(400, "Unsupported enterprise attribute", "invalidPath")
            result.setdefault(service.ENTERPRISE_SCHEMA, {})
            if action == "remove": result[service.ENTERPRISE_SCHEMA].pop(key, None)
            else: result[service.ENTERPRISE_SCHEMA][key] = value
        else:
            service.fail(400, "Unsupported patch path", "invalidPath")
    return service.profile(result)


async def _update_user(resource_id, request, response, authentication, session, replace):
    directory = lock_directory(session, authentication)
    row = service.scoped_user(session, directory, resource_id)
    service.check_version(row, request.headers.get("if-match"))
    raw = await body(request)
    if raw.get("id") not in {None, row.id}:
        service.fail(400, "id is read-only", "mutability")
    initial = {**json.loads(row.profile_json), "userName": row.user_name, "externalId": row.external_id, "active": row.active}
    data = service.profile(raw) if replace else patch_user(raw, initial)
    before_active = row.active
    service.update_profile(session, directory, row, data)
    service.touch(row)
    service.audit(session, directory.workspace_id, "user_updated" if data["active"] else "user_deactivated", "User", row.id)
    if before_active and not data["active"]:
        await service.deactivate(session, directory, row)
    elif not before_active and data["active"]:
        service.activate(session, directory, row, data)
    changed = service.reconcile_user(session, directory, row)
    session.commit()
    service.sync_channels_after_commit(session, directory, changed)
    result = service.user_payload(session, directory, row)
    resource_headers(response, result)
    return result


@router.put("/Users/{resource_id}")
async def replace_user(resource_id: str, request: Request, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    return await _update_user(resource_id, request, response, authentication, session, True)


@router.patch("/Users/{resource_id}")
async def update_user(resource_id: str, request: Request, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    return await _update_user(resource_id, request, response, authentication, session, False)


@router.delete("/Users/{resource_id}", status_code=204, response_class=Response)
async def delete_user(resource_id: str, request: Request, authentication=Depends(authenticate), session=Depends(get_session)):
    directory = lock_directory(session, authentication)
    row = service.scoped_user(session, directory, resource_id, include_deleted=True)
    if row.deleted:
        return Response(status_code=204)
    service.check_version(row, request.headers.get("if-match"))
    row.deleted = True
    group_ids = session.exec(select(SCIMGroupMember.group_id).where(SCIMGroupMember.scim_user_id == row.id)).all()
    for group_id in group_ids:
        group = session.get(SCIMGroup, group_id)
        if group:
            service.touch(group)
            session.add(group)
    session.execute(delete(SCIMGroupMember).where(SCIMGroupMember.scim_user_id == row.id))
    service.touch(row)
    service.audit(session, directory.workspace_id, "user_deleted", "User", row.id)
    await service.deactivate(session, directory, row)
    session.commit()
    return Response(status_code=204)


@router.get("/Groups")
def groups(filter: str | None = Query(default=None, max_length=2048), startIndex: int = Query(default=1, ge=1),
           count: int = Query(default=100, ge=0, le=100), authentication=Depends(authenticate), session=Depends(get_session)):
    directory, _ = authentication
    statement = service.filter_statement(select(SCIMGroup).where(SCIMGroup.workspace_id == directory.workspace_id), SCIMGroup, filter)
    rows = session.exec(statement.order_by(SCIMGroup.created_at, SCIMGroup.id)).all()
    page = rows[startIndex - 1:startIndex - 1 + count]
    return {"schemas": [service.LIST_SCHEMA], "totalResults": len(rows), "startIndex": startIndex, "itemsPerPage": len(page),
            "Resources": [service.group_payload(session, directory, row) for row in page]}


@router.get("/Groups/{resource_id}")
def group(resource_id: str, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    directory, _ = authentication
    result = service.group_payload(session, directory, service.scoped_group(session, directory, resource_id))
    resource_headers(response, result)
    return result


def group_data(session, directory, raw, existing=None):
    if not isinstance(raw, dict):
        service.fail(400, "A group object is required", "invalidSyntax")
    name = service.text(raw.get("displayName"), "displayName", 256, required=True)
    external_id = service.text(raw.get("externalId"), "externalId", 512)
    members = service.validate_members(session, directory, raw.get("members", []))
    conflict = session.exec(select(SCIMGroup.id).where(SCIMGroup.workspace_id == directory.workspace_id,
        SCIMGroup.id != (existing.id if existing else ""), (SCIMGroup.display_name_normalized == name.casefold()) |
        ((SCIMGroup.external_id == external_id) if external_id else False))).first()
    if conflict:
        service.fail(409, "displayName or externalId already exists", "uniqueness")
    return name, external_id, members


def store_members(session, directory, group, members):
    old = set(session.exec(select(SCIMGroupMember.scim_user_id).where(SCIMGroupMember.group_id == group.id)).all())
    for resource_id in old - members:
        session.delete(session.get(SCIMGroupMember, (group.id, resource_id)))
    for resource_id in members - old:
        session.add(SCIMGroupMember(group_id=group.id, scim_user_id=resource_id))
    session.flush()
    changed = set()
    for resource_id in old | members:
        row = service.scoped_user(session, directory, resource_id, include_deleted=True)
        if resource_id in old ^ members:
            service.touch(row)
            session.add(row)
        changed.update(service.reconcile_user(session, directory, row))
    return changed


@router.post("/Groups", status_code=201)
async def create_group(request: Request, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    directory = lock_directory(session, authentication)
    raw = await body(request)
    name, external_id, members = group_data(session, directory, raw)
    count = session.exec(select(func.count()).select_from(SCIMGroup).where(SCIMGroup.workspace_id == directory.workspace_id)).one()
    if count >= 2000:
        service.fail(409, "Directory group limit reached", "tooMany")
    row = SCIMGroup(workspace_id=directory.workspace_id, display_name=name, display_name_normalized=name.casefold(), external_id=external_id)
    session.add(row)
    session.flush()
    changed = store_members(session, directory, row, members)
    service.audit(session, directory.workspace_id, "group_created", "Group", row.id)
    session.commit()
    service.sync_channels_after_commit(session, directory, changed)
    result = service.group_payload(session, directory, row)
    resource_headers(response, result)
    return result


def patch_group(session, directory, row, raw):
    old = session.exec(select(SCIMGroupMember.scim_user_id).where(SCIMGroupMember.group_id == row.id)).all()
    result = {"displayName": row.display_name, "externalId": row.external_id, "members": [{"value": value} for value in old]}
    operations = raw.get("Operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        service.fail(400, "Operations must contain between 1 and 100 operations", "invalidSyntax")
    for operation in operations:
        if not isinstance(operation, dict):
            service.fail(400, "Invalid patch operation", "invalidSyntax")
        action, path, value = str(operation.get("op", "")).lower(), operation.get("path"), operation.get("value")
        if action not in {"add", "replace", "remove"}:
            service.fail(400, "Invalid patch operation", "invalidSyntax")
        if path is None:
            if action == "remove" or not isinstance(value, dict):
                service.fail(400, "A value object is required", "invalidValue")
            result.update(value)
        elif isinstance(path, str) and path.lower() in {"displayname", "externalid"}:
            key = "displayName" if path.lower() == "displayname" else "externalId"
            if action == "remove": result.pop(key, None)
            else: result[key] = value
        elif isinstance(path, str) and path.lower() == "members":
            if action == "remove" and value is None:
                result["members"] = []
            elif action == "replace":
                service.validate_members(session, directory, value)
                result["members"] = value
            elif action in {"add", "remove"}:
                incoming = service.validate_members(session, directory, value)
                current = service.validate_members(session, directory, result["members"])
                current = current | incoming if action == "add" else current - incoming
                result["members"] = [{"value": identifier} for identifier in sorted(current)]
        elif isinstance(path, str) and (match := re.fullmatch(r'members\[value\s+eq\s+"([^"\\]{1,128})"\]', path, re.I)) and action == "remove":
            result["members"] = [item for item in result["members"] if item.get("value") != match[1]]
        else:
            service.fail(400, "Unsupported group patch path", "invalidPath")
    return group_data(session, directory, result, row)


async def _update_group(resource_id, request, response, authentication, session, replace):
    directory = lock_directory(session, authentication)
    row = service.scoped_group(session, directory, resource_id)
    service.check_version(row, request.headers.get("if-match"))
    raw = await body(request)
    if raw.get("id") not in {None, row.id}:
        service.fail(400, "id is read-only", "mutability")
    name, external_id, members = group_data(session, directory, raw, row) if replace else patch_group(session, directory, row, raw)
    row.display_name, row.display_name_normalized, row.external_id = name, name.casefold(), external_id
    service.touch(row)
    session.add(row)
    changed = store_members(session, directory, row, members)
    service.audit(session, directory.workspace_id, "group_updated", "Group", row.id)
    session.commit()
    service.sync_channels_after_commit(session, directory, changed)
    result = service.group_payload(session, directory, row)
    resource_headers(response, result)
    return result


@router.put("/Groups/{resource_id}")
async def replace_group(resource_id: str, request: Request, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    return await _update_group(resource_id, request, response, authentication, session, True)


@router.patch("/Groups/{resource_id}")
async def update_group(resource_id: str, request: Request, response: Response, authentication=Depends(authenticate), session=Depends(get_session)):
    return await _update_group(resource_id, request, response, authentication, session, False)


@router.delete("/Groups/{resource_id}", status_code=204, response_class=Response)
async def delete_group(resource_id: str, request: Request, authentication=Depends(authenticate), session=Depends(get_session)):
    directory = lock_directory(session, authentication)
    row = service.scoped_group(session, directory, resource_id)
    service.check_version(row, request.headers.get("if-match"))
    changed = store_members(session, directory, row, set())
    session.execute(delete(SCIMChannelMapping).where(SCIMChannelMapping.group_id == row.id))
    mapping = session.get(SCIMGroupMapping, row.id)
    if mapping: session.delete(mapping)
    session.delete(row)
    service.audit(session, directory.workspace_id, "group_deleted", "Group", resource_id)
    session.commit()
    service.sync_channels_after_commit(session, directory, changed)
    return Response(status_code=204)
