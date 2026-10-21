import { SDKError, type ChannelMetadata, type ClientConfig, type ClientEvents, type CollaborationFile, type CollaborationMessage, type CollaborationNotification, type CollaborationPost, type CollaborationScope, type SessionState, type TokenReason } from './types.js';
import type { CalendarFields, CollaborationCalendarEvent, CollaborationDecision, CollaborationMeeting, CollaborationProject, CollaborationTask, DecisionFields, FileComment, MessageReaction, ProjectFields, SharedDocument, SharedDocumentPage, SharedDocumentUpdate, TaskFields, WorkflowDefinition, WorkflowRun } from './types.js';

const id = (value: string) => { if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) throw new SDKError('invalid-scope', 'Workspace and channel identifiers must be explicit identifiers.'); return value; };
const escaped = encodeURIComponent;
function scopeOf(value: CollaborationScope): Readonly<CollaborationScope> { return Object.freeze({ workspaceId: id(value.workspaceId), channelId: id(value.channelId) }); }
function baseOf(value: string) {
  let url: URL;
  try { url = new URL(value, typeof location === 'undefined' ? undefined : location.href); }
  catch { throw new SDKError('invalid-api-base', 'Provide an absolute API URL outside a browser.'); }
  if (url.username || url.password || url.search || url.hash || !['https:', 'http:'].includes(url.protocol)
      || (url.protocol !== 'https:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) {
    throw new SDKError('invalid-api-base', 'The API URL must use HTTPS, or HTTP on loopback, without credentials or query parameters.');
  }
  return url.href.replace(/\/$/, '');
}
const aborted = () => new DOMException('The component session changed.', 'AbortError');

/** One explicit channel/workspace binding. Tokens and response content stay in memory. */
export class CollaborationClient {
  readonly apiBase: string;
  private scopeValue: Readonly<CollaborationScope>;
  private stateValue: SessionState;
  private token: string | null = null;
  private generation = 0;
  private controller = new AbortController();
  private preparing: Promise<void> | null = null;
  private terminal = false;
  private listeners = new Map<keyof ClientEvents, Set<(value: never) => void>>();
  private knownFiles = new Map<string, CollaborationFile>();
  private knownNotifications = new Set<string>();
  private seenNotifications = new Set<string>();
  private knownResources = new Map<string, Set<string>>();
  private readonly fetcher: typeof globalThis.fetch;
  constructor(private readonly config: ClientConfig) {
    if (typeof config.tokenFactory !== 'function') throw new SDKError('missing-token-factory', 'Provide an asynchronous current-user token factory.');
    this.apiBase = baseOf(config.apiBase); this.scopeValue = scopeOf(config); this.stateValue = { status: 'idle', scope: this.scopeValue };
    this.fetcher = config.fetch || globalThis.fetch.bind(globalThis);
  }
  get scope() { return this.scopeValue; }
  get state(): Readonly<SessionState> { return Object.freeze({ ...this.stateValue }); }
  on<K extends keyof ClientEvents>(event: K, handler: (value: ClientEvents[K]) => void): () => void {
    if (this.terminal) throw new SDKError('destroyed', 'This collaboration client has been destroyed.');
    let group = this.listeners.get(event); if (!group) { group = new Set(); this.listeners.set(event, group); }
    group.add(handler as (value: never) => void); return () => group!.delete(handler as (value: never) => void);
  }
  private emit<K extends keyof ClientEvents>(event: K, value: ClientEvents[K]) {
    for (const handler of this.listeners.get(event) || []) { try { handler(value as never); } catch { /* A host callback cannot interrupt cleanup or authorization. */ } }
  }
  private reset(status: SessionState['status'], userId?: string, preservePreparation = false) {
    this.generation++; this.controller.abort(); this.controller = new AbortController(); if (!preservePreparation) this.preparing = null;
    this.knownFiles.clear(); this.knownNotifications.clear(); this.seenNotifications.clear();
    this.knownResources.clear();
    this.stateValue = { status, scope: this.scopeValue, ...(userId ? { userId } : {}) }; this.emit('state', this.state);
  }
  /** Call immediately when the host logs in, changes user, revokes a grant or logs out. Clears mounted content synchronously. */
  reauthenticate(): Promise<void> {
    if (this.terminal) return Promise.reject(new SDKError('destroyed', 'This collaboration client has been destroyed.'));
    this.token = null; this.reset('authenticating'); return this.authorize('reauthenticate');
  }
  setScope(scope: CollaborationScope): Promise<void> {
    if (this.terminal) return Promise.reject(new SDKError('destroyed', 'This collaboration client has been destroyed.'));
    this.scopeValue = scopeOf(scope); return this.reauthenticate();
  }
  destroy() {
    if (this.terminal) return; this.terminal = true; this.token = null; this.reset('destroyed'); this.controller.abort(); this.listeners.clear();
  }
  private async raw(path: string, token: string, options: RequestInit = {}, additionalSignal?: AbortSignal): Promise<Response> {
    const generation = this.generation, signal = additionalSignal ? AbortSignal.any([this.controller.signal, additionalSignal]) : this.controller.signal;
      const headers = new Headers(options.headers); headers.set('Authorization', 'Bearer ' + token);
      const response = await this.fetcher(this.apiBase + path, { ...options, headers, credentials: 'omit', cache: 'no-store', redirect: 'error', signal });
      if (generation !== this.generation || this.terminal) throw aborted();
      if (!response.ok) {
        void response.body?.cancel().catch(() => undefined);
        const code = response.status === 401 ? 'unauthenticated' : response.status === 403 || response.status === 404 ? 'access-denied' : 'request-failed';
        throw new SDKError(code, response.status === 401 ? 'Your sign-in has ended. Sign in through your application again.' : response.status === 403 || response.status === 404 ? 'This user does not have access to this channel.' : 'The collaboration request could not be completed.', response.status);
      }
      return response;
  }
  private async verifyChannel(credential: string) {
    const channel = await (await this.raw('/channels/' + escaped(this.scopeValue.channelId), credential)).json() as ChannelMetadata;
    if (channel.id !== this.scopeValue.channelId || channel.workspace_id !== this.scopeValue.workspaceId) throw new SDKError('scope-mismatch', 'The channel does not belong to the configured workspace.');
    if (typeof channel.secure !== 'boolean' || channel.secure) throw new SDKError('secure-channel-unsupported', 'This channel requires FreeCollab’s trusted encrypted client and verified devices. These native components cannot decrypt or send encrypted channel content.');
  }
  private authorize(reason: TokenReason = 'request'): Promise<void> {
    if (this.terminal) return Promise.reject(new SDKError('destroyed', 'This collaboration client has been destroyed.'));
    if (this.preparing) return this.preparing;
    const startingGeneration = this.generation;
    let ownedGeneration = startingGeneration;
    const operation = (async () => {
      const current = await this.config.tokenFactory({ scope: this.scopeValue, reason, signal: this.controller.signal });
      if (this.terminal || this.generation !== startingGeneration) throw aborted();
      if (!current) { this.token = null; throw new SDKError('unauthenticated', 'Sign in through your application to use collaboration.'); }
      if (typeof current !== 'string' || current.length > 16384 || /\s/.test(current)) throw new SDKError('invalid-token', 'The token factory must return a current user token.');
      if (current === this.token && this.stateValue.status === 'ready') {
        await this.verifyChannel(current);
        if (this.generation !== ownedGeneration || this.terminal) throw aborted();
        return;
      }
      ownedGeneration = this.generation + 1; this.token = current; this.reset('authenticating', undefined, true); const generation = ownedGeneration;
      if (generation !== this.generation || this.token !== current || this.terminal) throw aborted();
      const principal = await (await this.raw('/auth/me', current)).json() as { id?: unknown; workspace_id?: unknown; session_scope?: unknown };
      if (typeof principal.id !== 'string' || (principal.session_scope === 'workspace' && principal.workspace_id !== this.scopeValue.workspaceId)) throw new SDKError('scope-mismatch', 'This sign-in belongs to another workspace.');
      await this.verifyChannel(current);
      if (generation !== this.generation || this.terminal) throw aborted();
      this.stateValue = { status: 'ready', scope: this.scopeValue, userId: principal.id }; this.emit('state', this.state);
    })().catch(error => {
      if (error?.name === 'AbortError' || this.generation !== ownedGeneration || this.terminal) throw aborted();
      const failure = error instanceof SDKError ? error : new SDKError('authentication-failed', 'The current user could not be authenticated.');
      if (!this.terminal) { this.token = null; this.reset(failure.code === 'unauthenticated' ? 'signed-out' : 'denied'); this.emit('error', failure); }
      throw failure;
    }).finally(() => { if (this.preparing === operation) this.preparing = null; });
    this.preparing = operation; return operation;
  }
  private async request<T, R = T>(path: string, options: RequestInit = {}, signal?: AbortSignal, transform?: (value: T) => R, guard?: () => boolean): Promise<R> {
    const startingGeneration = this.generation, scope = this.scopeValue;
    await this.authorize(); if (signal?.aborted || !this.token) throw aborted();
    if (scope !== this.scopeValue || (options.method && options.method !== 'GET' && startingGeneration !== this.generation) || (guard && !guard())) throw aborted();
    const generation = this.generation;
    try {
      const result = await (await this.raw(path, this.token, options, signal)).json() as T;
      if (generation !== this.generation || this.terminal || signal?.aborted) throw aborted();
      return transform ? transform(result) : result as unknown as R;
    } catch (error) {
      if (error instanceof SDKError && ['unauthenticated', 'access-denied'].includes(error.code)) { this.token = null; this.reset(error.code === 'unauthenticated' ? 'signed-out' : 'denied'); this.emit('error', error); }
      throw error;
    }
  }
  async listMessages(signal?: AbortSignal): Promise<CollaborationMessage[]> {
    return this.request<CollaborationMessage[]>('/messages/channels/' + escaped(this.scopeValue.channelId) + '?limit=50', {}, signal, rows => {
      if (!Array.isArray(rows) || rows.some(row => row.channel_id !== this.scopeValue.channelId)) throw new SDKError('scope-mismatch', 'The server returned content outside the configured channel.');
      this.remember('message', rows);
      return rows.slice(0, 50).filter(row => !row.deleted && !row.parent_id).reverse();
    });
  }
  sendMessage(content: string, signal?: AbortSignal): Promise<CollaborationMessage> {
    if (!content.trim() || content.length > 20000) return Promise.reject(new SDKError('invalid-content', 'Write a message of at most 20,000 characters.'));
    return this.request('/messages/channels/' + escaped(this.scopeValue.channelId), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) }, signal);
  }
  async listPosts(signal?: AbortSignal): Promise<CollaborationPost[]> {
    return this.request<{ items: CollaborationPost[] }, CollaborationPost[]>('/channels/' + escaped(this.scopeValue.channelId) + '/activity-feed?limit=50', {}, signal, response => {
      const rows = response.items;
      if (!Array.isArray(rows) || rows.some(row => row.channel_id !== this.scopeValue.channelId || row.content_mode !== 'server' || !row.fields)) throw new SDKError('scope-mismatch', 'The server returned unsupported feed content.'); return rows.slice(0, 50);
    });
  }
  createPost(content: string, title: string, signal?: AbortSignal): Promise<CollaborationPost> {
    if (!content.trim() || content.length > 20000 || !title.trim() || title.length > 160) return Promise.reject(new SDKError('invalid-content', 'Write a title and a post of at most 20,000 characters.'));
    return this.request('/channels/' + escaped(this.scopeValue.channelId) + '/activity-feed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: crypto.randomUUID().replace(/-/g, ''), expected_revision: 0, archived: false, fields: { title, body: content, poll: null } }) }, signal);
  }
  async listFiles(signal?: AbortSignal): Promise<CollaborationFile[]> {
    return this.request<CollaborationFile[]>('/files/channels/' + escaped(this.scopeValue.channelId), {}, signal, rows => {
      if (!Array.isArray(rows) || rows.some(row => row.channel_id !== this.scopeValue.channelId)) throw new SDKError('scope-mismatch', 'The server returned files outside the configured channel.');
      const bounded = rows.slice(0, 100); this.knownFiles = new Map(bounded.map(row => [row.id, row])); return bounded;
    });
  }
  uploadFile(file: File, signal?: AbortSignal): Promise<CollaborationFile> {
    if (file.size > 50 * 1024 * 1024) return Promise.reject(new SDKError('file-too-large', 'The file limit is 50 MB.'));
    const body = new FormData(); body.set('file', file); return this.request('/files/channels/' + escaped(this.scopeValue.channelId), { method: 'POST', body }, signal);
  }
  async downloadFile(fileId: string, signal?: AbortSignal): Promise<{ blob: Blob; file: CollaborationFile }> {
    const file = this.knownFiles.get(fileId); if (!file) throw new SDKError('scope-mismatch', 'Refresh this channel’s files before downloading.');
    await this.authorize(); if (!this.token || !this.knownFiles.has(fileId)) throw aborted();
    const generation = this.generation;
    try {
      const response = await this.raw('/files/' + escaped(fileId), this.token, {}, signal), blob = await response.blob();
      if (generation !== this.generation || this.terminal || signal?.aborted) throw aborted(); return { blob, file };
    } catch (error) {
      if (error instanceof SDKError && ['unauthenticated', 'access-denied'].includes(error.code)) { this.token = null; this.reset(error.code === 'unauthenticated' ? 'signed-out' : 'denied'); this.emit('error', error); }
      throw error;
    }
  }
  async listNotifications(signal?: AbortSignal): Promise<CollaborationNotification[]> {
    return this.request<CollaborationNotification[]>('/notifications?channel_id=' + escaped(this.scopeValue.channelId) + '&limit=50', {}, signal, rows => {
      if (!Array.isArray(rows) || rows.some(row => row.channel_id !== this.scopeValue.channelId)) throw new SDKError('scope-mismatch', 'Channel-scoped notifications require a compatible FreeCollab server.');
      this.knownNotifications = new Set(rows.map(row => row.id));
      for (const row of rows.slice().reverse()) if (!this.seenNotifications.has(row.id)) { this.seenNotifications.add(row.id); this.emit('notification', { scope: this.scopeValue, notification: row }); }
      while (this.seenNotifications.size > 200) this.seenNotifications.delete(this.seenNotifications.values().next().value!);
      return rows.slice(0, 50);
    });
  }
  markNotificationRead(notificationId: string, signal?: AbortSignal): Promise<unknown> {
    if (!this.knownNotifications.has(notificationId)) return Promise.reject(new SDKError('scope-mismatch', 'Only this channel’s displayed notifications can be marked read.'));
    return this.request('/notifications/' + escaped(notificationId) + '/read', { method: 'POST' }, signal, undefined, () => this.knownNotifications.has(notificationId));
  }
  navigate(notification: CollaborationNotification) {
    if (!this.knownNotifications.has(notification.id) || notification.channel_id !== this.scopeValue.channelId || this.stateValue.status !== 'ready') return;
    this.emit('navigate', { scope: this.scopeValue, entityType: notification.entity_type, entityId: notification.entity_id });
  }
  private remember(kind: string, rows: Array<{id: string}>) {
    const values = new Set(kind === 'message' || kind === 'task' ? this.knownResources.get(kind) : undefined);
    if (kind !== 'message' && kind !== 'task') values.clear();
    for (const row of rows) values.add(row.id); while (values.size > 1000) values.delete(values.values().next().value!); this.knownResources.set(kind, values);
  }
  private guard(kind: string, identifier: string) {
    if (!this.knownResources.get(kind)?.has(identifier)) throw new SDKError('scope-mismatch', 'Refresh this channel’s resources before using this item.');
    return () => !!this.knownResources.get(kind)?.has(identifier);
  }
  private channelPath(suffix: string) { return '/channels/' + escaped(this.scopeValue.channelId) + suffix; }
  private workflowPath(suffix = '') { return '/workspaces/' + escaped(this.scopeValue.workspaceId) + this.channelPath('/workflows') + suffix; }
  private nativeRows<T extends {id: string; channel_id: string; content_mode?: string}>(rows: T[], kind: string): T[] {
    if (!Array.isArray(rows) || rows.some(row => row.channel_id !== this.scopeValue.channelId || (row.content_mode !== undefined && row.content_mode !== 'server'))) throw new SDKError('scope-mismatch', 'The server returned resources outside this native channel.');
    this.remember(kind, rows); return rows;
  }
  private json(method: string, body: unknown): RequestInit { return { method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) }; }
  /** Emits identifiers for the host router; never follows a server-supplied URL. */
  openResource(entityType: 'document' | 'meeting' | 'workflow' | 'task' | 'decision' | 'file' | 'message', entityId: string) {
    if (this.stateValue.status !== 'ready') return;
    if (entityType === 'file' ? !this.knownFiles.has(entityId) : !this.knownResources.get(entityType)?.has(entityId)) return;
    this.emit('navigate', {scope: this.scopeValue, entityType, entityId});
  }
  async listProjects(signal?: AbortSignal) {
    return this.request<CollaborationProject[]>(this.channelPath('/projects'), {}, signal, rows => this.nativeRows(rows, 'project'));
  }
  saveProject(fields: ProjectFields, previous?: CollaborationProject, signal?: AbortSignal) {
    const guard = previous ? this.guard('project', previous.id) : undefined;
    return this.request<CollaborationProject>(this.channelPath('/projects'), this.json('POST', {id: previous?.id || crypto.randomUUID(), expected_revision: previous?.revision || 0, archived: previous?.archived || false, fields}), signal, undefined, guard);
  }
  async listTasks(projectId: string, signal?: AbortSignal) {
    const guard = this.guard('project', projectId);
    const scope = this.scopeValue, generation = this.generation, rows: CollaborationTask[] = []; let offset: number | null = 0;
    while (offset !== null && rows.length < 1000) {
      const page: {items: CollaborationTask[]; next_offset: number | null} = await this.request(this.channelPath('/projects/' + escaped(projectId) + '/tasks?limit=200&offset=' + offset), {}, signal, undefined, guard);
      if (scope !== this.scopeValue || generation !== this.generation || signal?.aborted) throw aborted();
      if (!Array.isArray(page.items) || page.items.some(row => row.project_id !== projectId || row.channel_id !== scope.channelId || row.content_mode !== 'server')) throw new SDKError('scope-mismatch', 'The task belongs to a different native project.');
      if (page.next_offset !== null && (!Number.isInteger(page.next_offset) || page.next_offset <= offset)) throw new SDKError('invalid-pagination', 'Invalid task history cursor.');
      rows.push(...page.items); offset = page.next_offset;
    }
    this.remember('task:' + projectId, rows); this.remember('task', rows); return rows;
  }
  saveTask(projectId: string, fields: TaskFields, previous?: CollaborationTask, signal?: AbortSignal) {
    const projectGuard = this.guard('project', projectId), itemGuard = previous ? this.guard('task:' + projectId, previous.id) : () => true;
    return this.request<CollaborationTask>(this.channelPath('/projects/' + escaped(projectId) + '/tasks'), this.json('POST', {id: previous?.id || crypto.randomUUID(), expected_revision: previous?.revision || 0, archived: previous?.archived || false, fields}), signal, undefined, () => projectGuard() && itemGuard());
  }
  listDecisions(signal?: AbortSignal) { return this.request<CollaborationDecision[]>(this.channelPath('/decisions'), {}, signal, rows => this.nativeRows(rows, 'decision')); }
  saveDecision(fields: DecisionFields, previous?: CollaborationDecision, signal?: AbortSignal) {
    const guard = previous ? this.guard('decision', previous.id) : undefined;
    return this.request<CollaborationDecision>(previous ? '/decisions/' + escaped(previous.id) + '/revisions' : this.channelPath('/decisions'), this.json('POST', {...fields, ...(previous ? {expected_revision: previous.revision} : {})}), signal, undefined, guard);
  }
  listCalendar(signal?: AbortSignal) { return this.request<CollaborationCalendarEvent[]>(this.channelPath('/integrations/calendar'), {}, signal, rows => this.nativeRows(rows, 'calendar')); }
  saveCalendar(fields: CalendarFields, previous?: CollaborationCalendarEvent, signal?: AbortSignal) {
    const guard = previous ? this.guard('calendar', previous.id) : undefined;
    return this.request<CollaborationCalendarEvent>(this.channelPath('/integrations/calendar'), this.json('POST', {id: previous?.id || crypto.randomUUID(), expected_revision: previous?.revision || 0, fields}), signal, undefined, guard);
  }
  removeCalendar(previous: CollaborationCalendarEvent, signal?: AbortSignal) {
    const guard = this.guard('calendar', previous.id);
    return this.request(this.channelPath('/integrations/calendar'), this.json('POST', {id: previous.id, expected_revision: previous.revision, deleted: true}), signal, undefined, guard);
  }
  listMeetings(signal?: AbortSignal) { return this.request<CollaborationMeeting[]>(this.channelPath('/meetings'), {}, signal, rows => this.nativeRows(rows, 'meeting')); }
  /** Creates the real meeting; media is joined through the trusted full client. No media token is exposed to DOM attributes. */
  async createMeeting(signal?: AbortSignal) {
    const scope = this.scopeValue, generation = this.generation;
    const result = await this.request<{meeting: CollaborationMeeting}>(this.channelPath('/meetings'), this.json('POST', {}), signal);
    if (scope !== this.scopeValue || generation !== this.generation || signal?.aborted) throw aborted();
    if (!result.meeting || result.meeting.channel_id !== this.scopeValue.channelId) throw new SDKError('scope-mismatch', 'The meeting belongs to a different channel.');
    await this.listMeetings(signal); if (scope !== this.scopeValue || generation !== this.generation || signal?.aborted) throw aborted(); return result.meeting;
  }
  endMeeting(meetingId: string, signal?: AbortSignal) { const guard = this.guard('meeting', meetingId); return this.request('/meetings/' + escaped(meetingId) + '/end', this.json('POST', {}), signal, undefined, guard); }
  listWorkflowDefinitions(signal?: AbortSignal) {
    return this.request<{enabled: boolean; secure_channel: boolean; can_manage: boolean; definitions: WorkflowDefinition[]}>(this.workflowPath(), {}, signal, result => {
      if (result.secure_channel || result.definitions.some(row => row.workspace_id !== this.scopeValue.workspaceId || row.channel_id !== this.scopeValue.channelId)) throw new SDKError('scope-mismatch', 'Workflow scope does not match the component.');
      this.remember('workflow', result.definitions); return result;
    });
  }
  listWorkflowRuns(signal?: AbortSignal) { return this.request<{runs: WorkflowRun[]}, WorkflowRun[]>(this.workflowPath('/runs/history'), {}, signal, page => this.nativeRows(page.runs, 'workflow-run')); }
  startWorkflow(workflowId: string, signal?: AbortSignal) { const guard = this.guard('workflow', workflowId); return this.request<{id: string; status: string}>(this.workflowPath('/' + escaped(workflowId) + '/run'), this.json('POST', {}), signal, undefined, guard); }
  voteWorkflow(runId: string, choice: 'approve' | 'reject', note = '', signal?: AbortSignal) {
    const guard = this.guard('workflow-run', runId); return this.request<WorkflowRun>(this.workflowPath('/runs/' + escaped(runId) + '/vote'), this.json('POST', {choice, ...(note.trim() ? {note: note.trim()} : {})}), signal, undefined, guard);
  }
  listDocuments(signal?: AbortSignal) { return this.request<SharedDocument[]>(this.channelPath('/shared-documents'), {}, signal, rows => {
    if (rows.some(row => row.summary_event_id)) throw new SDKError('scope-mismatch', 'This document requires encrypted-client verification.'); return this.nativeRows(rows, 'document');
  }); }
  createDocument(title: string, updateBase64: string, signal?: AbortSignal) { return this.request<SharedDocument>(this.channelPath('/shared-documents'), this.json('POST', {id: crypto.randomUUID(), update_id: crypto.randomUUID(), title, update_base64: updateBase64}), signal); }
  documentUpdates(documentId: string, afterSequence = 0, signal?: AbortSignal) {
    const guard = this.guard('document', documentId);
    return this.request<SharedDocumentPage>(this.channelPath('/shared-documents/' + escaped(documentId) + '/updates?after_sequence=' + afterSequence + '&limit=200'), {}, signal, page => {
      if (page.document.id !== documentId || page.document.channel_id !== this.scopeValue.channelId || page.items.some(row => row.document_id !== documentId || row.channel_id !== this.scopeValue.channelId || row.content_mode !== 'server')) throw new SDKError('scope-mismatch', 'The document history does not match this channel.'); return page;
    }, guard);
  }
  appendDocument(documentId: string, title: string, updateBase64: string, signal?: AbortSignal, updateId: string = crypto.randomUUID()) {
    const guard = this.guard('document', documentId); return this.request<SharedDocumentUpdate>(this.channelPath('/shared-documents/' + escaped(documentId) + '/updates'), this.json('POST', {update_id: updateId, title, update_base64: updateBase64}), signal, undefined, guard);
  }
  async listThread(messageId: string, signal?: AbortSignal) {
    const guard = this.guard('message', messageId); return this.request<{parent_id: string; replies: CollaborationMessage[]}, CollaborationMessage[]>('/threads/messages/' + escaped(messageId), {}, signal, page => {
      if (page.parent_id !== messageId || page.replies.some(row => row.channel_id !== this.scopeValue.channelId || row.parent_id !== messageId)) throw new SDKError('scope-mismatch', 'The replies belong to a different conversation.'); return page.replies;
    }, guard);
  }
  replyMessage(messageId: string, content: string, signal?: AbortSignal) { const guard = this.guard('message', messageId); return this.request<CollaborationMessage>('/threads/messages/' + escaped(messageId) + '/reply', this.json('POST', {content}), signal, undefined, guard); }
  editMessage(messageId: string, content: string, signal?: AbortSignal) { const guard = this.guard('message', messageId); return this.request('/messages/' + escaped(messageId), this.json('PUT', {content}), signal, undefined, guard); }
  deleteMessage(messageId: string, signal?: AbortSignal) { const guard = this.guard('message', messageId); return this.request('/messages/' + escaped(messageId), {method: 'DELETE'}, signal, undefined, guard); }
  listReactions(messageId: string, signal?: AbortSignal) { const guard = this.guard('message', messageId); return this.request<MessageReaction[]>('/reactions/messages/' + escaped(messageId), {}, signal, undefined, guard); }
  reactMessage(messageId: string, emoji: string, remove = false, signal?: AbortSignal) { const guard = this.guard('message', messageId); return this.request('/reactions/messages/' + escaped(messageId) + (remove ? '/' + escaped(emoji) : ''), remove ? {method: 'DELETE'} : this.json('POST', {emoji}), signal, undefined, guard); }
  bookmarkMessage(messageId: string, signal?: AbortSignal) { const guard = this.guard('message', messageId); return this.request('/bookmarks', this.json('POST', {channel_id: this.scopeValue.channelId, message_id: messageId}), signal, undefined, guard); }
  /** Search is local over at most 500 recent messages; no search phrase leaves the host. */
  async searchMessages(query: string, signal?: AbortSignal): Promise<CollaborationMessage[]> {
    const phrase = query.trim().toLocaleLowerCase(); if (!phrase || phrase.length > 200) return [];
    const scope = this.scopeValue; let generation: number | undefined;
    const rows: CollaborationMessage[] = []; let before = '';
    for (let page = 0; page < 5; page++) {
      const next = await this.request<CollaborationMessage[]>('/messages/channels/' + escaped(this.scopeValue.channelId) + '?limit=100' + (before ? '&before=' + escaped(before) : ''), {}, signal, values => {
        if (!Array.isArray(values) || values.some(row => row.channel_id !== this.scopeValue.channelId)) throw new SDKError('scope-mismatch', 'Search history belongs to a different channel.'); return values;
      });
      if (scope !== this.scopeValue || (generation !== undefined && generation !== this.generation) || signal?.aborted) throw aborted(); generation = this.generation;
      rows.push(...next); if (next.length < 100) break; before = next[next.length - 1].id;
    }
    const matched = rows.filter(row => !row.deleted && row.content.toLocaleLowerCase().includes(phrase)); this.remember('message', matched); return matched;
  }
  fileComments(fileId: string, signal?: AbortSignal) { if (!this.knownFiles.has(fileId)) return Promise.reject(new SDKError('scope-mismatch', 'Refresh this channel’s files first.')); return this.request<FileComment[]>(this.channelPath('/file-experience/' + escaped(fileId) + '/comments'), {}, signal, undefined, () => this.knownFiles.has(fileId)); }
  fileVersions(fileId: string, signal?: AbortSignal) { if (!this.knownFiles.has(fileId)) return Promise.reject(new SDKError('scope-mismatch', 'Refresh this channel’s files first.')); return this.request<CollaborationFile[]>('/files/' + escaped(fileId) + '/versions', {}, signal, rows => {
    if (!Array.isArray(rows) || rows.some(row => row.channel_id !== this.scopeValue.channelId)) throw new SDKError('scope-mismatch', 'The file versions belong to a different channel.'); return rows;
  }, () => this.knownFiles.has(fileId)); }
  commentFile(fileId: string, text: string, signal?: AbortSignal) { if (!this.knownFiles.has(fileId)) return Promise.reject(new SDKError('scope-mismatch', 'Refresh this channel’s files first.')); return this.request<FileComment>(this.channelPath('/file-experience/' + escaped(fileId) + '/comments'), this.json('POST', {id: crypto.randomUUID(), text}), signal, undefined, () => this.knownFiles.has(fileId)); }
}
export const createClient = (config: ClientConfig) => new CollaborationClient(config);
