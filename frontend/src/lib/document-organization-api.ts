import { api } from './api';
import { getSecureMessaging } from './secure-client';
import type { SharedDocumentRecord } from './shared-documents-api';
import { validateMessageReferences, type MessageReference } from './message-references';

export interface DocumentStack { id: string; channel_id: string; title: string; created_by: string; created_at: string; updated_at: string; version: number; generation: string }
export interface DocumentPlacement { document_id: string; channel_id: string; stack_id: string | null; version: number; updated_by: string; updated_at: string }
export interface DocumentRelation { id: string; channel_id: string; first_document_id: string; second_document_id: string; created_by: string; created_at: string }
export interface StackMessageReference { id: string; channel_id: string; stack_id: string; message_id: string; created_by: string; created_at: string; available: boolean; excerpt: string | null; author_id?: string; source_edited_at?: string | null }
export interface StackMessagePreview { id: string; channel_id: string; text: string; author_id: string; created_at: string; edited_at: string | null; expected_text_sha256: string }
export interface StackDocumentComment { id: string; channel_id: string; document_id: string; stack_id: string | null; text: string; references?: MessageReference[]; created_by: string; created_at: string; resolved: boolean; version: number }
export interface DocumentOrganization { documents: SharedDocumentRecord[]; supported: boolean; stacks: DocumentStack[]; placements: DocumentPlacement[]; relations: DocumentRelation[]; message_references: StackMessageReference[]; stack_comments: StackDocumentComment[]; comments_incomplete: boolean }
export interface DocumentMoveUndo { documentId: string; fromStackId: string | null; toStackId: string | null; expectedVersion: number; fromStackGeneration: string | null }
const path = (channel: string) => '/channels/' + encodeURIComponent(channel) + '/shared-document-organization';
const unchanged = (owner: string | null) => { if (!owner || owner !== api.getAccessToken()) throw Error('The signed-in account changed.'); };
async function native(channel: string, owner: string | null) {
  unchanged(owner); const secure = await getSecureMessaging(); unchanged(owner);
  const encrypted = await secure.handles(channel); unchanged(owner); return !encrypted;
}
async function mutate<T>(channel: string, suffix: string, method: string, body?: unknown): Promise<T> {
  const owner = api.getAccessToken();
  if (!await native(channel, owner)) throw Error('Document stacks and relations are not yet available in encrypted channels.');
  unchanged(owner); const result = await api.request<T>(path(channel) + suffix, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); unchanged(owner);
  return result;
}
export const documentOrganizationApi = {
  async list(channel: string, archived = false): Promise<DocumentOrganization> {
    const owner = api.getAccessToken();
    if (!await native(channel, owner)) return { documents: [], supported: false, stacks: [], placements: [], relations: [], message_references: [], stack_comments: [], comments_incomplete: false };
    const result = await api.request<Omit<DocumentOrganization, 'supported'>>(path(channel) + (archived ? '?archived=true' : '')); unchanged(owner);
    if (!Array.isArray(result.stack_comments)) throw Error('Document comments could not be verified.');
    const stack_comments = result.stack_comments.map(row => {
      if (!row || row.channel_id !== channel || typeof row.text !== 'string' || !row.text || row.text.length > 4000)
        throw Error('Document comments could not be verified.');
      const references = validateMessageReferences(row.text, row.references ?? []);
      const value = { ...row };
      if (references.length) value.references = references; else delete value.references;
      return value;
    });
    return { ...result, stack_comments, supported: true };
  },
  async previewMessage(channel: string, id: string): Promise<StackMessagePreview> {
    const owner = api.getAccessToken();
    if (!await native(channel, owner)) throw Error('Saving messages in document stacks is not yet available in encrypted channels.');
    const result = await api.request<StackMessagePreview>(path(channel) + '/message-preview/' + encodeURIComponent(id)); unchanged(owner); return result;
  },
  saveMessage: (channel: string, stack: DocumentStack, preview: StackMessagePreview, id: string) => mutate<StackMessageReference>(channel, '/stacks/' + encodeURIComponent(stack.id) + '/message-references', 'POST', { id, message_id: preview.id, expected_text_sha256: preview.expected_text_sha256, stack_generation: stack.generation }),
  removeMessage: (channel: string, id: string) => mutate<void>(channel, '/message-references/' + encodeURIComponent(id), 'DELETE'),
  createStack: (channel: string, title: string, id: string) => mutate<DocumentStack>(channel, '/stacks', 'POST', { id, title }),
  renameStack: (channel: string, stack: DocumentStack, title: string) => mutate<DocumentStack>(channel, '/stacks/' + encodeURIComponent(stack.id), 'PATCH', { title, expected_version: stack.version, expected_generation: stack.generation }),
  deleteStack: (channel: string, stack: DocumentStack) => mutate<void>(channel, '/stacks/' + encodeURIComponent(stack.id) + '?expected_version=' + stack.version + '&expected_generation=' + encodeURIComponent(stack.generation), 'DELETE'),
  move: (channel: string, documentId: string, stackId: string | null, expectedVersion: number, stackGeneration: string | null) => mutate<DocumentPlacement>(channel, '/documents/' + encodeURIComponent(documentId) + '/placement', 'PUT', { stack_id: stackId, expected_version: expectedVersion, stack_generation: stackGeneration }),
  relate: (channel: string, documentId: string, relatedDocumentId: string, id: string) => mutate<DocumentRelation>(channel, '/relations', 'POST', { id, document_id: documentId, related_document_id: relatedDocumentId }),
  unlink: (channel: string, id: string) => mutate<void>(channel, '/relations/' + encodeURIComponent(id), 'DELETE'),
};

/** Visible native titles only. A proposal creates no relationship or request. */
export function suggestDocumentRelations(document: SharedDocumentRecord, visible: SharedDocumentRecord[], relations: DocumentRelation[], comments: StackDocumentComment[] = []) {
  if (document.archived) return [];
  const words = (title: string) => new Set((title.toLocaleLowerCase().match(/[\p{L}\p{N}]{3,}/gu) || []).filter(word => !['the','and','for','with','och','för','med','ett','det','den'].includes(word)));
  const source = words(document.title || '');
  const sourceComments = comments.filter(row => row.channel_id === document.channel_id && row.document_id === document.id && !row.resolved);
  const commentWords = sourceComments.map(comment => ({ comment, words: words(comment.text) }));
  return visible.filter(row => row.id !== document.id && row.channel_id === document.channel_id && !row.archived && !relations.some(link => [link.first_document_id, link.second_document_id].includes(document.id) && [link.first_document_id, link.second_document_id].includes(row.id)))
    .map(row => { const terms = [...words(row.title || '')].filter(word => source.has(word) || commentWords.some(source => source.words.has(word)));
      return { document: row, terms, commentIds: commentWords.filter(source => terms.some(term => source.words.has(term))).map(source => source.comment.id) }; })
    .filter(row => row.terms.length > 0).sort((a, b) => b.terms.length - a.terms.length || a.document.id.localeCompare(b.document.id)).slice(0, 3);
}
