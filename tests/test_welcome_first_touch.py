"""THE FIRST TOUCH — what the welcome screen offers before anything is loaded (review §6.7).

Three findings, all measured on the REAL StudioWindow offscreen:

  * (a) THE SECOND CTA DEAD-ENDED. "Open demo" was unconditional, and the clip it resolves comes
    from `PACER_DEMO_MP4`, a local cache, or a release asset **that was never published**
    (docs/FIRST_LAP.md says so). So on a machine with neither the env var nor a cache — i.e. anyone
    who builds this from source — the obvious low-commitment click produced "Demo clip
    unavailable…" as their FIRST experience of the app. The button is now offered only when
    `studio.demo.demo_available()` says a click could land somewhere, and the copy behind it no
    longer offers a "retry" that cannot work.
  * (b) THE BUTTON WEIGHTS WERE INVERTED — the amber PRIMARY measured 133 px beside a 178 px
    secondary, because the secondary was floored at its busy label, and that label was a whole
    sentence ("Fetching the demo clip…"). The theme's hierarchy said one thing and the geometry
    said the other.
  * (c) THE BRAND MOMENT WAS A STOCK DOWNLOAD-TRAY GLYPH (`ph.download-simple`) while the app's own
    speed chevron — the mark `studio/assets/pacer.icns` is built from — already existed in the tree.

THE THREE AVAILABILITY STATES ARE THE POINT, so all three are driven here: the env var pointing at
a real local file (the demo-recording path — it must still light the button up AND work), a cached
clip, and nothing resolvable (the shipping default). The seams are diverted so the answer is the
same on a machine that happens to have a cached clip.

Run: QT_QPA_PLATFORM=offscreen PACER_NO_MEDIA=1 python tests/test_welcome_first_touch.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Every persistence seam into one temp tree BEFORE any window exists — `demo` included, because the
# welcome column's SHAPE is a function of it: a developer with a cached clip in their real
# ~/Library/Application Support/pacer would otherwise measure a different screen than CI does.
from studio import demo, library, prefs, track_db  # noqa: E402

_SEAMS = tempfile.mkdtemp(prefix="pacer-test-first-touch-")
for _mod, _name in ((prefs, "prefs"), (library, "library"), (track_db, "track_db"),
                    (demo, "demo")):
    _dir = os.path.join(_SEAMS, _name)
    os.makedirs(_dir, exist_ok=True)
    _mod._app_support_dir = (lambda d=_dir: d)
os.environ.pop("PACER_DEMO_MP4", None)

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: measure the SHIPPING font stack

import numpy as np  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QPushButton  # noqa: E402

from studio import theme  # noqa: E402
from studio.app import DEMO_UNAVAILABLE_MESSAGE, StudioWindow  # noqa: E402
from studio.overlays import (  # noqa: E402
    BUSY_DEMO_LABEL,
    DEMO_LABEL,
    DROP_GLYPH_PX,
    OPEN_LABEL,
    column_metrics,
)

# A real file for PACER_DEMO_MP4 / the cache to point at. Nothing decodes it — resolution is a path
# question, and the click is driven with `_load` stubbed (loading a 16-byte MP4 would fail in the
# reader, which is a different test's subject).
_CLIP = os.path.join(_SEAMS, "pacer-demo-lap.mp4")
with open(_CLIP, "wb") as _f:
    _f.write(b"\x00" * 16)


def _settle(seconds=0.5):
    """Pump until the event queue has settled. HALF A SECOND, not one processEvents() pass: a
    single pump leaves the stylesheet/layout mid-flight and manufactures false findings — the
    harness trap this review's own ground rules name."""
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)


def _env_state():
    os.environ["PACER_DEMO_MP4"] = _CLIP


def _cache_state():
    os.environ.pop("PACER_DEMO_MP4", None)
    cached = demo.demo_cache_path()
    os.makedirs(os.path.dirname(cached), exist_ok=True)
    with open(cached, "wb") as f:
        f.write(b"\x00" * 16)


def _none_state():
    os.environ.pop("PACER_DEMO_MP4", None)
    cached = demo.demo_cache_path()
    if os.path.isfile(cached):
        os.remove(cached)


STATES = (("PACER_DEMO_MP4", _env_state, True),
          ("a cached clip", _cache_state, True),
          ("nothing resolvable", _none_state, False))


def _rgb(w):
    """A widget's painted pixels as (h, w, 3) uint8 IN RGB — from the WINDOW composite, so a rule
    that never reached the pixels cannot pass.

    THE CHANNEL ORDER IS LOAD-BEARING HERE and is not in the suite's other `_rgb` helpers: they
    count pixels that CHANGED, which is order-blind, while this file compares against a named theme
    colour. `Format_RGB32` is 0xffRRGGBB stored little-endian, so the bytes arrive B,G,R — reading
    them as RGB turns the accent (245,166,35) into (35,166,245) and finds zero matches on a screen
    that is painting it perfectly."""
    img = w.grab().toImage().convertToFormat(QImage.Format_RGB32)
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 4)
    return a[..., 2::-1]


def _window(size=(1280, 800)):
    win = StudioWindow([])
    win.resize(*size)
    win.show()
    _settle()
    return win


# ==================================================== (a) the second CTA
def test_the_demo_button_is_offered_only_when_a_demo_resolves():
    """The gate itself, in all three states and at both shipped window sizes: the button exists iff
    `demo.demo_available()`, and when it does not exist NOTHING on the card names a demo."""
    try:
        for name, enter, available in STATES:
            enter()
            assert demo.demo_available() is available, name
            for size in ((1280, 800), (1920, 1200)):
                win = _window(size)
                try:
                    view = win.centralWidget()
                    buttons = view.drop_zone.findChildren(QPushButton)
                    labels = [b.text() for b in buttons]
                    if available:
                        assert view.demo_btn is not None, (name, size)
                        assert labels == [OPEN_LABEL, DEMO_LABEL], (name, size, labels)
                        assert view.demo_btn.isEnabled(), (name, size)
                    else:
                        assert view.demo_btn is None, (name, size)
                        assert labels == [OPEN_LABEL], (name, size, labels)
                        # …and not merely hidden-but-present, nor disabled-and-lying.
                        assert not any("demo" in b.text().lower() for b in buttons)
                finally:
                    win.close()
                    _settle(0.1)
    finally:
        _none_state()
    print("test_the_demo_button_is_offered_only_when_a_demo_resolves OK "
          "(env / cache / nothing, at 1280x800 and 1920x1200)")


def test_the_env_var_lights_the_button_up_and_the_click_works_end_to_end():
    """THE DEMO-RECORDING PATH. `PACER_DEMO_MP4` pointing at a local file must still put the button
    on screen and still open THAT file — driven through the production slot chain (click ->
    _open_demo -> DemoResolveWorker -> the real studio.demo resolver -> _load), with only the load
    itself stubbed."""
    try:
        _env_state()
        win = _window()
        try:
            view = win.centralWidget()
            loaded = []
            win._load = lambda paths, **kw: loaded.append(list(paths))
            assert view.demo_btn is not None and view.demo_btn.isEnabled()
            view.demo_btn.click()
            # The click returns immediately (the resolve is off-thread) and says it is working.
            assert view.demo_btn.text() == BUSY_DEMO_LABEL
            assert not view.demo_btn.isEnabled()
            end = time.time() + 20.0
            while time.time() < end and not loaded:
                _APP.processEvents()
                time.sleep(0.004)
            assert loaded == [[_CLIP]], loaded
        finally:
            win.close()
            _settle(0.1)
    finally:
        _none_state()
    print("test_the_env_var_lights_the_button_up_and_the_click_works_end_to_end OK")


def test_the_cached_clip_is_the_other_state_that_offers_the_button():
    """Same, one step down the resolution order: no env var, a clip in the app-support cache."""
    try:
        _cache_state()
        win = _window()
        try:
            view = win.centralWidget()
            loaded = []
            win._load = lambda paths, **kw: loaded.append(list(paths))
            assert view.demo_btn is not None
            view.demo_btn.click()
            end = time.time() + 20.0
            while time.time() < end and not loaded:
                _APP.processEvents()
                time.sleep(0.004)
            assert loaded == [[demo.demo_cache_path()]], loaded
        finally:
            win.close()
            _settle(0.1)
    finally:
        _none_state()
    print("test_the_cached_clip_is_the_other_state_that_offers_the_button OK")


def test_the_unavailable_copy_no_longer_offers_a_retry_that_cannot_work():
    """The message `--demo` lands on when nothing resolves. It used to read "check your connection
    and retry" — a retry of a download of an asset that was never published, aimed at a button that
    is no longer on the screen. It must name what is true and what does work instead."""
    _none_state()
    text = DEMO_UNAVAILABLE_MESSAGE
    assert "retry" not in text.lower(), text
    assert "connection" not in text.lower(), text
    assert "demo" in text.lower() and ".mp4" in text.lower(), text
    assert OPEN_LABEL.rstrip("…") in text, "the copy names the door that IS on the screen"
    # And it is ONE string: the CLI's `--demo` path and the resolve-came-back-None path both use it.
    win = StudioWindow([], demo_unavailable=True)
    try:
        _settle(0.2)
        view = win.centralWidget()
        assert view.demo_btn is None, "the failed demo must not leave its button behind"
        assert DEMO_UNAVAILABLE_MESSAGE in view.error_label.text()
        assert not view.error_label.isHidden()
    finally:
        win.close()
        _settle(0.1)
    print("test_the_unavailable_copy_no_longer_offers_a_retry_that_cannot_work OK")


def test_the_cli_demo_flag_still_tries_the_network():
    """The gate is about what the UI OFFERS, not about disabling the feature: `--demo` (which calls
    the resolver with `allow_download=True`) must still attempt the download even in the state where
    the button is hidden. Stubbed at `_try_download_demo` — nothing here touches the network."""
    _none_state()
    calls = []
    real = demo._try_download_demo
    demo._try_download_demo = lambda dest, url=None: calls.append(dest) or False
    try:
        assert demo.demo_available() is False          # the UI offers nothing…
        assert demo.resolve_demo_recording() is None   # …and the explicit request still tried
        assert calls == [demo.demo_cache_path()], calls
        # …while the offline lookup never reaches the network, whoever asks.
        calls.clear()
        assert demo.resolve_demo_recording(allow_download=False) is None
        assert demo.demo_available() is False
        assert calls == [], calls
    finally:
        demo._try_download_demo = real
    print("test_the_cli_demo_flag_still_tries_the_network OK")


# ==================================================== (b) the button weights
def test_the_primary_is_the_wider_button_in_every_state_it_has_a_twin():
    """§6.7(b). Swept across the states and both window sizes, and checked through the ONE label
    swap the row can do (the busy label), because that swap is what inverted the pair in the first
    place."""
    try:
        for name, enter, available in STATES:
            enter()
            for size in ((1280, 800), (1920, 1200)):
                win = _window(size)
                try:
                    view = win.centralWidget()
                    if not available:
                        assert view.demo_btn is None
                        continue
                    assert view.open_btn.width() >= view.demo_btn.width(), (
                        f"{name} at {size}: primary {view.open_btn.width()} px < secondary "
                        f"{view.demo_btn.width()} px")
                    win._set_demo_busy(True)
                    _settle(0.2)
                    assert view.demo_btn.text() == BUSY_DEMO_LABEL
                    assert view.open_btn.width() >= view.demo_btn.width(), (
                        f"{name} at {size}: the busy label re-inverted the pair")
                finally:
                    win.close()
                    _settle(0.1)
        m = column_metrics(True)
        assert m.primary_w >= m.secondary_w, m
    finally:
        _none_state()
    print(f"test_the_primary_is_the_wider_button_in_every_state_it_has_a_twin OK "
          f"(primary {m.primary_w} px ≥ secondary {m.secondary_w} px, resting and busy)")


def test_the_secondary_button_still_cannot_move_the_row():
    """The D4-06 guarantee, kept: the floor is now the WIDER of the resting and busy labels, in
    either direction. The first cut of this PR floored it at the busy width alone, which — with a
    busy label SHORTER than "Open demo" — let the button shrink 7 px on the click and slid the
    centred row 3 px."""
    try:
        _env_state()
        win = _window()
        try:
            view = win.centralWidget()
            before = (view.open_btn.geometry(), view.demo_btn.geometry(),
                      view.drop_zone.geometry())
            for busy in (True, False, True, False):
                win._set_demo_busy(busy)
                _settle(0.2)
                now = (view.open_btn.geometry(), view.demo_btn.geometry(),
                       view.drop_zone.geometry())
                assert now == before, f"busy={busy} moved the row: {before} -> {now}"
        finally:
            win.close()
            _settle(0.1)
    finally:
        _none_state()
    print("test_the_secondary_button_still_cannot_move_the_row OK")


# ==================================================== (c) the brand moment
def test_the_drop_glyph_is_the_apps_own_mark_and_it_composites():
    """§6.7(c). Two claims, and the second is the one a pixmap comparison alone would miss: the
    label carries `theme.brand_mark`, AND the mark's accent ink is really on the window."""
    _none_state()
    win = _window()
    try:
        view = win.centralWidget()
        want = theme.brand_mark(DROP_GLYPH_PX).toImage()
        got = view.drop_icon.pixmap().toImage()
        assert got.size() == want.size(), (got.size(), want.size())
        assert got == want, "the welcome glyph is not the app's brand mark"

        # …and it PAINTED. Counted in the window composite over the glyph's own rect, against the
        # accent the mark is drawn in (both chevrons, one hue family).
        from PySide6.QtCore import QPoint

        px = _rgb(win)
        tl = view.drop_icon.mapTo(win, QPoint(0, 0))
        box = px[tl.y():tl.y() + view.drop_icon.height(),
                 tl.x():tl.x() + view.drop_icon.width()].astype(int)
        accent = np.array(QColor(theme.C.accent).getRgb()[:3], dtype=int)
        press = np.array(QColor(theme.C.accent_press).getRgb()[:3], dtype=int)
        near = ((np.abs(box - accent).sum(-1) < 60) | (np.abs(box - press).sum(-1) < 60)).sum()
        assert near > 100, f"only {near} accent-coloured pixels in the glyph box — it did not paint"
        # The stock glyph it replaced was C.text_muted: no accent ink at all, which is what makes
        # this count the discriminating measurement rather than a tautology.
        muted = np.array(QColor(theme.C.text_muted).getRgb()[:3], dtype=int)
        assert (np.abs(box - muted).sum(-1) < 20).sum() == 0, "the muted tray glyph is still there"
    finally:
        win.close()
        _settle(0.1)
    print(f"test_the_drop_glyph_is_the_apps_own_mark_and_it_composites OK "
          f"({DROP_GLYPH_PX}px mark, accent ink present)")


def test_the_mark_is_the_icons_geometry_not_a_second_copy_of_it():
    """The mark is worn twice — the welcome glyph and studio/assets/pacer.icns — so the numbers live
    once (theme.BRAND_*) and the icon generator reads them. A second copy is a brand that drifts."""
    from studio.dev import make_icon

    assert make_icon.BRAND_CHEVRONS == theme.BRAND_CHEVRONS
    assert make_icon.BRAND_CHEVRON_POINTS == theme.BRAND_CHEVRON_POINTS
    assert make_icon.BRAND_GRID == theme.BRAND_GRID
    # Two chevrons, the back one thinner and to the left of the front one (the depth cue).
    (back_x, back_w), (front_x, front_w) = theme.BRAND_CHEVRONS
    assert back_x < front_x and back_w < front_w, theme.BRAND_CHEVRONS
    # The pixmap is square, honours the device pixel ratio, and has ink in it.
    pm = theme.brand_mark(DROP_GLYPH_PX)
    assert pm.width() == pm.height()
    assert pm.width() == int(DROP_GLYPH_PX * pm.devicePixelRatio())
    img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
    a = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.width(), 4)
    assert (a[..., 3] > 0).sum() > 0.15 * a.shape[0] * a.shape[1], "the mark is mostly empty"
    print("test_the_mark_is_the_icons_geometry_not_a_second_copy_of_it OK")


def _run_all():
    test_the_demo_button_is_offered_only_when_a_demo_resolves()
    test_the_env_var_lights_the_button_up_and_the_click_works_end_to_end()
    test_the_cached_clip_is_the_other_state_that_offers_the_button()
    test_the_unavailable_copy_no_longer_offers_a_retry_that_cannot_work()
    test_the_cli_demo_flag_still_tries_the_network()
    test_the_primary_is_the_wider_button_in_every_state_it_has_a_twin()
    test_the_secondary_button_still_cannot_move_the_row()
    test_the_drop_glyph_is_the_apps_own_mark_and_it_composites()
    test_the_mark_is_the_icons_geometry_not_a_second_copy_of_it()
    print("ALL WELCOME FIRST-TOUCH TESTS OK")


if __name__ == "__main__":
    _run_all()
