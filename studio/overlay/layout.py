"""Layout TOKENS and the layout TABLE of the export's overlay styles: where each Broadcast element
sits, as data, so a position is one row to edit rather than arithmetic spread through painters.

EVERY LENGTH IS A FRACTION OF THE FRAME'S SHORT SIDE (`u`, `export_video.overlay_unit`), like the
Classic overlay's, so one table composes 720p, 1080p and 4K alike. A slot names an ANCHOR, a
height and (optionally) a width; a slot without a width is fitted by its painter to the widest
string the export will burn (the Classic pills' rule) and handed in as a measured size.

THE PORTRAIT OVERRIDE is the table's one exception, and only for the slots it names: a 9:16 frame
is too narrow for a Δ bar between the tower and the g-meter, so the bar runs full width under the
top row; and its bottom edge belongs to the (wide) minimap, so the big speed takes the bottom-LEFT
corner the Classic readout uses. Everything else is the landscape row.

THE CLASSIC PLATES A STYLE KEEPS (the g-meter, the minimap, the honesty stamp) are not in the table:
the Classic painter places them, and `resolve` takes their boxes as `keep_clear` and REFUSES a
layout that would put an element on one of them or outside the safe area — the caller turns that
into a failed export by name rather than a graphic burned over another one.

Qt-free and pacer-free (plain floats), so the table and its checks are testable without a frame.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------------------- tokens
# The Classic overlay's own edge inset (`OverlayConfig.margin_frac`): the safe area every style
# shares. Restated here so the table reads on its own; tests/test_export_broadcast.py holds the two
# equal.
MARGIN = 0.022
GAP = 0.014            # between two Broadcast elements that stack (the tower and the portrait Δ bar)
# The tower (top-left): a header band (the lap), a hero row (the lap clock), a Δ row.
TOWER_HEAD = 0.036
TOWER_TIME = 0.064
TOWER_DELTA = 0.040
TOWER_PAD = 0.016      # inner left/right padding
TOWER_PROGRESS = 0.004  # the lap-progress line under the header
# The Δ bar (top-centre): a number-free bar, ±scale at its ends.
DBAR_W = 0.36
DBAR_H = 0.050         # its scale and baseline name over the bar itself
# The big speed (bottom-centre).
SPEED_H = 0.100
SPEED_PAD = 0.020
# The Broadcast minimap: the Classic inset at this height (it was 0.22), labelled C1…
MAP_H_FRAC = 0.30

FULL = -1.0            # a slot width meaning "the safe area's full width"


@dataclass(frozen=True)
class Box:
    """An axis-aligned rectangle in frame pixels."""
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def intersects(self, o: Box) -> bool:
        """Overlap with a positive area (two boxes that only touch do not intersect)."""
        return (self.w > 0 and self.h > 0 and o.w > 0 and o.h > 0 and self.x < o.right
                and o.x < self.right and self.y < o.bottom and o.y < self.bottom)

    def contains(self, o: Box) -> bool:
        eps = 1e-6
        return (o.x >= self.x - eps and o.y >= self.y - eps and o.right <= self.right + eps
                and o.bottom <= self.bottom + eps)


@dataclass(frozen=True)
class Slot:
    """`anchor`: "tl" / "tc" / "bl" / "bc" (a corner or an edge's centre, inset by the margin), or
    "under" (the left margin, GAP below everything already placed or kept clear at the top).
    `w` None = fitted by the painter (a measured size); FULL = the safe area's width."""
    anchor: str
    h: float
    w: float | None = None


# ---------------------------------------------------------------------------------- the table
BROADCAST: dict[str, Slot] = {
    "tower": Slot("tl", TOWER_HEAD + TOWER_PROGRESS + TOWER_TIME + TOWER_DELTA),
    "dbar": Slot("tc", DBAR_H, DBAR_W),
    "speed": Slot("bc", SPEED_H),
}
BROADCAST_PORTRAIT: dict[str, Slot] = {
    **BROADCAST,
    "dbar": Slot("under", DBAR_H, FULL),
    "speed": Slot("bl", SPEED_H),
}


def table(out_w: int, out_h: int) -> dict[str, Slot]:
    """The Broadcast table for a frame: the portrait override when the frame is taller than wide."""
    return BROADCAST_PORTRAIT if out_h > out_w else BROADCAST


class LayoutError(ValueError):
    """An element would leave the safe area or land on another one."""


def resolve(slots: dict[str, Slot], names, out_w: int, out_h: int,
            fitted: dict[str, tuple[float, float]], keep_clear: dict[str, Box],
            placed: dict[str, Box] | None = None) -> dict[str, Box]:
    """Place `names` (in order) from `slots` in an out_w x out_h frame. `fitted` gives the measured
    (w, h) of a slot whose width is None; `keep_clear` the boxes of the Classic plates the style
    keeps; `placed` boxes an earlier call placed (they are kept clear too, and returned). Raises
    `LayoutError` when an element leaves the safe area or overlaps anything else."""
    u = float(min(out_w, out_h))
    m, gap = MARGIN * u, GAP * u
    safe = Box(m, m, out_w - 2 * m, out_h - 2 * m)
    out = dict(placed or {})
    for name in names:
        slot = slots[name]
        h = slot.h * u
        if slot.w is None:
            w, h = fitted[name]
        elif slot.w == FULL:
            w = safe.w
        else:
            w = slot.w * u
        if slot.anchor == "under":
            top = [b.bottom for b in (*out.values(), *keep_clear.values())
                   if b.h > 0 and b.y < out_h / 2]
            x, y = m, max(top, default=m - gap) + gap
        else:
            x = {"l": m, "c": (out_w - w) / 2.0}[slot.anchor[1]]
            y = {"t": m, "b": out_h - m - h}[slot.anchor[0]]
        box = Box(x, y, w, h)
        if not safe.contains(box):
            raise LayoutError(f"the {name} ({w:.0f}x{h:.0f}) leaves the safe area of a "
                              f"{out_w}x{out_h} frame")
        for other, b in (*out.items(), *keep_clear.items()):
            if box.intersects(b):
                raise LayoutError(f"the {name} would land on the {other} in a {out_w}x{out_h} "
                                  f"frame")
        out[name] = box
    return out
