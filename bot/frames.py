"""Cosmetic card frames.

Frames are drawn procedurally with Pillow, so there are no image assets to ship.
``compose`` takes the raw bytes of a piece of art and returns a PNG of that art
matted inside the chosen frame, ready to attach to a Discord message.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw

RGBA = tuple[int, int, int, int]

# How big the art may get before we mat it. Keeps composites small and fast.
MAX_ART = (640, 640)


@dataclass(frozen=True)
class Frame:
    key: str
    label: str
    border: int          # matte width in px; 0 means "no frame"
    base: RGBA           # main matte colour
    highlight: RGBA      # lit bevel edge (top / left)
    shadow: RGBA         # shaded bevel edge (bottom / right)
    accent: RGBA         # inner keyline + corner ornaments
    corner: str          # "diamond" | "circle" | "none"


def _c(r: int, g: int, b: int) -> RGBA:
    return (r, g, b, 255)


FRAMES: dict[str, Frame] = {
    "plain": Frame("plain", "Plain", 0, _c(0, 0, 0), _c(0, 0, 0), _c(0, 0, 0), _c(0, 0, 0), "none"),
    "bronze": Frame(
        "bronze", "Bronze", 30,
        base=_c(140, 94, 55), highlight=_c(198, 146, 97),
        shadow=_c(84, 52, 28), accent=_c(224, 180, 122), corner="diamond",
    ),
    "silver": Frame(
        "silver", "Silver", 30,
        base=_c(168, 172, 178), highlight=_c(228, 231, 235),
        shadow=_c(108, 112, 118), accent=_c(240, 242, 245), corner="diamond",
    ),
    "gold": Frame(
        "gold", "Gold", 34,
        base=_c(198, 158, 58), highlight=_c(246, 216, 122),
        shadow=_c(126, 94, 24), accent=_c(250, 233, 162), corner="diamond",
    ),
    "emerald": Frame(
        "emerald", "Emerald", 32,
        base=_c(34, 120, 84), highlight=_c(88, 192, 142),
        shadow=_c(16, 70, 48), accent=_c(152, 232, 192), corner="circle",
    ),
    "obsidian": Frame(
        "obsidian", "Obsidian", 32,
        base=_c(38, 40, 48), highlight=_c(80, 84, 98),
        shadow=_c(12, 12, 16), accent=_c(152, 122, 222), corner="circle",
    ),
    "rose": Frame(
        "rose", "Rose", 30,
        base=_c(196, 110, 128), highlight=_c(240, 172, 186),
        shadow=_c(126, 62, 78), accent=_c(250, 206, 216), corner="circle",
    ),
}

# Display order for pickers and listings (plain first).
CHOICES: list[Frame] = list(FRAMES.values())

DEFAULT = "plain"


def label_for(key: str) -> str:
    frame = FRAMES.get(key)
    return frame.label if frame else key


def is_frame(key: str) -> bool:
    return key in FRAMES


def _draw_frame(canvas: Image.Image, fr: Frame) -> None:
    d = ImageDraw.Draw(canvas)
    w, h = canvas.size
    b = fr.border
    radius = max(12, b // 2)

    # Solid matte body with rounded outer corners.
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=fr.base)

    # Outer bevel: lit on the top/left, shaded on the bottom/right.
    d.line([(2, radius), (2, h - radius)], fill=fr.highlight, width=2)
    d.line([(radius, 2), (w - radius, 2)], fill=fr.highlight, width=2)
    d.line([(w - 3, radius), (w - 3, h - radius)], fill=fr.shadow, width=2)
    d.line([(radius, h - 3), (w - radius, h - 3)], fill=fr.shadow, width=2)

    # The art window, bevelled the opposite way so it reads as recessed.
    win = [b - 4, b - 4, w - b + 3, h - b + 3]
    d.rectangle(win, outline=fr.shadow, width=3)
    d.rectangle([win[0] + 3, win[1] + 3, win[2] - 3, win[3] - 3], outline=fr.accent, width=2)

    if fr.corner != "none":
        r = max(4, b // 4)
        centres = [
            (b // 2, b // 2),
            (w - b // 2, b // 2),
            (b // 2, h - b // 2),
            (w - b // 2, h - b // 2),
        ]
        for cx, cy in centres:
            if fr.corner == "diamond":
                d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=fr.accent)
            else:
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fr.accent)


def compose(art_bytes: bytes, frame_key: str) -> BytesIO:
    """Return a PNG of ``art_bytes`` matted inside ``frame_key`` as a seekable buffer."""
    fr = FRAMES.get(frame_key, FRAMES[DEFAULT])

    art = Image.open(BytesIO(art_bytes)).convert("RGBA")
    art.thumbnail(MAX_ART)

    buf = BytesIO()
    if fr.border == 0:
        art.convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        return buf

    b = fr.border
    canvas = Image.new("RGBA", (art.width + b * 2, art.height + b * 2), (0, 0, 0, 0))
    _draw_frame(canvas, fr)
    canvas.paste(art, (b, b), art)

    canvas.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf
