"""NDI sender shared by both renderers (ndi_out.py = macOS WebKit, ndi_cdp.py = Edge/Chrome on Windows/Linux).

Talks to the installed NDI runtime with ctypes — no SDK, no compiled module. Install NDI Tools (ndi.video/tools).
"""
import ctypes, ctypes.util, glob, os, sys, threading, time

import numpy as np

FOURCC_BGRA = int.from_bytes(b"BGRA", "little")
PROGRESSIVE, SYNTH_TIMECODE = 1, 0x7FFFFFFFFFFFFFFF


def runtime_paths():
    env = [os.environ.get(v, "") for v in ("NDI_RUNTIME_DIR_V6", "NDI_RUNTIME_DIR_V5")]
    if sys.platform == "win32":
        pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]
        return ([os.path.join(d, "Processing.NDI.Lib.x64.dll") for d in env if d] +
                [p for base in pf if base for p in glob.glob(os.path.join(base, "NDI", "*", "*", "Processing.NDI.Lib.x64.dll"))] +
                [p for base in pf if base for p in glob.glob(os.path.join(base, "NDI", "*", "Processing.NDI.Lib.x64.dll"))])
    if sys.platform == "darwin":
        return [os.path.join(d, "libndi.dylib") for d in env if d] + [
            "/usr/local/lib/libndi.dylib", "/Library/NDI SDK for Apple/lib/macOS/libndi.dylib",
            "/Applications/NDI Video Monitor.app/Contents/Frameworks/libndi.dylib"]
    return ([os.path.join(d, n) for d in env if d for n in ("libndi.so.6", "libndi.so.5", "libndi.so")] +
            [p for d in ("/usr/lib", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu",
                         "/opt/ndi/lib") for p in sorted(glob.glob(os.path.join(d, "libndi.so*")), reverse=True)])


def find_runtime():
    return next((p for p in runtime_paths() if p and os.path.exists(p)), None)


class SendCreate(ctypes.Structure):
    _fields_ = [("p_ndi_name", ctypes.c_char_p), ("p_groups", ctypes.c_char_p),
                ("clock_video", ctypes.c_bool), ("clock_audio", ctypes.c_bool)]


class VideoFrame(ctypes.Structure):
    _fields_ = [("xres", ctypes.c_int), ("yres", ctypes.c_int), ("FourCC", ctypes.c_uint32),
                ("frame_rate_N", ctypes.c_int), ("frame_rate_D", ctypes.c_int),
                ("picture_aspect_ratio", ctypes.c_float), ("frame_format_type", ctypes.c_int),
                ("timecode", ctypes.c_int64), ("p_data", ctypes.c_void_p), ("line_stride_in_bytes", ctypes.c_int),
                ("p_metadata", ctypes.c_char_p), ("timestamp", ctypes.c_int64)]


def load_ndi():
    p = find_runtime()
    if not p:
        sys.exit("NDI runtime not found — install NDI Tools (ndi.video/tools)")
    lib = ctypes.CDLL(p)
    lib.NDIlib_initialize.restype = ctypes.c_bool
    lib.NDIlib_send_create.restype = ctypes.c_void_p
    lib.NDIlib_send_create.argtypes = [ctypes.POINTER(SendCreate)]
    lib.NDIlib_send_send_video_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(VideoFrame)]
    lib.NDIlib_send_get_no_connections.restype = ctypes.c_int
    lib.NDIlib_send_get_no_connections.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.NDIlib_send_destroy.argtypes = [ctypes.c_void_p]
    if not lib.NDIlib_initialize():
        sys.exit("NDI runtime refused to initialise (unsupported CPU?)")
    return lib, p


class Sender(threading.Thread):
    """Sends the latest frame at a steady rate (NDI clocks the call), whether or not the page changed."""

    def __init__(self, lib, name, w, h, fps):
        super().__init__(daemon=True)
        self.lib, self.w, self.h, self.fps = lib, w, h, fps
        self.inst = lib.NDIlib_send_create(ctypes.byref(SendCreate(name.encode(), None, True, False)))
        if not self.inst:
            sys.exit("could not create NDI sender")
        self.frame = np.zeros((h, w, 4), np.uint8)  # fully transparent until the page renders
        self.lock, self.conns, self.renderer = threading.Lock(), 0, None

    def watched(self):
        return self.conns > 0

    def update(self, frame):
        with self.lock:
            self.frame = frame

    def run(self):
        vf = VideoFrame(self.w, self.h, FOURCC_BGRA, self.fps, 1, self.w / self.h, PROGRESSIVE, SYNTH_TIMECODE,
                        None, self.w * 4, None, 0)
        n = 0
        while True:
            if n % 5 == 0 or not self.conns:  # idle: check every frame so a new receiver ramps up instantly
                c = self.lib.NDIlib_send_get_no_connections(self.inst, 0)
                if c and not self.conns and self.renderer:  # new receiver: give it a fresh picture now
                    self.renderer.dirty(300)
                self.conns = c
            with self.lock:
                f = self.frame
            vf.p_data = f.ctypes.data
            self.lib.NDIlib_send_send_video_v2(self.inst, ctypes.byref(vf))  # blocks to pace at fps
            if not self.conns:
                time.sleep(0.5)  # nobody watching: trickle at ~2 fps, source stays visible on the network
            n += 1
