"""THE FIRST TOUCH — what the welcome screen offers before anything is loaded (review §6.7).

Three findings, all measured on the REAL StudioWindow offscreen:

  * (a) THE SECOND CTA DEAD-ENDED, AND THEN IT WAS NOT THERE. "Open demo" was unconditional
    while the release asset it downloads **had never been published**, so on a machine with
    neither the env var nor a cache the obvious low-commitment click produced "Demo clip
    unavailable…" as the FIRST experience of the app — and the fix gated the button on
    `studio.demo.demo_available()`, which left a fresh launch with one action and no mention of a
    demo at all (LEFT-24 / NEW-7), after the synthetic demo WAS published. The button is always
    offered now and `demo_available()` decides its LABEL: "Open demo" when the clip is on this
    machine, "Get demo · N MB" (N from the pinned asset's size) when the click will download it.
    The network is still reached only on that click, and a failed fetch keeps the button.
  * (b) THE BUTTON WEIGHTS WERE INVERTED — the amber PRIMARY measured 133 px beside a 178 px
    secondary, because the secondary was floored at its busy label, and that label was a whole
    sentence ("Fetching the demo clip…"). The theme's hierarchy said one thing and the geometry
    said the other.
  * (c) THE BRAND MOMENT WAS A STOCK DOWNLOAD-TRAY GLYPH (`ph.download-simple`) while the app's own
    speed chevron — the mark `studio/assets/pacer.icns` is built from — already existed in the tree.
    And on a Retina screen the mark that replaced it showed only its top-left quadrant: it was
    scaled by the device pixel ratio twice, and every check here compared it with itself at DPR 1
    (QA3-BRANDMARK). So one case renders it at DPR 2 in a child process, where Qt reads
    QT_SCALE_FACTOR, and holds its edges and its shape to the DPR-1 render.

THE THREE AVAILABILITY STATES ARE THE POINT, so all three are driven here: the env var pointing at
a real local file (the demo-recording path — it must still say "Open demo" AND work), a cached
clip, and nothing resolvable (the shipping default: "Get demo · N MB"). The seams are diverted so
the answer is the same on a machine that happens to have a cached clip, and `urllib.request.urlopen`
is replaced by a tripwire around every test (`_offline`, put back after each), so no test here can
reach the network.

Run: QT_QPA_PLATFORM=offscreen PACER_NO_MEDIA=1 python tests/test_welcome_first_touch.py
"""
import json
import os
import subprocess
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

# THE NETWORK TRIPWIRE, around every test here (`_offline`). The button now exists on a machine with
# no demo and says it downloads, so "nothing here reaches the network" is no longer true by
# construction: it is held by counting. Every test that clicks the button stubs the fetch one level
# up (`demo._try_download_demo`), so a call landing here is a path that bypassed that stub.
import functools  # noqa: E402
import urllib.request  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

_URLOPEN_AT_IMPORT = urllib.request.urlopen
_URLOPEN_CALLS = []


def _urlopen_tripwire(url, *args, **kwargs):
    _URLOPEN_CALLS.append(url)
    raise OSError(f"tests/test_welcome_first_touch.py reached the network: {url}")


def _offline(test):
    """Run `test` with urlopen replaced by the tripwire, and put back what was there — passed or
    failed. PER TEST, NOT AT IMPORT: installed for the module and never restored, it outlived this
    file in one pytest process and stood in every later module's way (CTest's one process per file
    hid that)."""
    @functools.wraps(test)
    def run():
        found = urllib.request.urlopen
        urllib.request.urlopen = _urlopen_tripwire
        try:
            return test()
        finally:
            urllib.request.urlopen = found
    run.offline = True
    return run

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()            # module scope, BEFORE any widget: measure the SHIPPING font stack

import numpy as np  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QPushButton  # noqa: E402

from studio import theme  # noqa: E402
from studio.app import DEMO_UNAVAILABLE_MESSAGE, StudioWindow  # noqa: E402
from studio.overlays import (  # noqa: E402
    BUSY_DEMO_LABEL,
    DEMO_GET_LABEL,
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


def _resting(available):
    """What the demo button says at rest in a state: open what is here, or say the click fetches."""
    return DEMO_LABEL if available else DEMO_GET_LABEL


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
@_offline
def test_a_fresh_launch_offers_the_demo_and_says_the_click_downloads_it():
    """LEFT-24 / NEW-7, in all three states and at both shipped window sizes: the second button is
    ALWAYS on the card, enabled, and `demo.demo_available()` decides only what it says. With no
    clip on this machine (the shipping default) it reads "Get demo · N MB", N the pinned asset's
    size rounded — computed from `studio/demo.py`'s `_DEMO_BYTES`, never typed — and its tooltip
    says the click downloads it; with one, "Open demo". Building the window reaches the network in
    no state (the tripwire above counts)."""
    mb = round(demo._DEMO_BYTES / 1e6)
    assert demo.download_mb() == mb, (demo.download_mb(), demo._DEMO_BYTES)
    assert f"{mb} MB" in DEMO_GET_LABEL, DEMO_GET_LABEL
    try:
        for name, enter, available in STATES:
            enter()
            assert demo.demo_available() is available, name
            for size in ((1280, 800), (1920, 1200)):
                calls = len(_URLOPEN_CALLS)
                win = _window(size)
                try:
                    view = win.centralWidget()
                    labels = [b.text() for b in view.drop_zone.findChildren(QPushButton)]
                    assert view.demo_btn is not None, (name, size, labels)
                    assert labels == [OPEN_LABEL, _resting(available)], (name, size, labels)
                    assert view.demo_btn.isEnabled(), (name, size)
                    # The label the busy state hands back is the one the view was built with.
                    assert view.demo_label == _resting(available), (name, view.demo_label)
                    tip = view.demo_btn.toolTip().lower()
                    assert "synthetic" in tip, (name, tip)
                    if not available:
                        assert f"{mb} mb" in view.demo_btn.text().lower(), view.demo_btn.text()
                        assert "download" in tip, (name, tip)
                    assert len(_URLOPEN_CALLS) == calls, f"{name}: building the welcome fetched"
                finally:
                    win.close()
                    _settle(0.1)
    finally:
        _none_state()
    print(f"test_a_fresh_launch_offers_the_demo_and_says_the_click_downloads_it OK "
          f"(env / cache → {DEMO_LABEL!r}, nothing → {DEMO_GET_LABEL!r}, at 1280x800 and "
          f"1920x1200, 0 network calls)")


@_offline
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


@_offline
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


@_offline
def test_a_fresh_launch_click_downloads_once_and_a_failure_keeps_the_door():
    """The none-state click, through the production chain (click -> _open_demo ->
    DemoResolveWorker -> the real `demo.resolve_demo_recording` -> `_try_download_demo`), with the
    fetch stubbed one level above the network: it must be ATTEMPTED exactly once, into the pinned
    cache path; the button says it is busy meanwhile; and when the fetch fails, the welcome comes
    back with the failure message AND the button, still offering the download — the retry the
    message names is on the screen (before LEFT-24 the rebuilt card had no second button)."""
    _none_state()
    fetched = []
    real = demo._try_download_demo
    demo._try_download_demo = lambda dest, url=None, sha256=None: fetched.append(dest) or False
    calls = len(_URLOPEN_CALLS)
    try:
        win = _window()
        try:
            view = win.centralWidget()
            loaded = []
            win._load = lambda paths, **kw: loaded.append(list(paths))
            assert view.demo_btn is not None and view.demo_btn.text() == DEMO_GET_LABEL
            view.demo_btn.click()
            end = time.time() + 20.0
            while time.time() < end and win.centralWidget() is view:
                _APP.processEvents()
                time.sleep(0.004)
            again = win.centralWidget()
            assert again is not view, "the failed fetch never came back to the welcome screen"
            assert fetched == [demo.demo_cache_path()], fetched
            assert loaded == [], loaded
            assert again.demo_btn is not None, "the failed fetch took the retry off the screen"
            assert again.demo_btn.text() == DEMO_GET_LABEL, again.demo_btn.text()
            assert again.demo_btn.isEnabled()
            assert DEMO_UNAVAILABLE_MESSAGE in again.error_label.text()
            assert not again.error_label.isHidden()
        finally:
            win.close()
            _settle(0.1)
    finally:
        demo._try_download_demo = real
        _none_state()
    assert len(_URLOPEN_CALLS) == calls, _URLOPEN_CALLS[calls:]
    print("test_a_fresh_launch_click_downloads_once_and_a_failure_keeps_the_door OK "
          "(1 fetch attempted, button kept, message shown, 0 network calls)")


@_offline
def test_the_unavailable_copy_names_the_failed_download_and_the_door_that_works():
    """The message `--demo` and a failed click land on. It read "check your connection and retry"
    while the asset had never been published — a retry that could not work, aimed at a button no
    longer on the screen — then "Pacer doesn't ship one", false once the synthetic demo was
    published (demo-data-v1), then offered no retry because the gate took the button away. The
    button stays now (LEFT-24), so the copy names the failed download, the button to try again BY
    ITS OWN LABEL, and the door that always works."""
    _none_state()
    text = DEMO_UNAVAILABLE_MESSAGE
    assert "download" in text.lower(), text
    assert "doesn't ship" not in text.lower(), text
    assert "demo" in text.lower() and ".mp4" in text.lower(), text
    assert OPEN_LABEL.rstrip("…") in text, "the copy names the door that IS on the screen"
    get = DEMO_GET_LABEL.split(" · ")[0]
    assert f"{get} again" in text, f"the copy must name the retry by the label it has ({get!r})"
    # And it is ONE string: the CLI's `--demo` path and the resolve-came-back-None path both use it.
    win = StudioWindow([], demo_unavailable=True)
    try:
        _settle(0.2)
        view = win.centralWidget()
        assert view.demo_btn is not None, "the failed demo must leave the retry it names"
        assert view.demo_btn.text().startswith(get), view.demo_btn.text()
        assert DEMO_UNAVAILABLE_MESSAGE in view.error_label.text()
        assert not view.error_label.isHidden()
    finally:
        win.close()
        _settle(0.1)
    print("test_the_unavailable_copy_names_the_failed_download_and_the_door_that_works OK")


@_offline
def test_the_cli_demo_flag_still_tries_the_network():
    """`demo_available()` decides what the button SAYS, not whether the feature works: `--demo`
    (which calls the resolver with `allow_download=True`) must still attempt the download in the
    state where the button offers one. Stubbed at `_try_download_demo` — nothing here touches the
    network.

    THE OTHER HALF IS THE PROMISE THE LABEL RESTS ON: the offline lookup never reaches the network,
    whoever asks — `demo_available()` in every state, and a whole welcome screen built on it (the
    button now exists with no demo on the machine, so a reachability probe would be one line away).
    Counted at `_try_download_demo` AND at the urlopen tripwire."""
    _none_state()
    calls = []
    real = demo._try_download_demo
    demo._try_download_demo = lambda dest, url=None: calls.append(dest) or False
    try:
        assert demo.demo_available() is False          # the UI offers the download…
        assert demo.resolve_demo_recording() is None   # …and the explicit request still tried
        assert calls == [demo.demo_cache_path()], calls
        # …while the offline lookup never reaches the network, whoever asks.
        calls.clear()
        net = len(_URLOPEN_CALLS)
        assert demo.resolve_demo_recording(allow_download=False) is None
        for name, enter, available in STATES:
            enter()
            assert demo.demo_available() is available, name
            win = _window()
            try:
                assert win.centralWidget().demo_btn.text() == _resting(available), name
            finally:
                win.close()
                _settle(0.1)
        assert calls == [], calls
        assert len(_URLOPEN_CALLS) == net, _URLOPEN_CALLS[net:]
    finally:
        demo._try_download_demo = real
        _none_state()
    print("test_the_cli_demo_flag_still_tries_the_network OK "
          "(--demo fetched once; demo_available + the welcome in 3 states: 0 fetches, 0 urlopen)")


# Where the fetch goes, per PACER_DEMO_URL: unset (the pinned release asset), a mirror by name, a
# mirror on this machine with a port (the port is not the host), and a file: mirror, which names no
# host at all.
_MIRRORS = (None, "https://mirror.example.test/demo/pacer-demo.mp4",
            "http://localhost:8765/pacer-demo.mp4", "file:///srv/mirror/pacer-demo.mp4")


@_offline
def test_the_download_tooltip_names_the_host_the_fetch_really_goes_to():
    """DEMO-4 review: the "Get demo" tooltip said the clip downloads "from GitHub" in every case,
    while the dev-only PACER_DEMO_URL mirror sends the fetch somewhere else. The tooltip is the
    app's one statement of where its one fetch goes, so it is held to the URL a planted fetch
    REALLY asks for — counted at the tripwire, not read back from the code that builds the tip: the
    pinned asset's host reads "GitHub", a mirror is named by its host (its URL when it has none)."""
    _none_state()
    saved = os.environ.pop("PACER_DEMO_URL", None)
    said = []
    try:
        for mirror in _MIRRORS:
            if mirror is None:
                os.environ.pop("PACER_DEMO_URL", None)
            else:
                os.environ["PACER_DEMO_URL"] = mirror
            before = len(_URLOPEN_CALLS)
            planted = os.path.join(_SEAMS, "planted", "pacer-demo.mp4")
            assert demo._try_download_demo(planted) is False, "the tripwire let a fetch through"
            asked = _URLOPEN_CALLS[before:]
            assert len(asked) == 1, asked
            host = urlsplit(asked[0]).hostname
            where = "GitHub" if host == "github.com" else (host or asked[0])
            win = _window()
            try:
                tip = win.centralWidget().demo_btn.toolTip()
            finally:
                win.close()
                _settle(0.1)
            assert f"from {where}," in tip, (
                f"the fetch goes to {asked[0]!r} but the tooltip says: {tip!r}")
            if where != "GitHub":
                assert "GitHub" not in tip, (mirror, tip)
            said.append(where)
    finally:
        if saved is None:
            os.environ.pop("PACER_DEMO_URL", None)
        else:
            os.environ["PACER_DEMO_URL"] = saved
        _none_state()
    # The shipping default is the pinned asset, and it still says GitHub.
    assert said[0] == "GitHub", said
    print(f"test_the_download_tooltip_names_the_host_the_fetch_really_goes_to OK ({said})")


def test_the_network_tripwire_is_per_test_put_back_and_still_trips():
    """The tripwire used to be installed when this module was imported and never put back: harmless
    under CTest (one process per file), but one pytest process over several files ran every module
    collected after this one with `urlopen` raising. It is installed per test now (`_offline`), and
    this holds both halves: between tests the urlopen this file found is back, and inside a test a
    planted fetch still trips it — also after a test that failed."""
    assert urllib.request.urlopen is _URLOPEN_AT_IMPORT, (
        f"between tests urlopen is {urllib.request.urlopen!r}, not the one this file found")
    before = len(_URLOPEN_CALLS)

    @_offline
    def planted_fetch():
        assert urllib.request.urlopen is not _URLOPEN_AT_IMPORT, "no tripwire inside a test"
        return demo._try_download_demo(os.path.join(_SEAMS, "planted", "pacer-demo.mp4"))

    assert planted_fetch() is False, "a planted fetch got through"
    assert len(_URLOPEN_CALLS) == before + 1, _URLOPEN_CALLS[before:]
    assert urllib.request.urlopen is _URLOPEN_AT_IMPORT, "the tripwire stayed after a test"

    @_offline
    def failing():
        raise AssertionError("planted failure")

    try:
        failing()
    except AssertionError:
        pass
    assert urllib.request.urlopen is _URLOPEN_AT_IMPORT, "the tripwire stayed after a failed test"
    # Every other test here runs inside it, so none can reach the network.
    bare = [n for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)
            and f is not test_the_network_tripwire_is_per_test_put_back_and_still_trips
            and not getattr(f, "offline", False)]
    assert not bare, f"tests that run without the network tripwire: {bare}"
    print("test_the_network_tripwire_is_per_test_put_back_and_still_trips OK "
          "(put back after a pass and a failure; a planted fetch tripped it once)")


# ==================================================== (b) the button weights
@_offline
def test_the_primary_is_the_wider_button_in_every_state_it_has_a_twin():
    """§6.7(b). Swept across the states and both window sizes, and checked through the ONE label
    swap the row can do (the busy label), because that swap is what inverted the pair in the first
    place. Every state has the twin now (LEFT-24), and the longest thing it says is the download
    label, so the none-state is the one that matters most."""
    try:
        for name, enter, available in STATES:
            enter()
            for size in ((1280, 800), (1920, 1200)):
                win = _window(size)
                try:
                    view = win.centralWidget()
                    assert view.demo_btn is not None, name
                    assert view.demo_btn.text() == _resting(available), name
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


@_offline
def test_the_secondary_button_still_cannot_move_the_row():
    """The D4-06 guarantee, kept: the floor is the WIDEST of every label the button can carry, in
    either direction. The first cut of the D4-06 fix floored it at the busy width alone, which —
    with a busy label SHORTER than "Open demo" — let the button shrink 7 px on the click and slid
    the centred row 3 px.

    AND THE REST IS THE STATE'S OWN LABEL. `_set_demo_busy(False)` used to write "Open demo" back
    unconditionally, which on a fresh launch would turn "Get demo · N MB" into a promise that the
    clip is already here after one busy → rest round trip. Swept in all three states."""
    try:
        for name, enter, available in STATES:
            enter()
            win = _window()
            try:
                view = win.centralWidget()
                before = (view.open_btn.geometry(), view.demo_btn.geometry(),
                          view.drop_zone.geometry())
                for busy in (True, False, True, False):
                    win._set_demo_busy(busy)
                    _settle(0.2)
                    want = BUSY_DEMO_LABEL if busy else _resting(available)
                    assert view.demo_btn.text() == want, (name, busy, view.demo_btn.text())
                    now = (view.open_btn.geometry(), view.demo_btn.geometry(),
                           view.drop_zone.geometry())
                    assert now == before, f"{name}: busy={busy} moved the row: {before} -> {now}"
            finally:
                win.close()
                _settle(0.1)
    finally:
        _none_state()
    print("test_the_secondary_button_still_cannot_move_the_row OK (3 states, resting label kept)")


# ==================================================== (c) the brand moment
@_offline
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


@_offline
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


# The mark at one QT_SCALE_FACTOR, rendered in a child: Qt reads the factor once, when the
# QApplication is built, so this process (DPR 1) cannot render a Retina mark itself. The child
# prints the pixmap's ratio, size and alpha plane; nothing else is imported, and it is jailed.
_MARK_CHILD = """
import json, sys
sys.path.insert(0, sys.argv[1])
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from studio import theme
pm = theme.brand_mark(int(sys.argv[2]))
img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
print("EVIDENCE " + json.dumps({"app_dpr": app.devicePixelRatio(), "dpr": pm.devicePixelRatio(),
      "w": img.width(), "h": img.height(), "alpha": list(bytes(img.constBits())[3::4])}))
"""


def _mark_alpha_at(scale):
    """(evidence, alpha plane) of theme.brand_mark(DROP_GLYPH_PX) at QT_SCALE_FACTOR=`scale`."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, QT_SCALE_FACTOR=scale, QT_QPA_PLATFORM="offscreen",
               PACER_APP_SUPPORT_JAIL="1")
    r = subprocess.run([sys.executable, "-c", _MARK_CHILD, root, str(DROP_GLYPH_PX)], env=env,
                       capture_output=True, text=True, timeout=120)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("EVIDENCE ")), None)
    assert line, f"the mark at QT_SCALE_FACTOR={scale} produced no evidence (rc {r.returncode}):\n" \
                 f"{r.stderr[-2000:]}"
    ev = json.loads(line[len("EVIDENCE "):])
    return ev, np.array(ev.pop("alpha"), np.uint8).reshape(ev["h"], ev["w"])


@_offline
def test_the_mark_is_whole_on_a_retina_screen():
    """QA3-BRANDMARK. At DPR 2 the mark was drawn at twice its size into its own pixmap, so the
    welcome showed two chevron stubs cut off at the right and the bottom: 68 and 14 ink pixels on
    the pixmap's last column and row, where DPR 1 has 6 and 0 (the front chevron's round cap is
    the one stroke that touches an edge). Held two ways: the edges (only the cap, scaled), and the
    shape — the DPR-2 render averaged down 2x2 must be the DPR-1 render, which also catches a mark
    drawn too SMALL, where the edge counts alone would pass."""
    ev1, a1 = _mark_alpha_at("1")
    ev2, a2 = _mark_alpha_at("2")
    # An ignored QT_SCALE_FACTOR would silently compare DPR 1 with itself.
    assert (ev1["app_dpr"], ev2["app_dpr"]) == (1.0, 2.0), (ev1, ev2)
    assert ev2["dpr"] == 2.0 and a2.shape == (2 * DROP_GLYPH_PX, 2 * DROP_GLYPH_PX), (ev2, a2.shape)
    assert a1.shape == (DROP_GLYPH_PX, DROP_GLYPH_PX), a1.shape

    def edges(a):
        ink = a > 0
        return int(ink[:, -1].sum()), int(ink[-1, :].sum())

    (r1, b1), (r2, b2) = edges(a1), edges(a2)
    cap = 2  # device px: the cap's antialiased fringe does not scale exactly with the ratio
    assert r2 <= 2 * r1 + cap and b2 <= 2 * b1 + cap, (
        f"the DPR-2 mark runs off its pixmap: right/bottom edge ink {r2}/{b2} px, "
        f"DPR 1 has {r1}/{b1} (allowed {2 * r1 + cap}/{2 * b1 + cap})")
    down = a2.astype(float).reshape(DROP_GLYPH_PX, 2, DROP_GLYPH_PX, 2).mean(axis=(1, 3))
    diff = float(np.abs(down - a1.astype(float)).mean())
    assert diff < 8.0, f"the DPR-2 mark is not the DPR-1 mark's shape: mean |alpha| diff {diff:.1f}"
    print(f"test_the_mark_is_whole_on_a_retina_screen OK (edge ink DPR 1 {r1}/{b1}, "
          f"DPR 2 {r2}/{b2}; shape diff {diff:.1f}/255)")


def _run_all():
    test_a_fresh_launch_offers_the_demo_and_says_the_click_downloads_it()
    test_the_env_var_lights_the_button_up_and_the_click_works_end_to_end()
    test_the_cached_clip_is_the_other_state_that_offers_the_button()
    test_a_fresh_launch_click_downloads_once_and_a_failure_keeps_the_door()
    test_the_unavailable_copy_names_the_failed_download_and_the_door_that_works()
    test_the_cli_demo_flag_still_tries_the_network()
    test_the_download_tooltip_names_the_host_the_fetch_really_goes_to()
    test_the_network_tripwire_is_per_test_put_back_and_still_trips()
    test_the_primary_is_the_wider_button_in_every_state_it_has_a_twin()
    test_the_secondary_button_still_cannot_move_the_row()
    test_the_drop_glyph_is_the_apps_own_mark_and_it_composites()
    test_the_mark_is_the_icons_geometry_not_a_second_copy_of_it()
    test_the_mark_is_whole_on_a_retina_screen()
    print("ALL WELCOME FIRST-TOUCH TESTS OK")


if __name__ == "__main__":
    _run_all()
