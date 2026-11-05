"""Private local migration and relocatable managed paths; never build payload data."""
from __future__ import annotations
import copy,json,os
from pathlib import Path
from connections import ConnectionStore,defaults,write_private

def cleanup_app_registration(bundle=None):
    """Remove old Trash registrations left by a completed Sparkle replacement."""
    selected=bundle or os.environ.get('SPRINTWAKE_APP_BUNDLE')
    if not selected:return 0
    from app_bundles import BUNDLE_ID,bundle_id,clear_trash_registrations
    path=Path(selected).expanduser().absolute()
    ident=bundle_id(path)
    if not ident:raise ValueError('Installed application identity is unavailable')
    return clear_trash_registrations(ident,keep=(path,),include_qa=ident==BUNDLE_ID)

def rebase_bundle_paths(doc,bundle):
    doc=copy.deepcopy(doc);bundle=Path(bundle)
    suffixes={'engine':'/Contents/Resources/Studio/engine','python':'/Contents/Resources/Python/bin/python3.12'}
    for key,suffix in suffixes.items():
        current=doc['paths'][key]
        if '.app/' in current and current.endswith(suffix):doc['paths'][key]=str(bundle/suffix.lstrip('/'))
    return doc

def migrate_local_settings(legacy_engine,bundle,store=None):
    """Installer-only operation, after a verified app is installed; no overwrites."""
    legacy_engine=Path(legacy_engine).resolve();bundle=Path(bundle).resolve();store=store or ConnectionStore()
    existed=store.path.exists();doc=store.load() if existed else defaults()
    private=store.path.parent/'EngineSettings';private.mkdir(parents=True,exist_ok=True,mode=0o700)
    names=('server-config.json','llm-services.json','tts-services.json','soundtrack-services.json','voice-presets.json','render-timings.json','film-timings.json')
    for name in names:
        source=legacy_engine/name;target=private/name
        if source.is_file() and not target.exists():
            if source.stat().st_size>16_000_000:raise ValueError('Legacy settings exceed migration limit: '+name)
            write_private(target,json.loads(source.read_text()))
    config=private/'server-config.json'
    engine_settings=json.loads(config.read_text()) if config.exists() else {}
    if legacy_engine.is_dir() and 'modelInstallRoot' not in engine_settings:
        engine_settings['modelInstallRoot']=str(legacy_engine.parent);write_private(config,engine_settings)
    # Only inferred/default engine locations move to the managed app. Arbitrary
    # explicitly selected external engines/runtimes are retained.
    old_engine=Path(doc['paths']['engine']).resolve()
    managed=not existed or old_engine in {legacy_engine,Path(__file__).resolve().parent/'engine'} or '.app/Contents/Resources/Studio/engine' in str(old_engine)
    if managed:
        doc['paths']['engine']=str(bundle/'Contents/Resources/Studio/engine')
        old_python=Path(doc['paths']['python']).expanduser().absolute()
        known_python=(legacy_engine.parent/'ComfyUI-Film/.venv/bin/python',legacy_engine/'.venv/bin/python',Path(__file__).resolve().parent/'.venv/bin/python',Path(__file__).resolve().parent/'runtime/bundle-venv/bin/python')
        if not existed or any(old_python==p.expanduser().absolute() for p in known_python) or any(suffix in str(old_python) for suffix in ('.app/Contents/Frameworks/Python/','.app/Contents/Resources/Python/')):
            doc['paths']['python']=str(bundle/'Contents/Resources/Python/bin/python3.12')
    if not existed and isinstance(engine_settings.get('workspace'),str):doc['paths']['workspace']=engine_settings['workspace']
    store.save(doc)
    return doc
