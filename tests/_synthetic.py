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
import numpy as np

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
