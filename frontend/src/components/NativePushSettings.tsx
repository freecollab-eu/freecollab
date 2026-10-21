import { useEffect, useRef, useState } from 'react';
import { Bell, PhoneIncoming, ShieldCheck, X } from 'lucide-react';
import { api } from '../lib/api';
import { nativePushBridge, nativePushPlatform, enableNativePush, validNativePushItem } from '../lib/native-push';
import { t, useI18n } from '../i18n';

interface NativeDevice { id: string; platform: 'android' | 'ios'; active: boolean; confirmed: boolean; calls_enabled: boolean; current_session: boolean }
interface NativeConfig { enabled: boolean; platforms: string[] }
interface Action { type: 'activity' | 'incoming_call'; channel_id: string; workspace_id: string | null; meeting_id: string | null }

export function NativePushSettings() {
  useI18n();
  const [config, setConfig] = useState<NativeConfig | null>(null), [devices, setDevices] = useState<NativeDevice[]>([]);
  const [calls, setCalls] = useState(false), [busy, setBusy] = useState(false), [notice, setNotice] = useState(''), [error, setError] = useState('');
  const identity = useRef(api.getAccessToken()), generation = useRef(0);
  const available = Boolean(nativePushBridge()), platform = nativePushPlatform();
  const platformConfigured = Boolean(platform && config?.platforms.includes(platform));
  async function load() {
    const token = api.getAccessToken(), run = generation.current;
    if (!token || !navigator.onLine) return;
    try {
      const [configuration, rows] = await Promise.all([api.request<NativeConfig>('/mobile/native/config'), api.request<{ items: NativeDevice[] }>('/mobile/native/devices')]);
      if (run === generation.current && token === api.getAccessToken()) { setConfig(configuration); setDevices(rows.items); }
    } catch { /* Native controls stay disabled when current configuration cannot be read. */ }
  }
  useEffect(() => {
    const update = () => { void load(); };
    const changed = () => { if (identity.current !== api.getAccessToken()) { generation.current++; identity.current = api.getAccessToken(); setDevices([]); setConfig(null); setNotice(''); setError(''); setBusy(false); } void load(); };
    void load(); window.addEventListener('freecollab:mobile-state', update); window.addEventListener('freecollab:auth-changed', changed);
    return () => { generation.current++; window.removeEventListener('freecollab:mobile-state', update); window.removeEventListener('freecollab:auth-changed', changed); };
  }, []);
  async function run(operation: () => Promise<void>) {
    if (busy) return; const token = api.getAccessToken(), run = generation.current; setBusy(true); setError(''); setNotice('');
    try { await operation(); if (run === generation.current && token === api.getAccessToken()) await load(); }
    catch (failure) { if (run === generation.current && token === api.getAccessToken()) setError(failure instanceof Error ? t(failure.message) : t('Native notifications could not be updated.')); }
    finally { if (run === generation.current && token === api.getAccessToken()) setBusy(false); }
  }
  return <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{t('Native mobile notifications')}</h3><PhoneIncoming size={20} /></div>
    <p className="fc-account-muted">{t('Receive private Android or iOS alerts while the app is in the background. Incoming call alerts open the app; you choose whether to join and enable your microphone.')}</p>
    {!available && <p className="fc-account-muted">{t('Open the native mobile app and connect it using browser sign-in to enable these notifications.')}</p>}
    {!platformConfigured && <p className="fc-account-muted">{t('The operator must configure their own Firebase or Apple Push credentials and sign the mobile build before enabling native alerts.')}</p>}
    <label className="fc-account-checkbox"><input type="checkbox" checked={calls} onChange={event => setCalls(event.target.checked)} disabled={busy || !available} />{t('Also alert me when a channel call starts')}</label>
    <button type="button" className="fc-button fc-button-primary" disabled={busy || !available || !platformConfigured || !navigator.onLine} onClick={() => void run(async () => { await enableNativePush(calls); setNotice(t('Allow the device permission, then open the confirmation alert if needed. Activation requires the delivered device proof.')); })}><Bell size={16} />{t('Enable on this mobile device')}</button>
    {notice && <p role="status">{notice}</p>}{error && <p className="fc-alert" role="alert">{error}</p>}
    {devices.length > 0 && <ul className="fc-mobile-devices" aria-label={t('Native notification devices')}>{devices.map(device => <li key={device.id}><ShieldCheck size={17}/><div><strong>{device.platform === 'ios' ? 'iOS' : 'Android'} · {device.current_session ? t('This sign-in') : t('Another sign-in')}</strong><small>{device.confirmed && device.active ? t('Enabled') : device.confirmed ? t('Disabled') : t('Confirmation pending')}{device.calls_enabled ? ` · ${t('Incoming call alerts')}` : ''}</small></div><button className="fc-button" type="button" disabled={busy || !navigator.onLine} onClick={() => void run(async () => { await api.request(`/mobile/native/devices/${encodeURIComponent(device.id)}`, { method: 'DELETE' }); if (device.current_session) await nativePushBridge()?.unregisterPush(); })}>{t('Remove')}</button></li>)}</ul>}
  </section>;
}

/** Mounted with the app, so cold-start and foreground delivery use the same gate. */
export function NativePushController() {
  useI18n();
  const [action, setAction] = useState<Action | null>(null), [notice, setNotice] = useState('');
  const identity = useRef(api.getAccessToken()), active = useRef(true), inFlight = useRef(false);
  useEffect(() => {
    active.current = true; const plugin = nativePushBridge(); if (!plugin) return;
    const consume = async () => {
      const token = api.getAccessToken();
      if (inFlight.current || !token || !navigator.onLine || document.visibilityState !== 'visible') return;
      inFlight.current = true;
      try {
        const rows = await plugin.getPendingPush();
        for (const item of rows.items.slice(-10)) {
          if (!active.current || token !== api.getAccessToken()) break;
          if (!validNativePushItem(item)) continue;
          try {
            if (item.type === 'subscription_confirmation') {
              await api.request(`/mobile/native/devices/${item.device_id}/confirm`, { method: 'POST', body: JSON.stringify({ challenge: item.challenge }) });
              if (active.current && token === api.getAccessToken()) { setNotice(t('Native mobile notifications are enabled for this sign-in.')); window.dispatchEvent(new Event('freecollab:mobile-state')); }
            } else {
              const resolved = await api.request<Action>(`/mobile/native/deliveries/${item.id}`);
              if (active.current && token === api.getAccessToken()) setAction(resolved);
            }
            await plugin.clearPush({ key: item.key });
          } catch (failure) {
            // Authorization/expired proofs are discarded. Transport failures stay
            // in the bounded native queue for foreground retry.
            const status = Number((failure as { status?: number })?.status);
            if ([400, 401, 403, 404, 410].includes(status)) await plugin.clearPush({ key: item.key });
          }
        }
      } catch { /* No UI/payload is shown unless authenticated resolution succeeds. */ }
      finally { inFlight.current = false; }
    };
    const changed = () => {
      const prior = identity.current; identity.current = api.getAccessToken();
      if (prior !== identity.current) { setAction(null); setNotice(''); if (prior) void plugin.unregisterPush().catch(() => {}); }
      void consume();
    };
    let refreshing = false;
    const refresh = async () => {
      const token = api.getAccessToken();
      if (!token || refreshing || !navigator.onLine || document.visibilityState !== 'visible') return;
      refreshing = true;
      try {
        const devices = await api.request<{items: NativeDevice[]}>('/mobile/native/devices');
        const device = devices.items.find(item => item.active && item.confirmed && item.current_session);
        if (device && token === api.getAccessToken()) {
          // Silent token refresh requires existing server consent and existing OS
          // permission; this native method never opens a permission prompt.
          const registration = await plugin.refreshPush();
          if (token === api.getAccessToken()) await api.request('/mobile/native/devices', {method:'POST', body:JSON.stringify({...registration, calls_enabled:device.calls_enabled, consent:true})});
        }
      } catch { /* Explicit settings retain control when a provider is unavailable. */ }
      finally { refreshing = false; }
    };
    const update = () => { void consume(); };
    const foreground = () => { void refresh(); void consume(); };
    const interval = window.setInterval(update, 2000); void refresh(); void consume();
    window.addEventListener('freecollab:auth-changed', changed); window.addEventListener('storage', changed); document.addEventListener('visibilitychange', foreground);
    return () => { active.current = false; clearInterval(interval); window.removeEventListener('freecollab:auth-changed', changed); window.removeEventListener('storage', changed); document.removeEventListener('visibilitychange', foreground); };
  }, []);
  return <>{notice && !action && <aside className="fc-native-signin" role="status"><ShieldCheck size={18}/><span>{notice}</span><button className="fc-icon-button" type="button" aria-label={t('Close')} onClick={() => setNotice('')}><X size={18}/></button></aside>}{action && <aside className="fc-native-signin" role="status">{action.type === 'incoming_call' ? <PhoneIncoming size={20}/> : <Bell size={20}/>}<span>{action.type === 'incoming_call' ? t('Incoming FreeCollab call') : t('New FreeCollab activity')}</span><button type="button" className="fc-button fc-button-primary" onClick={() => {
      if (!action.workspace_id) { setAction(null); return; }
      window.dispatchEvent(new CustomEvent('freecollab:open-personal-source', { detail: { workspace_id: action.workspace_id, channel_id: action.channel_id, kind: action.type === 'incoming_call' ? 'meeting' : 'message', item_id: action.meeting_id ?? undefined } })); setAction(null);
    }}>{action.type === 'incoming_call' ? t('Open call') : t('Open activity')}</button><button className="fc-icon-button" type="button" aria-label={t('Dismiss')} onClick={() => setAction(null)}><X size={18}/></button></aside>}</>;
}
