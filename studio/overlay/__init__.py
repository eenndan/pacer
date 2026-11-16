"""Overlay ELEMENTS: the seam later export overlays land through, one small module per element.

`export_video.OverlayPainter` paints the shipped ("Classic") overlay itself — the g-dial, the map
inset, the readout, the lap strip — and that code stays where it is (≈960 lines that
`export_compare` imports by private name; moving it was measured as not worth it). Anything NEW is
an element here instead, so the 4,800-line exporter does not grow with every idea:

  * an element is a class with a `name`, a `layer`, `prepare(ctx) -> bool` (once per export: True
    = it has something to draw in THIS export, False = it sits this one out, silently) and
    `paint(painter, vals)` (once per frame, `vals` = that frame's `export_video.OverlayValues`);
  * `OverlayConfig.elements` names the ones a render asks for — EMPTY BY DEFAULT, and with it empty
    the painter never reaches this package, so the Classic export is byte-identical to a build
    without it (tests/test_export_ghost.py);
  * `layer` says where in the frame's paint order it lands: `LAYER_MAP` on the minimap UNDER the
    kart dot and its tail (the dot is the driver; nothing may cover it), `LAYER_TOP` over everything.
    A `LAYER_MAP` element may define `paint_map(painter, vals, dot, over)` instead of using `paint`:
    it is called with the kart dot's frame point (None without a dot) once under the dot
    (`over=False`) and once over it (`over=True`) — what it paints over the dot must leave the
    dot's own disc (`DOT_R` x k round `dot`) uncovered (the ghost's ring round the dot, ghost.py);
  * `replaces` (optional, a tuple of `PLATES` names) says which Classic plates it stands in for: a
    style that draws its own lap tower names "strip", and the painter then skips the Classic one.
    Only a PREPARED element replaces anything (one that sits the export out leaves the Classic
    plate in place). The honesty stamp is not a plate and cannot be replaced: what it qualifies is
    the recording, and a style may move the caveat but never drop it;
  * the context carries every Classic plate's rectangle (`plates`, read-only, in frame pixels: the
    rect each was painted into) and the frame's `safe` area (the frame inset by the Classic margin),
    so an element can place itself clear of them — or exactly over the one it replaces;
  * `occupies` (optional, set by `prepare`: a {plate name: rect} dict) says where a PREPARED element
    now stands in for a plate: every element prepared AFTER it reads those rects in `plates` (a
    style's tower is the "strip" a corner card keeps clear of), and an `occupies[STAMP]` moves the
    honesty stamp there — the same lines, painted by the same function at the same size, in a
    rect of the Classic stamp's size (`stamp_rect`). Only a style that replaces the strip the stamp
    hangs under has a reason to move it. ORDER MATTERS: an element prepared BEFORE the one that
    occupies (named earlier in `OverlayConfig.elements`) still reads the Classic rects — which is
    why `broadcast.styled` puts the style's elements first.

AN EXTRA THAT CANNOT FIT THE FRAME may sit the export out by returning False with a `skipped`
sentence set ("Corner cards did not fit this frame size…"): `build` collects it into `notes`, which
the finished box shows. A style's layout that cannot place what it must show raises
`OverlayLayoutError` ("overlay layout: …"), and the failure box says that, not that the encoder
stopped.

A FAILING ELEMENT FAILS THE EXPORT, LOUDLY, BY NAME. Skipping it would hand the user a file that is
missing what they asked for with nothing saying so; catching it per frame would hide a defect that
is identical on every frame. `OverlayElementError` is a RuntimeError, which the render path does not
retry on the software encoder (a paint defect is not an encoder failure) and the export dialog shows
as its failure box.

Qt like the exporter (it paints with QPainter); pacer-free like every view.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Protocol

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter

from studio.overlay import layout  # Qt-free, imports nothing from here

LAYER_MAP = "map"      # on the minimap, under the kart dot and its tail
LAYER_TOP = "top"      # over every Classic element

# The Classic plates an element may read the rect of, and replace: the g-meter dial (top-right),
# the minimap (bottom-right), the speed/Δ readout (bottom-left) and the lap strip (top-left). The
# honesty stamp under the strip is in `ElementContext.plates` as "stamp" (to be avoided) but is
# never replaceable.
PLATES = ("gmeter", "map", "readout", "strip")
STAMP = "stamp"
# The kart dot's outer radius at a 1080 px short side (scaled by `k`): its dark halo ring
# (`export_video._MapInset.paint`, radius 5.2 with a 1.6 pen) — what a `paint_map(over=True)` may
# never cover.
DOT_R = 6.0


@dataclass(frozen=True)
class ElementContext:
    """What an element may read once per export. `map_point` projects local metres into frame
    pixels exactly as the minimap draws its line and kart dot; None when the export has no map."""
    session: Any
    spec: Any                  # export_video.ExportSpec
    out_w: int
    out_h: int
    fps: float
    k: float                   # stroke scale: 1.0 at a 1080 px short side, as the Classic overlay
    map_rect: QRectF
    map_point: Callable[[float, float], QPointF] | None
    plates: Mapping[str, QRectF]   # PLATES + STAMP -> rect, read-only copies
    safe: QRectF                   # the frame inset by the Classic margin


class OverlayElement(Protocol):
    name: str
    layer: str
    # optional: `replaces: tuple[str, ...]` — Classic PLATES this element stands in for

    def prepare(self, ctx: ElementContext) -> bool: ...

    def paint(self, p: QPainter, vals: Any) -> None: ...


class OverlayElementError(RuntimeError):
    """An element raised. Carries the element's name so the failure box says what broke."""


class OverlayLayoutError(OverlayElementError):
    """A style's layout has no place for something it must show in this frame. Its message starts
    with `LAYOUT_PREFIX`, which the export dialog's failure box reads."""


LAYOUT_PREFIX = "overlay layout: "


def build(names, ctx: ElementContext, notes: list[str] | None = None) -> list[OverlayElement]:
    """The elements `names` asks for that have something to draw in this export, prepared once.
    An element that sits out with a `skipped` sentence adds it to `notes`."""
    out = []
    for name in names:
        factory = REGISTRY.get(name)
        if factory is None:
            raise OverlayElementError(f"unknown overlay element {name!r} (known: {sorted(REGISTRY)})")
        element = factory()
        bad = set(getattr(element, "replaces", ())) - set(PLATES)
        if bad:
            raise OverlayElementError(f"the overlay element {name!r} cannot replace {sorted(bad)}: "
                                      f"only the Classic plates {list(PLATES)} can be replaced")
        try:
            ready = element.prepare(ctx)
        except layout.LayoutError as exc:
            raise OverlayLayoutError(f"{LAYOUT_PREFIX}{exc}") from exc
        except Exception as exc:  # noqa: BLE001 — re-raised, named
            raise OverlayElementError(f"the overlay element {name!r} failed to prepare: {exc}") from exc
        if not ready and getattr(element, "skipped", None) and notes is not None:
            notes.append(element.skipped)
        if ready:
            out.append(element)
            occupies = getattr(element, "occupies", None) or {}
            bad = set(occupies) - set(PLATES) - {STAMP}
            if bad:
                raise OverlayElementError(f"the overlay element {name!r} cannot occupy "
                                          f"{sorted(bad)}: only {[*PLATES, STAMP]}")
            if occupies:
                ctx = replace(ctx, plates=MappingProxyType(
                    {**ctx.plates, **{n: QRectF(r) for n, r in occupies.items()}}))
    return out


def stamp_rect(elements, classic: QRectF) -> QRectF:
    """Where the honesty stamp is painted: the last prepared element's `occupies[STAMP]`, else the
    Classic rect under the strip."""
    for element in reversed(elements):
        moved = (getattr(element, "occupies", None) or {}).get(STAMP)
        if moved is not None:
            return QRectF(moved)
    return classic


def replaced(elements) -> frozenset[str]:
    """The Classic plates the prepared `elements` stand in for: the painter skips these."""
    return frozenset(name for e in elements for name in getattr(e, "replaces", ()))


def paint_layer(elements, p: QPainter, vals, layer: str, dot: QPointF | None = None,
                over: bool = False) -> None:
    """Paint every element of `layer` for one frame, each between a save/restore so a pen or brush
    it leaves set cannot leak into the Classic elements painted after it. On `LAYER_MAP` an element
    with a `paint_map` gets the kart `dot` and both passes; a plain one only the pass under it."""
    for element in elements:
        if element.layer != layer:
            continue
        paint_map = getattr(element, "paint_map", None)
        if over and paint_map is None:
            continue
        p.save()
        try:
            if paint_map is not None and layer == LAYER_MAP:
                paint_map(p, vals, dot, over)
            else:
                element.paint(p, vals)
        except Exception as exc:  # noqa: BLE001 — re-raised, named
            raise OverlayElementError(f"the overlay element {element.name!r} failed to paint the "
                                      f"frame at {vals.t:.3f} s: {exc}") from exc
        finally:
            p.restore()


# The registry: element name -> factory. Imported last, because each element module reads the
# types above. One line per element.
from studio.overlay.broadcast import BroadcastElement, MapLabelsElement  # noqa: E402
from studio.overlay.corner_cards import CornerCardsElement  # noqa: E402
from studio.overlay.ghost import GhostElement  # noqa: E402

REGISTRY: dict[str, Callable[[], OverlayElement]] = {
    GhostElement.name: GhostElement,
    CornerCardsElement.name: CornerCardsElement,
    BroadcastElement.name: BroadcastElement,
    MapLabelsElement.name: MapLabelsElement,
}
