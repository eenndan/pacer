"""Corner cards on the exported clip (`studio/overlay/corner_cards.py`), the second overlay element.

WHY A FILE OF ITS OWN. The cards are a new surface with their own contract: the numbers are the
Corners table's (never recomputed), a card is honest about timing precision and about which
corners were matched on track, it appears at the corner's exit for two seconds, it is rendered
once and blitted per frame, it never covers a Classic plate at any frame shape, and with the option
off the Classic export is untouched. test_export_ghost owns the seam itself; this file owns what
this element promises through it.

THE FIXTURE is test_export_ghost's: the demo's telemetry (`make_demo`'s seed, laps, origin and
planted C1 habit — a fictional circuit, verified timing on its built-in track, no personal data)
under a flat 960x540 picture. Renders go through the real `Renderer` with the real ffmpeg decode
and an encoder stand-in that keeps each painted frame as the render hands it over; the encoder is
pinned to libx264 with a software decode — the path CI and a Mac without VideoToolbox take.

WHAT IS CHECKED (each watched failing on a planted defect, see the PR):
  * the card's time is the Corners table's Δ cell, to the printed text; its speed is that table's
    Apex value at whole units — read off a real `CornerTable` on the same session;
  * the pre-rendered image of each card is pixel-identical to a reference render of the table's
    own strings;
  * |Δ| < 0.07 s reads "≈ best" in the neutral white (0.069 s), beyond it the signed time in the
    palette's gain/loss colour (0.071 s, both palettes); a cross-recording reference says "ref";
  * an unmatched corner gets no card, and a lap with none matched gets no element at all;
  * the best lap's cards carry the minimum speed only;
  * `prepare` renders each card exactly once and `paint` draws no text;
  * a real render: the card's pixels are in its rectangle one second after the exit, and the frames
    before the exit and after the hold are byte-identical to the Classic render;
  * option off (or nothing to show) = the Classic bytes;
  * placement inside the safe area and clear of every plate at 16:9, 9:16, 1:1 and 4K;
  * the dialog: "Corner cards" in the Extras row, off by default, remembered, disabled with its
    reason in the full-session scope.

Run: python tests/test_export_corner_cards.py
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("pacer-test-export-corner-cards-")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
)

_APP = QApplication.instance() or QApplication([])

from studio import chapters, overlay, prefs, theme, units  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.dev import make_demo as md  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.export_palette import EXPORT  # noqa: E402
from studio.lap_table import CornerTable  # noqa: E402
from studio.overlay import corner_cards as cc  # noqa: E402
from studio.session import Session  # noqa: E402

PIC_W, PIC_H = 960, 540
_FIX: dict = {}


def _flat_picture(path, truth, first_payload, n_payloads, ffmpeg):
    """`synth_gopro.generate(video=)`: a flat dark picture, 29.97 fps."""
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=0x1d2330:s={PIC_W}x{PIC_H}:r=30000/1001",
                    "-frames:v", str(n_payloads * sg.FRAMES_PER_PAYLOAD), "-c:v", "libx264",
                    "-preset", "ultrafast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                    "-x264-params", "keyint=300:min-keyint=300:scenecut=0",
                    "-video_track_timescale", "30000", "-an", "-n", path],
                   check=True, capture_output=True)


def _fixture() -> dict:
    if _FIX:
        return _FIX
    tmp = tempfile.TemporaryDirectory(prefix="pacer-export-cards-")
    rec = sg.generate(os.path.join(tmp.name, "rec"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                      origin=md.DEMO_ORIGIN, video=_flat_picture, habit=md.DEMO_HABIT)
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    assert s.timing_verified, "the demo circuit's timing is not verified on its built-in track"
    best = s.best_lap_id()
    # A habit lap: C1 is beyond precision there (the planted early braking), the rest near the best.
    lap = max((i for i in s.valid_lap_ids() if i != best),
              key=lambda i: abs(s.corners.lap_corner_stats(i)[0].delta))
    _FIX.update(tmp=tmp, src=rec.paths[0], s=s, best=best, lap=lap,
                out=os.path.join(tmp.name, "never-written.mp4"))
    return _FIX


def _cfg(**kw):
    return ev.OverlayConfig(encoder="libx264", hwaccel_decode=False, **kw)


def _spec(s, lap, **kw):
    return ev.build_lap_spec(s, _fixture()["out"], lap, config=_cfg(**kw))


def _element(painter):
    got = [e for e in painter.elements if e.name == cc.CornerCardsElement.name]
    return got[0] if got else None


class _Patched:
    """Temporarily replace attributes; restores on exit."""

    def __init__(self, *triples):
        self.triples, self.saved = triples, []

    def __enter__(self):
        for obj, name, value in self.triples:
            self.saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)
        return self

    def __exit__(self, *exc):
        for obj, name, value in reversed(self.saved):
            setattr(obj, name, value)


def _img_hash(img) -> str:
    ptr = img.constBits()
    return hashlib.sha256(bytes(ptr)[:img.sizeInBytes()]).hexdigest()


def _table_cells(s, lap, unit):
    """{cid: (Δ cell text, Apex cell text)} off a real Corners table showing `lap`."""
    table = CornerTable(s)
    table.set_speed_unit(unit)
    table.set_lap(lap)
    out = {}
    for r in range(table.table.rowCount()):
        cid = table._cids[r]
        out[cid] = (table.table.item(r, 2).text(), table.table.item(r, 3).text())
    table.deleteLater()
    return out


# ------------------------------------------------------------------------------ the numbers
def test_the_cards_say_what_the_corners_table_says():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    for unit in (units.DEFAULT_UNIT, "mph"):
        spec = _spec(s, lap, elements=("corner_cards",), speed_unit=unit)
        painter = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
        el = _element(painter)
        assert el is not None, "no corner cards prepared on a lap with matched corners"
        cells = _table_cells(s, lap, unit)
        resolved = s.corners.lap_corner_resolved(lap)
        stats = s.corners.lap_corner_stats(lap)
        matched = [st.cid for st, ok in zip(stats, resolved, strict=True) if ok]
        assert [c.cid for c in sorted(el.cards, key=lambda c: c.cid)] == matched
        coloured = approx = 0
        for card in el.cards:
            delta_cell, apex_cell = cells[card.cid]
            assert card.unit == units.speed_label(unit)
            # The speed: the table's Apex cell, the same string.
            assert card.speed == apex_cell, (card.speed, apex_cell)
            raw = next(st for st in stats if st.cid == card.cid).delta
            # Level or not is decided on the PRINTED Δ — the table's own cell.
            if abs(float(delta_cell.replace("−", "-"))) < 0.07:
                assert card.delta == "≈ best" and card.colour == EXPORT.neutral, (card, raw)
                approx += 1
            else:
                # The time: the table's Δbest cell, to the printed text.
                assert card.delta == f"{delta_cell} s vs best", (card.delta, delta_cell)
                assert card.colour == ev.export_delta_colour(raw, spec.config.palette)
                coloured += 1
        assert coloured >= 1 and approx >= 1, (coloured, approx)
        # The exit: the corner model's own entry time plus the corner's time.
        for card in el.cards:
            st = next(x for x in stats if x.cid == card.cid)
            assert abs(card.t_exit - (s.corners.corner_entry_media_time(lap, card.cid)
                                      + st.time)) < 1e-12
        spec.source.cleanup()
    print(f"ok the cards carry the Corners table's numbers ({len(el.cards)} cards, km/h and mph)")


def test_each_prepared_card_is_a_render_of_the_tables_strings():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    spec = _spec(s, lap, elements=("corner_cards",))
    painter = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
    el = _element(painter)
    cells = _table_cells(s, lap, units.DEFAULT_UNIT)
    h = painter._readout_rect.height()
    for card, img in zip(el.cards, el.images, strict=True):
        delta_cell, apex_cell = cells[card.cid]
        want = cc.Card(cid=card.cid, t_exit=card.t_exit, label=f"C{card.cid}",
                       speed=apex_cell, unit="km/h",
                       delta=card.delta if card.delta == "≈ best" else f"{delta_cell} s vs best",
                       colour=card.colour)
        ref = cc.render_card(want, cc.card_width(want, h), h)
        assert _img_hash(img) == _img_hash(ref), f"C{card.cid}: the prepared card is not the table's"
    spec.source.cleanup()
    print(f"ok {len(el.images)} pre-rendered cards hash-equal to renders of the table's strings")


def _with_deltas(s, lap, deltas):
    """`lap`'s stats with these per-corner deltas (and nothing else changed)."""
    real = s.corners.lap_corner_stats

    def fake(lap_id):
        got = real(lap_id)
        if lap_id != lap:
            return got
        return [replace(st, delta=d) for st, d in zip(got, deltas, strict=True)]
    return _Patched((s.corners, "lap_corner_stats", fake))


def _red(rgba):
    """The loss red (#FF4D4D) by hue — and not the amber "C1" (#FFB21E, g/r 0.70) or its halo
    blend, which a plain threshold catches. Ratios, because the card image is premultiplied."""
    r, g, b, a = (rgba[..., i].astype(float) for i in range(4))
    return (a > 0) & (r > 120) & (g < 0.45 * r) & (b < 0.45 * r)


def _arr(img):
    w, h = img.width(), img.height()
    return np.frombuffer(bytes(img.constBits())[:img.sizeInBytes()], np.uint8).reshape(
        h, img.bytesPerLine() // 4, 4)[:, :w, [2, 1, 0, 3]]


def test_inside_timing_precision_the_card_says_level_beyond_it_the_colour():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    n = len(s.corners.lap_corner_stats(lap))
    # Decided on the PRINTED value: 0.0649 prints "+0.06" (level); 0.0651, 0.0674 and 0.0699 all
    # print "+0.07" — the Corners table's cell in the loss colour — so the card must not say level.
    assert n >= 7, n
    deltas = [0.0649, 0.0651, 0.0674, 0.0699, -0.0649, -0.0651, 0.071] + [0.0] * (n - 7)
    with _with_deltas(s, lap, deltas), _Patched(
            (s.corners, "lap_corner_resolved", lambda _lap: [True] * n)):
        for palette in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
            got = {c.cid: c for c in cc.card_rows(s, lap, None, palette)}
            ahead, behind = ev.export_semantic_pair(palette)
            level, behind_, ahead_ = (("≈ best", EXPORT.neutral), ("+0.07 s vs best", behind),
                                      ("−0.07 s vs best", ahead))
            want = {1: level, 2: behind_, 3: behind_, 4: behind_, 5: level, 6: ahead_, 7: behind_}
            for cid, w in want.items():
                assert (got[cid].delta, got[cid].colour) == w, (cid, deltas[cid - 1], got[cid])
        # ...and on the burned pixels: the loss card carries red ink, the level one none.
        cards = {c.cid: c for c in cc.card_rows(s, lap, None, theme.PALETTE_STANDARD)}
        lvl, loss = (cc.render_card(cards[i], cc.card_width(cards[i], 43.2), 43.2) for i in (1, 2))
        assert _red(_arr(loss)).sum() > 40, "the loss card has no red ink"
        assert _red(_arr(lvl)).sum() == 0, "the level card has red ink"
    # A cross-recording reference is the Δ baseline: the card names it, as the table's "Δref".
    with _Patched((s, "has_reference", lambda: True), (s, "reference_is_own_recording", lambda: False)):
        got = cc.card_rows(s, lap, None, theme.PALETTE_STANDARD)
        assert got and all(c.delta is None or c.delta.endswith(" ref") for c in got), got
        assert not any("best" in (c.delta or "") for c in got), got
    print("ok ≈ best inside 0.07 s (neutral), signed + coloured beyond, both palettes; 'ref' with "
          "a reference")


def test_against_another_recording_the_card_says_level_under_the_reference_lap_step():
    """cool/reference-lap-corner-floor: with ANOTHER recording's lap as the Δ baseline the card's
    level step is the Corners table's Δref step (`stats.corner_delta_step(True)`), not timing
    precision — one lap's corner time against one lap of another recording reads that far apart
    with nothing changed. "≈ ref" in neutral under it (decided on the printed value), signed and
    coloured past it; a reference that is this recording's own keeps the timing step."""
    from studio import library, stats
    from studio._signal import fmt_signed
    f = _fixture()
    s, lap = f["s"], f["lap"]
    n = len(s.corners.lap_corner_stats(lap))
    step = stats.corner_delta_step(True)
    assert step > library.PB_PRECISION_S
    below = round(step - 0.0051, 4)                   # prints one hundredth under the step
    deltas = [0.08, -0.10, below, step + 0.001, -(step + 0.05)] + [0.0] * (n - 5)
    ahead, behind = ev.export_semantic_pair(theme.PALETTE_STANDARD)
    with _with_deltas(s, lap, deltas), _Patched(
            (s.corners, "lap_corner_resolved", lambda _lap: [True] * n),
            (s, "has_reference", lambda: True), (s, "reference_is_own_recording", lambda: False)):
        assert cc.level_step(s) == step
        got = {c.cid: c for c in cc.card_rows(s, lap, None, theme.PALETTE_STANDARD)}
        level = ("≈ ref", EXPORT.neutral)
        want = {1: level, 2: level, 3: level,
                4: (f"{fmt_signed(step + 0.001, 2)} s vs ref", behind),
                5: (f"{fmt_signed(-(step + 0.05), 2)} s vs ref", ahead)}
        for cid, w in want.items():
            assert (got[cid].delta, got[cid].colour) == w, (cid, deltas[cid - 1], got[cid])
    with _with_deltas(s, lap, deltas), _Patched(
            (s.corners, "lap_corner_resolved", lambda _lap: [True] * n),
            (s, "has_reference", lambda: True), (s, "reference_is_own_recording", lambda: True)):
        assert cc.level_step(s) == library.PB_PRECISION_S
        got = {c.cid: c for c in cc.card_rows(s, lap, None, theme.PALETTE_STANDARD)}
        assert (got[1].delta, got[1].colour) == ("+0.08 s vs ref", behind), got[1]
    print(f"ok ≈ ref under {step:.2f} s against another recording; its own keeps 0.07 s")


def test_no_card_for_an_unmatched_corner_and_none_at_all_without_one():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    real = s.corners.lap_corner_resolved(lap)
    assert real[0], "fixture: C1 must be matched for the positive control"
    have = {c.cid for c in cc.card_rows(s, lap, None, None)}
    assert 1 in have
    with _Patched((s.corners, "lap_corner_resolved",
                   lambda i: [False] + list(real[1:]) if i == lap else real)):
        have = {c.cid for c in cc.card_rows(s, lap, None, None)}
        assert 1 not in have and have, have
    with _Patched((s.corners, "lap_corner_resolved", lambda i: [False] * len(real))):
        assert cc.card_rows(s, lap, None, None) == []
        painter = ev.OverlayPainter(s, _spec(s, lap, elements=("corner_cards",)), 1920, 1080, 30.0)
        assert painter.elements == [], "an element with no card took part"
        why = cc.unavailable_reason(s, lap)
        assert why and "matched on track" in why, why
    print("ok an unmatched corner gets no card; a lap with none gets no element")


def test_the_best_lap_shows_the_minimum_speed_only():
    f = _fixture()
    s, best = f["s"], f["best"]
    got = cc.card_rows(s, best, None, None)
    assert got, "the best lap has no cards"
    assert all(c.delta is None and c.colour is None for c in got), got
    w_best = cc.card_width(got[0], 43.2)
    other = replace(got[0], delta="+0.12 s vs best", colour=EXPORT.behind)
    assert w_best < cc.card_width(other, 43.2) - 60
    print(f"ok the best lap: {len(got)} speed-only cards")


# ------------------------------------------------------------------------------ the cost
def test_prepare_renders_each_card_once_and_paint_never_draws_text():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    renders, texts, inside = [], [], [False]
    real_render, real_draw = cc.render_card, ev._draw_text
    real_paint = cc.CornerCardsElement.paint

    def count_render(*a, **k):
        renders.append(1)
        return real_render(*a, **k)

    def count_draw(*a, **k):
        if inside[0]:
            texts.append(1)
        return real_draw(*a, **k)

    def paint(self, p, vals):
        inside[0] = True
        try:
            real_paint(self, p, vals)
        finally:
            inside[0] = False

    spec = _spec(s, lap, elements=("corner_cards",))
    with _Patched((cc, "render_card", count_render), (ev, "_draw_text", count_draw),
                  (cc.CornerCardsElement, "paint", paint)):
        painter = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
        el = _element(painter)
        assert len(renders) == len(el.cards), (len(renders), len(el.cards))
        # Every frame a card is on screen is painted whole, as the render paints it, and so is one
        # in fifteen of the rest. Every OTHER frame still goes through the element's own `paint`
        # (the function the render and text counters watch) on one small image: with no card up
        # it returns at once, so a stray render or text draw a few frames long is still seen.
        # Painting all 1,534 frames whole at 1080p cost 18 s of this file's 33 s (2026-11-18).
        from PySide6.QtGui import QImage, QPainter
        scratch = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
        qp = QPainter(scratch)
        shown = painted = 0
        try:
            for i, t in enumerate(ev.frame_times(spec.t0, spec.t1, 30.0)):
                vals = ev.overlay_values_at(s, float(t), spec)
                dial = painter.advance_and_snapshot(vals)
                on = el.active(vals.tt) is not None
                if on or i % 15 == 0:
                    ev._paint_alpha_frame(painter, 1920, 1080, vals, dial)
                    painted += 1
                else:
                    qp.save()
                    el.paint(qp, vals)
                    qp.restore()
                shown += on
        finally:
            qp.end()
    assert len(renders) == len(el.cards), f"cards rendered again while painting: {len(renders)}"
    assert texts == [], f"paint drew text {len(texts)} times"
    assert shown > 30 * len(el.cards), shown
    spec.source.cleanup()
    print(f"ok {len(el.cards)} cards rendered once in prepare; {shown} frames blitted, no text "
          f"({painted} frames painted)")


def test_the_card_comes_at_the_exit_holds_and_fades_and_a_later_one_replaces_it():
    el = cc.CornerCardsElement()
    el._exits = [10.0, 11.0, 20.0]
    assert el.active(9.99) is None
    i, a = el.active(10.0)
    assert i == 0 and a == 0.0
    assert abs(el.active(10.1)[1] - 0.5) < 1e-9
    assert el.active(10.5) == (0, 1.0)
    assert el.active(11.0)[0] == 1, "the later card must replace the earlier one"
    assert el.active(12.9)[0] == 1 and el.active(12.9)[1] < 0.6
    assert el.active(13.0) is None
    assert el.active(None) is None
    print("ok exit, hold, fades, replacement")


# ------------------------------------------------------------------------------ real renders
class _KeepingEncoder:
    """Stands in for the encoding ffmpeg: keeps every frame exactly as the render hands it over."""

    def __init__(self, cmd):
        head = cmd[:cmd.index("pipe:0")]
        self.w, self.h = map(int, head[head.index("-s") + 1].split("x"))
        self.bpp = 3 if head[head.index("-pix_fmt") + 1] == "rgb24" else 4
        self.frames, self._buf = [], bytearray()
        self.stdin, self.stderr, self.returncode, self.pid = self, None, None, -1

    def write(self, b):
        self._buf += memoryview(b).cast("B")
        fb = self.w * self.h * self.bpp
        while len(self._buf) >= fb:
            self.frames.append(bytes(self._buf[:fb]))
            del self._buf[:fb]
        return len(b)

    def flush(self):
        pass

    def close(self):
        pass

    def wait(self, timeout=None):
        self.returncode = self.returncode or 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def _render(spec, s):
    real_popen = ev.subprocess.Popen
    encoders = []

    def popen(cmd, *a, **k):
        if "pipe:0" in cmd:
            encoders.append(_KeepingEncoder(cmd))
            return encoders[-1]
        return real_popen(cmd, *a, **k)

    ev.subprocess.Popen = popen
    try:
        r = ev.Renderer(s, spec)
        r.run()
    finally:
        ev.subprocess.Popen = real_popen
    return encoders[-1].frames, r, encoders[-1]


def _exit_tt(s, lap, k_card):
    """The k-th matched corner's EXIT on the telemetry clock, from the corner model itself (its
    entry time plus the corner's time) — never from the element under test."""
    stats = s.corners.lap_corner_stats(lap)
    ok = s.corners.lap_corner_resolved(lap)
    st = [x for x, r in zip(stats, ok, strict=True) if r][k_card]
    return s.corners.corner_entry_media_time(lap, st.cid) + st.time


def _around_exit(s, lap, k_card, **kw):
    """A window from 1.5 s before card `k_card`'s exit to 1.5 s past its hold (media clock)."""
    base = ev.build_lap_spec(s, _fixture()["out"], lap, config=_cfg(out_height=PIC_H, **kw))
    exit_tt = _exit_tt(s, lap, k_card)
    t_exit = exit_tt + (base.t0 - s.telemetry_time(base.t0))   # the lag is constant over a window
    return replace(base, t0=t_exit - 1.5, t1=t_exit + cc.HOLD_S + 1.5, ends_on_finish=False)


def test_a_real_render_shows_the_card_in_its_place_between_exit_and_hold():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    k_card = 1
    on, r_on, enc = _render(_around_exit(s, lap, k_card, elements=("corner_cards",)), s)
    off, _r, _enc = _render(_around_exit(s, lap, k_card), s)
    assert len(on) == len(off) == r_on.total_frames > 100
    el = _element(r_on._painter)
    img = el.images[k_card]
    x0, y0 = int(el._at.x()), int(el._at.y())
    x1, y1 = x0 + img.width(), y0 + img.height()
    exit_tt = _exit_tt(s, lap, k_card)
    before = during = after = 0
    for i, (a, b) in enumerate(zip(on, off, strict=True)):
        tt = s.telemetry_time(float(r_on._times[i]))
        if tt < exit_tt:
            assert a == b, f"frame {i}: a card before the corner's exit ({tt - exit_tt:+.3f} s)"
            before += 1
        elif tt >= exit_tt + cc.HOLD_S:
            assert a == b, f"frame {i}: the card outstayed its hold ({tt - exit_tt:+.3f} s)"
            after += 1
        elif abs(tt - exit_tt - 1.0) < 0.5:
            ia = np.frombuffer(a, np.uint8).reshape(enc.h, enc.w, enc.bpp).astype(int)
            ib = np.frombuffer(b, np.uint8).reshape(enc.h, enc.w, enc.bpp).astype(int)
            diff = np.argwhere((ia != ib).any(axis=2))
            assert len(diff) > 500, f"frame {i}: no card one second after the exit"
            assert (diff[:, 0] >= y0).all() and (diff[:, 0] < y1).all() and \
                (diff[:, 1] >= x0).all() and (diff[:, 1] < x1).all(), \
                f"frame {i}: the card changed pixels outside its rectangle"
            during += 1
    assert before > 30 and during > 20 and after > 30, (before, during, after)
    print(f"ok a real render: the card in its rectangle on {during} frames; {before} before the "
          f"exit and {after} after the hold are the Classic bytes")


def test_option_off_or_nothing_to_show_is_the_classic_export():
    """No element named, and the element named on a lap where it has nothing to show (no corner
    matched), produce the same bytes and never reach the package's per-frame painting. The
    cross-tree proof (this branch == main, ghost-only == the ghost branch) is in the PR."""
    f = _fixture()
    s, lap = f["s"], f["lap"]
    calls = []
    real_paint = overlay.paint_layer
    n = len(s.corners.lap_corner_stats(lap))
    off, on = _around_exit(s, lap, 0), _around_exit(s, lap, 0, elements=("corner_cards",))
    with _Patched((overlay, "paint_layer", lambda *a, **k: (calls.append(1), real_paint(*a, **k))[1]),
                  (s.corners, "lap_corner_resolved", lambda _i: [False] * n)):
        a, ra, _e = _render(off, s)
        b, rb, _e = _render(on, s)
    assert rb._painter.elements == [] and len(a) == len(b) > 100
    assert a == b, "frames differ with the option on and nothing to show"
    assert calls == [], f"the overlay package painted {len(calls)} times with no element"
    print("ok option off / nothing to show = the Classic bytes")


def test_the_card_sits_in_the_safe_area_clear_of_every_plate_at_every_shape():
    f = _fixture()
    s, lap = f["s"], f["lap"]
    for w, h in ((1920, 1080), (1080, 1920), (1080, 1080), (3840, 2160), (2160, 3840)):
        spec = _spec(s, lap, elements=("corner_cards",))
        painter = ev.OverlayPainter(s, spec, w, h, 30.0)
        el = _element(painter)
        m = spec.config.margin_frac * painter._u
        safe = (m, m, w - m, h - m)
        plates = {"gmeter": painter._g_rect, "map": painter._map._box,
                  "readout": painter._readout_rect, "strip": painter._strip_rect,
                  "stamp": painter._stamp_rect}
        for img in el.images:
            x0, y0 = el._at.x(), el._at.y()
            # The plate is drawn one pixel in from the image's edge.
            r = (x0 + 1, y0 + 1, x0 + img.width() - 1, y0 + img.height() - 1)
            assert safe[0] <= r[0] and safe[1] <= r[1] and r[2] <= safe[2] and r[3] <= safe[3], \
                f"{w}x{h}: card {r} outside the safe area {safe}"
            for name, q in plates.items():
                if q.width() <= 0 or q.height() <= 0:
                    continue
                clear = (r[2] <= q.left() or q.right() <= r[0] or r[3] <= q.top()
                         or q.bottom() <= r[1])
                assert clear, f"{w}x{h}: card {r} overlaps the {name} plate {q}"
        spec.source.cleanup()
    print("ok placed inside the safe area, clear of every plate, at 16:9, 9:16, 1:1 and 4K")


# ------------------------------------------------------------------------------ the dialog
def _window():
    from studio.app import StudioWindow
    f = _fixture()
    if "win" not in f:
        win = StudioWindow([])
        win.session = f["s"]
        win._paths = [f["src"]]
        f["win"] = win
    return f["win"]


def _open_dialog(lap, act):
    win = _window()
    orig = QDialog.exec

    def on_exec(dlg):
        form = dlg.findChild(QFormLayout)
        rows = {}
        for r in range(form.rowCount()):
            label = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
            if label is not None and field is not None:
                rows[label.widget().text()] = field.widget()
        return act(dlg, rows)
    QDialog.exec = on_exec
    try:
        return win.exports._ask_export_options(lap)
    finally:
        QDialog.exec = orig


def _cards_box(rows) -> QCheckBox:
    return next(b for b in rows["Extras"].findChildren(QCheckBox) if b.text() == "Corner cards")


def _hint(dlg):
    return " ".join(lab.text() for lab in dlg.findChildren(QLabel) if lab.property("role") == "Hint")


def test_the_dialog_offers_corner_cards_off_by_default_and_says_where_they_cannot_apply():
    f = _fixture()
    lap = f["lap"]
    key = "export_corner_cards"
    data = prefs.load()
    for k in (key, "export_ghost", "export_scope_idx", "export_res_idx"):
        data.pop(k, None)
    prefs.save(data)
    seen = {}

    def first(dlg, rows):
        box, scope = _cards_box(rows), rows["Export"]
        assert isinstance(scope, QComboBox)
        seen["default"], seen["enabled"] = box.isChecked(), box.isEnabled()
        for i in range(scope.count()):
            scope.setCurrentIndex(i)
            seen[scope.itemText(i)] = (box.isEnabled(), box.toolTip())
        scope.setCurrentIndex(0)
        box.setChecked(True)
        return QDialog.Accepted

    choice = _open_dialog(lap, first)
    assert seen["default"] is False, "corner cards must open OFF"
    assert seen["enabled"] is True
    assert seen["This lap"][0] and "0.07" in seen["This lap"][1], seen["This lap"]
    assert seen["Best lap"][0]
    full = seen["Full session — the whole recording"]
    assert not full[0] and full[1].startswith("Off here:") and "one lap" in full[1], full
    assert choice.config.elements == ("corner_cards",), choice.config.elements
    assert prefs.get(key) is True, "the tick was not remembered"
    again = {}

    def second(dlg, rows):
        again["checked"] = _cards_box(rows).isChecked()
        rows["Export"].setCurrentIndex(rows["Export"].count() - 1)     # Full session
        again["hint"] = _hint(dlg)
        return QDialog.Accepted

    choice = _open_dialog(lap, second)
    assert again["checked"] is True
    assert "No corner cards in this export" in again["hint"], again["hint"]
    assert "corner_cards" not in choice.config.elements, "a disabled option reached the render"
    # All laps: a lap with no corner matched on track gets no cards, and the hint names it the
    # way it names the ghost's ghostless files.
    s = f["s"]
    n_corners = len(s.corners.lap_corner_stats(lap))
    real = s.corners.lap_corner_resolved
    third = {}

    def all_laps(dlg, rows):
        scope = rows["Export"]
        scope.setCurrentIndex(scope.findText("All laps — one file per lap"))
        third["hint"] = _hint(dlg)
        third["tip"] = _cards_box(rows).toolTip()
        return QDialog.Rejected

    with _Patched((s.corners, "lap_corner_resolved",
                   lambda i: [False] * n_corners if i == lap else real(i))):
        _open_dialog(lap, all_laps)
    n = len(s.valid_lap_ids())
    assert f"No corner cards in 1 file of {n}: No corner of Lap {lap + 1} was matched on track." \
        in third["hint"], third["hint"]
    assert "same corner on your best lap" in third["tip"], third["tip"]
    assert "provisional" not in third["tip"], third["tip"]
    # Provisional timing: the cards keep their Δ, and the tooltip says the best lap is chosen from
    # provisional lap times.
    with _Patched((Session, "timing_verified", property(lambda _self: False))):
        _open_dialog(lap, all_laps)
    assert "best lap these cards compare against is chosen from them" in third["tip"], third["tip"]
    prefs.set(key, False)
    print("ok the dialog: Extras ▸ Corner cards off by default, remembered, disabled with its reason")


def _run_all():
    for fn in (test_the_cards_say_what_the_corners_table_says,
               test_each_prepared_card_is_a_render_of_the_tables_strings,
               test_inside_timing_precision_the_card_says_level_beyond_it_the_colour,
               test_against_another_recording_the_card_says_level_under_the_reference_lap_step,
               test_no_card_for_an_unmatched_corner_and_none_at_all_without_one,
               test_the_best_lap_shows_the_minimum_speed_only,
               test_prepare_renders_each_card_once_and_paint_never_draws_text,
               test_the_card_comes_at_the_exit_holds_and_fades_and_a_later_one_replaces_it,
               test_a_real_render_shows_the_card_in_its_place_between_exit_and_hold,
               test_option_off_or_nothing_to_show_is_the_classic_export,
               test_the_card_sits_in_the_safe_area_clear_of_every_plate_at_every_shape,
               test_the_dialog_offers_corner_cards_off_by_default_and_says_where_they_cannot_apply):
        fn()
    print("test_export_corner_cards: all ok")


if __name__ == "__main__":
    _run_all()
