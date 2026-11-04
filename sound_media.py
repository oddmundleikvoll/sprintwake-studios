"""Safe media import and deterministic FFmpeg sound-preview rendering."""
from __future__ import annotations

import hashlib
import json
import os
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import wave

from sound import SoundStore, effective_clips, validate_state
from workspace import _atomic_text, _digest

SAMPLE_RATE = 48000
MAX_CUES = 5000
BATCH_SIZE = 16
MIX_VERSION = 2


def _ffprobe():
    from media_tools import executable
    return executable("ffprobe")


def _ffmpeg():
    from media_tools import executable
    return executable("ffmpeg")


def _probe(path):
    command = [_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-print_format", "json", str(path)]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Kunne ikke lese lydmediet.") from exc
    streams = [stream for stream in (payload.get("streams") or []) if stream.get("codec_type") == "audio"]
    if not streams:
        raise ValueError("Mediet har ingen lydstrøm.")
    duration = streams[0].get("duration") or (payload.get("format") or {}).get("duration")
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        raise ValueError("Lydmediet mangler varighet.")
    if not math.isfinite(seconds) or not 0 < seconds <= 86400:
        raise ValueError("Lydmediet er tomt.")
    return max(1, round(seconds * 24))


def _audio_frames(path):
    """Return measured audio frames, or None for a video with no audio."""
    command = [_ffprobe(), "-v", "error", "-show_streams", "-show_format", "-print_format", "json", str(path)]
    try:
        payload = json.loads(subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Kunne ikke lese mediet.") from exc
    streams = [stream for stream in (payload.get("streams") or []) if stream.get("codec_type") == "audio"]
    if not streams:
        return None
    duration = streams[0].get("duration") or (payload.get("format") or {}).get("duration")
    try:
        seconds=float(duration)
        if not math.isfinite(seconds) or not 0<seconds<=86400: raise ValueError("Ugyldig lydvarighet.")
        return max(1, round(seconds * 24))
    except (TypeError, ValueError):
        raise ValueError("Lydmediet mangler varighet.")


def preserve_audio(store: SoundStore, source):
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError("Lydkilde må være en vanlig fil.")
    before=_digest(source)
    stored = store.workspace.import_input(store.slug, source)
    if before!=_digest(stored) or before!=_digest(source):
        raise ValueError("Lydkilden endret seg under import. Prøv igjen.")
    source_frames = _probe(stored)
    relative = str(stored.relative_to(store.project))
    return {"id": uuid.uuid4().hex, "track": "dialogue", "label": source.stem, "path": relative,
            "sourceFrames": source_frames, "inFrame": 0, "outFrame": source_frames,
            "atFrame": 0, "gainDb": 0.0, "fadeIn": 0, "fadeOut": 0, "muted": False}


def _frames(cut_doc):
    clips = cut_doc.get("clips", []) if isinstance(cut_doc, dict) else []
    return sum(int(c["outFrame"]) - int(c["inFrame"]) for c in clips if c.get("included", True))


def _safe_media(store, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts or not relative.startswith("Input/"):
        raise ValueError("Utrygg lydreferanse.")
    path = store.project / relative
    store.workspace._assert_managed_path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Lydreferansen finnes ikke.")
    return path


def _digest_obj(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def render_mix(store: SoundStore, doc, cut_doc, *, include_original=True):
    state = doc["state"] if "state" in doc else doc
    validate_state(state)
    film_frames = _frames(cut_doc)
    if film_frames <= 0:
        raise ValueError("Kan ikke lage lydmiks for et tomt klipp.")
    duration = film_frames / 24
    cues = []
    media_hashes = {}
    measured_media={}
    def media(relative):
        path=_safe_media(store,relative)
        if relative not in media_hashes:
            media_hashes[relative]=_digest(path)
            measured_media[relative]=_audio_frames(path)
        return path,measured_media[relative]
    track_map = {track["id"]: track for track in state["tracks"]}
    solo = {track["id"] for track in state["tracks"] if track["solo"]}
    for clip in effective_clips(state):
        if clip["atFrame"] >= film_frames:
            continue
        if solo and clip["track"] not in solo:
            continue
        path,measured = media(clip["path"])
        if measured is None:
            raise ValueError("Importert lydklipp har ingen lydstrøm.")
        if measured + 1 < clip["outFrame"]:
            raise ValueError("Importert lydklipp er kortere enn lagret trim.")
        duration_frames = clip["outFrame"] - clip["inFrame"]
        usable = min(duration_frames, film_frames - clip["atFrame"])
        if usable > 0:
            cues.append((path, clip["inFrame"] / 24, usable / 24, clip["atFrame"], clip["gainDb"] + track_map[clip["track"]]["gainDb"], clip["fadeIn"], clip["fadeOut"], clip["id"], duration_frames / 24))
    dialogue_track = track_map["dialogue"]
    if include_original and state["original"]["enabled"] and not dialogue_track["muted"] and (not solo or "dialogue" in solo):
        cursor = 0
        for cut in cut_doc.get("clips", []):
            if not cut.get("included", True):
                continue
            source = cut.get("source") or {}
            relative = source.get("video") or source.get("audio")
            if relative:
                path,measured = media(relative)
                length = min(cut["outFrame"] - cut["inFrame"], film_frames - cursor)
                if measured is not None and length > 0:
                    cues.append((path, cut["inFrame"] / 24, length / 24, cursor, state["original"]["gainDb"] + dialogue_track["gainDb"], 0, 0, "original-" + cut["shotId"], (cut["outFrame"] - cut["inFrame"]) / 24))
            cursor += cut["outFrame"] - cut["inFrame"]
            if cursor >= film_frames:
                break
    key_data = {"renderer":MIX_VERSION,"state": state, "cut": cut_doc.get("clips",[]), "includeOriginal": include_original, "filmFrames": film_frames, "media": media_hashes}
    key = _digest_obj(key_data)
    cache_dir = store.project / "System" / "sound-preview"
    store.workspace._mkdir(cache_dir)
    output = cache_dir / f"mix-{key}.wav"
    meta = output.with_suffix(".json")
    store.workspace._assert_managed_path(output)
    store.workspace._assert_managed_path(meta)
    if output.exists() and output.is_symlink() or meta.exists() and meta.is_symlink():
        raise ValueError("Lydcache kan ikke være en symlink.")
    if output.exists() and meta.exists() and not output.is_symlink() and not meta.is_symlink():
        try:
            cached = json.loads(meta.read_text(encoding="utf-8"))
            expected = {"key": key, "frames": film_frames, "media": media_hashes, "sha256": _digest(output)}
            if cached == expected and _wav_frames(output) == film_frames * 2000:
                return output
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    if len(cues) > MAX_CUES:
        raise ValueError("Lydmiksen har en urealistisk mengde lydkilder.")
    with tempfile.TemporaryDirectory(prefix="raven-sound-", dir=store.project / "System") as temp:
        temporary_output = Path(temp) / "mix.wav"
        previous=None
        # Bounded decoders and two streamed float accumulators. Limit and
        # quantize only once, after all batches have been summed.
        for first in range(0,len(cues),BATCH_SIZE):
            batch=cues[first:first+BATCH_SIZE]
            current=Path(temp)/f"batch-{first}.wav"
            command=[_ffmpeg(),"-v","error","-nostdin","-y"]
            filters=[];labels=[]
            if previous:
                command += ["-i",str(previous)]
                filters.append("[0:a]anull[previous]");labels.append("[previous]")
            for i,(path,start,length,at,gain,fade_in,fade_out,_,saved_length) in enumerate(batch):
                command += ["-i",str(path)]
                index=i+(1 if previous else 0);label=f"cue{i}"
                bits=[f"aresample={SAMPLE_RATE}","aformat=sample_fmts=fltp:channel_layouts=stereo",f"atrim=start_sample={round(start*SAMPLE_RATE)}:end_sample={round((start+length)*SAMPLE_RATE)}","asetpts=PTS-STARTPTS",f"volume={gain:.9f}dB"]
                if fade_in: bits.append(f"afade=t=in:st=0:d={fade_in/24:.9f}")
                if fade_out: bits.append(f"afade=t=out:st={max(0,saved_length-fade_out/24):.9f}:d={fade_out/24:.9f}")
                # Timestamp gaps become streamed silence without allocating
                # a per-cue delay buffer as long as the preceding film.
                bits += [f"apad=whole_len={round(length*SAMPLE_RATE)}",f"atrim=end_sample={round(length*SAMPLE_RATE)}",f"asettb=1/{SAMPLE_RATE}",f"asetpts=PTS-STARTPTS+{at*2000}",f"aresample={SAMPLE_RATE}:async=1:first_pts=0",f"atrim=end_sample={film_frames*2000}[{label}]"]
                filters.append(f"[{index}:a]"+",".join(bits));labels.append(f"[{label}]")
            filters.append("".join(labels)+f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,apad,atrim=end_sample={film_frames*2000}[mixed]")
            script=Path(temp)/"batch.filter";script.write_text(";".join(filters))
            command += ["-filter_complex",script.read_text(),"-map","[mixed]","-c:a","pcm_f32le","-ar",str(SAMPLE_RATE),"-ac","2",str(current)]
            _run_mix(command,max(120,duration*2))
            if previous: previous.unlink()
            previous=current
        if previous:
            command=[_ffmpeg(),"-v","error","-nostdin","-y","-i",str(previous),"-af",f"alimiter=limit=0.98:level=disabled:latency=1,apad,atrim=end_sample={film_frames*2000}","-c:a","pcm_s16le","-ar",str(SAMPLE_RATE),"-ac","2",str(temporary_output)]
        else:
            command=[_ffmpeg(),"-v","error","-nostdin","-y","-f","lavfi","-i",f"anullsrc=r={SAMPLE_RATE}:cl=stereo","-af",f"atrim=end_sample={film_frames*2000}","-c:a","pcm_s16le",str(temporary_output)]
        _run_mix(command,max(120,duration*2))
        if not temporary_output.is_file() or _wav_frames(temporary_output) != film_frames * 2000:
            raise ValueError("FFmpeg laget ikke en lydfil med eksakt filmlengde.")
        for relative,expected_hash in media_hashes.items():
            if _digest(_safe_media(store,relative))!=expected_hash:
                raise ValueError("Et lydmedium endret seg under miksing. Prøv igjen.")
        store.workspace._assert_managed_path(output)
        if output.exists() and output.is_symlink():
            raise ValueError("Lydcache kan ikke være en symlink.")
        os.replace(temporary_output, output)
    metadata = {"key": key, "frames": film_frames, "media": media_hashes, "sha256": _digest(output)}
    _atomic_text(meta, json.dumps(metadata, sort_keys=True) + "\n")
    return output


def _run_mix(command,timeout):
    try:
        subprocess.run(command,check=True,capture_output=True,timeout=timeout,text=True)
    except (OSError,subprocess.SubprocessError) as exc:
        raise ValueError("FFmpeg kunne ikke lage lydmiksen.") from exc


def _wav_frames(path):
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getframerate() != SAMPLE_RATE or handle.getnchannels() != 2 or handle.getsampwidth() != 2:
                return -1
            return handle.getnframes()
    except (OSError, wave.Error):
        return -1
