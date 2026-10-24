import { t, useI18n } from '../i18n';

const source = 'https://github.com/freecollab-eu/freecollab/blob/design/open-studio/docs/';
const topics = [
  ['Conversations and channels', 'Messages, direct conversations, threads, reactions and notifications share current channel permissions. Channel administrators can review and save channel details.', 'CHANNELS.md'],
  ['Documents and stacks', 'Write together with comments and named revisions. Native channels also organize pages in stacks and save reviewed document connections. Encrypted pages keep their separate Matrix editing path.', 'SHARED_DOCUMENTS.md'],
  ['Projects, tasks and decisions', 'Use project boards, assignees, deadlines, checklists and revisioned decisions. AI suggestions update reviewed drafts; creating or saving work remains an explicit action.', 'PROJECT_TASKS.md'],
  ['Files and Office', 'Organize and version files, review supported previews and open native documents through the configured Office saveback flow. Supported formats and client-encrypted boundaries are documented separately.', 'FILE_EXPERIENCE.md'],
  ['Calendar and meetings', 'Plan channel events, supported recurring series and reviewed invitations. Browser calls and optional LiveKit use the installation’s media services; external meeting providers need separate grants.', 'CALENDAR_PLANNING.md'],
  ['Personal appearance and language', 'Save your own colors, typeface, spacing and light or dark appearance. Swedish and English are selectable; bundled fonts do not need an external font service.', 'APPEARANCE.md'],
  ['AI where you work', 'Review help beside messages, drafts, documents and tasks. Your workspace chooses the provider. Each request identifies what is shared, its destination and possible charges; no suggestion sends a message automatically.', 'COPILOT.md'],
  ['Your organization’s services', 'Connect your organization’s Microsoft 365, Google Workspace and supported external services. Customer SSO and content access have separate configuration and consent.', 'CONNECTION_SETUP.md'],
  ['Embedded collaboration', 'Mount eleven native modules with Web Components or React, or embed the trusted workspace for Matrix encryption. Build a reviewed SDK tarball; npm publication is a separate release step.', 'COMPONENT_SDK.md'],
  ['Mobile, offline and calling', 'Use the PWA and encrypted offline drafts or the native clients. Push and background calling need customer services, platform signing and acceptance on physical devices.', 'NATIVE_CLIENTS.md'],
  ['Exports, governance and operation', 'Review native exports, retention and account closure separately. Workers and operational diagnostics enforce documented scopes; they cannot erase recipients’ Matrix copies or verify external backup delivery.', 'DATA_CONTROLS.md'],
] as const;

/** Current product help links to reviewable implementation contracts. */
export function ProductGuide() {
  useI18n();
  return <div className="space-y-6" data-product-guide>
    <p className="fc-muted">{t('Run FreeCollab on your own computer or a server in your private network. Accounts belong to that installation; no central FreeCollab cloud account is required.')}</p>
    <p><a className="fc-account-link" href={source + 'SELF_HOSTING.md'} target="_blank" rel="noopener noreferrer">{t('Local and network installation')}</a></p>
    <p><a className="fc-account-link" href={source + 'PRODUCT_DEMO.md'} target="_blank" rel="noopener noreferrer">{t('The same product, with example data')}</a></p>
    <p className="fc-muted">{t('Use these guides for the implemented product, its configuration and its limits. The demo runs the same app and API with temporary example accounts. External providers are disabled in that installation.')}</p>
    <p className="fc-muted">{t('These guides describe the current review branch. Its latest changes are not yet a published release.')}</p>
    <p><a className="fc-account-link" href={source + 'FEATURE_AUDIT.md'} target="_blank" rel="noopener noreferrer">{t('Feature status, code and verification')}</a></p>
    <dl className="space-y-6">{topics.map(([label, description, document]) => <div key={document}>
      <dt className="font-semibold"><a className="fc-account-link" href={source + document} target="_blank" rel="noopener noreferrer">{t(label)}</a></dt>
      <dd className="fc-muted mt-2">{t(description)}</dd>
    </div>)}</dl>
    <p className="fc-muted">{t('Operator configuration is part of self-hosting. Protocol tests do not establish live customer-provider acceptance, app-store publication or an independent security review.')}</p>
  </div>;
}
