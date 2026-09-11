"""The focus list: the training loop's persisted half — and the gate that decides whether it is
allowed to say anything.

PACER-FREE **AND** QT-FREE (json + numpy). The driver promotes one to three corners off the
coaching ranking; this module stores them per track and, on a LATER session at the same track,
measures the same stretch of track again and reports whether it moved. ``Session.focus_items`` /
``Session.focus_report`` own the pacer-side extraction, exactly as ``coaching`` is fed by
``Session.coaching_opportunities``.

WHY THE GATE IS THE FEATURE. "Did it improve?" is a cross-session comparison, and this wave has
already established twice over that such a comparison is frequently invalid:

  * a kart coach, on two days at one track: anything up to 4 seconds purely because of different
    temperature and humidity conditions;
  * PR #258 added the per-session record and a like-for-like warning that stays SILENT when either
    side has no record, because calling two blanks "comparable" is a reassurance backed by nothing.

A focus list that answers "+0.3 s, you improved" across a 12 °C swing is worse than no feature, so
every verdict here is gated and a blocked verdict carries **no number at all** (``Outcome.delta``
is None whenever ``kind`` is ``OUTCOME_NO_VERDICT`` — the refusal is enforced in the model, not in
the copy, so no surface can print a delta the evidence does not support).

MEASURED, on the two real D24 recordings — the same driver at the same track on CONSECUTIVE DAYS
(0060: 2026-05-23, 38 laps; 0062: 2026-05-24, 65 laps), which is the input this feature takes:

  * promote 0060's top three ranked corners (C12 +0.259 s, C4 +0.221 s, C2 +0.110 s) and measure
    them again on 0062 over the SAME windows: C12 −0.026 s, C4 +0.061 s, C2 +0.039 s. Every one of
    the three is inside half the corner's own interquartile spread (0.107 / 0.067 / 0.064 s). The
    honest verdict on the only real cross-session pair this repo has is "no change you can act on",
    three times out of three — which is why ``OUTCOME_UNCHANGED`` is a first-class answer here and
    not an error path;
  * neither recording has a session record (the owner's app-support dir has no
    ``session_records.json`` at all), so the like-for-like gate blocks all three verdicts before the
    spread test is even reached. What the feature says today, on real data, is "I can't tell you
    whether these two sessions were comparable" — not a number.

AND THE WINDOW PROBLEM, which is the one this module exists to solve and the reason a focus item
stores a WINDOW rather than a corner id. The corner partition is re-derived per session from that
session's own trace, so "C8" is not the same measurement twice: between the two recordings C8's
window grew 45.0 m → 56.3 m and its own-window median time went 2.110 s → 2.660 s. Reported as a
cross-session change that is +0.550 s of "you got slower" and every millisecond of it is the
detector drawing a longer window (C10 likewise: 75.1 m → 81.0 m, +0.268 s). Measured over 0060's
own stored window instead, 0062's C8 is 2.153 s — +0.042 s, and C10 +0.063 s. So a focus item
stores its window as a FRACTION of the lap odometer and both sides are measured by the same
function over that fraction; the corner id is a label on it, never the identity. The partitions do line up that way: across the twelve corners
the two sessions' apexes agree to −4.2..+0.2 m, and the lap totals to 0.65 % (1059.2 vs 1066.2 m).

Persistence follows ``library.py`` / ``session_record.py`` — schema version read + forward
migration, a ``.bak`` before any un-round-trippable overwrite, atomic write, one bad list dropped
rather than the file, and ``_app_support_dir`` as the single seam the tests (and
``studio/dev/_jail.py``) redirect. Not ``prefs.py``, which is the store that did not have the
discipline.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
from dataclasses import dataclass, field

import numpy as np

from . import session_record
from .coaching import MIN_CORNER_LAPS, SPREAD_MARGIN

_log = logging.getLogger(__name__)

VERSION = 1

_FILENAME = "focus.json"
_APP_DIR_NAME = "pacer"

# At most three corners. The device this loop is borrowed from lets a driver stack a coaching list
# until it is a to-do list, which is the failure mode `coaching.session_theme` already refused for
# the per-session story: twelve findings is not coaching. Three is what a driver can hold for a
# session, and it is the same shortlist length the panel headline already sums (PANEL_TOP_N).
MAX_ITEMS = 3

# How far the two sessions' lap odometers may disagree before a fraction-mapped window stops being
# the same stretch of track. MEASURED: the two D24 recordings' lap totals differ by 6.93 m on 1059 m
# — 0.65 % — which displaces a corner boundary by at most ~0.3 m inside a 50 m window. 2 % is three
# times that: comfortably past any re-fit of the same lap, and short of a genuinely different route
# or a start line placed somewhere else.
MAX_LAP_TOTAL_DRIFT = 0.02


# ----------------------------------------------------------------------------- outcome vocabulary
# What one focused corner's re-measurement came to. The numbers-bearing three are IMPROVED /
# SLOWER / UNCHANGED; NO_VERDICT is the refusal, and it never carries a delta.
OUTCOME_IMPROVED = "improved"
OUTCOME_SLOWER = "slower"
OUTCOME_UNCHANGED = "unchanged"      # the change is inside the corner's own lap-to-lap spread
OUTCOME_NO_VERDICT = "no_verdict"    # something the evidence cannot support — see `blocker`
OUTCOME_SET_HERE = "set_here"        # promoted from THIS session: there is nothing to compare yet

# Why a verdict was refused. Ordered by how fundamental the objection is — the reported one is the
# first that fires, so "we are not even looking at the same track" beats "the conditions differed".
BLOCK_NONE = ""
BLOCK_TRACK = "track"              # the stored list belongs to another track
BLOCK_UNVERIFIED = "unverified"    # a provisional start line on either side: the odometer origin,
                                   #   and so every fraction-mapped window, is arbitrary
BLOCK_DEGRADED = "degraded"        # ESTIMATED absolute timing on either side — the clock itself
BLOCK_GEOMETRY = "geometry"        # the two lap odometers disagree by more than MAX_LAP_TOTAL_DRIFT
BLOCK_NO_RECORD = "no_record"      # one or both sessions have no session record (PR #258's rule)
BLOCK_CONDITIONS = "conditions"    # the records exist and say the two days were NOT alike
BLOCK_FEW_LAPS = "few_laps"        # too few clean laps through the window this time


@dataclass(frozen=True)
class CornerSample:
    """One window's time over one session's clean laps — the only statistic this feature compares.

    ``iqr`` is the SAME measure ``coaching.Evidence.iqr`` is: the interquartile range of the
    per-lap times, robust where σ is not (on 0062's C1 σ reads 0.226 s against a 0.115 s IQR)."""

    median: float     # median time through the window (s)
    iqr: float        # interquartile range of the per-lap times (s)
    n_laps: int       # clean laps with a finite time through it


def sample_window(times) -> CornerSample | None:
    """``CornerSample`` for one window's per-lap times (non-finite dropped). None when nothing
    finite came back — an absent sample is not a zero one."""
    t = np.asarray(list(times), float)
    t = t[np.isfinite(t)]
    if len(t) == 0:
        return None
    q25, q75 = (np.percentile(t, [25, 75]) if len(t) >= 2 else (t[0], t[0]))
    return CornerSample(median=float(np.median(t)), iqr=float(q75 - q25), n_laps=int(len(t)))


def window_times(windows, laps) -> list[list[float]]:
    """Per window, the seconds over ``[enter_frac, exit_frac]`` of each lap's OWN odometer.

    ``windows``: (enter_frac, exit_frac) pairs, fractions of a lap (0..1). ``laps``: (dist, elapsed)
    array pairs — one lap's gap-aware odometer and its seconds-from-lap-start, the same two arrays
    ``coaching._span_clock`` reads. The time is read off that clock by edge interpolation, NOT
    integrated as ∫ds/v: the two disagree by up to 0.49 s on a slow corner (see `_span_clock`).

    Every lap is projected by its own total, so a lap that measured 1 m long does not shift the
    window; that is also what makes the fraction the portable identity across sessions."""
    out: list[list[float]] = [[] for _ in windows]
    for dist, elapsed in laps:
        dist = np.asarray(dist, float)
        elapsed = np.asarray(elapsed, float)
        if len(dist) < 2 or len(elapsed) != len(dist):
            continue
        total = float(dist[-1])
        if not (total > 0):
            continue
        for k, (f0, f1) in enumerate(windows):
            d0, d1 = float(f0) * total, float(f1) * total
            if not (d1 > d0):
                continue
            out[k].append(float(np.interp(d1, dist, elapsed) - np.interp(d0, dist, elapsed)))
    return out


# --------------------------------------------------------------------------------- the stored item
@dataclass(frozen=True)
class FocusItem:
    """One corner the driver is working on, with the baseline it was promoted against.

    The baseline travels WITH the item rather than with the list, so adding a fourth session's
    corner does not silently re-base the two already being worked on — each line says which day it
    is measured against, and a list can honestly mix them."""

    cid: int                   # the corner id in the session it was promoted from (a LABEL)
    direction: int             # +1 left / −1 right, for the UI glyph
    enter_frac: float          # the window, as a fraction of the lap odometer — the IDENTITY
    exit_frac: float
    median_s: float            # baseline: median time through that window over the clean laps
    iqr_s: float               # baseline: its interquartile spread
    n_laps: int                # baseline: how many clean laps that was over
    time_lost: float           # what it was promoted FOR (the coaching row's median loss, s)
    reason: str                # coaching.REASON_* — the lever named at promotion
    reach: str                 # coaching.REACH_* — execution work vs pace work
    fingerprint: str           # the recording the baseline came from (the library key)
    date: str | None           # its session date (YYYY-MM-DD), for "vs 23 May"
    lap_total: float           # its lap odometer total (m) — the geometry sanity check
    verified: bool             # its start line was TRUSTED (else the odometer origin is arbitrary)
    degraded: bool             # its absolute timing was ESTIMATED

    @property
    def label(self) -> str:
        return f"C{self.cid}"


def item_to_dict(item: FocusItem) -> dict:
    """The stored shape of one item (also the canonical key order)."""
    return {
        "cid": int(item.cid), "direction": int(item.direction),
        "enter_frac": float(item.enter_frac), "exit_frac": float(item.exit_frac),
        "median_s": float(item.median_s), "iqr_s": float(item.iqr_s),
        "n_laps": int(item.n_laps), "time_lost": float(item.time_lost),
        "reason": str(item.reason), "reach": str(item.reach),
        "fingerprint": str(item.fingerprint), "date": item.date,
        "lap_total": float(item.lap_total), "verified": bool(item.verified),
        "degraded": bool(item.degraded),
    }


def item_from_dict(d: dict) -> FocusItem:
    """One validated stored item back into a ``FocusItem``."""
    return FocusItem(
        cid=int(d["cid"]), direction=int(d.get("direction", 1)),
        enter_frac=float(d["enter_frac"]), exit_frac=float(d["exit_frac"]),
        median_s=float(d["median_s"]), iqr_s=float(d["iqr_s"]), n_laps=int(d["n_laps"]),
        time_lost=float(d.get("time_lost", 0.0)), reason=str(d.get("reason", "")),
        reach=str(d.get("reach", "")), fingerprint=str(d.get("fingerprint", "")),
        date=d.get("date"), lap_total=float(d.get("lap_total", 0.0)),
        verified=bool(d.get("verified", False)), degraded=bool(d.get("degraded", False)))


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _valid_item(d) -> bool:
    """True iff `d` is a structurally usable stored item. A list keeps only its valid items; an
    item with no usable WINDOW is not repairable (the window is the identity), so it goes."""
    if not isinstance(d, dict):
        return False
    if isinstance(d.get("cid"), bool) or not isinstance(d.get("cid"), int):
        return False
    for key in ("enter_frac", "exit_frac", "median_s", "iqr_s"):
        if not _finite(d.get(key)):
            return False
    f0, f1 = float(d["enter_frac"]), float(d["exit_frac"])
    if not (0.0 <= f0 < f1 <= 1.0):
        return False
    if float(d["median_s"]) <= 0 or float(d["iqr_s"]) < 0:
        return False
    n = d.get("n_laps")
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return False
    date = d.get("date")
    if date is not None and not isinstance(date, str):
        return False
    return True


def _valid_list(e) -> bool:
    """True iff `e` is a structurally valid per-track focus list with at least one valid item."""
    if not isinstance(e, dict) or not isinstance(e.get("track"), str) or not e["track"]:
        return False
    items = e.get("items")
    return isinstance(items, list) and any(_valid_item(i) for i in items)


def _norm_list(e: dict) -> dict:
    """Canonicalize a validated list: valid items only, capped at MAX_ITEMS, canonical key order."""
    items = [item_to_dict(item_from_dict(i)) for i in e.get("items", []) if _valid_item(i)]
    return {"track": str(e["track"]), "items": items[:MAX_ITEMS]}


# ------------------------------------------------------------------------------------ persistence
def _app_support_dir() -> str:
    """macOS app-support dir for pacer. The single seam tests + ``studio/dev/_jail.py`` redirect,
    so neither a test nor a harness can reach the user's own focus list."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def focus_path(path: str | None = None) -> str:
    """Absolute path of the focus store (``<app-support>/pacer/focus.json``)."""
    return path or os.path.join(_app_support_dir(), _FILENAME)


def empty_store() -> dict:
    """A fresh, valid, empty store — the safe default every corruption path returns to."""
    return {"version": VERSION, "lists": []}


def _is_loadable_dict(path: str) -> tuple[bool, dict | None]:
    """(readable_json_dict, parsed) — the seam ``load`` and ``save`` share, so "genuine corruption"
    is decided in one place (``library.py``'s idiom)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    return True, data


def _migrate(data: dict, from_version: int) -> dict:
    """Forward-migrate an OLDER store, PRESERVING every list. v1 is the first schema, so there is
    nothing to transform yet — the hook exists so the next bump has one obvious place to go and
    cannot be "fixed" by wiping the file (``library._migrate``'s whole point)."""
    return data


def load(path: str | None = None) -> dict:
    """Load + validate the store. Never wipes a user's focus list on a version mismatch: an OLDER
    file is migrated forward, a NEWER one (a downgrade) is read best-effort and left for ``save`` to
    back up, and one malformed list is dropped rather than the file. Only genuine FILE-level
    corruption falls back to ``empty_store()``."""
    path = focus_path(path)
    ok, data = _is_loadable_dict(path)
    if not ok:
        return empty_store()
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        return empty_store()
    if version < VERSION:
        _log.warning("focus: migrating store from version %d to %d (%s)", version, VERSION, path)
        data = _migrate(data, version)
    elif version > VERSION:
        _log.warning("focus: store is version %d, newer than this build's %d — loading "
                     "best-effort (%s)", version, VERSION, path)
    raw = data.get("lists")
    if not isinstance(raw, list):
        return empty_store()
    lists = [e for e in raw if _valid_list(e)]
    dropped = len(raw) - len(lists)
    if dropped:
        _log.warning("focus: dropped %d malformed focus list(s) of %d from %s",
                     dropped, len(raw), path)
    return {"version": VERSION, "lists": [_norm_list(e) for e in lists]}


def backup_path(path: str | None = None) -> str:
    """Absolute path of the store's backup sidecar (``focus.json.bak``)."""
    return focus_path(path) + ".bak"


def _backup_unsafe(path: str) -> None:
    """Copy an un-round-trippable existing store (corrupt, or a NEWER schema) to its ``.bak``
    sidecar before ``save`` would overwrite it — the user's bytes are never silently lost."""
    if not os.path.exists(path):
        return
    ok, data = _is_loadable_dict(path)
    unsafe = (not ok) or (
        isinstance(data, dict)
        and isinstance(data.get("version"), int)
        and not isinstance(data.get("version"), bool)
        and data["version"] > VERSION)
    if not unsafe:
        return
    try:
        shutil.copy2(path, backup_path(path))
        _log.warning("focus: backed up an unreadable/newer store to %s",
                     os.path.basename(backup_path(path)))
    except OSError as exc:
        _log.warning("focus: could not back up %s before overwrite (%r)", path, exc)


def save(store: dict, path: str | None = None) -> None:
    """Write the store atomically (temp file + ``os.replace``). Creates the app-support dir if
    missing; raises OSError on an unwritable destination (the caller guards it — a focus-list write
    must never disrupt the app)."""
    path = focus_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _backup_unsafe(path)
    out = {"version": VERSION,
           "lists": [_norm_list(e) for e in store.get("lists", []) if _valid_list(e)]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def for_track(store: dict, track: str | None) -> list[FocusItem]:
    """The focus items standing for `track` (empty for an unknown track — a focus list is per
    track, because the corners it names only exist on one)."""
    if not track:
        return []
    for e in store.get("lists", []):
        if e.get("track") == track:
            return [item_from_dict(d) for d in e.get("items", []) if _valid_item(d)]
    return []


def set_for_track(store: dict, track: str, items: list[FocusItem]) -> dict:
    """Replace `track`'s focus list with `items` (capped at ``MAX_ITEMS``). An empty list REMOVES
    the track's row rather than storing an empty one — "no focus list" and "an empty focus list"
    must not be two states. Mutates and returns `store`."""
    lists = store.setdefault("lists", [])
    rows = [item_to_dict(i) for i in items[:MAX_ITEMS]]
    for i, e in enumerate(lists):
        if e.get("track") == track:
            if rows:
                lists[i] = {"track": track, "items": rows}
            else:
                del lists[i]
            return store
    if rows:
        lists.append({"track": track, "items": rows})
    return store


def save_for_track(track: str, items: list[FocusItem], path: str | None = None) -> dict:
    """Load, replace `track`'s list, write back atomically, return the new store — the one call the
    app makes when the driver promotes or drops a corner."""
    store = load(path)
    set_for_track(store, track, items)
    save(store, path)
    return store


# ------------------------------------------------------------------------------------- the verdict
@dataclass(frozen=True)
class Outcome:
    """One focused corner, re-measured. ``delta`` is None unless a verdict was actually allowed."""

    item: FocusItem
    kind: str                       # OUTCOME_*
    now: CornerSample | None        # this session's sample over the SAME window (None if none)
    delta: float | None             # now.median − item.median_s (s; + = slower). NEVER set when
                                    #   the verdict is refused — the refusal is structural.
    blocker: str = BLOCK_NONE       # BLOCK_* — why, when kind is OUTCOME_NO_VERDICT
    detail: str = ""                # the specifics a blocker needs (the differing clauses, …)

    @property
    def has_verdict(self) -> bool:
        return self.kind in (OUTCOME_IMPROVED, OUTCOME_SLOWER, OUTCOME_UNCHANGED)


@dataclass(frozen=True)
class Report:
    """What the focus list has to say about the session in front of the driver."""

    track: str | None
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """True when there is a focus list to show at all (the surfaces stay dormant otherwise)."""
        return bool(self.outcomes)

    @property
    def n_verdicts(self) -> int:
        return sum(1 for o in self.outcomes if o.has_verdict)


def _blocker(item: FocusItem, now_ctx: dict, rec_then: dict | None,
             rec_now: dict | None) -> tuple[str, str]:
    """(BLOCK_*, detail) for one item against the current session, or (BLOCK_NONE, "").

    The tests run in a FIXED priority so the reported objection is deterministic and the most
    fundamental one wins: not the same track beats an arbitrary odometer origin beats an estimated
    clock beats a mismatched lap length beats an unknown-conditions pair beats a known-different
    one. The last two are PR #258's rule, said out loud instead of silently: an absent record is
    not evidence of like-for-like, and a driver told "+0.3 s, you improved" across a 12 °C swing has
    been told something the app cannot support."""
    if (now_ctx.get("track") or None) != (now_ctx.get("list_track") or None):
        return BLOCK_TRACK, str(now_ctx.get("track") or "an unknown track")
    if not item.verified or not now_ctx.get("verified", False):
        return BLOCK_UNVERIFIED, ""
    if item.degraded or now_ctx.get("degraded", False):
        return BLOCK_DEGRADED, ""
    then_total, now_total = float(item.lap_total or 0.0), float(now_ctx.get("lap_total") or 0.0)
    if then_total > 0 and now_total > 0:
        if abs(now_total / then_total - 1.0) > MAX_LAP_TOTAL_DRIFT:
            return BLOCK_GEOMETRY, f"{then_total:.0f} m vs {now_total:.0f} m"
    # PR #258's like-for-like pair, in its own order: "nothing written down" first, because a
    # record that does not exist cannot also disagree.
    if session_record.is_empty(rec_then) or session_record.is_empty(rec_now):
        missing = []
        if session_record.is_empty(rec_then):
            missing.append(_when(item.date))
        if session_record.is_empty(rec_now):
            missing.append("today")
        return BLOCK_NO_RECORD, " and ".join(missing)
    differences = session_record.comparable(rec_then, rec_now)
    if differences:
        return BLOCK_CONDITIONS, ", ".join(differences)
    return BLOCK_NONE, ""


def verdict(items: list[FocusItem], now_ctx: dict, samples: list[CornerSample | None],
            records: dict | None = None) -> Report:
    """Re-measure verdict for every focus item. PURE — every input is a plain value.

    ``now_ctx``: this session's facts — ``list_track`` (the focus list's track), ``track``,
    ``fingerprint``, ``date``, ``lap_total``, ``verified``, ``degraded``.
    ``samples``: this session's ``CornerSample`` per item, measured over the item's OWN stored
    window (``window_times``) — aligned to `items`, None where the window had no usable laps.
    ``records``: the whole session-record store (``session_record.load()``); each side of a
    comparison is looked up by its own fingerprint, and an absent record is itself an answer.

    An item promoted from THIS recording reports ``OUTCOME_SET_HERE`` — there is nothing to compare
    a session with itself against, and saying "no change" would be dressing that up as a result."""
    store = records or {}
    rec_now = session_record.get(store, str(now_ctx.get("fingerprint") or ""))
    outcomes: list[Outcome] = []
    for i, item in enumerate(items):
        now = samples[i] if i < len(samples) else None
        if item.fingerprint and item.fingerprint == (now_ctx.get("fingerprint") or ""):
            outcomes.append(Outcome(item=item, kind=OUTCOME_SET_HERE, now=now, delta=None))
            continue
        block, detail = _blocker(item, now_ctx, session_record.get(store, item.fingerprint),
                                 rec_now)
        if block:
            outcomes.append(Outcome(item=item, kind=OUTCOME_NO_VERDICT, now=now, delta=None,
                                    blocker=block, detail=detail))
            continue
        if now is None or now.n_laps < MIN_CORNER_LAPS:
            outcomes.append(Outcome(item=item, kind=OUTCOME_NO_VERDICT, now=now, delta=None,
                                    blocker=BLOCK_FEW_LAPS,
                                    detail=f"{now.n_laps if now else 0} clean laps"))
            continue
        delta = now.median - item.median_s
        # The SAME actionability test the coaching gate applies to a within-session claim, on the
        # same statistic (`coaching.SPREAD_MARGIN` × the interquartile spread) rather than a second
        # notion of significance invented here. The wider of the two sessions' spreads is the bar,
        # because a change is only as aimable as the noisier side of the comparison. With 38 and 65
        # laps the standard error of either median is ~0.02-0.03 s and a significance test would
        # pass almost anything: this asks whether a driver could aim at the difference, not whether
        # it is real.
        spread = max(item.iqr_s, now.iqr)
        if abs(delta) < SPREAD_MARGIN * spread:
            kind = OUTCOME_UNCHANGED
        else:
            kind = OUTCOME_IMPROVED if delta < 0 else OUTCOME_SLOWER
        outcomes.append(Outcome(item=item, kind=kind, now=now, delta=float(delta)))
    return Report(track=now_ctx.get("list_track") or now_ctx.get("track"), outcomes=outcomes)


# ------------------------------------------------------------------------------------- the words
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _when(date: str | None) -> str:
    """"23 May" from an ISO date — the short form a verdict line says a baseline was set on.
    "last time" when the recording carried no date (a GPS5-era file has none)."""
    if not date or len(date) < 10:
        return "last time"
    try:
        month = _MONTHS[int(date[5:7]) - 1]
        return f"{int(date[8:10])} {month}"
    except (ValueError, IndexError):
        return "last time"


_BLOCK_SENTENCE = {
    BLOCK_TRACK: "this is a different track",
    BLOCK_UNVERIFIED: "one of the two sessions has a provisional start line, so the two lap "
                      "odometers don't line up",
    BLOCK_DEGRADED: "one of the two sessions has ESTIMATED timing, so the clock itself isn't "
                    "solid enough to difference",
    BLOCK_GEOMETRY: "the two laps measure different lengths",
    BLOCK_NO_RECORD: "no session record for",
    BLOCK_CONDITIONS: "the two sessions weren't alike",
    BLOCK_FEW_LAPS: "too few clean laps through it today",
}


def outcome_sentence(o: Outcome) -> str:
    """One focused corner's line — the whole feature in a sentence, and never a number the gate
    did not allow. A refusal SAYS what it would need, because "we can't compare these" is a fact
    the driver can act on (write the conditions down) and a silent row is not."""
    label = o.item.label
    when = _when(o.item.date)
    if o.kind == OUTCOME_SET_HERE:
        return (f"{label} — on your focus list from this session ({o.item.median_s:.2f} s over "
                f"{o.item.n_laps} laps). Next time you're here, pacer will say whether it moved.")
    if o.kind == OUTCOME_NO_VERDICT:
        if o.blocker == BLOCK_NO_RECORD:
            return (f"{label} — can't say. There's no session record for {o.detail}, so nothing "
                    f"says the two days were comparable; a coach will put up to 4 s a lap on "
                    f"conditions alone.")
        if o.blocker == BLOCK_CONDITIONS:
            return f"{label} — can't say: {o.detail}."
        if o.blocker == BLOCK_FEW_LAPS:
            return f"{label} — can't say: {o.detail} through it today."
        tail = f" ({o.detail})" if o.detail and o.blocker == BLOCK_GEOMETRY else ""
        return f"{label} — can't say: {_BLOCK_SENTENCE.get(o.blocker, 'not comparable')}{tail}."
    assert o.now is not None and o.delta is not None  # has_verdict implies both
    # Both halves of the comparison, in the order they happened, with the sample each median came
    # from: a verdict that states only its difference cannot be checked by the person reading it.
    body = (f"{o.item.median_s:.2f} → {o.now.median:.2f} s, "
            f"{o.item.n_laps} → {o.now.n_laps} laps")
    if o.kind == OUTCOME_UNCHANGED:
        return (f"{label} — no change you can act on: {abs(o.delta):.2f} s apart, inside the "
                f"corner's own {max(o.item.iqr_s, o.now.iqr):.2f} s spread ({body}).")
    word = "faster" if o.kind == OUTCOME_IMPROVED else "slower"
    return f"{label} — {abs(o.delta):.2f} s {word} than {when} ({body})."


def _corner_list(outcomes: list[Outcome]) -> str:
    """"C12, C4 and C2" — the corners a shared line speaks for."""
    labels = [o.item.label for o in outcomes]
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def report_lines(report: Report) -> list[str]:
    """The lines a surface shows — ``outcome_sentence`` per corner, EXCEPT when every corner is
    blocked for the same reason, which is the common case and the one that reads worst per-row.

    Three corners each printing the same 200-character explanation of why nothing can be said is a
    wall of text that buries the one thing the driver can act on (write the conditions down), and it
    spends the block's whole height budget restating one fact. Collapsed, the shared refusal is one
    line that names the corners, the reason and the fix."""
    outcomes = list(report.outcomes)
    if not outcomes:
        return []
    kinds = {o.kind for o in outcomes}
    blockers = {o.blocker for o in outcomes}
    if len(outcomes) > 1 and kinds == {OUTCOME_NO_VERDICT} and len(blockers) == 1:
        first = outcomes[0]
        who = _corner_list(outcomes)
        if first.blocker == BLOCK_NO_RECORD:
            return [f"{who} — can't say. There's no session record for {first.detail}, so nothing "
                    f"says the two days were comparable; a coach will put up to 4 s a lap on "
                    f"conditions alone. File ▸ Session record… writes one."]
        if first.blocker == BLOCK_CONDITIONS:
            return [f"{who} — can't say: {first.detail}."]
        return [f"{who} — can't say: "
                f"{_BLOCK_SENTENCE.get(first.blocker, 'not comparable')}."]
    if len(outcomes) > 1 and kinds == {OUTCOME_SET_HERE}:
        n = outcomes[0].item.n_laps
        return [f"{_corner_list(outcomes)} — baselines measured on this session ("
                f"{', '.join(f'{o.item.median_s:.2f} s' for o in outcomes)} over {n} laps). Next "
                f"time you're at this track, pacer measures the same stretches again and says "
                f"whether they moved."]
    return [outcome_sentence(o) for o in outcomes]


def report_headline(report: Report) -> str:
    """The focus block's one-line framing: what the list is, and how much of it could be answered.
    It states the count of REFUSALS, because a loop that quietly answers two of three is how a
    driver comes to believe the third was fine."""
    n = len(report.outcomes)
    if not n:
        return ""
    noun = "corner" if n == 1 else "corners"
    set_here = sum(1 for o in report.outcomes if o.kind == OUTCOME_SET_HERE)
    if set_here == n:
        return f"Focus list · {n} {noun}, set from this session"
    verdicts = report.n_verdicts
    refused = sum(1 for o in report.outcomes if o.kind == OUTCOME_NO_VERDICT)
    if verdicts == 0:
        return f"Focus list · {n} {noun} · no verdict yet — {_refusal_summary(report)}"
    tail = f", {refused} without enough evidence" if refused else ""
    return (f"Focus list · {verdicts} of {n} {noun} re-measured{tail}")


def _refusal_summary(report: Report) -> str:
    """The single most common reason nothing could be said — the headline's tail."""
    blockers = [o.blocker for o in report.outcomes if o.kind == OUTCOME_NO_VERDICT]
    if not blockers:
        return "nothing to compare yet"
    top = max(set(blockers), key=blockers.count)
    if top == BLOCK_NO_RECORD:
        return "these sessions have no record of their conditions"
    if top == BLOCK_CONDITIONS:
        return "the two sessions weren't alike"
    if top == BLOCK_FEW_LAPS:
        return "too few clean laps through them today"
    return _BLOCK_SENTENCE.get(top, "not comparable")
