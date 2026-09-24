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

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from . import coaching, data_quality, focus, theme, units
from ._signal import DASH, lap_label
from .lap_table import set_corner_direction
from .theme import C
from .widgets import EmptyState, PanelHeader, WrapLabel

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# column indices. The Entry·Apex·Exit bars and the Jump buttons (the retired modal's two extra
# columns) come AFTER the reason, so the four the page always had keep their places.
_COL_CORNER, _COL_LOST = 0, 1
_PANEL_COL_REACH, _PANEL_COL_REASON, _PANEL_COL_PHASES, _PANEL_COL_GO = 2, 3, 4, 5
# NB (M4): "Time lost" is the cross-lap MEDIAN per-corner delta; the Entry·Apex·Exit column is a
# DIFFERENT statistic — the typical lap's Δt profile across the corner (where in the corner it wins
# or loses), which does NOT sum to "Time lost" and can even net faster. Its header must not also
# claim to be "time lost", or the two columns read as self-contradictory.
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
_HEADER_TIPS = {
    "Corner": "The corner's number in track order, with an arrow for its direction — "
              "anticlockwise is a left-hander, clockwise a right.",
    "Time lost": "Median time lost through this corner versus your own best lap's same corner, "
                 "over your clean laps (seconds). A lap counts at a corner only where it was "
                 "matched to your best lap's line on track at the corner's entry and exit.",
    "Done it?": "How many of your clean laps already matched or beat your best lap's time "
                "through this corner, out of the laps matched on track there (hover a cell for "
                "the count). Many of them — repeat what you have already driven. Few — "
                "this is pace you have not established yet, and it needs something new.",
    "Entry · Apex · Exit Δt": "Where in the corner your typical lap is faster/slower than your best "
                              "lap (Δt per third, seconds) — NOT the row's Time lost, which is a "
                              "cross-lap median.",
    "How to find it": "The dominant MEASURED reason this corner is losing time, with its numbers — "
                      "plus the ESTIMATED brake-point line when one is available for the corner.",
}


def _style_headers(table: QTableWidget, headers: list[str]):
    """Align every header over its own column's cells and give it its tooltip (L5-06/L5-08)."""
    for col, label in enumerate(headers):
        item = table.horizontalHeaderItem(col)
        if item is None or not label:
            continue
        item.setTextAlignment(_HEADER_ALIGN.get(label, Qt.AlignLeft) | Qt.AlignVCenter)
        item.setToolTip(_HEADER_TIPS.get(label, ""))


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
    item.setToolTip(_HEADER_TIPS.get(label, "") if text == label
                    else f"{label} — {_HEADER_TIPS.get(label, '')}".strip(" —"))


# L2: the time-lost cells render at 2 dp ("+{t:.2f} s"), so any loss under half a centisecond rounds
# to "+0.00 s" — an informationless "opportunity" with a live Jump button. summarize() keeps the raw
# 1e-9 ranking (used by the golden fingerprint + share card), but the DISPLAYED opportunity lists
# (dialog + panel) drop rows below the shown resolution so no "+0.00 s" row ever appears.
DISPLAY_MIN_LOST_S = 0.005  # < this rounds to +0.00 s at 2 dp — not a shown opportunity


# IA-01: the ONE scope word both coaching surfaces lead with. Every opportunity is a median over the
# session's clean laps — the page does not, and cannot, re-scope to the lap you have selected (see
# OpportunitiesPanel's scope note), so it says so where the number is read.
_SCOPE_PREFIX = "Whole session"


def _clean_laps_phrase(n: int) -> str:
    """"median of N clean laps" — the sample the opportunities are a median OVER, so the headline
    carries its own denominator (singular for the degenerate one-lap case)."""
    return f"median of {n} clean lap{'' if n == 1 else 's'}"


def _lap_account(session) -> tuple[int | None, int]:
    """(valid laps, how many of them had a GPS dropout) off `session` — the two counts the lap
    table itself shows — or (None, 0) for a caller with no session / a double with no lap account,
    which keeps the count-only copy."""
    valid = getattr(session, "valid_lap_ids", None)
    if not callable(valid):
        return None, 0
    dropout = getattr(session, "dropout_lap_ids", None)
    return len(valid()), (len(dropout()) if callable(dropout) else 0)


def empty_state_copy(opps: coaching.Opportunities, session=None) -> tuple[str, str]:
    """(title, body) for the two no-table cases — too few clean laps, or nothing losing time.

    U2 — THE REASON IS WHAT REMOVED THE LAPS, NOT ALWAYS THE DRIVER. `opps.n_laps` alone cannot
    tell three different sessions apart, and the copy used to tell all three to "drive a few more
    laps". Measured in the real window on hero6.mp4 and hero8.mp4: zero valid laps, and every other
    panel on the frame said data_quality's "No complete laps in this recording." with its GPS-lock /
    drag-the-line body while this page alone blamed the driving. So `session`'s lap account picks:
      * no valid lap at all — data_quality's one no-laps state, word for word;
      * enough valid laps but GPS dropouts took them under the minimum — the GPS, named with the
        lap table's own total (constructed only: no D24 recording has a dropout lap);
      * too few valid laps — the count is the reason, so "drive more" stays, and any dropout among
        them is named so this page's count reconciles with the rows the lap table lists.

    ONE PAIR OF CONSTANTS FOR TWO SURFACES, and this function is what makes that true rather than
    intended. `OpportunitiesPanel._show_excluded` carried a docstring claiming it matched "the modal
    dialog's wording so the two surfaces read the same"; measured on the same session (QA D2-08),
    the panel said "Drive at least 3 clean (valid, GPS-dropout-free) laps to surface coaching
    opportunities — this session has 0." and the modal said "Need at least 3 … This session has 2.
    Drive a few more laps and reload." Same fact, different sentence, and the PANEL — the surface a
    user actually lands on — was the one missing the NEXT ACTION. Two call sites re-authoring a
    string is how a docstring becomes a wish; there is one author now.

    The split is the app's empty-state copy contract: title = WHAT HAPPENED, body = WHY then WHAT
    NEXT (see widgets.EmptyState)."""
    if not opps.enough:
        valid, dropouts = _lap_account(session)
        if valid == 0:
            return data_quality.NO_LAPS_HEADLINE, data_quality.no_laps_body()
        needs = (f"Coaching needs {coaching.MIN_LAPS} clean (valid, GPS-dropout-free) laps; this "
                 f"session has {opps.n_laps}")
        lost = f"{dropouts} of its {valid} laps had a GPS dropout" if dropouts else ""
        if lost and valid >= coaching.MIN_LAPS:
            return ("Not enough clean laps.",
                    f"{needs}: {lost} (flagged in the Laps tab), which leaves too few to compare "
                    "corner by corner. You drove the laps — the GPS lost its fix during them.")
        return ("Not enough clean laps yet.",
                f"{needs}{f' ({lost})' if lost else ''}. Drive a few more laps and reload.")
    return ("No corner is losing time.",
            "Your typical lap matches your best lap all the way round — your best-lap pace is "
            "consistent. Nice driving.")


def _shown_rows(opps: coaching.Opportunities) -> list[coaching.Opportunity]:
    """The opportunity rows worth SHOWING: those whose time_lost does not round to +0.00 s at the
    2-dp display resolution (L2). Ranking/order is preserved (summarize already sinks the abstained
    rows below the ranked ones); only sub-resolution rows are dropped."""
    return [r for r in opps.rows if r.time_lost >= DISPLAY_MIN_LOST_S]


def _ranked_shown(opps: coaching.Opportunities) -> list[coaching.Opportunity]:
    """The shown rows that carry a CLAIM — the shortlist the headline totals and the digest tile
    mirrors. An abstained row is displayed (with its reason) but must never be summed into a
    "time available" figure: that is the number the evidence gate exists to keep honest.

    Reads the row's evidence through getattr because the Stats page hands this duck-typed rows from
    a session stub; a row with no evidence at all is UNMEASURED, not gated out, so it counts (the
    same rule `coaching._NO_EVIDENCE` encodes)."""
    return [r for r in _shown_rows(opps)
            if getattr(getattr(r, "evidence", None), "ranked", True)]


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
    MEASURES (the reach axis and the four driving signals) and says "no single theme" rather than
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
        theme_obj = getattr(opps, "theme", None) or coaching.Theme(
            kind=coaching.THEME_NONE, share=0.0, execution_s=0.0, pace_s=0.0,
            n_ranked=0, n_abstained=0, cause=coaching.REASON_NONE, cause_share=0.0)
        self._sentence = coaching.theme_sentence(theme_obj)
        self._lines = coaching.theme_actions(theme_obj, list(opps.rows))
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

    Read-only over a ``focus.Report`` plus a selection: the promote / drop / mark gestures are
    SIGNALS the app acts on (it owns the app-support stores — the same split ``set_session_record``
    uses)."""

    # The corner the driver wants added to / dropped from the focus list (a corner cid).
    add_requested = Signal(int)
    remove_requested = Signal(int)
    # The recordings (library fingerprints) the driver just marked Dry — exactly the ones the
    # button named, so the app writes what was offered and nothing more.
    mark_dry_requested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: list[str] = []
        self._headline = ""
        self._empty = False             # the one-line invitation (an active track, no corners yet)
        self._cids: list[int] = []      # cids currently on the list, in list order
        self._selected: int | None = None
        self._mark_fps: list[str] = []  # the unrecorded sessions the Mark button would write
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
        mark.addWidget(self.mark_question)
        mark.addWidget(self.mark_button)
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
        self.add_button = QPushButton("Add to focus list")
        self.add_button.setAutoDefault(False)
        self.add_button.setDefault(False)
        self.add_button.clicked.connect(self._emit_add)
        self.drop_button = QPushButton("Remove from focus list")
        self.drop_button.setAutoDefault(False)
        self.drop_button.setDefault(False)
        self.drop_button.clicked.connect(self._emit_remove)
        row.addWidget(self.add_button)
        row.addWidget(self.drop_button)
        self._row_stretch = row.count()
        row.addStretch(1)
        self._buttons = QWidget()
        self._buttons.setLayout(row)
        lay.addWidget(self._buttons)
        self.setVisible(False)

    # ------------------------------------------------------------------ fill
    def set_report(self, report: focus.Report | None) -> None:
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
        if self._empty:
            self._headline, self._lines = FOCUS_EMPTY_LINE, []
        prompt = None if self._empty else focus.mark_dry_prompt(report)
        self._mark_fps = [fp for fp, _when in report.unrecorded] if prompt else []
        if prompt:
            self.mark_question.setText(prompt[0])
            self.mark_button.setText(prompt[1])
            self.mark_button.setToolTip(prompt[2])
        # On an empty list the trailing stretch gives way, so the line takes the row's slack and
        # the Add button sits at its right edge.
        self._buttons.layout().setStretch(self._row_stretch, 0 if self._empty else 1)
        self.headline.setText(self._headline)
        for label, text in zip(self.lines, self._lines + [""] * focus.MAX_ITEMS, strict=False):
            label.setText(text)
        self._sync_buttons()
        self._apply(len(self._lines), bool(self._headline))

    def set_selected_corner(self, cid: int | None) -> None:
        """The table's selected corner — what the Add button would promote."""
        self._selected = cid if isinstance(cid, int) else None
        self._sync_buttons()

    def full_text(self) -> str:
        """The whole block as one string — what the header tooltip carries, so a line the height
        budget sheds is demoted rather than deleted (and the empty list's invitation with it)."""
        invite = [FOCUS_EMPTY_INVITE] if self._empty else []
        return "\n".join([self._headline, *self._lines, *invite]).strip()

    def _sync_buttons(self):
        """Label + enablement from the selection and the list. The button SAYS which corner it
        would add, because "Add to focus list" beside a table with a selection elsewhere is an
        instruction with no object."""
        cid, full = self._selected, len(self._cids) >= focus.MAX_ITEMS
        on_list = cid is not None and cid in self._cids
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
        self.drop_button.setText(f"Remove C{cid}" if on_list else "Remove from focus list")
        self.drop_button.setEnabled(on_list)
        self.drop_button.setToolTip(
            f"Take C{cid} off your focus list" if on_list
            else "Select a corner that is on your focus list")

    def _emit_add(self):
        if self._selected is not None:
            self.add_requested.emit(int(self._selected))

    def _emit_remove(self):
        if self._selected is not None and self._selected in self._cids:
            self.remove_requested.emit(int(self._selected))

    def _emit_mark(self):
        if self._mark_fps:
            self.mark_dry_requested.emit(list(self._mark_fps))

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
        if self._mark_fps:
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
        # The empty list shows its one line in place of the headline, and no Remove button — on an
        # empty list there is nothing it could ever remove.
        for widget, want in ((self.headline, not self._empty), (self.empty_line, self._empty),
                             (self.drop_button, not self._empty)):
            if widget.isHidden() == want:
                widget.setVisible(want)
        if self._buttons.isHidden() == visible:
            self._buttons.setVisible(visible)
        mark = visible and bool(self._mark_fps)
        if self._mark_row.isHidden() == mark:
            self._mark_row.setVisible(mark)
        if self.isHidden() == visible:
            self.setVisible(visible)


# Human label per coaching.PHASE_* id, in track order (for the breakdown bar segments + tooltip).
_PHASE_LABEL = {coaching.PHASE_ENTRY: "Entry", coaching.PHASE_APEX: "Apex",
                coaching.PHASE_EXIT: "Exit"}

# A short, friendly per-reason hint shown as the row tooltip (the sentence already carries the
# numbers; this explains what the lever IS). Keyed by the coaching.REASON_* ids.
_REASON_TIP = {
    coaching.REASON_APEX: "Your typical lap's minimum (apex) speed here is below your best "
                          "lap's — carry more speed through the slowest point.",
    coaching.REASON_BRAKING: "You spend longer on the brakes into this corner than on your best "
                             "lap — brake later and/or release sooner.",
    coaching.REASON_COASTING: "There's a coasting phase here (neither braking nor on throttle) "
                              "your best lap doesn't have — get back to throttle sooner.",
    coaching.REASON_LINE: "The loss here is mostly inconsistency (lap-to-lap spread) rather than "
                          "one fixable input — repeat the same line.",
    coaching.REASON_NONE: "Time is available here versus your best lap.",
}


class PhaseBar(QWidget):
    """A tiny horizontal entry/apex/exit Δt-profile for one corner on the TYPICAL lap (D2): three
    proportional segments (widths ∝ each third's |Δt| vs best) over the row's three numbers. This is
    a WHERE-in-the-corner profile of the typical lap vs best — NOT the row's "Time lost" (a cross-lap
    median), which it need not sum to or even agree in sign with. Read-only; the segment widths are
    the visual cue, the small numbers underneath the precise values, the net line the sign of the
    whole window, the tooltip the full breakdown.

    L5-05: a FASTER-than-best third is a real, readable state, not an absence of one. It used to
    render as a `C.border` sliver — 1.19:1 against the row, i.e. invisible — so a corner whose three
    thirds were ALL faster than best looked empty beside its "+0.08 s" Time lost, and only the
    tooltip reconciled the two measures. Faster thirds now take the palette's ahead colour, are
    sized by |Δt| like the losing ones, and the window's net is stated on the row face."""

    #: The proportional bar's INK, not a gap — the same category as the scrollbar track's width or
    #: the slider groove's, which is why the spatial guard's stylesheet half deliberately leaves
    #: sub-control artwork sizes alone. It is the thickness of a drawn mark under two lines of
    #: CAPTION type; a spacing step would be choosing it for the wrong reason.
    _BAR_H = 6  # px; the proportional bar's height (the numbers sit below it)

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
            lbl = QLabel(f"{v:+.2f}")
            lbl.setFont(num_font)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"color:{self._phase_colour(pid, v, dominant)};")
            nums.addWidget(lbl, 1)
        lay.addLayout(nums)

        # M4: this bar is the TYPICAL lap's Δt profile across the corner (where in the corner it is
        # faster/slower than best), a DIFFERENT statistic from the row's "Time lost" (a cross-lap
        # median). Label it as a profile, call the sum the typical-lap NET (not "time lost"), and —
        # when that net is ≤ 0 (the typical lap is net faster over the window) — say so plainly so a
        # positive-loss headline row never reads as if the corner were net faster overall.
        net = phases.total
        if net > 1e-6:
            net_line = (f"Typical-lap net {net:+.2f} s over the window "
                        f"— slowest third: {_PHASE_LABEL[dominant].lower()}.")
        elif net < -1e-6:
            net_line = (f"Typical-lap net {net:+.2f} s over the window (net faster than best here) "
                        "— the row's Time lost is the cross-lap median, a different measure.")
        else:
            net_line = "Typical-lap net ~0 s over the window (on your best-lap pace here)."

        # L5-05: the sign of the WINDOW on the row face, not only on hover — a row headlined
        # "+0.08 s lost" whose typical lap is net faster across the corner must say so where it is
        # read. Faster reads in the palette's ahead hue, slower stays muted (the accent is reserved
        # for the dominant losing third above).
        face = QLabel(f"net {net:+.2f} s" if abs(net) > 1e-6 else "net ~0 s")
        face.setFont(theme.mono_font(theme.CAPTION))
        face.setAlignment(Qt.AlignCenter)
        face.setProperty("role", "Note")     # the muted default; the ahead case tints over it
        # A PER-DATUM semantic colour, so it stays a runtime merge over the role rather than a QSS
        # rule: ahead_colour() is a palette ACCESSOR and the stylesheet is built once at startup, so
        # a rule here would freeze this label in the standard green while every other ahead/behind
        # surface followed the colour-blind flip. One of the merges tests/test_inline_styles.py
        # lists by owner.
        if net < -1e-6:
            face.setStyleSheet(f"color:{theme.ahead_colour()};")
        lay.addWidget(face)

        self.setToolTip(
            "Where in the corner your typical lap is faster/slower than your best lap "
            "(Δt per third, s) — NOT the same as the row's Time lost:\n"
            + "   ".join(f"{_PHASE_LABEL[p]} {v:+.2f}" for p, v in zip(ids, vals, strict=True))
            + "\n" + net_line)

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


# D4: below this many metres the brake-point delta is within the estimate's noise — show no hint.
BRAKE_HINT_MIN_M = 2.0

# L5-10: how far PAST a corner's own turn-in the estimated "latest sustainable brake point" may fall
# before the hint stops describing a brake point at all.
#
# The D4 optimum is `apex − d` under CONSTANT-DECEL braking at the session's demonstrated peak —
# straight-line physics, which the friction circle only affords on the APPROACH; the model says so
# itself (coaching.BRAKE_APPROACH_M: "braking starts on the straight before turn-in, ~1 medium-kart
# brake zone"). Past one brake zone beyond turn-in the estimate is outside its own domain, so those
# rows show the measured reason sentence and no metres.
#
# MEASURED (T16b, 2026-09-23) on the evidence table's two working-set recordings (coaching.py: 0068
# is SD_19_09_26, 0064 is Sandown 3h 2026), with D2 in — D2 stopped counting a string of brake blips
# with no sustained brake as a brake event, which moves the brake points this table's optimum is
# the median of — and re-measured the same day once Q2 made Sandown Park a built-in track, so both
# are timed on the owner's own start/finish line. On the loader's line the odometer started at C1's
# turn-in (0.0 m on both); on the real line C1's turn-in sits ~100 m on, every cell moved with it,
# and no verdict changed. Its D24 edition (T15, measured after #335 and marked UNVERIFIED once D24 was gone
# and D2 had moved it) and the figures before that ("3 of 11 ranked corners") are kept in
# studio/docs/coaching-tables-on-d24.md. One row per RANKED coaching row that has a habit to print
# (`coaching.MIN_BRAKE_LAPS` laps matched and at least BRAKE_HINT_MIN_M of metres — on both
# recordings that is every ranked row), with the corner's turn-in and apex on the reference odometer
# beside the MEDIAN optimum `Session.coaching_brake_points` prints. "past turn-in" is optimum −
# turn-in, so this gate's verdict is recomputable from the row's own two cells:
#
#   rec   corner  turn-in m   apex m  optimum m   hint
#   0068  C1           97.6    208.4      232.9   suppressed
#   0068  C2          278.6    301.6      297.7   shown
#   0068  C3          371.7    426.7      416.0   suppressed
#   0068  C5          567.6    595.5      586.0   shown
#   0068  C7          663.0    686.4      679.2   shown
#   0064  C1          100.6    207.0      230.6   suppressed
#   0064  C4          451.2    481.9      467.7   shown
#   0064  C6          611.9    631.8      626.0   shown
#   0064  C7          657.7    681.1      672.7   shown
#
# (tests/test_measured_figures.py derives the sentence below from these cells and, given the
# footage, re-measures every one.)
#
# The gate is narrow: 3 of the 9 ranked rows lose their metres — 0068's C1 and C3 and 0064's C1.
# The 6 it keeps sit 14.1..19.1 m past turn-in, inside the approach the physics assumes; the 3 it
# drops sit 44.3..135.3 m past it. Two of those three are past the APEX as well (0064 C1 by 23.6 m,
# 0068 C1 by 24.5 m), which is the same objection in its sharpest form: a "latest sustainable brake
# point" downstream of the slowest point of the corner is not a brake point.
BRAKE_HINT_MAX_PAST_TURN_IN_M = coaching.BRAKE_APPROACH_M


def _past_turn_in_m(bp, entry_dist: float) -> float:
    """How far past the corner's turn-in the ESTIMATED optimum sits (m; negative = still on the
    approach). Both are the REFERENCE (best-lap) odometer — ``Opportunity.entry_dist`` is that
    corner's enter boundary and ``BrakeHabit.optimal_brake_dist`` is the median apex − braking
    distance, projected into the same frame by ``Session._brake_rows``."""
    return float(bp.optimal_brake_dist) - float(entry_dist)


def _turn_in_phrase(m: float) -> str:
    """"~12 m past the turn-in" / "~12 m before the turn-in" — a brake point named against a
    landmark the driver can see, instead of a bare lap-odometer metre mark (L5-10: the hint stated
    a delta and the tooltip two raw odometer readings, so neither said where the target IS)."""
    if abs(m) < 0.5:
        return "right at the turn-in"
    return f"~{abs(m):.0f} m {'past' if m > 0 else 'before'} the turn-in"


def _brake_point_hint(bp, entry_dist: float | None = None) -> str | None:
    """A short, ESTIMATED braking-point coaching line for a corner's ``coaching.BrakeHabit``, or
    None.

    The metres are the driver's HABIT — the median over the clean laps, and literally the number
    the Stats ▸ BRAKING table's "m later" column shows for the same corner. It used to be the BEST
    lap's single application, so the two surfaces answered "how much later can I brake here?" with
    different metres and named neither (``coaching.BrakeHabit`` records what that measured).

    Positive metres_later => "brake later"; negative => "brake earlier". Labelled ESTIMATED
    (constant-decel assumption at the session's demonstrated peak braking). None when too few laps
    braked into the corner to call it a habit (< coaching.MIN_BRAKE_LAPS), when the metres are
    negligible (< BRAKE_HINT_MIN_M — within the estimate's noise) or, given the corner's turn-in
    odometer `entry_dist`, when the recommended point falls more than
    BRAKE_HINT_MAX_PAST_TURN_IN_M past it (L5-10). `entry_dist=None` skips that geometry gate."""
    if int(bp.n_laps) < coaching.MIN_BRAKE_LAPS:
        return None
    m = float(bp.metres_later)
    if abs(m) < BRAKE_HINT_MIN_M:
        return None
    if (entry_dist is not None
            and _past_turn_in_m(bp, entry_dist) > BRAKE_HINT_MAX_PAST_TURN_IN_M):
        return None
    # theme.ESTIMATED_MARK is the ONE canonical inline "estimated" badge (was a stray "(EST)" here) —
    # so the brake-point hint reads the same "(est)" as the grip column / brake-throttle legend.
    mark = theme.ESTIMATED_MARK
    if m > 0:
        return f"Brake ~{m:.0f} m later into C{bp.cid} {mark}"
    return f"Brake ~{abs(m):.0f} m earlier into C{bp.cid} {mark}"


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
    item = QTableWidgetItem(f"+{opp.time_lost:.2f} s")
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setFont(num_font)
    item.setForeground(QColor(theme.delta_colour(opp.time_lost) if opp.evidence.ranked
                              else C.text_dim))
    return item


# The one-word answer in the "Done it?" cell, per coaching.REACH_* id — the glance cue that
# separates "repeat what you already did" from "find something new" without reading a sentence.
_REACH_WORD = {
    coaching.REACH_REPEAT: "Yes",
    coaching.REACH_RARE: "Rarely",
    coaching.REACH_NEVER: "Never",
}


def _reach_tip(ev: coaching.Evidence, of: int | None) -> str:
    """The "Done it?" hover's count sentence. `of` is the session's clean-lap count
    (`Opportunities.n_laps`), or None from a caller that does not know it.

    SINCE #339 `ev.n_laps` IS NOT THE SESSION'S CLEAN LAPS. It counts the laps whose time through
    THIS corner counts — matched on track at its entry and exit, on the lap and on the best lap
    (`coaching.summarize`) — so on MK_18_09_26 C2 it is 16 of 19. This sentence said "3 of your 16
    clean laps" there, one line under a headline reading "median of 19 clean laps". Where the two
    differ it now says both, and why, in the words the Stats page's CORNERS hover uses for the same
    16 (`stats_panel._corner_count_tip`)."""
    if of is None or ev.n_laps >= of:
        return (f"{ev.reach_laps} of your {ev.n_laps} clean laps already matched or beat your "
                "best lap's time through this corner.")
    return (f"{ev.reach_laps} of the {ev.n_laps} laps that count here already matched or beat "
            f"your best lap's time through this corner. {ev.n_laps} of your {of} clean laps "
            f"count: on the other {of - ev.n_laps} the corner could not be matched to your best "
            "lap's line on track, so its time was interpolated between its neighbours and is left "
            "out, as the Stats page's CORNERS table leaves it out.")


def _reach_cell(opp: coaching.Opportunity, num_font, of: int | None = None) -> QTableWidgetItem:
    """"Yes · 9/38" — how many clean laps already matched this corner's target, and the word for it.
    `of` is the session's clean-lap count, for the hover (see `_reach_tip`).

    THIS REPLACED THE ±σ COLUMN, deliberately. σ was the raw dispersion printed for the reader to
    interpret, and on the real recordings interpreting it was the whole job: σ ≥ the row's own
    "Time lost" on 11 of the 12 shown rows across the two working-set recordings (worst 81.5x;
    coaching.py's evidence table, T16b), so the column
    that mattered most was the one asking for arithmetic. This states the conclusion instead —
    and states it as a COUNT OVER ITS DENOMINATOR, so it stays checkable. σ itself is not lost: the
    REASON_LINE sentence spells it, the Stats ▸ CORNERS table has a σ column, and the Consistency
    panel ranks on it.

    An unmeasured row (a legacy/synthetic Opportunity with no per-lap times) reads the em-dash
    rather than inventing a count."""
    ev = opp.evidence
    word = _REACH_WORD.get(ev.reach)
    item = QTableWidgetItem(f"{word} · {ev.reach_laps}/{ev.n_laps}" if word else DASH)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setFont(num_font)
    item.setForeground(QColor(C.text_dim))
    item.setToolTip(
        f"{_reach_tip(ev, of)}\nMany — you have the pace here and the work is repeating it. "
        "Few — you have rarely been this quick, and repeating your usual lap will not find it."
        if word else "Not measured for this row.")
    return item


def _reason_cell(opp: coaching.Opportunity, brake_points: dict,
                 speed_unit: str | None = None) -> QTableWidgetItem:
    """The 'How to find it' reason cell: the coaching sentence (apex deficit in `speed_unit`, km/h
    default) + (when a braking-point estimate is available for this corner) the ESTIMATED 'brake
    ~N m' line, with the per-reason tooltip.

    An ABSTAINED row shows `coaching.abstain_sentence` (which `reason_sentence` returns for it) and
    NO brake-point hint: the estimated "brake ~17 m later" line is exactly the collapse-to-a-default
    this gate exists to prevent, and appending it to a row that just declined to make a claim would
    hand the reader advice the model does not stand behind."""
    sentence = coaching.reason_sentence(opp, speed_unit)
    if not opp.evidence.ranked:
        item = QTableWidgetItem(sentence)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setForeground(QColor(C.text_dim))
        item.setToolTip(
            "Not ranked. Coaching abstains on this corner rather than offering a default: see the "
            "sentence for which evidence test it failed. The measurement is still shown.")
        return item
    bp = brake_points.get(opp.cid)
    # L5-10: the corner's own turn-in gates the hint — a "latest sustainable brake point" more than
    # one brake zone INSIDE the corner is not a brake point, and the metres are not shown for it.
    hint = _brake_point_hint(bp, opp.entry_dist) if bp is not None else None
    item = QTableWidgetItem(f"{sentence}\n{hint}" if hint else sentence)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    tip = _REASON_TIP.get(opp.reason.kind, "")
    if hint is not None:
        # L5-10: state the TARGET, not two bare odometer marks — both points are named against the
        # corner's turn-in, the landmark the driver is actually looking at.
        #
        # And state WHAT THE METRES ARE MEASURED OVER. "Brake later than what?" is the category's
        # loudest complaint, and this app used to have two unlabelled answers to it (see
        # coaching.BrakeHabit). The scope, the sample it came out of, the OBSERVED middle half of
        # that sample — never a modelled margin — and the other surface showing the same number all
        # live here rather than in the cell, because the cell has no vertical room to spare.
        # "…AND WERE MATCHED ON TRACK": since #339 `_brake_rows` drops a lap's brake point where
        # that lap's corner was interpolated, so n is not every clean lap that braked here (MK_18_09
        # C2: 16 counted, 18 braked). Always true, so it needs no second count to be honest.
        tip = (f"{tip}\n\n{hint}: over the {bp.n_laps} clean laps that braked into this corner and "
               "were matched on track at its entry and exit, the "
               "apex-speed-matched latest sustainable brake point sits "
               f"{_turn_in_phrase(_past_turn_in_m(bp, opp.entry_dist))}; you typically brake "
               f"{_turn_in_phrase(float(bp.actual_brake_dist) - float(opp.entry_dist))}. "
               f"The middle half of those laps read {bp.q25_m:+.0f} to {bp.q75_m:+.0f} m "
               "(+ = could have braked later) — your observed spread, not a modelled margin.\n"
               "This is the median, the same number the BRAKING table on the Stats page reports "
               "in its \"m later\" column. ESTIMATED (constant decel at this session's "
               "demonstrated peak braking).")
    item.setToolTip(tip)
    return item


# The actionable SHORTLIST: the rows the page's headline sums (and the Stats page's coaching digest
# tile mirrors — stats_panel._set_digest reads this constant so the two surfaces state one total),
# and the FLOOR on how many rows the page shows. L5-08: it is not a ceiling any more — the page
# renders as much of the ranking as its viewport can hold (see OpportunitiesPanel._tune_rows).
PANEL_TOP_N = 3

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

# The columns the page gives up as it narrows, first to last. The bars are a detail of the row (the
# reason sentence already names the dominant phase), Jump an action a row click half-does (it rings
# the corner), "Done it?" a count the sentence also states — see _apply_column_budget.
_OPTIONAL_COLS = (_PANEL_COL_PHASES, _PANEL_COL_GO, _PANEL_COL_REACH)
JUMP_MIN_PX = 88     # the Jump button's floor: its arrow + label at the app's button padding

# What the headline strip's hover says about the page itself. Lives beside PANEL_TOP_N because it
# quotes it; the live theme + actions are prepended to it (see _refresh_summary_label).
_SCOPE_TOOLTIP = (
    "The biggest realistic time gains vs your own best lap, across the WHOLE session "
    "(the median over your clean, GPS-dropout-free laps) — these rows do NOT follow the "
    "lap you select; the Corners tab is the per-lap view. The total is your top "
    f"{PANEL_TOP_N} corners that cleared the evidence gate; corners that did not are listed "
    "below with the reason. Coaching ▸ Opportunities (or the panel's maximize button) shows "
    "this page full-window, where "
    "each row also carries where in the corner the time goes and a Jump to it.")


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
    failed and NO brake-point hint — the collapse-to-a-default ("brake 8 m later") is exactly what
    the gate exists to prevent. Only the RANKED rows are summed into the headline total.

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
    every row is the MEDIAN loss vs best over the clean laps, ±σ is the cross-lap σ, and the reason
    is read off the median lap — none of which a single lap can answer. Its sibling Corners tab IS
    the per-lap surface (it renames itself "Corners · L6"), so this page must SAY it does not follow
    the selection rather than look like it silently failed to: the headline leads with the scope and
    the tab tooltip names it. Do not wire this to ``laps_selected`` — ``refresh()`` recomputes the
    identical session statistic, so that would repaint the same pixels and change nothing.

    Reads ONLY session accessors (``coaching_opportunities`` + ``coaching_brake_points``) — no
    analysis here. Refreshed on load / re-segmentation / unit + palette change (never on the 30 Hz
    tick, never on selection — see the scope note).
    A row click emits ``corner_clicked(cid)`` so the app can ring the corner's apex on the map.
    Honours the shared ESTIMATED labelling (the ``(est)`` brake-point lines via ``_reason_cell``,
    from ``theme.ESTIMATED_MARK``) and the friendly "need more laps" state when there aren't
    enough clean laps."""

    # Clicked corner cid (None on deselect) -> the map apex-ring highlight (wired in central_view).
    corner_clicked = Signal(object)
    # The focus list's two gestures, forwarded from the block: the app owns the store, so this page
    # asks rather than writes (the same split `CentralView.set_session_record` uses).
    focus_add_requested = Signal(int)
    focus_remove_requested = Signal(int)
    focus_mark_dry_requested = Signal(list)
    # A row's Jump: (cid, the corner's entry odometer on the best lap). The app selects the corner
    # and seeks the video to the best lap's entry to it (StudioWindow._jump_to_opportunity).
    jump_requested = Signal(int, float)

    _COLUMNS = ["Corner", "Time lost", "Done it?", "How to find it", "Entry · Apex · Exit Δt", ""]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._num_font = theme.mono_font(theme.TABLE)
        self._cids: list[int] = []  # row -> corner cid, set in refresh()
        # L5-08: the WHOLE shown ranking (the table renders as many of these as it can hold) + the
        # brake points its reason cells need, so a re-tune re-renders without re-reading the session.
        self._all_rows: list[coaching.Opportunity] = []
        self._brake_points: dict = {}
        self._n_clean: int | None = None  # the session's clean laps, for the "Done it?" hover
        self._typical_lap: int | None = None  # the lap the reasons + bars read (median_lap_id)
        self._tuning = False         # re-entrancy guard: a re-render fires resizeEvent
        self._tuned_key: tuple | None = None   # the viewport the current row count was tuned for
        self._budgeting = False        # re-entrancy guard: hiding a column fires resizeEvent
        self._theme_budgeting = False  # ditto: shedding a theme line re-lays the page out
        # The last width each optional column had, so the budget can cost one while it is hidden.
        # The bars and the Jump column are fixed widths; "Done it?" sizes to its content.
        self._col_px = {_PANEL_COL_PHASES: PHASE_COL_PX, _PANEL_COL_REACH: 0,
                        _PANEL_COL_GO: self._go_column_px()}
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
        for col, px in ((_PANEL_COL_PHASES, PHASE_COL_PX), (_PANEL_COL_GO, self._go_column_px())):
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

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.focus_block)
        lay.addWidget(self.theme_block)
        lay.addWidget(self.body, 1)  # the rows take the page's full height
        self.refresh()

    def set_focus_report(self, report: focus.Report | None) -> None:
        """Show the focus list and this session's verdict on it (the app builds the report from the
        store + ``Session.focus_report``). Passing an empty report shows the one-line invitation;
        never calling this at all leaves the page exactly as it was before the feature."""
        self.focus_block.set_report(report)
        self.focus_block.set_selected_corner(self._selected_cid())
        self._relayout()
        self._refresh_summary_label()

    # ------------------------------------------------------------------ build
    def refresh(self):
        """Recompute the opportunities from the session and rebuild the top-3 rows (or the friendly
        excluded state). Called on load / re-segmentation / unit + palette change — never on the
        30 Hz tick, and never on a lap selection (the summary is session-scoped; see the class note).
        Clears any held row selection (a stale cid would mis-ring the map)."""
        opps = self.session.coaching_opportunities()
        brake_points = self.session.coaching_brake_points()
        self.theme_block.set_theme(opps)
        # L2: only rows above the shown resolution count as opportunities (no "+0.00 s" rows).
        if opps.enough and _shown_rows(opps):
            self._fill_rows(opps, brake_points)
        else:
            self._show_excluded(opps)
        # A new theme is new prose of a new length, so re-run its height budget against the page
        # the panel currently has (the resize path re-runs it whenever that page changes).
        self._apply_theme_budget()

    def set_speed_unit(self, unit: str):
        """Switch the reason sentence's apex-deficit unit live: re-fill the rows. No-op if
        unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.refresh()

    def _fill_rows(self, opps: coaching.Opportunities, brake_points: dict):
        """Populate the table from `opps.rows` (shared cell builders, so a row reads identically to
        the modal dialog) and the headline summary."""
        # L2: only shown-resolution rows are opportunities (drop the "+0.00 s" rows).
        self._all_rows = _shown_rows(opps)
        self._brake_points = brake_points
        self._n_clean = opps.n_laps
        self._typical_lap = opps.median_lap_id
        self._tuned_key = None       # a new ranking: re-tune the row count against the viewport
        # The headline shortlist is the top RANKED rows, not the top rows: an abstained corner is
        # displayed but its number is not a claim, so summing it into "time available" would put
        # back exactly the noise the evidence gate just took out. (summarize orders ranked-first, so
        # this is normally `[:PANEL_TOP_N]` — the filter matters when fewer than N rows are ranked.)
        rows = _ranked_shown(opps)[:PANEL_TOP_N]
        # B12: sum the 2-dp DISPLAYED values, not the raw floats — the headline ("0.56 s")
        # and the visible rows (+0.26 +0.20 +0.11 = 0.57) must never disagree by a rounding
        # penny; the header is an aggregate of what the user can check by eye.
        total = sum(round(r.time_lost, 2) for r in rows)
        # P1: phrase the headline by COUNT — "in your worst corner" reads right for one, "across your
        # top N corners" for several, so it never says the ungrammatical "across the top 1".
        # ...AND NAME THE BASELINE, on the face, not only in the column tooltip. One tab away the
        # Stats CORNERS table prints a "Med loss" for the SAME corners measured against the best
        # anyone did in each — a different question with a much bigger answer (on the D24 0060 pair
        # the two columns sum to 1.02 s here and 3.93 s there, and corner by corner they run from
        # 1.3x to 2450x apart). Two numbers that far apart, one tab apart, both called a loss, is
        # not a rounding question the reader can resolve by looking harder. The modal dialog this
        # panel shares its rows with has said "vs your best lap" in its title all along; the page
        # a user actually lands on did not.
        # ...and the ZERO case is real now: every shown corner can fail the evidence gate, in which
        # case there is no time to claim and the honest headline says so rather than totalling 0.00 s
        # "across your top 0 corners". The table below still lists those corners with their reasons.
        if not rows:
            gains = "no corner clears its own lap-to-lap spread"
        else:
            gains = (f"{total:.2f} s in your worst corner" if len(rows) == 1
                     else f"{total:.2f} s across your top {len(rows)} corners")
            gains += " vs your best lap"
        # IA-01: LEAD with the scope. The tab strip beside this page renames itself "Corners · L6"
        # on a selection, so a coaching headline that neither moves nor names its scope reads as the
        # selected lap's number — on D24 lap 6 that understated the lap's own +2.08 s as "0.21 s".
        # State the session scope and the sample it is a median of, in the same "·" idiom the tabs
        # use, so the two pages can be told apart at a glance.
        self._headline = f"{_SCOPE_PREFIX} · {gains} ({_clean_laps_phrase(opps.n_laps)})"
        self._refresh_summary_label()

        # A refresh is new data / a new unit / a new palette, so every cell is rebuilt. The row
        # COUNT is the page's floor (the tune loop below grows it to the viewport); it is NOT the
        # headline shortlist's length, which counts only ranked rows.
        self._render_rows(min(PANEL_TOP_N, len(self._all_rows)),
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
            for r in range(built, len(rows)):
                opp = rows[r]
                self.table.setItem(r, 0, _corner_cell(opp))
                self.table.setItem(r, 1, _lost_cell(opp, self._num_font))
                self.table.setItem(r, 2, _reach_cell(opp, self._num_font,  # have you done it?
                                                     self._n_clean))
                self.table.setItem(r, 3, _reason_cell(opp, self._brake_points, self._speed_unit))
                self.table.setCellWidget(r, _PANEL_COL_PHASES, PhaseBar(opp.phases))  # D2
                self.table.setCellWidget(r, _PANEL_COL_GO, self._go_button(opp))
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
        goes: the reason sentence spells "Done it?" out ("You have already done this — 9 of 38
        laps"), a row click still rings the corner on the map, and full-window every column is back.

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
                if room - self._col_px[col] < REASON_MIN_PX:
                    break           # strict priority: never a lesser column in a greater one's place
                shown.add(col)
                room -= self._col_px[col]
            for col in _OPTIONAL_COLS:
                if (col not in shown) != t.isColumnHidden(col):
                    t.setColumnHidden(col, col not in shown)
                    self._restretch.start(0)
            _elide_header(t, _PANEL_COL_REASON, self._COLUMNS[_PANEL_COL_REASON],
                          self._reason_chrome)
        finally:
            self._budgeting = False

    def _go_column_px(self) -> int:
        """The Jump column's width: the button's own width (never under its floor) and the cell's
        inset on both sides — `theme.SPACE_S`, the gap a cell keeps around its content."""
        return max(self._go_button(None).sizeHint().width(), JUMP_MIN_PX) + 2 * theme.SPACE_S

    def _go_button(self, opp: coaching.Opportunity | None) -> QPushButton:
        """One row's Jump: emits ``jump_requested(cid, entry_dist)``. With no row it is the template
        the column budget measures before any row exists."""
        # Phosphor arrow icon + "Jump" (the Unicode arrow didn't render); primary CTA styling.
        btn = QPushButton(theme.icon("ph.arrow-right", color=C.on_accent), "Jump")
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
        key = (self.table.viewport().width() - (0 if bar.isVisible() else bar.sizeHint().width()),
               self.table.viewport().height(), len(self._all_rows),
               tuple(self.table.isColumnHidden(c) for c in _OPTIONAL_COLS))
        if key == self._tuned_key:
            return
        n_all = len(self._all_rows)
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
        self._tuned_key = None
        self._headline = ""
        self._refresh_summary_label()
        self.empty_state.set_state(*empty_state_copy(opps, self.session))
        self.body.setCurrentIndex(1)

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
        and its ``PANEL_TOP_N`` rows, measured at the current width, are whole."""
        if self._theme_budgeting:
            return
        self._theme_budgeting = True
        try:
            height = self.height()
            room = max(height - self._header.height() - self._shortlist_px(), 0)
            used = self.focus_block.fit_into(self.width(),
                                             min(int(height * FOCUS_MAX_FRACTION), room))
            self.theme_block.fit_into(
                self.width(),
                min(int(height * THEME_MAX_FRACTION),
                    max(int(height * BLOCKS_MAX_FRACTION) - used, 0),
                    max(room - used, 0)))
            self._refresh_summary_label()
        finally:
            self._theme_budgeting = False

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
        story = "\n\n".join(t for t in (self.focus_block.full_text(),
                                        self.theme_block.full_text()) if t)
        # The retired modal's title named the typical lap; it is said here now.
        typical = (f" The reasons and the Entry·Apex·Exit bars read your typical lap, lap "
                   f"{lap_label(self._typical_lap)}." if self._typical_lap is not None
                   and self._headline else "")
        scope = _SCOPE_TOOLTIP + typical
        self.summary_label.setToolTip(f"{story}\n\n{scope}" if story else scope)
