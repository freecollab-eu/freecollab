import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { ArrowLeft, Check, ChevronDown, Hash, Search, Settings2, Users, X } from 'lucide-react';
import { demoOwners, type DemoChannel, type DemoOwner } from '../lib/demo-showroom';
import { useModalFocus } from '../lib/useModalFocus';
import '../styles/demo-channel-switcher.css';

export type Translate = (source: string, variables?: Record<string, string | number>) => string;
export interface DemoManagedChannel {
  id: DemoChannel;
  name: string;
  description: string;
  members: readonly DemoOwner[];
  sampleName: boolean;
  sampleDescription: boolean;
}
export interface DemoChannelSwitcherProps {
  value: DemoChannel;
  onChange: (value: DemoChannel) => void;
  channels: readonly DemoManagedChannel[];
  onUpdate: (id: DemoChannel, patch: { name: string; description: string; members: DemoOwner[] }) => void;
  t: Translate;
}

/** Local showroom channel controls. No account, storage or provider operations. */
export function DemoChannelSwitcher({ value, onChange, channels, onUpdate, t }: DemoChannelSwitcherProps) {
  const id = useId(), trigger = useRef<HTMLButtonElement>(null), dialog = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false), [query, setQuery] = useState('');
  const [managing, setManaging] = useState<DemoChannel | null>(null);
  const [name, setName] = useState(''), [description, setDescription] = useState('');
  const [members, setMembers] = useState<DemoOwner[]>([]);
  const [position, setPosition] = useState({ left: 16, top: 80 });
  const active = channels.find(channel => channel.id === value) ?? channels[0];
  const channelName = (channel: DemoManagedChannel) => channel.sampleName ? t(channel.name) : channel.name;
  const channelDescription = (channel: DemoManagedChannel) => channel.sampleDescription ? t(channel.description) : channel.description;
  const close = () => { setOpen(false); setManaging(null); setQuery(''); };
  useModalFocus(dialog, close, open);

  useEffect(() => {
    if (!open) return;
    const updatePosition = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(420, Math.max(0, window.innerWidth - 32));
      setPosition({ left: Math.max(16, Math.min(rect.left, window.innerWidth - width - 16)), top: Math.max(16, Math.min(rect.bottom + 12, window.innerHeight - 180)) });
    };
    updatePosition();
    window.addEventListener('resize', updatePosition);
    return () => window.removeEventListener('resize', updatePosition);
  }, [open]);

  useLayoutEffect(() => {
    if (open) dialog.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus({ preventScroll: true });
  }, [open, managing]);

  if (!active) return null;
  const search = query.trim().toLocaleLowerCase();
  const visible = channels.filter(channel => `${channelName(channel)} ${channelDescription(channel)}`.toLocaleLowerCase().includes(search));
  function beginManage() {
    setName(channelName(active));
    setDescription(channelDescription(active));
    setMembers([...active.members]);
    setManaging(active.id);
  }
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!managing || !name.trim() || name.trim().length > 48 || description.trim().length > 180) return;
    onUpdate(managing, { name: name.trim(), description: description.trim(), members: demoOwners.filter(owner => members.includes(owner)) });
    close();
  }
  const modalStyle = { '--fc-channel-left': `${position.left}px`, '--fc-channel-top': `${position.top}px` } as CSSProperties;

  return <div className="fc-demo-channel-switcher">
    <button ref={trigger} type="button" className="fc-demo-channel-trigger" aria-label={t('Choose channel')} aria-describedby={`${id}-current`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? `${id}-dialog` : undefined} onClick={() => { trigger.current?.focus({ preventScroll: true }); setOpen(true); }}>
      <span id={`${id}-current`} className="fc-demo-channel-current"><span className="fc-demo-channel-kicker">{t('Channels')}</span><span className="fc-demo-channel-current-name"><Hash size={13} aria-hidden="true" /><strong>{channelName(active)}</strong></span></span><ChevronDown size={15} aria-hidden="true" />
    </button>
    {open && <div ref={dialog} id={`${id}-dialog`} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={managing ? `${id}-local-note` : undefined} className="fc-demo-channel-overlay" style={modalStyle} tabIndex={-1} onPointerDown={event => {
      if (event.target !== event.currentTarget) return;
      event.preventDefault();
      event.stopPropagation();
      close();
    }}>
      <div className="fc-demo-channel-panel">
        <header className="fc-demo-channel-heading"><div><span className="fc-demo-channel-kicker">{t('Nord Studio')}</span><h2 id={`${id}-title`}>{t(managing ? 'Manage channel' : 'Channels')}</h2></div><button type="button" className="fc-demo-channel-close" aria-label={t('Close channel picker')} onClick={close}><X size={18} aria-hidden="true" /></button></header>
        {managing ? <form onSubmit={save} className="fc-demo-channel-form">
          <button type="button" className="fc-demo-channel-back" onClick={() => setManaging(null)}><ArrowLeft size={14} aria-hidden="true" />{t('Back to channels')}</button>
          <p id={`${id}-local-note`} className="fc-demo-channel-note">{t('You manage only this demo channel. Changes stay in this tab.')}</p>
          <label htmlFor={`${id}-name`}>{t('Channel name')}<input id={`${id}-name`} data-autofocus value={name} onChange={event => setName(event.target.value)} required maxLength={48} autoComplete="off" /></label>
          <label htmlFor={`${id}-description`}>{t('Channel description')}<textarea id={`${id}-description`} value={description} onChange={event => setDescription(event.target.value)} maxLength={180} rows={3} /></label>
          <fieldset><legend>{t('Demo members')}</legend><p>{t('Choose who appears in this channel’s member list.')}</p><div className="fc-demo-channel-members">{demoOwners.map(owner => <label key={owner}><input type="checkbox" checked={members.includes(owner)} onChange={event => { const checked = event.currentTarget.checked; setMembers(current => checked ? [...current, owner] : current.filter(member => member !== owner)); }} /><span className="fc-demo-channel-member-initial" aria-hidden="true">{owner.charAt(0)}</span><span>{owner}</span></label>)}</div></fieldset>
          <footer className="fc-demo-channel-form-actions"><button type="button" onClick={close}>{t('Cancel')}</button><button className="fc-showroom-primary" type="submit" disabled={!name.trim()}>{t('Save channel')}</button></footer>
        </form> : <>
          <label className="fc-demo-channel-search" htmlFor={`${id}-search`}><Search size={16} aria-hidden="true" /><span className="fc-demo-channel-sr-only">{t('Search channels')}</span><input id={`${id}-search`} data-autofocus value={query} onChange={event => setQuery(event.target.value)} placeholder={t('Search channels')} autoComplete="off" /></label>
          <ul className="fc-demo-channel-list" aria-label={t('Available channels')}>{visible.map(channel => <li key={channel.id}><button type="button" aria-pressed={channel.id === value} onClick={() => { onChange(channel.id); close(); }}><span className="fc-demo-channel-row-hash" aria-hidden="true"><Hash size={18} /></span><span className="fc-demo-channel-row-copy"><strong>{channelName(channel)}</strong>{channelDescription(channel) && <span>{channelDescription(channel)}</span>}<small><Users size={12} aria-hidden="true" />{t('{count} members', { count: channel.members.length })}</small></span>{channel.id === value && <Check size={16} className="fc-demo-channel-row-check" aria-hidden="true" />}</button></li>)}</ul>
          {!visible.length && <p className="fc-demo-channel-empty" role="status">{t('No channels match your search.')}</p>}
          <footer className="fc-demo-channel-manage"><button type="button" onClick={beginManage}><Settings2 size={16} aria-hidden="true" /><span>{t('Manage channel')}<small>#{channelName(active)}</small></span></button><p>{t('Demo changes stay in this tab.')}</p></footer>
        </>}
      </div>
    </div>}
  </div>;
}
