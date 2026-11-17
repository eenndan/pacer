"""The Coaching page (F10): where to find time vs your own best lap.

A read-only page over a precomputed ``coaching.Opportunities`` (no analysis here). PACER-FREE:
only the ``coaching`` dataclasses + ``coaching.reason_sentence``. Each row's Jump button emits
``jump_requested(cid, entry_dist)`` (the app selects the corner + seeks the best lap to its entry).
When ``opportunities.enough`` is False the page is a friendly "need more laps" state.

ONE RANKING, ONE PLACE (board review R11 / PS-5). There used to be a modal Opportunities dialog
rendering the same ranking as this page, kept "for the full ranking + jump-to". This page already
shows as much of the ranking as its viewport holds; its Entry·Apex·Exit bars and Jump buttons now
appear whenever the page is wide enough for them, and Coaching ▸ Opportunities opens this page
full-window instead of a second copy of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import coaching, data_quality, focus, theme, trace_paint, units
from . import stats as stats_service
from ._signal import fmt_signed, fmt_time, lap_label, plural
from .coaching import PANEL_TOP_N
from .lap_table import PROVISIONAL_TOOLTIP, estimated_timing_tooltip, set_corner_direction
from .theme import PROVISIONAL_COLOR, C
from .widgets import ActionChip, EmptyState, PanelHeader, WrapLabel

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# column indices. The Entry·Apex·Exit bars and the Jump buttons (the retired modal's two extra
# columns) come AFTER the reason, so the four the page always had keep their places.
_COL_CORNER, _COL_LOST = 0, 1
_PANEL_COL_REACH, _PANEL_COL_REASON, _PANEL_COL_PHASES, _PANEL_COL_GO = 2, 3, 4, 5
_PANEL_COL_CMP = 6   # a row's Compare (cool/compare-at-corner), after Jump
# NB (M4): "Time lost" is the cross-lap MEDIAN per-corner delta; the Entry·Apex·Exit column is a
# DIFFERENT statistic — per third of the corner, the median Δt over the laps (where in the corner
# the time goes), three medians that need not sum to "Time lost" or share its sign. Its header must
# not also claim to be "time lost", or the two columns read as self-contradictory.
PHASE_COL_PX = 150   # the proportional bar's stable width (its segments are shares of it)

# L5-06/L5-08: how a header sits over its own column, and what it says on hover.
#
# A header is a label for the data UNDER it, but `defaultAlignment` centres every one of them: at a
# maximized 1234-px reason column "How to find it" floated 611 px from the left-aligned sentence it
# names. So numbers align right with their right-aligned cells and prose aligns left with its cells.
# The tooltips are new — all four panel headers carried an empty one, so a header the layout clips
# (at the app's own minimum "How to find it" paints as a hard-clipped "How to find") had nothing to
# hover for the full label. Keyed by the header TEXT so the dialog's six and the panel's four share
# one definition and can't drift.
_HEADER_ALIGN = {"Time lost": Qt.AlignRight, "Done it?": Qt.AlignRight}
# The phase column's header and every header's hover are `coaching`'s (PHASE_HEADER, COLUMN_TIPS):
# the exported report prints the same words as its column notes.
PHASE_HEADER = coaching.PHASE_HEADER
_HEADER_TIPS = coaching.COLUMN_TIPS


def _style_headers(table: QTableWidget, headers: list[str]):
    """Align every header over its own column's cells and give it its tooltip (L5-06/L5-08)."""
    for col, label in enumerate(headers):
        item = table.horizontalHeaderItem(col)
        if item is None or not label:
            continue
        item.setTextAlignment(_HEADER_ALIGN.get(label, Qt.AlignLeft) | Qt.AlignVCenter)
        item.setToolTip(_header_tip(table, label))


def _header_tip(table: QTableWidget, label: str) -> str:
    """`label`'s hover on `table`: the debrief's words while the table's "debrief" property is set
    (`coaching.column_tip`: its reason cells carry no braking-direction line), else COLUMN_TIPS."""
    return coaching.column_tip(label, debrief=bool(table.property("debrief")))


def _header_chrome_px(table: QTableWidget, col: int, label: str) -> int:
    """The non-text width the style adds to a header section (the QSS `QHeaderView::section`
    padding + margins), measured as the section's own size hint minus the label's advance — so no
    QSS constant is hard-coded here and the number follows the stylesheet."""
    hdr = table.horizontalHeader()
    return max(hdr.sectionSizeHint(col) - hdr.fontMetrics().horizontalAdvance(label), 0)


def _elide_header(table: QTableWidget, col: int, label: str, chrome_px: int):
    """Elide `col`'s header into the width the style really paints text into, full label on hover.

    L5-06: Qt's own header elide measures the FULL section, but the QSS padding lives INSIDE it, so
    a label that overflows by less than the padding is hard-clipped with no ellipsis at all — "How
    to find it" advances 82 px inside a 100-px section and paints as "How to find", which a naive
    width test passes. Only the stretch/reason column is elided: the content-sized columns never
    clip, and re-eliding one of them would feed its own size hint. The fixed point is stable — the
    elided label is measured to fill the section, so the section's next hint is the size it already
    has."""
    item = table.horizontalHeaderItem(col)
    if item is None:
        return
    fm = table.horizontalHeader().fontMetrics()
    avail = table.horizontalHeader().sectionSize(col) - chrome_px
    text = label if fm.horizontalAdvance(label) <= avail else fm.elidedText(
        label, Qt.ElideRight, max(avail, 0))
    if item.text() != text:
        item.setText(text)
    tip = _header_tip(table, label)
    item.setToolTip(tip if text == label else f"{label} — {tip}".strip(" —"))


# WHICH ROWS, AND IN WHAT WORDS, IS NOT DECIDED HERE. The shown rows (`coaching.shown_rows`, no
# "+0.00 s" row), the ranked ones, the headline's shortlist (`coaching.shortlist`, PANEL_TOP_N), the
# headline and every cell's text are Qt-free functions in `coaching`, so a surface with no widget
# (the exported report, the share card) prints the same answer (`coaching.page_text`). This module
# owns only how they are laid out, coloured and hovered.


# The two no-table cases' copy is `coaching.empty_state_copy` (one author: the exported report's
# COACHING section prints it too); the name stays importable from here.
empty_state_copy = coaching.empty_state_copy


# The share of the page the theme may take before it starts shedding lines (see ThemeBlock.fit_into).
#
# MEASURED, and this is why it is a budget and not a fixed block: a wrapping three-line summary is
# 88 px at a 640-px-wide page and 159 px at the app's own 280x196 minimum, where it left the table a
# viewport 0 px tall — the whole ranked list gone, under a headline about it. A page whose leading
# summary displaces the thing it summarizes is worse than one with no summary.
THEME_MAX_FRACTION = 0.35


class ThemeBlock(QWidget):
    """The session's ONE theme plus at most two actions, above the per-corner table.

    WHY IT LEADS. Twelve ranked corners is a report, not coaching; the compression into one theme
    and a short action list is the part a human coach does and the part every surveyed tool skips.
    Everything here comes from ``coaching.session_theme`` — which clusters only over what this app
    MEASURES (the reach axis and the five reason signals) and says "no single theme" rather than
    inventing one. Empty (and hidden) when nothing is ranked.

    AND IT YIELDS (``fit_into``), the vertical twin of the page's column budget: it sheds its second
    action, then its first, then itself, rather than take more than ``THEME_MAX_FRACTION`` of the
    page. Whatever it sheds stays reachable on the header strip's tooltip, so the theme is never
    lost — only demoted.

    Read-only, rebuilt on refresh; no analysis of its own."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sentence = ""
        self._lines: list[str] = []
        lay = QVBoxLayout(self)
        # The block sits between two hairline-bounded strips, so it takes the panel gutter
        # horizontally and the section step vertically — the same inset a PanelHeader's text has.
        lay.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_S)
        lay.setSpacing(theme.SPACE_XS)
        # The theme sentence is the loudest line on the page and takes the DEFAULT label tone
        # (primary text); the actions rank below it and take the app's secondary-prose role.
        self.headline = WrapLabel("")
        lay.addWidget(self.headline)
        self.actions = [WrapLabel(""), WrapLabel("")]
        for a in self.actions:
            a.setProperty("role", "Note")
            lay.addWidget(a)

    def set_theme(self, opps: coaching.Opportunities) -> None:
        """Fill from a summary; hide the whole block when there is no theme to state."""
        self._sentence, self._lines = coaching.theme_lines(opps)
        self.headline.setText(self._sentence)
        for label, text in zip(self.actions, self._lines + ["", ""], strict=False):
            label.setText(text)
        # The full block, which `fit_into` then trims to the page it actually has.
        self._apply(len(self._lines), bool(self._sentence))

    def full_text(self) -> str:
        """The theme and every action as one string — what the header tooltip carries, so the
        lines ``fit_into`` sheds are demoted rather than deleted."""
        return "\n".join([self._sentence, *self._lines]).strip()

    def _needed_px(self, width: int, n_actions: int) -> int:
        """The height the theme sentence plus `n_actions` action lines would need at `width`.

        Measured from the TEXT and the label fonts, never from the widgets' current state — for two
        reasons. A wrapping label's ``minimumSizeHint`` is its ONE-LINE height (the trap
        widgets.WrapLabel exists for) and its installed minimum ratchets, so neither answers "how
        tall is this text here"; and measuring by SHOWING a line first would make the fit itself
        churn the parent layout, which does not converge — the block was left painting at the size
        it had while being measured (a 159 px block over a table with a 0 px viewport), because
        every hide invalidated a layout that the next pass showed it again for."""
        lay = self.layout()
        m = lay.contentsMargins()
        inner = max(width - m.left() - m.right(), 1)
        fonts = [self.headline.font()] + [a.font() for a in self.actions[:n_actions]]
        texts = [self._sentence] + self._lines[:n_actions]
        need = m.top() + m.bottom() + lay.spacing() * max(len(texts) - 1, 0)
        for font, text in zip(fonts, texts, strict=True):
            need += QFontMetrics(font).boundingRect(
                QRect(0, 0, inner, 0), Qt.TextWordWrap, text).height()
        return need

    def fit_into(self, width: int, budget_px: int) -> None:
        """Show the theme plus as many action lines as fit `budget_px` at `width`; hide the block
        entirely when even the theme sentence alone does not.

        The count is chosen from the widest configuration DOWN, so widening the page brings shed
        lines back (a shed line must never become the floor of the next measurement — the same rule
        widgets.WrapLabel documents for its own minimum). Idempotent."""
        if self._sentence:
            for n in range(len(self._lines), -1, -1):
                if self._needed_px(width, n) <= budget_px:
                    self._apply(n, True)
                    return
        self._apply(0, False)

    def _apply(self, n_actions: int, visible: bool) -> None:
        """Set the block's visible configuration, touching a widget ONLY when it changes — an
        unnecessary setVisible invalidates the parent layout and re-enters this whole pass."""
        for i, label in enumerate(self.actions):
            want = visible and i < n_actions and bool(self._lines[i:i + 1])
            if label.isHidden() == want:
                label.setVisible(want)
        if self.isHidden() == visible:
            self.setVisible(visible)


# The share of the page the focus block may take before it starts shedding lines. Budgeted like
# the theme (and for the same measured reason), but FIRST: the theme is a fresh reading of today,
# while the focus block is the answer to a question the driver asked by making the list.
FOCUS_MAX_FRACTION = 0.30

# ...and the ceiling on the two leading blocks TOGETHER. Each yields on its own, but nothing made
# them yield to each other: two blocks each entitled to a third of the page leave the ranked list
# the last third of a page that is ABOUT the ranked list. The theme takes what is left under this
# after the focus block, so the table keeps at least 45 % of the page whatever the two want.
BLOCKS_MAX_FRACTION = 0.55

# UX-3: the EMPTY list's whole face — one line beside the Add button. The sentence that used to be
# a second block under it (what the list is for) is the line's hover and rides on the header strip's
# tooltip, so nothing is deleted; it stopped costing two rows of the ranking it points at.
FOCUS_EMPTY_LINE = f"Focus list · empty — pick up to {focus.MAX_ITEMS} corners below"
FOCUS_EMPTY_INVITE = (f"Pick up to {focus.MAX_ITEMS} corners to work on. Next time you're at this "
                      "track, Pacer measures the same corners again and says whether they moved — "
                      "or why it can't tell.")
# Why nothing can be added when the block is dormant: a focus list is kept per track.
FOCUS_DORMANT_REASON = "No focus list here — Pacer keeps one per track, and this one isn't known"
# QA REG-2: the hover of a session whose corners can never be a baseline (`Report.baseline_refusal`
# says which fact rules it out; this says why that rules it out).
FOCUS_REFUSED_TIP = ("A focus corner is checked against the session it was set on, and every later "
                     "check refuses a baseline measured on this session's timing, so none is "
                     "offered from it.")
# QA JOURNEY-1: the list's one line on a page with no room for the block, and what its click does.
# The glyph is the panel maximize button's own (central_view._MAXIMIZE_GLYPH), because the click
# does what that button does, on the Coaching page.
FOCUS_LINE_GLYPH = "ph.corners-out"
FOCUS_LINE_TIP = ("Your focus list at this track. The page has no room for it at this size: click "
                  "to see it whole, full-window, with its verdicts and its buttons (Coaching ▸ "
                  "Show focus list). Esc brings the grid back.")


def _sentence(text: str) -> str:
    """`text` as a sentence's start: the refusal is a clause in the empty line, a tooltip alone."""
    return text[:1].upper() + text[1:]


class FocusBlock(QWidget):
    """The training loop's face: the corners the driver put on their focus list, and what THIS
    session is allowed to say about them.

    THE GATE IS THE FEATURE (studio/focus.py). "Did C4 improve?" is a cross-session comparison, and
    a comparison across two days can move up to 4 seconds a lap on conditions alone; PR #258 added
    the session record precisely so the app stops assuming like-for-like. So a line here either
    states a measured change or states why it cannot, and the refusal is structural rather than
    editorial — ``Outcome.delta`` is None whenever the verdict was blocked, so there is no number
    for this widget to print even by accident.

    It yields vertically exactly as ``ThemeBlock`` does (shed the last line, then the one above it,
    then the whole block), and the lines it sheds stay on the header strip's tooltip.

    AN EMPTY LIST IS ONE LINE (UX-3). Measured on the real window at the 1440x900 default, the
    invitation — a headline, a two-line sentence and two buttons, one of which can never be enabled
    on an empty list — was 98 of the page's 417 px, and with the theme above the table it left the
    ranking a 146-162 px viewport: two of its top three rows on SD_19_09, one on SD_30_08. So the
    empty state is the line and the Add button beside it; the sentence is the line's hover.

    A REFUSAL FOR WANT OF A RECORD CARRIES ITS OWN ANSWER (UX-6). The record gate needs one field
    per session, and the owner — who has never written a record — was sent to a 17-input form,
    twice (today's through the File menu, the other day's through the Library). So when the only
    thing missing is a record, the block asks the question a driver can answer at a glance ("Both
    dry?") beside a button that writes exactly that and nothing else. It is an offer, not an
    assumption: nothing is written until the click (``focus.mark_dry_prompt``).

    EVERY CORNER ON THE LIST HAS ITS OWN REMOVE (board review PS-B1). The debrief pre-promotes the
    top corners, and a default is only honest if undoing it is one click per corner. The one Remove
    button there acted on the table's SELECTION, so a corner had to be found and selected in
    today's ranking first — and a corner promoted on another day need not be near its top: 19 Sep
    ranks the 30 Aug list's C3 fifth, and an abstained or sub-resolution corner not at all.

    Read-only over a ``focus.Report`` plus a selection: the promote / drop / mark gestures are
    SIGNALS the app acts on (it owns the app-support stores — the same split ``set_session_record``
    uses)."""

    # The corner the driver wants added to / dropped from the focus list (a corner cid).
    add_requested = Signal(int)
    remove_requested = Signal(int)
    # The recordings (library fingerprints) the driver just marked Dry — exactly the ones the
    # button named, so the app writes what was offered and nothing more.
    mark_dry_requested = Signal(list)
    # Today's top corners, to REPLACE the list with (``focus.replace_offer``, QA NEW-5).
    replace_requested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: list[str] = []
        self._headline = ""
        self._empty = False             # the one-line invitation (an active track, no corners yet)
        self._cids: list[int] = []      # cids currently on the list, in list order
        self._selected: int | None = None
        self._mark_fps: list[str] = []  # the unrecorded sessions the Mark button would write
        self._replace: list[int] = []    # today's top corners the Replace button would store
        self._refusal = ""               # why this session can be no baseline (QA REG-2), or ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_S)
        lay.setSpacing(theme.SPACE_XS)
        self.headline = QLabel("")
        self.headline.setProperty("role", "BarLabel")
        lay.addWidget(self.headline)
        self.lines = [WrapLabel("") for _ in range(focus.MAX_ITEMS)]
        for label in self.lines:
            label.setProperty("role", "Note")
            lay.addWidget(label)
        # The answer to a refusal for want of a record, directly under the line that asks for it.
        mark = QHBoxLayout()
        mark.setContentsMargins(0, 0, 0, 0)
        mark.setSpacing(theme.SPACE_S)
        self.mark_question = QLabel("")
        self.mark_question.setProperty("role", "BarLabel")
        self.mark_button = QPushButton("")
        self.mark_button.setAutoDefault(False)
        self.mark_button.setDefault(False)
        self.mark_button.clicked.connect(self._emit_mark)
        # After the check (never beside a pending Mark): today's top corners, one click to swap in.
        self.replace_button = QPushButton("")
        self.replace_button.setAutoDefault(False)
        self.replace_button.setDefault(False)
        self.replace_button.clicked.connect(self._emit_replace)
        mark.addWidget(self.mark_question)
        mark.addWidget(self.mark_button)
        mark.addWidget(self.replace_button)
        mark.addStretch(1)
        self._mark_row = QWidget()
        self._mark_row.setLayout(mark)
        self._mark_row.setVisible(False)
        lay.addWidget(self._mark_row)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE_S)
        # The empty state's one line, in the BUTTON row rather than above it: the headline label
        # and this one are never shown together (see _apply), so no widget ever changes layout.
        self.empty_line = WrapLabel(FOCUS_EMPTY_LINE)
        self.empty_line.setProperty("role", "BarLabel")
        self.empty_line.setToolTip(FOCUS_EMPTY_INVITE)
        self.empty_line.setVisible(False)
        row.addWidget(self.empty_line, 1)
        # One Remove per corner on the list, in list order (see the class note); hidden when unused.
        self.drop_buttons: list[QPushButton] = []
        for _ in range(focus.MAX_ITEMS):
            button = QPushButton("")
            button.setAutoDefault(False)
            button.setDefault(False)
            button.clicked.connect(lambda _checked=False, b=button: self._emit_remove(b))
            button.setVisible(False)
            row.addWidget(button)
            self.drop_buttons.append(button)
        self.add_button = QPushButton("Add to focus list")
        self.add_button.setAutoDefault(False)
        self.add_button.setDefault(False)
        self.add_button.clicked.connect(self._emit_add)
        row.addWidget(self.add_button)
        self._row_stretch = row.count()
        row.addStretch(1)
        self._buttons = QWidget()
        self._buttons.setLayout(row)
        lay.addWidget(self._buttons)
        self.setVisible(False)

    # ------------------------------------------------------------------ fill
    def set_report(self, report: focus.Report | None, shortlist: list[int] | None = None) -> None:
        """Fill from a ``focus.Report``. Three states, and the difference between the last two
        matters: **None** is DORMANT (this recording has no detected track, so there is nowhere to
        keep a list and inviting one would be an offer the app cannot honour); an **empty** report
        is the invitation (one quiet line and a button — not a nag); a report with outcomes is the
        loop itself."""
        outcomes = list(report.outcomes) if report is not None else []
        self._cids = [o.item.cid for o in outcomes]
        # `report_lines`, not one sentence per outcome: when every corner is blocked for the SAME
        # reason — the common case, and the one the real recordings land in — three copies of the
        # same 200-character explanation bury the one thing the driver can act on.
        self._lines = focus.report_lines(report) if report is not None else []
        self._headline = focus.report_headline(report) if report is not None else ""
        self._empty = report is not None and not report.active
        # A session whose corners every later check would refuse (QA REG-2: a GPS-degraded day
        # stored one with a manual Add, and it waited for a verdict forever) invites no pick: the
        # empty line says why instead, and Add stays off with the same words.
        self._refusal = getattr(report, "baseline_refusal", "") if report is not None else ""
        if self._empty:
            self._headline = (f"Focus list · empty — {self._refusal}" if self._refusal
                              else FOCUS_EMPTY_LINE)
            self._lines = []
            self.empty_line.setText(self._headline)
            self.empty_line.setToolTip(FOCUS_REFUSED_TIP if self._refusal else FOCUS_EMPTY_INVITE)
        prompt = None if self._empty else focus.mark_dry_prompt(report)
        self._mark_fps = [fp for fp, _when in report.unrecorded] if prompt else []
        if prompt:
            self.mark_question.setText(prompt[0])
            self.mark_button.setText(prompt[1])
            self.mark_button.setToolTip(prompt[2])
        self._replace = [] if self._empty or prompt else (
            focus.replace_offer(report, shortlist or []) or [])
        if self._replace:
            who = ", ".join(f"C{c}" for c in self._replace)
            self.replace_button.setText(focus.replace_label(self._replace))
            self.replace_button.setToolTip(
                f"Replaces your focus list with {who}, with baselines measured on this session: "
                "the next session here is checked against them.")
        for widget, want in ((self.mark_question, bool(prompt)), (self.mark_button, bool(prompt)),
                             (self.replace_button, bool(self._replace))):
            if widget.isHidden() == want:
                widget.setVisible(want)
        # On an empty list the trailing stretch gives way, so the line takes the row's slack and
        # the Add button sits at its right edge.
        self._buttons.layout().setStretch(self._row_stretch, 0 if self._empty else 1)
        self.headline.setText(self._headline)
        for label, text in zip(self.lines, self._lines + [""] * focus.MAX_ITEMS, strict=False):
            label.setText(text)
        for button, cid in zip(self.drop_buttons, self._cids + [None] * focus.MAX_ITEMS,
                               strict=False):
            button.setText(f"Remove C{cid}" if cid is not None else "")
            button.setToolTip(f"Take C{cid} off your focus list" if cid is not None else "")
        self._sync_buttons()
        self._apply(len(self._lines), bool(self._headline))

    def cids(self) -> list[int]:
        """The corners on the list, in list order."""
        return list(self._cids)

    def set_selected_corner(self, cid: int | None) -> None:
        """The table's selected corner — what the Add button would promote."""
        self._selected = cid if isinstance(cid, int) else None
        self._sync_buttons()

    def full_text(self) -> str:
        """The whole block as one string — what the header tooltip carries, so a line the height
        budget sheds is demoted rather than deleted (and the empty list's invitation with it)."""
        invite = [FOCUS_REFUSED_TIP if self._refusal else FOCUS_EMPTY_INVITE] if self._empty else []
        return "\n".join([self._headline, *self._lines, *invite]).strip()

    def line_text(self) -> str:
        """The list in ONE line, for a page with no room for the block (QA JOURNEY-1): the corners
        in list order and a question still waiting on the driver ("Both dry?"). "" when there is
        no list: the empty list's invitation and a dormant page get no line."""
        if not self._cids:
            return ""
        ask = f" — {self.mark_question.text()}" if self._mark_fps else ""
        return f"Focus list: {' · '.join(f'C{c}' for c in self._cids)}{ask}"

    def _sync_buttons(self):
        """Label + enablement from the selection and the list. The button SAYS which corner it
        would add, because "Add to focus list" beside a table with a selection elsewhere is an
        instruction with no object."""
        cid, full = self._selected, len(self._cids) >= focus.MAX_ITEMS
        on_list = cid is not None and cid in self._cids
        if self._refusal:            # no corner of this session can be a baseline (QA REG-2)
            self.add_button.setText("Add to focus list")
            self.add_button.setEnabled(False)
            self.add_button.setToolTip(_sentence(self._refusal))
            return
        self.add_button.setText(f"Add C{cid} to focus list" if cid is not None and not on_list
                                else "Add to focus list")
        self.add_button.setEnabled(cid is not None and not on_list and not full)
        self.add_button.setToolTip(
            f"You already have {focus.MAX_ITEMS} corners on the list — remove one first"
            if full and not on_list else
            "Select a corner in the table below to put it on your focus list" if cid is None else
            f"C{cid} is already on your focus list" if on_list else
            f"Work on C{cid}: Pacer will measure this exact stretch of track again next time "
            "you're here")

    def add_state(self) -> tuple[bool, str]:
        """Whether the Add button would put a corner on the list now, and the button's own words
        for why not — for the same gesture made from outside the page (Coaching ▸ Add selected
        corner to focus list, and so ⌘K). A dormant block (no track: nowhere to keep a list) adds
        nothing."""
        if not self._headline:
            return False, FOCUS_DORMANT_REASON
        if self._refusal:
            return False, _sentence(self._refusal)
        if self._selected is None:   # the button's "in the table below" is the page's own words
            return False, "Select a corner on the Coaching page first — then add it from here"
        return self.add_button.isEnabled(), self.add_button.toolTip()

    def _emit_add(self):
        if self._selected is not None:
            self.add_requested.emit(int(self._selected))

    def _emit_remove(self, button: QPushButton):
        """The corner `button` stands for — by its place in the list, never by the table's
        selection (see the class note)."""
        i = self.drop_buttons.index(button)
        if i < len(self._cids):
            self.remove_requested.emit(int(self._cids[i]))

    def _emit_mark(self):
        if self._mark_fps:
            self.mark_dry_requested.emit(list(self._mark_fps))

    def _emit_replace(self):
        if self._replace:
            self.replace_requested.emit(list(self._replace))

    # --------------------------------------------------------------- the fit
    def _needed_px(self, width: int, n_lines: int) -> int:
        """The height the headline plus `n_lines` outcome lines plus the button row would need at
        `width` — measured from the TEXT and the fonts, never from the widgets' current state (see
        ``ThemeBlock._needed_px``: a wrapping label's minimumSizeHint is its one-line height, and
        measuring by showing does not converge)."""
        lay = self.layout()
        m = lay.contentsMargins()
        inner = max(width - m.left() - m.right(), 1)
        if self._empty:
            # One row: the line wraps into what the Add button leaves it, and the row is as tall
            # as the taller of the two.
            button = self.add_button.sizeHint()
            beside = max(inner - button.width() - self._buttons.layout().spacing(), 1)
            line = QFontMetrics(self.empty_line.font()).boundingRect(
                QRect(0, 0, beside, 0), Qt.TextWordWrap, self._headline).height()
            return m.top() + m.bottom() + max(line, button.height())
        texts = [self._headline] + self._lines[:n_lines]
        fonts = [self.headline.font()] + [lb.font() for lb in self.lines[:n_lines]]
        need = m.top() + m.bottom() + lay.spacing() * max(len(texts), 1)
        need += self.add_button.sizeHint().height()
        if self._mark_fps or self._replace:
            # The mark row goes with the buttons, never with the lines: it is the answer to the
            # headline's "no record" as much as to any line, so it stays while lines are shed.
            need += lay.spacing() + max(self.mark_button.sizeHint().height(),
                                        QFontMetrics(self.mark_question.font()).height())
        for font, text in zip(fonts, texts, strict=True):
            need += QFontMetrics(font).boundingRect(
                QRect(0, 0, inner, 0), Qt.TextWordWrap, text).height()
        return need

    def fit_into(self, width: int, budget_px: int) -> int:
        """Show the headline plus as many outcome lines as fit `budget_px`; hide the block entirely
        when even the headline and the buttons do not. Returns the height it settled for (0 when
        hidden) so the caller can budget what is left. Chosen from the widest configuration DOWN,
        so widening the page brings shed lines back. Idempotent."""
        if self._headline:
            for n in range(len(self._lines), -1, -1):
                need = self._needed_px(width, n)
                if need <= budget_px:
                    self._apply(n, True)
                    return need
        self._apply(0, False)
        return 0

    def _apply(self, n_lines: int, visible: bool) -> None:
        """Set the visible configuration, touching a widget ONLY when it changes (an unnecessary
        setVisible invalidates the parent layout and re-enters the whole pass)."""
        for i, label in enumerate(self.lines):
            want = visible and i < n_lines and bool(self._lines[i:i + 1])
            if label.isHidden() == want:
                label.setVisible(want)
        # The empty list shows its one line in place of the headline, and a Remove per corner the
        # list holds — none on an empty one.
        pairs = [(self.headline, not self._empty), (self.empty_line, self._empty)]
        pairs += [(b, i < len(self._cids)) for i, b in enumerate(self.drop_buttons)]
        for widget, want in pairs:
            if widget.isHidden() == want:
                widget.setVisible(want)
        if self._buttons.isHidden() == visible:
            self._buttons.setVisible(visible)
        mark = visible and bool(self._mark_fps or self._replace)
        if self._mark_row.isHidden() == mark:
            self._mark_row.setVisible(mark)
        if self.isHidden() == visible:
            self.setVisible(visible)


DEBRIEF_ESC = "Esc returns to your usual layout."


def debrief_note(promoted: list[int], saved: bool = True) -> str:
    """The debrief's second line: what Pacer just did to the focus list, said as the default it is
    and how to undo it (board review PS-B1: "an explicit, reversible default — say so on screen"),
    then the way out. `saved` False is the synthetic demo's list, which is never written, so it is
    not promised "for next time" (QA EVAL-2)."""
    if not promoted:
        return DEBRIEF_ESC
    labels = [f"C{c}" for c in promoted]
    who = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + f" and {labels[-1]}"
    undo = "remove it if you won't work on it" if len(labels) == 1 else \
        "remove any you won't work on"
    where = "on your focus list for next time" if saved else "on the focus list"
    return (f"Pacer put {who}, from today's top corners, {where} — "
            f"a default, not a decision: {undo}. {DEBRIEF_ESC}")


# The debrief lead's share of the page before it sheds its note, then itself — budgeted FIRST
# (it is what the landing leads with), and small, because what it leads into is the ranking.
DEBRIEF_MAX_FRACTION = 0.15


DEBRIEF_COMPARE = "Compare with your previous PB"


class DebriefBlock(QWidget):
    """The debrief's lead (board review PS-B1): the session's personal-best standing
    (``library.pb_standing_text``) and ``debrief_note``, above the focus list. Shown only while
    the page is the debrief — the Coaching page maximized on a recording's first open — and hidden
    otherwise, so the page everyone else sees is unchanged. It yields height the way
    ``ThemeBlock`` does (the note first, then the whole block).

    A NEW PB's line carries its question beside it (board review PS-B4): "where did the time come
    from?" was File ▸ Open, Coaching ▸ Load reference…, Coaching ▸ Compare vs reference — every
    piece shipped, three menus apart. ``compare_btn`` is that one gesture (``compare_requested``;
    the window loads the previous PB's recording and opens the compare on the two best laps). It
    goes with the PB line, never with the note, so it is shed only with the line it answers."""

    compare_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._texts: list[str] = []
        self._compare = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE_M, theme.SPACE_S, theme.SPACE_M, theme.SPACE_S)
        lay.setSpacing(theme.SPACE_XS)
        self.headline = WrapLabel("")          # the PB line, in the page's primary tone
        self.compare_btn = QPushButton(DEBRIEF_COMPARE)
        self.compare_btn.setAutoDefault(False)
        self.compare_btn.setDefault(False)
        self.compare_btn.setToolTip(
            "Load the recording that held your previous best as the reference, and play the two "
            "best laps side by side")
        self.compare_btn.clicked.connect(self.compare_requested)
        self.compare_btn.setVisible(False)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme.SPACE_S)
        top.addWidget(self.headline, 1)
        top.addWidget(self.compare_btn, 0, Qt.AlignTop)
        self._top = top
        self.note = WrapLabel("")
        self.note.setProperty("role", "Note")
        lay.addLayout(top)
        lay.addWidget(self.note)
        self.setVisible(False)

    def set_lines(self, pb_line: str | None, note: str | None, compare: bool = False) -> None:
        """Fill the block (None for both clears it); `compare` offers the compare beside a PB line."""
        self.headline.setText(pb_line or "")
        self.note.setText(note or "")
        self._texts = [t for t in (pb_line, note) if t]
        self._compare = bool(compare and pb_line)
        self._apply(len(self._texts))

    def full_text(self) -> str:
        return "\n".join(self._texts)

    def _labels(self) -> list[QLabel]:
        return [lb for lb in (self.headline, self.note) if lb.text()]

    def _needed_px(self, width: int, n: int) -> int:
        """Height of the first `n` lines at `width`, from the text and fonts (see
        ``ThemeBlock._needed_px`` for why never from the widgets' current state)."""
        lay = self.layout()
        m = lay.contentsMargins()
        inner = max(width - m.left() - m.right(), 1)
        labels = self._labels()[:n]
        need = m.top() + m.bottom() + lay.spacing() * max(len(labels) - 1, 0)
        for label in labels:
            beside = self._compare and label is self.headline
            # The PB line wraps into what the compare button leaves it, and its row is as tall as
            # the taller of the two (FocusBlock's empty state measures the same shape).
            button = self.compare_btn.sizeHint() if beside else QSize(0, 0)
            wrap = max(inner - (button.width() + self._top.spacing() if beside else 0), 1)
            need += max(QFontMetrics(label.font()).boundingRect(
                QRect(0, 0, wrap, 0), Qt.TextWordWrap, label.text()).height(), button.height())
        return need

    def fit_into(self, width: int, budget_px: int) -> int:
        """Show as many lines as fit `budget_px`; returns the height used (0 when hidden)."""
        for n in range(len(self._texts), 0, -1):
            need = self._needed_px(width, n)
            if need <= budget_px:
                self._apply(n)
                return need
        self._apply(0)
        return 0

    def _apply(self, n: int) -> None:
        shown = self._labels()[:n]
        for label in (self.headline, self.note):
            want = label in shown
            if label.isHidden() == want:
                label.setVisible(want)
        want = self._compare and self.headline in shown
        if self.compare_btn.isHidden() == want:
            self.compare_btn.setVisible(want)
        if self.isHidden() == bool(shown):
            self.setVisible(bool(shown))


# ------------------------------------------------------------------ the debrief hero
# THE FIRST OPEN IS A PICTURE, NOT A PAGE (cool/debrief-hero). The debrief was grey prose in the top
# quarter of a maximized page over ~60 % of empty canvas, and the app's best visual — the track
# with its speed-coloured lap line — was nowhere on its first screen. The hero fills that canvas
# with the best lap's time, large, and the best lap's line, with the focus-list corners the lead
# names ("Pacer put C1, C5 and C7 …") marked where they are. It says nothing the page does not
# already say, and it is not an action bar: RUL-7 (no lap-first debrief buttons) stands.
HERO_BEST_LABEL = "BEST LAP"
HERO_PROVISIONAL = "provisional timing"     # the lap table's demotion, said in words here
HERO_MIN_PX = 150          # below this the picture is a scribble: the hero hides, the page is as before
HERO_LINE_PX = 4.0         # the lap line's width at the hero's size (the card's 7 px is a 1080-px canvas)
HERO_RING_PX = 7.0         # a focus corner's ring radius
HERO_LABEL_GAP_PX = 18.0   # apex → label centre, outward from the trace's centroid
HERO_RAMP_PX = 72          # the speed key's ramp width
# A landing lays the page out in a few passes (the maximize, the deferred grid restores, the focus
# line): measured, the hero was 275 px tall for one paint and 291 px for good. It renders once the
# size has held this long and the layout passes still queued have run (`DebriefHero._on_settled`),
# and blits what it has (or nothing) meanwhile.
HERO_SETTLE_MS = 60
HERO_LAYOUT_ROUNDS = 4     # queued layout passes run before a settled render (one per nesting level)
_QWIDGETSIZE_MAX = (1 << 24) - 1   # Qt's QWIDGETSIZE_MAX, which PySide6 does not export


@dataclass(frozen=True)
class HeroData:
    """What the debrief hero paints — display values off Session accessors only."""

    time_text: str                 # the best lap, fmt_time — the lap table's own string; "" while
    #                                the timing is provisional (the table names no best lap then)
    trust: str                     # "", HERO_PROVISIONAL, or the chip's word for a degraded clock
    #                                (data_quality.timing_word: "ESTIMATED", "GPS LOW", "NO GPS")
    trust_tip: str                 # the lap table's tooltip for that demotion ("" when none)
    caption: str                   # "Synthetic demo circuit · 14 clean laps" (circuit when known)
    xs: np.ndarray                 # the best lap's trace, local metres
    ys: np.ndarray
    seg: np.ndarray                # per-segment speed bucket (map_render.rainbow_channel), -1 = gap
    speed_lo: str                  # the speed key's two ends ("38", "71 km/h"), "" when flat
    speed_hi: str
    corners: dict                  # cid -> (x, y) apex on the best lap (corner_map_markers)
    finish: tuple | None = None    # (x, y, ux, uy): the start/finish crossing and the way the lap
    #                                runs there (trace_paint.finish_at), or None — no mark


def debrief_hero_data(session, n_clean: int | None, unit: str | None = None) -> HeroData | None:
    """The hero's data, or None when the session cannot draw it (no best lap, no trace, or a test
    double without the accessors) — the hero then stays hidden and the page is what it was.

    THE TIME IS THE LAP TABLE'S STRING, WITH THE LAP TABLE'S DEMOTIONS. ``fmt_time(lap_time(best))``
    is the Time cell's text. On an unconfirmed start line the table names no best lap (no star, no
    green), so neither does the hero: no BEST LAP figure, only "provisional timing" and the table's
    tooltip. A degraded clock keeps the figure, italic, under the lap panel's own chip word
    (``data_quality.timing_word``: ESTIMATED only on the media-clock fallback, GPS LOW on a true
    clock with rejected fixes), so the largest number in the app never reads as more — or
    differently — certain than the cell it repeats."""
    try:
        best = session.best_lap_id()
        if best is None:
            return None
        lap_s = float(session.lap_time(best))
        tr = trace_paint.lap_trace(session, best, unit)   # the share card's own fetch
        markers = session.corners.corner_map_markers()
        verified = bool(session.timing_verified)
        quality = session.timing_quality
    except Exception:  # noqa: BLE001 — a debrief without its picture beats no debrief
        return None
    if tr is None or not math.isfinite(lap_s):
        return None
    time_text = fmt_time(lap_s)
    if not verified:
        trust, tip, time_text = HERO_PROVISIONAL, PROVISIONAL_TOOLTIP, ""
    elif data_quality.timing_word(quality):
        trust, tip = data_quality.timing_word(quality), estimated_timing_tooltip(quality)
    else:
        trust, tip = "", ""
    bits = [session.track_name] if getattr(session, "track_name", None) else []
    if n_clean:
        bits.append(plural(n_clean, "clean lap"))
    return HeroData(time_text=time_text, trust=trust, trust_tip=tip,
                    caption=" · ".join(bits), xs=tr.xs, ys=tr.ys, seg=tr.seg,
                    speed_lo=tr.speed_lo, speed_hi=tr.speed_hi,
                    corners=trace_paint.corner_apexes(markers),
                    finish=trace_paint.finish_at(session, tr.xs, tr.ys))


def _label_rect(c: QPointF, ang: float, tw: float, th: float, placed: list[QRectF],
                box: QRectF) -> QRectF:
    """A focus corner's label plate at the hero's gap (`trace_paint.label_rect`, which the share
    card's corner labels use too)."""
    return trace_paint.label_rect(c, ang, tw, th, placed, box, HERO_LABEL_GAP_PX)


class DebriefHero(QWidget):
    """The debrief's picture (see the block above): the best lap's time at ``theme.DISPLAY`` beside
    the best lap's speed-coloured line, the focus corners ringed and labelled.

    PAINTED ONCE. The whole hero is one cached pixmap, re-rendered only when its size (once it has
    held for ``HERO_SETTLE_MS`` and the queued layout passes have run, ``_on_settled``), its data,
    its focus corners or the palette change (``renders`` counts them, for the perf pin); a paint
    event blits it.

    EVERY FOCUS CORNER IS RINGED; ONLY A CORNER WITH A ROW IS A TARGET. The picture shows the whole
    focus list, but a corner carried over from an earlier session can have no coaching row today,
    and so no Jump to repeat: it takes no hand cursor and a click on it does nothing
    (``set_jumpable``, from the page's rows). A target emits ``corner_clicked(cid)``.

    The trace is ``trace_paint``'s, the painter the share card's map calls too (north-up fit,
    rainbow buckets, no chord across a dropout, the lap closed where its two ends meet), with the
    live map's start/finish mark and its arrow at the lap's crossing."""

    corner_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: HeroData | None = None
        self._focus: tuple[int, ...] = ()
        self._pixmap: QPixmap | None = None
        self._key: tuple | None = None
        self._rings: list[tuple[int, QPointF]] = []      # (cid, ring centre) of the last render
        self._jumpable: frozenset[int] = frozenset()     # corners with a row (a Jump) on the page
        self.renders = 0
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(HERO_SETTLE_MS)
        self._settle.timeout.connect(self._on_settled)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.setMinimumHeight(0)
        self.setVisible(False)

    # ---------------------------------------------------------------- state
    def set_data(self, data: HeroData | None) -> None:
        self._data = data
        self._key = None
        self.setToolTip(data.trust_tip if data is not None else "")
        self.update()

    def data(self) -> HeroData | None:
        return self._data

    def set_focus(self, cids: list[int]) -> None:
        cids = tuple(int(c) for c in cids)
        if cids != self._focus:
            self._focus = cids
            self._key = None
            self.update()

    def set_jumpable(self, cids) -> None:
        """The corners a ring click may jump to: those with a coaching row on the page."""
        self._jumpable = frozenset(int(c) for c in cids)

    def focus_cids(self) -> list[int]:
        """The corners the picture rings: the focus list's, where the best lap has an apex for it."""
        if self._data is None:
            return []
        return [c for c in self._focus if c in self._data.corners]

    def time_text(self) -> str:
        return self._data.time_text if self._data is not None else ""

    def shows_picture(self) -> bool:
        """On screen with data and room for it (``HERO_MIN_PX``)."""
        return (self._data is not None and self.isVisible() and self.height() >= HERO_MIN_PX)

    def ring_points(self) -> list[tuple[int, QPointF]]:
        """(cid, ring centre in widget px) of every ringed corner — rendering the picture if stale."""
        self._ensure_pixmap()
        return list(self._rings)

    def target_points(self) -> list[tuple[int, QPointF]]:
        """The rings a click acts on: those whose corner has a row (``set_jumpable``)."""
        return [(c, pt) for c, pt in self.ring_points() if c in self._jumpable]

    # ---------------------------------------------------------------- painting
    def _ensure_pixmap(self) -> QPixmap | None:
        if self._data is None or self.width() < 2 or self.height() < HERO_MIN_PX:
            self._rings = []
            return None
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, self._focus, theme.rainbow_colors(2)[0])
        if key != self._key or self._pixmap is None:
            self._pixmap = self._render(dpr)
            self._key = key
        return self._pixmap

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._settle.start()

    def _on_settled(self) -> None:
        """The size has held ``HERO_SETTLE_MS`` — but a quiet clock is not a laid-out page. On the
        landing the hero's final height comes from a POSTED layout request (the panel's
        ``_relayout``, run inside the maximize's resize, re-fits the lead blocks and shows the theme
        block; the page applies their heights a turn later), and on a loaded Mac the clock ran out,
        and the hero painted, with that request still queued: the demo's first open rendered the
        picture at 275 px and again at 291 px (3 of 75 landings beside 4 CPU burners). So the
        queued passes run now, a nesting level per round; one that moves the hero restarts the wait
        (``resizeEvent``), and the picture renders only at a size the layout has finished with —
        ``stats_panel``'s idiom for reading a laid-out page."""
        for _ in range(HERO_LAYOUT_ROUNDS):
            QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
            if self._settle.isActive():
                return
        self.update()

    def paintEvent(self, _event):
        if self.height() < HERO_MIN_PX:
            return
        # Mid-layout: the last picture as it stands (or nothing), not a render per pass.
        pm = self._pixmap if self._settle.isActive() else self._ensure_pixmap()
        if pm is not None:
            p = QPainter(self)
            p.drawPixmap(0, 0, pm)
            p.end()

    def _render(self, dpr: float) -> QPixmap:
        self.renders += 1
        d = self._data
        w, h = self.width(), self.height()
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        pad = theme.SPACE_L
        text_w = self._paint_text(p, d, pad, h)
        self._paint_map(p, d, QRectF(pad + text_w + theme.SPACE_2XL, pad,
                                     w - text_w - theme.SPACE_2XL - 2 * pad, h - 2 * pad))
        p.end()
        return pm

    def _paint_text(self, p: QPainter, d: HeroData, pad: int, h: int) -> int:
        """The left column, top-aligned: label, the time, its trust mark, the caption; the speed key
        at the foot. Returns the column's width."""
        label_f = theme.ui_font(theme.CAPTION, theme.W_SEMIBOLD)
        label_f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        time_f = theme.mono_font(theme.DISPLAY, theme.W_SEMIBOLD)
        body_f = theme.ui_font(theme.BODY)
        cap_f = theme.ui_font(theme.CAPTION)
        lines = ([(HERO_BEST_LABEL, label_f, C.text_muted, False),
                  (d.time_text, time_f, C.text, bool(d.trust))] if d.time_text else [])
        if d.trust:     # provisional: muted, the table's tone; a degraded clock: the chip's accent
            lines.append((d.trust, cap_f, PROVISIONAL_COLOR if d.trust == HERO_PROVISIONAL
                          else C.accent, False))
        if d.caption:
            lines.append((d.caption, body_f, C.text_dim, False))
        width = 0
        y = float(pad)
        for text, font, colour, italic in lines:
            font = QFont(font)
            font.setItalic(italic)
            fm = QFontMetrics(font)
            p.setFont(font)
            p.setPen(QColor(colour))
            y += fm.ascent()
            p.drawText(QPointF(pad, y), text)
            y += fm.descent() + theme.SPACE_XS
            width = max(width, fm.horizontalAdvance(text))
        if d.speed_lo and d.speed_hi:     # the line's key: the same ramp, its two measured ends
            fm = QFontMetrics(cap_f)
            base = min(y + theme.SPACE_M + fm.ascent(), h - pad - fm.descent())
            width = max(width, int(trace_paint.paint_speed_key(
                p, pad, base, d.speed_lo, d.speed_hi, cap_f, HERO_RAMP_PX)))
        return width

    def _paint_map(self, p: QPainter, d: HeroData, box: QRectF) -> None:
        """The best lap, north up, fitted to `box` with room for the labels; the focus corners
        ringed in the accent (the colour of the first Jump) and labelled on a plate."""
        self._rings = []
        ok = np.isfinite(d.xs) & np.isfinite(d.ys)
        if box.width() < 40 or box.height() < 40 or ok.sum() < 2:
            return
        # A label outside the outermost apex still fits: the gap to its centre plus half a plate's
        # height (a BODY line, ~14 px with its padding) — width is the looser axis here.
        fit = trace_paint.fit_box(d.xs, d.ys, box, HERO_LABEL_GAP_PX + 14)
        if fit is None:
            return
        # The line grows a little with the picture (4 px at the small window, ~6 px maximized).
        line_px = max(HERO_LINE_PX, min(HERO_LINE_PX * 1.6, box.height() / 90))
        # A canvas casing under the line keeps its colours crisp where the trace crosses itself.
        px, py = trace_paint.paint_trace(p, trace_paint.LapTrace(d.xs, d.ys, d.seg, "", ""), fit,
                                         line_px, casing=QColor(C.canvas), casing_px=3)
        placed: list[QRectF] = []
        if d.finish is not None:      # the live map's mark at its live size; labels keep clear
            at = fit.point(d.finish[0], d.finish[1])
            trace_paint.paint_finish_mark(p, at, d.finish[2], d.finish[3])
            placed.append(trace_paint.finish_mark_rect(at))
        mx, my = float(np.mean(px[ok])), float(np.mean(py[ok]))
        label_f = theme.ui_font(theme.BODY, theme.W_SEMIBOLD)
        fm = QFontMetrics(label_f)
        p.setFont(label_f)
        for cid in self.focus_cids():
            c = fit.point(*d.corners[cid])
            self._rings.append((cid, c))
            # A canvas-filled ring with a canvas rim: the line stops at it rather than crossing it.
            trace_paint.paint_ring(p, c, HERO_RING_PX, QColor(C.canvas), 5, 2.5)
            text = f"C{cid}"
            tw, th = fm.horizontalAdvance(text) + 2 * theme.SPACE_S, fm.height() + theme.SPACE_XS
            rect = _label_rect(c, math.atan2(c.y() - my, c.x() - mx), tw, th, placed, box)
            placed.append(rect)
            p.setPen(QPen(QColor(C.border_strong), theme.BORDER_PX))
            p.setBrush(QColor(C.surface))
            p.drawRoundedRect(rect, theme.RADIUS_S, theme.RADIUS_S)
            p.setPen(QColor(C.text))
            p.drawText(rect, Qt.AlignCenter, text)

    # ---------------------------------------------------------------- interaction
    def _target_at(self, pos) -> int | None:
        for cid, c in self._rings:
            if cid not in self._jumpable:
                continue
            # The ring plus a fingertip's slack: 6 px is the width of the ring's canvas rim and more.
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= HERO_RING_PX + 6:
                return cid
        return None

    def mouseMoveEvent(self, event):
        hit = self._target_at(event.position())
        self.setCursor(Qt.PointingHandCursor if hit is not None else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        hit = self._target_at(event.position())
        if hit is not None and event.button() == Qt.LeftButton:
            self.corner_clicked.emit(hit)
            return
        super().mousePressEvent(event)


# Human label per coaching.PHASE_* id, in track order (for the breakdown bar segments + tooltip).
_PHASE_LABEL = {coaching.PHASE_ENTRY: "Entry", coaching.PHASE_APEX: "Apex",
                coaching.PHASE_EXIT: "Exit"}

# A short, friendly per-reason hint shown as the row tooltip (the sentence already carries the
# numbers; this explains what the lever IS). Keyed by the coaching.REASON_* ids.
_REASON_TIP = {
    coaching.REASON_APEX: "Across your clean laps, the minimum (apex) speed here is typically "
                          "below your best lap's (the median over those laps) — carry more speed "
                          "through the slowest point.",
    coaching.REASON_BRAKING: "Across your clean laps, you typically spend longer on the brakes "
                             "into this corner than on your best lap (the median over those laps) "
                             "— brake later and/or release sooner.",
    coaching.REASON_COASTING: "Across your clean laps, you typically coast here (neither braking "
                              "nor on throttle) longer than your best lap does (the median over "
                              "those laps) — get back to throttle sooner.",
    coaching.REASON_LINE: "Across your clean laps, your best lap takes a different line here: a "
                          "slower apex than the laps' median and a faster exit — it gives up "
                          "minimum speed to get out quicker. Try its line.",
    # COACHING-5: this tip said "repeat the same line" under a reason nothing positional measured.
    coaching.REASON_CONSISTENCY: "The loss here is lap-to-lap spread, not one input: no brake, "
                                 "coast, apex or line difference explains it. The spread is the "
                                 "middle half of your clean laps through this corner.",
    coaching.REASON_NONE: "Time is available here versus your best lap.",
    coaching.REASON_LEVEL: "Time is available here versus your best lap, and no one input explains it: "
                           "the middle half of your clean laps through this corner is under the "
                           f"{coaching.LEVEL_STEP_S:.2f} s step Stats ▸ CORNERS calls level, so the "
                           "spread is not a cause to "
                           "work on either.",
}


class PhaseBar(QWidget):
    """A tiny horizontal entry/apex/exit Δt-profile for one corner (D2): three proportional
    segments (widths ∝ each third's |Δt| vs best) over the row's three numbers, each third the
    MEDIAN over the laps (`coaching.PhaseLoss`, COACHING-2). A WHERE-in-the-corner profile — NOT the
    row's "Time lost" (the median of the whole corner's time), which three medians need not sum to
    or even agree in sign with. Read-only; the segment widths are the visual cue, the small numbers
    underneath the precise values, the face the statistic's name, the tooltip the full breakdown.

    L5-05: a FASTER-than-best third is a real, readable state, not an absence of one. It used to
    render as a `C.border` sliver — 1.19:1 against the row, i.e. invisible — so a corner whose three
    thirds were ALL faster than best looked empty beside its "+0.08 s" Time lost, and only the
    tooltip reconciled the two measures. Faster thirds now take the palette's ahead colour, are
    sized by |Δt| like the losing ones, and a row whose every third is quicker says so on its face
    in the ahead hue."""

    #: The proportional bar's INK, not a gap — the same category as the scrollbar track's width or
    #: the slider groove's, which is why the spatial guard's stylesheet half deliberately leaves
    #: sub-control artwork sizes alone. It is the thickness of a drawn mark under two lines of
    #: CAPTION type; a spacing step would be choosing it for the wrong reason.
    _BAR_H = 6  # px; the proportional bar's height (the numbers sit below it)

    #: The face: the statistic's name, never a lap's (JOURNEY-8 asked whose number the bars are)
    #: and never a sum — a sum of three medians is neither a lap's net nor the row's Time lost.
    FACE = "median over laps"

    def __init__(self, phases: coaching.PhaseLoss, parent=None):
        super().__init__(parent)
        self._phases = phases
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        lay = QVBoxLayout(self)
        # ONE inset for a widget this small — SPACE_XS, the tightest gap between two separate
        # things. It shipped 6/4: two numbers for one box, and the 6 gave the bar 4 px less width to
        # divide between three proportional segments than the cell actually had.
        lay.setContentsMargins(theme.SPACE_XS, theme.SPACE_XS, theme.SPACE_XS, theme.SPACE_XS)
        # SPACE_XXS: the bar and the numbers under it are ONE element, which is the whole job the
        # sub-step exists for.
        lay.setSpacing(theme.SPACE_XXS)

        vals = phases.as_tuple()                      # (entry, apex, exit) seconds
        dominant = phases.dominant
        ids = (coaching.PHASE_ENTRY, coaching.PHASE_APEX, coaching.PHASE_EXIT)
        # L5-05: |Δt| sizes the bar, so a faster-than-best third is as visible as a losing one (the
        # old sum-of-losses scale gave an all-faster row three 1-px slivers and no readable state).
        mags = [abs(v) for v in vals]
        scale = sum(mags)

        # proportional bar
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        # NOT A SPACING. The 1 px between the three segments is a HAIRLINE — the thing that keeps
        # entry / apex / exit legible as three marks when two of them happen to take the same
        # colour — so it is the app's border weight wearing a layout's clothes, and it follows
        # BORDER_PX rather than the gap scale. SPACE_XXS here would be a 2 px gutter that reads as
        # three separate bars; a 0 would merge them.
        bar.setSpacing(theme.BORDER_PX)
        for pid, v, m in zip(ids, vals, mags, strict=True):
            seg = QWidget()
            seg.setFixedHeight(self._BAR_H)
            # stretch ∝ the third's |Δt|; a tiny floor so a flat row still shows three slivers
            bar.addWidget(seg, max(int(round(m / scale * 100)), 1) if scale > 1e-9 else 1)
            seg.setStyleSheet(f"background:{self._phase_colour(pid, v, dominant)}; "
                              "border-radius:2px;")
        lay.addLayout(bar)

        # the three numbers under the bar (the dominant loss accented, the faster thirds ahead-hued)
        nums = QHBoxLayout()
        nums.setContentsMargins(0, 0, 0, 0)
        nums.setSpacing(theme.SPACE_XS)
        num_font = theme.mono_font(theme.CAPTION)
        for pid, v in zip(ids, vals, strict=True):
            lbl = QLabel(fmt_signed(v, 2))
            lbl.setFont(num_font)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"color:{self._phase_colour(pid, v, dominant)};")
            nums.addWidget(lbl, 1)
        lay.addLayout(nums)

        # M4: the bar is a WHERE-in-the-corner profile, a DIFFERENT statistic from the row's "Time
        # lost". Since COACHING-2 each third is its own median over the laps, so there is no net
        # line any more: the thirds' sum is no lap's clock (it was the typical lap's net) and not
        # the Time lost beside it, and "net faster" would claim what nothing measured. The tooltip
        # says what the three are instead, and — L5-05 — states the all-quicker case in words.
        quicker = all(v < -1e-6 for v in vals)
        if any(v > 1e-6 for v in vals):
            state_line = f"Slowest third: {_PHASE_LABEL[dominant].lower()}."
        elif quicker:
            state_line = ("Every third's median is quicker than your best lap's — the row's Time "
                          "lost is the median of the whole corner's time, a different measure.")
        else:
            state_line = "On your best-lap pace in every third."

        # QA JOURNEY-8: "typical lap +0.12 s" beside a Time lost of "+0.15 s" read as one quantity
        # stated twice, so the face names whose numbers the bars are — the laps' medians, not a
        # lap, and with no number of its own to be read against Time lost. L5-05: a row whose every
        # third is quicker than best says so where it is read, in the palette's ahead hue.
        face = QLabel(self.FACE)
        face.setFont(theme.mono_font(theme.CAPTION))
        face.setAlignment(Qt.AlignCenter)
        face.setProperty("role", "Note")     # the muted default; the ahead case tints over it
        # A PER-DATUM semantic colour, so it stays a runtime merge over the role rather than a QSS
        # rule: ahead_colour() is a palette ACCESSOR and the stylesheet is built once at startup, so
        # a rule here would freeze this label in the standard green while every other ahead/behind
        # surface followed the colour-blind flip. One of the merges tests/test_inline_styles.py
        # lists by owner.
        if quicker:
            face.setStyleSheet(f"color:{theme.ahead_colour()};")
        lay.addWidget(face)

        self.setToolTip(
            "Where in the corner your laps are faster/slower than your best lap — per third, the "
            "median Δt over your clean laps (s). Each third is its own median, so the three need "
            "not add up to the row's Time lost:\n"
            + "   ".join(f"{_PHASE_LABEL[p]} {fmt_signed(v, 2)}"
                         for p, v in zip(ids, vals, strict=True))
            + "\n" + state_line)

    @staticmethod
    def _phase_colour(pid: str, v: float, dominant: str) -> str:
        """One third's colour: the palette's AHEAD hue when it is faster than best (L5-05 — a real
        state, not an absence of one), the accent for the dominant losing third, muted otherwise.
        Routes through theme.ahead_colour() so the colour-blind palette recolours it too, and the
        already-signed number under the bar keeps the cue non-colour."""
        if v < -1e-6:
            return theme.ahead_colour()
        if v > 1e-6 and pid == dominant:
            return C.accent
        return C.text_dim if abs(v) > 1e-6 else C.border


# L5-03: a QTableWidget wraps the "How to find it" cell NARROWER than it measures it. The row height
# comes from the delegate's sizeHint, which the stylesheet style computes at the FULL section width
# and then ADDS the QSS `QTableWidget::item {padding: 4px 8px}` to; the paint pass instead DEDUCTS
# that padding, so it wraps into 16 fewer px. At the app's own 1440x900 default that 16-px delta
# costs a whole line — a reason sentence advancing 309 px in a 317-px column is measured as one line
# and painted as two, and the row's "(est)" brake-point line is silently dropped, cell ending on a
# literal "…", with no user action at all. So measure each reason cell at the width the delegate
# really PAINTS into (the style's own SE_ItemViewItemText rect, QSS padding included) and pin the
# resulting height as the item's explicit sizeHint, which the delegate returns verbatim.
def _wire_reason_fit(table: QTableWidget, col: int):
    """Keep `col`'s wrapped rows fitted for the life of `table`, and fit them now.

    The reason column STRETCHES, and the header settles its final width *after* every signal we can
    hook: `sectionResized` stops firing partway (measured: last emission 189 px against a final
    445 px) and a build-time fit measures a width the table never uses. So re-fit from a coalesced
    queued call that lands once the layout pass is over, in addition to the immediate one that keeps
    headless callers (and the tests) correct without an event loop."""
    timer = QTimer(table)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: _fit_reason_rows(table, col))
    table.horizontalHeader().sectionResized.connect(lambda *_: timer.start(0))
    _fit_reason_rows(table, col)
    timer.start(0)


def _reason_text_box(table: QTableWidget, col: int, column_px: int) -> tuple[int, int]:
    """(width the glyphs are laid out in, vertical padding) for a `col` cell `column_px` wide —
    the rect the delegate PAINTS into, not the section's."""
    opt = QStyleOptionViewItem()
    opt.initFrom(table)
    opt.features = QStyleOptionViewItem.HasDisplay
    # The painter's own cell width, not the section's: the grid line lives inside the section and
    # is not paintable text (QTableView::visualRect is a pixel narrower than columnWidth for it).
    cell_w = column_px - (1 if table.showGrid() else 0)
    opt.rect = QRect(0, 0, cell_w, 100)
    text_rect = table.style().subElementRect(QStyle.SE_ItemViewItemText, opt, table)
    # ...and SE_ItemViewItemText is still NOT where the glyphs land. QCommonStyle's own
    # viewItemDrawText insets that rect by a further `PM_FocusFrameHMargin + 1` on EACH side before
    # laying the text out — 6 px here, which is not a rounding difference, it is a WHOLE LINE at the
    # widths this column runs at. Measured on the shipped page: a reason cell 376 px by this
    # function's old arithmetic is 370 px to the painter, and one of D24 0062's rows wraps to two
    # lines at 376 and three at 370 — so the row was pinned two lines tall and painted its third
    # line as "…", eating the sentence's last word with no user action at all. Same family as the
    # QSS-padding error the block above this function documents, one layer further in.
    inset = 2 * (table.style().pixelMetric(QStyle.PM_FocusFrameHMargin, opt, table) + 1)
    return text_rect.width() - inset, 100 - text_rect.height()


def _narrow_column_px(table: QTableWidget, col: int) -> int:
    """The width the stretching `col` has once the vertical scrollbar is showing: the viewport as if
    the bar were there, less every other visible column. Read this way, not off the section,
    because the section lags — Qt re-stretches it on its NEXT pass, so a fit run from the
    viewport's own resize read the width of the state just left, pinned rows for it, and toggled the
    bar back (measured: rows pinned for 250 px under a 262 px column and the reverse, alternating,
    for as long as the page was open). Pinned at the narrow width a row is at worst one line roomy
    while the bar is off, and never depends on whether it is."""
    bar = table.verticalScrollBar()
    others = sum(table.columnWidth(c) for c in range(table.columnCount())
                 if c != col and not table.isColumnHidden(c))
    return table.viewport().width() - (0 if bar.isVisible() else bar.sizeHint().width()) - others


def _fit_reason_rows(table: QTableWidget, col: int):
    """Re-height every wrapped reason cell in `col` from the rect the delegate paints into, then
    re-fit the rows. Idempotent — safe to call on every resize."""
    # Re-entrancy guard: re-fitting rows can toggle the vertical scrollbar, which re-stretches the
    # header, which calls back in here. One pass at a time; the next width change refits anyway.
    if table.property("_fitting_reason"):
        return
    avail, pad_v = _reason_text_box(table, col, _narrow_column_px(table, col))
    if avail <= 0:  # a collapsed column: leave Qt's own heights alone rather than pin nonsense
        return
    fm = table.fontMetrics()
    rows = [r for r in range(table.rowCount()) if table.item(r, col) is not None]
    table.setProperty("_fitting_reason", True)
    try:
        # Qt's own answer first (drop any hint pinned by a previous pass, so this shrinks again when
        # the column widens). We only ever GROW past it — the fix is the dropped line, not a
        # re-invention of the row metrics, and the pin must stay harmless where there is no
        # stylesheet padding to mis-measure.
        #
        # ASKED, NOT APPLIED. This read Qt's answer by applying it (a resizeRowsToContents between
        # clearing the pins and setting them again), and Qt under-measures these cells — so for a
        # moment the rows were shorter than they paint, which could hide the vertical scrollbar,
        # widen the column the `avail` above was measured at, and have the pins it then set shown
        # the bar again: one fit per toggle, forever, at a page height between the two totals. The
        # Coaching page's R11 columns (cell widgets built and dropped as the row count is tuned)
        # kept that ping-pong fed; sizeHintForRow is the same number without the transient.
        for r in rows:
            table.item(r, col).setData(Qt.SizeHintRole, None)
        floor = table.verticalHeader().minimumSectionSize()
        own = {r: max(table.sizeHintForRow(r), floor) for r in rows}
        for r in rows:
            item = table.item(r, col)
            wrapped = fm.boundingRect(QRect(0, 0, avail, 0), Qt.TextWordWrap, item.text()).height()
            # The width must be a REAL one: setSizeHint DISCARDS an invalid QSize (a -1 "don't care"
            # width clears the role instead of pinning the height). The section stretches, so the
            # width we pass never drives the layout.
            item.setSizeHint(QSize(table.columnWidth(col), max(wrapped + pad_v, own[r])))
        table.resizeRowsToContents()
    finally:
        table.setProperty("_fitting_reason", False)


# D4's noise floor for a braking DISTANCE: below this many metres a brake-point delta is within the
# estimate's own noise. No Coaching row prints braking metres since L7 (see `_reason_cell`); the
# constant stays because the refusal of a relative distance states its reopening bar in it
# (studio/docs/refused-2026-09.md §16) and that refusal's probe reads it.
BRAKE_HINT_MIN_M = 2.0


# --- shared per-row cell builders (the modal dialog AND the persistent panel render rows the SAME
# way, so the corner / time-lost / reason cells can't drift between the two surfaces). ---
def _corner_cell(opp: coaching.Opportunity) -> QTableWidgetItem:
    """The 'C<n>' corner cell (read-only), with the turn sense in the item's icon slot — the same
    lap_table.set_corner_direction the Corners and Stats ▸ CORNERS tables use."""
    item = QTableWidgetItem(f"C{opp.cid}")
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return set_corner_direction(item, opp.direction)


def _lost_cell(opp: coaching.Opportunity, num_font) -> QTableWidgetItem:
    """The '+<t> s' time-lost cell (right-aligned, red = time given away).

    An ABSTAINED row prints the same number in the muted tone instead of the delta hue: the
    measurement is still shown (never silently dropped), but it is no longer a claim, and a red
    "time given away" beside a "Not ranked:" sentence would say two opposite things in one row."""
    item = QTableWidgetItem(coaching.lost_text(opp))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setFont(num_font)
    item.setForeground(QColor(theme.delta_colour(opp.time_lost) if opp.evidence.ranked
                              else C.text_dim))
    return item


def _reach_tip(ev: coaching.Evidence, of: int | None) -> str:
    """The "Done it?" hover's count sentence. `of` is the session's clean-lap count
    (`Opportunities.n_laps`), or None from a caller that does not know it.

    SINCE #339 `ev.n_laps` IS NOT THE SESSION'S CLEAN LAPS. It counts the laps whose time through
    THIS corner counts — matched on track at its entry and exit, on the lap and on the best lap
    (`coaching.summarize`) — so on MK_18_09_26 C2 it is 16 of 19. This sentence said "3 of your 16
    clean laps" there, one line under a headline reading "median of 19 clean laps". Where the two
    differ it now says both, and why, in the words the Stats page's CORNERS hover uses for the same
    16 (`stats_corners._corner_count_tip`)."""
    # An ABSTAINED row is on the table too (ABSTAIN_FEW_LAPS: under MIN_CORNER_LAPS), so
    # `ev.n_laps` can be 1 here — and it is the subject of both "count"s below (K2).
    counts = "counts" if ev.n_laps == 1 else "count"
    if of is None or ev.n_laps >= of:
        return (f"{ev.reach_laps} of your {plural(ev.n_laps, 'clean lap')} already matched or "
                "beat your best lap's time through this corner.")
    return (f"{ev.reach_laps} of the {plural(ev.n_laps, 'lap')} that {counts} here already "
            f"matched or beat your best lap's time through this corner. {ev.n_laps} of your {of} "
            f"clean laps {counts}: on the other {of - ev.n_laps} the corner could not be matched "
            "to your best lap's line on track, so its time was interpolated between its neighbours "
            "and is left out, as the Stats page's CORNERS table leaves it out.")


def _reach_cell(opp: coaching.Opportunity, num_font, of: int | None = None) -> QTableWidgetItem:
    """"Yes · 9/38" — how many clean laps already matched this corner's target, and the word for it.
    `of` is the session's clean-lap count, for the hover (see `_reach_tip`).

    THIS REPLACED THE ±σ COLUMN, deliberately. σ was the raw dispersion printed for the reader to
    interpret, and on the real recordings interpreting it was the whole job: σ ≥ the row's own
    "Time lost" on 11 of the 12 shown rows across the two working-set recordings (worst 81.5x;
    coaching.py's evidence table, T16b), so the column
    that mattered most was the one asking for arithmetic. This states the conclusion instead —
    and states it as a COUNT OVER ITS DENOMINATOR, so it stays checkable. σ itself is not lost: the
    Stats ▸ CORNERS table has a σ column, and the Consistency panel ranks on it. (The spread
    reason's sentence — REASON_CONSISTENCY's since COACHING-5 — used to spell it too; since ADV-4
    it states the IQR its own evidence gate reads.)

    An unmeasured row (a legacy/synthetic Opportunity with no per-lap times) reads the em-dash
    rather than inventing a count."""
    ev = opp.evidence
    word = coaching.REACH_WORD.get(ev.reach)
    # THE COUNT, NOT THE WORD (LOOK-9): "Yes · 2/19" beside "Rarely · 1/19" was a verdict that
    # flipped at one lap (coaching.REACH_REPEAT_FRAC). The count carries it, and cannot flip.
    # With its unit, as the reason sentence says it — and as wide as the "Rarely · 1/8" it replaced:
    # a narrower cell let the column budget afford the Jump column at the 1440x900 default page,
    # which squeezed the reason to 193 px, grew the shortlist rows 58 -> 105 px and pushed the
    # focus line off the page (test_the_default_window_shows_the_top_three_whole).
    item = QTableWidgetItem(coaching.done_it_text(opp))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setFont(num_font)
    item.setForeground(QColor(C.text_dim))
    item.setToolTip(
        f"{_reach_tip(ev, of)}\nMany — you have the pace here and the work is repeating it. "
        "Few — you have rarely been this quick, and repeating your usual lap will not find it."
        if word else "Not measured for this row.")
    return item


def _p_phrase(p: float) -> str:
    """"p = 0.016" — or "p < 0.001", which a permutation p this small honestly is."""
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def _reason_cell(opp: coaching.Opportunity, directions: dict,
                 speed_unit: str | None = None, reach: bool = True) -> QTableWidgetItem:
    """The 'How to find it' reason cell: the coaching sentence (apex deficit in `speed_unit`, km/h
    default) + (where the laps separate one, corrected for every corner the recording tested)
    the MEASURED braking-direction line, with the per-reason tooltip.

    NO BRAKING METRES (L7). This cell used to end in the ESTIMATED "Brake ~N m later into Cx": a
    constant-peak-deceleration optimum that sat past the driver's braking at 33 of 33 corners on the
    working set by construction (coaching.BrakeDirection has the measurement). Its replacement says
    only which way braking went with quicker passes, with the lap count behind it, and says nothing
    at a corner whose laps do not separate a direction — so a missing line is a finding, not a gap.

    An ABSTAINED row shows `coaching.abstain_sentence` (which `reason_sentence` returns for it) and
    no braking line: a row that has just declined to make a claim does not grow a lever under it.

    `reach` False leaves off the "N of M laps matched your best lap here" count, for a row whose
    "Done it?" cell is on screen saying exactly that (QA JOURNEY-8: every row said it twice)."""
    text = coaching.reason_text(opp, directions, speed_unit, reach=reach)
    if not opp.evidence.ranked:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setForeground(QColor(C.text_dim))
        item.setToolTip(
            "Not ranked. Coaching abstains on this corner rather than offering a default: see the "
            "sentence for which evidence test it failed. The measurement is still shown.")
        return item
    d = directions.get(opp.cid)
    line = coaching.brake_direction_line(d)
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    tip = _REASON_TIP.get(opp.reason.kind, "")
    if line is not None and d is not None:
        # The count is the laps the rank test ran over, and they are not every clean lap: since
        # #339 `_brake_rows` drops a lap's brake point where that lap's corner was interpolated
        # (MK_18_09 C2: 16 counted, 18 braked). The sentence says so rather than a second count.
        rho = fmt_signed(d.rho, 2)   # the true minus the reason sentences print
        tip = (f"{tip}\n\n{line}: over the {d.n_laps} clean laps that braked into this corner and "
               "were matched on track at its entry and exit, the "
               f"{d.verdict} a lap began braking, the less time it took through the corner "
               f"(Spearman ρ {rho}, {_p_phrase(d.p)} against "
               f"{coaching.BRAKE_DIRECTION_DRAWS:,} random pairings of the same laps, and still "
               f"{_p_phrase(d.p_holm)} once corrected for all {d.family} "
               f"corner{'' if d.family == 1 else 's'} tested on this recording).\n"
               f"It {coaching.BRAKE_DIRECTION_CAVEAT}")
    item.setToolTip(tip)
    return item


# L5-06: the width below which the page stops paying for the "Done it?" column.
#
# What the panel does when it cannot give every column its content width: the corner and its loss
# are the row's identity and its headline number and always stay; the reason cell is the only column
# carrying PROSE and is the one that must wrap; "Done it?" is a glance cue whose content the reason
# sentence ALSO states in words ("You have already done this — 9 of your 38 laps matched it"), so it
# is the first to go and nothing is actually lost when it does. At the app's own minimum the three
# numeric columns held 198 of the 270 px the panel has and the reason fell back to its header's own
# 100-px size hint — overflowing the viewport into a horizontal scrollbar, over a table that already
# could not show one whole row.
REASON_MIN_PX = 180

# The columns the page gives up as it narrows, first to last. The Compare column goes first because
# it never takes the action away: without it each row's Compare sits under its Jump (ActionCell).
# The bars are a detail of the row (the reason sentence already names the dominant phase), Jump an
# action a row click half-does (it rings the corner), "Done it?" a count the sentence also states —
# see _apply_column_budget. The default docked page (515 px at 1440x900, 498 px at 1280x800)
# affords none of the actions, as before; from ~540 px Jump and the stacked Compare show.
_OPTIONAL_COLS = (_PANEL_COL_CMP, _PANEL_COL_PHASES, _PANEL_COL_GO, _PANEL_COL_REACH)
JUMP_MIN_PX = 88     # the Jump button's floor: its arrow + label at the app's button padding


# Each of the two stacked buttons' floor: a TABLE line and its padding. Stacked, they need a cell
# 2 x 22 + the 2 px gap tall, and a cell is its row less the 4 px inset above and below and the 1 px
# grid line (measured: a 48 px row holds a 39 px cell) — a row of 55 px, which a reason wrapped to
# three lines has and a one-line full-window row (48 px) has not (`_stack_fits`).
STACKED_BUTTON_MIN_PX = 22
STACKED_ROW_PX = 2 * STACKED_BUTTON_MIN_PX + theme.SPACE_XXS + 2 * theme.SPACE_XS + 1


class ActionCell(QWidget):
    """A row's Jump cell: the Jump, and — while the page has no room for the Compare column — the
    row's Compare stacked under it (cool/compare-at-corner). The Compare column costs 124 px, and a
    docked page that affords Jump (104 px) but not both still shows the action in the same cell: a
    row there wraps its reason to three lines or more, so the cell already has the height for two
    buttons. Alone, the Jump fills the cell exactly as it did when it was the cell widget itself.
    Tab order is the layout's: Jump, then Compare."""

    def __init__(self, jump: QPushButton, compare: QPushButton):
        super().__init__()
        self.jump, self.compare = jump, compare
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.SPACE_XXS)
        for btn in (jump, compare):
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            lay.addWidget(btn)
        compare.setVisible(False)

    def set_stacked(self, on: bool) -> None:
        """Show the stacked Compare (or hide it); the two share the cell's height."""
        for btn in (self.jump, self.compare):
            btn.setMinimumHeight(STACKED_BUTTON_MIN_PX if on else 0)
        if self.compare.isHidden() == on:
            self.compare.setVisible(on)
        self.updateGeometry()

    def is_stacked(self) -> bool:
        return not self.compare.isHidden()

    def sizeHint(self):
        """The height the row must give the cell: Jump's own while alone (the hint the row
        reserved when Jump was the cell widget), both floors while stacked — never the two
        buttons' full 31 px hints, so stacking does not by itself make a row taller."""
        hint = self.jump.sizeHint()
        if self.is_stacked():
            hint.setHeight(2 * STACKED_BUTTON_MIN_PX + theme.SPACE_XXS)
        return hint


def compare_status(tp: stats_service.TypicalPass) -> str:
    """What the window says when a row's Compare lands: the two passes, the corner's typical and
    its gap to the best lap's, every figure as printed (`MATRIX_DECIMALS`), so the gap is the
    subtraction of the two figures beside it — and the row's Time lost (`TypicalPass`).

    HONEST ABOUT WHICH LAP. Pane A's lap is the one whose pass at THIS corner is the typical one
    (`stats.typical_pass_lap`), not the session's median lap, and the typical itself is printed:
    over an even count no lap prints it (the demo's C1: lap 4 3.39 s, typical 3.38 s)."""
    a, b = lap_label(tp.lap_id), lap_label(tp.best_lap_id)
    d = stats_service.MATRIX_DECIMALS
    return (f"C{tp.cid} · lap {a} {tp.pass_s:.{d}f} s vs lap {b} {tp.best_pass_s:.{d}f} s "
            f"(best lap) — lap {a}'s is your {stats_service.TYPICAL_PASS} here (median of {tp.n}: "
            f"{tp.typical_s:.{d}f} s, "
            f"{stats_service.fmt_pass_vs_typical(tp.typical_vs_best_s)} on the best lap's)")


def compare_tooltip(cid: int, tp: stats_service.TypicalPass | None) -> str:
    """A row's Compare hover: what the click will show, or why it is disabled."""
    if tp is None:
        return (f"No {stats_service.TYPICAL_PASS} to compare at C{cid}: that needs at least "
                f"{stats_service.MATRIX_MIN_LAPS} passes matched on track at both edges of the "
                "corner, your best lap's among them.")
    a, b = lap_label(tp.lap_id), lap_label(tp.best_lap_id)
    d = stats_service.MATRIX_DECIMALS
    return (f"Compare your {stats_service.TYPICAL_PASS} at C{cid} with your best lap's: lap {a} "
            f"({tp.pass_s:.{d}f} s) beside lap {b} ({tp.best_pass_s:.{d}f} s), both videos at the "
            "corner's entry and the charts on the corner.\n"
            f"Lap {a} is the lap whose pass here is nearest the median of {tp.n} "
            f"({tp.typical_s:.{d}f} s) — not your median lap.\n"
            "Like C, it ends a Lap race; C again, or picking a lap, ends the compare.")

# What the headline strip's hover says about the page itself. Lives beside PANEL_TOP_N because it
# quotes it; the live theme + actions are prepended to it (see _refresh_summary_label).
# Its scope sentences are `coaching.scope_note`'s, which the exported report prints too.
_SCOPE_FULL_WINDOW = (" Coaching ▸ Opportunities (or the panel's maximize button) shows "
                      "this page full-window, where "
                      "each row also carries where in the corner the time goes and a Jump to it.")


# The total it describes is the headline's shortlist, counted as the headline counts it.
def scope_tooltip(n: int = PANEL_TOP_N) -> str:
    """The strip's scope hover over a headline summing `n` ranked corners (`coaching.scope_note`)."""
    return coaching.scope_note(n=n) + _SCOPE_FULL_WINDOW


_SCOPE_TOOLTIP = scope_tooltip()


def more_rows_hint(hidden: list) -> str:
    """The grid page's line for the ranking rows it has no room for (LOOK-3): how many, whether
    Coaching ranked them, and the gesture that shows them. "" when nothing is hidden."""
    if not hidden:
        return ""
    ranked = sum(1 for r in hidden if getattr(getattr(r, "evidence", None), "ranked", True))
    rest = len(hidden) - ranked
    n = len(hidden)
    if not ranked:
        what = (f"{n} more corner{'' if n == 1 else 's'} "
                f"{'is' if n == 1 else 'are'} listed but not ranked")
    elif not rest:
        what = f"{n} more ranked corner{'' if n == 1 else 's'}"
    else:
        what = f"{n} more corners: {ranked} ranked, {rest} listed but not ranked"
    return (f"{what} — the panel's maximize button, or Opportunities in the Coaching menu, "
            f"shows {'it' if n == 1 else 'them'}.")


class OpportunitiesPanel(QWidget):
    """The Coaching page of the lap panel's tab stack: the session THEME, then the ranked
    opportunities (corner · time lost · done-it? · dominant reason) over a freshly computed
    ``coaching.Opportunities``, at the panel's FULL height — the full reason sentences get room to
    breathe (this replaced the old capped under-table strip whose whole drag range was 68 px).
    Full-window (Coaching ▸ Opportunities, or the maximize button) each row also shows WHERE in the corner
    the time goes and a Jump to it — the two columns of the modal this page replaced.

    THEME FIRST, ROWS AS THE DRILL-DOWN (Part 3). ``ThemeBlock`` states one clustered story and at
    most two actions above the table; the per-corner list under it is the detail behind them.

    ABSTAINED ROWS ARE SHOWN, NOT DROPPED. ``summarize`` sinks the corners that failed the evidence
    gate below the ranked ones and they render muted, with the sentence saying which test they
    failed and no braking line — the collapse-to-a-default ("brake 8 m later") is exactly what the
    gate exists to prevent. Only the RANKED rows are summed into the headline total.

    RESPONSIVE, in both directions (L5-06/L5-08). The page shows ``PANEL_TOP_N`` rows as its floor
    and then as many further ranked corners as the viewport can hold — maximized it used to be 3
    rows in 808 px (78 % dead canvas re-measured after #B23 grew the rows; the sweep filed 83 %)
    while the model had 11 corners ranked and the modal fitted all 11 in a third of the area.
    Narrow, the Entry·Apex·Exit bars, then the Jump buttons, then the "Done it?" column drop out
    before the reason prose is squeezed below ``REASON_MIN_PX`` (``_OPTIONAL_COLS``; a row click
    still rings the corner on the map, and maximizing brings them back) and the reason header elides into the
    width the style paints into, so
    the app's own minimum window no longer raises a horizontal scrollbar over a clipped header. The
    HEADLINE still sums the ``PANEL_TOP_N`` shortlist and names that count ("across your top 3
    corners"), because the Stats page's digest tile states the same total from the same constant.

    SCOPE — WHOLE SESSION, NOT THE SELECTED LAP (IA-01). ``coaching_opportunities()`` takes no lap:
    every row is the MEDIAN loss vs best over the clean laps, and its reason, its IQR and its
    Entry·Apex·Exit bars are medians or spreads over the same laps — none of which a single lap can
    answer. Its sibling Corners tab IS
    the per-lap surface (it renames itself "Corners · L6"), so this page must SAY it does not follow
    the selection rather than look like it silently failed to: the headline leads with the scope and
    the tab tooltip names it. Do not wire this to ``laps_selected`` — ``refresh()`` recomputes the
    identical session statistic, so that would repaint the same pixels and change nothing.

    THE DEBRIEF IS THIS PAGE, NOT A SIXTH ONE (board review PS-B1, ``set_debrief``). A recording's
    first open lands here full-window with a lead above the focus list (``DebriefBlock``) and the
    table cut to the shortlist the headline sums — one time figure and its corners, each with its
    Jump. The rows carry their measured reason and nothing under it: the braking-direction line
    joins them the moment the debrief ends. The ESTIMATED "brake ~N m later" line it replaced was
    never on the debrief — it read "later" at 33 of 33 corners on the four working-set recordings
    (UX-2), no relative figure that would say where survived measurement (refused-2026-09.md §16),
    and whatever a landing leads with is read literally. L7 took it off the ordinary page too.

    Reads ONLY session accessors (``coaching_opportunities`` + ``coaching_brake_direction``) — no
    analysis here. Refreshed on load / re-segmentation / unit + palette change (never on the 30 Hz
    tick, never on selection — see the scope note).
    A row click emits ``corner_clicked(cid)`` so the app can ring the corner's apex on the map.
    Carries no estimated figure, and shows the friendly "need more laps" state when there aren't
    enough clean laps."""

    # Clicked corner cid (None on deselect) -> the map apex-ring highlight (wired in central_view).
    corner_clicked = Signal(object)
    # The focus list's two gestures, forwarded from the block: the app owns the store, so this page
    # asks rather than writes (the same split `CentralView.set_session_record` uses).
    focus_add_requested = Signal(int)
    focus_remove_requested = Signal(int)
    focus_mark_dry_requested = Signal(list)
    focus_replace_requested = Signal(list)
    # A row's Jump: (cid, the corner's entry odometer on the best lap). The app selects the corner
    # and seeks the video to the best lap's entry to it (StudioWindow._jump_to_opportunity).
    jump_requested = Signal(int, float)
    # The debrief's "Compare with your previous PB" (DebriefBlock); the window does the loading.
    compare_pb_requested = Signal()
    # The focus list's one line was clicked: show the list whole (the window's Show focus list).
    focus_show_requested = Signal()

    # A row's Compare: the corner's typical pass against the best lap's (a stats.TypicalPass), at
    # the corner — CentralView.compare_at_corner. Never on the debrief (RUL-7: no lap-first
    # buttons there), where the column stays hidden.
    compare_requested = Signal(object)

    _COLUMNS = ["Corner", "Time lost", "Done it?", "How to find it", PHASE_HEADER, "", ""]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._num_font = theme.mono_font(theme.TABLE)
        self._cids: list[int] = []  # row -> corner cid, set in refresh()
        # L5-08: the WHOLE shown ranking (the table renders as many of these as it can hold) + the
        # braking directions its reason cells need, so a re-tune re-renders without re-reading the
        # session.
        self._all_rows: list[coaching.Opportunity] = []
        self._shortlist: list[coaching.Opportunity] = []   # what the headline sums (PANEL_TOP_N)
        self._debrief = False        # the page is the first-open debrief (set_debrief)
        # (PB line, the corners Pacer pre-promoted, whether the line offers the PB compare, whether
        # the list is saved — False only on the synthetic demo)
        self._debrief_lead: tuple = (None, [], False, True)
        self._brake_dirs: dict = {}  # cid -> coaching.BrakeDirection (refresh)
        self._n_clean: int | None = None  # the session's clean laps, for the "Done it?" hover
        self._tuning = False         # re-entrancy guard: a re-render fires resizeEvent
        self._tuned_key: tuple | None = None   # the viewport the current row count was tuned for
        self._budgeting = False        # re-entrancy guard: hiding a column fires resizeEvent
        self._theme_budgeting = False  # ditto: shedding a theme line re-lays the page out
        # The last width each optional column had, so the budget can cost one while it is hidden.
        # The bars and the Jump column are fixed widths; "Done it?" sizes to its content.
        self._col_px = {_PANEL_COL_PHASES: PHASE_COL_PX, _PANEL_COL_REACH: 0,
                        _PANEL_COL_GO: self._go_column_px(),
                        _PANEL_COL_CMP: self._cmp_column_px()}
        # cid -> the corner's typical pass beside the best lap's (refresh), for the rows' Compare.
        self._typical: dict = {}
        self._stacked = False   # each row's Compare sits under its Jump (`_sync_stacked`)
        self._stack_ok = False  # ...which every shown row is tall enough for (the column budget)
        # The headline (e.g. "0.60 s across your top 3 corners") — the page's one-line framing.
        self._headline = ""
        # Speed display unit (km/h default) for the reason sentence's apex deficit; pushed by the
        # window's Units toggle via set_speed_unit.
        self._speed_unit = units.DEFAULT_UNIT

        # --- the headline strip: the tab bar already names the page, so this is just the
        # summary sentence (no title, no chevron — a tab you leave costs nothing).
        self.summary_label = QLabel("")  # "Whole session · 0.42 s in 3 corners …" — set in refresh()
        self.summary_label.setProperty("role", "BarLabel")
        self.summary_label.setToolTip(_SCOPE_TOOLTIP)
        # The same PanelHeader the four quadrants use. This strip was a byte-identical copy of
        # CentralView._header_bar — same (8, 4, 8, 4) margins, same spacing — which is how the
        # Coaching page came to sit under a header of a DIFFERENT height from the tab bar directly
        # above it. There is one header now and it declares its height, so this page's strip and the
        # panel header it lives under can no longer drift apart.
        header = PanelHeader(self.summary_label)
        self._header = header

        # --- body: a stack of {top-3 table, friendly "need more laps" label}, swapped in refresh().
        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        # The corner-direction arrow paints at the app's ICON_PX, not the style's PM_SmallIconSize.
        self.table.setIconSize(QSize(theme.ICON_PX, theme.ICON_PX))
        # Let each row grow to fit its wrapped "How to find it" cell instead of a fixed 34-px row
        # that clips a 2nd line at a narrow panel width (the ellipsis-truncation bug). As a full
        # tab page there is normally room for all rows; the table scrolls only when the panel is
        # dragged very short.
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        hdr = self.table.horizontalHeader()
        # The reason takes the slack; corner · time-lost · done-it? · Jump size to their content and
        # the bars keep a stable width (their segments are shares of it).
        hdr.setSectionResizeMode(_PANEL_COL_REASON, QHeaderView.Stretch)
        for col in (_COL_CORNER, _COL_LOST, _PANEL_COL_REACH):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # §6.4, carried over from the modal: ResizeToContents sizes a column from the cell widget's
        # HINT and knows nothing about the inset the view then paints the widget INSIDE, so every
        # Jump was flat-cut on its right edge. The modal asked the painter once, at build; the page
        # re-budgets on every resize, where asking again would ADD the inset again. So the column
        # is a fixed width: the button's own floor plus that inset on each side.
        for col, px in ((_PANEL_COL_PHASES, PHASE_COL_PX), (_PANEL_COL_GO, self._go_column_px()),
                        (_PANEL_COL_CMP, self._cmp_column_px())):
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, px)
        # L5-06/L5-08: headers over their own columns, with tooltips, and the padding budget the
        # reason header is elided against (measured now, while it still carries its full label).
        _style_headers(self.table, self._COLUMNS)
        self._reason_chrome = _header_chrome_px(self.table, _PANEL_COL_REASON,
                                                self._COLUMNS[_PANEL_COL_REASON])
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        # L5-03: keep the wrapped reason rows fitted to the width the delegate really paints into,
        # re-measured whenever the header re-stretches the column.
        _wire_reason_fit(self.table, _PANEL_COL_REASON)
        # Showing or hiding a column re-stretches the reason section only on Qt's NEXT pass, after
        # the pass that toggled it has fitted rows to the old width and counted how many fit.
        # Measured: the first full-window pass saw 105-px rows (the reason squeezed by bars not yet
        # re-stretched around) and stopped at 6 of 11 rows that settle at 48 px. So a pass that
        # changes a column's visibility lays the page out once more, next pass, when the width is
        # real (_apply_column_budget). Only a CHANGE schedules it, so it cannot feed itself.
        self._restretch = QTimer(self)
        self._restretch.setSingleShot(True)
        self._restretch.timeout.connect(self._retune)
        # L5-06/L5-08: re-budget and re-tune off the TABLE VIEWPORT's own resize, not the panel's.
        # `QWidget.resize` delivers our resizeEvent before the child layout has been applied, so a
        # budget computed there measures the width the table is about to stop having (measured: the
        # panel goes to 280 px while `viewport().width()` still reads 376). The viewport's resize is
        # the event that means "the columns now have this much room".
        self.table.viewport().installEventFilter(self)

        # The app's ONE empty-state object, owning the pane — which is the whole of QA D2-07. This
        # page's state was a bare `role="Note"` label on the window CANVAS while its three sibling
        # tabs, in the SAME rectangle ([0, 461, 515, 417] measured), showed a `C.surface` card: the
        # panel changed colour when you switched tab, and nothing in either file said so.
        self.empty_state = EmptyState("")

        self.body = QStackedWidget()
        self.body.addWidget(self.table)        # index 0 — the top-3 rows
        self.body.addWidget(self.empty_state)  # index 1 — the friendly excluded state

        # Part 3: the theme + at most two actions, ABOVE the table — the page leads with one story
        # and the per-corner rows are its drill-down. It hides itself when nothing is ranked, so
        # the "need more laps" state below is untouched.
        self.theme_block = ThemeBlock()

        # The training loop, above the theme: what the driver put on their focus list last time and
        # whether it moved. DORMANT until the app hands it a report (`set_focus_report`) — a panel
        # built without one is byte-for-byte the page it was before, which is what keeps the
        # app-support stores out of this widget and out of its tests.
        self.focus_block = FocusBlock()
        self.focus_block.add_requested.connect(self.focus_add_requested)
        self.focus_block.remove_requested.connect(self.focus_remove_requested)
        self.focus_block.mark_dry_requested.connect(self.focus_mark_dry_requested)
        self.focus_block.replace_requested.connect(self.focus_replace_requested)
        # THE LIST KEEPS ONE LINE WHEN THE BLOCK HAS NO ROOM (QA JOURNEY-1). The shortlist is
        # reserved first, so at the owner's grid (a 719x222 page, the top three rows already short
        # of room) the block's budget is 0 and his list — and the "Both dry?" waiting in it — was
        # simply not on the page he lands on after the debrief. This row names the corners and the
        # pending question, and its click shows the block whole (`focus_show_requested`).
        self.focus_line = ActionChip("")
        self.focus_line.setIcon(theme.icon(FOCUS_LINE_GLYPH, color=C.text_dim))
        self.focus_line.setIconSize(QSize(theme.ICON_PX, theme.ICON_PX))
        self.focus_line.setToolTip(FOCUS_LINE_TIP)
        self.focus_line.clicked.connect(self.focus_show_requested)
        line_row = QHBoxLayout()
        line_row.setContentsMargins(theme.SPACE_M, theme.SPACE_XXS, theme.SPACE_M, theme.SPACE_XXS)
        line_row.addWidget(self.focus_line)
        line_row.addStretch(1)
        self._focus_line_row = QWidget()
        self._focus_line_row.setLayout(line_row)
        self._focus_line_row.setVisible(False)
        # The debrief's lead, above everything it introduces; hidden unless the page IS the debrief.
        self.debrief_block = DebriefBlock()
        self.debrief_block.compare_requested.connect(self.compare_pb_requested)
        # The debrief's picture, in the canvas the shortlist leaves under it (`_apply_hero_budget`);
        # hidden, and costing nothing, everywhere else.
        self.hero = DebriefHero()
        self.hero.corner_clicked.connect(self._jump_to_corner)
        self._hero_budgeting = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.debrief_block)
        lay.addWidget(self.focus_block)
        lay.addWidget(self._focus_line_row)   # in the block's place, only while the block is hidden
        lay.addWidget(self.theme_block)
        lay.addWidget(self.body, 1)  # the rows take the page's full height
        lay.addWidget(self.hero, 0)  # ...except on the debrief, where the hero takes what they leave
        self._lay = lay
        # THE ROWS THE VIEWPORT COULD NOT HOLD, SAID (LOOK-3, QA 2026-09-26). In the grid the page
        # fits what it can — 3 rows at the owner's layout — and the rest simply were not there: on
        # MK_18_09 8 corners, C7's +0.25 s (the largest loss against his best lap) among them, were
        # listed only once the page was maximized, and nothing in the grid said so. One line under
        # the table now counts them and names the gesture that shows them (`_sync_more_hint`).
        self.more_hint = QLabel("")
        self.more_hint.setProperty("role", "Note")
        self.more_hint.setWordWrap(True)
        self.more_hint.setContentsMargins(theme.SPACE_S, 2, theme.SPACE_S, 2)
        self.more_hint.setVisible(False)
        lay.addWidget(self.more_hint)
        self.refresh()

    def set_focus_report(self, report: focus.Report | None) -> None:
        """Show the focus list and this session's verdict on it (the app builds the report from the
        store + ``Session.focus_report``). Passing an empty report shows the one-line invitation;
        never calling this at all leaves the page exactly as it was before the feature."""
        self.focus_block.set_report(report, self.shortlist_cids())
        self.focus_block.set_selected_corner(self._selected_cid())
        self.hero.set_focus(self.focus_block.cids())   # a Remove re-rings the picture too
        self._sync_debrief_lead()
        self._relayout()
        self._refresh_summary_label()

    def _sync_debrief_lead(self) -> None:
        """The lead names only the pre-promoted corners STILL on the list: a Remove below it must
        not leave it saying Pacer put a corner there that is gone."""
        if self._debrief:
            pb_line, promoted, compare, saved = self._debrief_lead
            kept = [c for c in promoted if c in self.focus_block.cids()]
            self.debrief_block.set_lines(pb_line, debrief_note(kept, saved), compare)

    def shortlist_cids(self) -> list[int]:
        """The corners the headline sums, in rank order — what the debrief pre-promotes, so the
        focus list it fills is exactly the "top N" the page names. Empty when nothing is ranked."""
        return [r.cid for r in self._shortlist]

    def set_debrief(self, on: bool, pb_line: str | None = None,
                    promoted: list[int] | None = None, compare: bool = False,
                    saved: bool = True) -> None:
        """Make this page the first-open debrief (see the class note), or the ordinary page again.
        The view owns WHEN, and is this method's only caller in studio/ (``CentralView.show_debrief``
        / ``_end_debrief`` set on/off; ``update_debrief_lead`` re-leads an open one), so the page is
        never the debrief while the view thinks it is not (FIRST-OPEN-LOOP-9); this owns what the
        page shows: the lead, the shortlist-only table, and no braking line. `compare` offers
        "Compare with your previous PB" beside the PB line (``compare_pb_requested``); `saved`
        False words the note for a list that is never written (the demo's, ``debrief_note``)."""
        self._debrief = bool(on)
        # The reason header's hover follows the mode: the debrief's cells carry no braking line.
        self.table.setProperty("debrief", self._debrief)
        label = self._COLUMNS[_PANEL_COL_REASON]
        head = self.table.horizontalHeaderItem(_PANEL_COL_REASON)
        tip = _header_tip(self.table, label)
        head.setToolTip(tip if head.text() == label else f"{label} — {tip}")   # as _elide_header
        self._debrief_lead = ((pb_line, list(promoted or []), bool(compare), bool(saved)) if on
                              else (None, [], False, True))
        if on:
            self._sync_debrief_lead()
        else:
            self.debrief_block.set_lines(None, None)
        self._load_hero()
        if self._all_rows:
            self._tuned_key = None
            self._render_rows(min(PANEL_TOP_N, self._row_limit()), keep_selection=False,
                              rebuild=True)
        self._relayout()
        self._refresh_summary_label()

    def is_debrief(self) -> bool:
        return self._debrief

    def _row_limit(self) -> int:
        """How many rows the table may grow to: the whole shown ranking, or, on the debrief, the
        shortlist the headline sums (ranked rows lead the order, so it is the first rows)."""
        return len(self._shortlist) if self._debrief else len(self._all_rows)

    # ------------------------------------------------------------------ build
    def refresh(self):
        """Recompute the opportunities from the session and rebuild the top-3 rows (or the friendly
        excluded state). Called on load / re-segmentation / unit + palette change — never on the
        30 Hz tick, and never on a lap selection (the summary is session-scoped; see the class note).
        Clears any held row selection (a stale cid would mis-ring the map)."""
        opps = self.session.coaching_opportunities()
        directions = self.session.coaching_brake_direction()
        self.theme_block.set_theme(opps)
        # L2: only rows above the shown resolution count as opportunities (no "+0.00 s" rows).
        if opps.enough and coaching.shown_rows(opps):
            self._fill_rows(opps, directions)
        else:
            self._show_excluded(opps)
        # A new theme is new prose of a new length, so re-run its height budget against the page
        # the panel currently has (the resize path re-runs it whenever that page changes).
        self._apply_theme_budget()
        if self._debrief:        # a re-segmentation or a unit change while the debrief is up
            self._load_hero()
            self._apply_hero_budget()

    def set_speed_unit(self, unit: str):
        """Switch the reason sentence's apex-deficit unit live: re-fill the rows. No-op if
        unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.refresh()

    def _fill_rows(self, opps: coaching.Opportunities, directions: dict):
        """Populate the table from `opps.rows` (shared cell builders, so a row reads identically to
        the modal dialog) and the headline summary."""
        # L2: only shown-resolution rows are opportunities (drop the "+0.00 s" rows).
        self._all_rows = coaching.shown_rows(opps)
        self._brake_dirs = directions
        self._n_clean = opps.n_laps
        self._tuned_key = None       # a new ranking: re-tune the row count against the viewport
        # The headline sums THE shortlist — the top ranked rows (`coaching.shortlist`) — and its
        # words, the sum of the printed cells and the scope it leads with, are `coaching.headline`'s
        # (one author, so the exported report prints the same line).
        self._shortlist = coaching.shortlist(opps)
        self._headline = coaching.headline(opps)
        # getattr-guarded as compare_controller's duck-typed reads are: a stand-in session with no
        # corner grid offers no typical pass, so every Compare is disabled and says why.
        typical = getattr(self.session, "typical_passes", None)
        self._typical = typical() if callable(typical) else {}
        self._refresh_summary_label()

        # A refresh is new data / a new unit / a new palette, so every cell is rebuilt. The row
        # COUNT is the page's floor (the tune loop below grows it to the viewport); it is NOT the
        # headline shortlist's length, which counts only ranked rows (except on the debrief, whose
        # table IS that shortlist — _row_limit).
        self._render_rows(min(PANEL_TOP_N, self._row_limit()),
                          keep_selection=False, rebuild=True)
        self._apply_column_budget()
        self._tune_rows()

    def _render_rows(self, n: int, keep_selection: bool = True, rebuild: bool = False):
        """Show the first `n` ranked rows (shared cell builders) and re-fit them.

        L5-08: the row COUNT is viewport-driven, so this runs on a resize as well as on a refresh.
        It keeps the ringed corner selected across a re-render (and, when that corner falls off the
        end of a shrunk table, clears the map ring exactly once rather than leaving it stale), and
        it only BUILDS the rows that are new — the tune loop calls this several times per resize and
        rebuilding 11 wrapped reason cells each pass costs an order of magnitude more than the
        gesture is worth. `rebuild=True` (a refresh: new data, new unit, new palette) rebuilds all
        of them."""
        rows = self._all_rows[:max(n, 0)]
        held = self._selected_cid() if keep_selection else None
        self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            if rebuild:
                self.table.setRowCount(0)
            built = self.table.rowCount()   # rows already on the table keep their cells
            self.table.setRowCount(len(rows))
            self._cids = [opp.cid for opp in rows]
            # The debrief leaves the braking-direction line off (see the class note).
            directions = {} if self._debrief else self._brake_dirs
            for r in range(built, len(rows)):
                opp = rows[r]
                self.table.setItem(r, 0, _corner_cell(opp))
                self.table.setItem(r, 1, _lost_cell(opp, self._num_font))
                self.table.setItem(r, 2, _reach_cell(opp, self._num_font,  # have you done it?
                                                     self._n_clean))
                self.table.setItem(r, 3, self._reason_item(opp, directions))
                self.table.setCellWidget(r, _PANEL_COL_PHASES,  # D2
                                         PhaseBar(opp.phases))
                self.table.setCellWidget(r, _PANEL_COL_GO, self._action_cell(opp, primary=r == 0))
                self.table.setCellWidget(r, _PANEL_COL_CMP, self._cmp_button(opp))
            if held is not None and held in self._cids:
                self.table.selectRow(self._cids.index(held))
        finally:
            self.table.blockSignals(False)
        # Grow each row to its wrapped-reason height for the current column widths (the reason is the
        # stretch column, so its width — and thus the wrap — depends on the panel's live size).
        _fit_reason_rows(self.table, _PANEL_COL_REASON)
        self.body.setCurrentIndex(0)
        if held is not None and held not in self._cids:
            self.corner_clicked.emit(None)

    def _reason_item(self, opp: coaching.Opportunity, directions: dict) -> QTableWidgetItem:
        """One row's reason cell, carrying the reach count only while "Done it?" is off screen: the
        count is said once, by whichever of the two the page shows (QA JOURNEY-8)."""
        return _reason_cell(opp, directions, self._speed_unit,
                            reach=self.table.isColumnHidden(_PANEL_COL_REACH))

    def _refill_reasons(self) -> None:
        """Rebuild the reason cells in place after "Done it?" came or went, then re-fit the rows."""
        directions = {} if self._debrief else self._brake_dirs
        self.table.blockSignals(True)
        try:
            for r, opp in enumerate(self._all_rows[:self.table.rowCount()]):
                self.table.setItem(r, _PANEL_COL_REASON, self._reason_item(opp, directions))
        finally:
            self.table.blockSignals(False)
        _fit_reason_rows(self.table, _PANEL_COL_REASON)

    def _selected_cid(self):
        """The currently ringed corner's cid, or None."""
        rows = self.table.selectionModel().selectedRows()
        if rows and 0 <= rows[0].row() < len(self._cids):
            return self._cids[rows[0].row()]
        return None

    # ------------------------------------------------------------- responsive layout
    def _apply_column_budget(self):
        """Spend the panel's width on the column that carries the prose (L5-06).

        Shows the optional columns, most essential first ("Done it?", then Jump, then the
        Entry·Apex·Exit bars — ``_OPTIONAL_COLS`` read backwards), while the reason cell keeps at
        least ``REASON_MIN_PX``; then elides the reason header into the width the style really paints
        into. At the app's own minimum this takes the reason column from its header's 100-px fallback
        to the 128 px actually left over, retires the horizontal scrollbar the overflow raised, and
        stops "How to find it" painting as a clipped "How to find". Nothing is lost when a column
        goes: when "Done it?" goes, its count moves into the reason sentence ("9 of 38 laps matched
        your best lap here", said once either way: `_reason_item`), a row click still rings the
        corner on the map, and full-window every column is back.

        The room is measured as if the vertical scrollbar were showing, for the reason
        ``_shortlist_px`` gives: a threshold the bar's own 12 px can cross toggles a column every
        time the bar does."""
        if self._budgeting:
            return
        t = self.table
        self._budgeting = True
        try:
            for col in _OPTIONAL_COLS:
                if not t.isColumnHidden(col) and t.columnWidth(col):
                    # Remember what each column costs, so the budget can price it while hidden.
                    self._col_px[col] = t.columnWidth(col)
            bar = t.verticalScrollBar()
            room = (t.viewport().width() - (0 if bar.isVisible() else bar.sizeHint().width())
                    - t.columnWidth(_COL_CORNER) - t.columnWidth(_COL_LOST))
            shown = set()
            for col in reversed(_OPTIONAL_COLS):
                if col == _PANEL_COL_CMP:
                    continue        # decided last, below: every other column is decided as before
                if room - self._col_px[col] < REASON_MIN_PX:
                    break           # strict priority: never a lesser column in a greater one's place
                shown.add(col)
                room -= self._col_px[col]
            # The Compare column only where a row's Compare cannot sit under its Jump (ActionCell):
            # where some row is too short for two buttons (one-line rows, full-window). Never on the
            # debrief (compare_requested). Stacked, a docked page keeps main's row heights exactly;
            # the column there took the demo's rows from 58 to 89 px at a 676 px page.
            # Where neither fits (rows too short to stack, no room for the column) the row keeps
            # its Jump alone, as on main: buttons squeezed under 22 px clip their descenders.
            self._stack_ok = (_PANEL_COL_GO in shown and not self._debrief
                              and self._stack_fits(room))
            if (_PANEL_COL_GO in shown and not self._debrief and not self._stack_ok
                    and room - self._col_px[_PANEL_COL_CMP] >= REASON_MIN_PX):
                shown.add(_PANEL_COL_CMP)
            for col in _OPTIONAL_COLS:
                if (col not in shown) != t.isColumnHidden(col):
                    t.setColumnHidden(col, col not in shown)
                    self._restretch.start(0)
                    if col == _PANEL_COL_REACH:     # the count moves into / out of the reason
                        self._refill_reasons()
            self._sync_stacked()
            _elide_header(t, _PANEL_COL_REASON, self._COLUMNS[_PANEL_COL_REASON],
                          self._reason_chrome)
        finally:
            self._budgeting = False

    def _go_column_px(self) -> int:
        """The Jump column's width: the button's own width (never under its floor) and the cell's
        inset on both sides — `theme.SPACE_S`, the gap a cell keeps around its content."""
        return max(self._go_button(None).sizeHint().width(), JUMP_MIN_PX) + 2 * theme.SPACE_S

    def _go_button(self, opp: coaching.Opportunity | None, primary: bool = True) -> QPushButton:
        """One row's Jump: emits ``jump_requested(cid, entry_dist)``. With no row it is the template
        the column budget measures before any row exists.

        ONE PRIMARY. Only the top row's Jump is the amber CTA; the rest are the app's ordinary
        button. Six identical amber buttons in one column said every corner was the first thing to
        do, and the page's first thing is the corner it ranks first (cool/debrief-hero). The two
        variants share padding and border, so the column's width does not depend on which."""
        # Phosphor arrow icon + "Jump" (the Unicode arrow didn't render).
        btn = QPushButton(theme.icon("ph.arrow-right", color=C.on_accent if primary else C.text),
                          "Jump")
        if primary:
            btn.setProperty("variant", "primary")
        btn.setMinimumWidth(JUMP_MIN_PX)
        # B10 (belt+braces with the theme.icon color_active fix): never the focused-default
        # styling that repainted the arrow amber-on-amber.
        btn.setAutoDefault(False)
        btn.setDefault(False)
        if opp is not None:
            btn.setToolTip(f"Select C{opp.cid} on the map and jump the video to your best lap's "
                           "entry to this corner")
            cid, entry = opp.cid, float(opp.entry_dist)
            btn.clicked.connect(lambda _checked=False, c=cid, d=entry:
                                self.jump_requested.emit(c, d))
        return btn

    def _action_cell(self, opp: coaching.Opportunity, primary: bool) -> ActionCell:
        """One row's Jump cell: the Jump, with the row's Compare stacked under it for a page too
        narrow for the Compare column (`_sync_stacked`)."""
        cell = ActionCell(self._go_button(opp, primary=primary), self._cmp_button(opp, compact=True))
        cell.set_stacked(self._stacked)
        return cell

    def _stack_fits(self, reason_px: int) -> bool:
        """Would every shown row hold Jump and Compare stacked, with the reason `reason_px` wide?
        Read off the reason's own wrapped height (the measure `_shortlist_px` uses), so the answer
        is decided without the Compare column and cannot flip with it."""
        t = self.table
        avail, pad_v = _reason_text_box(t, _PANEL_COL_REASON, reason_px)
        if avail <= 0:
            return False
        fm = t.fontMetrics()
        for r in range(t.rowCount()):
            item = t.item(r, _PANEL_COL_REASON)
            text = item.text() if item is not None else ""
            wrapped = fm.boundingRect(QRect(0, 0, avail, 0), Qt.TextWordWrap, text).height()
            if wrapped + pad_v < STACKED_ROW_PX:
                return False
        return True

    def _sync_stacked(self) -> None:
        """Show each row's stacked Compare exactly while the Jump column is on screen, the Compare
        column is not, and every row is tall enough for both (`_stack_fits`, decided by the column
        budget) — never on the debrief (RUL-7)."""
        t = self.table
        stacked = (self._stack_ok and not self._debrief and not t.isColumnHidden(_PANEL_COL_GO)
                   and t.isColumnHidden(_PANEL_COL_CMP))
        if stacked == self._stacked:
            return
        self._stacked = stacked
        for r in range(t.rowCount()):
            cell = t.cellWidget(r, _PANEL_COL_GO)
            if isinstance(cell, ActionCell):
                cell.set_stacked(stacked)

    def _cmp_column_px(self) -> int:
        """The Compare column's width, priced as the Jump column's is (`_go_column_px`)."""
        return max(self._cmp_button(None).sizeHint().width(), JUMP_MIN_PX) + 2 * theme.SPACE_S

    def _cmp_button(self, opp: coaching.Opportunity | None, compact: bool = False) -> QPushButton:
        """One row's Compare: emits ``compare_requested(TypicalPass)`` — the corner's typical pass
        against the best lap's, at the corner. Disabled, saying why, where the corner has no typical
        pass (`compare_tooltip`). The app's ordinary button, as every Jump but the first is: one
        primary per page. With no row it is the template the column budget measures.

        `compact` is the one stacked under Jump (`ActionCell`): no glyph, because "Compare" with its
        glyph is 108 px and the Jump column 88 px inside its inset; without it, 83 px."""
        btn = (QPushButton("Compare") if compact
               else QPushButton(theme.icon("ph.columns", color=C.text), "Compare"))
        btn.setMinimumWidth(JUMP_MIN_PX)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        if opp is not None:
            # Only a RANKED row names a loss to show; an abstained one says why it does not.
            tp = self._typical.get(opp.cid) if opp.evidence.ranked else None
            btn.setAccessibleName(f"Compare at C{opp.cid}")
            btn.setToolTip(compare_tooltip(opp.cid, tp) if opp.evidence.ranked else
                           f"C{opp.cid} is not ranked, so there is no loss here to compare — "
                           "the row says why.")
            btn.setEnabled(tp is not None)
            if tp is not None:
                btn.clicked.connect(lambda _checked=False, t=tp: self.compare_requested.emit(t))
        return btn

    def _tune_rows(self):
        """Show as many of the ranking as the viewport can actually hold (L5-08).

        ``PANEL_TOP_N`` is the shortlist the headline sums and the FLOOR on what the page shows, not
        a ceiling: maximized, the page was 3 rows in 808 px — 78 % dead canvas — while the model had
        11 corners ranked and the modal fitted all 11 in a third of the area. Row heights are
        content-driven (a wrapped reason costs 2–5 lines and the same corner's row is 89 px at one
        panel width and 169 px at another), so the count is ESTIMATED from the mean row height and
        then VERIFIED by measurement: shrink while the rows overflow, then try one more and put it
        back if it does not fit. Estimating first is what keeps a maximize gesture cheap — walking
        3 → 11 one row at a time cost 160 ms against main's 16 ms. Both correction loops are
        monotone, so this terminates; the (width, height, ranking) key makes a resize that changes
        nothing free."""
        if self._tuning or self.body.currentIndex() != 0 or not self._all_rows:
            return
        # The width is keyed as if the vertical scrollbar were showing (the column budget and the
        # shortlist reserve read it the same way): the trial row below can toggle the bar, and a
        # key that moved with it never matched again — measured, one page re-tuned ~70 times a
        # second between two widths 12 px apart, for as long as it was on screen.
        bar = self.table.verticalScrollBar()
        n_all = self._row_limit()
        key = (self.table.viewport().width() - (0 if bar.isVisible() else bar.sizeHint().width()),
               self.table.viewport().height(), n_all,
               tuple(self.table.isColumnHidden(c) for c in _OPTIONAL_COLS))
        if key == self._tuned_key:
            return
        self._tuning = True
        try:
            n, used, avail = self.table.rowCount(), self._rows_px(), self._viewport_px()
            if n and used > 0:
                estimate = min(max(int(avail // (used / n)), PANEL_TOP_N), n_all)
                if estimate != n:
                    self._render_rows(estimate)
            while self.table.rowCount() > PANEL_TOP_N and self._rows_px() > self._viewport_px():
                self._render_rows(self.table.rowCount() - 1)
            while self.table.rowCount() < n_all:
                fitted = self.table.rowCount()
                self._render_rows(fitted + 1)
                if self._rows_px() > self._viewport_px():
                    self._render_rows(fitted)   # one row too many — put it back and stop
                    break
            self._tuned_key = key
        finally:
            self._tuning = False
        self._sync_more_hint()

    def _sync_more_hint(self):
        """Count the shown ranking's rows the table is NOT showing, ranked and not, and say how to
        see them — or hide the line when every row is on screen. Not on the debrief, whose table is
        the shortlist by design (`_row_limit`), nor in the empty state."""
        hidden = ([] if self._debrief or self.body.currentIndex() != 0
                  else self._all_rows[self.table.rowCount():])
        text = more_rows_hint(hidden)
        if self.more_hint.text() != text:
            self.more_hint.setText(text)
        if self.more_hint.isHidden() == bool(text):
            self.more_hint.setVisible(bool(text))

    def _rows_px(self) -> int:
        """Total height the current rows occupy."""
        return sum(self.table.rowHeight(r) for r in range(self.table.rowCount()))

    def _viewport_px(self) -> int:
        """Height available to rows (the header is outside the viewport)."""
        return self.table.viewport().height()

    def _show_excluded(self, opps: coaching.Opportunities):
        """Show the friendly "need more laps" / "no corner losing time" state (NOT an empty box).

        The wording is `empty_state_copy`'s, so the panel and the Opportunities modal read the same
        — which is what the docstring here USED to claim while the two strings differed and only
        the modal's carried a next action (QA D2-08)."""
        self._cids = []
        self._all_rows = []
        self._shortlist = []
        self._typical = {}
        self._tuned_key = None
        self._headline = ""
        self._refresh_summary_label()
        self.empty_state.set_state(*empty_state_copy(opps, self.session))
        self.body.setCurrentIndex(1)
        self._sync_more_hint()

    def resizeEvent(self, event):
        """Re-budget the columns, re-fit the row heights and re-tune the row count.

        The reason (stretch) column re-wraps as the panel narrows, so a row that was one line can
        become two — auto-height keeps the full "How to find it" sentence visible instead of
        clipping it (the truncation bug). Width also decides whether ±σ is affordable (L5-06) and
        height decides how much of the ranking fits (L5-08); both are measured from the laid-out
        table, so they belong here rather than at build time."""
        super().resizeEvent(event)
        self._relayout()

    def eventFilter(self, obj, event):
        """Re-lay-out on the table VIEWPORT's resize — the moment the column and row budgets have
        their real numbers (see the installEventFilter note in __init__)."""
        if obj is self.table.viewport() and event.type() == QEvent.Resize:
            self._relayout()
        return super().eventFilter(obj, event)

    def _relayout(self):
        """Budget the two leading blocks' height, then the columns, then re-fit the wrapped rows,
        then tune the row count — in that order: the blocks decide how much height the table has,
        the column widths decide the wrap, the wrap decides the row heights, and the row heights
        decide how many rows fit."""
        self._apply_theme_budget()
        self._apply_column_budget()
        _fit_reason_rows(self.table, _PANEL_COL_REASON)
        self._tune_rows()
        self._apply_hero_budget()

    # ------------------------------------------------------------- the debrief hero
    def _load_hero(self) -> None:
        """Read the hero's data once per debrief (and again on a refresh while it is up); off the
        debrief it is dropped, so the ordinary page never pays for it."""
        n_clean = self._n_clean if self._all_rows else None
        self.hero.set_data(debrief_hero_data(self.session, n_clean, self._speed_unit)
                           if self._debrief else None)
        self.hero.set_focus(self.focus_block.cids())
        self.hero.set_jumpable(o.cid for o in self._all_rows)

    def _apply_hero_budget(self) -> None:
        """On the debrief, cap the table at its shortlist and give the hero the canvas under it.

        The layout does the arithmetic: the hero has no height of its own (vertical policy
        Ignored, minimum 0), so it gets only what the lead, the focus list, the theme and the
        shortlist leave — it can never push one of them down or shrink it — and it paints nothing
        below ``HERO_MIN_PX`` (``DebriefHero.shows_picture``)."""
        if self._hero_budgeting:
            return
        self._hero_budgeting = True
        try:
            rows = (self._shortlist_px() if self._debrief and self.hero.data() is not None
                    and self.body.currentIndex() == 0 else 0)
            want = rows > 0
            cap = rows if want else _QWIDGETSIZE_MAX
            if self.body.maximumHeight() != cap:
                self.body.setMaximumHeight(cap)
            self._lay.setStretchFactor(self.body, 0 if want else 1)
            self._lay.setStretchFactor(self.hero, 1 if want else 0)
            if self.hero.isHidden() == want:
                self.hero.setVisible(want)
        finally:
            self._hero_budgeting = False

    def _jump_to_corner(self, cid: int) -> None:
        """A ring the hero offers as a target does what its row's Jump does (the same signal, the
        same entry odometer). Only corners with a row are targets (``DebriefHero.set_jumpable``):
        a focus corner with no row today is ringed but takes no click."""
        for opp in self._all_rows:
            if opp.cid == cid:
                self.jump_requested.emit(cid, float(opp.entry_dist))
                return

    def _retune(self):
        """Lay the page out again with the row count re-counted: the columns changed under the
        count the last pass settled on (see the ``_restretch`` note in __init__)."""
        self._tuned_key = None
        self._relayout()

    def _apply_theme_budget(self):
        """Let the focus list and the theme lead, but never displace the ranking they are about.

        Measured: at the app's own 280x196 minimum the three-line theme summary wanted 159 of the
        page's 196 px and left the table a viewport 0 px TALL — a headline about a list, with the
        list gone. Each block sheds its last line, then the one above it, then itself; what they
        shed moves onto the header strip's tooltip, so nothing is deleted, only demoted.

        The FOCUS block is budgeted first and the theme takes what is left under
        ``BLOCKS_MAX_FRACTION``: two blocks that each yield only against the PAGE still add up to
        two thirds of it, and the answer to the driver's own focus list outranks a fresh reading of
        today. A page with no focus report is unchanged — the block returns 0 px.

        AND THE SHORTLIST IS RESERVED BEFORE EITHER (UX-3). Fractions of the page are not a promise
        to the table: at the 1440x900 default the page is 417 px, the two blocks were entitled to
        55 % of it, and the three ranked rows those blocks summarize need ~190-230 px there — so the
        real window showed two of them (one on SD_30_08) and the answer sat below the fold of the
        page that exists to give it. The blocks now share only what is left once the table's header
        and its ``PANEL_TOP_N`` rows, measured at the current width, are whole.

        A LIST THE BLOCK HAS NO ROOM FOR KEEPS ONE LINE (QA JOURNEY-1), the way the rows the
        viewport cannot hold keep ``more_hint``: its corners, a pending "Both dry?" and a click to
        see it whole. Like that hint it is paid for by the table, since it only appears when the
        page has no room left to give; the theme then yields to it."""
        if self._theme_budgeting:
            return
        self._theme_budgeting = True
        try:
            height = self.height()
            hint = self.more_hint.sizeHint().height() if self.more_hint.isVisibleTo(self) else 0
            room = max(height - self._header.height() - self._shortlist_px() - hint, 0)
            # The debrief's lead first (0 px and hidden on the ordinary page), then the focus block.
            used = self.debrief_block.fit_into(self.width(),
                                               min(int(height * DEBRIEF_MAX_FRACTION), room))
            shown = self.focus_block.fit_into(self.width(),
                                              min(int(height * FOCUS_MAX_FRACTION),
                                                  max(room - used, 0)))
            used += shown + self._sync_focus_line("" if shown else self.focus_block.line_text())
            self.theme_block.fit_into(
                self.width(),
                min(int(height * THEME_MAX_FRACTION),
                    max(int(height * BLOCKS_MAX_FRACTION) - used, 0),
                    max(room - used, 0)))
            self._refresh_summary_label()
        finally:
            self._theme_budgeting = False

    def _sync_focus_line(self, text: str) -> int:
        """Show the focus list's one line with `text` ("" hides it); returns the height it takes.
        Touches a widget only when it changes, for FocusBlock._apply's reason."""
        if text and self.focus_line.text() != text:
            self.focus_line.setText(text)
        row = self._focus_line_row
        if row.isHidden() == bool(text):
            row.setVisible(bool(text))
        return row.sizeHint().height() if text else 0

    def _shortlist_px(self) -> int:
        """The height the table needs to show its first ``PANEL_TOP_N`` rows whole: its frame, its
        header and those rows. 0 while the page shows its empty state — there is no ranking to make
        room for.

        EACH ROW AT ITS NARROW HEIGHT — wrapped as if the vertical scrollbar were showing, whether
        or not it is. Reserving the rows at the width they happen to have made a knife-edge, and
        the real window sat on it: on SD_30_08 the three rows fit without the scrollbar (190 px in
        200) and not with it (204 px), so each fit toggled the bar, narrowed or widened the reason
        column by its 12 px, and pinned rows for the width it had just left — the page flipped
        between the two every ~0.4 s, half the time with C7's brake-point line cut to "…". Sized
        for the narrow width, the shortlist fits either way and the bar never has to appear."""
        t = self.table
        if self.body.currentIndex() != 0 or t.rowCount() == 0:
            return 0
        col = _PANEL_COL_REASON
        avail, pad_v = _reason_text_box(t, col, _narrow_column_px(t, col))
        fm = t.fontMetrics()
        rows = 0
        for r in range(min(PANEL_TOP_N, t.rowCount())):
            item = t.item(r, col)
            wrapped = (fm.boundingRect(QRect(0, 0, avail, 0), Qt.TextWordWrap, item.text()).height()
                       if item is not None and avail > 0 else 0)
            rows += max(t.rowHeight(r), wrapped + pad_v)
        hdr = t.horizontalHeader()
        return rows + max(hdr.height(), hdr.sizeHint().height()) + 2 * t.frameWidth()

    # ------------------------------------------------------------- interaction
    def _on_row_selected(self):
        """Emit the clicked row's corner cid (None on deselect). The map apex-ring is the only
        consumer — no seek/lap-selection side effects here (that is the row's Jump button, shown
        whenever the page is wide enough for it). The focus block also follows the selection, because its
        Add button names the corner it would promote."""
        cid = self._selected_cid()
        self.focus_block.set_selected_corner(cid)
        self.corner_clicked.emit(cid)

    def _refresh_summary_label(self):
        """Set the headline-strip text from the stashed headline ("0.60 s across your top 3
        corners"). Empty headline (the friendly no-opportunity state) → no summary.

        The strip's TOOLTIP also carries the full theme + both actions, so a page too short to
        show the theme block (see `_apply_theme_budget`) still has the story one hover away — the
        block sheds lines, it never deletes them."""
        self.summary_label.setText(self._headline)
        story = "\n\n".join(t for t in (self.debrief_block.full_text(),
                                        self.focus_block.full_text(),
                                        self.theme_block.full_text()) if t)
        # The retired modal's title named the typical lap, and until COACHING-2 this line did: the
        # reasons (ADV-1) and the bars are medians over the clean laps now, and no lap is named.
        typical = " " + coaching.medians_note() if self._headline else ""
        # The scope counts the shortlist the headline above it sums (none ranked: no total).
        scope = (scope_tooltip(len(self._shortlist)) if self._headline else _SCOPE_TOOLTIP) + typical
        self.summary_label.setToolTip(f"{story}\n\n{scope}" if story else scope)
