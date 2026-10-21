import { useEffect, useRef, useState } from 'react';
import { FileText, Image as ImageIcon, ShieldCheck } from 'lucide-react';
import { api } from '../lib/api';
import { downloadChannelFile, type ManagedFile } from '../lib/files-api';
import { useI18n } from '../i18n';
import '../styles/file-experience.css';

export function FileThumbnail({ file }: { file: ManagedFile }) {
  const [url, setUrl] = useState(''); const element = useRef<HTMLSpanElement>(null);
  const image = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(file.content_type);
  useEffect(() => {
    setUrl(''); if (!image || file.size > 8 * 1024 * 1024) return;
    const owner = api.getAccessToken(), abort = new AbortController(); let active = true, objectUrl = '', started = false;
    const load = () => {
      if (started) return; started = true;
      void downloadChannelFile(file, { signal: abort.signal }).then(blob => {
        if (!active || owner !== api.getAccessToken() || blob.size > 8 * 1024 * 1024) return;
        objectUrl = URL.createObjectURL(new Blob([blob], { type: file.content_type })); setUrl(objectUrl);
      }).catch(() => undefined);
    };
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) { load(); observer.disconnect(); } });
    if (element.current) observer.observe(element.current);
    const purge = () => { active = false; abort.abort(); observer.disconnect(); if (objectUrl) URL.revokeObjectURL(objectUrl); setUrl(''); };
    window.addEventListener('freecollab:auth-changed', purge);
    return () => { purge(); window.removeEventListener('freecollab:auth-changed', purge); };
  }, [file.id, file.channel_id, image]);
  return <span ref={element} className="fc-file-thumbnail" aria-hidden="true">{url ? <img src={url} alt="" loading="lazy" onError={() => setUrl('')} onLoad={event => { if (event.currentTarget.naturalWidth * event.currentTarget.naturalHeight > 32_000_000) setUrl(''); }} /> : image ? <ImageIcon size={24} /> : <FileText size={24} />}</span>;
}

export function FileGallery({ files, onPreview }: { files: ManagedFile[]; onPreview: (file: ManagedFile) => void }) {
  const { t, locale } = useI18n();
  return <ul className="fc-file-gallery" aria-label={t('File gallery')}>{files.map(file => <li key={file.document_id || file.id}><button type="button" onClick={() => onPreview(file)} aria-label={t('Open {filename}', { filename: file.filename })}><FileThumbnail file={file} /><strong>{file.filename}</strong><span>{t('Version {number}', { number: file.version_number || 1 })} · {new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(file.size / 1024)} KB</span>{file.encrypted && <small><ShieldCheck size={12} />{t('Encrypted on your device')}</small>}</button></li>)}</ul>;
}
