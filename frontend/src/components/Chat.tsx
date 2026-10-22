import { t, useI18n, currentLocale } from '../i18n';
import { Fragment, useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';
import { api, APIError, Channel, Message, Reaction } from '../lib/api';
import { getSocket, disconnectSocket } from '../lib/socket';
import {
  Hash,
  Send,
  Plus,
  LogOut,
  Users,
  Lock,
  Settings,
  Paperclip,
  Menu,
  X,
  MessageCircle,
  Smile,
  Edit3,
  Trash2,
  Check,
  CornerDownRight,
  Palette,
  ArrowUpRight,
  RefreshCw,
  Search,
  ArrowDown,
  Plug,
  Layers,
  Smartphone,
  MoreHorizontal,
  Compass,
  ClipboardCheck,
  Clock,
  Bell,
} from 'lucide-react';
import { TypingIndicator, useTypingEmitter } from './TypingIndicator';
import { PresenceIndicator } from './PresenceIndicator';
import { NotificationCenter, NotificationBell } from './NotificationCenter';
import { ChannelTools } from './ChannelTools';
import { useAppearance } from '../appearance/AppearanceProvider';
import { CommandPalette, type CommandAction } from './CommandPalette';
import { useAccount } from '../account/AccountProvider';
import { WorkspaceSwitcher } from './WorkspaceSwitcher';
import { HistorySearchPanel } from './HistorySearchPanel';
import { getSecureMessaging } from '../lib/secure-client';
import { CollaborationPanel, MessageSaveActions, ThreadWatchButton } from './CollaborationPanel';
import { Brand } from './Brand';
import { IntegrationsPanel } from './IntegrationsPanel';
import { WorkspaceLaunchPanel } from './WorkspaceLaunchPanel';
import { WorkspaceFeedbackDialog } from './WorkspaceFeedbackDialog';
import { shouldSuggestWorkspaceLaunch } from '../lib/workspace-launch';
import { WorkHub, type WorkHubTab } from './WorkHub';
import { HistoricalSlackDetails } from './HistoricalSlackDetails';
import { ChatProductivityPanel } from './ChatProductivityPanel';
import { MyDay } from './MyDay';
import type { PersonalSource } from '../lib/my-day';
import { MeetingDock } from './MeetingDock';
import { reconcileMessageChanges } from '../lib/message-reconcile';
import { MobileSettings, MobileStatus } from './MobileSettings';
import { mobileDrafts, mobileOutbox } from '../mobile';
import { offlineLibrary } from '../mobile/library';
import { useModalFocus } from '../lib/useModalFocus';
import '../styles/chat.css';

interface ChatProps {
  onLogout: (warning?: string) => void;
  onAdmin?: () => void;
  initialChannelId?: string | null;
  /** A focused trusted embed cannot navigate to a different channel. */
  embeddedChannelId?: string;
  initialSyncReturn?: { workspaceId: string; connectionId: string } | null;
}

const REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '😡', '🎉', '🚀'];

export function Chat({ onLogout, onAdmin, initialChannelId, initialSyncReturn, embeddedChannelId }: ChatProps) {
  useI18n();
  const { openAppearance } = useAppearance();
  const { workspace, workspacesLoading, workspacesError, openAccount, openDirectMessages, openSecureSettings, refreshWorkspaces } = useAccount();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [activeChannel, setActiveChannel] = useState<Channel | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [newMessage, setNewMessage] = useState('');
  const [showNewChannel, setShowNewChannel] = useState(false);
  const [newChannelName, setNewChannelName] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(() => window.matchMedia('(min-width: 768px)').matches);
  const [loading, setLoading] = useState(true);
  const [channelError, setChannelError] = useState('');
  const [messageError, setMessageError] = useState('');
  const [encryptionLocked, setEncryptionLocked] = useState(false);
  const [actionError, setActionError] = useState('');
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [connection, setConnection] = useState('Connecting…');
  const [toolPanel, setToolPanel] = useState<'members' | 'files' | null>(null);
  const [collaborationTab, setCollaborationTab] = useState<'pins' | 'bookmarks' | 'decisions' | 'notifications' | null>(null);
  const [focusDecisionId, setFocusDecisionId] = useState<string | undefined>();
  const [decisionSource, setDecisionSource] = useState<Message | undefined>();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [myDayOpen, setMyDayOpen] = useState(false);
  const [productivity, setProductivity] = useState<{message?:Message;draft?:string;mentions?:string[]}|null>(null);
  const [integrationsOpen, setIntegrationsOpen] = useState(false);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [nativeSession, setNativeSession] = useState(false);
  const [integrationEntry, setIntegrationEntry] = useState<{ tab: 'connections' | 'health' | 'calendar'; channelId?: string; revision: number } | null>(null);
  const [workHubOpen, setWorkHubOpen] = useState(false);
  const [workHubInitialTab, setWorkHubInitialTab] = useState<WorkHubTab>('tasks');
  const [workHubTask, setWorkHubTask] = useState<{ projectId?: string; taskId?: string } | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [mobileToolsOpen, setMobileToolsOpen] = useState(false);
  const [mobileNotice, setMobileNotice] = useState('');
  const [policyNotice, setPolicyNotice] = useState(false);
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [selectedMentions, setSelectedMentions] = useState<string[]>([]);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [secureAvailable, setSecureAvailable] = useState(false);
  const [createSecure, setCreateSecure] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);
  const [memberNames, setMemberNames] = useState<Record<string, string>>({});
  
  // New feature states
  const [reactions, setReactions] = useState<Record<string, Reaction[]>>({});
  const [showReactionPicker, setShowReactionPicker] = useState<string | null>(null);
  const [editingMessage, setEditingMessage] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [threadMessage, setThreadMessage] = useState<Message | null>(null);
  const [threadReplies, setThreadReplies] = useState<Message[]>([]);
  const [threadReplyContent, setThreadReplyContent] = useState('');
  const [unreadCounts, setUnreadCounts] = useState<Record<string, number>>({});
  const [showNotifications, setShowNotifications] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [pendingMessages, setPendingMessages] = useState(0);
  const [highlightedMessage, setHighlightedMessage] = useState<string | null>(null);
  
  const channelsRequestRef = useRef(0);
  const workspaceRef = useRef(workspace?.id); workspaceRef.current = workspace?.id;
  const channelsWorkspaceRef = useRef<string | null>(null);
  const pendingJumpRef = useRef<Message | null>(null);
  const pendingPersonalSource = useRef<(PersonalSource & { token: string }) | null>(null);
  const personalNavigation = useRef(0);
  const timelineRef = useRef<HTMLDivElement>(null);
  const nearBottomRef = useRef(true);
  const followingLatestRef = useRef(false);
  const followTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const positionedChannelRef = useRef<string | null>(null);
  const observedMessagesRef = useRef<{ channelId: string | null; ids: Set<string>; lastTime: number }>({ channelId: null, ids: new Set(), lastTime: 0 });
  const lastReadRef = useRef<Record<string, string>>({});
  const highlightTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const composingRef = useRef(false);
  const activeChannelRef = useRef<string | null>(null);
  const threadMessageRef = useRef<Message | null>(null);
  const messageRequestRef = useRef(0);
  const nativeSyncRef=useRef<{channelId:string;token:string|null;cursor:number}|null>(null);
  const messagesRef = useRef<Message[]>([]);
  const draftsRef = useRef<Record<string, string>>({});
  const draftWrites = useRef(new Map<string, Promise<void>>());
  const mobileDialogRef = useRef<HTMLDivElement>(null);
  const mobileToolsRef = useRef<HTMLDivElement>(null);
  const mobileToolsTrigger = useRef<HTMLButtonElement>(null);
  const closeMobileTools = (restoreFocus = false) => {
    setMobileToolsOpen(false);
    if (restoreFocus) mobileToolsTrigger.current?.focus({ preventScroll: true });
  };
  const openMobileTool = (open: () => void) => {
    // Dialogs remember a visible return target rather than the hidden menu item.
    closeMobileTools(true);
    open();
  };
  messagesRef.current = messages;
  activeChannelRef.current = activeChannel?.id || null;
  threadMessageRef.current = threadMessage;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const createDialogRef = useRef<HTMLDivElement>(null);
  const threadDialogRef = useRef<HTMLElement>(null);
  const sidebarRef = useCallback((element: HTMLElement | null) => { if (element) element.inert = !sidebarOpen; }, [sidebarOpen]);
  useModalFocus(mobileDialogRef, () => setMobileOpen(false), mobileOpen);
  useModalFocus(createDialogRef, () => setShowNewChannel(false), showNewChannel);
  useModalFocus(threadDialogRef, () => setThreadMessage(null), !!threadMessage);

  useEffect(() => {
    const cleanup = setupSocket();
    const owner = api.getAccessToken(); let alive = true;
    void api.getMe().then(me => { if (alive && owner === api.getAccessToken()) setNativeSession(me.session_scope !== 'workspace' && me.account_type !== 'managed'); }).catch(() => {});
    void api.request<{secure_messaging:boolean}>('/capabilities').then(data => setSecureAvailable(data.secure_messaging)).catch(() => {});
    return () => { alive = false; cleanup(); };
  }, []);

  useEffect(() => {
    setActiveChannel(null); setChannels([]); setMessages([]); setHistoryOpen(false); setIntegrationsOpen(false); setWorkHubOpen(false); setCollaborationTab(null); setLaunchOpen(false); setFeedbackOpen(false); setIntegrationEntry(null);
    draftsRef.current = {};
    if (workspace) { setLoading(true); void loadChannels(); } else setLoading(workspacesLoading);
  }, [workspace?.id]);

  const persistDraft = (channelId: string, content: string, secure: boolean) => {
    draftsRef.current[channelId] = content;
    const token = api.getAccessToken();
    const previous = draftWrites.current.get(channelId) || Promise.resolve();
    const pending = previous.catch(() => {}).then(async () => {
      if (token === api.getAccessToken()) await mobileDrafts.save(channelId, content, secure);
    }).catch(() => { if (token === api.getAccessToken() && activeChannelRef.current === channelId) setActionError('This draft could not be saved on this device. Keep this page open until it is sent.'); });
    draftWrites.current.set(channelId, pending);
  };

  useEffect(() => {
    const openActivity = () => { setShowNotifications(true); if (window.location.hash === '#mobile=activity') window.history.replaceState(null, '', window.location.pathname + window.location.search); };
    const message = (event: MessageEvent) => { if (event.data?.type === 'open_activity') openActivity(); };
    const sent = (event: Event) => { const record = (event as CustomEvent<Message>).detail; if (record?.channel_id === activeChannelRef.current) void loadMessages(record.channel_id); };
    const restored = (event: Event) => { const channelId = (event as CustomEvent<{channelId:string}>).detail?.channelId; const token = api.getAccessToken(), previous = draftsRef.current[channelId]; if (channelId === activeChannelRef.current) void mobileDrafts.load(channelId).then(value => { if (token === api.getAccessToken() && activeChannelRef.current === channelId && draftsRef.current[channelId] === previous) { draftsRef.current[channelId] = value; setNewMessage(value); } }).catch(() => {}); };
    if (window.location.hash === '#mobile=activity') openActivity();
    navigator.serviceWorker?.addEventListener('message', message);
    window.addEventListener('freecollab:offline-message-sent', sent);
    window.addEventListener('freecollab:offline-draft-restored', restored);
    return () => { navigator.serviceWorker?.removeEventListener('message', message); window.removeEventListener('freecollab:offline-message-sent', sent); window.removeEventListener('freecollab:offline-draft-restored', restored); };
  }, []);

  useEffect(() => { if (!workspace && !workspacesLoading) setLoading(false); }, [workspace?.id, workspacesLoading]);
  useEffect(() => { if (initialSyncReturn && workspace?.id === initialSyncReturn.workspaceId) setIntegrationsOpen(true); }, [workspace?.id, initialSyncReturn?.workspaceId, initialSyncReturn?.connectionId]);

  useEffect(() => {
    const open = (event: Event) => {
      const channel = (event as CustomEvent<Channel>).detail;
      if (!channel || channel.workspace_id !== workspaceRef.current || embeddedChannelId && channel.id !== embeddedChannelId) return;
      setChannels(old => old.some(item => item.id === channel.id) ? old.map(item => item.id === channel.id ? channel : item) : [...old, channel]);
      selectChannel(channel);
    };
    const openTools = (event: Event) => {
      const detail = (event as CustomEvent<{channel: Channel; tab: WorkHubTab}>).detail;
      if (!detail?.channel || detail.channel.workspace_id !== workspaceRef.current || embeddedChannelId && detail.channel.id !== embeddedChannelId || !['feed', 'workflows', 'cloud'].includes(detail.tab)) return;
      selectChannel(detail.channel); setWorkHubInitialTab(detail.tab); setWorkHubOpen(true);
    };
    const reload = () => { void loadChannels(); };
    window.addEventListener('freecollab:channels-updated', reload);
    window.addEventListener('freecollab:open-channel', open);
    window.addEventListener('freecollab:open-workspace-tools', openTools);
    return () => { window.removeEventListener('freecollab:open-workspace-tools', openTools); window.removeEventListener('freecollab:open-channel', open); window.removeEventListener('freecollab:channels-updated', reload); };
  }, []);

  useEffect(() => {
    const openDecision = (event: Event) => {
      const detail = (event as CustomEvent<{channel_id:string;decision_id:string}>).detail;
      const channel = channels.find(item => item.id === detail?.channel_id);
      if (!channel) return;
      setHistoryOpen(false); setDecisionSource(undefined); setFocusDecisionId(detail.decision_id);
      setActiveChannel(channel); setCollaborationTab('decisions');
    };
    window.addEventListener('freecollab:open-decision',openDecision);
    return () => window.removeEventListener('freecollab:open-decision',openDecision);
  },[channels]);

  useEffect(() => {
    const open = (event:Event) => {
      const detail=(event as CustomEvent<{channel_id:string;kind:string;id:string;parent_id?:string}>).detail;
      const channel=channels.find(item=>item.id===detail?.channel_id);
      if(!channel||embeddedChannelId&&channel.id!==embeddedChannelId)return;
      setHistoryOpen(false);selectChannel(channel);
      if(['file','file-content'].includes(detail.kind)){setToolPanel('files');return;}
      const target:Record<string,WorkHubTab>={document:'documents',task:'tasks',project:'tasks',feed:'feed'};
      if(target[detail.kind]){setWorkHubInitialTab(target[detail.kind]);setWorkHubOpen(true);}
    };
    window.addEventListener('freecollab:open-search-source',open);
    return()=>window.removeEventListener('freecollab:open-search-source',open);
  },[channels,embeddedChannelId]);

  useEffect(() => {
    const open = async (event: Event) => {
      const detail = (event as CustomEvent<PersonalSource>).detail;
      if (!detail || typeof detail.channel_id !== 'string' || typeof detail.workspace_id !== 'string'
          || !['task', 'calendar', 'message', 'meeting'].includes(detail.kind)
          || embeddedChannelId && detail.channel_id !== embeddedChannelId) return;
      const token = api.getAccessToken(), request = ++personalNavigation.current;
      if (!token) return;
      try {
        // Device notifications and a retained personal view are navigation
        // hints. Neither grants a channel, workspace or meeting admission.
        const channel = await api.getChannel(detail.channel_id);
        if (request !== personalNavigation.current || token !== api.getAccessToken()) return;
        if (channel.workspace_id !== detail.workspace_id) throw new Error('This source is no longer available in your current workspace.');
        pendingPersonalSource.current = { ...detail, token };
        setMyDayOpen(false);
        if (workspaceRef.current !== channel.workspace_id) await refreshWorkspaces(channel.workspace_id);
        else { setChannels(rows => rows.some(row => row.id === channel.id) ? rows.map(row => row.id === channel.id ? channel : row) : [...rows, channel]); }
      } catch (failure) {
        if (request === personalNavigation.current && token === api.getAccessToken()) {
          pendingPersonalSource.current = null; setActionError(failure instanceof Error ? failure.message : 'This source is no longer available in your current workspace.');
        }
      }
    };
    window.addEventListener('freecollab:open-personal-source', open);
    return () => { ++personalNavigation.current; window.removeEventListener('freecollab:open-personal-source', open); };
  }, [embeddedChannelId, refreshWorkspaces]);

  useEffect(() => {
    const target = pendingPersonalSource.current;
    if (!target) return;
    if (target.token !== api.getAccessToken()) { pendingPersonalSource.current = null; return; }
    if (workspace?.id !== target.workspace_id || channelsWorkspaceRef.current !== target.workspace_id || loading) return;
    const channel = channels.find(row => row.id === target.channel_id);
    pendingPersonalSource.current = null;
    if (!channel) { setActionError('This source is no longer available in your current workspace.'); return; }
    if (target.kind === 'message' && target.item_id) pendingJumpRef.current = { id: target.item_id, channel_id: channel.id, author_id: '', content: '', encrypted: !!channel.secure, created_at: '' };
    selectChannel(channel);
    if (target.kind === 'message' && activeChannelRef.current === channel.id) void loadMessages(channel.id);
    if (target.kind === 'task' || target.kind === 'meeting') { setWorkHubTask(target.kind === 'task' ? { projectId: target.project_id, taskId: target.item_id } : null); setWorkHubInitialTab(target.kind === 'task' ? 'tasks' : 'meetings'); setWorkHubOpen(true); }
    if (target.kind === 'calendar') { setIntegrationEntry(previous => ({ tab: 'calendar', channelId: channel.id, revision: (previous?.revision || 0) + 1 })); setIntegrationsOpen(true); }
  }, [channels, workspace?.id, loading]);

  useEffect(() => {
    const media = window.matchMedia('(min-width: 768px)');
    const resize = () => { setSidebarOpen(media.matches); if (media.matches) setMobileToolsOpen(false); };
    media.addEventListener('change', resize);
    return () => media.removeEventListener('change', resize);
  }, []);

  useEffect(() => { setMobileToolsOpen(false); setProductivity(null); }, [activeChannel?.id, workspace?.id]);
  useEffect(()=>{
    if(!activeChannel||activeChannel.secure)return;
    const channelId=activeChannel.id,token=api.getAccessToken();let live=true,busy=false;
    const poll=async()=>{
      const state=nativeSyncRef.current;
      if(!live||busy||!navigator.onLine||document.visibilityState==='hidden'||!state||state.channelId!==channelId||state.token!==token||token!==api.getAccessToken())return;
      busy=true;
      try{
        for(let page=0;page<4;page++){
          const data=await api.request<{messages:Message[];next_cursor:number;has_more:boolean}>('/messages/channels/'+encodeURIComponent(channelId)+'/sync?after='+state.cursor+'&limit=100');
          if(!live||token!==api.getAccessToken()||activeChannelRef.current!==channelId||nativeSyncRef.current!==state)return;
          if(data.next_cursor<state.cursor)throw new Error('Message cursor moved backwards.');
          setMessages(current=>reconcileMessageChanges(current,data.messages));
          const parentId=threadMessageRef.current?.id;
          if(parentId){
            const parent=data.messages.find(message=>message.id===parentId);
            if(parent?.deleted){setThreadMessage(null);setThreadReplies([]);}
            else{if(parent)setThreadMessage(parent);setThreadReplies(current=>reconcileMessageChanges(current,data.messages,parentId));}
          }
          state.cursor=data.next_cursor;
          if(!data.has_more)break;
        }
        window.dispatchEvent(new Event('freecollab:notifications-updated'));
      }catch(failure){
        if(live&&token===api.getAccessToken()&&failure instanceof APIError){
          if(failure.status===400){nativeSyncRef.current=null;void loadMessages(channelId);}
          else if([401,403,404,409].includes(failure.status)){
            nativeSyncRef.current=null;setMessages([]);setThreadMessage(null);setThreadReplies([]);setMessageError(failure.message);
            if(failure.status!==401)void loadChannels();
          }
        }
      }
      finally{busy=false;}
    };
    const interval=window.setInterval(()=>void poll(),15000),resume=()=>void poll();
    window.addEventListener('online',resume);document.addEventListener('visibilitychange',resume);
    return()=>{live=false;window.clearInterval(interval);window.removeEventListener('online',resume);document.removeEventListener('visibilitychange',resume);};
  },[activeChannel?.id,activeChannel?.secure]);
  useEffect(()=>{const warn=()=>setPolicyNotice(true);window.addEventListener('freecollab:policy-warning',warn);return()=>window.removeEventListener('freecollab:policy-warning',warn);},[]);
  useEffect(() => {
    if (!mobileToolsOpen) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !mobileToolsRef.current?.contains(event.target)) closeMobileTools(true);
    };
    document.addEventListener('pointerdown', outside, true);
    return () => document.removeEventListener('pointerdown', outside, true);
  }, [mobileToolsOpen]);

  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || commandOpen || document.querySelector('.appearance-panel')) return;
      if (mentionOpen) setMentionOpen(false);
      else if (showNewChannel) setShowNewChannel(false);
      else if (showReactionPicker) setShowReactionPicker(null);
      else if (showNotifications) setShowNotifications(false);
      else if (threadMessage) setThreadMessage(null);
      else if (toolPanel) setToolPanel(null);
      else if (window.matchMedia('(max-width: 767px)').matches) setSidebarOpen(false);
    };
    document.addEventListener('keydown', dismiss);
    return () => document.removeEventListener('keydown', dismiss);
  }, [commandOpen, showNewChannel, showReactionPicker, showNotifications, threadMessage, toolPanel, mentionOpen]);

  useEffect(() => {
    const openCommands = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229 || composingRef.current || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'k') return;
      if (document.querySelector('[aria-modal="true"]') && !commandOpen) return;
      event.preventDefault();
      setCommandOpen((open) => !open);
    };
    document.addEventListener('keydown', openCommands);
    return () => document.removeEventListener('keydown', openCommands);
  }, [commandOpen]);

  useEffect(() => () => { clearTimeout(highlightTimeoutRef.current); clearTimeout(followTimeoutRef.current); }, []);

  useEffect(() => {
    const unlocked = () => { setEncryptionLocked(false); const channel = activeChannelRef.current; if (channel) void loadMessages(channel); };
    window.addEventListener('freecollab:encryption-unlocked', unlocked);
    return () => window.removeEventListener('freecollab:encryption-unlocked', unlocked);
  }, []);

  useEffect(() => {
    setMessages([]);
    setHasOlder(false); setLoadingOlder(false); setSelectedMentions([]); setMentionOpen(false); setCollaborationTab(null);
    setPendingMessages(0);
    setHighlightedMessage(null);
    setCommandOpen(false);
    composingRef.current = false;
    nearBottomRef.current = true;
    followingLatestRef.current = false;
    clearTimeout(followTimeoutRef.current);
    positionedChannelRef.current = null;
    observedMessagesRef.current = { channelId: activeChannel?.id || null, ids: new Set(), lastTime: 0 };
    setReactions({});
    setThreadMessage(null);
    setThreadReplies([]);
    setEditingMessage(null);
    setShowReactionPicker(null);
    setMessageError(''); setEncryptionLocked(false); setActionError(''); setToolPanel(null);
    setMemberNames({});
    setNewMessage(activeChannel ? draftsRef.current[activeChannel.id] || '' : '');
    if (activeChannel && !(activeChannel.id in draftsRef.current)) {
      const id = activeChannel.id, token = api.getAccessToken();
      void mobileDrafts.load(id).then(value => { if (token === api.getAccessToken() && activeChannelRef.current === id && !(id in draftsRef.current)) { draftsRef.current[id] = value; setNewMessage(value); } }).catch(() => {});
    }
    if (!activeChannel) return;

    const channelId = activeChannel.id;
    void refreshMemberNames(channelId);
    const socket = getSocket();
    const joinActiveChannel = () => {
      socket.emit('join', { channel_id: channelId }, (result: { ok?: boolean; error?: string }) => {
        // Fetch only after subscribing so no message falls between snapshot and join.
        if (result?.ok && activeChannelRef.current === channelId) loadMessages(channelId);
        else if (activeChannelRef.current === channelId) {
          setMessageError('Channel access was denied. Refresh your channel list to continue.');
          setMessages([]);
        }
      });
    };
    socket.on('connect', joinActiveChannel);
    if (socket.connected) joinActiveChannel();
    else loadMessages(channelId);
    loadUnreadCounts();

    return () => {
      socket.off('connect', joinActiveChannel);
      if (socket.connected) socket.emit('leave', { channel_id: channelId });
    };
  }, [activeChannel?.id]);

  const scrollBehavior = (): ScrollBehavior => window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';

  const markLatestRead = () => {
    if (document.visibilityState !== 'visible' || document.querySelector('[aria-modal="true"]')) return;
    const channelId = activeChannelRef.current;
    const timeline = messagesRef.current.filter((message) => message.channel_id === channelId);
    const lastMessage = timeline[timeline.length - 1];
    if (!channelId || !lastMessage || lastReadRef.current[channelId] === lastMessage.id) return;
    lastReadRef.current[channelId] = lastMessage.id;
    void api.markAsRead(channelId, lastMessage.id).then(() => {
      setUnreadCounts((counts) => ({ ...counts, [channelId]: 0 }));
    }).catch(() => { delete lastReadRef.current[channelId]; });
  };

  useEffect(() => {
    let frame = 0;
    const reconcileRead = () => {
      cancelAnimationFrame(frame);
      // Shared modal cleanup restores focus before the dialog DOM is removed.
      frame = requestAnimationFrame(() => { if (nearBottomRef.current) markLatestRead(); });
    };
    document.addEventListener('visibilitychange', reconcileRead);
    document.addEventListener('focusin', reconcileRead);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('visibilitychange', reconcileRead);
      document.removeEventListener('focusin', reconcileRead);
    };
  }, []);

  const scrollToLatest = (behavior: ScrollBehavior = scrollBehavior()) => {
    const timeline = timelineRef.current;
    if (!timeline) return;
    nearBottomRef.current = true;
    followingLatestRef.current = behavior === 'smooth';
    clearTimeout(followTimeoutRef.current);
    if (followingLatestRef.current) followTimeoutRef.current = setTimeout(() => { followingLatestRef.current = false; handleTimelineScroll(); }, 700);
    timeline.scrollTo({ top: timeline.scrollHeight, behavior });
    setPendingMessages(0);
    markLatestRead();
  };

  const handleTimelineScroll = () => {
    const timeline = timelineRef.current;
    if (!timeline) return;
    const atBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 96;
    if (followingLatestRef.current && !atBottom) return;
    nearBottomRef.current = atBottom;
    if (atBottom) { followingLatestRef.current = false; clearTimeout(followTimeoutRef.current); }
    if (nearBottomRef.current) {
      setPendingMessages(0);
      markLatestRead();
    }
  };

  useLayoutEffect(() => {
    const channelId = activeChannel?.id;
    const timeline = messages.filter((message) => message.channel_id === channelId);
    if (!channelId || timeline.length === 0) return;
    const previous = observedMessagesRef.current;
    const added = previous.channelId === channelId
      ? timeline.filter((message) => !previous.ids.has(message.id) && new Date(message.created_at).getTime() >= previous.lastTime)
      : [];
    observedMessagesRef.current = { channelId, ids: new Set(timeline.map((message) => message.id)), lastTime: new Date(timeline[timeline.length - 1].created_at).getTime() };
    const initial = positionedChannelRef.current !== channelId;
    positionedChannelRef.current = channelId;
    if (initial || nearBottomRef.current || added.some((message) => message.author_id === api.getCurrentUserId())) {
      scrollToLatest(initial ? 'auto' : scrollBehavior());
    } else if (added.length > 0) {
      setPendingMessages((count) => count + added.length);
    }
  }, [messages, activeChannel?.id]);

  const resizeComposer = () => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 168) + 'px';
    if (nearBottomRef.current) scrollToLatest('auto');
  };

  useLayoutEffect(resizeComposer, [newMessage, activeChannel?.id]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    let frame = 0;
    let width = input.clientWidth;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(resizeComposer);
    };
    // Observe width only: changing the textarea's own height must not form a loop.
    const resize = new ResizeObserver(() => {
      if (input.clientWidth === width) return;
      width = input.clientWidth;
      schedule();
    });
    resize.observe(input);
    const appearance = new MutationObserver(schedule);
    appearance.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
    window.addEventListener('resize', schedule);
    document.fonts.addEventListener('loadingdone', schedule);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      appearance.disconnect();
      window.removeEventListener('resize', schedule);
      document.fonts.removeEventListener('loadingdone', schedule);
    };
  }, [loading, activeChannel?.id]);

  const setupSocket = () => {
    const socket = getSocket();
    const onConnect = () => setConnection('Connected');
    const onDisconnect = () => setConnection('Connection interrupted. Reconnecting…');
    const onConnectError = () => setConnection('Unable to connect live. Refresh messages or try again shortly.');
    if (socket.connected) setConnection('Connected');
    const onMessage = (msg: Message) => {
      if (msg.channel_id !== activeChannelRef.current) return;
      api.rememberMessage(msg);
      if (msg.parent_id) {
        if (threadMessageRef.current?.id === msg.parent_id) {
          setThreadReplies((prev) => prev.some((reply) => reply.id === msg.id) ? prev : [...prev, msg]);
        }
        return;
      }
      setMessages((prev) => prev.some((message) => message.id === msg.id) ? prev : [...prev, msg]);
    };
    const onEdited = (data: { id: string; content: string; edited_at: string }) => {
      const update = (message: Message) => message.id === data.id ? { ...message, content: data.content, edited_at: data.edited_at } : message;
      setMessages((prev) => prev.map(update));
      setThreadReplies((prev) => prev.map(update));
      setThreadMessage((prev) => prev ? update(prev) : prev);
    };
    const onDeleted = (data: { id: string }) => {
      const update = (message: Message) => message.id === data.id ? { ...message, deleted: true, content: '[Message deleted]' } : message;
      setMessages((prev) => prev.map(update));
      setThreadReplies((prev) => prev.map(update));
      if (threadMessageRef.current?.id === data.id) {
        setThreadMessage(null);
        setThreadReplies([]);
      }
    };
    const onReaction = (data: { message_id: string }) => loadReactions(data.message_id);
    const onThreadReply = (data: { parent_id: string; reply: Message; thread_count: number }) => {
      if (data.reply.channel_id !== activeChannelRef.current) return;
      setMessages((prev) => prev.map((message) => message.id === data.parent_id ? { ...message, thread_count: data.thread_count } : message));
      if (threadMessageRef.current?.id === data.parent_id) {
        setThreadReplies((prev) => prev.some((reply) => reply.id === data.reply.id) ? prev : [...prev, data.reply]);
      }
    };
    const onMention = (data: { author_name: string }) => {
      if ('Notification' in window && Notification.permission === 'granted') {
        new Notification(`@${data.author_name} mentioned you`);
      }
    };
    const onChannelRemoved = (data: { channel_id: string }) => {
      setChannels((records) => records.filter((record) => record.id !== data.channel_id));
      delete draftsRef.current[data.channel_id];
      if (activeChannelRef.current === data.channel_id) {
        setActiveChannel(null); setMessages([]); setToolPanel(null); setNewMessage('');
      }
    };
    const onMembersChanged = (data: { channel_id: string }) => {
      if (data.channel_id === activeChannelRef.current) void refreshMemberNames(data.channel_id);
    };
    let unsubscribeSecure: (() => void) | undefined;
    let disposed = false;
    void getSecureMessaging().then(secure => {
      if (disposed) return;
      unsubscribeSecure = secure.subscribe((event, data) => {
        if (event === 'new_message') onMessage(data as unknown as Message);
        else if (event === 'message_updated') onEdited({ id: String(data.id), content: String(data.content), edited_at: String(data.edited_at || new Date().toISOString()) });
        else if (event === 'message_deleted') onDeleted({ id: String(data.id || data.message_id) });
        else if (event === 'reaction_updated') onReaction({ message_id: String(data.message_id) });
        else if (event === 'thread_reply') onThreadReply(data as unknown as { parent_id: string; reply: Message; thread_count: number });
        else if (event === 'secure_metadata') {
          const patch = (channel: Channel) => channel.id === data.channel_id && channel.type !== 'direct' ? {...channel, name: String(data.name), secure:true} : channel;
          setChannels(old => old.map(patch)); setActiveChannel(old => old ? patch(old) : old);
        }
      });
    });
    socket.on('message', onMessage);
    socket.on('message_edited', onEdited);
    socket.on('message_deleted', onDeleted);
    socket.on('reaction', onReaction);
    socket.on('reaction_added', onReaction);
    socket.on('reaction_removed', onReaction);
    socket.on('thread_reply', onThreadReply);
    socket.on('mention', onMention);
    const onChannelsChanged=(data:{workspace_id?:string})=>{if(!data.workspace_id||data.workspace_id===workspaceRef.current)void loadChannels();};
    socket.on('channel_removed', onChannelRemoved);
    socket.on('channels_changed',onChannelsChanged);
    socket.on('members_changed', onMembersChanged);
    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('connect_error', onConnectError);

    return () => {
      disposed = true; unsubscribeSecure?.();
      socket.off('message', onMessage);
      socket.off('message_edited', onEdited);
      socket.off('message_deleted', onDeleted);
      socket.off('reaction', onReaction);
      socket.off('reaction_added', onReaction);
      socket.off('reaction_removed', onReaction);
      socket.off('thread_reply', onThreadReply);
      socket.off('mention', onMention);
      socket.off('channel_removed', onChannelRemoved);
      socket.off('channels_changed',onChannelsChanged);
      socket.off('members_changed', onMembersChanged);
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('connect_error', onConnectError);
    };
  };

  const loadChannels = async () => {
    const scope = workspaceRef.current;
    if (!scope) return;
    const request = ++channelsRequestRef.current;
    setChannelError('');
    try {
      const permitted = await api.getChannels(scope);
      const data = embeddedChannelId ? permitted.filter(channel => channel.id === embeddedChannelId) : permitted;
      if (scope !== workspaceRef.current || request !== channelsRequestRef.current) return;
      channelsWorkspaceRef.current = scope;
      setChannels(data);
      if (data.some(channel => channel.secure && channel.type !== 'direct')) void getSecureMessaging().then(async secure => {
        await secure.refreshBindings();
        for (const channel of data.filter(item => item.secure && item.type !== 'direct')) {
          try {
            const title = await secure.getChannelTitle(channel.id);
            if (scope !== workspaceRef.current || request !== channelsRequestRef.current) return;
            const patch = (item: Channel) => item.id === channel.id ? {...item,name:title} : item;
            setChannels(old => old.map(patch)); setActiveChannel(old => old ? patch(old) : old);
          } catch { /* The security panel can unlock history on an untrusted device. */ }
        }
      }).catch(() => {});
      setActiveChannel((current) => data.find((channel) => channel.id === current?.id) || data.find((channel) => channel.id === initialChannelId) || data[0] || null);
    } catch (err) {
      if (scope === workspaceRef.current && request === channelsRequestRef.current) setChannelError(err instanceof Error ? err.message : 'Could not load channels. Please try again.');
    } finally {
      if (scope === workspaceRef.current && request === channelsRequestRef.current) setLoading(false);
    }
  };

  const refreshMemberNames = async (channelId: string) => {
    try {
      const members = await api.getChannelMembers(channelId);
      if (activeChannelRef.current !== channelId) return;
      setMemberNames(Object.fromEntries(members.map((member) => [member.user_id, member.name])));
    } catch { /* Message history remains usable while member metadata is unavailable. */ }
  };

  const loadMessages = async (channelId: string) => {
    const request = ++messageRequestRef.current;
    const token=api.getAccessToken();
    const initial = new Map(messagesRef.current.map((message) => [message.id, message]));
    setMessagesLoading(true); setMessageError('');
    try {
      let cursor:number|null=null;
      if(!channels.find(channel=>channel.id===channelId)?.secure){
        try{cursor=(await api.request<{cursor:number}>('/messages/channels/'+encodeURIComponent(channelId)+'/sync/head')).cursor;}
        catch(failure){if(!(failure instanceof APIError&&failure.status===409))throw failure;}
      }
      if(token!==api.getAccessToken()||activeChannelRef.current!==channelId||messageRequestRef.current!==request)return;
      const data = await api.getMessages(channelId);
      const target = pendingJumpRef.current;
      let page = data;
      const targetId = target?.channel_id === channelId ? target.parent_id || target.id : null;
      while (targetId && !data.some(item => item.id === targetId) && page.length >= 50 && activeChannelRef.current === channelId && messageRequestRef.current === request) {
        page = await api.getMessages(channelId, {before: page[page.length - 1].id});
        data.push(...page);
      }
      if (activeChannelRef.current !== channelId || messageRequestRef.current !== request) return;
      if(token!==api.getAccessToken())return;
      nativeSyncRef.current=cursor===null?null:{channelId,token,cursor};
      setEncryptionLocked(false);
      setHasOlder(page.length >= 50);
      const timeline = data.reverse().filter((message) => !message.parent_id);
      const ids = new Set(timeline.map((message) => message.id));
      setMessages((prev) => {
        // Keep events received during the fetch, while letting the snapshot
        // remove messages deleted during an outage.
        const updates = prev.filter((message) => message.channel_id === channelId && initial.get(message.id) !== message);
        const byId = new Map(updates.map((message) => [message.id, message]));
        return [...timeline.map((message) => byId.get(message.id) || message), ...updates.filter((message) => !ids.has(message.id))];
      });
      void offlineLibrary.capture(channelId, timeline).catch(() => {});
      timeline.forEach((message) => loadReactions(message.id));
      if (targetId) requestAnimationFrame(() => {
        const selected = pendingJumpRef.current;
        pendingJumpRef.current = null;
        jumpToMessage(targetId);
        if (selected?.parent_id) { const parent = timeline.find(item => item.id === selected.parent_id); if (parent) void loadThread(parent); }
      });
    } catch (err) {
      if (activeChannelRef.current === channelId && messageRequestRef.current === request) { setEncryptionLocked(err instanceof APIError && err.code === 'requires_encryption_unlock'); setMessageError(err instanceof Error ? err.message : 'Could not load messages. Please try again.'); }
    } finally { if (activeChannelRef.current === channelId && messageRequestRef.current === request) setMessagesLoading(false); }
  };

  const loadOlderMessages = async () => {
    const channelId = activeChannelRef.current;
    const oldest = messagesRef.current[0];
    if (!channelId || !oldest || loadingOlder) return;
    const request = messageRequestRef.current;
    setLoadingOlder(true); setMessageError('');
    const timeline = timelineRef.current;
    const oldHeight = timeline?.scrollHeight || 0;
    const oldTop = timeline?.scrollTop || 0;
    try {
      const data = await api.getMessages(channelId, {before:oldest.id,limit:100});
      if (activeChannelRef.current !== channelId || messageRequestRef.current !== request) return;
      setHasOlder(data.length >= 100);
      setMessages(previous => { const seen = new Set(previous.map(item => item.id)); return [...data.reverse().filter(item => !item.parent_id && !seen.has(item.id)), ...previous]; });
      requestAnimationFrame(() => { if (timeline && activeChannelRef.current === channelId) timeline.scrollTop = oldTop + timeline.scrollHeight - oldHeight; });
    } catch (error) { if (activeChannelRef.current === channelId) setMessageError(error instanceof Error ? error.message : 'Older messages could not be loaded.'); }
    finally { if (activeChannelRef.current === channelId) setLoadingOlder(false); }
  };

  const openHistoryMessage = (message: Message) => {
    const channel = channels.find(item => item.id === message.channel_id);
    if (!channel) return;
    pendingJumpRef.current = message;
    setHistoryOpen(false); setCollaborationTab(null);
    if (activeChannelRef.current === channel.id) void loadMessages(channel.id); else selectChannel(channel);
  };

  const loadReactions = async (messageId: string) => {
    const channelId = activeChannelRef.current;
    try {
      const data = await api.getReactions(messageId);
      if (activeChannelRef.current !== channelId) return;
      const normalized = data.map((reaction) => ({ ...reaction, user_reacted: reaction.users.includes(api.getCurrentUserId() || '') }));
      setReactions((prev) => ({ ...prev, [messageId]: normalized }));
    } catch (err) {
      console.error('Failed to load reactions:', err);
    }
  };

  const loadUnreadCounts = async () => {
    const counts: Record<string, number> = {};
    for (const channel of channels) {
      try {
        const data = await api.getUnreadCount(channel.id);
        counts[channel.id] = data.unread_count;
      } catch { counts[channel.id] = 0; }
    }
    setUnreadCounts(counts);
  };

  const loadThread = async (message: Message) => {
    setThreadMessage(message);
    threadMessageRef.current = message;
    setThreadReplies([]);
    try {
      const data = await api.getThread(message.id);
      if (threadMessageRef.current?.id !== message.id || activeChannelRef.current !== message.channel_id) return;
      const ids = new Set(data.replies.map((reply) => reply.id));
      setThreadReplies((prev) => [...data.replies, ...prev.filter((reply) => reply.parent_id === message.id && !ids.has(reply.id))]);
    } catch (err) {
      console.error('Failed to load thread:', err);
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newMessage.trim() || !activeChannel || sending) return;
    const channelId = activeChannel.id;
    setSending(true); setActionError('');
    try {
      const content = newMessage;
      const token = api.getAccessToken();
      const result = await mobileOutbox.sendOrQueue(channelId, content, selectedMentions.filter(id => content.includes('@' + memberNames[id])), !!activeChannel.secure);
      if (token !== api.getAccessToken()) return;
      if (draftsRef.current[channelId] === content) persistDraft(channelId, '', !!activeChannel.secure);
      setMobileNotice(result.kind === 'queued' ? 'Saved in your encrypted outbox. It will sync when this sign-in reconnects.' : '');
      if (activeChannelRef.current === channelId) {
        if (result.kind === 'sent') setMessages((prev) => prev.some((message) => message.id === result.message.id) ? prev : [...prev, result.message]);
        setNewMessage((current) => current === content ? '' : current);
        setSelectedMentions([]); setMentionOpen(false);
      }
      stopTyping(); // Stop typing indicator when message is sent
    } catch (err) {
      if (activeChannelRef.current === channelId) setActionError(`Message was not sent. ${err instanceof Error ? err.message + '. ' : ''}Your draft is saved; please try again.`);
    } finally { setSending(false); }
  };

  const handleAddReaction = async (messageId: string, emoji: string) => {
    try {
      await api.addReaction(messageId, emoji);
      loadReactions(messageId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not add reaction.');
    }
    setShowReactionPicker(null);
  };

  const handleRemoveReaction = async (messageId: string, emoji: string) => {
    try {
      await api.removeReaction(messageId, emoji);
      loadReactions(messageId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not remove reaction.');
    }
  };

  const handleEditMessage = async (messageId: string) => {
    if (!editContent.trim()) return;
    try {
      await api.editMessage(messageId, editContent);
      setEditingMessage(null);
      setEditContent('');
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not edit message.');
    }
  };

  const handleDeleteMessage = async (messageId: string) => {
    if (!confirm(t("Delete this message?"))) return;
    try {
      await api.deleteMessage(messageId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not delete message.');
    }
  };

  const handleThreadReply = async () => {
    if (!threadReplyContent.trim() || !threadMessage) return;
    try {
      const parentId = threadMessage.id;
      const content = threadReplyContent;
      const reply = await api.replyToMessage(parentId, content);
      if (threadMessageRef.current?.id === parentId) {
        setThreadReplies((prev) => prev.some((message) => message.id === reply.id) ? prev : [...prev, reply]);
        setThreadReplyContent((current) => current === content ? '' : current);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Reply was not sent. Please try again.');
    }
  };

  // Typing emitter hook
  const { startTyping, stopTyping } = useTypingEmitter(activeChannel?.id || '');

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setNewMessage(value);
    if (activeChannel) persistDraft(activeChannel.id, value, !!activeChannel.secure);
    setMentionOpen(/(?:^|\s)@[^@\n]*$/.test(value));
    
    // Emit typing event
    if (value.trim()) {
      startTyping();
    } else {
      stopTyping();
    }
    
  };

  const handleCreateChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newChannelName.trim() || creating) return;
    setCreating(true); setCreateError('');
    try {
      const title = newChannelName;
      const channel = await api.createChannel(secureAvailable && createSecure ? 'Encrypted channel' : title, 'group', [], workspace?.id);
      if (secureAvailable && createSecure) {
        const secure = await getSecureMessaging();
        const binding = await secure.enableChannel(channel.id, title);
        channel.secure = true; channel.matrix_room_id = binding.room_id || undefined; channel.name = title;
      }
      setChannels((prev) => [...prev, channel]);
      setActiveChannel(channel);
      setNewChannelName('');
      setShowNewChannel(false);
      if (window.matchMedia('(max-width: 767px)').matches) setSidebarOpen(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Could not create channel. Please try again.');
    } finally { setCreating(false); }
  };

  const selectChannel = (channel: Channel) => {
    setActiveChannel(channel);
    if (window.matchMedia('(max-width: 767px)').matches) setSidebarOpen(false);
  };

  const jumpToMessage = (messageId: string) => {
    requestAnimationFrame(() => {
      const timeline = timelineRef.current;
      const message = document.getElementById(`message-${messageId}`);
      if (!timeline || !message) return;
      nearBottomRef.current = false;
      followingLatestRef.current = false;
      clearTimeout(followTimeoutRef.current);
      const top = timeline.scrollTop + message.getBoundingClientRect().top - timeline.getBoundingClientRect().top - Math.max(0, (timeline.clientHeight - message.clientHeight) / 2);
      timeline.scrollTo({ top, behavior: scrollBehavior() });
      message.focus({ preventScroll: true });
      setHighlightedMessage(messageId);
      clearTimeout(highlightTimeoutRef.current);
      highlightTimeoutRef.current = setTimeout(() => setHighlightedMessage(null), 2600);
    });
  };

  const runCommand = (action: CommandAction) => {
    if (window.matchMedia('(max-width: 767px)').matches) setSidebarOpen(false);
    if (action === 'appearance') { openAppearance(); return; }
    if (action === 'create') { setCreateError(''); setShowNewChannel(true); return; }
    if (activeChannel && (action === 'members' || action === 'files')) {
      setThreadMessage(null);
      setToolPanel(action);
    }
  };

  const handleComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOpen && event.key === 'ArrowDown') {
      const option = document.querySelector<HTMLButtonElement>('.fc-chat-mention-picker button');
      if (option) { event.preventDefault(); option.focus(); return; }
    }
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229 || composingRef.current) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const handleLogout = async () => {
    const accountToken = api.getAccessToken();
    setLoggingOut(true);
    let warning: string | undefined;
    try { await api.logoutSession(); }
    catch { warning = 'Signed out on this device. The server could not be reached to revoke the session.'; }
    finally { if (!api.getAccessToken() || api.getAccessToken() === accountToken) { disconnectSocket(); onLogout(warning); } }
  };
  const currentUserId = api.getCurrentUserId();
  const avatarInitial = (userId: string) => (memberNames[userId] || (userId === currentUserId ? t('You') : t('Member'))).trim().slice(0, 1).toUpperCase();

  const authorName = (userId: string) => userId === currentUserId ? t('You') : memberNames[userId] || t('Member {id}', { id: userId.slice(0, 6) });
  const dateKey = (date: string) => new Date(date).toDateString();
  const dateLabel = (date: string) => {
    const value = new Date(date);
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);
    if (value.toDateString() === today.toDateString()) return t('Today');
    if (value.toDateString() === yesterday.toDateString()) return t('Yesterday');
    return value.toLocaleDateString(currentLocale(), { weekday: 'long', month: 'long', day: 'numeric', ...(value.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) });
  };
  const timeLabel = (date: string) => new Date(date).toLocaleTimeString(currentLocale(), { hour: '2-digit', minute: '2-digit' });

  if (loading) {
    return (
      <div key="chat-loading" className="fc-chat-loading" role="status" aria-label={t("Loading your workspace")}>
        <span className="fc-chat-loader" />
        <span>{t("Opening your workspace…")}</span>
      </div>
    );
  }

  return (
    <div key="chat-shell" className="fc-chat">
      {sidebarOpen && <button className="fc-chat-scrim" aria-label={t("Close channels")} onClick={() => setSidebarOpen(false)} />}
      <aside className={`fc-chat-sidebar ${sidebarOpen ? 'is-open' : ''}`} aria-label={t("Workspace channels")} aria-hidden={!sidebarOpen} ref={sidebarRef}>
        <div className="fc-chat-sidebar-inner">
          <div className="fc-chat-identity">
            <Brand size="medium" className="fc-chat-wordmark" />
            <span className="fc-kicker">{t("Your shared workspace")}</span><WorkspaceSwitcher />
          </div>
          <button type="button" onClick={() => setCommandOpen(true)} className="fc-chat-switcher" aria-label={t("Quick switcher")} aria-haspopup="dialog"><Search size={16} /><span>{t("Search & switch")}</span><kbd className="fc-shortcut">{/Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : t("Ctrl")} {t(" K")}</kbd></button>

          <button type="button" onClick={() => setHistoryOpen(true)} className="fc-chat-text-button fc-chat-history-button"><Search size={15} />{t("Search all history")}</button>
          {!embeddedChannelId && <button type="button" onClick={() => { setMyDayOpen(true); if (window.matchMedia('(max-width: 767px)').matches) setSidebarOpen(false); }} className="fc-chat-text-button fc-chat-history-button"><ClipboardCheck size={15} />{t('My day')}</button>}
          <button type="button" onClick={openDirectMessages} className="fc-chat-text-button fc-chat-history-button"><MessageCircle size={15} />{t("Direct messages")}</button>
          <button type="button" onClick={() => { setIntegrationEntry(null); setIntegrationsOpen(true); }} className="fc-chat-text-button fc-chat-history-button" aria-label={t("Integrations")}><Plug size={15} />{t("Integrations")}</button>
          {workspace && nativeSession && ['owner', 'admin'].includes(workspace.role) && <button type="button" onClick={() => setLaunchOpen(true)} className="fc-chat-text-button fc-chat-history-button" aria-label={t('Get started')}><Compass size={15} />{t('Get started')}</button>}
          {workspace && nativeSession && ['owner', 'admin'].includes(workspace.role) && shouldSuggestWorkspaceLaunch(workspace, channels) && <div className="fc-chat-launch-card"><span className="fc-kicker">{t('A good place to begin')}</span><p>{t('Your team, your first channel, your own connections.')}</p><button type="button" className="fc-account-link" onClick={() => setLaunchOpen(true)}>{t('Set up this workspace')}<ArrowUpRight size={14} /></button></div>}
          <nav className="fc-chat-channel-nav" aria-label={t("Channels")}>
            <div className="fc-chat-section-label">
              <span className="fc-kicker">{t("Channels")}</span>
              <button type="button" onClick={() => { setCreateError(''); setShowNewChannel(true); }} aria-label={t("Create channel")} className="fc-icon-button"><Plus size={17} /></button>
            </div>
            <div className="fc-chat-channel-list">
              {channels.map((channel) => (
                <button key={channel.id}
                  onClick={() => selectChannel(channel)}
                  aria-current={activeChannel?.id === channel.id ? 'page' : undefined}
                  className={`fc-chat-channel ${activeChannel?.id === channel.id ? 'is-active' : ''} ${activeChannel?.id !== channel.id && unreadCounts[channel.id] > 0 ? 'has-unread' : ''}`}>
                  {['direct','group_direct'].includes(channel.type) ? <MessageCircle size={17} aria-hidden="true" /> : <Hash size={17} aria-hidden="true" />}
                  <span>{channel.name || t("Unnamed")}</span>
                  {activeChannel?.id !== channel.id && unreadCounts[channel.id] > 0 && <span className="fc-chat-unread" aria-label={t("{value0} unread messages", { value0: String(unreadCounts[channel.id]) })}>{unreadCounts[channel.id]}</span>}
                  {<Lock className="fc-chat-channel-lock" size={12} aria-label={channel.secure ? t("End-to-end encrypted") : t("Server-encrypted messages")} />}
                </button>
              ))}
            </div>
            {channels.length === 0 && <p className="fc-chat-nav-empty">{t("No channels yet")}</p>}
            {(channelError || workspacesError) && <div role="alert" className="fc-alert">{channelError || workspacesError}<button type="button" className="fc-chat-text-button" onClick={() => void loadChannels()}>{t("Retry channels")}</button></div>}
          </nav>

          <div className="fc-chat-sidebar-note"><span className="fc-chat-note-rule" /><p>{t("A little less noise.")}<br />{t("A little more together.")}</p></div>
          <footer className="fc-chat-account">
            <div className="fc-chat-account-person">
              <div className="fc-chat-avatar fc-chat-avatar-self">{currentUserId ? avatarInitial(currentUserId) : t("Y")}</div>
              <div><strong>{currentUserId && memberNames[currentUserId] || t("Your account")}</strong><span>{t("Personal workspace settings")}</span></div>
              <button onClick={() => void handleLogout()} disabled={loggingOut} className="fc-icon-button" title={t("Sign out")} aria-label={t("Sign out")}><LogOut size={17} /></button>
            </div>
            <div className="fc-chat-account-actions"><button type="button" onClick={openAccount} aria-label={t("Account settings")} className="fc-icon-button" title={t("Account settings")}><Users size={17} /></button>
              <button type="button" onClick={openAppearance} aria-label={t("Appearance settings")} className="fc-chat-appearance"><Palette size={16} /><span>{t("Appearance")}</span><ArrowUpRight size={14} /></button>
              {onAdmin && <button type="button" onClick={onAdmin} className="fc-icon-button" title={t("Admin")} aria-label={t("Administration")}><Settings size={17} /></button>}
            </div>
            {workspace && <button type="button" className="fc-chat-text-button fc-chat-feedback" onClick={() => setFeedbackOpen(true)}><ClipboardCheck size={14} />{t('Workspace feedback')}</button>}
          </footer>
        </div>
      </aside>

      <main className="fc-chat-main">
        <header className="fc-chat-topbar">
          <div className="fc-chat-channel-heading">
            <button type="button" onClick={() => setSidebarOpen(!sidebarOpen)} aria-label={t("Toggle channels")} aria-expanded={sidebarOpen} className="fc-icon-button fc-chat-sidebar-toggle">{sidebarOpen ? <X size={20} /> : <Menu size={20} />}</button>
            <div className="fc-chat-topbar-channel">{activeChannel ? <><Hash size={18} aria-hidden="true" /><strong>{activeChannel.name || t("Unnamed Channel")}</strong></> : <strong>{t("Your workspace")}</strong>}</div>
            {activeChannel && <button type="button" className="fc-chat-security" onClick={() => openSecureSettings(activeChannel.secure ? activeChannel.id : undefined)}><Lock size={12} />{activeChannel.secure ? t("End-to-end encrypted") : t("Server-encrypted messages")}</button>}
          </div>
          <div className="fc-chat-topbar-actions">
            <MobileStatus onOpen={() => setMobileOpen(true)} />
            <button type="button" className="fc-chat-topbar-action fc-chat-secondary-action" aria-label={t("Mobile and offline settings")} onClick={() => setMobileOpen(true)}><Smartphone size={17} /><span>{t("Mobile")}</span></button>
            {activeChannel && <>
              <button type="button" aria-label={t("Open workspace tools")} className="fc-chat-topbar-action" onClick={() => { setWorkHubInitialTab('tasks'); setWorkHubOpen(true); setToolPanel(null); setThreadMessage(null); }}><Layers size={17} /><span>{t("Work")}</span></button>
              <button type="button" aria-label={t("Saved items and decisions")} className="fc-chat-topbar-action fc-chat-secondary-action" onClick={() => { setCollaborationTab("pins"); setDecisionSource(undefined); setToolPanel(null); setThreadMessage(null); }}><Check size={17} /><span>{t("Saved")}</span></button>
              <button type="button" aria-label={t("Channel members")} aria-pressed={toolPanel === 'members'} onClick={() => { setThreadMessage(null); setToolPanel((current) => current === 'members' ? null : 'members'); }} className="fc-chat-topbar-action fc-chat-secondary-action"><Users size={17} /><span>{t("Members")}</span></button>
              <button type="button" aria-label={t("Channel files")} aria-pressed={toolPanel === 'files'} onClick={() => { setThreadMessage(null); setToolPanel((current) => current === 'files' ? null : 'files'); }} className="fc-chat-topbar-action fc-chat-secondary-action"><Paperclip size={17} /><span>{t("Files")}</span></button>
            </>}
            <div ref={mobileToolsRef} className="fc-chat-mobile-tools" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) closeMobileTools(); }} onKeyDown={event => {
              if (event.key === 'Escape' && mobileToolsOpen) { event.preventDefault(); event.stopPropagation(); closeMobileTools(true); }
            }}>
              <button ref={mobileToolsTrigger} type="button" className="fc-chat-topbar-action" aria-label={t('More channel tools')} title={t('More channel tools')} aria-expanded={mobileToolsOpen} aria-controls="mobile-channel-tools" onClick={() => setMobileToolsOpen(open => !open)} onKeyDown={event => {
                if (event.key === 'ArrowDown') { event.preventDefault(); setMobileToolsOpen(true); requestAnimationFrame(() => mobileToolsRef.current?.querySelector<HTMLButtonElement>('.fc-chat-mobile-tool')?.focus()); }
              }}><MoreHorizontal size={20} aria-hidden="true" /></button>
              {mobileToolsOpen && <div id="mobile-channel-tools" className="fc-chat-mobile-tools-popover" role="group" aria-label={t('More channel tools')}>
                <MobileStatus onOpen={() => openMobileTool(() => setMobileOpen(true))} />
                <button type="button" className="fc-chat-mobile-tool" onClick={() => openMobileTool(() => setMobileOpen(true))}><Smartphone size={17} /><span>{t('Mobile and offline settings')}</span></button>
                {!embeddedChannelId && <button type="button" className="fc-chat-mobile-tool" onClick={() => openMobileTool(() => setMyDayOpen(true))}><ClipboardCheck size={17} /><span>{t('My day')}</span></button>}
                {activeChannel && <>
                  <button type="button" className="fc-chat-mobile-tool" onClick={() => openMobileTool(() => { setCollaborationTab('pins'); setDecisionSource(undefined); setToolPanel(null); setThreadMessage(null); })}><Check size={17} /><span>{t('Saved items and decisions')}</span></button>
                  <button type="button" className="fc-chat-mobile-tool" onClick={() => openMobileTool(() => { setThreadMessage(null); setToolPanel(current => current === 'members' ? null : 'members'); })}><Users size={17} /><span>{t('Channel members')}</span></button>
                  <button type="button" className="fc-chat-mobile-tool" onClick={() => openMobileTool(() => { setThreadMessage(null); setToolPanel(current => current === 'files' ? null : 'files'); })}><Paperclip size={17} /><span>{t('Channel files')}</span></button>
                </>}
              </div>}
            </div>
            <div className="fc-chat-notifications"><NotificationBell onClick={() => setShowNotifications(!showNotifications)} />{showNotifications && <div className="fc-chat-notification-popover"><NotificationCenter onOpenMessage={openHistoryMessage} onClose={() => setShowNotifications(false)} /></div>}</div>
          </div>
        </header>

        <div className="fc-chat-connection" role="status" data-connected={connection === 'Connected'}><span className="fc-chat-connection-dot" /><span>{connection === 'Connected' ? t("Live connection") : connection}</span>{activeChannel && <button type="button" onClick={() => void loadMessages(activeChannel.id)}><RefreshCw size={11} /><span>{t("Refresh messages")}</span></button>}</div>

        <div className="fc-chat-timeline-wrap"><div ref={timelineRef} onScroll={handleTimelineScroll} onWheel={() => { followingLatestRef.current = false; }} onTouchStart={() => { followingLatestRef.current = false; }} onPointerDown={() => { followingLatestRef.current = false; }} className="fc-chat-timeline" aria-label={t("Channel messages")}>
          <div className="fc-chat-timeline-inner">
            {activeChannel && <div className={`fc-chat-intro ${messages.length > 0 ? 'has-messages' : ''}`}><span className="fc-kicker">{t("A space for conversation")}</span><h1>{activeChannel.name || t("Your channel")}<span>.</span></h1><p>{t("Ideas, updates, and everything in between.")}</p></div>}
            {messageError && <div role="alert" className="fc-alert">{messageError}{encryptionLocked ? <button type="button" className="fc-chat-text-button" onClick={() => activeChannel && openSecureSettings(activeChannel.id)}>{t("Unlock encrypted messaging")}</button> : <button type="button" className="fc-chat-text-button" onClick={() => activeChannel && void loadMessages(activeChannel.id)}>{t("Retry messages")}</button>}</div>}
            {messagesLoading && messages.length === 0 && <div className="fc-chat-history-loading" role="status"><span className="fc-chat-loader" /><span>{t("Loading your conversation…")}</span></div>}
            {hasOlder && <button type="button" className="fc-button fc-chat-load-older" disabled={loadingOlder} onClick={() => void loadOlderMessages()}>{loadingOlder ? t("Loading older messages…") : t("Load older messages")}</button>}
            {activeChannel ? messages.length > 0 ? <div className="fc-chat-message-list">
              {messages.map((msg, index) => (
                <Fragment key={msg.id}>
                  {(index === 0 || dateKey(messages[index - 1].created_at) !== dateKey(msg.created_at)) && <div className="fc-chat-date"><span>{dateLabel(msg.created_at)}</span></div>}
                  <article id={`message-${msg.id}`} tabIndex={-1} className={`fc-chat-message ${highlightedMessage === msg.id ? 'is-highlighted' : ''}`} aria-label={t("Message from {value0}", { value0: String(authorName(msg.author_id)) })}>
                    <div className="fc-chat-message-avatar"><div className={`fc-chat-avatar ${msg.author_id === currentUserId ? 'fc-chat-avatar-self' : ''}`}>{avatarInitial(msg.author_id)}</div><div className="fc-chat-presence"><PresenceIndicator userId={msg.author_id} size="sm" /></div></div>
                    <div className="fc-chat-message-body">
                      {editingMessage === msg.id ? <div className="fc-chat-edit"><textarea value={editContent} onChange={(e) => setEditContent(e.target.value)} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); setEditingMessage(null); } else if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void handleEditMessage(msg.id); } }} aria-label={t("Edit message")} maxLength={10000} rows={2} className="fc-input" autoFocus /><button type="button" onClick={() => handleEditMessage(msg.id)} className="fc-icon-button" aria-label={t("Save edit")}><Check size={17} /></button><button type="button" onClick={() => setEditingMessage(null)} className="fc-icon-button" aria-label={t("Cancel edit")}><X size={17} /></button></div> : <>
                        <div className="fc-chat-message-meta"><strong>{authorName(msg.author_id)}</strong><time dateTime={msg.created_at} title={new Date(msg.created_at).toLocaleString(currentLocale())}>{timeLabel(msg.created_at)}</time>{msg.edited_at && <span>{t("edited")}</span>}</div>
                        <p className={`fc-chat-message-content ${msg.deleted ? 'is-deleted' : ''}`}>{msg.content}</p>
                        {!msg.deleted && !msg.locked && msg.content.startsWith('[Imported from Slack · ') && <HistoricalSlackDetails key={`${activeChannel.id}:${msg.id}`} channelId={activeChannel.id} messageId={msg.id} secure={!!activeChannel.secure} />}
                        {reactions[msg.id]?.length > 0 && <div className="fc-chat-reactions">{reactions[msg.id].map((reaction) => <button type="button" key={reaction.emoji} aria-pressed={reaction.user_reacted} aria-label={t("{value0}, {value1} {value2}", { value0: String(reaction.emoji), value1: String(reaction.count), value2: String(reaction.count === 1 ? 'reaction' : 'reactions') })} onClick={() => reaction.user_reacted ? handleRemoveReaction(msg.id, reaction.emoji) : handleAddReaction(msg.id, reaction.emoji)}><span>{reaction.emoji}</span><span>{reaction.count}</span></button>)}</div>}
                        {(msg.thread_count || 0) > 0 && <button type="button" onClick={() => { setToolPanel(null); void loadThread(msg); }} className="fc-chat-thread-link"><MessageCircle size={14} />{msg.thread_count} {msg.thread_count === 1 ? t("reply") : t("replies")}<ArrowUpRight size={12} /></button>}
                        {!msg.deleted && !msg.locked && <div className="fc-chat-message-actions"><MessageSaveActions channel={activeChannel} message={msg} onDecision={(message) => { setDecisionSource(message); setCollaborationTab("decisions"); setToolPanel(null); }} /><button type="button" className="fc-icon-button" aria-label={t("Remind me about this message")} onClick={() => setProductivity({message:msg})}><Bell size={15}/></button><button type="button" onClick={() => setShowReactionPicker(showReactionPicker === msg.id ? null : msg.id)} className="fc-icon-button" title={t("Add reaction")} aria-label={t("Add reaction")}><Smile size={16} /></button><button type="button" onClick={() => { setToolPanel(null); void loadThread(msg); }} className="fc-icon-button" title={t("Reply in thread")} aria-label={t("Reply in thread")}><CornerDownRight size={16} /></button>{msg.author_id === currentUserId && <><button type="button" onClick={() => { setEditingMessage(msg.id); setEditContent(msg.content); }} className="fc-icon-button" title={t("Edit")} aria-label={t("Edit")}><Edit3 size={15} /></button><button type="button" onClick={() => handleDeleteMessage(msg.id)} className="fc-icon-button fc-chat-delete" title={t("Delete")} aria-label={t("Delete")}><Trash2 size={15} /></button></>}</div>}
                        {showReactionPicker === msg.id && <div className="fc-chat-reaction-picker" aria-label={t("Choose a reaction")}>{REACTIONS.map((emoji) => <button type="button" key={emoji} onClick={() => handleAddReaction(msg.id, emoji)} aria-label={t("React {value0}", { value0: String(emoji) })}>{emoji}</button>)}</div>}
                      </>}
                    </div>
                  </article>
                </Fragment>
              ))}
            </div> : !messagesLoading && !messageError && <div className="fc-chat-empty"><div className="fc-chat-empty-mark"><MessageCircle size={25} strokeWidth={1.3} /></div><h2>{t("No messages yet")}</h2><p>{t("Start the conversation.")}<br />{t("Good things begin with a hello.")}</p><button type="button" className="fc-chat-text-button" onClick={() => { setToolPanel('members'); setThreadMessage(null); }}>{t("Invite a teammate ")}<ArrowUpRight size={14} /></button></div> : <div className="fc-chat-empty fc-chat-empty-workspace"><span className="fc-kicker">{t("Make room for good work")}</span><h1>{t("Select a channel")}</h1><p>{t("Or create a new one to get started.")}</p><button type="button" className="fc-button fc-button-primary" onClick={() => { if (channels.length) setSidebarOpen(true); else { setCreateError(''); setShowNewChannel(true); } }}><Plus size={16} />{channels.length ? t("Browse channels") : t("Create your first channel")}</button></div>}
          </div>
        </div>{pendingMessages > 0 && <div className="fc-chat-latest-wrap" role="status"><button type="button" className="fc-chat-latest" aria-label={t("Jump to latest messages")} onClick={() => scrollToLatest()}><ArrowDown size={15} /><span>{pendingMessages} {t(" new ")}{pendingMessages === 1 ? t("message") : t("messages")}</span><span>{t("Jump to latest")}</span></button></div>}</div>

        {activeChannel && <div className="fc-chat-composer-area">
          {currentUserId && <div className="fc-chat-typing"><TypingIndicator channelId={activeChannel.id} currentUserId={currentUserId} /></div>}
          {actionError && <p role="alert" className="fc-alert">{actionError}</p>}
          {policyNotice && <p role="status" className="fc-alert">{t('Shared content matched an organization policy. Review it before sharing further.')}<button type="button" className="fc-icon-button" aria-label={t('Dismiss policy notice')} onClick={()=>setPolicyNotice(false)}><X size={14}/></button></p>}
          {mobileNotice && <p role="status" className="fc-chat-muted">{mobileNotice}</p>}
          {mentionOpen && <div className="fc-chat-mention-picker" role="listbox" aria-label={t("Mention a teammate")}>{Object.entries(memberNames).filter(([id,name]) => id !== currentUserId && name.toLocaleLowerCase().includes((/(?:^|\s)@([^@\n]*)$/.exec(newMessage)?.[1] || "").toLocaleLowerCase())).slice(0,8).map(([id,name]) => <button type="button" role="option" aria-selected={selectedMentions.includes(id)} key={id} onClick={() => { const value = newMessage.replace(/(?:^|\s)@[^@\n]*$/, match => (match.startsWith(" ") ? " " : "") + "@" + name + " "); setNewMessage(value); persistDraft(activeChannel.id, value, !!activeChannel.secure); setSelectedMentions(old => [...new Set([...old,id])]); setMentionOpen(false); inputRef.current?.focus(); }}>{name}</button>)}</div>}
          <form onSubmit={handleSendMessage} className="fc-chat-composer"><textarea ref={inputRef} value={newMessage} onChange={handleInputChange} onKeyDown={handleComposerKeyDown} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} rows={1} data-modal-return-focus maxLength={10000} aria-label={t("Message #{value0}", { value0: String(activeChannel.name || 'channel') })} aria-describedby="message-composer-hint" placeholder={t("Message #{value0}", { value0: String(activeChannel.name || 'channel') })} /><button type="submit" aria-label={t("Send message")} disabled={!newMessage.trim() || sending} className="fc-button fc-button-primary"><span>{sending ? t("Sending…") : t("Send")}</span><Send size={15} /></button></form>
          <button type="button" className="fc-chat-text-button" disabled={sending} onClick={() => setProductivity({draft:newMessage,mentions:selectedMentions})}><Clock size={14}/>{t("Schedule & reminders")}</button><div id="message-composer-hint" className="fc-chat-composer-hint"><span>{t("Enter to send ")}<span aria-hidden="true">·</span> {t(" Shift + Enter for a new line")}</span><span>{newMessage.length >= 9000 ? `${newMessage.length.toLocaleString(currentLocale())} / 10,000` : t("A thought worth sharing?")}</span></div>
        </div>}
      </main>

      {activeChannel && toolPanel && <ChannelTools key={`${activeChannel.id}:${toolPanel}`} channel={activeChannel} tab={toolPanel} onClose={() => setToolPanel(null)} onRoleChange={(role) => { if (activeChannelRef.current === activeChannel.id) setActiveChannel((current) => current ? { ...current, role } : current); }} onLeave={() => { setChannels((records) => records.filter((record) => record.id !== activeChannel.id)); if (activeChannelRef.current === activeChannel.id) { setActiveChannel(null); setToolPanel(null); } void loadChannels(); }} />}

      <MeetingDock />
      {activeChannel && productivity && <ChatProductivityPanel key={activeChannel.id} channel={activeChannel} message={productivity.message} draft={productivity.draft} mentions={productivity.mentions} onClose={()=>setProductivity(null)} onScheduled={()=>{if(draftsRef.current[activeChannel.id]===productivity.draft){setNewMessage('');persistDraft(activeChannel.id,'',!!activeChannel.secure);setSelectedMentions([]);}setProductivity(null);}}/>}

      {activeChannel && collaborationTab && <CollaborationPanel key={`${activeChannel.id}:${collaborationTab}`} channel={activeChannel} tab={collaborationTab} focusDecisionId={focusDecisionId} sourceMessage={decisionSource} onClose={() => {setCollaborationTab(null); setDecisionSource(undefined);}} onOpenMessage={(channelId,messageId) => { const channel = channels.find(item => item.id === channelId); if (!channel) return; openHistoryMessage({id:messageId,channel_id:channelId,author_id:'',content:'',encrypted:!!channel.secure,created_at:''}); }} />}

      {threadMessage && <aside ref={threadDialogRef} tabIndex={-1} className="fc-chat-thread" role="dialog" aria-modal="true" aria-label={t("Thread")}>
        <header className="fc-chat-thread-header"><div><span className="fc-kicker">{t("Keep the conversation close")}</span><h2>{t("Thread")}</h2><ThreadWatchButton channelId={threadMessage.channel_id} messageId={threadMessage.id} /></div><button type="button" onClick={() => setThreadMessage(null)} className="fc-icon-button" aria-label={t("Close thread")} data-autofocus><X size={19} /></button></header>
        <div className="fc-chat-thread-parent"><div className="fc-chat-avatar">{avatarInitial(threadMessage.author_id)}</div><div><strong>{authorName(threadMessage.author_id)}</strong><p>{threadMessage.content}</p><time dateTime={threadMessage.created_at}>{new Date(threadMessage.created_at).toLocaleString(currentLocale())}</time></div></div>
        {!threadMessage.deleted && !threadMessage.locked && threadMessage.content.startsWith('[Imported from Slack · ') && <HistoricalSlackDetails key={`${threadMessage.channel_id}:${threadMessage.id}`} channelId={threadMessage.channel_id} messageId={threadMessage.id} secure={!!activeChannel?.secure} />}
        <div className="fc-chat-thread-replies">{threadReplies.length === 0 && <p className="fc-chat-muted">{t("No replies yet. Add your perspective below.")}</p>}{threadReplies.map((reply) => <div key={reply.id} className="fc-chat-thread-reply"><div className="fc-chat-avatar">{avatarInitial(reply.author_id)}</div><div><div className="fc-chat-message-meta"><strong>{authorName(reply.author_id)}</strong><time dateTime={reply.created_at}>{timeLabel(reply.created_at)}</time></div><p>{reply.content}</p>{!reply.deleted && !reply.locked && reply.content.startsWith('[Imported from Slack · ') && <HistoricalSlackDetails channelId={reply.channel_id} messageId={reply.id} secure={!!activeChannel?.secure} />}</div></div>)}</div>
        <form className="fc-chat-thread-composer" onSubmit={(event) => { event.preventDefault(); void handleThreadReply(); }}><input type="text" value={threadReplyContent} onChange={(event) => setThreadReplyContent(event.target.value)} maxLength={10000} aria-label={t("Reply in thread")} placeholder={t("Reply...")} className="fc-input" /><button type="submit" disabled={!threadReplyContent.trim()} aria-label={t("Send reply")} className="fc-icon-button fc-button-primary"><Send size={17} /></button></form>
      </aside>}

      {historyOpen && <HistorySearchPanel channels={channels} onClose={() => setHistoryOpen(false)} onMessage={openHistoryMessage} />}
      {myDayOpen && !embeddedChannelId && <MyDay onClose={() => setMyDayOpen(false)} />}
      {launchOpen && workspace && nativeSession && ['owner', 'admin'].includes(workspace.role) && <WorkspaceLaunchPanel key={`${workspace.id}:${api.getCurrentUserId()}`} workspace={workspace} channels={channels} onClose={() => setLaunchOpen(false)} onChanged={() => refreshWorkspaces(workspace.id)} onChannelsChanged={async created => { if (created && created.workspace_id === workspaceRef.current) setChannels(records => records.some(row => row.id === created.id) ? records : [...records, created]); await loadChannels(); }} onOpenAppearance={openAppearance} onOpenConnections={channelId => { setIntegrationEntry(previous => ({ tab: 'connections', channelId, revision: (previous?.revision || 0) + 1 })); setIntegrationsOpen(true); }} onSelectChannel={channel => { if (channel.workspace_id === workspaceRef.current) { selectChannel(channel); setLaunchOpen(false); requestAnimationFrame(() => inputRef.current?.focus()); } }} />}
      {feedbackOpen && workspace && <WorkspaceFeedbackDialog key={`${workspace.id}:${api.getCurrentUserId()}`} workspaceId={workspace.id} onClose={() => setFeedbackOpen(false)} onOpenLaunch={nativeSession && ['owner', 'admin'].includes(workspace.role) ? () => setLaunchOpen(true) : undefined} onOpenConnections={nativeSession && ['owner', 'admin'].includes(workspace.role) ? () => { setIntegrationEntry(previous => ({ tab: 'connections', revision: (previous?.revision || 0) + 1 })); setIntegrationsOpen(true); } : undefined} />}
      {integrationsOpen && workspace && <IntegrationsPanel key={`${workspace.id}:${integrationEntry?.revision || 0}`} workspaceId={workspace.id} channelId={integrationEntry?.channelId || activeChannel?.id} channels={channels} onClose={() => { setIntegrationsOpen(false); window.dispatchEvent(new Event('freecollab:channels-updated')); window.dispatchEvent(new Event('freecollab:launch-refresh')); }} initialTab={integrationEntry?.tab || (initialSyncReturn?.workspaceId === workspace.id ? 'sync' : undefined)} initialConnectionId={initialSyncReturn?.workspaceId === workspace.id ? initialSyncReturn.connectionId : undefined} />}
      {workHubOpen && workspace && activeChannel && <WorkHub key={`${workspace.id}:${activeChannel.id}:${workHubInitialTab}:${workHubTask?.taskId || ""}`} workspaceId={workspace.id} channel={activeChannel} initialTab={workHubInitialTab} initialProjectId={workHubTask?.projectId} focusTaskId={workHubTask?.taskId} integrationAdmin={workspace.role === "owner" || workspace.role === "admin"} dataAdmin={workspace.role === "owner"} onClose={() => setWorkHubOpen(false)} />}
      {mobileOpen && <div className="fc-overlay"><div ref={mobileDialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("Mobile and offline")} className="fc-dialog" style={{maxWidth:720,maxHeight:"90dvh",overflowY:"auto"}}><button type="button" className="fc-icon-button" aria-label={t("Close mobile settings")} onClick={() => setMobileOpen(false)}><X size={20} /></button><MobileSettings channel={activeChannel || undefined} /></div></div>}
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} channels={channels} activeChannelId={activeChannel?.id || null} messages={messages.filter((message) => message.channel_id === activeChannel?.id && !message.deleted)} hasActiveChannel={!!activeChannel} onChannel={(id) => { const channel = channels.find((item) => item.id === id); if (channel) { selectChannel(channel); requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true })); } }} onMessage={jumpToMessage} onAction={runCommand} />

      {showNewChannel && <div className="fc-overlay"><div ref={createDialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("Create channel")} className="fc-dialog fc-chat-create-dialog"><span className="fc-kicker">{t("Bring people together")}</span><h2>{t("Create Channel")}</h2><p>{t("A shared place for a project, a team, or an idea.")}</p><form onSubmit={handleCreateChannel}><label htmlFor="channel-name">{t("Channel name")}</label><input id="channel-name" type="text" value={newChannelName} onChange={(event) => setNewChannelName(event.target.value)} placeholder={t("Channel name")} maxLength={100} required className="fc-input" data-autofocus autoFocus />{secureAvailable && <label className="fc-chat-encryption-choice"><input type="checkbox" checked={createSecure} onChange={event => setCreateSecure(event.target.checked)} />{t("End-to-end encrypted")}<span>{t("Only verified devices can read this channel. Keep a recovery key.")}</span></label>}{!secureAvailable && <p className="fc-chat-muted">{t("Messages in this installation use server-side encryption. Configure the encrypted messaging service to create end-to-end encrypted channels.")}</p>}{createError && <p role="alert" className="fc-alert">{createError}</p>}<div className="fc-chat-dialog-actions"><button type="button" onClick={() => setShowNewChannel(false)} className="fc-button">{t("Cancel")}</button><button type="submit" disabled={creating || !newChannelName.trim()} className="fc-button fc-button-primary">{creating ? t("Creating…") : t("Create")}</button></div></form></div></div>}
    </div>
  );
}
