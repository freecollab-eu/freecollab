import { useEffect, useRef } from 'react';
import { api } from './api';
import { connectionSetupApi } from './connection-setup-api';
import type { CloudProvider } from './cloud-files-api';
import { providerNames, type ExternalMeetingProvider } from './external-meetings-api';

const cloudNames: Record<CloudProvider, string> = { google: 'Google Drive', microsoft: 'OneDrive', dropbox: 'Dropbox', box: 'Box' };
export function freshCloudApplication(provider: CloudProvider = 'google', preset = false) {
  return { name: preset ? cloudNames[provider] + ' files' : '', provider, client_id: '', client_secret: '',
    folder_id: preset && provider === 'box' ? '0' : '', tenant_id: '', drive_id: '', password: '', code: '', consent: false };
}
export function freshMeetingApplication(provider: ExternalMeetingProvider = 'google_meet', preset = false) {
  return { name: preset ? providerNames[provider] + ' meetings' : '', provider, clientId: '', clientSecret: '',
    calendarId: preset && provider === 'google_meet' ? 'primary' : '', tenantId: '', source: '', editingId: '' };
}

export const customerAppConsoles: Record<CloudProvider | ExternalMeetingProvider, string> = {
  google: 'https://console.cloud.google.com/apis/credentials',
  microsoft: 'https://entra.microsoft.com/', dropbox: 'https://www.dropbox.com/developers/apps', box: 'https://developer.box.com/console',
  google_meet: 'https://console.cloud.google.com/apis/credentials', microsoft_teams: 'https://entra.microsoft.com/', zoom: 'https://marketplace.zoom.us/develop/create',
};

/** Server-advertised callback only: never infer it from the browser's origin. */
export async function meetingSetupCallback(workspaceId: string, channelId: string, provider: ExternalMeetingProvider) {
  const token = api.getAccessToken();
  const result = await connectionSetupApi.get(workspaceId, channelId);
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  return validSetupCallback(result.recipes.find(row => row.id === 'meetings.' + provider)?.callback_url || '');
}
export function validSetupCallback(value: string) {
  try {
    if (typeof value !== 'string' || !value || value.length > 8192 || /[\s\\\x00-\x1f\x7f]/.test(value)) return '';
    const url = new URL(value);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if ((url.protocol !== 'https:' && !(local && url.protocol === 'http:')) || url.username || url.password || url.search || url.hash) return '';
    return value;
  } catch { return ''; }
}

export interface SetupFormSignals { onGuardChange?: (guard: 'busy' | null) => void; onDirtyChange?: (dirty: boolean) => void }
export function useSetupFormSignals(busy: boolean, dirty: boolean, { onGuardChange, onDirtyChange }: SetupFormSignals) {
  const callbacks = useRef({ onGuardChange, onDirtyChange }); callbacks.current = { onGuardChange, onDirtyChange };
  useEffect(() => { callbacks.current.onGuardChange?.(busy ? 'busy' : null); }, [busy]);
  useEffect(() => { callbacks.current.onDirtyChange?.(dirty); }, [dirty]);
  useEffect(() => () => { callbacks.current.onGuardChange?.(null); callbacks.current.onDirtyChange?.(false); }, []);
}
