import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function load(file) {
  const exports = {};
  const source = readFileSync(new URL('../src/lib/' + file + '.ts', import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, URL, URLSearchParams });
  return exports;
}
const { productDemoUrl } = load('product-demo-url');
const { readDemoDestination, demoDestinationHash } = load('demo-showroom');

test('every marketing destination round-trips to a real product route', () => {
  for (const section of ['chat', 'projects', 'documents', 'connections', 'appearance']) {
    const parsed = readDemoDestination(demoDestinationHash({ section }));
    assert.equal(parsed.section, section);
    const target = new URL(productDemoUrl('https://demo.example/product-demo/', 'https://www.example', parsed, 'sv'));
    assert.equal(target.origin, 'https://demo.example');
    assert.equal(target.pathname, '/product-demo/');
    assert.equal(target.searchParams.get('section'), section);
    assert.equal(target.searchParams.get('locale'), 'sv');
  }
});

test('demo URLs reject active schemes, insecure remote hosts and embedded credentials', () => {
  for (const base of ['javascript:alert(1)', 'data:text/html,hello', 'http://demo.example/', 'https://key:secret@demo.example/']) {
    assert.throws(() => productDemoUrl(base, 'https://www.example'));
  }
  assert.throws(() => productDemoUrl(undefined, 'https://www.example'));
  assert.equal(new URL(productDemoUrl('/product-demo/', 'http://127.0.0.1:3010')).origin, 'http://127.0.0.1:3010');
});

test('only the two named connection recipes and languages reach the sample installation', () => {
  for (const provider of ['google', 'microsoft']) assert.equal(new URL(productDemoUrl('https://demo.example/product-demo/', 'https://site.example', { section: 'connections', provider })).searchParams.get('provider'), provider);
  const target = new URL(productDemoUrl('https://demo.example/product-demo/?token=do-not-forward#private', 'https://site.example', { section: 'unknown', provider: 'https://evil.invalid' }, 'unknown'));
  assert.equal(target.search, '?section=chat'); assert.equal(target.hash, '');
  assert.equal(readDemoDestination('#docs'), null);
  assert.equal(readDemoDestination('#demo=connections&provider=evil').provider, undefined);
});
