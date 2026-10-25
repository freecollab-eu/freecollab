import { useEffect, useRef, useState } from 'react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { pendingMeetingRecordings, publishMeetingRecording, discardMeetingRecording, recordingBlob, type PendingMeetingRecording } from '../lib/meeting-recording-vault';

/** Private encrypted device drafts survive a failed upload, tab close or call end. */
export function MeetingRecordingRecovery({ channelId, onPublished,onEmpty }: { channelId: string; onPublished: () => void;onEmpty?:()=>void }) {
  useI18n();
  const [rows, setRows] = useState<PendingMeetingRecording[]>([]), [busy, setBusy] = useState(''), [error, setError] = useState('');
  const generation = useRef(0), transfer = useRef<AbortController | null>(null);
  const empty=useRef(onEmpty);empty.current=onEmpty;
  async function load() { const attempt = generation.current,actor=api.getCurrentUserId(),token=api.getAccessToken(); try { const next = await pendingMeetingRecordings(channelId); if (attempt === generation.current&&actor===api.getCurrentUserId()&&token===api.getAccessToken()) {setRows(next);if(!next.length)empty.current?.();} } catch (failure) { if (attempt === generation.current) setError(String(failure)); } }
  useEffect(() => {
    ++generation.current; setRows([]); setError(''); void load();
    const refresh = () => void load(), cancel = () => { ++generation.current; transfer.current?.abort(); setRows([]); };
    window.addEventListener('freecollab:meeting-recording-pending', refresh); window.addEventListener('freecollab:auth-changed', cancel);
    window.addEventListener('freecollab:clips-changed',refresh);
    return () => { cancel(); window.removeEventListener('freecollab:meeting-recording-pending', refresh); window.removeEventListener('freecollab:auth-changed', cancel);window.removeEventListener('freecollab:clips-changed',refresh); };
  }, [channelId]);
  if (!rows.length && !error) return null;
  async function publish(row: PendingMeetingRecording) {
    if (busy) return; const attempt = generation.current, token = api.getAccessToken(), controller = new AbortController(); transfer.current = controller;
    setBusy(row.id); setError('');
    try { await publishMeetingRecording(row, undefined, controller.signal); if (attempt === generation.current && token === api.getAccessToken()) { await load(); onPublished(); } }
    catch (failure) { if (attempt === generation.current) setError(failure instanceof Error ? failure.message : 'The recording remains private on this device.'); }
    finally { if (attempt === generation.current) setBusy(''); }
  }
  return <section className="fc-meeting-recording-recovery" aria-label={t('Private recording drafts')}>
    <h4>{t('Private recording drafts')}</h4><p>{t('These parts are encrypted in this account’s device storage. Retry checks current recording consent; nothing is published after permission was withdrawn.')}</p>
    {error && <p className="fc-alert" role="alert">{error}</p>}
    {rows.map(row => <article key={row.id}><strong>{row.fields.title}</strong><small>{row.stage === 'published' ? t('Clip saved; private AI notes need attention') : t('Waiting to publish')}</small>
      <div className="fc-meeting-capture-actions"><button className="fc-button" disabled={!!busy} onClick={() => void publish(row)}>{busy === row.id ? t('Publishing…') : row.stage === 'published' ? t('Retry private meeting notes') : t('Retry publication')}</button>
        {row.stage === 'captured' && <button className="fc-button" disabled={!!busy} onClick={() => {
          const blob = recordingBlob(row), url = URL.createObjectURL(blob), link = document.createElement('a');
          link.href = url; link.download = 'private-meeting-' + row.id + (row.mime.includes('mp4') ? '.mp4' : '.webm'); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        }}>{t('Download my private copy')}</button>}
        <button className="fc-button" disabled={!!busy} onClick={() => { void discardMeetingRecording(row.id).then(load).catch(failure => setError(String(failure))); }}>{row.stage === 'published' ? t('Keep clip without automatic notes') : t('Discard private draft')}</button>
      </div></article>)}
  </section>;
}
