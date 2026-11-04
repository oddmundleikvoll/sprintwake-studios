"""Tiny ctypes interface to the Apple media bridge. No Python GUI dependency."""
import ctypes as C
from pathlib import Path


class Player:
    def __init__(self):
        self.lib = C.CDLL(str(Path(__file__).resolve().parent / "runtime/lib/libstoryboard_media.dylib"))
        types = {"prepare": (None, []), "close": (None, []),
                 "attach_window": (None, [C.c_void_p]),
                 "menu_action": (C.c_int, []),
                 "open": (C.c_int, [C.c_char_p]), "status": (C.c_int, []),
                 "error": (C.c_char_p, []), "time": (C.c_double, []),
                 "duration": (C.c_double, []), "playing": (C.c_int, []),
                 "play": (None, [C.c_int]), "mute": (None, [C.c_int]),
                 "has_audio": (C.c_int, []), "seek": (None, [C.c_double]),
                 "frame": (C.c_void_p, [C.POINTER(C.c_int), C.POINTER(C.c_int)]),
                 "choose_project": (C.c_char_p, [])}
        types.update({"choose_media": (C.c_char_p, []), "reveal": (None, [C.c_char_p]),
                      "confirm": (C.c_int, [C.c_char_p, C.c_char_p])})
        types.update({"secret_get":(C.c_char_p,[C.c_char_p]),"secret_set":(C.c_int,[C.c_char_p,C.c_char_p]),"secret_delete":(C.c_int,[C.c_char_p])})
        for name, (result, args) in types.items():
            fn = getattr(self.lib, "sr_" + name)
            fn.restype, fn.argtypes = result, args
            setattr(self, name, fn)

    def read_frame(self):
        width, height = C.c_int(), C.c_int()
        pixels = self.frame(C.byref(width), C.byref(height))
        return pixels, width.value, height.value
