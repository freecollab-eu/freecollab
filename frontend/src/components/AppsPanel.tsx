import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Blocks, Check, CheckCircle2, KeyRound, Loader2, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { appPlatformApi, type AppJob, type AppManifest, type AppRecipe, type InstalledApp } from '../lib/app-platform-api';
import { t, useI18n } from '../i18n';
import '../styles/app-platform.css';

interface Props { workspaceId: string; channels: Channel[]; onGuardChange?: (guard: 'busy' | 'secret' | null) => void }
export function AppsPanel({ workspaceId, channels, onGuardChange }: Props) {
  useI18n();
  const [recipes, setRecipes] = useState<AppRecipe[]>([]), [apps, setApps] = useState<InstalledApp[]>([]);
  const [recipe, setRecipe] = useState<AppRecipe | null>(null), [editing, setEditing] = useState<InstalledApp | null>(null);
  const [manifest, setManifest] = useState(''), [endpoint, setEndpoint] = useState(''), [authorization, setAuthorization] = useState('');
  const [selected, setSelected] = useState<string[]>([]), [reviewed, setReviewed] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [worker, setWorker] = useState(false);
  const [secret, setSecret] = useState<{ signing_secret: string; callback_path: string } | null>(null);
  const [jobApp, setJobApp] = useState(''), [jobs, setJobs] = useState<AppJob[]>([]), [retry, setRetry] = useState(''), [revoking, setRevoking] = useState('');
  const mounted = useRef(true), owner = useRef(api.getAccessToken()), identity = useRef(api.getCurrentUserId()), serial = useRef(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const current = (version = serial.current) => mounted.current && version === serial.current && owner.current === api.getAccessToken() && identity.current === api.getCurrentUserId();
  const nativeChannels = channels.filter(channel => !channel.secure && !['direct', 'group_direct'].includes(channel.type) && channel.role === 'admin');
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; serial.current++; }; }, [workspaceId]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { onGuardChange?.(busy ? 'busy' : secret ? 'secret' : null); }, [busy, secret, onGuardChange]);
  async function load() {
    const version = ++serial.current; setError('');
    const results = await Promise.allSettled([appPlatformApi.catalog(workspaceId), appPlatformApi.list(workspaceId)]);
    if (!current(version)) return;
    if (results[0].status === 'fulfilled') setRecipes(results[0].value.recipes);
    if (results[1].status === 'fulfilled') { setApps(results[1].value.installations); setWorker(results[1].value.worker_ready); }
    for (const result of results) if (result.status === 'rejected') setError(result.reason instanceof Error ? result.reason.message : t('Apps could not be loaded.'));
  }
  async function action(run: () => Promise<void>) {
    if (busy || secret) return;
    const version = serial.current; setBusy(true); setError(''); setNotice('');
    try { await run(); } catch (failure) { if (current(version)) setError(failure instanceof Error ? failure.message : t('The app operation failed.')); }
    finally { if (current()) setBusy(false); }
  }
  function choose(next: AppRecipe | null, installed: InstalledApp | null = null) {
    if (busy || secret) return;
    setRecipe(next); setEditing(installed); setManifest(JSON.stringify(installed?.manifest || next?.manifest || { schema_version: 1, app_id: 'yourcompany.app', name: 'Your app', version: '1.0.0', description: 'Your customer-owned signed integration.', scopes: ['commands:invoke', 'cards:write'], commands: [{ name: 'invoke', description: 'Run an explicitly reviewed request', protocol: 'signed-webhook', arguments: [{ name: 'text', label: 'Text', required: true, max_length: 2000 }] }] }, null, 2));
    setEndpoint(''); setAuthorization(''); setSelected(installed?.channel_ids || []); setReviewed(false); setError('');
  }
  async function install() {
    const value: AppManifest = JSON.parse(manifest);
    const result = await appPlatformApi.install(workspaceId, { manifest: value, channel_ids: selected, endpoint, authorization, expected_generation: editing?.generation || 0 }, editing?.id);
    if (!current()) return;
    setAuthorization(''); setSecret(result); setNotice(t('Installed with the exact scopes and channels below. Store this one-time signing secret.'));
    await load();
  }
  return <section className="fc-app-platform" aria-label={t('Workspace apps')}>
    <div className="fc-app-platform-intro"><Blocks size={24} /><div><h3>{t('Your tools, deliberately connected.')}</h3><p>{t('Install a signed app or choose a working provider recipe. Your organization owns the endpoint, credentials and access.')}</p></div></div>
    {error && <div ref={errorRef} role="alert" tabIndex={-1} className="fc-alert fc-account-error"><AlertCircle size={17} /><p>{error}</p></div>}
    {notice && <p role="status" className="fc-alert"><CheckCircle2 size={16} />{notice}</p>}
    <p className="fc-app-platform-status"><span className={worker ? 'fc-badge' : 'fc-account-muted'}>{worker ? t('Worker available') : t('Waiting for integration worker')}</span>{t('Installation is not a provider connection test. Delivery history shows actual receiver responses.')}</p>
    {secret ? <section className="fc-app-secret" aria-label={t('One-time app signing secret')}><h4><KeyRound size={18} />{t('Store your signing secret')}</h4><p>{t('Use this installation secret to verify outgoing requests and sign card callbacks. It is never a FreeCollab account token.')}</p><label>{t('Signing secret')}<input readOnly type="text" value={secret.signing_secret} autoComplete="off" /></label><label>{t('Callback path')}<input readOnly value={secret.callback_path} /></label><button type="button" className="fc-button fc-button-primary" onClick={() => { setSecret(null); setManifest(''); setRecipe(null); setEditing(null); setNotice(t('Signing secret cleared from this panel.')); }}><Check size={15} />{t('I stored the secret securely')}</button></section> : <>
      <div className="fc-app-recipe-grid">{recipes.map(item => <button type="button" key={item.id} className="fc-app-recipe" disabled={busy} aria-pressed={recipe?.id === item.id} onClick={() => choose(item)}><span className="fc-badge">{t('Setup recipe')}</span><strong>{item.manifest.name}</strong><p>{t(item.manifest.description)}</p><span>{t('Review and configure')} →</span></button>)}<button type="button" className="fc-app-recipe" disabled={busy} onClick={() => choose(null)}><span className="fc-badge">{t('Developer app')}</span><strong>{t('Bring an app manifest')}</strong><p>{t('Declare exact commands, argument schemas and scopes. No scripts or administrative bot tokens.')}</p></button></div>
      {manifest && <form className="fc-app-install-form" onSubmit={event => { event.preventDefault(); void action(install); }}><h4>{editing ? t('Review a new app generation') : t('Review installation')}</h4>{recipe && <p>{t(recipe.setup)}</p>}
        <fieldset disabled={busy}><label>{t('Customer-owned HTTPS endpoint')}<input required type="url" maxLength={2048} value={endpoint} placeholder={recipe?.endpoint_example || 'https://your-app.example.com/freecollab'} onChange={event => { setEndpoint(event.target.value); setAuthorization(''); setReviewed(false); }} /></label><label>{t('Your provider authorization, if required')}<input type="password" autoComplete="new-password" value={authorization} maxLength={4096} placeholder="Bearer … / Basic …" onChange={event => setAuthorization(event.target.value)} /></label><p>{t('Credentials stay encrypted on your server. A changed endpoint requires you to enter credentials again.')}</p>
        <details><summary>{t('Review app manifest and exact scopes')}</summary><label>{t('App manifest JSON')}<textarea rows={12} value={manifest} maxLength={16000} onChange={event => { setManifest(event.target.value); setReviewed(false); }} spellCheck={false} /></label></details>
        <fieldset><legend>{t('Granted native channels')}</legend>{nativeChannels.length ? nativeChannels.map(channel => <label className="fc-app-check" key={channel.id}><input type="checkbox" checked={selected.includes(channel.id)} onChange={event => { setSelected(previous => event.target.checked ? [...previous, channel.id] : previous.filter(value => value !== channel.id)); setReviewed(false); }} />{channel.name}</label>) : <p>{t('You must administer a native workspace channel to install an app.')}</p>}</fieldset>
        <label className="fc-app-check"><input type="checkbox" required checked={reviewed} onChange={event => setReviewed(event.target.checked)} />{t('I reviewed this manifest, endpoint, exact scopes and channel grants. Only explicitly invoked arguments may leave FreeCollab.')}</label><button className="fc-button fc-button-primary" disabled={!reviewed || !selected.length || !endpoint}>{busy ? <Loader2 size={15} className="fc-account-spinner" /> : <ShieldCheck size={15} />}{editing ? t('Grant new generation and rotate secret') : t('Install with these grants')}</button><button type="button" className="fc-button" onClick={() => setManifest('')}>{t('Cancel')}</button></fieldset></form>}
      <section><div className="fc-app-section-title"><h4>{t('Installed apps')}</h4><button type="button" className="fc-button" disabled={busy} onClick={() => void load()}><RefreshCw size={14} />{t('Refresh')}</button></div>{!apps.length && <p>{t('No apps installed. Choosing a recipe does not connect to a provider.')}</p>}{apps.map(app => <article key={app.id} className="fc-installed-app"><div><strong>{app.manifest.name}</strong><span className="fc-badge">v{app.manifest.version} · {t('Generation')} {app.generation}</span><p>{app.origin}</p><p><code>{app.bot_id}</code></p><p>{app.manifest.scopes.join(' · ')}</p><small>{app.channel_ids.map(id => channels.find(channel => channel.id === id)?.name || id).join(', ')}</small></div><div className="fc-app-actions">{app.revoked_at ? <span>{t('Revoked')}</span> : <><button type="button" className="fc-button" disabled={busy} onClick={() => choose(null, app)}>{t('New generation')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => setRevoking(app.id)}><Trash2 size={14} />{t('Revoke')}</button></>}<button type="button" className="fc-button" disabled={busy} onClick={() => void action(async () => { const result = await appPlatformApi.jobs(workspaceId, app.id); if (current()) { setJobs(result.jobs); setJobApp(app.id); setRetry(''); } })}>{t('Delivery history')}</button></div>{revoking === app.id && <div className="fc-app-confirm"><p>{t('Revocation removes this bot’s signing secret and cancels queued requests immediately.')}</p><button type="button" className="fc-button" disabled={busy} onClick={() => void action(async () => { await appPlatformApi.revoke(workspaceId, app.id); if (current()) { setRevoking(''); await load(); } })}>{t('Confirm revocation')}</button><button type="button" className="fc-button" onClick={() => setRevoking('')}>{t('Cancel')}</button></div>}</article>)}</section>
      {jobApp && <section aria-label={t('Delivery history')}><h4>{t('Delivery history')}</h4>{!jobs.length && <p>{t('No requests have been sent to this app.')}</p>}{jobs.map(job => <article key={job.id} className="fc-app-job"><code>/{job.command}</code><strong>{t(job.status)}</strong><span>{t('Attempts')}: {job.attempts}{job.http_status ? ` · HTTP ${job.http_status}` : ''}</span>{job.failure && <p>{t(job.failure)}</p>}{['failed', 'uncertain'].includes(job.status) && <button type="button" className="fc-button" disabled={busy} onClick={() => setRetry(job.id)}>{t('Review retry')}</button>}{retry === job.id && <div><p>{t('First inspect the receiving system. Retrying a processed provider write can create a duplicate.')}</p><button type="button" className="fc-button" disabled={busy} onClick={() => void action(async () => { await appPlatformApi.retry(workspaceId, jobApp, job.id); const result = await appPlatformApi.jobs(workspaceId, jobApp); if (current()) { setJobs(result.jobs); setRetry(''); } })}>{t('I verified it was not processed; retry')}</button></div>}</article>)}</section>}
    </>}
    <p className="fc-app-privacy"><ShieldCheck size={17} />{t('Encrypted channels stay on trusted devices. Apps cannot read channel history, fetch URLs, execute card code or impersonate a person.')}</p>
  </section>;
}
