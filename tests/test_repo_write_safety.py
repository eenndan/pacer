"""THE APP MAY NOT WRITE INTO ITS OWN SOURCE TREE — proved at a DPI the suite does not use.

WHAT THIS CAUGHT. `theme.apply_theme` -> `_caret_down_asset` rendered the QComboBox chevron and
saved it to `studio/assets/caret-down.png`, a TRACKED file, on every boot. Running the app — or
any test that themes a QApplication — dirtied the working copy. Measured, one boot per row:

    env                             bytes  IHDR   pHYs           vs the committed file
    offscreen, DPR 1                  321  24x24  3780 (96 dpi)  IDENTICAL
    cocoa,     DPR 1                  321  24x24  3937 (100 dpi) 8 bytes differ (pHYs)
    offscreen, QT_SCALE_FACTOR=2      570  48x48  3780           a different image entirely

The suite never saw it because ctest runs OFFSCREEN AT 96 dpi — the one environment that
reproduces the committed bytes exactly. That is the whole lesson of this file: a guard that only
ever runs at the default DPI is not a guard, so both boots below go through a SUBPROCESS with
QT_SCALE_FACTOR pinned (a QApplication reads it once, at construction — an in-process test cannot
change it) and the DPR is asserted, so an ignored env var cannot fake a pass.

THE THREE THINGS EACH BOOT PROVES, and each is a negative control on the next:
  1. no write lands in the repo — from a TRIPWIRE on every write path Python and Qt expose
     (`builtins.open` in a write mode, `os.open` with a write flag, `os.mkdir`/`makedirs`,
     `QPixmap.save`, `QImage.save`), not from `git status` alone;
  2. `git status --porcelain` is byte-identical before and after (the end-to-end form of 1, and it
     compares against the tree as FOUND, so a developer's unrelated edits cannot fail it);
  3. the chevron still PAINTS, read from the window composite of a shown QComboBox and diffed
     against the same window with the rule stripped — because "wrote nothing" is trivially true
     for a render that failed and fell back to the native arrow, which is exactly what a
     write-denial would have produced.

Run: python tests/test_repo_write_safety.py
"""
import json
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_CHILD_FLAG = "--themed-boot-probe"

# Ink floors for the chevron in the drop-down box, per device pixel ratio. MEASURED (22 px at
# DPR 1, 50 px at DPR 2) and set ~20 % under, because this is a "did it paint at all" floor, not a
# pin on the glyph: the count does NOT scale with area — the @2x source lands crisper in the same
# logical box, so fewer of its pixels are partial. A one-directional `>=`, so a sharper glyph can
# never turn the build red.
_MIN_INK = {"1": 18, "2": 40}


# =============================================================== the child (one themed boot)
def _child(scale: str) -> None:
    """Boot a themed QApplication under a write tripwire and print one JSON line of evidence.

    Runs in its own process because QT_SCALE_FACTOR is read once, when the QApplication is built.
    """
    import builtins
    import re

    writes: list[str] = []
    repo_real = os.path.realpath(_REPO)

    def _record(path) -> None:
        if isinstance(path, (str, bytes, os.PathLike)):
            try:
                writes.append(os.path.realpath(os.fsdecode(path)))
            except Exception:  # noqa: BLE001 — a tripwire must never break the run it observes
                pass

    _open, _os_open, _mkdir, _makedirs = builtins.open, os.open, os.mkdir, os.makedirs

    def jail_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            _record(file)
        return _open(file, mode, *a, **k)

    def jail_os_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            _record(path)
        return _os_open(path, flags, *a, **k)

    builtins.open = jail_open
    os.open = jail_os_open
    os.mkdir = lambda p, *a, **k: (_record(p), _mkdir(p, *a, **k))[1]
    os.makedirs = lambda p, *a, **k: (_record(p), _makedirs(p, *a, **k))[1]

    # QPixmap.save / QImage.save bypass Python's file layer entirely — the caret's own write path.
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication, QComboBox, QWidget

    for cls in (QImage, QPixmap):
        orig = cls.save

        def wrapped(self, fileName=None, *a, _o=orig, **k):
            _record(fileName)
            return _o(self, fileName, *a, **k)
        cls.save = wrapped

    from studio import theme

    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)

    caret = theme._caret_down_asset()
    data = b""
    if caret and os.path.exists(caret):
        with _open(caret, "rb") as f:
            data = f.read()

    # ---- does the chevron still paint?  Window composite, themed vs the native-arrow fallback.
    keep = []

    def composite(qss):
        app.setStyleSheet(qss)
        w = QWidget()
        w.resize(200, 80)
        cb = QComboBox(w)
        cb.addItems(["Speed", "Delta"])
        cb.setGeometry(20, 20, 120, theme.CTRL_H)
        w.show()
        app.processEvents()
        # RESTING state, deliberately: showing a window gives its first focusable child the
        # keyboard, and the FOCUS_RING_PX accent ring then paints C.accent_hover right through
        # the drop-down box — 26 px of it, which reads as "ink" to any threshold below it.
        cb.clearFocus()
        app.processEvents()
        keep.append((w, cb))
        return w.grab().toImage(), cb.mapTo(w, QPoint(0, 0)), cb

    real_qss = theme._build_qss()
    native_qss = re.sub(r"QComboBox::down-arrow \{.*?\}\nQComboBox::down-arrow:on \{.*?\}",
                        "/* native arrow */", real_qss, flags=re.S)
    img_a, org, cb = composite(real_qss)
    img_b, _, _ = composite(native_qss)
    d = img_a.devicePixelRatio()

    # The drop-down BOX, inside the control's border, minus the rounded corners (RADIUS_S) so the
    # only thing left in it is the arrow.
    lx1 = org.x() + cb.width() - theme.BORDER_PX
    box = (int((lx1 - theme.SPACE_L) * d), int(lx1 * d),
           int((org.y() + theme.RADIUS_S) * d), int((org.y() + cb.height() - theme.RADIUS_S) * d))
    surface = theme.qcolor(theme.C.surface)
    dim = theme.qcolor(theme.C.text_dim)

    def ink(img):
        """Pixels in the drop-down box that are NOT the control's own background, plus the
        STRONGEST one expressed as its position on the C.surface -> C.text_dim blend line.

        The blend fraction is the hue claim: a glyph tinted to C.text_dim and composited over
        C.surface lands on that line in all three channels at once (the peak sits below t=1 only
        because a 24 px source is smooth-scaled into a 12 px box). Any other painter — the native
        arrow in ButtonText, a stray focus ring in C.accent_hover — misses the line."""
        pts, peak, peak_d = [], None, -1.0
        for y in range(box[2], box[3]):
            for x in range(box[0], box[1]):
                c = img.pixelColor(x, y)
                d = (abs(c.red() - surface.red()) + abs(c.green() - surface.green())
                     + abs(c.blue() - surface.blue()))
                if d > 24:
                    pts.append((x, y))
                    if d > peak_d:
                        peak_d, peak = d, c
        w_h = t = None
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w_h = [max(xs) - min(xs) + 1, max(ys) - min(ys) + 1]
            t = [round((getattr(peak, ch)() - getattr(surface, ch)())
                       / (getattr(dim, ch)() - getattr(surface, ch)()), 3)
                 for ch in ("red", "green", "blue")]
        return {"px": len(pts), "bbox": w_h, "peak": None if peak is None else peak.name(),
                "peak_blend": t}

    print("PROBE " + json.dumps({
        "requested_scale": scale,
        "dpr": app.devicePixelRatio(),
        "caret": caret,
        "caret_bytes": len(data),
        "caret_is_png": data[:8].hex() == "89504e470d0a1a0a",
        "writes": sorted(set(writes)),
        "writes_in_repo": sorted({w for w in writes if w.startswith(repo_real + os.sep)}),
        "qss_url": (re.search(r"image: url\(([^)]+)\)", real_qss) or [None, None])[1],
        "themed_ink": ink(img_a),
        "native_ink": ink(img_b),
    }))


# =============================================================== the parent
_EVIDENCE: dict[str, dict] = {}


def _boot(scale: str, fresh: bool = False) -> dict:
    """Run one child at QT_SCALE_FACTOR=`scale` and return its evidence dict.

    Memoized per scale — a boot is ~1.5 s and two of the three tests below want the same
    evidence. `fresh=True` for the git test, which needs the write to happen BETWEEN its two
    `git status` calls."""
    if not fresh and scale in _EVIDENCE:
        return _EVIDENCE[scale]
    env = dict(os.environ, QT_SCALE_FACTOR=scale, QT_QPA_PLATFORM="offscreen",
               QT_ENABLE_HIGHDPI_SCALING="0", QT_AUTO_SCREEN_SCALE_FACTOR="0")
    out = subprocess.run([sys.executable, os.path.abspath(__file__), _CHILD_FLAG, scale],
                         capture_output=True, text=True, env=env, timeout=300, check=False)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("PROBE ")), None)
    assert line, (f"themed boot at QT_SCALE_FACTOR={scale} produced no evidence.\n"
                  f"--- stdout ---\n{out.stdout}\n--- stderr ---\n{out.stderr}")
    _EVIDENCE[scale] = json.loads(line[len("PROBE "):])
    return _EVIDENCE[scale]


def _git_status() -> str | None:
    """`git status --porcelain` for the superproject, or None when git can't answer.

    `--ignore-submodules=all`: the C++ submodules are not this guard's business, and a worktree
    with an unlinked submodule gitdir makes a plain `git status` exit non-zero."""
    try:
        r = subprocess.run(["git", "-C", _REPO, "status", "--porcelain", "--ignore-submodules=all"],
                           capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def theme_space_m() -> int:
    """SPACE_M — the width the QSS gives QComboBox::down-arrow. Imported lazily so the parent
    process never has to build a QApplication."""
    from studio import theme
    return theme.SPACE_M


def test_a_themed_boot_writes_nothing_into_the_source_tree():
    """At BOTH device pixel ratios: no write path touches the repo, and the chevron is real."""
    seen = {}
    for scale in ("1", "2"):
        ev = _boot(scale)
        # Negative control #1: the run really happened at the DPI we asked for. Without this, an
        # ignored QT_SCALE_FACTOR would silently re-run the one environment that never failed.
        assert ev["dpr"] == float(scale), (
            f"QT_SCALE_FACTOR={scale} did not take: devicePixelRatio={ev['dpr']}")
        # Negative control #2: something was actually rendered. A render that returned None writes
        # nothing at all and would pass the real assertion below for the wrong reason.
        assert ev["caret"] and ev["caret_is_png"] and ev["caret_bytes"] > 100, (
            f"no chevron PNG was generated at DPR {scale}: {ev['caret']} "
            f"({ev['caret_bytes']} bytes, png={ev['caret_is_png']})")
        assert ev["qss_url"] == ev["caret"], (
            f"the stylesheet points at {ev['qss_url']}, not at the generated {ev['caret']}")

        assert not ev["writes_in_repo"], (
            f"a themed boot at DPR {scale} WROTE INTO THE SOURCE TREE: {ev['writes_in_repo']}. "
            f"Generated assets belong in a temp/cache dir — see theme._caret_down_asset.")
        assert not os.path.realpath(ev["caret"]).startswith(os.path.realpath(_REPO) + os.sep), (
            f"the generated chevron lives inside the repo: {ev['caret']}")
        seen[scale] = ev
        print(f"test_a_themed_boot_writes_nothing_into_the_source_tree DPR {scale} OK — "
              f"{len(ev['writes'])} write paths, 0 in the repo; chevron "
              f"{ev['caret_bytes']} B at {ev['caret']}")

    # The two renders DIFFER — i.e. DPR 2 is genuinely the case a single committed file could not
    # have satisfied, which is why "write it only when the bytes change" was not the fix.
    assert seen["1"]["caret_bytes"] != seen["2"]["caret_bytes"], (
        "the DPR 1 and DPR 2 chevrons are the same size — this guard is no longer testing the "
        f"case it was written for ({seen['1']['caret_bytes']} B both).")


def test_a_themed_boot_leaves_the_git_working_tree_exactly_as_it_found_it():
    """The end-to-end form: `git status` before == after, over a DPR 2 boot."""
    before = _git_status()
    if before is None:
        print("test_a_themed_boot_leaves_the_git_working_tree_exactly_as_it_found_it SKIPPED "
              "(no usable git checkout)")
        return
    _boot("2", fresh=True)
    after = _git_status()
    assert after == before, (
        "a themed boot changed the git working tree.\n"
        f"--- before ---\n{before}\n--- after ---\n{after}")
    print("test_a_themed_boot_leaves_the_git_working_tree_exactly_as_it_found_it OK — "
          f"{len(before.splitlines())} dirty path(s) before and after")


def test_the_combo_still_paints_the_phosphor_chevron():
    """From the WINDOW composite, at both DPRs: our tinted caret, not the native arrow.

    A child grab lies about anything the QSS box paints, so both readings come from the window's
    own composite at `cb.mapTo(window, QPoint(0, 0))`. The comparison is DIFFERENTIAL — the same
    window with the `QComboBox::down-arrow` rule stripped is what a failed render falls back to,
    and offscreen that draws nothing in the drop-down box at all."""
    for scale in ("1", "2"):
        ev = _boot(scale)
        themed, native = ev["themed_ink"], ev["native_ink"]
        d = float(scale)
        assert native["px"] == 0, (
            f"the fallback baseline is not empty at DPR {scale} ({native['px']} px) — this "
            "differential no longer proves the chevron came from the theme.")
        assert themed["px"] >= _MIN_INK[scale], (
            f"the drop-down box is (nearly) empty at DPR {scale}: {themed['px']} ink px, floor "
            f"{_MIN_INK[scale]}. The chevron fell back to the native arrow.")
        # A caret is WIDE AND SHORT — this is shape, not just "some ink".
        w, h = themed["bbox"]
        assert w > h, f"chevron ink is {w}x{h} at DPR {scale} — a caret is wider than it is tall"
        # Measured 8 px at DPR 1 and 16 at DPR 2, in the SPACE_M box the rule declares; the
        # band is +-25 % of that so an antialiasing pixel cannot turn the build red.
        assert 6 * d <= w <= theme_space_m() * d, (
            f"chevron ink is {w} px wide at DPR {scale}; the rule gives it a "
            f"{theme_space_m()} px box")
        # The hue claim: the strongest pixel is ON the C.surface -> C.text_dim blend line, in all
        # three channels, at nearly full strength — i.e. it is the tinted Phosphor glyph.
        t = themed["peak_blend"]
        assert max(t) - min(t) <= 0.06, (
            f"the strongest pixel {themed['peak']} at DPR {scale} blends {t} — off the "
            "C.surface -> C.text_dim line, so it is not the tinted chevron")
        assert 0.7 <= min(t) <= 1.05, (
            f"the strongest pixel {themed['peak']} at DPR {scale} is only {min(t):.2f} of the way "
            "to C.text_dim — the chevron is not painting at strength")
        print(f"test_the_combo_still_paints_the_phosphor_chevron DPR {scale} OK — "
              f"{themed['px']} ink px, {w}x{h}, peak {themed['peak']} at blend {t} "
              f"(native fallback: {native['px']} px)")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == _CHILD_FLAG:
        _child(sys.argv[2])
    else:
        test_a_themed_boot_writes_nothing_into_the_source_tree()
        test_a_themed_boot_leaves_the_git_working_tree_exactly_as_it_found_it()
        test_the_combo_still_paints_the_phosphor_chevron()
        print("\n3 repo-write-safety tests passed")
