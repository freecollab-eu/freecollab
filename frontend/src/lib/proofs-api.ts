import { api } from './api';
import { downloadChannelFile, type ManagedFile } from './files-api';
import type { Decision } from './collaboration-api';

export interface VersionProof {
  id: string;
  channel_id: string;
  target_kind: 'file' | 'decision';
  target_id: string;
  sha256: string;
  content_binding: 'server_bytes' | 'client_asserted';
  requested_by: string;
  provider_origin: string;
  state: 'queued' | 'processing' | 'pending' | 'proof_available' | 'failed';
  receipt_id: string | null;
  provider_timestamp: string | null;
  attempts: number;
  failure: string | null;
  created_at: string;
  updated_at: string;
  next_attempt_at: string;
  commitment_checked: boolean;
  bitcoin_attestation_present: boolean;
  bitcoin_verified: boolean;
  proof_available: boolean;
}
export type ProofTarget = { file: ManagedFile; decision?: never } | { decision: Decision; file?: never };
export function proofTarget(target: ProofTarget) {
  return target.file ? { channelId: target.file.channel_id, kind: 'file' as const, id: target.file.secure_locator || target.file.id }
    : { channelId: target.decision.channel_id, kind: 'decision' as const, id: target.decision.revision_id };
}

export function decisionTimestampBytes(decision: Decision): Uint8Array<ArrayBuffer> {
  if (decision.resolution_error || typeof decision.title !== 'string' || typeof decision.content !== 'string' || !decision.status) {
    throw new Error('Unlock this exact decision revision before requesting a timestamp.');
  }
  return new TextEncoder().encode(JSON.stringify([
    'freecollab-decision-v1', decision.id, decision.revision,
    decision.title, decision.content, decision.status,
    decision.responsible_user_id || null, decision.decision_date || null, decision.source_message_id || null,
  ]));
}

export async function timestampDigest(target: ProofTarget): Promise<string> {
  const token = api.getAccessToken();
  const bytes = target.file ? await (await downloadChannelFile(target.file)).arrayBuffer() : decisionTimestampBytes(target.decision);
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('');
}

const segment = encodeURIComponent;
export const proofsApi = {
  status() { return api.request<{ enabled: boolean; provider_url: string; automatic_bitcoin_verification: false }>('/proofs/status'); },
  list(target: ProofTarget) {
    const { channelId, kind, id } = proofTarget(target);
    return api.request<VersionProof[]>(`/channels/${segment(channelId)}/proofs?target_kind=${kind}&target_id=${segment(id)}`);
  },
  request(target: ProofTarget, sha256: string) {
    const { channelId, kind, id } = proofTarget(target);
    return api.request<VersionProof>(`/channels/${segment(channelId)}/proofs`, { method: 'POST', body: JSON.stringify({ target_kind: kind, target_id: id, sha256, consent: true }) });
  },
  refresh(id: string) { return api.request<VersionProof>(`/proofs/${segment(id)}/refresh`, { method: 'POST' }); },
  async download(id: string) {
    const token = api.getAccessToken();
    const response = await fetch(`${api.getBaseUrl()}/proofs/${segment(id)}/download`, { headers: token ? { Authorization: 'Bearer ' + token } : {} });
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    if (!response.ok) {
      if (response.status === 401 && token) window.dispatchEvent(new Event('freecollab:session-expired'));
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.detail === 'string' ? data.detail : 'Timestamp proof download failed.');
    }
    const blob = await response.blob();
    if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
    return blob;
  },
};
