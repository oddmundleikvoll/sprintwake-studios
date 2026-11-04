// Real frontend dispatch with API stubs: never starts a server or a model.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const calls = [];
const context = vm.createContext({
  state: {slug: 'source', board: {}, status: {}, pendingSaves: new Set(), dirty: false},
  $: () => null,
  filmRequest: async (...args) => { calls.push(['api', ...args]); return {slug: 'new-film', status: {busy:true, slug:'new-film'}}; },
  openFilmResponse: async (...args) => { calls.push(['open', ...args]); },
  paintFilmStatus: () => {},
  startPolling: () => calls.push(['poll']),
  showStudioFilmProgress: () => calls.push(['studio-progress']),
  saveNow: async () => { calls.push(['save']); context.state.dirty = false; },
  takeStale: () => {},
  API: {getBoard: async slug => { calls.push(['board', slug]); return {board: {filmAutomation: {revision:'fresh'}}}; }},
  BOARD_TOOLS: new Set(),
});
vm.runInContext(section('const FILM_OPERATION_TOOLS', '// The installed video motor'), context);
vm.runInContext(section('async function applyAssistantActions', 'const AD_THINKING_LINES'), context);

for (const [tool, operation] of [['start_film','start'], ['render_storyboard','resume'], ['resume_film','resume']]) {
  calls.length = 0;
  const action = {tool, slug:'source', revision:'revision-1', continuationAnchor:true};
  const result = await context.applyAssistantActions([action]);
  const api = calls.find(row => row[0] === 'api');
  assert.equal(api[1], 'POST');
  assert.equal(api[2], '/api/film/action');
  assert.equal(api[3].slug, 'source');
  assert.equal(api[3].action, action);
  assert.equal(result.movedTo, 'new-film');
  assert.equal(calls.find(row => row[0] === 'open')[2], operation === 'resume');
}
calls.length = 0;
await context.applyAssistantActions([{tool:'stop_film', slug:'source', jobId:'job-1'}]);
assert(calls.some(row => row[0] === 'poll'));
assert(!calls.some(row => row[0] === 'save'));
assert.equal(context.state.status.film.busy, true);

calls.length = 0;
await context.applyAssistantActions([{tool:'resume_film', acceptReview:true, slug:'source'}]);
assert(calls.some(row => row[0] === 'studio-progress'));
assert(!calls.some(row => row[0] === 'api'));

calls.length = 0;
context.state.status = {};
await context.applyAssistantActions([{tool:'set_film_anchor', slug:'source', revision:'old', enabled:true}]);
assert.equal(context.state.board.filmAutomation.revision, 'fresh');
assert(calls.some(row => row[0] === 'board'));

calls.length = 0;
const anchors = [{imageId:'img_selected', frame:0, strength:.4}, {imageId:'img_second', frame:'last', strength:.7}];
const anchorResult = await context.applyAssistantActions([{tool:'set_shot_anchors', slug:'source',
  revision:'anchor-revision', shotId:'s1', anchors}]);
assert(calls.some(row => row[0] === 'board'));
assert(!calls.some(row => row[0] === 'open' || row[0] === 'poll'));
assert.match(anchorResult.messages[0], /Ingen generering/);
assert.deepEqual(calls.find(row => row[0] === 'api')[3].action.anchors, anchors);

calls.length = 0;
await assert.rejects(context.applyAssistantActions([{tool:'start_film'}, {tool:'add_shot'}]), /alene/);
assert.equal(calls.length, 0);
context.state.status = {film:{busy:true, slug:'source'}};
await assert.rejects(context.applyAssistantActions([{tool:'set_board_fields', fields:{sceneDescription:'wrong'}}]), /eier prosjektet/);
assert.equal(calls.length, 0);
console.log('Film chat dispatch: start, resume, scoped stop, anchor refresh, mixed-action and active-project guards passed.');

// Exercise the real response/dialog/polling path, not just its dispatch stub.
// A planned film already started; showing this result must never open a new
// blank creation form or submit another request.
const progressElements = new Map();
const progressCalls = [];
const progressElement = selector => {
  if (!progressElements.has(selector)) progressElements.set(selector, {
    hidden:false, disabled:false, textContent:'', value:'', focus(){ progressCalls.push(['focus',selector]); },
  });
  return progressElements.get(selector);
};
const plannedBoard = {name:'Robot og orc', filmAutomation:{plannedClips:7, requestedSeconds:28.125},
  shots:[1,1,1,2,2,2,3].map(directorScene => ({directorScene}))};
const progressContext = vm.createContext({
  state:{slug:'source',board:plannedBoard,status:{},filmDialogMode:'create'},
  $:progressElement, document:{querySelector:progressElement},
  boardName:slug => slug, paintFilmChatControls(){},
  API:{getBoard:async () => ({board:plannedBoard}),listBoards:async () => ({boards:[]})},
  setBoard:(slug,board) => Object.assign(progressContext.state,{slug,board}),
  renderBoardPicker(){}, startPolling:() => progressCalls.push(['poll']),
  toast:text => progressCalls.push(['toast',text]), setTimeout:fn => fn(),
  filmRequest:() => { throw new Error('Displaying status must not start a job'); },
});
vm.runInContext(section('function filmClipCount', 'async function resumeFilmFromDialog'),progressContext);
for (const [seconds, count] of [[6,1],[7,2],[8,2],[10,2],[12,2],[13,3],[30,6],[60,12],[600,120],[7200,1440]]) {
  assert.equal(progressContext.filmClipCount(seconds), count);
}
progressElement('#filmDuration').value = '30';
progressContext.paintFilmEstimate();
assert.match(progressElement('#filmEstimate').textContent, /6 klipp/);
assert.match(progressElement('#filmEstimate').textContent, /4–6 sekunder/);
await progressContext.openFilmResponse({operation:'planned',slug:'source',
  status:{busy:true,slug:'source',phase:'images',detail:'Qwen-Image-2.1-Turbo: Ragnar',completed:0,total:7,
    plan:{scenes:3,estimatedVideoSeconds:28.125}}},true);
assert.equal(progressElement('#filmDialog').hidden,false);
assert.equal(progressElement('#filmDialogTitle').textContent,'Filmproduksjon');
assert.equal(progressElement('#filmSetup').hidden,true);
assert.equal(progressElement('#filmStart').hidden,true);
assert.equal(progressElement('#filmJobSummary').hidden,false);
assert.match(progressElement('#filmJobSummary').textContent,/Robot og orc.*3 scener.*7 klipp.*28\.13/);
assert.match(progressElement('#filmStatus').textContent,/Filmjobben kjører.*Referansebilder.*Ragnar.*0\/7/);
assert.equal(progressElement('#btnFilmFromIdea').textContent,'Filmstatus');
assert.equal(progressElement('.main').inert,true);
assert.equal(progressCalls.filter(row => row[0]==='poll').length,1);
progressContext.state.status.film.phase='video';
progressContext.state.status.film.completed=1;
progressContext.paintFilmStatus(progressContext.state.status.film);
assert.match(progressElement('#filmStatus').textContent,/Video.*1\/7 klipp lagret/);
progressElement('#filmDialog').hidden=true;
progressContext.openFilmDialog();
assert.equal(progressElement('#filmDialog').hidden,false);
assert.equal(progressElement('#filmSetup').hidden,true);
assert.equal(progressElement('#filmStart').hidden,true);
assert(!progressCalls.some(row => row[0]==='focus'));
Object.assign(progressContext.state.status.film,{busy:false,phase:'failed',error:'Inert failure'});
progressContext.paintFilmStatus(progressContext.state.status.film);
assert.equal(progressElement('#filmStop').hidden,true);
assert.equal(progressElement('#filmResume').hidden,false);
assert.equal(progressElement('#filmStart').hidden,true);
assert.match(progressElement('#filmStatus').textContent,/Inert failure/);
assert.equal(progressElement('.main').inert,false);
progressContext.openFilmDialog('A separate new film');
assert.equal(progressElement('#filmDialogTitle').textContent,'Ny film fra idé');
assert.equal(progressElement('#filmSetup').hidden,false);
assert.equal(progressElement('#filmStart').hidden,false);
assert.equal(progressElement('#filmJobSummary').hidden,true);
assert.equal(progressElement('#filmDescription').value,'A separate new film');
assert(progressCalls.some(row => row[0]==='focus'));
await progressContext.openFilmResponse({operation:'resume',slug:'source',
  status:{busy:true,slug:'source',phase:'video',completed:2,total:7}},true);
assert.equal(progressElement('#filmSetup').hidden,true);
assert.equal(progressElement('#filmStart').hidden,true);
assert.match(progressElement('#filmStatus').textContent,/2\/7/);
await progressContext.openFilmResponse({operation:'start',slug:'source',
  status:{busy:false,slug:'source',phase:'failed',error:'Failed just after admission',completed:0,total:7}});
assert.equal(progressContext.state.status.film.busy,false);
assert.equal(progressElement('#filmStop').hidden,true);
assert.equal(progressElement('#filmResume').hidden,false);
assert.match(progressElement('#filmStatus').textContent,/Failed just after admission/);
console.log('Film progress: actual response, project summary, live updates, safe reopen and separate creation form passed.');

progressContext.state.board={name:'Neutral film',shots:[],filmAutomation:{description:'A robot walks.'}};
progressContext.paintFilmStatus({...progressContext.state.status.film,busy:false,phase:'failed',canRetryPlanning:true,canResume:true});
assert.equal(progressElement('#filmResume').hidden,false);
assert.equal(progressElement('#filmResume').textContent,'Prøv planlegging igjen');
assert.match(progressElement('#filmStatus').textContent,/samme prosjekt/);
progressContext.paintFilmStatus({...progressContext.state.status.film,busy:false,phase:'failed',canResume:false});
assert.equal(progressElement('#filmResume').hidden,true);
progressContext.paintFilmStatus({...progressContext.state.status.film,busy:false,phase:'unplanned',canResume:true});
assert.equal(progressElement('#filmResume').hidden,false);

// Real modal options and review acceptance must reach the controller intact.
const productionRequests = [];
Object.assign(progressContext.state, {pendingSaves:new Set(), dirty:false});
progressContext.filmRequest = async (method,path,payload) => {
  productionRequests.push({method,path,payload});
  return {slug:'source',status:{busy:false,slug:'source',phase:'prepared',revision:'review-revision',
    animaticUrl:'/media/source/animatic.html',completed:0,total:7}};
};
for (const [selector,value] of [['#filmDescription','A feature'],['#filmTitle','Feature'],
  ['#filmDuration','7200'],['#filmReviewMode','fast'],['#filmSpeechMode','recording'],['#filmEndStrength','0.4']]) {
  progressElement(selector).value=value;
}
progressElement('#filmPrepareOnly').checked=true;
progressElement('#filmEndAnchors').checked=false;
await progressContext.startFilmFromDialog();
const options=productionRequests.at(-1).payload;
assert.equal(options.duration,7200);
assert.equal(options.reviewMode,'fast');
assert.equal(options.speechMode,'recording');
assert.equal(options.prepareOnly,true);
assert.equal(options.automaticEndAnchors,false);
assert.equal(options.endAnchorStrength,0.4);
assert.equal(progressElement('#filmResume').hidden,false);
assert.equal(progressElement('#filmAnimatic').href,'/media/source/animatic.html');
vm.runInContext(section('async function resumeFilmFromDialog', 'async function stopFilmFromDialog'),progressContext);
progressContext.state.status.film.phase='needs_review';
progressContext.paintFilmStatus(progressContext.state.status.film);
assert.match(progressElement('#filmResume').textContent,/Godkjenn videoscenen/);
progressElement('#filmReviewAccept').checked=true;
await progressContext.resumeFilmFromDialog();
assert.equal(productionRequests.at(-1).payload.acceptReview,true);
assert.equal(productionRequests.at(-1).payload.expectedRevision,'review-revision');
console.log('Long film controls: duration, quality, animatic, voices, anchors and revision-bound review resume passed.');

// Execute the real card click handler: backend errors must survive the toast,
// leave the action unapplied, and clear after a successful retry.
class CardElement {
  constructor(tag, cls, ...children) { this.tag = tag; this.cls = cls; this.children = children; this.listeners = {}; this.style = {}; }
  set innerHTML(value) { this.children = []; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); return child; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
}
const cardHost = new CardElement('div');
const cardTurn = {role:'assistant', actions:[{tool:'start_film', description:'Robot and orc', duration:30, plannedClips:6}]};
const persisted = [];
const cardContext = vm.createContext({
  state:{slug:'source', chats:{source:[cardTurn]}},
  FILM_OPERATION_TOOLS:new Set(['start_film']),
  $:() => cardHost, el:(...args) => new CardElement(...args),
  chatTurns:() => [cardTurn], paintFilmChatControls(){}, paintBladeContext(){},
  persistChat:slug => persisted.push(slug), render(){}, toast(){},
  applyAssistantActions:async () => { throw new Error('Åpne Comfy Desktop og start «LTX-2.5 Q8».'); },
});
vm.runInContext(section('function proposalDetailFields', 'function proposalSummary'), cardContext);
vm.runInContext(section('function proposalSummary', 'function renderAssistantChat'), cardContext);
vm.runInContext(section('function renderAssistantChat', 'function assistantId'), cardContext);
const cardNodes = node => [node, ...node.children.filter(child => child instanceof CardElement).flatMap(cardNodes)];
const startButton = () => cardNodes(cardHost).find(node => node.tag === 'button' && node.children.includes('Start ny film'));
cardContext.renderAssistantChat();
assert(startButton());
await startButton().listeners.click();
assert.match(cardTurn.applyError, /Comfy Desktop/);
assert.equal(cardTurn.applied, undefined);
assert(cardNodes(cardHost).some(node => node.cls === 'chat-message assistant error' && node.children.includes(cardTurn.applyError)));
assert.deepEqual(persisted, ['source']);
cardContext.applyAssistantActions = async () => ({messages:[]});
await startButton().listeners.click();
assert.equal(cardTurn.applied, true);
assert.equal(cardTurn.applyError, undefined);
assert(!cardNodes(cardHost).some(node => node.cls === 'chat-message assistant error'));
console.log('Film start cards: persistent actionable errors, safe retry and new-project label passed.');

const elements = new Map();
const turns = [];
context.$ = selector => {
  if (!elements.has(selector)) elements.set(selector, {value:'', hidden:false, focus(){}});
  return elements.get(selector);
};
Object.assign(context, {
  AbortController,
  currentLLM: () => null,
  chatTurns: () => turns,
  persistChat: () => {},
  randomADThinkingLine: () => 'test',
  renderAssistantChat: () => {},
  paintChatProgress: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
  chatLive: null,
  toast: (...args) => calls.push(['toast', ...args]),
  openFilmDialog: () => calls.push(['dialog']),
  speakAssistantReply: () => { throw new Error('Local film controls must not invoke TTS'); },
});
context.state.status = {};
context.state.speechEnabled = true;
context.state.chatBusy = false;
context.API.chat = async (...args) => {
  calls.push(['chat', ...args]);
  return {message:'status', actions:[], localControl:true};
};
vm.runInContext(section('async function sendAssistantMessage', 'function wireAssistantBlade'), context);
vm.runInContext(section('async function stopFilmFromDialog', 'const FILM_OPERATION_TOOLS'), context);

for (const text of ['filmstatus', '/film stop?', 'Kan du stoppe filmen?', '/film resume', '/film produce', 'start filmen', 'Slå på videreføringsankeret']) {
  calls.length = 0;
  context.$('#bladeInput').value = text;
  await context.sendAssistantMessage();
  assert.equal(calls.filter(row => row[0] === 'chat').length, 1, text);
  assert(!calls.some(row => row[0] === 'dialog'), text);
}
calls.length = 0;
context.$('#bladeInput').value = 'Skriv et nytt manus';
await context.sendAssistantMessage();
assert(!calls.some(row => row[0] === 'chat'));
assert(calls.some(row => row[0] === 'toast'));

calls.length = 0;
context.state.status = {film:{busy:true, slug:'source', jobId:'job-A'}};
await context.stopFilmFromDialog();
const stopped = calls.find(row => row[0] === 'api');
assert.equal(stopped[2], '/api/film/stop');
assert.equal(stopped[3].slug, 'source');
assert.equal(stopped[3].jobId, 'job-A');
console.log('Film chat availability: unloaded Qwen, no read-aloud TTS, ordinary-chat health guard and scoped dialog stop passed.');

class ReviewElement {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.hidden = false; this.disabled = false; this.checked = false; this.textContent = ''; this.dataset = {}; this.listeners = {}; this.attributes = {}; this.value = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  appendChild(node) { this.append(node); return node; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const reviewElements = new Map();
const reviewElement = selector => {
  if (!reviewElements.has(selector)) reviewElements.set(selector, new ReviewElement());
  return reviewElements.get(selector);
};
const reviewDocument = {
  querySelector: reviewElement,
  createElement: tag => new ReviewElement(tag),
  createTextNode: text => { const node = new ReviewElement('text'); node.textContent = text; return node; },
};
const reviewInput = () => {
  const walk = node => {
    if (node.id === 'filmReviewAccept' || (node.tag === 'input' && node.type === 'checkbox')) return node;
    for (const child of node.children || []) { const found = walk(child); if (found) return found; }
    return null;
  };
  return walk(reviewElement('#filmReview')) || new ReviewElement('input');
};
const reviewDollar = selector => {
  if (selector === '#filmReviewAccept') return reviewInput();
  if (selector === '#filmReviewNote') {
    const find = node => node.id === 'filmReviewNote' ? node : (node.children || []).map(find).find(Boolean);
    return find(reviewElement('#filmReview')) || reviewElement(selector);
  }
  return reviewElement(selector);
};
const reviewRequests = [];
const reviewContext = vm.createContext({
  state:{slug:'source',board:{filmAutomation:{revision:'rev-1'},shots:[]},status:{},pendingSaves:new Set(),dirty:false},
  $:reviewDollar, document:reviewDocument,
  paintFilmStatus:()=>{}, saveNow:async()=>{},
  filmRequest:async (method,path,payload)=>{reviewRequests.push({method,path,payload});return {slug:'source',status:{phase:'video',busy:true,revision:'rev-2'}};},
  openFilmResponse:async()=>{}, toast:()=>{}, Promise, setTimeout:fn=>fn(),
});
vm.runInContext(section('function paintFilmReview', 'function showFilmProgress'), reviewContext);
vm.runInContext(section('async function resumeFilmFromDialog', 'async function stopFilmFromDialog'), reviewContext);
const reviewFilm = (phase, extra = {}) => ({slug:'source', phase, revision:'rev-1', busy:false,
  imagePlan:{digest:'digest-1',cards:[{name:'Robot',ref:{path:'refs/card.png'}}],images:[{title:'Garden',kind:'start',ref:{path:'refs/scene.png'}}]},
  ...extra});
reviewContext.state.status.film = reviewFilm('prepared');
reviewContext.paintFilmReview(reviewContext.state.status.film);
assert.equal(reviewElement('#filmReview').children.length > 0, true);
const preparedAccept = reviewInput();
assert.notEqual(preparedAccept.disabled, true);
preparedAccept.checked = true;
reviewContext.state.status.film.revision = 'rev-2';
reviewContext.paintFilmReview(reviewContext.state.status.film);
assert.equal(reviewInput().checked, false);
reviewContext.state.status.film = reviewFilm('prepared', {imagePlan:undefined});
reviewContext.state.board.filmAutomation.imagePlan = undefined;
reviewContext.paintFilmReview(reviewContext.state.status.film);
assert.equal(reviewInput().disabled, true);
reviewContext.state.status.film = reviewFilm('prepared', {imagePlan:{digest:'digest-1',cards:[],images:[]}});
reviewContext.state.board.filmAutomation.imagePlanStale = true;
reviewContext.paintFilmReview(reviewContext.state.status.film);
assert.equal(reviewInput().disabled, true);
reviewContext.state.status.film = reviewFilm('prepared');
reviewContext.state.board.filmAutomation.imagePlanStale = false;
reviewContext.paintFilmReview(reviewContext.state.status.film);
reviewInput().checked = true;
reviewRequests.length = 0;
await reviewContext.resumeFilmFromDialog();
assert.equal(reviewRequests.at(-1).payload.acceptImagePlan, 'digest-1');
assert.equal(reviewRequests.at(-1).payload.expectedRevision, 'rev-1');
reviewRequests.length = 0;
reviewInput().checked = false;
await reviewContext.resumeFilmFromDialog(true);
assert.equal(reviewRequests.at(-1).payload.prepareOnly, true);
assert.equal('acceptImagePlan' in reviewRequests.at(-1).payload, false);
assert.notEqual(reviewRequests.at(-1).payload.acceptReview, true);
reviewRequests.length = 0;
reviewContext.state.status.film = reviewFilm('needs_review', {quality:{issues:[]}, imagePlan:undefined});
reviewContext.state.board.shots = [{status:'done',title:'Scene',outputs:['/media/source/shots/01/clip.mp4']}];
reviewContext.paintFilmReview(reviewContext.state.status.film);
assert.equal(reviewInput().disabled, false);
assert.equal(reviewElement('#filmReview').children.some(node => node.tag === 'div'), true);
reviewRequests.length = 0;
reviewInput().checked = true;
await reviewContext.resumeFilmFromDialog();
assert.equal(reviewRequests.at(-1).payload.acceptReview, true);
reviewContext.state.status.film = reviewFilm('prepared');
reviewContext.state.board.filmAutomation.imagePlanStale = true;
reviewInput().checked = true;
reviewRequests.length = 0;
await reviewContext.resumeFilmFromDialog();
assert.equal(reviewRequests.length, 0);
reviewContext.state.board.filmAutomation.imagePlanStale = false;
reviewContext.state.status.film = reviewFilm('prepared');
reviewContext.paintFilmReview(reviewContext.state.status.film);
reviewRequests.length = 0;
await reviewContext.resumeFilmFromDialog({type:'click'});
assert.equal(reviewRequests.length, 0);
// Each boundary shows only its own videos and the exact adjacent images.
reviewContext.state.board.shots = [
  {id:'old',status:'done',title:'Earlier',outputs:['/media/source/shots/01/clip.mp4']},
  {id:'current',status:'done',title:'Current',outputs:['/media/source/shots/02/clip.mp4']},
];
reviewContext.state.status.film = reviewFilm('needs_review', {reviewRequired:'receipt-boundary',
  reviewPreview:{key:'receipt-boundary',scene:2,title:'Reverse angle',shotIds:['current'],
    tail:{path:'source/shots/02/frames/tail.png'},nextOpening:{path:'source/refs/next.png'},
    nextTitle:'Next',nextStateBefore:'Robot holds cup',modelAvailable:false}});
reviewContext.paintFilmReview(reviewContext.state.status.film);
const reviewNodes = node => [node,...(node.children || []).flatMap(reviewNodes)];
const boundaryNodes = reviewNodes(reviewElement('#filmReview'));
assert.deepEqual(boundaryNodes.filter(n => n.tag === 'video').map(n => n.src), ['/media/source/shots/02/clip.mp4']);
assert(boundaryNodes.some(n => n.tag === 'img' && n.src === '/media/source/refs/next.png'));
assert(boundaryNodes.some(n => n.tag === 'img' && n.src.includes('/frames/tail.png')));
assert(boundaryNodes.some(n => /Ingen visuell modellgodkjenning/.test(n.textContent)));
reviewDollar('#filmReviewNote').value = 'Actual robot holds the cup.';
reviewInput().checked = true;
await reviewContext.resumeFilmFromDialog();
assert.equal(reviewRequests.at(-1).payload.reviewNote, 'Actual robot holds the cup.');
console.log('Film review gate: card/scene previews, stale and missing-manifest rejection, digest/revision binding, refresh semantics and video accessibility passed.');
