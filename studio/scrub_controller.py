"""ScrubController: the plot-cursor scrub cluster (fine, lap-scoped scrub over speed/Δ charts;
the full-video slider is separate). In compare mode the drag is distance-locked and parks BOTH
panes on the same track position.

Coalesce contract: a drag move only stashes the latest target + a dirty flag; the seek(s) AND the
cursor/marker/readout refresh are coalesced to <=1 each per tick (`apply_tick`, from `_tick`) —
this keeps the tick cheap and breaks the drag<->positionChanged feedback loop. Pacer-free, Qt-free.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:  # injected collaborators — typed for readers, not imported at runtime
    from .compare_controller import CompareController
    from .map_view import MapView
    from .playback_state import PlaybackState
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
        playback: PlaybackState,
    ):
        self.session = session
        self.video = video
        self.plots = plots
        self.map = map_view
        # view-side readout callback (lives on StudioWindow); the scrub drives it for the dragged time.
        self._apply_readout = apply_readout
        # shared PlaybackState: reads applied_t (the grabbed lap) and seeds it to the final target
        # on release.
        self.playback = playback
        # Wired after construction (the two controllers are mutually referential): compare mode
        # turns the scrub distance-locked (fan the coalesced seek to the secondary pane).
        self.compare: CompareController | None = None

        # --- scrub state (owned here) ---
        self._scrub_lap: int | None = None       # the lap captured at grab; the drag is scoped to it
        self._scrub_target: float | None = None   # latest requested telemetry time (coalesced)
        self._scrub_pending = False               # a new target awaits the next tick's seek
        self._scrub_view_t: float | None = None    # latest dragged time for the view refresh (coalesced)
        self._scrub_view_pending = False           # a view refresh (cursor/marker/readout) awaits the tick
        self._scrub_was_playing = False            # restore playback on release iff it was playing
        # SECONDARY pane coalesced seek target+dirty (compare distance-lock; mirrors the primary
        # fields above).
        self._scrub_target_b: float | None = None
        self._scrub_pending_b = False

    def set_compare(self, compare: CompareController) -> None:
        """Inject the compare controller after construction (mutually-referential wiring)."""
        self.compare = compare

    # --- read-only state the tick loop + compare controller observe ---
    @property
    def is_active(self) -> bool:
        """True while a scrub drag owns the truth (a target was set this drag)."""
        return self._scrub_target is not None

    @property
    def target(self) -> float | None:
        """Primary pane latest coalesced scrub target."""
        return self._scrub_target

    @property
    def target_b(self) -> float | None:
        """Secondary pane latest coalesced target (compare distance-lock)."""
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
        """One coalesced seek/tick to the latest dragged target; do NOT apply seek-driven playback
        while dragging — that gating breaks the feedback loop. Caller gates on `is_active`."""
        if self._scrub_pending:
            self._scrub_pending = False
            # pending is only ever set together with a target (on_moved), never alone
            self.video.seek(cast(float, self._scrub_target))
        # fan the coalesced seek to the secondary pane (compare distance-lock)
        if self._is_comparing and self._scrub_pending_b:
            self._scrub_pending_b = False
            if self._scrub_target_b is not None:
                self.video.seek_pane(1, self._scrub_target_b)
        # views: one refresh/tick to the latest dragged time
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
        # compare mode: scope to the pinned pair (distance-lock), not a single playhead lap.
        self._scrub_lap = (self._compare_a if self._is_comparing
                           else self.session.lap_at_time(self.playback.applied_t or 0.0))
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
        """Drag: convert plot-x (in mode axis) to a telemetry time in the captured lap, clamped; stash
        as latest target + dirty flag (seek + view refresh coalesced to the next tick)."""
        lap = self._scrub_lap
        if lap is None:  # not inside a valid lap (lead-in / between laps) — no-op
            return
        # distance-mode x is scaled by the active baseline total (reference when loaded, else local
        # best) — the same basis delta() scaled the x-grid with.
        best_d = self.session.active_baseline_total_distance()
        t = self.session.media_time_at_plot_x(lap, x, mode, best_distance=best_d)
        if t is None:
            return
        self._scrub_target = t
        self._scrub_pending = True
        # The primary pane's lap position drives the cursor/marker/readout; apply it in the tick.
        self._scrub_view_t = t
        self._scrub_view_pending = True
        compare_b = self._compare_b
        if self._is_comparing and compare_b is not None:
            # distance-lock: convert the same plot-x to the secondary lap's global telemetry time.
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
        # flush a final view refresh if the last drag move never reached a tick
        if view_t is not None:
            self.plots.set_playhead_time(view_t, force=True)
            self.map.set_playhead_time(view_t)
            self._apply_readout(view_t)
        if target is not None:
            self.video.seek(target)
            self.playback.applied_t = target  # keep current-lap/readout consistent until the seek lands
        if self._is_comparing and target_b is not None:
            self.video.seek_pane(1, target_b)  # secondary pane (final distance-locked park)
        if self._scrub_was_playing:
            self.video.play()  # fans out to both panes in compare mode
        self._scrub_was_playing = False
        self._scrub_lap = None

    # ----------------------------------------------------------- compare-state helpers
    # compare-state accessors: tolerate the controller not being wired yet.
    @property
    def _is_comparing(self) -> bool:
        return self.compare is not None and self.compare.active

    @property
    def _compare_a(self) -> int | None:
        return self.compare.lap_a if self.compare is not None else None

    @property
    def _compare_b(self) -> int | None:
        return self.compare.lap_b if self.compare is not None else None
