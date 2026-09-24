"""The REAL loader on a recording WITH LAPS, against ground truth (B1a; board review RISK-7).

Until this file CI never ran `Session.load` on a recording that segments into laps: the one bundled
real clip (hero6) has 0 valid laps, and the golden gate's synthetic Session is seeded — no GPMF, no
`pacer.Laps` crossing interpolation, no band, 2-4 laps against lap-count gates of 5-8. The owner has
not consented to publishing his footage, so the recording here is synthetic:
`studio/dev/synth_gopro.py` writes a HERO13-shaped, two-chapter .MP4 (video trak + GoPro MET gpmd
trak: GPS9, ACCL, GYRO, GRAV, CORI) of a fictional 7-corner circuit, 14 flying laps with one slow
lap, and returns the truth it was generated from. Everything below goes through the real path:
`chapters.discover_siblings` -> `Session.load` -> gpmf-parser -> `pacer.Laps` -> the analyses.

WHAT THE TRUTH COMPARISON MEASURES, and why there are two of them (fixed seed, measured 2026-09-24):
  * GPS noise OFF, a line square across mid-straight applied through the app's own
    `apply_timing_lines_latlon`: every lap within 0.41 ms of truth. That is the GPS9 true-clock axis,
    the chapter seam (lap 7 spans it) and the crossing interpolation, with nothing else in the way —
    so the tolerance is tight and a clock, seam or interpolation defect has nowhere to hide.
  * The default recording (the clean end of a HERO13's GPS noise) at the line the app's unknown-track
    heuristic places itself: max |Δ| 23.4 ms, mean +2.6 ms. Noise dominates; the rest is the
    heuristic putting its line at the peak-speed point, where laps begin to brake, and the load-time
    position boxcar biasing a crossing made under braking (13.1 ms max on the noise-free recording).
The truth is always taken at the SAME line the app used, because a lap time is only defined by one.

Deterministic: the same seed gives the same telemetry bytes and the same lap times.

Run: python tests/test_synth_gopro.py   (~10 s; needs the pixi env's ffmpeg for the video trak)
"""
import hashlib
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import numpy as np  # noqa: E402

from studio import chapters, data_quality  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402

# Tolerances, each with the measured value it sits over (fixed seed; see the module doc).
EXACT_S = 0.002        # noise-free, mid-straight line: measured max 0.41 ms
NOISY_MAX_S = 0.050    # default noise, the app's own line: measured max 23.4 ms
NOISY_BIAS_S = 0.010   # …and its mean: measured +2.6 ms

_LOADED: dict = {}


def _load(**kw):
    """(recording, session) for `sg.generate(**kw)`, generated into a temp dir and loaded ONCE per
    configuration — through `discover_siblings`, as the app opens a chapter."""
    key = tuple(sorted(kw.items()))
    if key not in _LOADED:
        tmp = tempfile.TemporaryDirectory(prefix="synth_gopro_test_")
        rec = sg.generate(os.path.join(tmp.name, "rec"), **kw)
        paths = chapters.discover_siblings(rec.paths[0])
        assert paths == rec.paths, f"sibling discovery found {paths}, generated {rec.paths}"
        _LOADED[key] = (tmp, rec, Session.load(paths))
    return _LOADED[key][1], _LOADED[key][2]


def _residuals(rec, s, line):
    got = np.array([s.lap_time(i) for i in s.valid_lap_ids()])
    truth = rec.truth.lap_times(line)
    assert len(got) == len(truth) == rec.truth.laps, (
        f"{len(got)} valid laps, {len(truth)} true laps at that line, {rec.truth.laps} generated")
    return got, truth, got - truth


def _describe(d) -> str:
    a = np.abs(d) * 1e3
    return (f"max|Δ| {a.max():.2f} ms, mean {d.mean() * 1e3:+.2f}, σ {d.std() * 1e3:.2f}, "
            f"p50|Δ| {np.median(a):.2f}, p90|Δ| {np.percentile(a, 90):.2f}")


def test_the_real_loader_times_every_lap_on_the_gps9_clock():
    rec, s = _load()
    tq = s.timing_quality
    assert tq.clock == data_quality.GPS9_TRUECLOCK, f"clock {tq.clock}: the GPS9 axis was not built"
    assert tq.dropped_fraction < 0.01, f"{tq.dropped_fraction:.3f} of moving fixes rejected"
    assert s.track_name is None, f"the fictional circuit matched a built-in track: {s.track_name}"
    cmap = s.chapters
    assert len(cmap.chapters) == 2 and not cmap.desynced_chapters(), cmap.desynced_chapters()
    for c, n in zip(cmap.chapters, rec.truth.chapter_payloads, strict=True):
        assert abs(c.duration - n * sg.PAYLOAD_S) < 1e-6, (c.duration, n)
    got, truth, d = _residuals(rec, s, s.timing_lines_latlon()[0])
    print(f"  app's own line: {len(got)} laps vs truth — {_describe(d)}")
    assert np.abs(d).max() <= NOISY_MAX_S, f"lap times off truth: {np.round(d, 4)}"
    assert abs(d.mean()) <= NOISY_BIAS_S, f"lap times biased by {d.mean():+.4f} s"
    slow = int(np.argmax(got))
    assert slow == rec.truth.slow_lap == int(np.argmax(truth)), (slow, rec.truth.slow_lap)
    assert got[slow] > 1.08 * np.median(got), f"the slow lap is not clearly slow: {got[slow]:.3f}"


def test_noise_free_laps_are_exact_to_the_millisecond():
    rec, s = _load(gps_noise=0.0)
    mid = rec.truth.line_at(sum(rec.truth.circuit.straights[0]) / 2.0)
    assert s.apply_timing_lines_latlon(mid, [], confirmed=True), "the mid-straight line was refused"
    _, _, d = _residuals(rec, s, mid)
    print(f"  noise-free, mid-straight line: {_describe(d)}")
    assert np.abs(d).max() <= EXACT_S, f"lap times off truth on a noise-free trace: {np.round(d, 5)}"


def test_the_corners_are_the_circuits():
    rec, s = _load()
    found = s.corners.corner_list()
    want = rec.truth.circuit.corners
    print("  corners:", [(k.label, k.direction, round(k.turn_deg)) for k in found])
    assert len(found) == len(want), f"{len(found)} corners detected, the circuit has {len(want)}"
    for k, w in zip(found, want, strict=True):
        assert k.direction == (1 if w.turn_deg > 0 else -1), (k, w)
        assert abs(k.turn_deg - abs(w.turn_deg)) < 12.0, (k, w)


def test_the_stats_tiles_and_the_coaching_page_are_populated():
    _, s = _load()
    st = s.stats
    tot = st.totals()
    tiles = {
        "distance": tot.distance_m, "start clock": tot.start_clock, "end clock": tot.end_clock,
        "pace": st.pace(), "pace trend": st.pace_trend(), "race pace": st.race_pace(),
        "pace cov": st.pace_cov(), "laps within 1%": st.laps_within_pct(1.0)[0],
        "vmax": st.session_vmax(), "speed bands": st.speed_bands(),
        "lateral g bands": st.lateral_g_bands(), "g-g cloud": st.gg_cloud(),
        "grip envelope": st.gg_envelope(), "longest coast": st.longest_coast_s(),
    }
    empty = sorted(k for k, v in tiles.items() if v is None)
    assert not empty, f"Stats tiles with no value on a 14-lap session: {empty}"
    assert st.stints(), "no stint on a 14-lap session"
    for row in st.lap_stats():
        missing = [f for f in ("vmax_kmh", "avg_kmh", "vmin_kmh", "peak_lat_g", "peak_brake_g")
                   if getattr(row, f) is None]
        assert not missing, f"lap {row.idx}: {missing}"
    ops = s.coaching_opportunities()
    ranked = ops.ranked_rows()
    print(f"  coaching: {len(ops.rows)} rows, {len(ranked)} ranked "
          f"{[(r.cid, round(r.time_lost, 3)) for r in ranked]}")
    assert ops.enough and ranked, f"no ranked coaching row ({len(ops.rows)} rows)"


def test_the_imu_is_one_rigid_motion_with_the_gps():
    rec, s = _load()
    axis, cross, rot = s.gmeter_axis(), s.gmeter_cross(), s.rotation_cross()
    assert axis is not None and axis.measurable and axis.ok, axis and axis.summary()
    assert s.gmeter_source() == "accl", f"the g-meter fell back to {s.gmeter_source()}"
    assert cross is not None and cross.ok, cross and cross.summary()
    assert rot is not None and rot.ok, rot and rot.summary()
    print(f"  {axis.summary()}\n  {cross.summary()}\n  {rot.summary()}")
    assert rot.loop_exact == -1.0, rot.summary()                    # the circuit runs clockwise
    assert abs(rot.loop_ratio_gyro + 1.0) < 0.03, rot.loop_ratio_gyro
    # The planted clock offset is found (measured +0.487 s for the planted 0.46 s).
    assert rot.gps_lag_s is not None and abs(rot.gps_lag_s - rec.truth.gps_lag_s) < 0.1, rot.gps_lag_s


def test_the_same_seed_gives_the_same_recording():
    def digest(per_chapter):
        return hashlib.sha256(b"".join(b"".join(ch) for ch in per_chapter)).hexdigest()

    t1, a = sg.build()
    t2, b = sg.build()
    assert digest(a) == digest(b), "the same seed produced different telemetry"
    line = t1.line_at(100.0)
    assert np.array_equal(t1.lap_times(line), t2.lap_times(line))
    other = sg.simulate(sg.DEFAULT_SEED + 1)
    assert not np.allclose(other.lap_times(line), t1.lap_times(line)), "the seed changes nothing"


def _run_all():
    for fn in (test_the_real_loader_times_every_lap_on_the_gps9_clock,
               test_noise_free_laps_are_exact_to_the_millisecond,
               test_the_corners_are_the_circuits,
               test_the_stats_tiles_and_the_coaching_page_are_populated,
               test_the_imu_is_one_rigid_motion_with_the_gps,
               test_the_same_seed_gives_the_same_recording):
        t0 = time.time()
        fn()
        print(f"ok {fn.__name__} ({time.time() - t0:.1f} s)")


if __name__ == "__main__":
    _run_all()
