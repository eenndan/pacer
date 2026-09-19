"""P12 — is the per-corner Grip % column worth anything beyond description? (M5.3)

THE QUESTION. `driving.corner_grip` divides a corner's median combined |g| by the SESSION grip
envelope (`driving.grip_envelope`, the p98 of |g| over the whole recording), so the number is
comparable across laps by construction — a slow lap reads genuinely lower. The August roadmap
gated "grip headroom + utilisation trend per corner" on a RE-GROUNDING, because the channel was
then "pace-blind (~0.6)" and nothing had re-checked it since the normalisation changed. This probe
is that re-check, and it is deliberately asked as a product question: if Grip % does not move with
how fast the corner actually goes, the column is DESCRIPTIVE ONLY and no headroom feature can be
built on it.

  * THE PRE-SPECIFIED TEST, fixed before the data was read: the pooled mean of the per-corner
    Spearman of Grip % against the corner's own time across laps. Pooled because it selects no
    corner; the per-corner numbers are reported beside it with a max-statistic family-wise p and a
    selection control, since a family-wise correction does not cover picking the winner (#311).
  * THE NULL IT MUST BEAT: Grip % shuffled across laps within each corner — the marginal
    distribution of the column survives exactly and only its pairing with the time dies.
  * THE LAP-PACE CONFOUND (#265): a fast lap is fast in every corner, so the whole test is run a
    second time with each lap's own level removed from both sides.
  * WHAT IT WOULD ADD: the app already publishes the corner's APEX SPEED next to Grip %. The probe
    reports the partial correlation of Grip % with corner time given apex speed — a column that
    only restates the one beside it is not a new channel.
  * THE HEADROOM CLAIM, which is a BETWEEN-corner claim ("you use 63 % here, so there is room"),
    tested between corners against the time each corner actually loses, with its own permutation
    null and its own power — 7 or 12 corners is a small sample and the probe says so.
  * POWER: an association of KNOWN rank strength is planted into the real matrix and the probe
    reports how often each test finds it, so a silence here means something.
  * DIRECTION: Grip % is built from `hypot(lat, long)`, which no sign can reach — but a
    direction-dependent sign bug shipped unnoticed in this campaign (#334), so the left/right
    corner split is printed for a clockwise track and an anticlockwise one.

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.probes.p12_grip_regrounding

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
PLANT_RHO = (0.2, 0.3, 0.5)
PLANT_REPS = 200
PLANT_PERMS = 2_000
ALPHA = 0.05


def _resid(y, x):
    """y with its linear dependence on x removed (both already ranks)."""
    x = np.asarray(x, float) - np.mean(x)
    y = np.asarray(y, float) - np.mean(y)
    denom = float(x @ x)
    return y if denom == 0 else y - (float(x @ y) / denom) * x


def partial_rho(g, t, z) -> float:
    """Spearman of g and t with z held: the correlation of their rank residuals on z."""
    rg, rt, rz = S.ranks(g), S.ranks(t), S.ranks(z)
    a, b = _resid(rg, rz), _resid(rt, rz)
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def plant(target_rho: float, T, rng) -> np.ndarray:
    """A synthetic Grip % matrix with a KNOWN rank association to the corner times.

    Built column by column from the time's own rank scores: `rho * (-z_time) + sqrt(1-rho^2) * e`,
    so more grip goes with a faster corner — the direction a headroom feature would claim."""
    out = np.empty_like(T, dtype=float)
    n = T.shape[0]
    for j in range(T.shape[1]):
        z = S.ranks(T[:, j])
        z = (z - z.mean()) / (z.std() or 1.0)
        e = rng.normal(size=n)
        out[:, j] = target_rho * (-z) + np.sqrt(max(1 - target_rho ** 2, 0.0)) * e
    return out


def power(T, rng) -> None:
    for target in PLANT_RHO:
        hits = {"pooled": 0, "any_fwer": 0}
        rhos = []
        for _ in range(PLANT_REPS):
            G = plant(target, T, rng)
            r = S.perm_test(G, T, PLANT_PERMS, rng)
            rhos.append(float(np.nanmean(r["rho"])))
            if r["p_pooled"] < ALPHA:
                hits["pooled"] += 1
            if np.nanmin(r["p_fwer"]) < ALPHA:
                hits["any_fwer"] += 1
        print(f"  POWER planting rho {-target:+.1f} (more grip, faster corner; realised pooled "
              f"{np.mean(rhos):+.3f}): the pooled test fires {hits['pooled'] / PLANT_REPS:.0%}, "
              f"some corner at FWER {hits['any_fwer'] / PLANT_REPS:.0%} "
              f"({PLANT_REPS} replicates, nominal {ALPHA:.0%})")


def report(key: str, d: dict, rng) -> None:
    grip = np.array(d["grip"], float)
    times = np.array(d["time"], float)
    apex = np.array(d["apex_v"], float)
    ok = d["resolved"] & np.isfinite(grip) & (grip > 0)
    G = np.where(ok, grip, np.nan)
    n_l, n_c = G.shape
    print(f"\n{'=' * 100}\n{key}: {n_l} laps x {n_c} corners; grip envelope "
          f"{d['grip_envelope']:.3f} g (SESSION p98 — the divisor is one number for the whole "
          f"recording, so a slow lap reads genuinely lower)")
    print(f"  Grip % per corner (median over laps): "
          f"{np.round(np.nanmedian(G, axis=0) * 100, 1).tolist()}")
    print(f"  Grip % spread within a corner (p90-p10, points): "
          f"{np.round((np.nanpercentile(G, 90, axis=0) - np.nanpercentile(G, 10, axis=0)) * 100, 1).tolist()}")
    left = d["corner_dir"] > 0
    print(f"  direction check (#334): {int(left.sum())} left / {int((~left).sum())} right corners; "
          f"median Grip % left {np.nanmedian(G[:, left]) * 100:.1f}, right "
          f"{np.nanmedian(G[:, ~left]) * 100:.1f} — the channel is hypot(lat, long), which no sign "
          f"can reach")

    # Does the session-envelope normalisation make a slow LAP read lower, as its docstring claims?
    with np.errstate(invalid="ignore"):
        lap_grip = np.nanmean(G, axis=1)
    print(f"  per-lap mean Grip % vs lap time: rho {S.spearman(lap_grip, d['lap_time']):+.3f} "
          f"(negative = a slow lap reads lower, which is what the session divisor is for)")

    block = S.complete_block(G)
    if block is None:
        print("  no rectangular resolved block — no test")
        return
    cols, rows = block
    Gb, Tb, Ab = (M[np.ix_(rows, cols)] for M in (G, times, apex))
    print(f"  test block: {int(rows.sum())} laps x {int(cols.sum())} corners "
          f"(cids {d['cid'][cols].tolist()})")

    for label, X, Y in (("raw", Gb, Tb),
                        ("pace-removed", S.within_lap_centre(Gb), S.within_lap_centre(Tb))):
        r = S.perm_test(X, Y, PERMS, rng)
        top = int(np.nanargmax(np.abs(r["rho"])))
        print(f"  {label:<12} per-corner rho(Grip %, corner time) {np.round(r['rho'], 3).tolist()}; "
              f"PRE-SPECIFIED pooled {r['pooled']:+.3f} (p {r['p_pooled']:.4f}); top corner cid "
              f"{d['cid'][cols][top]} rho {r['rho'][top]:+.3f} p_unc {r['p_unc'][top]:.4f} "
              f"p_fwer {r['p_fwer'][top]:.4f}")
    ctl = S.selection_control(Gb, Tb, outer=SEL_OUTER, inner=SEL_INNER, rng=rng)
    print(f"  selection control (nothing to find): any-corner-at-FWER {ctl['any_fwer']:.1%}, "
          f"TOP-corner-by-its-own-p {ctl['top_unc']:.1%}, pooled {ctl['pooled']:.1%} "
          f"(nominal {ALPHA:.0%})")

    # What would it ADD? The apex-speed column already sits next to it.
    rho_apex = np.asarray([S.spearman(Gb[:, j], Ab[:, j]) for j in range(Gb.shape[1])], float)
    rho_t = np.asarray([S.spearman(Gb[:, j], Tb[:, j]) for j in range(Gb.shape[1])], float)
    rho_part = np.asarray([partial_rho(Gb[:, j], Tb[:, j], Ab[:, j])
                           for j in range(Gb.shape[1])], float)
    rho_a_t = np.asarray([S.spearman(Ab[:, j], Tb[:, j]) for j in range(Gb.shape[1])], float)
    rho_a_part = np.asarray([partial_rho(Ab[:, j], Tb[:, j], Gb[:, j])
                             for j in range(Gb.shape[1])], float)
    print(f"  rho(Grip %, apex speed) {np.round(rho_apex, 3).tolist()} (mean "
          f"{np.nanmean(rho_apex):+.3f})")
    print(f"  rho(Grip %, corner time) {np.round(rho_t, 3).tolist()} -> with APEX SPEED held: "
          f"{np.round(rho_part, 3).tolist()} (mean {np.nanmean(rho_t):+.3f} -> "
          f"{np.nanmean(rho_part):+.3f})")
    print(f"  the same question the other way round — rho(apex speed, corner time) mean "
          f"{np.nanmean(rho_a_t):+.3f} -> with GRIP % held {np.nanmean(rho_a_part):+.3f}: "
          f"each column keeps most of its own association when the other is held, so they are "
          f"two readings of how hard the corner was taken, not one")

    # The headroom claim is BETWEEN corners: does a low-grip corner lose more time?
    best = int(d["best_lap_id"]) if d["best_lap_id"] is not None else -1
    brow = np.flatnonzero(d["lap_id"] == best)
    if len(brow):
        lost = np.nanmedian(times[:, cols], axis=0) - times[int(brow[0]), cols]
        med_grip = np.nanmedian(G[:, cols], axis=0)
        rho_h = S.spearman(med_grip, lost)
        draws = np.asarray([S.spearman(rng.permutation(med_grip), lost) for _ in range(PERMS)])
        print(f"  HEADROOM (between corners, {int(cols.sum())} of them): median Grip % "
              f"{np.round(med_grip * 100, 1).tolist()} vs median time lost vs the best lap "
              f"{np.round(lost, 3).tolist()}; rho {rho_h:+.3f}, permutation p "
              f"{float((np.abs(draws) >= abs(rho_h)).mean()):.4f} "
              f"(a 7-12 corner sample: |rho| must exceed "
              f"{float(np.percentile(np.abs(draws), 95)):.2f} to reach {ALPHA:.0%})")
    power(Tb, rng)


def main() -> None:
    real_before = C.folder_state(C.REAL_APP_SUPPORT)
    print(f"app-support seams diverted to {C.jail_app_support()}")
    for i, key in enumerate(sys.argv[1:] or C.present_keys()):
        d = C.load(key)
        if d is None:
            continue
        report(key, d, np.random.default_rng([12, i]))
    if C.folder_state(C.REAL_APP_SUPPORT) != real_before:
        raise SystemExit("THE REAL APP-SUPPORT DIRECTORY CHANGED during this run — stop and look.")
    print("\nreal app-support directory unchanged (size + mtime)")


if __name__ == "__main__":
    main()
