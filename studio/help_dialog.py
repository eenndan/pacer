"""Help-menu dialogs: the keyboard-shortcut reference + the About card.

The whole interaction model (Space/M/G/C toggles, ←/→ stepping, chart-cursor scrub) is otherwise
undiscoverable — there's no on-screen hint for any of it. These two themed QDialogs are the
discoverable surface, opened from the Help menu (and F1). (The draggable start/finish line is the
exception: an unverified-timing recording now surfaces a persistent banner + an on-canvas "drag to
set start/finish" cue, so that one IS discoverable on the recordings where it matters.)

Single source of truth: SHORTCUT_GROUPS below is the ONE place the shortcut text lives. The keys
listed here MUST stay in lockstep with the actual bindings, which are defined in
``StudioWindow._build_shortcuts`` (Space / M / G / C / 1-4 / ?), ``StudioWindow.keyPressEvent``
(the ←/→ ± stepping) and the menus (⌘O, ⌘Z, ⌃⌘F, ⇧⌘S, F1). Every accelerator row stores the
``QKeySequence`` itself rather than hand-typed glyphs, so the card renders exactly what Qt paints
in the menu bar (``⇧⌘S``, not ``⌘⇧S``) on whatever platform it runs — see ``_key_text``. The
drag / double-click interactions have no key binding — they're handled in MapView (the draggable
start/finish line), ScrubController (the chart cursor), CentralView (double-click a panel header
or the maximize button to maximize that panel) and VideoView (double-click the video / the
fullscreen transport button to fill the screen) — so they're documented here as the only place a
user can learn them. Both of those buttons are documented with THEIR OWN glyph (see
MAXIMIZE_GLYPH), not with a lookalike character.
If you change a binding in app.py, change it HERE too — tests/test_help_dialog.py fails the build
when a live binding has no row.
"""

from __future__ import annotations

from typing import NamedTuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__, theme
from .widgets import WrapLabel

# ---------------------------------------------------------------- the two documented BUTTONS
# THE CARD PAINTS THE BUTTON'S OWN GLYPH, NOT A LOOKALIKE (D1-06).
#
# Two adjacent Layout rows documented two controls with characters neither control uses: ⛶ (U+26F6,
# which Inter lacks, so macOS resolved it to STIX Two Math — 8x8 px of ink) for the panel-maximize
# button, which actually paints Phosphor ph.corners-out at 10x10, and ⤢ (U+2922 -> Apple Symbols,
# 6x5 px) for the video-fullscreen button, which paints ph.arrows-out at 10x10. Two rows of one key
# column, two fallback faces, neither used by any button in the app. Change the button's glyph and
# the documentation silently became wrong.
#
# These are the names the buttons paint, and tests/test_glyph_vocabulary.py asserts each one
# against its own control — `central_view._MAXIMIZE_GLYPH` for the panel button and the literal
# `ToggleButton(glyph=...)` argument in `video_view` for the transport one, read out of the source.
#
# NAMED HERE RATHER THAN IMPORTED, on purpose. Importing central_view would drag the entire view
# stack (map + charts + pyqtgraph + the media pipeline) into a Help dialog that is otherwise a leaf,
# and `_MAXIMIZE_GLYPH` is private to that module — while the video button has NO constant to
# import at all, it passes its glyph inline to ToggleButton. So one mechanism covers both, and it
# is a stronger one than an import: an import guarantees the same STRING, the test guarantees the
# button actually paints it.
MAXIMIZE_GLYPH = "ph.corners-out"        # central_view's panel-maximize button
VIDEO_FULLSCREEN_GLYPH = "ph.arrows-out"  # video_view's transport fullscreen button


class GlyphKey(NamedTuple):
    """A key-column cell that ends in a CONTROL'S GLYPH rather than a keystroke: the words, then
    the Phosphor icon that control paints. `text` is what `_key_text` returns for the row, so the
    "every live binding is documented" guard reads it exactly like any other key."""

    text: str
    glyph: str

# ---------------------------------------------------------------- shortcut catalogue
# (key, what it does). Grouped by the same mental model the app uses: File (getting footage in),
# Playback (the transport), Navigation (moving through time + space), Analysis (the
# comparison/overlay tools). Each entry's key column is rendered in the mono face so the glyphs
# line up. See the module docstring for the cross-reference to the live bindings in app.py.
#
# The key is either a literal str (a plain letter, or a drag / double-click gesture that has no
# binding) or the QKeySequence the action is actually bound to — the latter is rendered through
# _key_text at build time so a modifier row can never drift from the menu bar's own glyphs. The
# StandardKey members stay unresolved here on purpose: resolving one needs the platform theme,
# i.e. a live QGuiApplication, which does not exist at import time.
Keys = str | QKeySequence | QKeySequence.StandardKey | GlyphKey
SHORTCUT_GROUPS: list[tuple[str, list[tuple[Keys, str]]]] = [
    ("File", [
        (QKeySequence.StandardKey.Open, "Open a recording"),
    ]),
    ("Playback", [
        ("Space", "Play / pause the video"),
        ("M", "Mute / unmute"),
    ]),
    ("Navigation", [
        ("← / →", "Step the video back / forward 1 second"),
        ("Shift + ← / →", "Step the video back / forward 5 seconds"),
        ("Drag chart cursor", "Scrub through the current lap"),
        ("Drag start/finish line", "Fix lap timing on the map (key for unknown tracks)"),
    ]),
    ("Analysis", [
        ("G", "Toggle the g-meter overlay"),
        ("C", "Toggle compare mode (two laps side by side)"),
        ("1 · 2 · 3 · 4", "Lap-panel tabs: Laps · Corners · Stats · Coaching"),
        # The DESCRIPTION named the maximize button by a character the button does not paint, in a
        # sentence — where an icon cannot go. It names the button in words instead; the Layout
        # group below is where the glyph itself is documented.
        (QKeySequence("Ctrl+Shift+S"),
         "Session statistics, full-window (again / the panel-maximize button to restore)"),
    ]),
    ("Editing", [
        # "timing-line", matching the menu item it documents: ⌘Z takes back SECTOR-line edits too,
        # and this card used to be one of the two surfaces (with the status bar) that said otherwise.
        (QKeySequence.StandardKey.Undo, "Undo the last timing-line edit"),
    ]),
    ("Layout", [
        (GlyphKey("Double-click header  ·", MAXIMIZE_GLYPH),
         "Maximize a panel to fill the window (Esc / again to restore)"),
        (QKeySequence.StandardKey.FullScreen, "Enter / exit full screen"),
        (GlyphKey("Double-click video  ·", VIDEO_FULLSCREEN_GLYPH),
         "Make the video fill the screen (Esc / again to restore)"),
        ("Drag any splitter", "Resize the panels (the layout is remembered)"),
    ]),
    ("Help", [
        ("F1  ·  ?", "Show this shortcut reference"),
    ]),
]

# Your-data & privacy disclosure (Help ▸ Your data & privacy). Honest + calm: everything is local
# and offline. The card opens by framing the list as exhaustive, so it has to BE exhaustive: one
# bullet per store the app writes — the per-video sidecar plus all three app-support files
# (library.json, prefs.json, tracks.json) — and a removal route that reaches every one of them.
# Kept here (with the shortcuts / about copy) as the single source of the app's Help-menu text.
PRIVACY_TITLE = "Your data & privacy"
PRIVACY_PARAGRAPHS = [
    "Pacer Studio runs entirely on your Mac. It does not upload, sync or share anything — no "
    "account, no network, no telemetry. Everything below stays on this computer, offline.",
    "What it stores, and where:",
    "•  Timing-line sidecar — when you place or drag a start/finish or sector line, pacer saves "
    "those lines next to your video as a small \"<name>.pacer.json\" file, so your lap timing "
    "survives a restart. It holds those lines' coordinates and the track name — no video.",
    "•  Session library — each analyzed recording is indexed in "
    "\"~/Library/Application Support/pacer/library.json\": the file path(s), track name, GPS date "
    "and lap times. This is what powers the Library list and per-track PB progression.",
    "•  Preferences — \"~/Library/Application Support/pacer/prefs.json\" remembers your speed "
    "unit, palette and panel layout, plus the last folder you opened a recording from (a path "
    "into your filesystem).",
    "•  Saved tracks — \"~/Library/Application Support/pacer/tracks.json\" holds each track you "
    "save: its name, its start/finish and sector lines, and the centre point and bounding box "
    "used to recognise the track next time. Those are GPS coordinates of where you drive.",
    "How to remove it:  open File ▸ Library…, right-click a recording and choose "
    "\"Forget this recording\" to drop it from the index and delete its sidecar, or click "
    "\"Clear library\" to wipe the whole index. That covers the sidecars and the library only — "
    "to remove everything, including your preferences and saved tracks, quit pacer and delete the "
    "folder \"~/Library/Application Support/pacer\". Your video files are never touched.",
]

APP_TAGLINE = "Race-telemetry analysis for GoPro footage."
APP_BLURB = (
    "Open a GoPro recording and Pacer Studio reconstructs the laps from its embedded GPS — then "
    "lets you scrub the footage against the map, speed / Δ charts and a g-meter overlay, compare "
    "your laps (and other recordings) side by side, and find where the time goes."
)


def _key_text(key: Keys) -> str:
    """The TEXT of one row's key column. A str is literal (a plain letter, or a drag gesture
    with no binding); a GlyphKey contributes its words (its glyph is painted beside them, see
    _key_widget); a QKeySequence / StandardKey is rendered with Qt's OWN native text, which is
    what the menu bar paints — so the card cannot disagree with the menus it documents, and a
    non-macOS run reads "Ctrl+Shift+S" instead of Mac glyphs."""
    if isinstance(key, GlyphKey):
        return key.text
    if isinstance(key, str):
        return key
    return QKeySequence(key).toString(QKeySequence.NativeText)


def _key_cap(text: str) -> QLabel:
    """One KEY CAP label: BarLabel's dimmed small-header type in the MONO face, so the glyphs line
    up into a gutter. It shipped as BarLabel + a one-line `font-family` patch that spelled the whole
    mono stack out by hand — a literal copy of theme.MONO_STACK, in a file that cannot see it
    drift. It is theme's [role="KeyCap"] now; see that rule for why this is a missing role rather
    than a legitimate one-off."""
    label = QLabel(text)
    label.setProperty("role", "KeyCap")
    label.setAlignment(Qt.AlignRight | Qt.AlignTop)
    return label


def _key_widget(key: Keys) -> QWidget:
    """The whole key column for one row: a KeyCap label, plus — for a GlyphKey — the Phosphor icon
    the documented control actually paints, rendered through the same `theme.icon()` the button
    itself goes through (see MAXIMIZE_GLYPH).

    The pair is right-aligned inside the gutter like every other cap, so the icon lands where a
    trailing character used to and the column still reads as a column."""
    if not isinstance(key, GlyphKey):
        return _key_cap(_key_text(key))
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    # SPACE_XS between the words and the glyph — the gap WITHIN one cell. The string it replaces
    # carried two mono spaces (13.2 px); ICON_PX's box already contributes ~3 px of padding around
    # its ~10 px of ink, so the sub-step lands the ink where the character's did.
    row.setSpacing(theme.SPACE_XS)
    row.addStretch(1)                       # the caps are a right-aligned gutter
    row.addWidget(_key_cap(key.text))
    icon = QLabel()
    icon.setFixedSize(QSize(theme.ICON_PX, theme.ICON_PX))
    icon.setAlignment(Qt.AlignCenter)
    # C.text_dim: the KeyCap role's own colour, so the glyph reads at the same weight as the words
    # it completes rather than louder than them.
    icon.setPixmap(theme.icon(key.glyph, color=theme.C.text_dim)
                   .pixmap(QSize(theme.ICON_PX, theme.ICON_PX)))
    # A pixmap has no text for a screen reader; the meaning has to stay in the accessibility tree.
    icon.setAccessibleName(key.glyph)
    row.addWidget(icon)
    row.setAlignment(Qt.AlignTop)
    return box


def _copy_column(spacing: int, inset: int = theme.SPACE_XL) -> tuple[QScrollArea, QVBoxLayout]:
    """The scrolling column of a read-only Help card: a frameless, vertically-scrolling QScrollArea
    around a plain widget column. Returns (scroll area, the layout to add paragraphs to).

    Both cards used to put their paragraphs straight into the dialog, which set only a minimum
    WIDTH — so every height Qt permitted below the copy's height guillotined the last paragraphs
    with no scrollbar and no cue. The scroll area guarantees the text stays reachable; _fit_to_copy
    below is what keeps it off screen in the first place.

    THE INSET IS SPACE_XL, AND PROSE DOES NOT GET A SCALE OF ITS OWN. This was `20, 18, 20, 16` —
    four numbers for one box — under a guard exemption arguing that a reading column has "its own
    typographic measure … off the scale and off it consistently". It was not consistent: the same
    claim covered the Shortcuts card's 12/10/12/12 and the export dialog's 16/14/16/14, three
    different insets for the same job, which is the definition of a nudge rather than a measure. And
    the scale already declares the step this wants — SPACE_XL is documented as "a page's own
    breathing room (empty states, dialog bodies)" — so a PROSE_* token at 20 would have bought a
    ninth spacing value to preserve numbers nobody chose.

    What a prose column legitimately owns is `spacing`, and it stays a PARAMETER because its two
    callers mean different things by it: the About card's four lines are one identity block
    (SPACE_S, the gap inside a bar), the Privacy card's are paragraphs (SPACE_M, the panel gutter).
    That is the one place these cards diverge, and now it is stated instead of being 8 and 10.

    `inset` IS THE SAME KIND OF PARAMETER, and it exists because the third Help card was the one
    card of the three with neither this scroll area nor the screen cap below it — it opened 733 px
    tall with a hard 717 px floor, off the bottom of the two smallest 13-inch scaled modes (695 and
    615 px of available height), with the Close button as the part that went missing (QA W14-03).
    The MECHANISM it was missing is generic; the reading inset is not. Shortcuts is a reference
    TABLE whose group strips run flush to the card's edges and whose rows carry their own control
    insets (see ShortcutsDialog._group_body, and the prose in tests/test_design_system.py that
    settled which surface takes which spacing), so it takes this column at inset 0 rather than
    growing a fourth margin. Sharing the scroll and the cap while keeping the typography apart is
    the whole point of the parameter — the alternative was a second copy of the mechanism."""
    body = QWidget()
    column = QVBoxLayout(body)
    column.setContentsMargins(inset, inset, inset, inset)
    column.setSpacing(spacing)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    # The copy wraps, so it never needs horizontal room — an h-scrollbar would only ever be noise.
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    return scroll, column


def _fit_to_copy(dialog: QDialog, scroll: QScrollArea, column: QVBoxLayout) -> None:
    """Open the card at the height its copy actually needs, and make that the floor — Qt then
    refuses the shrink instead of hiding the tail of the text.

    Run from showEvent, not __init__: the wrapped height of a paragraph is only real once the
    global QSS font is polished onto the labels and they have been laid out at the card's actual
    width, so a construction-time heightForWidth under-reads it (measured 588 px against a real
    940 px on the privacy card). ``totalMinimumSize`` then reads the heights WrapLabel has
    already asserted. Idempotent, so re-showing the card is a no-op. Capped at the screen: a card
    whose copy outgrows a small display scrolls rather than opening taller than the desk."""
    body = scroll.widget()
    # Lay the copy out at the width it will really get (minus the scrollbar, so the estimate errs
    # tall) — that is what makes WrapLabel assert each paragraph's wrapped height, which
    # totalMinimumSize then sums. widgetResizable re-sizes the body straight after.
    body.resize(max(1, dialog.minimumWidth() - scroll.verticalScrollBar().sizeHint().width()),
                body.height())
    body.layout().activate()
    need = column.totalMinimumSize().height()
    screen = dialog.screen() or QGuiApplication.primaryScreen()
    room = int(screen.availableGeometry().height() * 0.85) if screen is not None else need
    height = min(need, max(240, room))
    if scroll.minimumHeight() != height:
        scroll.setMinimumHeight(height)
        dialog.resize(dialog.width(), max(dialog.height(), dialog.sizeHint().height()))


class ShortcutsDialog(QDialog):
    """Help ▸ Keyboard shortcuts. A read-only, themed reference grouped File / Playback /
    Navigation / Analysis / Editing / Layout / Help. Inherits the global QSS (PanelHeader section
    headers, BarLabel-styled key column); content is data-driven from SHORTCUT_GROUPS so the list
    can't drift from the layout, and each accelerator's glyphs come from its own QKeySequence so it
    can't drift from the menu bar either. Self-contained — takes no app state, so it's trivially
    constructible in headless tests.

    IT SCROLLS AND IT FITS THE SCREEN, like its two siblings. This is the longest card in the app —
    seven groups, thirty-odd rows — and it was the only one of the three built straight into its
    own root layout: no scroll area, so nothing could be reached once a height was refused, and no
    screen cap, so Qt refused every height below 717 px. On the two smallest 13-inch scaled modes
    (1152x720 and 1024x640, i.e. 695 and 615 px of available height) that put the Close button and
    the HELP group off the bottom of the display, with no scrollbar and no way to shrink the card
    (QA W14-03; #187 grew the band by 18 px, it did not open it). The groups now live in the same
    `_copy_column` the copy cards use — at inset 0, because this card's strips are flush and its
    rows own their spacing — under the same `_fit_to_copy` cap, so the card opens at the height its
    rows need or at 85% of the display, whichever is smaller, and scrolls for the rest."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — keyboard shortcuts")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # inset 0 / spacing 0: the group strips are flush to the card's edges and each group body
        # carries its own control margins, so the column adds nothing of its own — the scroll and
        # the screen cap are all this card wants from _copy_column.
        scroll, column = _copy_column(spacing=0, inset=0)
        root.addWidget(scroll)

        for title, rows in SHORTCUT_GROUPS:
            # Flush PanelHeader strip per group — same surface bg + hairline as every panel header.
            header = QLabel(title.upper())
            header.setProperty("role", "PanelHeader")
            column.addWidget(header)
            column.addWidget(self._group_body(rows))

        # Standard close button row (Esc / Enter both dismiss via the button box's default). It
        # stays OUTSIDE the scroll area, like the copy cards' — the way out of a card must not be
        # the thing you have to scroll to find.
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_M)
        box_layout.addWidget(buttons)
        root.addWidget(box)
        self._scroll, self._column = scroll, column

    def showEvent(self, event):
        super().showEvent(event)
        _fit_to_copy(self, self._scroll, self._column)

    def _group_body(self, rows: list[tuple[Keys, str]]) -> QWidget:
        """A two-column grid (key | description) for one group. The key column is mono + dimmed
        (BarLabel role) and right-aligned so the glyphs line up into a tidy gutter; the
        description is the primary text colour and wraps (a WrapLabel, so a wrapped row is TALLER
        rather than sliced in half).

        THIS CARD IS A REFERENCE TABLE, NOT PROSE, so it takes control spacing rather than the copy
        cards' SPACE_XL reading inset: a key cap beside its description is a ROW, and the group
        headers above them are the app's own PanelHeader strips. The row gap was 6 and is SPACE_S —
        a description here can wrap to two lines, so the gap BETWEEN rows has to beat the gap
        WITHIN one or two rows read as one."""
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_M)
        grid.setHorizontalSpacing(theme.SPACE_L)
        grid.setVerticalSpacing(theme.SPACE_S)
        grid.setColumnStretch(1, 1)
        for r, (key, desc) in enumerate(rows):
            grid.addWidget(_key_widget(key), r, 0)
            grid.addWidget(WrapLabel(desc), r, 1)
        return body


class AboutDialog(QDialog):
    """Help ▸ About pacer studio. A small themed card: app name (hero), one-line tagline, and a
    short blurb on what it does (analyses GoPro race telemetry). Self-contained / app-state-free."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # SPACE_S: the four lines below (wordmark, version, tagline, blurb) are ONE identity block,
        # not four paragraphs — the gap inside a bar, not the gap between paragraphs.
        scroll, column = _copy_column(spacing=theme.SPACE_S)
        root.addWidget(scroll)

        name = QLabel(APP_NAME)
        name.setProperty("role", "Title")
        column.addWidget(name)

        version = QLabel(f"v{__version__}")
        version.setProperty("role", "Note")
        column.addWidget(version)

        tagline = QLabel(APP_TAGLINE)
        tagline.setProperty("role", "Tagline")
        column.addWidget(tagline)

        blurb = WrapLabel(APP_BLURB)
        blurb.setProperty("role", "Note")
        column.addWidget(blurb)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        # The card's own inset, so Close lines up with the left edge of the copy above it; no top
        # margin because the copy column's SPACE_XL bottom already provides the gap.
        box_layout.setContentsMargins(theme.SPACE_XL, 0, theme.SPACE_XL, theme.SPACE_XL)
        box_layout.addWidget(buttons)
        root.addWidget(box)
        self._scroll, self._column = scroll, column

    def showEvent(self, event):
        super().showEvent(event)
        _fit_to_copy(self, self._scroll, self._column)


class PrivacyDialog(QDialog):
    """Help ▸ Your data & privacy. A read-only, themed card disclosing what pacer stores locally
    (the per-video .pacer.json sidecar + all three app-support files — library.json, prefs.json,
    tracks.json) and how to remove it. All copy is single-sourced from PRIVACY_PARAGRAPHS.
    Self-contained / app-state-free (headless-safe)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — {PRIVACY_TITLE}")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # SPACE_M: these ARE paragraphs — a framing line, a list of four bullets and a removal
        # route — so they take the panel gutter rather than the About card's tighter block gap.
        scroll, column = _copy_column(spacing=theme.SPACE_M)
        root.addWidget(scroll)

        heading = QLabel(PRIVACY_TITLE)
        # The same [role="Title"] the About card's name and the welcome wordmark wear. It was
        # 18px/700 inline, i.e. a FIFTH type step that no scale declared, one pixel of hierarchy
        # away from a heading nobody else in the app has; HERO is the step this rank has.
        heading.setProperty("role", "Title")
        column.addWidget(heading)

        for para in PRIVACY_PARAGRAPHS:
            label = WrapLabel(para)
            label.setProperty("role", "Note")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            column.addWidget(label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        # The card's own inset, so Close lines up with the left edge of the copy above it; no top
        # margin because the copy column's SPACE_XL bottom already provides the gap.
        box_layout.setContentsMargins(theme.SPACE_XL, 0, theme.SPACE_XL, theme.SPACE_XL)
        box_layout.addWidget(buttons)
        root.addWidget(box)
        self._scroll, self._column = scroll, column

    def showEvent(self, event):
        super().showEvent(event)
        _fit_to_copy(self, self._scroll, self._column)
