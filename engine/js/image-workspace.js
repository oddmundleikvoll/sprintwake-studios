/* Standalone image workspace.  The host owns navigation and project state;
 * this module only owns the image prompt, gallery and image-job lifecycle. */
(function (root) {
  "use strict";

  const FORMATS = { "3:2": [3, 2], "1:1": [1, 1], "2:3": [2, 3], "16:9": [16, 9], "9:16": [9, 16] };
  const SIZES = [768, 1536];

  function localUrl(value) {
    if (!value) return "";
    if (value.ref && typeof value.ref === "object") value = value.ref;
    const path = typeof value === "string" ? value : (value.url || value.path || "");
    if (typeof path !== "string" || !path || /^[a-z][a-z\d+.-]*:/i.test(path) || path.startsWith("//")) return "";
    let clean;
    try { clean = decodeURIComponent(path.replace(/^\/media\//, "").replace(/^\/+/, "")); }
    catch { return ""; }
    if (!clean || clean.split("/").includes("..") || /[\\\u0000-\u001f]/.test(clean)) return "";
    return "/media/" + clean.split("/").map(encodeURIComponent).join("/");
  }
  function create(host, callbacks = {}) {
    if (!host) throw new Error("StoryboardImages.create requires a host element");
    const request = callbacks.request || (async (method, url, body) => {
      const response = await fetch(url, { method, headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(new Error(payload.error || `${response.status} ${response.statusText}`), { payload, status: response.status });
      return payload;
    });
    const state = { context: null, images: [], failures: [], selected: null, refs: [], loading: false, error: "", job: null, poll: null, targetOpen: false, drafts: {}, prompt: "", epoch: 0 };
    const context = () => callbacks.getContext ? (callbacks.getContext() || {}) : {};
    const slug = () => context().slug || "";
    const node = (tag, attrs, ...children) => {
      const el = document.createElement(tag);
      Object.entries(attrs || {}).forEach(([key, value]) => {
        if (key === "text") el.textContent = value;
        else if (key === "class") el.className = value;
        else if (key === "checked" || key === "disabled" || key === "hidden") el[key] = !!value;
        else if (key === "value") el.value = value;
        else el.setAttribute(key, value);
      });
      children.flat().filter(Boolean).forEach(child => el.append(child.nodeType ? child : document.createTextNode(String(child))));
      return el;
    };
    const button = (label, action, attrs = {}) => { const el = node("button", { type: "button", ...attrs, text: label }); el.addEventListener("click", action); return el; };
    function dimensions(format, size) {
      const ratio = FORMATS[format] || FORMATS["3:2"]; const long = Number(size) || 768;
      const short = Math.round(long * Math.min(ratio[0], ratio[1]) / Math.max(ratio[0], ratio[1]) / 64) * 64;
      return ratio[0] >= ratio[1] ? { width: long, height: short } : { width: short, height: long };
    }
    function currentContext() { state.context = context(); return state.context; }
    function imageRef(value) { const ref = value?.ref && typeof value.ref === "object" ? value.ref : value; return ref || {}; }
    function galleryImages(values) { return (Array.isArray(values) ? values : []).filter(image => { const status = image.status; return (!status || status === "done" || status === "complete" || image.ready) && !!localUrl(image); }); }
    function saveDraft() { if (state.projectSlug) state.drafts[state.projectSlug] = { prompt: state.prompt, refs: state.refs.slice(), format: state.format, size: state.size, seed: state.seed, inheritStyle: state.inheritStyle }; }
    function refsForSend() { return state.refs.map(value => { const ref = imageRef(value); return ({ id: ref.id, path: ref.path, hash: ref.hash, label: ref.label }); }).filter(ref => ref.path || ref.id); }
    function setStatus(text, error) { const el = host.querySelector?.(".image-workspace-status"); if (el) { el.replaceChildren(node("span", { class: error ? "image-error" : "image-job-phase", text })); } }
    function render(keepForm = false) {
      const focusedLabel = document.activeElement?.getAttribute?.("aria-label");
      const previousResults = keepForm && host.querySelector?.(".image-workspace-results");
      const form = node("form", { class: "image-workspace-form", "aria-labelledby": "image-workspace-title" });
      const title = node("h2", { id: "image-workspace-title", class: "image-workspace-title", text: "Bilder" });
      const note = node("p", { class: "image-workspace-note", text: "Lag et bilde direkte, og bruk resultatet i filmen når du er klar." });
      const prompt = node("textarea", { class: "image-workspace-prompt", rows: 5, required: true, placeholder: "Beskriv bildet du vil lage…", "aria-label": "Bildebeskrivelse" });
      prompt.value = state.prompt || ""; prompt.addEventListener("input", () => { state.prompt = prompt.value; if (state.projectSlug) state.drafts[state.projectSlug] = { ...(state.drafts[state.projectSlug] || {}), prompt: state.prompt, refs: state.refs.slice() }; });
      const refs = node("div", { class: "image-workspace-refs" }, node("div", { class: "image-workspace-label", text: "Referanser" }));
      state.refs.forEach((ref, index) => refs.append(node("span", { class: "image-ref-chip" }, localUrl(ref) ? node("img", { src: localUrl(ref), alt: "", class: "image-ref-thumb" }) : null, button(ref.label || ref.path || `Referanse ${index + 1}`, () => pickReference(index), { class: "image-ref-open" }), button("×", () => { state.refs.splice(index, 1); render(); }, { class: "image-ref-remove", "aria-label": "Fjern referanse" }))));
      refs.append(button("+ Legg til referanse", () => pickReference(), { class: "btn btn-ghost btn-sm" }));
      const format = node("select", { class: "image-workspace-select", "aria-label": "Bildeformat" });
      Object.keys(FORMATS).forEach(value => format.append(node("option", { value, text: value }))); format.value = state.format || "3:2"; format.addEventListener("change", () => { state.format = format.value; saveDraft(); });
      const size = node("select", { class: "image-workspace-select", "aria-label": "Lengste side" });
      SIZES.forEach(value => size.append(node("option", { value, text: `${value} px` }))); size.value = state.size || "768"; size.addEventListener("change", () => { state.size = Number(size.value); saveDraft(); });
      const seed = node("input", { class: "image-workspace-seed", type: "number", min: 0, step: 1, placeholder: "Tilfeldig", "aria-label": "Seed" }); seed.value = state.seed == null ? "" : state.seed; seed.addEventListener("input", () => { state.seed = seed.value === "" ? null : Math.max(0, Number(seed.value)); saveDraft(); });
      const inherit = node("label", { class: "image-workspace-check" }, node("input", { type: "checkbox", checked: state.inheritStyle !== false }), node("span", { text: "Arv prosjektets stil" })); inherit.firstChild.addEventListener("change", () => { state.inheritStyle = inherit.firstChild.checked; saveDraft(); });
      const geometry = node("p", { class: "image-workspace-note" });
      const paintGeometry = () => { const d = dimensions(format.value,Number(size.value)); geometry.textContent = `${d.width} × ${d.height} px · Qwen Image · lokalt`; };
      format.addEventListener("change",paintGeometry); size.addEventListener("change",paintGeometry); paintGeometry();
      const controls = node("div", { class: "image-workspace-controls" }, node("label", {}, node("span", { text: "Format" }), format), node("label", {}, node("span", { text: "Størrelse" }), size));
      const style = context().board?.renderStyle || "";
      const styleNote = node("p", { class: "image-workspace-note", text: style ? `Prosjektstil: ${style}` : "Prosjektet har ingen ekstra stilbeskrivelse." });
      const advanced = node("details", { class: "image-workspace-details" }, node("summary", { text:"Detaljer og prosjektstil" }), node("label", { class:"image-workspace-label" }, node("span", {text:"Seed (tomt felt gir en ny tilfeldig verdi)"}),seed),styleNote);
      const status = node("div", { class: "image-workspace-status", role: "status", "aria-live": "polite" });
      const submit = button(state.job ? "Lager bilde…" : "Lag bilde", () => form.requestSubmit(), { class: "btn btn-primary image-workspace-submit", disabled: !!state.job });
      const stop = button("Stopp", stopJob, { class: "btn btn-danger", disabled: !state.job });
      form.addEventListener("submit", event => { event.preventDefault(); generate({ prompt: prompt.value, format: format.value, size: Number(size.value), seed: seed.value === "" ? null : Number(seed.value), inheritStyle: inherit.firstChild.checked }); });
      form.append(title, note, node("label", { class: "image-workspace-label" }, node("span", { text: "Beskrivelse" }), prompt), refs, controls, geometry, inherit, advanced, node("div", { class: "image-workspace-actions" }, submit, stop), status);
      if (state.error) status.append(node("span", { class: "image-error", text: state.error }));
      if (state.job) status.append(node("span", { class: "image-job-phase", text: state.job.phase || "I kø…" }));
      const results = node("section", { class: "image-workspace-results", "aria-labelledby": "image-results-title" }, node("div", { class: "image-workspace-results-head" }, node("h3", { id: "image-results-title", text: "Resultater" }), node("span", { class: "image-workspace-count", text: `${state.images.length}` })));
      for(const failure of state.failures)results.append(node("p",{class:"image-error",text:`${failure.status==="failed"?"Bildejobben feilet":"Bildejobben ble avbrutt"}: ${failure.error || failure.prompt || "Prøv en ny kjøring."}`}));
      const grid = node("div", { class: "image-gallery" });
      state.images.forEach(image => {
        const url = localUrl(image); const card = node("article", { class: `image-result-card${state.selected && state.selected.id === image.id ? " is-selected" : ""}`, role: "button", tabindex: "0" });
        const img = url ? node("img", { src: url, alt: image.prompt || "Generert bilde", loading: "lazy" }) : node("div", { class: "image-result-placeholder", text: "Bilde mangler" });
        const label=image.label || (Number.isFinite(image.createdAt)?new Date(image.createdAt*1000).toLocaleString("nb-NO",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"}):"Bilde");
        card.append(img, node("div", { class: "image-result-meta" }, node("strong", { text: label }), node("span", { text: image.width&&image.height?`${image.width}×${image.height}`:"" })));
        const choose = () => { state.selected = image; render(); }; card.addEventListener("click", choose); card.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(); } }); grid.append(card);
      });
      if (state.selected) results.append(detail(state.selected));
      if (!state.images.length) results.append(node("p", { class: "image-empty-state", text: "Ingen bilder ennå. Skriv en beskrivelse eller legg til en referanse for å komme i gang." }));
      results.append(grid);
      if (previousResults) { previousResults.replaceWith(results); updateControls(); return; }
      host.replaceChildren(node("div", { class: "image-workspace", "data-busy": !!state.job }, form, results));
      if (focusedLabel) {
        const focusTarget = host.querySelector?.(`[aria-label="${focusedLabel.replace(/"/g, "\\\"")}"]`);
        focusTarget?.focus?.();
      }
    }
    async function pickReference(replace) {
      const source=slug(), epoch=state.epoch, original=state.refs[replace];
      try {
        if (replace == null && state.refs.length >= 10) throw new Error("Velg maksimalt 10 referanser.");
        const picked=await callbacks.pickReference?.({replace,kind:"image"});
        if (!picked || destroyed || (source && (source !== slug() || epoch !== state.epoch))) return;
        if (replace != null && state.refs[replace] !== original) return;
        const choices=Array.isArray(picked)?picked:[picked];
        if (replace == null) state.refs.push(...choices.slice(0,10-state.refs.length));
        else state.refs[replace]=choices[0];
        saveDraft();if(active)render();
      } catch(error) { if(!source||source===slug()){state.error=error.message;updateControls();} }
    }
    function addReference(ref) {
      if(!ref)return;
      if(state.refs.length>=10){state.error="Velg maksimalt 10 referanser.";updateControls();return;}
      state.refs.push(ref);saveDraft();if(active)render();
    }
    function reuseImage(image) {
      state.prompt=image.prompt || state.prompt;
      if(Array.isArray(image.references))state.refs=image.references.slice();
      if(image.width>0&&image.height>0){
        const ratio=image.width/image.height;
        state.format=Object.keys(FORMATS).reduce((best,key)=>Math.abs(FORMATS[key][0]/FORMATS[key][1]-ratio)<Math.abs(FORMATS[best][0]/FORMATS[best][1]-ratio)?key:best,"3:2");
        state.size=Math.max(image.width,image.height)>=1536?1536:768;
      }
      if(typeof image.inheritStyle==="boolean")state.inheritStyle=image.inheritStyle;
      state.seed=null;saveDraft();render();host.querySelector(".image-workspace-prompt")?.focus();
    }
    function detail(image) {
      const box = node("div", { class: "image-result-detail" });
      const url = localUrl(image); if (url) box.append(node("img", { class: "image-result-preview", src: url, alt: image.prompt || "Valgt bilde" }));
      const ref = imageRef(image); box.append(node("div", { class: "image-result-copy" }, node("h3", { text: "Valgt bilde" }), node("p", { text: image.prompt || "" }), node("div", { class: "image-detail-actions" }, url ? button("Last ned", () => { const link = document.createElement("a"); link.href = url; link.download = image.name || "storyboard-image.png"; link.click(); }) : null, button("Bruk som referanse", () => addReference({ path: ref.path, id: ref.id, hash: ref.hash, label: ref.label || image.label || "Valgt bilde" })), button("Bruk i film", () => { state.targetOpen = true; render(); }), button("Lag ny variant", () => reuseImage(image)))), state.targetOpen ? targetPicker(image) : null);
      const recipe=node("details", {class:"image-workspace-details"},node("summary",{text:"Oppskrift og faktisk beskrivelse"}));
      if(callbacks.deleteImage && ref.path){
        const remove=button("Slett bilde…",async()=>{remove.disabled=true;try{await callbacks.deleteImage({...ref,label:image.label||ref.label});}catch(error){state.error=error.message;updateControls();}finally{remove.disabled=!!context().busy||!!state.job;}},{class:"btn btn-danger btn-sm",disabled:!!context().busy||!!state.job});
        box.querySelector(".image-detail-actions")?.append(remove);
      }
      recipe.append(node("p",{text:image.recipeAvailable===false?"Eldre bilde: full oppskrift ble ikke lagret.":`${image.width||"?"} × ${image.height||"?"} px · seed ${image.seed ?? "ukjent"}`}),node("p",{text:image.resolvedPrompt||image.prompt||""}));
      box.querySelector(".image-result-copy")?.append(recipe);
      return box;
    }
    function targetPicker(image) {
      const ctx = state.context || context(); const shots = ctx.board?.shots || []; const selectedShot = ctx.selectedShot || shots[0];
      const select = node("select", { class: "image-target-select", "aria-label": "Målklipp" }); shots.forEach(shot => select.append(node("option", { value: shot.id, text: shot.title || shot.name || shot.id }))); if (selectedShot) select.value = selectedShot.id;
      const kind = node("select", { class: "image-target-select", "aria-label": "Bruk bildet som" });
      [["start", "Åpningsbilde"], ["end", "Sluttbilde"], ["new", "Nytt klipp"]].forEach(([value, label]) => kind.append(node("option", { value, text: label })));
      const target = node("div", { class: "image-target-picker" }, node("strong", { text: "Bruk i film" }),node("p",{class:"image-workspace-note",text:`Prosjekt: ${ctx.board?.name || ctx.slug || "Mine bilder"}`}));
      if (!shots.length && !callbacks.ensureProject) target.append(node("p", { text: "Ingen klipp er valgt ennå. Opprett et prosjekt eller et nytt klipp først." }));
      else { kind.value = selectedShot ? "start" : "new"; target.append(kind, select); const chosen = () => shots.find(shot => shot.id === select.value); const chain = () => { const shot = chosen(); return !!(shot?.startRef?.kind === "chain" || shot?.continuationOf || shot?.chainFrom || shot?.previousShotId); }; const warning = node("p", { class: "image-chain-warning", hidden: true, text: "Dette klippet fortsetter en kjede. Å bruke bildet som åpningsbilde kan bryte videreføringen." }); const error = node("p", { class: "image-error", hidden: true }); target.append(warning, error); kind.addEventListener("change", () => { warning.hidden = kind.value !== "start" || !chain(); }); select.addEventListener("change", () => { warning.hidden = kind.value !== "start" || !chain(); }); const apply = button("Bekreft bruk", async () => { apply.disabled = true; error.hidden = true; try { let project = ctx; if (kind.value === "new" && callbacks.ensureProject) project = await callbacks.ensureProject(); if (project?.slug && project.slug !== slug()) { state.context = context(); return; } const breakChain = kind.value === "start" && chain(); const confirmFn = root.confirm || (() => true); if (breakChain && !confirmFn("Dette bryter klippets videreføring. Fortsett?")) return; callbacks.onUseImage && await callbacks.onUseImage(image, { kind: kind.value, shotId: kind.value === "new" ? null : select.value, projectSlug: project?.slug || slug(), breakChain }); } catch (cause) { error.textContent = cause.message || "Kunne ikke bruke bildet."; error.hidden = false; } finally { apply.disabled = false; } }, { class: "btn btn-primary btn-sm" }); target.append(apply); }
      return target;
    }
    let active = false, destroyed = false, lastSlug = "", pollSession = null;
    let requestSerial = 0, generationSerial = 0;
    let readFlight = Promise.resolve();
    const defaults = () => ({prompt:"", refs:[], format:"3:2", size:768, seed:null, inheritStyle:true});
    function stopPolling() {
      if (pollSession) pollSession.closed = true;
      pollSession = null;
      clearTimeout(state.poll);
      state.poll = null;
    }
    // Even a closing/reopening panel has at most one pending status read. Each
    // poll owns its own session; an old finally block cannot unlock a new one.
    function readStatus(url) {
      const read = readFlight.catch(() => {}).then(() => request("GET", url));
      readFlight = read;
      return read;
    }
    function switchProject() {
      const next = slug();
      if (state.projectSlug === next) return false;
      const previous = state.projectSlug;
      const unbound = previous === undefined ? state.prompt : "";
      saveDraft();
      stopPolling(); state.epoch++; requestSerial++;
      state.projectSlug = next;
      const draft = Object.prototype.hasOwnProperty.call(state.drafts, next) ? state.drafts[next] : defaults();
      Object.assign(state, draft, {refs:(draft.refs || []).slice(),images:[],failures:[],selected:null,job:null,error:"",targetOpen:false});
      if (unbound && !draft.prompt) state.prompt = unbound;
      return true;
    }
    function mergeHistory(payload) {
      const values = Array.isArray(payload) ? payload : payload.images || payload.results;
      if (!Array.isArray(values)) return;
      state.images = galleryImages(values).slice().sort((a,b)=>(Number(b.createdAt)||0)-(Number(a.createdAt)||0));
      state.failures=values.filter(i=>["failed","interrupted","cancelled"].includes(i.status)).slice().sort((a,b)=>(Number(b.createdAt)||0)-(Number(a.createdAt)||0)).slice(0,3);
      if (state.selected) state.selected = state.images.find(i => i.id === state.selected.id) || null;
      if (!state.selected && state.images.length) state.selected = state.images[0];
    }
    function updateControls() {
      const submit = host.querySelector?.(".image-workspace-submit");
      const stop = host.querySelector?.(".image-workspace-actions .btn-danger");
      if (submit) { submit.disabled = !!state.job || state.loading; submit.textContent = state.job || state.loading ? "Lager bilde…" : "Lag bilde"; }
      if (stop) stop.disabled = !state.job || !!state.job.pending || !!state.job.cancelRequested;
      setStatus(state.error || state.job?.phase || "", !!state.error);
    }
    async function refresh() {
      const switched = switchProject();
      const current = slug(), serial = ++requestSerial, epoch = state.epoch;
      state.context = context();
      if (!current) { if (active) render(); return []; }
      try {
        const payload = await readStatus(`/api/images?slug=${encodeURIComponent(current)}`);
        if (destroyed || serial !== requestSerial || epoch !== state.epoch || current !== slug()) return state.images;
        mergeHistory(payload); state.error = "";
        const job = payload.job;
        state.job = job?.busy ? job : null;
        if (job && !job.busy && job.error) state.error = job.error;
        if (active) render(!switched);
        if (state.job && active) void pollJob(current, job.id || job.jobId);
        return state.images;
      } catch (error) {
        if (serial === requestSerial && epoch === state.epoch && current === slug()) { state.error = error.message; updateControls(); }
        return state.images;
      }
    }
    async function generate(input) {
      if (state.loading || state.job || !input?.prompt?.trim()) return;
      if (state.refs.length > 10) { state.error = "Velg maksimalt 10 referanser."; updateControls(); return; }
      const source = slug(), serial = ++generationSerial;
      const references = refsForSend();
      state.loading = true; state.error = ""; updateControls();
      try {
        const project = callbacks.ensureProject ? await callbacks.ensureProject({slug:source,save:true}) : context();
        const current = project?.slug || source || slug();
        if (destroyed || serial !== generationSerial || current !== slug() || (source && source !== current)) return;
        const payload = await request("POST", "/api/images", {slug:current,prompt:input.prompt.trim(),references,
          format:input.format,...dimensions(input.format,input.size),seed:input.seed == null ? undefined : input.seed,
          inheritStyle:input.inheritStyle !== false});
        if (destroyed || serial !== generationSerial || current !== slug()) return;
        state.job = payload.job || null;
        state.loading = false; updateControls();
        if (state.job && active) await pollJob(current,state.job.id || state.job.jobId);
        else if (active) await refresh();
      } catch (error) {
        if (serial === generationSerial && (!source || source === slug())) { state.error = error.message; state.job = null; }
      } finally {
        if (serial === generationSerial) { state.loading = false; updateControls(); }
      }
    }
    async function pollJob(current, jobId) {
      if (!active || !jobId) return;
      if (pollSession && !pollSession.closed && pollSession.slug === current && pollSession.jobId === jobId) return;
      stopPolling();
      const session = {slug:current,jobId,epoch:state.epoch,closed:false};
      pollSession = session;
      const valid = () => !destroyed && active && !session.closed && pollSession === session && session.epoch === state.epoch && slug() === current;
      const tick = async () => {
        if (!valid()) return;
        try {
          const payload = await readStatus(`/api/images?slug=${encodeURIComponent(current)}&jobId=${encodeURIComponent(jobId)}`);
          if (!valid()) return;
          mergeHistory(payload);
          const job = payload.job;
          if (payload.stale || !job || (job.id || job.jobId) !== jobId) {
            state.job = null; state.loading = false;
            state.error = payload.stale ? "Den tidligere bildejobben er avsluttet eller erstattet. Oppdater for å se ny jobbstatus." : "";
            stopPolling(); render(true); return;
          }
          state.job = job; state.error = job.error || "";
          if (job.busy === false || (!Object.prototype.hasOwnProperty.call(job,"busy") && ["done","failed","interrupted","cancelled","complete"].includes(job.status))) {
            const image = job.result || payload.image || payload.result || state.images.find(i=>i.id===job.recordId);
            if (image && localUrl(image)) {
              if (!state.images.some(i=>i.id===image.id)) state.images.unshift(image);
              state.selected = image;
            }
            state.job = null; state.loading = false; state.error = job.error || "";
            stopPolling(); render(true); return;
          }
          updateControls();
        } catch (error) { if (valid()) { state.error = error.message; updateControls(); } }
        if (valid()) state.poll = setTimeout(tick,1500);
      };
      await tick();
    }
    async function stopJob() {
      const job = state.job, current = slug();
      if (!job || job.pending || job.cancelRequested) return;
      const id = job.id || job.jobId;
      state.job = {...job,cancelRequested:true,phase:"Stopper og rydder opp…"};
      updateControls();
      try { await request("POST","/api/images/stop",{slug:current,jobId:id}); }
      catch (error) {
        if (slug()===current && (state.job?.id || state.job?.jobId)===id) {
          state.job = {...state.job,cancelRequested:false};state.error=error.message;updateControls();
        }
      }
    }
    const controller = {
      open() {
        if (destroyed) return Promise.resolve([]);
        active=true;state.epoch++;stopPolling();host.hidden=false;
        switchProject();currentContext();lastSlug=slug();render();return refresh();
      },
      close() { saveDraft();active=false;state.epoch++;stopPolling();host.hidden=true; },
      destroy() { saveDraft();destroyed=true;active=false;state.epoch++;stopPolling();host.replaceChildren(); },
      refresh,
      sync() {
        if (!active || destroyed) return Promise.resolve(state.images);
        state.context=context();
        if (slug()!==lastSlug) { lastSlug=slug();return refresh(); }
        if (state.job && !pollSession) return pollJob(slug(),state.job.id || state.job.jobId);
        return Promise.resolve(state.images);
      },
      generate,stop:stopJob,
      addReference,
      removeImage(path) {
        requestSerial++;
        const matches=value=>imageRef(value)?.path===path;
        state.images=state.images.filter(image=>!matches(image));
        state.refs=state.refs.filter(ref=>!matches(ref));
        for(const draft of Object.values(state.drafts))draft.refs=(draft.refs||[]).filter(ref=>!matches(ref));
        if(matches(state.selected))state.selected=state.images[0]||null;
        state.targetOpen=false;saveDraft();if(active)render();
      },
      setPrompt(prompt) { switchProject();state.prompt=prompt || "";if(active)render(); },
      selectImage(image,options={}) {state.selected=image;state.targetOpen=!!options.useInFilm;if(active)render();},
      getState:()=>state,
    };
    return controller;
  }
  root.StoryboardImages = { create, localUrl };
})(typeof window !== "undefined" ? window : globalThis);
