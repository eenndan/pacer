"""Shared Qt widget primitives — one home for the small behaviours more than one view needs.

Pacer-free and view-agnostic (Qt only, no telemetry / session / app imports), so anything here is
safe to reuse from any dialog or panel without dragging that surface's dependencies along with it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel


class WrapLabel(QLabel):
    """A word-wrapping QLabel that claims the height its wrapped text actually needs.

    QLabel implements ``heightForWidth``, but a layout's MINIMUM is built from each item's
    ``minimumSizeHint`` — which for a wrapping label is its ONE-LINE height. So a layout hands a
    wrapping label one line's worth of room and every further line paints outside it: sliced
    through the letterforms and over whatever sits below, with the widget's own minimum never
    growing to say so. Re-asserting an explicit ``minimumHeight`` on each resize is what the layout
    does read, and it makes the fix width-independent: a longer string, a bigger system font or a
    translation grows the box instead of clipping it. Converges — ``heightForWidth`` depends only
    on the width, which the extra height does not change.

    This trap has bitten three separate surfaces in this app — the Help shortcut/About/privacy
    cards (#135), the map's refused-reference notice (#170) and the Library dialog's privacy note
    (#172) — which is why the wrapper lives here rather than in any one of them. Use it instead of
    ``QLabel`` + ``setWordWrap(True)`` for any paragraph a layout has to make room for.
    """

    def __init__(self, text: str = "", parent=None):
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
