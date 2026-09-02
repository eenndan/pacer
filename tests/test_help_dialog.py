"""Help-menu dialogs (fix/qa-help-dialogs): the shortcut card + the two read-only copy cards.

These pin the three failure modes the QA sweep found in studio/help_dialog.py, all of which are
"the card silently says less than it should":

  * L1-01 (a regression of #119) — a wrapping description was given the SINGLE-LINE height, so the
    LAYOUT row painted its second line outside the row, sliced through the letterforms. #119 only
    widened the card (440 -> 560), which changes WHICH strings wrap, never how tall a wrapped row
    is. Pinned at the card's natural size AND at 600 px, because a width bump is not the fix.
  * L1-08 — ⌘O was a live binding with no row on the card that calls itself the single source of
    truth. The set-difference test below is the general guard: every accelerator and QShortcut the
    window binds must appear in SHORTCUT_GROUPS, so the NEXT one cannot go undocumented either.
  * L1-07 — the card hand-typed "⌘⇧S" where Qt paints "⇧⌘S". The rows now hold the QKeySequence,
    so the glyphs come from Qt. NOTE: this asserts the card's text EQUALS the live binding's
    NativeText, never a literal glyph — QKeySequence.FullScreen resolves to F11 under the
    offscreen platform and to ⌃⌘F on a real Mac, and both must pass.
  * L1-05 — About / Privacy could be shrunk below their own copy with no scrollbar and no cue.
  * L1-04 — the privacy card must name every store the app writes, not two of the three.

Real offscreen Qt widgets; no pacer, no telemetry file. The binding half builds a StudioWindow via
__new__ + QMainWindow.__init__ (the seam the other offscreen app tests use), so no session loads.
Run: QT_QPA_PLATFORM=offscreen python tests/test_help_dialog.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QMainWindow,
    QScrollArea,
)

_APP = QApplication.instance() or QApplication([])

from studio import help_dialog, theme  # noqa: E402
from studio.help_dialog import (  # noqa: E402
    PRIVACY_PARAGRAPHS,
    SHORTCUT_GROUPS,
    AboutDialog,
    PrivacyDialog,
    ShortcutsDialog,
)

# The wrapped height of a paragraph depends on the font, so measure against the app's REAL theme —
# without this every height below is Qt's default-palette default-font guess.
theme.register_fonts()
theme.apply_theme(_APP)


def _shown(dialog):
    """Show a dialog offscreen and let its layout settle (wrapping needs a few passes)."""
    dialog.show()
    for _ in range(6):
        _APP.processEvents()
    return dialog


def _overflowing(dialog):
    """Every wrapping label whose wrapped text is taller than the row it was given."""
    return [(lb.text(), lb.width(), lb.height(), lb.heightForWidth(lb.width()))
            for lb in dialog.findChildren(QLabel)
            if lb.wordWrap() and lb.heightForWidth(lb.width()) > lb.height()]


# ------------------------------------------------------- L1-01: wrapped rows get their height
def test_shortcut_rows_are_tall_enough_at_the_natural_size():
    d = _shown(ShortcutsDialog())
    bad = _overflowing(d)
    assert not bad, f"{len(bad)} row(s) paint outside their row at {d.width()}x{d.height()}: {bad}"
    # The row the regression showed: it wraps at 560 px, and wrapping must make it TALLER.
    layout_row = [lb for lb in d.findChildren(QLabel)
                  if lb.text().startswith("Maximize a panel")]
    assert len(layout_row) == 1, layout_row
    lb = layout_row[0]
    assert lb.height() >= lb.heightForWidth(lb.width()) > 0, (lb.height(), lb.width())
    d.close()
    print("test_shortcut_rows_are_tall_enough_at_the_natural_size OK")


def test_shortcut_rows_are_tall_enough_at_other_widths():
    """Width-independence — the point of the fix. 560 is where the longest description misses its
    box by 5 px today; a wider card only changes which strings wrap, and a narrower one wraps
    more of them. None of those may clip."""
    for width in (520, 560, 600, 700, 900):
        d = _shown(ShortcutsDialog())
        d.resize(width, d.height())
        for _ in range(6):
            _APP.processEvents()
        bad = _overflowing(d)
        assert not bad, f"at {width} px: {bad}"
        d.close()
    print("test_shortcut_rows_are_tall_enough_at_other_widths OK")


# ------------------------------------- L1-08 + L1-07: the card IS the single source of truth
def _live_bindings():
    """Every key the real window binds: the menu accelerators + the window-level QShortcuts.
    Built off the same seam the other offscreen app tests use (__new__ + QMainWindow.__init__),
    so no session is loaded and no media is touched."""
    from studio import library, units
    from studio.app import StudioWindow
    win = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(win)
    # The three view-state fields _build_menu seeds its checkable actions from; __init__ (which we
    # skip, since it would load a recording) normally reads them out of prefs.
    win._excluded_visible = True
    win._colorblind = False
    win._speed_unit = units.KMH
    with tempfile.TemporaryDirectory() as tmp:                      # never read the real library
        library._app_support_dir, real = (lambda: tmp), library._app_support_dir
        try:
            win._build_menu()
        finally:
            library._app_support_dir = real
    win._build_shortcuts()
    keys = {}
    for name in dir(win):
        if not name.endswith("_action"):
            continue
        action = getattr(win, name, None)
        seq = getattr(action, "shortcut", None)
        if seq is not None and callable(seq) and not action.shortcut().isEmpty():
            keys[action.shortcut().toString(QKeySequence.NativeText)] = action.text()
    for sc in win.findChildren(QShortcut):
        keys.setdefault(sc.key().toString(QKeySequence.NativeText), "QShortcut")
    return win, keys


def test_every_live_binding_is_on_the_card():
    """The guard for the whole L1-08 class: a binding added in app.py with no row here fails the
    build. Matching is on Qt's own NativeText, so it is platform-correct by construction."""
    win, live = _live_bindings()
    card = {help_dialog._key_text(key) for _group, rows in SHORTCUT_GROUPS for key, _d in rows}
    # The multi-key rows document several bindings in one cell ("1 · 2 · 3 · 4", "F1  ·  ?").
    documented = set(card)
    for row in card:
        documented.update(part.strip() for part in row.split("·"))
    missing = {k: v for k, v in live.items() if k not in documented}
    assert not missing, f"bound but undocumented: {missing}\ncard rows: {sorted(card)}"
    assert "⌘O" in documented or "Ctrl+O" in documented, sorted(card)
    win.deleteLater()   # never shown; close() would run closeEvent's session teardown
    print(f"test_every_live_binding_is_on_the_card OK ({len(live)} bindings)")


def test_card_glyphs_match_the_live_bindings():
    """L1-07: the card's modifier rows read exactly what Qt paints in the menu bar. Asserted
    against the LIVE QAction, never a literal glyph — QKeySequence.FullScreen is F11 offscreen
    and ⌃⌘F on macOS, and this must hold on both."""
    win, _live = _live_bindings()
    rows = {desc: help_dialog._key_text(key)
            for _group, group_rows in SHORTCUT_GROUPS for key, desc in group_rows}
    for handle, desc in (("_open_action", "Open a recording"),
                         ("_undo_action", "Undo the last start/finish-line edit"),
                         ("_stats_action", "Session statistics, full-window (again / ⛶ to restore)"),
                         ("_fullscreen_action", "Enter / exit full screen")):
        live = getattr(win, handle).shortcut().toString(QKeySequence.NativeText)
        assert rows[desc] == live, f"{handle}: card says {rows[desc]!r}, Qt paints {live!r}"
    # And the specific drift the sweep measured: Qt's order is ⇧⌘S, not ⌘⇧S.
    stats = rows["Session statistics, full-window (again / ⛶ to restore)"]
    assert stats == QKeySequence("Ctrl+Shift+S").toString(QKeySequence.NativeText), stats
    win.deleteLater()   # never shown; close() would run closeEvent's session teardown
    print("test_card_glyphs_match_the_live_bindings OK")


# ------------------------------------------------- L1-05: the copy cards refuse to be truncated
def test_copy_cards_refuse_to_shrink_below_their_copy():
    for cls, ask in ((AboutDialog, (380, 200)), (PrivacyDialog, (460, 260))):
        d = _shown(cls())
        natural = d.height()
        d.resize(*ask)
        for _ in range(6):
            _APP.processEvents()
        assert d.height() >= natural, (cls.__name__, natural, d.height())
        assert d.minimumSize().height() >= natural, (cls.__name__, d.minimumSize())
        d.close()
    print("test_copy_cards_refuse_to_shrink_below_their_copy OK")


def test_copy_cards_scroll_rather_than_clip():
    """The backstop for a card whose copy outgrows the display: the paragraphs live in a scroll
    area, so even a screen-capped height keeps every line reachable."""
    for cls in (AboutDialog, PrivacyDialog):
        d = _shown(cls())
        scrolls = d.findChildren(QScrollArea)
        assert len(scrolls) == 1, (cls.__name__, scrolls)
        d.close()
    print("test_copy_cards_scroll_rather_than_clip OK")


# --------------------------------------------------- L1-04: the privacy card names every store
def test_privacy_card_names_every_store_it_writes():
    from studio import library, prefs, track_db
    copy = " ".join(PRIVACY_PARAGRAPHS)
    for store in (os.path.basename(prefs.prefs_path()),
                  os.path.basename(track_db.db_path()),
                  os.path.basename(library.library_path())):
        assert store in copy, f"{store} is written by the app but not disclosed"
    assert ".pacer.json" in copy or "pacer.json" in copy, copy
    # The sidecar bullet's "only" was false at app scope (the app also stores GPS coordinates in
    # tracks.json and a filesystem path in prefs.json), under a card that frames the list as
    # exhaustive — "Everything below stays on this computer".
    assert "contains only the line coordinates" not in copy, copy
    # Every documented removal route must reach every store.
    removal = [p for p in PRIVACY_PARAGRAPHS if p.startswith("How to remove")]
    assert len(removal) == 1, removal
    assert "Application Support/pacer" in removal[0], removal[0]
    print("test_privacy_card_names_every_store_it_writes OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} HELP-DIALOG TESTS PASSED", flush=True)
