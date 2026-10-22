import { api } from './api';
import type { CalendarFields } from './calendar';
export interface ExternalCalendarAttendee { email: string; name?: string; optional?: boolean; response?: string; self?: boolean; organizer?: boolean }
export interface ProviderCalendarEvent { id: string; etag: string; fields?: CalendarFields; attendees?: ExternalCalendarAttendee[]; can_respond?: boolean; organizer_self?: boolean; deleted?: boolean }
export interface CalendarPublication { id: string; item_id: string; connection_id: string; connection_revision: number; revision: number; state: string; has_remote_event: boolean; updated_at: string; provider_event: ProviderCalendarEvent | null }
export interface CalendarDeliveryJob { id: string; item_id: string; publication_id: string; action: string; status: string; failure_code: string | null; created_at: string; finished_at: string | null }
export interface CalendarSubscription { id: string; channel_id: string; include_titles: boolean; expires_at: string; revoked_at: string | null; created_at: string; subscription_url?: string }
export interface CalendarDeliveryState { connections: Array<{ id: string; name: string; provider: 'google'|'microsoft'; revision: number; ready: boolean }>; publications: CalendarPublication[]; jobs: CalendarDeliveryJob[]; subscriptions: CalendarSubscription[] }
export type CalendarProviderAction = 'publish'|'cancel'|'respond'|'refresh'|'reconcile';
export interface CalendarReviewBody { connection_id: string; connection_revision: number; event_revision: number; publication_revision: number; action: CalendarProviderAction; attendees: ExternalCalendarAttendee[]; response: 'accepted'|'tentative'|'declined'|null; acknowledge_uncertain: boolean }
export interface CalendarProviderReview extends CalendarReviewBody { review_digest: string; fields: CalendarFields; provider: string; connection_name: string; external_invitations: boolean }
const base = (channel: string) => `/channels/${encodeURIComponent(channel)}/calendar`;
export const calendarDeliveryApi = {
  state: (channel: string) => api.request<CalendarDeliveryState>(base(channel) + '/provider-delivery'),
  preview: (channel: string, event: string, body: CalendarReviewBody) => api.request<CalendarProviderReview>(`${base(channel)}/${encodeURIComponent(event)}/provider/preview`, { method: 'POST', body: JSON.stringify(body) }),
  approve: (channel: string, event: string, body: CalendarReviewBody, digest: string, requestId: string) => api.request<CalendarDeliveryJob>(`${base(channel)}/${encodeURIComponent(event)}/provider/actions`, { method: 'POST', body: JSON.stringify({ ...body, request_id: requestId, review_digest: digest, provider_disclosure: true, send_invitations: ['publish','cancel','respond'].includes(body.action) }) }),
  subscribe: (channel: string, body: { password: string; code?: string; days: number; include_titles: boolean; public_disclosure: boolean }) => api.request<CalendarSubscription>(base(channel) + '/subscriptions', { method: 'POST', body: JSON.stringify(body) }),
  revoke: (channel: string, id: string, body: { password: string; code?: string }) => api.request<CalendarSubscription>(`${base(channel)}/subscriptions/${encodeURIComponent(id)}`, { method: 'DELETE', body: JSON.stringify(body) }),
};
