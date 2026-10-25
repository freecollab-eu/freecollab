import { useEffect, useRef, type RefObject } from 'react';
import { api } from './api';

/** One exact resource intent, canceled even while the initial list is loading. */
export function useResourceTargetFocus(region: RefObject<HTMLElement | null>, scope: string, kind: string,
  target: string | undefined, ready: boolean, resolve: (id: string, current: () => boolean) => Promise<boolean>) {
  const callback = useRef(resolve); callback.current = resolve;
  const lease = useRef<{ current: () => boolean; done: boolean; cancel: () => void } | null>(null);
  useEffect(() => {
    if (!target) { lease.current = null; return; }
    const actor = api.getCurrentUserId(), token = api.getAccessToken();
    let live = true;
    const current = () => live && actor === api.getCurrentUserId() && token === api.getAccessToken();
    const cancel = (event?: Event) => {
      if (event instanceof KeyboardEvent && ['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'NumLock'].includes(event.key)) return;
      live = false;
    };
    const value = { current, done: false, cancel: () => cancel() };
    lease.current = value;
    const events = ['pointerdown', 'keydown', 'beforeinput', 'input', 'compositionstart'];
    events.forEach(name => document.addEventListener(name, cancel, true));
    window.addEventListener('freecollab:auth-changed', cancel);
    return () => {
      cancel(); if (lease.current === value) lease.current = null;
      events.forEach(name => document.removeEventListener(name, cancel, true));
      window.removeEventListener('freecollab:auth-changed', cancel);
    };
  }, [scope, kind, target]);
  useEffect(() => {
    const value = lease.current;
    if (!target || !ready || !value || value.done || !value.current()) return;
    value.done = true;
    let frame = 0;
    void callback.current(target, value.current).then(found => {
      if (!found || !value.current()) return;
      frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => {
        if (!value.current()) return;
        const node = [region.current, ...region.current?.querySelectorAll<HTMLElement>('[data-resource-id]') || []]
          .find((item): item is HTMLElement => !!item && item.dataset.resourceId === target && item.dataset.resourceKind === kind);
        if (node && !node.closest('[hidden],[inert]') && node.getClientRects().length) {
          node.focus({ preventScroll: true }); node.scrollIntoView({ block: 'nearest' });
        }
      }); });
    }).catch(() => {});
    return () => cancelAnimationFrame(frame);
  }, [scope, kind, target, ready, region]);
}
