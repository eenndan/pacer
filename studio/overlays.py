"""Presentational overlay widgets shown over StudioWindow: the first-run empty state (WelcomeView)
and the personal-best celebration/share toast (PBToast). Self-contained — they take DI callbacks +
formatted text and route Qt signals; no reach into StudioWindow internals."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .theme import C


class WelcomeView(QWidget):
    """First-run / no-recording empty state — the product's tagline made literal: drop a GoPro
    recording onto the window, or open one. The centred content sits inside a dashed-border DROP
    ZONE (`drop_zone`, objectName "WelcomeDropZone") so the drag-and-drop affordance is VISIBLE — a
    user reads "you can drop a file here" instead of just being told. `on_open` runs the file
    picker, `on_demo` resolves and loads a real demo lapping recording (and re-shows this state with
    an honest message if the demo can't be fetched). An optional `error` line is shown when this
    stands in for a failed first load. The buttons are exposed (`open_btn`/`demo_btn`) for tests."""

    def __init__(self, on_open, on_demo, error: str | None = None, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignCenter)

        # The dashed-border drop zone framing the centred content — the VISIBLE target for the
        # window's drag-and-drop (StudioWindow dragEnter/dropEvent). Restrained, on-theme (a
        # rounded rect with a dashed hairline over the canvas), not a heavy hero box.
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("WelcomeDropZone")
        self.drop_zone.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        zone = QVBoxLayout(self.drop_zone)
        zone.setAlignment(Qt.AlignCenter)
        zone.setContentsMargins(theme.SPACE_3XL, theme.SPACE_2XL,
                                theme.SPACE_3XL, theme.SPACE_2XL)
        zone.setSpacing(theme.SPACE_L)

        # A small muted drop glyph above the wordmark, reinforcing "drop a file here" without hue.
        self.drop_icon = QLabel()
        self.drop_icon.setPixmap(theme.icon("ph.download-simple", color=C.text_muted).pixmap(36, 36))
        self.drop_icon.setAlignment(Qt.AlignCenter)
        zone.addWidget(self.drop_icon)

        # Intentional short brand lockup on the welcome screen — NOT the full APP_NAME wordmark.
        title = QLabel("Pacer")
        title.setProperty("role", "Title")   # shared with the About / privacy cards
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel("Drop a GoPro recording here — or open one — to get your laps.")
        subtitle.setProperty("role", "WelcomeSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        zone.addWidget(title)
        zone.addWidget(subtitle)

        buttons = QHBoxLayout()
        buttons.setAlignment(Qt.AlignCenter)
        self.open_btn = QPushButton("Open recording…")
        self.open_btn.setProperty("variant", "primary")
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(on_open)
        self.demo_btn = QPushButton("Open demo")
        self.demo_btn.clicked.connect(on_demo)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.demo_btn)
        zone.addLayout(buttons)

        if error:
            # The ⚠ the rest of the app marks low-confidence/attention with, so the failure reads as
            # a failure WITHOUT hue (it pairs with the amber [role=WelcomeError] styling, which used
            # to be the subtitle's exact grey — the message hid inside the invitation copy).
            err = QLabel(f"⚠  {error}")
            err.setProperty("role", "WelcomeError")
            err.setAlignment(Qt.AlignCenter)
            err.setWordWrap(True)
            zone.addWidget(err)

        root.addWidget(self.drop_zone, 0, Qt.AlignCenter)


class PBToast(QWidget):
    """A transient "new personal best!" celebration card overlaid on the window (top-centre) when a
    freshly-analysed session beats its track's prior PB on verified timing. Tasteful, not modal: an
    amber-accented card that auto-dismisses after a few seconds, holding that clock while the
    pointer is on it so it never vanishes mid-click. At the peak-pride moment it turns
    into a SHARE loop: the PRIMARY "Share your PB →" button saves the shareable lap card (image),
    and a secondary "See your progress →" link opens the per-track PB-progression chart (retention),
    plus a × to dismiss now.

    Purely presentational — the caller decides WHEN to show it (library.pb_moment) and passes the
    formatted `title`/`body` + the `on_progress` / `on_share` callbacks (either may be None to hide
    that action). Exposed attributes (title_label / body_label / link_btn / share_btn / close_btn)
    let the suite assert the wording + that each button routes to its injected callback."""

    AUTO_DISMISS_MS = 6000  # generous but transient — long enough to read, short enough to not nag
    # Pointer-target floor for the card's flat controls. They are sized by their QSS text padding
    # alone, which left the ✕ at 20x19 and the progression link 19px tall — under the 24px minimum,
    # on the one card in the app you must hit before it deletes itself. Applied ONLY to those two:
    # the primary share button already stands 30px on its variant's padding, and an explicit
    # minimum would REPLACE its layout minimum (qSmartMinSize takes an explicit minimumSize
    # verbatim), letting the row squeeze it down to 24 instead of leaving it alone.
    #
    # The number itself is theme.HIT_MIN — the app-wide pointer-target floor, which lives with the
    # rest of the dimensional tokens now instead of being owned by whichever widget needed it
    # first. The NAME stays, because it is the widget's own statement of the rule and
    # tests/test_pb_toast.py reads it.
    MIN_HIT_PX = theme.HIT_MIN

    def __init__(self, title: str, body: str, on_progress, on_share=None, parent=None):
        super().__init__(parent)
        self.setObjectName("PBToast")
        self._on_progress = on_progress
        self._on_share = on_share
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_S)
        lay.setSpacing(theme.SPACE_XXS)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PBToastTitle")
        top.addWidget(self.title_label)
        top.addStretch(1)
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("PBToastClose")
        self.close_btn.setMinimumSize(self.MIN_HIT_PX, self.MIN_HIT_PX)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.clicked.connect(self.dismiss)
        top.addWidget(self.close_btn)
        lay.addLayout(top)

        self.body_label = QLabel(body)
        self.body_label.setObjectName("PBToastBody")
        self.body_label.setWordWrap(True)
        lay.addWidget(self.body_label)

        # The action row: the PRIMARY "Share your PB →" (one tap to the lap card) then the
        # secondary progression link. Each is created only when its callback is injected.
        self.share_btn = None
        self.link_btn = None
        link_row = QHBoxLayout()
        link_row.setContentsMargins(0, 0, 0, 0)
        link_row.addStretch(1)
        if on_share is not None:
            self.share_btn = QPushButton("Share your PB →")
            self.share_btn.setObjectName("PBToastShare")
            self.share_btn.setProperty("variant", "primary")
            self.share_btn.setCursor(Qt.PointingHandCursor)
            self.share_btn.setToolTip("Save a shareable lap card (image) of this personal best")
            self.share_btn.clicked.connect(self._on_share_clicked)
            link_row.addWidget(self.share_btn)
        self.link_btn = QPushButton("See your progress →")
        self.link_btn.setObjectName("PBToastLink")
        self.link_btn.setMinimumHeight(self.MIN_HIT_PX)
        self.link_btn.setCursor(Qt.PointingHandCursor)
        self.link_btn.setToolTip("Open this track's personal-best progression chart")
        self.link_btn.clicked.connect(self._on_link)
        link_row.addWidget(self.link_btn)
        lay.addLayout(link_row)

        # Auto-dismiss after a beat (window-owned QTimer so it's cleaned up with the toast).
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def show_for(self, parent: QWidget):
        """Position the toast top-centre over `parent`, show it on top, and start the auto-dismiss."""
        self.adjustSize()
        pw = parent.width()
        x = max(0, (pw - self.width()) // 2)
        self.move(x, theme.SPACE_L)
        self.raise_()
        self.show()
        self._timer.start(self.AUTO_DISMISS_MS)

    def enterEvent(self, ev):
        """Hold the auto-dismiss while the pointer is on the card: someone who has moved onto it is
        reading it or aiming at one of its actions, and a 6 s clock that fires mid-aim is exactly
        where a small target hurts. (Qt sends no Leave when the pointer crosses onto a CHILD — the
        enter/leave pair is dispatched up to the common ancestor — so the hold covers the buttons.)"""
        self._timer.stop()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        """Start the FULL clock again on leave, not the remainder — the card stays transient, the
        countdown just begins from when it stopped being read."""
        if self.isVisible():
            self._timer.start(self.AUTO_DISMISS_MS)
        super().leaveEvent(ev)

    def _on_link(self):
        """Route to the PB-progression surface, then dismiss (the chart is the destination now)."""
        self.dismiss()
        if self._on_progress is not None:
            self._on_progress()

    def _on_share_clicked(self):
        """One-tap share: route to the injected share callback (save the lap card), then dismiss."""
        self.dismiss()
        if self._on_share is not None:
            self._on_share()

    def dismiss(self):
        self._timer.stop()
        self.hide()
        self.deleteLater()
