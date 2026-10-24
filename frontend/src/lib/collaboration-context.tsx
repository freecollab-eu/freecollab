import { createContext, useContext, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import { api } from './api';
import { getSlashCommands, type SlashCommand, type SlashContext } from './slash-commands';
import type { ComposerCommandIntent } from './composer-commands';
import type { MessageReference, ResolvedReference } from './message-references';

export type CollaborationInputElement = HTMLInputElement | HTMLTextAreaElement;

/** A caller's exact editing context. Credentials stay in memory, never in DOM attributes. */
export interface CollaborationOrigin {
  contextId: string;
  surfaceId?: string;
  input: CollaborationInputElement;
  value: string;
  references: MessageReference[];
  selectionStart: number;
  selectionEnd: number;
  commandRange?: { start: number; end: number; text: string };
  personIds: string[];
  actorId: string | null;
  token: string;
  workspaceId: string;
  channelId: string;
  secure: boolean;
  /** Identity, field instance and source text must still match. Hidden callers may remain mounted. */
  isCurrent: () => boolean;
  /** The host reveals the caller before explicitly returning its field/caret. */
  restore: () => boolean;
}

/** An explicit modal boundary whose existing DOM can be retained during related work. */
export interface CollaborationModalRegistration {
  id: string;
  title: string;
  resourceKey?: string;
  parentId?: string;
  surfaceId?: string;
  root: HTMLElement;
  isCurrent: () => boolean;
  park: () => void;
  resume: () => void;
}

export interface CollaborationContextValue {
  surfaceId?: string;
  workspaceId?: string;
  channelId?: string;
  secure?: boolean;
  /** Provider-owned committed lifetime/scope lease for captured editing origins. */
  isCurrent?: () => boolean;
  commandContext: SlashContext;
  onCommand: (command: SlashCommand, intent: ComposerCommandIntent, origin: CollaborationOrigin) => boolean | void | Promise<boolean | void>;
  onOpenReference?: (reference: ResolvedReference, origin?: CollaborationOrigin, sourceCurrent?: () => boolean, sourceElement?: HTMLElement) => void | Promise<void>;
  onRegisterModal?: (modal: CollaborationModalRegistration) => () => void;
}

const Context = createContext<CollaborationContextValue | null>(null);

/** Explicitly wraps product writing surfaces. It never scans or intercepts arbitrary inputs. */
export function CollaborationProvider({ value, children }: { value: CollaborationContextValue; children: ReactNode }) {
  const actor = api.getCurrentUserId(), token = api.getAccessToken();
  const identity = JSON.stringify([actor, token, value.workspaceId, value.channelId, !!value.secure, value.surfaceId]);
  const mounted = useRef(false);
  useLayoutEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const scope = useRef({ identity, generation: 0 });
  if (scope.current.identity !== identity) scope.current = { identity, generation: scope.current.generation + 1 };
  const generation = scope.current.generation;
  const live = useRef({ value, identity }); live.current = { value, identity };
  const registerModal = useMemo(() => {
    const current = () => mounted.current && generation === scope.current.generation && identity === live.current.identity
      && actor === api.getCurrentUserId() && token === api.getAccessToken();
    return (modal: CollaborationModalRegistration) => {
      if (!current() || modal.surfaceId !== live.current.value.surfaceId) return () => {};
      const sourceCurrent = modal.isCurrent;
      return live.current.value.onRegisterModal?.({ ...modal, isCurrent: () => current() && sourceCurrent(),
        park: () => { if (current() && sourceCurrent()) modal.park(); },
        resume: () => { if (current() && sourceCurrent()) modal.resume(); },
      }) || (() => {});
    };
  }, [identity, generation]);
  const guarded = useMemo<CollaborationContextValue>(() => {
    const current = () => mounted.current && generation === scope.current.generation && identity === live.current.identity
      && actor === api.getCurrentUserId() && token === api.getAccessToken();
    return {
      ...value,
      isCurrent: current,
      onRegisterModal: value.onRegisterModal ? registerModal : undefined,
      onCommand(command, intent, origin) {
        if (!current() || !origin.isCurrent() || origin.actorId !== actor || origin.token !== token
          || origin.workspaceId !== live.current.value.workspaceId || origin.channelId !== live.current.value.channelId
          || origin.secure !== !!live.current.value.secure || origin.surfaceId !== live.current.value.surfaceId
          || !getSlashCommands(live.current.value.commandContext).some(item => item.id === command.id)) return false;
        return live.current.value.onCommand(command, intent, origin);
      },
      onOpenReference(reference, origin, sourceCurrent, sourceElement) {
        // The host may await a second file/version lookup. Pass the exact
        // source lease through so closing the reader also cancels that result.
        const canOpen = () => current() && reference.available && (!origin || origin.isCurrent()) && (!sourceCurrent || sourceCurrent());
        if (!canOpen()) return;
        return live.current.value.onOpenReference?.(reference, origin, canOpen, sourceElement);
      },
    };
  }, [identity, generation, value, registerModal]);
  return <Context.Provider value={guarded}>{children}</Context.Provider>;
}

/** Standalone panels retain ordinary native inputs when no collaboration host is present. */
export function useCollaborationContext() { return useContext(Context); }
