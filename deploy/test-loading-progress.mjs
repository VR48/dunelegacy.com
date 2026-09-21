import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { createHash, webcrypto } from 'node:crypto';
const script = readFileSync(new URL('./loading-progress.js', import.meta.url), 'utf8');

test('uses decoded package size and changes to engine preparation at completion', () => {
  const statuses = [];
  let indeterminate = false;
  const Module = { setStatus: text => statuses.push(text) };
  runInNewContext(script, { Module, document: { getElementById: () => ({
    removeAttribute: name => { assert.equal(name, 'value'); indeterminate = true; },
  }) } });
  assert.equal(Module.getPreloadedPackage('game.data', 100), undefined);
  Module.setStatus('Downloading data... (60/60)');
  assert.equal(statuses.at(-1), 'Downloading data... (60/100)');
  assert.equal(indeterminate, false);
  Module.setStatus('Downloading data... (100/60)');
  assert.match(statuses.at(-1), /Preparing the game engine/);
  assert.equal(indeterminate, true);
  Module.setStatus('');
  assert.equal(statuses.at(-1), '');
});

test('preserves an existing preload callback and unrelated status messages', () => {
  const buffer = new ArrayBuffer(8);
  const Module = { getPreloadedPackage: () => buffer, setStatus: text => text };
  runInNewContext(script, { Module, document: {} });
  assert.equal(Module.getPreloadedPackage('cached.data', 8), buffer);
  assert.equal(Module.setStatus('Loading graphics'), 'Loading graphics');
});

const wasm = Uint8Array.from([0, 97, 115, 109, 1, 0, 0, 0]);
const engineHash = createHash('sha256').update(wasm).digest('hex');
const primaryURL = 'https://raw.githubusercontent.com/example/engine.wasm';
const fallbackURL = 'https://game.test/play/dunecity.wasm?v=release';
const response = bytes => ({ ok: true, arrayBuffer: async () => bytes.slice().buffer });

function runEngine(fetcher) {
  const calls = [];
  const done = new Promise(resolve => {
    const Module = { locateFile: () => fallbackURL, setStatus: () => {}, onAbort: error => resolve({ error }) };
    const context = {
      Module, URL, AbortController, Uint8Array, WebAssembly, crypto: webcrypto,
      setTimeout: callback => setTimeout(callback, 20), clearTimeout,
      fetch: (url, options) => { calls.push(url); return fetcher(url, options); },
      document: { currentScript: { src: 'https://game.test/play/loading-progress.js',
        dataset: { engineUrl: primaryURL, engineSha256: engineHash } } },
    };
    runInNewContext(script, context);
    Module.instantiateWasm({}, (instance, module) => resolve({ instance, module }));
  });
  return { done, calls };
}

test('instantiates the verified public copy without requesting the origin', async () => {
  const { done, calls } = runEngine(async () => response(wasm));
  assert.ok((await done).instance instanceof WebAssembly.Instance);
  assert.deepEqual(calls, [primaryURL]);
});

test('rejects a corrupt public copy and verifies the origin fallback', async () => {
  const { done, calls } = runEngine(async url => response(url === primaryURL ? new Uint8Array(4) : wasm));
  assert.ok((await done).instance instanceof WebAssembly.Instance);
  assert.deepEqual(calls, [primaryURL, fallbackURL]);
});

test('falls back when the public copy times out', async () => {
  const { done, calls } = runEngine((url, options) => url === primaryURL
    ? new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('timeout'))))
    : Promise.resolve(response(wasm)));
  assert.ok((await done).instance instanceof WebAssembly.Instance);
  assert.deepEqual(calls, [primaryURL, fallbackURL]);
});

test('displays an error if neither host returns the expected engine', async () => {
  const { done } = runEngine(async () => ({ ok: false, status: 503 }));
  assert.match((await done).error, /Could not prepare the game engine/);
});
