"""keys.py <combo>|trusted - post a key combo via CoreGraphics (no osascript), or report Accessibility trust.

Combos like "c" (= cmd+c, the default modifier), "v", "cmd+shift+4", "cmd+ctrl+shift+4", "cmd+ctrl+esc".
Must run as Python.app so the Accessibility permission granted to it applies. Exit 2 if not trusted.
"""
import ctypes
import ctypes.util
import sys
import time

cg = ctypes.CDLL(ctypes.util.find_library("ApplicationServices"))
cg.AXIsProcessTrusted.restype = ctypes.c_bool
cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
cf.CFRelease.argtypes = [ctypes.c_void_p]

KEYCODE = {"c": 8, "v": 9, "4": 21, "5": 23, "esc": 53}
MOD = {"shift": 1 << 17, "ctrl": 1 << 18, "alt": 1 << 19, "cmd": 1 << 20}
HID = 0                # kCGHIDEventTap


def press(combo):
    parts = combo.lower().split("+")
    if len(parts) == 1:
        parts = ["cmd"] + parts
    flags = 0
    for m in parts[:-1]:
        flags |= MOD[m]
    for down in (True, False):
        ev = cg.CGEventCreateKeyboardEvent(None, KEYCODE[parts[-1]], down)
        cg.CGEventSetFlags(ev, flags)
        cg.CGEventPost(HID, ev)
        cf.CFRelease(ev)
        time.sleep(0.02)


trusted = cg.AXIsProcessTrusted()
if sys.argv[1] == "trusted":
    print(trusted)
    sys.exit(0 if trusted else 2)
if not trusted:
    print("keys.py: Accessibility NOT granted to this Python", file=sys.stderr)
    sys.exit(2)
press(sys.argv[1])
