"""Model-free image-plan manifests and approval matching.

The manifest is a stable description of the planned image inputs.  It is
deliberately separate from render status and server revisions so an approval
cannot be forged by editing a storyboard page or invalidated by a generated
chain preview URL changing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .ltx_anchors import image_path


MANIFEST_VERSION = 1
_SHOT_FIELDS = ("start", "action", "end", "camera", "locationId", "stateBefore",
                "stateAfter", "motionIntent", "propIds", "assetStates", "prompt",
                "frames", "seed", "characterIds", "dialogue", "soundNote",
                "dialogueStyle", "dialogueLanguage", "speakerId", "dialogueSource")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def _decoded_hash(path: Path) -> str:
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        digest = hashlib.sha256()
        digest.update(f"{image.width}x{image.height}:RGB:".encode())
        digest.update(image.tobytes())
        return digest.hexdigest()


def _stable_ref(ref: Any) -> dict[str, Any] | None:
    if not isinstance(ref, dict):
        return None
    if ref.get("kind") == "chain":
        return {"kind": "chain", "from": ref.get("from") or ref.get("shotId"),
                "frame": ref.get("frame", "last")}
    return {key: ref[key] for key in ("kind", "path", "url", "role", "tag", "label")
            if isinstance(ref.get(key), str)}


def image_plan(store: Any, slug: str, board: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Create a digest-bound image plan from existing local references.

    ``store`` must expose ``data_dir``; ``slug`` is retained for callers and
    diagnostics but is not included in the digest. No path supplied by a page
    is opened directly: all references pass through ``ltx_anchors.image_path``.
    """
    del slug
    data_dir = Path(store.data_dir)
    board_order = [shot for shot in board.get("shots") or [] if isinstance(shot, dict)]
    planned_order = [shot for scene in plan.get("scenes") or []
                     for shot in (scene.get("shots") or []) if isinstance(shot, dict)]
    if len(board_order) != len(planned_order):
        raise ValueError("Bildeplanen og storyboardet har ulikt antall klipp")
    images: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    warnings: list[str] = [value[:1000] for value in (plan.get("warnings") or [])
                           if isinstance(value, str)]
    for item in plan.get("planWarnings") or []:
        message = item.get("message") if isinstance(item, dict) else item
        if isinstance(message, str):
            warnings.append(message[:1000])
    canonical_shots: list[dict[str, Any]] = []
    canonical_scenes: list[dict[str, Any]] = []
    for scene_index, scene in enumerate(plan.get("scenes") or [], 1):
        if not isinstance(scene, dict):
            continue
        scene_shots = scene.get("shots") or []
        warnings.extend(value[:1000] for value in (scene.get("warnings") or []) if isinstance(value, str))
        canonical_scenes.append({key: scene.get(key) for key in
                                 ("title", "setting", "imagePrompt", "locationId", "propIds",
                                  "assetStates", "stateBefore", "stateAfter") if key in scene})
        for shot_index, planned in enumerate(scene_shots, 1):
            if not isinstance(planned, dict):
                continue
            ordered_index = sum(len(s.get("shots") or []) for s in (plan.get("scenes") or [])[:scene_index - 1]) + shot_index - 1
            planned_id = planned.get("id") or planned.get("shotId")
            source = board_order[ordered_index]
            shot_id = str(source.get("id") or planned_id or f"scene-{scene_index}-{shot_index}")
            if planned_id and str(planned_id) != shot_id:
                raise ValueError(f"Klipprekkefølgen avviker ved klipp {shot_id}")
            merged = {**planned, **source}
            canonical_shot = {key: merged.get(key) for key in _SHOT_FIELDS if key in merged}
            canonical_shot["id"] = shot_id
            canonical_shot["scene"] = scene_index

            start = merged.get("startRef")
            if start is None:
                start = merged.get("imagePromptRef")
            chain_start = isinstance(start, dict) and start.get("kind") == "chain"
            anchors = merged.get("imageAnchors") if isinstance(merged.get("imageAnchors"), list) else []
            has_frame_zero_anchor = any(isinstance(item, dict) and item.get("frame") == 0 for item in anchors) if isinstance(merged.get("imageAnchors"), list) else False
            if shot_index == 1 and not scene.get("chainStart") and (start is None or chain_start) and not has_frame_zero_anchor:
                raise ValueError(f"Mangler startbilde for første klipp i scene {scene_index}")
            refs = [("start", start), ("end", merged.get("endRef"))]
            canonical_shot["anchors"] = [{"ref": _stable_ref(item.get("ref")),
                                           "frame": item.get("frame"),
                                           "strength": item.get("strength")}
                                          for item in anchors if isinstance(item, dict)]
            canonical_shots.append(canonical_shot)
            refs.extend(("anchor", item.get("ref")) for item in anchors if isinstance(item, dict))
            for kind, ref in refs:
                stable = _stable_ref(ref)
                if stable is None or stable.get("kind") == "chain":
                    if stable and stable.get("kind") == "chain":
                        canonical_shot.setdefault("chainRefs", []).append(stable)
                    continue
                try:
                    path = image_path(ref, data_dir, verify=True)
                    content_hash = _decoded_hash(path)
                except (OSError, ValueError) as exc:
                    raise ValueError(f"Ugyldig {kind}-bilde for klipp {shot_id}: {exc}") from exc
                images.append({"shotId": shot_id, "title": merged.get("title", ""),
                               "kind": kind, "ref": stable, "frame": next((item.get("frame") for item in anchors if kind == "anchor" and item.get("ref") == ref), None),
                               "strength": next((item.get("strength") for item in anchors if kind == "anchor" and item.get("ref") == ref), None),
                               "hash": content_hash})
            if not chain_start and start is None:
                warnings.append(f"Klipp {shot_id} har ikke et eksplisitt startbilde")

    film_bible = plan.get("filmBible") if isinstance(plan.get("filmBible"), dict) else {}
    # Bible refs are first-class inputs: resolve and hash the actual pixels so
    # changing an environment/prop image invalidates approval.  Validate all
    # declared refs, including ones not used by the first scene, so a forged
    # or stale path cannot hide in a saved plan.
    for group in ("locations", "props"):
        for entry in film_bible.get(group) or []:
            if not isinstance(entry, dict) or not entry.get("referenceImage"):
                continue
            ref = entry.get("referenceImage")
            try:
                path = image_path(ref, data_dir, verify=True)
                content_hash = _decoded_hash(path)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Ugyldig filmbibelbilde for {entry.get('id', group[:-1])}: {exc}") from exc
            assets.append({"id": entry.get("id", ""), "kind": group[:-1],
                           "referenceState": entry.get("referenceState", "base"),
                           "ref": _stable_ref(ref), "hash": content_hash})
    cast = []
    cards = []
    plan_cast = plan.get("characters") if isinstance(plan.get("characters"), list) else []
    for character_index, character in enumerate(board.get("characters") or []):
        if not isinstance(character, dict):
            continue
        entry = {key: character.get(key) for key in ("id", "name", "description")
                 if character.get(key) is not None}
        image = character.get("image")
        if image is not None:
            try:
                path = image_path(image, data_dir, verify=True)
                entry["imageHash"] = _decoded_hash(path)
                cards.append({"name": character.get("name", ""), "ref": _stable_ref(image),
                              "hash": entry["imageHash"]})
            except (OSError, ValueError) as exc:
                raise ValueError(f"Ugyldig karakterbilde for {character.get('name', 'rolle')}: {exc}") from exc
        cast.append(entry)
    plan_cast_clean = [{key: character.get(key) for key in ("id", "name", "description", "image")
                       if character.get(key) is not None}
                      for character in plan_cast if isinstance(character, dict)]
    canonical = {
        "title": plan.get("title"),
        "style": plan.get("style"),
        "board": {key: board.get(key) for key in ("sceneDescription", "renderStyle", "soundscape")},
        "continuationAnchor": bool((board.get("filmAutomation") or {}).get("continuationAnchor", False)),
        "filmBible": film_bible,
        "scenes": canonical_scenes,
        "cast": cast,
        "planCast": plan_cast_clean,
        "shots": canonical_shots,
        "images": images,
        "assets": assets,
    }
    return {"version": MANIFEST_VERSION, "digest": _digest(canonical),
            "images": images, "assets": assets, "cards": cards,
            "warnings": list(dict.fromkeys(warnings))}


def approval_matches(manifest: dict[str, Any] | None, automation: dict[str, Any] | None) -> bool:
    """Return true only for an exact server-recorded image-plan digest."""
    return bool(isinstance(manifest, dict) and isinstance(automation, dict)
                and isinstance(manifest.get("digest"), str)
                and automation.get("imageApproval", {}).get("digest") == manifest["digest"])
