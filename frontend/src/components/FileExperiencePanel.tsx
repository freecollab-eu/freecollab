import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Check, ExternalLink, MessageSquare, RefreshCw, X } from 'lucide-react';
import { api } from '../lib/api';
import type { ManagedFile } from '../lib/files-api';
import { fileExperienceApi, officeHandoff, type FileCommentRecord, type OfficeSession } from '../lib/file-experience-api';
import { OOXML_TYPES } from '../lib/file-preview';
import { useI18n } from '../i18n';
import '../styles/file-experience.css';

export function FileExperiencePanel({ file, onVersionSaved }: { file: ManagedFile; onVersionSaved?: () => void }) {
  const { t, locale } = useI18n();
  const [comments, setComments] = useState<FileCommentRecord[]>([]), [draft, setDraft] = useState(''), [members, setMembers] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [showOffice, setShowOffice] = useState(false), [password, setPassword] = useState(''), [code, setCode] = useState(''), [office, setOffice] = useState<OfficeSession | null>(null);
  const owner = useRef(api.getAccessToken()), generation = useRef(0), identifier = useRef(crypto.randomUUID()), alert = useRef<HTMLDivElement>(null);
  const valid = (request: number) => request === generation.current && owner.current === api.getAccessToken();
  const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'This file action could not be completed.';
  const canOffice = !file.encrypted && !file.secure_locator && OOXML_TYPES.has(file.content_type) && (file.document_owner_id || file.uploader_id) === api.getCurrentUserId();
  const date = (value: string) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value + (/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? '' : 'Z')));
  async function refresh(request = generation.current) {
    try { const rows = await fileExperienceApi.comments(file); if (valid(request)) setComments(rows); }
    catch (failure) { if (valid(request)) setError(describe(failure)); }
    finally { if (valid(request)) setLoading(false); }
  }
  useEffect(() => {
    const request = ++generation.current; owner.current = api.getAccessToken();
    setComments([]); setDraft(''); setError(''); setNotice(''); setPassword(''); setCode(''); setOffice(null); setShowOffice(false); setBusy(''); setLoading(true); identifier.current = crypto.randomUUID();
    void refresh(request);
    void api.getChannelMembers(file.channel_id).then(rows => { if (valid(request)) setMembers(Object.fromEntries(rows.map(row => [row.user_id, row.name]))); }).catch(() => undefined);
    let fetching = false;
    const timer = window.setInterval(() => { if (!fetching && valid(request)) { fetching = true; void refresh(request).finally(() => { fetching = false; }); } }, 5000);
    const purge = () => { ++generation.current; setComments([]); setDraft(''); setPassword(''); setCode(''); setOffice(null); setError(''); setNotice(''); setBusy(''); };
    window.addEventListener('freecollab:auth-changed', purge);
    return () => { ++generation.current; clearInterval(timer); window.removeEventListener('freecollab:auth-changed', purge); };
  }, [file.id, file.channel_id]);
  useEffect(() => { if (error) alert.current?.focus(); }, [error]);
  async function run(key: string, action: (request: number) => Promise<void>) {
    if (busy) return; const request = generation.current; setBusy(key); setError(''); setNotice('');
    try { await action(request); } catch (failure) { if (valid(request)) setError(describe(failure)); }
    finally { if (valid(request)) setBusy(''); }
  }
  function comment(event: FormEvent) {
    event.preventDefault(); void run('comment', async request => {
      await fileExperienceApi.comment(file, draft, identifier.current); if (!valid(request)) return;
      identifier.current = crypto.randomUUID(); setDraft(''); setNotice('Comment posted.'); await refresh(request);
    });
  }
  function openOffice(event: FormEvent) {
    event.preventDefault(); void run('office', async request => {
      try { const result = await fileExperienceApi.office(file, password, code || undefined); if (valid(request)) { setOffice(result); setShowOffice(false); } }
      finally { if (valid(request)) { setPassword(''); setCode(''); } }
    });
  }
  let handoff: ReturnType<typeof officeHandoff> | undefined, handoffError = '';
  if (office) try { handoff = officeHandoff(office); } catch (failure) { handoffError = describe(failure); }
  const expires = office ? new Date(office.expires_at + (/Z|[+-]\d\d:\d\d$/.test(office.expires_at) ? '' : 'Z')) : null;
  return <section className="fc-file-experience" aria-label={t('File discussion and editing')}>
    {error && <div ref={alert} tabIndex={-1} role="alert" className="fc-alert">{t(error)}</div>}
    {notice && <p role="status" className="fc-file-experience-notice"><Check size={14} />{t(notice)}</p>}
    {canOffice && <section className="fc-office-handoff" aria-label={t('Desktop Office editing')}><header><div><h3>{t('Continue in Office')}</h3><p>{t('Open the current DOCX, XLSX or PPTX in your desktop app. Saving creates a new version here.')}</p></div>{!office && <button type="button" className="fc-button" onClick={() => setShowOffice(!showOffice)} disabled={!!busy}><ExternalLink size={14} />{t('Set up Office editing')}</button>}</header>
      {showOffice && <form onSubmit={openOffice}><p>{t('Confirm your password to create a private 15-minute editing session. Your browser sign-in token is never placed in the document URL.')}</p><label>{t('Current password')}<input className="fc-input" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required /></label><label>{t('Authenticator or recovery code, if enabled')}<input className="fc-input" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)} /></label><div><button type="button" className="fc-button" onClick={() => { setShowOffice(false); setPassword(''); setCode(''); }}>{t('Cancel')}</button><button type="submit" className="fc-button fc-button-primary" disabled={!!busy || !password}>{busy === 'office' ? t('Preparing…') : t('Create Office session')}</button></div></form>}
      {office && <div className="fc-office-session">{handoffError && <p role="alert">{t(handoffError)}</p>}{handoff && <a className="fc-button fc-button-primary" href={handoff.office} rel="noreferrer" onClick={() => setNotice('Your desktop Office app will ask to open this document. Keep FreeCollab open to refresh saved versions.')}><ExternalLink size={14} />{t('Open in desktop Office')}</a>}<p>{t('Editing access expires at {time}. Keep this private link on your device.', { time: expires ? new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(expires) : '' })}</p><p>{t('Save conflicts preserve the newer version. Reopen the file if Office reports a conflict or expired access.')}</p><div><button type="button" className="fc-button" onClick={() => onVersionSaved?.()}><RefreshCw size={14} />{t('Refresh saved versions')}</button><button type="button" className="fc-button" disabled={!!busy} onClick={() => void run('close', async request => { await fileExperienceApi.closeOffice(file, office); if (valid(request)) { setOffice(null); setNotice('Office access revoked.'); onVersionSaved?.(); } })}><X size={14} />{t('Close Office session')}</button></div></div>}
    </section>}
    {file.encrypted && OOXML_TYPES.has(file.content_type) && <p className="fc-file-preview-note">{t('Encrypted Office files remain encrypted. Download to open locally; automatic desktop saveback is available only for standard channel files.')}</p>}
    <section className="fc-file-discussion" aria-label={t('File comments')}><header><h3><MessageSquare size={17} />{t('File comments')}</h3><button type="button" className="fc-icon-button" aria-label={t('Refresh file comments')} onClick={() => void refresh()}><RefreshCw size={15} /></button></header><p>{t('Comments follow this document across versions. Each comment keeps its author and the version it refers to.')}</p>
      {loading ? <p role="status">{t('Loading comments…')}</p> : comments.length ? <ol>{comments.map(row => <li key={row.id}><div><strong>{members[row.created_by] || t('Channel member')}</strong><time dateTime={row.created_at}>{date(row.created_at)}</time></div><p>{row.text}</p>{row.file_id !== file.id && <small>{t('On another version of this document')}</small>}</li>)}</ol> : <p>{t('Start a conversation about this file.')}</p>}
      <form onSubmit={comment}><label htmlFor={'file-comment-' + file.id}>{t('Add a file comment')}</label><textarea id={'file-comment-' + file.id} className="fc-input" rows={3} maxLength={2000} value={draft} disabled={busy === 'comment'} onChange={event => { setDraft(event.target.value); identifier.current = crypto.randomUUID(); }} /><button type="submit" className="fc-button" disabled={!!busy || !draft.trim()}><MessageSquare size={14} />{busy === 'comment' ? t('Posting…') : t('Post comment')}</button></form>
    </section>
  </section>;
}
