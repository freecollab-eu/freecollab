import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode, type RefObject } from 'react';
import { api } from './api';
import { useCollaborationContext } from './collaboration-context';

const ModalContinuationContext = createContext<string | undefined>(undefined);

/** React ancestry also identifies parent modals whose children use DOM portals. */
export function ModalContinuationProvider({ modalId, children }: { modalId: string; children: ReactNode }) {
  return <ModalContinuationContext.Provider value={modalId}>{children}</ModalContinuationContext.Provider>;
}

/** Parking hides the same modal tree; local forms, selections and loaded data remain mounted. */
export function useCollaborationModal(root: RefObject<HTMLElement>, title: string, resourceKey?: string) {
  const context = useCollaborationContext(), parentId = useContext(ModalContinuationContext), modalId = useId();
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const identity = JSON.stringify([actor, token, context?.workspaceId, context?.channelId, !!context?.secure, context?.surfaceId]);
  const scope = useRef({ identity, generation: 0 });
  if (scope.current.identity !== identity) scope.current = { identity, generation: scope.current.generation + 1 };
  const generation = scope.current.generation;
  const [parked, setParked] = useState(false);
  const register = context?.onRegisterModal, providerCurrent = context?.isCurrent;
  useEffect(() => {
    const element = root.current;
    if (!register || !element) return;
    let mounted = true;
    const isCurrent = () => mounted && scope.current.identity === identity && scope.current.generation === generation
      && root.current === element && element.isConnected && providerCurrent?.() !== false
      && actor === api.getCurrentUserId() && token === api.getAccessToken();
    const unregister = register({ id: modalId, title, resourceKey, parentId, surfaceId: context?.surfaceId, root: element, isCurrent,
      park: () => { if (isCurrent()) setParked(true); },
      resume: () => { if (isCurrent()) setParked(false); },
    });
    return () => { mounted = false; unregister(); };
  }, [root, register, modalId, title, resourceKey, parentId, identity, generation]);
  return { parked, modalId };
}
