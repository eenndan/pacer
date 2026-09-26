"""LIFE-3 (QA 2026-09-26): a reload and a compare exit must RELEASE what they replace.

THE PREMISE. Over a compressed evening (opens, compare toggles, exports) the process RSS climbed
693 -> 1,336 -> 1,461 -> 1,556 MB, and 10 compare enter/exit cycles took it 670 -> 1,088 MB. The
shape a leak of ours would have is the one this file pins shut: something long-lived (the window,
a controller, a module global, a lambda connected to an app-lifetime signal) keeping the OUTGOING
`Session` or `CentralView` reachable, so every open would add a whole recording's arrays and panels.

WHAT WAS MEASURED (fix wave J, `main` ffd921a: the real StudioWindow on four of the owner's
recordings, driven from inside `app.exec()`; the table is in studio/docs/module-notes.md, "Memory
over a long session"):
  * after each of 32 opens the old Session and CentralView were dead (weakrefs cleared), the
    per-class census of Qt/PySide objects was 1:1 per rebuild, and `leaks` found 0 leaks;
  * RSS plateaued (open-only ~1.65-1.67 GB from the fifth cycle on) while the physical footprint
    stayed flat (~200 MB; ~256 MB compare-only). The climb is libmalloc keeping FREED pages
    resident as reusable: MALLOC_MEDIUM 848 MB resident, 22 MB dirty. Not ours, and not memory the
    system charges the app.

So there was nothing to fix in the app, and this file keeps it that way. Each test holds weak
references to what a gesture replaces, lets the event loop do its teardown, and asserts they are
gone — naming the holders when they are not, because "something still holds the old view" is
useless without the something.

ITS FIRST RUN FAILED — ON THE FIXTURE. `_studiowindow_with_view` discarded the CentralView its
builder makes without deleting it, and a parentless widget whose children hold Python lambdas is
never collected: it kept the session alive ("held by CompareController; ScrubController"). The
fixture now deletes it (tests/test_central_view_realqt.py).

THE EVENT-LOOP TRAP (harness, not product). `setCentralWidget` and the compare teardown release the
old widgets with `deleteLater()`, and a DeferredDelete posted outside any running loop is never
delivered by `processEvents()` — a probe that only pumps sees a "leak" that the real app, inside
`exec()`, never has. `_settle` delivers them the way the real loop does.

PROVEN ABLE TO FAIL: a lambda capturing the view, connected to an app-lifetime signal in
`CentralView.__init__` (`QApplication.instance().focusChanged.connect(lambda *_: self)`), fails the
reload test naming the lambda; the same capture of pane B in `VideoView.set_compare` fails the
compare test.

Run: QT_QPA_PLATFORM=offscreen PACER_NO_MEDIA=1 python tests/test_reload_memory.py
"""
import gc
import os
import sys
import weakref

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The inert media triplet (no decoder/audio device) — set BEFORE importing the studio widgets.
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# The shared fixtures: a REAL StudioWindow over the synthetic two-lap session with a REAL
# CentralView (its production __init__), plus the stub load that drives the real async reload path.
# Importing the module diverts every app-support seam it writes through, before any window exists.
import test_load_lifecycle as _lc  # noqa: E402


def _settle(rounds: int = 5) -> None:
    """What `exec()` does between gestures: run queued slots, deliver the deferred deletes, then
    collect cycles (a PySide wrapper and its Python attributes can sit in one)."""
    for _ in range(rounds):
        _APP.processEvents()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _describe(r) -> str:
    code = getattr(r, "__code__", None)
    if code is not None:
        return f"{code.co_name} at {os.path.basename(code.co_filename)}:{code.co_firstlineno}"
    return type(r).__qualname__


def _owner(container) -> str:
    """Name what owns a dict / tuple on the reference path: an object's __dict__ (by its type), or
    a function's defaults or closure (by the function)."""
    for o in gc.get_referrers(container):
        if getattr(o, "__dict__", None) is container:
            return type(o).__qualname__
        if any(container is getattr(o, a, None) for a in ("__kwdefaults__", "__defaults__", "__closure__")):
            return _describe(o)
    return "?"


def _holders(obj) -> str:
    """Who still references `obj` — the diagnosis a failure needs. An attribute is named by its
    owner's type; a default argument or a closure cell by the function holding it (a lambda
    connected to a long-lived signal is the usual culprit)."""
    here = sys._getframe()
    lines = []
    for r in gc.get_referrers(obj):
        if r is here or type(r) is type(here):
            continue
        if type(r).__name__ == "cell":
            tuples = [t for t in gc.get_referrers(r) if isinstance(t, tuple)]
            lines.append("a closure of " + (", ".join(_owner(t) for t in tuples) or "?"))
        elif isinstance(r, dict):
            keys = [k for k, v in r.items() if v is obj][:3]
            lines.append(f"{_owner(r)}.{'/'.join(map(str, keys))}" if keys else f"dict ({len(r)} items)")
        else:
            lines.append(_describe(r))
    return "; ".join(lines[:8]) or "no Python referrer (a C++ owner?)"


def _assert_released(refs: dict, gesture: str) -> None:
    alive = [name for name, r in refs.items() if r() is not None]
    if alive:
        report = [f"{gesture} kept the OUTGOING {', '.join(alive)} alive:"]
        for name in alive:
            report.append(f"  {name}: held by {_holders(refs[name]())}")
        raise AssertionError("\n".join(report))


def test_a_reload_releases_the_outgoing_session_and_view():
    """Open another recording over a loaded one — the real `_load` -> worker -> queued
    `_on_session_loaded` -> `_build_ui` swap — and the outgoing Session and CentralView must be
    unreachable once the event loop has run its teardown. The incoming session is a stub: what is
    under test is what the swap lets go of, not what it builds."""
    win, view = _lc._window()
    refs = {"Session": weakref.ref(win.session), "CentralView": weakref.ref(view),
            "PlayerPane": weakref.ref(view.video.pane)}
    del view
    finished = []
    win.loadFinished.connect(lambda: finished.append(True))
    try:
        with _lc._StubLoad(lambda paths: _lc._stub_session()), _lc._SwapView(_lc._StubView):
            win._load(["/tmp/pacer-reload-memory-GX010099.MP4"])
            assert _lc._pump(30.0, lambda: bool(finished)), "the reload never reported finished"
        assert win.session is not None and refs["Session"]() is not win.session, \
            "the reload never committed the incoming session"
        _settle()
        _assert_released(refs, "a reload")
    finally:
        _lc._teardown(win)
    print("test_a_reload_releases_the_outgoing_session_and_view OK")


def test_leaving_compare_releases_pane_b():
    """Enter and leave compare twice on the real view: each exit must release pane B (its player,
    video widget and overlay) — the premise's 10-cycle climb was this gesture. Twice, because the
    second entry rebuilds pane B and a leak that only a re-entry exposes is still a leak."""
    win, view = _lc._window()
    try:
        video = view.video
        assert video.compare_btn.isEnabled(), "two valid laps -> the compare toggle is enabled"
        for cycle in range(2):
            video.compare_btn.click()                  # the real gesture: compareToggled(True)
            _settle(2)
            pane_b = video.secondary
            assert pane_b is not None, f"cycle {cycle}: compare on mounted no pane B"
            refs = {"pane B": weakref.ref(pane_b), "pane B's g-meter": weakref.ref(pane_b.gmeter)}
            del pane_b
            video.compare_btn.click()                  # ...and off
            _settle()
            assert video.secondary is None, f"cycle {cycle}: compare off left pane B mounted"
            _assert_released(refs, f"leaving compare (cycle {cycle})")
    finally:
        _lc._teardown(win)
    print("test_leaving_compare_releases_pane_b OK")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} RELOAD-MEMORY TESTS PASSED")


if __name__ == "__main__":
    _run_all()
