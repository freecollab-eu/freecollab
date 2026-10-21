import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, Building2, CheckCircle2, Copy, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';
import { api } from '../lib/api';
import type { AccountSecurity } from '../lib/account-api';
import { workspaceSSOApi, type WorkspaceSSO } from '../lib/integrations-api';
import { buildSSOPresetIssuer, defaultSSOScopes, emptySSOPresetFields, ssoDraftIsDirty, ssoPresets, ssoSecretInput, validateSSOIssuer, type SSOPresetFields, type SSOPresetId, type SSOSecretHandling } from '../lib/sso-presets';

type Action = 'save' | 'test' | 'enable' | 'disable';
interface Props { workspaceId: string; security: AccountSecurity | null; onGuardChange: (guard: 'busy' | null) => void; onChanged?: () => void; onDirtyChange?: (dirty: boolean) => void }

export function WorkspaceSSOSettings({ workspaceId, security, onGuardChange, onChanged, onDirtyChange }: Props) {
  useI18n();
  const [settings, setSettings] = useState<WorkspaceSSO | null>(null);
  const [name, setName] = useState('');
  const [issuer, setIssuer] = useState('');
  const [clientId, setClientId] = useState('');
  const [scopes, setScopes] = useState(defaultSSOScopes);
  const [secretHandling, setSecretHandling] = useState<SSOSecretHandling>('replace');
  const [discardSavedSecret, setDiscardSavedSecret] = useState(false);
  const [preset, setPreset] = useState<SSOPresetId>('generic');
  const [presetFields, setPresetFields] = useState<SSOPresetFields>(emptySSOPresetFields);
  const [secret, setSecret] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [action, setAction] = useState<Action | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const owner = useRef(api.getAccessToken());
  const mounted = useRef(true);
  const activeWorkspace = useRef(workspaceId); activeWorkspace.current = workspaceId;
  const loadSequence = useRef(0);
  const callbacks = useRef({ onGuardChange, onDirtyChange }); callbacks.current = { onGuardChange, onDirtyChange };
  const passwordInput = useRef<HTMLInputElement>(null);
  const current = () => mounted.current && activeWorkspace.current === workspaceId && owner.current === api.getAccessToken();
  useEffect(() => { mounted.current = true; setSettings(null); setAction(null); setPassword(''); setCode(''); setSecret(''); void load(); return () => { mounted.current = false; loadSequence.current++; callbacks.current.onGuardChange(null); callbacks.current.onDirtyChange?.(false); }; }, [workspaceId]);
  useEffect(() => { if (action) passwordInput.current?.focus(); }, [action]);
  async function load() {
    const sequence = ++loadSequence.current;
    setLoading(true); setError('');
    try {
      const next = await workspaceSSOApi.get(workspaceId);
      if (!current() || sequence !== loadSequence.current) return;
      setSettings(next); setName(next.provider?.name || ''); setIssuer(next.provider?.issuer_url || '');
      setClientId(next.provider?.client_id || ''); setScopes(next.provider?.scopes || defaultSSOScopes);
      setSecretHandling(next.provider ? (next.provider.has_secret ? 'retain' : 'remove') : 'replace'); setSecret('');
      setDiscardSavedSecret(false); setPreset('generic'); setPresetFields(emptySSOPresetFields());
    } catch (failure) { if (current() && sequence === loadSequence.current) setError(failure instanceof Error ? failure.message : t("Workspace sign-in settings could not be loaded.")); }
    finally { if (current() && sequence === loadSequence.current) setLoading(false); }
  }
  const provider = settings?.provider;
  const dirty = ssoDraftIsDirty(provider, { name, issuer, clientId, scopes, secretHandling, secret, discardSavedSecret });
  const canRetainSecret = !!provider?.has_secret && !discardSavedSecret && issuer === provider.issuer_url && clientId === provider.client_id;
  const meaningfulDraft = !loading && (dirty || preset !== 'generic' || Object.values(presetFields).some(Boolean) || !!action);
  useEffect(() => { callbacks.current.onDirtyChange?.(meaningfulDraft); }, [meaningfulDraft, onDirtyChange]);
  let issuerError = '';
  if (preset !== 'generic') {
    try { buildSSOPresetIssuer(preset, presetFields); }
    catch (failure) { issuerError = failure instanceof Error ? failure.message : 'Check your identity provider details.'; }
  }
  function changeAppIdentity(update: () => void) {
    update(); setSecret(''); setDiscardSavedSecret(true);
    if (secretHandling === 'retain') setSecretHandling('replace');
    setAction(null); setPassword(''); setCode('');
  }
  function selectPreset(next: SSOPresetId) {
    if (next === preset) return;
    setPreset(next); setPresetFields(emptySSOPresetFields()); setName(next === 'generic' ? '' : ssoPresets.find(item => item.id === next)!.name);
    setIssuer(next === 'google' ? buildSSOPresetIssuer(next, emptySSOPresetFields()) : '');
    setClientId(''); setSecret(''); setSecretHandling('replace'); setDiscardSavedSecret(true); setScopes(defaultSSOScopes);
    setAction(null); setPassword(''); setCode(''); setError(''); setNotice('');
  }
  function changePresetField(key: keyof SSOPresetFields, value: string) {
    const fields = { ...presetFields, [key]: value }; setPresetFields(fields);
    changeAppIdentity(() => {
      try { setIssuer(preset === 'generic' ? '' : buildSSOPresetIssuer(preset, fields)); }
      catch { setIssuer(''); }
    });
  }
  function choose(next: Action) {
    if (!current()) return;
    if (next === 'save') {
      try { validateSSOIssuer(issuer.trim()); }
      catch (failure) { setError(failure instanceof Error ? t(failure.message) : t('Check your identity provider details.')); return; }
    }
    setAction(next); setPassword(''); setCode(''); setError(''); setNotice('');
  }
  async function confirm(event: FormEvent) {
    event.preventDefault(); if (!action || busy || !current()) return;
    const selected = action;
    setBusy(true); onGuardChange('busy'); setError(''); setNotice('');
    try {
      const reauth = { password, code: code.trim() || undefined };
      if (selected === 'save') await workspaceSSOApi.save(workspaceId, { ...reauth, name: name.trim(), issuer_url: validateSSOIssuer(issuer.trim()), client_id: clientId.trim(), scopes: scopes.trim(), ...ssoSecretInput(secretHandling, secret, discardSavedSecret) });
      else await workspaceSSOApi[selected](workspaceId, reauth);
      if (!current()) return;
      setSecret(''); setAction(null); await load();
      if (!current()) return;
      setNotice(t({ save: 'Configuration saved as a disabled draft. Test the connection, then enable workspace sign-in.', test: 'Connection verified. This saved configuration is ready to enable for one hour.', enable: 'Workspace sign-in is enabled. Share your workspace’s sign-in link with linked members.', disable: 'Workspace sign-in is disabled. FreeCollab password sign-in remains available.' }[selected]));
      onChanged?.();
    } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t("Workspace sign-in could not be updated.")); }
    finally { if (current()) { setPassword(''); setCode(''); setBusy(false); onGuardChange(null); } }
  }
  async function copy(value: string, label: string) {
    try { await navigator.clipboard.writeText(value); if (current()) setNotice(t('{label} copied.', { label: t(label) })); }
    catch { if (current()) setError(t("Copy is unavailable. Select the address and copy it manually.")); }
  }
  return <section className="fc-account-section fc-workspace-sso" aria-label={t("Workspace single sign-on")}>
    <div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Your workspace · your identity provider")}</span><h3>{t("Workspace single sign-on")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh workspace single sign-on")} disabled={busy || loading || meaningfulDraft} onClick={() => void load()}><RefreshCw size={16} /></button></div>
    <p className="fc-account-muted">{t("Connect this workspace to your organization’s OpenID Connect provider. Each workspace owns its configuration and client credentials.")}</p>
    {error && <div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}
    {notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
    {loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading workspace sign-in…")}</p> : settings && <>
      <div className="fc-sso-status"><Building2 size={19} /><strong>{provider?.enabled ? t("Enabled") : provider ? t("Disabled draft") : t("Ready to configure")}</strong>{provider?.tested && <span className="fc-badge">{t("Connection verified")}</span>}</div>
      <div className="fc-account-form">
        <label htmlFor="workspace-sso-callback">{t("Callback URI")}</label><div className="fc-integration-secret-value"><input id="workspace-sso-callback" className="fc-input" value={settings.callback_url} readOnly /><button type="button" className="fc-icon-button" aria-label={t("Copy callback URI")} onClick={() => void copy(settings.callback_url, 'Callback URI')}><Copy size={16} /></button></div>
        <p className="fc-account-muted">{t("Register this exact redirect URI with your identity provider. Use an HTTPS issuer and allow the authorization code flow.")}</p>
      </div>
      <form className="fc-account-form fc-sso-configuration" aria-label={t("Workspace SSO configuration")} onSubmit={event => { event.preventDefault(); choose('save'); }}>
        <label htmlFor="workspace-sso-preset">{t('Identity provider preset')}</label><select id="workspace-sso-preset" className="fc-input" value={preset} onChange={event => selectPreset(event.target.value as SSOPresetId)} disabled={busy}>{ssoPresets.map(item => <option key={item.id} value={item.id}>{t(item.name)}</option>)}</select>
        <p className="fc-account-muted">{t('Use your organization’s own provider application. Selecting a different preset clears this form’s client ID and secret; it does not save or change the active connection.')}</p>
        {provider && preset === 'generic' && <p className="fc-account-muted">{t('Your saved issuer is shown exactly as configured. Choose a preset only when preparing a different application.')}</p>}
        <p className="fc-account-muted">{t(ssoPresets.find(item => item.id === preset)!.guide)}</p>
        {preset === 'entra' && <><label htmlFor="workspace-sso-tenant">{t('Directory (tenant) ID')}</label><input id="workspace-sso-tenant" className="fc-input" value={presetFields.tenant} onChange={event => changePresetField('tenant', event.target.value)} placeholder="00000000-0000-0000-0000-000000000000" maxLength={36} disabled={busy} required autoComplete="off" /></>}
        {(preset === 'okta' || preset === 'auth0') && <><label htmlFor="workspace-sso-domain">{preset === 'okta' ? t('Organization domain') : t('Tenant or custom domain')}</label><input id="workspace-sso-domain" className="fc-input" value={presetFields.domain} onChange={event => changePresetField('domain', event.target.value)} placeholder={preset === 'okta' ? 'example.okta.com' : 'example.eu.auth0.com'} maxLength={253} disabled={busy} required autoComplete="off" /><p className="fc-account-muted">{t('Enter only the domain name, without https:// or a path. Custom domains must already be configured with your provider.')}</p></>}
        {preset === 'okta' && <><label htmlFor="workspace-sso-server">{t('Authorization server ID (optional)')}</label><input id="workspace-sso-server" className="fc-input" value={presetFields.authorizationServer} onChange={event => changePresetField('authorizationServer', event.target.value)} maxLength={128} disabled={busy} autoComplete="off" /><p className="fc-account-muted">{t('Leave this empty for your Okta organization issuer. Enter default or another server ID only if your administrator uses that authorization server.')}</p></>}
        {preset === 'keycloak' && <><label htmlFor="workspace-sso-base">{t('Keycloak base URL')}</label><input id="workspace-sso-base" className="fc-input" type="url" value={presetFields.baseUrl} onChange={event => changePresetField('baseUrl', event.target.value)} placeholder="https://identity.example.org" maxLength={2048} disabled={busy} required autoComplete="off" /><label htmlFor="workspace-sso-realm">{t('Realm')}</label><input id="workspace-sso-realm" className="fc-input" value={presetFields.realm} onChange={event => changePresetField('realm', event.target.value)} placeholder="organization" maxLength={128} disabled={busy} required autoComplete="off" /></>}
        {issuerError && <p className="fc-account-muted" role="status">{t(issuerError)}</p>}
        <details><summary>{t('Provider application checklist')}</summary><ol className="fc-account-muted"><li>{t('Create an OpenID Connect web application in your organization’s own provider account.')}</li><li>{t('Enable Authorization Code with PKCE S256. FreeCollab exchanges the code on the server.')}</li><li>{t('Register the exact callback URI above and allow the openid, profile and email scopes.')}</li><li>{t('These web presets use a confidential client and a client secret. Use Remove secret only for a public client whose provider supports token endpoint authentication none.')}</li><li>{t('Save a disabled draft, then explicitly test and enable it with your FreeCollab password and MFA when required.')}</li></ol></details>
        <label htmlFor="workspace-sso-name">{t("Provider name")}</label><input id="workspace-sso-name" className="fc-input" value={name} maxLength={80} onChange={event => setName(event.target.value)} disabled={busy} required placeholder={t("Your organization")} />
        <label htmlFor="workspace-sso-issuer">{t("Issuer URL")}</label><input id="workspace-sso-issuer" className="fc-input" type="url" value={issuer} onChange={event => { setPreset('generic'); setPresetFields(emptySSOPresetFields()); changeAppIdentity(() => setIssuer(event.target.value)); }} disabled={busy} required maxLength={2048} placeholder="https://identity.example.org" />
        {preset !== 'generic' && <p className="fc-account-muted">{t('The issuer is built from the fields above. Editing it directly switches to Generic OpenID Connect.')}</p>}
        <label htmlFor="workspace-sso-client-id">{t("Client ID")}</label><input id="workspace-sso-client-id" className="fc-input" value={clientId} maxLength={512} onChange={event => changeAppIdentity(() => setClientId(event.target.value))} disabled={busy} required autoComplete="off" />
        <label htmlFor="workspace-sso-secret-handling">{t("Client secret handling")}</label><select id="workspace-sso-secret-handling" className="fc-input" value={secretHandling} onChange={event => { setSecretHandling(event.target.value as SSOSecretHandling); setSecret(''); }} disabled={busy}>{canRetainSecret && <option value="retain">{t("Keep saved secret")}</option>}<option value="replace">{t("Replace secret")}</option><option value="remove">{t("Remove secret (public client)")}</option></select>
        {secretHandling === 'replace' && <><label htmlFor="workspace-sso-secret">{t("Client secret")}</label><input id="workspace-sso-secret" className="fc-input" type="password" autoComplete="new-password" value={secret} onChange={event => setSecret(event.target.value)} disabled={busy} placeholder={canRetainSecret ? t("Enter a new secret to rotate") : t("Enter your provider’s client secret")} required={!canRetainSecret} /><p className="fc-account-muted">{canRetainSecret ? t("Leaving this empty keeps the saved secret only when the issuer and client ID stay the same. A different app needs its own secret.") : t("Enter this app’s own client secret. For a public client without a secret, choose Remove secret.")}</p></>}
        {secretHandling === 'retain' && <p className="fc-account-muted">{t("The saved secret is kept only for the same issuer and client ID. Changing either clears it; choose Replace secret for a different app. Public clients can use Remove secret.")}</p>}
        <label htmlFor="workspace-sso-scopes">{t("Scopes")}</label><input id="workspace-sso-scopes" className="fc-input" value={scopes} onChange={event => setScopes(event.target.value)} disabled={busy} required />
        <p className="fc-account-muted">{t("Saving an edit disables SSO until you test and enable the new configuration.")}</p>
        <p className="fc-account-muted">{t('The connection test checks OIDC discovery metadata. Verify client credentials and user access with a linked-member sign-in before rolling out SSO.')}</p>
        <button type="submit" className="fc-button" disabled={busy || !security || !dirty || !!issuerError}>{t("Save SSO configuration")}</button>
      </form>
      {provider && <div className="fc-integration-action-grid"><button type="button" className="fc-button" disabled={busy || dirty || !security} onClick={() => choose('test')}>{t("Test connection")}</button><button type="button" className="fc-button fc-button-primary" disabled={busy || dirty || !provider.tested || provider.enabled || !security} onClick={() => choose('enable')}>{t("Enable SSO")}</button><button type="button" className="fc-button" disabled={busy || !provider.enabled || !security} onClick={() => choose('disable')}>{t("Disable SSO")}</button></div>}
      {action && <form className="fc-account-reauth" aria-label={t("Confirm workspace SSO change")} onSubmit={confirm}><h3>{action === 'save' ? t("Save this configuration?") : action === 'test' ? t("Test your saved connection?") : action === 'enable' ? t("Enable workspace sign-in?") : t("Disable workspace sign-in?")}</h3><p>{t("Confirm your FreeCollab identity for this change.")}</p><label htmlFor="workspace-sso-password">{t("Confirm with your FreeCollab password")}</label><input ref={passwordInput} id="workspace-sso-password" className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy} required />{security?.mfa_enabled && <><label htmlFor="workspace-sso-code">{t("Authentication or recovery code")}</label><input id="workspace-sso-code" className="fc-input" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} disabled={busy} required /></>}<div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy} onClick={() => { setAction(null); setPassword(''); setCode(''); }}>{t("Cancel")}</button><button type="submit" className="fc-button fc-button-primary" disabled={busy}>{busy ? <Loader2 size={15} className="fc-account-spinner" /> : <ShieldCheck size={15} />}{t("Confirm ")}{action === 'save' ? t("configuration") : action === 'test' ? t("connection test") : action === 'enable' ? t("enable SSO") : t("disable SSO")}</button></div></form>}
      {provider && <div className="fc-account-form"><label htmlFor="workspace-sso-login-url">{t("Workspace sign-in link")}</label><div className="fc-integration-secret-value"><input id="workspace-sso-login-url" className="fc-input" value={settings.login_url || provider.login_url} readOnly /><button type="button" className="fc-icon-button" aria-label={t("Copy workspace sign-in link")} onClick={() => void copy(settings.login_url || provider.login_url, 'Workspace sign-in link')}><Copy size={16} /></button></div><p className="fc-account-muted">{t("Invite colleagues to this workspace first. They verify their FreeCollab email and link their own organization identity in Sign-in before using this link.")}</p></div>}
    </>}
  </section>;
}
