"""Vendor the production engine source into this single repository, excluding user data."""
from pathlib import Path
import argparse,shutil

def sync(source,target):
    source=Path(source).resolve();target=Path(target).resolve()
    if not (source/'server/__main__.py').is_file():raise ValueError('Matching engine source required')
    target.mkdir(parents=True,exist_ok=True)
    for name in ('server','tests','js','css','workflows','prompts'):
        path=source/name
        if path.is_dir():shutil.copytree(path,target/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
    local=target/'local';local.mkdir(parents=True,exist_ok=True)
    for name in ('start_fable.py','stop_fable.py'):
        path=source/'local'/name
        if path.is_file():shutil.copy2(path,local/name)
    for path in source.iterdir():
        if path.is_file() and (path.suffix in {'.html','.css','.js'} or path.name.endswith('-sample.json') or path.name in {'requirements-ci.txt','LICENSE','COPYING','NOTICE'}):shutil.copy2(path,target/path.name)
    (target/'UPSTREAM.md').write_text('Production engine source integrated into Sprintwake Studios.\nOriginally based on https://github.com/crashtestoz/storyboard.\nKeep personal configs, projects, models and local runtimes outside this directory.\n')
    print('Engine source synchronized; no personal config, media, models or build runtime copied.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);a=p.parse_args();sync(a.source,Path(__file__).resolve().parents[1]/'engine')
