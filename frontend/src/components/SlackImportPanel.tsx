import { useEffect, useRef, useState } from 'react';
import { Archive, CheckCircle2, Loader2, Upload } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { workspaceApi, type WorkspaceMember } from '../lib/workspace-api';
import { slackImportApi, type SlackPreview, type SlackImportResult } from '../lib/slack-import-api';
import '../styles/app-platform.css';
import '../styles/product-expansion.css';

export function SlackImportPanel({ workspaceId, onGuardChange }: { workspaceId: string; onGuardChange?: (guard: 'busy' | 'secret' | null) => void }) {
  useI18n();
  const [preview, setPreview] = useState<SlackPreview | null>(null), [result, setResult] = useState<SlackImportResult | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]), [selected, setSelected] = useState<string[]>([]), [mapping, setMapping] = useState<Record<string, string>>({});
  const [prefix, setPrefix] = useState('slack-'), [reviewed, setReviewed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const owner = useRef(api.getAccessToken()), mounted = useRef(true), operation = useRef(0);
  const current = (revision = operation.current) => mounted.current && revision === operation.current && owner.current === api.getAccessToken();
  useEffect(() => { mounted.current = true; void workspaceApi.members(workspaceId).then(rows => { if (current()) setMembers(rows.filter(row => row.role !== 'guest')); }).catch(failure => { if (current()) setError(failure instanceof Error ? failure.message : t('Workspace members could not be loaded.')); }); return () => { mounted.current = false; operation.current++; }; }, [workspaceId]);
  useEffect(() => { onGuardChange?.(busy ? 'busy' : null); }, [busy, onGuardChange]);
  async function run(action: () => Promise<void>) { if (busy) return; const revision = operation.current; setBusy(true); setError(''); try { await action(); } catch (failure) { if (current(revision)) setError(failure instanceof Error ? failure.message : t('The archive operation failed.')); } finally { if (current(revision)) setBusy(false); } }
  async function upload(file: File) {
    if (file.size > 10 * 1024 * 1024) throw new Error(t('Choose a Slack ZIP smaller than 10 MB.'));
    const value = await slackImportApi.preview(workspaceId, file); if (!current()) return;
    setPreview(value); setSelected([]); setMapping({}); setReviewed(false); setResult(value.result || null);
  }
  return <section className="fc-app-platform fc-product-expansion" aria-label={t('Import from Slack')}>
    <div className="fc-app-platform-intro"><Archive size={25} /><div><span className="fc-kicker">{t('Bring your history')}</span><h3>{t('A considered move from Slack.')}</h3><p>{t('Preview your export, choose conversations and map people deliberately. Existing channels stay in place.')}</p></div></div>
    {error && <p role="alert" className="fc-alert fc-account-error">{error}</p>}
    {result ? <section className="fc-expansion-result" role="status"><CheckCircle2 size={24} /><h4>{t('Your archive is ready.')}</h4><p>{t('Imported')}: {result.channels.length} {t('channels')} · {result.messages} {t('messages')} · {result.files} {t('files')}</p><ul>{result.channels.map(channel => <li key={channel.id}>{channel.name}</li>)}</ul>{result.missing_files > 0 && <p>{t('Attachments absent from the export')}: {result.missing_files}</p>}<button className="fc-button" onClick={() => { setPreview(null); setResult(null); window.dispatchEvent(new Event('freecollab:launch-refresh')); }}>{t('Import another archive')}</button></section> : <>
      <label className="fc-expansion-upload"><Upload size={22} /><strong>{t('Choose a Slack ZIP export')}</strong><span>{t('10 MB compressed · 50 MB expanded · 10,000 messages')}</span><input type="file" accept=".zip,application/zip" disabled={busy} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void run(() => upload(file)); }} /></label>
      {busy && <p role="status"><Loader2 size={16} className="fc-account-spinner" />{t('Processing your archive…')}</p>}
      {preview?.channels && <form onSubmit={event => { event.preventDefault(); void run(async () => { const imported = await slackImportApi.confirm(workspaceId, preview.id, { channel_ids: selected, user_mapping: mapping, channel_prefix: prefix, acknowledge_native_archive: reviewed }); if (current()) { setResult(imported); window.dispatchEvent(new Event('freecollab:launch-refresh')); } }); }}><fieldset disabled={busy}>
        <fieldset><legend>{t('Choose up to 25 channels')}</legend>{preview.channels.map(channel => <label className="fc-app-check" key={channel.id}><input type="checkbox" checked={selected.includes(channel.id)} disabled={!selected.includes(channel.id) && selected.length >= 25} onChange={event => { setSelected(previous => event.target.checked ? [...previous, channel.id] : previous.filter(id => id !== channel.id)); setReviewed(false); }} /><span><strong>{channel.name}</strong> · {channel.message_count} {t('messages')}</span></label>)}</fieldset>
        <label>{t('New channel prefix')}<input required value={prefix} minLength={1} maxLength={30} onChange={event => { setPrefix(event.target.value); setReviewed(false); }} /></label>
        <details><summary>{t('Map historical people to current members')}</summary><p>{t('Mapping adds selected current members only to their imported channels. It never creates accounts. Unmapped names remain historical labels.')}</p><div className="fc-expansion-mappings">{preview.users?.map(user => <label key={user.id}>{user.name}<select aria-label={user.name} value={mapping[user.id] || ''} onChange={event => { const value = event.target.value; setMapping(previous => { const next = { ...previous }; if (value) next[user.id] = value; else delete next[user.id]; return next; }); setReviewed(false); }}><option value="">{t('Historical label only')}</option>{members.map(member => <option key={member.user_id} value={member.user_id}>{member.name}</option>)}</select></label>)}</div></details>
        <p>{t('Bundled attachments')}: {preview.bundled_files} · {t('Attachments absent from the export')}: {preview.missing_files}</p>
        <p>{t('Remote Slack attachment links are never fetched. Bundled files use files/SLACK_FILE_ID inside the ZIP. Replies retain their thread relationships.')}</p>
        <label className="fc-app-check"><input type="checkbox" required checked={reviewed} onChange={event => setReviewed(event.target.checked)} /><span>{t('I reviewed the channel selection and member mapping. This creates native channels with server-readable archives; it does not turn Slack history into end-to-end encrypted messages. Historical messages show their Slack author and are imported by my account.')}</span></label>
        <div className="fc-app-actions"><button className="fc-button fc-button-primary" disabled={!reviewed || !selected.length || !prefix}>{t('Import selected history')}</button><button type="button" className="fc-button" onClick={() => void run(async () => { await slackImportApi.discard(workspaceId, preview.id); if (current()) setPreview(null); })}>{t('Discard preview')}</button></div>
      </fieldset></form>}
    </>}
  </section>;
}
