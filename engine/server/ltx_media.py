"""Streaming checks and durable checkpoints shared by Storyboard's LTX bridge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

FPS = 24
RATE = 48000


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w') as target:
            json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write('\n')
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def runtime_identity(runtime, model, bridge):
    """Identify installed code and pack without reading 75 GB of weights per clip."""
    runtime, model, bridge = Path(runtime), Path(model), Path(bridge)
    marker = json.loads((model / '.ltx-installed.json').read_text())
    weights = {}
    for name, size in marker['files'].items():
        path = model / name
        if not path.resolve().is_relative_to(model.resolve()):
            raise ValueError('Unsafe model marker path')
        stat = path.stat()
        if size is not None and stat.st_size != size:
            raise ValueError('Incomplete LTX model: ' + name)
        weights[name] = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    files = [runtime / 'uv.lock', *sorted((runtime / 'packages').rglob('*.py')),
             *sorted(bridge.glob('*.py')), Path(__file__), Path(__file__).with_name('ltx_anchors.py')]
    return {'pack': digest(marker), 'weights': digest(weights),
            'code': digest([(str(p.relative_to(runtime)) if p.is_relative_to(runtime) else p.name, file_hash(p))
                            for p in files if '__pycache__' not in p.parts])}


def validate_video(path, frames, width=None, height=None, cancel=None):
    """Fully decode one frame at a time, including audio; container headers aren't proof."""
    import av
    with av.open(str(path)) as media:
        if len(media.streams.video) != 1 or len(media.streams.audio) != 1:
            raise ValueError('LTX media requires one video and one audio stream')
        video, audio = media.streams.video[0], media.streams.audio[0]
        if (width is not None and video.width != width or height is not None and video.height != height
                or video.average_rate is None or abs(float(video.average_rate) - FPS) > .001):
            raise ValueError('LTX geometry or frame rate differs from recipe')
        if audio.rate != RATE or len(audio.layout.channels) != 2:
            raise ValueError('LTX audio must be 48 kHz stereo')
        count, samples, video_start = 0, 0, None
        for packet in media.demux(video, audio):
            if cancel and cancel():
                raise InterruptedError('Filmjobben ble stoppet; ferdige klipp er bevart')
            for frame in packet.decode():
                if isinstance(frame, av.VideoFrame):
                    if video_start is None:
                        video_start = frame.time
                        # MP4 stream copy can offset video by one AAC priming packet.
                        if video_start is None or abs(video_start) > 1 / FPS:
                            raise ValueError('LTX video has an unexpected start offset')
                    if frame.time is None or abs(frame.time - video_start - count / FPS) > .001:
                        raise ValueError('LTX video timestamps differ from frame cadence')
                    count += 1
                else:
                    if frame.time is None or abs(frame.time - samples / RATE) > .04:
                        raise ValueError('LTX audio timestamps contain an offset or gap')
                    samples += frame.samples
                if count > frames or samples > frames * (RATE // FPS) + 2048:
                    raise ValueError('LTX media exceeds the expected length')
        seconds = frames / FPS
        # AAC pads its last packet by up to 1023 samples.
        if count != frames or abs(samples / RATE - seconds) > .04:
            raise ValueError(f'LTX media length differs: {count}/{frames} frames, {samples/RATE:.3f}/{seconds:.3f}s audio')
        if video.duration is not None and abs(float(video.duration * video.time_base) - seconds) > 1 / FPS:
            raise ValueError('LTX video timestamps differ from recipe')
        return {'frames': count, 'width': video.width, 'height': video.height, 'fps': FPS, 'has_audio': True}


def validate_wave(path, frames):
    import wave
    with wave.open(str(path), 'rb') as audio:
        samples = frames * (RATE // FPS)
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()) != (RATE, 2, 2, samples):
            raise ValueError('LTX WAV format or length differs from recipe')
        remaining = samples * 4
        while remaining:
            chunk = audio.readframes(min(remaining // 4, RATE))
            if not chunk:
                raise ValueError('Truncated LTX WAV')
            remaining -= len(chunk)


def run_ffmpeg(args, *, timeout=300, cancel=None):
    # Drain diagnostics to disk: long ffmpeg stderr must not deadlock the pipe.
    with tempfile.TemporaryFile(mode='w+') as log:
        process = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                                    *map(str, args)], stdout=subprocess.DEVNULL, stderr=log)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancel and cancel():
                    raise InterruptedError('Filmjobben ble stoppet; ferdige klipp er bevart')
                if time.monotonic() >= deadline:
                    raise TimeoutError('ffmpeg exceeded its time limit')
                time.sleep(.05)
            if process.returncode:
                log.seek(0)
                raise RuntimeError('ffmpeg failed: ' + log.read()[-2000:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def concat_media(paths, output, *, audio_paths=None, frames=None, cancel=None):
    """Concat demuxers open clips sequentially, keeping decoder count bounded."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name('.' + output.stem + '.' + uuid.uuid4().hex + '.pending.mp4')
    with tempfile.TemporaryDirectory(prefix='.concat-', dir=output.parent) as directory:
        def listing(name, files):
            target = Path(directory) / name
            lines = []
            for i, path in enumerate(files):
                escaped = str(Path(path).resolve()).replace("'", "'\\''")
                if '\n' in escaped or '\r' in escaped:
                    raise ValueError('Newline in media path')
                lines.append("file '" + escaped + "'")
                if frames is not None:
                    lines.append(f'duration {frames[i] / FPS:.12f}')
            target.write_text('\n'.join(lines) + '\n')
            return target
        args = ['-f', 'concat', '-safe', '0', '-i', listing('video.txt', paths)]
        if audio_paths:
            args += ['-f', 'concat', '-safe', '0', '-i', listing('audio.txt', audio_paths)]
        # Correct AAC packet padding at each seam against the concat timestamps.
        audio_filter = 'aresample=48000:async=1:first_pts=0:min_hard_comp=0.001'
        if frames is not None:
            audio_filter += f',apad,atrim=end_sample={sum(frames) * (RATE // FPS)}'
        args += ['-map', '0:v:0', '-map', '1:a:0' if audio_paths else '0:a:0',
                 '-vf', 'setpts=N/(24*TB)', '-r', str(FPS), '-c:v', 'libx264',
                 '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p',
                 '-c:a', 'aac', '-af', audio_filter, '-ar', str(RATE), '-ac', '2']
        if frames is not None:
            args += ['-t', str(sum(frames) / FPS)]
        args += ['-movflags', '+faststart', pending]
        try:
            run_ffmpeg(args, timeout=180 + 30 * len(paths), cancel=cancel)
            if frames is not None:
                validate_video(pending, sum(frames), cancel=cancel)
            if cancel and cancel():
                raise InterruptedError('Filmjobben ble stoppet')
            pending.replace(output)
        finally:
            pending.unlink(missing_ok=True)
    return output
