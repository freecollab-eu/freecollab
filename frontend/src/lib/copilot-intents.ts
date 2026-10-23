/** Local draft preparation only. Provider requests remain behind the panel's send flow. */
export const COPILOT_PROMPT_LIMIT = 3900;
export const copilotIntents = [
  { id: 'summary', label: 'Summarise selection', instruction: 'Summarise only the sources I selected. Give the key points and open questions, cite the selected sources, and say when the selection is insufficient.' },
  { id: 'next_steps', label: 'Suggest next steps', instruction: 'Suggest up to three concrete next steps using only the sources I selected. Separate agreed actions from suggestions, cite the selected sources, and leave owners or dates unspecified unless the sources state them.' },
  { id: 'reply', label: 'Draft a reply', instruction: 'Draft a short reply using only the sources I selected and my request. Keep the tone clear and natural, cite the selected sources, and do not invent commitments, owners or dates.' },
  { id: 'task', label: 'Propose a task', instruction: 'Propose one next task from the sources I selected with a short title and description. Use create_task only if it is available for my chosen destination; otherwise return a draft. Cite the selected sources and do not claim the task was created.' },
] as const;
export type CopilotIntentId = typeof copilotIntents[number]['id'];
export interface CopilotIntentResult { prompt: string; status: 'added' | 'already_present' | 'too_long' | 'unavailable' }

/** Preserve the exact existing draft and refuse overflow rather than truncating user text. */
export function appendCopilotIntent(prompt: string, intentId: CopilotIntentId, translate: (source: string) => string = source => source): CopilotIntentResult {
  const intent = copilotIntents.find(item => item.id === intentId);
  if (!intent) return { prompt, status: 'unavailable' };
  const instruction = translate(intent.instruction);
  if (prompt === instruction || prompt.endsWith('\n\n' + instruction)) return { prompt, status: 'already_present' };
  const next = prompt ? prompt + '\n\n' + instruction : instruction;
  return next.length > COPILOT_PROMPT_LIMIT ? { prompt, status: 'too_long' } : { prompt: next, status: 'added' };
}

/** Display only a hostname from a credential-free HTTPS URL, never query values or a key. */
export function copilotProviderHost(endpoint: string | undefined): string | null {
  if (!endpoint) return null;
  try {
    const url = new URL(endpoint);
    return url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash ? url.hostname : null;
  } catch { return null; }
}
