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
# The version is DUPLICATED in two pyproject.toml files that cannot import this one (pyproject.toml
# names the .dmg; bindings/pacer/pyproject.toml is the bindings package). tests/test_version.py
# pins all three together — bump them in one commit.
#
# THE NAME CONVENTION — three forms, each with one job. tests/test_version.py enforces all three.
#   * FORMAL  "Pacer Studio" (APP_NAME): the product's identity. Use it for the macOS bundle
#     (packaging/pacer.spec's CFBundleName / CFBundleDisplayName, `Pacer Studio.app`, the
#     `Pacer-Studio-<v>.dmg`), the application and display names, every window and dialog title,
#     the About card, the landing page's product name and the header of every EXPORTED file,
#     because an export carries the product's identity outside the app. Always this constant,
#     never a retyped literal.
#   * SHORT   "Pacer", capitalised: the product in a SENTENCE ("Pacer never looks the weather
#     up"), the welcome screen's headline and the README's title.
#   * WORDMARK "pacer", lowercase: the share card's logotype, set as a graphic. Nowhere else.
# Lowercase "pacer" in a sentence is NOT a fourth form. It was the actual inconsistency (about 30
# user-visible sentences, fixed in U5). Identifiers are not names and keep their own spelling: the
# `pacer` package, "~/Library/Application Support/pacer", ".pacer.json" sidecars and the
# `app.pacer.studio` bundle id.
__version__ = "0.2.0"
APP_NAME = "Pacer Studio"
