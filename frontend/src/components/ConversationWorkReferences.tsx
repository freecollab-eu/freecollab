import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, CheckSquare, ClipboardCheck, FileText } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { allowsChannelMaterial } from '../lib/chat-shared-material';
import { conversationWorkApi, type ConversationWorkReference } from '../lib/conversation-work-api';
import { sharedDocumentsApi } from '../lib/shared-documents-api';
import { projectTasksApi } from '../lib/project-tasks-api';
import { collaborationApi } from '../lib/collaboration-api';
import { getSecureMessaging } from '../lib/secure-client';
import { t } from '../i18n';

export interface ResolvedConversationWork { reference: ConversationWorkReference; title: string; status?: string }

/** The routing index contains IDs. Visible content is always read from its real resource. */
export function useConversationWorkReferences(channel: Channel | null, workspaceId: string | undefined, refreshKey: number) {
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const scope = JSON.stringify([actor, token, workspaceId, channel?.id, channel?.secure, channel?.matrix_room_id, channel?.encrypted_channel_key]);
  const latest = useRef(scope); latest.current = scope;
  const [snapshot, setSnapshot] = useState<{ scope: string; rows: ResolvedConversationWork[]; error: boolean } | null>(null);
  const reload = useRef<(() => void) | null>(null), lastRefresh = useRef(refreshKey);
  useEffect(() => {
    let live = true, loading = false, queued = false;
    const abort = new AbortController();
    const current = () => live && latest.current === scope && token === api.getAccessToken() && actor === api.getCurrentUserId();
    const clear = () => { live = false; abort.abort(); setSnapshot(null); };
    window.addEventListener('freecollab:auth-changed', clear);
    window.addEventListener('freecollab:workspace-changed', clear);
    const load = async () => {
      if (!current() || !channel || !workspaceId || !allowsChannelMaterial(channel, workspaceId)) return;
      if (loading) { queued = true; return; }
      loading = true;
      try {
        const secure = await getSecureMessaging();
        if (!current() || await secure.handles(channel.id) || !current()) return;
        const page = await conversationWorkApi.list(channel.id, { limit: 50, signal: abort.signal });
        if (!current()) return;
        const projectIds = [...new Set(page.items.filter(row => row.resource_kind === 'task').map(row => row.project_id).filter((id): id is string => !!id))].slice(0, 8);
        const [documents, decisions, tasks] = await Promise.all([
          page.items.some(row => row.resource_kind === 'document') ? sharedDocumentsApi.list(channel.id) : [],
          collaborationApi.decisions(channel.id),
          Promise.all(projectIds.map(id => projectTasksApi.tasks(channel.id, id).catch(() => []))),
        ]);
        if (!current()) return;
        const taskRows = tasks.flat();
        const rows: ResolvedConversationWork[] = [];
        for (const reference of page.items) {
          if (reference.resource_kind === 'document') {
            const row = documents.find(item => item.id === reference.resource_id && item.channel_id === channel.id && !item.archived && !item.resolution_error);
            if (row?.title) rows.push({ reference, title: row.title });
          } else if (reference.resource_kind === 'task') {
            const row = taskRows.find(item => item.id === reference.resource_id && item.channel_id === channel.id && item.project_id === reference.project_id && !item.archived);
            if (row?.fields) rows.push({ reference, title: row.fields.title, status: row.fields.status });
          } else {
            const row = decisions.find(item => item.id === reference.resource_id && item.channel_id === channel.id && !item.resolution_error);
            if (row?.title) rows.push({ reference, title: row.title, status: row.status });
          }
        }
        // Decisions already carry their original message in the canonical
        // revision. Derive that relationship directly; do not invent another
        // decision or require a second successful write to display its source.
        for (const decision of decisions) {
          if (!decision.source_message_id || !decision.title || decision.resolution_error || decision.channel_id !== channel.id
              || rows.some(row => row.reference.resource_kind === 'decision' && row.reference.resource_id === decision.id && row.reference.source_message_id === decision.source_message_id)) continue;
          rows.push({ reference: { id: decision.id, channel_id: channel.id, source_message_id: decision.source_message_id,
            resource_kind: 'decision', resource_id: decision.id, project_id: null, created_by: decision.created_by, created_at: decision.created_at }, title: decision.title, status: decision.status });
        }
        if (current()) setSnapshot({ scope, rows, error: false });
      } catch { if (current()) setSnapshot({ scope, rows: [], error: true }); }
      finally { loading = false; if (queued && current()) { queued = false; void load(); } }
    };
    const refresh = () => { if (!document.hidden) void load(); };
    reload.current = refresh; void load();
    window.addEventListener('focus', refresh);
    const timer = window.setInterval(refresh, 60000);
    return () => { live = false; abort.abort(); clearInterval(timer); reload.current = null; window.removeEventListener('focus', refresh); window.removeEventListener('freecollab:auth-changed', clear); window.removeEventListener('freecollab:workspace-changed', clear); };
  }, [scope]);
  useEffect(() => { if (lastRefresh.current !== refreshKey) { lastRefresh.current = refreshKey; reload.current?.(); } }, [refreshKey]);
  const rows = snapshot?.scope === scope && token === api.getAccessToken() && actor === api.getCurrentUserId() ? snapshot.rows : [];
  const byMessage = new Map<string, ResolvedConversationWork[]>();
  for (const row of rows) byMessage.set(row.reference.source_message_id, [...(byMessage.get(row.reference.source_message_id) || []), row]);
  return { byMessage, error: snapshot?.scope === scope && snapshot.error, retry: () => reload.current?.() };
}

export function ConversationWorkReferences({ rows, onOpen }: { rows: ResolvedConversationWork[]; onOpen: (row: ResolvedConversationWork) => void }) {
  if (!rows.length) return null;
  return <div className="fc-chat-linked-work" aria-label={t('Work from this message')}>{rows.map(row => {
    const Icon = row.reference.resource_kind === 'document' ? FileText : row.reference.resource_kind === 'task' ? CheckSquare : ClipboardCheck;
    return <button type="button" className="fc-chat-linked-work-card" key={row.reference.id} onClick={() => onOpen(row)} data-work-reference={row.reference.id}>
      <Icon size={16} aria-hidden="true" /><span><small>{t(row.reference.resource_kind === 'document' ? 'Document' : row.reference.resource_kind === 'task' ? 'Next step' : 'Decision')}</small><strong>{row.title}</strong>{row.status && <small>{t(({ todo: 'To do', doing: 'In progress', done: 'Done', proposed: 'Proposed', accepted: 'Accepted', superseded: 'Superseded' } as Record<string, string>)[row.status] || row.status)}</small>}</span><ArrowUpRight size={14} aria-hidden="true" />
    </button>;
  })}</div>;
}
