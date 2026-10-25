/** Device-only adapters. Decrypted text never enters native search/index APIs. */
import { api, type Channel } from './api';
import { collaborationApi } from './collaboration-api';
import { documentCalendarApi } from './document-calendar-api';
import { clipsApi } from './clips-api';
import { EncryptedWorkClient } from './encrypted-work-api';
import { FederatedWorkClient } from './federated-work-api';
import type { SearchSource, SearchSources } from './enterprise-search';

export async function additionalEncryptedSources(channel: Channel, signal?: AbortSignal): Promise<SearchSources> {
  const token = api.getAccessToken(), items: SearchSource[] = [], skipped: SearchSources['skipped'] = []; let complete = true;
  const current = () => { if (!token || token !== api.getAccessToken()) throw Error('The signed-in account changed.'); if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError'); };
  const add = (source: SearchSource) => { if (items.length >= 1000 || items.reduce((size, row) => size + row.text.length, 0) + source.text.length > 1000000) { complete = false; return; } items.push({...source, secure: true}); };
  const base = (row: {id: string; created_by: string; created_at: string; revision?: number; matrix_event_id?: string | null}) => ({id: row.id, channel_id: channel.id, created_by: row.created_by, created_at: row.created_at, parent_id: null, truncated: false, version: JSON.stringify([row.revision || row.id, row.matrix_event_id || null])});
  const [decisions, links, events, clips] = await Promise.all([collaborationApi.decisions(channel.id), documentCalendarApi.documents(channel.id), documentCalendarApi.calendar(channel.id), clipsApi.list(channel.id)]); current();
  for (const row of decisions) { if (row.resolution_error || !row.title || typeof row.content !== 'string') { skipped.push({kind:'decision',id:row.id,status:409}); continue; } add({...base(row), kind:'decision', title:row.title, text:row.content}); }
  for (const row of links) { if (!row.fields || row.deleted) continue; add({...base(row), kind:'linked-document', title:row.fields.title, text:row.fields.notes}); }
  for (const row of events) { if (!row.fields || row.deleted) continue; add({...base(row), kind:'calendar', title:row.fields.title, text:[row.fields.description,row.fields.location,row.fields.starts_at,row.fields.ends_at].join('\n')}); }
  for (const row of clips) { if (!row.fields) { skipped.push({kind:'clip',id:row.id,status:409}); continue; } add({...base(row), kind:'clip', title:row.fields.title, text:row.fields.transcript || ''}); }
  let before: string | undefined;
  for (let page = 0; page < 10; page++) {
    const messages = await api.getMessages(channel.id, {before, limit:100,signal}); current();
    for (const row of messages) if (!row.deleted && !row.locked) add({id:row.id, channel_id:channel.id, kind:'message', title:row.content.split('\n')[0].slice(0,160) || 'Message', text:row.content, created_by:row.author_id, created_at:row.created_at, parent_id:row.parent_id || null, truncated:false, version:JSON.stringify([row.id,row.edited_at || '',row.content])});
    if (messages.length < 100) break;
    const last = messages[messages.length - 1]?.id; if (!last || last === before) { complete = false; break; } before = last; if (page === 9) complete = false;
  }
  const client = new EncryptedWorkClient(channel.id);
  try {
    const view = await client.load(); current(); complete &&= !view.warnings.length && !view.pending_pointers;
    for (const row of view.records) {
      if (row.selected.archived || ['organization','comment','collection','workflow-run'].includes(row.kind)) continue;
      if (row.invalid.length || row.conflicts) { skipped.push({kind:row.kind,id:row.id,status:409}); continue; }
      const kind = row.kind === 'linked-file' ? 'linked-document' : row.kind;
      if (!['feed','decision','workflow','task','project','linked-document'].includes(kind)) continue;
      add({id:row.id,channel_id:channel.id,kind:kind as SearchSource['kind'],title:row.selected.fields.title,text:[row.selected.fields.body,...(row.selected.fields.checklist || []).map(part=>part.title),...(row.selected.fields.steps || []).map(part=>part.title+'\n'+part.body)].join('\n'),created_by:row.created_by,created_at:new Date(row.history[0].timestamp).toISOString(),parent_id:row.selected.fields.project_id || null,truncated:false,version:JSON.stringify(row.heads.map(head=>head.event_id).sort()),encrypted_resource:true,encrypted_kind:row.kind});
    }
    if (view.legacyRecords.length && view.context.mode === 'federated') {
      const legacy = new FederatedWorkClient(channel.id);
      try {
        const actual = await legacy.load(); current();
        for (const row of actual.records) {
          if (row.selected.archived || row.invalid.length || row.conflicts) continue;
          const fields = row.selected.fields;
          let title = fields.title, text = 'description' in fields ? fields.description : '';
          let version = JSON.stringify(row.heads.map(head=>head.event_id).sort());
          if (row.kind === 'document') { try { const state = await legacy.document(row); current(); title = state.title; text = state.body; version = JSON.stringify([version,state.update_ids]); } catch { current(); skipped.push({kind:'document',id:row.id,status:409}); continue; } }
          else if (row.kind === 'calendar' && 'starts_at' in fields) text = [fields.description,fields.location,fields.starts_at,fields.ends_at].join('\n');
          add({id:row.id,channel_id:channel.id,kind:row.kind,title,text,created_by:row.created_by,created_at:new Date(row.history[0].timestamp).toISOString(),parent_id:null,truncated:false,version,encrypted_resource:true,encrypted_kind:row.kind});
        }
      } finally { legacy.stop(); }
    }
  } finally { client.stop(); }
  current();
  // Fixed-size commitments keep message bodies out of citation identifiers.
  for (const item of items) { item.version = [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(item.version)))].map(byte=>byte.toString(16).padStart(2,'0')).join(''); current(); }
  return {items,skipped,complete:complete && !skipped.length};
}
