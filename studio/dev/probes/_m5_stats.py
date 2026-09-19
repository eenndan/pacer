"""The statistics the three M5 probes share — ranks, permutation nulls, and the two corrections
this repo has already paid to learn.

TWO RULES, BOTH FROM MEASURED FAILURES IN THIS TREE:

  * A MAX-STATISTIC FAMILY-WISE CORRECTION DOES NOT COVER SELECTING THE WINNER. #311 measured a
    shuffled control where "the top corner is separable from *some* other corner" fired 19.3 % and
    20.7 % of the time under a null with no effect in it, against a nominal 5 %. So every claim
    about ONE corner here is either pre-specified or carries `selection_control`, which measures
    the firing rate of the claim's own selection rule on shuffled data.
  * THE LAP IS A CONFOUND. A slow lap is slow in every corner (#265), so a per-corner association
    measured across laps can be manufactured by lap-level pace alone. `within_lap_centre` removes
    each lap's own level before the statistic, and `within_lap_permute` is the null that keeps each
    lap's own values and destroys only corner identity.

Nothing here is product code; it is pure numpy and is only imported by `studio/dev/probes/p1*`.
"""

from __future__ import annotations

import numpy as np


def ranks(a) -> np.ndarray:
    """Column-wise average ranks (ties share their mean rank). 1-D in, 1-D out."""
    a = np.asarray(a, float)
    col = a.ndim == 1
    a2 = a[:, None] if col else a
    out = np.empty_like(a2, dtype=float)
    for j in range(a2.shape[1]):
        v = a2[:, j]
        finite = np.isfinite(v)
        r = np.full(len(v), np.nan)
        vv = v[finite]
        if len(vv):
            order = np.argsort(vv, kind="stable")
            rr = np.empty(len(vv))
            rr[order] = np.arange(len(vv), dtype=float)
            _u, inv = np.unique(vv, return_inverse=True)
            r[finite] = (np.bincount(inv, rr) / np.bincount(inv))[inv]
        out[:, j] = r
    return out[:, 0] if col else out


def spearman(x, y) -> float:
    """Spearman rho over the pairs where both are finite; nan with fewer than 3 such pairs."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return float("nan")
    rx, ry = ranks(x[ok]), ranks(y[ok])
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def col_spearman(X, Y) -> np.ndarray:
    """Per-column Spearman rho of two (laps x corners) matrices."""
    X, Y = np.asarray(X, float), np.asarray(Y, float)
    return np.asarray([spearman(X[:, j], Y[:, j]) for j in range(X.shape[1])], float)


def within_lap_centre(M) -> np.ndarray:
    """Subtract each ROW's own median: what is left is corner-specific, with lap pace removed."""
    M = np.asarray(M, float)
    with np.errstate(invalid="ignore"):
        return M - np.nanmedian(M, axis=1, keepdims=True)


def within_lap_permute(M, rng) -> np.ndarray:
    """Shuffle each row's corner labels independently — every lap keeps its OWN values, spread and
    pace exactly, and only corner identity dies (the #265 null)."""
    M = np.asarray(M, float)
    out = np.empty_like(M)
    for i in range(M.shape[0]):
        out[i] = M[i, rng.permutation(M.shape[1])]
    return out


def column_permute(M, rng) -> np.ndarray:
    """Shuffle the LAP index within each column independently — the plain across-laps null."""
    M = np.asarray(M, float)
    out = np.empty_like(M)
    for j in range(M.shape[1]):
        out[:, j] = M[rng.permutation(M.shape[0]), j]
    return out


def _unit_cols(M) -> np.ndarray:
    """Centre each column and scale it to unit norm — then a Pearson r is one dot product."""
    M = np.asarray(M, float)
    M = M - M.mean(axis=0)
    norm = np.linalg.norm(M, axis=0)
    norm[norm == 0] = 1.0
    return M / norm


def perm_test(X, Y, perms: int, rng, *, null: str = "column") -> dict:
    """Per-corner Spearman rho of X against Y across laps, with a permutation null on X.

    Returns per-corner `rho`, the UNCORRECTED per-corner p, the max-statistic family-wise p
    (the share of null draws whose max |rho| over corners beats this corner's), the POOLED
    pre-specified statistic (the mean rho over corners, which selects nothing) and its own p, and
    the null draws themselves so a caller can price its own selection rule.

    `null="column"` shuffles laps within each corner; `null="within_lap"` shuffles corner labels
    within each lap (keeps lap pace — see the module doc).

    The column null runs on PRE-RANKED columns: permuting a column's values permutes its ranks the
    same way, and a permutation changes neither a column's mean nor its norm, so every draw is one
    dot product against the unit-scaled ranks. The within-lap null moves values BETWEEN columns and
    therefore has to re-rank, so it keeps the plain loop."""
    X, Y = np.asarray(X, float), np.asarray(Y, float)
    rho = col_spearman(X, Y)
    pooled = float(np.nanmean(rho))
    RX, RY = ranks(X), ranks(Y)
    if null == "column" and np.isfinite(RX).all() and np.isfinite(RY).all():
        A, B = _unit_cols(RX), _unit_cols(RY)
        n, k = A.shape
        draws = np.empty((perms, k))
        step = max(1, 2_000_000 // max(n * k, 1))
        done = 0
        while done < perms:
            c = min(step, perms - done)
            idx = np.argsort(rng.random((c, n, k)), axis=1)
            perm = np.take_along_axis(np.broadcast_to(A, (c, n, k)), idx, axis=1)
            draws[done:done + c] = np.einsum("cnk,nk->ck", perm, B)
            done += c
    else:
        draws = np.empty((perms, X.shape[1]))
        shuffle = within_lap_permute if null == "within_lap" else column_permute
        for k in range(perms):
            draws[k] = col_spearman(shuffle(X, rng), Y)
    with np.errstate(invalid="ignore"):
        maxstat = np.nanmax(np.abs(draws), axis=1)
        pooled_draws = np.nanmean(draws, axis=1)
        p_unc = np.asarray([(np.abs(draws[:, j]) >= abs(rho[j])).mean()
                            for j in range(X.shape[1])], float)
        p_fwer = np.asarray([(maxstat >= abs(rho[j])).mean() for j in range(X.shape[1])], float)
        p_pooled = float((np.abs(pooled_draws) >= abs(pooled)).mean())
    return {"rho": rho, "p_unc": p_unc, "p_fwer": p_fwer, "pooled": pooled,
            "p_pooled": p_pooled, "draws": draws, "pooled_draws": pooled_draws}


def selection_control(X, Y, *, outer: int, inner: int, rng, null: str = "column",
                      alpha: float = 0.05) -> dict:
    """How often each claim fires when there is NOTHING to find (X shuffled before the test).

    Three claims, because they have three different real sizes:
      * `any_fwer` — "some corner is significant" under the max-statistic correction. This is the
        one the correction is for, and it should land near alpha.
      * `top_unc` — "the top corner is significant" read off its own uncorrected p. This is the
        rule a reader applies by eye, and #311 measured it at 4x alpha.
      * `pooled` — the pre-specified pooled statistic, which selects nothing."""
    fires = {"any_fwer": 0, "top_unc": 0, "pooled": 0}
    for _ in range(outer):
        Xs = (within_lap_permute if null == "within_lap" else column_permute)(X, rng)
        r = perm_test(Xs, Y, inner, rng, null=null)
        if np.nanmin(r["p_fwer"]) < alpha:
            fires["any_fwer"] += 1
        top = int(np.nanargmax(np.abs(r["rho"])))
        if r["p_unc"][top] < alpha:
            fires["top_unc"] += 1
        if r["p_pooled"] < alpha:
            fires["pooled"] += 1
    return {k: v / outer for k, v in fires.items()}


def theil_sen(y, x=None) -> float:
    """Median pairwise slope of y against x (lap index by default) — robust to a single bad lap."""
    y = np.asarray(y, float)
    x = np.arange(len(y), dtype=float) if x is None else np.asarray(x, float)
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = y[ok], x[ok]
    if len(y) < 3:
        return float("nan")
    i, j = np.triu_indices(len(y), k=1)
    dx = x[j] - x[i]
    good = dx != 0
    return float(np.median((y[j] - y[i])[good] / dx[good]))


def describe(a, fmt: str = "{:+.3f}") -> str:
    """median [p10, p90] n=N — the distribution, never one example."""
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    if not len(a):
        return "n=0"
    return (f"median {fmt.format(float(np.median(a)))} "
            f"[p10 {fmt.format(float(np.percentile(a, 10)))}, "
            f"p90 {fmt.format(float(np.percentile(a, 90)))}] n={len(a)}")
