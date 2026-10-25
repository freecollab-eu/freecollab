import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

const root = fileURLToPath(new URL('../src/', import.meta.url));
function files(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? files(file) : file.endsWith('.tsx') ? [file] : [];
  });
}

test('product writing helpers are never nested inside a field label', () => {
  const violations = [];
  for (const file of files(root)) {
    const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function visit(node, label = null) {
      const element = ts.isJsxElement(node) ? node.openingElement : ts.isJsxSelfClosingElement(node) ? node : null;
      const tag = element?.tagName.getText(source);
      if (label && /^(?:Scoped)?CollaborationInput$|^WritingCopilot$/.test(tag || ''))
        violations.push(`${path.relative(root, file)}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`);
      const parentLabel = tag === 'label' ? node : label;
      ts.forEachChild(node, child => visit(child, parentLabel));
    }
    visit(source);
  }
  assert.deepEqual(violations, [], 'Use a sibling label for an input that contains other interactive writing controls.');
});

test('a translated field label targets only its input and preserves an existing ID', () => {
  const source = ts.transpileModule(fs.readFileSync(path.join(root, 'components/LabeledWritingField.tsx'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(source, { exports, require: name => name === 'react' ? React : name === 'react/jsx-runtime' ? jsxRuntime : {} });
  function Field({ id }) { return React.createElement('div', null, React.createElement('input', { id }), React.createElement('button', null, 'AI help')); }
  const render = (label, id) => renderToStaticMarkup(React.createElement(exports.LabeledWritingField, { label }, React.createElement(Field, { id })));
  for (const label of ['Group name', 'Gruppnamn']) {
    const html = render(label, 'existing-writing-field');
    assert.match(html, new RegExp(`<label for="existing-writing-field">${label}</label><div>`));
    assert.match(html, /<input id="existing-writing-field"\/>/);
    assert.ok(!/<label[^>]*>[^]*<button[^]*<\/label>/.test(html));
  }
  const generated = render('Group name');
  const association = /<label for="([^"]+)">/.exec(generated)?.[1];
  assert.ok(association);
  assert.ok(generated.includes(`<input id="${association}"`));
});
