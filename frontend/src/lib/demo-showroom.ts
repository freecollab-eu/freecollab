/** Public showroom data. Every mutation is local and disappears on reset/reload. */
export type DemoChannel = 'studio' | 'product' | 'design';
export type DemoSection = 'chat' | 'projects' | 'documents' | 'connections' | 'appearance';
export interface DemoMessage { id: string; author: string; text: string; time: string; sample: boolean }
export interface DemoTask { id: string; text: string; done: boolean; sample: boolean; group: 'Planning' | 'Design' | 'Team' }
export interface DemoDocument { id: 'brief' | 'notes'; title: string; source: string; edited: string | null }
export type DemoMaterial = 'courtyard' | 'brief';
export interface DemoLayout { order: DemoMaterial[]; pinned: DemoMaterial[]; side: 'left' | 'right'; focus: boolean }
export const createDemoLayout = (): DemoLayout => ({ order: ['courtyard', 'brief'], pinned: [], side: 'right', focus: false });
export interface DemoState { messages: Record<DemoChannel, DemoMessage[]>; tasks: DemoTask[]; documents: DemoDocument[]; nextId: number; layout: DemoLayout }
export const demoSections: DemoSection[] = ['chat', 'projects', 'documents', 'connections', 'appearance'];
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
      { id: 'brief', title: 'Project brief', edited: null, source: '## A quieter courtyard\n\nCreate a welcoming shared space for the neighbourhood.\n\n### Our direction\n- Warm, durable materials\n- Room to gather and room to pause\n- A simple first version, shaped by feedback\n\n### Open question\nWhich materials work best for this space?' },
      { id: 'notes', title: 'Studio notes', edited: null, source: '## A few things we agreed\n\n- Start small and keep decisions visible.\n- Collect questions before the next walkthrough.\n- Give every task a clear next step.\n\n### Next conversation\nBring one idea that makes the courtyard feel more welcoming.' },
    ], nextId: 1, layout: createDemoLayout(),
  };
}
export type DemoAction = { type: 'message'; channel: DemoChannel; text: string; time: string } | { type: 'task_add'; text: string } | { type: 'task_toggle'; id: string } | { type: 'document'; id: DemoDocument['id']; text: string } | { type: 'reset' } | { type: 'layout_reset' } | { type: 'focus' } | { type: 'layout_side'; side: DemoLayout['side'] } | { type: 'layout_pin'; id: DemoMaterial } | { type: 'layout_move'; id: DemoMaterial; direction: -1 | 1 };
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
    const text = action.text.trim(), rows = state.messages[action.channel];
    if (!rows || !text || text.length > 2000 || rows.length >= 100) return state;
    return { ...state, nextId: state.nextId + 1, messages: { ...state.messages, [action.channel]: [...rows, { id: 'local-' + state.nextId, author: 'You', text, time: action.time, sample: false }] } };
  }
  if (action.type === 'task_add') {
    const text = action.text.trim(); if (!text || text.length > 180 || state.tasks.length >= 50) return state;
    return { ...state, nextId: state.nextId + 1, tasks: [...state.tasks, { id: 'local-' + state.nextId, text, done: false, sample: false, group: 'Team' }] };
  }
  if (action.type === 'task_toggle') return { ...state, tasks: state.tasks.map(row => row.id === action.id ? { ...row, done: !row.done } : row) };
  if (action.type === 'document') {
    if (action.text.length > 20000) return state;
    return { ...state, documents: state.documents.map(row => row.id === action.id ? { ...row, edited: action.text } : row) };
  }
  return state;
}
export function demoMarkdown(document: DemoDocument, translate: (source: string) => string) { return '# ' + translate(document.title) + '\n\n' + (document.edited ?? translate(document.source)) + '\n'; }
export const demoConnectionProviders = {
  microsoft: { name: 'Microsoft 365', console: 'Microsoft Entra admin center', scopes: 'Files.ReadWrite.All · offline_access (files) / Calendars.ReadWrite · offline_access (calendar)' },
  google: { name: 'Google Workspace', console: 'Google Cloud console', scopes: 'drive (files) / calendar.events (calendar)' },
} as const;
export const demoCallbackExample = 'https://your-freecollab.example/api/sync/oauth/callback';
export const demoConnectionSteps = ['Register your organisation’s OAuth app', 'Choose a channel and the minimum permissions', 'Copy the exact callback from your installation', 'Review consent, then explicitly connect and test'] as const;
