"""_qtapp — the ONE way a Qt test gets its QApplication: themed, in the app's own order.

    from _qtapp import themed_app
    _APP = themed_app()          # module scope, BEFORE the first widget is built

WHY THIS EXISTS. A widget's size is a function of its font, so a test that measures a width, a
column, a row height or an elide is only testing the shipping app if it measures in the shipping
app's font. Three ways to get that wrong were all live in this suite at once:

  * no theme at all — the file measured Qt's default stack. tests/test_stats.py asserted the Stats
    tiles' `font().italic()` on an unthemed view (green for a reason that had nothing to do with
    the app), and tests/test_lap_table_columns.py pinned the Δ-header elide guard on column widths
    up to 34 % off the shipped ones.
  * apply_theme() without register_fonts() — the QSS names "Inter", the bundled TTFs were never
    loaded, and Qt substituted a family, so the charts column's asserted minimum was 752 px in-test
    against 759 px shipped. (theme.apply_theme now registers the fonts itself, so this one can no
    longer happen — but going through here keeps the ordering right as well.)
  * theming AFTER building the widget — studio/app.py registers fonts and applies the theme before
    the first widget exists. A test that builds a CentralView first freezes construction-time
    metrics into it (the charts header's x-axis combo takes a fixed minimumWidth from its
    sizeHint), then themes, and measures a control whose box was sized in a different font.

So: call this at MODULE scope, before importing anything that builds widgets. It is idempotent,
so several test modules importing each other is fine.

It deliberately does NOT redirect prefs/library/track_db — a file that persists state owns its own
temp redirect (see tests/test_library.py, tests/test_export_gates.py).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def themed_app():
    """The process QApplication with the bundled fonts registered and the theme applied — the
    same pair, in the same order, that studio/app.py does before it builds the window."""
    from PySide6.QtWidgets import QApplication

    from studio import theme

    app = QApplication.instance() or QApplication([])
    theme.register_fonts()       # idempotent; apply_theme calls it too, kept explicit here
    theme.apply_theme(app)
    return app
