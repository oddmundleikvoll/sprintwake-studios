"""Refresh/check pure engine contracts bundled so first-run setup needs no engine."""
from pathlib import Path
import argparse,shutil
p=argparse.ArgumentParser();p.add_argument('--engine',type=Path,default=(Path(__file__).resolve().parents[1]/'engine' if (Path(__file__).resolve().parents[1]/'engine').is_dir() else Path(__file__).resolve().parents[2]/'storyboard'));p.add_argument('--write',action='store_true');a=p.parse_args()
out=Path(__file__).resolve().parents[1]/'contracts'
for name in ['raven_contract.py','raven_projects.py','raven_editorial.py','raven_director_guide.md','ltx_anchors.py','comfy_workflow.py','app_lifetime.py','store.py','media.py']:
    source=a.engine/'server'/name;target=out/name
    if a.write:shutil.copy2(source,target)
    elif source.read_bytes()!=target.read_bytes():raise SystemExit('Bundled contract differs: '+name)
print('Pure contracts synchronized' if a.write else 'Pure contracts match engine')
