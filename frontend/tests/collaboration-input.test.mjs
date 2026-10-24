import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as React from 'react';
import * as JSX from 'react/jsx-runtime';

const source = readFileSync(new URL('../src/components/CollaborationInput.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports, require(name) {
  if (name === 'react') return React;
  if (name === 'react/jsx-runtime') return JSX;
  return {};
} });
const range = (text, caret = text.length) => {
  const result = exports.getCollaborationCommandRange(text, caret);
  return result && JSON.parse(JSON.stringify(result));
};

test('commands belong only to the caret line and do not include other document paragraphs', () => {
  const text = 'A paragraph before.\n/task "Review" Work context.\nA paragraph after.';
  const start = text.indexOf('/task'), end = text.indexOf('\n', start);
  assert.deepEqual(range(text, end), { start, end, text: text.slice(start, end) });
  assert.equal(range(text, text.length), null);
  assert.equal(range(text, start), null);
});

test('plain writing, URLs, escaped slash and fractions never open a feature list', () => {
  for (const text of ['Visit https://example.test/docs', 'https://example.test/', '//literal text', 'a /task', '1/2', '\\/task']) assert.equal(range(text), null, text);
});

test('a fresh slash and indented command preserve exact source offsets', () => {
  assert.deepEqual(range('/'), { start: 0, end: 1, text: '/' });
  assert.deepEqual(range('one\n  /docs'), { start: 6, end: 11, text: '/docs' });
  assert.deepEqual(range('one\n\t/task thing'), { start: 5, end: 16, text: '/task thing' });
});

test('fenced code stays literal, including marker-shaped text inside a sample', () => {
  assert.equal(range('```text\n/docs'), null);
  assert.equal(range('```text\n```not-a-closing-fence\n/task'), null);
  assert.equal(range('~~~~\n~~~\n/docs'), null);
  const text = '```text\n/docs\n```\n/task';
  assert.deepEqual(range(text), { start: text.lastIndexOf('/task'), end: text.length, text: '/task' });
});

test('Unicode and CRLF keep native UTF-16 ranges without truncating source text', () => {
  const text = '👩🏽‍💻 Context\r\n/docs\r\nOther';
  const start = text.indexOf('/docs'), end = text.indexOf('\n', start);
  assert.deepEqual(range(text, end - 1), { start, end, text: '/docs\r' });
});

test('invalid caret positions and a cursor before the slash do not invent a command', () => {
  for (const caret of [-1, 7, 1.5, NaN, Infinity]) assert.equal(range('/docs', caret), null);
  assert.equal(range('  /docs', 1), null);
});
