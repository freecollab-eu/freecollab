import { useEffect, useRef, useState } from 'react';
import { Blocks, Check, KeyRound, Search, ShieldCheck } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api, type Channel } from '../lib/api';
import { integrationLibraryApi, type IntegrationRelease } from '../lib/integration-library-api';
import '../styles/app-platform.css';
import '../styles/product-expansion.css';

export function IntegrationLibraryPanel({ workspaceId, channels, onGuardChange }: { workspaceId: string; channels: Channel[]; onGuardChange?: (guard: 'busy' | 'secret' | null) => void }) {
  useI18n();
  const [releases, setReleases] = useState<IntegrationRelease[]>([]), [own, setOwn] = useState<IntegrationRelease[]>([]), [queue, setQueue] = useState<IntegrationRelease[]>([]);
  const [operator, setOperator] = useState(false), [query, setQuery] = useState(''), [release, setRelease] = useState<IntegrationRelease | null>(null);
  const [endpoint, setEndpoint] = useState(''), [authorization, setAuthorization] = useState(''), [selected, setSelected] = useState<string[]>([]), [reviewed, setReviewed] = useState(false);
  const [packageText, setPackageText] = useState(''), [publishConsent, setPublishConsent] = useState(false), [secret, setSecret] = useState<{ signing_secret: string; callback_path: string } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const mounted = useRef(true), owner = useRef(api.getAccessToken()), identity = useRef(workspaceId);
  const loadGeneration = useRef(0), actionGeneration = useRef(0), actionBusy = useRef(false);
  const current = (revision?: number) => mounted.current && identity.current === workspaceId && owner.current === api.getAccessToken()
    && (revision === undefined || revision === loadGeneration.current);
  const native = channels.filter(channel => !channel.secure && !['direct', 'group_direct'].includes(channel.type) && channel.role === 'admin');
  useEffect(() => {
    mounted.current = true; owner.current = api.getAccessToken(); identity.current = workspaceId;
    setReleases([]); setOwn([]); setQueue([]); setOperator(false); setRelease(null); setAuthorization(''); setSecret(null); setBusy(false);
    void load();
    return () => { mounted.current = false; loadGeneration.current++; actionGeneration.current++; actionBusy.current = false; };
  }, [workspaceId]);
  useEffect(() => { onGuardChange?.(busy ? 'busy' : secret ? 'secret' : null); }, [busy, secret, onGuardChange]);
  async function load() {
    const revision = ++loadGeneration.current;
    try {
      const results = await Promise.allSettled([integrationLibraryApi.catalog(), integrationLibraryApi.own(workspaceId), api.request<{ is_admin: boolean }>('/admin/me')]);
      if (!current(revision)) return;
      if (results[0].status === 'fulfilled') setReleases(results[0].value.releases);
      if (results[1].status === 'fulfilled') setOwn(results[1].value.releases);
      for (const result of results.slice(0, 2)) if (result.status === 'rejected') setError(result.reason instanceof Error ? result.reason.message : t('The integration library could not be loaded.'));
      if (results[2].status === 'fulfilled' && results[2].value.is_admin) {
        setOperator(true);
        const data = await integrationLibraryApi.reviewQueue();
        if (current(revision)) setQueue(data.releases);
      } else { setOperator(false); setQueue([]); }
    } catch (failure) {
      // The initial effect also calls load. Queue/network failures must be
      // visible errors, never an unhandled rejection outside an action.
      if (current(revision)) { setQueue([]); setError(failure instanceof Error ? failure.message : t('The integration library could not be loaded.')); }
    }
  }
  async function run(action: () => Promise<void>) {
    if (actionBusy.current || secret) return;
    const revision = ++actionGeneration.current; actionBusy.current = true;
    setBusy(true); setError(''); setNotice('');
    try { await action(); }
    catch (failure) { if (current() && revision === actionGeneration.current) setError(failure instanceof Error ? failure.message : t('The library operation failed.')); }
    finally {
      // An action may perform its own load. Read generations must not prevent
      // its busy state and the parent navigation guard from being released.
      if (revision === actionGeneration.current) { actionBusy.current = false; if (current()) setBusy(false); }
    }
  }
  const visible = releases.filter(item => (item.app_id + ' ' + item.package.manifest.name + ' ' + item.package.manifest.description).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <section className="fc-app-platform fc-product-expansion" aria-label={t('Integration library')}>
    <div className="fc-app-platform-intro"><Blocks size={25} /><div><span className="fc-kicker">{t('Made to work together')}</span><h3>{t('A library you can inspect.')}</h3><p>{t('Versioned, declarative integrations. Review their source, scopes and contract checks before granting access in your workspace.')}</p></div></div>
    {error && <p role="alert" className="fc-alert fc-account-error">{error}</p>}{notice && <p role="status" className="fc-alert">{notice}</p>}
    {secret ? <section className="fc-app-secret"><h4><KeyRound size={18} />{t('Store your signing secret')}</h4><label>{t('Signing secret')}<input readOnly value={secret.signing_secret} autoComplete="off" /></label><label>{t('Callback path')}<input readOnly value={secret.callback_path} /></label><button className="fc-button fc-button-primary" onClick={() => { setSecret(null); setRelease(null); }}><Check size={16} />{t('I stored the secret securely')}</button></section> : <>
      <label><span><Search size={15} /> {t('Find an integration')}</span><input value={query} maxLength={100} onChange={event => setQuery(event.target.value)} placeholder={t('Search names, descriptions or app identifiers')} /></label>
      <div className="fc-app-recipe-grid">{visible.map(item => <button key={item.id} type="button" className="fc-app-recipe" disabled={busy} aria-pressed={release?.id === item.id} onClick={() => { setRelease(item); setEndpoint(''); setAuthorization(''); setSelected([]); setReviewed(false); }}><span className="fc-badge">{item.status === 'bundled' ? t('Bundled recipe') : t('Community reviewed')} · v{item.version}</span><strong>{item.package.manifest.name}</strong><p>{item.package.manifest.description}</p><small>{item.package.publisher} · {item.package.license}</small><span>{t('Inspect this version')} →</span></button>)}</div>
      {!visible.length && <p>{t('No integrations match this search.')}</p>}
      {release && <form className="fc-app-install-form" onSubmit={event => { event.preventDefault(); void run(async () => { const value = await integrationLibraryApi.install(workspaceId, release.id, { package_digest: release.package_digest, endpoint, authorization, channel_ids: selected, confirm_scopes: reviewed }); if (current()) { setAuthorization(''); setSecret(value); } }); }}><h4>{release.package.manifest.name} · v{release.version}</h4><p>{release.package.setup}</p><a href={release.package.source_url} target="_blank" rel="noopener noreferrer">{t('Inspect source')}</a><p>{t('Verified checks')}: {release.checks.join(' · ')}</p><p>{t('Contract checks validate declared request shapes. They do not certify a publisher or prove a live provider connection.')}</p><code className="fc-expansion-digest">SHA-256 {release.package_digest}</code>
        <details><summary>{t('Manifest and contract fixtures')}</summary><pre>{JSON.stringify(release.package, null, 2)}</pre></details>
        <fieldset disabled={busy}><label>{t('Customer-owned HTTPS endpoint')}<input required type="url" value={endpoint} maxLength={2048} placeholder={release.package.endpoint_example} onChange={event => { setEndpoint(event.target.value); setAuthorization(''); setReviewed(false); }} /></label><label>{t('Your provider authorization, if required')}<input type="password" value={authorization} autoComplete="new-password" maxLength={4096} onChange={event => setAuthorization(event.target.value)} /></label><fieldset><legend>{t('Granted native channels')}</legend>{native.map(channel => <label key={channel.id} className="fc-app-check"><input type="checkbox" checked={selected.includes(channel.id)} onChange={event => { setSelected(previous => event.target.checked ? [...previous, channel.id] : previous.filter(id => id !== channel.id)); setReviewed(false); }} />{channel.name}</label>)}{!native.length && <p>{t('You must administer a native workspace channel to install an app.')}</p>}</fieldset>
        <p>{t('Requested scopes')}: {release.package.manifest.scopes.join(' · ')}</p><label className="fc-app-check"><input type="checkbox" required checked={reviewed} onChange={event => setReviewed(event.target.checked)} />{t('I reviewed this exact version, source, endpoint, scopes and channel grants.')}</label><button className="fc-button fc-button-primary" disabled={!reviewed || !selected.length || !endpoint}><ShieldCheck size={16} />{t('Install this exact version')}</button><button type="button" className="fc-button" onClick={() => { setAuthorization(''); setRelease(null); }}>{t('Cancel')}</button></fieldset>
      </form>}
      <details><summary>{t('Publish a community integration')}</summary><p>{t('Submit a declarative package with one passing contract fixture per command. An instance administrator reviews it before public listing. Package contents become public after approval; never include credentials or private URLs.')}</p><form onSubmit={event => { event.preventDefault(); void run(async () => { await integrationLibraryApi.submit(workspaceId, JSON.parse(packageText)); if (current()) { setPackageText(''); setPublishConsent(false); setNotice(t('Version submitted for administrator review.')); await load(); } }); }}><fieldset disabled={busy}><label>{t('Integration package JSON')}<textarea rows={12} required maxLength={64000} value={packageText} onChange={event => { setPackageText(event.target.value); setPublishConsent(false); }} spellCheck={false} /></label><label className="fc-app-check"><input required type="checkbox" checked={publishConsent} onChange={event => setPublishConsent(event.target.checked)} />{t('I can publish this source and license, and the package contains no secrets or private information.')}</label><button className="fc-button" disabled={!publishConsent || !packageText}>{t('Submit version for review')}</button></fieldset></form></details>
      {!!own.length && <section><h4>{t('Your submitted versions')}</h4>{own.map(item => <p key={item.id}>{item.app_id} · v{item.version} · {t(item.status)}</p>)}</section>}
      {operator && <details><summary>{t('Administrator review queue')}</summary>{queue.map(item => <article className="fc-expansion-review" key={item.id}><strong>{item.app_id} · v{item.version}</strong><p>{t(item.status)} · {item.package.publisher}</p><code className="fc-expansion-digest">{item.package_digest}</code><details><summary>{t('Manifest and contract fixtures')}</summary><pre>{JSON.stringify(item.package, null, 2)}</pre></details><div className="fc-app-actions"><button className="fc-button" disabled={busy || item.status === 'approved'} onClick={() => void run(async () => { await integrationLibraryApi.review(item.id, item.package_digest, true); await load(); })}>{t('Approve public listing')}</button><button className="fc-button" disabled={busy || item.status === 'withdrawn'} onClick={() => void run(async () => { await integrationLibraryApi.review(item.id, item.package_digest, false); await load(); })}>{t('Withdraw this version')}</button></div></article>)}</details>}
    </>}
    <p className="fc-app-privacy"><ShieldCheck size={17} />{t('Installing a version grants only its declared commands and selected native channels. Updates require a new review. Withdrawing a listing prevents new installs; existing installations remain under their workspace administrator’s control.')}</p>
  </section>;
}
