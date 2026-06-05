#!/usr/bin/env bash
# Build the `timeline` app for the web (WebAssembly) via Emscripten + HelloImGui.
#
# Prerequisites (not bundled): the Emscripten SDK on PATH (emcc/emcmake).
#   git clone https://github.com/emscripten-core/emsdk && cd emsdk \
#     && ./emsdk install latest && ./emsdk activate latest \
#     && source ./emsdk_env.sh
#   # or: pixi add emscripten   (conda-forge)
#
# Output: build/Web/apps/timeline.html (+ .js/.wasm). Serve it over HTTP, e.g.:
#   python -m http.server -d build/Web/apps 8000   # then open localhost:8000/timeline.html
#
# Status: GROUNDWORK. HelloImGui handles the emscripten main loop and GL context.
# File ingestion (GPMF .MP4 / u-blox .dat via fopen) does NOT work in the browser
# sandbox without preloading data into emscripten's virtual FS (emcc
# --preload-file ...). Wire that up per-dataset; this script just produces the
# WASM/HTML shell.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v emcmake >/dev/null 2>&1; then
  echo "error: emcmake not found. Install/activate the Emscripten SDK first" >&2
  echo "       (see the header of this script)." >&2
  exit 1
fi

BUILD_DIR="build/Web"
emcmake cmake -S . -B "$BUILD_DIR" -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR" --target timeline

echo
echo "Built web app under $BUILD_DIR/apps/ (look for timeline.html)."
echo "Serve over HTTP, e.g.: python -m http.server -d $BUILD_DIR/apps 8000"
