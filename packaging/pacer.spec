# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: build an UNSIGNED macOS `Pacer Studio.app` for the `studio` desktop app.

Run from the repo root via packaging/build_macos.sh (which sets the pixi env on PATH):

    pyinstaller --noconfirm packaging/pacer.spec

What this bundles (the things the app loads at runtime that are NOT plain importable modules):
  * the entry is studio/__main__.py — i.e. `python -m studio` (StudioWindow + app.main()).
  * the compiled nanobind extension `pacer._pacer` (a .so) + the `pacer` package, found via the
    installed `pacer` package rather than hard-coding the build path.
  * PySide6 + QtMultimedia: collected wholesale so the multimedia/AVFoundation backend plugins
    ship (the video player needs them; PyInstaller's default hook can miss the media plugins).
  * studio's runtime data loaded via __file__ — studio/assets (fonts + caret-down.png) and
    studio/mk_centerline.json — and the bundled sample clip Session.DEFAULT_SAMPLE opens.
  * ffmpeg + ffprobe binaries at the bundle root; a runtime hook (rthook_ffmpeg.py) exports
    PACER_FFMPEG / PACER_FFPROBE so studio.export_video finds them with no PATH ffmpeg.

UNSIGNED: this spec produces a runnable-locally .app. Distribution requires codesign + notarize +
staple with the user's Apple Developer ID — see docs/PACKAGING.md / build_macos.sh.
"""

import os
import shutil

from PyInstaller.utils.hooks import collect_all

# SPECPATH is the dir holding this spec (packaging/); the repo root is its parent. Resolve every
# bundled path from there so the build is invariant to the cwd PyInstaller was launched from.
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 (SPECPATH is injected)


def _repo(*parts):
    return os.path.join(REPO_ROOT, *parts)


# Single-source the version: regex-read studio.__version__ from disk (this build interpreter may
# not be able to `import studio`), mirroring build_macos.sh's grep of pyproject.toml. Keeps the
# .app's CFBundle*Version in lockstep with the canonical studio/__init__.py.
import re  # noqa: E402


def _studio_version():
    try:
        txt = open(_repo("studio", "__init__.py"), encoding="utf-8").read()
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', txt)
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


_VERSION = _studio_version()


# --- the compiled pacer extension + package -----------------------------------------------------
# Locate the installed `pacer` package (editable-installed in the pixi env) so we bundle the SAME
# _pacer.*.so the app imports, plus the package's __init__/.pyi, without hard-coding a build path.
import pacer  # noqa: E402

_pacer_dir = os.path.dirname(pacer.__file__)
pacer_binaries = []
pacer_datas = []
for _name in os.listdir(_pacer_dir):
    _src = os.path.join(_pacer_dir, _name)
    if _name.endswith((".so", ".pyd", ".dylib")):
        pacer_binaries.append((_src, "pacer"))          # native ext -> bundled under pacer/
    elif _name.endswith((".py", ".pyi")):
        pacer_datas.append((_src, "pacer"))             # __init__.py + stubs

# --- PySide6 (incl. QtMultimedia media plugins) -------------------------------------------------
pyside_datas, pyside_binaries, pyside_hidden = collect_all("PySide6")
# pyqtgraph + qtawesome ship Qt-side data (icon fonts, qss) loaded via __file__ — collect them too.
pg_datas, pg_binaries, pg_hidden = collect_all("pyqtgraph")
qta_datas, qta_binaries, qta_hidden = collect_all("qtawesome")

# --- studio runtime data (loaded via __file__) --------------------------------------------------
# Mirror the on-disk layout so os.path.dirname(__file__)/"assets"/... resolves inside the bundle.
studio_datas = [
    (_repo("studio", "assets"), os.path.join("studio", "assets")),
    (_repo("studio", "mk_centerline.json"), "studio"),
    # The "Open demo"/launch fallback sample (Session.DEFAULT_SAMPLE). Real --demo media is fetched
    # at runtime (studio.demo) and NOT bundled; this tiny clip just proves the app launched.
    (_repo("3rdparty", "gpmf-parser", "samples", "hero6.mp4"),
     os.path.join("3rdparty", "gpmf-parser", "samples")),
]

# --- ffmpeg / ffprobe -----------------------------------------------------------------------------
# Bundle the two binaries at the bundle ROOT; rthook_ffmpeg.py wires PACER_FFMPEG/PACER_FFPROBE to
# them. Source: whatever ffmpeg/ffprobe is first on PATH at build time — for this repo that's the
# pixi conda-forge ffmpeg (pyproject.toml [tool.pixi.dependencies] ffmpeg >=7.1,<8), an LGPL build.
# DISTRIBUTION NOTE: ship the matching ffmpeg LICENSE/COPYING alongside the .app (see PACKAGING.md).
ffmpeg_binaries = []
for _bin in ("ffmpeg", "ffprobe"):
    _path = shutil.which(_bin)
    if _path:
        ffmpeg_binaries.append((_path, "."))            # "." -> bundle root == sys._MEIPASS
    else:
        # Don't hard-fail the spec: the app still runs (export disabled). build_macos.sh warns.
        print(f"pacer.spec: WARNING {_bin} not found on PATH — export video will be disabled in "
              f"the .app until it is bundled.")

a = Analysis(
    [_repo("studio", "__main__.py")],                   # entry == `python -m studio`
    pathex=[REPO_ROOT],
    binaries=pacer_binaries + pyside_binaries + pg_binaries + qta_binaries + ffmpeg_binaries,
    datas=pacer_datas + studio_datas + pyside_datas + pg_datas + qta_datas,
    hiddenimports=(
        ["pacer", "pacer._pacer", "studio", "studio.app"]
        + pyside_hidden + pg_hidden + qta_hidden
    ),
    hookspath=[],
    runtime_hooks=[_repo("packaging", "rthook_ffmpeg.py")],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Pacer Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                                       # windowed (GUI) app, no terminal
    target_arch="arm64",                                # this repo is osx-arm64 only (pyproject)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Pacer Studio",
)

_icon = _repo("studio", "assets", "pacer.icns")         # generated by studio/dev/make_icon.py

app = BUNDLE(
    coll,
    name="Pacer Studio.app",
    icon=_icon if os.path.exists(_icon) else None,      # guarded: a checkout without the asset still builds
    bundle_identifier="app.pacer.studio",
    info_plist={
        "CFBundleName": "Pacer Studio",
        "CFBundleDisplayName": "Pacer Studio",
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": _VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        # The app reads GoPro files the user picks; no special entitlements needed unsigned.
        "NSHumanReadableCopyright": "© 2025-2026 eenndan — CC BY-NC-SA 4.0",
    },
)
