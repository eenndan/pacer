"""Generate the branded macOS app icon (studio/assets/pacer.icns) — dev-only, NOT a runtime dep.

This renders the approved "Speed chevron" mark at every macOS iconset size and packs a
multi-resolution `.icns` using ONLY PySide6 (already a repo dep) + stdlib `struct`. It
deliberately avoids Pillow AND Apple's `iconutil`: iconutil is broken on the build box (it
exits 1 "Failed to generate ICNS"), so we write the ICNS container ourselves — a tiny format
(a "icns" magic + length header, then one type-tagged chunk per PNG). The result is a
`file(1)`-valid "Mac OS X icon" that QIcon reloads non-null at sizes 16..1024.

The mark itself: two right-pointing chevrons (a darker one behind for depth, a brighter amber
one in front) on a rounded amber-to-canvas gradient tile — a clean generated placeholder that
mirrors the app's amber accent, open to a future design pass.

Run offscreen (no display needed) and commit the resulting binary asset:

    QT_QPA_PLATFORM=offscreen pixi run python -m studio.dev.make_icon

The output studio/assets/pacer.icns is bundled by packaging/pacer.spec (studio/assets is a
BUNDLE data dir) so it resolves in BOTH dev and frozen runs.
"""

from __future__ import annotations

import os
import struct
import tempfile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

# Colours: pulled from studio.theme.C for single-source. If importing the theme is awkward
# (it registers fonts / touches Qt on import), the literal hex fall-back below mirrors
# studio.theme.C exactly (canvas / bg / accent / accent_press).
try:
    from studio.theme import C

    CANVAS = C.canvas
    BG = C.bg
    ACCENT = C.accent
    ACCENT_PRESS = C.accent_press
except Exception:  # pragma: no cover - fall back to literals that mirror studio.theme.C
    CANVAS = "#15181E"        # mirrors studio.theme.C.canvas
    BG = "#1A1D23"            # mirrors studio.theme.C.bg
    ACCENT = "#F5A623"        # mirrors studio.theme.C.accent
    ACCENT_PRESS = "#D98E12"  # mirrors studio.theme.C.accent_press

# Where the packed icon lands (bundled by the spec via studio/assets).
_ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_ICNS_OUT = os.path.join(_ASSETS, "pacer.icns")

# Apple iconset members: (filename, pixel size). These are the sizes iconutil would expect.
ICONSET = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

# ICNS type tags -> which rendered iconset PNG carries that resolution. (icp4/icp5 are the
# small legacy PNG slots; ic07..ic14 are the modern PNG slots up to 512@2x.)
TYPE_MAP = [
    (b"icp4", "icon_16x16.png"),
    (b"icp5", "icon_32x32.png"),
    (b"ic07", "icon_128x128.png"),
    (b"ic08", "icon_256x256.png"),
    (b"ic09", "icon_512x512.png"),
    (b"ic10", "icon_512x512@2x.png"),
    (b"ic11", "icon_16x16@2x.png"),
    (b"ic12", "icon_32x32@2x.png"),
    (b"ic13", "icon_128x128@2x.png"),
    (b"ic14", "icon_256x256@2x.png"),
]


def render(px: int) -> QImage:
    """Render the "Speed chevron" mark at px-square, scaled down from a 1024 design grid."""
    k = px / 1024.0
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(k, k)

    # Rounded tile with an amber-dark vertical gradient + a faint 1px inner light edge.
    margin = 100.0
    tile = QRectF(margin, margin, 1024 - 2 * margin, 1024 - 2 * margin)
    radius = 185.0
    tilepath = QPainterPath()
    tilepath.addRoundedRect(tile, radius, radius)
    g = QLinearGradient(0, tile.top(), 0, tile.bottom())
    g.setColorAt(0.0, QColor(BG))
    g.setColorAt(1.0, QColor(CANVAS))
    p.fillPath(tilepath, QBrush(g))
    pen = QPen(QColor(255, 255, 255, 16))
    pen.setWidthF(3)
    p.setPen(pen)
    p.drawPath(tilepath)
    p.setClipPath(tilepath)

    def chevron(cx: float, color: str, w: float) -> None:
        path = QPainterPath()
        path.moveTo(cx - 96, 336)
        path.lineTo(cx + 128, 512)
        path.lineTo(cx - 96, 688)
        pn = QPen(QColor(color))
        pn.setWidthF(w)
        pn.setCapStyle(Qt.RoundCap)
        pn.setJoinStyle(Qt.RoundJoin)
        p.setPen(pn)
        p.drawPath(path)

    chevron(452, ACCENT_PRESS, 92)   # darker chevron behind (depth)
    chevron(596, ACCENT, 100)        # brighter accent chevron in front
    p.end()
    return img


def pack_icns(iconset_dir: str, out_path: str) -> None:
    """Pack the rendered iconset PNGs into a single multi-resolution .icns at out_path.

    ICNS layout: a `icns` magic + a big-endian u32 total-length header, then one chunk per
    image: 4-byte OSType tag + big-endian u32 (8 + len(png)) + the raw PNG bytes.
    """
    body = b""
    for ostype, fname in TYPE_MAP:
        with open(os.path.join(iconset_dir, fname), "rb") as f:
            png = f.read()
        body += ostype + struct.pack(">I", 8 + len(png)) + png
    data = b"icns" + struct.pack(">I", 8 + len(body)) + body
    with open(out_path, "wb") as f:
        f.write(data)


def main() -> int:
    # A QGuiApplication is needed for QImage/QPainter; harmless offscreen.
    app = QGuiApplication.instance() or QGuiApplication([])
    os.makedirs(_ASSETS, exist_ok=True)

    with tempfile.TemporaryDirectory() as iconset_dir:
        # Render every iconset member. Distinct filenames may share a pixel size (e.g. @2x
        # of one size equals the 1x of the next); we render each independently for clarity.
        for fname, px in ICONSET:
            img = render(px)
            img.save(os.path.join(iconset_dir, fname), "PNG")
        pack_icns(iconset_dir, _ICNS_OUT)

    # Acceptance line: reload the packed icon and report the sizes QIcon recovered.
    ic = QIcon(_ICNS_OUT)
    sizes = sorted(s.width() for s in ic.availableSizes())
    print(f"wrote {_ICNS_OUT} ({os.path.getsize(_ICNS_OUT)} bytes); "
          f"QIcon null={ic.isNull()} sizes={sizes}")
    del app  # keep flake happy; the instance lives for process lifetime anyway
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
