"""Check a disposable standalone bundle and retire it on success or failure."""
# SPDX-License-Identifier: MIT
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_bundles import BUNDLE_ID,bundle_id,trash_bundle

def check_bundle(bundle:Path,output:Path):
    bundle=bundle.expanduser().absolute()
    canonical=Path.home()/'Applications/Sprintwake Studios.app'
    selected=bundle.resolve();protected=canonical.resolve()
    if bundle.is_symlink() or selected==protected or selected.is_relative_to(protected) or protected.is_relative_to(selected):
        raise ValueError('Use a disposable build; the installed application is protected')
    if bundle_id(bundle)!=BUNDLE_ID:
        raise ValueError('Choose a disposable Sprintwake production build')
    process=None
    try:
        output.mkdir(parents=True,exist_ok=False)
        subprocess.run(['codesign','--verify','--deep','--strict',str(bundle)],check=True,capture_output=True)
        env=dict(os.environ,SPRINTWAKE_QA_DIR=str(output.resolve()))
        for name in ('SPRINTWAKE_QA_ROOT','SPRINTWAKE_APP_BUNDLE','SPRINTWAKE_BUNDLED_PYTHON','PYTHONHOME','PYTHONPATH'):
            env.pop(name,None)
        with (output/'launch.log').open('wb') as log:
            process=subprocess.Popen([str(bundle/'Contents/MacOS/Sprintwake Studios'),'--smoke-test'],env=env,cwd='/',stdout=log,stderr=log)
            code=process.wait(timeout=300)
        if code:raise RuntimeError('Standalone window check failed; inspect '+str(output/'launch.log'))
        result=json.loads((output/'production-smoke.json').read_text())
        if not result.get('passed') or result.get('models_started') is not False:
            raise RuntimeError('Standalone check did not confirm model-free completion')
        subprocess.run(['codesign','--verify','--deep','--strict',str(bundle)],check=True,capture_output=True)
        return result
    finally:
        if process is not None and process.poll() is None:
            process.terminate();process.wait(timeout=10)
        trash_bundle(bundle,receipt_dir=output.parent/'cleanup-receipts.noindex')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=check_bundle(args.bundle,args.output)
    print(f"{len(result['passed'])} window checks passed; disposable app retired")
