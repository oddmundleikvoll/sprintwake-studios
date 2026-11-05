"""Synchronous, queue-scoped model-memory barrier for ComfyUI.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
from __future__ import annotations
import gc
from enum import Enum

def _release(mm, torch_module=None, gc_module=gc, execution_module=None):
    """Release only Comfy-owned model/cache state, in a deterministic order."""
    required=(getattr(mm,"soft_empty_cache",None),getattr(mm,"unload_all_models",None))
    if not all(callable(x) for x in required): raise RuntimeError("Comfy memory-management API is unavailable; release aborted")
    # Only classic/default or NONE caches are safe: LRU/RAM_PRESSURE can retain CPU weights.
    if execution_module is None:
        try:
            import execution as execution_module
        except ImportError as exc: raise RuntimeError("Comfy execution API is unavailable; release aborted") from exc
    cache_type=getattr(execution_module,"CacheType",None); executor_type=getattr(execution_module,"PromptExecutor",None)
    if cache_type is None or executor_type is None: raise RuntimeError("unsupported Comfy execution API; release aborted")
    allowed={getattr(cache_type,"CLASSIC",object()),getattr(cache_type,"NONE",object()),None,False}
    for executor in (x for x in gc_module.get_objects() if isinstance(x,executor_type)):
        mode=getattr(executor,"cache_type",None)
        if mode not in allowed: raise RuntimeError("active Comfy cache mode may retain CPU weights; release aborted")
        caches=getattr(getattr(executor,"caches",None),"all",None)
        if not isinstance(caches,(list,tuple)): raise RuntimeError("active Comfy cache set is unavailable; release aborted")
        for cache in caches:
            clean=getattr(cache,"clean_unused",None)
            if not callable(clean): raise RuntimeError("active Comfy cache cannot be cleaned safely; release aborted")
            clean()
    # Clear idle allocator blocks before model references are unloaded.
    mm.soft_empty_cache()
    mm.unload_all_models()
    gc_module.collect()
    mm.soft_empty_cache()
    sync=getattr(mm,"synchronize",None)
    if callable(sync): sync()
    mps=getattr(torch_module,"mps",None) if torch_module is not None else None
    backend=getattr(getattr(torch_module,"backends",None),"mps",None)
    if backend is not None and not backend.is_available(): mps=None
    synchronize=getattr(mps,"synchronize",None)
    if callable(synchronize): synchronize()
    empty=getattr(mps,"empty_cache",None) if mps is not None else None
    if callable(empty): empty()
    loaded=getattr(mm,"current_loaded_models",None)
    if loaded is None: raise RuntimeError("Comfy loaded-model state is unavailable")
    if loaded: raise RuntimeError("Comfy retained loaded model references; release not confirmed")
    return "Sprintwake memory barrier complete; Comfy model state synchronously released."

def release_memory():
    try:
        import comfy.model_management as mm
        try: import torch
        except ImportError: torch=None
    except ImportError as exc:
        raise RuntimeError("Sprintwake release node requires ComfyUI model management") from exc
    return _release(mm,torch)

class SprintwakeReleaseMemory:
    @classmethod
    def INPUT_TYPES(cls): return {"required":{}}
    RETURN_TYPES=("STRING",); RETURN_NAMES=("status",); FUNCTION="release"; CATEGORY="Sprintwake"; OUTPUT_NODE=True
    @classmethod
    def IS_CHANGED(cls): return float("nan")
    def release(self):
        status=release_memory()
        return {"ui":{"text":[status]},"result":(status,)}
