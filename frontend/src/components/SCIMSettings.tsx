import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Copy, KeyRound, Loader2, Plus, RefreshCw, Trash2, Users } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { scimApi, type SCIMAuditEvent, type SCIMMapping, type SCIMMappings, type SCIMStatus } from '../lib/enterprise-api';
import type { AccountSecurity } from '../lib/account-api';
import type { SSOReauthentication } from '../lib/integrations-api';
import { EnterpriseAlerts, NativeConfirmation, OneTimeSecret, enterpriseDate, enterpriseFailure, type IntegrationGuard } from './EnterpriseControls';

type Action = 'token' | 'disable' | 'mappings';
const describeAction = (value: string) => value.replace(/[._-]/g, ' ');
export function SCIMSettings({ workspaceId, security, isOwner, displayChannels, onGuardChange, onDirtyChange }: { workspaceId: string; security: AccountSecurity | null; isOwner: boolean; displayChannels: Channel[]; onGuardChange: (guard: IntegrationGuard) => void; onDirtyChange?: (dirty: boolean) => void }) {
  useI18n();
  const [status, setStatus] = useState<SCIMStatus | null>(null);
  const [directory, setDirectory] = useState<SCIMMappings | null>(null);
  const [mappings, setMappings] = useState<SCIMMapping[]>([]);
  const [defaultRole, setDefaultRole] = useState<'member' | 'guest'>('member');
  const [groupToAdd, setGroupToAdd] = useState('');
  const [audit, setAudit] = useState<SCIMAuditEvent[]>([]);
  const [secret, setSecret] = useState('');
  const [saved, setSaved] = useState(false);
  const [action, setAction] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const owner = useRef(api.getAccessToken());
  const mounted = useRef(true);
  const generation = useRef(0);
  const current = () => mounted.current && owner.current === api.getAccessToken();
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; generation.current++; onGuardChange(null); }; }, [workspaceId]);
  useEffect(() => { onGuardChange(busy ? 'busy' : secret && !saved ? 'secret' : null); }, [busy, secret, saved, onGuardChange]);
  async function load() {
    const request = ++generation.current; setLoading(true); setError('');
    try {
      const [config, records, history] = await Promise.all([scimApi.status(workspaceId), scimApi.mappings(workspaceId), scimApi.audit(workspaceId)]);
      if (!current() || request !== generation.current) return;
      setStatus(config); setDirectory(records); setMappings(records.mappings); setDefaultRole(records.default_role); setAudit(history.events);
    } catch (failure) { if (current() && request === generation.current) setError(enterpriseFailure(failure)); }
    finally { if (current() && request === generation.current) setLoading(false); }
  }
  async function confirm(values: SSOReauthentication) {
    if (!action || busy) return;
    const selected = action; setBusy(true); setError(''); setNotice(''); onGuardChange('busy');
    try {
      if (selected === 'token') {
        const result = await scimApi.token(workspaceId, values);
        if (!current()) return; const { token, ...next } = result; setStatus(next); setSecret(token); setSaved(false);
        setNotice(t("Provisioning token created. Save it in your identity provider before leaving. Previous tokens no longer work."));
      } else if (selected === 'disable') {
        const result = await scimApi.disable(workspaceId, values);
        if (!current()) return; setStatus(result); setNotice(t("Directory provisioning is paused. Existing memberships remain until changed."));
      } else {
        const result = await scimApi.saveMappings(workspaceId, { ...values, default_role: defaultRole, mappings: mappings.map(({ group_id, workspace_role, channels }) => ({ group_id, workspace_role, channels: channels.map(({ channel_id, role }) => ({ channel_id, role })) })) });
        if (!current()) return; setDirectory(result); setMappings(result.mappings); setDefaultRole(result.default_role); setNotice(t("Group access rules saved. Directory-managed access is reconciled by the provisioning service."));
      }
      if (!current()) return; setAction(null);
      const history = await scimApi.audit(workspaceId); if (current()) setAudit(history.events);
    } catch (failure) { if (current()) setError(enterpriseFailure(failure)); }
    finally { if (current()) setBusy(false); }
  }
  function updateMapping(index: number, value: Partial<SCIMMapping>) { setMappings(previous => previous.map((mapping, number) => number === index ? { ...mapping, ...value } : mapping)); }
  const availableGroups = directory?.groups.filter(group => !mappings.some(mapping => mapping.group_id === group.id)) || [];
  const dirty = !!directory && (defaultRole !== directory.default_role || JSON.stringify(mappings) !== JSON.stringify(directory.mappings));
  function chooseAction(next: Action | null) {
    // Publish intent in the click handler so another navigation event cannot
    // outrun the render/effect that mounts the native confirmation form.
    onDirtyChange?.(dirty || next !== null);
    setAction(next);
  }
  useEffect(() => { onDirtyChange?.(dirty || action !== null); }, [dirty, action, onDirtyChange]);
  useEffect(() => () => { onDirtyChange?.(false); }, [onDirtyChange]);
  const locked = busy || !!secret;
  return <div className="fc-enterprise-scim"><section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("People, kept in sync")}</span><h3>{t("Directory provisioning")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh directory provisioning")} disabled={loading || locked} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("Connect your own identity provider’s SCIM 2.0 service to create, update and deactivate organization accounts in this workspace. Groups can grant only the workspace and channel access you choose below.")}</p><EnterpriseAlerts error={error} notice={notice} />
      {loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading directory settings…")}</p> : status && <>
        <div className="fc-sso-status"><Users size={19} /><strong>{status.credential?.requires_rotation ? t("Owner credential rotation needed") : status.enabled ? t("Provisioning enabled") : status.configured ? t("Provisioning paused") : t("Ready to configure")}</strong>{status.credential && <span className="fc-badge">{t("Credential ")}{status.credential.version}</span>}</div>
        <div className="fc-account-form"><label htmlFor="scim-endpoint-url">{t("SCIM endpoint URL")}</label><div className="fc-integration-secret-value"><input id="scim-endpoint-url" className="fc-input" value={status.endpoint_url} readOnly /><button type="button" className="fc-icon-button" aria-label={t("Copy SCIM endpoint")} onClick={() => { void navigator.clipboard.writeText(status.endpoint_url).then(() => { if (current()) setNotice(t("SCIM endpoint copied.")); }).catch(() => { if (current()) setError(t("Select and copy the SCIM endpoint manually.")); }); }}><Copy size={16} /></button></div><p className="fc-account-muted">{t("Use this address and a provisioning token in your identity provider. Configure the workspace’s SSO onboarding policy to accept provisioned accounts and match its signed identity claim to SCIM externalId.")}</p></div>
        <dl className="fc-integration-status-grid"><div><dt>{t("Managed users")}</dt><dd>{status.counts.users}</dd></div><div><dt>{t("Active users")}</dt><dd>{status.counts.active_users}</dd></div><div><dt>{t("Directory groups")}</dt><dd>{status.counts.groups}</dd></div><div><dt>{t("Last provisioning request")}</dt><dd>{enterpriseDate(status.credential?.last_used_at)}</dd></div></dl>
        <div className="fc-integration-action-grid">{isOwner && <button type="button" className="fc-button fc-button-primary" disabled={locked || !security} onClick={() => { chooseAction('token'); setError(''); }}><KeyRound size={15} />{status.configured ? t("Rotate provisioning token") : t("Create provisioning token")}</button>}<button type="button" className="fc-button" disabled={locked || !status.enabled || !security} onClick={() => { chooseAction('disable'); setError(''); }}>{t("Pause provisioning")}</button></div>{!isOwner && <p className="fc-account-muted">{t("A workspace owner creates and rotates provisioning credentials.")}</p>}{status.credential?.requires_rotation && <p className="fc-alert" role="status">{t("The credential’s issuing owner no longer has access. A current workspace owner needs to rotate the token before provisioning can resume.")}</p>}
      </>} 
    </section>
    {secret && <OneTimeSecret id="scim-provisioning-token" title={t("Save your provisioning token")} label={t("Provisioning bearer token")} value={secret} saved={saved} onStored={setSaved} onHide={() => { setSecret(''); setSaved(false); setNotice(t("Provisioning token stored. You can rotate it if a replacement is needed.")); }} onNotice={value => { if (current()) setNotice(value); }} />}
    {directory && <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Explicit group access")}</span><h3>{t("Group and role mapping")}</h3></div><Users size={19} /></div><p className="fc-account-muted">{t("A directory group can grant access only inside this workspace. It can never create an owner or installation administrator. Removing a managed grant stops future access; retained messages and files remain.")}</p><div className="fc-account-form"><label htmlFor="scim-default-role">{t("Default role for provisioned people")}</label><select id="scim-default-role" className="fc-input" value={defaultRole} onChange={event => setDefaultRole(event.target.value as 'member' | 'guest')} disabled={locked}><option value="member">{t("Member")}</option><option value="guest">{t("Guest")}</option></select></div>
      {(directory.mappings.some(mapping => mapping.channels.some(channel => channel.requires_reapproval)) || directory.channels.some(channel => channel.requires_reapproval)) && <p className="fc-alert" role="status">{t("Some channel grants need renewed approval from a current workspace and channel administrator. Review and save these rules with the required permissions before new access is granted.")}</p>}<div className="fc-enterprise-mappings">{mappings.map((mapping, index) => <fieldset key={mapping.group_id} className="fc-integration-checks"><legend>{directory.groups.find(group => group.id === mapping.group_id)?.display_name || mapping.group_name || t("Directory group")}</legend><div className="fc-account-form"><label htmlFor={`scim-group-role-${index}`}>{t("Workspace role granted by this group")}</label><select id={`scim-group-role-${index}`} className="fc-input" value={mapping.workspace_role || ''} onChange={event => updateMapping(index, { workspace_role: (event.target.value || null) as SCIMMapping['workspace_role'] })} disabled={locked || !isOwner && directory.mappings.some(value => value.group_id === mapping.group_id && value.workspace_role === 'admin')}><option value="">{t("Use default membership")}</option><option value="guest">{t("Guest")}</option><option value="member">{t("Member")}</option><option value="admin" disabled={!isOwner}>{t("Workspace administrator")}{!isOwner ? t(" · owner approval required") : ''}</option></select></div><p className="fc-account-muted">{t("Channel grants require your current channel administration permission.")}</p>{directory.channels.map(channel => { const granted = mapping.channels.find(value => value.channel_id === channel.id); return <div key={channel.id} className="fc-enterprise-channel-grant"><label className="fc-account-checkbox"><input type="checkbox" checked={!!granted} disabled={locked} onChange={event => updateMapping(index, { channels: event.target.checked ? [...mapping.channels, { channel_id: channel.id, role: 'member' }] : mapping.channels.filter(value => value.channel_id !== channel.id) })} />{displayChannels.find(value => value.id === channel.id)?.name || channel.name || t("Channel")}{channel.secure ? t(" · encrypted") : ''}</label>{granted && <select className="fc-input" aria-label={'Role in ' + (displayChannels.find(value => value.id === channel.id)?.name || channel.name || 'channel')} value={granted.role} disabled={locked} onChange={event => updateMapping(index, { channels: mapping.channels.map(value => value.channel_id === channel.id ? { ...value, role: event.target.value as 'member' | 'admin' } : value) })}><option value="member">{t("Channel member")}</option><option value="admin">{t("Channel administrator")}</option></select>}</div>; })}<button type="button" className="fc-account-link fc-account-danger" disabled={locked || !isOwner && directory.mappings.some(value => value.group_id === mapping.group_id && value.workspace_role === 'admin')} onClick={() => setMappings(previous => previous.filter((_, value) => value !== index))}><Trash2 size={14} />{t("Remove group rule")}</button></fieldset>)}</div>
      {!directory.groups.length && <p className="fc-account-muted">{t("No groups have been provisioned yet. Send your directory groups through SCIM, then refresh this panel.")}</p>}
      <div className="fc-account-form"><label htmlFor="scim-add-group">{t("Directory group to map")}</label><select id="scim-add-group" className="fc-input" value={groupToAdd || availableGroups[0]?.id || ''} onChange={event => setGroupToAdd(event.target.value)} disabled={locked || !availableGroups.length}>{availableGroups.length ? availableGroups.map(group => <option key={group.id} value={group.id}>{group.display_name}</option>) : <option value="">{t("No unmapped groups")}</option>}</select></div><div className="fc-account-actions"><button type="button" className="fc-button" disabled={locked || !availableGroups.length} onClick={() => { const group = availableGroups.find(value => value.id === groupToAdd) || availableGroups[0]; if (group) setMappings(previous => [...previous, { group_id: group.id, workspace_role: null, channels: [] }]); setGroupToAdd(''); }}><Plus size={15} />{t("Add group rule")}</button><button type="button" className="fc-button fc-button-primary" disabled={locked || !security || !dirty} onClick={() => { chooseAction('mappings'); setError(''); }}>{t("Save group access rules")}</button></div>
    </section>}
    {action && <NativeConfirmation key={action} id="scim-confirm" title={action === 'token' ? status?.configured ? t("Replace the provisioning token?") : t("Create provisioning token?") : action === 'disable' ? t("Pause directory provisioning?") : t("Confirm group access rules")} mfa={!!security?.mfa_enabled} busy={busy} onCancel={() => chooseAction(null)} onConfirm={confirm}>{action === 'token' && <p>{t("The old token stops working immediately. Store the replacement in your identity provider; it is shown only once.")}</p>}{action === 'disable' && <p>{t("This stops incoming provisioning requests. Existing memberships are not erased by pausing the connection.")}</p>}{action === 'mappings' && <p>{t("These rules may change access for current directory group members.")}</p>}</NativeConfirmation>}
    <section className="fc-account-section"><h3>{t("Directory activity")}</h3><p className="fc-account-muted">{t("Provisioning actions are recorded without access tokens or personal profile contents.")}</p>{audit.length ? <ul className="fc-enterprise-records">{audit.map(event => <li key={event.id}><div><strong>{describeAction(event.action)}</strong><span>{event.resource_type} · {enterpriseDate(event.created_at)}</span></div></li>)}</ul> : <p className="fc-account-muted">{t("No provisioning activity yet.")}</p>}</section>
  </div>;
}
