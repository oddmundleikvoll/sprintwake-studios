import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from release_artifacts import prepare_release
p=argparse.ArgumentParser(description='Prepare signed local ZIP/appcast; never publish')
p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--sparkle-tools',type=Path,default=Path(__file__).resolve().parents[1]/'runtime/Sparkle/bin')
a=p.parse_args();print(json.dumps(prepare_release(a.bundle,a.output,tools=a.sparkle_tools),indent=2))
