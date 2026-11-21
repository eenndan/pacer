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
    heuristic places itself: max |Δ| 16.85 ms, mean +1.8 ms. Noise dominates.
  * GPS noise OFF, at that same auto-fitted line: max |Δ| 0.80 ms. This is the line's PLACEMENT alone.
    Until X1 the heuristic took the fastest single fix — the braking point — and the load-time
    position boxcar times a lap that has begun braking there differently from one that has not:
    13.1 ms max noise-free, 23.4 ms max and +2.6 ms mean with the default noise. It now takes the
    speed held over 11 fixes (`load._heuristic_start_base`), which sits a few fixes up the straight.
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
AUTO_LINE_S = 0.003    # noise-free, the app's own line: measured max 0.80 ms (13.1 ms before X1)
NOISY_MAX_S = 0.050    # default noise, the app's own line: measured max 16.85 ms (23.4 before X1)
NOISY_BIAS_S = 0.010   # …and its mean: measured +1.8 ms (+2.6 before X1)
ENTRY_KMH = 2.0        # lap-table entry speed vs 3.6 × the true speed at the crossing: measured max
                       # 0.893 km/h (0.92 %), default noise, the app's own line
# The lap figures `pixi run verify` prints for a visitor to check, each with its line, and the only
# ones a public page may quote. EXACT_S, AUTO_LINE_S and NOISY_MAX_S leave them room to drift unseen,
# so each is held to its measurement (the first to 0.05 ms, the others to their printed digit:
# 0.8033 and 16.8488 measured); tests/test_measured_figures.py holds every page's quote to these.
PUBLISHED_NOISE_FREE_MS = 0.41   # noise-free, at a line mid-straight
PUBLISHED_AUTO_LINE_MS = 0.80    # noise-free, at the app's own line
PUBLISHED_NOISY_MS = 16.85       # with the default GPS noise, at the app's own line
_QUOTED_ON = ("README.md", "docs/ACCURACY.md", "docs/index.html", "docs/ENGINEERING.md",
              "tests/README.md")

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
    print(f"  with GPS noise, the app's own line: {len(got)} laps vs truth — {_describe(d)}")
    assert np.abs(d).max() <= NOISY_MAX_S, f"lap times off truth: {np.round(d, 4)}"
    max_ms = float(np.abs(d).max()) * 1e3        # half a printed digit: a quote is what verify prints
    assert abs(max_ms - PUBLISHED_NOISY_MS) <= 0.005, (
        f"max|Δ| with GPS noise at the app's own line is {max_ms:.3f} ms, but a page may quote "
        f"PUBLISHED_NOISY_MS {PUBLISHED_NOISY_MS} ms — re-measure and update them together")
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
    max_ms = float(np.abs(d).max()) * 1e3
    assert abs(max_ms - PUBLISHED_NOISE_FREE_MS) < 0.05, (
        f"noise-free max|Δ| is {max_ms:.3f} ms, but {', '.join(_QUOTED_ON)} quote "
        f"{PUBLISHED_NOISE_FREE_MS} ms — re-measure and update them together")


def test_the_auto_fitted_line_is_off_the_braking_point():
    """X1: noise-free, at the line the app fits ITSELF on this unknown circuit, every lap within a few
    ms of truth. What is left is the line's placement: a crossing where some laps have begun braking
    and others have not is timed differently by the load-time position boxcar. The single-fix
    peak-speed line sat exactly there (13.1 ms max); the held-speed peak does not."""
    rec, s = _load(gps_noise=0.0)
    line = s._fitted_lines[0]   # the loader's own placement (another check applies its own line)
    assert s.apply_timing_lines_latlon(line, [], confirmed=False), "the auto-fitted line was refused"
    _, _, d = _residuals(rec, s, line)
    print(f"  noise-free, the app's own line: {_describe(d)}")
    assert np.abs(d).max() <= AUTO_LINE_S, (
        f"lap times off truth at the auto-fitted line on a noise-free trace: {np.round(d, 5)} — "
        f"is the unknown-track line back on the braking point?")
    max_ms = float(np.abs(d).max()) * 1e3        # half a printed digit: the quote is what verify prints
    assert abs(max_ms - PUBLISHED_AUTO_LINE_MS) <= 0.005, (
        f"noise-free max|Δ| at the app's own line is {max_ms:.3f} ms, but README.md and "
        f"docs/ACCURACY.md quote {PUBLISHED_AUTO_LINE_MS} ms — re-measure and update them together")


# A MISSING FIX NEAR THE LINE (`studio/_signal.py` MISSING_FIX_PERIODS). The load-time boxcar counts
# fixes, not seconds, so a fix the gate rejects within its reach of a crossing moves that crossing —
# and the 0.2 s it leaves is far under the 0.35 s an interior dropout needs to raise the ⚠. Each case
# loads this recording with some fixes reported 2D (rejected by the gate, exactly as a receiver that
# loses its 3D lock is) and times it at the CLEAN load's line, so the only difference is the fixes.
REJECT_SHARE = 0.05    # of all fixes, at random: inside the dropped-fix share that raises no concern
REJECT_SEED = 3
MOVED_S = 0.020        # a lap this far off its clean-trace time was moved by the missing fixes
EDGE_GAP_S = (2.0, 0.5)   # a 1.5 s hole (it splits the smoothing run) ending 0.5 s before a crossing


def _load_rejecting(rec, bad):
    """`rec` loaded with every fix whose media time satisfies `bad(t)` reported as a 2D fix, so the
    quality gate drops it. Patches the loader's read for the one call."""
    import pacer
    from studio import load
    real = load.read_recording

    def read(paths):
        out = real(paths)
        samples = [s if not bad(t) else pacer.GPSSample(
            lat=s.lat, lon=s.lon, altitude=s.altitude, full_speed=s.full_speed,
            ground_speed=s.ground_speed, timestamp_ms=s.timestamp_ms, dop=s.dop, fix=2)
            for s, t in zip(out[0], out[2], strict=True)]
        return (samples, *out[1:])
    load.read_recording = read
    try:
        return Session.load(rec.paths)
    finally:
        load.read_recording = real


def _moved_by_rejection(bad) -> tuple[dict, set]:
    """({lap id: lap time minus the clean trace's, at the same line}, the ⚠ dropout laps)."""
    rec, _ = _load()
    clean, holed = _load_rejecting(rec, lambda t: False), _load_rejecting(rec, bad)
    line = clean.timing_lines_latlon()[0]
    for s in (clean, holed):
        assert s.apply_timing_lines_latlon(line, [], confirmed=True), "the clean line was refused"
    starts = np.array([clean.laps.start_timestamp(i) for i in range(clean.lap_count())])
    moved = {}
    for i in holed.valid_lap_ids():
        j = int(np.argmin(np.abs(starts - holed.laps.start_timestamp(i))))
        assert abs(starts[j] - holed.laps.start_timestamp(i)) < 1.0, f"lap {i} has no clean twin"
        moved[i] = holed.lap_time(i) - clean.lap_time(j)
    return moved, holed.dropout_lap_ids()


def test_scattered_missing_fixes_never_move_a_lap_that_stays_in_the_bests():
    """5 % of fixes rejected at random, each alone: on main this recording read an unflagged lap
    tens of ms off its clean time, some of them FAST — a false best the size of the timing step.
    Now every lap a missing fix moved carries the dropout ⚠, and every lap without one is timed to
    the bit as on the clean trace."""
    keep = np.random.default_rng(REJECT_SEED).random(1_000_000) >= REJECT_SHARE
    moved, dropout = _moved_by_rejection(lambda t: not keep[int(round(t * 10))])
    off = {i: round(d * 1e3, 1) for i, d in moved.items() if abs(d) > 1e-9}
    print(f"  {len(moved)} laps, {len(dropout)} ⚠; moved (ms): {off}")
    assert max(abs(d) for d in moved.values()) > MOVED_S, "no lap moved: the case has no teeth"
    unflagged = {i: ms for i, ms in off.items() if i not in dropout}
    assert not unflagged, f"laps moved by missing fixes yet still in the bests (ms): {unflagged}"


def test_a_gap_ending_just_before_the_line_flags_the_lap_after_it():
    """A 1.5 s hole ending 0.5 s before a crossing splits the smoothing run, so the fixes after it
    are averaged one-sidedly, toward the run's interior: the crossing moves. The lap holding the gap
    always had the ⚠; the lap STARTING at that crossing has no gap inside it and read clean."""
    rec, s = _load()
    k = s.valid_lap_ids()[3]
    c = s.laps.start_timestamp(k)
    moved, dropout = _moved_by_rejection(lambda t: c - EDGE_GAP_S[0] < t < c - EDGE_GAP_S[1])
    print(f"  lap {k} starts at the moved crossing: {moved[k] * 1e3:+.1f} ms; ⚠ {sorted(dropout)}")
    assert abs(moved[k]) > MOVED_S, f"the crossing did not move ({moved[k] * 1e3:+.1f} ms): no teeth"
    assert k in dropout, f"lap {k} moved {moved[k] * 1e3:+.1f} ms by a gap at its start, unflagged"


def test_the_lap_table_entry_speed_is_km_h_at_the_true_crossing():
    """The lap table's `entry` column (and laps.csv's, which reads the same row) is km/h at the
    moment the lap began: within ENTRY_KMH of 3.6 × the TRUE speed where the kart crossed the app's
    own start line. Until this test the unit factor was held by the golden fingerprint alone —
    tests/test_units.py feeds a fake row — so a dropped ×3.6 (m/s under a km/h header) failed no
    other test. Rows are paired with crossings by their lap id (lap i starts at crossing i), and
    the ids are asserted to be every generated lap, so an excluded row cannot shift the pairing."""
    rec, s = _load()
    rows = s.lap_rows()
    ids = [r["idx"] for r in rows]
    assert sorted(ids) == list(range(rec.truth.laps)), f"lap-table ids {ids}"
    cross = rec.truth.crossings(s.timing_lines_latlon()[0])
    v_true = sg._kinematics(rec.truth, cross[ids])[1]
    d = np.array([r["entry"] for r in rows]) - 3.6 * v_true
    print(f"  entry speed vs truth: max|Δ| {np.abs(d).max():.3f} km/h, mean {d.mean():+.3f}")
    assert np.abs(d).max() <= ENTRY_KMH, (
        f"entry speed off 3.6 × the true crossing speed by {np.round(d, 2)} km/h — a unit factor?")


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
    # The planted clock offset is found (planted 0.46 s; the overlay needs ~0.41, because the stamp
    # map files each fix ~50 ms early, and the reading is ~+20 ms over that — tests/test_sync_truth.py
    # holds it to a frame. It read +0.487 s until X2 centred the reference's even windows).
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


def test_a_habit_moves_only_its_corner_on_its_laps():
    """`synth_gopro.Habit` (the demo's planted C1 habit, `make_demo.DEMO_HABIT`) is opt-in and local:
    with none the kart's motion is the default's to the bit (so is the telemetry: the golden gate's
    `gopro_telemetry_sha256` pins it); with one, the kart's speed changes ONLY on the habit's laps and
    only between the timing line and the next corner — timed lap k is the generator's lap k — every
    clean lap's true time is the habit-free one, and each habit lap costs 0.3-0.7 s (0.505-0.523 s
    measured). truth.json names the habit only when there is one."""
    from studio.dev import make_demo as md

    habit, args = md.DEMO_HABIT, (md.DEMO_SEED, md.DEMO_LAPS)
    base = sg.simulate(*args, origin=md.DEMO_ORIGIN)
    none = sg.simulate(*args, origin=md.DEMO_ORIGIN, habit=None)
    for key in ("d_nodes", "t_nodes", "v_nodes"):
        assert np.array_equal(getattr(base, key), getattr(none, key)), key
    assert "habit" not in base.to_json()
    planted = sg.simulate(*args, origin=md.DEMO_ORIGIN, habit=habit)
    assert planted.to_json()["habit"]["cid"] == habit.cid
    c = planted.circuit
    line_s = sum(c.straights[0]) / 2.0
    line = planted.line_at(line_s)
    d = planted.lap_times(line) - base.lap_times(line)
    clean = [k - 1 for k in habit.clean_laps]
    others = [i for i in range(md.DEMO_LAPS) if i not in clean]
    print(f"  habit laps +{d[others].min():.3f}..+{d[others].max():.3f} s, clean laps "
          f"max|Δ| {np.abs(d[clean]).max() * 1e6:.3f} µs")
    assert np.abs(d[clean]).max() <= 1e-6, f"a clean lap moved: {np.round(d[clean], 7)}"
    assert ((d[others] >= 0.3) & (d[others] <= 0.7)).all(), f"habit laps: {np.round(d[others], 3)}"
    assert np.array_equal(base.d_nodes, planted.d_nodes)                  # one distance grid
    s_abs = planted.s_start + planted.d_nodes
    lap = np.floor(s_abs / c.length).astype(int)
    s_lap = s_abs - lap * c.length
    moved = np.abs(planted.v_nodes - base.v_nodes) > 1e-9
    assert set(lap[moved].tolist()) == {i + 1 for i in others}, sorted(set(lap[moved].tolist()))
    after = next(k for k in c.corners if k.cid == habit.cid + 1)
    assert line_s < s_lap[moved].min() and s_lap[moved].max() < after.apex_s, (
        f"the habit moved the speed from {s_lap[moved].min():.1f} m to {s_lap[moved].max():.1f} m, "
        f"outside the line ({line_s:.1f} m) .. C{after.cid} ({after.apex_s:.1f} m)")


# The demo's telemetry, every GPMF byte (`make_demo.make` writes these under its picture), measured
# on main at c171628 before the layout options existed: the shipped demo's sha256 (studio/demo.py)
# is of a file whose picture is ffmpeg's, so this is the half of it no ffmpeg build can move.
DEMO_TELEMETRY_SHA256 = "561a539dc912aa69b89091f9f7e69885b12afaa198ce5ef85be4e2bccb97fd9a"


def test_the_layout_options_leave_the_demo_and_drive_the_same_tarmac():
    """The opt-in layouts (cool/layout-reversed-synth) change nothing by default — the demo's
    telemetry is the bytes it was — and each is what it says. `reverse`: every point where it was
    (the same built-in track and line), the lap as long, the corners reversed in order and in hand,
    the IMU's heading turned with the travel. `reprofiled`: one corner rebuilt, the tarmac away from
    it where it was, a replacement that does not turn the corner's angle refused."""
    from studio.dev import make_demo as md

    _t, per = sg.build(md.DEMO_SEED, md.DEMO_LAPS, chapters=1, origin=md.DEMO_ORIGIN,
                       habit=md.DEMO_HABIT)
    digest = hashlib.sha256(b"".join(b"".join(ch) for ch in per)).hexdigest()
    assert digest == DEMO_TELEMETRY_SHA256, f"the demo's telemetry moved: {digest}"
    a, r = sg.build_circuit(), sg.build_circuit(reverse=True)
    assert r.length == a.length and r.x[0] == r.x[-1] and r.y[0] == r.y[-1]
    gap = max(float(np.min(np.hypot(a.x - x, a.y - y))) for x, y in zip(r.x, r.y, strict=True))
    assert gap < 1e-9, f"the reversed circuit leaves the tarmac by {gap} m"
    assert [k.turn_deg for k in r.corners] == [-k.turn_deg for k in reversed(a.corners)]
    mid = sum(a.straights[0]) / 2.0
    assert r.straights[0] == (0.0, a.straights[0][1]) and np.allclose(r.at(mid)[:2], a.at(mid)[:2])
    heading = np.arctan2(np.diff(r.y), np.diff(r.x))
    assert np.abs(np.angle(np.exp(1j * (heading - r.heading[:-1])))).max() < 0.02
    tight = sg.build_circuit(layout=sg.reprofiled(6, ("C", 70.0, 12.0)))
    off = [float(np.min(np.hypot(tight.x - x, tight.y - y))) for x, y in zip(a.x, a.y, strict=True)]
    moved = a.s[:-1][np.asarray(off[:-1]) > 0.3]
    c6 = a.corners[5]
    assert moved.size and np.abs(moved - c6.apex_s).max() < 40.0, \
        f"tightening C6 moved tarmac {np.abs(moved - c6.apex_s).max():.0f} m from its apex"
    try:
        sg.reprofiled(6, ("C", 60.0, 12.0))
    except ValueError:
        pass
    else:
        raise AssertionError("a replacement turning 60° of C6's 70° was accepted")
    print(f"  demo telemetry {digest[:12]}…, reversed on the tarmac to {gap:.0e} m, tightened C6 "
          f"moved {moved.size} points within {np.abs(moved - c6.apex_s).max():.0f} m of its apex")


def _run_all():
    for fn in (test_the_real_loader_times_every_lap_on_the_gps9_clock,
               test_noise_free_laps_are_exact_to_the_millisecond,
               test_the_auto_fitted_line_is_off_the_braking_point,
               test_scattered_missing_fixes_never_move_a_lap_that_stays_in_the_bests,
               test_a_gap_ending_just_before_the_line_flags_the_lap_after_it,
               test_the_lap_table_entry_speed_is_km_h_at_the_true_crossing,
               test_the_corners_are_the_circuits,
               test_the_stats_tiles_and_the_coaching_page_are_populated,
               test_the_imu_is_one_rigid_motion_with_the_gps,
               test_the_same_seed_gives_the_same_recording,
               test_a_habit_moves_only_its_corner_on_its_laps,
               test_the_layout_options_leave_the_demo_and_drive_the_same_tarmac):
        t0 = time.time()
        fn()
        print(f"ok {fn.__name__} ({time.time() - t0:.1f} s)")


if __name__ == "__main__":
    _run_all()
