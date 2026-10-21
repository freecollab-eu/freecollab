import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Check, CheckCircle2, Copy, Download, KeyRound, Laptop, Loader2, LogOut, Mail, RefreshCw, Shield, ShieldCheck, Smartphone, X } from 'lucide-react';
import QRCode from 'qrcode';
import { api, type User } from '../lib/api';
import { accountApi, type AccountSecurity, type AccountSession } from '../lib/account-api';
import { useModalFocus } from '../lib/useModalFocus';
import { SSOConnections } from '../components/SSOConnections';
import { ScopedSessionNotice } from '../components/ScopedSessionNotice';
import { LanguagePicker, t, useI18n, currentLocale } from '../i18n';

interface AccountPanelProps { onClose: () => void; onSecureSettings?: () => void; }
type Section = 'account' | 'security' | 'sessions';
type ReauthAction = { kind: 'session'; session: AccountSession } | { kind: 'all' } | { kind: 'disable' } | { kind: 'codes' } | null;
const describe = (error: unknown) => error instanceof Error ? error.message : 'Please try again in a moment.';
const date = (value: string) => { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? 'Not available' : parsed.toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short' }); };

export function AccountPanel({ onClose, onSecureSettings }: AccountPanelProps) {
  useI18n();
  const panelRef = useRef<HTMLElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const reauthInput = useRef<HTMLInputElement>(null);
  const setupInput = useRef<HTMLInputElement>(null);
  const [section, setSection] = useState<Section>('account');
  const [user, setUser] = useState<User | null>(null);
  const [security, setSecurity] = useState<AccountSecurity | null>(null);
  const [sessions, setSessions] = useState<AccountSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordCode, setPasswordCode] = useState('');
  const [setupPassword, setSetupPassword] = useState('');
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string; expires_at: string } | null>(null);
  const [setupQR, setSetupQR] = useState('');
  const [setupCode, setSetupCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [codesStored, setCodesStored] = useState(false);
  const [reauth, setReauth] = useState<ReauthAction>(null);
  const [reauthPassword, setReauthPassword] = useState('');
  const [reauthCode, setReauthCode] = useState('');
  const identity = useRef(api.getCurrentUserId());
  const mounted = useRef(true);
  const isCurrent = () => mounted.current && identity.current === api.getCurrentUserId();
  const close = () => {
    if (recoveryCodes.length && !codesStored) { setSection('security'); setNotice(t("Save your sign-in recovery codes, then confirm that you have stored them.")); return; }
    onClose();
  };
  useModalFocus(panelRef, close);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (reauth) reauthInput.current?.focus(); }, [reauth]);
  useEffect(() => { if (setup) setupInput.current?.focus(); }, [setup]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setSetupQR('');
    if (setup) void QRCode.toDataURL(setup.otpauth_uri, { width: 200, margin: 2, errorCorrectionLevel: 'M' }).then(url => { if (active) setSetupQR(url); }).catch(() => {});
    return () => { active = false; };
  }, [setup]);

  async function load() {
    setLoading(true); setError('');
    try {
      const me = await api.getMe();
      if (!isCurrent()) return;
      setUser(me);
      if (me.session_scope === 'workspace') { setSecurity(null); setSessions([]); return; }
      const [state, records] = await Promise.all([accountApi.security(), accountApi.sessions()]);
      if (!isCurrent()) return;
      setUser(me); setSecurity(state); setSessions(records);
    } catch (failure) { if (isCurrent()) setError(describe(failure)); }
    finally { if (isCurrent()) setLoading(false); }
  }

  async function run(key: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(key); setError(''); setNotice('');
    try { await operation(); }
    catch (failure) { if (isCurrent()) setError(describe(failure)); }
    finally { if (isCurrent()) setBusy(''); }
  }

  function signedOut(message: string) {
    if (!isCurrent()) return;
    api.logout();
    window.dispatchEvent(new CustomEvent('freecollab:signed-out', { detail: { notice: message } }));
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    if (newPassword !== confirmPassword) { setError(t("The new passwords do not match.")); return; }
    await run('password', async () => {
      await accountApi.changePassword(password, newPassword, passwordCode);
      signedOut('Your password has changed. Sign in again with your new password.');
    });
  }

  async function beginMFA(event: FormEvent) {
    event.preventDefault();
    await run('setup', async () => {
      const response = await accountApi.setupMFA(setupPassword);
      if (!isCurrent()) return;
      setSetup(response); setSetupPassword(''); setSetupCode('');
      setNotice(t("Add this account to your authenticator, then enter its current code."));
    });
  }

  async function confirmMFA(event: FormEvent) {
    event.preventDefault();
    await run('confirm', async () => {
      const response = await accountApi.confirmMFA(setupCode.trim());
      if (!isCurrent()) return;
      api.applySession(response.session);
      setSetup(null); setSetupCode(''); setRecoveryCodes(response.recovery_codes); setCodesStored(false);
      setSecurity(previous => previous ? { ...previous, mfa_enabled: true } : previous);
      const records = await accountApi.sessions();
      if (isCurrent()) { setSessions(records); setNotice(t("Two-factor authentication is enabled. Keep your recovery codes somewhere safe.")); }
    });
  }

  function chooseReauth(action: ReauthAction) { setReauth(action); setReauthPassword(''); setReauthCode(''); setError(''); setNotice(''); }

  async function submitReauth(event: FormEvent) {
    event.preventDefault();
    if (!reauth) return;
    const action = reauth;
    await run('reauth', async () => {
      if (action.kind === 'session') {
        await accountApi.revokeSession(action.session.id, reauthPassword, reauthCode);
        if (!isCurrent()) return;
        if (action.session.current) { signedOut('This session has been signed out.'); return; }
        setSessions(previous => previous.filter(session => session.id !== action.session.id));
        setNotice(t("The selected session has been signed out."));
      } else if (action.kind === 'all') {
        await accountApi.revokeAll(reauthPassword, reauthCode);
        signedOut('All sessions have been signed out. Sign in again to continue.'); return;
      } else if (action.kind === 'disable') {
        const response = await accountApi.disableMFA(reauthPassword, reauthCode.trim());
        if (!isCurrent()) return;
        api.applySession(response.session);
        setSecurity(previous => previous ? { ...previous, mfa_enabled: false } : previous);
        setRecoveryCodes([]); setNotice(t("Two-factor authentication has been turned off. Other sessions were signed out."));
        const records = await accountApi.sessions();
        if (isCurrent()) setSessions(records);
      } else {
        const response = await accountApi.replaceRecoveryCodes(reauthPassword, reauthCode.trim());
        if (!isCurrent()) return;
        setRecoveryCodes(response.recovery_codes); setCodesStored(false);
        setNotice(t("New recovery codes created. Previous codes no longer work."));
      }
      if (isCurrent()) { setReauth(null); setReauthPassword(''); setReauthCode(''); }
    });
  }

  async function copy(value: string, message: string) {
    try { await navigator.clipboard.writeText(value); if (isCurrent()) setNotice(message); }
    catch { if (isCurrent()) setNotice(t("Select and copy the text below.")); }
  }

  function downloadCodes() {
    const content = `FreeCollab sign-in recovery codes\nAccount: ${user?.email || ''}\n\n${recoveryCodes.join('\n')}\n\nEach code can be used once. These codes do not unlock encrypted message history.\n`;
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'freecollab-sign-in-recovery-codes.txt'; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice(t("Recovery codes downloaded. Store the file somewhere private."));
  }

  return createPortal(<div className="fc-account-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section className="fc-account-panel" ref={panelRef} role="dialog" aria-modal="true" aria-label={t("Account settings")} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker">{t("Your FreeCollab")}</span><h2>{t("Your account.")}</h2><p>{t("Keep your access and identity in order.")}</p></div><button type="button" data-autofocus className="fc-icon-button" onClick={close} aria-label={t("Close account settings")}><X size={20} /></button></header>
      {user?.session_scope !== 'workspace' && <nav className="fc-account-tabs" aria-label={t("Account sections")}>{([{ id: 'account', label: t("Account"), Icon: Mail }, { id: 'security', label: t("Security"), Icon: Shield }, { id: 'sessions', label: t("Sessions"), Icon: Laptop }] as const).map(({ id, label, Icon }) => <button key={id} type="button" aria-current={section === id ? 'page' : undefined} onClick={() => { setSection(id); setError(''); setNotice(''); setReauth(null); }}><Icon size={16} />{label}</button>)}</nav>}
      <div className="fc-account-body">
        <LanguagePicker />
        {error && <div ref={errorRef} className="fc-alert fc-account-error" role="alert" tabIndex={-1}><AlertCircle size={17} /><p>{error}</p></div>}
        {notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
        {loading ? <div className="fc-account-loading" role="status"><Loader2 size={19} className="fc-account-spinner" />{t("Loading your account…")}</div> : user?.session_scope === 'workspace' ? <ScopedSessionNotice managed={user.account_type === 'managed'} /> : !security || !user ? <button className="fc-button" type="button" onClick={() => void load()}><RefreshCw size={16} />{t("Try again")}</button> : <>
          {section === 'account' && <>
            <section className="fc-account-section"><span className="fc-kicker">{t("Your identity")}</span><h3>{user.name}</h3><p className="fc-account-muted">{t("Your personal account can belong to more than one workspace.")}</p><dl className="fc-account-details"><div><dt>{t("Email")}</dt><dd>{security.email}</dd></div><div><dt>{t("Verification")}</dt><dd className={security.email_verified ? 'fc-account-success' : ''}>{security.email_verified ? <><CheckCircle2 size={15} />{t("Verified")}</> : t("Not yet verified")}</dd></div></dl>
              {!security.email_verified && <><button type="button" className="fc-button" disabled={!!busy || !security.mail_delivery_available} onClick={() => void run('verify', async () => { const response = await accountApi.requestVerification(); if (isCurrent()) setNotice(response.message); })}>{busy === 'verify' && <Loader2 size={15} className="fc-account-spinner" />}{t("Send verification email")}</button>{!security.mail_delivery_available && <p className="fc-account-muted">{t("Email delivery is currently unavailable on this server.")}</p>}</>}
            </section>
            <SSOConnections security={security} />
            <section className="fc-account-section fc-account-key-note"><KeyRound size={21} /><div><h3>{t("Account access and encrypted history")}</h3><p>{t("Your password and sign-in recovery codes restore account access. Encrypted message history uses its own trusted devices and recovery key.")}</p>{onSecureSettings && <button className="fc-account-link" type="button" onClick={() => { if (recoveryCodes.length && !codesStored) { setSection('security'); setNotice(t("Save your sign-in recovery codes before opening encryption settings.")); } else onSecureSettings(); }}>{t("Manage encryption devices & recovery")}</button>}</div></section>
          </>}
          {section === 'security' && <>
            <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("A second layer")}</span><h3>{t("Two-factor authentication")}</h3></div><span className={`fc-badge ${security.mfa_enabled ? 'fc-account-success' : ''}`}>{security.mfa_enabled ? t("Enabled") : t("Not enabled")}</span></div><p className="fc-account-muted">{t("Use an authenticator app to protect sign-in with a second code.")}</p>
              {!security.mfa_enabled && !setup && <form onSubmit={beginMFA} className="fc-account-form"><label htmlFor="mfa-setup-password">{t("Current password")}</label><input className="fc-input" id="mfa-setup-password" type="password" autoComplete="current-password" value={setupPassword} onChange={event => setSetupPassword(event.target.value)} disabled={!!busy} required /><button type="submit" className="fc-button fc-button-primary" disabled={!!busy}>{busy === 'setup' && <Loader2 size={15} className="fc-account-spinner" />}{t("Set up two-factor authentication")}</button></form>}
              {setup && <div className="fc-account-mfa-setup">{setupQR && <img className="fc-account-qr" src={setupQR} width="200" height="200" alt={t("Scan this QR code with your authenticator app")} />}<p>{t("Scan the code in your authenticator app, or enter this setup key manually.")}</p><div className="fc-account-secret"><code>{setup.secret}</code><button type="button" className="fc-icon-button" aria-label={t("Copy authenticator setup key")} onClick={() => void copy(setup.secret, 'Authenticator setup key copied.')}><Copy size={16} /></button></div><p className="fc-account-muted">{t("Setup expires ")}{date(setup.expires_at)}{t(". This setup key stays in this view only.")}</p><form className="fc-account-form" onSubmit={confirmMFA}><label htmlFor="mfa-confirm-code">{t("Authenticator code")}</label><input ref={setupInput} id="mfa-confirm-code" className="fc-input fc-account-code-input" type="text" inputMode="numeric" autoComplete="one-time-code" value={setupCode} onChange={event => setSetupCode(event.target.value)} maxLength={12} disabled={!!busy} required /><div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => { setSetup(null); setSetupCode(''); }}>{t("Cancel setup")}</button><button type="submit" className="fc-button fc-button-primary" disabled={!!busy}>{busy === 'confirm' && <Loader2 size={15} className="fc-account-spinner" />}{t("Enable two-factor authentication")}</button></div></form></div>}
              {security.mfa_enabled && !recoveryCodes.length && <div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => chooseReauth({ kind: 'codes' })}><RefreshCw size={15} />{t("Replace recovery codes")}</button><button className="fc-account-link fc-account-danger" type="button" disabled={!!busy} onClick={() => chooseReauth({ kind: 'disable' })}>{t("Turn off two-factor authentication")}</button></div>}
            </section>
            {recoveryCodes.length > 0 && <section className="fc-account-section fc-account-recovery"><span className="fc-kicker">{t("Shown once")}</span><h3>{t("Save your sign-in recovery codes")}</h3><p>{t("Use an unused code if you cannot access your authenticator. Each code works once.")}</p><div className="fc-account-recovery-codes">{recoveryCodes.map(value => <code key={value}>{value}</code>)}</div><div className="fc-account-actions"><button className="fc-button" type="button" onClick={() => void copy(recoveryCodes.join('\n'), 'Recovery codes copied. Store them somewhere private.')}><Copy size={15} />{t("Copy codes")}</button><button className="fc-button" type="button" onClick={downloadCodes}><Download size={15} />{t("Download codes")}</button></div><p className="fc-account-muted">{t("These codes are for signing in. They do not unlock encrypted message history.")}</p><label className="fc-account-checkbox"><input type="checkbox" checked={codesStored} onChange={event => setCodesStored(event.target.checked)} />{t("I have stored these codes somewhere safe.")}</label>{codesStored && <button type="button" className="fc-button" onClick={() => { setRecoveryCodes([]); setNotice(t("Your recovery codes are stored.")); }}><Check size={15} />{t("Done")}</button>}</section>}
            <section className="fc-account-section"><span className="fc-kicker">{t("Your password")}</span><h3>{t("Change password")}</h3><p className="fc-account-muted">{t("Changing your password signs out all existing sessions. It does not replace an encryption recovery key.")}</p><form onSubmit={changePassword} className="fc-account-form"><label htmlFor="account-current-password">{t("Current password")}</label><input className="fc-input" id="account-current-password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={!!busy} required /><label htmlFor="account-new-password">{t("New password")}</label><input className="fc-input" id="account-new-password" type="password" autoComplete="new-password" minLength={6} value={newPassword} onChange={event => setNewPassword(event.target.value)} disabled={!!busy} required /><label htmlFor="account-confirm-password">{t("Confirm new password")}</label><input className="fc-input" id="account-confirm-password" type="password" autoComplete="new-password" minLength={6} value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} disabled={!!busy} required />{security.mfa_enabled && <><label htmlFor="account-password-code">{t("Authentication or recovery code")}</label><input className="fc-input" id="account-password-code" type="text" autoComplete="one-time-code" value={passwordCode} onChange={event => setPasswordCode(event.target.value)} disabled={!!busy} required /></>}<button className="fc-button" type="submit" disabled={!!busy}>{busy === 'password' && <Loader2 size={15} className="fc-account-spinner" />}{t("Change password and sign out")}</button></form></section>
          </>}
          {section === 'sessions' && <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Where you’re signed in")}</span><h3>{t("Active sessions")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh sessions")} disabled={!!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("A session grants account access. Trusted encryption devices are managed separately.")}</p><ul className="fc-account-session-list">{sessions.map(session => <li key={session.id}><span className="fc-account-session-icon">{/Mobile|Android|iPhone|iPad/i.test(session.user_agent || '') ? <Smartphone size={21} /> : <Laptop size={21} />}</span><div><strong>{session.current ? t("This session") : t("Signed-in session")}{session.current && <span className="fc-badge">{t("Current")}</span>}</strong><p title={session.user_agent || undefined}>{session.user_agent || t("Device details unavailable")}</p><small>{t("Signed in ")}{date(session.created_at)}<br />{t("Expires ")}{date(session.expires_at)}{session.client_address && <><br />{t("Address ")}{session.client_address}</>}</small><button className="fc-account-link" type="button" disabled={!!busy} onClick={() => chooseReauth({ kind: 'session', session })}>{t("Sign out ")}{session.current ? t("this session") : t("session")}</button></div></li>)}</ul><button className="fc-button" type="button" disabled={!!busy} onClick={() => chooseReauth({ kind: 'all' })}><LogOut size={16} />{t("Sign out all sessions")}</button></section>}
          {reauth && <form className="fc-account-reauth" onSubmit={submitReauth}><h3>{reauth.kind === 'all' ? t("Sign out every session?") : reauth.kind === 'disable' ? t("Turn off two-factor authentication?") : reauth.kind === 'codes' ? t("Replace your recovery codes?") : t("Sign out this session?")}</h3><p>{reauth.kind === 'codes' ? t("Your previous recovery codes will stop working.") : t("Confirm your identity to continue.")}</p><label htmlFor="account-reauth-password">{t("Confirm with your password")}</label><input ref={reauthInput} className="fc-input" id="account-reauth-password" type="password" autoComplete="current-password" value={reauthPassword} onChange={event => setReauthPassword(event.target.value)} disabled={!!busy} required />{security.mfa_enabled && <><label htmlFor="account-reauth-code">{t("Authentication or recovery code")}</label><input className="fc-input" id="account-reauth-code" type="text" autoComplete="one-time-code" value={reauthCode} onChange={event => setReauthCode(event.target.value)} disabled={!!busy} required /></>}<div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => chooseReauth(null)}>{t("Cancel")}</button><button className="fc-button fc-button-primary" type="submit" disabled={!!busy}>{busy === 'reauth' && <Loader2 size={15} className="fc-account-spinner" />}{t("Confirm")}</button></div></form>}
        </>}
      </div>
      <footer className="fc-account-footer"><ShieldCheck size={15} /><span>{t("Your settings belong to your account.")}</span><button type="button" className="fc-button" onClick={close}>{t("Done")}</button></footer>
    </section>
  </div>, document.body);
}
