import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source = readFileSync(new URL('../src/i18n/core.ts', import.meta.url), 'utf8');
function setup() {
  const exports = {}, document = { documentElement: {} };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, document, Intl, Date });
  return { ...exports, document };
}
test('browser language negotiation uses registered catalogs with an English fallback', () => {
  const { resolveLanguage } = setup();
  assert.equal(resolveLanguage('auto', ['sv-SE', 'en']), 'sv');
  assert.equal(resolveLanguage('auto', ['de-DE', 'sv-FI']), 'sv');
  assert.equal(resolveLanguage('auto', ['de-DE']), 'en');
  assert.equal(resolveLanguage('en', ['sv-SE']), 'en');
});
test('catalog interpolation keeps selected content literal and unknown application keys readable', () => {
  const { t, registerCatalog, applyLocale } = setup();
  registerCatalog('sv', { 'Hello {name}': 'Hej {name}', 'Save': 'Spara' }); applyLocale('sv');
  assert.equal(t('Hello {name}', { name: '<script>{other}</script>' }), 'Hej <script>{other}</script>');
  assert.equal(t('Hello {name}'), 'Hej {name}');
  assert.equal(t('Not translated'), 'Not translated');
  assert.equal(t('toString'), 'toString');
});
test('language changes update semantic document language and notify subscribers without remounting', () => {
  const { applyLocale, subscribeLocale, document, currentLocale, formatNumber, formatDate } = setup();
  let calls = 0; const unsubscribe = subscribeLocale(() => calls++);
  applyLocale('sv'); applyLocale('sv'); assert.equal(calls, 1); assert.equal(document.documentElement.lang, 'sv');
  assert.equal(currentLocale(), 'sv'); assert.match(formatNumber(1234.5), /1\s234,5/);
  assert.equal(formatDate('invalid'), 'Not available');
  unsubscribe(); applyLocale('en'); assert.equal(calls, 1);
});

test('bundled Swedish catalogs cover literal UI keys and retain interpolation values', () => {
  const catalog = {}, base = new URL('../src/', import.meta.url);
  for (const name of readdirSync(new URL('i18n/', base)).filter(name => name === 'sv.ts' || name.endsWith('.sv.ts'))) {
    const exports = {};
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL('i18n/' + name, base), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports });
    Object.assign(catalog, exports.default);
  }
  const placeholders = text => [...new Set(text.match(/\{[A-Za-z][A-Za-z0-9_]*\}/g) || [])].sort();
  for (const [key, value] of Object.entries(catalog)) assert.deepEqual(placeholders(value), placeholders(key), key);
  function check(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const file = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory);
      if (entry.isDirectory()) { if (entry.name !== 'i18n') check(file); continue; }
      if (!/\.tsx?$/.test(entry.name)) continue;
      const tree = ts.createSourceFile(entry.name, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, entry.name.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
      function visit(node) {
        const argument = node.arguments?.[0];
        if (ts.isCallExpression(node) && node.expression.getText(tree) === 't' && argument && (ts.isStringLiteral(argument) || ts.isNoSubstitutionTemplateLiteral(argument))) assert.ok(Object.hasOwn(catalog, argument.text), 'Missing Swedish UI key: ' + argument.text);
        ts.forEachChild(node, visit);
      }
      visit(tree);
    }
  }
  check(base);
});
