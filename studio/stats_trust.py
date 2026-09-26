"""DATA TRUST, the Stats page's second section: what every number on the page is worth.

One labelled FACT per row: the trust-breaking caveats first (an unconfirmed start line, an unknown
track, laps left out of every statistic, in-lap GPS dropouts, a break in the series), then the
provenance — the timing clock, the video sync, GPS quality over time, the g source, and the
IMU↔GPS and rotation cross-checks. The lap panel's data-quality chip lands on the `TIMING_TERM` row
(`StatsView.reveal_trust`). A section of `stats_panel.StatsView`: build, refresh and copy here; the
page places it (see `stats_common`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from . import data_quality, media_clock, theme
from ._signal import exclusion_summary, fmt_signed, plural
from .lap_table import DROPOUT_MARK, EXCLUDED_MARK, too_brief_count, too_brief_note
from .stats_common import NO_GMETER_NOTE, section_heading
from .widgets import DASH, WrapLabel

#: The DATA TRUST term for the picture↔telemetry fact. Named once so the row, its test and the
#: docs cannot drift into three spellings of one thing.
VIDEO_SYNC_TERM = "Video sync"

#: The DATA TRUST term for the timing-quality fact — the row the lap panel's data-quality chip
#: (ESTIMATED / GPS LOW / NO GPS) opens. Named once so the row, the chip's destination and their
#: test cannot disagree about which row that is.
TIMING_TERM = "Timing"

#: WHAT NO CORRECTION REMOVES, stated once beside the row that states what the corrections did.
#: `studio/media_clock.py` measures it: a GPMF payload spans 1.001 s and carries 9, 10 or 11 fixes
#: laid evenly across it, so where a fix really sat inside its payload is recorded nowhere. It is a
#: FLOOR — the part that does NOT grow through a recording, which is exactly what distinguishes it
#: from the two clock errors the app now takes out.
VIDEO_SYNC_TIP = (
    "Where a GPS fix sits inside its 1.001 s GPMF payload is recorded nowhere, so no method can "
    "place a telemetry instant on the picture better than about ±0.05 s — 1.5 frames at 30 fps. "
    "That floor sits under whatever this row says, and unlike a clock difference it does not grow "
    "through a recording. Lap times are differences taken on one clock, so none of this moves "
    "them.")


def video_sync_row(session):
    """Is what is drawn over a frame that frame's own? — as a (term, value, caveat) row, or None.

    THE FACT THE CARD COULD NOT STATE. The app crosses ONE seam between the picture and the
    telemetry (`Session.media_time`), and two corrections ride on it: the two clocks' ~27 ppm rate
    difference, and the GPS timestamps' own measured lag. The second one is a PER-RECORDING
    VERDICT — `Session.gps_lag_applied_s` is None for a camera with no gyro, for a gyro that never
    tracks the racing line, and for a measurement past `media_clock.MAX_GPS_LAG_S` — and until this
    row the only place it was stated was the ROTATION row's tooltip, which exists only when there
    is a gyro to measure an offset with. So the disclosure was absent on all ten bundled samples,
    and absent on precisely the recordings where the correction had FAILED.

    Measured on the real StudioWindow with `rotation.measure_lag` forced to its own refusing branch
    over D24's 0060 pair (the #283 idiom — the real gate driven to the branch the owner's files
    never reach): `gps_lag_applied_s` None, every GPS-derived overlay ~0.46 s — 14 frames at
    30 fps — behind the picture, and the card read `Timing: GPS9 true clock · 0% of moving fixes
    rejected` with the rotation row silently dropping its clause. Nothing on the window said so.

    FOUR STATES, and all four occur. Both D24 recordings are the first (fitted map, +26.73 /
    +27.11 ppm, lag +0.4764 / +0.4589 s installed). Eight of the ten bundled samples are the third
    — a GPS5 camera never leaves the media clock, so `media_clock.fit` returns IDENTITY because
    there is genuinely nothing to convert, which must not read as a failed fit. `karma.mp4` is the
    fifth: no GPS trace at all, so there is nothing to place on the picture and the Timing row
    above already says nothing here can be lap-timed — no row rather than a fifth sentence.

    ACCESSORS ONLY, and it returns None for a session that models no clock at all. Every other
    suite in this repo builds a duck-typed stand-in; "this object knows nothing about a map" is not
    "this recording's map could not be fitted", and reporting the first as the second is the
    failure mode `track_name`'s `""` default already exists to avoid."""
    clock = getattr(session, "media_clock", None)
    quality = getattr(session, "timing_quality", None)
    if not isinstance(clock, media_clock.MediaClock) or quality is None:
        return None
    if getattr(quality, "no_gps", False):
        return None
    applied = getattr(session, "gps_lag_applied_s", None)
    # The RATE FIT alone — `gps_lag` is a separate installation and would otherwise make every
    # corrected recording look like a fitted one even where the fit was refused.
    fitted = not clock.without_gps_lag().is_identity
    ppm = abs(clock.rate - 1.0) * 1e6
    # The recording's own length, off the object the clock rides on. Absent (a stand-in, a session
    # with no chapter map) means the seconds figure is omitted, never invented.
    span = getattr(getattr(session, "chapters", None), "total_duration", None)
    drift = (f", {ppm * 1e-6 * float(span):.2f} s across this recording"
             if isinstance(span, (int, float)) and not isinstance(span, bool) and span > 0
             else "")
    if applied:
        # Copy #5 (QA 2026-09-26): said plainly, with the clock drift in the same clause — the
        # speed, Δ and map dot beside a frame are that frame's own, in the app and in exports.
        span = f" ({drift.removeprefix(', ')})" if drift else ""
        tail = (f" and its clock drifted {ppm:.1f} ppm{span}; both are removed" if fitted
                else "; that is removed")
        return (VIDEO_SYNC_TERM,
                f"corrected — telemetry is lined up to the frame, in the app and in exports: the "
                f"GPS ran {abs(applied):.2f} s late{tail}", False)
    if fitted:
        # The forced case above, and the honest half-correction: the drift is out, the lag is not.
        # "around half a second" is what it measured wherever it COULD be measured (+0.476 /
        # +0.459 s on the two D24 recordings) — this recording's own is unknown, which is the
        # whole point of the row, so the figure is offered as a scale and not as this file's.
        return (VIDEO_SYNC_TERM,
                f"the two clocks' {ppm:.1f} ppm drift is taken out{drift}, but this recording's "
                f"GPS-timestamp lag could not be measured — so the speed, Δ and map dot drawn "
                f"over the video may trail the picture by the receiver's own fix latency, which "
                f"measures around half a second on the recordings where it can be measured. Lap "
                f"times are differences taken on one clock and are unaffected.", True)
    if getattr(quality, "media_clock", False):
        return (VIDEO_SYNC_TERM,
                "this camera writes no GPS clock of its own, so the telemetry and the picture are "
                "already on one clock — there is nothing to convert, and nothing is shifted.",
                False)
    return (VIDEO_SYNC_TERM,
            "the picture↔telemetry map could not be fitted on this recording, so everything drawn "
            "over the video keeps the uncorrected mapping — which drifts from the picture by up "
            "to about 0.2 s by the end of a long session. Lap times are differences taken on one "
            "clock and are unaffected.", True)


def _set_highlight(label: QLabel, value: str) -> None:
    """Set a label's `highlight` property and re-polish it, only when it actually changes — a
    property in a QSS selector is re-read on a polish alone (see widgets.set_tone), and this runs
    for every row on every refresh."""
    if (label.property("highlight") or "") == value:
        return
    label.setProperty("highlight", value)
    label.style().unpolish(label)
    label.style().polish(label)


class _TrustCard(QWidget):
    """DATA TRUST as a list of FACTS — one labelled row each — instead of a paragraph.

    WHAT IT REPLACES, and why the shape had to change. The card shipped as a single word-wrapping
    QLabel holding up to seven `·`-separated sentences joined by newlines. Three things were wrong
    with that, and only one of them was the clipping:

      * it CLIPPED. The label wrapped at the scroll BODY's width — which the content-sized report
        tables had pushed to 742 px inside a 503 px quadrant — so the longest line ran 61 px past
        the right edge of the viewport and stopped mid-number ("…longitudinal r=+0.82 · 3468").
        Measured at 1280x800 it was 119 px. Nothing about the label was wrong; it was being asked
        to lay out at a width nobody could see.
      * it read as PROSE in a page made of tiles. Every other group on this page is a value with a
        name under it; the densest, most technical block on the surface was the one thing with no
        structure at all, and the `·` separators made a fact list look like a sentence.
      * it could not be SCANNED. "Is the timing verified? what is the g source?" are lookups, and a
        lookup wants a column of terms, not three lines of running text.

    So each fact is a ROW: a dim CAPTION term on the left, its value on the right. That is the
    tile's own type pair — the dim name and the value it names — turned through ninety degrees,
    which is what makes it survive a ~500 px quadrant where a tile grid of seven captions would not.
    The value WRAPS (WrapLabel, so the layout is actually told the height it needs), so no fact can
    ever be cut again however long it gets.

    THE CAVEATS LEAD. The trust-BREAKING facts — an unconfirmed start line, an unknown track, laps
    left out of every statistic, in-lap GPS dropouts — appear only when they apply, and appear
    FIRST, marked `⚠`, so the card cannot read the same on a session where three of them are wrong
    and one where none are. That ordering was already the shipped behaviour; a row of its own and a
    marked term is what makes it visible at a glance rather than on a careful read.

    WHY THE CAVEATS ARE NOT PAINTED AMBER. The app's amber call-to-action treatment
    (`#ProvisionalBanner`) is already on this page, as its own strip, ~100 px above this card and
    stating the first of these caveats in the same words. A second amber block for the same fact is
    noise rather than emphasis, and the other three caveats have no single action to offer. So the
    alarm here is carried by MARKING and by ORDER; the amber is spent once, where the action is.

    `text()` is the whole card as one string, and it is not a test affordance: a composite widget
    announces as nothing to assistive tech, so it is also the card's accessible description.
    Deliberately `f"{term}: {value}"` per row — the same sentences the paragraph printed, so this
    change is provably presentational."""

    #: What marks a caveat row's term. The glyph the Laps tab already uses for a flagged lap.
    CAVEAT_MARK = "⚠ "

    def __init__(self, parent=None):
        super().__init__(parent)
        # The one name a screen reader gives the card when the data-quality chip moves focus here.
        self.setAccessibleName("DATA TRUST")
        self._rows: list[tuple[str, str, bool]] = []
        # The term of the row a reader was SENT to (see `set_highlight`); None when nobody was.
        self._highlight: str | None = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.SPACE_M)
        self._grid.setVerticalSpacing(theme.SPACE_XS)
        # The TERM column takes exactly what its longest term needs; the VALUE column takes
        # everything else and wraps inside it. A stretch on the value column (and none on the term)
        # is what stops a long value from widening the card past its pane — the defect that put the
        # old paragraph 61 px off-screen.
        self._grid.setColumnStretch(0, 0)
        self._grid.setColumnStretch(1, 1)
        self._widgets: list[tuple[QLabel, WrapLabel]] = []

    def rows(self) -> list[tuple[str, str, bool]]:
        """The facts currently shown, as (term, value, is_caveat)."""
        return list(self._rows)

    def text(self) -> str:
        """The card as text: one "term: value" line per fact (also its accessible description)."""
        return "\n".join(f"{term}: {value}" for term, value, _caveat in self._rows)

    def set_rows(self, rows) -> None:
        """Re-render the card from (term, value, is_caveat) triples.

        Widgets are REUSED and only the surplus is hidden, rather than deleted and rebuilt: this
        runs on every refresh() — a unit flip, a palette flip, a re-segmentation — and tearing down
        QLabels inside a live QGridLayout on every one of those is the same re-entrancy the tile
        reflow had to be taught to avoid (see StatsView._place_tiles)."""
        self._rows = [(str(t), str(v), bool(c)) for t, v, c in rows]
        while len(self._widgets) < len(self._rows):
            term = QLabel()
            term.setFont(theme.ui_font(theme.CAPTION))
            term.setProperty("role", "Note")
            # Top-aligned: a one-word term must sit level with the FIRST line of a value that
            # wraps to three, not float in the middle of it.
            term.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            value = WrapLabel()
            value.setProperty("role", "Note")
            value.setFont(theme.ui_font(theme.CAPTION))
            r = len(self._widgets)
            self._grid.addWidget(term, r, 0)
            self._grid.addWidget(value, r, 1)
            self._widgets.append((term, value))
        for i, (term_w, value_w) in enumerate(self._widgets):
            if i >= len(self._rows):
                term_w.setVisible(False)
                value_w.setVisible(False)
                continue
            term, value, caveat = self._rows[i]
            term_w.setText(f"{self.CAVEAT_MARK}{term}" if caveat else term)
            value_w.setText(value)
            term_w.setVisible(True)
            value_w.setVisible(True)
        self._apply_highlight()
        self.setAccessibleDescription(self.text())

    # ------------------------------------------------------------ the row a reader was sent to
    def row_widgets(self, term: str):
        """The (term label, value label) pair currently showing `term`'s fact; None if no row does."""
        for i, (t, _v, _c) in enumerate(self._rows):
            if t == term:
                return self._widgets[i]
        return None

    def highlighted(self) -> str | None:
        """The term of the row marked by `set_highlight`, if that row is on the card right now."""
        return self._highlight if self.row_widgets(self._highlight or "") is not None else None

    def set_highlight(self, term: str | None) -> None:
        """Mark `term`'s row as the one the reader was sent here to read — or clear the mark (None).

        WHY A MARK AT ALL. The lap panel's data-quality chip opens this card, and the card is a list
        of up to ten facts; landing on it without saying which one answers "why is that chip lit"
        leaves the reader to work out that "Timing" is the row about an ESTIMATED clock. The mark is
        that answer and nothing more, so the Stats page clears it as soon as it is left.

        TYPE, NOT A BOX. The term takes the amber the chip is drawn in and the value steps up from
        the dim Note ink to the primary text, both through QSS (`[highlight=…]`), so nothing is
        resized and nothing moves: a tinted band would either touch the text at its left edge or
        need padding the other nine rows do not have. Held by TERM rather than by row index, so a
        refresh that reorders the caveats keeps it on the same fact."""
        self._highlight = term
        self._apply_highlight()

    def _apply_highlight(self) -> None:
        for i, (term_w, value_w) in enumerate(self._widgets):
            on = i < len(self._rows) and self._rows[i][0] == self._highlight
            _set_highlight(term_w, "term" if on else "")
            _set_highlight(value_w, "value" if on else "")


class TrustSection:
    """The DATA TRUST heading and its facts card (`heading`, `card`)."""

    def __init__(self):
        self.heading = section_heading("DATA TRUST")
        self.card = _TrustCard()

    def widgets(self) -> tuple:
        return (self.heading, self.card)

    def tables(self) -> tuple:
        return ()

    def refresh(self, session):
        """The DATA TRUST card: what the numbers on this page are worth, one labelled FACT per row.

        The TRUST-BREAKING facts LEAD — an unconfirmed start line, an unknown track, laps left
        out of every statistic, in-lap GPS dropouts. Without them the card printed provenance only,
        and read identically on a session where all three were wrong and one where all three were
        fine. The provenance facts (clock, g source, cross-check) follow.

        EVERY SENTENCE HERE IS THE SHIPPED ONE. Each row is a (term, value) split of a line the
        card already printed — at the line's own colon where it had one, and at its verb where it
        did not ("Statistics use | 21 of the 22 laps found …") — so `_TrustCard.text()` re-joins
        into what the paragraph said. This change is the card's SHAPE, never its claims. Nothing
        was moved into a tooltip, and in particular the lateral GAIN stays on the surface — r is
        scale-invariant, so halving the g channel left the old card byte-identical while every g
        the app shows halved, and the gain is the number that moves."""
        rows: list[tuple[str, str, bool]] = []
        tips: list[str] = []
        valid = session.valid_lap_ids() if hasattr(session, "valid_lap_ids") else []
        # Gated on having laps, like the banner: with none, "every lap time below" refers to
        # nothing, and the empty-state block already makes placing the line the next action.
        if valid and not getattr(session, "timing_verified", True):
            rows.append(("Start/finish line",
                         "auto-fitted, not confirmed — every lap time and split below is "
                         "measured from an arbitrary point. Drag it on the map.", True))
        # "" (not None) as the getattr default: a test double that models no track at all must
        # not be reported as a recording whose track lookup FAILED. And not on a recording with
        # no GPS trace: there was no location to look up, and the Timing row below already says
        # why there is no line — blaming the track database beside it was a second, wrong cause.
        if (getattr(session, "track_name", "") is None
                and not data_quality.no_start_line(session)):
            rows.append(("Track",
                         "unknown — not in the track database, so the start/finish line "
                         "could not be placed for you.", True))
        excluded = getattr(session, "excluded_lap_ids", list)() or []
        if excluded:
            # "LAPS FOUND" = THE LAPS, valid + excluded, and the crossings too brief to be a lap
            # are named as crossings (LOOK-6, QA 2026-09-26). The denominator used to be
            # `lap_count()`, which counts those slivers too: "19 of the 22 laps found — 2
            # excluded" on MK_18_09, where 19 + 2 = 21 and the 22nd was the crossing the Laps tab
            # says was "too brief to count as a lap". The Laps tab's strip counts the same way
            # (lap_table.too_brief_count), so the two pages still state one fact with one
            # denominator — the reason this once moved to `lap_count()`.
            total = len(valid) + len(excluded)
            brief = too_brief_count(session, total)
            # WHY, per reason — it used to say "their distance off the session median" for every
            # excluded lap, which was already false for a lap with a stop and is false again for a
            # piece that does not end where it started. getattr-guarded for the lighter doubles.
            why = exclusion_summary(getattr(session, "excluded_lap_reasons", dict)() or {})
            rows.append(("Statistics use",
                         f"{len(valid)} of the {plural(total, 'lap')} found — "
                         f"{len(excluded)} {EXCLUDED_MARK} excluded"
                         + (f": {why}" if why else "") + " (see the Laps tab)."
                         + (f" {too_brief_note(brief)}" if brief else ""), True))
        # In-lap GPS dropouts: the ⚠ rule made visible — the count AND what it means for the
        # statistics on this page (those laps feed no best/σ/pace number). It moved UP here, with
        # the other three caveats: it is one, and it was the only one printed among the provenance.
        dropouts = session.dropout_lap_ids() if hasattr(session, "dropout_lap_ids") else set()
        if dropouts:
            rows.append(("GPS dropout",
                         f"inside {len(dropouts)} of {plural(len(valid), 'lap')} — "
                         f"flagged {DROPOUT_MARK} and left out of bests, σ and pace", True))
        # BREAK IN SERIES — the fourth trust-breaking fact, and the one the card had no name for.
        # A skipped chapter or a chapter whose telemetry stops covering its video means the times
        # either side are not on the same footing; both were already detected and both were only
        # ever mentioned in the transient load notice, which is gone by the time anyone reads this
        # page. Stated here in the card's own prose voice, NOT as the exported `[b]` code: a code
        # needs a key and this card is a list of sentences (studio/data_quality.py's vocabulary
        # note says why the letters stop at the app's edge).
        broke = data_quality.break_in_series(session)
        if broke:
            rows.append(("Break in series",
                         f"{broke} — compare times across it with that in mind", True))
        quality = getattr(session, "timing_quality", None)  # a Session @property
        if quality is not None and getattr(quality, "no_gps", False):
            # A THIRD clock state, and the row below could not say it: its label was a two-way
            # choice — the media-clock fallback, else "GPS9 true clock" — so a verdict that was
            # NEITHER fell through to the flattering branch. Measured on the bundled `karma.mp4`
            # (0 GPS fixes) this card printed "GPS9 true clock · 0% of moving fixes rejected",
            # vouching for the app's best timing on a file with no satellite fix in it, and
            # reporting a reassuring 0 % over a population of nothing. It is a CAVEAT, so it
            # sorts up with the other trust-breaking facts, and it carries the action: the
            # cause is a camera setting or a camera without a receiver, and the strip row
            # beside it says which of the two this recording was.
            rows.append((TIMING_TERM,
                         "no GPS fixes survived in this recording — nothing here can be "
                         "lap-timed, and no time axis was built from satellite fixes. Check "
                         "that the camera's GPS was switched on; some models carry no "
                         "receiver at all.", True))
            tips.append(quality.detail())
        elif quality is not None:
            clock = ("video clock (estimated)" if quality.media_clock
                     else "GPS9 true clock")
            # "of MOVING fixes" is not padding: the fraction is judged over the RETAINED MOVING
            # trace, deliberately (load.py:266-272 — the raw count includes the stationary
            # GPS-acquisition lead-in the pipeline trims, which flagged clean footage as
            # degraded purely on how many chapters were opened). Naming the population is the
            # fix; the number itself is the shipped one.
            #
            # ON A DEGRADED CLOCK THE ROW SAYS WHAT IT COSTS, AND IS A CAVEAT. It is the row the
            # lap panel's amber ESTIMATED / GPS LOW chip opens, and it used to read exactly like a
            # clean recording's — "video clock (estimated) · 0% of moving fixes rejected" names the
            # clock and never says what estimated means; "GPS9 true clock · 12% of moving fixes
            # rejected" is the clean row with a bigger number in it. A reader sent here to find out
            # why the chip was lit found nothing that said so, and an amber chip landing on an
            # unmarked row is two surfaces disagreeing about one fact. The clause is
            # TimingQuality's own (`cost`), so it says what the banner and the chip's hover say.
            value = f"{clock} · {quality.dropped_pct()}% of moving fixes rejected"
            cost = quality.cost()
            rows.append((TIMING_TERM, f"{value} — {cost}" if cost else value,
                         bool(quality.degraded)))
            tips.append("The rejected-fix share is measured over the fixes taken WHILE MOVING. "
                        "The stationary lead-in before you drive off is trimmed by the loader "
                        "and left out of the verdict, so opening one chapter or all of them "
                        "gives the same answer.")
            if quality.degraded:
                tips.append(quality.detail())
        # …and what that clock is worth AGAINST THE PICTURE, which is the other half of the same
        # question and the half a viewer can check for themselves. The row above names the axis the
        # times are measured on; this one says whether the numbers painted beside a frame belong to
        # that frame. It sits here because it qualifies the row above, and it is a CAVEAT when the
        # correction did not land — see `video_sync_row` for the state that made it necessary.
        sync = video_sync_row(session)
        if sync is not None:
            rows.append(sync)
            tips.append(VIDEO_SYNC_TIP)
        # …and the SAME fact per second, which is a different verdict often enough to be worth its
        # own row. Measured on the owner's two recordings, the two rows come out INVERTED: 0060
        # rejects not one fix (the row above reads 0 %) and yet 17 of its 38 clean laps contain a
        # second whose DOP left the GNSS good band, while 0062 rejects 1 % and every one of those
        # rejections is in the 48 seconds before the kart moves, so not a single lap inherits
        # anything but good. A percentage cannot say that; a bar can, and this row says which laps
        # it is about and points at the bar for where.
        strip = getattr(session, "quality_timeline", None)
        if strip is not None and len(strip):
            # One pass over the laps, not two: `lap_quality` resolves a lap window and folds its
            # cells, and this runs on every refresh (a unit flip, a palette flip, a re-segment).
            # UNREPORTED sorts ABOVE good on purpose, so an ungraded recording reports no degraded
            # lap rather than every lap — "not measured" is not a finding.
            lap_cls = [q for lid in valid if (q := session.lap_quality(lid)) is not None]
            degraded = [q for q in lap_cls if q < data_quality.GOOD]
            holed = [q for q in degraded if q <= data_quality.POOR]
            # The noun agrees with the laps found, the verb with the degraded ones (K2).
            note = (f" · {len(degraded)} of {plural(len(valid), 'lap')} "
                    f"contain{'s' if len(degraded) == 1 else ''} a second below good"
                    if degraded and valid else "")
            rows.append(("GPS quality over time",
                         f"{strip.summary()}{note} — the bar under the scrubber shows where",
                         bool(holed)))
            tips.append("The strip under the scrub bar grades every second of the recording, and "
                        "a lap inherits the WORST second inside it. That is the distinction this "
                        "page's percentage cannot draw: a receiver acquiring a lock before you "
                        "drive off and a receiver failing mid-session are the same percentage and "
                        "completely different recordings.")
        # A REFUSED accelerometer (`gmeter.axis_check`) is stated in the row that states the g
        # source, because that is the row it changes. Without it a refused IMU read exactly like a
        # camera that never had a usable one — GPS on both axes, unmarked — and a refused IMU is
        # never cross-checked, so the DISAGREE row below cannot say it either. Measured on the real
        # window over hero8.mp4 and over a D24 recording forced through the real gate: the refusal
        # was on stdout and nowhere else. The reason is AxisCheck's own clause, the same one the
        # g-meter toggle's tooltip finishes its sentence with.
        axis = session.gmeter_axis() if hasattr(session, "gmeter_axis") else None
        refusal = axis.refusal() if axis is not None else None
        if refusal:
            tips.append(axis.summary())
        if getattr(session, "has_gmeter", False):
            src = {"accl": "IMU", "gps": "GPS"}
            lat_src = src.get(session.gmeter_source(), session.gmeter_source())
            long_src = src.get(session.gmeter_long_source(), session.gmeter_long_source())
            value = f"{lat_src} lateral · {long_src}-derived longitudinal"
            if refusal:
                value += f" — the accelerometer was not used: {refusal}"
            rows.append(("g-meter", value, bool(refusal)))
        else:
            # The card used to go SILENT about the g channel exactly when it is missing — while
            # the peak-g tiles, the per-lap g columns and the corner Grip (est) all render em-dashes
            # with no stated reason anywhere on the window. Split on NO_GMETER_NOTE's own "term:
            # value" colon so the constant stays the single source of that sentence.
            term, _, value = NO_GMETER_NOTE.partition(": ")
            if refusal:
                # ...except that a refused IMU with no GPS trace to fall back on DID have an
                # accelerometer, so "no accelerometer in this recording" would be the wrong reason.
                value = (f"the accelerometer was not used: {refusal}, and there is no GPS "
                         "trajectory to derive g from — lateral g, braking g and grip are "
                         "unavailable.")
            rows.append((term, value, True))
        cross = session.gmeter_cross() if hasattr(session, "gmeter_cross") else None
        if cross is not None:
            verdict = "agree" if cross.ok else "DISAGREE"
            gain = getattr(cross, "lat_gain", None)
            gain_bit = f" · lateral gain ×{gain:.2f}" if gain is not None else ""
            # Grouped: the cross-check's sample count is the only six-figure number the app
            # prints, and "346713" is read digit by digit where "346,713" is read at a glance.
            measured = (f"lateral r={fmt_signed(cross.lat_corr, 2)}{gain_bit} · longitudinal "
                        f"r={fmt_signed(cross.long_corr, 2)} · {cross.n:,} samples")
            # Copy #6 (QA 2026-09-26): the verdict in words, the figures on the hover — except
            # when the two DISAGREE, where the figures are the diagnosis and stay on the face.
            weighed = gain is not None and getattr(cross, "gain_measurable", True)
            if not cross.ok:
                value = f"{verdict} · {measured}"
            elif weighed:
                value = (f"{verdict} · the g-meter's cornering force matches the GPS path's "
                         f"within {max(abs(gain - 1.0) * 100.0, 1.0):.0f} %")
            else:
                value = (f"{verdict} · the g-meter's cornering follows the GPS path's (its scale "
                         f"could not be weighed: too little cornering)")
            rows.append(("IMU↔GPS cross-check", value, not cross.ok))
            tips.append(f"IMU↔GPS, measured: {measured}.")
            tips.append(cross.summary())
            tips.append("Lateral gain is the IMU's lateral magnitude over the GPS-derived one: "
                        "×1 means the g you read is scaled right. The correlation beside it "
                        "cannot tell you that — Pearson r is unchanged by a scale error, so a "
                        "channel reading half would still correlate perfectly.")
        # The THIRD cross-check row, and the strongest of the three, because it is the only one on
        # this card whose target is EXACT. The two above compare one estimate against another, so
        # their r and gain describe agreement and nothing more; a lap is a closed loop, so the yaw
        # integrated over one is 2π whatever the racing line and whatever the smoothing.
        #
        # The row states what the MEASURED channel reads against that target, and prints the
        # inferred channel's own ratio beside it — both live off `RotationCheck`, so neither can
        # go stale. It does NOT editorialise about the gap between them: that gap was 6.5-10 % of
        # the lap when this channel landed, is ~0.1 % since the curvature basis was fixed, and a
        # sentence characterising it would have been wrong within the week. Two numbers against
        # one exact target say it without a verdict attached.
        rot = session.rotation_cross() if hasattr(session, "rotation_cross") else None
        if rot is not None:
            verdict = "agrees" if rot.ok else "DISAGREES"
            # The CLOCK OFFSET belongs in this row and not in a footnote, because the correlation
            # printed beside it is measured with that offset LEFT IN: the gyroscope is timed on the
            # camera's media clock and the GPS trace on its receiver's, and on the owner's own
            # recordings an event's GPS timestamp lands ~0.46 s after its gyro timestamp. Stating
            # the r without the offset would read as how well the two channels agree, when a good
            # part of the gap between them is just the two clocks. `lag_clause` is RotationCheck's
            # own sentence, so this row and the load-time log say it in the same words — and it is
            # EMPTY when the offset could not be measured, which is why the clause is appended
            # rather than formatted in: a row that has no measurement says nothing instead of 0.00.
            measured = (f"over {plural(rot.loop_n, 'closed lap')} the gyroscope's measured yaw "
                        f"integrates to {rot.loop_ratio_gyro:.3f}×2π and the path-derived rate to "
                        f"{rot.loop_ratio_path:.3f}×2π, against an exact "
                        f"{fmt_signed(rot.loop_exact, 3)} · r={fmt_signed(rot.corner_corr, 2)} "
                        "between them through the corners")
            # Copy #4/#5 (QA 2026-09-26). In words when they agree, the figures on the hover; the
            # figures stay on the face when they DISAGREE, because then they are the diagnosis.
            # The clock clause is not on the face any more: beside the Video sync row's "the GPS
            # ran 0.43 s late … removed" it read as a contradiction ("… measured with that offset
            # left in"). Both are true — this check compares the channels as RECORDED — so the face
            # says that, and the offset with both correlations is on the hover (below).
            if rot.ok:
                exact = abs(rot.loop_exact) or 1.0
                off = max(abs(rot.loop_ratio_gyro - rot.loop_exact),
                          abs(rot.loop_ratio_path - rot.loop_exact)) / exact * 100.0
                raw = " (this check uses the raw GPS clock)" if rot.lag_clause else ""
                value = (f"{verdict} · the gyro and the GPS path each count one full turn per "
                         f"lap, within {max(off, 0.1):.1f} % over "
                         f"{plural(rot.loop_n, 'closed lap')}, and match through the corners{raw}")
            else:
                value = f"{verdict} · {measured}"
            rows.append(("Rotation cross-check", value, not rot.ok))
            tips.append(f"Rotation, measured: {measured}.")
            tips.append("A lap is a closed loop, so the heading change over one is exactly 2π — "
                        "the only quantity on this card with a ground truth rather than a second "
                        "estimate to agree with. That is why the headline here is the closed-lap "
                        "ratio and not the correlation: halving the channel leaves r bit-identical "
                        "and moves this ratio to 0.5. The exact target beside it carries this "
                        "circuit's own direction — a clockwise lap closes at −1.000×2π, which is "
                        "just as exact — and a gyroscope read through the wrong gravity axis lands "
                        "on the opposite side of zero from the path.")
            if rot.lag_clause:
                # WHAT WAS MEASURED AND WHAT WAS DONE WITH IT ARE TWO SENTENCES, from two sources
                # — and since the Video sync row they are two SURFACES, which is the fix for the
                # hole this tooltip had. The clause here is the MEASUREMENT (`RotationCheck`);
                # whether the overlay is corrected by it is the SESSION's answer
                # (`gps_lag_applied_s`), and that answer is true of every recording — including
                # the ones with no gyro, where this tooltip does not exist at all and the
                # correction is exactly as likely to have been refused. So the action is stated
                # once, in the row, and pointed at from here rather than restated.
                tips.append(
                    f"The two channels are not on the same clock. The gyroscope is timestamped on "
                    f"the camera's media clock — the one the picture plays on — and the GPS trace "
                    f"on its receiver's own. Measured on this recording, {rot.lag_clause}: the "
                    f"correlation above is what they score with that offset still in "
                    f"(r={fmt_signed(rot.lag_corr, 2)} at the offset, {fmt_signed(rot.lag_corr_at_zero, 2)} without "
                    f"it). The figures above are not shifted; they describe the two channels as "
                    f"recorded, and what the app did with the offset is the Video sync row above. "
                    f"Lap times are differences taken on one clock, so none of this moves them.")
            tips.append(f"The measured channel is the {session.rotation_device() or 'camera'}'s "
                        f"gyroscope (GPMF GYRO, ~200 Hz), projected onto gravity so it reads a "
                        f"road-plane yaw rate however the camera is tilted on its mount. The "
                        f"path-derived rate is the racing line's own turning, from the GPS trace. "
                        f"They are independent, and they are checked over {rot.n:,} samples.")
        self.card.set_rows(rows or [(DASH, DASH, False)])
        # Set unconditionally (both ways): a stale cross-check summary must not survive a
        # re-render onto a session that has none.
        self.card.setToolTip("\n\n".join(tips))
