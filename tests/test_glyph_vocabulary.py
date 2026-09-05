"""The GLYPH guard — the sibling of tests/test_design_system.py (dimensions) and
tests/test_contrast.py (colour). This one is about the third vocabulary: the marks.

WHY THIS FILE EXISTS. Pacer had FOUR mechanisms for drawing a glyph and only one of them was
designed. `theme.icon()` — qtawesome's Phosphor set — was measured across all fifteen live icon
controls at ONE size (`ICON_PX` inside `ICON_BTN`) with the ink centred within 0.5 px of its box on
every one, at DPR 1 and DPR 2. The map key paints its own markers, deliberately, because no icon
font can draw the symbol that is on the plot right now. And then there was the fourth: a literal
Unicode character dropped into a label's text.

That fourth mechanism has no author. Qt falls back PER CHARACTER to whatever the OS can supply, so
on this machine one 32-character line in the excluded strip carried a 16x14 ⊘ from Inter, an 8.0 px
cap-height, and a 4x4 ▸ from `.AppleSystemUIFont` — a 4x range of ink in two faces, and no line of
code chose either. Measured across the app, eight marks resolved OUTSIDE the app's own font into
`.AppleSystemUIFont`, `Apple Symbols`, `STIX Two Math`, `Menlo` and `.Apple Color Emoji UI`, at
sizes from 4 px of ink to 16 px of ink.

So the rule, and it is a rule about FALLBACK rather than about any particular character:

    a character in a user-visible string must be drawn by the app's own UI face.

Inter carries ⊘ ★ ⚠ ▶ ▲ ▼ ⌘ ⇧ ⌃ · • → Δ ± × − ≥ ↔ σ and the rest of the notation set, so those
are legitimately text and stay text — that half of the finding was ASSUMED and is measured here
(check 2) rather than believed. It does not carry ▸ ▾ ⟲ ⟳ ⛶ ⤢ ✕ or any emoji; those became
`theme.icon()` pixmaps, or words.

THE THREE CHECKS

  1. NO GLYPH FALLS OUT OF THE FACE. An AST walk over the guarded modules' string literals
     (docstrings excluded — they are not painted), each hit labelled by the CLASS AND METHOD that
     owns it, the same shape test_design_system.py and test_contrast.py use, so an EXEMPT entry
     names a decision rather than a line number. Resolution is LIVE, through
     `_qtapp.themed_app()` and `theme.ui_font()`, because a test that measures a font stack the app
     does not ship measures nothing (that has happened here before).

  2. THE LEDGER IS MEASURED. The two sets above — what Inter has and what it lacks — are asserted
     against the real shipped face, so a font update that changes either one fails loudly here
     instead of silently reopening the finding somewhere else.

  3. THE SURFACES THAT WERE FIXED STAY FIXED. The excluded strip's two marks are icons and its
     caret says which way a click goes; all three corner tables carry the turn sense in the cell's
     ICON slot rather than in its text; the Shortcuts card documents the two Layout controls with
     the glyph those controls actually paint (read out of `central_view` / `video_view` by AST, so
     the card cannot drift from either); and the PB moment has no colour emoji.

SCOPE. The walk covers the five modules the icon-vocabulary PR owns. The rest of `studio/` has two
known hits that are hand-offs to other lanes, both filed and both in files this PR must not touch:
`overlays.py`'s ✕ toast-dismiss (Menlo, 7x7 — `ph.x`, and the button is already 24x24) and the
`▲`/`▼` delta arrows in `theme.py`, which are Inter but land on a `MONO_STACK` QSS surface where
Menlo draws them 4 px shorter than the digits beside them. Widening `_GUARDED` is what closes them.

Run: QT_QPA_PLATFORM=offscreen python tests/test_glyph_vocabulary.py
"""
import ast
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from PySide6.QtGui import QFont, QTextLayout  # noqa: E402
from PySide6.QtWidgets import QLabel, QTableWidgetItem  # noqa: E402

from studio import coaching, data_quality, help_dialog, lap_table, library, theme  # noqa: E402

_STUDIO = os.path.join(_REPO, "studio")

# The modules this guard covers. Not "every module": the walk is only honest where the surfaces
# have actually been migrated, and a guard that starts green everywhere it looks is worth more than
# one carrying a backlog of exemptions (see SCOPE in the docstring for the two open hand-offs).
_GUARDED = ("lap_table", "stats_panel", "coaching_panel", "help_dialog", "library")

# (module, owning scope, character, why it is allowed to fall out of the face).
#
# ONE decision per entry, and each names the file that owns the decision rather than this one.
_EXEMPT = {
    ("lap_table", "<module>", "⟲",
     "CORNER_DIR_GLYPH — the ⟲/⟳ CODEPOINTS, kept solely for share_card.py, which composes the "
     "lap-card IMAGE with QPainter and has no cell to hang an icon in. The three TABLES that used "
     "to read this now take the arrow from CORNER_DIR_ICON. Whether a painted card wants a painted "
     "arrow is share_card.py's decision, and share_card.py is not this PR's file."),
    ("lap_table", "<module>", "⟳", "see the ⟲ entry above — the same constant."),
    ("coaching_panel", "OpportunitiesPanel.__init__", "▸",
     "A MENU PATH inside a sentence ('Open Coaching ▸ Opportunities…'), not an affordance. The "
     "convention is app-wide — app.py alone spells it in ~40 strings — so the connector's face is "
     "one decision for the whole app and it belongs with app.py/theme.py, not with two of forty "
     "sites changed here. Filed as a hand-off (D1-01: UI_FAMILIES names three faces that do not "
     "exist on this machine, which is what sends any Inter-less character to a foreign font)."),
    ("help_dialog", "<module>", "▸",
     "PRIVACY_PARAGRAPHS' removal route — 'open File ▸ Library…'. The same menu-path connector as "
     "the coaching_panel entry above, and the same hand-off."),
}
assert all(len(e) == 4 for e in _EXEMPT)


# --------------------------------------------------------------------------- font resolution
def _family(font: QFont, ch: str) -> str:
    """The family Qt REALLY shaped `ch` with under `font` — after per-character fallback. This is
    the whole measurement: `font.family()` says what was asked for, this says what was drawn."""
    layout = QTextLayout(ch, font)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(9999)
    layout.endLayout()
    runs = layout.glyphRuns()
    return runs[0].rawFont().familyName() if runs else ""


_UI = theme.ui_font(theme.TABLE)
_UI_FACE = _family(_UI, "A")          # what plain Latin text resolves to == the app's real UI face


def _falls_out_of_the_face(ch: str) -> bool:
    """True when `ch` had to be fetched from some other font than the one the words beside it are
    drawn in — i.e. Qt performed a fallback nobody chose."""
    return _family(_UI, ch) != _UI_FACE


# --------------------------------------------------------------------------- the source walk
def _literals(path: str):
    """(lineno, owning scope, string) for every string literal in `path` that is NOT a docstring.

    Docstrings and comments are excluded because they are never painted; the scope is the dotted
    class/function that owns the literal, so an exemption reads as a decision about a surface."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) \
           and body and isinstance(body[0], ast.Expr) \
           and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))
    out = []

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, f"{scope}.{child.name}" if scope else child.name)
                continue
            if isinstance(child, ast.Constant) and isinstance(child.value, str) \
               and id(child) not in docstrings:
                out.append((child.lineno, scope or "<module>", child.value))
            walk(child, scope)

    walk(tree, "")
    return out


def test_no_glyph_in_a_guarded_module_falls_out_of_the_app_font():
    """Check 1. Every character the guarded modules put on screen is drawn by the face the app
    declares, not by whatever the OS happened to supply for that codepoint."""
    exempt = {(m, s, c) for m, s, c, _why in _EXEMPT}
    hits, seen = [], set()
    for module in _GUARDED:
        for lineno, scope, text in _literals(os.path.join(_STUDIO, module + ".py")):
            for ch in text:
                if ch.isascii():
                    continue
                seen.add(ch)
                if (module, scope, ch) in exempt or not _falls_out_of_the_face(ch):
                    continue
                hits.append(f"{module}.py:{lineno} [{scope}] {ch!r} U+{ord(ch):04X} is drawn by "
                            f"{_family(_UI, ch)!r}, the words beside it by {_UI_FACE!r} "
                            f":: {text[:60]!r}")
    assert not hits, (
        f"{len(hits)} glyph(s) in a guarded module are drawn by a font nobody chose — use "
        f"theme.icon() (a pixmap beside the words) or say it in words:\n  " + "\n  ".join(hits))
    print(f"test_no_glyph_in_a_guarded_module_falls_out_of_the_app_font OK "
          f"({len(seen)} distinct non-ASCII characters across {len(_GUARDED)} modules, all "
          f"{_UI_FACE}; {len(_EXEMPT)} exemptions)")


# --------------------------------------------------------------------------- the ledger
# Measured, not assumed. The brief this came from asserted the opposite of the first row.
_IN_THE_FACE = "⊘★⚠▶▲▼⌘⇧⌃⌥←→·•±×−≥↔Δσ…—–“”"
_OUT_OF_THE_FACE = "▸▾⟲⟳⛶⤢✕🏁"


def test_the_ledger_of_what_the_face_can_draw_is_measured():
    """Check 2. The two sets the fix was designed against, asserted against the SHIPPED face. A
    font upgrade that gives Inter a ⟳ (or takes away its ⊘) reopens the whole finding, and this is
    where that has to surface — not in a screenshot six months later."""
    assert _UI_FACE, "no face resolved at all — the theme's fonts are not registered"
    missing = [c for c in _IN_THE_FACE if _falls_out_of_the_face(c)]
    assert not missing, (
        f"{_UI_FACE} no longer draws {missing} — these are marks the app deliberately keeps as "
        f"TEXT because the face carried them. Re-measure before changing anything else.")
    present = [(c, _family(_UI, c)) for c in _OUT_OF_THE_FACE if not _falls_out_of_the_face(c)]
    assert not present, (
        f"{_UI_FACE} now draws {present} — the fallback these were replaced for is gone, so the "
        f"ledger above is stale. That is good news; update it and re-check the surfaces.")
    print(f"test_the_ledger_of_what_the_face_can_draw_is_measured OK ({_UI_FACE} draws "
          f"{len(_IN_THE_FACE)} of the app's marks, falls back on {len(_OUT_OF_THE_FACE)})")


# --------------------------------------------------------------------------- the fixed surfaces
class _FakeLapSession:
    """The LapTable read surface, with excluded laps so the strip is populated."""

    timing_verified = True
    timing_quality = data_quality.TimingQuality()

    def __init__(self, valid=25, excluded=24, detected=50):
        self.valid, self.excluded, self.detected = valid, excluded, detected

    def lap_rows(self):
        return [{"idx": i, "time": 13.0 + i * 0.1, "dist": 203.0, "entry": 50.0}
                for i in range(self.valid)]

    def excluded_lap_rows(self):
        return [{"idx": 100 + i, "time": 34.0 + i * 0.1, "dist": 536.0, "entry": 50.0}
                for i in range(self.excluded)]

    def lap_count(self):
        return self.detected

    def sector_count(self):
        return 0

    def lap_sector_splits(self, lap_id):
        return []

    def session_best_splits(self):
        return []

    def best_lap_id(self):
        return 0 if self.valid else None

    def dropout_lap_ids(self):
        return set()


def test_the_excluded_strip_draws_its_two_marks_with_theme_icon():
    """Check 3a. The ⊘ was Inter's U+2298 asking for a 15.53 px box inside a 14 px line, so its
    apex and base arcs were cut off; the ▸ beside it was 4x4 px of `.AppleSystemUIFont` and was the
    ONLY affordance on a click target. Both are theme.icon() pixmaps now — at ICON_PX, tinted, and
    named in the accessibility tree, because a pixmap has no text for a screen reader.

    The CARET IS A STATE: it must say which way the next click goes, and it must escalate to the
    attention amber with the words when the strip does (a QSS `tone` rule cannot reach a pixmap)."""
    table = lap_table.LapTable(_FakeLapSession(valid=25, excluded=24, detected=50))
    mark, caret = table._excluded_mark, table._excluded_caret
    assert mark.glyph_name() == lap_table.EXCLUDED_ICON
    assert mark.accessibleName(), "the ⊘'s meaning must survive as accessible text"
    assert not mark.pixmap().isNull(), "the mark renders no pixels"
    assert mark.width() == mark.height() == theme.ICON_PX, (mark.width(), mark.height())
    # Collapsed (the default) → the caret points at the list it would open; expanded → it closes.
    assert table._excluded_collapsed
    assert caret.glyph_name() == lap_table.EXPAND_ICON
    table._toggle_excluded_collapsed()
    assert caret.glyph_name() == lap_table.COLLAPSE_ICON
    assert caret.accessibleName() == "collapse", caret.accessibleName()
    # 24 of 49 is past EXCLUDED_WARN_RATIO, so both glyphs wear the amber the words wear.
    assert table._excluded_header.property("tone") == "warn"
    assert mark._colour == theme.C.accent and caret._colour == theme.C.accent
    # ...and a calm strip drops back to the BarLabel dim, in step with the label.
    calm = lap_table.LapTable(_FakeLapSession(valid=21, excluded=1, detected=22))
    assert calm._excluded_header.property("tone") in (None, "")
    assert calm._excluded_mark._colour == theme.C.text_dim
    # The words keep the whole sentence: nothing that used to be a glyph carried a number.
    assert "24 excluded of 49 laps" in table._excluded_header.text()
    print("test_the_excluded_strip_draws_its_two_marks_with_theme_icon OK")


def test_every_corner_table_carries_the_direction_in_the_icon_slot():
    """Check 3b. Three tables render a corner-identity cell — Corners, Stats ▸ CORNERS and
    Coaching — and all three used to format ⟲/⟳ into the cell's TEXT, where Apple Symbols drew them
    at 8x7 px, 3 px shorter than the digits and 1.5 px below their centre, while the column header
    promised the reader could tell a left-hander from a right one.

    They now share ONE builder (`lap_table.set_corner_direction`) that puts the arrow in the item's
    icon slot and names it on hover, so the cell's text is the bare label and the three surfaces
    cannot drift."""
    left = lap_table.set_corner_direction(QTableWidgetItem("C1"), 1)
    right = lap_table.set_corner_direction(QTableWidgetItem("C2"), -1)
    unknown = lap_table.set_corner_direction(QTableWidgetItem("C3"), 0)
    assert left.text() == "C1" and not left.icon().isNull()
    assert right.text() == "C2" and not right.icon().isNull()
    assert unknown.icon().isNull(), "a corner with no measured turn sense gets no arrow"
    assert left.toolTip() != right.toolTip(), "the two directions must not say the same thing"
    for item in (left, right):
        assert item.toolTip(), "the mark has to name itself somewhere it can be hovered"
    # The two glyphs are genuinely different names (the 8x7 characters were near-indistinguishable).
    assert lap_table.CORNER_DIR_ICON[1] != lap_table.CORNER_DIR_ICON[-1]
    # The Coaching panel's cell builder goes through the same function.
    reason = coaching.Reason(kind=coaching.REASON_NONE, contribution=0.0, apex_speed_deficit=0.0,
                             brake_extra_s=0.0, coast_extra_s=0.0, sigma=0.1)
    opp = coaching.Opportunity(cid=7, direction=-1, time_lost=0.42, entry_dist=40.0,
                               reason=reason)
    from studio.coaching_panel import _corner_cell
    cell = _corner_cell(opp)
    assert cell.text() == "C7", cell.text()
    assert not cell.icon().isNull(), "the Coaching corner cell lost its direction mark"
    print("test_every_corner_table_carries_the_direction_in_the_icon_slot OK")


def _assigned_toggle_glyph(module: str, attribute: str) -> str:
    """The `glyph=` a `ToggleButton(...)` was constructed with, read out of `module`'s SOURCE for
    the button assigned to `self.<attribute>`. AST rather than import: the point is to read what
    the control declares without dragging the whole view stack (and the media pipeline) into this
    test, and the video transport's glyph is an inline keyword argument — there is no constant to
    import even if we wanted one."""
    tree = ast.parse(open(os.path.join(_STUDIO, module + ".py"), encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        named = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        if named != "ToggleButton":
            continue
        targets = [t for t in node.targets
                   if isinstance(t, ast.Attribute) and t.attr == attribute]
        if not targets:
            continue
        for kw in call.keywords:
            if kw.arg == "glyph" and isinstance(kw.value, ast.Constant):
                return kw.value.value
    raise AssertionError(f"no ToggleButton(glyph=...) assigned to self.{attribute} in {module}.py")


def _module_constant(module: str, name: str) -> str:
    """A module-level `NAME = "literal"` read out of source, for the same reason as above."""
    tree = ast.parse(open(os.path.join(_STUDIO, module + ".py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value.value
    raise AssertionError(f"{module}.py has no module-level {name}")


def test_the_shortcuts_card_documents_the_buttons_own_glyphs():
    """Check 3c. Two adjacent rows of one key column documented two controls with characters
    NEITHER control uses: ⛶ (STIX Two Math, 8x8) for a button that paints Phosphor ph.corners-out
    at 10x10, and ⤢ (Apple Symbols, 6x5) for one that paints ph.arrows-out. Change a button's glyph
    and the documentation silently became wrong.

    The card now renders those two keycaps from the same Phosphor names the buttons pass to
    theme.icon(), and this is the binding that keeps them equal. The two controls declare their
    glyph in two different ways — central_view names a module constant, video_view passes a keyword
    inline — so both are read from source, and the card is asserted against each."""
    layout = dict(help_dialog.SHORTCUT_GROUPS)["Layout"]
    glyph_rows = [key for key, _desc in layout if isinstance(key, help_dialog.GlyphKey)]
    assert len(glyph_rows) == 2, glyph_rows
    assert help_dialog.MAXIMIZE_GLYPH == _module_constant("central_view", "_MAXIMIZE_GLYPH")
    assert help_dialog.VIDEO_FULLSCREEN_GLYPH == _assigned_toggle_glyph("video_view",
                                                                       "fullscreen_btn")
    # The constant central_view declares is the one it hands to theme.icon() — otherwise the card
    # would be pinned to a string the button had stopped using.
    source = open(os.path.join(_STUDIO, "central_view.py"), encoding="utf-8").read()
    painted = [node for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "icon"
               and any(isinstance(a, ast.Name) and a.id == "_MAXIMIZE_GLYPH"
                       or isinstance(a, ast.IfExp)
                       and any(isinstance(b, ast.Name) and b.id == "_MAXIMIZE_GLYPH"
                               for b in (a.body, a.orelse))
                       for a in node.args)]
    assert painted, "central_view._MAXIMIZE_GLYPH is no longer passed to theme.icon()"
    # And the card really builds an icon beside the words, not a character inside them.
    for key in glyph_rows:
        assert not any(c in key.text for c in _OUT_OF_THE_FACE), key.text
        cell = help_dialog._key_widget(key)
        painted = [lb for lb in cell.findChildren(QLabel) if not lb.pixmap().isNull()]
        assert len(painted) == 1, f"the {key.glyph} keycap painted {len(painted)} pixmaps"
        assert painted[0].width() == theme.ICON_PX, painted[0].width()
        # ...beside the words, not instead of them.
        assert any(lb.text() == key.text for lb in cell.findChildren(QLabel))
    print(f"test_the_shortcuts_card_documents_the_buttons_own_glyphs OK "
          f"({help_dialog.MAXIMIZE_GLYPH} / {help_dialog.VIDEO_FULLSCREEN_GLYPH})")


def test_the_personal_best_moment_carries_no_colour_emoji():
    """Check 3d. The 🏁 on the PB toast was the app's ONLY colour glyph — `.Apple Color Emoji UI`,
    a 19 px advance in a 13 px line — on the one surface designed as a peak moment. Phosphor has no
    chequered flag (4,470 names, none of them flag-checkered), so there is no swap; the sentence
    celebrates on its own."""
    beat = {"kind": "beat", "track": "Daytona MK", "best": 68.42, "prior": 70.0,
            "improvement": 1.58}
    first = {"kind": "first", "track": "Daytona MK", "best": 68.42}
    for moment in (beat, first):
        title, body = library.pb_moment_text(moment, lambda s: f"{s:.2f}")
        for text in (title, body):
            bad = [c for c in text if ord(c) > 0xFFFF or _falls_out_of_the_face(c)]
            assert not bad, f"{text!r} carries {bad}"
    assert "personal best" in library.pb_moment_text(beat, lambda s: f"{s:.2f}")[0].lower()
    print("test_the_personal_best_moment_carries_no_colour_emoji OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} GLYPH-VOCABULARY TESTS PASSED", flush=True)
