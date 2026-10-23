import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Check, Loader2, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { channelMetadataFields, nativeChannelMetadata, type ChannelMetadata } from '../lib/channel-metadata';
import { getSecureMessaging } from '../lib/secure-client';
import { useModalFocus } from '../lib/useModalFocus';
import { useI18n } from '../i18n';
import '../styles/channel-metadata.css';

interface Props {
  channel: Channel;
  onClose: () => void;
  onSaved: (metadata: ChannelMetadata) => void;
}

/** Real channel details. Failed/stale writes retain the person's unsaved form. */
export function ChannelMetadataPanel({ channel, onClose, onSaved }: Props) {
  const { t } = useI18n(), uid = useId(), panel = useRef<HTMLElement>(null);
  const [loaded, setMetadata] = useState<ChannelMetadata | null>(null), [loadedScope, setLoadedScope] = useState('');
  const [name, setName] = useState(''), [description, setDescription] = useState('');
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false), [error, setError] = useState('');
  const generation = useRef(0), mounted = useRef(false), busy = useRef(false);
  const identity = JSON.stringify([api.getAccessToken(), api.getCurrentUserId(), channel.workspace_id, channel.id, !!channel.secure]);
  const metadata = loadedScope === identity ? loaded : null;
  const live = useRef(identity); live.current = identity;
  const callbacks = useRef({ onClose, onSaved }); callbacks.current = { onClose, onSaved };
  const close = () => { if (!busy.current) callbacks.current.onClose(); };
  useModalFocus(panel, close);
  const valid = (revision: number, scope: string) => mounted.current && generation.current === revision && live.current === scope
    && scope === JSON.stringify([api.getAccessToken(), api.getCurrentUserId(), channel.workspace_id, channel.id, !!channel.secure]);

  async function load() {
    const revision = ++generation.current, scope = identity;
    setLoading(true); setError('');
    try {
      const record = await api.getChannelMetadata(channel.id);
      if (!valid(revision, scope)) return;
      if (record.secure !== !!channel.secure) throw new Error('Channel encryption changed; reload channel details');
      const current = record.secure ? await (await getSecureMessaging()).getChannelMetadata(channel.id, record) : nativeChannelMetadata(record);
      if (!valid(revision, scope)) return;
      setLoadedScope(scope); setMetadata(current); setName(current.name); setDescription(current.description);
    } catch (failure) { if (valid(revision, scope)) setError(failure instanceof Error ? failure.message : 'Channel details could not be loaded.'); }
    finally { if (valid(revision, scope)) setLoading(false); }
  }

  useEffect(() => {
    mounted.current = true; busy.current = false; setSaving(false); setMetadata(null); void load();
    const changed = () => { generation.current++; callbacks.current.onClose(); };
    window.addEventListener('freecollab:auth-changed', changed);
    return () => { mounted.current = false; generation.current++; window.removeEventListener('freecollab:auth-changed', changed); };
  }, [identity]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!metadata?.can_manage || busy.current || loading) return;
    const revision = generation.current, scope = identity;
    let fields: { name: string; description: string };
    try { fields = channelMetadataFields(name, description); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Channel details could not be saved.'); return; }
    if (!valid(revision, scope)) return;
    busy.current = true; setSaving(true); setError('');
    try {
      const saved = metadata.secure
        ? await (await getSecureMessaging()).setChannelMetadata(channel.id, fields.name, fields.description, metadata.version)
        : nativeChannelMetadata(await api.updateChannelMetadata(channel.id, { ...fields, expected_version: metadata.version }));
      if (!valid(revision, scope)) return;
      callbacks.current.onSaved(saved); callbacks.current.onClose();
    } catch (failure) { if (valid(revision, scope)) setError(failure instanceof Error ? failure.message : 'Channel details could not be saved.'); }
    finally { if (valid(revision, scope)) { busy.current = false; setSaving(false); } }
  }

  const dirty = !!metadata && (name.trim() !== metadata.name || description.replace(/\r\n?/g, '\n').trim() !== metadata.description);
  return createPortal(<div className="fc-channel-metadata-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={panel} className="fc-channel-metadata" role="dialog" aria-modal="true" aria-labelledby={uid + '-title'} tabIndex={-1} aria-busy={loading || saving}>
      <header><div><span className="fc-kicker">{t('This conversation')}</span><h2 id={uid + '-title'}>{t(metadata?.can_manage ? 'Manage channel' : 'Channel details')}</h2></div><button data-autofocus type="button" className="fc-icon-button" aria-label={t('Close channel details')} disabled={saving} onClick={close}><X size={20} aria-hidden="true" /></button></header>
      {loading && <p role="status"><Loader2 size={16} aria-hidden="true" />{t('Loading channel details…')}</p>}
      {error && <div role="alert" className="fc-alert"><p>{t(error)}</p>{dirty && <p>{t('Your changes are still here. Reload only when you want to replace this draft.')}</p>}<button type="button" className="fc-text-button" disabled={saving || loading} onClick={() => void load()}><RefreshCw size={14} aria-hidden="true" />{t('Reload channel details')}</button></div>}
      {metadata && <form onSubmit={save}>
        <p className="fc-channel-metadata-boundary"><ShieldCheck size={16} aria-hidden="true" />{t(metadata.secure
          ? 'Names and descriptions are encrypted on your device. The server stores an event reference and revision metadata, without their readable text.'
          : 'Channel members and the authorized server can read these details.')}</p>
        <div className="fc-channel-metadata-field"><label htmlFor={uid + '-name'}>{t('Channel name')}</label><input id={uid + '-name'} value={name} maxLength={100} readOnly={!metadata.can_manage} disabled={saving || loading} required onChange={event => setName(event.target.value)} autoComplete="off" /></div>
        <div className="fc-channel-metadata-field"><label htmlFor={uid + '-description'}>{t('Channel description')}</label><textarea id={uid + '-description'} value={description} maxLength={1000} readOnly={!metadata.can_manage} disabled={saving || loading} rows={5} onChange={event => setDescription(event.target.value)} /></div>
        {!metadata.can_manage && <p>{t(metadata.management === 'connection' ? 'Shared-channel details are managed by their connection' : metadata.management === 'conversation' ? 'Direct conversation details use their conversation controls' : 'Only a channel administrator can change these details.')}</p>}
        <footer><button type="button" className="fc-button" disabled={saving} onClick={close}>{t('Cancel')}</button>{metadata.can_manage && <button type="submit" className="fc-button fc-button-primary" disabled={saving || loading || !dirty || !name.trim()}>{saving ? <Loader2 size={15} aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}{t(saving ? 'Saving…' : 'Save channel')}</button>}</footer>
      </form>}
    </section>
  </div>, document.body);
}
