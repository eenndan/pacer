"""The GHOST: where your best lap was at the same lap time, as a ring on the export's minimap.

A single-lap export draws a second mark on the minimap: the session's best lap at the SAME elapsed
lap time as the frame. Its position comes from the in-app Lap Race (`lap_race.race_state` over a
two-lap `Session.race_tracks([lap, best])`), the on-track alignment the Δ family uses — so the gap
between the ring and the kart dot IS the Δ the lap strip prints on that frame (the design lane
measured agreement to 3.6e-14 s; tests/test_export_ghost.py pins it). There is no number beside
it: the strip already carries that Δ, and a second copy could only disagree.

WHEN THERE IS NO GHOST (`unavailable_reason`, the one rule the dialog and the render both read):
  * the exported lap IS the best lap — a ghost of yourself, at zero gap, says nothing;
  * a reference lap from another recording is loaded — the strip's Δ is then against THAT lap, so
    a ring at your own best would sit at a gap the strip does not print;
  * the start/finish line is provisional — every lap time, the best lap included, is referenced to
    an arbitrary point (the Laps table blanks its Δbest there for the same reason);
  * the Lap Race leaves either lap out (not a clean lap, a GPS dropout, no on-track match) — the
    ring would be an interpolated or equal-fraction guess;
  * the full-session scope — it follows every lap, so there is no one lap to race.
Never the ideal lap: it is a composite of segments, not a drive with a position at a time.
The ring is shown only between the lines: in the run-up there is no lap clock yet, and in the
run-off the strip's Δ is frozen at the finish while the best lap has long finished.

AFTER THE BEST LAP HAS FINISHED, while the exported lap is still running, the ring PARKS ON THE
FINISH LINE — `race_state` holds a finished lap at its finish, as the in-app Lap Race parks its
finished dots. That is the true answer to "where was the best lap at this lap time": done. The gap
to the dot then keeps growing with the strip's Δ; the ring goes when the exported lap crosses.

AN ESTIMATED CLOCK (an older camera's media-clock fallback, every lap time ±0.15 s) still shows
the ghost. The clock error moves the whole recording's times together: the kart dot and the ring
are both read on it, so they shift together, and the gap between them is a difference of two lap
times on the same clock — the strip's Δ, which the app already shows under that clock's caveat.

THE TWO HALVES RUN ON TWO THREADS. `unavailable_reason` (the dialog, GUI thread) reads the Lap
Race's default field — the memo the app already holds — because only that keeps each left-out
lap's reason. The render (`GhostElement.prepare`, the export's worker thread) never builds that
field: it asks the session-wide rules and then the two-lap field it needs anyway to paint. A lap
is left out of a field for reasons of its own (not clean, unmatched, a dropout), so either lap
being left out drops the two-lap field below `lap_race.MIN_FIELD` — the same verdict, no reason.

THE LOOK is the app's own "other lap" language (map_view's compare ghost): the compare cyan as a
HOLLOW ring on a dark plate, where the kart is a filled hot-coral dot — so it never reads as you,
or as the amber tail, or the white line, in either palette (the palette moves only the Δ hues).
Painted on `LAYER_MAP`, under the kart dot and its tail.

BESIDE THE KART IT IS A RING ROUND THE DOT. On a lap close to the best the ghost sits within a dot
radius of the kart for most of the lap, and under the dot it vanished exactly when the race was
close (QA, #556). So when its centre is within the dot's outer radius plus a margin (`_NEAR_R`) it
is drawn OVER the dot instead, as a bigger hollow ring (`_NEAR_RING_R`: the dot's radius plus a
stroke) still centred on the best lap's place, clipped out of the dot's own disc — it never covers
the driver; where it crosses the dot it passes behind it. Clear of the dot it keeps the plate look
above, under the dot, unchanged. Same position, same truth: only the drawing changes.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen

from studio import lap_race, theme
from studio.export_palette import EXPORT
from studio.overlay import DOT_R, LAYER_MAP, ElementContext

# The app's compare-ghost cyan (map_view.GHOST_COLOR is this entry): "the other lap".
GHOST_COLOR = theme.CHART_SERIES[1]
# Sizes at a 1080 px short side, scaled by the export's `k` like every Classic stroke. The ring's
# radius matches the kart dot's white rim (4.6) plus its pen, so the two read as the same size class.
_RING_R = 5.6
_RING_W = 2.4
_PLATE_ALPHA = 200
_HALO_W = 4.4
# Beside the kart (centre within the dot's outer radius plus a margin), the ring round the dot:
# its radius clears the dot by one stroke, its halo is a thin dark line rather than a plate.
_NEAR_R = DOT_R + 1.2
_NEAR_RING_R = DOT_R + _RING_W + 0.6
_NEAR_HALO_W = 1.6


def _lap_name(lap_id: int) -> str:
    return f"Lap {lap_id + 1}"


# A pass clip (export_video.PassSubject) burns no lap clock and no lap Δ, and the ring's gap to the
# kart IS the lap's running Δ from the start line — read beside a corner's name, it would read as
# that corner's gap. So a pass has no ghost.
PASS_REASON = ("A corner-pass clip shows no lap clock, and the ghost's gap is the lap's running Δ, "
               "not this corner's.")


def _session_reason(session, lap_id: int | None, follow_laps: bool) -> str | None:
    """The rules that need no Lap Race field: cheap, and safe on any thread."""
    if follow_laps:
        return "The full-session export follows every lap, so there is no one lap to race."
    best_fn = getattr(session, "best_lap_id", None)
    best = best_fn() if callable(best_fn) else None
    if best is None:
        return "There is no best lap to race against."
    if getattr(session, "has_reference", lambda: False)():
        return ("A reference lap from another recording is loaded: the lap strip's Δ is against "
                "it, not against your best lap.")
    if not bool(getattr(session, "timing_verified", False)):
        return "Lap times are provisional until the start/finish line is confirmed."
    if lap_id is None:
        return None
    if int(lap_id) == int(best):
        return f"{_lap_name(lap_id)} is your best lap: the ghost would be itself."
    return None


def unavailable_reason(session, lap_id: int | None, follow_laps: bool = False) -> str | None:
    """Why `lap_id`'s export can have no ghost, as a sentence for the dialog — or None when it can.
    `lap_id` None asks only the session-wide rules (the All-laps scope: each lap is asked again).
    For the GUI thread: it reads the Lap Race's default field (see the module doc)."""
    why = _session_reason(session, lap_id, follow_laps)
    if why is not None or lap_id is None:
        return why
    best = int(session.best_lap_id())
    # The in-app Lap Race's own field (every clean lap: the memo the app already holds), so a
    # left-out lap carries its reason; a two-lap field would drop to None and lose it.
    race = getattr(session, "race_tracks", None)
    tracks = race() if callable(race) else None
    if tracks is None:
        return f"{_lap_name(lap_id)} cannot be raced against your best lap."
    for lap in (int(lap_id), int(best)):
        if lap not in tracks.ids:
            why = tracks.excluded.get(lap, "")
            if why == lap_race.EXCLUDED_DROPOUT:
                return f"{_lap_name(lap)} has a GPS dropout, so its place on track is a guess."
            if why == lap_race.EXCLUDED_UNMATCHED:
                return f"{_lap_name(lap)} could not be matched on track to your best lap."
            return f"{_lap_name(lap)} is not a clean lap."
    return None


class GhostElement:
    name = "ghost"
    layer = LAYER_MAP

    def prepare(self, ctx: ElementContext) -> bool:
        spec = ctx.spec
        if getattr(spec, "pass_subject", None) is not None:
            return False                      # PASS_REASON
        if ctx.map_point is None or _session_reason(ctx.session, spec.lap_id,
                                                    spec.follow_laps) is not None:
            return False
        best = int(ctx.session.best_lap_id())
        # The two-lap field only (never memoized, never the app's default field): None, or
        # missing a lap, when the Lap Race leaves either out — see the module doc.
        self._tracks = ctx.session.race_tracks([int(spec.lap_id), best])
        if self._tracks is None or not {int(spec.lap_id), best} <= set(self._tracks.ids):
            return False
        self._best = best
        self._row = self._tracks.index(best)
        # The exported lap's own clock origin: race time 0 is this lap on its start line.
        self._clock_start = float(self._tracks.clock_starts[self._tracks.index(int(spec.lap_id))])
        self._point = ctx.map_point
        self._k = ctx.k
        return True

    def state(self, vals) -> lap_race.RaceState | None:
        """The two-lap race at this frame's lap time, or None outside the lap."""
        if not vals.lap_started or vals.lap_finished or vals.tt is None:
            return None
        return lap_race.race_state(self._tracks, float(vals.tt) - self._clock_start)

    def paint(self, p: QPainter, vals) -> None:
        """Without a dot to read (a caller outside the minimap): the plate look."""
        self.paint_map(p, vals, None, False)

    def near(self, c: QPointF, dot: QPointF | None) -> bool:
        """True when the ring's centre `c` is beside the kart `dot`: drawn round it, over it."""
        return dot is not None and math.hypot(c.x() - dot.x(), c.y() - dot.y()) < _NEAR_R * self._k

    def paint_map(self, p: QPainter, vals, dot: QPointF | None, over: bool) -> None:
        st = self.state(vals)
        if st is None:
            return
        c = self._point(float(st.x[self._row]), float(st.y[self._row]))
        near = self.near(c, dot)
        if near != over:
            return
        k = self._k
        if near:
            # Clipped out of the dot's disc: the ring passes behind the driver, never over it.
            clip = QPainterPath()
            big = (_NEAR_RING_R + _RING_W + 2.0) * k
            clip.addRect(QRectF(c.x() - big, c.y() - big, 2 * big, 2 * big))
            hole = QPainterPath()
            hole.addEllipse(dot, DOT_R * k, DOT_R * k)
            p.setClipPath(clip.subtracted(hole))
            r = _NEAR_RING_R * k
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(theme.qcolor(EXPORT.halo, 220), (_RING_W + 2 * _NEAR_HALO_W) * k))
            p.drawEllipse(c, r, r)
            p.setPen(QPen(theme.qcolor(GHOST_COLOR, 255), _RING_W * k))
            p.drawEllipse(c, r, r)
            return
        r = _RING_R * k
        p.setPen(QPen(theme.qcolor(EXPORT.halo, 220), _HALO_W * k))
        p.setBrush(theme.qcolor(EXPORT.halo, _PLATE_ALPHA))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(theme.qcolor(GHOST_COLOR, 255), _RING_W * k))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r, r)
