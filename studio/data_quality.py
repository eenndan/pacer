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
"""
from __future__ import annotations

from dataclasses import dataclass

# Timing-clock provenance — which per-sample time axis the load path actually built.
GPS9_TRUECLOCK = "gps9_trueclock"        # GPS9 per-sample fix spacing (the validated headline path)
MEDIA_CLOCK_FALLBACK = "media_clock_fallback"  # naive media clock (older GPS5 camera, no GPS9)

# A dropped-fix fraction at/above this reads as "GPS quality low" in the UI (a few rejected fixes
# on an otherwise clean trace is normal and not worth a banner). 8% ≈ a fix every ~12 s on a 10 Hz
# stream rejected — conservative, so the badge only fires on a genuinely degraded recording.
DROPPED_FIX_CONCERN_FRAC = 0.08


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
