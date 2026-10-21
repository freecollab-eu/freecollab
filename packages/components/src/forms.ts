import {element} from './view.js';
export function field(form: HTMLElement, label: string, multiline = false, maximum = 200) {
  const wrapper = element('label', label), input = multiline ? element('textarea') : element('input');
  input.setAttribute('aria-label', label); input.maxLength = maximum; wrapper.append(input); form.append(wrapper); return input;
}
export function choice<T extends string>(form: HTMLElement, label: string, values: Array<[T, string]>) {
  const wrapper = element('label', label), input = element('select'); input.setAttribute('aria-label', label);
  for (const [value, title] of values) { const option = element('option', title); option.value = value; input.append(option); }
  wrapper.append(input); form.append(wrapper); return input;
}
export function submit(form: HTMLFormElement, label: string, run: () => void) {
  const button = element('button', label); button.type = 'submit'; button.className = 'fc-primary'; form.append(button);
  form.addEventListener('submit', event => {event.preventDefault(); run();}); return button;
}
