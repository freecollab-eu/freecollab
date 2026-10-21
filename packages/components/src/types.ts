export interface CollaborationScope { workspaceId: string; channelId: string }
export type TokenReason = 'request' | 'reauthenticate';
export interface TokenContext { readonly scope: Readonly<CollaborationScope>; readonly reason: TokenReason; readonly signal: AbortSignal }
/** Return the CURRENT native user JWT or customer-workspace SSO JWT, or null after logout. Never return an API key. */
export type TokenFactory = (context: TokenContext) => string | null | Promise<string | null>;
export type SessionStatus = 'idle' | 'authenticating' | 'ready' | 'signed-out' | 'denied' | 'destroyed';
export interface SessionState { status: SessionStatus; scope: Readonly<CollaborationScope>; userId?: string }
export interface NavigationEvent { scope: Readonly<CollaborationScope>; entityType: string | null; entityId: string | null }
export interface NotificationEvent { scope: Readonly<CollaborationScope>; notification: CollaborationNotification }
export interface ClientEvents { state: SessionState; error: SDKError; navigate: NavigationEvent; notification: NotificationEvent }
export interface ClientConfig extends CollaborationScope {
  /** API prefix, e.g. https://collaboration.your-company.eu/api or /api. HTTPS is required except loopback. */
  apiBase: string;
  tokenFactory: TokenFactory;
  fetch?: typeof globalThis.fetch;
}
export interface ChannelMetadata { id: string; workspace_id: string | null; secure: boolean; name: string | null }
export interface CollaborationMessage { id: string; channel_id: string; author_id: string; content: string; created_at: string; deleted?: boolean; parent_id?: string | null; edited_at?: string | null; thread_count?: number }
export interface CollaborationFile { id: string; channel_id: string; filename: string; content_type: string; size: number; created_at: string; version_number?: number }
export interface CollaborationPost { id: string; kind: 'post'; channel_id: string; created_by: string; created_at: string; content_mode: 'server'; revision: number; fields: { title: string; body: string; poll: null | unknown } }
export interface CollaborationNotification { id: string; channel_id: string; type: string; title: string; body: string | null; read: boolean; entity_type: string | null; entity_id: string | null; created_at: string }
export interface ModuleOptions { title?: string; pollIntervalMs?: number; locale?: 'en' | 'sv'; labels?: Partial<Labels>; errorMessages?: Readonly<Record<string, string>> }
export interface MountedComponent { refresh(): Promise<void>; destroy(): void }
export interface Labels {
  chat: string; files: string; feed: string; notifications: string; refresh: string;
  loading: string; signedOut: string; denied: string; destroyed: string; empty: string;
  message: string; send: string; post: string; publish: string; file: string; upload: string;
  download: string; markRead: string; open: string; unread: string; read: string;
  nativePrivacy: string; fileLimit: string; genericFailure: string;
  tasks: string; documents: string; meetings: string; calendar: string; workflows: string; decisions: string; search: string;
  title: string; description: string; create: string; save: string; cancel: string; edit: string; remove: string; reply: string;
  replies: string; react: string; bookmark: string; project: string; status: string; todo: string; doing: string; done: string;
  newProject: string; newTask: string; body: string; openItem: string; starts: string; ends: string; location: string;
  startMeeting: string; endMeeting: string; startWorkflow: string; approve: string; reject: string; proposed: string; accepted: string;
  superseded: string; searchRecent: string; searchLimit: string; preview: string; close: string; versions: string;
}
export interface ProjectFields { title: string; description: string; color: 'sage' | 'blue' | 'sand' | 'rose' }
export interface TaskFields { title: string; description: string; status: 'todo' | 'doing' | 'done'; priority: 'low' | 'normal' | 'high'; assignee_user_id: string | null; due_date: string | null; checklist: Array<{id: string; title: string; done: boolean}>; file_ids: string[]; decision_ids: string[] }
export interface ProductRecord<T> { id: string; kind: 'project' | 'task'; channel_id: string; project_id: string | null; created_by: string; created_at: string; revision: number; revised_by: string; revised_at: string; archived: boolean; content_mode: 'server'; fields: T }
export type CollaborationProject = ProductRecord<ProjectFields>;
export type CollaborationTask = ProductRecord<TaskFields>;
export interface DecisionFields { title: string; content: string; status: 'proposed' | 'accepted' | 'superseded'; responsible_user_id: string | null; decision_date: string | null; source_message_id: string | null }
export interface CollaborationDecision extends DecisionFields { id: string; channel_id: string; created_by: string; created_at: string; revision: number; content_mode: 'server' }
export interface CalendarFields { title: string; description: string; location: string; starts_at: string; ends_at: string; all_day: boolean; timezone: string; reminder_minutes: number | null; external_uid: string | null }
export interface CollaborationCalendarEvent { id: string; channel_id: string; kind: 'calendar'; revision: number; deleted: boolean; content_mode: 'server'; fields: CalendarFields; created_by: string }
export interface CollaborationMeeting { id: string; channel_id: string; host_user_id: string; created_at: string; expires_at: string; ended: boolean; max_participants: number; encryption: 'webrtc-dtls-srtp' }
export interface WorkflowDefinition { id: string; workspace_id: string; channel_id: string; revision: number; enabled: boolean; fields: {name: string; trigger: string; steps: Array<{kind: string}>} }
export interface WorkflowRun { id: string; workflow_id: string; workflow_name: string; channel_id: string; status: string; step_index: number; step_count: number; may_vote: boolean; created_at: string; votes: Array<{user_id: string; choice: 'approve' | 'reject'; note: string | null; matrix_event_id: string | null}> }
export interface SharedDocument { id: string; channel_id: string; title?: string; created_by: string; created_at: string; updated_at: string; archived: boolean; version: number; update_count: number; summary_event_id: string | null }
export interface SharedDocumentUpdate { id: string; document_id: string; channel_id: string; sequence: number; content_mode: 'server'; fields: {update_base64: string; title?: string} }
export interface SharedDocumentPage { document: SharedDocument; items: SharedDocumentUpdate[]; has_more: boolean; next_sequence: number }
export interface MessageReaction { emoji: string; count: number; users: string[]; me: boolean }
export interface FileComment { id: string; document_id: string; file_id: string; created_by: string; created_at: string; text: string }
export class SDKError extends Error {
  constructor(readonly code: string, message: string, readonly status?: number) { super(message); this.name = 'FreeCollabSDKError'; }
}
