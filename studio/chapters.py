"""GoPro chaptered-recording discovery + the global<->chapter time mapping.

A long GoPro recording is split, at a file-SIZE limit (not at a lap), into a series of
"chapters" that all share one 4-digit RECORDING number and increment a 2-digit CHAPTER
index. They are contiguous in time: chapter k+1's media clock starts exactly where chapter
k's ended. To treat the whole recording as ONE session we

  1. discover the sibling chapters of an opened file (same recording number, same folder,
     ordered ascending by chapter index), and
  2. lay them on ONE global time axis: chapter i covers global [offset_i, offset_i+dur_i),
     where offset_i is the cumulative duration of the chapters before it.

This module is PURE PYTHON and `pacer`-free: it only parses filenames and does the offset
arithmetic, so it is trivially unit-testable without a telemetry file. session.py owns the
actual telemetry chaining (via the C++ `SequentialGPSSource`); the durations that build the
offset table come from the GPMF/media (passed in by the caller).

GoPro naming (confirmed on disk): ``GX<CC><NNNN>.MP4`` — a 2-letter prefix (GX/GH/GP), then
the 2-digit chapter index ``CC``, then the 4-digit recording number ``NNNN``, then ``.MP4``.
Example recording 0060: ``GX010060.MP4`` (chapter 1), ``GX020060.MP4`` (2), ``GX030060.MP4``
(3). Siblings of one recording share ``NNNN`` and are ordered ascending by ``CC``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# GX/GH/GP + 2-digit chapter + 4-digit recording + .MP4 (case-insensitive on prefix/ext).
# Anchored to the whole basename so an unrelated file (e.g. "GX0100600.MP4") never matches.
_GOPRO_RE = re.compile(r"^(G[XHLP])(\d{2})(\d{4})\.MP4$", re.IGNORECASE)


@dataclass(frozen=True)
class GoProName:
    """The parsed parts of a GoPro chaptered filename."""

    prefix: str       # "GX" / "GH" / "GP" / "GL" (upper-cased)
    chapter: int      # CC — the 2-digit chapter index (1-based on GoPro)
    recording: int    # NNNN — the 4-digit recording number shared by siblings
    ext: str          # the original extension as written on disk (e.g. ".MP4")


def parse_gopro_name(path: str) -> GoProName | None:
    """Parse a GoPro chaptered filename, or None if it isn't one.

    Only the BASENAME matters; any directory is ignored. Robust to the GX/GH/GP/GL prefixes
    and to lowercase ``.mp4``. Returns None for non-GoPro names (e.g. the bundled
    ``hero6.mp4`` sample) so callers fall back to single-file behaviour."""
    base = os.path.basename(path)
    m = _GOPRO_RE.match(base)
    if not m:
        return None
    prefix, cc, nnnn = m.group(1), m.group(2), m.group(3)
    ext = os.path.splitext(base)[1]
    return GoProName(prefix=prefix.upper(), chapter=int(cc), recording=int(nnnn), ext=ext)


def discover_siblings(path: str) -> list[str]:
    """All chapter files of the SAME recording as `path`, in the SAME folder, ordered ascending
    by chapter index. The returned list always INCLUDES `path` itself.

    Grouping rule: same recording number ``NNNN`` AND same prefix as the opened file, in the
    opened file's directory. Different recording numbers are never mixed. A single-chapter
    recording (no siblings on disk) returns just ``[path]`` — so the caller can always chain
    the result and a lone chapter loads exactly as today.

    Returns ``[path]`` unchanged if the name isn't a GoPro chaptered name (e.g. the sample
    clip), so this is safe to call on any opened file."""
    info = parse_gopro_name(path)
    if info is None:
        return [path]
    folder = os.path.dirname(os.path.abspath(path))
    try:
        entries = os.listdir(folder)
    except OSError:
        return [path]

    siblings: list[tuple[int, str]] = []
    for name in entries:
        sib = parse_gopro_name(name)
        if sib is None:
            continue
        # Same recording AND same prefix (a GH/GP from the same shoot is a different stream).
        if sib.recording == info.recording and sib.prefix == info.prefix:
            siblings.append((sib.chapter, os.path.join(folder, name)))

    if not siblings:  # shouldn't happen (path itself parses), but be defensive
        return [path]
    # Ascending by chapter index; dedupe on the resolved absolute path.
    siblings.sort(key=lambda t: t[0])
    ordered, seen = [], set()
    for _, full in siblings:
        key = os.path.abspath(full)
        if key not in seen:
            seen.add(key)
            ordered.append(full)
    return ordered


def recording_label(paths: list[str]) -> str:
    """A short human label for a (possibly multi-chapter) session, e.g.
    ``"recording 0060 · 3 chapters"`` or ``"recording 0060"`` for a lone chapter, or the bare
    filename for a non-GoPro clip. Used in the window title / a status label so the user can
    see they're looking at a chained recording."""
    if not paths:
        return ""
    info = parse_gopro_name(paths[0])
    if info is None:
        return os.path.basename(paths[0])
    rec = f"recording {info.recording:04d}"
    n = len(paths)
    return f"{rec} · {n} chapters" if n > 1 else rec


@dataclass(frozen=True)
class Chapter:
    """One chapter on the global time axis: its file, its 0-based media duration, and its
    global start offset (cumulative duration of all earlier chapters)."""

    path: str
    duration: float   # seconds — the chapter's own (0-based) media duration
    offset: float     # seconds — this chapter's global start = sum of prior durations
    index: int        # position in the ordered chapter list (0-based)


class ChapterMap:
    """The ordered chapter list + cumulative offsets, and the global<->chapter mapping the
    video layer drives source-switching with.

    chapter i covers global ``[offset_i, offset_i + dur_i)``. Total session duration is the
    sum of the per-chapter durations. The mapping is pure arithmetic on the offset table:

      * ``chapter_at(global_t)`` -> chapter index i with offset_i <= t < offset_{i+1}
        (clamped to the last chapter at/after the end so a seek to the very end is valid).
      * ``to_local(global_t)``   -> (i, local_t) where local_t = global_t - offset_i.
      * ``to_global(i, local_t)``-> offset_i + local_t.

    Built from the ordered sibling paths + each chapter's media duration (from the GPMF/media,
    supplied by the caller — this module stays pacer-free)."""

    def __init__(self, paths: list[str], durations: list[float]):
        if len(paths) != len(durations):
            raise ValueError("paths and durations must align")
        if not paths:
            raise ValueError("ChapterMap needs at least one chapter")
        self.chapters: list[Chapter] = []
        offset = 0.0
        for i, (p, d) in enumerate(zip(paths, durations, strict=True)):
            d = max(float(d), 0.0)
            self.chapters.append(Chapter(path=p, duration=d, offset=offset, index=i))
            offset += d
        self.total_duration = offset

    def __len__(self) -> int:
        return len(self.chapters)

    @property
    def is_multi(self) -> bool:
        return len(self.chapters) > 1

    @property
    def paths(self) -> list[str]:
        return [c.path for c in self.chapters]

    def chapter_at(self, global_t: float) -> int:
        """Index of the chapter containing global time `global_t`. Times below 0 map to the
        first chapter; times at/after the total map to the LAST chapter (so seeking to the
        exact end still resolves to a real chapter+offset)."""
        if global_t <= 0.0:
            return 0
        for c in self.chapters:
            if global_t < c.offset + c.duration:
                return c.index
        return len(self.chapters) - 1

    def to_local(self, global_t: float) -> tuple[int, float]:
        """(chapter_index, local_time_within_chapter) for a global time. local_t is clamped to
        ``[0, chapter_duration]`` so it's always a valid seek target inside the chosen file. A
        chapter with an UNKNOWN duration (0.0 — e.g. a lone file whose media duration wasn't
        supplied) is not upper-clamped, so seeking still works (global == local there)."""
        i = self.chapter_at(global_t)
        c = self.chapters[i]
        local = max(global_t - c.offset, 0.0)
        if c.duration > 0.0:
            local = min(local, c.duration)
        return i, local

    def to_global(self, index: int, local_t: float) -> float:
        """Global time for a local time within chapter `index`."""
        c = self.chapters[index]
        return c.offset + local_t
