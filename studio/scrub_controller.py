"""ScrubController: the plot-cursor scrub behavioural cluster, extracted from StudioWindow.

A *fine, lap-scoped scrubber* over the speed/Δ charts (the full-video slider is separate and
unaffected). Dragging either plot cursor seeks the video WITHIN the current lap; in compare mode
the same drag is distance-locked and parks BOTH panes on the same track position. To keep the
30 Hz tick cheap and to break the drag↔positionChanged feedback loop, every drag move only stashes
the latest target + a dirty flag and returns; the actual seek(s) AND the cursor/marker/readout
view refresh are COALESCED to ≤1 each per tick (`apply_tick`, called from StudioWindow's `_tick`).

This object OWNS the scrub state and is a plain control-layer collaborator: it talks to Session's
public API and the view widgets it is handed, never to `pacer` directly (the views-stay-pacer-free
boundary). It is Qt-free itself — StudioWindow forwards the plots' scrub signals and the per-tick
scrub branch into it, injecting its collaborators + the two small playback-state hooks it needs.

Behaviour is byte-identical to the pre-extraction StudioWindow methods (`_on_scrub_started`,
`_on_scrub_moved`, `_on_scrub_ended`, the `take_marker_seek` drain, and the `_scrub_target`-gated
branch of `_tick`); this is a move + dependency-injection, not a redesign.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # injected collaborators — typed for readers, not imported at runtime
    from .compare_controller import CompareController
    from .map_view import MapView
    from .plots_view import PlotsView
    from .session import Session
    from .video_view import VideoView


class ScrubController:
    def __init__(
        self,
        session: Session,
        video: VideoView,
        plots: PlotsView,
        map_view: MapView,
        apply_readout: Callable[[float], None],
        get_applied_t: Callable[[], float | None],
        set_applied_t: Callable[[float | None], None],
    ):
        self.session = session
        self.video = video
        self.plots = plots
        self.map = map_view
        # StudioWindow's shared single-driver readout + its playback `_applied_t` cursor. The scrub
        # path drives the readout for the dragged time and, on release, seeds `_applied_t` so the
        # current-lap/readout stay consistent until the final seek lands.
        self._apply_readout = apply_readout
        self._get_applied_t = get_applied_t
        self._set_applied_t = set_applied_t
        # Wired after construction (the two controllers are mutually referential): compare mode
        # turns the scrub distance-locked (fan the coalesced seek to the secondary pane).
        self.compare: CompareController | None = None

        # --- scrub state (owned here) ---
        self._scrub_lap: int | None = None       # the lap captured at grab; the drag is scoped to it
        self._scrub_target: float | None = None   # latest requested media time (coalesced)
        self._scrub_pending = False               # a new target awaits the next tick's seek
        self._scrub_view_t: float | None = None    # latest dragged time for the view refresh (coalesced)
        self._scrub_view_pending = False           # a view refresh (cursor/marker/readout) awaits the tick
        self._scrub_was_playing = False            # restore playback on release iff it was playing
        # Compare-mode scrub: the drag is distance-locked, so it parks BOTH panes on the SAME
        # track position. The same plot-x converts to each lap's own global media time; each
        # pane's seek is coalesced to <=1/tick reusing the gate (the fields above are the PRIMARY
        # pane's; these add the SECONDARY pane's). Only used while compare is on.
        self._scrub_target_b: float | None = None
        self._scrub_pending_b = False

    def set_compare(self, compare: CompareController) -> None:
        """Inject the compare controller after construction (mutually-referential wiring)."""
        self.compare = compare

    # --- read-only state the tick loop + compare controller observe ---
    @property
    def is_active(self) -> bool:
        """True while a scrub drag is the source of truth (a target has been set this drag).
        The tick loop branches on this; the compare controller bypasses its (t_a,t_b) early-out
        while it's True so the badges/g track the drag, not the lagging pane times."""
        return self._scrub_target is not None

    @property
    def target(self) -> float | None:
        """The PRIMARY pane's latest coalesced scrub target (the drag's truth for the badges/g)."""
        return self._scrub_target

    @property
    def target_b(self) -> float | None:
        """The SECONDARY pane's latest coalesced scrub target (compare distance-lock)."""
        return self._scrub_target_b

    # --- map marker-drag coalescing drain (called first in the tick) ---
    def drain_marker_seek(self) -> None:
        """Drain a coalesced MAP MARKER-DRAG seek (one per tick, not per mouse-move): the marker
        stashes its latest dragged time and the resulting seek drives the normal playback→tick sync
        that re-places the marker/cursor/readout."""
        marker_t = self.map.take_marker_seek()
        if marker_t is not None:
            self.video.seek(marker_t)

    # --- per-tick scrub apply (the `_scrub_target is not None` branch of `_tick`) ---
    def apply_tick(self) -> None:
        """While the user is scrubbing a plot cursor, the source of truth is the drag, not playback:
        issue at most ONE coalesced seek per tick to the latest dragged target, and DON'T apply the
        (stale / seek-driven) playback position — that gating is what prevents the
        drag↔positionChanged feedback loop from oscillating.

        Caller (`_tick`) gates this on `is_active` and, after it, calls `compare.tick()` to keep the
        secondary g + Δ badges live while scrubbing."""
        if self._scrub_pending:
            self._scrub_pending = False
            self.video.seek(self._scrub_target)  # PRIMARY pane
        # Compare mode: the same drag is distance-locked across both panes — fan the coalesced
        # seek out to the SECONDARY pane too (its own lap's global time, computed in on_moved).
        if self._is_comparing and self._scrub_pending_b:
            self._scrub_pending_b = False
            if self._scrub_target_b is not None:
                self.video.seek_pane(1, self._scrub_target_b)
        # Apply the cursor/marker/readout ONCE per tick to the latest dragged time (coalesced
        # in on_moved) instead of on every mouse-move — the views are driven by the one
        # clamped truth `t`, and playback ticks are ignored mid-drag.
        if self._scrub_view_pending and self._scrub_view_t is not None:
            self._scrub_view_pending = False
            t = self._scrub_view_t
            self.plots.set_playhead_time(t, force=True)
            self.map.set_playhead_time(t)
            self._apply_readout(t)

    # ------------------------------------------------------------- plots scrub signals
    def on_started(self) -> None:
        """Grab: scope the scrub to the lap the playhead is currently in (compare mode: to the
        pinned pair A/B); pause playback, remembering whether it was playing so we can resume on
        release."""
        # In compare mode the scrub is distance-locked to the pinned pair (the drag parks BOTH
        # panes on the same track position), not to a single playhead lap.
        self._scrub_lap = (self._compare_a if self._is_comparing
                           else self.session.lap_at_time(self._get_applied_t() or 0.0))
        self._scrub_was_playing = self.video.is_playing()
        if self._scrub_was_playing:
            self.video.pause()
        self._scrub_target = None
        self._scrub_pending = False
        self._scrub_view_t = None
        self._scrub_view_pending = False
        self._scrub_target_b = None
        self._scrub_pending_b = False

    def on_moved(self, x: float, mode: str) -> None:
        """Drag: convert the raw plot-x (in `mode`'s axis) to a media time within the captured
        current lap, clamped to that lap. Store it as the latest target + a dirty flag and return
        immediately — the seek AND the cursor/marker/readout view refresh are both COALESCED to the
        next tick (≤1 of each per tick), so a fast drag does one conversion+view pass per tick
        instead of one per mouse-move."""
        lap = self._scrub_lap
        if lap is None:  # not inside a valid lap (lead-in / between laps) — no-op
            return
        # The distance-mode plot-x is scaled by the ACTIVE baseline total (the cross-recording
        # reference's total when one is loaded, else the local best) — the same basis delta()
        # scaled the x-grid with — so the dragged x maps back to the right track position.
        best_d = self.session.active_baseline_total_distance()
        t = self.session.media_time_at_plot_x(lap, x, mode, best_distance=best_d)
        if t is None:
            return
        self._scrub_target = t
        self._scrub_pending = True
        # The PRIMARY pane's lap position drives the cursor/marker/readout; apply it in the tick.
        self._scrub_view_t = t
        self._scrub_view_pending = True
        compare_b = self._compare_b
        if self._is_comparing and compare_b is not None:
            # Distance-locked: the SAME dragged plot-x is a track position; convert it to the
            # SECONDARY lap's own global media time so both panes park on the same spot. Coalesced
            # to <=1 seek/tick via _scrub_pending_b (the secondary's gate), exactly like the primary.
            t_b = self.session.media_time_at_plot_x(compare_b, x, mode, best_distance=best_d)
            if t_b is not None:
                self._scrub_target_b = t_b
                self._scrub_pending_b = True

    def on_ended(self) -> None:
        """Release: issue a final seek to the last target (so the frame matches the cursor even
        if the last move coalesced out), resume playback iff it was playing at grab, then clear
        the scrub state so the normal playback→cursor sync resumes."""
        target = self._scrub_target
        target_b = self._scrub_target_b
        view_t = self._scrub_view_t if self._scrub_view_pending else None
        self._scrub_target = None
        self._scrub_pending = False
        self._scrub_view_t = None
        self._scrub_view_pending = False
        self._scrub_target_b = None
        self._scrub_pending_b = False
        # Flush a final coalesced view refresh if the last drag move never reached a tick, so the
        # cursor/marker/readout end exactly on the released position (matches the pre-coalesce
        # behaviour where every move applied the views synchronously).
        if view_t is not None:
            self.plots.set_playhead_time(view_t, force=True)
            self.map.set_playhead_time(view_t)
            self._apply_readout(view_t)
        if target is not None:
            self.video.seek(target)  # PRIMARY pane
            self._set_applied_t(target)  # keep current-lap/readout consistent until the seek lands
        if self._is_comparing and target_b is not None:
            self.video.seek_pane(1, target_b)  # SECONDARY pane (final distance-locked park)
        if self._scrub_was_playing:
            self.video.play()  # fans out to both panes in compare mode
        self._scrub_was_playing = False
        self._scrub_lap = None

    # ----------------------------------------------------------- compare-state helpers
    # The scrub path reads three things off the compare controller (whether compare is on + the
    # pinned A/B lap ids); funnel them through these so the distance-lock logic above reads cleanly
    # and tolerates the controller not being wired yet (defensive — mirrors the old getattr guards).
    @property
    def _is_comparing(self) -> bool:
        return self.compare is not None and self.compare.active

    @property
    def _compare_a(self) -> int | None:
        return self.compare.lap_a if self.compare is not None else None

    @property
    def _compare_b(self) -> int | None:
        return self.compare.lap_b if self.compare is not None else None
