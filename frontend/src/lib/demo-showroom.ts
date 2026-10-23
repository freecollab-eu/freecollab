/** Public showroom data. Every mutation is local and disappears on reset/reload. */
export type DemoChannel = 'studio' | 'product' | 'design';
export const demoChannels: readonly DemoChannel[] = ['studio', 'product', 'design'];
export type DemoSection = 'chat' | 'projects' | 'documents' | 'connections' | 'appearance';
export type DemoProvider = 'microsoft' | 'google';
export interface DemoDestination { section?: DemoSection; provider?: DemoProvider }
export type DemoOwner = 'Maja' | 'Noor' | 'Elias' | 'Nora';
export const demoOwners: DemoOwner[] = ['Maja', 'Noor', 'Elias', 'Nora'];
export const demoDecisionExample = { text: 'Use warm, durable materials and keep the courtyard open.', nextStep: 'Bring the material samples to the walkthrough' };
export interface DemoDecision { text: string; nextStep: string; owner: DemoOwner }
export const demoDecisionTaskId = 'courtyard-follow-up';
export interface DemoMessage { id: string; author: string; text: string; time: string; sample: boolean }
export interface DemoTask { id: string; text: string; done: boolean; sample: boolean; group: 'Planning' | 'Design' | 'Team'; owner?: DemoOwner; source?: { channel: DemoChannel; messageId: string } }
export type DemoDocumentId = string;
export interface DemoDocument { id: DemoDocumentId; channel: DemoChannel; stackId: string; title: string; source: string; edited: string | null; sample: boolean; owner: DemoOwner | 'You'; updatedLabel: string }
export interface DemoDocumentLink { id: string; documentId: DemoDocumentId; relatedDocumentId: DemoDocumentId }
export interface DemoDocumentStack { id: string; title: string; sample: boolean; references: string[] }
export type DemoStackUndo = { kind: 'document_move'; documentId: DemoDocumentId; fromStackId: string; toStackId: string } | { kind: 'reference_add'; stackId: string; messageId: string } | { kind: 'reference_remove'; stackId: string; messageId: string; index: number } | { kind: 'document_link'; linkId: string; stackId: string } | { kind: 'document_unlink'; link: DemoDocumentLink; index: number; stackId: string };
export const demoStackLimit = 12;
export const demoDocumentLimit = 30;
export const demoStackReferenceLimit = 30;
export const demoDocumentLinkLimit = 100;
export const demoDismissedConnectionLimit = 1000;
export const demoConnectionIdLimit = 300;
const validChannel = (channel: DemoChannel) => demoChannels.includes(channel);
const sameDocumentPair = (link: DemoDocumentLink, documentId: DemoDocumentId, relatedDocumentId: DemoDocumentId) => (link.documentId === documentId && link.relatedDocumentId === relatedDocumentId) || (link.documentId === relatedDocumentId && link.relatedDocumentId === documentId);
export function createDemoStacks(): Record<DemoChannel, DemoDocumentStack[]> {
  const stacks = (channel: DemoChannel): DemoDocumentStack[] => [
    { id: channel + '-reference', title: 'Reference material', sample: true, references: [] },
    { id: channel + '-sketches', title: 'Sketches', sample: true, references: [] },
    { id: channel + '-decisions', title: 'Decisions', sample: true, references: [] },
  ];
  return { studio: stacks('studio'), product: stacks('product'), design: stacks('design') };
}
export type DemoMaterial = 'courtyard' | 'brief';
export interface DemoLayout { order: DemoMaterial[]; pinned: DemoMaterial[]; side: 'left' | 'right'; focus: boolean }
export const createDemoLayout = (): DemoLayout => ({ order: ['courtyard', 'brief'], pinned: [], side: 'right', focus: false });
export interface DemoState { messages: Record<DemoChannel, DemoMessage[]>; tasks: DemoTask[]; documents: DemoDocument[]; stacks: Record<DemoChannel, DemoDocumentStack[]>; documentLinks: Record<DemoChannel, DemoDocumentLink[]>; dismissedConnections: Record<DemoChannel, string[]>; stackUndo: Partial<Record<DemoChannel, DemoStackUndo>>; nextId: number; layout: DemoLayout; decision: DemoDecision | null }
export function demoChannelDocuments(state: DemoState, channel: DemoChannel): DemoDocument[] {
  return validChannel(channel) ? state.documents.filter(document => document.channel === channel) : [];
}
export function demoChannelStacks(state: DemoState, channel: DemoChannel): DemoDocumentStack[] {
  return validChannel(channel) ? state.stacks[channel] : [];
}
export function demoStackDocuments(state: DemoState, channel: DemoChannel, stackId: string): DemoDocument[] {
  return demoChannelStacks(state, channel).some(stack => stack.id === stackId) ? demoChannelDocuments(state, channel).filter(document => document.stackId === stackId) : [];
}
export function demoStackReferences(state: DemoState, channel: DemoChannel, stackId: string): DemoMessage[] {
  const stack = demoChannelStacks(state, channel).find(row => row.id === stackId);
  return stack ? stack.references.map(id => state.messages[channel].find(message => message.id === id)).filter((message): message is DemoMessage => !!message) : [];
}
export const demoSections: DemoSection[] = ['chat', 'projects', 'documents', 'connections', 'appearance'];
export function readDemoDestination(hash: string): DemoDestination | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  if (!params.has('demo')) return null;
  const requested = params.get('demo');
  const section = demoSections.find(value => value === requested) ?? 'chat';
  const provider = params.get('provider');
  return section === 'connections' && (provider === 'microsoft' || provider === 'google') ? { section, provider } : { section };
}
export function demoDestinationHash(destination: DemoDestination = {}): string {
  const section = destination.section && demoSections.includes(destination.section) ? destination.section : 'chat';
  if (section === 'chat') return '#demo';
  const params = new URLSearchParams({ demo: section });
  if (section === 'connections' && (destination.provider === 'microsoft' || destination.provider === 'google')) params.set('provider', destination.provider);
  return '#' + params.toString();
}
export function createDemoState(): DemoState {
  return {
    messages: {
      studio: [
        { id: 'studio-1', author: 'Maja', text: 'Shall we open up to the courtyard?', time: '10:39', sample: true },
        { id: 'studio-2', author: 'Noor', text: 'Yes. Then the light reaches all the way in.', time: '10:42', sample: true },
        { id: 'studio-3', author: 'Elias', text: 'I’ve shared the new sketch here. The long table can sit by the open doors.', time: '10:45', sample: true },
      ],
      product: [
        { id: 'product-1', author: 'Noor', text: 'One shared plan, with a clear next step for everyone.', time: '10:05', sample: true },
        { id: 'product-2', author: 'Maja', text: 'The sample project has a few tasks you can try changing.', time: '10:08', sample: true },
      ],
      design: [
        { id: 'design-1', author: 'Elias', text: 'Warm materials, generous space and a little less noise.', time: '11:02', sample: true },
        { id: 'design-2', author: 'Maja', text: 'Try a new colour or font in Appearance. It changes this demo only.', time: '11:07', sample: true },
      ],
    },
    tasks: [
      { id: 'task-1', text: 'Agree on the design direction', done: true, sample: true, group: 'Planning' },
      { id: 'task-2', text: 'Review the courtyard materials', done: false, sample: true, group: 'Design' },
      { id: 'task-3', text: 'Add open questions to the brief', done: true, sample: true, group: 'Planning' },
      { id: 'task-4', text: 'Plan a short team walkthrough', done: false, sample: true, group: 'Team' },
    ],
    documents: [
      { id: 'brief', channel: 'studio', stackId: 'studio-reference', title: 'Project brief', edited: null, sample: true, owner: 'Maja', updatedLabel: '10:39', source: '## A quieter courtyard\n\nCreate a welcoming shared space for the neighbourhood.\n\n### Our direction\n- Warm, durable materials\n- Room to gather and room to pause\n- A simple first version, shaped by feedback\n\n### Open question\nWhich materials work best for this space?' },
      { id: 'notes', channel: 'studio', stackId: 'studio-reference', title: 'Studio notes', edited: null, sample: true, owner: 'Noor', updatedLabel: '10:42', source: '## A few things we agreed\n\n- Start small and keep decisions visible.\n- Collect questions before the next walkthrough.\n- Give every task a clear next step.\n\n### Next conversation\nBring one idea that makes the courtyard feel more welcoming.' },
      { id: 'materials', channel: 'studio', stackId: 'studio-sketches', title: 'Materials & sketches', edited: null, sample: true, owner: 'Elias', updatedLabel: '10:45', source: '## Materials to bring together\n\n- A warm timber sample for the long table\n- Brick and stone colours from the courtyard\n- A sketch of the open doorway\n\nLeave room for the team to add another possibility.' },
      { id: 'decision', channel: 'studio', stackId: 'studio-decisions', title: 'The direction we agreed', edited: null, sample: true, owner: 'Maja', updatedLabel: '11:03', source: '## Our starting point\n\nKeep the courtyard welcoming, durable and open.\n\n### Before the walkthrough\nBring a few material samples and one question to discuss together.' },
      { id: 'product-plan', channel: 'product', stackId: 'product-reference', title: 'A shared plan', edited: null, sample: true, owner: 'Noor', updatedLabel: '10:05', source: '## One shared plan\n\nStart with a small first version. Keep the work visible and give every task a clear next step.' },
      { id: 'product-walkthrough', channel: 'product', stackId: 'product-decisions', title: 'The next walkthrough', edited: null, sample: true, owner: 'Maja', updatedLabel: '10:08', source: '## Make the next step clear\n\n- Review what is ready\n- Collect the remaining questions\n- Choose one next step and a person to take it forward' },
      { id: 'design-palette', channel: 'design', stackId: 'design-reference', title: 'Colour & materials', edited: null, sample: true, owner: 'Elias', updatedLabel: '11:02', source: '## A warm, quiet palette\n\nUse the colours of timber, paper and the courtyard. Give the work plenty of room and keep the important details easy to find.' },
      { id: 'design-type', channel: 'design', stackId: 'design-sketches', title: 'Type & space', edited: null, sample: true, owner: 'Maja', updatedLabel: '11:07', source: '## Room to read\n\nChoose comfortable type, clear headings and generous spacing. Try your own colours and typeface in My view.' },
    ], stacks: createDemoStacks(), documentLinks: { studio: [], product: [], design: [] }, dismissedConnections: { studio: [], product: [], design: [] }, stackUndo: {}, nextId: 1, layout: createDemoLayout(), decision: null,
  };
}
export type DemoAction = { type: 'document_compare'; channel: DemoChannel; id: string; expected: string | null; text: string | null } | { type: 'task_text_compare'; channel: DemoChannel; id: string; expected: DemoTask; text: string; sample: boolean } | { type: 'message'; channel: DemoChannel; text: string; time: string } | { type: 'task_add'; text: string } | { type: 'task_from_message'; channel: DemoChannel; messageId: string; text: string; owner?: DemoOwner } | { type: 'task_undo_created'; channel: DemoChannel; id: string; text: string; owner?: DemoOwner } | { type: 'task_toggle'; id: string } | { type: 'decision_record'; text: string; nextStep: string; owner: DemoOwner } | { type: 'document'; id: DemoDocument['id']; text: string } | { type: 'document_add'; channel: DemoChannel; stackId: string; title: string } | { type: 'document_move'; channel: DemoChannel; id: DemoDocumentId; stackId: string } | { type: 'document_link'; channel: DemoChannel; documentId: DemoDocumentId; relatedDocumentId: DemoDocumentId } | { type: 'document_unlink'; channel: DemoChannel; id: string } | { type: 'connection_dismiss'; channel: DemoChannel; id: string } | { type: 'connection_restore_dismissed'; channel: DemoChannel } | { type: 'stack_add'; channel: DemoChannel; title: string } | { type: 'stack_rename'; channel: DemoChannel; id: string; title: string } | { type: 'stack_reference'; channel: DemoChannel; stackId: string; messageId: string } | { type: 'stack_reference_remove'; channel: DemoChannel; stackId: string; messageId: string } | { type: 'stack_undo'; channel: DemoChannel } | { type: 'reset' } | { type: 'layout_reset' } | { type: 'focus' } | { type: 'layout_side'; side: DemoLayout['side'] } | { type: 'layout_pin'; id: DemoMaterial } | { type: 'layout_move'; id: DemoMaterial; direction: -1 | 1 };
export function demoReducer(state: DemoState, action: DemoAction): DemoState {
  if (action.type === 'reset') return createDemoState();
  if (action.type === 'layout_reset') return { ...state, layout: createDemoLayout() };
  if (action.type === 'focus') return { ...state, layout: { ...state.layout, focus: !state.layout.focus } };
  if (action.type === 'layout_side') return { ...state, layout: { ...state.layout, side: action.side } };
  if (action.type === 'layout_pin') {
    if (!state.layout.order.includes(action.id)) return state;
    return { ...state, layout: { ...state.layout, pinned: state.layout.pinned.includes(action.id) ? state.layout.pinned.filter(id => id !== action.id) : [...state.layout.pinned, action.id] } };
  }
  if (action.type === 'layout_move') {
    const order = [...state.layout.order], from = order.indexOf(action.id), to = from + action.direction;
    if (from < 0 || to < 0 || to >= order.length) return state;
    [order[from], order[to]] = [order[to], order[from]];
    return { ...state, layout: { ...state.layout, order } };
  }
  if (action.type === 'message') {
    if (!validChannel(action.channel)) return state;
    const text = action.text.trim(), rows = state.messages[action.channel];
    if (!rows || !text || text.length > 2000 || rows.length >= 100) return state;
    return { ...state, nextId: state.nextId + 1, messages: { ...state.messages, [action.channel]: [...rows, { id: 'local-' + state.nextId, author: 'You', text, time: action.time, sample: false }] } };
  }
  if (action.type === 'task_add') {
    const text = action.text.trim(); if (!text || text.length > 180 || state.tasks.length >= 50) return state;
    return { ...state, nextId: state.nextId + 1, tasks: [...state.tasks, { id: 'local-' + state.nextId, text, done: false, sample: false, group: 'Team' }] };
  }
  if (action.type === 'document_compare') {
    const document = state.documents.find(row => row.id === action.id && row.channel === action.channel);
    if (!validChannel(action.channel) || !document || document.edited !== action.expected || (action.text !== null && (typeof action.text !== 'string' || action.text.length > 20000))) return state;
    if (document.edited === action.text) return state;
    return { ...state, documents: state.documents.map(row => row === document ? { ...row, edited: action.text } : row) };
  }
  if (action.type === 'task_text_compare') {
    if (typeof action.text !== 'string') return state;
    const task = state.tasks.find(row => row.id === action.id), expected = action.expected, text = action.text.trim();
    if (!validChannel(action.channel) || !task || !expected || (task.source && task.source.channel !== action.channel) || !text || text.length > 180 || typeof action.sample !== 'boolean') return state;
    if (task.id !== expected.id || task.text !== expected.text || task.sample !== expected.sample || task.done !== expected.done || task.owner !== expected.owner || task.group !== expected.group || task.source?.channel !== expected.source?.channel || task.source?.messageId !== expected.source?.messageId) return state;
    if (task.text === text && task.sample === action.sample) return state;
    return { ...state, tasks: state.tasks.map(row => row === task ? { ...row, text, sample: action.sample } : row) };
  }
  if (action.type === 'task_from_message') {
    if (!validChannel(action.channel) || !state.messages[action.channel].some(message => message.id === action.messageId) || state.tasks.some(task => task.source?.channel === action.channel && task.source.messageId === action.messageId)) return state;
    const text = action.text.trim();
    if (!text || text.length > 180 || (action.owner !== undefined && !demoOwners.includes(action.owner)) || state.tasks.length >= 50) return state;
    const task: DemoTask = { id: 'local-' + state.nextId, text, owner: action.owner, done: false, sample: false, group: 'Team', source: { channel: action.channel, messageId: action.messageId } };
    return { ...state, nextId: state.nextId + 1, tasks: [...state.tasks, task] };
  }
  if (action.type === 'task_undo_created') {
    if (!validChannel(action.channel)) return state;
    const task = state.tasks.find(row => row.id === action.id);
    if (!task || task.source?.channel !== action.channel || task.text !== action.text || task.owner !== action.owner || task.done !== false || task.sample !== false || task.group !== 'Team') return state;
    return { ...state, tasks: state.tasks.filter(row => row.id !== task.id) };
  }
  if (action.type === 'task_toggle') return { ...state, tasks: state.tasks.map(row => row.id === action.id ? { ...row, done: !row.done } : row) };
  if (action.type === 'decision_record') {
    const text = action.text.trim(), nextStep = action.nextStep.trim();
    const previousTask = state.tasks.find(row => row.id === demoDecisionTaskId);
    if (!text || text.length > 1000 || !nextStep || nextStep.length > 180 || !demoOwners.includes(action.owner) || (!previousTask && state.tasks.length >= 50)) return state;
    const decision = { text, nextStep, owner: action.owner };
    const task: DemoTask = { id: demoDecisionTaskId, text: nextStep, owner: action.owner, sample: false, group: 'Team', done: previousTask?.text === nextStep && previousTask.owner === action.owner ? previousTask.done : false };
    return { ...state, decision, tasks: previousTask ? state.tasks.map(row => row.id === demoDecisionTaskId ? task : row) : [...state.tasks, task] };
  }
  if (action.type === 'document') {
    if (action.text.length > 20000 || !state.documents.some(row => row.id === action.id)) return state;
    return { ...state, documents: state.documents.map(row => row.id === action.id ? { ...row, edited: action.text, owner: 'You', updatedLabel: 'Just now' } : row) };
  }
  if (action.type === 'stack_add' || action.type === 'stack_rename') {
    if (!validChannel(action.channel)) return state;
    const title = action.title.trim(), stacks = state.stacks[action.channel];
    if (!title || title.length > 80 || stacks.some(stack => (action.type === 'stack_add' || stack.id !== action.id) && stack.title.toLowerCase() === title.toLowerCase())) return state;
    if (action.type === 'stack_add') {
      if (stacks.length >= demoStackLimit) return state;
      return { ...state, nextId: state.nextId + 1, stacks: { ...state.stacks, [action.channel]: [...stacks, { id: 'stack-' + state.nextId, title, sample: false, references: [] }] } };
    }
    if (!stacks.some(stack => stack.id === action.id && stack.title !== title)) return state;
    return { ...state, stacks: { ...state.stacks, [action.channel]: stacks.map(stack => stack.id === action.id ? { ...stack, title, sample: false } : stack) } };
  }
  if (action.type === 'document_add') {
    if (!validChannel(action.channel)) return state;
    const title = action.title.trim();
    if (!title || title.length > 200 || !state.stacks[action.channel].some(stack => stack.id === action.stackId) || demoChannelDocuments(state, action.channel).length >= demoDocumentLimit) return state;
    const document: DemoDocument = { id: 'document-' + state.nextId, channel: action.channel, stackId: action.stackId, title, source: '', edited: '', sample: false, owner: 'You', updatedLabel: 'Just now' };
    return { ...state, nextId: state.nextId + 1, documents: [...state.documents, document] };
  }
  if (action.type === 'document_move') {
    if (!validChannel(action.channel)) return state;
    const document = state.documents.find(row => row.id === action.id && row.channel === action.channel);
    if (!document || document.stackId === action.stackId || !state.stacks[action.channel].some(stack => stack.id === action.stackId)) return state;
    return { ...state, documents: state.documents.map(row => row.id === document.id ? { ...row, stackId: action.stackId } : row), stackUndo: { ...state.stackUndo, [action.channel]: { kind: 'document_move', documentId: document.id, fromStackId: document.stackId, toStackId: action.stackId } } };
  }
  if (action.type === 'document_link') {
    if (!validChannel(action.channel) || action.documentId === action.relatedDocumentId) return state;
    const document = state.documents.find(row => row.id === action.documentId && row.channel === action.channel), related = state.documents.find(row => row.id === action.relatedDocumentId && row.channel === action.channel);
    const links = state.documentLinks[action.channel];
    if (!document || !related || links.length >= demoDocumentLinkLimit || links.some(link => sameDocumentPair(link, document.id, related.id))) return state;
    const link: DemoDocumentLink = { id: 'link-' + state.nextId, documentId: document.id, relatedDocumentId: related.id };
    return { ...state, nextId: state.nextId + 1, documentLinks: { ...state.documentLinks, [action.channel]: [...links, link] }, stackUndo: { ...state.stackUndo, [action.channel]: { kind: 'document_link', linkId: link.id, stackId: document.stackId } } };
  }
  if (action.type === 'document_unlink') {
    if (!validChannel(action.channel)) return state;
    const links = state.documentLinks[action.channel], index = links.findIndex(link => link.id === action.id), link = links[index];
    if (!link) return state;
    const document = state.documents.find(row => row.id === link.documentId && row.channel === action.channel);
    if (!document || !state.documents.some(row => row.id === link.relatedDocumentId && row.channel === action.channel)) return state;
    return { ...state, documentLinks: { ...state.documentLinks, [action.channel]: links.filter(row => row.id !== link.id) }, stackUndo: { ...state.stackUndo, [action.channel]: { kind: 'document_unlink', link, index, stackId: document.stackId } } };
  }
  if (action.type === 'connection_dismiss') {
    if (!validChannel(action.channel) || !action.id.trim() || action.id.length > demoConnectionIdLimit) return state;
    const dismissed = state.dismissedConnections[action.channel];
    if (dismissed.length >= demoDismissedConnectionLimit || dismissed.includes(action.id)) return state;
    return { ...state, dismissedConnections: { ...state.dismissedConnections, [action.channel]: [...dismissed, action.id] } };
  }
  if (action.type === 'connection_restore_dismissed') {
    if (!validChannel(action.channel) || !state.dismissedConnections[action.channel].length) return state;
    return { ...state, dismissedConnections: { ...state.dismissedConnections, [action.channel]: [] } };
  }
  if (action.type === 'stack_reference') {
    if (!validChannel(action.channel) || !state.messages[action.channel].some(message => message.id === action.messageId)) return state;
    const stack = state.stacks[action.channel].find(row => row.id === action.stackId);
    if (!stack || stack.references.includes(action.messageId) || stack.references.length >= demoStackReferenceLimit) return state;
    return { ...state, stacks: { ...state.stacks, [action.channel]: state.stacks[action.channel].map(row => row.id === stack.id ? { ...row, references: [...row.references, action.messageId] } : row) }, stackUndo: { ...state.stackUndo, [action.channel]: { kind: 'reference_add', stackId: stack.id, messageId: action.messageId } } };
  }
  if (action.type === 'stack_reference_remove') {
    if (!validChannel(action.channel)) return state;
    const stack = state.stacks[action.channel].find(row => row.id === action.stackId), index = stack?.references.indexOf(action.messageId) ?? -1;
    if (!stack || index < 0) return state;
    return { ...state, stacks: { ...state.stacks, [action.channel]: state.stacks[action.channel].map(row => row.id === stack.id ? { ...row, references: row.references.filter(id => id !== action.messageId) } : row) }, stackUndo: { ...state.stackUndo, [action.channel]: { kind: 'reference_remove', stackId: stack.id, messageId: action.messageId, index } } };
  }
  if (action.type === 'stack_undo') {
    if (!validChannel(action.channel)) return state;
    const undo = state.stackUndo[action.channel];
    if (!undo) return state;
    const stackUndo = { ...state.stackUndo }; delete stackUndo[action.channel];
    if (undo.kind === 'document_move') {
      const document = state.documents.find(row => row.id === undo.documentId && row.channel === action.channel);
      if (!document || document.stackId !== undo.toStackId || !state.stacks[action.channel].some(stack => stack.id === undo.fromStackId)) return state;
      return { ...state, stackUndo, documents: state.documents.map(row => row.id === document.id ? { ...row, stackId: undo.fromStackId } : row) };
    }
    if (undo.kind === 'document_link') {
      const links = state.documentLinks[action.channel];
      if (!links.some(link => link.id === undo.linkId)) return state;
      return { ...state, stackUndo, documentLinks: { ...state.documentLinks, [action.channel]: links.filter(link => link.id !== undo.linkId) } };
    }
    if (undo.kind === 'document_unlink') {
      const links = state.documentLinks[action.channel], { link } = undo;
      if (link.documentId === link.relatedDocumentId || links.length >= demoDocumentLinkLimit || links.some(row => row.id === link.id || sameDocumentPair(row, link.documentId, link.relatedDocumentId)) || !state.documents.some(row => row.id === link.documentId && row.channel === action.channel) || !state.documents.some(row => row.id === link.relatedDocumentId && row.channel === action.channel)) return state;
      const restored = [...links]; restored.splice(undo.index, 0, link);
      return { ...state, stackUndo, documentLinks: { ...state.documentLinks, [action.channel]: restored } };
    }
    const stack = state.stacks[action.channel].find(row => row.id === undo.stackId);
    if (undo.kind === 'reference_remove') {
      if (!stack || stack.references.includes(undo.messageId) || stack.references.length >= demoStackReferenceLimit || !state.messages[action.channel].some(message => message.id === undo.messageId)) return state;
      const references = [...stack.references]; references.splice(undo.index, 0, undo.messageId);
      return { ...state, stackUndo, stacks: { ...state.stacks, [action.channel]: state.stacks[action.channel].map(row => row.id === stack.id ? { ...row, references } : row) } };
    }
    if (!stack?.references.includes(undo.messageId)) return state;
    return { ...state, stackUndo, stacks: { ...state.stacks, [action.channel]: state.stacks[action.channel].map(row => row.id === stack.id ? { ...row, references: row.references.filter(id => id !== undo.messageId) } : row) } };
  }
  return state;
}
export function demoMarkdown(document: DemoDocument, translate: (source: string) => string) { return '# ' + (document.sample ? translate(document.title) : document.title) + '\n\n' + (document.edited ?? (document.sample ? translate(document.source) : document.source)) + '\n'; }
export const demoConnectionProviders = {
  microsoft: { name: 'Microsoft 365', console: 'Microsoft Entra admin center', scopes: 'Files.ReadWrite.All · offline_access (files) / Calendars.ReadWrite · offline_access (calendar)' },
  google: { name: 'Google Workspace', console: 'Google Cloud console', scopes: 'drive (files) / calendar.events (calendar)' },
} as const;
export const demoCallbackExample = 'https://your-freecollab.example/api/sync/oauth/callback';
export const demoConnectionSteps = ['Register your organisation’s OAuth app', 'Choose a channel and the minimum permissions', 'Copy the exact callback from your installation', 'Review consent, then explicitly connect and test'] as const;
