import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/appearance.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function fixture(darkSystem = false) {
  const exports = {};
  const variables = new Map();
  const root = { dataset: {}, style: { setProperty: (key, value) => variables.set(key, value) } };
  const body = { dataset: {} };
  vm.runInNewContext(compiled, {
    exports, document: { documentElement: root, body },
    window: { matchMedia: () => ({ matches: darkSystem }) },
  });
  return { ...exports, root, body, variables };
}

const plain = value => JSON.parse(JSON.stringify(value));

test('stored settings accept a finite vocabulary and reject arbitrary CSS, fonts and future versions', () => {
  const { DEFAULT_APPEARANCE: defaults, normalizeAppearance } = fixture();
  for (const value of [undefined, null, [], 'dark', 1, { version: 2, mode: 'dark' }, { version: '1', mode: 'dark' }]) {
    assert.deepEqual(plain(normalizeAppearance(value)), plain(defaults));
  }
  const result = normalizeAppearance({
    mode: 'url(https://example.invalid)', accent_color: '#fff; background:url(https://example.invalid)',
    background_color: 'red', dark_background_color: '#12345', font_family: 'Comic Sans',
    font_size: '100px', density: '0', corner_style: 'circle', arbitrary_css: 'body { display:none }',
  });
  assert.deepEqual(plain(result), plain(defaults));
  assert.equal('arbitrary_css' in result, false);
});

test('valid settings round-trip and color values have one canonical representation', () => {
  const { DEFAULT_APPEARANCE: defaults, normalizeAppearance } = fixture();
  const chosen = {
    ...defaults, mode: 'system', accent_color: '#AABBCC', background_color: '#F0EEDD',
    dark_background_color: '#102030', font_family: 'serif', font_size: 'large',
    density: 'compact', corner_style: 'square', ignored: true,
  };
  assert.deepEqual(plain(normalizeAppearance(chosen)), {
    version: 1, mode: 'system', accent_color: '#aabbcc', background_color: '#f0eedd',
    dark_background_color: '#102030', font_family: 'serif', font_size: 'large',
    density: 'compact', corner_style: 'square',
  });
});

test('contrast uses the WCAG relative luminance formula', () => {
  const { contrastRatio } = fixture();
  assert.equal(contrastRatio('#000000', '#ffffff'), 21);
  assert.equal(contrastRatio('#ffffff', '#000000'), 21);
  assert.equal(contrastRatio('#777777', '#777777'), 1);
  assert.ok(Math.abs(contrastRatio('#777777', '#ffffff') - 4.478089) < 0.00001);
});

test('custom colors retain readable text across canvases, surfaces, sidebars and accent buttons', () => {
  const { DEFAULT_APPEARANCE: defaults, PRESETS, buildAppearanceTokens, contrastRatio } = fixture();
  const canvases = ['#000000', '#ffffff', '#747474', '#757575', '#777777', '#808080', '#123456', '#ff0000', '#00ff00', '#0000ff'];
  const accents = ['#000000', '#ffffff', '#777777', '#ff0000', '#00ff00', '#0000ff', '#36594c'];
  const palettes = PRESETS.map(preset => ({ ...defaults, ...preset }));
  for (const mode of ['light', 'dark']) {
    for (const canvas of canvases) {
      for (const accent_color of accents) {
        palettes.push({ ...defaults, mode, background_color: canvas, dark_background_color: canvas, accent_color });
      }
    }
  }
  for (const preferences of palettes) {
    const tokens = buildAppearanceTokens(preferences);
    const readablePairs = [
      ['--fc-text', '--fc-bg'], ['--fc-text', '--fc-surface'], ['--fc-text', '--fc-sidebar'],
      ['--fc-text', '--fc-accent-soft'],
      ['--fc-muted', '--fc-bg'], ['--fc-muted', '--fc-surface'], ['--fc-muted', '--fc-sidebar'],
      ['--fc-muted', '--fc-accent-soft'],
      ['--fc-accent', '--fc-bg'], ['--fc-accent', '--fc-surface'], ['--fc-accent', '--fc-accent-soft'],
      ['--fc-on-accent', '--fc-accent'],
    ];
    for (const [foreground, background] of readablePairs) {
      const ratio = contrastRatio(tokens[foreground], tokens[background]);
      assert.ok(ratio >= 4.5, `${foreground} ${tokens[foreground]} on ${background} ${tokens[background]}: ${ratio.toFixed(3)}; ${JSON.stringify(preferences)}`);
    }
  }
});

test('system mode and all personal controls reach the DOM without untrusted style interpolation', () => {
  const { DEFAULT_APPEARANCE: defaults, applyAppearance, root, body, variables } = fixture(true);
  applyAppearance({ ...defaults, mode: 'system', font_family: 'manrope', font_size: 'large', density: 'compact', corner_style: 'square' });
  assert.equal(root.dataset.mode, 'dark');
  assert.equal(body.dataset.mode, 'dark');
  assert.equal(root.style.colorScheme, 'dark');
  assert.equal(root.dataset.font, 'manrope');
  assert.equal(body.dataset.fontSize, 'large');
  assert.equal(body.dataset.density, 'compact');
  assert.equal(body.dataset.corners, 'square');
  assert.match(variables.get('--fc-font'), /Manrope/);
  assert.equal(variables.get('--fc-bg'), defaults.dark_background_color);
  assert.ok(parseFloat(variables.get('--fc-space')) < 1);
  applyAppearance({ ...defaults, font_family: 'url(https://example.invalid)' });
  assert.match(variables.get('--fc-font'), /Inter/);
});
