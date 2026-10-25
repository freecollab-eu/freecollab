import { api } from './api';
import type { CopilotSource } from './copilot-api';

export type AIProtocol = 'openai' | 'xai' | 'anthropic' | 'azure' | 'gemini' | 'ollama';
export type AIOperation = 'draft' | 'summarize' | 'task_plan' | 'group' | 'compare' | 'answer';
export interface AIModelProfile {
  id: string; name: string; revision: number; protocol: AIProtocol; endpoint: string; model: string;
  enabled: boolean; configured: boolean; consented: boolean; key_source: 'workspace' | 'personal';
  max_output_tokens: number; input_cost_per_million_micros: number; output_cost_per_million_micros: number;
  personal_key_configured?: boolean;
  editable?: boolean;
  instructions?: string;
  api_version?: string;
}
export interface AIBudget {
  revision: number; period: 'month'; max_tokens: number; max_cost_micros: number;
  used_tokens: number; reserved_tokens: number; used_cost_micros: number; reserved_cost_micros: number;
  currency: 'USD'; cost_is_estimate: boolean;
  default_input_cost_per_million_micros: number; default_output_cost_per_million_micros: number;
}
export interface AIUsage { input_tokens: number; output_tokens: number; total_tokens: number; estimated_cost_micros: number; usage_reported: boolean }
export interface AIRunStep { index: number; status: string; attempts: number; usage?: AIUsage; error?: string }
export interface AIGroup { title?: string; label?: string; source_ids: string[]; reason?: string }
export interface AIRunResult {
  text: string; citations?: { citation: string; kind: string; id: string }[];
  proposals?: { id: string; tool: string; title?: string; description?: string; project_id?: string }[];
  groups?: AIGroup[];
}
export interface AIRun {
  id: string; revision: number; status: 'queued' | 'running' | 'awaiting_review' | 'completed' | 'failed' | 'cancelled';
  operation: AIOperation; profile_id: string; profile_revision: number; channel_id: string;
  created_at: string; updated_at: string; steps: AIRunStep[]; result?: AIRunResult; error?: string; can_resume: boolean;
  plan?: AIPlanStep[];
}
export interface AIPlanStep { operation: AIOperation; profile_id: string; expected_profile_revision: number; instructions?: string }
export interface AIRuntimeState { globally_enabled: boolean; profiles: AIModelProfile[]; budget: AIBudget; runs: AIRun[] }
export interface AIExecution {
  id: string; source_id: string; family: 'run' | 'copilot' | 'app-draft' | 'recap' | 'workflow' | 'app-command'; channel_id: string;
  status: string; created_at: string | null; updated_at: string; timestamp: string; timestamp_kind: 'created_at' | 'last_run_at' | 'next_run_at';
  steps: { index: number; status: string; attempts: number }[];
  usage: { input_tokens: number; output_tokens: number; charged_tokens: number; estimated_cost_micros: number; unknown_attempts: number; cost_is_estimate: true };
  source: { run_id?: string; conversation_id?: string; turn_id?: string; draft_id?: string; schedule_id?: string; workflow_id?: string; job_id?: string; tab?: 'apps' | 'workflows'; channel_id: string };
  content_included: false;
}
export function validRuntimeState(value: unknown): value is AIRuntimeState {
  const state = value as AIRuntimeState | null;
  return !!state && typeof state.globally_enabled === 'boolean' && Array.isArray(state.profiles) && Array.isArray(state.runs) && !!state.budget;
}
export interface AIProfileInput {
  password: string; code?: string; expected_revision: number; name: string; protocol: AIProtocol;
  endpoint: string; model: string; api_key?: string; key_source: 'workspace' | 'personal'; enabled: boolean;
  max_output_tokens: number; input_cost_per_million_micros: number; output_cost_per_million_micros: number;
  instructions?: string; api_version?: string;
}
export interface AIRunInput {
  request_id: string; channel_id: string; profile_id: string; expected_profile_revision: number;
  operation: AIOperation; prompt: string; sources: CopilotSource[]; selected_text?: string;
  max_steps: number; max_tokens: number; disclose_to_provider: true; target_project_id?: string;
  plan?: AIPlanStep[];
  disclose_cross_channel_sources?: boolean;
  /** Device-verified IDs for encrypted foreground grouping; grants no server access. */
  reviewed_source_ids?: string[];
  disclosure_scope?: { kind: 'meeting_recording'; id: string; revision: number };
}
export interface AIReadTool {
  id: string; name: string; revision: number; kind: 'web_search' | 'http_read'; endpoint: string; method: 'GET' | 'POST'; enabled: boolean; configured: boolean;
  auth_header: 'bearer' | 'x-api-key'; query_field: string; result_path: string[]; title_field: string; url_field: string; text_field: string; max_results: number;
}
export interface AIReadResult { tool_id: string; tool_revision: number; recipient: string; query: string; results: { title: string; url: string; text: string }[]; text: string; bounded: true; ephemeral: true; truncated?: boolean }
export type AIReadToolInput = Omit<AIReadTool, 'id' | 'revision' | 'configured'> & { password: string; code?: string; expected_revision: number; api_key?: string };
const path = (workspace: string) => '/workspaces/' + encodeURIComponent(workspace) + '/copilot/runtime';
const data = (value: unknown, method = 'POST') => ({ method, body: JSON.stringify(value) });
export const aiRuntimeApi = {
  status: (workspace: string) => api.request<AIRuntimeState>(path(workspace)),
  executions: (workspace: string) => api.request<{ items: AIExecution[]; complete: boolean; source_engines: string[]; writes_performed: false }>(path(workspace) + '/executions'),
  tools: (workspace: string) => api.request<{ items: AIReadTool[]; role: string }>(path(workspace) + '/tools'),
  tool: (workspace: string, id: string, value: AIReadToolInput) => api.request<AIReadTool>(path(workspace) + '/tools/' + encodeURIComponent(id), data(value, 'PUT')),
  read: (workspace: string, tool: AIReadTool, value: { channel_id: string; query: string; disclose_to_tool: true; disclose_decrypted_content: boolean }) => api.request<AIReadResult>(path(workspace) + '/tools/' + encodeURIComponent(tool.id) + '/read', data({ ...value, expected_revision: tool.revision })),
  profile: (workspace: string, id: string, value: AIProfileInput) => api.request<Pick<AIModelProfile, 'id' | 'revision' | 'enabled'>>(path(workspace) + '/profiles/' + encodeURIComponent(id), data(value, 'PUT')),
  consent: (workspace: string, id: string, revision: number, enabled: boolean) => api.request(path(workspace) + '/profiles/' + encodeURIComponent(id) + '/consent', data({ enabled, expected_revision: revision })),
  personalKey: (workspace: string, id: string, value: { password: string; code?: string; expected_profile_revision: number; api_key: string }) => api.request(path(workspace) + '/personal-keys/' + encodeURIComponent(id), data(value, 'PUT')),
  removePersonalKey: (workspace: string, id: string, value: { password: string; code?: string }) => api.request(path(workspace) + '/personal-keys/' + encodeURIComponent(id), data(value, 'DELETE')),
  budget: (workspace: string, value: { password: string; code?: string; expected_revision: number; max_tokens: number; max_cost_micros: number; default_input_cost_per_million_micros?: number; default_output_cost_per_million_micros?: number }) => api.request<AIBudget>(path(workspace) + '/budget', data(value, 'PUT')),
  create: (workspace: string, value: AIRunInput) => api.request<AIRun>(path(workspace) + '/runs', data(value)),
  plan: (workspace: string, value: AIRunInput & { allowed_profiles: { profile_id: string; expected_profile_revision: number }[]; disclose_decrypted_content: boolean }) => api.request<{ plan: AIPlanStep[]; planner_profile_id: string; planner_profile_revision: number; usage: AIUsage; ephemeral: true }>(path(workspace) + '/plans', data(value)),
  complete: (workspace: string, value: AIRunInput & { disclose_decrypted_content: boolean }) => api.request<AIRunResult & { usage?: AIUsage; ephemeral: true }>(path(workspace) + '/complete', data(value)),
  run: (workspace: string, id: string) => api.request<AIRun>(path(workspace) + '/runs/' + encodeURIComponent(id)),
  cancel: (workspace: string, run: AIRun) => api.request<AIRun>(path(workspace) + '/runs/' + encodeURIComponent(run.id) + '/cancel', data({ expected_revision: run.revision })),
  remove: (workspace: string, id: string) => api.request<void>(path(workspace) + '/runs/' + encodeURIComponent(id), { method: 'DELETE' }),
  resume: (workspace: string, run: AIRun) => api.request<AIRun>(path(workspace) + '/runs/' + encodeURIComponent(run.id) + '/resume', data({ expected_revision: run.revision, acknowledge_possible_duplicate_charge: true })),
  approve: (workspace: string, run: AIRun, proposal: string) => api.request<{ task_id?: string; completed?: boolean; already_approved: boolean }>(path(workspace) + '/runs/' + encodeURIComponent(run.id) + '/approve', data({ expected_revision: run.revision, proposal_id: proposal, confirm: true })),
};

/** A reviewed route is an exact recipient and model, never a silent fallback. */
export function sameAIProfile(left: AIModelProfile, right: AIModelProfile): boolean {
  return left.id === right.id && left.revision === right.revision && left.protocol === right.protocol
    && left.endpoint === right.endpoint && left.model === right.model && left.key_source === right.key_source;
}
export function aiRunIsActive(run: AIRun): boolean { return run.status === 'queued' || run.status === 'running'; }
export function aiProfileRecipient(profile: AIModelProfile): string {
  try { const endpoint = new URL(profile.endpoint); return endpoint.username || endpoint.password ? '' : endpoint.hostname; }
  catch { return ''; }
}
