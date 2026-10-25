import { api } from './api';
import { validateCalendar, type CalendarFields } from './calendar';

export interface PersonalCalendarEvent { id: string; workspace_id: string; revision: number; fields: CalendarFields; context_channel_id: string | null; created_at: string; updated_at: string }
const path = (workspace: string) => '/workspaces/' + encodeURIComponent(workspace) + '/personal-calendar';
const current = (owner: string | null) => { if (!owner || owner !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
function validate(row: PersonalCalendarEvent, workspace: string) { if (!row || row.workspace_id !== workspace || !row.id || !Number.isInteger(row.revision)) throw new Error('Private calendar context changed.'); return { ...row, fields: validateCalendar(row.fields) }; }
export const personalCalendarApi = {
  async list(workspace: string) { const owner = api.getAccessToken(), rows = await api.request<PersonalCalendarEvent[]>(path(workspace)); current(owner); return rows.map(row => validate(row, workspace)); },
  async save(workspace: string, fields: CalendarFields, previous?: PersonalCalendarEvent, contextChannelId?: string, draftId: string = crypto.randomUUID()) {
    const owner = api.getAccessToken(), body = { id: previous?.id || draftId, expected_revision: previous?.revision || 0, fields: validateCalendar(fields), context_channel_id: contextChannelId || null };
    const row = await api.request<PersonalCalendarEvent>(path(workspace), { method: 'POST', body: JSON.stringify(body) }); current(owner); return validate(row, workspace);
  },
  async remove(workspace: string, row: PersonalCalendarEvent) { const owner = api.getAccessToken(); await api.request(path(workspace) + '/' + encodeURIComponent(row.id) + '?expected_revision=' + row.revision, { method: 'DELETE' }); current(owner); },
};
