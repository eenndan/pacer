"""Presentational overlay widgets shown over StudioWindow: the no-recording state (WelcomeView —
what the app does, the demo and the recent sessions) and the personal-best celebration/share
toast (PBToast). Self-contained — they take DI callbacks + formatted text and route Qt signals; no
reach into StudioWindow internals.

The one thing an overlay cannot decide for itself is WHERE it belongs, because that depends on
what the window is currently showing. So there is a small optional protocol instead of a reach:
a parent may expose ``view.overlay_anchor()`` returning the widget a transient card should sit
inside (see ``PBToast.anchor_region`` and ``CentralView.overlay_anchor``). Absent, the card falls
back to the window itself — which is what keeps these widgets constructible over a bare QWidget in
the tests, exactly as before."""

from __future__ import annotations

import os
from typing import NamedTuple

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import demo, library, theme
from ._signal import fmt_time, plural
from .theme import C

# What the welcome screen's demo button says while its resolve is in flight. Owned HERE because this
# is the module that has to reserve room for it (see WelcomeView.demo_btn); read by
# StudioWindow._set_demo_busy, which sets it — the width the button is floored at and the text that
# fills it are the same fact, and a second copy of the string is a button that grows again.
#
# A WORD, NOT THE SENTENCE ("Fetching the demo clip…"): a button floored at a whole sentence is how
# the secondary action once became wider than the primary (D4-06). The loading card that comes up
# behind it, if the fetch is slow, says the whole thing (DEMO_FETCH_TITLE).
BUSY_DEMO_LABEL = "Fetching…"
# The loading card's headline for the same wait. Not the button's label: the card is the surface
# with room for the full sentence, and it is the one that is up long enough to be read.
DEMO_FETCH_TITLE = "Fetching the demo clip…"
# THE WELCOME'S WORDS (cool/welcome-screen, owner-approved design 2A). What the app does, in one
# line, the README's own claim cut to the three things a first screen can promise; the two doors;
# where a file can be dropped; and the demo, honestly labelled.
DESCRIPTION = "Lap times, corners and coaching from your GoPro's GPS."
OPEN_LABEL = "Open a recording…"
# The demo's two resting labels. `studio.demo.demo_available()` — the offline question, env var or
# cache — picks which one it says: the short one when the clip is on this machine, the download one
# when the click will fetch it, with the size stated, because a click that reaches the network
# should say so before it does. The size is the pinned asset's (`demo.download_mb()`), never typed.
DEMO_LABEL = "Open the demo (synthetic session)"
DEMO_GET_LABEL = f"{DEMO_LABEL} · {demo.download_mb()} MB download"
DROP_HINT = "Or drop a GoPro .MP4 anywhere on this window."
# True of the asset: `studio/dev/make_demo.py` simulates a kart on a fictional circuit (FIRST_LAP.md).
DEMO_NOTE = "The demo is generated, not filmed: a simulated kart on a made-up circuit."
RECENT_TITLE = "RECENT SESSIONS"
LIBRARY_LABEL = "Library…"
# The drop glyph's pixmap side. Named because the loading card reserves a leading row of exactly
# this height for its busy bar.
DROP_GLYPH_PX = 36

# The PB toast's dismiss mark, in the app's ONE glyph vocabulary (PR #189: a character in a
# user-visible string must be drawn by the app's own UI face, or it must be a theme.icon() pixmap).
# `✕` U+2715 is not in Inter, so Qt fetched it per-character from **Menlo** and drew **6x6 px** of
# ink in a 24x24 button — measured from the window composite, 1.0 px below the button's centre.
# `ph.x` at ICON_PX is **10x10, cy 0.0**: 2.8x the ink, centred, and drawn by a face something
# chose. Named here so tests/test_glyph_vocabulary.py can bind to the button's own declaration.
DISMISS_ICON = "ph.x"


class ColumnMetrics(NamedTuple):
    """The welcome column's own measurements, read off real widgets under the live stylesheet."""

    glyph_h: int    # the leading glyph row — the brand mark's pixmap side
    error_h: int    # the reserved error slot ABOVE the card, on the canvas
    action_w: int   # what every action BUTTON in the column is floored at: the widest it can say


def column_metrics() -> ColumnMetrics:
    """Measure the welcome column from throwaway widgets polished against the live stylesheet (the
    app stylesheet reaches every widget, shown or not, so `sizeHint()` is the size the real control
    takes).

    ACTION_W IS ONE FLOOR FOR BOTH BUTTONS, and the widest of everything either says — the two
    doors' labels and the busy label. On a first visit the demo is the primary and "Open a
    recording…" the secondary; stacked at one width, neither can be the smaller twin (§6.7(b)),
    and a click that swaps a label cannot move the column (D4-06)."""
    err = QLabel("")
    err.setProperty("role", "WelcomeError")
    err.ensurePolished()
    btn = QPushButton(OPEN_LABEL)
    btn.setProperty("variant", "primary")
    btn.ensurePolished()
    widest = max(busy_button_width(btn, text)
                 for text in (OPEN_LABEL, DEMO_LABEL, DEMO_GET_LABEL, BUSY_DEMO_LABEL))
    return ColumnMetrics(glyph_h=DROP_GLYPH_PX,
                         error_h=WelcomeView.ERROR_LINES * err.fontMetrics().height(),
                         action_w=widest)


def busy_button_width(btn: QPushButton, busy_text: str) -> int:
    """The width `btn` would ask for if its label were `busy_text` — its own sizeHint with the two
    strings' advances swapped, so the style's padding is whatever the style says rather than a
    number copied out of a stylesheet."""
    fm = btn.fontMetrics()
    return (btn.sizeHint().width()
            + fm.horizontalAdvance(busy_text) - fm.horizontalAdvance(btn.text()))


class Anchors(NamedTuple):
    """Where the welcome card's landmarks are, in the CARD's own coordinates — what the loading card
    (its second frame) is built on, so the two cannot be measured against different answers."""

    card: QSize      # the dashed card's outer size
    glyph: QRect     # the brand-mark row
    title: QRect     # the "Pacer" headline
    primary: QRect   # the primary action
    error_h: int     # the reserved error slot above the card


class _ElidedLabel(QLabel):
    """A row's track: elides its NAME to the room it is given and keeps `keep` (the way and the
    trust tag, `library.track_text`) whole — the Library's Track cell does the same
    (cool/pb-per-direction). The full label stays on the row's tooltip and accessible name."""

    def __init__(self, text: str, keep: str = ""):
        super().__init__(text)
        self._full, self._keep = text, keep
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def resizeEvent(self, event):
        fm, room = self.fontMetrics(), self.width()
        if fm.horizontalAdvance(self._full) <= room:
            self.setText(self._full)
        else:
            name = self._full[:len(self._full) - len(self._keep)]
            left = max(0, room - fm.horizontalAdvance(self._keep))
            self.setText(fm.elidedText(name, Qt.ElideRight, left) + self._keep)
        super().resizeEvent(event)


class WelcomeView(QWidget):
    """The no-recording state: what Pacer does, the two doors (a recording, the demo), and — once
    the Library has rows — the newest sessions, each one click from reopening (cool/welcome-screen,
    owner-approved design 2A, Q2 = (a)). The column sits inside a dashed DROP ZONE (`drop_zone`,
    objectName "WelcomeDropZone") so the drag-and-drop affordance is VISIBLE, and the zone lights up
    while a droppable file is over the window (StudioWindow._set_dragover).

    TWO SHAPES, ONE COLUMN. `recent` is the rows to list (`library.recent_rows`):
      * none — a FIRST VISIT: the demo is the primary (amber) button and "Open a recording…" the
        secondary, with the drop hint and a line saying the demo is generated, not filmed;
      * some — RETURNING: "Open a recording…" is the primary, then RECENT SESSIONS (at most
        `library.RECENT_LIMIT` rows, newest recording first) with "Library…", then the drop hint
        and the demo as a small link.
    A row calls `on_recent(paths)` — the app passes the same `_load` the Library window's
    double-click calls — and a row whose footage is missing is greyed, disabled, and names the file
    in the Library's own words. `on_library` opens the Library window.

    `on_demo` IS OPTIONAL, AND None MEANS "NO DEMO ACTION" — the protocol PBToast uses for its
    actions too. The app passes it on every welcome screen, and `demo_label` says what the click
    will do; it is kept on the view because `_set_demo_busy` swaps it out and has to hand back the
    one it replaced.

    THE FAILURE STATE IS THE SAME SHAPE AS THE CLEAN ONE (QA D2-09 / D4-06): `_show_welcome`
    DESTROYS and rebuilds this view to show an error, so the error slot is ALWAYS built, empty and
    hidden with `RetainSizeWhenHidden`, ABOVE the card on the canvas, as wide as the card; the
    offending path is shown as its basename with the absolute path on the tooltip.

    Keyboard: Tab runs primary → secondary → rows → Library… → the demo link, and Return
    activates whichever has focus (`keyPressEvent`)."""

    # How many BODY lines the error slot reserves. MEASURED, not chosen: the longest of the app's
    # failure sentences (app._load_failure_message plus the demo's) takes two at the card's
    # measure, and the file it names a third. tests/test_first_run_path.py sweeps every one.
    ERROR_LINES = 3

    def __init__(self, on_open, on_demo, error: str | None = None, parent=None,
                 error_path: str | None = None, demo_label: str = DEMO_LABEL,
                 recent: list[dict] | None = None, on_recent=None, on_library=None):
        super().__init__(parent)
        m = column_metrics()
        self.recent = list(recent or [])
        returning = bool(self.recent)
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignCenter)
        # No outer margin: five rows have to fit the window's minimum size, and the loading card's
        # container (app._show_loading_placeholder) is built the same way.
        root.setContentsMargins(0, 0, 0, 0)

        self.drop_zone = QFrame()
        self.drop_zone.setObjectName("WelcomeDropZone")
        self.drop_zone.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self.drop_zone.setMinimumWidth(theme.WELCOME_CARD_W)
        zone = QVBoxLayout(self.drop_zone)
        zone.setContentsMargins(theme.SPACE_3XL, theme.SPACE_2XL,
                                theme.SPACE_3XL, theme.SPACE_2XL)
        # The returning card is the taller one, so it sets its groups one step closer.
        zone.setSpacing(theme.SPACE_M if returning else theme.SPACE_L)

        # THE BRAND MARK, above the wordmark it belongs to — the app's own speed chevron, the same
        # geometry studio/assets/pacer.icns is drawn from (theme.brand_mark), drawn tile-free.
        self.drop_icon = QLabel()
        self.drop_icon.setPixmap(theme.brand_mark(DROP_GLYPH_PX))
        self.drop_icon.setAlignment(Qt.AlignCenter)
        self.drop_icon.setAccessibleName("Pacer")
        zone.addWidget(self.drop_icon)
        self.title_label = QLabel("Pacer")
        self.title_label.setProperty("role", "Title")   # shared with the About / privacy cards
        self.title_label.setAlignment(Qt.AlignCenter)
        zone.addWidget(self.title_label)
        # What the app does — said to a first visit; a returning one is shown its own sessions.
        self.description = None
        if not returning:
            self.description = QLabel(DESCRIPTION)
            self.description.setProperty("role", "WelcomeSubtitle")
            self.description.setAlignment(Qt.AlignCenter)
            self.description.setWordWrap(True)
            zone.addWidget(self.description)

        # THE DOORS, stacked at one width (column_metrics.action_w), centred.
        self.open_btn = QPushButton(OPEN_LABEL)
        self.open_btn.clicked.connect(on_open)
        self.demo_btn = None
        self.demo_label = demo_label
        if on_demo is not None:
            self.demo_btn = QPushButton(demo_label)
            self.demo_btn.clicked.connect(on_demo)
        doors = QVBoxLayout()
        zone.addLayout(doors)
        doors.setSpacing(theme.SPACE_S)
        if returning or self.demo_btn is None:
            self.primary_btn = self.open_btn
            self.open_btn.setProperty("variant", "primary")
            order = [self.open_btn]
        else:
            self.primary_btn = self.demo_btn
            self.demo_btn.setProperty("variant", "primary")
            order = [self.demo_btn, self.open_btn]
        for btn in order:
            btn.setMinimumWidth(m.action_w)
            doors.addWidget(btn, 0, Qt.AlignHCenter)

        self.recent_rows: list[QPushButton] = []
        self.library_btn = None
        self.drop_hint = QLabel(DROP_HINT)
        self.drop_hint.setProperty("role", "Hint")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.demo_note = None
        if returning:
            self._build_recent(zone, on_recent, on_library)
            foot = QVBoxLayout()
            zone.addLayout(foot)
            foot.setSpacing(theme.SPACE_XXS)
            foot.addWidget(self.drop_hint)
            if self.demo_btn is not None:
                # Q2 (a): the demo stays, as a small link. Floored at the widest thing it says, so
                # the busy swap cannot move it.
                self.demo_btn.setObjectName("WelcomeLink")
                self.demo_btn.ensurePolished()
                self.demo_btn.setMinimumWidth(max(
                    busy_button_width(self.demo_btn, t)
                    for t in (DEMO_LABEL, DEMO_GET_LABEL, BUSY_DEMO_LABEL)))
                foot.addWidget(self.demo_btn, 0, Qt.AlignHCenter)
        else:
            zone.addWidget(self.drop_hint)
            if self.demo_btn is not None:
                self.demo_note = QLabel(DEMO_NOTE)
                self.demo_note.setProperty("role", "Hint")
                self.demo_note.setAlignment(Qt.AlignCenter)
                self.demo_note.setWordWrap(True)
                zone.addWidget(self.demo_note)

        tab = [b for b in (self.primary_btn, self.open_btn if self.primary_btn is not self.open_btn
                           else None) if b is not None]
        tab += [r for r in self.recent_rows if r.isEnabled()]
        tab += [b for b in (self.library_btn, self.demo_btn if returning else None)
                if b is not None]
        for a, b in zip(tab, tab[1:], strict=False):
            QWidget.setTabOrder(a, b)

        # THE ERROR SLOT, ON THE CANVAS ABOVE THE CARD — always built, `RetainSizeWhenHidden`, so
        # the clean frame and the failure frame have IDENTICAL geometry. FIXED width (the card's),
        # not maximum: a centred word-wrapping label is otherwise granted its narrow sizeHint and
        # clipped inside the lines reserved for it. The ⚠ marks it as a failure without hue.
        self.error_label = QLabel("")
        self.error_label.setProperty("role", "WelcomeError")
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setWordWrap(True)
        self.error_label.setFixedWidth(self._card_size().width())
        self.error_label.setMinimumHeight(m.error_h)
        policy = self.error_label.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.error_label.setSizePolicy(policy)
        if error:
            text = f"⚠  {error}"
            if error_path:
                text += f"\n{os.path.basename(error_path)}"
                self.error_label.setToolTip(error_path)
        else:
            self.error_label.setVisible(False)
            text = ""
        self.error_label.setText(text)
        root.addWidget(self.error_label, 0, Qt.AlignCenter)
        root.addWidget(self.drop_zone, 0, Qt.AlignCenter)

    def _build_recent(self, zone: QVBoxLayout, on_recent, on_library) -> None:
        """RECENT SESSIONS: a header with "Library…", then one row per `self.recent` entry. The
        texts are the Library's (`library.recent_rows`); only the time is formatted here, with the
        app's one formatter, as the Library window formats it."""
        head = QHBoxLayout()
        zone.addLayout(head)
        title = QLabel(RECENT_TITLE)
        title.setObjectName("WelcomeSection")
        title.setAccessibleName("Recent sessions")
        head.addWidget(title)
        head.addStretch(1)
        rows = QVBoxLayout()
        rows.setSpacing(0)
        zone.addLayout(rows)
        fm = self.fontMetrics()
        texts = [self._row_texts(r) for r in self.recent]
        date_w = max(fm.horizontalAdvance(t["date"]) for t in texts)
        laps_w = max(fm.horizontalAdvance(t["laps"]) for t in texts)
        # The time and the lap count share one TAIL, as wide as the widest missing-file line, so a
        # missing row's file name lines up under the columns it replaces.
        tail = max([fm.horizontalAdvance(t["best"]) + theme.SPACE_L + laps_w for t in texts]
                   + [fm.horizontalAdvance(t["missing"]) for t in texts if t["missing"]])
        for row, t in zip(self.recent, texts, strict=True):
            btn = QPushButton()
            btn.setObjectName("WelcomeRow")
            btn.setAccessibleName(t["spoken"])
            btn.setToolTip(t["tip"])
            cells = QHBoxLayout(btn)
            cells.setContentsMargins(theme.SPACE_M, theme.SPACE_XS, theme.SPACE_M, theme.SPACE_XS)
            cells.setSpacing(theme.SPACE_L)
            track = _ElidedLabel(t["track"], row["way"] + row["tag"])
            cells.addWidget(track, 1)
            date = QLabel(t["date"])
            date.setProperty("role", "Note")
            date.setFixedWidth(date_w)
            cells.addWidget(date)
            if t["missing"]:
                gone = QLabel(t["missing"])
                gone.setProperty("role", "Note")
                gone.setFixedWidth(tail)
                cells.addWidget(gone)
                btn.setEnabled(False)
            else:
                best = QLabel(t["best"])
                best.setProperty("role", "Note")
                best.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                best.setFixedWidth(tail - theme.SPACE_L - laps_w)
                laps = QLabel(t["laps"])
                laps.setProperty("role", "Note")
                laps.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                laps.setFixedWidth(laps_w)
                cells.addWidget(best)
                cells.addWidget(laps)
                if row["trust"]:
                    # The Library's trust tier: muted + italic on the name and the time, so a
                    # provisional best never reads as a plain one.
                    for cell in (track, best):
                        cell.setProperty("role", "Note")
                        font = cell.font()
                        font.setItalic(True)
                        cell.setFont(font)
                paths = list(row["paths"])
                if on_recent is not None:
                    btn.clicked.connect(lambda _=False, p=paths: on_recent(list(p)))
            rows.addWidget(btn)
            self.recent_rows.append(btn)
        if on_library is not None:
            self.library_btn = QPushButton(LIBRARY_LABEL)
            self.library_btn.setObjectName("WelcomeLink")
            self.library_btn.clicked.connect(on_library)
            head.addWidget(self.library_btn)

    @staticmethod
    def _row_texts(row: dict) -> dict:
        """What one recent row shows, from a `library.recent_rows` dict."""
        best = row["best"]
        shown = {
            "track": row["track"],
            "date": row["date"] or "—",
            "best": f"best {fmt_time(best)}" if best is not None else "—",
            "laps": plural(row["laps"], "lap"),
            "missing": (f"{library.MISSING_FOOTAGE}: {row['name']}" if row["missing"] else ""),
        }
        tail = shown["missing"] or f"{shown['best']}, {shown['laps']}"
        shown["spoken"] = f"{shown['track'].strip()}, {shown['date']}, {tail}"
        shown["tip"] = row["missing"] or row["name"]
        return shown

    def _card_size(self) -> QSize:
        """The size the card is laid out at: its floor width (a hint ignores a minimum width), and
        the height its wrapping lines take at that width."""
        hint = self.drop_zone.sizeHint()
        w = max(hint.width(), self.drop_zone.minimumWidth())
        return QSize(w, self.drop_zone.heightForWidth(w) if self.drop_zone.hasHeightForWidth()
                     else hint.height())

    def anchors(self) -> Anchors:
        """The card's landmarks, laid out at the card's own size (shown or not)."""
        zone = self.drop_zone
        if not zone.isVisible():
            zone.resize(self._card_size())
            zone.layout().activate()

        def rect(w):
            return QRect(w.mapTo(zone, QPoint(0, 0)), w.size())

        return Anchors(card=zone.size(), glyph=rect(self.drop_icon), title=rect(self.title_label),
                       primary=rect(self.primary_btn), error_h=self.error_label.minimumHeight())

    def keyPressEvent(self, event):
        """Return / Enter activate the focused action. A QPushButton outside a dialog takes only
        Space, and passes Return up to here."""
        btn = self.focusWidget()
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter) and isinstance(btn, QPushButton)
                and btn.isEnabled() and self.isAncestorOf(btn)):
            btn.click()
            return
        super().keyPressEvent(event)


class PBToast(QWidget):
    """A transient "new personal best!" celebration card overlaid on the window — at the bottom of
    the LAP panel's body, see `show_for` / `anchor_region` — when a freshly-analysed session beats
    its track's prior PB on verified timing. Tasteful, not modal: an
    amber-accented card that auto-dismisses after a few seconds, holding that clock while the
    pointer is on it so it never vanishes mid-click. Its PRIMARY action is the question a new PB
    raises — "Compare with your previous PB →", both best laps side by side (board review PS-B4) —
    when the caller can answer it (`on_compare`); "Share your PB →" (the lap card, an image) and
    "See your progress →" (the per-track PB-progression chart) are secondary links, plus a × to
    dismiss now. Without `on_compare` the share button is the primary, as it always was.

    Purely presentational — the caller decides WHEN to show it (library.pb_moment) and passes the
    formatted `title`/`body` + the `on_progress` / `on_share` / `on_compare` callbacks (the last
    two may be None to hide that action). Exposed attributes (title_label / body_label / link_btn /
    share_btn / compare_btn / close_btn) let the suite assert the wording + that each button routes
    to its injected callback."""

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

    def __init__(self, title: str, body: str, on_progress, on_share=None, parent=None,
                 on_compare=None):
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
        self._on_compare = on_compare
        # The widget show_for() was given, i.e. the one this card is placed inside and follows.
        # None until then, and None again after dismiss(), which is what makes _place a no-op for
        # a card that is on its way out.
        self._host: QWidget | None = None
        # A callable returning the rectangle this card must not cover, injected by show_for.
        self._keepout = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_S)
        lay.setSpacing(theme.SPACE_XXS)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PBToastTitle")
        top.addWidget(self.title_label)
        top.addStretch(1)
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("PBToastClose")
        self.close_btn.setMinimumSize(self.MIN_HIT_PX, self.MIN_HIT_PX)
        self.close_btn.setIconSize(QSize(theme.ICON_PX, theme.ICON_PX))
        self.close_btn.setIcon(self._dismiss_icon(hover=False))
        # A pixmap carries no text, so the meaning has to survive somewhere a screen reader reads.
        self.close_btn.setAccessibleName("Dismiss")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.clicked.connect(self.dismiss)
        # ...and the HOVER half of its rule, which a `color:` cannot deliver to a pixmap — see
        # _dismiss_icon and eventFilter.
        self.close_btn.installEventFilter(self)
        top.addWidget(self.close_btn)
        lay.addLayout(top)

        self.body_label = QLabel(body)
        self.body_label.setObjectName("PBToastBody")
        self.body_label.setWordWrap(True)
        lay.addWidget(self.body_label)

        # The actions, each created only when its callback is injected. The PRIMARY one is the
        # compare when there is one, on its own row, and otherwise "Share your PB →" as it always
        # was; the rest are flat links on the row below. Two rows, because three actions abreast
        # would roughly double the card's width over a lap panel that is ~700 px across.
        self.share_btn = None
        self.compare_btn = None
        self.link_btn = None
        if on_compare is not None:
            self.compare_btn = self._action(
                "Compare with your previous PB →",
                "Load the recording that held your previous best as the reference, and play the "
                "two best laps side by side", self._on_compare_clicked, primary=True)
            compare_row = QHBoxLayout()
            compare_row.setContentsMargins(0, 0, 0, 0)
            compare_row.addStretch(1)
            compare_row.addWidget(self.compare_btn)
            lay.addLayout(compare_row)
        link_row = QHBoxLayout()
        link_row.setContentsMargins(0, 0, 0, 0)
        link_row.addStretch(1)
        if on_share is not None:
            self.share_btn = self._action(
                "Share your PB →", "Save a shareable lap card (image) of this personal best",
                self._on_share_clicked, primary=on_compare is None)
            link_row.addWidget(self.share_btn)
        self.link_btn = self._action(
            "See your progress →", "Open this track's personal-best progression chart",
            self._on_link, primary=False)
        link_row.addWidget(self.link_btn)
        lay.addLayout(link_row)

        # Auto-dismiss after a beat (window-owned QTimer so it's cleaned up with the toast).
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def _action(self, text: str, tip: str, slot, primary: bool) -> QPushButton:
        """One of the card's actions: the amber `#PBToastPrimary` or a flat `#PBToastLink`, which
        takes the pointer floor explicitly (see MIN_HIT_PX; the primary stands taller on its own
        padding and an explicit minimum would replace that)."""
        btn = QPushButton(text)
        if primary:
            btn.setObjectName("PBToastPrimary")
            btn.setProperty("variant", "primary")
        else:
            btn.setObjectName("PBToastLink")
            btn.setMinimumHeight(self.MIN_HIT_PX)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        btn.clicked.connect(slot)
        return btn

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
        return self._anchor_state(parent)[0]

    def _anchor_state(self, parent: QWidget) -> tuple[QRect, bool]:
        """`anchor_region`, plus whether that rectangle is the FINAL answer.

        The two are one measurement and they used to be one return value, which is what let the
        card be shown before it was placed. The flag distinguishes the fallback's two very
        different meanings:

          * SETTLED — the anchor widget answered, and the region is its own; or the host has no
            `overlay_anchor` at all (a bare QWidget in a test), in which case the whole-window
            fallback is not a miss, it is the documented answer for that host and it will not
            improve by waiting.
          * PROVISIONAL — the host DOES know where this card belongs, and the widget it named is
            not on screen yet (`isHidden()`, not yet a descendant, or clipped smaller than the
            card). During `_load` that is simply "the layout has not run", and the region will
            change under the card the moment it does.

        A genuinely collapsed panel reads PROVISIONAL too, and that is harmless: the caller waits
        one deferred pass and then shows the card at the fallback anyway."""
        view = getattr(parent, "view", None)
        anchor = getattr(view, "overlay_anchor", None)
        if not callable(anchor):
            return parent.rect(), True
        widget = anchor()
        if widget is None or widget.isHidden() or not parent.isAncestorOf(widget):
            return parent.rect(), False
        shown = QRect(widget.mapTo(parent, QPoint(0, 0)),
                      widget.size()).intersected(parent.rect())
        if (shown.width() < self.width()
                or shown.height() < self.height() + 2 * theme.SPACE_M):
            return parent.rect(), False
        return shown, True

    def show_for(self, parent: QWidget, keepout=None):
        """Show the toast over `parent` and keep it on its anchor for as long as it lives.

        `keepout` is an optional CALLABLE returning a QRect in `parent`'s coordinates that this
        card must not cover — or a list of them — or None. A callable and not a rect, because the card is placed three
        times over 120 ms and the thing being protected moves under it during that (`_place`).
        The caller supplies it — this module stays Qt-only and knows nothing about laps or grids —
        and see `_place` for what it is for.

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
        stale.

        AND RE-PLACING IT IS NOT ENOUGH EITHER, WHICH IS WHY THE **SHOW** IS WHAT DEFERS NOW. All
        of the above still SHOWED the card first and corrected it afterwards, so on the real load
        path the user got the defect anyway: measured at 1440x900, the card was shown at (568, 792)
        and PAINTED there six times over 120 ms — a readable eighth of a second over the Δ chart —
        before the settle pass teleported it 462 px left onto the lap panel (QA W14-01). Three
        placement passes had been added to that sequence and none of them could help, because both
        the immediate one and the 0 ms one run before the layout does.

        The other repair — make the anchor real at that instant — is the one that cannot be had.
        Forcing the new view's layout early would still not settle it: `CentralView.showEvent`
        RESTORES the persisted grid splitter sizes on a 120 ms timer, and that restore is the last
        thing that moves the panel this card sits in. There is no earlier moment at which the
        answer is true, so the card waits for it instead of guessing and jumping.

        So: place immediately, and show only once the placement is SETTLED (`_anchor_state`) —
        which for a host with no `overlay_anchor` (a bare QWidget in a test, a window torn down
        inside the same block) is still immediately, unchanged. A host that names an anchor it has
        not laid out yet gets the card from the first deferred pass whose answer is real, and from
        the 120 ms one unconditionally, so a permanently-collapsed panel still celebrates. The
        auto-dismiss clock starts when the card becomes visible, so the wait is not taken out of
        the 6 s anyone gets to read it."""
        self._host = parent
        self._keepout = keepout
        parent.installEventFilter(self)
        # Owned by this widget (never QTimer.singleShot's static form), so a card dismissed inside
        # the settle window takes its pending re-places to the grave with it.
        for delay, slot in ((0, self._place_then_reveal_if_settled),
                            (self.SETTLE_MS, self._place_then_reveal)):
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(slot)
            t.start(delay)
        if self._place():
            self._reveal()

    def _place_then_reveal_if_settled(self):
        """The 0 ms pass: one turn of the event loop is enough for a host that was already laid
        out, and not enough for one mid-`_load`. Show only if the answer is real by now."""
        if self._place():
            self._reveal()

    def _place_then_reveal(self):
        """The 120 ms pass — the same beat as CentralView's splitter restore, and the last one
        there is. Whatever the answer is now is the answer, so the card is shown either way: a
        celebration that never appears is worse than one at the documented fallback."""
        self._place()
        self._reveal()

    def _reveal(self):
        """Raise and show the card, once, and start its clock from that moment. A no-op for a card
        already up or already dismissed (`dismiss` clears `_host`), so the deferred passes can
        never resurrect one."""
        if self._host is None or self.isVisible():
            return
        self.raise_()
        self.show()
        self._timer.start(self.AUTO_DISMISS_MS)

    def _place(self) -> bool:
        """Put the card at the BOTTOM of its anchor region, centred. Safe to call at any time.

        BOTTOM, not top, and that is the whole placement decision. The anchor region is a panel's
        BODY (`anchor_region`), so its top edge is immediately under that panel's header — and the
        lap grid puts its own column headers there, so a card inset from the top would land on a
        header again, one level down. It also puts the card in the window's bottom-left, which is
        where a transient notification conventionally lives, instead of centred on the splitter
        between two columns.

        BUT NOT OVER THE ROW IT IS ANNOUNCING, WHICH THE BOTTOM DID NOT GUARANTEE. This method
        used to justify the bottom edge with "the rows a card covers there are the ones furthest
        from the ★ best lap this card is about, and they scroll" — and the app's own load path
        falsifies both halves: it selects the session best and scrolls it into view, and a session
        best is usually late, so the grid arrives at its scroll MAXIMUM with that row at the bottom.
        Measured at 1440x900 the card covered all 27 px of it across 60.5 % of its width, including
        the `1:08.771` Time cell the card exists to celebrate (68.5 % at 1280x800, 77.0 % at the
        window's own 973x528 minimum), with the table already at scroll max so it could not be
        scrolled clear. So the caller may name a rectangle to keep clear (`show_for(keepout=…)`)
        and the card is LIFTED just above it — still bottom-anchored, still inside the region,
        still covering nothing structural, and unchanged when there is nothing to avoid or no room
        to avoid it.

        Clamped rather than trusted: a region shorter or narrower than the card (a panel dragged
        small) pins the card to the region's top-left and lets it overhang, because a celebration
        that is off-screen is worse than one that overlaps. Nothing here can push it outside
        `parent`.

        Returns whether the region it used was SETTLED — see `_anchor_state`. `show_for` reads
        that to decide whether the card may be shown yet; a dismissed card (no host) reports
        False, which is also what makes every re-place in flight a no-op."""
        parent = self._host
        if parent is None:
            return False
        self.adjustSize()
        region, settled = self._anchor_state(parent)
        x = region.left() + max(0, (region.width() - self.width()) // 2)
        y = max(region.top() + theme.SPACE_M,
                region.bottom() + 1 - theme.SPACE_M - self.height())
        y = self._clear_of_keepout(region, x, y)
        self.move(max(0, min(x, max(0, parent.width() - self.width()))),
                  max(0, min(y, max(0, parent.height() - self.height()))))
        return settled

    def _clear_of_keepout(self, region: QRect, x: int, y: int) -> int:
        """`y`, raised to sit SPACE_M above each of the caller's keep-out rectangles the card at
        (x, y) would overlap — the callable may return one QRect or several, taken lowest first,
        so a card lifted clear of the excluded-laps strip is then checked against the selected row
        above it — and left exactly as it was when it overlaps none, when the caller named
        nothing, or when a lift would push the card out of its own anchor region.

        The last clause is why this returns a y rather than moving the card: a region too short to
        hold both keeps today's placement, so the worst case is the behaviour that shipped rather
        than a card half outside the panel it belongs to."""
        avoid = self._keepout() if callable(self._keepout) else None
        rects = [avoid] if isinstance(avoid, QRect) else list(avoid or [])
        for rect in sorted((r for r in rects if r is not None and not r.isEmpty()),
                           key=lambda r: r.bottom(), reverse=True):
            if not QRect(x, y, self.width(), self.height()).intersects(rect):
                continue
            lifted = rect.top() - theme.SPACE_M - self.height()
            if lifted < region.top() + theme.SPACE_M:
                break   # no room above this one: keep the placement so far
            y = lifted
        return y

    @staticmethod
    def _dismiss_icon(hover: bool) -> QIcon:
        """The dismiss ✕ as a Phosphor pixmap, carrying BOTH of the state cues its stylesheet rule
        spells for the character it replaces.

        `QPushButton#PBToastClose` declares `color: text_dim`, `:hover { color: text }` and
        `:focus { color: accent_hover }`. **A stylesheet `color:` cannot reach a pixmap** — this is
        the same class of miss as the five other QSS mechanisms that have reached a widget's rule
        but not its pixels in this app — so both states are painted here instead:

          * NORMAL is re-set from `eventFilter` on Enter/Leave, because Qt gives a QPushButton no
            icon mode for "the pointer is on me";
          * ACTIVE is the FOCUS state, because `QCommonStyle::drawControl(CE_PushButtonLabel)`
            picks `QIcon::Active` from `State_HasFocus`, not from `State_MouseOver`. Measured on
            the real card: the character's focus cue changed 32 px of the button's 576; the pixmap's
            changes 52. (tests/test_focus_cues.py enumerates this control — a silent focus ring on
            the one card in the app that deletes itself after six seconds is a HIGH here.)"""
        px = QSize(theme.ICON_PX, theme.ICON_PX)
        ic = QIcon()
        ic.addPixmap(theme.icon(DISMISS_ICON, color=C.text if hover else C.text_dim).pixmap(px),
                     QIcon.Normal)
        ic.addPixmap(theme.icon(DISMISS_ICON, color=C.accent_hover).pixmap(px), QIcon.Active)
        return ic

    def eventFilter(self, obj, event):
        """Follow the anchor when the window resizes under the card (its 6 s outlives a drag), and
        carry the dismiss button's hover tint, which its QSS rule cannot (see _dismiss_icon)."""
        if obj is self._host and event.type() == QEvent.Resize and self.isVisible():
            self._place()
        elif obj is self.close_btn and event.type() in (QEvent.Enter, QEvent.Leave):
            self.close_btn.setIcon(self._dismiss_icon(hover=event.type() == QEvent.Enter))
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

    def _on_compare_clicked(self):
        """Route to the injected compare (load the previous PB as the reference), then dismiss."""
        self.dismiss()
        if self._on_compare is not None:
            self._on_compare()

    def dismiss(self):
        self._timer.stop()
        self._host = None   # any re-place still in flight becomes a no-op
        self.hide()
        self.deleteLater()
