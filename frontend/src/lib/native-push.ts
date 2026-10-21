import { api } from './api';

export interface NativePushItem { key: string; type: 'subscription_confirmation' | 'activity' | 'incoming_call'; id?: string; device_id?: string; challenge?: string }
interface NativePushBridge {
  registerPush(): Promise<{ platform: 'android' | 'ios'; token: string }>;
  refreshPush(): Promise<{ platform: 'android' | 'ios'; token: string }>;
  unregisterPush(): Promise<void>;
  getPendingPush(): Promise<{ items: NativePushItem[] }>;
  clearPush(value: { key: string }): Promise<void>;
}
interface NativeCapacitor { getPlatform?(): string; isNativePlatform?(): boolean; registerPlugin?(name: string): NativePushBridge }
let bridge: NativePushBridge | null = null;
export function nativePushPlatform(): 'android' | 'ios' | null {
  const capacitor = window.Capacitor as unknown as NativeCapacitor | undefined;
  const platform = capacitor?.getPlatform?.();
  return platform === 'android' || platform === 'ios' ? platform : null;
}
export function nativePushBridge(): NativePushBridge | null {
  const capacitor = window.Capacitor as unknown as NativeCapacitor | undefined;
  if (!capacitor?.isNativePlatform?.() || !capacitor.registerPlugin) return null;
  return bridge ??= capacitor.registerPlugin('FreeCollabPush');
}
export function validNativePushItem(value: unknown): value is NativePushItem {
  if (!value || typeof value !== 'object') return false;
  const item = value as NativePushItem;
  if (item.type === 'subscription_confirmation') return typeof item.device_id === 'string' && /^[a-f0-9]{32}$/.test(item.device_id)
    && item.key === item.device_id && typeof item.challenge === 'string' && /^[A-Za-z0-9_-]{43}$/.test(item.challenge);
  return (item.type === 'activity' || item.type === 'incoming_call') && typeof item.id === 'string'
    && /^[a-f0-9]{32}$/.test(item.id) && item.key === item.id;
}
export async function enableNativePush(calls: boolean) {
  const plugin = nativePushBridge(), identity = api.getAccessToken();
  if (!plugin || !identity) throw new Error('Use the connected native mobile app to enable native notifications.');
  const registration = await plugin.registerPush();
  if (identity !== api.getAccessToken()) { await plugin.unregisterPush(); throw new Error('Your sign-in changed. Enable notifications again.'); }
  await api.request('/mobile/native/devices', { method: 'POST', body: JSON.stringify({ ...registration, calls_enabled: calls, consent: true }) });
  if (identity !== api.getAccessToken()) { await plugin.unregisterPush(); throw new Error('Your sign-in changed. Enable notifications again.'); }
  window.dispatchEvent(new Event('freecollab:mobile-state'));
}
