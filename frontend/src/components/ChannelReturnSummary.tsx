import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ChevronDown, History, Loader2 } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { formatDate, useI18n } from '../i18n';
import { loadChannelReturnSummary } from '../lib/channel-return-loader';
import { returnTimestamp, type ChannelReturnBaseline, type ChannelReturnSource, type ChannelReturnSnapshot } from '../lib/channel-return-summary';
import '../styles/channel-return-summary.css';

export type { ChannelReturnBaseline, ChannelReturnSource } from '../lib/channel-return-summary';
interface Props {
  workspaceId: string; channel: Pick<Channel, 'id' | 'secure' | 'workspace_id'>;
  baseline: ChannelReturnBaseline; entryKey: string | number;
  onOpenSource: (source: ChannelReturnSource) => void;
}
type State = { scope: string; generation: number; status: 'loading' | 'ready' | 'error'; snapshot?: ChannelReturnSnapshot };

/** An entry-scoped inspection. It neither writes receipts nor keeps account storage. */
export function ChannelReturnSummary({ workspaceId, channel, baseline, entryKey, onOpenSource }: Props) {
  const { t } = useI18n(), [state, setState] = useState<State | null>(null), [retry, setRetry] = useState(0);
  const token = api.getAccessToken(), user = api.getCurrentUserId();
  const since = baseline.status === 'known' ? baseline.readAt : null;
  const scope = JSON.stringify([token, user, workspaceId, channel.id, !!channel.secure, entryKey,
    baseline.status, since, baseline.status === 'known' ? baseline.lastReadMessageId : null, retry]);
  const live = useRef(scope); live.current = scope;
  const generation = useRef(0);
  const eligible = !!token && !!user && !!workspaceId && baseline.status === 'known' && !!since && Number.isFinite(returnTimestamp(since));
  useEffect(() => {
    const request = ++generation.current, abort = new AbortController();
    const current = () => !abort.signal.aborted && generation.current === request && live.current === scope
      && token === api.getAccessToken() && user === api.getCurrentUserId();
    const invalidate = () => { abort.abort(); generation.current++; setState(null); };
    window.addEventListener('freecollab:auth-changed', invalidate);
    window.addEventListener('freecollab:session-expired', invalidate);
    if (eligible) {
      setState({ scope, generation: request, status: 'loading' });
      void loadChannelReturnSummary({ workspaceId, channel, baseline, signal: abort.signal, isCurrent: current }).then(snapshot => {
        if (current()) setState({ scope, generation: request, status: 'ready', snapshot });
      }).catch(() => { if (current()) setState({ scope, generation: request, status: 'error' }); });
    } else setState(null);
    return () => { abort.abort(); generation.current++; window.removeEventListener('freecollab:auth-changed', invalidate); window.removeEventListener('freecollab:session-expired', invalidate); };
  }, [scope, eligible]);
  if (!eligible || state?.scope !== scope) return null;
  if (state.status === 'loading') return <div className="fc-channel-return-status" role="status"><Loader2 size={14} />{t('Checking what changed since your last read…')}</div>;
  if (state.status === 'error' || !state.snapshot) return <div className="fc-channel-return-status" role="status"><span>{t('Your return summary is unavailable.')}</span><button type="button" className="fc-text-button" onClick={() => setRetry(value => value + 1)}>{t('Try again')}</button></div>;
  const snapshot = state.snapshot;
  const open = (source: ChannelReturnSource) => {
    if (state.generation !== generation.current || live.current !== scope || token !== api.getAccessToken() || user !== api.getCurrentUserId()) return;
    onOpenSource({ ...source });
  };
  const labels = { decisions: 'Decisions', documents: 'Updated documents', attention: 'For your attention' };
  const reasons = { decision: 'Decision', document: 'Shared document', mention: 'You were mentioned', reply: 'Reply to your message', assignment: 'Open task assigned to you' };
  return <section className="fc-channel-return" aria-label={t('Since your last read')}>
    <details>
      <summary><History size={16} /><span><strong>{t('Since your last read')}</strong><small>{(['decisions', 'documents', 'attention'] as const).map(key => {
        const group = snapshot.groups[key];
        return <span key={key}>{t(labels[key])}: {snapshot.secure || group.incomplete ? t('At least {count}', { count: group.count }) : group.count}</span>;
      })}</small></span><ChevronDown className="fc-channel-return-chevron" size={16} /></summary>
      <p className="fc-channel-return-caption">{t('A quick return to the conversation. Mentions and replies use explicit recipients; assignments are still open.')}</p>
      <div className="fc-channel-return-groups">{(['decisions', 'documents', 'attention'] as const).map(key => {
        const group = snapshot.groups[key];
        return <section key={key} aria-label={t(labels[key])}><h3>{t(labels[key])}</h3>{group.items.length ? <ul>{group.items.map(item => <li key={item.source.kind + ':' + item.source.id}><button type="button" onClick={() => open(item.source)}><span><strong>{item.title || t(reasons[item.reason])}</strong><small>{item.title && <>{t(reasons[item.reason])} · </>}<time dateTime={item.occurredAt}>{formatDate(new Date(returnTimestamp(item.occurredAt)), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time></small></span><ArrowUpRight size={14} /></button></li>)}</ul>
          : <p>{t(snapshot.secure || group.incomplete ? 'No matching items in available history.' : 'No changes in this category.')}</p>}
          {group.count > group.items.length && <p>{t('Showing the latest {count} items.', { count: group.items.length })}</p>}</section>;
      })}</div>
      {(snapshot.incomplete || snapshot.secure) && <p className="fc-channel-return-caption" role="note">{t(snapshot.secure ? 'Encrypted summary: available verified history only. Locked items, older messages and some document updates may be missing.' : 'This summary reached a history limit or could not verify some items. Counts cover the available records.')}</p>}
    </details>
  </section>;
}
