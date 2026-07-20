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
# The last folder the user opened a recording from — so the Open dialog reopens where their footage
# lives instead of a useless default each session. Stored as an absolute path string; the accessor
# only returns it when it still exists on disk (an old drive gets unmounted), else "" (today's fallback).
LAST_DIR = "last_dir"
# Left-column declutter (the "calm default"): three UI-state bools that survive a relaunch, so a
# user who tidied their layout finds it the way they left it. All default to the calm posture:
#   * COACHING_COLLAPSED — the coaching (Opportunities) panel body starts collapsed to its header
#     bar (the summary line still reads as the re-open affordance). Default True = collapsed.
#   * COACHING_VISIBLE — whether the whole coaching panel (header included) is shown. Default True =
#     shown-but-collapsed, so the calm default still exposes the one-click re-open header.
#   * EXCLUDED_VISIBLE — whether the ⊘ excluded-laps strip is shown at all. Default True = shown
#     (as its own collapsed one-liner). A garbage stored value coerces to bool (never crashes).
COACHING_COLLAPSED = "coaching_collapsed"
COACHING_VISIBLE = "coaching_visible"
EXCLUDED_VISIBLE = "excluded_visible"


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


def coaching_collapsed(path: str | None = None) -> bool:
    """Whether the coaching (Opportunities) panel starts COLLAPSED to its header bar (default True —
    the calm default). A garbage stored value coerces to bool, so a corrupt file never crashes the
    toggle — it just reads as collapsed."""
    return bool(get(COACHING_COLLAPSED, True, path))


def set_coaching_collapsed(on: bool, path: str | None = None) -> None:
    """Persist the coaching-panel collapsed state."""
    set(COACHING_COLLAPSED, bool(on), path)


def coaching_visible(path: str | None = None) -> bool:
    """Whether the whole coaching (Opportunities) panel is shown (default True — shown, but
    collapsed by default, so the calm default still exposes the one-click re-open header). Coerced
    to bool so a corrupt file never crashes the toggle."""
    return bool(get(COACHING_VISIBLE, True, path))


def set_coaching_visible(on: bool, path: str | None = None) -> None:
    """Persist the coaching-panel visibility (the View-menu hide toggle)."""
    set(COACHING_VISIBLE, bool(on), path)


def excluded_visible(path: str | None = None) -> bool:
    """Whether the ⊘ excluded-laps strip is shown (default True — shown, as its own collapsed
    one-liner). Coerced to bool so a corrupt file never crashes the toggle."""
    return bool(get(EXCLUDED_VISIBLE, True, path))


def set_excluded_visible(on: bool, path: str | None = None) -> None:
    """Persist the excluded-strip visibility (the View-menu hide toggle)."""
    set(EXCLUDED_VISIBLE, bool(on), path)


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
