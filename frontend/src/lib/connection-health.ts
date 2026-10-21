import type { ConnectionRecipe, ConnectionStatus } from './connection-setup-api';

export type ConnectionHealthFilter = 'configured' | 'follow_up' | 'all';
export const connectionStatusLabels: Record<ConnectionStatus, string> = {
  not_configured: 'Not configured', draft: 'Draft saved', authorization_required: 'Account authorization needed',
  verification_required: 'Explicit test needed', ready: 'Configuration ready', paused: 'Paused', attention: 'Needs attention', unavailable: 'Unavailable',
};
const followUp = new Set<ConnectionStatus>(['draft', 'authorization_required', 'verification_required', 'attention']);
export function connectionHealthSummary(recipes: readonly ConnectionRecipe[]) {
  return {
    configured: recipes.filter(row => row.connections.length > 0).length,
    ready: recipes.filter(row => row.status === 'ready').length,
    followUp: recipes.filter(row => followUp.has(row.status)).length,
    paused: recipes.filter(row => row.status === 'paused').length,
  };
}
export function connectionHealthRows(recipes: readonly ConnectionRecipe[], filter: ConnectionHealthFilter): ConnectionRecipe[] {
  return recipes.filter(row => filter === 'all' || (filter === 'follow_up' ? followUp.has(row.status) : row.connections.length > 0))
    .sort((a, b) => Number(followUp.has(b.status)) - Number(followUp.has(a.status)) || a.name.localeCompare(b.name));
}
export function connectionNextAction(status: ConnectionStatus): string {
  switch (status) {
    case 'authorization_required': return 'Authorize the customer account';
    case 'verification_required': return 'Review and run an explicit test';
    case 'attention': return 'Review current access and repair the connection';
    case 'draft': return 'Finish the saved draft';
    case 'paused': return 'Review the paused configuration';
    case 'unavailable': return 'Review installation requirements';
    case 'ready': return 'Review saved configuration';
    default: return 'Prepare this connection';
  }
}
