import type { Channel, User } from './api';
import type { Workspace, WorkspaceInvite } from './workspace-api';
import type { ConnectionSetup } from './connection-setup-api';

export type WorkspaceLaunchGuard = 'busy' | 'secret' | 'dirty' | null;
export function canLaunchWorkspace(user: Pick<User, 'id' | 'account_type' | 'session_scope'> | null, workspace: Workspace | null | undefined): boolean {
  return !!user && user.account_type !== 'managed' && user.session_scope !== 'workspace' && !!workspace && ['owner', 'admin'].includes(workspace.role);
}
export function nativeLaunchChannels(workspaceId: string, channels: Channel[]): Channel[] {
  return channels.filter(channel => channel.workspace_id === workspaceId && ['group', 'public'].includes(channel.type) && !channel.secure && !channel.matrix_room_id);
}
/** A conservative suggestion for a newly created workspace, never an auto-open instruction. */
export function shouldSuggestWorkspaceLaunch(workspace: Workspace, channels: Channel[]): boolean {
  return !workspace.is_legacy && workspace.member_count === 1 && !channels.some(channel => channel.workspace_id === workspace.id && channel.type !== 'direct');
}
export function activeLaunchInvites(workspaceId: string, invites: WorkspaceInvite[], now = Date.now()): WorkspaceInvite[] {
  return invites.filter(invite => invite.workspace_id === workspaceId && !invite.accepted_at && !invite.revoked_at && Date.parse(invite.expires_at) > now);
}
export function launchGuard(busy: boolean, dirty: boolean, invitationPending: boolean): WorkspaceLaunchGuard {
  return busy ? 'busy' : invitationPending ? 'secret' : dirty ? 'dirty' : null;
}
export function prepareLaunchInvite(invite: WorkspaceInvite, workspaceId: string, role: 'member' | 'guest', origin: string, pathname: string) {
  if (invite.workspace_id !== workspaceId || invite.role !== role || !invite.id || invite.id.length > 256 ||
      !invite.token || !/^[a-zA-Z0-9_-]{20,512}$/.test(invite.token) ||
      invite.accepted_at || invite.revoked_at || !(Date.parse(invite.expires_at) > Date.now())) {
    throw new Error('The invitation response could not be verified. Refresh invitations before trying again.');
  }
  const url = new URL(origin);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !pathname.startsWith('/')) {
    throw new Error('The invitation address could not be prepared.');
  }
  url.pathname = pathname; url.search = ''; url.hash = 'workspace-invite=' + encodeURIComponent(invite.token);
  const metadata: WorkspaceInvite = { id: invite.id, workspace_id: workspaceId, role, expires_at: invite.expires_at, revoked_at: null, accepted_at: null };
  return { metadata, url: url.href };
}
export function summarizeLaunchConnections(snapshot: ConnectionSetup | null, workspaceId: string, channelId: string | null) {
  if (!snapshot) return null;
  if (snapshot.workspace_id !== workspaceId || snapshot.channel_id !== channelId) throw new Error('Connection status does not match this workspace and channel.');
  const enabled = snapshot.recipes.filter(recipe => recipe.enabled);
  return {
    ready: enabled.filter(recipe => recipe.status === 'ready').length,
    drafts: enabled.filter(recipe => ['draft', 'authorization_required', 'verification_required'].includes(recipe.status)).length,
    attention: enabled.filter(recipe => recipe.status === 'attention').length,
  };
}
