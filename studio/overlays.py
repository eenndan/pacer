"""Presentational overlay widgets shown over StudioWindow: the first-run empty state (WelcomeView)
and the personal-best celebration/share toast (PBToast). Self-contained — they take DI callbacks +
formatted text and route Qt signals; no reach into StudioWindow internals.

The one thing an overlay cannot decide for itself is WHERE it belongs, because that depends on
what the window is currently showing. So there is a small optional protocol instead of a reach:
a parent may expose ``view.overlay_anchor()`` returning the widget a transient card should sit
inside (see ``PBToast.anchor_region`` and ``CentralView.overlay_anchor``). Absent, the card falls
back to the window itself — which is what keeps these widgets constructible over a bare QWidget in
the tests, exactly as before."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
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
    """A transient "new personal best!" celebration card overlaid on the window — at the bottom of
    the LAP panel's body, see `show_for` / `anchor_region` — when a freshly-analysed session beats
    its track's prior PB on verified timing. Tasteful, not modal: an
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
    # How long after show_for() the card re-asks where it belongs. Deliberately the SAME 120 ms
    # CentralView.showEvent uses to restore the grid splitters, because that restore is the last
    # thing that moves the panel this card anchors to — see show_for.
    SETTLE_MS = 120
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
        # THE CARD HAS TO BE TOLD TO PAINT ITSELF. theme.py has drawn this toast a background, an
        # accent border and a RADIUS_M corner since the moment shipped — and a bare QWidget honours
        # none of it: Qt only runs the stylesheet's box painting for a QWidget subclass when
        # WA_StyledBackground is set (QPushButton, QLabel and friends draw their own, which is why
        # every other #Name rule in this app just works). So the "card" was transparent, and it read
        # as one only because it happened to be sitting over the map's empty top-left corner, which
        # is flat surface colour. Moved onto the lap grid it became text over alternating stripes
        # and lap times, which is what made this visible at all.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._on_progress = on_progress
        self._on_share = on_share
        # The widget show_for() was given, i.e. the one this card is placed inside and follows.
        # None until then, and None again after dismiss(), which is what makes _place a no-op for
        # a card that is on its way out.
        self._host: QWidget | None = None
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

    def anchor_region(self, parent: QWidget) -> QRect:
        """The rectangle of `parent` this card may sit in, in `parent`'s own coordinates.

        WHY THIS EXISTS. The card used to be placed top-centre of the WINDOW, at a fixed 16 px from
        its top edge — a position chosen when the window was a picture rather than a set of panels.
        Measured on the shipped app at 1440x900 it landed at (579, 16, 281x96), which put it 36 px
        deep into the MAP panel's PanelHeader (colliding with the word "MAP"), across the whole 32 px
        of the map's PanelToolbar, and 20 px into the track canvas below that; with the lap panel
        maximized it sat on THAT panel's header instead. Now that every panel declares a header
        height, an overlay landing on one is not a near-miss, it is the one rule the chrome has.

        So the position is asked for rather than assumed: a parent that knows what it is showing
        exposes ``view.overlay_anchor()`` -> QWidget (CentralView does; see its docstring for which
        panel body it hands back and why). Anything else — a bare QWidget host in a test, the
        window before its view exists — gets the parent's own rect, which is the previous behaviour
        minus the fixed top offset.

        Deliberately duck-typed, and deliberately read off `parent.view` rather than imported: this
        module is Qt-only and knows nothing about sessions, panels or telemetry, and importing the
        view that owns the panels would end that.

        THE ANSWER IS THEN CHECKED, not trusted, against the only thing this class does know: its
        own size. A panel can be laid out, correctly sized and still not be on screen — Qt collapses
        a splitter SECTION rather than the widget in it — so the region is clipped to what `parent`
        can actually show, and a clipped region too small to hold this card hands the whole window
        back instead. That keeps the failure mode "a toast somewhere slightly worse" rather than "a
        toast at (0, 0)"."""
        view = getattr(parent, "view", None)
        anchor = getattr(view, "overlay_anchor", None)
        widget = anchor() if callable(anchor) else None
        if widget is None or widget.isHidden() or not parent.isAncestorOf(widget):
            return parent.rect()
        shown = QRect(widget.mapTo(parent, QPoint(0, 0)),
                      widget.size()).intersected(parent.rect())
        if (shown.width() < self.width()
                or shown.height() < self.height() + 2 * theme.SPACE_M):
            return parent.rect()
        return shown

    def show_for(self, parent: QWidget):
        """Show the toast over `parent` and keep it on its anchor for as long as it lives.

        PLACING IT ONCE IS NOT ENOUGH, and that is what this method learned the hard way. The
        caller is `StudioWindow._load`, which runs `_build_ui()` — constructing a NEW CentralView
        and `setCentralWidget`-ing it — and then celebrates in the SAME synchronous block, before
        Qt has shown that widget or laid it out. So at this instant `view.overlay_anchor()` hands
        back a panel body that is still `isHidden()`, `anchor_region` takes its documented fallback
        to the whole window, and the card lands bottom-CENTRE over the Δ chart: measured at
        (571, 792), 449 px from where it belongs, on the first load AND on a second load into an
        already-visible window. The fallback that `anchor_region` calls exceptional was the only
        branch a production toast ever took. (The card was also 298 px wide there instead of 270,
        because its wrapped body label had not been measured yet either.)

        So the position is (re-)DECIDED, not remembered: `_place` is idempotent — it re-measures
        the card and moves it only if the answer changed — and it is called now, again on the next
        turn of the event loop, again after the deferred layout passes, and on any resize of the
        parent while the card is up. The two deferred calls are deliberately the same shape and the
        same 120 ms as `CentralView.showEvent`'s splitter restore, for the same reason and against
        the same event: that restore MOVES the lap panel, so a card placed before it would be
        stale. Placing immediately as well means a host that never spins an event loop (a test, a
        window torn down inside the same block) still gets the old behaviour rather than a card at
        (0, 0)."""
        self._host = parent
        parent.installEventFilter(self)
        self._place()
        self.raise_()
        self.show()
        self._timer.start(self.AUTO_DISMISS_MS)
        # Owned by this widget (never QTimer.singleShot's static form), so a card dismissed inside
        # the settle window takes its pending re-places to the grave with it.
        for delay in (0, self.SETTLE_MS):
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._place)
            t.start(delay)

    def _place(self):
        """Put the card at the BOTTOM of its anchor region, centred. Safe to call at any time.

        BOTTOM, not top, and that is the whole placement decision. The anchor region is a panel's
        BODY (`anchor_region`), so its top edge is immediately under that panel's header — and the
        lap grid puts its own column headers there, so a card inset from the top would land on a
        header again, one level down. Its bottom edge has nothing structural on it: the rows a card
        covers there are the ones furthest from the ★ best lap this card is about, and they scroll.
        It also puts the card in the window's bottom-left, which is where a transient notification
        conventionally lives, instead of centred on the splitter between two columns.

        Clamped rather than trusted: a region shorter or narrower than the card (a panel dragged
        small) pins the card to the region's top-left and lets it overhang, because a celebration
        that is off-screen is worse than one that overlaps. Nothing here can push it outside
        `parent`."""
        parent = self._host
        if parent is None:
            return
        self.adjustSize()
        region = self.anchor_region(parent)
        x = region.left() + max(0, (region.width() - self.width()) // 2)
        y = max(region.top() + theme.SPACE_M,
                region.bottom() + 1 - theme.SPACE_M - self.height())
        self.move(max(0, min(x, max(0, parent.width() - self.width()))),
                  max(0, min(y, max(0, parent.height() - self.height()))))

    def eventFilter(self, obj, event):
        """Follow the anchor when the window resizes under the card (its 6 s outlives a drag)."""
        if obj is self._host and event.type() == QEvent.Resize and self.isVisible():
            self._place()
        return super().eventFilter(obj, event)

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
        self._host = None   # any re-place still in flight becomes a no-op
        self.hide()
        self.deleteLater()
