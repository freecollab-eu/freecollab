import { useEffect, useRef, useState } from 'react';
import { Archive, CheckCircle2, Loader2, Upload } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { workspaceApi, type WorkspaceMember } from '../lib/workspace-api';
import { slackImportApi, type SlackPreview, type SlackImportResult } from '../lib/slack-import-api';
import { reviewSlackArchive, SLACK_LIMITS, type SlackArchive } from '../lib/slack-archive';
import { secureSlackMigration, discardSecureSlackMigration } from '../lib/slack-secure-import';
import '../styles/app-platform.css';
import '../styles/product-expansion.css';

export function SlackImportPanel({ workspaceId, onGuardChange }: { workspaceId: string; onGuardChange?: (guard: 'busy' | 'secret' | null) => void }) {
  useI18n();
  const [preview, setPreview] = useState<SlackPreview | null>(null), [result, setResult] = useState<SlackImportResult | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]), [selected, setSelected] = useState<string[]>([]), [mapping, setMapping] = useState<Record<string, string>>({});
  const [prefix, setPrefix] = useState('slack-'), [reviewed, setReviewed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [mode, setMode] = useState<'native' | 'secure'>('native'), [progress, setProgress] = useState<{ processed: number; total: number } | null>(null), [paused, setPaused] = useState(false);
  const localArchive = useRef<SlackArchive | null>(null), pause = useRef(false);
  const owner = useRef(api.getAccessToken()), mounted = useRef(true), operation = useRef(0);
  const current = (revision = operation.current) => mounted.current && revision === operation.current && owner.current === api.getAccessToken();
  useEffect(() => {
    mounted.current = true; owner.current = api.getAccessToken(); const revision = ++operation.current;
    pause.current = false; localArchive.current = null; setPreview(null); setResult(null); setSelected([]); setMapping({}); setReviewed(false); setProgress(null); setError(''); setBusy(false);
    void workspaceApi.members(workspaceId).then(rows => { if (current(revision)) setMembers(rows.filter(row => row.role !== 'guest')); }).catch(failure => { if (current(revision)) setError(failure instanceof Error ? failure.message : t('Workspace members could not be loaded.')); });
    return () => { mounted.current = false; operation.current++; pause.current = true; localArchive.current = null; };
  }, [workspaceId]);
  useEffect(() => { onGuardChange?.(busy ? 'busy' : null); }, [busy, onGuardChange]);
  async function run(action: () => Promise<void>) { if (busy) return; const revision = operation.current; setBusy(true); setError(''); setPaused(false); pause.current = false; try { await action(); } catch (failure) { if (current(revision)) { if (pause.current) setPaused(true); else setError(failure instanceof Error ? failure.message : t('The archive operation failed.')); } } finally { if (current(revision)) setBusy(false); } }
  async function upload(file: File) {
    if (file.size > SLACK_LIMITS.compressed) throw new Error(t('Choose a Slack ZIP smaller than 50 MB.'));
    const revision = operation.current;
    let value: SlackPreview;
    if (mode === 'secure') {
      const archive = await reviewSlackArchive(file); if (!current(revision)) return; localArchive.current = archive;
      value = { id: 'local:' + archive.digest, status: 'preview', channels: archive.channels.map(channel => ({ id: channel.id, name: channel.name, member_ids: channel.members, message_count: channel.messages.length })), users: [...archive.labels].map(([id, name]) => ({ id, name })), bundled_files: archive.bundledFiles, missing_files: archive.missingFiles };
    } else value = await slackImportApi.preview(workspaceId, file);
    if (!current(revision)) return; setPreview(value); setSelected([]); setMapping({}); setReviewed(false); setResult(value.result || null); setProgress(null);
  }
  async function migrate() {
    if (!preview) return; const revision = operation.current;
    if (mode === 'secure') {
      if (!localArchive.current) throw new Error(t('Migration paused. Choose the exact same ZIP to resume safely.'));
      const imported = await secureSlackMigration(localArchive.current, workspaceId, { channels: selected, mapping, prefix }, (processed, total) => { if (current(revision)) setProgress({ processed, total }); }, () => current(revision) && !pause.current);
      if (current(revision)) { setResult(imported); localArchive.current = null; window.dispatchEvent(new Event('freecollab:launch-refresh')); window.dispatchEvent(new Event('freecollab:channels-updated')); }
    } else {
      let value = await slackImportApi.begin(workspaceId, preview.id, { channel_ids: selected, user_mapping: mapping, channel_prefix: prefix, acknowledge_native_archive: reviewed });
      while (value.status !== 'completed') {
        if (!current(revision)) return; if (pause.current) { setPaused(true); return; }
        setProgress({ processed: value.processed || 0, total: value.total || 0 });
        if (value.status !== 'in_progress' || value.sequence == null) throw new Error(t('Migration paused. Choose the exact same ZIP to resume safely.'));
        value = await slackImportApi.step(workspaceId, preview.id, value.sequence);
      }
      if (current(revision) && value.result) { setResult(value.result); window.dispatchEvent(new Event('freecollab:launch-refresh')); window.dispatchEvent(new Event('freecollab:channels-updated')); }
    }
  }
  return <section className="fc-app-platform fc-product-expansion" aria-label={t('Import from Slack')}>
    <div className="fc-app-platform-intro"><Archive size={25} /><div><span className="fc-kicker">{t('Bring your history')}</span><h3>{t('A considered move from Slack.')}</h3><p>{t('Preview your export, choose conversations and map people deliberately. Existing channels stay in place.')}</p></div></div>
    {error && <p role="alert" className="fc-alert fc-account-error">{error}</p>}
    {paused && <p role="status">{t('Migration paused. Choose the exact same ZIP to resume safely.')}</p>}
    {result ? <section className="fc-expansion-result" role="status"><CheckCircle2 size={24} /><h4>{t('Your archive is ready.')}</h4><p>{t('Imported')}: {result.channels.length} {t('channels')} · {result.messages} {t('messages')} · {result.files} {t('files')}</p><ul>{result.channels.map(channel => <li key={channel.id}>{channel.name}</li>)}</ul>{result.missing_files > 0 && <p>{t('Attachments absent from the export')}: {result.missing_files}</p>}<button className="fc-button" onClick={() => { setPreview(null); setResult(null); window.dispatchEvent(new Event('freecollab:launch-refresh')); window.dispatchEvent(new Event('freecollab:channels-updated')); }}>{t('Import another archive')}</button></section> : <>
      <label>{t('Migration storage')}<select aria-label={t('Migration storage')} value={mode} disabled={busy || !!preview} onChange={event => { setMode(event.target.value as 'native' | 'secure'); localArchive.current = null; setPreview(null); }}><option value="native">{t('Server-readable native archive')}</option><option value="secure">{t('End-to-end encrypted archive')}</option></select></label>
      {mode === 'secure' && <><p>{t('The encrypted option reads this ZIP only on your device. Historical text, reactions and edits are encrypted before leaving it.')}</p><p>{t('Encrypted migration requires this original sign-in and trusted device to resume. Keep the original ZIP; it is not saved in offline storage.')}</p></>}
      <label className="fc-expansion-upload"><Upload size={22} /><strong>{t('Choose a Slack ZIP export')}</strong><span>{t('50 MB compressed · 100 MB expanded · 100,000 messages')}</span><input type="file" accept=".zip,application/zip" disabled={busy} onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void run(() => upload(file)); }} /></label>
      {busy && <p role="status"><Loader2 size={16} className="fc-account-spinner" />{t('Processing your archive…')}{progress && <> {progress.processed.toLocaleString()} / {progress.total.toLocaleString()}</>}</p>}
      {busy && progress && <button type="button" className="fc-button" onClick={() => { pause.current = true; }}>{t('Pause after this batch')}</button>}
      {preview?.channels && <form onSubmit={event => { event.preventDefault(); void run(migrate); }}><fieldset disabled={busy}>
        <p>{t('Review the same channel selection, prefix and mapping to resume an earlier migration.')}</p>
        <fieldset><legend>{t('Choose up to 100 channels')}</legend>{preview.channels.map(channel => <label className="fc-app-check" key={channel.id}><input type="checkbox" checked={selected.includes(channel.id)} disabled={!selected.includes(channel.id) && selected.length >= 100} onChange={event => { setSelected(previous => event.target.checked ? [...previous, channel.id] : previous.filter(id => id !== channel.id)); setReviewed(false); }} /><span><strong>{channel.name}</strong> · {channel.message_count} {t('messages')}</span></label>)}</fieldset>
        <label>{t('New channel prefix')}<input required value={prefix} minLength={1} maxLength={30} onChange={event => { setPrefix(event.target.value); setReviewed(false); }} /></label>
        <details><summary>{t('Map historical people to current members')}</summary><p>{t('Mapping adds selected current members only to their imported channels. It never creates accounts. Unmapped names remain historical labels.')}</p><div className="fc-expansion-mappings">{preview.users?.map(user => <label key={user.id}>{user.name}<select aria-label={user.name} value={mapping[user.id] || ''} onChange={event => { const value = event.target.value; setMapping(previous => { const next = { ...previous }; if (value) next[user.id] = value; else delete next[user.id]; return next; }); setReviewed(false); }}><option value="">{t('Historical label only')}</option>{members.map(member => <option key={member.user_id} value={member.user_id}>{member.name}</option>)}</select></label>)}</div></details>
        <p>{t('Bundled attachments')}: {preview.bundled_files} · {t('Attachments absent from the export')}: {preview.missing_files}</p>
        <p>{t('Remote Slack attachment links are never fetched. Bundled files use files/SLACK_FILE_ID inside the ZIP. Replies retain their thread relationships.')}</p>
        <label className="fc-app-check"><input type="checkbox" required checked={reviewed} onChange={event => setReviewed(event.target.checked)} /><span>{mode === 'secure' ? t('I reviewed these destinations and members. Historical authors and reactions remain export statements, not actions by current members.') : t('I reviewed the channel selection and member mapping. This creates native channels with server-readable archives; it does not turn Slack history into end-to-end encrypted messages. Historical messages show their Slack author and are imported by my account.')}</span></label>
        <div className="fc-app-actions"><button className="fc-button fc-button-primary" disabled={!reviewed || !selected.length || !prefix}>{progress || paused ? t('Continue migration') : t('Import selected history')}</button><button type="button" className="fc-button" onClick={() => void run(async () => { if (mode === 'native') await slackImportApi.discard(workspaceId, preview.id); else if (localArchive.current) await discardSecureSlackMigration(workspaceId, localArchive.current.digest); if (current()) { setPreview(null); localArchive.current = null; setProgress(null); } })}>{t('Discard preview')}</button></div>
      </fieldset></form>}
    </>}
  </section>;
}
