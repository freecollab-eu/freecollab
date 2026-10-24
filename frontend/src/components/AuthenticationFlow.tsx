import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowLeft, ArrowRight, CheckCircle2, Loader2, Mail, KeyRound } from 'lucide-react';
import { api } from '../lib/api';
import { accountApi } from '../lib/account-api';
import { newPasswordValidationError } from '../lib/password-policy';
import { Brand } from './Brand';
import '../styles/account.css';

interface AuthenticationFlowProps {
  kind: 'verify-email' | 'reset-password';
  token: string;
  onSuccess: () => void;
  onBack: () => void;
}

export function AuthenticationFlow({ kind, token, onSuccess, onBack }: AuthenticationFlowProps) {
  useI18n();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const errorRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(true);
  const reset = kind === 'reset-password';
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError('');
    const passwordError = reset ? newPasswordValidationError(password) : null;
    if (passwordError) { setError(t(passwordError)); requestAnimationFrame(() => errorRef.current?.focus()); return; }
    if (reset && password !== confirm) { setError(t("The passwords do not match.")); requestAnimationFrame(() => errorRef.current?.focus()); return; }
    setBusy(true);
    const userId = api.getCurrentUserId();
    try {
      if (reset) { await accountApi.resetPassword(token, password); if (!mounted.current || userId !== api.getCurrentUserId()) return; api.logout(); setPassword(''); setConfirm(''); }
      else { await accountApi.verifyEmail(token); if (!mounted.current || userId !== api.getCurrentUserId()) return; window.dispatchEvent(new Event('freecollab:account-updated')); }
      setDone(true);
    } catch (failure) {
      if (!mounted.current || userId !== api.getCurrentUserId()) return;
      setError(failure instanceof Error ? failure.message : t("This link could not be used. Please request a new one."));
      requestAnimationFrame(() => errorRef.current?.focus());
    } finally { if (mounted.current) setBusy(false); }
  }

  return <main className="fc-authentication-flow">
    <header><Brand /><button type="button" className="fc-account-link" onClick={onBack}><ArrowLeft size={16} />{t("Back to FreeCollab")}</button></header>
    <section className="fc-authentication-card" aria-labelledby="authentication-title">
      <span className="fc-account-feature-icon">{done ? <CheckCircle2 size={27} /> : reset ? <KeyRound size={27} /> : <Mail size={27} />}</span>
      <p className="fc-kicker">{done ? t("ALL SET") : reset ? t("ACCOUNT RECOVERY") : t("YOUR EMAIL ADDRESS")}</p>
      <h1 id="authentication-title">{done ? reset ? t("A fresh start.") : t("Email verified.") : reset ? t("Choose a new password.") : t("Verify your email.")}</h1>
      {done ? <>
        <p>{reset ? t("Your password has been updated and previous sessions have been signed out. Sign in with your new password to continue.") : t("Your email address is now verified. You can return to your workspace.")}</p>
        {reset && <p className="fc-account-muted">{t("Encrypted history still requires a trusted device or its separate recovery key.")}</p>}
        <button type="button" className="fc-button fc-button-primary" onClick={onSuccess}>{reset ? t("Continue to sign in") : t("Continue")}<ArrowRight size={17} /></button>
      </> : <form onSubmit={submit} aria-busy={busy}>
        <p>{reset ? t("Create a password you have not used on another service.") : t("Confirm this address so you can receive account recovery messages.")}</p>
        {reset && <>
          <label htmlFor="reset-new-password">{t("New password")}</label><input className="fc-input" id="reset-new-password" type="password" autoComplete="new-password" aria-describedby="reset-password-help" value={password} onChange={event => { setPassword(event.target.value); setError(''); }} disabled={busy} required />
          <p id="reset-password-help" className="fc-account-muted">{t("At least 12 characters. Make it something only you know.")}</p>
          <label htmlFor="reset-confirm-password">{t("Confirm new password")}</label><input className="fc-input" id="reset-confirm-password" type="password" autoComplete="new-password" value={confirm} onChange={event => { setConfirm(event.target.value); setError(''); }} disabled={busy} required />
          <p className="fc-account-muted">{t("Resetting this password restores account access. It does not replace a key for encrypted history.")}</p>
        </>}
        {error && <div ref={errorRef} className="fc-alert fc-account-error" role="alert" tabIndex={-1}><AlertCircle size={17} /><p>{error}</p></div>}
        <button type="submit" className="fc-button fc-button-primary" disabled={busy || !token}>{busy && <Loader2 size={16} className="fc-account-spinner" />}{busy ? t("One moment…") : reset ? t("Reset password") : t("Verify email")}{!busy && <ArrowRight size={17} />}</button>
      </form>}
    </section>
  </main>;
}
