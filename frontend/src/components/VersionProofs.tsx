import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Download, Fingerprint, Loader2, RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import { proofsApi, proofTarget, timestampDigest, type ProofTarget, type VersionProof } from '../lib/proofs-api';
import '../styles/version-proofs.css';

const describe = (error: unknown) => error instanceof Error ? error.message : 'This timestamp could not be opened.';
const stateLabel: Record<VersionProof['state'], string> = {
  queued: 'Queued for timestamping', processing: 'Contacting timestamp provider', pending: 'Waiting for timestamp proof',
  proof_available: 'Proof available · commitment checked', failed: 'Timestamp request needs attention',
};

/** Inline panel: no decrypted bytes, filename or decision text go to OTRUST. */
export function VersionProofs(target: ProofTarget) {
  useI18n();
  const context = proofTarget(target);
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<VersionProof[]>([]);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [provider, setProvider] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [digest, setDigest] = useState('');
  const [consent, setConsent] = useState(false);
  const [notice, setNotice] = useState('');
  const generation = useRef(0);
  const urls = useRef(new Set<string>());
  const current = (run: number, token: string | null) => run === generation.current && token === api.getAccessToken();
  useEffect(() => {
    ++generation.current; setOpen(false); setRecords([]); setDigest(''); setConsent(false); setError(''); setNotice(''); setBusy('');
    return () => { ++generation.current; for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear(); };
  }, [context.channelId, context.kind, context.id]);

  async function run(kind: string, action: (lifecycle: number, token: string | null) => Promise<void>) {
    const lifecycle = generation.current, token = api.getAccessToken();
    setBusy(kind); setError(''); setNotice('');
    try { await action(lifecycle, token); }
    catch (failure) { if (current(lifecycle, token)) setError(describe(failure)); }
    finally { if (current(lifecycle, token)) setBusy(''); }
  }
  async function load(lifecycle: number, token: string | null) {
    const [status, items] = await Promise.all([proofsApi.status(), proofsApi.list(target)]);
    if (current(lifecycle, token)) { setEnabled(status.enabled); setProvider(status.provider_url); setRecords(items); }
  }

  return <section className="fc-version-proofs">
    <button type="button" className="fc-text-button" aria-expanded={open} disabled={!!busy} onClick={() => {
      if (open) { setOpen(false); setDigest(''); setConsent(false); return; }
      setOpen(true); void run('load', load);
    }}><Fingerprint size={13} />{t("Timestamp proof")}</button>
    {open && <div className="fc-version-proof-panel" aria-label={t("Timestamp proof for this {value0} version", { value0: String(context.kind) })}>
      <h4>{t("A proof for this exact version.")}</h4>
      <p>{t("A timestamp commits to the version’s SHA-256 hash. It does not prove who wrote it, legal identity or that the content is true.")}</p>
      {error && <p role="alert" className="fc-version-proof-error">{error}</p>}{notice && <p role="status">{notice}</p>}
      {busy === 'load' && <p role="status"><Loader2 size={14} className="fc-collaboration-spinner" />{t("Loading timestamp records…")}</p>}
      {enabled === false && <p>{t("Timestamping is not configured on this server. Existing receipts remain available to channel members.")}</p>}
      {records.length > 0 && <ul>{records.map(record => <li key={record.id}>
        <strong>{stateLabel[record.state]}</strong>
        <code>{record.sha256}</code>
        {digest && <p>{record.sha256 === digest ? t("Matches the bytes checked on this device.") : t("This receipt has a different digest from the bytes checked on this device.")}</p>}
        {record.content_binding === 'client_asserted' && <p>{t("Encrypted content: this digest was supplied by a channel member’s device. Check it locally against the exact version.")}</p>}
        {record.commitment_checked && <p>{t("The OpenTimestamps file commits to this digest. Independent Bitcoin-chain verification has not been performed.")}</p>}
        {record.receipt_id && <small>{t("Receipt ")}{record.receipt_id}</small>}
        {record.failure && <p role="status">{record.failure}</p>}
        <div className="fc-version-proof-actions">
          {record.proof_available && <button type="button" className="fc-text-button" disabled={!!busy} onClick={() => void run('download', async (lifecycle, token) => {
            const blob = await proofsApi.download(record.id); if (!current(lifecycle, token)) return;
            const url = URL.createObjectURL(blob); urls.current.add(url);
            const anchor = document.createElement('a'); anchor.href = url; anchor.download = `freecollab-version-${record.id}.ots`; anchor.click();
            window.setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); }, 1000);
          })}><Download size={13} />{t("Download .ots proof")}</button>}
          {enabled && (record.requested_by === api.getCurrentUserId()) && <button type="button" className="fc-text-button" disabled={!!busy} onClick={() => void run('retry', async (lifecycle, token) => {
            await proofsApi.refresh(record.id); await load(lifecycle, token);
            if (current(lifecycle, token)) setNotice(t("Timestamp check queued. Provider backoff is preserved."));
          })}><RefreshCw size={13} />{record.state === 'failed' ? t("Retry timestamp") : t("Check for updated proof")}</button>}
        </div>
      </li>)}</ul>}
      <div className="fc-version-proof-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => void run('hash', async (lifecycle, token) => {
        const value = await timestampDigest(target); if (current(lifecycle, token)) { setDigest(value); setConsent(false); }
      })}>{busy === 'hash' ? <Loader2 size={14} className="fc-collaboration-spinner" /> : <Fingerprint size={14} />}{t("Check this version’s hash locally")}</button><button type="button" className="fc-text-button" disabled={!!busy} onClick={() => void run('load', load)}><RefreshCw size={13} />{t("Refresh")}</button></div>
      {digest && <div className="fc-version-proof-consent"><label>{t("SHA-256 of this exact version")}<code>{digest}</code></label>
        {enabled && <><p>{t("Only this hash and the source value “api” will be sent to ")}{provider}{t(". The provider can correlate a hash with known content. Filename, channel title and document content are excluded.")}</p>
          <label className="fc-account-checkbox"><input type="checkbox" checked={consent} disabled={!!busy} onChange={event => setConsent(event.target.checked)} />{t("I want to share this hash with the timestamp provider.")}</label>
          <button type="button" className="fc-button" disabled={!!busy || !consent} onClick={() => void run('request', async (lifecycle, token) => {
            await proofsApi.request(target, digest); await load(lifecycle, token);
            if (current(lifecycle, token)) { setConsent(false); setNotice(t("Timestamp requested for this exact version. Anchoring may take 24–48 hours; this is not a verified Bitcoin timestamp yet.")); }
          })}>{busy === 'request' ? <Loader2 size={14} className="fc-collaboration-spinner" /> : <Fingerprint size={14} />}{t("Request timestamp")}</button></>}
      </div>}
    </div>}
  </section>;
}
