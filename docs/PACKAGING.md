# Packaging Pacer Studio for macOS

**An ungated local recipe, not a distribution.** No CI job builds or launches this bundle and no
test runs it, so a build is untested until you launch it yourself. It crashed at launch on every
build from June 2026 until #450, and nothing noticed: the tag job that built it never launched it,
and that job is gone (RUL-11 in [DECISIONS.md](DECISIONS.md)). Source is the only supported way to
run Pacer.

The recipe is meant to build an unsigned **`Pacer Studio.app`** (and a drag-to-install `.dmg`) from
the `studio` desktop app, carrying its own Python, Qt and ffmpeg so the target Mac needs no pixi.

Target: macOS 12+ on Apple Silicon (`osx-arm64` — the only platform this repo supports).

> The build is **unsigned**. Launch it yourself before you rely on it; distributing it to other
> Macs past Gatekeeper needs **codesign + notarize + staple** with your Apple Developer ID (steps
> below). You cannot notarize without that ID — there is no way around it.

## What ships inside the .app

`packaging/pacer.spec` is a [PyInstaller](https://pyinstaller.org) spec. Its entry point is
`studio/__main__.py`, which PyInstaller runs as a top-level script with no parent package, not as
`python -m studio`: the entry must import absolutely, and its one relative import is what kept
every build from starting until #450 (`tests/test_first_launch_says_so.py` now runs the entry that
way, without building a bundle). It bundles everything the app loads at runtime that isn't a
plain importable module:

| Bundled | Why |
| --- | --- |
| `pacer._pacer` native extension (`.so`) + the `pacer` package | the C++ core; found via the installed `pacer` package, so the bundle uses the same binary the app imports |
| **PySide6 incl. QtMultimedia plugins** | the synced-video player needs the AVFoundation media backend; collected wholesale because the default hook can miss media plugins |
| pyqtgraph + qtawesome Qt-side data | icon fonts / styling loaded via `__file__`. qtawesome brings twelve fonts; the app draws only Phosphor, but qtawesome loads all twelve on its first icon, so all of them ship (licenses in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md#the-icon-fonts-qtawesome-bundles)) |
| `studio/assets/` (Inter fonts, `pacer.icns`) and `studio/mk_centerline.json` | loaded via `os.path.dirname(__file__)`; mirrored into the bundle so those paths resolve. The QComboBox chevron used to live here too as a tracked `caret-down.png` the app re-rendered on every boot — PR #206 moved it to a per-process temp dir, which is also what stopped the frozen `.app` silently losing its chevron (`px.save` fails inside a read-only signed bundle) |
| the tiny `3rdparty/.../hero6.mp4` sample | `Session.DEFAULT_SAMPLE` (the launch / "Open demo" fallback). Resolved via `sys._MEIPASS` when frozen |
| **`ffmpeg` + `ffprobe`** binaries at the bundle root | a Finder-launched `.app` has no PATH ffmpeg; a runtime hook wires the app to the bundled ones (see below) |

### ffmpeg / ffprobe

Video export shells out to `ffmpeg`/`ffprobe`. The spec bundles whatever `ffmpeg`/`ffprobe` is
**first on `PATH` at build time** — in this repo that is the pixi conda-forge ffmpeg
(`pyproject.toml [tool.pixi.dependencies] ffmpeg >=7.1,<8`), which `pixi.lock` pins as
`ffmpeg-7.1.1-gpl_h670d5b4_111`: **GPL-3.0-or-later** by its own `ffmpeg -L`, and it links libx264
and libx265.

The runtime hook `packaging/rthook_ffmpeg.py` runs before any app code and sets `PACER_FFMPEG` /
`PACER_FFPROBE` to the bundled binaries. `studio.export_video._resolve_binary` reads those env vars
first (then a `sys._MEIPASS` lookup, then the bare PATH name), so the app finds ffmpeg with no PATH.
In a normal dev checkout neither marker is set, so it's exactly the old PATH lookup — unchanged.

> **Licensing for redistribution:** the bundled ffmpeg is that `gpl_*` build, and libx264 is the
> export's software fallback, so redistributing the `.app` or the `.dmg` owes the GPLv3 text and the
> corresponding source of ffmpeg, x264 and x265 (or a written offer for it), plus the notices of the
> libraries it links. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md#ffmpeg-read-this-before-redistributing-the-app)
> spells it out.
>
> The alternative is conda-forge's `lgpl_*` variant of the same ffmpeg (LGPL-2.1-or-later, no x264
> or x265): pin it and move the software fallback to `libopenh264`, which is a code change, since the
> fallback passes libx264's own `-preset` and `-crf`. Swap in any other ffmpeg build by putting it
> first on `PATH` before running the build.

## Build (unsigned, local)

One-time, **inside the pixi env** (PyInstaller is intentionally **not** a project dependency —
packaging is opt-in):

```bash
pixi run build                  # build the C++ core + bindings (once, and after C++ changes)
pixi shell                      # enter the env so `import pacer`, PySide6, ffmpeg all resolve
python -m ensurepip --upgrade   # a fresh env has no pip: pip is not a pixi dependency
python -m pip install "pyinstaller==6.22.3" "pyinstaller-hooks-contrib==2026.7"
```

The pins are the last versions known to build the bundle: the removed tag job's, which built
v0.4.1. An unpinned install lets any PyInstaller release change the build silently.

Then:

```bash
packaging/build_macos.sh
```

Output:

- `dist/Pacer Studio.app` — launch it with `open "dist/Pacer Studio.app"` and open a recording
  before you trust the build: nothing else runs it
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

For a real first-run experience the app opens a demo session that is **fetched at runtime**, so no
media is committed to the repo (keeping the repo + the `.app` small). It is **synthetic** —
generated by `studio/dev/make_demo.py`, not filmed: a simulated kart on a fictional circuit, with
no person, kart, place or camera footage in it, and a picture that says so on every frame:

```bash
python -m studio --demo            # open the synthetic demo session on startup
```

Resolution order (`studio/demo.py`):

1. **`PACER_DEMO_MP4`** — an explicit path to a recording you already have.
2. a cached copy under `~/Library/Application Support/pacer/demo/<tag>/`, one folder per pinned
   pre-release (`_DEMO_TAG`): the cache is trusted by existence, so a copy of an older demo is
   never found again once the pin moves.
3. a **one-time download** of `pacer-demo.mp4` from the pinned demo-data pre-release
   (`studio/demo._DEMO_TAG`) into that cache, kept only if its sha256 matches `_DEMO_SHA256`
   (override the URL with `PACER_DEMO_URL`). If offline / the download fails, the app falls back
   to the empty welcome state — it still launches.

**The welcome screen's "Open demo" button is gated on steps 1–2 only** (`demo.demo_available()`, an
offline path lookup), so the app never probes the network on its own; `--demo` on the command line
runs the full order, download included, and the cache then offers the button on every later
launch. **Recording the demo video: set `PACER_DEMO_MP4`** and the button points at your clip.

The demo is not a clean session on purpose: since `demo-data-v2` it carries one planted habit
(`make_demo.DEMO_HABIT`: C1, on 8 of its 14 flying laps), so its debrief has a real call to make
and a reader can check it against the truth.

To re-publish the demo: `pixi run make-demo -- --out pacer-demo.mp4` (deterministic: the same code
and pixi env give the same bytes), attach it to a NEW `demo-data-vN` pre-release as an asset named
exactly `pacer-demo.mp4`, and in the same PR move the three pins in `studio/demo.py` —
`_DEMO_TAG` (the URL is built from it, and so is the cache folder), `_DEMO_SHA256` and
`_DEMO_BYTES` — plus `make_demo.PUBLISHED_LAP_MS` if the telemetry changed. **Never delete or
replace an older `demo-data-vN`:** older clones pin its exact bytes. Do **not** commit the file to
git.
