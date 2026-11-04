// Real editor controls with DOM/API stubs. No server, model or generation.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element {
  children = []; listeners = {}; style = {}; dataset = {};
  classList = {add() {}, remove() {}};
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
}
const saves = [], warnings = [], choices = [];
const context = vm.createContext({
  document: {createElement: () => new Element(), addEventListener() {}, querySelector: () => new Element()},
  setTimeout: () => 1, clearTimeout() {}, structuredClone, assert,
  API: {saveBoard: async (_, board) => { saves.push(structuredClone(board));
          return {board: {...structuredClone(board), anchorsRevision: 'server-revision-' + saves.length}}; },
        uploadRef: async () => ({path: 'test/refs/drop.png', label: 'Dropped'})},
});
vm.runInContext(fs.readFileSync(new URL('../js/app.js', import.meta.url), 'utf8'), context);
context.recordWarning = message => warnings.push(message);
context.recordChoice = options => choices.push(options);
await vm.runInContext(`(async () => {
  render = () => {}; renderEditor = () => {}; paintStale = () => {};
  toast = recordWarning;
  chooseImage = async (_, opts = {}) => { recordChoice(opts); return opts.multi
    ? [{path: 'test/refs/new.png', label: 'New image'}]
    : {path: 'test/refs/replaced.png', label: 'Replaced'}; };
  state.slug = 'test'; state.status = {};
  state.board = {shots: [{id: 's0', directorScene: 1, frames: 73},
    {id: 's1', directorScene: 1, frames: 96, title: 'Continuation', imageAnchors: [
      {ref: {path: 'test/refs/one.png', label: 'One'}, frame: 0, strength: .35},
      {ref: {path: 'test/refs/two.png', label: 'Two'}, frame: 'last', strength: .7}]}]};
  const all = (node) => [node, ...node.children.flatMap(all)];
  const mount = () => shotImageAnchors(shotById('s1'));
  let panel = mount();
  assert(all(panel).some(n => String(n.textContent || '').includes('første NYE bilde')));
  const field = (label) => all(panel).find(n => n['aria-label'] === label);
  // Old DOM handlers must update the current board after a server refresh.
  state.board = structuredClone(state.board);
  let input = field('Tid (sekunder) for bildeanker 1'); input.value = '1.5';
  await input.listeners.change();
  assert.equal(shotById('s1').imageAnchors[0].frame, 36);
  panel = mount(); input = field('Styrke for bildeanker 1'); input.value = '.6';
  await input.listeners.change();
  assert.equal(shotById('s1').imageAnchors[0].strength, .6);
  panel = mount(); input = field('Bildeindeks for bildeanker 1'); input.value = '96';
  await input.listeners.change();
  assert.equal(shotById('s1').imageAnchors[0].frame, 36);
  panel = mount(); input = field('Styrke for bildeanker 1'); input.value = '0';
  await input.listeners.change();
  assert.equal(shotById('s1').imageAnchors[0].strength, .6);
  assert.match(imageAnchorProblem([
    {ref: {path:'one'}, frame:'last', strength:.4},
    {ref: {path:'two'}, frame:95, strength:.6}], 96), /samme/);
  panel = mount();
  await all(panel).find(n => n.textContent === 'Legg til bildeanker').listeners.click();
  assert.equal(shotById('s1').imageAnchors.length, 3);
  panel = mount();
  let slot = all(panel).find(n => n.className === 'ref-slot filled');
  await slot.listeners.click();
  assert.equal(shotById('s1').imageAnchors[0].ref.label, 'Replaced');
  await slot.ondrop({preventDefault(){}, stopPropagation(){}, dataTransfer:{files:[{type:'image/png'}]}});
  assert.equal(shotById('s1').imageAnchors[0].ref.label, 'Dropped');
  await all(slot).find(n => n.className === 'pick-delete').listeners.click({stopPropagation(){}});
  assert.equal(shotById('s1').imageAnchors.length, 2);
  const before = JSON.stringify(shotById('s1').imageAnchors);
  shotById('s1').locked = true;
  await commitImageAnchors('s1', []);
  assert.equal(JSON.stringify(shotById('s1').imageAnchors), before);
  shotById('s1').locked = false; state.status = {film:{busy:true,slug:'test'}};
  await commitImageAnchors('s1', []);
  assert.equal(JSON.stringify(shotById('s1').imageAnchors), before);
  state.status = {};
  await commitImageAnchors('s1', []);
  assert.equal(shotById('s1').imageAnchors.length, 0);
  assert.match(state.board.anchorsRevision, /^server-revision-/);
  assert.equal(anchorImageUrl({path:'test/refs/safe.png',url:'https://unwanted.example/leak'}), '/media/test/refs/safe.png');
  assert.equal(anchorImageUrl({url:'https://unwanted.example/leak'}), '');
  for (const invalid of [[null], null, '', 0, false]) {
    shotById('s1').imageAnchors = invalid;
    panel = mount();
    assert(all(panel).some(n => String(n.textContent || '').includes('Ugyldige bildeankre')));
    await all(panel).find(n => n.textContent === 'Tøm bildeankre').listeners.click();
    assert.equal(shotById('s1').imageAnchors.length, 0);
  }
  state.board.defaults = {draft:true, sketch:true};
  await commitImageAnchors('s1', [{ref:{path:'test/refs/one.png'},frame:48,strength:.4}]);
  assert.equal(shotById('s1').imageAnchors.length, 0);
  panel = mount();
  assert(all(panel).some(n => String(n.textContent || '').includes('ikke i Sketch')));
  // Two immediate edits with a slow server must use successive tokens and
  // persist the second edit; the first reply cannot mark it saved early.
  state.board.defaults = {};
  shotById('s1').imageAnchors = [{ref:{path:'test/refs/one.png'},frame:0,strength:.35}];
  const pending = [], snapshots = [];
  API.saveBoard = async (_, board) => {
    snapshots.push(structuredClone(board));
    return new Promise((resolve, reject) => pending.push({resolve, reject}));
  };
  const editA = commitImageAnchors('s1', [{ref:{path:'test/refs/one.png'},frame:24,strength:.35}]);
  const editB = commitImageAnchors('s1', [{ref:{path:'test/refs/one.png'},frame:24,strength:.8}]);
  assert.equal(pending.length, 1);
  pending[0].resolve({board:{...snapshots[0],anchorsRevision:'token-A'}});
  for (let i=0; i<8 && pending.length<2; i++) await Promise.resolve();
  assert.equal(pending.length, 2);
  assert.equal(snapshots[1].anchorsRevision, 'token-A');
  assert.equal(snapshots[1].shots[1].imageAnchors[0].strength, .8);
  assert.equal(state.dirty, true);
  pending[1].resolve({board:{...snapshots[1],anchorsRevision:'token-B'}});
  await Promise.all([editA, editB]);
  assert.equal(state.board.anchorsRevision, 'token-B');
  assert.equal(shotById('s1').imageAnchors[0].strength, .8);
  assert.equal(state.dirty, false);
  const failed = commitImageAnchors('s1', [{ref:{path:'test/refs/one.png'},frame:24,strength:.9}]);
  pending[2].reject(new Error('stale external revision'));
  await failed;
  assert.equal(state.dirty, true);
  assert.equal(shotById('s1').imageAnchors[0].strength, .9);
})()`, context);
assert(saves.length >= 6);
assert(warnings.length >= 4);
assert(choices.every(options => !options.generate));
console.log('Image anchors: image controls, time/strength, malformed data, local URLs, delayed serialized saves and failure retention passed without generation.');
