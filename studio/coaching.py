"""Auto coaching summary: the post-load "opportunities" model.

PACER-FREE BY CONTRACT (numpy only, no Qt). Does NOT recompute corner / driving / consistency
math; it COMPOSES the values Session already caches into a ranked, explainable shortlist of
"where to find time vs your own best lap" — every number measured and deterministic (no ML).

What it does: per corner, the median time lost vs your own best over the consistency laps
(valid, dropout-free); corners ranked by that loss, biggest first. For every ranked corner a
dominant reason (apex / braking / coasting / line) is picked from four signals, each mapped to a
comparable strength; the strongest wins (ties → a fixed reason priority), REASON_NONE when none
fires. summarize returns enough=False under MIN_LAPS consistency laps. Pure + deterministic
(corners in cid order, candidate laps ascending).

Each row also carries `Evidence` — how many of your laps have ALREADY matched that corner's
target and how wide the corner's own interquartile spread is — which decides two things a bare
`time_lost` cannot: whether the corner is execution work or pace work (`REACH_*`), and whether
the row carries a claim at all (`ABSTAIN_*`). The session's rows then cluster into one `Theme`.
See the "spread, reach and the evidence gate" block below for the measured numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import corners as corners_mod
from . import units
from .corners import project_boundaries

# Min clean laps before coaching; the per-corner loss is a MEDIAN, ill-defined/unstable below 3.
MIN_LAPS = 3

# D2: the three equal-distance phases a corner window is split into for the entry/apex/exit
# Δt-vs-best decomposition, in track order. The dominant phase is named in the reason sentence.
PHASE_ENTRY = "entry"
PHASE_APEX = "apex"
PHASE_EXIT = "exit"
PHASES = (PHASE_ENTRY, PHASE_APEX, PHASE_EXIT)

# m prepended to a corner window when matching brake events — braking starts on the straight
# before turn-in (~1 medium-kart brake zone), upstream of the model's cornering-start enter point.
BRAKE_APPROACH_M = 30.0

# Reason ids, ordered by the tie-break PRIORITY when two signals tie: a directly-actionable input
# (apex) over a process cue (braking, coasting); raw inconsistency (line) last.
REASON_APEX = "apex"
REASON_BRAKING = "braking"
REASON_COASTING = "coasting"
REASON_LINE = "line"
REASON_NONE = "none"  # a ranked corner with no positive signal (still shows the time lost)
_REASON_PRIORITY = (REASON_APEX, REASON_BRAKING, REASON_COASTING, REASON_LINE, REASON_NONE)


@dataclass(frozen=True)
class Reason:
    """The dominant measured reason a corner is losing time, with the supporting numbers (only the
    kind-relevant fields are non-zero; all carried so UI/tests read them uniformly)."""

    kind: str
    contribution: float          # estimated s of loss attributed to this reason (the score)
    apex_speed_deficit: float    # best apex − median apex (km/h, > 0 means slower than best)
    brake_extra_s: float         # median lap's extra time-on-brakes in the window vs best (s)
    coast_extra_s: float         # extra coasting duration inside the corner vs best (s)
    sigma: float                 # cross-lap σ of time-in-corner (s)


@dataclass(frozen=True)
class PhaseLoss:
    """D2: the entry / apex(mid) / exit Δt-vs-best decomposition of ONE corner on the TYPICAL
    (median) lap (s). This is a WHERE-IN-THE-CORNER profile of the typical lap vs best — it is NOT
    the Opportunity's ``time_lost`` and does NOT sum to it. ``time_lost`` is the cross-lap MEDIAN
    per-corner delta over the consistency laps; these thirds are a single-lap clock difference over
    the typical lap alone, so the two are different statistics and can disagree in sign (a typical lap
    can be net faster over the window than the corner's median loss). Each third is positive when
    the typical lap is slower than best over that third, negative when faster."""

    entry: float             # Δt over the entry third (first 1/3 of the corner window, s)
    apex: float              # Δt over the apex/mid third (s)
    exit: float              # Δt over the exit third (s)

    @property
    def total(self) -> float:
        """The typical lap's NET Δt-vs-best across the whole corner window (the thirds telescope to
        it). NOT the same statistic as the Opportunity's ``time_lost`` (a cross-lap median) — do not
        present it as 'time lost'; it is the typical-lap net over the window and can be negative."""
        return self.entry + self.apex + self.exit

    @property
    def dominant(self) -> str:
        """The phase id (PHASE_*) costing the most time — the largest of the three thirds. Ties
        resolve to track order (entry, then apex, then exit) for determinism."""
        vals = {PHASE_ENTRY: self.entry, PHASE_APEX: self.apex, PHASE_EXIT: self.exit}
        return max(PHASES, key=lambda p: vals[p])

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.entry, self.apex, self.exit)


# A zero decomposition (no phase data available / window degenerate) — keeps every row uniform.
_NO_PHASES = PhaseLoss(entry=0.0, apex=0.0, exit=0.0)


# ------------------------------------------------ spread, reach and the per-corner evidence gate
#
# WHY THIS EXISTS. `time_lost` is median-minus-your-best-lap, and on its own it cannot tell two
# very different drivers apart. A driver who has hit the target here on 9 of 38 laps and one who
# has hit it twice in 65 get the identical row and the identical sentence — yet the first needs
# "do again what you already did" and the second needs "find something new". Worse, a median-minus-
# best gap can be smaller than the corner's own lap-to-lap scatter, in which case the row is not an
# opportunity at all, it is sampling noise wearing a number.
#
# MEASURED, on the two real D24 recordings (0060: 38 clean laps, 12 corners, 9 rows above the
# panel's display resolution; 0062: 65 clean laps, 12 corners, 11 rows) —
#
#   * σ ≥ time_lost on 17 of those 20 rows. The worst: 0060 C1 lost 0.034 s against σ 0.372 s
#     (10.8x), 0060 C10 lost 0.071 s against σ 0.592 s (8.4x). Those rows shipped a live Jump
#     button beside a number smaller than a tenth of the corner's own scatter.
#   * σ is NOT the right spread statistic: on 0062 C1 it reads 0.226 s while the interquartile
#     range is 0.115 s — 4x apart, because a handful of slow laps drag the second moment and a
#     quartile does not. The gate below reads the IQR.
#   * the reach rate (how many clean laps already matched the corner's target) runs 3 %..34 % and
#     splits cleanly at 1 lap in 10 — 15 of the 20 rows are corners the driver reaches routinely,
#     5 are corners reached about once a session.
#
# WHAT THE BRIEFED PREMISE GOT WRONG, and it is worth writing down: the target the ranking uses is
# the BEST LAP's time through the corner, and that is never a lone outlier. `z > 1.5` against the
# rest of the distribution fired on 0 of 20 rows, and every one of the 20 had at least two OTHER
# laps strictly beating it (2..21 of them). Nor is it "your optimal line": on both recordings the
# best lap's corner time was slower than that corner's own best instance at all 12 of 12 corners
# (by 0.07..0.55 s). So ABSTAIN_ONE_OFF below is a real guard that has never fired on real data —
# it is kept because a 3-lap session can trivially produce it, not because it is common.

# A corner needs at least this many clean instances before ANY per-corner claim is made about it.
# (The session-level MIN_LAPS gate above is a different question — it asks whether the median is
# defined at all; this one asks whether THIS corner was actually driven enough times to talk about.)
MIN_CORNER_LAPS = 3

# "You have already done this" needs more than one lap saying so. The BEST LAP's own instance is
# always in the candidate set, so a reach count of 1 means nothing but the baseline itself ever got
# there — a target with no second witness is not a target.
MIN_REACH_LAPS = 2

# ...and it needs to be more than a rounding-level rate: fewer than 1 lap in 10 at the target is a
# corner you have visited, not a pace you have established. Measured, the 20 real rows' reach rates
# sort as 4.6 4.6 4.6 7.7 7.9 | 13.2 13.8 16.9 18.4 18.4 18.5 23.7 24.6 26.3 26.3 28.9 29.2 30.8
# 31.6 33.8 % — the widest gap in the whole set sits exactly on 10 %.
REACH_REPEAT_FRAC = 0.10

# A claim must clear half the corner's own INTERQUARTILE spread to be aimable. Not a significance
# test — with 38-65 laps the standard error of a median is ~0.03 s and almost nothing would abstain
# — but an ACTIONABILITY test: a driver cannot aim at 0.03 s inside a band whose middle half is
# 0.20 s wide, however real the 0.03 s is. Measured: 6 of the 20 real rows abstain here, including
# 0062's THIRD-ranked corner (C12, 0.078 s on offer against a 0.236 s interquartile band).
SPREAD_MARGIN = 0.5

# How a corner's target relates to what the driver has actually produced — the "can't vs didn't"
# axis, and the one thing that changes the instruction rather than only the number.
REACH_REPEAT = "repeat"    # reached routinely (>= REACH_REPEAT_FRAC of laps): EXECUTION work
REACH_RARE = "rare"        # reached, but seldom: PACE work — new ground, not a repeat
REACH_NEVER = "never"      # no clean lap has ever matched the target
REACH_UNKNOWN = "unknown"  # not measured (a row built without the per-lap times) — no claim either way

# Why a corner carries no ranked claim. "" is the ranked case; the rest are shown, never dropped —
# a row that says why it is not ranked is worth more than a row that silently disappears.
ABSTAIN_NONE = ""
ABSTAIN_FEW_LAPS = "few_laps"   # too few clean instances of this corner to say anything
ABSTAIN_ONE_OFF = "one_off"     # nothing but the baseline itself ever reached the target
ABSTAIN_SPREAD = "spread"       # the claim is inside the corner's own lap-to-lap scatter


@dataclass(frozen=True)
class Evidence:
    """What is actually known about ONE corner: how repeatable its target is, how wide its own
    spread is, and whether that is enough to make a claim."""

    n_laps: int          # clean laps with a finite time through this corner
    reach_laps: int      # how many of them matched or beat the target (the best lap's own time)
    reach: str           # REACH_* — the can't/didn't axis
    iqr: float           # interquartile range of time-in-corner (s); robust where sigma is not
    abstain: str         # ABSTAIN_* — "" when the row carries a ranked claim

    @property
    def ranked(self) -> bool:
        """True when this corner's row is a real opportunity (nothing gated it out)."""
        return not self.abstain

    @property
    def reach_frac(self) -> float:
        """Fraction of clean laps that already matched the target (0.0 when nothing measured)."""
        return self.reach_laps / self.n_laps if self.n_laps else 0.0


# The "not measured" evidence: a row built without per-lap times (a synthetic/legacy construction).
# It deliberately does NOT abstain and carries no reach claim — an unmeasured row must behave
# exactly as rows did before the gate existed, rather than being silently gated out.
_NO_EVIDENCE = Evidence(n_laps=0, reach_laps=0, reach=REACH_UNKNOWN, iqr=0.0,
                        abstain=ABSTAIN_NONE)


def corner_evidence(times, target: float, time_lost: float) -> Evidence:
    """One corner's Evidence from its per-lap times and the target the loss is measured against.

    `times` are that corner's time-in-corner over the candidate laps (non-finite entries dropped);
    `target` is the BEST LAP's time through the same corner — the baseline `time_lost` is measured
    from, so "reached" means literally "drove this corner at least as fast as your best lap did".

    The three abstain tests are applied in a FIXED priority so the reported reason is deterministic
    and the most fundamental objection wins: not enough instances beats no-second-witness beats
    inside-the-scatter."""
    t = np.asarray(list(times), float)
    t = t[np.isfinite(t)]
    n = int(len(t))
    if n == 0:
        return Evidence(n_laps=0, reach_laps=0, reach=REACH_NEVER, iqr=0.0,
                        abstain=ABSTAIN_FEW_LAPS)
    # "Matched or beat" — the best lap's own instance sits exactly on the target, so the epsilon is
    # float slack on an identical computation, not a tolerance band.
    reach_laps = int(np.count_nonzero(t <= float(target) + 1e-9))
    q25, q75 = (np.percentile(t, [25, 75]) if n >= 2 else (t[0], t[0]))
    iqr = float(q75 - q25)
    if reach_laps == 0:
        reach = REACH_NEVER
    elif reach_laps >= MIN_REACH_LAPS and reach_laps >= REACH_REPEAT_FRAC * n:
        reach = REACH_REPEAT
    else:
        reach = REACH_RARE
    if n < MIN_CORNER_LAPS:
        abstain = ABSTAIN_FEW_LAPS
    elif reach_laps < MIN_REACH_LAPS:
        abstain = ABSTAIN_ONE_OFF
    elif float(time_lost) < SPREAD_MARGIN * iqr:
        abstain = ABSTAIN_SPREAD
    else:
        abstain = ABSTAIN_NONE
    return Evidence(n_laps=n, reach_laps=reach_laps, reach=reach, iqr=iqr, abstain=abstain)


# ------------------------------------------- the braking habit: ONE answer to "how much later?"
#
# WHY THIS EXISTS. The app answered the driver's plainest question — "how much later can I brake
# into this corner?" — with TWO different numbers on two surfaces, and named neither. The coaching
# row's hint read the BEST lap's single brake application (`driving.BrakePoint.metres_later` on
# `best_lap_id()`); Stats ▸ BRAKING's "m later" column read the MEDIAN of that same quantity over
# the clean laps. Nothing cross-referenced them, so the disagreement was invisible and unresolvable.
#
# MEASURED, on the two real D24 recordings, over the corners whose hint the coaching panel actually
# showed (best-lap value vs BRAKING's median, metres):
#
#   0060 (38 laps)  C2 29.0 / 22.9   C5 24.1 / 16.6   C9 22.7 / 17.3   C12 9.6 / 14.9
#                   C7 13.7 / 14.6   C4 16.5 / 16.6            — median gap 5.3 m, worst 7.5 m
#   0062 (65 laps)  C1  2.9 / 12.2   C5 13.4 / 15.9   C7 10.7 / 13.1  — median gap 2.5 m, worst 9.3
#
# 0062's C1 is the one that shows what the split cost: the best lap happened to brake within 3 m of
# its own optimum, so coaching printed "~3 m later" — barely over the BRAKE_HINT_MIN_M noise floor,
# i.e. a shrug — while the driver's HABIT over 62 laps was 12.2 m early. Opposite advice from one
# lap of sampling noise. The reverse case is just as bad: on 0062 the best lap had no matched brake
# event at all into C3 or C9, so coaching said nothing about corners 44 and 39 laps DID brake into.
#
# THE RULE, therefore: a coaching instruction is about the driver's HABIT, so it is a cross-lap
# statistic over the same clean laps every other number on the row already uses (`time_lost` is a
# cross-lap median; "Done it?" is a count over those laps). The best lap's own application is one
# sample of a scattered distribution — the BRAKING table's σ and span columns exist precisely
# because that scatter is large — and it was the only number on the row that was not.

# A braking habit needs at least this many matched applications before it is a habit. (Measured, a
# real recording is nowhere near it: the corners above matched on 32..38 of 38 and 39..65 of 65
# laps. This guards a 3-lap session, not a normal one.)
MIN_BRAKE_LAPS = 3


@dataclass(frozen=True)
class BrakeHabit:
    """One corner's braking habit over the clean laps — the app's SINGLE answer to "how much later
    could I brake here", and the same number the Stats ▸ BRAKING table's "m later" column shows
    (both are the median of the same per-lap `driving.BrakePoint.metres_later` list).

    Distances are the REFERENCE (best-lap) odometer, the frame `Opportunity.entry_dist` and the
    corner windows live in, so a brake point can be named against the corner's own turn-in."""

    cid: int                    # the Corner.cid this habit belongs to
    n_laps: int                 # clean laps with a matched brake application into this corner
    metres_later: float         # MEDIAN optimal − actual (+ = you could brake later)
    optimal_brake_dist: float   # median apex-speed-matched latest sustainable brake point (m)
    actual_brake_dist: float    # median onset where the driver actually brakes (m)
    # The OBSERVED middle half of `metres_later` across those laps — the spread the recommendation
    # is drawn from, never a modelled confidence interval. It is what makes the median checkable:
    # "~23 m later, and the middle half of your laps wanted 19–26 m" is a claim a driver can test.
    q25_m: float
    q75_m: float


def brake_habits(cids, rows_by_lap) -> dict[int, BrakeHabit]:
    """Per-corner braking habits from the SAME per-lap rows `stats.brake_consistency` aggregates.

    `rows_by_lap`: one dict per clean lap, cid → (onset_ref_m, commit_frac | None,
    metres_later | None, optimal_ref_m | None) — a corner missing from a lap's dict simply had no
    matched brake application on it. Corners with no matched application anywhere, or with an
    unusable optimum, are absent from the result rather than present with a fabricated zero.

    The headline `metres_later` is `median` over exactly the list `brake_consistency` medians for
    its own column, so the two surfaces cannot report different numbers for the same corner."""
    out: dict[int, BrakeHabit] = {}
    for cid in cids:
        vals = [r[cid] for r in rows_by_lap if cid in r]
        laters = np.asarray([v[2] for v in vals if v[2] is not None], float)
        optima = np.asarray([v[3] for v in vals if len(v) > 3 and v[3] is not None], float)
        onsets = np.asarray([v[0] for v in vals if v[0] is not None], float)
        if len(laters) == 0 or len(optima) == 0 or len(onsets) == 0:
            continue
        q25, q75 = np.percentile(laters, [25, 75]) if len(laters) >= 2 else (laters[0], laters[0])
        out[int(cid)] = BrakeHabit(
            cid=int(cid), n_laps=len(laters), metres_later=float(np.median(laters)),
            optimal_brake_dist=float(np.median(optima)),
            actual_brake_dist=float(np.median(onsets)),
            q25_m=float(q25), q75_m=float(q75))
    return out


@dataclass(frozen=True)
class Opportunity:
    """One corner's coaching row: how much time is realistically available and why."""

    cid: int                 # 1-based corner id (track order)
    direction: int           # +1 left / -1 right (for the UI glyph)
    time_lost: float         # median time lost vs the best lap's same corner (s, > 0)
    entry_dist: float        # the corner's enter odometer on the BEST lap (m) — the jump-to seek
    reason: Reason           # the dominant measured reason + numbers (REASON_NONE = none fired)
    # D2: typical-lap entry/apex/exit Δt-vs-best thirds (s) — a WHERE-in-the-corner profile, a
    # DIFFERENT statistic from time_lost (their sum is the typical lap's net, not the median loss).
    phases: PhaseLoss = _NO_PHASES
    # Whether this corner is execution work or pace work, and whether it carries a claim at all.
    evidence: Evidence = _NO_EVIDENCE


# ------------------------------------------------------------------------- the session theme
#
# Twelve findings is not coaching; one theme plus at most two actions is. The clustering runs over
# what this app can MEASURE — the reach axis above and the four driving signals — and over SHARE OF
# RANKED TIME rather than a row count, because the ranking's own unit is seconds and a count lets
# six trivial corners outvote the one that matters.
#
# MEASURED, on the two real recordings, over the ranked (non-abstained) rows: 0060 splits 73 %
# execution / 27 % pace and 0062 splits 35 % / 65 % — SAME driver, SAME track, weeks apart, and
# the theme comes out opposite. That is the finding that justifies the feature. The cause axis is
# weaker: braking holds 61 % of 0060's ranked time (a theme) but only 44 % of 0062's (not one), so
# the cause line is conditional and will often read "no single cause dominates".
#
# AND THE HONEST CAVEAT, also measured: the theme is a property of the LAP SET, and it moves with
# it. Loading only chapter 2 of each recording (24 laps instead of 38 and 65) flips BOTH verdicts —
# 0060 reads 86 % pace and 0062 reads 72 % execution. Corners cluster near the 1-in-10 reach line,
# so a different lap set moves several of them across it at once. The sentence therefore always
# states its own share, and THEME_SPLIT exists so a balanced session is not forced to pick a side.
THEME_EXECUTION = "execution"  # most of the ranked time is in corners already driven at this pace
THEME_PACE = "pace"            # most of it is in corners the driver has rarely reached
THEME_SPLIT = "split"          # neither side holds a clear majority — say so, don't invent one
THEME_NONE = "none"            # nothing ranked (every row abstained, or there are no rows)

# The share one side must hold before it is called the session's theme. 0.60 is a clear majority
# with room to spare; measured, 0060 lands at 0.73 and 0062 at 0.65, and 0060 would fall to a SPLIT
# if its C4 (reached on 5 of 38 laps, just over the 1-in-10 line) tipped the other way.
THEME_SHARE = 0.60


@dataclass(frozen=True)
class Theme:
    """The one-line story the session's ranked rows add up to, plus the numbers behind it."""

    kind: str                        # THEME_*
    share: float                     # the winning side's share of ranked time (0..1)
    execution_s: float               # ranked seconds in REACH_REPEAT corners
    pace_s: float                    # ranked seconds in REACH_RARE / REACH_NEVER corners
    n_ranked: int                    # rows carrying a claim
    n_abstained: int                 # rows shown but not ranked (the evidence gate)
    cause: str                       # the REASON_* holding the most ranked time (REASON_NONE if none)
    cause_share: float               # its share of ranked time (0..1)
    cause_cids: tuple[int, ...] = ()  # the corners that cause covers, in ranked order
    lead_cid: int | None = None      # the biggest single ranked corner — the "start here"


_NO_THEME = Theme(kind=THEME_NONE, share=0.0, execution_s=0.0, pace_s=0.0, n_ranked=0,
                  n_abstained=0, cause=REASON_NONE, cause_share=0.0)


def session_theme(rows: list[Opportunity]) -> Theme:
    """Cluster the rows into ONE theme. Pure, deterministic, and it only ever reads the reach axis
    and the four measured reasons — this app cannot see vision or reference points and must not
    name a cause it cannot measure.

    Only RANKED rows vote: an abstained row has no claim, so letting it weigh on the theme would
    reintroduce through the back door exactly the noise the gate removed."""
    ranked = [r for r in rows if r.evidence.ranked]
    abstained = len(rows) - len(ranked)
    total = sum(r.time_lost for r in ranked)
    if not ranked or total <= 0:
        return Theme(kind=THEME_NONE, share=0.0, execution_s=0.0, pace_s=0.0, n_ranked=0,
                     n_abstained=abstained, cause=REASON_NONE, cause_share=0.0)
    execution_s = sum(r.time_lost for r in ranked if r.evidence.reach == REACH_REPEAT)
    pace_s = sum(r.time_lost for r in ranked
                 if r.evidence.reach in (REACH_RARE, REACH_NEVER))
    # REACH_UNKNOWN rows (built without per-lap times) count towards neither side, so an unmeasured
    # session reports a SPLIT rather than a theme it has no evidence for.
    if execution_s >= THEME_SHARE * total:
        kind, share = THEME_EXECUTION, execution_s / total
    elif pace_s >= THEME_SHARE * total:
        kind, share = THEME_PACE, pace_s / total
    else:
        kind, share = THEME_SPLIT, max(execution_s, pace_s) / total
    # The cause axis, on the same share-of-time basis and the same threshold. Ties resolve by
    # _REASON_PRIORITY so the winner is deterministic.
    by_cause = {k: sum(r.time_lost for r in ranked if r.reason.kind == k)
                for k in _REASON_PRIORITY}
    cause = max(_REASON_PRIORITY, key=lambda k: (by_cause[k], -_REASON_PRIORITY.index(k)))
    cause_share = by_cause[cause] / total
    if cause == REASON_NONE or cause_share < THEME_SHARE:
        cause, cause_share, cids = REASON_NONE, 0.0, ()
    else:
        cids = tuple(r.cid for r in ranked if r.reason.kind == cause)
    return Theme(kind=kind, share=share, execution_s=execution_s, pace_s=pace_s,
                 n_ranked=len(ranked), n_abstained=abstained, cause=cause,
                 cause_share=cause_share, cause_cids=cids, lead_cid=ranked[0].cid)


@dataclass(frozen=True)
class Opportunities:
    """The whole summary the panel renders. `enough` is False (and `rows` empty) when there are
    fewer than MIN_LAPS consistency laps — the friendly "need more laps" state."""

    enough: bool
    n_laps: int                              # consistency laps the summary ran over
    median_lap_id: int | None                # the representative lap the reasons read off
    rows: list[Opportunity] = field(default_factory=list)
    # The clustered one-line story over `rows` (THEME_NONE when nothing is ranked). Computed once
    # by summarize() so the panel, the modal and any export state the SAME theme.
    theme: Theme = _NO_THEME

    def ranked_rows(self) -> list[Opportunity]:
        """The rows carrying a claim — `rows` minus everything the evidence gate abstained on.
        `rows` is ordered ranked-first, so this is a prefix."""
        return [r for r in self.rows if r.evidence.ranked]


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
# Each reason's raw evidence is on its own scale, so each maps to a unitless strength in [0,1) via
# evidence/(evidence + half). The strengths are comparable; the contribution is time_lost ×
# strength, so no reason overclaims the corner's own loss.

# Evidence level at which each reason hits half strength (_saturate); only sets ties between
# co-present causes — ranking insensitive within a wide band. Tuned on the D24 recordings.
_APEX_HALF_KMH = 3.0   # km/h apex deficit; below ~1 is line/GPS noise
_BRAKE_HALF_S = 0.30   # s longer/earlier than best; sub-0.1 is threshold ripple
_COAST_HALF_S = 0.30   # ~ the shortest coast the channel reports
_SIGMA_HALF_S = 0.15   # s lap-to-lap σ; below ~0.05 the line is repeatable


def _saturate(evidence: float, half: float) -> float:
    """A unitless strength in [0, 1): evidence/(evidence + half), 0 for non-positive evidence.
    Half-strength at `evidence == half`, →1 for evidence ≫ half. Makes the four reasons'
    different-unit evidence directly comparable without a magic unit conversion."""
    e = max(float(evidence), 0.0)
    return e / (e + half) if e > 0 else 0.0


def _window_brake_time(events, d_enter: float, d_exit: float,
                       dist: np.ndarray | None = None,
                       elapsed: np.ndarray | None = None) -> float:
    """Time on the brakes (s) spent INSIDE a corner's approach+window
    [d_enter − BRAKE_APPROACH_M, d_exit], integrating each brake event's OVERLAP with that window.
    `events` is a list with .onset_dist / .onset_time / .duration (driving.BrakeEvent); `dist` /
    `elapsed` are the SAME lap's odometer + seconds-from-lap-start arrays the events were detected
    on (see _brake_extra).

    An event carries no release odometer, so it is recovered by interpolating onset_time + duration
    through the lap's own clock (which lands back on the detector's release sample exactly, since
    duration IS elapsed[release] − elapsed[onset]); the clipped span is converted back to seconds
    the same way, so what is counted is the time the lap actually spent braking between the two
    distances. Without the arrays the event degenerates to a point at its onset — the pre-overlap
    rule, kept only for callers that have no trace.

    Matching the sibling _coast_in_window, which already tests OVERLAP. The onset-membership rule
    scored the SAME brake application as 0.00 s or its full duration depending on which side of an
    arbitrary cut its onset landed: on D24 the best lap's 2.2 s application into C3 began 14.5 m
    upstream of the cut and scored 0.00 s, so a corner losing 0.109 s printed "~0.90 s longer on
    the brakes"."""
    lo = d_enter - BRAKE_APPROACH_M
    if dist is None or elapsed is None or len(dist) < 2 or len(elapsed) < 2:
        return sum(float(e.duration) for e in events if lo <= e.onset_dist <= d_exit)
    total = 0.0
    for e in events:
        d_release = float(np.interp(float(e.onset_time) + float(e.duration), elapsed, dist))
        a = max(float(e.onset_dist), lo)
        b = min(d_release, d_exit)
        if b <= a:  # the event and the window do not overlap
            continue
        total += max(float(np.interp(b, dist, elapsed))
                     - float(np.interp(a, dist, elapsed)), 0.0)
    return total


def _brake_extra(med_events, best_events, med_win: tuple[float, float],
                 best_win: tuple[float, float],
                 med_trace: tuple = (None, None), best_trace: tuple = (None, None)) -> float:
    """Extra s on the brakes vs best in the corner approach, floored at 0. An earlier onset shows
    up as more time on the brakes, so this one difference captures both 'earlier' and 'longer'.
    med_win/best_win are the corner window projected onto each lap's own odometer (see _win);
    med_trace/best_trace are that lap's (dist, elapsed) arrays for the overlap integral."""
    return max(_window_brake_time(med_events, *med_win, *med_trace)
               - _window_brake_time(best_events, *best_win, *best_trace), 0.0)


def _coast_in_window(spans, d_enter: float, d_exit: float) -> float:
    """Total coasting DURATION (s) of the spans (driving.CoastSpan) whose span overlaps
    [d_enter, d_exit] (counted in full)."""
    total = 0.0
    for s in spans:
        if s.end_dist >= d_enter and s.start_dist <= d_exit:
            total += float(s.duration)
    return total


def _coast_extra(med_spans, best_spans, med_win: tuple[float, float],
                 best_win: tuple[float, float]) -> float:
    """Extra coasting seconds inside the corner vs best, floored at 0. med_win/best_win projected
    onto each lap's own odometer (see _win)."""
    return max(_coast_in_window(med_spans, *med_win)
               - _coast_in_window(best_spans, *best_win), 0.0)


# ---------------------------------------------------------- D2: entry/apex/exit Δt decomposition
# A corner window [enter, exit] is split into three equal-distance thirds (entry, apex/mid, exit).
# The time each lap spends in a third is READ OFF THAT LAP'S OWN CLOCK at the two odometer edges,
# and the loss is the difference of two such readings (positive ⇒ slower than best, negative ⇒
# faster), so the three telescope EXACTLY to the typical lap's net Δt-vs-best across the window (a
# WHERE-in-the-corner profile of one lap; NOT the Opportunity's cross-lap-median time_lost, which
# is a different statistic and need not agree).
#
# It used to compute the span time as ∫ds/v over the smoothed speed channel. That is the only
# option WITHOUT a per-sample clock, and `_span_clock` below records what it cost when measured
# against one.

def _span_clock(dist: np.ndarray, elapsed: np.ndarray, d0: float, d1: float) -> float:
    """Seconds between two odometer points on ONE lap, read off that lap's own elapsed clock by
    edge interpolation — the same quantity, measured the same way, as `corners.segment_times`.

    THIS USED TO BE `∫ds/v` on a 64-point-per-third grid of the SMOOTHED speed channel, and the
    two do not agree. Measured on the best lap of the D24 0060 pair, where there is no drift and
    no comparison — the corner's own time, both ways:

        C7   clock 4.484 s   ∫ds/v 4.870 s   +0.385 s  (+8.6%)   apex 31.1 km/h
        C11  clock 6.384 s   ∫ds/v 6.877 s   +0.493 s  (+7.7%)   apex 19.2 km/h
        whole lap: clock 68.228 s (= lap_time) vs ∫ds/v 67.748 s

    Every corner was off, r = -0.46 between apex speed and the signed error: 1/v amplifies any
    speed error exactly where the kart is slowest, which is exactly where a corner's time is
    largest. That error then landed in a user-facing number — the coaching row's phase bars sit
    beside a true-clock "time lost", and on 4 of 11 D24 rows the bars netted FASTER than the loss
    they were decomposing (C7: +0.096 s listed, bars netting -0.508 s).

    The clock is the validated timing (GPS9 true clock, transponder-validated; `segment_times`
    and every headline number already read it), so the decomposition reads it too. The thirds now
    telescope EXACTLY to the corner's own `CornerStat.time`, by construction rather than by
    approximation."""
    if not (d1 > d0):
        return 0.0
    return float(np.interp(d1, dist, elapsed) - np.interp(d0, dist, elapsed))


def corner_phase_losses(
    lap_dist: np.ndarray, lap_elapsed: np.ndarray,
    best_dist: np.ndarray, best_elapsed: np.ndarray,
    c_enter: float, c_exit: float,
    *,
    corner_dist_total: float | None = None,
    lap_total: float | None = None,
    best_total: float | None = None,
    lap_traces: tuple | None = None,
    best_traces: tuple | None = None,
    frame=None,
    lap_align=corners_mod.DERIVE_ALIGNMENT,
    best_align=corners_mod.DERIVE_ALIGNMENT,
    best_thirds: tuple[float, float, float] | None = None,
) -> PhaseLoss:
    """Decompose ONE corner's Δt-vs-best into entry / apex(mid) / exit thirds (seconds).

    The corner window [c_enter, c_exit] is in the reference (best-lap) odometer; it is projected
    onto EACH lap's own odometer by the drift-gated alignment (project_boundaries — normalized
    distance d·lap_total/corner_dist_total within NORMALIZED_DRIFT_MAX, one monotone spatial warp
    for the whole lap above it, the SAME alignment lap_corner_stats uses), so the third boundaries
    land on the same TRACK positions on both laps. Each lap's window is split into three
    equal-distance thirds; per third Δt = (this lap's clock over the third) − (best's), read off
    each lap's own elapsed array by the same edge interpolation `segment_times` uses (see
    `_span_clock` for why this is the clock and not ∫ds/v). The thirds telescope EXACTLY to this
    lap's `CornerStat.time` − best's across the window — a where-in-the-corner profile of one lap,
    NOT the cross-lap-median time_lost. Positive ⇒ the lap is slower than best over that third.

    `lap_traces`/`best_traces` (Session-fed (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) for
    the typical / best lap respectively) enable the spatial alignment; omitted → normalized,
    byte-identical to the pre-gate output. `frame` is the WHOLE partition's reference boundaries
    (every corner's enter/exit): a lap's warp is built from all of them, so this one corner's
    window is the same window `lap_corner_stats` measured rather than a two-knot warp of its own.
    `lap_align`/`best_align` are that warp ALREADY BUILT (`corners.lap_alignment`): a caller looping
    over the corners of one lap should build it once and pass it, or the lap's spatial match re-runs
    per corner — 106 ms of a 168 ms `Session.phase_report` on the 38-lap D24 0060 pair (43.9 ms
    once hoisted).
    Returns a zero PhaseLoss when either trace is unusable or the window is degenerate."""
    lap_dist = np.asarray(lap_dist, float)
    lap_elapsed = np.asarray(lap_elapsed, float)
    best_dist = np.asarray(best_dist, float)
    best_elapsed = np.asarray(best_elapsed, float)
    if len(lap_dist) < 2 or len(best_dist) < 2 or not (c_exit > c_enter):
        return _NO_PHASES

    def _proj(total: float | None, traces: tuple | None, align) -> tuple[float, float]:
        # Project the reference-odometer window [c_enter, c_exit] onto a lap's own odometer via the
        # shared drift gate (identity if a total is missing or equals the corner basis' total — the
        # best lap's own frame; traces enable the spatial alignment above the drift bound).
        if (corner_dist_total and total and corner_dist_total > 0
                and total != corner_dist_total):
            proj = project_boundaries([c_enter, c_exit], corner_dist_total, total,
                                      traces=traces, frame=frame, alignment=align)
            return float(proj[0]), float(proj[1])
        return c_enter, c_exit

    lap0, lap1 = _proj(lap_total, lap_traces, lap_align)
    # Equal-distance thirds of each lap's own projected window (same fraction → same track third).
    lap_edges = np.linspace(lap0, lap1, 4)
    if best_thirds is None:
        best_thirds = corner_best_thirds(
            best_dist, best_elapsed, c_enter, c_exit,
            corner_dist_total=corner_dist_total, best_total=best_total,
            best_traces=best_traces, frame=frame, best_align=best_align)
    out = []
    for k in range(3):
        dt_lap = _span_clock(lap_dist, lap_elapsed, lap_edges[k], lap_edges[k + 1])
        out.append(dt_lap - best_thirds[k])
    return PhaseLoss(entry=out[0], apex=out[1], exit=out[2])


def corner_best_thirds(
    best_dist: np.ndarray, best_elapsed: np.ndarray,
    c_enter: float, c_exit: float,
    *,
    corner_dist_total: float | None = None,
    best_total: float | None = None,
    best_traces: tuple | None = None,
    frame=None,
    best_align=corners_mod.DERIVE_ALIGNMENT,
) -> tuple[float, float, float]:
    """The BEST lap's own clock over the three thirds of one corner — the subtrahend half of
    `corner_phase_losses` (which is `lap third − best third`, per third).

    It depends only on the corner and the best lap, so it is IDENTICAL for every comparison lap.
    `Session.phase_report` decomposes every consistency lap against the best, and was recomputing
    this inside each one: on the D24 0060 pair (37 comparison laps × 12 corners) that is 1,332 of
    the 1,776 span reads doing work already done — 12x duplication of exactly half the work.
    Hoist it per corner and pass it as `best_thirds=`.

    Same arithmetic in the same order, so the result is bit-identical to deriving it inline."""
    best0, best1 = c_enter, c_exit
    if (corner_dist_total and best_total and corner_dist_total > 0
            and best_total != corner_dist_total):
        proj = project_boundaries([c_enter, c_exit], corner_dist_total, best_total,
                                  traces=best_traces, frame=frame, alignment=best_align)
        best0, best1 = float(proj[0]), float(proj[1])
    best_edges = np.linspace(best0, best1, 4)
    return tuple(_span_clock(best_dist, best_elapsed, best_edges[k], best_edges[k + 1])
                 for k in range(3))


def _pick_reason(time_lost: float, apex_speed_delta: float, sigma: float,
                 med_events, best_events, med_spans, best_spans,
                 med_win: tuple[float, float], best_win: tuple[float, float],
                 med_trace: tuple = (None, None), best_trace: tuple = (None, None)) -> Reason:
    """Choose the dominant reason for one corner: the strongest of the four comparable strengths
    (largest wins, ties → _REASON_PRIORITY order). All raw evidence is carried on the Reason; the
    contribution is time_lost × the winning strength (≤ time_lost — never overclaims).

    LINE is the fallback (real spread but no concrete input fires); REASON_NONE when nothing fires
    (the row still shows the time lost)."""
    apex_deficit = max(-float(apex_speed_delta), 0.0)   # km/h slower than best at the apex
    brake_extra = _brake_extra(med_events, best_events, med_win, best_win, med_trace, best_trace)
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
    median_dist: np.ndarray | None = None,
    median_elapsed: np.ndarray | None = None,
    best_dist: np.ndarray | None = None,
    best_elapsed: np.ndarray | None = None,
    median_traces: tuple | None = None,
    best_traces: tuple | None = None,
    median_align=corners_mod.DERIVE_ALIGNMENT,
    best_align=corners_mod.DERIVE_ALIGNMENT,
    top_n: int | None = None,
    min_laps: int = MIN_LAPS,
) -> Opportunities:
    """Assemble the ranked opportunities from pre-extracted, pacer-free inputs (Session owns the
    extraction; numpy-only, unit-testable on synthetic inputs).

    All arrays are aligned to candidate_lap_ids / corners and pre-restricted to the consistency
    laps + best lap. median_apex_deltas MUST use the SAME local-best baseline as the losses.
    corner_dist_total / median_lap_total / best_lap_total project each corner window onto each
    lap's own odometer before matching its brake/coast events; any None → identity projection.
    median_dist + best_dist are the typical-lap and best-lap odometers; with the matching elapsed
    arrays each row gets the D2 entry/apex/exit Δt-vs-best decomposition (the typical lap vs best,
    same comparison the reasons use) — absent → zero phases.
    median_elapsed/best_elapsed are the seconds-from-lap-start arrays the decomposition READS ITS
    TIMES FROM (see `_span_clock`: it used to integrate ds/v instead, which disagreed with the
    corner's own time by up to 0.49 s at the slowest corner). With them a brake
    event's OVERLAP with the corner window is integrated on the lap's own clock instead of the event
    being taken or dropped whole by its onset (_window_brake_time) — absent → that degenerate rule.
    median_traces/best_traces are the matching local-frame xy traces ((ref_xs, ref_ys, ref_cum,
    lap_xs, lap_ys, lap_cum) for the typical / best lap); they enable the drift-gated spatial
    boundary alignment in the phase decomposition (omitted → normalized, byte-identical pre-gate).
    median_align/best_align are those two laps' warps ALREADY BUILT (Session hands over the corner
    service's memoized ones); omitted → derived here from the traces, exactly as before.
    top_n caps how many ranked rows get a dominant reason attached; None (the default) analyses
    EVERY ranked row, so a REASON_NONE row means "measured, nothing fired" rather than "not looked
    at" — the rows below any cap are shown too (the Opportunities dialog lists all of them).
    Every row also gets its `Evidence` (from the same corner_times_by_lap column and the same
    best_corner_times baseline — no new inputs) and the rows are ordered ranked-first,
    abstained-last; the clustered `Theme` over the ranked rows rides on the result.
    Returns Opportunities; enough=False (empty rows) when < min_laps candidate laps."""
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

    # Project [enter,exit] onto one lap's own odometer (scale lap_total/corner_dist_total); identity
    # if a total is missing. A lap's brake/coast events live in its own odometer, so this matches frames.
    #
    # STILL UN-GATED, and knowingly: this is the one corner-window projection that has not moved
    # onto corners.lap_alignment, so on a >NORMALIZED_DRIFT_MAX lap the window feeding
    # Reason.brake_extra_s / coast_extra_s is up to ~12 m from the window the row's own phase
    # triple was measured in. Migrating it needs the (ref, lap) traces plumbed to this call site —
    # the follow-up corner_model.corner_entry_media_time's note names.
    def _win(c, lap_total: float | None) -> tuple[float, float]:
        if (corner_dist_total and lap_total and corner_dist_total > 0
                and lap_total != corner_dist_total):
            scale = lap_total / corner_dist_total
            return float(c.enter) * scale, float(c.exit) * scale
        return float(c.enter), float(c.exit)

    # D2: the typical lap's (odometer, elapsed) trace + best lap's, for the entry/apex/exit Δt
    # decomposition — the CLOCK, not the speed channel (`_span_clock`). Both must be present (and
    # usable) to attach phases; otherwise zero phases.
    have_phases = (median_dist is not None and median_elapsed is not None
                   and best_dist is not None and best_elapsed is not None)

    # L5-01: each lap's (odometer, seconds-from-start) pair, so a brake event's overlap with the
    # corner window is integrated on that lap's own clock (a BrakeEvent carries no release
    # odometer). Missing either half → (None, None) → the degenerate onset rule.
    med_trace = ((median_dist, median_elapsed)
                 if median_dist is not None and median_elapsed is not None else (None, None))
    best_trace = ((best_dist, best_elapsed)
                  if best_dist is not None and best_elapsed is not None else (None, None))

    # The WHOLE partition's reference boundaries: each lap's drift-gated warp is built from all of
    # them, so a per-corner phase window is the same window the Corners table measured. The two
    # warps are built ONCE here, not once per corner inside the loop below — and when the caller
    # passes them in (Session reads them off the corner service's memo) not even once.
    phase_frame = [b for c in corners for b in (float(c.enter), float(c.exit))]
    if median_align is corners_mod.DERIVE_ALIGNMENT:
        median_align = (corners_mod.lap_alignment(phase_frame, corner_dist_total,
                                                  median_lap_total, traces=median_traces)
                        if corner_dist_total and median_lap_total else None)
    if best_align is corners_mod.DERIVE_ALIGNMENT:
        best_align = (corners_mod.lap_alignment(phase_frame, corner_dist_total, best_lap_total,
                                                traces=best_traces)
                      if corner_dist_total and best_lap_total else None)

    # Build a row per corner with a positive median loss; rank by the loss (biggest first).
    ranked_idx = [i for i in np.argsort(-losses, kind="stable") if losses[i] > 1e-9]

    rows: list[Opportunity] = []
    for rank, i in enumerate(ranked_idx):
        c = corners[i]
        phases = (corner_phase_losses(
            median_dist, median_elapsed, best_dist, best_elapsed,
            float(c.enter), float(c.exit),
            corner_dist_total=corner_dist_total, lap_total=median_lap_total,
            best_total=best_lap_total,
            lap_traces=median_traces, best_traces=best_traces, frame=phase_frame,
            lap_align=median_align, best_align=best_align,
        ) if have_phases else _NO_PHASES)
        # L5-04: every ranked row is analysed unless a cap is asked for, so the "How to find it"
        # cell of a row below any cut is a MEASURED "nothing fired" rather than an un-run analysis.
        if top_n is None or rank < top_n:
            reason = _pick_reason(
                time_lost=float(losses[i]),
                apex_speed_delta=(float(median_apex_deltas[i])
                                  if i < len(median_apex_deltas) else 0.0),
                sigma=float(sigmas_by_cid.get(c.cid, 0.0)),
                med_events=median_brake_events, best_events=best_brake_events,
                med_spans=median_coast_spans, best_spans=best_coast_spans,
                med_win=_win(c, median_lap_total), best_win=_win(c, best_lap_total),
                med_trace=med_trace, best_trace=best_trace,
            )
        else:
            reason = Reason(kind=REASON_NONE, contribution=0.0, apex_speed_deficit=0.0,
                            brake_extra_s=0.0, coast_extra_s=0.0,
                            sigma=float(sigmas_by_cid.get(c.cid, 0.0)))
        rows.append(Opportunity(
            cid=c.cid, direction=c.direction, time_lost=float(losses[i]),
            entry_dist=float(c.enter), reason=reason, phases=phases,
            # The per-corner evidence reads the SAME time matrix the loss came from and the SAME
            # baseline (the best lap's own time through the corner), so a row's "you have matched
            # this on 9 of 38 laps" is literally a count of the column the median above summarized.
            evidence=corner_evidence(times[:, i], float(best[i]), float(losses[i])),
        ))
    # Abstained rows sink BELOW the ranked ones (each half keeps the loss ranking, which the
    # stable sort preserves). They are never dropped — the panel shows them with the reason they
    # are not ranked — but a row with no claim must not sit above rows that have one, and
    # everything downstream that reads `rows[0]` or `rows[:3]` (the share card's headline
    # opportunity, the panel's top-N total, the Stats digest) then gets a row that survived the
    # gate instead of the biggest number.
    rows.sort(key=lambda r: not r.evidence.ranked)
    return Opportunities(enough=True, n_laps=n_laps, median_lap_id=med_id, rows=rows,
                         theme=session_theme(rows))


# Names for the dominant phase in the coaching sentence (PHASE_* → human words).
_PHASE_WORD = {PHASE_ENTRY: "entry", PHASE_APEX: "the apex", PHASE_EXIT: "exit"}

# M5: the phase each reason's LEVER naturally lives on. The "most of it on <phase>" clause reads as
# a FIX LOCATION, so it only makes sense appended to a reason whose lever acts on that phase — a
# braking (entry/approach) fix pointed at the EXIT third reads as nonsense. When the dominant third
# is NOT in a reason's compatible set the clause is rephrased as a CONSEQUENCE ("…and it carries to
# exit"), not a fix location. LINE/NONE have no single lever phase → they take the plain clause on
# any dominant third (the σ/"find time" sentence is phase-agnostic, so a location cue is fine).
_REASON_PHASES = {
    REASON_APEX: {PHASE_APEX},              # apex speed is an apex-third lever
    REASON_BRAKING: {PHASE_ENTRY},          # brake later/shorter acts on entry/approach
    REASON_COASTING: {PHASE_APEX, PHASE_EXIT},  # coasting → back-to-throttle is apex/exit
    REASON_LINE: set(PHASES),               # phase-agnostic → any dominant third is fine
    REASON_NONE: set(PHASES),
}


def dominant_phase_clause(opp: Opportunity) -> str:
    """A short clause naming the corner's worst (slowest-vs-best) third, or "" when the
    decomposition is absent/flat or no phase is clearly losing time. Surfaces the D2 attribution in
    the human sentence without overclaiming: only when the dominant third is positive AND holds a
    clear majority (≥ half) of the typical-lap window Δt.

    M5: the clause is REASON-AWARE. When the dominant third matches the reason's natural lever phase
    (_REASON_PHASES) it reads as the fix location ("… — most of it on entry"). When it does NOT
    (e.g. a braking/entry fix but the exit third dominates) it reads as a CONSEQUENCE instead
    ("… and it carries to exit"), so an entry lever is never phrased as an exit fix."""
    ph = opp.phases
    total = ph.total
    if total <= 1e-6:
        return ""
    worst = ph.dominant
    worst_dt = {PHASE_ENTRY: ph.entry, PHASE_APEX: ph.apex, PHASE_EXIT: ph.exit}[worst]
    if worst_dt <= 1e-6 or worst_dt < 0.5 * total:
        return ""
    compatible = worst in _REASON_PHASES.get(opp.reason.kind, set(PHASES))
    if compatible:
        return f" — most of it on {_PHASE_WORD[worst]}"
    # The lever's phase and the dominant third disagree: phrase as where the loss SHOWS, not where
    # to fix it, so a braking (entry) reason no longer reads "brake … — most of it on exit".
    return f", and it carries to {_PHASE_WORD[worst]}"


# ------------------------------------------------------------------ UI sentence helper
def reach_clause(opp: Opportunity) -> str:
    """The second sentence: whether this corner is something the driver has already done, or
    something they have not.

    THE POINT OF THE WHOLE FEATURE. The lever sentence ("brake later / shorter") is identical for
    a driver who has hit this corner's target on 9 of 38 laps and one who has hit it twice in 65 —
    and the two need opposite instructions. This says which one they are, with the count and its
    denominator so the claim can be checked. "" when nothing was measured (REACH_UNKNOWN)."""
    ev = opp.evidence
    # Terse on purpose: the clause rides on the end of an already-long lever sentence in a wrapping
    # table cell, and the count is ALSO in the row's own "Done it?" column. What cannot be dropped
    # is the VOICE (already / rarely / never — that is the instruction changing) and the
    # denominator (the honesty rule: never a count without the sample it came out of).
    if ev.reach == REACH_REPEAT:
        return f" You have already done this — {ev.reach_laps} of {ev.n_laps} laps."
    if ev.reach == REACH_RARE:
        return f" You have rarely done this — {ev.reach_laps} of {ev.n_laps} laps."
    if ev.reach == REACH_NEVER:
        return f" No lap has matched this yet — new ground over {ev.n_laps} laps."
    return ""


def abstain_sentence(opp: Opportunity) -> str:
    """Why this corner is NOT ranked, in the driver's terms — the visible, honest abstain.

    The category's recurring failure is collapsing to a default ("brake 8 m later") when there is
    nothing specific to say. A row that states its own objection is worth more than a row silently
    dropped, and more than a confident sentence with no evidence under it. "" for a ranked row."""
    ev = opp.evidence
    if ev.abstain == ABSTAIN_FEW_LAPS:
        laps = f"{ev.n_laps} clean lap" + ("" if ev.n_laps == 1 else "s")
        return f"Not ranked: only {laps} through this corner — too few to call."
    if ev.abstain == ABSTAIN_ONE_OFF:
        return ("Not ranked: no second lap has matched your best here, so there is no repeatable "
                "target to aim at.")
    if ev.abstain == ABSTAIN_SPREAD:
        return (f"Not ranked: the {opp.time_lost:.2f} s on offer is inside your own lap-to-lap "
                f"spread here — the middle half of your laps span {ev.iqr:.2f} s.")
    return ""


def reason_sentence(opp: Opportunity, unit: str | None = None) -> str:
    """The human, numbers-only coaching sentence for one opportunity's dominant reason. Kept
    here (next to the model) so the panel and any export read ONE phrasing and can't drift. When
    a clear dominant phase exists (D2) a reason-aware clause is appended (a fix-location "… — most
    of it on the apex" when the phase matches the lever, else a consequence "… and it carries to
    exit"), then the `reach_clause` says whether this is a repeat or new ground. Raw driving-channel
    numbers (brake/coast) are phrased as a CAUSE ("~X s longer on the brakes"), never as recoverable
    time. `unit` (km/h default) converts the apex-speed deficit at the DISPLAY boundary — the
    deficit stays km/h.

    An ABSTAINED row returns its `abstain_sentence` instead: the lever is not stated for a corner
    whose claim did not survive the evidence gate, on any surface, so no consumer can accidentally
    print advice this module just declined to give."""
    if not opp.evidence.ranked:
        return abstain_sentence(opp)
    r = opp.reason
    if r.kind == REASON_APEX:
        deficit = units.convert_speed(r.apex_speed_deficit, unit)
        base = f"carry more apex speed (−{deficit:.1f} {units.speed_label(unit)})"
    elif r.kind == REASON_BRAKING:
        # M6: brake_extra_s is a raw driving-channel CAUSE (extra seconds on the brakes vs best) and
        # can exceed the corner's whole time_lost — it is NOT recoverable time. Phrase it explicitly
        # as a cause ("~X s longer on the brakes") so the sentence never advertises a gain larger
        # than the corner's measured loss (the bounded recoverable estimate is reason.contribution).
        base = f"brake later / shorter (~{r.brake_extra_s:.2f} s longer on the brakes)"
    elif r.kind == REASON_COASTING:
        # M6 (same pathology): coast_extra_s is a raw cause, not recoverable time — phrase as cause.
        base = f"back to throttle sooner (~{r.coast_extra_s:.2f} s longer coasting)"
    elif r.kind == REASON_LINE:
        base = f"be consistent here (σ {r.sigma:.2f} s)"
    else:
        base = "find time here"
    lever = base + dominant_phase_clause(opp)
    reach = reach_clause(opp)
    # The lever has never carried a terminator — it is a fragment read under a "How to find it"
    # header. The reach clause is a second SENTENCE, so it needs one in front of it, and only when
    # there is one: an unmeasured row (REACH_UNKNOWN) still prints byte-identically to before.
    return f"{lever}.{reach}" if reach else lever


# ------------------------------------------------------- the session theme, in words
# The cause words, as a THEME headline says them (the per-row sentences keep their own imperative
# phrasing — "brake later / shorter" is an instruction, "Braking" is a category).
_CAUSE_WORD = {
    REASON_APEX: "Apex speed",
    REASON_BRAKING: "Braking",
    REASON_COASTING: "Coasting",
    REASON_LINE: "Consistency",
}


def theme_sentence(theme: Theme) -> str:
    """The session's ONE line — what the twelve findings add up to. "" when nothing is ranked.

    Percentages are shares of the RANKED time on offer, which is the same total the panel's
    headline sums, so the two numbers on the page are two readings of one quantity."""
    if theme.kind == THEME_NONE:
        return ""
    # "the time on offer", not "your time": the shares are over the RANKED rows only, and on a
    # short session most of the measured loss can sit in abstained corners (0062's single chapter:
    # 0.135 s ranked against 0.198 s abstained). The same words the abstain sentence uses for the
    # same quantity, so the page has one name for it.
    if theme.kind == THEME_EXECUTION:
        return (f"Most of the time on offer is execution, not pace — {theme.share:.0%} of it is "
                "in corners you have already driven at this pace.")
    if theme.kind == THEME_PACE:
        return (f"Most of the time on offer is pace, not execution — {theme.share:.0%} of it is "
                "in corners you have rarely been quick through.")
    # SPLIT: state both halves rather than crowning the larger one — this is the honest answer
    # when no side clears THEME_SHARE, and it is a real state (0060 lands here if one corner tips).
    total = theme.execution_s + theme.pace_s
    exec_pct = theme.execution_s / total if total > 0 else 0.0
    return (f"No single theme: {exec_pct:.0%} of the time on offer is in corners you have already "
            f"driven at this pace, {1 - exec_pct:.0%} in corners you have rarely reached.")


def theme_actions(theme: Theme, rows: list[Opportunity]) -> list[str]:
    """AT MOST TWO actions under the theme — the compression that makes a summary coaching.

    One is the common cause across the ranked corners (or an explicit "no single cause", because
    manufacturing one is precisely the failure this feature exists to avoid); the other names the
    single corner to start with and how often the driver has already been there. Never more than
    two, whatever the session looks like."""
    if theme.kind == THEME_NONE:
        return []
    out: list[str] = []
    if theme.cause != REASON_NONE and theme.cause_cids:
        corners_txt = ", ".join(f"C{c}" for c in theme.cause_cids)
        out.append(f"{_CAUSE_WORD[theme.cause]} is the common thread — {theme.cause_share:.0%} "
                   f"of that time is in {corners_txt}.")
    else:
        out.append("No single cause dominates these corners — work them one at a time.")
    lead = next((r for r in rows if r.cid == theme.lead_cid and r.evidence.ranked), None)
    if lead is not None:
        ev = lead.evidence
        had = (f"you have matched it on {ev.reach_laps} of {ev.n_laps} laps"
               if ev.reach == REACH_REPEAT else
               f"only {ev.reach_laps} of {ev.n_laps} laps have matched it"
               if ev.reach == REACH_RARE else
               "no lap has matched it yet" if ev.reach == REACH_NEVER else "")
        out.append(f"Start with C{lead.cid}: +{lead.time_lost:.2f} s"
                   + (f", and {had}." if had else "."))
    return out[:2]
