"""The surfaces that LEAVE the app must say what the in-app surfaces say (§5.4 + roadmap N13).

The review found the three EXPORTED surfaces exempt from the disclosure rule every in-app one
follows: the share card printed "+4.56 s vs your ideal lap / your best corners and straights" with
no lap count, and the laps.csv trailer printed "Theoretical best,62.869" bare — while the Stats
tile it came from is captioned `theoretical best · 24 laps` and carries a whole sentence saying
what the minimum ran over. The ideal is an ORDER STATISTIC: on the owner's D24 three chapters the
same driving reads 68.016 s over 5 laps and 66.781 s over 65 (corner_model.IdealSample's measured
table). A number that moves with lap count, published without the lap count, invites a comparison
it cannot support — and an exported file has no tooltip to put the caveat in.

THE GOLDEN RULE THIS FILE PINS: the ideal number AND its lap count in the report, the CSV and the
card EQUAL the Session accessors on the same session. Not "look similar" — the assertions compare
the exported TEXT against `session.theoretical_best()` / `session.ideal_sample()` directly, so a
future edit to either side that moves one without the other fails here. The same rule is asserted
for every value `export_data.stats_summary` publishes: each is compared to the accessor the Stats
page reads, because "the report cannot drift from the page" is a claim, and this is its test.

Also here, from the same review section (§7.5, second half): the three writers are ATOMIC. They
were the app's only non-atomic writes, so a writer that raised part-way left a truncated file at
the user's chosen path — which, for an overwrite, meant a good previous export was destroyed by a
failed one.

Bare Sessions via tests/_synthetic (no pacer Laps, no telemetry file); the card's pure layer needs
no QApplication (its Qt render + the sublabel WIDTH are pinned in test_share_card.py).
Run:  python tests/test_export_disclosures.py
"""
import csv
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_export_data import PNG_1PX, make_session, make_stitched_session  # noqa: E402

from studio import export_data, share_card  # noqa: E402
from studio._signal import DASH, fmt_hms, fmt_time  # noqa: E402


def _write_report(session, **kw):
    """Write a report to a temp path and return its text (the writer is the unit under test)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(path, session, source_label="GX0X0060", **kw)
        with open(path, encoding="utf-8") as f:
            return f.read()


def _trailer(session):
    """The laps.csv summary trailer as written, keyed by label."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "laps.csv")
        export_data.write_laps_csv(path, session)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    marker = export_data.SUMMARY_MARKER
    return {r[0].split(": ", 1)[1]: r for r in rows if r and r[0].startswith(f"{marker}: ")}


# --------------------------------------------------------------- the golden rule, per surface
def test_csv_trailer_states_the_ideals_sample():
    """The trailer's theoretical row equals the accessors — value, count and sentence."""
    s = make_stitched_session()
    smp = s.ideal_sample()
    assert smp is not None and smp.laps == 3, smp
    row = _trailer(s)["Theoretical best"]
    # column 0/1 UNCHANGED (the #212-F4 machine contract), 2/3 are the appended disclosure.
    assert row[0] == f"{export_data.SUMMARY_MARKER}: Theoretical best"
    assert row[1] == f"{s.theoretical_best():.3f}", row
    assert row[2] == str(smp.laps), row
    assert row[3] == smp.sentence(), row
    assert str(smp.laps) in row[3] and str(smp.donors) in row[3], row[3]
    # "Best rolling" states no sample, because the app's own tile states none for it either —
    # inventing one here would be the export claiming a disclosure the app does not make.
    rolling = _trailer(s)["Best rolling"]
    assert rolling[2] == "" and rolling[3] == "", rolling
    print(f"test_csv_trailer_states_the_ideals_sample OK ({row[1]} s over {row[2]} laps)")


def test_csv_trailer_stays_ascii():
    """laps.csv is the MACHINE-readable file and has been pure ASCII its whole life; the
    disclosure must not be what breaks that. The caption's separator is a middle dot, so the
    trailer carries the ASCII `sentence()` and the bare `over_laps` integer instead — the two
    HUMAN surfaces (report, clipboard) print the caption verbatim (asserted below)."""
    s = make_stitched_session()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "laps.csv")
        export_data.write_laps_csv(path, s)
        raw = open(path, "rb").read()
    raw.decode("ascii")  # raises UnicodeDecodeError the moment a non-ASCII mark creeps in
    print(f"test_csv_trailer_stays_ascii OK ({len(raw)} bytes, ascii)")


def test_report_prints_the_ideal_with_its_caption_and_sentence():
    """The HTML report's IDEAL LAP group equals the accessors and the Stats tile's own strings.

    (Note for the record: the review's §5.4 says the report "prints Theoretical best bare". On
    `main` it printed the ideal NOWHERE AT ALL — the page was meta + laps table + snapshots. N13
    is what puts it there, and it arrives with the disclosure attached rather than needing one
    retrofitted.)"""
    s = make_stitched_session()
    smp = s.ideal_sample()
    doc = _write_report(s)
    root = ET.fromstring(doc)
    text = "".join(root.itertext())
    assert smp.caption() in text, "report lost the tile's caption"
    assert smp.sentence() in text, "report lost the sample sentence"
    assert fmt_time(s.ideal_total()) in text, "report lost the ideal value"
    # The caption and the value are the SAME ROW — a caption floating beside some other number
    # would satisfy two substring checks and disclose nothing.
    rows = [(tr.findtext("th"), tr.findtext("td")) for tr in root.findall(".//table/tr")]
    assert (smp.caption(), fmt_time(s.ideal_total())) in rows, rows
    print(f"test_report_prints_the_ideal_with_its_caption_and_sentence OK ({smp.caption()})")


def test_card_carries_the_lap_count_next_to_the_gap():
    """The share card's Δ-to-ideal sublabel names the sample, off the same accessor.

    The gap and the count are read under ONE gate in `card_data`, so a card can never show the
    number without the disclosure: withhold the gap (one donor won every segment) and both go."""
    s = make_stitched_session()
    smp = s.ideal_sample()
    data = share_card.card_data(s, unit="kmh")
    assert data.delta_to_ideal_s is not None
    assert data.ideal_laps == smp.laps, (data.ideal_laps, smp.laps)
    sub = share_card.ideal_sublabel(data.ideal_laps)
    assert f"over {smp.laps} laps" in sub, sub
    assert "not a single drivable lap" in sub, sub

    # The degenerate session (one lap wins every segment): no gap, and therefore no count either.
    degenerate = share_card.card_data(make_session(), unit="kmh")
    assert degenerate.delta_to_ideal_s is None and degenerate.ideal_laps is None, degenerate
    print(f"test_card_carries_the_lap_count_next_to_the_gap OK ({sub!r})")


def test_every_exported_ideal_agrees_with_every_other():
    """ONE session, all four surfaces, one number and one count. This is the regression the
    review's own lane ran by scraping; here it is permanent."""
    s = make_stitched_session()
    ideal, laps = s.theoretical_best(), s.ideal_sample().laps

    csv_row = _trailer(s)["Theoretical best"]
    report = _write_report(s)
    clip = export_data.stats_summary_text(s, None)
    card = share_card.card_data(s, unit="kmh")

    assert csv_row[1] == f"{ideal:.3f}"
    assert csv_row[2] == str(laps), csv_row
    assert fmt_time(ideal) in report and f"· {laps} laps" in report
    assert fmt_time(ideal) in clip and f"· {laps} laps" in clip
    assert card.ideal_laps == laps
    # ...and the card's own gap is best − ideal, off the same two accessors.
    gap = float(s.lap_time(s.best_lap_id())) - float(ideal)
    assert abs(card.delta_to_ideal_s - gap) < 1e-9, (card.delta_to_ideal_s, gap)
    print(f"test_every_exported_ideal_agrees_with_every_other OK "
          f"({fmt_time(ideal)} over {laps} laps on 4 surfaces)")


# ------------------------------------------------------------------ the stats summary (N13)
def test_stats_summary_values_equal_the_session_accessors():
    """Every published value is recomputed here from the accessor the Stats page reads. A second
    implementation inside stats_summary would fail this the first time it rounded differently."""
    s = make_stitched_session()
    st = s.stats
    sections = {sec.title: dict(sec.rows) for sec in export_data.stats_summary(s)}

    tot = st.totals()
    session = sections["SESSION"]
    assert session["recorded"] == fmt_hms(tot.duration_s)
    assert session["moving"] == fmt_hms(tot.moving_s)
    assert session["laps"].startswith(f"{len(s.valid_lap_ids())} valid"), session["laps"]

    pace = st.pace()
    p = sections["PACE"]
    assert p["best lap"] == fmt_time(pace.best)
    assert p[f"median lap · {pace.n} clean laps"] == fmt_time(pace.median)
    assert p["best rolling"] == fmt_time(s.best_rolling_lap())
    assert p["race pace · best 3-lap run"] == fmt_time(st.race_pace())
    assert p["σ lap"] == f"{pace.sigma:.2f} s"
    assert p["consistency · σ/median"] == f"{st.pace_cov():.1f} %"
    count, n = st.laps_within_pct(1.0)
    assert p["within 1% of best"] == f"{count} / {n}"

    ideal = sections["IDEAL LAP"]
    assert ideal[s.ideal_sample().caption()] == fmt_time(s.ideal_total())
    print(f"test_stats_summary_values_equal_the_session_accessors OK "
          f"({sum(len(v) for v in sections.values())} values across {len(sections)} groups)")


def test_absent_signals_are_groups_and_dashes_never_zeros():
    """None-not-zero, all the way out to the exported document.

    A group whose SIGNAL is absent vanishes (the page hides DRIVING without a g-meter); an
    individual absent value inside a shown group reads as the em-dash, because there the dash is
    the information. Neither ever becomes a 0."""
    with_g = {sec.title for sec in export_data.stats_summary(make_stitched_session())}
    # make_stitched_session has no g signal (gmeter._empty), so no DRIVING group at all.
    assert "DRIVING" not in with_g, with_g
    assert {"SESSION", "PACE", "IDEAL LAP", "SPEED · G"} <= with_g, with_g
    # ...and the g tiles inside SPEED · G dash rather than printing 0.00 g.
    speed = dict(next(sec for sec in export_data.stats_summary(make_stitched_session())
                      if sec.title == "SPEED · G").rows)
    assert speed["peak lateral g"] == "" and speed["peak braking g"] == "", speed
    # The renderers turn that "" into the app's em-dash, in both of them.
    doc = _write_report(make_stitched_session())
    assert f"<td>{DASH}</td>" in doc, "report printed something other than the em-dash"
    assert "0.00 g" not in doc, "report invented a zero for an absent signal"
    clip = export_data.stats_summary_text(make_stitched_session(), None)
    assert f"peak lateral g {DASH}" in " ".join(clip.split()), clip
    assert "0.00 g" not in clip, clip

    # A session WITH a g signal does publish the group — so the check above is not vacuous.
    assert "DRIVING" in {sec.title for sec in export_data.stats_summary(make_session())}
    print("test_absent_signals_are_groups_and_dashes_never_zeros OK")


def test_degenerate_ideal_is_withheld_from_every_surface_together():
    """One lap winning every segment makes the "ideal" that lap. The CSV already withheld the row;
    the report group, the clipboard block and the card's gap withhold with it — a surface that
    published it alone would print a bare duplicate of the best-lap readout."""
    s = make_session()
    assert s.ideal_donor_lap_id() is not None, "fixture must be the degenerate case"
    assert "Theoretical best" not in _trailer(s)
    assert "IDEAL LAP" not in {sec.title for sec in export_data.stats_summary(s)}
    assert "theoretical best" not in _write_report(s)
    assert "theoretical best" not in export_data.stats_summary_text(s, None)
    assert share_card.card_data(s, unit="kmh").delta_to_ideal_s is None
    print("test_degenerate_ideal_is_withheld_from_every_surface_together OK")


def test_the_clipboard_summary_states_the_timing_too():
    """THE LEAST HOVERABLE SURFACE CARRIES THE LOUDEST CAVEAT (F1).

    The clipboard block lands in a chat as raw text — no page, no tooltip, nothing to hover. On a
    recording whose track is unrecognised (`timing_verified` False, the state any unknown circuit
    loads in) the app DISABLES the share-card action outright and the report prints "PROVISIONAL",
    while this text published a best lap, a median and `theoretical best · N laps` with no
    qualifier anywhere in it. It now prints the SAME `_timing_meta` string the report row does.

    Disclosure, not refusal: the action stays enabled, matching the report's own choice."""
    s = make_stitched_session()
    good = export_data.stats_summary_text(s, None)
    assert "Timing: verified start line" in good, good[:200]

    s.track_name = None
    assert not s.timing_verified
    text = export_data.stats_summary_text(s, None)
    assert "PROVISIONAL" in text and "arbitrary point" in text, text[:400]
    # ...and it says exactly what the report's Timing row says — one string, two renderings.
    assert export_data._timing_meta(s) in text
    assert export_data._timing_meta(s) in _write_report(s)
    # The numbers are still published (disclosure, not refusal).
    assert s.ideal_sample().caption() in text
    print("test_the_clipboard_summary_states_the_timing_too OK")


def test_pace_group_follows_the_pages_gate_not_the_pace_summarys():
    """F3: the page shows PACE whenever there are VALID laps and dashes the tiles inside it; it
    also sets `best rolling` OUTSIDE the pace branch, because a start-anywhere loop is not a pace
    statistic.

    Gating the exported group on `pace is not None` diverged on a real state: `pace` runs over the
    CONSISTENCY laps (valid ∧ dropout-free), so a session whose every lap carries a GPS dropout has
    valid laps and no pace summary — the page rendered PACE with dashes and a real `best rolling`,
    the export dropped the whole group and the rolling number with it."""
    s = make_stitched_session()
    rolling = s.best_rolling_lap()
    assert rolling is not None, "fixture must have a rolling target to lose"

    # The divergent state: valid laps, no consistency laps ⇒ no pace summary.
    s.consistency_lap_ids = lambda: []
    s.stats.invalidate()
    assert s.stats.pace() is None and s.valid_lap_ids()

    sections = {sec.title: dict(sec.rows) for sec in export_data.stats_summary(s)}
    assert "PACE" in sections, "the export dropped a group the page still renders"
    pace = sections["PACE"]
    assert pace["best rolling"] == fmt_time(rolling), pace
    # ...and EVERY pace-derived row dashes rather than vanishing or faking a number. "within 1% of
    # best" is the one that catches a careless fix: `stats.within_pct_of_best` returns 0 (not None)
    # for an empty input, so reading it unconditionally puts "0 / 0" in a report whose page shows
    # an em-dash — a zero manufactured from no laps.
    assert all(v == "" for k, v in pace.items() if k != "best rolling"), pace
    # Both renderings show it, with the em-dash for the absent values.
    assert "best rolling" in _write_report(s)
    assert f"best rolling   {fmt_time(rolling)}" in " ".join(
        export_data.stats_summary_text(s, None).split()).replace("  ", " ") or \
        fmt_time(rolling) in export_data.stats_summary_text(s, None)
    print(f"test_pace_group_follows_the_pages_gate_not_the_pace_summarys OK "
          f"(best rolling {fmt_time(rolling)} kept)")


def test_report_states_what_the_timing_is_worth():
    """The exported page carries the trust verdict the app gates on. It used to state lap times
    with no qualifier anywhere on it, while the same session's card was greyed out entirely."""
    s = make_stitched_session()
    assert s.timing_verified, "fixture must start verified (it carries a track name)"
    assert "verified start line" in _write_report(s)

    # PROVISIONAL is the REAL condition, not a patched flag: `timing_verified` is a property that
    # reads False when the track is unknown and the line was never user-confirmed — the state a
    # recording on an unrecognised circuit loads in.
    s.track_name = None
    assert not s.timing_verified
    doc = _write_report(s)
    assert "PROVISIONAL" in doc and "arbitrary point" in doc, doc[:2000]
    print("test_report_states_what_the_timing_is_worth OK")


def test_clipboard_text_is_plain_and_complete():
    """The "Copy stats summary" payload: every group, every row, the disclosure, no markup."""
    s = make_stitched_session()
    text = export_data.stats_summary_text(s, None, title="GX0X0060")
    assert "<" not in text and ">" not in text, "the clipboard payload must be plain text"
    assert text.startswith("Pacer Studio"), text[:80]
    assert "GX0X0060" in text and (s.track_name or "") in text
    for sec in export_data.stats_summary(s):
        assert sec.title in text
        for label, _value in sec.rows:
            assert label in text, label
    assert s.ideal_sample().sentence() in text
    print(f"test_clipboard_text_is_plain_and_complete OK ({len(text)} chars)")


# ------------------------------------------------------------------- atomic writes (§7.5)
class _FailingHandle:
    """A text handle that passes `allow` writes through, then raises OSError — the ENOSPC shape.

    THE FAILURE HAS TO HAPPEN INSIDE THE WRITE, and getting that wrong is how this guard was
    vacuous on its first draft: poisoning the writer's DATA source (`laps_table`) raises before the
    file is ever opened, so a deliberately non-atomic writer passed the test. Everything §7.5 is
    about — a disk filling up, a network volume going away, a handle dying mid-document — happens
    after the target has been opened, which is the exact instant a plain `open(path, "w")` has
    already truncated the user's previous export."""

    def __init__(self, f, allow: int):
        self._f = f
        self._left = allow

    def write(self, s):
        if self._left <= 0:
            raise OSError("simulated ENOSPC")
        self._left -= 1
        return self._f.write(s)

    def __getattr__(self, name):
        return getattr(self._f, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._f.__exit__(*exc)


def _failing_fdopen(allow: int):
    """A drop-in `os.fdopen` whose handles die after `allow` chunks.

    `os.fdopen`, not `builtins.open`, because `_atomic_write` creates its temp with
    `tempfile.mkstemp` (a unique name, so an export can never clobber a user's own `<file>.tmp` in
    the folder they picked) and adopts the returned descriptor. Patching `open` stopped
    intercepting anything the moment that changed — and the test said so, which is the point of
    driving the failure through the real code path rather than a stub."""
    real = os.fdopen

    def opener(fd, mode="r", *a, **kw):
        f = real(fd, mode, *a, **kw)
        return _FailingHandle(f, allow) if "w" in mode else f

    return opener


def _assert_atomic(name, write, allow):
    """Drive `write` once for real, then again with the handle dying after `allow` chunks; the
    file on disk must still be the FIRST (good) export, and nothing may be left beside it."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        write(path)
        good = open(path, "rb").read()
        assert good, name

        real_fdopen = os.fdopen
        os.fdopen = _failing_fdopen(allow)
        try:
            write(path)
            raise AssertionError(f"{name}: the failing handle did not surface as an error")
        except OSError:
            pass
        finally:
            os.fdopen = real_fdopen

        assert open(path, "rb").read() == good, f"{name}: a failed write damaged the good file"
        assert os.listdir(tmp) == [name], f"{name}: left {os.listdir(tmp)} behind"


def test_a_users_own_tmp_file_beside_the_target_survives(fixture=None):
    """F5: the temp is `mkstemp`-unique, so an export cannot destroy a user file that happens to
    be named after the predictable temp the four app-support stores use.

    Those stores write inside a directory the app owns, where `<file>.tmp` can only collide with
    itself. These writers go wherever a save dialog pointed — so a fixed name would open, TRUNCATE
    and then delete a user's `laps.csv.tmp`."""
    s = fixture or make_stitched_session()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "laps.csv")
        decoy = path + ".tmp"
        with open(decoy, "w", encoding="utf-8") as f:
            f.write("the user's own file, not ours\n")
        export_data.write_laps_csv(path, s)
        assert os.path.exists(decoy), "the export destroyed a user file beside the target"
        assert open(decoy, encoding="utf-8").read() == "the user's own file, not ours\n"
        assert sorted(os.listdir(tmp)) == ["laps.csv", "laps.csv.tmp"], os.listdir(tmp)
    print("test_a_users_own_tmp_file_beside_the_target_survives OK")


def test_writers_are_atomic_and_leave_no_partial_file():
    """A writer that fails part-way must leave the PREVIOUS file untouched and nothing beside it.

    These three were the app's only non-atomic writes. Straight to the user's chosen path meant an
    export over a good previous one destroyed it the instant anything raised — `open(path, "w")`
    truncates before the first byte is written — and a first-time export left a partial document
    that looked finished. `allow` is chosen per writer to land the failure MID-file where the
    writer emits many chunks (the CSVs, one row each) and at chunk 0 for the report, which emits
    its whole page in one `write` and so can only fail before it."""
    s = make_stitched_session()
    _assert_atomic("laps.csv", lambda p: export_data.write_laps_csv(p, s), allow=2)
    _assert_atomic("channels.csv", lambda p: export_data.write_channels_csv(p, s, 0), allow=3)
    _assert_atomic("report.html",
                   lambda p: export_data.write_report_html(p, s, images=[("m", PNG_1PX)]), allow=0)
    print("test_writers_are_atomic_and_leave_no_partial_file OK (all three writers)")


# ============================================ the QUALITY-MARKER VOCABULARY on the exports
# `studio/data_quality.py` carries the per-marker decision (which Analysis Function codes pacer
# adopts, which it refuses, and why the letters stop at the app's edge). These pin the half that
# reaches a file: the column, the key that decodes it, and the disclosure that was MISSING.
def _csv_rows(session):
    """(header, lap rows, trailer rows) of a written laps.csv."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "laps.csv")
        export_data.write_laps_csv(path, session)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    blank = rows.index([])
    return rows[0], rows[1:blank], rows[blank + 2:]


def test_csv_gained_a_quality_column_and_flag_is_byte_identical():
    """`quality` is APPENDED; `flag` keeps its old vocabulary exactly.

    A consumer testing `flag == DROPOUT_FLAG` is the reader this ordering protects, and the column
    goes last because everything past index 4 is already variable in number (splits, corner pairs)
    — so anything reading that far reads by header name and appending breaks nothing."""
    s = make_session()
    header, laps, _trail = _csv_rows(s)
    assert header[:5] == ["lap", "time_s", "dist_m", "entry_kmh", "flag"], header[:5]
    assert header[-1] == "quality", header
    flag_i, qual_i = header.index("flag"), header.index("quality")
    flags = {r[flag_i] for r in laps}
    assert flags <= {"", export_data.DROPOUT_FLAG}, f"flag's vocabulary changed: {flags}"
    # lap 2 is the dropout lap in make_session: it is the one [u] row, and flag still says so.
    marked = [r for r in laps if r[qual_i]]
    assert len(marked) == 1 and marked[0][qual_i] == "[u]", [r[qual_i] for r in laps]
    assert marked[0][flag_i] == export_data.DROPOUT_FLAG
    print(f"test_csv_gained_a_quality_column_and_flag_is_byte_identical OK "
          f"({len(header)} columns, 1 marked row)")


def test_a_provisional_session_is_marked_on_every_csv_row():
    """THE GAP THIS VOCABULARY EXISTS FOR. laps.csv's only marker was the GPS dropout, so a file
    exported from a session whose start/finish line was auto-fitted and never confirmed — every
    time measured from an arbitrary point — carried a blank flag on every row and said nothing
    about it anywhere. The app greys the share card out entirely on that same flag."""
    s = make_session()
    assert s.timing_verified, "fixture must start verified (it carries a track name)"
    s.track_name = None   # the REAL condition, not a patched flag (timing_verified is a property)
    assert not s.timing_verified
    header, laps, trailer = _csv_rows(s)
    qual_i = header.index("quality")
    assert all("[p]" in r[qual_i] for r in laps), [r[qual_i] for r in laps]
    assert laps[2][qual_i] == "[p] [u]", laps[2][qual_i]   # the dropout lap carries both
    # …and the key decodes it, in the same file.
    key = {r[0]: r[3] for r in trailer if r[0].startswith("summary: quality ")}
    assert "summary: quality [p]" in key, sorted(key)
    assert "auto-fitted and not confirmed" in key["summary: quality [p]"]
    print("test_a_provisional_session_is_marked_on_every_csv_row OK")


def test_every_code_a_file_emits_is_decoded_by_its_key_in_both_writers():
    """A code with no key is the failure the convention exists to prevent. Asserted as a SET
    equality in both directions and on both writers: no code without a key, no key without a code."""
    from studio import data_quality
    s = make_session()
    s.track_name = None                      # provisional; lap 2 already carries the dropout
    header, laps, trailer = _csv_rows(s)
    qual_i = header.index("quality")
    emitted = {m for r in laps for m in r[qual_i].split()}
    keyed = {r[0].removeprefix("summary: quality ")
             for r in trailer if r[0].startswith("summary: quality ")}
    assert emitted == keyed, f"csv: emitted {sorted(emitted)} vs keyed {sorted(keyed)}"

    doc = _write_report(s)
    text = "".join(ET.fromstring(doc).itertext())
    for code in emitted:
        assert data_quality.MARK_MEANING[code] in text, f"report key lost {code}"
    print(f"test_every_code_a_file_emits_is_decoded_by_its_key_in_both_writers OK "
          f"({sorted(emitted)})")


def test_a_clean_session_gains_no_key_to_read_past():
    """No codes ⇒ no column content, no key, no break line. A legend on a spotless recording
    teaches the reader that the marks are decoration."""
    s = make_stitched_session()
    header, laps, trailer = _csv_rows(s)
    assert all(r[header.index("quality")] == "" for r in laps)
    assert not [r for r in trailer if r[0].startswith("summary: quality ")], trailer
    doc = _write_report(s)
    assert "Analysis Function" not in doc and "Break in series" not in doc
    print("test_a_clean_session_gains_no_key_to_read_past OK")


def test_a_break_in_series_is_named_not_just_coded_on_both_writers():
    """[b] says only THAT the recording is not continuous. Both files also say WHICH break — a
    skipped chapter here — because the code without the reason is not actionable."""
    s = make_session()
    s.skipped_chapters = ["GX020060.MP4"]
    header, laps, trailer = _csv_rows(s)
    assert all("[b]" in r[header.index("quality")] for r in laps)
    named = [r[3] for r in trailer if r[0] == "summary: break in series"]
    assert len(named) == 1 and "could not be read" in named[0], trailer
    text = "".join(ET.fromstring(_write_report(s)).itertext())
    assert "Break in series:" in text and "could not be read" in text
    print("test_a_break_in_series_is_named_not_just_coded_on_both_writers OK")


if __name__ == "__main__":
    test_csv_trailer_states_the_ideals_sample()
    test_csv_trailer_stays_ascii()
    test_report_prints_the_ideal_with_its_caption_and_sentence()
    test_card_carries_the_lap_count_next_to_the_gap()
    test_every_exported_ideal_agrees_with_every_other()
    test_stats_summary_values_equal_the_session_accessors()
    test_absent_signals_are_groups_and_dashes_never_zeros()
    test_degenerate_ideal_is_withheld_from_every_surface_together()
    test_the_clipboard_summary_states_the_timing_too()
    test_pace_group_follows_the_pages_gate_not_the_pace_summarys()
    test_report_states_what_the_timing_is_worth()
    test_clipboard_text_is_plain_and_complete()
    test_writers_are_atomic_and_leave_no_partial_file()
    test_a_users_own_tmp_file_beside_the_target_survives()
    test_csv_gained_a_quality_column_and_flag_is_byte_identical()
    test_a_provisional_session_is_marked_on_every_csv_row()
    test_every_code_a_file_emits_is_decoded_by_its_key_in_both_writers()
    test_a_clean_session_gains_no_key_to_read_past()
    test_a_break_in_series_is_named_not_just_coded_on_both_writers()
    print("\nALL EXPORT-DISCLOSURE TESTS PASSED")
