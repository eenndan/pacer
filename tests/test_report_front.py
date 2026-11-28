"""The exported report's front page (cool/report-front): it opens like the app's debrief — the lap
card beside a COACHING section — instead of a metadata table with its first picture ~2,600 px down
and no Coaching rows at all, while its own CORNERS note sent the reader to "the Coaching tab's
list".

WHAT IS HELD, on real Sessions loaded from synthetic GoPro recordings (the shipped demo, and planted
drives of the demo circuit: a three-way tie at the top, and a provisional shortlist of two) plus a two-lap
session for the empty state and a one-lap one for the gaps that are 0 by construction:

  * the order — header, COACHING, the pinned map and chart, then the Stats groups (two columns on
    a wide screen, cool/report-pictures-up) — and the hero card only when one is passed;
  * every Coaching string on the page is `coaching.page_text`'s, row by row, in its order — never a
    second derivation — and the empty state is `coaching.empty_state_copy`'s, never a blank section;
  * the notes a page has no hover for (scope, medians, column meanings, the braking caveat) and the
    report's Timing stamp under COACHING when the timing is provisional or degraded;
  * K6: the CORNERS note carries the Stats page's reconciliation in the report's words, pointing at
    the report's own COACHING section, never at "the Coaching tab";
  * one self-contained page; every interpolated string escaped; the card's print rule.

The window half (the card the export passes is the app's own lap card, and none on provisional
timing) is in tests/test_export_gates.py, beside the report's other real-window checks.
Run: python tests/test_report_front.py
"""
import base64
import html
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import coaching, coaching_panel, data_quality, export_data  # noqa: E402
from studio import stats as stats_service  # noqa: E402

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJ"
    "RU5ErkJggg==")

_SESSIONS: dict = {}


def _sessions() -> dict:
    """demo (verified, planted C1 habit), tie (verified: seed 1 at 10 laps with
    `_synthetic.sweep_habits` planted, a three-way tie at the top), twoprov (a DIFFERENT drive,
    seed 3 at 10 laps planted the same way, at an unknown origin: PROVISIONAL, and two ranked
    corners — the shortlist-of-two case). Until cool/coaching-ranks-past-the-step both were the
    habit-free seed 7 at 12 laps, at two origins; it ranks nothing past the timing step, and no one
    planted drive is both a three-way tie and a shortlist of two, twolaps (under coaching.MIN_LAPS: the empty state), onelap (seed
    3, one lap, driven in reverse: the ideal is that lap, every corner one pass), onedonor (seed
    29, two laps, one of them won every segment). Generated once
    per process; the TemporaryDirectory stays alive with them (sessions read their files lazily)."""
    if not _SESSIONS:
        from studio.dev import make_demo as md
        from studio.dev import synth_gopro as sg
        from studio.session import Session
        tmp = tempfile.TemporaryDirectory(prefix="report_front_")
        _SESSIONS["_tmp"] = tmp
        demo = os.path.join(tmp.name, "GX019000.MP4")
        md.make(demo, render=False)
        _SESSIONS["demo"] = Session.load([demo])
        from _synthetic import sweep_habits
        for name, seed, laps, origin in (("tie", 1, 10, md.DEMO_ORIGIN),
                                         ("twoprov", 3, 10, sg.ORIGIN),
                                         ("twolaps", md.DEMO_SEED, 2, md.DEMO_ORIGIN),
                                         ("onelap", 3, 1, md.DEMO_ORIGIN),
                                         ("onedonor", 29, 2, md.DEMO_ORIGIN)):
            # onelap is driven the other way round: forwards, at one lap, the generator's second
            # lone teleport lands 0.51 s after the only finish, a missing fix within the smoother's
            # reach of that line, so the lap wears the dropout ⚠ (every seed: the teleport sits at
            # a fixed fraction of the drive) and the session reads low. Reversed, it lands clear.
            rec = sg.generate(os.path.join(tmp.name, name), seed, laps, chapters=1, origin=origin,
                              reverse=name == "onelap",
                              habit=sweep_habits(laps) if name in ("tie", "twoprov") else None)
            _SESSIONS[name] = Session.load(rec.paths)
    return _SESSIONS


def _report(s, **kw) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "report.html")
        export_data.write_report_html(path, s, source_label="recording 9000", **kw)
        with open(path, encoding="utf-8") as f:
            return f.read()


def _text(el) -> str:
    return "".join(el.itertext())


def _section(root, title: str):
    for sec in root.iter("section"):
        h2 = sec.find("h2")
        if h2 is not None and _text(h2).startswith(title):
            return sec
    return None


def _page_text(s, unit=None) -> coaching.PageText:
    return coaching.page_text(s.coaching_opportunities(), s.coaching_brake_direction(),
                              unit or "kmh")


def _coaching_strings(sec) -> dict:
    """What the COACHING section prints, read back out of the markup."""
    rows = []
    for tr in sec.iter("tr"):
        tds = tr.findall("td")
        if tds:
            # A reason's braking line sits after a <br/>: put the newline page_text carries back.
            why = (tds[3].text or "") + "".join("\n" + (br.tail or "") for br in tds[3].iter("br"))
            rows.append((_text(tds[0]), _text(tds[1]), _text(tds[2]), why,
                         tr.get("class") != "muted"))
    return {"lead": [_text(p) for p in sec.iter("p") if p.get("class") == "lead"],
            "body": [_text(p) for p in sec.iter("p") if p.get("class") == "body"],
            "actions": [_text(li) for ul in sec.iter("ul") for li in ul.iter("li")],
            "head": [_text(th) for th in sec.iter("th")],
            "rows": rows,
            "notes": [_text(p) for p in sec.iter("p") if p.get("class") == "note"]}


def test_the_page_opens_on_the_card_and_coaching_before_the_stats_groups():
    """Header, then the lap card and COACHING, then SESSION / PACE / … in the app's order. No card
    passed (its gate said no) is no figure at all, and COACHING still leads."""
    s = _sessions()["demo"]
    text = _report(s, card=PNG_1PX)
    root = ET.fromstring(text)
    main = root.find(".//main")
    kids = list(main)
    assert kids[0].tag == "header", kids[0].tag
    front = kids[1]
    assert front.tag == "div" and front.get("class") == "front with-card", front.attrib
    fig, sec = list(front)
    assert fig.tag == "figure" and fig.get("class") == "hero", fig.tag
    img = fig.find("img")
    assert img.get("alt") == "Lap card" and img.get("class") == "card", img.attrib
    assert img.get("src") == "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
    assert img.get("width") == str(export_data.CARD_LAYOUT_PX)
    h2s = [_text(h) for h in root.iter("h2")]
    assert h2s[:4] == ["COACHING", "SESSION", "PACE", "MOMENTS"], h2s
    assert h2s.index("IDEAL LAP") < next(i for i, h in enumerate(h2s) if h.startswith("CORNERS"))

    bare = ET.fromstring(_report(s))
    assert not list(bare.iter("figure")) and 'class="front' not in _report(s)
    kids = list(bare.find(".//main"))
    assert kids[0].tag == "header" and _text(kids[1].find("h2")) == "COACHING", kids[1].tag
    print("ok order: header → card + COACHING → SESSION, PACE, MOMENTS …; no card, no figure")


def test_the_pinned_figures_come_right_after_the_front_page():
    """cool/report-pictures-up: the map and the chart sat after nine text cards, the lap strip and
    the Laps table (~4,800 px down at 1100 px on the demo). Now: header → card + COACHING → the
    figures, in the order they are passed → the Stats groups, together in one `.groups` block →
    CORNERS → SLOW LAPS → LAP STRIP → Laps. Nothing dropped: every section and image still once."""
    s = _sessions()["demo"]
    figs = [("Track map", PNG_1PX, 960, "the map"), ("Speed · Δ", PNG_1PX, 960, "the chart")]
    root = ET.fromstring(_report(s, card=PNG_1PX, lapstrip=PNG_1PX, images=figs))
    kids = list(root.find(".//main"))
    assert kids[0].tag == "header" and kids[1].get("class") == "front with-card", kids[:2]
    assert [(k.tag, k.get("class"), _text(k.find("h2"))) for k in kids[2:4]] == [
        ("section", "figure", "Track map"), ("section", "figure", "Speed · Δ")], kids[2:4]
    groups = kids[4]
    assert groups.tag == "div" and groups.get("class") == "groups", groups.attrib
    titles = [_text(sec.find("h2")) for sec in groups]
    assert titles == ["SESSION", "PACE", "MOMENTS", "IDEAL LAP", "SPEED · G", "DRIVING"], titles
    rest = [_text(k.find("h2")) for k in kids[5:]]
    assert rest[0].startswith("CORNERS") and rest[1:] == [
        "SLOW LAPS", "LAP STRIP", f"Laps ({len(s.valid_lap_ids())})"], rest
    h2s = [_text(h) for h in root.iter("h2")]
    assert len(h2s) == len(set(h2s)) == 13, h2s
    alts = [img.get("alt") for img in root.iter("img")]
    assert alts == ["Lap card", "Track map", "Speed · Δ", "Lap strip"], alts
    # Each figure keeps its stated width and its caption, under its own picture.
    for sec, (_title, _png, width, caption) in zip(kids[2:4], figs, strict=True):
        assert sec.find("img").get("width") == str(width), sec.find("img").attrib
        assert _text(sec.find("p")) == caption
    # No figure passed: no empty figure block, the groups straight under the front page.
    bare = list(ET.fromstring(_report(s, card=PNG_1PX)).find(".//main"))
    assert bare[2].get("class") == "groups", bare[2].attrib
    print("ok order: front page → map, chart → Stats groups (one block) → CORNERS … Laps")


def test_the_stats_groups_take_two_columns_only_on_a_wide_screen():
    """The narrow Stats cards flow in two columns at 1100 px and wider — down the first column
    then the second, so the Stats page's reading order holds — and one column below that and on
    paper. A card is never split across the columns, and on paper a group or a figure with its
    caption is kept on one page (the tall COACHING, CORNERS and Laps cards may still break)."""
    css = export_data.report_css()
    rule = re.search(r"@media screen and \(min-width: (\d+)px\) \{\s*\.groups \{ ([^}]*) \}", css)
    assert rule, "no wide-screen two-column rule for the Stats groups"
    assert int(rule.group(1)) == 1100, rule.group(1)
    assert "column-count: 2" in rule.group(2), rule.group(2)
    assert css.count("column-count") == 1, "the two columns must live only in the wide rule"
    assert ".groups > section { break-inside: avoid; }" in css
    paper = css.split("@media print {", 2)[2]
    assert "section.figure, .groups > section { break-inside: avoid; }" in paper, paper
    # …and the print rule that lets a tall section break is still there for the others.
    assert "break-inside: auto" in paper
    print("ok columns: two at ≥ 1100 px on screen, one below and on paper; cards kept whole")


def _assert_page_text_on_page(s, unit=None) -> dict:
    pt = _page_text(s, unit)
    sec = _section(ET.fromstring(_report(s, unit=unit)), "COACHING")
    got = _coaching_strings(sec)
    assert got["lead"] == [pt.headline], (got["lead"], pt.headline)
    assert got["body"] == ([pt.theme] if pt.theme else []), (got["body"], pt.theme)
    assert got["actions"] == list(pt.actions), (got["actions"], pt.actions)
    assert got["head"] == list(export_data.REPORT_COACHING_COLUMNS), got["head"]
    want = [(r.corner, r.lost, r.done_it, r.reason, r.ranked) for r in pt.rows]
    assert got["rows"] == want, (got["rows"], want)
    # The shortlist the headline sums leads, in its own body; the rest under it.
    bodies = sec.findall(".//tbody")
    assert len(bodies[0].findall("tr")) == len(pt.shortlist), len(bodies[0].findall("tr"))
    return got


def test_every_coaching_string_is_page_texts_in_the_pages_order():
    """The demo: the headline, the theme, both actions, and all six shown rows — the planted C1 with
    its braking line, then five "Not ranked" rows muted (three level with the best lap inside the
    timing step, two with no second lap at the target) — exactly as `page_text` says them, under the
    panel's four column words. The planted tie session: a three-way tie at the top."""
    assert export_data.REPORT_COACHING_COLUMNS == tuple(
        coaching_panel.OpportunitiesPanel._COLUMNS[:4])
    got = _assert_page_text_on_page(_sessions()["demo"])
    assert got["actions"][-1] == "Start with C1: +0.36 s, and you have matched it on 6 of 14 laps."
    reasons = [r[3] for r in got["rows"]]
    assert reasons[0].endswith("\nBraking later went with quicker passes here (14 laps)"), reasons
    assert sum(r.startswith("Not ranked: your typical pass here is level with your best lap") for r in reasons) == 3
    muted = [r for r in got["rows"] if not r[4]]
    assert len(muted) == 5 and all(r[3].startswith("Not ranked: ") for r in muted), muted
    tie = _assert_page_text_on_page(_sessions()["tie"])
    assert any(a.startswith("Start with C7, C5 or C3:") and "this cannot rank them" in a
               for a in tie["actions"]), tie["actions"]
    print("ok coaching: page_text row for row on the demo and on a tie")


def test_the_notes_say_what_the_page_says_on_hover():
    """Scope + medians, the two column meanings (the baseline among them), and the braking caveat
    naming the row that prints a direction — the panel's own strings (`coaching`)."""
    notes = _coaching_strings(_section(ET.fromstring(_report(_sessions()["demo"])),
                                       "COACHING"))["notes"]
    assert notes[0] == (f"{coaching.scope_note(page=False, n=1)} "
                        f"{coaching.medians_note(bars=False)}"), notes[0]
    assert notes[1] == f"Time lost — {coaching.COLUMN_TIPS['Time lost']}", notes[1]
    assert "versus your own best lap's same corner" in notes[1]
    assert notes[2] == f"Done it? — {coaching.COLUMN_TIPS['Done it?']}", notes[2]
    assert notes[3] == f"The braking line under C1 {coaching.BRAKE_DIRECTION_CAVEAT}", notes[3]
    assert len(notes) == 5, notes          # verified, clean clock: no Timing stamp; the pointer last
    assert notes[4].startswith("0.36 s is how far your typical pass through this corner")
    # The tie session prints no direction: no caveat.
    seven = _coaching_strings(_section(ET.fromstring(_report(_sessions()["tie"])), "COACHING"))
    assert not any("braking line" in n for n in seven["notes"]), seven["notes"]
    # The panel reads the same strings (byte for byte with what it printed before they moved).
    assert coaching_panel._SCOPE_TOOLTIP.startswith(coaching.scope_note() + " Coaching ▸ ")
    assert coaching_panel._HEADER_TIPS is coaching.COLUMN_TIPS
    assert coaching_panel.empty_state_copy is coaching.empty_state_copy
    print("ok notes: scope, medians, columns, braking caveat — the panel's words")


def test_provisional_and_degraded_timing_stamp_the_coaching_rows():
    """PROVISIONAL (the planted two-ranked drive at an unknown origin): the rows the Coaching page shows, unchanged, and
    the report's Timing stamp under them. A degraded clock (GPS low, then the media-clock fallback)
    stamps them too; a clean one does not."""
    prov = _sessions()["twoprov"]
    assert not prov.timing_verified
    got = _assert_page_text_on_page(prov)
    stamp = f"Timing: {export_data.timing_meta(prov, where='on this page')}"
    # The stamp, then the totals pointer (it has an IDEAL LAP section and two ranked corners).
    assert "PROVISIONAL" in stamp and got["notes"][-2] == stamp, got["notes"]
    assert got["notes"][-1].startswith("0.37 s is how far your typical pass through these 2 ")
    s = _sessions()["demo"]
    saved = s._timing_quality
    try:
        for quality, word in ((data_quality.TimingQuality(dropped_fraction=0.4), "GPS"),
                              (data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK),
                               "ESTIMATED")):
            s._timing_quality = quality
            got = _assert_page_text_on_page(s)
            stamp = f"Timing: {export_data.timing_meta(s, where='on this page')}"
            assert word in stamp and got["notes"][-2] == stamp, (word, got["notes"])
    finally:
        s._timing_quality = saved
    print("ok timing: provisional / GPS low / estimated stamp COACHING; the rows are the page's")


def test_no_ranked_table_prints_the_pages_own_empty_state():
    """Under coaching.MIN_LAPS clean laps `page_text` has no headline and no rows: the section
    prints `empty_state_copy`'s title and body — what the Coaching page shows — and no table."""
    s = _sessions()["twolaps"]
    pt = _page_text(s)
    assert pt.headline == "" and not pt.rows
    title, body = coaching.empty_state_copy(s.coaching_opportunities(), s)
    sec = _section(ET.fromstring(_report(s)), "COACHING")
    got = _coaching_strings(sec)
    assert got["lead"] == [title] and got["body"] == [body], got
    assert title == "Not enough clean laps yet." and not got["rows"] and not got["notes"], got
    print(f"ok empty state: {title!r}")


def test_the_corners_note_reconciles_the_answers_in_the_reports_words():
    """K6: the report printed three "where is the time" answers — COACHING's 0.43 s, IDEAL LAP's
    0.32 s, CORNERS' Med loss — and the Stats page's sentence that ties them was not on it. It is
    now, under CORNERS (below both other answers), naming the report's own COACHING section; and the
    mark rule no longer sends a reader to a tab the page does not have."""
    s = _sessions()["demo"]
    text = _report(s)
    sec = _section(ET.fromstring(text), "CORNERS")
    notes = [_text(p) for p in sec.iter("p")]
    first = notes[0]
    report = s.corner_report()
    left = sum(r.n_laps - r.n for r in report if getattr(r, "n_laps", None) is not None)
    coach = coaching.corners_reconciliation(s.coaching_opportunities(), left, page=False)
    assert coach.startswith("The COACHING section above measures the SAME corners against your "
                            "best lap: its headline totals 0.36 s over 1 corner"), coach
    gap = coaching.ideal_gap_s(s)
    assert f"{coach} {coaching.ideal_gap_sentence(gap)} {coaching.BASELINES_SENTENCE}" in first
    assert "Your ideal lap is 0.30 s under your best." in first, first
    assert stats_service.corner_mark_rule(hover=False) in notes
    assert "the COACHING section's list of what to work on" in notes[1], notes[1]
    assert "Coaching tab" not in text, re.findall(r"[^.]*Coaching tab[^.]*", text)
    # The app keeps its own words.
    assert "the Coaching tab's list" in stats_service.corner_mark_rule()
    assert coaching.corners_reconciliation(s.coaching_opportunities(), left).startswith(
        "The Coaching tab measures the SAME corners")
    print("ok K6: CORNERS reconciles COACHING, the ideal and Med loss, in the report's words")


def test_a_one_lap_report_states_no_zero_as_a_measurement():
    """QA v0.13.0 (synthetic seed 3, one lap, through the real loader): the CORNERS note read
    "Summed, that is 0.00 s. … Your ideal lap is 0.00 s under your best." over seven "+0.00" Med
    loss cells, while the report's own IDEAL LAP section was hidden — the ideal is the one lap
    (`ideal_donor_lap_id`), and one pass is a corner's Best and Median at once. Now: dashes, and
    neither sentence."""
    s = _sessions()["onelap"]
    assert len(s.valid_lap_ids()) == 1 and s.ideal_donor_lap_id() is not None
    text = _report(s)
    sec = _section(ET.fromstring(text), "CORNERS")
    body = _text(sec)
    cells = [_text(tr.findall("td")[5]) for tr in sec.iter("tr") if tr.findall("td")]
    assert cells and set(cells) == {"—"}, cells
    for gone in ("Your ideal lap is", "Summed, that is", "+0.00"):
        assert gone not in body, (gone, body)
    assert "IDEAL LAP" not in text
    # The rule is the donor gate, not the lap count: seed 29 drives TWO clean laps and one of them
    # won every segment, so its ideal is that lap too — neither the report's CORNERS note nor the
    # Stats page's (the same `ideal_gap_s`, through CornersSection._ideal_gap) states a 0.00 gap.
    from studio.stats_corners import CornersSection
    two = _sessions()["onedonor"]
    assert len(two.valid_lap_ids()) == 2 and two.ideal_donor_lap_id() is not None
    assert CornersSection._ideal_gap(two) is None
    two_body = _text(_section(ET.fromstring(_report(two)), "CORNERS"))
    assert "Your ideal lap is" not in two_body, two_body
    print(f"ok one lap: {len(cells)} Med loss cells dashed, no 0.00 sum, no 0.00 ideal gap "
          f"(and none on a two-lap, one-donor session)")


def test_one_self_contained_page_with_every_string_escaped():
    """No script, no link, no http; every src a data: URI, the card's included. Every string the
    COACHING section interpolates is escaped: planted markup in `page_text` and in the empty state
    reaches the page as text, never as elements."""
    s = _sessions()["demo"]
    text = _report(s, card=PNG_1PX, lapstrip=PNG_1PX)
    root = ET.fromstring(text)
    assert "http" not in text.lower() and "url(" not in text and "<script" not in text
    assert not list(root.iter("link"))
    srcs = [el.get("src") for el in root.iter() if el.get("src") is not None]
    assert len(srcs) == 2 and all(x.startswith("data:image/png;base64,") for x in srcs), srcs

    evil = '<b id="x">&"\'</b>'
    real_page, real_empty = coaching.page_text, coaching.empty_state_copy
    row = coaching.RowText(cid=1, corner="C1" + evil, lost="+0.36 s" + evil, done_it=evil,
                           reason=evil + "\n" + evil, ranked=True)
    coaching.page_text = lambda *a, **k: coaching.PageText(
        headline=evil, theme=evil, actions=(evil,), shortlist=(row,), rows=(row,))
    try:
        planted = _report(s)
        coaching.page_text = lambda *a, **k: coaching.PageText("", "", (), (), ())
        coaching.empty_state_copy = lambda *a, **k: (evil, evil)
        empty = _report(s)
    finally:
        coaching.page_text, coaching.empty_state_copy = real_page, real_empty
    # headline, theme, action, four cells (the reason twice) and the braking note's corner name.
    for page, n in ((planted, 9), (empty, 2)):
        assert '<b id="x">' not in page, "markup reached the page"
        assert page.count(html.escape(evil)) == n, (page.count(html.escape(evil)), n)
        assert not list(ET.fromstring(page).iter("b"))
    print("ok page: self-contained, every COACHING string escaped")


def test_print_draws_the_card_with_a_hairline_beside_coaching():
    """A dark card on white paper gets the lap strip's hairline; on paper the opening stays side by
    side with the card a third of the width, and is never split across pages."""
    css = export_data.report_css()
    paper = css.split("@media print {", 2)[2]
    assert "img.card { border: 1px solid var(--text); border-radius: 0; }" in paper, paper
    assert ".front.with-card { grid-template-columns: 58mm minmax(0, 1fr); }" in paper
    assert "figure.hero { max-width: none; break-inside: avoid; }" in paper
    screen = css.split("@media print {", 2)[1]
    assert ".front.with-card { grid-template-columns: minmax(200px, 30%) minmax(0, 1fr); }" in screen
    print("ok print: the card has its hairline and stays beside COACHING")


def test_mph_reaches_the_coaching_reasons():
    """The reason's apex deficit is in the reader's unit, as on the page."""
    got = _assert_page_text_on_page(_sessions()["demo"], unit="mph")
    assert "mph)" in got["rows"][0][3] and "km/h" not in got["rows"][0][3], got["rows"][0]
    print("ok unit: the apex deficit reads in mph")


def _pointer(s):
    sec = _section(ET.fromstring(_report(s)), "COACHING")
    hits = [n for n in _coaching_strings(sec)["notes"] if "is not time under your best lap" in n]
    assert len(hits) <= 1, hits
    return hits[0] if hits else None


def test_coachings_total_and_the_ideal_gap_are_told_apart_on_the_first_screen():
    """Review of #592 (P2): COACHING's "0.43 s … vs your best lap" and IDEAL LAP's "0.32 s on the
    table · vs your best" read as one contradiction. COACHING's last note tells them apart in the
    page's own two figures — and only when both are printed: no IDEAL LAP section, no ranked
    corner, or the empty state print no pointer; no CORNERS reconciliation drops its last clause."""
    import dataclasses
    s = _sessions()["demo"]
    root = ET.fromstring(_report(s))
    head = _coaching_strings(_section(root, "COACHING"))["lead"][0]
    ideal = dict((tr.findtext("th"), tr.findtext("td"))
                 for tr in _section(root, "IDEAL LAP").iter("tr"))[export_data.IDEAL_GAP_ROW]
    assert "0.36 s in your worst corner" in head and ideal == "0.30 s", (head, ideal)
    assert _pointer(s) == (
        "0.36 s is how far your typical pass through this corner sits from your best lap's — "
        "it is not time under your best lap. Time under your best lap is the ideal lap's 0.30 s "
        "(IDEAL LAP below); the note under CORNERS ties the two together."), _pointer(s)
    # Counted right for one, two, three corners; the CORNERS clause only when that note is printed.
    one = coaching.totals_pointer(0.36, 1, "0.32 s", corners_note=False)
    assert one.startswith("0.36 s is how far your typical pass through this corner sits"), one
    assert one.endswith("the ideal lap's 0.32 s (IDEAL LAP below)."), one
    assert "through these 2 corners" in coaching.totals_pointer(0.09, 2, "0.29 s",
                                                                 corners_note=True)
    # No IDEAL LAP section: no pointer.
    real_sb = s.ideal_segment_bests
    s.ideal_segment_bests = lambda: None
    try:
        assert _section(ET.fromstring(_report(s)), "IDEAL LAP") is None
        assert _pointer(s) is None
    finally:
        s.ideal_segment_bests = real_sb
    # No ranked corner (every row abstained, as the evidence gate can leave it): no pointer.
    opps = s.coaching_opportunities()
    zero = dataclasses.replace(opps, rows=[dataclasses.replace(
        r, evidence=dataclasses.replace(r.evidence, abstain=coaching.ABSTAIN_SPREAD))
        for r in opps.rows])
    real_co = s.coaching_opportunities
    s.coaching_opportunities = lambda: zero
    try:
        assert not coaching.shortlist(zero) and _pointer(s) is None
        notes = _coaching_strings(_section(ET.fromstring(_report(s)), "COACHING"))["notes"]
        assert notes[0].startswith(coaching.scope_note(page=False, n=0)), notes[0]
    finally:
        s.coaching_opportunities = real_co
    # The empty state: no pointer.
    assert _pointer(_sessions()["twolaps"]) is None
    # Escaped like every other note.
    evil = '<b id="x">&</b>'
    real_tp = coaching.totals_pointer
    coaching.totals_pointer = lambda *a, **k: evil
    try:
        page = _report(s)
    finally:
        coaching.totals_pointer = real_tp
    assert '<b id="x">' not in page and html.escape(evil) in page
    print("ok P2: the totals pointer, from the page's figures, only where both are printed")


def test_the_scope_note_counts_the_shortlist_the_headline_sums():
    """Review of #592 (P3): "your top 3 corners" under a headline summing two, and a total
    described where none is printed. Counted in the headline's words; at 3 the app's sentence is
    the one it always printed."""
    three = coaching.scope_note()
    assert three == coaching.scope_note(n=3) and three.endswith(
        "The total is your top 3 corners that cleared the evidence gate; corners that did not are "
        "listed below with the reason."), three
    assert "The total is your top 2 corners that cleared" in coaching.scope_note(n=2)
    assert "The total is your worst corner that cleared" in coaching.scope_note(n=1)
    none = coaching.scope_note(n=0)
    assert "No corner is ranked, so there is no total" in none, none
    assert "top 0" not in none and "The total is" not in none
    assert coaching_panel.scope_tooltip(3) == coaching_panel._SCOPE_TOOLTIP
    # The provisional session ranks two: the report's note says two, as its headline does.
    s = _sessions()["twoprov"]
    got = _coaching_strings(_section(ET.fromstring(_report(s)), "COACHING"))
    assert "top 2 corners" in got["lead"][0] and "your top 2 corners that" in got["notes"][0]
    print("ok P3: the scope note counts the headline's shortlist")


if __name__ == "__main__":
    test_the_page_opens_on_the_card_and_coaching_before_the_stats_groups()
    test_the_pinned_figures_come_right_after_the_front_page()
    test_the_stats_groups_take_two_columns_only_on_a_wide_screen()
    test_every_coaching_string_is_page_texts_in_the_pages_order()
    test_the_notes_say_what_the_page_says_on_hover()
    test_provisional_and_degraded_timing_stamp_the_coaching_rows()
    test_no_ranked_table_prints_the_pages_own_empty_state()
    test_the_corners_note_reconciles_the_answers_in_the_reports_words()
    test_a_one_lap_report_states_no_zero_as_a_measurement()
    test_one_self_contained_page_with_every_string_escaped()
    test_print_draws_the_card_with_a_hairline_beside_coaching()
    test_mph_reaches_the_coaching_reasons()
    test_coachings_total_and_the_ideal_gap_are_told_apart_on_the_first_screen()
    test_the_scope_note_counts_the_shortlist_the_headline_sums()
    print("test_report_front: OK")
