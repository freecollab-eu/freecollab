import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { downloadChannelFile, type ManagedFile, wasCancelled } from '../lib/files-api';
import { parseCsv, previewKind } from '../lib/file-preview';
import { useI18n } from '../i18n';
import '../styles/file-experience.css';

export function SafeFilePreview({ file, onVerifiedText }: { file: ManagedFile; onVerifiedText?: (value: { key: string; owner: string | null; text: string }) => void }) {
  const { t } = useI18n(), kind = previewKind(file), owner = useRef(api.getAccessToken());
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [text, setText] = useState(''), [url, setUrl] = useState(''), [blocks, setBlocks] = useState<string[]>([]), [truncated, setTruncated] = useState(false);
  const textCallback = useRef(onVerifiedText); textCallback.current = onVerifiedText;
  useEffect(() => {
    const token = api.getAccessToken(); owner.current = token;
    let stopped = false, objectUrl = ''; const abort = new AbortController();
    const valid = () => !stopped && token === api.getAccessToken();
    setLoading(true); setError(''); setText(''); setUrl(''); setBlocks([]); setTruncated(false);
    const purge = () => { stopped = true; abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); setUrl(''); setText(''); setBlocks([]); setError(''); setLoading(false); };
    window.addEventListener('freecollab:auth-changed', purge);
    void (async () => {
      if (!kind) throw new Error('Preview is unavailable for this file type.');
      if (kind === 'office') {
        const result = await api.request<{ blocks: string[]; truncated: boolean; passive: boolean }>('/channels/' + encodeURIComponent(file.channel_id) + '/file-experience/' + encodeURIComponent(file.id) + '/preview', { signal: abort.signal });
        if (!valid()) return;
        if (result.passive !== true || !Array.isArray(result.blocks) || result.blocks.length > 2000 || result.blocks.some(value => typeof value !== 'string') || result.blocks.join('').length > 100000) throw new Error('This Office preview could not be verified.');
        setBlocks(result.blocks); setTruncated(result.truncated);
        textCallback.current?.({ key: file.channel_id + ':' + file.id, owner: token, text: result.blocks.join('\n') });
      } else {
        const maximum = kind === 'text' || kind === 'csv' ? 1024 * 1024 : 50 * 1024 * 1024;
        if (file.size > maximum) throw new Error(kind === 'text' || kind === 'csv' ? 'Text preview limit is 1 MiB. Download the file to read it.' : 'Media preview limit is 50 MiB.');
        const blob = await downloadChannelFile(file, { signal: abort.signal }); if (!valid()) return;
        if (blob.size > maximum) throw new Error('This file exceeds the preview limit.');
        if (kind === 'text' || kind === 'csv') {
          const bytes = await blob.arrayBuffer(); if (!valid()) return;
          let value: string; try { value = new TextDecoder('utf-8', { fatal: true }).decode(bytes); } catch { throw new Error('This file is not supported UTF-8 text.'); }
          if (value.includes('\0')) throw new Error('Binary content cannot be displayed as text.');
          setText(value);
          textCallback.current?.({ key: file.channel_id + ':' + file.id, owner: token, text: value });
        } else { objectUrl = URL.createObjectURL(new Blob([blob], { type: file.content_type })); setUrl(objectUrl); }
      }
      if (valid()) setLoading(false);
    })().catch(failure => { if (valid() && !wasCancelled(failure)) { setError(failure instanceof Error ? failure.message : 'This file could not be previewed.'); setLoading(false); } });
    return () => { stopped = true; abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); window.removeEventListener('freecollab:auth-changed', purge); };
  }, [file.id, file.channel_id, file.content_type, file.encrypted, kind]);
  let csv: ReturnType<typeof parseCsv> | undefined, csvError = '';
  if (kind === 'csv' && text) try { csv = parseCsv(text); } catch (failure) { csvError = failure instanceof Error ? failure.message : 'This CSV could not be displayed.'; }
  return <section className="fc-safe-file-preview" aria-label={t('File contents')} aria-busy={loading}>
    {loading && <p role="status">{t('Opening file…')}</p>}
    {(error || csvError) && <p role="alert" className="fc-alert">{t(error || csvError)}</p>}
    {!loading && !error && (kind === 'office' ? <><p className="fc-file-preview-note">{t('Passive text preview. Layout, embedded content and formulas are not executed.')}</p><div className="fc-office-text">{blocks.map((value, index) => <p key={index}>{value}</p>)}</div>{truncated && <p role="status">{t('The preview is shortened. Download the file for the complete document.')}</p>}</> : kind === 'text' ? <pre className="fc-file-code"><code>{text}</code></pre> : kind === 'csv' && csv ? <><div className="fc-file-csv-scroll"><table aria-label={t('CSV preview')}><tbody>{csv.rows.map((row, index) => <tr key={index}>{row.map((value, cell) => <td key={cell}>{value}</td>)}</tr>)}</tbody></table></div>{csv.truncated && <p role="status">{t('Showing up to 200 rows and 30 columns.')}</p>}</> : kind === 'audio' && url ? <audio controls preload="metadata" src={url} aria-label={file.filename} onError={() => setError('Your browser cannot play this audio format. Download it to open it.')} /> : kind === 'video' && url ? <video controls playsInline preload="metadata" src={url} aria-label={file.filename} onError={() => setError('Your browser cannot play this video format. Download it to open it.')} /> : null)}
  </section>;
}
