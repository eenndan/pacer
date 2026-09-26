"""Session library: a local index of analyzed recordings (F8) with PB progression.

Every successful load upserts the opened recording into one JSON index in the macOS app-support
dir; the dialog lists every analyzed recording, re-opens any, and draws a per-track PB chart.
PACER-FREE: pure path resolution, schema validation and atomic JSON I/O (the analyzed values
arrive as a plain dict from ``Session.library_entry()``).

fingerprint = (GoPro prefix, recording number) so every chapter of a recording maps to one
entry — neither the path list nor the media duration is stable across a single-chapter vs a full
chaptered open of the SAME recording. Because every chapter subset of a recording upserts that one
row, a row is kept by the chapters its measurement COVERED: an open of fewer of them never
displaces it (``upsert``).

Schema (version 4) — one JSON object::

    {"version": 4,
     "entries": [
       {"fingerprint": "GX0062",            # the chapter-invariant identity key (see above)
        "stem":        "GX010062",          # first-chapter stem, for display
        "track":       <registry track name or null>,
        "date":        "YYYY-MM-DD" | null,  # GPS9 wall-clock date (Session.session_date)
        "lap_count":   <int>,                # valid lap count — THE SAMPLE both time columns
                                             #   below are a minimum over (see the note)
        "best":        <float seconds> | null,    # best lap time
        "theoretical": <float seconds> | null,    # Session.theoretical_best — the IDEAL lap
                                             #   (v3 meaning; see the v2→v3 note below)
        "ideal_version": <int> | null,       # corner_model.IDEAL_VERSION that measured
                                             #   `theoretical`; null = written before rows were
                                             #   stamped (see "A ROW IS A MEASUREMENT" below)
        "verified":    <bool>,               # session.timing_verified — a TRUSTED start/finish line
        "degraded":    <bool>,               # session.timing_quality.degraded — ESTIMATED absolute timing
        "dropout":     <bool>,               # the session's best lap (or any valid lap) had a GPS dropout
        "paths":       ["/abs/GX010062.MP4", ...]}, # the chapter file path(s) as opened (absolute)
       ...]}

``theoretical`` CHANGED MEANING at v3, which is why the schema moved. Up to v2 it was the sum of
the session-best sector splits — and since sector lines default to none, and a lap with no sector
line is one sub-sector whose split is its lap time, every entry any user has on disk holds a value
byte-identical to its own ``best``. From v3 it is ``Session.theoretical_best``, the corner/straight
partition composite: a real target, faster than the best lap by 1.37 s on D24 one chapter and
1.49 s on three. A stored v2 number cannot be reinterpreted as a v3 one, and showing the two in one
column would silently mix definitions, so the v2→v3 migration NULLS the field (see ``_migrate``) —
every entry is kept, only that one value is retired, and it returns for real the next time the
recording is opened.

``lap_count`` IS PART OF THE ANSWER, NOT METADATA. Both ``best`` and ``theoretical`` are MINIMA
over the session's laps, so both fall as a session gets longer, and a table that ranks either
without showing the count ranks session length as much as pace. Measured over random subsets of
the clean laps of the owner's five working-set recordings, per doubling of lap count:
``theoretical`` falls 0.742 / 0.273 / 0.160 / 0.178 / 0.490 s and ``best`` falls 0.785 / 0.093 /
0.087 / 0.082 / 0.194 s, recording for recording — the ideal is the more sample-dependent of the
two on four of the five, and on the fifth (Sandown 3h's first chapter, the first hour of a 3-hour session)
the best lap moves MORE, which is why the dialog shows the count for the ROW rather than qualifying
one column. The sharpest real instance is in this index, as
stored before #300: Sandown chapter 1 (23 laps) stores 47.941 and Sandown chapters 1–3 (59 laps)
stores 47.375 — 0.57 s apart, same driver, same day, same track, and nothing between them but how
many laps were loaded. ``studio/library_dialog.py`` renders it as the ``Laps`` column; see ``studio/corner_model.py IdealSample`` for the full table.
It is the count of VALID laps, while the ideal is minimised over the CLEAN ones (valid, no GPS
dropout) — the same number on all five of the owner's recordings, and an over-statement by the
dropout count on a recording that has one.

A ROW IS A MEASUREMENT, taken by whichever build last opened the recording, and it is re-taken only
when the recording is opened again: rows without their footage (a disconnected drive) are never
recomputed. So ``ideal_version`` records which ideal-lap maths measured ``theoretical``, and the
dialog mutes a row from any other version (``ideal_stale``). It is an optional field, not a schema
bump: an absent or unusable stamp reads as "older", which is what every row written before it is
(QA NEW-8 / LOOK-4: Sandown 19 Sep's row said 0:46.063 while its Stats page said 0:46.196). The
BEST lap needs no stamp: re-measured on the owner's four present recordings under one build, every
stored ``best`` and ``lap_count`` came back bit-identical while three ideals moved 0.004-0.133 s,
because lap timing is held bit-identical across builds by the golden equivalence gate. A PB
comparison across rows written by different builds is therefore sound, and one across ideals is not.

The three TRUST flags (``verified``/``degraded``/``dropout``, schema v2) let the PB progression
EXCLUDE an untrustworthy "best": a PROVISIONAL start line (``not verified``) or a data-quality-
DEGRADED clock (``degraded``) references its lap number to something arbitrary/estimated, so it
must never set or beat a personal best; a GPS-``dropout`` best is less reliable, so it is kept but
also excluded from the PB set (a dropout lap's time can't be trusted to set a PB either). The
library TABLE still shows every session (with a trust indicator); only the PB CHART + PB logic use
the trustworthy subset (each entry filtered by ``is_trustworthy``). See ``_TRUST_UNKNOWN`` for the
v1→v2 back-compat
default that keeps a pre-existing (flag-less) PB history included.

Load self-heals WITHOUT destroying durable history:
  * a single bad entry is dropped (count logged), the rest kept (so the next ``save``, which
    rewrites only the survivors, doesn't lose all history);
  * an OLDER on-disk ``version`` is MIGRATED forward (``_migrate``) — never discarded — so the
    first run after a future schema bump preserves every analyzed recording;
  * a NEWER on-disk ``version`` (a downgrade) is loaded BEST-EFFORT (keep the entries it can, ignore
    unknown fields) and the newer file is NOT destructively rewritten in place;
  * an OLDER file whose migration re-keys or merges rows (v3 → v4, the one step that can drop a
    row) is copied to ``library.json.bak`` before the first save rewrites it;
  * only genuine FILE-level corruption (unreadable / not JSON / not a dict / missing-or-bad version /
    non-list ``entries``) falls back to an empty index — and even then, before any write would
    overwrite the unparseable/newer file, ``save`` first copies it to a ``library.json.bak`` sidecar
    so nothing is ever silently lost.

The DELIBERATE wipe is held to the same rule: ``clear`` copies the index to that same
``library.json.bak`` sidecar BEFORE emptying it, and ``restore`` puts the backup back (swapping the
replaced index into the sidecar, so a restore is itself reversible). ``backup_summary`` reports what
the sidecar holds, for a confirm that can name both sides of the swap.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import re
import shutil

from . import _jsonstore, app_support

_log = logging.getLogger(__name__)

VERSION = 4

# v1→v2 back-compat default for the three trust flags on a LEGACY (pre-flags) entry. A schema-v1
# library was written before per-entry trust existed, so its bests must NOT be retroactively
# discarded from the PB history — we treat a legacy entry as "trusted-unknown": verified (its best
# stays a candidate PB), not degraded, no dropout. This is the honest choice for pre-existing data
# (we have no per-entry signal to say otherwise) and is the whole point of the migration: every
# legacy best stays in the chart. New v2 saves overwrite these with the real session flags.
_TRUST_UNKNOWN = {"verified": True, "degraded": False, "dropout": False}

# GoPro stem G[XHPL]<CC><NNNN>; the CC chapter index is stripped so every chapter shares one key.
_GOPRO_STEM_RE = re.compile(r"^(G[XHPL])\d{2}(\d{4})$", re.IGNORECASE)

# The key 8caab68 (2026-06-18) retired: "<first-chapter stem>|<total media duration, 0.1 s>".
_LEGACY_KEY_RE = re.compile(r"^(?P<stem>.*)\|\d+\.\d$")

_FILENAME = "library.json"


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). The single seam
    tests monkeypatch so the suite never touches the real library — and a test that forgets to is
    still jailed, because the seam resolves through ``app_support.resolve`` (see that module)."""
    return app_support.resolve()


def library_path() -> str:
    """Absolute path to the library index (``<app-support>/pacer/library.json``). Resolves the
    app-support dir through ``_app_support_dir`` so tests that patch that seam are honoured. Does
    NOT create the directory — that happens lazily on the first ``save`` (only a write needs it;
    a read of a missing file already returns the safe empty index)."""
    return os.path.join(_app_support_dir(), _FILENAME)


def empty_index() -> dict:
    """A fresh, valid, empty index — the safe default every corruption path returns to, and the
    starting point before the first recording is added."""
    return {"version": VERSION, "entries": []}


def fingerprint(stem: str) -> str:
    """Chapter-invariant identity key from a first-chapter stem: GoPro ``G[XHPL]<CC><NNNN>`` drops
    ``CC`` -> prefix+NNNN (``"GX010062"`` -> ``"GX0062"``); a non-GoPro stem keys on itself."""
    m = _GOPRO_STEM_RE.match(stem or "")
    if m is None:
        return stem
    return f"{m.group(1).upper()}{m.group(2)}"


def _legacy_stem(entry: dict) -> str | None:
    """The stem a row keyed by the RETIRED scheme was keyed on, or None for any other key.

    Until 8caab68 (2026-06-18) the key was ``"<first-chapter stem>|<total duration, 0.1 s>"``, and
    the duration splits one recording into a row per chapter set (one chapter ~1730 s, the full
    chain ~4655 s). It matches only when the part before the bar IS the row's own ``stem`` — the
    shape that scheme always wrote — so a clip whose own name contains a bar keeps its key."""
    fp, stem = entry.get("fingerprint"), entry.get("stem")
    if not isinstance(fp, str) or not isinstance(stem, str) or not stem:
        return None
    m = _LEGACY_KEY_RE.match(fp)
    return stem if m is not None and m.group("stem") == stem else None


def _chapters(entry: dict) -> frozenset[str]:
    """The chapter FILES a measurement covered, by name: ``paths`` are absolute, and a recording
    whose folder moved between two opens still covers the same chapters."""
    return frozenset(os.path.basename(p).casefold() for p in entry.get("paths") or [])


def _keeps(stored: dict, new: dict) -> bool:
    """True when `stored` must survive an upsert of `new` under the same fingerprint, i.e. `new`
    measured LESS of that outing. Coverage decides, never the numbers measured — see ``upsert``."""
    have, got = _chapters(stored), _chapters(new)
    if got >= have:
        return False          # the same chapters (a re-measurement) or more of them: new wins
    if got < have:
        return True           # a strict part of what is stored never displaces it
    # Two partials that don't contain each other: the one covering more, then with more laps.
    return ((len(have), int(stored.get("lap_count") or 0))
            > (len(got), int(new.get("lap_count") or 0)))


def _valid_entry(e) -> bool:
    """True iff `e` is a structurally valid library entry; load() drops invalid rows (keeps the
    rest)."""
    if not isinstance(e, dict):
        return False
    fp, stem = e.get("fingerprint"), e.get("stem")
    if not isinstance(fp, str) or not fp or not isinstance(stem, str):
        return False
    track, date = e.get("track"), e.get("date")
    if track is not None and not isinstance(track, str):
        return False
    if date is not None and not isinstance(date, str):
        return False
    lap_count = e.get("lap_count")
    # bool is an int subclass; lap counts are real ints, so reject bool explicitly.
    if isinstance(lap_count, bool) or not isinstance(lap_count, int) or lap_count < 0:
        return False
    for key in ("best", "theoretical"):
        v = e.get(key)
        if v is not None and (
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        ):
            return False
    # Trust flags (schema v2). ABSENT is allowed (a legacy v1 entry lacks them — the migration
    # defaults them to trusted-unknown); PRESENT must be a real bool, never a stray non-bool.
    for key in ("verified", "degraded", "dropout"):
        v = e.get(key)
        if v is not None and not isinstance(v, bool):
            return False
    paths = e.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return False
    return True


def _norm_entry(e: dict) -> dict:
    """Canonicalize a validated entry to the stored shape + key order. A MISSING trust flag (a
    legacy v1 entry that never had one) is filled with its trusted-unknown default so a normalized
    entry always carries all three v2 flags as real bools — the PB filter reads them directly."""
    best = e.get("best")
    theo = e.get("theoretical")
    stamp = e.get("ideal_version")
    return {
        "fingerprint": str(e["fingerprint"]),
        "stem": str(e["stem"]),
        "track": e.get("track"),
        "date": e.get("date"),
        "lap_count": int(e["lap_count"]),
        "best": None if best is None else float(best),
        "theoretical": None if theo is None else float(theo),
        # A stamp that is not a plain int reads as none ("older"): it may cost the row its
        # current status, never the row — unlike the fields above, which validate or drop it.
        "ideal_version": stamp if isinstance(stamp, int) and not isinstance(stamp, bool) else None,
        "verified": bool(e.get("verified", _TRUST_UNKNOWN["verified"])),
        "degraded": bool(e.get("degraded", _TRUST_UNKNOWN["degraded"])),
        "dropout": bool(e.get("dropout", _TRUST_UNKNOWN["dropout"])),
        "paths": [str(p) for p in e.get("paths", [])],
    }


def _migrate(data: dict, from_version: int) -> dict:
    """Forward-migrate an OLDER on-disk index (``from_version`` < ``VERSION``) to the current
    schema, PRESERVING every entry. This is the hook PR #55 built precisely so a schema bump keeps
    the user's analyzed history — a version bump must MIGRATE, never wipe. Per-version transforms
    run in ascending order; each MUST keep every entry. Returns ``data`` (mutated in place); the
    caller re-stamps the version and re-validates entries after this runs.

    v1 → v2 (trust flags): a v1 entry predates ``verified``/``degraded``/``dropout``, so it has none
    of them. We do NOT drop those legacy bests from the PB history — they're back-filled with the
    trusted-unknown default (``_TRUST_UNKNOWN``: verified, not degraded, no dropout) so a pre-
    existing PB stays in the chart. ``_norm_entry`` fills the same default for any that slip through
    absent, so this back-fill is belt-and-suspenders; doing it here keeps the migration explicit and
    self-documenting (the point of #55: the transform lives in one hook).

    v2 → v3 (``theoretical`` changed meaning): a v2 value is the sum of the session-best SECTOR
    splits, which on a track with no sector lines — the default, and every recording measured — is
    the entry's own ``best``, to the last bit. v3's value is the corner/straight composite. The
    number is not convertible: nothing in the entry carries the segment times a composite needs.
    The honest move is therefore to RETIRE that one field rather than let one column print two
    definitions, so it is set to None; the dialog shows an em dash and says why on hover, and the
    real value is written back the next time that recording is opened. It drops the value BECAUSE
    keeping it would be a lie — every entry, and every other field on it (including ``best``, which
    the PB history runs on), survives untouched.

    v3 → v4 (one row per outing): 8caab68 retired the ``"<stem>|<duration>"`` key but never
    re-keyed the rows it had written, so a recording opened both ways before 2026-06-18 is still
    two rows — three with a later partial open under today's key; the owner's index holds one D24
    afternoon three times, and its PB chart plots it three times on one date. Each such row is
    re-keyed with ``fingerprint(stem)`` and rows that now share a key are folded through ``upsert``'s
    own rule (``_rekey_and_merge``): the row that covered the most chapters survives WHOLE. This is
    the one step that drops ROWS, so ``save`` keeps the older file as ``.bak`` first
    (``_backup_unsafe``).
    """
    if from_version < 2:
        # entries may be a non-list here (a corrupt shape load() rejects AFTER migration); guard so
        # the back-fill is a no-op on a bad shape rather than crashing.
        entries = data.get("entries")
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict):
                    for key, default in _TRUST_UNKNOWN.items():
                        e.setdefault(key, default)
    if from_version < 3:
        entries = data.get("entries")
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict) and e.get("theoretical") is not None:
                    e["theoretical"] = None
    if from_version < 4:
        entries = data.get("entries")
        if isinstance(entries, list):
            data["entries"] = _rekey_and_merge(entries)
            if len(data["entries"]) < len(entries):
                _log.warning("library: merged %d row(s) that were one recording under the "
                             "retired stem|duration key", len(entries) - len(data["entries"]))
    return data


def _rekey_and_merge(entries: list) -> list:
    """The v3 → v4 step: re-key every row the retired scheme wrote, then fold rows sharing a key
    through ``_keeps`` in file order — as if each had been upserted in turn — the merged row taking
    the first one's position. A row that fails validation passes through untouched, for ``load`` to
    drop and count as it always has."""
    out: list = []
    at: dict[str, int] = {}
    for e in entries:
        if not _valid_entry(e):
            out.append(e)
            continue
        stem = _legacy_stem(e)
        if stem is not None:
            e["fingerprint"] = fingerprint(stem)
        key = e["fingerprint"]
        if key not in at:
            at[key] = len(out)
            out.append(e)
        elif not _keeps(out[at[key]], e):
            out[at[key]] = e
    return out


def _migration_rewrites_rows(data: dict) -> bool:
    """Whether migrating this OLDER on-disk index re-keys or merges rows — the v3 → v4 step, the one
    that can drop a row rather than fill or retire a field, and so the one whose input is kept."""
    entries = data.get("entries")
    if not isinstance(entries, list):
        return False
    before = [e["fingerprint"] for e in entries if _valid_entry(e)]
    after = [e["fingerprint"] for e in _rekey_and_merge(copy.deepcopy(entries))
             if _valid_entry(e)]
    return before != after


def load(path: str | None = None) -> dict:
    """Load + validate the library index, returning the normalized dict. NEVER wipes durable
    history on a version mismatch:

      * an OLDER ``version`` is MIGRATED forward (``_migrate``) and re-stamped — entries preserved;
      * a NEWER ``version`` (a downgrade) is loaded BEST-EFFORT (keep valid entries, ignore unknown
        fields); the on-disk newer file is left for ``save`` to back up rather than clobber;
      * a single malformed entry is dropped (count logged), the rest kept.

    Only genuine FILE-level corruption (absent / unreadable / not JSON / not a dict / missing or
    non-int ``version`` / non-list ``entries``) -> ``empty_index()``. `path` defaults to
    ``library_path()``.

    ONE PARSE PER FILE STATE (QA HEALTH-6). A launch called this four times — the Open Recent
    seed, the Library action's gate, the load-time verdict and its upsert — and every menu sync
    since, so an older index was migrated, and its migration logged, on each; a launch that saved
    nothing did it all again next time. The result is kept per path under the file's IDENTITY
    (``_identity``: inode, size, mtime and ctime), taken BEFORE the read, and handed out as a copy
    so no caller's mutation reaches it. Every writer here replaces the file with a new inode
    (``_jsonstore.write_json``), so a save by any process misses; ``save`` records the state it
    wrote itself. Inside ``locked`` the identity is read under the lock, so a read-modify-write
    still sees the other writers' rows (#380)."""
    if path is None:
        path = library_path()
    key = os.path.abspath(path)
    ident = _identity(path)
    kept = _parsed.get(key)
    if ident is not None and kept is not None and kept[0] == ident:
        return copy.deepcopy(kept[1])
    index, safe = _parse(path)
    if ident is not None:
        _parsed[key] = (ident, copy.deepcopy(index), safe)
    return index


# abspath -> (file identity when read, the index load() returned, whether save may overwrite that
# state without a backup). What `load` read; what `save` wrote.
_parsed: dict[str, tuple[tuple, dict, bool]] = {}


def _identity(path: str) -> tuple | None:
    """What tells one state of the file at `path` from any other, or None when there is no file.
    The inode moves on every atomic replace, and ctime on any in-place write — even one that keeps
    the size and copies an old mtime back, as ``shutil.copy2`` onto the ``.bak`` does."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _unsafe_reason(ok: bool, data: dict | None) -> str | None:
    """Why ``save`` must copy this on-disk state aside before overwriting it, or None when it may
    simply be rewritten: see ``_backup_unsafe``. Takes the raw ``read_object`` result, before any
    migration."""
    version = data.get("version") if ok and data is not None else None
    stamped = isinstance(version, int) and not isinstance(version, bool)
    if not ok or not stamped or version > VERSION or not isinstance(data.get("entries"), list):
        return "an unreadable/newer index"
    if version < VERSION and _migration_rewrites_rows(data):
        return f"the version-{version} index its migration merges rows of"
    return None


def _parse(path: str) -> tuple[dict, bool]:
    """``load``'s uncached read: (the normalized index, whether ``save`` may overwrite this file
    state without a backup)."""
    ok, data = _jsonstore.read_object(path)
    safe = _unsafe_reason(ok, data) is None
    if not ok:
        return empty_index(), safe
    version = data.get("version")
    # A missing / non-int version is untrustworthy shape (not a real schema number) -> corruption.
    if isinstance(version, bool) or not isinstance(version, int):
        _jsonstore.report_unreadable(path, f"version {version!r} is not a schema number")
        return empty_index(), safe
    if version < VERSION:
        # OLDER file: migrate forward, preserving every entry, then re-validate below.
        _log.warning("library: migrating index from version %d to %d (%s)", version, VERSION, path)
        data = _migrate(data, version)
    elif version > VERSION:
        # NEWER file (a downgrade): load best-effort — keep what validates, ignore unknown fields.
        # save() backs the newer file up before it would ever be overwritten (see _backup_unsafe).
        _log.warning("library: index is version %d, newer than this build's %d — loading "
                     "best-effort (%s)", version, VERSION, path)
    raw = data.get("entries")
    if not isinstance(raw, list):
        _jsonstore.report_unreadable(path, "its entries are not a list")
        return empty_index(), safe
    entries = [e for e in raw if _valid_entry(e)]
    dropped = len(raw) - len(entries)
    if dropped:
        # A later save rewrites only the survivors, healing the file.
        _log.warning("library: dropped %d malformed entr%s of %d from %s",
                     dropped, "y" if dropped == 1 else "ies", len(raw), path)
    return {"version": VERSION, "entries": [_norm_entry(e) for e in entries]}, safe


def backup_path(path: str | None = None) -> str:
    """Absolute path of the index's backup sidecar (``<library.json>.bak``) — the ONE slot every
    backup here writes and ``restore`` reads, so "where the copy went" is stated in one place.
    `path` defaults to ``library_path()``."""
    if path is None:
        path = library_path()
    return path + ".bak"


def _backup_unsafe(path: str) -> None:
    """Before ``save`` would OVERWRITE an existing on-disk library it could not safely round-trip
    (genuine corruption, a NEWER un-migratable file, or an OLDER one whose migration re-keys or
    merges rows), copy it to a ``<path>.bak`` sidecar so the user's original bytes are never
    silently lost. Called only for the un-round-trippable cases: a healthy current file, or an older
    one ``load`` migrated without touching a row, is rewritten normally (no backup churn — the one
    slot may be holding a cleared library), and a healthy file being WIPED is ``clear``'s business,
    not this hook's. An older file is backed up at most once: the save it precedes re-stamps it. The
    copy itself (best-effort, same slot, never blocking the write) is ``_copy_to_backup``.

    "Unreadable" is everything ``load`` reads as EMPTY, not only what fails to parse: a string
    ``version`` or a non-list ``entries`` is valid JSON too, and such a file used to be overwritten
    with no copy at all (``marks._backup_unsafe`` names the same trap).

    A file state ``load`` already parsed, or ``save`` wrote, carries its verdict (``_parsed``), so
    the common save does not read the file a second time to learn it is healthy."""
    if not os.path.exists(path):
        return
    kept = _parsed.get(os.path.abspath(path))
    if kept is not None and kept[2] and kept[0] == _identity(path):
        return
    reason = _unsafe_reason(*_jsonstore.read_object(path))
    if reason is not None:
        _copy_to_backup(path, reason)


def _copy_to_backup(path: str, what: str) -> bool:
    """Copy `path` to its ``.bak`` sidecar, best-effort. Returns True on success. Shared by the two
    reasons a backup is taken — an un-round-trippable file about to be overwritten
    (``_backup_unsafe``) and a deliberate wipe (``clear``) — so both land in the same slot with the
    same guarantees: ``shutil.copy2`` (mtime preserved), the ``.bak`` overwritten rather than
    accumulating, and ANY failure logged instead of raised (a backup must never be the reason a
    write the user asked for doesn't happen)."""
    try:
        shutil.copy2(path, backup_path(path))
        _log.warning("library: backed up %s to %s", what,
                     os.path.basename(backup_path(path)))
        return True
    except OSError as exc:
        _log.warning("library: could not back up %s before overwrite (%r)", path, exc)
        return False


def save(index: dict, path: str | None = None) -> None:
    """Write the index atomically (a unique temp file + ``os.replace``, ``_jsonstore.write_json``)
    under the store lock, so neither a crash nor a second writer can leave a truncated or
    interleaved library. Creates the app-support dir if missing. `path` defaults to
    ``library_path()``. Raises OSError on an unwritable destination.

    DATA-SAFETY: before overwriting an existing file that could not be parsed/migrated (genuine
    corruption or a NEWER downgrade-incompatible file), the original is first copied to a
    ``library.json.bak`` sidecar (``_backup_unsafe``) — a schema bump or a downgrade can never
    silently destroy the user's analyzed history."""
    if path is None:
        path = library_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _jsonstore.locked(path):
        _backup_unsafe(path)
        # Re-normalize on the way out: store only the schema fields, in canonical shape/order.
        out = {"version": VERSION, "entries": [_norm_entry(e) for e in index.get("entries", [])]}
        st = _jsonstore.write_json(path, out)
        # What a `load` of these bytes returns, under the identity of the inode WE wrote (fstat of
        # our own descriptor, so another writer landing after the replace cannot be mistaken for
        # us): a launch that saves reads its own save back from here, not from disk.
        _parsed[os.path.abspath(path)] = (
            (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns),
            {"version": VERSION, "entries": [_norm_entry(e) for e in out["entries"]
                                             if _valid_entry(e)]},
            True)


def upsert(index: dict, entry: dict) -> dict:
    """Insert `entry`, or REPLACE the existing entry with the same fingerprint — the no-duplicate
    rule — unless `entry` measured LESS of that outing than the stored row did. Mutates and returns
    `index` (entries list). The replacement keeps the entry's POSITION so a re-open doesn't
    reshuffle the library order; a new fingerprint appends. `entry` must be a valid entry dict
    (built by ``Session.library_entry`` / a test); it is normalized on store.

    KEPT BY WHAT IT COVERED. The key is chapter-invariant on purpose, so every open of any subset of
    a recording's chapters upserts this one row, and the command line can load a single chapter.
    Replacing unconditionally made whichever chapter was opened LAST the outing — board review
    UX-4, on SD_19_09: a full load (36 laps · 0:46.808), then chapter 2 alone, then chapter 1 alone
    left "26 laps · 0:46.862", and the owner's Sandown PB was gone from the Library and its PB
    chart. So (``_keeps``):

      * a strict SUBSET of the stored chapters never displaces them;
      * the SAME chapters always replace, whatever the lap count: a re-open, a start-line drag or
        Save as track is a re-measurement, and the latest timing is the truth;
      * a SUPERSET always replaces (Load full recording after a chapter);
      * two partials that don't contain each other: more chapters, then more valid laps; a tie
        goes to the newer.

    Chapters, not laps, measure coverage because the valid-lap count is per load: Sandown 3h's
    chapter 1 alone counts four ~85 s laps valid that the full 3-hour chain's band rejects. And a
    WHOLE row survives, never a min across two: ``best`` and ``theoretical`` are minima over the
    row's own laps (``lap_count`` is part of the answer, above), and a partial load's band can admit
    a lap the full one rejects — the short-lap-crowned-best failure. Measured on all nine chapters of
    the working set, no chapter alone beat its full chain's best or ideal; the full best sat inside
    one chapter on the three Sandown recordings, and on MK it is the seam lap no chapter alone has.

    The cost is deliberate: a drag or Save as track on a partial open leaves a fuller stored row as
    it was, until the next open that covers at least its chapters rewrites it."""
    norm = _norm_entry(entry)
    entries = index.setdefault("entries", [])
    for i, e in enumerate(entries):
        if e.get("fingerprint") == norm["fingerprint"]:
            if _keeps(e, norm):
                _log.info("library: kept %s's %d-chapter row over a %d-chapter measurement",
                          norm["fingerprint"], len(_chapters(e)), len(_chapters(norm)))
            else:
                entries[i] = norm
            return index
    entries.append(norm)
    return index


def upsert_and_save(entry: dict, path: str | None = None) -> dict:
    """Load the current index, upsert `entry`, write it back atomically, and return the new
    index — all under the store lock, so a second writer's row can't be lost in between. The one
    call the app makes post-load. Any OSError from the write propagates to the caller, which guards
    it (a library write must never disrupt the app)."""
    if path is None:
        path = library_path()
    with _jsonstore.locked(path):
        index = load(path)
        upsert(index, entry)
        save(index, path)
    return index


def remove_and_save(fingerprint_key: str, path: str | None = None) -> dict:
    """Drop `fingerprint_key`'s row under the store lock and write the index back, returning it —
    the index half of "forget this recording". A no-op (no such row) writes nothing. Any OSError
    from the write propagates to the caller, which guards it."""
    if path is None:
        path = library_path()
    with _jsonstore.locked(path):
        index = load(path)
        if remove(index, fingerprint_key):
            save(index, path)
    return index


def remove(index: dict, fingerprint_key: str) -> bool:
    """Drop the entry with fingerprint `fingerprint_key` from `index` (mutates the entries list).
    Returns True if an entry was removed, False if none matched. The privacy "forget this recording"
    control's index half — the sidecar-file deletion is a separate, guarded os call in the app."""
    entries = index.setdefault("entries", [])
    for i, e in enumerate(entries):
        if e.get("fingerprint") == fingerprint_key:
            del entries[i]
            return True
    return False


def rename_track(index: dict, old: str, new: str) -> int:
    """Re-key every entry recorded under track `old` to `new` (mutates), returning how many moved.

    THE INDEX KEYS A CIRCUIT'S HISTORY BY NAME. ``prior_best`` / ``best_entry`` / ``pb_series`` /
    ``track_summary`` all select on ``e.get("track") == track``, so renaming a circuit in the track
    database WITHOUT this leaves every past session filed under the old name while the next
    recording at that location auto-detects the new one — ONE circuit with TWO personal-best
    histories, neither of them complete, and a PB chart that starts over. Entries of any other track
    are untouched.

    The caller owns the write (``rename_track_and_save``) and the multi-store gesture: the per-track
    focus list and the session record's provenance stamp key on the same name and move with it."""
    moved = 0
    for e in index.get("entries", []):
        if e.get("track") == old:
            e["track"] = new
            moved += 1
    return moved


def rename_track_and_save(old: str, new: str, path: str | None = None) -> tuple[dict, int]:
    """Load, re-key `old` → `new`, write back atomically; returns (index, entries moved).

    Writes NOTHING when no entry carried the old name, so renaming a circuit this library has no
    history for cannot churn the file. No ``.bak`` is taken either, deliberately: a re-key is
    reversible by renaming back, while the one backup slot holds the copy ``clear`` leaves — a
    whole wiped library — and spending it on a reversible rename would be the worse trade. Any
    OSError from the write propagates to the caller, which guards it."""
    if path is None:
        path = library_path()
    with _jsonstore.locked(path):
        index = load(path)
        moved = rename_track(index, old, new)
        if moved:
            save(index, path)
    return index, moved


def clear(path: str | None = None) -> None:
    """Wipe the whole library index to an empty one and write it back atomically, KEEPING A COPY.
    Removes ONLY the app-support index (the personal history of what/where you recorded) — the
    actual media files and their per-video ``.pacer.json`` sidecars are left untouched. `path`
    defaults to ``library_path()``; raises OSError on an unwritable destination.

    DATA-SAFETY: this is the one destructive act a user can reach from the UI, and it used to be the
    one write path with NO copy — ``save``'s backup hook (``_backup_unsafe``) fires only for an
    unparseable or newer-version file, which a deliberate wipe of a perfectly healthy index is not.
    So the index is copied to its ``library.json.bak`` sidecar FIRST: a mis-clicked "Clear library"
    is recoverable, by ``restore`` or by hand from the folder "Reveal in Finder" opens. The copy is
    best-effort like every other backup here — a failed copy logs and the wipe still proceeds (the
    ``.bak`` sits in the same directory as the index, so a slot too unwritable to copy into is one
    the wipe itself is about to fail on; refusing to clear would be the worse failure)."""
    if path is None:
        path = library_path()
    with _jsonstore.locked(path):
        if os.path.exists(path):
            _copy_to_backup(path, "the library index before clearing it")
        save(empty_index(), path)


def backup_summary(path: str | None = None) -> dict | None:
    """What the ``.bak`` backup sidecar holds, or None when there is nothing worth restoring (no
    backup, an unreadable one, or one with zero entries). Returns::

        {"path": <the .bak path>, "entries": <int >= 1>, "mtime": <float POSIX seconds | None>}

    The read half of the backup slot: a caller shows this in its confirm so the user sees BOTH sides
    of a restore before it happens. Kept format-free (a raw mtime, not a date string) so this module
    stays display-agnostic — the dialog formats it."""
    bak = backup_path(path)
    if not os.path.exists(bak):
        return None
    entries = load(bak).get("entries", [])
    if not entries:
        return None
    try:
        mtime = os.path.getmtime(bak)
    except OSError:
        mtime = None
    return {"path": bak, "entries": len(entries), "mtime": mtime}


def restore(path: str | None = None) -> dict:
    """Put the ``.bak`` backup back as the live index, and return the resulting index.

    The missing inverse: the app could WRITE a backup ("Back up…") and ``clear`` now leaves one, but
    nothing could READ one — a backup you cannot restore is half a feature, and the one destructive
    control had no way back.

    A restore is a SWAP: the index it replaces becomes the new ``.bak``, so restoring is itself
    reversible and a restore fired at the wrong moment can be taken back exactly the way it was
    made. (It also keeps the invariant that the sidecar always holds "the library as it was before
    the last destructive act".)

    REFUSES to act — returns the current index unchanged — when the backup is missing, unreadable or
    holds no entries: replacing a live library with nothing would be the very data loss this
    function exists to undo. Callers show ``backup_summary`` in their confirm so the user sees both
    counts first. `path` defaults to ``library_path()``; raises OSError on an unwritable
    destination (the swap half is best-effort and only logs)."""
    if path is None:
        path = library_path()
    with _jsonstore.locked(path):
        index = load(backup_path(path))
        if not index["entries"]:
            return load(path)
        # Keep the index we are about to replace, so the swap can put it back. Copied BEFORE the
        # write and moved into the .bak slot after it, so a crash mid-restore leaves the backup.
        swap = path + ".swap"
        kept = False
        if os.path.exists(path):
            try:
                shutil.copy2(path, swap)
                kept = True
            except OSError as exc:
                _log.warning("library: could not keep the replaced index before restoring (%r)",
                             exc)
        save(index, path)
        if kept:
            try:
                os.replace(swap, backup_path(path))
            except OSError as exc:
                _log.warning("library: restored the index but could not swap the backup (%r)", exc)
        return load(path)


def pb_moment(index: dict, track: str | None, best: float | None,
              fingerprint_key: str | None = None) -> dict | None:
    """Decide the "new personal best" moment for a freshly-analysed session, comparing its `best`
    lap (seconds) against `track`'s ``prior_best`` in the CURRENT index (BEFORE this session is
    upserted). Pacer-free — the caller (app) supplies the values from Session accessors and owns the
    timing-trust gate (never celebrate PROVISIONAL timing). Returns:

      * ``{"kind": "beat", "track", "best", "prior", "improvement"}`` when there IS a prior best and
        this session beats it (``best < prior``) — the real celebration; ``improvement`` = prior−best (>0);
      * ``{"kind": "first", "track", "best"}`` when the track has NO prior best (first session logged
        here) — a gentler acknowledgement, not a "PB beaten";
      * ``None`` when there's nothing to celebrate: no track, no valid best, a session that ties /
        is slower than the existing PB — or a session whose improvement was already announced
        (below).

    A RECORDING CANNOT BE ITS OWN PREVIOUS BEST, and `fingerprint_key` is what makes that true. The
    comparison is by TRACK, so every entry of the same track is a candidate prior — including THIS
    recording's own entry, which is in the index the moment it has been analysed once. Opening one
    chapter and then clicking "Load full recording" therefore celebrated the SAME outing beating
    itself: 22 laps at 1:08.771 upserted under fingerprint GX0062, then 66 laps of the same
    recording at 1:08.201 read that entry as the "previous best" and minted a shareable "0.57 s
    faster than your previous best" — as did opening a second chapter of one outing (both fingerprint
    to GX0062; the key strips the chapter index by construction, see ``fingerprint``).

    So the index is PARTITIONED by that identity and the prior is taken from the OTHER recordings
    only. Everything else follows from that one split:

      * no other recording has a trustworthy best here → there is no bar. A recording already in
        the index is not a new session (it is a re-open, a second chapter, or the full chain of one
        already logged), so it gets nothing; a genuinely new one gets its "first". This deliberately
        covers the case where the track's ONLY entry is this recording's own and it is not in the PB
        set at all — a track-less row, or a PROVISIONAL/degraded/dropout one — which the pre-fix code
        greeted with "first lap logged here" because ``prior_best`` had filtered that row out. Those
        laps are in the library; being unable to compare against them is not the same as never having
        seen them, and "your first session on this track" about a recording the library already holds
        is the same re-announcement in a friendlier voice.
      * this recording's OWN stored best already beats that prior → the improvement is not news:
        it was the story when that number was logged, and re-announcing it every time the same
        outing is re-opened with one more chapter is the defect above wearing a different hat.
        Only a TRUSTWORTHY own entry can silence a celebration, matching what ``prior_best`` will
        admit — a provisional/degraded/dropout "best" is not in the PB set and must not act like it.
      * otherwise the ordinary comparison against the other recordings' best decides.

    Suppressing on mere PRESENCE would be wrong, and measurably so: with a previous day's recording
    at 68.500 on the track, a chapter at 68.771 (correctly silent, it is slower) followed by the
    full 66-lap chain at 68.201 is a GENUINE 0.299 s personal best over that other recording, and a
    presence check swallows it on the very gesture this fix is about. It is also what makes the plain
    re-open silent honestly rather than by coincidence (it used to report None only because the same
    laps produce the same best — re-analyse with a start line dragged since and the recording's own
    older number became a "previous best" to beat).

    Callers with no identity to offer pass nothing: the partition is then empty and the behaviour is
    exactly what it always was."""
    if not track or best is None or not math.isfinite(best):
        return None
    # Split by identity — by POSITION, never by value: two entries can compare equal.
    mine, others = [], []
    for e in index.get("entries", []):
        target = mine if fingerprint_key and e.get("fingerprint") == fingerprint_key else others
        target.append(e)
    prior = prior_best({"entries": others}, track)
    if prior is None:
        return None if mine else {"kind": "first", "track": track, "best": float(best)}
    own = [float(e["best"]) for e in mine if e.get("best") is not None and is_trustworthy(e)]
    if own and min(own) < prior:
        return None
    if best < prior:
        return {"kind": "beat", "track": track, "best": float(best),
                "prior": float(prior), "improvement": float(prior) - float(best)}
    return None


def previous_pb(index: dict, track: str | None, fingerprint_key: str | None = None) -> dict | None:
    """The library ROW a new personal best beat — the entry holding `track`'s fastest trustworthy
    best among the OTHER recordings (board review PS-B4: "compare with your previous PB").

    ``pb_moment`` answers with the number (its ``prior``); this answers with the row, because the
    row is what carries the footage's paths. Same partition by `fingerprint_key` and the same
    trustworthy subset (``best_entry``), so the row and the number the PB line quotes are one
    fact: a recording can no more be its own previous PB here than there. None when there is no
    such row (no track, or no other trustworthy best)."""
    if not track:
        return None
    others = [e for e in index.get("entries", [])
              if not (fingerprint_key and e.get("fingerprint") == fingerprint_key)]
    return best_entry({"entries": others}, track)


def pb_moment_for(verified: bool, index: dict, track: str | None, best: float | None,
                  degraded: bool = False, fingerprint_key: str | None = None) -> dict | None:
    """``pb_moment`` gated on BOTH timing axes: returns None (never celebrates) when either

      * `verified` is False (TIMING TRUST) — a lap number referenced to an arbitrary provisional
        start line is meaningless; or
      * `degraded` is True (DATA QUALITY — ``session.timing_quality.degraded``) — the app itself
        calls the absolute timing ESTIMATED (media-clock fallback / low GPS quality), so don't
        celebrate a PB whose time it won't fully stand behind.

    The one place every half of the celebration decision (both trust gates + the PB comparison)
    lives, so the app just passes ``session.timing_verified`` / ``session.timing_quality.degraded``
    + the entry's track/best/fingerprint and the gate stays tested in one spot. `degraded` defaults
    False so the common high-quality path is unchanged; `fingerprint_key` is the entry's identity
    key, forwarded to ``pb_moment``, which partitions the index on it so a recording is compared
    against the OTHER recordings rather than against itself (see there)."""
    if not verified or degraded:
        return None
    return pb_moment(index, track, best, fingerprint_key)


def pb_moment_text(moment: dict, fmt_time) -> tuple[str, str]:
    """(title, body) copy for a ``pb_moment`` result, formatting lap times through the injected
    `fmt_time` (studio._signal.fmt_time — kept out of this pacer-free module so it stays Qt/format-
    agnostic and testable). A "beat" leads with the celebration + the gap to the old PB; a "first"
    is a gentler acknowledgement. The one place the celebration wording lives."""
    track = moment["track"]
    best = fmt_time(moment["best"])
    if moment["kind"] == "beat":
        gap = moment["improvement"]
        # NO EMOJI (D1-07). The 🏁 that shipped here was the app's ONLY colour glyph: U+1F3C1
        # resolves to .Apple Color Emoji UI — a 19 px advance in a 13 px line, in full colour, on
        # the one surface the app designs as a peak moment, inside a UI whose every other mark is a
        # monochrome Phosphor or Inter glyph. There is no like-for-like replacement: Phosphor 1.4.2
        # ships 4,470 names and none of them is a chequered flag (flag / flag-banner / flag-fill /
        # flag-thin, no flag-checkered). So this is a copy decision, and the sentence already
        # celebrates — the exclamation mark was doing the work the emoji was decorating.
        return (
            "New personal best!",
            f"{track} — {best}, {gap:.2f} s faster than your previous best "
            f"({fmt_time(moment['prior'])}).",
        )
    # A SESSION, not a lap: the title said "First lap logged here" over a body that said "your
    # first session on this track" (board review UX-9a). The words are the debrief's own first-open
    # line (pb_standing_text), so the card and the debrief say one thing one way.
    return (
        "First session logged here",
        f"{track} — best lap {best}, the time to beat next time.",
    )


def pb_standing_for(verified: bool, index: dict, track: str | None, best: float | None,
                    degraded: bool = False, fingerprint_key: str | None = None) -> dict | None:
    """Where a session's best lap stands against its track's personal best — the debrief's one
    PB line (board review PS-B1). ``pb_moment_for``'s answer when there is one (``beat`` /
    ``first``), and otherwise, for a recording the index does NOT hold yet, ``{"kind": "behind",
    "track", "best", "prior", "gap"}`` against the fastest trustworthy best of the OTHER
    recordings. Same trust gates, so an unverified or ESTIMATED session gets no PB line at all.

    A best lap against the best lap, never a median against the last session: that comparison
    was measured and refused (studio/docs/refused-2026-09.md §8). A recording already in the index
    gets only its moment — its own row may be the PB, and "behind" would then be false."""
    moment = pb_moment_for(verified, index, track, best, degraded, fingerprint_key)
    if moment is not None or not verified or degraded or not track:
        return moment
    if best is None or not math.isfinite(best):
        return None
    entries = index.get("entries", [])
    if fingerprint_key and any(e.get("fingerprint") == fingerprint_key for e in entries):
        return None
    prior = prior_best(index, track)
    if prior is None or best < prior:
        return None
    return {"kind": "behind", "track": track, "best": float(best), "prior": float(prior),
            "gap": float(best) - float(prior)}


def pb_standing_text(standing: dict, fmt_time) -> str:
    """The debrief's PB sentence for a ``pb_standing_for`` result — ``pb_moment_text``'s facts in
    one line, since the debrief replaces the card rather than repeating it."""
    track, best = standing["track"], fmt_time(standing["best"])
    if standing["kind"] == "beat":
        return (f"New personal best at {track}: {best}, {standing['improvement']:.2f} s faster "
                f"than your previous best ({fmt_time(standing['prior'])}).")
    if standing["kind"] == "behind":
        if round(standing["gap"], 2) == 0:     # a tie is not a beat (pb_moment), nor "0.00 s off"
            return f"Best lap {best} at {track}, level with your personal best there."
        return (f"Best lap {best} at {track}, {standing['gap']:.2f} s off your personal best "
                f"there ({fmt_time(standing['prior'])}).")
    return f"First session logged at {track}: best lap {best}, the time to beat next time."


def is_trustworthy(entry: dict) -> bool:
    """Whether an entry's ``best`` may set / beat a personal best. False (EXCLUDED from the PB set)
    when the entry is:

      * NOT verified (``verified`` is False) — a PROVISIONAL start line, so the lap number is
        referenced to an arbitrary point and is meaningless as a PB; or
      * degraded (``degraded`` is True) — the app itself calls the absolute timing ESTIMATED
        (media-clock fallback / low GPS quality), so we won't stand behind it as a PB; or
      * a GPS ``dropout`` best (``dropout`` is True) — a dropout lap's time/distance are less
        reliable, so it must not silently set a PB either.

    A LEGACY (v1-migrated) entry carries the trusted-unknown default (verified, not degraded, no
    dropout — see ``_TRUST_UNKNOWN``), so it stays INCLUDED: a pre-existing PB history is honoured.
    Reads the normalized flags directly (``_norm_entry`` fills any absent one with its default), so
    the gate matches the app's ``pb_moment_for(verified, degraded)`` decision — dropout adds the one
    axis a per-entry index can carry that the live gate can't."""
    return (bool(entry.get("verified", _TRUST_UNKNOWN["verified"]))
            and not bool(entry.get("degraded", _TRUST_UNKNOWN["degraded"]))
            and not bool(entry.get("dropout", _TRUST_UNKNOWN["dropout"])))


def trust_label(entry: dict) -> str | None:
    """A short, muted trust tag for an UNTRUSTWORTHY entry (for the library table indicator), or
    None for a trustworthy one (verified, not degraded, no dropout — including legacy trusted-
    unknown). The most-significant reason wins so one tag renders: ``"provisional"`` (unverified
    start line — the lap number is meaningless), else ``"estimated"`` (degraded / media-clock
    absolute timing), else ``"dropout"`` (a GPS-dropout best). Pure classification, reused by the
    dialog so its indicator wording stays in one place and matches ``is_trustworthy``."""
    if not bool(entry.get("verified", _TRUST_UNKNOWN["verified"])):
        return "provisional"
    if bool(entry.get("degraded", _TRUST_UNKNOWN["degraded"])):
        return "estimated"
    if bool(entry.get("dropout", _TRUST_UNKNOWN["dropout"])):
        return "dropout"
    return None


def ideal_stale(entry: dict, current: int) -> str | None:
    """None when `entry`'s ideal lap was measured by the ideal-lap maths `current` names
    (``corner_model.IDEAL_VERSION``, passed in so this module stays numpy-free); otherwise
    ``"older"`` — a lower stamp, or none at all, which is every row written before rows were
    stamped — or ``"newer"``, a row a later build wrote before this one was run again. Either way
    its ``theoretical`` is not comparable with the rows measured today (see "A ROW IS A
    MEASUREMENT" above)."""
    stamp = entry.get("ideal_version")
    if stamp == current:
        return None
    return "newer" if isinstance(stamp, int) and stamp > current else "older"


def prior_best(index: dict, track: str) -> float | None:
    """The fastest TRUSTWORTHY recorded best lap for `track` across the CURRENT index (seconds), or
    None when the track has no trustworthy prior best yet. Used to decide the "new personal best"
    moment: the caller compares a freshly-analysed session's best against this BEFORE upserting the
    session, so a genuine improvement is a PB beat and the first-ever session on a track has no prior
    to beat. EXCLUDES untrustworthy entries (provisional / degraded / dropout — see
    ``is_trustworthy``) so a meaningless "best" never becomes the bar a real lap has to beat. Unlike
    pb_series this does NOT require a date (a PB is a PB even on a GPS5 no-date recording)."""
    bests = [
        float(e["best"])
        for e in index.get("entries", [])
        if e.get("track") == track and e.get("best") is not None and is_trustworthy(e)
    ]
    return min(bests) if bests else None


def best_entry(index: dict, track: str) -> dict | None:
    """The ENTRY holding `track`'s fastest trustworthy best lap, or None when it has none.

    ``prior_best`` answers "what is the bar?" with a number; this answers "which session set it?"
    with the row, and the row is what carries an identity. That identity is what a caller needs to
    reach anything stored ALONGSIDE the index under the same fingerprint — the session record
    (``studio/session_record.py``: conditions, tyres, setup) being the first such thing, so the
    Library can say whether the row you are looking at and the row that holds the PB were even
    comparable. Ties go to the EARLIEST date (then the first row), so the answer is stable across
    re-renders rather than dependent on index order.

    Uses the same trustworthy subset as ``prior_best`` / ``pb_series``: a provisional, degraded or
    dropout "best" is not the track's best and must not be what another session is measured
    against."""
    candidates = [e for e in index.get("entries", [])
                  if e.get("track") == track and e.get("best") is not None and is_trustworthy(e)]
    if not candidates:
        return None
    return min(candidates, key=lambda e: (float(e["best"]), e.get("date") or ""))


def pb_series(index: dict, track: str) -> list[tuple[str, float]]:
    """The PB-progression series for one `track`: ``[(date, best), ...]`` over every TRUSTWORTHY
    entry of that track that has BOTH a date and a best lap, sorted ascending by date (then by best,
    so two sessions on the same day order by lap time). The mini-chart plots best-vs-date from this.
    Entries with no date or no best are dropped (nothing to place on the time axis); UNTRUSTWORTHY
    entries (provisional / degraded / dropout — see ``is_trustworthy``) are dropped too, so a
    meaningless "best" never appears in the progression or sets its floor. Legacy trusted-unknown
    entries stay in (back-compat)."""
    pts = [
        (e["date"], float(e["best"]))
        for e in index.get("entries", [])
        if e.get("track") == track and e.get("date") and e.get("best") is not None
        and is_trustworthy(e)
    ]
    pts.sort(key=lambda p: (p[0], p[1]))
    return pts


def track_summary(index: dict, track: str) -> dict | None:
    """A compact cross-session progress read for one `track`, for the library header line. Reuses
    the index + ``pb_series`` (no new cross-session corner analytics). Returns None when `track` is
    falsy; otherwise::

        {"track", "sessions",     # total library rows for this track (all, trustworthy or not)
         "best", "best_date",     # the fastest TRUSTWORTHY dated best + its date (None if none)
         "pb_count",              # count of LATER sessions that BEAT the running best
         "trend"}                 # "improving" | "stalled" | "single" | "none"

    ``best``/``pb_count``/``trend`` come from the TRUSTWORTHY dated series only (``pb_series``), so a
    provisional/degraded/dropout "best" never inflates the read; ``sessions`` counts every row so the
    header is honest about the whole library ("12 sessions · best … · 3 PBs"). ``pb_count`` is the
    number of times a LATER session beat the running best — the FIRST session seeds the baseline and
    is not itself a PB (there was no prior best to beat), the same first/beat split ``pb_moment``
    makes — and ``trend`` is a light "improving" (the latest session is at/within the running best)
    vs "stalled" verdict — the 2nd/3rd-visit hook, not an analytics engine."""
    if not track:
        return None
    sessions = sum(1 for e in index.get("entries", []) if e.get("track") == track)
    series = pb_series(index, track)          # trustworthy, dated, sorted ascending by date
    if not series:
        return {"track": track, "sessions": sessions, "best": None, "best_date": None,
                "pb_count": 0, "trend": "none"}
    # Fastest trustworthy dated best + the date it was set (earliest date on a tie).
    best_date, best = min(series, key=lambda p: (p[1], p[0]))
    # Count improving steps: each time the running best drops, that session set a new PB. The first
    # session SEEDS the running best rather than counting — it had nothing to beat, so calling it a
    # PB would report "1 session · 1 PB" on a brand-new track.
    pb_count = 0
    running = series[0][1]
    for _date, b in series[1:]:
        if b < running:
            pb_count += 1
            running = b
    if len(series) == 1:
        trend = "single"
    else:
        # Improving iff the most-recent session's best equals the overall best (it holds/renewed
        # the record); otherwise the track has stalled off its PB.
        trend = "improving" if series[-1][1] <= best else "stalled"
    return {"track": track, "sessions": sessions, "best": float(best), "best_date": best_date,
            "pb_count": pb_count, "trend": trend}


# The reasons a track's library row is NOT on its PB chart, in the order `pb_left_out` classifies
# them (first match wins, so each row is counted once). The three trust tags are `trust_label`'s
# own words — the ones the library table prints on those rows.
PB_LEFT_OUT_REASONS = ("no laps", "provisional", "estimated", "dropout", "undated")


def pb_left_out(index: dict, track: str) -> dict[str, int]:
    """How many of `track`'s rows are left OFF the PB chart, and why: ``{"sessions": N,
    <reason>: count, ...}`` over ``PB_LEFT_OUT_REASONS``. Every row lands in exactly one bucket or
    is charted, so ``sessions - sum(reasons)`` is ``len(pb_series(index, track))``.

    U2: the chart's empty state used to say "Not enough sessions on this track yet" for ANY empty
    series — including a track with dated sessions whose every best was provisional, estimated or a
    dropout, where the count was not the reason at all."""
    out = {"sessions": 0, **dict.fromkeys(PB_LEFT_OUT_REASONS, 0)}
    for e in index.get("entries", []):
        if e.get("track") != track:
            continue
        out["sessions"] += 1
        if e.get("best") is None:
            out["no laps"] += 1
        elif (tag := trust_label(e)) is not None:
            out[tag] += 1
        elif not e.get("date"):
            out["undated"] += 1
    return out
