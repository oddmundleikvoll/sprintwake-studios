"""Film from a brief: one validated plan, serial images, native LTX scenes.

The language model never chooses paths, model files, workflow nodes or IDs.
Every run creates a project and keeps its plan and completed artifacts.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from . import assemble
from .film_comfy import ComfyFilmClient, IMAGE_MODEL
from .gpu_handoff import gpu_admission
from .ltx_media import atomic_json, concat_media, file_hash, digest
from .ltx_anchors import MAX_ANCHORS, shot_anchors, image_path, frame_index
from .film_storyboard import storyboard_plan
from .film_planning import FIRST_DEFAULT, clip_count, validate_plan, FilmPlanner, allocate_timing
from .film_quality import clean_policy, ProductionMetrics
from .film_production import (ReferenceBank, FilmTimings, write_animatic, process_peak_bytes,
                              bible_references, validate_bible_references, archive_take)
from .film_review import SceneReviewer, transcribe_existing
from .film_preflight import image_plan, approval_matches
from .film_references import (AUTO_END_ROLE, AUTO_START_ROLE, END_STRENGTH,
                              automatic_end, frame_prompt, reference_frame,
                              character_card_prompt, character_card_signature)
from .store import default_character, default_shot, render_fingerprint, stale_reason
from .backends.render_helpers import _resolved_prompt


def _local_json(url, payload=None):
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=4) as response:
        body = response.read()
        return json.loads(body) if body else {}


def prepare_language_memory():
    """Passive probes and empty-queue cache release, never model interruption."""
    try:
        queue = _local_json("http://127.0.0.1:8188/queue")
    except URLError:
        queue = None  # Comfy Desktop may be closed during ordinary chat.
    if queue is not None:
        if queue.get("queue_running") or queue.get("queue_pending"):
            raise RuntimeError("ComfyUI arbeider — vent før språkmodellen starter")
        ComfyFilmClient().release_memory()
    assert_other_models_idle(allow_director=True)


def assert_other_models_idle(*, allow_director=False):
    for url, name in (("http://127.0.0.1:11234/v1/models", "MLX-Serve"),
                      ("http://127.0.0.1:8000/health", "oMLX")):
        try:
            status = _local_json(url)
        except URLError:
            continue  # Closed services hold no weights.
        if name == "MLX-Serve":
            loaded = any(m.get("loaded") or m.get("bytes_resident", 0) > 0 or
                         m.get("state", "unloaded") != "unloaded" for m in status.get("data", []))
        else:
            loaded = status.get("engine_pool", {}).get("loaded_count", 0) > 0
            # A loading model can have zero entries in loaded_count while
            # already allocating unified memory. Check the passive catalogue
            # too; failure to establish this state must block GPU handoff.
            from .director_omlx import DirectorOMLX, MODEL
            entries = DirectorOMLX().models()
            resident = any((m.get("loaded") or m.get("is_loading"))
                           and (not allow_director or m["id"] != MODEL) for m in entries)
            loaded = resident or (loaded and not allow_director)
        if loaded:
            raise RuntimeError(f"{name} har en modell i minnet. Frigjør den i appen før filmjobben; andre jobber stoppes ikke.")


def _text(obj, key, limit=4000, optional=False):
    value = obj.get(key, "" if optional else None)
    if not isinstance(value, str) or len(value) > limit or (not optional and not value.strip()):
        raise ValueError(f"Ugyldig eller manglende {key}")
    # A generated delimiter could accidentally create additional Director shots.
    if re.search(r"(?m)^\s*---\s*$", value):
        raise ValueError("Planen inneholder en reservert klippskiller")
    return value.strip()


def film_prompt(style, characters, scene, shot):
    """Plain LTX scene prose; portrait lists are not frame anchors."""
    cast = [c for c in characters if c["key"] in scene["characterKeys"]]
    parts = [style, scene["setting"]]
    if scene.get("assetStates"):
        parts.append("Recurring physical states: " + json.dumps(scene["assetStates"], ensure_ascii=False))
    parts.extend([shot.get("stateBefore") or scene.get("stateBefore") or "",
                  shot.get("stateAfter") and "Required closing continuity: " + shot["stateAfter"] or ""])
    parts.append("Keep each character's identity, body design, materials, colours and costume consistent with the image anchors. "
                 "Keep the characters distinct throughout the action.")
    parts.extend(f"{c['name']}: {c['description']}" for c in cast)
    parts.extend([shot["start"], shot["action"], "The action ends with " + shot["end"],
                  shot["camera"]])
    if shot["dialogue"]:
        speaker = next(c["name"] for c in cast if c["key"] == shot["speakerKey"])
        parts.append(f'{speaker} says in {shot.get("dialogueLanguage", "Norwegian")}: "{shot["dialogue"]}". Other characters listen.')
    parts.extend([shot["sound"], "No background music."])
    return " ".join(part if part.endswith((".", "!", "?")) else part + "." for part in parts if part)


def planning_system(root):
    return """You are the film director inside Storyboard on this Mac. Convert the user's
film brief into a complete, achievable film plan. Return ONLY one JSON object. Before expanding shots, plan the whole story:
clear character motivations, setup, development, turning points and resolution.
Include filmBible: {"theme":"...","storyArc":"causal narrative and character arc",
"acts":[{"id":"act1","description":"dramatic purpose and change"}],
"locations":[{"id":"garden","description":"stable spatial layout and light"}],
"props":[{"id":"key","description":"stable object design and story role"}]}.
Each character may include motivation. Each scene includes locationId,
propIds, stateBefore and stateAfter referencing the bible. Use actId only when
the corresponding act is defined in filmBible. Use assetStates
with recurring asset IDs as keys and short physical states as values; change
these only for intentional story changes (damage, rebuilding, day/night). Preserve intentional
costume/damage/knowledge changes; do not reset them between scenes. Keep the
bible concise, include only recurring assets, and use few coherent locations.
A dramatic scene may span multiple camera setups: a directorScene is ONE setup.
Use one clip when that completes the shot. Add native continuations only when
the same unbroken take needs more time; reactions and reverse angles can stand alone.
Six clips is a hard maximum. Keep the camera field identical across native
continuations; describe subject movement in action. Changed framing needs a new scene.
Avoid overlong continuous passages when an ordinary film cut serves the story.
Do not include code fences, paths, commands, model downloads or workflow nodes.
Descriptions and image prompts are English; titles may be Norwegian. Dialogue
keeps the language requested in the brief (Norwegian by default), short enough
for a 4-6 second clip, normally five seconds. The server allocates actual frame-grid durations
from dialogue/action, but rejects a plan that expands the requested runtime by
more than 10% (two-second tolerance for short films). Fit the requested runtime
by shortening dialogue or simplifying action; split content that needs more
than six seconds. Do not rely on extending every
clip. Set dialogueLanguage to its English language name.
Each clip has ONE visible
action and a concrete start and end. Following clips in a scene start from the
previous end; LTX carries the last 25 video frames and matching audio forward. A scene is
a continuous passage with stable cast, setting, lighting and camera framing. A new scene is a cut.
Keep the same visible subjects and shot scale through native continuation. A
close-up, reverse angle or switch from robot-only to orc-only is a NEW SCENE,
never an instruction to transform the preceding continuous shot. Stage physical
contact as one readable action per clip with distinct subjects and a reachable
end pose. The server uses four-view character cards as visual references in Qwen-Image-2.1-Turbo
to create complete scene openings, then sends those frames to LTX as anchors.
Automatic closing anchors are OFF by default; do not depend on a fixed end image
to create movement. Optional closing anchors require an explicit reachable end.
The saved bible can link approved environment/prop reference images selected from
the library. You must not invent image references or paths in a new film plan.
Complete scene openings and every camera group's actual video require human
review. Compare the actual ending against the next approved opening; persistent
state matters across camera cuts. Never silently replace an approved image.
Balanced/thorough visual-model checks run serially after releasing Qwen/LTX.
The cold 27B reviewer has a separate 720-second startup limit and up to 180 seconds
for each selected assessment; its memory must be released before video resumes.
Describe opening and closing poses precisely; never
use a portrait/contact sheet as the complete scene. Manual anchors take priority.
Use up to four main characters with fixed face, age, hair, costume and palette.
Character imagePrompt describes the fixed full-body design. The server creates
a character card with front, three-quarter, side and back views on a plain
background, using an existing portrait as an identity reference when present.
Scene imagePrompt shows ONE complete opening frame with the scene cast, setting,
lighting and opening poses. Qwen uses the cards visually to compose scene frames;
the multi-view card itself must never become a video frame.
Use stable character keys and place every speaking character in characterKeys.
Use restrained camera movement and visible action rather than abstract emotion.
Schema:
{"title":"...","style":"shared film style and lighting",
 "characters":[{"key":"kari","name":"Kari","description":"fixed identity",
 "imagePrompt":"fixed full-body character design, costume, materials, palette"}],
 "filmBible":{"storyArc":"...","locations":[{"id":"garden","description":"fixed layout, lighting and palette"}],"props":[],"acts":[]},
 "scenes":[{"title":"...","setting":"environment and spatial layout","locationId":"garden","propIds":[],"assetStates":{"garden":"unchanged"},"stateBefore":"where everyone and recurring objects are before this setup","stateAfter":"what physically changed after this setup",
 "imagePrompt":"complete opening frame, cast, setting, style, lighting","characterKeys":["kari"],
 "shots":[{"title":"...","start":"visible opening state","motionIntent":"action or hold","action":"detailed
 observable action and composition","end":"visible final state","camera":"...",
 "sound":"diegetic ambience and effects","dialogue":"optional spoken line","dialogueLanguage":"Norwegian",
 "speakerKey":"kari or empty string"}]}]}
Each scene may contain ONE shot or several native continuations. A scene cut does not require extra filler clips. The server writes plain LTX prompts
with stable character descriptions, start/action/end, camera, sound and quoted
dialogue. Native continuation carries the previous 25 frames and audio; identities
outside that window may drift. Keep scenes short and stage faces visibly.
"""



class GuardedLLM:
    """Reserve language inference against image/video starts, including streams."""
    def __init__(self, inner, orch):
        object.__setattr__(self, "inner", inner)
        object.__setattr__(self, "orch", orch)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "inner"), name)

    def __copy__(self):
        # Request overrides belong to a new service; inference keeps the shared gate.
        return type(self)(copy.copy(self.inner), self.orch)

    def __setattr__(self, name, value):
        setattr(self.inner, name, value)

    def _call(self, method, *args, **kwargs):
        if getattr(self.inner,"vision",None) is False and (method=="complete_with_image" or method=="complete_with_media" and kwargs.get("images")):
            raise RuntimeError("Valgt språkmodellprofil støtter bare tekst; velg en profil med bildestøtte.")
        with self.orch._start_lock:
            gate = getattr(self.orch, "update_lifecycle", None)
            if gate is not None:
                gate.assert_open("språkmodell-admisjon")
            if getattr(self.orch, "_closing", False):
                raise RuntimeError("Storyboard avsluttes")
            if hasattr(getattr(self.orch,'backend',None),'admission_ready'):self.orch.backend.admission_ready()
            own_refine = (threading.current_thread() is self.orch._thread and self.orch._operation == "refine")
            if self.orch._llm_active or ((self.orch.busy or self.orch.stills_busy) and not own_refine):
                raise RuntimeError("Storyboard arbeider — vent med språkmodellen til køen er ferdig")
            self.orch._llm_active += 1
        try:
            if hasattr(self.inner, "release_memory"):
                prepare_language_memory()
            return getattr(self.inner, method)(*args, **kwargs)
        except Exception as exc:
            key=getattr(self.inner,"api_key","")
            if key and key in str(exc):
                raise RuntimeError(str(exc).replace(key,"[redacted]")) from None
            raise
        finally:
            with self.orch._start_lock:
                self.orch._llm_active -= 1

    def complete(self, *args, **kwargs):
        return self._call("complete", *args, **kwargs)

    def complete_with_image(self, *args, **kwargs):
        return self._call("complete_with_image", *args, **kwargs)

    def complete_with_media(self, *args, **kwargs):
        return self._call("complete_with_media", *args, **kwargs)


class FilmController:
    def __init__(self, ctx, comfy=None):
        self.ctx = ctx
        self.comfy = comfy or ComfyFilmClient()
        self._lock = threading.Lock()
        self._state = {"busy": False}
        jobs = list(ctx.data_dir.glob("*/film-job.json"))
        if jobs:
            try:
                self._state = json.loads(max(jobs, key=lambda p: p.stat().st_mtime).read_text())
                if self._state.get("busy"):
                    self._state.update(busy=False, phase="interrupted", error="Appen ble startet på nytt. Fortsett fra den lagrede planen.")
            except (OSError, ValueError):
                self._state = {"busy": False}
        if self._state.get("slug"):
            self._state.setdefault("jobId", digest([self._state["slug"], self._state.get("startedAt")])[:32])

    def status(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def owns(self, slug):
        state = self.status()
        return state.get("busy") and state.get("slug") == slug

    def _update(self, **fields):
        with self._lock:
            self._state.update(fields)
            if self._state.get("slug"):
                target = self.ctx.store.project_dir(self._state["slug"]) / "film-job.json"
                atomic_json(target, self._state)

    def start(self, payload):
        resume_slug = payload.get("resumeSlug")
        storyboard_slug = payload.get("storyboardSlug")
        source_revision = payload.get("storyboardRevision")
        expected_revision = payload.get("expectedRevision")
        anchor_override = payload.get("continuationAnchor")
        saved_plan = None
        policy_payload = dict(payload)
        if resume_slug:
            old = self.ctx.store.load(resume_slug)
            settings = old.get("filmAutomation") or {}
            if settings.get("engine") != "ltx-2.5":
                raise ValueError("Denne filmen ble laget med den tidligere videomotoren. Behold filmen og start en ny LTX-film fra samme idé.")
            plan_path = self.ctx.store.project_dir(resume_slug) / "film-plan.json"
            saved_plan = (storyboard_plan(old, strict=True) if settings.get("planSource") == "storyboard" else
                          validate_plan(json.loads(plan_path.read_text()), int(settings["plannedClips"]), strict=True)
                          if plan_path.is_file() else None)
            payload = {"description": settings["description"], "duration": settings["requestedSeconds"],
                       "imageEngine": IMAGE_MODEL, "title": old["name"],
                       "continuationAnchor": anchor_override if anchor_override is not None else settings.get("continuationAnchor", False)}
        if storyboard_slug:
            old = self.ctx.store.load(storyboard_slug)
            if not source_revision or digest(old) != source_revision:
                raise ValueError("Storyboardet er endret; be om et nytt startkort")
            if old.get("filmAutomation"):
                raise ValueError("Bruk Fortsett film for et eksisterende filmprosjekt")
            saved_plan = storyboard_plan(old, strict=True)
            payload = {**payload, "description": old.get("sceneDescription") or old["name"],
                       "title": old["name"], "duration": saved_plan["frames"] / 24}
        from_storyboard = bool(saved_plan and saved_plan.get("kind") == "storyboard")
        existing_settings = (old.get("filmAutomation") or {}) if resume_slug or storyboard_slug else {}
        policy = clean_policy(policy_payload, existing_settings.get("productionPolicy"))
        policy["requireImageApproval"] = True
        if resume_slug:
            policy["prepareOnly"] = bool(policy_payload.get("prepareOnly", False))
            if "reviewNote" in policy_payload and (not isinstance(policy_payload["reviewNote"], str)
                                                   or len(policy_payload["reviewNote"]) > 4000):
                raise ValueError("Gjennomgangsmerknaden må være tekst på høyst 4000 tegn")
            if existing_settings.get("reviewRequired") and not policy["prepareOnly"] and (policy_payload.get("acceptReview") is not True
                                                           or not expected_revision):
                raise ValueError("Se kvalitetsrapporten og bekreft Fortsett etter gjennomgang før filmen fortsetter")
            old_speech = clean_policy(existing_settings.get("productionPolicy"))["speechMode"]
            if old.get("shots") and policy["speechMode"] != old_speech:
                raise ValueError("Dialogmodus velges før den nye filmen planlegges; behold modusen ved gjenopptakelse")
        # Resumed jobs use the installed image engine; original media receipts
        # still describe their original takes and are never rewritten.
        character_references = True
        if character_references and saved_plan and any(len(s["characterKeys"]) > 4 for s in saved_plan["scenes"]):
            raise ValueError("Referansebaserte scenebilder støtter inntil fire karakterer per scene")
        description = str(payload.get("description") or "") if from_storyboard else _text(payload, "description", 12000)
        count = (sum(len(s["shots"]) for s in saved_plan["scenes"]) if saved_plan else
                 existing_settings["plannedClips"] if resume_slug and type(existing_settings.get("plannedClips")) is int
                 and existing_settings["plannedClips"] > 0 else clip_count(payload.get("duration", 7)))
        image_engine = payload.get("imageEngine", IMAGE_MODEL)
        if image_engine != IMAGE_MODEL:
            raise ValueError("Bildemotoren er Qwen-Image-2.1-Turbo; oppdater startkortet")
        title = _text(payload, "title", 160, True) or "Film fra idé"
        continuation_anchor = payload.get("continuationAnchor", False)
        if type(continuation_anchor) is not bool:
            raise ValueError("Ugyldig valg for videreføringsanker")
        orch = self.ctx.orch
        with orch._start_lock:
            gate = getattr(self, "update_lifecycle", None)
            if gate is not None:
                gate.assert_open("film-admisjon")
            if getattr(orch, "_closing", False):
                raise RuntimeError("Storyboard avsluttes")
            if storyboard_slug and digest(self.ctx.store.load(storyboard_slug)) != source_revision:
                raise ValueError("Storyboardet er endret; be om et nytt startkort")
            if resume_slug:
                current_revision = (self.ctx.store.load(resume_slug).get("filmAutomation") or {}).get("revision")
                if (current_revision != settings.get("revision")
                        or expected_revision is not None and current_revision != expected_revision):
                    raise ValueError("Filmprosjektet er endret; les filmstatus igjen før du fortsetter")
                if policy_payload.get("acceptImagePlan") is not None:
                    if existing_settings.get("imagePlanStale"):
                        raise ValueError("Bildeplanen er endret; lag og gjennomgå en ny forhåndsvisning før video")
                    manifest = image_plan(self.ctx.store, resume_slug, old, saved_plan)
                    if (not expected_revision or policy_payload["acceptImagePlan"] != manifest["digest"]
                            or manifest["digest"] != (existing_settings.get("imagePlan") or {}).get("digest")):
                        raise ValueError("Bildeplanen er endret; lag og gjennomgå en ny forhåndsvisning før video")
                    old["filmAutomation"]["imageApproval"] = {"digest": manifest["digest"], "approvedAt": time.time()}
            if orch.busy or orch.stills_busy or orch._llm_active:
                raise RuntimeError("Storyboard arbeider allerede — vent til køen er tom")
            service = self.ctx.llm_services().get("splash-local")
            if service is None or not hasattr(service, "release_memory"):
                raise RuntimeError("Den lokale regissørmodellen er ikke konfigurert")
            if saved_plan is not None:
                validate_bible_references(saved_plan.get("filmBible"), self.ctx.data_dir)
            self.comfy.preflight(require_images=True)
            for model in ("ltx-2.5", IMAGE_MODEL):
                capability = self.ctx.backend.capability(model)
                if capability is None or not capability.available:
                    raise RuntimeError(capability.unavailable_reason if capability else f"Modell ikke tilgjengelig: {model}")
            if from_storyboard:
                for shot in old["shots"]:
                    shot_anchors(shot, shot["frames"], self.ctx.data_dir)
                    for ref in (shot.get("startRef"), shot.get("endRef")):
                        if ref and ref.get("kind") != "chain":
                            image_path(ref, self.ctx.data_dir, verify=True)
                for character in old.get("characters", []):
                    if character.get("image"):
                        image_path(character["image"], self.ctx.data_dir, verify=True)
            if not from_storyboard and policy["speechMode"] == "recording" and (
                    getattr(self.ctx, "default_tts", "none") == "none" or not orch.prepare_dialogue):
                raise ValueError("Velg en konfigurert stemmemotor før faste stemmer brukes")
            slug, board = ((storyboard_slug or resume_slug, old) if storyboard_slug or resume_slug else self.ctx.store.create(title))
            board["defaults"].update(llm="splash-local", stillsEngine=IMAGE_MODEL,
                resolution="384x256", steps=8, model="ltx-2.5")
            if not resume_slug and not storyboard_slug:
                board["defaults"]["frames"] = FIRST_DEFAULT
            if not from_storyboard:
                if policy["speechMode"] == "recording":
                    tts_id = getattr(self.ctx, "default_tts", "none")
                    if tts_id == "none" or not orch.prepare_dialogue:
                        raise ValueError("Velg en konfigurert stemmemotor før faste stemmer brukes")
                    board["defaults"]["tts"] = tts_id
                else:
                    board["defaults"]["tts"] = "none"
            else:
                board["defaults"].update(draft=False, sketch=False)
            if policy_payload.get("acceptReview") and existing_settings.get("reviewRequired"):
                receipt = existing_settings["reviewRequired"]
                board["filmAutomation"].setdefault("acceptedReviews", []).append(receipt)
                board["filmAutomation"].setdefault("reviewAcknowledgements", {})[receipt] = {
                    "acceptedAt": time.time(), "revision": expected_revision,
                    "boundaryConfirmed": bool((existing_settings.get("reviewPreview") or {}).get("nextOpening")),
                    "note": policy_payload.get("reviewNote", "").strip()}
            board["filmAutomation"] = {**(board.get("filmAutomation") or {}), "description": description, "imageEngine": image_engine,
                "requestedSeconds": float(payload.get("duration", 7)), "plannedClips": count,
                "director": True, "resolution": "384x256", "engine": "ltx-2.5", "modelPack": "dgrauet/ltx-2.5-mlx-q8", "continuationAnchor": continuation_anchor,
                "characterReferences": character_references, "productionPolicy": policy,
                "productionVersion": 3, "reviewRequired": None, "reviewPreview": None}
            if from_storyboard:
                board["filmAutomation"]["planSource"] = "storyboard"
                if storyboard_slug:
                    board["filmAutomation"]["frameAdjustments"] = saved_plan["frameAdjustments"]
                atomic_json(self.ctx.store.project_dir(slug) / "film-plan.json", saved_plan)
            board["filmAutomation"]["revision"] = uuid.uuid4().hex
            self.ctx.store.save(slug, board)
            orch._cancel.clear()
            orch._operation, orch._slug = "film", slug
            orch._runs, orch._order, orch._assembly, orch._error = {}, [], None, ""
            orch._batch_started, orch._batch_ended = time.time(), None
            self._update(busy=True, jobId=uuid.uuid4().hex, checkpointScene=None, checkpointClips=0, slug=slug, phase="images" if saved_plan else "plan",
                detail=("Produserer det planlagte storyboardet" if storyboard_slug else
                        "Fortsetter fra lagret plan" if saved_plan else f"Regissørmodellen planlegger {count} klipp"),
                startedAt=time.time(), endedAt=None, completed=0, total=count, error="", failedPhase=None, cancelled=False, plan=None,
                revision=board["filmAutomation"]["revision"], reviewRequired=None, reviewPreview=None, imagePlan=None, quality=None)
            orch._thread = threading.Thread(target=self._work, args=(slug, description, count, image_engine, service, saved_plan),
                                            name="film-from-brief", daemon=True)
            orch._thread.start()
        return {"slug": slug, "status": self.status()}

    def stop(self, slug=None, job_id=None):
        with self.ctx.orch._start_lock:
            state = self.status()
            if ((slug is not None and slug != state.get("slug"))
                    or (job_id is not None and job_id != state.get("jobId"))):
                raise ValueError("Filmjobben er byttet; les filmstatus igjen før du stopper")
            if state.get("busy"):
                self.ctx.orch._cancel.set()
                self._update(detail="Stopper etter den aktive forespørselen", cancelled=True)
            return self.status()

    def set_anchor(self, slug, enabled, revision):
        if type(enabled) is not bool:
            raise ValueError("Videreføringsanker må være på eller av")
        with self.ctx.orch._start_lock:
            if self.owns(slug):
                raise RuntimeError("Stopp filmen og vent til arbeideren er avsluttet før ankeret endres")
            board = self.ctx.store.load(slug)
            settings = board.get("filmAutomation") or {}
            if settings.get("engine") != "ltx-2.5" or not revision or settings.get("revision") != revision:
                raise ValueError("Filmprosjektet er endret; les filmstatus igjen")
            settings.update(continuationAnchor=enabled, revision=uuid.uuid4().hex)
            self.ctx.store.save(slug, board)

    def _check_cancel(self):
        if self.ctx.orch._cancel.is_set():
            raise RuntimeError("Filmjobben ble stoppet; ferdige filer er bevart")

    def _plan(self, service, description, count):
        slug = self.status().get("slug")
        checkpoint = self.ctx.store.project_dir(slug) / "film-planning.json" if slug else None
        planner = FilmPlanner(service, check_cancel=self._check_cancel,
            on_progress=lambda **p: self._update(detail=f"Regi: scene {p['scene']}, {p['completed']}/{p['total']} klipp"),
            checkpoint_path=checkpoint)
        settings = {k: getattr(service, k, None) for k in ("model", "thinking", "temperature", "num_ctx")}
        return planner.plan(description, count, system=planning_system(self.ctx.ui_root), settings=settings)

    def _plan_cards(self, service, description, count, *, cast=None):
        plan = self._plan(service, description, count)
        if cast is not None and (len(plan["scenes"]) != 1 or set(plan["scenes"][0]["characterKeys"]) != set(cast)):
            raise ValueError("Return exactly one continuous scene with the supplied characterKeys")
        return plan

    def _long_plan(self, service, description, count):
        return self._plan(service, description, count)

    def _image(self, prompt, path, engine, slug, index):
        self._check_cancel()
        if path.is_file():
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            return
        self.comfy.qwen_image(prompt, path, seed=index, cancel=self.ctx.orch._cancel.is_set)
        from PIL import Image
        with Image.open(path) as image:
            image.verify()

    def _ref(self, path, label):
        rel = path.resolve().relative_to(self.ctx.data_dir.resolve()).as_posix()
        return {"kind": "upload", "path": rel, "url": "/media/" + rel, "label": label}

    def _work(self, slug, description, count, engine, service, saved_plan=None):
        orch, store = self.ctx.orch, self.ctx.store
        root = store.project_dir(slug)
        from_storyboard = bool(saved_plan and saved_plan.get("kind") == "storyboard")
        settings = store.load(slug).get("filmAutomation") or {}
        policy = clean_policy(settings.get("productionPolicy"))
        metrics_path = root / "production-metrics.json"
        try:
            previous_metrics = json.loads(metrics_path.read_text())
        except (OSError, ValueError):
            previous_metrics = {}
        metrics = ProductionMetrics(previous_metrics)
        # Engine timing history is per-user configuration, not bundled app
        # content.  Keep the old workspace location for lightweight/fake
        # contexts used by integrations that predate config_root.
        timings = FilmTimings(getattr(self.ctx, "config_root", self.ctx.workspace) / "film-timings.json")
        reviewer = None
        try:
            metrics.start("plan")
            character_references = bool((store.load(slug).get("filmAutomation") or {}).get("characterReferences"))
            # Empty Comfy queue was verified at admission. Drop cached image
            # tensors before the language model loads; never interrupt jobs.
            self.comfy.preflight(require_images=True)
            # Resume skips planning and proceeds to GPU work. A new plan can
            # reuse an already active director, avoiding an unnecessary reload.
            if saved_plan is not None and hasattr(self.ctx, "release_language_memory"):
                with gpu_admission():
                    self.ctx.release_language_memory()
            if saved_plan is None:
                prepare_language_memory()
            plan = saved_plan or self._plan(service, description, count)
            if saved_plan is None and any(entry.get("referenceImage")
                    for group in ("locations", "props") for entry in plan.get("filmBible", {}).get(group, [])):
                raise ValueError("Regissøren kan ikke finne på bildestier. Velg miljø-/rekvisittbilder fra biblioteket i den lagrede planen.")
            validate_bible_references(plan.get("filmBible"), store.data_dir)
            if saved_plan is None:
                plan = allocate_timing(plan, settings["requestedSeconds"])
                if plan.get("timing", {}).get("flags"):
                    raise ValueError("Planen trenger kortere replikker eller flere klipp: " + str(plan["timing"]["flags"][:3]))
            for scene in plan["scenes"]:
                refs = bible_references(scene, plan.get("filmBible"), store.data_dir)
                ref_limit = 9 if policy["automaticEndAnchors"] else 10
                if len(scene["characterKeys"]) + len(refs) > ref_limit:
                    raise ValueError(f"Scenen har for mange referanser; velg maksimalt {ref_limit} karakter-/miljøbilder.")
                if len(scene["shots"]) > 6:
                    raise ValueError("En scene kan ha maks seks klipp før gjennomgang. Del planen i kamerautsnitt med egne startbilder før produksjon.")
                bible = plan.get("filmBible") or {}
                location = next((entry.get("description") for entry in bible.get("locations", []) if entry.get("id") == scene.get("locationId")), "")
                if location and location not in scene["setting"]:
                    scene["setting"] += " Fixed location design: " + location
                if any(card.get("locationId") and card["locationId"] != scene.get("locationId") for card in scene["shots"]):
                    raise ValueError("Et stedsskifte trenger ny kamerascene og eget startbilde")
                scene["propDescriptions"] = {entry["id"]: entry.get("description") or entry.get("title") or ""
                    for entry in bible.get("props", []) if entry.get("id") in (scene.get("propIds") or [])}
            metrics.finish("plan", reused=saved_plan is not None)
            self._check_cancel()
            atomic_json(root / "film-plan.json", plan)
            board = store.load(slug)
            if not from_storyboard:
                board["filmBible"] = copy.deepcopy(plan.get("filmBible", {}))
            if board["name"] == "Film fra idé":
                board["name"] = plan["title"]
            if not from_storyboard:
                board["renderStyle"] = plan["style"]
                board["sceneDescription"] = description
            characters = {}
            for index, character in enumerate(plan["characters"]):
                item = (board["characters"][index] if saved_plan and len(board["characters"]) == len(plan["characters"])
                        else default_character())
                item.update(name=character["name"], description=character["description"], filmKey=character["key"])
                characters[character["key"]] = item
            board["characters"] = list(characters.values())
            scene_shots = []
            existing_shots = list(board.get("shots", []))
            if saved_plan and existing_shots and len(existing_shots) != count:
                raise ValueError("Klippantallet er endret. Lag en ny film for en ny plan.")
            cursor = 0
            for scene in plan["scenes"]:
                group = []
                for index, card in enumerate(scene["shots"]):
                    if saved_plan and existing_shots:
                        shot = existing_shots[cursor]
                        if from_storyboard:
                            if shot["id"] != card["id"]:
                                raise ValueError("Storyboardets klipprekkefølge er endret")
                            shot.update(frames=card["frames"], seed=card["seed"], imageAnchors=card["imageAnchors"], characterIds=card["characterIds"],
                                        directorScene=len(scene_shots)+1, model="ltx-2.5", resolution="384x256", steps=8)
                            if shot.get("status") != "done":
                                shot.update(status="queued", reason="")
                    else:
                        shot = default_shot(board["defaults"])
                        shot.update(title=card["title"], prompt=film_prompt(plan["style"], plan["characters"], scene, card),
                            frames=scene.get("firstFrames", 73) if index == 0 else scene.get("continuationFrames", 96), characterIds=[characters[k]["id"] for k in scene["characterKeys"]],
                            dialogue=card["dialogue"], speakerId=characters[card["speakerKey"]]["id"] if card["dialogue"] else "",
                            dialogueSource=policy["speechMode"], status="queued", directorScene=len(scene_shots) + 1)
                        if policy["speechMode"] == "recording":
                            shot["prompt"] = film_prompt(plan["style"], plan["characters"], scene, {**card, "dialogue": ""}) + " No speech; dialogue is added separately."
                    if not from_storyboard:
                        shot["soundNote"] = card.get("sound", "")
                        shot["dialogueLanguage"] = card.get("dialogueLanguage", "Norwegian")
                        for field in ("start", "action", "end", "camera", "motionIntent", "stateBefore", "stateAfter"):
                            shot[field] = card.get(field, "action" if field == "motionIntent" else scene.get(field, ""))
                        for field in ("locationId", "propIds", "assetStates"):
                            shot[field] = copy.deepcopy(card.get(field, scene.get(field, [] if field == "propIds" else {} if field == "assetStates" else "")))
                    cursor += 1
                    group.append(shot)
                scene_shots.append(group)
            board["shots"] = [shot for group in scene_shots for shot in group]
            store.save(slug, board)
            # Close manual language-start admission before releasing the last
            # plan request; this phase proceeds directly to GPU image work.
            self._update(phase="images", detail="Plan lagret. Frigjør regissørmodellens minne", plan={
                "characters": len(characters), "scenes": len(scene_shots), "clips": count,
                "estimatedVideoSeconds": sum(s["frames"] for g in scene_shots for s in g) / 24})
            with gpu_admission():
                service.release_memory(cancel=orch._cancel.is_set)
                assert_other_models_idle()
            metrics.start("images")
            bank = ReferenceBank(root, plan["style"])
            image_index = 0
            portrait_paths = {}
            for character in plan["characters"]:
                image_index += 1
                self._update(phase="images", detail=f"Qwen: karakterkort for {character['name']}")
                item = characters[character["key"]]
                existing = item.get("image") or {}
                signature = character_card_signature(plan["style"], character["name"],
                    character["description"], self.comfy.qwen_model_signature())
                source = image_path(existing, store.data_dir, verify=True) if existing else None
                if (source and existing.get("role") == "character-card"
                        and existing.get("cardSignature") == signature
                        and existing.get("cardHash") == file_hash(source)):
                    path = source
                else:
                    path = reference_frame(self.comfy,
                        character_card_prompt(plan["style"], character["name"], character["description"]),
                        [(source, f"{character['name']} — preserve this character's identity")] if source else [],
                        root / "refs" / "character-cards", seed=image_index,
                        cancel=orch._cancel.is_set, width=1536, height=1024)
                portrait_paths[character["key"]] = path
                board = store.load(slug)
                actual = next(c for c in board["characters"] if c["id"] == item["id"])
                actual["image"] = {**self._ref(path, character["name"] + " — karakterkort"),
                    "role": "character-card", "cardSignature": signature, "cardHash": file_hash(path)}
                store.save(slug, board)
            def prepare_scene_images(number, scene, soft_reference=None):
                nonlocal image_index
                image_index += 1
                self._update(phase="images", detail=f"{engine}: miljø {number}/{len(scene_shots)}")
                path = root / "refs" / f"scene-opening-{number:02d}.png"
                cast_text = "\n".join(f"{c['name']}: {c['description']}" for c in plan["characters"] if c["key"] in scene["characterKeys"])
                opening = "\n".join([plan["style"], "One complete cinematic opening frame, not a portrait or collage.",
                    scene["setting"], cast_text, scene["shots"][0]["start"], scene["shots"][0]["camera"]])
                first_shot = scene_shots[number-1][0]
                first_ref = first_shot.get("startRef") or {}
                explicit_start = next((a for a in first_shot.get("imageAnchors", []) if a["frame"] == 0), None)
                references = [(portrait_paths[c["key"]], f"{c['name']} — character identity only")
                              for c in plan["characters"] if c["key"] in scene["characterKeys"]]
                explicit_refs = bible_references(scene, plan.get("filmBible"), store.data_dir)
                # Explicit design references must never disappear silently behind
                # a full reference budget. Leave one slot for optional end frames.
                limit = 9 if policy["automaticEndAnchors"] else 10
                seen = {Path(path).resolve() for path, _ in references}
                asset_refs = []
                for path, label in explicit_refs:
                    if path.resolve() not in seen:
                        asset_refs.append((path, label)); seen.add(path.resolve())
                if len(references) + len(asset_refs) > limit:
                    raise ValueError(f"Scene {number} har for mange karakter-/miljøreferanser. "
                                     f"Velg maksimalt {limit} forskjellige referansebilder før produksjon.")
                fallback = bank.references(scene, before_scene=number)
                if soft_reference and soft_reference.is_file():
                    fallback.insert(0, (soft_reference, "reviewed prior scene tail — preserve compatible set/props only; "
                                        "ignore people, poses and framing; planned state changes take priority"))
                for path, label in fallback:
                    if len(references) + len(asset_refs) >= limit:
                        break
                    if path.resolve() not in seen:
                        asset_refs.append((path, label)); seen.add(path.resolve())
                if (from_storyboard or character_references) and explicit_start:
                    path = image_path(explicit_start["ref"], store.data_dir, verify=True)
                elif ((from_storyboard or character_references) and first_ref and first_ref.get("kind") != "chain"
                      and (not character_references or first_ref.get("role") != AUTO_START_ROLE)):
                    path = image_path(first_ref, store.data_dir, verify=True)
                elif character_references:
                    path = reference_frame(self.comfy,
                        frame_prompt(plan["style"], scene, scene["shots"][0], plan["characters"]), references + asset_refs,
                        root / "refs" / "scene-frames", seed=number, cancel=orch._cancel.is_set)
                else:
                    self._image(opening, path, engine, slug, image_index)
                bank.remember(scene, path, number)
                board = store.load(slug)
                group = [s for s in board["shots"] if s.get("directorScene") == number]
                for index, shot in enumerate(group):
                    if not from_storyboard:
                        shot["referenceImages"] = []
                    if (not from_storyboard and not character_references or not shot.get("startRef")
                            or character_references and index == 0 and first_ref.get("role") == AUTO_START_ROLE):
                        shot["startRef"] = (self._ref(path, scene["title"]) if index == 0 else
                                            {"kind": "chain", "from": group[index - 1]["id"]})
                        if index == 0 and character_references and not explicit_start:
                            shot["startRef"]["role"] = AUTO_START_ROLE
                    if character_references:
                        anchors = [a for a in shot.get("imageAnchors", [])
                                   if not policy["automaticEndAnchors"] or not automatic_end(a)]
                        has_end = bool(shot.get("endRef")) or any(
                            frame_index(a["frame"], shot["frames"]) == shot["frames"]-1 for a in anchors)
                        if (policy["automaticEndAnchors"] and policy["endAnchorStrength"] > 0
                                and shot.get("end") and shot.get("motionIntent", "action") != "hold"
                                and not has_end and len(anchors) < MAX_ANCHORS):
                            self._update(detail=f"Qwen: sluttanker for {shot['title']}")
                            closing = reference_frame(self.comfy,
                                frame_prompt(plan["style"], scene, scene["shots"][index], plan["characters"], closing=True),
                                references + asset_refs + [(path, "scene environment, lighting and visual style")],
                                root / "refs" / "scene-frames", seed=number*1000+index+1, cancel=orch._cancel.is_set)
                            ref = {**self._ref(closing, shot["title"] + " — automatisk sluttanker"),
                                   "role": AUTO_END_ROLE, "autoStrength": policy["endAnchorStrength"]}
                            anchors.append({"ref": ref, "frame": "last", "strength": policy["endAnchorStrength"]})
                        if anchors != shot.get("imageAnchors", []):
                            shot["imageAnchors"] = anchors
                            board["anchorsRevision"] = uuid.uuid4().hex
                store.save(slug, board)
                return path

            if policy["prepareOnly"] or policy["requireImageApproval"]:
                for number, scene in enumerate(plan["scenes"], 1):
                    prepare_scene_images(number, scene)
            metrics.finish("images")
            board = store.load(slug)
            animatic = write_animatic(root, board, plan)
            board["filmAutomation"]["animaticUrl"] = self._ref(animatic, "Animatic")["url"]
            board["filmAutomation"]["filmBible"] = plan.get("filmBible", {})
            store.save(slug, board)
            self._update(animaticUrl=board["filmAutomation"]["animaticUrl"])
            manifest = image_plan(store, slug, board, plan)
            board["filmAutomation"]["imagePlan"] = manifest
            board["filmAutomation"]["imagePlanStale"] = False
            if policy["prepareOnly"] or not approval_matches(manifest, board["filmAutomation"]):
                board["filmAutomation"]["preparedReferences"] = True
                store.save(slug, board)
                self._update(phase="prepared", imagePlan=manifest,
                    detail="Bildeplanen er klar. Kontroller figurer, klær, rom og startposisjoner. Godkjenn bildene før video; første videoscene må også gjennomgås.")
                return
            store.save(slug, board)
            if (from_storyboard or policy["speechMode"] == "recording") and orch.prepare_dialogue:
                recorded = [shot for shot in board["shots"] if shot.get("dialogue") and shot.get("dialogueSource") != "native"]
                if recorded:
                    self._update(phase="dialogue", detail="Frigjør bildemodellen før separate dialogopptak")
                    self.comfy.release_memory(cancel=orch._cancel.is_set)
                    metrics.start("speech")
                for shot in recorded:
                    self._check_cancel()
                    orch.prepare_dialogue(slug, shot["id"])
                if recorded:
                    metrics.finish("speech")
            reviewer = SceneReviewer(root, plan, policy, service,
                release_images=lambda: self.comfy.release_memory(cancel=orch._cancel.is_set),
                release_language=lambda: service.release_memory(cancel=orch._cancel.is_set),
                set_phase=self._update, cancel=orch._cancel.is_set,
                transcriber=lambda path, deadline: transcribe_existing(self.ctx, path, deadline),
                prepare_language=prepare_language_memory,
                confirm_language_released=assert_other_models_idle)
            completed = 0
            scene_movies = []
            for number, (scene, group) in enumerate(zip(plan["scenes"], scene_shots), 1):
                self._check_cancel()
                board = store.load(slug)
                current_manifest = image_plan(store, slug, board, plan)
                if not approval_matches(current_manifest, board["filmAutomation"]):
                    board["filmAutomation"].update(imagePlan=current_manifest, imagePlanStale=False)
                    board["filmAutomation"].pop("imageApproval", None)
                    store.save(slug, board)
                    self._update(phase="prepared", imagePlan=current_manifest, completed=completed,
                        detail="Bildegrunnlaget ble endret. Gjennomgå den oppdaterte bildeplanen før flere klipp lages.")
                    return
                assert_other_models_idle()
                metrics.start("images")
                if policy["requireImageApproval"]:
                    first = next(s for s in store.load(slug)["shots"] if s["id"] == group[0]["id"])
                    start_anchor = next((a["ref"] for a in first.get("imageAnchors", []) if a["frame"] == 0), None)
                    environment = image_path(start_anchor or first["startRef"], store.data_dir, verify=True)
                else:
                    prior_tail = (None if settings.get("preparedReferences") else bank.continuity_reference(scene, number))
                    environment = prepare_scene_images(number, scene, prior_tail)
                metrics.finish("images")
                board = store.load(slug)
                if number == 1 or number % 10 == 0 or number == len(scene_shots):
                    write_animatic(root, board, plan)
                self._update(phase="director", detail=f"LTX-2.5 Q8: scene {number}/{len(scene_shots)}, {len(group)} klipp")
                scene_base = completed
                def checkpoint(clips):
                    self._update(completed=scene_base + clips, checkpointScene=number, checkpointClips=clips,
                                 detail=f"Scene {number}: {clips}/{len(group)} klipp kontrollert og lagret")
                current = [next(s for s in board["shots"] if s["id"] == shot["id"]) for shot in group]
                first_frames = scene.get("firstFrames", 73)
                continuation_frames = scene.get("continuationFrames", 96)
                timed_anchors = [shot_anchors(s, first_frames if i == 0 else continuation_frames, store.data_dir)
                                 for i, s in enumerate(current)]
                if from_storyboard or character_references:
                    for shot, anchors in zip(current, timed_anchors):
                        explicit = {frame_index(a["frame"], shot["frames"]) for a in anchors}
                        for key, frame in (("startRef", 0), ("endRef", shot["frames"]-1)):
                            ref = shot.get(key)
                            if ref and ref.get("kind") != "chain" and frame not in explicit:
                                anchors.append({"path": str(image_path(ref, store.data_dir, verify=True)), "frame": frame, "strength": 1.0})
                resolve_prompt = getattr(self.ctx.backend, "resolved_prompt", _resolved_prompt)
                prompts = [resolve_prompt(s, board) for s in current]
                scene_seed = int(current[0].get("seed") or 0) if from_storyboard else number
                provenance = {"scene": scene, "style": plan["style"], "cast": plan["characters"],
                    "portraits": {k: file_hash(portrait_paths[k]) for k in scene["characterKeys"]},
                    "shots": [{k: s.get(k) for k in ("prompt", "frames", "seed", "model", "dialogue", "dialogueSource", "speakerId")}
                              for s in current]}
                # A verified tail from the immediately preceding compatible
                # scene is a cheap continuity anchor.  It does not invoke
                # Qwen or alter the planned opening image; explicit/manual
                # anchors and state changes still take precedence.
                anchor = environment if board["filmAutomation"].get("continuationAnchor") else None
                result = None
                records = board["filmAutomation"].get("scenes", [])
                records = [s for s in records if isinstance(s, dict)] if isinstance(records, list) else []
                saved = next((s for s in records if s.get("index") == number), None)
                if (saved_plan and saved and not saved.get("contract") and not anchor and not any(timed_anchors)
                        and saved.get("shotIds") == [s["id"] for s in current]
                        and all(s.get("status") == "done" and not stale_reason(s, board) for s in current)
                        and isinstance(saved.get("movie"), str) and isinstance(saved.get("runId"), str)
                        and hasattr(self.comfy, "legacy_scene")):
                    movie = self.ctx.data_dir / saved["movie"]
                    if movie.resolve().is_relative_to(root.resolve()) and not movie.parent.is_symlink():
                        result = self.comfy.legacy_scene(movie.parent, saved["runId"], [s["prompt"] for s in current],
                                                        environment, seed=number, cancel=orch._cancel.is_set)
                scene_started = time.monotonic()
                metrics.start("video")
                if result is None:
                    result = self.comfy.director_scene(prompts, [environment],
                        root / "director" / f"scene-{number:03d}", seed=scene_seed,
                        first_frames=first_frames, continuation_frames=continuation_frames,
                        continuation_image=anchor, continuation_frame=0, continuation_strength=.35,
                        image_anchors=timed_anchors,
                        take_archive_root=root / "takes" / f"scene-{number:03d}" / "native",
                        provenance=provenance, cancel=orch._cancel.is_set, on_checkpoint=checkpoint)
                metrics.finish("video", reused=bool(result.get("reused")))
                scene_elapsed = time.monotonic() - scene_started
                self._check_cancel()
                if len(result["shots"]) != len(group) or any(int(output["frames"]) != shot["frames"] for shot,output in zip(current,result["shots"])):
                    raise RuntimeError("Director returnerte feil antall klipp eller bilder")
                # Preserve the validated scene's native audio and clip order.
                scene_movies.append((scene["title"], result["movie"]))
                board = store.load(slug)
                for planned, output in zip(group, result["shots"]):
                    shot = next(s for s in board["shots"] if s["id"] == planned["id"])
                    index = board["shots"].index(shot) + 1
                    target = self.ctx.data_dir / store.shot_rel_dir(slug, index)
                    target.mkdir(parents=True, exist_ok=True)
                    if (shot.get("status") == "done" and not stale_reason(shot, board)
                            and (target / "clip.mp4").is_file() and (target / "director.wav").is_file()
                            and file_hash(target / "clip.mp4") == file_hash(output["video"])
                            and file_hash(target / "director.wav") == file_hash(output["audio"])):
                        completed += 1
                        continue
                    if (target / "clip.mp4").is_file() and (
                            file_hash(target / "clip.mp4") != file_hash(output["video"])
                            or (target / "director.wav").is_file()
                            and file_hash(target / "director.wav") != file_hash(output["audio"])):
                        archive_take(target, root / "takes" / f"shot-{index:03d}", {
                            "shot": copy.deepcopy(shot), "scene": saved,
                            "acceptedReviews": board["filmAutomation"].get("acceptedReviews", [])})
                    for source, name in ((output["video"], "clip.mp4"), (output["audio"], "director.wav")):
                        pending = target / ("." + name + ".pending")
                        shutil.copy2(source, pending)
                        pending.replace(target / name)
                    # Decode before publication; retain the tail for manual chaining.
                    import av
                    with av.open(str(target / "clip.mp4")) as media:
                        count_frames, middle, last = 0, None, None
                        for frame in media.decode(video=0):
                            if count_frames == int(output["frames"]) // 2:
                                middle = frame.to_image()
                            last = frame
                            count_frames += 1
                        if count_frames != int(output["frames"]) or not media.streams.audio:
                            raise RuntimeError("Director-klippets bildeantall eller lyd avviker fra manifestet")
                        thumb = target / "director-thumb.png"
                        middle.save(thumb)
                        frame_dir = target / "frames"
                        frame_dir.mkdir(exist_ok=True)
                        last.to_image().save(frame_dir / f"frame-{count_frames-1:05d}.png")
                    shot.update(status="done", progress=100, reason="", frames=count_frames,
                        outputs=[self._ref(target / "clip.mp4", "clip")["url"]], thumb=self._ref(thumb, "thumb")["url"],
                        renderedAs="final", renderedDialogueSource="native", directorRun=result["run_id"])
                    completed += 1
                if from_storyboard or policy["speechMode"] == "recording":
                    from .orchestrator import ShotRun
                    for i, shot in enumerate(board["shots"], 1):
                        if shot.get("directorScene") == number and shot.get("dialogueSource") != "native" and shot.get("dialogue"):
                            speech_dir = self.ctx.data_dir / store.shot_rel_dir(slug, i)
                            mixed = orch._relay_speech(shot, speech_dir, ShotRun(shot["id"]))
                            selection = {**shot, "renderedDialogueSource": shot.get("dialogueSource", "recording")}
                            if not mixed or assemble.shot_clip(speech_dir, selection) != mixed:
                                raise RuntimeError("Talt dialog er ikke mikset korrekt. Kontroller opptak/stemme før Fortsett film.")
                            shot["renderedDialogueSource"] = shot.get("dialogueSource", "recording")
                # Tail frames now exist. Resolve the manual chain previews
                # before recording fingerprints, so reopening/resuming the
                # film does not make an unchanged continuation look stale.
                store._resolve_chain_previews(slug, board)
                for shot in board["shots"]:
                    if shot.get("directorScene") == number:
                        shot["renderFingerprint"] = render_fingerprint(shot, board)
                previous = [s for s in records if s.get("index") != number]
                board["filmAutomation"]["scenes"] = previous + [{"index": number, "runId": result["run_id"], "contract": 0 if result.get("legacy") else 2,
                    "shotIds": [s["id"] for s in group], "movie": result["movie"].relative_to(self.ctx.data_dir).as_posix()}]
                store.save(slug, board)
                timings.record(sum(s["frames"] for s in current), scene_elapsed,
                               result=result, client=self.comfy, reused=bool(result.get("reused")))
                actual_shots = [s for s in board["shots"] if s.get("directorScene") == number]
                production_seconds = sum(row.get("elapsed", 0) for row in metrics.phases.values())
                next_scene = plan["scenes"][number] if number < len(plan["scenes"]) else None
                next_opening = None
                if next_scene is not None:
                    first_next = next(s for s in board["shots"] if s["id"] == scene_shots[number][0]["id"])
                    next_anchor = next((a["ref"] for a in first_next.get("imageAnchors", []) if a["frame"] == 0), None)
                    next_opening = image_path(next_anchor or first_next["startRef"], store.data_dir, verify=True)
                self._update(phase="review", detail=f"Kontrollerer faktisk scene {number} og overgangen videre")
                review = reviewer.scene(number, scene, actual_shots, board, environment,
                    [(c["name"], portrait_paths[c["key"]]) for c in plan["characters"] if c["key"] in scene["characterKeys"]],
                    production_seconds, next_scene=next_scene, next_opening=next_opening)
                tail_dir = root / "shots" / f"{board['shots'].index(actual_shots[-1])+1:02d}" / "frames"
                tails = sorted(tail_dir.glob("frame-*.png"))
                board = store.load(slug)
                accepted = board["filmAutomation"].get("acceptedReviews", [])
                critical = review.get("verdict") == "pause" or any(i.get("severity") == "critical" for i in review.get("issues", []))
                if review.get("available") and (not critical or review.get("key") in accepted):
                    bank.observe(number, review.get("observedEndState"), tails[-1] if tails else None, scene,
                                 asset_states=review.get("observedAssetStates"))
                board["filmAutomation"]["qualitySummary"] = {"reviewedScenes": sum(bool(r.get("available")) for r in reviewer.receipts),
                    "visualReviewAvailable": bool(review.get("available")),
                    "reviewSeconds": reviewer.used_seconds, "issues": [i for r in reviewer.receipts for i in r.get("issues", [])][-32:],
                    "motion": [m for receipt in reviewer.receipts for m in receipt.get("motion", [])][-100:]}
                # Model samples and motion metrics assist a human; neither can
                # approve an unseen boundary. Every setup, including the final
                # one, has its own receipt-bound playback checkpoint.
                if review["key"] not in accepted:
                    preview = {"scene": number, "title": scene["title"],
                        "shotIds": [s["id"] for s in actual_shots], "key": review["key"],
                        "plannedEnd": scene.get("stateAfter") or scene["shots"][-1].get("end"),
                        "observedEndState": review.get("observedEndState"),
                        "observedAssetStates": review.get("observedAssetStates", {}),
                        "modelAvailable": bool(review.get("available")), "reason": review.get("reason"),
                        "issues": review.get("issues", []),
                        "tail": self._ref(tails[-1], "Faktisk slutt") if tails else None,
                        "nextOpening": self._ref(next_opening, "Neste planlagte åpning") if next_opening else None,
                        "nextTitle": next_scene.get("title") if next_scene else None,
                        "nextStateBefore": next_scene.get("stateBefore") if next_scene else None}
                    board["filmAutomation"]["reviewRequired"] = review["key"]
                    board["filmAutomation"]["reviewPreview"] = preview
                    store.save(slug, board)
                    write_animatic(root, board, plan)
                    self._update(phase="needs_review", detail=f"Se og hør scene {number}. Sammenlign faktisk slutt med neste åpning før du godkjenner. " + "; ".join(i["issue"] for i in review.get("issues", []))[:1200],
                        completed=completed, quality=board["filmAutomation"]["qualitySummary"], reviewRequired=review["key"], reviewPreview=preview)
                    return
                store.save(slug, board)
                remaining_frames = sum(s["frames"] for g in scene_shots[number:] for s in g)
                self._update(completed=completed, quality=board["filmAutomation"]["qualitySummary"], etaSeconds=timings.estimate(remaining_frames))
            self._update(phase="assemble", detail="Setter sammen filmen")
            metrics.start("assembly")
            total_frames = [sum(s["frames"] for s in group) for group in scene_shots]
            movies = [movie for _, movie in scene_movies]
            music = None
            if from_storyboard or policy["speechMode"] == "recording":
                if orch.prepare_soundtrack:
                    music = orch.prepare_soundtrack(slug, cancel=orch._cancel.is_set)
                self._check_cancel()
                board = store.load(slug)
                if assemble.unmixed_dialogue(board, root):
                    raise RuntimeError("Talt dialog er ikke mikset korrekt. Kontroller opptak/stemme før Fortsett film.")
                parts = assemble.board_parts(board, root)
                options = assemble.board_options(board, root, self.ctx.data_dir)
                # Keep simple long cuts sequential; use the existing ffmpeg
                # editor for trims, transitions and soundtrack processing.
                edited = (any((board.get("assembly") or {}).values()) or any(
                    s.get("trimIn") or s.get("trimOut") or s.get("muteAudio") for s in board["shots"])
                    or options.get("backgroundPath"))
                if edited:
                    result = assemble.assemble(parts, assemble.final_path(root),
                        *assemble.frame_size(board["defaults"]["resolution"]), options=options, cancel=orch._cancel.is_set)
                    if not result.ok or result.missing:
                        raise RuntimeError(result.error or "Filmen mangler ferdige klipp")
                else:
                    final = concat_media([path for _, path in parts], assemble.final_path(root),
                        frames=[s["frames"] for s in board["shots"]], cancel=orch._cancel.is_set)
                    result = assemble.AssemblyResult(ok=True, path=final,
                        parts=[(title, path.name) for title, path in parts], seconds=sum(s["frames"] for s in board["shots"])/24)
            else:
                final = concat_media(movies, assemble.final_path(root),
                                     frames=total_frames, cancel=orch._cancel.is_set)
                result = assemble.AssemblyResult(ok=True, path=final,
                    parts=[(title, movie.name) for title, movie in scene_movies], seconds=sum(total_frames) / 24)
            self._check_cancel()
            board = store.load(slug)
            url = self._ref(result.path, "Film")["url"]
            board["finalVideo"] = result.to_json(url)
            board["finalVideo"]["directorScenes"] = [{"shot": title, "file": movie.name} for title, movie in scene_movies]
            if not from_storyboard:
                board["finalVideo"]["parts"] = [{"shot": shot["title"], "file": "clip.mp4"} for shot in board["shots"]]
            if music:
                board["finalVideo"]["soundtrack"] = music
            board["finalVideo"]["settingsFingerprint"] = assemble.assembly_fingerprint(board)
            store.save(slug, board)
            orch._set_assembly("done", f"{count} klipp, {result.seconds:.1f}s", url=url)
            metrics.finish("assembly")
            self._update(phase="done", detail="Videoen er teknisk ferdig. Kontroller handling, sammenheng og lyd; ferdig status er ikke en visuell godkjenning.", etaSeconds=0)
        except Exception as exc:
            failed_phase = self.status().get("phase")
            logging.getLogger(__name__).exception("Film %s failed during %s", slug, failed_phase)
            cancelled = orch._cancel.is_set()
            orch._error = str(exc)
            self._update(phase="cancelled" if cancelled else "failed", error=str(exc), cancelled=cancelled,
                         detail=str(exc), failedPhase=failed_phase)
            board = store.load(slug)
            for shot in board.get("shots", []):
                if shot.get("status") == "queued":
                    shot.update(status="interrupted" if cancelled else "failed", reason=str(exc))
            store.save(slug, board)
        finally:
            try:
                for phase in list(metrics._active):
                    metrics.finish(phase)
                metric_record = {**metrics.as_dict(), "editorPeakRSS": process_peak_bytes(),
                    "projectBytes": sum(path.stat().st_size for path in root.rglob("*") if path.is_file()),
                    "diskFreeBytes": shutil.disk_usage(root).free,
                    "reviewSeconds": reviewer.used_seconds if reviewer else 0.0}
                atomic_json(metrics_path, metric_record)
                self._update(metrics=metric_record)
                board = store.load(slug)
                board["filmAutomation"]["revision"] = uuid.uuid4().hex
                store.save(slug, board)
                self._update(revision=board["filmAutomation"]["revision"])
            finally:
                orch._batch_ended = time.time()
                self._update(busy=False, endedAt=time.time())
