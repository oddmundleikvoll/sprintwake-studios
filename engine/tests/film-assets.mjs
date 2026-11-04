// Execute the real Film Bible controls with local DOM/API fixtures; no service.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(tag, cls, ...children) {
    Object.assign(this, {tag, className:cls, children, dataset:{}, style:{}, listeners:{}});
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  contains() { return false; }
}
const source = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const parent = new Element('div'), description = new Element('textarea');
description.parentElement = parent;
const nodes = node => typeof node === 'object' && node !== null ? [node,...(node.children || []).flatMap(nodes)] : [];
const initial = {filmBible:{locations:[{id:'garden',title:'Garden',description:'Bench right'}],props:[]}};
const saves = [], picks = [];
const context = vm.createContext({
  state:{slug:'film',board:structuredClone(initial)}, document:{activeElement:null},
  $:selector => selector === '#sceneDescription' ? description : nodes(parent).find(node => node.id === selector.slice(1)),
  el:(...args) => new Element(...args),
  chooseImage:async (...args) => { picks.push(args); return {path:'film/refs/garden.png',url:'/media/film/refs/garden.png'}; },
  markDirty(){ context.state.dirty = true; },
  saveNow:async () => saves.push(structuredClone(context.state.board)),
  render(){ context.renderFilmBible(); },
});
const start = source.indexOf('function renderFilmBible');
vm.runInContext(source.slice(start,source.indexOf('function renderStudioContinuity',start)), context);
context.renderFilmBible();
const button = text => nodes(parent).find(node => node.tag === 'button' && node.children.includes(text));
await button('Velg referansebilde').listeners.click();
assert.equal(context.state.board.filmBible.locations[0].referenceImage.path,'film/refs/garden.png');
assert.equal(saves.length,1);
assert.equal(picks[0][1].generate,false);
assert(nodes(parent).some(node => node.tag === 'img' && node.src === '/media/film/refs/garden.png'));
button('Fjern referanse').listeners.click();
assert.equal(context.state.board.filmBible.locations[0].referenceImage,undefined);

// A delayed library choice must never attach to a different open project.
let resolveChoice;
context.chooseImage = () => new Promise(resolve => { resolveChoice = resolve; });
const pending = button('Velg referansebilde').listeners.click();
context.state.slug = 'other'; context.state.board = structuredClone(initial);
resolveChoice({path:'film/refs/garden.png'});
await pending;
assert.equal(context.state.board.filmBible.locations[0].referenceImage,undefined);
assert.equal(saves.length,1);
console.log('Film assets: real reference selection/save/preview/removal and stale-project reply isolation passed.');
