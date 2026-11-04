"""Sparkle bridge and conservative native-work readiness checks."""
from __future__ import annotations
import ctypes as C
import json
from pathlib import Path

class NativeUpdater:
    def __init__(self):
        self.lib=None
        path=Path(__file__).resolve().parent/'runtime/lib/libstoryboard_update.dylib'
        if not path.is_file():return
        self.lib=C.CDLL(str(path))
        for name,result,args in [('start',C.c_int,[]),('check',None,[]),('status',C.c_char_p,[]),('request_install',None,[]),('defer',None,[]),('approve',C.c_int,[C.c_uint64])]:
            fn=getattr(self.lib,'sw_update_'+name);fn.restype=result;fn.argtypes=args
        self.lib.sw_update_start()
    def status(self):
        if self.lib:return json.loads(self.lib.sw_update_status().decode())
        return {'configured':False,'stage':'disabled','message':'Updates are available in the packaged app.','requestId':0}
    def check(self):
        if self.lib:self.lib.sw_update_check()
    def request_install(self):
        if self.lib:self.lib.sw_update_request_install()
    def defer(self):
        if self.lib:self.lib.sw_update_defer()
    def approve(self,request_id):return bool(self.lib and self.lib.sw_update_approve(request_id))

def local_blockers(studio):
    """Polling itself is harmless; all other pending work must finish."""
    blocked=[]
    if not getattr(studio,'ready',False):blocked.append('The production engine is not confirmed ready')
    for key in getattr(studio,'pending',{}):
        if key not in {'poll','updates.prepare'}:blocked.append('Active work: '+key)
    for name in ('opening','director_starting','coverage_drag','coverage_io_busy','cut_gesture_pending','sound_gesture_pending'):
        if getattr(studio,name,False):blocked.append(name)
    if getattr(studio,'dirty',False):blocked.append('Unsaved cut changes')
    return blocked
