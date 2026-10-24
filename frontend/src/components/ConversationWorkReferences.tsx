import { useEffect, useId, useRef, useState } from 'react';
import { ArrowUpRight, CheckSquare, ChevronDown, ClipboardCheck, FileText } from 'lucide-react';
import { useAccount } from '../account/AccountProvider';
import { api, type Channel } from '../lib/api';
import { allowsChannelMaterial } from '../lib/chat-shared-material';
import { conversationWorkApi } from '../lib/conversation-work-api';
import { sharedDocumentsApi } from '../lib/shared-documents-api';
import { projectTasksApi } from '../lib/project-tasks-api';
import { collaborationApi } from '../lib/collaboration-api';
import { getSecureMessaging } from '../lib/secure-client';
import { t, useI18n } from '../i18n';
import { discussionWorkCounts, readDiscussionWork, type DiscussionWorkSnapshot, type ResolvedConversationWork } from '../lib/discussion-work';
import '../styles/discussion-work.css';

export type { ResolvedConversationWork } from '../lib/discussion-work';

/** One scoped resource load serves every message and open discussion. */
export function useConversationWorkReferences(channel: Channel | null, workspaceId: string | undefined, refreshKey: number) {
  const { workspace } = useAccount();
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const scope = JSON.stringify([actor, token, workspaceId, workspace?.id, channel?.id, channel?.workspace_id,
    channel?.secure, channel?.matrix_room_id, channel?.encrypted_channel_key]);
  const latest = useRef(scope); latest.current = scope;
  const [snapshot, setSnapshot] = useState<({ scope: string; supported: boolean } & DiscussionWorkSnapshot) | null>(null);
  const [loading, setLoading] = useState(false);
  const reload = useRef<(() => void) | null>(null), lastRefresh = useRef(refreshKey);
  const eligible = !!actor && !!token && !!channel && !!workspaceId && workspace?.id === workspaceId && allowsChannelMaterial(channel, workspaceId);
  useEffect(() => {
    let live = true, fetching = false, queued = false;
    const abort = new AbortController();
    const current = () => live && !abort.signal.aborted && latest.current === scope && token === api.getAccessToken() && actor === api.getCurrentUserId();
    const clear = () => { live = false; abort.abort(); setSnapshot(null); setLoading(false); };
    window.addEventListener('freecollab:auth-changed', clear);
    window.addEventListener('freecollab:workspace-changed', clear);
    setSnapshot(null);
    const load = async () => {
      if (!current()) return;
      if (!eligible || !channel) { setSnapshot({ scope, supported: false, rows: [], documents: [], partial: false }); return; }
      if (fetching) { queued = true; return; }
      fetching = true; setLoading(true);
      try {
        const secure = await getSecureMessaging();
        if (!current()) return;
        const encrypted = await secure.handles(channel.id);
        if (!current()) return;
        if (encrypted) { setSnapshot({ scope, supported: false, rows: [], documents: [], partial: false }); return; }
        const result = await readDiscussionWork(channel.id, {
          current,
          references: offset => conversationWorkApi.list(channel.id, { limit: 100, offset, signal: abort.signal }),
          documents: () => sharedDocumentsApi.list(channel.id),
          decisions: () => collaborationApi.decisions(channel.id),
          tasks: projectId => projectTasksApi.tasks(channel.id, projectId),
        });
        if (current() && result) setSnapshot({ scope, supported: true, ...result });
      } catch { if (current()) setSnapshot({ scope, supported: true, rows: [], documents: [], partial: true }); }
      finally { fetching = false; if (current()) setLoading(false); if (queued && current()) { queued = false; void load(); } }
    };
    const refresh = () => { if (!document.hidden) void load(); };
    reload.current = refresh; void load();
    window.addEventListener('focus', refresh); document.addEventListener('visibilitychange', refresh);
    const timer = window.setInterval(refresh, 60000);
    return () => { live = false; abort.abort(); clearInterval(timer); reload.current = null; window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh); window.removeEventListener('freecollab:auth-changed', clear); window.removeEventListener('freecollab:workspace-changed', clear); };
  }, [scope]);
  useEffect(() => { if (lastRefresh.current !== refreshKey) { lastRefresh.current = refreshKey; reload.current?.(); } }, [refreshKey]);
  const value = eligible && snapshot?.scope === scope && token === api.getAccessToken() && actor === api.getCurrentUserId() ? snapshot : null;
  const rows = value?.rows || [];
  const byMessage = new Map<string, ResolvedConversationWork[]>();
  for (const row of rows) byMessage.set(row.reference.source_message_id, [...(byMessage.get(row.reference.source_message_id) || []), row]);
  return { byMessage, rows, documents: value?.documents || [], partial: !!value?.partial, error: !!value?.partial,
    loading: eligible && (loading || !value), supported: eligible && value?.supported !== false, retry: () => reload.current?.() };
}

export function ConversationWorkReferences({ rows, onOpen, label }: { rows: ResolvedConversationWork[]; onOpen: (row: ResolvedConversationWork) => void; label?: string }) {
  if (!rows.length) return null;
  return <div className="fc-chat-linked-work" aria-label={label || t('Work from this message')}>{rows.map(row => {
    const Icon = row.reference.resource_kind === 'document' ? FileText : row.reference.resource_kind === 'task' ? CheckSquare : ClipboardCheck;
    return <button type="button" className="fc-chat-linked-work-card" key={row.reference.id} onClick={() => onOpen(row)} data-work-reference={row.reference.id}>
      <Icon size={16} aria-hidden="true" /><span><small>{t(row.reference.resource_kind === 'document' ? 'Document' : row.reference.resource_kind === 'task' ? 'Next step' : 'Decision')}</small><strong>{row.title}</strong>{row.status && <small>{t(({ todo: 'To do', doing: 'In progress', done: 'Done', proposed: 'Proposed', accepted: 'Accepted', superseded: 'Superseded' } as Record<string, string>)[row.status] || row.status)}</small>}</span><ArrowUpRight size={14} aria-hidden="true" />
    </button>;
  })}</div>;
}

/** A discussion owns one compact summary, with genuine resources on demand. */
export function DiscussionWorkSummary({ rows, onOpen, label, partial = false }: {
  rows: ResolvedConversationWork[];
  onOpen: (row: ResolvedConversationWork) => void;
  label?: string;
  partial?: boolean;
}) {
  useI18n();
  const [expanded, setExpanded] = useState(false);
  const contentId = useId(), countsId = useId();
  if (!rows.length) return null;
  const counts = discussionWorkCounts(rows);
  const kinds = (['document', 'task', 'decision'] as const).filter(kind => counts[kind] > 0);
  return <section className="fc-discussion-work" aria-label={label || t('Work in this discussion')}>
    <button type="button" className="fc-discussion-work-toggle" aria-label={label || t('Work in this discussion')}
      aria-describedby={countsId} aria-expanded={expanded} aria-controls={contentId} onClick={() => setExpanded(value => !value)}>
      <span id={countsId} className="fc-discussion-work-counts">
        {partial && <span className="fc-discussion-work-available">{t('Available work')}</span>}
        {kinds.map((kind, index) => {
          const Icon = kind === 'document' ? FileText : kind === 'task' ? CheckSquare : ClipboardCheck;
          const key = kind === 'document' ? (counts[kind] === 1 ? '{value0} document' : '{value0} documents')
            : kind === 'task' ? (counts[kind] === 1 ? '{value0} task' : '{value0} tasks')
            : counts[kind] === 1 ? '{value0} decision' : '{value0} decisions';
          return <span key={kind} data-discussion-work-kind={kind}>
            {index > 0 && <span className="fc-discussion-work-separator" aria-hidden="true">·</span>}
            <Icon size={14} aria-hidden="true" />{t(key, { value0: String(counts[kind]) })}
          </span>;
        })}
      </span>
      <ChevronDown size={15} aria-hidden="true" />
    </button>
    <div id={contentId} hidden={!expanded} className="fc-discussion-work-resources">
      <ConversationWorkReferences rows={rows} onOpen={onOpen} label={label || t('Work in this discussion')} />
      {partial && <p className="fc-discussion-work-notice">{t('Some linked work could not be loaded.')}</p>}
    </div>
  </section>;
}
