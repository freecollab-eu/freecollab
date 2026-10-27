import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function load(name, dependencies = {}) {
  const source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, require: name => dependencies[name], Set, Map });
  return exports;
}
export const messageReferences = load('message-references');
export const fieldReferences = load('field-references', { './message-references': messageReferences });
export const taskDrafts = load('task-draft', { './message-references': messageReferences });

export function resourceReferenceTargets(api, resolve = async (_, refs) => refs.map(ref => ({ ...ref, available: true }))) {
  return load('resource-reference-targets', { './api': { api }, './field-references': fieldReferences, './message-references-api': { messageReferencesApi: { resolve } } });
}
