import { useCallback, useEffect, useRef, useState } from 'react';
import { Building2, Download, Fingerprint, FolderLock, Loader2, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { t, useI18n } from '../i18n';
import { api } from '../lib/api';
import { dataControlsApi, type DataState, type Reauthentication } from '../lib/data-controls-api';
import { governanceApi, type CaseCollection, type DLPSurface, type DLPPolicy, type GovernanceAudit, type GovernanceOrganization, type GovernanceState, type PreservationCase, type UsageRow } from '../lib/enterprise-governance-api';
import { workspaceApi, type WorkspaceMember } from '../lib/workspace-api';
import { EnterpriseAlerts, enterpriseDate, enterpriseFailure, NativeConfirmation, type IntegrationGuard } from './EnterpriseControls';
import '../styles/enterprise-governance.css';

interface Props { workspaceId: string; mfaEnabled?: boolean; onGuardChange?: (guard: IntegrationGuard) => void }
type Section = 'organization' | 'policies' | 'cases' | 'audit';
interface Pending { title: string; explanation: string; run: (auth: Reauthentication) => Promise<unknown> }
const allSurfaces: DLPSurface[] = ['message', 'file', 'app', 'assistant', 'search', 'document'];

export function EnterpriseGovernancePanel({ workspaceId, mfaEnabled = false, onGuardChange }: Props) {
  useI18n();
  const [section, setSection] = useState<Section>('organization');
  const [state, setState] = useState<GovernanceState | null>(null);
  const [organizations, setOrganizations] = useState<GovernanceOrganization[]>([]);
  const [organization, setOrganization] = useState<GovernanceOrganization | null>(null);
  const [usage, setUsage] = useState<UsageRow[]>([]);
  const [data, setData] = useState<DataState | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [events, setEvents] = useState<GovernanceAudit[]>([]);
  const [orgName, setOrgName] = useState('');
  const [country, setCountry] = useState('');
  const [region, setRegion] = useState('');
  const [orgMember, setOrgMember] = useState('');
  const [orgRole, setOrgRole] = useState('admin');
  const [policyName, setPolicyName] = useState('');
  const [detector, setDetector] = useState<DLPPolicy['detector']>('credit_card');
  const [policyAction, setPolicyAction] = useState<DLPPolicy['action']>('warn');
  const [literal, setLiteral] = useState('');
  const [surfaces, setSurfaces] = useState<DLPSurface[]>(['message', 'file', 'app', 'assistant', 'search']);
  const [opaqueFiles, setOpaqueFiles] = useState(false);
  const [caseTitle, setCaseTitle] = useState('');
  const [caseChannels, setCaseChannels] = useState<string[]>([]);
  const [custodians, setCustodians] = useState<string[]>([]);
  const [selectedCase, setSelectedCase] = useState<PreservationCase | null>(null);
  const [collectionChannels, setCollectionChannels] = useState<string[]>([]);
  const [collections, setCollections] = useState<CaseCollection[]>([]);
  const [consent, setConsent] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const scope = useRef({ workspaceId, token: api.getAccessToken(), generation: 0, mounted: true });
  const selectedOrganizationId = useRef(organization?.id);
  const selectedCaseId = useRef(selectedCase?.id);
  selectedOrganizationId.current = organization?.id;
  selectedCaseId.current = selectedCase?.id;
  const valid = useCallback((generation: number) => scope.current.mounted && scope.current.generation === generation && scope.current.workspaceId === workspaceId && scope.current.token === api.getAccessToken(), [workspaceId]);
  const refresh = useCallback(async () => {
    const generation = scope.current.generation;
    const [next, orgs, controls, people, audit] = await Promise.all([governanceApi.state(workspaceId), governanceApi.organizations(), dataControlsApi.state(workspaceId), workspaceApi.members(workspaceId), governanceApi.audit(workspaceId)]);
    if (!valid(generation)) return;
    setState(next); setOrganizations(orgs.organizations); setData(controls); setMembers(people); setEvents(audit.events);
    setSelectedCase(previous => previous ? next.cases.find(item => item.id === previous.id) || null : null);
    setOrganization(previous => previous ? orgs.organizations.find(item => item.id === previous.id) || null : orgs.organizations.find(item => item.id === next.organization_id) || null);
  }, [workspaceId, valid]);
  useEffect(() => {
    scope.current = { workspaceId, token: api.getAccessToken(), generation: scope.current.generation + 1, mounted: true };
    const generation = scope.current.generation;
    setState(null); setOrganization(null); setOrganizations([]); setSelectedCase(null); setCollections([]); setPending(null); setError(''); setNotice(''); setLoading(true); setLiteral('');
    void refresh().catch(failure => { if (valid(generation)) setError(enterpriseFailure(failure)); }).finally(() => { if (valid(generation)) setLoading(false); });
    return () => { scope.current.mounted = false; scope.current.generation++; onGuardChange?.(null); };
  }, [workspaceId, refresh, valid, onGuardChange]);
  useEffect(() => {
    onGuardChange?.(busy ? 'busy' : null);
  }, [busy, onGuardChange]);
  useEffect(() => {
    const generation = scope.current.generation, identifier = organization?.id;
    if (!identifier) { setUsage([]); return; }
    setUsage([]);
    void governanceApi.usage(identifier).then(result => { if (valid(generation) && selectedOrganizationId.current === identifier) setUsage(result.workspaces); }).catch(failure => { if (valid(generation) && selectedOrganizationId.current === identifier) setError(enterpriseFailure(failure)); });
  }, [organization, valid]);
  const reloadCollections = useCallback(async (caseId: string) => {
    const generation = scope.current.generation;
    const result = await governanceApi.collections(workspaceId, caseId);
    if (valid(generation) && selectedCaseId.current === caseId) setCollections(result.exports);
  }, [workspaceId, valid]);
  useEffect(() => {
    setCollections([]); setConsent(false); setCollectionChannels([]);
    const generation = scope.current.generation, identifier = selectedCase?.id;
    if (identifier) void reloadCollections(identifier).catch(failure => { if (valid(generation) && selectedCaseId.current === identifier) setError(enterpriseFailure(failure)); });
  }, [selectedCase?.id, reloadCollections, valid]);
  function confirm(title: string, explanation: string, run: Pending['run']) { setError(''); setNotice(''); setPending({ title, explanation, run }); }
  async function execute(auth: Reauthentication) {
    const generation = scope.current.generation;
    if (!pending || busy || !valid(generation)) return;
    setBusy(true);
    try {
      await pending.run(auth);
      if (!valid(generation)) return;
      setPending(null); setLiteral(''); setNotice(t('Governance settings updated.')); await refresh();
      if (selectedCase && valid(generation)) await reloadCollections(selectedCase.id);
    } catch (failure) { if (valid(generation)) setError(enterpriseFailure(failure)); }
    finally { if (valid(generation)) setBusy(false); }
  }
  function toggle(value: string, values: string[], setter: (values: string[]) => void) { setter(values.includes(value) ? values.filter(item => item !== value) : [...values, value]); }
  async function download(job: CaseCollection, auth: Reauthentication) {
    const generation = scope.current.generation;
    const blob = await dataControlsApi.download(workspaceId, job.id, auth);
    if (!valid(generation)) return;
    const url = URL.createObjectURL(blob), anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'freecollab-evidence-' + job.id + '.zip'; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const selectChannels = (values: string[], setter: (values: string[]) => void, nativeOnly = false) => <div className="fc-governance-choice-list">{data?.channels.filter(item => !nativeOnly || !item.secure).map(item => <label key={item.id} className="fc-account-checkbox"><input type="checkbox" checked={values.includes(item.id)} disabled={busy} onChange={() => toggle(item.id, values, setter)} /><span>{item.name || t('Encrypted channel')} {item.secure && <span className="fc-account-muted">· {t('native metadata only')}</span>}</span></label>)}</div>;
  return <section className="fc-governance" aria-label={t('Enterprise governance')} aria-busy={busy || loading}>
    <header className="fc-governance-intro"><ShieldCheck size={25} /><div><span className="fc-kicker">{t('Governance')}</span><h3>{t('Shared standards. Clear boundaries.')}</h3><p>{t('Manage workspaces, protect native content and preserve evidence with explicit access.')}</p></div></header>
    <EnterpriseAlerts error={error} notice={notice} />
    <nav className="fc-governance-tabs" aria-label={t('Governance sections')}>{(['organization', 'policies', 'cases', 'audit'] as Section[]).map(item => <button type="button" key={item} className="fc-button" aria-current={section === item ? 'page' : undefined} disabled={busy} onClick={() => setSection(item)}>{t(({ organization: 'Organization', policies: 'Content policies', cases: 'Preservation cases', audit: 'Audit trail' })[item])}</button>)}<button type="button" className="fc-icon-button" aria-label={t('Refresh governance')} disabled={busy || loading} onClick={() => { void refresh().catch(failure => setError(enterpriseFailure(failure))); }}><RefreshCw size={15} /></button></nav>
    {loading && <p role="status"><Loader2 size={16} className="fc-account-spinner" />{t('Loading governance…')}</p>}
    {!loading && state && <>
      {section === 'organization' && <>
        <section className="fc-account-section"><h3><Building2 size={17} /> {t('Your organization')}</h3><p className="fc-account-muted">{t('Organization roles manage policy and usage counts. Channel membership controls access to conversations. Each workspace keeps its own identity provider.')}</p>
          <label htmlFor="governance-organization">{t('Organization')}</label><select id="governance-organization" className="fc-input" value={organization?.id || ''} onChange={event => setOrganization(organizations.find(item => item.id === event.target.value) || null)}><option value="">{t('Select an organization')}</option>{organizations.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
          {organization && <><div className="fc-governance-inline"><span className="fc-badge">{organization.role}</span><span>{organization.workspaces.length} {t('workspaces')}</span><span>{organization.country_code || t('Country not recorded')}</span></div><p className="fc-account-muted">{t('Hosting location is operator supplied metadata. Set the actual hosting region in your own infrastructure.')}</p>{!organization.workspaces.some(item => item.id === workspaceId) && organization.role !== 'auditor' && <button type="button" className="fc-button" disabled={busy} onClick={() => confirm(t('Attach this workspace'), t('Confirm that you own this workspace and can attach it to this organization.'), auth => governanceApi.link(organization.id, workspaceId, auth))}>{t('Attach this workspace')}</button>}{organization.role === 'owner' && <label className="fc-account-checkbox"><input type="checkbox" checked={organization.require_sso} disabled={busy} onChange={event => { const required = event.target.checked; confirm(t('Update organization sign-in policy'), t('Each attached workspace must have its own enabled SSO. Workspace owners retain their emergency account access.'), auth => governanceApi.policy(organization.id, { ...auth, expected_revision: organization.revision, require_sso: required, country_code: organization.country_code, region_label: organization.region_label })); }} />{t('Require each workspace’s SSO for members')}</label>}
          <div className="fc-governance-metrics">{usage.map(item => <article key={item.workspace_id}><strong>{organization.workspaces.find(workspace => workspace.id === item.workspace_id)?.name || item.workspace_id}</strong><dl><div><dt>{t('Members')}</dt><dd>{item.members}</dd></div><div><dt>{t('Channels')}</dt><dd>{item.channels}</dd></div><div><dt>{t('Encrypted channels')}</dt><dd>{item.secure_channels}</dd></div><div><dt>{t('Native messages')}</dt><dd>{item.native_messages}</dd></div><div><dt>{t('Stored files')}</dt><dd>{(item.stored_file_bytes / 1048576).toFixed(1)} MB</dd></div></dl></article>)}</div>
          {organization.role === 'owner' && <div className="fc-governance-form"><h4>{t('Organization administrators')}</h4><label htmlFor="governance-member">{t('Account ID')}</label><input id="governance-member" className="fc-input" value={orgMember} onChange={event => setOrgMember(event.target.value)} /><label htmlFor="governance-role">{t('Role')}</label><select id="governance-role" className="fc-input" value={orgRole} onChange={event => setOrgRole(event.target.value)}>{['owner', 'admin', 'auditor', 'remove'].map(value => <option value={value} key={value}>{t(value)}</option>)}</select><button type="button" className="fc-button" disabled={!orgMember.trim() || busy} onClick={() => confirm(t('Update organization role'), t('This changes management access and does not add the account to channels.'), auth => governanceApi.member(organization.id, orgMember.trim(), orgRole, auth))}>{t('Update role')}</button></div>}</>}
        </section>
        <section className="fc-account-section fc-governance-form"><h3>{t('Create an organization')}</h3><label htmlFor="governance-org-name">{t('Name')}</label><input id="governance-org-name" className="fc-input" value={orgName} maxLength={100} onChange={event => setOrgName(event.target.value)} /><div className="fc-governance-form-row"><div><label htmlFor="governance-country">{t('Hosting country code')}</label><input id="governance-country" className="fc-input" placeholder="SE" value={country} maxLength={2} onChange={event => setCountry(event.target.value.toUpperCase())} /></div><div><label htmlFor="governance-region">{t('Hosting region label')}</label><input id="governance-region" className="fc-input" value={region} maxLength={120} onChange={event => setRegion(event.target.value)} /></div></div><button type="button" className="fc-button" disabled={!orgName.trim() || busy || Boolean(country && !/^[A-Z]{2}$/.test(country))} onClick={() => confirm(t('Create an organization'), t('You will become the first organization owner.'), auth => governanceApi.createOrganization({ ...auth, name: orgName.trim(), country_code: country, region_label: region }))}><Plus size={15} />{t('Create organization')}</button></section>
      </>}
      {section === 'policies' && <>
        <section className="fc-account-section"><h3><Fingerprint size={17} /> {t('Native content protection')}</h3><p className="fc-account-muted">{t('Policies inspect server processed native content. Matrix messages and encrypted attachments remain client encrypted and cannot be inspected here. Binary files require an explicit deny policy when inspection is unavailable.')}</p>{state.policies.length === 0 && <p>{t('No content policy is enabled by default.')}</p>}{state.policies.map(item => <article key={item.id} className="fc-governance-item"><div><strong>{item.name}</strong><p>{item.detector} · {t(item.action)} · {item.surfaces.join(', ')}</p></div><button type="button" className="fc-button" disabled={busy} onClick={() => confirm(t('Update content policy'), t('The policy takes effect for subsequent native operations in this workspace.'), auth => governanceApi.updateDLP(workspaceId, item, !item.enabled, auth))}>{item.enabled ? t('Disable') : t('Enable')}</button></article>)}</section>
        <section className="fc-account-section fc-governance-form"><h3>{t('Add a content policy')}</h3><label htmlFor="governance-policy-name">{t('Policy name')}</label><input id="governance-policy-name" className="fc-input" value={policyName} maxLength={100} onChange={event => setPolicyName(event.target.value)} /><div className="fc-governance-form-row"><div><label htmlFor="governance-detector">{t('Detect')}</label><select id="governance-detector" className="fc-input" value={detector} onChange={event => setDetector(event.target.value as DLPPolicy['detector'])}><option value="credit_card">{t('Payment card numbers')}</option><option value="aws_access_key">{t('AWS access key IDs')}</option><option value="email">{t('Email addresses')}</option><option value="literal">{t('A protected phrase')}</option></select></div><div><label htmlFor="governance-action">{t('Action')}</label><select id="governance-action" className="fc-input" value={policyAction} onChange={event => setPolicyAction(event.target.value as DLPPolicy['action'])}><option value="warn">{t('Warn and audit')}</option><option value="block">{t('Block')}</option></select></div></div>{detector === 'literal' && <><label htmlFor="governance-literal">{t('Protected phrase')}</label><input id="governance-literal" className="fc-input" type="password" autoComplete="new-password" value={literal} minLength={3} maxLength={200} onChange={event => setLiteral(event.target.value)} /></>}<fieldset className="fc-governance-fieldset"><legend>{t('Native surfaces')}</legend>{allSurfaces.map(value => <label className="fc-account-checkbox" key={value}><input type="checkbox" checked={surfaces.includes(value)} onChange={() => setSurfaces(previous => previous.includes(value) ? previous.filter(item => item !== value) : [...previous, value])} />{t(value)}</label>)}</fieldset><p className="fc-account-muted">{t('Collaborative document updates use a binary format. A blocking document policy denies those updates because this scanner cannot reliably inspect their text.')}</p><label className="fc-account-checkbox"><input type="checkbox" checked={opaqueFiles} onChange={event => setOpaqueFiles(event.target.checked)} />{t('Block files that cannot be inspected when this is a blocking policy')}</label><button type="button" className="fc-button" disabled={busy || !policyName.trim() || surfaces.length === 0 || detector === 'literal' && literal.length < 3} onClick={() => confirm(t('Add content policy'), t('Apply this rule to the selected native surfaces in this workspace.'), auth => governanceApi.createDLP(workspaceId, { ...auth, name: policyName.trim(), detector, action: policyAction, surfaces, enabled: true, block_uninspectable_files: opaqueFiles, ...(detector === 'literal' ? { literal } : {}) }))}>{t('Add policy')}</button></section>
        <section className="fc-account-section"><h3>{t('Customer managed keys')}</h3><p>{state.kms.enabled ? t('Vault Transit envelope encryption is enabled for new native encrypted content.') : t('Native encrypted content uses the installation’s versioned key ring.')}</p><p className="fc-account-muted">{t('Vault access and key retention are configured by your hosting operator. Existing key versions remain necessary to read older content. Matrix keys remain on verified devices.')}</p></section>
      </>}
      {section === 'cases' && <>
        <section className="fc-account-section"><h3><FolderLock size={17} /> {t('Preserve before collecting')}</h3><p className="fc-account-muted">{t('An active case prevents native evidence from being overwritten or erased. Evidence exports require your current membership in every selected channel. Matrix history, provider copies and backups need their own preservation process.')}</p><div className="fc-governance-inline"><span className="fc-badge">{state.retention_hold ? t('Workspace retention hold active') : t('Workspace retention hold off')}</span><span className="fc-badge">{state.case_hold_active ? t('Case preservation active') : t('No active case hold')}</span></div>{state.cases.map(item => <article key={item.id} className="fc-governance-item"><div><strong>{item.title}</strong><p>{t(item.status)} · {enterpriseDate(item.created_at)} · {item.custodian_ids.length || t('All')} {t('custodians')}</p></div><div className="fc-account-actions"><button type="button" className="fc-button" disabled={busy} onClick={() => setSelectedCase(item)}>{t('Evidence')}</button>{item.status === 'active' && <button type="button" className="fc-button" disabled={busy} onClick={() => confirm(t('Release case preservation'), t('Native edits and deletion can resume where no other case applies. Retention purges still follow the workspace retention policy.'), auth => governanceApi.release(workspaceId, item, auth))}>{t('Release')}</button>}</div></article>)}</section>
        {selectedCase && <section className="fc-account-section"><h3>{selectedCase.title} · {t('Evidence')}</h3><p className="fc-account-muted">{t('Collect full native channel context for review. Custodian holds limit preservation; the export includes the selected channels’ context.')}</p>{selectChannels(collectionChannels, setCollectionChannels, true)}<label className="fc-account-checkbox"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />{t('I authorize a private plaintext evidence export.')}</label><button type="button" className="fc-button" disabled={busy || !consent || !collectionChannels.length || selectedCase.status !== 'active'} onClick={() => confirm(t('Collect case evidence'), t('Preservation must remain active while the export is collected.'), auth => governanceApi.collect(workspaceId, selectedCase, collectionChannels, auth))}>{t('Collect evidence')}</button>{collections.map(job => <article key={job.id} className="fc-governance-item"><div><strong>{t(job.status)} · {t(job.review_state)}</strong><p>{enterpriseDate(job.created_at)}</p>{job.sha256 && <code className="fc-governance-digest">SHA-256 {job.sha256}</code>}{job.failure && <p role="status">{job.failure}</p>}</div><div className="fc-account-actions">{job.status === 'queued' && <button type="button" className="fc-button" disabled={busy} onClick={() => confirm(t('Run evidence collection'), t('Your current session, channel access and preservation case are checked again.'), auth => dataControlsApi.run(workspaceId, job.id, auth))}>{t('Run collection')}</button>}{job.download_available && <><button type="button" className="fc-button" disabled={busy} onClick={() => confirm(t('Download evidence'), t('Keep the downloaded plaintext archive in approved private storage.'), auth => download(job, auth))}><Download size={15} />{t('Download')}</button><button type="button" className="fc-button" disabled={busy} onClick={() => confirm(t('Record evidence review'), t('Record that you inspected this evidence collection.'), auth => governanceApi.review(workspaceId, selectedCase.id, job.id, 'reviewed', auth))}>{t('Mark reviewed')}</button></>}</div></article>)}</section>}
        <section className="fc-account-section fc-governance-form"><h3>{t('Open a preservation case')}</h3><label htmlFor="governance-case-title">{t('Case title')}</label><input id="governance-case-title" className="fc-input" value={caseTitle} maxLength={200} onChange={event => setCaseTitle(event.target.value)} /><fieldset className="fc-governance-fieldset"><legend>{t('Channels — leave empty for the whole workspace')}</legend>{selectChannels(caseChannels, setCaseChannels, true)}</fieldset><fieldset className="fc-governance-fieldset"><legend>{t('Custodians — leave empty for all authors')}</legend><div className="fc-governance-choice-list">{members.map(person => <label className="fc-account-checkbox" key={person.user_id}><input type="checkbox" checked={custodians.includes(person.user_id)} onChange={() => toggle(person.user_id, custodians, setCustodians)} />{person.name}</label>)}</div></fieldset><button type="button" className="fc-button" disabled={busy || !caseTitle.trim()} onClick={() => confirm(t('Activate case preservation'), t('This immediately prevents native deletion and overwriting within the selected scope.'), auth => governanceApi.createCase(workspaceId, { ...auth, title: caseTitle.trim(), channel_ids: caseChannels, custodian_ids: custodians, acknowledge_native_scope: true }))}>{t('Preserve evidence')}</button></section>
      </>}
      {section === 'audit' && <section className="fc-account-section"><h3>{t('Governance audit trail')}</h3><p className="fc-account-muted">{t('Records contain actor IDs, actions and resource IDs. Matched text, case titles and keys are excluded. Database records are not an externally attested immutable ledger.')}</p>{events.length === 0 && <p>{t('No governance events yet.')}</p>}{events.map(item => <article className="fc-governance-item" key={item.id}><div><strong>{item.action}</strong><p>{item.actor_id} · {enterpriseDate(item.created_at)}</p><code className="fc-governance-digest">{item.target_id}</code></div></article>)}</section>}
    </>}
    {pending && <NativeConfirmation id="enterprise-governance-confirm" title={pending.title} mfa={mfaEnabled} busy={busy} onCancel={() => setPending(null)} onConfirm={execute}><p>{pending.explanation}</p></NativeConfirmation>}
  </section>;
}
