import { api } from './api';
import { resolveProjectTaskRecord, type TaskRecord } from './project-tasks-api';
import { resolveCalendarRecord, type CalendarRecord } from './document-calendar-api';

export interface DayChannel { id: string; name: string | null; workspace_id: string; workspace_name: string; secure: boolean }
export interface DayNotification { id: string; channel_id: string; type: string; entity_type: string | null; entity_id: string | null; created_at: string }
export interface DayReminder { id: string; channel_id: string; item_id: string; kind: 'message' | 'matrix'; due_at: string; status: string }
export interface DaySaved { channel_id: string; item_id: string; kind: 'message' | 'matrix'; created_at: string }
export interface MyDaySnapshot { generated_at: string; channels: DayChannel[]; tasks: TaskRecord[]; calendar: CalendarRecord[];
  notifications: DayNotification[]; reminders: DayReminder[]; saved: DaySaved[]; incomplete: boolean; locked?: number }
export interface PersonalSource { workspace_id: string; channel_id: string; kind: 'task' | 'calendar' | 'message' | 'meeting'; item_id?: string; project_id?: string }
export function openPersonalSource(target: PersonalSource) { window.dispatchEvent(new CustomEvent('freecollab:open-personal-source', { detail: target })); }

export async function loadMyDay(signal?: AbortSignal): Promise<MyDaySnapshot> {
  const token = api.getAccessToken(), user = api.getCurrentUserId();
  const current = () => { if (!token || token !== api.getAccessToken() || user !== api.getCurrentUserId() || signal?.aborted) throw new Error('The signed-in account changed.'); };
  const snapshot = await api.request<MyDaySnapshot>('/my-day', { signal }); current();
  // Four concurrent resolutions bound device work, especially for Matrix rooms
  // requiring sync/recovery. A locked revision never becomes a native fallback.
  const rows = [
    ...snapshot.tasks.map(record => async () => { current(); const resolved = await resolveProjectTaskRecord(record); current(); return { task: resolved }; }),
    ...snapshot.calendar.map(record => async () => { current(); const resolved = await resolveCalendarRecord(record); current(); return { calendar: resolved }; }),
  ];
  const tasks: TaskRecord[] = [], calendar: CalendarRecord[] = []; let next = 0, locked = 0;
  await Promise.all(Array.from({ length: Math.min(4, rows.length) }, async () => {
    while (next < rows.length) {
      const row = await rows[next++]();
      if ('task' in row && row.task) { if (!row.task.fields) locked++; else if (row.task.fields.assignee_user_id === user && row.task.fields.status !== 'done' && !row.task.archived) tasks.push(row.task); }
      if ('calendar' in row && row.calendar) { if (!row.calendar.fields) locked++; else if (!row.calendar.deleted) calendar.push(row.calendar); }
    }
  }));
  current();
  tasks.sort((a, b) => (a.fields?.due_date || '9999').localeCompare(b.fields?.due_date || '9999') || a.id.localeCompare(b.id));
  return { ...snapshot, tasks, calendar, locked };
}
