import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Link, Pause, RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import { useScopedWritingGuard } from '../lib/useScopedWritingGuard';
import { slackLivebridgeApi, type SlackBridge, type SlackBridgeInput, type SlackBridgeJob } from '../lib/slack-livebridge-api';
import { useI18n } from '../i18n';

interface Props { workspaceId: string; channelId: string }
const blank = (channelId: string): SlackBridgeInput => ({ channel_id: channelId, slack_team_id: '', slack_channel_id: '', slack_bot_user_id: '', expected_generation: 0, inbound: true, outbound: false, enabled: true, disclose_future_messages: false, password: '' });

export function SlackBridgeDisclosure({ workspaceId, channelId }: Props) {
  const { t } = useI18n(); const [bridges, setBridges] = useState<SlackBridge[]>([]);
  useEffect(() => {
    let live = true; const token = api.getAccessToken(), actor = api.getCurrentUserId();
    const load = () => slackLivebridgeApi.list(workspaceId, channelId).then(value => { if (live && token === api.getAccessToken() && actor === api.getCurrentUserId()) setBridges(value.bridges.filter(bridge => bridge.enabled)); }).catch(() => { if (live) setBridges([]); });
    const invalidate = () => { live = false; setBridges([]); };
    window.addEventListener('freecollab:auth-changed', invalidate);
    void load(); const timer = window.setInterval(() => void load(), 15000);
    return () => { live = false; window.clearInterval(timer); window.removeEventListener('freecollab:auth-changed', invalidate); };
  }, [workspaceId, channelId]);
  return bridges.length ? <aside className="fc-alert" role="note" aria-label={t('Slack bridge disclosure')}><Link size={14} />{bridges.map(bridge => <span key={bridge.id}>{t(bridge.outbound ? 'Messages in this channel are shared with Slack.' : 'This channel receives messages from Slack.')} <code>{bridge.slack_team_id} / {bridge.slack_channel_id}</code></span>)}</aside> : null;
}

export function SlackBridgePanel({ workspaceId, channelId }: Props) {
  const { t } = useI18n(); const [bridges, setBridges] = useState<SlackBridge[]>([]), [canManage, setCanManage] = useState(false);
  const [editor, setEditor] = useState<SlackBridgeInput | null>(null), [selected, setSelected] = useState<SlackBridge | null>(null), [jobs, setJobs] = useState<SlackBridgeJob[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [publicExample, setPublicExample] = useState(false);
  const identityKey = `${workspaceId}:${channelId}`;
  const scope = useRef({ active: true, token: api.getAccessToken(), actor: api.getCurrentUserId(), key: identityKey });
  const current = () => scope.current.active && scope.current.key === identityKey && scope.current.token === api.getAccessToken() && scope.current.actor === api.getCurrentUserId();
  useScopedWritingGuard(channelId, !!editor, busy, { workspaceId });
  async function load() {
    const next = await slackLivebridgeApi.list(workspaceId, channelId);
    if (current()) { setBridges(next.bridges); setCanManage(next.can_manage); setPublicExample(next.public_example); }
  }
  useEffect(() => {
    scope.current = { active: true, token: api.getAccessToken(), actor: api.getCurrentUserId(), key: identityKey };
    setBridges([]); setEditor(null); setSelected(null); setJobs([]); setError(''); setNotice(''); setBusy(false);
    void load().catch(failure => { if (current()) setError(failure instanceof Error ? failure.message : t('Slack bridges could not be loaded.')); });
    const invalidate = () => { scope.current.active = false; setEditor(null); setSelected(null); setBridges([]); setJobs([]); setCanManage(false); setBusy(false); };
    const storage = (event: StorageEvent) => { if (event.key === null || event.key === 'freecollab_token' || event.key === 'freecollab_user_id') invalidate(); };
    window.addEventListener('freecollab:auth-changed', invalidate); window.addEventListener('storage', storage);
    return () => { scope.current.active = false; window.removeEventListener('freecollab:auth-changed', invalidate); window.removeEventListener('storage', storage); };
  }, [workspaceId, channelId]);
  async function act(run: () => Promise<void>) {
    if (busy || !current()) return; setBusy(true); setError(''); setNotice('');
    try { await run(); } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t('Slack bridge action failed.')); }
    finally { if (current()) setBusy(false); }
  }
  const edit = (bridge?: SlackBridge) => {
    setSelected(bridge || null); setJobs([]); setEditor(bridge ? { ...blank(channelId), slack_team_id: bridge.slack_team_id, slack_channel_id: bridge.slack_channel_id, slack_bot_user_id: bridge.slack_bot_user_id, expected_generation: bridge.generation, inbound: bridge.inbound, outbound: bridge.outbound, enabled: true } : blank(channelId));
  };
  function field<K extends keyof SlackBridgeInput>(key: K, value: SlackBridgeInput[K]) { setEditor(previous => previous ? { ...previous, [key]: value, ...(key !== 'disclose_future_messages' ? { disclose_future_messages: false } : {}) } : previous); }
  return <section className="fc-app-platform" aria-label={t('Continuous Slack bridge')}>
    <h3>{t('Continuous Slack bridge')}</h3><p>{t('Connect your own Slack app to this channel. New messages, edits, deletions and mapped threads use durable delivery receipts; importing an archive is separate.')}</p>
    {error && <p role="alert" className="fc-alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {publicExample ? <p>{t('External services are not connected in this public example.')}</p> : <>
      {canManage && !editor && <button type="button" className="fc-button" onClick={() => edit()}>{t('Connect Slack channel')}</button>}
      {bridges.map(bridge => <article key={bridge.id} className="fc-app-card"><strong>{bridge.slack_team_id} / {bridge.slack_channel_id}</strong><p>{t(bridge.enabled ? 'Enabled' : 'Paused')} · {bridge.inbound ? 'Slack → FreeCollab' : ''} {bridge.outbound ? 'FreeCollab → Slack' : ''} · {bridge.last_status}</p>
        <p>{t('Slack Events Request URL')}: <code>{new URL(api.getBaseUrl().replace(/\/$/, '') + '/slack/bridges/' + encodeURIComponent(bridge.id) + '/events', location.origin).href}</code></p>
        {canManage && <div className="fc-app-actions"><button type="button" className="fc-button" disabled={busy} onClick={() => edit(bridge)}>{t(bridge.enabled ? 'Edit connection' : 'Review and resume')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => void act(async () => { await slackLivebridgeApi.test(workspaceId, bridge.id); if (current()) setNotice(t('Slack bot and workspace identity verified. Invite the bot to the mapped channel and subscribe message events in your Slack app.')); })}>{t('Test connection')}</button>
          {bridge.enabled && <button type="button" className="fc-button" disabled={busy} onClick={() => void act(async () => { await slackLivebridgeApi.pause(workspaceId, bridge.id); if (current()) await load(); })}><Pause size={14} />{t('Pause')}</button>}
          <button type="button" className="fc-button" disabled={busy} onClick={() => void act(async () => { const result = await slackLivebridgeApi.jobs(workspaceId, bridge.id); if (current()) { setSelected(bridge); setJobs(result.jobs); } })}><RefreshCw size={14} />{t('Delivery receipts')}</button>
          <button type="button" className="fc-button" disabled={busy} onClick={() => void act(async () => { if (!window.confirm(t('Revoke this Slack bridge and cancel pending deliveries?'))) return; await slackLivebridgeApi.revoke(workspaceId, bridge.id); if (current()) { setJobs([]); setSelected(null); await load(); } })}>{t('Revoke connection')}</button></div>}
      </article>)}
      {editor && <form onSubmit={event => { event.preventDefault(); void act(async () => { const result = await slackLivebridgeApi.save(workspaceId, editor, selected?.id); if (current()) { setEditor(null); setSelected(result.bridge); setNotice(t('Mapping saved. Test your Slack app and configure its signed event URL. No history was backfilled.')); await load(); } }); }}><fieldset disabled={busy}>
        <label>{t('Slack workspace ID')}<input required pattern="[A-Z][A-Z0-9]{2,40}" value={editor.slack_team_id} onChange={event => field('slack_team_id', event.target.value)} /></label>
        <label>{t('Slack channel ID')}<input required pattern="[A-Z][A-Z0-9]{2,40}" value={editor.slack_channel_id} onChange={event => field('slack_channel_id', event.target.value)} /></label>
        <label>{t('Slack bot user ID')}<input required pattern="[A-Z][A-Z0-9]{2,40}" value={editor.slack_bot_user_id} onChange={event => field('slack_bot_user_id', event.target.value)} /></label>
        <label>{t('Slack bot token')}<input type="password" autoComplete="new-password" required={!selected} value={editor.bot_token || ''} onChange={event => field('bot_token', event.target.value || undefined)} /></label>
        <label>{t('Slack signing secret')}<input type="password" autoComplete="new-password" required={!selected} value={editor.signing_secret || ''} onChange={event => field('signing_secret', event.target.value || undefined)} /></label>
        <p>{t('Stored encrypted. Leave secret fields blank to retain this connection’s existing credentials. Your Slack app needs chat:write and the message event subscriptions for the mapped channel; private channels require their corresponding Slack grants.')}</p>
        <label className="fc-app-check"><input type="checkbox" checked={editor.inbound} onChange={event => field('inbound', event.target.checked)} />{t('Receive new Slack messages, edits and deletions')}</label>
        <label className="fc-app-check"><input type="checkbox" checked={editor.outbound} onChange={event => field('outbound', event.target.checked)} />{t('Share future FreeCollab messages with Slack')}</label>
        <aside className="fc-alert"><AlertTriangle size={16} /><p>{t('Slack receives future channel messages when outbound sharing is enabled. Slack retains its own copies. Imported posts are clearly labeled with the Slack user ID and attributed to the authorizing account; Slack is not end-to-end encrypted.')}</p></aside>
        <label>{t('Current password')}<input type="password" autoComplete="current-password" required value={editor.password} onChange={event => field('password', event.target.value)} /></label>
        <label>{t('Authenticator or recovery code')}<input autoComplete="one-time-code" value={editor.code || ''} onChange={event => field('code', event.target.value || undefined)} /></label>
        <label className="fc-app-check"><input type="checkbox" required checked={editor.disclose_future_messages} onChange={event => field('disclose_future_messages', event.target.checked)} />{t('I authorize this exact channel mapping and future message disclosure in the selected directions.')}</label>
        <button type="submit" className="fc-button fc-button-primary" disabled={!editor.disclose_future_messages || !editor.inbound && !editor.outbound}>{t('Save reviewed Slack bridge')}</button><button type="button" className="fc-button" onClick={() => setEditor(null)}>{t('Cancel')}</button>
      </fieldset></form>}
      {jobs.map(job => <article className="fc-app-job" key={job.id}><code>{job.id}</code><span>{job.direction} · {job.status} · {job.attempts}</span>{job.failure && <p>{job.failure}</p>}{selected && ['uncertain', 'failed'].includes(job.status) && <button type="button" className="fc-button" disabled={busy} onClick={() => void act(async () => { if (!window.confirm(t('Inspect Slack first. Confirm this exact request was not processed before retrying.'))) return; await slackLivebridgeApi.retry(workspaceId, selected.id, job.id); if (current()) setJobs((await slackLivebridgeApi.jobs(workspaceId, selected.id)).jobs); })}>{t('Confirm inspected and retry')}</button>}</article>)}
    </>}
  </section>;
}
