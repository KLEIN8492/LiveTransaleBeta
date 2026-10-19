"""Draws the LiveTranslate app icon (macapp/AppIcon.png, 1024x1024).

Same family as LiveRack / VenueTuner / FOHScope: flat dark macOS tile, faint grid, no gradients, no glow.
A speech waveform (teal) on top; below it two broadcast-style caption bars, the second one dimmer
(source line + translated line), with a short amber cursor marking "live".
Run: uv run --with pillow python macapp/make_icon.py
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw

S = 4
W = 1024 * S
TILE = (22, 23, 24)
TILE_EDGE = (52, 56, 61)
GRID = (36, 39, 42)
TEAL = (79, 209, 197)
TEAL_DIM = (39, 104, 98)
FG = (214, 216, 218)
DIM = (96, 101, 106)
AMBER = (224, 179, 65)


def px(v):
    return int(round(v * S))


def rect(d, box, **kw):
    d.rectangle([px(c) for c in box], **kw)


def main():
    icon = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle([px(100), px(100), px(924), px(924)], radius=px(185), fill=TILE, outline=TILE_EDGE, width=px(4))

    left, right, top, base = 190, 834, 220, 520
    for y in (top + 100, top + 200):
        d.line([px(left), px(y), px(right), px(y)], fill=GRID, width=px(4))
    for x in (left + 161, left + 322, left + 483):
        d.line([px(x), px(top), px(x), px(840)], fill=GRID, width=px(4))

    # Speech waveform: bars with a syllable envelope.
    n, gap = 31, 6
    w = (right - left - gap * (n - 1)) / n
    mid = (top + base) / 2
    for i in range(n):
        env = abs(math.sin(i * 0.33 + 0.3)) * (0.35 + 0.65 * abs(math.sin(i * 0.11 + 1.1)))
        h = 14 + env * (base - top - 30) / 2
        x0 = left + i * (w + gap)
        rect(d, (x0, mid - h, x0 + w, mid + h), fill=TEAL if env > 0.35 else TEAL_DIM)

    # Caption bars: source (bright) and translation (dim), like a lower third.
    rect(d, (left, 600, right, 690), fill=(10, 11, 12), outline=TILE_EDGE, width=px(3))
    for x0, x1 in ((left + 30, 420), (440, 590), (610, 760)):
        rect(d, (x0, 630, x1, 660), fill=FG)
    rect(d, (left, 712, right - 120, 802), fill=(10, 11, 12), outline=TILE_EDGE, width=px(3))
    for x0, x1 in ((left + 30, 360), (380, 560)):
        rect(d, (x0, 742, x1, 772), fill=DIM)
    rect(d, (580, 736, 594, 778), fill=AMBER)  # live cursor

    out = Path(__file__).with_name("AppIcon.png")
    icon.resize((1024, 1024), Image.LANCZOS).save(out)
    print(out)


if __name__ == "__main__":
    main()
