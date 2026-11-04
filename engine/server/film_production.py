"""Local production artifacts: reusable references, timed boards and measurements."""
from __future__ import annotations

import html
import hashlib
import json
import math
import resource
import re
import shutil
import time
import uuid
from pathlib import Path

from .ltx_media import atomic_json, digest, file_hash
from .ltx_anchors import image_path


def archive_take(directory, archive_root, metadata=None):
    """Create/reuse an immutable, content-addressed snapshot of a take."""
    source_raw = Path(directory)
    root_raw = Path(archive_root)
    if source_raw.is_symlink() or root_raw.is_symlink():
        raise ValueError("take archive refuses symlink directories")
    # Only inspect the task-controlled portion of each path. macOS /var itself
    # may legitimately be a system symlink, while a project/takes symlink must
    # never redirect a snapshot outside the project.
    import os
    common = Path(os.path.commonpath((source_raw.absolute(), root_raw.absolute())))
    for raw in (source_raw.absolute(), root_raw.absolute()):
        cursor = raw
        while cursor != common:
            if cursor.is_symlink():
                raise ValueError("take archive refuses symlink parents")
            cursor = cursor.parent
    source = source_raw.resolve()
    root = root_raw.resolve()
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("active take and archive directories must be separate")
    if not source.is_dir():
        raise ValueError("take directory must be a real directory")
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("take archive refuses symlinks")
        if path.is_file():
            files.append([path.relative_to(source).as_posix(), file_hash(path), path.stat().st_size])
    clean_metadata = metadata if isinstance(metadata, dict) else {}
    manifest_base = {"version": 1, "files": files, "metadata": clean_metadata}
    snapshot = hashlib.sha256(json.dumps(manifest_base, sort_keys=True,
                                        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    target = root / snapshot
    root.mkdir(parents=True, exist_ok=True)

    def verify(path):
        manifest_path = path / "archive.json"
        if path.is_symlink() or not manifest_path.is_file():
            return False
        try:
            saved = json.loads(manifest_path.read_text())
            if saved != {**manifest_base, "snapshot": snapshot}:
                return False
            for rel, digest_value, size in files:
                item = path / rel
                if item.is_symlink() or not item.is_file() or item.stat().st_size != size:
                    return False
                if file_hash(item) != digest_value:
                    return False
            return True
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    if target.exists():
        if verify(target):
            return target
        raise ValueError("existing take archive is corrupted")
    pending = root / f".{snapshot}.{uuid.uuid4().hex}.pending"
    try:
        if pending.exists():
            shutil.rmtree(pending)
        pending.mkdir(parents=True)
        for rel, _, _ in files:
            dest = pending / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, dest, follow_symlinks=False)
        atomic_json(pending / "archive.json", {**manifest_base, "snapshot": snapshot})
        if not verify(pending):
            raise ValueError("take changed during archiving; original files were retained")
        pending.replace(target)
    except Exception:
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        raise
    return target


def validate_bible_references(bible, data_dir):
    """Resolve every declared bible image before any inference starts."""
    if not isinstance(bible, dict):
        return
    for group in ("locations", "props"):
        for entry in bible.get(group, []) or []:
            if not isinstance(entry, dict) or not entry.get("referenceImage"):
                continue
            try:
                image_path(entry["referenceImage"], data_dir, verify=True)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Ugyldig filmbibelbilde for {entry.get('id', group[:-1])}: {exc}") from exc


def bible_references(scene, bible, data_dir):
    """Return verified, labeled environment/prop refs for a scene.

    Explicit bible references are authoritative and are returned before the
    generated ReferenceBank refs.  Invalid, missing, external, or unknown
    references are ignored here; strict image-plan validation reports them.
    """
    if not isinstance(scene, dict) or not isinstance(bible, dict):
        return []
    validate_bible_references(bible, data_dir)
    out, seen = [], set()
    wanted = []
    location = scene.get("locationId")
    if location:
        wanted.append(("locations", location, "location"))
    for prop in scene.get("propIds") or []:
        if prop:
            wanted.append(("props", prop, "prop"))
    for group, key, label in wanted:
        entry = next((x for x in bible.get(group, []) if isinstance(x, dict) and x.get("id") == key), None)
        ref = entry.get("referenceImage") if entry else None
        if not isinstance(ref, dict):
            continue
        try:
            path = image_path(ref, data_dir, verify=True).resolve()
        except (OSError, ValueError):
            continue
        if path in seen:
            continue
        desired_state = (scene.get("assetStates") or {}).get(key, "base")
        reference_state = entry.get("referenceState", "base")
        if reference_state != "base" and reference_state != desired_state:
            continue
        seen.add(path)
        name = (entry.get("title") or key) if entry else key
        out.append((path, f"{label}: {name}; reference state {reference_state}; "
                           f"preserve design/layout only; planned state {desired_state} is authoritative; "
                           "do not copy people or framing"))
    return out


class ReferenceBank:
    """Reuse existing scene frames for recurring locations and props, without extra renders."""

    def __init__(self, root, style):
        self.root = Path(root)
        self.path = self.root / "film-assets.json"
        try:
            saved = json.loads(self.path.read_text())
        except (OSError, ValueError):
            saved = {}
        self.data = saved if isinstance(saved, dict) and saved.get("style") == digest(style) else {
            "version": 1, "style": digest(style), "locations": {}, "props": {}, "observed": {}}

    def _keys(self, scene):
        # A free-form setting is not a stable identity.  Reusing it across
        # scenes can silently carry a prop/layout into an unrelated set.
        location = scene.get("locationId")
        states = scene.get("assetStates") or {}
        entries = ([('locations', location)] if location else []) + [
            ("props", key) for key in scene.get("propIds", []) if key]
        return [(kind, key + "@" + digest(states.get(key, "base"))[:16]) for kind, key in entries]

    def references(self, scene, before_scene=None):
        refs, seen = [], set()
        for kind, key in self._keys(scene):
            entry = self.data.get(kind, {}).get(key)
            if not entry:
                continue
            if before_scene is not None and (type(entry.get("sourceScene")) is not int
                                            or entry["sourceScene"] >= before_scene):
                continue
            path = (self.root / entry["file"]).resolve()
            if (not path.is_relative_to(self.root.resolve()) or not path.is_file()
                    or path in seen or file_hash(path) != entry.get("hash")):
                continue
            seen.add(path)
            refs.append((path, f"{kind}: {key}; preserve set layout / object design only; "
                         "do not copy people, their poses or framing"))
        return refs

    def remember(self, scene, image, scene_number=None):
        image = Path(image).resolve()
        if not image.is_relative_to(self.root.resolve()):
            # A manual anchor may live in another project; retain a local asset copy.
            target = self.root / "refs" / "asset-bank" / (file_hash(image) + image.suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(image, target)
            image = target
        entry = {"file": image.relative_to(self.root.resolve()).as_posix(), "hash": file_hash(image),
                 "sourceScene": scene_number}
        for kind, key in self._keys(scene):
            old = self.data[kind].get(key)
            if (not old or scene_number is not None and
                    (type(old.get("sourceScene")) is not int or old["sourceScene"] >= scene_number)):
                self.data[kind][key] = entry
        atomic_json(self.path, self.data)

    def observe(self, scene_number, state, tail, scene=None, asset_states=None):
        if asset_states is None and isinstance(state, dict):
            asset_states = state.get("observedAssetStates")
        tail_path = Path(tail).resolve() if tail else None
        if tail_path and tail_path.is_file() and not tail_path.is_relative_to(self.root.resolve()):
            target = self.root / "refs" / "observed" / (file_hash(tail_path) + tail_path.suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(tail_path, target)
            tail_path = target.resolve()
        self.data["observed"][str(scene_number)] = {
            # Preserve the reviewer narrative verbatim.  Older code dropped
            # string observations, making later continuity checks blind.
            "state": state if isinstance(state, (dict, str)) else None,
            "locationId": (scene or {}).get("locationId"),
            "propIds": list((scene or {}).get("propIds") or []),
            "assetStates": dict((scene or {}).get("assetStates") or {}),
            "observedAssetStates": {str(k): v for k, v in (asset_states or {}).items()
                                    if isinstance(k, str) and isinstance(v, str)},
            "tail": (tail_path.relative_to(self.root.resolve()).as_posix()
                     if tail_path and tail_path.is_file() else None),
            "tailHash": file_hash(tail_path) if tail_path and tail_path.is_file() else None}
        atomic_json(self.path, self.data)

    def continuity_reference(self, scene, before_scene=None):
        """Return a verified prior scene tail when its set/state is compatible."""
        location = scene.get("locationId")
        if not location:
            return None
        states = scene.get("assetStates") or {}
        observed = self.data.get("observed", {})
        keys = sorted((int(k), v) for k, v in observed.items() if str(k).isdigit())
        for scene_number, item in reversed(keys):
            if before_scene is not None and scene_number >= before_scene:
                continue
            if not isinstance(item, dict) or not item.get("tail") or item.get("locationId") != location:
                continue
            prior_props = set(item.get("propIds") or [])
            props = set(scene.get("propIds") or [])
            state_keys = prior_props | props | ({location} if location else set())
            if prior_props != props:
                continue
            observed_states = item.get("observedAssetStates")
            if not isinstance(observed_states, dict):
                observed_states = {}
            # Only an actually observed value can disqualify continuity;
            # planned metadata and unknown state are never evidence.
            planned_states = item.get("assetStates")
            if not isinstance(planned_states, dict):
                planned_states = {}
            if any(planned_states.get(k, "base") != states.get(k, "base")
                   for k in planned_states):
                continue
            if any(k in observed_states and observed_states[k] != states.get(k, "base")
                   for k in state_keys):
                continue
            path = (self.root / item["tail"]).resolve()
            if path.is_relative_to(self.root.resolve()) and path.is_file() and file_hash(path) == item.get("tailHash"):
                return path
        return None


def write_animatic(root, board, plan):
    """An immediately playable timed reel; reuses stills/audio and performs no inference/encoding."""
    root = Path(root)
    cards = []
    for index, shot in enumerate(board["shots"], 1):
        start = shot.get("startRef") or {}
        closing = next((a.get("ref", {}) for a in shot.get("imageAnchors", [])
                        if a.get("frame") == "last"), shot.get("endRef") or {})
        if start.get("kind") == "chain" and cards:
            start = {"url": cards[-1]["end"] or cards[-1]["start"]}
        audio = root / "shots" / f"{index:02d}" / "dialogue.wav"
        local_url = lambda ref: ref.get("url", "") if isinstance(ref.get("url"), str) and ref["url"].startswith("/media/") else ""
        cards.append({"title": shot["title"], "seconds": shot["frames"] / 24,
                      "start": local_url(start), "end": local_url(closing),
                      "dialogue": shot.get("dialogue", ""),
                      "audio": f"shots/{index:02d}/dialogue.wav" if audio.is_file() else "",
                      "scene": shot.get("directorScene")})
    payload = json.dumps(cards, ensure_ascii=False).replace("<", "\\u003c")
    title = html.escape(board.get("name") or plan.get("title") or "Animatic")
    source = """<!doctype html><html lang="no"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ – animatic</title><style>
body{margin:0;background:#17191e;color:#eee;font:16px system-ui;text-align:center}
main{max-width:960px;margin:auto;padding:1rem}img{width:100%;height:65vh;object-fit:contain;background:#08090b}
button,input{font:inherit;margin:.5rem}input{width:70%}#line{min-height:3em}small{color:#bfc3cc}
</style><main><h1>__TITLE__ – animatic</h1><img id="frame" alt="Storyboardbilde">
<div id="shot"></div><p id="line"></p><button id="play">Spill</button>
<input id="seek" type="range" min="0" step="0.01" value="0" aria-label="Tid"><span id="clock"></span>
<p><small>Tidsatt storyboard før video. Opptak spilles når de finnes; øvrige replikker vises som tekst.
Dette er en forhåndsvisning, ikke en visuell godkjenning.</small></p><audio id="audio"></audio></main>
<script>const cards=__CARDS__; const offsets=[];let total=0;
for(const c of cards){offsets.push(total);total+=c.seconds}const el=id=>document.getElementById(id);
let t=0,playing=false,last=0,active=-1;el('seek').max=total;
function draw(){let i=cards.length-1;while(i>0&&offsets[i]>t)i--;const c=cards[i];if(!c)return;
const local=t-offsets[i],src=(local>c.seconds/2&&c.end)?c.end:c.start;
if(src)el('frame').src=src;else el('frame').removeAttribute('src');
el('shot').textContent=`Scene ${c.scene||''} · ${i+1}/${cards.length} · ${c.title}`;
el('line').textContent=c.dialogue;el('seek').value=t;el('clock').textContent=`${t.toFixed(1)} / ${total.toFixed(1)} s`;
if(active!==i){el('audio').pause();active=i;el('audio').src=c.audio||'';
if(playing&&c.audio){el('audio').currentTime=local;el('audio').play().catch(()=>{})}}}
el('play').onclick=()=>{playing=!playing;el('play').textContent=playing?'Pause':'Spill';last=performance.now();
if(playing&&cards[active]?.audio){el('audio').currentTime=t-offsets[active];el('audio').play().catch(()=>{})}else el('audio').pause()};
el('seek').oninput=e=>{t=Number(e.target.value);active=-1;draw()};
function tick(now){if(playing){t=Math.min(total,t+(now-last)/1000);if(t>=total){playing=false;el('audio').pause();el('play').textContent='Spill'}draw()}last=now;requestAnimationFrame(tick)}
draw();requestAnimationFrame(tick);</script></html>""".replace("__TITLE__", title).replace("__CARDS__", payload)
    target = root / "animatic.html"
    temporary = root / ".animatic.html.pending"
    temporary.write_text(source)
    temporary.replace(target)
    atomic_json(root / "animatic.json", {"version": 1, "seconds": total_seconds(cards), "cards": cards})
    return target


def total_seconds(cards):
    return sum(c["seconds"] for c in cards)


def process_peak_bytes():
    # macOS reports bytes; Linux reports KiB. This is editor RSS, not unified GPU memory.
    import sys
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)


class FilmTimings:
    """Measured local scene work, separate from ordinary single-shot estimates."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            payload = json.loads(self.path.read_text())
            rows = payload.get("scenes", []) if isinstance(payload, dict) else []
            self.rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        except (OSError, ValueError):
            self.rows = []

    @staticmethod
    def _positive_number(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0)

    @classmethod
    def _positive_frames(cls, value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @staticmethod
    def _qualified_result(result, client):
        if result.get("legacy") or result.get("reused"):
            return False
        try:
            from .film_comfy import ComfyFilmClient
            if not isinstance(client, ComfyFilmClient):
                return False
        except ImportError:
            return False
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            return False
        movie = result.get("movie")
        shots = result.get("shots")
        if not isinstance(movie, (str, Path)) or not Path(movie).is_file() or not isinstance(shots, list) or not shots:
            return False
        return all(isinstance(shot, dict) and FilmTimings._positive_frames(shot.get("frames"))
                   and isinstance(shot.get("video"), (str, Path)) and Path(shot["video"]).is_file()
                   and isinstance(shot.get("audio"), (str, Path)) and Path(shot["audio"]).is_file()
                   for shot in shots)

    def record(self, frames, seconds, *, result=None, client=None, reused=False):
        if (not reused and self._positive_frames(frames) and self._positive_number(seconds)
                and isinstance(result, dict)
                and self._qualified_result(result, client)):
            self.rows.append({"frames": frames, "seconds": round(seconds, 3),
                              "geometry": "384x256", "model": "ltx-2.5-q8",
                              "source": "comfy-film-client", "runId": result["run_id"], "at": time.time()})
            atomic_json(self.path, {"scenes": self.rows[-100:]})

    def estimate(self, frames):
        import statistics
        rows = [r for r in self.rows[-10:] if r.get("geometry") == "384x256"
                and r.get("model") == "ltx-2.5-q8" and r.get("source") == "comfy-film-client"
                and isinstance(r.get("runId"), str) and re.fullmatch(r"[0-9a-f]{32}", r["runId"])
                and self._positive_frames(r.get("frames"))
                and self._positive_number(r.get("seconds"))]
        rates = [r["seconds"] / r["frames"] for r in rows]
        return round(statistics.median(rates) * frames) if rows and self._positive_frames(frames) else None
