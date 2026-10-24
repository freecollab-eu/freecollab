import { validateFieldReferences, type FieldReferences } from './field-references';
import { validateResourceReferenceTargets } from './resource-reference-targets';
import { api } from './api';
import { getSecureMessaging } from './secure-client';
import { listFileVersions, type ManagedFile } from './files-api';

export interface FileCommentRecord { field_references?: FieldReferences; id: string; document_id: string; file_id: string; created_by: string; created_at: string; text: string }
export interface OfficeSession { id: string; path: string; handler: 'ms-word' | 'ms-excel' | 'ms-powerpoint'; expires_at: string }
const base = (file: ManagedFile) => '/channels/' + encodeURIComponent(file.channel_id) + '/file-experience/' + encodeURIComponent(file.id);
const current = (owner: string | null) => { if (!owner || owner !== api.getAccessToken()) throw new Error('The signed-in account changed.'); };
const COMMENT = 'eu.freecollab.file.comment';

async function secureContext(file: ManagedFile, owner: string | null) {
  current(owner); const secure = await getSecureMessaging(); current(owner);
  const encrypted = await secure.handles(file.channel_id); current(owner);
  if (!encrypted) {
    if (file.encrypted || file.secure_locator) throw new Error('Unlock this encrypted channel before opening its files.');
    return null;
  }
  const versions = await listFileVersions(file); current(owner);
  const verified = versions.find(value => value.id === file.id && value.document_id === file.document_id);
  if (!verified) throw new Error('This encrypted file is no longer available.');
  return { secure, verified };
}

export const fileExperienceApi = {
  async comments(file: ManagedFile): Promise<FileCommentRecord[]> {
    const owner = api.getAccessToken(), context = await secureContext(file, owner); current(owner);
    if (!context) {
      const rows = await api.request<FileCommentRecord[]>(base(file) + '/comments'); current(owner); return rows;
    }
    const { secure, verified } = context, { c, roomId } = await secure.roomFor(file.channel_id); current(owner);
    const domain = c.getUserId()!.split(':').slice(1).join(':'), events = await secure.events(file.channel_id); current(owner);
    const result: FileCommentRecord[] = [];
    for (const event of events) {
      const content = event.getContent();
      if (event.getType() !== COMMENT || content.document_id !== verified.document_id) continue;
      if (event.getRoomId() !== roomId || !event.isEncrypted() || event.isRedacted()
          || typeof content.owner_id !== 'string' || event.getSender() !== '@fc_' + content.owner_id + ':' + domain
          || typeof content.id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(content.id)
          || typeof content.file_event_id !== 'string' || typeof content.text !== 'string' || !content.text.trim() || content.text.length > 2000)
        throw new Error('An encrypted file comment could not be verified.');
      const target = await secure.getDecryptedEvent(file.channel_id, content.file_event_id); current(owner);
      const payload = target.getContent();
      if (target.getRoomId() !== roomId || target.getType() !== 'eu.freecollab.file' || !target.isEncrypted() || target.isRedacted()
          || payload.document_id !== verified.document_id || target.getSender() !== '@fc_' + verified.document_owner_id + ':' + domain)
        throw new Error('The comment refers to a different encrypted file.');
      result.push({ id: event.getId()!, document_id: verified.document_id!, file_id: content.file_event_id,
        created_by: content.owner_id, created_at: new Date(event.getTs()).toISOString(), text: content.text, ...(content.field_references ? { field_references: validateFieldReferences({ text: content.text }, content.field_references) } : {}) });
    }
    current(owner); return result.slice(-100);
  },
  async comment(file: ManagedFile, text: string, identifier = crypto.randomUUID(), references?: FieldReferences) {
    const field_references = validateFieldReferences({ text }, references);
    const owner = api.getAccessToken(), normalized = field_references?.text?.length ? text : text.trim(); current(owner);
    if (!normalized || normalized.length > 2000) throw new Error('Write a comment of at most 2,000 characters.');
    const context = await secureContext(file, owner); current(owner);
    await validateResourceReferenceTargets(file.channel_id, { text: normalized }, field_references, owner, !!context); current(owner);
    if (context) {
      const { secure, verified } = context;
      await secure.sendEvent(file.channel_id, COMMENT, { id: identifier, document_id: verified.document_id,
        file_event_id: verified.id, owner_id: api.getCurrentUserId(), text: normalized, ...(field_references ? { field_references } : {}) }, 'file-comment-' + identifier);
      current(owner);
    } else { await api.request(base(file) + '/comments', { method: 'POST', body: JSON.stringify({ id: identifier, text: normalized, ...(field_references ? { field_references } : {}) }) }); current(owner); }
  },
  async office(file: ManagedFile, password: string, code?: string) {
    const owner = api.getAccessToken(); current(owner);
    if (file.encrypted || file.secure_locator) throw new Error('Desktop Office editing is available only for standard channel files.');
    const result = await api.request<OfficeSession>(base(file) + '/office-sessions', { method: 'POST', body: JSON.stringify({ password, ...(code ? { code } : {}) }) });
    current(owner);
    if (!['ms-word', 'ms-excel', 'ms-powerpoint'].includes(result.handler) || !/^\/office-edit\/[0-9a-f]{32}\/[A-Za-z0-9_-]{43}\//.test(result.path)) throw new Error('Invalid Office handoff.');
    return result;
  },
  async closeOffice(file: ManagedFile, session: OfficeSession) {
    const owner = api.getAccessToken(); current(owner);
    await api.request(base(file) + '/office-sessions/' + encodeURIComponent(session.id), { method: 'DELETE' }); current(owner);
  },
};

export function officeHandoff(session: OfficeSession): { webdav: string; office: string } {
  const apiBase = new URL(api.getBaseUrl().replace(/\/$/, '') + '/', location.origin);
  const webdav = new URL(session.path.replace(/^\//, ''), apiBase);
  if (webdav.protocol !== 'https:' && !['127.0.0.1', 'localhost', '[::1]'].includes(webdav.hostname)) throw new Error('Desktop Office editing requires HTTPS.');
  return { webdav: webdav.href, office: session.handler + ':ofe|u|' + webdav.href };
}
