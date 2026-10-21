/** Customer-owned OIDC helpers. Construct addresses locally; never discover or save. */
export type SSOPresetId = 'generic' | 'entra' | 'google' | 'okta' | 'auth0' | 'keycloak';
export type SSOSecretHandling = 'retain' | 'replace' | 'remove';
export const defaultSSOScopes = 'openid profile email';
export const ssoPresets = [
  { id: 'generic', name: 'Generic OpenID Connect', guide: 'Copy the exact issuer from your provider’s discovery document, including its path and trailing slash. Configure a confidential web client unless your provider explicitly supports a public client.' },
  { id: 'entra', name: 'Microsoft Entra ID', guide: 'Create a single-tenant app registration with a Web redirect URI. Use the Directory (tenant) ID and the client secret value, not its secret ID. Leave public client flows disabled for this confidential web app.' },
  { id: 'google', name: 'Google Workspace', guide: 'Create a Web application OAuth client in your own Google Cloud project. Set the consent audience to your organization when appropriate. Google uses a shared issuer; the OAuth client and its credentials belong to your organization.' },
  { id: 'okta', name: 'Okta', guide: 'Create an OIDC Web Application with Authorization Code and a client secret. Use the organization issuer, or enter the custom authorization server ID supplied by your Okta administrator.' },
  { id: 'auth0', name: 'Auth0', guide: 'Create a Regular Web Application with Authorization Code enabled. Enter its exact tenant or custom domain and use that app’s client secret. Auth0 issuer identifiers end with a slash.' },
  { id: 'keycloak', name: 'Keycloak', guide: 'Create an OpenID Connect client with Standard flow and Client authentication enabled. Require PKCE S256. Enter the server base URL and realm, rather than an administration-console URL.' },
] as const satisfies readonly { id: SSOPresetId; name: string; guide: string }[];

export interface SSOPresetFields { tenant: string; domain: string; authorizationServer: string; baseUrl: string; realm: string }
export const emptySSOPresetFields = (): SSOPresetFields => ({ tenant: '', domain: '', authorizationServer: '', baseUrl: '', realm: '' });

function inputValue(raw: string): string {
  if (/[\u0000-\u001f\u007f]/.test(raw)) throw new Error('Use values without control characters.');
  return raw.trim();
}
function domainName(raw: string): string {
  const value = inputValue(raw).toLowerCase(), labels = value.split('.');
  if (value.length > 253 || !value.includes('.') || /[^a-z0-9.-]/.test(value) ||
      value.split('.').some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ||
      !/[a-z]/.test(labels[labels.length - 1] || '') ||
      /(?:^|\.)(?:localhost|local|internal)$/.test(value) || value.endsWith('.home.arpa')) {
    throw new Error('Enter a public domain name without a scheme, port, credentials or path.');
  }
  return value;
}

/** Structural check only; the backend resolves and authorizes public addresses.
 * Preserve the issuer identifier: URL.href would change encoded paths/slashes. */
export function validateSSOIssuer(value: string): string {
  const invalid = () => new Error('Use a public HTTPS issuer on port 443, without credentials, query parameters or a fragment.');
  if (value.length > 2048 || /[\s\\\u0000-\u001f\u007f]/.test(value)) throw invalid();
  const match = /^https:\/\/([^/?#]+)(\/[^?#]*)?$/i.exec(value);
  if (!match || /[@%]/.test(match[1])) throw invalid();
  try {
    const authority = /^(\[[0-9a-f:.]+\]|[^:\[\]]+)(?::([0-9]+))?$/i.exec(match[1]);
    if (!authority || (authority[2] !== undefined && Number(authority[2]) !== 443)) throw invalid();
    if (authority[1].startsWith('[')) {
      // Use the URL parser for IPv6 syntax, without serializing the issuer.
      new URL('https://' + authority[1]);
    } else {
      const raw = authority[1], host = /[^\x00-\x7f]/.test(raw) ? new URL('https://' + raw).hostname : raw.toLowerCase();
      if (host.length > 253 || !host.includes('.') || host.split('.').some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)) ||
          /(?:^|\.)(?:localhost|local|internal)$/.test(host) || host.endsWith('.home.arpa')) throw invalid();
    }
  } catch { throw invalid(); }
  return value;
}

export function buildSSOPresetIssuer(preset: Exclude<SSOPresetId, 'generic'>, fields: SSOPresetFields): string {
  if (preset === 'google') return 'https://accounts.google.com';
  if (preset === 'entra') {
    const tenant = inputValue(fields.tenant).toLowerCase();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(tenant)) {
      throw new Error('Enter your organization’s Directory (tenant) ID as a UUID, rather than common, organizations or a tenant name.');
    }
    return `https://login.microsoftonline.com/${tenant}/v2.0`;
  }
  if (preset === 'auth0') return `https://${domainName(fields.domain)}/`;
  if (preset === 'okta') {
    const domain = domainName(fields.domain), server = inputValue(fields.authorizationServer);
    if (server && !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(server)) {
      throw new Error('Enter an authorization server ID without slashes, spaces or URL parameters.');
    }
    return `https://${domain}${server ? '/oauth2/' + server : ''}`;
  }
  if (preset === 'keycloak') {
    const base = validateSSOIssuer(inputValue(fields.baseUrl)).replace(/\/+$/, '');
    domainName(new URL(base).hostname);
    // Literal, unambiguous context segments only. Complex legacy paths remain
    // available through the generic issuer input without guessing their meaning.
    const path = /^https:\/\/[^/]+(.*)$/i.exec(base)?.[1] || '';
    if (path && path.split('/').slice(1).some(segment => !/^[a-zA-Z0-9_~.-]+$/.test(segment) || ['.', '..', 'realms', 'admin', '.well-known'].includes(segment.toLowerCase()))) {
      throw new Error('Enter the Keycloak server base URL, optionally including its context path, without realms or admin paths.');
    }
    const realm = inputValue(fields.realm);
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.~-]{0,127}$/.test(realm)) {
      throw new Error('Enter a realm name using letters, numbers, dots, hyphens, underscores or tildes.');
    }
    return `${base}/realms/${realm}`;
  }
  throw new Error('Choose a supported identity provider preset.');
}

export interface SSODraft { name: string; issuer: string; clientId: string; scopes: string; secretHandling: SSOSecretHandling; secret: string; discardSavedSecret: boolean }
export interface SSOSaved { name: string; issuer_url: string; client_id: string; scopes: string; has_secret: boolean }
export function ssoDraftIsDirty(saved: SSOSaved | null | undefined, draft: SSODraft): boolean {
  return draft.name !== (saved?.name || '') || draft.issuer !== (saved?.issuer_url || '') ||
    draft.clientId !== (saved?.client_id || '') || draft.scopes !== (saved?.scopes || defaultSSOScopes) ||
    draft.secretHandling !== (saved ? (saved.has_secret ? 'retain' : 'remove') : 'replace') || !!draft.secret ||
    (!!saved?.has_secret && draft.discardSavedSecret);
}

/** A preset/app change cannot implicitly reuse a previously stored credential. */
export function ssoSecretInput(mode: SSOSecretHandling, secret: string, discardSavedSecret: boolean): { client_secret?: string | null } {
  if (mode === 'remove' || (mode === 'retain' && discardSavedSecret)) return { client_secret: null };
  if (mode === 'replace') return { client_secret: secret || (discardSavedSecret ? null : undefined) };
  return {};
}
