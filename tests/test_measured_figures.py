"""THE TABLES MEASURED ON REAL FOOTAGE, AND EVERY SENTENCE THAT QUOTES THEM.

WHY THIS FILE EXISTS. #300 warped every lap. Published figures were measured before it and nobody
re-measured them: `coaching.py`'s evidence and THEME blocks, `theme.py`'s pointwise-Δ floor
table above `format_ideal_run`, and the #272 recombination record in
`studio/docs/refused-2026-09.md` (#321); then `coaching.py`'s brake-habit table and `focus.py`'s
cross-session tables (T14). When they were re-measured, every block had moved, and prose
beside each table still quoted the old cells — in the same file and in five others. The tables
are now written as tables, so the prose around them can be DERIVED from them rather than typed
twice, and the derivation is what this file checks.

WHAT RUNS IN CI, AND WHAT CANNOT:

  1. THE PROSE IS ITS TABLE'S ARITHMETIC. Every count, range, ratio and verdict word quoted beside
     a table is recomputed from the table's own cells and from the constants the code applies
     (`THEME_SHARE`, `REACH_REPEAT_FRAC`, `MIN_REACH_LAPS`, `DELTA_EVEN_EPS_S`, read out of the
     source with `ast`, not typed in here). The coaching THEME table's two full-recording rows are
     recomputed from the evidence table through the code's own reach rule, so the two tables in
     one file cannot disagree either.
  2. EVERY QUOTE ELSEWHERE IS THE TABLE'S. The tracked tree is searched for the phrasings that
     quote these figures (README, the coaching panel, focus.py, the tests' docstrings, the floor
     quoted in session.py and corner_model.py), and each quote must equal what the table says.
     CHANGELOG.md is exempt: a released entry records what was true at release.
  3. THE TABLES ARE STILL TRUE OF THE APP. Opt-in, because it needs the owner's footage, which CI
     does not have: set `PACER_MEASURED_FIGURES_DIR` to the folder holding `D24/`,
     `Sandown_09_05_2026/` and `SD_30_08_26/` (on the dev machine, `~/Desktop`). Each check loads
     its recordings through `Session.load`, with every app-support seam jailed, re-measures every
     cell by the method the table states, and prints the re-measured table in the source's own
     syntax. A size-and-mtime tripwire over every file in those folders must come back unchanged.
     `D24/GX010060.MP4` is 2.4 MB of JSON a tool wrote over the owner's footage; nothing here opens
     it, and the loaders below assert as much. Each of these checks is its own CTest registration,
     `footage.<name>`, and not part of this file's ordinary run: with the variable unset, CTest
     reports it SKIPPED by name (tests/_footage.py) instead of this file counting it as passed.
     Since 2026-09-19 `D24/` and `Sandown_09_05_2026/` are gone from that Desktop, so a run with the
     variable set FAILS naming each missing lap set; which recordings the tables move to is T16.
  4. A TABLE CHECK 3 CANNOT RE-MEASURE SAYS SO WHERE IT IS PUBLISHED (T16). #339 found eight of
     these tables stale after #335 changed corner matching, and then their recordings went. Each
     table whose rows name a recording in `tests/_stale.GONE` must carry a "⚠ STALE" or
     "⚠ UNVERIFIED — NOT RE-MEASURABLE (T16)" mark in its own paragraph, and check 2's quotes, where
     a reader meets them without the table, must date themselves before #335. It has a negative
     control that plants every defect it looks for.
  5. `refused-2026-09.md` NUMBERS ITS SECTIONS ONCE EACH, AND ITS INTRO COUNTS THEM. Two PRs from one
     base each take "the next free section" and collide on merge. That has happened in four waves.
     The sections must run 1..N, the intro's count word is derived from N, and every
     "refused-2026-09.md … §N" in the tree must land on a section that exists. A dev probe's
     citation must also land on the section that names the probe. It has a negative control built
     from the collisions that really happened (#314/#315, #348/#349/#351).

Checks 1 and 2 cannot see whether a table matches the app. Only 3 can, and only where the footage
is. Figures that exist only in prose and need footage to derive (the z-score, the best lap's gap to
each corner's best instance, the end-of-lap range) are checked by 3 alone.

Run:  python tests/test_measured_figures.py
      PACER_MEASURED_FIGURES_DIR=~/Desktop python tests/test_measured_figures.py --footage <check>
"""

from __future__ import annotations

import ast
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _footage  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COACHING = os.path.join(_REPO, "studio", "coaching.py")
_PANEL = os.path.join(_REPO, "studio", "coaching_panel.py")
_THEME = os.path.join(_REPO, "studio", "theme.py")
_REFUSED = os.path.join(_REPO, "studio", "docs", "refused-2026-09.md")
_FOCUS = os.path.join(_REPO, "studio", "focus.py")
_CORNER_MODEL = os.path.join(_REPO, "studio", "corner_model.py")
_SESSION = os.path.join(_REPO, "studio", "session.py")
_STUB = "GX010060.MP4"


# ─── reading the source ──────────────────────────────────────────────────────────────────────────
def _read(path: str) -> str:
    return open(path, encoding="utf-8").read()


def _constant(path: str, name: str):
    """A module-level literal, read out of the file with `ast` — the value the code applies, with
    no import (so no numpy, no Qt) and no copy typed into this test."""
    for node in ast.parse(_read(path)).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{os.path.relpath(path, _REPO)} no longer defines {name}")


def _flatten(text: str) -> str:
    """One file as one line: a newline with its comment marks, indent and any string-literal quotes
    becomes one space, so a figure wrapped across two comment lines is one phrase to a regex."""
    return re.sub(r"""[ \t]*["']?[ \t]*\n[ \t#*]*["']?[ \t]*""", " ", text)


def _need(pattern: str, text: str, what: str) -> re.Match:
    m = re.search(pattern, text)
    assert m, f"{what} — the sentence this check reads is gone or reworded; update the check with it"
    return m


def _pct(x: float) -> int:
    """Percent, rounded half-up the way a reader rounds (Python's round() is half-even)."""
    return int(math.floor(100 * x + 0.5))


# ─── coaching.py: the evidence table ─────────────────────────────────────────────────────────────
class EvRow:
    def __init__(self, rec, cid, rank, lost, sigma, iqr, reached, laps, gate):
        self.rec, self.cid, self.rank, self.lost, self.sigma = rec, cid, rank, lost, sigma
        self.iqr, self.reached, self.laps, self.gate = iqr, reached, laps, gate

    @property
    def frac(self) -> float:
        return self.reached / self.laps

    def __repr__(self):
        return f"<{self.rec} C{self.cid} #{self.rank} {self.lost} σ{self.sigma} {self.reached}/{self.laps} {self.gate}>"


_EV_LINE = re.compile(r"^#\s+(00\d\d)\s+C(\d+)\s+(\d+)\s+(\d\.\d{3})\s+(\d\.\d{3})\s+(\d\.\d{3})\s+"
                      r"(\d+)/(\d+)\s+(ranked|spread|one_off|few_laps)\s*$")


def _evidence_rows() -> list[EvRow]:
    rows = []
    for line in _read(_COACHING).splitlines():
        m = _EV_LINE.match(line)
        if m:
            rows.append(EvRow(m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4)),
                              float(m.group(5)), float(m.group(6)), int(m.group(7)),
                              int(m.group(8)), m.group(9)))
    assert len(rows) >= 10, f"coaching.py's evidence table parsed to {rows!r}"
    return rows


def _reach_side(row: EvRow) -> str:
    """The code's own reach rule (`coaching.corner_evidence`), on the table's cells."""
    min_reach = _constant(_COACHING, "MIN_REACH_LAPS")
    frac = _constant(_COACHING, "REACH_REPEAT_FRAC")
    if row.reached >= min_reach and row.reached >= frac * row.laps:
        return "execution"
    return "pace"


def _kind(execution_share: float, pace_share: float) -> str:
    """`coaching.session_theme`'s verdict rule on two shares."""
    share = _constant(_COACHING, "THEME_SHARE")
    if execution_share >= share:
        return "execution"
    if pace_share >= share:
        return "pace"
    return "split"


def _ratio_bounds(row: EvRow) -> tuple[float, float]:
    """σ / time lost, as far as three-decimal cells pin it down."""
    return (row.sigma - 0.0005) / (row.lost + 0.0005), (row.sigma + 0.0005) / (row.lost - 0.0005)


def test_the_evidence_prose_is_its_table_s_arithmetic():
    """The bullets under coaching.py's evidence table, recomputed from the table."""
    rows = _evidence_rows()
    text = _flatten(_read(_COACHING))
    by_rec = {rec: [r for r in rows if r.rec == rec] for rec in ("0060", "0062")}
    m = _need(r"0060: (\d+) clean laps, 12 corners, (\d+) rows above the panel's display resolution; "
              r"0062: (\d+) clean laps, 12 corners, (\d+) rows\)", text, "the evidence block's header")
    for rec, laps, shown in (("0060", m.group(1), m.group(2)), ("0062", m.group(3), m.group(4))):
        assert int(shown) == len(by_rec[rec]), (rec, shown, len(by_rec[rec]))
        assert {r.laps for r in by_rec[rec]} == {int(laps)}, (rec, laps, by_rec[rec])
    # The display resolution the header names is the panel's own constant: a row the panel drops is
    # not a row of this table.
    floor = _constant(_PANEL, "DISPLAY_MIN_LOST_S")
    assert all(r.lost >= floor for r in rows), [r for r in rows if r.lost < floor]

    m = _need(r"σ ≥ time_lost on (\d+) of those (\d+) rows\. The worst: (00\d\d) C(\d+) lost (\d\.\d{3}) s "
              r"against σ (\d\.\d{3}) s \((\d+\.\d)x\), (00\d\d) C(\d+) lost (\d\.\d{3}) s against σ "
              r"(\d\.\d{3}) s \((\d+\.\d)x\)", text, "the σ-versus-time-lost bullet")
    assert int(m.group(2)) == len(rows), (m.group(2), len(rows))
    assert int(m.group(1)) == sum(r.sigma >= r.lost for r in rows), (
        m.group(1), [r for r in rows if r.sigma >= r.lost])
    worst = sorted(rows, key=lambda r: r.sigma / r.lost, reverse=True)[:2]
    for i, row in enumerate(worst):
        rec, cid, lost, sigma, ratio = m.group(3 + 5 * i, 4 + 5 * i, 5 + 5 * i, 6 + 5 * i, 7 + 5 * i)
        assert (rec, int(cid), float(lost), float(sigma)) == (row.rec, row.cid, row.lost, row.sigma), (
            f"worst #{i + 1} is {row!r}, the bullet names {rec} C{cid}")
        lo, hi = _ratio_bounds(row)
        assert lo - 0.05 <= float(ratio) <= hi + 0.05, (f"{rec} C{cid}: {ratio}x is not "
                                                        f"{row.sigma}/{row.lost} ∈ [{lo:.2f}, {hi:.2f}]")

    m = _need(r"on 0062 C1 it reads (\d\.\d{3}) s while the interquartile range is (\d\.\d{3}) s", text,
              "the σ-is-not-robust bullet")
    c1 = next(r for r in by_rec["0062"] if r.cid == 1)
    assert (float(m.group(1)), float(m.group(2))) == (c1.sigma, c1.iqr), (m.groups(), c1)
    _need(r"twice the width of the whole middle half", text, "the σ-over-IQR ratio in words")
    assert round(c1.sigma / c1.iqr) == 2, f"0062 C1's σ is {c1.sigma / c1.iqr:.2f}x its IQR, not twice"

    m = _need(r"runs (\d+) %\.\.(\d+) % and splits cleanly at 1 lap in 10 — (\d+) of the (\d+) rows are "
              r"corners the driver reaches routinely, (\d+) are", text, "the reach-rate bullet")
    fracs = [r.frac for r in rows]
    assert (int(m.group(1)), int(m.group(2))) == (_pct(min(fracs)), _pct(max(fracs))), (m.groups(), fracs)
    routine = sum(_reach_side(r) == "execution" for r in rows)
    assert (int(m.group(3)), int(m.group(4)), int(m.group(5))) == (routine, len(rows), len(rows) - routine)

    m = _need(r"fires on one of the (\d+) rows, (00\d\d) C(\d+) at z (\d\.\d\d), and every row but that "
              r"one has at least two OTHER laps strictly beating it \((\d+)\.\.(\d+) of them\)", text,
              "the lone-outlier paragraph")
    assert int(m.group(1)) == len(rows)
    # The best lap's own instance is one of `reached`, so the laps strictly beating it are the rest.
    beaten = [r.reached - 1 for r in rows]
    assert (int(m.group(5)), int(m.group(6))) == (min(beaten), max(beaten)), (m.groups(), beaten)
    thin = [r for r in rows if r.reached - 1 < 2]
    assert [(r.rec, r.cid) for r in thin] == [(m.group(2), int(m.group(3)))], (
        f"the paragraph names {m.group(2)} C{m.group(3)} as the one exception; the table's rows "
        f"with fewer than two other laps beating the target are {thin!r}")
    assert "has not fired on either full recording" in text
    assert not [r for r in rows if r.gate == "one_off"], "the table has a ONE_OFF row the prose denies"
    print(f"test_the_evidence_prose_is_its_table_s_arithmetic OK ({len(rows)} rows)")


def test_the_reach_line_and_the_spread_gate_are_the_table_s():
    """REACH_REPEAT_FRAC's and SPREAD_MARGIN's measured notes, from the same table."""
    rows = _evidence_rows()
    text = _flatten(_read(_COACHING))
    frac_line = _constant(_COACHING, "REACH_REPEAT_FRAC")
    # T16: the gap below is what the (stale) table says, and it is no longer the reason. #339 found
    # the line in the 4th-widest gap once #335 changed corner matching, so the note has to say the
    # value is carried forward unverified rather than present the gap as its justification.
    note = _need(r"THE MEASURED REASON FOR (\d+) % NO LONGER HOLDS, AND THE VALUE IS CARRIED FORWARD "
                 r"UNVERIFIED \(T16\)\.(.*?)REACH_REPEAT_FRAC = ", text,
                 "REACH_REPEAT_FRAC's note, which must say its measured reason no longer holds")
    assert int(note.group(1)) == round(100 * frac_line), (note.group(1), frac_line)
    for claim in ("before #335", "#339", "4th-widest gap", "is NOT moved"):
        assert claim in note.group(2), f"REACH_REPEAT_FRAC's note no longer says {claim!r}"
    m = _need(r"the (\d+) real rows' reach rates sort as ([\d. |]+?) % — no row sits between "
              r"(\d+\.\d) and (\d+\.\d) %, and that gap across (\d+) % is the (second-widest|widest) in the "
              r"set \((\d+\.\d\d) points, against (\d+\.\d\d) for (\d+\.\d) → (\d+\.\d) %\)", text,
              "REACH_REPEAT_FRAC's measured note")
    assert int(m.group(1)) == len(rows)
    rates = sorted(100 * r.frac for r in rows)
    below = [x for x in rates if x < 100 * frac_line]
    above = [x for x in rates if x >= 100 * frac_line]
    expect = " ".join(f"{x:.1f}" for x in below) + " | " + " ".join(f"{x:.1f}" for x in above)
    assert " ".join(m.group(2).split()) == expect, f"published {m.group(2)!r}\n  table   {expect!r}"
    assert (float(m.group(3)), float(m.group(4)), int(m.group(5))) == (
        round(below[-1], 1), round(above[0], 1), round(100 * frac_line)), m.groups()
    across = above[0] - below[-1]
    gaps = sorted(((b - a, a, b) for a, b in zip(rates, rates[1:], strict=False)), reverse=True)
    assert abs(float(m.group(7)) - across) < 0.006, (m.group(7), across)
    rank = sum(g[0] > across + 1e-9 for g in gaps)          # 0 = the widest gap in the set
    assert m.group(6) == ("widest" if rank == 0 else "second-widest") and rank <= 1, (m.group(6), gaps[:3])
    if rank == 1:
        top = gaps[0]
        assert (abs(float(m.group(8)) - top[0]) < 0.006
                and (float(m.group(9)), float(m.group(10))) == (round(top[1], 1), round(top[2], 1))), (
            m.groups(), top)

    margin = _constant(_COACHING, "SPREAD_MARGIN")
    spread = [r for r in rows if r.gate == "spread"]
    for r in rows:
        # The gate's own test on the cells — a ranked row clears SPREAD_MARGIN × its IQR, a spread
        # row does not — except within the cells' rounding of the line, where they cannot say.
        if abs(r.lost - margin * r.iqr) > 0.001:
            assert (r.gate == "spread") == (r.lost < margin * r.iqr), f"{r!r} against {margin} × IQR"
    m = _need(r"Measured: (\d+) of the (\d+) real rows abstain here, and the highest-ranked is (\w+) on "
              r"both recordings \((00\d\d) C(\d+), (\d\.\d{3}) s on offer against a (\d\.\d{3}) s "
              r"interquartile band; (00\d\d) C(\d+), (\d\.\d{3}) s against (\d\.\d{3}) s\)", text,
              "SPREAD_MARGIN's measured note")
    assert (int(m.group(1)), int(m.group(2))) == (len(spread), len(rows))
    words = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh"}
    for i, rec in enumerate(("0060", "0062")):
        top = min((r for r in spread if r.rec == rec), key=lambda r: r.rank)
        g = m.groups()[3 + 4 * i:7 + 4 * i]
        assert (g[0], int(g[1]), float(g[2]), float(g[3])) == (top.rec, top.cid, top.lost, top.iqr), (g, top)
        assert words[top.rank] == m.group(3), (m.group(3), top)
    print(f"test_the_reach_line_and_the_spread_gate_are_the_table_s OK ({len(spread)} abstain)")


# ─── coaching.py: the THEME table ────────────────────────────────────────────────────────────────
class ThemeRow:
    def __init__(self, name, laps, ranked, ranked_s, abstained_s, execution, pace, cause, cause_pct):
        self.name, self.laps, self.ranked, self.ranked_s = name, laps, ranked, ranked_s
        self.abstained_s, self.execution, self.pace = abstained_s, execution, pace
        self.cause, self.cause_pct = cause, cause_pct

    @property
    def kind(self) -> str:
        return _kind(self.execution / 100, self.pace / 100)

    @property
    def names_a_cause(self) -> bool:
        return self.cause_pct / 100 >= _constant(_COACHING, "THEME_SHARE")

    def __repr__(self):
        return f"<{self.name}: {self.execution}/{self.pace} {self.cause} {self.cause_pct} ({self.kind})>"


_THEME_LINE = re.compile(r"^#\s+(00\d\d(?: chapter \d)?)\s+(\d+)\s+(\d+)\s+(\d\.\d{3})\s+(\d\.\d{3})\s+"
                         r"(\d+) %\s+(\d+) %\s+(apex|braking|coasting|line) (\d+) %\s*$")


def _theme_rows() -> dict[str, ThemeRow]:
    out = {}
    for line in _read(_COACHING).splitlines():
        m = _THEME_LINE.match(line)
        if m:
            out[m.group(1)] = ThemeRow(m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4)),
                                       float(m.group(5)), int(m.group(6)), int(m.group(7)), m.group(8),
                                       int(m.group(9)))
    assert {"0060", "0062"} <= set(out) and len(out) >= 4, f"the THEME table parsed to {out!r}"
    return out


def test_the_theme_table_is_the_evidence_table_s_arithmetic():
    """The two full-recording THEME rows, recomputed from the evidence table through the code's own
    reach rule and share basis: two tables in one file that must describe one measurement."""
    rows, themes = _evidence_rows(), _theme_rows()
    for t in themes.values():
        assert t.execution + t.pace == 100, t
    for rec in ("0060", "0062"):
        t = themes[rec]
        ranked = [r for r in rows if r.rec == rec and r.gate == "ranked"]
        assert t.laps == ranked[0].laps and t.ranked == len(ranked), (t, ranked)
        total = sum(r.lost for r in ranked)
        assert abs(total - t.ranked_s) <= 0.0005 * (len(ranked) + 1), (rec, total, t.ranked_s)
        execution = sum(r.lost for r in ranked if _reach_side(r) == "execution")
        assert abs(100 * execution / total - t.execution) <= 1, (rec, execution / total, t)
        abstained = sum(r.lost for r in rows if r.rec == rec and r.gate != "ranked")
        # The table's abstained seconds also count abstained rows under the display resolution,
        # which the evidence table (shown rows only) leaves out — so it can only be larger.
        assert t.abstained_s >= abstained - 0.0005 * 12, (rec, abstained, t.abstained_s)
    print(f"test_the_theme_table_is_the_evidence_table_s_arithmetic OK ({len(themes)} lap sets)")


def test_the_theme_prose_follows_the_table_and_THEME_SHARE():
    """Every verdict word in the THEME block — theme, split, "a theme on each", "no single cause" —
    derived from the table's shares and the THEME_SHARE constant the code applies."""
    rows, t = _evidence_rows(), _theme_rows()
    text = _flatten(_read(_COACHING))
    share = _constant(_COACHING, "THEME_SHARE")
    m = _need(r"0060 splits (\d+) % execution / (\d+) % pace and 0062 splits (\d+) % / (\d+) %", text,
              "the THEME block's headline split")
    assert tuple(map(int, m.groups())) == (t["0060"].execution, t["0060"].pace, t["0062"].execution,
                                           t["0062"].pace), m.groups()
    assert "the theme comes out opposite" in text and t["0060"].kind != t["0062"].kind != "split", (
        t["0060"], t["0062"])
    m = _need(r"braking holds (\d+) % of 0060's ranked time and (\d+) % of 0062's, a theme on each", text,
              "the cause-axis sentence")
    for rec, pct in (("0060", m.group(1)), ("0062", m.group(2))):
        assert (t[rec].cause, t[rec].cause_pct) == ("braking", int(pct)) and t[rec].names_a_cause, t[rec]
    chapters = [row for name, row in t.items() if "chapter" in name]
    numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    m = _need(r"of the (\w+) single chapters, (\w+) name no single cause", text, "the cause caveat")
    assert (numbers[m.group(1)], numbers[m.group(2)]) == (
        len(chapters), sum(not c.names_a_cause for c in chapters)), (m.groups(), chapters)
    _need(r"0060 falls to a SPLIT and 0062 flips to execution", text, "the chapter-2 caveat")
    assert (t["0060 chapter 2"].kind, t["0062 chapter 2"].kind) == ("split", "execution"), (
        t["0060 chapter 2"], t["0062 chapter 2"])
    m = _need(r"across the (\w+) single chapters the verdict is execution on (\w+) and a split on (\w+)",
              text, "the single-chapter tally")
    kinds = [c.kind for c in chapters]
    assert (numbers[m.group(1)], numbers[m.group(2)], numbers[m.group(3)]) == (
        len(kinds), kinds.count("execution"), kinds.count("split")), (m.groups(), kinds)

    m = _need(r"0060 lands at (0\.\d\d) and 0062 at (0\.\d\d)", text, "THEME_SHARE's measured note")
    for rec, pub in (("0060", m.group(1)), ("0062", m.group(2))):
        assert round(float(pub) * 100) == max(t[rec].execution, t[rec].pace), (rec, pub, t[rec])
    # "one corner tips either": flip the named corner's reach side in the evidence table and the
    # code's verdict rule must come out SPLIT, and "the line" is the rule's own threshold.
    for rec, cid, reached, laps, line in re.findall(
            r"(00\d\d) would(?: fall to a SPLIT)? if its C(\d+) \(reached on (\d+) of (\d+) laps; the line "
            r"is (\d+)\)", text):
        row = next(r for r in rows if r.rec == rec and r.cid == int(cid))
        assert (row.reached, row.laps) == (int(reached), int(laps)), row
        assert int(line) == max(_constant(_COACHING, "MIN_REACH_LAPS"),
                                math.ceil(_constant(_COACHING, "REACH_REPEAT_FRAC") * row.laps)), (line, row)
        ranked = [r for r in rows if r.rec == rec and r.gate == "ranked"]
        side = {id(r): _reach_side(r) for r in ranked}
        side[id(row)] = "pace" if side[id(row)] == "execution" else "execution"
        total = sum(r.lost for r in ranked)
        e = sum(r.lost for r in ranked if side[id(r)] == "execution") / total
        assert _kind(e, 1 - e) == "split", f"{rec} C{cid} tipped leaves {e:.3f} execution, not a SPLIT"
    assert len(re.findall(r"if its C\d+ \(reached on", text)) == 2, "THEME_SHARE's note names two corners"

    m = _need(r"\(0060's chapter 3 alone: (\d\.\d{3}) s ranked against (\d\.\d{3}) s abstained\)", text,
              "theme_sentence's abstained-majority example")
    c3 = t["0060 chapter 3"]
    assert (float(m.group(1)), float(m.group(2))) == (c3.ranked_s, c3.abstained_s) and c3.abstained_s > c3.ranked_s
    _need(r"\(0060's chapter 2 alone lands here\)", text, "theme_sentence's SPLIT example")
    assert t["0060 chapter 2"].kind == "split"
    print(f"test_the_theme_prose_follows_the_table_and_THEME_SHARE OK (THEME_SHARE {share})")


# ─── quotes of the coaching figures elsewhere in the tree ────────────────────────────────────────
def _tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", _REPO, "ls-files"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    keep = (".py", ".md", ".html", ".txt", ".toml", "CMakeLists.txt")
    return [f for f in out.stdout.split() if f.endswith(keep)]


def _scanned():
    """(path, flattened text) for every tracked text file a quote could live in — minus this file
    (it names the figures it checks) and CHANGELOG.md (a released entry is history)."""
    for rel in _tracked_files():
        if rel.endswith(os.path.basename(__file__)) or rel == "CHANGELOG.md":
            continue
        path = os.path.join(_REPO, rel)
        if os.path.exists(path):
            yield rel, _flatten(open(path, encoding="utf-8", errors="ignore").read())


def _presented(problems: list[str], rel: str, m: re.Match, text: str, what: str,
               sentence: str | None = None) -> None:
    """T16: every table these scans quote is marked stale, so a quote a reader meets WITHOUT the
    table (an in-app string, README.md, docs/, studio/README.md) has to date itself before #335.
    `text` is what `m` matched in; `sentence` is given when the scan already split one out."""
    unit = sentence if sentence is not None else _stale.sentence_at(text, m.start(), m.end())
    p = _stale.unmarked(rel, m.group(0), unit, what)
    if p:
        problems.append(p)


def test_every_quote_of_the_coaching_figures_is_coaching_py_s():
    """README's module map, the coaching panel's docstring, focus.py and the coaching tests all
    quoted the evidence and THEME figures. Each quote, wherever it is, must be the table's."""
    rows, t = _evidence_rows(), _theme_rows()
    n_sigma = sum(r.sigma >= r.lost for r in rows)
    worst = max(rows, key=lambda r: r.sigma / r.lost)
    c1 = next(r for r in rows if r.rec == "0062" and r.cid == 1)
    beaten = [r.reached - 1 for r in rows]
    found: dict[str, list[str]] = {"sigma": [], "iqr": [], "theme": [], "cause": [], "beaten": []}
    problems = []
    for rel, text in _scanned():
        # The tail runs to the end of the clause — through a decimal point ("worst 10.8x"), not a full stop.
        for m in re.finditer(r"(?:σ|sigma) ?(?:≥|>=) ?[^.;]{0,40}?\bon \**(\d+) of (?:the |those )?(\d+)\**"
                             r"((?:[^.;]|\.\d){0,80})", text):
            found["sigma"].append(rel)
            _presented(problems, rel, m, text, "coaching.py's evidence and THEME tables")
            if (int(m.group(1)), int(m.group(2))) != (n_sigma, len(rows)):
                problems.append(f"{rel}: σ ≥ time lost on {m.group(1)} of {m.group(2)}, the table says "
                                f"{n_sigma} of {len(rows)}")
            w = re.search(r"worst (\d+\.\d)x", m.group(3))
            if w:
                lo, hi = _ratio_bounds(worst)
                if not lo - 0.05 <= float(w.group(1)) <= hi + 0.05:
                    problems.append(f"{rel}: worst {w.group(1)}x, the table's worst is {worst!r}")
            a = re.search(r"\**(\d+) abstain", m.group(3))
            if a and int(a.group(1)) != sum(r.gate == "spread" for r in rows):
                problems.append(f"{rel}: {a.group(1)} abstain, the table has "
                                f"{sum(r.gate == 'spread' for r in rows)}")
        for m in re.finditer(r"0062(?:'s)? C1[^.]{0,30}?(?:σ|sigma)?[^.]{0,20}?reads (\d\.\d{3}) s "
                             r"(?:against an? |while the interquartile range is )(\d\.\d{3}) s", text):
            found["iqr"].append(rel)
            _presented(problems, rel, m, text, "coaching.py's evidence and THEME tables")
            if (float(m.group(1)), float(m.group(2))) != (c1.sigma, c1.iqr):
                problems.append(f"{rel}: 0062 C1 σ {m.group(1)} / IQR {m.group(2)}, the table says "
                                f"{c1.sigma} / {c1.iqr}")
        for m in re.finditer(r"0060 (?:is )?(\d+) ?% execution(?:,| and) 0062 (?:is )?(\d+) ?% pace", text):
            found["theme"].append(rel)
            _presented(problems, rel, m, text, "coaching.py's evidence and THEME tables")
            if (int(m.group(1)), int(m.group(2))) != (t["0060"].execution, t["0062"].pace):
                problems.append(f"{rel}: 0060 {m.group(1)} % execution / 0062 {m.group(2)} % pace, the "
                                f"table says {t['0060'].execution} / {t['0062'].pace}")
        for m in re.finditer(r"braking holds (\d+) ?% of 0060's ranked time[^.]{0,20}?(\d+) ?% of 0062's", text):
            found["cause"].append(rel)
            _presented(problems, rel, m, text, "coaching.py's evidence and THEME tables")
            if (int(m.group(1)), int(m.group(2))) != (t["0060"].cause_pct, t["0062"].cause_pct):
                problems.append(f"{rel}: braking {m.group(1)} % / {m.group(2)} %, the table says "
                                f"{t['0060'].cause_pct} / {t['0062'].cause_pct}")
        for m in re.finditer(r"OTHER laps[^.]{0,60}?\((\d+)\.\.(\d+) of them\)", text):
            found["beaten"].append(rel)
            _presented(problems, rel, m, text, "coaching.py's evidence and THEME tables")
            if (int(m.group(1)), int(m.group(2))) != (min(beaten), max(beaten)):
                problems.append(f"{rel}: {m.group(1)}..{m.group(2)} other laps beat the target, the "
                                f"table says {min(beaten)}..{max(beaten)}")
        for m in re.finditer(r"ONE_OFF[^.]{0,120}?fired on (\d+) of (?:the )?(\d+) rows", text):
            found["beaten"].append(rel)
            _presented(problems, rel, m, text, "coaching.py's evidence and THEME tables")
            if (int(m.group(1)), int(m.group(2))) != (sum(r.gate == "one_off" for r in rows), len(rows)):
                problems.append(f"{rel}: ONE_OFF fired on {m.group(1)} of {m.group(2)} rows, the table "
                                f"has {sum(r.gate == 'one_off' for r in rows)} of {len(rows)}")
    # A scan that matches nothing passes exactly like one that matches everything.
    for family, least in (("sigma", 4), ("iqr", 3), ("theme", 2), ("cause", 2), ("beaten", 2)):
        assert len(found[family]) >= least, (f"the {family} scan found {found[family]} — fewer quotes "
                                             f"than the tree is known to carry; a phrasing changed")
    assert not problems, "quotes of coaching.py's measured figures that are not its tables':\n  " + \
        "\n  ".join(problems)
    print(f"test_every_quote_of_the_coaching_figures_is_coaching_py_s OK "
          f"({sum(map(len, found.values()))} quotes: {sorted({f for v in found.values() for f in v})})")


# ─── coaching.py: the brake-habit table ──────────────────────────────────────────────────────────
class BrakeRow:
    def __init__(self, rec, cid, rank, best, habit, laps, clean):
        self.rec, self.cid, self.rank, self.best, self.habit = rec, cid, rank, best, habit
        self.laps, self.clean = laps, clean

    @property
    def gap(self) -> float:
        return abs(self.best - self.habit)

    def __repr__(self):
        return f"<{self.rec} C{self.cid} #{self.rank} best {self.best} habit {self.habit} {self.laps}/{self.clean}>"


_BRAKE_LINE = re.compile(r"^#\s+(00\d\d)\s+C(\d+)\s+(\d+)\s+(-?\d+\.\d)\s+(-?\d+\.\d)\s+(\d+)/(\d+)\s*$")


def _brake_rows() -> list[BrakeRow]:
    rows = []
    for line in _read(_COACHING).splitlines():
        m = _BRAKE_LINE.match(line)
        if m:
            rows.append(BrakeRow(m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4)),
                                 float(m.group(5)), int(m.group(6)), int(m.group(7))))
    assert {r.rec for r in rows} == {"0060", "0062"}, f"coaching.py's brake-habit table parsed to {rows!r}"
    return rows


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def test_the_brake_habit_prose_is_its_table_s_arithmetic():
    """coaching.py's brake-habit table against the evidence table beside it, the panel's hint
    constants, and the prose under it."""
    rows, ev = _brake_rows(), _evidence_rows()
    text = _flatten(_read(_COACHING))
    hint_min = _constant(_PANEL, "BRAKE_HINT_MIN_M")
    for r in rows:
        # A row is a RANKED coaching row, and its rank is the evidence table's for that corner.
        match = [e for e in ev if (e.rec, e.cid) == (r.rec, r.cid)]
        assert match and match[0].gate == "ranked" and match[0].rank == r.rank, (
            f"{r!r} is not a ranked row of the evidence table: {match}")
        assert r.clean == match[0].laps and r.laps <= r.clean, (r, match[0])
        # A best-lap value under the panel's noise floor would not have been a hint at all.
        assert abs(r.best) >= hint_min, f"{r!r}: under BRAKE_HINT_MIN_M ({hint_min} m), never shown"
    for rec in ("0060", "0062"):
        ranks = [r.rank for r in rows if r.rec == rec]
        assert ranks == sorted(ranks), f"{rec}'s rows are not in rank order: {ranks}"

    m = _need(r"sit (\d+\.\d) m apart at the median on 0060 \(worst (\d+\.\d) m, C(\d+)\) and (\d+\.\d) m "
              r"apart on 0062 \(worst (\d+\.\d) m, C(\d+)\)", text, "the gap sentence under the table")
    for i, rec in enumerate(("0060", "0062")):
        mine = [r for r in rows if r.rec == rec]
        med, worst, cid = float(m.group(1 + 3 * i)), float(m.group(2 + 3 * i)), int(m.group(3 + 3 * i))
        top = max(mine, key=lambda r: r.gap)
        # One-decimal cells pin each gap to ±0.1 m; the prose is rounded from the unrounded pair.
        assert abs(med - _median([r.gap for r in mine])) <= 0.1 + 1e-9, (rec, med, [r.gap for r in mine])
        assert abs(worst - top.gap) <= 0.1 + 1e-9 and cid == top.cid, (rec, worst, cid, top)

    c1 = next(r for r in rows if (r.rec, r.cid) == ("0062", 1))
    m = _need(r"brake within (\d) m of its own optimum, so coaching printed \"~(\d) m later\" — barely over the "
              r"BRAKE_HINT_MIN_M noise floor, i\.e\. a shrug — while the driver's HABIT over (\d+) laps was "
              r"(\d+\.\d) m early\. Both say \"later\"", text, "0062 C1's paragraph")
    assert c1.best <= int(m.group(1)) and f"{c1.best:.0f}" == m.group(2), (m.groups(), c1)
    assert hint_min <= c1.best < 2 * hint_min, f"{c1!r} is not 'barely over' {hint_min} m"
    assert (int(m.group(3)), float(m.group(4))) == (c1.laps, c1.habit), (m.groups(), c1)
    assert c1.best > 0 and c1.habit > 0, f"{c1!r}: the two do not both say 'later'"

    m = _need(r"every corner on the two D24 recordings matched on at least (\d+) of its clean laps", text,
              "MIN_BRAKE_LAPS's measured note")
    assert min(r.laps for r in rows) >= int(m.group(1)) > _constant(_COACHING, "MIN_BRAKE_LAPS"), (
        m.group(1), rows)
    print(f"test_the_brake_habit_prose_is_its_table_s_arithmetic OK ({len(rows)} rows)")


def test_every_quote_of_the_brake_habit_figures_is_the_table_s():
    """session.py, test_coaching.py and README's module map quote how far apart the two brake
    answers were and what 0062's C1 said. Each quote must be coaching.py's."""
    rows = _brake_rows()
    text = _flatten(_read(_COACHING))
    worst = max(float(x) for x in _need(r"\(worst (\d+\.\d) m, C\d+\) and \d+\.\d m apart on 0062 \(worst "
                                        r"(\d+\.\d) m", text, "the gap sentence").groups())
    c1 = next(r for r in rows if (r.rec, r.cid) == ("0062", 1))
    found: dict[str, list[str]] = {"upto": [], "habit": [], "within": []}
    problems = []
    for rel, flat in _scanned():
        # These two phrasings are specific enough to read off the whole file: a sentence split would
        # cut coaching.py's own at "i.e.".
        for m in re.finditer(r"(?i:habit) over (\d+) laps was (\d+\.\d) m|(\d+)-lap habit was (\d+\.\d) m", flat):
            found["habit"].append(rel)
            _presented(problems, rel, m, flat, "coaching.py's brake-habit table")
            laps, habit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if (int(laps), float(habit)) != (c1.laps, c1.habit):
                problems.append(f"{rel}: 0062 C1's habit over {laps} laps was {habit} m; the table "
                                f"says {c1.laps} laps, {c1.habit} m")
        for m in re.finditer(r"within (\d) m of its own optimum", flat):
            found["within"].append(rel)
            _presented(problems, rel, m, flat, "coaching.py's brake-habit table")
            if not c1.best <= int(m.group(1)) < c1.best + 1:
                problems.append(f"{rel}: 0062 C1's best lap 'within {m.group(1)} m'; the table "
                                f"says {c1.best} m")
        for sentence in re.split(r"(?<=[.!?])\s+", flat):
            if not re.search(r"best lap", sentence, re.I):
                continue
            if re.search(r"habit|median|table", sentence):
                for m in re.finditer(r"up to (\d+\.\d) m\b", sentence):
                    found["upto"].append(rel)
                    _presented(problems, rel, m, sentence, "coaching.py's brake-habit table", sentence)
                    if float(m.group(1)) != worst:
                        problems.append(f"{rel}: the two brake answers 'up to {m.group(1)} m' apart; "
                                        f"coaching.py's table says {worst}")
            # "Opposite advice" is a claim about SIGNS: the two answers must point different ways.
            if "C1" in sentence and re.search(r"opposite|invert", sentence) and (c1.best > 0) == (c1.habit > 0):
                problems.append(f"{rel}: calls 0062 C1's two answers opposite; the table has "
                                f"{c1.best:+} m and {c1.habit:+} m, both 'later'")
    for family, least in (("upto", 3), ("habit", 3), ("within", 3)):
        assert len(found[family]) >= least, (f"the {family} scan found {found[family]} — fewer quotes "
                                             f"than the tree is known to carry; a phrasing changed")
    assert not problems, "quotes of the brake-habit table that are not coaching.py's:\n  " + \
        "\n  ".join(problems)
    print(f"test_every_quote_of_the_brake_habit_figures_is_the_table_s OK "
          f"({sum(map(len, found.values()))} quotes: {sorted({f for v in found.values() for f in v})})")


# ─── coaching_panel.py: the brake hint's geometry gate ───────────────────────────────────────────
class HintRow:
    def __init__(self, rec, cid, turn_in, apex, optimum, hint):
        self.rec, self.cid, self.turn_in = rec, cid, turn_in
        self.apex, self.optimum, self.hint = apex, optimum, hint

    @property
    def past_turn_in(self) -> float:
        return self.optimum - self.turn_in

    @property
    def past_apex(self) -> float:
        return self.optimum - self.apex

    def __repr__(self):
        return (f"<{self.rec} C{self.cid} turn-in {self.turn_in} apex {self.apex} "
                f"optimum {self.optimum} {self.hint}>")


_HINT_LINE = re.compile(r"^#\s+(00\d\d)\s+C(\d+)\s+(-?\d+\.\d)\s+(-?\d+\.\d)\s+(-?\d+\.\d)\s+"
                        r"(shown|suppressed)\s*$")


def _hint_rows() -> list[HintRow]:
    rows = []
    for line in _read(_PANEL).splitlines():
        m = _HINT_LINE.match(line)
        if m:
            rows.append(HintRow(m.group(1), int(m.group(2)), float(m.group(3)),
                                float(m.group(4)), float(m.group(5)), m.group(6)))
    assert {r.rec for r in rows} == {"0060", "0062"}, (
        f"coaching_panel.py's brake-hint gate table parsed to {rows!r}")
    return rows


def _hint_gate() -> float:
    """BRAKE_HINT_MAX_PAST_TURN_IN_M, which is an ALIAS rather than a literal — read the constant it
    aliases and pin the alias itself, so neither half can move without the other being seen."""
    assert "BRAKE_HINT_MAX_PAST_TURN_IN_M = coaching.BRAKE_APPROACH_M" in _read(_PANEL), (
        "the hint gate is no longer one brake zone; this check reads BRAKE_APPROACH_M for it")
    return float(_constant(_COACHING, "BRAKE_APPROACH_M"))


def test_the_brake_hint_gate_prose_is_its_table_s_arithmetic():
    """T15 — coaching_panel.py's L5-10 note quoted "3 of 11 ranked corners", measured before #300
    removed the drift gate. It is a table now, and every count, name and range in the sentence
    under it is recomputed here from the table's own cells and from the gate the code applies."""
    rows = _hint_rows()
    text = _flatten(_read(_PANEL))
    gate = _hint_gate()

    # 1. Each row's own verdict is the gate applied to its two cells — the recomputable claim the
    #    note makes for the reader ("past turn-in is optimum − turn-in").
    for r in rows:
        assert r.hint == ("suppressed" if r.past_turn_in > gate else "shown"), (r, gate)
    shown = [r for r in rows if r.hint == "shown"]
    dropped = [r for r in rows if r.hint == "suppressed"]

    # 2. The counts and the named corners.
    m = _need(r"The gate is narrow: (\d+) of the (\d+) ranked rows lose their metres — "
              r"([^.]+?)\. The", text, "the hint gate's count sentence")
    assert (int(m.group(1)), int(m.group(2))) == (len(dropped), len(rows)), (m.groups(), rows)
    named = set(re.findall(r"(00\d\d)'s ((?:C\d+(?:,? (?:and )?)?)+)", m.group(3)))
    got = {(rec, tuple(sorted(int(c) for c in re.findall(r"C(\d+)", cs))))
           for rec, cs in named}
    want = {(rec, tuple(sorted(r.cid for r in dropped if r.rec == rec)))
            for rec in {r.rec for r in dropped}}
    assert got == want, (f"the sentence names {sorted(got)}; the table drops {sorted(want)}")

    # 3. The two ranges, each rounded from the table's own cells.
    m = _need(r"The (\d+) it keeps sit (\d+\.\d)\.\.(\d+\.\d) m past turn-in, inside the approach "
              r"the physics assumes; the (\d+) it drops sit (\d+\.\d)\.\.(\d+\.\d) m past it",
              text, "the hint gate's two ranges")
    for n, lo, hi, group in ((m.group(1), m.group(2), m.group(3), shown),
                             (m.group(4), m.group(5), m.group(6), dropped)):
        assert int(n) == len(group), (n, group)
        past = [r.past_turn_in for r in group]
        # One-decimal cells pin each difference to ±0.2 m.
        assert abs(float(lo) - min(past)) <= 0.2 and abs(float(hi) - max(past)) <= 0.2, (
            lo, hi, sorted(round(p, 1) for p in past))
    assert max(r.past_turn_in for r in shown) <= gate < min(r.past_turn_in for r in dropped), (
        "the two ranges overlap the gate")

    # 4. …and the sharpest form of the objection: a suppressed optimum past the corner's own apex.
    m = _need(r"Two of those three are past the APEX as well \((00\d\d) C(\d+) by (\d+\.\d) m, "
              r"(00\d\d) C(\d+) by (\d+\.\d) m\)", text, "the past-the-apex clause")
    beyond = sorted((r for r in dropped if r.past_apex > 0), key=lambda r: (r.rec, r.cid))
    assert len(beyond) == 2, f"{len(beyond)} suppressed rows sit past their apex, not two: {dropped}"
    for i, r in enumerate(beyond):
        rec, cid, by = m.group(1 + 3 * i), int(m.group(2 + 3 * i)), float(m.group(3 + 3 * i))
        assert (rec, cid) == (r.rec, r.cid), (m.groups(), beyond)
        assert abs(by - r.past_apex) <= 0.2, (by, r.past_apex)

    # 5. A row in this table is a row the hint would otherwise print: its habit clears the noise
    #    floor. The metres themselves are coaching.py's brake-habit table where the two overlap.
    floor = _constant(_PANEL, "BRAKE_HINT_MIN_M")
    assert floor > 0
    _need(r"on both recordings that is every ranked row", text, "the every-ranked-row claim")
    print(f"test_the_brake_hint_gate_prose_is_its_table_s_arithmetic OK "
          f"({len(rows)} rows, {len(dropped)} suppressed)")


# ─── corner_model.py: the beat-rate correlation table ────────────────────────────────────────────
class BeatRow:
    def __init__(self, name, saved, n, r, rho, p):
        self.name, self.saved, self.n, self.r, self.rho, self.p = name, saved, n, r, rho, p

    def __repr__(self):
        return f"<{self.name}{' †' if self.saved else ''} n {self.n} r {self.r:+} ρ {self.rho:+} p {self.p}>"


_BEAT_LINE = re.compile(r"^\s+\| (D24 1 ch|D24 3 ch|Sandown ch 1|Sandown 3 ch|SD_30_08)( †)?\s*\|\s+(\d+)\s*\| "
                        r"([−+-]\d\.\d{3}) \| ([−+-]\d\.\d{3})\s*\| (\d\.\d{3}) \|\s*$")
# The beat-rate table's names for the lap sets the floor table names in full.
_BEAT_SETS = {"D24 1 ch": "D24 1 chapter", "D24 3 ch": "D24 3 chapters", "Sandown ch 1": "Sandown chapter 1",
              "Sandown 3 ch": "Sandown 3 chapters", "SD_30_08": "SD_30_08"}


def _beat_rows() -> list[BeatRow]:
    rows = []
    for line in _read(_CORNER_MODEL).splitlines():
        m = _BEAT_LINE.match(line)
        if m:
            rows.append(BeatRow(m.group(1), bool(m.group(2)), int(m.group(3)), _signed(m.group(4)),
                                _signed(m.group(5)), float(m.group(6))))
    assert sorted(r.name for r in rows) == sorted(_BEAT_SETS), f"the beat-rate table parsed to {rows!r}"
    return rows


def test_the_beat_rate_verdict_is_derived_from_its_table():
    """`SegmentBests.beat_counts`' correlation table and the sentence under it: how many rows are
    distinguishable from chance, which r is strongest, what share of variance it explains and how
    many r are negative — all recomputed from the cells."""
    rows = _beat_rows()
    text = _flatten(_read(_CORNER_MODEL))
    words = {0: "None", 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    _constant(_CORNER_MODEL, "POINT_SPAN_M")   # the method names it; it must still exist
    m = _need(r"Re-measured on the owner's (\w+) recordings as the app opens them", text, "the method sentence")
    assert m.group(1).lower() == words[len(rows)].lower(), (m.group(1), len(rows))
    m = _need(r"(\w+) of the (\w+) is distinguishable from chance at p < (0\.\d+), and the strongest r "
              r"\(([^)]+)\) explains (\d+) % of the variance in beat rate", text, "the verdict sentence")
    alpha = float(m.group(3))
    assert m.group(1) == words[sum(r.p < alpha for r in rows)] and m.group(2) == words[len(rows)].lower(), (
        m.groups(), rows)
    strongest = max(rows, key=lambda r: abs(r.r))
    assert m.group(4) == strongest.name, (m.group(4), strongest)
    # A 3-decimal r pins r² to about ±0.1 point; the prose rounds it to a whole percent.
    lo, hi = (abs(strongest.r) - 0.0005) ** 2 * 100, (abs(strongest.r) + 0.0005) ** 2 * 100
    assert math.floor(lo + 0.5) <= int(m.group(5)) <= math.floor(hi + 0.5), (m.group(5), strongest)
    m = _need(r"(\w+) of the (\w+) r are negative", text, "the sign sentence")
    assert (m.group(1), m.group(2)) == (words[sum(r.r < 0 for r in rows)], words[len(rows)].lower()), (m.groups(), rows)
    # D24 carries no saved line (floor table's rule): a † D24 row would claim a restore that cannot happen.
    assert not [r for r in rows if r.saved and r.name.startswith("D24")], rows
    print(f"test_the_beat_rate_verdict_is_derived_from_its_table OK ({len(rows)} rows, "
          f"strongest r {strongest.r:+.3f} on {strongest.name})")


# ─── focus.py: the cross-session tables ──────────────────────────────────────────────────────────
class FocusRow:
    def __init__(self, cid, promoted, then_med, then_iqr, now_med, now_iqr, change, bar, verdict):
        self.cid, self.promoted, self.then_med, self.then_iqr = cid, promoted, then_med, then_iqr
        self.now_med, self.now_iqr, self.change, self.bar, self.verdict = now_med, now_iqr, change, bar, verdict

    def __repr__(self):
        return (f"<C{self.cid} +{self.promoted} {self.then_med}/{self.then_iqr} → {self.now_med}/"
                f"{self.now_iqr} Δ{self.change:+} bar {self.bar} {self.verdict}>")


def _signed(s: str) -> float:
    return float(s.replace("−", "-"))


_FOCUS_LINE = re.compile(r"^\s+C(\d+)\s+\+(\d\.\d{3}) s\s+(\d+\.\d{3}) s\s+(\d\.\d{3})\s+(\d+\.\d{3}) s\s+"
                         r"(\d\.\d{3})\s+([−+-]\d\.\d{3})\s+(\d\.\d{3})\s+(\w+)\s*$")
_WINDOW_LINE = re.compile(r"^\s+C(\d+)\s+(\d+\.\d) m\s+(\d+\.\d) m\s+(\d+\.\d{3}) s\s+(\d+\.\d{3}) s\s+"
                          r"([−+-]\d\.\d{3}) s\s+(\d+\.\d{3}) s\s+([−+-]\d\.\d{3}) s\s*$")


def _focus_tables() -> tuple[list[FocusRow], dict[int, tuple]]:
    """focus.py's two docstring tables: the promoted three, and {cid: (window 0060 m, window 0062 m,
    own 0060 s, own 0062 s, own change, 0062 over 0060's window s, stored change)}."""
    rows, windows = [], {}
    for line in _read(_FOCUS).splitlines():
        m = _FOCUS_LINE.match(line)
        if m:
            g = m.groups()
            rows.append(FocusRow(int(g[0]), float(g[1]), float(g[2]), float(g[3]), float(g[4]),
                                 float(g[5]), _signed(g[6]), float(g[7]), g[8]))
        m = _WINDOW_LINE.match(line)
        if m:
            g = m.groups()
            windows[int(g[0])] = (float(g[1]), float(g[2]), float(g[3]), float(g[4]), _signed(g[5]),
                                  float(g[6]), _signed(g[7]))
    assert len(rows) == 3 and 8 in windows, f"focus.py's tables parsed to {rows!r} / {windows!r}"
    return rows, windows


def _se_median(iqr: float, n: int) -> float:
    """The normal-approximation standard error of a median focus.py states: 1.2533 σ / √n with
    σ = IQR / 1.349."""
    return 1.2533 * iqr / 1.349 / math.sqrt(n)


def test_the_focus_prose_is_its_tables_arithmetic():
    """focus.py's promoted-three table and window table: every change, bar and verdict recomputed
    from the cells and the code's own rule, the promoted three recomputed from coaching.py's
    evidence table, and every figure in the prose around them."""
    rows, windows = _focus_tables()
    ev = _evidence_rows()
    text = _flatten(_read(_FOCUS))
    margin = _constant(_COACHING, "SPREAD_MARGIN")
    # The bar is the WIDER of the two spreads — read that off the code rather than assume it.
    assert re.search(r"spread = max\(item\.iqr_s, now\.iqr\)\s+if abs\(delta\) < SPREAD_MARGIN \* spread:",
                     _read(_FOCUS)), "focus.verdict's spread test changed; update this check and the table"
    words = {k: _constant(_FOCUS, k) for k in ("OUTCOME_UNCHANGED", "OUTCOME_IMPROVED", "OUTCOME_SLOWER")}
    for r in rows:
        assert abs(r.change - (r.now_med - r.then_med)) <= 0.001 + 1e-9, f"{r!r}: change is not now − then"
        assert abs(r.bar - margin * max(r.then_iqr, r.now_iqr)) <= 0.001 + 1e-9, (
            f"{r!r}: bar is not SPREAD_MARGIN ({margin}) × the wider IQR")
        if abs(abs(r.change) - r.bar) > 0.002:
            want = (words["OUTCOME_UNCHANGED"] if abs(r.change) < r.bar else
                    words["OUTCOME_IMPROVED"] if r.change < 0 else words["OUTCOME_SLOWER"])
            assert r.verdict == want, f"{r!r}: verdict.py's rule gives {want}"
    # "Promote 0060's top three ranked corners": the evidence table's first three ranked rows.
    ranked = sorted((e for e in ev if e.rec == "0060" and e.gate == "ranked"), key=lambda e: e.rank)[:3]
    assert [(r.cid, r.promoted) for r in rows] == [(e.cid, e.lost) for e in ranked], (
        f"focus.py promotes {[(r.cid, r.promoted) for r in rows]}; coaching.py's evidence table ranks "
        f"{[(e.cid, e.lost) for e in ranked]}")
    m = _need(r"\(0060: \d{4}-\d\d-\d\d, (\d+) laps; 0062: \d{4}-\d\d-\d\d, (\d+) laps\)", text,
              "the recordings' dates and lap counts")
    laps = {e.rec: e.laps for e in ev}
    assert (int(m.group(1)), int(m.group(2))) == (laps["0060"], laps["0062"]), (m.groups(), laps)
    numbers = {1: "one", 2: "two", 3: "three"}
    unchanged = sum(r.verdict == words["OUTCOME_UNCHANGED"] for r in rows)
    m = _need(r"every one of the (\w+) changes is inside its bar\. The honest verdict on the only real "
              r"cross-session pair this repo has is \"no change you can act on\", (\w+) times out of (\w+)",
              text, "the verdict sentence")
    assert unchanged == len(rows) and m.groups() == (numbers[len(rows)], numbers[unchanged],
                                                     numbers[len(rows)]), (m.groups(), rows)

    c8 = windows[8]
    m = _need(r"C8's own-window \+(\d\.\d{3}) s is \"you got slower\" and every millisecond of it is the "
              r"detector drawing a longer window; over the stored window it is \+(\d\.\d{3}) s", text,
              "the C8 sentence under the window table")
    assert (float(m.group(1)), float(m.group(2))) == (c8[4], c8[6]) and c8[1] > c8[0], (m.groups(), c8)
    for cid, (_w60, _w62, own60, own62, own_d, at60, stored_d) in windows.items():
        assert abs(own_d - (own62 - own60)) <= 0.001 + 1e-9, f"C{cid}: own change is not {own62} − {own60}"
        assert abs(stored_d - (at60 - own60)) <= 0.001 + 1e-9, f"C{cid}: stored change is not {at60} − {own60}"
        assert abs(stored_d) < abs(own_d), f"C{cid}: the stored window does not remove the window's growth"

    m = _need(r"the lap totals to (\d\.\d\d) % \((\d+\.\d) vs (\d+\.\d) m\)", text, "the lap-total sentence")
    pct, t60, t62 = float(m.group(1)), float(m.group(2)), float(m.group(3))
    lo, hi = 100 * ((t62 - 0.1) / (t60 + 0.1) - 1), 100 * ((t62 + 0.1) / (t60 - 0.1) - 1)
    assert lo - 0.005 <= pct <= hi + 0.005, (pct, lo, hi)
    m = _need(r"lap totals differ by (\d+\.\d\d) m on (\d+) m — (\d\.\d\d) % — which displaces a corner boundary "
              r"by at most ~(\d\.\d) m inside a (\d+) m window\. (\d+) % is (\w+) times that", text,
              "MAX_LAP_TOTAL_DRIFT's measured note")
    diff, on, pct2, shift, window, drift, times = m.groups()
    drift_const = _constant(_FOCUS, "MAX_LAP_TOTAL_DRIFT")
    assert abs(float(diff) - (t62 - t60)) <= 0.1 + 1e-9 and int(on) == int(t60) and float(pct2) == pct, m.groups()
    assert float(shift) == round(int(window) * pct / 100, 1), (shift, window, pct)
    assert int(drift) == round(100 * drift_const) and times == {3: "three", 2: "twice", 4: "four"}[
        round(drift_const / (pct / 100))], (drift, times, drift_const, pct)
    m = _need(r"the standard error of either median is ~(\d\.\d\d)-(\d\.\d\d) s", text, "the standard-error note")
    ses = [_se_median(r.then_iqr, laps["0060"]) for r in rows] + [_se_median(r.now_iqr, laps["0062"]) for r in rows]
    assert (float(m.group(1)), float(m.group(2))) == (_pct(min(ses)) / 100, _pct(max(ses)) / 100), (m.groups(), ses)
    print(f"test_the_focus_prose_is_its_tables_arithmetic OK ({len(rows)} promoted, {len(windows)} windows)")


def test_every_quote_of_the_focus_figures_is_focus_py_s():
    """README's module map, session.py, app.py, tests/CMakeLists.txt and test_focus_list.py quote
    the focus figures. Each quote must be focus.py's, and none may call the spread test the
    corner's OWN spread when `verdict` takes the wider of two."""
    rows, windows = _focus_tables()
    w60, w62, _own60, _own62, own_d, _at60, stored_d = windows[8]
    pct = _need(r"the lap totals to (\d\.\d\d) %", _flatten(_read(_FOCUS)), "the lap-total sentence").group(1)
    drift = round(100 * _constant(_FOCUS, "MAX_LAP_TOTAL_DRIFT"))
    found: dict[str, list[str]] = {k: [] for k in ("promoted", "moves", "grew", "grew_by", "own", "stored", "pct")}
    problems = []
    for rel, flat in _scanned():
        for sentence in re.split(r"(?<=[.!?])\s+", flat):
            for m in re.finditer(r"top three \(C(\d+) \+(\d\.\d{3}) s, C(\d+) \+(\d\.\d{3}) s, C(\d+) \+(\d\.\d{3}) s\)",
                                 sentence):
                found["promoted"].append(rel)
                _presented(problems, rel, m, sentence, "focus.py's tables", sentence)
                got = [(int(m.group(1 + 2 * i)), float(m.group(2 + 2 * i))) for i in range(3)]
                if got != [(r.cid, r.promoted) for r in rows]:
                    problems.append(f"{rel}: promotes {got}; focus.py's table says "
                                    f"{[(r.cid, r.promoted) for r in rows]}")
            for m in re.finditer(r"moves them ([−+-]\d\.\d{3}) / ([−+-]\d\.\d{3}) / ([−+-]\d\.\d{3}) s(.{0,60})",
                                 sentence):
                found["moves"].append(rel)
                _presented(problems, rel, m, sentence, "focus.py's tables", sentence)
                if [_signed(x) for x in m.groups()[:3]] != [r.change for r in rows]:
                    problems.append(f"{rel}: moves them {m.groups()[:3]}; the table's changes are "
                                    f"{[r.change for r in rows]}")
                if "own spread" in m.group(4):
                    problems.append(f"{rel}: 'inside half its own spread' — the bar is the wider of the "
                                    f"two sessions' spreads")
            for m in re.finditer(r"C8's (?:own )?window grew (\d+\.\d) m → (\d+\.\d) m", sentence):
                found["grew"].append(rel)
                _presented(problems, rel, m, sentence, "focus.py's tables", sentence)
                if (float(m.group(1)), float(m.group(2))) != (w60, w62):
                    problems.append(f"{rel}: C8 grew {m.group(1)} → {m.group(2)} m; focus.py says {w60} → {w62}")
            for m in re.finditer(r"C8(?:'s)? window grew (\d+\.\d) m (?:between|and)", sentence):
                found["grew_by"].append(rel)
                _presented(problems, rel, m, sentence, "focus.py's tables", sentence)
                if abs(float(m.group(1)) - (w62 - w60)) > 0.1 + 1e-9:
                    problems.append(f"{rel}: C8 grew {m.group(1)} m; focus.py says {w60} → {w62}")
            if "C8" in sentence:
                for m in re.finditer(r"(?:median time (?:by )?|worth |with it )\+?(\d\.\d{3}) s", sentence):
                    found["own"].append(rel)
                    _presented(problems, rel, m, sentence, "focus.py's tables", sentence)
                    if float(m.group(1)) != own_d:
                        problems.append(f"{rel}: C8's own-window change {m.group(1)} s; focus.py says {own_d}")
                    if "whole second" in sentence and own_d < 1.0:
                        problems.append(f"{rel}: calls C8's {own_d} s 'a whole second'")
            if "stored window" in sentence:
                for m in re.finditer(r"(?:the same corner is|C8 is[^.]{0,12}?) \+(\d\.\d{3}) s", sentence):
                    found["stored"].append(rel)
                    _presented(problems, rel, m, sentence, "focus.py's tables", sentence)
                    if float(m.group(1)) != stored_d:
                        problems.append(f"{rel}: C8 over the stored window {m.group(1)} s; focus.py says {stored_d}")
            for m in re.finditer(r"(\d\.\d\d) ?% (?:apart|of real lap-total drift)", sentence):
                found["pct"].append(rel)
                _presented(problems, rel, m, sentence, "focus.py's tables", sentence)
                if m.group(1) != pct:
                    problems.append(f"{rel}: the D24 lap totals {m.group(1)} % apart; focus.py says {pct}")
            for m in re.finditer(r"MAX_LAP_TOTAL_DRIFT` \((\d+) %", sentence):
                if int(m.group(1)) != drift:
                    problems.append(f"{rel}: MAX_LAP_TOTAL_DRIFT quoted as {m.group(1)} %, it is {drift} %")
            if re.search(r"change smaller than", sentence) and re.search(r"(?:the corner's|its) own (?:IQR|interquartile|spread)",
                                                                         sentence):
                problems.append(f"{rel}: the spread test described with the corner's OWN spread — "
                                f"`verdict` takes the wider of the two sessions'")
    for family, least in (("promoted", 1), ("moves", 1), ("grew", 3), ("grew_by", 2), ("own", 5),
                          ("stored", 2), ("pct", 3)):
        assert len(found[family]) >= least, (f"the {family} scan found {found[family]} — fewer quotes "
                                             f"than the tree is known to carry; a phrasing changed")
    assert not problems, "quotes of focus.py's measured figures that are not its tables':\n  " + \
        "\n  ".join(problems)
    print(f"test_every_quote_of_the_focus_figures_is_focus_py_s OK "
          f"({sum(map(len, found.values()))} quotes: {sorted({f for v in found.values() for f in v})})")


# ─── theme.py: the pointwise-Δ floor table ───────────────────────────────────────────────────────
class FloorRow:
    def __init__(self, name, saved, laps, samples, floor, neg, minus):
        self.name, self.saved, self.laps, self.samples = name, saved, laps, samples
        self.floor, self.neg, self.minus = floor, neg, minus

    def __repr__(self):
        return f"<{self.name}{' †' if self.saved else ''} {self.floor} {self.neg}% {self.minus}%>"


_FLOOR_LINE = re.compile(r"^#\s+(D24 1 chapter|D24 3 chapters|Sandown chapter 1|Sandown 3 chapters|SD_30_08)"
                         r"( †)?\s+(\d+)\s+(\d+)\s+(-\d\.\d{3}) s\s+(\d+\.\d\d) %\s+(\d+\.\d\d) %\s*$")


def _floor_rows() -> list[FloorRow]:
    rows = []
    for line in _read(_THEME).splitlines():
        m = _FLOOR_LINE.match(line)
        if m:
            rows.append(FloorRow(m.group(1), bool(m.group(2)), int(m.group(3)), int(m.group(4)),
                                 float(m.group(5)), float(m.group(6)), float(m.group(7))))
    names = [r.name for r in rows if not r.saved]
    assert sorted(names) == sorted({"D24 1 chapter", "D24 3 chapters", "Sandown chapter 1",
                                    "Sandown 3 chapters", "SD_30_08"}), f"floor table parsed to {rows!r}"
    return rows


def test_the_floor_table_is_consistent_with_its_own_definitions():
    """theme.py's floor table and the sentence under it. A printed minus sign needs a raw Δ below
    -DELTA_EVEN_EPS_S, so it can never be commoner than a negative Δ, and a row whose floor is above
    that line cannot print one at all. The wobble the prose quotes is the table's deepest floor."""
    rows = _floor_rows()
    eps = _constant(_THEME, "DELTA_EVEN_EPS_S")
    for r in rows:
        assert r.minus <= r.neg, f"{r!r}: more minus signs than negative samples"
        assert (r.floor > -eps) <= (r.minus == 0.0), f"{r!r}: floor above -{eps} yet a minus sign printed"
        assert r.minus == 0.0 or r.floor < -eps, r
    text = _flatten(_read(_THEME))
    m = _need(r"a wobble of at most (\d\.\d\d) s \((\d\.\d\d) s on the owner's saved lines\)", text,
              "the wobble sentence under the floor table")
    assert float(m.group(1)) == round(max(-r.floor for r in rows), 2), (m.group(1), rows)
    assert float(m.group(2)) == round(max(-r.floor for r in rows if r.saved), 2), (m.group(2), rows)
    # T13: "the two lines now count the same N laps" on SD_30_08 is a claim about two rows' cells.
    # When it was false, the loader's row counted 25 pieces of a lap against the saved line's 23.
    m = _need(r"On SD_30_08 the two lines now count the same (\d+) laps", text, "the SD_30_08 line sentence")
    sd = {r.saved: r.laps for r in rows if r.name == "SD_30_08"}
    assert sd == {False: int(m.group(1)), True: int(m.group(1))}, (
        f"theme.py says both SD_30_08 lines count {m.group(1)} laps; its rows count {sd}")
    # D24 carries no saved line: a † D24 row would claim a restore that cannot happen.
    assert not [r for r in rows if r.saved and r.name.startswith("D24")], rows
    print(f"test_the_floor_table_is_consistent_with_its_own_definitions OK ({len(rows)} rows)")


def test_every_quote_of_the_floor_is_a_row_of_the_table():
    """session.py, corner_model.py and three tests quote the floor. A quoted floor must be the floor
    of a recording the sentence names, or — when it names none, and so claims the floor over all of
    them — the table's deepest. Each "% of samples" beside it must be a cell of the table, and every
    "end-of-lap values of +a … +b s" the range theme.py states. A sentence that dates itself (#211,
    "before #300") is history and exempt: the −0.052 s a test docstring kept quoting was retired by
    #211, and the −0.159 s five sites quoted was measured before #300."""
    rows = _floor_rows()
    deepest = f"{min(r.floor for r in rows):.3f}"
    cells = {c for r in rows for c in (r.neg, r.minus)}
    ends = _need(r"against end-of-lap values of \+(\d+\.\d\d) … \+(\d+\.\d\d) s", _flatten(_read(_THEME)),
                 "theme.py's end-of-lap range").groups()
    found, problems = [], []
    for rel, text in _scanned():
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if "#211" in sentence or "before #300" in sentence:
                continue
            for m in re.finditer(r"end-of-lap values of \+(\d+\.\d\d) … \+(\d+\.\d\d) s", sentence):
                found.append(rel)
                _presented(problems, rel, m, sentence, "theme.py's floor table", sentence)
                if m.groups() != ends:
                    problems.append(f"{rel}: end-of-lap values +{m.group(1)} … +{m.group(2)} s, theme.py "
                                    f"says +{ends[0]} … +{ends[1]}")
            hits = [*re.finditer(r"(?:floor(?: is)?|worst excursion (?:to|is)|most negative value is) \**"
                                 r"([−-]0\.\d{3}) s", sentence), *re.finditer(r"([−-]0\.\d{3}) s floor", sentence)]
            quotes = [m.group(1) for m in hits]
            if not quotes:
                continue
            found.append(rel)
            for m in hits:
                _presented(problems, rel, m, sentence, "theme.py's floor table", sentence)
            named = [r for r in rows if r.name in sentence]
            allowed = {f"{r.floor:.3f}" for r in named} if named else {deepest}
            for q in quotes:
                if q.replace("−", "-") not in allowed:
                    problems.append(f"{rel}: quotes a floor of {q} s for "
                                    f"{sorted({r.name for r in named}) or 'all five recordings'} — "
                                    f"theme.py's table gives {sorted(allowed)}")
            for p in re.findall(r"(\d+(?:\.\d+)?) ?% of (?:samples|frames)", sentence):
                digits = len(p.split(".")[1]) if "." in p else 0
                if not any(abs(round(c, digits) - float(p)) < 1e-9 for c in cells):
                    problems.append(f"{rel}: '{p} % of samples' beside the floor is not a cell of the table")
    assert len(found) >= 6, f"the floor scan found only {found} — a phrasing changed"
    assert not problems, "\n  ".join(["floor quotes that are not theme.py's table:"] + problems)
    print(f"test_every_quote_of_the_floor_is_a_row_of_the_table OK ({len(found)} sentences in "
          f"{sorted(set(found))})")


# ─── T16: a table no footage here can re-measure says so where it is published ───────────────────
# #339 ran this file's footage half after #335 changed corner matching. Eight of the nine tables
# below no longer matched the app. The ninth, the brake-hint gate, had just been re-measured and
# did. Then D24 and Sandown_09_05_2026 left the owner's machine (tests/_stale.py), so none of the
# nine can be re-measured where it stands.
#
# WHICH tables need a mark is DERIVED, not listed: a table needs one when one of its own rows names
# a recording in `_stale.GONE`, read off the rows through `_LAP_SETS`. WHAT the mark says is
# checked against the status below. That status is #339's record, a fact about a measurement, and
# no text check could derive it. Every footage check must re-measure a table in this registry, so a
# new footage check cannot publish a table the guard has never heard of.
import _stale
import test_ideal_sample_table as _ideal

_STATS = os.path.join(_REPO, "studio", "stats.py")


def _published() -> list[tuple]:
    """(name, file, first-row pattern, the `_LAP_SETS` keys its rows name, the footage check that
    re-measures it or None, the recorded status) for every table a footage check re-measures, and
    for the D24 tables of the same kind that no check does."""
    stale, unverified = _stale.STALE, _stale.UNVERIFIED
    return [
        # #339: the check failed after #335, on 0062's rows too, and 0062 has no interpolated cell.
        ("coaching.py's evidence table", _COACHING, _EV_LINE, lambda: [r.rec for r in _evidence_rows()],
         "test_the_coaching_tables_match_the_footage", stale),
        ("coaching.py's THEME table", _COACHING, _THEME_LINE, lambda: list(_theme_rows()),
         "test_the_coaching_tables_match_the_footage", stale),
        ("coaching.py's brake-habit table", _COACHING, _BRAKE_LINE, lambda: [r.rec for r in _brake_rows()],
         "test_the_brake_habit_table_matches_the_footage", stale),
        # #339 (T15) re-measured this one after #335 and C5, and it came back byte-identical.
        ("coaching_panel.py's brake-hint gate table", _PANEL, _HINT_LINE, lambda: [r.rec for r in _hint_rows()],
         "test_the_brake_hint_gate_table_matches_the_footage", unverified),
        # #339: the beat-rate, focus and floor checks failed after #335; the floor re-measure was
        # byte-identical before and after #339's own change, so the move is #335's.
        ("corner_model.py's beat-rate table", _CORNER_MODEL, _BEAT_LINE,
         lambda: [_BEAT_SETS[r.name] for r in _beat_rows()], "test_the_beat_rate_table_matches_the_footage", stale),
        # focus.py's two tables compare 0060 with 0062, which its prose names; the rows are corners.
        ("focus.py's cross-session tables", _FOCUS, _FOCUS_LINE, lambda: ["0060", "0062"],
         "test_the_focus_tables_match_the_footage", stale),
        ("theme.py's floor table", _THEME, _FLOOR_LINE, lambda: [r.name for r in _floor_rows()],
         "test_the_floor_table_matches_the_footage", stale),
        # #339: 0060's ideal is 65.864 s after it (65.637 after #335); the record publishes 65.464.
        ("the #272 recombination record", _REFUSED, re.compile(r"^\| \| 0060 \(38 laps\)"), lambda: list(_record_tables()[0]),
         "test_the_refusal_record_matches_the_footage", stale),
        # T16b re-based it on the working set and its footage check re-measured every row, so it is
        # CURRENT: it must carry no mark. What it replaced is kept below it as the record.
        ("corner_model.IdealSample's table", _CORNER_MODEL, re.compile(r"^\s+\| Sandown 3h 1 chapter"),
         lambda: list(_ideal.ROW_RECORDINGS), "test_the_table_still_matches_the_app", _stale.CURRENT),
        # #339: the `all` cells were stale by 0.05–0.43 s on all five rows. No check re-measures the
        # record: two of its three recordings are gone, and it is history, not a claim about the app.
        ("corner_model.IdealSample's record", _CORNER_MODEL, re.compile(r"^\s+\| D24 1 chapter"),
         lambda: [_BEAT_SETS.get(r.name, r.name) for r in _ideal._record_rows()], None, stale),
        # No footage check re-measures these two, but they are D24 tables of the same kind: how far
        # an interpolated corner cell is off. #335 moved which cells are interpolated (0060: 236 →
        # 34 of 456), so both describe cells the app no longer has.
        ("stats.CornerMatrix's gate-crossing table", _STATS, re.compile(r"^\s+cells with both edges matched\s+0060:"),
         lambda: ["0060", "0062"], None, stale),
        ("stats.corner_report's line-crossing table", _STATS, re.compile(r"^\s+0060 pair \(38 laps\)\s+matched"),
         lambda: ["0060", "0062"], None, stale),
    ]


def _footage_check_names() -> set[str]:
    """Every check that re-measures a published table against footage: `FOOTAGE_CHECKS` in this file
    and in test_ideal_sample_table.py, each its own `footage.<name>` CTest registration (#341)."""
    return {fn.__name__ for fn in (*FOOTAGE_CHECKS, *_ideal.FOOTAGE_CHECKS)}


def _block_above(lines: list[str], row: int, path: str) -> list[int]:
    """The line indices above `row`, nearest first, that belong to the same comment block, the
    same docstring or the same markdown section — the paragraph a table's mark has to sit in."""
    out = []
    for j in range(row - 1, -1, -1):
        line = lines[j]
        if path.endswith(".md"):
            if line.startswith("## "):
                break
        elif lines[row].lstrip().startswith("#"):
            if not line.lstrip().startswith("#"):
                break
        elif '"""' in line:
            out.append(j)                      # the docstring's opening line is part of it
            break
        out.append(j)
    return out


def _find_mark(lines: list[str], row: int, path: str) -> tuple[int, str] | None:
    for j in _block_above(lines, row, path):
        m = _stale.TABLE_MARK.search(lines[j])
        if m:
            return j, m.group(1)
    return None


# IdealSample marks each row it could not re-measure with ‡, and its own checks read that mark
# (a quote of a ‡ row must not read as current), so a gone recording's row has to carry it too.
_IDEAL_ROW = re.compile(r"^\s+\| ((?:D24|Sandown|SD_30_08)[^|‡]*?)\s*(‡)?\s*\|")


def _mark_problems(texts: dict[str, str]) -> list[str]:
    """What is wrong with the marks, given each publishing file's text — a function of the text so
    the negative control can hand it a planted copy."""
    problems = []
    for name, path, first_row, lap_sets, _check, status in _published():
        rel = os.path.relpath(path, _REPO)
        lines = texts[path].splitlines()
        row = next((i for i, line in enumerate(lines) if first_row.match(line)), None)
        if row is None:
            problems.append(f"{name}: no row of it in {rel} — update this registry with the table")
            continue
        gone = sorted({_LAP_SETS[s][0] for s in lap_sets()} & set(_stale.GONE))
        if status == _stale.CURRENT:
            # Re-measured on footage that is here. A stale mark left on it would be the opposite lie:
            # a current table presented as unverifiable.
            if gone:
                problems.append(f"{name} is registered {status}, but its rows need {' and '.join(gone)}, "
                                f"which are no longer available")
            found = _find_mark(lines, row, path)
            if found:
                problems.append(f"{name} ({rel}:{found[0] + 1}) was re-measured and is {status}, but "
                                f"still carries a {found[1]} mark")
            continue
        if not gone:
            continue        # every row's recording is here, so its footage check can answer
        found = _find_mark(lines, row, path)
        if found is None:
            problems.append(f"{name} ({rel}:{row + 1}) is presented without its mark. Its rows need "
                            f"{' and '.join(gone)}, no longer available, so no footage check can say "
                            f"whether it is current: head the paragraph above it with "
                            f"'⚠ {status} — NOT RE-MEASURABLE (T16).'")
            continue
        j, said = found
        if said != status:
            problems.append(f"{name}: marked {said} at {rel}:{j + 1}, but #339's record makes it {status}")
        para = _flatten("\n".join(lines[j:row]))
        missing = [w for w in ("#335", "corner matching", "no longer available", *gone) if w not in para]
        if missing:
            problems.append(f"{name}: its mark at {rel}:{j + 1} does not say {missing}")
        if path == _CORNER_MODEL and "IdealSample" in name:
            for line in lines[row:row + 8]:
                m = _IDEAL_ROW.match(line)
                if m and _LAP_SETS[_BEAT_SETS.get(m.group(1), m.group(1))][0] in _stale.GONE and not m.group(2):
                    problems.append(f"{name}: the {m.group(1)} row needs a recording that is no longer "
                                    f"available and does not carry ‡")
    return problems


def test_every_table_no_footage_can_re_measure_carries_its_mark():
    """T16. Each table below is re-measured by a footage check that cannot run: its recordings are
    gone. So the place that publishes it must say so — measured on the named recordings, before or
    after #335 changed corner matching, stale or unverified per #339, and not re-measurable."""
    tables = _published()
    registered, checks = {t[4] for t in tables if t[4]}, _footage_check_names()
    assert registered == checks, (
        f"footage checks with no registered table: {sorted(checks - registered)}; registered checks "
        f"that do not exist: {sorted(registered - checks)} — each footage check's table belongs in "
        f"_published()")
    problems = _mark_problems({t[1]: _read(t[1]) for t in tables})
    assert not problems, "tables presented as current that no footage can re-measure:\n  " + \
        "\n  ".join(problems)
    print(f"test_every_table_no_footage_can_re_measure_carries_its_mark OK ({len(tables)} tables, "
          f"{sum(t[5] == _stale.STALE for t in tables)} stale, "
          f"{sum(t[5] == _stale.UNVERIFIED for t in tables)} unverified, "
          f"{sum(t[5] == _stale.CURRENT for t in tables)} re-measured and unmarked)")


def test_the_mark_guard_fails_on_each_planted_defect():
    """The guard's negative control. Each defect is planted in a COPY of the published text and must
    be named: every table's mark stripped in turn, a status flipped, a gone recording dropped from a
    mark, a ‡ dropped from an IdealSample row. The presented-quote rule gets the same treatment on a
    doc sentence and on an in-app string."""
    tables = _published()
    clean = {t[1]: _read(t[1]) for t in tables}
    assert not _mark_problems(clean), "the control needs a clean tree to plant into"

    def planted(path: str, span: range, old: str, new: str) -> dict[str, str]:
        lines = clean[path].splitlines()
        assert any(old in lines[i] for i in span), (old, [lines[i] for i in span])
        for i in span:
            lines[i] = lines[i].replace(old, new)
        return {**clean, path: "\n".join(lines)}

    def mark_of(name: str) -> tuple[str, int, int]:
        """(file, the mark's line, the table's first row) for one registered table."""
        t = next(t for t in tables if t[0] == name)
        lines = clean[t[1]].splitlines()
        row = next(i for i, line in enumerate(lines) if t[2].match(line))
        j, _status = _find_mark(lines, row, t[1])
        return t[1], j, row

    for name, *_, status in tables:
        if status == _stale.CURRENT:
            continue        # nothing to strip: planted the other way round below
        path, j, _row = mark_of(name)
        tag = _stale.TABLE_MARK.search(clean[path].splitlines()[j]).group(0)
        got = _mark_problems(planted(path, range(j, j + 1), tag, "a note"))
        assert any(p.startswith(name) and "without its mark" in p for p in got), (name, got)
    # T16b: a table re-measured on present footage must NOT carry a stale mark.
    current = [t for t in tables if t[5] == _stale.CURRENT]
    assert current, "no table is registered CURRENT, so the half of the guard below is untested"
    for name, path, first_row, *_ in current:
        lines = clean[path].splitlines()
        row = next(i for i, line in enumerate(lines) if first_row.match(line))
        got = _mark_problems(planted(path, range(row - 2, row - 1), lines[row - 2],
                                     lines[row - 2] + " ⚠ STALE — NOT RE-MEASURABLE (T16)."))
        assert any(p.startswith(name) and "still carries a STALE mark" in p for p in got), (name, got)
    path, j, _row = mark_of("theme.py's floor table")
    got = _mark_problems(planted(path, range(j, j + 1), "⚠ STALE", "⚠ UNVERIFIED"))
    assert any("marked UNVERIFIED" in p for p in got), got
    path, j, row = mark_of("coaching.py's evidence table")
    got = _mark_problems(planted(path, range(j, row), "D24", "the recording"))
    assert any(p.startswith("coaching.py's evidence table") and "does not say ['D24']" in p for p in got), got
    row = next(i for i, line in enumerate(clean[_CORNER_MODEL].splitlines()) if line.lstrip().startswith("| D24 3"))
    got = _mark_problems(planted(_CORNER_MODEL, range(row, row + 1), "‡", ""))
    assert any("D24 3 chapters row" in p and "does not carry ‡" in p for p in got), got

    # The presented-quote rule: a doc sentence and an in-app string, each with its mark taken out.
    # (Planted figures are made up, so no scan of the real tree mistakes this file for a quote.)
    doc = "Same driving, same recording: 12.345 s here and 11.111 s there"
    assert _stale.unmarked("README.md", "11.111 s there", doc, "a table") is not None
    assert _stale.unmarked("README.md", "11.111 s there", doc + ", measured before #335.", "a table") is None
    assert _stale.unmarked("studio/coaching.py", "11.111 s there", doc, "a table") is None, (
        "a code comment or docstring is a note to a developer, and the table carries the mark")
    tip = "Two rows are 9.99 s apart on the same driving."
    real = _stale.in_app_strings
    try:
        _stale.in_app_strings = lambda rel: (tip,)
        assert _stale.unmarked("studio/library_dialog.py", "9.99 s apart", "…", "a table") is not None
        _stale.in_app_strings = lambda rel: (tip + " Measured before a September 2026 change to corner matching.",)
        assert _stale.unmarked("studio/library_dialog.py", "9.99 s apart", "…", "a table") is None
    finally:
        _stale.in_app_strings = real

    # The interpolated-cell error: undated, a maximum without its measurement, a maximum swapped.
    crossing = _crossing_tables()
    bare = "Timed at each crossing, such cells were a median 0.22 s off (up to 0.89 s) on the recording."
    got = _crossing_quote_problems("x", bare, *crossing)[1]
    assert any("before #335" in p for p in got) and any("gate measurement" in p for p in got), got
    fine = "Timed at a gate before #335, such cells were a median 0.22 s off (up to 0.89 s) on the recording."
    assert _crossing_quote_problems("x", fine, *crossing) == (1, []), _crossing_quote_problems("x", fine, *crossing)
    swapped = fine.replace("0.89 s", "0.96 s")
    assert any("line measurement" in p for p in _crossing_quote_problems("x", swapped, *crossing)[1])
    print(f"test_the_mark_guard_fails_on_each_planted_defect OK ({len(tables)} marks stripped, status, "
          f"recording and ‡ planted; a doc sentence and an in-app string without the quote mark; an "
          f"undated, an unattributed and a swapped interpolated-cell maximum)")


def _crossing_tables() -> tuple[dict[str, float], dict[str, float]]:
    """stats.py's two measurements of how far a corner cell is off an independent crossing time:
    CornerMatrix's (a gate drawn across the track) and corner_report's (a line), each as
    {"median": interpolated median, "max": interpolated max, "matched_max": matched max}."""
    text = _read(_STATS)
    g = _need(r"cells with both edges matched\s+0060: \d+ of \d+\s+\|Δ\| median \d\.\d{3} s, p90 \d\.\d{3}, "
              r"max (\d\.\d{3})\s+.*?cells with an interpolated edge\s+0060: (\d+) of \d+\s+\|Δ\| median "
              r"(\d\.\d{3}) s, p90 \d\.\d{3}, max (\d\.\d{3})", text.replace("\n", " "), "CornerMatrix's gate table")
    c = _need(r"0060 pair \(38 laps\)\s+matched \d+ of \d+ cells\s+\|Δt\| median \d\.\d{3} s, max (\d\.\d{3}) s\s+"
              r"interpolated (\d+)\s+\|Δt\| median (\d\.\d{3}) s, max (\d\.\d{3}) s", text.replace("\n", " "),
              "corner_report's line table")
    assert g.group(2) == c.group(2), "the two tables no longer measure the same interpolated cells"
    return ({"median": float(g.group(3)), "max": float(g.group(4)), "matched_max": float(g.group(1))},
            {"median": float(c.group(3)), "max": float(c.group(4)), "matched_max": float(c.group(1))})


def _crossing_quote_problems(rel: str, sentence: str, gate: dict, line: dict) -> tuple[int, list[str]]:
    """(quotes of the interpolated-cell error in `sentence`, what is wrong with them)."""
    if not re.search(r"cross(?:ing|es the track)|gate", sentence):
        return 0, []
    medians = {gate["median"], line["median"]}
    quoted, problems = 0, []
    # A table row's own cells ("|Δ| median 0.219 s") are the table, not a quote of it.
    for m in re.finditer(r"(?<!\| )median (0\.2\d{1,2}) s(?![\w.])", sentence):
        quoted += 1
        q = m.group(1)
        if not any(f"{v:.{len(q) - 2}f}" == q for v in medians):
            problems.append(f"{rel}: an interpolated cell a median {q} s off; stats.py measured {sorted(medians)}")
        if not _stale.QUOTE_MARK.search(sentence):
            problems.append(f"{rel}: quotes the interpolated-cell error ({q} s) without saying it was "
                            f"measured before #335 — the cells it describes are no longer interpolated")
    if not quoted:
        return 0, []
    for mx in re.finditer(r"(?<!\| )(?:up to|max) (\d\.\d{2,3}) s", sentence):
        v = mx.group(1)
        which = [name for name, t in (("gate", gate), ("line", line))
                 for key in ("max", "matched_max") if f"{t[key]:.{len(v) - 2}f}" == v]
        if not which:
            problems.append(f"{rel}: a maximum of {v} s, which neither stats.py table measured")
        elif not any(re.search({"gate": r"gate", "line": r"line[- ]cross"}[w], sentence) for w in which):
            other = line["max"] if which[0] == "gate" else gate["max"]
            problems.append(f"{rel}: a maximum of {v} s without saying it is the {which[0]} "
                            f"measurement (the {'line' if which[0] == 'gate' else 'gate'} read {other} s)")
    return quoted, problems


def test_every_quote_of_the_interpolated_cell_error_is_dated_and_names_its_measurement():
    """How far an interpolated corner cell is off was measured twice on D24's 0060 pair, over the
    same 236 cells, before #335: CornerMatrix against a gate (median 0.219 s, max 0.886 s) and C4's
    corner_report against a line (0.221 s, 0.960 s). The tree quoted both maxima, 0.89 s in the
    CORNERS BY LAP tooltip and 0.96 s in lap_table.py, as if they were one figure. #335 then left 34
    of the 456 cells interpolated, so every quote is of a population the app no longer has.

    So every sentence quoting the median must be one of the two tables' medians and date itself
    before #335, wherever it is. A quoted maximum must be one of the tables' maxima, and the
    sentence must say which measurement it is (gate or line). CHANGELOG.md records what a release
    said, and studio/dev/ and studio/docs/ are dated records, so none of them is read here."""
    tables = _crossing_tables()
    found, problems = [], []
    for rel, flat in _scanned():
        if rel.startswith(("studio/dev/", "studio/docs/")):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", flat):
            quoted, wrong = _crossing_quote_problems(rel, sentence, *tables)
            found += [rel] * quoted
            problems += wrong
    assert len(found) >= 10, f"the scan found only {found} — a phrasing changed"
    assert not problems, "quotes of the interpolated-cell error:\n  " + "\n  ".join(problems)
    print(f"test_every_quote_of_the_interpolated_cell_error_is_dated_and_names_its_measurement OK "
          f"({len(found)} quotes in {sorted(set(found))})")


# ─── refused-2026-09.md: the #272 recombination record ───────────────────────────────────────────
def _record_tables() -> list[dict[str, dict[str, float]]]:
    """The #272 section's two tables — the current one first, then #272's own — as
    {rec: {cell: value}}."""
    text = _read(_REFUSED)
    section = text[text.index("## 2. The ideal-lap recombination dotplot"):text.index("## 3.")]
    tables, cur = [], None
    for line in section.splitlines():
        if line.startswith("| | 0060"):
            laps = [int(x) for x in re.findall(r"\((\d+) laps\)", line)]
            cur = {"0060": {"laps": laps[0]}, "0062": {"laps": laps[1]}}
            tables.append(cur)
            continue
        if cur is None or not line.startswith("| ") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        label, values = cells[0].strip("* "), cells[1:3]
        for rec, v in zip(("0060", "0062"), values, strict=True):
            nums = [float(x) for x in re.findall(r"\d+\.\d+|\d+", v.replace("×", ""))]
            d = cur[rec]
            if label.startswith("ideal / best"):
                d["ideal"], d["best"] = nums
            elif label.startswith("recombination support"):
                d["lo"], d["hi"], d["width"] = nums
            elif label.startswith("recombination sd"):
                d["sd"], d["ratio"] = nums
            elif label.startswith("best lap's percentile"):
                d["pct"] = nums[0]
            elif label.startswith("the 20 dots"):
                d["dot_lo"], d["dot_hi"] = nums
            elif label.startswith("dots at or left"):
                d["left"], d["of"] = nums
    assert len(tables) == 2 and all(len(t["0060"]) == 13 for t in tables), f"parsed {tables!r}"
    return tables


def test_the_refusal_record_s_verdict_is_derived_from_its_table():
    """The #272 record: its verdict row and both reasons, recomputed from the current table, and
    every comparison with #272's own table recomputed from the two."""
    now, then = _record_tables()
    text = _flatten(_read(_REFUSED))
    for rec, d in now.items():
        assert d["lo"] == d["ideal"], f"{rec}: the support's left end is the ideal by construction, {d}"
        assert abs(d["width"] - round(d["hi"] - d["lo"], 1)) < 1e-9, (rec, d)
        # 20 dots at the (i + ½)/20 quantiles: none is at or left of the best lap exactly when the
        # first is right of it, and then the best lap sits below the 2.5th percentile.
        assert d["of"] == 20
        if d["dot_lo"] > d["best"]:
            assert d["left"] == 0 and d["pct"] < 2.5, (rec, d)
        else:
            assert d["left"] >= 1, (rec, d)
        # Reason 1: the ideal AND the best lap are off the left edge of the plotted body.
        assert d["ideal"] < d["dot_lo"] and d["best"] < d["dot_lo"], (rec, d)
        # Reason 2: narrower than the laps driven.
        assert d["ratio"] < 1, (rec, d)
    ratios = sorted({f"{d['ratio']:.2f}" for d in now.values()})
    # (no leading `*`: _flatten eats a comment/list mark at the start of a wrapped line, and this
    # phrase starts one)
    m = _need(r"narrower than the laps actually driven\* \(([\d.–]+)× the real sd(?: on both recordings)?\)",
              text, "reason 2's ratio")
    assert m.group(1) == "–".join(ratios), (m.group(1), ratios)
    m = _need(r"with only \*\*(\d+)/(\d+)\*\* and \*\*(\d+)/(\d+)\*\* laps moving it", text, "the jackknife line")
    assert (int(m.group(2)), int(m.group(4))) == (now["0060"]["laps"], now["0062"]["laps"]), m.groups()
    m = _need(r"The support shrank \((\d+\.\d) → (\d+\.\d) s, (\d+\.\d) → (\d+\.\d) s\)", text,
              "the support comparison")
    assert tuple(map(float, m.groups())) == (then["0060"]["width"], now["0060"]["width"],
                                             then["0062"]["width"], now["0062"]["width"]), m.groups()
    m = _need(r"The 20 dots, which are what a plot would draw, moved by at most (\d\.\d\d) s", text,
              "the dots comparison")
    moved = max(abs(now[r][k] - then[r][k]) for r in now for k in ("dot_lo", "dot_hi"))
    assert float(m.group(1)) == round(moved, 2), (m.group(1), moved)
    assert now["0060"]["best"] == then["0060"]["best"] and now["0062"]["best"] == then["0062"]["best"], (
        "#300 cannot move a lap time; a best lap that differs between the tables is a typo")
    print(f"test_the_refusal_record_s_verdict_is_derived_from_its_table OK "
          f"(0 of 20 dots left of the best lap on both, sd {'/'.join(ratios)}×)")


# ─── refused-2026-09.md: its section numbers, its count, and every citation of a section ─────────
# In four waves running, two pull requests each claimed the same section number here: #314 and
# #315 both wrote §4, and #348, #349 and #351 all wrote §11 or §12. No per-PR run can see that,
# because each PR is consistent on its own base. Git flags the two appended sections as a conflict,
# and whoever resolves it renumbers by hand: the section, the intro's count, and every file that
# cites the section. Git flags none of the citing files. #351's probe merged its "§12" cleanly, and
# after the merge §12 was D1's section. These checks make that hand resolution checkable on the
# combined tree: numbers 1..N in order and each used once, an intro count DERIVED from N, and every
# "refused-2026-09.md … §N" anywhere in the tree landing on a section that exists. A dev probe's
# citation must also land on a section that names the probe, because each section's numbers come
# from its probe.
_REFUSED_HEADING = re.compile(r"^## (.*)$", re.M)
_REFUSED_NUMBERED = re.compile(r"(\d+)\. \S")
_REFUSED_UNNUMBERED = ("Related refusals that already live in the tree",)
_REFUSED_INTRO = re.compile(r"^([A-Z][a-z]+(?:-[a-z]+)?) features were built far enough", re.M)
_REFUSED_NAME = re.compile(r"refused-2026-09\.md")
_SECTION_REF = re.compile(r"§(\d+)\b(?!\.\d)")          # "§4.5" is another doc's subsection
# A citation's § numbers are the ones after the doc's name, up to the end of that sentence or the
# next file named in it, whichever comes first.
_CITATION_END = re.compile(r"[.;:!?][*_)\]`'\"]*(?=\s|$)|\.(?:md|py)\b")
_PROBE_NAME = re.compile(r"\bp\d+_[a-z0-9_]+")
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
         "fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def _count_word(n: int) -> str:
    """1..99 the way the intro spells its count: "Thirteen", "Twenty-one"."""
    word = _ONES[n] if n < 20 else _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    return word.capitalize()


def _refused_sections(doc: str) -> dict[int, str]:
    """{section number: its heading and body} — the LAST of any duplicated number wins, which is
    fine: a duplicate already fails `_refused_numbering_problems`."""
    heads = list(_REFUSED_HEADING.finditer(doc))
    out = {}
    for k, m in enumerate(heads):
        num = _REFUSED_NUMBERED.match(m.group(1))
        if num:
            out[int(num.group(1))] = doc[m.start():heads[k + 1].start() if k + 1 < len(heads) else None]
    return out


def _refused_numbering_problems(doc: str) -> tuple[list[int], list[str]]:
    """(the section numbers in file order, what is wrong with them and with the intro's count). A
    function of the text, so the negative control can hand it a planted copy."""
    numbers, problems = [], []
    for m in _REFUSED_HEADING.finditer(doc):
        head = m.group(1).strip()
        num = _REFUSED_NUMBERED.match(head)
        if num:
            numbers.append(int(num.group(1)))
        elif head not in _REFUSED_UNNUMBERED:
            problems.append(f"'## {head}' is neither a numbered section ('## N. …') nor one of "
                            f"{list(_REFUSED_UNNUMBERED)}, so a refusal written under it is outside "
                            f"the count")
    dup = sorted({n for n in numbers if numbers.count(n) > 1})
    if dup:
        problems.append(f"§{', §'.join(map(str, dup))} claimed more than once. Two refusals written "
                        f"from the same base took the same number: keep the one already on main "
                        f"(other files cite it), renumber the other to the next free number, and "
                        f"move every citation of it")
    elif numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"the sections run {numbers}, not 1..{len(numbers)} in order")
    intro = _REFUSED_INTRO.search(doc)
    want = _count_word(len(numbers))
    if intro is None:
        problems.append("the intro's '<Count> features were built far enough …' sentence is gone, "
                        "so nothing states the count this check derives")
    elif intro.group(1) != want:
        problems.append(f"the intro says '{intro.group(1)} features', but the doc has {len(numbers)} "
                        f"numbered sections: it must say '{want}'")
    return numbers, problems


def _refused_citations(files: dict[str, str]) -> list[tuple[str, int, set[str]]]:
    """(file, cited section, the dev probes that citation speaks for) for every "refused-2026-09.md
    … §N" in `files`. Scanned a paragraph at a time, because the probes index names each probe at the
    head of the paragraph that cites its section; a probe module also speaks for itself."""
    out = []
    for rel, raw in files.items():
        own = re.fullmatch(r"studio/dev/probes/(p\d+_[a-z0-9_]+)\.py", rel)
        for para in re.split(r"\n[ \t#]*\n", raw):
            flat = _flatten(para)
            probes = set(_PROBE_NAME.findall(flat)) | ({own.group(1)} if own else set())
            cited = set()
            for m in _REFUSED_NAME.finditer(flat):
                window = flat[m.end():m.end() + 240]
                end = _CITATION_END.search(window)
                cited |= {int(n) for n in _SECTION_REF.findall(window[:end.start() if end else None])}
            out += [(rel, n, probes if rel.startswith("studio/dev/probes/") else set())
                    for n in sorted(cited)]
    return out


def _refused_citation_problems(doc: str, files: dict[str, str]) -> tuple[int, list[str]]:
    """(how many citations were found, what is wrong with them). A function of the texts, like
    `_refused_numbering_problems`, for the same reason."""
    sections = _refused_sections(doc)
    cites = _refused_citations(files)
    problems = []
    for rel, n, probes in cites:
        if n not in sections:
            problems.append(f"{rel} cites refused-2026-09.md §{n}, and there is no §{n} "
                            f"(the doc has §1–§{max(sections)})")
        elif probes and not any(p in sections[n] for p in probes):
            head = sections[n].splitlines()[0]
            problems.append(f"{rel} cites §{n} for {sorted(probes)}, but §{n} is '{head}' and names "
                            f"none of them: the citation points at another refusal's section (a "
                            f"renumbered collision), or §{n} should name the probe its numbers come from")
    return len(cites), problems


def _refused_tree() -> dict[str, str]:
    """Every tracked text file that could cite the doc, except the doc and this file (whose negative
    control plants wrong citations on purpose). CHANGELOG.md stays in: a released entry is history,
    but the section numbers it cites are never renumbered, so they still have to resolve."""
    out = {}
    for rel in _tracked_files():
        path = os.path.join(_REPO, rel)
        if rel == os.path.relpath(_REFUSED, _REPO) or rel.endswith(os.path.basename(__file__)):
            continue
        if os.path.exists(path):
            out[rel] = open(path, encoding="utf-8", errors="ignore").read()
    return out


def test_the_refusals_doc_numbers_its_sections_once_each_and_counts_them():
    """The integrator's hand check, made automatic. Run on a combined tree, it fails on two
    refusals that took the same number, on a gap, on an intro count nobody bumped, and on a citation
    that did not move with its section."""
    doc = _read(_REFUSED)
    tree = _refused_tree()
    numbers, problems = _refused_numbering_problems(doc)
    found, cite_problems = _refused_citation_problems(doc, tree)
    problems += cite_problems
    # A citation outside the probes can only be checked for existence, and after a collision the
    # stale number still exists. So name every file that cites a doubled number, for the hand fix.
    for n in sorted({n for n in numbers if numbers.count(n) > 1}):
        citers = sorted({rel for rel, m, _p in _refused_citations(tree) if m == n})
        problems.append(f"§{n} is cited by {citers or 'nothing'}: check which refusal each one means")
    assert not problems, "studio/docs/refused-2026-09.md:\n  " + "\n  ".join(problems)
    # Both directions: the scan has to find the citations it exists for, so a regex that stops
    # matching cannot turn this into a check over nothing.
    subjects = {("studio/dev/probes/p4_corner_gps_quality.py", 4),
                ("studio/dev/probes/p15_gps_gap_census.py", 13)}
    seen = {(rel, n) for rel, n, _p in _refused_citations(tree)}
    assert subjects <= seen, f"the citation scan lost {sorted(subjects - seen)} — it has lost its subject"
    print(f"test_the_refusals_doc_numbers_its_sections_once_each_and_counts_them OK "
          f"(§1–§{len(numbers)}, intro '{_count_word(len(numbers))}', {found} citations resolve)")


def test_the_refusals_doc_guard_fails_on_each_collision_it_has_seen():
    """THE NEGATIVE CONTROL. Each planted copy is a shape that really reached this repo, or the
    hand fix that would have missed one. Every one must be caught, and the safe shapes must pass."""
    doc = _read(_REFUSED)
    n = len(_refused_sections(doc))
    assert n >= 13, n
    last, prev = f"## {n}. ", f"## {n - 1}. "
    related = "## " + _REFUSED_UNNUMBERED[0]
    word = _count_word(n)
    planted_docs = {
        "#348 + #349: two sections numbered the same": doc.replace(last, prev),
        "a gap, the next refusal numbered one too far": doc.replace(last, f"## {n + 1}. "),
        "two sections in the wrong order": doc.replace(prev, "## @@. ").replace(last, prev)
                                              .replace("## @@. ", last),
        "the intro not bumped": doc.replace(f"{word} features were built",
                                            f"{_count_word(n - 1)} features were built"),
        "a new section with its heading misspelt": doc.replace(
            related, f"## {n + 1} An unnumbered refusal — refused (X9)\n\n---\n\n{related}"),
    }
    for label, text in planted_docs.items():
        assert text != doc, f"the plant for {label!r} changed nothing — the fixture moved"
        _nums, problems = _refused_numbering_problems(text)
        assert problems, f"the numbering check missed: {label}"
    p15 = "studio/dev/probes/p15_gps_gap_census.py"
    p4 = "studio/dev/probes/p4_corner_gps_quality.py"
    p15_text = _read(os.path.join(_REPO, p15))
    p4_text = _read(os.path.join(_REPO, p4))
    planted_cites = {
        "#351: p15 still citing §12 after its refusal became §13":
            {p15: p15_text.replace("refused-2026-09.md`\n§13", "refused-2026-09.md`\n§12")},
        "#314: a probe pointing at the section another PR took":
            {p4: p4_text.replace("refused-2026-09.md` §4", "refused-2026-09.md` §3")},
        "a citation of a section that does not exist":
            {"studio/x.py": f"# The numbers are in `studio/docs/refused-2026-09.md` §{n + 1}.\n"},
        "the § wrapped onto the next comment line":
            {"studio/x.py": f"# Its verdict is `refused-2026-09.md`\n# §{n + 1} (a new refusal).\n"},
    }
    for label, files in planted_cites.items():
        assert all(text not in (p15_text, p4_text) for text in files.values()), \
            f"the plant for {label!r} changed nothing — the fixture moved"
        _found, problems = _refused_citation_problems(doc, files)
        assert problems, f"the citation check missed: {label}"
    safe = {
        "a mention with no section": "# the full refusal is in studio/docs/refused-2026-09.md.\n",
        "another doc's subsection": "# refused-2026-09.md does not cover it (market research §44.5).\n",
        "a § after another file in the sentence":
            "# refused-2026-09.md and `corner-match-0060-2026-09.md` §44 agree.\n",
        "two sections in one sentence": "# Verdicts: `refused-2026-09.md`\n# §9 (one) and §10 (two).\n",
    }
    for label, src in safe.items():
        _found, problems = _refused_citation_problems(doc, {"studio/x.py": src})
        assert not problems, f"the citation check flagged {label}, which is safe: {problems}"
    print(f"test_the_refusals_doc_guard_fails_on_each_collision_it_has_seen OK — "
          f"{len(planted_docs) + len(planted_cites)} planted collisions caught, {len(safe)} safe "
          f"shapes passed")


# ─── the real-footage half ───────────────────────────────────────────────────────────────────────
def _footage_root() -> str:
    """The folder `PACER_MEASURED_FIGURES_DIR` names; unset raises `FootageMissing`, which CTest
    reports as the calling check SKIPPED."""
    return _footage.directory("PACER_MEASURED_FIGURES_DIR",
                              "the folder holding D24/, Sandown_09_05_2026/ and SD_30_08_26/")


_LAP_SETS = {
    "0060": ("D24", ["GX020060.MP4", "GX030060.MP4"]),
    "0062": ("D24", ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"]),
    "0060 chapter 2": ("D24", ["GX020060.MP4"]),
    "0060 chapter 3": ("D24", ["GX030060.MP4"]),
    "0062 chapter 1": ("D24", ["GX010062.MP4"]),
    "0062 chapter 2": ("D24", ["GX020062.MP4"]),
    "0062 chapter 3": ("D24", ["GX030062.MP4"]),
    "D24 1 chapter": ("D24", ["GX010062.MP4"]),
    "D24 3 chapters": ("D24", ["GX010062.MP4", "GX020062.MP4", "GX030062.MP4"]),
    "Sandown chapter 1": ("Sandown_09_05_2026", ["GX010059.MP4"]),
    "Sandown 3 chapters": ("Sandown_09_05_2026", ["GX010059.MP4", "GX020059.MP4", "GX030059.MP4"]),
    "SD_30_08": ("SD_30_08_26", ["GX010065.MP4"]),
    # corner_model.IdealSample's rows since T16b, which name their own chapter files.
    **{name: (folder, list(files)) for name, (folder, files) in _ideal.ROW_RECORDINGS.items()},
}


class _Footage:
    """Loads lap sets read-only, jailed, and proves on exit that no file in the footage folders
    changed size or modification time."""

    def __init__(self, root: str):
        self.root = root
        sys.path.insert(0, _REPO)
        sys.path.insert(0, os.path.join(_REPO, "bindings", "pacer"))
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("PACER_NO_MEDIA", "1")
        from studio.dev._jail import divert_app_support
        divert_app_support("pacer-measured-figures-")

    def _stat(self) -> dict[str, tuple[int, int]]:
        out = {}
        for folder in {f for f, _ in _LAP_SETS.values()}:
            d = os.path.join(self.root, folder)
            if os.path.isdir(d):
                # stat() reads metadata and opens nothing. Dotfiles are Finder's, not the owner's.
                for name in sorted(n for n in os.listdir(d) if not n.startswith(".")):
                    st = os.stat(os.path.join(d, name))
                    out[os.path.join(folder, name)] = (st.st_size, st.st_mtime_ns)
        return out

    def __enter__(self):
        self.before = self._stat()
        return self

    def __exit__(self, *exc):
        after = self._stat()
        assert after == self.before, (
            "A FOOTAGE FILE CHANGED while this check ran — stop and look before anything else: "
            f"{ {k: (self.before.get(k), after.get(k)) for k in set(self.before) | set(after) if self.before.get(k) != after.get(k)} }")
        return False

    def paths(self, name: str) -> list[str] | None:
        folder, files = _LAP_SETS[name]
        paths = [os.path.join(self.root, folder, f) for f in files]
        assert not any(os.path.basename(p) == _STUB for p in paths), "the destroyed stub is never opened"
        return paths if all(os.path.isfile(p) for p in paths) else None

    def load(self, name: str, saved_line: bool = False):
        from studio.session import Session
        paths = self.paths(name)
        if paths is None:
            return None
        s = Session.load(paths)
        if saved_line:
            assert s.restore_saved_timing_lines() is True, f"{name}: no saved start line to restore"
        return s


def _coaching_measure(s):
    """Everything the two coaching tables and their prose publish, off one real session."""
    import numpy as np

    opps = s.coaching_opportunities()
    corner_list = s.corners.corner_list()
    n = len(corner_list)
    best = s.best_lap_id()
    mat = np.asarray([[c.time for c in s.corners.lap_corner_stats(i)] for i in s.consistency_lap_ids()
                      if len(s.corners.lap_corner_stats(i)) == n], float)
    best_t = np.asarray([c.time for c in s.corners.lap_corner_stats(best)], float)
    col = {c.cid: k for k, c in enumerate(corner_list)}
    sigma = {sp.cid: sp.sigma for sp in s.corner_consistency()}
    order = sorted(opps.rows, key=lambda r: -r.time_lost)
    floor = _constant(_PANEL, "DISPLAY_MIN_LOST_S")
    rows, z = [], {}
    for r in opps.rows:
        k = col[r.cid]
        t = mat[:, k]
        others = t[np.abs(t - best_t[k]) > 1e-12]
        z[r.cid] = (float(others.mean()) - best_t[k]) / float(others.std(ddof=1))
        if r.time_lost >= floor:
            rows.append((r.cid, order.index(r) + 1, r.time_lost, sigma[r.cid], r.evidence.iqr,
                         r.evidence.reach_laps, r.evidence.n_laps, r.evidence.abstain or "ranked"))
    ranked = opps.ranked_rows()
    by_cause: dict[str, float] = {}
    for r in ranked:
        by_cause[r.reason.kind] = by_cause.get(r.reason.kind, 0.0) + r.time_lost
    total = sum(by_cause.values())
    cause = max(by_cause.items(), key=lambda kv: kv[1])
    theme = (opps.n_laps, len(ranked), total, sum(r.time_lost for r in opps.rows if not r.evidence.ranked),
             _pct(opps.theme.execution_s / total), _pct(opps.theme.pace_s / total), cause[0],
             _pct(cause[1] / total))
    gaps = [float(best_t[k] - mat[:, k].min()) for k in range(n)]
    one_off = sum(r.evidence.abstain == "one_off" for r in opps.rows)
    return rows, theme, z, gaps, one_off


def test_the_coaching_tables_match_the_footage():
    """Re-measure both coaching tables, and the prose figures only footage can give, on every lap
    set the THEME table names."""
    root = _footage_root()
    ev, th = _evidence_rows(), _theme_rows()
    text = _flatten(_read(_COACHING))
    problems, ev_lines, th_lines, single_one_off, zs, gaps = [], [], [], [], {}, {}
    with _Footage(root) as fx:
        for name, pub in th.items():
            s = fx.load(name)
            if s is None:
                problems.append(f"{name}: footage missing under {root}")
                continue
            rows, theme, z, gap, one_off = _coaching_measure(s)
            laps, ranked, rs, ab, ex, pa, cause, cp = theme
            th_lines.append(f"#   {name:<16s}{laps:>6d}{ranked:>8d}{rs:>10.3f}{ab:>13.3f}{ex:>9d} %"
                            f"{pa:>5d} %  {cause} {cp} %")
            got = (laps, ranked, round(rs, 3), round(ab, 3), ex, pa, cause, cp)
            want = (pub.laps, pub.ranked, pub.ranked_s, pub.abstained_s, pub.execution, pub.pace,
                    pub.cause, pub.cause_pct)
            if got != want:
                problems.append(f"THEME {name}: published {want}, measured {got}")
            if "chapter" in name:
                single_one_off.append(one_off)
                continue
            zs[name], gaps[name] = z, gap
            for cid, rank, lost, sig, iqr, reached, n, gate in rows:
                ev_lines.append(f"#   {name}  C{cid:<6d}{rank:>5d}{lost:>8.3f}{sig:>9.3f}{iqr:>7.3f}"
                                f"{reached:>6d}/{n}  {gate}")
            want_rows = [(r.cid, r.rank, r.lost, r.sigma, r.iqr, r.reached, r.laps, r.gate)
                         for r in ev if r.rec == name]
            got_rows = [(c, k, round(lo, 3), round(sg, 3), round(q, 3), rc, nn, g)
                        for c, k, lo, sg, q, rc, nn, g in rows]
            if got_rows != want_rows:
                problems.append(f"evidence {name}: the table does not match the footage")
    m = _need(r"fires on one of the \d+ rows, (00\d\d) C(\d+) at z (\d\.\d\d)", text, "the z sentence")
    z_all = [(rec, cid, v) for rec, z in zs.items() for cid, v in z.items()]
    over = [(rec, cid) for rec, cid, v in z_all
            if v > 1.5 and cid in {r.cid for r in ev if r.rec == rec}]
    named_z = zs.get(m.group(1), {}).get(int(m.group(2)))
    if over != [(m.group(1), int(m.group(2)))] or named_z is None or abs(named_z - float(m.group(3))) > 0.005:
        problems.append(f"z > 1.5 fires on {over}; the prose names {m.groups()} (measured z {named_z})")
    m = _need(r"at all 12 of 12 corners \(by (\d\.\d\d)\.\.(\d\.\d\d) s on 0060, (\d\.\d\d)\.\.(\d\.\d\d) s on 0062\)",
              text, "the optimal-line sentence")
    for i, rec in enumerate(("0060", "0062")):
        g = gaps.get(rec, [])
        if len(g) != 12 or min(g) <= 0 or (float(m.group(1 + 2 * i)), float(m.group(2 + 2 * i))) != (
                round(min(g), 2), round(max(g), 2)):
            problems.append(f"{rec}: best lap vs each corner's best instance {g}, prose {m.groups()}")
    m = _need(r"it fires on (\w+) of the (\w+) single chapters", text, "the ONE_OFF sentence")
    numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    if (numbers[m.group(1)], numbers[m.group(2)]) != (sum(x > 0 for x in single_one_off), len(single_one_off)):
        problems.append(f"ONE_OFF fires on {single_one_off} single chapters; prose says {m.groups()}")
    report = "\n".join(["  re-measured evidence table:"] + ev_lines + ["  re-measured THEME table:"] + th_lines)
    assert not problems, "coaching.py's measured figures are not what the app computes:\n  " + \
        "\n  ".join(problems) + "\n" + report
    print(f"test_the_coaching_tables_match_the_footage OK\n{report}")


def _floor_measure(s):
    """The floor table's stated method: every valid lap, every 25 ms of its window."""
    import numpy as np

    eps = _constant(_THEME, "DELTA_EVEN_EPS_S")
    values, ends = [], []
    for lap in s.valid_lap_ids():
        lo, hi = s.lap_window(lap)
        values.extend(s.delta_to_ideal_at(lap, float(t)) for t in np.arange(lo, hi, 0.025))
        ends.append(s.delta_to_ideal_at(lap, hi - 1e-6))
    v = np.asarray(values, float)
    best = s.best_lap_id()
    lo, hi = s.lap_window(best)
    peak = max(s.delta_to_ideal_at(best, float(t)) for t in np.arange(lo, hi, 0.025))
    return (len(s.valid_lap_ids()), int(v.size), round(float(v.min()), 3), round(100 * float(np.mean(v < 0)), 2),
            round(100 * float(np.mean(v < -eps)), 2)), (min(ends), max(ends)), peak


def test_the_floor_table_matches_the_footage():
    root = _footage_root()
    rows = _floor_rows()
    text = _flatten(_read(_THEME))
    problems, lines, ends, peaks = [], [], [], []
    with _Footage(root) as fx:
        for r in rows:
            s = fx.load(r.name, saved_line=r.saved)
            if s is None:
                problems.append(f"{r.name}: footage missing under {root}")
                continue
            got, end, peak = _floor_measure(s)
            ends.extend(end)
            peaks.append(peak)
            lines.append(f"#   {r.name + (' †' if r.saved else ''):<22s}{got[0]:>5d}{got[1]:>9d}  {got[2]:.3f} s"
                         f"{got[3]:>9.2f} %{got[4]:>12.2f} %")
            if got != (r.laps, r.samples, r.floor, r.neg, r.minus):
                problems.append(f"{r!r}: measured {got}")
    m = _need(r"against end-of-lap values of \+(\d+\.\d\d) … \+(\d+\.\d\d) s", text, "the end-of-lap range")
    if ends and (float(m.group(1)), float(m.group(2))) != (round(min(ends), 2), round(max(ends), 2)):
        problems.append(f"end-of-lap values {min(ends):.3f} … {max(ends):.3f}, prose {m.groups()}")
    m = _need(r"whose job is to read 0 … \+(\d\.\d) s", text, "the readout's range")
    if peaks and float(m.group(1)) != round(max(peaks), 1):
        problems.append(f"the best lap's Δideal peaks at {max(peaks):.3f} s, prose says +{m.group(1)}")
    report = "\n".join(["  re-measured floor table:"] + lines)
    assert not problems, "theme.py's floor table is not what the app computes:\n  " + \
        "\n  ".join(problems) + "\n" + report
    print(f"test_the_floor_table_matches_the_footage OK\n{report}")


def _recombination(s) -> dict[str, float]:
    """The #272 record's stated method, on the app's own segment matrix (see the record)."""
    import numpy as np

    sb = s.ideal_segment_bests()
    times = np.asarray(sb.times, float)
    # C5: a cell may donate only when it is BOTH admitted (MAX_DONOR_SPAN_DEV) and resolved (its two
    # boundaries matched on track). This stand-in re-runs the composite over subsets, so it has to
    # mask exactly as `CornerModel.segment_bests` does — including the two-stage fallback, a column
    # with nothing resolved dropping back to its admitted cells rather than to every cell.
    span = np.asarray(sb.admitted, bool)
    admitted = span & np.asarray(sb.resolved, bool)
    lap_times = np.asarray([s.lap_time(i) for i in sb.lap_ids], float)
    ideal, best = float(s.ideal_total()), float(s.lap_time(s.best_lap_id()))

    def ideal_of(t, a, sp=None):
        sp = a if sp is None else sp
        empty = ~a.any(axis=0)
        a = np.where(empty[None, :], sp, a)
        m = np.where(a, t, np.inf).min(axis=0)
        return float(np.where(np.isinf(m), t.min(axis=0), m).sum())

    assert abs(ideal_of(times, admitted, span) - ideal) < 1e-9, (
        "the stand-in no longer reproduces the ideal")
    cols = [times[admitted[:, j], j] if admitted[:, j].any() else times[:, j] for j in range(times.shape[1])]
    grid, pmf = 0.001, np.array([1.0])
    for c in cols:
        k = np.round((c - c.min()) / grid).astype(int)
        pmf = np.convolve(pmf, np.bincount(k) / len(c))
    x = sum(float(c.min()) for c in cols) + grid * np.arange(pmf.size)
    cdf = np.cumsum(pmf)
    dots = [float(x[np.searchsorted(cdf, (i + 0.5) / 20)]) for i in range(20)]
    moves = []
    for k in range(times.shape[0]):
        keep = np.arange(times.shape[0]) != k
        moves.append(abs(ideal_of(times[keep], admitted[keep], span[keep]) - ideal))
    sd = math.sqrt(sum(float(np.var(c)) for c in cols))
    return {"laps": len(sb.lap_ids), "ideal": round(ideal, 3), "best": round(best, 3),
            "lo": round(sum(float(c.min()) for c in cols), 3), "hi": round(sum(float(c.max()) for c in cols), 3),
            "sd": round(sd, 3), "ratio": round(sd / float(np.std(lap_times, ddof=1)), 2),
            "pct": float(cdf[np.searchsorted(x, best + 1e-9) - 1]) * 100,
            "dot_lo": round(dots[0], 3), "dot_hi": round(dots[-1], 3), "left": sum(d <= best for d in dots),
            "jack_max": max(moves), "jack_n": sum(m > 1e-9 for m in moves)}


def test_the_refusal_record_matches_the_footage():
    root = _footage_root()
    now, _then = _record_tables()
    text = _flatten(_read(_REFUSED))
    jack = _need(r"at most \*\*(\d\.\d{3}) s\*\* and \*\*(\d\.\d{3}) s\*\*, with only \*\*(\d+)/\d+\*\* and "
                 r"\*\*(\d+)/\d+\*\*", text, "the jackknife line")
    problems, lines = [], []
    with _Footage(root) as fx:
        for i, rec in enumerate(("0060", "0062")):
            s = fx.load(rec)
            if s is None:
                problems.append(f"{rec}: footage missing under {root}")
                continue
            got = _recombination(s)
            pub = now[rec]
            pct = f"{got['pct']:.1g}" if got["pct"] < 0.1 else f"{got['pct']:.2f}"
            lines.append(f"  {rec} ({got['laps']} laps): ideal / best {got['ideal']:.3f} / {got['best']:.3f} · "
                         f"support [{got['lo']:.3f} … {got['hi']:.3f}] = {got['hi'] - got['lo']:.1f} s · sd "
                         f"{got['sd']:.3f} ({got['ratio']:.2f}×) · percentile {pct} · dots {got['dot_lo']:.3f} … "
                         f"{got['dot_hi']:.3f}, {got['left']} of 20 left · jackknife {got['jack_max']:.3f} s, "
                         f"{got['jack_n']}/{got['laps']}")
            for key in ("laps", "ideal", "best", "lo", "hi", "sd", "ratio", "dot_lo", "dot_hi", "left"):
                if abs(got[key] - pub[key]) > 1e-9:
                    problems.append(f"{rec} {key}: published {pub[key]}, measured {got[key]}")
            if float(pct) != pub["pct"]:
                problems.append(f"{rec} percentile: published {pub['pct']}, measured {pct}")
            if (round(got["jack_max"], 3), got["jack_n"]) != (float(jack.group(1 + i)), int(jack.group(3 + i))):
                problems.append(f"{rec} jackknife: measured {got['jack_max']:.3f} s, {got['jack_n']} laps")
    assert not problems, "the #272 record is not what the app computes:\n  " + "\n  ".join(problems) + \
        "\n" + "\n".join(lines)
    print("test_the_refusal_record_matches_the_footage OK\n" + "\n".join(lines))


def _brake_measure(s):
    """coaching.py's brake-habit table by its stated method, off one real session: the rows the
    coaching panel shows, RANKED, whose best lap has a matched application that the panel's own
    `_brake_point_hint` would still print if it were handed that single application instead of the
    habit. Also everything the prose names that is not a cell."""
    from types import SimpleNamespace

    from studio import coaching
    from studio import coaching_panel as panel

    opps = s.coaching_opportunities()
    order = sorted(opps.rows, key=lambda r: -r.time_lost)   # the evidence table's rank
    best = s.best_lap_id()
    bps = {bp.cid: bp for bp in s.driving.lap_brake_points(best)}
    braking = {b.cid: b for b in s.brake_report()}
    habits = s.coaching_brake_points()
    clean = len(s.consistency_lap_ids())
    rows = []
    for r in panel._shown_rows(opps):
        bp = bps.get(r.cid)
        if not r.evidence.ranked or bp is None:
            continue
        # The best lap's application in the habit's shape. n_laps is the one field a single lap
        # cannot have; it is set to the minimum so the habit-only gate is neutral.
        one_lap = SimpleNamespace(cid=bp.cid, n_laps=coaching.MIN_BRAKE_LAPS, metres_later=bp.metres_later,
                                  optimal_brake_dist=bp.optimal_brake_dist, actual_brake_dist=bp.actual_brake_dist)
        if panel._brake_point_hint(one_lap, r.entry_dist) is None:
            continue
        b = braking[r.cid]
        # The table's "habit" is BRAKING's column, which the fix made the coaching hint's by construction.
        assert habits[r.cid].metres_later == b.metres_later_med, (r.cid, habits[r.cid], b)
        rows.append((r.cid, order.index(r) + 1, bp.metres_later, b.metres_later_med, b.n, clean))
    unbraked = {r.cid: (order.index(r) + 1, braking[r.cid].n) for r in opps.rows
                if r.evidence.ranked and r.cid not in bps and r.cid in braking}
    return sorted(rows, key=lambda x: x[1]), unbraked, min(b.n for b in braking.values())


def test_the_brake_habit_table_matches_the_footage():
    root = _footage_root()
    pub = _brake_rows()
    text = _flatten(_read(_COACHING))
    problems, lines, gaps, unbraked, fewest = [], [], {}, {}, []
    with _Footage(root) as fx:
        for rec in ("0060", "0062"):
            s = fx.load(rec)
            if s is None:
                problems.append(f"{rec}: footage missing under {root}")
                continue
            rows, unbraked[rec], least = _brake_measure(s)
            fewest.append(least)
            gaps[rec] = rows
            for cid, rank, best, habit, n, clean in rows:
                lines.append(f"#   {rec}  C{cid:<6d}{rank:>4d}{best:>12.1f}{habit:>9.1f}  {n:>2d}/{clean}")
            got = [(c, k, round(b, 1), round(h, 1), n, cl) for c, k, b, h, n, cl in rows]
            want = [(r.cid, r.rank, r.best, r.habit, r.laps, r.clean) for r in pub if r.rec == rec]
            if got != want:
                problems.append(f"brake habits {rec}: published {want}, measured {got}")
    m = _need(r"sit (\d+\.\d) m apart at the median on 0060 \(worst (\d+\.\d) m, C(\d+)\) and (\d+\.\d) m "
              r"apart on 0062 \(worst (\d+\.\d) m, C(\d+)\)", text, "the gap sentence under the table")
    for i, rec in enumerate(("0060", "0062")):
        g = [abs(b - h) for _c, _k, b, h, _n, _cl in gaps.get(rec, [])]
        if not g:
            continue
        worst = max(gaps[rec], key=lambda x: abs(x[2] - x[3]))
        got = (f"{_median(g):.1f}", f"{max(g):.1f}", str(worst[0]))
        if got != (m.group(1 + 3 * i), m.group(2 + 3 * i), m.group(3 + 3 * i)):
            problems.append(f"{rec}: median / worst gap measured {got}, prose {m.groups()[3 * i:3 + 3 * i]}")
    m = _need(r"0062's best lap had no matched brake event at all into C(\d+), so the (top|second|third)-ranked "
              r"row said nothing about a corner (\d+) laps DID brake into", text, "the unbraked-corner sentence")
    rank = {"top": 1, "second": 2, "third": 3}[m.group(2)]
    if unbraked.get("0062", {}).get(int(m.group(1))) != (rank, int(m.group(3))):
        problems.append(f"0062's ranked corners the best lap never braked into: {unbraked.get('0062')}; "
                        f"prose names C{m.group(1)}, rank {rank}, {m.group(3)} laps")
    m = _need(r"every corner on the two D24 recordings matched on at least (\d+) of its clean laps", text,
              "MIN_BRAKE_LAPS's measured note")
    if fewest and min(fewest) != int(m.group(1)):
        problems.append(f"the fewest laps any corner matched is {min(fewest)}; prose says {m.group(1)}")
    report = "\n".join(["  re-measured brake-habit table:"] + lines)
    assert not problems, "coaching.py's brake-habit table is not what the app computes:\n  " + \
        "\n  ".join(problems) + "\n" + report
    print(f"test_the_brake_habit_table_matches_the_footage OK\n{report}")


def _hint_measure(s):
    """coaching_panel's brake-hint gate table off one real session: one row per RANKED coaching row
    that has a habit to print, with the corner's turn-in and apex on the reference odometer and the
    median optimum the panel gates on. Read through the panel's OWN predicate, not a copy of it."""
    from studio import coaching_panel as panel

    opps = s.coaching_opportunities()
    habits = s.coaching_brake_points()
    corner = {int(c.cid): c for c in s.corners.corner_list()}
    rows, ungated = [], []
    for r in sorted((r for r in opps.rows if r.evidence.ranked), key=lambda r: r.cid):
        bp = habits.get(r.cid)
        # `entry_dist=None` skips the geometry gate, so this asks "would there be metres at all?"
        if bp is None or panel._brake_point_hint(bp, None) is None:
            ungated.append(r.cid)
            continue
        hint = panel._brake_point_hint(bp, r.entry_dist)
        rows.append((r.cid, float(r.entry_dist), float(corner[int(r.cid)].apex),
                     float(bp.optimal_brake_dist), "shown" if hint else "suppressed"))
    return rows, ungated


def test_the_brake_hint_gate_table_matches_the_footage():
    """T15 — re-measure coaching_panel's gate table on the two D24 recordings. The row SET is the
    app's own ranked set, so a corner that stops being ranked is a failure here rather than a row
    that quietly goes missing."""
    root = _footage_root()
    pub = _hint_rows()
    problems, lines = [], []
    with _Footage(root) as fx:
        for rec in ("0060", "0062"):
            s = fx.load(rec)
            if s is None:
                problems.append(f"{rec}: footage missing under {root}")
                continue
            rows, ungated = _hint_measure(s)
            for cid, turn_in, apex, optimum, hint in rows:
                lines.append(f"#   {rec}  C{cid:<6d}{turn_in:>10.1f}{apex:>9.1f}{optimum:>11.1f}   {hint}")
            got = [(c, round(t, 1), round(a, 1), round(o, 1), h) for c, t, a, o, h in rows]
            want = [(r.cid, r.turn_in, r.apex, r.optimum, r.hint) for r in pub if r.rec == rec]
            if got != want:
                problems.append(f"hint gate {rec}: published {want}, measured {got}")
            if ungated:
                problems.append(f"{rec}: ranked rows with no metres to gate {ungated} — the note "
                                f"says that is every ranked row on both recordings")
    report = "\n".join(["  re-measured brake-hint gate table:"] + lines)
    assert not problems, "coaching_panel.py's brake-hint gate table is not what the app computes:\n  " + \
        "\n  ".join(problems) + "\n" + report
    print(f"test_the_brake_hint_gate_table_matches_the_footage OK\n{report}")


def _avg_ranks(x):
    """Ranks 0..n-1 with tied values given the mean of the ranks they span."""
    import numpy as np

    x = np.asarray(x, float)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=float)
    for v in np.unique(x):
        tied = x == v
        ranks[tied] = ranks[tied].mean()
    return ranks


def _beat_measure(s, n_perm: int = 20000):
    """corner_model's beat-rate table by its stated method, off one real session."""
    import numpy as np

    sb = s.ideal_segment_bests()
    best = s.best_lap_id()
    total_ref = float(s.corners.basis()[1])
    wide = np.diff(np.asarray(sb.s_edges, float)) * total_ref > _constant(_CORNER_MODEL, "POINT_SPAN_M")
    rate = np.asarray([b / n for b, n in sb.beat_counts(best)], float)[wide]
    dur = np.asarray(sb.times, float).mean(axis=0)[wide]
    r = float(np.corrcoef(rate, dur)[0, 1])
    ra, rd = _avg_ranks(rate), _avg_ranks(dur)
    rho = float(np.corrcoef(ra, rd)[0, 1])
    rng = np.random.default_rng(0)
    # Shuffling the rates shuffles their tied-averaged ranks with them.
    hits = sum(abs(float(np.corrcoef(ra[rng.permutation(len(ra))], rd)[0, 1])) >= abs(rho) - 1e-12
               for _ in range(n_perm))
    return int(wide.sum()), r, rho, hits / n_perm


def test_the_beat_rate_table_matches_the_footage():
    root = _footage_root()
    rows = _beat_rows()
    problems, lines = [], []
    with _Footage(root) as fx:
        for row in rows:
            s = fx.load(_BEAT_SETS[row.name], saved_line=row.saved)
            if s is None:
                problems.append(f"{row.name}: footage missing under {root}")
                continue
            n, r, rho, p = _beat_measure(s)
            label = row.name + (" †" if row.saved else "")
            lines.append(f"        | {label:<14s} | {n:<2d} | {r:+.3f} | {rho:+.3f}   | {p:.3f} |".replace("-", "−"))
            if (n, f"{r:+.3f}", f"{rho:+.3f}", f"{p:.3f}") != (row.n, f"{row.r:+.3f}", f"{row.rho:+.3f}", f"{row.p:.3f}"):
                problems.append(f"{row!r}: measured n {n}, r {r:+.4f}, ρ {rho:+.4f}, p {p:.4f}")
    report = "\n".join(["  re-measured beat-rate table:"] + lines)
    assert not problems, "corner_model's beat-rate table is not what the app computes:\n  " + \
        "\n  ".join(problems) + "\n" + report
    print(f"test_the_beat_rate_table_matches_the_footage OK\n{report}")


def _focus_measure(s60, p60, s62, p62):
    """focus.py's tables by their stated method: promote 0060's top three through
    `Session.focus_items`, re-measure them on 0062 through `Session.focus_report` — once against
    the real (empty) session-record store, and once with an identical record forced onto both
    sides so the spread test is reached — plus the window figures and the apex/lap-total prose."""
    import numpy as np

    from studio import coaching, session_record
    from studio import focus as F

    e60, e62 = s60.library_entry(p60), s62.library_entry(p62)
    top = s60.coaching_opportunities().ranked_rows()[:3]
    items = s60.focus_items([r.cid for r in top], e60)
    real = s62.focus_report(items, e62, session_record.empty_store(), e60["track"])
    store = session_record.empty_store()
    for fp in (e60["fingerprint"], e62["fingerprint"]):
        rec = session_record.blank_record()
        rec["conditions"] = "dry"
        session_record.put(store, fp, rec)
    alike = s62.focus_report(items, e62, store, e60["track"])
    rows = []
    for r, o in zip(top, alike.outcomes, strict=True):
        it, now = o.item, o.now
        rows.append((it.cid, r.time_lost, it.median_s, it.iqr_s, now.median, now.iqr, o.delta,
                     coaching.SPREAD_MARGIN * max(it.iqr_s, now.iqr), o.kind, it.n_laps, now.n_laps))
    t60, t62 = float(s60.corners.basis()[1]), float(s62.corners.basis()[1])
    c60 = {c.cid: c for c in s60.corners.corner_list()}
    c62 = {c.cid: c for c in s62.corners.corner_list()}
    cids = sorted(set(c60) & set(c62))
    own60 = dict(zip(cids, s60.focus_samples([(c60[c].enter / t60, c60[c].exit / t60) for c in cids]), strict=True))
    own62 = dict(zip(cids, s62.focus_samples([(c62[c].enter / t62, c62[c].exit / t62) for c in cids]), strict=True))
    at60 = dict(zip(cids, s62.focus_samples([(c60[c].enter / t60, c60[c].exit / t60) for c in cids]), strict=True))
    windows = {c: (c60[c].exit - c60[c].enter, c62[c].exit - c62[c].enter, own60[c].median, own62[c].median,
                   own62[c].median - own60[c].median, at60[c].median, at60[c].median - own60[c].median)
               for c in cids}
    apex = [c62[c].apex - c60[c].apex * t62 / t60 for c in cids]
    # session.py's focus_items note: the stored (window) median against the corner service's own.
    service = {}
    for name, s, own in (("0060", s60, own60), ("0062", s62, own62)):
        mat = np.asarray([[st.time for st in s.corners.lap_corner_stats(i)] for i in s.consistency_lap_ids()
                          if len(s.corners.lap_corner_stats(i)) == len(cids)], float)
        service[name] = [abs(float(np.median(mat[:, k])) - own[c].median) for k, c in enumerate(cids)]
    return {"rows": rows, "blockers": [o.blocker for o in real.outcomes], "no_record": F.BLOCK_NO_RECORD,
            "dates": (e60["date"], e62["date"]), "totals": (t60, t62), "windows": windows, "apex": apex,
            "n_corners": len(cids), "service": service}


def test_the_focus_tables_match_the_footage():
    root = _footage_root()
    rows, windows = _focus_tables()
    text = _flatten(_read(_FOCUS))
    problems, lines = [], []
    with _Footage(root) as fx:
        s60, s62 = fx.load("0060"), fx.load("0062")
        if s60 is None or s62 is None:
            raise AssertionError(f"D24 footage missing under {root}")
        got = _focus_measure(s60, fx.paths("0060"), s62, fx.paths("0062"))
    for cid, lost, m0, q0, m1, q1, d, bar, kind, _n0, _n1 in got["rows"]:
        lines.append(f"  C{cid:<6d}+{lost:.3f} s      {m0:.3f} s      {q0:.3f}  {m1:.3f} s      {q1:.3f}  "
                     f"{d:+.3f}  {bar:.3f}  {kind}")
    measured = [(c, f"{lo:.3f}", f"{m0:.3f}", f"{q0:.3f}", f"{m1:.3f}", f"{q1:.3f}", f"{d:+.3f}", f"{b:.3f}", k)
                for c, lo, m0, q0, m1, q1, d, b, k, _n0, _n1 in got["rows"]]
    published = [(r.cid, f"{r.promoted:.3f}", f"{r.then_med:.3f}", f"{r.then_iqr:.3f}", f"{r.now_med:.3f}",
                  f"{r.now_iqr:.3f}", f"{r.change:+.3f}", f"{r.bar:.3f}", r.verdict) for r in rows]
    if measured != published:
        problems.append(f"promoted three: published {published}, measured {measured}")
    if got["blockers"] != [got["no_record"]] * len(rows):
        problems.append(f"with no session records the verdicts are blocked by {got['blockers']}, not no_record")
    m = _need(r"\(0060: (\d{4}-\d\d-\d\d), (\d+) laps; 0062: (\d{4}-\d\d-\d\d), (\d+) laps\)", text, "the dates")
    laps = {(r[9], r[10]) for r in got["rows"]}
    if (m.group(1), m.group(3)) != got["dates"] or laps != {(int(m.group(2)), int(m.group(4)))}:
        problems.append(f"dates {got['dates']} and laps {laps}; prose {m.groups()}")
    for cid, pub in windows.items():
        w = got["windows"][cid]
        lines.append(f"  C{cid:<6d}{w[0]:.1f} m       {w[1]:.1f} m       {w[2]:.3f} s   {w[3]:.3f} s   "
                     f"{w[4]:+.3f} s    {w[5]:.3f} s           {w[6]:+.3f} s")
        fmt = ("{:.1f}", "{:.1f}", "{:.3f}", "{:.3f}", "{:+.3f}", "{:.3f}", "{:+.3f}")
        if [f.format(x) for f, x in zip(fmt, w, strict=True)] != [f.format(x) for f, x in zip(fmt, pub, strict=True)]:
            problems.append(f"window C{cid}: published {pub}, measured {tuple(round(x, 4) for x in w)}")
    m = _need(r"across the (\w+) corners the two sessions' apexes agree to ([−-]\d\.\d)\.\.\+(\d\.\d) m", text,
              "the apex sentence")
    apex = got["apex"]
    words = {12: "twelve", 11: "eleven", 13: "thirteen"}
    if (m.group(1), _signed(m.group(2)), float(m.group(3))) != (words.get(got["n_corners"]), round(min(apex), 1),
                                                               round(max(apex), 1)):
        problems.append(f"apexes agree to {min(apex):.2f}..{max(apex):.2f} m over {got['n_corners']} corners; "
                        f"prose {m.groups()}")
    t60, t62 = got["totals"]
    m = _need(r"the lap totals to (\d\.\d\d) % \((\d+\.\d) vs (\d+\.\d) m\)", text, "the lap-total sentence")
    if (m.group(1), m.group(2), m.group(3)) != (f"{100 * (t62 / t60 - 1):.2f}", f"{t60:.1f}", f"{t62:.1f}"):
        problems.append(f"lap totals {t60:.3f} vs {t62:.3f}; prose {m.groups()}")
    m = _need(r"lap totals differ by (\d+\.\d\d) m on (\d+) m", text, "MAX_LAP_TOTAL_DRIFT's note")
    if m.group(1) != f"{t62 - t60:.2f}":
        problems.append(f"lap totals differ by {t62 - t60:.4f} m; MAX_LAP_TOTAL_DRIFT's note says {m.group(1)}")
    sm = _need(r"per-corner medians run (\d\.\d\d)–(\d\.\d\d) s apart on 0060 and (\d\.\d\d)–(\d\.\d\d) s apart on "
               r"0062", _flatten(_read(_SESSION)), "Session.focus_items' note")
    for i, rec in enumerate(("0060", "0062")):
        g = got["service"][rec]
        if (float(sm.group(1 + 2 * i)), float(sm.group(2 + 2 * i))) != (_pct(min(g)) / 100, _pct(max(g)) / 100):
            problems.append(f"{rec}: window vs corner-service medians {min(g):.3f}..{max(g):.3f} s; "
                            f"session.py says {sm.group(1 + 2 * i)}–{sm.group(2 + 2 * i)}")
    report = "\n".join(["  re-measured focus tables:"] + lines +
                       [f"  apexes {min(apex):+.2f}..{max(apex):+.2f} m; totals {t60:.3f} / {t62:.3f} m; "
                        f"window vs service 0060 {min(got['service']['0060']):.3f}..{max(got['service']['0060']):.3f}"
                        f", 0062 {min(got['service']['0062']):.3f}..{max(got['service']['0062']):.3f} s"])
    assert not problems, "focus.py's measured figures are not what the app computes:\n  " + \
        "\n  ".join(problems) + "\n" + report
    print(f"test_the_focus_tables_match_the_footage OK\n{report}")


# Each is its own CTest registration, `footage.<name>` (tests/_footage.py): reported SKIPPED by
# name without PACER_MEASURED_FIGURES_DIR, and not part of `_run_all`.
FOOTAGE_CHECKS = (test_the_coaching_tables_match_the_footage,
                  test_the_brake_habit_table_matches_the_footage,
                  test_the_brake_hint_gate_table_matches_the_footage,
                  test_the_beat_rate_table_matches_the_footage,
                  test_the_focus_tables_match_the_footage,
                  test_the_floor_table_matches_the_footage,
                  test_the_refusal_record_matches_the_footage)


def _run_all():
    test_every_table_no_footage_can_re_measure_carries_its_mark()
    test_the_mark_guard_fails_on_each_planted_defect()
    test_every_quote_of_the_interpolated_cell_error_is_dated_and_names_its_measurement()
    test_the_evidence_prose_is_its_table_s_arithmetic()
    test_the_reach_line_and_the_spread_gate_are_the_table_s()
    test_the_theme_table_is_the_evidence_table_s_arithmetic()
    test_the_theme_prose_follows_the_table_and_THEME_SHARE()
    test_every_quote_of_the_coaching_figures_is_coaching_py_s()
    test_the_brake_habit_prose_is_its_table_s_arithmetic()
    test_every_quote_of_the_brake_habit_figures_is_the_table_s()
    test_the_brake_hint_gate_prose_is_its_table_s_arithmetic()
    test_the_beat_rate_verdict_is_derived_from_its_table()
    test_the_focus_prose_is_its_tables_arithmetic()
    test_every_quote_of_the_focus_figures_is_focus_py_s()
    test_the_floor_table_is_consistent_with_its_own_definitions()
    test_every_quote_of_the_floor_is_a_row_of_the_table()
    test_the_refusal_record_s_verdict_is_derived_from_its_table()
    test_the_refusals_doc_numbers_its_sections_once_each_and_counts_them()
    test_the_refusals_doc_guard_fails_on_each_collision_it_has_seen()
    print("\nmeasured-figures checks passed")


if __name__ == "__main__":
    if _footage.requested():
        sys.exit(_footage.run(FOOTAGE_CHECKS))
    sys.exit(_run_all())
