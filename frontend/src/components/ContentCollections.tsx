import { LabeledWritingField } from './LabeledWritingField';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { FolderPlus, Layers, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { t, useI18n } from '../i18n';
import { contentCollectionsApi, collectionCatalog, type ContentCollection, type CollectionItem } from '../lib/content-collections-api';
import { useScopedWritingGuard } from '../lib/useScopedWritingGuard';
import { ScopedCollaborationInput } from './ScopedCollaborationInput';
import '../styles/content-completion.css';

const key = (item: CollectionItem) => item.kind + ':' + item.id;
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'The collection action failed.';
/** References organize actual resources; originals, ACLs and histories stay put. */
export function ContentCollections({ workspaceId, channelId, stackId, onPendingChange, onBusyChange, onChanged }: { workspaceId: string; channelId: string; stackId?: string; onPendingChange?: (pending: boolean) => void; onBusyChange?: (busy: boolean) => void; onChanged?: () => void }) {
  useI18n(); const identity = useRef(api.getAccessToken()), epoch = useRef(0), mutation = useRef(false), createId = useRef<string | null>(null), id = useId();
  const [rows, setRows] = useState<ContentCollection[]>([]), [catalog, setCatalog] = useState<CollectionItem[]>([]), [adding, setAdding] = useState(''), [title, setTitle] = useState(''), [choice, setChoice] = useState(''), [renaming, setRenaming] = useState(''), [renameTitle, setRenameTitle] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const callbacks = useRef({onPendingChange, onBusyChange}); callbacks.current = {onPendingChange, onBusyChange};
  const sourceLease = useRef<(() => void) | null>(null);
  const draft = useRef({title: '', choice: '', rename: '', busy: false});
  function publish(part: keyof typeof draft.current, value: string | boolean) { (draft.current as Record<string, string | boolean>)[part] = value; callbacks.current.onPendingChange?.(Object.values(draft.current).some(Boolean)); if (part === 'busy') callbacks.current.onBusyChange?.(!!value); }
  function clearDraft() { draft.current = {title: '', choice: '', rename: '', busy: false}; callbacks.current.onPendingChange?.(false); callbacks.current.onBusyChange?.(false); }
  const dirty = !!title.trim() || !!choice || !!renaming && renameTitle !== rows.find(row => row.id === renaming)?.title;
  useScopedWritingGuard(channelId, dirty, busy, { workspaceId });
  const current = (request: number) => request === epoch.current && identity.current === api.getAccessToken();
  useEffect(() => () => { clearDraft(); sourceLease.current?.(); }, []);
  async function load(request = epoch.current) { const values = await contentCollectionsApi.list(channelId); if (current(request)) { setRows(values); setLoading(false); if (values.some(row => row.items.some(item => !item.title))) { const materials = await collectionCatalog(channelId); if (current(request)) setCatalog(materials); } } }
  useEffect(() => {
    ++epoch.current; identity.current = api.getAccessToken(); clearDraft(); setRenaming(''); setRenameTitle(''); setRows([]); setCatalog([]); setAdding(''); setTitle(''); setChoice(''); setLoading(true); setError(''); setBusy(false); mutation.current = false;
    const changed = () => { if (!mutation.current) void load().catch(failure => { if (identity.current === api.getAccessToken()) setError(describe(failure)); }); };
    const revoke = () => { ++epoch.current; identity.current = null; clearDraft(); sourceLease.current?.(); setRenaming(''); setRenameTitle(''); setRows([]); setCatalog([]); setAdding(''); setTitle(''); setChoice(''); setLoading(false); };
    changed(); window.addEventListener('freecollab:content-collections-changed', changed); window.addEventListener('focus', changed); window.addEventListener('freecollab:auth-changed', revoke);
    return () => { ++epoch.current; window.removeEventListener('freecollab:content-collections-changed', changed); window.removeEventListener('focus', changed); window.removeEventListener('freecollab:auth-changed', revoke); };
  }, [channelId, workspaceId]);
  async function choose(id: string) { if (Object.values(draft.current).some(Boolean) && !window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?'))) return; clearDraft(); setTitle(''); setRenaming(''); setRenameTitle(''); const request = epoch.current; setAdding(id); setChoice(''); setError(''); try { const values = await collectionCatalog(channelId); if (current(request)) setCatalog(values); } catch (failure) { if (current(request)) setError(describe(failure)); } }
  async function run(action: () => Promise<unknown>) { if (mutation.current || identity.current !== api.getAccessToken()) return; const request = epoch.current; mutation.current = true; publish('busy', true); setBusy(true); setError(''); try { await action(); if (current(request)) { setAdding(''); setChoice(''); setTitle(''); setRenaming(''); setRenameTitle(''); clearDraft(); publish('busy', true); createId.current = null; await load(request); onChanged?.(); window.dispatchEvent(new CustomEvent('freecollab:content-collections-changed', { detail: { channel_id: channelId } })); } } catch (failure) { if (current(request)) setError(describe(failure)); } finally { if (current(request)) { mutation.current = false; publish('busy', false); setBusy(false); } } }
  async function submit(event: FormEvent, row?: ContentCollection) { event.preventDefault(); const item = catalog.find(item => key(item) === choice); if (row && !item) return; if (row) await run(() => contentCollectionsApi.add(channelId, row, item!)); else { createId.current ||= crypto.randomUUID(); await run(() => contentCollectionsApi.create(channelId, { id: createId.current!, title, items: item ? [item] : [] })); } }
  async function open(item: CollectionItem, element: HTMLButtonElement) {
    sourceLease.current?.(); const request = epoch.current; let active = true;
    const cancel = () => { active = false; window.removeEventListener('pointerdown', cancel, true); window.removeEventListener('keydown', cancel, true); };
    sourceLease.current = cancel; window.addEventListener('pointerdown', cancel, true); window.addEventListener('keydown', cancel, true);
    const isCurrent = () => active && current(request) && element.isConnected;
    try {
      const latest = (await collectionCatalog(channelId)).find(row => key(row) === key(item));
      if (!isCurrent()) return; if (!latest) throw new Error('This material is no longer available in the conversation.');
      const lease = {sourceElement: element, isCurrent};
      if (latest.encrypted_resource) window.dispatchEvent(new CustomEvent('freecollab:open-encrypted-resource', { detail: { channel_id: channelId, kind: latest.encrypted_kind || latest.kind, id: latest.id, ...lease } }));
      else if (latest.kind === 'message') window.dispatchEvent(new CustomEvent('freecollab:open-personal-source', { detail: { workspace_id: workspaceId, channel_id: channelId, kind: 'message', item_id: latest.id, ...lease } }));
      else window.dispatchEvent(new CustomEvent('freecollab:open-search-source', { detail: { channel_id: channelId, kind: latest.kind === 'file' ? 'file-content' : latest.kind, id: latest.id, parent_id: latest.parent_id, ...lease } }));
    } catch (failure) { if (isCurrent()) setError(describe(failure)); cancel(); }
  }

  const visible = stackId ? rows.filter(row => row.id === stackId) : rows;
  function form(row?: ContentCollection) { return <form className="fc-content-editor" onSubmit={event => void submit(event, row)} aria-label={t(row ? 'Add material' : 'New collection')}>
    {!row && <LabeledWritingField label={t('Collection name')}><ScopedCollaborationInput as="input" channelId={channelId} allowReferences={false} contextId={`${id}:${channelId}:collection-name`} className="fc-input" value={title} onValueChange={value => { publish('title', value.trim()); setTitle(value); createId.current = null; }} maxLength={100} disabled={busy} required /></LabeledWritingField>}
    <label>{t('Choose material')}<select className="fc-input" disabled={busy} value={choice} onChange={event => { publish('choice', event.target.value); setChoice(event.target.value); }}><option value="">{t(row ? 'Choose material' : 'Start with an empty collection')}</option>{catalog.filter(item => !row?.items.some(existing => key(existing) === key(item))).map(item => <option key={key(item)} value={key(item)}>{t(item.kind)} · {item.title || item.id}</option>)}</select></label>
    <div className="fc-content-toolbar"><button type="submit" className="fc-button" disabled={busy || (row ? !choice : !title.trim())}>{t(row ? 'Add material' : 'Create collection')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => { setAdding(''); setChoice(''); setTitle(''); clearDraft(); createId.current = null; }}>{t('Cancel')}</button></div>
  </form>; }
  return <section className="fc-collection-contents" aria-label={t(stackId ? 'Other material in this stack' : 'Collections')}>
    {!stackId && <header className="fc-content-toolbar"><h3><Layers size={17} />{t('Collections')}</h3><button type="button" className="fc-button" disabled={busy} onClick={() => void choose('new')}><FolderPlus size={15} />{t('New collection')}</button><button type="button" className="fc-icon-button" disabled={busy} aria-label={t('Refresh collections')} onClick={() => void load().catch(failure => setError(describe(failure)))}><RefreshCw size={15} /></button></header>}
    {error && <p role="alert" className="fc-alert">{t(error)}</p>}{loading && <p role="status">{t('Loading collections…')}</p>}{adding === 'new' && form()}
    {visible.map(row => <div key={row.id}>{!stackId && <header className="fc-content-toolbar"><h4>{row.title}</h4><button type="button" className="fc-icon-button" disabled={busy} aria-label={t('Rename collection')} onClick={() => { setRenaming(row.id); setRenameTitle(row.title); }}><Pencil size={14} /></button></header>}{renaming === row.id && <form className="fc-content-editor" onSubmit={event => { event.preventDefault(); void run(() => contentCollectionsApi.rename(channelId, row, renameTitle)); }}><LabeledWritingField label={t('Collection name')}><ScopedCollaborationInput as="input" channelId={channelId} allowReferences={false} contextId={`${id}:${row.id}:rename`} className="fc-input" value={renameTitle} maxLength={100} required disabled={busy} onValueChange={value => { publish('rename', value !== row.title ? value : ''); setRenameTitle(value); }} /></LabeledWritingField><button className="fc-button" disabled={busy || !renameTitle.trim()}>{t('Save name')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => { publish('rename', ''); setRenaming(''); setRenameTitle(''); }}>{t('Cancel')}</button></form>}<p className="fc-chat-muted">{t('Material references keep files and links in their original location.')}</p><ul className="fc-content-items">{row.items.map(item => <li key={key(item)}><div><button type="button" className="fc-text-button" disabled={item.available === false} onClick={event => void open(item, event.currentTarget)}>{item.available === false ? t('Unavailable material') : item.title || catalog.find(known => key(known) === key(item))?.title || t(item.kind)}</button><small>{t(item.kind)}</small></div><button type="button" className="fc-icon-button" aria-label={t('Remove material reference')} disabled={busy} onClick={() => void run(() => contentCollectionsApi.remove(channelId, row, item))}><Trash2 size={14} /></button></li>)}</ul><button type="button" className="fc-text-button" disabled={busy} onClick={() => void choose(row.id)}><Plus size={14} />{t('Add material')}</button>{adding === row.id && form(row)}{!stackId && !row.items.length && <button type="button" className="fc-text-button" disabled={busy} onClick={() => void run(() => contentCollectionsApi.removeCollection(channelId, row))}>{t('Delete empty collection')}</button>}</div>)}
  </section>;
}
