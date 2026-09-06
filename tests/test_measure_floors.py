"""A MEASURE BOUNDS CONTENT. It may not become the minimum of the thing that carries it.

The design wave gave the app's prose surfaces one measure (`theme.EMPTY_MEASURE_PX`, PR #195) and
gave the Stats laps tile the height its own ink needs (PR #194). Both were right, and both were
spent in the same wrong place: on a dimension the widget SHARES with its neighbours. Its own
regression sweep then measured the cost.

  * `EmptyState.resizeEvent` pins the measure with `setFixedWidth`, which sets a minimum as well
    as a maximum. A pane cannot shrink below its child's minimum, so the state's measure became a
    ONE-WAY RATCHET: on a zero-lap recording, opening the window at 1440 and dragging it back to
    1280 left the lap pane 488 px wide where a window OPENED at 1280 gives 457, and the window's
    own resize floor went 1018 -> 1049 px and never came back. One recording, THREE minimums
    (929 / 1018 / 1049) depending on which tab was showing and what the window had been. The
    negative control that named the cause: the Stats tab, the one zero-lap surface #195 kept off
    the object, ratcheted 0 px.
  * `Tile._claim_ink_height` measured the value's ink PER STRING. A grid row is as tall as its
    tallest member, so on a recording whose laps tile prints `25 · 24 ⊘` that one tile asked for
    37 px, its two row-mates for 35, and the ⊘ tile's caption sat 2 px below both of theirs.
  * the Stats zero-lap note was moved OFF `EmptyState` and onto `#ProvisionalBanner` — correctly,
    it is a banner over a page that keeps rendering — but `#ProvisionalBanner` is an 11 px
    semibold amber CALL-TO-ACTION LINE, and it was handed all 308 characters: the app's longest
    paragraph in its shortest type, at a measure nothing capped, while the identical sentences on
    the four sibling panels in the same frame set 13 px inside 440 px.

So this file asserts the three things that make a measure a measure:

  1. The `EmptyState` measure holds at EVERY width, swept at 1 px in BOTH directions — and the
     state's minimum is the same number before and after, so a pane can always be given back.
  2. Every `Tile` claims one row height whatever it prints.
  3. The Stats zero-lap block states its prose in the app's prose step at the app's prose measure,
     and the two halves still add up to the shared copy.

Run: QT_QPA_PLATFORM=offscreen python tests/test_measure_floors.py
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # BEFORE the first widget: a size is a function of its font

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget  # noqa: E402

from studio import theme  # noqa: E402
from studio.widgets import EmptyState, Tile  # noqa: E402

_ALIVE: list = []

# The real zero-lap copy's shape: a sentence of a title over a three-sentence body. Long enough
# that the wrap heuristic and the cap genuinely disagree, which is the condition the measure
# exists for.
_TITLE = "No complete laps in this recording."
_BODY = ("The GPS may not have locked, or the recording is too short to cross the start/finish "
         "line. If this is the right track, drag the start/finish line on the map to set where a "
         "lap begins. If it is the wrong recording, open another with ⌘O.")


def _keep(w):
    _ALIVE.append(w)
    return w


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _host(widget, size):
    """`widget` alone in a REAL top-level window — the shape a panel gives a state.

    A top-level widget's layout writes `totalMinimumSize()` onto the window as a hard
    `setMinimumSize`, so "can this window be made narrow again" is answered by resizing it and
    reading back what it became, not by asking a size hint."""
    win = QWidget()
    win.setObjectName("centralwidget")
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)
    win.resize(*size)
    win.show()
    _settle(8)
    return _keep(win)


def _measure_for(state) -> int:
    """The width both slots must be at this state's current width — the rule, not the outcome."""
    return min(max(0, state.width() - 2 * theme.SPACE_XL), theme.EMPTY_MEASURE_PX)


# =========================================================================== 1. the ratchet
def test_the_empty_state_measure_never_becomes_the_panes_minimum():
    """`widgets.EmptyState.minimumSizeHint`, and the defect it was written for.

    Swept at 1 px in both directions rather than sampled: a one-size check is exactly what let the
    ratchet through (#195's own guard asserted the measure at rest and it was true at rest)."""
    fresh = _keep(EmptyState(_TITLE, _BODY))
    fresh_host = _host(fresh, (320, 320))          # this one is NEVER made wide
    fresh_min = fresh.minimumSizeHint().width()

    state = _keep(EmptyState(_TITLE, _BODY))
    host = _host(state, (900, 320))
    wide_min = state.minimumSizeHint().width()

    bad_measure, mins = [], set()
    widths = list(range(900, 319, -1)) + list(range(320, 901))
    for w in widths:
        host.resize(w, 320)
        _settle(2)
        want = _measure_for(state)
        if state.title.width() != want or state.body.width() != want:
            bad_measure.append((w, state.width(), state.title.width(),
                                state.body.width(), want))
        mins.add(state.minimumSizeHint().width())
    assert not bad_measure, (
        "the measure is not `min(pane - 2*SPACE_XL, EMPTY_MEASURE_PX)` at "
        f"{len(bad_measure)} of {len(widths)} widths — first few "
        f"(window, state, title, body, want): {bad_measure[:4]}")
    assert len(mins) == 1, (
        f"EmptyState.minimumSizeHint().width() took {sorted(mins)} over a "
        f"{min(widths)}..{max(widths)} sweep — a minimum that moves with the current width is the "
        "ratchet: it can only ever go up, because the pane cannot shrink to lower it")

    # ...and the window it lives in can be given the pane back after having been wide. Before the
    # fix this window came back 488 px, whatever it was asked for.
    host.resize(320, 320)
    _settle(8)
    assert host.width() == 320, (
        f"a window that had been 900 px could only shrink to {host.width()} px, not 320 — the "
        f"state's minimum is {state.minimumSizeHint().width()} px "
        f"(a never-widened one asks for {fresh_min})")
    assert wide_min == fresh_min, (
        f"a state that has been 900 px wide asks for {wide_min} px as its minimum where a fresh "
        f"one asks {fresh_min} — the same recording would get two window minimums")
    assert fresh_host.width() == 320
    print("test_the_empty_state_measure_never_becomes_the_panes_minimum OK "
          f"({len(widths)} widths 320..900 both ways, minimum {fresh_min} px at every one, "
          f"measure = min(pane - {2 * theme.SPACE_XL}, {theme.EMPTY_MEASURE_PX}))")


def test_the_measure_still_yields_a_pane_narrower_than_the_cap():
    """The other half of the same rule, and the reason the pin exists at all: below the cap the
    state takes the PANE, not a word-wrapped QLabel's roughly-square `sizeHint` — which is what
    the first port of this object did (30 characters per line inside a 440 px allowance)."""
    state = _keep(EmptyState(_TITLE, _BODY))
    host = _host(state, (400, 320))
    want = _measure_for(state)
    assert state.width() == 400 and want == 400 - 2 * theme.SPACE_XL
    assert state.body.width() == want, (
        f"in a {state.width()} px pane the body set {state.body.width()} px, not the pane's "
        f"{want} px — the cap alone does not produce a measure")
    host.resize(900, 320)
    _settle(6)
    assert state.body.width() == theme.EMPTY_MEASURE_PX, (
        f"in a {state.width()} px pane the body set {state.body.width()} px, not the "
        f"{theme.EMPTY_MEASURE_PX} px cap")
    print("test_the_measure_still_yields_a_pane_narrower_than_the_cap OK "
          f"(352 px in a 400 px pane, {theme.EMPTY_MEASURE_PX} px in a 900 px one)")


# =========================================================================== 2. the tile row
def test_every_tile_claims_one_row_height_whatever_it_prints():
    """`widgets.Tile._claim_ink_height`. The ink is claimed on the TYPE STEP (`widgets.VALUE_INK`),
    so a tile that happens to print a ⊘ cannot make itself taller than the tiles beside it.

    The strings are the Stats page's own: a bare count, a lap time, the laps tile's excluded and
    dropout runs, the em-dash "no signal" default, a unit suffix and the ideal-lap mark."""
    values = ["25", "1:08.771", "25 · 24 ⊘", "25 · 3 ⚠", "—", "1.28 g", "★"]
    tiles = [_keep(Tile("caption")) for _ in values]
    for tile, value in zip(tiles, values, strict=True):
        tile.set(value)
    _settle(2)
    hints = {t.sizeHint().height() for t in tiles}
    assert len(hints) == 1, (
        "Tile.sizeHint().height() is string-dependent — "
        + ", ".join(f"{v!r}={t.sizeHint().height()}" for t, v in zip(tiles, values, strict=True))
        + ". A grid row is as tall as its tallest member, so this drops the shorter tiles' "
          "captions below their row-mates'.")

    # ...and in a real row it shows: every caption on one baseline.
    row = _keep(QWidget())
    lay = QHBoxLayout(row)
    for tile in tiles:
        lay.addWidget(tile)
    _host(row, (720, 120))
    tops = {t.caption.y() for t in tiles}
    assert len(tops) == 1, (
        "captions in one tile row sit at different heights: "
        + ", ".join(f"{v!r}=y{t.caption.y()}" for t, v in zip(tiles, values, strict=True)))
    print("test_every_tile_claims_one_row_height_whatever_it_prints OK "
          f"({len(values)} value strings, sizeHint {hints.pop()} px, caption y {tops.pop()})")


def test_the_tile_ink_sample_covers_every_mark_a_tile_prints():
    """The sample is the CONTRACT — claiming the height on the type step is only correct if the
    step's sample is at least as tall as anything a tile can be given. Measured against the marks
    the Stats page actually prints beside a count."""
    from PySide6.QtGui import QFontMetrics

    from studio import widgets
    fm = QFontMetrics(theme.mono_font(theme.EMPHASIS, theme.W_SEMIBOLD))
    claimed = max(fm.height(), fm.tightBoundingRect(widgets.VALUE_INK).height())
    short = []
    for mark in ("⊘", "⚠", "★", "1:08.771", "25 · 24 ⊘ · 3 ⚠", "—", "1.28 g", "98 %"):
        need = fm.tightBoundingRect(mark).height()
        if need > claimed:
            short.append(f"{mark!r} needs {need} px, the sample claims {claimed}")
    assert not short, "widgets.VALUE_INK does not cover:\n  " + "\n  ".join(short)
    print(f"test_the_tile_ink_sample_covers_every_mark_a_tile_prints OK "
          f"(claims {claimed} px, line box {fm.height()} px)")


# =========================================================================== 3. the Stats banner
def _zero_lap_stats_view():
    from test_stats import _fake_view_session

    from studio.stats_panel import StatsView

    sess = _fake_view_session(laps=False, has_g=False)
    sess.timing_verified = False
    view = _keep(StatsView(sess))
    _host(view, (515, 700))        # the lap panel's own width at a 1440 px window
    return view


def test_the_zero_lap_prose_is_prose_and_the_banner_is_one_line():
    """`stats_panel.StatsView.__init__` / `_show_no_laps_prose`.

    The banner keeps the one line the role is built for; the paragraph takes the app's prose step
    and the app's prose measure — the same `role="EmptyBody"` rule and the same
    `theme.EMPTY_MEASURE_PX` its four siblings in the same frame wear. No new token."""
    view = _zero_lap_stats_view()
    note, prose = view.no_laps_note, view.no_laps_prose
    assert note.isVisible() and prose.isVisible()
    assert prose.font().pixelSize() == theme.BODY, (
        f"the zero-lap prose is {prose.font().pixelSize()} px, not BODY ({theme.BODY}) — "
        f"role={prose.property('role')!r}")
    assert prose.maximumWidth() == theme.EMPTY_MEASURE_PX
    assert prose.width() == min(theme.EMPTY_MEASURE_PX, prose.parentWidget().width()), (
        f"the prose set {prose.width()} px in a {prose.parentWidget().width()} px page — the cap "
        "is not a measure unless the layout hands it the room below the cap")
    # The banner is ONE LINE at the width the page gives it — that is what the role is for.
    line = note.fontMetrics().height()
    assert note.height() <= line + 2 * theme.SPACE_XS + 2, (
        f"the #ProvisionalBanner strip is {note.height()} px — more than one {line} px line plus "
        f"its own {theme.SPACE_XS} px padding. A banner role is not a home for a paragraph.")
    print("test_the_zero_lap_prose_is_prose_and_the_banner_is_one_line OK "
          f"(banner {note.height()} px / {note.font().pixelSize()} px type, "
          f"prose {prose.width()} px / {prose.font().pixelSize()} px)")


def test_the_zero_lap_prose_sets_the_same_measure_as_its_siblings():
    """Check 2 of tests/test_state_surfaces.py, applied to the one zero-lap surface that is NOT an
    EmptyState — measured in characters, in the live face, against the same 45-75 band."""
    view = _zero_lap_stats_view()
    label = QLabel("x")
    label.setFont(theme.ui_font(theme.BODY))
    avg = label.fontMetrics().horizontalAdvance("abcdefghijklmnopqrstuvwxyz") / 26.0
    chars = view.no_laps_prose.width() / avg
    assert 45 <= chars <= 75, (
        f"the Stats zero-lap prose sets {chars:.0f} characters per line "
        f"({view.no_laps_prose.width()} px at {view.no_laps_prose.font().pixelSize()} px) — "
        f"outside the 45-75 readable band the ported states are held to")
    print("test_the_zero_lap_prose_sets_the_same_measure_as_its_siblings OK "
          f"({chars:.0f} characters at BODY, cap {theme.EMPTY_MEASURE_PX} px)")


def test_the_split_zero_lap_copy_still_adds_up_to_the_shared_string():
    """Splitting a state's copy across two labels is how a surface loses half a sentence, so the
    two halves are asserted to BE `NO_LAPS_TEXT` — the string tests/test_state_surfaces.py holds
    every zero-lap surface to."""
    from studio import stats_panel
    joined = f"{stats_panel.NO_LAPS_BANNER} {stats_panel.NO_LAPS_PROSE}"
    assert joined == stats_panel.NO_LAPS_TEXT, (
        f"the two halves render {joined!r}, not NO_LAPS_TEXT")
    view = _zero_lap_stats_view()
    live = f"{view.no_laps_note.text()} {view.no_laps_prose.text()}"
    assert live == stats_panel.NO_LAPS_TEXT, f"the live labels render {live!r}"
    print("test_the_split_zero_lap_copy_still_adds_up_to_the_shared_string OK "
          f"({len(stats_panel.NO_LAPS_TEXT)} characters, "
          f"{len(stats_panel.NO_LAPS_BANNER)} in the banner)")


def test_the_zero_lap_prose_takes_its_own_air_with_it():
    """The indent is a LAYOUT margin (a QSS rule rewrites a QLabel's contents margins from its own
    box — see widgets.EmptyState), and a layout's margins outlive its only child being hidden. So
    the margins go with the slot, or a session WITH laps pays for a block it does not show."""
    view = _zero_lap_stats_view()
    m = view._no_laps_prose_row.contentsMargins()
    assert (m.left(), m.top()) == (theme.SPACE_XXS + theme.SPACE_M, theme.SPACE_XS)
    view._show_no_laps_prose(False)
    _settle(2)
    m = view._no_laps_prose_row.contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0), (
        f"the hidden prose left {m.top() + m.bottom()} px of air behind it")
    print("test_the_zero_lap_prose_takes_its_own_air_with_it OK "
          f"(indent {theme.SPACE_XXS + theme.SPACE_M} px shown, 0 hidden)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} MEASURE-FLOOR TESTS PASSED", flush=True)
