"""Wrap a user's API-exported graph in an explicit Raven workflow profile."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from connections import write_private
from contracts.comfy_workflow import validate_profile

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('workflow',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--id',required=True);p.add_argument('--label',required=True)
    p.add_argument('--kind',choices=['image','video'],required=True)
    p.add_argument('--bind',action='append',required=True,help='logical=node:input (repeat)')
    p.add_argument('--output',required=True,help='node:images, node:videos or node:gifs')
    p.add_argument('--capabilities',type=Path,help='JSON capability object; required for video')
    a=p.parse_args()
    if a.destination.exists():p.error('destination already exists; choose a new profile file')
    if a.workflow.stat().st_size>4_000_000:p.error('API workflow is too large')
    graph=json.loads(a.workflow.read_text())
    if a.kind=='video' and not a.capabilities:p.error('video requires explicit --capabilities JSON')
    bindings={}
    try:
        for item in a.bind:
            logical,target=item.split('=',1);node,name=target.split(':',1)
            if logical in bindings:raise ValueError('duplicate logical binding')
            bindings[logical]={'node':node,'input':name}
        node,key=a.output.split(':',1)
        caps=json.loads(a.capabilities.read_text()) if a.capabilities else {'sizeAlign':1}
        doc=validate_profile({'version':1,'id':a.id,'label':a.label,'kind':a.kind,'workflow':graph,'bindings':bindings,'output':{'node':node,'key':key},'capabilities':caps})
    except (ValueError,TypeError,KeyError) as exc:p.error(str(exc))
    write_private(a.destination,doc);print('Validated profile: '+str(a.destination))

if __name__=='__main__':main()
