import type { Channel } from './api';
import type { ManagedFile } from './files-api';
import type { DocumentOrganization, DocumentStack } from './document-organization-api';
import type { SharedDocumentRecord } from './shared-documents-api';
import type { ProjectRecord, TaskRecord } from './project-tasks-api';

export interface ChannelMaterialScope { channel: Channel; workspaceId: string; actor: string; token: string }
export interface ChannelMaterial {
  scope: ChannelMaterialScope;
  file?: ManagedFile;
  document?: { record: SharedDocumentRecord; excerpt?: string; stack?: DocumentStack; stackDocumentCount: number };
  nextTask?: { record: TaskRecord; project: ProjectRecord };
}
export interface ChannelMaterialReader {
  current: () => boolean;
  native: () => Promise<boolean>;
  files: () => Promise<ManagedFile[]>;
  documents: () => Promise<SharedDocumentRecord[]>;
  organization: () => Promise<DocumentOrganization>;
  projects: () => Promise<ProjectRecord[]>;
  tasks: (project: ProjectRecord) => Promise<TaskRecord[]>;
  readDocument: (document: SharedDocumentRecord) => Promise<{ document: SharedDocumentRecord; text: string }>;
}

/** A native room preview never falls back to plaintext for encrypted material. */
export function allowsChannelMaterial(channel: Channel, workspaceId?: string) {
  return !!workspaceId && (!channel.workspace_id || channel.workspace_id === workspaceId)
    && !channel.secure && !channel.matrix_room_id && !channel.encrypted_channel_key;
}
const time = (value: string) => Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;
const newest = <T,>(rows: T[], date: (row: T) => string) => [...rows].sort((a, b) => time(date(b)) - time(date(a)));
const pictures = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);

/** Read existing product records only; this overview performs no writes. */
export async function readChannelMaterial(scope: ChannelMaterialScope, reader: ChannelMaterialReader): Promise<ChannelMaterial | null> {
  const current = () => reader.current() && allowsChannelMaterial(scope.channel, scope.workspaceId);
  if (!current() || !await reader.native() || !current()) return null;
  const [files, documents, organization, projects] = await Promise.allSettled([
    reader.files(), reader.documents(), reader.organization(), reader.projects(),
  ]);
  if (!current()) return null;
  if (organization.status === 'fulfilled' && !organization.value.supported) return null;
  const channelId = scope.channel.id;
  const material: ChannelMaterial = { scope };
  if (files.status === 'fulfilled') {
    const visible = newest(files.value.filter(row => row.channel_id === channelId && !row.encrypted && !row.secure_locator && !row.secure_room_id), row => row.created_at);
    material.file = visible.find(row => pictures.has(row.content_type)) || visible[0];
  }
  if (documents.status === 'fulfilled') {
    const visible = newest(documents.value.filter(row => row.channel_id === channelId && !row.archived && !row.resolution_error && !!row.title?.trim() && !row.summary_event_id), row => row.updated_at);
    const record = visible[0];
    if (record) {
      const desk = organization.status === 'fulfilled' ? organization.value : undefined;
      const placement = desk?.placements.find(row => row.channel_id === channelId && row.document_id === record.id);
      const stack = desk?.stacks.find(row => row.channel_id === channelId && row.id === placement?.stack_id);
      const visibleIds = new Set(visible.map(row => row.id));
      material.document = { record, stack, stackDocumentCount: stack ? desk!.placements.filter(row => row.channel_id === channelId && row.stack_id === stack.id && visibleIds.has(row.document_id)).length : 0 };
    }
  }
  const boards = projects.status === 'fulfilled'
    ? newest(projects.value.filter(row => row.channel_id === channelId && row.kind === 'project' && !row.archived && row.content_mode === 'server' && !!row.fields && !row.resolution_error), row => row.revised_at).slice(0, 4)
    : [];
  const [body, taskLists] = await Promise.all([
    material.document ? reader.readDocument(material.document.record).catch(() => null) : Promise.resolve(null),
    Promise.allSettled(boards.map(async project => ({ project, tasks: await reader.tasks(project) }))),
  ]);
  if (!current()) return null;
  if (material.document && body?.document.id === material.document.record.id && body.document.channel_id === channelId && !body.document.archived && !body.document.summary_event_id && typeof body.text === 'string') {
    const text = body.text.trim();
    material.document.excerpt = text ? text.slice(0, 360) + (text.length > 360 ? '…' : '') : undefined;
  }
  const tasks = taskLists.flatMap(result => result.status === 'fulfilled'
    ? result.value.tasks.filter(row => row.channel_id === channelId && row.project_id === result.value.project.id && row.kind === 'task' && !row.archived && row.content_mode === 'server' && !!row.fields && !row.resolution_error && row.fields.status !== 'done')
      .map(record => ({ record, project: result.value.project })) : []);
  // A concrete unfinished step from recent channel projects; no generated claim
  // about every project or an invented assignment.
  tasks.sort((a, b) => Number(b.record.fields!.status === 'doing') - Number(a.record.fields!.status === 'doing')
    || Number(b.record.fields!.priority === 'high') - Number(a.record.fields!.priority === 'high')
    || (a.record.fields!.due_date || '9999').localeCompare(b.record.fields!.due_date || '9999')
    || time(a.record.created_at) - time(b.record.created_at));
  material.nextTask = tasks[0];
  return material.file || material.document || material.nextTask ? material : null;
}
