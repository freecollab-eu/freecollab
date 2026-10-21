import { api } from './api';

export const pilotTaskIds = ['first_message', 'file_share', 'invite_team', 'connect_service', 'find_content'] as const;
export type PilotTask = typeof pilotTaskIds[number];
export type PilotOutcome = 'completed' | 'blocked';
export interface PilotTaskGuide { id: PilotTask; title: string; instructions: string }
export interface PilotFeedback { task: PilotTask; outcome: PilotOutcome; rating: number | null; note: string | null; revision: number; created_at: string; updated_at: string }
export interface PilotOwn { workspace_id: string; can_manage: boolean; self_reported: true; tasks: PilotTaskGuide[]; revisions: Record<PilotTask, number>; feedback: PilotFeedback[] }
export interface PilotTaskSummary { task: PilotTask; completed: number; blocked: number; rating_count: number; rating_total: number }
export interface PilotSummary { workspace_id: string; self_reported: true; pilot_verdict: null; distinct_participants: number; feedback_count: number; tasks: PilotTaskSummary[]; notes_included: false }
export interface PilotDraft { outcome: PilotOutcome | ''; rating: string; note: string }
export interface PilotSubmission { expected_revision: number; outcome: PilotOutcome; rating: number | null; note: string | null; consent: true }
const invalid = () => new Error('Workspace feedback returned an invalid or mismatched response.');
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid(); return value as Record<string, unknown>; }
function text(value: unknown, limit: number): string { if (typeof value !== 'string' || value.length > limit) throw invalid(); return value; }
function count(value: unknown): number { if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw invalid(); return value; }
function task(value: unknown): PilotTask { if (!pilotTaskIds.includes(value as PilotTask)) throw invalid(); return value as PilotTask; }
function timestamp(value: unknown): string { const result = text(value, 100); if (!Number.isFinite(Date.parse(result))) throw invalid(); return result; }
function ordered<T>(values: unknown, parse: (value: unknown) => T, id: (value: T) => PilotTask): T[] {
  if (!Array.isArray(values) || values.length !== pilotTaskIds.length) throw invalid();
  const rows = values.map(parse); if (rows.some((row, index) => id(row) !== pilotTaskIds[index])) throw invalid(); return rows;
}
function feedback(value: unknown): PilotFeedback {
  const row = object(value), revision = count(row.revision);
  if (!revision || !['completed', 'blocked'].includes(row.outcome as string)) throw invalid();
  if (row.rating !== null && (typeof row.rating !== 'number' || !Number.isInteger(row.rating) || row.rating < 1 || row.rating > 5)) throw invalid();
  return { task: task(row.task), outcome: row.outcome as PilotOutcome, rating: row.rating as number | null,
    note: row.note === null ? null : text(row.note, 1000), revision, created_at: timestamp(row.created_at), updated_at: timestamp(row.updated_at) };
}
export function parsePilotOwn(value: unknown, workspaceId: string): PilotOwn {
  const root = object(value); if (root.workspace_id !== workspaceId || root.self_reported !== true || typeof root.can_manage !== 'boolean') throw invalid();
  const tasks = ordered(root.tasks, value => { const row = object(value); return { id: task(row.id), title: text(row.title, 200), instructions: text(row.instructions, 3000) }; }, row => row.id);
  const source = object(root.revisions), revisions = Object.fromEntries(pilotTaskIds.map(id => [id, count(source[id])])) as Record<PilotTask, number>;
  if (!Array.isArray(root.feedback) || root.feedback.length > pilotTaskIds.length) throw invalid();
  const records = root.feedback.map(feedback), ids = new Set<PilotTask>();
  for (const row of records) { if (ids.has(row.task) || row.revision !== revisions[row.task]) throw invalid(); ids.add(row.task); }
  return { workspace_id: workspaceId, can_manage: root.can_manage, self_reported: true, tasks, revisions, feedback: records };
}
export function parsePilotSummary(value: unknown, workspaceId: string): PilotSummary {
  const root = object(value); if (root.workspace_id !== workspaceId || root.self_reported !== true || root.pilot_verdict !== null || root.notes_included !== false) throw invalid();
  const participants = count(root.distinct_participants), records = count(root.feedback_count);
  const tasks = ordered(root.tasks, value => {
    const row = object(value), result = { task: task(row.task), completed: count(row.completed), blocked: count(row.blocked), rating_count: count(row.rating_count), rating_total: count(row.rating_total) };
    const reported = result.completed + result.blocked;
    if (reported > participants || result.rating_count > reported || result.rating_total < result.rating_count || result.rating_total > result.rating_count * 5) throw invalid();
    return result;
  }, row => row.task);
  if (tasks.reduce((sum, row) => sum + row.completed + row.blocked, 0) !== records || participants > records || (records > 0 && participants === 0)) throw invalid();
  return { workspace_id: workspaceId, self_reported: true, pilot_verdict: null, distinct_participants: participants, feedback_count: records, tasks, notes_included: false };
}
export function pilotDraft(row?: PilotFeedback): PilotDraft { return { outcome: row?.outcome || '', rating: row?.rating?.toString() || '', note: row?.note || '' }; }
export function pilotSubmission(draft: PilotDraft, revision: number, consent: boolean): PilotSubmission {
  if (consent !== true || !['completed', 'blocked'].includes(draft.outcome)) throw new Error('Choose an outcome and explicitly agree before saving.');
  count(revision); const note = draft.note.trim(); if (note.length > 1000) throw new Error('Keep your note within 1000 characters.');
  const rating = draft.rating === '' ? null : Number(draft.rating);
  if (rating !== null && (!/^[1-5]$/.test(draft.rating) || !Number.isInteger(rating))) throw new Error('Choose a rating from 1 to 5 or leave it empty.');
  return { expected_revision: revision, outcome: draft.outcome as PilotOutcome, rating, note: note || null, consent: true };
}
function identity() { const token = api.getAccessToken(), user = api.getCurrentUserId(); if (!token || !user) throw new Error('Sign in again to continue workspace feedback.'); return () => { if (token !== api.getAccessToken() || user !== api.getCurrentUserId()) throw new Error('The signed-in account changed.'); }; }
const path = (workspaceId: string) => '/workspaces/' + encodeURIComponent(workspaceId) + '/pilot-feedback';
export const pilotFeedbackApi = {
  async get(workspaceId: string, signal?: AbortSignal): Promise<PilotOwn> { const check = identity(); const result = await api.request<unknown>(path(workspaceId), { signal, cache: 'no-store' }); check(); return parsePilotOwn(result, workspaceId); },
  async summary(workspaceId: string, signal?: AbortSignal): Promise<PilotSummary> { const check = identity(); const result = await api.request<unknown>(path(workspaceId) + '/summary', { signal, cache: 'no-store' }); check(); return parsePilotSummary(result, workspaceId); },
  async save(workspaceId: string, selected: PilotTask, draft: PilotDraft, revision: number, consent: boolean, signal?: AbortSignal): Promise<PilotFeedback> {
    const check = identity(), id = task(selected), body = pilotSubmission(draft, revision, consent);
    const result = await api.request<unknown>(path(workspaceId) + '/' + id, { method: 'PUT', body: JSON.stringify(body), signal }); check();
    const row = feedback(result); if (row.task !== id || row.revision !== revision + 1) throw invalid(); return row;
  },
  async remove(workspaceId: string, selected: PilotTask, revision: number, signal?: AbortSignal): Promise<{ task: PilotTask; revision: number; deleted: true }> {
    const check = identity(), id = task(selected); count(revision);
    const result = object(await api.request<unknown>(path(workspaceId) + '/' + id + '?expected_revision=' + revision, { method: 'DELETE', signal })); check();
    if (result.task !== id || result.deleted !== true || result.revision !== revision + 1) throw invalid(); return { task: id, revision: result.revision as number, deleted: true };
  },
};
