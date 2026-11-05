"""Decoded media checks and exact 24 fps / 48 kHz transformations.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from fractions import Fraction


def _tool(name):
    value = os.environ.get('RAVEN_' + name.upper()) or shutil.which(name)
    if not value:
        raise RuntimeError(name + ' is required for media transformations')
    return value


def file_sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def media_metadata(path):
    """Decode every frame; absent container frame counts never imply success."""
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise ValueError('Media must be a nonempty regular file')
    if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif'}:
        from PIL import Image
        try:
            with Image.open(path) as image:
                image.load()
                return {'format': image.format, 'streams': [{'codec_type': 'video', 'width': image.width,
                    'height': image.height, 'nb_frames': getattr(image, 'n_frames', 1), 'image': True}]}
        except Exception as error:
            raise ValueError('Image pixels could not be decoded') from error
    import av
    streams = []
    try:
        with av.open(str(path)) as container:
            for stream in container.streams:
                if stream.type not in {'audio', 'video'}:
                    continue
                row = {'codec_type': stream.type, 'index': stream.index}
                if stream.type == 'video':
                    row.update(width=stream.codec_context.width, height=stream.codec_context.height,
                               r_frame_rate=str(stream.average_rate or 0), nb_frames=0)
                else:
                    row.update(sample_rate=stream.codec_context.sample_rate, decoded_samples=0,
                               channels=stream.codec_context.channels)
                streams.append(row)
            if not streams:
                raise ValueError('Media contains no audio or picture stream')
            indexed = {row['index']: row for row in streams}
            for packet in container.demux():
                if packet.stream.index not in indexed:
                    continue
                row = indexed[packet.stream.index]
                for frame in packet.decode():
                    if row['codec_type'] == 'video':
                        if (frame.width, frame.height) != (row['width'], row['height']):
                            raise ValueError('Picture dimensions changed inside a clip')
                        row['nb_frames'] += 1
                    else:
                        if frame.sample_rate != row['sample_rate']:
                            raise ValueError('Audio sample rate changed inside a clip')
                        row['decoded_samples'] += frame.samples
        if any(row.get('nb_frames', row.get('decoded_samples', 0)) == 0 for row in streams):
            raise ValueError('Media contains an empty stream')
        return {'streams': streams}
    except Exception as error:
        raise ValueError('Media could not be fully decoded') from error


def validate_decoded(path, *, kind=None, width=None, height=None, fps=None, frames=None, audio_hz=None):
    metadata = media_metadata(path)
    streams = metadata.get('streams', [])
    picture = next((row for row in streams if row.get('codec_type') == 'video'), None)
    audio = next((row for row in streams if row.get('codec_type') == 'audio'), None)
    if kind in {'image', 'video'} and not picture or kind == 'audio' and not audio:
        raise ValueError('Required decoded media stream is missing')
    if kind == 'image' and not picture.get('image'):
        raise ValueError('Expected a decoded still image')
    if kind == 'video' and picture.get('image'):
        raise ValueError('Expected moving-picture media')
    if picture:
        if width is not None and picture['width'] != width: raise ValueError('Decoded width mismatch')
        if height is not None and picture['height'] != height: raise ValueError('Decoded height mismatch')
        if frames is not None and picture['nb_frames'] != frames: raise ValueError('Decoded frame count mismatch')
        if fps is not None and Fraction(picture.get('r_frame_rate', '0')) != Fraction(fps):
            raise ValueError('Decoded frame rate mismatch')
    if audio_hz is not None and (not audio or audio['sample_rate'] != audio_hz):
        raise ValueError('Required audio sample rate mismatch')
    return metadata


def validate_media(path, kind='video', expected_frames=None, fps=None, **kwargs):
    return validate_decoded(path, kind=kind, frames=expected_frames, fps=fps, **kwargs)


def immutable_output(path):
    path = Path(path)
    validate_decoded(path)
    return {'path': str(path.resolve()), 'sha256': file_sha256(path), 'bytes': path.stat().st_size}


def recipe_digest(recipe):
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _transform(argv, output):
    output = Path(output)
    if output.exists() or output.is_symlink(): raise ValueError('Transformation target already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.media-', suffix=output.suffix, dir=output.parent)
    os.close(fd)
    try:
        subprocess.run([_tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', *argv, temporary],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=600)
        validate_decoded(temporary)
        os.link(temporary, output)  # Refuse an output concurrently created by another writer.
    except subprocess.CalledProcessError as error:
        raise ValueError('Media transformation failed') from error
    finally:
        Path(temporary).unlink(missing_ok=True)
    return immutable_output(output)


def join_clips(inputs, output):
    paths = [Path(value).absolute() for value in inputs]
    if not paths: raise ValueError('At least one clip is required')
    metadata = [validate_decoded(path, kind='video', fps=24, audio_hz=48000) for path in paths]
    pictures = [next(row for row in item['streams'] if row['codec_type'] == 'video') for item in metadata]
    if len({(row['width'], row['height']) for row in pictures}) != 1:
        raise ValueError('Film clips must share picture dimensions')
    if len(paths) > 24:
        with tempfile.TemporaryDirectory(prefix='sprintwake-film-batches-') as directory:
            batches=[]
            for index in range(0,len(paths),24):
                target=Path(directory)/f'batch-{index}.mp4'
                join_clips(paths[index:index+24],target); batches.append(target)
            return join_clips(batches,output)
    args=[]; filters=[]
    for index,(path,picture) in enumerate(zip(paths,pictures)):
        args += ['-i',str(path)]
        count=picture['nb_frames']
        filters += [f'[{index}:v:0]trim=end_frame={count},setpts=N/(24*TB)[v{index}]',
                    f'[{index}:a:0]aresample=48000,atrim=end_sample={count*2000},apad=whole_len={count*2000},asetpts=N/SR/TB[a{index}]']
    filters.append(''.join(f'[v{i}][a{i}]' for i in range(len(paths)))+f'concat=n={len(paths)}:v=1:a=1[v][a]')
    result = _transform([*args,'-filter_complex',';'.join(filters),'-map','[v]','-map','[a]',
        '-r','24','-ar','48000','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac'],output)
    validate_decoded(output, kind='video', fps=24, frames=sum(row['nb_frames'] for row in pictures), audio_hz=48000)
    return result


def trim_carried_frames(input_path, output_path, frames=25):
    if type(frames) is not int or frames < 0: raise ValueError('Invalid carried frame count')
    metadata = validate_decoded(input_path, kind='video', fps=24, audio_hz=48000)
    total = next(row['nb_frames'] for row in metadata['streams'] if row['codec_type'] == 'video')
    if total <= frames: raise ValueError('No publishable frames remain')
    result = _transform(['-i', str(input_path), '-map', '0:v:0', '-map', '0:a:0',
        '-vf', f'trim=start_frame={frames},setpts=PTS-STARTPTS',
        '-af', f'atrim=start_sample={frames * 2000}:end_sample={total * 2000},asetpts=PTS-STARTPTS',
        '-frames:v', str(total - frames), '-r', '24', '-ar', '48000', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac'], output_path)
    validate_decoded(output_path, kind='video', fps=24, frames=total-frames, audio_hz=48000)
    return result


def last_frame(input_path, output_path):
    import av
    from PIL import Image
    frame=None
    with av.open(str(input_path)) as container:
        for frame in container.decode(video=0): pass
    if frame is None: raise ValueError('The clip has no final picture')
    output_path=Path(output_path)
    if output_path.exists(): raise ValueError('Review still already exists')
    frame.to_image().save(output_path)
    validate_decoded(output_path,kind='image')
    return output_path


def tail_frames(input_path, output_path, count=25):
    metadata=validate_decoded(input_path,kind='video',fps=24,audio_hz=48000)
    total=next(row['nb_frames'] for row in metadata['streams'] if row['codec_type']=='video')
    if type(count) is not int or count<=0 or count>total: raise ValueError('Invalid tail frame count')
    start=total-count
    result=_transform(['-i',str(input_path),'-map','0:v:0','-map','0:a:0',
        '-vf',f'trim=start_frame={start}:end_frame={total},setpts=PTS-STARTPTS',
        '-af',f'atrim=start_sample={start*2000}:end_sample={total*2000},apad=whole_len={count*2000},asetpts=PTS-STARTPTS',
        '-frames:v',str(count),'-r','24','-ar','48000','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac'],output_path)
    validate_decoded(output_path,kind='video',frames=count,fps=24,audio_hz=48000)
    return result
