"""Retire one explicitly selected disposable Sprintwake build, recoverably."""
# SPDX-License-Identifier: MIT
from pathlib import Path
import argparse
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_bundles import trash_bundle

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle',type=Path)
    parser.add_argument('--receipt-dir',type=Path)
    args=parser.parse_args()
    receipt=args.receipt_dir or args.bundle.absolute().parent/'cleanup-receipts.noindex'
    moved=trash_bundle(args.bundle,receipt_dir=receipt)
    print('Retired build; recovery receipt: '+str(receipt))
