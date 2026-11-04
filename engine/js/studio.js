/* Project navigation and read-only film overview. The existing editor and AD
   retain ownership of edits, chat proposals, saves and production actions. */
"use strict";
(function (root) {
  const labels = {draft:"Utkast", queued:"Venter", running:"Lager klipp", done:"Generert",
    published:"Generert", checkpoint:"Lagret del", failed:"Feilet", blocked:"Blokkert",
    review:"Må vurderes", interrupted:"Avbrutt"};
  const localMedia = value => typeof value === "string" && /^\/media\/[^?#]+(?:\?[^#]*)?$/.test(value) &&
    !value.includes("..") && !/[\\\x00-\x1f]/.test(value);
  function groupScenes(shots) {
    const groups = [];
    let previous = 1;
    for (const [index, shot] of (shots || []).entries()) {
      const legacy = /^\s*scene\s*(\d+)\b/i.exec(shot.title || "");
      const number = Number.isInteger(shot.directorScene) && shot.directorScene > 0
        ? shot.directorScene : legacy ? Number(legacy[1]) : previous;
      let group = groups[groups.length - 1];
      if (!group || group.number !== number) {
        group = {key:`${number}:${index}`, number, shots:[], seconds:0};
        groups.push(group);
      }
      group.shots.push(shot);
      group.seconds += Number.isFinite(shot.frames) ? shot.frames / 24 : 0;
      previous = number;
    }
    return groups;
  }
  function productionAction(context) {
    if (!context?.project?.canProduce || !context.anchorRevision) {
      throw new Error(context?.project?.productionError || "Denne planen kan ikke startes nå. Bruk filmstatus for å fortsette en eksisterende produksjon.");
    }
    return {tool:"render_storyboard", slug:context.project.slug, revision:context.anchorRevision,
      imageEngine:"qwen-image-2.1-turbo", continuationAnchor:false};
  }
  function scenePage(group,pages) {
    const index=Math.max(0,Math.min(pages.get(group.key)||0,Math.ceil(group.shots.length/24)-1));
    return {index,shots:group.shots.slice(index*24,index*24+24)};
  }
  function create(host, api) {
    if (!host) return null;
    const node = (tag, cls, text) => {const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
    const button = (text, fn, cls="btn btn-sm") => {const b=node("button",cls,text);b.type="button";b.addEventListener("click",fn);return b;};
    let mode="film", view="overview", scene="all", lastSlug=null, signature="", filmContext=null;
    let contextSerial=0, librarySerial=0, requesting=false, destroyed=false, lastContextAt=0, jobSignature="";
    const pages=new Map();
    try {mode=localStorage.getItem("sb.studio.mode")||mode;} catch {}
    if (!["film","images","library"].includes(mode)) mode="film";
    const toolbar=node("div","studio-toolbar");
    const filmTabs=node("nav","studio-film-tabs");filmTabs.setAttribute("aria-label","Filmvisning");
    const filmViews=[["overview","Sceneoversikt"],["shot","Valgt klipp"],["project","Karakterer og stil"],["final","Ferdig film"]];
    filmViews.forEach(([key,label])=>{const b=button(label,()=>setView(key));b.dataset.view=key;filmTabs.appendChild(b);});
    const make=button("Lag film fra denne planen",reviewProduction,"btn btn-primary btn-sm");
    const animatic=button("Se bildeplan med timing",openAnimatic);
    toolbar.append(filmTabs,animatic,make);
    const overview=node("section","studio-overview");overview.setAttribute("aria-label","Scener og klipp");
    const edit=node("section","studio-edit-layout");
    const rail=document.querySelector(".rail"),main=document.querySelector(".main");
    if(rail)edit.appendChild(rail);if(main)edit.appendChild(main);
    const images=node("section","studio-images");images.id="imageWorkspace";images.hidden=true;
    const library=node("section","studio-library");library.hidden=true;library.setAttribute("aria-label","Bildebibliotek");
    const final=node("section","studio-final");final.hidden=true;
    const watch=node("section","studio-final");watch.hidden=true;watch.setAttribute("aria-label","Se generert klipp");
    const review=node("section","studio-production-review");review.hidden=true;review.setAttribute("aria-label","Gjennomgå filmproduksjon");
    const message=node("p","studio-error");message.setAttribute("role","alert");message.hidden=true;
    host.append(toolbar,message,overview,edit,images,library,final,watch,review);
    const job=document.getElementById("studioJob");
    const nav=document.getElementById("studioNav");
    nav?.querySelectorAll("[data-workspace]").forEach(b=>b.addEventListener("click",()=>setMode(b.dataset.workspace)));
    document.getElementById("studioDirector")?.addEventListener("click",()=>api.toggleDirector());
    document.getElementById("studioProduce")?.addEventListener("click",reviewProduction);
    const imageController=api.createImages?.(images,{openLibrary:()=>setMode("library")});
    function problem(error){message.textContent=error.message||String(error);message.hidden=false;}
    function setMode(next){
      mode=next;review.hidden=true;message.hidden=true;
      try{localStorage.setItem("sb.studio.mode",mode);}catch{}
      sync(true);if(mode==="library")loadLibrary();
      if(mode==="images")imageController?.open?.();else imageController?.close?.();
    }
    function owned(){const c=api.getContext();return c.status?.film?.busy&&c.status.film.slug===c.slug;}
    function setView(next){view=next==="shot"&&owned()?"watch":next;review.hidden=true;sync(true);if(next==="final")paintFinal();}
    function select(shotId,open=true){api.selectShot(shotId);if(open)view=owned()?"watch":"shot";sync(true);}
    function paintOverview(ctx){
      overview.replaceChildren();
      const groups=groupScenes(ctx.board?.shots);
      if(groups.length>12&&scene==="all")scene=groups[0].key;
      if(!groups.length){
        const cast=ctx.board?.characters||[];
        overview.append(node("h2",null,"Fra karakter til ferdig film"),node("p","field-note","1. Legg til karakterer og karakterkort. 2. Beskriv filmideen og trykk Send hos regissøren. Bruk klippforslaget når du er fornøyd. 3. Gjennomgå planen og start produksjon."),
          node("p","field-note",cast.length ? `${cast.length} karakterer i prosjektet · ${cast.filter(character=>character.image).length} med bilde eller karakterkort` : "Ingen karakterer ennå. Du kan også la regissøren foreslå dem."),
          button("1. Karakterer og stil",()=>setView("project")),button("2. Beskriv filmidé",()=>api.draftFilmPlan(),"btn btn-primary"),
          button("Lag bilder",()=>setMode("images")),button("Legg til klipp manuelt",()=>api.addShot()));return;
      }
      const summary=node("div","studio-overview-heading");summary.append(node("h2",null,ctx.board.name||"Filmplan"),
        node("span","field-note",`${groups.length} scener · ${ctx.board.shots.length} klipp · ${groups.reduce((n,g)=>n+g.seconds,0).toFixed(1)} s planlagt`));
      overview.appendChild(summary);
      const layout=node("div","studio-scene-layout");const scenes=node("nav","studio-scenes");scenes.setAttribute("aria-label","Velg scene");
      const all=button("Alle scener",()=>{scene="all";sync(true);});all.setAttribute("aria-pressed",String(scene==="all"));scenes.appendChild(all);
      for(const g of groups){const b=button(`Scene ${g.number} · ${g.shots.length} klipp`,()=>{scene=g.key;sync(true);});b.setAttribute("aria-pressed",String(scene===g.key));scenes.appendChild(b);}
      scenes.appendChild(button("+ Ny scene",()=>api.addScene()));
      const panels=node("div","studio-scene-panels");
      for(const g of groups.filter(g=>scene==="all"||g.key===scene)){
        const section=node("section","studio-scene");
        const done=g.shots.filter(s=>s.status==="done").length;
        section.append(node("h3",null,`Scene ${g.number}`),node("p","field-note",`${g.seconds.toFixed(1)} s · ${done}/${g.shots.length} klipp generert`));
        const grid=node("div","studio-shot-grid");
        const currentPage=scenePage(g,pages);
        for(const shot of currentPage.shots){
          const card=button("",()=>select(shot.id),"studio-shot-card");card.setAttribute("aria-label",`${shot.title||"Klipp"} · ${labels[shot.status]||"Utkast"}`);
          card.setAttribute("aria-pressed",String(shot.id===ctx.selectedShot?.id));
          const pic=node("img");const url=shot.thumb||shot.stills?.start?.url||shot.startRef?.url;
          pic.src=localMedia(url)?url:"assets/shot-placeholder.png";pic.alt="";pic.loading="lazy";
          const caption=node("div","studio-shot-caption");caption.append(node("strong",null,shot.title||"Uten navn"),node("span",null,`${((shot.frames||97)/24).toFixed(1)} s · ${labels[shot.status]||"Utkast"}`));
          const chain=shot.startRef?.kind==="chain";
          caption.append(node("span","field-note",chain?"↳ Fortsetter et tidligere klipp":"Ny åpning / nytt utsnitt"));
          if(shot.locked)caption.append(node("span","field-note","Låst"));
          const recorded=filmContext?.project?.clips?.find(c=>c.shotId===shot.id);
          if(recorded?.status==="checkpoint")caption.append(node("span","field-note","Delklipp lagret · kontrolleres ved fortsettelse"));
          card.append(pic,caption);grid.appendChild(card);
        }
        section.appendChild(grid);
        if(g.shots.length>24){const pager=node("div","studio-review-actions");const prev=button("Forrige klipp",()=>{pages.set(g.key,Math.max(0,currentPage.index-1));sync(true);});prev.disabled=currentPage.index===0;
          const next=button("Neste klipp",()=>{pages.set(g.key,currentPage.index+1);sync(true);});next.disabled=(currentPage.index+1)*24>=g.shots.length;pager.append(prev,node("span","field-note",`${currentPage.index*24+1}–${Math.min((currentPage.index+1)*24,g.shots.length)} av ${g.shots.length}`),next);section.appendChild(pager);}
        panels.appendChild(section);
      }
      panels.appendChild(button("+ Legg til klipp",()=>api.addShot()));
      layout.append(scenes,panels);overview.appendChild(layout);
      const sequence=node("nav","studio-sequence");sequence.setAttribute("aria-label","Filmens rekkefølge");
      for(const g of groups.filter(g=>scene==="all"||scene===g.key)){const group=node("div","studio-sequence-scene");group.appendChild(node("span","field-note",`Scene ${g.number}`));
        for(const s of scenePage(g,pages).shots){const b=button(`${ctx.board.shots.indexOf(s)+1} · ${((s.frames||97)/24).toFixed(1)} s`,()=>select(s.id));b.setAttribute("aria-label",s.title||"Åpne klipp");group.appendChild(b);}sequence.appendChild(group);}
      overview.appendChild(sequence);
    }
    function paintJob(ctx){
      if(!job)return;
      const f=ctx.status?.film,st=ctx.status?.stills;
      const imageJob=ctx.status?.images?.job;
      const imageError=!ctx.busy&&imageJob?.slug===ctx.slug&&imageJob.error;
      const active=ctx.status?.busy||st?.busy||f?.busy;
      const fLabel={plan:"Planlegger",images:"Lager bilder",dialogue:"Lager dialogopptak",director:"Lager video",video:"Lager video",review:"Kontrollerer",assemble:"Setter sammen"};
      const text=f?.busy?`${api.boardName?.(f.slug)||f.slug} · ${fLabel[f.phase]||f.phase} · ${f.completed||0}/${f.total||0} klipp lagret${f.detail?" · "+f.detail:""}`:
        st?.busy?`Lager bilde · ${st.phase||"Forbereder"}`:ctx.status?.busy?"Klippgenerering pågår":
        f?.error&&f.slug===ctx.slug?`Filmjobben stoppet: ${f.error}`:imageError?`Bildejobben stoppet: ${imageError} Tidligere resultater er beholdt.`:"Klar · endringer lagres automatisk";
      const jobKey=JSON.stringify([text,active,f?.jobId,imageJob?.id,ctx.slug,ctx.busy,!!ctx.board?.filmAutomation]);
      const reel=ctx.board?.filmAutomation?.animaticUrl;
      animatic.disabled=!localMedia(reel);
      make.disabled=(!ctx.board?.shots?.length&&!ctx.board?.filmAutomation)||!!ctx.busy;
      make.textContent=ctx.board?.filmAutomation?"Vis produksjon / fortsett":"Lag film fra denne planen";
      if(jobKey===jobSignature)return;jobSignature=jobKey;job.replaceChildren();job.appendChild(node("span","studio-job-text",text));
      if(f?.busy||(f?.error&&f.slug===ctx.slug)||ctx.board?.filmAutomation)job.appendChild(button("Filmstatus",()=>api.showProgress()));
      if(imageError)job.appendChild(button("Se bildejobben",()=>setMode("images")));
      if(active&&f?.busy)job.appendChild(button("Stopp film",()=>api.stopFilm()));
      if(ctx.status?.film?.busy&&ctx.status.film.slug===ctx.slug){
        const locked=node("span","studio-lock-note","Produksjonen eier prosjektet. Resultater kan vises; redigering åpnes etter stopp og opprydding.");job.appendChild(locked);
      }
    }
    function sync(force=false){
      if(destroyed)return;const ctx=api.getContext();
      const slug=ctx.slug;
      if(slug!==lastSlug){lastSlug=slug;filmContext=null;scene="all";pages.clear();signature="";contextSerial++;review.hidden=true;requestContext();if(mode==="library")loadLibrary();}
      toolbar.hidden=mode!=="film";images.hidden=mode!=="images";library.hidden=mode!=="library";
      edit.hidden=mode!=="film"||!["shot","project"].includes(view)||!review.hidden;
      overview.hidden=mode!=="film"||view!=="overview"||!review.hidden;
      final.hidden=mode!=="film"||view!=="final"||!review.hidden;
      watch.hidden=mode!=="film"||view!=="watch"||!review.hidden;
      host.dataset.filmView=view;
      nav?.querySelectorAll("[data-workspace]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.workspace===mode)));
      filmTabs.querySelectorAll("[data-view]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.view===view||(view==="watch"&&b.dataset.view==="shot"))));
      const sig=JSON.stringify([slug,scene,ctx.selectedShot?.id,ctx.board?.shots?.map(s=>[s.id,s.directorScene,s.title,s.frames,s.status,s.thumb,s.startRef,s.stills?.start?.url,s.locked]),filmContext?.project?.clips]);
      if(force||sig!==signature){signature=sig;paintOverview(ctx);if(view==="final")paintFinal();if(view==="watch")paintWatch(ctx);}
      paintJob(ctx);imageController?.sync?.();
      if(ctx.status?.film?.busy&&Date.now()-lastContextAt>5000)requestContext();
    }
    async function requestContext(){const ctx=api.getContext(),serial=++contextSerial;if(!ctx.slug)return;lastContextAt=Date.now();
      try{const result=await api.filmStatus(ctx.slug);if(serial!==contextSerial||api.getContext().slug!==ctx.slug||destroyed)return;filmContext=result;sync(true);}catch{/* Main controls still expose actionable errors. */}}
    async function reviewProduction(){
      if(requesting)return;
      const ctx=api.getContext();if(ctx.board?.filmAutomation){api.showProgress();return;}
      requesting=true;message.hidden=true;
      try{
        await api.save();const slug=api.getContext().slug;const data=await api.filmStatus(slug);
        if(api.getContext().slug!==slug)throw new Error("Prosjektet ble byttet. Åpne produksjon på nytt.");
        const action=data.project.canProduce?productionAction(data):null;mode="film";review.replaceChildren();review.hidden=false;sync();
        review.append(node("h2",null,"Gjennomgå filmproduksjonen"),node("p",null,data.project.name));
        const p=data.project.plannedFilm||{};
        review.append(node("p",null,`${p.plannedScenes||0} scener · ${p.plannedClips||0} klipp · ${(p.duration||0).toFixed(2)} s`),
          node("p","field-note","1. Lager en bildeplan fra disse klippene, faste steder og karakterkort. 2. Du ser og godkjenner bildene. 3. Første videoscene lages og gjennomgås før resten. Eksisterende medier beholdes."),
          node("p","field-note","Kjøretid: ikke målt for denne planen."));
        for(const [key,label] of [["frameAdjustments","Tilpasser klipplengde"],["seedAdjustments","Normaliserer scene-seed"],["characterAssignments","Knytter karakterer"]]){
          if(p[key]?.length){const d=node("details");d.append(node("summary",null,`${label} · ${p[key].length}`));
            for(const item of p[key])d.appendChild(node("p","field-note",`${item.title||item.shotId}: ${item.before??""}${item.after!=null?" → "+item.after:""}${item.characters?item.characters.join(", "):""}`));review.appendChild(d);}
        }
        for (const warning of p.planWarnings || []) review.appendChild(node("p", "studio-error", warning.message || String(warning)));
        if (!action) {
          review.appendChild(node("p", "studio-error", data.project.productionError || "Planen trenger utfylling før produksjon."));
          review.append(button("Tilbake til planen",()=>{review.hidden=true;sync();}), button("Be regissøren foreslå utfylling",()=>api.repairPlan?.()));
          return;
        }
        const prepare=node("p","field-note","Mangler planen tydelig start, handling, slutt eller felles steder? Be regissøren foreslå de manglende feltene før du lager bilder.");review.appendChild(prepare);
        const error=node("p","studio-error");error.setAttribute("role","alert");error.hidden=true;
        const start=button("Lag bildeplan for gjennomgang",async()=>{
          start.disabled=true;error.hidden=true;
          try{if(api.getContext().slug!==slug)throw new Error("Prosjektet ble byttet. Åpne oppsummeringen på nytt.");await api.runAction({...action,prepareOnly:true});review.hidden=true;sync(true);}
          catch(e){error.textContent=`${e.message} Åpne en ny oppsummering hvis planen er endret.`;error.hidden=false;start.disabled=false;}
        },"btn btn-primary");
        const actions=node("div","studio-review-actions");actions.append(button("Tilbake til planen",()=>{review.hidden=true;sync();}),start);review.append(error,actions);
      }catch(e){problem(e);}finally{requesting=false;}
    }
    function openAnimatic(){const ref=api.getContext().board?.filmAutomation?.animaticUrl;if(localMedia(ref))window.open(ref,"_blank","noopener");}
    function paintWatch(ctx){
      const s=ctx.board?.shots?.find(s=>s.id===ctx.selectedShot?.id);if(!s)return;
      const video=(s.renderedDialogueSource!=="native"&&s.dubUrl)||(s.outputs||[]).find(u=>/\.mp4(?:\?|$)/i.test(u));
      const url=localMedia(video)?video:(s.thumb||s.stills?.start?.url||s.startRef?.url);
      if(watch.dataset.shot===s.id&&watch.dataset.url===url)return;
      watch.dataset.shot=s.id;watch.dataset.url=url||"";watch.replaceChildren(node("h2",null,s.title||"Valgt klipp"));
      if(localMedia(url)){const media=node(video?"video":"img");media.src=url;if(video){media.controls=true;media.preload="metadata";}else media.alt=s.title||"Klippbilde";watch.appendChild(media);}
      else watch.appendChild(node("p","field-note","Klippet er ikke publisert ennå. Fullførte deler kontrolleres ved fortsettelse."));
      watch.appendChild(node("p","field-note","Prosjektet produseres. Her kan du se eksisterende resultater uten å endre produksjonsgrunnlaget."));
    }
    function paintFinal(){
      const ctx=api.getContext(),src=ctx.board?.finalVideo?.url;const old=final.querySelector("video");
      if(old?.getAttribute("src")===src)return;final.replaceChildren(node("h2",null,"Ferdig film"));
      if(localMedia(src)){final.appendChild(node("p","field-note","Teknisk ferdig video. Generert og satt sammen betyr ikke at handling, sammenheng og lyd er visuelt godkjent."));const video=node("video");video.src=src;video.controls=true;video.preload="metadata";final.appendChild(video);const a=node("a","btn","Last ned filmen");a.href=src;a.download="";final.appendChild(a);}
      else final.appendChild(node("p","field-note","Filmen vises her når klippene er satt sammen. Du kan se hvert ferdige klipp fra sceneoversikten."));
    }
    async function loadLibrary(){
      const serial=++librarySerial;const slug=api.getContext().slug;
      library.replaceChildren(node("h2",null,"Bildebibliotek"),node("p","field-note","Laster bilder …"));
      try{
        const result=await api.library();if(serial!==librarySerial||api.getContext().slug!==slug||destroyed)return;
        library.replaceChildren(node("h2",null,"Bildebibliotek"),node("p","field-note","Bilder fra prosjektene dine. Bruk i en annen film kopierer bildet inn i målprosjektet."));
        const grid=node("div","studio-library-grid");
        const items=result.items||result.images||[];
        for(const item of items){if(!localMedia(item.url))continue;const card=node("article","studio-library-item");const img=node("img");img.src=item.url;img.alt=item.label||item.name||"Bibliotekbilde";img.loading="lazy";
          card.append(img,node("strong",null,item.label||item.name||item.path?.split("/").pop()||"Bilde"));
          if(item.uses?.length)card.appendChild(node("p","field-note",item.uses.slice(0,3).join(" · ")));
          card.append(button("Bruk som referanse",async()=>{try{const ref=await api.adopt(item);setMode("images");imageController?.addReference?.(ref);}catch(e){problem(e);}}),
            button("Bruk i film",()=>{api.useImage({ref:item,url:item.url,prompt:"",recipeAvailable:false});}));
          const remove=button("Slett bilde…",async()=>{remove.disabled=true;try{if(await api.deleteImage(item))await loadLibrary();}catch(e){problem(e);}finally{remove.disabled=api.getContext().busy||item.canDelete===false;}},"btn btn-danger btn-sm");
          remove.disabled=!!api.getContext().busy||item.canDelete===false;
          if(item.deleteBlockedReason){remove.title=item.deleteBlockedReason;card.appendChild(node("p","field-note",`Kan ikke slettes: ${item.deleteBlockedReason}`));}
          card.appendChild(remove);grid.appendChild(card);
        }
        library.appendChild(grid);if(!grid.children.length)library.appendChild(node("p","field-note","Ingen bilder ennå. Lag et bilde eller last opp en referanse."));
      }catch(e){if(serial===librarySerial)problem(e);}
    }
    document.querySelector(".app")?.classList.add("studio-ready");
    sync(true);if(mode==="images")imageController?.open?.();if(mode==="library")loadLibrary();
    return {sync,setMode,setView,reviewProduction,requestContext,openShot:id=>select(id),
      director(open){
        document.querySelector(".app")?.classList.toggle("director-open",open);document.getElementById("studioDirector")?.setAttribute("aria-pressed",String(open));
        const overlay=!!window.matchMedia?.("(max-width:1050px)").matches;
        for(const n of [host,nav,job,document.querySelector(".header")])if(n)n.inert=!!open&&overlay;
        const director=document.getElementById("assistantBlade");
        if(director){if(open&&overlay){director.setAttribute("role","dialog");director.setAttribute("aria-modal","true");}else{director.removeAttribute("role");director.removeAttribute("aria-modal");}}
      },
      destroy(){destroyed=true;contextSerial++;librarySerial++;imageController?.destroy?.();}};
  }
  root.StoryboardStudio={create,groupScenes,scenePage,productionAction,localMedia};
})(globalThis);
