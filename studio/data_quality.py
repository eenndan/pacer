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

    def concerns(self) -> list[str]:
        """Human-readable concern lines (most-significant first), one per active issue — the
        text the data-quality banner stacks. Empty when the timing is fully high-quality."""
        out: list[str] = []
        if self.media_clock:
            out.append(
                "Timing estimated from the video clock (older GoPro without GPS9) — "
                "lap times may drift ~0.1%.")
        if self.low_gps_quality:
            pct = round(self.dropped_fraction * 100)
            out.append(
                f"GPS quality low — {pct}% of fixes were rejected; times may be less accurate.")
        return out
