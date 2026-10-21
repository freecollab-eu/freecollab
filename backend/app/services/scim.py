"""SCIM directory operations scoped to a single customer's managed namespace."""
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlmodel import select

from ..models.channel import Channel, ChannelMember
from ..models.scim import (SCIMAudit, SCIMChannelGrant, SCIMChannelMapping, SCIMDirectory,
                          SCIMGroup, SCIMGroupMapping, SCIMGroupMember, SCIMUser)
from ..models.secure_channel import SecureChannel
from ..models.session import ActiveSession
from ..models.user import User
from ..models.workforce import WorkforcePrincipal
from ..models.workforce import WorkspaceIdentityPolicy
from ..models.oidc import OIDCIdentity
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from ..models.workspace_oidc import WorkspaceOIDCSession, WorkspaceOIDCProvider
from .account_security import account_mail_base_url
from .workforce import ensure_principal

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
ENTERPRISE_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
RANK = {"guest": 0, "member": 1, "admin": 2}


def fail(status, detail, kind=None):
    body = {"schemas": [ERROR_SCHEMA], "status": str(status), "detail": detail}
    if kind:
        body["scimType"] = kind
    raise HTTPException(status, body)


def utc(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def endpoint(directory):
    return account_mail_base_url().rstrip("/") + "/api/scim/v2/" + directory.directory_id


def audit(session, workspace_id, action, resource_type, resource_id):
    session.add(SCIMAudit(workspace_id=workspace_id, action=action, resource_type=resource_type, resource_id=resource_id))


def touch(row):
    row.version += 1
    row.updated_at = datetime.utcnow()


def etag(row):
    return 'W/"' + str(row.version) + '"'


def check_version(row, value):
    if value is not None and value not in {"*", etag(row)}:
        fail(412, "This resource changed. Fetch its current version before retrying.", "mutability")


def text(value, name, maximum=512, required=False):
    if value is None and not required:
        return None
    if (not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value)
            or (required and not value.strip())):
        fail(400, "Invalid " + name, "invalidValue")
    return value.strip()


def active_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    fail(400, "active must be true or false", "invalidValue")


def profile(data, existing=None):
    if not isinstance(data, dict):
        fail(400, "A SCIM object is required", "invalidSyntax")
    if data.get("roles") or data.get("entitlements") or data.get("password"):
        fail(400, "Passwords and privilege attributes are not managed through SCIM users", "mutability")
    result = dict(existing or {})
    for key in ("userName", "externalId", "displayName", "nickName", "title", "userType", "preferredLanguage", "locale", "timezone"):
        if key in data:
            result[key] = text(data[key], key, 320 if key == "userName" else 512, required=key == "userName")
    if "active" in data:
        result["active"] = active_value(data["active"])
    if "name" in data:
        if not isinstance(data["name"], dict):
            fail(400, "Invalid name", "invalidValue")
        result["name"] = {key: text(value, "name", 160) for key, value in data["name"].items()
                          if key in {"formatted", "familyName", "givenName", "middleName", "honorificPrefix", "honorificSuffix"}}
    if "emails" in data:
        emails = data["emails"]
        if not isinstance(emails, list) or len(emails) > 10:
            fail(400, "Invalid emails", "invalidValue")
        result["emails"] = []
        for item in emails:
            if not isinstance(item, dict):
                fail(400, "Invalid email", "invalidValue")
            result["emails"].append({"value": text(item.get("value"), "email", 320, required=True),
                                     "type": text(item.get("type", "work"), "email type", 40),
                                     "primary": item.get("primary") is True})
    if ENTERPRISE_SCHEMA in data:
        source = data[ENTERPRISE_SCHEMA]
        if not isinstance(source, dict):
            fail(400, "Invalid enterprise attributes", "invalidValue")
        enterprise = {key: text(value, key, 512) for key, value in source.items()
                      if key in {"employeeNumber", "costCenter", "organization", "division", "department"}}
        if "manager" in source:
            manager = source["manager"]
            if not isinstance(manager, dict):
                fail(400, "Invalid manager", "invalidValue")
            enterprise["manager"] = {"value": text(manager.get("value"), "manager", 512)}
        result[ENTERPRISE_SCHEMA] = enterprise
    result.setdefault("active", True)
    if not result.get("userName"):
        fail(400, "userName is required", "invalidValue")
    result["externalId"] = result.get("externalId") or result["userName"]
    return result


def display_name(data):
    name = data.get("name") or {}
    return (data.get("displayName") or name.get("formatted") or " ".join(
        value for value in (name.get("givenName"), name.get("familyName")) if value) or data["userName"])[:80]


def email_address(data):
    emails = data.get("emails", [])
    email = next((item["value"] for item in emails if item.get("primary")), None)
    return email or (emails[0]["value"] if emails else data["userName"] if "@" in data["userName"] else "")


def reject_personal_binding(session, directory, external_id, managed_user_id=None):
    policy = session.get(WorkspaceIdentityPolicy, directory.workspace_id)
    if policy and policy.identity_claim != "sub":
        return
    provider = session.get(WorkspaceOIDCProvider, directory.workspace_id)
    if provider is None:
        return
    identity_id = hashlib.sha256(("workspace\0" + directory.workspace_id + "\0" + provider.issuer_url + "\0" +
                                 provider.client_id + "\0" + external_id).encode()).hexdigest()
    identity = session.get(OIDCIdentity, identity_id)
    if identity and identity.user_id != managed_user_id and session.get(WorkforcePrincipal, identity.user_id) is None:
        fail(409, "This identity is already linked to a personal account. Remove that workspace sign-in link before provisioning its separate managed account.", "uniqueness")


def user_payload(session, directory, row):
    data = json.loads(row.profile_json)
    data.update({"schemas": [USER_SCHEMA] + ([ENTERPRISE_SCHEMA] if ENTERPRISE_SCHEMA in data else []),
                 "id": row.id, "externalId": row.external_id, "userName": row.user_name, "active": row.active,
                 "meta": {"resourceType": "User", "created": utc(row.created_at), "lastModified": utc(row.updated_at),
                          "version": etag(row), "location": endpoint(directory) + "/Users/" + row.id}})
    groups = session.exec(select(SCIMGroup).join(SCIMGroupMember, SCIMGroupMember.group_id == SCIMGroup.id).where(
        SCIMGroupMember.scim_user_id == row.id, SCIMGroup.workspace_id == directory.workspace_id)).all()
    data["groups"] = [{"value": g.id, "display": g.display_name, "type": "direct",
                       "$ref": endpoint(directory) + "/Groups/" + g.id} for g in groups]
    return data


def group_payload(session, directory, row):
    members = session.exec(select(SCIMUser).join(SCIMGroupMember, SCIMGroupMember.scim_user_id == SCIMUser.id).where(
        SCIMGroupMember.group_id == row.id, SCIMUser.deleted == False, SCIMUser.workspace_id == directory.workspace_id)).all()
    return {"schemas": [GROUP_SCHEMA], "id": row.id, "externalId": row.external_id, "displayName": row.display_name,
            "members": [{"value": m.id, "display": m.user_name, "$ref": endpoint(directory) + "/Users/" + m.id} for m in members],
            "meta": {"resourceType": "Group", "created": utc(row.created_at), "lastModified": utc(row.updated_at),
                     "version": etag(row), "location": endpoint(directory) + "/Groups/" + row.id}}


def scoped_user(session, directory, resource_id, include_deleted=False):
    row = session.get(SCIMUser, resource_id)
    if not row or row.workspace_id != directory.workspace_id or (row.deleted and not include_deleted):
        fail(404, "User not found")
    return row


def scoped_group(session, directory, resource_id):
    row = session.get(SCIMGroup, resource_id)
    if not row or row.workspace_id != directory.workspace_id:
        fail(404, "Group not found")
    return row


def filter_statement(statement, model, expression):
    if expression is None:
        return statement
    match = re.fullmatch(r'\s*(userName|externalId|id|displayName|active)\s+eq\s+("(?:[^"\\]|\\.)*"|true|false)\s*', expression, re.I)
    if not match:
        fail(400, "Only an equality filter on a supported scalar attribute is supported", "invalidFilter")
    attribute, literal = match.groups()
    try:
        value = json.loads(literal)
    except ValueError:
        fail(400, "Invalid equality filter literal", "invalidFilter")
    fields = {"id": model.id, "externalid": model.external_id}
    if model is SCIMUser:
        fields.update({"username": model.user_name_normalized, "active": model.active})
    else:
        fields["displayname"] = model.display_name_normalized
    key = attribute.lower()
    if key not in fields or (key == "active" and type(value) is not bool) or (key != "active" and not isinstance(value, str)):
        fail(400, "Invalid equality filter", "invalidFilter")
    if key in {"username", "displayname"}:
        value = value.casefold()
    return statement.where(fields[key] == value)


def validate_members(session, directory, members):
    if not isinstance(members, list) or len(members) > 2000:
        fail(400, "members must contain at most 2000 user references", "invalidValue")
    result = set()
    for item in members:
        if not isinstance(item, dict):
            fail(400, "Invalid group member", "invalidValue")
        resource_id = text(item.get("value"), "member value", 128, required=True)
        row = scoped_user(session, directory, resource_id)
        result.add(row.id)
    return result


def remove_channel_grant(session, grant, member):
    from .access import lock_channel_writes
    from .matrix_bridge import remove_secure_member
    lock_channel_writes(session, grant.channel_id)
    if member is not None:
        if grant.was_present:
            if member.role == grant.applied_role:
                member.role = grant.previous_role or "member"
                session.add(member)
        elif member.role == grant.applied_role:
            remove_secure_member(session, grant.channel_id, grant.user_id)
            if member.role == "admin":
                remaining = session.exec(select(ChannelMember).where(ChannelMember.channel_id == grant.channel_id,
                    ChannelMember.user_id != grant.user_id).order_by(ChannelMember.joined_at, ChannelMember.user_id)).all()
                from .connect import connection_for_channel, shared_channel_allows
                if connection_for_channel(session, grant.channel_id):
                    remaining = [item for item in remaining if shared_channel_allows(
                        session, grant.channel_id, item.user_id, grant.workspace_id)]
                if remaining and not any(item.role == "admin" for item in remaining):
                    remaining[0].role = "admin"
                    session.add(remaining[0])
            session.delete(member)
    session.delete(grant)


def channel_approval_active(session, workspace_id, mapping):
    # Select scalars rather than ORM objects cached before an upstream wait.
    approval = session.exec(select(User.is_blocked, WorkspaceMember.role, ChannelMember.role)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .join(ChannelMember, ChannelMember.user_id == User.id)
        .where(User.id == mapping.granted_by, WorkspaceMember.workspace_id == workspace_id,
               ChannelMember.channel_id == mapping.channel_id)).first()
    return bool(approval and not approval[0] and approval[1] in {"owner", "admin"} and approval[2] == "admin")


def reconcile_user(session, directory, row):
    """Apply only tracked SCIM grants. Unrelated/manual memberships stay intact."""
    if not row.active or row.deleted:
        return set()
    principal = session.get(WorkforcePrincipal, row.user_id)
    member = session.get(WorkspaceMember, (directory.workspace_id, row.user_id))
    if not principal or not principal.active or principal.workspace_id != directory.workspace_id or not member:
        return set()
    group_ids = session.exec(select(SCIMGroupMember.group_id).where(SCIMGroupMember.scim_user_id == row.id)).all()
    mappings = session.exec(select(SCIMGroupMapping).where(SCIMGroupMapping.group_id.in_(group_ids))).all() if group_ids else []
    roles = [mapping.workspace_role for mapping in mappings if mapping.workspace_role]
    # Changes made through a native workspace administrator remain explicit grants.
    if member.role in RANK and member.role != row.applied_role:
        row.base_role = member.role
    desired_role = max([row.base_role, *roles], key=lambda value: RANK[value])
    if member.role != "owner":
        member.role = desired_role
        row.applied_role = desired_role
        session.add(member)
        session.add(row)
    channel_mappings = session.exec(select(SCIMChannelMapping).where(SCIMChannelMapping.group_id.in_(group_ids))).all() if group_ids else []
    desired = {}
    for mapping in channel_mappings:
        scope = session.get(WorkspaceChannel, mapping.channel_id)
        channel = session.get(Channel, mapping.channel_id)
        if (not scope or scope.workspace_id != directory.workspace_id or not channel or channel.type == "direct"
                or not channel_approval_active(session, directory.workspace_id, mapping)):
            continue
        desired[mapping.channel_id] = "admin" if mapping.role == "admin" or desired.get(mapping.channel_id) == "admin" else "member"
    grants = {grant.channel_id: grant for grant in session.exec(select(SCIMChannelGrant).where(
        SCIMChannelGrant.workspace_id == directory.workspace_id, SCIMChannelGrant.user_id == row.user_id)).all()}
    changed = set()
    for channel_id in sorted(set(grants) | set(desired)):
        grant, membership = grants.get(channel_id), session.get(ChannelMember, (channel_id, row.user_id))
        if channel_id not in desired:
            remove_channel_grant(session, grant, membership)
            changed.add(channel_id)
            continue
        from .access import lock_channel_writes
        lock_channel_writes(session, channel_id)
        role = desired[channel_id]
        if grant is None:
            grant = SCIMChannelGrant(user_id=row.user_id, channel_id=channel_id, workspace_id=directory.workspace_id,
                                     was_present=membership is not None, previous_role=membership.role if membership else None)
        elif membership and membership.role != grant.applied_role:
            grant.was_present, grant.previous_role = True, membership.role
        if grant.previous_role == "admin":
            role = "admin"
        if membership is None:
            membership = ChannelMember(channel_id=channel_id, user_id=row.user_id, role=role)
        else:
            membership.role = role
        grant.applied_role = role
        session.add(grant)
        session.add(membership)
        changed.add(channel_id)
    session.flush()
    return changed


async def deactivate(session, directory, row):
    """Revoke remote key recipients before native removal and durable disable."""
    principal = session.get(WorkforcePrincipal, row.user_id)
    if not principal or principal.workspace_id != directory.workspace_id:
        fail(409, "Managed identity is unavailable", "mutability")
    row.active, principal.active = False, False
    session.add(row)
    session.add(principal)
    hashes = session.exec(select(WorkspaceOIDCSession.token_hash).where(
        WorkspaceOIDCSession.workspace_id == directory.workspace_id)).all()
    if hashes:
        session.execute(update(ActiveSession).where(ActiveSession.user_id == row.user_id, ActiveSession.token_hash.in_(hashes))
                        .values(revoked_at=datetime.utcnow()))
    # Mark tracking state before the existing removal helper's atomic commit.
    session.execute(delete(SCIMChannelGrant).where(SCIMChannelGrant.user_id == row.user_id,
                                                  SCIMChannelGrant.workspace_id == directory.workspace_id))
    session.flush()
    if session.get(WorkspaceMember, (directory.workspace_id, row.user_id)):
        from ..api.workspaces import remove_workspace_membership
        await remove_workspace_membership(session, directory.workspace_id, row.user_id, directory.token_issued_by,
                                          scim_directory_id=directory.directory_id)
    else:
        # Clean up historical orphan grants as well: a later reprovision must
        # not reactivate access to another organization's retained room.
        from .connect import revoke_workspace_grants, workspace_grant_channel_ids
        from .matrix_bridge import remove_secure_member
        channel_ids = workspace_grant_channel_ids(session, directory.workspace_id, row.user_id)
        for channel_id in channel_ids:
            session.execute(update(Channel).where(Channel.id == channel_id).values(name=Channel.name))
            remove_secure_member(session, channel_id, row.user_id)
        revoke_workspace_grants(session, directory.workspace_id, row.user_id)


def activate(session, directory, row, data):
    principal = session.get(WorkforcePrincipal, row.user_id)
    if not principal or principal.workspace_id != directory.workspace_id:
        fail(409, "Managed identity is unavailable", "mutability")
    ensure_principal(session, directory.workspace_id, row.external_id, display_name(data), email_address(data),
                     active=True, role=row.base_role)
    row.active = True
    session.add(row)


def update_profile(session, directory, row, data):
    reject_personal_binding(session, directory, data["externalId"], row.user_id)
    conflict = session.exec(select(SCIMUser.id).where(SCIMUser.workspace_id == directory.workspace_id,
        SCIMUser.id != row.id, ((SCIMUser.user_name_normalized == data["userName"].casefold()) |
                               (SCIMUser.external_id == data["externalId"])))).first()
    if conflict:
        fail(409, "userName or externalId already exists", "uniqueness")
    principal = session.get(WorkforcePrincipal, row.user_id)
    if not principal or principal.workspace_id != directory.workspace_id:
        fail(409, "Managed identity is unavailable", "mutability")
    other_principal = session.exec(select(WorkforcePrincipal.user_id).where(
        WorkforcePrincipal.workspace_id == directory.workspace_id, WorkforcePrincipal.external_id == data["externalId"],
        WorkforcePrincipal.user_id != row.user_id)).first()
    if other_principal:
        fail(409, "externalId already exists", "uniqueness")
    principal.external_id, principal.managed_email = data["externalId"], email_address(data)
    user = session.get(User, row.user_id)
    user.name = display_name(data)
    row.user_name, row.user_name_normalized, row.external_id = data["userName"], data["userName"].casefold(), data["externalId"]
    row.profile_json = json.dumps({key: value for key, value in data.items() if key not in {"userName", "externalId", "active"}}, separators=(",", ":"))
    session.add_all([row, principal, user])


def sync_channels_after_commit(session, directory, channel_ids):
    """New remote invitations can retry; removal always happened before commit."""
    from .matrix_bridge import sync_secure_membership
    for channel_id in sorted(channel_ids):
        if session.get(SecureChannel, channel_id) is None:
            continue
        try:
            sync_secure_membership(session, channel_id)
            session.commit()
        except HTTPException:
            session.rollback()
            audit(session, directory.workspace_id, "encrypted_invitation_pending", "Channel", channel_id)
            session.commit()
