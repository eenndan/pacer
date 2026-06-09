"""Pure-Python tests for the compare-video feature's Δ-badge math (no pacer, no telemetry file).

`Session.delta_between(lap_a, lap_b, t_in_a)` is the time delta of lap_a vs an ARBITRARY lap_b
at the same normalized track position lap_a is at time `t_in_a`. It drives the per-pane
"Δ vs other" badge in compare mode. These tests exercise it directly on synthetic cached per-lap
(times, dists) arrays (built via Session.__new__, populating _dist_cache), checking:
  * antisymmetry at the finish: delta_between(A,B, finish_A) == −delta_between(B,A, finish_B) and
    equals lap_a_time − lap_b_time;
  * a zero self-delta (delta_between(A,A,t) ≈ 0) at every point;
  * the CROSS-CHECK the design calls out: for lap_b == the global best lap, delta_between(A,best,t)
    matches the existing hardcoded-vs-best `delta_at_time(t)` (so the new method is consistent);
  * graceful None on a degenerate lap / outside-window times clamp to the lap edges.
Run: python tests/test_compare.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# PlayerPane (Qt widget) needs a QApplication; offscreen so there's no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio.player_pane import PlayerPane  # noqa: E402
from studio.session import Session  # noqa: E402


def _odometer(n, dt, t0, total_dist, profile):
    """A monotonic (times, dists) lap from a positive speed `profile` integrated to total_dist."""
    times = t0 + np.arange(n) * dt
    speed = profile(np.linspace(0.0, np.pi, n))
    cum = np.cumsum(speed)
    dists = (cum - cum[0]) / (cum[-1] - cum[0]) * total_dist
    return times, dists


def make_two_lap_session(best_is_b=True):
    """A bare Session with TWO laps cached. Lap A is slower (longer time) than lap B by design,
    with DIFFERENT total distances (different racing lines) so the normalized-distance alignment
    is exercised non-trivially. Returns (session, lap_a, lap_b)."""
    s = Session.__new__(Session)
    s._dist_cache = {}
    s._lap_cache = {}
    lap_a, lap_b = 3, 7
    # Lap A: slow-fast-slow, 120 samples @ 0.1 s = 11.9 s span, 520 m.
    ta, da = _odometer(120, 0.1, 100.0, 520.0, lambda u: 1.0 + np.sin(u) ** 2)
    # Lap B: faster overall (shorter span) and a slightly different line/length (508 m).
    tb, db = _odometer(110, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s._dist_cache[lap_a] = (ta, da)
    s._dist_cache[lap_b] = (tb, db)
    s._best = lap_b if best_is_b else lap_a
    return s, lap_a, lap_b


def test_finish_delta_equals_laptime_difference():
    """At each lap's finish (s=1) delta_between is exactly that lap's time minus the other's."""
    s, a, b = make_two_lap_session()
    ta, _ = s._dist_cache[a]
    tb, _ = s._dist_cache[b]
    a_time = float(ta[-1] - ta[0])
    b_time = float(tb[-1] - tb[0])
    d_ab = s.delta_between(a, b, float(ta[-1]))  # A vs B at A's finish
    d_ba = s.delta_between(b, a, float(tb[-1]))  # B vs A at B's finish
    assert abs(d_ab - (a_time - b_time)) < 1e-6, (d_ab, a_time - b_time)
    assert abs(d_ba - (b_time - a_time)) < 1e-6, (d_ba, b_time - a_time)
    # A is slower than B, so A-vs-B at the finish is POSITIVE (behind) and the reverse negative.
    assert d_ab > 0 and d_ba < 0
    print("test_finish_delta_equals_laptime_difference OK")


def test_self_delta_is_zero():
    """A lap compared to ITSELF is 0 at every track position (s aligns to s exactly)."""
    s, a, _ = make_two_lap_session()
    ta, _ = s._dist_cache[a]
    for t in np.linspace(ta[0], ta[-1], 25):
        d = s.delta_between(a, a, float(t))
        assert abs(d) < 1e-9, (t, d)
    print("test_self_delta_is_zero OK")


def test_cross_check_vs_delta_at_time():
    """THE design cross-check: for lap_b == the GLOBAL best lap, delta_between(A, best, t) must
    match the existing hardcoded-vs-best delta_at_time(t) at the same media time. We monkey-patch
    the two best-lap entry points (best_lap_id + lap_at_time) on the bare session so delta_at_time
    runs with no pacer, then compare across lap A."""
    s, a, b = make_two_lap_session(best_is_b=True)  # b is the best (fastest) lap
    ta, da = s._dist_cache[a]
    # delta_at_time needs: best_lap_id(), lap_at_time(t)->the lap containing t.
    s.best_lap_id = lambda: b
    s.lap_at_time = lambda t: a if ta[0] <= t <= ta[-1] else None
    pairs = []
    for t in np.linspace(ta[0] + 1e-6, ta[-1] - 1e-6, 31):
        d_new = s.delta_between(a, b, float(t))
        d_old = s.delta_at_time(float(t))
        assert d_old is not None
        assert abs(d_new - d_old) < 1e-9, (t, d_new, d_old)
        pairs.append((float(t), d_new, d_old))
    # Print a few sample numbers for the report's cross-check evidence.
    mid = pairs[len(pairs) // 2]
    print(f"  cross-check sample @ t={mid[0]:.3f}: delta_between={mid[1]:+.5f} s  "
          f"delta_at_time={mid[2]:+.5f} s  (max |diff| over 31 pts < 1e-9)")
    print("test_cross_check_vs_delta_at_time OK")


def test_outside_window_clamps_and_degenerate_none():
    """Times before/after lap_a's window clamp to its edge values (np.interp clamps), and a
    degenerate (uncached / <2-point) lap returns None rather than raising."""
    s, a, b = make_two_lap_session()
    ta, _ = s._dist_cache[a]
    # A time well before the lap start clamps to the start fraction (s=0) -> delta == −b_at_0 == 0
    # only coincidentally; assert it simply returns a finite number equal to the start-clamped val.
    d_before = s.delta_between(a, b, float(ta[0]) - 100.0)
    d_at_start = s.delta_between(a, b, float(ta[0]))
    assert d_before is not None and abs(d_before - d_at_start) < 1e-9, (d_before, d_at_start)
    d_after = s.delta_between(a, b, float(ta[-1]) + 100.0)
    d_at_finish = s.delta_between(a, b, float(ta[-1]))
    assert d_after is not None and abs(d_after - d_at_finish) < 1e-9, (d_after, d_at_finish)
    # A degenerate (<2-point) lap -> _lap_time_dist returns None -> delta_between None (no crash),
    # whether it's the primary or the "other" side of the comparison.
    degen = 11
    s._dist_cache[degen] = (np.array([ta[0]]), np.array([0.0]))
    assert s.delta_between(a, degen, float(ta[0])) is None
    assert s.delta_between(degen, b, float(ta[0])) is None
    print("test_outside_window_clamps_and_degenerate_none OK")


# --------------------------------------------------------- P2: PlayerPane lap window
class _FakePlayer:
    """Minimal stand-in for the QMediaPlayer in a PlayerPane (source=None) so the lap-window
    state machine can be driven without a real decoder: tracks play/pause + records pauses."""
    def __init__(self):
        self.playing = True
        self.pause_calls = 0
        self.positions = []

    def playbackState(self):
        from PySide6.QtMultimedia import QMediaPlayer
        return (QMediaPlayer.PlaybackState.PlayingState if self.playing
                else QMediaPlayer.PlaybackState.PausedState)

    def pause(self):
        self.playing = False
        self.pause_calls += 1

    def setPosition(self, ms):
        self.positions.append(ms)


def _pane_with_fake_player():
    """A PlayerPane with no media (so _chapters is None -> _offset()==0, global==local) and its
    QMediaPlayer swapped for a fake. Emitted positions are captured off positionChanged."""
    pane = PlayerPane(None)
    pane.player = _FakePlayer()
    emitted = []
    pane.positionChanged.connect(lambda g: emitted.append(g))
    return pane, emitted


def test_lap_window_stops_and_clamps_at_end():
    """A set lap window pauses + clamps the EMITTED position at the lap end; before the end it
    plays through untouched and never pauses."""
    pane, emitted = _pane_with_fake_player()
    pane.set_lap_window(10.0, 20.0)  # global [10, 20] s
    # Inside the window: pass through, no pause, emitted == input.
    for ms in (10_000, 12_500, 19_990):  # 10.0, 12.5, 19.99 s — all before the 20.0 end
        pane._on_position(ms)
    assert pane.player.pause_calls == 0, pane.player.pause_calls
    assert abs(emitted[-1] - 19.990) < 1e-6, emitted[-1]
    # Step to/just past the end: pause once, emit the clamped end (exactly 20.0).
    pane._on_position(20_005)  # 20.005 s — a frame past the 20.0 end
    assert pane.player.pause_calls == 1
    assert abs(emitted[-1] - 20.0) < 1e-9, emitted[-1]
    assert abs(pane.current_global_time() - 20.0) < 1e-9
    # A stray late position after the stop re-clamps and doesn't re-pause (idempotent).
    pane.player.playing = False
    pane._on_position(20_100)
    assert pane.player.pause_calls == 1
    assert abs(emitted[-1] - 20.0) < 1e-9
    print("test_lap_window_stops_and_clamps_at_end OK")


def test_lap_window_tolerance_catches_sub_ms_short_fall():
    """The end fires within _WINDOW_STOP_TOL_S so a position that lands a hair BELOW the end (ms
    quantization / a 60 fps frame just shy of it) still stops — no under-resolved overshoot."""
    pane, emitted = _pane_with_fake_player()
    pane.set_lap_window(0.0, 5.0)
    pane._on_position(4_999)  # 4.999 s — within the 2 ms tolerance of 5.0
    assert pane.player.pause_calls == 1
    assert abs(emitted[-1] - 5.0) < 1e-9
    print("test_lap_window_tolerance_catches_sub_ms_short_fall OK")


def test_clear_lap_window_restores_whole_session():
    """clear_lap_window() drops the confinement: positions beyond the old end pass through and
    nothing pauses — byte-identical to a pane that never had a window."""
    pane, emitted = _pane_with_fake_player()
    pane.set_lap_window(0.0, 5.0)
    pane.clear_lap_window()
    for ms in (4_000, 6_000, 9_000):  # well past the dropped 5.0 end
        pane._on_position(ms)
    assert pane.player.pause_calls == 0
    assert abs(emitted[-1] - 9.0) < 1e-6, emitted[-1]
    print("test_clear_lap_window_restores_whole_session OK")


def test_normal_mode_no_window_unchanged():
    """With NO window set (normal single-pane mode) _on_position is a pure local->global emit:
    never pauses, emits the input position verbatim."""
    pane, emitted = _pane_with_fake_player()
    for ms in (0, 1_000, 50_000, 120_000):
        pane._on_position(ms)
    assert pane.player.pause_calls == 0
    assert [round(e, 6) for e in emitted] == [0.0, 1.0, 50.0, 120.0]
    print("test_normal_mode_no_window_unchanged OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} COMPARE TESTS PASSED")
