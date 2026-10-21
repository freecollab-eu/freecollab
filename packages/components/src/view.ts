import type { CollaborationClient } from './client.js';
import { errorLabel, labelsFor } from './labels.js';
import { componentStyles } from './styles.js';
import { SDKError, type Labels, type ModuleOptions, type MountedComponent, type SessionState } from './types.js';

export function element<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node;
}
export function timestamp(value: string) { const node = element('time'); node.dateTime = value; const date = new Date(value); node.textContent = Number.isNaN(date.getTime()) ? '' : date.toLocaleString(); return node; }
export function button(text: string, action: () => void) { const node = element('button', text); node.type = 'button'; node.addEventListener('click', action); return node; }
export interface ViewContext {
  readonly labels: Labels; readonly list: HTMLUListElement; readonly formArea: HTMLDivElement; readonly signal: AbortSignal;
  ready(): boolean; run(operation: (signal: AbortSignal) => Promise<void>): Promise<void>; error(error: unknown): void;
}
/** One DOM lifetime. Destroy removes all private content, timers and in-flight work. */
export function mountView<T>(target: HTMLElement | ShadowRoot, client: CollaborationClient, kind: 'chat' | 'files' | 'feed' | 'notifications' | 'tasks' | 'documents' | 'meetings' | 'calendar' | 'workflows' | 'decisions' | 'search', options: ModuleOptions,
  load: (signal: AbortSignal) => Promise<T[]>, render: (value: T, context: ViewContext) => HTMLElement, compose?: (context: ViewContext) => void): MountedComponent {
  if (client.state.status === 'destroyed') throw new SDKError('destroyed', 'This collaboration client has been destroyed.');
  const labels = labelsFor(options), host = element('div'); target.append(host); const shadow = host.attachShadow({ mode: 'open' });
  const style = element('style', componentStyles), card = element('section'); card.className = 'fc-card'; card.setAttribute('aria-label', options.title || labels[kind]);
  const heading = element('header'); heading.className = 'fc-header'; heading.append(element('h2', options.title || labels[kind]));
  const status = element('p'); status.className = 'fc-status'; status.setAttribute('role', 'status');
  const alert = element('p'); alert.className = 'fc-error'; alert.setAttribute('role', 'alert'); alert.hidden = true;
  const list = element('ul'), formArea = element('div'), privacy = element('p', labels.nativePrivacy); privacy.className = 'fc-privacy';
  card.append(heading, status, alert, list, formArea, privacy); shadow.append(style, card);
  let destroyed = false, generation = 0, loading = false, busy = false, timer: ReturnType<typeof setTimeout> | undefined, controller = new AbortController();
  const interval = Math.max(1500, Math.min(options.pollIntervalMs || 5000, 120000));
  const error = (failure: unknown) => { if (destroyed || (failure instanceof Error && failure.name === 'AbortError')) return; if (failure instanceof SDKError && ['access-denied', 'unauthenticated'].includes(failure.code) && client.state.status !== 'ready') return; alert.textContent = failure instanceof SDKError ? errorLabel(failure.code, failure.message, options) : failure instanceof Error ? failure.message : labels.genericFailure; alert.hidden = false; };
  const ready = () => !destroyed && client.state.status === 'ready';
  const context: ViewContext = {
    labels, list, formArea, get signal() { return controller.signal; }, ready, error,
    async run(operation) {
      if (!ready() || busy) return; busy = true; clearTimeout(timer); alert.hidden = true; const own = generation;
      const controls = [...card.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLButtonElement>('input,textarea,select,button')].map(control => ({control, disabled: control.disabled}));
      for (const {control} of controls) control.disabled = true;
      try { await operation(controller.signal); if (own === generation && !destroyed) await refresh(); }
      catch (failure) { if (own === generation) error(failure); }
      finally { if (own === generation) { busy = false; for (const {control, disabled} of controls) if (control.isConnected) control.disabled = !ready() || disabled; if (ready()) {clearTimeout(timer); timer = setTimeout(() => void refresh(), interval);} } }
    },
  };
  function change(state: Readonly<SessionState>) {
    if (destroyed) return;
    if (state.status !== 'ready') {
      generation++; clearTimeout(timer); controller.abort(); controller = new AbortController(); loading = false; busy = false; list.replaceChildren(); formArea.replaceChildren(); alert.hidden = true;
      status.textContent = state.status === 'signed-out' ? labels.signedOut : state.status === 'denied' ? labels.denied : state.status === 'destroyed' ? labels.destroyed : labels.loading;
      if (state.status === 'destroyed') destroy();
    } else {
      status.textContent = '';
      if (compose && !formArea.childElementCount) compose(context);
      if (!loading) void refresh();
    }
    refreshButton.disabled = !ready();
  }
  async function refresh() {
    if (destroyed || loading || ['signed-out', 'denied', 'destroyed'].includes(client.state.status)) return;
    clearTimeout(timer); loading = true; const own = generation;
    try {
      const rows = await load(controller.signal); if (destroyed || own !== generation || !ready()) return;
      list.replaceChildren(...rows.map(row => render(row, context))); if (!rows.length) { const empty = element('li', labels.empty); empty.className = 'fc-empty'; list.append(empty); }
    } catch (failure) { if (own === generation) error(failure); }
    finally {
      if (own === generation) { loading = false; if (!destroyed && client.state.status === 'ready') timer = setTimeout(() => void refresh(), interval); }
    }
  }
  function destroy() { if (destroyed) return; destroyed = true; generation++; clearTimeout(timer); controller.abort(); unsubscribe(); unsubscribeError(); host.remove(); }
  const refreshButton = button(labels.refresh, () => void refresh()); heading.append(refreshButton);
  const unsubscribe = client.on('state', change);
  const unsubscribeError = client.on('error', error);
  if (client.state.status === 'ready') change(client.state); else { status.textContent = labels.loading; void refresh(); }
  return { refresh, destroy };
}
