import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Cloud, RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import { syncApi, type SyncKind, type SyncProvider } from '../lib/enterprise-api';
import '../styles/integrations.css';

const providerNames: Record<SyncProvider, string> = { google: 'Google', microsoft: 'Microsoft', nextcloud: 'Nextcloud', caldav: 'CalDAV' };
export function ChannelSyncNotice({ channelId, kind }: { channelId: string; kind: SyncKind }) {
  useI18n();
  const [connections, setConnections] = useState<{ id: string; provider: SyncProvider; enabled: boolean }[]>([]);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const owner = useRef(api.getAccessToken());
  const mounted = useRef(true);
  const generation = useRef(0);
  async function load() {
    const request = ++generation.current; setLoading(true);
    try {
      const result = await syncApi.channelStatus(channelId);
      if (!mounted.current || owner.current !== api.getAccessToken() || request !== generation.current) return;
      setConnections(result.connections.filter(value => value.kind === kind)); setFailed(false);
    } catch { if (mounted.current && owner.current === api.getAccessToken() && request === generation.current) setFailed(true); }
    finally { if (mounted.current && owner.current === api.getAccessToken() && request === generation.current) setLoading(false); }
  }
  useEffect(() => {
    mounted.current = true; owner.current = api.getAccessToken(); setConnections([]); setFailed(false); void load();
    const refresh = () => { if (document.visibilityState === 'visible') void load(); };
    const interval = window.setInterval(refresh, 15000);
    window.addEventListener('focus', refresh);
    return () => { mounted.current = false; generation.current++; window.clearInterval(interval); window.removeEventListener('focus', refresh); };
  }, [channelId, kind]);
  if (failed) return <div className="fc-integration-privacy fc-channel-sync-notice" role="status"><Cloud size={17} /><p>{t("Cloud sharing status is unavailable. Refresh to check this channel’s external connections.")}</p><button type="button" className="fc-icon-button" aria-label={t("Refresh channel cloud sharing status")} disabled={loading} onClick={() => void load()}><RefreshCw size={15} /></button></div>;
  if (!connections.length) return null;
  const active = connections.filter(value => value.enabled);
  const names = [...new Set((active.length ? active : connections).map(value => providerNames[value.provider]))].join(', ');
  return <div className="fc-integration-privacy fc-channel-sync-notice" role="status"><Cloud size={18} /><p><strong>{active.length ? t("External sharing enabled.") : t("External sharing paused.")}</strong> {active.length ? t("This channel’s existing and future {value0} can synchronize with {value1}. Changes and deletions can travel both ways.", { value0: String(kind === 'files' ? 'files' : 'events'), value1: String(names) }) : t("This channel has a retained connection to {value0}. Previously shared provider copies may remain.", { value0: String(names) })}</p></div>;
}
