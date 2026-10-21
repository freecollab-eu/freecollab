import { t, useI18n, currentLocale } from '../i18n';
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertCircle, Check, CheckCircle2, Copy, ExternalLink, Loader2, Pause, Play, Plus, RefreshCw, RotateCw, Send, Settings2, Trash2, Webhook as WebhookIcon, X } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { webhookApi, type Webhook, type WebhookDelivery } from '../lib/integrations-api';

type Guard = 'busy' | 'secret' | null;
interface Props { workspaceId: string; channels: Channel[]; onGuardChange: (guard: Guard) => void; onDirtyChange?: (dirty: boolean) => void }
const date = (value: string | null) => value ? new Date(value).toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short' }) : 'Not yet';
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'Please try again.';
export function WebhookConnections({ workspaceId, channels, onGuardChange, onDirtyChange }: Props) {
  useI18n();
  const [records, setRecords] = useState<Webhook[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [events, setEvents] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState<Webhook | 'new' | null>(null);
  const [name, setName] = useState('');
  const [url, setURL] = useState('');
  const [selectedChannels, setSelectedChannels] = useState<string[]>([]);
  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);
  const [selected, setSelected] = useState<Webhook | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [loadingDeliveries, setLoadingDeliveries] = useState(false);
  const [confirm, setConfirm] = useState<'rotate' | 'delete' | null>(null);
  const [secret, setSecret] = useState('');
  const [secretSaved, setSecretSaved] = useState(false);
  const [secretName, setSecretName] = useState('');
  const mounted = useRef(true);
  const identity = useRef(api.getCurrentUserId());
  const session = useRef(api.getAccessToken());
  const generation = useRef(0);
  const deliveryGeneration = useRef(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const formName = useRef<HTMLInputElement>(null);
  const current = useCallback(() => mounted.current && identity.current === api.getCurrentUserId() && session.current === api.getAccessToken(), []);
  const grantable = channels.filter(channel => channel.role === 'admin' && channel.type !== 'direct');
  const sameSelection = (left: string[], right: string[]) => left.length === right.length && left.every(value => right.includes(value));
  const dirty = !!editing && (name !== (editing === 'new' ? '' : editing.name) || !!url ||
    !sameSelection(selectedChannels, editing === 'new' ? [] : editing.channel_ids) ||
    !sameSelection(selectedEvents, editing === 'new' ? [] : editing.event_types));
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => { onDirtyChange?.(false); }, [onDirtyChange]);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; generation.current++; deliveryGeneration.current++; onGuardChange(null); }; }, [workspaceId]);
  useEffect(() => { onGuardChange(busy ? 'busy' : secret && !secretSaved ? 'secret' : null); }, [busy, secret, secretSaved, onGuardChange]);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (editing) formName.current?.focus(); }, [editing]);
  useEffect(() => { if (selected) void loadDeliveries(selected.id); else { deliveryGeneration.current++; setDeliveries([]); } }, [selected?.id]);

  async function load() {
    const request = ++generation.current; setLoading(true); setError('');
    try {
      const data = await webhookApi.list(workspaceId);
      if (!current() || request !== generation.current) return;
      setRecords(data.webhooks); setEnabled(data.enabled); setEvents(data.event_types);
      setSelected(previous => previous ? data.webhooks.find(record => record.id === previous.id) || null : null);
    } catch (failure) { if (current() && request === generation.current) setError(describe(failure)); }
    finally { if (current() && request === generation.current) setLoading(false); }
  }
  async function loadDeliveries(id: string) {
    const request = ++deliveryGeneration.current; setLoadingDeliveries(true);
    try { const data = await webhookApi.deliveries(workspaceId, id); if (current() && request === deliveryGeneration.current) setDeliveries(data.deliveries); }
    catch (failure) { if (current() && request === deliveryGeneration.current) { setDeliveries([]); setError(describe(failure)); } }
    finally { if (current() && request === deliveryGeneration.current) setLoadingDeliveries(false); }
  }
  async function run(key: string, operation: () => Promise<void>) {
    if (busy || !current()) return; setBusy(key); setError(''); setNotice(''); onGuardChange('busy');
    try { await operation(); } catch (failure) { if (current()) setError(describe(failure)); }
    finally { if (current()) setBusy(''); }
  }
  function changed(record: Webhook) { setRecords(previous => previous.some(item => item.id === record.id) ? previous.map(item => item.id === record.id ? record : item) : [record, ...previous]); setSelected(record); }
  function showSecret(value: string, record: Webhook) { setSecret(value); setSecretSaved(false); setSecretName(record.name); onGuardChange('secret'); }
  function edit(record: Webhook | 'new') {
    setEditing(record); setName(record === 'new' ? '' : record.name); setURL('');
    setSelectedChannels(record === 'new' ? [] : record.channel_ids); setSelectedEvents(record === 'new' ? [] : record.event_types);
    setError(''); setNotice(''); setConfirm(null);
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!editing) return;
    if (!selectedChannels.length || !selectedEvents.length) { setError(t("Choose at least one channel and one event.")); return; }
    await run('save', async () => {
      const common = { name: name.trim(), channel_ids: selectedChannels, event_types: selectedEvents };
      if (editing === 'new') {
        const response = await webhookApi.create(workspaceId, { ...common, url: url.trim() });
        if (!current()) return; changed(response.webhook); showSecret(response.secret, response.webhook); setNotice(t("Webhook created. Save its signing secret before leaving this view."));
      } else {
        const response = await webhookApi.update(workspaceId, editing.id, { ...common, ...(url.trim() ? { url: url.trim() } : {}) });
        if (!current()) return; changed(response); setNotice(t("Webhook settings saved. Stale queued deliveries are cancelled when its destination or grants change."));
      }
      setEditing(null); setURL('');
    });
  }
  async function confirmChange() {
    if (!selected || !confirm) return;
    const record = selected, action = confirm;
    await run(action, async () => {
      if (action === 'delete') { await webhookApi.remove(workspaceId, record.id); if (!current()) return; setRecords(previous => previous.filter(item => item.id !== record.id)); setSelected(null); setNotice(t("Webhook removed. Its pending deliveries are cancelled.")); }
      else { const response = await webhookApi.rotate(workspaceId, record.id); if (!current()) return; changed(response.webhook); showSecret(response.secret, response.webhook); setNotice(t("Signing secret rotated. Update your receiver with the new secret.")); }
      setConfirm(null);
    });
  }
  async function copySecret() {
    try { await navigator.clipboard.writeText(secret); if (current()) setNotice(t("Signing secret copied. Store it securely in your receiving service.")); }
    catch { if (current()) setNotice(t("Select and copy the signing secret below.")); }
  }
  function toggle(value: string, selected: string[], change: (next: string[]) => void) { change(selected.includes(value) ? selected.filter(item => item !== value) : [...selected, value]); }

  return <div className="fc-integration-webhooks">
    <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Small events, useful connections")}</span><h3>{t("Webhooks & n8n")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh webhooks")} disabled={loading || !!busy} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("Send signed activity events to a public HTTPS receiver. Payloads contain event types and identifiers; message text, document names and decrypted content stay out of deliveries.")}</p>
      {error && <div ref={errorRef} tabIndex={-1} className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div>}
      {notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
      {loading ? <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t("Loading connections…")}</p> : <>
        {!enabled && <div className="fc-integration-empty"><WebhookIcon size={25} /><h3>{t("Webhooks are not enabled on this server.")}</h3><p>{t("An operator can configure outbound delivery and its worker. Existing team content remains available.")}</p></div>}
        {enabled && !editing && !secret && <button type="button" className="fc-button fc-button-primary" disabled={!!busy || !grantable.length} onClick={() => edit('new')}><Plus size={15} />{t("Create webhook")}</button>}
        {enabled && !grantable.length && <p className="fc-account-muted">{t("A webhook needs a channel you administer. Direct messages cannot be granted to workspace webhooks.")}</p>}
        <ul className="fc-integration-hook-list">{records.map(record => <li key={record.id}><button type="button" className="fc-integration-hook-select" aria-pressed={selected?.id === record.id} disabled={!!busy || !!secret} onClick={() => { setSelected(record); setConfirm(null); setEditing(null); }}><WebhookIcon size={20} /><span><strong>{record.name}</strong><small>{record.origin}</small><small>{record.channel_ids.length} {record.channel_ids.length === 1 ? t("channel") : t("channels")} · {record.event_types.length} {t(" event types")}</small></span><span className={`fc-badge ${record.enabled ? 'fc-account-success' : ''}`}>{record.enabled ? t("Active") : t("Paused")}</span></button></li>)}</ul>
        {enabled && !records.length && !editing && <p className="fc-account-muted">{t("No webhooks yet. Connect a receiver such as an n8n Webhook workflow to begin.")}</p>}
      </>}
    </section>

    {secret && <section className="fc-account-section fc-integration-secret" aria-label={t("Webhook signing secret")}><span className="fc-kicker">{t("Shown once")}</span><h3>{t("Save the signing secret")}</h3><p className="fc-account-muted">{t("For ")}{secretName}{t(". Add this secret to your receiver and verify each request signature before accepting an event.")}</p><label htmlFor="webhook-signing-secret">{t("Signing secret")}</label><div className="fc-integration-secret-value"><input id="webhook-signing-secret" className="fc-input" type="text" value={secret} readOnly autoComplete="off" spellCheck={false} onFocus={event => event.currentTarget.select()} /><button type="button" className="fc-icon-button" aria-label={t("Copy webhook signing secret")} onClick={() => void copySecret()}><Copy size={16} /></button></div><label className="fc-account-checkbox"><input type="checkbox" checked={secretSaved} onChange={event => setSecretSaved(event.target.checked)} />{t("I saved this secret in my receiver’s secure settings.")}</label><button type="button" className="fc-button" disabled={!secretSaved} onClick={() => { setSecret(''); setSecretSaved(false); setNotice(t("Signing secret saved. You can rotate it if a replacement is needed.")); }}><Check size={15} />{t("Hide saved secret")}</button></section>}

    {editing && <section className="fc-account-section"><form className="fc-account-form" aria-label={editing === 'new' ? t("Create webhook") : t("Edit webhook")} onSubmit={save}>
      <div className="fc-account-section-heading"><h3>{editing === 'new' ? t("New webhook") : t("Edit connection")}</h3><button type="button" className="fc-icon-button" disabled={!!busy} aria-label={t("Cancel webhook editing")} onClick={() => setEditing(null)}><X size={16} /></button></div>
      <label htmlFor="webhook-name">{t("Connection name")}</label><input ref={formName} id="webhook-name" className="fc-input" value={name} onChange={event => setName(event.target.value)} maxLength={80} placeholder={t("Studio automations")} disabled={!!busy} required />
      <label htmlFor="webhook-url">{t("HTTPS receiver URL")}</label><input id="webhook-url" className="fc-input" type="url" value={url} onChange={event => setURL(event.target.value)} maxLength={2048} placeholder={editing === 'new' ? 'https://automations.example.com/webhook/…' : t("{value0}/… (leave blank to keep)", { value0: String(editing.origin) })} disabled={!!busy} required={editing === 'new'} autoComplete="off" spellCheck={false} />
      <p className="fc-account-muted">{t("Use a public HTTPS endpoint on port 443. Receiver URLs are stored privately and are never included in delivery logs.")}</p>
      <fieldset className="fc-integration-checks"><legend>{t("Channels you explicitly grant")}</legend>{grantable.map(channel => <label key={channel.id}><input type="checkbox" checked={selectedChannels.includes(channel.id)} onChange={() => toggle(channel.id, selectedChannels, setSelectedChannels)} disabled={!!busy} /><span>{channel.name || t("Channel")}{channel.secure && <small>{t("End-to-end encrypted · identifiers only")}</small>}</span></label>)}</fieldset>
      <fieldset className="fc-integration-checks"><legend>{t("Events to send")}</legend>{events.map(event => <label key={event}><input type="checkbox" checked={selectedEvents.includes(event)} onChange={() => toggle(event, selectedEvents, setSelectedEvents)} disabled={!!busy} /><span>{event}</span></label>)}</fieldset>
      <div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => setEditing(null)}>{t("Cancel")}</button><button type="submit" className="fc-button fc-button-primary" disabled={!!busy || !name.trim() || !selectedChannels.length || !selectedEvents.length || (editing === 'new' && !url.trim())}>{busy === 'save' ? <Loader2 size={15} className="fc-account-spinner" /> : <Check size={15} />}{editing === 'new' ? t("Create connection") : t("Save connection")}</button></div>
    </form></section>}

    {selected && !editing && <section className="fc-account-section" aria-label={t("Selected webhook")}><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Connection details")}</span><h3>{selected.name}</h3></div><span className="fc-badge">{t("Secret ")}{selected.generation}</span></div><p className="fc-account-muted">{selected.origin}</p>
      <div className="fc-integration-action-grid"><button type="button" className="fc-button" disabled={!!busy || !!secret || !enabled} onClick={() => edit(selected)}><Settings2 size={14} />{t("Edit")}</button><button type="button" className="fc-button" disabled={!!busy || !!secret || (!enabled && !selected.enabled)} onClick={() => void run('toggle', async () => { const record = await webhookApi.update(workspaceId, selected.id, { enabled: !selected.enabled }); if (current()) { changed(record); setNotice(record.enabled ? t("Connection resumed.") : t("Connection paused.")); } })}>{selected.enabled ? <Pause size={14} /> : <Play size={14} />}{selected.enabled ? t("Pause") : t("Resume")}</button><button type="button" className="fc-button" disabled={!!busy || !!secret || !selected.enabled || !enabled} onClick={() => void run('test', async () => { const result = await webhookApi.test(workspaceId, selected.id); if (current()) { setNotice(result.queued ? t("Test queued. Refresh delivery history to see the receiver’s response.") : t("The test could not be queued.")); await loadDeliveries(selected.id); } })}><Send size={14} />{t("Send test event")}</button><button type="button" className="fc-button" disabled={!!busy || !!secret || !enabled} onClick={() => setConfirm('rotate')}><RotateCw size={14} />{t("Rotate secret")}</button><button type="button" className="fc-account-link fc-account-danger" disabled={!!busy || !!secret} onClick={() => setConfirm('delete')}><Trash2 size={14} />{t("Remove connection")}</button></div>
      {confirm && <div className="fc-account-inline-confirm"><h3>{confirm === 'rotate' ? t("Replace this signing secret?") : t("Remove this connection?")}</h3><p>{confirm === 'rotate' ? t("The old secret stops being used. Pending deliveries for its previous configuration are cancelled. Save the replacement in your receiver.") : t("New events stop and pending deliveries are cancelled. Events already received by the destination cannot be taken back.")}</p><div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => setConfirm(null)}>{t("Keep current settings")}</button><button type="button" className="fc-button" disabled={!!busy} onClick={() => void confirmChange()}>{busy && <Loader2 size={14} className="fc-account-spinner" />}{confirm === 'rotate' ? t("Replace secret") : t("Remove webhook")}</button></div></div>}
      <div className="fc-account-section-heading fc-integration-delivery-heading"><h3>{t("Delivery history")}</h3><button type="button" className="fc-icon-button" aria-label={t("Refresh webhook delivery history")} disabled={!!busy || loadingDeliveries} onClick={() => void loadDeliveries(selected.id)}><RefreshCw size={16} /></button></div>
      <p className="fc-account-muted">{t("Queued is separate from delivered. Delivery succeeds only after an HTTP success response.")}</p>
      {loadingDeliveries ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading deliveries…")}</p> : deliveries.length ? <ul className="fc-integration-deliveries">{deliveries.map(delivery => <li key={delivery.id}><div><strong>{delivery.event_type}</strong><span>{date(delivery.created_at)} · {delivery.attempts} {t(" attempts")}{delivery.http_status ? t(" · HTTP {value0}", { value0: String(delivery.http_status) }) : ''}</span>{delivery.failure && <p>{delivery.failure}</p>}</div><div><span className={`fc-badge ${delivery.status === 'delivered' ? 'fc-account-success' : ''}`}>{delivery.status}</span>{['failed', 'pending'].includes(delivery.status) && <button type="button" className="fc-account-link" disabled={!!busy || !enabled || !selected.enabled} onClick={() => void run(delivery.id, async () => { await webhookApi.retry(workspaceId, selected.id, delivery.id); if (current()) { await loadDeliveries(selected.id); setNotice(t("Delivery queued for another attempt.")); } })}><RefreshCw size={12} />{t("Retry")}</button>}</div></li>)}</ul> : <p className="fc-account-muted">{t("No deliveries yet. Send a test event to check your connection.")}</p>}
      <details className="fc-integration-help"><summary>{t("Connect an n8n workflow")}</summary><p>{t("Add an n8n Webhook trigger using POST, copy its production HTTPS URL into this connection, then verify the FreeCollab timestamp and HMAC signature before running workflow actions. Match events by their event ID to avoid duplicate work during retries.")}</p><a href="https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/" target="_blank" rel="noopener noreferrer">{t("n8n Webhook documentation")}<ExternalLink size={12} /></a></details>
    </section>}
  </div>;
}
