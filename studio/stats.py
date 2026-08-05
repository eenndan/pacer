"""Session-level statistics (the Stats page): pure reducers + the SessionStats service.

PACER-FREE BY CONTRACT (numpy only, no Qt). Two layers, mirroring the established split
(studio/consistency.py for the pure math, studio/driving_channels.py for the DI service):

  * module functions — pure numpy reducers over plain arrays/lists, unit-tested directly;
  * SessionStats — the Session-composed service. All inputs are Session-bound callables
    (Session owns the pacer side + the g-meter), so NO method here reaches a `_`-private
    attribute of Session. Trace-level results (totals) depend only on the constant trace and
    survive a re-segment; lap-level results are projected through the segmentation and are
    dropped by invalidate() (Session.set_timing_lines), exactly like the driving channels.

Everything here is a REDUCTION of channels the app already computes and trusts — lap arrays,
the validated g-meter series, the brake/coast event lists — never a new estimate. Peak
longitudinal g reads the GPS speed-derivative (long_g_gps) when present, the same validated
signal the dial/brake channels use (the IMU forward axis is vibration-inflated)."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .consistency import sigma

# "moving" threshold, m/s — the SAME cutoff the g-meter/thresholds use for their moving
# masks (gmeter._MOVING_MS), so "time moving" and every g statistic agree on what counts
# as driving vs sitting in the pits.
MOVING_MS = 4.0
# A media-clock step longer than this is a GPS dropout / chapter seam, not a real 10 Hz
# sample interval — moving time skips such steps (conservative: a gap while moving is NOT
# counted as time on track, because nothing was measured there).
MAX_SAMPLE_GAP_S = 1.0
# Pace-trend gate: below this many clean laps a fitted slope is noise dressed as insight,
# so the trend statistic reports None and the tile shows a dash.
TREND_MIN_LAPS = 6
# "Race pace" window: the best mean of this many CONSECUTIVE clean laps — the sustained-run
# number next to the single glory lap.
RACE_PACE_N = 3
# The demonstrated combined-g envelope percentile — the SAME robust p98 convention as the
# driving channels' friction-circle envelope (driving.grip_envelope), so the dashed ring on
# the g-g plot and the per-corner grip normalisation can never disagree in spirit.
ENVELOPE_PCT = 98.0


# --------------------------------------------------------------------- value objects
@dataclass(frozen=True)
class SessionTotals:
    """Whole-recording totals (trace-level — independent of the lap segmentation)."""

    duration_s: float        # recorded span, first→last kept sample (media clock, incl. gaps)
    moving_s: float          # time with speed ≥ MOVING_MS (dropout gaps excluded)
    distance_m: float        # path length of the smoothed trace (sum of chords)
    start_clock: str | None  # local wall-clock "HH:MM" of the first kept fix (GPS9); None on GPS5
    end_clock: str | None    # …and of the last kept fix


@dataclass(frozen=True)
class LapStat:
    """One valid lap's statistics row. None = the underlying signal is absent (no g-meter),
    NOT a zero — the view renders those as a dash, never as 0."""

    idx: int                    # lap id (0-based, same as LapRow["idx"])
    time: float                 # lap time (s)
    vmax_kmh: float | None      # max full_speed on the lap
    avg_kmh: float | None       # odometer / lap time — the distance-true average
    vmin_kmh: float | None      # min full_speed on the lap — the slowest-corner speed
    peak_lat_g: float | None    # max |lateral g| (IMU lateral — the trusted axis)
    peak_brake_g: float | None  # max deceleration, reported positive (validated GPS-derived long)
    brake_s: float | None       # total time in brake events
    brake_n: int | None         # number of brake events
    coast_s: float | None       # total time coasting
    coast_frac: float | None    # coast_s / lap time


@dataclass(frozen=True)
class CornerReport:
    """One corner's whole-session statistics row (the Stats page's CORNERS table): the
    session's demonstrated best/typical/spread through the corner, the apex speeds, and
    the median grip utilization. None = not derivable (no g signal / <2 laps), never 0."""

    cid: int                        # Corner.cid (1-based, track order)
    direction: int                  # +1 left / -1 right (Corner.direction)
    n: int                          # included laps with a finite time in this corner
    best_s: float | None            # session-best time-in-corner
    median_s: float | None          # the typical lap's time-in-corner
    sigma_s: float | None           # sample σ (ddof=1; None with <2 laps)
    median_loss_s: float | None     # median − best (≥ 0): what the typical lap gives away
    apex_best_kmh: float | None     # the fastest apex speed carried through
    apex_median_kmh: float | None
    grip_median: float | None       # median per-lap grip utilization (0..~1.1); None w/o g
    score: float                    # σ × median_loss — the inconsistency weight (0.0 when
    #                                 either input is missing), same product as consistency.py


@dataclass(frozen=True)
class BrakeConsistency:
    """One corner's braking repeatability + commitment over the included laps (the BRAKING
    table). Onsets are compared in the REFERENCE odometer (each lap's onset scaled by
    ref_total/lap_total — the house normalized projection), so cross-lap σ measures the
    DRIVER's scatter, not lap-length drift. Honesty floor: at 10 Hz a ~15 m/s kart moves
    ~1.5 m per fix, so a σ at or below that is measurement quantization, not driving."""

    cid: int                        # Corner.cid (1-based, track order)
    n: int                          # laps with a matched brake event into this corner
    median_dist_m: float | None     # median onset (reference odometer)
    sigma_m: float | None           # cross-lap σ of the onset (m); None with <2 laps
    span_m: float | None            # max − min onset spread (m)
    commit_pct: float | None        # median (event peak decel / session a_max) × 100
    metres_later_med: float | None  # median metres-left-on-table (optimal − actual; + = can
    #                                 brake later). ESTIMATED, from the D4 brake-point model.


@dataclass(frozen=True)
class PhaseShare:
    """Where the session's corner time goes: the POSITIVE-part column sums of the per-corner
    median (entry, apex, exit) losses — seconds a typical lap gives away per phase. Positive
    part on purpose: this is "where time is LOST" accounting, so a phase the driver GAINS in
    (negative median) must not cancel losses elsewhere."""

    entry_s: float
    apex_s: float
    exit_s: float

    @property
    def total_s(self) -> float:
        return self.entry_s + self.apex_s + self.exit_s

    def fracs(self) -> tuple[float, float, float] | None:
        """(entry, apex, exit) as fractions of the lost total — the "you lose 61% of your
        corner time on entry" headline. None when nothing is lost (degenerate)."""
        t = self.total_s
        if t <= 0:
            return None
        return (self.entry_s / t, self.apex_s / t, self.exit_s / t)


@dataclass(frozen=True)
class PhaseReport:
    """The session phase-loss matrix: per corner (aligned to `cids`) the MEDIAN
    (entry, apex, exit) Δt-vs-best triple over the included laps (None where no lap had a
    finite triple), plus the session-wide PhaseShare (None when nothing is lost)."""

    cids: list[int]
    rows: list[tuple[float, float, float] | None]
    share: PhaseShare | None


@dataclass(frozen=True)
class PaceStats:
    """Lap-time distribution over the consistency laps (valid ∧ dropout-free)."""

    n: int                # laps in the distribution
    best: float           # min lap time (s)
    median: float         # median lap time (s)
    sigma: float | None   # sample σ (ddof=1; None with <2 laps — consistency.sigma)
    spread: float         # median − best: what the TYPICAL lap gives away (s, ≥ 0)


# --------------------------------------------------------------------- pure reducers
def moving_time_s(times, speed_ms, threshold_ms: float = MOVING_MS) -> float:
    """Seconds spent at speed ≥ `threshold_ms`. Each inter-sample interval is attributed to
    its LEADING sample's speed; intervals longer than MAX_SAMPLE_GAP_S (dropouts / chapter
    seams) are skipped — nothing was measured there, so they count as neither moving nor
    stopped (see the module constant)."""
    t = np.asarray(times, float)
    v = np.asarray(speed_ms, float)
    n = min(len(t), len(v))
    if n < 2:
        return 0.0
    dt = np.diff(t[:n])
    keep = (dt > 0) & (dt <= MAX_SAMPLE_GAP_S) & (v[: n - 1] >= threshold_ms)
    return float(np.sum(dt[keep]))


def path_distance_m(xs, ys) -> float:
    """Path length of a local-metre trace: the sum of chords between consecutive samples —
    the same convention as the core's cum_distances odometer. A dropout gap contributes its
    straight-line chord (a slight under-count of the real path, never an over-count)."""
    x = np.asarray(xs, float)
    y = np.asarray(ys, float)
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    d = np.hypot(np.diff(x[:n]), np.diff(y[:n]))
    return float(np.sum(d[np.isfinite(d)]))


def clock_hhmm(epoch_ms) -> str | None:
    """LOCAL wall-clock "HH:MM" for a GPS9 epoch-ms timestamp, or None when the stream has
    no wall clock (GPS5 reports 0 — the same sentinel session_date() checks). Local for the
    same reason as session_date: the time of day the driver actually experienced."""
    ms = int(epoch_ms)
    if ms <= 0:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M")


def pace_stats(lap_times) -> PaceStats | None:
    """The lap-time distribution summary, or None with no laps. σ via consistency.sigma
    (sample σ, ddof=1) so this can never disagree with the consistency panel."""
    a = np.asarray(list(lap_times), float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return None
    best = float(np.min(a))
    med = float(np.median(a))
    return PaceStats(n=len(a), best=best, median=med, sigma=sigma(a), spread=med - best)


def peak_g(lat_g, long_g) -> tuple[float | None, float | None]:
    """(peak |lateral| g, peak braking g) over a g series — braking is the most NEGATIVE
    longitudinal sample, reported positive, floored at 0 (an all-throttle span brakes 0 g,
    not negative). (None, None) for an empty series."""
    lat = np.asarray(lat_g, float)
    lon = np.asarray(long_g, float)
    lat = lat[np.isfinite(lat)]
    lon = lon[np.isfinite(lon)]
    if len(lat) == 0 or len(lon) == 0:
        return None, None
    return float(np.max(np.abs(lat))), float(max(0.0, -np.min(lon)))


def in_windows_mask(times, windows) -> np.ndarray:
    """Boolean mask: which of `times` fall inside ANY of the (t0, t1) media-clock `windows`
    (t0 inclusive, t1 exclusive — a lap's end instant belongs to the next lap, matching
    lap_window's (start, start + lap_time) convention)."""
    t = np.asarray(times, float)
    mask = np.zeros(len(t), dtype=bool)
    for t0, t1 in windows:
        mask |= (t >= t0) & (t < t1)
    return mask


def sector_medians(splits_by_lap: list[list[float]]) -> list[float | None]:
    """Per-sector-column MEDIAN split over the included laps — the "typical" companion to
    consistency.sector_sigmas, same column convention (widest lap defines the count, a
    column with no finite split reads None)."""
    n_cols = max((len(sp) for sp in splits_by_lap), default=0)
    out: list[float | None] = []
    for k in range(n_cols):
        vals = np.asarray([sp[k] for sp in splits_by_lap if k < len(sp)], float)
        vals = vals[np.isfinite(vals)]
        out.append(float(np.median(vals)) if len(vals) else None)
    return out


def corner_report(cids, directions, times_by_lap, apex_by_lap,
                  grip_by_lap) -> list[CornerReport]:
    """The corner-by-corner session report: one CornerReport per corner (track order).

    Each *_by_lap input is one row per included lap, aligned to `cids` (ragged rows are
    tolerated — column k reads only rows long enough). σ via consistency.sigma (ddof=1);
    score = σ × median_loss, the same both-erratic-AND-slow product the consistency
    ranking uses (rationale in studio/consistency.py) — 0.0 when either input is missing
    so an under-sampled corner never outranks a measured one."""

    def column(rows, k):
        vals = np.asarray([r[k] for r in rows if k < len(r)], float)
        return vals[np.isfinite(vals)]

    out: list[CornerReport] = []
    for k, (cid, direction) in enumerate(zip(cids, directions, strict=True)):
        times = column(times_by_lap, k)
        apex = column(apex_by_lap, k)
        grip = column(grip_by_lap, k)
        n = len(times)
        best = float(np.min(times)) if n else None
        med = float(np.median(times)) if n else None
        sig = sigma(times)
        loss = med - best if n else None
        out.append(CornerReport(
            cid=int(cid), direction=int(direction), n=n,
            best_s=best, median_s=med, sigma_s=sig, median_loss_s=loss,
            apex_best_kmh=float(np.max(apex)) if len(apex) else None,
            apex_median_kmh=float(np.median(apex)) if len(apex) else None,
            grip_median=float(np.median(grip)) if len(grip) else None,
            score=(sig * loss) if sig is not None and loss is not None else 0.0,
        ))
    return out


def brake_consistency(cids, rows_by_lap) -> list[BrakeConsistency]:
    """Aggregate per-lap braking rows into per-corner repeatability + commitment stats.

    `rows_by_lap`: one dict per included lap, cid → (onset_ref_m, commit_frac | None,
    metres_later | None) — a corner absent from a lap's dict simply had no matched brake
    event there (an unbraked or undetected pass; it lowers n, it does not fake a 0)."""
    out: list[BrakeConsistency] = []
    for cid in cids:
        vals = [r[cid] for r in rows_by_lap if cid in r]
        if not vals:
            out.append(BrakeConsistency(cid=int(cid), n=0, median_dist_m=None, sigma_m=None,
                                        span_m=None, commit_pct=None, metres_later_med=None))
            continue
        onsets = np.asarray([v[0] for v in vals], float)
        commits = [v[1] for v in vals if v[1] is not None]
        laters = [v[2] for v in vals if v[2] is not None]
        out.append(BrakeConsistency(
            cid=int(cid), n=len(vals),
            median_dist_m=float(np.median(onsets)),
            sigma_m=sigma(onsets),
            span_m=float(np.max(onsets) - np.min(onsets)),
            commit_pct=float(np.median(commits)) * 100.0 if commits else None,
            metres_later_med=float(np.median(laters)) if laters else None,
        ))
    return out


def phase_matrix(cids, triples_by_lap) -> PhaseReport:
    """Aggregate per-lap per-corner (entry, apex, exit) Δt-vs-best triples into the session
    phase-loss matrix: per corner the MEDIAN triple (element-wise, over laps with a fully
    finite triple; ragged rows tolerated), plus the positive-part PhaseShare (see the
    dataclass for why positive-part). The per-lap triples come from the SAME drift-gated
    coaching.corner_phase_losses decomposition the coaching reasons use."""
    rows: list[tuple[float, float, float] | None] = []
    e_sum = a_sum = x_sum = 0.0
    for k in range(len(cids)):
        tri = np.asarray([row[k] for row in triples_by_lap if k < len(row)], float)
        if len(tri):
            tri = tri[np.all(np.isfinite(tri), axis=1)]
        if len(tri) == 0:
            rows.append(None)
            continue
        med = np.median(tri, axis=0)
        rows.append((float(med[0]), float(med[1]), float(med[2])))
        e_sum += max(0.0, float(med[0]))
        a_sum += max(0.0, float(med[1]))
        x_sum += max(0.0, float(med[2]))
    share = PhaseShare(e_sum, a_sum, x_sum) if (e_sum + a_sum + x_sum) > 0 else None
    return PhaseReport(cids=list(cids), rows=rows, share=share)


def theil_sen_slope(values) -> float | None:
    """Robust trend: the MEDIAN of all pairwise slopes (Theil–Sen), in units per index step
    (here: seconds per lap). Outlier-immune — one traffic lap can't fake a trend the way it
    drags a least-squares fit. None with fewer than 2 finite values. O(n²) pairs is nothing
    at session lap counts."""
    a = np.asarray(list(values), float)
    a = a[np.isfinite(a)]
    n = len(a)
    if n < 2:
        return None
    i, j = np.triu_indices(n, k=1)
    return float(np.median((a[j] - a[i]) / (j - i)))


def best_consecutive_mean(values, n: int = RACE_PACE_N) -> float | None:
    """The best (lowest) mean over `n` CONSECUTIVE values — "race pace": the best sustained
    n-lap run, the honest companion to the single best lap. Windows containing a non-finite
    value are skipped (NaN propagates through the window sum); None when no full window
    exists."""
    a = np.asarray(list(values), float)
    if len(a) < n:
        return None
    means = np.convolve(a, np.ones(n) / n, mode="valid")  # NaN poisons its windows only
    if not np.any(np.isfinite(means)):
        return None
    return float(np.nanmin(means))


def within_pct_of_best(values, pct: float) -> int:
    """How many values sit within `pct` percent of the best (minimum) — the "banked pace"
    count (the best itself counts). 0 for an empty/all-NaN input."""
    a = np.asarray(list(values), float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return 0
    return int(np.sum(a <= float(np.min(a)) * (1.0 + pct / 100.0)))


def cov_pct(values) -> float | None:
    """Coefficient of variation as a percent: sample σ / median × 100 — the one-number,
    scale-free consistency rating (comparable across tracks/lap lengths, unlike raw σ).
    σ via consistency.sigma (ddof=1); None with <2 finite values or a degenerate median."""
    a = np.asarray(list(values), float)
    a = a[np.isfinite(a)]
    s = sigma(a)
    if s is None:
        return None
    med = float(np.median(a))
    if med <= 0:
        return None
    return s / med * 100.0


def envelope_g(lat_g, long_g, pct: float = ENVELOPE_PCT) -> float | None:
    """The demonstrated combined-g envelope: the `pct`th percentile of hypot(lat, long) —
    robust to lone spikes (the same p98 convention as the driving channels' grip envelope).
    None on an empty series."""
    lat = np.asarray(lat_g, float)
    lon = np.asarray(long_g, float)
    m = min(len(lat), len(lon))
    if m == 0:
        return None
    combined = np.hypot(lat[:m], lon[:m])
    combined = combined[np.isfinite(combined)]
    if len(combined) == 0:
        return None
    return float(np.percentile(combined, pct))


# --------------------------------------------------------------------- the service
class SessionStats:
    """Session statistics over Session-bound primitives (see the module docstring).

    `gmeter` returns the live GMeter (built after construction, so it must be a callable);
    `trace_times` / `trace_speed_kmh` / `trace_xy` return the full smoothed trace (Session.tt /
    .tv / (.tx, .ty)); `wall_clock_ms` the (first, last) kept-fix GPS9 epoch timestamps;
    `valid_lap_ids` / `consistency_lap_ids` the memoized lap sets; `lap_time` / `lap_arrays` /
    `lap_window` the per-lap fetches; `brake_events` / `coast_spans` the driving-channel
    event lists (already cached per lap by DrivingChannels)."""

    def __init__(self, *,
                 gmeter: Callable[[], object],
                 trace_times: Callable[[], np.ndarray],
                 trace_speed_kmh: Callable[[], np.ndarray],
                 trace_xy: Callable[[], tuple[np.ndarray, np.ndarray]],
                 wall_clock_ms: Callable[[], tuple[int, int]],
                 valid_lap_ids: Callable[[], list[int]],
                 consistency_lap_ids: Callable[[], list[int]],
                 lap_time: Callable[[int], float],
                 lap_arrays: Callable[[int], tuple],
                 lap_window: Callable[[int], tuple[float, float] | None],
                 brake_events: Callable[[int], list],
                 coast_spans: Callable[[int], list]):
        self._gmeter = gmeter
        self._trace_times = trace_times
        self._trace_speed_kmh = trace_speed_kmh
        self._trace_xy = trace_xy
        self._wall_clock_ms = wall_clock_ms
        self._valid_lap_ids = valid_lap_ids
        self._consistency_lap_ids = consistency_lap_ids
        self._lap_time = lap_time
        self._lap_arrays = lap_arrays
        self._lap_window = lap_window
        self._brake_events = brake_events
        self._coast_spans = coast_spans
        # totals depend only on the constant trace → computed once, survives re-segments.
        self._totals_cache: SessionTotals | None = None
        # lap-level results are projected through the segmentation → dropped by invalidate().
        self._lap_stats_cache: list[LapStat] | None = None
        self._gg_cache: tuple[np.ndarray, np.ndarray] | None = None

    def invalidate(self) -> None:
        """Drop the segmentation-derived caches on re-segment (Session.set_timing_lines);
        the trace totals are unchanged by a timing-line edit and are kept."""
        self._lap_stats_cache = None
        self._gg_cache = None

    # ------------------------------------------------------------------ trace level
    def totals(self) -> SessionTotals:
        """Whole-recording totals; cached (the trace never changes for a loaded session)."""
        if self._totals_cache is not None:
            return self._totals_cache
        t = np.asarray(self._trace_times(), float)
        v = np.asarray(self._trace_speed_kmh(), float) / 3.6
        xs, ys = self._trace_xy()
        w0, w1 = self._wall_clock_ms()
        self._totals_cache = SessionTotals(
            duration_s=float(t[-1] - t[0]) if len(t) >= 2 else 0.0,
            moving_s=moving_time_s(t, v),
            distance_m=path_distance_m(xs, ys),
            start_clock=clock_hhmm(w0),
            end_clock=clock_hhmm(w1),
        )
        return self._totals_cache

    # ------------------------------------------------------------------ lap level
    def lap_stats(self) -> list[LapStat]:
        """One LapStat per VALID lap, in session order; cached per segmentation. Speed stats
        come from the lap's own arrays; g peaks slice the g-meter by the lap's media window;
        brake/coast reduce the driving-channel event lists. Signal-absent fields are None
        (never 0) — see LapStat."""
        if self._lap_stats_cache is not None:
            return self._lap_stats_cache
        gm = self._gmeter()
        has_g = bool(getattr(gm, "has_data", False))
        if has_g:
            long_src = gm.long_g_gps if gm.long_g_gps is not None else gm.long_g
        out: list[LapStat] = []
        for i in self._valid_lap_ids():
            lap_time = float(self._lap_time(i))
            dist, speed_kmh, elapsed = self._lap_arrays(i)
            vmax = float(np.max(speed_kmh)) if len(speed_kmh) else None
            vmin = float(np.min(speed_kmh)) if len(speed_kmh) else None
            avg = (float(dist[-1]) / lap_time * 3.6
                   if len(dist) and lap_time > 0 else None)
            lat_pk = brake_pk = None
            if has_g:
                win = self._lap_window(i)
                if win is not None:
                    m = in_windows_mask(gm.times, [win])
                    lat_pk, brake_pk = peak_g(gm.lat_g[m], long_src[m])
            brake_s = brake_n = coast_s = coast_frac = None
            if has_g:
                events = self._brake_events(i)
                spans = self._coast_spans(i)
                brake_s = float(sum(e.duration for e in events))
                brake_n = len(events)
                coast_s = float(sum(sp.duration for sp in spans))
                coast_frac = coast_s / lap_time if lap_time > 0 else None
            out.append(LapStat(idx=i, time=lap_time, vmax_kmh=vmax, avg_kmh=avg,
                               vmin_kmh=vmin, peak_lat_g=lat_pk, peak_brake_g=brake_pk,
                               brake_s=brake_s, brake_n=brake_n,
                               coast_s=coast_s, coast_frac=coast_frac))
        self._lap_stats_cache = out
        return out

    def pace(self) -> PaceStats | None:
        """Lap-time distribution over the CONSISTENCY laps (valid ∧ dropout-free — the same
        set every σ statistic runs over, so Pace and the consistency panel always agree).
        Cheap (a handful of floats) → not cached, like Session's consistency assemblers."""
        return pace_stats([self._lap_time(i) for i in self._consistency_lap_ids()])

    def _clean_times(self) -> list[float]:
        """The consistency laps' times in session order — the one series every pace-quality
        statistic below runs over (same set as pace(), so the tiles can never disagree)."""
        return [self._lap_time(i) for i in self._consistency_lap_ids()]

    def pace_trend(self) -> float | None:
        """The robust lap-time trend (Theil–Sen median slope, s/lap; negative = getting
        faster) over the clean laps IN SESSION ORDER. Gated at TREND_MIN_LAPS — a slope
        fitted to five laps is noise, so short sessions honestly report None."""
        times = self._clean_times()
        if len(times) < TREND_MIN_LAPS:
            return None
        return theil_sen_slope(times)

    def race_pace(self) -> float | None:
        """The best mean of RACE_PACE_N consecutive clean laps — the sustained-run pace next
        to the single glory lap. None with fewer than a full window of clean laps."""
        return best_consecutive_mean(self._clean_times())

    def pace_cov(self) -> float | None:
        """The consistency rating: coefficient of variation (σ/median %) of the clean lap
        times — scale-free, so it is comparable across tracks. None with <2 laps."""
        return cov_pct(self._clean_times())

    def laps_within_pct(self, pct: float = 1.0) -> tuple[int, int]:
        """(count, n): how many of the n clean laps sit within `pct` % of the session best —
        the "banked pace" count (the best lap itself counts)."""
        times = self._clean_times()
        return within_pct_of_best(times, pct), len(times)

    def longest_coast_s(self) -> float | None:
        """The longest single coasting span (s) across the valid laps — the headline "where
        seconds hide" number next to the median coast tile. None without a g signal; 0.0 is
        a real (and good) zero with one."""
        gm = self._gmeter()
        if not getattr(gm, "has_data", False):
            return None
        longest = 0.0
        for i in self._valid_lap_ids():
            for sp in self._coast_spans(i):
                longest = max(longest, float(sp.duration))
        return longest

    def gg_envelope(self) -> float | None:
        """The demonstrated combined-g envelope (p98 of hypot over the valid-lap g cloud) —
        the dashed ring on the friction circle and the "grip ceiling" tile. None without a
        g signal / valid laps."""
        cloud = self.gg_cloud()
        if cloud is None:
            return None
        return envelope_g(cloud[0], cloud[1])

    def session_vmax(self) -> tuple[float, int] | None:
        """(top speed km/h, lap id) over the valid laps — the session's headline Vmax and
        where it happened. None when no lap has a speed sample."""
        best: tuple[float, int] | None = None
        for st in self.lap_stats():
            if st.vmax_kmh is not None and (best is None or st.vmax_kmh > best[0]):
                best = (st.vmax_kmh, st.idx)
        return best

    def gg_cloud(self, max_points: int = 4000) -> tuple[np.ndarray, np.ndarray] | None:
        """The friction-circle scatter: (lat_g, long_g) samples restricted to the VALID laps'
        media windows (no pit/out-lap noise), evenly strided down to ≤ `max_points` so the
        view never draws an unbounded cloud. Longitudinal is the validated GPS-derived signal
        when present (the same axis convention as the dial / grip envelope). None when there
        is no g signal or no valid lap. Cached per segmentation."""
        if self._gg_cache is not None:
            return self._gg_cache
        gm = self._gmeter()
        if not getattr(gm, "has_data", False):
            return None
        windows = [w for w in (self._lap_window(i) for i in self._valid_lap_ids())
                   if w is not None]
        if not windows:
            return None
        mask = in_windows_mask(gm.times, windows)
        long_src = gm.long_g_gps if gm.long_g_gps is not None else gm.long_g
        lat = np.asarray(gm.lat_g[mask], float)
        lon = np.asarray(long_src[mask], float)
        if len(lat) == 0:
            return None
        stride = max(1, int(np.ceil(len(lat) / max_points)))
        self._gg_cache = (lat[::stride], lon[::stride])
        return self._gg_cache
