"""Launch the studio app on a real session, scrub a frame (exercising the renamed playhead
setter across both views), and save a QScreen.grabWindow screenshot."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

D = "~/Desktop/D24"
PATHS = [f"{D}/GX010060.MP4"]


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from studio.app import StudioWindow

    w = StudioWindow(PATHS)
    s = w.session
    w.resize(1600, 980)
    w.show()
    app.processEvents()

    # Scrub to ~40% through the session so the renamed playhead setter drives BOTH views
    # (plots cursor + map marker) and the readout updates.
    t0, t1 = float(s.tt[0]), float(s.tt[-1])
    t = t0 + 0.40 * (t1 - t0)
    # Drive the same paths the playback tick + scrub use.
    w.plots.set_playhead_time(t)            # normal (force=False)
    w.plots.set_playhead_time(t, force=True)  # mid-drag variant
    w.map.set_playhead_time(t)
    w._apply_readout(t)
    app.processEvents()

    out = os.path.join(os.environ.get("TMPDIR", "/tmp"), "m3_studio_scrub.png")
    screen = app.primaryScreen()
    pm = screen.grabWindow(w.winId())
    ok = pm.save(out, "PNG")
    print("SCREENSHOT", "OK" if ok else "FAIL", out, pm.width(), "x", pm.height())


if __name__ == "__main__":
    main()
