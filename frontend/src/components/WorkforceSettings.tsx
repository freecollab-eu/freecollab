import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Building2, Copy, Link2, Loader2, RefreshCw, UserPlus, Users } from 'lucide-react';
import { api } from '../lib/api';
import { workforceApi, type IdentityClaim, type IdentityMode, type IdentityPolicy, type NewWorkforceInvitation, type WorkforceInvitation, type WorkforcePerson } from '../lib/enterprise-api';
import type { AccountSecurity } from '../lib/account-api';
import type { SSOReauthentication } from '../lib/integrations-api';
import { EnterpriseAlerts, NativeConfirmation, enterpriseDate, enterpriseFailure, type IntegrationGuard } from './EnterpriseControls';

type Action = { kind: 'policy' } | { kind: 'invite' } | { kind: 'revoke'; invitation: WorkforceInvitation };
const modes: { id: IdentityMode; title: string; description: string }[] = [
  { id: 'linked', title: 'Personal accounts', description: 'Members create and verify a personal account, then explicitly link organization sign-in.' },
  { id: 'provisioned', title: 'Provisioned organization accounts', description: 'Your identity provider provisions people through SCIM. They sign in directly with your organization.' },
  { id: 'invited', title: 'Invited organization accounts', description: 'Administrators share one-use invitations. Invitees create their organization account through SSO.' },
  { id: 'jit', title: 'Create on first organization sign-in', description: 'Any identity accepted by this provider can become a workspace member. Restrict access at your provider first.' },
];

export function WorkforceSettings({ workspaceId, security, onGuardChange }: { workspaceId: string; security: AccountSecurity | null; onGuardChange: (guard: IntegrationGuard) => void }) {
  useI18n();
  const [policy, setPolicy] = useState<IdentityPolicy | null>(null);
  const [mode, setMode] = useState<IdentityMode>('linked');
  const [claim, setClaim] = useState<IdentityClaim>('sub');
  const [people, setPeople] = useState<WorkforcePerson[]>([]);
  const [invitations, setInvitations] = useState<WorkforceInvitation[]>([]);
  const [created, setCreated] = useState<NewWorkforceInvitation | null>(null);
  const [role, setRole] = useState<'member' | 'guest'>('member');
  const [acceptedJIT, setAcceptedJIT] = useState(false);
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
  async function load() {
    const request = ++generation.current; setLoading(true); setError('');
    try {
      const [settings, records, invites] = await Promise.all([workforceApi.policy(workspaceId), workforceApi.people(workspaceId), workforceApi.invitations(workspaceId)]);
      if (!current() || request !== generation.current) return;
      setPolicy(settings); setMode(settings.mode); setClaim(settings.identity_claim); setAcceptedJIT(false);
      setPeople(records); setInvitations(invites.invitations);
    } catch (failure) { if (current() && request === generation.current) setError(enterpriseFailure(failure)); }
    finally { if (current() && request === generation.current) setLoading(false); }
  }
  async function confirm(values: SSOReauthentication) {
    if (!action || busy) return;
    const selected = action; setBusy(true); setError(''); setNotice(''); onGuardChange('busy');
    try {
      if (selected.kind === 'policy') {
        await workforceApi.updatePolicy(workspaceId, { ...values, mode, identity_claim: claim, expected_revision: policy?.revision });
        if (!current()) return; await load();
        if (current()) setNotice(t("Organization onboarding policy saved. Existing personal accounts remain separate."));
      } else if (selected.kind === 'invite') {
        const result = await workforceApi.invite(workspaceId, { ...values, role, expires_in_hours: 24 });
        if (!current()) return; setCreated(result); await load();
        if (current()) setNotice(t("One-use organization invitation created. Copy the link for your colleague."));
      } else {
        await workforceApi.revokeInvitation(workspaceId, selected.invitation.id, values);
        if (!current()) return; await load();
        if (current()) setNotice(t("The unused organization invitation is revoked."));
      }
      if (current()) setAction(null);
    } catch (failure) { if (current()) setError(enterpriseFailure(failure)); }
    finally { if (current()) { setBusy(false); onGuardChange(null); } }
  }
  const dirty = !!policy && (mode !== policy.mode || claim !== policy.identity_claim);
  return <div className="fc-enterprise-workforce"><section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("A simpler welcome")}</span><h3>{t("Organization onboarding")}</h3></div><button type="button" className="fc-icon-button" aria-label={t("Refresh organization onboarding")} disabled={loading || busy} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("Choose how people join through this workspace’s own identity provider. Organization accounts belong only to this workspace; matching an email never takes over a personal account.")}</p><EnterpriseAlerts error={error} notice={notice} />
    {loading ? <p className="fc-account-loading" role="status"><Loader2 size={17} className="fc-account-spinner" />{t("Loading onboarding…")}</p> : policy && <>
      <fieldset className="fc-enterprise-options"><legend>{t("New people joining through SSO")}</legend>{modes.map(option => <label key={option.id}><input type="radio" name="workforce-onboarding-mode" value={option.id} checked={mode === option.id} onChange={() => { setMode(option.id); setAcceptedJIT(false); }} disabled={busy} /><span><strong>{option.title}</strong><small>{option.description}</small></span></label>)}</fieldset>
      <div className="fc-account-form"><label htmlFor="workforce-identity-claim">{t("Provisioned identity identifier")}</label><select id="workforce-identity-claim" className="fc-input" value={claim} onChange={event => setClaim(event.target.value as IdentityClaim)} disabled={busy}><option value="sub">{t("Provider subject (sub)")}</option><option value="oid">{t("Directory object ID (oid)")}</option><option value="email">{t("Organization email")}</option><option value="preferred_username">{t("Organization username")}</option></select><p className="fc-account-muted">{t("Match your SCIM external identifier to this signed provider claim. Email or username matching stays within this workspace and never links a personal FreeCollab account.")}</p></div>
      {mode === 'jit' && <label className="fc-account-checkbox fc-enterprise-consent"><input type="checkbox" checked={acceptedJIT} onChange={event => setAcceptedJIT(event.target.checked)} disabled={busy} />{t("I have restricted who can access this application at our identity provider. Accepted identities may become workspace members.")}</label>}
      <button type="button" className="fc-button fc-button-primary" disabled={busy || !security || !dirty || mode === 'jit' && !acceptedJIT} onClick={() => { setAction({ kind: 'policy' }); setError(''); }}>{t("Save onboarding policy")}</button>
      <div className="fc-integration-privacy"><Building2 size={18} /><p>{t("Organization accounts sign in through your provider without a separate FreeCollab password. Trusted encryption devices and the separate history recovery key still protect encrypted content.")}</p></div>
    </>}
    </section>
    {policy && <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("A deliberate invitation")}</span><h3>{t("Organization invitations")}</h3></div><UserPlus size={20} /></div><p className="fc-account-muted">{t("One-use links expire after 24 hours and create a workspace account only after successful organization sign-in. Channel access is granted separately.")}</p>
      {policy.mode !== 'invited' ? <p className="fc-account-muted">{t("Save the “Invited organization accounts” policy to create invitations here. Personal-account invitations remain available in Workspace settings.")}</p> : <div className="fc-enterprise-inline"><label htmlFor="workforce-invitation-role">{t("New person’s role")}</label><select id="workforce-invitation-role" className="fc-input" value={role} onChange={event => setRole(event.target.value as 'member' | 'guest')} disabled={busy}><option value="member">{t("Member")}</option><option value="guest">{t("Guest")}</option></select><button type="button" className="fc-button" disabled={busy || !security} onClick={() => { setAction({ kind: 'invite' }); setError(''); }}><UserPlus size={15} />{t("Create organization invitation")}</button></div>}
      {created && <div className="fc-integration-secret"><span className="fc-kicker">{t("Share privately")}</span><h3>{t("Your one-use invitation")}</h3><label htmlFor="workforce-invitation-link">{t("Organization invitation link")}</label><div className="fc-integration-secret-value"><input id="workforce-invitation-link" className="fc-input" type="password" value={created.login_url} readOnly autoComplete="off" onFocus={event => event.currentTarget.select()} /><button type="button" className="fc-icon-button" aria-label={t("Copy organization invitation")} onClick={() => { void navigator.clipboard.writeText(created.login_url).then(() => { if (current()) setNotice(t("Organization invitation copied. Share it with your intended colleague.")); }).catch(() => { if (current()) setError(t("Select and copy the invitation link manually.")); }); }}><Copy size={16} /></button></div><p className="fc-account-muted">{t("Expires ")}{enterpriseDate(created.expires_at)}{t(". This link is shown once; revoke it below if you shared it with the wrong person.")}</p><button type="button" className="fc-account-link" onClick={() => setCreated(null)}>{t("Hide invitation link")}</button></div>}
      <ul className="fc-enterprise-records">{invitations.map(invitation => <li key={invitation.id}><Link2 size={17} /><div><strong>{invitation.role === 'guest' ? t("Guest invitation") : t("Member invitation")}</strong><span>{t("Expires ")}{enterpriseDate(invitation.expires_at)}</span></div><span className="fc-badge">{invitation.revoked_at ? t("Revoked") : invitation.used_at ? t("Used") : new Date(invitation.expires_at).getTime() < Date.now() ? t("Expired") : t("Available")}</span>{!invitation.revoked_at && !invitation.used_at && new Date(invitation.expires_at).getTime() > Date.now() && <button type="button" className="fc-account-link fc-account-danger" disabled={busy} onClick={() => setAction({ kind: 'revoke', invitation })}>{t("Revoke")}</button>}</li>)}</ul>
    </section>}
    {action && <NativeConfirmation key={action.kind + ('invitation' in action ? action.invitation.id : '')} id="workforce-confirm" title={action.kind === 'policy' ? t("Confirm onboarding policy") : action.kind === 'invite' ? t("Create organization invitation?") : t("Revoke this invitation?")} mfa={!!security?.mfa_enabled} busy={busy} onCancel={() => setAction(null)} onConfirm={confirm}>{action.kind === 'policy' && <p>{modes.find(value => value.id === mode)?.title}{t(". Existing personal accounts are not converted or merged.")}</p>}</NativeConfirmation>}
    <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{t("Managed organization accounts")}</h3><Users size={19} /></div><p className="fc-account-muted">{t("Your directory or onboarding policy manages these accounts for this workspace.")}</p>{!people.length ? <p className="fc-account-muted">{t("No managed organization accounts yet.")}</p> : <ul className="fc-enterprise-records">{people.map(person => <li key={person.user_id}><Users size={17} /><div><strong>{person.name}</strong><span>{person.email}</span><small>{person.bound ? t("Organization identity connected") : t("Ready for first organization sign-in")}</small></div><span className="fc-badge">{person.active ? t("Active") : t("Inactive")}</span></li>)}</ul>}</section>
  </div>;
}
