import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const fn = source.slice(source.indexOf('async function prepareDesktopClose()'), source.indexOf('async function savePageOnce()'));
let saves = 0;
const state = {dirty: true, slug: 'test', status: {}};
const context = vm.createContext({state, saveNow: async () => { saves++; state.dirty = false; }});
vm.runInContext(fn, context);
assert.equal(await context.prepareDesktopClose(), true);
assert.equal(saves, 1);
assert.equal(await context.prepareDesktopClose(), true);
assert.equal(saves, 1);
state.dirty = true;
context.saveNow = async () => { saves++; }; // Failed save must keep the app open.
assert.equal(await context.prepareDesktopClose(), false);
assert.equal(state.dirty, true);
state.status.film = {busy: true, slug: 'test'};
assert.equal(await context.prepareDesktopClose(), true);
assert.equal(saves, 2); // Never overwrite the server-owned active film.
state.status = {};
let release;
state.saveFlight = new Promise(resolve => { release = () => { state.dirty = false; resolve(); }; });
let finished = false;
const closing = context.prepareDesktopClose().then(result => { finished = result; });
await Promise.resolve();
assert.equal(finished, false);
release();
await closing;
assert.equal(finished, true);
console.log('Desktop close: flush, in-flight save, failed save and active film checks passed');
