"""GMeterOverlay: a "G meter" dial painted over the video (felt-force convention).

Native-window trick: on macOS a QVideoWidget renders through a native surface the window-server
composites independently of Qt's z-order, so a plain child overlay is hidden behind the video.
This overlay is its own frameless top-level window, composited as a separate layer above the
video surface; the VideoView keeps it pinned to the video's corner.

Felt-force axes (the pointer is the inertial reaction the body feels, not the accel vector;
g_at_time's accel convention is +lateral=left, +long=accelerating):
  * braking      -> pointer UP
  * accelerating -> pointer DOWN
  * turning right -> pointer LEFT
  * turning left  -> pointer RIGHT
Screen mapping: dx = +lateral*scale, dy = +longitudinal*scale.

WHAT THE FACE CARRIES, and why it is this short. The dial used to be ELEVEN text items inside a
120x140 px card — a title, four cardinal peak numbers, four direction words, two labelled rings
and a sensor-provenance tag — set in three grey tiers between 6 and 8 px. Rendered and measured
(critical review 2026-09-07 §6.5) the side numbers ran into the card edge, the moving dot sat on
top of the right-hand one, and the ring captions were drawn centred ON the ring strokes they
named. It read as a debug widget: a legend with an instrument behind it.

It is now the shape every top-tier overlay converges on — a DOT WITH A TRAIL inside TWO RINGS,
and ONE number:

  * two rings: the outer boundary at `_FULL_SCALE_G` and one reference ring at `_SCALE_RING_G`;
  * one scale caption ("1.0") annotating the reference ring, sitting at the radial MIDPOINT of
    the annulus between the two strokes so it lands on neither (`_scale_caption`);
  * the grip envelope (the convex hull of the lap's filtered felt points) — a SHAPE, not text;
  * the dot, and the ~3 s trail behind it, which is what makes the direction of travel legible
    without four words saying it;
  * one readout: the current |g|, in the band under the dial.

The two facts the deleted words carried did not evaporate, they MOVED. The felt-force convention
and the axis provenance ("IMU lat · GPS long") are one sentence on the g-meter toggle's tooltip
(`source_sentence`, rendered by `VideoView.set_gmeter_source`) — a hover on the control that
turns the dial on, rather than nine pixels of sensor plumbing burned into a shared clip. That is
also why `DialState` no longer carries `source`: the dial never paints it, so it does not hold it.

NO SIGNAL, NO INSTRUMENT. `Session.g_at_time` returns None only when the recording produced no g
series at all, so a dial that has never been handed a sample (`DialState.seen` False) is a dial
with nothing to measure. The export then paints NOTHING — a clip off a recording with no
accelerometer gets no dial, instead of the rings, legend and four `0.0` it used to burn in — and
the live dial paints its resting template with an em-dash readout. On screen the toggle is
already disabled with "No accelerometer data in this recording" (see central_view), so the
explanation has a home and the dial does not have to fake one.

Chin-mount shake: the dot is an EMA of the felt-force g; the envelope + cardinal peaks use a
high-percentile (robust) peak so a single shake spike can't blow them out.

Envelope = a convex hull of accumulated filtered felt points (grip used this scope). The four
cardinal peaks are no longer PAINTED individually, but they are still computed and still in the
snapshot: they clamp the hull candidate (the anti-spike gate), the exporter freezes and blanks
them at the lap boundaries, and tests/test_export_padding.py reads them. Scope defaults to the
current lap and resets at the lap boundary (`_RESET_ON_LAP` / `reset_envelope()`).

`pacer`-free: the app feeds set_g + set_lap at the ~30 Hz tick. The convention flip + filtering
are display concerns and live here; the validated g values in gmeter.py are untouched.

THREE OBJECTS, and the split is load-bearing rather than tidiness. `DialFilter` is the
bookkeeping (EMA'd dot, trail, per-lap hull, robust peaks) with no Qt base class; `DialState` is
an immutable snapshot of it; `paint_dial` is a free function that draws a snapshot into any
QPainter. `GMeterOverlay` is then only the WINDOW. The offline video exporter runs on a worker
QThread and needs the first three but must never touch the fourth — a QWidget created off the
GUI thread is the SIGSEGV shape this repo has post-mortemed twice (see
tests/test_export_thread_safety.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import theme
from .export_palette import EXPORT
from .theme import C

_c = theme.qcolor  # QColor from a theme hex token (+ optional alpha) — shared home in theme.py

# THE TWO RINGS. The outer boundary is full scale — a 1.0 g corner sits well inside it and a
# ~1.5 g spike still lands within the face. The inner one is the reference the eye measures
# against, and the only ring the face names (`_scale_caption`).
_FULL_SCALE_G = 1.6
_SCALE_RING_G = 1.0

# THE FACE'S BOX, in fractions of it rather than in px, because the same numbers now lay out the
# 120x140 live card AND the export dial's square from 720p to 4K (`dial_geom` is shared).
_MARGIN_FRAC = 0.06              # ring inset from the box edge (floored at theme.SPACE_S so the
                                 # 120 px minimum keeps a hairline's worth of air)
_READOUT_BAND_FRAC = 0.15        # bottom strip reserved for the ONE readout (|g|). The dial's
                                 # height is paid out of the rest, so nothing can ever land on it.
_CAPTION_CLEAR_PX = 1.6          # air the scale caption keeps from each ring stroke (the strokes
                                 # are 1.0-1.2 px wide, so this clears their antialiasing too).
                                 # There is no size floor: `_scale_caption` drops the caption when
                                 # the annulus cannot hold it, which is the same rule at every size.

# TYPE, as a fraction of the dial radius so both modes and every output size scale together.
# The floors are what the review's "~6 px ring labels" finding cost: nothing on the face is set
# below 8 px any more, and the readout — the loudest thing on it — starts at 12.
_READOUT_PX_MIN, _READOUT_PX_FRAC = 12.0, 0.22
_CAPTION_PX_MIN, _CAPTION_PX_FRAC = 8.0, 0.10

_NO_VALUE = "—"                  # the app's no-value mark; the readout with no g signal at all

_DOT_EMA_ALPHA = 0.30            # dot low-pass per 30Hz sample (~0.1s tc); tames chin-mount shake

# THE TRAIL. The dot's own recent path, which is what tells you it is sweeping from brake to apex
# without four words saying so. Drawn from the RAW filtered points, not the peak-clamped hull
# ones, so the trail's head is the dot itself and not a point beside it (`dial_to_screen` clamps
# an excursion to the rim, which is a self-limiting way to show a real one).
# It is painted in the DOT's colour (the off-white text token / EXPORT.text), not the accent: the
# grip envelope under it is amber, and rendered on real D24 an amber trail disappeared into the
# amber wash exactly where it matters — inside the envelope. Dot and trail read as one object.
#
# It is DECIMATED, and that was found by rendering it. Storing every 30 Hz tick put ~36 vertices
# inside the couple of dial-millimetres the dot travels in a second, and the EMA's residual jitter
# then drew a white scribble around the dot instead of a path — the signal is honest, the picture
# was not. Committing one vertex every `_TRAIL_EVERY` ticks and keeping the newest sample glued to
# the head (see `_accumulate`) spans ~2.9 s of driving in vertices far enough apart to read as
# brake -> turn-in -> apex.
_TRAIL_PTS = 30                  # committed vertices
_TRAIL_EVERY = 3                 # ticks per committed vertex (~2.9 s of history at 30 Hz)
_TRAIL_WIDTH_FRAC = 0.028        # newest segment's width as a fraction of the dial radius
_TRAIL_TAPER = 0.30              # oldest segment's width, as a fraction of the newest

# The dot, in fractions of the dial radius, with the old fixed pixel sizes as floors: at the 120 px
# minimum it is exactly the dot it always was, and it no longer stays a 2.6 px speck on a dial four
# times that size.
_DOT_CORE_FRAC, _DOT_CORE_MIN = 0.030, 2.6
_DOT_GLOW_FRAC, _DOT_GLOW_MIN = 0.075, 7.0

_PEAK_PERCENTILE = 90.0          # cardinal peak = this percentile of recent felt-g (robust)
_PEAK_WINDOW = 90                # samples (~3 s at 30 Hz) feeding the percentile peak
_ENVELOPE_MAX_PTS = 240          # cap on hull input points per scope (ring buffer)
_RESET_ON_LAP = True             # reset the envelope + peaks at each lap boundary


def _font(px: float, bold: bool = False) -> QFont:
    """The dial's one face, at an explicit PIXEL size and with TABULAR FIGURES.

    IT NEVER JOINED THE #196/#197 FIX either. This was a bare ``QFont()`` with ``setPointSizeF``,
    which is two defects on a dial whose every box is measured in pixels:

      * PROPORTIONAL figures. Measured on the shipped Inter build, the sizes the dial asks for
        carried 8 or 9 distinct digit advances (2.84..4.52 px at the caption size, 4.47..7.09 at
        the number size), so the numbers a driver reads off the face were drawn in a face that
        gives ``1`` and ``8`` different widths. It matters more now, not less: the four cardinal
        peaks that first motivated this have been replaced by ONE readout, and a hero number that
        rewrites its own width thirty times a second is the whole reason tabular figures exist.
      * DPI-DERIVED sizes. A point size is resolved against the SCREEN's logical DPI, but every
        box ``dial_geom`` hands out — the readout band, the margin, the ring radii — is pixels.
        Measured: on a 96 dpi logical screen ``_font(8.0)`` came out at 11 px with a line
        height of 13.3 — inside a 12 px box — while the same call on a 72 dpi screen gives 8 px.
        The EXPORT dial makes that worse still: its canvas is fixed pixels and has no screen at
        all, so the burned-in glyph sizes depended on whatever display the render happened to run
        on. The numeric constants are unchanged; they are simply read as the pixels the boxes
        around them were always budgeted in.

    Routed through ``theme.mono_font`` rather than re-deriving the tag, so the dial follows the
    app's own fallback ORDER (Inter+tnum -> Inter -> the mono stack only when Inter is absent);
    ``setPixelSize`` must FOLLOW that call, because ``mono_font`` returns a point-sized font."""
    size = max(1, int(round(px)))
    f = theme.mono_font(size)
    f.setPixelSize(size)
    f.setBold(bold)
    return f


# Human names for the sensor behind each dial axis. The IMU (GoPro accelerometer) is trusted for
# LATERAL g (r~0.9); the LONGITUDINAL axis reads the GPS speed-derivative because the IMU forward
# axis is vibration-inflated (r~0.36). So the tag must name the axis provenance, not one source.
_SRC_NAME = {"accl": "IMU", "gps": "GPS"}


def source_label(lat_source: str, long_source: str | None = None) -> str:
    """The dial's axis provenance, as a label. Same source both axes -> that name ("IMU" / "GPS");
    mixed (the usual IMU-lateral + GPS-longitudinal meter) -> "IMU lat · GPS long" so the
    braking/accel axis the driver reads is labelled by where it actually comes from — not a bare
    "ACCL" that misattributes the GPS-derived longitudinal to the IMU.

    This used to be painted on the dial's face at 6.5 px. It is now a clause of `source_sentence`,
    on the toggle that turns the dial on."""
    lat = _SRC_NAME.get(lat_source, lat_source.upper())
    if long_source is None or long_source == lat_source:
        return lat
    lon = _SRC_NAME.get(long_source, long_source.upper())
    return f"{lat} lat · {lon} long"


# What the dial's face no longer says, said once, where it can be said in words: on the tooltip of
# the control that turns the dial on. Two clauses, and neither is decoration —
#   * the CONVENTION, because the dot shows the force the driver FEELS (a right-hand corner throws
#     the dot LEFT), which four 6 px words used to whisper on the face;
#   * the PROVENANCE, because the meter mixes sensors: lateral is the IMU (r ~ +0.89 against GPS)
#     and braking/accel is the GPS speed-derivative, the IMU forward axis being vibration-inflated
#     (r ~ +0.36). See studio/docs/gmeter-validation.md.
_CONVENTION_SENTENCE = ("The dot is the force you feel: braking pushes it up, accelerating down, "
                        "and a right-hand corner throws it left.")
_PROVENANCE = {
    "IMU lat · GPS long": "Cornering g comes from the IMU; braking and acceleration from the GPS "
                          "speed derivative, which the vibration-inflated IMU forward axis is not "
                          "trustworthy enough to carry.",
    "IMU": "Both axes come from the IMU.",
    "GPS": "No usable accelerometer, so both axes are derived from the GPS trajectory.",
}


def source_sentence(lat_source: str, long_source: str | None = None) -> str:
    """The g-meter toggle's tooltip body: the felt-force convention, then where the numbers come
    from. The dial's face carries neither any more (see the module docstring) — this is where they
    went, and it is the only place the app states the dial's provenance now."""
    label = source_label(lat_source, long_source)
    tail = _PROVENANCE.get(label)
    if tail is None:                      # an unrecognised pairing still names itself honestly
        tail = f"Axis sources: {label}."
    return f"{_CONVENTION_SENTENCE}\n{tail}"


@dataclass
class DialState:
    """Pure draw state for paint_dial; snapshotted by both the live widget and the offline
    exporter so the burned dial matches the screen.

    `seen` is the one field that is about the RECORDING rather than the instant: False means this
    dial has never been handed a g sample. `Session.g_at_time` returns None only for a recording
    with no g series at all (`GMeter.at_time` clamps inside the series and never returns None
    otherwise), so `not seen` is exactly "there is nothing to meter here" — which is why
    `paint_dial` may act on it. See the module docstring.

    The four `peak_*` are no longer painted individually; they are still the anti-spike clamp on
    the hull, still what the exporter freezes at the finish line, and still read by
    tests/test_export_padding.py."""
    fx: float = 0.0
    fy: float = 0.0
    have: bool = False
    seen: bool = False
    trail: list[tuple[float, float]] = field(default_factory=list)
    hull_pts: list[tuple[float, float]] = field(default_factory=list)
    peak_fwd: float = 0.0
    peak_back: float = 0.0
    peak_left: float = 0.0
    peak_right: float = 0.0


class DialFilter:
    """The dial's BOOKKEEPING — the EMA'd dot, the per-lap hull points, the robust cardinal peaks,
    the lap scope and the provenance label — with no QWidget anywhere in it.

    Why it is its own object. Two callers drive this exact same filtering: the live
    `GMeterOverlay` at the ~30 Hz UI tick, and `export_video.OverlayPainter`, once per rendered
    frame. The exporter used to get it by CONSTRUCTING A GMeterOverlay — i.e. a frameless
    translucent top-level QWidget — inside `Renderer.__init__`, which `VideoExportWorker.run()`
    runs on a QThread. Creating (and destroying) a top-level widget off the GUI thread is
    undefined behaviour in Qt and is the shape this repo has post-mortemed as a SIGSEGV twice
    (see tests/test_compare_lifecycle.py); it survived only because the export dial is never
    `show()`n, so nothing ever asked the window system for a backing store. The render path never
    needed the widget at all — it only calls the free function `paint_dial` with a `DialState`
    snapshot — so the state it DID need lives here and the widget became a thin shell over it.

    Every method keeps the widget's exact semantics, including the ones that read as quirks and
    are load-bearing (`set_lap` does NOT reset on the first lap it is ever handed; a None lap is
    held). The mutators return True when the painted dial changed, which is precisely where
    `GMeterOverlay` used to call `self.update()` — so the repaint pattern is unchanged too.

    `version` counts every change to the STATIC dial layer (the grip envelope); the widget keys
    its cached static pixmap on it. The trail, the dot and the |g| readout all move every tick and
    are therefore NOT in that layer — see `_paint_dial_moving`."""

    def __init__(self) -> None:
        # Filtered felt-force pointer in g; axes: +x = thrown right, +y(down) = thrown back (accel),
        # -y(up) = thrown forward (brake). Peaks are the robust per-direction max felt-g (all >= 0).
        self.fx = 0.0
        self.fy = 0.0
        self.have = False
        self.ema_init = False
        # Has this dial EVER been handed a g sample? False after any number of ticks means the
        # recording has no g series at all (see DialState.seen) — the no-instrument state.
        self.seen = False
        self.trail: list[tuple[float, float]] = []        # recent filtered points (ring buffer)
        self._trail_tick = 0                              # decimation phase (see _accumulate)
        self.hull_pts: list[tuple[float, float]] = []     # filtered felt points (ring buffer)
        self.recent: list[tuple[float, float]] = []       # rolling window for percentile peaks
        self.peak_fwd = 0.0
        self.peak_back = 0.0
        self.peak_left = 0.0
        self.peak_right = 0.0
        self.lap: int | None = None
        self.version = 0

    # ------------------------------------------------------------------ data in
    def set_g(self, g: tuple[float, float, float] | None) -> bool:
        """Push the current kart-frame (lateral_g, longitudinal_g, total_g). None blanks the live
        dot (keeps the template + the accumulated envelope). Applies the felt-force convention and
        the shake low-pass, and grows the envelope + robust cardinal peaks. Returns True when the
        painted dial changed."""
        if g is None:
            if not self.have:
                return False
            self.have = False
            return True
        lat, lon, _total = g
        # Felt-force convention (see module doc): felt x = +lateral, felt y = +longitudinal.
        fx, fy = lat, lon
        # Shake low-pass (EMA) so the dot tracks vehicle g, not head/mount jitter.
        if not self.ema_init:
            self.fx, self.fy, self.ema_init = fx, fy, True
        else:
            a = _DOT_EMA_ALPHA
            self.fx += a * (fx - self.fx)
            self.fy += a * (fy - self.fy)
        self.have = True
        self.seen = True            # a real sample: this recording has something to meter
        self._accumulate(self.fx, self.fy)
        return True

    def _accumulate(self, fx: float, fy: float) -> None:
        """Grow per-lap envelope + robust cardinal peaks from the filtered felt point. Peaks use a
        percentile of the recent window and the hull point is clamped to them, so a lone shake spike
        can't balloon either."""
        # The trail takes the RAW filtered point (the dot's own position), so its head IS the dot.
        # It lives in the per-frame layer, so it costs the static cache nothing.
        #
        # Decimated (see `_TRAIL_EVERY`) by OVERWRITING the head until a vertex is due: the last
        # element is therefore always the live point — the stroke stays welded to the dot — while
        # only every third one is kept, which is what makes the path read as a path.
        self._trail_tick = (self._trail_tick + 1) % _TRAIL_EVERY
        if self.trail and self._trail_tick:
            self.trail[-1] = (fx, fy)
        else:
            self.trail.append((fx, fy))
            if len(self.trail) > _TRAIL_PTS:
                self.trail.pop(0)
        self.recent.append((fx, fy))
        if len(self.recent) > _PEAK_WINDOW:
            self.recent.pop(0)
        # Robust peak per cardinal: percentile of the rolling window so a single shake sample can't win.
        right, left, back, fwd = [], [], [], []
        for px, py in self.recent:
            if px > 0:
                right.append(px)
            elif px < 0:
                left.append(-px)
            if py > 0:
                back.append(py)     # accelerating (felt down)
            elif py < 0:
                fwd.append(-py)     # braking (felt up)
        self.peak_right = max(self.peak_right, _pct(right, _PEAK_PERCENTILE))
        self.peak_left = max(self.peak_left, _pct(left, _PEAK_PERCENTILE))
        self.peak_back = max(self.peak_back, _pct(back, _PEAK_PERCENTILE))
        self.peak_fwd = max(self.peak_fwd, _pct(fwd, _PEAK_PERCENTILE))
        # Clamp the hull candidate to the robust per-direction peaks so one spike can't balloon the blob.
        hx = min(fx, self.peak_right) if fx >= 0 else max(fx, -self.peak_left)
        hy = min(fy, self.peak_back) if fy >= 0 else max(fy, -self.peak_fwd)
        self.hull_pts.append((hx, hy))
        if len(self.hull_pts) > _ENVELOPE_MAX_PTS:
            self.hull_pts.pop(0)
        # A new felt sample changed the hull points, which live in the widget's cached static
        # layer, so invalidate it. Bump unconditionally: cheap, and the clamped hull candidate can
        # move on any sample. (Once the envelope is full the hull_pts *content* still shifts, so a
        # length-only key would go stale here — the version counter is the safe choice.)
        self.version += 1

    def set_lap(self, lap_id: int | None) -> bool:
        """Set the current lap. A change to a new valid lap resets the envelope + peaks (when
        _RESET_ON_LAP); None (lead-in / between laps) is held so the envelope persists. Returns
        True when that reset actually happened (the only case that repaints)."""
        if lap_id is None or lap_id == self.lap:
            return False
        did_reset = False
        if _RESET_ON_LAP and self.lap is not None:
            self.reset_envelope()
            did_reset = True
        self.lap = lap_id
        return did_reset

    def reset_envelope(self) -> None:
        """Clear the envelope + cardinal peaks + the trail and re-seed the dot EMA so the pointer
        starts fresh on the new scope's first sample (no carry-over from the previous lap).

        The trail is cleared for the same reason the EMA is re-seeded: leaving it would draw a
        stroke from the old lap's last corner, through the re-seeded origin, to the new lap's first
        sample — a path the kart never took. `seen` is NOT cleared: it is a fact about the
        recording, not about the scope."""
        self.trail.clear()
        self._trail_tick = 0
        self.hull_pts.clear()
        self.recent.clear()
        self.peak_fwd = self.peak_back = self.peak_left = self.peak_right = 0.0
        # Re-seed the dot EMA: the next set_g seeds fx/fy from its own value (no carry-over).
        self.ema_init = False
        self.fx = self.fy = 0.0
        self.version += 1   # envelope cleared → the cached static layer is stale

    # ------------------------------------------------------------------ data out
    def snapshot(self) -> DialState:
        """Snapshot the filtering state into a pure DialState for paint_dial (the same snapshot the
        live widget and the exporter both render from, so the burned dial matches the screen)."""
        return DialState(
            fx=self.fx, fy=self.fy, have=self.have, seen=self.seen,
            trail=list(self.trail), hull_pts=list(self.hull_pts),
            peak_fwd=self.peak_fwd, peak_back=self.peak_back,
            peak_left=self.peak_left, peak_right=self.peak_right)


def dial_geom(w: float, h: float):
    """Centre + radius of the dial inside a (w, h) box, and the ONE geometry both modes use.

    The face is the box minus a bottom band for the |g| readout, inset by a margin. Nothing is
    painted outside the outer ring any more, so the margin only has to clear the box edge and the
    export's haloed stroke — where the old live geometry spent 18 px of every side on cardinal
    numbers and 18+13 px of height on a title strip and a tag band. Measured, at the 120x140
    minimum the dial radius goes 36.5 -> 51.5 px (+41 %, and the face nearly doubles in area);
    the ring no longer has numbers running off the card edge because there are none.

    IT IS SHARED WITH THE EXPORT NOW, and that is the point rather than a saving. `paint_dial`
    exists so the burned dial IS the on-screen dial; the export used to need its own
    `_export_dial_geom` purely to reserve room for the big outlined cardinal numbers it painted
    and the strips it didn't. With one composition there is one layout, at every output size."""
    band = max(float(theme.SPACE_L), _READOUT_BAND_FRAC * h)
    margin = max(float(theme.SPACE_S), _MARGIN_FRAC * min(w, h))
    dial_h = h - band
    r = max((min(w, dial_h) - 2 * margin) / 2.0, 8.0)
    return w / 2.0, dial_h / 2.0, r


def readout_rect(w: float, h: float) -> QRectF:
    """The band under the dial that holds the single |g| readout — the only text outside the outer
    ring, and the reason `dial_geom` pays for a band at all."""
    band = max(float(theme.SPACE_L), _READOUT_BAND_FRAC * h)
    return QRectF(0.0, h - band, w, band)


def dial_to_screen(cx, cy, r, fx, fy):
    """Map a felt-force point (felt-x, felt-y in g) to a dial pixel. +felt-x -> RIGHT, +felt-y ->
    DOWN (accelerating); braking (felt-y<0) -> UP. Clamped to the dial circle so a big value stays
    on the rim with its direction preserved. Shared by the live widget + the offline renderer."""
    scale = r / _FULL_SCALE_G
    dx = fx * scale
    dy = fy * scale
    d = math.hypot(dx, dy)
    if d > r:
        dx, dy = dx / d * r, dy / d * r
    return cx + dx, cy + dy


def _readout_px(r: float) -> float:
    """Type size (px) of the |g| readout — the loudest thing on the face, and now the only number
    on it. Proportional to the dial so a 120 px card and a 4K burn are the same design."""
    return max(_READOUT_PX_MIN, r * _READOUT_PX_FRAC)


def _caption_px(r: float) -> float:
    """Type size (px) of the scale caption. Floored well above the ~6 px the old ring labels
    reached, and kept clearly subordinate to the readout so the two never read as one pair."""
    return max(_CAPTION_PX_MIN, r * _CAPTION_PX_FRAC)


def readout_text(st: DialState) -> str:
    """The dial's one number: the magnitude of the felt-force pointer, in g.

    It is the RADIUS OF THE DOT — hypot of the same filtered pair the dot is drawn at — not
    `g_at_time`'s unfiltered total, so the number and the dot can never disagree on screen. One
    decimal: the EMA has a ~0.1 s time constant, and a second decimal would churn every frame at
    30 Hz without carrying a fact anyone can read. With no g signal at all, the app's no-value
    mark rather than a fabricated `0.0`."""
    if not st.seen:
        return _NO_VALUE
    return f"{math.hypot(st.fx, st.fy):.1f} g"


def _scale_caption(cx: float, cy: float, r: float, fm: QFontMetricsF):
    """(rect, text) for the ONE caption that gives the face its scale, or None when the gap between
    the two rings is too narrow to hold it.

    THE DEFECT THIS FIXES (review §6.5): the ring labels were centred exactly on the ring's own
    up-right 45-degree point — drawn ON the stroke they named, a caption with a line through it.
    It now sits at the RADIAL MIDPOINT of the annulus between the two rings, on the diagonal, which
    is the placement with the most clearance from both by construction and needs no tuning as the
    dial resizes. The diagonal is chosen because it is the one direction with no crosshair, no
    readout and no resting dot.

    IT IS THE BARE VALUE, and that was forced by measurement rather than taste. Rendered and
    measured at four sizes, "1.0 g" does not fit this annulus at the 120x140 minimum at ANY radial
    offset: its ink spans 18 px of a 19.3 px gap, so it lands on one stroke or the other whatever
    you do. The bare "1.0" spans 12 px and clears both by 2.1 px. The unit is not lost — the
    readout directly below it reads "1.1 g", in the largest type on the face — so the caption reads
    as what it is, a tick label on a ring.

    The DROP RULE keeps that honest on a machine whose face is wider than the shipped Inter: the
    caption's radial half-extent (modelled from its own metrics, with 15 % of headroom over the
    measured ink) plus the strokes' clearance must fit half the annulus, or nothing is drawn. A
    missing tick label is a smaller failure than one with a ring through it."""
    text = f"{_SCALE_RING_G:.1f}"
    fh = fm.height()
    tw = fm.horizontalAdvance(text)
    inner = r * (_SCALE_RING_G / _FULL_SCALE_G)
    half_gap = (r - inner) / 2.0
    # Measured: the ink's radial half-extent tracks (tw + fh) / 4 to within ~10 % at every size.
    if (tw + fh) / 4.0 * 1.15 + _CAPTION_CLEAR_PX > half_gap:
        return None
    d = (inner + half_gap) * 0.70710678        # the annulus midpoint, on the 45-degree diagonal
    return QRectF(cx + d - tw / 2.0, cy - d - fh / 2.0, tw, fh), text


def _trail_segments(cx: float, cy: float, r: float, trail):
    """The dot's recent path as [(QPointF a, QPointF b, recency), ...], oldest first, with recency
    running 0 -> 1 so the painter can taper width and fade alpha along it. Empty for fewer than
    two points (nothing to draw a stroke between)."""
    if len(trail) < 2:
        return []
    pts = [QPointF(*dial_to_screen(cx, cy, r, fx, fy)) for (fx, fy) in trail]
    n = len(pts) - 1
    return [(pts[i], pts[i + 1], (i + 1) / n) for i in range(n)]


# --------------------------------------------------------------------------- export palette
# The export-render palette (vivid/opaque for burning over bright footage; the live dial uses C.*
# tokens) is now `export_palette.EXPORT`, imported at the top. These five names were a second,
# hand-copied definition of colours `export_video.EXPORT` also declares — this module cannot import
# export_video (export_video imports THIS one), which is exactly why the palette moved out to a
# module they can both read. Aliased locally so the paint bodies below read unchanged.
_EX_TEXT = EXPORT.text
_EX_HALO = EXPORT.halo        # dark outline/shadow under every bright element
_EX_ACCENT = EXPORT.accent    # envelope amber (brighter + saturated vs C.accent)
_EX_ACCENT_HI = EXPORT.accent_bright   # dot glow highlight
_EX_GRID = EXPORT.grid        # rings / crosshair (white at moderate alpha)


def _draw_text_outlined(p: QPainter, rect: QRectF, flags, text: str, font: QFont,
                        colour: str, halo: float = 2.2) -> None:
    """Draw aligned `text` with a dark outline under a bright fill so a burned label reads over
    sky and tarmac."""
    fm = QFontMetricsF(font)
    w = fm.horizontalAdvance(text)
    if flags & Qt.AlignHCenter:
        x = rect.x() + (rect.width() - w) / 2.0
    elif flags & Qt.AlignRight:
        x = rect.right() - w
    else:
        x = rect.x()
    if flags & Qt.AlignVCenter:
        y = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2.0
    else:
        y = rect.y() + fm.ascent()
    path = QPainterPath()
    path.addText(QPointF(x, y), font, text)
    p.save()
    pen = QPen(_c(_EX_HALO, 235), halo * 2.0)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(_c(colour))
    p.drawPath(path)
    p.restore()


def paint_dial(p: QPainter, w: float, h: float, st: DialState,
               export: bool = False, scale_k: float = 1.0) -> None:
    """Paint the dial — two rings, one scale caption, the grip envelope, the dot and its trail, and
    the single |g| readout — sized to (w,h) at the origin. Single source for the live widget + the
    offline exporter; no widget state touched.

    BOTH MODES NOW PAINT THE SAME STRINGS. The old face had two live-only ones (a "G METER" title
    the export dropped for room, and the provenance tag the export deliberately did not burn into
    someone's clip), and tests/test_gmeter_overlay.py existed to hold that difference at exactly
    two. There is no difference left to hold: the title is gone from both and the provenance is on
    the toggle's tooltip, so the burn and the screen say the same two things — the readout and the
    scale caption. The test now pins set equality, which is a stronger contract than the old one.

    export=False = on-screen look; export=True = the burn-over-bright variant (no backdrop box,
    white haloed rings, brighter envelope and trail, a bigger glowing dot, an outlined readout).
    `scale_k` scales export strokes/glyphs to the output height (1.0 ≈ a ~280 px dial at 1080p);
    the LAYOUT is `dial_geom` in both modes and does not need it.

    The live split into a cached static layer + a moving layer is a rendering-cost optimisation,
    not a visual change: painting both here in one pass is byte-identical to blitting the cache
    and drawing the moving layer over it (pinned by test_cached_paint_is_pixel_identical...)."""
    p.setRenderHint(QPainter.Antialiasing, True)
    if export:
        _paint_dial_export(p, w, h, st, scale_k)
        return
    _paint_dial_static(p, w, h, st)
    _paint_dial_moving(p, w, h, st)


def _paint_dial_static(p: QPainter, w: float, h: float, st: DialState) -> None:
    """The SLOW-CHANGING live layer: backdrop box, the two rings, the crosshair, the scale caption
    and the grip envelope. Identical frame-to-frame at a given size while the envelope is
    unchanged, so the live widget renders it once into a cached pixmap (keyed by size + palette +
    envelope-version) and re-blits it every tick.

    THE READOUT IS DELIBERATELY NOT HERE. It is a function of the dot's position, which moves on
    every tick including ones that do not bump `version` (a `set_g(None)` blanks the dot without
    touching the envelope) — cached, it would go stale and quietly report a g the dial is no
    longer pointing at."""
    cx, cy, r = dial_geom(w, h)

    # panel-grey backing (C.surface) + theme hairline so the dial reads as app chrome over footage
    backdrop = QRectF(1, 1, w - 2, h - 2)
    p.setBrush(_c(C.surface, 168))
    p.setPen(QPen(_c(C.border, 200), 1))
    p.drawRoundedRect(backdrop, theme.RADIUS_M, theme.RADIUS_M)

    # the reference ring — theme hairline, dim
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(_c(C.border, 190), 1.0))
    rr = r * (_SCALE_RING_G / _FULL_SCALE_G)
    p.drawEllipse(QPointF(cx, cy), rr, rr)
    # outer boundary ring — the interactive/hover hairline (C.border_strong), a touch stronger
    p.setPen(QPen(_c(C.border_strong, 215), 1.2))
    p.drawEllipse(QPointF(cx, cy), r, r)
    # faint crosshair guides (tick/axis marks) — the muted tertiary text token, very low alpha
    p.setPen(QPen(_c(C.text_muted, 80), 0.8))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

    # grip envelope: low-alpha amber fill + brighter amber rim
    if len(st.hull_pts) >= 3:
        hull = _convex_hull(st.hull_pts)
        if len(hull) >= 3:
            poly = QPolygonF([QPointF(*dial_to_screen(cx, cy, r, hx, hy))
                              for (hx, hy) in hull])
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            p.setBrush(_c(C.accent, 38))             # quiet amber wash — lets the grid show through
            p.setPen(QPen(_c(C.accent_hover, 215), 1.4))  # bright amber rim = the grip envelope
            p.drawPath(path)

    # the one scale caption, floated off both ring strokes (see _scale_caption)
    cap_f = _font(_caption_px(r))
    item = _scale_caption(cx, cy, r, QFontMetricsF(cap_f))
    if item is not None:
        rect, text = item
        p.setFont(cap_f)
        p.setPen(QPen(_c(C.text_dim, 205)))
        p.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, text)


def _paint_dial_moving(p: QPainter, w: float, h: float, st: DialState) -> None:
    """The PER-FRAME live layer: the dot's trail, the felt-force dot itself, and the |g| readout —
    the three things that change on every ~30 Hz tick. Painted on top of the static layer.

    With no g signal at all (`st.seen` False) the trail and dot are skipped and the readout is the
    no-value mark: the instrument at rest rather than a fabricated `0.0`."""
    cx, cy, r = dial_geom(w, h)

    if st.seen:
        wide = max(1.6, r * _TRAIL_WIDTH_FRAC)
        p.setBrush(Qt.NoBrush)
        for a, b, f in _trail_segments(cx, cy, r, st.trail):
            pen = QPen(_c(C.text, int(25 + 140 * f)),
                       wide * (_TRAIL_TAPER + (1 - _TRAIL_TAPER) * f))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(a, b)

    if st.have:
        dx, dy = dial_to_screen(cx, cy, r, st.fx, st.fy)
        glow = max(_DOT_GLOW_MIN, r * _DOT_GLOW_FRAC)
        core = max(_DOT_CORE_MIN, r * _DOT_CORE_FRAC)
        grad = QRadialGradient(QPointF(dx, dy), glow + 1.0)
        grad.setColorAt(0.0, _c(C.accent_hover, 245))
        grad.setColorAt(1.0, _c(C.accent_hover, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(dx, dy), glow, glow)
        p.setPen(QPen(_c(C.canvas, 220), 1.0))   # thin dark ring so the core reads off the glow
        p.setBrush(_c(C.text, 250))
        p.drawEllipse(QPointF(dx, dy), core, core)

    # the one readout, in the band dial_geom reserves for it — so it can never land on the dial
    p.setFont(_font(_readout_px(r), bold=True))
    p.setPen(QPen(_c(C.text if st.seen else C.text_dim, 245)))
    p.drawText(readout_rect(w, h), Qt.AlignHCenter | Qt.AlignVCenter, readout_text(st))


def _paint_dial_export(p: QPainter, w: float, h: float, st: DialState, k: float) -> None:
    """The export g-dial: the same composition as the screen (two rings, one scale caption, the
    grip envelope, the trail, the dot, the |g| readout), restyled to survive being burned over
    bright footage — no backdrop box, white haloed rings, a brighter amber envelope and trail, a
    bigger glowing dot, and outlined text. Layout is the shared `dial_geom`; `k` scales strokes and
    glyphs with the output height.

    NO SIGNAL, NO DIAL. Every export of a recording with no g series used to burn in a complete
    instrument — two rings, a crosshair, six legend words and four `0.0` peaks — that measured
    nothing, because `overlay_values_at` hands the painter `g=None` on every frame and the dial had
    no way to tell that from "no sample yet" (review §6.5). `st.seen` is that distinction, and here
    it means the dial is simply not painted: the clip carries the overlays it has data for. On
    screen the same recording cannot even reach this state — `central_view` disables the g-meter
    toggle and says why on its tooltip — so nothing is silently dropped.

    THE PROVENANCE TAG that used to be live-only is now on that toggle's tooltip for both
    (`source_sentence`), so there is no longer any string one mode paints and the other does not.
    The rule it states is real — the dial's LATERAL axis is the IMU (r ~ +0.89 against GPS) while
    its BRAKING/ACCEL axis is the GPS speed-derivative, the IMU forward axis being vibration-
    inflated (r ~ +0.36) — and it is stated in words on a hover instead of nine pixels of sensor
    plumbing under a dial in a clip someone watches. See studio/docs/gmeter-validation.md."""
    if not st.seen:
        return
    k = max(0.5, float(k))
    cx, cy, r = dial_geom(w, h)

    # --- rings (white, high-contrast) with a dark halo so they read on bright sky too ---
    p.setBrush(Qt.NoBrush)
    rr = r * (_SCALE_RING_G / _FULL_SCALE_G)
    p.setPen(QPen(_c(_EX_HALO, 150), 3.0 * k))
    p.drawEllipse(QPointF(cx, cy), rr, rr)
    p.setPen(QPen(_c(_EX_GRID, 150), 1.4 * k))       # the reference ring
    p.drawEllipse(QPointF(cx, cy), rr, rr)
    # outer boundary ring — brightest
    p.setPen(QPen(_c(_EX_HALO, 170), 4.2 * k))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setPen(QPen(_c(_EX_GRID, 235), 2.2 * k))
    p.drawEllipse(QPointF(cx, cy), r, r)
    # crosshair guides (haloed white, subtle)
    p.setPen(QPen(_c(_EX_HALO, 130), 2.6 * k))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
    p.setPen(QPen(_c(_EX_GRID, 130), 1.1 * k))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

    # --- filled max-G envelope (grip used this lap): brighter amber, haloed outline ---
    if len(st.hull_pts) >= 3:
        hull = _convex_hull(st.hull_pts)
        if len(hull) >= 3:
            poly = QPolygonF([QPointF(*dial_to_screen(cx, cy, r, hx, hy))
                              for (hx, hy) in hull])
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            p.setPen(QPen(_c(_EX_HALO, 150), 3.4 * k))   # dark halo under the envelope edge
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(QPen(_c(_EX_ACCENT, 235), 2.0 * k))
            p.setBrush(_c(_EX_ACCENT, 70))
            p.drawPath(path)

    # --- the dot's trail. One dark polyline underneath carries the halo (a per-segment halo would
    # double the draw count for a stroke that is one shape), then the tapered fading amber over it.
    segs = _trail_segments(cx, cy, r, st.trail)
    if segs:
        wide = max(2.0, r * _TRAIL_WIDTH_FRAC)
        halo = QPen(_c(_EX_HALO, 150), wide + 2.4 * k)
        halo.setCapStyle(Qt.RoundCap)
        halo.setJoinStyle(Qt.RoundJoin)
        p.setPen(halo)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([a for a, _b, _f in segs] + [segs[-1][1]]))
        for a, b, f in segs:
            pen = QPen(_c(_EX_TEXT, int(35 + 175 * f)),
                       wide * (_TRAIL_TAPER + (1 - _TRAIL_TAPER) * f))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(a, b)

    # --- the one scale caption, outlined; same string and same placement as the screen ---
    cap_f = _font(_caption_px(r))
    item = _scale_caption(cx, cy, r, QFontMetricsF(cap_f))
    if item is not None:
        rect, text = item
        _draw_text_outlined(p, rect, Qt.AlignHCenter | Qt.AlignVCenter, text, cap_f, _EX_TEXT,
                            halo=1.5 * k)

    # --- the live felt-force dot: a bigger soft glow + a dark-haloed bright core ---
    if st.have:
        dx, dy = dial_to_screen(cx, cy, r, st.fx, st.fy)
        gr = 13.0 * k
        grad = QRadialGradient(QPointF(dx, dy), gr)
        grad.setColorAt(0.0, _c(_EX_ACCENT_HI, 235))
        grad.setColorAt(0.6, _c(_EX_ACCENT, 150))
        grad.setColorAt(1.0, _c(_EX_ACCENT, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(dx, dy), gr, gr)
        p.setPen(QPen(_c(_EX_HALO, 220), 1.8 * k))   # dark ring so the dot reads on bright sky
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(dx, dy), 4.6 * k, 4.6 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(_c(_EX_TEXT, 250))
        p.drawEllipse(QPointF(dx, dy), 4.0 * k, 4.0 * k)

    # --- the one readout, in the band under the dial (the same band the screen uses) ---
    rd_f = _font(_readout_px(r), bold=True)
    _draw_text_outlined(p, readout_rect(w, h), Qt.AlignHCenter | Qt.AlignVCenter,
                        readout_text(st), rd_f, _EX_TEXT, halo=2.2 * k)


class GMeterOverlay(QWidget):
    """The LIVE on-screen dial: a window that owns a `DialFilter` and repaints when it changes.

    Everything that is not a window — the EMA, the trail, the envelope, the peaks, the lap scope —
    is the filter's (see `DialFilter` for why it is separable). This class keeps the widget-only
    concerns: the frameless translucent top-level window, the static-layer pixmap cache, and
    calling `update()` at exactly the points it always did (each mutator repaints iff the filter
    reports the painted dial changed)."""

    def __init__(self, parent: QWidget | None = None):
        # Frameless translucent top-level window so it composites above the native video surface
        # (a child widget would be hidden behind it on macOS); positioned by the VideoView.
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMinimumSize(120, 140)
        self._filter = DialFilter()
        # --- static-layer cache (a per-frame repaint blits this + draws the moving layer) ---
        # The cached pixmap is keyed by (size, palette, `_filter.version`), which bumps whenever the
        # static layer's content changes (the grip envelope points), so the convex hull + all the
        # ring/backdrop/caption drawing recompute exactly ONCE per envelope change — not every
        # ~30 Hz tick that only moves the dot, its trail and the readout.
        self._static_pixmap = None          # QPixmap | None
        self._static_key: tuple | None = None

    # ------------------------------------------------------------------ data in
    def set_g(self, g: tuple[float, float, float] | None) -> None:
        """Push the current kart-frame (lateral_g, longitudinal_g, total_g) and repaint if the
        dial changed. See `DialFilter.set_g`."""
        if self._filter.set_g(g):
            self.update()

    def set_lap(self, lap_id: int | None) -> None:
        """Set the current lap; a change to a new valid lap resets the envelope + peaks (and only
        then is there anything to repaint). See `DialFilter.set_lap`."""
        if self._filter.set_lap(lap_id):
            self.update()

    def reset_envelope(self) -> None:
        """Clear the envelope + cardinal peaks + trail and re-seed the dot EMA. See
        `DialFilter.reset_envelope`."""
        self._filter.reset_envelope()
        self.update()

    # ------------------------------------------------------------------ painting
    def _geom(self):
        # thin delegate to dial_geom; kept for the offscreen tests
        return dial_geom(self.width(), self.height())

    def _to_screen(self, cx, cy, r, fx, fy):
        # thin delegate to dial_to_screen (tests)
        return dial_to_screen(cx, cy, r, fx, fy)

    def _dial_state(self) -> DialState:
        """Snapshot the live filtering state into a pure DialState for paint_dial (same snapshot
        the exporter renders from, so the burned dial matches the screen)."""
        return self._filter.snapshot()

    def _static_layer(self, st: DialState):
        """Return the cached static-dial QPixmap for the current size + palette + envelope-version,
        rendering it once on a miss. This is where the convex hull + the ring/backdrop/number
        drawing actually run — exactly once per envelope change, NOT once per ~30 Hz tick."""
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        key = (w, h, round(dpr, 4), theme.active_palette(), self._filter.version)
        if self._static_pixmap is not None and self._static_key == key:
            return self._static_pixmap
        # Render the static layer once into a transparent pixmap at the widget's device pixel ratio
        # (so it blits back 1:1 with no scaling — pixel-identical to painting it directly).
        pm = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        sp = QPainter(pm)
        sp.setRenderHint(QPainter.Antialiasing, True)
        _paint_dial_static(sp, w, h, st)
        sp.end()
        self._static_pixmap = pm
        self._static_key = key
        return pm

    def paintEvent(self, _event):
        # Per-frame cost = blit the cached static layer + draw the moving one. The backdrop, the
        # two rings, the crosshair, the scale caption and the grip envelope (convex hull) are
        # rendered once into `_static_pixmap` and reused until the envelope/size/palette changes;
        # the trail, the dot and the |g| readout are re-drawn each ~30 Hz tick.
        st = self._dial_state()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawPixmap(0, 0, self._static_layer(st))
        _paint_dial_moving(p, self.width(), self.height(), st)
        p.end()


def _pct(vals, q):
    """The q-th percentile of `vals` (a robust peak), or 0.0 if empty. Pure-Python (no numpy in
    the per-tick paint path) — the lists are tiny (<= _PEAK_WINDOW)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = (q / 100.0) * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _convex_hull(points):
    """Andrew's monotone-chain convex hull of `points` (list of (x,y)). Returns the hull vertices
    CCW. O(n log n); n <= _ENVELOPE_MAX_PTS, recomputed per paint (cheap at these sizes)."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for pt in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], pt) <= 0:
            lower.pop()
        lower.append(pt)
    upper = []
    for pt in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], pt) <= 0:
            upper.pop()
        upper.append(pt)
    return lower[:-1] + upper[:-1]
