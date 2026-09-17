"""THREE TABLES MEASURED ON REAL FOOTAGE, AND EVERY SENTENCE THAT QUOTES THEM.

WHY THIS FILE EXISTS. #300 warped every lap. Three published figures were measured before it and
nobody re-measured them: `coaching.py`'s evidence and THEME blocks, `theme.py`'s pointwise-Δ floor
table above `format_ideal_run`, and the #272 recombination record in
`studio/docs/refused-2026-09.md`. When they were re-measured, every block had moved, and prose
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
     it, and the loaders below assert as much.

Checks 1 and 2 cannot see whether a table matches the app. Only 3 can, and only where the footage
is. Figures that exist only in prose and need footage to derive (the z-score, the best lap's gap to
each corner's best instance, the end-of-lap range) are checked by 3 alone.

Run:  python tests/test_measured_figures.py
      PACER_MEASURED_FIGURES_DIR=~/Desktop python tests/test_measured_figures.py
"""

from __future__ import annotations

import ast
import math
import os
import re
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COACHING = os.path.join(_REPO, "studio", "coaching.py")
_PANEL = os.path.join(_REPO, "studio", "coaching_panel.py")
_THEME = os.path.join(_REPO, "studio", "theme.py")
_REFUSED = os.path.join(_REPO, "studio", "docs", "refused-2026-09.md")
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
    m = _need(r"Measured, the (\d+) real rows' reach rates sort as ([\d. |]+?) % — no row sits between "
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
            if (float(m.group(1)), float(m.group(2))) != (c1.sigma, c1.iqr):
                problems.append(f"{rel}: 0062 C1 σ {m.group(1)} / IQR {m.group(2)}, the table says "
                                f"{c1.sigma} / {c1.iqr}")
        for m in re.finditer(r"0060 (?:is )?(\d+) ?% execution(?:,| and) 0062 (?:is )?(\d+) ?% pace", text):
            found["theme"].append(rel)
            if (int(m.group(1)), int(m.group(2))) != (t["0060"].execution, t["0062"].pace):
                problems.append(f"{rel}: 0060 {m.group(1)} % execution / 0062 {m.group(2)} % pace, the "
                                f"table says {t['0060'].execution} / {t['0062'].pace}")
        for m in re.finditer(r"braking holds (\d+) ?% of 0060's ranked time[^.]{0,20}?(\d+) ?% of 0062's", text):
            found["cause"].append(rel)
            if (int(m.group(1)), int(m.group(2))) != (t["0060"].cause_pct, t["0062"].cause_pct):
                problems.append(f"{rel}: braking {m.group(1)} % / {m.group(2)} %, the table says "
                                f"{t['0060'].cause_pct} / {t['0062'].cause_pct}")
        for m in re.finditer(r"OTHER laps[^.]{0,60}?\((\d+)\.\.(\d+) of them\)", text):
            found["beaten"].append(rel)
            if (int(m.group(1)), int(m.group(2))) != (min(beaten), max(beaten)):
                problems.append(f"{rel}: {m.group(1)}..{m.group(2)} other laps beat the target, the "
                                f"table says {min(beaten)}..{max(beaten)}")
        for m in re.finditer(r"ONE_OFF[^.]{0,120}?fired on (\d+) of (?:the )?(\d+) rows", text):
            found["beaten"].append(rel)
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
    m = _need(r"a wobble of at most (\d\.\d\d) s \((\d\.\d\d) s on the loader's lines\)", text,
              "the wobble sentence under the floor table")
    assert float(m.group(1)) == round(max(-r.floor for r in rows), 2), (m.group(1), rows)
    assert float(m.group(2)) == round(max(-r.floor for r in rows if not r.saved), 2), (m.group(2), rows)
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
    ends = _need(r"against end-of-lap values of \+(\d\.\d\d) … \+(\d\.\d\d) s", _flatten(_read(_THEME)),
                 "theme.py's end-of-lap range").groups()
    found, problems = [], []
    for rel, text in _scanned():
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if "#211" in sentence or "before #300" in sentence:
                continue
            for m in re.finditer(r"end-of-lap values of \+(\d\.\d\d) … \+(\d\.\d\d) s", sentence):
                found.append(rel)
                if m.groups() != ends:
                    problems.append(f"{rel}: end-of-lap values +{m.group(1)} … +{m.group(2)} s, theme.py "
                                    f"says +{ends[0]} … +{ends[1]}")
            quotes = re.findall(r"(?:floor(?: is)?|worst excursion (?:to|is)|most negative value is) \**([−-]0\.\d{3}) s",
                                sentence) + re.findall(r"([−-]0\.\d{3}) s floor", sentence)
            if not quotes:
                continue
            found.append(rel)
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


# ─── the real-footage half ───────────────────────────────────────────────────────────────────────
def _footage_root() -> str | None:
    root = os.environ.get("PACER_MEASURED_FIGURES_DIR", "").strip()
    return os.path.expanduser(root) if root else None


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
    if not root:
        print("skip test_the_coaching_tables_match_the_footage (set PACER_MEASURED_FIGURES_DIR)")
        return
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
    if not root:
        print("skip test_the_floor_table_matches_the_footage (set PACER_MEASURED_FIGURES_DIR)")
        return
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
    m = _need(r"against end-of-lap values of \+(\d\.\d\d) … \+(\d\.\d\d) s", text, "the end-of-lap range")
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
    times, admitted = np.asarray(sb.times, float), np.asarray(sb.admitted, bool)
    lap_times = np.asarray([s.lap_time(i) for i in sb.lap_ids], float)
    ideal, best = float(s.ideal_total()), float(s.lap_time(s.best_lap_id()))

    def ideal_of(t, a):
        m = np.where(a, t, np.inf).min(axis=0)
        return float(np.where(np.isinf(m), t.min(axis=0), m).sum())

    assert abs(ideal_of(times, admitted) - ideal) < 1e-9, "the stand-in no longer reproduces the ideal"
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
        moves.append(abs(ideal_of(times[keep], admitted[keep]) - ideal))
    sd = math.sqrt(sum(float(np.var(c)) for c in cols))
    return {"laps": len(sb.lap_ids), "ideal": round(ideal, 3), "best": round(best, 3),
            "lo": round(sum(float(c.min()) for c in cols), 3), "hi": round(sum(float(c.max()) for c in cols), 3),
            "sd": round(sd, 3), "ratio": round(sd / float(np.std(lap_times, ddof=1)), 2),
            "pct": float(cdf[np.searchsorted(x, best + 1e-9) - 1]) * 100,
            "dot_lo": round(dots[0], 3), "dot_hi": round(dots[-1], 3), "left": sum(d <= best for d in dots),
            "jack_max": max(moves), "jack_n": sum(m > 1e-9 for m in moves)}


def test_the_refusal_record_matches_the_footage():
    root = _footage_root()
    if not root:
        print("skip test_the_refusal_record_matches_the_footage (set PACER_MEASURED_FIGURES_DIR)")
        return
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


def _run_all():
    test_the_evidence_prose_is_its_table_s_arithmetic()
    test_the_reach_line_and_the_spread_gate_are_the_table_s()
    test_the_theme_table_is_the_evidence_table_s_arithmetic()
    test_the_theme_prose_follows_the_table_and_THEME_SHARE()
    test_every_quote_of_the_coaching_figures_is_coaching_py_s()
    test_the_floor_table_is_consistent_with_its_own_definitions()
    test_every_quote_of_the_floor_is_a_row_of_the_table()
    test_the_refusal_record_s_verdict_is_derived_from_its_table()
    test_the_coaching_tables_match_the_footage()
    test_the_floor_table_matches_the_footage()
    test_the_refusal_record_matches_the_footage()
    print("\nmeasured-figures checks passed")


if __name__ == "__main__":
    sys.exit(_run_all())
