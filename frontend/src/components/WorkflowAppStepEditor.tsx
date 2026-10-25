import { useEffect, useId, useState } from 'react';
import { api } from '../lib/api';
import { installedAppCommandsApi, type InstalledAppCommand } from '../lib/installed-app-commands';
import type { WorkflowStep } from '../lib/workflows-api';
import { useI18n } from '../i18n';
import { ScopedCollaborationInput as CollaborationInput } from './ScopedCollaborationInput';

export function WorkflowAppStepEditor({ workspaceId, channelId, value, onChange, disabled }: {
  workspaceId: string; channelId: string; value: WorkflowStep; onChange: (patch: Partial<WorkflowStep>) => void; disabled: boolean;
}) {
  const { t } = useI18n(), writingId = useId(); const [commands, setCommands] = useState<InstalledAppCommand[]>([]), [error, setError] = useState('');
  useEffect(() => {
    let live = true; const token = api.getAccessToken(), actor = api.getCurrentUserId();
    void installedAppCommandsApi.list(workspaceId, channelId).then(result => { if (live && token === api.getAccessToken() && actor === api.getCurrentUserId()) setCommands(result.commands); })
      .catch(failure => { if (live) setError(failure instanceof Error ? failure.message : t('Installed commands could not be loaded.')); });
    return () => { live = false; };
  }, [workspaceId, channelId]);
  const selected = commands.find(command => command.installation_id === value.installation_id && command.name === value.command);
  return <fieldset className="fc-account-form" disabled={disabled}><legend>{t('Installed app action')}</legend>
    {error && <p role="alert">{error}</p>}
    <label>{t('Installed command')}<select required value={selected?.key || ''} onChange={event => {
      const command = commands.find(item => item.key === event.target.value);
      onChange({ installation_id: command?.installation_id || null, command: command?.name || null,
        expected_generation: command?.generation || null, arguments: {}, disclose_to_app: false });
    }}><option value="">{t('Choose an installed command')}</option>{commands.map(command => <option key={command.key} value={command.key}>{command.app_name} · /{command.name}</option>)}</select></label>
    {selected && <><p>{selected.description} · <code>{selected.origin}</code> · {t('Version')} {selected.generation}</p>
      {selected.generation !== value.expected_generation && <p role="alert">{t('The installed app changed. Select its current command and review again.')}</p>}
      {selected.arguments.map(argument => <label key={argument.name}>{argument.label}
        {argument.type === 'choice' || argument.type === 'boolean' ? <select required={argument.required} value={value.arguments?.[argument.name] || ''} onChange={event => onChange({ arguments: { ...value.arguments, [argument.name]: event.target.value }, disclose_to_app: false })}>
          <option value="">{t('Choose a value')}</option>{(argument.type === 'boolean' ? ['true', 'false'] : argument.choices || []).map(option => <option value={option} key={option}>{option}</option>)}
        </select> : <CollaborationInput as="textarea" toolbar allowReferences={false} channelId={channelId} contextId={`${writingId}:${selected.key}:${argument.name}`} rows={2} required={argument.required} maxLength={argument.max_length} value={value.arguments?.[argument.name] || ''} onValueChange={text => onChange({ arguments: { ...value.arguments, [argument.name]: text }, disclose_to_app: false })} />}
      </label>)}
      <p>{t('Every matching run sends exactly these static arguments through the current installed app grant. No message or document content is read. Revoking or changing the app stops pending actions; uncertain external writes require inspection.')}</p>
      <label><input type="checkbox" required checked={!!value.disclose_to_app} onChange={event => onChange({ disclose_to_app: event.target.checked })} />{t('Authorize these exact arguments and recipient for future matching workflow runs.')}</label>
    </>}
  </fieldset>;
}
