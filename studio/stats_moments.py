"""MOMENTS, a Stats-page section: three measured moments of the session (`story.session_moments`)
— the best lap, the biggest single-corner loss, the best 5-lap run — each a Jump to where it
happened. A section of `stats_panel.StatsView`: build, refresh and copy here; the page places it
(see `stats_common`) and owns the Jump's signal (`StatsView.moment_jump`).
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from . import story, theme
from ._signal import lap_label
from .lap_table import PROVISIONAL_TOOLTIP, estimated_timing_tooltip
from .library import PB_PRECISION_S
from .stats_common import section_heading
from .theme import C
from .widgets import WrapLabel, icon_button

# Two sentences: what a moment is with its tie rule, and how the 5-lap run differs from the PACE
# row's race pace a few lines up (a median of five against a mean of three: on the shipped demo the
# run reads 0:45.182 under the tile's 0:45.246, which without this would read as a contradiction).
MOMENTS_TOOLTIP = (
    "Three measurements from this session's clean laps, each with a Jump to where it happened; "
    f"figures within {PB_PRECISION_S:.2f} s of each other, the timing precision (wider on an "
    "estimated clock), are named as level rather than ranked. The 5-lap run is the best median of "
    "5 consecutive clean laps, not the PACE row's race pace, which is the best mean of 3.")

# The pairs that must not part across a wrap, each joined by a no-break space. Looked at, not
# guessed: the quadrant wrapped "(within 0.07 / s)" and "pass / (▼)", the 340 px page "C5 / +2.84 s",
# and a wrapped line could start with "· " — the separator now stays with the word before it.
_UNBROKEN = (r"(\d) (s\b)", r"\b(Laps?) (\d)", r"\b(C\d+) ([+−-]\d)", r"(\w) (\()", r"(\S) (·)")


def _unbroken(text: str) -> str:
    """`text` escaped for a rich label with `_UNBROKEN`'s pairs kept on one line."""
    for pattern in _UNBROKEN:
        text = re.sub(pattern, "\\1\u00a0\\2", text)
    return html.escape(text)


def caveat_lines(caveat: tuple[str, ...], timing_quality) -> list[str]:
    """The page's own wording for each timing state a moment carries, provisional first."""
    lines = []
    if story.CAVEAT_PROVISIONAL in caveat:
        lines.append(PROVISIONAL_TOOLTIP)
    if story.CAVEAT_ESTIMATED in caveat:
        lines.append(estimated_timing_tooltip(timing_quality))
    return lines


class MomentsSection:
    """MOMENTS: a heading, up to three rows ("Best lap: Lap 13 · 0:44.950" and a Jump) and, under
    a provisional line and/or an estimated clock, the page's own timing wording, once each. A None
    moment is an absent row; none at all is an absent section, heading included — never an empty box.

    Each row is ONE wrapping label with its label set dim inline, not a label column: at the 503 px
    quadrant a column wide enough for "Biggest single-corner loss" left the 5-lap text four lines.
    The row's words are `story.moment_row`'s, never re-derived or sliced here. `jump(lap_id, cid)`
    is the page's `moment_jump`; the app selects the lap and rings the corner (CentralView)."""

    def __init__(self, jump):
        self._jump = jump
        self.heading = section_heading("MOMENTS")
        self.heading.setToolTip(MOMENTS_TOOLTIP)
        # One body under the heading, its bottom margin the tile grids' group separator
        # (`StatsView._grid`), so the section ends where its neighbours' tile rows end.
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, theme.SPACE_S)
        # SPACE_S between rows, the tile rows' own step: at SPACE_XS a wrapped row's second line
        # ran into the next row's label.
        body.setSpacing(theme.SPACE_S)
        self.rows: list[QWidget] = []
        self.labels: list[WrapLabel] = []
        self.buttons: list[QPushButton] = []
        self._targets: list[tuple[int | None, int | None]] = [(None, None)] * 3
        for i in range(3):
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(theme.SPACE_S)
            label = WrapLabel("")
            label.setTextFormat(Qt.RichText)
            # The Jump is the app's square arrow button (28 px, the icon-button hit area), not the
            # coaching page's 88 px "Jump" pill: three pills down the right edge read as a
            # dashboard of calls to action, and took 96 px of every line from the text.
            btn = icon_button("ph.arrow-right")
            btn.clicked.connect(lambda _checked=False, k=i: self._jump(*self._targets[k]))
            lay.addWidget(label, 1, Qt.AlignVCenter)
            lay.addWidget(btn, 0, Qt.AlignTop)
            body.addWidget(row)
            self.rows.append(row)
            self.labels.append(label)
            self.buttons.append(btn)
        self.caveat = WrapLabel("")
        self.caveat.setProperty("role", "Note")
        self.caveat.setFont(theme.ui_font(theme.CAPTION))
        body.addWidget(self.caveat)

    def widgets(self) -> list:
        return [self.heading, self.body]

    def tables(self) -> list:
        return []

    def refresh(self, session) -> None:
        read = getattr(session, "session_moments", None)
        sm = read() if read is not None else None
        moments = [m for m in ((sm.best_lap, sm.biggest_loss, sm.best_window) if sm else ())
                   if m is not None]
        self.heading.setVisible(bool(moments))
        self.body.setVisible(bool(moments))
        for i, row in enumerate(self.rows):
            m = moments[i] if i < len(moments) else None
            row.setVisible(m is not None)
            if m is None:
                self._targets[i] = (None, None)
                continue
            r = story.moment_row(m)
            self.labels[i].setText(f'<span style="color:{C.text_dim}">{_unbroken(r.label)}:'
                                   f"</span> {_unbroken(r.value)}")
            self.labels[i].setToolTip(m.text)
            self._targets[i] = (r.lap_id, r.cid)
            btn = self.buttons[i]
            btn.setVisible(r.lap_id is not None)
            if r.lap_id is not None:
                where = f", C{r.cid}" if r.cid is not None else ""
                btn.setAccessibleName(f"Jump to Lap {lap_label(r.lap_id)}{where}")
                ring = f" and ring C{r.cid} on the map" if r.cid is not None else ""
                btn.setToolTip(f"Jump: select Lap {lap_label(r.lap_id)}{ring}")
        # Every state any moment carries (they share one caveat; the union is belt and braces).
        caveat = tuple(dict.fromkeys(c for m in moments for c in m.caveat))
        lines = caveat_lines(caveat, getattr(session, "timing_quality", None))
        self.caveat.setText("\n".join(lines))
        self.caveat.setVisible(bool(lines))
