"""User preferences — a tiny JSON store next to the session library.

Mirrors ``studio/library.py``'s persistence exactly: the same app-support directory
(``~/Library/Application Support/pacer``), the same monkeypatchable seam (``_app_support_dir``,
so the suite never touches the real file), and the same atomic write (temp file + ``os.replace``).
Kept separate from the library index (that file is a data catalogue; this is UI state).

Today it holds one key — the speed display unit (``studio/units.py``) — but it's a generic
get/set dict so future toggles can join it without a new file. Every read is guarded and defaults
to the safe value, so a missing / corrupt file is never fatal (the toggle just starts at km/h).
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
