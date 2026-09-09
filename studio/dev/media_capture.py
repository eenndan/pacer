"""Regenerate every image in `docs/media/` — dev-only, NOT a runtime dep.

Until this file existed the media pipeline was undocumented anywhere in the repo: the six PNGs
under `docs/media/` were all dated 2026-07-15 and their provenance survived only in matching pixel
dimensions. They went stale through 458 commits, and one of them — `plots.png`, live on the landing
page — was a picture of the flat zero-Δ line the ideal-lap wave had since fixed. A marketing image
nobody can regenerate is a marketing image that eventually lies. So: one script, one command.

    QT_QPA_PLATFORM=offscreen PYTHONPATH=bindings/pacer \\
        pixi run python -m studio.dev.media_capture ~/Desktop/D24/GX010062.MP4 --out docs/media

The POSITIONAL argument is the INPUT recording (chapter 1; siblings are discovered by the app's own
`chapters.discover_siblings`). The OUTPUT directory is behind `--out`, and defaults to `docs/media`.
That order matters: a previous agent destroyed 11.9 GB of the owner's only copy of a race recording
by passing a path positionally to a tool whose `argv[1]` was its output. Never open `GX010060.MP4`
— that file is the destroyed 2.4 MB JSON stub (its intact siblings GX020060/GX030060 are fine;
`Session.load` skips the stub and says so).

WHAT IT PRODUCES, AND THE CLAIM EACH IMAGE CARRIES

  hero.png        four-panel main window       "one window, everything, from one MP4"
  ideal-lap.png   Stats > IDEAL LAP            a synthesised target that says what it was
                                               minimised over, which lap donated each piece, and
                                               whose gain column sums to the tile
  data-trust.png  Stats > DATA TRUST + g-g     the accuracy story INSIDE the product, incl. the
                                               IMU<->GPS cross-check with its GAIN (r alone cannot
                                               catch a mis-scaled channel)
  map.png         map maximised, key open      "the racing line is a data channel, and every mark
                                               on it is named"
  overlay.png     a frame of a real export     "the analysis leaves the app as something you can
                                               post"
  accuracy.png    the transponder chart        drawn here from `ACCURACY` below, so the numbers are
                                               auditable in code instead of burned into artwork
  og.png          the social card              composed from hero.png, so it can never again embed
                                               a stale screenshot

TWO THINGS THE OFFSCREEN RENDER CANNOT DO BY ITSELF, AND WHAT THIS DOES ABOUT THEM

1. THE VIDEO PANE IS INERT.  `PACER_NO_MEDIA=1` swaps `QVideoWidget` for a plain `QWidget`
   (`player_pane.py`), and even with real media a macOS `QVideoWidget` composites through a native
   surface `widget.grab()` cannot see. So the hero is a COMPOSITE: the window is a real render, and
   a real frame — pulled with ffmpeg from the same recording, at the same media instant the rest of
   the window is describing, letterboxed into the video widget's true rect exactly as
   `Qt::KeepAspectRatio` would — is painted into the pane. Every other pixel is the app's own.
   `--no-video-frame` skips the composite and leaves the pane as the harness renders it.

2. A SEAM REOPEN NEVER COMPLETES.  Seeking across a chapter boundary puts "loading next chapter…"
   on the panel chip and waits for `LoadedMedia`, which the inert player never emits. `_settle_ui`
   calls the app's own `_on_seam_loading(False)` — the exact call the real app makes the moment the
   next chapter presents — so the chip reads the chapter the frame actually came from.

The PB toast is dismissed for the same reason: it is a genuine first-session moment, but the
capture's library seam is empty on every run, so it fires every time and covers the lap table.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

# Offscreen Qt + inert media BEFORE any Qt import (PlayerPane reads PACER_NO_MEDIA at construction).
# `setdefault`, so running this under the QA write-jail harness — which sets both at ITS import,
# earlier — leaves the harness's choices alone.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

from studio import APP_NAME, chapters, export_video, theme  # noqa: E402
from studio.app import StudioWindow  # noqa: E402
from studio.dev import _jail  # noqa: E402
from studio.theme import C  # noqa: E402

# ====================================================================== capture geometry
# ONE logical window size for every app shot, so the images read as one set: 1440x900 is the
# campaign's standard review size and the smaller of the two the design guards are measured at.
WINDOW = (1440, 900)
# The Stats page is a ~1400 px scroll column. DATA TRUST sits at its top and the friction circle
# ~950 px below it, so no 900 px viewport holds both. The stats shots therefore render into a TALL
# window: nothing about the page is height-dependent (it is one vertical column in a scroll area),
# so every crop is the same layout the 900 px window shows, just all of it at once.
WINDOW_TALL = (1440, 1500)
# The map shot gets its own window shape, for two measured reasons.
#
# FRAMING. Maximized in a 1440x900 window the map panel is 1432x846 and the fitted track's bounding
# box is ~0.88 wide-to-tall, so it binds on HEIGHT and the racing line uses 47 % of the frame's
# width; the rest is empty canvas. At 1432x1286 the panel is 1.11 and the track uses ~75 %.
#
# LABEL CLIPPING. `MapView._fit_view` pads the fitted view by 2 % of the DATA extent, and a corner's
# name is a TextItem drawn ~30 px BEYOND its apex in screen space — so whichever corner sits at the
# extreme of the BINDING axis has its label clipped by the plot edge, at every panel size under
# ~1500 px in that axis (2 % x 1500 = 30). It is a real, if small, app bug and it is not this lane's
# file: the choice here is only WHICH label. Height-binding clips C8, at the bottom edge beside the
# speed legend; width-binding clips C9, mid-frame on the right, which reads far worse.
WINDOW_MAP = (1440, 1340)
DPR = 2                       # retina; every PNG below is 2x its logical size
PAD = theme.SPACE_M           # breathing room around a cropped section, in logical px

# `og.png` is consumed by the Open Graph / Twitter card scrapers, which want 1200x630-ish at 1.91:1.
OG_SIZE = (1280, 640)
ACCURACY_SIZE = (1200, 540)   # the transponder chart's logical size (rendered at DPR)

_LOAD_TIMEOUT_S = 300.0
_SETTLE_PUMPS = 8

# ~/Desktop/D24/GX010060.MP4 is a 2.4 MB JSON stub that overwrote 11.9 GB of the owner's footage.
# The ban used to cover EVERY `…060` chapter, because `discover_siblings` routes them all through
# the stub and `GPMFSource` then failed the whole recording. `Session.load` now skips a sibling
# that isn't an MP4 (chapters.split_non_mp4), so chapters 2+3 open fine and only the stub itself
# is refused here — opening it directly would still ask the media stack to play a JSON file.
_BANNED_RE = re.compile(r"^G[XHLP]010060\.MP4$", re.IGNORECASE)

# Which lap the shots describe, and where in it. The BEST lap is the one the app selects and stars,
# and `_LAP_FRACTION` puts the playhead a third of the way round it — far enough in that the Δideal
# readout, the map's position marker and the chart playhead all carry a real number instead of the
# zeroes every lap starts on.
_LAP_FRACTION = 0.34
# The exported clip is deliberately NOT the best lap: the burned-in Δ is Δ-to-best, so a best-lap
# export reads +0.00 for its whole length. `_export_lap` picks the fastest lap that is not the best.
_EXPORT_FRAME_FRACTION = 0.42
# …and the frame is pulled out at 1440 wide rather than the export's own 1080p. A frame of real
# footage is the only photographic image in the set and it does not compress: at 1920x1080 it was
# 2.76 MB, more than half the weight of docs/media on its own, against 1.66 MB here — invisible on
# a page that renders it under 1000 px, and every burned-in element is a FRACTION of frame height
# (`OverlayConfig.strip_h_frac` etc.), so they scale with it and stay legible.
OVERLAY_WIDTH = 1440


# ====================================================================== the transponder numbers
# The accuracy chart is DRAWN, from these values, rather than hand-designed in a graphics editor —
# which is how the old one came to have "300+ laps" and "850+ laps" burned into it. Those two were
# `csv_lap_range` (`studio/dev/_validate_wallclock.py:284`): the FIRST and LAST lap IDs of the
# aligned span in a 24-hour transponder log, quoted as if they were counts. The span's LENGTH is
# the aligned-lap count — the validator builds it at `:230` as
# `csv_ids = [start + k for k in range(len(valid))]`, one per app valid lap — so 856..920 is 65
# laps, not "850+", and the app independently reports 65 valid laps on that recording.
#
# `n` IS THE CLEAN COUNT, NOT THE ALIGNED ONE, because the clean laps are what the mean and σ were
# measured over: `_validate_wallclock.py` reports three masks and the quoted row is the third
# (racing laps ≤ 72 s, GPS-dropout laps excluded). Clean is NOT aligned-minus-dropout — the aligned
# window also holds non-racing laps the ≤ 72 s mask drops — so it has to be read off the record
# rather than derived: `studio/docs/upstream-20ms-investigation.md:174,176` carries `clean n` 48 and
# 59 on the rows holding these exact mean/σ/RMS, corroborated by
# `studio/docs/start-line-verification.md:87-88`. 107 clean laps, not "~110" and not "1,150+".
#
# NO CORRELATION FIGURE ANYWHERE ON THIS IMAGE. The r ≥ 0.99 the old copy quoted is `best_offset`'s
# ARGMAX over candidate alignments — the lock that establishes the pairing — so quoting it as an
# independent accuracy statistic is circular. The honest and stronger fact is the lock's
# UNIQUENESS: every non-locked offset scores below 0.29, a margin of about +0.70
# (`start-line-verification.md:89`). That is what the caption says instead.
#
# σ-as-a-percentage deliberately quotes the WORSE recording.
LAP_S = 68.0                  # a representative kart lap at this circuit, for the σ-as-% claim
ACCURACY = [
    {"name": "Recording A", "note": "higher-noise GPS",
     "mean": 0.0030, "sigma": 0.0871, "clean": 48, "aligned": 57, "dop": 2.4},
    {"name": "Recording B", "note": "cleaner GPS",
     "mean": 0.0015, "sigma": 0.0527, "clean": 59, "aligned": 65, "dop": 1.4},
]
ACCURACY_TITLE = "Lap timing, validated against a transponder"
ACCURACY_SUB = ("Pacer lap time − transponder ground truth · out-of-sample · "
                "GPS9 true clock, default pipeline")
ACCURACY_AXIS = "difference from transponder lap time  (seconds)"

OG_TAGLINE = "Race telemetry from your GoPro"
OG_BODY = ("Transponder-validated true-clock lap timing, a synthesised ideal lap,\n"
           "a speed-coloured track map, synced video and corner-by-corner coaching.")
# Four FACTS, not four promises. The July card's chips ("free", "private") were product-launch
# copy for a page that is now a case study; these are things the repo can be checked against.
OG_CHIPS = ("transponder-validated", "100 % local", "GoPro GPMF", "source-available")


# ====================================================================== app plumbing
def _suppress_modals() -> None:
    """The load guard reports failures through a MODAL QMessageBox — offscreen nothing can dismiss
    it and it would block forever. Swallow them (same trick as `ui_capture.py`)."""
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Critical)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Warning)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Information)


def boot_app() -> QApplication:
    """Build the QApplication and theme it BEFORE any widget exists.

    LOAD-BEARING, and the trap `ui_capture.py` documents too: without `apply_theme` the whole window
    renders in Qt's DEFAULT LIGHT palette — a false "unstyled" look that is a missing-setup artefact
    and would make every image on the landing page wrong in the same way."""
    if DPR != 1:
        os.environ.setdefault("QT_SCALE_FACTOR", str(DPR))
    app = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else "media_capture"])
    theme.register_fonts()
    theme.apply_theme(app)
    _suppress_modals()
    return app


def settle(app: QApplication, n: int = _SETTLE_PUMPS) -> None:
    """Qt needs ~4 pumps for a resize to propagate through nested splitters into final geometry;
    measuring or grabbing before that is the standard source of a half-laid-out screenshot."""
    for _ in range(n):
        app.processEvents()


def open_window(app: QApplication, recording: str, size=WINDOW) -> StudioWindow:
    """Open the WHOLE recording in the REAL StudioWindow, laid out and settled at `size`.

    All chapters, not just the one named: `StudioWindow([path])` is the File ▸ Open path and loads
    that chapter alone, which on this recording is 21 clean laps instead of 65 — and the copy these
    images sit beside quotes 65. `discover_siblings` is the app's own File ▸ Load full recording
    path, so the pixels and the prose agree."""
    if _BANNED_RE.match(os.path.basename(recording)):
        raise SystemExit(f"REFUSED: {recording} is the destroyed 2.4 MB JSON stub, not footage — "
                         "name another chapter of the recording (GX020060.MP4).")
    paths = chapters.discover_siblings(recording)
    print(f"media_capture: opening {len(paths)} chapter(s): "
          f"{', '.join(os.path.basename(p) for p in paths)}")
    w = StudioWindow(list(paths))
    deadline = time.time() + _LOAD_TIMEOUT_S
    while w.view is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.005)
    if w.view is None:
        raise RuntimeError(f"session load did not complete within {_LOAD_TIMEOUT_S:.0f} s")
    w.resize(int(size[0]), int(size[1]))
    w.show()
    settle(app)
    return w


def _settle_ui(app: QApplication, w: StudioWindow) -> None:
    """Undo the two states the inert player leaves behind (see this module's docstring): the
    never-completing seam hint, and the first-session PB toast the empty library seam re-fires on
    every run."""
    toast = getattr(w, "_pb_toast", None)
    if toast is not None:
        toast.close()
    view = w.view
    if getattr(view, "_seam_loading", False):
        view._on_seam_loading(False)   # the app's own "the next chapter presented" path
    settle(app)


def seek(app: QApplication, w: StudioWindow, t: float) -> None:
    """Put the whole window at global media time `t` — the pair the live tick drives: the player
    pane resolves (chapter, local) and the view repaints readout, map marker and chart playhead."""
    view = w.view
    view.video.pane.seek(t)
    view._apply_position(t)
    _settle_ui(app, w)


def lap_time_at(session, lap_id: int, fraction: float) -> float:
    """Global media time `fraction` of the way through `lap_id`."""
    t0, t1 = session.lap_window(lap_id)
    return t0 + fraction * (t1 - t0)


# ====================================================================== grabbing
def grab(widget) -> QImage:
    """The widget's offscreen render as a raw QImage at device resolution (dpr stripped, so the
    saved PNG is exactly `logical * DPR` pixels and nothing downstream re-scales it)."""
    img = widget.grab().toImage()
    img.setDevicePixelRatio(1.0)
    return img


def region(w: StudioWindow, rect: QRect) -> QImage:
    """Crop `rect` (LOGICAL window coordinates) out of the WINDOW composite.

    Always the window, never the child: `QWidget.grab()` on a child lies about anything the
    stylesheet box paints, because QStyleSheetStyle writes the rule's colour into the palette even
    when nothing composites it. Coordinates are multiplied by the device pixel ratio because
    `QPixmap.copy()` works in device pixels while every Qt geometry is logical."""
    r = w.devicePixelRatioF()
    dev = QRect(round(rect.x() * r), round(rect.y() * r),
                round(rect.width() * r), round(rect.height() * r))
    img = w.grab().copy(dev).toImage()
    img.setDevicePixelRatio(1.0)
    return img


def span(w: StudioWindow, first, last, right: int) -> QRect:
    """The TIGHT logical rect from the top of `first` to the bottom of `last`, `right` px wide.

    NO PADDING, and that is the point. The Stats page's sections sit **4 px** apart — `ideal_note`
    ends at y=887 and the `SPEED · G` heading starts at y=891 — so a crop padded by even the
    smallest space token frames the first pixels of a neighbouring section and the image reads as a
    mis-cut screenshot. Breathing room is added afterwards, as canvas, by `matte`."""
    top = first.mapTo(w, QPoint(0, 0)).y()
    bottom = last.mapTo(w, QPoint(0, 0)).y() + last.height()
    return QRect(0, top, right, bottom - top)


def right_edge(w: StudioWindow, widgets, floor: int = 0) -> int:
    """How far right the CONTENT actually paints, in window coords.

    Not the widgets' widths: a Stats section heading is a 1396 px QLabel holding six characters,
    and a `WrapLabel` is as wide as the column it may use, not as wide as the line it set. So a
    label contributes the lesser of its own width and its text's advance; anything else (a table,
    a plot) contributes its full width because it paints to its edges."""
    edge = floor
    for x in widgets:
        left = x.mapTo(w, QPoint(0, 0)).x()
        if isinstance(x, QLabel):
            adv = QFontMetrics(x.font()).horizontalAdvance(x.text())
            edge = max(edge, left + min(x.width(), adv))
        else:
            edge = max(edge, left + x.width())
    return edge


def matte(img: QImage, pad: int = PAD, colour: str = C.canvas) -> QImage:
    """Surround a tight crop with `pad` LOGICAL px of the app's own canvas."""
    d = pad * DPR
    out = QImage(img.width() + 2 * d, img.height() + 2 * d, QImage.Format_RGB32)
    out.fill(QColor(colour))
    p = _painter(out)
    p.drawImage(d, d, img)
    p.end()
    return out


def save(img: QImage, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not img.save(path):
        raise RuntimeError(f"failed to write {path}")
    print(f"wrote {path}  {img.width()}x{img.height()}  {os.path.getsize(path):,} B")
    return path


def _painter(img: QImage) -> QPainter:
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    return p


def _canvas(w: int, h: int, colour: str = C.canvas) -> QImage:
    img = QImage(w * DPR, h * DPR, QImage.Format_RGB32)
    img.fill(QColor(colour))
    return img


# ====================================================================== 1 — hero
def _ffmpeg_frame(src: str, local_t: float, out_png: str, width: int | None = None) -> str | None:
    """One frame of real footage at `local_t` seconds into `src`. `-ss` BEFORE `-i` is the fast
    seek; `-frames:v 1` writes exactly one PNG. `width` scales it on the way out (lanczos, height
    to an even number). Returns the path, or None if ffmpeg is unavailable — the hero then renders
    with an empty video pane rather than failing the whole run."""
    try:
        ffmpeg = export_video._resolve_binary("ffmpeg", "PACER_FFMPEG")
    except Exception as exc:  # noqa: BLE001 — an absent ffmpeg is a degraded run, not a crash
        print(f"media_capture: no ffmpeg ({exc}); hero video pane left empty")
        return None
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{local_t:.3f}", "-i", src, "-frames:v", "1"]
    if width:
        cmd += ["-vf", f"scale={int(width)}:-2:flags=lanczos"]
    cmd += [out_png]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_png if os.path.exists(out_png) else None


def _paint_frame_into(img: QImage, frame_png: str, rect: QRect) -> None:
    """Paint `frame_png` into `rect` (LOGICAL window coords) of an already-grabbed window image,
    letterboxed exactly as `QVideoWidget`'s default `Qt::KeepAspectRatio` would: scaled to fit,
    centred, with the pane's own background showing through the bars."""
    dev = QRect(rect.x() * DPR, rect.y() * DPR, rect.width() * DPR, rect.height() * DPR)
    src = QPixmap(frame_png)
    scaled = src.scaled(dev.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    x = dev.x() + (dev.width() - scaled.width()) // 2
    y = dev.y() + (dev.height() - scaled.height()) // 2
    p = _painter(img)
    p.fillRect(dev, QColor("#000000"))
    p.drawPixmap(x, y, scaled)
    p.end()


def shot_hero(app: QApplication, w: StudioWindow, out_dir: str, work_dir: str,
              with_video: bool = True) -> str:
    """The four-panel window on the best lap, a third of the way round it."""
    view = w.view
    if view._maximized_panel is not None:
        view._toggle_panel_maximized(view._maximized_panel)
    view.tab_bar.setCurrentIndex(0)
    w.resize(*WINDOW)
    settle(app)
    lap = w.session.best_lap_id()
    # The app already selects and stars the best lap on load; assert rather than re-select, so a
    # change to that default surfaces here instead of being papered over.
    selected = view.table.selected_lap_ids()
    if selected != [lap]:
        view.table.select([lap])
        view.table._on_selection()   # select() blocks signals; this is the user's click path
        settle(app)
    t = lap_time_at(w.session, lap, _LAP_FRACTION)
    seek(app, w, t)
    img = grab(w)

    if with_video:
        pane_widget = view.video.pane.video          # the QVideoWidget / its inert stand-in
        origin = pane_widget.mapTo(w, QPoint(0, 0))
        rect = QRect(origin.x(), origin.y(), pane_widget.width(), pane_widget.height())
        index, local = w.session.chapters.to_local(t)
        src = w.session.chapters.chapters[index].path
        # The pulled frame lands in `work_dir` beside the exported MP4 — a named, inspectable
        # intermediate, and no `TemporaryDirectory`. `shutil.rmtree` walks with `os.unlink(name,
        # dir_fd=…)`, i.e. a RELATIVE name, which a path-based write guard resolves against the CWD
        # instead of the fd; under the QA jail that reported a write to a repo file that was never
        # created. Not worth manufacturing a false alarm for a two-line cleanup.
        frame = _ffmpeg_frame(src, local, os.path.join(work_dir, "hero_frame.png"))
        if frame:
            print(f"media_capture: hero frame = {os.path.basename(src)} @ {local:.3f}s "
                  f"(global {t:.3f}s, lap {lap}) into {rect.width()}x{rect.height()} px pane")
            _paint_frame_into(img, frame, rect)
    return save(img, os.path.join(out_dir, "hero.png"))


# ====================================================================== 2/3 — the Stats page
def _stats_page(app: QApplication, w: StudioWindow):
    """Switch to the Stats tab and maximize the lap panel — the state the ⛶ button and ⌘⇧S put the
    app in, and the only one where the page is read rather than glanced at.

    Each shot sets its OWN window size rather than inheriting whatever the previous one left, so a
    run of one shot and a run of all seven produce identical pixels."""
    view = w.view
    w.resize(*WINDOW_TALL)
    settle(app)
    view.tab_bar.setCurrentIndex(2)
    settle(app)
    if view._maximized_panel is not view._table_panel:
        view._toggle_panel_maximized(view._table_panel)
    settle(app)
    _settle_ui(app, w)
    return view.stats_view


def _heading(stats, text: str) -> QLabel:
    """The section heading QLabel with this text (`stats_panel._section` builds one per group)."""
    for label in stats.findChildren(QLabel):
        if label.text() == text:
            return label
    raise LookupError(f"no Stats section heading {text!r}")


def shot_ideal(app: QApplication, w: StudioWindow, out_dir: str) -> str:
    """IDEAL LAP: the heading, both tiles, the sample line, the segment table and the remainder
    note — the whole block, because the honesty IS the relationship between them."""
    stats = _stats_page(app, w)
    edge = right_edge(w, (stats.t_theoretical, stats.t_ideal_gap, stats.ideal_sample,
                          stats.ideal_table, stats.ideal_note))
    rect = span(w, _heading(stats, "IDEAL LAP"), stats.ideal_note, right=edge + PAD)
    print(f"media_capture: ideal-lap crop {rect.width()}x{rect.height()} logical · "
          f"sample line = {stats.ideal_sample.text()[:60]}…")
    return save(matte(region(w, rect)), os.path.join(out_dir, "ideal-lap.png"))


def shot_data_trust(app: QApplication, w: StudioWindow, out_dir: str) -> str:
    """DATA TRUST + the g-g friction circle, stacked.

    TWO CROPS, ONE IMAGE, AND IT SAYS SO. Both are real renders of the SAME window in the SAME
    frame; they are ~950 px apart on a scroll column no viewport can show at once, so they are
    joined with `SPACE_XL` of the app's own canvas between them. Nothing is redrawn, moved or
    retouched — each half carries its own in-app section heading, so a reader sees two sections of
    one page rather than a fabricated panel."""
    stats = _stats_page(app, w)
    edge = right_edge(w, [stats.gg, stats.gg_key, *stats.trust_card.findChildren(QLabel)]) + PAD
    top = span(w, _heading(stats, "DATA TRUST"), stats.trust_card, right=edge)
    bottom = span(w, _heading(stats, "FRICTION CIRCLE · g"), stats.gg_key, right=edge)
    a, b = region(w, top), region(w, bottom)

    gap = theme.SPACE_2XL * DPR
    img = QImage(max(a.width(), b.width()), a.height() + gap + b.height(), QImage.Format_RGB32)
    img.fill(QColor(C.canvas))
    p = _painter(img)
    p.drawImage(0, 0, a)
    p.drawImage(0, a.height() + gap, b)
    # A VISIBLE seam, on purpose. Butted together with the page's own SPACE_XL between them the two
    # crops read as adjacent sections, which they are not — six section groups sit between DATA
    # TRUST and the friction circle. A hairline in the app's border tone says "two panels" and
    # costs the image nothing.
    p.setPen(QPen(QColor(C.border), theme.BORDER_PX * DPR))
    seam = a.height() + gap // 2
    p.drawLine(0, seam, img.width(), seam)
    p.end()
    print(f"media_capture: data-trust = DATA TRUST {top.width()}x{top.height()} + "
          f"FRICTION CIRCLE {bottom.width()}x{bottom.height()} logical, {theme.SPACE_2XL} px apart")
    return save(matte(img), os.path.join(out_dir, "data-trust.png"))


# ====================================================================== 4 — the map
def shot_map(app: QApplication, w: StudioWindow, out_dir: str) -> str:
    """The map maximised with its key expanded: the racing line coloured by speed, every lap's
    trace behind it, brake points, corner apexes, the start line, and a key that names all of it."""
    view = w.view
    view.tab_bar.setCurrentIndex(0)
    settle(app)
    if view._maximized_panel is not None:
        view._toggle_panel_maximized(view._maximized_panel)
    w.resize(*WINDOW_MAP)
    settle(app)
    view._toggle_panel_maximized(view._map_panel)
    settle(app)
    key = view.map._map_key
    if key.collapsed():
        # The whole plate is the affordance — click it, rather than poking `_collapsed`, so the
        # re-pin and the preference write both run exactly as a user's click would.
        centre = QPointF(key.width() / 2, key.height() / 2)
        key.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, centre,
                                        key.mapToGlobal(centre.toPoint()).toPointF(),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    settle(app)
    _settle_ui(app, w)
    origin = view._map_panel.mapTo(w, QPoint(0, 0))
    rect = QRect(origin.x(), origin.y(), view._map_panel.width(), view._map_panel.height())
    print(f"media_capture: map panel {rect.width()}x{rect.height()} logical, "
          f"key expanded={not key.painted_collapsed()}")
    return save(region(w, rect), os.path.join(out_dir, "map.png"))


# ====================================================================== 5 — the exported overlay
def _export_lap(session) -> int:
    """The fastest lap that is NOT the session best. The burned-in Δ is Δ-to-best, so exporting the
    best lap paints `+0.00` across the whole clip against a baseline that is the lap itself."""
    best = session.best_lap_id()
    ids = [i for i in session.valid_lap_ids() if i != best]
    return min(ids, key=session.lap_time)


def shot_overlay(w: StudioWindow, out_dir: str, work_dir: str) -> str:
    """A frame of a REAL export — the same `export_video.render_lap` File ▸ Export overlay video…
    drives, at the app's default 1080p/high, then one frame pulled back out with ffmpeg."""
    session = w.session
    lap = _export_lap(session)
    mp4 = os.path.join(work_dir, f"lap{lap}_overlay.mp4")
    cfg = export_video.OverlayConfig(speed_unit=w._speed_unit, palette=theme.active_palette())
    t0 = time.time()
    result = export_video.render_lap(session, src_path=None, out_path=mp4, lap_id=lap, config=cfg,
                                     progress=None, cancel=None)
    dur = session.lap_time(lap)
    print(f"media_capture: exported lap {lap} ({dur:.3f} s) -> {result.out_path} "
          f"in {time.time() - t0:.1f} s")
    png = os.path.join(work_dir, "overlay_frame.png")
    _ffmpeg_frame(mp4, _EXPORT_FRAME_FRACTION * dur, png, width=OVERLAY_WIDTH)
    img = QImage(png)
    img.setDevicePixelRatio(1.0)
    return save(img, os.path.join(out_dir, "overlay.png"))


# ====================================================================== 6 — the accuracy chart
def _text(p: QPainter, x: float, y: float, s: str, font: QFont, colour: str,
          align=Qt.AlignLeft | Qt.AlignVCenter, width: float = 2000.0) -> None:
    """One line of text with its baseline box anchored at (x, y); `y` is the VERTICAL CENTRE."""
    p.setFont(font)
    p.setPen(QPen(QColor(colour)))
    fm = QFontMetrics(font)
    box = QRectF(x, y - fm.height(), width, fm.height() * 2)
    if align & Qt.AlignRight:
        box = QRectF(x - width, y - fm.height(), width, fm.height() * 2)
    p.drawText(box, int(align), s)


def draw_accuracy(out_dir: str) -> str:
    """The transponder chart: two intervals (mean ± σ) against a zero line.

    A RANGE PLOT, not a categorical chart — one measure, one hue. The accent belongs to the
    MEASUREMENT and to nothing else: the zero reference is a neutral `text_muted` rule, the grid and
    axis are recessive `border`, and every word wears a text token, so the only amber on the page is
    the data. Each row is direct-labelled, twice (its name at the left, its numbers at the right),
    so identity never rests on colour."""
    W, H = ACCURACY_SIZE
    img = _canvas(W, H)
    p = _painter(img)
    p.scale(DPR, DPR)

    f_title = theme.ui_font(22, theme.W_SEMIBOLD)
    f_sub = theme.ui_font(theme.BODY)
    f_row = theme.ui_font(theme.BODY, theme.W_SEMIBOLD)
    f_val = theme.mono_font(theme.BODY)
    f_cap = theme.ui_font(theme.CAPTION)

    m = theme.SPACE_3XL              # page margin
    _text(p, m, 52, ACCURACY_TITLE, f_title, C.text)
    _text(p, m, 88, ACCURACY_SUB, f_sub, C.text_dim)

    # --- the two text gutters, MEASURED from the strings that go in them rather than guessed.
    # A hardcoded gutter is how a chart ships with its longest label a few pixels off the page: the
    # first cut of this one reserved 250 px for a column whose widest line sets 235, and correcting
    # "57 aligned laps" to "48 clean of 57 aligned laps" pushed it 9 px past the margin.
    stats = [(f"mean {r['mean']:+.4f} s   ·   σ {r['sigma']:.4f} s",
              f"{r['clean']} clean of {r['aligned']} aligned laps · median DOP {r['dop']}")
             for r in ACCURACY]
    fm_val, fm_cap, fm_row = QFontMetrics(f_val), QFontMetrics(f_cap), QFontMetrics(f_row)
    gutter_r = max(max(fm_val.horizontalAdvance(s), fm_cap.horizontalAdvance(c))
                   for s, c in stats) + theme.SPACE_XL
    gutter_l = max(max(fm_row.horizontalAdvance(r["name"]),
                       fm_cap.horizontalAdvance(r["note"])) for r in ACCURACY) + theme.SPACE_XL

    # --- the claim card is laid out FIRST, because the plot's floor sits on top of it. Its height
    # is whatever the wrapped sentence needs, so the copy can grow without colliding with the axis
    # title (it did, the first time the lap counts were corrected and the caption ran to two lines).
    worst = max(ACCURACY, key=lambda r: r["sigma"])
    total = sum(r["clean"] for r in ACCURACY)
    claim = (f"Unbiased to within ±0.003 s. σ {worst['sigma']:.4f} s on the worse recording is "
             f"{worst['sigma'] / LAP_S * 100.0:.2f} % of a ~{LAP_S:.0f} s kart lap — the noise "
             f"floor of 10 Hz GPS. {total} clean laps, paired lap-for-lap by a per-lap duration "
             f"fingerprint that locks at exactly one offset.")
    inner_w = W - 2 * m - 2 * theme.SPACE_L
    flags = int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter)
    text_h = fm_cap.boundingRect(QRect(0, 0, int(inner_w), 400), flags, claim).height()
    card = QRectF(m, H - theme.SPACE_XL - (text_h + 2 * theme.SPACE_M),
                  W - 2 * m, text_h + 2 * theme.SPACE_M)

    # --- plot frame. x maps ±X_MAX seconds across [plot_l, plot_r]; the floor leaves room for the
    # tick row (+20) and the axis title (+42) between it and the card.
    X_MAX = 0.30
    plot_l, plot_r = m + gutter_l, W - m - gutter_r
    top, bottom = 150.0, card.y() - 62.0

    def px(v: float) -> float:
        return plot_l + (v + X_MAX) / (2 * X_MAX) * (plot_r - plot_l)

    p.setPen(QPen(QColor(C.border), theme.BORDER_PX, Qt.DashLine))
    for tick in (-0.3, -0.2, -0.1, 0.1, 0.2, 0.3):
        p.drawLine(int(px(tick)), int(top), int(px(tick)), int(bottom))
    p.setPen(QPen(QColor(C.text_muted), theme.SPACE_XXS))
    p.drawLine(int(px(0)), int(top - 8), int(px(0)), int(bottom + 8))

    # --- one row per recording
    rows = len(ACCURACY)
    for i, rec in enumerate(ACCURACY):
        y = top + (bottom - top) * (i + 0.5) / rows
        _text(p, m, y - 8, rec["name"], f_row, C.text)
        _text(p, m, y + 12, rec["note"], f_cap, C.text_dim)

        lo, hi, mu = px(rec["mean"] - rec["sigma"]), px(rec["mean"] + rec["sigma"]), px(rec["mean"])
        p.setPen(QPen(QColor(C.accent), 3, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(int(lo), int(y), int(hi), int(y))
        for cap in (lo, hi):                       # σ whisker ends
            p.drawLine(int(cap), int(y - 11), int(cap), int(y + 11))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C.accent)))
        p.drawEllipse(QRectF(mu - 6, y - 6, 12, 12))   # the mean, >= 8 px

        stat, cap_line = stats[i]
        _text(p, plot_r + theme.SPACE_XL, y - 8, stat, f_val, C.text)
        _text(p, plot_r + theme.SPACE_XL, y + 12, cap_line, f_cap, C.text_dim)

    # --- axis
    p.setPen(QPen(QColor(C.border), theme.BORDER_PX))
    p.drawLine(int(plot_l), int(bottom), int(plot_r), int(bottom))
    for tick in (-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3):
        label = "0" if tick == 0 else f"{tick:+.1f}s"
        _text(p, px(tick) - 40, bottom + 20, label, f_cap,
              C.text if tick == 0 else C.text_dim, Qt.AlignHCenter | Qt.AlignVCenter, 80)
    _text(p, (plot_l + plot_r) / 2 - 200, bottom + 42, ACCURACY_AXIS, f_cap, C.text_muted,
          Qt.AlignHCenter | Qt.AlignVCenter, 400)

    # --- the claim, in a card with an accent rule (the app's own note idiom)
    path = QPainterPath()
    path.addRoundedRect(card, theme.RADIUS_M, theme.RADIUS_M)
    p.fillPath(path, QColor(C.surface))
    p.fillRect(QRectF(card.x(), card.y(), theme.SPACE_XXS, card.height()), QColor(C.accent))
    p.setFont(f_cap)
    p.setPen(QPen(QColor(C.text_dim)))
    p.drawText(QRectF(card.x() + theme.SPACE_L, card.y(), inner_w, card.height()), flags, claim)

    print(f"media_capture: accuracy chart {W}x{H} logical · gutters L{gutter_l:.0f} R{gutter_r:.0f} "
          f"· caption wraps to {text_h} px in {inner_w:.0f} px")
    p.end()
    return save(img, os.path.join(out_dir, "accuracy.png"))


# ====================================================================== 7 — the social card
def build_og(out_dir: str, hero_png: str) -> str:
    """The Open Graph / Twitter card: the wordmark, one sentence, and the freshly-captured hero
    bleeding off the right edge. Composed FROM hero.png so it can never again embed a stale
    screenshot — the failure mode of the July card, which carried the flat zero-Δ line for weeks
    after the bug was fixed."""
    from studio.dev import make_icon

    W, H = OG_SIZE
    img = _canvas(W, H)
    p = _painter(img)
    p.scale(DPR, DPR)

    split = int(W * 0.52)
    # The screenshot bleeds off the right edge, scaled to the card's FULL HEIGHT and anchored to
    # its left — so what shows is the whole window top-to-bottom and the first ~60 % of its width
    # (video, map, the top of the charts, the lap table), which reads as an application. Scaling it
    # by an arbitrary factor instead, as the first cut did, filled the column with one enlarged
    # panel: a photograph of a kart, with nothing in frame saying it came from a piece of software.
    # A uniform canvas veil sits over it so the column never competes with the wordmark.
    hero = QPixmap(hero_png)
    if not hero.isNull():
        col_w, col_h = W - split, H
        scaled = hero.scaledToHeight(col_h * DPR, Qt.SmoothTransformation)
        p.save()
        p.setClipRect(QRect(split, 0, col_w, col_h))
        p.drawPixmap(QRectF(split, 0, scaled.width() / DPR, col_h), scaled,
                     QRectF(0, 0, scaled.width(), scaled.height()))
        p.fillRect(QRect(split, 0, col_w, col_h), QColor(21, 24, 30, 70))
        p.restore()
    p.setPen(QPen(QColor(C.border), theme.BORDER_PX))
    p.drawLine(split, 0, split, H)

    mark = make_icon.render(256)
    p.drawPixmap(QRect(theme.SPACE_3XL, theme.SPACE_3XL, 96, 96), QPixmap.fromImage(mark))

    x = theme.SPACE_3XL
    _text(p, x, 214, APP_NAME, theme.ui_font(58, theme.W_SEMIBOLD), C.text)
    _text(p, x, 268, OG_TAGLINE, theme.ui_font(24, theme.W_SEMIBOLD), C.accent)
    for i, line in enumerate(OG_BODY.split("\n")):
        _text(p, x, 320 + i * 26, line, theme.ui_font(15), C.text_dim)

    cx = x
    for chip in OG_CHIPS:
        f = theme.ui_font(theme.BODY, theme.W_SEMIBOLD)
        wchip = QFontMetrics(f).horizontalAdvance(chip) + 2 * theme.SPACE_L
        box = QRectF(cx, H - 108, wchip, 34)
        path = QPainterPath()
        path.addRoundedRect(box, theme.pill_radius(int(box.height())), theme.pill_radius(int(box.height())))
        p.fillPath(path, QColor(C.surface))
        _text(p, box.x(), box.center().y(), chip, f, C.text,
              Qt.AlignHCenter | Qt.AlignVCenter, box.width())
        cx += wchip + theme.SPACE_S
    p.end()
    return save(img, os.path.join(out_dir, "og.png"))


# ====================================================================== driver
SHOTS = ("hero", "ideal", "trust", "map", "overlay", "accuracy", "og")


def capture(recording: str, out_dir: str, only: set[str], work_dir: str,
            with_video: bool = True) -> None:
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    app = boot_app()

    # Never touch the user's real app-support state — divert EVERY seam to a throwaway dir BEFORE
    # any window is built (the load-time upsert reads one; StudioWindow.__init__ reads five more),
    # like `_smoke.py` and `ui_capture.py`. An upstream jail (the QA write-jail harness) is adopted
    # rather than replaced; see studio/dev/_jail.py.
    #
    # These shots are the ones that get PUBLISHED — docs/media, the landing page, the OG card — so
    # "the shipped defaults" is the only correct baseline for them. There is deliberately no
    # --prefs escape hatch here, unlike ui_capture: a published asset in someone's personal units
    # is a bug, not a variant.
    _jail.divert_app_support("pacer-media-")

    needs_app = only & {"hero", "ideal", "trust", "map", "overlay"}
    hero_png = os.path.join(out_dir, "hero.png")
    if needs_app:
        w = open_window(app, recording, WINDOW)
        print(f"media_capture: {w.session.lap_count()} laps, "
              f"{len(w.session.valid_lap_ids())} clean, best lap {w.session.best_lap_id()}")
        _settle_ui(app, w)
        if "ideal" in only:
            shot_ideal(app, w, out_dir)
        if "trust" in only:
            shot_data_trust(app, w, out_dir)
        if "map" in only:
            shot_map(app, w, out_dir)
        if "hero" in only:
            hero_png = shot_hero(app, w, out_dir, work_dir, with_video=with_video)
        if "overlay" in only:
            shot_overlay(w, out_dir, work_dir)
    if "accuracy" in only:
        draw_accuracy(out_dir)
    if "og" in only:
        build_og(out_dir, hero_png)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("recording", help="INPUT recording (chapter 1; siblings auto-discovered)")
    ap.add_argument("--out", default=os.path.join("docs", "media"),
                    help="OUTPUT directory for the PNGs (default: docs/media)")
    ap.add_argument("--work", default=None,
                    help="scratch dir for the exported MP4 (default: a temp dir)")
    ap.add_argument("--only", default=",".join(SHOTS),
                    help=f"comma-separated subset of {','.join(SHOTS)}")
    ap.add_argument("--no-video-frame", action="store_true",
                    help="skip the hero's real-footage composite (leaves the pane as rendered)")
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    unknown = only - set(SHOTS)
    if unknown:
        raise SystemExit(f"unknown shot(s): {sorted(unknown)}; choose from {SHOTS}")
    work = args.work or tempfile.mkdtemp(prefix="pacer-media-work-")
    capture(args.recording, args.out, only, work, with_video=not args.no_video_frame)
    print(f"media capture OK — {args.out}")


if __name__ == "__main__":
    main(sys.argv[1:])
