import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  const state = { outputs: [], failures: [], workers: [], processors: [], contexts: [] };
  class AudioContext {
    sampleRate = 16000; state = 'running'; destination = {};
    constructor() { state.contexts.push(this); }
    async resume() {}
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
    createScriptProcessor() { const node = { onaudioprocess: null, connect() {}, disconnect() {} }; state.processors.push(node); return node; }
  }
  class Worker {
    messages = []; terminated = false; onmessage = null; onerror = null;
    constructor() { state.workers.push(this); }
    postMessage(message) { this.messages.push(message); }
    terminate() { this.terminated = true; }
  }
  const source = fs.readFileSync(new URL('../src/lib/meeting-captions.ts', import.meta.url), 'utf8')
    .replace("new URL('./meeting-captions.worker.ts', import.meta.url)", "'owned-worker-fixture'");
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText,
    { exports, AudioContext, Worker, Float32Array, DOMException, setTimeout, clearTimeout });
  const engine = new exports.LocalMeetingCaptions({ captions: true, model: 'onnx-community/whisper-tiny', model_base: '/owned-models/' }, 'en',
    caption => state.outputs.push(caption), message => state.failures.push(message));
  const worker = () => state.workers.at(-1);
  const latest = () => worker().messages.filter(message => message.operation === 'transcribe').at(-1);
  return { state, engine, worker, latest,
    async start() {
      const ready = engine.start({}); const load = worker().messages[0];
      worker().onmessage({ data: { id: load.id, ready: true } }); await ready;
    },
    feed(seconds, value = .25) { const samples = new Float32Array(Math.round(seconds * 16000)); samples.fill(value); state.processors.at(-1).onaudioprocess?.({ inputBuffer: { getChannelData: () => samples } }); },
    answer(text = 'Known consented speech') { const request = latest(); assert.ok(request); worker().onmessage?.({ data: { id: request.id, text } }); },
  };
}

test('manual pause preserves partial and in-flight consented speech with an active-audio clock', async () => {
  const f = fixture(); await f.start();
  f.feed(5); f.feed(1.75); f.engine.pause(true); f.feed(2);
  assert.equal(f.engine.activeSeconds, 6.75);
  f.answer('Before the pause');
  const pausedDrain = f.engine.drain(); f.answer('The final word before pause'); await pausedDrain;
  f.engine.pause(false); f.feed(2.25); f.engine.pause(true);
  const finalDrain = f.engine.drain(); f.answer('After the pause'); await finalDrain;
  assert.deepEqual(f.state.outputs.map(row => [row.start, row.end]), [[0, 5], [5, 6.75], [6.75, 9]]);
  assert.equal(f.engine.activeSeconds, 9); assert.deepEqual(f.state.failures, []); f.engine.stop();
});

test('segment drain flushes only its snapshot frontier while later audio remains available', async () => {
  const f = fixture(); await f.start(); f.feed(3.5);
  const first = f.engine.drain(1000, 1.25); assert.equal(f.latest().samples.length, 20000);
  f.feed(1); f.answer('First segment'); await first;
  assert.equal(f.state.outputs[0].end, 1.25);
  f.engine.pause(true); const rest = f.engine.drain(1000, 999);
  assert.equal(f.latest().samples.length, 52000); f.answer('Second segment'); await rest;
  assert.deepEqual(f.state.outputs.map(row => [row.start, row.end]), [[0, 1.25], [1.25, 4.5]]);
  assert.equal(f.engine.activeSeconds, 4.5); f.engine.stop();
});

test('short final speech is padded for recognition without inventing a longer caption duration', async () => {
  const f = fixture(); await f.start(); f.feed(.2); f.engine.pause(true);
  const done = f.engine.drain(); const samples = f.latest().samples;
  assert.equal(samples.length, 8000); assert.equal(samples[0], .25); assert.equal(samples[3200], 0);
  f.answer('Last word'); await done;
  assert.equal(f.state.outputs[0].start, 0); assert.equal(f.state.outputs[0].end, .2); f.engine.stop();
});

test('a completion timeout is bounded and does not invoke the destructive recording failure callback', async () => {
  const f = fixture(); await f.start(); f.feed(2); f.engine.pause(true);
  await assert.rejects(f.engine.drain(15), { name: 'TimeoutError' });
  assert.deepEqual(f.state.failures, []); assert.equal(f.worker().terminated, false);
  const retry = f.engine.drain(1000); f.answer('A later acknowledged result'); await retry;
  assert.equal(f.state.outputs[0].end, 2); f.engine.stop();
});

test('revocation immediately aborts all drains and permanently suppresses already queued worker callbacks', async () => {
  const f = fixture(); await f.start(); f.feed(3);
  const first = f.engine.drain(); const second = f.engine.drain();
  const assertions = [assert.rejects(first, { name: 'AbortError' }), assert.rejects(second, { name: 'AbortError' })];
  const deliver = f.worker().onmessage, id = f.latest().id;
  f.engine.stop(); deliver({ data: { id, text: 'Must never be published after revocation' } });
  await Promise.all(assertions);
  assert.equal(f.worker().terminated, true); assert.equal(f.state.contexts[0].state, 'closed');
  assert.deepEqual(f.state.outputs, []); assert.equal(f.engine.buffer.length, 0);
  await assert.rejects(f.engine.drain(), { name: 'AbortError' });
});

test('concurrent completion requests settle only after their own audio frontier', async () => {
  const f = fixture(); await f.start(); f.feed(3); f.engine.pause(true);
  const first = f.engine.drain(1000, 1), second = f.engine.drain(1000, 2.5); let secondDone = false;
  second.then(() => { secondDone = true; });
  f.answer('First frontier'); await first; assert.equal(secondDone, false);
  assert.equal(f.latest().samples.length, 24000); f.answer('Second frontier'); await second;
  assert.deepEqual(f.state.outputs.map(row => row.end), [1, 2.5]); f.engine.stop();
});

test('stopping during model preparation rejects start and releases the actual worker owner', async () => {
  const f = fixture(); const start = f.engine.start({}); const assertion = assert.rejects(start, { name: 'AbortError' });
  f.engine.stop(); await assertion; assert.equal(f.worker().terminated, true); assert.equal(f.state.processors.length, 0);
});

test('invalid completion deadlines and frontiers fail before queuing recognition', async () => {
  const f = fixture(); await f.start(); f.feed(1);
  for (const [timeout, frontier] of [[0], [15001], [Infinity], [1000, -1], [1000, NaN]]) await assert.rejects(f.engine.drain(timeout, frontier));
  assert.equal(f.latest(), undefined); f.engine.stop();
});
