"""Reference track centerline — the fallback gap-fill source.

Used only where no measured lap covers a gap section. The stored normalized best-lap loop is
aligned to the session's own best-lap loop; both are closed loops, so alignment is a closed-loop
cyclic-arc-length similarity fit (details in fit_loop_to_loop).

The stored polyline is ONE circuit's shape (Daytona Milton Keynes — see mk_centerline.json),
so it is a legitimate donor on that circuit and on nothing else. The fit itself cannot refuse:
a similarity transform resizes and rotates whatever it is given until it sits on the loop, and
then reports a residual. So the fit's own quality is the ADMISSION TEST — `centerline_local`
returns the ring only when `fit_is_this_circuit` says the shape actually matches (see there).

Pure python + numpy + a stored polyline (no pacer). `centerline_local` takes the session's
best-lap loop and returns the centerline in LOCAL metres as an (M,2) array (or empty).
"""

from __future__ import annotations

import json
import os

import numpy as np

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "mk_centerline.json")

# A lap point within this distance of the fitted reference counts as "covered" — generous
# vs the ~8 m kart-track width + the hand-trace error, tight vs the ~60 m infield spacing,
# so a collapsed/mis-fit reference scores low while a correct fit scores ~100 %.
COVERAGE_TOL_M = 10.0
# Resampled correspondence points for the global cyclic search (offset granularity is
# track_length/N ≈ 2.5 m here) and for the returned polyline.
_N_FIT = 512
_N_OUT = 600

# --- the admission test: is the loop we were handed THIS circuit? -------------------------
# Both halves are load-bearing, and neither is a scale test — see the long note below.
#
# COVERAGE is the shape-COMPLETENESS half: the fraction of the lap's own points that land
# within COVERAGE_TOL_M of the fitted ring. A foreign circuit can be pressed onto a loop so its
# busiest parts line up, but the parts that have no counterpart stick out and score nothing.
# RMS is the CLOSENESS half: how far the typical lap point sits from the ring, in metres.
#
# MEASURED, in the app, over 91 fits of the shipped polyline (studio/reference.py's own
# fit_loop_to_loop, on the loop LapRenderCache.reference_fit_loop would hand it):
#   * 65 real Daytona MK laps — every valid lap of GX010062 (21) and of the 3-chapter
#     GX010062+63+64 (65), i.e. the same circuit at two different lap counts:
#         coverage 100.0 % on EVERY lap;  RMS 0.57 – 1.45 m
#   * 26 laps of two other circuits — Sandown (1 valid lap) and a 53 x 67 m unnamed kart track
#     (25 valid laps):
#         coverage 71.9 % / 82.4 – 97.1 %;  RMS 9.40 m / 5.11 – 6.35 m
# The two populations do not overlap on EITHER half, so both thresholds sit in a gap:
#   coverage 0.98 — 2 pp under the real laps (which are all exactly 1.000, so this tolerates
#     ~2 % of a lap's points being a genuine off-line excursion) and 0.9 pp over the best
#     foreign lap. Note 0.95 would NOT do: one foreign lap fits at 97.1 %.
#   RMS 3.0 m — 2.1x the worst real lap and 1.7x under the best foreign one (their geometric
#     midpoint is 2.72 m). Absolute metres are the right unit precisely BECAUSE this file
#     stores a single 210 m circuit: any loop the donor may legitimately serve is that circuit
#     at that size, so the tolerance never has to stretch to a bigger track. The floor is set
#     by the storage format, not the fit — points_norm is normalized per AXIS to a unit box,
#     which stretches the ~211 x 204 m source loop by ~3.7 % in one direction, and a SIMILARITY
#     fit cannot undo anisotropy. That is why the real laps sit near 1 m and not near 0.
# All 26 foreign laps fail BOTH halves, and all 65 real ones pass both.
FIT_COVERAGE_MIN = 0.98
FIT_RMS_TOL_M = 3.0
#
# WHY THERE IS NO SCALE TEST HERE, unlike cross_reference.fit_is_drawable.
# There the two loops are both in METRES, so a true fit has scale 1.0 and the fit has nothing
# legitimate to resize — a wrong SIZE is provable, and MAP_FIT_SCALE_TOL proves it. Here the
# stored polyline is normalized into a [0,1] box: the metric size of the circuit is not merely
# absent from mk_centerline.json, it was destroyed when the file was written. So there is no
# true scale to compare a fitted one against, and the only available yardstick — the session
# loop's own extent — is not a yardstick at all, because the fit CHOOSES the scale that makes
# the ring the size of that loop. Measured on the same 91 fits: fitted-scale / loop-extent is
# 0.971 – 0.985 on the real laps and 0.911 – 0.947 on the foreign ones, i.e. near 1 for every
# circuit and only 2.5 % apart at the boundary. A gate there would have to be tighter than the
# real laps' own spread and would still admit foreign geometry. Size cannot arbitrate; shape
# can, and does. (Storing the source loop's true metres would make a scale test possible, but
# that is a change to the shipped data asset, not to this gate.)


def fit_is_this_circuit(rms: float | None, coverage: float | None) -> bool:
    """True iff a `fit_loop_to_loop` result may be treated as "the stored circuit, matched" —
    close enough (RMS, metres) AND complete enough (coverage fraction). Both halves are
    load-bearing and neither is a size test; the constants above carry the measurements.
    A non-finite or missing input is never a match."""
    if rms is None or coverage is None:
        return False
    if not (np.isfinite(rms) and np.isfinite(coverage)):
        return False
    return bool(rms <= FIT_RMS_TOL_M and coverage >= FIT_COVERAGE_MIN)


def _load_normalized():
    """The stored centerline as a normalized (M,2) polyline, or None if no data file."""
    if not os.path.exists(_DATA):
        return None
    with open(_DATA) as fh:
        d = json.load(fh)
    pts = np.asarray(d.get("points_norm", []), float)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
        return None
    return pts


def _similarity_fit(src, dst):
    """Best-fit similarity (rotation+uniform scale+translation, reflection allowed) mapping
    `src` onto `dst` (both (K,2), point-correspondence assumed). Umeyama closed form."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    u, s, vt = np.linalg.svd(cov)
    S = np.eye(2)
    R = u @ S @ vt
    var_s = (xs ** 2).sum() / len(src)
    scale = (s * np.diag(S)).sum() / var_s if var_s > 0 else 1.0
    t = mu_d - scale * R @ mu_s
    return scale, R, t


def _resample_closed(xy, n):
    """Resample a CLOSED loop uniformly by arc length to n points (no duplicate endpoint).

    The loop is closed before measuring (the final segment back to the start counts), so a
    polyline whose last point isn't a repeat of the first still parameterizes the full ring.
    """
    xy = np.asarray(xy, float)
    if np.hypot(*(xy[-1] - xy[0])) > 1e-12:
        xy = np.vstack([xy, xy[:1]])
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    if d[-1] <= 0:
        return np.repeat(xy[:1], n, axis=0)
    s = np.arange(n) * (d[-1] / n)
    return np.column_stack([np.interp(s, d, xy[:, 0]), np.interp(s, d, xy[:, 1])])


def _dist_to_polyline(pts, poly):
    """Min Euclidean distance from each of `pts` (P,2) to the polyline `poly` (Q,2) —
    true point-to-SEGMENT distance, vectorized over all P×(Q-1) pairs."""
    pts = np.asarray(pts, float)
    a, b = poly[:-1], poly[1:]
    ab = b - a                                            # (S,2)
    ab2 = np.maximum((ab ** 2).sum(1), 1e-12)             # (S,)
    ap = pts[:, None, :] - a[None]                        # (P,S,2)
    t = np.clip((ap * ab[None]).sum(-1) / ab2[None], 0.0, 1.0)
    closest = a[None] + t[..., None] * ab[None]
    return np.sqrt(((pts[:, None, :] - closest) ** 2).sum(-1)).min(1)


def _close_ring(xy):
    """Append the first point so the returned polyline draws as a closed ring."""
    return np.vstack([xy, xy[:1]])


def fit_loop_to_loop(ref_xy, loop_xy, n=_N_FIT, icp_iters=8):
    """Fit the closed reference loop `ref_xy` onto the closed measured loop `loop_xy` (both
    (K,2), any scale/frame) by a similarity transform.

    Global search over cyclic offset × direction (scored by the closed-form similarity residual),
    then a nearest-point ICP polish — but every candidate is accepted only by the reported
    lap->ref RMS, so the polish can't trade footprint coverage for nearest-point comfort.

    Returns `(fitted, info)`: `fitted` is the reference as a closed ring in the measured frame;
    `info` has `rms` (m), `coverage`, `scale`, `R`, `t`, `offset_frac`, `reversed`.
    """
    ref_xy = np.asarray(ref_xy, float)
    loop_xy = np.asarray(loop_xy, float)
    ref_n = _resample_closed(ref_xy, n)
    lap_n = _resample_closed(loop_xy, n)

    best = None  # (residual_rms, scale, R, t, offset, reversed)
    for rev in (False, True):
        cand = ref_n[::-1] if rev else ref_n
        for k in range(n):
            src = np.roll(cand, -k, axis=0)
            scale, R, t = _similarity_fit(src, lap_n)
            res = (scale * src @ R.T + t) - lap_n
            rms = float(np.sqrt((res ** 2).sum(1).mean()))
            if best is None or rms < best[0]:
                best = (rms, scale, R, t, k, rev)
    _, scale, R, t, k, rev = best

    # The transform was solved on the rolled/reversed resampling; it applies to the
    # reference as a SET, so carry it over to the canonical resampled reference directly.
    ref_out = _resample_closed(ref_xy, _N_OUT)
    dense = _resample_closed(loop_xy, max(4 * n, 2048))

    def _apply(sc, rot, tr):
        return sc * ref_out @ rot.T + tr

    def _score(fitted):
        d = _dist_to_polyline(loop_xy, _close_ring(fitted))
        return float(np.sqrt((d ** 2).mean())), float((d <= COVERAGE_TOL_M).mean())

    fitted = _apply(scale, R, t)
    rms, cov = _score(fitted)
    win = (rms, cov, scale, R, t, fitted)

    # --- ICP polish, accepted only by the reported lap→reference metric ---
    cur = fitted
    for _ in range(icp_iters):
        d2 = ((cur[:, None, 0] - dense[None, :, 0]) ** 2
              + (cur[:, None, 1] - dense[None, :, 1]) ** 2)
        nn = dense[np.argmin(d2, axis=1)]
        sc_i, R_i, t_i = _similarity_fit(ref_out, nn)
        cur = _apply(sc_i, R_i, t_i)
        rms_i, cov_i = _score(cur)
        if rms_i < win[0]:
            win = (rms_i, cov_i, sc_i, R_i, t_i, cur)

    rms, cov, scale, R, t, fitted = win
    info = {"rms": rms, "coverage": cov, "scale": float(scale), "R": R,
            "t": np.asarray(t, float), "offset_frac": k / n, "reversed": rev}
    return _close_ring(fitted), info


def centerline_local(loop_xy):
    """Return the reference centerline in LOCAL metres as an (M,2) closed ring — empty if
    unavailable OR if the loop we were handed is not this circuit. `loop_xy` is the session's
    BEST-LAP loop (ordered local-metre points): a closed curve, so the stored loop is aligned
    to it by cyclic arc-length correspondence (see `fit_loop_to_loop`), and the fit is then put
    through `fit_is_this_circuit` before anything is handed back.

    An empty return is the whole refusal: `LapRenderCache.donors_for` simply stops offering the
    donor, and `gapfill` bridges the gap with a Catmull-Rom spline through the neighbouring
    measured points — drawn dashed and dimmed like every other inferred fill. So a rejection
    costs a nicer-looking fill, never a silent wrong shape, and needs no message of its own in
    the UI: on a foreign track today there is nothing to say, because nothing is drawn. The
    verdict and the numbers behind it still go to the console, where the fit already reported."""
    norm = _load_normalized()
    if norm is None or loop_xy is None or len(loop_xy) < 10:
        return np.empty((0, 2))
    fitted, info = fit_loop_to_loop(norm, loop_xy)
    ok = fit_is_this_circuit(info["rms"], info["coverage"])
    print(f"[reference] MK centerline fit: RMS {info['rms']:.1f} m, "
          f"{info['coverage']:.0%} of best-lap points within {COVERAGE_TOL_M:.0f} m — "
          + ("accepted as a gap-fill donor"
             if ok else
             f"REJECTED, not this circuit (needs RMS <= {FIT_RMS_TOL_M:.0f} m and "
             f"coverage >= {FIT_COVERAGE_MIN:.0%})"),
          flush=True)
    return fitted if ok else np.empty((0, 2))
