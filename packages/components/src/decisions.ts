import type {CollaborationClient} from './client.js';
import type {DecisionFields, ModuleOptions} from './types.js';
import {button, element, mountView} from './view.js';
import {choice, field, submit} from './forms.js';
export function mountDecisions(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  return mountView(target, client, 'decisions', options, signal => client.listDecisions(signal), (decision, context) => {
    const row = element('li'); row.append(element('h3', decision.title), element('p', decision.content), element('span', context.labels[decision.status]));
    if (decision.created_by === client.state.userId && decision.status !== 'accepted') row.append(button(context.labels.accepted, () => void context.run(async signal => {const {title, content, responsible_user_id, decision_date, source_message_id} = decision; await client.saveDecision({title, content, responsible_user_id, decision_date, source_message_id, status: 'accepted'}, decision, signal);}))); return row;
  }, context => {
    const form = element('form'), title = field(form, context.labels.title), content = field(form, context.labels.description, true, 20000), status = choice(form, context.labels.status, [['proposed', context.labels.proposed], ['accepted', context.labels.accepted], ['superseded', context.labels.superseded]]); title.required = content.required = true;
    submit(form, context.labels.create, () => {const fields: DecisionFields = {title: title.value.trim(), content: content.value.trim(), status: status.value as DecisionFields['status'], responsible_user_id: null, decision_date: null, source_message_id: null}; void context.run(async signal => {await client.saveDecision(fields, undefined, signal); title.value = content.value = '';});}); context.formArea.append(form);
  });
}
