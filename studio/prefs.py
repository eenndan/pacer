"""User preferences — a tiny JSON store next to the session library.

Mirrors ``studio/library.py``'s persistence exactly: the same app-support directory
(``~/Library/Application Support/pacer``), the same monkeypatchable seam (``_app_support_dir``,
so the suite never touches the real file), and the same atomic write (temp file + ``os.replace``).
Kept separate from the library index (that file is a data catalogue; this is UI state).

A generic get/set dict of persisted UI choices: the speed display unit (``studio/units.py``),
the colour-blind palette, the last-opened folder, the excluded-strip toggle, the lap panel's
active tab, the grid-splitter sizes, the map key's collapse and the Library dialog's size. Every
read is guarded and defaults to the safe value, so a missing / corrupt file is never fatal (each
choice just starts at its default).
"""

from __future__ import annotations

import json
import os

from . import units

_FILENAME = "prefs.json"
_APP_DIR_NAME = "pacer"

VERSION = 1

# Preference keys.
SPEED_UNIT = "speed_unit"
# The accessible/colour-blind-safe semantic palette toggle (studio/theme.py). Stored as a bool;
# False (default) keeps the original red/green cues, True swaps in the blue/orange CB-safe axis.
COLORBLIND_PALETTE = "colorblind_palette"
# The last folder the user opened a recording from — so the Open dialog reopens where their footage
# lives instead of a useless default each session. Stored as an absolute path string; the accessor
# only returns it when it still exists on disk (an old drive gets unmounted), else "" (today's fallback).
LAST_DIR = "last_dir"
# Lap-panel layout state that survives a relaunch, so a user who tidied their layout finds it
# the way they left it:
#   * EXCLUDED_VISIBLE — whether the ⊘ excluded-laps strip (inside the Laps page) is shown at
#     all. Default True = shown (as its own collapsed one-liner). Coerced to bool.
#   * LAP_PANEL_TAB — the lap panel's active tab (Laps 0 · Corners 1 · Stats 2 · Coaching 3).
#     Default 0; anything out of range reads as 0 (never crashes, never a blank page).
#   * GRID_SIZES — the [main, left, right] grid-splitter sizes as three int lists, or None
#     until the user drags a splitter (the built-in defaults apply then). The view validates
#     shape/section counts before applying, so a stale/corrupt value falls back cleanly.
# (COACHING_COLLAPSED / COACHING_VISIBLE were retired with the under-table strips — coaching is
# a full tab now; stale keys in an existing prefs file are simply ignored.)
EXCLUDED_VISIBLE = "excluded_visible"
LAP_PANEL_TAB = "lap_panel_tab"
GRID_SIZES = "grid_sizes"
# Whether the map's floating "Map key" plate is collapsed to its title row. Default False =
# expanded (the key explains four painted glyphs a first-time user has no other way to read).
# Shipped, the collapse was per-MapView state, so a user who put the key away got it back on the
# next launch AND on every recording they opened in the same session — the plate is 46% of the map
# canvas's height at 1440x900, so that is the one layout choice on the map worth remembering.
# Coerced to bool, like EXCLUDED_VISIBLE.
MAP_KEY_COLLAPSED = "map_key_collapsed"
# The Library dialog's size as [width, height] in logical px, or absent until the user actually
# resizes it (so a later change to the dialog's own default still reaches everyone who never
# touched it). Shape-guarded on read; the dialog additionally clamps whatever comes back to the
# screen it is opening on, so a size saved on an external monitor can't open off-screen.
LIBRARY_SIZE = "library_size"


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). The single seam
    tests monkeypatch so the suite never touches the real prefs (mirrors library._app_support_dir)."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def prefs_path() -> str:
    """Absolute path to the prefs file (``<app-support>/pacer/prefs.json``). Resolves the
    app-support dir through ``_app_support_dir`` so tests that patch that seam are honoured. Does
    NOT create the directory — that happens lazily on the first write."""
    return os.path.join(_app_support_dir(), _FILENAME)


def load(path: str | None = None) -> dict:
    """Load the prefs dict. Any corruption (absent / unreadable / not JSON / not a dict) → an
    empty dict — a missing preference always falls back to its caller default. `path` defaults to
    ``prefs_path()``."""
    if path is None:
        path = prefs_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict, path: str | None = None) -> None:
    """Write the prefs dict atomically (temp file + ``os.replace``). Creates the app-support dir
    if missing. `path` defaults to ``prefs_path()``. Raises OSError on an unwritable destination."""
    if path is None:
        path = prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = dict(data)
    out["version"] = VERSION
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def get(key: str, default=None, path: str | None = None):
    """Read one preference, returning `default` when absent (or the file is missing/corrupt)."""
    return load(path).get(key, default)


def set(key: str, value, path: str | None = None) -> None:  # noqa: A001 — the natural verb here
    """Set one preference and persist it (load-modify-save). A write failure propagates; callers
    that must never disrupt the app guard it."""
    data = load(path)
    data[key] = value
    save(data, path)


def speed_unit(path: str | None = None) -> str:
    """The persisted speed unit, normalized (km/h default). The one accessor the app + views read
    so a stale/garbage stored value can never reach a formatter."""
    return units.normalize_unit(get(SPEED_UNIT, units.DEFAULT_UNIT, path))


def set_speed_unit(unit: str, path: str | None = None) -> None:
    """Persist the speed unit (normalized first)."""
    set(SPEED_UNIT, units.normalize_unit(unit), path)


def colorblind_palette(path: str | None = None) -> bool:
    """Whether the colour-blind-safe semantic palette is enabled (default False = the original
    red/green cues). A garbage stored value coerces to bool, so a corrupt file never crashes the
    toggle — it just reads as off."""
    return bool(get(COLORBLIND_PALETTE, False, path))


def set_colorblind_palette(on: bool, path: str | None = None) -> None:
    """Persist the colour-blind-safe palette toggle."""
    set(COLORBLIND_PALETTE, bool(on), path)


def lap_panel_tab(path: str | None = None) -> int:
    """The lap panel's persisted active tab (Laps 0 · Corners 1 · Stats 2 · Coaching 3).
    Anything non-int or out of range reads as 0 — a corrupt file never opens a blank page."""
    val = get(LAP_PANEL_TAB, 0, path)
    return int(val) if isinstance(val, int) and 0 <= val <= 3 else 0


def set_lap_panel_tab(index: int, path: str | None = None) -> None:
    """Persist the lap panel's active tab."""
    set(LAP_PANEL_TAB, int(index), path)


def grid_sizes(path: str | None = None) -> list | None:
    """The persisted [main, left, right] grid-splitter sizes (three lists of ints), or None
    when unset / malformed — the view then keeps its built-in defaults. Shape-guarded here;
    the view additionally checks each list against its splitter's section count."""
    val = get(GRID_SIZES, None, path)
    if (isinstance(val, list) and len(val) == 3
            and all(isinstance(s, list) and s for s in val)):
        return val
    return None


def set_grid_sizes(sizes: list, path: str | None = None) -> None:
    """Persist the grid-splitter sizes (the view emits them debounced after a drag)."""
    set(GRID_SIZES, [[int(v) for v in s] for s in sizes], path)


def excluded_visible(path: str | None = None) -> bool:
    """Whether the ⊘ excluded-laps strip is shown (default True — shown, as its own collapsed
    one-liner). Coerced to bool so a corrupt file never crashes the toggle."""
    return bool(get(EXCLUDED_VISIBLE, True, path))


def set_excluded_visible(on: bool, path: str | None = None) -> None:
    """Persist the excluded-strip visibility (the View-menu hide toggle)."""
    set(EXCLUDED_VISIBLE, bool(on), path)


def map_key_collapsed(path: str | None = None) -> bool:
    """Whether the map's "Map key" plate opens collapsed to its title row (default False =
    expanded). Coerced to bool so a corrupt file never crashes the map."""
    return bool(get(MAP_KEY_COLLAPSED, False, path))


def set_map_key_collapsed(collapsed: bool, path: str | None = None) -> None:
    """Persist the map key's collapse. Fully guarded — remembering the state of a decorative plate
    must never disrupt the map — so an unwritable prefs file is swallowed (mirrors
    ``set_last_dir`` / ``set_library_size``). MapView calls this from the plate's own click."""
    try:
        set(MAP_KEY_COLLAPSED, bool(collapsed), path)
    except OSError:
        pass


def library_size(path: str | None = None) -> tuple[int, int] | None:
    """The persisted Library-dialog size as ``(width, height)``, or None when unset / malformed —
    the dialog then uses its own default. Shape-guarded here (two positive real ints, bool
    rejected); the dialog clamps the result to the screen it opens on."""
    val = get(LIBRARY_SIZE, None, path)
    if (isinstance(val, list) and len(val) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in val)):
        return (val[0], val[1])
    return None


def set_library_size(width: int, height: int, path: str | None = None) -> None:
    """Persist the Library dialog's size. Fully guarded — remembering a window size must never
    disrupt the UI — so a non-numeric/non-positive size or an unwritable prefs file is swallowed
    (mirrors ``set_last_dir``)."""
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return
    if w <= 0 or h <= 0:
        return
    try:
        set(LIBRARY_SIZE, [w, h], path)
    except OSError:
        pass


def last_dir(path: str | None = None) -> str:
    """The persisted last-opened folder, or "" when unset / no longer a directory. Guarded so a
    stale value (an unplugged drive) never lands the Open dialog on a missing path — the caller then
    falls back to today's behaviour (the current recording's folder, or nowhere)."""
    val = get(LAST_DIR, "", path)
    if isinstance(val, str) and val and os.path.isdir(val):
        return val
    return ""


def set_last_dir(folder: str, path: str | None = None) -> None:
    """Persist the folder a recording was just opened from. Fully guarded — remembering the folder
    must never disrupt a load — so an empty/garbage value or an unwritable prefs file is swallowed."""
    if not isinstance(folder, str) or not folder:
        return
    try:
        set(LAST_DIR, folder, path)
    except OSError:
        pass
