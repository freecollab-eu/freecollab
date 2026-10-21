import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { listChannelFiles } from './files-api';
import { collaborationApi } from './collaboration-api';

export interface ProjectFields { title: string; description: string; color: 'sage' | 'blue' | 'sand' | 'rose' }
export interface ChecklistItem { id: string; title: string; done: boolean }
export interface TaskFields {
  title: string; description: string; status: 'todo' | 'doing' | 'done'; priority: 'low' | 'normal' | 'high';
  assignee_user_id: string | null; due_date: string | null; checklist: ChecklistItem[]; file_ids: string[]; decision_ids: string[];
}
export interface ProjectTaskRecord<T> {
  id: string; kind: 'project' | 'task'; channel_id: string; project_id: string | null;
  created_by: string; created_at: string; revision: number; revised_by: string; revised_at: string;
  archived: boolean; content_mode: 'server' | 'matrix'; matrix_event_id: string | null;
  fields?: T; resolution_error?: string;
}
export type ProjectRecord = ProjectTaskRecord<ProjectFields>;
export type TaskRecord = ProjectTaskRecord<TaskFields>;
const segment = encodeURIComponent;
const base = (channelId: string) => `/channels/${segment(channelId)}/projects`;
const eventType = (kind: 'project' | 'task') => 'eu.freecollab.product.' + kind;
const fail = (): never => { throw new Error('This project or task contains unsupported fields.'); };
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : fail();
function text(value: unknown, max: number, required = false) {
  if (typeof value !== 'string' || value.trim().length > max || required && !value.trim()) return fail();
  return value.trim();
}
function references(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 20 || value.some(item => typeof item !== 'string' || !item || item.length > 128) || new Set(value).size !== value.length) return fail();
  return value as string[];
}
export function validateProject(value: unknown): ProjectFields {
  const v = object(value);
  if (Object.keys(v).some(key => !['title', 'description', 'color'].includes(key)) || !['sage', 'blue', 'sand', 'rose'].includes(String(v.color))) return fail();
  return { title: text(v.title, 160, true), description: text(v.description, 4000), color: v.color as ProjectFields['color'] };
}
export function validateTask(value: unknown): TaskFields {
  const v = object(value);
  if (Object.keys(v).some(key => !['title', 'description', 'status', 'priority', 'assignee_user_id', 'due_date', 'checklist', 'file_ids', 'decision_ids'].includes(key))) return fail();
  if (!['todo', 'doing', 'done'].includes(String(v.status)) || !['low', 'normal', 'high'].includes(String(v.priority))) return fail();
  if (v.assignee_user_id !== null && (typeof v.assignee_user_id !== 'string' || !v.assignee_user_id || v.assignee_user_id.length > 128)) return fail();
  if (v.due_date !== null && (typeof v.due_date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v.due_date) || !Number.isFinite(Date.parse(v.due_date)) || new Date(v.due_date).toISOString().slice(0, 10) !== v.due_date)) return fail();
  if (!Array.isArray(v.checklist) || v.checklist.length > 40) return fail();
  const checklist = v.checklist.map(value => {
    const item = object(value);
    if (Object.keys(item).some(key => !['id', 'title', 'done'].includes(key)) || typeof item.id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(item.id) || typeof item.done !== 'boolean') return fail();
    return { id: item.id, title: text(item.title, 200, true), done: item.done };
  });
  if (new Set(checklist.map(item => item.id)).size !== checklist.length) return fail();
  return { title: text(v.title, 200, true), description: text(v.description, 12000), status: v.status as TaskFields['status'], priority: v.priority as TaskFields['priority'],
    assignee_user_id: v.assignee_user_id as string | null, due_date: v.due_date as string | null, checklist, file_ids: references(v.file_ids), decision_ids: references(v.decision_ids) };
}
const validate = (kind: 'project' | 'task', value: unknown) => kind === 'project' ? validateProject(value) : validateTask(value);
async function resolve<T>(record: ProjectTaskRecord<T>): Promise<ProjectTaskRecord<T>> {
  try {
    if (record.content_mode === 'server') return { ...record, fields: validate(record.kind, record.fields) as T };
    if (!record.matrix_event_id) throw new Error('The encrypted revision reference is missing.');
    const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(record.channel_id, record.matrix_event_id);
    const { c, roomId } = await secure.roomFor(record.channel_id), content = event.getContent();
    const domain = c.getUserId()!.split(':').slice(1).join(':');
    if (event.getRoomId() !== roomId || event.getType() !== eventType(record.kind) || event.isRedacted()
        || event.getSender() !== `@fc_${record.revised_by}:${domain}` || content.id !== record.id
        || content.kind !== record.kind || content.project_id !== record.project_id
        || content.revision !== record.revision || content.archived !== record.archived) throw new Error('This encrypted revision could not be verified.');
    return { ...record, fields: validate(record.kind, content.fields) as T };
  } catch (failure) { return { ...record, fields: undefined, resolution_error: failure instanceof Error ? failure.message : 'Unlock recovery to read this revision.' }; }
}

async function validateLinks(channelId: string, fields: TaskFields) {
  const [members, files, decisions] = await Promise.all([
    fields.assignee_user_id ? api.getChannelMembers(channelId) : Promise.resolve([]),
    fields.file_ids.length ? listChannelFiles(channelId) : Promise.resolve([]),
    fields.decision_ids.length ? collaborationApi.decisions(channelId) : Promise.resolve([]),
  ]);
  if (fields.assignee_user_id && !members.some(member => member.user_id === fields.assignee_user_id)) throw new Error('Choose a current channel member.');
  if (fields.file_ids.some(id => !files.some(file => file.id === id))) throw new Error('A linked file is no longer available in this channel.');
  if (fields.decision_ids.some(id => !decisions.some(decision => decision.id === id))) throw new Error('A linked decision is no longer available in this channel.');
}
async function write<T>(channelId: string, kind: 'project' | 'task', fields: T, previous?: ProjectTaskRecord<T>, options?: { projectId?: string; id?: string; archived?: boolean }) {
  const token = api.getAccessToken();
  const current = () => { if (!token || token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
  const normalized = validate(kind, fields), id = previous?.id || options?.id || crypto.randomUUID();
  const projectId = kind === 'task' ? options?.projectId || previous?.project_id : null;
  if (kind === 'task' && !projectId) throw new Error('Choose a project for this task.');
  if (kind === 'task') await validateLinks(channelId, normalized as TaskFields);
  current();
  const revision = (previous?.revision || 0) + 1, archived = options?.archived ?? previous?.archived ?? false;
  const secure = await getSecureMessaging(); current();
  const encrypted = await secure.handles(channelId); current();
  let payload: Record<string, unknown> = { id, expected_revision: revision - 1, archived, fields: normalized };
  if (encrypted) {
    const event = await secure.sendEvent(channelId, eventType(kind), { id, kind, project_id: projectId, revision, archived, fields: normalized });
    payload = { id, expected_revision: revision - 1, archived, matrix_event_id: event.event_id };
  }
  current();
  const path = base(channelId) + (kind === 'task' ? '/' + segment(projectId!) + '/tasks' : '');
  const result = await resolve<T>(await api.request<ProjectTaskRecord<T>>(path, { method: 'POST', body: JSON.stringify(payload) }));
  current(); return result;
}
export const projectTasksApi = {
  async projects(channelId: string) {
    const token = api.getAccessToken();
    const rows = await api.request<ProjectRecord[]>(base(channelId));
    const result = await Promise.all(rows.map(resolve<ProjectFields>));
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); return result;
  },
  async tasks(channelId: string, projectId: string) {
    const token = api.getAccessToken(); const rows: TaskRecord[] = []; let offset: number | null = 0;
    while (offset !== null && rows.length < 1000) {
      const page: { items: TaskRecord[]; next_offset: number | null } = await api.request(base(channelId) + '/' + segment(projectId) + '/tasks?limit=200&offset=' + offset);
      rows.push(...page.items); offset = page.next_offset;
    }
    const result = await Promise.all(rows.map(resolve<TaskFields>));
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); return result;
  },
  saveProject: (channelId: string, fields: ProjectFields, previous?: ProjectRecord, options?: { id?: string; archived?: boolean }) => write(channelId, 'project', fields, previous, options),
  saveTask: (channelId: string, projectId: string, fields: TaskFields, previous?: TaskRecord, options?: { id?: string; archived?: boolean }) => write(channelId, 'task', fields, previous, { ...options, projectId }),
  async history<T>(channelId: string, kind: 'project' | 'task', projectId: string, taskId?: string) {
    const token = api.getAccessToken();
    const path = base(channelId) + '/' + segment(projectId) + (kind === 'task' ? '/tasks/' + segment(taskId!) : '') + '/revisions';
    const rows = await api.request<ProjectTaskRecord<T>[]>(path), result = await Promise.all(rows.map(resolve<T>));
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.'); return result;
  },
};
