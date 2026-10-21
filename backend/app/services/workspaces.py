"""Workspace authorization and additive migration for the original single-team pilot."""
from fastapi import HTTPException
from sqlalchemy import and_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..models.channel import Channel, ChannelMember
from ..models.direct_message import DirectMessage, UserBlock
from ..models.user import User
from ..models.secure_channel import SecureChannel
from ..models.workspace import PersonalWorkspace, Workspace, WorkspaceChannel, WorkspaceMember

from .sso_scope import current_workspace_scope, require_workspace_scope
from .workforce_access import principal_snapshot, require_principal_workspace

LEGACY_WORKSPACE_ID = "legacy"
WORKSPACE_ROLES = {"owner", "admin", "member", "guest"}


def lock_workspace(session, workspace_id: str):
    require_workspace_scope(session, workspace_id)
    # SELECT FOR UPDATE does not serialize SQLite writers. A no-op write does.
    result = session.execute(update(Workspace).where(Workspace.id == workspace_id).values(name=Workspace.name))
    if result.rowcount != 1:
        raise HTTPException(404, "Workspace not found")
    require_workspace_scope(session, workspace_id)


def require_workspace_member(session, workspace_id: str, user_id: str):
    require_workspace_scope(session, workspace_id)
    require_principal_workspace(session, user_id, workspace_id)
    if not session.get(Workspace, workspace_id):
        raise HTTPException(404, "Workspace not found")
    member = session.get(WorkspaceMember, (workspace_id, user_id))
    if not member or member.role not in WORKSPACE_ROLES:
        raise HTTPException(403, "Workspace membership required")
    from .enterprise_governance import organization_workspace_access_allowed
    if not organization_workspace_access_allowed(session, workspace_id, user_id):
        raise HTTPException(403, "This organization requires its workspace sign-in")
    return member


def require_workspace_admin(session, workspace_id: str, user_id: str):
    member = require_workspace_member(session, workspace_id, user_id)
    if member.role not in {"owner", "admin"}:
        raise HTTPException(403, "Workspace administrator access required")
    return member


def workspace_allows_channel(session, channel_id: str, user_id: str) -> bool:
    """Unmapped legacy records retain their existing channel-only policy."""
    from .connect import connection_for_channel, shared_channel_allows, shared_channel_home
    from .enterprise_governance import organization_workspace_access_allowed
    from .sso_scope import current_scope_actor_id
    connection = connection_for_channel(session, channel_id)
    if connection:
        scope = current_workspace_scope(session)
        actor = current_scope_actor_id(session) or user_id
        actor_home = shared_channel_home(session, channel_id, actor)
        if not actor_home or not organization_workspace_access_allowed(session, actor_home, actor):
            return False
        if scope is not None:
            if not actor or not shared_channel_allows(session, channel_id, actor, scope):
                return False
        return shared_channel_allows(session, channel_id, user_id)
    mapping = session.get(WorkspaceChannel, channel_id)
    principal = principal_snapshot(session, user_id)
    if principal is not None and (not principal[1] or mapping is None or mapping.workspace_id != principal[0]):
        return False
    scope = current_workspace_scope(session)
    if scope is not None and (mapping is None or mapping.workspace_id != scope):
        return False
    if mapping is None:
        return True
    if not organization_workspace_access_allowed(session, mapping.workspace_id,
                                                  current_scope_actor_id(session) or user_id):
        return False
    member = session.get(WorkspaceMember, (mapping.workspace_id, user_id))
    return bool(session.get(Workspace, mapping.workspace_id) and member and member.role in WORKSPACE_ROLES)


def channel_workspace_id(session, channel_id: str) -> str | None:
    mapping = session.get(WorkspaceChannel, channel_id)
    return mapping.workspace_id if mapping else None


def channel_security_metadata(session, channel_id: str):
    binding = session.get(SecureChannel, channel_id)
    return {"secure": binding is not None, "matrix_room_id": binding.matrix_room_id if binding else None}


def ensure_legacy_workspace(session):
    workspace = session.get(Workspace, LEGACY_WORKSPACE_ID)
    if workspace:
        return workspace
    try:
        with session.begin_nested():
            workspace = Workspace(id=LEGACY_WORKSPACE_ID, name="Original workspace", is_legacy=True)
            session.add(workspace)
            session.flush()
    except IntegrityError:
        workspace = session.get(Workspace, LEGACY_WORKSPACE_ID)
    return workspace


def ensure_legacy_member(session, user_id: str):
    require_principal_workspace(session, user_id, LEGACY_WORKSPACE_ID)
    member = session.get(WorkspaceMember, (LEGACY_WORKSPACE_ID, user_id))
    if member:
        return member
    user = session.get(User, user_id)
    if not user or user.is_blocked:
        raise HTTPException(400, "Channel members must be active users")
    try:
        with session.begin_nested():
            member = WorkspaceMember(workspace_id=LEGACY_WORKSPACE_ID, user_id=user_id,
                                     role="admin" if user.role == "admin" else "member")
            session.add(member)
            session.flush()
    except IntegrityError:
        member = session.get(WorkspaceMember, (LEGACY_WORKSPACE_ID, user_id))
    return member


def ensure_personal_workspace_membership(session, user_id: str):
    """Give a fresh account a private home without exposing the pilot directory."""
    scope = current_workspace_scope(session)
    if scope is not None:
        return require_workspace_member(session, scope, user_id)
    principal = principal_snapshot(session, user_id)
    if principal is not None:
        if not principal[1]:
            raise HTTPException(403, "Organization-managed account is inactive")
        return require_workspace_member(session, principal[0], user_id)
    # Account-provider requests can arrive simultaneously on initial sign-in.
    # Serialize on this account so only one default personal workspace is made.
    result = session.execute(update(User).where(User.id == user_id).values(name=User.name))
    if result.rowcount != 1:
        raise HTTPException(404, "Account not found")
    existing = session.exec(select(WorkspaceMember).join(
        Workspace, Workspace.id == WorkspaceMember.workspace_id,
    ).where(WorkspaceMember.user_id == user_id, WorkspaceMember.role.in_(WORKSPACE_ROLES))).first()
    if existing:
        return existing
    actor = session.get(User, user_id)
    workspace = Workspace(name=f"{actor.name[:60]}'s workspace")
    session.add(workspace)
    session.flush()
    member = WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role="owner")
    session.add(member)
    personal = session.get(PersonalWorkspace, user_id)
    if personal:
        personal.workspace_id = workspace.id
        session.add(personal)
    else:
        session.add(PersonalWorkspace(user_id=user_id, workspace_id=workspace.id))
    session.flush()
    return member


def prepare_channel_workspace(session, user_id: str, workspace_id: str | None, member_ids: set[str]):
    """Scope all new channels, retaining the old API without directory enrollment."""
    scope = current_workspace_scope(session)
    if scope is not None:
        workspace_id = workspace_id or scope
    if workspace_id:
        lock_workspace(session, workspace_id)
        actor = require_workspace_member(session, workspace_id, user_id)
        if actor.role == "guest":
            raise HTTPException(403, "Guests cannot create channels")
        personal = session.exec(select(PersonalWorkspace).where(PersonalWorkspace.workspace_id == workspace_id)).first()
        for member_id in member_ids:
            if personal and member_id != user_id and users_blocked(session, user_id, member_id):
                raise HTTPException(403, "Personal channel invitations are unavailable for this person")
            require_workspace_member(session, workspace_id, member_id)
        return workspace_id
    # Existing pilot members retain their original single-team API. Omitting a
    # workspace can never enroll a new caller in that pilot directory.
    legacy = session.get(WorkspaceMember, (LEGACY_WORKSPACE_ID, user_id))
    if legacy and session.get(Workspace, LEGACY_WORKSPACE_ID):
        lock_workspace(session, LEGACY_WORKSPACE_ID)
        session.expire(legacy)
        require_workspace_member(session, LEGACY_WORKSPACE_ID, user_id)
        for member_id in member_ids:
            ensure_legacy_member(session, member_id)
        return LEGACY_WORKSPACE_ID
    personal = session.get(PersonalWorkspace, user_id)
    if not personal:
        # Only a fresh account gets a new private home through the compatibility
        # path. A custom-workspace guest must choose/create a workspace explicitly.
        ensure_personal_workspace_membership(session, user_id)
        personal = session.get(PersonalWorkspace, user_id)
    if not personal:
        raise HTTPException(403, "Choose an authorized workspace before creating a channel")
    lock_workspace(session, personal.workspace_id)
    actor = require_workspace_member(session, personal.workspace_id, user_id)
    if actor.role != "owner":
        raise HTTPException(403, "Only the private workspace owner can use implicit channel invitations")
    for member_id in member_ids:
        if member_id != user_id and users_blocked(session, user_id, member_id):
            raise HTTPException(403, "Personal channel invitations are unavailable for this person")
        person = session.get(User, member_id)
        if not person or person.is_blocked:
            raise HTTPException(400, "All channel members must be active users")
        if not session.get(WorkspaceMember, (personal.workspace_id, member_id)):
            session.add(WorkspaceMember(workspace_id=personal.workspace_id, user_id=member_id, role="guest"))
    session.flush()
    return personal.workspace_id


def visible_workspace_user_ids(session, workspace_id: str, user_id: str) -> set[str]:
    actor = require_workspace_member(session, workspace_id, user_id)
    if actor.role != "guest":
        return set(session.exec(select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == workspace_id)).all())
    own_channels = session.exec(select(ChannelMember.channel_id).join(
        WorkspaceChannel, WorkspaceChannel.channel_id == ChannelMember.channel_id,
    ).join(Channel, Channel.id == ChannelMember.channel_id).where(WorkspaceChannel.workspace_id == workspace_id, ChannelMember.user_id == user_id)).all()
    visible = set(session.exec(select(ChannelMember.user_id).join(
        WorkspaceMember, and_(WorkspaceMember.user_id == ChannelMember.user_id,
                              WorkspaceMember.workspace_id == workspace_id),
    ).where(ChannelMember.channel_id.in_(own_channels), WorkspaceMember.role.in_(WORKSPACE_ROLES))).all()) if own_channels else set()
    return visible | {user_id}


def visible_user_ids(session, user_id: str) -> set[str]:
    """People visible in this account's authorized directories and old channels.

    Visibility is directional: a workspace member may see its guest while the
    guest only sees assigned channel peers. Personal blocks hide presence both ways.
    """
    actor = session.get(User, user_id)
    if not actor or actor.is_blocked:
        return set()
    visible = {user_id}
    memberships = session.exec(select(WorkspaceMember).join(
        Workspace, Workspace.id == WorkspaceMember.workspace_id,
    ).where(WorkspaceMember.user_id == user_id, WorkspaceMember.role.in_(WORKSPACE_ROLES))).all()
    scope = current_workspace_scope(session)
    for membership in memberships:
        if scope is not None and membership.workspace_id != scope:
            continue
        visible.update(visible_workspace_user_ids(session, membership.workspace_id, user_id))
    # Existing fixtures and not-yet-migrated channels retain their original
    # shared-channel scope without turning unrelated accounts into a directory.
    legacy_channels = session.exec(select(ChannelMember.channel_id).join(
        Channel, Channel.id == ChannelMember.channel_id,
    ).outerjoin(WorkspaceChannel, WorkspaceChannel.channel_id == Channel.id).where(
        ChannelMember.user_id == user_id, WorkspaceChannel.channel_id == None,
    )).all()
    if legacy_channels and scope is None:
        visible.update(session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id.in_(legacy_channels))).all())
    # Shared-channel peers are visible as peers, never as a foreign directory.
    from .connect import shared_channel_allows
    from ..models.connect import ConnectConnection
    shared_channels = session.exec(select(ChannelMember.channel_id).join(
        ConnectConnection, ConnectConnection.channel_id == ChannelMember.channel_id,
    ).where(ChannelMember.user_id == user_id)).all()
    for channel_id in shared_channels:
        if not shared_channel_allows(session, channel_id, user_id, scope):
            continue
        peers = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel_id)).all()
        visible.update(peer for peer in peers if shared_channel_allows(session, channel_id, peer))
    blocks = session.exec(select(UserBlock).where((UserBlock.blocker_id == user_id) | (UserBlock.blocked_id == user_id))).all()
    hidden = {row.blocked_id if row.blocker_id == user_id else row.blocker_id for row in blocks}
    return set(session.exec(select(User.id).where(User.id.in_(visible - hidden), User.is_blocked == False)).all())


def users_blocked(session, first: str, second: str) -> bool:
    return bool(session.get(UserBlock, (first, second)) or session.get(UserBlock, (second, first)))


def require_direct_message_writable(session, channel_id: str, user_id: str):
    """Call on send paths; reading retained history remains possible after a block."""
    channel = session.get(Channel, channel_id)
    if not channel or channel.type != "direct":
        return
    members = session.exec(select(ChannelMember.user_id).where(ChannelMember.channel_id == channel_id)).all()
    if len(members) != 2 or user_id not in members:
        raise HTTPException(409, "This direct conversation requires migration")
    other = next(member for member in members if member != user_id)
    other_user = session.get(User, other)
    if (not other_user or other_user.is_blocked or not workspace_allows_channel(session, channel_id, other)
            or users_blocked(session, user_id, other)):
        raise HTTPException(403, "Messaging is unavailable for this conversation")


def init_workspaces(engine):
    """Idempotent additive backfill; no table/column alterations or content access grants."""
    with Session(engine) as session:
        unmapped = session.exec(select(Channel).outerjoin(
            WorkspaceChannel, WorkspaceChannel.channel_id == Channel.id,
        ).where(WorkspaceChannel.channel_id == None).order_by(Channel.created_at, Channel.id)).all()
        if not unmapped:
            return
        ensure_legacy_workspace(session)
        lock_workspace(session, LEGACY_WORKSPACE_ID)
        # A concurrent startup may have completed the backfill while this
        # process waited for the legacy workspace lock.
        unmapped = session.exec(select(Channel).outerjoin(
            WorkspaceChannel, WorkspaceChannel.channel_id == Channel.id,
        ).where(WorkspaceChannel.channel_id == None).order_by(Channel.created_at, Channel.id)).all()
        for channel in unmapped:
            session.add(WorkspaceChannel(channel_id=channel.id, workspace_id=LEGACY_WORKSPACE_ID))
            members = session.exec(select(ChannelMember).where(ChannelMember.channel_id == channel.id)).all()
            valid_ids = []
            for membership in members:
                user = session.get(User, membership.user_id)
                if user and not user.is_blocked:
                    ensure_legacy_member(session, user.id)
                    valid_ids.append(user.id)
            if channel.type == "direct" and len(members) == 2 and len(valid_ids) == 2:
                low, high = sorted(valid_ids)
                existing = session.exec(select(DirectMessage).where(
                    DirectMessage.workspace_id == LEGACY_WORKSPACE_ID,
                    DirectMessage.user_low == low, DirectMessage.user_high == high,
                )).first()
                if not existing:
                    session.add(DirectMessage(channel_id=channel.id, workspace_id=LEGACY_WORKSPACE_ID,
                                              user_low=low, user_high=high))
        session.commit()
