import { t, useI18n } from '../i18n';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, type Channel } from '../lib/api';
import { workspaceApi, type Workspace } from '../lib/workspace-api';
import { mayLeaveConversationWork, setConversationNavigationWorkspace } from '../lib/conversation-navigation';
import { AccountPanel } from './AccountPanel';
import { WorkspaceManager } from '../components/WorkspaceManager';
import { DirectMessagePicker } from '../components/DirectMessagePicker';
import { SecureMessagingPanel } from '../components/SecureMessagingPanel';
import '../styles/account.css';

interface AccountContextValue {
  workspaces: Workspace[];
  workspace: Workspace | null;
  workspacesLoading: boolean;
  workspacesError: string;
  isAccountOpen: boolean;
  openAccount: () => void;
  openWorkspaceManager: () => void;
  openWorkspaceCreator: () => void;
  openDirectMessages: () => void;
  openSecureSettings: (channelId?: string) => void;
  selectWorkspace: (id: string) => void;
  refreshWorkspaces: (selectedId?: string, selectionCurrent?: () => boolean) => Promise<void>;
}
const AccountContext = createContext<AccountContextValue | null>(null);
const preferenceKey = (userId: string) => `freecollab.workspace:${userId}`;
function savedWorkspace(userId: string) { if (api.isMemorySession()) return null; try { return localStorage.getItem(preferenceKey(userId)); } catch { return null; } }

export function AccountProvider({ children }: { children: ReactNode }) {
  useI18n();
  const [identity, setIdentity] = useState(api.getCurrentUserId());
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [workspacesLoading, setWorkspacesLoading] = useState(!!identity);
  const [workspacesError, setWorkspacesError] = useState('');
  const [panel, setPanel] = useState<'account' | 'workspace' | 'messages' | 'secure' | null>(null);
  const [createWorkspace, setCreateWorkspace] = useState(false);
  const [secureChannel, setSecureChannel] = useState<string | undefined>();
  const selectedRef = useRef<string | null>(null);
  const identityRef = useRef(identity);
  const sessionRef = useRef(api.getAccessToken());
  const generation = useRef(0), selectionGeneration = useRef(0);
  const mounted = useRef(true);

  const choose = useCallback((id: string | null, userId: string) => {
    if (userId !== api.getCurrentUserId() || userId !== identityRef.current) return;
    const changed = selectedRef.current !== id;
    setConversationNavigationWorkspace(id);
    selectedRef.current = id; setSelected(id);
    if (!api.isMemorySession()) {
      try { if (id) localStorage.setItem(preferenceKey(userId), id); else localStorage.removeItem(preferenceKey(userId)); } catch { /* Selection remains usable without storage. */ }
    }
    if (changed) window.dispatchEvent(new CustomEvent('freecollab:workspace-changed', { detail: { workspace_id: id } }));
  }, []);

  const refreshWorkspaces = useCallback(async (selectedId?: string, selectionCurrent?: () => boolean) => {
    const userId = api.getCurrentUserId();
    const session = api.getAccessToken();
    if (!userId || userId !== identityRef.current) return;
    const request = ++generation.current, selection = selectionGeneration.current;
    const current = () => mounted.current && request === generation.current && userId === api.getCurrentUserId()
      && userId === identityRef.current && session === api.getAccessToken();
    const maySelectRequested = () => {
      try { return selection === selectionGeneration.current && (!selectionCurrent || selectionCurrent()) && selection === selectionGeneration.current; }
      catch { return false; }
    };
    setWorkspacesLoading(true); setWorkspacesError('');
    try {
      const records = await workspaceApi.list();
      if (!current()) return;
      const requested = selectedId && maySelectRequested() ? selectedId : undefined;
      if (!current()) return;
      setWorkspaces(records);
      const previousWorkspace = selectedRef.current;
      const fallback = () => [previousWorkspace, savedWorkspace(userId)].find(candidate => candidate && records.some(record => record.id === candidate)) || records[0]?.id || null;
      let next = requested && records.some(record => record.id === requested) ? requested : fallback();
      // A canceled navigation still refreshes membership and accepts an
      // authoritative fallback, but cannot select or warn for its old target.
      if (requested && next === requested && next !== previousWorkspace
        && records.some(record => record.id === previousWorkspace) && !mayLeaveConversationWork()) next = previousWorkspace;
      const selectionStillCurrent = !requested || maySelectRequested();
      if (!current() || previousWorkspace !== selectedRef.current) return;
      if (!selectionStillCurrent) next = fallback();
      choose(next, userId);
    } catch (failure) {
      if (current()) setWorkspacesError(failure instanceof Error ? failure.message : 'Workspaces could not be loaded.');
    } finally { if (current()) setWorkspacesLoading(false); }
  }, [choose]);

  useEffect(() => {
    mounted.current = true;
    setConversationNavigationWorkspace(selectedRef.current);
    const sync = () => {
      const next = api.getCurrentUserId();
      const session = api.getAccessToken();
      if (next === identityRef.current && session === sessionRef.current) return;
      const changedAccount = next !== identityRef.current;
      setConversationNavigationWorkspace(null);
      sessionRef.current = session;
      generation.current += 1; selectionGeneration.current += 1; identityRef.current = next; selectedRef.current = null;
      setIdentity(next); setWorkspaces([]); setSelected(null); if (changedAccount || !next) setPanel(null); setWorkspacesError(''); setWorkspacesLoading(!!next);
      if (next) {
        void refreshWorkspaces();
        // Keep native MFA setup open while its replacement session is applied,
        // so newly issued recovery codes remain visible until safely stored.
        if (!changedAccount) void api.getMe().then(me => { if (session === api.getAccessToken() && me.session_scope === 'workspace') setPanel(null); }).catch(() => {});
      }
    };
    window.addEventListener('freecollab:auth-changed', sync);
    window.addEventListener('storage', sync);
    return () => { mounted.current = false; generation.current += 1; setConversationNavigationWorkspace(null); window.removeEventListener('freecollab:auth-changed', sync); window.removeEventListener('storage', sync); };
  }, []);

  useEffect(() => { if (identity) void refreshWorkspaces(); }, [identity, refreshWorkspaces]);

  const selectWorkspace = useCallback((id: string) => {
    const userId = api.getCurrentUserId();
    const session = api.getAccessToken();
    const previousWorkspace = selectedRef.current;
    if (!userId || userId !== identityRef.current || !workspaces.some(record => record.id === id)) return;
    // An explicit newer choice, including the same workspace or an ABA path,
    // supersedes a pending refresh even when its own dirty guard is declined.
    ++selectionGeneration.current;
    if (id !== selectedRef.current && !mayLeaveConversationWork()) return;
    if (userId !== api.getCurrentUserId() || userId !== identityRef.current || session !== api.getAccessToken() || previousWorkspace !== selectedRef.current) return;
    setPanel(null); choose(id, userId);
  }, [workspaces, choose]);

  const openAccount = useCallback(() => { if (api.getCurrentUserId() === identityRef.current && identityRef.current) setPanel('account'); }, []);
  const openWorkspaceManager = useCallback(() => { if (api.getCurrentUserId() === identityRef.current && identityRef.current) { setCreateWorkspace(false); setPanel('workspace'); } }, []);
  const openWorkspaceCreator = useCallback(() => { if (api.getCurrentUserId() === identityRef.current && identityRef.current) { setCreateWorkspace(true); setPanel('workspace'); } }, []);
  const openDirectMessages = useCallback(() => { if (api.getCurrentUserId() === identityRef.current && identityRef.current && selectedRef.current) setPanel('messages'); }, []);
  const openSecureSettings = useCallback((channelId?: string) => { if (api.getCurrentUserId() === identityRef.current && identityRef.current) { setSecureChannel(channelId); setPanel('secure'); } }, []);
  const workspace = identity === api.getCurrentUserId() ? workspaces.find(record => record.id === selected) || null : null;
  const visibleWorkspaces = identity === api.getCurrentUserId() ? workspaces : [];
  const value = useMemo(() => ({ workspaces: visibleWorkspaces, workspace, workspacesLoading, workspacesError, isAccountOpen: panel === 'account', openAccount, openWorkspaceManager, openWorkspaceCreator, openDirectMessages, openSecureSettings, selectWorkspace, refreshWorkspaces }), [visibleWorkspaces, workspace, workspacesLoading, workspacesError, panel, openAccount, openWorkspaceManager, openWorkspaceCreator, openDirectMessages, openSecureSettings, selectWorkspace, refreshWorkspaces]);

  const openConversation = (channel: Channel) => {
    if (!identity || identity !== api.getCurrentUserId() || channel.workspace_id !== workspace?.id) return;
    setPanel(null); window.dispatchEvent(new CustomEvent('freecollab:open-channel', { detail: channel }));
  };

  return <AccountContext.Provider value={value}>{children}{identity && identity === api.getCurrentUserId() && <>
    {panel === 'account' && <AccountPanel key={`account:${identity}`} onClose={() => setPanel(null)} onSecureSettings={() => openSecureSettings()} />}
    {panel === 'workspace' && <WorkspaceManager key={`workspace:${identity}`} workspace={workspace} onClose={() => setPanel(null)} onChanged={refreshWorkspaces} initialSection={createWorkspace ? 'create' : 'general'} />}
    {panel === 'messages' && workspace && <DirectMessagePicker key={`messages:${identity}:${workspace.id}`} workspace={workspace} onClose={() => setPanel(null)} onOpen={openConversation} />}
    {panel === 'secure' && <SecureMessagingPanel key={`secure:${identity}:${secureChannel || 'account'}`} channelId={secureChannel} onClose={() => setPanel(null)} />}
  </>}</AccountContext.Provider>;
}

export function useAccount() { const value = useContext(AccountContext); if (!value) throw new Error(t("useAccount must be used within AccountProvider.")); return value; }
