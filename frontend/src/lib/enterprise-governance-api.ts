import { api } from './api';
import type { Reauthentication, DataJob } from './data-controls-api';

export interface GovernanceOrganization {
  id: string; name: string; role: 'owner' | 'admin' | 'auditor'; require_sso: boolean; revision: number;
  country_code: string; region_label: string; workspaces: { id: string; name: string }[];
  members?: { user_id: string; role: string }[];
}
export type DLPSurface = 'message' | 'file' | 'app' | 'assistant' | 'search' | 'document';
export interface DLPPolicy {
  id: string; name: string; detector: 'literal' | 'credit_card' | 'aws_access_key' | 'email'; action: 'warn' | 'block';
  surfaces: DLPSurface[]; enabled: boolean; revision: number; block_uninspectable_files: boolean; has_literal: boolean;
}
export interface PreservationCase {
  id: string; title: string; channel_ids: string[]; custodian_ids: string[]; status: 'active' | 'released';
  revision: number; created_at: string; released_at: string | null;
}
export interface GovernanceState {
  organization_id: string | null; policies: DLPPolicy[]; cases: PreservationCase[]; retention_hold: boolean; case_hold_active: boolean;
  kms: { provider: string; enabled: boolean; configured: boolean; automatic_rotation: false };
}
export interface UsageRow { workspace_id: string; members: number; channels: number; secure_channels: number; native_messages: number; stored_file_bytes: number }
export interface GovernanceAudit { id: string; actor_id: string; action: string; target_id: string; created_at: string }
export type CaseCollection = DataJob & { review_state: string; reviewed_at: string | null };
const base = (id: string) => '/workspaces/' + encodeURIComponent(id) + '/governance';
const org = (id: string) => '/organizations/' + encodeURIComponent(id);
const body = (data: unknown, method = 'POST') => ({ method, body: JSON.stringify(data) });
const cases = (id: string, caseId: string) => base(id) + '/cases/' + encodeURIComponent(caseId);
export const governanceApi = {
  state: (id: string) => api.request<GovernanceState>(base(id)),
  organizations: () => api.request<{ organizations: GovernanceOrganization[] }>('/organizations'),
  organization: (id: string) => api.request<GovernanceOrganization>(org(id)),
  createOrganization: (data: Reauthentication & { name: string; country_code: string; region_label: string }) => api.request<GovernanceOrganization>('/organizations', body(data)),
  link: (id: string, workspace: string, auth: Reauthentication) => api.request<GovernanceOrganization>(org(id) + '/workspaces/' + encodeURIComponent(workspace), body(auth)),
  unlink: (id: string, workspace: string, auth: Reauthentication) => api.request(org(id) + '/workspaces/' + encodeURIComponent(workspace), body(auth, 'DELETE')),
  policy: (id: string, data: Reauthentication & { expected_revision: number; require_sso: boolean; country_code: string; region_label: string }) => api.request<GovernanceOrganization>(org(id) + '/policy', body(data, 'PUT')),
  member: (id: string, user: string, role: string, auth: Reauthentication) => api.request(org(id) + '/members/' + encodeURIComponent(user), body({ ...auth, role }, 'PUT')),
  usage: (id: string) => api.request<{ workspaces: UsageRow[] }>(org(id) + '/usage'),
  createDLP: (id: string, data: Reauthentication & Omit<DLPPolicy, 'id' | 'revision' | 'has_literal'> & { literal?: string }) => api.request<DLPPolicy>(base(id) + '/dlp', body(data)),
  updateDLP: (id: string, policy: DLPPolicy, enabled: boolean, auth: Reauthentication) => api.request<DLPPolicy>(base(id) + '/dlp/' + encodeURIComponent(policy.id), body({ ...auth, name: policy.name, detector: policy.detector, action: policy.action, surfaces: policy.surfaces, block_uninspectable_files: policy.block_uninspectable_files, enabled, expected_revision: policy.revision }, 'PUT')),
  createCase: (id: string, data: Reauthentication & { title: string; channel_ids: string[]; custodian_ids: string[]; acknowledge_native_scope: true }) => api.request<PreservationCase>(base(id) + '/cases', body(data)),
  release: (id: string, item: PreservationCase, auth: Reauthentication) => api.request<PreservationCase>(cases(id, item.id) + '/release', body({ ...auth, expected_revision: item.revision, confirmation: 'RELEASE PRESERVATION' })),
  collect: (id: string, item: PreservationCase, channelIds: string[], auth: Reauthentication) => api.request<DataJob>(cases(id, item.id) + '/exports', body({ ...auth, channel_ids: channelIds, acknowledge_plaintext_export: true })),
  collections: (id: string, caseId: string) => api.request<{ exports: CaseCollection[] }>(cases(id, caseId) + '/exports'),
  review: (id: string, caseId: string, jobId: string, review_state: string, auth: Reauthentication) => api.request(cases(id, caseId) + '/exports/' + encodeURIComponent(jobId) + '/review', body({ ...auth, review_state })),
  audit: (id: string) => api.request<{ events: GovernanceAudit[] }>(base(id) + '/audit'),
};
