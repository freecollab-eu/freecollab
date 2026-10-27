import test from 'node:test';
import assert from 'node:assert/strict';
import { taskDrafts } from './load-field-references.mjs';

const { taskDraftFromMessage, suggestTaskDueDate, suggestTaskAssignee, isTaskDraftDate } = taskDrafts;
const now = new Date('2026-10-27T12:00:00Z');
const options = { now, timeZone: 'Europe/Stockholm', locale: 'sv' };
const members = [{ user_id: 'nora-id', name: 'Nora' }, { user_id: 'sam-id', name: 'Sam' }];
const source = content => ({ id: 'message-id', channel_id: 'room-id', content });
const plain = value => JSON.parse(JSON.stringify(value));

test('a selected sentence creates only a reviewed draft with actual member/date suggestions', () => {
  const text = 'Nora granskar skissen imorgon.';
  const message = source('Bakgrund från mötet. ' + text + ' En annan sak kan vänta.');
  const draft = taskDraftFromMessage(message, { ...options, members, text });
  assert.deepEqual(plain(draft), { key: 'message-task:room-id:message-id', title: text, text, assigneeUserId: 'nora-id', dueDate: '2026-10-28' });
  assert.equal(message.content, 'Bakgrund från mötet. ' + text + ' En annan sak kan vänta.');
  assert.equal('source_message_id' in draft, false, 'The existing conversation-work attachment owns persisted source provenance');
});

test('English sentence and multiline body keep the original content without inventing details', () => {
  const text = 'Sam reviews the plan by Friday.\nKeep the original measurements.';
  const draft = taskDraftFromMessage(source(text), { ...options, locale: 'en', members });
  assert.equal(draft.title, 'Sam reviews the plan by Friday.');
  assert.equal(draft.text, text);
  assert.equal(draft.dueDate, '2026-10-30');
  assert.equal(draft.assigneeUserId, 'sam-id');
});

test('names must identify exactly one current member; explicit IDs remain identity-bound', () => {
  assert.equal(suggestTaskAssignee('Nora tar detta', members), 'nora-id');
  assert.equal(suggestTaskAssignee('Nora och Sam tar detta', members), undefined);
  assert.equal(suggestTaskAssignee('Nora tar detta', [...members, { user_id: 'another-nora', name: 'Nora' }]), undefined);
  assert.equal(suggestTaskAssignee('Nora tar detta', [{ user_id: 'other', name: 'Nora Svensson' }]), 'other');
  assert.equal(suggestTaskAssignee('Please ask @nora-id', members), 'nora-id');
  assert.equal(suggestTaskAssignee('Please ask @nora-id-extra', members), undefined);
  assert.equal(suggestTaskAssignee('Please ask @nora-id:other', members), undefined);
  assert.equal(suggestTaskAssignee('Please ask @nora-id.example', members), undefined);
  assert.equal(suggestTaskAssignee('Please ask @nora-id.', members), 'nora-id');
  assert.equal(suggestTaskAssignee('Please ask @former-id', members), undefined);
  assert.equal(suggestTaskAssignee('nora@example.com sent this', members), undefined);
});

test('selected typed people override labels but groups and former members cannot imply an owner', () => {
  const text = '@Nora reviews @Sam';
  assert.equal(suggestTaskAssignee(text, members, [{ kind: 'person', id: 'sam-id', start: 0, end: 5 }, { kind: 'group', id: 'team', start: 14, end: 18 }]), 'sam-id');
  assert.equal(suggestTaskAssignee('@Nora', members, [{ kind: 'person', id: 'former', start: 0, end: 5 }]), undefined);
  assert.equal(suggestTaskAssignee('@Nora', members, [{ kind: 'group', id: 'group-id', start: 0, end: 5 }]), undefined);
});

test('a unique full first name is suggested only in bounded leading action syntax', () => {
  const people = [{ user_id: 'maja', name: 'Maja Lindström' }, { user_id: 'li', name: 'Li Andersson' }];
  assert.equal(suggestTaskAssignee('Maja skickar ritningen på fredag', people), 'maja');
  assert.equal(suggestTaskAssignee('Maja ska granska ritningen imorgon', people), 'maja');
  assert.equal(suggestTaskAssignee('Maja will send the drawing tomorrow', people), 'maja');
  for (const text of ['Ask Maja about the drawing', 'Maja mentioned the drawing', 'Maj skickar ritningen', 'Maja-Lisa skickar ritningen']) assert.equal(suggestTaskAssignee(text, people), undefined);
  assert.equal(suggestTaskAssignee('Maja skickar ritningen', [...people, { user_id: 'maja2', name: 'Maja Karlsson' }]), undefined);
  assert.equal(suggestTaskAssignee('Maja skickar ritningen', [...people, { user_id: 'maja2', name: 'Maja' }]), undefined);
});

test('dates use the chosen language for slash dates and accept explicit bilingual month names', () => {
  for (const [text, locale, expected] of [
    ['Deadline 2026-11-03', 'sv', '2026-11-03'], ['Senast 3/11/2026', 'sv', '2026-11-03'],
    ['Due 11/3/2026', 'en', '2026-11-03'], ['Due 3/11/2026', 'en', '2026-03-11'],
    ['Senast 3 november 2026', 'sv', '2026-11-03'], ['Due November 3rd, 2026', 'en', '2026-11-03'],
    ['Due 3 November 2026', 'en', '2026-11-03'], ['Senast i dag', 'sv', '2026-10-27'],
    ['Due tomorrow', 'en', '2026-10-28'], ['Senast i morgon', 'sv', '2026-10-28'],
    ['Till fredag', 'sv', '2026-10-30'], ['Due Tuesday', 'en', '2026-10-27'],
    ['Nästa tisdag', 'sv', '2026-11-03'], ['Due January 2', 'en', '2027-01-02'],
  ]) assert.equal(suggestTaskDueDate(text, { ...options, locale }), expected, text);
});

test('relative dates follow local calendar days at midnight and daylight-saving transitions', () => {
  const midnight = new Date('2026-10-27T23:30:00Z');
  assert.equal(suggestTaskDueDate('tomorrow', { now: midnight, timeZone: 'Europe/Stockholm' }), '2026-10-29');
  assert.equal(suggestTaskDueDate('tomorrow', { now: midnight, timeZone: 'America/Los_Angeles' }), '2026-10-28');
  for (const timestamp of ['2026-10-25T00:30:00Z', '2026-10-25T01:30:00Z']) {
    assert.equal(suggestTaskDueDate('imorgon', { now: new Date(timestamp), timeZone: 'Europe/Stockholm' }), '2026-10-26');
  }
});

test('conflicting, impossible and unsupported dates never become a guessed deadline', () => {
  for (const text of ['Today or tomorrow', 'Måndag eller fredag', '2026-02-30', 'Due 2026-11-03 or 2026-11-04', 'Senast 31/11/2026', 'Sometime soon', 'version2026-11-03', 'ref11/03/2026']) {
    assert.equal(suggestTaskDueDate(text, options), undefined, text);
  }
  assert.equal(suggestTaskDueDate('today', { now, timeZone: 'Invalid/Zone' }), undefined);
  assert.equal(suggestTaskDueDate('today', { now: new Date('invalid') }), undefined);
  assert.equal(isTaskDraftDate('2024-02-29'), true);
  assert.equal(isTaskDraftDate('2026-02-29'), false);
  assert.equal(isTaskDraftDate('0000-01-01'), false);
});

test('selected excerpts preserve exact typed reference ranges in real draft fields', () => {
  const content = 'Background. Ask @Nora to review @Plan tomorrow.';
  const start = content.indexOf('Ask');
  const references = [{ kind: 'person', id: 'nora-id', start: content.indexOf('@Nora'), end: content.indexOf('@Nora') + 5 },
    { kind: 'document', id: 'plan-id', start: content.indexOf('@Plan'), end: content.indexOf('@Plan') + 5 }];
  const draft = taskDraftFromMessage({ ...source(content), references }, { ...options, members, text: content.slice(start) });
  assert.equal(draft.assigneeUserId, 'nora-id');
  assert.deepEqual(plain(draft.field_references.description), references.map(reference => ({ ...reference, start: reference.start - start, end: reference.end - start })));
  assert.deepEqual(plain(draft.field_references.title), plain(draft.field_references.description));
});

test('locked/deleted/mismatched selections and cut/stale references never replace source text', () => {
  for (const message of [{ ...source('Review this'), locked: true }, { ...source('Review this'), deleted: true }, source('')]) assert.equal(taskDraftFromMessage(message), undefined);
  assert.equal(taskDraftFromMessage(source('Review this'), { text: 'Other channel text' }), undefined);
  assert.equal(taskDraftFromMessage(source('x'.repeat(12001))), undefined);
  const message = { ...source('Ask @Nora'), references: [{ kind: 'person', id: 'nora-id', start: 4, end: 9 }] };
  assert.equal(taskDraftFromMessage(message, { text: 'Ask @No' }), undefined);
  assert.equal(taskDraftFromMessage({ ...message, references: [{ ...message.references[0], end: 99 }] }), undefined);
  assert.equal(message.content, 'Ask @Nora');
});

test('long titles are bounded excerpts while all source text and reference limits survive', () => {
  const text = 'A '.repeat(140) + 'Next details';
  const draft = taskDraftFromMessage(source(text), options);
  assert.ok(draft.title.length <= 200); assert.equal(draft.text, text);
  const content = Array.from({ length: 50 }, (_, index) => '@p' + index).join(' ');
  const references = [...content.matchAll(/@p\d+/g)].map(match => ({ kind: 'person', id: match[0].slice(1), start: match.index, end: match.index + match[0].length }));
  const many = taskDraftFromMessage({ ...source(content), references }, options);
  assert.equal(Object.values(many.field_references).flat().length, 50);
  const unicode = taskDraftFromMessage(source('x'.repeat(199) + '🧭 more'), options);
  assert.equal(unicode.title, 'x'.repeat(199));
});

test('resource reference labels do not invent a date or a similarly named assignee', () => {
  const message = { ...source('Review @Friday with the team'), references: [{ kind: 'document', id: 'document', start: 7, end: 14 }] };
  const draft = taskDraftFromMessage(message, { ...options, members: [{ user_id: 'friday', name: 'Friday' }] });
  assert.equal(draft.dueDate, undefined); assert.equal(draft.assigneeUserId, undefined);
});
