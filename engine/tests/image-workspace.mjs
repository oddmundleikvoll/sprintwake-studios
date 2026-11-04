import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(tag='div') { this.nodeType=1;this.tagName=tag;this.children=[];this.listeners={};this.attributes={};this.style={};this.dataset={};this.className='';this.hidden=false;this.value=''; }
  get firstChild(){return this.children[0];}
  append(...items){for(const item of items.filter(Boolean)){item.parentNode=this;this.children.push(item);}}
  appendChild(item){this.append(item);return item;}
  replaceChildren(...items){this.children=[];this.append(...items);}
  replaceWith(item){const p=this.parentNode;if(p){p.children[p.children.indexOf(this)]=item;item.parentNode=p;}}
  setAttribute(k,v){this.attributes[k]=String(v);if(k==='class')this.className=String(v);}
  getAttribute(k){return this.attributes[k]??null;}
  addEventListener(k,v){this.listeners[k]=v;}
  async click(){return this.listeners.click?.({});}
  requestSubmit(){return this.listeners.submit?.({preventDefault(){}});}
  focus(){this.ownerDocument.activeElement=this;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector){const last=selector.split(' ').at(-1);const matches=n=>{
    if(last.startsWith('.'))return n.className?.split(' ').includes(last.slice(1));
    if(last.startsWith('[')){const [,key,value]=/^\[([^=]+)="([^"]*)"\]$/.exec(last)||[];return n.attributes?.[key]===value;}
    return n.tagName===last;
  };return this.children.flatMap(n=>n.nodeType===1?[...(matches(n)?[n]:[]),...n.querySelectorAll(selector)]:[]);}
}
const script=fs.readFileSync(new URL('../js/image-workspace.js',import.meta.url),'utf8');
const image=id=>({id,status:'done',prompt:`Prompt ${id}`,ref:{path:`a/refs/${id}.png`,url:`/media/a/refs/${id}.png`,label:`${id}.png`}});
const input={prompt:'En rød hytte',format:'3:2',size:768,seed:null,inheritStyle:true};
const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
function harness(handler,extra={}){
  let ctx={slug:'a',board:{shots:[{id:'shot',title:'Åpning'}]},selectedShot:{id:'shot'}};
  const timers=new Map();let nextTimer=1;
  const document={activeElement:null,createElement(tag){const n=new Element(tag);n.ownerDocument=document;return n;},createTextNode:text=>({nodeType:3,textContent:text}),querySelector:()=>null};
  const host=document.createElement('main'),calls=[];
  const sandbox=vm.createContext({document,console,setTimeout:fn=>{const id=nextTimer++;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),confirm:()=>true});
  vm.runInContext(script,sandbox);
  const controller=sandbox.StoryboardImages.create(host,{getContext:()=>ctx,request:async(method,url,body)=>{calls.push({method,url,body});return handler(method,url,body);},...extra});
  const button=label=>host.querySelectorAll('button').find(b=>b.textContent===label);
  return {controller,host,calls,timers,document,button,api:sandbox.StoryboardImages,setContext:value=>{ctx=value;},
    async tick(){const [id,fn]=timers.entries().next().value||[];assert.ok(fn,'A running job should schedule a poll');timers.delete(id);await fn();await flush();}};
}

// Actual server shape: history always accompanies job status, refs are nested.
let phase='running',saves=0;
const h=harness(async(method,url)=>{
  if(method==='POST')return {job:{id:'job',jobId:'job',busy:true,status:'running'}};
  if(!url.includes('jobId'))return {images:[image('old')],job:null};
  return phase==='running'?{images:[image('old'),{id:'new',status:'running'}],job:{id:'job',busy:true,status:'running',phase:'Lager bilde'}}:
    {images:[image('old'),image('new')],job:{id:'job',recordId:'new',busy:false,status:'done',result:image('new')}};
},{ensureProject:async()=>{saves++;return {slug:'a'};}});
assert.equal(h.api.localUrl({ref:{path:'a/refs/a b.png'}}),'/media/a/refs/a%20b.png');
assert.equal(h.api.localUrl({url:'https://example.com/a.png'}),'');
assert.equal(h.api.localUrl({path:'../secret.png'}),'');
assert.equal(h.api.localUrl({url:42}),'');
assert.equal(h.api.localUrl({path:'a/refs/a%20b.png'}),'/media/a/refs/a%20b.png');
assert.equal(h.api.localUrl({path:'a/refs/../secret.png'}),'');
assert.equal(h.api.localUrl({path:'a\\secret.png'}),'');
await h.controller.open();
const prompt=h.host.querySelector('[aria-label="Bildebeskrivelse"]');prompt.value='Uferdig tekst';prompt.listeners.input();prompt.focus();
await h.button('Bruk som referanse').click();
await h.controller.generate(input);
assert.equal(saves,1,'Existing projects must save before starting an image');
assert.equal(h.calls.find(c=>c.method==='POST').body.references[0].path,'a/refs/old.png');
assert.equal(h.controller.getState().job.id,'job');
assert.equal(h.controller.getState().images.length,1,'Running history is not a completed result');
const currentPrompt=h.host.querySelector('[aria-label="Bildebeskrivelse"]');
await h.tick();
assert.equal(h.host.querySelector('[aria-label="Bildebeskrivelse"]'),currentPrompt,'Live polling must preserve the composer DOM');
phase='done';await h.tick();
assert.equal(h.controller.getState().selected.id,'new');
assert.equal(h.controller.getState().images.length,2);
assert.equal(h.timers.size,0,'Completed jobs cannot leave polling timers');
await h.controller.close();await h.controller.open();
assert.equal(h.controller.getState().prompt,'Uferdig tekst','Reopening the same project preserves its composer');
h.controller.destroy();

// A fresh project starts empty; returning restores all choices, including an
// intentionally empty draft, rather than inheriting another project's text.
const drafts=harness(async()=>({images:[],job:null}));
await drafts.controller.open();
Object.assign(drafts.controller.getState(),{prompt:'Projekt A',refs:[{path:'a/refs/source.png'}],format:'2:3',size:1536});
drafts.setContext({slug:'b',board:{shots:[]}});await drafts.controller.sync();
assert.equal(drafts.controller.getState().prompt,'');assert.equal(drafts.controller.getState().refs.length,0);
assert.equal(drafts.controller.getState().format,'3:2');
drafts.controller.getState().prompt='';drafts.controller.close();await drafts.controller.open();
assert.equal(drafts.controller.getState().prompt,'');
drafts.setContext({slug:'a',board:{shots:[]}});await drafts.controller.sync();
assert.equal(drafts.controller.getState().prompt,'Projekt A');assert.equal(drafts.controller.getState().refs[0].path,'a/refs/source.png');
assert.equal(drafts.controller.getState().size,1536);drafts.controller.destroy();

// A pending picker from project A must not append to or replace project B.
for(const replace of [false,true]){
  const pendingRef=deferred();
  const picker=harness(async()=>({images:[],job:null}),{pickReference:()=>pendingRef.promise});
  await picker.controller.open();
  picker.controller.addReference({path:'a/refs/original.png',label:'Original'});
  const choosing=picker.button(replace?'Original':'+ Legg til referanse').click();
  picker.setContext({slug:'b',board:{shots:[]}});await picker.controller.sync();
  pendingRef.resolve({path:'a/refs/late.png'});await choosing;await flush();
  assert.equal(picker.controller.getState().refs.length,0);
  picker.setContext({slug:'a',board:{shots:[]}});await picker.controller.sync();
  assert.equal(picker.controller.getState().refs.length,1);
  assert.equal(picker.controller.getState().refs[0].path,'a/refs/original.png');picker.controller.destroy();
}

// A closing/reopening panel cannot resurrect an old poll or overlap reads.
const pending=deferred();let reads=0,inFlight=0,maxInFlight=0;
const life=harness(async()=>{
  reads++;inFlight++;maxInFlight=Math.max(maxInFlight,inFlight);
  try{if(reads===2)return await pending.promise;return {images:[],job:{id:'live',busy:true,status:'running'}};}
  finally{inFlight--;}
});
await life.controller.open();await flush();assert.equal(reads,2);
life.controller.close();const reopening=life.controller.open();await flush();assert.equal(reads,2,'New reads wait for the old response');
pending.resolve({images:[],job:{id:'live',busy:true,status:'running'}});await reopening;await flush();
assert.equal(maxInFlight,1);assert.equal(life.timers.size,1);life.controller.destroy();assert.equal(life.timers.size,0);

// Null/stale job status terminates only the old watcher and retains history.
for(const stale of [false,true]){
  const end=harness(async(_method,url)=>url.includes('jobId')?{images:[image('kept')],job:null,stale}:{images:[],job:{id:'past',busy:true,status:'running'}});
  await end.controller.open();await flush();
  assert.equal(end.controller.getState().job,null);assert.equal(end.timers.size,0);assert.equal(end.controller.getState().images[0].id,'kept');end.controller.destroy();
}
const interrupted=harness(async()=>({images:[{id:'interrupted',status:'interrupted',prompt:'Pending before restart',error:'Serveren ble avsluttet'}],job:null}));
await interrupted.controller.open();
assert.equal(interrupted.controller.getState().images.length,0);
assert.ok(interrupted.host.querySelectorAll('.image-error').some(n=>n.textContent.includes('Serveren ble avsluttet')));interrupted.controller.destroy();

// A failed stop can be retried; errors during cleanup cannot free admission.
let stopCalls=0,cleaned=false;
const stop=harness(async(method,url)=>{
  if(method==='POST'){stopCalls++;if(stopCalls===1)throw Error('Request failed');return {};}
  return {images:[],job:{id:'work',busy:!cleaned,status:'failed',error:'Worker error'}};
});
await stop.controller.open();await flush();assert.equal(stop.controller.getState().job.id,'work');
await stop.controller.stop();assert.equal(stop.controller.getState().job.cancelRequested,false);
await stop.controller.stop();assert.equal(stopCalls,2);assert.ok(stop.controller.getState().job,'Stop waits for worker exit');
cleaned=true;await stop.tick();assert.equal(stop.controller.getState().job,null);stop.controller.destroy();

// Film application rejection must remain visible instead of an unhandled promise.
const apply=harness(async()=>({images:[image('ready')],job:null}),{onUseImage:async()=>{throw Error('Målklippet er låst');}});
await apply.controller.open();await apply.button('Bruk i film').click();await apply.button('Bekreft bruk').click();
assert.ok(apply.host.querySelectorAll('.image-error').some(n=>n.textContent==='Målklippet er låst'&&!n.hidden));apply.controller.destroy();

const variant=harness(async()=>({images:[{...image('newer'),createdAt:20,width:1024,height:1536,seed:7,inheritStyle:false,references:[{path:'a/refs/recipe.png'}]}, {...image('older'),createdAt:10}],job:null}));
await variant.controller.open();assert.equal(variant.controller.getState().selected.id,'newer');
await variant.button('Lag ny variant').click();
assert.equal(variant.controller.getState().seed,null);assert.equal(variant.controller.getState().format,'2:3');
assert.equal(variant.controller.getState().size,1536);assert.equal(variant.controller.getState().inheritStyle,false);
assert.equal(variant.controller.getState().refs[0].path,'a/refs/recipe.png');
assert.equal(variant.calls.filter(c=>c.method==='POST').length,0,'Choosing a variant only prepares a new request');variant.controller.destroy();

// Reference limits fail before save or a POST; a failed save cannot generate.
let saveAttempts=0;
const limits=harness(async()=>({images:[],job:null}),{ensureProject:async()=>{saveAttempts++;throw Error('Save failed');}});
await limits.controller.open();limits.controller.getState().refs=Array.from({length:11},()=>({path:'a/refs/x.png'}));
await limits.controller.generate(input);assert.equal(saveAttempts,0);assert.equal(limits.calls.filter(c=>c.method==='POST').length,0);
limits.controller.getState().refs=[];await limits.controller.generate(input);assert.equal(saveAttempts,1);assert.match(limits.controller.getState().error,/Save failed/);limits.controller.destroy();
console.log('Images workspace: real payloads, reference reuse, draft isolation, scoped polling, cleanup, retry and film-application errors passed.');

const deleting=harness(async()=>({images:[image('one'),image('two')],job:null}),{deleteImage:async()=>true});
await deleting.controller.open();
deleting.controller.addReference(image('one').ref);
deleting.controller.selectImage(image('one'));
assert.ok(deleting.button('Slett bilde…'));
deleting.controller.removeImage('a/refs/one.png');
assert.equal(deleting.controller.getState().images.length,1);
assert.equal(deleting.controller.getState().selected.id,'two');
assert.equal(deleting.controller.getState().refs.length,0);
deleting.controller.removeImage('a/refs/two.png');
assert.equal(deleting.controller.getState().selected,null);
assert.equal(deleting.host.querySelector('.image-result-detail'),null);
deleting.controller.destroy();
