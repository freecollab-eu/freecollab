import { validateFieldReferences, type FieldReferences } from './field-references';
import { validateResourceReferenceTargets } from './resource-reference-targets';
import { api } from './api';
import { getSecureMessaging } from './secure-client';
import type { SSOReauthentication } from './integrations-api';

export type WorkflowTrigger = 'manual' | 'file_uploaded' | 'document_created' | 'decision_updated' | 'task_updated';
export interface WorkflowStep {
  kind: 'approval' | 'notify' | 'create_task'; recipients: string[]; quorum: number;
  reminder_minutes: number; timeout_minutes: number; max_reminders: number; escalation_recipients: string[];
  project_id: string | null; task_title: string | null;
}
export interface WorkflowFields {
  name: string; trigger: WorkflowTrigger;
  conditions: { actor: 'any' | 'creator'; status: 'todo' | 'doing' | 'done' | 'proposed' | 'accepted' | 'superseded' | null };
  steps: WorkflowStep[];
}
export interface WorkflowDefinition {
  id: string; workspace_id: string; channel_id: string; revision: number; enabled: boolean;
  created_by: string; granted_by: string; created_at: string; updated_at: string; fields: WorkflowFields;
}
export interface WorkflowVote {
  field_references?: FieldReferences;
  user_id: string; step_index: number; choice: 'approve' | 'reject'; created_at: string;
  matrix_event_id: string | null; note: string | null; resolution_error?: string | null;
}
export interface WorkflowRun {
  id: string; workflow_id: string; workflow_name: string; workflow_revision: number; channel_id: string;
  event_kind: WorkflowTrigger; resource_id: string | null; status: string; step_index: number; step_count: number;
  due_at: string; deadline_at: string | null; reminder_count: number; escalated: boolean;
  failure: string | null; created_at: string; finished_at: string | null; may_vote: boolean; votes: WorkflowVote[];
  activity?: { kind: string; step_index: number; actor_id: string | null; created_at: string }[];
}
export interface WorkflowList { enabled: boolean; secure_channel: boolean; can_manage: boolean; definitions: WorkflowDefinition[] }
export interface WorkflowRevision { revision: number; created_by: string; created_at: string; fields: WorkflowFields }
const segment = encodeURIComponent;
const path = (workspaceId: string, channelId: string) => `/workspaces/${segment(workspaceId)}/channels/${segment(channelId)}/workflows`;
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) });

async function resolvedRun(row: WorkflowRun): Promise<WorkflowRun> {
  const token = api.getAccessToken();
  const votes = await Promise.all(row.votes.map(async vote => {
    if (!vote.matrix_event_id) return vote;
    try {
      const secure = await getSecureMessaging(), event = await secure.getDecryptedEvent(row.channel_id, vote.matrix_event_id);
      const { c, roomId } = await secure.roomFor(row.channel_id), content = event.getContent();
      const domain = c.getUserId()!.split(':').slice(1).join(':');
      if (event.getRoomId() !== roomId || event.getType() !== 'eu.freecollab.workflow.vote' || event.isRedacted()
          || event.getSender() !== `@fc_${vote.user_id}:${domain}` || content.run_id !== row.id
          || content.step_index !== vote.step_index || content.choice !== vote.choice
          || typeof content.note !== 'string' || content.note.length > 2000) throw new Error('Encrypted approval note could not be verified.');
      return { ...vote, note: content.note, field_references: validateFieldReferences({ note: content.note }, content.field_references), resolution_error: null };
    } catch (failure) { return { ...vote, note: null, resolution_error: failure instanceof Error ? failure.message : 'Unlock recovery to read this encrypted note.' }; }
  }));
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  return { ...row, votes };
}

export const workflowsApi = {
  definitions: (workspaceId: string, channelId: string) => api.request<WorkflowList>(path(workspaceId, channelId)),
  save: (workspaceId: string, channelId: string, fields: WorkflowFields, enabled: boolean, values: SSOReauthentication, previous?: WorkflowDefinition) =>
    api.request<WorkflowDefinition>(path(workspaceId, channelId) + (previous ? '/' + segment(previous.id) : ''), {
      method: previous ? 'PUT' : 'POST', body: JSON.stringify({ fields, enabled, expected_revision: previous?.revision || 0, ...values }),
    }),
  disable: (workspaceId: string, channelId: string, id: string, values: SSOReauthentication) =>
    api.request<WorkflowDefinition>(path(workspaceId, channelId) + '/' + segment(id) + '/disable', post(values)),
  revisions: (workspaceId: string, channelId: string, id: string) =>
    api.request<{ revisions: WorkflowRevision[] }>(path(workspaceId, channelId) + '/' + segment(id) + '/revisions'),
  async runs(workspaceId: string, channelId: string) {
    const token = api.getAccessToken(), result = await api.request<{ runs: WorkflowRun[] }>(path(workspaceId, channelId) + '/runs/history');
    const runs = await Promise.all(result.runs.map(resolvedRun));
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    return runs;
  },
  async run(workspaceId: string, channelId: string, id: string) {
    return resolvedRun(await api.request<WorkflowRun>(path(workspaceId, channelId) + '/runs/' + segment(id)));
  },
  start: (workspaceId: string, channelId: string, id: string) =>
    api.request<{ id: string; status: string }>(path(workspaceId, channelId) + '/' + segment(id) + '/run', post({})),
  cancel: (workspaceId: string, channelId: string, id: string, values: SSOReauthentication) =>
    api.request<WorkflowRun>(path(workspaceId, channelId) + '/runs/' + segment(id) + '/cancel', post(values)),
  async vote(workspaceId: string, channelId: string, run: WorkflowRun, choice: 'approve' | 'reject', note: string, references?: FieldReferences) {
    const token = api.getAccessToken(), secure = await getSecureMessaging();
    const encrypted = await secure.handles(channelId);
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    const field_references = validateFieldReferences({ note }, references);
    note = field_references?.note?.length ? note : note.trim();
    await validateResourceReferenceTargets(channelId, { note }, field_references, token, encrypted);
    let body: Record<string, unknown> = { choice, ...(note.trim() ? { note, ...(field_references ? { field_references } : {}) } : {}) };
    if (encrypted) {
      let matrix_event_id: string | undefined;
      if (note.trim()) {
        const event = await secure.sendEvent(channelId, 'eu.freecollab.workflow.vote', {
          run_id: run.id, step_index: run.step_index, choice, note, ...(field_references ? { field_references } : {}),
        });
        matrix_event_id = event.event_id;
      }
      body = { choice, ...(matrix_event_id ? { matrix_event_id } : {}) };
    }
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    return resolvedRun(await api.request<WorkflowRun>(path(workspaceId, channelId) + '/runs/' + segment(run.id) + '/vote', post(body)));
  },
};
