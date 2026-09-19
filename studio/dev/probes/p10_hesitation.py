"""P10 — the hesitation metric (lift-to-brake gap per corner), measured (M5.1).

THE IDEA (roadmap LATER, "Hesitation metric"). Between lifting off the throttle and getting on the
brake there is a gap. A driver who dithers there loses time nobody can see on a lap chart, so
publish that gap per corner.

THE QUESTION THIS PROBE ASKS FIRST IS NOT WHETHER THE GAP PREDICTS ANYTHING. It is whether a 10 Hz
GPS speed trace can measure it at all. Both ends of the gap are read off ONE series — the session's
own `_signal.speed_long_g` on the lap's native ~10 Hz grid, the same series `driving.brake_events`
runs on — so the gap inherits that grid's 0.1 s quantum and that derivative's 0.10-0.12 g of noise.

  * THE MEASUREMENT. For every (lap x corner) the shipped rule attaches a brake event to
    (`lap_brake_points`' window: the LAST onset inside [enter - BRAKE_MATCH_LEAD_M, exit]), the gap
    is `onset - lift`, where the lift is the last sample before the onset that was not decelerating
    past `driving.COAST_DRAG_MIN`. Read on the brake detector's own bare series AND on the coast
    channel's 0.50 s series, because the app already ships two windows over this signal and they do
    not agree. The backlog's literal wording — "the end of the app's own coast span to the brake
    onset" — is measured too, and it is a third number again.
  * THE NULL IT MUST BEAT: the instrument's own floor. The same estimator is run over a synthetic
    approach with a PLANTED gap of exactly zero, built from this recording's own entry speeds, drag
    decel, brake ramp and — bootstrapped in contiguous blocks — this recording's own speed-trace
    noise. If the measured distribution is not separable from the planted-zero one, the channel is
    reporting its own sampling floor and there is nothing to publish.
  * THE RESOLUTION. The same simulation at planted gaps from 0 to 0.8 s says what the channel can
    and cannot resolve: bias, spread, and the smallest planted gap whose estimate clears the
    planted-zero p95.
  * THE POWER. Even where the gap were real, can a session see it pay? The probe plants the
    strongest physically honest effect — every second of hesitation costing its own second in the
    corner — into the real corner times and reports how often each test finds it.
  * THE SIGNAL TEST, run whatever the null says, so the verdict has both halves: per-corner
    Spearman of the gap against the corner's time across laps, with the lap-pace confound removed
    (#265), a max-statistic family-wise correction, a pre-specified pooled statistic, and a
    selection control for "the top corner" (#311 measured that rule firing 19-21 % on shuffled
    data, not 5 %).

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p10_hesitation
    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p10_hesitation MK_18_09

SAFETY: every footage folder is read-only and is snapshotted (size + mtime) around each load by
`_m5_cache`; the app-support seams are diverted before any studio import resolves one, and the real
app-support directory is snapshotted around the whole run. The probe writes no file of its own.
"""

from __future__ import annotations

import sys

import numpy as np

from studio import driving
from studio._signal import speed_long_g
from studio.dev.probes import _m5_cache as C
from studio.dev.probes import _m5_stats as S

LIFT_G = driving.COAST_DRAG_MIN     # g; above this decel the car is off power (the coast band floor)
ABUT_S = 0.50                       # s; a coast span "abuts" a brake if it ends within this of it
PERMS = 20_000
SEL_OUTER, SEL_INNER = 300, 1_000
PLANT_BETA = (1.0, 0.5)             # s of corner time per s of gap (1.0 = the physical ceiling)
PLANT_MEAN = 0.30                   # s; the planted hesitation a driver would be told about
PLANT_SD = (0.15, 0.30)             # s; its lap-to-lap spread
PLANT_REPS = 200
SIM_N = 600                         # synthetic approaches per planted gap
PLANTED_GAPS = (0.0, 0.1, 0.2, 0.3, 0.5, 0.8)
ALPHA = 0.05


# ------------------------------------------------------------------------------ the measurement
def _lift_gap(series, elapsed, onset_t: float) -> tuple[float, int]:
    """(gap, samples off power) for one brake onset on one series.

    The onset is a SAMPLE time (that is what `driving.brake_events` reports), and the lift is
    bracketed by two samples — the last on-power one and the first decelerating one — so the
    crossing is placed at their midpoint. With no decelerating sample at all the gap is half a
    sample, which is the smallest number this instrument can return."""
    i0 = int(np.argmin(np.abs(elapsed - onset_t)))
    j = i0
    while j > 0 and series[j - 1] < -LIFT_G:
        j -= 1
    if j == 0:
        return float("nan"), 0
    t_lift = 0.5 * (elapsed[j - 1] + elapsed[j])
    return float(onset_t - t_lift), int(i0 - j)


def measure(d: dict) -> dict:
    """The per-(lap x corner) gap matrices, on both series, plus the literal coast-span reading."""
    n_l, n_c = d["time"].shape
    bare = np.full((n_l, n_c), np.nan)
    smooth = np.full((n_l, n_c), np.nan)
    coast_gap = np.full((n_l, n_c), np.nan)
    abut = np.zeros((n_l, n_c), bool)
    onset_v, peak_g, ramp_s, drag_g, off_n = [], [], [], [], []
    for li in range(n_l):
        dist, spd, el = d["dist"][li], d["speed"][li], d["elapsed"][li]
        g = speed_long_g(spd, el)
        gs = driving.boxcar(g, driving._coast_window(el))
        for ci in range(n_c):
            lo = d["win_enter"][li, ci] - d["brake_match_lead_m"]
            hi = d["win_exit"][li, ci]
            ev = [e for e in d["brakes"][li] if lo <= e[0] <= hi]
            if not ev:
                continue
            onset_d, onset_t, peak, dur = ev[-1]
            gap, n_off = _lift_gap(g, el, onset_t)
            bare[li, ci] = gap
            smooth[li, ci] = _lift_gap(gs, el, onset_t)[0]
            off_n.append(n_off)
            # The literal backlog wording: the app's own coast span nearest before this onset.
            ends = [c[1] for c in d["coasts"][li] if c[1] <= onset_d + 1e-9]
            if ends:
                t_end = float(np.interp(max(ends), dist, el))
                coast_gap[li, ci] = onset_t - t_end
                abut[li, ci] = (onset_t - t_end) <= ABUT_S
            # context for the simulation: what an approach into this corner actually looks like
            i0 = int(np.argmin(np.abs(el - onset_t)))
            onset_v.append(float(spd[i0]) / 3.6)
            peak_g.append(float(peak))
            seg = (el >= onset_t) & (el <= onset_t + dur)
            if seg.any():
                ramp_s.append(float(el[seg][int(np.argmin(g[seg]))] - onset_t))
            if n_off > 0:
                drag_g.append(float(-np.median(g[i0 - n_off:i0])))
    return {"bare": bare, "smooth": smooth, "coast_gap": coast_gap, "abut": abut,
            "onset_v": np.asarray(onset_v), "peak_g": np.asarray(peak_g),
            "ramp_s": np.asarray(ramp_s), "drag_g": np.asarray(drag_g),
            "off_n": np.asarray(off_n)}


# --------------------------------------------------------------------------- the instrument null
def speed_noise(d: dict) -> tuple[float, np.ndarray]:
    """(sigma_v, the residual pool) — the recording's own speed-trace noise, as a local-quadratic
    residual over every clean lap. The pool keeps each lap's residual series intact so the
    simulation can bootstrap CONTIGUOUS blocks and inherit the noise's own autocorrelation."""
    pool = []
    for spd in d["speed"]:
        v = np.asarray(spd, float) / 3.6
        if len(v) < 9:
            continue
        # local quadratic over 5 samples, evaluated at the centre (Savitzky-Golay weights)
        w = np.array([-3.0, 12.0, 17.0, 12.0, -3.0]) / 35.0
        fit = np.convolve(v, w, mode="same")
        r = (v - fit)[2:-2]
        pool.append(r)
    allr = np.concatenate(pool)
    return float(np.std(allr, ddof=1)), pool


def _approach(t, v0, a_th, a_drag, gap, a_peak, ramp):
    """True speed (m/s) of one approach: on power, then `gap` seconds of drag, then a brake ramp."""
    G = 9.80665
    t_l, t_b = 0.0, float(gap)
    v = np.empty_like(t)
    pre = t < t_l
    v[pre] = v0 + a_th * G * (t[pre] - t_l)
    mid = (t >= t_l) & (t < t_b)
    v[mid] = v0 - a_drag * G * (t[mid] - t_l)
    v_b = v0 - a_drag * G * (t_b - t_l)
    rmp = (t >= t_b) & (t < t_b + ramp)
    dt_ = t[rmp] - t_b
    v[rmp] = v_b - G * (a_drag * dt_ + 0.5 * (a_peak - a_drag) * dt_ ** 2 / max(ramp, 1e-9))
    v_r = v_b - G * (a_drag * ramp + 0.5 * (a_peak - a_drag) * ramp)
    post = t >= t_b + ramp
    v[post] = v_r - G * a_peak * (t[post] - t_b - ramp)
    return np.maximum(v, 5.0)


def resolution_sim(d: dict, m: dict, rng, *, noise_scale: float = 1.0) -> dict:
    """Run the SHIPPED estimator over synthetic approaches with a known gap planted in them.

    `noise_scale=0` is the probe's own negative control: with the recording's noise switched off
    the estimator must recover the planted gap, or the simulation — not the channel — is broken."""
    theta_b = d["theta_b"]
    v0 = float(np.median(m["onset_v"]))
    a_peak = float(np.median(m["peak_g"]))
    ramp = max(float(np.median(m["ramp_s"])), 0.1)
    a_drag = float(np.median(m["drag_g"])) if len(m["drag_g"]) else 0.08
    a_th = 0.02
    _sigma, pool = speed_noise(d)
    dt = 0.1
    out = {}
    for gap in PLANTED_GAPS:
        est, est_sm, found = [], [], 0
        for _ in range(SIM_N):
            phase = float(rng.uniform(0, dt))
            t = np.arange(-3.0, gap + 2.5, dt) + phase
            v = _approach(t, v0, a_th, a_drag, gap, a_peak, ramp)
            if noise_scale:
                lap = pool[int(rng.integers(len(pool)))]
                while len(lap) < len(t):
                    lap = np.concatenate([lap, pool[int(rng.integers(len(pool)))]])
                k = int(rng.integers(0, len(lap) - len(t) + 1))
                v = v + noise_scale * lap[k:k + len(t)]
            el = t - t[0]
            dist = np.concatenate([[0.0], np.cumsum(np.diff(el) * 0.5 * (v[1:] + v[:-1]))])
            g = speed_long_g(v * 3.6, el)
            gs = driving.boxcar(g, driving._coast_window(el))
            evs = driving.brake_events(dist, el, g, theta_b)
            t_b_true = gap - t[0]
            near = [e for e in evs if abs(e.onset_time - t_b_true) <= 1.0]
            if not near:
                continue
            found += 1
            ev = min(near, key=lambda e: abs(e.onset_time - t_b_true))
            est.append(_lift_gap(g, el, ev.onset_time)[0])
            est_sm.append(_lift_gap(gs, el, ev.onset_time)[0])
        out[gap] = {"est": np.asarray(est, float), "est_sm": np.asarray(est_sm, float),
                    "found": found / SIM_N}
    return out


# ------------------------------------------------------------------------------ the signal tests
def complete_block(H, T, *, min_cover: float = 0.9):
    """The largest rectangular block of the (lap x corner) matrices with no missing cell.

    A corner needs a matched brake on at least `min_cover` of the laps to be in the test at all;
    the laps kept are then the ones complete across those corners. Rectangular on purpose: every
    statistic below permutes whole columns, and a ragged matrix would make each permutation a
    different test."""
    cover = np.isfinite(H).mean(axis=0)
    cols = (cover >= min_cover) & np.isfinite(T).all(axis=0)
    if cols.sum() < 2:
        return None
    rows = np.isfinite(H[:, cols]).all(axis=1)
    if rows.sum() < 8:
        return None
    return H[np.ix_(rows, cols)], T[np.ix_(rows, cols)], cols, rows


def signal_tests(name: str, H, T, rng) -> dict:
    """Does the gap predict the corner's time across laps? Pace-removed, corrected, controlled."""
    block = complete_block(H, T)
    if block is None:
        print(f"  {name}: no rectangular lap x corner block with a matched brake — no signal test")
        return {}
    Hc, Tc, cols, rows = block
    print(f"  test block: {rows.sum()} laps x {cols.sum()} corners "
          f"(corners {np.flatnonzero(cols).tolist()})")
    res = {}
    for label, X, Y, null in (("raw", Hc, Tc, "column"),
                              ("pace-removed", S.within_lap_centre(Hc),
                               S.within_lap_centre(Tc), "column")):
        r = S.perm_test(X, Y, PERMS, rng, null=null)
        res[label] = r
        top = int(np.nanargmax(np.abs(r["rho"])))
        print(f"  {label:<12} per-corner rho {np.round(r['rho'], 3).tolist()}; "
              f"pooled {r['pooled']:+.3f} (p {r['p_pooled']:.4f}); top corner "
              f"{top} rho {r['rho'][top]:+.3f} p_unc {r['p_unc'][top]:.4f} "
              f"p_fwer {r['p_fwer'][top]:.4f}")
    ctl = S.selection_control(Hc, Tc, outer=SEL_OUTER, inner=SEL_INNER, rng=rng)
    print(f"  selection control (nothing to find): any-corner-at-FWER {ctl['any_fwer']:.1%}, "
          f"TOP-corner-by-its-own-p {ctl['top_unc']:.1%}, pooled {ctl['pooled']:.1%} "
          f"(nominal {ALPHA:.0%})")
    res["control"] = ctl
    return res


def power(name: str, H, T, sim: dict, rng, *, field: str = "est") -> None:
    """END-TO-END power: plant a real hesitation, measure it through the instrument, then test.

    A power number computed on the MEASURED gap would answer the wrong question — it would say
    how well the test works given a perfect ruler. So the planted hesitation is passed through the
    simulation's own measurement model (the estimate distribution at the nearest planted gap) and
    the test is run on THAT, beside the same test run on the true gap. The difference between the
    two rows is what the instrument costs."""
    block = complete_block(H, T)
    if block is None:
        return
    Tc = block[1]
    n_l, n_c = Tc.shape
    buckets = np.asarray(sorted(sim), float)
    for sd in PLANT_SD:
        for beta in PLANT_BETA:
            hit_true = hit_meas = 0
            for _ in range(PLANT_REPS):
                h_true = np.clip(rng.normal(PLANT_MEAN, sd, size=(n_l, n_c)), 0.0, 1.2)
                planted = Tc + beta * (h_true - h_true.mean())
                idx = np.abs(h_true[..., None] - buckets).argmin(axis=-1)
                h_meas = np.empty_like(h_true)
                for b, gap in enumerate(buckets):
                    pool = sim[float(gap)][field]
                    sel = idx == b
                    h_meas[sel] = pool[rng.integers(0, len(pool), size=int(sel.sum()))]
                if S.perm_test(h_true, planted, 200, rng)["p_pooled"] < ALPHA:
                    hit_true += 1
                if S.perm_test(h_meas, planted, 200, rng)["p_pooled"] < ALPHA:
                    hit_meas += 1
            print(f"  POWER mean {PLANT_MEAN:.2f} s, lap-to-lap sd {sd:.2f} s, "
                  f"beta {beta:.1f} s per s: the pooled test fires on the TRUE gap "
                  f"{hit_true / PLANT_REPS:.0%}, on the MEASURED gap {hit_meas / PLANT_REPS:.0%} "
                  f"({PLANT_REPS} replicates, nominal {ALPHA:.0%})")


# ------------------------------------------------------------------------------------- reporting
def report(key: str, d: dict, rng) -> dict:
    n_l, n_c = d["time"].shape
    m = measure(d)
    bare, smooth = m["bare"], m["smooth"]
    got = np.isfinite(bare)
    dts = np.concatenate([np.diff(e) for e in d["elapsed"]])
    print(f"\n{'=' * 100}\n{key}: {n_l} laps x {n_c} corners, sample dt median "
          f"{np.median(dts):.4f} s, theta_b {d['theta_b']:.3f} g")
    print(f"  a brake event attaches to {got.sum()} of {n_l * n_c} cells "
          f"({got.mean():.0%}) by the shipped window rule")
    print(f"  gap, brake series (bare):  {S.describe(bare[got], '{:.3f}')} s; "
          f"distinct values {sorted(set(np.round(bare[got], 3).tolist()))[:6]}")
    print(f"  gap, coast series (0.50 s): {S.describe(smooth[got], '{:.3f}')} s")
    print(f"  the two series disagree by {S.describe(np.abs(smooth[got] - bare[got]), '{:.3f}')} s")
    print(f"  the backlog's literal reading (end of the app's own coast span -> onset): "
          f"{S.describe(m['coast_gap'][np.isfinite(m['coast_gap'])], '{:.2f}')} s; a coast span "
          f"ends within {ABUT_S:.2f} s of the onset on {m['abut'][got].mean():.0%} of them")
    print(f"  samples spent off power before the onset: "
          f"{S.describe(m['off_n'], '{:.1f}')} (one sample = {np.median(dts):.3f} s)")

    clean = resolution_sim(d, m, rng, noise_scale=0.0)
    print("  --- NEGATIVE CONTROL for the simulation itself: the same estimator with this "
          "recording's noise switched OFF")
    for gap, r in clean.items():
        print(f"    planted {gap:.1f} s -> estimated {S.describe(r['est'], '{:.3f}')} s "
              f"(bare series), {S.describe(r['est_sm'], '{:.3f}')} s (0.50 s series)")
    sim = resolution_sim(d, m, rng)
    zero = sim[0.0]["est"]
    q95 = float(np.percentile(zero, 95))
    print(f"  --- the instrument's own floor (synthetic approach, planted gap, this recording's "
          f"own noise; v0 {np.median(m['onset_v']) * 3.6:.0f} km/h, drag "
          f"{np.median(m['drag_g']) if len(m['drag_g']) else 0.08:.3f} g, peak "
          f"{np.median(m['peak_g']):.2f} g, ramp {np.median(m['ramp_s']):.2f} s, "
          f"sigma_v {speed_noise(d)[0]:.3f} m/s)")
    q95_sm = float(np.percentile(sim[0.0]["est_sm"], 95))
    for gap, r in sim.items():
        e, sm = r["est"], r["est_sm"]
        print(f"    planted {gap:.1f} s -> bare {S.describe(e, '{:.3f}')} s, bias "
              f"{np.median(e) - gap:+.3f}, over the planted-zero p95 ({q95:.3f}) "
              f"{float((e > q95).mean()):.0%}  |  0.50 s {S.describe(sm, '{:.3f}')} s, bias "
              f"{np.median(sm) - gap:+.3f}, over its p95 ({q95_sm:.3f}) "
              f"{float((sm > q95_sm).mean()):.0%}  |  brake found {r['found']:.0%}")
    for label, obs, zero_est, q in (("bare", bare[got], zero, q95),
                                    ("0.50 s", smooth[got], sim[0.0]["est_sm"], q95_sm)):
        obs = obs[np.isfinite(obs)]
        print(f"    MEASURED vs PLANTED-ZERO ({label}): observed median {np.median(obs):.3f} s "
              f"against {np.median(zero_est):.3f} s; observed share above the planted-zero p95 "
              f"{float((obs > q).mean()):.0%} (5 % is what no hesitation at all looks like)")

    res = {}
    for label, Hm, field in (("bare", bare, "est"), ("0.50 s", smooth, "est_sm")):
        print(f"  --- does the gap predict the corner's own time? ({label} series)")
        res[label] = signal_tests(key, Hm, d["time"], rng)
        power(key, Hm, d["time"], sim, rng, field=field)
    # split-half: does a corner's typical gap even repeat inside one session?
    odd, even = np.arange(n_l) % 2 == 1, np.arange(n_l) % 2 == 0
    for label, Hm in (("bare", bare), ("0.50 s", smooth)):
        with np.errstate(invalid="ignore"):
            a = np.nanmedian(Hm[odd], axis=0)
            b = np.nanmedian(Hm[even], axis=0)
        print(f"  split-half ({label}, odd vs even laps) of the per-corner median gap: "
              f"rho {S.spearman(a, b):+.3f} over {np.isfinite(a * b).sum()} corners; "
              f"odd {np.round(a, 3).tolist()} even {np.round(b, 3).tolist()}")
    return {"m": m, "sim": sim, "res": res}


def main() -> None:
    real_before = C.folder_state(C.REAL_APP_SUPPORT)
    print(f"app-support seams diverted to {C.jail_app_support()}")
    keys = sys.argv[1:] or C.present_keys()
    for i, key in enumerate(keys):
        d = C.load(key)
        if d is None:
            continue
        report(key, d, np.random.default_rng([10, i]))
    if C.folder_state(C.REAL_APP_SUPPORT) != real_before:
        raise SystemExit("THE REAL APP-SUPPORT DIRECTORY CHANGED during this run — stop and look.")
    print("\nreal app-support directory unchanged (size + mtime)")


if __name__ == "__main__":
    main()
