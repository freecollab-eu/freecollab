import { api } from './api';

export interface ConversationNavigationOwner {
  userId: string | null;
  credential: string | null;
  workspaceId: string;
}

interface NavigationGuard {
  owner: ConversationNavigationOwner;
  canLeave: () => boolean;
}

let workspace: ConversationNavigationOwner | null = null;
const guards = new Map<ConversationNavigationOwner, NavigationGuard>();
const checking = new Set<NavigationGuard>();

function current(owner: ConversationNavigationOwner) {
  return !!owner.userId && !!owner.credential && !!owner.workspaceId
    && owner.userId === api.getCurrentUserId() && owner.credential === api.getAccessToken()
    && owner.userId === workspace?.userId && owner.credential === workspace.credential
    && owner.workspaceId === workspace.workspaceId;
}

/** AccountProvider publishes committed authority, never a prospective selection. */
export function setConversationNavigationWorkspace(workspaceId: string | null) {
  const userId = api.getCurrentUserId(), credential = api.getAccessToken();
  workspace = workspaceId && userId && credential ? { workspaceId, userId, credential } : null;
  for (const [key, guard] of guards) if (!current(guard.owner)) guards.delete(key);
}

/** Keep the owner object stable for this surface's registration lifetime. */
export function registerConversationNavigationGuard(owner: ConversationNavigationOwner, canLeave: () => boolean) {
  // Separate surfaces may have identical scopes. Object identity keeps their
  // guards independent; an old cleanup cannot remove a replacement registration.
  const guard: NavigationGuard = { owner: { ...owner }, canLeave };
  if (owner.userId && owner.credential && owner.workspaceId
    && owner.userId === api.getCurrentUserId() && owner.credential === api.getAccessToken()) guards.set(owner, guard);
  return () => { if (guards.get(owner) === guard) guards.delete(owner); };
}

/** Call before user navigation mutates any selection, editor key or focus. */
export function mayLeaveConversationWork(): boolean {
  for (const [key, guard] of guards) {
    if (!current(guard.owner)) { guards.delete(key); continue; }
    if (checking.has(guard)) return false;
    let allowed = false;
    checking.add(guard);
    try { allowed = guard.canLeave(); }
    catch { /* A failed guard must preserve the current authorized editor. */ }
    finally { checking.delete(guard); }
    // A sign-out, session rotation or authoritative workspace change during the
    // callback revokes this guard. It must never keep old authority alive.
    if (!current(guard.owner)) { guards.delete(key); continue; }
    if (!allowed) return false;
  }
  return true;
}
