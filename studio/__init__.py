"""studio — Pacer, the local PySide6 + pyqtgraph desktop app for race-telemetry analysis.

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
# The version is DUPLICATED in two pyproject.toml files that cannot import this one (pyproject.toml
# names the .dmg; bindings/pacer/pyproject.toml is the bindings package). tests/test_version.py
# pins all three together — bump them in one commit.
#
# THE PRODUCT IS "Pacer", spelled one way everywhere a person reads it: the window and every
# dialog title (from here), the welcome screen, the About card, the Help prose, the macOS bundle
# (packaging/pacer.spec's CFBundleName and `Pacer.app`), the README and the landing page. It was
# "Pacer Studio" here, "Pacer" on the welcome screen and in the README, and "pacer" in running
# prose — three spellings of one name. tests/test_version.py pins the spelling across all of them.
# Two lowercase forms are NOT the name and stay: identifiers (the `pacer` package, the
# "~/Library/Application Support/pacer" folder, ".pacer.json" sidecars, the `app.pacer.studio`
# bundle id) and the share card's "pacer" logotype, a lowercase wordmark set in a graphic by design.
__version__ = "0.2.0"
APP_NAME = "Pacer"
