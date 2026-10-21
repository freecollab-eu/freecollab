import { t, useI18n } from '../i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckSquare, FileText, GitBranch, ShieldCheck, Smartphone, Video, X, Newspaper, Sparkles, Cloud } from 'lucide-react';
import type { Channel } from '../lib/api';
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
import '../styles/work-hub.css';

export type WorkHubTab = 'tasks' | 'meetings' | 'documents' | 'workflows' | 'data' | 'mobile' | 'feed' | 'copilot' | 'cloud' | 'external-meetings' | 'apps';
interface Props { workspaceId: string; channel: Channel; onClose: () => void; initialTab?: WorkHubTab; dataAdmin?: boolean; integrationAdmin?: boolean }
const tabs = [
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

export function WorkHub({ workspaceId, channel, onClose, initialTab = 'tasks', dataAdmin = false, integrationAdmin = false }: Props) {
  useI18n();
  const [tab, setTab] = useState<WorkHubTab>(initialTab);
  const [unrestricted, setUnrestricted] = useState(false);
  useEffect(() => { const token = api.getAccessToken(); let live = true; void api.getMe().then(me => { if (live && token === api.getAccessToken()) setUnrestricted(me.session_scope !== 'workspace' && me.account_type !== 'managed'); }).catch(() => {}); return () => { live = false; }; }, []);
  const [meetingOpened, setMeetingOpened] = useState(initialTab === 'meetings');
  const dialog = useRef<HTMLElement>(null), navigation = useRef<HTMLElement>(null);
  const documentPending = useRef(false), feedPending = useRef(false);
  const onFeedPending = useCallback((pending: boolean) => { feedPending.current = pending; }, []);
  const onDocumentPending = useCallback((pending: boolean) => { documentPending.current = pending; }, []);
  const mayLeaveDocument = () => !(documentPending.current || feedPending.current) || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?'));
  const close = () => { if (mayLeaveDocument()) onClose(); };
  useModalFocus(dialog, close);
  useEffect(() => {
    window.addEventListener('freecollab:auth-changed', onClose);
    return () => window.removeEventListener('freecollab:auth-changed', onClose);
  }, [onClose]);
  useEffect(() => { navigation.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }, [tab]);
  const select = (next: WorkHubTab) => { if (next === tab || !mayLeaveDocument()) return; if (next === 'meetings') setMeetingOpened(true); setTab(next); };
  const props = { workspaceId, channelId: channel.id, isAdmin: channel.role === 'admin' };
  const actor = api.getCurrentUserId();
  return createPortal(<div className="fc-overlay fc-work-hub-overlay">
    <section ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("Workspace tools")} className="fc-work-hub">
      <header className="fc-work-hub-header"><div><span className="fc-kicker">{channel.name || t("Your channel")}</span><h2>{t("Room for good work.")}</h2><p>{t("Plan, create and meet in one shared place.")}</p></div><button type="button" className="fc-icon-button" aria-label={t("Close workspace tools")} onClick={close}><X size={20} /></button></header>
      <label className="fc-work-hub-tool-switch"><span>{t('Workspace tool')}</span><select className="fc-input" aria-label={t('Workspace tool')} value={tab} onChange={event => select(event.target.value as WorkHubTab)}>{tabs.map(item => <option key={item.id} value={item.id}>{t(item.label)}</option>)}</select></label>
      <nav ref={navigation} role="tablist" aria-label={t("Workspace tools")} className="fc-work-hub-tabs" onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const current = tabs.findIndex(item => item.id === tab);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        select(tabs[next].id); navigation.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
      }}>{tabs.map(({ id, label, icon: Icon }) => <button key={id} id={`work-tab-${id}`} type="button" role="tab" tabIndex={tab === id ? 0 : -1} aria-selected={tab === id} aria-controls={`work-panel-${id}`} onClick={() => select(id)}><Icon size={16} /><span>{t(label)}</span></button>)}</nav>
      <div className="fc-work-hub-body">
        {tab !== 'meetings' && <div key={`${actor}:${channel.id}:${tab}`} role="tabpanel" id={`work-panel-${tab}`} aria-labelledby={`work-tab-${tab}`}>
          {tab === 'feed' && <ActivityFeedPanel {...props} onPendingChange={onFeedPending} />}
          {tab === 'copilot' && <CopilotPanel {...props} isAdmin={dataAdmin && unrestricted} />}
          {tab === 'cloud' && <><ChannelSyncNotice channelId={channel.id} kind="files" /><CloudFilesPicker workspaceId={workspaceId} channelId={channel.id} /></>}
          {tab === 'external-meetings' && <ExternalMeetingsPanel {...props} isAdmin={integrationAdmin && unrestricted && channel.role === 'admin'} />}
          {tab === 'tasks' && <ProjectTasksPanel {...props} />}
          {tab === 'documents' && <SharedDocumentsPanel {...props} onPendingChange={onDocumentPending} />}
          {tab === 'workflows' && <WorkflowsPanel {...props} />}
          {tab === 'apps' && <ChannelApps workspaceId={workspaceId} channelId={channel.id} />}
          {tab === 'data' && <DataControlsPanel {...props} isAdmin={dataAdmin && unrestricted} />}
          {tab === 'mobile' && <MobileSettings />}
        </div>}
        {meetingOpened && <div hidden={tab !== 'meetings'} role="tabpanel" id="work-panel-meetings" aria-labelledby="work-tab-meetings"><MeetingsPanel key={`${actor}:${channel.id}`} {...props} /></div>}
      </div>
      <footer className="fc-work-hub-footer"><ShieldCheck size={15} /><span>{channel.secure ? t("Messages, tasks and documents use verified device encryption.") : t("Your current channel and workspace permissions apply.")}</span><button type="button" className="fc-button" onClick={close}>{t("Done")}</button></footer>
    </section>
  </div>, document.body);
}
