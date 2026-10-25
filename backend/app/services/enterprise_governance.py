"""Current-grant governance. No organization role grants channel plaintext."""
from datetime import datetime
import json
import re

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlmodel import select

from ..models.channel import Channel, ChannelMember
from ..models.enterprise_governance import (
    GovernanceAudit, GovernanceCase, NativeDLPPolicy, Organization,
    OrganizationMember, OrganizationWorkspace,
)
from ..models.file import File
from ..models.message import Message
from ..models.secure_channel import SecureChannel
from ..models.user import User
from ..models.workspace import Workspace, WorkspaceChannel, WorkspaceMember
from .access import lock_channel_writes
from .account_security import unseal_private
from .sso_scope import current_scope_actor_id, current_workspace_scope, require_unrestricted
from .workforce_access import is_managed

SURFACES = {"message", "file", "app", "assistant", "search", "document"}
DETECTORS = {"literal", "credit_card", "aws_access_key", "email"}


def audit(session, actor_id, action, target_id, *, workspace_id=None, organization_id=None):
    # Metadata only; never matched text, filenames, case titles or credentials.
    session.add(GovernanceAudit(actor_id=actor_id, action=action, target_id=target_id,
        workspace_id=workspace_id, organization_id=organization_id))


def require_organization(session, identifier, actor_id, *, owner=False):
    require_unrestricted(session)
    if is_managed(session, actor_id):
        raise HTTPException(403, "Organization management requires a personal account")
    actor = session.exec(select(User.is_blocked).where(User.id == actor_id)).first()
    row = session.exec(select(OrganizationMember.role).where(
        OrganizationMember.organization_id == identifier, OrganizationMember.user_id == actor_id)).first()
    if actor is None or actor or row not in ({"owner"} if owner else {"owner", "admin", "auditor"}):
        raise HTTPException(403, "Current organization role required")
    organization = session.get(Organization, identifier, populate_existing=True)
    if not organization:
        raise HTTPException(404, "Organization not found")
    return organization, row


def lock_organization(session, identifier):
    result = session.execute(update(Organization).where(Organization.id == identifier).values(name=Organization.name))
    if result.rowcount != 1:
        raise HTTPException(404, "Organization not found")


def organization_workspace_access_allowed(session, workspace_id, user_id):
    actor_id = current_scope_actor_id(session)
    if actor_id is not None and actor_id != user_id:
        # Membership/recipient validation does not authenticate that recipient
        # using the caller's sign-in. Their own request is checked separately.
        return True
    policy = session.exec(select(Organization.require_sso).join(OrganizationWorkspace,
        OrganizationWorkspace.organization_id == Organization.id).where(
        OrganizationWorkspace.workspace_id == workspace_id)).first()
    if not policy:
        return True
    role = session.exec(select(WorkspaceMember.role).where(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)).first()
    # The workspace's own owner retains its documented emergency account.
    return role == "owner" or current_workspace_scope(session) == workspace_id


def lock_governed_channels(session, workspace_id):
    """Caller locks workspace first; serialize holds/policies against content writes."""
    ids = session.exec(select(WorkspaceChannel.channel_id).where(
        WorkspaceChannel.workspace_id == workspace_id).order_by(WorkspaceChannel.channel_id).limit(2001)).all()
    if len(ids) > 2000:
        raise HTTPException(409, "Manage preservation in workspace groups of at most 2000 channels")
    for identifier in ids:
        lock_channel_writes(session, identifier)


def active_holds(session, workspace_id):
    return session.exec(select(GovernanceCase).where(GovernanceCase.workspace_id == workspace_id,
        GovernanceCase.status == "active").execution_options(populate_existing=True)).all()


def hold_applies(row, channel_id, author_id=None):
    channels, custodians = json.loads(row.channel_ids_json), json.loads(row.custodian_ids_json)
    return (not channels or channel_id in channels) and (not custodians or author_id is None or author_id in custodians)


def assert_deletion_allowed(session, channel_id, *, author_id=None):
    # Channel locks are also taken by case creation/release before committing.
    # Do not acquire a workspace lock here: writers already hold channel locks.
    lock_channel_writes(session, channel_id)
    workspace = session.exec(select(WorkspaceChannel.workspace_id).where(WorkspaceChannel.channel_id == channel_id)).first()
    if workspace and any(hold_applies(row, channel_id, author_id) for row in active_holds(session, workspace)):
        raise HTTPException(423, "An active legal preservation case prevents changing or deleting this content")


def assert_purge_allowed(session, workspace_id, channel_ids):
    # A custodian hold also prevents bulk deletion when the plan mixes authors.
    for identifier in sorted(channel_ids):
        assert_deletion_allowed(session, identifier)


def _luhn(value):
    digits = re.sub(r"[^0-9]", "", value)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number = number * 2 - (9 if number >= 5 else 0)
        total += number
    return total % 10 == 0


def _matches(row, text):
    if row.detector == "literal":
        literal = unseal_private(row.encrypted_literal, "native-dlp", row.id) if row.encrypted_literal else ""
        return bool(literal and literal.casefold() in text.casefold())
    if row.detector == "credit_card":
        return any(_luhn(match.group()) for match in re.finditer(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", text))
    if row.detector == "aws_access_key":
        return bool(re.search(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", text))
    if row.detector == "email":
        return bool(re.search(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}\b", text))
    # A corrupt or unsupported enabled policy must never silently allow content.
    raise HTTPException(503, "The native content policy could not be evaluated")


def _policies(session, channel_id, surface):
    if surface not in SURFACES:
        raise ValueError("Unsupported native content surface")
    if session.get(SecureChannel, channel_id):
        raise HTTPException(409, "Native DLP cannot inspect client-encrypted Matrix content")
    workspace = session.exec(select(WorkspaceChannel.workspace_id).where(WorkspaceChannel.channel_id == channel_id)).first()
    if not workspace:
        return []
    return [row for row in session.exec(select(NativeDLPPolicy).where(
        NativeDLPPolicy.workspace_id == workspace, NativeDLPPolicy.enabled == True)
        .execution_options(populate_existing=True)).all() if surface in json.loads(row.surfaces_json)]


def native_content_allowed(session, channel_id, text, *, surface="search"):
    if not isinstance(text, str) or len(text.encode()) > 2 * 1024 * 1024:
        raise HTTPException(413, "Native content policy scan is limited to 2 MiB")
    return not any(row.action == "block" and _matches(row, text) for row in _policies(session, channel_id, surface))


def enforce_native_content(session, channel_id, actor_id, text, *, surface="message"):
    lock_channel_writes(session, channel_id)
    if not isinstance(text, str) or len(text.encode()) > 2 * 1024 * 1024:
        raise HTTPException(413, "Native content policy scan is limited to 2 MiB")
    matches = [row for row in _policies(session, channel_id, surface) if _matches(row, text)]
    if any(row.action == "block" for row in matches):
        raise HTTPException(423, {"code": "native_dlp_blocked", "message": "Workspace content policy blocks this native operation", "policy_ids": [row.id for row in matches if row.action == "block"]})
    warnings = [{"code": "native_dlp_warning", "policy_id": row.id} for row in matches]
    for row in matches:
        audit(session, actor_id, "dlp.warn", row.id, workspace_id=row.workspace_id)
    session.info.setdefault("native_dlp_warnings", []).extend(warnings)
    return warnings


def _native_file_text(policies, content_type, content):
    # Bounded UTF-8 text is scanned. Binary/PDF/archive/OCR is explicitly outside
    # this engine; an enabled block_uninspectable_files policy fails closed.
    text = None
    if (content_type or "").split(";", 1)[0].lower() in {"text/plain", "text/csv", "text/markdown", "application/json", "application/xml", "text/xml"}:
        try:
            if len(content) <= 2 * 1024 * 1024:
                text = content.decode("utf-8", errors="strict")
        except UnicodeError:
            pass
    if text is None and any(row.action == "block" and row.block_uninspectable_files for row in policies):
        raise HTTPException(423, "Workspace policy blocks files whose contents cannot be inspected")
    return text or ""


def native_file_allowed(session, channel_id, filename, content_type, content):
    """Pure preflight before private blob I/O; no locks, audit or commit."""
    policies = _policies(session, channel_id, "file")
    if not policies:
        return True
    text = _native_file_text(policies, content_type, content)
    return native_content_allowed(session, channel_id, filename + "\n" + text, surface="file")


def enforce_native_file(session, channel_id, actor_id, filename, content_type, content):
    lock_channel_writes(session, channel_id)
    policies = _policies(session, channel_id, "file")
    if not policies:
        return []
    text = _native_file_text(policies, content_type, content)
    return enforce_native_content(session, channel_id, actor_id, filename + "\n" + text, surface="file")


def enforce_native_document(session, channel_id, actor_id, fields, *, opaque_update=False):
    lock_channel_writes(session, channel_id)
    policies = _policies(session, channel_id, "document")
    if opaque_update and any(row.action == "block" for row in policies):
        raise HTTPException(423, "A blocking document policy prevents uninspectable collaborative document updates")
    text = json.dumps({key: value for key, value in fields.items() if key not in {"update_base64", "snapshot_base64"}}, ensure_ascii=False)
    return enforce_native_content(session, channel_id, actor_id, text, surface="document")


def organization_usage(session, organization_id):
    ids = session.exec(select(OrganizationWorkspace.workspace_id).where(
        OrganizationWorkspace.organization_id == organization_id)).all()
    results = []
    for identifier in ids:
        channels = select(WorkspaceChannel.channel_id).where(WorkspaceChannel.workspace_id == identifier)
        native = select(WorkspaceChannel.channel_id).where(WorkspaceChannel.workspace_id == identifier,
            ~WorkspaceChannel.channel_id.in_(select(SecureChannel.channel_id)))
        results.append({"workspace_id": identifier,
            "members": session.exec(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == identifier)).one(),
            "channels": session.exec(select(func.count()).select_from(WorkspaceChannel).where(WorkspaceChannel.workspace_id == identifier)).one(),
            "secure_channels": session.exec(select(func.count()).select_from(SecureChannel).where(SecureChannel.channel_id.in_(channels))).one(),
            "native_messages": session.exec(select(func.count()).select_from(Message).where(Message.channel_id.in_(native), Message.deleted == False)).one(),
            "stored_file_bytes": session.exec(select(func.coalesce(func.sum(File.size), 0)).where(File.channel_id.in_(channels))).one(),
        })
    return results
