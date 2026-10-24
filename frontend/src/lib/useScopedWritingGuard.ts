import { useEffect, useLayoutEffect, useRef } from 'react';
import { api } from './api';
import { useCollaborationContext } from './collaboration-context';
import { registerConversationNavigationGuard } from './conversation-navigation';
import { t } from '../i18n';

export interface WritingLeaveController { current: (() => boolean) | null }
/** A writing card guards its own close, while conversation navigation checks every card. */
export function useScopedWritingGuard(channelId: string, dirty: boolean, busy: boolean, options: { workspaceId?: string; leaveController?: WritingLeaveController } = {}) {
  const context = useCollaborationContext(), token = api.getAccessToken(), actor = api.getCurrentUserId();
  const workspaceId = options.workspaceId || context?.workspaceId, surfaceId = context?.surfaceId;
  const live = useRef({ token, actor, dirty, busy }); live.current = { token, actor, dirty, busy };
  const mayLeave = useRef(() => {
    const value = live.current;
    if (!value.token || !value.actor || value.token !== api.getAccessToken() || value.actor !== api.getCurrentUserId()) return true;
    return !value.busy && (!value.dirty || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?')));
  }).current;
  useEffect(() => {
    if (!workspaceId || !channelId) return;
    return registerConversationNavigationGuard({ userId: actor, credential: token, workspaceId, surfaceId }, mayLeave);
  }, [workspaceId, channelId, surfaceId, actor, token, mayLeave]);
  useLayoutEffect(() => {
    const target = options.leaveController;
    if (!target) return;
    target.current = mayLeave;
    return () => { if (target.current === mayLeave) target.current = null; };
  }, [options.leaveController, mayLeave]);
  return mayLeave;
}
