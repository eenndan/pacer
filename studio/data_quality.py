"""Data-quality signal for a loaded recording — the timing-accuracy axis.

PACER-FREE BY CONTRACT (pure value object + plain helpers; no pacer / no Qt). This is the
SECOND, orthogonal quality axis to the timing-TRUST surface (Session.timing_verified, PR #40):

  * timing TRUST  — is the start/finish line trusted (auto-detected / user-confirmed)? It governs
    whether a "best" measured against the line is meaningful.
  * timing QUALITY — is the per-sample TIMING itself accurate, and were the GPS fixes good? This
    is what `TimingQuality` carries. A media-clock recording produces fully-segmented laps off a
    trusted line, yet the times still drift ~0.1% (older GoPro without GPS9); and a trace whose
    DOP/fix gate rejected a large fraction of fixes is geometrically degraded. Both render the
    lap times with the same de-emphasis the trust surface already provides.

The load pipeline (studio/load.py + studio/_signal.py) computes the raw signals; this module just
classifies them into UI-facing concerns. The views render them through the shared banner/theme infra.

`QualityTimeline` at the foot of the file is the same fact made LOCATABLE — one cell per second of
recording instead of one verdict for the whole of it. See its own doc for why the two are not the
same surface.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import MAX_DOP

# Timing-clock provenance — which per-sample time axis the load path actually built.
GPS9_TRUECLOCK = "gps9_trueclock"        # GPS9 per-sample fix spacing (the validated headline path)
MEDIA_CLOCK_FALLBACK = "media_clock_fallback"  # naive media clock (older GPS5 camera, no GPS9)

# A dropped-fix fraction at/above this reads as "GPS quality low" in the UI (a few rejected fixes
# on an otherwise clean trace is normal and not worth a banner). 8% ≈ a fix every ~12 s on a 10 Hz
# stream rejected — conservative, so the badge only fires on a genuinely degraded recording.
DROPPED_FIX_CONCERN_FRAC = 0.08

# --- SHARED no-complete-laps copy (one source, same reason as the degraded-timing copy below).
# A 0-valid-lap recording is stated by EVERY panel at once, and each call site authored its own
# sentence: the lap table said "No complete laps in this recording.", the map "No complete laps
# found in this recording.", the charts "No lap data to plot." and the status bar "no complete laps
# detected in this recording — the GPS may not have locked, or the recording is too short" — four
# phrasings of one fact in a single frame, with the status bar restating the table's reason almost
# verbatim (QA L10-08).
#
# ...AND THEN NOTHING READ IT. Measured on the shipped app (QA D2-02): a grep over `studio/` found
# NO_LAPS_REASON and NO_LAPS_NEXT_ACTION at their DEFINITION LINES ONLY — zero consumers — while
# four panels kept private copies and two of those had diverged. The design that left them
# divergable was the one in the paragraph above: "each surface appends only what it can add", with
# the panels owning the REASON and the map owning the NEXT ACTION. On a zero-lap recording every
# one of those surfaces is on screen AT ONCE, so what the split actually produced was the lap panel
# saying "Open another recording with ⌘O" while the charts panel and the map, 523 px to its right
# in the same frame, said "drag the start/finish line" — two mutually exclusive instructions for
# one fact (QA D2-01, HIGH).
#
# So there is no per-surface subset any more. There are two ways out of a zero-lap recording —
# the line is in the wrong place, or the recording is the wrong recording — and `no_laps_body()`
# is the only way to ask for either, which is what makes stating one without the other impossible
# rather than merely discouraged. The status bar still carries the HEADLINE alone: it has one line,
# and a headline is the half that is true in one line.
NO_LAPS_HEADLINE = "No complete laps in this recording."
NO_LAPS_REASON = ("The GPS may not have locked, or the recording is too short to cross the "
                  "start/finish line.")
NO_LAPS_NEXT_ACTION = ("If this is the right track, drag the start/finish line on the map to set "
                       "where a lap begins.")
# The second way out, and it is an ADDITION rather than a replacement: `lap_table.NO_LAPS_ACTION`
# said this and ONLY this, which is how the contradiction arose. Its intent survives here, stated
# after the drag action rather than instead of it.
NO_LAPS_ALT_ACTION = "If it is the wrong recording, open another with ⌘O."


def no_laps_body() -> str:
    """The zero-lap BODY, whole: why it happened, then BOTH ways out, in that order.

    THE FUNCTION IS THE FIX — the constants alone were already there and were already ignored. A
    surface cannot state one action without the other because there is no way to ask for half of
    this string, and a surface that re-authors it has to do so visibly, next to a call that says
    what it should have said. `tests/test_state_surfaces.py` reads every zero-lap surface's live
    text and asserts this exact string is inside it."""
    return f"{NO_LAPS_REASON} {NO_LAPS_NEXT_ACTION} {NO_LAPS_ALT_ACTION}"


@dataclass(frozen=True)
class TimingQuality:
    """The data-quality verdict for one loaded recording (pure value object on Session).

      * `clock` — GPS9_TRUECLOCK or MEDIA_CLOCK_FALLBACK (which per-sample time axis was built);
      * `dropped_fraction` — fraction of raw GPS fixes the DOP/fix quality gate rejected, in [0, 1].

    `degraded` is True when EITHER concern fires; the views show the banner/badge + de-emphasize
    the lap times only then, so a normal GPS9 recording is visually identical to today."""

    clock: str = GPS9_TRUECLOCK
    dropped_fraction: float = 0.0

    @property
    def media_clock(self) -> bool:
        """True when timing fell back to the (~0.1%-fast) media clock — an older GPS5 camera."""
        return self.clock == MEDIA_CLOCK_FALLBACK

    @property
    def low_gps_quality(self) -> bool:
        """True when the quality gate rejected a concerning fraction of fixes."""
        return self.dropped_fraction >= DROPPED_FIX_CONCERN_FRAC

    @property
    def degraded(self) -> bool:
        """True when ANY data-quality concern applies — the views demote the timing only then."""
        return self.media_clock or self.low_gps_quality

    def dropped_pct(self) -> int:
        """The rejected-fix percentage (rounded) surfaced to the user — the exact figure the
        low-GPS concern reports, so no consumer has to re-derive or collapse it to "some fixes"."""
        return round(self.dropped_fraction * 100)

    def concerns(self) -> list[str]:
        """Human-readable concern lines (most-significant first), one per active issue — the
        text the data-quality banner stacks. Empty when the timing is fully high-quality."""
        out: list[str] = []
        if self.media_clock:
            out.append(
                "Timing estimated from the video clock (older GoPro without GPS9) — "
                "lap times may drift ~0.1%.")
        if self.low_gps_quality:
            out.append(
                f"GPS quality low — {self.dropped_pct()}% of fixes were rejected; "
                "times may be less accurate.")
        return out

    # --- SHARED degraded-timing copy (one source, so the map banner, the lap-table Time tooltip,
    # the footer tiles and the header chip can never disagree — the M3 fix). Both derive from the
    # SAME flags/percent, and both split by CLOCK PROVENANCE: "estimated"/"video clock" wording is
    # reserved for the media-clock fallback that actually estimates the times; a low-GPS-only,
    # true-clock recording says "GPS quality low — some fixes rejected" (no "estimated", which
    # overclaimed on true-clock footage). Empty string when not degraded.
    def summary(self) -> str:
        """A single COMPACT line summarising the active data-quality concern(s) — the map banner's
        FYI line, and the same wording every other degraded-timing surface shows. One concern reads
        as its own short summary (the low-GPS case surfaces the exact rejected-fix %); both collapse
        to a combined one-liner."""
        media, low = self.media_clock, self.low_gps_quality
        if media and low:
            return (f"Timing estimated (video clock) and GPS quality low — "
                    f"{self.dropped_pct()}% of fixes rejected; times may be less accurate.")
        if media:
            return "Timing estimated from the video clock — lap times may drift ~0.1%."
        if low:
            return (f"GPS quality low — {self.dropped_pct()}% of fixes rejected; "
                    "times may be less accurate.")
        return ""

    def detail(self) -> str:
        """The fuller TOOLTIP prose for the degraded-timing surfaces (lap-table Time cells + footer
        tiles + the header chip) — the same clock-aware split as summary(), one paragraph. Reserves
        the "estimated"/"video clock" language for the media-clock fallback; a true-clock recording
        whose only concern is rejected fixes gets the low-GPS wording (no "estimated"). Empty string
        when not degraded."""
        media, low = self.media_clock, self.low_gps_quality
        if media:
            base = ("Lap times are estimated from the video clock (an older GoPro without GPS9), "
                    "which runs ~0.1% fast — treat the absolute times as approximate.")
            if low:
                base += (f" GPS quality is also low: {self.dropped_pct()}% of fixes were rejected, "
                         "so the positions are less accurate too.")
            return base + " See the note over the map."
        if low:
            return (f"GPS quality low for this recording — {self.dropped_pct()}% of fixes were "
                    "rejected, so the positions (and the times derived from them) may be less "
                    "accurate. See the note over the map.")
        return ""


# ============================================================ the LOCATABLE half of the verdict
# `TimingQuality` above is ONE verdict for a WHOLE recording, and that is the shape of the thing it
# cannot say. Measured on the owner's own recordings: the D24 0062 trio rejects 482 of 50,492 fixes
# — 0.96 %, which rounds to "1 % of fixes rejected" on the card and tells you nothing. WHERE they
# are is the entire fact: ALL 482 land in the first 48.2 seconds, before the kart has moved (the
# first fix above 3 m/s is at t=167.6 s), because that is the receiver acquiring a lock. The same
# 0.96 % scattered through the middle of the session would be a broken receiver, and the card
# prints the same sentence for both.
#
# That distinction is also a bug this repo already shipped: the dropped-fix fraction was taken over
# the RAW fix count INCLUDING that stationary lead-in, so opening one chapter read 0.12 and the
# same footage as three read 0.04 — a clean recording called degraded purely by how it was opened
# (load.py's `moving_speed` gate is the fix). A strip that draws the lead-in as its own block at
# the head of the bar makes that a glance instead of an inference.
#
# THE CLASSES ARE THE GNSS CONVENTION, not a scale invented here: the standard DOP rating table is
# ideal (<1) / excellent (1-2) / good (2-5) / moderate (5-10) / fair (10-20) / poor (>20), and a
# non-expert acts on the WORD, not on the number. Collapsed to what this app can act on: at or
# under 5 is GOOD (ideal through good); 5 to `MAX_DOP` is MODERATE — still used, but one step from
# being thrown away; above `MAX_DOP`, or without a 3D lock, is POOR, which is exactly the set the
# loader's quality gate rejects. The boundaries are `_signal`'s own `MIN_FIX` / `MAX_DOP` so the
# strip and the gate can never disagree about what was thrown away.

#: The GNSS "good or better" DOP bound (the ideal/excellent/good band of the standard rating
#: table). Above it a fix is still USED — `MAX_DOP` is where the loader rejects — but it is worth
#: drawing differently, because it is the band a degrading receiver passes through on its way out.
DOP_GOOD_MAX = 5.0

#: One cell = one second of recording. A second is the unit a driver locates by ("the first minute
#: was rubbish"), it is ~10 GPS fixes at the GoPro's rate, and it is finer than any pixel the strip
#: will ever have — the widget folds cells into pixel columns WORST-FIRST, so one bad second can
#: never be averaged out of existence by the thousands of good ones around it.
CELL_S = 1.0
#: …but not without bound: past this the cell grows, so no input can allocate arbitrarily. 7,200
#: one-second cells is a two-hour recording; the longest D24 recording is 5,050 s.
MAX_CELLS = 7200

# The cell classes, ORDERED so that `min()` means "the worst of these" — which is how a pixel
# column folds its cells and how a lap inherits a class from the cells under it.
NO_FIX = 0        # not one GPS fix landed in this span
POOR = 1          # every fix here was rejected: no 3D lock, or DOP above MAX_DOP
MODERATE = 2      # kept but degrading: worst DOP in (DOP_GOOD_MAX, MAX_DOP], or some fixes dropped
GOOD = 3          # a 3D lock throughout, DOP inside the GNSS good band, nothing rejected
UNREPORTED = 4    # fixes arrived, and this camera writes no per-sample quality at all (GPS5)

#: The WORD for each class — the half a non-expert acts on (see the GNSS note above).
QUALITY_LABEL = {NO_FIX: "No fix", POOR: "Poor", MODERATE: "Moderate", GOOD: "Good",
                 UNREPORTED: "Not reported"}

#: …and what it means. The label alone is a word; this is the sentence behind it.
QUALITY_MEANING = {
    NO_FIX: "no GPS fix arrived here at all",
    POOR: f"every fix here was rejected — no 3D lock, or DOP above {MAX_DOP:g}",
    MODERATE: f"usable but degrading — DOP above {DOP_GOOD_MAX:g}, or some fixes here rejected",
    GOOD: f"3D lock, DOP inside the GNSS good band (at or under {DOP_GOOD_MAX:g})",
    UNREPORTED: ("this camera writes no per-sample GPS quality — the GPS5-era stream carries "
                 "neither a fix type nor a DOP, so there is nothing to grade"),
}

#: The classes worth LOOKING for on the bar — what `concern_seconds` counts.
CONCERN_CLASSES = (NO_FIX, POOR, MODERATE)


@dataclass(frozen=True)
class QualityTimeline:
    """Per-second GPS quality over one recording, on the media clock the scrubber uses.

    Built from the RAW fixes — before the loader's quality gate drops the bad ones and before
    `_clean` trims the stationary lead-in — because the dropped and the trimmed are precisely what
    this surface exists to show. One entry per cell in each parallel array:

      * `cls`     — the cell's class (NO_FIX … UNREPORTED), `min()`-ordered worst-first;
      * `n`       — raw fixes that landed in the cell;
      * `dropped` — how many of those the quality gate rejected;
      * `dop`     — the WORST (largest) DOP among the fixes the gate KEPT; NaN when it kept none.

    `span_s` is the recording's own length (the video duration), NOT the last fix's timestamp: the
    strip has to cover exactly what the scrubber covers, so a receiver that stops reporting for the
    last four minutes leaves four minutes of empty bar instead of quietly rescaling.

    Frozen, like `TimingQuality`; its arrays are never written after construction."""

    cell_s: float
    cls: np.ndarray
    n: np.ndarray
    dropped: np.ndarray
    dop: np.ndarray
    reports_quality: bool

    def __len__(self) -> int:
        return int(len(self.cls))

    @property
    def span_s(self) -> float:
        """The recording length this timeline covers, in seconds."""
        return float(len(self.cls) * self.cell_s)

    def _bounds(self, t0: float, t1: float) -> tuple[int, int]:
        """The half-open cell range [i0, i1) covering [t0, t1] seconds, clamped into the timeline.

        A zero-width query — a hover at one instant, a lap window that collapsed — still names ONE
        cell rather than none: a caller asking "what is the quality HERE" must get an answer."""
        n = len(self.cls)
        if n == 0:
            return 0, 0
        if t1 < t0:
            t0, t1 = t1, t0
        i0 = min(max(int(t0 // self.cell_s), 0), n - 1)
        i1 = min(max(int(-(-t1 // self.cell_s)), i0 + 1), n)
        return i0, i1

    def worst_between(self, t0: float, t1: float) -> int | None:
        """The WORST class over [t0, t1] seconds; None when there is no timeline.

        Worst, never average. It is what makes a lap inherit the one bad second inside it, and what
        stops a pixel column three cells wide from painting the mean of a dropout and two clean
        seconds as "fine"."""
        i0, i1 = self._bounds(t0, t1)
        if i1 <= i0:
            return None
        return int(self.cls[i0:i1].min())

    def stats_between(self, t0: float, t1: float) -> dict | None:
        """The exact numbers behind `worst_between` — what the hover prints.

        `dop` is the worst DOP among the KEPT fixes of the span (NaN if it kept none), so a span
        whose only readable fixes were thrown away reports no DOP rather than the DOP of a fix the
        app refused to use."""
        i0, i1 = self._bounds(t0, t1)
        if i1 <= i0:
            return None
        dop = self.dop[i0:i1]
        finite = dop[np.isfinite(dop)]
        return {
            "cls": int(self.cls[i0:i1].min()),
            "t0": float(i0 * self.cell_s),
            "t1": float(i1 * self.cell_s),
            "n": int(self.n[i0:i1].sum()),
            "dropped": int(self.dropped[i0:i1].sum()),
            "dop": float(finite.max()) if len(finite) else float("nan"),
            "cells": int(i1 - i0),
        }

    def counts(self) -> dict[int, int]:
        """How many cells of each class — `{class: cells}`, only the classes actually present."""
        if not len(self.cls):
            return {}
        vals, cnt = np.unique(self.cls, return_counts=True)
        return {int(v): int(c) for v, c in zip(vals.tolist(), cnt.tolist(), strict=True)}

    @property
    def worst(self) -> int | None:
        """The worst class anywhere in the recording; None when there is no timeline."""
        return int(self.cls.min()) if len(self.cls) else None

    def concern_seconds(self) -> float:
        """Seconds of recording that are anything but GOOD (or UNREPORTED) — the one number that
        says whether this strip is worth looking at on THIS recording."""
        if not len(self.cls):
            return 0.0
        return float(np.isin(self.cls, CONCERN_CLASSES).sum() * self.cell_s)

    def summary(self) -> str:
        """One line: how much of the recording is in each concerning class, worst first.

        Deliberately in SECONDS rather than percent — "1 % of fixes rejected" is what the
        whole-recording verdict already says, and it is the phrasing that hides the lead-in."""
        if not len(self.cls):
            return ""
        if not self.reports_quality:
            return (f"GPS quality not graded over {self.span_s:.0f} s — "
                    f"{QUALITY_MEANING[UNREPORTED]}")
        counts = self.counts()
        parts = [f"{counts[c] * self.cell_s:.0f} s {QUALITY_LABEL[c].lower()}"
                 for c in CONCERN_CLASSES if counts.get(c)]
        if not parts:
            return f"GPS quality good over all {self.span_s:.0f} s of the recording"
        return (f"GPS quality over {self.span_s:.0f} s of recording: " + ", ".join(parts)
                + ", the rest good")


def empty_timeline() -> QualityTimeline:
    """A timeline covering nothing — what a Session with no recording (and the no-__init__ test
    path) reads. Every accessor on it answers None / 0 rather than raising."""
    return QualityTimeline(cell_s=CELL_S, cls=np.empty(0, np.int8), n=np.empty(0, np.int32),
                           dropped=np.empty(0, np.int32), dop=np.empty(0), reports_quality=False)


def build_quality_timeline(times, rejected, dop, span_s: float,
                           cell_s: float = CELL_S) -> QualityTimeline:
    """Grade every second of a recording → `QualityTimeline`.

      * `times`    — media-clock seconds of each RAW fix (pre-gate, pre-trim);
      * `rejected` — the per-fix verdict from `_signal._quality_ok` ITSELF, so the strip can never
                     disagree with the gate about what was thrown away. Passed in rather than
                     re-derived here for exactly that reason;
      * `dop`      — per-fix GPS9 DOP; sentinels (non-finite, or <= 0) allowed;
      * `span_s`   — the recording's own length on that clock (the VIDEO duration; see the class
                     doc for why not the last fix's time).

    A recording whose camera reports no per-sample quality at all (GPS5: every `dop` a sentinel)
    comes back UNREPORTED wherever fixes landed — never GOOD. Painting a confident green over a
    stream carrying nothing to be confident about is the one failure mode this surface must not
    have."""
    t = np.asarray(times, float)
    span = float(span_s) if np.isfinite(span_s) and span_s > 0 else (
        float(t[-1]) if len(t) else 0.0)
    if span <= 0:
        return empty_timeline()
    cell = max(float(cell_s), span / MAX_CELLS)
    ncell = max(int(np.ceil(span / cell)), 1)
    n_arr = np.zeros(ncell, np.int32)
    drop_arr = np.zeros(ncell, np.int32)
    dop_arr = np.full(ncell, np.nan)
    cls = np.full(ncell, NO_FIX, np.int8)
    if not len(t):
        return QualityTimeline(cell_s=cell, cls=cls, n=n_arr, dropped=drop_arr, dop=dop_arr,
                               reports_quality=False)

    rej = np.asarray(rejected, bool)
    d = np.asarray(dop, float)
    m = min(len(t), len(rej), len(d))
    t, rej, d = t[:m], rej[:m], d[:m]
    # A fix a hair outside the declared span still belongs to the recording (the GPMF track can run
    # past the video track — measured at up to +0.934 s on GoPro's own sample clips), so clamp it
    # into the end cell instead of dropping it out of the counts.
    idx = np.clip((t / cell).astype(np.int64), 0, ncell - 1)
    n_arr += np.bincount(idx, minlength=ncell).astype(np.int32)
    drop_arr += np.bincount(idx, weights=rej.astype(float), minlength=ncell).astype(np.int32)

    known = np.isfinite(d) & (d > 0)
    reports = bool(known.any())
    keep = ~rej & known
    if keep.any():
        # The worst DOP among the KEPT fixes of each cell: `np.maximum.at` over a -inf seed, then
        # back to NaN where no kept fix landed. Seeding with NaN instead PROPAGATES — NaN wins every
        # `maximum`, so the whole array comes back NaN, and it does it silently (measured: the first
        # cut of this measurement reported "0 cells above DOP 2" on a recording where 61.6 % of the
        # fixes are).
        seeded = np.full(ncell, -np.inf)
        np.maximum.at(seeded, idx[keep], d[keep])
        dop_arr = np.where(np.isfinite(seeded), seeded, np.nan)

    covered = n_arr > 0
    if not reports:
        cls[covered] = UNREPORTED
        return QualityTimeline(cell_s=cell, cls=cls, n=n_arr, dropped=drop_arr, dop=dop_arr,
                               reports_quality=False)
    cls[covered] = GOOD
    # Worst-wins, applied in worsening order so the later rule overwrites the earlier one.
    cls[covered & (dop_arr > DOP_GOOD_MAX)] = MODERATE
    cls[covered & (drop_arr > 0) & (drop_arr < n_arr)] = MODERATE
    cls[covered & (drop_arr >= n_arr)] = POOR
    return QualityTimeline(cell_s=cell, cls=cls, n=n_arr, dropped=drop_arr, dop=dop_arr,
                           reports_quality=True)


# ======================================================== the QUALITY-MARKER VOCABULARY
# Pacer has always marked a number it cannot fully stand behind. It did it in a HOUSE style, one
# mark per surface, each defined where that surface lives: `theme.ESTIMATED_MARK` = "(est)", the
# muted+italic provisional demotion (`theme.apply_provisional_style`), `lap_table.DROPOUT_MARK` =
# "⚠", `lap_table.EXCLUDED_MARK` = "⊘". Every one of them is measured into the shipped face and
# legended on the surface that paints it. Nothing below replaces any of them.
#
# What the house style could not do is travel. The UK Government Analysis Function publishes a
# STANDARD set of shorthand symbols for exactly this job ("Symbols in tables"), designed to be
# read by a screen reader and decoded from a key: [c] confidential · [e] estimated · [b] break in
# series · [f] forecast · [p] provisional · [r] revised · [u] low reliability · [x] not available ·
# [z] not applicable. Its guidance explicitly warns AGAINST `*`, `†`, `.` and `:`, which are hard
# to see and which screen readers handle badly — pacer uses none of those four, so adopting the
# standard costs it nothing it was relying on.
#
# ── WHERE THE STANDARD IS ADOPTED, AND WHERE IT IS NOT ────────────────────────────────────────
# The decision is PER SURFACE FAMILY before it is per marker, because the standard is a convention
# for a TABLE WITH A KEY UNDER IT, and pacer has two kinds of surface:
#
#   * LIVE, IN-APP. A cell has hover, colour, weight and a legend on the glyph itself. A one-letter
#     code there is strictly worse than the mark it would replace: "(est)" is a word and decodes
#     itself, "[e]" needs a key that a table cell has nowhere to put. The in-app marks STAY. This
#     is not conservatism — #189/#237 and tests/test_glyph_vocabulary.py spent real measurement
#     getting ⚠, ⊘ and ▲ into the app's own face at the right size; swapping them for letters
#     would throw that away and buy nothing a tooltip does not already give.
#   * LEAVING THE APP. laps.csv and the HTML report are tables with no hover, read by a person in
#     a spreadsheet or by a script. They are the standard's own design target, and they are where
#     pacer's disclosures have repeatedly been found thin (§5.4). The codes are adopted THERE.
#   * BURNED INTO A PICTURE. The overlay export (studio/export_video.py) is a THIRD family and it
#     was named as the one genuine leaving-the-app surface the paragraph above does not cover. It
#     takes NO codes at all — see the block below for the whole argument.
#
# Per marker, then — including the ones that do not fit, because a vocabulary forced onto the last
# two cases is worse than the house style:
#
#   [e] estimated       ADOPTED (exports). The media-clock fallback: an older GoPro without GPS9,
#                       so the times are estimated from the video clock. In-app this stays
#                       `theme.ESTIMATED_MARK`, which also covers the inferred CHANNELS (grip, the
#                       brake/throttle band) that never reach a lap row.
#   [p] provisional     ADOPTED (exports). The start/finish line was auto-fitted and not confirmed,
#                       so every time is measured from an arbitrary point and a drag WILL revise
#                       it — which is precisely what the standard means by provisional. laps.csv
#                       carried no per-row marker for this at all before; the app's own word for
#                       it is already "Provisional timing".
#   [u] low reliability ADOPTED (exports). A GPS dropout inside the lap (the ⚠ rule), or a
#                       recording whose quality gate rejected a concerning share of fixes. It is
#                       deliberately NOT extended to a lap whose `QualityTimeline` class is merely
#                       below GOOD, and that refusal is measured rather than cautious: on the D24
#                       0060 pair 17 of the 38 clean laps contain a second outside the GNSS good
#                       band, so that rule would mark 45 % of the rows of a recording the app
#                       itself reports as clean (0 rejected fixes, no dropout, not degraded). The
#                       card states that count as a fact and the strip shows where; no in-app
#                       surface DEMOTES those laps, and an export must not invent a demotion the
#                       app does not make. The codes follow the app's verdicts; they do not add to
#                       them.
#   [b] break in series ADOPTED (exports + the Stats DATA TRUST card) — and NEW. See
#                       `break_in_series`: pacer has two genuine instances and had no name for
#                       either.
#   [x] not available   NOT ADOPTED. The app already prints `_signal.DASH` (an em-dash) for a value
#                       it does not have, from one source, on every surface including the exports —
#                       and the em-dash is not one of the four characters the guidance warns about.
#                       A second spelling of a thing that already has one is drift, not a standard.
#   [z] not applicable  NOT ADOPTED, same reason and one more: pacer's rule is None-not-zero, and a
#                       statistic that does not apply is OMITTED (the row, the tile, the group), not
#                       printed as a blank with a code. No surface needs to tell [x] from [z].
#   [r] revised         NOT ADOPTED, and this is the closest call. Dragging the start/finish line
#                       DOES revise every lap time and re-cut the corner partition. But [r] marks a
#                       figure in a series a reader may hold an earlier copy of, and pacer publishes
#                       no series — each export is a fresh document of the session as it stands. A
#                       code that fired on every number after a drag the user just performed is
#                       noise, and the honest disclosure for that case is [p], which is already on.
#   [f] forecast        NOT APPLICABLE. Nothing here predicts a future value. The ideal lap is the
#                       nearest thing and it is emphatically NOT a forecast — it is an order
#                       statistic over laps already driven (corner_model.IdealSample), and marking
#                       it [f] would be exactly the overclaim that class exists to prevent.
#   [c] confidential    NOT APPLICABLE. A local single-user tool suppresses nothing for disclosure
#                       control.
#
# ── THE ONE HOUSE MARK WITH NO STANDARD EQUIVALENT ───────────────────────────────────────────
# ⊘ EXCLUDED — a substantial lap the median-distance band left OUT of the times, bests and
# coaching. The two nearest codes are [x] (not available) and [z] (not applicable) and the lap is
# NEITHER: it was measured, its time exists, the app shows it in the strip. It is simply not IN
# the statistics. There is no standard symbol for "measured, shown, and deliberately not counted",
# so ⊘ and its sentence stay, and the exports state the count in words (they never wrote an
# excluded lap as a row in the first place). Naming this is the point of the table above: the
# standard covers four of pacer's five quality conditions, and forcing the fifth would have made
# the vocabulary less true, not more standard.
#
# ★ (session best) and ▲ (worst loss) are NOT quality markers and are deliberately absent here —
# #237 separated the priority glyph from the trust glyph precisely so the two vocabularies could
# not be read as one.
# ── THE THIRD FAMILY: A FRAME OF VIDEO, WHICH TAKES NO CODES ─────────────────────────────────
# The burned-in overlay export is the most public artifact pacer produces and the least able to
# caveat itself: it lands in a group chat and on a feed, it is watched by people who have never
# seen the app, and unlike a CSV or a report it has NO KEY, no hover, and no margin to put one in.
# Every argument for adopting the standard above runs the other way here. `[p]` in the corner of a
# video is a letter in a box that the viewer cannot decode and the file cannot explain — strictly
# worse than the plain word, which decodes itself and costs the same ink. So: NO CODES ON A FRAME.
# The four letters stay where a key can follow them.
#
# WHAT DOES GO ON THE FRAME IS THE SHARE CARD'S RULE, because the card is this family's nearest
# sibling — a public, keyless, hover-less image. `share_card.card_data` refuses to render at all on
# PROVISIONAL timing and burns the word "estimated timing" when the clock is degraded. The overlay
# already inherits half of that (app.py warns before a provisional render) and burned NOTHING; the
# warning dialog even named the gap in as many words — "that estimate gets burned into the video,
# with nothing in the frame to say so".
#
# IT STILL DOES NOT BLOCK, and that asymmetry is a decision the app already made and wrote down
# (export_controller.sync_menu): "a provisional clip is still useful to the driver reviewing their
# own footage, an unverified brag card never is". The card's ENTIRE content is a lap time offered
# as an achievement; the clip's content is the driving, with a clock laid over it. Blocking the
# export would withhold a person's own footage over a caveat about one element of the overlay, and
# it would take the overlay-only (alpha) render — which exists to be re-cut in an NLE — with it.
# What the frame must not be is SILENT, which is what this fixes.
#
# PER CONDITION, and only the ones that qualify something the frame actually shows:
#   PROVISIONAL   the clock and the Δ are measured from a line nobody confirmed. On the frame.
#   ESTIMATED     the clock came from the video clock (an older camera, no GPS9). On the frame.
#   GPS LOW       the fixes the speed number, the map dot and the times are made of were rejected
#                 in a concerning share. On the frame.
#   [b] break in series   REFUSED. A break is a fact about a SERIES — times either side of a gap
#                 not being on the same footing — and one clip is one window of one recording, not
#                 a series a viewer can compare across. The exports that ARE series keep it.
#   the per-lap GPS-dropout ⚠  REFUSED, and this is the closer call: it is genuinely a fact about
#                 the exported lap. But it changes per lap, and the SESSION scope renders many laps
#                 in one clip — so it would have to raise and drop a mark mid-clip, which reads as
#                 a rendering fault rather than a disclosure. The stamp is session-scoped, exactly
#                 like `session_marks` and like the pill budget that is resolved once per export.
#                 ⚠ stays where a reader can see which lap it belongs to.
#
# THE WORDS ARE THE APP'S OWN. "ESTIMATED" and "GPS LOW" are literally the lap panel's data-quality
# chip (central_view._refresh_quality_badge), and "provisional" is what the map banner, the export
# dialog and `[p]`'s own meaning already call it — so a driver who has seen the app reads the same
# vocabulary on the clip, and a viewer who has not gets a whole word. These strings are NOT
# ASCII-bound the way `MARK_MEANING` is (nothing here reaches laps.csv); the em dash is the app's
# own clause separator and the export already burns one (`export_video._PENDING_TIME`).
STAMP_PROVISIONAL = "PROVISIONAL — lap timing from a start/finish line that was never confirmed"
STAMP_ESTIMATED = "ESTIMATED — lap timing from the video clock, not GPS"
STAMP_LOW_GPS = "GPS LOW — {pct}% of fixes rejected; speed and position less accurate"


def burned_timing_stamp(session) -> list[str]:
    """The honesty lines a burned-in overlay carries, worst-footing first; [] for a clean session.

    LINES, not one string, because the frame's constraint is WIDTH and a video is re-cropped by
    every feed it passes through. Stacked under the lap strip each line stays inside the top-left
    corner the composition already owns; joined into one they would run across the picture and be
    the first thing a 9:16 crop cut in half.

    Session-scoped and duck-typed like `session_marks`, whose conditions these are minus the break
    in series (see the block above for why a clip is not a series)."""
    quality = getattr(session, "timing_quality", None)
    out: list[str] = []
    if not getattr(session, "timing_verified", True):
        out.append(STAMP_PROVISIONAL)
    if quality is not None:
        if getattr(quality, "media_clock", False):
            out.append(STAMP_ESTIMATED)
        if getattr(quality, "low_gps_quality", False):
            out.append(STAMP_LOW_GPS.format(pct=quality.dropped_pct()))
    return out


MARK_ESTIMATED = "[e]"
MARK_PROVISIONAL = "[p]"
MARK_LOW_RELIABILITY = "[u]"
MARK_BREAK_IN_SERIES = "[b]"

#: Canonical order, worst-footing first, so a row's codes read the same way every time.
MARK_ORDER = (MARK_PROVISIONAL, MARK_ESTIMATED, MARK_BREAK_IN_SERIES, MARK_LOW_RELIABILITY)

#: What each adopted code means, in pacer's terms rather than the standard's generic gloss. This
#: is what `mark_key` renders into the export's own legend, so a file that carries a code always
#: carries its decode.
#:
#: PURE ASCII, AND THAT IS A CONSTRAINT RATHER THAN A STYLE. These strings reach laps.csv, which
#: has been pure ASCII for its whole life and is pinned that way by
#: tests/test_export_disclosures.py::test_csv_trailer_stays_ascii — the same reason the ideal's
#: trailer row carries `sentence()` and not the middle-dotted `caption()`. So the clause separator
#: is a colon, not the em-dash the app's prose uses. ONE string for both exports rather than a
#: typographic twin that can drift from it.
MARK_MEANING = {
    MARK_PROVISIONAL: ("provisional: the start/finish line was auto-fitted and not confirmed, so "
                       "this time is measured from an arbitrary point and will change if it moves"),
    MARK_ESTIMATED: ("estimated: timing came from the video clock (an older camera with no GPS9), "
                     "which runs slightly fast"),
    MARK_BREAK_IN_SERIES: ("break in series: the recording is not continuous, so times either "
                           "side of the break are not on the same footing"),
    MARK_LOW_RELIABILITY: ("low reliability: a GPS dropout inside this lap, or a recording whose "
                           "quality gate rejected a concerning share of fixes"),
}


def break_in_series(session) -> str | None:
    """WHY this recording is not one continuous series, or None when it is — the [b] condition.

    Pacer has two genuine instances, both already detected and neither previously NAMED as a break:

      * a SKIPPED chapter (`Session.skipped_chapters`) — a file in the middle of a recording that
        is not readable video is left out and the rest is analysed. The chapter map is then built
        from the chapters that loaded, with cumulative offsets, so the global time axis closes
        over the hole: laps after it are not continuous with laps before it.
      * a DESYNCED chapter (`ChapterMap.desynced_chapters`) — a non-last chapter whose GPMF track
        does not match its video track by more than a payload. The module that detects it says it
        plainly: "everything after it in the recording is telemetry the picture no longer matches."

    A plain chapter SEAM is deliberately NOT one, and that is a measured refusal rather than an
    omission. load.py's own measurement: a seam does not break a GPS9 run (its delta is an ordinary
    0.100 s) and the axis steps by −0.000127 s across one. Marking every chaptered recording [b]
    would fire on both D24 recordings, on every multi-chapter session anyone records, and would
    mean nothing — the exact failure the [x]/[z] rows above are rejected for.

    Duck-typed and getattr-guarded throughout, like every other export-facing read: a Session
    double that models neither field is a recording with no break, never a crash. The sentence is
    ASCII for the same reason `MARK_MEANING` is: it reaches laps.csv."""
    skipped = list(getattr(session, "skipped_chapters", None) or [])
    chapter_map = getattr(session, "chapters", None)
    ask = getattr(chapter_map, "desynced_chapters", None)
    try:
        desynced = list(ask()) if callable(ask) else []
    except Exception:  # noqa: BLE001 — a stand-in chapter object must never fail an export
        desynced = []
    bits = []
    if skipped:
        n = len(skipped)
        bits.append(f"{n} chapter{'' if n == 1 else 's'} could not be read and "
                    f"{'was' if n == 1 else 'were'} left out, so the recording closes over the gap")
    if desynced:
        n = len(desynced)
        bits.append(f"{n} chapter{'' if n == 1 else 's'} carr{'ies' if n == 1 else 'y'} more or "
                    "less telemetry than video, so the timing after it stops matching the picture")
    return "; ".join(bits) if bits else None


def session_marks(session) -> list[str]:
    """The quality codes that apply to EVERY row of an export of this session, in `MARK_ORDER`.

    The split between this and `lap_marks` is the split the app already makes: `timing_verified`
    and `timing_quality` are one verdict for the whole recording, a GPS dropout is a fact about
    one lap. Keeping them apart is what lets an export state a session-wide caveat once, in its
    key, and still mark the individual laps that carry something the others do not."""
    out = []
    if not getattr(session, "timing_verified", True):
        out.append(MARK_PROVISIONAL)
    quality = getattr(session, "timing_quality", None)
    if quality is not None:
        # The [e] / [u] split is the app's own, not a new one: TimingQuality.detail() already
        # reserves the "estimated"/"video clock" wording for the media-clock fallback and gives a
        # true-clock recording with rejected fixes the low-reliability wording instead.
        if getattr(quality, "media_clock", False):
            out.append(MARK_ESTIMATED)
        if getattr(quality, "low_gps_quality", False):
            out.append(MARK_LOW_RELIABILITY)
    if break_in_series(session):
        out.append(MARK_BREAK_IN_SERIES)
    return [m for m in MARK_ORDER if m in out]


def lap_marks(session, lap_id, dropout_ids=None) -> list[str]:
    """Every quality code that applies to ONE lap row — the session-wide ones plus this lap's own.

    `dropout_ids` lets a writer looping over hundreds of rows fetch `dropout_lap_ids()` once;
    omitted, it is read per call. Returns [] for a lap with nothing to disclose, so the common
    case writes an empty cell exactly as the `flag` column already does."""
    ids = dropout_ids
    if ids is None:
        ask = getattr(session, "dropout_lap_ids", None)
        ids = ask() if callable(ask) else ()
    out = list(session_marks(session))
    if lap_id in ids and MARK_LOW_RELIABILITY not in out:
        out.append(MARK_LOW_RELIABILITY)
    return [m for m in MARK_ORDER if m in out]


def mark_key(codes) -> list[tuple[str, str]]:
    """`(code, meaning)` for the codes given, in `MARK_ORDER` — an export's KEY.

    A code with no key is the failure mode the standard exists to prevent, so every writer that
    can emit a code renders this beside the table it emitted them into. Returns only the codes
    actually present: a legend listing four marks on a file that carries none teaches the reader
    that the marks are decoration."""
    present = set(codes)
    return [(m, MARK_MEANING[m]) for m in MARK_ORDER if m in present]
