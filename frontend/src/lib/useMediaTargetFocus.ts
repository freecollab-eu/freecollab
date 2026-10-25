import { useEffect, useRef, type RefObject } from 'react';
import { api } from './api';
/** Exact resource lookup and focus are canceled by newer user/session intent. */
export function useMediaTargetFocus(region: RefObject<HTMLElement | null>, channel: string, kind: 'clip' | 'meeting',
  target: string | undefined, resolve: (id: string, current: () => boolean) => Promise<boolean>) {
  const resolver = useRef(resolve); resolver.current = resolve;
  useEffect(() => {
    if (!target) return;
    const actor = api.getCurrentUserId(), token = api.getAccessToken(); let live = true, frame = 0;
    const current = () => live && actor === api.getCurrentUserId() && token === api.getAccessToken();
    const cancel = (event?: Event) => {
      if (event instanceof KeyboardEvent && ['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'NumLock'].includes(event.key)) return;
      live = false; cancelAnimationFrame(frame);
    };
    document.addEventListener('pointerdown', cancel, true); document.addEventListener('keydown', cancel, true);
    for(const event of ['beforeinput','input','compositionstart'])document.addEventListener(event,cancel,true);
    window.addEventListener('freecollab:auth-changed', cancel);
    void resolver.current(target, current).then(found => {
      if (!found || !current()) return;
      frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => {
        if (!current()) return;
        const node = [...region.current?.querySelectorAll<HTMLElement>('[data-media-id]') || []]
          .find(element => element.dataset.mediaId === target && element.dataset.mediaKind === kind);
        if (node && !node.closest('[hidden],[inert]') && node.getClientRects().length) { node.focus({ preventScroll: true }); node.scrollIntoView({ block: 'nearest' }); }
      }); });
    }).catch(() => {});
    return () => {
      cancel(); document.removeEventListener('pointerdown', cancel, true); document.removeEventListener('keydown', cancel, true);
      for(const event of ['beforeinput','input','compositionstart'])document.removeEventListener(event,cancel,true);
      window.removeEventListener('freecollab:auth-changed', cancel);
    };
  }, [channel, kind, target]);
}
