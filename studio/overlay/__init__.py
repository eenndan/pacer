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

A FAILING ELEMENT FAILS THE EXPORT, LOUDLY, BY NAME. Skipping it would hand the user a file that is
missing what they asked for with nothing saying so; catching it per frame would hide a defect that
is identical on every frame. `OverlayElementError` is a RuntimeError, which the render path does not
retry on the software encoder (a paint defect is not an encoder failure) and the export dialog shows
as its failure box.

Qt like the exporter (it paints with QPainter); pacer-free like every view.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter

LAYER_MAP = "map"      # on the minimap, under the kart dot and its tail
LAYER_TOP = "top"      # over every Classic element


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


class OverlayElement(Protocol):
    name: str
    layer: str

    def prepare(self, ctx: ElementContext) -> bool: ...

    def paint(self, p: QPainter, vals: Any) -> None: ...


class OverlayElementError(RuntimeError):
    """An element raised. Carries the element's name so the failure box says what broke."""


def build(names, ctx: ElementContext) -> list[OverlayElement]:
    """The elements `names` asks for that have something to draw in this export, prepared once."""
    out = []
    for name in names:
        factory = REGISTRY.get(name)
        if factory is None:
            raise OverlayElementError(f"unknown overlay element {name!r} (known: {sorted(REGISTRY)})")
        element = factory()
        try:
            ready = element.prepare(ctx)
        except Exception as exc:  # noqa: BLE001 — re-raised, named
            raise OverlayElementError(f"the overlay element {name!r} failed to prepare: {exc}") from exc
        if ready:
            out.append(element)
    return out


def paint_layer(elements, p: QPainter, vals, layer: str) -> None:
    """Paint every element of `layer` for one frame, each between a save/restore so a pen or brush
    it leaves set cannot leak into the Classic elements painted after it."""
    for element in elements:
        if element.layer != layer:
            continue
        p.save()
        try:
            element.paint(p, vals)
        except Exception as exc:  # noqa: BLE001 — re-raised, named
            raise OverlayElementError(f"the overlay element {element.name!r} failed to paint the "
                                      f"frame at {vals.t:.3f} s: {exc}") from exc
        finally:
            p.restore()


# The registry: element name -> factory. Imported last, because each element module reads the
# types above. One line per element.
from studio.overlay.ghost import GhostElement  # noqa: E402

REGISTRY: dict[str, Callable[[], OverlayElement]] = {
    GhostElement.name: GhostElement,
}
