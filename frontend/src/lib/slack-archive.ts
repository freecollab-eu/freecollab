/** Strict, bounded ZIP review on the trusted device. No archive bytes are uploaded. */
import { Inflate } from 'fflate';
import { FILE_EXPERIENCE_TYPES } from './file-preview';
export const SLACK_LIMITS = { compressed: 50 * 1024 * 1024, expanded: 100 * 1024 * 1024, json: 8 * 1024 * 1024, entries: 10000, messages: 100000, channels: 1000, files: 2000 } as const;
export interface HistoricalPerson { id: string; name: string }
export interface SlackHistoricalDetails {
  version: 1; source: 'slack'; source_timestamp: string; current_text: string; author: HistoricalPerson; edited_at: string | null;
  history: { timestamp: string; text: string; author: HistoricalPerson }[];
  reactions: { name: string; count: number; users: HistoricalPerson[] }[];
}
export interface SlackArchiveMessage { ts: string; text: string; author: string; parent: string | null; files: { path: string; name: string; mime: string }[]; missing: number; details: SlackHistoricalDetails }
export interface SlackArchiveChannel { id: string; name: string; members: string[]; messages: SlackArchiveMessage[] }
interface Entry { name: string; offset: number; compressed: number; expanded: number; method: number; crc: number; end: number }
const utf8 = new TextDecoder('utf-8', { fatal: true }), idPattern = /^[A-Z][A-Z0-9]{0,31}$/, stampPattern = /^\d{1,12}\.\d{1,6}$/;
function fail(message = 'Invalid or unsupported Slack ZIP archive'): never { throw new Error(message); }
const object = (v: unknown): Record<string, unknown> => !v || typeof v !== 'object' || Array.isArray(v) ? fail() : v as Record<string, unknown>;
const string = (v: unknown, max: number): string => typeof v !== 'string' || v.length > max ? fail() : v;
const stamp = (v: unknown): string => { const value = string(v, 20); if (!stampPattern.test(value) || !Number.isFinite(new Date(Number(value) * 1000).getTime())) fail('Invalid Slack timestamp'); return value; };
const decimalCompare = (a: string, b: string) => { const av = BigInt(a.replace('.', '').padEnd(a.indexOf('.') + 6, '0')), bv = BigInt(b.replace('.', '').padEnd(b.indexOf('.') + 6, '0')); return av < bv ? -1 : av > bv ? 1 : 0; };
const crcTable = Array.from({ length: 256 }, (_, n) => { for (let i = 0; i < 8; i++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1; return n >>> 0; });
function crc32(bytes: Uint8Array) { let crc = 0xffffffff; for (const b of bytes) crc = crcTable[(crc ^ b) & 255] ^ (crc >>> 8); return (crc ^ 0xffffffff) >>> 0; }
export async function slackDigest(bytes: Uint8Array | string) { const data = typeof bytes === 'string' ? new TextEncoder().encode(bytes) : new Uint8Array(bytes); return [...new Uint8Array(await crypto.subtle.digest('SHA-256', data))].map(v => v.toString(16).padStart(2, '0')).join(''); }

/** Validate duplicate keys and recursion before JSON.parse can erase evidence. */
export function parseSlackJson(text: string): unknown {
  let cursor = 0, nodes = 0;
  const space = () => { while (/\s/.test(text[cursor] || '') && cursor < text.length) cursor++; };
  const quoted = () => {
    if (text[cursor++] !== '"') fail(); const start = cursor - 1;
    while (cursor < text.length) { const c = text[cursor++]; if (c === '\\') cursor++; else if (c === '"') return text.slice(start, cursor); }
    return fail();
  };
  const value = (depth: number) => {
    if (depth > 64 || ++nodes > 2000000) fail('Slack JSON exceeds the nesting or node limit');
    space(); const c = text[cursor];
    if (c === '{') {
      cursor++; space(); const keys = new Set<string>();
      if (text[cursor] === '}') { cursor++; return; }
      while (true) { space(); const key = JSON.parse(quoted()); if (keys.has(key)) fail('Duplicate JSON key'); keys.add(key); space(); if (text[cursor++] !== ':') fail(); value(depth + 1); space(); const next = text[cursor++]; if (next === '}') return; if (next !== ',') fail(); }
    }
    if (c === '[') { cursor++; space(); if (text[cursor] === ']') { cursor++; return; } while (true) { value(depth + 1); space(); const next = text[cursor++]; if (next === ']') return; if (next !== ',') fail(); } }
    if (c === '"') { quoted(); return; }
    const literal = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(cursor));
    if (!literal) fail(); cursor += literal![0].length;
  };
  value(0); space(); if (cursor !== text.length) fail(); return JSON.parse(text);
}

function details(row: Record<string, unknown>, labels: Map<string, string>): SlackHistoricalDetails {
  const person = (id: unknown): HistoricalPerson => { const identifier = string(id, 80); if (!identifier) fail(); return { id: identifier, name: labels.get(identifier) || identifier }; };
  const author = person(row.user || row.bot_id || 'unknown');
  const edited = row.edited == null ? null : stamp(object(row.edited).ts);
  const history = row.edit_history ?? row.history ?? [];
  if (!Array.isArray(history) || history.length > 25) fail('Historical edits exceed the per-message limit');
  const source = row.previous_message ? [...history, row.previous_message] : history, revisions: SlackHistoricalDetails['history'] = [], seen = new Set<string>();
  for (const item of source) {
    const old = object(item), timestamp = stamp(old.edited ? object(old.edited).ts : old.ts), text = string(old.text ?? '', 18000), key = JSON.stringify([timestamp, text]);
    if (seen.has(key)) continue; seen.add(key); revisions.push({ timestamp, text, author: person(old.user || author.id) });
  }
  if (revisions.length > 25) fail(); revisions.sort((a, b) => decimalCompare(a.timestamp, b.timestamp));
  const reactions = row.reactions ?? []; if (!Array.isArray(reactions) || reactions.length > 100) fail();
  const names = new Set<string>(), parsed = reactions.map(item => {
    const value = object(item), name = string(value.name, 100), count = value.count, users = value.users ?? [];
    if (!/^[A-Za-z0-9_+\-]{1,100}$/.test(name) || names.has(name) || !Array.isArray(users) || users.length > 5000 || new Set(users).size !== users.length || !Number.isSafeInteger(count) || (count as number) < users.length || (count as number) > 1000000) fail('Invalid historical reaction');
    names.add(name); return { name, count: count as number, users: users.map(person) };
  });
  const result: SlackHistoricalDetails = { version: 1, source: 'slack', source_timestamp: stamp(row.ts), current_text: string(row.text || '', 18000), author, edited_at: edited, history: revisions, reactions: parsed };
  if (new TextEncoder().encode(JSON.stringify(result)).length > 128 * 1024) fail('Historical message metadata exceeds 128 KB');
  return result;
}

const allowedFiles = new Set(['image/jpeg','image/png','image/gif','image/webp','application/pdf','text/plain','application/json',...FILE_EXPERIENCE_TYPES,'application/vnd.oasis.opendocument.text','application/vnd.oasis.opendocument.spreadsheet','application/vnd.oasis.opendocument.presentation']);
export class SlackArchive {
  private entries = new Map<string, Entry>();
  private verifiedFiles = new Set<string>();
  readonly labels = new Map<string, string>();
  readonly channels: SlackArchiveChannel[] = [];
  digest = ''; bundledFiles = 0; missingFiles = 0;
  constructor(private raw: Uint8Array) {
    if (!raw.length || raw.length > SLACK_LIMITS.compressed) fail('Choose a Slack ZIP smaller than 50 MB.');
    const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength), u16 = (p: number) => view.getUint16(p, true), u32 = (p: number) => view.getUint32(p, true);
    let eocd = -1;
    for (let p = raw.length - 22; p >= Math.max(0, raw.length - 65557); p--) if (u32(p) === 0x06054b50 && p + 22 + u16(p + 20) === raw.length) { eocd = p; break; }
    if (eocd < 0 || u16(eocd + 4) || u16(eocd + 6) || u16(eocd + 8) !== u16(eocd + 10)) fail('Split ZIP archives are not supported');
    const count = u16(eocd + 10), start = u32(eocd + 16), size = u32(eocd + 12);
    if (!count || count > SLACK_LIMITS.entries || count === 0xffff || start === 0xffffffff || size === 0xffffffff || start + size !== eocd) fail('ZIP directory exceeds the limit or uses ZIP64');
    let p = start, expandedTotal = 0; const ranges: [number, number][] = [];
    const extras = (at: number, length: number) => { const end = at + length; while (at < end) { if (at + 4 > end) fail(); const kind = u16(at), bytes = u16(at + 2); if (kind === 1 || kind === 0x9901 || at + 4 + bytes > end) fail('ZIP64 and encrypted ZIP entries are unsupported'); at += 4 + bytes; } };
    for (let i = 0; i < count; i++) {
      if (p + 46 > eocd || u32(p) !== 0x02014b50) fail();
      const flags = u16(p + 8), method = u16(p + 10), crc = u32(p + 16), compressed = u32(p + 20), expanded = u32(p + 24), names = u16(p + 28), extra = u16(p + 30), comment = u16(p + 32), disk = u16(p + 34), mode = u32(p + 38) >>> 16, local = u32(p + 42);
      const end = p + 46 + names + extra + comment;
      if (end > eocd || disk || flags & ~0x80e || ![0, 8].includes(method) || compressed === 0xffffffff || expanded === 0xffffffff || local === 0xffffffff) fail();
      const nameBytes = raw.subarray(p + 46, p + 46 + names), name = utf8.decode(nameBytes);
      if (!(flags & 0x800) && nameBytes.some(b => b > 127)) fail('Ambiguous ZIP filename encoding');
      const parts = name.split('/'); if (parts[parts.length - 1] === '') parts.pop();
      if (!name || name.length > 512 || name.startsWith('/') || name.includes('\\') || /[\u0000-\u001f\u007f]/.test(name) || parts.some(part => !part || part === '.' || part === '..') || parts.length > 5 || this.entries.has(name) || ![0, 0o100000, 0o040000].includes(mode & 0o170000)) fail('Unsafe or duplicate ZIP path');
      expandedTotal += expanded;
      if (expandedTotal > SLACK_LIMITS.expanded || expanded > Math.max(1024 * 1024, compressed * 200) || name.endsWith('.json') && expanded > SLACK_LIMITS.json) fail('ZIP expanded size or compression ratio exceeds the limit');
      extras(p + 46 + names, extra);
      if (local + 30 > start || u32(local) !== 0x04034b50 || u16(local + 6) !== flags || u16(local + 8) !== method) fail('ZIP local and central headers disagree');
      const localNames = u16(local + 26), localExtra = u16(local + 28), data = local + 30 + localNames + localExtra;
      if (data > start || utf8.decode(raw.subarray(local + 30, local + 30 + localNames)) !== name || data + compressed > start) fail('ZIP local and central paths disagree');
      extras(local + 30 + localNames, localExtra);
      let dataEnd = data + compressed;
      if (flags & 8) { const offset = dataEnd + (u32(dataEnd) === 0x08074b50 ? 4 : 0); if (offset + 12 > start || u32(offset) !== crc || u32(offset + 4) !== compressed || u32(offset + 8) !== expanded) fail('Invalid ZIP data descriptor'); dataEnd = offset + 12; }
      else if (u32(local + 14) !== crc || u32(local + 18) !== compressed || u32(local + 22) !== expanded) fail('ZIP local and central sizes disagree');
      this.entries.set(name, { name, offset: data, compressed, expanded, method, crc, end: dataEnd }); ranges.push([local, dataEnd]); p = end;
    }
    if (p !== eocd) fail(); ranges.sort((a, b) => a[0] - b[0]); for (let i = 1; i < ranges.length; i++) if (ranges[i][0] < ranges[i - 1][1]) fail('Overlapping ZIP entries');
  }
  read(path: string): Uint8Array {
    const entry = this.entries.get(path); if (!entry || path.endsWith('/')) return fail('Slack archive is missing required files');
    const input = this.raw.subarray(entry.offset, entry.offset + entry.compressed), output = new Uint8Array(entry.expanded); let offset = 0;
    if (entry.method === 0) { if (input.length !== output.length) fail(); output.set(input); offset = input.length; }
    else {
      const inflater = new Inflate(chunk => { if (offset + chunk.length > output.length) fail('ZIP entry expanded beyond its declared bound'); output.set(chunk, offset); offset += chunk.length; });
      for (let p = 0; p < input.length; p += 1024) inflater.push(input.subarray(p, p + 1024), p + 1024 >= input.length);
      if (!input.length) inflater.push(input, true);
    }
    if (offset !== entry.expanded || crc32(output) !== entry.crc) fail('ZIP entry failed size or CRC verification');
    return output;
  }
  private json(path: string) { return parseSlackJson(utf8.decode(this.read(path))); }
  async review() {
    this.digest = await slackDigest(this.raw);
    const users = this.json('users.json'), channels = this.json('channels.json');
    if (!Array.isArray(users) || users.length > 5000 || !Array.isArray(channels) || channels.length > SLACK_LIMITS.channels) fail();
    for (const item of users) { const user = object(item), id = string(user.id, 32); if (!idPattern.test(id) || this.labels.has(id)) fail(); const profile = object(user.profile || {}), name = string(profile.display_name || profile.real_name || user.real_name || user.name || id, 200); this.labels.set(id, name); }
    const channelIds = new Set<string>(), channelNames = new Set<string>(); let messageCount = 0, fileBytes = 0;
    for (const item of channels) {
      const value = object(item), id = string(value.id, 32), name = string(value.name, 80), members = value.members || [];
      if (!idPattern.test(id) || channelIds.has(id) || !/^[A-Za-z0-9_-]{1,80}$/.test(name) || channelNames.has(name) || !Array.isArray(members) || members.length > 5000 || members.some(id => typeof id !== 'string' || !this.labels.has(id))) fail();
      channelIds.add(id); channelNames.add(name); const messages = new Map<string, SlackArchiveMessage>();
      for (const path of [...this.entries.keys()].sort()) {
        if (!new RegExp('^' + name + '/\\d{4}-\\d{2}-\\d{2}\\.json$').test(path)) continue;
        const rows = this.json(path); if (!Array.isArray(rows)) fail();
        for (const item of rows) {
          let row = object(item); const changed = row.type === 'message' && row.subtype === 'message_changed';
          if (changed) { const envelope = row; row = { ...object(row.message), type: 'message' }; if (envelope.previous_message != null) { const old = object(envelope.previous_message); if (old.ts !== row.ts) fail(); row.previous_message = old; } }
          if (row.type !== 'message' || ![undefined, null, 'bot_message', 'file_share', 'thread_broadcast'].includes(row.subtype as string)) continue;
          const ts = stamp(row.ts), old = messages.get(ts);
          if (old) { if (!changed) fail('Duplicate Slack message timestamp'); const history = row.edit_history || []; if (!Array.isArray(history)) fail(); row.edit_history = [...old.details.history.map(v => ({ ts: v.timestamp, text: v.text, user: v.author.id })), { ts: old.details.edited_at || ts, text: old.text, user: old.author }, ...history]; row.thread_ts ??= old.parent; }
          const text = string(row.text || '', 18000), author = string(row.user || row.bot_id || 'unknown', 80), parent = row.thread_ts == null ? null : stamp(row.thread_ts), files = row.files || [];
          if (!Array.isArray(files) || files.length > 20) fail();
          const bundled: SlackArchiveMessage['files'] = []; let missing = 0;
          for (const item of files) { const file = object(item), fid = string(file.id, 32); if (!idPattern.test(fid)) fail(); const path = 'files/' + fid, entry = this.entries.get(path); if (entry) { const mime = string(file.mimetype || 'application/octet-stream', 200), filename = string(file.name || fid, 255); if (!allowedFiles.has(mime) || entry.expanded > 50 * 1024 * 1024) fail('Bundled file type or size is unsupported'); if (!this.verifiedFiles.has(path)) { this.read(path); this.verifiedFiles.add(path); } bundled.push({ path, mime, name: filename }); this.bundledFiles++; fileBytes += entry.expanded; if (this.bundledFiles > SLACK_LIMITS.files || fileBytes > SLACK_LIMITS.expanded) fail('Too many bundled file references'); } else { missing++; this.missingFiles++; } }
          if (!text.trim() && !files.length) continue;
          messages.set(ts, { ts, text, author, parent, files: bundled, missing, details: details(row, this.labels) });
          if (!old && ++messageCount > SLACK_LIMITS.messages) fail('Slack archive exceeds 100,000 messages');
        }
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      for (const row of messages.values()) { const root = row.parent ? messages.get(row.parent) : undefined; if (root && root.ts !== row.ts && (root.parent && root.parent !== root.ts || decimalCompare(root.ts, row.ts) > 0)) fail('Slack thread replies must refer to an earlier root message'); }
      this.channels.push({ id, name, members: members as string[], messages: [...messages.values()].sort((a, b) => decimalCompare(a.ts, b.ts)) });
    }
    return this;
  }
}
export async function reviewSlackArchive(file: File) { if (file.size > SLACK_LIMITS.compressed) fail('Choose a Slack ZIP smaller than 50 MB.'); return new SlackArchive(new Uint8Array(await file.arrayBuffer())).review(); }
