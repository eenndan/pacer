"""Dev visual-QA harness: build the REAL StudioWindow offscreen on a recording and grab its key
screens to PNG for visual review / regression (NOT wired into the product — a dev tool, like the
other studio/dev/ scripts).

Run (the PYTHONPATH note is load-bearing):

    PYTHONPATH=bindings/pacer pixi run python -m studio.dev.ui_capture <recording.mp4> --out /tmp/uxshots

Why the PYTHONPATH prefix: a bare run can resolve a STALE site-packages `pacer` that lacks
`read_accl_columns` and then hangs at load. Pointing PYTHONPATH at the freshly built `bindings/pacer`
picks up the correct bindings. With no <recording> the tool falls back to the bundled
`3rdparty/gpmf-parser/samples/hero6.mp4`, so it runs with no user file.

THE THEME TRAP THIS BAKES IN: building StudioWindow WITHOUT `theme.apply_theme(app)` renders every
widget in Qt's DEFAULT LIGHT palette — a false "unstyled / amateur" look that is purely a
missing-setup artefact, not a real bug. We therefore call `theme.register_fonts()` +
`theme.apply_theme(app)` BEFORE building any widget (see the marked block below); that is the
load-bearing line, do not remove it.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

# Offscreen Qt + inert media BEFORE any Qt import (the PlayerPane reads PACER_NO_MEDIA at
# construction, so setting it here — before any window exists — is early enough by construction).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from studio import library, theme  # noqa: E402
from studio.app import StudioWindow  # noqa: E402
from studio.coaching_panel import OpportunitiesDialog  # noqa: E402

# The bundled sample: a real GPMF clip so the tool runs with no user-supplied file.
_DEFAULT_RECORDING = "3rdparty/gpmf-parser/samples/hero6.mp4"
_LOAD_TIMEOUT_S = 30.0


def _suppress_modals() -> None:
    """The app's load guard reports failures via a MODAL QMessageBox — nothing can dismiss it
    offscreen and it would block. Swallow them (dev tool: we just want the screens)."""
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Critical)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Warning)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Information)


def _wait_for_load(app: QApplication, w: StudioWindow) -> None:
    """Pump the event loop until Session.load settles (it runs on a worker QThread since C1, so the
    session isn't ready synchronously after __init__). Bounded deadline, mirroring studio/dev/_smoke.py."""
    deadline = time.time() + _LOAD_TIMEOUT_S
    while w.view is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    if w.view is None:
        raise RuntimeError(f"session load did not complete within {_LOAD_TIMEOUT_S:.0f} s")


def _grab(widget, path: str) -> None:
    """Grab a widget to a PNG (offscreen render of the real, themed widget)."""
    widget.grab().save(path)
    print("wrote", path)


def capture(recording: str, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    # Never touch the user's real session library — divert the index to a throwaway temp dir BEFORE
    # any window is built (the load-time upsert reads this seam), like _smoke.py.
    lib_dir = tempfile.mkdtemp(prefix="pacer-uicapture-lib-")
    library._app_support_dir = lambda: lib_dir

    app = QApplication.instance() or QApplication([])
    # --- LOAD-BEARING: theme the app BEFORE building any widget. Without this the whole window
    # renders in Qt's default LIGHT palette (a false "unstyled" look). Do not remove / reorder. ---
    theme.register_fonts()
    theme.apply_theme(app)

    _suppress_modals()

    w = StudioWindow([recording])
    _wait_for_load(app, w)
    app.processEvents()  # one more pump so the default lap-selection has drawn the plots/map

    w.resize(1600, 1000)
    app.processEvents()

    view = w.view
    # Whole window + the individual 2x2 quadrants + the coaching front-door panel.
    _grab(w, os.path.join(out_dir, "window.png"))
    _grab(view.map, os.path.join(out_dir, "map.png"))
    _grab(view.plots, os.path.join(out_dir, "plots.png"))
    _grab(view.table, os.path.join(out_dir, "table.png"))
    _grab(view.opportunities, os.path.join(out_dir, "opportunities_panel.png"))

    # The coaching MODAL: build + show() + grab it directly. Do NOT call the app's
    # _open_opportunities() — it calls dlg.exec(), which BLOCKS forever offscreen (no one closes the
    # modal). show() renders it non-blocking so we can grab the laid-out table.
    opps = w.session.coaching_opportunities()
    brake_points = w.session.coaching_brake_points()
    dlg = OpportunitiesDialog(opps, jump_to=None, brake_points=brake_points,
                              speed_unit=w._speed_unit)
    dlg.resize(920, 380)
    dlg.show()
    app.processEvents()
    _grab(dlg, os.path.join(out_dir, "opportunities_dialog.png"))
    dlg.close()

    print(f"UI capture OK — {out_dir}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("recording", nargs="?", default=_DEFAULT_RECORDING,
                        help=f"GPMF recording to load (default: {_DEFAULT_RECORDING})")
    parser.add_argument("--out", default="/tmp/uxshots", help="directory for the PNGs")
    args = parser.parse_args(argv)
    capture(args.recording, args.out)


if __name__ == "__main__":
    main(sys.argv[1:])
