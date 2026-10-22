import { api, type Channel } from '../lib/api';
import { nativePushPlatform } from '../lib/native-push';
export interface NativeCallState { configured: boolean; device_id?: string; phase: 'idle' | 'ringing' | 'connecting' | 'active'; muted: boolean; held: boolean; audio_e2ee: false }
export interface NativeCallingBridge {
  prepareCalling(): Promise<void>;
  configureCalling(value: { device_id: string; call_secret: string }): Promise<void>;
  getCallState(): Promise<NativeCallState>;
  joinCall(value: { room_id: string; explicit_answer: true }): Promise<void>;
  answerCall(value: { explicit_answer: true }): Promise<void>;
  muteCall(value: { muted: boolean }): Promise<void>;
  endCall(): Promise<void>;
  disableCalling(): Promise<void>;
}
interface Capacitor { isNativePlatform?(): boolean; registerPlugin?(name: string): NativeCallingBridge }
let cached: NativeCallingBridge | null = null;
export function nativeCallingBridge(): NativeCallingBridge | null {
  const capacitor = window.Capacitor as unknown as Capacitor | undefined;
  if (!capacitor?.isNativePlatform?.() || !capacitor.registerPlugin) return null;
  return cached ??= capacitor.registerPlugin('FreeCollabCalling');
}
export async function enableNativeCalling() {
  const plugin = nativeCallingBridge(), source = api.getAccessToken();
  if (!plugin || !source) throw new Error('Use the connected mobile app to enable native calling.');
  await plugin.prepareCalling();
  if (api.getAccessToken() !== source) throw new Error('Your sign-in changed. Enable native calling again.');
  const registrations = await api.request<{items: {id:string;platform:string;current_session:boolean;active:boolean;confirmed:boolean;calls_enabled:boolean}[]}>('/mobile/native/devices');
  const push = registrations.items.find(row => row.platform === nativePushPlatform() && row.current_session && row.active && row.confirmed && row.calls_enabled);
  if (!push) throw new Error('Enable and confirm incoming call alerts on this device first.');
  const credential = await api.request<{device_id:string;call_secret:string}>('/mobile/native/calling/devices', {method:'POST',body:JSON.stringify({push_device_id:push.id,consent:true})});
  if (api.getAccessToken() !== source) { await plugin.disableCalling(); throw new Error('Your sign-in changed. Enable native calling again.'); }
  // One-time purpose-limited credential goes directly to the OS secure store;
  // never localStorage, IndexedDB, query strings, telemetry or account headers.
  await plugin.configureCalling({device_id:credential.device_id,call_secret:credential.call_secret});
  if (api.getAccessToken() !== source) { await plugin.disableCalling(); throw new Error('Your sign-in changed. Enable native calling again.'); }
  window.dispatchEvent(new Event('freecollab:mobile-state'));
}
export async function acknowledgeNativeAudio(channel: Pick<Channel,'id'>) {
  const plugin = nativeCallingBridge(), source = api.getAccessToken();
  const state = await plugin?.getCallState();
  if (!state?.configured || !state.device_id || !source) throw new Error('Enable native calling on this device first.');
  const result = await api.request(`/mobile/native/calling/devices/${encodeURIComponent(state.device_id)}/channels/${encodeURIComponent(channel.id)}/media-consent`, {method:'POST',body:JSON.stringify({acknowledge_customer_sfu_audio_not_e2ee:true})});
  if (api.getAccessToken() !== source) { await plugin?.disableCalling(); throw new Error('Your sign-in changed. Enable native calling again.'); }
  return result;
}
