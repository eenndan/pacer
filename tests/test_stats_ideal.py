"""The Stats page's IDEAL LAP section (studio/stats_ideal.py), on the real `StatsView`.

Split out of tests/test_stats.py with the section itself (ARCH-3): one of these tests sweeps 240
composites through a whole `StatsView` each and was 59 % of that file's run time, so as its own
CTest registration it runs BESIDE the rest of the page's tests under `-j4` instead of after them.

Pins, on the stub session `test_stats._fake_view_session` builds (offscreen Qt, no telemetry file):
  * the remainder note ADDS UP in the reader's own printed numbers — swept over composites whose
    gains carry a third decimal, with the retired raw-float spelling as its negative control;
  * the decomposition is ranked by gain × the share of laps that matched it, not by gain; donors
    are 1-based; a row rings its corner on the map; every cell carries its row's arithmetic;
  * the `IdealSample` disclosure (lap count on the tile, donors and partition on the line under
    the tiles, the mechanism on both tooltips) moves with the number;
  * the block hides as a unit when one lap won every segment, with no corner partition, and on a
    zero-lap recording;
  * both tiles take the provisional / degraded timing mute; the measured PACE tiles do not.
Run: QT_QPA_PLATFORM=offscreen python tests/test_stats_ideal.py
"""
import os
import re
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _qtapp import themed_app  # noqa: E402

# The tiles' mute cue is font-resolution behaviour, so this runs in the app's real regime, as
# tests/test_stats.py does (see tests/_qtapp.py).
_APP = themed_app()

from test_stats import _fake_view_session  # noqa: E402  (the page's shared stub session)


def _app():
    return _APP


_IDEAL_NOTE_RE = re.compile(
    r"These (\d+) segments hold (-?\d+\.\d\d) s of the (-?\d+\.\d\d) s; "
    r"the other (\d+) hold (-?\d+\.\d\d) s between them")


def _ideal_page_numbers(v):
    """Every number the IDEAL LAP block prints, read off the RENDERED widgets: the `Gain (s)`
    cells, the note's three figures, and the gap tile. Strings only — no session accessor — so
    this measures what a reader can add up, not what the model believes."""
    t = v.ideal.table
    cells = [float(t.item(r, 1).text()) for r in range(t.rowCount())]
    m = _IDEAL_NOTE_RE.search(v.ideal.note.text())
    assert m, f"the note's shape changed; this guard cannot read it: {v.ideal.note.text()!r}"
    tile = v.ideal.t_gap.value.text()
    assert tile.endswith(" s") and tile[0] == "-", tile
    return SimpleNamespace(cells=cells, n_shown=int(m[1]), shown=float(m[2]), gap=float(m[3]),
                           n_rest=int(m[4]), rest=float(m[5]), tile=abs(float(tile[:-2])))


def _assert_ideal_page_adds_up(v):
    """F1 — THE PAGE ADDS UP IN THE READER'S OWN NUMBERS, at 2 dp, on the surfaces themselves.

    Three claims, none of which the model can satisfy on its own behalf:
      * the note's "these N segments hold X s" IS the sum of the printed cells;
      * X + the stated remainder IS the note's own total;
      * that total IS the number on the gap tile.

    Written structurally rather than as a pinned string because the pinned string is exactly how
    this shipped broken: `tests/test_stats.py` asserted "These 2 segments hold 0.48 s of the
    0.50 s" over a fixture whose gains were 0.28 and 0.20 — numbers that round cleanly, so
    summing the raw floats and summing the printed cells agreed and the guard could not tell the
    two spellings apart. On all four of the owner's real recordings they disagree."""
    n = _ideal_page_numbers(v)
    assert n.n_shown == len(n.cells), (n.n_shown, n.cells)
    assert round(sum(n.cells), 2) == n.shown, (
        f"the note says {n.n_shown} segments hold {n.shown:.2f} s, but the cells above it print "
        f"{n.cells} = {round(sum(n.cells), 2):.2f}")
    assert round(n.shown + n.rest, 2) == n.gap, (n.shown, n.rest, n.gap)
    assert n.gap == n.tile, (
        f"the note's total {n.gap:.2f} s is not the tile's {n.tile:.2f} s")
    return n


def test_the_ideal_note_adds_up_in_the_numbers_on_screen():
    """F1, swept rather than sampled: 240 composites whose gains carry a THIRD decimal, which is
    where the two spellings of "the total" come apart.

    The shipped note summed the raw floats. Measured on the real recordings that put a 1.40 s
    column under a 1.39 s sentence (D24, 3 chapters) and a 0.92 s column under a 0.94 s one
    (Sandown chapter 1) — a reader adding the cells and the stated remainder landed a penny off
    the tile in either direction. One fixture cannot show that (the previous one could not), so
    this sweeps the rounding space and carries its own NEGATIVE CONTROL: the retired spelling is
    re-derived here from the same rows and asserted to fail on a real share of the draws.

    Every number is read back off the rendered widgets by `_assert_ideal_page_adds_up`."""
    _app()
    from studio.corner_model import SegmentBests
    from studio.stats_ideal import IDEAL_GAIN_FLOOR
    from studio.stats_panel import StatsView
    rng = np.random.default_rng(20260906)
    n_seg = 7                      # 3 corners -> 2N+1 segments, the smallest realistic plan
    idx = np.arange(n_seg)
    old_spelling_failures = 0
    checked = 0
    for _ in range(240):
        gains = np.round(rng.uniform(0.0, 0.30, n_seg), 3)
        base = np.full(n_seg, 3.0)
        # Two donors, so the block is never the single-donor state: lap 0 owns the even segments,
        # lap 2 the odd ones, and lap 1 (the subject / best lap) is `gains` slower everywhere.
        times = np.stack([base + np.where(idx % 2 == 0, 0.0, 0.5),
                          base + gains,
                          base + np.where(idx % 2 == 1, 0.0, 0.5)])
        sb = SegmentBests(
            labels=[f"s{j}" for j in range(n_seg)], cids=[1, 2, 3], lap_ids=[0, 1, 2],
            times=times, admitted=np.ones(times.shape, bool),
            resolved=np.ones(times.shape, bool),
            bests=[float(c.min()) for c in times.T],
            donors=[int(times[:, j].argmin()) for j in range(n_seg)],
            s_edges=list(np.linspace(0.0, 1.0, n_seg + 1)),
            donor_span=[(0.0, 0.0)] * n_seg)
        if sb.single_donor_id() is not None:
            continue
        s = _fake_view_session()
        s.ideal_segment_bests = lambda sb=sb: sb
        s.ideal_total = lambda sb=sb: sb.total
        s.theoretical_best = lambda sb=sb: sb.total
        s.ideal_donor_lap_id = lambda sb=sb: sb.single_donor_id()
        v = StatsView(s)
        if v.ideal.table.rowCount() == 0:
            continue               # every gain fell under the display floor: no plan to check
        checked += 1
        _assert_ideal_page_adds_up(v)
        # NEGATIVE CONTROL — the retired spelling, on these very rows.
        shown = [g for g in gains if g >= IDEAL_GAIN_FLOOR]
        if f"{sum(shown):.2f}" != f"{round(sum(round(float(g), 2) for g in shown), 2):.2f}":
            old_spelling_failures += 1
        v.deleteLater()
    assert checked >= 200, checked
    assert old_spelling_failures >= 20, (
        f"only {old_spelling_failures} of {checked} draws separate the two spellings — this sweep "
        f"is not exercising the rounding it exists to pin")
    print(f"test_the_ideal_note_adds_up_in_the_numbers_on_screen OK ({checked} composites; the "
          f"retired raw-float spelling misses the printed column on {old_spelling_failures})")


def test_ideal_decomposition_table_is_a_plan_not_a_taunt():
    """The IDEAL LAP table (N8): where the gap lives, on which lap, and how repeatable it is.

    Pinned on the hand-built composite in `_fake_segment_bests`, whose numbers are:

        row  segment      gain   beat   donor   priority
        1    C2          0.204    3/3   lap 3   0.204   <- smaller, every lap matched it
        2    C1          0.284    2/3   lap 1   0.189   <- BIGGER, matched once
        -    C1 → C2     0.024    2/3   lap 1   under the 0.05 s floor
        -    S/F → C1    0.000    3/3   lap 1   under the floor
        -    C2 → S/F    0.000    3/3   lap 1   under the floor

    So the table leads with the smaller gain, and a raw-gain order would lead with the bigger
    one. That inversion is the whole feature: measured on the owner's recordings D24's largest
    single gain (C2, 0.213 s, matched on 42 of 65 laps) and Sandown's (C5, 0.224 s, 13 of 59)
    rank differently for exactly this reason, and Sandown's biggest falls to third."""
    _app()
    from studio.stats_ideal import IDEAL_GAIN_FLOOR
    from studio.stats_panel import RING_ROLE, StatsView
    v = StatsView(_fake_view_session())
    t = v.ideal.table
    assert t.rowCount() == 2, [t.item(r, 0).text() for r in range(t.rowCount())]
    labels = [t.item(r, 0).text() for r in range(t.rowCount())]
    assert set(labels) == {"C1", "C2"}, labels
    assert labels == ["C2", "C1"], labels          # the ranking, not the gain order
    gains = {t.item(r, 0).text(): float(t.item(r, 1).text()) for r in range(t.rowCount())}
    assert gains == {"C1": 0.28, "C2": 0.20}, gains
    beats = {t.item(r, 0).text(): t.item(r, 2).text() for r in range(t.rowCount())}
    assert beats == {"C1": "2 / 3", "C2": "3 / 3"}, beats
    # The donor lap is 1-BASED on screen (the app-wide rule) — lap id 0 prints as "1".
    donors = {t.item(r, 0).text(): t.item(r, 3).text() for r in range(t.rowCount())}
    assert donors == {"C1": "1", "C2": "3"}, donors
    # Rows point the map at the corner they name (a straight would point at the corner feeding
    # it); the rows below the floor are not on screen at all.
    assert [t.item(r, 0).data(RING_ROLE) for r in range(t.rowCount())] == [
        int(lbl[1:]) for lbl in labels]
    assert IDEAL_GAIN_FLOOR == 0.05
    # THE NOTE CLOSES THE ARITHMETIC — IN THE READER'S OWN NUMBERS. The tile says -0.51 s; the two
    # printed cells add to 0.48 and the note accounts for the remaining three segments (0.03 s),
    # so 0.48 + 0.03 = 0.51 = the tile. A top-N list under a total that does not add up is the
    # defect this line exists to prevent, and summing the RAW gains (0.488 -> "0.49") is how it
    # shipped anyway on all four real recordings — see `_fake_segment_bests` for why the third
    # decimal is in this fixture.
    note = v.ideal.note.text()
    assert "These 2 segments hold 0.48 s of the 0.51 s" in note, note
    assert "the other 3 hold 0.03 s between them" in note, note
    assert "Ranked by gain" in note, note
    # THE SAMPLE moved OFF this note and onto its own line between the tiles and the table, so it
    # sits with the numbers it qualifies instead of under ten rows of decomposition. It is still
    # printed exactly once on the block: the note must not have kept a copy.
    assert "Stitched from 2 of your 3 clean laps" in v.ideal.sample.text(), v.ideal.sample.text()
    assert "Stitched from" not in note, note
    # ...and the same three numbers read STRUCTURALLY off the rendered surfaces, so this guard
    # keeps working when the fixture changes and cannot go blind again on a fixture whose gains
    # happen to round cleanly.
    _assert_ideal_page_adds_up(v)
    # Every cell of a row carries that row's own arithmetic, because NEITHER visible column is
    # sorted — the order is their product, and the CORNERS table's marked-loss column is on
    # record in this app as unreadable for exactly that reason.
    tip = t.item(0, 0).toolTip()
    assert "Ranked 1 of 2 by" in tip, tip
    assert t.item(0, 1).toolTip() == tip and t.item(0, 3).toolTip() == tip
    # NEGATIVE CONTROL: the retired ordering really would have got it wrong here.
    assert sorted(gains, key=lambda k: -gains[k]) == ["C1", "C2"], gains
    assert labels[0] == "C2", "a gain-ordered table leads with C1; this one must not"
    print("test_ideal_decomposition_table_is_a_plan_not_a_taunt OK")


def test_the_ideal_says_what_it_was_minimised_over_where_a_reader_sees_it():
    """THE IDEAL IS AN ORDER STATISTIC, AND THE PAGE NOW SAYS SO ON THE SURFACE, not only on hover.

    `SegmentBests.total` is a sum of per-segment minima, so it falls as a session accumulates laps
    and moves again when the corner partition is re-cut. Measured over random subsets of the
    owner's five recordings it falls 0.160–0.742 s per DOUBLING of lap count with no plateau, and
    on Sandown 3h's three chapters the gap tile reads `-0.61 s` over 5 clean laps and `-1.27 s` over 62 —
    the same driving, the same recording. Before this, the tile,
    the caption and the hero all printed the number with nothing beside it and the counts lived
    only in the last sentence of a note under a ten-row table.

    Three placements, and each is load-bearing for a different reader:
      * the TILE CAPTION carries the lap count, so the number and its sample cannot be read apart
        — the same shape the measured `median · N clean laps` tile beside it already uses;
      * the SAMPLE LINE between the tiles and the table carries the donor count and the PARTITION
        (N corners + N+1 straights), which is what visibly changes when a start/finish-line drag
        re-detects the corners;
      * the TOOLTIPS carry the mechanism and the measured rate, on BOTH tiles, because it is one
        fact about both numbers.

    And the sample appears exactly ONCE on the block: the remainder note used to open with it and
    must not have kept a copy (two surfaces stating one fact is how they drift)."""
    _app()
    from studio.stats_ideal import IDEAL_SAMPLE_TOOLTIP
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    sb = v.session.ideal_segment_bests()
    smp = sb.sample
    assert (smp.donors, smp.laps, smp.corners, smp.segments) == (2, 3, 2, 5), smp

    assert v.ideal.t_theoretical.caption.text() == "theoretical best · 3 laps", \
        v.ideal.t_theoretical.caption.text()
    line = v.ideal.sample.text()
    assert "Stitched from 2 of your 3 clean laps" in line, line
    # N corners and N+1 straights, printed as `segments - corners` so the sentence stays true if
    # the partition ever stops being 2N+1 rather than printing a derived lie.
    assert "2 corners and 3 straights" in line, line
    assert line.count("Stitched from") == 1, line
    assert "Stitched from" not in v.ideal.note.text(), v.ideal.note.text()

    for tile in (v.ideal.t_theoretical, v.ideal.t_gap):
        assert IDEAL_SAMPLE_TOOLTIP.strip() in tile.toolTip(), tile.toolTip()
        assert "per doubling of lap count" in tile.toolTip(), tile.toolTip()
    # …and the retired copy is still gone from the theoretical tile (the #211 guard, re-checked
    # here because this test rewrote that constant).
    for dead in ("best sector", "sector splits"):
        assert dead not in v.ideal.t_theoretical.toolTip(), dead

    # SINGULARS. `corners` and `straights` and `laps` all have 1 as a legal value, and this page
    # has shipped "median · 1 clean laps" once already.
    # The pluralizer moved to _signal (the exported report prints the same sentence and cannot
    # import a Qt view); IdealSample.sentence() is now the ONE place that composes it.
    from studio._signal import plural
    assert plural(1, "corner") == "1 corner"
    assert plural(2, "corner") == "2 corners"

    # THE LINE FOLLOWS THE NUMBER. Restrict the composite to two laps — what a shorter recording
    # does — and the caption, the line and the tile all move together, in the same frame.
    from studio.corner_model import SegmentBests
    # Laps 1 and 2 — it must keep the BEST lap (the subject the decomposition is measured against,
    # which `_clean_lap_ids` guarantees by appending it) and it must keep two distinct donors,
    # or the block hides instead of re-rendering and this guard would pass on a stale caption.
    keep, times = [1, 2], sb.times[1:]
    donors = [keep[int(times[:, j].argmin())] for j in range(times.shape[1])]
    two = SegmentBests(labels=sb.labels, cids=sb.cids, lap_ids=keep,
                       times=times, admitted=sb.admitted[1:], resolved=sb.resolved[1:],
                       bests=[float(c.min()) for c in times.T], donors=donors,
                       s_edges=sb.s_edges, donor_span=sb.donor_span)
    assert two.single_donor_id() is None and len(two.donor_ids()) == 2, two.donor_ids()
    was_gap = v.ideal.t_gap.value.text()
    v.session.ideal_segment_bests = lambda: two
    v.session.ideal_total = lambda: two.total
    v.session.ideal_donor_lap_id = lambda: two.single_donor_id()
    v.refresh()
    assert v.ideal.t_theoretical.caption.text() == "theoretical best · 2 laps", \
        v.ideal.t_theoretical.caption.text()
    assert "Stitched from 2 of your 2 clean laps" in v.ideal.sample.text(), v.ideal.sample.text()
    # Fewer laps, a SLOWER ideal and therefore a smaller gap — the property the disclosure exists
    # for, on the rendered strings rather than on the model.
    assert two.total >= sb.total, (two.total, sb.total)
    assert abs(float(v.ideal.t_gap.value.text()[:-2])) < abs(float(was_gap[:-2])), (
        was_gap, v.ideal.t_gap.value.text())
    print("test_the_ideal_says_what_it_was_minimised_over_where_a_reader_sees_it OK")


def test_ideal_block_hides_when_it_would_duplicate_a_lap_you_drove():
    """The two states the block must NOT print, and they are the only two.

      * ONE lap won every segment — the "ideal" IS that lap, so a tile would be a byte-identical
        duplicate of the ★ best. (`ideal_donor_lap_id()` names it; the house precedent is to hide
        a degenerate synthesized target, not to print it.)
      * no corner partition at all — the "partition" is the whole lap and its minimum is the best
        lap time again, the original defect.

    Sector lines are absent in BOTH fakes and present in neither hide reason, which is the point:
    the gate moved off `sector_count()` entirely."""
    _app()
    from studio.stats_panel import StatsView
    one = StatsView(_fake_view_session(single_donor=True))
    assert one.session.ideal_donor_lap_id() is not None
    assert one.ideal.heading.isHidden() and one.ideal.t_theoretical.isHidden()
    assert one.ideal.t_gap.isHidden() and one.ideal.table.isHidden()
    assert one.ideal.table.rowCount() == 0 and one.ideal.note.text() == ""
    assert one.ideal.note.isHidden()
    # …and the SAMPLE line with them. A sentence counting the laps of a composite that is not on
    # screen is the same "tile left behind under a hidden heading" defect, one widget over.
    assert one.ideal.sample.isHidden() and one.ideal.sample.text() == ""

    none = StatsView(_fake_view_session(ideal=False))
    assert none.session.ideal_segment_bests() is None
    assert none.ideal.heading.isHidden() and none.ideal.table.isHidden()

    # …and the zero-lap recording, where there is no best lap to decompose against.
    empty = StatsView(_fake_view_session(laps=False))
    assert empty.ideal.heading.isHidden() and empty.ideal.t_theoretical.isHidden()
    print("test_ideal_block_hides_when_it_would_duplicate_a_lap_you_drove OK")


def test_ideal_targets_mute_with_the_timing_they_borrow_authority_from():
    """Both IDEAL LAP tiles are SYNTHESIZED, so both take `set_target_tile`'s provisional
    treatment — muted, italic, and carrying the start-line note above their own tooltip — while
    the measured PACE tiles beside them stay upright, because those ARE laps you drove.

    The TABLE is deliberately not muted: its cells are measured segment times and differences
    between them, the same standing the CORNERS and STRAIGHTS reports have, and the page's own
    amber banner already qualifies the page (ledger A26/A7 — a page that hides the map carries
    its own trust chrome)."""
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(verified=False))
    assert v.provisional_banner.isVisible() or not v.isVisible()   # the page's own chrome
    assert v.ideal.t_theoretical.value.font().italic()
    assert v.ideal.t_gap.value.font().italic()
    assert v.t_rolling.value.font().italic()
    assert not v.t_best.value.font().italic(), "a lap you DROVE must not be muted"
    assert not v.t_median.value.font().italic()
    for tile in (v.ideal.t_theoretical, v.ideal.t_gap, v.t_rolling):
        assert "start/finish line" in tile.toolTip(), tile.toolTip()
    assert "not a lap you drove" in v.ideal.t_theoretical.toolTip()
    # …and it is reversible: the same fake with a verified line renders upright.
    ok = StatsView(_fake_view_session())
    assert not ok.ideal.t_theoretical.value.font().italic()
    assert not ok.ideal.t_gap.value.font().italic()
    print("test_ideal_targets_mute_with_the_timing_they_borrow_authority_from OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} IDEAL LAP TESTS PASSED")
