import { api } from './api';
import { registerSecureFileProvider } from './files-api';
let owner: string | null | undefined;
let loading: Promise<typeof import('./secure')> | undefined;
export async function getSecureMessaging() {
  loading ||= import('./secure').then(async module => {
    const { secureFileProvider } = await import('./secure/files');
    registerSecureFileProvider(secureFileProvider);
    return module;
  });
  const module = await loading;
  const token = api.getAccessToken();
  if (owner !== token) { module.secureMessaging.stop(); owner = token; }
  module.configureSecureMessaging({ baseUrl: api.getBaseUrl(), getAccessToken: () => api.getAccessToken(), getUserId: () => api.getCurrentUserId() });
  return module.secureMessaging;
}
window.addEventListener('freecollab:auth-changed', () => {
  if (loading) void loading.then(module => { module.secureMessaging.stop(); owner = api.getAccessToken(); });
});
