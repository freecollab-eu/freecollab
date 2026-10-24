import type { SyncConnection, SyncKind, SyncProvider } from './enterprise-api';
import type { CopilotAgent } from './copilot-api';

export interface SyncRecipe { provider?: SyncProvider; kind?: SyncKind; channelId?: string }
export function matchingSyncRecipe(connection: Pick<SyncConnection, 'provider' | 'kind' | 'channel_id'>, recipe: SyncRecipe): boolean {
  return (!recipe.provider || connection.provider === recipe.provider) && (!recipe.kind || connection.kind === recipe.kind)
    && (!recipe.channelId || connection.channel_id === recipe.channelId);
}
export function syncRecipeDraft(recipe: SyncRecipe, availableChannelIds: readonly string[]) {
  const provider = recipe.provider || 'google', kind = provider === 'caldav' ? 'calendar' : recipe.kind || 'files';
  const providerName = { google: 'Google', microsoft: 'Microsoft', nextcloud: 'Nextcloud', caldav: 'CalDAV' }[provider];
  return { provider, kind, name: providerName + (kind === 'files' ? ' files' : ' calendar'),
    channelId: recipe.channelId ? availableChannelIds.includes(recipe.channelId) ? recipe.channelId : '' : availableChannelIds[0] || '',
    // No folder/drive/tenant/server/account is guessed. Google's documented
    // primary calendar identifier remains editable before explicit consent.
    config: (provider === 'google' && kind === 'calendar' ? { calendar_id: 'primary' } : {}) as Record<string, string> };
}
export function stableDraft(value: unknown): string {
  const canonical = (item: unknown): unknown => Array.isArray(item) ? item.map(canonical) : item && typeof item === 'object'
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b)).map(([key, child]) => [key, canonical(child)])) : item;
  return JSON.stringify(canonical(value));
}
export const copilotPresets = [
  { id: 'openai-mini', label: 'OpenAI · GPT-4o mini', endpoint: 'https://api.openai.com/v1/chat/completions', model: 'gpt-4o-mini' },
  { id: 'openai-41-mini', label: 'OpenAI · GPT-4.1 mini', endpoint: 'https://api.openai.com/v1/chat/completions', model: 'gpt-4.1-mini' },
  { id: 'xai-grok', label: 'Grok · xAI', endpoint: 'https://api.x.ai/v1/chat/completions', model: 'grok-4.20-0309-non-reasoning' },
  { id: 'custom', label: 'Your OpenAI-compatible service', endpoint: '', model: '' },
] as const;
export function copilotPresetFor(endpoint: string, model: string): string {
  return copilotPresets.find(preset => preset.id !== 'custom' && preset.endpoint === endpoint && preset.model === model)?.id || 'custom';
}
export function copilotDraftSnapshot(enabled: boolean, endpoint: string, model: string, agents: readonly CopilotAgent[]) {
  return stableDraft({ enabled, endpoint, model, agents: agents.map(agent => ({ ...agent, tools: [...agent.tools].sort() })) });
}
