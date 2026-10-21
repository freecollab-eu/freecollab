import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, CheckCircle2, Code2, Copy, Loader2, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api, type Channel } from '../lib/api';
import type { AccountSecurity } from '../lib/account-api';
import { workspaceSSOApi } from '../lib/integrations-api';

interface Application { application_id: string; workspace_id: string; name: string; parent_origin: string; enabled: boolean; revision: number }
interface Props { workspaceId: string; channels: Channel[]; security: AccountSecurity | null; onGuardChange: (value: 'busy' | null) => void }
export function EmbedApplicationsSettings({ workspaceId, channels, security, onGuardChange }: Props) {
  useI18n();
  const [applications, setApplications] = useState<Application[]>([]), [selected, setSelected] = useState(''), [name, setName] = useState(''), [origin, setOrigin] = useState('');
  const [enabled, setEnabled] = useState(true), [password, setPassword] = useState(''), [factor, setFactor] = useState(''), [channel, setChannel] = useState(channels[0]?.id || '');
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState(''), [sso, setSSO] = useState(false);
  const mounted = useRef(true), owner = useRef(api.getAccessToken()), workspace = useRef(workspaceId), sequence = useRef(0), guard = useRef(onGuardChange); guard.current = onGuardChange;
  const current = () => mounted.current && owner.current === api.getAccessToken() && workspace.current === workspaceId;
  const base = '/workspaces/' + encodeURIComponent(workspaceId) + '/embed/applications';
  useEffect(() => { mounted.current = true; workspace.current = workspaceId; void load(); return () => { mounted.current = false; sequence.current++; guard.current(null); }; }, [workspaceId]);
  async function load() {
    const request = ++sequence.current; setLoading(true); setError('');
    const [apps, identity] = await Promise.allSettled([api.request<{ applications: Application[] }>(base), workspaceSSOApi.get(workspaceId)]);
    if (!current() || request !== sequence.current) return;
    if (apps.status === 'fulfilled') setApplications(apps.value.applications);
    else setError('Embedded applications could not be loaded.');
    setSSO(identity.status === 'fulfilled' && !!identity.value.provider?.enabled); setLoading(false);
  }
  function edit(app?: Application) {
    setSelected(app?.application_id || ''); setName(app?.name || ''); setOrigin(app?.parent_origin || ''); setEnabled(app?.enabled ?? true);
    setPassword(''); setFactor(''); setError(''); setNotice('');
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy || !current()) return;
    let exact: string;
    try {
      const url = new URL(origin.trim());
      if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) throw new Error();
      exact = url.origin;
      if (exact === window.location.origin) throw new Error();
    } catch { setError('Enter an exact HTTPS application origin without a path or wildcard.'); return; }
    setBusy(true); guard.current('busy'); setError(''); setNotice('');
    try {
      const result = await api.request<Application>(base + (selected ? '/' + encodeURIComponent(selected) : ''), { method: selected ? 'PUT' : 'POST',
        body: JSON.stringify({ name: name.trim(), parent_origin: exact, enabled, password, code: factor.trim() || undefined }) });
      if (!current()) return;
      setPassword(''); setFactor(''); setSelected(result.application_id); setOrigin(result.parent_origin); setName(result.name); setEnabled(result.enabled);
      await load(); if (current()) setNotice(result.enabled ? 'Application saved. Copy the integration example for your product.' : 'Application disabled. Its embedding grants and sessions are no longer valid.');
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : 'The embedded application could not be saved.'); }
    finally { if (current()) { setBusy(false); setPassword(''); setFactor(''); guard.current(null); } }
  }
  const active = applications.find(value => value.application_id === selected);
  const selectedChannel = channels.find(value => value.id === channel);
  const example = active && selectedChannel ? `import { createEmbedSignIn } from '@freecollab/components/auth';\nimport { ${selectedChannel.secure ? 'mountSecureChat' : 'mountWorkspace'} } from '@freecollab/components/embed';\n\nconst signIn = createEmbedSignIn({\n  appUrl: ${JSON.stringify(window.location.origin)},\n  applicationId: ${JSON.stringify(active.application_id)},\n  locale: 'sv',\n});\nlet view;\nconnectButton.onclick = () => {\n  signIn.open(); // Call from the user's click.\n  view?.destroy();\n  view = ${selectedChannel.secure ? 'mountSecureChat' : 'mountWorkspace'}(document.querySelector('#collaboration'), {\n    appUrl: ${JSON.stringify(window.location.origin)},\n    applicationId: ${JSON.stringify(active.application_id)},\n    workspaceId: ${JSON.stringify(workspaceId)},\n    channelId: ${JSON.stringify(channel)},\n    locale: 'sv',\n    grantFactory: signIn.grantFactory,\n  });\n};\n// On host logout or account change, clear the old session immediately:\n// view?.destroy(); signIn.destroy();\n` : '';
  async function copy() {
    try { await navigator.clipboard.writeText(example); if (current()) setNotice('Integration example copied.'); }
    catch { if (current()) setError('Copy is unavailable. Select the example and copy it manually.'); }
  }
  return <section className="fc-account-section" aria-label={t('Embedded applications')}>
    <div className="fc-account-section-heading"><div><span className="fc-kicker">{t('Your product · your design')}</span><h3>{t('Embedded applications')}</h3></div><button type="button" className="fc-icon-button" aria-label={t('Refresh embedded applications')} disabled={busy || loading} onClick={() => void load()}><RefreshCw size={16} /></button></div>
    <p className="fc-account-muted">{t('Register the exact origin of your product. Your users sign in through this workspace’s identity provider. Encrypted content and keys stay in the trusted FreeCollab frame.')}</p>
    {!sso && !loading && <p role="status" className="fc-alert">{t('Enable this workspace’s organization sign-in before connecting an embedded application.')}</p>}
    {error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{t(error)}</p></div>}
    {notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{t(notice)}</p></div>}
    {loading ? <p role="status"><Loader2 size={16} />{t('Loading…')}</p> : <>
      <div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy} onClick={() => edit()}><Plus size={15} />{t('New application')}</button>{applications.map(app => <button key={app.application_id} type="button" className="fc-button" aria-pressed={selected === app.application_id} disabled={busy} onClick={() => edit(app)}>{app.name} · {t(app.enabled ? 'Enabled' : 'Disabled')}</button>)}</div>
      <form className="fc-account-form" aria-label={t('Embedding application configuration')} onSubmit={save}>
        <label htmlFor="embed-app-name">{t('Application name')}</label><input id="embed-app-name" className="fc-input" value={name} onChange={event => setName(event.target.value)} required maxLength={80} disabled={busy} />
        <label htmlFor="embed-app-origin">{t('Application origin')}</label><input id="embed-app-origin" className="fc-input" type="url" placeholder="https://app.example.com" value={origin} onChange={event => setOrigin(event.target.value)} required maxLength={2048} disabled={busy} />
        <p className="fc-account-muted">{t('Use one application registration per origin. Saving a change invalidates its existing grants and embedded sessions.')}</p>
        <label><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} disabled={busy} /> {t('Allow embedding from this origin')}</label>
        <label htmlFor="embed-app-password">{t('Confirm with your FreeCollab password')}</label><input id="embed-app-password" className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy} required />
        {security?.mfa_enabled && <><label htmlFor="embed-app-factor">{t('Authentication or recovery code')}</label><input id="embed-app-factor" className="fc-input" autoComplete="one-time-code" value={factor} onChange={event => setFactor(event.target.value)} disabled={busy} required /></>}
        <button type="submit" className="fc-button fc-button-primary" disabled={busy || !name.trim() || !origin.trim() || !password}>{busy ? <Loader2 size={16} className="fc-account-spinner" /> : <ShieldCheck size={16} />}{t(selected ? 'Save application' : 'Register application')}</button>
      </form>
      {active && <div className="fc-account-form"><h4><Code2 size={17} /> {t('Integration example')}</h4><label htmlFor="embed-example-channel">{t('Channel')}</label><select id="embed-example-channel" className="fc-input" value={channel} onChange={event => setChannel(event.target.value)}>{channels.map(item => <option key={item.id} value={item.id}>{item.name || item.id}{item.secure ? ' · E2EE' : ''}</option>)}</select>
        {example ? <><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxWidth: '100%' }}><code>{example}</code></pre><button type="button" className="fc-button" onClick={() => void copy()}><Copy size={15} />{t('Copy integration example')}</button></> : <p>{t('Choose a channel to generate the integration example.')}</p>}
        <p className="fc-account-muted">{t('Install the reviewed SDK artifact in your product. Give its container a height. Host sign-out and account changes must destroy the previous embedded view.')}</p>
      </div>}
    </>}
  </section>;
}
