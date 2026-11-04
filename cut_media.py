"""Preserve takes and encode an editorial rough cut; never start inference."""
from __future__ import annotations

import copy
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from cut import FPS, choose_take, validate_clips, visible_clips
from workspace import IMAGE_EXTENSIONS, _atomic_text, _digest


from media_tools import executable


def probe(path):
    result = subprocess.run([executable("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-print_format", "json", str(path)],
                            capture_output=True, text=True, timeout=60, check=True)
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("Opptaket har ingen videostrøm.")
    duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
    if str(video.get("nb_frames", "")).isdigit() and int(video["nb_frames"]) > 0:
        rate = Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "24")
        if rate > 0:
            duration = float(int(video["nb_frames"]) / rate)
    if not math.isfinite(duration) or duration < 1 / FPS:
        raise ValueError("Opptakets faktiske varighet kunne ikke måles.")
    return {"frames": max(1, math.floor(duration * FPS + 1e-4)), "duration": duration,
            "width": int(video["width"]), "height": int(video["height"]),
            "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def preserve_take(store, video, image="", *, take_id="production", label="Produksjonsopptak"):
    """A content-addressed Input copy survives canonical render/adopt changes."""
    video = Path(video)
    digest = _digest(video)
    frozen = store.workspace.import_input(store.slug, video)
    if _digest(frozen) != digest:
        raise ValueError("Opptaket endret seg mens det ble bevart. Prøv igjen.")
    measured = probe(frozen)
    still = ""
    if not image:
        with tempfile.TemporaryDirectory(prefix="raven-take-poster-") as directory:
            poster = Path(directory) / "take-poster.png"
            subprocess.run([executable("ffmpeg"), "-v", "error", "-nostdin", "-y", "-i", str(frozen), "-frames:v", "1", "-vf", "scale=480:-2", str(poster)],
                           capture_output=True, check=True, timeout=60)
            still = str(store.workspace.import_input(store.slug, poster).relative_to(store.project))
    if image and Path(image).is_file():
        still = str(store.workspace.import_input(store.slug, image).relative_to(store.project))
    return {"id": str(take_id), "label": str(label), "video": str(frozen.relative_to(store.project)), "image": still}, measured["frames"]


def bind_production(store, doc, project):
    """Bind only previously unbound cards; never follow a new canonical take."""
    clips = copy.deepcopy(doc["clips"])
    by_id = {s.id: s for s in project.shots}
    for clip in list(clips):
        shot = by_id.get(clip["shotId"])
        if clip["source"] is None and shot and shot.take and shot.take.video:
            source, total = preserve_take(store, shot.take.video, shot.take.image)
            clips = choose_take(clips, shot.id, source, total)
    return clips


def export_cut(store, doc, production, *, sound_store=None, sound_doc=None, preview=False, include_mix=True):
    """Encode 24 fps picture and linked 48 kHz audio at the saved in/out frames.

    Missing video is never silently omitted. Unbound storyboard cards are
    explicitly labelled still placeholders in this rough-cut export.
    """
    clips = visible_clips(validate_clips(doc["clips"]))
    if not clips:
        raise ValueError("Klippen er tom.")
    view = store.view(doc, production)
    sources = []
    for clip, shot in zip(clips, view.shots):
        source = clip["source"]
        if source and source["video"]:
            path = store.project / source["video"]
            store.workspace._assert_managed_path(path)
            if not path.is_file():
                raise ValueError(f"Opptaket mangler: {shot.title}")
            info = probe(path)
            if info["frames"] < clip["outFrame"]:
                raise ValueError(f"Opptaket er kortere enn klippingen: {shot.title}")
            sources.append((path, info, clip, shot, False))
        else:
            image = Path(shot.take.image) if shot.take and shot.take.image else None
            sources.append((image, None, clip, shot, True))
    first = next((info for _, info, *_ in sources if info), {"width": 1280, "height": 720})
    width, height = (max(2, int(first[k]) // 2 * 2) for k in ("width", "height"))
    from sound import SoundStore
    from sound_media import render_mix
    sound_store = sound_store or SoundStore(store.workspace, store.slug)
    if include_mix and sound_doc is None and sound_store.path.exists():
        sound_doc = sound_store.load()
    mix = render_mix(sound_store, sound_doc, doc) if include_mix and sound_doc is not None else None
    recipe = {"cut": doc, "sound": sound_doc} if sound_doc is not None else doc
    if preview:
        import hashlib
        fingerprints = [_digest(path) if path and path.is_file() else "" for path, *_ in sources]
        key = hashlib.sha256(json.dumps([recipe, fingerprints, _digest(mix) if mix else ""], sort_keys=True).encode()).hexdigest()
        folder = store.project / "System" / "editorial-preview"
        store.workspace._mkdir(folder)
        output = folder / f"film-{key}.mp4"
    else:
        output = store.project / "Output" / "Video" / f"grovklipp-r{doc['revision']}-{uuid.uuid4().hex[:8]}.mp4"
    store.workspace._assert_managed_path(output)
    receipt = output.with_suffix(".cut.json")
    store.workspace._assert_managed_path(receipt)
    if preview and output.exists() and receipt.exists():
        try: cached = json.loads(receipt.read_text())
        except (OSError,ValueError): cached = {}
        if cached.get("sha256") == _digest(output) and cached.get("recipe") == recipe:
            return output
    if mix:
        # Picture edits and sound edits have separate caches. A bus-level or
        # cue change remixes/muxes audio without re-encoding every picture.
        picture_doc={**doc,"revision":0,"undo":[],"redo":[]}
        picture=export_cut(store,picture_doc,production,preview=True,include_mix=False)
        expected=sum(c["outFrame"]-c["inFrame"] for c in clips)
        with tempfile.TemporaryDirectory(prefix="raven-cut-mix-") as folder:
            mixed=Path(folder)/"mixed.mp4"
            subprocess.run([executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(picture), "-i", str(mix),
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", f"{expected/FPS:.9f}",
                            "-video_track_timescale", "24000", "-movflags", "+faststart", str(mixed)], capture_output=True, check=True, timeout=300)
            _publish_cut(store,mixed,output)
        record={"recipe":recipe,"sha256":_digest(output)} if preview else recipe
        _atomic_text(receipt,json.dumps(record,ensure_ascii=False,indent=2)+"\n")
        return output
    with tempfile.TemporaryDirectory(prefix="raven-cut-") as directory:
        directory = Path(directory)
        pieces = []
        for i, (path, info, clip, shot, placeholder) in enumerate(sources):
            count = clip["outFrame"] - clip["inFrame"]
            seconds = count / FPS
            args = [executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
            if placeholder:
                if path and path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                    args += ["-loop", "1", "-framerate", str(FPS), "-i", str(path)]
                else:
                    args += ["-f", "lavfi", "-i", f"color=c=0x242c2f:s={width}x{height}:r={FPS}"]
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                vf = f"fps={FPS},trim=end_frame={count},setpts=PTS-STARTPTS"
                # Fixed label; user text never becomes filter syntax.
                vf += ",drawbox=x=0:y=0:w=iw:h=48:color=black@0.7:t=fill,drawtext=text='BILDEPLAN':x=16:y=14:fontsize=20:fontcolor=white"
                af = f"[1:a]atrim=duration={seconds:.9f},asetpts=PTS-STARTPTS[a]"
            else:
                args += ["-i", str(path)]
                vf = f"fps={FPS},trim=start_frame={clip['inFrame']}:end_frame={clip['outFrame']},setpts=PTS-STARTPTS"
                if info["audio"]:
                    af = f"[0:a]atrim=start={clip['inFrame']/FPS:.9f}:end={clip['outFrame']/FPS:.9f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={seconds:.9f}[a]"
                else:
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                    af = f"[1:a]atrim=duration={seconds:.9f},asetpts=PTS-STARTPTS[a]"
            vf += f",scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
            # NUT preserves exact 1/24 timestamps; Matroska's millisecond
            # timebase would round clip boundaries and drift the cut.
            piece = directory / f"{i:04d}.nut"
            args += ["-filter_complex", f"[0:v]{vf}[v];{af}", "-map", "[v]", "-map", "[a]",
                     "-t", f"{seconds:.9f}", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                     "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(piece)]
            subprocess.run(args, capture_output=True, check=True, timeout=300)
            pieces.append((piece, seconds))
        listing = directory / "concat.txt"
        listing.write_text("".join(f"file '{p.name}'\nduration {seconds:.9f}\n" for p, seconds in pieces))
        temporary = directory / "grovklipp.mp4"
        subprocess.run([executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1",
                        "-i", str(listing), "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-video_track_timescale", "24000", "-movflags", "+faststart", str(temporary)],
                       capture_output=True, check=True, timeout=300)
        expected = sum(c["outFrame"] - c["inFrame"] for c in clips)
        if abs(probe(temporary)["frames"] - expected) > 1:
            raise ValueError("Eksportens varighet stemmer ikke med klippelaget.")
        _publish_cut(store,temporary,output)
    record = {"recipe": recipe, "sha256": _digest(output)} if preview else recipe
    _atomic_text(receipt, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return output


def _publish_cut(store,source,output):
    store.workspace._assert_managed_path(output)
    fd,staging=tempfile.mkstemp(prefix=".grovklipp-",dir=output.parent)
    try:
        with os.fdopen(fd,"wb") as handle,source.open("rb") as original:
            shutil.copyfileobj(original,handle);handle.flush();os.fsync(handle.fileno())
        os.replace(staging,output)
    finally:
        if os.path.exists(staging): os.unlink(staging)
