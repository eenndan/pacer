"""Session library: a local index of analyzed recordings (F8) with PB progression.

Every successful load upserts the opened recording into one JSON index in the macOS app-support
dir; the dialog lists every analyzed recording, re-opens any, and draws a per-track PB chart.
PACER-FREE: pure path resolution, schema validation and atomic JSON I/O (the analyzed values
arrive as a plain dict from ``Session.library_entry()``).

fingerprint = (GoPro prefix, recording number) so every chapter of a recording maps to one
entry — neither the path list nor the media duration is stable across a single-chapter vs a full
chaptered open of the SAME recording.

Schema (version 2) — one JSON object::

    {"version": 2,
     "entries": [
       {"fingerprint": "GX0062",            # the chapter-invariant identity key (see above)
        "stem":        "GX010062",          # first-chapter stem, for display
        "track":       <registry track name or null>,
        "date":        "YYYY-MM-DD" | null,  # GPS9 wall-clock date (Session.session_date)
        "lap_count":   <int>,                # valid lap count
        "best":        <float seconds> | null,    # best lap time
        "theoretical": <float seconds> | null,    # Session.theoretical_best
        "verified":    <bool>,               # session.timing_verified — a TRUSTED start/finish line
        "degraded":    <bool>,               # session.timing_quality.degraded — ESTIMATED absolute timing
        "dropout":     <bool>,               # the session's best lap (or any valid lap) had a GPS dropout
        "paths":       ["/abs/GX010062.MP4", ...]}, # the chapter file path(s) as opened (absolute)
       ...]}

The three TRUST flags (``verified``/``degraded``/``dropout``, schema v2) let the PB progression
EXCLUDE an untrustworthy "best": a PROVISIONAL start line (``not verified``) or a data-quality-
DEGRADED clock (``degraded``) references its lap number to something arbitrary/estimated, so it
must never set or beat a personal best; a GPS-``dropout`` best is less reliable, so it is kept but
also excluded from the PB set (a dropout lap's time can't be trusted to set a PB either). The
library TABLE still shows every session (with a trust indicator); only the PB CHART + PB logic use
the trustworthy subset (``trustworthy_entries``). See ``_TRUST_UNKNOWN`` for the v1→v2 back-compat
default that keeps a pre-existing (flag-less) PB history included.

Load self-heals WITHOUT destroying durable history:
  * a single bad entry is dropped (count logged), the rest kept (so the next ``save``, which
    rewrites only the survivors, doesn't lose all history);
  * an OLDER on-disk ``version`` is MIGRATED forward (``_migrate``) — never discarded — so the
    first run after a future schema bump preserves every analyzed recording;
  * a NEWER on-disk ``version`` (a downgrade) is loaded BEST-EFFORT (keep the entries it can, ignore
    unknown fields) and the newer file is NOT destructively rewritten in place;
  * only genuine FILE-level corruption (unreadable / not JSON / not a dict / missing-or-bad version /
    non-list ``entries``) falls back to an empty index — and even then, before any write would
    overwrite the unparseable/newer file, ``save`` first copies it to a ``library.json.bak`` sidecar
    so nothing is ever silently lost.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil

_log = logging.getLogger(__name__)

VERSION = 2

# v1→v2 back-compat default for the three trust flags on a LEGACY (pre-flags) entry. A schema-v1
# library was written before per-entry trust existed, so its bests must NOT be retroactively
# discarded from the PB history — we treat a legacy entry as "trusted-unknown": verified (its best
# stays a candidate PB), not degraded, no dropout. This is the honest choice for pre-existing data
# (we have no per-entry signal to say otherwise) and is the whole point of the migration: every
# legacy best stays in the chart. New v2 saves overwrite these with the real session flags.
_TRUST_UNKNOWN = {"verified": True, "degraded": False, "dropout": False}

# GoPro stem G[XHPL]<CC><NNNN>; the CC chapter index is stripped so every chapter shares one key.
_GOPRO_STEM_RE = re.compile(r"^(G[XHPL])\d{2}(\d{4})$", re.IGNORECASE)

_FILENAME = "library.json"
_APP_DIR_NAME = "pacer"


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). The single seam
    tests monkeypatch so the suite never touches the real library."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


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
    return {
        "fingerprint": str(e["fingerprint"]),
        "stem": str(e["stem"]),
        "track": e.get("track"),
        "date": e.get("date"),
        "lap_count": int(e["lap_count"]),
        "best": None if best is None else float(best),
        "theoretical": None if theo is None else float(theo),
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
    self-documenting (the point of #55: the transform lives in one hook)."""
    if from_version < 2:
        # entries may be a non-list here (a corrupt shape load() rejects AFTER migration); guard so
        # the back-fill is a no-op on a bad shape rather than crashing.
        entries = data.get("entries")
        if isinstance(entries, list):
            for e in entries:
                if isinstance(e, dict):
                    for key, default in _TRUST_UNKNOWN.items():
                        e.setdefault(key, default)
    return data


def _is_loadable_dict(path: str) -> tuple[bool, dict | None]:
    """(readable_json_dict, parsed) for `path`: True/parsed when the file exists and parses to a
    JSON object, else (False, None). The seam ``load`` and ``save`` share so 'genuine corruption'
    (the only case that ever falls back to empty / triggers a backup) is decided in one place."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    return True, data


def load(path: str | None = None) -> dict:
    """Load + validate the library index, returning the normalized dict. NEVER wipes durable
    history on a version mismatch:

      * an OLDER ``version`` is MIGRATED forward (``_migrate``) and re-stamped — entries preserved;
      * a NEWER ``version`` (a downgrade) is loaded BEST-EFFORT (keep valid entries, ignore unknown
        fields); the on-disk newer file is left for ``save`` to back up rather than clobber;
      * a single malformed entry is dropped (count logged), the rest kept.

    Only genuine FILE-level corruption (absent / unreadable / not JSON / not a dict / missing or
    non-int ``version`` / non-list ``entries``) -> ``empty_index()``. `path` defaults to
    ``library_path()``."""
    if path is None:
        path = library_path()
    ok, data = _is_loadable_dict(path)
    if not ok:
        return empty_index()
    version = data.get("version")
    # A missing / non-int version is untrustworthy shape (not a real schema number) -> corruption.
    if isinstance(version, bool) or not isinstance(version, int):
        return empty_index()
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
        return empty_index()
    entries = [e for e in raw if _valid_entry(e)]
    dropped = len(raw) - len(entries)
    if dropped:
        # A later save rewrites only the survivors, healing the file.
        _log.warning("library: dropped %d malformed entr%s of %d from %s",
                     dropped, "y" if dropped == 1 else "ies", len(raw), path)
    return {"version": VERSION, "entries": [_norm_entry(e) for e in entries]}


def _backup_unsafe(path: str) -> None:
    """Before ``save`` would OVERWRITE an existing on-disk library it could not safely round-trip
    (genuine corruption, or a NEWER un-migratable file), copy it to a ``<path>.bak`` sidecar so the
    user's original bytes are never silently lost. Called only for the un-round-trippable cases:
    a healthy current/older file that ``load`` migrated is rewritten normally (no backup churn).

    Backup is best-effort and MUST NOT block the write: any failure to back up just logs (a write
    that keeps the app usable beats refusing to save because the backup slot is unwritable). Uses
    ``shutil.copy2`` (preserves mtime); the ``.bak`` is overwritten each time so it always mirrors
    the last replaced-yet-unparseable file rather than accumulating."""
    if not os.path.exists(path):
        return
    ok, data = _is_loadable_dict(path)
    unsafe = (not ok) or (
        isinstance(data, dict)
        and isinstance(data.get("version"), int)
        and not isinstance(data.get("version"), bool)
        and data["version"] > VERSION
    )
    if not unsafe:
        return
    try:
        shutil.copy2(path, path + ".bak")
        _log.warning("library: backed up an unreadable/newer index to %s before overwriting",
                     os.path.basename(path) + ".bak")
    except OSError as exc:
        _log.warning("library: could not back up %s before overwrite (%r)", path, exc)


def save(index: dict, path: str | None = None) -> None:
    """Write the index atomically (temp file + ``os.replace``) so a crash mid-write can't leave a
    truncated library. Creates the app-support dir if missing. `path` defaults to
    ``library_path()``. Raises OSError on an unwritable destination.

    DATA-SAFETY: before overwriting an existing file that could not be parsed/migrated (genuine
    corruption or a NEWER downgrade-incompatible file), the original is first copied to a
    ``library.json.bak`` sidecar (``_backup_unsafe``) — a schema bump or a downgrade can never
    silently destroy the user's analyzed history."""
    if path is None:
        path = library_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _backup_unsafe(path)
    # Re-normalize on the way out: store only the schema fields, in canonical shape/order.
    out = {"version": VERSION, "entries": [_norm_entry(e) for e in index.get("entries", [])]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def upsert(index: dict, entry: dict) -> dict:
    """Insert `entry`, or REPLACE the existing entry with the same fingerprint — the no-duplicate
    rule. Mutates and returns `index` (entries list). The replacement keeps the entry's POSITION
    so a re-open doesn't reshuffle the library order; a new fingerprint appends. `entry` must be
    a valid entry dict (built by ``Session.library_entry`` / a test); it is normalized on store."""
    norm = _norm_entry(entry)
    entries = index.setdefault("entries", [])
    for i, e in enumerate(entries):
        if e.get("fingerprint") == norm["fingerprint"]:
            entries[i] = norm
            return index
    entries.append(norm)
    return index


def upsert_and_save(entry: dict, path: str | None = None) -> dict:
    """Load the current index, upsert `entry`, write it back atomically, and return the new
    index. The one call the app makes post-load. Any OSError from the write propagates to the
    caller, which guards it (a library write must never disrupt the app)."""
    index = load(path)
    upsert(index, entry)
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


def clear(path: str | None = None) -> None:
    """Wipe the whole library index to an empty one and write it back atomically. Removes ONLY the
    app-support index (the personal history of what/where you recorded) — the actual media files and
    their per-video ``.pacer.json`` sidecars are left untouched. `path` defaults to
    ``library_path()``; raises OSError on an unwritable destination."""
    save(empty_index(), path)


def pb_moment(index: dict, track: str | None, best: float | None) -> dict | None:
    """Decide the "new personal best" moment for a freshly-analysed session, comparing its `best`
    lap (seconds) against `track`'s ``prior_best`` in the CURRENT index (BEFORE this session is
    upserted). Pacer-free — the caller (app) supplies the values from Session accessors and owns the
    timing-trust gate (never celebrate PROVISIONAL timing). Returns:

      * ``{"kind": "beat", "track", "best", "prior", "improvement"}`` when there IS a prior best and
        this session beats it (``best < prior``) — the real celebration; ``improvement`` = prior−best (>0);
      * ``{"kind": "first", "track", "best"}`` when the track has NO prior best (first session logged
        here) — a gentler acknowledgement, not a "PB beaten";
      * ``None`` when there's nothing to celebrate: no track, no valid best, or a session that ties /
        is slower than the existing PB.

    A tie or a re-open of the same recording (its own entry is the prior best) reports None, so the
    banner never fires on an unimproved number."""
    if not track or best is None or not math.isfinite(best):
        return None
    prior = prior_best(index, track)
    if prior is None:
        return {"kind": "first", "track": track, "best": float(best)}
    if best < prior:
        return {"kind": "beat", "track": track, "best": float(best),
                "prior": float(prior), "improvement": float(prior) - float(best)}
    return None


def pb_moment_for(verified: bool, index: dict, track: str | None,
                  best: float | None, degraded: bool = False) -> dict | None:
    """``pb_moment`` gated on BOTH timing axes: returns None (never celebrates) when either

      * `verified` is False (TIMING TRUST) — a lap number referenced to an arbitrary provisional
        start line is meaningless; or
      * `degraded` is True (DATA QUALITY — ``session.timing_quality.degraded``) — the app itself
        calls the absolute timing ESTIMATED (media-clock fallback / low GPS quality), so don't
        celebrate a PB whose time it won't fully stand behind.

    The one place every half of the celebration decision (both trust gates + the PB comparison)
    lives, so the app just passes ``session.timing_verified`` / ``session.timing_quality.degraded``
    + the entry's track/best and the gate stays tested in one spot. `degraded` defaults False so the
    common high-quality path is unchanged."""
    if not verified or degraded:
        return None
    return pb_moment(index, track, best)


def pb_moment_text(moment: dict, fmt_time) -> tuple[str, str]:
    """(title, body) copy for a ``pb_moment`` result, formatting lap times through the injected
    `fmt_time` (studio._signal.fmt_time — kept out of this pacer-free module so it stays Qt/format-
    agnostic and testable). A "beat" leads with the celebration + the gap to the old PB; a "first"
    is a gentler acknowledgement. The one place the celebration wording lives."""
    track = moment["track"]
    best = fmt_time(moment["best"])
    if moment["kind"] == "beat":
        gap = moment["improvement"]
        return (
            "New personal best! 🏁",
            f"{track} — {best}, {gap:.2f} s faster than your previous best "
            f"({fmt_time(moment['prior'])}).",
        )
    return (
        "First lap logged here",
        f"{track} — {best}. Your first session on this track; beat it next time.",
    )


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
         "pb_count",              # count of improving steps in the PB series (new-PB moments)
         "trend"}                 # "improving" | "stalled" | "single" | "none"

    ``best``/``pb_count``/``trend`` come from the TRUSTWORTHY dated series only (``pb_series``), so a
    provisional/degraded/dropout "best" never inflates the read; ``sessions`` counts every row so the
    header is honest about the whole library ("12 sessions · best … · 3 PBs"). ``pb_count`` is the
    number of times a session set a new running best (a genuine improvement step), and ``trend`` is a
    light "improving" (the latest session is at/within the running best) vs "stalled" verdict — the
    2nd/3rd-visit hook, not an analytics engine."""
    if not track:
        return None
    sessions = sum(1 for e in index.get("entries", []) if e.get("track") == track)
    series = pb_series(index, track)          # trustworthy, dated, sorted ascending by date
    if not series:
        return {"track": track, "sessions": sessions, "best": None, "best_date": None,
                "pb_count": 0, "trend": "none"}
    # Fastest trustworthy dated best + the date it was set (earliest date on a tie).
    best_date, best = min(series, key=lambda p: (p[1], p[0]))
    # Count improving steps: each time the running best drops, that session set a new PB.
    pb_count = 0
    running = None
    for _date, b in series:
        if running is None or b < running:
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
