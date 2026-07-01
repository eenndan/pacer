"""Speed unit — the single source of truth for km/h ↔ mph display.

Pacer-free AND Qt-free: pure math + a couple of string helpers, so it can be imported by the
theme layer, the pure-numpy map render, and the offscreen tests alike. All INTERNAL/analysis
values stay km/h (the app computes and stores km/h everywhere); this module converts only at the
DISPLAY boundary. Distances are out of scope — they stay in metres.

Public surface:
  * ``KMH``, ``MPH`` — the two unit ids (also the persisted string values);
  * ``convert_speed(value_kmh, unit)`` — the km/h→display-unit number (no rounding);
  * ``speed_label(unit)`` — the unit's display string ("km/h" / "mph");
  * ``format_speed(value_kmh, unit, decimals=…)`` — the "<n> <unit>" display string.

The current unit is a preference (see ``studio/prefs.py``); this module is stateless — callers
pass the unit in, so it stays trivially testable and free of import-time side effects.
"""

from __future__ import annotations

# 1 km/h = 0.621371 mph. The ONE conversion constant — never scatter `* 0.621371` across the views.
KMH_TO_MPH = 0.621371

KMH = "kmh"
MPH = "mph"

# The two valid unit ids; anything else falls back to km/h (the default, no behaviour change).
UNITS = (KMH, MPH)
DEFAULT_UNIT = KMH

_LABEL = {KMH: "km/h", MPH: "mph"}


def normalize_unit(unit: str | None) -> str:
    """Coerce any input to a valid unit id, defaulting to km/h. Keeps every display site safe
    against a stale/garbage persisted value."""
    return unit if unit in UNITS else DEFAULT_UNIT


def speed_label(unit: str | None) -> str:
    """The unit's display string: "km/h" or "mph"."""
    return _LABEL[normalize_unit(unit)]


def convert_speed(value_kmh: float, unit: str | None) -> float:
    """Convert a km/h value to the display unit's number (unrounded). Identity for km/h; ×factor
    for mph. Display formatters round; analysis math must NOT call this (stays km/h/SI)."""
    return value_kmh * KMH_TO_MPH if normalize_unit(unit) == MPH else value_kmh


def format_speed(value_kmh: float, unit: str | None, *, decimals: int = 0) -> str:
    """The "<n> <unit>" display string for a km/h value in the chosen unit (e.g. 100 km/h → the
    mph string "62 mph"). `decimals` controls the rounding (0 for the hero readout, 1 for the
    lap/corner tables). Callers that need the number alone use `convert_speed`."""
    return f"{convert_speed(value_kmh, unit):.{decimals}f} {speed_label(unit)}"
