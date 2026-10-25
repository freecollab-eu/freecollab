import { api } from './api';
export type NativeOpenKind = 'channel' | 'message' | 'task' | 'project' | 'document' | 'linked-document' | 'file' | 'clip' | 'decision' | 'feed' | 'calendar' | 'meeting' | 'stack' | 'workflow' | 'workflow-run';
export interface NativeOpenSource { workspace_id: string; channel_id: string; kind: NativeOpenKind; item_id: string; project_id?: string }
const kinds = new Set(['channel','message','task','project','document','linked-document','file','clip','decision','feed','calendar','meeting','stack','workflow','workflow-run']);
const identifier = /^[A-Za-z0-9_-]{1,128}$/, item = /^[A-Za-z0-9_$:./=-]{1,256}$/;
export function validateNativeOpenSource(value: unknown): NativeOpenSource {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid app link.');
  const source = value as NativeOpenSource, keys = Object.keys(source);
  if (keys.some(key => !['workspace_id','channel_id','kind','item_id','project_id'].includes(key))
      || typeof source.workspace_id !== 'string' || typeof source.channel_id !== 'string'
      || !identifier.test(source.workspace_id) || !identifier.test(source.channel_id) || !kinds.has(source.kind)
      || typeof source.item_id !== 'string' || !item.test(source.item_id)
      || source.project_id !== undefined && (typeof source.project_id !== 'string' || !identifier.test(source.project_id)) || source.kind === 'task' && !source.project_id)
    throw new Error('Invalid app link scope or resource.');
  return { workspace_id: source.workspace_id, channel_id: source.channel_id, kind: source.kind, item_id: source.item_id,
    ...(source.project_id ? { project_id: source.project_id } : {}) };
}
export function parseNativeLink(value: string, installation = location.origin): NativeOpenSource {
  if (typeof value !== 'string' || value.length > 2048) throw new Error('Invalid app link.');
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.origin !== installation || url.username || url.password || url.hash || url.pathname !== '/open')
    throw new Error('The app link belongs to another installation.');
  const source: Record<string, string> = Object.create(null);
  url.searchParams.forEach((value, key) => { if (Object.prototype.hasOwnProperty.call(source, key)) throw new Error('Repeated app link field.'); source[key] = value; });
  return validateNativeOpenSource(source);
}
export function parseOpenFragment(fragment: string): NativeOpenSource | null {
  if (!fragment.startsWith('#open=')) return null;
  if (fragment.length > 4096 || !/^[A-Za-z0-9_-]+$/.test(fragment.slice(6))) throw new Error('Invalid app resource fragment.');
  const raw = fragment.slice(6).replace(/-/g, '+').replace(/_/g, '/');
  return validateNativeOpenSource(JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(raw), value => value.charCodeAt(0)))));
}
export function nativeSourceFromLocation(): NativeOpenSource | null {
  return location.pathname === '/open' ? parseNativeLink(location.href) : parseOpenFragment(location.hash);
}
const pendingKey = 'freecollab.native-source-hint.v1';
export interface PendingNativeSource { source: NativeOpenSource; generation: string }
interface StoredHint extends PendingNativeSource { origin: string; created_at: number; actor: string | null }
let memoryHint: StoredHint | null = null, ephemeralHint: StoredHint | null = null;
function writeHint(value:StoredHint|null){
  // The demo and embedded app never borrow or remove the standalone account's
  // pending IDs, even when hosted on the same origin in another application.
  if(api.isMemorySession()){ephemeralHint=value;return;}
  memoryHint=value;
  try{if(value)sessionStorage.setItem(pendingKey,JSON.stringify(value));else sessionStorage.removeItem(pendingKey);}catch{}
}
export function rememberNativeSource(value: NativeOpenSource): PendingNativeSource {
  const source = validateNativeOpenSource(value), hint: StoredHint = { source, generation: crypto.randomUUID(),
    origin: location.origin, created_at: Date.now(), actor: api.getAccessToken() ? api.getCurrentUserId() : null };
  writeHint(hint);return {source:hint.source,generation:hint.generation};
}
export function pendingNativeSource(): PendingNativeSource | null {
  try{
    let hint=api.isMemorySession()?ephemeralHint:memoryHint;
    if(!api.isMemorySession())try{const raw=sessionStorage.getItem(pendingKey);if(raw)hint=JSON.parse(raw)as StoredHint;}catch{}
    if(!hint)return null;
    const source=validateNativeOpenSource(hint.source),actor=api.getAccessToken()?api.getCurrentUserId():null;
    if(hint.origin!==location.origin||typeof hint.created_at!=='number'||hint.created_at>Date.now()+30000||Date.now()-hint.created_at>600000
      ||!/^[A-Fa-f0-9-]{36}$/.test(hint.generation)||hint.actor&&actor&&hint.actor!==actor){writeHint(null);return null;}
    if(!hint.actor&&actor){hint={...hint,actor};writeHint(hint);}
    return {source,generation:hint.generation};
  }catch{writeHint(null);return null;}
}
export function clearNativeSource(expected: PendingNativeSource) {
  const pending=pendingNativeSource();
  if(!pending||pending.generation!==expected.generation||JSON.stringify(pending.source)!==JSON.stringify(validateNativeOpenSource(expected.source)))return;
  writeHint(null);
}
export async function authorizeNativeSource(source: NativeOpenSource) {
  source = validateNativeOpenSource(source);
  const token = api.getAccessToken(), actor = api.getCurrentUserId();
  if (!token || !actor) throw new Error('Sign in to open this app link.');
  const channel = await api.getChannel(source.channel_id);
  if (token !== api.getAccessToken() || actor !== api.getCurrentUserId()) throw new Error('The signed-in account changed.');
  if (channel.id !== source.channel_id || channel.workspace_id !== source.workspace_id) throw new Error('This app link is no longer available.');
  return source;
}
