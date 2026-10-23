import { t, useI18n } from '../i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckSquare, FileText, GitBranch, Globe, ShieldCheck, Smartphone, Video, X, Newspaper, Sparkles, Cloud } from 'lucide-react';
import type { Channel, Message } from '../lib/api';
import { registerConversationNavigationGuard, mayLeaveConversationWork } from '../lib/conversation-navigation';
import { api } from '../lib/api';
import { useModalFocus } from '../lib/useModalFocus';
import { ProjectTasksPanel } from './ProjectTasksPanel';
import { MeetingsPanel } from './MeetingsPanel';
import { SharedDocumentsPanel } from './SharedDocumentsPanel';
import { WorkflowsPanel } from './WorkflowsPanel';
import { DataControlsPanel } from './DataControlsPanel';
import { MobileSettings } from './MobileSettings';
import { ActivityFeedPanel } from './ActivityFeedPanel';
import { CopilotPanel } from './CopilotPanel';
import { CloudFilesPicker } from './CloudFilesPicker';
import { ExternalMeetingsPanel } from './ExternalMeetingsPanel';
import { ChannelSyncNotice } from './ChannelSyncNotice';
import { ChannelApps } from './ChannelApps';
import { FederatedWorkPanel } from './FederatedWorkPanel';
import type { WorkHubTab } from '../lib/work-hub-view';
import '../styles/work-hub.css';

export type { WorkHubTab } from '../lib/work-hub-view';
export interface ConversationResourceSelection { kind: 'document' | 'task' | 'decision'; id: string; projectId?: string | null; title?: string }
interface Props { presentation?: 'dialog' | 'flow'; sourceMessage?: Message | null; onRevealSource?: () => void; onResourceSaved?: (resource: ConversationResourceSelection) => void; workspaceId: string; channel: Channel; onClose: () => void; onTabChange?: (tab: WorkHubTab) => void; initialTab?: WorkHubTab; dataAdmin?: boolean; integrationAdmin?: boolean; initialProjectId?: string; focusTaskId?: string; initialDocumentId?: string; initialStackId?: string; pendingStackMessageId?: string | null; onStackMessageSaved?: () => void; onRevealMessage?: (id: string) => void }
const tabs = [
  { id: 'shared-work', label: 'Shared work', icon: Globe },
  { id: 'feed', label: 'Activity feed', icon: Newspaper },
  { id: 'copilot', label: 'Copilot', icon: Sparkles },
  { id: 'cloud', label: 'Cloud files', icon: Cloud },
  { id: 'external-meetings', label: 'Meeting services', icon: Video },
  { id: 'tasks', label: 'Tasks', icon: CheckSquare },
  { id: 'meetings', label: 'Meetings', icon: Video },
  { id: 'documents', label: 'Documents', icon: FileText },
  { id: 'workflows', label: 'Workflows', icon: GitBranch },
  { id: 'apps', label: 'Apps & automation', icon: Sparkles },
  { id: 'data', label: 'Data & retention', icon: ShieldCheck },
  { id: 'mobile', label: 'Mobile & offline', icon: Smartphone },
] as const;

export function WorkHub({ presentation = 'dialog', sourceMessage, onRevealSource, onResourceSaved, workspaceId, channel, onClose, onTabChange, initialTab = 'tasks', dataAdmin = false, integrationAdmin = false, initialProjectId, focusTaskId, initialDocumentId, initialStackId, pendingStackMessageId, onStackMessageSaved, onRevealMessage }: Props) {
  useI18n();
  const [tab, setTab] = useState<WorkHubTab>(initialTab);
  const [sharedWork, setSharedWork] = useState(false);
  const [contextChecked, setContextChecked] = useState(!channel.secure);
  const visibleTabs = tabs.filter(item => item.id !== 'shared-work' || sharedWork);
  const selectedView = useRef(onTabChange); selectedView.current = onTabChange;
  useEffect(() => {
    if (contextChecked && (tab !== 'shared-work' || sharedWork)) selectedView.current?.(tab);
  }, [tab, contextChecked, sharedWork]);
  useEffect(() => {
    const token = api.getAccessToken(), abort = new AbortController();
    let live = true;
    setSharedWork(false); setContextChecked(!channel.secure);
    if (!channel.secure) return () => { live = false; abort.abort(); };
    const timer = window.setTimeout(() => abort.abort(), 10000);
    void api.request<{protocol: string; channel_id: string; connection_id: string}>(`/connect/federation/channels/${encodeURIComponent(channel.id)}/work-context`, { signal: abort.signal }).then(context => {
      if (live && token === api.getAccessToken()) {
        const eligible = context.protocol === 'freecollab-federated-work/1' && context.channel_id === channel.id && /^[a-f0-9]{32}$/.test(context.connection_id);
        setSharedWork(eligible); if (!eligible && initialTab === 'shared-work') setTab('tasks');
      }
    }).catch(() => { if (live && token === api.getAccessToken() && initialTab === 'shared-work') setTab('tasks'); }).finally(() => {
      clearTimeout(timer); if (live && token === api.getAccessToken()) setContextChecked(true);
    });
    return () => { live = false; clearTimeout(timer); abort.abort(); };
  }, [channel.id, channel.secure, initialTab]);
  const [unrestricted, setUnrestricted] = useState(false);
  useEffect(() => { const token = api.getAccessToken(); let live = true; void api.getMe().then(me => { if (live && token === api.getAccessToken()) setUnrestricted(me.session_scope !== 'workspace' && me.account_type !== 'managed'); }).catch(() => {}); return () => { live = false; }; }, []);
  const [meetingOpened, setMeetingOpened] = useState(initialTab === 'meetings');
  const dialog = useRef<HTMLElement>(null), navigation = useRef<HTMLElement>(null);
  const documentPending = useRef(false), feedPending = useRef(false), sharedPending = useRef(false), taskPending = useRef(false);
  const [selectedResource, setSelectedResource] = useState<ConversationResourceSelection | null>(null);
  const onTaskPending = useCallback((pending: boolean) => { taskPending.current = pending; }, []);
  const onSelectedDocument = useCallback((row: import('../lib/shared-documents-api').SharedDocumentRecord | null) => { setSelectedResource(row ? { kind: 'document', id: row.id, title: row.title } : null); }, []);
  const onSelectedTask = useCallback((row: import('../lib/project-tasks-api').TaskRecord | null) => { setSelectedResource(row ? { kind: 'task', id: row.id, projectId: row.project_id, title: row.fields?.title } : null); }, []);
  const onFeedPending = useCallback((pending: boolean) => { feedPending.current = pending; }, []);
  const onDocumentPending = useCallback((pending: boolean) => { documentPending.current = pending; }, []);
  const onSharedPending = useCallback((pending: boolean) => { sharedPending.current = pending; }, []);
  const mayLeaveDocument = () => !(documentPending.current || feedPending.current || sharedPending.current || (presentation !== 'flow' && taskPending.current)) || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?'));
  const close = () => { if (presentation === 'flow' ? mayLeaveConversationWork() : mayLeaveDocument()) onClose(); };
  useModalFocus(dialog, close, presentation === 'dialog');
  useEffect(() => {
    if (presentation !== 'flow') return;
    return registerConversationNavigationGuard({ userId: api.getCurrentUserId(), credential: api.getAccessToken(), workspaceId }, mayLeaveDocument);
  }, [presentation, workspaceId, channel.id]);
  useEffect(() => {
    window.addEventListener('freecollab:auth-changed', onClose);
    return () => window.removeEventListener('freecollab:auth-changed', onClose);
  }, [onClose]);
  useEffect(() => { navigation.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }, [tab]);
  const select = (next: WorkHubTab) => { if (!visibleTabs.some(item => item.id === next) || next === tab || !mayLeaveDocument()) return; if (next === 'meetings') setMeetingOpened(true); setTab(next); };
  const props = { workspaceId, channelId: channel.id, isAdmin: channel.role === 'admin' };
  const actor = api.getCurrentUserId();
  const flow = presentation === 'flow';
  useEffect(() => {
    if (!flow) return;
    const frame = requestAnimationFrame(() => { const region = dialog.current; if (!region) return; if (!region.contains(document.activeElement)) { if (window.matchMedia('(max-width: 999px)').matches) region.scrollIntoView({ block: 'start', behavior: 'auto' }); region.focus({ preventScroll: true }); } });
    return () => cancelAnimationFrame(frame);
  }, [flow, channel.id, initialTab]);
  const selectedLabel = tabs.find(item => item.id === tab)?.label || 'Shared work';
  const content = <>
    <section ref={dialog} tabIndex={-1} role={flow ? 'region' : 'dialog'} aria-modal={flow ? undefined : true} aria-label={t(flow ? 'Work in this conversation' : 'Workspace tools')} className={flow ? 'fc-conversation-work' : 'fc-work-hub'} data-work-kind={tab} onKeyDown={event => { if (!flow || event.key !== 'Escape' || event.defaultPrevented || (event.target as Element).closest('[role="dialog"]')) return; event.preventDefault(); event.stopPropagation(); close(); }}>
      <header className={flow ? "fc-conversation-work-header" : "fc-work-hub-header"}><div><span className="fc-kicker">{channel.name || t("Your channel")}</span><h2>{t(flow ? selectedLabel : "Room for good work.")}</h2>{!flow && <p>{t("Plan, create and meet in one shared place.")}</p>}</div><button type="button" className="fc-icon-button" aria-label={t(flow ? "Close this work" : "Close workspace tools")} onClick={close}><X size={20} /></button></header>
      {flow && sourceMessage?.channel_id === channel.id && <div className="fc-conversation-work-source"><div><span>{t("From this conversation")}</span><p>{sourceMessage.content}</p>{onRevealSource && <button type="button" onClick={onRevealSource}>{t("Show source message")}</button>}</div></div>}
      {flow && sourceMessage && selectedResource && onResourceSaved && <div className="fc-conversation-work-source"><button type="button" onClick={() => onResourceSaved(selectedResource)}>{t("Link this work to the message")}</button></div>}
      {!flow && <><label className="fc-work-hub-tool-switch"><span>{t('Workspace tool')}</span><select className="fc-input" aria-label={t('Workspace tool')} value={tab} onChange={event => select(event.target.value as WorkHubTab)}>{visibleTabs.map(item => <option key={item.id} value={item.id}>{t(item.label)}</option>)}</select></label>
      <nav ref={navigation} role="tablist" aria-label={t("Workspace tools")} className="fc-work-hub-tabs" onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const current = visibleTabs.findIndex(item => item.id === tab);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? visibleTabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + visibleTabs.length) % visibleTabs.length;
        select(visibleTabs[next].id); navigation.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
      }}>{visibleTabs.map(({ id, label, icon: Icon }) => <button key={id} id={`work-tab-${id}`} type="button" role="tab" tabIndex={tab === id ? 0 : -1} aria-selected={tab === id} aria-controls={`work-panel-${id}`} onClick={() => select(id)}><Icon size={16} /><span>{t(label)}</span></button>)}</nav></>}
      <div className={flow ? "fc-conversation-work-body" : "fc-work-hub-body"}>
        {!contextChecked && <p role="status" className="fc-account-muted">{t('Checking channel collaboration…')}</p>}
        {sharedWork && tab !== 'shared-work' && ['tasks', 'documents', 'workflows', 'feed'].includes(tab) && <p className="fc-account-muted">{t('These tools belong to this installation. Use Shared work for tasks, calendar and documents shared across the connection.')}</p>}
        {contextChecked && tab !== 'meetings' && <div key={`${actor}:${channel.id}:${tab}`} role={flow ? undefined : "tabpanel"} id={`work-panel-${tab}`} aria-labelledby={flow ? undefined : `work-tab-${tab}`}>
          {tab === 'shared-work' && sharedWork && <FederatedWorkPanel channelId={channel.id} onPendingChange={onSharedPending} />}
          {tab === 'feed' && <ActivityFeedPanel {...props} onPendingChange={onFeedPending} />}
          {tab === 'copilot' && <CopilotPanel {...props} isAdmin={dataAdmin && unrestricted} />}
          {tab === 'cloud' && <><ChannelSyncNotice channelId={channel.id} kind="files" /><CloudFilesPicker workspaceId={workspaceId} channelId={channel.id} /></>}
          {tab === 'external-meetings' && <ExternalMeetingsPanel {...props} isAdmin={integrationAdmin && unrestricted && channel.role === 'admin'} />}
          {tab === 'tasks' && <ProjectTasksPanel {...props} presentation={flow ? "flow" : "full"} initialProjectId={initialProjectId} focusTaskId={focusTaskId} sourceMessage={sourceMessage} onPendingChange={onTaskPending} onSelectedTask={onSelectedTask} onOpenMessage={onRevealMessage ? (_channelId, messageId) => onRevealMessage(messageId) : undefined} onTaskSaved={row => onResourceSaved?.({ kind: "task", id: row.id, projectId: row.project_id, title: row.fields?.title })} />}
          {tab === 'documents' && <SharedDocumentsPanel {...props} presentation={flow ? "flow" : "full"} initialDraft={flow && sourceMessage && !initialDocumentId && !initialStackId ? { key: sourceMessage.id, title: sourceMessage.content.trim().split("\n")[0].slice(0, 120), text: sourceMessage.content } : undefined} onSelectedDocument={onSelectedDocument} onCreatedDocument={row => onResourceSaved?.({ kind: "document", id: row.id, title: row.title })} initialDocumentId={initialDocumentId} initialStackId={initialStackId} onPendingChange={onDocumentPending} pendingStackMessageId={pendingStackMessageId} onStackMessageSaved={onStackMessageSaved} onRevealMessage={onRevealMessage ? id => { if (flow || mayLeaveDocument()) onRevealMessage(id); } : undefined} />}
          {tab === 'workflows' && <WorkflowsPanel {...props} />}
          {tab === 'apps' && <ChannelApps workspaceId={workspaceId} channelId={channel.id} />}
          {tab === 'data' && <DataControlsPanel {...props} isAdmin={dataAdmin && unrestricted} />}
          {tab === 'mobile' && <MobileSettings />}
        </div>}
        {meetingOpened && <div hidden={tab !== 'meetings'} role={flow ? undefined : "tabpanel"} id="work-panel-meetings" aria-labelledby={flow ? undefined : "work-tab-meetings"}><MeetingsPanel key={`${actor}:${channel.id}`} {...props} /></div>}
      </div>
      <footer className={flow ? "fc-conversation-work-footer" : "fc-work-hub-footer"}><ShieldCheck size={15} /><span>{channel.secure ? t("Messages, tasks and documents use verified device encryption.") : t("Your current channel and workspace permissions apply.")}</span><button type="button" className="fc-button" onClick={close}>{t("Back to conversation")}</button></footer>
    </section>
  </>;
  return flow ? content : createPortal(<div className="fc-overlay fc-work-hub-overlay">{content}</div>, document.body);
}
