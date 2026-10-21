import { api } from './api';
export interface CalendarAttendee { user_id: string; name: string; response: 'needs-action' | 'accepted' | 'tentative' | 'declined'; responded_at: string | null }
export interface CalendarRoster { event_revision: number; organizer_id: string; attendees: CalendarAttendee[] }
export interface CalendarAvailability { scope: 'selected-channel-only'; private_calendars_included: false; busy: Array<{ starts_at: string; ends_at: string }>; slots: Array<{ starts_at: string; ends_at: string }> }
const base = (channel: string) => `/channels/${encodeURIComponent(channel)}/calendar`;
export const calendarPlanningApi = {
  attendees: (channel: string, event: string) => api.request<CalendarRoster>(`${base(channel)}/${encodeURIComponent(event)}/attendees`),
  invite: (channel: string, event: string, revision: number, userIds: string[]) => api.request<CalendarRoster>(`${base(channel)}/${encodeURIComponent(event)}/attendees`, { method: 'PUT', body: JSON.stringify({ expected_revision: revision, user_ids: userIds }) }),
  respond: (channel: string, event: string, revision: number, response: 'accepted' | 'tentative' | 'declined') => api.request<CalendarRoster>(`${base(channel)}/${encodeURIComponent(event)}/response`, { method: 'PUT', body: JSON.stringify({ expected_revision: revision, response }) }),
  availability: (channel: string, startsAt: string, endsAt: string, userIds: string[], durationMinutes: number) => api.request<CalendarAvailability>(`${base(channel)}/availability`, { method: 'POST', body: JSON.stringify({ starts_at: startsAt, ends_at: endsAt, user_ids: userIds, duration_minutes: durationMinutes }) }),
};
