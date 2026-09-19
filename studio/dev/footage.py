"""The real recording every recording-agnostic real-footage check runs on: one variable, one default.

`PACER_GOLDEN_MP4` names it. The golden dump (`studio.dev.golden_session_dump`) fingerprints it, and
the real-media checks in `tests/` that hold for ANY recording (`test_export_video`'s real renders,
`test_video_view_compare`'s cross-recording proof) run on it. Before this, the export checks read
`PACER_REAL_MP4` and the compare proof hard-coded `~/Desktop/D24` behind `PACER_D24_MEDIA=1`, so
pointing "the real-footage checks" at a recording took three spellings, and one of the three
defaults named the chapter a dev tool had overwritten.

The one check that compares two DIFFERENT recordings also reads `PACER_GOLDEN_REF_MP4`.

THE DEFAULTS STILL NAME D24, WHICH IS GONE (2026-09-19). That is deliberate. The published figures
were measured on D24, and which recording they move to is an open decision (backlog T16): a default
quietly re-pointed at another recording would change what every one of those numbers means. So the
default stays where it was and its absence is REPORTED instead: a test that needs it is registered
with CTest as SKIPPED, by name (`tests/_footage.py`), and the golden dump refuses loudly.

Stdlib only; it resolves paths and opens nothing.
"""
from __future__ import annotations

import os

RECORDING_ENV = "PACER_GOLDEN_MP4"
# Chapter 2, not 1: GX010060.MP4 is the 2.4 MB JSON a dev tool wrote over 11.9 GB of footage.
RECORDING_DEFAULT = "~/Desktop/D24/GX020060.MP4"

REFERENCE_ENV = "PACER_GOLDEN_REF_MP4"
REFERENCE_DEFAULT = "~/Desktop/D24/GX010062.MP4"


def resolve(env: str, default: str) -> tuple[str, bool]:
    """(`path`, `named`) for one footage variable. `named` is True when the operator set `env`
    themselves, so a caller can tell "the default is not on this machine" (a skip) from "the
    recording you pointed me at is not there" (a mistake worth failing on). Blank counts as unset."""
    raw = os.environ.get(env, "").strip()
    return os.path.expanduser(raw or default), bool(raw)


def recording_path() -> str:
    """The recording `PACER_GOLDEN_MP4` names, or its D24 default. Not checked for existence."""
    return resolve(RECORDING_ENV, RECORDING_DEFAULT)[0]


def reference_path() -> str:
    """The second recording `PACER_GOLDEN_REF_MP4` names, or its D24 default."""
    return resolve(REFERENCE_ENV, REFERENCE_DEFAULT)[0]
