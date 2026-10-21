import type { Labels, ModuleOptions } from './types.js';
const en: Labels = {
  chat: 'Conversation', files: 'Files', feed: 'Activity feed', notifications: 'Notifications', refresh: 'Refresh',
  loading: 'Connecting…', signedOut: 'Sign in through your application to continue.', denied: 'This user does not have access to this channel.', destroyed: 'Collaboration disconnected.', empty: 'Nothing here yet.',
  message: 'Message', send: 'Send message', post: 'Post', publish: 'Publish post', file: 'Choose a file', upload: 'Upload file', download: 'Download', markRead: 'Mark read', open: 'Open notification', unread: 'Unread', read: 'Read',
  nativePrivacy: 'Native components · The server processes supported content over HTTPS. Encrypted channels require the trusted FreeCollab client.', fileLimit: 'Up to 50 MB. Files are downloaded as attachments.', genericFailure: 'The request could not be completed.',
  tasks: 'Projects and tasks', documents: 'Shared documents', meetings: 'Meetings', calendar: 'Calendar', workflows: 'Workflows', decisions: 'Decisions', search: 'Search',
  title: 'Title', description: 'Description', create: 'Create', save: 'Save', cancel: 'Cancel', edit: 'Edit', remove: 'Delete', reply: 'Reply', replies: 'Replies', react: 'Like', bookmark: 'Bookmark', project: 'Project', status: 'Status', todo: 'To do', doing: 'In progress', done: 'Done', newProject: 'Create project', newTask: 'Create task', body: 'Document text', openItem: 'Open in your application', starts: 'Starts', ends: 'Ends', location: 'Location', startMeeting: 'Start meeting', endMeeting: 'End meeting', startWorkflow: 'Start workflow', approve: 'Approve', reject: 'Reject', proposed: 'Proposed', accepted: 'Accepted', superseded: 'Superseded', searchRecent: 'Search recent messages', searchLimit: 'Search up to 500 recent messages locally. Older history is available in the full client.', preview: 'Preview', close: 'Close', versions: 'Versions',
};
const sv: Labels = {
  chat: 'Samtal', files: 'Filer', feed: 'Aktivitetsflöde', notifications: 'Aviseringar', refresh: 'Uppdatera',
  loading: 'Ansluter…', signedOut: 'Logga in genom din applikation för att fortsätta.', denied: 'Användaren saknar åtkomst till kanalen.', destroyed: 'Samarbetet är frånkopplat.', empty: 'Här är det tomt än så länge.',
  message: 'Meddelande', send: 'Skicka meddelande', post: 'Inlägg', publish: 'Publicera inlägg', file: 'Välj en fil', upload: 'Ladda upp fil', download: 'Ladda ned', markRead: 'Markera som läst', open: 'Öppna avisering', unread: 'Oläst', read: 'Läst',
  nativePrivacy: 'Komponenter för vanliga kanaler · Servern behandlar innehållet via HTTPS. Krypterade kanaler kräver FreeCollabs betrodda klient.', fileLimit: 'Högst 50 MB. Filer hämtas som bilagor.', genericFailure: 'Begäran kunde inte genomföras.',
  tasks: 'Projekt och uppgifter', documents: 'Gemensamma dokument', meetings: 'Möten', calendar: 'Kalender', workflows: 'Arbetsflöden', decisions: 'Beslut', search: 'Sök',
  title: 'Titel', description: 'Beskrivning', create: 'Skapa', save: 'Spara', cancel: 'Avbryt', edit: 'Redigera', remove: 'Ta bort', reply: 'Svara', replies: 'Svar', react: 'Gilla', bookmark: 'Bokmärk', project: 'Projekt', status: 'Status', todo: 'Att göra', doing: 'Pågår', done: 'Klart', newProject: 'Skapa projekt', newTask: 'Skapa uppgift', body: 'Dokumenttext', openItem: 'Öppna i din applikation', starts: 'Börjar', ends: 'Slutar', location: 'Plats', startMeeting: 'Starta möte', endMeeting: 'Avsluta möte', startWorkflow: 'Starta arbetsflöde', approve: 'Godkänn', reject: 'Avslå', proposed: 'Föreslaget', accepted: 'Godkänt', superseded: 'Ersatt', searchRecent: 'Sök bland senaste meddelanden', searchLimit: 'Sök lokalt i högst 500 senaste meddelanden. Äldre historik finns i hela klienten.', preview: 'Förhandsvisa', close: 'Stäng', versions: 'Versioner',
};
export const labelsFor = (options: ModuleOptions): Labels => ({ ...(options.locale === 'sv' ? sv : en), ...options.labels });
const errorsSv: Readonly<Record<string, string>> = {
  'unauthenticated': 'Logga in på nytt genom din applikation.',
  'access-denied': 'Användaren saknar åtkomst till kanalen.',
  'scope-mismatch': 'Innehållets behörighet matchar inte vald arbetsyta och kanal.',
  'secure-channel-unsupported': 'Kanalen kräver FreeCollabs betrodda krypterade klient och verifierade enheter. Dessa komponenter kan inte läsa eller skicka innehåll i krypterade kanaler.',
  'request-failed': 'Begäran kunde inte genomföras. Försök igen.',
  'authentication-failed': 'Den aktuella användaren kunde inte verifieras.',
  'invalid-token': 'Inloggningsfunktionen måste returnera en aktuell användartoken.',
  'invalid-content': 'Skriv giltigt innehåll inom formulärets längdgräns.',
  'file-too-large': 'Filen får vara högst 50 MB.',
  'invalid-document': 'Dokumentets innehåll eller historik kunde inte verifieras. Stäng dokumentet och öppna det igen.',
  'document-limit': 'Dokumentet behöver ytterligare en synkronisering innan det kan fortsätta.',
  'invalid-pagination': 'Historikens sidindelning kunde inte verifieras. Försök att uppdatera.',
  'destroyed': 'Anslutningen till samarbetet har avslutats.',
};
export const errorLabel = (code: string, message: string, options: ModuleOptions) => options.errorMessages?.[code] || (options.locale === 'sv' ? errorsSv[code] : null) || message;
