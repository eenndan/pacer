"""S5-01 — the compare toggle must survive being used, and the mounting order that made it not.

THE BUG. `_PaneCell` wraps the PRIMARY `PlayerPane` — a widget that is, at that moment, LIVE in
`VideoView._stage_lay` — in a nested `QHBoxLayout` that carries the video's splitter-handle inset.
It built that row and filled it BEFORE handing it to the cell's own layout:

    video_row = QHBoxLayout()            # free-standing: parentWidget() is None
    video_row.addWidget(self.pane)       # <- half a move
    lay.addLayout(video_row, 1)          # <- the other half, several statements later

`QLayout::addWidget` delegates to `QLayout::addChildWidget`, which does two things: it pulls the
widget out of whatever layout currently holds it (`removeWidgetRecursively`) and it reparents it
onto the layout's `parentWidget()`. A free-standing QLayout has no `parentWidget()`, so the second
half cannot run and only the first half happens — measured here in
`test_free_standing_layout_addwidget_is_half_a_move`: the pane's item is deleted out of the live
`_stage_lay` (`indexOf` 0 -> -1, `count()` 1 -> 0) while `pane.parent()` still points at the old
stage. Qt prints no warning. `addLayout`'s `reparentChildWidgets` finishes the move afterwards, so
the widget tree LOOKS right the moment `__init__` returns, and every structural assertion in the
suite passed.

What it actually cost: entering and leaving compare repeatedly SIGSEGVd. Through the full app — a
real StudioWindow on real footage, 200 enter/exit cycles — it killed 5 of 6 processes on the broken
build and 0 of 6 on the fixed one. The subprocess below reproduces the same fault with no session
and no footage at all. The crash always landed inside PySide's per-type metaobject lookup while
CONSTRUCTING the next QObject (`SignalManager::retrieveMetaObject` dereferencing the null that
`retrieveTypeUserData` returned), so the backtrace never named compare, and the cycle it died on
ranged from 11 to 556. It is a probabilistic fault — the allocator has to hand back an address it
has already used — which is why the guard below is a subprocess loop rather than an assertion.

The test is therefore two things:
  * a DETERMINISTIC source guard — no free-standing layout in `studio/video_view.py` may be filled
    before it is mounted — plus the Qt behaviour that rule exists for, pinned as a measurement;
  * a PROBABILISTIC crash guard — a subprocess that toggles compare and must exit 0.

`PACER_NO_MEDIA=1` gives the production widget tree with an inert media triplet, so this needs no
footage and no session: the crash is in the widget lifecycle, not in playback.
Run: QT_QPA_PLATFORM=offscreen python tests/test_compare_lifecycle.py
"""
import ast
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget  # noqa: E402

from studio import chapters, theme  # noqa: E402
from studio.player_pane import PlayerPane  # noqa: E402
from studio.video_view import PaneSpec, VideoView  # noqa: E402

theme.apply_theme(_APP)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_VIEW = os.path.join(REPO, "studio", "video_view.py")

#: How many enter/exit cycles each subprocess drives, and how many run AT ONCE. Both numbers are
#: measured against the broken build, not guessed: 600 cycles killed 4 of 6 solo runs, 1500 killed
#: 6 of 6 solo and 2 of 3 when three ran concurrently — call the per-process miss rate ~1 in 9.
#: Three concurrent processes put a broken build's chance of slipping through near 1 in 700, for
#: ~90 s of wall time (they overlap, so this costs little more than one process would).
#: CONCURRENT on purpose: the fault needs the allocator to hand back an address it has already
#: used, and a loaded machine reaches that sooner.
CYCLES = 1500
PROCS = 3

_LAYOUTS = {"QHBoxLayout", "QVBoxLayout", "QGridLayout", "QFormLayout", "QStackedLayout"}
_FILLERS = {"addWidget", "insertWidget", "addLayout", "insertLayout", "addItem"}


# ------------------------------------------------------------------ the Qt behaviour, measured
def test_free_standing_layout_addwidget_is_half_a_move():
    """A free-standing QLayout given an ALREADY-LAID-OUT widget drops it from its current layout
    and does NOT reparent it. This is the mechanism the source guard below exists for; pinning it
    means the guard names a measured hazard rather than a style preference."""
    stage = QWidget()
    stage_lay = QVBoxLayout(stage)
    pane = QWidget()
    stage_lay.addWidget(pane, 1)
    assert stage_lay.indexOf(pane) == 0, "setup: the pane starts in the stage layout"
    assert stage_lay.count() == 1

    free_row = QHBoxLayout()                       # never mounted -> parentWidget() is None
    assert free_row.parentWidget() is None
    free_row.addWidget(pane)

    assert stage_lay.indexOf(pane) == -1, (
        "Qt changed: addWidget on a free-standing layout no longer removes the widget from its "
        "current layout — re-derive the guard below from what it does now")
    assert stage_lay.count() == 0
    assert pane.parent() is stage, (
        "Qt changed: addWidget on a free-standing layout now reparents too — the half-move this "
        "guard exists for is gone")

    # ...and mounting the row FIRST makes the same call one complete move.
    host = QWidget()
    host_lay = QVBoxLayout(host)
    stage_lay.addWidget(pane, 1)                   # put it back in the stage
    row = QHBoxLayout()
    host_lay.addLayout(row, 1)                     # mounted BEFORE it is filled
    assert row.parentWidget() is host
    row.addWidget(pane)
    assert stage_lay.indexOf(pane) == -1
    assert pane.parent() is host, "a mounted row reparents in the same call"


# ------------------------------------------------------------------ the source guard
def _fill_before_mount(path):
    """Every `<layout>.addWidget(...)`-family call in `path` that runs while the layout is still
    free-standing, labelled BY ITS OWNING FUNCTION so an argument about it names a decision rather
    than a line number.

    A layout is free-standing when it is built with no parent — `QVBoxLayout()` rather than
    `QVBoxLayout(widget)` — and stays that way until some other layout takes it
    (`addLayout`/`insertLayout`) or a widget adopts it (`setLayout`)."""
    with open(path) as fh:
        tree = ast.parse(fh.read(), path)
    hits = []
    for func in [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        free = {n.targets[0].id: n.lineno for n in ast.walk(func)
                if isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name) and n.value.func.id in _LAYOUTS
                and not n.value.args}
        if not free:
            continue
        mounted = {}
        for n in ast.walk(func):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("addLayout", "insertLayout", "setLayout")):
                for arg in n.args:
                    if isinstance(arg, ast.Name) and arg.id in free:
                        mounted.setdefault(arg.id, n.lineno)
        for n in ast.walk(func):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id in free and n.func.attr in _FILLERS):
                mount = mounted.get(n.func.value.id)
                if mount is None or n.lineno < mount:
                    hits.append(f"{func.name}(): {n.func.value.id}.{n.func.attr}(...) at line "
                                f"{n.lineno} — " + ("never mounted" if mount is None
                                                    else f"mounted only at line {mount}"))
    return hits


def test_video_view_mounts_every_layout_before_it_fills_it():
    """`studio/video_view.py` is the one file that mounts widgets which are ALREADY LIVE in another
    layout (the primary pane moves between the stage and a compare cell on every toggle), so it is
    the file where the half-move above is reachable. Every layout here is mounted first.

    Scoped to this file on purpose. The same shape exists in builder code elsewhere in `studio/`
    (`widgets.PanelToolbar._mount` is the one that takes caller-supplied widgets), where it is
    latent rather than live because those callers pass freshly-constructed, unparented widgets."""
    hits = _fill_before_mount(VIDEO_VIEW)
    assert hits == [], (
        "a layout in studio/video_view.py is filled before it is mounted; this file moves a LIVE "
        "widget between layouts, where that is half a move (see the test above):\n  "
        + "\n  ".join(hits))


# ------------------------------------------------------------------ the crash guard
_CHILD = r"""
import os, sys
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, {repo!r})
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from studio import chapters, theme
theme.apply_theme(app)
from studio.video_view import PaneSpec, VideoView

cmap = chapters.ChapterMap(["/tmp/pacer_lifecycle_ch%d.MP4" % i for i in range(3)], [1700.0] * 3)
view = VideoView(cmap)
view.resize(700, 520)
view.show()
a = PaneSpec(0, (10.0, 80.0), "lap 0", choices=[0, 1], choice_labels=["lap 0", "lap 1"])
b = PaneSpec(1, (90.0, 160.0), "lap 1", choices=[0, 1], choice_labels=["lap 0", "lap 1"])
for _ in range({cycles}):
    view.set_compare(a, b)
    app.processEvents()
    view.exit_compare()
    app.processEvents()
    app.processEvents()
print("OK")
"""


def test_toggle_compare_repeatedly_does_not_crash():
    """Enter and leave compare CYCLES times in each of PROCS SUBPROCESSES, and require clean exits.

    Subprocesses because the failure is a SIGSEGV: it takes the whole interpreter with it, so an
    in-process assertion could never run and a `try` could never see it. The exit code IS the
    assertion. Each cycle destroys and rebuilds the secondary pane, both cells and the splitter,
    and moves the primary pane between the stage and its cell — the widget-lifecycle churn the
    half-move above corrupted."""
    src = _CHILD.format(repo=REPO, cycles=CYCLES)
    procs = [subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(PROCS)]
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=600)
        results.append((proc.returncode, out, err))
    bad = [(i, rc, out, err) for i, (rc, out, err) in enumerate(results) if rc != 0 or "OK" not in out]
    assert not bad, "\n".join(
        f"compare-toggle subprocess {i + 1}/{PROCS} exited {rc} (-11 / 139 = SIGSEGV) after up to "
        f"{CYCLES} enter/exit cycles.\nstdout: {out[-1500:]}\nstderr: {err[-3000:]}"
        for i, rc, out, err in bad)


# ------------------------------------------------------------------ the tree is still right
def test_compare_cell_leaves_the_primary_pane_in_its_cell():
    """The mounting order change must not move the pane anywhere else: entering compare puts the
    primary pane inside cell A (under the SPACE_XS video inset the nested row carries), and leaving
    puts it back in the stage layout."""
    cmap = chapters.ChapterMap([f"/tmp/pacer_cell_ch{i}.MP4" for i in range(2)], [1700.0] * 2)
    view = VideoView(cmap)
    view.resize(700, 520)
    view.show()
    _APP.processEvents()
    pane = view.pane
    assert view._stage_lay.indexOf(pane) == 0, "setup: single mode keeps the pane in the stage"

    spec_a = PaneSpec(0, (10.0, 80.0), "lap 0", choices=[0, 1], choice_labels=["lap 0", "lap 1"])
    spec_b = PaneSpec(1, (90.0, 160.0), "lap 1", choices=[0, 1], choice_labels=["lap 0", "lap 1"])
    view.set_compare(spec_a, spec_b)
    _APP.processEvents()
    assert pane.parent() is view._cell_a, "the primary pane lives in cell A while comparing"
    assert view._stage_lay.indexOf(pane) == -1
    assert isinstance(pane, PlayerPane)
    # the inset the nested row exists for is still applied
    row = view._cell_a.layout().itemAt(1).layout()
    assert row is not None and row.indexOf(pane) == 0
    assert row.contentsMargins().left() == theme.SPACE_XS
    assert row.contentsMargins().right() == theme.SPACE_XS

    view.exit_compare()
    _APP.processEvents()
    assert view._stage_lay.indexOf(pane) == 0, "leaving compare returns the pane to the stage"
    assert view._cell_a is None and view._cell_b is None and view._splitter is None


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("compare-lifecycle guards:", "OK" if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)
