"""Layout TOKENS and the layout TABLE of the export's overlay styles: where each Broadcast element
sits, as data, so a position is one row to edit rather than arithmetic spread through painters.

EVERY LENGTH IS A FRACTION OF THE FRAME'S SHORT SIDE (`u`, `export_video.overlay_unit`), like the
Classic overlay's, so one table composes 720p, 1080p and 4K alike. A slot names an ANCHOR, a
height and (optionally) a width; a slot without a width is fitted by its painter to the widest
string the export will burn (the Classic pills' rule) and handed in as a measured size.

EVERY SLOT HAS FALLBACKS, tried in order, so every frame the dialog offers lays out (16:9 at
any size, 9:16, 1:1, a 4:3 or vertical source, crop or fit — tests/test_export_broadcast.py walks
that matrix): the Δ bar sits top-centre or, where the top row is too narrow, full width under it;
the big speed sits bottom-centre or, where the (wide) minimap is in the way, bottom-left; the
honesty stamp hangs under the tower or, where the g-meter is in the way, below the top row. The
PORTRAIT OVERRIDE only reorders those for a tall frame, and a tall frame's safe area
(`safe_area`) keeps every element of both styles out of the bands the apps cover.

THE CLASSIC PLATES A STYLE KEEPS (the g-meter, the minimap) are not in the table: the Classic
painter places them, and `resolve` takes their boxes as `keep_clear`; an element lands on none of
them and never leaves the safe area — and if none of its places fits, `resolve` raises, naming it,
rather than burn a graphic over another one.

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
# A whole-recording export's LAST line: a sub-plate under the tower for `broadcast.LAST_HOLD_S`
# after each crossing — the finished lap's time as the Laps table prints it. Reserved under the
# tower on such an export (the stamp hangs below it), drawn only during a hold.
TOWER_LAST = 0.040
TOWER_LAST_GAP = 0.006
# The Δ bar (top-centre): a number-free bar, ±scale at its ends.
DBAR_W = 0.36
DBAR_H = 0.050         # its scale and baseline name over the bar itself
# The big speed (bottom-centre).
SPEED_H = 0.100
SPEED_PAD = 0.020
# The Broadcast minimap: the Classic inset at this height (it was 0.22), labelled C1…
MAP_H_FRAC = 0.30

FULL = -1.0            # a slot width meaning "the safe area's full width"

# THE PORTRAIT SAFE AREA (owner ruling, wave 22). A taller-than-wide export is made for the apps
# that play it full screen, and they cover the bottom of the frame with their own buttons and
# caption and the top with their header: at the margin alone the speed sat 13 px off the bottom of
# a 608x1080 frame, under them. So in portrait no overlay element is painted in the top
# PORTRAIT_TOP or the bottom PORTRAIT_BOTTOM of the frame's HEIGHT (fractions of the height, not of
# `u`: the apps' bands scale with the screen's height). Landscape and square keep the margin alone.
PORTRAIT_TOP = 0.08
PORTRAIT_BOTTOM = 0.18


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
    """`anchors`: the places to try, IN ORDER — the first that lands in the safe area clear of
    everything placed or kept so far wins:
      "tl" / "tc" / "bl" / "bc": a corner or an edge's centre, inset by the margin;
      "hang": under the tower, offset by the caller's (dx, dy) from its bottom-left (the stamp's
              place under the Classic strip, translated);
      "under": GAP below everything already placed or kept clear in the frame's top half — at the
              left margin, or at the tower's x plus dx for a hanging slot;
      a trailing "!" ("hang!"): the same place held only to the FRAME, not the safe area, and
              slid left as far as it must be to end inside it — the honesty stamp's last resort.
    `w` None = fitted by the painter (a measured size); FULL = the safe area's width."""
    anchors: tuple[str, ...]
    h: float
    w: float | None = None


# ---------------------------------------------------------------------------------- the table
# The honesty stamp is a slot too (its size and offset are the Classic stamp's, `fitted` and
# `hang`): it hangs under the tower, or — where the Classic g-meter is in the way (a narrow frame
# with a long stamp) — drops below the top row. It is never dropped and never shrunk. On a frame
# too narrow for its longest line inside the safe area (≲ 300 px wide: a 9:16 crop of 540p footage)
# it takes the same two places held only to the frame, into the margins and slid left until its
# longest line ends inside the frame (QA, #565: the export used to fail there instead). The Classic
# stamp runs into the margin on that frame too — and past the frame's edge with three lines, which
# Classic keeps: its frames are 0.9.0's. On a frame narrower than the stamp itself (≲ 290 px: 9:16
# from 480p footage) it starts at the frame's left edge and its longest line is cut where the frame
# ends — exactly as, and less than, the Classic stamp there; never dropped, never shrunk.
BROADCAST: dict[str, Slot] = {
    "tower": Slot(("tl",), TOWER_HEAD + TOWER_PROGRESS + TOWER_TIME + TOWER_DELTA),
    "stamp": Slot(("hang", "under", "hang!", "under!"), 0.0),
    "dbar": Slot(("tc", "under"), DBAR_H, DBAR_W),
    "speed": Slot(("bc", "bl"), SPEED_H),
}
# Portrait: the Δ bar runs full width under the top row, the speed takes the bottom-left corner
# (the bottom edge belongs to the wide minimap). A square frame uses the landscape table, and its
# fallbacks place it (the speed falls back to the bottom-left past the map).
BROADCAST_PORTRAIT: dict[str, Slot] = {
    **BROADCAST,
    "dbar": Slot(("under",), DBAR_H, FULL),
    "speed": Slot(("bl", "bc"), SPEED_H),
}
ORDER = ("tower", "stamp", "dbar", "speed")
# What a layout failure calls each slot ("overlay layout: the honesty stamp does not fit …").
NAMES = {"tower": "timing tower", "stamp": "honesty stamp", "dbar": "Δ bar", "speed": "big speed"}


def table(out_w: int, out_h: int) -> dict[str, Slot]:
    """The Broadcast table for a frame: the portrait override when the frame is taller than wide."""
    return BROADCAST_PORTRAIT if out_h > out_w else BROADCAST


def safe_area(out_w: int, out_h: int, margin: float = MARGIN) -> Box:
    """The box every overlay element of an out_w x out_h frame stays inside, both styles: the frame
    inset by `margin` (a fraction of the short side), and in portrait (taller than wide) also clear
    of the top PORTRAIT_TOP and the bottom PORTRAIT_BOTTOM of the height."""
    m = margin * float(min(out_w, out_h))
    top = bottom = m
    if out_h > out_w:
        top, bottom = max(m, PORTRAIT_TOP * out_h), max(m, PORTRAIT_BOTTOM * out_h)
    return Box(m, top, out_w - 2 * m, out_h - top - bottom)


class LayoutError(ValueError):
    """An element would leave the safe area or land on another one at every place it may take."""


def resolve(slots: dict[str, Slot], names, out_w: int, out_h: int,
            fitted: dict[str, tuple[float, float]], keep_clear: dict[str, Box],
            hang: dict[str, tuple[float, float]] | None = None) -> dict[str, Box]:
    """Place `names` (in order) from `slots` in an out_w x out_h frame, each at the first of its
    anchors that is inside the safe area and clear of every box placed or kept so far. `fitted`
    gives the measured (w, h) of a slot whose width is None; `keep_clear` the boxes of the Classic
    plates the style keeps; `hang` the (dx, dy) of a slot that hangs under the tower. Raises
    `LayoutError`, naming the element and what each place hit, when no anchor fits."""
    u = float(min(out_w, out_h))
    m, gap = MARGIN * u, GAP * u
    safe = safe_area(out_w, out_h)
    # The "!" places' area: the frame across (into the side margins), and down to the margin in
    # landscape; in portrait still between the bands the safe area keeps clear.
    frame = Box(0.0, 0.0, float(out_w), float(out_h)) if out_h <= out_w else Box(
        0.0, safe.y, float(out_w), safe.h)
    offsets: dict[str, tuple[float, float]] = dict(hang or {})
    out: dict[str, Box] = {}
    for name in names:
        slot = slots[name]
        h = slot.h * u
        if slot.w is None:
            w, h = fitted[name]
        elif slot.w == FULL:
            w = safe.w
        else:
            w = slot.w * u
        why = []
        for anchor in slot.anchors:
            area = frame if anchor.endswith("!") else safe
            anchor = anchor.rstrip("!")
            dx, dy = offsets[name] if name in offsets else (0.0, 0.0)
            if anchor == "hang":
                tw = out["tower"]
                x, y = tw.x + dx, tw.bottom + dy
            elif anchor == "under":
                top = [b.bottom for b in (*out.values(), *keep_clear.values())
                       if b.h > 0 and b.y < out_h / 2]
                x = out["tower"].x + dx if name in offsets else m
                y = max(top, default=safe.y - gap) + gap
            else:
                x = {"l": m, "c": (out_w - w) / 2.0}[anchor[1]]
                y = {"t": safe.y, "b": safe.bottom - h}[anchor[0]]
            fits_frame = False
            if area is frame:
                x = max(0.0, min(x, out_w - w))    # slid left until every line is whole
                # Wider than the frame itself: from its left edge, as far as it goes — the
                # Classic stamp is cut off on that frame too, further in (see the table).
                fits_frame = w > out_w and y >= frame.y and y + h <= frame.bottom
            box = Box(x, y, w, h)
            if not (area.contains(box) or fits_frame):
                why.append(f"{anchor}: leaves the {'frame' if area is frame else 'safe area'}")
                continue
            hit = [o for o, b in (*out.items(), *keep_clear.items()) if box.intersects(b)]
            if hit:
                why.append(f"{anchor}: lands on the {', '.join(hit)}")
                continue
            out[name] = box
            break
        else:
            raise LayoutError(f"the {NAMES.get(name, name)} does not fit a {out_w}×{out_h} frame "
                              f"({name} {w:.0f}x{h:.0f}: {'; '.join(why)})")
    return out
