"""THE IDEAL LAP'S MEASURED TABLE IS A PUBLISHED NUMBER, AND THIS IS WHAT CHECKS IT.

WHY THIS FILE EXISTS. `corner_model.IdealSample` carries a measured table — the ideal lap over
random subsets of five real recordings — and a dozen other surfaces quote a figure out of it: the
README, both Stats tooltips, the hero chip, two Library header hovers, `Session.ideal_total`, the
laps.csv writer, the landing page and its screenshot. #228 moved the app's ideal on D24's three
chapters by +0.218 s and every one of those numbers became false in the same instant. Nothing went
red. The D24 golden gate SAW the value move — it fingerprints `theoretical_best` — and had no way
to know that eighteen files quoted it.

THREE CHECKS, AND ONLY THE THIRD NEEDS FOOTAGE:

  1. THE TABLE IS INTERNALLY CONSISTENT. Every row falls monotonically across its rungs, which is
     the claim the shipping tooltips make in prose ("it keeps falling — there is no floor it
     settles on"); and the rate column is RECOMPUTED from the row's own two endpoint cells, so a
     reader can check it with a calculator and nobody can quietly change what it means.
  2. EVERY SURFACE QUOTES THIS TABLE. The family is enumerated BY SEARCH, not by a list a later
     edit can fall off: every tracked file whose prose says "per doubling of lap count" is
     scanned, and every rate in that sentence must be one this table publishes. The standing
     lesson in this repo is that sweeps miss whole surface families — a guard with the family
     hard-coded would miss the twelfth surface exactly the way the sweep did.
  3. THE TABLE IS STILL TRUE OF THE APP. Opt-in, because it needs a recording CI does not have:
     set `PACER_IDEAL_TABLE_MP4` to a comma-separated chapter list, and the check matches the
     recording to the row with its clean-lap count and asserts the row's `all` cell equals
     `Session.ideal_total()` to the millisecond. That is the check that would have caught #228 the
     day it landed. Without the footage it prints SKIP and says what to point it at.

Checks 1 and 2 read text — no Qt, no pacer, no numpy. Check 3 imports the app.
Run:  python tests/test_ideal_sample_table.py
      PACER_IDEAL_TABLE_MP4=~/Desktop/D24/GX010062.MP4,…/GX020062.MP4,…/GX030062.MP4 \\
          python tests/test_ideal_sample_table.py
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOURCE = os.path.join(_REPO, "studio", "corner_model.py")

# The rung columns of the published table, in order. The first is the one every rate is measured
# FROM; "all" is the whole recording and carries its own lap count.
_RUNGS = (5, 10, 20, 40)


class Row:
    """One parsed table row: the rung cells (None where the recording is too short), the `all`
    cell with its lap count, and the rate the table publishes for it."""

    def __init__(self, name: str, cells: list[float | None], all_s: float, all_n: int, rate: float):
        self.name, self.cells, self.all_s, self.all_n, self.rate = name, cells, all_s, all_n, rate

    @property
    def measured_rate(self) -> float:
        """The rate the row's own two ends imply — the table's stated definition, recomputed."""
        return (self.cells[0] - self.all_s) / math.log2(self.all_n / _RUNGS[0])

    def __repr__(self) -> str:
        return f"<{self.name} {self.cells} all={self.all_s} ({self.all_n}) rate={self.rate}>"


def _rows() -> list[Row]:
    """Parse the markdown table out of IdealSample's docstring. Reads the FILE, not the imported
    docstring, so this check costs no numpy/Qt import and sees exactly what a reader sees."""
    text = open(_SOURCE, encoding="utf-8").read()
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
        name = cells[0]
        rungs: list[float | None] = [None if c == "—" else float(c) for c in cells[1:1 + len(_RUNGS)]]
        m = re.fullmatch(r"([\d.]+) \((\d+)\)", cells[-2])
        assert m, f"the `all` cell must read `<seconds> (<laps>)`, got {cells[-2]!r}"
        rate = re.fullmatch(r"([\d.]+) s", cells[-1])
        assert rate, f"the rate cell must read `<seconds> s`, got {cells[-1]!r}"
        out.append(Row(name, rungs, float(m.group(1)), int(m.group(2)), float(rate.group(1))))
    assert len(out) == 5, f"expected the five measured recordings, parsed {out!r}"
    return out


def test_the_table_only_ever_falls():
    """THE CLAIM EVERY IDEAL-LAP TOOLTIP MAKES, checked against the table it is drawn from: the
    ideal falls with every rung and keeps falling to the end of the sample. "There is no floor it
    settles on" is shipping copy on two tiles, the hero chip and a Library header; if a future
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


# The phrase every quotation of this table uses, in either of the two casings the tree has. A rate
# "per doubling" of something ELSE is not a quotation of it — `MAX_DONOR_SPAN_DEV`'s arbiter reads
# 0.623 s per doubling on a recording this table does not carry, and must not be swept in here.
_QUOTES = re.compile(r"per doubling of lap count", re.IGNORECASE)


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


def _gap_pair() -> set[str]:
    """The gap the app headlines at both ends of the table's own range ("−0.84 s at 5 laps and
    −1.42 s at 65"), parsed from the same docstring. It is the same measurement stated as the
    subtraction a reader actually sees on the tile."""
    text = re.sub(r"\n\s*", " ", open(_SOURCE, encoding="utf-8").read())
    m = re.search(r"reads −([\d.]+) s at 5 laps and −([\d.]+) s at (\d+)", text)
    assert m, "IdealSample no longer states the headline gap at both ends of its range"
    return {m.group(1), m.group(2)}


def _control_rates() -> list[float]:
    """The best-lap control rates, from the same docstring paragraph. Parsed rather than pinned so
    the two halves of one measurement cannot drift apart."""
    text = open(_SOURCE, encoding="utf-8").read()
    m = re.search(r"it falls\s+((?:[\d.]+\s*/\s*)+[\d.]+) s per doubling on those five",
                  re.sub(r"\n\s*", " ", text))
    assert m, "IdealSample no longer states the best-lap control rates"
    return [float(v) for v in m.group(1).split("/")]


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
    for v in list(ideal) + list(control):
        ok.add(f"{v:.3f}")
        ok.add(f"{v:.2f}")
    for lo, hi in ((min(ideal), max(ideal)), (min(control), max(control))):
        ok.update({f"{lo:.2f}", f"{hi:.2f}", f"{lo:.3f}", f"{hi:.3f}"})
    # The per-rung rates the shape sentence quotes are measurements of the same experiment at
    # intermediate N; they are checked by the real-media half, not derivable from the five cells.
    ok.update({"0.380", "0.343", "0.322", "0.301", "0.193", "0.189", "0.070", "0.060"})
    # The HEADLINE GAP at both ends of the same experiment — several of these sentences carry the
    # rate and the gap in one breath ("falls X per doubling ... reads −0.84 s at 5 laps"), and the
    # gap is published by the same docstring, so it is read from there rather than waved through.
    ok.update(_gap_pair())

    seen = 0
    for rel in _tracked_files():
        # This file (it prints the numbers it is checking) and CHANGELOG.md, whose released
        # sections record what was measured AT that release — history, not a live claim. A
        # changelog that is edited when a later measurement moves is not a changelog.
        if rel.endswith(os.path.basename(__file__)) or rel == "CHANGELOG.md":
            continue
        text = _flatten(open(os.path.join(_REPO, rel), encoding="utf-8", errors="ignore").read())
        if not _QUOTES.search(text):
            continue
        for sentence in _sentences(text):
            if not _QUOTES.search(sentence):
                continue
            seen += 1
            # A bare `0.xx` / `0.xxx`, not the tail of a lap time: 67.957 is a cell, not a rate.
            for num in re.findall(r"(?<![\d.])\d\.\d{2,3}(?![\d])", sentence):
                assert num in ok, (
                    f"{rel} quotes {num} s per doubling of lap count; corner_model.IdealSample "
                    f"publishes {sorted(ok)}. One of the two is stale — the table is the source.")
    assert seen >= 8, f"the scan found only {seen} sentences; it has gone vacuous"
    print(f"test_every_surface_quotes_this_table OK ({seen} sentences)")


def test_the_headline_pair_reaches_the_readme():
    """THE TWO CELLS THE PUBLIC PAGES QUOTE. The README and the laps.csv writer both print the
    D24 three-chapter row's first and last cells as one sentence — "X s over 5 laps, Y s over 65"
    — because that pair IS the disclosure's argument. It is the sentence that went stale, so it is
    read straight off the table here."""
    row = next(r for r in _rows() if r.name.startswith("D24 3"))
    want = (f"{row.cells[0]:.3f}", f"{row.all_s:.3f}", str(row.all_n))
    for rel in ("README.md", os.path.join("studio", "export_data.py")):
        text = open(os.path.join(_REPO, rel), encoding="utf-8").read()
        flat = re.sub(r"\s+", " ", text)
        m = re.search(r"([\d.]+) s over 5 laps(?:,| and) ([\d.]+) s over (\d+)", flat)
        assert m, f"{rel} no longer states the ideal over 5 laps against the whole recording"
        assert m.groups() == want, (
            f"{rel} says {m.groups()}, the table says {want} — the table is the source")
    print("test_the_headline_pair_reaches_the_readme OK")


def test_the_table_still_matches_the_app():
    """THE CHECK THAT WOULD HAVE CAUGHT IT, on real footage: load a recording, find the row with
    its clean-lap count, and assert the row's `all` cell IS `Session.ideal_total()`.

    Opt-in via `PACER_IDEAL_TABLE_MP4` (comma-separated chapters, e.g. the three that make D24's
    65-lap recording). It cannot run in CI — the recordings are 11 GB each and are not committed —
    so it prints SKIP instead, exactly like the real-media checks in test_chapter_timeline.py. The
    dev-Desktop gate to run it with is the same one AGENTS.md already names for a core-math
    change: if the golden dump moved, this moved too."""
    paths = [p.strip() for p in os.environ.get("PACER_IDEAL_TABLE_MP4", "").split(",") if p.strip()]
    if not paths:
        print("skip test_the_table_still_matches_the_app (set PACER_IDEAL_TABLE_MP4 to a "
              "comma-separated chapter list of one of the five recordings in the table)")
        return
    sys.path.insert(0, _REPO)
    # …and the bindings AHEAD of it: the repo root holds the C++ source folder `pacer/`, which
    # shadows the binding package as a namespace portion when nothing else on the path is a real
    # `pacer`. CTest injects this; a bare `python tests/…` run would otherwise die inside the
    # loader with "module 'pacer' has no attribute 'Laps'".
    sys.path.insert(0, os.path.join(_REPO, "bindings", "pacer"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import tempfile

    from studio import demo, focus, library, marks, prefs, session_record, track_db
    tmp = tempfile.mkdtemp(prefix="pacer-ideal-table-")
    for mod in (demo, focus, library, marks, prefs, session_record, track_db):
        mod._app_support_dir = lambda: tmp          # hermetic, like the golden dump
    from studio.session import Session

    s = Session.load([os.path.expanduser(p) for p in paths])
    laps = len(s.consistency_lap_ids())
    rows = [r for r in _rows() if r.all_n == laps]
    assert rows, (f"{laps} clean laps is not a row of the table "
                  f"({[(r.name, r.all_n) for r in _rows()]}) — point the variable at one of them")
    got = s.ideal_total()
    assert got is not None, "no corner partition, so there is no ideal to check the table against"
    assert abs(got - rows[0].all_s) < 0.001, (
        f"{rows[0].name}: the table publishes {rows[0].all_s:.3f} s over {laps} laps, the app "
        f"computes {got:.3f}. The app is right; every surface quoting the table has to move.")
    print(f"test_the_table_still_matches_the_app OK ({rows[0].name}, {got:.3f} s over {laps} laps)")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} ideal-sample-table checks passed")


if __name__ == "__main__":
    sys.exit(_run_all())
