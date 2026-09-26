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

MEASURED (T16b, 2026-09-23) on the one pair of working-set sessions the focus list can compare in
order at one track — 0064 then 0068, two Sandown sessions two months apart (0064: 2026-07-19, 62
laps; 0068: 2026-09-19, 36 laps), which is the input this feature takes. 0064 is Sandown 3h 2026
(GX010064 + GX020064 + GX030064) and 0068 is SD_19_09_26 (GX010068 + GX020068); the coaching tables
list them the other way round, because there 0068 stands where D24's 0060 stood, and a focus list is
promoted on the EARLIER session. D24's pair was one driver on consecutive days on a built-in track,
so both its start lines were trusted; since Q2 (2026-09-23) so are this pair's, because Sandown Park
is a built-in track too, on the owner's own saved line. The D24 edition, which #344 marked stale
after #335 changed corner matching, is kept in studio/docs/coaching-tables-on-d24.md.
Promote 0064's top three ranked corners and measure them again on 0068 over the SAME windows.
"promoted for" is the coaching row's time lost; the medians and interquartile ranges are the
window's seconds over each session's clean laps matched on track at both of its edges (since QA
LOOK-10 the CORNERS table's own instrument, ``METHOD_MATCHED``: 0064's C4 is 61 of its 62 laps and
0068's 35 of 36); "bar" is ``SPREAD_MARGIN`` × the wider of the two IQRs, the test ``verdict``
applies. tests/test_measured_figures.py derives the prose from these
cells and, given the footage, re-measures every one:

  corner  promoted for  0064 median  IQR    0068 median  IQR    change  bar    verdict
  C1      +0.229 s      10.341 s     0.309  10.312 s     0.200  −0.029  0.155  unchanged
  C4      +0.193 s      5.286 s      0.231  5.116 s      0.171  −0.170  0.115  improved
  C7      +0.178 s      4.100 s      0.305  4.104 s      0.105  +0.004  0.152  unchanged

  * both start lines are trusted, even in the jailed check, because both recordings detect the
    built-in Sandown Park. So the gate passes the start line and blocks all three verdicts at
    `no_record`: neither recording has a session record (the owner's app-support dir has no
    ``session_records.json`` at all). What the feature says today, on real data, is "record the
    conditions first" — not a number. Until Q2 it stopped a step earlier, at `unverified`: with no
    Sandown Park to detect, both lines were the loader's own fit;
  * the spread test's verdicts, reached only by forcing the start-line and session-record gates
    open: unchanged on 2, improved on 1, slower on 0. The fraction instrument before LOOK-10
    read unchanged on 3, as on D24, whose lines were trusted too: its C4 baseline was 5.223 s where
    the matched laps' is 5.286 s, the same cells the Stats page's CORNERS table reads.
    Before Q2 the same test read two of the three "improved" (C7 −0.136 s, C4 −0.214 s), over
    windows that were not the same stretch of track — exactly what the start-line gate exists to
    stop, and it did (below).

AND THE WINDOW PROBLEM, which is the one this module exists to solve and the reason a focus item
stores a WINDOW rather than a corner id. The corner partition is re-derived per session from that
session's own trace, so "C1" is not the same measurement twice. Each recording's own window for
every corner, the median time over it, and 0068's median over 0064's STORED window instead:

  corner  0064 window  0068 window  0064 own  0068 own  own change  0068 over 0064's  stored change
  C1      173.4 m      179.5 m      10.341 s  10.511 s  +0.170 s    10.312 s          −0.029 s
  C2      51.1 m       51.8 m       4.401 s   4.257 s   −0.144 s    4.242 s           −0.159 s
  C3      79.6 m       81.1 m       5.424 s   5.605 s   +0.181 s    5.574 s           +0.150 s
  C4      70.6 m       74.3 m       5.286 s   5.311 s   +0.025 s    5.116 s           −0.170 s
  C5      45.8 m       47.3 m       3.577 s   3.640 s   +0.063 s    3.590 s           +0.013 s
  C6      43.5 m       44.3 m       3.373 s   3.123 s   −0.250 s    3.092 s           −0.281 s
  C7      51.1 m       51.1 m       4.100 s   4.076 s   −0.024 s    4.104 s           +0.004 s

Reported as a cross-session change, C1's own-window +0.170 s is "you got slower", and more than all
of it is the detector drawing a longer window; over the stored window it is −0.029 s. So a focus
item stores its window as a FRACTION of the lap odometer and both sides are measured by the same
function over that fraction; the corner id is a label on it, never the identity. A fraction is the
same stretch of track only when the two odometers start in the same place, and since Q2 they do:
both recordings are timed on the built-in Sandown Park line, and across the seven corners 0068's apex
sits −1.0..+0.4 m from 0064's (scaled by the two lap totals). Before Q2 each was cut on the loader's
own line, the two lines sat about 10 m apart, and every apex was offset by −11.2..−8.5 m — the reason
the gate refuses a comparison when either line is provisional. Over the stored windows the seven
corners read −0.281..+0.150 s, while the lap totals agree to 0.93 % (730.6 vs 737.3 m).

Persistence follows ``library.py`` / ``session_record.py`` — schema version read + forward
migration, a ``.bak`` before any un-round-trippable overwrite, atomic write, one bad list dropped
rather than the file, and ``_app_support_dir`` as the single seam the tests (and
``studio/dev/_jail.py``) redirect. Not ``prefs.py``, which is the store that did not have the
discipline.
"""

from __future__ import annotations

import datetime
import logging
import math
import os
import shutil
from dataclasses import dataclass, field

import numpy as np

from . import _jsonstore, app_support, session_record
from ._signal import plural
from .coaching import MIN_CORNER_LAPS, SPREAD_MARGIN

_log = logging.getLogger(__name__)

# v2 (QA LOOK-10): a baseline is measured the way the CORNERS table measures a corner
# (`METHOD_MATCHED`) and carries how many laps it was measured on (`n_of`). A v1 item keeps its
# meaning, `METHOD_FRACTION`, and is re-measured by it (`_migrate`).
VERSION = 2

# How a baseline was measured, stored per item so both halves of a comparison use one instrument.
# FRACTION (v1): every clean lap's time over the window's fractions of its OWN odometer.
# MATCHED (v2): the laps whose spatial matches of both window edges survived, timed between them —
# on the session that promoted it, exactly that corner's CORNERS-table cells (`Session.focus_samples`).
METHOD_FRACTION = "fraction"
METHOD_MATCHED = "matched"
_METHODS = (METHOD_FRACTION, METHOD_MATCHED)

_FILENAME = "focus.json"

# At most three corners. The device this loop is borrowed from lets a driver stack a coaching list
# until it is a to-do list, which is the failure mode `coaching.session_theme` already refused for
# the per-session story: twelve findings is not coaching. Three is what a driver can hold for a
# session, and it is the same shortlist length the panel headline already sums (PANEL_TOP_N).
MAX_ITEMS = 3

# How far the two sessions' lap odometers may disagree before a fraction-mapped window stops being
# the same stretch of track. MEASURED (T16b and Q2, the table at the top of this module): the
# working-set pair's lap totals differ by 6.77 m on 730 m — 0.93 % — which displaces a corner boundary
# by at most ~0.5 m inside a 50 m window. 2 % is twice that: comfortably past any re-fit of the same
# lap, and short of a genuinely different route. (A start line placed somewhere else on the same loop
# leaves the lap total where it was: that is BLOCK_UNVERIFIED's case, and the same pair showed it,
# ~10 m, until Q2 put both on Sandown Park's built-in line.)
MAX_LAP_TOTAL_DRIFT = 0.02


# ----------------------------------------------------------------------------- outcome vocabulary
# What one focused corner's re-measurement came to. The numbers-bearing three are IMPROVED /
# SLOWER / UNCHANGED; NO_VERDICT is the refusal, and it never carries a delta.
OUTCOME_IMPROVED = "improved"
OUTCOME_SLOWER = "slower"
OUTCOME_UNCHANGED = "unchanged"      # the change is inside the wider side's lap-to-lap spread
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
    per-lap times, robust where σ is not (on 0064's C4 σ reads 1.051 s against a 0.231 s IQR)."""

    median: float     # median time through the window (s)
    iqr: float        # interquartile range of the per-lap times (s)
    n_laps: int       # clean laps with a finite time through it
    n_of: int | None = None  # clean laps measured at all (≥ n_laps: a lap whose window edge was
    #                          not matched on track is counted here, never timed); None = unknown


def sample_window(times, n_of: int | None = None) -> CornerSample | None:
    """``CornerSample`` for one window's per-lap times (non-finite dropped). None when nothing
    finite came back — an absent sample is not a zero one. `n_of`: the laps it was measured on."""
    t = np.asarray(list(times), float)
    t = t[np.isfinite(t)]
    if len(t) == 0:
        return None
    q25, q75 = (np.percentile(t, [25, 75]) if len(t) >= 2 else (t[0], t[0]))
    return CornerSample(median=float(np.median(t)), iqr=float(q75 - q25), n_laps=int(len(t)),
                        n_of=None if n_of is None else int(n_of))


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
    n_of: int = 0              # baseline: the clean laps measured at all (0 = not recorded, v1)
    method: str = METHOD_MATCHED  # how the baseline was measured (METHOD_*): the re-measure too

    @property
    def label(self) -> str:
        return f"C{self.cid}"

    @property
    def count_text(self) -> str:
        """"19 laps", or "16 of 19 laps" when some laps' window could not be matched on track."""
        if self.n_of and self.n_of != self.n_laps:
            return f"{self.n_laps} of {plural(self.n_of, 'lap')}"
        return plural(self.n_laps, "lap")


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
        "degraded": bool(item.degraded), "n_of": int(item.n_of), "method": str(item.method),
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
        verified=bool(d.get("verified", False)), degraded=bool(d.get("degraded", False)),
        # An item that does not say how it was measured is a v1 item: the fraction instrument.
        n_of=int(d.get("n_of", 0)), method=str(d.get("method", METHOD_FRACTION)))


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
    n_of = d.get("n_of", 0)
    if isinstance(n_of, bool) or not isinstance(n_of, int) or n_of < 0:
        return False
    return d.get("method", METHOD_FRACTION) in _METHODS


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
    so neither a test nor a harness can reach the user's own focus list; it resolves through
    ``app_support.resolve``, which jails a test that redirects nothing."""
    return app_support.resolve()


def focus_path(path: str | None = None) -> str:
    """Absolute path of the focus store (``<app-support>/pacer/focus.json``)."""
    return path or os.path.join(_app_support_dir(), _FILENAME)


def empty_store() -> dict:
    """A fresh, valid, empty store — the safe default every corruption path returns to."""
    return {"version": VERSION, "lists": []}


def _migrate(data: dict, from_version: int) -> dict:
    """Forward-migrate an OLDER store, PRESERVING every list (``library._migrate``'s whole point:
    a version bump is never "fixed" by wiping the file).

    v1 → v2 (QA LOOK-10): a v1 baseline was measured by `METHOD_FRACTION`, and it KEEPS that
    meaning — each item is stamped with it, and the verdict re-measures it by the same instrument —
    rather than being compared against a differently-measured today (the two run 0.01–0.17 s apart
    per corner, the size of what the verdict compares). A driver's list survives the upgrade and
    still gets its verdict; a corner promoted from now on is measured the matched way."""
    if from_version < 2:
        for e in data.get("lists", []) if isinstance(data.get("lists"), list) else []:
            for item in e.get("items", []) if isinstance(e, dict) and isinstance(
                    e.get("items"), list) else []:
                if isinstance(item, dict):
                    item.setdefault("method", METHOD_FRACTION)
    return data


def load(path: str | None = None) -> dict:
    """Load + validate the store. Never wipes a user's focus list on a version mismatch: an OLDER
    file is migrated forward, a NEWER one (a downgrade) is read best-effort and left for ``save`` to
    back up, and one malformed list is dropped rather than the file. Only genuine FILE-level
    corruption falls back to ``empty_store()``."""
    path = focus_path(path)
    ok, data = _jsonstore.read_object(path)
    if not ok:
        return empty_store()
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        _jsonstore.report_unreadable(path, f"version {version!r} is not a schema number")
        return empty_store()
    if version < VERSION:
        _log.warning("focus: migrating store from version %d to %d (%s)", version, VERSION, path)
        data = _migrate(data, version)
    elif version > VERSION:
        _log.warning("focus: store is version %d, newer than this build's %d — loading "
                     "best-effort (%s)", version, VERSION, path)
    raw = data.get("lists")
    if not isinstance(raw, list):
        _jsonstore.report_unreadable(path, "its lists are not a list")
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
    """Copy an un-round-trippable existing store to its ``.bak`` sidecar before ``save`` would
    overwrite it — the user's bytes are never silently lost. Un-round-trippable is everything
    ``load`` reads as EMPTY (unparseable, a non-int ``version``, a non-list ``lists``) plus a NEWER
    schema; the two valid-JSON shapes used to be overwritten with no copy at all."""
    if not os.path.exists(path):
        return
    ok, data = _jsonstore.read_object(path)
    version = data.get("version") if ok else None
    stamped = isinstance(version, int) and not isinstance(version, bool)
    unsafe = not ok or not stamped or version > VERSION or not isinstance(data.get("lists"), list)
    if not unsafe:
        return
    try:
        shutil.copy2(path, backup_path(path))
        _log.warning("focus: backed up an unreadable/newer store to %s",
                     os.path.basename(backup_path(path)))
    except OSError as exc:
        _log.warning("focus: could not back up %s before overwrite (%r)", path, exc)


def save(store: dict, path: str | None = None) -> None:
    """Write the store atomically (a unique temp file + ``os.replace``, ``_jsonstore.write_json``)
    under the store lock. Creates the app-support dir if missing; raises OSError on an unwritable
    destination (the caller guards it — a focus-list write must never disrupt the app)."""
    path = focus_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {"version": VERSION,
           "lists": [_norm_list(e) for e in store.get("lists", []) if _valid_list(e)]}
    with _jsonstore.locked(path):
        _backup_unsafe(path)
        _jsonstore.write_json(path, out)


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


def rename_track(store: dict, old: str, new: str) -> bool:
    """Move the focus list stored for `old` onto `new` (mutates). True when a list moved.

    The list is keyed by track NAME (``for_track`` / ``set_for_track``), so a circuit renamed in the
    track database without this leaves the corners the driver is working on UNREACHABLE: the next
    session at that circuit detects the new name and finds no list, and the training loop silently
    restarts. The items themselves are untouched — each still carries the baseline it was promoted
    against, and the whole point of that baseline is that it survives.

    If a list already stands under `new` — only reachable when that name belonged to a circuit since
    deleted — the MOVED list wins, because it is the one belonging to a circuit that still exists,
    and the stranded one is dropped rather than silently merged into it."""
    lists = store.setdefault("lists", [])
    moving = next((e for e in lists if e.get("track") == old), None)
    if moving is None:
        return False
    lists[:] = [e for e in lists if e.get("track") != new or e is moving]
    moving["track"] = str(new)
    return True


def rename_track_and_save(old: str, new: str, path: str | None = None) -> bool:
    """Load, move `old`'s focus list onto `new`, write back atomically. True when one moved — and
    only then is anything written, so renaming a circuit with no focus list cannot churn the file."""
    path = focus_path(path)
    with _jsonstore.locked(path):
        store = load(path)
        moved = rename_track(store, old, new)
        if moved:
            save(store, path)
    return moved


def save_for_track(track: str, items: list[FocusItem], path: str | None = None) -> dict:
    """Load, replace `track`'s list, write back atomically, return the new store — under the store
    lock, so another writer's list cannot be lost in between. The one call the app makes when the
    driver promotes or drops a corner."""
    path = focus_path(path)
    with _jsonstore.locked(path):
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
    karts: tuple[str, str] | None = None  # (then, now) kart numbers when a VERDICT crossed two
                                    #   different karts (`session_record.kart_pair`) — said, not
                                    #   refused: a fleet kart changes every session

    @property
    def has_verdict(self) -> bool:
        return self.kind in (OUTCOME_IMPROVED, OUTCOME_SLOWER, OUTCOME_UNCHANGED)


@dataclass(frozen=True)
class Report:
    """What the focus list has to say about the session in front of the driver."""

    track: str | None
    outcomes: list[Outcome] = field(default_factory=list)
    # The sessions a BLOCK_NO_RECORD refusal is waiting on, as (fingerprint, "19 Jul" | "today"):
    # the earlier days first in list order, today last, each once. Exactly what the focus block's
    # one-click "Mark both dry" offers to write — and nothing it may not (`mark_dry_prompt`).
    unrecorded: tuple[tuple[str, str], ...] = ()

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
    now_fp = str(now_ctx.get("fingerprint") or "")
    rec_now = session_record.get(store, now_fp)
    outcomes: list[Outcome] = []
    unrecorded: dict[str, str] = {}
    for i, item in enumerate(items):
        now = samples[i] if i < len(samples) else None
        if item.fingerprint and item.fingerprint == now_fp:
            outcomes.append(Outcome(item=item, kind=OUTCOME_SET_HERE, now=now, delta=None))
            continue
        rec_then = session_record.get(store, item.fingerprint)
        block, detail = _blocker(item, now_ctx, rec_then, rec_now)
        if block:
            if block == BLOCK_NO_RECORD and item.fingerprint and session_record.is_empty(rec_then):
                unrecorded.setdefault(item.fingerprint, _when(item.date))
            outcomes.append(Outcome(item=item, kind=OUTCOME_NO_VERDICT, now=now, delta=None,
                                    blocker=block, detail=detail))
            continue
        if now is None or now.n_laps < MIN_CORNER_LAPS:
            outcomes.append(Outcome(item=item, kind=OUTCOME_NO_VERDICT, now=now, delta=None,
                                    blocker=BLOCK_FEW_LAPS,
                                    # 0, 1 or 2 by this branch's own test, so the singular is
                                    # one of the three values it can print.
                                    detail=plural(now.n_laps if now else 0, "clean lap")))
            continue
        delta = now.median - item.median_s
        # The SAME actionability test the coaching gate applies to a within-session claim, on the
        # same statistic (`coaching.SPREAD_MARGIN` × the interquartile spread) rather than a second
        # notion of significance invented here. The wider of the two sessions' spreads is the bar,
        # because a change is only as aimable as the noisier side of the comparison. With 62 and 36
        # laps the standard error of either median is ~0.02-0.04 s (the normal approximation,
        # 1.2533 × IQR / 1.349 / √laps, over the six samples in the table at the top of this module)
        # and a significance test would pass almost anything: this asks whether a driver could aim
        # at the difference, not whether it is real.
        spread = max(item.iqr_s, now.iqr)
        if abs(delta) < SPREAD_MARGIN * spread:
            kind = OUTCOME_UNCHANGED
        else:
            kind = OUTCOME_IMPROVED if delta < 0 else OUTCOME_SLOWER
        outcomes.append(Outcome(item=item, kind=kind, now=now, delta=float(delta),
                                karts=session_record.kart_pair(rec_then, rec_now)))
    if any(o.blocker == BLOCK_NO_RECORD for o in outcomes) and now_fp \
            and session_record.is_empty(rec_now):
        unrecorded.setdefault(now_fp, "today")
    return Report(track=now_ctx.get("list_track") or now_ctx.get("track"), outcomes=outcomes,
                  unrecorded=tuple(unrecorded.items()))


# ------------------------------------------------------------------------------------- the words
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _when(date: str | None) -> str:
    """"23 May" from an ISO date — the short form a verdict line says a baseline was set on.
    "last time" when the recording carried no date — no wall clock on its fixes at all, which is
    NOT the GPS5 era (that stamps every fix from GPSU; see Session.session_date)."""
    if not date or len(date) < 10:
        return "last time"
    # A CALENDAR day, not two in-range-looking integers: the stored date is a string the store only
    # checks IS a string, and indexing `_MONTHS[int("00") - 1]` does not raise — it is December, so
    # "2026-00-15" used to be stated as a baseline set on "15 Dec" (and a day of 00 as "0 May").
    try:
        day = datetime.date(int(date[0:4]), int(date[5:7]), int(date[8:10]))
    except ValueError:
        return "last time"
    return f"{day.day} {_MONTHS[day.month - 1]}"


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
                f"{o.item.count_text}). Next time you're here, Pacer will say whether it moved.")
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
    if o.karts:
        # Not silently like-for-like: the kart changed under the comparison, and the line says so
        # in the same then → now grammar as the two pairs before it (session_record.kart_pair).
        body += f", kart {o.karts[0]} → {o.karts[1]}"
    if o.kind == OUTCOME_UNCHANGED:
        return (f"{label} — no change you can act on: {abs(o.delta):.2f} s apart, inside the "
                f"corner's own {max(o.item.iqr_s, o.now.iqr):.2f} s spread ({body}).")
    word = "faster" if o.kind == OUTCOME_IMPROVED else "slower"
    return f"{label} — {abs(o.delta):.2f} s {word} than {when} ({body})."


def mark_dry_prompt(report: Report | None) -> tuple[str, str, str] | None:
    """(question, button, hover) for the focus block's one-click answer to a BLOCK_NO_RECORD
    refusal — ``("Both dry?", "Mark both dry", …)`` — or None when nothing is waiting on a record.

    It is an OFFER, never an assumption (conditions are typed by the driver, never fetched or
    guessed — ``session_record``'s docstring): nothing is written until the click, the button names
    exactly which sessions it writes, and the hover says what goes in them — conditions Dry and
    nothing else. Only a session with NO record is offered; one that has any record is never
    touched (``session_record.put_if_blank_and_save`` enforces it at the write)."""
    names = [label for _fp, label in (report.unrecorded if report is not None else ())]
    if not names:
        return None
    if len(names) == 1:
        name = names[0]
        question = "Dry today?" if name == "today" else f"Dry on {name}?"
        button = f"Mark {name} dry"
    elif len(names) == 2:
        question, button = "Both dry?", "Mark both dry"
    else:
        question, button = f"All {len(names)} dry?", f"Mark all {len(names)} dry"
    whom = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    hover = (f"Writes a session record for {whom} saying the conditions were Dry — and nothing "
             f"else. File ▸ Session record… adds the rest, or corrects it.")
    return question, button, hover


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
        # The lap count each baseline was ACTUALLY measured on: one count when they share it, else
        # one per corner — C2 over "16 of 19 laps" is not C5 over 19 (QA LOOK-10).
        counts = {o.item.count_text for o in outcomes}
        if len(counts) == 1:
            said = (f"{', '.join(f'{o.item.median_s:.2f} s' for o in outcomes)} over "
                    f"{counts.pop()}")
        else:
            said = ", ".join(f"{o.item.median_s:.2f} s over {o.item.count_text}" for o in outcomes)
        return [f"{_corner_list(outcomes)} — baselines measured on this session ({said}). Next "
                f"time you're at this track, Pacer measures the same stretches again and says "
                f"whether they moved."]
    return [outcome_sentence(o) for o in outcomes]


def replace_offer(report: Report | None, shortlist: list[int]) -> list[int] | None:
    """Today's ranked top corners, when the focus block should offer to REPLACE the list with them
    (QA NEW-5) — else None.

    After the check has run: the list holds a corner set on an EARLIER session, nothing is waiting
    on a session record (the one-click "Mark both dry" comes first, so the verdict is read before
    the list moves on), and today's shortlist is not already the list. Measured on the owner's
    SD_30_08 → SD_19_09: the list stayed 30 Aug's C7/C5/C3 while the page said "Start with C1",
    and the next Sandown check would have re-measured the old three against the old baselines.
    Replacing is the driver's click, never a default: a list he chose must not move under him."""
    cids = [int(c) for c in shortlist or []]
    if report is None or not report.active or not cids or report.unrecorded:
        return None
    if all(o.kind == OUTCOME_SET_HERE for o in report.outcomes):
        return None
    if sorted(o.item.cid for o in report.outcomes) == sorted(cids):
        return None
    return cids[:MAX_ITEMS]


def replace_label(cids: list[int]) -> str:
    """The offer's button: "Replace with today's top 3 (C1, C5, C7)"."""
    return f"Replace with today's top {len(cids)} ({', '.join(f'C{c}' for c in cids)})"


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
