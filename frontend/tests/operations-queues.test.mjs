import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/OperationsPanel.tsx', import.meta.url), 'utf8');
const groups = {
  webhooks: { delivered: 2 }, otrust: { proof_available: 1 }, sync: { connected: 1 },
  'calendar-delivery': { uncertain: 1 }, workflows: { waiting_approval: 1 }, data: { ready: 1 },
  push: { failed: 1 }, native_push: { processing: 1 }, apps: { uncertain: 2, delivered: 1 },
  chat: { notification_pending: 3, notified: 1, completed: 1 }, recaps: { scheduled: 2, disabled: 1 },
  'native-call-cleanup': { scheduled: 1 }, 'slack-import-cleanup': { inactive: 2 },
  'personal-oauth-cleanup': { pending: 1 },
};

function render(locale) {
  const core = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/i18n/core.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports: core, document: { documentElement: {} }, Intl, Date });
  for (const name of readdirSync(new URL('../src/i18n/', import.meta.url)).filter(name => name === 'sv.ts' || name.endsWith('.sv.ts'))) {
    const exports = {};
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/i18n/' + name, import.meta.url), 'utf8'),
      { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports });
    core.registerCatalog('sv', exports.default);
  }
  core.applyLocale(locale);
  const status = { mail: { mode: 'disabled', configured: false, tls: 'not_applicable', issues: [] },
    queue: {}, worker: { status: 'unknown', last_seen_at: null }, database: { reachable: true },
    integration_worker: { enabled: true, status: 'stale' },
    integration_queues: Object.fromEntries(Object.entries(groups).map(([name, counts]) => [name, { ...counts, oldest_pending_seconds: 317 }])) };
  const states = [status, [], false, '', '', '', null, '', ''];
  const nodes = (type, props) => ({ type, props });
  const exports = {};
  const dependencies = {
    '../i18n': { ...core, useI18n() {} },
    react: { useState: () => [states.shift(), () => {}], useRef: current => ({ current }), useEffect() {} },
    'react/jsx-runtime': { jsx: nodes, jsxs: nodes, Fragment: 'fragment' },
    'lucide-react': {}, '../lib/api': { api: { getAccessToken: () => 'fixture', getCurrentUserId: () => 'operator' } },
    '../lib/integrations-api': { operationsApi: {} }, './OperationsSecurity': {}, './DeploymentReadinessPanel': {},
  };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText,
  { exports, require: name => { assert.ok(name in dependencies, name); return dependencies[name]; } });
  const rows = {};
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object'
    ? text(node.props?.children) : node == null ? '' : String(node);
  function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit);
    if (!node || typeof node !== 'object') return;
    if (node.props?.['data-worker-queue']) rows[node.props['data-worker-queue']] = text(node);
    visit(node.props?.children);
  }
  visit(exports.OperationsPanel({ security: null }));
  return rows;
}

test('operations renders every worker group and preserves uncertain, future and notification work', () => {
  const rows = render('en');
  assert.deepEqual(Object.keys(rows).sort(), Object.keys(groups).sort());
  assert.match(rows['calendar-delivery'], /Calendar delivery1 Uncertain/);
  assert.match(rows.apps, /2 Uncertain · 1 Delivered/);
  assert.match(rows.chat, /3 Notifications waiting · 1 Reminder created · 1 Completed/);
  assert.match(rows.recaps, /2 Scheduled · 1 Disabled/);
  for (const value of Object.values(rows)) assert.doesNotMatch(value, /317|undefined|NaN/);
});

test('operations group labels use the bundled Swedish feature catalogs', () => {
  const rows = render('sv');
  assert.match(rows['calendar-delivery'], /Kalenderleverans1 Osäkert resultat/);
  assert.match(rows.native_push, /Push till mobilappen1 Bearbetas/);
  assert.match(rows.chat, /3 Väntande notifieringar · 1 Påminnelse skapad/);
  assert.match(rows['personal-oauth-cleanup'], /Kontroll av leverantörssamtycken1 Väntande/);
  assert.match(rows.recaps, /Schemalagda privata sammanfattningar2 Schemalagt/);
});
