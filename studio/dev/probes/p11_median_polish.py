"""P11 — driver learning vs session evolution, by median polish over the lap x corner matrix (M5.2).

THE IDEA (roadmap LATER, "Driver-learning vs session-evolution decomposition"). A session gets
faster for two different reasons — the track rubbers in, the tyres and fuel come to the driver
(SESSION EVOLUTION), and the driver works a particular corner out (DRIVER LEARNING). Tukey's median
polish over the laps x corners matrix of corner times splits a cell into a lap effect, a corner
effect and what is left, so the story goes: the lap effects' trend is the session, and a corner
whose RESIDUAL trends on top of that is the driver learning that corner.

WHAT THE DECOMPOSITION CAN AND CANNOT SAY, stated before any number is printed: median polish
separates COMMON from CORNER-SPECIFIC. It does not separate the driver from the track. A driver who
simply gets faster everywhere lands entirely in the lap effects, next to the rubber, and nothing in
the arithmetic can tell those apart. So only the corner-specific half is testable at all, and this
probe tests exactly that half:

  * THE NULL IT MUST BEAT: the lap ORDER, shuffled. Whole rows of the residual matrix are permuted
    — every lap keeps its own residuals across every corner, so the within-lap correlation the
    polish leaves behind survives and only time order dies. A corner-specific trend that does not
    beat that null is a pattern in noise.
  * PRE-SPECIFIED, so nothing rests on picking a winner: the global statistic is the summed squared
    rank-trend over ALL corners, and the per-corner claims carry a max-statistic family-wise p AND
    a selection control, because a family-wise correction does not cover choosing which corner to
    talk about (#311 measured that rule firing 19-21 % under a null).
  * POWER, by planting. A known corner-specific improvement (0.10-0.40 s over the session) is added
    to ONE corner of the REAL matrix and the probe reports how often it is found, how often the
    RIGHT corner is named, and — the mirror-image error — how often a purely COMMON improvement of
    the same size is misattributed to some corner.
  * The session-evolution half is reported too (the lap effects' Theil-Sen slope against a
    shuffled-order null) with the seconds it is worth, since the product claim is a split of one
    number into two.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p11_median_polish

SAFETY: as `_m5_cache` — read-only footage snapshotted around every load, app-support seams
diverted before any studio import resolves one, nothing written.
"""

from __future__ import annotations

import sys

import numpy as np

from studio.dev.probes import _m5_cache as C
from studio.dev.probes import _m5_stats as S

PERMS = 20_000
SEL_OUTER, SEL_INNER = 300, 1_000
PLANT_S = (0.10, 0.20, 0.40)     # s of improvement planted across the whole session
PLANT_REPS = 200
PLANT_PERMS = 2_000
ALPHA = 0.05
MIN_LAPS = 8


def median_polish(M, iters: int = 20) -> dict:
    """Tukey's median polish: M ~ mu + row + col + residual, NaN-aware (an unresolved cell is
    missing, not zero). Sweeps alternate until the effects stop moving."""
    R = np.array(M, float)
    n_l, n_c = R.shape
    mu, row, col = 0.0, np.zeros(n_l), np.zeros(n_c)
    with np.errstate(invalid="ignore"):
        for _ in range(iters):
            rm = np.nanmedian(R, axis=1)
            rm = np.where(np.isfinite(rm), rm, 0.0)
            R -= rm[:, None]
            row += rm
            d = float(np.nanmedian(row))
            row -= d
            mu += d
            cm = np.nanmedian(R, axis=0)
            cm = np.where(np.isfinite(cm), cm, 0.0)
            R -= cm[None, :]
            col += cm
            d = float(np.nanmedian(col))
            col -= d
            mu += d
    return {"mu": mu, "row": row, "col": col, "resid": R}


def trend_stats(resid, rng, perms: int = PERMS) -> dict:
    """Per-corner rank trend of the residuals against lap order, with the shuffled-order null.

    The permutation moves WHOLE ROWS, so a lap's residuals stay together — the within-lap
    correlation the polish leaves behind is inside the null — and only time order dies. The
    pre-specified global statistic is the summed squared per-corner rho, which selects nothing.

    `resid` must be complete (see `S.complete_block`); the ranks are then precomputed once and
    every draw is one index-and-dot."""
    resid = np.asarray(resid, float)
    n_l, n_c = resid.shape
    order = S._unit_cols(np.tile(S.ranks(np.arange(n_l, dtype=float))[:, None], (1, n_c)))
    R = S._unit_cols(S.ranks(resid))
    rho = np.einsum("nk,nk->k", order, R)
    energy = float(np.sum(rho ** 2))
    idx = np.argsort(rng.random((perms, n_l)), axis=1)
    draws = np.einsum("cnk,nk->ck", R[idx], order)
    maxstat = np.max(np.abs(draws), axis=1)
    slope = np.asarray([S.theil_sen(resid[:, j]) for j in range(n_c)], float)
    return {"rho": rho, "energy": energy, "slope": slope,
            "p_energy": float((np.sum(draws ** 2, axis=1) >= energy).mean()),
            "p_fwer": np.asarray([(maxstat >= abs(r)).mean() for r in rho], float),
            "p_unc": np.asarray([(np.abs(draws[:, j]) >= abs(rho[j])).mean()
                                 for j in range(n_c)], float)}


def controls(resid, rng) -> dict:
    """Firing rates when the lap order carries nothing: the correction's claim, the reader's
    claim ("the top corner"), and the pre-specified global one."""
    fires = {"any_fwer": 0, "top_unc": 0, "energy": 0}
    n_l = resid.shape[0]
    for _ in range(SEL_OUTER):
        r = trend_stats(resid[rng.permutation(n_l)], rng, SEL_INNER)
        if np.nanmin(r["p_fwer"]) < ALPHA:
            fires["any_fwer"] += 1
        if r["p_unc"][int(np.nanargmax(np.abs(r["rho"])))] < ALPHA:
            fires["top_unc"] += 1
        if r["p_energy"] < ALPHA:
            fires["energy"] += 1
    return {k: v / SEL_OUTER for k, v in fires.items()}


def power(M, block, rng) -> None:
    """Plant a corner-specific improvement in ONE corner, and a COMMON one in all of them.

    The plant goes into the REAL matrix, so the recording's own lap-to-lap spread, traffic and
    missing cells are all still in the way — and the whole chain (polish, then the trend test on
    the polished residuals) runs on it, not a simplified copy."""
    cols, rows = block
    n_l, n_c = M.shape
    ramp = np.linspace(0.5, -0.5, n_l)          # a linear improvement over the session, mean 0
    for total in PLANT_S:
        found = right = common_leak = 0
        for _ in range(PLANT_REPS):
            target = int(rng.integers(int(cols.sum())))
            target_col = int(np.flatnonzero(cols)[target])
            planted = np.array(M, float)
            planted[:, target_col] += total * ramp
            R = median_polish(planted)["resid"][np.ix_(rows, cols)]
            r = trend_stats(R, rng, PLANT_PERMS)
            if np.nanmin(r["p_fwer"]) < ALPHA:
                found += 1
                if int(np.nanargmin(r["p_fwer"])) == target:
                    right += 1
            common = np.array(M, float) + total * ramp[:, None]
            rc = trend_stats(median_polish(common)["resid"][np.ix_(rows, cols)], rng, PLANT_PERMS)
            if np.nanmin(rc["p_fwer"]) < ALPHA:
                common_leak += 1
        print(f"  POWER planting {total:.2f} s of improvement over the session in ONE corner: "
              f"some corner named {found / PLANT_REPS:.0%}, the RIGHT corner named "
              f"{right / PLANT_REPS:.0%}; the same {total:.2f} s planted in EVERY corner "
              f"(session evolution) is misread as corner-specific "
              f"{common_leak / PLANT_REPS:.0%} ({PLANT_REPS} replicates)")


def report(key: str, d: dict, rng) -> None:
    times = np.array(d["time"], float)
    resolved = d["resolved"]
    n_l, n_c = times.shape
    M = np.where(resolved, times, np.nan)
    print(f"\n{'=' * 100}\n{key}: {n_l} laps x {n_c} corners; "
          f"{np.isfinite(M).mean():.0%} of cells resolved (an unresolved window is a ~0.22 s "
          f"instrument — stats.CornerMatrix — so it is left MISSING, not imputed)")
    if n_l < MIN_LAPS:
        print("  too few laps")
        return
    stints, counts = np.unique(d["stint"], return_counts=True)
    laps_per_stint = dict(zip(stints.tolist(), counts.tolist(), strict=True))
    print(f"  lap time: first 5 {np.round(d['lap_time'][:5], 2).tolist()}, last 5 "
          f"{np.round(d['lap_time'][-5:], 2).tolist()}; stints {laps_per_stint}")

    pol = median_polish(M)
    row, col = pol["row"], pol["col"]
    print(f"  polish: mu {pol['mu']:.3f} s; corner effects {np.round(col, 2).tolist()}; "
          f"residual spread (MAD) {float(np.nanmedian(np.abs(pol['resid']))):.3f} s")

    # --- session evolution: the lap effects' own trend
    slope = S.theil_sen(row)
    rho_row = S.spearman(np.arange(n_l, dtype=float), row)
    draws = np.asarray([S.spearman(np.arange(n_l, dtype=float), rng.permutation(row))
                        for _ in range(PERMS)])
    p_row = float((np.abs(draws) >= abs(rho_row)).mean())
    print(f"  SESSION EVOLUTION (lap effects): Theil-Sen {slope * 1000:+.1f} ms/lap = "
          f"{slope * n_l:+.3f} s across the session; rank trend rho {rho_row:+.3f}, "
          f"shuffled-order p {p_row:.4f}")

    # --- driver learning: corner-specific trends on top of it
    block = S.complete_block(M)
    if block is None:
        print("  no rectangular resolved block — no corner-specific test")
        return
    cols, rows = block
    Rb = pol["resid"][np.ix_(rows, cols)]
    print(f"  test block: {rows.sum()} laps x {cols.sum()} corners "
          f"(cids {d['cid'][cols].tolist()})")
    tr = trend_stats(Rb, rng)
    print(f"  CORNER-SPECIFIC (residual trends): rho {np.round(tr['rho'], 3).tolist()}")
    print(f"    Theil-Sen per corner, s across the block: "
          f"{np.round(tr['slope'] * int(rows.sum()), 3).tolist()}")
    top = int(np.nanargmax(np.abs(tr["rho"])))
    print(f"    pre-specified global (summed rho^2) {tr['energy']:.3f}, p {tr['p_energy']:.4f}; "
          f"top corner cid {d['cid'][cols][top]} rho {tr['rho'][top]:+.3f}, "
          f"p_unc {tr['p_unc'][top]:.4f}, p_fwer {tr['p_fwer'][top]:.4f}")
    ctl = controls(Rb, rng)
    print(f"    selection control (lap order shuffled, nothing to find): any-corner-at-FWER "
          f"{ctl['any_fwer']:.1%}, TOP-corner-by-its-own-p {ctl['top_unc']:.1%}, "
          f"pre-specified global {ctl['energy']:.1%} (nominal {ALPHA:.0%})")

    # --- does a corner-specific trend repeat inside the session?
    odd = rows & (np.arange(n_l) % 2 == 1)
    even = rows & (np.arange(n_l) % 2 == 0)
    ro = trend_stats(median_polish(M[odd])["resid"][:, cols], rng, perms=2)["rho"]
    re = trend_stats(median_polish(M[even])["resid"][:, cols], rng, perms=2)["rho"]
    print(f"    split-half (odd vs even laps, both spanning the session): per-corner trend rho "
          f"agrees at r {S.spearman(ro, re):+.3f}, same sign on "
          f"{int((np.sign(ro) == np.sign(re)).sum())} of {int(cols.sum())}; "
          f"odd {np.round(ro, 2).tolist()} "
          f"even {np.round(re, 2).tolist()}")

    # --- the largest stint on its own: a trend inside one run has no pit stop in it
    # A trend inside ONE stint has no pit stop, tyre change or long stand in it — but every stint
    # is another test, so they are all printed, never just the one that fired.
    for st in stints:
        sel = rows & (d["stint"] == st)
        if sel.sum() < MIN_LAPS:
            continue
        ps = median_polish(M[sel])
        ts = trend_stats(ps["resid"][:, cols], rng, perms=5_000)
        best = int(np.nanargmin(ts["p_fwer"]))
        print(f"    within stint {int(st)} ({int(sel.sum())} laps): lap-effect Theil-Sen "
              f"{S.theil_sen(ps['row']) * int(sel.sum()):+.3f} s; corner-specific global p "
              f"{ts['p_energy']:.4f}; best corner cid {d['cid'][cols][best]} rho "
              f"{ts['rho'][best]:+.3f} p_fwer {ts['p_fwer'][best]:.4f}; all rho "
              f"{np.round(ts['rho'], 2).tolist()}")
    power(M, block, rng)


def main() -> None:
    real_before = C.folder_state(C.REAL_APP_SUPPORT)
    print(f"app-support seams diverted to {C.jail_app_support()}")
    for i, key in enumerate(sys.argv[1:] or C.present_keys()):
        d = C.load(key)
        if d is None:
            continue
        report(key, d, np.random.default_rng([11, i]))
    if C.folder_state(C.REAL_APP_SUPPORT) != real_before:
        raise SystemExit("THE REAL APP-SUPPORT DIRECTORY CHANGED during this run — stop and look.")
    print("\nreal app-support directory unchanged (size + mtime)")


if __name__ == "__main__":
    main()
