"""PlaybackState: the single owner of the per-frame playback / scrub / auto-follow cursor.

Before this object the "what time/lap is currently shown" state had no home — it was smeared
across StudioWindow (`_latest_t`, `_applied_t`, `_followed_lap`) and reached into the two injected
controllers through a fragile callback web (CompareController got a `set_followed_lap` lambda;
ScrubController got a `get_applied_t`/`set_applied_t` lambda pair). Reasoning about the cursor meant
reading three files plus the tick gating. This is a plain value object that holds those three
shared fields in ONE place; StudioWindow constructs ONE instance and hands the SAME reference to
both controllers, so a controller's write is visible to the window (and vice-versa) without any
getters/setters in between — the callback trio is replaced by shared state.

It is deliberately a dumb container: no behaviour, no Qt, no `pacer`. WHEN each field is read or
written is unchanged by this extraction (the tick advance, the `latest_t != applied_t` gate, the
auto-follow edge in `_follow_current_lap`, the scrub release seeding `applied_t`, compare
freezing/clearing `followed_lap`); only WHERE the fields live moved — off StudioWindow / the
controllers' own attributes and onto this shared object.

Fields:
  * `latest_t`   — the most recent media time `positionChanged` reported (recorded on the hot video
    path; cheap). `0.0` until the first frame.
  * `applied_t`  — the media time the map/plot/readout were last driven for. The ~30 Hz tick advances
    it to `latest_t` ONLY when they differ (the gate that throttles the heavy view refresh and breaks
    the drag↔positionChanged feedback loop). `None` until the first apply / poster-seek.
  * `followed_lap` — the lap the speed/Δ charts are currently auto-following (current-vs-best). The
    O(1) per-tick edge check in `_follow_current_lap` only re-points the charts when the playhead's
    lap differs from this. Compare mode FREEZES it on the primary lap (suspending auto-follow) and
    CLEARS it on exit; a lap-select / poster / corner seek seeds it so the immediate post-seek tick
    isn't mistaken for a lap-change edge. `None` = "nothing followed yet" (launch / post re-segment).

The scrub controller's own coalescing fields (`_scrub_target` et al.) stay on ScrubController: they
are drag-private working state the window never reads, not part of the shared cursor.
"""

from __future__ import annotations


class PlaybackState:
    """The shared playback / scrub / auto-follow cursor (see the module docstring). A plain mutable
    container — StudioWindow constructs one and passes the SAME instance to both controllers."""

    __slots__ = ("latest_t", "applied_t", "followed_lap")

    def __init__(
        self,
        latest_t: float = 0.0,
        applied_t: float | None = None,
        followed_lap: int | None = None,
    ):
        self.latest_t = latest_t            # most recent positionChanged time (hot path; 0.0 to start)
        self.applied_t = applied_t          # time the views were last driven for (None until first apply)
        self.followed_lap = followed_lap    # lap the charts auto-follow (None = nothing followed yet)
