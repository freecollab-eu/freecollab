import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { installedAppCommandsApi, type InstalledAppCommand } from '../lib/installed-app-commands';
import { useScopedWritingGuard } from '../lib/useScopedWritingGuard';
import { useI18n } from '../i18n';
import { AppCommandReview } from './AppCommandReview';

/** Same real foreground catalogue used by /, retained inside the channel. */
export function SecureChannelAppCommands({ workspaceId, channelId }: { workspaceId: string; channelId: string }) {
  const { t } = useI18n();
  const [commands, setCommands] = useState<InstalledAppCommand[]>([]), [selected, setSelected] = useState('');
  const [pending, setPending] = useState(false), [error, setError] = useState('');
  const lease = useRef({ token: api.getAccessToken(), actor: api.getCurrentUserId(), active: true });
  const mayLeave = useScopedWritingGuard(channelId, pending, false, { workspaceId });
  useEffect(() => {
    const scope = { token: api.getAccessToken(), actor: api.getCurrentUserId(), active: true }; lease.current = scope;
    const current = () => scope.active && scope.token === api.getAccessToken() && scope.actor === api.getCurrentUserId();
    setCommands([]); setSelected(''); setPending(false); setError('');
    void installedAppCommandsApi.list(workspaceId, channelId).then(result => { if (current()) setCommands(result.commands.filter(row => row.delivery === 'foreground' && row.supports_secure_foreground)); }).catch(failure => { if (current()) setError(failure instanceof Error ? failure.message : t('The installed command is unavailable.')); });
    const invalidate = () => { scope.active = false; setCommands([]); setSelected(''); setPending(false); };
    const storage = (event: StorageEvent) => { if (event.key === null || event.key === 'freecollab_token' || event.key === 'freecollab_user_id') invalidate(); };
    window.addEventListener('freecollab:auth-changed', invalidate); window.addEventListener('storage', storage);
    return () => { scope.active = false; window.removeEventListener('freecollab:auth-changed', invalidate); window.removeEventListener('storage', storage); };
  }, [workspaceId, channelId]);
  const command = commands.find(row => row.key === selected);
  return <section className="fc-app-platform"><h3>{t('Reviewed apps in this encrypted channel')}</h3><p>{t('Choose an installed command here or use / in any conversation editor. Each action shows its exact recipient before anything leaves encryption.')}</p>
    {error && <p className="fc-alert" role="alert">{error}</p>}
    {!commands.length && !error && <p>{t('No foreground app is installed for this channel. A workspace administrator can explicitly grant one in Integrations → Apps.')}</p>}
    {!!commands.length && <label>{t('Installed command')}<select value={selected} onChange={event => { if (mayLeave()) { setSelected(event.target.value); setPending(false); } }}><option value="">{t('Choose a command')}</option>{commands.map(row => <option value={row.key} key={row.key}>{row.app_name} · /{row.name}</option>)}</select></label>}
    {command && <AppCommandReview key={command.key} workspaceId={workspaceId} channelId={channelId} command={command} arguments={{}} onPendingChange={setPending} onClose={() => { if (mayLeave()) { setSelected(''); setPending(false); } }} />}
  </section>;
}
