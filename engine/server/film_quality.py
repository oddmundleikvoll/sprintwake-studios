"""Cheap, durable quality gates for long-film production.

This module deliberately contains no model loading.  A caller may inject a
reviewer callback for a small contact sheet; otherwise quality checks remain
local and deterministic.  Planned and observed continuity are kept separate.
"""
from __future__ import annotations

import hashlib
import json
import os
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 2
_SEVERITIES = {"info", "warning", "critical"}
_ACTIONS = {"none", "review", "retry", "pause"}
_ISSUE_FIELDS = {"severity", "shot", "shotIndex", "issue", "action", "quote"}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()


def clean_policy(payload: dict[str, Any] | None = None,
                 settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize bounded review sampling; fast/off never start a reviewer.

    Balanced samples at most three setups; thorough at most twelve. Startup
    and inference have separate deadlines. The legacy budget fraction remains
    in saved settings for the optional already-running ASR check only.
    """
    source = {}
    if isinstance(settings, dict): source.update(settings)
    if isinstance(payload, dict): source.update(payload)
    mode = str(source.get("reviewMode", source.get("quality", "balanced"))).lower()
    if mode not in {"fast", "balanced", "thorough", "off", "sampled"}:
        raise ValueError("reviewMode must be fast, balanced, thorough, off, or sampled")
    budget = {"fast": 0.0, "off": 0.0, "balanced": .05,
              "sampled": .05, "thorough": .10}[mode]
    strength = source.get("endAnchorStrength", .65)
    try:
        strength = float(strength)
        if not math.isfinite(strength): raise ValueError
        strength = max(0.0, min(1.0, strength))
    except (TypeError, ValueError):
        raise ValueError("endAnchorStrength must be a finite number from 0 to 1")
    speech = str(source.get("speechMode", "native")).lower()
    if speech not in {"native", "recording"}:
        raise ValueError("speechMode must be native or recording")
    for name in ("automaticEndAnchors", "prepareOnly", "requireImageApproval"):
        if name in source and not isinstance(source[name], bool):
            raise ValueError(f"{name} must be boolean")
    return {"schema": SCHEMA_VERSION, "reviewMode": mode,
            "reviewBudgetFraction": budget,
            "automaticEndAnchors": bool(source.get("automaticEndAnchors", False)),
            "requireImageApproval": bool(source.get("requireImageApproval", True)),
            "endAnchorStrength": strength, "speechMode": speech,
            "prepareOnly": bool(source.get("prepareOnly", False)),
            "maxReviewCalls": 0 if mode in {"fast", "off"} else (3 if mode in {"balanced", "sampled"} else 12)}


def review_scene_indices(scene_count: int, policy: dict[str, Any] | None = None) -> list[int]:
    """Sample across the whole film, including its beginning and end."""
    if scene_count <= 0 or clean_policy(policy).get("reviewMode") in {"fast", "off"}:
        return []
    limit = min(scene_count, clean_policy(policy).get("maxReviewCalls", 3))
    if limit == 1:
        return [0]
    return sorted({round(i*(scene_count-1)/(limit-1)) for i in range(limit)})


def contact_sheet(paths: Iterable[str | os.PathLike[str]], *, max_images: int = 6,
                  max_size: int = 512, labels: Iterable[str] | None = None):
    """Make a small PIL contact sheet from existing images; returns ``None`` if unavailable."""
    try:
        from PIL import Image, ImageOps, ImageDraw
    except ImportError:
        return None
    images = []
    labels = list(labels or [])
    for index, path in enumerate(list(paths)[:max_images]):
        try:
            with Image.open(path) as src:
                image = ImageOps.exif_transpose(src).convert("RGB"); image.thumbnail((max_size, max_size - 28))
                canvas = Image.new("RGB", (max_size, max_size), "black")
                canvas.paste(image, ((max_size-image.width)//2, 28+(max_size-28-image.height)//2))
                if index < len(labels):
                    draw = ImageDraw.Draw(canvas)
                    draw.text((6, 5), str(labels[index])[:72], fill="white")
                images.append(canvas)
        except (OSError, ValueError):
            continue
    if not images: return None
    sheet = Image.new("RGB", (max_size * 2, max_size * ((len(images)+1)//2)), "black")
    for i, image in enumerate(images): sheet.paste(image, ((i % 2)*max_size, (i//2)*max_size))
    return sheet


def parse_verdict(raw: str | dict[str, Any] | None,
                 *, allowed_asset_ids: set[str] | None = None) -> dict[str, Any]:
    """Keep only a safe, structured reviewer result; malformed replies are unavailable."""
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except (TypeError, ValueError): raw = {}
    raw = raw if isinstance(raw, dict) else {}
    issues = []
    raw_issues = raw.get("issues", [])
    if not isinstance(raw_issues, list): raw_issues = []
    for item in raw_issues[:32]:
        if not isinstance(item, dict): continue
        issue = {k: item[k] for k in _ISSUE_FIELDS if k in item}
        if issue.get("severity") not in _SEVERITIES: issue["severity"] = "warning"
        if issue.get("action") not in _ACTIONS: issue["action"] = "review"
        if not isinstance(issue.get("issue"), str): issue["issue"] = "Unspecified issue"
        for field in ("issue", "quote", "shot"):
            if field in issue:
                if not isinstance(issue[field], str): issue.pop(field, None)
                else: issue[field] = issue[field][:2000]
        if isinstance(issue.get("shotIndex"), bool) or not isinstance(issue.get("shotIndex", 0), int) or not 0 <= issue.get("shotIndex", 0) <= 100000:
            issue.pop("shotIndex", None)
        issues.append(issue)
    observed = raw.get("observedEndState")
    def _simple(value, depth=0):
        if depth > 3: return None
        if isinstance(value, str): return value[:4000]
        if isinstance(value, float): return value if math.isfinite(value) else None
        if isinstance(value, (int, bool)) or value is None: return value
        if isinstance(value, dict) and len(value) <= 32 and all(isinstance(k, str) for k in value):
            return {k: _simple(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list) and len(value) <= 32: return [_simple(v, depth + 1) for v in value]
        return None
    observed = _simple(observed)
    # Asset observations are deliberately narrower than the human end-state:
    # the model may only report physical continuity for known location/prop
    # resources, never invent a new asset or an arbitrary nested document.
    observed_assets: dict[str, str] = {}
    raw_assets = raw.get("observedAssetStates")
    if isinstance(raw_assets, dict):
        for asset_id, state in list(raw_assets.items())[:64]:
            if not isinstance(asset_id, str) or not asset_id or len(asset_id) > 160:
                continue
            if allowed_asset_ids is not None and asset_id not in allowed_asset_ids:
                continue
            if not isinstance(state, str) or not state.strip():
                continue
            if state.strip().lower() in {"unknown", "uncertain", "not visible", "not observed"}:
                continue
            observed_assets[asset_id] = state.strip()[:1000]
    verdict = raw.get("verdict") if isinstance(raw.get("verdict"), str) else "unavailable"
    verdict = verdict.lower() if verdict in {"allow", "pass", "review", "pause"} else "unavailable"
    return {"verdict": verdict, "issues": issues,
            "observedEndState": observed if verdict != "unavailable" else None,
            "observedAssetStates": observed_assets if verdict != "unavailable" else {},
            "available": verdict != "unavailable"}


def review_sample(reviewer: Callable[..., Any] | None, images: list[Any], context: dict[str, Any],
                  timeout: float = 60.0) -> dict[str, Any]:
    if reviewer is None: return {"verdict": "unavailable", "issues": [], "observedEndState": None,
                                 "observedAssetStates": {}, "available": False}
    allowed = context.get("allowedAssetIds") if isinstance(context, dict) else None
    allowed = set(allowed) if isinstance(allowed, (list, tuple, set)) else None
    try: return parse_verdict(reviewer(images, context, timeout), allowed_asset_ids=allowed)
    except InterruptedError: raise
    except Exception as exc: return {"verdict": "unavailable", "issues": [], "observedEndState": None,
                                     "observedAssetStates": {}, "available": False, "error": str(exc)}


def receipt_key(*, clip_hashes: list[str], reference_hashes: list[str], prompts: list[str],
                policy: dict[str, Any], reviewer_model: str | None = None,
                model_identity: Any = None, input_code_hash: str | None = None) -> str:
    return _hash({"clips": clip_hashes, "references": reference_hashes, "prompts": prompts,
                  "policy": policy, "reviewerModel": reviewer_model, "modelIdentity": model_identity,
                  "inputCodeHash": input_code_hash, "schema": SCHEMA_VERSION})


def load_receipt(path: str | os.PathLike[str], key: str) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text())
        return value if value.get("key") == key and value.get("schema") in {1, SCHEMA_VERSION} else None
    except (OSError, ValueError, AttributeError): return None


def save_receipt(path: str | os.PathLike[str], receipt: dict[str, Any]) -> None:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    from .ltx_media import atomic_json
    atomic_json(target, receipt)


def continuity_ledger(plan: dict[str, Any], observed: dict[str, Any] | None = None,
                     receipts: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Durably represent plan and observed states without inferring either."""
    return {"schema": SCHEMA_VERSION, "planned": plan, "observed": observed or {},
            "receipts": list(receipts)}


class ProductionMetrics:
    def __init__(self, existing: dict[str, Any] | None = None):
        self.phases: dict[str, dict[str, Any]] = dict((existing or {}).get("phases", {})); self._active = {}
    def start(self, phase: str) -> None: self._active[phase] = time.monotonic()
    def finish(self, phase: str, *, reused: bool = False, memory_rss: int | None = None,
               disk_bytes: int | None = None) -> dict[str, Any]:
        elapsed = max(0.0, time.monotonic() - self._active.pop(phase, time.monotonic()))
        old = self.phases.get(phase, {})
        self.phases[phase] = {"elapsed": old.get("elapsed", 0.0) + elapsed, "reused": bool(reused),
                              "memoryRSS": memory_rss, "diskBytes": disk_bytes}
        return self.phases[phase]
    def as_dict(self) -> dict[str, Any]: return {"schema": SCHEMA_VERSION, "phases": dict(self.phases)}
    def save(self, path: str | os.PathLike[str]) -> None: save_receipt(path, self.as_dict())


def audio_check(path: str | os.PathLike[str], script: str = "", *, transcriber: Callable[..., Any] | None = None,
                deadline: float = 10.0) -> dict[str, Any]:
    """Cheap RMS/silence check with optional injected transcription."""
    result = {"available": False, "silent": None, "rms": None, "transcriptMatch": None}
    try:
        import av
        with av.open(str(path)) as container:
            import numpy as np
            energy, count, duration = 0.0, 0, 0.0
            for frame in container.decode(audio=0):
                pcm = frame.to_ndarray()
                scale = float(max(abs(np.iinfo(pcm.dtype).min), np.iinfo(pcm.dtype).max)) if np.issubdtype(pcm.dtype, np.integer) else 1.0
                arr = pcm.astype("float64") / scale
                energy += float(np.square(arr).sum()); count += arr.size
                duration += float(frame.samples or 0) / float(frame.sample_rate or 48000)
                if duration >= 10.0: break
            if count:
                rms = math.sqrt(energy / count)
                result.update(available=True, rms=rms, silent=rms < 1e-4)
    except Exception: pass
    if transcriber and script:
        try:
            norm = lambda s: re.findall(r"[\w]+", str(s).lower())
            got = norm(transcriber(path, deadline=deadline)); want = norm(script)
            from difflib import SequenceMatcher
            result["transcriptMatch"] = bool(got and want and min(len(got),len(want))/max(len(got),len(want)) >= .85
                and SequenceMatcher(None, got, want).ratio() >= .85)
        except Exception: result["transcriptMatch"] = None
    return result


def motion_check(path: str | os.PathLike[str], intent: str = "action", *,
                 max_samples: int = 24) -> dict[str, Any]:
    """Measure low-resolution temporal change as an advisory technical signal.

    This deliberately does not recognize actions, identity, or continuity.  A
    reviewer must decide whether a quiet shot is wrong; ``hold`` shots never
    receive a low-motion warning.  The threshold is a conservative engineering
    heuristic, not a validated visual-quality score.
    """
    result: dict[str, Any] = {"available": False, "intent": intent,
                              "samples": 0, "meanDifference": None,
                              "p95Difference": None, "lowMotion": False,
                              "method": "PyAV RGB temporal difference advisory"}
    if intent not in {"action", "hold"}:
        intent = "action"
        result["intent"] = intent
    try:
        import av
        import numpy as np
        previous = None
        differences: list[float] = []
        with av.open(str(path)) as container:
            stream = next((item for item in container.streams if item.type == "video"), None)
            if stream is None:
                return result
            total = int(stream.frames or 0)
            if total <= 0 and stream.duration and stream.average_rate:
                total = max(0, int(round(float(stream.duration * stream.time_base) * float(stream.average_rate))))
            selected = None
            if total > 0:
                selected = {round(i * (total - 1) / max(1, max_samples))
                            for i in range(max_samples + 1)}
            frame_number = 0
            for frame in container.decode(stream):
                if selected is not None and frame_number not in selected:
                    frame_number += 1
                    continue
                image = frame.to_ndarray(format="rgb24")
                image = image[:: max(1, image.shape[0] // 96), :: max(1, image.shape[1] // 96)]
                current = image.astype("float32") / 255.0
                if previous is not None:
                    differences.append(float(np.mean(np.abs(current - previous))))
                previous = current
                frame_number += 1
                if selected is None and len(differences) >= max(1, int(max_samples)):
                    break
        if not differences:
            return result
        result.update(available=True, samples=len(differences),
                      meanDifference=float(np.mean(differences)),
                      p95Difference=float(np.percentile(differences, 95)))
        result["lowMotion"] = intent == "action" and result["meanDifference"] < 0.001
    except Exception as exc:
        result["error"] = str(exc)
    return result
