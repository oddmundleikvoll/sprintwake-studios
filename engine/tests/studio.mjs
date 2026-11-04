// CPU-only studio contracts; the browser pass verifies actual DOM/layout.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source=fs.readFileSync(new URL("../js/app.js",import.meta.url),"utf8");
const section=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(new URL("../js/studio.js",import.meta.url),"utf8"),context);
const {groupScenes,scenePage,productionAction,localMedia}=context.StoryboardStudio;
const shots=[{id:"a",directorScene:1,frames:97},{id:"b",directorScene:1,frames:96,startRef:{kind:"chain",from:"a"}},
  {id:"c",directorScene:2,frames:97},{id:"d",directorScene:3,frames:97}];
const before=JSON.stringify(shots);
assert.equal(groupScenes(shots).length,3);
assert.equal(groupScenes(shots)[0].shots[1].id,"b");
assert.equal(JSON.stringify(shots),before,"Browsing scenes must not mutate or renumber saved clips");
assert.equal(groupScenes([{id:"old",title:"Scene 2 — old"},{id:"other",title:"An unlabeled continuation"}])[0].shots.length,2);
assert.equal(groupScenes([{id:"a",title:"No metadata"},{id:"b",title:"No metadata either"}]).length,1);
const longScene={key:"long",shots:Array.from({length:30},(_,i)=>({id:`clip-${i}`}))};
const shortScene={key:"short",shots:[{id:"last"}]};
const pages=new Map([["long",1]]);
assert.equal(scenePage(longScene,pages).shots[0].id,"clip-24");
assert.equal(scenePage(shortScene,pages).shots[0].id,"last");
assert.equal(scenePage(longScene,pages).index,1,"A later short scene cannot reset another scene's page");
assert.equal(localMedia("https://example.com/x.mp4"),false);
assert.equal(localMedia("/media/demo/clip.mp4?t=2"),true);
assert.equal(localMedia("/media/../private"),false);
assert.throws(()=>productionAction({project:{canProduce:false,productionError:"Locked clip"}}),/Locked clip/);
const action=productionAction({anchorRevision:"reviewed-revision",project:{slug:"demo",canProduce:true}});
assert.equal(action.tool,"render_storyboard");
assert.equal(action.revision,"reviewed-revision");
assert.equal(action.slug,"demo");
assert.equal(action.continuationAnchor,false);

let adopted=0,saves=0;
const state={slug:"target",board:{shots:[{id:"chain",startRef:{kind:"chain",from:"prior"}}]},selectedId:"chain",dirty:false};
const apply=vm.createContext({state,
  studioContext:()=>({busy:false}),saveStudio:async()=>{saves++;},
  shotById:id=>state.board.shots.find(s=>s.id===id),
  API:{adoptRef:async(slug,path)=>{adopted++;return {kind:"upload",path:`${slug}/refs/copy.png`,url:`/media/${slug}/refs/copy.png`};}},
  markDirty:()=>{state.dirty=true;},render(){},toast(){},SHOT_PROMPT_TEMPLATE:"Opening",shots:()=>state.board.shots,
});
vm.runInContext(section("async function useStudioImage(","async function showStudioFilmProgress("),apply);
await assert.rejects(()=>apply.useStudioImage({ref:{path:"source/refs/a.png"}},{kind:"start",shotId:"chain",projectSlug:"target"}),/Bekreft/);
assert.equal(adopted,0,"A chain cannot be broken before explicit choice");
await apply.useStudioImage({ref:{path:"source/refs/a.png"}},{kind:"start",shotId:"chain",projectSlug:"target",breakChain:true});
assert.equal(adopted,1);
assert.equal(state.board.shots[0].startRef.path,"target/refs/copy.png");
assert.equal(saves,2);
state.board.shots[0].locked=true;
await assert.rejects(()=>apply.useStudioImage({ref:{path:"source/refs/a.png"}},{kind:"end",shotId:"chain",projectSlug:"target"}),/låst/);
assert.equal(adopted,1);
// A slow adoption cannot overwrite a newer manually chosen image.
state.board.shots[0].locked=false;
let resolveAdopt;
apply.API.adoptRef=()=>new Promise(resolve=>{resolveAdopt=resolve;});
const attachment=apply.useStudioImage({ref:{path:"source/refs/a.png"}},{kind:"end",shotId:"chain",projectSlug:"target"});
for(let i=0;i<12&&!resolveAdopt;i++)await Promise.resolve();
assert.equal(typeof resolveAdopt,"function");
state.board.shots[0].endRef={path:"target/refs/new-manual.png"};
resolveAdopt({path:"target/refs/late-copy.png"});
await assert.rejects(()=>attachment,/manuelle valget/);
assert.equal(state.board.shots[0].endRef.path,"target/refs/new-manual.png");
await assert.rejects(()=>apply.useStudioImage({ref:{path:"source/refs/a.png"}},{kind:"new",projectSlug:"another"}),/byttet/);

// Adding a clip cannot replace edits made while the server response is pending.
let finishAdd;
const addState={slug:"a",board:{shots:[{id:"existing",prompt:"before"}]},editVersion:0};
const addContext=vm.createContext({state:addState,API:{addShot:()=>new Promise(resolve=>{finishAdd=resolve;})},
  saveStudio:async()=>{},shots:()=>addState.board.shots,markDirty(){},render(){},toast(){},SHOT_PROMPT_TEMPLATE:"New"});
vm.runInContext(section("async function addShot()", "/* --- editor"),addContext);
const adding=addContext.addShot();
for(let i=0;i<12&&!finishAdd;i++)await Promise.resolve();
addState.editVersion++;addState.board.shots[0].prompt="latest manual edit";
finishAdd({board:{shots:[{id:"existing",prompt:"before"},{id:"new"}],anchorsRevision:"new-revision"},shot:{id:"new"}});
await adding;
assert.equal(addState.board.shots[0].prompt,"latest manual edit");assert.equal(addState.board.shots[1].id,"new");
assert.equal(addState.board.anchorsRevision,"new-revision");

const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{classList:{toggle(){}},style:{},setAttribute(){},value:"Uferdig melding",focus(){}});return elements.get(id);};
let chatPaints=0,docked=false;
const director=vm.createContext({state:{bladeOpen:false},$:element,
  localStorage:{setItem(){}},positionAssistantBlade(){},renderAssistantChat(){chatPaints++;},setTimeout(){},
  storyboardStudio:{director:open=>{docked=open;}},
});
vm.runInContext(section("function setBladeOpen(","function paintBladeContext("),director);
director.setBladeOpen(true);
assert.equal(element("#assistantBlade").inert,false);
assert.equal(docked,true);
director.setBladeOpen(false);
assert.equal(element("#assistantBlade").inert,true);
assert.equal(element("#bladeInput").value,"Uferdig melding");
assert.equal(chatPaints,1,"Closing the director must not replace messages or composer drafts");
const planInput={value:"",focus(){this.focused=true;}};
let planOpened=0;
const planContext=vm.createContext({state:{board:{characters:[{id:"robot",name:"Rødbert"}]}},
  setBladeOpen:()=>{planOpened++;},$:()=>planInput});
vm.runInContext(section("function draftStudioFilmPlan(", "function initializeStudio()"),planContext);
planContext.draftStudioFilmPlan();
assert.match(planInput.value,/dette prosjektet med Rødbert/);
assert.match(planInput.value,/eksisterende karakterkort/);
assert.match(planInput.value,/Ikke start generering/);
assert.equal(planOpened,1);
planInput.value="Min egen usendte idé";
planContext.draftStudioFilmPlan();
assert.equal(planInput.value,"Min egen usendte idé");
planInput.value="";
planContext.draftStudioFilmPlan(true);
assert.match(planInput.value,/eksisterende filmplan/);
assert.match(planInput.value,/Behold historien, klipp-ID-er/);
assert.match(planInput.value,/Ikke start generering/);
console.log("Studio: scene navigation, reviewed production actions, scoped image use and director docking passed.");
