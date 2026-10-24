import { validateFieldReferences, type FieldReferences } from './field-references';
export type DocumentProvider = 'nextcloud' | 'google_drive' | 'sharepoint';
export interface DocumentFields { field_references?: FieldReferences; title: string; provider: DocumentProvider; url: string; notes: string }
export const documentProviders: Record<DocumentProvider, string> = { nextcloud: 'Nextcloud', google_drive: 'Google Drive', sharepoint: 'SharePoint' };
export function validateDocument(value: unknown): DocumentFields {
  if (!value || typeof value !== 'object') throw new Error('Invalid document link.');
  const f = value as Record<string, unknown>;
  if (typeof f.title !== 'string' || !f.title.trim() || f.title.length > 200 || typeof f.notes !== 'string' || f.notes.length > 4000
    || typeof f.provider !== 'string' || !Object.prototype.hasOwnProperty.call(documentProviders, f.provider)
    || typeof f.url !== 'string' || f.url.length > 4096 || /[\x00-\x20\x7f\\]/.test(f.url)) throw new Error('Use a title and a valid provider link.');
  let url: URL; try { url = new URL(f.url); } catch { throw new Error('Use a valid HTTPS document URL.'); }
  const host = url.hostname.toLowerCase(), path = decodeURIComponent(url.pathname);
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || (url.port && url.port !== '443')
    || host.endsWith('.') || !host.includes('.') || /^[\d.]+$/.test(host) || host.includes(':') || /[\x00-\x1f\x7f\\]/.test(path))
    throw new Error('Use an HTTPS provider link without credentials, query parameters or a fragment.');
  const provider = f.provider as DocumentProvider;
  const valid = provider === 'nextcloud' ? /^\/(?:index\.php\/)?(?:s\/[A-Za-z0-9_-]+|f\/\d+|apps\/files(?:\/|$))/.test(path)
    : provider === 'sharepoint' ? (host.endsWith('.sharepoint.com') || host.endsWith('.sharepoint.de')) && path !== '/'
    : host === 'drive.google.com' ? /^\/(?:file\/d\/|drive\/(?:u\/\d+\/)?folders\/)[A-Za-z0-9_-]+/.test(path)
    : host === 'docs.google.com' && /^\/(?:document|spreadsheets|presentation|forms|drawings)\/d\/[A-Za-z0-9_-]+/.test(path);
  if (!valid) throw new Error('The URL does not match the selected document provider.');
  const field_references = validateFieldReferences({ title: f.title, notes: f.notes }, f.field_references);
  return { title: field_references?.title?.length ? f.title : f.title.trim(), provider, url: url.origin + url.pathname, notes: f.notes, ...(field_references ? { field_references } : {}) };
}
