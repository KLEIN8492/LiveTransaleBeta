#!/usr/bin/env python3
"""NDI receiver probe: finds a LiveTranslate NDI source, receives frames like OBS/vMix would, and checks them.

    .venv/bin/python tests/ndi_probe.py [--name LiveTranslate] [--seconds 5] [--png out.png]

Reports frame rate, resolution, FourCC, how much of the frame is transparent, and whether opaque text pixels are
light (white captions) — and saves one frame composited over grey so you can look at it.
"""
import argparse, ctypes, os, sys, time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ndi_lib import VideoFrame, load_ndi  # noqa: E402


class Source(ctypes.Structure):
    _fields_ = [("p_ndi_name", ctypes.c_char_p), ("p_url_address", ctypes.c_char_p)]


class RecvCreate(ctypes.Structure):
    _fields_ = [("source_to_connect_to", Source), ("color_format", ctypes.c_int), ("bandwidth", ctypes.c_int),
                ("allow_video_fields", ctypes.c_bool), ("p_ndi_recv_name", ctypes.c_char_p)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="LiveTranslate")
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--png", default="")
    a = ap.parse_args()
    lib, _ = load_ndi()
    lib.NDIlib_find_create_v2.restype = ctypes.c_void_p
    lib.NDIlib_find_get_current_sources.restype = ctypes.POINTER(Source)
    lib.NDIlib_find_get_current_sources.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.NDIlib_find_wait_for_sources.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.NDIlib_recv_create_v3.restype = ctypes.c_void_p
    lib.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(RecvCreate)]
    lib.NDIlib_recv_capture_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(VideoFrame), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    lib.NDIlib_recv_free_video_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(VideoFrame)]

    f = lib.NDIlib_find_create_v2(None)
    src, t0 = None, time.time()
    while src is None and time.time() - t0 < 10:
        lib.NDIlib_find_wait_for_sources(f, 1000)
        n = ctypes.c_uint32()
        arr = lib.NDIlib_find_get_current_sources(f, ctypes.byref(n))
        names = [arr[i].p_ndi_name.decode() for i in range(n.value)]
        hit = [i for i, nm in enumerate(names) if a.name.lower() in nm.lower()]
        if hit:
            src = Source(arr[hit[0]].p_ndi_name, arr[hit[0]].p_url_address)
    if src is None:
        sys.exit(f"no NDI source matching '{a.name}' (seen: {names})")
    print(f"found  {src.p_ndi_name.decode()}  after {time.time() - t0:.1f}s")
    rx = lib.NDIlib_recv_create_v3(ctypes.byref(RecvCreate(src, 0, 100, False, b"probe")))  # BGRX_BGRA, highest
    frames, last, t_start = 0, None, None
    while t_start is None or time.time() - t_start < a.seconds:
        v = VideoFrame()
        if lib.NDIlib_recv_capture_v2(rx, ctypes.byref(v), None, None, 1000) == 1:  # video frame
            t_start = t_start or time.time()
            frames += 1
            buf = ctypes.string_at(v.p_data, v.line_stride_in_bytes * v.yres)
            last = (np.frombuffer(buf, np.uint8).reshape(v.yres, v.line_stride_in_bytes // 4, 4)[:, :v.xres].copy(),
                    v.FourCC.to_bytes(4, "little").decode(), v.xres, v.yres, v.frame_rate_N / v.frame_rate_D)
            lib.NDIlib_recv_free_video_v2(rx, ctypes.byref(v))
    px, fcc, w, h, fr = last
    alpha = px[..., 3]
    opaque = alpha > 200
    lum = px[..., :3].astype(int).mean(axis=2)
    print(f"frames {frames} in {a.seconds:.0f}s ({frames / a.seconds:.1f} fps, nominal {fr:g}) · {w}x{h} {fcc}")
    print(f"transparent {np.mean(alpha == 0):.1%} · opaque pixels {opaque.sum()} · "
          f"light (text) {np.sum(opaque & (lum > 200))} · dark (outline/box) {np.sum(opaque & (lum < 60))}")
    if opaque.any():
        ys, xs = np.nonzero(opaque)
        print(f"caption area x {xs.min()}–{xs.max()} · y {ys.min()}–{ys.max()}")
    if a.png:
        from PIL import Image
        a_ = alpha[..., None] / 255.0
        comp = (px[..., [2, 1, 0]] * a_ + 90 * (1 - a_)).astype(np.uint8)  # over mid-grey
        Image.fromarray(comp).save(a.png)
        print("saved", a.png)


if __name__ == "__main__":
    main()
