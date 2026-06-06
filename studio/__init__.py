"""pacer studio — a local PySide6 + pyqtgraph desktop app for race-telemetry analysis.

Greenfield UI on top of the existing C++ `pacer` core (via its nanobind bindings).
Single-language Python, optimized for fast iteration. Panels:
  * MapView   — track trace + draggable start/sector timing lines (local meters).
  * PlotsView — speed-vs-distance + lap-vs-best delta for the selected laps.
  * VideoView — the GoPro .mp4, synced both ways with the telemetry.
  * LapTable  — lap times / distances; selection drives the plots.

Run:  pixi run studio [GoPro.MP4 ...]   (or: python -m studio [files])
"""
