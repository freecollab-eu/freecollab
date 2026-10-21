import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Loader2, MessageCircle, Plus, RefreshCw, Settings2 } from 'lucide-react';
import { useAccount } from '../account/AccountProvider';
import { WorkspaceAvatar } from './WorkspaceAvatar';

export function WorkspaceSwitcher() {
  useI18n();
  const { workspace, workspaces, workspacesLoading, workspacesError, selectWorkspace, refreshWorkspaces, openWorkspaceManager, openWorkspaceCreator, openDirectMessages } = useAccount();
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!container.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    requestAnimationFrame(() => menu.current?.querySelector<HTMLButtonElement>('button[aria-checked="true"], button')?.focus());
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);

  const close = () => { setOpen(false); trigger.current?.focus(); };
  return <div className="fc-workspace-switcher" ref={container}>
    <button ref={trigger} type="button" className="fc-workspace-switch-trigger" aria-label={t("Switch workspace{value0}", { value0: String(workspace ? `, ${workspace.name}` : '') })} aria-expanded={open} aria-haspopup="menu" aria-controls="workspace-switch-menu" onClick={() => setOpen(value => !value)}><WorkspaceAvatar workspace={workspace} /><div><strong>{workspace?.name || (workspacesLoading ? t("Loading workspace…") : t("Your workspaces"))}</strong><small>{workspace ? t(workspace.role === 'owner' ? 'Owner workspace' : workspace.role === 'admin' ? 'Admin workspace' : 'Member workspace') : t("Choose a shared place")}</small></div>{workspacesLoading ? <Loader2 size={14} className="fc-account-spinner" /> : <ChevronDown size={14} />}</button>
    {open && <div ref={menu} id="workspace-switch-menu" className="fc-workspace-menu" role="menu" aria-label={t("Your workspaces")} onKeyDown={event => {
      const buttons = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || []);
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      if (event.key === 'Escape') { event.preventDefault(); close(); }
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length; buttons[next]?.focus(); }
      else if (event.key === 'Tab') close();
    }}><p>{t("YOUR WORKSPACES")}</p>{workspaces.map(record => <button role="menuitemradio" aria-checked={record.id === workspace?.id} key={record.id} type="button" onClick={() => { selectWorkspace(record.id); close(); }}><WorkspaceAvatar workspace={record} /><span>{record.name}</span>{record.id === workspace?.id && <Check size={14} />}</button>)}{workspacesError && <><p className="fc-workspace-switch-error" role="alert">{workspacesError}</p><button role="menuitem" type="button" disabled={workspacesLoading} onClick={() => void refreshWorkspaces()}><RefreshCw size={15} />{t("Try again")}</button></>}<hr /><button role="menuitem" type="button" onClick={() => { setOpen(false); openWorkspaceManager(); }}><Settings2 size={15} /><span>{workspace ? t("Workspace settings & invitations") : t("Create a workspace")}</span></button>{workspace && <button role="menuitem" type="button" onClick={() => { setOpen(false); openDirectMessages(); }}><MessageCircle size={15} /><span>{t("Direct messages")}</span></button>}<button role="menuitem" type="button" onClick={() => { setOpen(false); openWorkspaceCreator(); }}><Plus size={15} /><span>{t("New workspace")}</span></button></div>}
  </div>;
}
