import { api, type Message } from './api';
import { getSecureMessaging } from './secure-client';
import { downloadChannelFile, listChannelFiles, type ManagedFile } from './files-api';
import { sharedDocumentsApi, SharedDocumentSession, type SharedDocumentRecord } from './shared-documents-api';
import { projectTasksApi } from './project-tasks-api';
import { clipsApi, type ChannelClip } from './clips-api';
export interface CopilotAgent { name: string; instructions: string; tools: string[] }
export interface CopilotConversation { id: string; channel_id: string; secure: boolean; revision: number }
export interface CopilotSource { kind: 'message' | 'file' | 'shared_document' | 'clip'; id: string; text?: string; event_ids?: string[]; revision?: number; expected_text_sha256?: string }
export interface CopilotCitation extends CopilotSource { citation: string; fingerprint: string }
export interface CopilotTurn { id: string; prompt: string; text: string; agent: string; ephemeral: boolean; revision?: number; citations: CopilotCitation[]; proposals: { id: string; tool: string; project_id: string; title: string; description: string }[]; tool_results: { tool: string; sources: string[] }[] }
export interface CopilotState { globally_enabled: boolean; configured: boolean; config_revision: number; provider: { endpoint: string; model: string } | null; agents: CopilotAgent[]; consented: boolean; conversations: CopilotConversation[]; role: string }
export interface ContextChoice { key: string; source: CopilotSource; label: string; preview: string; message?: Message; file?: ManagedFile; document?: SharedDocumentRecord; clip?: ChannelClip }
export interface CopilotSourceReference { kind: 'message' | 'shared_document'; id: string }
const path = (workspace: string) => '/workspaces/' + encodeURIComponent(workspace) + '/copilot';
const json = (data: unknown, method = 'POST') => ({ method, body: JSON.stringify(data) });
export const copilotApi = {
  status: (workspace: string) => api.request<CopilotState>(path(workspace)),
  configure: (workspace: string, data: { expected_revision: number; enabled: boolean; endpoint: string; model: string; api_key?: string; agents: CopilotAgent[]; password: string; code?: string }) => api.request(path(workspace) + '/config', json(data, 'PUT')),
  consent: (workspace: string, revision: number, enabled: boolean) => api.request(path(workspace) + '/consent', json({ enabled, config_revision: revision }, 'PUT')),
  create: (workspace: string, channel_id: string) => api.request<CopilotConversation>(path(workspace) + '/conversations', json({ channel_id })),
  history: (workspace: string, conversation: string) => api.request<CopilotConversation & { turns: CopilotTurn[] }>(path(workspace) + '/conversations/' + encodeURIComponent(conversation)),
  citations: (workspace: string, conversation: string, turn: string) => api.request(path(workspace) + '/conversations/' + encodeURIComponent(conversation) + '/turns/' + encodeURIComponent(turn) + '/citations'),
  reset: (workspace: string, conversation: string) => api.request<void>(path(workspace) + '/conversations/' + encodeURIComponent(conversation), { method: 'DELETE' }),
  send: (workspace: string, conversation: string, data: { request_id: string; expected_revision: number; expected_config_revision: number; prompt: string; sources: CopilotSource[]; disclose_to_provider: boolean; disclose_decrypted_content: boolean; target_project_id?: string }) => api.request<CopilotTurn & { revision: number }>(path(workspace) + '/conversations/' + encodeURIComponent(conversation) + '/turns', json(data)),
  approve: (workspace: string, turn: string, proposal: string) => api.request<{ task_id: string }>(path(workspace) + '/turns/' + encodeURIComponent(turn) + '/tools/' + encodeURIComponent(proposal) + '/approve', json({ confirm: true })),
};
export async function copilotChannelIsSecure(channel: string) { return (await getSecureMessaging()).handles(channel); }
export async function copilotTaskDestinations(channel: string) {
  return (await projectTasksApi.projects(channel)).filter(project => !project.archived && project.fields && !project.resolution_error).map(project => ({ id: project.id, name: project.fields!.title }));
}
export async function loadCopilotChoices(channel: string): Promise<{ choices: ContextChoice[]; secure: boolean }> {
  const token = api.getAccessToken(), secureClient = await getSecureMessaging();
  const secure = await secureClient.handles(channel);
  const [messages, files, documents, clips] = await Promise.all([api.getMessages(channel), listChannelFiles(channel), sharedDocumentsApi.list(channel), clipsApi.list(channel)]);
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  return { secure, choices: [
    ...messages.filter(message => !message.deleted && !message.locked).slice(-30).map(message => ({ key: 'message:' + message.id, source: { kind: 'message' as const, id: message.id }, label: message.content.slice(0, 100), preview: message.content, message })),
    ...files.filter(file => file.size <= 12000 && ['text/plain', 'application/json'].includes(file.content_type)).slice(0, 20).map(file => ({ key: 'file:' + file.id, source: { kind: 'file' as const, id: file.id }, label: file.filename, preview: file.filename, file })),
    ...documents.filter(document => !document.archived && !document.resolution_error).slice(0, 20).map(document => ({ key: 'shared_document:' + document.id, source: { kind: 'shared_document' as const, id: document.id }, label: document.title || 'Untitled document', preview: document.title || '', document })),
    ...clips.filter(clip => !clip.resolution_error && clip.fields?.transcript).slice(0, 20).map(clip => ({ key: 'clip:' + clip.id, source: { kind: 'clip' as const, id: clip.id }, label: clip.fields!.title, preview: clip.fields!.transcript!.slice(0, 12000), clip })),
  ] };
}
/** Resolve reviewed inline targets independently of the central picker's display caps. */
export async function loadCopilotReferencedChoices(channel: string, refs: readonly CopilotSourceReference[]): Promise<{ choices: ContextChoice[]; secure: boolean }> {
  if (refs.length > 8 || refs.some(ref => !ref || !['message', 'shared_document'].includes(ref.kind) || typeof ref.id !== 'string' || !ref.id || ref.id.length > 1024)
    || new Set(refs.map(ref => ref.kind + ':' + ref.id)).size !== refs.length) throw new Error('Choose up to eight unique sources.');
  const selected = refs.map(ref => ({ ...ref })), token = api.getAccessToken();
  const current = () => { if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
  const unavailable = () => new Error('This AI source is unavailable.');
  const secureClient = await getSecureMessaging(); current();
  const secure = await secureClient.handles(channel); current();
  const documents = selected.some(ref => ref.kind === 'shared_document') ? await sharedDocumentsApi.list(channel) : [];
  current();
  let encryptedMessages: Message[] = [];
  const messageRefs = selected.filter(ref => ref.kind === 'message');
  if (secure && messageRefs.length) {
    const { roomId } = await secureClient.roomFor(channel); current();
    await Promise.all(messageRefs.map(async ref => {
      const event = await secureClient.getDecryptedEvent(channel, ref.id); current();
      if (event.getId() !== ref.id || event.getRoomId() !== roomId || event.getType() !== 'm.room.message'
        || !event.isEncrypted() || event.isRedacted() || event.isDecryptionFailure()
        || event.getOriginalContent()['m.relates_to']?.rel_type === 'm.replace') throw unavailable();
    }));
    // Reuse the trusted, bounded projection that applies same-sender replacements.
    // Older events outside its canonical window fail closed; never use a raw base body.
    encryptedMessages = await secureClient.getMessages(channel); current();
  }
  const choices = await Promise.all(selected.map(async (ref: CopilotSourceReference): Promise<ContextChoice> => {
    if (ref.kind === 'shared_document') {
      const matches = documents.filter(document => document.id === ref.id);
      const document = matches[0];
      if (matches.length !== 1 || document.channel_id !== channel || document.archived || document.resolution_error) throw unavailable();
      return { key: 'shared_document:' + ref.id, source: { ...ref }, label: document.title || 'Untitled document', preview: document.title || '', document };
    }
    let message: Message;
    if (secure) {
      const matches = encryptedMessages.filter(value => value.id === ref.id);
      if (matches.length !== 1 || !matches[0].encrypted) throw unavailable();
      message = matches[0];
    } else {
      message = await api.request<Message>('/messages/' + encodeURIComponent(ref.id)); current();
    }
    if (message.id !== ref.id || message.channel_id !== channel || message.deleted || message.locked || typeof message.content !== 'string') throw unavailable();
    return { key: 'message:' + ref.id, source: { ...ref }, label: message.content.slice(0, 100), preview: message.content, message };
  }));
  current();
  return { secure, choices };
}
export async function prepareCopilotSource(choice: ContextChoice, channel: string, secure: boolean): Promise<CopilotSource> {
  const token = api.getAccessToken();
  if (choice.clip) {
    if (choice.clip.resolution_error || !choice.clip.fields?.transcript || secure && !choice.clip.matrix_event_id) throw new Error('Select an available clip transcript.');
    return { ...choice.source, ...(secure ? { text: (choice.clip.fields.title + '\n' + choice.clip.fields.transcript).slice(0, 12000), event_ids: [choice.clip.matrix_event_id!] } : {}) };
  }
  if (choice.message) {
    if (secure) return { ...choice.source, text: choice.message.content, event_ids: [choice.message.id] };
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(choice.message.content));
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    return { ...choice.source, expected_text_sha256: Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('') };
  }
  if (choice.file) {
    const blob = await downloadChannelFile(choice.file);
    if (blob.size > 12000) throw new Error('Select a text file of at most 12 KB.');
    const text = new TextDecoder('utf-8', { fatal: true }).decode(await blob.arrayBuffer());
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    return { ...choice.source, ...(secure ? { text, event_ids: [choice.file.id] } : {}) };
  }
  if (!choice.document) throw new Error('Selected context is unavailable.');
  const document = new SharedDocumentSession(channel, choice.document);
  try {
    await document.sync();
    const verified = document.state();
    if (!verified.ready || verified.locked || verified.error) throw new Error('Wait for a verified, available document before selecting AI context.');
    const title = document.doc.getText('title').toString(), body = document.doc.getText('body').toString();
    const text = (title + '\n' + body).slice(0, 12000);
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    if (secure && !document.metadata.summary_event_id) throw new Error('The encrypted document reference is unavailable.');
    return { ...choice.source, text, revision: document.metadata.version, ...(secure ? { event_ids: [document.metadata.summary_event_id!] } : {}) };
  } finally { document.stop(); }
}
