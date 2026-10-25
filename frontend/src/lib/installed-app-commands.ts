import { api } from './api';
import type { AppArgument, AppJob } from './app-platform-api';

export interface InstalledAppCommand {
  key: string; installation_id: string; app_id: string; app_name: string;
  generation: number; origin: string; name: string; description: string; arguments: AppArgument[];
  supports_secure_foreground?: boolean; delivery?: 'queued' | 'foreground';
}
const path = (workspaceId: string, channelId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/channels/${encodeURIComponent(channelId)}/apps/commands`;
export const installedAppCommandsApi = {
  list: (workspaceId: string, channelId: string) => api.request<{ commands: InstalledAppCommand[] }>(path(workspaceId, channelId)),
  invoke: (workspaceId: string, channelId: string, command: InstalledAppCommand, arguments_: Record<string, string>, requestId: string) =>
    api.request<AppJob>(path(workspaceId, channelId) + '/' + encodeURIComponent(command.installation_id), {
      method: 'POST', body: JSON.stringify({ request_id: requestId, expected_generation: command.generation,
        command: command.name, arguments: arguments_, confirm: true, disclose_to_app: true }),
    }),
  foreground: (workspaceId: string, channelId: string, command: InstalledAppCommand, arguments_: Record<string, string>, requestId: string, deviceId: string) =>
    api.request<AppJob>(path(workspaceId, channelId).replace(/\/commands$/, '/foreground') + '/' + encodeURIComponent(command.installation_id), {
      method: 'POST', body: JSON.stringify({ request_id: requestId, expected_generation: command.generation,
        command: command.name, arguments: arguments_, confirm: true, disclose_to_app: true,
        disclose_decrypted_content: true, matrix_device_id: deviceId }),
    }),
};

/** Parse fields, never executable shell: quotes and backslash only group text. */
export function parseInstalledAppArguments(command: InstalledAppCommand, raw: string): Record<string, string> {
  if (raw.length > 64_000 || raw.includes('\0')) throw new Error('Installed command arguments are too long.');
  const tokens: string[] = []; let token = '', quote = '', escape = false, started = false;
  for (const character of raw) {
    if (escape) { token += character; escape = false; started = true; continue; }
    if (character === '\\') { escape = true; started = true; continue; }
    if (quote) { if (character === quote) quote = ''; else token += character; started = true; continue; }
    if (character === '"' || character === "'") { quote = character; started = true; continue; }
    if (/\s/u.test(character)) { if (started) tokens.push(token); token = ''; started = false; continue; }
    token += character; started = true;
  }
  if (quote || escape) throw new Error('Close the quoted argument before continuing.');
  if (started) tokens.push(token);
  const result: Record<string, string> = Object.create(null);
  if (tokens.length && tokens.every(value => !value.includes('=')) && command.arguments.length === 1) {
    result[command.arguments[0].name] = tokens.join(' ');
  } else {
    for (const value of tokens) {
      const equal = value.indexOf('=');
      if (equal < 1) throw new Error('Use name=value for each installed command argument.');
      const name = value.slice(0, equal);
      if (!command.arguments.some(argument => argument.name === name) || Object.prototype.hasOwnProperty.call(result, name)) throw new Error('Unknown or repeated installed command argument.');
      result[name] = value.slice(equal + 1);
    }
  }
  for (const argument of command.arguments) {
    const value = result[argument.name] ?? '';
    if (value.length > argument.max_length || argument.required && !value.trim()) throw new Error('An installed command argument is missing or too long.');
    if (value && argument.type === 'number' && (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) || !Number.isFinite(Number(value)))) throw new Error('Enter a finite number.');
    if (value && argument.type === 'boolean' && !['true', 'false'].includes(value)) throw new Error('Use true or false.');
    if (value && argument.type === 'choice' && !argument.choices?.includes(value)) throw new Error('Choose an installed argument value.');
    result[argument.name] = value;
  }
  return result;
}
