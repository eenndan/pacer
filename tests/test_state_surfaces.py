"""The COMPOSITION guard — the sibling of tests/test_design_system.py (dimensions),
tests/test_contrast.py (colour) and tests/test_glyph_vocabulary.py (marks). This one is about the
fourth vocabulary: what a surface that has NOTHING TO SHOW is made of.

WHY THIS FILE EXISTS. Sixteen states that answer "why is there nothing here?" were driven in the
real app and measured from the window composite (QA D2). They shared exactly ONE property — the
13 px body size. Everything else was decided per site:

  * an ICON at 1 of 16 sites, a TITLE slot at 1;
  * ALIGNMENT centred at 15 and LEFT at one — and the outlier wore the SAME `role="EmptyState"` as
    five of the centred ones;
  * MEASURE from 19 to 138 characters at that one type size, a 7x spread, because nothing anywhere
    set a maximum width: each state's measure was really the width of whatever pane it landed in;
  * SURFACE card at 7 sites and canvas at 8 — so the same rectangle changed colour when you
    switched tab;
  * and on a zero-lap recording the lap panel said "Open another recording with ⌘O" while the
    charts panel and the map, 523 px to its right IN THE SAME FRAME, said "drag the start/finish
    line" — two mutually exclusive instructions for one fact.

Spacing was NOT the finding: every internal gap was already on the scale. So this file does not
check spacing. It checks the things that had never been decided once.

THE CONTRACT, and every clause is asserted below:

  1. ONE OBJECT. Every empty state in `studio/` is a `widgets.EmptyState`, and the ledger of sites
     is enumerated BY OWNING FUNCTION, so adopting the object at a new site is a decision somebody
     writes down rather than a diff nobody reads. The retired `role="EmptyState"` — one role that
     produced three presentations — may not come back.
  2. ONE MEASURE. Every title and body is capped at `theme.EMPTY_MEASURE_PX`, and that cap is
     checked against the readable band (45-75 characters) in the LIVE face, not against a comment.
  3. ONE TITLE SIZE and ONE BODY SIZE, resolved through the real stylesheet.
  4. THE DECLARED GAPS, and each carried by the slot that can vanish.
  5. THE SURFACE RULE, read from the WINDOW COMPOSITE. A child `grab()` reads the rule's colour out
     of the palette and reports success against a widget that composited nothing — which is how a
     QWidget subclass silently not painting its QSS background cost three widgets in PR #185.
  6. NO SURFACE STATES ONE NEXT ACTION WITHOUT ITS SIBLING.

Plus the two residuals this lane closed, which are size-dependent and therefore swept rather than
sampled: the Stats tile's ⊘ (its ink against its box) and the lap grid's trailing spacer (its width
against the pointer floor, at every panel width from 360 to 1440 px).

Run: QT_QPA_PLATFORM=offscreen python tests/test_state_surfaces.py
"""
import ast
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # BEFORE the first widget: a size is a function of its font

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget  # noqa: E402

from studio import data_quality, theme  # noqa: E402
from studio import lap_table as LT  # noqa: E402
from studio.theme import C  # noqa: E402
from studio.widgets import EmptyState, Tile  # noqa: E402

_STUDIO = os.path.join(_REPO, "studio")

# Every widget these tests build stays referenced for the run — the same reason
# tests/test_lap_table_empty_states.py keeps its own list: a collected panel is torn down in Python
# while Qt is still delivering events to the viewport it filters.
_ALIVE: list = []


def _keep(w):
    _ALIVE.append(w)
    return w


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _host(widget, size=(640, 420)):
    """`widget` in a REAL top-level window, shown and settled. Returns the window.

    Every paint claim in this file is read from this window's rendered image at
    `child.mapTo(window, QPoint(0, 0))` — never from `child.grab()`."""
    win = QWidget()
    win.setObjectName("centralwidget")     # the canvas rule, as in the shipping window
    lay = QVBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)
    win.resize(*size)
    win.show()
    _settle(8)
    return _keep(win)


def _px_in(win, child, dx=2 * theme.RADIUS_M, dy=2 * theme.RADIUS_M):
    """The composited colour `dx, dy` px inside `child`, read from the WINDOW's image.

    The offset clears the card's own RADIUS_M corner: two pixels in, an antialiased rounded corner
    reads #1E222A — a blend of the card and the canvas behind it — which is a true measurement of
    the wrong pixel."""
    img = win.grab().toImage()
    tl = child.mapTo(win, QPoint(0, 0))
    return f"#{img.pixel(tl.x() + dx, tl.y() + dy) & 0xFFFFFF:06X}"


# --------------------------------------------------------------------------- the live sites
def _lap_table():
    from test_lap_table_empty_states import _FakeLapSession
    return _keep(LT.LapTable(_FakeLapSession(valid=0)))


def _corner_table(valid=()):
    from test_lap_table_empty_states import _FakeCornerSession
    ct = _keep(LT.CornerTable(_FakeCornerSession(valid=valid)))
    ct.refresh()
    return ct


def _plots_view():
    from studio.plots_view import PlotsView

    class _NoLaps:
        def has_reference(self):
            return False

        def best_lap_id(self):
            return None

        def delta(self, ids, x_mode):
            return None            # falsy == nothing to plot, as Session.delta does

        def delta_to_ideal(self, ids, x_mode="distance"):
            return None

        def lap_window(self, lid):
            return None

    pv = _keep(PlotsView(_NoLaps()))
    pv.refresh()
    return pv


def _library(entries):
    from studio.library_dialog import LibraryDialog
    dlg = _keep(LibraryDialog({"entries": entries}, open_recording=lambda _p: None))
    dlg.resize(900, 520)
    dlg.show()
    _settle(8)
    return dlg


def _coaching_panel():
    from test_coaching import _gate_session

    from studio.coaching_panel import OpportunitiesPanel
    return _keep(OpportunitiesPanel(_gate_session()))


def _coaching_modal():
    from test_coaching import _gate_session

    from studio.coaching_panel import OpportunitiesDialog
    session = _gate_session()
    dlg = _keep(OpportunitiesDialog(session.coaching_opportunities(), jump_to=None,
                                    brake_points=session.coaching_brake_points()))
    dlg.resize(920, 380)
    # SHOWN, not merely built: a stylesheet reaches a widget on polish, and an unshown dialog's
    # labels still carry the app-wide default font — which is a test measuring a surface the user
    # never sees (the trap _qtapp.themed_app's docstring exists for, from the other end).
    dlg.show()
    _settle(8)
    return dlg


def _states():
    """(label, EmptyState, owns_pane_expected) for every site this file can build for real.

    The label is the OWNING SURFACE, so a failure names a decision rather than a widget id. MapView
    is the one adopting site not built here — it needs a segmented pacer session — and it is
    covered live by tests/test_rainbow_map.py (objectName + copy) and by the AST inventory below."""
    lt = _lap_table()
    corners_none = _corner_table(valid=())
    corners_unsel = _corner_table(valid=(0, 1))
    plots = _plots_view()
    panel = _coaching_panel()
    # HOSTED, every one of them: a widget that has never been laid out has never run its own
    # resizeEvent, so its labels still carry the size hint they were born with — that is a test
    # measuring a composition the user never sees.
    for w in (lt, corners_none, corners_unsel, plots, panel):
        _host(w, (640, 420))
    empty_lib = _library([])
    filtered = _library([{"track": "Daytona MK", "date": "2026-08-12", "best": 68.42,
                          "theoretical": 67.9, "paths": ["/tmp/a.MP4"], "verified": True}])
    filtered.search.setText("zzzzz")
    _settle(6)
    return [
        ("LapTable.__init__", lt._empty, True),
        ("CornerTable.refresh (no selectable lap)", corners_none.empty, True),
        ("CornerTable.refresh (nothing selected)", corners_unsel.empty, True),
        ("PlotsView.__init__", plots._empty, True),
        ("LibraryDialog._show_empty_note (empty index)", empty_lib._empty_note, True),
        ("LibraryDialog._show_empty_note (filter)", filtered._empty_note, True),
        ("OpportunitiesPanel.__init__", panel.empty_state, True),
        ("OpportunitiesDialog._empty_state", _coaching_modal().findChild(EmptyState), False),
    ]


# =========================================================================== 1. the one object
def _empty_state_sites():
    """(module, owning scope) for every `EmptyState(...)` construction in studio/, by AST."""
    out = []
    for fn in sorted(os.listdir(_STUDIO)):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(_STUDIO, fn), encoding="utf-8").read(), fn)

        def walk(node, scope, fn=fn):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    walk(child, f"{scope}.{child.name}" if scope else child.name)
                    continue
                if isinstance(child, ast.Call) and getattr(child.func, "id", "") == "EmptyState":
                    out.append((fn, scope or "<module>"))
                walk(child, scope)

        walk(tree, "")
    return sorted(set(out))


def test_every_empty_state_in_the_app_is_the_one_object():
    """Check 1. The ledger of adopting sites, by owning function, and the retired role stays dead.

    The list is written out rather than counted so that adopting the object somewhere new is a
    line somebody adds here — the same reason test_design_system.py's EXEMPT entries name a
    constructor rather than a line number."""
    LEDGER = {
        ("coaching_panel.py", "OpportunitiesDialog._empty_state"),
        ("coaching_panel.py", "OpportunitiesPanel.__init__"),
        ("lap_table.py", "CornerTable.__init__"),
        ("lap_table.py", "LapTable.__init__"),
        ("library_dialog.py", "LibraryDialog.__init__"),
        ("map_view.py", "MapView.__init__"),
        ("plots_view.py", "PlotsView.__init__"),
    }
    got = set(_empty_state_sites())
    assert got == LEDGER, (
        "the empty-state site list moved — add the new site here (naming the function that owns "
        f"the decision) or explain the one that went:\n  new: {sorted(got - LEDGER)}\n  "
        f"gone: {sorted(LEDGER - got)}")
    # The role that produced three presentations is retired: the object carries the surface now.
    stale = []
    for fn in sorted(os.listdir(_STUDIO)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(_STUDIO, fn), encoding="utf-8").read()
        if 'setProperty("role", "EmptyState")' in src:
            stale.append(fn)
    assert not stale, (
        f'setProperty("role", "EmptyState") is back in {stale} — one role for a card, a canvas and '
        f"a left-aligned banner is what QA D2-06 filed. Use widgets.EmptyState.")
    print(f"test_every_empty_state_in_the_app_is_the_one_object OK ({len(got)} sites)")


# =========================================================================== 2/3. measure + type
def test_one_measure_one_title_size_one_body_size():
    """Checks 2 and 3, live, on every site — the 7x measure spread and the per-site type."""
    offenders = []
    for label, state, _card in _states():
        title, body = state.title, state.body
        # THE MEASURE IS A LIVE RULE, not a ceiling: everything the pane can give, up to the cap.
        # Asserting only `maximumWidth == cap` would pass against the state that shipped from the
        # first port, where a word-wrapped QLabel's own size hint set 30 characters per line inside
        # a 440 px allowance (see EmptyState.resizeEvent).
        want = min(max(0, state.width() - 2 * theme.SPACE_XL), theme.EMPTY_MEASURE_PX)
        if title.width() != want:
            offenders.append(f"{label}: title is {title.width()} px in a {state.width()} px pane, "
                             f"not the measure {want}")
        if body.width() != want:
            offenders.append(f"{label}: body is {body.width()} px in a {state.width()} px pane, "
                             f"not the measure {want}")
        if title.font().pixelSize() != theme.EMPHASIS:
            offenders.append(f"{label}: title is {title.font().pixelSize()} px, not EMPHASIS")
        if body.font().pixelSize() != theme.BODY:
            offenders.append(f"{label}: body is {body.font().pixelSize()} px, not BODY")
        for slot, w in (("title", title), ("body", body)):
            if not (w.alignment() & Qt.AlignHCenter):
                offenders.append(f"{label}: {slot} is not centred ({w.alignment()})")
    assert not offenders, (
        "empty states that do not share one measure and one pair of type sizes:\n  "
        + "\n  ".join(offenders))
    print("test_one_measure_one_title_size_one_body_size OK "
          f"({len(_ALIVE)} widgets, cap {theme.EMPTY_MEASURE_PX} px)")


def test_the_measure_cap_is_inside_the_readable_band():
    """Check 2's other half: the cap is a TYPOGRAPHIC decision, so it is measured against the face
    the app really resolves rather than against the comment that chose it. 45-75 characters is the
    readable band; the shipped states ran 19 to 138."""
    label = QLabel("x")
    label.setFont(theme.ui_font(theme.BODY))
    fm = label.fontMetrics()
    # A lower-case alphabet is the standard proxy for average character width.
    avg = fm.horizontalAdvance("abcdefghijklmnopqrstuvwxyz") / 26.0
    chars = theme.EMPTY_MEASURE_PX / avg
    assert 45 <= chars <= 75, (
        f"theme.EMPTY_MEASURE_PX = {theme.EMPTY_MEASURE_PX} px sets {chars:.0f} characters at "
        f"BODY in {label.font().family()!r} (avg advance {avg:.2f} px) — outside the 45-75 band")
    print(f"test_the_measure_cap_is_inside_the_readable_band OK "
          f"({theme.EMPTY_MEASURE_PX} px = {chars:.0f} characters at BODY)")


# =========================================================================== 4. the gaps
def test_the_declared_gaps_are_carried_by_the_slot_that_can_vanish():
    """Check 4. Every gap belongs to the slot that can disappear — the icon's below it, the body's
    above it — so a hidden slot takes its own air with it. A layout `spacing` item does not: it is
    what leaves a hole behind a widget that went away, which is what Coaching's stray 11 px gap was
    (an empty 7x14 header label nobody could see)."""
    with_icon = _keep(EmptyState("Title", "Body", icon="ph.folder-open"))
    plain = _keep(EmptyState("Title", "Body"))
    no_body = _keep(EmptyState("Title"))
    for state in (with_icon, plain, no_body):
        assert state.layout().spacing() == 0, "the layout must state no gap of its own"
        assert state.layout().contentsMargins().left() == theme.SPACE_XL
        assert state.layout().contentsMargins().top() == theme.SPACE_XL
    assert with_icon.icon_label is not None
    assert plain.icon_label is None, "an absent slot must not be CONSTRUCTED"
    assert not no_body.body.isVisible() or not no_body.body.text()
    # THE GAPS ARE SPACER ITEMS, not contents margins, and this is the assertion that says why: a
    # QSS rule that reaches a QLabel rewrites its contents margins from the rule's own box, and it
    # did so unevenly — four panels kept a body margin of 8 while the map's floating card, the same
    # object under the same rule, came back 0 and stood its title on its body.
    for state in (with_icon, plain, no_body):
        assert state.title.contentsMargins().bottom() == 0
        assert state.body.contentsMargins().top() == 0
    assert with_icon.layout().itemAt(1).sizeHint().height() == theme.SPACE_M
    assert plain.layout().itemAt(1).sizeHint().height() == theme.SPACE_S
    assert no_body.layout().itemAt(1).sizeHint().height() == 0, (
        "a state with no body must not stand its title over 8 px of nothing")
    # ...and the icon really goes away, gap included: hidden, it occupies no height at all.
    _host(with_icon, (520, 300))
    # sizeHint, not height(): the state is stretched by its host, so the WIDGET does not shrink —
    # what has to shrink is the room the composition asks for.
    tall = with_icon.sizeHint().height()
    with_icon.set_icon_visible(False)
    with_icon.layout().activate()
    _settle(8)
    shrunk = with_icon.sizeHint().height()
    assert tall - shrunk == theme.ICON_PX + theme.SPACE_M, (
        f"hiding the icon freed {tall - shrunk} px, not the mark ({theme.ICON_PX}) plus its own "
        f"gap ({theme.SPACE_M}) — the gap is a layout item again")
    print(f"test_the_declared_gaps_are_carried_by_the_slot_that_can_vanish OK "
          f"(icon+gap = {tall - shrunk} px)")


# =========================================================================== 5. the surface
def test_the_card_is_painted_and_read_from_the_window_composite():
    """Check 5. `owns_pane=True` composites C.surface; False leaves the canvas showing. Read from
    the WINDOW's image, because a child grab reads the rule's colour out of the palette and would
    pass against a widget that painted nothing — the exact failure mode a QWidget subclass has when
    WA_StyledBackground is not set (PR #185, three widgets)."""
    card = EmptyState("On a card", "body", owns_pane=True)
    win = _host(card, (520, 300))
    assert card.testAttribute(Qt.WA_StyledBackground), "the subclass was never told to paint"
    assert _px_in(win, card) == C.surface.upper(), (
        f"the card composited {_px_in(win, card)}, not C.surface {C.surface}")
    canvas = EmptyState("On the canvas", "body", owns_pane=False)
    win2 = _host(canvas, (520, 300))
    assert _px_in(win2, canvas) == C.canvas.upper(), (
        f"a canvas state composited {_px_in(win2, canvas)}, not C.canvas {C.canvas}")
    # ...and every SITE declares which one it is, so two tabs of one panel cannot disagree by
    # accident (QA D2-07: the same rectangle repainted #21252E -> #15181E on a tab switch).
    for label, state, owns_pane in _states():
        assert (state.property("card") == "true") is owns_pane, (
            f"{label}: card={state.property('card')!r}, expected owns_pane={owns_pane}")
    print("test_the_card_is_painted_and_read_from_the_window_composite OK "
          f"(card {C.surface}, canvas {C.canvas}, from the window composite)")


# =========================================================================== 6. the copy
def test_no_surface_states_one_next_action_without_the_other():
    """Check 6, and the highest-value line in the lane. A zero-lap recording has TWO ways out — the
    line is in the wrong place, or the recording is the wrong recording — and the shipped app named
    one of them per surface, which is how the lap panel and the map came to contradict each other
    523 px apart in one frame (QA D2-01)."""
    from studio import plots_view, stats_panel
    body = data_quality.no_laps_body()
    assert data_quality.NO_LAPS_NEXT_ACTION in body and data_quality.NO_LAPS_ALT_ACTION in body
    assert data_quality.NO_LAPS_REASON in body
    zero_lap = {
        "lap_table.NO_LAPS_TEXT": LT.NO_LAPS_TEXT,
        "plots_view.EMPTY_TEXT": plots_view.EMPTY_TEXT,
        "stats_panel.NO_LAPS_TEXT": stats_panel.NO_LAPS_TEXT,
        "LapTable._empty": _lap_table()._empty.text(),
        "CornerTable (no selectable lap)": _corner_table(valid=()).empty.text(),
        "PlotsView._empty": _plots_view()._empty.text(),
    }
    offenders = [f"{name}: {text!r}" for name, text in zero_lap.items() if body not in text]
    assert not offenders, (
        "zero-lap surfaces not stating the shared body (why, then BOTH ways out):\n  "
        + "\n  ".join(offenders))
    # Every one of them also leads on the SAME headline — four phrasings of one fact was the
    # other half of D2-01.
    for name, text in zero_lap.items():
        assert data_quality.NO_LAPS_HEADLINE in text, f"{name} re-authored the headline: {text!r}"
    # ...and no module may state the drag action on its own again.
    loose = []
    for fn in sorted(os.listdir(_STUDIO)):
        if not fn.endswith(".py") or fn == "data_quality.py":
            continue
        src = open(os.path.join(_STUDIO, fn), encoding="utf-8").read()
        if "drag the start/finish line on the map to set where a lap begins" in src:
            loose.append(fn)
    assert not loose, (
        f"{loose} re-typed NO_LAPS_NEXT_ACTION instead of reading data_quality.no_laps_body() — "
        f"which is exactly how two of the four copies drifted")
    print(f"test_no_surface_states_one_next_action_without_the_other OK "
          f"({len(zero_lap)} surfaces, one body)")


# =========================================================================== the two residuals
def test_the_tile_value_claims_the_height_its_own_ink_needs():
    """The residual PR #189 could not reach. Inter carries ⊘, so it is legitimately TEXT — and it
    is taller than its own line box at every step of the scale (21 px of ink in a 19 px line at
    EMPHASIS). Every other ⊘ in the app sits in a 24 or 28 px table row; the Stats laps tile's box
    IS the line height, so it was the one surface slicing the apex and base arcs off.

    Read from the window composite: the number of INK ROWS must equal the ink the string has, not
    merely 'ink reaches the edge'.

    THE HEIGHT IS CLAIMED ON THE TYPE STEP, NOT ON THE STRING, and the two assertions at the foot
    of this test are where that shows. As shipped, #194 measured `tightBoundingRect(text)` — the
    height of THIS tile's own value — so a tile printing a ⊘ stood 2 px taller than its row-mates
    and dropped its caption 2 px below theirs in a shared grid row (the wave's own regression
    sweep, SW4-02). The clip fix is unchanged and still proved from the pixels below; what moved
    is that the claim is now made over `widgets.VALUE_INK`, every mark a tile can print, so the
    marked and the plain tile are ONE height. The cost is paid by every tile or by none —
    tests/test_measure_floors.py holds the row."""
    marked = Tile("laps")
    marked.set(f"25 · 24 {LT.EXCLUDED_MARK} · 3 {LT.DROPOUT_MARK}")
    plain = Tile("best lap")
    plain.set("1:08.771")
    holder = QWidget()
    box = QVBoxLayout(holder)
    box.setContentsMargins(theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL)
    box.setSpacing(theme.SPACE_XL)
    box.addWidget(marked, 0, Qt.AlignTop)
    box.addWidget(plain, 0, Qt.AlignTop)
    box.addStretch(1)
    win = _host(holder, (520, 260))
    img = win.grab().toImage()
    for tile, name in ((marked, "the laps tile"), (plain, "a lap-time tile")):
        lb = tile.value
        tl = lb.mapTo(win, QPoint(0, 0))
        wanted = lb.fontMetrics().tightBoundingRect(lb.text()).height()
        bg = img.pixel(tl.x() + lb.width() - 2, tl.y() + 1) & 0xFFFFFF
        ink = [y for y in range(lb.height())
               if any((img.pixel(x, tl.y() + y) & 0xFFFFFF) != bg
                      for x in range(tl.x(), tl.x() + lb.width()))]
        # TWO assertions, because either alone would pass for the wrong reason: the BOX has to be
        # at least as tall as the string's ink (the structural fix), and the ink has to really
        # composite that many rows (the proof it did). `>=` on the second because antialiasing
        # legitimately tints one row past the tight rect — 13 rows for a 12 px "1:08.771".
        assert lb.height() >= wanted, (
            f"{name}: a {lb.height()} px box for a string whose ink is {wanted} px tall — the "
            f"glyph is clipped top and bottom")
        assert len(ink) >= wanted, (
            f"{name}: only {len(ink)} ink rows composited in a {lb.height()} px box for {wanted} "
            f"px of ink")
    # ...and the claim is a property of the TYPE STEP, so the two tiles are one height. A tile
    # whose height depended on its own string is a tile that can move its neighbours' captions.
    assert marked.value.height() == plain.value.height(), (
        f"the ⊘ tile's value box is {marked.value.height()} px and a digits-only tile's is "
        f"{plain.value.height()} — a shared grid row is as tall as its tallest member, so the "
        f"shorter tile's caption sits {marked.value.height() - plain.value.height()} px low")
    assert plain.value.height() >= plain.value.fontMetrics().height()
    print(f"test_the_tile_value_claims_the_height_its_own_ink_needs OK "
          f"(marked {marked.value.height()} px == digits {plain.value.height()} px, over a "
          f"{plain.value.fontMetrics().height()} px line box)")


def test_the_lap_grids_trailing_spacer_never_paints_under_the_pointer_floor():
    """QA D4-12, swept at 1 px rather than sampled — the defect is size-dependent and a one-size
    test is exactly what let a one-pixel elision bug through in an earlier wave.

    The blank trailing spacer used to floor at MIN_SECTION_PX, which is what a SQUEEZED panel
    always produces: a 4x29 enabled, clickable header section at the panel's right edge, four
    pixels against a declared HIT_MIN of 24. It is hidden rather than collapsed now."""
    from test_lap_table_empty_states import _FakeLapSession
    table = _keep(LT.LapTable(_FakeLapSession(valid=3)))
    _host(table, (900, 420))
    spacer = table._n_real_cols()
    tb = table.table
    under, widths = [], set()
    for w in range(360, 1441):
        table.resize(w, table.height())
        tb.resize(w, tb.height())
        table._fit_columns()
        if tb.isColumnHidden(spacer):
            continue
        px = tb.columnWidth(spacer)
        widths.add(px)
        if px < theme.HIT_MIN:
            under.append((w, px))
    assert not under, (
        f"the trailing spacer paints an enabled header section under HIT_MIN={theme.HIT_MIN} at "
        f"{len(under)} of 1081 panel widths — first few {under[:6]}")
    assert widths, "the spacer was hidden at EVERY width — the sweep proved nothing"
    print(f"test_the_lap_grids_trailing_spacer_never_paints_under_the_pointer_floor OK "
          f"(1081 widths 360..1440; visible spacer widths {min(widths)}..{max(widths)} px)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STATE-SURFACE TESTS PASSED", flush=True)
