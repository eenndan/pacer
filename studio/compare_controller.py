"""CompareController: the dual-lap compare-mode behavioural cluster.

Compare mode shows two side-by-side video panes (left drives ALL telemetry; right is video-only)
playing "time into lap" from S/F, each with a per-pane "Δ vs other" badge. Owns the compare state
(on/off + pinned (A,B)), the enter/exit orchestration, and the per-tick upkeep.

Qt-free and pacer-free: talks only to Session's public API + injected view widgets + the shared
PlaybackState (writes `followed_lap` to suspend auto-follow, reads `applied_t` to seed pane A).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import theme
from ._signal import fmt_time, lap_label
from .video_view import PaneSpec

if TYPE_CHECKING:  # injected collaborators — typed for readers, not imported at runtime
    from .map_view import MapView
    from .playback_state import PlaybackState
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
        playback: PlaybackState,
        select_default: Callable[[], None],
        map_view: MapView | None = None,
        on_pair_changed: Callable[[], None] | None = None,
    ):
        self.session = session
        self.video = video
        self.plots = plots
        self.table = table
        self.map = map_view  # map ghost (lap B's kart); optional for tests
        self._on_pair_changed = on_pair_changed or (lambda: None)  # brake-glyph refresh on pair change
        # Shared PlaybackState: enter/exit writes `followed_lap` to suspend/restore auto-follow;
        # enter() reads `applied_t` to seed pane A.
        self.playback = playback
        self._select_default = select_default  # restore the table-driven chart selection on exit
        # Wired after construction; the per-tick early-out is bypassed during a distance-locked scrub.
        self.scrub: ScrubController | None = None

        self._compare = False
        self._compare_a: int | None = None  # primary (left) lap id
        self._compare_b: int | None = None  # secondary (right) lap id
        # Last (t_a, t_b) the badges/g were computed for; lets tick() early-out when neither pane
        # moved. Sentinel that no real (float, float) equals, so the first tick always applies.
        self._compare_last_t: object = None
        # Cross-recording compare: when _cross, pane B's g / lap window / lap id / video source all
        # resolve against _session_b (the reference Session), not self.session. Dormant otherwise
        # (_session_b is self.session, byte-identical to same-recording compare).
        self._cross = False
        self._session_b: Session = session
        # Sticky "prefer cross-recording compare" so toggling compare off/on after a cross compare
        # re-enters cross (keeping pane B's reference footage) instead of falling back to same-recording.
        self._prefer_cross = False

    @property
    def session_b(self) -> Session:
        """The session pane B resolves against: self.session same-recording, the reference Session cross."""
        return self._session_b

    @property
    def cross(self) -> bool:
        """True iff this is a cross-recording compare."""
        return self._cross

    def set_scrub(self, scrub: ScrubController) -> None:
        """Inject the scrub controller after construction (mutually-referential wiring)."""
        self.scrub = scrub

    def clear_prefer_cross(self) -> None:
        """D5: drop the sticky cross-recording preference (see on_reference_cleared) so a later
        compare toggle enters SAME-recording compare, not a cross compare against a reference that
        no longer exists."""
        self._prefer_cross = False

    def on_reference_cleared(self) -> None:
        """The app dropped the cross-recording reference: leave any compare that was ABOUT it, then
        drop the sticky preference for the next one.

        QA-W2R-05: clearing used to do only the second half. `_cross` and `_session_b` therefore
        outlived the reference they pointed at — the video quadrant kept a "REFERENCE" pane naming
        the cleared recording and playing its footage behind a dead "Δ —" badge, while every menu
        item and the status chip said there was no reference, and the reference Session's arrays
        stayed alive on `_session_b` against clear_reference's own "frees its decode/arrays"
        docstring. A cross compare exists only for the reference, so clearing the reference ends
        it; a same-recording compare is untouched, because the reference was never what it was
        about. Ordered exit-then-forget so `exit()` restores the table-driven selection once,
        before the app's own reference-change refresh runs."""
        if self._cross:
            self.exit()
        self.clear_prefer_cross()

    # --- read-only state the tick loop, scrub controller + auto-follow observe ---
    @property
    def active(self) -> bool:
        """True iff compare mode is on — the SINGLE source of truth for 'are we comparing'. VideoView
        is a dumb renderer that derives its two-pane layout from the live widget tree and is driven by
        this controller; it holds no compare flag. Auto-follow's re-point is suspended while True."""
        return self._compare

    @property
    def lap_a(self) -> int | None:
        return self._compare_a

    @property
    def lap_b(self) -> int | None:
        return self._compare_b

    # ------------------------------------------------------------------ slider/arrow distance-lock
    def fanout_seek_b(self, t_a: float) -> None:
        """The global scrub slider + arrow keys seek only pane A, which desyncs the pair in compare
        mode (D1). So distance-lock the same move to pane B: convert pane A's new media time `t_a`
        to a normalized-distance position, then back to pane B's own media time, and seek B there.
        No-op outside compare or if either lap is degenerate. Pane B resolves against session_b (the
        reference clock when cross)."""
        a, b = self._compare_a, self._compare_b
        if not self._compare or a is None or b is None:
            return
        # Shared distance axis: pane A media time → plot-x → pane B media time (distance mode, so the
        # two laps' different lengths map to the same lap fraction).
        best_d = self.session.best_lap_total_distance()
        x = self.session.plot_x_at_media_time(a, t_a, "distance", best_distance=best_d)
        if x is None:
            return
        t_b = self._session_b.media_time_at_plot_x(b, x, "distance", best_distance=best_d)
        if t_b is not None:
            self.video.seek_pane(1, t_b)

    # ------------------------------------------------------------------ per-tick upkeep
    def tick(self) -> None:
        """Per-tick compare upkeep (O(1)): pane B's g + both Δ badges + the F4 ghost; early-out when
        neither pane moved."""
        a, b = self._compare_a, self._compare_b
        if a is None or b is None:
            return
        scrubbing = self.scrub is not None and self.scrub.is_active
        if scrubbing:
            # Pane times lag during a distance-locked scrub, so drive from the scrub's own clamped
            # targets and bypass the early-out so the badges/g stay live.
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
        # Each pane's Δ vs the OTHER lap, at that pane's own track position.
        if self._is_same_lap_pair(a, b):
            # Degenerate pair: the pickers cross-exclude, so this is only reachable if the pair is
            # set programmatically. Say what it is — two "Δ +0.00 s" badges read as a dead-even
            # comparison of two laps, not as one lap measured against itself.
            self._set_pane_badge(0, None, same_lap=True)
            self._set_pane_badge(1, None, same_lap=True)
        elif self._cross:
            # Cross badge routing: pane A vs the reference, pane B (the reference) vs the primary.
            self._set_pane_badge(0, self.session.delta_at_lap(a, t_a))
            self._set_pane_badge(1, self.session.reference_delta_vs_lap(a, t_b))
        else:
            self._set_pane_badge(0, self.session.delta_between(a, b, t_a))
            self._set_pane_badge(1, self.session.delta_between(b, a, t_b))
        # F4 map ghost: lap B's kart at the same t_b the badge used (both panes play time-into-lap).
        if self.map is not None:
            if self._cross:
                # Cross ghost rides the fitted reference line, not the primary trace.
                i = self.session.reference_overlay_index_at_progress(t_b)
                xy = self.session.reference_overlay_xy()
                if i is not None and xy is not None:
                    self.map.set_ghost_pos(float(xy[i, 0]), float(xy[i, 1]))
            else:
                self.map.set_ghost_index(self.session.index_at_time(t_b))

    def _is_same_lap_pair(self, a: int, b: int) -> bool:
        """True when both panes are showing the SAME lap of the SAME recording.

        Equal ids are not enough in cross mode: there the two ids index DIFFERENT recordings, so
        lap 41 of one and lap 41 of the other is a perfectly ordinary comparison and badging it
        "same lap" would be a new lie in place of the old one. It IS the same lap when the
        reference was taken from this session's own footage — which set_reference_session refuses,
        but which the badge must not depend on it refusing (QA-W2R-04: the check sat AFTER the
        cross branch, so the badge was simply unreachable while comparing against a reference,
        and a recording referenced against itself pinned the same lap of the same file in both
        panes with both badged 'Δ +0.00 s' as though measured)."""
        if a != b:
            return False
        if not self._cross:
            return True
        own = getattr(self.session, "reference_is_own_recording", None)
        return bool(callable(own) and own())

    # The badge for the degenerate "both panes on the same lap" pair (see tick()).
    SAME_LAP_BADGE = "same lap"

    def _set_pane_badge(self, side: int, d: float | None, *, same_lap: bool = False) -> None:
        """Format + colour a pane Δ badge via theme.format_delta_run (the app's SINGLE Δ formatter,
        so the badge carries the same ▲/▼ non-colour direction cue as the hero readout, the charts
        and the corner table — hand-rolling the f-string here dropped it) and theme.delta_colour
        (neutral when dead-even)."""
        if same_lap:
            self.video.set_pane_badge(side, self.SAME_LAP_BADGE, None)
        else:
            self.video.set_pane_badge(side, theme.format_delta_run(d), theme.delta_colour(d))

    # ------------------------------------------------------------------ enter / exit
    def on_toggled(self, on: bool) -> None:
        """on=True enters compare (re-enter cross if _prefer_cross + reference loaded, else
        same-recording, with same-recording fallback if cross setup fails); on=False exits."""
        if on:
            if self._prefer_cross and self.session.reference_session() is not None:
                if self.enter_cross():
                    return
                # Cross re-entry failed — drop the sticky preference, fall through to same-recording.
                self._prefer_cross = False
            self.enter()
        else:
            self.exit()

    def enter(self) -> None:
        # same-recording entry: reset cross routing + drop sticky cross pref
        self._cross = False
        self._session_b = self.session
        self._prefer_cross = False
        valid = self.session.valid_lap_ids()
        if len(valid) < 2:
            return  # the toggle should be disabled, but guard anyway
        best = self.session.best_lap_id()
        # A = the lap the playhead is currently in, else the primary table selection, else best.
        a = self.session.lap_at_time(self.playback.applied_t or 0.0)
        if a is None or a not in valid:
            sel = [lid for lid in self.plots.selected_lap_ids() if lid in valid]
            a = sel[0] if sel else (best if best in valid else valid[0])
        # B = best; if A already is best, pick the next-fastest valid lap as B.
        b = best if best is not None and best in valid else None
        if b is None or b == a:
            others = sorted((lid for lid in valid if lid != a),
                            key=self.session.lap_time)
            b = others[0] if others else a
        wa, wb = self.session.lap_window(a), self.session.lap_window(b)
        if wa is None or wb is None:
            return  # degenerate window — stay out of compare (flags above are reset, none latched)
        # Each picker lists every valid lap EXCEPT the one the other pane holds (see _picker_items).
        ids_a, labels_a = self._picker_items(valid, b)
        ids_b, labels_b = self._picker_items(valid, a)
        # Both panes are laps of this recording; source=None reuses the primary ChapterMap.
        spec_a = PaneSpec(a, wa, self._lap_caption(a), source=None,
                          choices=ids_a, choice_labels=labels_a)
        spec_b = PaneSpec(b, wb, self._lap_caption(b), source=None,
                          choices=ids_b, choice_labels=labels_b)
        self._enter(spec_a, spec_b)

    def _enter(self, spec_a: PaneSpec, spec_b: PaneSpec) -> None:
        """Shared enter tail: pin (A,B), build panes, realign S/F, drive overlay + per-pane g,
        suspend auto-follow on A. Overlay is [A,B] same-recording, [A] cross (pane B is the
        reference baseline). Callers set _cross/_session_b first."""
        a, b = spec_a.lap_id, spec_b.lap_id
        self._compare = True
        self._compare_a, self._compare_b = a, b
        self.video.set_compare(spec_a, spec_b)
        self._reset_pair_to_start()
        self.plots.set_laps([a] if self._cross else [a, b])
        self.video.set_pane_gmeter_lap(0, a)
        self.video.set_pane_gmeter_lap(1, b)
        # Freeze followed_lap on A so the per-tick edge never re-points the charts while comparing.
        self.playback.followed_lap = a
        self._compare_last_t = None  # force the next tick() to recompute for the new pair
        self._on_pair_changed()  # refresh the brake glyphs for the compared pair

    def enter_cross(self) -> bool:
        """Enter cross-recording compare: pane A = this recording's lap, pane B = the reference
        recording's lap with its own footage/telemetry. Returns False (no-op) if no reference is
        loaded or the windows are degenerate. Pane B's picker is locked to the single reference lap."""
        ref_sess = self.session.reference_session()
        ref_lap = self.session.reference_lap_id()
        if ref_sess is None or ref_lap is None:
            return False
        valid = self.session.valid_lap_ids()
        if not valid:
            return False
        best = self.session.best_lap_id()
        # Pane A = the lap the playhead is in, else the primary table selection, else best/first.
        a = self.session.lap_at_time(self.playback.applied_t or 0.0)
        if a is None or a not in valid:
            sel = [lid for lid in self.plots.selected_lap_ids() if lid in valid]
            a = sel[0] if sel else (best if best is not None and best in valid else valid[0])
        wa = self.session.lap_window(a)
        wb = ref_sess.lap_window(ref_lap)  # the reference lap's window on the REFERENCE clock
        if wa is None or wb is None:
            return False
        # Set the cross routing flags BEFORE _enter so the overlay + per-tick feeds use the reference.
        self._cross = True
        self._session_b = ref_sess
        self._prefer_cross = True  # so a later toggle off/on re-enters cross
        cap_b = self._cross_caption_b(ref_sess, ref_lap)
        # Pane B's spec is the only cross-vs-same difference: reference footage + picker locked to the
        # single reference lap. No cross-exclusion here — the two lap ids index DIFFERENT recordings,
        # so an equal id is not the same lap (see _picker_items / _other_lap).
        ids_a, labels_a = self._picker_items(valid, None)
        spec_a = PaneSpec(a, wa, self._lap_caption(a), source=None,
                          choices=ids_a, choice_labels=labels_a)
        spec_b = PaneSpec(ref_lap, wb, cap_b,
                          source=(ref_sess.chapters or ref_sess.video_path),
                          choices=[ref_lap],
                          choice_labels=[self._ref_choice_label(ref_sess, ref_lap)])
        self._enter(spec_a, spec_b)
        return True

    def _cross_caption_b(self, ref_sess: Session, ref_lap: int) -> str:
        """Pane B CAPTION for cross compare (the pane's tooltip): reference label + lap + time."""
        label = self.session.reference_label() or "reference"
        return f"{label} · lap {lap_label(ref_lap)} · {fmt_time(ref_sess.lap_time(ref_lap))}"

    def _ref_choice_label(self, ref_sess: Session, ref_lap: int) -> str:
        """Pane B's PICKER ITEM for cross compare: the lap, in the same shape every other picker
        item takes. It used to be the caption above — recording label and all — and a recording
        name is unbounded: `GX010099 · Tuesday evening · lap 1 · 0:37.955` measured 281 px inside a
        238 px combo, so the item elided and what it elided away was the LAP TIME, in the one
        control that exists to carry it. The recording is a property of the PANE, not of the lap it
        holds: it stays in the pane's tooltip (set_caption) and in the window's permanent reference
        status chip, which is the same call the charts header's `Δ TO REF` label already makes."""
        return f"lap {lap_label(ref_lap)}  ({fmt_time(ref_sess.lap_time(ref_lap))})"

    def exit(self) -> None:
        self._cross = False
        self._session_b = self.session
        self._compare = False
        self._compare_a = self._compare_b = None
        self.video.exit_compare()
        if self.map is not None:
            self.map.clear_ghost()  # the ghost exists only while compare is on
        # Re-enable auto-follow and restore the table-driven chart selection.
        self.playback.followed_lap = None
        ids = self.table.selected_lap_ids()
        if ids:
            self.plots.set_laps(ids)
        else:
            self._select_default()
        self._on_pair_changed()  # restore the single-lap brake glyphs

    def on_pane_repoint(self, side: int, lap_id: int) -> None:
        """A pane's lap picker repointed that side to `lap_id`: re-seed its lap window + caption,
        re-seek it to the new lap start, and refresh the chart overlay + g scope. The OTHER pane is
        untouched. Drives the [A,B] pair that feeds the charts + the per-tick Δ badges."""
        if not self._compare:
            return
        if self._cross and side != 0:
            # cross — pane B is locked to the reference lap, ignore its single-entry repoint
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
        # Fresh PaneSpec for the repointed side; reseed_pane leaves its media source as is.
        ids, labels = self._picker_items(valid, self._other_lap(side))
        self.video.reseed_pane(side, PaneSpec(
            lap_id, window, self._lap_caption(lap_id),
            choices=ids, choice_labels=labels))
        self.video.set_pane_gmeter_lap(side, lap_id)
        # The OTHER pane's picker still lists the lap this side just took — refresh it too, or the
        # next pick over there lands both panes on the same lap (which the exclusion exists to stop).
        self._refresh_other_choices(side)
        # realign the whole pair at S/F (see _reset_pair_to_start)
        self._reset_pair_to_start()
        # Refresh the chart overlay ([A] cross, [A,B] same-recording); freeze auto-follow on A.
        if self._cross:
            self.plots.set_laps([self._compare_a])
            self.playback.followed_lap = self._compare_a
        elif self._compare_a is not None and self._compare_b is not None:
            self.plots.set_laps([self._compare_a, self._compare_b])
            self.playback.followed_lap = self._compare_a
        self._compare_last_t = None  # force the next tick() to recompute
        self._on_pair_changed()  # refresh the brake glyphs for the new pair

    # ------------------------------------------------------------------ picker contents
    def _other_lap(self, side: int) -> int | None:
        """The lap id the OTHER pane holds, or None when it cannot collide with this pane's picker:
        in a cross-recording compare the two ids index DIFFERENT recordings, so an equal id is a
        different lap and excluding it would silently drop a legitimate choice."""
        if self._cross:
            return None
        return self._compare_b if side == 0 else self._compare_a

    def _picker_items(self, valid: list[int], exclude: int | None) -> tuple[list[int], list[str]]:
        """One pane's picker contents (ids + parallel labels): every valid lap EXCEPT `exclude`,
        the lap the other pane already holds. Comparing a lap with itself is a null comparison —
        two identical pickers, a "Δ +0.00 s" on both badges and a chart overlay carrying the same
        series twice — so it is never offered rather than silently allowed."""
        ids = [lid for lid in valid if lid != exclude]
        return ids, self._lap_choice_labels(ids)

    def _refresh_other_choices(self, side: int) -> None:
        """After `side` was repointed, re-list the OTHER pane's picker so it drops the newly-taken
        lap and re-offers the one this side just left. Same lap, window and caption over there — only
        the choices move, and set_lap_choices selects without emitting, so no repoint bounces back."""
        other = 1 - side
        lap = self._compare_a if other == 0 else self._compare_b
        if self._cross or lap is None:
            return  # cross: pane B is locked to the reference lap, pane A excludes nothing
        window = self.session.lap_window(lap)
        if window is None:
            return
        ids, labels = self._picker_items(self.session.valid_lap_ids(), self._other_lap(other))
        self.video.reseed_pane(other, PaneSpec(
            lap, window, self._lap_caption(lap), choices=ids, choice_labels=labels))

    # ------------------------------------------------------------------ pane S/F realign
    def _seek_pane_to_lap_start(self, side: int, lap_id: int) -> None:
        """Seek a pane to LAP_SEEK_NUDGE_S into its lap (keeps the ms-quantized pos inside the lap);
        pane B resolves against session_b."""
        sess = self.session if side == 0 else self._session_b
        window = sess.lap_window(lap_id)
        if window is not None:
            self.video.seek_pane(side, window[0] + theme.LAP_SEEK_NUDGE_S)

    def _reset_pair_to_start(self) -> None:
        """Re-seek both panes to lap S/F paused so they roll together on next Play.

        IMPORTANT: pause only the PLAYING pane — a never-played pane → StoppedState, whose play()
        restarts at 0 and discards the seek."""
        a, b = self._compare_a, self._compare_b
        if a is None or b is None:
            return
        self.video.pause_if_playing()
        self._seek_pane_to_lap_start(0, a)
        self._seek_pane_to_lap_start(1, b)

    # ------------------------------------------------------------------ pane captions
    def _lap_caption(self, lap_id: int) -> str:
        """Per-pane caption "lap N · m:ss.mmm" for a lap id, marking the best lap with a ★, so the
        user can confirm which lap is loaded in each pane without opening the picker."""
        star = " ★" if lap_id == self.session.best_lap_id() else ""
        return f"lap {lap_label(lap_id)} · {fmt_time(self.session.lap_time(lap_id))}{star}"

    def _lap_choice_labels(self, lap_ids: list[int]) -> list[str]:
        """Picker item labels "lap N  (m:ss.mmm)" (★ on the best lap) so picking the right lap
        doesn't require guessing. Parallel to `lap_ids`; computed once per (re)seed, not per tick."""
        best = self.session.best_lap_id()
        return [f"lap {lap_label(lid)}  ({fmt_time(self.session.lap_time(lid))})"
                f"{'  ★' if lid == best else ''}" for lid in lap_ids]
