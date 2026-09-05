"""Accessibility cues + the "new personal best!" moment (feat/accessible-cues-pb-moment).

Covers the two CPO blind spots this PR closes, all on synthetic data (no pacer, no telemetry
file), offscreen for the Qt bits:

  A. COLOUR-BLIND-SAFE CUES
     * non-colour redundancy: the Δ readout carries a ▲/▼ direction arrow paired with the signed
       number (ahead/behind never depends on hue), the lap table's best-lap + best-sector cells
       carry a ★ mark, and the grip-map legend marks the at-limit extreme with ⚠;
     * the palette SELECTOR in theme.py — one source of truth: set_palette flips delta_colour,
       best_lap_colour/best_sector_colour and the rainbow endpoints between the default red/green
       and the colour-blind-safe blue/orange, and the default palette is byte-identical to before;
     * the LapTable repaints its best cells through the selector on a palette flip, and persists via
       prefs.

  B. "NEW PERSONAL BEST!" MOMENT (library.pb_moment / pb_moment_for / pb_moment_text)
     * fires when a session BEATS the track's prior PB on VERIFIED timing;
     * does NOT fire on provisional/unverified timing, on a first-ever session (a gentler "first"
       instead), on a tie, or on a slower lap;
     * the toast wording + its "See your progress →" link routing to the progression surface.

Run: python tests/test_accessible_cues.py
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import data_quality, library, prefs, theme  # noqa: E402
from studio._signal import fmt_time  # noqa: E402
from studio.lap_table import (  # noqa: E402
    BEST_LAP_MARK,
    BEST_SECTOR_MARK,
    COLLAPSE_ICON,
    EXCLUDED_ICON,
    EXPAND_ICON,
    LapTable,
)


# ===================================================================== A. non-colour Δ redundancy
def test_delta_arrow_and_run_carry_direction_without_colour():
    """The Δ ahead/behind meaning survives greyscale: ahead (Δ<0) → ▲, behind (Δ>0) → ▼, even → no
    arrow. format_delta_run pairs the arrow with the already-signed value, and the even dead-band
    (the byte-identical existing readout) still emits NO arrow."""
    assert theme.delta_arrow(-0.30) == theme.DELTA_AHEAD_ARROW == "▲"
    assert theme.delta_arrow(0.30) == theme.DELTA_BEHIND_ARROW == "▼"
    assert theme.delta_arrow(0.0) == ""
    assert theme.delta_arrow(None) == ""
    # The signed number AND the arrow agree — doubly non-colour.
    ahead = theme.format_delta_run(-0.32)
    behind = theme.format_delta_run(0.32)
    assert ahead == "Δ -0.32 s ▲", ahead
    assert behind == "Δ +0.32 s ▼", behind
    # Even Δ (dead-band): no arrow, so the existing readout is unchanged.
    assert theme.format_delta_run(0.0) == "Δ +0.00 s", theme.format_delta_run(0.0)
    # arrow=False for word-labelled contexts (tooltips) drops the glyph.
    assert theme.format_delta_run(-0.32, arrow=False) == "Δ -0.32 s"
    # The combined live readout inherits the arrow (it composes format_delta_run).
    assert theme.format_delta_speed(-0.20, 100.0, 2)[0].startswith("Δ -0.20 s ▲")
    print("test_delta_arrow_and_run_carry_direction_without_colour OK")


# ===================================================================== A. palette selector
def test_palette_selector_is_single_source_and_swaps_semantic_hues():
    """theme.set_palette is the one switch: it flips delta_colour + best_lap/best_sector colours +
    the rainbow endpoints between the default red/green/purple and the colour-blind blue/orange/teal.
    The default palette is byte-identical to the raw C tokens (no change for existing users)."""
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        assert theme.active_palette() == theme.PALETTE_STANDARD
        # Default == the original tokens exactly.
        assert theme.delta_colour(-1.0) == theme.C.ahead
        assert theme.delta_colour(1.0) == theme.C.behind
        assert theme.best_lap_colour() == theme.C.ahead
        assert theme.best_sector_colour() == theme.C.best
        std_lo, std_hi = theme.rainbow_colors()[0], theme.rainbow_colors()[-1]

        theme.set_palette(theme.PALETTE_COLORBLIND)
        assert theme.active_palette() == theme.PALETTE_COLORBLIND
        cb_ahead = theme.ahead_colour()
        cb_behind = theme.behind_colour()
        # The CB pair is DIFFERENT from the default and from each other.
        assert cb_ahead != theme.C.ahead and cb_behind != theme.C.behind
        assert cb_ahead != cb_behind
        assert theme.delta_colour(-1.0) == cb_ahead
        assert theme.delta_colour(1.0) == cb_behind
        assert theme.best_lap_colour() == cb_ahead  # best lap == success == ahead hue
        # best-sector is distinct from best-lap so the two "best" cues never collide.
        assert theme.best_sector_colour() not in (cb_ahead, cb_behind, theme.C.best)
        # The rainbow endpoints followed the palette too (so the map matches the readout).
        assert theme.rainbow_colors()[0] == cb_behind
        assert theme.rainbow_colors()[-1] == cb_ahead
        assert (theme.rainbow_colors()[0], theme.rainbow_colors()[-1]) != (std_lo, std_hi)

        # The even dead-band never colours, in either palette.
        assert theme.delta_colour(0.0) is None
        # Unknown palette names fall back to STANDARD (never crash).
        theme.set_palette("nonsense")
        assert theme.active_palette() == theme.PALETTE_STANDARD
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_palette_selector_is_single_source_and_swaps_semantic_hues OK")


def test_colorblind_palette_pref_roundtrip(tmp_path=None):
    """The colour-blind toggle persists via prefs (default off), like the km/h/mph unit — a corrupt
    / missing file reads as off, never crashing."""
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "prefs.json")
    assert prefs.colorblind_palette(path) is False  # missing file → default off
    prefs.set_colorblind_palette(True, path)
    assert prefs.colorblind_palette(path) is True
    prefs.set_colorblind_palette(False, path)
    assert prefs.colorblind_palette(path) is False
    print("test_colorblind_palette_pref_roundtrip OK")


# ===================================================================== A. lap-table best marks
class _FakeLapSession:
    """The read surface LapTable touches: 3 laps, 1 sector line (2 S-columns), lap 1 the best lap,
    verified high-quality timing. The per-column minima are [33.8, 34.4] (lap 0's S1, lap 1's S2)."""

    timing_verified = True
    timing_quality = data_quality.TimingQuality()

    def __init__(self):
        self.splits = {0: [33.8, 36.2], 1: [34.0, 34.4], 2: [35.5, 35.7]}

    def lap_rows(self):
        return [{"idx": 0, "time": 70.0, "dist": 1001.0, "entry": 51.0},
                {"idx": 1, "time": 68.4, "dist": 998.0, "entry": 52.5},
                {"idx": 2, "time": 71.2, "dist": 1003.0, "entry": 49.0}]

    def sector_count(self):
        return 1

    def lap_sector_splits(self, lap_id):
        return self.splits[lap_id]

    def session_best_splits(self):
        return [min(sp[i] for sp in self.splits.values()) for i in range(2)]

    def theoretical_best(self):
        return 68.2

    def best_rolling_lap(self):
        return 68.3

    def best_lap_id(self):
        return 1

    def dropout_lap_ids(self):
        return set()


def _lap_cell(table, lap_id):
    """The Lap-column cell (col 0) whose lap id == lap_id."""
    for r in range(table.table.rowCount()):
        if table._lap_id(r) == lap_id:
            return table.table.item(r, 0)
    raise AssertionError(f"lap {lap_id} not in table")


def _sector_cells(table):
    """Every S-split cell (cols after the base 4 columns), any row."""
    from studio.lap_table import COLUMNS
    n = table._n_split_cols()
    return [table.table.item(r, len(COLUMNS) + i)
            for r in range(table.table.rowCount()) for i in range(n)]


def test_lap_table_best_cells_carry_non_colour_star_marks():
    """The best-lap Lap cell carries a ★ (after any ▶) and every session-best split cell a trailing
    ★, so "this is the best" reads WITHOUT the green/purple hue. Non-best rows carry no ★.

    WHAT THE ★ IS FOR, and therefore where it does NOT belong. A mark is owed wherever a tint picks
    ONE cell out of a column of comparable ones: which of these 21 laps is the best, which of these
    splits was the session's — questions whose answer is carried by hue alone and is lost in
    greyscale. Stats ▸ SECTORS "Best" was filed as the app's one un-★-ed session-best cue, and on
    inspection it is a different shape: the tint there covers the WHOLE column, because every cell
    in that column IS a session best by construction. Nothing is being distinguished from its
    neighbours, the meaning is already in the header, and four ★s marking a tautology would spend
    the mark where it says nothing.

    So the convention is stated as: colour-only is a defect when it RANKS WITHIN a column, not when
    it LABELS a whole one. The column-wide case is measured below rather than taken on trust — if
    a future refresh ever tints only the fastest sector, this fails and the ★ is owed."""
    table = LapTable(_FakeLapSession())
    # Best lap (id 1) Lap cell shows the ★ mark; the two non-best laps do not.
    assert BEST_LAP_MARK.strip() in _lap_cell(table, 1).text(), _lap_cell(table, 1).text()
    assert BEST_LAP_MARK.strip() not in _lap_cell(table, 0).text()
    assert BEST_LAP_MARK.strip() not in _lap_cell(table, 2).text()
    # Exactly the two session-best split cells (33.8 and 34.4) carry the trailing ★.
    starred = [it.text() for it in _sector_cells(table) if it and it.text().endswith(BEST_SECTOR_MARK)]
    assert len(starred) == 2, starred
    assert any(s.startswith("33.80") for s in starred) and any(s.startswith("34.40") for s in starred)
    print("test_lap_table_best_cells_carry_non_colour_star_marks OK")


def test_stats_sector_best_column_is_tinted_whole_and_says_so_on_hover():
    """The other half of the rule above, on the surface it exempts: Stats ▸ SECTORS "Best".

    Two things have to hold for the missing ★ to be a convention note rather than a lost cue.
    EVERY derivable cell in the column carries the tint (so the hue labels a column, not a winner),
    and the column's own header says what it is. Both are measured on the real StatsView. And the
    half that WAS a real gap — the cell said nothing at all on hover — is closed: each best cell now
    names itself, so the purple is explained where it is used rather than only in the tile above."""
    _APP  # noqa: B018
    from PySide6.QtGui import QColor
    from test_stats import _fake_view_session

    from studio.stats_panel import SECTOR_COLUMNS, StatsView
    from studio.widgets import DASH
    view = StatsView(_fake_view_session())
    t = view.sector_table
    assert t.rowCount() >= 2, t.rowCount()
    assert SECTOR_COLUMNS[1] == "Best", SECTOR_COLUMNS
    purple = QColor(theme.best_sector_colour()).name().upper()
    cells = [t.item(r, 1) for r in range(t.rowCount())]
    real = [c for c in cells if c is not None and c.text() != DASH]
    assert len(real) == t.rowCount(), "this fixture must give every sector a best"
    # COLUMN-WIDE: every one of them, not a subset — which is exactly why no ★ is owed.
    tinted = [c for c in real if c.foreground().color().name().upper() == purple]
    assert len(tinted) == len(real), (
        f"the Best column tints {len(tinted)} of {len(real)} cells — a tint that picks a SUBSET "
        f"ranks within the column and owes a non-colour mark (see the test above)")
    # ...and no neighbouring column is wearing the same hue, which would make it a row cue again.
    for col in (2, 3):
        for r in range(t.rowCount()):
            it = t.item(r, col)
            assert it is None or it.foreground().color().name().upper() != purple, (r, col)
    # The meaning is in the header and, now, on every cell.
    assert all(c.toolTip() for c in real), "a tinted cell with no words is a colour and nothing else"
    assert "Session-best" in real[0].toolTip() and "S1" in real[0].toolTip(), real[0].toolTip()
    assert BEST_SECTOR_MARK.strip() not in real[0].text(), (
        "the column-wide case deliberately carries no ★ — see the convention above")
    print("test_stats_sector_best_column_is_tinted_whole_and_says_so_on_hover OK")


def test_stats_worst_loss_cells_carry_a_mark_and_the_score_that_chose_them():
    """Stats ▸ CORNERS "Med loss": the worst-3 cue was hue and NOTHING else, over a ranking that is
    not a column on screen.

    Measured on D24: tinted and plain cells were identical in size, weight, family, alignment and
    format, and carried the same tooltip. The ranking is σ × median-loss — erratic AND slow — so on
    screen a tinted +0.09 sat directly under a plain +0.11, and a plain +0.10 beat the tinted +0.09.
    A reader saw a column that appeared to be ordered by its own numbers and was not, with no way
    to find out. Contrast was never the problem; meaning was.

    The fixture reproduces exactly that contradiction, and the test asserts the three things that
    make the cue survive it: a non-colour mark, the deciding SCORE on the cell it decided, and the
    numbers themselves untouched (the mark is a PREFIX because right alignment in a fixed-decimal
    column IS decimal alignment, and the sort key is the bare float)."""
    _APP  # noqa: B018
    from types import SimpleNamespace

    from PySide6.QtGui import QColor
    from test_stats import _fake_view_session

    from studio.lap_table import DROPOUT_MARK, NUM_ROLE
    from studio.stats import CornerReport
    from studio.stats_panel import WORST_LOSS_MARK, WORST_TINT_N, StatsView

    def corner(cid, loss, sigma):
        return CornerReport(cid=cid, direction=1, n=6, best_s=9.0, median_s=9.0 + loss,
                            sigma_s=sigma, median_loss_s=loss, apex_best_kmh=60.0,
                            apex_median_kmh=58.0, grip_median=0.8, score=sigma * loss)

    # C11 (+0.09, erratic) outranks C10 (+0.11, steady): the D24 contradiction, reproduced.
    report = [corner(1, 0.05, 0.05), corner(6, 0.12, 0.20), corner(7, 0.10, 0.05),
              corner(10, 0.11, 0.05), corner(11, 0.09, 0.20), corner(12, 0.16, 0.20)]
    sess = _fake_view_session()
    sess.corner_report = lambda: report
    # A phase triple on C11 only: the two tooltip lines must COMPOSE, not overwrite each other.
    sess.phase_report = lambda: SimpleNamespace(cids=[11], rows=[(0.03, 0.04, 0.02)], share=None)
    view = StatsView(sess)
    t = view.corners_table
    assert t.rowCount() == len(report), t.rowCount()

    behind = QColor(theme.behind_colour()).name().upper()
    by_cid = {}
    for r in range(t.rowCount()):
        cell = t.item(r, 4)
        by_cid[int(t.item(r, 0).data(NUM_ROLE))] = cell
    marked = {cid for cid, c in by_cid.items() if c.text().startswith(WORST_LOSS_MARK)}
    tinted = {cid for cid, c in by_cid.items()
              if c.foreground().color().name().upper() == behind}
    assert marked == tinted == {6, 11, 12}, (marked, tinted)
    assert len(marked) == WORST_TINT_N, marked
    # THE CONTRADICTION: a marked +0.09 and an unmarked +0.11 in the same column. Without the mark
    # the only difference between these two cells was hue; with it, greyscale still separates them.
    assert by_cid[11].text() == WORST_LOSS_MARK + "+0.09", by_cid[11].text()
    assert by_cid[10].text() == "+0.11", by_cid[10].text()
    assert by_cid[11].text().replace(WORST_LOSS_MARK, "") < by_cid[10].text()
    # ...and the number that caused it is ON the cell it caused, not only in the table's tooltip.
    tip = by_cid[11].toolTip()
    assert "0.20" in tip and "0.09" in tip and "0.018" in tip, tip
    assert "σ × median loss" in tip, tip
    assert "entry +0.03" in tip and "exit +0.02" in tip, "the phase triple must survive"
    assert by_cid[10].toolTip() == "", "an unmarked cell must not claim a score"
    # The mark is the app's existing attention glyph (the lap grid's dropout mark), so the shipped
    # font ledger in tests/test_glyph_vocabulary.py already covers it — no new codepoint arrives.
    assert WORST_LOSS_MARK.strip() == DROPOUT_MARK == "⚠"
    # PREFIX, not suffix: every cell in the column still ends at the same decimal place, and the
    # numeric sort key is untouched by the mark.
    assert {len(c.text().split("+")[-1]) for c in by_cid.values()} == {4}, (
        [c.text() for c in by_cid.values()])
    assert by_cid[11].data(NUM_ROLE) == 0.09
    print("test_stats_worst_loss_cells_carry_a_mark_and_the_score_that_chose_them OK")


def test_lap_table_best_star_survives_a_sort():
    """Sorting a column must not double-star or lose the best marks (the split text is rebuilt from
    the stored numeric key each highlight pass)."""
    from PySide6.QtCore import Qt
    table = LapTable(_FakeLapSession())
    table.table.sortByColumn(1, Qt.DescendingOrder)  # by Time, desc
    # Still exactly two starred split cells (no double-★, no loss).
    starred = [it.text() for it in _sector_cells(table) if it and it.text().endswith(BEST_SECTOR_MARK)]
    assert len(starred) == 2, starred
    for s in starred:
        assert not s.endswith(BEST_SECTOR_MARK + BEST_SECTOR_MARK.strip()), f"double-star: {s}"
    assert BEST_LAP_MARK.strip() in _lap_cell(table, 1).text()
    print("test_lap_table_best_star_survives_a_sort OK")


def test_lap_table_shows_a_banded_out_lap_in_the_excluded_strip():
    """A substantial lap the median band left out (a mis-segmented 921 m / 0:59 short lap) is
    surfaced in the muted ⊘ EXCLUDED strip — NOT injected into the sortable rows (where it would
    sort to the top as the 'fastest'), so a dropped lap isn't invisible.

    Declutter PR: the strip now ships COLLAPSED to a one-liner (the count in the header, the full
    list hidden), and expands to the per-lap detail on a header click. So the default state shows
    the count in the HEADER and an empty body; expanding reveals the lap line.

    The ⊘ and the ▸ that used to be IN that header string are now theme.icon() pixmaps beside it
    (they fell out of Inter, or were clipped by the 14 px line — see tests/test_glyph_vocabulary.py,
    which owns the glyph half). What this test still asserts is the NON-COLOUR cue's substance: the
    count is in the words, and the disclosure state is visible."""
    sess = _FakeLapSession()
    sess.excluded_lap_rows = lambda: [{"idx": 47, "time": 59.091, "dist": 921.0, "entry": 40.0}]
    table = LapTable(sess)
    # It is NOT one of the sortable lap rows ... (the internal lap id / row key stays 0-based)
    assert 47 not in [table._lap_id(r) for r in range(table.table.rowCount())]
    # ... the strip is visible (there's an excluded lap) and COLLAPSED by default: the header reads
    # "1 excluded …" beside the ⊘ mark, and the detail body is empty until expanded.
    assert not table._excluded_strip.isHidden()
    assert table._excluded_collapsed, "the excluded strip must ship collapsed (the one-liner)"
    header = table._excluded_header.text()
    assert "1 excluded" in header, header
    assert table._excluded_mark.glyph_name() == EXCLUDED_ICON, "the ⊘ cue must be on the strip"
    assert table._excluded_caret.glyph_name() == EXPAND_ICON, "collapsed → a 'will expand' caret"
    assert table._excluded_body.text() == "", "collapsed: the full list stays hidden"
    # Expand it (a header click): the full per-lap line appears, showing the 1-based lap NUMBER
    # (id 47 → "Lap 48", matching the table's Lap column — NOT the raw id) + time + distance, and the
    # header caret flips to the "will collapse" one.
    table._toggle_excluded_collapsed()
    assert not table._excluded_collapsed
    assert table._excluded_caret.glyph_name() == COLLAPSE_ICON
    body = table._excluded_body.text()
    assert "Lap 48" in body and "0:59.091" in body and "921" in body, body
    print("test_lap_table_shows_a_banded_out_lap_in_the_excluded_strip OK")


def test_lap_table_excluded_strip_hidden_on_a_clean_recording():
    """No excluded laps (the common case) → the strip is hidden, adding no chrome. The plain
    _FakeLapSession exposes no excluded_lap_rows, exercising the getattr fallback too."""
    table = LapTable(_FakeLapSession())
    assert table._excluded_strip.isHidden()
    print("test_lap_table_excluded_strip_hidden_on_a_clean_recording OK")


def test_lap_table_excluded_strip_menu_hide_is_orthogonal_to_collapse():
    """Declutter PR: View ▸ Show excluded laps fully hides the whole ⊘ strip (set_excluded_visible),
    orthogonally to its own collapse — a menu-hidden strip stays hidden regardless of collapse
    state, and re-showing it (when there ARE excluded laps) brings it back. The auto-hide when the
    session has no excluded laps still wins over 'shown'."""
    sess = _FakeLapSession()
    sess.excluded_lap_rows = lambda: [{"idx": 47, "time": 59.091, "dist": 921.0, "entry": 40.0}]
    table = LapTable(sess)
    assert not table._excluded_strip.isHidden(), "shown by default when there are excluded laps"

    # Menu-hide it entirely: the whole strip (header included) disappears.
    table.set_excluded_visible(False)
    assert table._excluded_strip.isHidden(), "the menu toggle must hide the whole strip"
    # Expanding while menu-hidden must not re-show it (the two flags are orthogonal).
    table._toggle_excluded_collapsed()
    assert table._excluded_strip.isHidden(), "menu-hidden stays hidden regardless of collapse"

    # Re-show via the menu: it comes back (there are still excluded laps).
    table.set_excluded_visible(True)
    assert not table._excluded_strip.isHidden(), "re-showing restores the strip"

    # A session with NO excluded laps auto-hides even when the menu flag is 'shown'.
    sess.excluded_lap_rows = lambda: []
    table.refresh()
    assert table._excluded_strip.isHidden(), "no excluded laps -> auto-hidden even when menu-shown"
    print("test_lap_table_excluded_strip_menu_hide_is_orthogonal_to_collapse OK")


def test_lap_table_best_colours_follow_the_palette_selector():
    """A palette flip recolours the best cells THROUGH theme's selector: the best-lap cell's
    foreground is best_lap_colour() and the best-sector cell's is best_sector_colour(), which change
    with set_palette — so the lap table honours the colour-blind option, from a single source."""
    from PySide6.QtGui import QColor

    from studio.lap_table import COLUMNS
    try:
        table = LapTable(_FakeLapSession())

        def _best_lap_fg():
            return _lap_cell(table, 1).foreground().color().name().upper()

        def _best_sector_fg():
            # lap 0's S1 (33.8) is a session-best split cell.
            for r in range(table.table.rowCount()):
                if table._lap_id(r) == 0:
                    return table.table.item(r, len(COLUMNS)).foreground().color().name().upper()
            raise AssertionError

        theme.set_palette(theme.PALETTE_STANDARD)
        table.refresh()
        assert _best_lap_fg() == QColor(theme.C.ahead).name().upper()
        assert _best_sector_fg() == QColor(theme.C.best).name().upper()

        theme.set_palette(theme.PALETTE_COLORBLIND)
        table.refresh()
        assert _best_lap_fg() == QColor(theme.best_lap_colour()).name().upper()
        assert _best_sector_fg() == QColor(theme.best_sector_colour()).name().upper()
        # And they genuinely changed from the default.
        assert _best_lap_fg() != QColor(theme.C.ahead).name().upper()
        assert _best_sector_fg() != QColor(theme.C.best).name().upper()
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_lap_table_best_colours_follow_the_palette_selector OK")


# ============================================ A2. every semantic surface follows the palette selector
# PR fix/palette-estimated-consistency: PR #48 wired the palette into only SOME surfaces. These pin
# that the brake/throttle band, the consistency PB dots, and the EXPORT delta cue now follow it too
# (not just the lap table / map / Δ readout), and that the always-on Opportunities panel + charts
# re-render on a flip. Default (STANDARD) output stays byte-identical (the accessors already return
# the standard values; we just stopped FREEZING them at import).
import numpy as np  # noqa: E402


class _FakeChartSession:
    """Minimal duck-typed session for a bare PlotsView: one flat lap curve, no reference/sectors."""

    def best_lap_id(self):
        return 0

    def has_reference(self):
        return False

    def lap_time(self, i):
        return 70.0

    def delta(self, ids, x_mode="distance"):
        sx = np.linspace(0.0, 200.0, 100)
        return 0, {0: (sx, np.full(100, 60.0))}, {0: (sx, np.zeros(100))}

    def delta_to_ideal(self, ids, x_mode="distance"):
        return None  # P7: no ideal envelope here → the Δ chart keeps its Δ-to-best baseline

    def sector_plot_positions(self, m):
        return []


def _bt_fill_colours(pv):
    """The (brake, throttle) FillBetweenItem brush hex names currently drawn in the band, upper-cased.
    The band draws the brake fill (min side) before the throttle fill (max side) per lap."""
    from pyqtgraph import FillBetweenItem
    fills = [it for it in pv._brake_throttle_items if isinstance(it, FillBetweenItem)]
    return [f.brush().color().name().upper() for f in fills]


def test_brake_throttle_band_colour_follows_the_palette():
    """The synthetic brake/throttle band's fills read theme.behind_colour()/ahead_colour() at DRAW
    time (not frozen C.behind/C.ahead at import), so a colour-blind flip recolours the band. Standard
    stays the original red/green; a flip changes BOTH fills."""
    _APP  # noqa: B018  (ensure the QApplication exists)
    from PySide6.QtGui import QColor

    from studio.plots_view import PlotsView
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        pv = PlotsView(_FakeChartSession())
        pv.set_laps([0])
        xs = np.linspace(0.0, 200.0, 100)
        inten = np.zeros(100)
        inten[20:40] = -0.9   # a braking stretch (fills toward "behind")
        inten[60:80] = 0.5    # a throttle stretch (fills toward "ahead")
        pv.set_brake_throttle([(xs, inten)])
        pv.brake_throttle_btn.setChecked(True)   # draw the band

        std = _bt_fill_colours(pv)
        assert len(std) == 2, std
        brake_std, thr_std = std
        # Standard palette == the original red/green (RGB unchanged; the fills carry an alpha).
        assert brake_std == QColor(theme.C.behind).name().upper()
        assert thr_std == QColor(theme.C.ahead).name().upper()

        theme.set_palette(theme.PALETTE_COLORBLIND)
        pv.refresh_palette()   # the fan-out redraw the app does on a flip
        brake_cb, thr_cb = _bt_fill_colours(pv)
        assert brake_cb == QColor(theme.behind_colour()).name().upper()
        assert thr_cb == QColor(theme.ahead_colour()).name().upper()
        # Genuinely changed — the CPO gap (the band stayed red/green) is closed.
        assert brake_cb != brake_std and thr_cb != thr_std
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_brake_throttle_band_colour_follows_the_palette OK")


def test_stats_spark_pb_colour_follows_the_palette():
    """The Stats page's trend sparkline (the retired consistency strip's successor) carries the
    PB dots + session-best baseline in the best/ahead hue via the accessors (not frozen
    C.ahead), re-penned on refresh_palette. Standard stays green; the colour-blind flip
    changes them to the palette's hue."""
    _APP  # noqa: B018
    from PySide6.QtGui import QColor
    from test_stats import _fake_view_session

    from studio.stats_panel import StatsView
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        sess = _fake_view_session()
        sess.lap_time_trend = lambda: [(0, 70.0), (1, 71.2), (2, 69.8)]  # lap 3 = a new PB
        view = StatsView(sess)
        assert view.spark.isVisibleTo(view), "the sparkline shows with >=2 clean laps"

        def _pb_brush():
            return view._spark_pb_dots.opts["brush"].color().name().upper()

        def _baseline_pen():
            return view._spark_baseline.pen.color().name().upper()

        assert _pb_brush() == QColor(theme.C.ahead).name().upper()
        assert _baseline_pen() == QColor(theme.C.ahead).name().upper()

        theme.set_palette(theme.PALETTE_COLORBLIND)
        view.refresh_palette()
        assert _pb_brush() == QColor(theme.best_lap_colour()).name().upper()
        assert _baseline_pen() == QColor(theme.best_lap_colour()).name().upper()
        assert _pb_brush() != QColor(theme.C.ahead).name().upper()  # actually changed
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_stats_spark_pb_colour_follows_the_palette OK")


def test_export_delta_colour_follows_the_palette():
    """The burned-in video export's Δ cue follows the active palette: standard → the punchy vivid
    green/red EXPORT pair; colour-blind → a VIVID deuteranopia-safe blue/orange pair (same
    legibility intent, swapped hue axis). Default (no palette arg) is byte-identical to before."""
    from studio import export_video as ev
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        # Standard: unchanged vivid green/red (byte-identical to pre-PR).
        assert ev.export_delta_colour(-0.20) == ev.EXPORT.ahead
        assert ev.export_delta_colour(+0.20) == ev.EXPORT.behind
        assert ev.export_semantic_pair() == (ev.EXPORT.ahead, ev.EXPORT.behind)

        # Colour-blind: a DIFFERENT, still-vivid pair (blue/orange), distinct from each other + green/red.
        ahead_cb, behind_cb = ev.export_semantic_pair(theme.PALETTE_COLORBLIND)
        assert ahead_cb != ev.EXPORT.ahead and behind_cb != ev.EXPORT.behind
        assert ahead_cb != behind_cb
        assert ev.export_delta_colour(-0.20, theme.PALETTE_COLORBLIND) == ahead_cb
        assert ev.export_delta_colour(+0.20, theme.PALETTE_COLORBLIND) == behind_cb
        # Selecting from the ACTIVE palette (the worker path passes OverlayConfig.palette, but the
        # default None resolves theme.active_palette()).
        theme.set_palette(theme.PALETTE_COLORBLIND)
        assert ev.export_delta_colour(-0.20) == ahead_cb
        assert ev.export_delta_colour(+0.20) == behind_cb
        # Neutral/dead-even stays white in both palettes.
        assert ev.export_delta_colour(None, theme.PALETTE_COLORBLIND) == ev.EXPORT.neutral
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_export_delta_colour_follows_the_palette OK")


def test_overlay_config_carries_the_palette():
    """OverlayConfig threads the active palette into the render (a worker QThread mustn't read the
    global live), defaulting to STANDARD; _paint_readout selects the export Δ pair from it."""
    from studio import export_video as ev
    assert ev.OverlayConfig().palette == theme.PALETTE_STANDARD
    cfg = ev.OverlayConfig(palette=theme.PALETTE_COLORBLIND)
    assert cfg.palette == theme.PALETTE_COLORBLIND
    print("test_overlay_config_carries_the_palette OK")


def test_opportunities_panel_rerenders_on_palette_flip():
    """The always-on Opportunities panel's time-lost cells go through theme.delta_colour, so a
    palette flip must re-render them (the CPO gap: the coaching front-door stayed red/green). Here we
    pin that a refresh() after a flip repaints the lost cell in the new 'behind' hue."""
    _APP  # noqa: B018
    from PySide6.QtGui import QColor

    from studio import coaching
    from studio.coaching_panel import OpportunitiesPanel

    # A tiny opportunities set with one losing corner (time_lost > 0 -> the 'behind' hue). Real
    # dataclasses so reason_sentence / _reason_cell read them exactly as in production.
    reason = coaching.Reason(kind=coaching.REASON_APEX, contribution=0.35,
                             apex_speed_deficit=2.4, brake_extra_s=0.0, coast_extra_s=0.0,
                             sigma=0.12)
    phases = coaching.PhaseLoss(entry=0.1, apex=0.2, exit=0.05)
    opp = coaching.Opportunity(cid=3, direction=1, time_lost=0.35, entry_dist=40.0,
                               reason=reason, phases=phases)
    opps = coaching.Opportunities(rows=[opp], enough=True, n_laps=5, median_lap_id=2)

    class _S:
        def coaching_opportunities(self):
            return opps

        def coaching_brake_points(self):
            return {}

    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        panel = OpportunitiesPanel(_S())

        def _lost_fg():
            return panel.table.item(0, 1).foreground().color().name().upper()

        assert _lost_fg() == QColor(theme.delta_colour(0.35)).name().upper()
        std = _lost_fg()

        theme.set_palette(theme.PALETTE_COLORBLIND)
        panel.refresh()   # what CentralView.refresh_palette calls for this panel
        assert _lost_fg() == QColor(theme.delta_colour(0.35)).name().upper()
        assert _lost_fg() != std  # the coaching front-door recoloured
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_opportunities_panel_rerenders_on_palette_flip OK")


# ============================================ B. unified "estimated" labelling + the ESTIMATED chip
def test_estimated_short_label_is_one_canonical_form():
    """The inline "estimated" marker is spelled ONE way everywhere: theme.ESTIMATED_MARK == "(est)",
    and estimated_label appends exactly that. The brake-point coaching hint (was a stray "(EST)") and
    the grip column both read it, so the app no longer spells estimated four ways."""
    from types import SimpleNamespace

    from studio import theme as th
    from studio.coaching_panel import _brake_point_hint
    assert th.ESTIMATED_MARK == "(est)"
    assert th.ESTIMATED_SUFFIX == " (est)"
    assert th.estimated_label("Grip") == "Grip (est)"
    # The brake-point hint uses the canonical mark (no more "(EST)").
    bp = SimpleNamespace(cid=3, metres_later=6.4)
    hint = _brake_point_hint(bp)
    assert hint == "Brake ~6 m later into C3 (est)", hint
    assert "(EST)" not in hint and "(est.)" not in hint
    print("test_estimated_short_label_is_one_canonical_form OK")


def test_estimated_quality_badge_is_a_real_chip():
    """The central-view ESTIMATED quality badge renders as a real CHIP (padding + rounded + tinted),
    not plain text.

    It used to carry a one-off #QualityBadge objectName and a rule that existed only for it. The
    app has ONE chip vocabulary now — a [role="Chip"] pill worn by this badge, by the status bar's
    reference chip and by the charts toolbar's "vs ideal" — so the assertion moved onto that role
    and onto the amber `tone` this badge takes, and it is made against the LIVE widget rather than
    against a name only this test knew about."""
    from studio.widgets import chip
    qss = theme._build_qss()
    assert 'QLabel[role="Chip"]' in qss, "no QSS chip rule"
    block = qss.split('QLabel[role="Chip"], QPushButton[role="Chip"]', 1)[1].split("}", 1)[0]
    assert "border-radius" in block and "padding" in block, block
    warn = qss.split('QLabel[role="Chip"][tone="warn"]', 1)[1].split("}", 1)[0]
    assert theme.C.accent_tint in warn or theme.C.accent in warn, warn
    # the live badge really wears them (built by the same factory central_view uses)
    badge = chip("ESTIMATED", tone="warn")
    assert badge.property("role") == "Chip" and badge.property("tone") == "warn"
    print("test_estimated_quality_badge_is_a_real_chip OK")


# ===================================================================== B. PB moment
def _index(*entries):
    return {"version": 1, "entries": list(entries)}


def _entry(track, best, date="2026-01-01", fp="GX0001"):
    return {"fingerprint": fp, "stem": "GX010001", "track": track, "date": date,
            "lap_count": 3, "best": best, "theoretical": None, "paths": []}


def test_pb_moment_beats_prior_best_on_verified_timing():
    """A freshly-analysed session that BEATS the track's prior PB fires a "beat" moment carrying the
    improvement (prior − best). Gated on verified timing via pb_moment_for."""
    idx = _index(_entry("MK", 70.0))
    m = library.pb_moment_for(True, idx, "MK", 68.5)
    assert m is not None and m["kind"] == "beat"
    assert m["track"] == "MK" and m["best"] == 68.5 and m["prior"] == 70.0
    assert abs(m["improvement"] - 1.5) < 1e-9
    print("test_pb_moment_beats_prior_best_on_verified_timing OK")


def test_pb_moment_does_not_fire_on_provisional_timing():
    """PROVISIONAL / unverified timing NEVER celebrates — a PB against an arbitrary start line is
    meaningless. Same beating session as above, but verified=False → None."""
    idx = _index(_entry("MK", 70.0))
    assert library.pb_moment_for(False, idx, "MK", 68.5) is None
    print("test_pb_moment_does_not_fire_on_provisional_timing OK")


def test_pb_moment_does_not_fire_on_degraded_timing():
    """DATA-QUALITY degraded timing NEVER celebrates — a recording can be Verified (trusted start
    line) yet still ESTIMATED (media-clock fallback / low GPS), and the app won't celebrate a PB
    whose absolute time it itself calls estimated. Same beating session that fires on verified +
    high-quality (degraded default False), but degraded=True → None even with verified=True."""
    idx = _index(_entry("MK", 70.0))
    assert library.pb_moment_for(True, idx, "MK", 68.5) is not None       # verified + high quality
    assert library.pb_moment_for(True, idx, "MK", 68.5, degraded=True) is None  # verified but degraded
    print("test_pb_moment_does_not_fire_on_degraded_timing OK")


def test_pb_moment_first_session_is_not_a_beat():
    """The first-ever session on a track has no prior PB to beat → a gentler "first" moment (not a
    celebration of beating anything)."""
    m = library.pb_moment_for(True, _index(), "MK", 68.5)
    assert m is not None and m["kind"] == "first" and m["best"] == 68.5
    print("test_pb_moment_first_session_is_not_a_beat OK")


def test_pb_moment_tie_slower_and_no_track_do_not_fire():
    """A tie, a slower lap, an absent track, or an invalid best all report None (no false
    celebration). A re-open of the same recording ties its own prior best → None."""
    idx = _index(_entry("MK", 70.0))
    assert library.pb_moment_for(True, idx, "MK", 70.0) is None      # tie
    assert library.pb_moment_for(True, idx, "MK", 71.0) is None      # slower
    assert library.pb_moment_for(True, idx, None, 60.0) is None      # no track
    assert library.pb_moment_for(True, idx, "MK", None) is None      # no best
    # prior_best reads the min across the track's entries (multiple sessions).
    idx2 = _index(_entry("MK", 70.0, fp="A"), _entry("MK", 69.0, fp="B"), _entry("X", 50.0, fp="C"))
    assert library.prior_best(idx2, "MK") == 69.0
    assert library.pb_moment_for(True, idx2, "MK", 69.5) is None     # beats 70 but not 69
    assert library.pb_moment_for(True, idx2, "MK", 68.5) is not None  # beats the real PB
    print("test_pb_moment_tie_slower_and_no_track_do_not_fire OK")


def test_pb_moment_text_wording():
    """The celebration copy: a "beat" names the track + the gap to the old PB; a "first" is gentler.
    Times format through the injected fmt_time (kept out of the pacer-free library module)."""
    beat = library.pb_moment_for(True, _index(_entry("Daytona MK", 70.0)), "Daytona MK", 68.42)
    title, body = library.pb_moment_text(beat, fmt_time)
    assert "personal best" in title.lower()
    assert "Daytona MK" in body and fmt_time(68.42) in body
    assert "faster than your previous best" in body and fmt_time(70.0) in body
    first_title, first_body = library.pb_moment_text(
        library.pb_moment_for(True, _index(), "MK", 60.0), fmt_time)
    assert "first" in first_title.lower() and "MK" in first_body
    print("test_pb_moment_text_wording OK")


def test_pb_toast_shows_wording_and_link_routes_to_progression():
    """The PBToast surfaces the celebration wording and its "See your progress →" link routes to
    the injected progression callback (the app passes _open_library — the PB-progression chart),
    then dismisses. This is the retention hook made discoverable."""
    from studio.overlays import PBToast
    routed = []
    toast = PBToast("New personal best! 🏁", "MK — 1:08.42, 0.31 s faster.",
                     on_progress=lambda: routed.append(True))
    assert "personal best" in toast.title_label.text().lower()
    assert "faster" in toast.body_label.text()
    assert "progress" in toast.link_btn.text().lower()
    toast.link_btn.click()
    assert routed == [True], "the link must route to the PB-progression surface"
    print("test_pb_toast_shows_wording_and_link_routes_to_progression OK")


if __name__ == "__main__":
    test_delta_arrow_and_run_carry_direction_without_colour()
    test_palette_selector_is_single_source_and_swaps_semantic_hues()
    test_colorblind_palette_pref_roundtrip()
    test_lap_table_best_cells_carry_non_colour_star_marks()
    test_stats_sector_best_column_is_tinted_whole_and_says_so_on_hover()
    test_stats_worst_loss_cells_carry_a_mark_and_the_score_that_chose_them()
    test_lap_table_best_star_survives_a_sort()
    test_lap_table_shows_a_banded_out_lap_in_the_excluded_strip()
    test_lap_table_excluded_strip_hidden_on_a_clean_recording()
    test_lap_table_excluded_strip_menu_hide_is_orthogonal_to_collapse()
    test_lap_table_best_colours_follow_the_palette_selector()
    test_brake_throttle_band_colour_follows_the_palette()
    test_stats_spark_pb_colour_follows_the_palette()
    test_export_delta_colour_follows_the_palette()
    test_overlay_config_carries_the_palette()
    test_opportunities_panel_rerenders_on_palette_flip()
    test_estimated_short_label_is_one_canonical_form()
    test_estimated_quality_badge_is_a_real_chip()
    test_pb_moment_beats_prior_best_on_verified_timing()
    test_pb_moment_does_not_fire_on_provisional_timing()
    test_pb_moment_does_not_fire_on_degraded_timing()
    test_pb_moment_first_session_is_not_a_beat()
    test_pb_moment_tie_slower_and_no_track_do_not_fire()
    test_pb_moment_text_wording()
    test_pb_toast_shows_wording_and_link_routes_to_progression()
    print("\nAll accessible-cues + PB-moment tests passed.")
