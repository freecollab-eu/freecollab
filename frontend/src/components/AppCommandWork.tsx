import { useCallback, useEffect, useRef } from 'react';
import { api } from '../lib/api';
import { t } from '../i18n';
import { useCollaborationContext } from '../lib/collaboration-context';
import { mayLeaveConversationWork, registerConversationNavigationGuard } from '../lib/conversation-navigation';
import type { InstalledAppCommand } from '../lib/installed-app-commands';
import { AppCommandReview } from './AppCommandReview';
import { ConversationWorkControls, type ConversationWorkLayoutActions } from './ConversationWorkControls';

interface Props {
  workspaceId: string;
  channelId: string;
  channelName: string;
  command: InstalledAppCommand;
  arguments: Record<string, string>;
  layoutActions: ConversationWorkLayoutActions;
  closeController: { current: (() => void) | null };
  onClose: () => void;
}

/** An installed command is reviewed in the same retained work as documents. */
export function AppCommandWork({ workspaceId, channelId, channelName, command, arguments: args, layoutActions, closeController, onClose }: Props) {
  const context = useCollaborationContext();
  const surfaceId = context?.surfaceId;
  const pending = useRef(Object.values(args).some(Boolean));
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const mayLeave = useCallback(() => !pending.current || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?')), []);
  useEffect(() => registerConversationNavigationGuard({ userId: actor, credential: token, workspaceId, surfaceId }, mayLeave), [actor, token, workspaceId, surfaceId, mayLeave]);
  const close = useCallback(() => {
    if (actor !== api.getCurrentUserId() || token !== api.getAccessToken() || !mayLeaveConversationWork(surfaceId)) return;
    if (actor !== api.getCurrentUserId() || token !== api.getAccessToken()) return;
    onClose();
  }, [actor, token, onClose, surfaceId]);
  useEffect(() => { closeController.current = close; return () => { if (closeController.current === close) closeController.current = null; }; }, [closeController, close]);
  return <section className="fc-conversation-work" data-work-kind="apps" aria-label={t('Review app command')}>
    <header className="fc-conversation-work-header">
      <div><span className="fc-kicker">{channelName}</span><h2>{command.app_name}: {command.name}</h2></div>
      <ConversationWorkControls layout={layoutActions} onClose={close} />
    </header>
    <div className="fc-conversation-work-body">
      <AppCommandReview workspaceId={workspaceId} channelId={channelId} command={command} arguments={args}
        onPendingChange={value => { pending.current = value; }} onClose={close} />
    </div>
  </section>;
}
