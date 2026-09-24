"""Pure-Python tests for studio.dev._validate_wallclock — the reusable wall-clock auto-discovery
helpers that reconstruct which transponder-CSV laps a recording covers (so a GPS9-timing
validation can be re-run for any recording without hand-entering a lap range).

The four signals are exercised on a SYNTHETIC continuous lap log (no telemetry file needed):
the cumulative-completion clock, the elapsed-time -> lap lookup, the pit-bracket detector, and
the duration-correlation LOCK (the authoritative per-lap-shape alignment). The lock is the
fragile bit — it must peak at exactly the right integer offset and be ~0 elsewhere, the same
property the real 0060/0062 alignments relied on.

LOCK-ONLY MODE (B3) is exercised on a synthetic SPRINT: a field of drivers sharing the race's
common mode (a slow opening lap, laps under a yellow), which is what makes a 15-lap fingerprint
hard to pin by r alone. The rule has to lock the true row there, and — the two ways it must
refuse — say AMBIGUOUS once that row is gone and when a near-twin row explains the laps as well.
`studio/dev/clubspeed.py` is exercised on a synthetic heat page whose driver names must not
survive into anything it writes.

`accuracy_mk` is a REAL-FOOTAGE check (`footage.accuracy_mk`, tests/_footage.py): it re-runs the
lock-only validation of docs/ACCURACY.md's row C — MK_18_09_26 against the circuit's Club Speed
sheet for 18 Sep 2026 — and holds the table, its lock line and the chart's data to what it
measures. Without the recording or the sheet (never committed) it is reported SKIPPED by name.

Run: python tests/test_validate_wallclock.py
"""
import ast
import os
import re
import sys
import tempfile

import numpy as np

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
sys.path.insert(0, _REPO)
import _footage  # noqa: E402  (tests/ is this script's own directory)

from studio.dev import _validate_wallclock as vw  # noqa: E402
from studio.dev import clubspeed  # noqa: E402


def _make_log(seed=3):
    """A synthetic continuous lap log: 1000 racing laps ~69 s with a long pit lap every ~70 laps,
    so it has the same structure as the real 24 h CSV (racing stints bracketed by pit laps)."""
    rng = np.random.default_rng(seed)
    laps = {}
    for i in range(1, 1001):
        if i % 70 == 0:  # a pit / driver-change lap
            laps[i] = float(rng.uniform(200.0, 260.0))
        else:
            laps[i] = float(69.0 + rng.normal(0.0, 0.6))
    return laps


def test_cumulative_completion_and_lap_being_driven():
    laps = {1: 70.0, 2: 71.0, 3: 200.0, 4: 69.0}
    comp = vw.cumulative_completion(laps)
    assert abs(comp[1] - 70.0) < 1e-9
    assert abs(comp[2] - 141.0) < 1e-9
    assert abs(comp[3] - 341.0) < 1e-9     # the long pit lap is REAL elapsed time
    assert abs(comp[4] - 410.0) < 1e-9
    # The lap in progress at an elapsed time is the first whose COMPLETION is at/after it.
    assert vw.lap_being_driven(comp, 0.0) == 1
    assert vw.lap_being_driven(comp, 70.5) == 2
    assert vw.lap_being_driven(comp, 200.0) == 3   # mid pit lap
    assert vw.lap_being_driven(comp, 409.0) == 4
    print("test_cumulative_completion_and_lap_being_driven OK")


def test_pit_brackets():
    laps = _make_log()
    # Lap 105 is mid-stint (pit laps at 70 and 140). Brackets should be those two.
    before, after = vw.pit_brackets(laps, 105)
    assert before == 70, before
    assert after == 140, after
    print("test_pit_brackets OK")


def test_best_offset_locks_unique_alignment():
    """The duration-correlation must peak sharply at the true offset and be ~0 elsewhere — the
    property that pins the CSV lap range. Take a contiguous racing window out of the log, add a
    little GPS-noise (so it's not a trivial identity match), and confirm the lock recovers the
    exact start offset with high corr and a clear margin over every neighbour."""
    laps = _make_log()
    true_start = 211  # a racing window between pit laps at 210 and 280
    n = 60
    rng = np.random.default_rng(11)
    app = np.array([laps[true_start + k] + rng.normal(0.0, 0.12) for k in range(n)])

    start, corr, offsets = vw.best_offset(app, laps, true_start - 8, true_start + 8)
    assert start == true_start, (start, true_start)
    assert corr > 0.9, corr
    # Uniqueness: every OTHER offset's corr is far below the locked one.
    others = [c for s, c, _ in offsets if s != true_start]
    assert max(others) < corr - 0.4, (corr, max(others))
    print("test_best_offset_locks_unique_alignment OK")


def test_residual_stats():
    r = np.array([0.1, -0.1, 0.2, -0.2, 0.0])
    s = vw.residual_stats(r)
    assert s["n"] == 5
    assert abs(s["mean"]) < 1e-9
    assert abs(s["median"]) < 1e-9
    assert abs(s["rms"] - float(np.sqrt(np.mean(r ** 2)))) < 1e-12
    print("test_residual_stats OK")


def test_parse_when_handles_z_and_naive():
    a = vw._parse_when("2026-05-23 12:00:00Z")
    assert a.utcoffset().total_seconds() == 0
    assert a.hour == 12
    b = vw._parse_when("2026-05-24 06:54")  # naive -> treated UTC
    assert b.hour == 6 and b.minute == 54
    print("test_parse_when_handles_z_and_naive OK")


# ----------------------------------------------------------------------------- lock-only mode (B3)
_TRUE = (90190, 11)


def _sprint_field(seed=5, drivers=25, laps=16):
    """A `laps`-lap sprint of `drivers` rows, all sharing the race's COMMON MODE — a slow opening
    lap from the start and two laps under a yellow — on top of their own pace and 0.6 s of
    lap-to-lap scatter. The true driver (`_TRUE`) runs ~68 s laps, like the MK race."""
    rng = np.random.default_rng(seed)
    common = np.zeros(laps)
    common[0], common[[5, 6]] = 8.0, 2.5
    rows = {}
    for d in range(1, drivers + 1):
        pace = 68.0 if (90190, d) == _TRUE else rng.uniform(67.5, 74.0)
        rows[(90190, d)] = {k + 1: float(t) for k, t in
                            enumerate(pace + common + rng.normal(0.0, 0.6, laps))}
    return rows


def _stint(rows, start=2, n=15, gps_sd=0.03, seed=7):
    """What the app times for the true driver: sheet laps start..start+n-1, plus GPS noise."""
    rng = np.random.default_rng(seed)
    return np.array([rows[_TRUE][start + k] for k in range(n)]) + rng.normal(0.0, gps_sd, n)


def test_stints_split_at_pit_laps_and_the_open_lap():
    times = [80.3, 75.6, 74.3, 70.7, 487.3, 326.6, 75.8, 70.1, 69.7, 0.0]
    assert vw.stints(times) == [[0, 1, 2, 3], [6, 7, 8]], vw.stints(times)
    assert vw.stints([]) == [] and vw.stints([130.0, 0.0]) == []
    print("test_stints_split_at_pit_laps_and_the_open_lap OK")


def test_lock_only_pins_the_true_row_in_a_field_that_shares_its_common_mode():
    rows = _sprint_field()
    app = _stint(rows)
    v = vw.lock_verdict(vw.lock_all(app, rows))
    assert v["locked"], v["why"]
    assert (v["lock"]["row"], v["lock"]["start"]) == (_TRUE, 2), v["lock"]
    assert v["separation"] >= vw.LOCK_MIN_SEPARATION, v["separation"]
    print(f"test_lock_only_pins_the_true_row_in_a_field_that_shares_its_common_mode OK "
          f"(r {v['lock']['r']:.4f}, margin {v['margin']:+.2f}, separation {v['separation']:.0f}x)")


def test_lock_only_is_ambiguous_once_the_true_row_is_gone():
    """The negative control of the rule itself: the same stint against the same field without the
    driver it belongs to must NOT lock onto the best of the rest."""
    rows = _sprint_field()
    app = _stint(rows)
    del rows[_TRUE]
    v = vw.lock_verdict(vw.lock_all(app, rows))
    assert not v["locked"], (v["lock"], v.get("separation"))
    print(f"test_lock_only_is_ambiguous_once_the_true_row_is_gone OK ({v['why']})")


def test_lock_only_is_ambiguous_when_a_twin_row_fits_as_well():
    """A second row that explains the laps about as well as the true one (a timing sheet listing a
    driver twice, or two karts in lockstep) leaves no unique answer, however high r is."""
    rows = _sprint_field()
    app = _stint(rows)
    rng = np.random.default_rng(3)
    rows[(90190, 99)] = {k: t + rng.normal(0.0, 0.02) for k, t in rows[_TRUE].items()}
    v = vw.lock_verdict(vw.lock_all(app, rows))
    assert v["lock"]["r"] > 0.99 and not v["locked"], (v["lock"], v.get("separation"))
    assert vw.lock_verdict([]) == {"locked": False, "why": "no candidate window fits this stint"}
    # One window in the whole sheet (a row exactly the stint's length) has nothing to be unique
    # against — however well it fits, that is not a lock.
    only = {_TRUE: {k: t for k, t in _sprint_field()[_TRUE].items() if 2 <= k <= 16}}
    single = vw.lock_verdict(vw.lock_all(app, only))
    assert not single["locked"] and single["rivals"] == 0, single
    print(f"test_lock_only_is_ambiguous_when_a_twin_row_fits_as_well OK ({v['why']})")


def test_a_fingerprint_with_no_shape_does_not_lock():
    """A metronomic driver in a clean race: 15 laps within a tenth of each other. Every rival
    misses by far more (the separation alone would call it), but there is no shape for r to pin,
    and a pairing decided by the residual's size alone is not the method — so `LOCK_MIN_R` refuses
    it. This is the case that holds that threshold on its own; the others trip both. Only a narrow
    band of lap spread over GPS noise (~2.1-3.0) separates by σ yet not by r — for a metronome the
    nearest rival is its own row one lap off — so the seed is chosen to sit in it, and the first
    assertion says so if a change ever moves it out."""
    rng = np.random.default_rng(17)
    rows = {(90190, d): {k: 68.0 + 0.3 * d + rng.normal(0.0, 0.6) for k in range(1, 17)}
            for d in range(1, 13)}
    rows[_TRUE] = {k: 68.0 + rng.normal(0.0, 0.05) for k in range(1, 17)}
    v = vw.lock_verdict(vw.lock_all(_stint(rows, gps_sd=0.02), rows))
    assert v["lock"]["row"] == _TRUE and v["separation"] >= vw.LOCK_MIN_SEPARATION, v
    assert not v["locked"] and "best r" in v["why"], v
    print(f"test_a_fingerprint_with_no_shape_does_not_lock OK ({v['why']}, "
          f"separation {v['separation']:.0f}x)")


_PAGE = """<html><body><form name="Form1" method="post" action="./HeatDetails.aspx?HeatNo=90123">
<span id="lblDate" class="ItemTitle">18/09/2026 20:10</span>
<table class='RaceResults'><tr><td class="Racername"><a href="RacerHistory.aspx?CustID=1">Zebedee Quux</a></td></tr></table>
<table class='LapTimesContainer'><tbody><tr><td><table class='LapTimes'><thead><tr><th colspan='2'>Zebedee Quux</th></tr></thead>
<tbody><tr><td colspan='2'>(Penalties: 0)</td></tr>
<tr class='LapTimesRow'><td>1</td><td>81.865 [1]</td></tr><tr class='LapTimesRowAlt'><td>2</td><td>77.03 [1]</td></tr>
<tr class='LapTimesRow'><td>3</td><td>1:45.2 [2]</td></tr></tbody></table></td>
<td><table class='LapTimes'><thead><tr><th colspan='2'>Wilhelmina Xyzzy</th></tr></thead>
<tbody><tr><td colspan='2'>(Penalties: 1)</td></tr>
<tr class='LapTimesRow'><td>1</td><td>83.5 [2]</td></tr><tr class='LapTimesRowAlt'><td>2</td><td>&nbsp;</td></tr>
</tbody></table></td></tr></tbody></table></form></body></html>"""


def test_a_club_speed_page_becomes_laps_and_never_a_name():
    start, rows = clubspeed.parse_page(_PAGE)
    assert start == "18/09/2026 20:10", start
    assert rows == [{1: 81.865, 2: 77.03, 3: 105.2}, {1: 83.5}], rows
    with tempfile.TemporaryDirectory(prefix="pacer-clubspeed-") as d:
        page, out = os.path.join(d, "saved.html"), os.path.join(d, "day.csv")
        with open(page, "w", encoding="utf-8") as f:
            f.write(_PAGE)
        assert clubspeed.main([page, "--out", out]) == 0
        text = open(out, encoding="utf-8").read()
        for word in ("Zebedee", "Quux", "Wilhelmina", "Xyzzy"):
            assert word not in text, f"a driver's name reached the CSV: {word!r}"
        rows, starts = vw.load_rows(out)
        assert rows == {(90123, 1): {1: 81.865, 2: 77.03, 3: 105.2}, (90123, 2): {1: 83.5}}, rows
        assert starts == {90123: "18/09/2026 20:10"}, starts
        transponder_csv = os.path.join(d, "race-results.csv")
        with open(transponder_csv, "w", encoding="utf-8") as f:
            f.write('Lap,Pos,Lap Time,Diff\n1,3,1:08.376,"2", laps\n2,3,1:9.030,x\n')
        assert vw.load_rows(transponder_csv) == ({("csv", 1): {1: 68.376, 2: 69.03}}, {})
    print("test_a_club_speed_page_becomes_laps_and_never_a_name OK")


def test_no_tool_here_writes_beside_the_footage_or_over_an_input():
    """Both tools take their INPUTS positionally and write only where a flag says — and refuse a
    flag that points at the Desktop's footage or back at an input, before reading anything."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    with tempfile.TemporaryDirectory(prefix="pacer-wallclock-cli-") as d:
        rec, sheet = os.path.join(d, "GX010001.MP4"), os.path.join(d, "day.csv")
        refused = [
            (vw.main, [rec, sheet, "--dump", os.path.join(desktop, "pacer-never-written.json")]),
            (vw.main, [rec, sheet, "--dump", sheet]),
            (vw.main, [rec, sheet, "--local-start", "2026-09-18 20:00"]),
            (clubspeed.main, [sheet, "--out", os.path.join(desktop, "pacer-never-written.csv")]),
            (clubspeed.main, [sheet, "--out", os.path.join(d, "day.html")]),
        ]
        for fn, argv in refused:
            try:
                fn(argv)
            except SystemExit as exc:
                assert exc.code == 2, (argv, exc.code)
            else:
                raise AssertionError(f"{fn.__module__} ran with {argv}")
            assert os.listdir(d) == [], os.listdir(d)
    print("test_no_tool_here_writes_beside_the_footage_or_over_an_input OK")


# ----------------------------------------------------------------------------- footage.accuracy_mk
# docs/ACCURACY.md's row C: the recording, where its chapters are, and the sheet it was locked to.
_MK_CHAPTERS = ("MK_18_09_26/GX010067.MP4", "MK_18_09_26/GX020067.MP4")
_MK_SHEET = "mk-2026-09-18.csv"
_ACCURACY_MD = os.path.join(_REPO, "docs", "ACCURACY.md")
_MEDIA_CAPTURE = os.path.join(_REPO, "studio", "dev", "media_capture.py")
_NUM = re.compile(r"[+\-−]?\d+(?:\.\d+)?")


def _num(cell: str) -> float:
    m = _NUM.search(cell)
    assert m, f"no number in {cell!r}"
    return float(m.group(0).replace("−", "-"))


def _table_row(text: str, head: str, after: str) -> list[str]:
    """The cells of the first table row starting `| <head>` below the line containing `after`."""
    tail = text[text.index(after):]
    line = next(ln for ln in tail.splitlines() if ln.startswith(f"| {head}"))
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _published_row_c() -> dict:
    """Row C as docs/ACCURACY.md publishes it (the results table, then the lock table) and as the
    accuracy chart is drawn (`media_capture.ACCURACY`, read with ast — no Qt needed)."""
    text = open(_ACCURACY_MD, encoding="utf-8").read()
    res = _table_row(text, "**C**", "## The validated numbers")
    lock = _table_row(text, "**C**", "| Recording | Searched |")
    tree = ast.parse(open(_MEDIA_CAPTURE, encoding="utf-8").read())
    chart = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "ACCURACY" for t in n.targets))
    c = next(r for r in chart if r["name"] == "Recording C")
    searched = [int(x) for x in re.findall(r"\d+", lock[1])]
    return {
        "table": {"dop": _num(res[2]), "aligned": int(_num(res[3])), "clean": int(_num(res[4])),
                  "mean": _num(res[5]), "sigma": _num(res[6])},
        "chart": {k: c[k] for k in ("dop", "aligned", "clean", "mean", "sigma")},
        "lock": {"windows": searched[0], "rows": searched[1], "heats": searched[2],
                 "r": _num(lock[2]), "rival_r": _num(lock[3]), "margin": _num(lock[4]),
                 "separation": int(re.search(r"(\d+)×", lock[5]).group(1))},
    }


def accuracy_mk():
    """docs/ACCURACY.md's row C, re-measured: MK_18_09_26 (both chapters) locked, with no race start
    and no hand-matching, against every driver row of the circuit's Club Speed sheet for the day.
    Exactly one stint must LOCK; its clean-lap mean, σ, counts and the recording's median DOP must be
    what the table and the chart publish, and its lock figures what the lock table publishes. And
    the lock must not be the only thing left: without the row it locked to, the stint is AMBIGUOUS."""
    root = _footage.directory("PACER_MEASURED_FIGURES_DIR", "the accuracy table's row C (MK_18_09_26)",
                              _MK_CHAPTERS)
    sheet = _footage.timing_sheet(_MK_SHEET)
    got = vw.run_lock_only(os.path.join(root, _MK_CHAPTERS[0]), sheet)
    locked = [s for s in got["stints"] if s.get("locked")]
    assert len(locked) == 1, [(s["app_laps"], s.get("why")) for s in got["stints"]]
    s = locked[0]
    clean = s["stats"]["clean"]
    measured = {"dop": round(got["median_dop"], 2), "aligned": s["n"], "clean": clean["n"],
                "mean": round(clean["mean"], 4), "sigma": round(clean["std"], 4)}
    pub = _published_row_c()
    assert measured == pub["table"] == pub["chart"], (
        f"row C re-measured {measured}; docs/ACCURACY.md publishes {pub['table']}, the chart "
        f"(media_capture.ACCURACY) {pub['chart']}")
    lock = {"windows": s["candidates"], "rows": got["rows"], "heats": len(got["heats"]),
            "r": round(s["lock_r"], 4), "rival_r": round(s["best_rival_r"], 2),
            "margin": round(s["margin"], 2), "separation": int(s["separation"])}
    assert lock == pub["lock"], f"row C's lock re-measured {lock}; docs/ACCURACY.md says {pub['lock']}"

    rows, _starts = vw.load_rows(sheet)
    del rows[tuple(s["row"])]
    app = np.array([p["app_s"] for p in s["per_lap"]])
    rest = vw.lock_verdict(vw.lock_all(app, rows))
    assert not rest["locked"], f"the stint still locks without its own row: {rest['lock']}"
    print(f"accuracy_mk: re-measured {measured}, lock {lock}; without its row: {rest['why']}")


FOOTAGE_CHECKS = (accuracy_mk,)


if __name__ == "__main__":
    if _footage.requested():
        sys.exit(_footage.run(FOOTAGE_CHECKS))
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} WALLCLOCK-VALIDATION TESTS PASSED")

