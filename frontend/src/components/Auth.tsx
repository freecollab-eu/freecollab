import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, Check, AlertCircle, Building2, Eye, EyeOff, Loader2, LockKeyhole, Mail, RefreshCw, ShieldCheck } from 'lucide-react';
import { Brand } from './Brand';
import { LanguagePicker, t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { accountApi, needsSecondFactor, type MFAChallenge } from '../lib/account-api';
import { followOIDCAuthorization, oidcApi, type OIDCProvider } from '../lib/integrations-api';
import '../styles/public.css';
import '../styles/account.css';
import '../styles/integrations.css';

interface AuthProps {
  onSuccess: () => void;
  invited?: boolean;
  notice?: string;
  onBack?: () => void;
  initialChallenge?: MFAChallenge | null;
  organizationLoginId?: string | null;
  organizationInvitationToken?: string | null;
  onOrganizationChange?: (loginId: string | null) => void;
}

type AuthMode = 'login' | 'register' | 'forgot' | 'mfa';

export function Auth({ onSuccess, invited, notice, onBack, initialChallenge, organizationLoginId, organizationInvitationToken, onOrganizationChange }: AuthProps) {
  useI18n();
  const [mode, setMode] = useState<AuthMode>(initialChallenge ? 'mfa' : 'login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [challenge, setChallenge] = useState<MFAChallenge | null>(initialChallenge || null);
  const [providers, setProviders] = useState<OIDCProvider[]>([]);
  const [providerError, setProviderError] = useState(false);
  const [organizationLink, setOrganizationLink] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const errorRef = useRef<HTMLDivElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const providerRequest = useRef(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { void loadProviders(); }, [organizationLoginId]);
  useEffect(() => { if (initialChallenge) { setChallenge(initialChallenge); setMode('mfa'); setCode(''); } }, [initialChallenge]);

  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (mode === 'mfa') codeRef.current?.focus(); }, [mode]);

  function switchMode(next: AuthMode) {
    setMode(next); setError(''); setSent(''); setShowPassword(false); setCode('');
    if (next !== 'mfa') setChallenge(null);
  }

  async function loadProviders() {
    const request = ++providerRequest.current;
    setProviderError(false);
    setProviders([]);
    try { const response = await oidcApi.providers(organizationLoginId ? { loginId: organizationLoginId } : undefined); if (mounted.current && request === providerRequest.current) setProviders(response.providers); }
    catch { if (mounted.current && request === providerRequest.current) setProviderError(true); }
  }

  async function singleSignOn() {
    if (loading) return;
    setLoading(true); setError('');
    const token = api.getAccessToken();
    try {
      const result = await oidcApi.start(organizationLoginId || undefined, organizationInvitationToken || undefined);
      if (mounted.current && token === api.getAccessToken()) followOIDCAuthorization(result.authorization_url);
    } catch (failure) { if (mounted.current && token === api.getAccessToken()) setError(failure instanceof Error ? failure.message : t("Single sign-on could not be started.")); }
    finally { if (mounted.current) setLoading(false); }
  }

  function selectOrganization(event: React.FormEvent) {
    event.preventDefault();
    let loginId = organizationLink.trim();
    if (loginId.includes('#')) {
      try { loginId = new URLSearchParams(new URL(loginId).hash.slice(1)).get('workspace-sso') || ''; }
      catch { loginId = ''; }
    }
    if (!/^[A-Za-z0-9_-]{16,200}$/.test(loginId)) { setError(t("Paste the workspace sign-in link or organization identifier shared by your administrator.")); return; }
    setError(''); setOrganizationLink(''); onOrganizationChange?.(loginId);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (loading) return;
    setError(''); setSent(''); setLoading(true);
    const requestToken = api.getAccessToken();
    const current = () => mounted.current && requestToken === api.getAccessToken();
    try {
      if (mode === 'forgot') {
        const response = await accountApi.forgotPassword(email.trim());
        if (current()) setSent(response.message);
      } else if (mode === 'mfa') {
        if (!challenge) throw new Error(t("Start sign-in again to request a new verification challenge."));
        const session = await accountApi.verifyMFA(challenge.challenge_token, code.trim());
        if (!current()) return;
        api.applySession(session); setCode(''); setChallenge(null); onSuccess();
      } else if (mode === 'register') {
        if ([...password].length < 12) throw new Error(t("New passwords must contain at least 12 characters."));
        if (new TextEncoder().encode(password).length > 72) throw new Error(t("This password is too long. Try a shorter one."));
        const session = await accountApi.register(name.trim(), email.trim(), password);
        if (!current()) return;
        api.applySession(session); setPassword(''); onSuccess();
      } else {
        const response = await accountApi.login(email.trim(), password);
        if (!current()) return;
        setPassword('');
        if (needsSecondFactor(response)) { setChallenge(response); setMode('mfa'); }
        else { api.applySession(response); onSuccess(); }
      }
    } catch (err) { if (current()) setError(err instanceof Error ? err.message : t("Please try again in a moment.")); }
    finally { if (mounted.current) setLoading(false); }
  }

  const labels = {
    login: { kicker: t("WELCOME BACK"), title: <>{t("Pick up the")}<br /><em>{t("conversation.")}</em></>, intro: t("Sign in to your FreeCollab workspace."), submit: t("Sign In"), pending: t("Signing you in…") },
    register: { kicker: t("YOUR OWN ACCOUNT. YOUR OWN PERSPECTIVE."), title: <>{t("Create your")}<br /><em>{t("account.")}</em></>, intro: invited ? t("Create an account, then accept your invitation.") : t("Join this FreeCollab server and make yourself at home."), submit: t("Create Account"), pending: t("Creating your account…") },
    forgot: { kicker: t("A WAY BACK IN"), title: <>{t("Let’s get you")}<br /><em>{t("back in.")}</em></>, intro: t("Request a password reset link for your account."), submit: t("Send reset link"), pending: t("Requesting your reset link…") },
    mfa: { kicker: t("ONE MORE STEP"), title: <>{t("Make sure")}<br /><em>{t("it’s you.")}</em></>, intro: t("Enter a code from your authenticator app, or an unused recovery code."), submit: t("Verify and sign in"), pending: t("Checking your code…") },
  }[mode];

  return <main className="fc-auth">
    <aside className="fc-auth-editorial" aria-label={t("About FreeCollab")}>
      <Brand size="large" />
      <div className="fc-auth-editorial-copy"><p className="fc-kicker">{t("A SHARED PLACE FOR YOUR TEAM.")}</p><h2>{t("Good work")}<br />{t("starts with")}<br /><em>{t("your people.")}</em></h2><p>{t("Conversations, project files, and small decisions. One shared place to move the work forward.")}</p></div>
      <div className="fc-auth-editorial-footer"><Brand size="large" showName={false} /><p>{t("Space for every perspective.")}<br /><span>{t("Hosted on your own terms.")}</span></p></div>
    </aside>
    <section className="fc-auth-form-panel" aria-labelledby="auth-heading">
      {onBack && <button type="button" className="fc-public-text-button fc-auth-back" onClick={onBack}><ArrowLeft size={15} aria-hidden="true" />{t("Back to FreeCollab")}</button>}
      <div className="fc-auth-form-wrap">
        <LanguagePicker compact />
        <Brand size="large" className="fc-auth-mobile-brand" />
        <p className="fc-kicker">{labels.kicker}</p><h1 id="auth-heading">{labels.title}</h1><p className="fc-auth-intro">{labels.intro}</p>
        {invited && mode !== 'forgot' && <div className="fc-alert fc-auth-notice" role="status"><ArrowUpRight size={18} aria-hidden="true" /><p>{t("Sign in or create an account to accept your invitation.")}</p></div>}
        {notice && <div className="fc-alert fc-auth-notice" role="status"><Check size={18} aria-hidden="true" /><p>{notice}</p></div>}
        {sent && <div className="fc-alert fc-auth-notice" role="status"><Mail size={18} aria-hidden="true" /><p>{sent}</p></div>}
        {(mode === 'login' || mode === 'register') && <>
          {organizationLoginId && <p className="fc-account-muted">{providers[0]?.onboarding_mode && providers[0].onboarding_mode !== 'linked' ? organizationInvitationToken ? t("Your invitation is ready. Continue with your organization to create your workspace account. No separate FreeCollab password is needed.") : providers[0].onboarding_mode === 'provisioned' ? t("Use the organization account provisioned by your administrator. No separate FreeCollab password is needed.") : providers[0].onboarding_mode === 'invited' ? t("Use the invitation shared by your workspace administrator for your first organization sign-in.") : t("Continue with your organization to join this workspace. Your organization account stays separate from personal FreeCollab accounts.") : t("Organization sign-in for this workspace. Your personal account must already be a member with its organization identity linked.")}</p>}
          {providers.map(provider => <button key={provider.id} type="button" className="fc-button fc-auth-sso" disabled={loading} onClick={() => void singleSignOn()}><Building2 size={17} />{t("Continue with ")}{provider.name}<ArrowUpRight size={16} /></button>)}
          {providers.length > 0 && <p className="fc-auth-divider"><span>{t("or use your FreeCollab account")}</span></p>}
          {providerError && <p className="fc-auth-sso-retry">{t("Single sign-on is temporarily unavailable. ")}<button type="button" className="fc-account-link" onClick={() => void loadProviders()} disabled={loading}><RefreshCw size={12} />{t("Retry")}</button></p>}
          {organizationLoginId && !providerError && !providers.length && <p className="fc-account-muted" role="status">{t("This workspace sign-in link is unavailable or has been disabled. You can continue with your FreeCollab password.")}</p>}
          {onOrganizationChange && <details className="fc-auth-organization"><summary>{organizationLoginId ? t("Use another organization") : t("Sign in with your organization")}</summary><form className="fc-account-form" onSubmit={selectOrganization}><label htmlFor="organization-sign-in-link">{t("Workspace sign-in link or identifier")}</label><input id="organization-sign-in-link" className="fc-input" value={organizationLink} onChange={event => setOrganizationLink(event.target.value)} autoComplete="off" disabled={loading} required /><button type="submit" className="fc-button" disabled={loading}>{t("Find organization sign-in")}</button>{organizationLoginId && <button type="button" className="fc-account-link" disabled={loading} onClick={() => onOrganizationChange(null)}>{t("Use FreeCollab sign-in")}</button>}</form></details>}
        </>}
        <form onSubmit={handleSubmit} className="fc-auth-form" aria-busy={loading}>
          {mode === 'register' && <div className="fc-auth-field"><label htmlFor="auth-name">{t("Name")}</label><input className="fc-input" id="auth-name" type="text" autoComplete="name" maxLength={80} value={name} onChange={event => setName(event.target.value)} placeholder={t("Your name")} disabled={loading} required /></div>}
          {mode !== 'mfa' && <div className="fc-auth-field"><label htmlFor="auth-email">{t("Email")}</label><input className="fc-input" id="auth-email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} placeholder={t("you@example.com")} disabled={loading} required /></div>}
          {(mode === 'login' || mode === 'register') && <div className="fc-auth-field">
            <label htmlFor="auth-password">{t("Password")}</label>
            <div className="fc-auth-password"><input className="fc-input" id="auth-password" type={showPassword ? 'text' : 'password'} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} value={password} onChange={event => setPassword(event.target.value)} placeholder={t("Your password")} required minLength={mode === 'register' ? 12 : 1} disabled={loading} aria-describedby={mode === 'register' ? 'auth-password-help' : undefined} /><button type="button" onClick={() => setShowPassword(value => !value)} aria-label={showPassword ? t("Hide password") : t("Show password")} aria-pressed={showPassword} disabled={loading}>{showPassword ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}</button></div>
            {mode === 'register' && <p id="auth-password-help" className="fc-auth-field-help">{t("At least 12 characters. Make it something only you know.")}</p>}
            {mode === 'login' && <button type="button" className="fc-account-link fc-auth-forgot" onClick={() => switchMode('forgot')} disabled={loading}>{t("Forgot password?")}</button>}
          </div>}
          {mode === 'mfa' && <div className="fc-auth-field"><label htmlFor="auth-code">{t("Authentication or recovery code")}</label><input ref={codeRef} className="fc-input fc-account-code-input" id="auth-code" type="text" autoComplete="one-time-code" spellCheck={false} value={code} onChange={event => setCode(event.target.value)} placeholder={t("Enter your code")} maxLength={80} disabled={loading} required /><p className="fc-auth-field-help">{t("The sign-in challenge expires after five minutes.")}</p></div>}
          {mode === 'forgot' && <p className="fc-account-muted">{t("A reset link restores account access. Encrypted history still requires a trusted device or its separate recovery key.")}</p>}
          {error && <div ref={errorRef} tabIndex={-1} id="auth-error" role="alert" className="fc-alert fc-alert-error fc-public-error"><AlertCircle size={18} aria-hidden="true" /><div><strong>{mode === 'mfa' ? t("We couldn’t verify that code.") : mode === 'forgot' ? t("Your request couldn’t be completed.") : mode === 'login' ? t("We couldn’t sign you in.") : t("Your account couldn’t be created.")}</strong><p>{error}</p></div></div>}
          <button type="submit" aria-label={labels.submit} disabled={loading} className="fc-button fc-button-primary fc-auth-submit">{loading ? <><span role="status">{labels.pending}</span><Loader2 size={17} className="fc-public-spinner" aria-hidden="true" /></> : <>{labels.submit}<ArrowRight size={17} aria-hidden="true" /></>}</button>
        </form>
        <div className="fc-auth-switch"><button type="button" className="fc-public-text-button" disabled={loading} onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}>{mode === 'login' ? t("Don't have an account?") : mode === 'register' ? t("Already have an account?") : t("Back to sign in")}<ArrowUpRight size={15} aria-hidden="true" /></button>{(mode === 'login' || mode === 'register') && <span>{mode === 'login' ? t("Create one on this server.") : t("Sign in to pick up where you left off.")}</span>}</div>
        <p className="fc-auth-trust">{mode === 'mfa' ? <ShieldCheck size={14} aria-hidden="true" /> : <LockKeyhole size={14} aria-hidden="true" />}<span>{t("Your account belongs to this server.")}<br />{t("Use an installation you know and trust.")}</span></p>
      </div><p className="fc-auth-panel-footer">{t("SELF-HOSTED TEAM CHAT / FREECOLLAB")}</p>
    </section>
  </main>;
}
