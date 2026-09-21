import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
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
  runInNewContext(script, { Module });
  assert.equal(Module.getPreloadedPackage('cached.data', 8), buffer);
  assert.equal(Module.setStatus('Loading graphics'), 'Loading graphics');
});
