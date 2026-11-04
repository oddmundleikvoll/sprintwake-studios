"""Preserve this user's legacy configuration after local app installation."""
import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from installation import migrate_local_settings
p=argparse.ArgumentParser();p.add_argument('--legacy-engine',required=True,type=Path);p.add_argument('--bundle',required=True,type=Path)
a=p.parse_args();migrate_local_settings(a.legacy_engine,a.bundle)
print('Existing production paths, model setup and private settings preserved.')
