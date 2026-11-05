"""Independent Sprintwake Comfy nodes.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
from .memory import SprintwakeReleaseMemory

NODE_CLASS_MAPPINGS={"SprintwakeReleaseMemory":SprintwakeReleaseMemory}
NODE_DISPLAY_NAME_MAPPINGS={"SprintwakeReleaseMemory":"Sprintwake Release Memory"}
__all__=["NODE_CLASS_MAPPINGS","NODE_DISPLAY_NAME_MAPPINGS"]

from .pending import register
register()
