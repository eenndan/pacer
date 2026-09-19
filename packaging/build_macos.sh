#!/usr/bin/env bash
#
# build_macos.sh — build an UNSIGNED "Pacer Studio.app" + a .dmg from the repo.
#
# Produces a locally-runnable macOS app from the `studio` desktop app via PyInstaller
# (packaging/pacer.spec), then wraps it in a drag-to-Applications .dmg with hdiutil.
#
# Prerequisites (NOT installed by this script):
#   * the pixi env built + the pacer extension importable: `pixi run build` once, then run this
#     from inside `pixi shell` (or with the pixi env's bin on PATH) so `import pacer`, PySide6 and
#     ffmpeg/ffprobe all resolve. The spec bundles whatever ffmpeg/ffprobe is first on PATH.
#   * PyInstaller in that env:  pip install pyinstaller   (intentionally NOT a project dep —
#     packaging is opt-in; CI/dev installs it only when cutting a build).
#
# Usage (from anywhere; paths are resolved from the script location):
#   packaging/build_macos.sh
#
# Output: dist/Pacer Studio.app  and  dist/Pacer-Studio-<version>.dmg
#
# This app is UNSIGNED. To distribute it past Gatekeeper you must codesign + notarize + staple it
# with YOUR Apple Developer ID — those steps are documented (commented, not run) at the bottom and
# in docs/PACKAGING.md, because they need your signing identity + an App Store Connect API key.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SPEC="${SCRIPT_DIR}/pacer.spec"
APP_NAME="Pacer Studio"
APP_PATH="${REPO_ROOT}/dist/${APP_NAME}.app"
VERSION="$(grep -m1 '^version' "${REPO_ROOT}/pyproject.toml" | sed -E 's/.*"(.*)".*/\1/')"
DMG_PATH="${REPO_ROOT}/dist/Pacer-Studio-${VERSION}.dmg"

cd "${REPO_ROOT}"

# --- sanity: the env must have the things the spec bundles --------------------------------------
command -v pyinstaller >/dev/null 2>&1 || {
  echo "error: pyinstaller not found. Run inside the pixi env and: pip install pyinstaller" >&2
  exit 1
}
python -c "import pacer, PySide6" 2>/dev/null || {
  echo "error: 'import pacer' / PySide6 failed. Run 'pixi run build' and use the pixi env." >&2
  exit 1
}
command -v ffmpeg  >/dev/null 2>&1 || echo "warning: ffmpeg not on PATH — video export will be disabled in the .app." >&2
command -v ffprobe >/dev/null 2>&1 || echo "warning: ffprobe not on PATH — video export will be disabled in the .app." >&2

# --- build the .app ----------------------------------------------------------------------------
echo "==> PyInstaller: building ${APP_NAME}.app (v${VERSION})"
pyinstaller --noconfirm --clean "${SPEC}"

[ -d "${APP_PATH}" ] || { echo "error: expected ${APP_PATH} not produced." >&2; exit 1; }
echo "==> built ${APP_PATH}"

# --- wrap in a drag-to-Applications .dmg -------------------------------------------------------
echo "==> hdiutil: building $(basename "${DMG_PATH}")"
STAGE="$(mktemp -d)"
cp -R "${APP_PATH}" "${STAGE}/"
ln -s /Applications "${STAGE}/Applications"          # drag-to-install affordance
[ -f "${DMG_PATH}" ] && rm -f "${DMG_PATH}"
hdiutil create -volname "${APP_NAME}" -srcfolder "${STAGE}" -ov -format UDZO "${DMG_PATH}"
rm -rf "${STAGE}"
echo "==> built ${DMG_PATH}"

echo
echo "Done (UNSIGNED). Local run:  open \"${APP_PATH}\""
echo "First launch on another Mac is blocked by Gatekeeper until you sign + notarize (below)."

# ================================================================================================
# DISTRIBUTION — codesign + notarize + staple  (RUN MANUALLY with your Apple Developer ID)
# ================================================================================================
# These need YOUR signing identity and an App Store Connect API key, so they are documented here
# (and in docs/PACKAGING.md) rather than executed. Fill in the <PLACEHOLDERS>.
#
# 0. One-time: store notarytool credentials in the keychain (App Store Connect API key recommended):
#      xcrun notarytool store-credentials pacer-notary \
#        --key   /path/to/AuthKey_<KEYID>.p8 \
#        --key-id <KEYID> --issuer <ISSUER-UUID>
#
# 1. Codesign the .app with a hardened runtime + timestamp (deep-signs the bundled binaries,
#    Python framework, the _pacer .so, and ffmpeg/ffprobe):
#      codesign --force --deep --options runtime --timestamp \
#        --sign "Developer ID Application: <YOUR NAME> (<TEAMID>)" \
#        "dist/${APP_NAME}.app"
#      codesign --verify --deep --strict --verbose=2 "dist/${APP_NAME}.app"
#
# 2. Notarize. Notarize the .dmg (recreate it from the now-signed .app first), submit, and wait:
#      hdiutil create -volname "${APP_NAME}" -srcfolder "dist/${APP_NAME}.app" -ov -format UDZO "${DMG_PATH}"
#      xcrun notarytool submit "${DMG_PATH}" --keychain-profile pacer-notary --wait
#
# 3. Staple the notarization ticket so the app validates OFFLINE, then verify with Gatekeeper:
#      xcrun stapler staple "dist/${APP_NAME}.app"
#      xcrun stapler staple "${DMG_PATH}"
#      spctl --assess --type execute --verbose=4 "dist/${APP_NAME}.app"
# ================================================================================================
