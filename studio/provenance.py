"""Provenance — what produced a displayed number, as a value rather than as a story.

PACER-FREE AND Qt-FREE BY CONTRACT (pure value objects + numpy helpers), like `data_quality`
and `_signal` next door. `session.py` assembles the arrays; `provenance_panel.py` renders the
result; neither of those two ends is allowed to know the other.

WHY THIS EXISTS. The app's one product rule is that it never shows a number better than the data
supports, and until now that rule was enforced entirely by discipline and by tests — i.e. by
things the person reading the screen cannot see. A statistic that can NAME ITS OWN INPUTS moves
the rule from a claim into something a user can check: the window it was measured over, the raw
GPS fixes inside that window, the quality of those fixes, the arithmetic, and — the part that
only a deterministic core can offer — the value RE-DERIVED from exactly those rows, next to the
value on screen.

THE SHAPE. One `Provenance` per displayed number:

    value / formatted   the number, and the exact string the surface painted
    method_id, method   a stable id + ONE plain sentence (single-sourced in METHODS below)
    window              [t0, t1] media seconds, or [d0, d1] lap odometer metres
    n                   how many raw fixes are inside that window
    quality             the fix-quality distribution over the window
    tables              the raw rows (tables[0]) and, where a number is a min/median over a
                        population, that population as a second table
    steps               the arithmetic, in the order it runs
    reconstructed       the value recomputed FROM tables[0] ALONE by the stated method
    notes               what the panel would otherwise be quietly assuming

`reconstructed` is the load-bearing field and it is deliberately allowed to disagree: see
`residual_note`. A provenance panel that could only ever agree with itself would be decoration.

NOT A QUERY LANGUAGE. Read-only inspection plus CSV. Nothing here evaluates user input, and
nothing here computes a statistic the app does not already compute somewhere else — every builder
takes the number as an argument and explains it. A number the app cannot vouch for is exactly
what this feature exists to make impossible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import data_quality
from ._signal import MAX_DOP, MIN_FIX, lap_label

# The two axes a window can be stated on. A lap time is bounded by two INSTANTS; a sector split
# and a corner are bounded by two ODOMETER POSITIONS and only become times after an interpolation.
# Saying which one a number was measured on is half of saying what it means.
TIME = "time"
DISTANCE = "distance"

# Every method sentence in one place, keyed by the id the Provenance carries. Single-sourced for
# the same reason data_quality.no_laps_body() is: three panels stating one method three ways is
# how a method description drifts from the method. `tests/test_provenance.py` asserts every id a
# builder emits is present here and that every sentence here is emitted by a builder.
METHODS: dict[str, str] = {
    "lap_time.line_crossings": (
        "Lap time is the finish-line crossing instant minus the start-line crossing instant. "
        "Neither instant is a GPS sample: each is interpolated along the one GPS chord that "
        "crosses the timing line, at t = t0 + f × (t1 - t0) where f is the crossing's fraction "
        "along that chord — which is why a lap time is finer than the 10 Hz fix rate."
    ),
    "sector_split.distance_projection": (
        "A sector split is the elapsed time at its closing boundary minus the elapsed time at "
        "its opening boundary, both read off this lap's (odometer, elapsed) curve by linear "
        "interpolation. Each boundary's odometer is the cumulative distance of the lap point "
        "nearest that sector line's midpoint, so a short line that a lap geometrically misses "
        "still produces a split."
    ),
    "corner_best.min_over_laps": (
        "A corner best is the quickest time any clean lap spent inside that corner. One lap's "
        "time in the corner is the elapsed time at the corner's exit boundary minus the elapsed "
        "time at its entry boundary, interpolated on that lap's (odometer, elapsed) curve; the "
        "session best is the minimum of those over the clean laps."
    ),
}

# Column captions the raw-fix table uses everywhere, so the three numbers produce ONE table shape
# and a CSV pasted from any of them lines up with a CSV pasted from another.
#
# `elapsed (s)` is a column rather than a derivation the reader performs, because it is the column
# the arithmetic actually consumes: two of the three numbers are differences of interpolations on
# (d, elapsed), and `elapsed = t - t[lap start]` is computed ONCE over the whole lap. Re-deriving
# it per row from the `t` column would be a different subtraction and could land a bit away.
FIX_COLUMNS = ("#", "t (s)", "elapsed (s)", "d (m)", "lat", "lon", "speed (m/s)", "fix", "DOP",
               "role")
FIX_FORMATS = ("{:.0f}", "{:.4f}", "{:.4f}", "{:.2f}", "{:.7f}", "{:.7f}", "{:.3f}", "{}",
               "{:.2f}", "{}")

# `role` values. A table that mixes measured fixes with derived boundary points has to say which
# is which per row — the two interpolated crossings of a lap ARE the lap time, and they are not
# data.
RAW = "raw fix"
CROSSING = "interpolated crossing"
BRACKET = "brackets the boundary"

# What the load pipeline's quality gate rejected before any of this ran — stated in the panel so
# "every fix here is a 3D lock" reads as the gate's doing rather than as luck.
GATE_NOTE = (f"Fixes with a known 2D-or-worse lock (fix < {MIN_FIX}) or a known DOP above "
             f"{MAX_DOP:g} were rejected at load and are not in this table; a fix that reports "
             f"no quality at all (the GPS5-era stream) is kept and shows as unknown.")


def _absent(v) -> bool:
    """Is this cell's value ABSENT — in either of the two spellings that reach these tables?

    `None` is how the row builders spell "this table has no such column" (`_fix_table` passes
    `None` for `elapsed`/`dists` when a caller has neither). NaN is how NUMPY spells the same
    fact one column at a time, and the fix tables are built straight off numpy arrays that use
    it deliberately: `d (m)` is the lap's own odometer, which does not exist outside the lap, so
    `Session._lap_fixes(bracket=True)` writes NaN into the two rows that BRACKET the start/finish
    crossing and `_lap_time` reads `dists[i] != dists[i]` to mean exactly "outside the lap".

    They are one fact and they get one rendering. Treating only `None` as absent is what printed
    the literal word `nan` in the first visible row of every lap's inspection — measured on
    ~/Desktop/D24 GX020060+GX030060: two cells per lap (the first and last row of the fix table),
    on all 38 valid laps, and in the Copy-as-CSV output beside them."""
    return v is None or (isinstance(v, float) and not math.isfinite(v))


def _fmt(fmt: str, v) -> str:
    """One cell, for DISPLAY. A value the panel does not have renders as an em dash rather than
    as the WORD for it — `None` or `nan`, see `_absent`."""
    if _absent(v):
        return "—"
    try:
        return fmt.format(v)
    except (TypeError, ValueError):
        return str(v)


def _csv_cell(v) -> str:
    """One cell, for COPY. Floats go out at full `repr` precision: the display rounds, and a CSV
    that rounded too would be a picture of the data rather than the data. An ABSENT value (see
    `_absent`) goes out as an EMPTY field — `nan` in a numeric column is a value to a spreadsheet
    and a missing one to a reader, and this panel's whole promise is that the two agree."""
    if _absent(v):
        return ""
    if isinstance(v, float):
        return repr(v)
    s = str(v)
    if any(c in s for c in ',"\n'):
        return '"' + s.replace('"', '""') + '"'
    return s


@dataclass(frozen=True)
class Window:
    """The exact interval a number was measured over, and which axis it is stated on."""

    kind: str          # TIME (media-clock seconds) or DISTANCE (lap odometer metres)
    lo: float
    hi: float

    @property
    def span(self) -> float:
        return self.hi - self.lo

    @property
    def unit(self) -> str:
        return "s" if self.kind == TIME else "m"

    def label(self) -> str:
        """`[1203.1626 s, 1271.3903 s]  (68.2277 s)` — the bracket notation, spelled out."""
        p = 4 if self.kind == TIME else 2
        return (f"[{self.lo:.{p}f} {self.unit}, {self.hi:.{p}f} {self.unit}]  "
                f"({self.span:.{p}f} {self.unit})")


@dataclass(frozen=True)
class Quality:
    """The fix-quality distribution over ONE window — not over the recording.

    A session-wide quality badge answers "is this recording any good"; this answers the question
    a single number raises, which is "were the fixes that produced THIS number any good". They
    can disagree: a clean session still has corners where the trace thinned out.
    """

    n: int
    fix_counts: tuple[tuple[str, int], ...]   # ("3D lock", 612), ("unknown", 0), … in that order
    dop_min: float | None
    dop_median: float | None
    dop_max: float | None
    dt_median: float | None                   # median inter-fix spacing inside the window (s)
    gaps: int                                 # inter-fix steps > 1.5x that median
    clock: str = data_quality.GPS9_TRUECLOCK  # which time axis the load path built

    def lines(self) -> list[str]:
        """The distribution as the panel stacks it — most load-bearing fact first."""
        out = [" · ".join(f"{name}: {k}" for name, k in self.fix_counts if k) or "no fixes"]
        if self.dop_median is not None:
            out.append(f"DOP  min {self.dop_min:.2f} · median {self.dop_median:.2f} · "
                       f"max {self.dop_max:.2f}")
        else:
            out.append("DOP  not reported by this camera (GPS5-era stream)")
        if self.dt_median is not None:
            rate = 1.0 / self.dt_median if self.dt_median > 0 else float("nan")
            gap = f"{self.gaps} gap{'s' if self.gaps != 1 else ''} over 1.5x that"
            out.append(f"Fix spacing  median {self.dt_median * 1000:.0f} ms ({rate:.1f} Hz) · {gap}")
        out.append("Time axis  " + ("GPS9 true clock (per-fix GPS timestamps)"
                                    if self.clock == data_quality.GPS9_TRUECLOCK
                                    else "video clock — no GPS9 stream; times drift"))
        return out


@dataclass(frozen=True)
class Table:
    """One block of rows the panel renders and the CSV writes. `formats` is per column and is
    DISPLAY ONLY (see `_csv_cell`)."""

    caption: str
    columns: tuple[str, ...]
    formats: tuple[str, ...]
    rows: tuple[tuple, ...]
    note: str = ""

    def cell(self, r: int, c: int) -> str:
        return _fmt(self.formats[c], self.rows[r][c])

    def csv_lines(self) -> list[str]:
        out = [_csv_cell(self.caption), ",".join(_csv_cell(c) for c in self.columns)]
        out += [",".join(_csv_cell(v) for v in row) for row in self.rows]
        return out


@dataclass(frozen=True)
class Step:
    """One line of the arithmetic, in the order it runs. `text` is the expression WITH ITS
    NUMBERS SUBSTITUTED — an un-substituted formula is a restatement of the method sentence."""

    label: str
    text: str
    highlight: bool = False    # the step that IS the answer (the winning lap, the subtraction)


@dataclass(frozen=True)
class Provenance:
    """Everything that produced one displayed number. Built by `session.py`, rendered by
    `provenance_panel.py`, and asserted against itself by `tests/test_provenance.py`."""

    title: str                 # "Lap 18 · lap time"
    formatted: str             # the exact string the surface painted ("1:08.228")
    value: float
    unit: str
    method_id: str
    window: Window
    n: int
    quality: Quality
    tables: tuple[Table, ...]
    steps: tuple[Step, ...] = ()
    reconstructed: float | None = None
    # `reconstructed` put through the SAME formatter that produced `formatted`. Both fields are
    # filled by the builder from one `fmt` callable, so "does it match on screen" is a property
    # of the two strings rather than of a rounding this file chose.
    reconstructed_formatted: str = ""
    notes: tuple[str, ...] = ()
    source: str = ""           # where in the code the number comes from, for the curious

    @property
    def method(self) -> str:
        return METHODS[self.method_id]

    @property
    def samples(self) -> Table:
        """tables[0] is always the raw fixes — the rows `reconstructed` was derived from."""
        return self.tables[0]

    @property
    def residual(self) -> float | None:
        if self.reconstructed is None:
            return None
        return self.reconstructed - self.value

    @property
    def exact(self) -> bool:
        """True when re-deriving the number from its own rows lands on the same double."""
        return self.reconstructed is not None and self.reconstructed == self.value

    @property
    def matches_display(self) -> bool:
        """True when the re-derived number PRINTS as the string on screen. This is the claim the
        panel is entitled to make about the displayed value; `exact` is the stronger claim about
        the double behind it, and the two are allowed to differ."""
        return (self.reconstructed is not None
                and self.reconstructed_formatted == self.formatted)

    def residual_note(self) -> str:
        """The sentence under the re-derived value. THIS IS THE HONEST HALF OF THE FEATURE.

        A reconstruction is allowed to miss, and when it does the panel says by how much, in
        units of the last bit, rather than rounding the disagreement away — an inspector that
        could only ever agree with the app would be decoration.

        The one miss that actually occurs is worth stating in full, because it is a property of
        the build rather than of the data: the core evaluates a crossing instant as
        `t0*(1-f) + f*t1`, and a C++ compiler is free to contract that into a single fused
        multiply-add — one rounding where the transcription here does two. Measured over every
        lap of both D24 recordings, the re-derived crossing lands within ONE unit in the last
        place of the core's, so a lap time (a difference of two crossings) lands within two:
        about 5e-13 s on a value displayed to the millisecond, i.e. ten orders of magnitude below
        the last digit anyone reads.
        """
        if self.reconstructed is None:
            return "not re-derivable from these rows alone"
        shown = f"re-derived from the rows above: {self.reconstructed_formatted}"
        if self.exact:
            return f"{shown} — exact, bit for bit ({self.reconstructed!r})"
        r = self.residual
        clock = max(abs(self.window.hi), abs(self.value)) if self.window.kind == TIME \
            else abs(self.value)
        agree = "same to the last digit shown" if self.matches_display else "DISAGREES ON SCREEN"
        return (f"{shown} — {agree}; as a double {r:+.3e} {self.unit}, "
                f"{abs(r) / math.ulp(clock or 1.0):.0f} ulp of the clock it was read off "
                f"(the core fuses the multiply-add; this transcription cannot)")

    def to_csv(self) -> str:
        """The whole inspection as CSV: a metadata block, then every table. Floats at full
        precision, so a spreadsheet round-trips the doubles rather than the pixels."""
        rows = [
            ("pacer provenance", self.title),
            ("displayed", self.formatted),
            ("value", repr(self.value)),
            ("unit", self.unit),
            ("method_id", self.method_id),
            ("method", self.method),
            ("window", f"{self.window.kind}: {self.window.lo!r} .. {self.window.hi!r} "
                       f"{self.window.unit}"),
            ("n (raw fixes in window)", str(self.n)),
            ("fix quality", "; ".join(f"{k}={v}" for k, v in self.quality.fix_counts)),
            ("dop min/median/max", "; ".join(
                "" if v is None else repr(v)
                for v in (self.quality.dop_min, self.quality.dop_median, self.quality.dop_max))),
            ("time axis", self.quality.clock),
            ("re-derived", "" if self.reconstructed is None else repr(self.reconstructed)),
            ("re-derivation", self.residual_note()),
            ("source", self.source),
        ]
        out = [",".join((_csv_cell(a), _csv_cell(b))) for a, b in rows]
        for step in self.steps:
            out.append(",".join((_csv_cell("step: " + step.label), _csv_cell(step.text))))
        for note in self.notes:
            out.append(",".join((_csv_cell("note"), _csv_cell(note))))
        for table in self.tables:
            out.append("")
            out += table.csv_lines()
            if table.note:
                out.append(",".join((_csv_cell("note"), _csv_cell(table.note))))
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------- the raw-fix table


def fix_label(fix) -> str:
    """A GPS9 fix mode as the words the panel prints. Sentinels are "unknown", never 0 — the
    difference between "the receiver had no lock" and "this camera does not report a lock" is
    the whole reason the core keeps a negative sentinel instead of a zero."""
    if fix is None or fix < 0:
        return "unknown"
    return {0: "no lock", 2: "2D", 3: "3D lock"}.get(int(fix), f"fix={int(fix)}")


def quality_over(fix, dop, times, clock: str = data_quality.GPS9_TRUECLOCK) -> Quality:
    """The fix-quality distribution over one window's raw fixes.

    `fix`/`dop` are the per-sample GPS9 quality fields as they arrived from the camera, `times`
    their media-clock seconds. All three are already sliced to the window."""
    fix = np.asarray(fix)
    dop = np.asarray(dop, float)
    times = np.asarray(times, float)
    labels = [fix_label(v) for v in fix]
    # Fixed order, and EVERY category is emitted (a zero count is a fact: "no 2D fixes here").
    order = ("3D lock", "2D", "no lock", "unknown")
    counts = [(name, labels.count(name)) for name in order]
    counts += [(name, labels.count(name)) for name in sorted(set(labels) - set(order))]
    known = dop[np.isfinite(dop) & (dop > 0)]
    dt = np.diff(times)
    dt_med = float(np.median(dt)) if len(dt) else None
    gaps = int(np.count_nonzero(dt > 1.5 * dt_med)) if dt_med else 0
    return Quality(
        n=len(fix),
        fix_counts=tuple(counts),
        dop_min=float(known.min()) if len(known) else None,
        dop_median=float(np.median(known)) if len(known) else None,
        dop_max=float(known.max()) if len(known) else None,
        dt_median=dt_med,
        gaps=gaps,
        clock=clock,
    )


def fix_table(caption: str, idx, times, elapsed, dists, lats, lons, speeds, fix, dop,
              roles=None, note: str = "") -> Table:
    """The raw-fix table, one row per GPS fix, in the shared `FIX_COLUMNS` shape.

    Every array is already sliced to the window and index-aligned. `roles` defaults to RAW for
    every row; a caller that has interpolated boundary points to show passes them in so the table
    can distinguish measurement from derivation ROW BY ROW."""
    n = len(times)
    roles = roles if roles is not None else [RAW] * n
    rows = tuple(
        (int(idx[i]),
         float(times[i]),
         None if elapsed is None else float(elapsed[i]),
         None if dists is None else float(dists[i]),
         float(lats[i]),
         float(lons[i]),
         float(speeds[i]),
         fix_label(fix[i]),
         None if not np.isfinite(dop[i]) or dop[i] <= 0 else round(float(dop[i]), 2),
         roles[i])
        for i in range(n)
    )
    return Table(caption=caption, columns=FIX_COLUMNS, formats=FIX_FORMATS, rows=rows,
                 note=note or GATE_NOTE)


# ------------------------------------------------------- the crossing arithmetic, re-run in Python
#
# The C++ core finds a lap boundary with `Segment::Intersects` + `Split`
# (pacer/geometry/geometry.cpp). `crossing_fraction` below is that arithmetic transcribed
# operation for operation — same perpendicular, same two straddle tests, same ratio of absolute
# perpendicular distances, same lon/lat ordering — so the panel re-derives the crossing from the
# two raw fixes it is showing rather than asking the core what the answer was.
#
# IT IS A TRANSCRIPTION, NOT A SECOND IMPLEMENTATION. If the core's crossing test ever changes,
# this must change with it, and `tests/test_provenance.py` is what notices: it re-derives every
# lap boundary of a real segmentation and asserts the result lands within one ulp of the core's.


def _rot(v: tuple[float, float]) -> tuple[float, float]:
    """pacer::Point::Rot — the 90-degree left turn (x, y) -> (-y, x)."""
    return (-v[1], v[0])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _dot(a, b) -> float:
    """pacer's `Scalar`, which accumulates in ascending index order — so does this."""
    acc = a[0] * b[0]
    acc += a[1] * b[1]
    return acc


def crossing_fraction(line, first, second) -> float | None:
    """The fraction f along the chord `first`->`second` at which it PROPERLY crosses `line`,
    or None when it does not. Every point is (lon, lat) degrees, the space the core's crossing
    test runs in.

    Both straddle tests use strict signs, exactly as the core does: a fix sitting precisely on a
    timing line is not a crossing from either side, which is what stops one pass being counted
    twice."""
    a, b = line
    perp_other = _rot(_sub(second, first))
    if _dot(perp_other, _sub(b, first)) * _dot(perp_other, _sub(a, first)) >= 0:
        return None
    perp_self = _rot(_sub(b, a))
    d_snd = _dot(perp_self, _sub(second, a))
    d_fst = _dot(perp_self, _sub(first, a))
    if d_snd * d_fst >= 0:
        return None
    d_snd, d_fst = abs(d_snd), abs(d_fst)
    return d_fst / (d_snd + d_fst)


def crossing_time(t_first: float, t_second: float, f: float) -> float:
    """pacer::Split's interpolated instant, spelled the way the core spells it."""
    return t_first * (1 - f) + f * t_second


def elapsed_at(edges, dists, elapsed) -> np.ndarray:
    """`np.interp` on a lap's (odometer, elapsed) curve — the one operation both the sector
    split and the corner time are built out of. Named so the two builders demonstrably share it
    rather than each writing their own interp."""
    return np.interp(np.asarray(edges, float), np.asarray(dists, float),
                     np.asarray(elapsed, float))


# ------------------------------------------------------------------- one lap's raw fixes


@dataclass
class LapFixes:
    """One lap's raw GPS fixes as the builders want them — the materialised lap's interior rows
    plus the whole-track index each came from, so a row can be traced back to the recording.

    A plain carrier, mutable and un-frozen on purpose: `session.py` fills it in one pass."""

    index: np.ndarray      # whole-track row number of each fix
    times: np.ndarray      # media-clock seconds
    dists: np.ndarray      # lap odometer metres, aligned to `times`
    lats: np.ndarray
    lons: np.ndarray
    speeds: np.ndarray     # m/s, raw 3D GPS speed
    fix: np.ndarray
    dop: np.ndarray
    elapsed: np.ndarray = field(default_factory=lambda: np.empty(0))

    def __post_init__(self):
        if not len(self.elapsed) and len(self.times):
            self.elapsed = self.times - self.times[0]

    def window(self, lo: float, hi: float, pad: int = 0) -> np.ndarray:
        """Row indices whose odometer lies in [lo, hi], optionally widened by `pad` rows on each
        side. The padding is not decoration: an interpolation AT a boundary needs the fix on the
        far side of it, and a table that omitted it could not re-derive its own number."""
        inside = np.flatnonzero((self.dists >= lo) & (self.dists <= hi))
        if not len(inside):
            return inside
        lo_i = max(int(inside[0]) - pad, 0)
        hi_i = min(int(inside[-1]) + pad, len(self.dists) - 1)
        return np.arange(lo_i, hi_i + 1)

    def slice_table(self, caption: str, rows: np.ndarray, roles=None, note: str = "") -> Table:
        return fix_table(caption,
                         self.index[rows], self.times[rows], self.elapsed[rows], self.dists[rows],
                         self.lats[rows], self.lons[rows], self.speeds[rows], self.fix[rows],
                         self.dop[rows], roles=roles, note=note)

    def quality(self, rows: np.ndarray, clock: str) -> Quality:
        return quality_over(self.fix[rows], self.dop[rows], self.times[rows], clock)


# ----------------------------------------------------------------------------- builders
#
# Each builder takes the number AS AN ARGUMENT and explains it. None of them is allowed to be the
# thing that computes a displayed value: a provenance panel that recomputed the number it
# describes would be describing itself, and would agree with the app for the wrong reason.


def lap_time(*, lap_id: int, value: float, fmt, fixes: LapFixes,
             start_chord: tuple[int, int], finish_chord: tuple[int, int],
             line: tuple[tuple[float, float], tuple[float, float]],
             clock: str) -> Provenance:
    """Provenance for one lap time.

    `fixes` covers the materialised lap PLUS the one raw fix on either side of it, because the two
    chords that produce the lap's boundaries each straddle the boundary — a table that showed only
    the lap's own fixes could not re-derive the lap's own time. `start_chord` / `finish_chord` are
    (row, row) index pairs into `fixes` naming those chords, and `line` is the start/finish line in
    (lon, lat), which is the space the core's crossing test runs in."""
    rows = np.arange(len(fixes.times))
    roles = []
    for i in rows:
        if fixes.dists[i] != fixes.dists[i] or i in (0, len(rows) - 1):   # NaN d = outside the lap
            roles.append(BRACKET)
        else:
            roles.append(RAW)
    # The two interpolated boundaries are rows of the materialised lap, and they are the only rows
    # in the table that were not measured. Saying so per row is the point of the column.
    roles[1] = roles[len(rows) - 2] = CROSSING
    inside = rows[2:len(rows) - 2]

    steps: list[Step] = []
    derived: list[float] = []
    for name, (lo, hi) in (("start line", start_chord), ("finish line", finish_chord)):
        p0 = (fixes.lons[lo], fixes.lats[lo])
        p1 = (fixes.lons[hi], fixes.lats[hi])
        f = crossing_fraction(line, p0, p1)
        if f is None:
            derived.append(float("nan"))
            steps.append(Step(f"{name} crossing",
                              f"rows #{int(fixes.index[lo])}-#{int(fixes.index[hi])} do not cross "
                              f"the line in this transcription"))
            continue
        t = crossing_time(float(fixes.times[lo]), float(fixes.times[hi]), f)
        derived.append(t)
        steps.append(Step(
            f"{name} crossing",
            f"chord #{int(fixes.index[lo])} -> #{int(fixes.index[hi])},  f = {f:.9f},  "
            f"t = {fixes.times[lo]:.6f}×(1-f) + f×{fixes.times[hi]:.6f} = {t:.9f} s"))
    steps.append(Step("lap time",
                      f"{derived[1]:.9f} - {derived[0]:.9f} = {derived[1] - derived[0]:.9f} s",
                      highlight=True))

    rebuilt = derived[1] - derived[0]
    return Provenance(
        title=f"Lap {lap_label(lap_id)} · lap time",
        formatted=fmt(value), value=value, unit="s",
        method_id="lap_time.line_crossings",
        window=Window(TIME, float(fixes.times[1]), float(fixes.times[len(rows) - 2])),
        n=len(inside),
        quality=fixes.quality(inside, clock),
        tables=(fixes.slice_table(
            f"Every GPS fix in lap {lap_label(lap_id)}, plus the one on each side of it "
            f"({len(rows)} rows; {len(inside)} measured)", rows, roles=roles),),
        steps=tuple(steps),
        reconstructed=rebuilt, reconstructed_formatted=fmt(rebuilt),
        notes=(
            "The two interpolated crossings are NOT fixes — the receiver never sampled the line. "
            "They are what makes a lap time finer than the 10 Hz fix rate, and they are only as "
            "good as the line's placement.",
            "The line's own position is the other input, and it is not measured either: it is "
            "auto-fitted or dragged. Two of the rows above bracket it; the line decides where "
            "between them the lap begins.",
        ),
        source="pacer::Laps::Update -> Split (pacer/geometry/geometry.hpp)",
    )


def sector_split(*, lap_id: int, sector: int, of: int, value: float, fmt,
                 fixes: LapFixes, d0: float, d1: float, clock: str) -> Provenance:
    """Provenance for one sub-sector split of one lap.

    `d0`/`d1` are the sub-sector's opening and closing odometer boundaries on THIS lap, as
    `Session.sector_boundary_distances` placed them — the panel takes those as given (it says so)
    and shows what happens after them."""
    rows = fixes.window(d0, d1, pad=1)
    t_at = elapsed_at([d0, d1], fixes.dists, fixes.elapsed)
    sub = elapsed_at([d0, d1], fixes.dists[rows], fixes.elapsed[rows])
    roles = [BRACKET if (fixes.dists[i] < d0 or fixes.dists[i] > d1) else RAW for i in rows]
    inside = fixes.window(d0, d1)
    rebuilt = float(sub[1] - sub[0])
    return Provenance(
        title=f"Lap {lap_label(lap_id)} · sector S{sector + 1} of {of}",
        formatted=fmt(value), value=value, unit="s",
        method_id="sector_split.distance_projection",
        window=Window(DISTANCE, float(d0), float(d1)),
        n=len(inside),
        quality=fixes.quality(inside, clock),
        tables=(fixes.slice_table(
            f"The GPS fixes between {d0:.1f} m and {d1:.1f} m on lap {lap_label(lap_id)}, "
            f"plus the fix bracketing each boundary", rows, roles=roles),),
        steps=(
            Step("opening boundary", f"d = {d0:.4f} m  ->  elapsed {t_at[0]:.9f} s"),
            Step("closing boundary", f"d = {d1:.4f} m  ->  elapsed {t_at[1]:.9f} s"),
            Step("split", f"{t_at[1]:.9f} - {t_at[0]:.9f} = {t_at[1] - t_at[0]:.9f} s",
                 highlight=True),
        ),
        reconstructed=rebuilt, reconstructed_formatted=fmt(rebuilt),
        notes=(
            "A boundary is a DISTANCE, not a crossing. The sector line is projected onto this "
            "lap by its midpoint, because a short line is geometrically missed by some passes — "
            "so every lap gets a split, and the splits always sum to the lap time.",
            "Neither boundary falls on a fix. Both elapsed times are linear interpolations "
            "between the two rows marked as bracketing them.",
        ),
        source="Session.lap_sector_splits (studio/session.py)",
    )


def corner_best(*, cid: int, label: str, value: float, fmt, donor_lap: int,
                fixes: LapFixes, d0: float, d1: float, per_lap: tuple[tuple[int, float], ...],
                clock: str) -> Provenance:
    """Provenance for one corner's session-best time.

    Two tables, because the number is two operations deep: the raw fixes of the lap that WON the
    corner, and the per-lap times the minimum was taken over. A panel that showed only the first
    would hide the comparison; only the second, only the winner's data."""
    rows = fixes.window(d0, d1, pad=1)
    t_at = elapsed_at([d0, d1], fixes.dists, fixes.elapsed)
    sub = elapsed_at([d0, d1], fixes.dists[rows], fixes.elapsed[rows])
    roles = [BRACKET if (fixes.dists[i] < d0 or fixes.dists[i] > d1) else RAW for i in rows]
    inside = fixes.window(d0, d1)
    ranked = sorted(per_lap, key=lambda lt: lt[1])
    population = Table(
        caption=f"Time in {label} on each of the {len(per_lap)} clean laps, quickest first",
        columns=("lap", "time in corner (s)", "vs best (s)"),
        formats=("{}", "{:.4f}", "{:+.4f}"),
        rows=tuple((lap_label(lap), t, t - value) for lap, t in ranked),
        note=("Clean laps only: the valid laps with no GPS dropout. A dropout lap's distance is "
              "reconstructed from its speed trace, so its corner boundaries — and the time "
              "between them — are exactly what must not be allowed to win a corner."),
    )
    rebuilt = float(sub[1] - sub[0])
    return Provenance(
        title=f"{label} · session best",
        formatted=fmt(value), value=value, unit="s",
        method_id="corner_best.min_over_laps",
        window=Window(DISTANCE, float(d0), float(d1)),
        n=len(inside),
        quality=fixes.quality(inside, clock),
        tables=(
            fixes.slice_table(
                f"The GPS fixes inside {label} on lap {lap_label(donor_lap)} — the lap "
                f"that set this best",
                rows, roles=roles),
            population,
        ),
        steps=(
            Step("laps compared", f"{len(per_lap)} clean laps"),
            Step("quickest", f"lap {lap_label(donor_lap)}"),
            Step("corner entry", f"d = {d0:.4f} m  ->  elapsed {t_at[0]:.9f} s"),
            Step("corner exit", f"d = {d1:.4f} m  ->  elapsed {t_at[1]:.9f} s"),
            Step("time in corner",
                 f"{t_at[1]:.9f} - {t_at[0]:.9f} = {t_at[1] - t_at[0]:.9f} s", highlight=True),
        ),
        reconstructed=rebuilt, reconstructed_formatted=fmt(rebuilt),
        notes=(
            "The corner's shape was detected once, on the best lap, and its entry/exit are "
            "reference-lap distances. The window above is that pair PROJECTED onto this lap by "
            "the drift-gated alignment; the panel reads that projection rather than re-deriving "
            "it, and the two metres are where a corner comparison is least certain.",
            "A best over a handful of laps is an ORDER STATISTIC, not a limit: it is the "
            "quickest of what was driven, and it gets quicker as laps are added.",
        ),
        source="stats.corner_report / corners.lap_corner_stats (studio/)",
    )
