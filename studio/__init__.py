"""pacer studio — a local PySide6 + pyqtgraph desktop app for race-telemetry analysis.

Greenfield UI on top of the existing C++ `pacer` core (via its nanobind bindings).
Single-language Python, optimized for fast iteration. Panels:
  * MapView   — track trace + draggable start/sector timing lines (local meters).
  * PlotsView — speed-vs-distance + lap-vs-best delta for the selected laps.
  * VideoView — the GoPro .mp4, synced both ways with the telemetry.
  * LapTable  — lap times / distances; selection drives the plots.

Run:  pixi run studio [GoPro.MP4 ...]   (or: python -m studio [files])
"""

# Canonical version + wordmark — the single source of truth for both (the About dialog reads
# these, the PyInstaller spec regex-reads __version__, and every user-facing title/label imports
# APP_NAME via `from . import APP_NAME`). Keep this a leaf: no submodule imports here, so
# `from . import APP_NAME` can never cycle.
__version__ = "0.1.0"
APP_NAME = "Pacer Studio"
