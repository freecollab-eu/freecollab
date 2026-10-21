import { api } from './api';
import { documentCalendarApi, type CalendarRecord } from './document-calendar-api';
import { validateCalendar } from './calendar';

export type ExternalMeetingProvider = 'google_meet' | 'microsoft_teams' | 'zoom';
export interface ExternalMeetingConnection { id: string; workspace_id: string; channel_id: string; provider: ExternalMeetingProvider; name: string; enabled: boolean; status: string; revision: number; tested: boolean; source_connection_id: string | null; config?: Record<string, string>; has_credentials?: boolean }
export interface ExternalMeeting { id: string; request_id: string; channel_id: string; connection_id: string; created_by: string; state: string; revision: number; secure: boolean; calendar_item_id: string | null; calendar_revision: number | null; created_at: string; last_error: string | null; fields?: MeetingFields; provider_fields?: MeetingFields; join_url?: string }
export interface MeetingFields { title: string; starts_at: string; ends_at: string; timezone: string }
export interface ExternalMeetingList { enabled: boolean; connections: ExternalMeetingConnection[]; meetings: ExternalMeeting[] }
export interface CalendarSource { id: string; channel_id: string; provider: 'google' | 'microsoft'; name: string; kind: string; revision: number; has_credentials: boolean }
export interface ProviderDraft { name: string; provider: ExternalMeetingProvider; channel_id: string; config: Record<string, string>; client_secret?: string; source_connection_id?: string; password: string; code?: string; provider_disclosure: boolean; expected_revision?: number }
const segment = encodeURIComponent;
const channelPath = (id: string) => '/channels/' + segment(id) + '/external-meetings';
const connectionsPath = (id: string) => '/workspaces/' + segment(id) + '/external-meetings/connections';
const post = <T>(path: string, body: unknown) => api.request<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const providerNames: Record<ExternalMeetingProvider, string> = { google_meet: 'Google Meet', microsoft_teams: 'Microsoft Teams', zoom: 'Zoom' };

export const externalMeetingsApi = {
  list: (channel: string) => api.request<ExternalMeetingList>(channelPath(channel)),
  connections: (workspace: string) => api.request<ExternalMeetingConnection[]>(connectionsPath(workspace)),
  calendarSources: (workspace: string) => api.request<CalendarSource[]>('/workspaces/' + segment(workspace) + '/sync'),
  configure: (workspace: string, draft: ProviderDraft) => post<ExternalMeetingConnection>(connectionsPath(workspace), draft),
  revise: (workspace: string, connection: string, draft: ProviderDraft) => api.request<ExternalMeetingConnection>(connectionsPath(workspace) + '/' + segment(connection), { method: 'PUT', body: JSON.stringify(draft) }),
  test: (workspace: string, connection: string, password: string, code?: string) => post<ExternalMeetingConnection>(connectionsPath(workspace) + '/' + segment(connection) + '/test', { password, ...(code ? { code } : {}) }),
  disconnect: (workspace: string, connection: string, password: string, code?: string) => post<ExternalMeetingConnection>(connectionsPath(workspace) + '/' + segment(connection) + '/disconnect', { password, ...(code ? { code } : {}) }),
  authorize: (workspace: string, connection: string, password: string, code?: string) => post<{ authorization_url: string; callback_url: string }>(connectionsPath(workspace) + '/' + segment(connection) + '/oauth/start', { password, ...(code ? { code } : {}) }),
  create: (channel: string, connection: string, fields: MeetingFields, requestId: string, connectionRevision: number) => post<ExternalMeeting>(channelPath(channel), { connection_id: connection, connection_revision: connectionRevision, fields, request_id: requestId, provider_disclosure: true }),
  recover: (row: ExternalMeeting) => post<ExternalMeeting>(channelPath(row.channel_id) + '/' + segment(row.id) + '/recover', { expected_revision: row.revision, provider_disclosure: true }),
  cancel: (row: ExternalMeeting) => post<ExternalMeeting>(channelPath(row.channel_id) + '/' + segment(row.id) + '/cancel', { expected_revision: row.revision, expected_calendar_revision: row.calendar_revision, provider_disclosure: true }),
  link: (row: ExternalMeeting, calendar: CalendarRecord) => post<ExternalMeeting>(channelPath(row.channel_id) + '/' + segment(row.id) + '/calendar-link', { expected_revision: row.revision, calendar_item_id: calendar.id, calendar_revision: calendar.revision, matrix_event_id: calendar.matrix_event_id }),
};

export function validateProviderNavigation(provider: ExternalMeetingProvider, value: string, authorization = false): string {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.port && url.port !== '443' || url.hash) throw new Error('The provider returned an unsafe navigation URL.');
  const host = url.hostname;
  const allowed = authorization ? { google_meet: host === 'accounts.google.com', microsoft_teams: host === 'login.microsoftonline.com', zoom: host === 'zoom.us' }[provider]
    : { google_meet: host === 'meet.google.com', microsoft_teams: host === 'teams.microsoft.com' || host === 'teams.live.com', zoom: host === 'zoom.us' || host.endsWith('.zoom.us') }[provider];
  if (!allowed) throw new Error('The provider returned an unsafe navigation URL.');
  return value;
}

export async function attachEncryptedMeetingCalendar(row: ExternalMeeting, check: () => void): Promise<ExternalMeeting> {
  check();
  if (!row.secure || row.state !== 'awaiting_secure_calendar' || !row.join_url) return row;
  const f = row.provider_fields;
  if (!f) throw new Error('Retrieve the provider meeting before saving its encrypted calendar entry.');
  const fields = validateCalendar({ ...f, description: 'Join meeting: ' + row.join_url, location: row.join_url.length <= 1000 ? row.join_url : 'Online meeting', all_day: false, reminder_minutes: null, external_uid: null });
  const identity = 'meeting_' + row.id;
  const previous = (await documentCalendarApi.calendar(row.channel_id)).find(item => item.id === identity); check();
  let saved: CalendarRecord;
  if (previous) {
    if (!previous.fields || previous.deleted || previous.fields.title !== fields.title || previous.fields.starts_at !== fields.starts_at || previous.fields.ends_at !== fields.ends_at
      || previous.fields.location !== fields.location || previous.fields.description !== fields.description) throw new Error('The encrypted calendar item changed. Review it before attaching this meeting.');
    saved = previous;
  } else {
    saved = await documentCalendarApi.saveEvent(row.channel_id, fields, undefined, identity); check();
    if (saved.resolution_error || !saved.fields || saved.content_mode !== 'matrix') throw new Error('Unlock verified encrypted calendar access before attaching this meeting.');
  }
  const result = await externalMeetingsApi.link(row, saved); check();
  return { ...result, join_url: row.join_url, provider_fields: row.provider_fields };
}

export async function removeEncryptedMeetingCalendar(row: ExternalMeeting, check: () => void) {
  check();
  if (!row.secure || row.state !== 'cancelled' || !row.calendar_item_id) return row;
  const previous = (await documentCalendarApi.calendar(row.channel_id)).find(item => item.id === row.calendar_item_id); check();
  if (!previous) {
    const history = await documentCalendarApi.history(row.channel_id, 'calendar', row.calendar_item_id); check();
    const deleted = history[0];
    if (deleted?.deleted && deleted.content_mode === 'matrix' && deleted.matrix_event_id) {
      const linked = await externalMeetingsApi.link(row, deleted as CalendarRecord); check(); return linked;
    }
    return row;
  }
  if (!previous.fields || previous.revision !== row.calendar_revision) throw new Error('The encrypted calendar item changed. Review it before removing it.');
  const removed = await documentCalendarApi.removeEvent(row.channel_id, previous); check();
  const linked = await externalMeetingsApi.link(row, removed); check(); return linked;
}
