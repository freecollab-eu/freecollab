import { t } from '../i18n/core';

export type SlashAction =
  | 'document' | 'task' | 'decision' | 'meetings' | 'files' | 'members' | 'channel'
  | 'bookmarks' | 'pins' | 'notification-settings' | 'search' | 'activity' | 'workflows'
  | 'cloud' | 'external-meetings' | 'apps' | 'copilot' | 'data' | 'mobile' | 'shared-work'
  | 'schedule' | 'focus' | 'appearance' | 'account' | 'my-day' | 'integrations'
  | 'direct-message' | 'new-channel' | 'channels' | 'secure-settings' | 'status'
  | 'notifications' | 'get-started' | 'feedback' | 'admin';

export interface SlashCommand {
  id: SlashAction;
  /** Stable, untranslated shortcut, without the initial slash. */
  command: string;
  label: string;
  description: string;
  group: 'conversation' | 'personal' | 'workspace';
  keywords: string;
}

export interface SlashContext {
  hasChannel: boolean;
  secure: boolean;
  channelType?: string;
  embedded: boolean;
  nativeSession: boolean;
  workspaceRole?: string;
  hasWorkspace: boolean;
  hasAdmin: boolean;
  /** True only after verifying this channel's federated-work context. */
  sharedWork?: boolean;
}

interface Definition extends SlashCommand {
  available?: (context: SlashContext) => boolean;
}

const channelWork = (context: SlashContext) => context.hasChannel && context.hasWorkspace;
const isDirect = (context: SlashContext) => ['direct', 'group_direct'].includes(context.channelType || '');
const workspaceNavigation = (context: SlashContext) => context.hasWorkspace && !context.embedded;
const workspaceManager = (context: SlashContext) => workspaceNavigation(context) && context.nativeSession && ['owner', 'admin'].includes(context.workspaceRole || '');

// These are entry points to existing product handlers. Opening one never sends
// the draft, creates a resource, contacts an AI provider or approves an app.
// Native and Matrix-backed documents/tasks/decisions share their existing UI;
// their API clients retain the verified-device encryption boundary.
const definitions: readonly Definition[] = [
  { id: 'document', command: 'document', label: 'Documents & stacks', description: 'Write together, organize stacks and open document history.', group: 'conversation', keywords: 'doc docs writing editor revision collection dokument skriva redigera staplar samling historik', available: channelWork },
  { id: 'task', command: 'task', label: 'Next steps & tasks', description: 'Projects, checklists and the next thing to do.', group: 'conversation', keywords: 'todo next step project checklist assignment uppgift uppgifter projekt nasta steg att gora checklista', available: channelWork },
  { id: 'decision', command: 'decision', label: 'Decisions', description: 'Record an agreement and keep its context close.', group: 'conversation', keywords: 'decide agreement record proposal beslut bestam overenskommelse forslag', available: channelWork },
  { id: 'meetings', command: 'meet', label: 'Meet here', description: 'Start or join a conversation with voice, video and screen sharing.', group: 'conversation', keywords: 'meeting meetings call huddle video audio screen mote moten ring samtal ljud skarm dela', available: channelWork },
  { id: 'files', command: 'files', label: 'Channel files', description: 'Upload, preview and manage shared file versions.', group: 'conversation', keywords: 'file attachment upload download preview version fil filer bilaga ladda hamta forhandegranska', available: context => context.hasChannel },
  { id: 'members', command: 'members', label: 'Channel members', description: 'People, invitations and access in this conversation.', group: 'conversation', keywords: 'people invite team membership access medlem medlemmar person personer bjud in team behorighet', available: context => context.hasChannel },
  { id: 'channel', command: 'channel', label: 'Channel details', description: 'View the name, description and settings of this channel.', group: 'conversation', keywords: 'rename topic description settings channel kanal namn byt beskrivning amne installningar', available: context => context.hasChannel && !isDirect(context) },
  { id: 'bookmarks', command: 'saved', label: 'Saved messages', description: 'Your private saved messages and completed follow-ups.', group: 'conversation', keywords: 'bookmark saved complete follow up spara sparat sparade bokmarke folj upp', available: context => context.hasChannel },
  { id: 'pins', command: 'pins', label: 'Pinned messages', description: 'References pinned for everyone in the conversation.', group: 'conversation', keywords: 'pin pinned reference fast fastnalt fastnalade referens', available: context => context.hasChannel },
  { id: 'notification-settings', command: 'notify', label: 'Channel notification settings', description: 'Choose mentions, message alerts and email summaries.', group: 'conversation', keywords: 'notification alert digest email mute mention avisering aviseringar notis notiser epost sammanfattning tysta omnamnande omnamnanden', available: context => context.hasChannel },
  { id: 'activity', command: 'activity', label: 'Channel activity', description: 'Updates, announcements, polls and responses.', group: 'conversation', keywords: 'feed update announcement poll vote aktivitet uppdatering anslag nyhet omrostning rost', available: channelWork },
  { id: 'workflows', command: 'workflow', label: 'Workflows', description: 'Review approvals, reminders and ongoing workflow runs.', group: 'conversation', keywords: 'workflow approval automation reminder flow arbetsflode arbetsfloden godkannande automatisering paminnelse flode', available: channelWork },
  { id: 'cloud', command: 'cloud', label: 'Cloud files', description: 'Browse your connected files or share a provider link.', group: 'conversation', keywords: 'cloud drive onedrive sharepoint google microsoft nextcloud provider moln molnfiler lank', available: channelWork },
  { id: 'external-meetings', command: 'meeting-services', label: 'Meeting services', description: 'Plan meetings through your connected meeting providers.', group: 'conversation', keywords: 'calendar teams zoom google meet external provider service kalender motestjanst motestjanster boka', available: channelWork },
  { id: 'apps', command: 'apps', label: 'Apps & automation', description: 'Review installed commands, app cards and AI drafts.', group: 'conversation', keywords: 'app bot integration automation command recap application appar bott automatisering kommando ai utkast', available: context => channelWork(context) && !context.secure && !isDirect(context) },
  { id: 'copilot', command: 'ai', label: 'AI settings', description: 'Your AI providers, permissions and reviewed context.', group: 'conversation', keywords: 'ai copilot assistant provider model openai claude gemini grok ollama agent assistent leverantor modell kontext', available: channelWork },
  { id: 'shared-work', command: 'shared-work', label: 'Shared work across connections', description: 'Tasks, documents and calendars shared with a connected installation.', group: 'conversation', keywords: 'federated shared connect calendar remote federation delat arbete anslutning kalender', available: context => channelWork(context) && context.secure && context.sharedWork === true },
  { id: 'schedule', command: 'schedule', label: 'Schedule & reminders', description: 'Schedule your draft or arrange a personal reminder.', group: 'conversation', keywords: 'schedule later reminder timed send delay schemalagg skicka senare paminnelse tid', available: context => context.hasChannel },
  { id: 'focus', command: 'focus', label: 'Focus mode', description: 'Give the conversation more room for quiet reading.', group: 'conversation', keywords: 'focus quiet reading layout fokus lugn lasa lasning layout', available: context => context.hasChannel },
  { id: 'search', command: 'search', label: 'Search all history', description: 'Find messages, files and decisions you can access.', group: 'workspace', keywords: 'search find history message sok hitta historik meddelande', available: context => context.hasWorkspace },
  { id: 'channels', command: 'channels', label: 'Browse channels', description: 'Find another conversation in this workspace.', group: 'workspace', keywords: 'channel browse switch list kanal kanaler bladdra byt samtal', available: workspaceNavigation },
  { id: 'new-channel', command: 'new-channel', label: 'Create a channel', description: 'Give a new conversation a home.', group: 'workspace', keywords: 'create new group channel skapa ny nytt grupp kanal', available: workspaceNavigation },
  { id: 'direct-message', command: 'message', label: 'Direct messages', description: 'Start a private conversation with a person or a small group.', group: 'workspace', keywords: 'direct message dm private group chat direktmeddelande privat grupp person', available: workspaceNavigation },
  { id: 'integrations', command: 'integrations', label: 'Integrations', description: 'Your organization sign-in, providers and connected services.', group: 'workspace', keywords: 'integration connection sso oauth microsoft google service koppling anslutning inloggning leverantor tjanst', available: workspaceNavigation },
  { id: 'get-started', command: 'setup', label: 'Set up this workspace', description: 'Welcome your team and connect your own services.', group: 'workspace', keywords: 'setup start onboarding invite configure kom igang konfigurera installera bjud valkomna', available: workspaceManager },
  { id: 'feedback', command: 'feedback', label: 'Workspace feedback', description: 'Share feedback about how this workspace is working.', group: 'workspace', keywords: 'feedback report suggestion issue synpunkt synpunkter aterkoppling forslag problem', available: context => context.hasWorkspace },
  { id: 'data', command: 'data', label: 'Data & retention', description: 'Review workspace exports, retention and data controls.', group: 'workspace', keywords: 'data export retention governance archive privacy lagring arkiv dataskydd bevarande gallring', available: context => channelWork(context) && context.nativeSession && context.workspaceRole === 'owner' },
  { id: 'admin', command: 'admin', label: 'Administration', description: 'Manage this FreeCollab installation.', group: 'workspace', keywords: 'admin administration installation system administrator hantera administrera', available: context => context.hasAdmin && context.nativeSession && !context.embedded },
  { id: 'my-day', command: 'my-day', label: 'My day', description: 'Your assigned work, reminders and things to return to.', group: 'personal', keywords: 'my day today personal assigned reminder min dag idag tilldelat personligt', available: workspaceNavigation },
  { id: 'notifications', command: 'notifications', label: 'Your notifications', description: 'Mentions, replies and updates waiting for you.', group: 'personal', keywords: 'notification inbox mention reply update notis notiser avisering aviseringar inkorg svar', available: context => context.hasWorkspace },
  { id: 'status', command: 'status', label: 'Set your status', description: 'Let teammates know your availability.', group: 'personal', keywords: 'status presence availability away busy online tillganglighet narvaro upptagen borta', available: context => context.hasWorkspace },
  { id: 'mobile', command: 'mobile', label: 'Mobile & offline', description: 'Your device, encrypted outbox and offline preferences.', group: 'personal', keywords: 'mobile offline device outbox push install mobil enhet utkorg avisering', available: () => true },
  { id: 'appearance', command: 'appearance', label: 'Appearance settings', description: 'Choose your colors, fonts and personal reading space.', group: 'personal', keywords: 'appearance theme font typography color colour light dark utseende tema typsnitt farg farger ljust morkt', available: () => true },
  { id: 'account', command: 'account', label: 'Account & language', description: 'Your language, identity and account preferences.', group: 'personal', keywords: 'account language profile avatar photo sign in security konto sprak profil bild foto inloggning sakerhet', available: () => true },
  { id: 'secure-settings', command: 'encryption', label: 'Encryption & trusted devices', description: 'Manage verified devices, encrypted history and recovery.', group: 'personal', keywords: 'secure encryption trusted verify device key recovery saker kryptering betrodd verifiera enhet nyckel aterstallning', available: () => true },
];

function normalize(value: string): string {
  return value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
}

/** Parse an intentional, single-line composer shortcut, never a URL or // text. */
export function getSlashQuery(value: string): string | null {
  if (!value.startsWith('/') || value.startsWith('//') || value.length > 129 || /[\r\n\u2028\u2029]/.test(value)) return null;
  return value.slice(1).trim();
}

/** Searches local feature metadata only; never reads or sends conversation text. */
export function getSlashCommands(context: SlashContext, query = ''): SlashCommand[] {
  const normalized = normalize(query.replace(/^\//, ''));
  return definitions.filter(item => !item.available || item.available(context)).map((item, index) => {
    const description = item.id === 'cloud' && context.secure ? 'Share encrypted links to your provider files.' : item.description;
    const command: SlashCommand = { id: item.id, command: item.command, label: t(item.label), description: t(description), group: item.group, keywords: item.keywords };
    const shortcut = normalize(item.command);
    const label = normalize(command.label);
    const original = normalize(item.label);
    const searchable = normalize([item.command, item.label, command.label, item.description, command.description, item.keywords].join(' '));
    const score = !normalized ? 0 : shortcut === normalized || label === normalized || original === normalized ? 0 : shortcut.startsWith(normalized) || label.startsWith(normalized) || original.startsWith(normalized) ? 1 : normalized.split(' ').every(word => searchable.includes(word)) ? 2 : -1;
    return { command, index, score };
  }).filter(item => item.score >= 0).sort((left, right) => left.score - right.score || left.index - right.index).map(item => item.command);
}
