"""The provenance contract: every inspectable number RE-DERIVES from the rows it shows.

This is the test the "inspect this number" feature exists to make possible, so it is written as
the claim rather than as a set of smoke checks: build a REAL `pacer.Laps` over a synthetic
stadium, ask `Session` for the provenance of a lap time, a sector split and a corner best, and
re-compute each value from the panel's own table by the panel's own stated method.

Three separate claims, and they are NOT the same strength — which is the honest part:

  * a SECTOR SPLIT and a CORNER BEST re-derive BIT FOR BIT. Both are `np.interp` on a lap's
    (odometer, elapsed) curve, and the rows the panel shows bracket the boundaries, so the
    re-derivation performs the identical arithmetic on the identical doubles.
  * a LAP TIME re-derives to within ONE UNIT IN THE LAST PLACE per crossing. Its boundaries come
    out of the C++ core, which evaluates `t0*(1-f) + f*t1` and is free to contract that into a
    fused multiply-add — one rounding where `provenance.crossing_time` does two. So the panel
    claims agreement AT THE DISPLAYED PRECISION and states the residual in ulp; this file pins
    both halves, including the ulp bound, because a transcription that silently drifted further
    would be a defect and the panel's sentence would then be false.

Pure Python + the pacer bindings; no Qt, no telemetry file, sub-second. The panel that renders
these values is tested separately (tests/test_provenance_panel.py).
Run: PYTHONPATH=bindings/pacer python tests/test_provenance.py
"""
import math
import os
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import pacer  # noqa: E402
from studio import provenance  # noqa: E402
from studio.session import Session  # noqa: E402

# ---------------------------------------------------------------- a real, synthetic recording
RADIUS, STRAIGHT = 30.0, 200.0
ARC = math.pi * RADIUS
TOTAL = 2 * STRAIGHT + 2 * ARC
ORIGIN = pacer.GPSSample(lat=52.0, lon=-0.75, altitude=60.0)


def _stadium(ds: float = 2.0):
    """(xs, ys) of one stadium loop in local metres, parametrised by arc length: straight, a
    180-degree left arc, the return straight, a second arc. Two arcs = two detectable corners."""
    s = np.arange(0.0, TOTAL, ds)
    xs, ys = np.empty_like(s), np.empty_like(s)
    for i, si in enumerate(s):
        if si < STRAIGHT:
            xs[i], ys[i] = si, 0.0
        elif si < STRAIGHT + ARC:
            th = (si - STRAIGHT) / RADIUS
            xs[i] = STRAIGHT + RADIUS * math.sin(th)
            ys[i] = RADIUS - RADIUS * math.cos(th)
        elif si < 2 * STRAIGHT + ARC:
            xs[i] = STRAIGHT - (si - STRAIGHT - ARC)
            ys[i] = 2 * RADIUS
        else:
            th = (si - 2 * STRAIGHT - ARC) / RADIUS
            xs[i] = -RADIUS * math.sin(th)
            ys[i] = RADIUS + RADIUS * math.cos(th)
    return xs, ys, s.copy()


def _session(n_laps: int = 6, dt: float = 0.1) -> Session:
    """A real `Session` over a real `pacer.Laps`: `n_laps` stadium loops fed through `add_point`
    exactly as the load pipeline feeds it, with GPS9 quality fields on every fix so the
    fix-quality distribution has something true to report.

    SAMPLED IN TIME, at a fixed 10 Hz, not along the track. A GPS receiver reports on a clock, so
    a distance-sampled stand-in would be denser through the slow arcs than down the straights —
    which is backwards, and would make the panel's fix-spacing and gap figures meaningless here
    while they are meaningful on a real recording. The stadium is therefore built by arc length
    and then resampled onto a uniform time grid.

    Speeds differ per lap so there IS a best lap and a per-corner best to be found, and every lap
    is slower through the arcs than down the straights so the curvature detector sees real
    corners."""
    xs, ys, cum = _stadium(1.0)
    cs = pacer.CoordinateSystem(ORIGIN)
    laps = pacer.Laps()
    on_arc = ((cum >= STRAIGHT) & (cum < STRAIGHT + ARC)) | (cum >= 2 * STRAIGHT + ARC)
    t = 100.0
    row = 0
    for lap in range(n_laps):
        # Per-lap pace: lap 3 is the quickest. The arcs are always slower than the straights.
        pace = 1.0 + 0.04 * abs(lap - 3)
        speed = np.where(on_arc, 12.0, 26.0) / pace
        # Arc length -> elapsed (the trapezoidal ds/v integral), then invert onto a 10 Hz grid.
        step = np.diff(cum) / ((speed[:-1] + speed[1:]) / 2.0)
        elapsed = np.concatenate(([0.0], np.cumsum(step)))
        grid = np.arange(0.0, elapsed[-1], dt)
        at = np.interp(grid, elapsed, cum)
        for s_i, dt_i in zip(at, grid, strict=True):
            g = cs.global_(pacer.Vec3f(float(np.interp(s_i, cum, xs)),
                                       float(np.interp(s_i, cum, ys)), 0.0))
            v = float(np.interp(s_i, cum, speed))
            # A 3D lock everywhere, DOP walking gently so min/median/max differ.
            sample = pacer.GPSSample(lat=g.lat, lon=g.lon, altitude=60.0,
                                     full_speed=v, ground_speed=v,
                                     dop=1.0 + 0.5 * (row % 5), fix=3)
            laps.add_point(sample, float(t + dt_i))
            row += 1
        t += float(elapsed[-1]) + dt
    laps.set_coordinate_system(cs)
    # A start/finish line across the bottom straight, well away from either arc.
    a, b = pacer.Point(), pacer.Point()
    a.x, a.y, b.x, b.y = 100.0, -20.0, 100.0, 20.0
    seg = pacer.Segment()
    seg.first, seg.second = a, b
    laps.sectors = pacer.Sectors(start_line=seg, sector_lines=[])
    laps.update()
    return Session(laps, cs, None)


def _with_sectors(s: Session) -> Session:
    s.set_timing_lines(s.start_line, s.suggest_sectors(2), user_confirm=True)
    return s


# ------------------------------------------------------------------------- the three claims
def test_a_lap_time_redderives_from_its_two_crossing_chords():
    """The headline claim for lap time: the two raw fixes on each side of each timing-line
    crossing, plus the core's own crossing rule, reproduce the lap time.

    The bound is stated in ULP OF THE MEDIA CLOCK, not as a tolerance someone liked: each
    crossing instant is one `t0*(1-f) + f*t1`, the core may fuse that multiply-add and this
    transcription cannot, so each of the two boundaries may land one ulp out and their
    difference two. Anything beyond that is a transcription that has drifted from the core."""
    s = _session()
    checked = 0
    for lap_id in s.valid_lap_ids():
        p = s.lap_time_provenance(lap_id)
        assert p is not None, lap_id
        assert p.value == s.lap_time(lap_id), "the provenance must describe the DISPLAYED value"
        assert p.reconstructed is not None
        # The claim the panel makes on screen.
        assert p.matches_display, (p.formatted, p.reconstructed_formatted)
        # ...and the stronger numeric bound behind it.
        ulp = abs(p.residual) / math.ulp(p.window.hi)
        assert ulp <= 2.0, (lap_id, p.residual, ulp)
        checked += 1
    assert checked >= 3, checked
    print(f"test_a_lap_time_redderives_from_its_two_crossing_chords OK — {checked} laps, "
          f"every one within 2 ulp of the media clock and identical on screen")


def test_a_sector_split_redderives_bit_for_bit():
    """A split is `np.interp` at two odometer boundaries; the panel shows the fixes that bracket
    both, so re-running that interpolation on the shown rows must land on the SAME double. No
    tolerance: this one is exact or it is broken."""
    s = _with_sectors(_session())
    checked = 0
    for lap_id in s.valid_lap_ids():
        splits = s.lap_sector_splits(lap_id)
        assert len(splits) >= 2, splits
        for k, split in enumerate(splits):
            p = s.sector_split_provenance(lap_id, k)
            assert p is not None, (lap_id, k)
            assert p.value == split
            assert p.exact, (lap_id, k, p.residual)
            checked += 1
    assert checked >= 6, checked
    print(f"test_a_sector_split_redderives_bit_for_bit OK — {checked} splits, all exact")


def test_a_corner_best_redderives_and_names_the_lap_that_set_it():
    """A corner best is two operations deep, so it needs two tables: the winning lap's fixes and
    the per-lap population the minimum was taken over. Both must be true — the re-derivation
    from the fixes, AND the minimum of the population equalling the displayed value."""
    s = _session()
    corners = s.corners.corner_list()
    assert corners, "the stadium's two arcs must detect as corners"
    report = {r.cid: r for r in s.corner_report()}
    checked = 0
    for c in corners:
        p = s.corner_best_provenance(c.cid)
        assert p is not None, c.cid
        # It describes the CORNERS table's own Best cell, not a second opinion about it.
        assert p.value == report[c.cid].best_s, (c.cid, p.value, report[c.cid].best_s)
        assert p.exact, (c.cid, p.residual)
        # Two tables: raw fixes, then the population.
        assert len(p.tables) == 2, p.tables
        population = [row[1] for row in p.tables[1].rows]
        assert min(population) == p.value, "the minimum of the shown population IS the value"
        assert p.tables[1].rows[0][1] == p.value, "the population is quickest-first"
        checked += 1
    print(f"test_a_corner_best_redderives_and_names_the_lap_that_set_it OK — {checked} corners, "
          f"all exact, each minimum shown alongside the laps it beat")


# -------------------------------------------------------------- the crossing transcription
def test_the_crossing_transcription_agrees_with_the_core():
    """`provenance.crossing_fraction` is a transcription of `pacer::Segment::Intersects`, and
    this is what notices if the core's crossing rule ever changes underneath it.

    A crossing is recomputed from the two RAW fixes either side of every lap boundary and
    compared with the boundary the core actually produced."""
    s = _session()
    line = s._start_line_lonlat()
    worst = 0.0
    n = 0
    for lap_id in s.valid_lap_ids():
        fixes = s._lap_fixes(lap_id, bracket=True)
        rows = len(fixes.times)
        for (lo, hi), truth in ((( 0, 2), fixes.times[1]),
                                ((rows - 3, rows - 1), fixes.times[rows - 2])):
            f = provenance.crossing_fraction(
                line, (fixes.lons[lo], fixes.lats[lo]), (fixes.lons[hi], fixes.lats[hi]))
            assert f is not None, "the chord the core crossed must cross here too"
            assert 0.0 < f < 1.0, f
            got = provenance.crossing_time(float(fixes.times[lo]), float(fixes.times[hi]), f)
            worst = max(worst, abs(got - float(truth)) / math.ulp(float(truth)))
            n += 1
    assert worst <= 1.0, worst
    print(f"test_the_crossing_transcription_agrees_with_the_core OK — {n} crossings, worst "
          f"{worst:.1f} ulp")


def test_a_chord_that_does_not_cross_reports_no_crossing():
    """The strict-sign rule the core uses, transcribed: a chord that merely approaches the line,
    and one whose endpoint sits exactly ON it, are both NOT crossings. That is what stops one
    pass being counted as two."""
    line = ((0.0, -1.0), (0.0, 1.0))
    assert provenance.crossing_fraction(line, (-2.0, 0.0), (-1.0, 0.0)) is None
    assert provenance.crossing_fraction(line, (0.0, 0.0), (1.0, 0.0)) is None, "a touch is not a cross"
    f = provenance.crossing_fraction(line, (-1.0, 0.0), (3.0, 0.0))
    assert f == 0.25, f
    print("test_a_chord_that_does_not_cross_reports_no_crossing OK")


# ------------------------------------------------------------------- what the value SAYS
def test_the_window_and_n_describe_the_rows_actually_shown():
    """N and the window are the two facts a number is meaningless without, so they have to agree
    with the table rather than be asserted next to it: every row inside the window is counted by
    N, and the rows outside it are exactly the ones marked as brackets."""
    s = _with_sectors(_session())
    lap_id = s.best_lap_id()
    p = s.sector_split_provenance(lap_id, 1)
    role = provenance.FIX_COLUMNS.index("role")
    d = provenance.FIX_COLUMNS.index("d (m)")
    inside = [r for r in p.samples.rows if p.window.lo <= r[d] <= p.window.hi]
    outside = [r for r in p.samples.rows if not (p.window.lo <= r[d] <= p.window.hi)]
    assert len(inside) == p.n, (len(inside), p.n)
    assert all(r[role] == provenance.RAW for r in inside)
    assert outside and all(r[role] == provenance.BRACKET for r in outside), outside
    # The distribution is the WINDOW'S, not the table's: the bracket rows are shown so the
    # interpolation is re-derivable, and counted in neither N nor the quality summary. The panel
    # read "N = 84" over "3D lock: 86" until this was pinned.
    assert p.quality.n == p.n, (p.quality.n, p.n)
    assert p.quality.n == len(p.samples.rows) - len(outside)
    print(f"test_the_window_and_n_describe_the_rows_actually_shown OK — N={p.n} inside, "
          f"{len(outside)} bracketing rows")


def test_the_lap_time_table_marks_its_two_derived_rows():
    """The two interpolated crossings ARE the lap time and they are not measurements. A table
    that did not say so per row would be presenting a derivation as data — the exact thing this
    feature exists to prevent."""
    s = _session()
    p = s.lap_time_provenance(s.best_lap_id())
    role = provenance.FIX_COLUMNS.index("role")
    fix = provenance.FIX_COLUMNS.index("fix")
    roles = [r[role] for r in p.samples.rows]
    assert roles.count(provenance.CROSSING) == 2, roles.count(provenance.CROSSING)
    assert roles[0] == roles[-1] == provenance.BRACKET
    crossings = [r for r in p.samples.rows if r[role] == provenance.CROSSING]
    # A derived point HAS no fix quality; the core does not blend the quality fields and the
    # panel must not invent one.
    assert all(r[fix] == "unknown" for r in crossings), crossings
    assert all(r[fix] == "3D lock" for r in p.samples.rows if r[role] != provenance.CROSSING)
    print("test_the_lap_time_table_marks_its_two_derived_rows OK — 2 interpolated rows, both "
          "reported as unknown quality")


def test_the_fix_quality_is_summarised_over_the_window_not_the_recording():
    """The distribution is per-window: every category is emitted (a zero count is a fact), and
    the DOP figures come from the fixes inside this window only."""
    s = _session()
    p = s.lap_time_provenance(s.best_lap_id())
    names = [name for name, _ in p.quality.fix_counts]
    assert names[:4] == ["3D lock", "2D", "no lock", "unknown"], names
    assert dict(p.quality.fix_counts)["3D lock"] == p.n
    assert p.quality.dop_min == 1.0 and p.quality.dop_max == 3.0, p.quality
    # Fix spacing is reported, and the gap count is a real signal rather than a formality: this
    # synthetic trace is sampled by DISTANCE (a fixed 2 m step), so its inter-fix TIME genuinely
    # more than doubles at each arc and the counter fires. A real 10 Hz recording reads 0 — both
    # D24 fixtures do — which is exactly the difference the panel is meant to surface.
    # Fix spacing is reported, and the gap count is a real signal rather than a formality: this
    # trace is sampled on a clock at a fixed 10 Hz (see _session), exactly as a receiver reports,
    # so a clean window reads no gaps — as both D24 fixtures do. A count above zero here would
    # mean the synthetic recording had grown a dropout.
    assert abs(p.quality.dt_median - 0.1) < 1e-9, p.quality.dt_median
    assert p.quality.gaps == 0, p.quality.gaps
    assert any("3D lock" in line for line in p.quality.lines())
    print(f"test_the_fix_quality_is_summarised_over_the_window_not_the_recording OK — "
          f"{p.quality.lines()[0]}")


def test_the_quality_fields_survive_the_load_smoothing():
    """`load._smooth_track` rebuilds every sample field by field, and for a long time it did not
    name `dop`/`fix` — so the app's record of how good each fix was ended one step after the gate
    that read it, and a per-fix quality distribution was not derivable at all.

    Pinned here rather than in the loader's own test because THIS is the consumer: if the two
    fields stop reaching `pacer.Laps`, the panel silently reports "unknown" for every row and
    still looks fine."""
    from studio.load import _smooth_track
    samples = [pacer.GPSSample(lat=52.0 + i * 1e-5, lon=-0.75, altitude=60.0, full_speed=20.0,
                               ground_speed=20.0, dop=1.5, fix=3) for i in range(40)]
    times = [100.0 + 0.1 * i for i in range(40)]
    out = _smooth_track(samples, times, 13)
    assert out is not samples, "the smoother must have rebuilt the samples for this to be a test"
    assert [x.lat for x in out] != [x.lat for x in samples], "positions must actually smooth"
    assert all(x.fix == 3 and x.dop == 1.5 for x in out), "quality must ride across untouched"
    print("test_the_quality_fields_survive_the_load_smoothing OK — dop/fix carried, lat smoothed")


# -------------------------------------------------------------------------- method sentences
def test_every_method_id_has_a_sentence_and_every_sentence_is_used():
    """`METHODS` is the single source of the one-sentence explanations, pinned in BOTH directions
    like the layering allow-lists: an id with no sentence would crash the panel, and a sentence
    no builder emits is a description of something the app no longer does."""
    s = _with_sectors(_session())
    lap_id = s.best_lap_id()
    emitted = {
        s.lap_time_provenance(lap_id).method_id,
        s.sector_split_provenance(lap_id, 0).method_id,
        s.corner_best_provenance(s.corners.corner_list()[0].cid).method_id,
    }
    assert emitted <= set(provenance.METHODS), emitted - set(provenance.METHODS)
    assert set(provenance.METHODS) == emitted, set(provenance.METHODS) - emitted
    for mid, sentence in provenance.METHODS.items():
        assert sentence.endswith("."), mid
        assert len(sentence) > 80, mid
    print(f"test_every_method_id_has_a_sentence_and_every_sentence_is_used OK — "
          f"{len(emitted)} methods, both directions")


# -------------------------------------------------------------- an absent number is never a word
def test_a_cell_with_no_value_never_renders_as_the_word_nan():
    """`_fmt` already refuses to print the word `None` for a missing value — and a missing value
    reaches this panel spelled BOTH ways.

    `d (m)` is the lap's own odometer, which is undefined outside the lap, and the builder says so
    in NumPy's spelling: `_lap_fixes(bracket=True)` writes NaN there and
    `provenance._lap_time` reads `dists[i] != dists[i]` to mean "outside the lap". The two rows
    that bracket the start/finish crossing therefore carry a NaN odometer BY CONSTRUCTION, on
    every lap of every recording — so the panel whose entire job is to show a number's evidence
    printed the literal string `nan` in its first visible row, and the Copy-as-CSV button wrote
    `nan` into the column beside it.

    `None` and NaN are the same fact here (this row has no odometer), so they get the same
    rendering: the panel's absent-value dash on screen, an empty field in the CSV. The `role`
    column already says which rows those are."""
    s = _session()
    absent = provenance._fmt("{:.2f}", None)
    d_col = provenance.FIX_COLUMNS.index("d (m)")

    checked = bracketed = 0
    for lap_id in s.valid_lap_ids()[:4]:
        p = s.lap_time_provenance(lap_id)
        assert p is not None, lap_id
        for table in p.tables:
            for r in range(len(table.rows)):
                for c in range(len(table.columns)):
                    raw, shown = table.rows[r][c], table.cell(r, c)
                    checked += 1
                    assert shown.strip().lower() not in ("nan", "-nan", "inf", "-inf", "none"), (
                        f"lap {lap_id} {table.caption!r} row {r} column "
                        f"{table.columns[c]!r} displays {shown!r} — a value the panel does not "
                        f"have must render as {absent!r}, not as the word for it")
                    if isinstance(raw, float) and not math.isfinite(raw):
                        bracketed += 1
                        assert shown == absent, (raw, shown)
                        assert table.rows[r][-1] == provenance.BRACKET, (
                            "only a row that brackets the boundary may lack an odometer")
        # ...and the CSV the panel's copy button writes leaves the field EMPTY rather than
        # exporting a NaN into somebody's spreadsheet.
        body = p.tables[0].csv_lines()[2:]
        for line in body:
            cells = line.split(",")
            assert cells[d_col].strip().lower() not in ("nan", "-nan", "inf", "-inf"), line[:120]

    assert bracketed >= 2, (
        f"expected the two crossing-bracket rows per lap to carry a NaN odometer; saw "
        f"{bracketed} — if the builder stopped using NaN this test is no longer measuring "
        f"anything and must be rewritten against whatever replaced it")
    print(f"test_a_cell_with_no_value_never_renders_as_the_word_nan OK — {checked} cells, "
          f"{bracketed} absent odometers rendered as {absent!r}")


# ---------------------------------------------------------------------------------- the CSV
def test_the_csv_carries_every_row_at_full_precision():
    """Copy as CSV is the escape hatch, so it has to be LOSSLESS where the display is not: the
    panel rounds a coordinate to 7 places to be readable, and the CSV must still hand back the
    double. A CSV that rounded too would be a picture of the data."""
    s = _with_sectors(_session())
    p = s.sector_split_provenance(s.best_lap_id(), 0)
    lines = p.to_csv().splitlines()
    header = lines.index(",".join(p.samples.columns))
    body = lines[header + 1: header + 1 + len(p.samples.rows)]
    assert len(body) == len(p.samples.rows), (len(body), len(p.samples.rows))
    lat_col = provenance.FIX_COLUMNS.index("lat")
    for row, line in zip(p.samples.rows, body, strict=True):
        assert float(line.split(",")[lat_col]) == row[lat_col], "a lat must round-trip exactly"
    assert f"value,{p.value!r}" in p.to_csv(), "the value itself goes out at full precision"
    assert p.method in p.to_csv() or p.method.replace(",", "") in p.to_csv()
    print(f"test_the_csv_carries_every_row_at_full_precision OK — {len(lines)} lines, "
          f"{len(p.samples.rows)} fixes, lat round-trips")


def test_the_csv_quotes_a_field_that_contains_a_comma():
    """The method sentences contain commas and the captions contain them too, so the writer has
    to quote — otherwise one pasted inspection silently gains columns."""
    table = provenance.Table(caption="a, caption", columns=("x",), formats=("{}",),
                             rows=(('has "quotes", and a comma',),))
    line = table.csv_lines()[-1]
    assert line == '"has ""quotes"", and a comma"', line
    assert table.csv_lines()[0] == '"a, caption"'
    print("test_the_csv_quotes_a_field_that_contains_a_comma OK")


# ------------------------------------------------------------------------ degenerate inputs
def test_a_lap_too_short_to_explain_declines_rather_than_guesses():
    """The menu's contract is that a number is either explicable or not offered. A lap with too
    few fixes to carry two crossing chords returns None all the way up, and the three call sites
    all read that as "do not open"."""
    s = _session()
    # An out-of-range lap materialises as an empty Lap, which is short of the four rows a
    # crossing-chord table needs — so it declines at the carrier, before any arithmetic.
    assert s._lap_fixes(10_000) is None
    assert s.lap_time_provenance(10_000) is None
    assert s.sector_split_provenance(s.best_lap_id(), 99) is None
    assert s.corner_best_provenance(9_999) is None
    print("test_a_lap_too_short_to_explain_declines_rather_than_guesses OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PROVENANCE TESTS PASSED")
