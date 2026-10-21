import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, ArrowUpRight, Building2, CheckCircle2, Copy, Link2, Loader2, RefreshCw, Unlink } from 'lucide-react';
import { api } from '../lib/api';
import { accountApi, type AccountSecurity } from '../lib/account-api';
import { followOIDCAuthorization, oidcApi, type OIDCIdentity, type OIDCProvider } from '../lib/integrations-api';
import '../styles/integrations.css';
import { ScopedSessionNotice } from './ScopedSessionNotice';

export function SSOConnections({ security: initialSecurity, workspaceId, scopedSession = false, managedAccount = false }: { security?: AccountSecurity; workspaceId?: string; scopedSession?: boolean; managedAccount?: boolean }) {
  useI18n();
  const [providers, setProviders] = useState<OIDCProvider[]>([]);
  const [identities, setIdentities] = useState<OIDCIdentity[]>([]);
  const [security, setSecurity] = useState<AccountSecurity | null>(initialSecurity || null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [linking, setLinking] = useState(false);
  const [removing, setRemoving] = useState<OIDCIdentity | null>(null);
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [copied, setCopied] = useState(false);
  const identity = useRef(api.getCurrentUserId());
  const session = useRef(api.getAccessToken());
  const mounted = useRef(true);
  const passwordInput = useRef<HTMLInputElement>(null);
  const current = () => mounted.current && identity.current === api.getCurrentUserId() && session.current === api.getAccessToken();
  useEffect(() => { mounted.current = true; if (!scopedSession) void load(); return () => { mounted.current = false; }; }, [workspaceId, scopedSession]);
  useEffect(() => { if (linking || removing) passwordInput.current?.focus(); }, [linking, removing]);
  async function load() {
    setLoading(true); setError('');
    try {
      const [available, linked, state] = await Promise.all([oidcApi.providers(workspaceId ? { workspaceId } : undefined), oidcApi.identities(), initialSecurity ? Promise.resolve(initialSecurity) : accountApi.security()]);
      if (current()) { setProviders(available.providers); setIdentities(workspaceId ? linked.filter(value => value.workspace_id === workspaceId) : linked); setSecurity(state); }
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t("Single sign-on settings could not be loaded.")); }
    finally { if (current()) setLoading(false); }
  }
  async function link(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError('');
    try {
      if (removing) {
        await oidcApi.unlink(removing.id, password, code.trim() || undefined);
        if (current()) { api.logout(); window.dispatchEvent(new CustomEvent('freecollab:signed-out', { detail: { notice: 'Organization sign-in removed. All sessions signed out. Continue with your FreeCollab password.' } })); }
      } else {
        const response = await oidcApi.link(password, code.trim() || undefined, workspaceId); if (current()) { setPassword(''); setCode(''); followOIDCAuthorization(response.authorization_url); }
      }
    }
    catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t("Single sign-on could not be linked.")); }
    finally { if (current()) setBusy(false); }
  }
  if (scopedSession) return <ScopedSessionNotice managed={managedAccount} />;
  const linked = providers.some(provider => identities.some(identity => identity.issuer === provider.issuer && (identity.workspace_id || null) === (provider.workspace_id || null)));
  return <section className="fc-account-section fc-integration-sso" aria-labelledby="sso-connections-title">
    <div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Your organization")}</span><h3 id="sso-connections-title">{t("Single sign-on")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh single sign-on connections")} disabled={loading || busy} onClick={() => void load()}><RefreshCw size={16} /></button></div>
    <p className="fc-account-muted">{t("Link your organization’s identity to this account. Your FreeCollab password and trusted encryption devices remain separate.")}</p>
    {error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}
    {loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading sign-in connections…")}</p> : <>
      {identities.length > 0 && <ul className="fc-integration-identity-list">{identities.map(identity => <li key={identity.id}><Building2 size={19} /><div><strong>{identity.provider}</strong><span>{identity.issuer}</span><button type="button" className="fc-account-link fc-account-danger" disabled={busy || !security?.email_verified} aria-label={t("Remove {value0} sign-in connection", { value0: String(identity.provider) })} onClick={() => { setRemoving(identity); setLinking(false); setPassword(''); setCode(''); setError(''); }}><Unlink size={13} />{t("Remove connection")}</button></div><span className="fc-badge fc-account-success"><CheckCircle2 size={12} />{t("Linked")}</span></li>)}</ul>}
      {!providers.length ? <p className="fc-account-muted">{workspaceId ? t("Your workspace administrator has not enabled organization sign-in yet.") : t("Each workspace manages its own organization sign-in in Integrations → Sign-in.")} {t(" Your FreeCollab password remains available.")}</p> : linked ? <p className="fc-account-muted">{t("Use your workspace’s sign-in link the next time you sign in.")}</p> : <>
        <p className="fc-account-muted">{t("Available provider: ")}{providers[0].name}</p>
        {!security?.email_verified && <p className="fc-account-muted">{t("Verify your FreeCollab email in Account settings before linking an identity.")}</p>}
        {!linking ? <button type="button" className="fc-button" disabled={!security?.email_verified || busy} onClick={() => { setLinking(true); setRemoving(null); setError(''); }}><Link2 size={15} />{t("Link organization sign-in")}</button> : <form className="fc-account-reauth" onSubmit={link} aria-label={t("Link organization sign-in")}>
          <h3>{t("Link ")}{providers[0].name}</h3><p>{t("Confirm this FreeCollab account, then approve sign-in with your identity provider.")}</p>
          <label htmlFor="sso-link-password">{t("Confirm with your FreeCollab password")}</label><input ref={passwordInput} id="sso-link-password" className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy} required />
          {security?.mfa_enabled && <><label htmlFor="sso-link-code">{t("Authentication or recovery code")}</label><input id="sso-link-code" className="fc-input" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} disabled={busy} required /></>}
          <div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy} onClick={() => { setLinking(false); setPassword(''); setCode(''); }}>{t("Cancel")}</button><button type="submit" className="fc-button fc-button-primary" disabled={busy}>{busy ? <Loader2 size={15} className="fc-account-spinner" /> : <ArrowUpRight size={15} />}{t("Continue to organization")}</button></div>
        </form>}
      </>}
      {workspaceId && providers[0]?.login_id && <button type="button" className="fc-account-link" onClick={() => { const url = new URL(window.location.pathname, window.location.origin); url.hash = 'workspace-sso=' + encodeURIComponent(providers[0].login_id!); void navigator.clipboard.writeText(url.href).then(() => { if (current()) setCopied(true); }).catch(() => { if (current()) setError(t("The sign-in link could not be copied. Ask your workspace administrator for the link.")); }); }}><Copy size={14} />{copied ? t("Workspace sign-in link copied") : t("Copy workspace sign-in link")}</button>}
      {removing && <form className="fc-account-reauth" onSubmit={link} aria-label={t("Remove organization sign-in")}><h3>{t("Remove ")}{removing.provider}?</h3><p>{t("This disconnects the organization identity and signs out every FreeCollab session. Continue with your FreeCollab password afterwards.")}</p><label htmlFor="sso-remove-password">{t("Confirm with your FreeCollab password")}</label><input ref={passwordInput} id="sso-remove-password" className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy} required />{security?.mfa_enabled && <><label htmlFor="sso-remove-code">{t("Authentication or recovery code")}</label><input id="sso-remove-code" className="fc-input" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} disabled={busy} required /></>}<div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy} onClick={() => { setRemoving(null); setPassword(''); setCode(''); }}>{t("Keep connection")}</button><button type="submit" className="fc-button" disabled={busy}>{busy ? <Loader2 size={15} className="fc-account-spinner" /> : <Unlink size={15} />}{t("Remove connection and sign out")}</button></div></form>}
      <p className="fc-account-muted fc-integration-footnote">{t("Linking requires an existing, verified FreeCollab account and membership in this workspace. Organization sign-in does not recover encrypted history.")}</p>
    </>}
  </section>;
}
