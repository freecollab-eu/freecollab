import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Bell, Download, Loader2, RefreshCw, ShieldCheck, Smartphone, Trash2, Wifi, WifiOff } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { OfflineLibrary } from './OfflineLibrary';
import { NativePushSettings } from './NativePushSettings';
import { disablePush, installApplication, mobileCapabilities, mobileDrafts, mobileOutbox, subscribePush, type QueuedMessage, type PrivateDraft } from '../mobile';
import '../styles/mobile.css';

interface Device { id: string; created_at: string; confirmed: boolean; active: boolean; current_session: boolean }
const describe = (error: unknown) => error instanceof Error ? error.message : 'The mobile action could not be completed.';

export function MobileSettings({ channel }: { channel?: Channel }) {
  useI18n();
  const [capabilities, setCapabilities] = useState(mobileCapabilities), [queue, setQueue] = useState<QueuedMessage[]>([]);
  const [devices, setDevices] = useState<Device[]>([]), [configured, setConfigured] = useState(false);
  const [drafts, setDrafts] = useState<PrivateDraft[]>([]), [editing, setEditing] = useState<PrivateDraft | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState('');
  const identity = useRef(api.getAccessToken()), generation = useRef(0), errorRef = useRef<HTMLDivElement>(null);
  const current = (request: number) => request === generation.current && identity.current === api.getAccessToken();
  async function load() {
    const request = generation.current; setCapabilities(mobileCapabilities());
    try { const rows = await mobileOutbox.list(); if (current(request)) setQueue(rows); } catch { /* A signed-out browser holds no visible private queue. */ }
    try { const rows = await mobileDrafts.list(); if (current(request)) setDrafts(rows); } catch { /* Keep online controls available if private storage is unavailable. */ }
    if (!navigator.onLine) return;
    try {
      const [config, subscriptions] = await Promise.all([api.request<{ enabled: boolean }>('/mobile/config'), api.request<{ items: Device[] }>('/mobile/subscriptions')]);
      if (current(request)) { setConfigured(config.enabled); setDevices(subscriptions.items); }
    } catch (failure) { if (current(request)) setError(describe(failure)); }
  }
  useEffect(() => {
    const update = () => { void load(); };
    const accountChanged = () => {
      if (identity.current === api.getAccessToken()) return;
      generation.current++; identity.current = api.getAccessToken();
      setQueue([]); setDrafts([]); setEditing(null); setDevices([]); setConfigured(false); setError(''); setNotice(''); setBusy('');
      if (api.getCurrentUserId()) void load();
    };
    void load(); window.addEventListener('freecollab:mobile-state', update);
    window.addEventListener('freecollab:auth-changed', accountChanged); window.addEventListener('storage', accountChanged);
    return () => { generation.current++; window.removeEventListener('freecollab:mobile-state', update); window.removeEventListener('freecollab:auth-changed', accountChanged); window.removeEventListener('storage', accountChanged); };
  }, []);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  async function run(key: string, operation: () => Promise<void>) {
    if (busy) return; const request = generation.current; setBusy(key); setError(''); setNotice('');
    try { await operation(); if (current(request)) await load(); }
    catch (failure) { if (current(request)) setError(describe(failure)); }
    finally { if (current(request)) setBusy(''); }
  }
  return <div className="fc-mobile-settings">
    {error && <div className="fc-alert fc-account-error" role="alert" tabIndex={-1} ref={errorRef}>{error}</div>}
    {notice && <p className="fc-alert" role="status">{notice}</p>}
    <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Work at your own pace")}</span><h3>{t("Mobile & offline")}</h3></div><Smartphone size={22} /></div>
      <p className="fc-account-muted">{t("Install FreeCollab on your home screen. Drafts and queued messages are encrypted privately on this browser and removed when you sign out.")}</p>
      <div className="fc-mobile-state"><span>{capabilities.online ? <Wifi size={16} /> : <WifiOff size={16} />}{capabilities.online ? t("Online") : t("Offline")}</span><span>{capabilities.installed ? t("Installed application") : t("Browser application")}</span></div>
      <button className="fc-button" type="button" disabled={!!busy || capabilities.installed} onClick={() => void run('install', async () => { await installApplication(); })}><Download size={16} />{capabilities.installed ? t("Installed") : t("Install FreeCollab")}</button>
      {!capabilities.installPrompt && !capabilities.installed && <p className="fc-account-muted">{t("On iPhone or iPad, use Share → Add to Home Screen. Other browsers provide an Install app action when supported. Open the app online once before using its offline shell.")}</p>}
    </section>
    <section className="fc-account-section"><h3>{t("Private drafts")}</h3><p className="fc-account-muted">{t("Drafts work after an offline reload independently of your optional Offline library. Channel identifiers are shown here until you reopen a channel online.")}</p>
      {!drafts.length && <p className="fc-account-muted">{t("No private drafts are saved on this device.")}</p>}
      <ul className="fc-mobile-queue" aria-label={t("Private drafts")}>{drafts.map(draft => <li key={draft.channelId}><div><strong>{t("Draft")}</strong><small>{t("Channel ")}{draft.channelId}</small><p>{draft.content.slice(0, 180)}{draft.content.length > 180 ? '…' : ''}</p></div><div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => setEditing({ ...draft })}>{t("Edit draft")}</button><button className="fc-icon-button" type="button" disabled={!!busy} aria-label={t("Discard private draft")} onClick={() => void run('discard-draft', () => mobileDrafts.clear(draft.channelId))}><Trash2 size={16} /></button></div></li>)}</ul>
      {editing && <form onSubmit={event => { event.preventDefault(); const draft = editing; void run('save-draft', async () => { await mobileDrafts.save(draft.channelId, draft.content, draft.secure); setEditing(null); setNotice(t("Private draft saved on this device.")); }); }}><label htmlFor="mobile-private-draft">{t("Private draft text")}</label><textarea id="mobile-private-draft" className="fc-input" value={editing.content} maxLength={20000} rows={6} onChange={event => setEditing({ ...editing, content: event.target.value })} /><div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => setEditing(null)}>{t("Cancel")}</button><button className="fc-button fc-button-primary" type="submit" disabled={!!busy || !editing.content.trim()}>{t("Save private draft")}</button></div></form>}
    </section>
    <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{t("Queued messages")}</h3><button type="button" className="fc-icon-button" aria-label={t("Refresh queued messages")} onClick={() => void load()} disabled={!!busy}><RefreshCw size={16} /></button></div>
      <p className="fc-account-muted">{t("Reconnect sends only queued messages from the same sign-in, after checking current channel access. A changed sign-in or encryption mode requires review. Private conversation history and attachments are not cached by the service worker.")}</p>
      {queue.length === 0 ? <p className="fc-account-muted">{t("No messages are waiting on this device.")}</p> : <ul className="fc-mobile-queue" aria-label={t("Queued messages")}>{queue.map(row => <li key={row.id}><div><strong>{row.state === 'review' ? t("Needs review") : t("Waiting to send")}</strong><small>{t("Channel ")}{row.channelId} · {row.secure ? t("End-to-end encrypted channel") : t("Server-encrypted channel")}</small><p>{row.content.slice(0, 220)}{row.content.length > 220 ? '…' : ''}</p>{row.error && <p className="fc-account-muted">{row.error}</p>}</div><div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => void run(row.id, async () => { await mobileDrafts.save(row.channelId, row.content); await mobileOutbox.remove(row.id); window.dispatchEvent(new CustomEvent('freecollab:offline-draft-restored', { detail: { channelId: row.channelId } })); setNotice(t("Saved as a private draft. Open that channel to edit and send it.")); })}>{t("Move to draft")}</button><button className="fc-icon-button" type="button" aria-label={t("Remove queued message")} disabled={!!busy} onClick={() => void run(row.id, () => mobileOutbox.remove(row.id))}><Trash2 size={16} /></button></div></li>)}</ul>}
      <button className="fc-button" type="button" disabled={!!busy || !capabilities.online || !queue.some(row => row.state === 'queued')} onClick={() => void run('send', () => mobileOutbox.flush())}>{busy === 'send' ? <Loader2 size={16} className="fc-account-spinner" /> : <RefreshCw size={16} />}{t("Send eligible queued messages")}</button>
    </section>
    <OfflineLibrary channel={channel} />
    <NativePushSettings />
    <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{t("Private activity notifications")}</h3><Bell size={20} /></div>
      <p className="fc-account-muted">{t("Device notifications say only that new FreeCollab activity is available. Message text, names, channel titles and recovery keys never enter push payloads. Access is checked again before delivery.")}</p>
      {!configured && <p className="fc-account-muted">{t("The installation operator needs to configure Web Push before this browser can enable it.")}</p>}
      <button className="fc-button fc-button-primary" type="button" disabled={!!busy || !configured || !capabilities.online || !capabilities.push || capabilities.notificationPermission === 'denied'} onClick={() => void run('push', async () => { await subscribePush(); setNotice(t("Notifications are enabled for this browser and sign-in.")); })}>{busy === 'push' ? <Loader2 size={16} className="fc-account-spinner" /> : <Bell size={16} />}{t("Enable on this browser")}</button>
      {capabilities.notificationPermission === 'denied' && <p className="fc-account-muted">{t("Notifications are blocked in browser settings. Change that permission before trying again.")}</p>}
      {devices.length > 0 && <ul className="fc-mobile-devices" aria-label={t("Notification devices")}>{devices.map(device => <li key={device.id}><ShieldCheck size={17} /><div><strong>{device.current_session ? t("This sign-in") : t("Another sign-in")}</strong><small>{device.active && device.confirmed ? t("Enabled") : device.confirmed ? t("Disabled") : t("Confirmation pending")}</small></div><button className="fc-button" type="button" disabled={!!busy || !capabilities.online} onClick={() => void run(device.id, () => disablePush(device.id))}>{t("Remove")}</button></li>)}</ul>}
    </section>
  </div>;
}

export function MobileStatus({ onOpen }: { onOpen?: () => void }) {
  useI18n();
  const [online, setOnline] = useState(navigator.onLine), [count, setCount] = useState(0);
  useEffect(() => {
    let active = true;
    const update = () => { setOnline(navigator.onLine); void mobileOutbox.list().then(rows => { if (active) setCount(rows.length); }).catch(() => { if (active) setCount(0); }); };
    update(); window.addEventListener('freecollab:mobile-state', update);
    return () => { active = false; window.removeEventListener('freecollab:mobile-state', update); };
  }, []);
  if (online && !count) return null;
  return <div className="fc-mobile-notice" role="status">{online ? <RefreshCw size={14} /> : <WifiOff size={14} />}<span>{online ? t("Connected") : t("Offline — drafts stay private on this device.")}{count > 0 && ` ${count} queued message${count === 1 ? '' : 's'}.`}</span>{onOpen && <button type="button" onClick={onOpen}>{t("Review")}</button>}</div>;
}
