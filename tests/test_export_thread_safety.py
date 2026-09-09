"""The export render path may not construct a QWidget — it does not run on the GUI thread.

THE DEFECT. `workers.VideoExportWorker.run()` builds `export_video.Renderer` **on a QThread**.
`Renderer.__init__` builds an `OverlayPainter`, and that constructor used to do this:

    self._dial = gmeter_overlay.GMeterOverlay()          # a QWidget. On the worker thread.

`GMeterOverlay` is not an incidental widget either — it is a frameless translucent `Qt.Tool`
TOP-LEVEL window (it has to be, to composite above the native video surface on macOS). Qt widgets
may only be created and destroyed on the GUI thread; every video export created one off-thread and
destroyed it there too, and a VideoToolbox encode that fell back to libx264 built a SECOND
`Renderer`, so it did it twice. It never crashed because the dial is never `show()`n, so nothing
ever asked the window system for a backing store — i.e. it survived by accident, not by design.
This is the same shape tests/test_compare_lifecycle.py exists for, where it *did* SIGSEGV.

WHY NO EXISTING TEST COULD CATCH IT. Every export test builds its `OverlayPainter` on the main
thread, where constructing a widget is perfectly legal. The bug is not in what the code computes —
the exporter only ever needed the dial's BOOKKEEPING (the EMA'd dot, the per-lap hull, the robust
cardinal peaks) and only ever called the free function `paint_dial` — it is in what kind of object
it used to keep it in. So the guard has to be about CONSTRUCTION, not about output.

TWO GUARDS, because either alone is weak:

  * a STRUCTURAL one — walk the call graph reachable from `VideoExportWorker.run`, `Renderer` and
    `OverlayPainter` through everything they construct inside `studio/`, and require that no
    QWidget subclass is ever instantiated. It has a positive control (`player_pane.PlayerPane`,
    which builds widgets by design, must be FLAGGED) so it cannot pass by resolving nothing.
  * a BEHAVIOURAL one — actually build an `OverlayPainter` on a real QThread, drive frames through
    it, paint one, and require the process's widget census to be unchanged.

Run: QT_QPA_PLATFORM=offscreen python tests/test_export_thread_safety.py
"""
import ast
import inspect
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()   # module scope, before any painting: the SHIPPED face

import numpy as np  # noqa: E402
from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from studio import export_video as ev  # noqa: E402
from studio import gmeter_overlay, player_pane, workers  # noqa: E402


# ------------------------------------------------------------------ the structural guard
def _resolve(node, mod):
    """The runtime object an `ast` call target names, or None.

    Resolution is through the DEFINING MODULE's namespace, which covers both forms the code uses:
    a bare `Renderer(...)` (a module global) and a qualified `gmeter_overlay.DialFilter(...)` (an
    imported module, then its attribute). `self.x.y(...)` deliberately resolves to None — an
    instance attribute is not knowable statically, and it does not need to be: the object it holds
    was CONSTRUCTED somewhere this walk already visits, and visiting a class visits all its
    methods."""
    if isinstance(node, ast.Name):
        return getattr(mod, node.id, None)
    if isinstance(node, ast.Attribute):
        base = _resolve(node.value, mod)
        return None if base is None else getattr(base, node.attr, None)
    return None


def _in_studio(obj) -> bool:
    return str(getattr(obj, "__module__", "")).split(".")[0] == "studio"


def _walk_constructions(roots):
    """Follow `roots` (classes/functions) through every studio class-or-function they call, and
    return (qwidget_hits, visited). A class is walked WHOLE — all of its methods — because
    constructing one is what puts its behaviour on this thread.

    Returns hits as readable "where -> what" strings, and the set of visited qualnames so a caller
    can assert the walk reached the interesting places instead of falling off after one hop."""
    hits, visited, queue = [], set(), list(roots)
    while queue:
        obj = queue.pop()
        mod = inspect.getmodule(obj)
        key = f"{getattr(mod, '__name__', '?')}.{obj.__qualname__}"
        if key in visited or mod is None:
            continue
        visited.add(key)
        try:
            src, first_line = inspect.getsourcelines(obj)
        except (OSError, TypeError):        # no source (C extension / dynamically built)
            continue
        tree = ast.parse(textwrap.dedent("".join(src)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _resolve(node.func, mod)
            if target is None:
                continue
            if isinstance(target, type) and issubclass(target, QWidget):
                hits.append(f"{key} (line {first_line + node.lineno - 1}) constructs "
                            f"{target.__module__}.{target.__name__}, a QWidget")
            elif _in_studio(target) and (isinstance(target, type) or inspect.isfunction(target)):
                queue.append(target)
    return hits, visited


def test_the_guard_flags_a_widget_when_there_is_one():
    """The positive control. `PlayerPane` builds widgets by design (it OWNS the live g-meter
    overlay), so the same walk over it must report them. Without this, a walk that silently
    resolved nothing would report "no widgets" for every input and pass forever."""
    hits, visited = _walk_constructions([player_pane.PlayerPane])
    assert hits, ("the walk found no QWidget construction in player_pane.PlayerPane, which builds "
                  "several — the guard below would pass vacuously")
    assert any("GMeterOverlay" in h for h in hits), hits
    print(f"ok  positive control: {len(hits)} QWidget constructions found in PlayerPane "
          f"across {len(visited)} visited objects")


def test_the_export_render_path_constructs_no_qwidget():
    """The guard itself, rooted at the thread entry point rather than at a class name, so it is
    about the code that actually runs off the GUI thread.

    `QImage` / `QPixmap` / `QPainter` / `QFont` are fine and are expected here: they are paint
    devices and value types, not widgets. The line this holds is `OverlayPainter.__init__`, which
    keeps the dial's filtering in a `gmeter_overlay.DialFilter` (no Qt base class at all) rather
    than in a `GMeterOverlay` window."""
    hits, visited = _walk_constructions(
        [workers.VideoExportWorker.run, ev.Renderer, ev.OverlayPainter])
    assert hits == [], (
        "the video export runs on VideoExportWorker's QThread, and Qt widgets may only be created "
        "and destroyed on the GUI thread:\n  " + "\n  ".join(hits))
    # The walk must have gone deep enough to be able to see the old defect: `OverlayPainter` ->
    # the dial's state object -> the shared paint routine. If the recursion ever stops short, this
    # fails loudly instead of quietly guarding nothing.
    for needed in ("studio.export_video.OverlayPainter",
                   "studio.gmeter_overlay.DialFilter",
                   "studio.gmeter_overlay.paint_dial",
                   "studio.export_video._MapInset"):
        assert needed in visited, (f"the call-graph walk never reached {needed} — it can no longer "
                                   f"see where the worker-thread widget used to be built")
    assert len(visited) > 20, len(visited)
    print(f"ok  no QWidget is constructed anywhere the export render path reaches "
          f"({len(visited)} studio objects visited)")


def test_the_dial_state_holder_is_not_a_qt_object_at_all():
    """`DialFilter` is the extraction: the bookkeeping the exporter needed, with no Qt base class,
    so neither thread affinity nor the GUI-thread rule applies to it."""
    from PySide6.QtCore import QObject

    d = gmeter_overlay.DialFilter()
    assert not isinstance(d, QWidget) and not isinstance(d, QObject), type(d).__mro__
    assert type(d).__mro__[1:] == (object,), type(d).__mro__
    print("ok  DialFilter is a plain object (mro: DialFilter -> object)")


# ------------------------------------------------------------------ the behavioural guard
class _Stub:
    """The accessors `OverlayPainter` reaches for, with a g signal so the dial really accumulates.
    Same shape as tests/test_export_pill_budget.py's Stub."""
    has_gmeter = True

    def __init__(self):
        self.tt = np.linspace(0.0, 20.0, 201)
        self.tv = np.full(201, 60.0)
        self.tx = np.linspace(0.0, 100.0, 201)
        self.ty = np.zeros(201)
        self._lap = 3
        self._w = (2.0, 18.0)

    def lap_window(self, lap_id):
        return self._w if lap_id == self._lap else None

    def lap_at_time(self, t):
        return self._lap if self._w[0] <= t < self._w[1] else None

    def index_at_time(self, t):
        return int(min(max(np.searchsorted(self.tt, t), 0), len(self.tt) - 1))

    def delta_at_lap(self, lap_id, t):
        return 0.25 if lap_id == self._lap else None

    def g_at_time(self, t):
        return (0.6, -0.35, 0.69)

    def gmeter_source(self):
        return "accl"

    def gmeter_long_source(self):
        return "gps"

    def lap_trace_xy(self, lap_id):
        return (self.tx, self.ty) if lap_id == self._lap else None


class _PainterThread(QThread):
    """Do off the GUI thread exactly what `VideoExportWorker.run` does: build the painter, advance
    it frame by frame, and composite a frame."""

    def __init__(self, session, spec):
        super().__init__()
        self._session, self._spec = session, spec
        self.error = None
        self.states = []
        self.dial_type = None
        # A STRONG REFERENCE the census depends on. Left as a local, the painter — and any widget
        # it built — is collected the moment `run()` returns, so a census taken afterwards sees
        # nothing and the guard passes against the very build it is meant to fail (measured: it
        # did). Constructed-and-destroyed off the GUI thread IS the defect; keeping the painter
        # alive is what makes the construction observable.
        self.painter = None
        self.widgets_after_build = None

    def run(self):
        try:
            self.painter = ev.OverlayPainter(self._session, self._spec, 640, 360, 30.0)
            self.widgets_after_build = len(QApplication.allWidgets())
            self.dial_type = type(self.painter._dial)
            vals = None
            for i in range(60):
                vals = ev.overlay_values_at(self._session, 1.0 + i / 30.0, self._spec)
                self.states.append(self.painter.advance_and_snapshot(vals))
            img = QImage(640, 360, QImage.Format_RGB888)
            img.fill(0)
            self.painter.paint_frame_with_state(img, vals, self.states[-1])
        except BaseException as exc:            # noqa: BLE001 — reported on the GUI thread
            self.error = exc


def test_building_and_driving_the_painter_off_thread_creates_no_widget():
    """The census. Build + drive + paint on a real QThread and require that the process gained no
    QWidget of any kind — the defect's whole signature was two of them per export.

    It also checks the dial still WORKS off-thread (the peaks grow, the envelope fills), because a
    guard that passed by having the painter do nothing would be worthless."""
    session = _Stub()
    spec = ev.ExportSpec(src_path="/x.MP4", out_path="/o.MP4", lap_id=session._lap,
                         t0=1.0, t1=4.0, config=ev.OverlayConfig(out_height=360),
                         lead_in=1.0, lead_out=0.0)
    before = set(QApplication.allWidgets())
    before_top = set(QApplication.topLevelWidgets())

    th = _PainterThread(session, spec)
    th.start()
    assert th.wait(30000), "the off-thread painter did not finish within 30 s"

    assert th.error is None, f"the painter failed on the worker thread: {th.error!r}"
    built = set(QApplication.allWidgets()) - before
    built_top = set(QApplication.topLevelWidgets()) - before_top
    assert th.widgets_after_build == len(before), (
        f"the widget census went {len(before)} -> {th.widgets_after_build} while OverlayPainter "
        f"was being constructed ON THE WORKER THREAD")
    assert not built, f"the export painter created {len(built)} QWidget(s) off the GUI thread: " \
                      f"{[type(w).__name__ for w in built]}"
    assert not built_top, [type(w).__name__ for w in built_top]
    assert not isinstance(th.painter._dial, QWidget), type(th.painter._dial).__mro__
    assert th.dial_type is gmeter_overlay.DialFilter, th.dial_type
    # ...and it really ran: the lap frames carry a grown envelope and non-zero cardinal peaks.
    last = th.states[-1]
    assert last.have and last.seen and len(last.hull_pts) > 10, (last.have, len(last.hull_pts))
    assert last.peak_right > 0.1 and last.peak_fwd > 0.1, (last.peak_right, last.peak_fwd)
    # ...including the dot's TRAIL, which the redesign added to the same filter and which the
    # painter therefore also has to build off-thread. (The provenance tag that used to be asserted
    # here is gone from the dial entirely — it is a sentence on the on-screen toggle's tooltip now;
    # tests/test_gmeter_overlay.py pins both halves of that move.)
    assert len(last.trail) > 2 and last.trail[-1] == (last.fx, last.fy), last.trail
    print(f"ok  60 frames built+driven+painted on a QThread; widget census unchanged "
          f"(peaks R={last.peak_right:.2f} F={last.peak_fwd:.2f}, hull {len(last.hull_pts)} pts, "
          f"trail {len(last.trail)} pts)")


def test_the_live_widget_still_owns_the_same_filter(_=None):
    """The other half of the extraction: the on-screen dial must be unchanged, i.e. it drives the
    SAME bookkeeping the exporter does. Same input sequence, same snapshot, field for field."""
    widget = gmeter_overlay.GMeterOverlay()
    plain = gmeter_overlay.DialFilter()
    seq = [(0.5, -0.2, 0.54), (-0.9, 0.3, 0.95), (0.1, 0.8, 0.81), None, (1.4, -1.1, 1.78)]
    for holder in (widget, plain):
        holder.set_lap(4)
        for g in seq * 20:
            holder.set_g(g)
        holder.set_lap(5)
        for g in seq * 5:
            holder.set_g(g)
    a, b = widget._dial_state(), plain.snapshot()
    assert a == b, f"the live dial and the exporter's filter diverged:\n  {a}\n  {b}"
    assert len(a.hull_pts) > 10 and a.peak_right > 0.1, a
    widget.deleteLater()
    print("ok  the live widget's dial state is the exporter's, field for field")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} export thread-safety tests passed")
