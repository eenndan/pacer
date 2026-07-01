"""Help-menu dialogs: the keyboard-shortcut reference + the About card.

The whole interaction model (Space/M/G/C toggles, ←/→ stepping, chart-cursor scrub) is otherwise
undiscoverable — there's no on-screen hint for any of it. These two themed QDialogs are the
discoverable surface, opened from the Help menu (and F1). (The draggable start/finish line is the
exception: an unverified-timing recording now surfaces a persistent banner + an on-canvas "drag to
set start/finish" cue, so that one IS discoverable on the recordings where it matters.)

Single source of truth: SHORTCUT_GROUPS below is the ONE place the shortcut text lives. The keys
listed here MUST stay in lockstep with the actual bindings, which are defined in
``StudioWindow._build_shortcuts`` (Space / M / G / C) and ``StudioWindow.keyPressEvent`` (the
←/→ ± stepping). The drag interactions have no key binding — they're handled in MapView (the
draggable start/finish line) and ScrubController (the chart cursor) — so they're documented here
as the only place a user can learn them. If you change a binding in app.py, change it HERE too.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .theme import C

# ---------------------------------------------------------------- shortcut catalogue
# (key, what it does). Grouped by the same mental model the app uses: Playback (the transport),
# Navigation (moving through time + space), Analysis (the comparison/overlay tools). Each entry's
# key column is rendered in the mono face so the glyphs line up. See the module docstring for the
# cross-reference to the live bindings in app.py — keep them in sync.
SHORTCUT_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
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
    ]),
    ("Help", [
        ("F1  ·  ?", "Show this shortcut reference"),
    ]),
]

# Your-data & privacy disclosure (Help ▸ Your data & privacy). Honest + calm: everything is local
# and offline. Names the two on-disk stores + how to remove them. Kept here (with the shortcuts /
# about copy) as the single source of the app's Help-menu text.
PRIVACY_TITLE = "Your data & privacy"
PRIVACY_PARAGRAPHS = [
    "pacer studio runs entirely on your Mac. It does not upload, sync or share anything — no "
    "account, no network, no telemetry. Everything below stays on this computer, offline.",
    "What it stores, and where:",
    "•  Timing-line sidecar — when you place or drag a start/finish or sector line, pacer saves "
    "those lines next to your video as a small \"<name>.pacer.json\" file, so your lap timing "
    "survives a restart. It contains only the line coordinates and the track name — no video.",
    "•  Session library — each analyzed recording is indexed in "
    "\"~/Library/Application Support/pacer/library.json\": the file path(s), track name, GPS date "
    "and lap times. This is what powers the Library list and per-track PB progression.",
    "How to remove it:  open File ▸ Library…, right-click a recording and choose "
    "\"Forget this recording\" to drop it from the index and delete its sidecar, or click "
    "\"Clear library\" to wipe the whole index. Your video files are never touched.",
]

APP_NAME = "pacer studio"
APP_TAGLINE = "Race-telemetry analysis for GoPro footage."
APP_BLURB = (
    "Open a GoPro recording and pacer studio reconstructs the laps from its embedded GPS — then "
    "lets you scrub the footage against the map, speed / Δ charts and a g-meter overlay, compare "
    "your laps (and other recordings) side by side, and find where the time goes."
)


class ShortcutsDialog(QDialog):
    """Help ▸ Keyboard shortcuts. A read-only, themed reference grouped Playback / Navigation /
    Analysis / Help. Inherits the global QSS (PanelHeader section headers, BarLabel-styled key
    column); content is data-driven from SHORTCUT_GROUPS so the list can't drift from the layout.
    Self-contained — takes no app state, so it's trivially constructible in headless tests."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — keyboard shortcuts")
        self.setMinimumWidth(440)

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

    def _group_body(self, rows: list[tuple[str, str]]) -> QWidget:
        """A two-column grid (key | description) for one group. The key column is mono + dimmed
        (BarLabel role) and right-aligned so the glyphs line up into a tidy gutter; the
        description is the primary text colour and wraps."""
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(12, 8, 12, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for r, (key, desc) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setProperty("role", "BarLabel")
            key_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
            # The reference's key glyphs read better in the mono face (they line up); the QSS
            # BarLabel role gives the dimmed small-header colour/size, we only add the family.
            key_label.setStyleSheet('font-family: "SF Mono","JetBrains Mono","Menlo","monospace";')
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            grid.addWidget(key_label, r, 0)
            grid.addWidget(desc_label, r, 1)
        return body


class AboutDialog(QDialog):
    """Help ▸ About pacer studio. A small themed card: app name (hero), one-line tagline, and a
    short blurb on what it does (analyses GoPro race telemetry). Self-contained / app-state-free."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(8)

        name = QLabel(APP_NAME)
        name.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {C.text};")
        root.addWidget(name)

        tagline = QLabel(APP_TAGLINE)
        tagline.setStyleSheet(f"color: {C.accent}; font-weight: 600;")
        root.addWidget(tagline)

        blurb = QLabel(APP_BLURB)
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {C.text_dim};")
        root.addWidget(blurb)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addSpacing(6)
        root.addWidget(buttons)


class PrivacyDialog(QDialog):
    """Help ▸ Your data & privacy. A read-only, themed card disclosing what pacer stores locally
    (the per-video .pacer.json sidecar + the app-support library index) and how to remove it. All
    copy is single-sourced from PRIVACY_PARAGRAPHS. Self-contained / app-state-free (headless-safe)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — {PRIVACY_TITLE}")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        heading = QLabel(PRIVACY_TITLE)
        heading.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {C.text};")
        root.addWidget(heading)

        for para in PRIVACY_PARAGRAPHS:
            label = QLabel(para)
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {C.text_dim};")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            root.addWidget(label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addSpacing(6)
        root.addWidget(buttons)
