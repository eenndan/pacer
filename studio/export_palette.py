"""The BURN-OVER-FOOTAGE palette: the vivid, opaque colours every overlay burned into the exported
MP4 is drawn in, as opposed to the dim-on-dark live tokens in `theme.C`.

WHY IT IS ITS OWN MODULE. It has two owners that cannot import each other. `export_video`
composites the frame; `gmeter_overlay` paints the dial into that frame (and drives the live
on-screen widget), and `export_video` imports `gmeter_overlay` — so a `from .export_video import
EXPORT` there would close an import cycle. The five colours the dial needs were therefore a second,
hand-copied set of literals, carrying the comment "Kept local to mirror export_video.EXPORT without
importing it" and kept in step by hand. Five hex values with two definitions is a drift waiting for
a colour change; this is the one definition both read.

Not `theme.py`: these are not design-system tokens. Nothing on screen is drawn in them, none of
them is palette-swappable — the one axis that IS (ahead/behind) is chosen at draw time by
`export_video.export_semantic_pair`, so a colour-blind user's exported clip follows their choice —
and they exist precisely BECAUSE the live tokens wash out over bright footage.

Pacer-free and Qt-free: plain hex strings, turned into a QColor at the paint boundary by
`theme.qcolor`.
"""

from __future__ import annotations


class EXPORT:
    """Vivid, opaque, export-tuned colours for burning overlays onto BRIGHT footage. Separate from
    the live theme tokens (which are dim-on-dark). Hex strings; use these in the composite only."""

    # text / structure
    text = "#FFFFFF"            # primary readout text — pure white (max contrast over footage)
    text_dim = "#E6EAF0"        # secondary text (units / labels) — near-white, still bright
    halo = "#0A0C10"            # the dark outline/shadow colour under every bright element
    # accent (amber) — brighter + fully saturated vs the theme's #F5A623
    accent = "#FFB21E"          # primary accent: lap line, g-dial envelope, lap-strip fill, ★ BEST
    accent_bright = "#FFD34D"   # highlight (g dot glow, marker ring)
    # semantics — PUNCHY, fully-saturated ahead/behind (theme's #5DD6A0/#E8746B are too soft here)
    ahead = "#26E07A"           # ahead / gaining — vivid green
    behind = "#FF4D4D"          # behind / losing — vivid red
    neutral = "#FFFFFF"         # dead-even Δ — white (no semantic colour)
    marker = "#FF5A36"          # map current-position marker — hot coral (pops on green/grey)
    grid = "#FFFFFF"            # g-dial rings / crosshair — white at moderate alpha (set per use)
