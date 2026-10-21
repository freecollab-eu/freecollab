import test from 'node:test';
import assert from 'node:assert/strict';
import { installationOrigin } from '../scripts/configure.mjs';
test('native mobile configuration accepts only explicit HTTPS installation origins', () => {
  assert.equal(installationOrigin('https://collab.example.com/'), 'https://collab.example.com');
  assert.equal(installationOrigin('https://collab.example.com:8443'), 'https://collab.example.com:8443');
  for (const value of ['http://collab.example.com', 'file:///tmp/app', 'https://user:secret@collab.example.com', 'https://collab.example.com/path', 'https://collab.example.com/?token=x', 'https://collab.example.com/#token=x', ' https://collab.example.com']) assert.throws(() => installationOrigin(value));
});
