"""ONE synthetic bare-Session factory for the pure-Python studio tests.

Not a test (no CTest registration) — a helper module the test_*.py files import. The studio
tests drive REAL Session math (delta/odometer/scrub conversions, nearest-in-lap, lap_at_time)
on a Session built via __new__ with its private caches seeded directly — the seeding idiom
Session explicitly supports for tests. Each file used to hand-roll its own variant, and some
seeded the legacy 2-tuple (times, dists) `_dist_cache` form, which kept a test-only upgrade
shim alive in production. This module is now the single place that knows the cache shapes:
  * `_dist_cache[lap_id] = (times, dists, elapsed)` — the CURRENT 3-tuple form
    (elapsed = times - times[0], exactly what `Session._lap_time_dist_elapsed` memoizes);
  * `_valid_cache` / `_best_cache` — the `valid_lap_ids()` / `best_lap_id()` memo slots, so
    the REAL methods serve the seeded ids without a pacer `laps` object.
Anything pacer-backed beyond that (lap_window, lap_at_time, g_at_time, ...) stays a per-test
stub at the call site — only what a test genuinely needs is faked.
"""
from types import SimpleNamespace

import numpy as np

from studio import corners, gmeter
from studio.session import Session


def odometer(n, dt, t0, total_dist, profile=None):
    """A monotonic (times, dists) lap: `times` start at t0 with step dt; `dists` integrates a
    positive speed `profile` (sampled over u ∈ [0, π]) normalized to end at total_dist. The
    default slow-fast-slow sin² profile keeps every step positive (strictly increasing
    odometer) and NON-uniform, so distance<->time is a real, non-linear interpolation — a
    constant-speed lap would make distance mode trivially equal to a scaled time mode."""
    if profile is None:
        def profile(u):
            return 1.0 + np.sin(u) ** 2
    times = t0 + np.arange(n) * dt
    speed = profile(np.linspace(0.0, np.pi, n))
    cum = np.cumsum(speed)
    dists = (cum - cum[0]) / (cum[-1] - cum[0]) * total_dist
    return times, dists


def seed_lap(session, lap_id, times, dists):
    """Seed ONE lap's arrays into `session._dist_cache`, always in the current 3-tuple
    (times, dists, elapsed) form so production never sees a legacy 2-tuple entry."""
    times = np.asarray(times, dtype=float)
    dists = np.asarray(dists, dtype=float)
    session._dist_cache[lap_id] = (times, dists, times - times[0])


def seed_cols(session, lap_id, times, dists):
    """Seed one `_cols_cache` 5-tuple (times, xs, ys, full_speed m/s, cum_distances) for a
    STRAIGHT-LINE lap along the x-axis (xs = odometer, ys = 0), so the sector-midpoint→trace
    projection geometry is consistent with the odometer the splits interpolate on. Feeds the
    paths that read the bulk `lap_columns` crossing directly (`_lap_arrays` / sector splits)."""
    times = np.asarray(times, float)
    dists = np.asarray(dists, float)
    if not hasattr(session, "_cols_cache"):  # bare Session.__new__ — the slot needs creating
        session._cols_cache = {}
    session._cols_cache[lap_id] = (
        times, dists.copy(), np.zeros_like(dists), np.gradient(dists, times), dists.copy(),
    )


def seed_trace(session, laps_arr):
    """Seed the WHOLE-RECORDING trace (`tt`/`tv`/`tx`/`ty`) by concatenating the seeded laps.

    `Session.stats` (studio/stats.py's SessionStats) is wired over these four, so a bare Session
    without them raises `AttributeError: 'Session' object has no attribute 'tt'` the moment anything
    asks for `totals()` — which is now every caller of `export_data.stats_summary` as well as the
    Stats page. `laps_arr` is the same `{lap_id: (times, dists)}` mapping fed to `bare_session`;
    the trace is laid out along +x like `seed_cols` does (ys = 0), so `path_distance` measures the
    odometer and speed is its real derivative rather than a constant.

    Concatenated in LAP-ID ORDER, which is session order for every fixture here — the totals are a
    reduction over the trace, so a shuffled one would report a duration the laps do not add up to.
    Each lap's odometer is OFFSET by the running total rather than restarting at 0: a per-lap
    odometer concatenated raw is a sawtooth, whose seam steps back several hundred metres and would
    make the trace's own speed channel negative there (and `path_distance`'s chord gate reject it).

    THE TIME AXIS IS THEN FORCED STRICTLY INCREASING, which a real trace's is by construction and
    some fixtures' hand-picked lap windows are not: `test_export_data.make_session` starts its three
    laps at t=10/25/40 s with ~12 s of samples each, so they OVERLAP, and a raw concatenation gives
    a time axis that steps backwards (`np.gradient` then divides by a zero dt and the speed channel
    comes back NaN). Sorting and keeping only strictly-later samples is the minimal repair, and it
    leaves a fixture whose laps ARE contiguous completely untouched."""
    keys = sorted(laps_arr)
    times = np.concatenate([np.asarray(laps_arr[k][0], float) for k in keys])
    chunks, offset = [], 0.0
    for k in keys:
        d = np.asarray(laps_arr[k][1], float)
        chunks.append(d + offset)
        offset += float(d[-1])
    dists = np.concatenate(chunks)
    order = np.argsort(times, kind="stable")
    times, dists = times[order], dists[order]
    keep = np.concatenate(([True], np.diff(times) > 0))
    times, dists = times[keep], dists[keep]
    session.tt = times
    session.tv = np.gradient(dists, times) * 3.6  # km/h, the unit SessionStats expects
    session.tx = dists
    session.ty = np.zeros_like(dists)


_MISSING = object()  # "no basis seed" sentinel for reset_corner_caches


def reset_corner_caches(session, *, basis=_MISSING):
    """Reset the CornerModel service's caches on a bare Session — the post-F1 replacement for
    the pre-extraction ``s._corner_cache = _UNSET; s._corner_stats_cache = {}; s._corner_bests
    = _UNSET`` seeding (those raw slots moved into studio.corner_model.CornerModel). Force a
    fresh service so the next access recomputes; optionally SEED the (corner_list, total_ref)
    basis so a test that hand-builds corners (no real curvature on a FakeLaps) gets them back
    from ``session.corners.corner_list()`` / ``.lap_corner_stats()``. The per-lap stats then come
    from the REAL projection against the seeded basis, exactly as before."""
    cm = session.corners  # the CornerModel service; lazily builds on a bare Session.__new__
    cm.invalidate()
    if basis is not _MISSING:
        cm._basis_cache = basis  # seeded basis (or None); the per-lap stats stay real-projected


def seed_corner_basis(session, spans=((200.0, 300.0), (600.0, 750.0)), total=1000.0):
    """Give a synthetic session a CORNER PARTITION: seed `reset_corner_caches`'s basis with a
    hand-built corner list on the reference odometer.

    `seed_cols` lays every lap out as a straight line along +x (ys = 0), so real curvature
    detection finds nothing — which means the whole ideal-lap family (Session.ideal_total /
    ideal_lap_elapsed / delta_to_ideal / theoretical_best, all of which are the corner/straight
    partition composite) returns None on an unseeded synthetic session. Everything downstream of
    the basis is still REAL: the per-lap projection, the drift gate, and the
    `corners.segment_times` sum assertion all run against it.

    `spans` is [(enter, exit), …] in reference-odometer metres, giving a 2N+1 partition."""
    return reset_corner_caches(session, basis=(
        [corners.Corner(cid=i + 1, enter=float(lo), exit=float(hi), apex=(lo + hi) / 2.0,
                        direction=1 if i % 2 == 0 else -1, turn_deg=90.0)
         for i, (lo, hi) in enumerate(spans)], float(total)))


def reset_driving_caches(session):
    """Reset the DrivingChannels service's caches on a bare Session — the post-F1 replacement
    for the pre-extraction ``s._driving_thresholds_cache = ...; s._brake_events_cache = {}; …``
    seeding (those raw slots moved into studio.driving_channels.DrivingChannels). Forces a fresh
    service so the next access derives the thresholds from the seeded ``s._gmeter`` + reprojects
    the per-lap channels. The thresholds slot is reset to the service's own _UNSET sentinel so
    they re-derive lazily (matching production)."""
    from studio.driving_channels import _UNSET as driving_unset
    dc = session.driving  # the DrivingChannels service; lazily builds on a bare Session.__new__
    dc._thresholds_cache = driving_unset
    dc.invalidate()


def bare_session(laps=None, *, best=None, valid=None, excluded=None):
    """A bare Session (Session.__new__ — no pacer, no telemetry file, no Qt event loop).

    laps:  optional {lap_id: (times, dists)} seeded into `_dist_cache` (see `seed_lap`) —
           feeds the real delta_between / delta_at_lap / media_time_at_plot_x / plot_x_at_-
           media_time / nearest_*_in_lap math.
    best:  optional best-lap id — seeds the `best_lap_id()` memo (`_best_cache`), which also
           drives `best_lap_total_distance()` off the seeded cache.
    valid: optional iterable of valid lap ids — seeds the `valid_lap_ids()` memo
           (`_valid_cache`).
    excluded: optional iterable of banded-out substantial lap ids — seeds the
           `excluded_lap_ids()` memo (`_excluded_cache`); defaults to none."""
    s = Session.__new__(Session)
    s._dist_cache = {}
    for lap_id, (times, dists) in (laps or {}).items():
        seed_lap(s, lap_id, times, dists)
    if best is not None:
        s._best_cache = best
    if valid is not None:
        s._valid_cache = list(valid)
    # Excluded (banded-out) laps default to none: a bare/synthetic session seeds a clean valid set,
    # and computing this lazily would touch the (often minimal) fake `laps`. Seed the memo so
    # Session.excluded_lap_ids / _rows resolve without reaching self.laps (mirrors _valid_cache).
    s._excluded_cache = list(excluded) if excluded is not None else []
    return s


# ---------------------------------------------------------------- the drift + noise fixture
# THE STADIUM FIXTURE CANNOT SEE TWO DEFECT CLASSES THIS REPO HAS ALREADY SHIPPED, and this one
# exists to see both. `test_session_services._synthetic_session` drives both of its laps round ONE
# polyline (0 % line-length drift, so the spatial warp is the identity there and every projection
# defect is invisible) at a noise-free speed. #228's one-frame-per-lap repair and #275's coast
# window both moved real D24
# numbers while re-cutting that baseline came back byte-identical.
#
# What each ingredient is for, and why it is the size it is (each one was measured to be NEEDED —
# a smaller fixture reads the reverted fix green):
#   * SPEED NOISE, on the GPS speed COLUMN, not on the g series. The coast and brake detectors
#     differentiate the lap's own `full_speed` (`driving_channels.lap_coasting_spans` ->
#     `speed_long_g`); the g-meter never reaches them. Noise on the old fixture's g series moves 0
#     leaves when COAST_SMOOTH_S is reverted to 0.10 s; the same noise on its speed column moves 6.
#     sigma = 0.16 m/s is #275's measured Doppler residual (0.139 / 0.168 m/s on the two D24
#     recordings), sampled at GPS9's 10 Hz so `speed_long_g` sees the noise it sees on a real lap.
#   * A WIDER LINE on one lap, run DN_WIDE_LINE_OFFSET_M outside the others (a parallel curve: the
#     straights are unchanged and each arc gains pi*offset), so its line-length drift is 1.0 % —
#     twice the gate, where D24 0060's lap 15 sat at 0.502 %.
#   * AN EXCURSION on that same lap: it runs DN_RUN_WIDE_M wide out of C1, so the C1-exit boundary
#     fails `corners.SPATIAL_MATCH_MAX_M` and the lap's warp has to INTERPOLATE it. Drift alone is
#     not enough: when every boundary matches, the pre-#228 per-boundary projection and the warp
#     agree at every boundary, and reverting #228 moves nothing. The failed match is the defect.
#   * A THIRD LAP on the reference line. Corner detection pools the laps' median curvature
#     (`CornerModel.basis`), so with only two laps the excursion drags the detected C1 exit along
#     with it and the match passes again. With three, the median keeps the corner where the track is.
#   * STRAIGHTS THAT LIFT AND COAST before braking, at 0.095 g — the centre of the coast band
#     (driving.COAST_DRAG_MIN, driving.BRAKE_G_FLOOR) — so there is a real coast for the noise to
#     shred, plus a 0.5 g brake and a 0.25 g exit. Lap 1 wins the C1-C2 straight (0.13 s, on a
#     higher top speed and a later lift), so the ideal lap is not simply the best lap — and the
#     segment it donates is the one whose START is its unmatched boundary: the #228 harvest case.
# The IMU channels are left noise-free and derived from the same kinematics (lateral = v^2*kappa):
# no detector under test reads them for longitudinal, and inventing an IMU sigma adds a knob that
# guards nothing here.
#
# Deterministic: seeded `numpy.random.default_rng` streams (PCG64, pinned by the pixi lock), no
# clock. `test_golden_synthetic.test_drift_noise_fixture_reaches_the_paths_it_exists_for` pins every
# property above, so the fixture cannot silently decay back into one that reads green.
DN_SPEED_SIGMA_MPS = 0.16     # #275's measured GPS Doppler speed residual
DN_DT_S = 0.10                # GPS9 fix interval
DN_WIDE_LINE_OFFSET_M = 0.383  # solved so lap 1 drifts 1.0 % against the best lap
DN_RUN_WIDE_M = 5.0           # peak excursion out of C1; > SPATIAL_MATCH_MAX_M at the C1 exit
DN_RUN_WIDE_HALF_M = 30.0     # half-length of that excursion along the lap
DN_ACCEL_G, DN_COAST_G, DN_BRAKE_G = 0.25, 0.095, 0.50
_DN_STRAIGHT_M, _DN_RADIUS_M, _DN_G = 200.0, 30.0, 9.81
_DN_FINE_N = 60001            # ~1 cm geometry grid the 10 Hz fixes are interpolated off
# (offset, corner speed m/s, per-straight (top speed m/s, coast seconds), excursion m, noise seed)
_DN_LAPS = (
    dict(offset=0.0, vc=12.5, straights=((22.0, 2.0), (22.0, 2.0)), run_wide_m=0.0, seed=11),
    dict(offset=DN_WIDE_LINE_OFFSET_M, vc=12.0, straights=((21.5, 3.5), (25.0, 0.5)),
         run_wide_m=DN_RUN_WIDE_M, seed=12),
    dict(offset=0.0, vc=12.2, straights=((21.8, 2.5), (21.8, 2.5)), run_wide_m=0.0, seed=14),
)

# -------------------------------------------------- the MEDIAN-drift variant of the same fixture
# THE FIXTURE ABOVE CANNOT SEE A COACHING-PATH DEFECT, and this one exists because of it. In
# `_DN_LAPS` the drifting lap is the SLOWEST (37.06 s vs 35.91 / 36.60), while the whole coaching
# model — `Session.coaching_opportunities` -> `coaching.summarize` — reads the MEDIAN-time lap
# (`coaching.median_lap_id`). Measured on main: `_DN_LAPS`' median lap 2 sits at 0.0000 % drift with
# `corners.lap_alignment` None, so every corner-window projection on the coaching path is the
# IDENTITY there and no change to it can move a leaf. That is exactly what happened: #289 moved
# `coaching._win` onto the gated, warped projection and moved 15 of 168,664 leaves on the D24 0060
# pair and ZERO synthetic ones, drift_noise included.
#
# THE ONLY THING THAT CHANGES HERE IS WHICH LAP IS THE MEDIAN. The three geometries are the two
# fixtures' own: same reference line, the same DN_WIDE_LINE_OFFSET_M parallel curve and the same
# DN_RUN_WIDE_M excursion out of C1, so the 1.0 % drift, the detected partition and the ONE
# unmatched boundary are the same properties `_DN_LAPS` already pins — only the speed profiles move,
# putting the drifting lap's time (36.39 s) BETWEEN the two clean laps (35.91 / 37.93 s). The wide
# lap is therefore the median while lap 0 stays the best, so the coaching median window is drifted,
# warped and interpolated at its unmatched boundary, and a defect in it moves leaves.
# `test_golden_synthetic.test_drift_median_fixture_puts_the_drift_where_coaching_reads` pins that
# ordering, so a later speed tweak cannot silently hand the median back to a clean lap.
_DM_LAPS = (
    dict(offset=0.0, vc=12.5, straights=((22.0, 2.0), (22.0, 2.0)), run_wide_m=0.0, seed=21),
    dict(offset=DN_WIDE_LINE_OFFSET_M, vc=12.3, straights=((22.0, 1.5), (23.5, 1.0)),
         run_wide_m=DN_RUN_WIDE_M, seed=22),
    dict(offset=0.0, vc=11.8, straights=((21.0, 3.5), (21.0, 3.5)), run_wide_m=0.0, seed=24),
)

# ------------------------------------------- the SUB-GATE DRIFT BAND variant, as a drift LADDER
# NEITHER FIXTURE ABOVE HAS A LAP IN THE BAND THE REMOVED DRIFT GATE GOVERNED, and this one is a
# ladder across it. `corners.NORMALIZED_DRIFT_MAX = 0.005` kept every lap at or below 0.5 %
# line-length drift on the normalized projection and warped only the rest; #300 removed it after
# measuring the longitudinal boundary residual on the laps it had skipped (D24 0060 pair: median
# 1.96 → 0.10 m; 0062: 0.90 → 0.01 m). That removal moved 0 of the golden's 24,859 leaves — not
# because it is inert, but because every lap of the two fixtures above is either exactly 0.0000 %
# drift (where the warp is the identity) or 0.995 %, already past the gate. Nothing sat in
# (0 %, 0.5 %], so a regression there passed CI in silence.
#
# A LADDER, NOT ONE RUNG. This is the third blind spot found in this fixture family — noise-free
# signals (#275), then slowest-lap-only drift (#298, whose median lap measured exactly 0.0000 %) —
# and each time the fixture was one point short of the defect. ONE in-band lap would only see a
# gate reintroduced above its own drift; three rungs at 0.118 %, 0.289 % and 0.460 % mean any
# threshold at or above the lowest moves at least one lap of this phase. They cost ONE phase, not
# three: #298 added a phase rather than laps because more laps change the pooled median curvature
# `CornerModel.basis` detects from, which would move an existing phase's leaves — that reasoning
# binds the two fixtures above, not a new session that has no baseline to preserve.
#
# HOW A LAP DRIFTS ONLY A LITTLE AND IS STILL MISALIGNED, which is the whole point: #300 measured
# line-length drift to be a WEAK predictor of odometer misalignment (r = +0.38; a lap at 0.41 %
# drift carried 14.7 m of it). Each rung runs WIDE round turn 1 by `a1` m and TIGHT round turn 2
# by `a2`, as a raised-cosine displacement along the reference line's own outward normal spanning
# exactly that turn — zero at both ends, so the straights are untouched and no heading kink is
# introduced for corner detection to trip over. A normal offset only changes length where the line
# is curved (∫n·kappa ds), so turn 1 adds ≈1.57·a1 m and turn 2 gives ≈1.57·|a2| back: the two
# nearly cancel in the TOTAL while the lap's odometer runs metres ahead of the normalized
# projection in between. Measured on the built fixture, max |warp − normalized| over the detected
# partition: 4.24 m at 0.118 % drift, 2.37 m at 0.289 %, 2.86 m at 0.460 %.
#
# Amplitudes stay under `corners.SPATIAL_MATCH_MAX_M`, so EVERY interior boundary matches
# spatially on every lap and the warp here is built from measurement end to end — deliberately the
# opposite of the two fixtures above, whose one unmatched boundary makes the warp interpolate a
# knot. The speeds keep lap 0, the undrifted reference line, the fastest, so the rungs are measured
# against a lap that is not one of them.
# `test_golden_synthetic.test_drift_band_fixture_covers_the_sub_gate_band` pins every property
# above. Negative control, measured: restoring the 0.5 % gate moves 68 of this phase's 15,451
# leaves and 0 of the 24,859 in the five phases before it.
_DB_ARC = np.pi * _DN_RADIUS_M                              # these laps run the reference line
_DB_TURN1 = _DN_STRAIGHT_M + _DB_ARC / 2.0                  # turn-1 midpoint, in arc length
_DB_TURN2 = 2 * _DN_STRAIGHT_M + _DB_ARC + _DB_ARC / 2.0    # turn-2 midpoint


def _db_rung(a1: float, a2: float) -> tuple:
    """One ladder rung as a `bumps` list: `a1` metres of outward displacement over turn 1 and `a2`
    over turn 2 (negative = the tighter line), each a raised cosine spanning exactly its own turn."""
    return ((_DB_TURN1, _DB_ARC / 2.0, a1), (_DB_TURN2, _DB_ARC / 2.0, a2))


_DB_LAPS = (
    dict(offset=0.0, vc=12.5, straights=((22.0, 2.0), (22.0, 2.0)), run_wide_m=0.0, seed=31),
    dict(offset=0.0, vc=12.2, straights=((21.6, 2.6), (21.6, 2.6)), run_wide_m=0.0, seed=32,
         bumps=_db_rung(2.8, -2.6)),   # 0.118 % drift, 4.24 m of odometer offset — the low rung
    dict(offset=0.0, vc=12.0, straights=((21.3, 3.0), (21.3, 3.0)), run_wide_m=0.0, seed=33,
         bumps=_db_rung(2.0, -1.0)),   # 0.289 %, 2.37 m — the middle of the band
    dict(offset=0.0, vc=11.8, straights=((21.0, 3.4), (21.0, 3.4)), run_wide_m=0.0, seed=34,
         bumps=_db_rung(2.6, -1.0)),   # 0.460 %, 2.86 m — just under the old 0.5 % gate
)


def _dn_loop_xy(u, offset):
    """(xs, ys) at arc length `u` round a 200 m x 30 m-radius stadium run `offset` metres OUTSIDE
    the reference line — its parallel curve, so the lap is exactly 2*pi*offset longer. CCW from the
    timing line at the start of the bottom straight; u = the full length closes the loop."""
    radius = _DN_RADIUS_M + offset
    arc = np.pi * radius
    xs = np.empty_like(u)
    ys = np.empty_like(u)
    bottom = u < _DN_STRAIGHT_M
    xs[bottom], ys[bottom] = u[bottom], -offset
    turn1 = (u >= _DN_STRAIGHT_M) & (u < _DN_STRAIGHT_M + arc)
    th = (u[turn1] - _DN_STRAIGHT_M) / radius
    xs[turn1], ys[turn1] = _DN_STRAIGHT_M + radius * np.sin(th), _DN_RADIUS_M - radius * np.cos(th)
    top = (u >= _DN_STRAIGHT_M + arc) & (u < 2 * _DN_STRAIGHT_M + arc)
    xs[top], ys[top] = _DN_STRAIGHT_M - (u[top] - _DN_STRAIGHT_M - arc), _DN_RADIUS_M + radius
    turn2 = u >= 2 * _DN_STRAIGHT_M + arc
    th = (u[turn2] - 2 * _DN_STRAIGHT_M - arc) / radius
    xs[turn2], ys[turn2] = -radius * np.sin(th), _DN_RADIUS_M + radius * np.cos(th)
    return xs, ys


def _dn_straight_speed(along, vc, vt, coast_s):
    """Speed (m/s) `along` one straight entered and left at corner speed `vc`: throttle at
    DN_ACCEL_G up to `vt`, hold, lift and coast for `coast_s` at DN_COAST_G, then brake at DN_BRAKE_G
    back to `vc` exactly at the corner entry. Constant-acceleration phases, so v^2 is linear in
    distance inside each."""
    a_acc, a_cst, a_brk = DN_ACCEL_G * _DN_G, DN_COAST_G * _DN_G, DN_BRAKE_G * _DN_G
    d_acc = (vt ** 2 - vc ** 2) / (2 * a_acc)
    v_lift = vt - a_cst * coast_s
    d_cst = (vt ** 2 - v_lift ** 2) / (2 * a_cst)
    d_brk = (v_lift ** 2 - vc ** 2) / (2 * a_brk)
    assert d_acc + d_cst + d_brk < _DN_STRAIGHT_M, "phases do not fit on the straight"
    lift_at, brake_at = _DN_STRAIGHT_M - d_brk - d_cst, _DN_STRAIGHT_M - d_brk
    v = np.full_like(along, vt)
    acc = along < d_acc
    v[acc] = np.sqrt(vc ** 2 + 2 * a_acc * along[acc])
    cst = (along >= lift_at) & (along < brake_at)
    v[cst] = np.sqrt(vt ** 2 - 2 * a_cst * (along[cst] - lift_at))
    brk = along >= brake_at
    v[brk] = np.sqrt(np.maximum(v_lift ** 2 - 2 * a_brk * (along[brk] - brake_at), vc ** 2))
    return v


def drift_noise_laps(t0: float = 100.0, specs=_DN_LAPS) -> list[dict]:
    """The drift + noise fixture's laps, contiguous on one media clock from `t0`. Each dict holds
    `cols` — the `_cols_cache` 5-tuple (times, xs, ys, full_speed m/s WITH noise, cum) exactly as
    `Session._lap_columns` serves it — and `clean_speed`, the same speed before the noise, which is
    what lets a test measure the noise it was given. See the block above for every choice.

    `specs` is the per-lap recipe list; `_DM_LAPS` builds the median-drift variant and `_DB_LAPS`
    the sub-gate drift ladder off the SAME builder (see their blocks above)."""
    laps = []
    for spec in specs:
        offset = spec["offset"]
        arc = np.pi * (_DN_RADIUS_M + offset)
        u = np.linspace(0.0, 2 * _DN_STRAIGHT_M + 2 * arc, _DN_FINE_N)
        xs, ys = _dn_loop_xy(u, offset)
        # Every lateral departure from this lap's line is a raised-cosine displacement along the
        # outward normal: `run_wide_m` is the one centred on C1's geometric exit, and `bumps` —
        # (centre, half-length, amplitude) triples — is the general form `_DB_LAPS` builds its
        # drift-band rungs out of. Summing them leaves the single-bump case arithmetically
        # untouched (0.0 + x is exact), which is what keeps _DN_LAPS / _DM_LAPS byte-identical.
        bumps = list(spec.get("bumps", ()))
        if spec["run_wide_m"]:
            bumps.append((_DN_STRAIGHT_M + arc, DN_RUN_WIDE_HALF_M, spec["run_wide_m"]))
        if bumps:
            tx, ty = np.gradient(xs), np.gradient(ys)
            norm = np.hypot(tx, ty)
            push = np.zeros_like(u)
            for centre, half, amp in bumps:
                rel = u - centre
                push = push + np.where(np.abs(rel) < half,
                                       amp * 0.5 * (1.0 + np.cos(np.pi * rel / half)), 0.0)
            xs, ys = xs + push * ty / norm, ys - push * tx / norm  # outward normal of a CCW loop
        odo = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))))
        v = np.full_like(u, spec["vc"])
        for k, (vt, coast_s) in enumerate(spec["straights"]):
            start = k * (_DN_STRAIGHT_M + arc)
            on = (u >= start) & (u < start + _DN_STRAIGHT_M)
            v[on] = _dn_straight_speed(u[on] - start, spec["vc"], vt, coast_s)
        clock = np.concatenate(([0.0], np.cumsum(np.diff(odo) / (0.5 * (v[:-1] + v[1:])))))
        tk = np.linspace(0.0, clock[-1], int(round(clock[-1] / DN_DT_S)) + 1)
        uk = np.interp(tk, clock, u)
        clean = np.interp(uk, u, v)
        noisy = clean + np.random.default_rng(spec["seed"]).normal(0.0, DN_SPEED_SIGMA_MPS, tk.size)
        times = t0 + tk
        laps.append({"cols": (times, np.interp(uk, u, xs), np.interp(uk, u, ys), noisy,
                              np.interp(uk, u, odo)),
                     "clean_speed": clean})
        t0 = float(times[-1])  # the next lap starts on this lap's finish crossing
    return laps


def drift_median_laps(t0: float = 100.0) -> list[dict]:
    """The MEDIAN-drift lap set: `drift_noise_laps`' geometry and builder, `_DM_LAPS`' speeds, so
    the drifting lap is the median-time one instead of the slowest. See that block."""
    return drift_noise_laps(t0, specs=_DM_LAPS)


def drift_band_laps(t0: float = 100.0) -> list[dict]:
    """The SUB-GATE BAND ladder: the same builder and the same reference line, with each comparison
    lap displaced over the two turns (`_DB_LAPS`) so its line-length drift lands INSIDE the
    (0 %, 0.5 %] band the removed gate governed. See that block."""
    return drift_noise_laps(t0, specs=_DB_LAPS)


def _drift_session(laps: list[dict]):
    """A bare Session over one of the drift lap sets above: three or four valid laps, lap 0 the
    best (and genuinely the fastest), GPS speed noise on all. `_DN_LAPS` / `_DM_LAPS` drift ONE lap
    by 1.0 % with one unmatched corner boundary; `_DB_LAPS` puts three laps inside the sub-gate
    band with every boundary matched.

    Beyond `_synthetic_session`'s seeding it carries a minimal pacer `laps` stand-in —
    `laps_count` / `lap_time` / `start_timestamp`, each read straight off the seeded columns, the
    same stub `test_coaching` and `test_consistency` use — so `lap_window` and `lap_time` resolve and
    the fingerprint's coaching, trend, rolling-lap and time-grid Δ leaves are REAL here instead of
    the `__unsupported__` sentinel the stadium fixture records for them. The whole-recording trace
    and a kinematic g-meter (lateral v^2*kappa, longitudinal dv/dt, 50 Hz) span all three laps."""
    s = bare_session({i: (lap["cols"][0], lap["cols"][4]) for i, lap in enumerate(laps)},
                     best=0, valid=range(len(laps)))
    s._cols_cache = {i: lap["cols"] for i, lap in enumerate(laps)}
    s._xyt_cache = {}
    s._render_cache = SimpleNamespace(invalidate=lambda: None, reference_fit_loop=lambda: None)
    s.laps = SimpleNamespace(
        laps_count=lambda: len(laps),
        lap_time=lambda i: float(laps[i]["cols"][0][-1] - laps[i]["cols"][0][0]),
        start_timestamp=lambda i: float(laps[i]["cols"][0][0]),
    )

    def joined(parts):  # consecutive laps share their crossing sample: keep it once
        return np.concatenate([parts[0]] + [p[1:] for p in parts[1:]])

    s.tt = joined([lap["cols"][0] for lap in laps])
    s.tv = joined([lap["cols"][3] for lap in laps]) * 3.6  # km/h, as Session.tv
    lat = joined([lap["clean_speed"] ** 2 * corners.lap_curvature(*lap["cols"][1:3], lap["cols"][4])
                  / _DN_G for lap in laps])
    lon = joined([np.gradient(lap["clean_speed"], lap["cols"][0]) / _DN_G for lap in laps])
    gt = np.arange(s.tt[0], s.tt[-1], 0.02)
    s._gmeter = gmeter.GMeter(times=gt, lat_g=np.interp(gt, s.tt, lat),
                              long_g=np.interp(gt, s.tt, lon), cross=None, source="accl")
    reset_corner_caches(s)
    reset_driving_caches(s)
    return s


def drift_noise_session():
    """The drift + noise session: the drifting, run-wide lap is the SLOWEST one (`_DN_LAPS`)."""
    return _drift_session(drift_noise_laps())


def drift_median_session():
    """The same session with the drift on the MEDIAN-time lap (`_DM_LAPS`) — the lap the whole
    coaching model reads, and the one `drift_noise_session` leaves undrifted."""
    return _drift_session(drift_median_laps())


def drift_band_session():
    """The drift-LADDER session (`_DB_LAPS`): four laps, lap 0 on the reference line and the
    fastest, the other three at 0.118 / 0.289 / 0.460 % line-length drift — the sub-gate band the
    two sessions above have no lap in, with every corner boundary spatially matched."""
    return _drift_session(drift_band_laps())
