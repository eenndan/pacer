"""Consistency statistics (F6): per-sector / per-corner spread + the inconsistency ranking.

PACER-FREE BY CONTRACT (numpy only, no Qt). Fed by Session's per-lap values (sector splits,
corner times, lap times); Session owns which laps are included — the consistency lap set is
the VALID laps with no GPS dropout (`Session.consistency_lap_ids`, the ⚠ rule: a dropout
lap's times are low-confidence, so they are excluded from every statistic here exactly as
they are excluded from the corner-detection profile).

Definitions (all asserted exact against direct numpy in tests + on the real recordings):
  * σ — the SAMPLE standard deviation (np.std(…, ddof=1)). The session's laps are a sample
    of the driver's lap/sector/corner-time distribution, not the whole population; with the
    typical 5–20 valid laps the ddof=1 correction matters. None when fewer than 2 values.
  * median loss (per corner) — median(t) − min(t) over the included laps: how much slower
    the TYPICAL lap is through that corner than the demonstrated best. min/median over the
    same included lap set, so the panel is internally consistent (≥ 0 by construction).
  * inconsistency score (the ranking weight) — σ × median_loss (s²). WHY the product:
    σ alone ranks a corner where the driver is erratic around a *good* time above one that
    is erratic AND slow; median loss alone ranks a consistently-slow corner (a line problem,
    not a consistency problem) at the top. The product is the AND — a corner must have BOTH
    spread and typical loss to rank highly, and those are exactly the corners where
    consistency practice pays the most time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CornerSpread:
    """One corner's consistency stats over the included (valid, dropout-free) laps."""

    cid: int                  # Corner.cid (1-based, track order)
    sigma: float              # sample σ of time-in-corner (s)
    median_loss: float        # median(t) − min(t) over the included laps (s, ≥ 0)
    score: float              # σ × median_loss — the inconsistency ranking weight (s²)
    n: int                    # number of laps the stats run over


def sigma(values) -> float | None:
    """Sample standard deviation (ddof=1) of `values`, or None when fewer than 2 finite
    values exist (a spread needs at least two samples). Non-finite entries (a partial
    lap's missing split) are excluded — matching np.std on the same filtered array."""
    a = np.asarray(list(values), float)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        return None
    return float(np.std(a, ddof=1))


def sector_sigmas(splits_by_lap: list[list[float]]) -> list[float | None]:
    """Per-sector-column σ over the included laps: column k's σ runs over every lap that
    has a finite k-th split (a partial lap may have fewer). The column count is the widest
    lap's (the full laps; same convention as the lap table's S-columns)."""
    n_cols = max((len(sp) for sp in splits_by_lap), default=0)
    return [
        sigma([sp[k] for sp in splits_by_lap if k < len(sp)])
        for k in range(n_cols)
    ]


def corner_spreads(cids: list[int], times_by_lap: list[list[float]]) -> list[CornerSpread]:
    """Per-corner spread stats (cid order) from `times_by_lap`: one row per included lap,
    each row the per-corner times aligned to `cids`. Corners without enough data (fewer
    than 2 laps with a finite time) are dropped — σ is undefined there."""
    out: list[CornerSpread] = []
    for k, cid in enumerate(cids):
        vals = np.asarray([row[k] for row in times_by_lap if k < len(row)], float)
        vals = vals[np.isfinite(vals)]
        s = sigma(vals)
        if s is None:
            continue
        loss = float(np.median(vals) - np.min(vals))
        out.append(CornerSpread(cid=cid, sigma=s, median_loss=loss,
                                score=s * loss, n=len(vals)))
    return out


def rank_corners(spreads: list[CornerSpread]) -> list[CornerSpread]:
    """The "most inconsistent corners" ranking: score (σ × median_loss) descending — see
    the module docstring for why the product. Ties (e.g. two zero-score corners) keep
    track order (stable sort on −score)."""
    return sorted(spreads, key=lambda s: -s.score)


def pb_mask(times) -> list[bool]:
    """True where a lap set a NEW session best (a running minimum — strictly faster than
    every lap before it). The first lap is trivially the best so far. The sparkline marks
    these so the trend reads "where the PBs happened" at a glance."""
    best = math.inf
    out = []
    for t in times:
        on = t < best
        out.append(on)
        if on:
            best = t
    return out
