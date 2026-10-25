import { useEffect, useId, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, Send } from 'lucide-react';
import { api } from '../lib/api';
import type { AppJob } from '../lib/app-platform-api';
import { installedAppCommandsApi, type InstalledAppCommand } from '../lib/installed-app-commands';
import { useI18n } from '../i18n';
import '../styles/app-platform.css';
import { ScopedCollaborationInput as CollaborationInput } from './ScopedCollaborationInput';
import { getSecureMessaging } from '../lib/secure-client';

export interface AppCommandReviewProps {
  workspaceId: string; channelId: string; command: InstalledAppCommand;
  arguments: Record<string, string>; onClose?: () => void; onExecuted?: (job: AppJob) => void; onPendingChange?: (pending: boolean) => void;
}

/** Ordinary product work surface; opening never sends arguments to an app. */
export function AppCommandReview({ workspaceId, channelId, command, arguments: initial, onClose, onExecuted, onPendingChange }: AppCommandReviewProps) {
  const { t } = useI18n();
  const writingId = useId(), activePalettes = useRef(new Set<string>());
  const submissionScope = `${writingId}:${command.key}:approve`;
  const [arguments_, setArguments] = useState<Record<string, string>>({ ...initial });
  const [approved, setApproved] = useState(false), [ready, setReady] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [job, setJob] = useState<AppJob | null>(null);
  const [attempted, setAttempted] = useState(false);
  const [decryptedConsent, setDecryptedConsent] = useState(false), [outcomeAcknowledged, setOutcomeAcknowledged] = useState(false);
  const [publishConsent, setPublishConsent] = useState(false), [published, setPublished] = useState(false);
  const foreground = command.delivery === 'foreground' && command.supports_secure_foreground === true;
  const identityKey = `${workspaceId}:${channelId}:${command.key}:${command.generation}`;
  const lease = useRef({ token: api.getAccessToken(), actor: api.getCurrentUserId(), active: true, key: identityKey });
  const request = useRef(crypto.randomUUID());
  const current = () => lease.current.active && lease.current.key === identityKey && lease.current.token === api.getAccessToken() && lease.current.actor === api.getCurrentUserId();
  const pending = busy || (!job && (attempted || approved || decryptedConsent || Object.values(arguments_).some(value => !!value.trim())))
    || !!job?.foreground && (!outcomeAcknowledged || publishConsent) && !published;
  useEffect(() => { onPendingChange?.(pending); }, [pending, onPendingChange]);
  async function verify() {
    const catalogue = await installedAppCommandsApi.list(workspaceId, channelId);
    if (!current()) return false;
    const actual = catalogue.commands.find(value => value.key === command.key);
    if (!actual || actual.generation !== command.generation || actual.origin !== command.origin
        || actual.installation_id !== command.installation_id || actual.name !== command.name || actual.app_id !== command.app_id
        || actual.delivery !== command.delivery || actual.supports_secure_foreground !== command.supports_secure_foreground
        || JSON.stringify(actual.arguments) !== JSON.stringify(command.arguments)) throw new Error(t('The installed app changed. Open its current command again.'));
    return true;
  }
  useEffect(() => {
    lease.current = { token: api.getAccessToken(), actor: api.getCurrentUserId(), active: true, key: identityKey };
    setArguments({ ...initial }); setApproved(false); setReady(false); setBusy(false); setError(''); setJob(null); setAttempted(false); request.current = crypto.randomUUID();
    setDecryptedConsent(false); setOutcomeAcknowledged(false); setPublishConsent(false); setPublished(false);
    void verify().then(valid => { if (current()) setReady(valid); }).catch(failure => { if (current()) setError(failure instanceof Error ? failure.message : t('The installed command is unavailable.')); });
    const invalidate = () => { lease.current.active = false; setArguments({}); setApproved(false); setReady(false); setBusy(false); setJob(null); setAttempted(false); setDecryptedConsent(false); setOutcomeAcknowledged(false); setPublishConsent(false); setPublished(false); };
    const storage = (event: StorageEvent) => { if (event.key === null || event.key === 'freecollab_token' || event.key === 'freecollab_user_id') invalidate(); };
    window.addEventListener('freecollab:auth-changed', invalidate); window.addEventListener('storage', storage);
    return () => { lease.current.active = false; window.removeEventListener('freecollab:auth-changed', invalidate); window.removeEventListener('storage', storage); };
  }, [workspaceId, channelId, command.key, command.generation]);
  function change(name: string, value: string) {
    setArguments(previous => ({ ...previous, [name]: value })); setApproved(false); setJob(null);
    setDecryptedConsent(false); setPublishConsent(false); setOutcomeAcknowledged(false); setPublished(false);
    if (!attempted) request.current = crypto.randomUUID();
  }
  async function invoke(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !approved || foreground && !decryptedConsent || !ready || !current() || activePalettes.current.size) return;
    setBusy(true); setError(''); setAttempted(true);
    try {
      if (!await verify()) return;
      let next: AppJob;
      if (foreground) {
        const secure = await getSecureMessaging(), room = await secure.roomFor(channelId, true);
        if (!current() || !await verify()) return;
        const device = room.c.getDeviceId(); if (!device) throw new Error(t('A current secure device is required.'));
        next = await installedAppCommandsApi.foreground(workspaceId, channelId, command, arguments_, request.current, device);
      } else next = await installedAppCommandsApi.invoke(workspaceId, channelId, command, arguments_, request.current);
      if (current()) { setJob(next); setApproved(false); setDecryptedConsent(false); setOutcomeAcknowledged(next.status === 'delivered'); onExecuted?.(next); }
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t('The app command could not be queued.')); }
    finally { if (current()) setBusy(false); }
  }
  async function publish() {
    if (busy || !current() || !publishConsent || !job?.preview || job.status !== 'delivered') return;
    const preview = job.preview, receipt = job.id; setBusy(true); setError('');
    try {
      if (!await verify() || !current()) return;
      const secure = await getSecureMessaging(); if (!current()) return;
      await secure.sendMessage(channelId, preview, undefined, [], [], 'app-response-' + receipt);
      if (current()) { setPublished(true); setPublishConsent(false); }
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t('The encrypted response could not be published.')); }
    finally { if (current()) setBusy(false); }
  }
  return <section className="fc-app-platform" aria-label={t('Review installed app command')}>
    <h3>{command.app_name} · /{command.name}</h3><p>{command.description}</p>
    {error && <p className="fc-alert" role="alert"><AlertCircle size={16} />{error}</p>}
    {job && <p role="status"><CheckCircle2 size={16} />{job.foreground ? t(job.status === 'delivered' ? 'The recipient acknowledged this foreground action.' : 'Inspect the recipient: this action may already have been received.') : t('Command queued for delivery.')} <code>{job.id}</code></p>}
    {job?.foreground && <aside><p>{job.failure}</p>{job.preview && <><h4>{t('Private response preview')}</h4><pre>{job.preview}</pre><label className="fc-app-check"><input type="checkbox" checked={publishConsent} disabled={busy || published} onChange={event => setPublishConsent(event.target.checked)} />{t('I reviewed this response and want to publish it encrypted to this channel.')}</label><button type="button" className="fc-button" disabled={busy || !publishConsent || published} onClick={() => void publish()}>{t(published ? 'Encrypted response published' : 'Publish encrypted response')}</button></>}{!outcomeAcknowledged && <button type="button" className="fc-button" disabled={busy} onClick={() => setOutcomeAcknowledged(true)}>{t('I acknowledge the delivery outcome. Do not retry this action.')}</button>}</aside>}
    <form onSubmit={event => void invoke(event)}><fieldset disabled={busy || !ready || !!job}>
      {command.arguments.map(argument => <label key={argument.name}>{argument.label}
        {argument.type === 'choice' || argument.type === 'boolean' ? <select required={argument.required} value={arguments_[argument.name] || ''} onChange={event => change(argument.name, event.target.value)}>
          <option value="">{t('Choose a value')}</option>{(argument.type === 'boolean' ? ['true', 'false'] : argument.choices || []).map(value => <option value={value} key={value}>{value}</option>)}
        </select> : <CollaborationInput as="textarea" aria-label={argument.label} toolbar allowReferences={false} channelId={channelId} contextId={`${writingId}:${command.key}:${argument.name}`} submissionScope={submissionScope} onPaletteActiveChange={active => { if (active) activePalettes.current.add(argument.name); else activePalettes.current.delete(argument.name); }} rows={argument.max_length > 500 ? 3 : 1} inputMode={argument.type === 'number' ? 'decimal' : undefined} maxLength={argument.max_length} required={argument.required} value={arguments_[argument.name] || ''} onValueChange={value => change(argument.name, value)} />}
      </label>)}
      <p><strong>{t('Recipient')}: </strong><code>{command.origin}</code> · {command.app_name} · {t('Version')} {command.generation}</p>
      <p>{t('Only these arguments are shared. Channel history and unrelated documents are not included. The external app may retain this information and perform the requested action.')}</p>
      {foreground && <p>{t('These device-selected arguments leave end-to-end encryption for this exact recipient. FreeCollab relays them in memory and retains only a content-free delivery receipt. No history is decrypted on the server and no response is published automatically.')}</p>}
      {attempted && !job && <p>{t('Retrying uses the same request identifier. If you changed the arguments after a failed response, open a new reviewed command instead of duplicating an uncertain action.')}</p>}
      <label className="fc-app-check"><input type="checkbox" required checked={approved} onChange={event => setApproved(event.target.checked)} />{t('I reviewed these exact arguments and approve sharing them with this installed app.')}</label>
      {foreground && <label className="fc-app-check"><input type="checkbox" required checked={decryptedConsent} onChange={event => setDecryptedConsent(event.target.checked)} />{t('For this request, I approve these exact arguments leaving end-to-end encryption.')}</label>}
      <button type="submit" data-collaboration-submit={submissionScope} className="fc-button fc-button-primary" disabled={!approved || foreground && !decryptedConsent || !!job}><Send size={15} />{t('Approve and run command')}</button>
    </fieldset></form>{onClose && <button type="button" className="fc-button" disabled={busy} onClick={onClose}>{t('Close')}</button>}
  </section>;
}
