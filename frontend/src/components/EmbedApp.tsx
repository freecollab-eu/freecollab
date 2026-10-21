import { useEffect, useRef, useState } from 'react';
import { AccountProvider, useAccount } from '../account/AccountProvider';
import { AppearanceProvider } from '../appearance/AppearanceProvider';
import { LocaleProvider, applyLocale } from '../i18n';
import { api, type Channel } from '../lib/api';
import { disconnectSocket } from '../lib/socket';
import { Chat } from './Chat';
import { ProjectTasksPanel } from './ProjectTasksPanel';
import { SharedDocumentsPanel } from './SharedDocumentsPanel';
import { MeetingsPanel } from './MeetingsPanel';
import { CalendarPanel } from './CalendarPanel';
import { WorkflowsPanel } from './WorkflowsPanel';
import { CollaborationPanel } from './CollaborationPanel';
import { ChannelFiles } from './ChannelFiles';
import { ActivityFeedPanel } from './ActivityFeedPanel';
import { IntegrationsPanel } from './IntegrationsPanel';
import { HistorySearchPanel } from './HistorySearchPanel';
import { CopilotPanel } from './CopilotPanel';
import { CloudFilesPicker } from './CloudFilesPicker';
import { ExternalMeetingsPanel } from './ExternalMeetingsPanel';
import { listChannelFiles, type ManagedFile } from '../lib/files-api';

const protocol = 'freecollab-embed-v1';
const modules = new Set(['workspace', 'chat', 'tasks', 'documents', 'meetings', 'calendar', 'workflows', 'decisions', 'files', 'feed', 'integrations', 'search', 'copilot', 'cloud', 'external-meetings']);
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
interface Session { channel: Channel; module: string; workspaceId: string; revision: number; locale: 'en' | 'sv'; theme: unknown }
interface Exchange { access_token: string; token_type: string; user_id: string; public_key: string; encrypted_private_key: string | null; workspace_id: string; application_id: string; channel_id: string; expires_in: number; expires_at: number }

function EmbeddedFiles({ channel }: { channel: Channel }) {
  const [files, setFiles] = useState<ManagedFile[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => {
    const token = api.getAccessToken(); let active = true;
    void listChannelFiles(channel.id).then(rows => { if (active && token === api.getAccessToken()) setFiles(rows); }).catch(() => { if (active) setError('Files could not be loaded.'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [channel.id]);
  return <>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}<ChannelFiles channelId={channel.id} files={files} loading={loading} setFiles={setFiles} onError={setError} onNotice={setNotice} /></>;
}

function EmbeddedContent({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const { refreshWorkspaces, openSecureSettings } = useAccount();
  const channel = session.channel, props = { workspaceId: session.workspaceId, channelId: channel.id, isAdmin: channel.role === 'admin' };
  useEffect(() => { void refreshWorkspaces(session.workspaceId); }, [session.workspaceId, refreshWorkspaces]);
  useEffect(() => {
    if (!session.theme || typeof session.theme !== 'object') return;
    const values = session.theme as Record<string, unknown>;
    const properties: Record<string, string> = { accent: '--fc-accent', background: '--fc-bg', surface: '--fc-surface', text: '--fc-text', font: '--fc-font' };
    const previous = new Map<string, string>();
    for (const [key, property] of Object.entries(properties)) {
      const value = values[key];
      if (typeof value !== 'string' || value.length > 150 || /[;{}]|url\s*\(/i.test(value) || !CSS.supports(key === 'font' ? 'font-family' : 'color', value)) continue;
      previous.set(property, document.body.style.getPropertyValue(property)); document.body.style.setProperty(property, value);
    }
    return () => { for (const [property, value] of previous) document.body.style.setProperty(property, value); };
  }, [session.theme]);
  if (session.module === 'workspace' || session.module === 'chat') return <Chat initialChannelId={channel.id} embeddedChannelId={session.module === 'chat' ? channel.id : undefined} onLogout={onLogout} />;
  const close = () => { /* A focused embed stays on its explicit module until the host changes it. */ };
  return <main className="fc-embed-module" aria-label="Embedded collaboration">
    <header className="fc-embed-module-header"><span>{channel.name || (session.locale === 'sv' ? 'Krypterad kanal' : 'Encrypted channel')}</span>{channel.secure && <button type="button" className="fc-button" onClick={() => openSecureSettings(channel.id)}>{session.locale === 'sv' ? 'Enheter och återställning' : 'Devices & recovery'}</button>}<button type="button" className="fc-text-button" onClick={onLogout}>{session.locale === 'sv' ? 'Koppla från' : 'Disconnect'}</button></header>
    {session.module === 'tasks' && <ProjectTasksPanel {...props} />}
    {session.module === 'documents' && <SharedDocumentsPanel {...props} />}
    {session.module === 'meetings' && <MeetingsPanel {...props} />}
    {session.module === 'calendar' && <CalendarPanel {...props} />}
    {session.module === 'workflows' && <WorkflowsPanel {...props} />}
    {session.module === 'decisions' && <CollaborationPanel channel={channel} tab="decisions" onClose={close} />}
    {session.module === 'files' && <EmbeddedFiles channel={channel} />}
    {session.module === 'feed' && <ActivityFeedPanel {...props} />}
    {session.module === 'integrations' && <IntegrationsPanel workspaceId={session.workspaceId} channelId={channel.id} channels={[channel]} onClose={close} />}
    {session.module === 'search' && <HistorySearchPanel channels={[channel]} onClose={close} onMessage={() => {}} />}
    {session.module === 'copilot' && <CopilotPanel {...props} isAdmin={false} />}
    {session.module === 'cloud' && <CloudFilesPicker workspaceId={session.workspaceId} channelId={channel.id} />}
    {session.module === 'external-meetings' && <ExternalMeetingsPanel {...props} isAdmin={false} />}
  </main>;
}

/** Executed only by the per-application CSP document returned by the backend. */
export function EmbedApp() {
  const [state, setState] = useState<'connecting' | 'ready' | 'signed-out' | 'error'>('connecting');
  const [session, setSession] = useState<Session | null>(null);
  const active = useRef<AbortController | null>(null), disconnectRef = useRef<() => void>(() => {});
  const nonceRef = useRef(new URLSearchParams(location.hash.slice(1)).get('nonce'));
  const revision = useRef(0);
  useEffect(() => {
    const root = document.getElementById('root'), applicationId = root?.dataset.embedApplication, parentOrigin = root?.dataset.embedParentOrigin, nonce = nonceRef.current;
    if (!identifier(applicationId) || !parentOrigin || parentOrigin === location.origin || !nonce || !/^[A-Za-z0-9_-]{32,128}$/.test(nonce) || window.parent === window) { setState('error'); return; }
    let expected: URL;
    try { expected = new URL(parentOrigin); if (expected.origin !== parentOrigin || !['http:', 'https:'].includes(expected.protocol)) throw new Error(); } catch { setState('error'); return; }
    history.replaceState(null, '', location.pathname + location.search);
    const controller = new AbortController(); active.current = controller; let ready = false, started = false, renewing = false;
    let expiryTimer: number | undefined, renewalTimer: number | undefined, leaseDeadline = 0, failures = 0;
    let binding: Pick<Exchange, 'workspace_id' | 'channel_id' | 'user_id' | 'access_token'> | null = null;
    const post = (data: Record<string, unknown>) => window.parent.postMessage({ protocol, nonce, ...data }, parentOrigin);
    const disconnect = (status: 'signed-out' | 'error' = 'signed-out') => {
      ++revision.current; window.clearTimeout(expiryTimer); window.clearTimeout(renewalTimer); const token = api.getAccessToken();
      controller.abort(); active.current = null; setSession(null); setState(status); disconnectSocket();
      api.logout();
      if (token) void fetch('/api/embed/' + encodeURIComponent(applicationId) + '/logout', { method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error', keepalive: true, headers: { Authorization: 'Bearer ' + token } }).then(response => { post({ type: 'disconnected', revoked: response.ok }); }).catch(() => { post({ type: 'disconnected', revoked: false }); });
      else post({ type: 'disconnected', revoked: true });
      post({ type: status });
    };
    disconnectRef.current = () => disconnect();
    const fail = () => disconnect('error');
    const scheduleLease = (result: Exchange) => {
      if (!Number.isFinite(result.expires_in) || result.expires_in <= 0 || result.expires_in > 600 || !Number.isFinite(result.expires_at)) throw new Error('Invalid embedded lease');
      const remaining = Math.min(result.expires_in * 1000, result.expires_at * 1000 - Date.now());
      if (remaining <= 0) throw new Error('Expired embedded lease');
      leaseDeadline = performance.now() + remaining;
      window.clearTimeout(expiryTimer); window.clearTimeout(renewalTimer);
      expiryTimer = window.setTimeout(() => disconnect(), remaining);
      // Refresh authority while there is still time for bounded network retries.
      // This extends only a server-side lease; identity, JWT and verified devices
      // remain unchanged and no pending user write is queued or replayed.
      if (remaining > 1000) renewalTimer = window.setTimeout(() => { void renewLease(); }, Math.max(250, Math.floor(remaining * .6)));
    };
    const retryRenewal = () => {
      if (controller.signal.aborted) return;
      const remaining = leaseDeadline - performance.now();
      if (remaining <= 1000) return; // The existing expiry timer stays authoritative.
      const delay = Math.min(1000 * 2 ** Math.min(failures++, 3), remaining - 250);
      renewalTimer = window.setTimeout(() => { void renewLease(); }, delay);
    };
    const renewLease = async () => {
      if (controller.signal.aborted || renewing || !binding) return;
      const current = revision.current, token = api.getAccessToken();
      if (!token || token !== binding.access_token || api.getCurrentUserId() !== binding.user_id) { disconnect(); return; }
      renewing = true;
      try {
        const response = await fetch('/api/embed/' + encodeURIComponent(applicationId) + '/renew', { method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal, headers: { Authorization: 'Bearer ' + token } });
        if (controller.signal.aborted || current !== revision.current) return;
        if ([401, 403, 404, 409].includes(response.status)) { disconnect(); return; }
        if (!response.ok) { retryRenewal(); return; }
        const result = await response.json() as Exchange;
        if (controller.signal.aborted || current !== revision.current) return;
        if (result.application_id !== applicationId || result.workspace_id !== binding.workspace_id || result.channel_id !== binding.channel_id || result.user_id !== binding.user_id || result.access_token !== token || api.getAccessToken() !== token) { fail(); return; }
        scheduleLease(result); failures = 0;
      } catch { if (!controller.signal.aborted && current === revision.current) retryRenewal(); }
      finally { renewing = false; }
    };
    const receive = (event: MessageEvent) => {
      if (event.origin !== parentOrigin || event.source !== window.parent || !event.data || event.data.protocol !== protocol || event.data.nonce !== nonce) return;
      const data = event.data;
      if (data.type === 'disconnect') { disconnect(); return; }
      if (data.type !== 'connect' || !ready || started || controller.signal.aborted) return;
      started = true;
      if (typeof data.code !== 'string' || !/^[A-Za-z0-9_-]{24,512}$/.test(data.code) || typeof data.code_verifier !== 'string' || !/^[A-Za-z0-9_-]{43,128}$/.test(data.code_verifier)
        || !identifier(data.channel_id) || !identifier(data.workspace_id) || !modules.has(data.module) || !['en', 'sv'].includes(data.locale) || typeof data.require_encrypted !== 'boolean') { fail(); return; }
      const current = ++revision.current;
      void (async () => {
        const response = await fetch('/api/embed/' + encodeURIComponent(applicationId) + '/exchange', { method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal,
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: data.code, code_verifier: data.code_verifier, parent_origin: parentOrigin, channel_id: data.channel_id }) });
        if (!response.ok) throw new Error('access-denied');
        const result = await response.json() as Exchange;
        if (controller.signal.aborted || current !== revision.current) return;
        if (result.application_id !== applicationId || result.workspace_id !== data.workspace_id || result.channel_id !== data.channel_id || typeof result.access_token !== 'string' || !identifier(result.user_id) || !Number.isFinite(result.expires_in) || result.expires_in <= 0 || result.expires_in > 600 || !Number.isFinite(result.expires_at)) throw new Error('scope-mismatch');
        api.applySession(result);
        binding = { workspace_id: result.workspace_id, channel_id: result.channel_id, user_id: result.user_id, access_token: result.access_token };
        scheduleLease(result);
        const [me, channel] = await Promise.all([api.getMe(), api.getChannel(data.channel_id)]);
        if (controller.signal.aborted || current !== revision.current) return;
        if (me.session_scope !== 'workspace' || me.workspace_id !== data.workspace_id || me.id !== result.user_id || channel.id !== data.channel_id || channel.workspace_id !== data.workspace_id || typeof channel.secure !== 'boolean' || data.require_encrypted && !channel.secure) throw new Error('scope-mismatch');
        applyLocale(data.locale);
        setSession({ channel, module: data.module, workspaceId: data.workspace_id, revision: current, locale: data.locale, theme: data.theme }); setState('ready');
        post({ type: 'connected', workspace_id: data.workspace_id, channel_id: channel.id, module: data.module });
      })().catch(() => { if (!controller.signal.aborted && current === revision.current) fail(); });
    };
    const expired = () => disconnect();
    const pagehide = () => disconnect();
    window.addEventListener('message', receive); window.addEventListener('pagehide', pagehide); window.addEventListener('freecollab:session-expired', expired);
    void fetch('/api/embed/' + encodeURIComponent(applicationId) + '/bootstrap?parent_origin=' + encodeURIComponent(parentOrigin), { credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error();
      const boot = await response.json();
      if (boot.application_id !== applicationId || boot.parent_origin !== parentOrigin || !identifier(boot.workspace_id)) throw new Error();
      if (!controller.signal.aborted) { ready = true; post({ type: 'ready' }); }
    }).catch(() => { if (!controller.signal.aborted) fail(); });
    return () => { ++revision.current; window.clearTimeout(expiryTimer); window.clearTimeout(renewalTimer); controller.abort(); window.removeEventListener('message', receive); window.removeEventListener('pagehide', pagehide); window.removeEventListener('freecollab:session-expired', expired); disconnectSocket(); api.logout(); };
  }, []);
  return <div className="fc-embed-root" data-embed-state={state}>
    {state === 'ready' && session ? <LocaleProvider><AppearanceProvider><AccountProvider><EmbeddedContent key={session.revision} session={session} onLogout={() => disconnectRef.current()} /></AccountProvider></AppearanceProvider></LocaleProvider>
      : <main className="fc-embed-status" role={state === 'error' ? 'alert' : 'status'}><span className="fc-kicker">FreeCollab</span><p>{state === 'connecting' ? 'Connecting your workspace…' : state === 'signed-out' ? 'Disconnected. Sign in through your application to reconnect.' : 'This embedded workspace is unavailable. Check the application, sign-in and permitted origin.'}</p></main>}
  </div>;
}
