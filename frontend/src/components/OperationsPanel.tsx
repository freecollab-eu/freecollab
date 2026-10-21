import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Activity, AlertCircle, CheckCircle2, Download, Loader2, Mail, RefreshCw, Send } from 'lucide-react';
import { api } from '../lib/api';
import type { AccountSecurity } from '../lib/account-api';
import { operationsApi, type MailDelivery, type OperationsStatus } from '../lib/integrations-api';
import { OperationsSecurity } from './OperationsSecurity';
import { DeploymentReadinessPanel } from './DeploymentReadinessPanel';

const date = (value: string | null) => value ? new Date(value).toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short' }) : 'No heartbeat yet';
const issues: Record<string, string> = {
  delivery_disabled: 'Email delivery is disabled.', mail_disabled: 'Email delivery is disabled.',
  smtp_host_missing: 'The SMTP server is missing.', smtp_sender_missing: 'A sender address is missing.',
  sender_missing: 'A sender address is missing.', smtp_tls_disabled: 'SMTP transport encryption is disabled.',
  smtp_authentication_missing: 'SMTP authentication has not been configured.',
  public_url_not_https: 'The public application URL needs HTTPS before external use.',
};
export function OperationsPanel({ security }: { security: AccountSecurity | null }) {
  useI18n();
  const [status, setStatus] = useState<OperationsStatus | null>(null);
  const [deliveries, setDeliveries] = useState<MailDelivery[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [action, setAction] = useState<MailDelivery | 'test' | null>(null);
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const mounted = useRef(true);
  const owner = useRef(api.getAccessToken());
  const identity = useRef(api.getCurrentUserId());
  const generation = useRef(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const current = () => mounted.current && owner.current === api.getAccessToken() && identity.current === api.getCurrentUserId();
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; generation.current++; }; }, []);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (action) passwordRef.current?.focus(); }, [action]);
  async function load() {
    const request = ++generation.current; setLoading(true); setError('');
    try {
      const [state, records] = await Promise.all([operationsApi.status(), operationsApi.deliveries()]);
      if (current() && request === generation.current) { setStatus(state); setDeliveries(records.items); }
    } catch (failure) { if (current() && request === generation.current) setError(failure instanceof Error ? failure.message : t("Operations status could not be loaded.")); }
    finally { if (current() && request === generation.current) setLoading(false); }
  }
  async function run(key: string, operation: () => Promise<void>) {
    if (busy || !current()) return; setBusy(key); setError(''); setNotice('');
    try { await operation(); } catch (failure) { if (current()) setError(failure instanceof Error ? failure.message : t("The operation could not be completed.")); }
    finally { if (current()) setBusy(''); }
  }
  function choose(next: MailDelivery | 'test' | null) { setAction(next); setPassword(''); setCode(''); setError(''); setNotice(''); }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!action) return; const selected = action;
    await run('mail', async () => {
      if (selected === 'test') await operationsApi.test(password, code.trim() || undefined);
      else await operationsApi.retry(selected.id, password, code.trim() || undefined);
      if (!current()) return; choose(null); setNotice(selected === 'test' ? t("A single test email is queued for your verified account address. Check the delivery result below.") : t("The failed delivery is queued for another attempt.")); await load();
    });
  }
  async function metrics() {
    await run('metrics', async () => {
      const blob = await api.requestBlob('/operations/metrics'); if (!current()) return;
      const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'freecollab-metrics.txt'; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(t("Operations metrics downloaded. Keep access to the authenticated endpoint private."));
    });
  }
  return <div>
    <DeploymentReadinessPanel />
    <OperationsSecurity />
    <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("For server operators")}</span><h3>{t("Email & operations")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh operations status")} disabled={loading || !!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("Check delivery, workers and queues without opening private account messages. Only installation administrators can see this panel.")}</p>
      {error && <div ref={errorRef} tabIndex={-1} className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}{notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
      {loading ? <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t("Checking services…")}</p> : status ? <>
        <dl className="fc-integration-status-grid"><div><dt>{t("Email transport")}</dt><dd>{status.mail.mode}</dd></div><div><dt>{t("Configuration")}</dt><dd>{status.mail.configured ? t("Configured") : t("Needs attention")}</dd></div><div><dt>{t("Transport encryption")}</dt><dd>{typeof status.mail.tls === 'boolean' ? status.mail.tls ? t("Enabled") : t("Disabled") : status.mail.tls}</dd></div><div><dt>{t("Mail worker")}</dt><dd>{status.worker.status}</dd></div><div><dt>{t("Database")}</dt><dd>{status.database.reachable ? t("Available") : t("Unavailable")}</dd></div>{status.integration_worker && <div><dt>{t("Integration worker")}</dt><dd>{status.integration_worker.enabled ? status.integration_worker.status : t("Not enabled")}</dd></div>}</dl>
        {status.mail.issues.length > 0 && <ul className="fc-integration-issues">{status.mail.issues.map(issue => <li key={issue}><AlertCircle size={14} /><span>{issues[issue] || issue.replace(/_/g, ' ')}</span></li>)}</ul>}
        <p className="fc-account-muted">{t("Mail worker last seen: ")}{date(status.worker.last_seen_at)}</p>
        <div className="fc-integration-queue-grid">{Object.entries(status.queue).filter(([key]) => ['pending', 'processing', 'failed', 'delivered'].includes(key)).map(([key, count]) => <div key={key}><strong>{count ?? 0}</strong><span>{key}</span></div>)}</div>
        {status.integration_queues && <div className="fc-integration-queue-details">{Object.entries(status.integration_queues).map(([name, counts]) => <p key={name}><strong>{name === 'otrust' ? t("Version proofs") : name === 'webhooks' ? t("Webhooks") : name}</strong><span>{counts.pending || 0} {t(" pending · ")}{counts.processing || 0} {t(" processing · ")}{counts.failed || 0} {t(" failed")}</span></p>)}</div>}
        <div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy || status.mail.mode === 'disabled' || !security?.email_verified} onClick={() => choose('test')}><Send size={15} />{t("Send test email")}</button><button type="button" className="fc-button" disabled={!!busy} onClick={() => void metrics()}><Download size={15} />{t("Download metrics")}</button></div>
        {!security?.email_verified && <p className="fc-account-muted">{t("Verify your own account email before sending a test message.")}</p>}
      </> : <button type="button" className="fc-button" onClick={() => void load()}><RefreshCw size={15} />{t("Try again")}</button>}
    </section>
    {action && <form className="fc-account-reauth" aria-label={action === 'test' ? t("Send test email") : t("Retry email delivery")} onSubmit={submit}><h3>{action === 'test' ? t("Send one test email?") : t("Retry this failed delivery?")}</h3><p>{action === 'test' ? t("The email goes to your verified FreeCollab account address using the configured transport.") : t("The original recipient and purpose stay unchanged. The worker will attempt delivery again.")}</p><label htmlFor="operations-password">{t("Confirm with your password")}</label><input ref={passwordRef} id="operations-password" className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} disabled={!!busy} required />{security?.mfa_enabled && <><label htmlFor="operations-code">{t("Authentication or recovery code")}</label><input id="operations-code" className="fc-input" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} disabled={!!busy} required /></>}<div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => choose(null)}>{t("Cancel")}</button><button type="submit" className="fc-button fc-button-primary" disabled={!!busy}>{busy === 'mail' ? <Loader2 size={15} className="fc-account-spinner" /> : <Mail size={15} />}{action === 'test' ? t("Queue test email") : t("Queue delivery retry")}</button></div></form>}
    <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{t("Email delivery history")}</h3><Activity size={17} /></div><p className="fc-account-muted">{t("Status and timing only. Recipient addresses, message bodies and account tokens are excluded.")}</p><ul className="fc-integration-deliveries">{deliveries.map(delivery => <li key={delivery.id}><div><strong>{t("Email delivery")}</strong><span>{date(delivery.created_at)} · {delivery.attempts} {t(" attempts")}</span>{delivery.failure && <p>{delivery.failure}</p>}</div><div><span className={`fc-badge ${delivery.status === 'delivered' ? 'fc-account-success' : ''}`}>{delivery.status}</span>{delivery.retryable && <button type="button" className="fc-account-link" disabled={!!busy} onClick={() => choose(delivery)}><RefreshCw size={13} />{t("Retry delivery")}</button>}</div></li>)}</ul>{!loading && !deliveries.length && <p className="fc-account-muted">{t("No email deliveries recorded yet.")}</p>}</section>
  </div>;
}
