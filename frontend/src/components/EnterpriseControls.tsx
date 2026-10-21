import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, Check, CheckCircle2, Copy, Eye, EyeOff, Loader2, ShieldCheck } from 'lucide-react';
import type { SSOReauthentication } from '../lib/integrations-api';

export type IntegrationGuard = 'busy' | 'secret' | null;
export const enterpriseFailure = (failure: unknown) => failure instanceof Error ? failure.message : 'Please try again.';
export const enterpriseDate = (value?: string | null) => value ? new Date(value).toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short' }) : 'Not yet';

export function EnterpriseAlerts({ error, notice }: { error: string; notice: string }) {
  useI18n();
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  return <>{error && <div ref={errorRef} tabIndex={-1} className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}{notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}</>;
}

export function NativeConfirmation({ id, title, children, mfa, busy, onCancel, onConfirm }: { id: string; title: string; children?: React.ReactNode; mfa: boolean; busy: boolean; onCancel: () => void; onConfirm: (values: SSOReauthentication) => Promise<void> }) {
  useI18n();
  const passwordInput = useRef<HTMLInputElement>(null);
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; passwordInput.current?.focus(); return () => { mounted.current = false; }; }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    try { await onConfirm({ password, code: code.trim() || undefined }); }
    finally { if (mounted.current) { setPassword(''); setCode(''); } }
  }
  return <form className="fc-account-reauth" onSubmit={submit} aria-label={title} aria-busy={busy}><h3>{title}</h3>{children}<p>{t("Confirm with your personal FreeCollab account.")}</p><label htmlFor={id + '-password'}>{t("FreeCollab password")}</label><input ref={passwordInput} id={id + '-password'} className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy} required />{mfa && <><label htmlFor={id + '-code'}>{t("Authentication or recovery code")}</label><input id={id + '-code'} className="fc-input" type="text" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} disabled={busy} required /></>}<div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy} onClick={onCancel}>{t("Cancel")}</button><button type="submit" className="fc-button fc-button-primary" disabled={busy}>{busy ? <Loader2 size={15} className="fc-account-spinner" /> : <ShieldCheck size={15} />}{t("Confirm")}</button></div></form>;
}

export function OneTimeSecret({ id, title, label, value, saved, onStored, onHide, onNotice }: { id: string; title: string; label: string; value: string; saved: boolean; onStored: (value: boolean) => void; onHide: () => void; onNotice: (value: string) => void }) {
  useI18n();
  const [visible, setVisible] = useState(false);
  return <section className="fc-account-section fc-integration-secret" aria-label={title}><span className="fc-kicker">{t("Shown once")}</span><h3>{title}</h3><p className="fc-account-muted">{t("Store this credential in your identity provider’s secure provisioning settings. It grants access to this workspace’s directory. You cannot reveal it again.")}</p><label htmlFor={id}>{label}</label><div className="fc-integration-secret-value"><input id={id} className="fc-input" value={value} type={visible ? 'text' : 'password'} autoComplete="off" readOnly spellCheck={false} onFocus={event => event.currentTarget.select()} /><button type="button" className="fc-icon-button" aria-label={visible ? t("Hide provisioning token") : t("Show provisioning token")} onClick={() => setVisible(previous => !previous)}>{visible ? <EyeOff size={16} /> : <Eye size={16} />}</button><button type="button" className="fc-icon-button" aria-label={t("Copy provisioning token")} onClick={() => { void navigator.clipboard.writeText(value).then(() => onNotice('Provisioning token copied. Store it privately.')).catch(() => onNotice('Select and copy the provisioning token manually.')); }}><Copy size={16} /></button></div><label className="fc-account-checkbox"><input type="checkbox" checked={saved} onChange={event => onStored(event.target.checked)} />{t("I stored this token in my identity provider’s secure settings.")}</label><button type="button" className="fc-button" disabled={!saved} onClick={onHide}><Check size={15} />{t("Hide stored token")}</button></section>;
}
