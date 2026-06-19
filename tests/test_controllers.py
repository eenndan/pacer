"""Direct unit tests for the two extracted StudioWindow collaborators (no full window / event loop).

The N2 refactor pulled the two heaviest behavioural clusters out of the StudioWindow
god-controller into injected, Qt-light objects:
  * studio.scrub_controller.ScrubController — the lap-scoped plot-cursor scrub: per-tick coalesced
    seek + cursor/marker/readout apply, the map marker-drag drain, and (in compare mode) the
    distance-locked SECONDARY-pane fan-out.
  * studio.compare_controller.CompareController — dual-lap compare: the per-tick "Δ vs other"
    badges + secondary g, the (t_a,t_b) early-out, and the scrub-bypass that drives the badges/g
    from the drag's own clamped targets while a distance-locked scrub is in flight.

The payoff of the extraction is exactly this: we can now drive that logic DIRECTLY — a real (bare)
Session for the genuine delta/odometer math (its per-lap odometer cache seeded via the shared
tests/_synthetic factory) + tiny fake view recorders for the side-effects — and assert:
  * a coalesced scrub issues at most ONE primary seek per tick and applies the cursor/marker/readout
    exactly once to the latest dragged time (not once per mouse-move);
  * the map marker-drag drain seeks once per tick;
  * a distance-locked compare scrub fans a SECOND coalesced seek to the secondary pane at the same
    track position (its own lap's global time) and bypasses the compare early-out;
  * compare per-tick produces the right +behind/−ahead badges + secondary g for two laps, and
    early-outs (zero work) when neither pane moved.
Run: python tests/test_controllers.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Session imports Qt (via the view layer) even though the controllers are Qt-free; offscreen so
# there's no display. The controllers themselves never construct a widget.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from _synthetic import bare_session, odometer  # noqa: E402

from studio.compare_controller import CompareController  # noqa: E402
from studio.scrub_controller import ScrubController  # noqa: E402


# --------------------------------------------------------------------- fakes / fixtures
def _make_session():
    """A bare Session (tests/_synthetic factory) with TWO laps cached so the REAL delta_between /
    media_time_at_plot_x / odometer math runs; the factory also seeds the valid_lap_ids /
    best_lap_id memos so the real methods serve them. The remaining pacer-backed lookups the
    controllers touch (lap_window / lap_at_time / lap_time / g_at_time / index_at_time /
    delta_at_lap / tv) are monkey-patched off the cached arrays — the same pattern as
    test_compare/test_studio_features.

    Lap A (id 3): slower, 12.0 s span, 520 m. Lap B (id 7, the best): faster, 11.0 s, 508 m.
    Returns (session, lap_a, lap_b)."""
    a, b = 3, 7
    ta, da = odometer(121, 0.1, 100.0, 520.0)  # ~12.0 s, slow-fast-slow default profile
    tb, db = odometer(111, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)  # ~11.0 s
    s = bare_session({a: (ta, da), b: (tb, db)}, best=b, valid=[a, b])

    windows = {a: (float(ta[0]), float(ta[-1])), b: (float(tb[0]), float(tb[-1]))}
    s.lap_window = lambda lid: windows.get(lid)
    s.lap_time = lambda lid: float(s._dist_cache[lid][0][-1] - s._dist_cache[lid][0][0])
    # lap_at_time: which lap's window contains t (None outside both — lead-in / between laps).
    def _lap_at_time(t):
        for lid, (w0, w1) in windows.items():
            if w0 <= t <= w1:
                return lid
        return None
    s.lap_at_time = _lap_at_time
    # A synthetic g signal: deterministic from t so the secondary-g assertions are exact.
    s.g_at_time = lambda t: (round(0.1 * t, 6), round(-0.2 * t, 6), round(0.3 * t, 6))
    # index_at_time / tv only feed the readout speed; map t to a stable index + a speed table.
    s.tv = np.linspace(40.0, 120.0, 256)
    s.index_at_time = lambda t: int(t) % len(s.tv)
    # delta_at_lap drives the always-on diff box; reuse the real vs-best delta math.
    s.delta_at_lap = lambda lid, t: (s.delta_between(lid, b, t) if lid is not None else None)
    return s, a, b


class _FakeVideo:
    """Records every side-effect the controllers push at the video layer, and serves per-pane
    times + the g-meter-visible flag back. `pane_times[side]` is the live position the compare
    early-out / non-scrub path reads; the tests set it to simulate playback/seek landing."""

    def __init__(self):
        self.seeks = []                 # video.seek(t) — PRIMARY pane
        self.pane_seeks = []            # video.seek_pane(side, t) — (side, t)
        self.badges = []                # video.set_pane_badge(side, text, colour)
        self.pane_g = []                # video.set_pane_g(side, g)
        self.g = []                     # video.set_g(g) — primary (readout path)
        self.gmeter_lap = []            # video.set_gmeter_lap(lap_id)
        self.readouts = []              # video.set_readout(text)
        self.play_calls = 0
        self.pause_calls = 0
        self.pause_if_playing_calls = 0
        self.playing = False
        self._gmeter_visible = True
        self.pane_times = {0: 0.0, 1: 0.0}

    # scrub side
    def seek(self, t):
        self.seeks.append(t)

    def seek_pane(self, side, t):
        self.pane_seeks.append((side, t))

    def is_playing(self):
        return self.playing

    def play(self):
        self.play_calls += 1
        self.playing = True

    def pause(self):
        self.pause_calls += 1
        self.playing = False

    def pause_if_playing(self):
        self.pause_if_playing_calls += 1
        self.playing = False

    # compare side
    def current_pane_time(self, side):
        return self.pane_times[side]

    def is_gmeter_visible(self):
        return self._gmeter_visible

    def set_pane_g(self, side, g):
        self.pane_g.append((side, g))

    def set_g(self, g):
        self.g.append(g)

    def set_gmeter_lap(self, lap_id):
        self.gmeter_lap.append(lap_id)

    def set_pane_badge(self, side, text, colour):
        self.badges.append((side, text, colour))

    # enter/exit-compare plumbing (recorded so we know the orchestration ran, values unused here)
    def set_compare(self, *a, **k):
        self.compare_args = (a, k)

    def exit_compare(self):
        self.exited = True

    def reseed_pane(self, *a, **k):
        self.reseed_args = (a, k)

    def set_pane_gmeter_lap(self, side, lap_id):
        self.pane_g.append(("scope", side, lap_id))

    def set_compare_enabled(self, on):
        self.compare_enabled = on


class _FakePlots:
    """Records cursor placements + chart lap-set changes; serves the dragging flag + selection."""

    def __init__(self):
        self.placed = []        # set_playhead_time(t, force=...)
        self.lap_sets = []      # set_laps(ids)
        self.dragging = False
        self._selected = []

    def set_playhead_time(self, t, *, force=False):
        self.placed.append((t, force))

    def set_laps(self, ids):
        self.lap_sets.append(list(ids))

    def is_dragging(self):
        return self.dragging

    def selected_lap_ids(self):
        return list(self._selected)


class _FakeMap:
    """Records marker placements + serves a queued marker-drag seek (drained once per tick), and
    records the compare GHOST drives: `ghost_idx` (same-recording, set_ghost_index on the primary
    trace) vs `ghost_pos` (cross-recording, set_ghost_pos on the reference overlay line) — the two
    are mutually exclusive, so a cross-recording tick must NEVER touch ghost_idx and vice versa."""

    def __init__(self):
        self.placed = []        # set_playhead_time(t)
        self._marker_seek = None
        self.ghost_idx = []     # set_ghost_index(i) — same-recording ghost (primary-trace index)
        self.ghost_pos = []     # set_ghost_pos(x, y) — cross-recording ghost (overlay-line point)
        self.cleared_ghost = 0  # clear_ghost() count (compare exit)

    def set_playhead_time(self, t):
        self.placed.append(t)

    def take_marker_seek(self):
        t, self._marker_seek = self._marker_seek, None
        return t

    def set_ghost_index(self, i):
        self.ghost_idx.append(i)

    def set_ghost_pos(self, x, y):
        self.ghost_pos.append((x, y))

    def clear_ghost(self):
        self.cleared_ghost += 1


class _FakeTable:
    def __init__(self, selected=None):
        self._selected = list(selected or [])
        self.current = []

    def selected_lap_ids(self):
        return list(self._selected)

    def set_current_lap(self, lap_id):
        self.current.append(lap_id)


def _lap_times(s, lid):
    """The lap's media-clock times array from the seeded (times, dists, elapsed) cache entry."""
    return s._dist_cache[lid][0]


def _make_controllers(session, *, table_selected=None):
    """Wire a ScrubController + CompareController over fake views + the bare session, exactly as
    StudioWindow does (mutually referential), with a captured `_applied_t` and a recorder for the
    injected apply_readout / set_followed_lap / select_default hooks."""
    video, plots, map_view, table = _FakeVideo(), _FakePlots(), _FakeMap(), _FakeTable(table_selected)
    state = {"applied_t": None, "followed_lap": None, "readout_calls": [], "select_default": 0}

    def apply_readout(t):
        state["readout_calls"].append(t)

    def set_followed_lap(lid):
        state["followed_lap"] = lid

    def select_default():
        state["select_default"] += 1

    compare = CompareController(
        session, video, plots, table,
        set_followed_lap=set_followed_lap,
        select_default=select_default,
        get_applied_t=lambda: state["applied_t"],
        map_view=map_view,  # the F4 ghost collaborator (same- and cross-recording)
    )
    scrub = ScrubController(
        session, video, plots, map_view,
        apply_readout=apply_readout,
        get_applied_t=lambda: state["applied_t"],
        set_applied_t=lambda t: state.__setitem__("applied_t", t),
    )
    compare.set_scrub(scrub)
    scrub.set_compare(compare)
    return scrub, compare, video, plots, map_view, table, state


# ===================================================================== ScrubController
def test_scrub_coalesces_one_seek_and_one_apply_per_tick():
    """A fast drag (many moves) before a single tick collapses to exactly ONE primary seek + ONE
    cursor/marker/readout apply at the LATEST dragged time — the coalescing the 30 Hz tick buys.
    No compare, so no secondary seek; the playhead/readout are driven by the one clamped truth."""
    s, a, _b = _make_session()
    scrub, _compare, video, plots, map_view, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0  # the playhead sits inside lap A
    scrub.on_started()
    assert scrub.is_active is False, "no target yet -> not active until the first move"
    assert video.pause_calls == 0 and video.is_playing() is False  # wasn't playing

    # Three drag moves in 'time' mode (x = seconds into lap) BEFORE any tick. Only the LAST sticks.
    for x in (1.0, 3.0, 6.5):
        scrub.on_moved(x, "time")
    assert scrub.is_active is True
    ta = _lap_times(s, a)
    expect_t = float(ta[0]) + 6.5  # 'time' mode: lap start + x, clamped (well within the 12 s lap)
    assert abs(scrub.target - expect_t) < 1e-9, (scrub.target, expect_t)

    # ONE tick: exactly one primary seek + one apply, both at the latest target. No secondary seek.
    scrub.apply_tick()
    assert video.seeks == [expect_t], video.seeks
    assert video.pane_seeks == [], "no secondary seek outside compare"
    assert plots.placed == [(expect_t, True)], plots.placed   # forced re-place, once
    assert map_view.placed == [expect_t], map_view.placed
    assert state["readout_calls"] == [expect_t], state["readout_calls"]

    # A SECOND tick with no new move does nothing (the dirty flags cleared) — zero coalesced work.
    scrub.apply_tick()
    assert video.seeks == [expect_t] and plots.placed == [(expect_t, True)]
    assert state["readout_calls"] == [expect_t]
    print("test_scrub_coalesces_one_seek_and_one_apply_per_tick OK")


def test_scrub_pauses_playing_and_resumes_on_release():
    """Grab while PLAYING pauses; release issues a final seek to the last target, seeds _applied_t,
    and resumes. Release also flushes a final view apply iff the last move never reached a tick."""
    s, a, _b = _make_session()
    scrub, _compare, video, plots, map_view, _table, state = _make_controllers(s)
    state["applied_t"] = 106.0
    video.playing = True
    scrub.on_started()
    assert video.pause_calls == 1 and video.is_playing() is False, "must pause a playing video on grab"
    scrub.on_moved(4.0, "time")
    target = scrub.target
    # Release with the move still pending (no tick ran): final seek + final view flush + resume.
    scrub.on_ended()
    assert video.seeks == [target], video.seeks
    assert abs(state["applied_t"] - target) < 1e-9, "release must seed _applied_t to the final target"
    assert plots.placed == [(target, True)] and map_view.placed == [target]
    assert state["readout_calls"] == [target]
    assert video.play_calls == 1 and video.is_playing() is True, "must resume (was playing at grab)"
    assert scrub.is_active is False, "scrub state cleared after release"
    print("test_scrub_pauses_playing_and_resumes_on_release OK")


def test_scrub_paused_grab_does_not_resume():
    """Grab while PAUSED: no pause call, and release must NOT play (we only restore prior play)."""
    s, _a, _b = _make_session()
    scrub, _compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 106.0
    video.playing = False
    scrub.on_started()
    scrub.on_moved(2.0, "time")
    scrub.on_ended()
    assert video.pause_calls == 0 and video.play_calls == 0, "paused grab must not pause/resume"
    print("test_scrub_paused_grab_does_not_resume OK")


def test_scrub_outside_lap_is_noop():
    """A grab where the playhead is NOT inside a valid lap (lead-in) scopes to None, so moves are
    no-ops: no target, no seek, the tick does nothing."""
    s, _a, _b = _make_session()
    scrub, _compare, video, plots, map_view, _table, state = _make_controllers(s)
    state["applied_t"] = 0.0  # before lap A's window (100 s) -> lap_at_time None
    scrub.on_started()
    scrub.on_moved(3.0, "time")
    assert scrub.is_active is False and scrub.target is None
    scrub.apply_tick()
    assert video.seeks == [] and plots.placed == [] and map_view.placed == []
    print("test_scrub_outside_lap_is_noop OK")


def test_marker_drag_drains_one_seek_per_tick():
    """The map marker-drag drain seeks once when a marker time is queued, and not when it isn't —
    independent of the scrub branch."""
    s, _a, _b = _make_session()
    scrub, _compare, video, _plots, map_view, _table, _state = _make_controllers(s)
    scrub.drain_marker_seek()
    assert video.seeks == [], "no queued marker seek -> no seek"
    map_view._marker_seek = 123.4
    scrub.drain_marker_seek()
    assert video.seeks == [123.4]
    scrub.drain_marker_seek()
    assert video.seeks == [123.4], "drained once; the queue is now empty"
    print("test_marker_drag_drains_one_seek_per_tick OK")


def test_compare_scrub_is_distance_locked_to_both_panes():
    """In compare mode the SAME dragged plot-x is a TRACK POSITION: the scrub converts it to each
    lap's own global media time and fans a coalesced seek to BOTH panes (primary via seek, secondary
    via seek_pane(1, ·)), parking both at the same normalized distance. Driven in 'distance' mode
    (the shared s×best_distance axis), so the two targets differ (different lap lengths/lines)."""
    s, a, b = _make_session()
    scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    # Enter compare on the pinned pair (A primary, B secondary), playhead inside A.
    state["applied_t"] = 105.0
    compare.enter()
    assert compare.active and compare.lap_a == a and compare.lap_b == b
    # enter() resets both panes to S/F (its own seek_pane calls) — clear them so we assert ONLY the
    # scrub's distance-locked fan-out below.
    video.seeks.clear()
    video.pane_seeks.clear()
    # Grab + a single distance-mode move at x = halfway on the shared (best-distance) axis.
    scrub.on_started()
    assert scrub._scrub_lap == a, "compare scrub scopes to the pinned primary lap A"
    best_d = s.best_lap_total_distance()
    x = 0.5 * (best_d or 0.0)   # halfway down the shared distance axis
    scrub.on_moved(x, "distance")
    t_a = s.media_time_at_plot_x(a, x, "distance", best_distance=best_d)
    t_b = s.media_time_at_plot_x(b, x, "distance", best_distance=best_d)
    assert t_b is not None and abs(scrub.target_b - t_b) < 1e-9
    assert abs(scrub.target - t_a) < 1e-9
    # The tick fans ONE seek to each pane (primary via seek, secondary via seek_pane(1, ·)).
    scrub.apply_tick()
    assert video.seeks == [t_a], video.seeks
    assert video.pane_seeks == [(1, t_b)], video.pane_seeks
    print("test_compare_scrub_is_distance_locked_to_both_panes OK")


# ===================================================== D1: slider/arrow distance-lock fan-out
def test_compare_fanout_seek_b_distance_locks_slider_to_pane_b():
    """D1: the global scrub slider + ←/→ arrows seek pane A only (VideoView's primary path). In
    compare mode CompareController.fanout_seek_b(t_a) distance-locks the SAME move to pane B: it
    converts pane A's new global media time to the shared-distance position and back to pane B's own
    lap's media time, then seek_pane(1, t_b). The result must match the plot-scrub distance-lock at
    the same track position (one distance-lock, two entry points)."""
    s, a, b = _make_session()
    _scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    compare.enter()
    video.pane_seeks.clear()  # drop enter()'s S/F reset so we assert ONLY the fan-out
    # Pick a primary media time partway into lap A, derive the expected pane-B time INDEPENDENTLY
    # via the shared-distance round-trip (plot-x at t_a on lap A -> media time at that x on lap B).
    ta = _lap_times(s, a)
    t_a = float(ta[len(ta) // 3])
    best_d = s.best_lap_total_distance()
    x = s.plot_x_at_media_time(a, t_a, "distance", best_distance=best_d)
    t_b_expected = s.media_time_at_plot_x(b, x, "distance", best_distance=best_d)
    compare.fanout_seek_b(t_a)
    assert video.pane_seeks == [(1, t_b_expected)], video.pane_seeks
    # The fan-out parks pane B at the SAME normalized distance as t_a — not at pane A's raw time
    # (the bug was pane B freezing / only pane A moving). Different lap lengths => different times.
    assert t_b_expected != t_a, "distance-lock must remap, not copy pane A's time"
    print(f"test_compare_fanout_seek_b_distance_locks_slider_to_pane_b OK: t_a={t_a:.3f} -> t_b={t_b_expected:.3f}")


def test_compare_fanout_seek_b_noop_outside_compare():
    """D1 guard: fanout_seek_b is a no-op when compare is off (the hook is wired once in app.py and
    self-guards), so the slider/arrows in single-video mode never touch a (non-existent) pane B."""
    s, _a, _b = _make_session()
    _scrub, compare, video, _plots, _map, _table, _state = _make_controllers(s)
    compare.fanout_seek_b(105.0)  # compare never entered
    assert video.pane_seeks == [], "fan-out must do nothing outside compare"
    print("test_compare_fanout_seek_b_noop_outside_compare OK")


# ===================================================================== CompareController
def test_compare_tick_badges_and_g_for_two_laps():
    """Compare per-tick: each pane's "Δ vs other" badge (+behind / −ahead) at that pane's own track
    position, plus the SECONDARY pane's own-lap g (the primary's g is the readout path's job). With
    A slower than B, the badges have opposite signs at matching positions."""
    s, a, b = _make_session()
    _scrub, compare, video, _plots, _map, _table, _state = _make_controllers(s)
    compare._compare = True
    compare._compare_a, compare._compare_b = a, b
    compare._compare_last_t = None  # force the first tick to apply
    ta = _lap_times(s, a)
    tb = _lap_times(s, b)
    # Park both panes mid-lap (live pane times feed the non-scrub path).
    video.pane_times = {0: float(ta[len(ta) // 2]), 1: float(tb[len(tb) // 2])}
    compare.tick()
    # Two badges set this tick: side 0 (A vs B) and side 1 (B vs A).
    sides = [side for side, _txt, _col in video.badges]
    assert sides == [0, 1], sides
    d_ab = s.delta_between(a, b, video.pane_times[0])
    d_ba = s.delta_between(b, a, video.pane_times[1])
    assert video.badges[0][1] == f"Δ {d_ab:+.2f} s", video.badges[0]
    assert video.badges[1][1] == f"Δ {d_ba:+.2f} s", video.badges[1]
    # Secondary g fed once, from the secondary pane's own time; primary g NOT touched here.
    assert video.pane_g[-1] == (1, s.g_at_time(video.pane_times[1])), video.pane_g[-1]
    assert video.g == [], "primary g is the readout path's job, not compare.tick()"
    print("test_compare_tick_badges_and_g_for_two_laps OK")


def test_compare_tick_early_out_when_neither_pane_moved():
    """The (t_a, t_b) early-out: a second tick with UNCHANGED pane times does ZERO badge/g work
    (mirrors the playback _applied_t gate) — paused/idle compare is free per tick."""
    s, a, b = _make_session()
    _scrub, compare, video, _plots, _map, _table, _state = _make_controllers(s)
    compare._compare = True
    compare._compare_a, compare._compare_b = a, b
    compare._compare_last_t = None
    ta = _lap_times(s, a)
    tb = _lap_times(s, b)
    video.pane_times = {0: float(ta[10]), 1: float(tb[10])}
    compare.tick()
    n_badges, n_g = len(video.badges), len(video.pane_g)
    assert n_badges == 2
    compare.tick()  # same pane times -> early-out
    assert len(video.badges) == n_badges, "early-out must skip the badge work"
    assert len(video.pane_g) == n_g, "early-out must skip the g work"
    # Move ONE pane -> recompute.
    video.pane_times[1] = float(tb[20])
    compare.tick()
    assert len(video.badges) == n_badges + 2, "a moved pane must recompute both badges"
    print("test_compare_tick_early_out_when_neither_pane_moved OK")


def test_compare_tick_scrub_bypass_uses_drag_targets():
    """While a distance-locked scrub is in flight the pane times LAG (coalesced seeks not yet
    landed), so compare.tick() must BYPASS the early-out and drive the badges/g from the scrub's OWN
    clamped targets (t_a = primary target, t_b = secondary target), not the stale pane times."""
    s, a, b = _make_session()
    scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    compare.enter()                       # pins (a, b), resets _compare_last_t
    # Stale pane times (pre-scrub) — the early-out WOULD freeze here if not bypassed.
    video.pane_times = {0: 100.0, 1: 300.0}
    scrub.on_started()
    best_d = s.best_lap_total_distance()
    x = 0.4 * (best_d or 0.0)
    scrub.on_moved(x, "distance")         # sets scrub.target + scrub.target_b
    assert scrub.is_active
    video.badges.clear()
    video.pane_g.clear()
    compare.tick()
    # Badges/g computed from the DRAG targets, not the stale pane times.
    t_a, t_b = scrub.target, scrub.target_b
    d_ab = s.delta_between(a, b, t_a)
    d_ba = s.delta_between(b, a, t_b)
    assert video.badges[0][1] == f"Δ {d_ab:+.2f} s", video.badges[0]
    assert video.badges[1][1] == f"Δ {d_ba:+.2f} s", video.badges[1]
    assert video.pane_g[-1] == (1, s.g_at_time(t_b)), "secondary g from the drag target, not pane time"
    # And it does NOT early-out on a second tick (still scrubbing) even with identical targets.
    n = len(video.badges)
    compare.tick()
    assert len(video.badges) == n + 2, "scrub bypass must keep applying every tick"
    print("test_compare_tick_scrub_bypass_uses_drag_targets OK")


def test_compare_enter_seeds_pair_and_suspends_autofollow():
    """enter(): seeds (A,B) = (current lap, best); resets BOTH panes to S/F PAUSED (pause_if_playing
    + a seek_pane per side); drives the chart overlay [A,B]; freezes auto-follow on A; arms the
    badge recompute. exit(): clears the pair, restores the table-driven selection, re-enables follow."""
    s, a, b = _make_session()
    _scrub, compare, video, plots, _map, table, state = _make_controllers(s, table_selected=[a])
    state["applied_t"] = 105.0  # inside lap A
    compare.on_toggled(True)
    assert compare.active and (compare.lap_a, compare.lap_b) == (a, b)
    assert video.pause_if_playing_calls == 1, "reset must pause-if-playing exactly once"
    # Both panes seeked to their lap starts (a hair INTO the lap via the nudge), primary then secondary.
    seeked_sides = [side for side, _t in video.pane_seeks]
    assert seeked_sides == [0, 1], seeked_sides
    assert plots.lap_sets[-1] == [a, b], "chart overlay = the pinned pair"
    assert state["followed_lap"] == a, "auto-follow frozen on the primary lap"
    assert compare._compare_last_t is None, "badge recompute armed for the new pair"

    compare.on_toggled(False)
    assert not compare.active and compare.lap_a is None and compare.lap_b is None
    assert state["followed_lap"] is None, "exit re-enables auto-follow (cleared follow state)"
    assert plots.lap_sets[-1] == [a], "exit restores the table-driven selection"
    print("test_compare_enter_seeds_pair_and_suspends_autofollow OK")


def test_compare_repoint_realigns_pair_and_refreezes_follow():
    """on_pane_repoint(side, lap): repoints that side, re-seeds its pane, realigns the WHOLE pair to
    S/F (pause_if_playing + both panes re-seeked), refreshes the overlay, refreezes follow on the
    (new) primary, and re-arms the badge recompute. Repointing the SECONDARY keeps A as primary."""
    s, a, b = _make_session()
    _scrub, compare, video, plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    compare.enter()
    video.pane_seeks.clear()
    video.pause_if_playing_calls = 0
    compare._compare_last_t = "sentinel"  # prove it's re-armed to None
    compare.on_pane_repoint(0, b)         # repoint the PRIMARY to lap b
    assert compare.lap_a == b and compare.lap_b == b
    assert video.pause_if_playing_calls == 1
    assert [side for side, _t in video.pane_seeks] == [0, 1], "whole pair realigned to S/F"
    assert plots.lap_sets[-1] == [b, b]
    assert state["followed_lap"] == b, "follow refrozen on the new primary"
    assert compare._compare_last_t is None
    # A repoint to a lap NOT in valid_lap_ids is ignored.
    before = (compare.lap_a, compare.lap_b)
    compare.on_pane_repoint(1, 999)
    assert (compare.lap_a, compare.lap_b) == before, "invalid repoint is a no-op"
    print("test_compare_repoint_realigns_pair_and_refreezes_follow OK")


def test_compare_tick_noop_until_pair_set():
    """tick() with no pinned pair (compare off / not entered) is a pure no-op — no badge/g work."""
    s, _a, _b = _make_session()
    _scrub, compare, video, _plots, _map, _table, _state = _make_controllers(s)
    compare.tick()
    assert video.badges == [] and video.pane_g == []
    print("test_compare_tick_noop_until_pair_set OK")


# ============================================= F7 Phase B: cross-recording video compare
def _attach_reference(primary, *, ref_lap=5, faster=0.95, overlay=True):
    """Build a SECOND bare reference Session and adopt it as the primary's cross-recording
    reference. The reference lap mirrors the primary's lap A curve but `faster`× the time (so the
    deltas are non-trivial) on its OWN clock (anchored away from 0). Returns (ref_session, ref_lap).
    Stubs only what the cross-recording compare reads: lap_window / lap_time / g_at_time on the
    reference, plus chapters/video_path as the pane-B video source marker."""
    ta = primary._dist_cache[3][0]   # lap A times (the _make_session lap A id is 3)
    da = primary._dist_cache[3][1]   # lap A dists
    r_times = (ta - ta[0]) * faster + 1000.0   # reference's own clock, anchored at 1000 s
    from tests._synthetic import seed_lap
    ref = bare_session({ref_lap: (r_times, da.copy())}, best=ref_lap, valid=[ref_lap])
    seed_lap(ref, ref_lap, r_times, da.copy())
    rwin = (float(r_times[0]), float(r_times[-1]))
    ref.lap_window = lambda lid, _w=rwin: _w
    ref.lap_time = lambda lid, _t=float(r_times[-1] - r_times[0]): _t
    # Reference g: a DIFFERENT deterministic signal from the primary's, to prove pane B routes
    # through the reference session (not self.session).
    ref.g_at_time = lambda t: (round(0.5 * t, 6), round(-0.6 * t, 6), round(0.7 * t, 6))
    # A distinct chapters marker (the pane-B video source the controller must pass through).
    ref.chapters = f"REF_CHAPTERS_{ref_lap}"
    ref.video_path = None
    # Build a cross_reference.ReferenceLap directly (avoids the pacer-backed loop-fit path) and
    # wire the primary's reference seam to it + the live reference session.
    from studio import cross_reference as xr
    overlay_xy = np.column_stack([np.linspace(0, 100, 60), np.linspace(0, 50, 60)]) if overlay else None
    primary._reference = xr.ReferenceLap(
        dist=da.copy(), speed_kmh=np.full(len(da), 50.0), elapsed=(r_times - r_times[0]),
        total_time=float(r_times[-1] - r_times[0]), source_label="friend", lap_id=ref_lap,
        overlay_xy=overlay_xy, map_fit_rms=(1.0 if overlay else None),
    )
    primary._reference_session = ref
    return ref, ref_lap


def test_cross_compare_routes_pane_b_through_reference():
    """enter_cross(): pane A is the primary lap, pane B is the reference recording's lap. The
    pane-B video SOURCE handed to set_compare is the REFERENCE's chapters (not the primary's), and
    the lap windows differ (pane B on the reference clock). Pane B's lap is locked to the reference."""
    s, a, _b = _make_session()
    ref, ref_lap = _attach_reference(s)
    _scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0  # inside lap A
    assert compare.enter_cross() is True
    assert compare.cross and compare.session_b is ref
    assert compare.lap_a == a and compare.lap_b == ref_lap
    # F8b: set_compare now takes two PaneSpecs. Pane B's spec carries the REFERENCE source + the
    # locked single-lap picker; pane A's the primary lap A.
    (spec_a, spec_b), _kwargs = video.compare_args
    assert spec_b.source == f"REF_CHAPTERS_{ref_lap}", spec_b.source
    assert spec_b.choices == [ref_lap], spec_b.choices
    assert spec_a.lap_id == a and spec_b.lap_id == ref_lap
    # Pane A window is the primary lap A's; pane B window is the reference lap's (different clock).
    assert spec_a.window == s.lap_window(a)
    assert spec_b.window == ref.lap_window(ref_lap) and spec_b.window[0] >= 1000.0, spec_b.window
    print(f"test_cross_compare_routes_pane_b_through_reference OK: pane B source={spec_b.source}")


def test_cross_compare_tick_pane_b_feeds_from_reference():
    """In cross-recording tick(): pane B's g == reference_session.g_at_time(t_b) EXACTLY (not the
    primary's g), pane B's badge == the reference-vs-primary cross-recording delta, and pane A's
    badge == the primary-vs-reference delta. The map ghost sits on the reference overlay line.

    HARDENED against the global-clock-vs-from-0 clamp bug: pane B is parked MID-lap on the
    reference's GLOBAL clock (≈ 1000 + half the lap), and the expected ghost index + badge are
    computed INDEPENDENTLY of the methods under test — by rebasing t_b to seconds-into-the-
    reference-lap and indexing the overlay ring / scaling the linear synthetic delta by hand. A
    clamp-to-finish regression (interp of a ~1000 s t_b against the from-0 reference axis) would
    drive the ghost to the LAST overlay index and the badge to the finish Δ, both of which these
    independent assertions reject. We also assert the ghost lands STRICTLY between the start and
    last overlay indices."""
    s, a, _b = _make_session()
    ref, ref_lap = _attach_reference(s)
    _scrub, compare, video, _plots, map_view, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    assert compare.enter_cross()
    # Park both panes mid-lap on their OWN clocks (pane B's clock is anchored ≈ 1000 s, not 0).
    ta = _lap_times(s, a)
    r_times = ref._dist_cache[ref_lap][0]
    t_a = float(ta[len(ta) // 2])
    t_b = float(r_times[len(r_times) // 2])
    assert t_b >= 1000.0, t_b  # the reference clock is genuinely away from 0 (the clamp trap)
    video.pane_times = {0: t_a, 1: t_b}
    compare._compare_last_t = None
    compare.tick()
    # Pane B g routes through the REFERENCE session, exactly.
    assert video.pane_g[-1] == (1, ref.g_at_time(t_b)), video.pane_g[-1]
    assert video.pane_g[-1] != (1, s.g_at_time(t_b)), "must NOT use the primary session's g"
    # Badges: pane A = primary vs reference (delta_at_lap), pane B = reference vs primary.
    d_a = s.delta_at_lap(a, t_a)
    d_b = s.reference_delta_vs_lap(a, t_b)
    badge_sides = {side: txt for side, txt, _c in video.badges}
    assert badge_sides[0] == f"Δ {d_a:+.2f} s", badge_sides[0]
    assert badge_sides[1] == f"Δ {d_b:+.2f} s", badge_sides[1]

    # --- INDEPENDENT expectations (no call to the methods under test) ---
    # The reference lap (see _attach_reference) is the primary lap A's distance curve, elapsed
    # scaled by `faster` (0.95) and anchored at 1000 s; its overlay ring is M points sampled along
    # the lap. Rebase the GLOBAL t_b to into-lap, derive s, and compute both expectations by hand.
    ref_lap_obj = s._ref
    ref_dist, ref_elapsed = ref_lap_obj.dist, ref_lap_obj.elapsed  # from-0 arrays
    win0 = ref.lap_window(ref_lap)[0]
    t_into = t_b - win0
    s_frac = float(np.interp(t_into, ref_elapsed, ref_dist)) / float(ref_dist[-1])
    xy = s.reference_overlay_xy()
    m = len(xy)
    want_idx = min(int(round(s_frac * (m - 1))), m - 1)
    # primary lap A's elapsed at the same fraction s, then reference − primary (faster=0.95 vs 1.0).
    pa_elapsed = ta - ta[0]
    pa_dist = s._dist_cache[a][1]
    prim_elapsed_at_s = float(np.interp(s_frac * float(pa_dist[-1]), pa_dist, pa_elapsed))
    want_b_delta = float(np.interp(t_into, ref_elapsed, ref_elapsed)) - prim_elapsed_at_s

    # Map ghost placed on the reference OVERLAY line (set_ghost_pos), NOT a primary-trace index.
    assert map_view.ghost_pos, "cross-recording ghost must use set_ghost_pos"
    assert not map_view.ghost_idx, "cross-recording must NOT index the primary trace"
    assert map_view.ghost_pos[-1] == (float(xy[want_idx, 0]), float(xy[want_idx, 1])), (
        map_view.ghost_pos[-1], want_idx)
    # The mid ghost must land STRICTLY inside the ring — proves it is not clamped to S/F or finish.
    assert 0 < want_idx < m - 1, (want_idx, m)
    # And the pane-B badge is the genuine MID delta, not the clamped finish delta.
    assert abs(d_b - want_b_delta) < 1e-6, (d_b, want_b_delta)
    print(f"test_cross_compare_tick_pane_b_feeds_from_reference OK: ghost idx={want_idx}/{m-1} "
          f"(mid, not clamped), paneB Δ={d_b:+.3f}s")


def test_cross_compare_disabled_without_reference():
    """enter_cross() is a no-op (returns False) when no reference Session is retained — the menu
    action is disabled in that state, but the controller guards anyway."""
    s, _a, _b = _make_session()
    _scrub, compare, _video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    assert compare.enter_cross() is False
    assert not compare.active and not compare.cross
    print("test_cross_compare_disabled_without_reference OK")


def test_d5_toggle_reenters_cross_after_off_on():
    """D5: after a CROSS-recording compare, toggling compare off then on must RE-ENTER cross — not
    drop the reference footage. Before the fix, on_toggled(True) always called enter() (same-
    recording), silently replacing pane B's reference lap with this session's best lap. Now the
    sticky `_prefer_cross` flag routes the re-toggle back through enter_cross while the reference is
    loaded, so pane B's source stays the reference recording's chapters."""
    s, a, _b = _make_session()
    ref, ref_lap = _attach_reference(s)
    _scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    assert compare.enter_cross() is True
    assert compare.cross and compare.session_b is ref
    # Toggle compare OFF (button / C key) then ON again.
    compare.on_toggled(False)
    assert not compare.active and not compare.cross
    compare.on_toggled(True)
    # Re-entered CROSS, not same-recording: pane B is still the reference (session_b + locked lap).
    assert compare.cross, "re-toggle must re-enter cross-recording compare"
    assert compare.session_b is ref, "pane B must still resolve against the reference session"
    assert (compare.lap_a, compare.lap_b) == (a, ref_lap)
    # And the pane-B video source handed to set_compare is the REFERENCE's chapters, not this session.
    (_spec_a, spec_b), _kwargs = video.compare_args
    assert spec_b.source == f"REF_CHAPTERS_{ref_lap}", spec_b.source
    print("test_d5_toggle_reenters_cross_after_off_on OK")


def test_d5_same_recording_enter_clears_prefer_cross():
    """D5: an explicit SAME-recording enter() (the user choosing local-vs-local) drops the sticky
    cross preference, so a subsequent toggle-off/on stays same-recording even with a reference still
    loaded. The flag is direction-correct: cross sets it, same-recording clears it."""
    s, a, b = _make_session()
    ref, _ref_lap = _attach_reference(s)
    _scrub, compare, _video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    assert compare.enter_cross() is True  # sets _prefer_cross
    compare.exit()
    compare.enter()                       # explicit same-recording -> clears _prefer_cross
    assert not compare.cross and compare.session_b is s
    # Now off/on must STAY same-recording (a reference is still loaded, but the user chose local).
    compare.on_toggled(False)
    compare.on_toggled(True)
    assert not compare.cross, "after an explicit same-recording enter, re-toggle stays same-recording"
    assert (compare.lap_a, compare.lap_b) == (a, b)
    print("test_d5_same_recording_enter_clears_prefer_cross OK")


def test_d5_clear_prefer_cross_drops_stickiness():
    """D5: clear_prefer_cross() (the app calls it when the reference is CLEARED) drops the sticky
    cross preference, so a later toggle enters same-recording compare — there is no reference to
    compare against. Also: with the reference gone, on_toggled(True) can't re-enter cross even if the
    flag were stale (the reference_session() guard in on_toggled)."""
    s, a, b = _make_session()
    _ref, _ref_lap = _attach_reference(s)
    _scrub, compare, _video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    assert compare.enter_cross() is True
    compare.exit()
    compare.clear_prefer_cross()          # reference cleared
    compare.on_toggled(True)
    assert not compare.cross, "after clear_prefer_cross, re-toggle is same-recording"
    assert (compare.lap_a, compare.lap_b) == (a, b)
    print("test_d5_clear_prefer_cross_drops_stickiness OK")


def test_same_recording_compare_is_byte_identical_session_b():
    """Regression guard: the same-recording compare (enter()) must route pane B through
    self.session — `session_b is self.session` and `cross` is False — so its feeds are unchanged
    from main. Even with a reference loaded, enter() (the same-recording toggle) stays local."""
    s, a, b = _make_session()
    _attach_reference(s)  # a reference is loaded, but the SAME-recording toggle ignores it
    _scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    compare.enter()  # the existing same-recording compare path
    assert compare.session_b is s, "same-recording compare must route pane B through self.session"
    assert compare.cross is False
    assert (compare.lap_a, compare.lap_b) == (a, b), "same-recording pins two LOCAL laps"
    # Pane B's spec source is None (reuse the primary recording's source) — byte-identical to main.
    (spec_a, spec_b), _kwargs = video.compare_args
    assert spec_b.source is None, spec_b.source
    assert spec_a.lap_id == a and spec_b.lap_id == b
    # tick() uses delta_between (the same-recording path), not the cross-recording helpers.
    ta, tb = _lap_times(s, a), _lap_times(s, b)
    video.pane_times = {0: float(ta[len(ta) // 2]), 1: float(tb[len(tb) // 2])}
    compare._compare_last_t = None
    compare.tick()
    d_ab = s.delta_between(a, b, video.pane_times[0])
    d_ba = s.delta_between(b, a, video.pane_times[1])
    assert video.badges[0][1] == f"Δ {d_ab:+.2f} s"
    assert video.badges[1][1] == f"Δ {d_ba:+.2f} s"
    assert video.pane_g[-1] == (1, s.g_at_time(video.pane_times[1])), "pane B g from self.session"
    print("test_same_recording_compare_is_byte_identical_session_b OK")


def test_enter_builds_two_panespecs_same_recording():
    """F8b: same-recording enter() builds two PaneSpecs and calls set_compare(spec_a, spec_b). Both
    specs carry source=None (the primary recording), both pickers list the SAME valid laps, and the
    per-side lap/window/caption are pane A's lap A and pane B's lap B respectively — the byte-for-byte
    data the old 11-positional call spread, now bundled per side."""
    s, a, b = _make_session()
    _scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    compare.enter()
    (spec_a, spec_b), kwargs = video.compare_args
    assert kwargs == {}, "set_compare is positional (two specs), no leftover keyword spread"
    assert (spec_a.lap_id, spec_b.lap_id) == (a, b)
    assert spec_a.window == s.lap_window(a) and spec_b.window == s.lap_window(b)
    assert spec_a.source is None and spec_b.source is None, "same-recording -> both panes primary src"
    # Both pickers list the session's valid laps, with the shared per-lap labels.
    valid = s.valid_lap_ids()
    labels = compare._lap_choice_labels(valid)
    assert spec_a.choices == valid and spec_b.choices == valid
    assert spec_a.choice_labels == labels and spec_b.choice_labels == labels
    # Captions are the per-lap "lap N · time" text (★ on best) — A's and B's, distinctly.
    assert spec_a.caption == compare._lap_caption(a) and spec_b.caption == compare._lap_caption(b)
    print("test_enter_builds_two_panespecs_same_recording OK")


def test_enter_cross_builds_locked_reference_panespec():
    """F8b: enter_cross() builds the SAME shaped pair, differing ONLY in pane B's spec — its source
    is the reference recording's chapters and its picker is LOCKED to the single reference lap (one
    choice, the cross caption). Pane A's spec is unchanged from the same-recording case. This is the
    'cross-vs-same is just how spec_b was built' contract, asserted on the specs themselves."""
    s, a, _b = _make_session()
    ref, ref_lap = _attach_reference(s)
    _scrub, compare, video, _plots, _map, _table, state = _make_controllers(s)
    state["applied_t"] = 105.0
    assert compare.enter_cross() is True
    (spec_a, spec_b), _kwargs = video.compare_args
    # Pane A: this session's lap A, primary source, full valid-lap picker (same as same-recording).
    assert spec_a.lap_id == a and spec_a.source is None
    assert spec_a.choices == s.valid_lap_ids()
    # Pane B: the reference recording — reference source, reference window, LOCKED single-lap picker.
    assert spec_b.lap_id == ref_lap
    assert spec_b.source == f"REF_CHAPTERS_{ref_lap}", spec_b.source
    assert spec_b.window == ref.lap_window(ref_lap)
    assert spec_b.choices == [ref_lap], "pane B picker is locked to the single reference lap"
    assert spec_b.choice_labels == [spec_b.caption], "the one locked entry shows the cross caption"
    print("test_enter_cross_builds_locked_reference_panespec OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} CONTROLLER TESTS PASSED")
