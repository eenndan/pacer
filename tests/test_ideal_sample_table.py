"""THE IDEAL LAP'S MEASURED TABLE IS A PUBLISHED NUMBER, AND THIS IS WHAT CHECKS IT.

WHY THIS FILE EXISTS. `corner_model.IdealSample` carries a measured table — the ideal lap over
random subsets of five real recordings — and a dozen other surfaces quote a figure out of it: the
README, both Stats tooltips, the hero chip, two Library header hovers, `Session.ideal_total`, the
laps.csv writer, the landing page and its screenshot. #228 moved the app's ideal on D24's three
chapters by +0.218 s and every one of those numbers became false in the same instant. Nothing went
red. The D24 golden gate SAW the value move — it fingerprints `theoretical_best` — and had no way
to know that eighteen files quoted it. After #300 warped every lap it happened again: both D24
`all` cells moved (66.781 → 66.709, 67.831 → 67.403). Check 5 below catches that, but only when
someone runs it against the footage, and the text checks had no way to see the move.

WHAT CAN BE CHECKED WITHOUT FOOTAGE, AND WHAT CANNOT:

  1. THE TABLE IS INTERNALLY CONSISTENT. Every row falls monotonically across its rungs, which is
     the claim the shipping tooltips make in prose ("it keeps falling — there is no floor it
     settles on"); and the rate column is RECOMPUTED from the row's own two endpoint cells, so a
     reader can check it with a calculator and nobody can quietly change what it means.
  2. EVERY RATE QUOTED IN THE TREE IS ONE THE TABLE PUBLISHES. The family is enumerated BY
     SEARCH, not by a list a later edit can fall off: every tracked file whose prose says "per
     doubling of lap count" is scanned. The standing lesson in this repo is that sweeps miss
     whole surface families — a guard with the family hard-coded would miss the twelfth surface
     exactly the way the sweep did.
  3. EVERY "X s OVER 5 LAPS, Y s OVER N" PAIR IS A ROW'S OWN TWO CELLS, found by search.
  4. EVERY D24 GAP QUOTED IN THE TREE IS THE ONE THE DOCSTRING PUBLISHES — "−G s at 5 laps",
     "G s gap over 65", "G s on D24 one chapter and G s on three", the screenshot alt text's
     "1:SS.mmm theoretical best over N laps, −G s on the table" — found by search.
     Checks 1-4 read text — no Qt, no pacer, no numpy — and run in CI. They prove that every
     surface agrees with the TABLE. They cannot prove that the table agrees with the APP, and
     they cannot see a screenshot's pixels: an alt text that matches the table beside a PNG
     that does not is invisible to them (regenerate with studio/dev/media_capture.py).
  5. THE TABLE IS STILL TRUE OF THE APP. Opt-in, because it needs a recording CI does not have:
     set `PACER_IDEAL_TABLE_MP4` to a comma-separated chapter list. Without it this check is
     reported SKIPPED by name — it is its own CTest registration, `footage.<name>`, and not part of
     this file's ordinary run or count (tests/_footage.py). The check matches the
     recording to the row by its clean-lap count, asserts the `all` cell IS
     `Session.ideal_total()` to the millisecond, and then RE-RUNS THE TABLE'S STATED METHOD
     (20,000 random subsets per rung, partition held) over the app's own per-lap segment matrix.
     Every rung cell must come out within Monte-Carlo error. So must the prose figures the
     docstring publishes beside the row: the best lap's own rate, and on D24 and SD_30_08 the
     gap at both ends, the per-doubling decrements and the top-rung rates. The recording is loaded
     AS THE APP OPENS IT — `Session.load`, then the start line saved beside it — because that is
     the number the app prints. When it fails it prints the re-measured row in the table's own
     syntax.

AND A CONTROL ON CHECK 5'S STAND-IN, which does run in CI. Re-measuring the table means re-running
the ideal lap over random subsets, which the app never computes — so check 5 carries a STAND-IN for
`CornerModel.segment_bests`' masking rule, and a stand-in that has drifted from the rule re-measures
nothing. It had drifted: #339 gave the model a second mask (a cell may donate only where it is
admitted AND resolved), fixed the twin stand-in in `test_measured_figures._recombination`, and this
file kept masking on `admitted` alone (H9). `test_the_stand_in_masks_as_the_app_does` drives the
REAL rule on `_synthetic`'s drift fixtures and ships deliberately-wrong shapes it has to classify.
It is the only check in this file that imports numpy and pacer.

‡ ROWS. A row not re-measured against the current app carries a ‡ and the docstring must say
what it means. #319 marked the Sandown and SD_30_08 rows because their footage was believed to be
elsewhere; it was on the same machine, and T13 re-measured all three. The SD_30_08 row turned out
not to measure laps at all: 12.862 s over 25 "laps" was 25 pieces of 13 s cut from a 46 s lap by
the loader's start line, which `load._fit_start_line` no longer chooses.

Run:  python tests/test_ideal_sample_table.py
      PACER_IDEAL_TABLE_MP4=~/Desktop/D24/GX010062.MP4,…/GX020062.MP4,…/GX030062.MP4 \\
          python tests/test_ideal_sample_table.py --footage test_the_table_still_matches_the_app
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOURCE = os.path.join(_REPO, "studio", "corner_model.py")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _footage  # noqa: E402  (stdlib only: checks 1-4 still import no Qt, pacer or numpy)

# The rung columns of the published table, in order. The first is the one every rate is measured
# FROM; "all" is the whole recording and carries its own lap count.
_RUNGS = (5, 10, 20, 40)

# The mark a row carries while it has NOT been re-measured against the current app.
_UNVERIFIED = "‡"


class Row:
    """One parsed table row: the rung cells (None where the recording is too short), the `all`
    cell with its lap count, the rate the table publishes for it, and whether it carries the ‡
    that says it was not re-measured against the current app."""

    def __init__(self, name: str, cells: list[float | None], all_s: float, all_n: int, rate: float,
                 verified: bool = True):
        self.name, self.cells, self.all_s, self.all_n, self.rate = name, cells, all_s, all_n, rate
        self.verified = verified

    @property
    def measured_rate(self) -> float:
        """The rate the row's own two ends imply — the table's stated definition, recomputed."""
        return (self.cells[0] - self.all_s) / math.log2(self.all_n / _RUNGS[0])

    def __repr__(self) -> str:
        mark = "" if self.verified else f" {_UNVERIFIED}"
        return f"<{self.name}{mark} {self.cells} all={self.all_s} ({self.all_n}) rate={self.rate}>"


def _source_text() -> str:
    return open(_SOURCE, encoding="utf-8").read()


def _docstring_flat() -> str:
    """corner_model.py with every newline-and-indent collapsed to one space, so a published figure
    wrapped across two docstring lines is still one phrase to the regexes below."""
    return re.sub(r"\n\s*", " ", _source_text())


def _rows() -> list[Row]:
    """Parse the markdown table out of IdealSample's docstring. Reads the FILE, not the imported
    docstring, so this check costs no numpy/Qt import and sees exactly what a reader sees."""
    text = _source_text()
    start = text.index("class IdealSample")
    body = text[start:text.index("| SD_30_08", start) + 400]
    out: list[Row] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|--") or "recording" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(_RUNGS) + 3:          # name + rungs + all + rate
            continue
        verified = _UNVERIFIED not in cells[0]
        name = cells[0].replace(_UNVERIFIED, "").strip()
        rungs: list[float | None] = [None if c == "—" else float(c) for c in cells[1:1 + len(_RUNGS)]]
        m = re.fullmatch(r"([\d.]+) \((\d+)\)", cells[-2])
        assert m, f"the `all` cell must read `<seconds> (<laps>)`, got {cells[-2]!r}"
        rate = re.fullmatch(r"([\d.]+) s", cells[-1])
        assert rate, f"the rate cell must read `<seconds> s`, got {cells[-1]!r}"
        out.append(Row(name, rungs, float(m.group(1)), int(m.group(2)), float(rate.group(1)),
                       verified))
    assert len(out) == 5, f"expected the five measured recordings, parsed {out!r}"
    if not all(r.verified for r in out):
        # A mark nobody explains is a mark nobody reads.
        assert f"{_UNVERIFIED} ROWS ARE NOT RE-MEASURED" in _docstring_flat(), (
            f"the table marks rows {_UNVERIFIED} but IdealSample no longer says what that means")
    return out


def _row(prefix: str) -> Row:
    return next(r for r in _rows() if r.name.startswith(prefix))


def test_the_table_only_ever_falls():
    """THE CLAIM EVERY IDEAL-LAP TOOLTIP MAKES, checked against the table it is drawn from: the
    ideal falls with every rung and keeps falling to the whole recording. If a future
    re-measurement ever shows a plateau, the copy has to change, and this fails first.

    Also the sample itself: the `all` column must hold MORE laps than the last quoted rung, or the
    final drop is being read off a rung the recording never reached."""
    for row in _rows():
        quoted = [(n, v) for n, v in zip(_RUNGS, row.cells, strict=True) if v is not None]
        assert quoted[0][0] == _RUNGS[0], row
        for (n_a, a), (n_b, b) in zip(quoted, quoted[1:], strict=False):
            assert b < a, f"{row.name}: {n_b} laps ({b}) is not below {n_a} laps ({a})"
        last_n, last_v = quoted[-1]
        assert row.all_s < last_v, (
            f"{row.name}: the whole recording ({row.all_s}) is not below its last rung ({last_v})")
        assert row.all_n > last_n, (
            f"{row.name}: `all` is {row.all_n} laps, not more than the {last_n}-lap rung")
    print("test_the_table_only_ever_falls OK")


def test_the_rate_column_is_the_row_s_own_arithmetic():
    """THE RATE COLUMN IS RECOMPUTABLE, which is the difference between a published measurement
    and a published assertion. The table states its definition — (5-lap cell − `all` cell) ÷
    log2(laps ÷ 5) — and this is that sum, done again.

    It is also the check that pins the DEFINITION. The column used to be the last rung alone
    (20 → 21 laps on three of these rows), and nothing said so, so a reader could not tell the two
    apart and a later editor could swap them back without anybody noticing."""
    for row in _rows():
        assert abs(row.measured_rate - row.rate) <= 0.001, (
            f"{row.name}: the table publishes {row.rate:.3f} s per doubling, its own cells give "
            f"{row.measured_rate:.3f} — ({row.cells[0]} − {row.all_s}) / log2({row.all_n}/5)")
    print("test_the_rate_column_is_the_row_s_own_arithmetic OK")


def _tracked_files() -> list[str]:
    """Every tracked text file, from git — the scan's enumeration. `git ls-files` rather than a
    hand-kept list, for the same reason the scan exists at all."""
    out = subprocess.run(["git", "-C", _REPO, "ls-files"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    keep = (".py", ".md", ".html", ".txt", ".json", ".toml", ".cmake", "CMakeLists.txt")
    return [f for f in out.stdout.split() if f.endswith(keep)]


def _scanned_texts():
    """(path, flattened text) for every tracked file a quotation could live in. Skips this file
    (it prints the numbers it is checking) and CHANGELOG.md, whose released sections record what
    was measured AT that release — history, not a live claim. A changelog that is edited when a
    later measurement moves is not a changelog."""
    for rel in _tracked_files():
        if rel.endswith(os.path.basename(__file__)) or rel == "CHANGELOG.md":
            continue
        yield rel, _flatten(open(os.path.join(_REPO, rel), encoding="utf-8", errors="ignore").read())


# The phrase every quotation of this table uses, in either of the two casings the tree has. A rate
# "per doubling" of something ELSE is not a quotation of it — `MAX_DONOR_SPAN_DEV`'s arbiter reads
# 0.623 s per doubling on a recording this table does not carry, and must not be swept in here.
_QUOTES = re.compile(r"per doubling of lap count", re.IGNORECASE)

# T16. A ‡ row's figures are not current, so wherever a reader meets one WITHOUT this table beside
# it (an in-app string, README.md, docs/), the sentence has to date itself before #335. The
# best-lap control is exempt: it is lap times, which corner matching does not move. The one
# recording left to test that on, SD_30_08, re-measured 0.119 s per doubling on 2026-09-19, as
# published.
import _stale


def _stale_ideal_figures() -> set[str]:
    """Every figure the scans below read that comes off a ‡ row: that row's rate, the range across
    the rates when either end is ‡, the D24 3-chapter decrements and gaps, and the top-rung ideal
    rates of D24 1 chapter and SD_30_08."""
    rows = _rows()
    out = set()
    for r in rows:
        if not r.verified:
            out |= {f"{r.rate:.3f}", f"{r.rate:.2f}"}
    lo, hi = min(rows, key=lambda r: r.rate), max(rows, key=lambda r: r.rate)
    if not (lo.verified and hi.verified):
        out |= {f"{lo.rate:.2f}", f"{hi.rate:.2f}", f"{lo.rate:.3f}", f"{hi.rate:.3f}"}
    if not _row("D24 3").verified:
        out |= {r for _a, _b, r in _decrements()} | _gap_pair()
    if not _row("D24 1").verified:
        out.add(_top_rung()[0])
    if not _row("SD_30_08").verified:
        out.add(_sd_last_doubling()[2])
    return out


def _flatten(text: str) -> str:
    """One file as one line: a newline (with its comment marks and indent) becomes a space, and so
    does a Python string concatenation across one — a tooltip wrapped as `"… per doubling "` then
    `"of lap count …"` is ONE sentence to the reader and must be one here. Missing that is how this
    scan first ran, and it missed exactly what the sweep it guards against misses: the two surfaces
    whose phrase straddled a line break (the Stats tooltip, `Session.ideal_total`) were not scanned
    at all, and the scan said nothing."""
    # The spaces on BOTH sides of the join go too: a wrapped literal ends `… per doubling "` and
    # rejoining it naively leaves a double space, which is a phrase the search no longer matches —
    # a scan that silently covers nothing looks exactly like a scan that passes.
    return re.sub(r"""[ \t]*["']?[ \t]*\n[ \t#*]*["']?[ \t]*""", " ", text)


def _sentences(text: str) -> list[str]:
    """Flattened, then split where a sentence ends."""
    return re.split(r"(?<=[.!?])\s+", _flatten(text))


class Gaps:
    """The D24 ideal-to-best gaps the docstring publishes, parsed rather than pinned: the gap on
    three chapters at 5 laps and over the whole recording (`n3` laps), and the gap on one chapter
    at the fitted line. Every surface that quotes a D24 gap has to quote one of these."""

    def __init__(self) -> None:
        text = _docstring_flat()
        m = re.search(r"D24 3 chapters reads −(\d\.\d\d) s at 5 laps and −(\d\.\d\d) s at (\d+)",
                      text)
        assert m, "IdealSample no longer states the D24 3-chapter gap at both ends of its range"
        self.at5_3ch, self.all_3ch, self.n3 = m.group(1), m.group(2), int(m.group(3))
        m = re.search(r"on D24 1 chapter the gap at the fitted line is (\d\.\d\d) s over its (\d+) "
                      r"laps", text)
        assert m, "IdealSample no longer states the D24 1-chapter gap at the fitted line"
        self.all_1ch, self.n1 = m.group(1), int(m.group(2))
        assert self.n3 == _row("D24 3").all_n and self.n1 == _row("D24 1").all_n, (
            "the gap sentences name lap counts the table's D24 rows do not carry")


def _gap_pair() -> set[str]:
    """The gap the app headlines at both ends of the table's own range ("−G s at 5 laps and −G s
    at 65"), parsed from the same docstring. It is the same measurement stated as the subtraction
    a reader actually sees on the tile."""
    g = Gaps()
    return {g.at5_3ch, g.all_3ch}


def _control_pair() -> tuple[list[float], list[float]]:
    """The best-lap control rates and the ideal rates they are set against, row for row, from the
    same docstring paragraph. Parsed rather than pinned so the two halves of one measurement cannot
    drift apart."""
    m = re.search(r"row for row, it falls\s+((?:[\d.]+\s*/\s*)+[\d.]+) s per doubling on those "
                  r"five, against the ideal's\s+((?:[\d.]+\s*/\s*)+[\d.]+)", _docstring_flat())
    assert m, "IdealSample no longer states the best-lap control rates row for row"
    return ([float(v) for v in m.group(1).split("/")], [float(v) for v in m.group(2).split("/")])


def _control_rates() -> list[float]:
    return _control_pair()[0]


def test_the_best_lap_control_is_set_against_the_table_s_own_rates():
    """"LESS than the ideal on all five", derived. The paragraph pairs each recording's best-lap
    rate with the ideal's; the ideal's half must BE the table's rate column in the table's order,
    and each best-lap rate must sit below its own row's, or the sentence's verdict is false. The
    lists used to be two independently sorted rows of numbers, so no reader could tell which
    recording a best-lap rate belonged to."""
    best, ideal = _control_pair()
    rows = _rows()
    assert ideal == [r.rate for r in rows], (
        f"the paragraph sets the best lap against {ideal}; the table's rate column is "
        f"{[r.rate for r in rows]} — the table is the source")
    assert len(best) == len(rows), (best, rows)
    over = [(r.name, b, r.rate) for r, b in zip(rows, best, strict=True) if b >= r.rate]
    assert "LESS than the ideal on all five" in _docstring_flat() and not over, (
        f"the best lap falls at least as fast as the ideal on {over}: the verdict has to change")
    print(f"test_the_best_lap_control_is_set_against_the_table_s_own_rates OK ({best} vs {ideal})")


def _decrements() -> list[tuple[int, int, str]]:
    """The shape sentence's per-rung decrements on D24 3 chapters — `(from N, to N, rate)` — parsed
    from the docstring. They are measurements of the same experiment at intermediate N, so no text
    check can derive them from the five cells; the real-media half re-measures them."""
    text = _docstring_flat()
    start = text.index("Re-measured it shrinks")
    span = text[start:text.index("A thirteenfold", start)]
    found = [(int(a), int(b), r)
             for r, a, b in re.findall(r"(\d\.\d{3})(?: s per doubling)? over (\d+) → (\d+)", span)]
    assert len(found) >= 3, f"IdealSample's shape sentence parsed to {found!r}"
    return found


def _top_rung() -> tuple[str, str]:
    """D24 1 chapter's 20 → 21-lap rate, ideal and best, as the docstring states them."""
    m = re.search(r"at the top rung \(20 → 21 laps\) D24 1 chapter reads (\d\.\d{3}) ideal against "
                  r"(\d\.\d{3}) best", _docstring_flat())
    assert m, "IdealSample no longer states D24 1 chapter's top-rung rates"
    return m.group(1), m.group(2)


def _sd_last_doubling() -> tuple[str, str, str, str]:
    """SD_30_08's last doubling, as the docstring states it twice: on the 25 pieces it was once
    measured on (best, ideal — history, T13) and on its real laps' top rung (ideal, best), which
    the real-media half re-measures."""
    text = _docstring_flat()
    m = re.search(r"the best lap moved more than the ideal \((\d\.\d{3}) s against (\d\.\d{3}) over "
                  r"20 → 25 laps\)", text)
    assert m, "IdealSample no longer records what SD_30_08's pieces measured"
    now = re.search(r"on its (\d+) real laps the top rung \(20 → \1 laps\) reads (\d\.\d{3}) ideal "
                    r"against (\d\.\d{3}) best", text)
    assert now, "IdealSample no longer states SD_30_08's top-rung rates"
    assert int(now.group(1)) == _row("SD_30_08").all_n, "the top-rung sentence names another lap count"
    return m.group(1), m.group(2), now.group(2), now.group(3)


def test_every_surface_quotes_this_table():
    """THE SURFACE FAMILY, ENUMERATED BY SEARCH. Every sentence in the repo that says "per
    doubling of lap count" is a quotation of this table, and every rate it prints must be one the
    table publishes — for the ideal (the rate column) or for the best-lap control the docstring
    carries beside it.

    WHY A SEARCH AND NOT A LIST OF PATHS: re-measuring these numbers touched eighteen files, and a
    grep for the headline value — the number that actually moved — finds five of them. The other
    thirteen quote a rate, a range or a gap DERIVED from it, so the thing to search for is the
    phrase every one of them shares, not the number one of them prints."""
    rows = _rows()
    ideal = [r.rate for r in rows]
    control = _control_rates()
    ok = set()
    ranges = set()
    for v in list(ideal) + list(control):
        ok.add(f"{v:.3f}")
        ok.add(f"{v:.2f}")
    for lo, hi in ((min(ideal), max(ideal)), (min(control), max(control))):
        ok.update({f"{lo:.2f}", f"{hi:.2f}", f"{lo:.3f}", f"{hi:.3f}"})
        ranges.update({(f"{lo:.2f}", f"{hi:.2f}"), (f"{lo:.3f}", f"{hi:.3f}")})
    # The per-rung rates the shape sentence quotes are measurements of the same experiment at
    # intermediate N. They used to be a hand-typed set here, which is a second copy of the
    # docstring that goes stale with it; they are parsed from the docstring now, and the
    # real-media half is what checks them against the app.
    ok.update(r for _a, _b, r in _decrements())
    ok.update(_top_rung())
    ok.update(_sd_last_doubling()[2:])
    # The HEADLINE GAP at both ends of the same experiment — several of these sentences carry the
    # rate and the gap in one breath ("falls X per doubling ... reads −G s at 5 laps"), and the
    # gap is published by the same docstring, so it is read from there rather than waved through.
    ok.update(_gap_pair())

    stale_figures = _stale_ideal_figures()
    unmarked = []
    seen = 0
    for rel, text in _scanned_texts():
        if not _QUOTES.search(text):
            continue
        for sentence in _sentences(text):
            if not _QUOTES.search(sentence):
                continue
            seen += 1
            stale = [n for n in re.findall(r"(?<![\d.])\d\.\d{2,3}(?![\d])", sentence) if n in stale_figures]
            if stale:
                # The number WITH the phrase is what locates the quote: the bare phrase also sits in
                # the best-lap tooltip, whose rates no corner-matching change can move.
                n = re.escape(stale[0])
                m = (re.search(n + r".{0,80}?per doubling of lap count", sentence, re.I)
                     or re.search(r"per doubling of lap count.{0,80}?" + n, sentence, re.I))
                p = _stale.unmarked(rel, m.group(0) if m else stale[0], sentence,
                                    "a rate off IdealSample's ‡ rows")
                if p:
                    unmarked.append(p)
            # A bare `0.xx` / `0.xxx`, not the tail of a lap time: 67.957 is a cell, not a rate.
            for num in re.findall(r"(?<![\d.])\d\.\d{2,3}(?![\d])", sentence):
                assert num in ok, (
                    f"{rel} quotes {num} s per doubling of lap count; corner_model.IdealSample "
                    f"publishes {sorted(ok)}. One of the two is stale — the table is the source.")
            # A RANGE has to be a range the table publishes, end to end. Each end checked alone is
            # not enough: when the D24 rows moved, the shipping tooltips' "0.07–0.33 s" went stale
            # (the table's top is 0.377 now) and still passed the per-number test above, because
            # 0.326 — a different row's rate — also rounds to 0.33.
            for lo, hi in re.findall(r"(?<![\d.])(\d\.\d{2,3})\s*(?:–|-|…|to)\s*(\d\.\d{2,3})"
                                     r"(?![\d])", sentence):
                assert (lo, hi) in ranges, (
                    f"{rel} quotes the range {lo}–{hi} s per doubling of lap count; the table's "
                    f"ranges are {sorted(ranges)} — the table is the source.")
    assert seen >= 8, f"the scan found only {seen} sentences; it has gone vacuous"
    assert not unmarked, "\n  ".join(["stale IdealSample rates presented as current:"] + unmarked)
    print(f"test_every_surface_quotes_this_table OK ({seen} sentences)")


# "68.016 s over 5 laps, 66.781 s over 65" and "68.016 -> 66.781 s between 5 and 65 laps": a row's
# first and last cells stated as one sentence, because that pair IS the disclosure's argument.
_PAIR_FORMS = (
    re.compile(r"(?<![\d.])(\d{2}\.\d{3}) s over 5 laps(?:,| and) (\d{2}\.\d{3}) s over (\d+)"),
    re.compile(r"(?<![\d.])(\d{2}\.\d{3}) -> (\d{2}\.\d{3}) s between 5 and (\d+) laps"),
)


def test_every_five_lap_pair_is_the_table_s():
    """THE TWO CELLS THE PUBLIC PAGES QUOTE, wherever they are quoted. The README and the laps.csv
    writer print the D24 three-chapter row's first and last cells as one sentence, and so do a
    test docstring and a CMake comment. #300 left all four stale, and the check that stood here
    looked at only the first two. So the family is FOUND BY SEARCH now, and each hit is read
    against the row with its lap count."""
    by_n = {r.all_n: r for r in _rows()}
    hits: dict[str, int] = {}
    unmarked = []
    for rel, text in _scanned_texts():
        flat = re.sub(r"\s+", " ", text)
        for form in _PAIR_FORMS:
            for m in form.finditer(flat):
                five, whole, n = m.groups()
                row = by_n.get(int(n))
                assert row, f"{rel} quotes the ideal over {n} laps; no table row has {n}"
                want = (f"{row.cells[0]:.3f}", f"{row.all_s:.3f}")
                assert (five, whole) == want, (
                    f"{rel} says {five} s over 5 laps and {whole} s over {n}; the table's "
                    f"{row.name} row says {want} — the table is the source")
                hits[rel] = hits.get(rel, 0) + 1
                if not row.verified:
                    p = _stale.unmarked(rel, m.group(0), _stale.sentence_at(flat, m.start(), m.end()),
                                        f"IdealSample's {row.name} {_UNVERIFIED} row")
                    if p:
                        unmarked.append(p)
        # "… chapter 1 (21 laps) and … chapters 1–3 (65 laps) are 0.69 s apart": two rows' `all`
        # cells, subtracted — the Library's Ideal-lap header hover makes its whole case with it.
        for m in re.finditer(r"\((\d+) laps\) and [^()]{1,40}\((\d+) laps\) (?:are|were) "
                             r"(\d\.\d\d) s apart", flat):
            n_a, n_b, apart = m.groups()
            a, b = by_n.get(int(n_a)), by_n.get(int(n_b))
            assert a and b, f"{rel} compares {n_a} laps with {n_b}; the table lacks one of them"
            # Anywhere, not only where a reader sees it: this comparison is a claim about the app.
            sentence = _stale.sentence_at(flat, m.start(), m.end())
            unit = _stale.presented_unit(rel, m.group(0), sentence) or sentence
            assert (a.verified and b.verified) or _stale.QUOTE_MARK.search(unit), (
                f"{rel} quotes {a.name} against {b.name} as current, and one of them is "
                f"{_UNVERIFIED} — not re-measured. Say it was measured before #335.")
            assert apart == f"{abs(a.all_s - b.all_s):.2f}", (
                f"{rel} says {n_a} and {n_b} laps are {apart} s apart; the table's `all` cells are "
                f"{abs(a.all_s - b.all_s):.2f} s apart")
            hits[rel] = hits.get(rel, 0) + 1
    for rel in ("README.md", os.path.join("studio", "export_data.py")):
        assert rel in hits, (
            f"{rel} no longer states the ideal over 5 laps against the whole recording")
    assert len(hits) >= 4, f"the pair scan found only {sorted(hits)}; it has gone vacuous"
    assert not unmarked, "\n  ".join(["stale IdealSample cells presented as current:"] + unmarked)
    print(f"test_every_five_lap_pair_is_the_table_s OK ({sum(hits.values())} pairs in "
          f"{len(hits)} files)")


def test_every_d24_gap_is_the_docstring_s():
    """THE GAP A READER SEES ON THE TILE, wherever the tree quotes it. After #300 the D24 gaps
    had moved from 1.42 to 1.49 s (three chapters) and from 0.94 to 1.37 s (one chapter). The
    sentences quoting them mostly do not say "per doubling of lap count", so the rate scan passed
    over them. Each form here is anchored on its own phrase, and every number is read
    against the docstring's published gaps, not against a copy typed into this file."""
    g = Gaps()
    all_3ch = _row("D24 3")
    at_n = re.compile(r"(?<![\d.])(\d\.\d\d) s`?\s*(?:gap\s+)?(?:at|over|from)\s+"
                      rf"(5\s+(?:clean\s+)?laps|{g.n3}(?![\d.]))")
    one_three = re.compile(r"(?<![\d.])(\d\.\d\d) s on D24 one chapter(?:,| and) [+−-]?"
                           r"(\d\.\d\d) s on three")
    alt = re.compile(r"(?:1:(\d\d\.\d{3}) theoretical best over (\d+) laps|theoretical best "
                     r"1:(\d\d\.\d{3}) over (\d+) laps), [−-](\d\.\d\d) s on the table")
    # T16: every gap here is a D24 row's; while those rows are ‡, a reader who meets one without the
    # table (an in-app string, README.md, docs/) has to be told it predates #335.
    d24_stale = not (_row("D24 1").verified and all_3ch.verified)
    unmarked = []

    def presented(rel: str, flat: str, m: re.Match) -> None:
        if d24_stale:
            p = _stale.unmarked(rel, m.group(0), _stale.sentence_at(flat, m.start(), m.end()),
                                f"a D24 gap off IdealSample's {_UNVERIFIED} rows")
            if p:
                unmarked.append(p)

    seen = 0
    for rel, text in _scanned_texts():
        flat = re.sub(r"\s+", " ", text)
        for m in at_n.finditer(flat):
            value, where = m.groups()
            want = g.at5_3ch if where.startswith("5") else g.all_3ch
            assert value == want, (
                f"{rel} quotes a {value} s gap at {where}; IdealSample publishes {want} "
                f"(D24 3 chapters) — the docstring is the source")
            presented(rel, flat, m)
            seen += 1
        for m in one_three.finditer(flat):
            one, three = m.groups()
            assert (one, three) == (g.all_1ch, g.all_3ch), (
                f"{rel} quotes {one} s on D24 one chapter and {three} s on three; IdealSample "
                f"publishes {g.all_1ch} and {g.all_3ch}")
            presented(rel, flat, m)
            seen += 1
        for m in alt.finditer(flat):
            a_s, a_n, b_s, b_n, gap = m.groups()
            secs, n = (a_s, a_n) if a_s else (b_s, b_n)
            assert int(n) == all_3ch.all_n, f"{rel}: a screenshot of {n} laps is not a D24 row"
            assert (f"{60 + float(secs):.3f}", gap) == (f"{all_3ch.all_s:.3f}", g.all_3ch), (
                f"{rel} describes a screenshot reading 1:{secs} and −{gap} s; the table reads "
                f"{all_3ch.all_s:.3f} s and IdealSample −{g.all_3ch} s. Regenerate the image "
                "(studio/dev/media_capture.py) and its alt text together.")
            presented(rel, flat, m)
            seen += 1
    assert seen >= 12, f"the gap scan found only {seen} quotations; it has gone vacuous"
    assert not unmarked, "\n  ".join(["stale D24 gaps presented as current:"] + unmarked)
    print(f"test_every_d24_gap_is_the_docstring_s OK ({seen} quotations)")


# ─── the real-media half ─────────────────────────────────────────────────────────────────────────
_DRAWS = 20_000
_SEED = 20260917


def _ideal(times, may_donate, admitted, axis: int = 0):
    """THE APP'S OWN MASKING RULE, over one lap set (`axis=0`) or a batch of them (`axis=1`).

    C5 (#339): a cell may donate to the composite minimum only where it is BOTH admitted — its
    projected span is comparable to its expected one, MAX_DONOR_SPAN_DEV — and RESOLVED, its two
    boundaries matched on track rather than interpolated. `CornerModel.segment_bests` carries both
    masks and takes the minimum over their conjunction, and it falls back in TWO stages: a segment
    no lap may donate drops to that segment's ADMITTED cells, and only a segment no lap is
    admitted on drops to every lap. This stand-in re-runs the composite over subsets the app never
    computes, so it has to mask exactly as the model does, fallbacks included; the same shape as
    `test_measured_figures._recombination`, which #339 fixed for the #272 record.

    Masking on `admitted` alone — what this file did until H9 — is not a weaker version of the
    rule, it is the pre-#339 rule. Over the full set the caller's own guard would catch it (on the
    D24 0060 pair it reads 65.637 s where the app reads 65.864). Over a SUBSET nothing catches it:
    on SD_30_08 the two rules pick different donors in 12 of 20,000 five-lap draws, by up to
    0.223 s on one draw, which moves that rung's published cell by 0.00002 s — inside the 0.005 s
    tolerance below, so the row is reported current while being re-measured by a rule the app
    dropped. `test_the_stand_in_masks_as_the_app_does` is the control that fails when it does."""
    import numpy as np

    empty = ~may_donate.any(axis=axis)
    may = np.where(np.expand_dims(empty, axis), admitted, may_donate)
    m = np.where(may, times, np.inf).min(axis=axis)
    return np.where(np.isinf(m), times.min(axis=axis), m).sum(axis=-1)


def _subset_means(times, may_donate, admitted, lap_times, n: int) -> tuple[float, float]:
    """The table's stated method at one rung: the mean IDEAL and the mean BEST LAP over `_DRAWS`
    random `n`-lap subsets of the clean laps, the partition and every cell's admission held (the
    admission is per cell — a lap's own projected span against its own expected span — so holding
    the partition holds it). The two subset-dependent rules, a segment no subset lap may donate on
    falling back to its admitted cells and then to the whole column, are `CornerModel.segment_bests`'
    own and live in `_ideal`; the caller proves this reproduces the app over the full set before it
    varies anything. Seeded per rung, so a cell does not depend on which other rungs were drawn
    first."""
    import numpy as np

    rng = np.random.default_rng((_SEED, n))
    laps = times.shape[0]
    ideal_sum = best_sum = 0.0
    left = _DRAWS
    while left:
        k = min(2_000, left)
        idx = np.argsort(rng.random((k, laps)), axis=1)[:, :n]
        ideal_sum += float(_ideal(times[idx], may_donate[idx], admitted[idx], axis=1).sum())
        best_sum += float(lap_times[idx].min(axis=1).sum())
        left -= k
    return ideal_sum / _DRAWS, best_sum / _DRAWS


def _full_set_ideal(times, may_donate, admitted) -> float:
    """The composite over every lap — what `Session.ideal_total()` prints, by the app's rule."""
    return float(_ideal(times, may_donate, admitted))


def _pre_339_ideal(times, admitted):
    """THE DEFECT, KEPT AS A SHAPE. `admitted` alone, the rule `_full_set_ideal` and
    `_subset_means` used until H9. Never called by the check — only by the control below, which
    has to be able to tell it apart from the app's rule or it is not controlling anything."""
    import numpy as np

    m = np.where(admitted, times, np.inf).min(axis=0)
    return float(np.where(np.isinf(m), times.min(axis=0), m).sum())


# Each shape is (times, admitted, resolved, the composite worked out by hand). Between them they
# reach all four branches of `CornerModel.segment_bests`' rule, including both fallback stages.
_MASK_SHAPES = {
    "an unresolved cell is the quickest": (
        [[1.0, 5.0], [2.0, 4.0]], [[1, 1], [1, 1]], [[0, 1], [1, 1]], 2.0 + 4.0),
    "an unresolved cell wins one segment and an unadmitted cell another": (
        [[1.0, 5.0, 9.0], [2.0, 4.0, 8.0], [3.0, 6.0, 7.0]],
        [[1, 1, 1], [1, 1, 1], [1, 1, 0]], [[0, 1, 1], [1, 1, 1], [1, 1, 1]], 2.0 + 4.0 + 8.0),
    "an unadmitted cell is the quickest": (
        [[1.0, 5.0], [2.0, 4.0]], [[0, 1], [1, 1]], [[1, 1], [1, 1]], 2.0 + 4.0),
    "a segment nothing may donate falls back to its ADMITTED cells, not to every lap": (
        [[1.0, 5.0], [2.0, 4.0]], [[0, 1], [1, 1]], [[0, 1], [0, 1]], 2.0 + 4.0),
    "a segment nothing is admitted on falls back to every lap": (
        [[1.0, 5.0], [2.0, 4.0]], [[0, 1], [0, 1]], [[0, 1], [1, 1]], 1.0 + 4.0),
    "every cell resolved is the plain minimum": (
        [[1.0, 5.0], [2.0, 4.0]], [[1, 1], [1, 1]], [[1, 1], [1, 1]], 1.0 + 4.0),
}

# …and these are the shapes above that the pre-#339 rule gets WRONG. Pinned in both directions: a
# control that stops distinguishing the two rules passes for every shape at once, which is the
# failure mode a stand-in is most prone to (tests/test_temp_isolation.py's lesson).
_CAUGHT_BY_THE_CONTROL = {
    "an unresolved cell is the quickest",
    "an unresolved cell wins one segment and an unadmitted cell another",
}


def test_the_stand_in_masks_as_the_app_does():
    """THE CONTROL ON THE CHECK ABOVE, and it runs in CI without a recording.

    `test_the_table_still_matches_the_app` re-measures a published table by re-running the ideal
    lap over random subsets, which the app never computes — so it carries a STAND-IN for
    `CornerModel.segment_bests`' masking rule, and a stand-in that has drifted from the rule
    re-measures nothing. It had drifted: #339 gave the model a second mask (a cell may donate only
    where it is admitted AND resolved) and fixed the twin stand-in in
    `test_measured_figures._recombination`; this file's kept masking on `admitted` alone. Its own
    full-set guard would have caught that on a recording where an unresolved cell wins outright —
    the D24 0060 pair, 65.637 s against the app's 65.864 — but the per-rung cells, which is what
    the table publishes, had nothing behind them at all. On SD_30_08, the one recording of the five
    still on this machine, the two rules pick different donors in 12 of 20,000 five-lap draws (up
    to 0.223 s on a single draw) and the rung cell moves 0.00002 s — well inside the 0.005 s
    tolerance, so the check reported the row current while measuring it by a rule the app dropped.

    Two halves. The first drives the REAL rule: on `_synthetic`'s drift fixtures the stand-in must
    equal `CornerModel.segment_bests`' own `bests`, and the fixture must be one where that means
    something — drift_noise's quickest C1→C2 sits on an unmatched boundary and is refused, worth
    0.155 s, so a stand-in masking on `admitted` alone is 0.155 s light. The second ships
    deliberately-wrong shapes the stand-in has to classify, reaching the two fallback stages the
    fixtures do not, and pins exactly which of them the pre-#339 rule gets wrong."""
    import numpy as np

    sys.path.insert(0, _REPO)
    sys.path.insert(0, os.path.join(_REPO, "bindings", "pacer"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import _synthetic

    lines = []
    exercised = 0
    for label in ("drift_noise", "drift_median", "drift_band"):
        s = getattr(_synthetic, f"{label}_session")()
        sb = s.corners.segment_bests()
        assert sb is not None, f"{label} has no corner partition to composite on"
        times = np.asarray(sb.times, float)
        admitted = np.asarray(sb.admitted, bool)
        may_donate = admitted & np.asarray(sb.resolved, bool)
        app = float(np.sum(sb.bests))
        got = _full_set_ideal(times, may_donate, admitted)
        assert abs(got - app) < 1e-9, (
            f"{label}: the stand-in composites {got:.6f} s where CornerModel.segment_bests — the "
            f"rule it stands in for — composites {app:.6f} s. The stand-in has to BE the app "
            f"before the check varies anything, or every published cell it re-measures is a "
            f"measurement of a copy of the rule.")
        # The SUBSET path too, drawn at the full lap count so its answer is the same one: a fix
        # applied to `_full_set_ideal` alone would leave every published rung cell still wrong.
        lap_times = np.array([s.lap_time(i) for i in sb.lap_ids], float)
        mean_ideal, _best = _subset_means(times, may_donate, admitted, lap_times, times.shape[0])
        assert abs(mean_ideal - app) < 1e-9, (
            f"{label}: the subset path composites {mean_ideal:.6f} s over the whole lap set where "
            f"the model composites {app:.6f} — the rung cells do not use the app's rule")
        drift = app - _pre_339_ideal(times, admitted)
        exercised += drift > 1e-3
        lines.append(f"  {label}: {app:.6f} s, {drift:+.6f} s against the pre-#339 rule")
    assert exercised >= 2, (
        "no drift fixture still carries a cell that is admitted, unresolved and quickest, so this "
        "half of the control cannot tell the two rules apart:\n" + "\n".join(lines))

    caught = set()
    for label, (t, a, r, want) in _MASK_SHAPES.items():
        times = np.asarray(t, float)
        admitted = np.asarray(a, bool)
        may_donate = admitted & np.asarray(r, bool)
        got = _full_set_ideal(times, may_donate, admitted)
        assert abs(got - want) < 1e-9, (
            f"the stand-in composites {got} where {label!r} composites {want} by the model's rule")
        # …and through the batched path the rung cells run on, which has its own fallback code.
        batch = _ideal(np.stack([times, times]), np.stack([may_donate, may_donate]),
                       np.stack([admitted, admitted]), axis=1)
        assert [float(v) for v in batch] == [want, want], (
            f"{label!r}: the batched path gives {list(batch)}, the single one {want}")
        if abs(_pre_339_ideal(times, admitted) - want) > 1e-9:
            caught.add(label)
    assert caught == _CAUGHT_BY_THE_CONTROL, (
        f"the shapes the pre-#339 rule gets wrong are {sorted(caught)}, not "
        f"{sorted(_CAUGHT_BY_THE_CONTROL)} — a control that no longer separates the two rules "
        f"passes for every shape at once")
    print(f"test_the_stand_in_masks_as_the_app_does OK ({len(_MASK_SHAPES)} shapes, "
          f"{len(caught)} of them lost on the pre-#339 rule; real rule on 3 fixtures)\n"
          + "\n".join(lines))


def test_the_table_still_matches_the_app():
    """THE CHECK THAT WOULD HAVE CAUGHT IT, on real footage: load a recording, find the row with
    its clean-lap count, assert the row's `all` cell IS `Session.ideal_total()`, and re-measure
    everything else the docstring publishes about that recording by its own stated method.

    Opt-in via `PACER_IDEAL_TABLE_MP4` (comma-separated chapters, e.g. the three that make D24's
    65-lap recording). It cannot run in CI — the recordings are 11 GB each and are not committed —
    so there CTest reports it SKIPPED, by name: it is a FOOTAGE_CHECK (tests/_footage.py). It used
    to print a skip line and return, and this file then counted it among its passes. The
    dev-Desktop gate to run it with is the same one AGENTS.md already names for a core-math
    change: if the golden dump moved, this moved too.

    WHY THE RUNGS AND NOT ONLY `all`: when the D24 `all` cells went stale, their 5-lap cells had
    moved too, by −0.10 and −0.16 s. The version of this check that compared only `all` would have
    been satisfied by a hand-edit of that one cell, leaving the rest of the row, and the rate
    computed from it, false."""
    paths = _footage.recording_list(
        "PACER_IDEAL_TABLE_MP4", "a comma-separated chapter list of one of the recordings in the table")
    sys.path.insert(0, _REPO)
    # …and the bindings AHEAD of it: the repo root holds the C++ source folder `pacer/`, which
    # shadows the binding package as a namespace portion when nothing else on the path is a real
    # `pacer`. CTest injects this; a bare `python tests/…` run would otherwise die inside the
    # loader with "module 'pacer' has no attribute 'Laps'".
    sys.path.insert(0, os.path.join(_REPO, "bindings", "pacer"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import tempfile

    import numpy as np

    from studio import demo, focus, library, marks, prefs, session_record, track_db
    tmp = tempfile.mkdtemp(prefix="pacer-ideal-table-")
    for mod in (demo, focus, library, marks, prefs, session_record, track_db):
        mod._app_support_dir = lambda: tmp          # hermetic, like the golden dump
    from studio.session import Session

    s = Session.load(paths)
    # AS THE APP OPENS IT: `StudioWindow._on_session_loaded` applies the start line the owner saved
    # beside the recording before anything is drawn, through this same seam. Without it, SD_30_08
    # was measured on a line the owner never sees (T13). A recording with no saved line (D24 0062)
    # is unchanged by the call.
    s.restore_saved_timing_lines()
    sb = s.ideal_segment_bests()
    assert sb is not None, "no corner partition, so there is no ideal to check the table against"
    laps = s.ideal_sample().laps
    rows = [r for r in _rows() if r.all_n == laps]
    assert rows, (f"{laps} clean laps is not a row of the table "
                  f"({[(r.name, r.all_n) for r in _rows()]}) — point the variable at one of them")
    got = s.ideal_total()
    # Two recordings can share a lap count (Sandown chapter 1 and SD_30_08 both count 23 on the
    # loader's own lines), and taking the first would report one recording's row as the other's
    # stale figures. The row whose `all` cell is nearest is the recording's own.
    row = min(rows, key=lambda r: abs(r.all_s - got))
    times = np.asarray(sb.times, float)
    # BOTH of the model's masks, and both are needed: `admitted` is the span test alone, and it is
    # what a segment nothing may donate on falls back to (`_ideal`). C5 takes the minimum over the
    # conjunction. Masking on `admitted` alone here measured the pre-#339 rule (H9).
    admitted = np.asarray(sb.admitted, bool)
    may_donate = admitted & np.asarray(sb.resolved, bool)
    lap_times = np.array([s.lap_time(i) for i in sb.lap_ids], float)
    # The stand-in has to BE the app before anything is varied, or every cell below measures a
    # copy of the rule rather than the rule.
    assert abs(_full_set_ideal(times, may_donate, admitted) - got) < 1e-9, (
        "the subset minimum no longer reproduces Session.ideal_total() over every lap — "
        "CornerModel.segment_bests changed its rule and this check has to follow it")

    means = {n: _subset_means(times, may_donate, admitted, lap_times, n) for n in _RUNGS if n < laps}
    for n in (8, 15, 30, 50):              # the shape sentence's intermediate rungs
        if n < laps and n not in means:
            means[n] = _subset_means(times, may_donate, admitted, lap_times, n)
    means[laps] = (got, float(lap_times.min()))
    cells = [means[n][0] if n < laps else None for n in _RUNGS]
    rate = (means[5][0] - got) / math.log2(laps / 5)

    def dec(a: int, b: int, col: int = 0) -> float:
        return (means[a][col] - means[b][col]) / math.log2(b / a)

    measured = (f"| {row.name} | " + " | ".join("—" if c is None else f"{c:.3f}" for c in cells)
                + f" | {got:.3f} ({laps}) | {rate:.3f} s |")
    # The figures the docstring's prose quotes beside the row, from the same draws — printed on a
    # pass as well as a failure, so re-publishing them never needs a second, hand-rolled probe.
    prose = (f"best-lap rate 5 → {laps}: {dec(5, laps, 1):.3f} · gap at 5 laps "
             f"−{means[5][1] - means[5][0]:.3f} s, at {laps} −{means[laps][1] - got:.3f} s")
    if laps > 20:
        prose += f" · top rung 20 → {laps}: ideal {dec(20, laps):.3f}, best {dec(20, laps, 1):.3f}"
    prose += "".join(f" · {a} → {b}: {dec(a, b):.3f}" for a, b in ((5, 8), (8, 15), (20, 30), (50, laps))
                     if a < b and a in means and b in means)
    problems = []
    if abs(got - row.all_s) >= 0.001:
        problems.append(f"`all`: the table publishes {row.all_s:.3f} s, the app computes {got:.3f}")
    # 20,000 draws put the Monte-Carlo standard error at ≤ 0.0015 s on every D24 cell; 0.005 is
    # three of those plus the table's own rounding. When #300 left this table stale, the smallest
    # move on any D24 cell was 0.072 s.
    for n, pub, now in zip(_RUNGS, row.cells, cells, strict=True):
        if (pub is None) != (now is None):
            problems.append(f"{n} laps: the table has {pub}, the recording gives {now}")
        elif pub is not None and abs(pub - now) > 0.005:
            problems.append(f"{n} laps: the table publishes {pub:.3f} s, re-measured {now:.3f}")
    if row.name.startswith("D24 3"):
        g = Gaps()
        for label, pub, now in (("gap at 5 laps", g.at5_3ch, means[5][1] - means[5][0]),
                                (f"gap at {laps}", g.all_3ch, means[laps][1] - got)):
            if abs(float(pub) - now) > 0.01:
                problems.append(f"{label}: IdealSample publishes −{pub} s, re-measured −{now:.3f}")
        for a, b, pub in _decrements():
            if abs(float(pub) - dec(a, b)) > 0.01:
                problems.append(f"decrement {a} → {b}: published {pub}, re-measured "
                                f"{dec(a, b):.3f}")
    if row.name.startswith("D24 1"):
        g = Gaps()
        if abs(float(g.all_1ch) - (means[laps][1] - got)) > 0.005:
            problems.append(f"gap at the fitted line: IdealSample publishes {g.all_1ch} s, "
                            f"re-measured {means[laps][1] - got:.3f}")
        ideal_top, best_top = _top_rung()
        for label, pub, col in (("ideal", ideal_top, 0), ("best", best_top, 1)):
            if abs(float(pub) - dec(20, laps, col)) > 0.01:
                problems.append(f"top-rung {label} rate: published {pub}, re-measured "
                                f"{dec(20, laps, col):.3f}")
    if row.name.startswith("SD_30_08"):
        _pieces_best, _pieces_ideal, ideal_top, best_top = _sd_last_doubling()
        for label, pub, col in (("ideal", ideal_top, 0), ("best", best_top, 1)):
            if abs(float(pub) - dec(20, laps, col)) > 0.01:
                problems.append(f"top-rung {label} rate: published {pub}, re-measured "
                                f"{dec(20, laps, col):.3f}")
    # The best lap's own rate over the whole range, which the paragraph sets against this row's.
    best_pub = _control_rates()[[r.name for r in _rows()].index(row.name)]
    if abs(best_pub - dec(5, laps, 1)) > 0.005:
        problems.append(f"best-lap rate 5 → {laps}: published {best_pub:.3f}, re-measured "
                        f"{dec(5, laps, 1):.3f}")
    assert not problems, (
        f"{row.name}{'' if row.verified else ' ' + _UNVERIFIED}: the published figures are not "
        f"what the app computes. The app is right; the table and every surface quoting it move.\n  "
        + "\n  ".join(problems) + f"\n  re-measured row:  {measured}\n  and its prose:    {prose}")
    note = "" if row.verified else f" — this row can drop its {_UNVERIFIED}"
    print(f"test_the_table_still_matches_the_app OK ({row.name}, {got:.3f} s over {laps} laps, "
          f"every rung within Monte-Carlo error){note}\n    re-measured row:  {measured}"
          f"\n    and its prose:    {prose}")


# Its own CTest registration, `footage.<name>` (tests/_footage.py): reported SKIPPED by name
# without PACER_IDEAL_TABLE_MP4, and not in `_run_all`'s count.
FOOTAGE_CHECKS = (test_the_table_still_matches_the_app,)


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v) and v not in FOOTAGE_CHECKS]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} ideal-sample-table checks passed")


if __name__ == "__main__":
    if _footage.requested():
        sys.exit(_footage.run(FOOTAGE_CHECKS))
    sys.exit(_run_all())
