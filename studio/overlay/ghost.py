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

THE LOOK is the app's own "other lap" language (map_view's compare ghost): the compare cyan as a
HOLLOW ring on a dark plate, where the kart is a filled hot-coral dot — so it never reads as you,
or as the amber tail, or the white line, in either palette (the palette moves only the Δ hues).
Painted on `LAYER_MAP`, under the kart dot and its tail.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen

from studio import lap_race, theme
from studio.export_palette import EXPORT
from studio.overlay import LAYER_MAP, ElementContext

# The app's compare-ghost cyan (map_view.GHOST_COLOR is this entry): "the other lap".
GHOST_COLOR = theme.CHART_SERIES[1]
# Sizes at a 1080 px short side, scaled by the export's `k` like every Classic stroke. The ring's
# radius matches the kart dot's white rim (4.6) plus its pen, so the two read as the same size class.
_RING_R = 5.6
_RING_W = 2.4
_PLATE_ALPHA = 200
_HALO_W = 4.4


def _lap_name(lap_id: int) -> str:
    return f"Lap {lap_id + 1}"


def unavailable_reason(session, lap_id: int | None, follow_laps: bool = False) -> str | None:
    """Why `lap_id`'s export can have no ghost, as a sentence for the dialog — or None when it can.
    `lap_id` None asks only the session-wide rules (the All-laps scope: each lap is asked again)."""
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
        return "This is your best lap: the ghost would be itself."
    # Asked of the in-app Lap Race's own field (every clean lap: the memo the app already holds),
    # so a left-out lap carries its reason; a two-lap field would drop to None and lose it.
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
        if ctx.map_point is None or unavailable_reason(ctx.session, spec.lap_id,
                                                       spec.follow_laps) is not None:
            return False
        best = int(ctx.session.best_lap_id())
        self._tracks = ctx.session.race_tracks([int(spec.lap_id), best])
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
        st = self.state(vals)
        if st is None:
            return
        c = self._point(float(st.x[self._row]), float(st.y[self._row]))
        k = self._k
        r = _RING_R * k
        p.setPen(QPen(theme.qcolor(EXPORT.halo, 220), _HALO_W * k))
        p.setBrush(theme.qcolor(EXPORT.halo, _PLATE_ALPHA))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(theme.qcolor(GHOST_COLOR, 255), _RING_W * k))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r, r)
