import { api } from './api';

export type ConnectionCategory = 'identity' | 'files' | 'calendar' | 'meetings' | 'ai' | 'automation';
export type ConnectionTarget = 'sso' | 'sync' | 'cloud_files' | 'external_meetings' | 'copilot' | 'webhooks' | 'people';
export type ConnectionStatus = 'not_configured' | 'draft' | 'authorization_required' | 'verification_required' | 'ready' | 'paused' | 'attention' | 'unavailable';
export interface SetupConnection { id: string; name: string; status: string; enabled: boolean; revision: number; verified_at: string | null }
export interface SetupField { key: string; label: string; required: boolean; secret: boolean }
export interface ConnectionRecipe {
  id: string; name: string; category: ConnectionCategory; description: string; target: ConnectionTarget;
  provider: string; kind: 'files' | 'calendar' | null; enabled: boolean; can_configure: boolean; requires_channel: boolean; secure_supported: boolean;
  callback_url: string | null; scopes: string[]; docs_url: string | null; prerequisites: string[]; steps: string[];
  fields: SetupField[]; status: ConnectionStatus; blockers: string[]; connections: SetupConnection[];
}
export interface ConnectionSetup {
  workspace_id: string; channel_id: string | null; public_origin: string | null;
  origin_status: 'valid' | 'local_development' | 'invalid'; recipes: ConnectionRecipe[];
}

const invalid = () => new Error('Connection setup returned an invalid or mismatched response.');
function object(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid(); return value as Record<string, unknown>; }
function text(value: unknown, limit = 10000): string { if (typeof value !== 'string' || value.length > limit) throw invalid(); return value; }
function bool(value: unknown): boolean { if (typeof value !== 'boolean') throw invalid(); return value; }
function nullable(value: unknown): string | null { return value === null ? null : text(value); }
function strings(value: unknown): string[] { if (!Array.isArray(value) || value.length > 200) throw invalid(); return value.map(item => text(item)); }
function oneOf<T extends string>(value: unknown, choices: readonly T[]): T { if (typeof value !== 'string' || !choices.includes(value as T)) throw invalid(); return value as T; }

/** Exact provider addresses only: no userinfo, session query or executable URL. */
export function setupAddress(value: string | null, kind: 'callback' | 'documentation'): string | null {
  if (value === null) return null;
  try {
    const address = new URL(value);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(address.hostname);
    if (address.username || address.password || address.search || (kind === 'callback' && address.hash)
      || (address.protocol !== 'https:' && !(kind === 'callback' && local && address.protocol === 'http:'))) throw invalid();
    return value;
  } catch { throw new Error('Connection setup returned an unsafe provider address.'); }
}

/** Validate both scope and shape before allowing a recipe to configure a panel. */
export function parseConnectionSetup(value: unknown, workspaceId: string, channelId?: string | null): ConnectionSetup {
  const root = object(value);
  if (root.workspace_id !== workspaceId || root.channel_id !== (channelId || null)) throw invalid();
  if (!Array.isArray(root.recipes) || root.recipes.length > 100) throw invalid();
  const ids = new Set<string>();
  const recipes = root.recipes.map(value => {
    const row = object(value), id = text(row.id, 128);
    if (!/^[a-z0-9][a-z0-9._-]*$/.test(id) || ids.has(id)) throw invalid();
    ids.add(id);
    if (!Array.isArray(row.connections) || row.connections.length > 500) throw invalid();
    if (!Array.isArray(row.fields) || row.fields.length > 100) throw invalid();
    const fieldKeys = new Set<string>();
    const fields = row.fields.map(value => {
      const field = object(value), key = text(field.key, 128);
      if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(key) || fieldKeys.has(key)) throw invalid();
      fieldKeys.add(key);
      return { key, label: text(field.label, 256), required: bool(field.required), secret: bool(field.secret) };
    });
    const connections = row.connections.map(value => {
      const item = object(value);
      if (!Number.isSafeInteger(item.revision) || (item.revision as number) < 0) throw invalid();
      return { id: text(item.id, 256), name: text(item.name, 256), status: text(item.status, 128), enabled: bool(item.enabled), revision: item.revision as number, verified_at: nullable(item.verified_at) };
    });
    return {
      id, name: text(row.name, 256), description: text(row.description),
      category: oneOf(row.category, ['identity', 'files', 'calendar', 'meetings', 'ai', 'automation'] as const),
      target: oneOf(row.target, ['sso', 'sync', 'cloud_files', 'external_meetings', 'copilot', 'webhooks', 'people'] as const),
      provider: text(row.provider, 128), kind: row.kind === null ? null : oneOf(row.kind, ['files', 'calendar'] as const),
      enabled: bool(row.enabled), can_configure: bool(row.can_configure), requires_channel: bool(row.requires_channel), secure_supported: bool(row.secure_supported),
      callback_url: setupAddress(nullable(row.callback_url), 'callback'), docs_url: setupAddress(nullable(row.docs_url), 'documentation'),
      scopes: strings(row.scopes), prerequisites: strings(row.prerequisites), steps: strings(row.steps),
      status: oneOf(row.status, ['not_configured', 'draft', 'authorization_required', 'verification_required', 'ready', 'paused', 'attention', 'unavailable'] as const),
      fields, blockers: strings(row.blockers), connections,
    };
  });
  return { workspace_id: workspaceId, channel_id: channelId || null, public_origin: nullable(root.public_origin), origin_status: oneOf(root.origin_status, ['valid', 'local_development', 'invalid'] as const), recipes };
}

/** Export static setup instructions only, never connection names, grants or sessions. */
export function connectionRecipeDownload(recipe: ConnectionRecipe): { filename: string; json: string } {
  const document = {
    format: 'freecollab.connection-setup', version: 1, recipe: recipe.id,
    provider: recipe.provider, feature: recipe.target, kind: recipe.kind,
    callback_url: setupAddress(recipe.callback_url, 'callback'), scopes: [...recipe.scopes],
    fields: recipe.fields.map(field => ({ key: field.key, label: field.label, required: field.required, secret: field.secret })),
    prerequisites: [...recipe.prerequisites], steps: [...recipe.steps],
    documentation_url: setupAddress(recipe.docs_url, 'documentation'),
  };
  return { filename: 'freecollab-setup-' + recipe.id.replace(/[^a-z0-9._-]/gi, '-').slice(0, 100) + '.json', json: JSON.stringify(document, null, 2) + '\n' };
}

export const connectionSetupApi = {
  async get(workspaceId: string, channelId?: string | null, signal?: AbortSignal): Promise<ConnectionSetup> {
    const query = channelId ? '?channel_id=' + encodeURIComponent(channelId) : '';
    const value = await api.request<unknown>('/workspaces/' + encodeURIComponent(workspaceId) + '/connection-setup' + query, { signal, cache: 'no-store' });
    return parseConnectionSetup(value, workspaceId, channelId);
  },
};
