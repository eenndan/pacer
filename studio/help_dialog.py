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
or the ⛶ button to maximize that panel) and VideoView (double-click the video / the ⤢ transport
button to fill the screen) — so they're documented here as the only place a user can learn them.
If you change a binding in app.py, change it HERE too — tests/test_help_dialog.py fails the build
when a live binding has no row.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__
from .theme import C

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
Keys = str | QKeySequence | QKeySequence.StandardKey
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
        (QKeySequence("Ctrl+Shift+S"), "Session statistics, full-window (again / ⛶ to restore)"),
    ]),
    ("Editing", [
        # "timing-line", matching the menu item it documents: ⌘Z takes back SECTOR-line edits too,
        # and this card used to be one of the two surfaces (with the status bar) that said otherwise.
        (QKeySequence.StandardKey.Undo, "Undo the last timing-line edit"),
    ]),
    ("Layout", [
        ("Double-click header  ·  ⛶", "Maximize a panel to fill the window (Esc / again to restore)"),
        (QKeySequence.StandardKey.FullScreen, "Enter / exit full screen"),
        ("Double-click video  ·  ⤢", "Make the video fill the screen (Esc / again to restore)"),
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
    """The glyphs for one row's key column. A str is literal (a plain letter, or a drag gesture
    with no binding); a QKeySequence / StandardKey is rendered with Qt's OWN native text, which is
    what the menu bar paints — so the card cannot disagree with the menus it documents, and a
    non-macOS run reads "Ctrl+Shift+S" instead of Mac glyphs."""
    if isinstance(key, str):
        return key
    return QKeySequence(key).toString(QKeySequence.NativeText)


class _WrapLabel(QLabel):
    """A word-wrapping QLabel that claims the height its wrapped text actually needs.

    QLabel implements heightForWidth but ships a size policy that does not advertise it, so a
    layout hands a wrapping label the SINGLE-LINE sizeHint height and every further line paints
    outside the row — sliced through the letterforms and over whatever sits below. Re-asserting
    the height on each resize makes the fix width-independent: a longer string, a bigger system
    font or a translation grows the row instead of clipping it. (Widening the card only changes
    WHICH strings wrap; at 560 px the longest description clears its box by 5 px, which is not a
    margin to rely on.) Converges — heightForWidth depends only on the width, which the extra
    height does not change."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        need = self.heightForWidth(self.width())
        if need > 0 and need != self.minimumHeight():
            self.setMinimumHeight(need)


def _copy_column(spacing: int) -> tuple[QScrollArea, QVBoxLayout]:
    """The copy column of a read-only Help card: a frameless, vertically-scrolling QScrollArea
    around a plain widget column. Returns (scroll area, the layout to add paragraphs to).

    Both cards used to put their paragraphs straight into the dialog, which set only a minimum
    WIDTH — so every height Qt permitted below the copy's height guillotined the last paragraphs
    with no scrollbar and no cue. The scroll area guarantees the text stays reachable; _fit_to_copy
    below is what keeps it off screen in the first place."""
    body = QWidget()
    column = QVBoxLayout(body)
    column.setContentsMargins(20, 18, 20, 16)
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
    940 px on the privacy card). ``totalMinimumSize`` then reads the heights _WrapLabel has
    already asserted. Idempotent, so re-showing the card is a no-op. Capped at the screen: a card
    whose copy outgrows a small display scrolls rather than opening taller than the desk."""
    body = scroll.widget()
    # Lay the copy out at the width it will really get (minus the scrollbar, so the estimate errs
    # tall) — that is what makes _WrapLabel assert each paragraph's wrapped height, which
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
    constructible in headless tests."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — keyboard shortcuts")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        for title, rows in SHORTCUT_GROUPS:
            # Flush PanelHeader strip per group — same surface bg + hairline as every panel header.
            header = QLabel(title.upper())
            header.setProperty("role", "PanelHeader")
            root.addWidget(header)
            root.addWidget(self._group_body(rows))

        # Standard close button row (Esc / Enter both dismiss via the button box's default).
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 10, 12, 12)
        box_layout.addWidget(buttons)
        root.addWidget(box)

    def _group_body(self, rows: list[tuple[Keys, str]]) -> QWidget:
        """A two-column grid (key | description) for one group. The key column is mono + dimmed
        (BarLabel role) and right-aligned so the glyphs line up into a tidy gutter; the
        description is the primary text colour and wraps (a _WrapLabel, so a wrapped row is TALLER
        rather than sliced in half)."""
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(12, 8, 12, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for r, (key, desc) in enumerate(rows):
            key_label = QLabel(_key_text(key))
            key_label.setProperty("role", "BarLabel")
            key_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
            # The reference's key glyphs read better in the mono face (they line up); the QSS
            # BarLabel role gives the dimmed small-header colour/size, we only add the family.
            key_label.setStyleSheet('font-family: "SF Mono","JetBrains Mono","Menlo","monospace";')
            grid.addWidget(key_label, r, 0)
            grid.addWidget(_WrapLabel(desc), r, 1)
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
        scroll, column = _copy_column(spacing=8)
        root.addWidget(scroll)

        name = QLabel(APP_NAME)
        name.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {C.text};")
        column.addWidget(name)

        version = QLabel(f"v{__version__}")
        version.setStyleSheet(f"color: {C.text_dim};")
        column.addWidget(version)

        tagline = QLabel(APP_TAGLINE)
        tagline.setStyleSheet(f"color: {C.accent}; font-weight: 600;")
        column.addWidget(tagline)

        blurb = _WrapLabel(APP_BLURB)
        blurb.setStyleSheet(f"color: {C.text_dim};")
        column.addWidget(blurb)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(20, 0, 20, 16)
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
        scroll, column = _copy_column(spacing=10)
        root.addWidget(scroll)

        heading = QLabel(PRIVACY_TITLE)
        heading.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {C.text};")
        column.addWidget(heading)

        for para in PRIVACY_PARAGRAPHS:
            label = _WrapLabel(para)
            label.setStyleSheet(f"color: {C.text_dim};")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            column.addWidget(label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(20, 0, 20, 16)
        box_layout.addWidget(buttons)
        root.addWidget(box)
        self._scroll, self._column = scroll, column

    def showEvent(self, event):
        super().showEvent(event)
        _fit_to_copy(self, self._scroll, self._column)
