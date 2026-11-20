"""File ▸ Export overlay video… opens the export picker — fired as the REAL menu action, and as ⌘K.

THE DEFECT (0.17.0 – 0.18.0). The menu item connects `triggered(bool)` straight to
`ExportController.export_overlay_video`, and that method grew optional POSITIONAL parameters
(`pass_`, `preset_pass`) for the corner-pass export. PySide hands a slot the signal's arguments
whenever it has positional room for them, so every click delivered `checked=False` as `pass_`, and
`pass_[0]` raised "TypeError: 'bool' object is not subscriptable" before the picker opened — from
the menu and from its ⌘K row, which triggers the same action. Every test called the method
directly, with no arguments, and so never met the signal.

THE CHECKS.
  * The real QAction, triggered on the demo in a real StudioWindow: the overlay picker opens under
    its real `exec()` (a timer closes it), and no exception reaches `sys.excepthook` — which is
    where PySide sends one raised in a slot.
  * The same through the palette's own row (`command_palette.entries` → `Entry.run`).
  * A structural guard over studio/: a `triggered` / `clicked` / `toggled` signal connected
    STRAIGHT to a callable whose first positional parameter is not a "checked"-style name is a
    finding, because that parameter receives the signal's bool. Targets are resolved by name — a
    `self.method` in the enclosing class first, otherwise every studio def of that name — and
    targets that resolve to no studio def (Qt slots, signals, a callable passed in as a variable)
    are not judged. QShortcut / QComboBox `activated` is left out: the one carries nothing and the
    other an int index this guard cannot type.
Run: QT_QPA_PLATFORM=offscreen python tests/test_export_menu_entry.py
"""
import ast
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from studio import command_palette, library, prefs  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
library._app_support_dir = prefs._app_support_dir = (lambda: _TMP.name)
_DEMO: dict = {}
_PICKER = "Export overlay video"


def _settle(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


def _window():
    if "win" not in _DEMO:
        from studio.app import StudioWindow
        from studio.dev import make_demo as md
        for k in ("critical", "warning", "information"):
            setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: QMessageBox.Ok))
        path = os.path.join(_TMP.name, "GX019000.MP4")
        md.make(path, render=False)
        win = StudioWindow([path])
        deadline = time.time() + 60
        while win.view is None and time.time() < deadline:
            _settle(0.02)
        assert win.view is not None, "the demo did not load"
        win.resize(1280, 800)
        win.show()
        _settle(0.2)
        win._share_card_blocked = lambda: False    # the trust prompt is not what is under test
        _DEMO["win"] = win
    return _DEMO["win"]


def _opens_the_picker(fire) -> None:
    """Run `fire`; a timer rejects whatever modal comes up. The picker must be what came up, run
    by its real exec(), and nothing may have been raised into a slot on the way."""
    seen, raised = [], []
    hook = sys.excepthook
    sys.excepthook = lambda *exc: raised.append(exc)

    def close():
        w = QApplication.activeModalWidget()
        if isinstance(w, QDialog):
            seen.append(w.windowTitle())
            w.reject()
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(close)
    timer.start()
    try:
        fire()
        _settle(0.1)
    finally:
        timer.stop()
        sys.excepthook = hook
    assert not raised, f"the slot raised: {raised[0][0].__name__}: {raised[0][1]}"
    assert seen == [_PICKER], f"expected the overlay picker to open once, saw {seen}"


def test_the_file_menu_item_opens_the_overlay_picker():
    win = _window()
    win.exports.sync_menu()
    action = win._export_video_action
    assert action.isEnabled(), action.toolTip()
    _opens_the_picker(action.trigger)
    print("ok File ▸ Export overlay video… opens the picker")


def test_the_palette_row_opens_the_overlay_picker():
    win = _window()
    rows = [e for e in command_palette.entries(win) if e.title == "Export overlay video…"]
    assert len(rows) == 1 and rows[0].enabled, [(e.title, e.enabled) for e in rows]
    _opens_the_picker(rows[0].run)
    print("ok ⌘K Export overlay video… opens the picker")


# ------------------------------------------------------------------ the structural guard
_SIGNALS = {"triggered", "clicked", "toggled"}
#: The names a slot may give the parameter that receives the signal's bool.
_CHECKED = {"checked", "_checked", "on", "_on", "_"}


def _first_positional(args: ast.arguments) -> str | None:
    names = [a.arg for a in args.posonlyargs + args.args]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return names[0] if names else None


def _findings(root: Path) -> list[str]:
    trees = {p: ast.parse(p.read_text(), str(p)) for p in sorted((root / "studio").rglob("*.py"))}
    by_name: dict[str, list] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                by_name.setdefault(node.name, []).append(node)
    out = []

    def visit(path, node, own):
        if isinstance(node, ast.ClassDef):        # `self.x` inside it means this class's x
            own = {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
        for child in ast.iter_child_nodes(node):
            visit(path, child, own)
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect" and node.args
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in _SIGNALS):
            return
        target = node.args[0]
        if isinstance(target, ast.Lambda):
            defs = [target]
        elif (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
              and target.value.id == "self" and target.attr in own):
            defs = [own[target.attr]]
        elif isinstance(target, (ast.Attribute, ast.Name)):
            defs = by_name.get(target.attr if isinstance(target, ast.Attribute) else target.id, [])
        else:
            defs = []
        for fn in defs:
            first = _first_positional(fn.args)
            if first is not None and first not in _CHECKED:
                out.append(f"{path.relative_to(root)}:{node.lineno}: {ast.unparse(node)[:100]} — "
                           f"`{first}` would receive the signal's bool")

    for path, tree in trees.items():
        visit(path, tree, {})
    return out


def test_no_signal_bool_lands_in_a_parameter_that_is_not_checked():
    found = _findings(Path(_REPO))
    assert not found, "\n".join(found)
    print("ok every triggered/clicked/toggled slot takes the bool as `checked` or not at all")


if __name__ == "__main__":
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    sys.stdout.flush()
    os._exit(0)          # the demo window's teardown is not what this file tests
