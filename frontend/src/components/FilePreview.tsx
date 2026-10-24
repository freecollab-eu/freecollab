import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, X } from 'lucide-react';
import { createPortal } from 'react-dom';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { downloadChannelFile, type ManagedFile, wasCancelled } from '../lib/files-api';
import { api } from '../lib/api';
import { useModalFocus } from '../lib/useModalFocus';
import { ModalContinuationProvider, useCollaborationModal } from '../lib/useCollaborationModal';
import '../styles/panels-files.css';
import { SafeFilePreview } from './SafeFilePreview';
import { FileExperiencePanel } from './FileExperiencePanel';
import { canPreviewExpanded } from '../lib/file-preview';

const imageTypes = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
export const canPreviewFile = (file: ManagedFile) => imageTypes.has(file.content_type) || file.content_type === 'application/pdf' || canPreviewExpanded(file);

function PdfPage({ document, page }: { document: PDFDocumentProxy; page: number }) {
  useI18n();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState('');
  const [rendering, setRendering] = useState(true);
  useEffect(() => {
    let stopped = false;
    let cancel: (() => void) | undefined;
    setError(''); setRendering(true);
    void document.getPage(page).then(async pdfPage => {
      if (stopped || !canvas.current) return;
      const original = pdfPage.getViewport({ scale: 1 });
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const scale = Math.min(720 * ratio / original.width, Math.sqrt(4_000_000 / (original.width * original.height)));
      const viewport = pdfPage.getViewport({ scale });
      const element = canvas.current;
      element.width = Math.ceil(viewport.width); element.height = Math.ceil(viewport.height);
      const render = pdfPage.render({ canvas: element, viewport });
      cancel = () => render.cancel();
      try { await render.promise; if (!stopped) setRendering(false); }
      finally { pdfPage.cleanup(); }
    }).catch(err => { if (!stopped) { setError(err instanceof Error ? err.message : t("This page could not be displayed.")); setRendering(false); } });
    return () => { stopped = true; cancel?.(); };
  }, [document, page]);
  return <div className="fc-pdf-page" aria-busy={rendering}>{rendering && <p role="status">{t("Rendering page…")}</p>}{error && <p role="alert">{t("Unable to preview this page. Download the PDF to open it.")}</p>}<canvas ref={canvas} aria-label={t("PDF page {value0}", { value0: String(page) })} role="img" hidden={!!error} /></div>;
}

export function FilePreview({ file, onClose, onVersionSaved }: { file: ManagedFile; onClose: () => void; onVersionSaved?: () => void }) {
  useI18n();
  const writingLeave = useRef<(() => boolean) | null>(null);
  const close = () => { if (!writingLeave.current || writingLeave.current()) onClose(); };
  const dialog = useRef<HTMLDivElement>(null);
  const modal = useCollaborationModal(dialog, t("Preview {value0}", { value0: String(file.filename) }), `file:${file.channel_id}:${file.id}`);
  useModalFocus(dialog, close, true, { suspended: modal.parked });
  const [url, setUrl] = useState('');
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const download = async () => {
    if (downloading) return;
    const token = api.getAccessToken(); setDownloading(true);
    try {
      const blob = await downloadChannelFile(file);
      if (token !== api.getAccessToken()) return;
      const objectUrl = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = objectUrl; link.download = file.filename; document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (failure) { if (token === api.getAccessToken()) setError(failure instanceof Error ? failure.message : t('Unable to open this file.')); }
    finally { if (token === api.getAccessToken()) setDownloading(false); }
  };
  useEffect(() => {
    let stopped = false;
    let localUrl: string | undefined;
    let destroyPdf: (() => Promise<void>) | undefined;
    const controller = new AbortController();
    setLoading(true); setError(''); setPage(1); setPdf(null); setUrl('');
    if (canPreviewExpanded(file)) { setLoading(false); return () => { controller.abort(); }; }
    void (async () => {
      if (!canPreviewFile(file)) throw new Error(t("Preview is unavailable for this file type."));
      const blob = await downloadChannelFile(file, { signal: controller.signal });
      if (stopped) return;
      if (blob.size > 50 * 1024 * 1024) throw new Error(t("This file is too large to preview."));
      // Force the authenticated allowlisted media type; never inline HTML or SVG.
      const typed = new Blob([blob], { type: file.content_type });
      localUrl = URL.createObjectURL(typed);
      setUrl(localUrl);
      if (file.content_type === 'application/pdf') {
        const [pdfjs, worker] = await Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')]);
        if (stopped) return;
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
        const data = new Uint8Array(await typed.arrayBuffer());
        if (stopped) return;
        // PDF.js 6 renders canvas operators without evaluating document scripts.
        const task = pdfjs.getDocument({ data,
          useSystemFonts: true, stopAtErrors: true, maxImageSize: 16_000_000,
          disableAutoFetch: true, disableStream: true });
        destroyPdf = () => task.destroy();
        const document = await task.promise;
        if (stopped) { await task.destroy(); return; }
        setPdf(document);
      }
      if (!stopped) setLoading(false);
    })().catch(err => { if (!stopped && !wasCancelled(err)) { setError(err instanceof Error ? err.message : t("Unable to open this file.")); setLoading(false); } });
    return () => { stopped = true; controller.abort(); if (localUrl) URL.revokeObjectURL(localUrl); void destroyPdf?.(); };
  }, [file.id, file.channel_id]);

  return createPortal(<ModalContinuationProvider modalId={modal.modalId}><div ref={dialog} hidden={modal.parked} style={modal.parked ? { display: 'none' } : undefined} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("Preview {value0}", { value0: String(file.filename) })} className="fc-file-preview-layer" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section className="fc-file-preview">
      <header><div><span className="fc-kicker">{file.content_type === 'application/pdf' ? t("PDF preview") : imageTypes.has(file.content_type) ? t("Image preview") : t("File preview")} {t(" · Version ")}{file.version_number || 1}</span><h2>{file.filename}</h2></div><button type="button" data-autofocus className="fc-icon-button" aria-label={t("Close file preview")} onClick={close}><X size={20} /></button></header>
      <div className="fc-file-preview-content" aria-busy={loading}>{loading && <p role="status" className="fc-file-preview-status"><span className="fc-panel-spinner" />{t("Opening file…")}</p>}{error && <p role="alert" className="fc-alert">{error}</p>}{!loading && !error && (canPreviewExpanded(file) ? <SafeFilePreview file={file} /> : pdf ? <PdfPage document={pdf} page={page} /> : url && <img src={url} alt={file.filename} onError={() => setError(t("This image could not be displayed. Download it to open it."))} onLoad={event => { if (event.currentTarget.naturalWidth * event.currentTarget.naturalHeight > 32_000_000) setError(t("This image is too large to preview. Download it to open it.")); }} />)}</div>
      <div className="fc-file-experience-scroll"><FileExperiencePanel key={`${file.channel_id}:${file.id}`} file={file} onVersionSaved={onVersionSaved} leaveController={writingLeave} /></div>
      <footer>{pdf ? <div className="fc-pdf-pagination"><button type="button" className="fc-icon-button" aria-label={t("Previous PDF page")} disabled={page <= 1} onClick={() => setPage(value => value - 1)}><ChevronLeft size={17} /></button><span aria-live="polite">{t("Page ")}{page} {t(" of ")}{pdf.numPages}</span><button type="button" className="fc-icon-button" aria-label={t("Next PDF page")} disabled={page >= pdf.numPages} onClick={() => setPage(value => value + 1)}><ChevronRight size={17} /></button></div> : <span>{file.encrypted ? t("Decrypted on this device.") : t("Available to channel members.")}</span>}{url ? <a href={url} download={file.filename} className="fc-button fc-panel-button"><Download size={14} />{t("Download")}</a> : <button type="button" className="fc-button fc-panel-button" disabled={downloading} onClick={() => void download()}><Download size={14} />{t("Download")}</button>}</footer>
    </section>
  </div></ModalContinuationProvider>, document.body);
}
