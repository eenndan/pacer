"""Corner model: curvature-based corner detection + per-corner lap analysis.

PACER-FREE BY CONTRACT (numpy only); fed by Session's per-lap arrays, so neither this module
nor the views import the compiled `pacer` bindings. NOT MAP-MATCHING: everything runs on our own
smoothed GPS trace (curvature from its own heading, threshold from its own distribution) — no
external centerline.

Pipeline: per-lap curvature kappa(s) → median profile on the best-lap grid (averages out line
choice + GPS noise) → log-domain Otsu threshold (no magic constant) → hysteresis spans split at
sign changes, merged across jitter, filtered by arc length + turn angle → enter/exit/apex
(|kappa|-weighted centroid, stable on flat-topped sweepers) + direction, in best-lap odometer.

Projection (lap_corner_stats / segment_times): corner windows are fractions of the best lap's
odometer, projected onto EVERY lap by ONE monotone spatial warp for that whole lap
(project_boundaries). Never a mix of frames within a lap: that is what shrank segments by up
to 27 % and made the ideal lap's headline 44 % artifact. The normalized projection (same fraction =
same track position, as lap_sector_splits) is what remains where there is nothing to match against
— no spatial traces, or no match anywhere on the lap. Corners + straights partition each lap, so
the telescoping sum of segment times equals the lap time exactly (asserted).

That warp is ONE object per lap and every projection here is a read of it, so it is built once and
passed down (`alignment=`); `corner_model.CornerModel.lap_alignment` owns the memo and its
invalidation. The functions still derive it themselves when no caller supplies one, which is what
keeps this module usable from the pure-numpy unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import _smooth

# --- per-corner alignment ---------------------------------------------------------------
# The corner-window projection (lap_corner_stats / segment_times / coaching.corner_phase_losses)
# aligns EVERY comparison lap to the reference (best) lap by the heading-gated spatial
# nearest-point match, assembled into ONE warp for the whole lap (project_boundaries). The
# NORMALIZED projection (d·total_lap/total_ref — same fraction = same track position) is what
# remains where there is nothing to match against: no spatial `traces` (the pure-numpy callers, and
# a cross-recording reference lap, which has no local trace pair), or no match anywhere on the lap.
#
# THERE WAS A DRIFT GATE HERE — `NORMALIZED_DRIFT_MAX = 0.005` — which kept a lap whose line length
# drifted under 0.5 % on the normalized projection verbatim. It was kept for byte-identity with the
# pre-gate output, NOT because it separated aligned laps from misaligned ones, and its own note
# called lowering it a live follow-up. Measured on the laps it SKIPPED (error = the LONGITUDINAL
# component of |P_ref(d) − P_lap(proj(d))|, so a wider racing line does not count as misalignment):
#
#                       0060 pair (22 of 38 laps)     0062 (54 of 65 laps)
#   normalized (gated)  median 1.96 m, p90 6.46 m     median 0.90 m, p90 2.83 m
#   spatial warp        median 0.10 m, p90 4.44 m     median 0.01 m, p90 0.08 m
#
# Line-length drift is only a WEAK predictor of odometer misalignment (r = +0.38 on the pair): a lap
# at 0.41 % drift carried 14.7 m of it while one at 1.55 % carried 7.6 m. So the gate split one
# session's laps into TWO measurement frames on a scalar that does not measure what it was gating —
# a lap at 0.499 % and a lap at 0.501 % were projected by different machinery. Removing it moves the
# ideal +0.316 s on the 0060 pair and −0.071 s on 0062 and reorders the coaching ranking on both;
# the warp is not uniformly better per boundary (it is worse on 17.4 % of the pair's below-gate
# boundaries, by a median 1.4 m, where a match fails the 3 m gate and the knot is interpolated) but
# every order statistic of the residual improves on both recordings.
#
# THE REFERENCE LAP IS UNTOUCHED, by construction and in measurement: it matches its own trace, so
# every knot lands on itself — max boundary move 7e-15 m on 0060 and exactly 0 m on 0062. The
# yardstick the other laps are measured against does not move.
#
# Spatial nearest-point gates — the SAME constants best_rolling_lap trusts
# (session._ROLLING_*): search a ±SEARCH_FRAC arc of the comparison lap around the reference
# fraction, keep only same-direction samples (heading cos ≥ MIN_COS), accept the sub-sample-refined
# closest approach only when it is within MATCH_MAX_M of the reference point. A boundary whose match
# fails ANY gate contributes no knot to the lap's warp (it is INTERPOLATED, never mixed frames).
_SPATIAL_SEARCH_FRAC = 0.02      # ±2% of the comparison lap's samples (~21 m), floored at 5
_SPATIAL_HEADING_MIN_COS = 0.5   # same-direction within 60° (rejects the other leg of a corner)
# PUBLIC because it is the projection's stated per-boundary resolution and is cited as such from
# corner_model (see MAX_DONOR_SPAN_DEV's sub-resolution paragraph: a segment whose admission band
# is far under this cannot be judged by a projection built out of matches only accurate to it).
SPATIAL_MATCH_MAX_M = 3.0        # refined closest approach must be ≤ 3 m to count as the same point

# --- the session's own geometry: a consensus line + each lap's rigid receiver shift ----------
# WHAT THIS IS FOR. The gate above judges the distance between the reference lap's trace point and
# the comparison lap's trace. That distance is not only the two drivers' lines: a consumer GNSS
# receiver's position error is dominated by a slowly-varying bias that is very nearly CONSTANT over
# one 90-second lap, so each lap's whole trace sits displaced by one 2D vector. Measured on the
# owner's two D24 recordings (M6, `studio/docs/corner-match-0060-2026-09.md`): |T| a median 1.34 m
# and up to 3.65 m on 0060 against 0.61 / 1.23 m on 0062, persisting from one lap to the next
# (lag-1 +0.41, shuffle p < 0.001), with an ALTITUDE witness — which no racing line can move —
# scattering 3.9x as far on 0060 (sd 3.23 m vs 0.82). On 0060 that alone failed 38 % of the
# interior boundaries, all of them on this distance gate.
#
# WHY A RIGID TRANSLATION IS SEPARABLE FROM A RACING LINE, which is the whole risk here. Around a
# CLOSED circuit the heading sweeps a full turn, so a fixed vector T shows up as a signed
# perpendicular offset n̂(s)·T that changes SIGN twice a lap — outside here, inside there. A
# different line does not do that: running wider is an offset along the local normal with a
# CONSISTENT sign wherever the track curves the same way, and it is not in the span of {n̂·T}. So
# fitting one T per lap by least squares over stations spread around the whole lap removes the
# receiver's bias and leaves the line alone, and the fit's own residual says how much was left.
# `tests/test_corner_drift.py` plants both on a real trace and asserts exactly that separation.
#
# ARC LENGTH IS TRANSLATION-INVARIANT, which is why de-drifting cannot move the reference frame:
# the corner windows are odometer positions along the reference lap, and translating a polyline
# does not change its arc length. De-drifting every lap (the reference included) is therefore the
# same match as de-drifting each comparison lap by T_lap − T_ref with the anchor left alone — and
# the reference lap still matches itself exactly, so its own windows are untouched.
DRIFT_STATIONS = 256             # stations spread over the reference lap; 2 unknowns, so ample
DRIFT_MIN_LAPS = 3               # fewer clean laps than this and a median is not a consensus
DRIFT_MIN_STATIONS = 32          # fewer answering stations than this and the fit is not grounded
_DRIFT_ARC_FRAC = 0.05           # ±5 % of the lap searched for the nearest same-direction sample
_DRIFT_TRIM_MAD = 3.0            # stations past this many MADs are refit-excluded (see _rigid_shift)


@dataclass(frozen=True)
class SessionGeometry:
    """One session's spatial geometry model, fitted once per segmentation over the clean laps.

    * `stations` — reference-lap odometer (m) of each measurement station, ascending.
    * `consensus` — the CONSENSUS LINE as a signed perpendicular offset (m) from the reference lap
      at each station: the median over the clean laps of their own offset there. Positive is to
      the left of the reference lap's direction of travel. NaN where no lap answered.
    * `shift` — per lap id, the rigid 2D translation (metres, local frame) that best explains that
      lap's offset from the CONSENSUS. The reference lap has one too: its row of offsets is zero by
      construction, so its shift is fitted to −consensus.
    * `residual_rms` — per lap id, the RMS perpendicular offset left after its own shift is
      removed. This is the part a translation does not explain — line, local noise, everything else.

    `total_ref` and `frame` (`_station_frame` on the reference lap) are kept so that `fit` can
    measure a lap that was not in the consensus set — a GPS-dropout lap is excluded from every
    "best" in the app but its corner windows are still drawn, and it must be projected in the same
    frame as the rest rather than left on the uncorrected one.

    `self_offset` is what `perpendicular_offsets` reports for the REFERENCE LAP AGAINST ITSELF,
    which is not identically zero: the offset is taken to the nearest fix rather than to the line
    between fixes, so on a curve it carries a bias of order kappa·ds²/2 (a few millimetres at 1 m
    spacing on a 40 m radius). Every lap is measured by the same rule and so carries the same bias,
    and subtracting this cancels it — which is what makes a session whose laps sit on ONE line come
    out at exactly zero consensus and exactly zero shift, and therefore matched bit for bit as
    before rather than three millimetres off it.
    """

    stations: np.ndarray
    total_ref: float
    frame: tuple
    self_offset: np.ndarray
    consensus: np.ndarray
    shift: dict[int, np.ndarray]
    residual_rms: dict[int, float]

    def offsets(self, lap_xs, lap_ys, lap_cum) -> np.ndarray:
        """One lap's signed perpendicular offset from the reference lap at each station, with the
        estimator's own bias (`self_offset`) taken out."""
        return perpendicular_offsets(self.stations, self.total_ref, self.frame,
                                     lap_xs, lap_ys, lap_cum) - self.self_offset

    def fit(self, lap_xs, lap_ys, lap_cum) -> tuple[np.ndarray, float]:
        """(rigid translation, residual RMS) of one lap against this session's consensus line."""
        return _rigid_shift(self.offsets(lap_xs, lap_ys, lap_cum) - self.consensus,
                            self.frame[4], self.frame[5])

    def relative_shift(self, lap_shift, ref_id: int) -> tuple[float, float]:
        """The translation to remove from a lap before matching it against lap `ref_id`'s trace:
        T_lap − T_ref, so a session-wide common bias cancels and the reference lap gets exactly
        (0, 0) against itself. `lap_shift` is that lap's own fitted translation."""
        t = np.asarray(lap_shift, float) - self.shift.get(int(ref_id), np.zeros(2))
        return float(t[0]), float(t[1])


def _rigid_shift(off: np.ndarray, nx: np.ndarray, ny: np.ndarray) -> tuple[np.ndarray, float]:
    """The least-squares rigid 2D translation T with `off_i ≈ n̂_i · T`, and the RMS residual.

    TRIMMED ONCE, because this feeds a geometric correction and a single 5 m excursion must not
    tilt it: stations whose first-pass residual exceeds `_DRIFT_TRIM_MAD` median-absolute-deviations
    are dropped and T is refitted on the rest (the refit is skipped when it would leave too few).
    Returns ((0, 0), 0.0) when there is nothing to fit."""
    ok = np.isfinite(off)
    if int(ok.sum()) < DRIFT_MIN_STATIONS:
        return np.zeros(2), 0.0
    a = np.column_stack([nx[ok], ny[ok]])
    y = off[ok]
    t, *_ = np.linalg.lstsq(a, y, rcond=None)
    r = y - a @ t
    mad = float(np.median(np.abs(r - np.median(r))))
    if mad > 0:
        keep = np.abs(r - np.median(r)) <= _DRIFT_TRIM_MAD * mad
        if int(keep.sum()) >= DRIFT_MIN_STATIONS:
            t, *_ = np.linalg.lstsq(a[keep], y[keep], rcond=None)
            r = y - a @ t
    return t, float(np.sqrt(np.mean(r ** 2)))


def _station_frame(stations, ref_xs, ref_ys, ref_cum):
    """The reference lap's point, unit tangent and unit left normal at each station odometer."""
    ax = np.interp(stations, ref_cum, ref_xs)
    ay = np.interp(stations, ref_cum, ref_ys)
    rtx, rty = _unit_tangents(ref_xs, ref_ys)
    atx = np.interp(stations, ref_cum, rtx)
    aty = np.interp(stations, ref_cum, rty)
    norm = np.hypot(atx, aty)
    norm[norm == 0] = 1.0
    atx, aty = atx / norm, aty / norm
    return ax, ay, atx, aty, -aty, atx


def perpendicular_offsets(stations, total_ref: float, ref_frame, lap_xs, lap_ys, lap_cum):
    """Signed perpendicular offset (m) of one lap from the reference lap at each station: positive
    to the LEFT of the reference lap's direction of travel, NaN where no same-direction sample of
    that lap lies in the searched arc.

    `ref_frame` is `_station_frame`'s tuple. The nearest SAMPLE is used rather than the
    sub-sample-refined nearest point: the quantity taken from it is the PERPENDICULAR component,
    which an along-track sampling error of half a fix interval perturbs only to second order in the
    local curvature (≤ 0.03 m at 3 m spacing on a 30 m radius), while refining every one of
    DRIFT_STATIONS × laps candidates would cost what the whole fit is worth."""
    ax, ay, atx, aty, nx, ny = ref_frame
    lap_xs = np.asarray(lap_xs, float)
    lap_ys = np.asarray(lap_ys, float)
    lap_cum = np.asarray(lap_cum, float)
    n = len(lap_cum)
    out = np.full(len(stations), np.nan)
    if n < 2 or float(lap_cum[-1]) <= 0 or total_ref <= 0:
        return out
    k = min(max(5, int(_DRIFT_ARC_FRAC * n)), n)
    centers = np.clip(np.searchsorted(lap_cum, stations / total_ref * float(lap_cum[-1])), 0, n - 1)
    idx = np.clip(centers[:, None] + np.arange(-k, k + 1)[None, :], 0, n - 1)
    dx = lap_xs[idx] - ax[:, None]
    dy = lap_ys[idx] - ay[:, None]
    ltx, lty = _unit_tangents(lap_xs, lap_ys)
    same = atx[:, None] * ltx[idx] + aty[:, None] * lty[idx] >= _SPATIAL_HEADING_MIN_COS
    d2 = np.where(same, dx * dx + dy * dy, np.inf)
    j = np.argmin(d2, axis=1)
    rows = np.arange(len(stations))
    hit = np.isfinite(d2[rows, j])
    out[hit] = (nx[hit] * dx[rows, j][hit]) + (ny[hit] * dy[rows, j][hit])
    return out


def session_geometry(ref_trace, lap_traces: dict) -> SessionGeometry | None:
    """Fit the session's consensus line + each clean lap's rigid receiver shift (SessionGeometry),
    or None when there is not enough to fit one (< DRIFT_MIN_LAPS laps, a degenerate reference,
    or too few stations answered).

    `ref_trace` is the reference lap's (xs, ys, cum); `lap_traces` maps lap id -> the same triple
    for every CLEAN lap, the reference lap included (its own offsets are then zero and its shift is
    fitted against the consensus like any other lap's)."""
    if ref_trace is None or len(lap_traces) < DRIFT_MIN_LAPS:
        return None
    ref_xs, ref_ys, ref_cum = (np.asarray(v, float) for v in ref_trace)
    if len(ref_cum) < 2:
        return None
    total_ref = float(ref_cum[-1])
    if total_ref <= 0:
        return None
    step = total_ref / DRIFT_STATIONS
    stations = np.arange(DRIFT_STATIONS) * step + step / 2.0
    frame = _station_frame(stations, ref_xs, ref_ys, ref_cum)
    self_offset = perpendicular_offsets(stations, total_ref, frame, ref_xs, ref_ys, ref_cum)
    ids = sorted(lap_traces)
    offs = {lid: perpendicular_offsets(stations, total_ref, frame, *lap_traces[lid]) - self_offset
            for lid in ids}
    stack = np.array([offs[lid] for lid in ids])
    if not np.isfinite(stack).any():
        return None
    with np.errstate(invalid="ignore"):
        consensus = np.nanmedian(stack, axis=0)
    if int(np.isfinite(consensus).sum()) < DRIFT_MIN_STATIONS:
        return None
    nx, ny = frame[4], frame[5]
    shift, residual = {}, {}
    for lid in ids:
        t, rms = _rigid_shift(offs[lid] - consensus, nx, ny)
        shift[lid], residual[lid] = t, rms
    return SessionGeometry(stations, total_ref, frame, self_offset, consensus, shift, residual)


def anchor_offsets(d_ref, geometry: SessionGeometry | None, ref_id: int,
                   ref_xs, ref_ys, ref_cum):
    """Per-boundary (dx, dy) that cancels the REFERENCE lap's own NON-RIGID deviation from the
    session's consensus line, purely PERPENDICULAR to its direction of travel. None when there is
    no geometry to measure it with, and all-zero when the reference lap sits on the consensus.

    WHAT IS LEFT FOR THIS TO DO. De-drifting a comparison lap by `SessionGeometry.relative_shift`
    already removes both laps' RIGID shifts — the lap's own and the reference's — so what remains
    between the anchor and the consensus is the reference lap's fit RESIDUAL: the part of its
    displacement one translation does not explain. On D24 0060 that is what the reference lap
    (17, rank 34/38 from the consensus) carries at the C8 exit and the C9 entry, the two worst
    boundaries in the session; a rigid correction cannot reach it, and it is measured on the same
    stations as everything else here: `consensus + n̂ · T_ref`.

    PERPENDICULAR ONLY, and that is what keeps ONE frame. The corner windows are arc-length
    positions along the reference lap, so moving the anchor ALONG the track would redefine where
    the corner is on every lap except the reference (which keeps its own odometer by definition)
    and put the two in different frames. Sideways it cannot: the anchor keeps the reference lap's
    longitudinal position, while the distance the gate judges — a closest approach, i.e. a
    PERPENDICULAR distance to the comparison lap's line — is measured from the line the session
    actually drove rather than from the one lap that happens to be fastest."""
    if geometry is None:
        return None
    d_ref = np.asarray(d_ref, float)
    ref_xs = np.asarray(ref_xs, float)
    ref_ys = np.asarray(ref_ys, float)
    ref_cum = np.asarray(ref_cum, float)
    _sx, _sy, _stx, _sty, snx, sny = _station_frame(geometry.stations, ref_xs, ref_ys, ref_cum)
    t_ref = geometry.shift.get(int(ref_id), np.zeros(2))
    c = geometry.consensus + snx * t_ref[0] + sny * t_ref[1]
    ok = np.isfinite(c)
    if not ok.any():
        return None
    off = np.interp(d_ref, geometry.stations[ok], c[ok])
    _ax, _ay, _atx, _aty, nx, ny = _station_frame(d_ref, ref_xs, ref_ys, ref_cum)
    return np.column_stack([off * nx, off * ny])


def line_length_drift(total_lap: float, total_ref: float) -> float:
    """The cheap line-length drift between a comparison lap and the reference (best) lap:
    |total_lap − total_ref| / total_ref, where each total is that lap's full odometer (cum[-1]).
    0 (no drift) for a non-positive reference total.

    DESCRIPTIVE ONLY — nothing gates on it. It used to be the scalar the per-corner alignment
    switched on (`NORMALIZED_DRIFT_MAX`), until it was measured to be a weak predictor of the
    odometer misalignment it was standing in for (see the block above). It stays because it is a
    real, cheap property of a lap and the fixtures use it to say how far a lap's line has drifted."""
    total_ref = float(total_ref)
    if total_ref <= 0:
        return 0.0
    return abs(float(total_lap) - total_ref) / total_ref


def _unit_tangents(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-sample unit direction-of-travel of a trace (central differences, normalized; a
    zero-length step keeps a zero vector, which the heading gate then rejects). Mirrors
    session._unit_tangents — the same same-direction filter best_rolling_lap uses. Needs len ≥ 2."""
    tx = np.gradient(np.asarray(xs, float))
    ty = np.gradient(np.asarray(ys, float))
    norm = np.hypot(tx, ty)
    norm[norm == 0] = 1.0
    return tx / norm, ty / norm


def _spatial_matches(d_ref, total_ref: float,
                     ref_xs, ref_ys, ref_cum,
                     lap_xs, lap_ys, lap_cum,
                     lap_shift=(0.0, 0.0), anchor_offset=None) -> np.ndarray:
    """Map reference-odometer boundaries `d_ref` onto a comparison lap's odometer by the robust
    heading-gated, sub-sample-refined nearest-point search (the best_rolling_lap machinery, on
    anchors instead of every sample). Per boundary: the reference (x,y) at `d_ref` is the anchor;
    the nearest same-direction comparison-lap sample within the ±_SPATIAL_SEARCH_FRAC arc is found,
    its two adjacent segments are projected onto, and the closer projection's chord parameter
    interpolates the comparison-lap odometer. NaN where no candidate passes the heading or
    SPATIAL_MATCH_MAX_M distance gate.

    Takes the WHOLE boundary set in one call because the per-lap unit-tangent fields are O(n) and
    identical for every anchor — computing them once per lap instead of once per boundary is what
    lets the alignment be built from the full partition without paying for it (the previous
    one-anchor-per-call shape recomputed both tangent fields 24 times a lap).

    `lap_shift` is subtracted from the comparison lap's trace and `anchor_offset` (one (dx, dy) per
    boundary) added to the anchor point, so the gate judges the two lines with the receiver's rigid
    bias taken out — see SessionGeometry. Both default to no-ops, and a no-op is EXACT: `x - 0.0`
    and `x + 0.0` are the same double, so a session with no fitted geometry matches bit for bit."""
    d_ref = np.asarray(d_ref, float)
    ref_xs = np.asarray(ref_xs, float)
    ref_ys = np.asarray(ref_ys, float)
    ref_cum = np.asarray(ref_cum, float)
    lap_xs = np.asarray(lap_xs, float) - float(lap_shift[0])
    lap_ys = np.asarray(lap_ys, float) - float(lap_shift[1])
    lap_cum = np.asarray(lap_cum, float)
    out = np.full(len(d_ref), np.nan)
    n_lap = len(lap_cum)
    if n_lap < 2 or len(ref_cum) < 2 or total_ref <= 0 or not len(d_ref):
        return out
    # Anchors: the reference trace point + direction at each boundary odometer, moved sideways onto
    # the session's consensus line when one was fitted (anchor_offset).
    ax = np.interp(d_ref, ref_cum, ref_xs)
    ay = np.interp(d_ref, ref_cum, ref_ys)
    if anchor_offset is not None:
        anchor_offset = np.asarray(anchor_offset, float)
        ax = ax + anchor_offset[:, 0]
        ay = ay + anchor_offset[:, 1]
    rtx, rty = _unit_tangents(ref_xs, ref_ys)
    atx = np.interp(d_ref, ref_cum, rtx)
    aty = np.interp(d_ref, ref_cum, rty)
    ltx, lty = _unit_tangents(lap_xs, lap_ys)
    # Search only a ±SEARCH_FRAC arc of the comparison lap around the same normalized fraction.
    total_lap = float(lap_cum[-1])
    k = max(5, int(_SPATIAL_SEARCH_FRAC * n_lap))
    centers = np.clip(np.searchsorted(lap_cum, (d_ref / total_ref) * total_lap), 0, n_lap - 1)
    for i in range(len(d_ref)):
        lo = max(0, int(centers[i]) - k)
        hi = min(n_lap, int(centers[i]) + k + 1)
        dx = lap_xs[lo:hi] - ax[i]
        dy = lap_ys[lo:hi] - ay[i]
        d2 = dx * dx + dy * dy
        # Same-direction gate: reject the other leg of a corner / out-and-back.
        d2 = np.where(atx[i] * ltx[lo:hi] + aty[i] * lty[lo:hi] >= _SPATIAL_HEADING_MIN_COS,
                      d2, np.inf)
        if not np.isfinite(d2).any():
            continue
        j = lo + int(np.argmin(d2))
        # Sub-sample refinement: project the anchor onto the two trace segments adjacent to its
        # nearest sample; the closer projection's chord parameter interpolates the odometer.
        best_d2, best_dist = np.inf, 0.0
        for j0, j1 in ((max(j - 1, 0), j), (j, min(j + 1, n_lap - 1))):
            vx, vy = lap_xs[j1] - lap_xs[j0], lap_ys[j1] - lap_ys[j0]
            len2 = vx * vx + vy * vy
            if len2 <= 0:
                cand_d2 = (lap_xs[j0] - ax[i]) ** 2 + (lap_ys[j0] - ay[i]) ** 2
                cand_dist = float(lap_cum[j0])
            else:
                u = float(np.clip(((ax[i] - lap_xs[j0]) * vx
                                   + (ay[i] - lap_ys[j0]) * vy) / len2, 0.0, 1.0))
                qx, qy = lap_xs[j0] + u * vx, lap_ys[j0] + u * vy
                cand_d2 = (qx - ax[i]) ** 2 + (qy - ay[i]) ** 2
                cand_dist = float(lap_cum[j0] + u * (lap_cum[j1] - lap_cum[j0]))
            if cand_d2 < best_d2:
                best_d2, best_dist = cand_d2, cand_dist
        # Distance gate on the REFINED closest approach (same point only if within MATCH_MAX_M).
        if best_d2 <= SPATIAL_MATCH_MAX_M ** 2:
            out[i] = best_dist
    return out


# "no alignment supplied — derive it here". A distinct sentinel because None is the LEGAL value
# meaning "this lap keeps the normalized projection", which a caller must be able to pass through.
DERIVE_ALIGNMENT = object()


def lap_alignment(frame, total_ref: float, total_lap: float, *,
                  traces: tuple | None = None,
                  lap_shift=(0.0, 0.0), anchor_offset=None) -> tuple | None:
    """ONE comparison lap's odometer alignment to the reference lap, as the (knot_ref, knot_lap)
    pair of a monotone piecewise-linear warp — or None when the normalized projection applies
    verbatim (no traces, or no spatial match survived anywhere on the lap).

    `frame` is the reference-odometer boundary set the warp is fitted to; the two timing-line
    anchors are added here. See project_boundaries for what the warp is and why it exists.

    `lap_shift` / `anchor_offset` are the session geometry's corrections (`SessionGeometry`,
    `anchor_offsets`), passed straight through to the match. Both default to no-ops.

    BUILD IT ONCE PER LAP when you are projecting many windows of the same lap.
    `coaching.corner_phase_losses` is called per (lap, corner) — deriving the lap's warp inside
    each of those calls cost 106 ms of a 168 ms `Session.phase_report` on the 38-lap D24 0060 pair.
    Hoisting it to once per lap took that to 43.9 ms."""
    total_ref = float(total_ref)
    total_lap = float(total_lap)
    if traces is None:
        return None
    ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum = traces
    knots = np.asarray(frame, float)
    matched = _spatial_matches(knots, total_ref, ref_xs, ref_ys, ref_cum,
                               lap_xs, lap_ys, lap_cum,
                               lap_shift=lap_shift, anchor_offset=anchor_offset)
    # The two exact timing-line anchors plus every surviving match, in track order, kept strictly
    # increasing on both axes (a match that crosses its accepted neighbour is a mis-match, and
    # dropping it costs only interpolation).
    knot_ref = [0.0]
    knot_lap = [0.0]
    for i in np.argsort(knots, kind="stable"):
        d, m = float(knots[i]), float(matched[i])
        if not np.isfinite(m) or not (knot_ref[-1] < d < total_ref) \
                or not (knot_lap[-1] < m < total_lap):
            continue
        knot_ref.append(d)
        knot_lap.append(m)
    if len(knot_ref) == 1:
        # Nothing matched anywhere: the two anchors alone ARE the normalized map, so say so and let
        # the caller return it verbatim rather than re-deriving it through np.interp.
        return None
    knot_ref.append(total_ref)
    knot_lap.append(total_lap)
    return np.asarray(knot_ref, float), np.asarray(knot_lap, float)


def project_boundaries(d_ref, total_ref: float, total_lap: float, *,
                       traces: tuple | None = None, frame=None,
                       alignment=DERIVE_ALIGNMENT) -> np.ndarray:
    """Project reference-odometer corner-window boundaries `d_ref` onto a comparison lap's odometer.

    The alignment shared by lap_corner_stats / segment_times / driving / coaching. With no spatial
    `traces` (the pure-numpy callers, a cross-recording reference lap) this is exactly the legacy
    normalized projection `d_ref · total_lap / total_ref`.

    Otherwise the lap is aligned by ONE WARP, not boundary by boundary. The spatial matches
    that pass the gates become the interior KNOTS of a monotone piecewise-linear map, anchored at
    the timing line at both ends (0 → 0 and total_ref → total_lap are the same physical point on
    both laps by definition), and every boundary is read off that map. A boundary whose own match
    fails is therefore INTERPOLATED between its neighbours' matches — it is never left in the
    normalized frame while its neighbour sits in the spatial one.

    WHY (the P0 this replaces): the previous version fell back to the normalized value for THAT
    boundary alone, so a segment with one spatial edge and one fallback edge shrank or stretched by
    the whole local odometer offset — up to 11.4 m on a 42.8 m straight (27 %) on the D24 0060 pair.
    Per-lap tiling still summed exactly (the time moved BETWEEN segments), so nothing downstream
    could see it, and `CornerModel.segment_bests`' per-segment minimum then harvested the shrunken
    windows: the pair's ideal read 62.869 s where the order statistic extrapolated from the 22
    undistorted laps of the same recording predicts 65.226 s — 2.36 s of the 5.36 s headline was
    measurement artifact, not driving. With one frame per lap and the symmetric span admission in
    `corner_model.MAX_DONOR_SPAN_DEV` it read 65.149 s, and 65.464 s since #300 warped every lap.

    The warp is strictly increasing by construction (a match that would cross its neighbour is
    dropped, not clamped), so the partition can no longer fold — which is why the old
    `np.maximum.accumulate` repair is gone.

    `traces`, when given, is (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) — the reference
    (best) lap's and the comparison lap's local-frame trace + odometer (Session feeds these; the
    pure-numpy unit tests omit them and so always get the normalized projection).

    `frame`, when given, is the FULL reference-odometer boundary set the lap's warp is built from,
    so a caller asking about ONE corner (coaching.corner_phase_losses) gets the SAME alignment as a
    caller asking about the whole partition (lap_corner_stats / segment_bests / driving_channels).
    Defaults to `d_ref` itself — which is already the full set at every whole-partition call site.

    `alignment` is that warp, already built (`lap_alignment`) — pass it when projecting many
    windows of the SAME lap so the spatial match runs once for the lap instead of once per window.
    None is a legal value meaning "this lap keeps the normalized projection"."""
    d_ref = np.asarray(d_ref, float)
    total_ref = float(total_ref)
    total_lap = float(total_lap)
    normalized = d_ref * (total_lap / total_ref) if total_ref > 0 else d_ref.copy()
    if alignment is DERIVE_ALIGNMENT:
        alignment = lap_alignment(d_ref if frame is None else frame, total_ref, total_lap,
                                  traces=traces)
    elif alignment is not None and abs(float(alignment[1][-1]) - total_lap) > 1e-6:
        # A warp built for a DIFFERENT lap would otherwise project silently and wrongly — its last
        # anchor is that lap's total. Cheap and exact: lap_alignment ends knot_lap at total_lap.
        raise ValueError(
            f"alignment was built for a lap of total {float(alignment[1][-1])} m, not {total_lap}")
    if alignment is None:
        return normalized
    return np.interp(d_ref, alignment[0], alignment[1])


# --- model constants -------------------------------------------------------------------
# Constants tuned on the D24 recordings; the detected set is insensitive within the noted bands.
KAPPA_SMOOTH_M = 8.0      # m of arc; curvature boxcar (~5 samples), resolves the shortest corners
GRID_STEP_M = 0.75        # m; median-profile grid step, ~2x finer than GPS spacing
LOG_KAPPA_FLOOR = 1e-4    # |kappa| floor before log10; only guards log10(0), can't affect the split
HYSTERESIS_RATIO = 0.8    # span extends while |kappa| >= ratio×threshold; Schmitt trigger; band [0.6,1.0]
MERGE_GAP_M = 10.0        # m; re-merge adjacent same-direction jitter fragments; band [6,15]
MIN_SPAN_M = KAPPA_SMOOTH_M  # shorter than the smoothing support is unresolvable; band [5,12]
MIN_TURN_DEG = 30.0       # min integrated turn for a real corner (kinks <=17°, corners >=44°); band [20,40]


@dataclass(frozen=True)
class Corner:
    """One detected corner, in REFERENCE (best) lap odometer metres, track order."""

    cid: int          # 1-based id in track order (C1 is the first corner after the line)
    enter: float      # odometer (m) where sustained cornering starts
    exit: float       # odometer (m) where it ends
    apex: float       # odometer (m) of the |kappa|-weighted centroid (the geometric apex)
    direction: int    # +1 = left (kappa > 0), -1 = right
    turn_deg: float   # integrated heading change magnitude over the span (degrees)

    @property
    def label(self) -> str:
        return f"C{self.cid}"


@dataclass(frozen=True)
class CornerStat:
    """One lap x corner: the projected per-corner metrics (speeds in km/h, times in s)."""

    cid: int                  # Corner.cid this row belongs to
    time: float               # time-in-corner (s)
    delta: float              # time vs the reference lap's same corner (s; 0 for the ref)
    apex_speed: float         # MIN speed inside the window (km/h)
    apex_speed_delta: float   # vs the reference lap's apex speed (km/h; 0 for the ref)
    apex_dist: float          # THIS lap's odometer (m) at the min-speed sample
    entry_speed: float        # speed at the corner-enter boundary (km/h)
    exit_speed: float         # speed at the corner-exit boundary (km/h)


# ------------------------------------------------------------------- curvature profile
def lap_curvature(xs, ys, dists) -> np.ndarray:
    """Signed curvature kappa(s) (1/m, + = left) of one lap's local-frame trace: unwrapped
    heading differentiated vs arc length, boxcar-smoothed over KAPPA_SMOOTH_M of arc.
    `dists` must be strictly increasing (dedupe stationary samples first)."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    dists = np.asarray(dists, float)
    heading = np.unwrap(np.arctan2(np.gradient(ys, dists), np.gradient(xs, dists)))
    kappa = np.gradient(heading, dists)
    ds = float(np.median(np.diff(dists)))
    w = max(int(round(KAPPA_SMOOTH_M / max(ds, 1e-9))), 1)
    return _smooth(kappa, w)


def lap_yaw_rate(xs, ys, dists, times, kappa=None) -> np.ndarray:
    """Path-derived body yaw rate dtheta/dt (rad/s, + = left): `lap_curvature`'s kappa carried
    along the trace at the trace's OWN speed, ds/dt. The rotation-rate form of the curvature
    channel, and the thing a measured gyro must be compared against.

    THE BASIS IS THE WHOLE POINT. kappa is dtheta/ds against `dists` — this lap's gap-aware
    odometer — so the only speed that turns it back into a rate is that same odometer's
    derivative. Then, over one closed lap,

        int kappa (ds/dt) dt  ==  int kappa ds  ==  the lap's heading change  ==  exactly 2*pi

    which is an exact, fixture-independent target (tests/test_corners.py asserts it). Reaching
    for the GPS Doppler speed instead multiplies that by the ratio of Doppler distance to
    odometer distance — and that ratio is neither 1 nor constant. Measured per segment on the
    D24 recordings it climbs with |kappa|: 0.99 on the straights, 1.03 through a mid-speed
    corner, 1.07-1.08 in the tightest, because the GPS POSITION trace rounds corners off (the
    13-sample load-time boxcar, and the receiver's own filtering under it) while the Doppler
    speed does not. Weighted by kappa that inflated a lap's rotation by +10.1 % / +6.4 %, of
    which only +1.9 % / +2.1 % was the uniform odometer deficit and the rest was
    corner-concentrated. This form reads 1.0007 x 2*pi on the same laps — 20x closer to exact
    than the measured gyro itself (0.983 / 0.975).

    `xs`, `ys`, `dists` are `lap_curvature`'s inputs (strictly increasing `dists`); `times` is
    the media clock, same length. `kappa` is that lap's curvature profile already computed —
    pass it when you need BOTH (rotation._path_reference does) rather than paying for the
    profile twice, which is 21.7 ms over the 65 laps of the D24 0062 recording."""
    dists = np.asarray(dists, float)
    times = np.asarray(times, float)
    if kappa is None:
        kappa = lap_curvature(xs, ys, dists)
    elif len(kappa) != len(dists):
        # A profile from a DIFFERENT lap would otherwise multiply through silently; the lengths
        # are the cheap half of that check and the only half a caller can get wrong by accident.
        raise ValueError(f"kappa has {len(kappa)} samples, the lap has {len(dists)}")
    return np.asarray(kappa, float) * np.gradient(dists, _monotonic(times))


def _monotonic(t: np.ndarray) -> np.ndarray:
    """`t` with every non-positive gap replaced by the median positive one. A chapter seam
    clamps the time axis monotonic (load._gps9_times -> maximum.accumulate), leaving a
    duplicated instant that np.gradient would divide by; the sibling guard in
    `_signal.speed_long_g` repairs the same axis the same way. A no-op on clean input."""
    if len(t) < 2:
        return t
    dt = np.diff(t)
    if not (dt <= 0).any():
        return t
    pos = dt[dt > 0]
    med = float(np.median(pos)) if pos.size else 1.0
    return np.concatenate([t[:1], t[:1] + np.cumsum(np.where(dt <= 0, med, dt))])


def pooled_curvature(traces, total_ref: float):
    """The track's curvature profile on the reference lap's odometer grid: the MEDIAN of
    the per-lap kappa profiles, aligned by normalized distance (same fraction = same track
    position — the projection identity the whole feature rests on).

    `traces` is an iterable of (xs, ys, cum_dists) triples, one per clean lap (the caller
    passes the session's valid, dropout-free laps; a single trace degrades to that lap's own
    profile). Returns (grid_dists, kappa) with grid_dists spanning [0, total_ref]."""
    n = max(int(round(float(total_ref) / GRID_STEP_M)), 16)
    s_grid = np.linspace(0.0, 1.0, n)
    profiles = []
    for xs, ys, cum in traces:
        xs = np.asarray(xs, float)
        ys = np.asarray(ys, float)
        cum = np.asarray(cum, float)
        keep = np.concatenate(([True], np.diff(cum) > 1e-9))  # drop stationary duplicates
        xs, ys, cum = xs[keep], ys[keep], cum[keep]
        if len(cum) < 8 or cum[-1] <= 0:
            continue
        k = lap_curvature(xs, ys, cum)
        if not np.all(np.isfinite(k)):
            continue
        profiles.append(np.interp(s_grid, cum / cum[-1], k))
    if not profiles:
        return s_grid * float(total_ref), np.zeros(n)
    return s_grid * float(total_ref), np.median(np.vstack(profiles), axis=0)


def derive_threshold(kappa) -> float:
    """Corner/straight |kappa| split via Otsu (max between-class variance) on log10|kappa| — no
    magic constant. Log domain because |kappa| has two log-separated modes (straight noise floor
    vs corners); in linear space the corner mode's long tail destabilises Otsu inside the corner
    mode itself, while log space lands the split mid-valley and stable. Detection is insensitive to
    the exact value (corner set unchanged for a 0.8×..1.25× scaling)."""
    a = np.log10(np.maximum(np.abs(np.asarray(kappa, float)), LOG_KAPPA_FLOOR))
    hist, edges = np.histogram(a, bins=128)
    p = hist.astype(float) / max(hist.sum(), 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(p)               # class-0 (straighter) weight
    w1 = 1.0 - w0                   # class-1 (cornering) weight
    mu = np.cumsum(p * centers)     # class-0 first moment
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * w0 - mu) ** 2 / (w0 * w1)  # between-class variance per split
    between[~np.isfinite(between)] = 0.0
    return float(10.0 ** centers[int(np.argmax(between))])


# ----------------------------------------------------------------------- detection
def detect_corners(dists, kappa, threshold: float | None = None) -> list[Corner]:
    """Corner list (track order, C1 first) from a curvature profile on an odometer grid.

    Pipeline: hysteresis spans on |kappa| (enter at `threshold`, extend while >= ratio x
    threshold), split at kappa sign changes so an S-complex yields one corner per direction,
    keep only parts that actually reach the threshold, re-merge adjacent same-direction
    parts within MERGE_GAP_M (jitter fragments, not real straights), then drop spans
    shorter than MIN_SPAN_M or turning less than MIN_TURN_DEG (sub-corner kinks).

    A lap is treated LINEARLY [0, total]: the timing line is conventionally on a straight,
    so a corner is not expected to straddle the start/finish seam; if the line does sit in
    an arc, the arc shows as a corner at each end of the lap (consistently across laps)."""
    dists = np.asarray(dists, float)
    kappa = np.asarray(kappa, float)
    if len(dists) < 3:
        return []
    hi = derive_threshold(kappa) if threshold is None else float(threshold)
    lo = hi * HYSTERESIS_RATIO
    a = np.abs(kappa)
    n = len(a)

    # 1. hysteresis spans (index-inclusive [j0, j1]) seeded wherever |kappa| >= hi.
    spans: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if a[i] >= hi:
            j0, j1 = i, i
            while j0 > 0 and a[j0 - 1] >= lo:
                j0 -= 1
            while j1 + 1 < n and a[j1 + 1] >= lo:
                j1 += 1
            spans.append((j0, j1))
            i = j1 + 1
        else:
            i += 1

    # 2. split each span at kappa sign changes (S-complex -> one part per direction)…
    parts: list[tuple[int, int]] = []
    for j0, j1 in spans:
        sgn = np.sign(kappa[j0:j1 + 1])
        cuts = np.flatnonzero(np.diff(sgn) != 0)
        lo_i = j0
        for c in cuts:
            parts.append((lo_i, j0 + int(c)))
            lo_i = j0 + int(c) + 1
        parts.append((lo_i, j1))
    # …keeping only parts that genuinely reach the seed threshold (a sign-flip sliver that
    # only ever sat between lo and hi is jitter, not a corner of its own).
    parts = [(j0, j1) for j0, j1 in parts
             if j1 >= j0 and float(np.max(a[j0:j1 + 1])) >= hi]

    def _dir(j0: int, j1: int) -> int:
        seg = kappa[j0:j1 + 1]
        return 1 if seg[int(np.argmax(np.abs(seg)))] > 0 else -1

    # 3. re-merge ADJACENT same-direction parts separated by less than MERGE_GAP_M.
    merged: list[tuple[int, int]] = []
    for p in parts:
        if merged and _dir(*merged[-1]) == _dir(*p) and \
                dists[p[0]] - dists[merged[-1][1]] <= MERGE_GAP_M:
            merged[-1] = (merged[-1][0], p[1])
        else:
            merged.append(p)

    # 4. length + turn-angle filters, apex/direction extraction.
    out: list[Corner] = []
    for j0, j1 in merged:
        dd = dists[j0:j1 + 1]
        if dd[-1] - dd[0] < MIN_SPAN_M:
            continue
        seg = kappa[j0:j1 + 1]
        turn = float(np.degrees(abs(np.trapezoid(seg, dd))))  # integral of kappa ds = angle
        if turn < MIN_TURN_DEG:
            continue
        w = np.abs(seg)
        apex = float(np.sum(dd * w) / np.sum(w))  # |kappa|-weighted centroid (see module doc)
        out.append(Corner(cid=len(out) + 1, enter=float(dd[0]), exit=float(dd[-1]),
                          apex=apex, direction=_dir(j0, j1), turn_deg=turn))
    return out


# ---------------------------------------------------------------------- projection
def _window_edges(corner_list: list[Corner], total_ref: float, total_lap: float,
                  traces: tuple | None = None,
                  alignment=DERIVE_ALIGNMENT) -> np.ndarray:
    """All partition edges (lap odometer metres) for one lap: lap start, each corner's
    enter/exit projected onto the lap's odometer (one monotone spatial warp for the whole lap; see
    project_boundaries), and the lap
    end. The lap start/end stay the literal 0 / total_lap — the timing line is the shared S/F point
    on both laps, which is exactly why it is also the warp's two anchors. `traces` (Session-fed)
    enables the spatial alignment; `alignment` passes that lap's warp in already built
    (`corner_model.CornerModel.lap_alignment` memoizes it)."""
    interior = []
    for c in corner_list:
        interior.extend((c.enter, c.exit))
    edges = [0.0]
    if interior:
        edges.extend(project_boundaries(interior, total_ref, total_lap, traces=traces,
                                        alignment=alignment).tolist())
    edges.append(float(total_lap))
    return np.asarray(edges, float)


def segment_times(corner_list: list[Corner], total_ref: float, dists, elapsed,
                  traces: tuple | None = None,
                  alignment=DERIVE_ALIGNMENT) -> np.ndarray:
    """Per-segment times of the corner/straight partition: 2N+1 entries [straight0, corner1, ...].
    One np.interp at the shared edges, so segments sum to the lap time exactly (asserted). `traces`
    (Session-fed) enables the spatial alignment; omitted → the normalized projection.
    `alignment` is that lap's warp already built — pass it (see project_boundaries) rather than
    paying for the spatial match again.

    The sum holding does NOT mean the pieces are comparable across laps: it held throughout the
    projection defect project_boundaries documents, because a shrunken segment pushes its time into
    its neighbour. `corner_model.MAX_DONOR_SPAN_DEV` is where per-piece comparability is checked."""
    dists = np.asarray(dists, float)
    elapsed = np.asarray(elapsed, float)
    edges = _window_edges(corner_list, total_ref, float(dists[-1]), traces, alignment)
    t_at = np.interp(edges, dists, elapsed)
    seg = np.diff(t_at)
    assert abs(float(seg.sum()) - float(elapsed[-1] - elapsed[0])) < 1e-9, \
        "corner/straight partition does not sum to the lap time"
    return seg


def lap_corner_stats(corner_list: list[Corner], total_ref: float, dists, speed_kmh,
                     elapsed, ref: list[CornerStat] | None = None,
                     traces: tuple | None = None,
                     alignment=DERIVE_ALIGNMENT) -> list[CornerStat]:
    """Project the corner windows onto ONE lap and measure each corner: time-in-corner
    (from the same edge interpolation as segment_times, so corner times + straight times
    partition the lap exactly), apex = MIN speed over the in-window samples (+ its lap
    odometer position), entry/exit speeds at the window edges, and deltas vs `ref` (the
    reference — best — lap's own stats; None for the reference lap itself -> deltas 0).

    The window-boundary projection is one monotone spatial warp for the whole lap
    (project_boundaries). `traces` (Session-fed (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum))
    enables that alignment; omitted (pure-numpy callers) → the normalized projection.
    `alignment` is this lap's warp already built — WITHOUT it this function derives the same warp
    TWICE (once for the partition, once for the window edges below)."""
    dists = np.asarray(dists, float)
    speed_kmh = np.asarray(speed_kmh, float)
    elapsed = np.asarray(elapsed, float)
    total_lap = float(dists[-1])
    # Derive the lap's warp ONCE here when the caller did not supply it, so the two projections
    # below share it instead of each running the lap's spatial match.
    if alignment is DERIVE_ALIGNMENT:
        frame = [b for c in corner_list for b in (c.enter, c.exit)]
        alignment = lap_alignment(frame, total_ref, total_lap, traces=traces) if frame else None
    seg = segment_times(corner_list, total_ref, dists, elapsed, traces, alignment)
    # The same projected enter/exit boundaries segment_times partitions on, so the in-window apex/edge
    # speeds read off the identical window (interleaved [enter1, exit1, enter2, exit2, …]).
    interior = []
    for c in corner_list:
        interior.extend((c.enter, c.exit))
    proj = (project_boundaries(interior, total_ref, total_lap, traces=traces,
                               alignment=alignment)
            if interior else np.empty(0))
    out: list[CornerStat] = []
    for i, c in enumerate(corner_list):
        d0 = float(proj[2 * i])
        d1 = float(proj[2 * i + 1])
        t = float(seg[2 * i + 1])  # this corner's slice of the partition
        idx = np.flatnonzero((dists >= d0) & (dists <= d1))
        if len(idx):
            j = idx[int(np.argmin(speed_kmh[idx]))]
            apex_speed = float(speed_kmh[j])
            apex_dist = float(dists[j])
        else:  # window narrower than the sample spacing — fall back to the midpoint
            apex_dist = (d0 + d1) / 2.0
            apex_speed = float(np.interp(apex_dist, dists, speed_kmh))
        r = ref[i] if ref is not None and i < len(ref) else None
        out.append(CornerStat(
            cid=c.cid, time=t,
            delta=t - r.time if r is not None else 0.0,
            apex_speed=apex_speed,
            apex_speed_delta=apex_speed - r.apex_speed if r is not None else 0.0,
            apex_dist=apex_dist,
            entry_speed=float(np.interp(d0, dists, speed_kmh)),
            exit_speed=float(np.interp(d1, dists, speed_kmh)),
        ))
    return out
