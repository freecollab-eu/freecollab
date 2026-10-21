import type {CollaborationClient} from './client.js';
import type {ModuleOptions, WorkflowDefinition, WorkflowRun} from './types.js';
import {button, element, mountView} from './view.js';
type Item = {kind: 'definition'; value: WorkflowDefinition} | {kind: 'run'; value: WorkflowRun};
export function mountWorkflows(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  return mountView<Item>(target, client, 'workflows', options, async signal => {
    const [definitions, runs] = await Promise.all([client.listWorkflowDefinitions(signal), client.listWorkflowRuns(signal)]);
    return [...definitions.definitions.map(value => ({kind: 'definition' as const, value})), ...runs.map(value => ({kind: 'run' as const, value}))];
  }, (item, context) => {
    const row = element('li');
    if (item.kind === 'definition') {const definition = item.value; row.append(element('h3', definition.fields.name)); if (definition.enabled && definition.fields.trigger === 'manual') row.append(button(context.labels.startWorkflow, () => void context.run(async signal => {await client.startWorkflow(definition.id, signal);}))); row.append(button(context.labels.openItem, () => client.openResource('workflow', definition.id)));}
    else {const run = item.value; row.append(element('h3', run.workflow_name), element('p', run.status + ' · ' + (run.step_index + 1) + '/' + run.step_count)); if (run.may_vote) for (const [choice, label] of [['approve', context.labels.approve], ['reject', context.labels.reject]] as const) row.append(button(label, () => void context.run(async signal => {await client.voteWorkflow(run.id, choice, '', signal);})));} return row;
  });
}
