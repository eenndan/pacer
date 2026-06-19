"""Pure-Python pins for the F2 Δ-engine dedup: ONE `LapCurve` + `project()` primitive that the
whole delta family (delta_at_lap / delta_between / delta_at_time / reference_delta_vs_lap, and the
sector-guide reference branch) now shares, with the local best lap and the cross-recording
REFERENCE collapsed into the SAME `LapCurve` baseline type.

No pacer, no telemetry file: every Session is the bare `tests/_synthetic` factory build with its
per-lap caches seeded directly. The math is checked from FIRST PRINCIPLES on a deterministic
synthetic session (a constant-step time axis over a non-linear sin² odometer, so distance↔time is
a real interpolation), plus the structural invariants the refactor must preserve:
  * `LapCurve.fraction_at_time` / `elapsed_at_time` / `elapsed_at_fraction` + `project()` equal the
    closed-form values they replace;
  * `delta_at_lap(best, t) == 0` along the best lap (the flat-zero self-baseline);
  * `delta_between(a, b, finish_a) == −delta_between(b, a, finish_b)` (antisymmetry at the aligned
    finish), and both equal the laptime difference;
  * `media_time_at_plot_x(plot_x_at_media_time(t)) ≈ t` (the scrub round-trip) in both modes;
  * `delta_at_lap` against the local best equals `delta_between(lap, best, t)` (the two re-expressed
    paths agree), and against a cross-recording REFERENCE equals `delta_between(lap, ref_as_lap, t)`
    — i.e. the reference baseline and a same-shaped local lap flow through ONE `project()`.

Run:  python tests/test_delta_engine.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Session import pulls in Qt (player_pane etc.); offscreen so there's no display needed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from studio.session import LapCurve, project  # noqa: E402
from tests._synthetic import bare_session, odometer, seed_cols  # noqa: E402

TOL = 1e-9


# ----------------------------------------------------------------- the value object + primitive
def test_lapcurve_primitives_match_closed_form():
    """LapCurve's three primitives + project() reproduce the open-coded interp arithmetic exactly."""
    times, dists = odometer(140, 0.1, 50.0, 600.0)  # t0=50, span=13.9s, 600m, non-linear odometer
    elapsed = times - times[0]
    c = LapCurve(dist=dists, elapsed=elapsed, times=times, speed=np.empty(0))

    assert abs(c.total - float(dists[-1])) < TOL
    # A spread of media times inside + on + outside the window (np.interp clamps the edges).
    for t in [40.0, 50.0, 53.3, 57.77, float(times[-1]), float(times[-1]) + 9.0]:
        s_ref = float(np.interp(t, times, dists)) / float(dists[-1])
        e_ref = float(np.interp(t, times, elapsed))
        assert abs(c.fraction_at_time(t) - s_ref) < TOL
        assert abs(c.elapsed_at_time(t) - e_ref) < TOL
    # elapsed_at_fraction == project(s, c) == interp(s*total, dist, elapsed), byte-for-byte.
    for s in np.linspace(0.0, 1.0, 23):
        s = float(s)
        ref = float(np.interp(s * float(dists[-1]), dists, elapsed))
        assert c.elapsed_at_fraction(s) == ref      # EXACT (same float ops)
        assert project(s, c) == ref                 # the free primitive is just baseline.eaf
    print("test_lapcurve_primitives_match_closed_form OK")


def test_lapcurve_built_from_session_cache_matches_open_form():
    """`Session._lap_curve` / `baseline_curve` build from the existing cache and the resulting
    delta_at_lap equals the closed-form normalized-distance delta computed by hand."""
    lap, best = 4, 1
    tl, dl = odometer(130, 0.1, 0.0, 540.0)                      # the lap being driven
    tb, db = odometer(120, 0.1, 200.0, 560.0,                    # a faster, slightly longer best
                      lambda u: 1.4 + 0.6 * np.sin(u) ** 2)
    s = bare_session({lap: (tl, dl), best: (tb, db)}, best=best, valid=[lap, best])

    el, eb = tl - tl[0], tb - tb[0]
    bt = float(db[-1])
    for t in np.linspace(float(tl[0]), float(tl[-1]), 17):
        t = float(t)
        s_frac = float(np.interp(t, tl, dl)) / float(dl[-1])
        elapsed_lap = float(np.interp(t, tl, el))
        expected = elapsed_lap - float(np.interp(s_frac * bt, db, eb))
        got = s.delta_at_lap(lap, t)
        assert got is not None and abs(got - expected) < TOL, (got, expected, t)
    print("test_lapcurve_built_from_session_cache_matches_open_form OK")


# ----------------------------------------------------------------- the structural invariants
def test_delta_at_lap_along_best_is_zero():
    """delta_at_lap(best, t) == 0 at every t inside the best lap (the flat-zero self-baseline)."""
    best = 2
    tb, db = odometer(160, 0.1, 0.0, 700.0)
    s = bare_session({best: (tb, db)}, best=best, valid=[best])
    # lap_at_time isn't needed (we pass the lap id straight in), but delta_at_lap resolves the
    # baseline via best_lap_id() — seeded — so a single-lap session baselines on itself.
    for t in np.linspace(float(tb[0]), float(tb[-1]), 41):
        d = s.delta_at_lap(best, float(t))
        assert d is not None and abs(d) < 1e-7, (d, t)  # exactly 0 up to interp round-off
    print("test_delta_at_lap_along_best_is_zero OK")


def test_delta_between_antisymmetry_at_finish():
    """delta_between(A,B,finish_A) == −delta_between(B,A,finish_B) == lap_A_time − lap_B_time."""
    a, b = 3, 6
    ta, da = odometer(120, 0.1, 100.0, 520.0)                    # 11.9 s
    tb, db = odometer(110, 0.1, 300.0, 508.0,                    # 10.9 s, different line/length
                      lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s = bare_session({a: (ta, da), b: (tb, db)}, best=b, valid=[a, b])
    a_time = float(ta[-1] - ta[0])
    b_time = float(tb[-1] - tb[0])

    d_ab = s.delta_between(a, b, float(ta[-1]))  # A vs B at A's finish (s=1)
    d_ba = s.delta_between(b, a, float(tb[-1]))  # B vs A at B's finish (s=1)
    assert abs(d_ab - (a_time - b_time)) < 1e-6, (d_ab, a_time - b_time)
    assert abs(d_ba - (b_time - a_time)) < 1e-6, (d_ba, b_time - a_time)
    assert abs(d_ab + d_ba) < 1e-6, (d_ab, d_ba)  # antisymmetry at the aligned finish
    # Self-delta is flat zero everywhere.
    for t in np.linspace(float(ta[0]), float(ta[-1]), 21):
        assert abs(s.delta_between(a, a, float(t))) < 1e-9
    print(f"test_delta_between_antisymmetry_at_finish OK: Δ_AB={d_ab:+.4f}s == −Δ_BA")


def test_delta_at_lap_equals_delta_between_vs_best():
    """The two re-expressed paths agree: delta_at_lap(lap, t) (best baseline) ==
    delta_between(lap, best, t) at the same media time."""
    lap, best = 5, 0
    tl, dl = odometer(130, 0.1, 0.0, 540.0)
    tb, db = odometer(120, 0.1, 0.0, 560.0, lambda u: 1.4 + 0.6 * np.sin(u) ** 2)
    s = bare_session({lap: (tl, dl), best: (tb, db)}, best=best, valid=[lap, best])
    for t in np.linspace(float(tl[0]), float(tl[-1]), 25):
        t = float(t)
        a = s.delta_at_lap(lap, t)
        b = s.delta_between(lap, best, t)
        assert a is not None and b is not None and abs(a - b) < TOL, (a, b, t)
    print("test_delta_at_lap_equals_delta_between_vs_best OK")


def test_scrub_roundtrip_both_modes():
    """media_time_at_plot_x(plot_x_at_media_time(t)) ≈ t in time AND distance mode (the cursor↔
    media-time inverse pair the shared x-axis math hinges on)."""
    lap = 2
    times, dists = odometer(150, 0.1, 10.0, 800.0)
    s = bare_session({lap: (times, dists)}, best=lap, valid=[lap])
    best_dist = s.active_baseline_total_distance()  # == this lap's total (it is the best)
    for t in np.linspace(float(times[0]), float(times[-1]), 33):
        t = float(t)
        for mode in ("time", "distance"):
            x = s.plot_x_at_media_time(lap, t, mode, best_dist)
            assert x is not None
            back = s.media_time_at_plot_x(lap, x, mode, best_dist)
            assert back is not None and abs(back - t) < 1e-6, (mode, t, x, back)
    print("test_scrub_roundtrip_both_modes OK")


# ----------------------------------------------------------------- the reference baseline collapse
def _make_session(laps, *, best, valid, track="Test Track"):
    """A bare Session whose reference machinery resolves: _dist_cache + _cols_cache + the best/valid
    memos + a track name + a laps stub (delta()'s laps_count range check). Mirrors the
    test_cross_reference factory, kept local so this file stands alone."""
    s = bare_session(laps, valid=valid)
    s._best_cache = best
    for lid, (times, dists) in laps.items():
        seed_cols(s, lid, times, dists)
    s.track_name = track
    s._reference = None
    n = (max(laps) + 1) if laps else 0
    s.laps = type("L", (), {"laps_count": staticmethod(lambda n=n: n)})()
    return s


def test_reference_baseline_is_just_a_lapcurve():
    """The F2 collapse: with a cross-recording reference active, delta_at_lap(lap, t) equals
    delta_between(lap, <local lap with the reference's identical curve>, t). I.e. the reference
    baseline flows through the SAME project()/LapCurve as a local lap — no separate code path."""
    p_times, p_dists = odometer(150, 0.10, 0.0, 900.0)                 # primary lap
    primary = _make_session({2: (p_times, p_dists)}, best=2, valid=[2])

    # Reference recording: a faster lap of a DIFFERENT length (so the alignment matters).
    r_times, r_dists = odometer(140, 0.10, 500.0, 940.0,
                                lambda u: 1.2 + 0.8 * np.sin(u) ** 2)
    ref = _make_session({5: (r_times, r_dists)}, best=5, valid=[5])
    primary._reference_fit_loop = lambda: None
    ref.lap_trace_xy = lambda _lid: (np.zeros(0), np.zeros(0))         # < 10 pts -> no overlay
    assert primary.set_reference_session(ref) is None
    assert primary.has_reference()

    # active_baseline_total_distance == the reference lap's total (the baseline_curve().total).
    assert abs(primary.active_baseline_total_distance() - float(r_dists[-1])) < TOL

    # Build a control session where the reference's curve is just ANOTHER LOCAL LAP, and compare
    # delta_at_lap(ref baseline) against delta_between(lap, that-local-lap). The reference's
    # time-axis is its OWN 0-anchored elapsed (ReferenceLap.time_dist_elapsed), so the control
    # local lap must use elapsed-from-0 as its `times` to align with the reference path exactly.
    r_elapsed = r_times - r_times[0]
    control = bare_session({2: (p_times, p_dists), 9: (r_elapsed, r_dists)},
                           best=2, valid=[2, 9])

    for t in np.linspace(float(p_times[0]), float(p_times[-1]), 31):
        t = float(t)
        via_ref = primary.delta_at_lap(2, t)               # reference baseline (F7 path)
        via_local = control.delta_between(2, 9, t)          # same curve as a plain local lap
        assert via_ref is not None and via_local is not None
        assert abs(via_ref - via_local) < TOL, (via_ref, via_local, t)

    # Endpoint sanity: at the primary finish the Δ is primary_time − reference_time.
    expected = float(p_times[-1] - p_times[0]) - float(r_times[-1] - r_times[0])
    fin = primary.delta_at_lap(2, float(p_times[-1]))
    assert fin is not None and abs(fin - expected) < 1e-6, (fin, expected)
    print(f"test_reference_baseline_is_just_a_lapcurve OK: finish Δ={fin:+.4f}s")


def main():
    test_lapcurve_primitives_match_closed_form()
    test_lapcurve_built_from_session_cache_matches_open_form()
    test_delta_at_lap_along_best_is_zero()
    test_delta_between_antisymmetry_at_finish()
    test_delta_at_lap_equals_delta_between_vs_best()
    test_scrub_roundtrip_both_modes()
    test_reference_baseline_is_just_a_lapcurve()
    print("\nall test_delta_engine tests OK")


if __name__ == "__main__":
    main()
