import { t, useI18n, currentLocale } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Activity, AlertCircle, CheckCircle2, ChevronDown, HelpCircle, HardDrive, Loader2, Minus, RefreshCw, ShieldCheck } from 'lucide-react';
import { api } from '../lib/api';
import { operationsApi, type OperationalCheckStatus, type OperationalSecurity, type SecurityEvent, type SecurityEventOutcome } from '../lib/integrations-api';

const checkLabels: Record<string, string> = {
  public_https_origin: 'HTTPS application origin', cors_https_origins: 'Allowed browser origins',
  experimental_features: 'Experimental routes', demo_ai: 'AI demo routes',
  independent_server_keys: 'Independent server keys', operator_mfa: 'Operator authenticators',
  storage_capacity: 'Local volume headroom', mail_leases: 'Email worker leases',
  webhook_leases: 'Webhook worker leases', proof_leases: 'Version proof worker leases', sync_leases: 'File and calendar sync leases',
  workflow_leases: 'Workflow worker leases', data_leases: 'Export and retention worker leases', push_leases: 'Push delivery leases',
};
const queueLabels: Record<string, string> = { mail: 'Email', webhooks: 'Webhooks', otrust: 'Version proofs', sync: 'File and calendar sync', workflows: 'Workflows', data: 'Exports and retention', push: 'Push notifications' };
const statusLabels: Record<OperationalCheckStatus, string> = { pass: 'Ready', attention: 'Needs attention', disabled: 'Disabled', unknown: 'Not established' };
const outcomeLabels: Record<SecurityEventOutcome, string> = { success: 'Successful', denied: 'Denied', error: 'Error' };
const actionLabels: Record<string, string> = {
  'account.sign_in': 'Account sign-in', 'account.sign_out': 'Account sign-out', 'account.password_reset': 'Password reset',
  'account.password_change': 'Password change', 'account.session_revoke': 'Session revocation', 'account.sessions_revoke': 'All-session revocation',
  'account.mfa_setup': 'Authenticator setup', 'account.mfa_enable': 'Authenticator activation', 'account.mfa_disable': 'Authenticator removal',
  'account.recovery_codes_replace': 'Recovery code replacement', 'installation.role_change': 'Installation role change',
  'installation.access_change': 'Account access change', 'sso.configure': 'Workspace SSO configuration',
  'sso.test': 'Workspace SSO test', 'sso.enable': 'Workspace SSO activation', 'sso.disable': 'Workspace SSO pause',
  'directory.credential_replace': 'Directory credential replacement', 'directory.mapping_change': 'Directory access rules',
  'workforce.policy_change': 'Organization onboarding policy', 'encryption.device_revoke': 'Encryption device revocation',
  'encryption.unlock': 'Personal encryption unlock', 'sync.oauth_start': 'Provider account connection',
  'sync.conflict_resolve': 'Sync conflict resolution', 'operations.mail_test': 'Diagnostic email request',
  'operations.mail_retry': 'Email retry request',
};
const humanize = (value: string) => value.replace(/[._-]+/g, ' ').replace(/^\w/, letter => letter.toUpperCase());
const date = (value: string) => new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + 'Z').toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short' });
const space = (value: number | null) => value === null ? 'Not available' : new Intl.NumberFormat(currentLocale(), { maximumFractionDigits: 1 }).format(value / 1024 ** 3) + ' GiB';
function checkState(id: string, status: OperationalCheckStatus) {
  if (status !== 'pass') return statusLabels[status];
  if (id === 'experimental_features' || id === 'demo_ai') return 'Disabled';
  if (id === 'public_https_origin') return 'HTTPS configured';
  if (id === 'cors_https_origins') return 'Exact origins';
  if (id === 'independent_server_keys') return 'Configured';
  return 'Ready';
}

function CheckIcon({ status }: { status: OperationalCheckStatus }) {
  useI18n();
  if (status === 'pass') return <CheckCircle2 size={16} />;
  if (status === 'attention') return <AlertCircle size={16} />;
  if (status === 'disabled') return <Minus size={16} />;
  return <HelpCircle size={16} />;
}

export function OperationsSecurity() {
  useI18n();
  const [snapshot, setSnapshot] = useState<OperationalSecurity | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [events, setEvents] = useState<SecurityEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [eventsError, setEventsError] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [retention, setRetention] = useState<number | null>(null);
  const [recordLimit, setRecordLimit] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<SecurityEventOutcome | ''>('');
  const mounted = useRef(true);
  const owner = useRef(api.getAccessToken());
  const identity = useRef(api.getCurrentUserId());
  const snapshotGeneration = useRef(0);
  const eventsGeneration = useRef(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const eventsErrorRef = useRef<HTMLDivElement>(null);
  const current = () => mounted.current && owner.current === api.getAccessToken() && identity.current === api.getCurrentUserId();

  useEffect(() => {
    mounted.current = true;
    void loadSnapshot();
    return () => { mounted.current = false; snapshotGeneration.current++; eventsGeneration.current++; };
  }, []);
  useEffect(() => { void loadEvents(); }, [outcome]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (eventsError) eventsErrorRef.current?.focus(); }, [eventsError]);

  async function loadSnapshot() {
    if (!current()) return;
    const request = ++snapshotGeneration.current;
    setLoading(true); setError('');
    try {
      const result = await operationsApi.security();
      if (current() && request === snapshotGeneration.current) setSnapshot(result);
    } catch (failure) {
      if (current() && request === snapshotGeneration.current) setError(failure instanceof Error ? failure.message : t("Security status could not be loaded."));
    } finally {
      if (current() && request === snapshotGeneration.current) setLoading(false);
    }
  }

  async function loadEvents(earlier?: string) {
    if (!current()) return;
    const request = ++eventsGeneration.current;
    setEventsLoading(true); setEventsError('');
    if (!earlier) { setEvents([]); setCursor(null); }
    try {
      const result = await operationsApi.securityEvents({ cursor: earlier, outcome: outcome || undefined });
      if (!current() || request !== eventsGeneration.current) return;
      setEvents(previous => earlier ? [...previous, ...result.items.filter(item => !previous.some(existing => existing.id === item.id))] : result.items);
      setCursor(result.next_cursor); setRetention(result.retention_days); setRecordLimit(result.max_records ?? null);
    } catch (failure) {
      if (current() && request === eventsGeneration.current) setEventsError(failure instanceof Error ? failure.message : 'Security actions could not be loaded.');
    } finally {
      if (current() && request === eventsGeneration.current) setEventsLoading(false);
    }
  }

  const expired = snapshot ? Object.values(snapshot.queues).reduce((total, queue) => total + (queue.enabled ? queue.expired_leases : 0), 0) : 0;
  return <>
    <section className="fc-account-section fc-operations-security" aria-labelledby="operations-security-heading">
      <div className="fc-account-section-heading"><div><span className="fc-kicker">{t("For server operators")}</span><h3 id="operations-security-heading">{t("Security & service health")}</h3></div><button className="fc-icon-button" type="button" aria-label={t("Refresh security status")} disabled={loading} onClick={() => void loadSnapshot()}><RefreshCw size={16} /></button></div>
      <p className="fc-account-muted">{t("Review local configuration, storage and worker claims. These checks do not verify public certificates, provider delivery or off-site backups.")}</p>
      {error && <div className="fc-alert fc-account-error" ref={errorRef} tabIndex={-1} role="alert"><AlertCircle size={17} /><p>{error}</p></div>}
      {loading ? <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t("Checking security status…")}</p> : snapshot ? <>
        <dl className="fc-integration-status-grid">
          <div><dt><ShieldCheck size={14} />{t("Operator authenticators")}</dt><dd>{snapshot.operators.mfa_enabled} {t(" of ")}{snapshot.operators.active} {t(" enabled")}</dd></div>
          <div><dt><HardDrive size={14} />{t("Free local storage")}</dt><dd>{space(snapshot.storage.free_bytes)}{snapshot.storage.free_percent !== null && <small>{new Intl.NumberFormat(currentLocale(), { maximumFractionDigits: 1 }).format(snapshot.storage.free_percent)}{t("% available")}</small>}</dd></div>
          <div><dt>{t("Worker claims requiring attention")}</dt><dd>{expired}</dd></div>
          <div><dt>{t("Environment")}</dt><dd>{humanize(snapshot.environment)}</dd></div>
        </dl>
        {snapshot.blob_storage?.backend === 's3' && <p className="fc-account-muted">{t("S3 object storage is configured. Local volume capacity does not verify the remote service; run the operator storage probe to check it.")}</p>}
        <ul className="fc-operations-checks" aria-label={t("Security configuration checks")}>{snapshot.checks.map(check => <li key={check.id} data-status={check.status}><CheckIcon status={check.status} /><span>{checkLabels[check.id] || humanize(check.id)}</span><strong>{checkState(check.id, check.status)}</strong></li>)}</ul>
        <div className="fc-integration-queue-details" aria-label={t("Worker queue health")}>{Object.entries(snapshot.queues).map(([name, queue]) => <p key={name}><strong>{queueLabels[name] || humanize(name)}</strong><span>{queue.enabled ? t("{value0} due · {value1} expired claims · {value2} failed", { value0: String(queue.due), value1: String(queue.expired_leases), value2: String(queue.failed) }) : t("Not enabled")}</span></p>)}</div>
        <p className="fc-account-muted fc-integration-footnote">{t("Checked ")}{date(snapshot.checked_at)}{t(". Administrator counts and queue totals exclude message content and credentials.")}</p>
      </> : <button className="fc-button" type="button" onClick={() => void loadSnapshot()}><RefreshCw size={15} />{t("Try security status again")}</button>}
    </section>
    <section className="fc-account-section fc-operations-security" aria-labelledby="operations-events-heading">
      <div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Access & changes")}</span><h3 id="operations-events-heading">{t("Recent security actions")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh security actions")} disabled={eventsLoading} onClick={() => void loadEvents()}><RefreshCw size={16} /></button></div>
      <p className="fc-account-muted">{t("Account and administration actions with their result. Request bodies, passwords, tokens and message content stay outside this log.")}</p>
      <div className="fc-operations-filter"><label htmlFor="operations-security-outcome">{t("Show results")}</label><select className="fc-input" id="operations-security-outcome" value={outcome} onChange={event => setOutcome(event.target.value as SecurityEventOutcome | '')}><option value="">{t("All results")}</option><option value="success">{t("Successful")}</option><option value="denied">{t("Denied")}</option><option value="error">{t("Errors")}</option></select></div>
      {eventsError && <div className="fc-alert fc-account-error" ref={eventsErrorRef} tabIndex={-1} role="alert"><AlertCircle size={17} /><p>{eventsError}</p></div>}
      <ul className="fc-operations-events" aria-label={t("Security action history")}>{events.map(event => <li key={event.id}><Activity size={17} /><div><strong>{actionLabels[event.action] || humanize(event.action)}</strong><span>{date(event.created_at)} {t(" · HTTP ")}{event.status_code}</span><small>{event.actor_kind === 'directory' ? t("Directory credential") : t("Account")}{event.actor_id ? t(" · {value0}", { value0: String(event.actor_id) }) : ''}{event.workspace_id ? t(" · Workspace {value0}", { value0: String(event.workspace_id) }) : ''}{event.target_id ? t(" · Target {value0}", { value0: String(event.target_id) }) : ''}</small></div><span className={`fc-badge ${event.outcome === 'success' ? 'fc-account-success' : ''}`}>{outcomeLabels[event.outcome]}</span></li>)}</ul>
      {eventsLoading && <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t("Loading security actions…")}</p>}
      {!eventsLoading && !events.length && !eventsError && <p className="fc-account-muted">{t("No retained security actions match this view.")}</p>}
      {cursor && <button className="fc-button" type="button" disabled={eventsLoading} onClick={() => void loadEvents(cursor)}><ChevronDown size={15} />{t("Show earlier actions")}</button>}
      {eventsError && !eventsLoading && <button className="fc-button" type="button" onClick={() => void loadEvents()}><RefreshCw size={15} />{t("Try security actions again")}</button>}
      {retention !== null && <p className="fc-account-muted fc-integration-footnote">{t("Up to ")}{retention} {t(" days")}{recordLimit !== null ? `, newest ${new Intl.NumberFormat(currentLocale()).format(recordLimit)} records` : ''}{t(". The log records server-observed actions; database administrators control its storage.")}</p>}
    </section>
  </>;
}
