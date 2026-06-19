"""Auto coaching summary (F10): the post-load "opportunities" model.

PACER-FREE BY CONTRACT (numpy only, no Qt). The capstone of the analysis stack — it does
NOT recompute any corner / driving / consistency math. It COMPOSES the values Session already
caches (the corner model F2, the driving channels F5, the consistency stats F6) into a ranked,
explainable shortlist of "where to find time vs your own best lap" — the Garmin-Catalyst /
APEX-Pro style coaching cue, but every number is measured and deterministic (no ML, no
randomness, no vibes).

THE MODEL, in order
-------------------
1. Candidate laps = the CONSISTENCY laps (valid, GPS-dropout-free — Session.consistency_lap_ids,
   the same ⚠ rule the corner-detection profile and the σ stats use). The "typical" lap is the
   one whose lap TIME is the median of that set (ties -> the lower lap id, so it is fully
   deterministic). All the per-corner reasons below are read off THIS one median lap, so the
   advice describes the driver's representative lap, not a one-off mistake.

2. Per corner, the TIME LOST is the MEDIAN over the candidate laps of (that lap's time in the
   corner − the best lap's time in the same corner). The per-lap per-corner delta-vs-best is
   exactly CornerStat.delta (Session.lap_corner_stats already measures it against the best lap),
   so the median of those deltas is the realistic, repeatable time available in that corner —
   robust to a single bad lap. Corners are RANKED by this median loss, biggest first; only
   corners with a positive median loss are opportunities (a corner the driver already does as
   well as their best on a typical lap has nothing to gain).

3. For the TOP-N corners (default 3) the DOMINANT measured reason is chosen deterministically
   from four candidate signals, EACH converted to an estimated seconds-of-loss contribution so
   they are directly comparable, then the largest wins (ties broken by a fixed reason priority):

     * APEX  — apex (min) speed deficit: the median lap's apex speed in the corner is below the
       best lap's apex there (CornerStat.apex_speed_delta < 0). Contribution = the corner's
       time loss scaled by how much of the speed gap is "explained" deficit (see _apex_signal):
       carrying more apex speed is the lever.  => "carry more apex speed (−X km/h)".
     * BRAKING — the median lap brakes EARLIER and/or LONGER than the best lap in the corner's
       approach. Projecting both laps' brake events (Session.lap_brake_events) onto the corner
       window [enter − approach, exit], an earlier onset and/or a longer duration than the best
       lap's matched event is wasted time. Contribution = the time-on-brakes difference (s).
       => "brake later / shorter".
     * COASTING — a coasting span (Session.lap_coasting_spans) that lies INSIDE the corner on
       the median lap but NOT on the best lap: time spent neither braking nor accelerating that
       the best lap doesn't give up. Contribution = the extra coasting duration in the window (s).
       => "back to throttle sooner".
     * LINE  — high cross-lap σ of time-in-corner (CornerSpread.sigma from
       Session.corner_consistency): the loss is mostly INCONSISTENCY, not a single fixable
       input. Contribution = the σ (s), the spread the driver could remove by repeating the
       same line.  => "be consistent here".

   Every reason carries the supporting numbers that produced it, so the UI sentence is "numbers
   only". When no signal has a positive contribution (e.g. no g channel and the loss is small),
   the reason falls back to LINE if there is real spread, else a neutral "find time here".

EXCLUDED STATE
--------------
`summarize` returns an Opportunities with `enough=False` (and an empty list) when there are
fewer than MIN_LAPS consistency laps — coaching off two laps would be noise. The UI shows a
friendly "need more laps" message; nothing crashes.

DETERMINISM
-----------
Pure functions of the passed-in arrays + dataclasses (themselves deterministic Session outputs).
No RNG, no time, no dict-ordering dependence (corners are processed in cid order, candidate laps
in ascending id). `summarize` called twice on the same Session yields byte-identical results
(asserted in the tests and on the real recordings).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Minimum number of consistency laps (valid, dropout-free) before any coaching is offered.
# WHY 3: the per-corner time loss is a MEDIAN over the candidate laps; with only two laps the
# "median" is just their mean and a single off lap dominates, and the median-lap selection is
# ill-defined. Three is the smallest set where the median is a real central value and a typical
# lap can differ from the best. (Rental-kart sessions routinely have 5-20 valid laps.)
MIN_LAPS = 3

# How many ranked corners get a dominant-reason attached (the panel's row count).
TOP_N = 3

# Approach margin (metres) prepended to a corner's window when matching brake events: the
# decisive braking for a corner begins on the straight BEFORE turn-in, so the brake-onset
# comparison must look a little upstream of the geometric corner entry. 30 m ~ the brake zone
# length of a medium kart corner; the corner model's own enter point is where sustained
# CORNERING starts, which is already past the brake point.
BRAKE_APPROACH_M = 30.0

# Reasons, as stable string ids (the UI maps them to sentences + picks an icon/colour). Ordered
# by the tie-break PRIORITY used when two signals produce an equal contribution: a concrete,
# directly-actionable input (apex speed) is preferred over a process cue (braking, coasting),
# and raw inconsistency (line) is the last resort. Deterministic and documented.
REASON_APEX = "apex"
REASON_BRAKING = "braking"
REASON_COASTING = "coasting"
REASON_LINE = "line"
REASON_NONE = "none"  # a ranked corner with no positive signal (still shows the time lost)
_REASON_PRIORITY = (REASON_APEX, REASON_BRAKING, REASON_COASTING, REASON_LINE, REASON_NONE)


@dataclass(frozen=True)
class Reason:
    """The dominant measured reason a corner is losing time, with the supporting numbers.

    `kind` is one of the REASON_* ids; `contribution` is the estimated seconds-of-loss the
    reason accounts for (the score the dominant reason won on); the remaining fields are the
    measured numbers behind the sentence (only the ones relevant to `kind` are non-zero, but
    all are carried so the UI/tests can read them uniformly)."""

    kind: str
    contribution: float          # estimated s of loss attributed to this reason (the score)
    apex_speed_deficit: float    # best apex − median apex (km/h, > 0 means slower than best)
    brake_extra_s: float         # median lap's extra time-on-brakes in the window vs best (s)
    coast_extra_s: float         # extra coasting duration inside the corner vs best (s)
    sigma: float                 # cross-lap σ of time-in-corner (s)


@dataclass(frozen=True)
class Opportunity:
    """One corner's coaching row: how much time is realistically available and why."""

    cid: int                 # 1-based corner id (track order)
    direction: int           # +1 left / -1 right (for the UI glyph)
    time_lost: float         # median time lost vs the best lap's same corner (s, > 0)
    entry_dist: float        # the corner's enter odometer on the BEST lap (m) — the jump-to seek
    reason: Reason           # the dominant measured reason + numbers (only on the top-N rows)


@dataclass(frozen=True)
class Opportunities:
    """The whole summary the panel renders. `enough` is False (and `rows` empty) when there are
    fewer than MIN_LAPS consistency laps — the friendly "need more laps" state."""

    enough: bool
    n_laps: int                              # consistency laps the summary ran over
    median_lap_id: int | None                # the representative lap the reasons read off
    rows: list[Opportunity] = field(default_factory=list)


# ----------------------------------------------------------------- median-lap selection
def median_lap_id(lap_ids: list[int], lap_times: list[float]) -> int | None:
    """The candidate lap whose TIME is the median of the set — the representative lap. Even
    counts take the lower-time of the two central laps (np.argsort is stable, so a tie in time
    then resolves to the lower lap id): fully deterministic, no averaging of two laps. None for
    an empty set."""
    if not lap_ids:
        return None
    order = np.argsort(np.asarray(lap_times, float), kind="stable")
    mid = (len(order) - 1) // 2  # lower-middle index -> the median (lower of two for even n)
    return int(lap_ids[int(order[mid])])


# ---------------------------------------------------------------------- reason signals
# Each reason's RAW evidence (apex-speed deficit in km/h, extra brake/coast seconds, σ in s) is
# on its OWN scale, so they cannot be compared directly — a 1.6 s "extra time on the brakes" is
# NOT a 1.6 s time loss (braking longer doesn't cost a second). So each evidence value is mapped
# to a unitless STRENGTH in [0, 1) by a saturating curve evidence/(evidence + half), where `half`
# is the evidence level at which that reason is "half-credited". The four strengths ARE
# comparable; the dominant reason is the strongest, and its CONTRIBUTION (for display) is the
# corner's own time_lost × strength — so no reason can ever claim more than the corner actually
# gives up (the bug a raw extra-seconds score caused: a long brake-zone difference swamping a
# clear apex deficit on a 0.2 s corner). The `half` constants are documented below.

# Evidence levels at which each reason reaches HALF strength (the cross-over points that set
# which signal wins a mixed corner). Tuned against the D24 validation recordings:
#   * apex 3 km/h: a typical lap 3 km/h down at the apex is unambiguously an apex-speed problem;
#     < ~1 km/h is within line/GPS noise (the corner model's apex is a min over the window).
#   * brake 0.30 s: a brake zone that runs ~0.3 s longer/earlier than the best lap's is a clear,
#     decisive braking difference; a sub-0.1 s difference is threshold ripple, not a real cause.
#   * coast 0.30 s: == MIN_COAST_S in driving.py — the shortest coast the channel even reports, so
#     any reported extra coast is already "real" and reaches half strength at one such span.
#   * sigma 0.15 s: a corner whose lap-to-lap time σ is ~0.15 s is genuinely inconsistent (the
#     F6 panel flags these); below ~0.05 s the line is repeatable. The ranking is insensitive to
#     each within a wide band (these only set ties between two co-present causes).
_APEX_HALF_KMH = 3.0
_BRAKE_HALF_S = 0.30
_COAST_HALF_S = 0.30
_SIGMA_HALF_S = 0.15


def _saturate(evidence: float, half: float) -> float:
    """A unitless strength in [0, 1): evidence/(evidence + half), 0 for non-positive evidence.
    Half-strength at `evidence == half`, →1 for evidence ≫ half. Makes the four reasons'
    different-unit evidence directly comparable without a magic unit conversion."""
    e = max(float(evidence), 0.0)
    return e / (e + half) if e > 0 else 0.0


def _window_brake_time(events, d_enter: float, d_exit: float) -> float:
    """Total time on the brakes (s) for the brake events whose onset falls in a corner's
    approach+window [d_enter − BRAKE_APPROACH_M, d_exit]. `events` is a list with .onset_dist /
    .duration (driving.BrakeEvent)."""
    lo = d_enter - BRAKE_APPROACH_M
    return sum(float(e.duration) for e in events if lo <= e.onset_dist <= d_exit)


def _brake_extra(med_events, best_events, med_win: tuple[float, float],
                 best_win: tuple[float, float]) -> float:
    """Extra seconds on the brakes vs best in the corner's approach: the median lap's total
    time-on-brakes in the window minus the best lap's, floored at 0 (braking LESS than best is
    not a loss this reason claims). An earlier onset shows up as more time on the brakes, so the
    single time-on-brakes difference captures both 'earlier' and 'longer'.

    Each lap's events live in its OWN odometer (Session reads them off each lap's own distance
    axis), so the SAME corner is a different metre window on each: `med_win`/`best_win` are the
    corner window already projected onto the median / best lap's own odometer (D13). With no
    projection both equal the corner edges, so this is unchanged."""
    return max(_window_brake_time(med_events, *med_win)
               - _window_brake_time(best_events, *best_win), 0.0)


def _coast_in_window(spans, d_enter: float, d_exit: float) -> float:
    """Total coasting DURATION (s) whose span overlaps the corner window [d_enter, d_exit].
    `spans` is a list with .start_dist / .end_dist / .duration (driving.CoastSpan). A span is
    counted (in full) when any part of it lies inside the corner — a coast 'inside the corner'."""
    total = 0.0
    for s in spans:
        if s.end_dist >= d_enter and s.start_dist <= d_exit:
            total += float(s.duration)
    return total


def _coast_extra(med_spans, best_spans, med_win: tuple[float, float],
                 best_win: tuple[float, float]) -> float:
    """Extra coasting seconds inside the corner vs best: the median lap's coasting duration in
    the window minus the best lap's, floored at 0. `med_win`/`best_win` are the corner window
    projected onto each lap's OWN odometer (its spans live there) — see _brake_extra (D13)."""
    return max(_coast_in_window(med_spans, *med_win)
               - _coast_in_window(best_spans, *best_win), 0.0)


def _pick_reason(time_lost: float, apex_speed_delta: float, sigma: float,
                 med_events, best_events, med_spans, best_spans,
                 med_win: tuple[float, float], best_win: tuple[float, float]) -> Reason:
    """Choose the dominant reason for one corner: the strongest of the four comparable strengths
    (largest wins, ties -> _REASON_PRIORITY order). The raw evidence behind each strength is
    carried on the Reason regardless of which won, so the UI/tests read them uniformly, and the
    contribution is time_lost × the winning strength (≤ time_lost — never overclaims).

    `med_win`/`best_win` are the corner window projected onto the median / best lap's OWN
    odometer (the frame their brake/coast events live in — D13). With no projection both equal
    the corner edges, so this is unchanged.

    LINE is the fallback: when no concrete input signal (apex/brake/coast) fires but there IS
    real cross-lap spread, σ carries the row. When nothing fires the reason is REASON_NONE (the
    row still shows the time lost)."""
    apex_deficit = max(-float(apex_speed_delta), 0.0)   # km/h slower than best at the apex
    brake_extra = _brake_extra(med_events, best_events, med_win, best_win)
    coast_extra = _coast_extra(med_spans, best_spans, med_win, best_win)
    sig = max(float(sigma), 0.0)

    # Comparable strengths in [0,1). A reason can only win when the corner is actually losing
    # time (time_lost > 0) — these explain a measured loss, they don't manufacture one.
    lossy = time_lost > 1e-9
    strengths = {
        REASON_APEX: _saturate(apex_deficit, _APEX_HALF_KMH) if lossy else 0.0,
        REASON_BRAKING: _saturate(brake_extra, _BRAKE_HALF_S) if lossy else 0.0,
        REASON_COASTING: _saturate(coast_extra, _COAST_HALF_S) if lossy else 0.0,
        REASON_LINE: _saturate(sig, _SIGMA_HALF_S) if lossy else 0.0,
    }
    # Largest strength; ties broken by the fixed reason priority (apex first). The contribution
    # reported is time_lost × strength (so it is bounded by the corner's own loss).
    best_kind = REASON_NONE
    best_strength = 0.0
    for kind in _REASON_PRIORITY:
        st = strengths.get(kind, 0.0)
        if st > best_strength + 1e-12:  # strictly greater (priority already favours earlier ties)
            best_strength = st
            best_kind = kind
    return Reason(
        kind=best_kind,
        contribution=time_lost * best_strength,
        apex_speed_deficit=apex_deficit,
        brake_extra_s=brake_extra,
        coast_extra_s=coast_extra,
        sigma=sig,
    )


# --------------------------------------------------------------------------- the summary
def summarize(
    corners,
    candidate_lap_ids: list[int],
    lap_times: list[float],
    corner_times_by_lap: list[list[float]],
    best_corner_times: list[float],
    sigmas_by_cid: dict[int, float],
    median_brake_events,
    best_brake_events,
    median_coast_spans,
    best_coast_spans,
    median_apex_deltas: list[float],
    *,
    corner_dist_total: float | None = None,
    median_lap_total: float | None = None,
    best_lap_total: float | None = None,
    top_n: int = TOP_N,
    min_laps: int = MIN_LAPS,
) -> Opportunities:
    """Assemble the ranked opportunities from pre-extracted, pacer-free inputs (Session owns the
    extraction; this stays numpy-only and fully unit-testable on synthetic inputs).

    Arguments (all aligned, all already restricted to the consistency laps / the best lap):
      corners                 list[Corner] (cid/enter/exit/apex/direction), track order.
      candidate_lap_ids       the consistency lap ids (ascending), len == rows of the matrices.
      lap_times               each candidate lap's total time (s), aligned to candidate_lap_ids.
      corner_times_by_lap     per candidate lap, the per-corner time-in-corner aligned to
                              `corners` (one inner list per lap).
      best_corner_times       the best lap's per-corner time-in-corner, aligned to `corners`.
      sigmas_by_cid           cid -> cross-lap σ of time-in-corner (from corner_consistency).
      median_brake_events     the MEDIAN lap's brake events (driving.BrakeEvent list).
      best_brake_events       the BEST lap's brake events.
      median_coast_spans      the MEDIAN lap's coasting spans (driving.CoastSpan list).
      best_coast_spans        the BEST lap's coasting spans.
      median_apex_deltas      the median lap's per-corner apex-speed delta vs the LOCAL best lap
                              (km/h; negative = slower than best), aligned to `corners`. MUST use
                              the SAME baseline as the losses (the local best) — see D13.
      corner_dist_total       the corner edges' (`c.enter`/`c.exit`) odometer reference total (m);
      median_lap_total        the MEDIAN lap's own odometer total (m);
      best_lap_total          the BEST lap's own odometer total (m).
                              These three project each corner window onto each lap's OWN odometer
                              (d = c.enter / corner_dist_total × lap_total) before matching that
                              lap's brake/coast events, which live in its own odometer (D13).
                              When any is None the projection is the identity (the corner edges are
                              used as-is) — the dormant path, byte-identical to before.

    Returns an Opportunities. `enough=False` (empty rows) when < `min_laps` candidate laps."""
    n_laps = len(candidate_lap_ids)
    med_id = median_lap_id(candidate_lap_ids, lap_times)
    if n_laps < min_laps or not corners:
        return Opportunities(enough=False, n_laps=n_laps, median_lap_id=med_id, rows=[])

    times = np.asarray(corner_times_by_lap, float)  # (n_laps, n_corners)
    best = np.asarray(best_corner_times, float)     # (n_corners,)
    n_corners = len(corners)
    # Per-corner median time lost vs the best lap's same corner, over the candidate laps. Guard
    # a ragged matrix (a degenerate lap projecting to fewer corners is already filtered upstream,
    # but stay defensive) by only using columns present for every lap.
    if times.ndim != 2 or times.shape[1] != n_corners or len(best) != n_corners:
        return Opportunities(enough=False, n_laps=n_laps, median_lap_id=med_id, rows=[])
    losses = np.median(times - best[None, :], axis=0)  # (n_corners,)

    # Project a corner's [enter, exit] (in the corner-edge odometer reference) onto one lap's OWN
    # odometer (d = c.enter / corner_dist_total × lap_total — the SAME normalized-distance
    # projection lap_corner_grip / lap_corner_stats use). A lap's brake/coast events are measured
    # in its own odometer, so this puts the corner window in the SAME frame before matching (D13).
    # If any total is missing/degenerate the corner edges are returned as-is (the dormant path,
    # identical to before this fix).
    def _win(c, lap_total: float | None) -> tuple[float, float]:
        if (corner_dist_total and lap_total and corner_dist_total > 0
                and lap_total != corner_dist_total):
            scale = lap_total / corner_dist_total
            return float(c.enter) * scale, float(c.exit) * scale
        return float(c.enter), float(c.exit)

    # Build a row per corner with a positive median loss; rank by the loss (biggest first).
    ranked_idx = [i for i in np.argsort(-losses, kind="stable") if losses[i] > 1e-9]

    rows: list[Opportunity] = []
    for rank, i in enumerate(ranked_idx):
        c = corners[i]
        attach_reason = rank < top_n
        if attach_reason:
            reason = _pick_reason(
                time_lost=float(losses[i]),
                apex_speed_delta=(float(median_apex_deltas[i])
                                  if i < len(median_apex_deltas) else 0.0),
                sigma=float(sigmas_by_cid.get(c.cid, 0.0)),
                med_events=median_brake_events, best_events=best_brake_events,
                med_spans=median_coast_spans, best_spans=best_coast_spans,
                med_win=_win(c, median_lap_total), best_win=_win(c, best_lap_total),
            )
        else:
            reason = Reason(kind=REASON_NONE, contribution=0.0, apex_speed_deficit=0.0,
                            brake_extra_s=0.0, coast_extra_s=0.0,
                            sigma=float(sigmas_by_cid.get(c.cid, 0.0)))
        rows.append(Opportunity(
            cid=c.cid, direction=c.direction, time_lost=float(losses[i]),
            entry_dist=float(c.enter), reason=reason,
        ))
    return Opportunities(enough=True, n_laps=n_laps, median_lap_id=med_id, rows=rows)


# ------------------------------------------------------------------ UI sentence helper
def reason_sentence(opp: Opportunity) -> str:
    """The human, numbers-only coaching sentence for one opportunity's dominant reason. Kept
    here (next to the model) so the panel and any export read ONE phrasing and can't drift."""
    r = opp.reason
    if r.kind == REASON_APEX:
        return f"carry more apex speed (−{r.apex_speed_deficit:.1f} km/h)"
    if r.kind == REASON_BRAKING:
        return f"brake later / shorter (+{r.brake_extra_s:.2f} s on the brakes)"
    if r.kind == REASON_COASTING:
        return f"back to throttle sooner (+{r.coast_extra_s:.2f} s coasting)"
    if r.kind == REASON_LINE:
        return f"be consistent here (σ {r.sigma:.2f} s)"
    return "find time here"
