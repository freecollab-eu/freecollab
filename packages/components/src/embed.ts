import { SDKError } from './types.js';

export type EmbedModule = 'workspace' | 'chat' | 'tasks' | 'documents' | 'meetings' | 'calendar' | 'workflows' | 'decisions' | 'files' | 'feed' | 'integrations' | 'search' | 'copilot' | 'cloud' | 'external-meetings';
export interface EmbedGrantContext {
  readonly applicationId: string;
  readonly workspaceId: string;
  readonly channelId: string;
  readonly codeChallenge: string;
  readonly parentOrigin: string;
  readonly signal: AbortSignal;
}
export interface EmbedTheme { accent?: string; background?: string; surface?: string; text?: string; font?: string }
export interface EmbedConfig {
  appUrl: string;
  applicationId: string;
  workspaceId: string;
  channelId: string;
  module?: EmbedModule;
  locale?: 'en' | 'sv';
  theme?: EmbedTheme;
  /** Returns a short-lived launch grant, never a user JWT or administrative API key. */
  grantFactory: (context: EmbedGrantContext) => Promise<{ code: string }>;
  title?: string;
  /** Camera/microphone remain subject to an explicit user gesture and browser permission. */
  media?: boolean;
  timeoutMs?: number;
  onState?: (state: { status: 'connecting' | 'ready' | 'signed-out' | 'error' | 'destroyed' }) => void;
  onError?: (error: SDKError) => void;
}
export interface EmbeddedWorkspace {
  readonly ready: Promise<void>;
  reauthenticate(): Promise<void>;
  setScope(scope: { workspaceId: string; channelId: string }): Promise<void>;
  destroy(): void;
}

const protocol = 'freecollab-embed-v1';
const identifier = (value: string) => {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) throw new SDKError('invalid-scope', 'Provide explicit embedding application, workspace and channel identifiers.');
  return value;
};
function publicOrigin(value: string) {
  let url: URL;
  try { url = new URL(value); } catch { throw new SDKError('invalid-app-url', 'Provide the trusted FreeCollab application HTTPS origin.'); }
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/' || !['http:', 'https:'].includes(url.protocol)
    || url.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) throw new SDKError('invalid-app-url', 'Use a trusted HTTPS application origin, or HTTP on loopback during development.');
  if (url.origin === location.origin) throw new SDKError('isolated-origin-required', 'Host the trusted embedded client on a different origin from your application.');
  return url.origin;
}
const random = () => btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const challenge = async (verifier: string) => btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const allowedModules = new Set<EmbedModule>(['workspace', 'chat', 'tasks', 'documents', 'meetings', 'calendar', 'workflows', 'decisions', 'files', 'feed', 'integrations', 'search', 'copilot', 'cloud', 'external-meetings']);

/** Trusted-origin full product adapter. Decryption and device keys stay inside the iframe. */
export function mountWorkspace(target: HTMLElement, config: EmbedConfig): EmbeddedWorkspace {
  return mount(target, config, false);
}

/** The selected channel must be encrypted; there is no native-content fallback. */
export function mountSecureChat(target: HTMLElement, config: Omit<EmbedConfig, 'module'>): EmbeddedWorkspace {
  return mount(target, { ...config, module: 'chat' }, true);
}

function mount(target: HTMLElement, config: EmbedConfig, requireEncrypted: boolean): EmbeddedWorkspace {
  if (!(target instanceof HTMLElement) || typeof config.grantFactory !== 'function') throw new SDKError('invalid-embed-config', 'Provide a host element and an asynchronous launch grant factory.');
  const origin = publicOrigin(config.appUrl), parentOrigin = location.origin, applicationId = identifier(config.applicationId);
  const module = config.module || 'workspace';
  if (!allowedModules.has(module)) throw new SDKError('invalid-module', 'Choose a supported embedded product module.');
  const host = new URL(parentOrigin);
  if (parentOrigin === 'null' || !['https:', 'http:'].includes(host.protocol) || host.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(host.hostname)) throw new SDKError('invalid-parent-origin', 'The host application needs HTTPS, or HTTP on loopback during development.');
  let workspaceId = identifier(config.workspaceId), channelId = identifier(config.channelId);
  const owner = document.createElement('div'); owner.style.cssText = 'width:100%;height:100%;min-height:420px'; target.replaceChildren(owner);
  let terminal = false, generation = 0, cleanup: (() => void) | undefined;
  let currentReady: Promise<void>;
  const state = (status: Parameters<NonNullable<EmbedConfig['onState']>>[0]['status']) => { try { config.onState?.({ status }); } catch { /* Host callbacks cannot prevent teardown. */ } };
  function disconnect() { cleanup?.(); cleanup = undefined; }
  function connect(): Promise<void> {
    if (terminal) return Promise.reject(new SDKError('destroyed', 'This embedded workspace has been destroyed.'));
    ++generation; disconnect(); state('connecting');
    const version = generation, frameWorkspace = workspaceId, frameChannel = channelId, controller = new AbortController(), nonce = random(), verifier = random();
    const frame = document.createElement('iframe');
    frame.title = config.title || 'FreeCollab workspace'; frame.referrerPolicy = 'no-referrer';
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-downloads allow-modals allow-popups allow-popups-to-escape-sandbox');
    frame.setAttribute('allow', config.media ? 'camera; microphone; display-capture; clipboard-write' : 'camera \'none\'; microphone \'none\'; display-capture \'none\'; clipboard-write');
    frame.style.cssText = 'display:block;width:100%;height:100%;min-height:420px;border:0;background:transparent';
    frame.src = origin + '/api/embed/' + encodeURIComponent(applicationId) + '/document?parent_origin=' + encodeURIComponent(parentOrigin) + '#nonce=' + encodeURIComponent(nonce);
    let settled = false, started = false, resolveReady: () => void, rejectReady: (error: unknown) => void;
    const ready = new Promise<void>((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
    // Automatic initial mounting must not produce an unhandled rejection when a host uses callbacks.
    void ready.catch(() => undefined);
    const timeout = window.setTimeout(() => fail('frame-timeout', 'The embedded application did not finish connecting.'), Math.min(660000, Math.max(5000, config.timeoutMs || 300000)));
    const post = (payload: Record<string, unknown>) => frame.contentWindow?.postMessage({ protocol, nonce, ...payload }, origin);
    const fail = (code: string, message: string) => {
      if (terminal || version !== generation) return;
      const error = new SDKError(code, message); controller.abort(); window.clearTimeout(timeout);
      frame.remove(); window.removeEventListener('message', receive); state('error');
      if (!settled) { settled = true; rejectReady(error); }
      try { config.onError?.(error); } catch { /* Host callbacks do not control session cleanup. */ }
    };
    const receive = (event: MessageEvent) => {
      if (terminal || version !== generation || event.origin !== origin || event.source !== frame.contentWindow) return;
      const data = event.data;
      if (!data || data.protocol !== protocol || data.nonce !== nonce) return;
      if (data.type === 'ready' && !started) {
        started = true;
        void challenge(verifier).then(async codeChallenge => {
          if (controller.signal.aborted || version !== generation || terminal) return;
          const grant = await config.grantFactory(Object.freeze({ applicationId, workspaceId: frameWorkspace, channelId: frameChannel, codeChallenge, parentOrigin, signal: controller.signal }));
          if (controller.signal.aborted || version !== generation || terminal) return;
          if (!grant || typeof grant.code !== 'string' || !/^[A-Za-z0-9_-]{24,512}$/.test(grant.code)) throw new SDKError('invalid-launch-grant', 'The application did not return a valid one-time launch grant.');
          post({ type: 'connect', code: grant.code, code_verifier: verifier, workspace_id: frameWorkspace, channel_id: frameChannel, module, locale: config.locale || 'en', theme: config.theme, require_encrypted: requireEncrypted });
        }).catch(() => { if (!controller.signal.aborted) fail('launch-failed', 'Sign in through your application to connect this workspace.'); });
      } else if (data.type === 'connected' && started && !settled) {
        if (data.workspace_id !== frameWorkspace || data.channel_id !== frameChannel || data.module !== module) { fail('scope-mismatch', 'The embedded application returned a different scope.'); return; }
        settled = true; window.clearTimeout(timeout); state('ready'); resolveReady();
      } else if (data.type === 'signed-out') {
        state('signed-out');
        if (!settled) { settled = true; window.clearTimeout(timeout); rejectReady(new SDKError('unauthenticated', 'Sign in through your application again.')); }
      } else if (data.type === 'error') fail('embed-denied', 'The embedded application could not authorize this workspace, channel or encryption mode.');
    };
    cleanup = () => {
      controller.abort(); window.clearTimeout(timeout); window.removeEventListener('message', receive);
      // Clear displayed private content synchronously, while giving the trusted
      // frame a brief opportunity to revoke its own child session and acknowledge.
      frame.hidden = true; frame.style.display = 'none';
      let disposed = false;
      const dispose = () => { if (disposed) return; disposed = true; window.clearTimeout(disposalTimer); window.removeEventListener('message', acknowledgement); frame.remove(); if (!owner.childNodes.length && terminal) owner.remove(); };
      const acknowledgement = (event: MessageEvent) => { if (event.origin === origin && event.source === frame.contentWindow && event.data?.protocol === protocol && event.data?.nonce === nonce && event.data?.type === 'disconnected') dispose(); };
      const disposalTimer = window.setTimeout(dispose, 2000);
      window.addEventListener('message', acknowledgement); post({ type: 'disconnect' });
      if (!settled) { settled = true; rejectReady(new DOMException('The embedded application session changed.', 'AbortError')); }
    };
    window.addEventListener('message', receive); owner.append(frame); return ready;
  }
  currentReady = connect();
  return {
    get ready() { return currentReady; },
    reauthenticate() { currentReady = connect(); return currentReady; },
    setScope(scope) { workspaceId = identifier(scope.workspaceId); channelId = identifier(scope.channelId); currentReady = connect(); return currentReady; },
    destroy() { if (terminal) return; terminal = true; ++generation; disconnect(); state('destroyed'); },
  };
}
