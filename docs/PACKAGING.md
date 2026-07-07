# Packaging Pacer Studio for macOS

This builds a standalone, double-clickable **`Pacer Studio.app`** (and a drag-to-install `.dmg`)
from the `studio` desktop app, with **no pixi / no Python install required** on the target Mac.

Target: macOS 12+ on Apple Silicon (`osx-arm64` — the only platform this repo supports).

> The build is **unsigned**. It runs locally immediately; distributing it to other Macs past
> Gatekeeper needs **codesign + notarize + staple** with your Apple Developer ID (steps below). You
> cannot notarize without that ID — there is no way around it.

## What ships inside the .app

`packaging/pacer.spec` is a [PyInstaller](https://pyinstaller.org) spec. The entry point is
`studio/__main__.py` (i.e. `python -m studio`). It bundles everything the app loads at runtime that
isn't a plain importable module:

| Bundled | Why |
| --- | --- |
| `pacer._pacer` native extension (`.so`) + the `pacer` package | the C++ core; found via the installed `pacer` package, so the bundle uses the same binary the app imports |
| **PySide6 incl. QtMultimedia plugins** | the synced-video player needs the AVFoundation media backend; collected wholesale because the default hook can miss media plugins |
| pyqtgraph + qtawesome Qt-side data | icon fonts / styling loaded via `__file__` |
| `studio/assets/` (Inter fonts, `caret-down.png`) and `studio/mk_centerline.json` | loaded via `os.path.dirname(__file__)`; mirrored into the bundle so those paths resolve |
| the tiny `3rdparty/.../hero6.mp4` sample | `Session.DEFAULT_SAMPLE` (the launch / "Open demo" fallback). Resolved via `sys._MEIPASS` when frozen |
| **`ffmpeg` + `ffprobe`** binaries at the bundle root | a Finder-launched `.app` has no PATH ffmpeg; a runtime hook wires the app to the bundled ones (see below) |

### ffmpeg / ffprobe

Video export shells out to `ffmpeg`/`ffprobe`. The spec bundles whatever `ffmpeg`/`ffprobe` is
**first on `PATH` at build time** — in this repo that is the pixi conda-forge ffmpeg
(`pyproject.toml [tool.pixi.dependencies] ffmpeg >=7.1,<8`, an LGPL build).

The runtime hook `packaging/rthook_ffmpeg.py` runs before any app code and sets `PACER_FFMPEG` /
`PACER_FFPROBE` to the bundled binaries. `studio.export_video._resolve_binary` reads those env vars
first (then a `sys._MEIPASS` lookup, then the bare PATH name), so the app finds ffmpeg with no PATH.
In a normal dev checkout neither marker is set, so it's exactly the old PATH lookup — unchanged.

> **Licensing for redistribution:** ffmpeg/ffprobe are bundled. The conda-forge ffmpeg is LGPL; if
> you redistribute the `.dmg`, ship the matching ffmpeg `LICENSE`/`COPYING` alongside it. Swap in a
> different ffmpeg build by putting it first on `PATH` before running the build.

## Build (unsigned, local)

One-time, **inside the pixi env** (PyInstaller is intentionally **not** a project dependency —
packaging is opt-in):

```bash
pixi run build          # build the C++ core + editable bindings (only needed once / after C++ changes)
pixi shell              # enter the env so `import pacer`, PySide6, ffmpeg all resolve
pip install pyinstaller # only needed when cutting a build
```

Then:

```bash
packaging/build_macos.sh
```

Output:

- `dist/Pacer Studio.app` — run locally with `open "dist/Pacer Studio.app"`
- `dist/Pacer-Studio-<version>.dmg` — the drag-to-Applications disk image

To run the spec directly (what the script does): `pyinstaller --noconfirm packaging/pacer.spec`.

## Distribute (signed + notarized) — needs your Apple Developer ID

These need your signing identity and an App Store Connect API key, so `build_macos.sh` documents
them (commented) but does not run them. Run them by hand after a successful build.

```bash
# 0. one-time: store notarytool credentials in the keychain
xcrun notarytool store-credentials pacer-notary \
  --key /path/to/AuthKey_<KEYID>.p8 --key-id <KEYID> --issuer <ISSUER-UUID>

# 1. codesign (hardened runtime + timestamp; --deep signs the bundled .so / ffmpeg / Python fwk)
codesign --force --deep --options runtime --timestamp \
  --sign "Developer ID Application: <YOUR NAME> (<TEAMID>)" "dist/Pacer Studio.app"
codesign --verify --deep --strict --verbose=2 "dist/Pacer Studio.app"

# 2. notarize the dmg (recreate it from the signed .app first), submit and wait
hdiutil create -volname "Pacer Studio" -srcfolder "dist/Pacer Studio.app" \
  -ov -format UDZO "dist/Pacer-Studio-<version>.dmg"
xcrun notarytool submit "dist/Pacer-Studio-<version>.dmg" --keychain-profile pacer-notary --wait

# 3. staple the ticket (so it validates offline) and verify with Gatekeeper
xcrun stapler staple "dist/Pacer Studio.app"
xcrun stapler staple "dist/Pacer-Studio-<version>.dmg"
spctl --assess --type execute --verbose=4 "dist/Pacer Studio.app"
```

## Gatekeeper

If you skip notarization, a user can still open the unsigned app via **right-click ▸ Open** (or
`xattr -dr com.apple.quarantine "Pacer Studio.app"`), but Gatekeeper will warn on first launch.

## Demo data

The clips bundled inside the `.app` (`3rdparty/gpmf-parser/samples`) are tiny GoPro **test** clips
with **no real laps** — fine to prove the app launched, useless for actually seeing the studio.

For a real first-run experience the app supports a demo recording that is **fetched at runtime**, so
no large media is committed to the repo (keeping the repo + the `.app` small):

```bash
python -m studio --demo            # open a real demo lapping recording on startup
```

Resolution order (`studio/demo.py`):

1. **`PACER_DEMO_MP4`** — an explicit path to a recording you already have.
2. a cached copy under `~/Library/Application Support/pacer/demo/`.
3. a **one-time download** from the pinned `v0.1.0` GitHub release asset into that cache
   (override the URL with `PACER_DEMO_URL`). If offline / the asset is missing, the app falls back
   to the empty welcome state — it still launches.

To publish the demo recording, attach a small single-chapter lapping `.mp4`
(`pacer-demo-lap.mp4`) to the GitHub `v0.1.0` release (or Git LFS); see `_DEMO_URL` in
`studio/demo.py`. Keep it small (one clean lap is plenty) — do **not** commit it to git.
