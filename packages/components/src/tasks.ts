import type {CollaborationClient} from './client.js';
import type {CollaborationProject, CollaborationTask, ModuleOptions, TaskFields} from './types.js';
import {button, element, mountView} from './view.js';
import {choice, field, submit} from './forms.js';
export function mountTasks(target: HTMLElement | ShadowRoot, client: CollaborationClient, options: ModuleOptions = {}) {
  let projects: CollaborationProject[] = [], projectSelect: HTMLSelectElement | undefined;
  return mountView<CollaborationProject | CollaborationTask>(target, client, 'tasks', options, async signal => {
    const loadedProjects = await client.listProjects(signal); if (signal.aborted) return []; projects = loadedProjects;
    if (projectSelect) { const selected = projectSelect.value; projectSelect.replaceChildren(...projects.map(project => {const option = element('option', project.fields.title); option.value = project.id; return option;})); projectSelect.value = projects.some(p => p.id === selected) ? selected : projects[0]?.id || ''; }
    const tasks = await Promise.all(projects.map(project => project.archived ? Promise.resolve([]) : client.listTasks(project.id, signal)));
    return projects.flatMap((project, index) => [project, ...(tasks[index] || [])]);
  }, (record, context) => {
    const row = element('li'); row.append(element('h3', record.fields.title), element('p', record.fields.description));
    if (record.kind === 'task') {
      const task = record as CollaborationTask, current = task.fields.status;
      row.append(element('span', context.labels[current]), button(current === 'done' ? context.labels.todo : context.labels.done, () => void context.run(async signal => {await client.saveTask(task.project_id!, {...task.fields, status: current === 'done' ? 'todo' : 'done'}, task, signal);})), button(context.labels.openItem, () => client.openResource('task', task.id)));
    } return row;
  }, context => {
    const projectForm = element('form'), projectTitle = field(projectForm, context.labels.newProject); projectTitle.required = true; projectTitle.maxLength = 160;
    submit(projectForm, context.labels.newProject, () => void context.run(async signal => {await client.saveProject({title: projectTitle.value.trim(), description: '', color: 'sage'}, undefined, signal); projectTitle.value = '';}));
    const taskForm = element('form'); projectSelect = choice(taskForm, context.labels.project, []); projectSelect.required = true;
    const title = field(taskForm, context.labels.newTask), description = field(taskForm, context.labels.description, true, 12000); title.required = true;
    submit(taskForm, context.labels.newTask, () => {const project = projectSelect!.value; const fields: TaskFields = {title: title.value.trim(), description: description.value, status: 'todo', priority: 'normal', assignee_user_id: null, due_date: null, checklist: [], file_ids: [], decision_ids: []}; void context.run(async signal => {await client.saveTask(project, fields, undefined, signal); title.value = ''; description.value = '';});});
    context.formArea.append(projectForm, taskForm);
  });
}
