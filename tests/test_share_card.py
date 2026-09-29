"""Unit tests for the shareable lap card (image) — the one-tap viral output.

Two layers, tested separately (mirroring the module split):
  * share_card.card_data — the PURE data function off Session accessors (no Qt for the numbers):
    the expected fields (best lap, Δ-to-ideal, top opportunity, track, date, unit), the km/h↔mph
    unit carried into the reason sentence, and the HONESTY verdict — a provisional/no-valid-lap
    session is `blocked` (no card); a data-quality-degraded session is `stamp`ed, not blocked.
  * share_card.render_card — the Qt composition: renders to a non-empty QImage of the expected
    CARD_W×CARD_H size, on both palettes, with + without a map thumbnail.
  * The app wiring (offscreen, DI): File ▸ Export ▸ "Lap card (image)…" saves the PNG through a
    monkeypatched QFileDialog; "Copy lap card" puts an image on a monkeypatched clipboard; the
    Export-menu sync greys both out on a blocked session; and the PBToast "Share your PB →"
    button routes to its injected on_share callback.

Runs offscreen (QImage/QPainter need a QApplication). No telemetry file, no pacer Laps — the
Session surface is duck-typed exactly as far as card_data reaches. Run: python tests/test_share_card.py
"""
import os
import re
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import coaching, corner_model, share_card, theme  # noqa: E402

theme.register_fonts()


# --------------------------------------------------------------------- fakes
def _quality(degraded=False):
    return SimpleNamespace(degraded=degraded)


def _apex_opp(cid=4, direction=-1, time_lost=0.28, deficit=4.0):
    """A real coaching.Opportunity whose dominant reason is an apex-speed deficit (so
    reason_sentence renders "carry more apex speed (−N unit)")."""
    r = coaching.Reason(kind=coaching.REASON_APEX, contribution=0.2, apex_speed_deficit=deficit,
                        brake_extra_s=0.0, coast_extra_s=0.0, sigma=0.1)
    return coaching.Opportunity(cid=cid, direction=direction, time_lost=time_lost,
                                entry_dist=100.0, reason=r)


class FakeSession:
    """The minimal Session surface share_card.card_data reaches through — duck-typed, no pacer."""

    def __init__(self, *, track="Daytona MK", verified=True, degraded=False, best_id=3,
                 best_time=68.42, ideal=67.90, date="2026-06-29", opps=None, ideal_donor=None,
                 ideal_laps=24):
        # `ideal_laps=None` models a Session double with no `ideal_sample` accessor at all — the
        # getattr-guarded degradation card_data takes, asserted below.
        self._ideal_sample = (None if ideal_laps is None else
                              corner_model.IdealSample(donors=11, laps=ideal_laps,
                                                       corners=12, segments=25))
        self.track_name = track
        self.timing_verified = verified
        self.timing_quality = _quality(degraded)
        self._best_id = best_id
        self._best_time = best_time
        self._ideal = ideal
        self._ideal_donor = ideal_donor  # a lap id => ONE lap won every segment (degenerate)
        self._date = date
        self._opps = opps if opps is not None else coaching.Opportunities(
            enough=True, n_laps=5, median_lap_id=3, rows=[_apex_opp()])

    def best_lap_id(self):
        return self._best_id

    def lap_time(self, lap_id):
        return self._best_time

    def ideal_total(self):
        return self._ideal

    def ideal_donor_lap_id(self):
        return self._ideal_donor

    def ideal_sample(self):
        return self._ideal_sample

    def session_date(self):
        return self._date

    def coaching_opportunities(self):
        return self._opps


# --------------------------------------------------------------------- data-layer tests
def test_card_data_carries_the_expected_fields():
    """card_data reads the headline display values off Session accessors: track, date, best lap
    (formatted), the Δ-to-ideal gap (best − ideal ≥ 0), the unit, and the #1 opportunity as a
    display row (corner label + glyph, time lost, reason sentence)."""
    d = share_card.card_data(FakeSession(), unit="kmh")
    assert d.track == "Daytona MK"
    assert d.date == "2026-06-29"
    assert d.best_time == "1:08.420", d.best_time
    assert d.best_lap_id == 3
    assert abs(d.delta_to_ideal_s - (68.42 - 67.90)) < 1e-6, d.delta_to_ideal_s
    # §5.4: the SAMPLE travels with the gap, off `ideal_sample()` — the same accessor the Stats
    # tile caption and the exported report read.
    assert d.ideal_laps == 24, d.ideal_laps
    # ...and a Session double with no `ideal_sample` accessor at all degrades to the un-counted
    # line rather than crashing the one artifact a user SENDS to someone.
    assert share_card.card_data(FakeSession(ideal_laps=None), unit="kmh").ideal_laps is None
    assert d.unit == "kmh"
    assert not d.blocked and d.stamp == ""
    assert d.top_opp is not None
    assert d.top_opp.corner_label.startswith("C4"), d.top_opp.corner_label
    assert abs(d.top_opp.time_lost_s - 0.28) < 1e-6
    assert "apex speed" in d.top_opp.reason and "km/h" in d.top_opp.reason
    print("test_card_data_carries_the_expected_fields OK")


def test_card_data_unit_flips_reason_to_mph():
    """The apex-speed deficit in the reason sentence honours the active km/h↔mph unit (the shared
    coaching.reason_sentence conversion), and the unit id is carried on the card data."""
    d = share_card.card_data(FakeSession(), unit="mph")
    assert d.unit == "mph"
    assert "mph" in d.top_opp.reason and "km/h" not in d.top_opp.reason, d.top_opp.reason
    # 4.0 km/h ≈ 2.49 mph — the value is converted, not just relabelled.
    assert "2.5 mph" in d.top_opp.reason, d.top_opp.reason
    print("test_card_data_unit_flips_reason_to_mph OK")


def test_card_data_unknown_track_and_no_ideal():
    """A session with no detected track name shows 'Unknown track'; with no ideal buildable the
    Δ-to-ideal is None (the card just omits that line). Track name None but user-confirmed timing
    still counts verified (not blocked)."""
    s = FakeSession(track=None, ideal=None)
    s.timing_verified = True  # user-confirmed start line on an unknown track
    d = share_card.card_data(s, unit="kmh")
    assert d.track == "Unknown track"
    assert d.delta_to_ideal_s is None
    assert not d.blocked
    print("test_card_data_unknown_track_and_no_ideal OK")


def test_card_data_blocks_provisional_and_no_lap():
    """HONESTY: a provisional (unverified start line) session yields a blocked card (an unverified
    lap time is never a brag), and so does a session with no valid best lap."""
    prov = share_card.card_data(FakeSession(track=None, verified=False), unit="kmh")
    assert prov.blocked, "provisional timing must block the shareable card"
    no_lap = share_card.card_data(FakeSession(best_id=None), unit="kmh")
    assert no_lap.blocked and no_lap.best_time == "—"
    print("test_card_data_blocks_provisional_and_no_lap OK")


def test_card_data_stamps_degraded_timing():
    """A data-quality-degraded (media-clock / low-GPS) session still renders a card, but STAMPED so
    the number is shown honestly as estimated — never blocked, never presented as exact."""
    d = share_card.card_data(FakeSession(degraded=True), unit="kmh")
    assert not d.blocked, "a degraded but verified session should stamp, not block"
    assert d.stamp == "estimated timing", d.stamp
    print("test_card_data_stamps_degraded_timing OK")


def test_card_data_no_opportunity_when_too_few_laps():
    """Under MIN_LAPS clean laps the coaching model has no rows → the card's top opportunity is
    None (the card shows a gentle 'drive more laps' line instead of a fabricated tip)."""
    few = coaching.Opportunities(enough=False, n_laps=1, median_lap_id=None, rows=[])
    d = share_card.card_data(FakeSession(opps=few), unit="kmh")
    assert d.top_opp is None
    assert not d.blocked  # a verified best lap is still shareable without a coaching tip
    print("test_card_data_no_opportunity_when_too_few_laps OK")


def test_card_data_survives_coaching_error():
    """A hiccup in coaching_opportunities degrades to 'no opportunity' — the card is never broken
    by the coaching layer (the top opportunity is optional)."""
    class Boom(FakeSession):
        def coaching_opportunities(self):
            raise RuntimeError("coaching blew up")
    d = share_card.card_data(Boom(), unit="kmh")
    assert d.top_opp is None and not d.blocked
    print("test_card_data_survives_coaching_error OK")


# ------------------------------------------ QA W1 REG-1: the card honours the page's tie rule
# The working set's RANKED rows, biggest loss first — (cid, time lost s, IQR s, counted laps, laps at
# the target, reach) — as the QA r4 ADVICE lane extracted them through the real (jailed) loader; the
# same numbers tests/test_coaching.py's `_WORKING_SET_RANKED` pins the page's "Start with" on
# (asserted equal to it below, by `_page_twin`).
_R, _P = coaching.REACH_REPEAT, coaching.REACH_RARE
_WORKING_SET_RANKED = {
    "MK_18_09_26": [(5, 0.134780, 0.230944, 19, 6, _R), (2, 0.087096, 0.106218, 16, 3, _R),
                    (8, 0.064168, 0.107409, 17, 4, _R)],
    "SD_19_09_26": [(1, 0.146633, 0.200070, 36, 4, _R), (5, 0.093451, 0.064636, 35, 3, _P),
                    (7, 0.077588, 0.101853, 36, 4, _R), (2, 0.059539, 0.096228, 35, 5, _R),
                    (3, 0.049277, 0.090632, 36, 9, _R)],
    "SD_30_08_26": [(7, 0.099327, 0.095077, 37, 5, _R), (5, 0.082542, 0.113700, 37, 6, _R),
                    (3, 0.046750, 0.078567, 37, 9, _R)],
    "Sandown 3h 2026": [(1, 0.229400, 0.309380, 61, 3, _P), (4, 0.193470, 0.230531, 61, 5, _P),
                        (7, 0.177929, 0.304993, 62, 4, _P), (6, 0.167461, 0.187463, 62, 4, _P)],
}


def _page_twin() -> dict:
    """tests/test_coaching.py's `_WORKING_SET_RANKED`, read from its SOURCE — importing that file
    would theme this process's QApplication at module scope — so the card's copy cannot drift from
    the one the page's "Start with" is pinned on without a test saying so."""
    import ast

    class _Reach(ast.NodeTransformer):   # the table's only names are the two reach constants
        def visit_Name(self, node: ast.Name) -> ast.Constant:
            return ast.Constant({"_R": _R, "_P": _P}[node.id])

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_coaching.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    table = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                 and [getattr(t, "id", None) for t in n.targets] == ["_WORKING_SET_RANKED"])
    return ast.literal_eval(_Reach().visit(table))


def _working_set_session(name: str) -> FakeSession:
    """A card session whose coaching rows are one working-set recording's real ranked rows."""
    return _ranked_session(_WORKING_SET_RANKED[name])


def _ranked_session(ranked: list[tuple]) -> FakeSession:
    """A card session over these ranked rows, `_WORKING_SET_RANKED`'s tuple shape (the reason is
    `_apex_opp`'s: the tie rule reads only the losses, spreads and lap counts)."""
    rows = [coaching.Opportunity(
        cid=cid, direction=1, time_lost=loss, entry_dist=100.0, reason=_apex_opp().reason,
        evidence=coaching.Evidence(n_laps=n, reach_laps=hit, reach=reach, iqr=iqr,
                                   abstain=coaching.ABSTAIN_NONE))
        for cid, loss, iqr, n, hit, reach in ranked]
    return FakeSession(opps=coaching.Opportunities(
        enough=True, n_laps=max(r.evidence.n_laps for r in rows), median_lap_id=3, rows=rows))


def _named(text: str) -> tuple[list[str], int]:
    """(the corners a line names, in order; how many more it counts) — "C1, C4, C7 or C6" reads
    (["C1", "C4", "C7", "C6"], 0) and "C3 · C1 · C4 · 3 more" (["C3", "C1", "C4"], 3)."""
    more = re.search(r"(\d+) more", text)
    return re.findall(r"C\d+", text), int(more.group(1)) if more else 0


# A tie wider than the card's row names: six corners the page cannot rank (the spreads dwarf the
# 0.005 s steps), so the page names all six and the card its first three and "3 more".
_WIDE_TIE = [(c, 0.40 - 0.005 * i, 0.3, 30, 4, _R) for i, c in enumerate((3, 1, 4, 5, 9, 2))]


def test_a_tie_the_page_will_not_rank_is_not_crowned_on_the_card():
    """QA W1 REG-1. On MK_18_09_26 the Coaching page says "Start with C5, C2 or C8: +0.13 s down to
    +0.06 s sit closer together than your own lap-to-lap spread, so this cannot rank them.", and
    the shared lap card said "BIGGEST OPPORTUNITY C5 +0.13 s" — the one surface that leaves the
    app overclaiming the one ranking the page refuses. The same on SD_30_08_26 (C7 vs "C7 or C5")
    and Sandown 3h (C1 vs "C1, C4, C7 or C6").

    The card now asks the page's own rule (`coaching.lead_ties`, off the same rows and lead) and
    names the same corners in the same order; SD_19_09_26, whose lead stands alone on the page,
    keeps the single-lead block word for word. The page names EVERY tied corner (QA1-THEME-CLASH);
    the card's fixed-width row names at most `_OPP_TIE_NAMES` of them and counts the rest, so its
    names are the page's first ones and its "N more" is exactly how many the page names after
    them — checked on a six-corner tie too, since no working-set tie is that wide."""
    assert _WORKING_SET_RANKED == _page_twin(), "this table drifted from test_coaching.py's twin"
    TIE = "TOP OPPORTUNITIES · too close to rank"
    expect = {  # (heading, corner line, loss figure) on the card
        "MK_18_09_26": (TIE, "C5 · C2 · C8", "+0.13 to +0.06 s"),
        "SD_30_08_26": (TIE, "C7 · C5", "+0.10 to +0.08 s"),
        "Sandown 3h 2026": (TIE, "C1 · C4 · C7 · C6", "+0.23 to +0.17 s"),
        "SD_19_09_26": ("BIGGEST OPPORTUNITY", "C1 ⟲", "+0.15 s"),
        "six-corner tie": (TIE, "C3 · C1 · C4 · 3 more", "+0.40 to +0.38 s"),
    }
    for name, (heading, label, loss) in expect.items():
        session = (_ranked_session(_WIDE_TIE) if name == "six-corner tie"
                   else _working_set_session(name))
        top = share_card.card_data(session, unit="kmh").top_opp
        assert top is not None, name
        assert top.corner_label == label, (name, top.corner_label)
        assert (share_card.opp_heading(top), share_card.opp_loss(top)) == (heading, loss), \
            (name, share_card.opp_heading(top), share_card.opp_loss(top))
        # The card names the page's "Start with" corners: a prefix of them, and a count of the rest.
        opps = session.coaching_opportunities()
        page = coaching.theme_actions(coaching.session_theme(opps.rows), opps.rows)[-1]
        assert page.startswith("Start with "), page
        (page_names, page_more), (card_names, card_more) = (
            _named(page.split(":")[0]), _named(top.corner_label))
        assert page_more == 0, (name, page)                  # the page counts nothing
        assert card_names == page_names[:len(card_names)], (name, page, top)
        assert card_more == len(page_names) - len(card_names), (name, page, top)
        assert card_more != 1, (name, top)    # a lone corner is named: "C6", never "1 more"
        if heading == TIE:
            assert top.reason == "", (name, top.reason)   # no one corner's reason under a tie
        else:                                             # ...and the single lead is unchanged
            assert top.tied_low_s is None, top
            assert top.reason == coaching.reason_sentence(opps.rows[0], "kmh", reach=False), top
    # A span whose two ends round to one figure says so, rather than "+0.15 to +0.15 s".
    level = share_card.TopOpp("C3 · C12", 0.148, "", tied_low_s=0.146)
    assert share_card.opp_loss(level) == "+0.15 s each", share_card.opp_loss(level)
    print("test_a_tie_the_page_will_not_rank_is_not_crowned_on_the_card OK")


def _row_ink(img: QImage, y0: int, y1: int, colour: str) -> list[int]:
    """The x of every pixel in rows [y0, y1) painted exactly `colour` (glyph cores, not their
    antialiased edges)."""
    rgb = QColor(colour).rgb()
    return [x for x in range(share_card.CARD_W) for y in range(y0, y1)
            if img.pixel(x, y) == rgb]


def test_the_tie_block_draws_no_reason_and_keeps_its_names_clear_of_the_loss():
    """Rendered: a tie's block has its heading, its corners and its span, and NOTHING on the reason
    row (the single-lead card of the same lead does ink it — the control); and a tie's corner line,
    sized beside a right-aligned span, never runs into it — swept over the widest a real track makes
    (12 corners, the card's cap, the widest span) and a pathological 99-corner, 99-more line."""
    import dataclasses

    from PySide6.QtGui import QPainter

    tie = share_card.card_data(_working_set_session("MK_18_09_26"), unit="kmh")
    lead = share_card.card_data(_working_set_session("SD_19_09_26"), unit="kmh")
    # No map: the plate is MAP_PLATE_H_MAX tall, so the block's rows sit at fixed y.
    opp_top = 512 + share_card.MAP_PLATE_H_MAX + 56
    reason_band = (opp_top + 80, opp_top + 118)
    tie_img = share_card.render_card(tie, None, palette=theme.PALETTE_STANDARD)
    lead_img = share_card.render_card(lead, None, palette=theme.PALETTE_STANDARD)
    assert _row_ink(lead_img, *reason_band, theme.C.text_dim), "control: the lead has a reason"
    assert not _row_ink(tie_img, *reason_band, theme.C.text_dim), "a tie drew a reason line"

    pad, right = 72, share_card.CARD_W - 72
    img = QImage(share_card.CARD_W, share_card.CARD_H, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        widest = []
        for label in ("C12 · C10 · C11 · 9 more", "C99 · C99 · C99 · 99 more", "C5 · C2 · C8",
                      "C12 · C10 · C11 · C9"):   # ...and the widest tie with its lone 4th named
            for hi, lo in ((9.99, 9.98), (0.13, 0.06), (0.15, 0.15)):
                opp = share_card.TopOpp(label, hi, "", tied_low_s=lo)
                p.setFont(share_card._font(46, theme.W_SEMIBOLD))
                loss_w = p.fontMetrics().horizontalAdvance(share_card.opp_loss(opp))
                avail = right - pad - loss_w - share_card._OPP_GAP
                txt, font = share_card._fit_line(p, label, avail, share_card._OPP_PX_STEPS,
                                                 theme.W_SEMIBOLD)
                p.setFont(font)
                w = p.fontMetrics().horizontalAdvance(txt)
                assert w <= avail, (label, share_card.opp_loss(opp), w, avail)
                widest.append((pad + w, font.pixelSize(), txt))
        # Every real form draws WHOLE at the block's own 46 px; only the pathological one shrinks.
        for _right_x, px, txt in widest:
            assert "…" not in txt, txt
            assert px == 46 or txt.startswith("C99"), (txt, px)
    finally:
        p.end()
    # ...and in pixels, on the card itself: the corners' ink ends left of the span's.
    for label in ("C12 · C10 · C11 · 9 more", "C99 · C99 · C99 · 99 more"):
        data = dataclasses.replace(tie, top_opp=share_card.TopOpp(label, 9.99, "",
                                                                  tied_low_s=9.98))
        img = share_card.render_card(data, None, palette=theme.PALETTE_STANDARD)
        names = _row_ink(img, opp_top + 20, opp_top + 64, theme.C.text)
        span = _row_ink(img, opp_top + 20, opp_top + 64, theme.behind_colour())
        assert names and span and max(names) < min(span), (label, max(names), min(span))
        bg = img.pixel(5, 5)
        assert not [x for x in range(right + 1, share_card.CARD_W)
                    if any(img.pixel(x, y) != bg for y in range(opp_top, opp_top + 120))], label
    print(f"test_the_tie_block_draws_no_reason_and_keeps_its_names_clear_of_the_loss OK "
          f"(widest corner line ends at x={max(r for r, *_ in widest)} of {right})")


# --------------------------------------------------------------------- hero Δ-to-ideal copy
def test_hero_delta_line_reads_cleanly_on_both_branches():
    """The Δ-to-ideal hero line reads like a shipped product on BOTH branches. A positive gap keeps
    the "+0.31 s vs your ideal lap" voice; a gap AT the envelope (≈ 0) reads plainly as "level with
    your ideal lap" — NEVER the old doubled "on your ideal lap vs your ideal lap" template bug."""
    pos = share_card.hero_delta_line(0.31)
    assert pos == "+0.31 s vs your ideal lap", pos
    # right on the envelope (and safely inside the even-epsilon): the clean even copy
    even = share_card.hero_delta_line(0.0)
    assert even == "level with your ideal lap", even
    # the garbled doubled label must be gone from the even branch
    assert even.count("vs your ideal lap") == 0
    assert "on your ideal lap vs" not in even
    # a hair below the even-epsilon still reads as level (not a spurious "+0.00 s")
    assert share_card.hero_delta_line(theme.DELTA_EVEN_EPS_S) == "level with your ideal lap"
    # just above the epsilon flips to the positive voice
    just_over = share_card.hero_delta_line(theme.DELTA_EVEN_EPS_S + 0.01)
    assert just_over.startswith("+") and just_over.endswith("vs your ideal lap"), just_over
    print("test_hero_delta_line_reads_cleanly_on_both_branches OK")


def test_even_ideal_card_renders_with_the_clean_copy():
    """A best lap sitting ON the ideal envelope (gap ≈ 0) still renders a valid card, and its hero
    line is the clean even copy — the whole reason for the fix (rendering the real card surfaced
    the garbled string)."""
    d = share_card.card_data(FakeSession(best_time=67.90, ideal=67.90), unit="kmh")
    assert d.delta_to_ideal_s == 0.0, d.delta_to_ideal_s
    assert share_card.hero_delta_line(d.delta_to_ideal_s) == "level with your ideal lap"
    img = share_card.render_card(d, None, palette=theme.PALETTE_STANDARD)
    assert not img.isNull() and img.width() == share_card.CARD_W
    print("test_even_ideal_card_renders_with_the_clean_copy OK")


def test_single_donor_session_withholds_the_ideal_block():
    """When ONE lap is quickest through every corner and every straight, the ideal IS that lap —
    so the card states no gap at all rather than printing "level with your ideal lap" over a
    session with one usable lap.

    That copy used to be the ONLY thing this card could print: the old ideal was the pointwise
    minimum of the laps' cumulative-elapsed curves, which at the flag is the best lap time on every
    recording, so the gap was structurally 0. It is now a real state with a real cause, and the
    house rule for a synthesized value that has collapsed onto a real one is to hide it (see
    export_data.laps_summary). The card still renders — only the Δ block is absent."""
    degenerate = FakeSession(best_time=48.98, ideal=48.98, ideal_donor=3)
    d = share_card.card_data(degenerate, unit="kmh")
    assert d.delta_to_ideal_s is None, d.delta_to_ideal_s
    assert not d.blocked, "a one-donor session is still a shareable best lap"
    assert d.best_time == "0:48.980", d.best_time
    img = share_card.render_card(d, None, palette=theme.PALETTE_STANDARD)
    assert not img.isNull() and img.width() == share_card.CARD_W
    # ...and a session with the SAME numbers but more than one donor does print the gap, so the
    # withholding is keyed on the donor and not on the times being equal.
    stitched = share_card.card_data(FakeSession(best_time=48.98, ideal=47.93), unit="kmh")
    assert stitched.delta_to_ideal_s is not None
    assert share_card.hero_delta_line(stitched.delta_to_ideal_s) == "+1.05 s vs your ideal lap"
    print("test_single_donor_session_withholds_the_ideal_block OK")


def test_ideal_sublabel_fits_the_card_content_box_unelided():
    """The Δ-to-ideal sublabel is drawn with no fit and no elide at 24 px from the card's own
    `pad`, so its width is a hard constraint, not a preference. It names what the ideal is, and
    that wording changed with the maths (it used to say "best-at-each-point", which described the
    deleted envelope), so the width is pinned here rather than eyeballed once.

    IT IS A FUNCTION NOW, so the width is pinned on the WIDEST FORM IT CAN PRINT, not on the one
    today's fixture happens to produce: §5.4 put the sample size into the line ("over 24 laps"), and
    a lap count is 1 to 3 digits. Measuring only the shipped string is how a pinned width rots the
    first time the input grows — the exact failure mode this card's own "fits exactly" comments
    were caught in once."""
    from PySide6.QtGui import QFontMetrics
    box = share_card.CARD_W - 2 * 72     # the card's content width (pad = 72 in render_card)
    fm = QFontMetrics(share_card._font(24))
    widths = {n: fm.horizontalAdvance(share_card.ideal_sublabel(n))
              for n in (None, 1, 24, 999)}
    for n, w in widths.items():
        assert w <= box, f"{share_card.ideal_sublabel(n)!r} is {w} px in a {box} px content box"
    # The counted forms must actually be the wider ones — a guard that the count is IN the string.
    assert widths[999] > widths[None], widths
    # It must still say what kind of thing this is: synthesized from pieces, not a lap driven...
    for n in (None, 24):
        lower = share_card.ideal_sublabel(n).lower()
        assert "not a single drivable lap" in lower, lower
        assert "corners" in lower and "straights" in lower, lower
    # ...and, when there is a count, WHAT IT WAS MINIMISED OVER. The card is the app's most public
    # and least hoverable surface; it was the one printing the ideal with no sample (§5.4).
    assert "over 24 laps" in share_card.ideal_sublabel(24), share_card.ideal_sublabel(24)
    # None is the no-sample fallback: the un-counted line, never the string "None".
    assert "None" not in share_card.ideal_sublabel(None), share_card.ideal_sublabel(None)
    print("test_ideal_sublabel_fits_the_card_content_box_unelided OK "
          f"({widths[None]} / {widths[24]} / {widths[999]} px in a {box} px box)")


# --------------------------------------------------------------------- render-layer tests
def _one_px_png() -> bytes:
    """A tiny real PNG to stand in for the grabbed MapView thumbnail."""
    img = QImage(8, 8, QImage.Format_ARGB32)
    img.fill(0xFF334455)
    return share_card.card_to_png(img)


def test_render_card_is_a_nonempty_image_of_the_right_size():
    """render_card produces a non-null QImage of exactly CARD_W×CARD_H, and card_to_png encodes it
    to non-empty PNG bytes — with a map thumbnail composited in."""
    d = share_card.card_data(FakeSession(), unit="kmh")
    img = share_card.render_card(d, _one_px_png(), palette=theme.PALETTE_STANDARD)
    assert not img.isNull()
    assert img.width() == share_card.CARD_W and img.height() == share_card.CARD_H
    png = share_card.card_to_png(img)
    assert len(png) > 1000 and png[:8] == b"\x89PNG\r\n\x1a\n", len(png)
    print("test_render_card_is_a_nonempty_image_of_the_right_size OK")


def test_render_card_without_thumbnail_and_on_both_palettes():
    """The card renders cleanly with no map thumbnail (map_png=None) and on the colour-blind
    palette (which recolours the semantic hues) — and restores the previously-active palette."""
    theme.set_palette(theme.PALETTE_STANDARD)
    d = share_card.card_data(FakeSession(), unit="kmh")
    img = share_card.render_card(d, None, palette=theme.PALETTE_COLORBLIND)
    assert not img.isNull()
    assert img.width() == share_card.CARD_W and img.height() == share_card.CARD_H
    # render_card must restore the caller's active palette (it only swaps for the render).
    assert theme.active_palette() == theme.PALETTE_STANDARD, theme.active_palette()
    print("test_render_card_without_thumbnail_and_on_both_palettes OK")


def test_render_card_stamped_and_degraded_still_renders():
    """A stamped (degraded) card still renders to a valid image (the stamp is drawn, not blocked)."""
    d = share_card.card_data(FakeSession(degraded=True), unit="kmh")
    assert d.stamp == "estimated timing"
    img = share_card.render_card(d, None, palette=theme.PALETTE_STANDARD)
    assert not img.isNull() and img.width() == share_card.CARD_W
    print("test_render_card_stamped_and_degraded_still_renders OK")


# --------------------------------------------------------- M10: long-title elision + no collision
def _title_fit(text: str, avail: int):
    """Run share_card._fit_title against a real QPainter on a card-sized image (fontMetrics need a
    live paint device). Returns (fitted_text, font_pixel_size)."""
    from PySide6.QtGui import QPainter
    img = QImage(share_card.CARD_W, share_card.CARD_H, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        txt, font = share_card._fit_title(p, text, avail)
        return txt, font.pixelSize()
    finally:
        p.end()


def test_title_fit_shrinks_then_elides_long_names_keeps_short_unchanged():
    """M10: the flagship short name renders whole at the biggest step; a very long user-typed name
    (‘_save_as_track’ accepts any length) is shrunk a step or two and then elided-right so it can
    never smear off the header / into the stamp. The fit must never exceed the available width."""
    biggest, smallest = share_card._TITLE_PX_STEPS[0], share_card._TITLE_PX_STEPS[-1]
    # Short flagship name: whole, at the biggest size, untouched.
    txt, px = _title_fit("Daytona Milton Keynes", avail=800)
    assert txt == "Daytona Milton Keynes" and px == biggest, (txt, px)
    # A pathologically long name into a NARROW column: shrunk to the smallest step and elided.
    long_name = "Silverstone International Circuit Grand Prix Layout"
    txt, px = _title_fit(long_name, avail=560)
    assert px == smallest, px
    assert txt != long_name and txt.endswith("…"), txt   # elided, not the full string
    assert txt != "" and long_name.startswith(txt.rstrip("…").rstrip()[:6])  # keeps the leading name
    print(f"M10 title fit: short stays {biggest}px whole; long -> {px}px elided '{txt}'")


def _reason_fit(text: str, avail: int = share_card.CARD_W - 2 * 72):
    """`_fit_line` against the reason's own steps on a real paint device. (fitted_text, px, width)"""
    from PySide6.QtGui import QPainter
    img = QImage(share_card.CARD_W, share_card.CARD_H, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        txt, font = share_card._fit_line(p, text, avail, share_card._REASON_PX_STEPS)
        p.setFont(font)
        return txt, font.pixelSize(), p.fontMetrics().horizontalAdvance(txt)
    finally:
        p.end()


# The app's own longest opportunity sentences, produced by the REAL formatter rather than typed out
# — a phrasing change in studio/coaching.py must reach this guard. The braking lever plus the
# "carries to" consequence clause is the widest combination the model can emit, and since ADV-4 the
# spread row's IQR clause (at 9.99 s, the widest the 2-dp format prints below ten seconds) with the
# apex third's clause is the widest consistency sentence — drawn as the card draws it, the lever
# alone. Since COACHING-5 the measured line's two speeds (two digits each, in both units) with the
# entry third's consequence clause is the widest line sentence.
def _longest_real_reasons():
    long_brake = coaching.Opportunity(
        cid=1, direction=1, time_lost=0.9, entry_dist=100.0,
        reason=coaching.Reason(kind=coaching.REASON_BRAKING, contribution=0.4,
                               apex_speed_deficit=0.0, brake_extra_s=0.50, coast_extra_s=0.0,
                               sigma=0.1),
        phases=coaching.PhaseLoss(entry=0.05, apex=0.40, exit=0.05))
    long_coast = coaching.Opportunity(
        cid=2, direction=-1, time_lost=0.9, entry_dist=100.0,
        reason=coaching.Reason(kind=coaching.REASON_COASTING, contribution=0.4,
                               apex_speed_deficit=0.0, brake_extra_s=0.0, coast_extra_s=1.25,
                               sigma=0.1),
        phases=coaching.PhaseLoss(entry=0.40, apex=0.05, exit=0.05))
    long_spread = coaching.Opportunity(
        cid=3, direction=1, time_lost=5.5, entry_dist=100.0,
        reason=coaching.Reason(kind=coaching.REASON_CONSISTENCY, contribution=0.4,
                               apex_speed_deficit=0.0, brake_extra_s=0.0, coast_extra_s=0.0,
                               sigma=12.5),
        phases=coaching.PhaseLoss(entry=0.05, apex=0.40, exit=0.05),
        evidence=coaching.Evidence(n_laps=62, reach_laps=4, reach=coaching.REACH_RARE, iqr=9.99,
                                   abstain=coaching.ABSTAIN_NONE))
    spread = coaching.reason_sentence(long_spread, reach=False)
    assert spread == "consistency: middle half of laps within 9.99 s — most of it on the apex", spread
    long_line = coaching.Opportunity(
        cid=4, direction=-1, time_lost=0.9, entry_dist=100.0,
        reason=coaching.Reason(kind=coaching.REASON_LINE, contribution=0.4,
                               apex_speed_deficit=0.0, brake_extra_s=0.0, coast_extra_s=0.0,
                               sigma=0.1, apex_speed_gain=-99.94, exit_speed_gain=99.94),
        phases=coaching.PhaseLoss(entry=0.40, apex=0.05, exit=0.05))
    line = coaching.reason_sentence(long_line, reach=False)
    assert line == ("take your best lap's line (its apex −99.9, exit +99.9 km/h), and it "
                    "carries to entry"), line
    return [coaching.reason_sentence(o, u) for o in (long_brake, long_coast, long_line)
            for u in ("kmh", "mph")] + [spread]


def test_the_coaching_reason_is_fitted_like_every_other_line_on_the_card():
    """SW1-01: the reason was the ONE text draw on the card with neither an `align_right_at` nor a
    fit — an unbounded drawText from x=72 on a 1080 px canvas whose content box ends at 1008.

    Measured on three real recordings, at the shipped 30 px: the widest sentence the model emits is
    1086 px, i.e. 150 px past the content box and 78 px past the IMAGE, cut mid-word with no
    ellipsis — and on two of the three fixtures the overflowing sentence is the rank-0 one the card
    actually draws. This is the artifact a user SENDS to someone.

    Swept rather than sampled, one character at a time, because the failure is a threshold: for
    every prefix of every sentence the model can emit, the fitted line must sit inside the box."""
    avail = share_card.CARD_W - 2 * 72
    worst = ("", 0)
    for sentence in _longest_real_reasons():
        for n in range(1, len(sentence) + 1):
            txt, _px, width = _reason_fit(sentence[:n])
            assert width <= avail, (
                f"'{sentence[:n]}' fitted to {width} px in a {avail} px box as '{txt}'")
            if width > worst[1]:
                worst = (txt, width)
    # ...and the real sentences are still WHOLE — the fix is a fit, not a silent truncation.
    for sentence in _longest_real_reasons():
        txt, px, width = _reason_fit(sentence)
        assert txt == sentence, f"the shipped sentence was elided at {px}px: {txt!r}"
        assert px in share_card._REASON_PX_STEPS, px
    # A pathological string still elides rather than smearing off the edge.
    txt, px, width = _reason_fit("x" * 400)
    assert txt.endswith("…") and width <= avail, (txt[-8:], width)
    assert px == share_card._REASON_PX_STEPS[-1], px
    print(f"test_the_coaching_reason_is_fitted_like_every_other_line_on_the_card OK "
          f"(worst fitted line {worst[1]} px in {avail})")


def test_the_card_of_a_new_pb_says_so_and_keeps_its_reason_whole():
    """QA JOURNEY-7 (2026-09-26). On SD_19_09's first open the debrief said "New personal best at
    Sandown Park: 0:46.808, 0.10 s faster than your previous best (0:46.912)", and the lap card of
    that very lap said nothing of it; its reason line ended "4 of 36 laps match…", cut mid-word,
    because the swept guard above only ever met sentences with no reach count (an Opportunity built
    with no per-lap times). The mark is the load's PB verdict, a BEAT, of THIS lap only; the reason
    is the lever alone, the count being the Coaching row's to state."""
    # 4 of 36 laps at the best lap's time; the middle half spans 0.20 s (5.05 → 5.25), SD19 C1's
    times = [5.0] * 4 + [5.05] * 6 + [5.15] * 14 + [5.25] * 12
    c1 = coaching.Opportunity(
        cid=1, direction=1, time_lost=0.147, entry_dist=100.0,
        reason=coaching.Reason(kind=coaching.REASON_CONSISTENCY, contribution=0.1,
                               apex_speed_deficit=0.0,
                               brake_extra_s=0.0, coast_extra_s=0.0, sigma=0.46),
        phases=coaching.PhaseLoss(entry=0.02, apex=0.02, exit=0.07),
        evidence=coaching.corner_evidence(times, 5.0, 0.147))
    assert "4 of 36 laps matched" in coaching.reason_sentence(c1), "the fixture has its count"
    session = FakeSession(track="Sandown Park", best_time=46.808, ideal=46.196, opps=(
        coaching.Opportunities(enough=True, n_laps=36, median_lap_id=3, rows=[c1])))
    reason = share_card.card_data(session, unit="kmh").top_opp.reason
    assert reason == "consistency: middle half of laps within 0.20 s — most of it on exit", \
        reason
    txt, px, _width = _reason_fit(reason)
    assert txt == reason and px in share_card._REASON_PX_STEPS, (txt, px)
    beat = {"kind": "beat", "track": "Sandown Park", "best": 46.808, "prior": 46.912,
            "improvement": 0.104}
    try:
        d = share_card.card_data(session, unit="kmh", pb_standing=beat, prior_date="2026-08-30")
    except TypeError as exc:                             # a tree with no PB mark at all
        raise AssertionError(f"the card takes no PB verdict: {exc}") from exc
    assert d.pb == "NEW PB · −0.10 s vs 30 Aug", d.pb
    assert share_card.card_data(session, unit="kmh", pb_standing=beat).pb == \
        "NEW PB · −0.10 s"
    for other in (None, {**beat, "kind": "first"}, {**beat, "kind": "behind", "gap": 0.2},
                  {**beat, "best": 46.9}):               # ...and the last: another lap's verdict
        assert share_card.card_data(session, unit="kmh", pb_standing=other).pb == "", other
    # DOMAIN-6: a beat inside timing precision is LEVEL on the debrief, so the card claims no PB —
    # it said "NEW PB · 0.00 s" for a 0.004 s beat. The floor itself still earns the mark.
    level = {**beat, "prior": 46.812, "improvement": 0.004}
    assert share_card.card_data(session, unit="kmh", pb_standing=level,
                                prior_date="2026-08-30").pb == "", "a sub-floor beat is no PB"
    floor = {**beat, "prior": 46.878, "improvement": 0.07}
    assert share_card.pb_mark(floor, 46.808, "2026-08-30") == "NEW PB · −0.07 s vs 30 Aug"
    # Drawn: the ahead hue's ink on the BEST LAP row, right of the label, only with the mark.
    ahead = QColor(theme.ahead_colour())

    def _ink(data):
        img = share_card.render_card(data)
        return sum(1 for x in range(560, 1008, 2) for y in range(212, 244, 2)
                   if QColor(img.pixel(x, y)).rgb() == ahead.rgb())
    assert _ink(d) > 20 and _ink(share_card.card_data(session, unit="kmh")) == 0, "not drawn"
    print(f"test_the_card_of_a_new_pb_says_so_and_keeps_its_reason_whole OK ({d.pb!r})")


def test_no_card_ink_reaches_the_edge_of_the_image():
    """The same finding, read from the RENDERED PIXELS rather than from the fit — the defect was
    visible as ink in the last 4 pixel COLUMNS of the PNG. Everything the card draws lives inside
    [pad, CARD_W - pad]; nothing may cross that on any sentence the model can emit.

    `CARD_W - pad` itself is INSIDE the box, not outside it: the map plate's rounded rect is drawn
    to exactly that x, so its 1 px border legitimately inks that column on every card. The
    assertion is about everything to the RIGHT of the content edge."""
    import dataclasses

    edge = share_card.CARD_W - 72
    base = share_card.card_data(FakeSession(), unit="kmh")
    assert base.top_opp is not None
    for sentence in [*_longest_real_reasons(), "x" * 400]:
        data = dataclasses.replace(
            base, top_opp=dataclasses.replace(base.top_opp, reason=sentence))
        img = share_card.render_card(data, _one_px_png(), palette=theme.PALETTE_STANDARD)
        bg = img.pixel(5, 5)
        spill = [x for x in range(edge + 1, share_card.CARD_W)
                 if any(img.pixel(x, y) != bg for y in range(share_card.CARD_H))]
        assert not spill, f"ink in columns {spill[:6]}… of the PNG for {sentence[:40]!r}"
    print("test_no_card_ink_reaches_the_edge_of_the_image OK "
          f"(0 ink columns > {edge} on {len(_longest_real_reasons()) + 1} cards)")


def test_title_does_not_collide_with_the_stamp_on_a_degraded_long_name():
    """M10 (render): a long track name on a DEGRADED (stamped) session must fit in the width LEFT of
    the amber '(est)' stamp — the two share the header band. We measure the fitted title at its
    chosen size and assert its right edge clears the stamp's left edge with the reserved gap."""
    from PySide6.QtGui import QPainter
    long_name = "Silverstone International Circuit Grand Prix Layout"
    d = share_card.card_data(FakeSession(track=long_name, degraded=True), unit="kmh")
    assert d.stamp == "estimated timing"
    # Reconstruct the header geometry the render uses (pad=72, right=CARD_W-72).
    pad, right = 72, share_card.CARD_W - 72
    img = QImage(share_card.CARD_W, share_card.CARD_H, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        stamp_txt = f"{d.stamp} {theme.ESTIMATED_MARK}"
        p.setFont(share_card._font(26, theme.W_SEMIBOLD))
        stamp_w = p.fontMetrics().horizontalAdvance(stamp_txt)
        stamp_left = right - stamp_w
        avail = (right - (stamp_w + 24)) - pad
        txt, font = share_card._fit_title(p, long_name, avail)
        p.setFont(font)
        title_right = pad + p.fontMetrics().horizontalAdvance(txt)
        assert title_right <= stamp_left, (title_right, stamp_left)   # no smear into the stamp
    finally:
        p.end()
    # And the whole card still renders (with a map thumbnail) without error.
    img2 = share_card.render_card(d, _one_px_png(), palette=theme.PALETTE_STANDARD)
    assert not img2.isNull() and img2.width() == share_card.CARD_W
    print("M10 render: long degraded title elided, clears the (est) stamp")


# --------------------------------------------------------- L5: map plate hugs the thumbnail aspect
def _landscape_png(w: int, h: int) -> bytes:
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0xFF204060)
    return share_card.card_to_png(img)


def test_map_plate_height_hugs_the_thumbnail_aspect():
    """L5: the map plate height follows the thumbnail's scaled-to-width height, so a WIDE landscape
    grab yields a SHORT plate (no ~40% dead band) while a near-square grab keeps the tall plate —
    always within [MIN, MAX]. Pure geometry (map_plate_height)."""
    lo, hi = share_card.MAP_PLATE_H_MIN, share_card.MAP_PLATE_H_MAX
    # A wide 16:6 landscape map (the real MapView pane shape) -> a plate well short of the 512 max.
    wide = share_card.map_plate_height(1600, 600)
    assert lo <= wide < hi, wide
    # A very wide/thin grab floors at MIN (never a sliver plate).
    assert share_card.map_plate_height(2000, 200) == lo
    # A near-square grab fills the full-height plate (clamped at MAX).
    assert share_card.map_plate_height(900, 900) == hi
    # Degenerate sizes fall back to the max plate (never a zero/negative rect).
    assert share_card.map_plate_height(0, 0) == hi
    # A wider map -> a shorter plate (monotone in aspect): more landscape, less dead space.
    assert share_card.map_plate_height(1800, 600) < share_card.map_plate_height(1200, 600)
    print(f"L5 map plate: wide 16:6 -> {wide}px (was fixed {hi}px); square -> {hi}px, floored {lo}px")


def test_render_card_with_a_wide_landscape_thumbnail():
    """L5 (render): a card built with a wide landscape map thumbnail renders fine end-to-end, and the
    opportunity block below the (now shorter) plate stays on-canvas (never pushed off the card)."""
    d = share_card.card_data(FakeSession(), unit="kmh")
    img = share_card.render_card(d, _landscape_png(1600, 600), palette=theme.PALETTE_STANDARD)
    assert not img.isNull() and img.height() == share_card.CARD_H
    print("L5 render: wide landscape thumbnail composites without dead-band letterboxing")


# ---------------------------------------------------------- clean map grab (legend off the card)
def _map_view_session():
    """A bare Session with just enough surface for MapView.__init__ (trace arrays + a start-line
    Seg via the real ``laps.sectors`` shape + the best-lap/reference hooks) — the test_map_ghost
    idiom, trimmed. No pacer, no file. ``start_line``/``sector_lines`` stay the real Session
    properties (they read ``laps.sectors`` through ``Seg.from_pacer``)."""
    import numpy as np

    from studio.session import Session
    s = Session.__new__(Session)
    ang = np.linspace(0.0, 2 * np.pi, 60)
    s.tt = np.linspace(0.0, 6.0, 60)
    s.tx = np.cos(ang) * 50.0
    s.ty = np.sin(ang) * 30.0
    s.tv = np.linspace(40.0, 120.0, 60)
    line = SimpleNamespace(first=SimpleNamespace(x=-60.0, y=0.0),
                           second=SimpleNamespace(x=-40.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    s._valid_cache = [1]  # one valid lap → the empty-state placeholder stays hidden
    s.reference_overlay_xy = lambda: None
    s.reference_label = lambda: None
    s.best_lap_id = lambda: None
    s.nearest_index = lambda x, y: None
    return s


def test_map_view_grab_clean_hides_the_map_key_legend():
    """MapView.grab_clean() hides the dev 'Map key' legend overlay for the duration of a grab (so it
    never lands on the shareable card) and RESTORES it afterwards — the live map keeps its key. The
    speed rainbow mode (the card's signature visual) is untouched by the clean grab."""
    from studio.map_view import MapView
    mv = MapView(_map_view_session())
    key = mv._map_key
    key.show()
    # isHidden() is the explicit hide flag (isVisible() reads False off-screen because the top-level
    # window isn't shown — the empty-state idiom), so assert on isHidden throughout.
    assert not key.isHidden(), "precondition: the map key is shown on the live map"
    seen = {}
    with mv.grab_clean():
        seen["key_hidden_during_grab"] = key.isHidden()
        # the clean grab must not disturb the speed colouring the card leads with
        seen["rainbow_mode"] = mv._rainbow_mode
    assert seen["key_hidden_during_grab"] is True, "the map key must be hidden during a clean grab"
    assert seen["rainbow_mode"] == "speed", "clean grab must preserve the speed rainbow"
    assert not key.isHidden(), "the map key must be restored after the clean grab"
    print("test_map_view_grab_clean_hides_the_map_key_legend OK")


def test_map_view_grab_clean_hides_the_marker_and_start_line_handles():
    """H2 regression guard: grab_clean() must ALSO hide the app's editing chrome that used to burn
    into the shareable card — the coral video-position ``marker`` and every timing line's segment +
    drag handles (start line here; sectors iterate the same way) — for the duration of the grab, and
    restore each item's prior visibility on exit. Otherwise the amber "+" crosshairs + coral marker
    circle land on the social brag image (pixel-confirmed on the flagship D24 card)."""
    from studio.map_view import MapView
    mv = MapView(_map_view_session())
    # The pyqtgraph plot items that must vanish for a clean grab (marker + start-line line/handles).
    marker = mv.marker
    start = mv._start
    chrome = [marker, start.line, start.h1, start.h2]
    for it in chrome:
        it.setVisible(True)
        assert it.isVisible(), "precondition: the editing chrome is shown on the live map"
    during = {}
    with mv.grab_clean():
        during["marker"] = marker.isVisible()
        during["line"] = start.line.isVisible()
        during["h1"] = start.h1.isVisible()
        during["h2"] = start.h2.isVisible()
    assert during == {"marker": False, "line": False, "h1": False, "h2": False}, during
    # restored on exit (the live map keeps its marker + draggable start line)
    for it in chrome:
        assert it.isVisible(), "the editing chrome must be restored after the clean grab"
    print("test_map_view_grab_clean_hides_the_marker_and_start_line_handles OK")


# --------------------------------------------------------------------- app-wiring tests (DI)
def _bare_window(session):
    """A StudioWindow with just enough state for the share-card actions (no heavy __init__): the
    session, a view whose .map is a real grab-able QWidget, the display unit, and a captured
    statusbar. QMainWindow.__init__ so statusBar()/QMessageBox parenting work."""
    from PySide6.QtWidgets import QMainWindow

    from studio.app import StudioWindow
    w = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(w)
    w.session = session
    w._speed_unit = "kmh"
    w._paths = ["/Users/x/Desktop/D24/GX010060.MP4"]
    map_widget = QWidget()
    map_widget.resize(80, 60)
    w.view = SimpleNamespace(map=map_widget)
    # The export cluster lives on its own controller (§7.1) and the real __init__ builds it; this
    # fixture skips __init__, so it has to attach one explicitly.
    from studio.app import STATUS_MS
    from studio.export_controller import ExportController
    w.exports = ExportController(w, STATUS_MS)
    return w


def test_export_share_card_saves_png_through_the_dialog():
    """File ▸ Export ▸ 'Lap card (image)…' renders the card and writes a real PNG to the path the
    (monkeypatched) save dialog returns."""
    from PySide6.QtWidgets import QFileDialog
    w = _bare_window(FakeSession())
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "my_lap_card.png")
        orig = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, "PNG images (*.png)"))
        try:
            w.exports.export_share_card()
        finally:
            QFileDialog.getSaveFileName = orig
        assert os.path.exists(out), "the lap card PNG was not written"
        with open(out, "rb") as f:
            head = f.read(8)
        assert head == b"\x89PNG\r\n\x1a\n", head
    print("test_export_share_card_saves_png_through_the_dialog OK")


def test_export_share_card_cancel_writes_nothing():
    """Cancelling the save dialog (empty path) writes no file."""
    from PySide6.QtWidgets import QFileDialog
    w = _bare_window(FakeSession())
    written = []
    w._save_card_png = lambda img, path: written.append(path)  # spy — must not be called
    orig = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    try:
        w.exports.export_share_card()
    finally:
        QFileDialog.getSaveFileName = orig
    assert written == [], "a cancelled save must write nothing"
    print("test_export_share_card_cancel_writes_nothing OK")


def test_copy_share_card_sets_clipboard_image(monkeypatched=None):
    """'Copy lap card' renders the card and puts an image on the clipboard (monkeypatched), so a
    user can paste it straight into a chat."""
    from studio import app as app_mod
    w = _bare_window(FakeSession())
    captured = {}

    class FakeClip:
        def setImage(self, image):
            captured["image"] = image

    orig = app_mod.QApplication.clipboard
    app_mod.QApplication.clipboard = staticmethod(lambda: FakeClip())
    try:
        w._copy_share_card()
    finally:
        app_mod.QApplication.clipboard = orig
    assert "image" in captured, "no image was placed on the clipboard"
    assert isinstance(captured["image"], QImage)
    assert captured["image"].width() == share_card.CARD_W
    print("test_copy_share_card_sets_clipboard_image OK")


def test_blocked_session_builds_no_card_and_greys_actions():
    """A blocked (provisional) session: _build_share_card returns None, the save/copy actions do
    nothing, and _share_card_blocked reports True so _sync_export_menu greys them out."""
    w = _bare_window(FakeSession(track=None, verified=False))
    assert w._build_share_card() is None
    assert w._share_card_blocked() is True
    # copy on a blocked session must not touch the clipboard
    from studio import app as app_mod
    touched = []
    orig = app_mod.QApplication.clipboard
    app_mod.QApplication.clipboard = staticmethod(
        lambda: SimpleNamespace(setImage=lambda im: touched.append(im)))
    try:
        w._copy_share_card()
    finally:
        app_mod.QApplication.clipboard = orig
    assert touched == [], "a blocked session must not copy a card"
    print("test_blocked_session_builds_no_card_and_greys_actions OK")


class _TracedSession(FakeSession):
    """FakeSession with `lap_channels`: the best lap (3) an ellipse, every other lap a figure-eight
    far to the east — so a thumbnail that draws anything but the best lap's own samples is caught."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.asked = []

    def lap_channels(self, lap_id):
        import numpy as np
        self.asked.append(lap_id)
        a = np.linspace(0.0, 2 * np.pi, 240)
        if lap_id == self._best_id:
            x, y = 60.0 * np.cos(a), 40.0 * np.sin(a)
        else:
            x, y = 900.0 + 60.0 * np.sin(2 * a), 40.0 * np.sin(a)
        return {"t_telemetry_s": np.linspace(0.0, 60.0, len(a)), "x_m": x, "y_m": y,
                "speed_kmh": 40.0 + 30.0 * np.sin(a), "dist_m": np.linspace(0.0, 1000.0, len(a))}


def test_the_card_map_is_the_best_lap_drawn_from_data_not_a_grab_of_the_live_map():
    """EXP-3: the thumbnail was a grab of the live MapView, so three cards of one session, all
    "BEST LAP 1:07.479", carried three maps — brake triangles, corner stars, another lap's glyphs,
    stray GPS off the circuit — whatever the window showed. It is now the best lap's own trace on
    the speed ramp, drawn from data: the live map is never grabbed, only lap 3 is asked for, and
    the picture is the ellipse (ink on the ring, none at its centre or where the other laps are)."""
    import numpy as np

    grabs = []

    class _LiveMap(QWidget):
        def grab(self, *a):
            grabs.append(a)
            return super().grab(*a)

    w = _bare_window(_TracedSession())
    w.view = SimpleNamespace(map=_LiveMap())
    img = w._build_share_card()
    assert img is not None and img.width() == share_card.CARD_W
    assert grabs == [], "the card grabbed the live map"
    assert w.session.asked == [3], f"the card drew laps {w.session.asked}, not the best (3)"

    thumb = QImage.fromData(share_card.lap_map_png(w.session, 3, unit="kmh"))
    assert not thumb.isNull()
    # 120 x 80 m, fitted into the plate: the full 912 wide and the tallest plate allows.
    assert (thumb.width(), thumb.height()) == (share_card.MAP_PLATE_W - share_card.MAP_PLATE_INNER,
                                               share_card.MAP_PLATE_H_MAX
                                               - share_card.MAP_PLATE_INNER), thumb.size()
    thumb = thumb.convertToFormat(QImage.Format_RGBA8888)
    px = np.frombuffer(thumb.constBits(), np.uint8).reshape(thumb.height(), thumb.width(), 4)
    ink = px[..., 3] > 0
    h, wd = ink.shape
    assert not ink[h // 2 - 20:h // 2 + 20, wd // 2 - 20:wd // 2 + 20].any(), "ink inside the ring"
    ys, xs = np.nonzero(ink)
    # The ellipse is the height-limited fit (432 px for its 80 m, so 648 px for its 120 m),
    # centred — and nothing of the far-off laps widens it.
    assert abs((xs.max() - xs.min()) - 648) <= 10 and abs((ys.max() - ys.min()) - 432) <= 10, \
        (xs.min(), xs.max(), ys.min(), ys.max())
    assert abs((xs.min() + xs.max()) / 2 - wd / 2) <= 3, (xs.min(), xs.max())
    colours = {tuple(c) for c in px[ink][:, :3][px[ink][:, 3] == 255]}
    assert len(colours) >= 8, f"one flat colour, not the speed ramp: {len(colours)}"
    # A lap with no trace gives no thumbnail, and the card still renders without one.
    assert share_card.lap_map_png(FakeSession(), 3) is None
    print("test_the_card_map_is_the_best_lap_drawn_from_data_not_a_grab_of_the_live_map OK")


def test_pb_toast_share_button_routes_to_on_share():
    """The PBToast's 'Share your PB →' primary button routes to its injected on_share callback
    (the one-tap card save), and 'See your progress →' still routes to on_progress."""
    from studio.overlays import PBToast
    shared, progressed = [], []
    toast = PBToast("New personal best! 🏁", "MK — 1:08.42, 0.31 s faster.",
                    on_progress=lambda: progressed.append(True),
                    on_share=lambda: shared.append(True))
    assert toast.share_btn is not None
    assert "share" in toast.share_btn.text().lower()
    toast.share_btn.click()
    assert shared == [True], "the share button must route to on_share"
    # a fresh toast for the progress link (the first click dismissed the toast)
    toast2 = PBToast("New personal best! 🏁", "MK — 1:08.42.",
                     on_progress=lambda: progressed.append(True),
                     on_share=lambda: shared.append(True))
    toast2.link_btn.click()
    assert progressed == [True], "the progress link must still route to on_progress"
    print("test_pb_toast_share_button_routes_to_on_share OK")


def test_pb_toast_hides_share_button_when_no_callback():
    """With no on_share callback (a session that can't make a card), the toast shows no share
    button — only the progression link (backwards-compatible with the pre-share toast)."""
    from studio.overlays import PBToast
    toast = PBToast("New personal best!", "MK.", on_progress=lambda: None, on_share=None)
    assert toast.share_btn is None
    assert toast.link_btn is not None
    print("test_pb_toast_hides_share_button_when_no_callback OK")


if __name__ == "__main__":
    test_card_data_carries_the_expected_fields()
    test_card_data_unit_flips_reason_to_mph()
    test_card_data_unknown_track_and_no_ideal()
    test_card_data_blocks_provisional_and_no_lap()
    test_card_data_stamps_degraded_timing()
    test_card_data_no_opportunity_when_too_few_laps()
    test_card_data_survives_coaching_error()
    test_a_tie_the_page_will_not_rank_is_not_crowned_on_the_card()
    test_the_tie_block_draws_no_reason_and_keeps_its_names_clear_of_the_loss()
    test_hero_delta_line_reads_cleanly_on_both_branches()
    test_even_ideal_card_renders_with_the_clean_copy()
    test_single_donor_session_withholds_the_ideal_block()
    test_ideal_sublabel_fits_the_card_content_box_unelided()
    test_render_card_is_a_nonempty_image_of_the_right_size()
    test_render_card_without_thumbnail_and_on_both_palettes()
    test_render_card_stamped_and_degraded_still_renders()
    test_title_fit_shrinks_then_elides_long_names_keeps_short_unchanged()
    test_the_coaching_reason_is_fitted_like_every_other_line_on_the_card()
    test_the_card_of_a_new_pb_says_so_and_keeps_its_reason_whole()
    test_no_card_ink_reaches_the_edge_of_the_image()
    test_title_does_not_collide_with_the_stamp_on_a_degraded_long_name()
    test_map_plate_height_hugs_the_thumbnail_aspect()
    test_render_card_with_a_wide_landscape_thumbnail()
    test_map_view_grab_clean_hides_the_map_key_legend()
    test_map_view_grab_clean_hides_the_marker_and_start_line_handles()
    test_export_share_card_saves_png_through_the_dialog()
    test_export_share_card_cancel_writes_nothing()
    test_copy_share_card_sets_clipboard_image()
    test_blocked_session_builds_no_card_and_greys_actions()
    test_the_card_map_is_the_best_lap_drawn_from_data_not_a_grab_of_the_live_map()
    test_pb_toast_share_button_routes_to_on_share()
    test_pb_toast_hides_share_button_when_no_callback()
    print("\nAll shareable-lap-card tests passed.")
