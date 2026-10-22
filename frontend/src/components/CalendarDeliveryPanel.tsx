import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Link2, RefreshCw, Send } from 'lucide-react';
import { api } from '../lib/api';
import { calendarDeliveryApi, type CalendarDeliveryState, type CalendarProviderAction, type CalendarProviderReview, type CalendarReviewBody } from '../lib/calendar-delivery-api';
import type { CalendarRecord } from '../lib/document-calendar-api';
import { currentLocale, t, useI18n } from '../i18n';
import '../styles/calendar-delivery.css';
const message = (error: unknown) => error instanceof Error ? error.message : t('The calendar action could not be completed.');
const failure = (code: string | null) => code === 'provider_conflict' ? t('The provider event changed. Read its latest status and review again.') : code === 'combined_series_edit_requires_separate_review' ? t('Change the series schedule and its occurrence exceptions in separate reviewed actions.') : code === 'cancelled_occurrence_requires_new_series' ? t('This provider cannot restore that cancelled occurrence. Create a new series.') : code ? t('The action stopped. Review current access, event versions and the provider outcome before retrying.') : '';
const receiptAction = (action: string) => { const labels: Record<string,string> = {publish:t('Publish or update and send invitations'),refresh:t('Read provider status and responses'),respond:t('Respond as my provider account'),cancel:t('Cancel the provider event and notify attendees'),reconcile:t('Find an uncertain provider event without resending')}; return labels[action] || action; };

export function CalendarDeliveryPanel({ channelId, rows }: { channelId: string; rows: CalendarRecord[] }) {
  useI18n();
  const [open, setOpen] = useState(false), [state, setState] = useState<CalendarDeliveryState | null>(null), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [eventId, setEventId] = useState(''), [connectionId, setConnectionId] = useState(''), [addresses, setAddresses] = useState(''), [action, setAction] = useState<CalendarProviderAction>('publish'), [response, setResponse] = useState<'accepted'|'tentative'|'declined'>('accepted');
  const [review, setReview] = useState<{ body: CalendarReviewBody; result: CalendarProviderReview; requestId: string; event: string } | null>(null), [approved, setApproved] = useState(false), [uncertain, setUncertain] = useState(false);
  const [password, setPassword] = useState(''), [code, setCode] = useState(''), [days, setDays] = useState(30), [titles, setTitles] = useState(false), [publicConsent, setPublicConsent] = useState(false), [subscriptionUrl, setSubscriptionUrl] = useState('');
  const scope = useRef(0), token = useRef(api.getAccessToken());
  const current = (version: number) => version === scope.current && token.current === api.getAccessToken();
  const selected = rows.find(row => row.id === eventId), connection = state?.connections.find(row => row.id === connectionId);
  const publication = state?.publications.find(row => row.item_id === eventId && row.connection_id === connectionId);
  const invalidate = () => { setReview(null); setApproved(false); };
  useEffect(() => { const version = ++scope.current; token.current = api.getAccessToken(); setState(null); setOpen(false); setBusy(''); setError(''); setNotice(''); setReview(null); setSubscriptionUrl(''); setPassword(''); setCode(''); setEventId(''); setConnectionId(''); setAddresses(''); setUncertain(false); setTitles(false); setPublicConsent(false); setApproved(false); const revoke = () => { scope.current++; token.current = ''; setState(null); setOpen(false); setBusy(''); setReview(null); setSubscriptionUrl(''); setPassword(''); setCode(''); setAddresses(''); setUncertain(false); setPublicConsent(false); setApproved(false); }; window.addEventListener('freecollab:auth-changed', revoke); return () => { if (scope.current === version) scope.current++; window.removeEventListener('freecollab:auth-changed', revoke); }; }, [channelId]);
  useEffect(() => { invalidate(); }, [rows.map(row => row.id + ':' + row.revision).join('|')]);
  async function load(version = scope.current) {
    if (!current(version)) return;
    const next = await calendarDeliveryApi.state(channelId);
    if (!current(version)) return;
    setState(next); setConnectionId(previous => next.connections.some(row => row.id === previous) ? previous : next.connections.find(row => row.ready)?.id || next.connections[0]?.id || '');
    setEventId(previous => rows.some(row => row.id === previous && row.content_mode === 'server') ? previous : rows.find(row => row.content_mode === 'server' && row.fields)?.id || '');
  }
  useEffect(() => {
    if (!open || !state?.jobs.some(job => ['queued','running'].includes(job.status))) return;
    const version = scope.current, timer = window.setTimeout(() => { void load(version).catch(failure => { if (current(version)) setError(message(failure)); }); }, 2000);
    return () => window.clearTimeout(timer);
  }, [open, state]);
  async function run(kind: string, operation: (version: number) => Promise<void>) {
    if (busy || token.current !== api.getAccessToken()) return;
    const version = scope.current; setBusy(kind); setError(''); setNotice('');
    try { await operation(version); } catch (failure) { if (current(version)) setError(message(failure)); }
    finally { if (current(version)) setBusy(''); }
  }
  async function toggle() { if (open) { setOpen(false); return; } await run('load', async version => { await load(version); if (current(version)) setOpen(true); }); }
  async function preview() {
    if (!selected || !connection) return;
    const body: CalendarReviewBody = { connection_id: connection.id, connection_revision: connection.revision, event_revision: selected.revision, publication_revision: publication?.revision || 0, action, response: action === 'respond' ? response : null, attendees: action === 'publish' ? addresses.split(/\r?\n/).map(email => email.trim()).filter(Boolean).map(email => ({ email })) : [], acknowledge_uncertain: uncertain };
    await run('preview', async version => { const result = await calendarDeliveryApi.preview(channelId, selected.id, body); if (current(version)) { setReview({ body, result, requestId: crypto.randomUUID(), event: selected.id }); setApproved(false); } });
  }
  async function send() {
    if (!review || review.result.external_invitations && !approved) return;
    const pending = review;
    await run('send', async version => { await calendarDeliveryApi.approve(channelId, pending.event, pending.body, pending.result.review_digest, pending.requestId); if (current(version)) { setReview(null); setApproved(false); setNotice(t('Calendar action queued. The receipt shows completion separately.')); await load(version); } });
  }
  async function createSubscription() {
    await run('feed', async version => { const result = await calendarDeliveryApi.subscribe(channelId, { password, ...(code ? { code } : {}), days, include_titles: titles, public_disclosure: publicConsent }); if (current(version)) { setSubscriptionUrl(result.subscription_url || ''); setPassword(''); setCode(''); setPublicConsent(false); await load(version); } });
  }
  return <section className="fc-calendar-delivery" aria-label={t('External calendar delivery and subscriptions')}>
    <button type="button" className="fc-button" disabled={!!busy} onClick={() => void toggle()}><ExternalLink size={15} />{t('External calendars & subscription links')}</button>
    {error && <p role="alert" className="fc-integration-content-error">{error}</p>}{notice && <p role="status" className="fc-integration-content-notice">{notice}</p>}
    {open && state && <div className="fc-calendar-delivery-content">
      <div className="fc-integration-editor-heading"><h4>{t('Send deliberately. Keep control.')}</h4><button type="button" className="fc-icon-button" aria-label={t('Refresh calendar delivery status')} disabled={!!busy} onClick={() => void run('load', load)}><RefreshCw size={15} /></button></div>
      <p className="fc-integration-content-caption">{t('Native schedules only. Your own Google or Microsoft calendar grant sends exactly the reviewed event and attendee list. Background sync sends no invitations. Encrypted schedules stay on your device.')}</p>
      {state.connections.length ? <div className="fc-calendar-provider-form"><label>{t('Provider calendar connection')}<select aria-label={t('Provider calendar connection')} className="fc-input" value={connectionId} disabled={!!busy} onChange={event => { setConnectionId(event.target.value); invalidate(); }}>{state.connections.map(row => <option value={row.id} key={row.id}>{row.name}{row.ready ? '' : ' · ' + t('Connect and test first')}</option>)}</select></label>
      <label>{t('Event to review')}<select aria-label={t('Event to review')} className="fc-input" value={eventId} disabled={!!busy} onChange={event => { setEventId(event.target.value); invalidate(); }}><option value="">{t('Choose an event')}</option>{rows.filter(row => row.content_mode === 'server' && row.fields).map(row => <option key={row.id} value={row.id}>{row.fields!.title}</option>)}</select></label>
      <label>{t('Provider action')}<select aria-label={t('Provider action')} className="fc-input" value={action} disabled={!!busy} onChange={event => { setAction(event.target.value as CalendarProviderAction); invalidate(); }}><option value="publish">{t('Publish or update and send invitations')}</option><option value="refresh">{t('Read provider status and responses')}</option><option value="respond">{t('Respond as my provider account')}</option><option value="cancel">{t('Cancel the provider event and notify attendees')}</option><option value="reconcile">{t('Find an uncertain provider event without resending')}</option></select></label>
      {action === 'publish' && <label>{t('External attendee emails, one per line')}<textarea aria-label={t('External attendee emails, one per line')} className="fc-input" rows={3} maxLength={13000} value={addresses} disabled={!!busy} onChange={event => { setAddresses(event.target.value); invalidate(); }} /><small>{t('This replaces the provider attendee list. An empty list removes all attendees. Removed people may receive cancellation notices.')}</small></label>}
      {action === 'respond' && <label>{t('My provider response')}<select aria-label={t('My provider response')} className="fc-input" value={response} onChange={event => { setResponse(event.target.value as typeof response); invalidate(); }}><option value="accepted">{t('accepted')}</option><option value="tentative">{t('tentative')}</option><option value="declined">{t('declined')}</option></select></label>}
      {publication?.provider_event && <div className="fc-calendar-provider-roster"><strong>{t('Last read provider attendees')}</strong><ul>{(publication.provider_event.attendees || []).map(person => <li key={person.email}><span>{person.email}</span><span>{t(person.response || 'needs-action')}</span></li>)}</ul></div>}
      {publication?.state === 'uncertain' && <label className="fc-calendar-consent"><input type="checkbox" checked={uncertain} onChange={event => { setUncertain(event.target.checked); invalidate(); }} />{t('I checked the provider. The previous action may already have happened, and I explicitly approve a reviewed retry.')}</label>}
      <button type="button" className="fc-button" disabled={!!busy || !connection?.ready || !selected} onClick={() => void preview()}>{t('Review calendar action')}</button>
      {review && <section className="fc-calendar-review" aria-label={t('Reviewed calendar provider action')}><h5>{review.result.fields.title}</h5><p>{review.result.connection_name} · {t('Version ')}{review.result.event_revision}</p><p>{review.result.fields.starts_at} – {review.result.fields.ends_at}</p><p>{review.result.fields.recurrence ? t('The full recurring series and its occurrence exceptions are included.') : t('This action uses the single reviewed event.')}</p>{review.result.attendees.length > 0 && <ul>{review.result.attendees.map(person => <li key={person.email}>{person.email}</li>)}</ul>}
      {review.result.external_invitations && <label className="fc-calendar-consent"><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)} />{t('I approve sharing these event details and the displayed attendee list, including provider invitation, cancellation or response messages for this action.')}</label>}
      <button className="fc-button fc-button-primary" type="button" disabled={!!busy || review.result.external_invitations && !approved} onClick={() => void send()}><Send size={14} />{review.result.external_invitations ? t('Approve and queue provider action') : t('Queue provider read')}</button></section>}
      </div> : <p className="fc-integration-content-caption">{t('Create, authorize and test your own Google or Microsoft calendar connection in Integrations → Sync first.')}</p>}
      {state.jobs.length > 0 && <ol className="fc-calendar-delivery-jobs" aria-label={t('Calendar delivery receipts')}>{state.jobs.slice(0,8).map(job => <li key={job.id}><span>{receiptAction(job.action)} · {t(job.status)}</span>{job.failure_code && <small>{failure(job.failure_code)}</small>}</li>)}</ol>}
      <form className="fc-calendar-feed-form" onSubmit={event => { event.preventDefault(); void createSubscription(); }}><h4><Link2 size={16} />{t('A revocable calendar subscription')}</h4><p>{t('Anyone with this link can read its native schedule. Busy-only is the default; attendee emails, descriptions and locations are always excluded. Revoking the link stops future fetches; recipients may retain copies.')}</p><label>{t('Subscription lifetime')}<select aria-label={t('Subscription lifetime')} className="fc-input" value={days} onChange={event => setDays(Number(event.target.value))}>{[7,30,90].map(value => <option value={value} key={value}>{value} {t('days')}</option>)}</select></label><label className="fc-calendar-consent"><input type="checkbox" checked={titles} onChange={event => setTitles(event.target.checked)} />{t('Include event titles in the public subscription')}</label>
      <label>{t('Confirm password')}<input type="password" className="fc-input" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} /></label><label>{t('Authentication or recovery code (if enabled)')}<input className="fc-input" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} maxLength={64} /></label><label className="fc-calendar-consent"><input type="checkbox" checked={publicConsent} onChange={event => setPublicConsent(event.target.checked)} />{t('I explicitly authorize public access to this selected channel schedule for the displayed lifetime.')}</label><button className="fc-button" disabled={!!busy || !password || !publicConsent}>{t('Create subscription link')}</button></form>
      {subscriptionUrl && <div role="status" className="fc-calendar-new-link"><p>{t('Copy this subscription URL now. It is shown only once and acts as a private bearer key.')}</p><input className="fc-input" aria-label={t('New calendar subscription URL')} readOnly value={subscriptionUrl} onFocus={event => event.target.select()} /></div>}
      <ul className="fc-calendar-feed-list">{state.subscriptions.filter(row => !row.revoked_at).map(row => <li key={row.id}><span>{row.include_titles ? t('Titles and times') : t('Busy times only')} · {t('Expires ')}{new Date(row.expires_at).toLocaleDateString(currentLocale())}</span><button type="button" className="fc-text-button" disabled={!!busy || !password} onClick={() => void run('revoke', async version => { await calendarDeliveryApi.revoke(channelId,row.id,{password,...(code ? {code} : {})}); if (current(version)) { setSubscriptionUrl(''); setPassword(''); setCode(''); await load(version); } })}>{t('Revoke subscription')}</button></li>)}</ul>
    </div>}
  </section>;
}
