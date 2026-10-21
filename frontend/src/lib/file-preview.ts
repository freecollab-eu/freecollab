import type { ManagedFile } from './files-api';

export const OOXML_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
]);
const textTypes = new Set(['text/plain', 'application/json', 'text/csv', 'text/markdown', 'text/javascript', 'application/javascript',
  'text/typescript', 'application/typescript', 'text/x-python', 'text/x-java-source', 'text/x-c', 'text/x-c++src', 'text/css', 'text/xml', 'application/xml', 'text/html', 'application/yaml', 'text/yaml']);
const audioTypes = new Set(['audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/webm', 'audio/mp4']);
const videoTypes = new Set(['video/mp4', 'video/webm', 'video/ogg']);
export const FILE_EXPERIENCE_TYPES = [...textTypes, ...audioTypes, ...videoTypes, ...OOXML_TYPES];
const extensions: Record<string, string> = { txt:'text/plain', md:'text/markdown', csv:'text/csv', json:'application/json', js:'text/javascript', jsx:'text/javascript', ts:'text/typescript', tsx:'text/typescript',
  py:'text/x-python', java:'text/x-java-source', c:'text/x-c', h:'text/x-c', cpp:'text/x-c++src', css:'text/css', xml:'application/xml', html:'text/html', htm:'text/html', yaml:'text/yaml', yml:'text/yaml',
  mp3:'audio/mpeg', wav:'audio/wav', oga:'audio/ogg', m4a:'audio/mp4', mp4:'video/mp4', webm:'video/webm', ogv:'video/ogg' };
export function normalizeExperienceFile(file: File): File {
  const type = (!file.type || file.type === 'application/octet-stream') && extensions[file.name.split('.').pop()?.toLowerCase() || ''];
  return type ? new File([file], file.name, { type, lastModified: file.lastModified }) : file;
}
export function previewKind(file: Pick<ManagedFile, 'content_type' | 'encrypted'>): 'text' | 'csv' | 'audio' | 'video' | 'office' | null {
  if (file.content_type === 'text/csv') return 'csv';
  if (textTypes.has(file.content_type)) return 'text';
  if (audioTypes.has(file.content_type)) return 'audio';
  if (videoTypes.has(file.content_type)) return 'video';
  if (OOXML_TYPES.has(file.content_type) && !file.encrypted) return 'office';
  return null;
}
export const canPreviewExpanded = (file: ManagedFile) => previewKind(file) !== null;

export function parseCsv(text: string, maximumRows = 200, maximumColumns = 30) {
  const rows: string[][] = []; let row: string[] = [], value = '', quoted = false, truncated = false;
  const finishCell = () => { if (row.length < maximumColumns) row.push(value); else truncated = true; value = ''; };
  const finishRow = () => { finishCell(); if (rows.length < maximumRows) rows.push(row); else truncated = true; row = []; };
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === '"' && (quoted || !value)) {
      if (quoted && text[index + 1] === '"') { value += '"'; index++; } else quoted = !quoted;
    } else if (char === ',' && !quoted) finishCell();
    else if ((char === '\n' || char === '\r') && !quoted) { if (char === '\r' && text[index + 1] === '\n') index++; finishRow(); }
    else value += char;
    if (rows.length >= maximumRows && index < text.length - 1) { truncated = true; break; }
  }
  if (value || row.length) finishRow();
  if (quoted) throw new Error('This CSV contains an unfinished quoted field.');
  return { rows, truncated };
}
