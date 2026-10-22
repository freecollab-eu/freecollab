import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { readSlackHistory } from '../lib/slack-secure-import';
import type { SlackHistoricalDetails } from '../lib/slack-archive';
import { t, useI18n } from '../i18n';
import '../styles/slack-history.css';
/** Historical export statements stay separate from current reactions and edits. */
export function HistoricalSlackDetails({ channelId, messageId, secure }: { channelId: string; messageId: string; secure: boolean }) {
  useI18n(); const [data, setData] = useState<SlackHistoricalDetails | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const mounted = useRef(false), generation = useRef(0), loaded = useRef(false);
  useEffect(() => { mounted.current = true; generation.current++; loaded.current = false; setData(null); setError(''); setBusy(false); return () => { mounted.current = false; generation.current++; }; }, [channelId, messageId, secure]);
  async function load() {
    if (loaded.current || busy) return; const revision = generation.current, token = api.getAccessToken(); setBusy(true); setError('');
    const current = () => mounted.current && revision === generation.current && token === api.getAccessToken();
    try { const value = await readSlackHistory(channelId, messageId, secure); if (current()) { setData(value); loaded.current = true; } }
    catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t('Imported history could not be loaded.')); }
    finally { if (current()) setBusy(false); }
  }
  return <details className="fc-slack-history" onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary>{t('Imported Slack history')}{data && <span>{data.reactions.reduce((total, reaction) => total + reaction.count, 0)} {t('historical reactions')} · {data.history.length} {t('recorded edits')}</span>}</summary>
    <p className="fc-chat-muted">{t('These are statements from the supplied export, imported by the current sender. They are separate from actions by current members.')}</p>
    {busy && <p role="status">{t('Loading imported history…')}</p>}
    {error && <p role="alert">{error} <button type="button" className="fc-chat-text-button" onClick={() => void load()}>{t('Retry')}</button></p>}
    {!busy && loaded.current && !data && <p>{t('This message has no retained Slack provenance.')}</p>}
    {data && <>
      <dl><div><dt>{t('Original author')}</dt><dd>{data.author.name}</dd></div><div><dt>{t('Slack timestamp')}</dt><dd>{data.source_timestamp}</dd></div>{data.edited_at && <div><dt>{t('Last exported edit')}</dt><dd>{data.edited_at}</dd></div>}</dl>
      {data.reactions.length > 0 && <div className="fc-slack-history-reactions" aria-label={t('Historical Slack reactions')}>{data.reactions.map(reaction => <span key={reaction.name} title={reaction.users.map(user => user.name).join(', ')}>:{reaction.name}: <strong>{reaction.count}</strong><small>{reaction.users.map(user => user.name).join(', ') || t('Reactors were not included in the export')}</small></span>)}</div>}
      <details><summary>{t('Full exported message')}</summary><p className="fc-slack-history-text">{data.current_text}</p></details>
      {data.history.length > 0 && <ol aria-label={t('Exported edit history')}>{data.history.map((revision, index) => <li key={index}><span>{revision.author.name} · {revision.timestamp}</span><p className="fc-slack-history-text">{revision.text}</p></li>)}</ol>}
      {data.edited_at && data.history.length === 0 && <p>{t('The export records an edit but does not contain the earlier message body.')}</p>}
    </>}
  </details>;
}
