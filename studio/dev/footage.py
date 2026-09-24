"""The real recordings the real-footage checks and the golden dump run on: the variables, the defaults.

`PACER_GOLDEN_MP4` names THE recording. The golden dump (`studio.dev.golden_session_dump`) fingerprints
it, and the real-media checks in `tests/` that hold for ANY recording (`test_export_video`'s real
renders, the two Pedal-band checks, the primary of `test_video_view_compare`'s cross-recording proof)
run on it. Before this, the export checks read `PACER_REAL_MP4` and the compare proof hard-coded
`~/Desktop/D24` behind `PACER_D24_MEDIA=1`, so pointing "the real-footage checks" at a recording took
three spellings, and one of the three defaults named the chapter a dev tool had overwritten.

The one check that compares two DIFFERENT recordings also reads `PACER_GOLDEN_REF_MP4`.

THE DEFAULTS NAME THE DESKTOP WORKING SET (G2, 2026-09-23). D24 left the machine on 2026-09-19, and
until the published tables were re-measured on the recordings the owner chose in its place (T16b)
the defaults stayed on D24 on purpose, so every check reported SKIPPED rather than quietly measuring
something else. With the tables re-based, each default now names the working-set recording the
check NEEDS; `tests/_footage.py` says which check needs what. Every recording here is the owner's,
strictly read-only, and opened for reading only. A variable still overrides its default, and a
variable naming a file that is not there fails rather than skips.

`PACER_TIMING_DIR` names where the official TIMING SHEETS are, the ground truth of the accuracy table's
re-measured rows (`timing_dir`).

Stdlib only; it resolves paths and opens nothing but a linked worktree's one-line `.git` pointer.
"""
from __future__ import annotations

import os
import re

# THE WORKING SET (the owner's decision, 2026-09-23), each named by its first chapter; sibling
# discovery (`studio.chapters.discover_siblings`) finds the rest. Every Sandown recording is
# clockwise, and since Q2 Sandown Park is a built-in track (on the owner's own saved line), so a
# jailed load times it on the line his app uses instead of auto-fitting one.
# MK_18_09_26 is on the SAME circuit as D24 (Daytona Milton Keynes, 12 corners, ~1059 m) and is the
# only anticlockwise one: where a figure depends on the TRACK, it is D24's like-for-like stand-in.
DESKTOP = "~/Desktop"
SANDOWN_3H = "~/Desktop/Sandown 3h 2026/GX010064.MP4"   # primary: 3 chapters, 62 laps (was D24 0062)
SD_19_09 = "~/Desktop/SD_19_09_26/GX010068.MP4"         # secondary: 2 chapters, 36 laps (was D24 0060)
MK_18_09 = "~/Desktop/MK_18_09_26/GX010067.MP4"         # D24's circuit: 2 chapters, 19 laps

RECORDING_ENV = "PACER_GOLDEN_MP4"
# MK, because every check that reads this default holds on ANY recording, and MK serves all of their
# needs at once. It is CHAPTERED: the chaptered render needs a lap wholly inside a later chapter, and
# MK's chapter 2 holds some. It is D24's own circuit: the golden dump was the D24 gate, and what it
# fingerprints — corners, braking zones, coaching — follows the track, so on MK a dump covers what it
# covered on D24 (12 corners, not Sandown's 7). And it is the smallest chaptered recording of the set
# (15 GB), so the checks that load it whole load the least.
RECORDING_DEFAULT = MK_18_09

REFERENCE_ENV = "PACER_GOLDEN_REF_MP4"
# THE COMPARE PROOF'S PAIR, the one check that needs TWO recordings. They have to be of ONE track:
# `Session.set_reference_session` refuses a reference from anywhere else before pane B opens. MK
# cannot be half of the pair — it is the only recording of its track — so when PACER_GOLDEN_MP4 is
# unset the proof's primary is SD_19_09, not RECORDING_DEFAULT, against Sandown 3h: the two stand
# where D24 0060 and 0062 stood as this pair. The reference is the three-chapter one, so pane B's
# lap-start seek has to resolve to a chapter that is not the first (it lands in GX020064).
PAIR_RECORDING_DEFAULT = SD_19_09
REFERENCE_DEFAULT = SANDOWN_3H


def resolve(env: str, default: str) -> tuple[str, bool]:
    """(`path`, `named`) for one footage variable. `named` is True when the operator set `env`
    themselves, so a caller can tell "the default is not on this machine" (a skip) from "the
    recording you pointed me at is not there" (a mistake worth failing on). Blank counts as unset."""
    raw = os.environ.get(env, "").strip()
    return os.path.expanduser(raw or default), bool(raw)


def recording_path() -> str:
    """The recording `PACER_GOLDEN_MP4` names, or its working-set default. Not checked for existence."""
    return resolve(RECORDING_ENV, RECORDING_DEFAULT)[0]


def reference_path() -> str:
    """The second recording `PACER_GOLDEN_REF_MP4` names, or its working-set default."""
    return resolve(REFERENCE_ENV, REFERENCE_DEFAULT)[0]


TIMING_ENV = "PACER_TIMING_DIR"
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def timing_dir(repo: str | None = None) -> str:
    """Where the official timing sheets are by default: `.claude/reference/timing/` of the MAIN
    checkout, gitignored. The sheets are lap CSVs `studio.dev.clubspeed` writes from a circuit's
    public heat pages — other drivers' lap times, so never committed, exactly like the D24
    transponder CSV. A linked worktree (every agent's) finds the main checkout through the
    `gitdir: <main>/.git/worktrees/<name>` line of its `.git` FILE, so a check reading a sheet runs
    from any checkout on the dev Mac and skips on a machine without one, CI included."""
    root = repo or _REPO
    gitfile = os.path.join(root, ".git")
    if os.path.isfile(gitfile):
        with open(gitfile, encoding="utf-8") as f:
            m = re.match(r"gitdir:\s*(.+?)[\\/]\.git[\\/]worktrees[\\/]", f.read().strip())
        if m:
            root = m.group(1)
    return os.path.join(root, ".claude", "reference", "timing")
