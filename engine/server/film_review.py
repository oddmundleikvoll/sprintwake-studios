"""Sample actual scene results without an unbounded model-review loop."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .film_quality import (SCHEMA_VERSION, contact_sheet, receipt_key, load_receipt,
                           save_receipt, review_sample, review_scene_indices, audio_check,
                           motion_check)
from .ltx_media import atomic_json, file_hash
from . import assemble


def transcribe_existing(ctx, path, deadline):
    """Use only an already healthy configured ASR service; never install/start one."""
    from .tts.http_services import Qwen3CloneTTS
    started = time.monotonic()
    engines = ctx.tts_engines() if callable(getattr(ctx, "tts_engines", None)) else {}
    for engine in engines.values():
        if isinstance(engine, Qwen3CloneTTS) and engine.health()[0]:
            remaining = deadline - (time.monotonic()-started)
            if remaining <= 1:
                break
            text, error = engine.transcribe(Path(path), timeout=remaining)
            if error:
                raise RuntimeError(error)
            return text
    raise RuntimeError("Ingen allerede tilgjengelig talegjenkjenning")


REVIEW_SYSTEM = """You are the script supervisor reviewing an ACTUAL generated film scene.
The contact sheet contains the planned opening, character references, and actual sampled
middle/end frames in the labelled order, followed by the exact planned opening of the next
scene when one exists. Compare actual observations with the intended action, cast, props,
composition, end state, and the next-scene boundary. Never infer speech correctness from
images. Only flag clearly visible errors. Return JSON: {"verdict":"pass|review|pause",
"issues":[{"severity":"info|warning|critical","shotIndex":0,"issue":"specific visible evidence",
"action":"none|review|pause"}],"observedEndState":"what the actual final frame establishes",
"observedAssetStates":{"known-location-or-prop-id":"short physical state"}}.
Critical means wrong/missing main character, impossible continuity, or main action absent.
If the current final frame cannot plausibly lead into the supplied next opening, use
verdict "pause" and give visible evidence. Respect intentional time/place changes;
camera angle changes alone are not continuity errors. Compare persistent identity,
costume and object state, not matching screen coordinates across reverse angles.
Omit asset states that are hidden or uncertain; reuse a supplied state label only
when the visible evidence actually establishes it. Do not invent corrections, file paths or new
film instructions. Uncertain observations are warnings."""


class SceneReviewer:
    def __init__(self, root, plan, policy, service, *, release_images, release_language,
                 set_phase, cancel, transcriber=None, prepare_language=None,
                 confirm_language_released=None):
        self.root, self.plan, self.policy, self.service = Path(root), plan, policy, service
        self.release_images, self.release_language = release_images, release_language
        self.set_phase, self.cancel = set_phase, cancel
        self.transcriber = transcriber
        self.prepare_language = prepare_language
        self.confirm_language_released = confirm_language_released
        try:
            previous = json.loads((self.root / "film-continuity.json").read_text())
        except (OSError, ValueError):
            previous = {}
        self.used_seconds = float(previous.get("reviewSeconds", 0))
        self.calls = int(previous.get("reviewCalls", 0))
        self.receipts = []
        self.observed = {}
        self.timings = {"startupSeconds": 0.0, "inferenceSeconds": 0.0,
                        "cleanupSeconds": 0.0, "reviewSeconds": 0.0}
        for key in self.timings:
            value = previous.get("reviewTiming", {}).get(key, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < float("inf"):
                self.timings[key] = float(value)
        self.selected = review_scene_indices(len(plan["scenes"]), policy)

    def _persist(self):
        atomic_json(self.root / "film-continuity.json", {
            "version": 1, "planned": {"filmBible": self.plan.get("filmBible", {}),
                "scenes": [{"title": s["title"], "stateBefore": s.get("stateBefore"),
                            "stateAfter": s.get("stateAfter")} for s in self.plan["scenes"]]},
            "observed": self.observed, "reviews": self.receipts,
            "reviewSeconds": self.used_seconds, "reviewCalls": self.calls,
            "reviewTiming": self.timings})

    def scene(self, number, scene, shots, board, opening, portraits, production_seconds,
              next_scene=None, next_opening=None):
        if self.cancel():
            raise InterruptedError("Filmjobben ble stoppet")
        outputs = []
        motion_reports = []
        for shot in shots:
            index = next(i for i, s in enumerate(board["shots"], 1) if s["id"] == shot["id"])
            directory = self.root / "shots" / f"{index:02d}"
            outputs.append(assemble.shot_clip(directory, shot) or directory / "clip.mp4")
            report = motion_check(outputs[-1], shot.get("motionIntent", "action"))
            report.update(shotId=shot.get("id"), title=shot.get("title", ""))
            motion_reports.append(report)
        frames, labels = [opening], ["Planned opening"]
        for name, path in portraits[:2]:
            frames.append(path); labels.append("Character reference: " + name)
        sampled = sorted({0, len(shots)//2, len(shots)-1})
        for local in sampled:
            shot = shots[local]
            index = next(i for i, s in enumerate(board["shots"], 1) if s["id"] == shot["id"])
            directory = self.root / "shots" / f"{index:02d}"
            # Include the actual last frame of the last clip even for large casts.
            if local == sampled[-1]:
                tail = sorted((directory / "frames").glob("frame-*.png"))
                path = tail[-1] if tail else directory / "director-thumb.png"
            else:
                path = directory / "director-thumb.png"
            frames.append(path); labels.append(f"Actual clip {local+1}: " + ("end" if local == sampled[-1] else "middle"))
        if next_opening is not None:
            frames.append(next_opening); labels.append("Next scene planned opening (boundary)")
        key = receipt_key(clip_hashes=[file_hash(p) for p in outputs],
            reference_hashes=[file_hash(p) for p in frames if Path(p).is_file()],
            prompts=[json.dumps({"scene": scene, "nextScene": next_scene,
                "nextOpening": str(next_opening) if next_opening is not None else None,
                "style": self.plan.get("style"),
                "cast": self.plan.get("characters"), "bible": self.plan.get("filmBible"),
                "shot": {field: shot.get(field) for field in ("id", "prompt", "start", "action", "end", "camera", "stateBefore", "stateAfter", "motionIntent", "assetStates", "characterIds", "dialogue")}},
                sort_keys=True, ensure_ascii=False) for shot in shots], policy=self.policy,
            reviewer_model=getattr(self.service, "model", "local-director"),
            input_code_hash=file_hash(Path(__file__)) + ":" + file_hash(Path(__file__).with_name("film_quality.py")))
        receipt_path = self.root / "quality" / f"scene-{number:03d}-{key}.json"
        cached = load_receipt(receipt_path, key)
        if cached and (cached.get("available") or cached.get("modelAttempted")
                       or self.policy["reviewMode"] in ("fast", "off")
                       or any(i.get("severity") == "critical" for i in cached.get("issues", []))):
            cached.setdefault("observedAssetStates", {})
            self.receipts.append(cached)
            self.observed[str(number)] = cached.get("observedEndState")
            self._persist()
            return cached
        result = {"verdict": "unavailable", "available": False, "issues": [],
                  "observedEndState": None, "observedAssetStates": {},
                  "reason": "Scene outside review sample", "modelAttempted": False,
                  "motion": motion_reports}
        for shot, report in zip(shots, motion_reports):
            if report.get("lowMotion"):
                result["issues"].append({"severity": "warning", "shot": shot.get("title", ""),
                                         "issue": "Lite bevegelse må vurderes mot handlingen",
                                         "action": "review"})
        for shot in shots:
            if shot.get("dialogue"):
                index = next(i for i, s in enumerate(board["shots"], 1) if s["id"] == shot["id"])
                directory = self.root / "shots" / f"{index:02d}"
                check = audio_check(assemble.shot_clip(directory, shot) or directory / "clip.mp4", shot["dialogue"])
                if check["available"] and check["silent"]:
                    result["issues"].append({"severity": "critical", "issue": f"Ingen hørbar lyd: {shot['title']}", "action": "pause"})
        # Startup is an operational wait, separate from the bounded inference
        # budget.  A cold 27B may legitimately need several minutes.
        # Each selected call gets its own bounded inference window.  The
        # percentage policy is retained for reporting compatibility, but must
        # never silently disable a selected quality gate.
        allowance = 180.0
        if number-1 in self.selected and self.calls < self.policy["maxReviewCalls"]:
            if not callable(getattr(self.service, "complete_with_media", None)):
                result["reason"] = "No measured budget / visual reviewer available"
            else:
                started = time.monotonic()
                # Keep the explicit boundary tile: the default six-tile
                # preview would otherwise drop the next opening.
                sheet = contact_sheet(frames, max_images=len(frames), labels=labels)
                if sheet:
                    sheet_path = self.root / "quality" / f"scene-{number:03d}-sheet.jpg"
                    sheet_path.parent.mkdir(exist_ok=True)
                    sheet.save(sheet_path, quality=82)
                    self.set_phase(phase="review", detail=f"Kontrollerer scene {number} innen tidsbudsjettet")
                    self.calls += 1
                    result["modelAttempted"] = True
                    timing = {"startupSeconds": 0.0, "inferenceSeconds": 0.0,
                              "cleanupSeconds": 0.0}
                    def ask(images, context, timeout):
                        if self.cancel():
                            raise InterruptedError("Filmjobben ble stoppet")
                        released = time.monotonic()
                        self.release_images()
                        timing["cleanupSeconds"] += time.monotonic() - released
                        if self.cancel():
                            raise InterruptedError("Filmjobben ble stoppet")
                        if callable(self.prepare_language):
                            self.prepare_language()
                        ready_started = time.monotonic()
                        if not hasattr(self.service, "ensure_ready"):
                            raise RuntimeError("Visual reviewer cannot be started safely")
                        try:
                            self.service.ensure_ready(timeout=max(720.0, float(getattr(self.service, "startup_timeout", 720.0))))
                        finally:
                            timing["startupSeconds"] += time.monotonic() - ready_started
                        if self.cancel():
                            raise InterruptedError("Filmjobben ble stoppet")
                        inference_started = time.monotonic()
                        try:
                            return self.service.complete_with_media(
                                REVIEW_SYSTEM, json.dumps(context, ensure_ascii=False),
                                images=images, timeout=max(30.0, min(float(timeout), allowance)))
                        finally:
                            timing["inferenceSeconds"] += time.monotonic() - inference_started
                    try:
                        bible = self.plan.get("filmBible") or {}
                        allowed_assets = set()
                        for group in (bible.get("locations", []), bible.get("props", []),
                                      self.plan.get("characters", [])):
                            for asset in group or []:
                                if isinstance(asset, dict) and asset.get("id"):
                                    allowed_assets.add(str(asset["id"]))
                        for field in ("locationId", "propIds", "assetIds"):
                            value = scene.get(field, [])
                            value = value if isinstance(value, list) else [value]
                            allowed_assets.update(str(item) for item in value if item)
                        for shot in shots:
                            for asset in shot.get("assetStates", []) or []:
                                if isinstance(asset, dict) and asset.get("id"):
                                    allowed_assets.add(str(asset["id"]))
                        reviewed = review_sample(ask, [sheet_path], {
                            "scene": scene["title"], "imagesInOrder": labels,
                            "priorObservedState": self.observed.get(str(number-1)),
                            "totalClips": len(shots),
                            "shots": [{"clip": i+1, "title": shots[i]["title"],
                                       "prompt": shots[i]["prompt"][:4000],
                                       "start": shots[i].get("start"), "action": shots[i].get("action"),
                                       "end": shots[i].get("end"), "camera": shots[i].get("camera"),
                                       "assetStates": shots[i].get("assetStates")}
                                      for i in sampled],
                            "plannedEnd": scene.get("stateAfter") or scene["shots"][-1].get("end"),
                            "nextScene": (next_scene or {}).get("title") if isinstance(next_scene, dict) else None,
                            "nextOpeningState": (next_scene or {}).get("stateBefore") if isinstance(next_scene, dict) else None,
                            "plannedAssetStates": scene.get("assetStates", {}),
                            "nextAssetStates": (next_scene or {}).get("assetStates", {}),
                            "allowedAssetIds": sorted(allowed_assets)}, allowance)
                        audio_issues = result["issues"]
                        technical_motion = result["motion"]
                        result.update(reviewed)
                        if result.get("available"):
                            result["reason"] = "Sampled visual review"
                        elif reviewed.get("error"):
                            result["reason"] = reviewed["error"]
                        result["issues"] = audio_issues + result.get("issues", [])
                        result["motion"] = technical_motion
                    finally:
                        # Cleanup always runs, including cancellation and model
                        # failure.  A cleanup failure is a hard stop.
                        cleanup_started = time.monotonic()
                        cleanup_error = None
                        try:
                            self.release_language()
                            if callable(self.confirm_language_released):
                                self.confirm_language_released()
                        except BaseException as exc:
                            cleanup_error = exc
                        timing["cleanupSeconds"] += time.monotonic() - cleanup_started
                        for name, value in timing.items():
                            self.timings[name] += value
                        self.timings["reviewSeconds"] += time.monotonic() - started
                        self.used_seconds += time.monotonic() - started
                        result["reviewTiming"] = timing
                        if cleanup_error is not None:
                            raise RuntimeError(f"Review cleanup failed; refusing to continue: {cleanup_error}") from cleanup_error
                else:
                    result["reason"] = "Actual preview frames unavailable"
        speech_budget = min(8.0, production_seconds * self.policy["reviewBudgetFraction"] - self.used_seconds)
        if self.policy["reviewMode"] == "thorough" and number-1 in self.selected and self.transcriber and speech_budget >= 4:
            spoken = next((s for s in shots if s.get("dialogue")), None)
            if spoken:
                index = next(i for i,s in enumerate(board["shots"],1) if s["id"] == spoken["id"])
                directory = self.root / "shots" / f"{index:02d}"
                started = time.monotonic()
                check = audio_check(assemble.shot_clip(directory, spoken) or directory / "clip.mp4",
                    spoken["dialogue"], transcriber=self.transcriber, deadline=speech_budget)
                self.used_seconds += time.monotonic()-started
                result["speech"] = check
                if check["transcriptMatch"] is False:
                    result["issues"].append({"severity": "warning", "issue": "Talegjenkjenning avviker fra manus: " + spoken["title"], "action": "review"})
        if self.cancel():
            raise InterruptedError("Filmjobben ble stoppet")
        result.update(schema=SCHEMA_VERSION, key=key, scene=number)
        result.setdefault("observedAssetStates", {})
        result["boundary"] = {
            "nextScene": (next_scene or {}).get("title") if isinstance(next_scene, dict) else None,
            "nextOpeningProvided": next_opening is not None,
            "tailLabel": labels[-2] if next_opening is not None and len(labels) > 1 else labels[-1],
        }
        save_receipt(receipt_path, result)
        self.receipts.append(result)
        self.observed[str(number)] = result.get("observedEndState")
        self._persist()
        return result
