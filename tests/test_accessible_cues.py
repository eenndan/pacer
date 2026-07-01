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
from studio.lap_table import BEST_LAP_MARK, BEST_SECTOR_MARK, LapTable  # noqa: E402


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
    ★, so "this is the best" reads WITHOUT the green/purple hue. Non-best rows carry no ★."""
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
    """The _PBToast surfaces the celebration wording and its "See your progress →" link routes to
    the injected progression callback (the app passes _open_library — the PB-progression chart),
    then dismisses. This is the retention hook made discoverable."""
    from studio.app import _PBToast
    routed = []
    toast = _PBToast("New personal best! 🏁", "MK — 1:08.42, 0.31 s faster.",
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
    test_lap_table_best_star_survives_a_sort()
    test_lap_table_best_colours_follow_the_palette_selector()
    test_pb_moment_beats_prior_best_on_verified_timing()
    test_pb_moment_does_not_fire_on_provisional_timing()
    test_pb_moment_first_session_is_not_a_beat()
    test_pb_moment_tie_slower_and_no_track_do_not_fire()
    test_pb_moment_text_wording()
    test_pb_toast_shows_wording_and_link_routes_to_progression()
    print("\nAll accessible-cues + PB-moment tests passed.")
