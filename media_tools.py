"""Resolve media tools from explicit settings, selected engine, then normal PATH."""
import os,shutil
from pathlib import Path

def executable(name):
    explicit=os.environ.get('RAVEN_'+name.upper())
    engine=Path(os.environ.get('RAVEN_ENGINE_ROOT',str(Path(__file__).resolve().parent/'engine')))
    candidates=[Path(explicit)] if explicit else []
    bundle = Path(os.environ.get('RAVEN_BUNDLE_ROOT', str(Path(__file__).resolve().parent)))
    candidates += [bundle/'runtime/tools'/name, engine/'local/bin'/name]
    found=shutil.which(name)
    if found:candidates.append(Path(found))
    candidates += [Path('/opt/homebrew/bin')/name,Path('/usr/local/bin')/name]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate,os.X_OK):return str(candidate)
    raise ValueError(name+' is missing. Install FFmpeg or set RAVEN_'+name.upper()+'.')
