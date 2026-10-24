import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function compile(name, dependencies, globals = {}) {
  const exports = {}, source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports, require: name => dependencies[name], ...globals });
  return exports;
}

test('real scoped writing guards protect only the closing card, block busy saves, and release revoked authority', () => {
  let token = 'session', actor = 'actor', confirm = false, confirms = 0;
  const api = { getAccessToken: () => token, getCurrentUserId: () => actor };
  const navigation = compile('conversation-navigation', { './api': { api } }); navigation.setConversationNavigationWorkspace('workspace');
  function fixture(surfaceId) {
    const slots = [], effects = []; let cursor = 0;
    const hooks = { useRef(initial) { return slots[cursor++] ||= { current: initial }; }, useEffect(callback, deps) { const index = cursor++, old = slots[index]; if (!old || deps.some((value, i) => value !== old.deps[i])) { slots[index] = { deps, cleanup: old?.cleanup }; effects.push(() => { slots[index].cleanup?.(); slots[index].cleanup = callback(); }); } } }; hooks.useLayoutEffect = hooks.useEffect;
    const helpers = compile('useScopedWritingGuard', { react: hooks, './api': { api }, './conversation-navigation': navigation, './collaboration-context': { useCollaborationContext: () => ({ channelId: 'channel', workspaceId: 'workspace', surfaceId }) }, '../i18n': { t: value => value } }, { window: { confirm() { confirms++; return confirm; } } });
    const leaveController = { current: null };
    return { render(dirty, busy) { cursor = 0; const leave = helpers.useScopedWritingGuard('channel', dirty, busy, { leaveController }); effects.splice(0).forEach(effect => effect()); return leave; }, leaveController, unmount() { slots.forEach(slot => slot.cleanup?.()); } };
  }
  const a = fixture('card-a'), b = fixture('card-b');
  a.render(false, false); b.render(false, false); assert.equal(a.leaveController.current(), true); assert.equal(confirms, 0);
  a.render(true, false); b.render(true, true);
  assert.equal(navigation.mayLeaveConversationWork('card-a'), false); assert.equal(confirms, 1);
  confirm = true; assert.equal(navigation.mayLeaveConversationWork('card-a'), true); assert.equal(navigation.mayLeaveConversationWork('card-b'), false); assert.equal(navigation.mayLeaveConversationWork(), false);
  b.render(false, false); assert.equal(navigation.mayLeaveConversationWork('card-b'), true);
  token = 'rotated'; actor = 'different'; assert.equal(a.leaveController.current(), true); assert.equal(navigation.mayLeaveConversationWork(), true);
  a.unmount(); b.unmount(); assert.equal(a.leaveController.current, null); assert.equal(b.leaveController.current, null);
});
