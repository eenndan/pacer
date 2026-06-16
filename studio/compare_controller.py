"""CompareController: the dual-lap compare-mode behavioural cluster, extracted from StudioWindow.

Compare mode shows two equal side-by-side video panes (the PRIMARY/left pane keeps driving ALL
telemetry exactly as in single-video mode; the SECONDARY/right pane is video-only) playing
"time into lap" — both roll from S/F at 1×, the faster pulls ahead, each shown as a per-pane
"Δ vs other" badge. This object OWNS the compare state (the on/off flag + the pinned (A,B) lap ids)
and the enter/exit-compare orchestration + the per-tick compare upkeep (`tick`: pane times, badges,
secondary g, the (t_a,t_b) early-out incl. the scrub-bypass, and the F4 map GHOST — lap B's kart
drawn on the track map at the secondary pane's own time, removed on exit).

It is a plain control-layer collaborator: it talks to Session's public API and the view widgets it
is handed, never to `pacer` directly (the views-stay-pacer-free boundary). It is Qt-free itself —
StudioWindow forwards the compare/repoint signals + the per-tick compare branch into it, injecting
its collaborators + the small auto-follow / default-selection hooks it needs (entering/leaving
compare suspends StudioWindow's auto-follow re-point and restores the table-driven selection).

Behaviour is byte-identical to the pre-extraction StudioWindow methods (`_on_compare_toggled`,
`_enter_compare`, `_exit_compare`, `_on_pane_repoint`, `_compare_tick`, `_set_pane_badge`,
`_reset_pair_to_start`, `_seek_pane_to_lap_start`, `_lap_caption`, `_lap_choice_labels`); this is a
move + dependency-injection, not a redesign.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import theme
from ._signal import fmt_time

if TYPE_CHECKING:  # injected collaborators — typed for readers, not imported at runtime
    from .map_view import MapView
    from .plots_view import PlotsView
    from .scrub_controller import ScrubController
    from .session import Session
    from .video_view import VideoView

class CompareController:
    def __init__(
        self,
        session: Session,
        video: VideoView,
        plots: PlotsView,
        table,
        set_followed_lap: Callable[[int | None], None],
        select_default: Callable[[], None],
        get_applied_t: Callable[[], float | None],
        map_view: MapView | None = None,
        on_pair_changed: Callable[[], None] | None = None,
    ):
        self.session = session
        self.video = video
        self.plots = plots
        self.table = table
        # F4: the map gets a GHOST marker — lap B's kart at the secondary pane's own time —
        # while compare is on. Injected like the other view collaborators (optional so the
        # controller stays drivable without a map in unit tests); cleared on exit.
        self.map = map_view
        # F5: fired whenever the compared pair changes (enter / exit / pane repoint) so the app
        # can refresh the per-lap driving channels (brake glyphs) for the new pair — both laps in
        # compare, the current lap on exit. Injected as a callable so the controller stays
        # Qt-free and doesn't reach into the window. Optional (None = a no-op, e.g. in tests).
        self._on_pair_changed = on_pair_changed or (lambda: None)
        # Entering/leaving compare suspends/restores StudioWindow's auto-follow (freeze _followed_lap
        # on the primary lap while comparing; clear it on exit) and, on exit, restores the
        # table-driven chart selection (the `_select_default` fallback). Injected as callables so the
        # controller stays Qt-free and doesn't hold a back-reference to the whole window.
        self._set_followed_lap = set_followed_lap
        self._select_default = select_default
        self._get_applied_t = get_applied_t
        # Wired after construction (mutually referential): the per-tick early-out is BYPASSED during
        # a distance-locked scrub so the badges/g track the drag, driven from the scrub's targets.
        self.scrub: ScrubController | None = None

        # --- compare state (owned here) ---
        self._compare = False
        self._compare_a: int | None = None  # primary (left) lap id
        self._compare_b: int | None = None  # secondary (right) lap id
        # Last (t_a, t_b) the badges/g were computed for — lets tick() early-out when neither pane
        # moved (mirrors the playback _applied_t gate). A sentinel that no real (float, float)
        # equals, so the first tick after enter always applies.
        self._compare_last_t: object = None
        # F7 Phase B (cross-recording video compare): pane B can come from a DIFFERENT recording —
        # the loaded reference Session (`session.reference_session()`). When that mode is active,
        # `_cross` is True and `_session_b` is the reference Session: pane B's g / lap window / lap
        # id / video source all resolve against IT, not `self.session`. The pane-B Δ badge + the
        # F4 map ghost route through the cross-recording helpers on the PRIMARY session (which owns
        # the reference curve + the fitted overlay line). DORMANT (`_cross` False): `_session_b is
        # self.session` and every pane-B feed is byte-identical to same-recording compare today.
        self._cross = False
        self._session_b: Session = session

    @property
    def session_b(self) -> Session:
        """The session pane B's telemetry (g / lap window / lap id / video source) resolves
        against: `self.session` for same-recording compare (byte-identical to today), or the
        retained reference Session for the cross-recording video compare (F7 Phase B)."""
        return self._session_b

    @property
    def cross(self) -> bool:
        """True iff the active compare is a CROSS-RECORDING video compare (pane B = the reference
        recording's lap), distinct from the same-recording two-lap compare."""
        return self._cross

    def set_scrub(self, scrub: ScrubController) -> None:
        """Inject the scrub controller after construction (mutually-referential wiring)."""
        self.scrub = scrub

    # --- read-only state the tick loop, scrub controller + auto-follow observe ---
    @property
    def active(self) -> bool:
        """True iff compare mode is on (the semantic compare ownership; distinct from VideoView's
        own two-pane LAYOUT flag). Auto-follow's re-point is suspended while this is True."""
        return self._compare

    @property
    def lap_a(self) -> int | None:
        return self._compare_a

    @property
    def lap_b(self) -> int | None:
        return self._compare_b

    # ------------------------------------------------------------------ per-tick upkeep
    def tick(self) -> None:
        """Per-tick compare upkeep (O(1)): feed the SECONDARY pane its own-lap g, and refresh each
        pane's "Δ vs other" badge at that pane's current track position. The PRIMARY pane's g and
        telemetry are still driven by the single-valued readout path — this never touches
        StudioWindow's _latest_t/_applied_t, so the primary telemetry stays exactly as today.

        Called from StudioWindow's `_tick` in BOTH the scrub branch (to keep the secondary g + Δ
        badges live while scrubbing) and the playback branch (when compare is on)."""
        a, b = self._compare_a, self._compare_b
        if a is None or b is None:
            return
        scrubbing = self.scrub is not None and self.scrub.is_active
        if scrubbing:
            # During a distance-locked scrub the pane times lag (the coalesced seeks are still in
            # flight), so the (t_a, t_b) early-out below would key off stale times and freeze the
            # badges/g at the pre-scrub position. Drive them instead from the scrub's OWN clamped
            # target times (already computed per-tick in on_moved): t_a = primary target,
            # t_b = secondary target. Fall back to the live pane time if a target isn't set yet
            # (e.g. the grab before the first move), and bypass the early-out so they stay live.
            t_a = self.scrub.target
            t_b = (self.scrub.target_b if self.scrub.target_b is not None
                   else self.video.current_pane_time(1))
        else:
            t_a = self.video.current_pane_time(0)  # primary pane's global time
            t_b = self.video.current_pane_time(1)  # secondary pane's global time (read once)
            # Early-out when NEITHER pane time changed since the last tick (mirrors the playback
            # _latest_t != _applied_t gate): paused/idle compare does zero badge/g work per tick.
            # Skipped while scrubbing so the badges/g track the drag, not the lagging pane times.
            if (t_a, t_b) == self._compare_last_t:
                return
        self._compare_last_t = (t_a, t_b)
        # Secondary g (the primary's g comes from the readout path). A no-op if the overlay is off.
        # Cross-recording: pane B's g comes from the REFERENCE session's own g(t) on its own clock.
        if self.video.is_gmeter_visible():
            self.video.set_pane_g(1, self._session_b.g_at_time(t_b))
        # Each pane's Δ vs the OTHER lap, at that pane's own current track position.
        if self._cross:
            # Cross-recording: pane A's badge is the primary lap vs the REFERENCE (the active
            # baseline, so delta_at_lap already measures vs it); pane B's badge is the reference
            # vs the primary lap at the reference's own track position. Both reuse the Phase-A
            # normalized-distance alignment, so they're consistent + endpoint-symmetric.
            self._set_pane_badge(0, self.session.delta_at_lap(a, t_a))
            self._set_pane_badge(1, self.session.reference_delta_vs_lap(a, t_b))
        else:
            self._set_pane_badge(0, self.session.delta_between(a, b, t_a))
            self._set_pane_badge(1, self.session.delta_between(b, a, t_b))
        # F4 map ghost: lap B's kart at the SAME t_b the badge above used (the secondary pane's
        # own clock — both panes play "time into lap" from S/F, so this is lap B's position at
        # equal elapsed-into-lap; no second time-alignment is invented).
        if self.map is not None:
            if self._cross:
                # The ghost must sit on the REFERENCE racing line (already fit into THIS session's
                # local frame by cross_reference), NOT the primary trace. Drive it from the
                # reference lap's progress at t_b indexed onto the fitted overlay ring.
                i = self.session.reference_overlay_index_at_progress(t_b)
                xy = self.session.reference_overlay_xy()
                if i is not None and xy is not None:
                    self.map.set_ghost_pos(float(xy[i, 0]), float(xy[i, 1]))
            else:
                # Same-recording: index_at_time is the same O(log n) lookup the red marker's tick
                # path resolves on the PRIMARY trace; the map does a setPos only.
                self.map.set_ghost_index(self.session.index_at_time(t_b))

    def _set_pane_badge(self, side: int, d: float | None) -> None:
        """Format + colour a pane's "Δ vs other" badge (+behind / −ahead vs the other pane's lap).
        Colour via the shared three-way rule (theme.delta_colour): green/red only when
        meaningfully ahead/behind; a dead-even |Δ| <= theme.DELTA_EVEN_EPS_S (like no delta at
        all) keeps the badge's neutral foreground — an exact 0 used to read GREEN."""
        if d is None:
            self.video.set_pane_badge(side, "Δ —", None)
        else:
            self.video.set_pane_badge(side, f"Δ {d:+.2f} s", theme.delta_colour(d))

    # ------------------------------------------------------------------ enter / exit
    def on_toggled(self, on: bool) -> None:
        """The "Compare videos" toggle flipped. On enter: seed (A,B) = (current/primary lap, best)
        — default current-vs-best (if they coincide, pick the next-fastest as B) — build the two
        panes, seek each to its lap start, drive the chart overlay with [A,B], and SUSPEND
        auto-follow's lap re-point. On exit: restore the single pane, re-enable auto-follow, and
        restore the table-driven chart selection."""
        if on:
            self.enter()
        else:
            self.exit()

    def enter(self) -> None:
        # Same-recording compare: pane B is another lap of THIS session. Make that explicit so a
        # prior cross-recording compare (or a re-entry) resets the routing to the primary session.
        self._cross = False
        self._session_b = self.session
        valid = self.session.valid_lap_ids()
        if len(valid) < 2:
            return  # the toggle should be disabled, but guard anyway
        best = self.session.best_lap_id()
        # A = the lap the playhead is currently in, else the primary table selection, else best.
        a = self.session.lap_at_time(self._get_applied_t() or 0.0)
        if a is None or a not in valid:
            sel = [lid for lid in self.plots.selected_lap_ids() if lid in valid]
            a = sel[0] if sel else (best if best in valid else valid[0])
        # B = best; if A already is best, pick the next-fastest valid lap as B.
        b = best if best is not None and best in valid else None
        if b is None or b == a:
            others = sorted((lid for lid in valid if lid != a),
                            key=self.session.lap_time)
            b = others[0] if others else a
        self._compare = True
        self._compare_a, self._compare_b = a, b
        wa, wb = self.session.lap_window(a), self.session.lap_window(b)
        if wa is None or wb is None:
            self._compare = False
            return
        # pane_b_source=None → the secondary pane reuses the PRIMARY recording's ChapterMap
        # (`video._source`), byte-identical to before — both panes play the SAME recording.
        self.video.set_compare(a, b, wa, wb, self._lap_caption(a), self._lap_caption(b),
                               valid, self._lap_choice_labels(valid), pane_b_source=None)
        # Each pane plays "time into lap": reset the pair to its lap starts, PAUSED, so both videos
        # are aligned at S/F and roll together on the next Play (no auto-play on enter).
        self._reset_pair_to_start()
        # The pair drives the chart overlay (A primary curve, B reference) and each pane's g scope.
        self.plots.set_laps([a, b])
        self.video.set_pane_gmeter_lap(0, a)
        self.video.set_pane_gmeter_lap(1, b)
        # Suspend auto-follow: freeze _followed_lap on A so the per-tick edge check never re-points
        # the charts while compare is on (also gated by self.active in _follow_current_lap).
        self._set_followed_lap(a)
        # Force the next tick() to recompute the badges/g for the new pair (the pane times
        # may not have moved, but the COMPARED LAPS changed).
        self._compare_last_t = None
        self._on_pair_changed()  # F5: refresh the brake glyphs to show BOTH compared laps

    def enter_cross(self) -> bool:
        """F7 Phase B: enter the CROSS-RECORDING video compare — pane A = the primary recording's
        current/selected lap (unchanged), pane B = the loaded REFERENCE recording's reference lap,
        played from the reference's OWN footage + telemetry. Requires a reference Session retained
        (`session.reference_session()`); returns False (a no-op) if none is loaded or the lap
        windows are degenerate, so the caller can keep the UI state coherent.

        v1 LOCKS pane B to the reference lap (no pane-B lap picker — a picker would have to list
        the reference recording's laps and re-route every pane-B feed through a reseed against the
        reference Session; deferred, see the PR note). Pane B's g / lap window / lap id / video
        source resolve through the reference Session; the Δ badge + map ghost reuse Phase A's
        normalized-distance reference curve + the fitted overlay line."""
        ref_sess = self.session.reference_session()
        ref_lap = self.session.reference_lap_id()
        if ref_sess is None or ref_lap is None:
            return False
        valid = self.session.valid_lap_ids()
        if not valid:
            return False
        best = self.session.best_lap_id()
        # Pane A = the lap the playhead is in, else the primary table selection, else best/first.
        a = self.session.lap_at_time(self._get_applied_t() or 0.0)
        if a is None or a not in valid:
            sel = [lid for lid in self.plots.selected_lap_ids() if lid in valid]
            a = sel[0] if sel else (best if best is not None and best in valid else valid[0])
        wa = self.session.lap_window(a)
        wb = ref_sess.lap_window(ref_lap)  # the reference lap's window on the REFERENCE clock
        if wa is None or wb is None:
            return False
        self._cross = True
        self._session_b = ref_sess
        self._compare = True
        self._compare_a, self._compare_b = a, ref_lap
        # Pane B's video source is the REFERENCE recording's ChapterMap (its footage); pane A keeps
        # the primary recording's source. The pane-B picker is locked to the reference lap (one
        # choice), so its choice list is just that lap's caption.
        pane_b_source = ref_sess.chapters or ref_sess.video_path
        cap_a = self._lap_caption(a)
        cap_b = self._cross_caption_b(ref_sess, ref_lap)
        # Pane A's picker still lists this session's valid laps; pane B's is locked (single entry).
        self.video.set_compare(a, ref_lap, wa, wb, cap_a, cap_b,
                               valid, self._lap_choice_labels(valid),
                               pane_b_source=pane_b_source,
                               pane_b_choices=[ref_lap], pane_b_choice_labels=[cap_b])
        self._reset_pair_to_start()
        # The chart overlay shows pane A's lap vs the reference baseline (already the active Δ
        # baseline since a reference is loaded) — drive it with just lap A; the reference curve is
        # drawn as the green baseline by Session.delta under REFERENCE_ID.
        self.plots.set_laps([a])
        self.video.set_pane_gmeter_lap(0, a)
        # Pane B's g scope is the reference lap, but the lap id lives on the REFERENCE session, not
        # this one. set_pane_gmeter_lap only needs the lap id for the scope window the overlay
        # draws; feed the reference lap id (the overlay reads its own per-pane g feed in tick()).
        self.video.set_pane_gmeter_lap(1, ref_lap)
        self._set_followed_lap(a)
        self._compare_last_t = None
        self._on_pair_changed()
        return True

    def _cross_caption_b(self, ref_sess: Session, ref_lap: int) -> str:
        """Pane B caption for the cross-recording compare: the reference RECORDING's label plus the
        reference lap time, so the cross-recording origin is obvious (the friend's file, not a lap
        of this session)."""
        label = self.session.reference_label() or "reference"
        return f"{label} · lap {ref_lap} · {fmt_time(ref_sess.lap_time(ref_lap))}"

    def exit(self) -> None:
        self._cross = False
        self._session_b = self.session
        self._compare = False
        self._compare_a = self._compare_b = None
        self.video.exit_compare()
        # F4: the ghost exists only while compare is on — remove it so the map's item state
        # returns byte-identical to pre-compare.
        if self.map is not None:
            self.map.clear_ghost()
        # Restore the table-driven chart selection + re-enable auto-follow (a fresh edge will
        # re-establish the followed lap on the next playhead movement).
        self._set_followed_lap(None)
        ids = self.table.selected_lap_ids()
        if ids:
            self.plots.set_laps(ids)
        else:
            self._select_default()
        self._on_pair_changed()  # F5: restore the single-lap brake glyphs on exit

    def on_pane_repoint(self, side: int, lap_id: int) -> None:
        """A pane's lap picker repointed that side to `lap_id`: re-seed its lap window + caption,
        re-seek it to the new lap start, and refresh the chart overlay + g scope. The OTHER pane is
        untouched. Drives the [A,B] pair that feeds the charts + the per-tick Δ badges."""
        if not self._compare:
            return
        if self._cross and side != 0:
            # v1: pane B is LOCKED to the reference lap in cross-recording compare — ignore any
            # pane-B repoint (the picker is a single locked entry, so this shouldn't fire).
            return
        valid = self.session.valid_lap_ids()
        if lap_id not in valid:
            return
        if side == 0:
            self._compare_a = lap_id
        else:
            self._compare_b = lap_id
        window = self.session.lap_window(lap_id)
        if window is None:
            return
        self.video.reseed_pane(side, lap_id, window, self._lap_caption(lap_id),
                               valid, self._lap_choice_labels(valid))
        self.video.set_pane_gmeter_lap(side, lap_id)
        # Realign the WHOLE pair at S/F, PAUSED — not just the side that changed — so the two
        # videos never end up "one mid-lap, the other at start". This clears the other pane's
        # lingering position too and leaves both ready to roll together on the next Play.
        self._reset_pair_to_start()
        # Refresh the chart overlay; freeze auto-follow on the (new) primary lap. Cross-recording
        # draws just lap A vs the reference baseline (pane B's reference curve is the green
        # baseline drawn by Session.delta), so the overlay is [A] there, [A,B] for same-recording.
        if self._cross:
            self.plots.set_laps([self._compare_a])
            self._set_followed_lap(self._compare_a)
        elif self._compare_a is not None and self._compare_b is not None:
            self.plots.set_laps([self._compare_a, self._compare_b])
            self._set_followed_lap(self._compare_a)
        # The compared pair changed — force the next tick() to recompute the badges/g.
        self._compare_last_t = None
        self._on_pair_changed()  # F5: refresh the brake glyphs to the new compared pair

    # ------------------------------------------------------------------ pane S/F realign
    def _seek_pane_to_lap_start(self, side: int, lap_id: int) -> None:
        """Seek one pane to a hair INTO its lap (the theme.LAP_SEEK_NUDGE_S nudge keeps the
        ms-quantized position inside the lap, mirroring the lap-table seek), so it parks on the
        lap's start. Pane B's window resolves against `session_b` — for cross-recording that is the
        REFERENCE session's clock (its local media time), so the seek lands on the reference lap's
        S/F in the reference footage, not a primary-clock time."""
        sess = self.session if side == 0 else self._session_b
        window = sess.lap_window(lap_id)
        if window is not None:
            self.video.seek_pane(side, window[0] + theme.LAP_SEEK_NUDGE_S)

    def _reset_pair_to_start(self) -> None:
        """The user's main pain was getting both videos to start together. So EVERY change to the
        compared pair (enter-compare, either picker repoint, any pair change) ends here: leave BOTH
        panes NOT playing and re-seek BOTH to their lap's start line — not just the side that
        changed — so the two videos are always realigned at S/F and ready to roll together on the
        next Play. This clears any lingering mid-lap position on the untouched pane (the "one video
        mid-lap, the other at start" state). The primary's seek drives the chart cursor / map marker
        to the primary's S/F via the normal tick path.

        IMPORTANT: only pause a pane that is actually PLAYING — calling pause() on a freshly-loaded
        pane that has never played puts QMediaPlayer in StoppedState (not PausedState), and a later
        play() from StoppedState RESTARTS from position 0, throwing away the seek-to-S/F (so the
        videos would NOT roll from their lap starts). Pausing only the playing pane keeps an
        already-stopped pane seekable, so play() resumes from each lap's start as intended."""
        a, b = self._compare_a, self._compare_b
        if a is None or b is None:
            return
        # Stop only panes that are actually playing (see the StoppedState caveat above), then seek
        # BOTH to their lap starts so the freshly-seeked position survives the next play().
        self.video.pause_if_playing()
        self._seek_pane_to_lap_start(0, a)  # PRIMARY -> its lap S/F
        self._seek_pane_to_lap_start(1, b)  # SECONDARY -> its lap S/F

    # ------------------------------------------------------------------ pane captions
    def _lap_caption(self, lap_id: int) -> str:
        """Per-pane caption "lap N · m:ss.mmm" for a lap id, marking the best lap with a ★, so the
        user can confirm which lap is loaded in each pane without opening the picker."""
        star = " ★" if lap_id == self.session.best_lap_id() else ""
        return f"lap {lap_id} · {fmt_time(self.session.lap_time(lap_id))}{star}"

    def _lap_choice_labels(self, lap_ids: list[int]) -> list[str]:
        """Picker item labels "lap N  (m:ss.mmm)" (★ on the best lap) so picking the right lap
        doesn't require guessing. Parallel to `lap_ids`; computed once per (re)seed, not per tick."""
        best = self.session.best_lap_id()
        return [f"lap {lid}  ({fmt_time(self.session.lap_time(lid))})"
                f"{'  ★' if lid == best else ''}" for lid in lap_ids]
