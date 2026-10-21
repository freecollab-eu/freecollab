import { t, useI18n } from '../i18n';
import { useCallback, useRef, useState } from 'react';
import { Building2, Users } from 'lucide-react';
import type { AccountSecurity } from '../lib/account-api';
import type { Channel } from '../lib/api';
import { WorkforceSettings } from './WorkforceSettings';
import { SCIMSettings } from './SCIMSettings';
import type { IntegrationGuard } from './EnterpriseControls';

export function PeopleSettings({ workspaceId, security, isOwner, channels, onGuardChange }: { workspaceId: string; security: AccountSecurity | null; isOwner: boolean; channels: Channel[]; onGuardChange: (guard: IntegrationGuard) => void }) {
  useI18n();
  const [section, setSection] = useState<'onboarding' | 'directory'>('onboarding');
  const [notice, setNotice] = useState('');
  const guard = useRef<IntegrationGuard>(null);
  const changed = useCallback((value: IntegrationGuard) => { guard.current = value; onGuardChange(value); }, [onGuardChange]);
  function choose(value: 'onboarding' | 'directory') {
    if (guard.current === 'busy') return;
    if (guard.current === 'secret') { setNotice(t("Store the displayed provisioning token and confirm it is saved before switching sections.")); return; }
    setNotice(''); setSection(value);
  }
  return <div className="fc-enterprise-people"><nav className="fc-enterprise-subnav" aria-label={t("People integration sections")}><button type="button" className="fc-button" aria-pressed={section === 'onboarding'} onClick={() => choose('onboarding')}><Building2 size={15} />{t("Onboarding")}</button><button type="button" className="fc-button" aria-pressed={section === 'directory'} onClick={() => choose('directory')}><Users size={15} />{t("Directory & groups")}</button></nav>{notice && <p className="fc-alert" role="status">{notice}</p>}{section === 'onboarding' ? <WorkforceSettings workspaceId={workspaceId} security={security} onGuardChange={changed} /> : <SCIMSettings workspaceId={workspaceId} security={security} isOwner={isOwner} displayChannels={channels} onGuardChange={changed} />}</div>;
}
