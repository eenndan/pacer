"""Demo recording resolution for `python -m studio --demo`.

The clips bundled inside the .app (3rdparty/gpmf-parser/samples) are tiny GoPro test clips with NO
real laps (Session reports 0 valid laps on them) — fine as a "the app launched" smoke fixture, but
they show nothing in the lap table / delta plots, so a first-run user sees an empty studio.

`--demo` instead loads a SMALL real lapping recording. We deliberately do NOT commit that media to
the repo (the brief: keep it lightweight, no large media), so it is resolved at runtime in this
order:

  1. PACER_DEMO_MP4 env var      — an explicit path (a dev who already has a recording; also the
     test seam).
  2. a cached copy under <app-support>/pacer/demo/  — downloaded once, reused forever.
  3. a one-time download from the pinned v0.1.0 GitHub release asset into that cache (best-effort).
     Override the URL with PACER_DEMO_URL.

If none resolve (offline first run, no env, download failed) `resolve_demo_recording` returns None
and the caller falls back to the normal empty welcome state — the app still launches.

PACER-FREE: pure path resolution + a best-effort urllib fetch. No Qt, no pacer, so it is unit
testable with the network stubbed.
"""

from __future__ import annotations

import os

# Pinned demo asset on the v0.1.0 release. A small (single-chapter) real lapping recording uploaded
# to the GitHub release / attached via the release page — kept OUT of the git tree on purpose (see
# docs/PACKAGING.md "Demo data"). Override with PACER_DEMO_URL for a local mirror.
_DEMO_URL = (
    "https://github.com/eenndan/pacer/releases/download/v0.1.0/pacer-demo-lap.mp4"
)
_DEMO_FILENAME = "pacer-demo-lap.mp4"
_APP_DIR_NAME = "pacer"


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). A separate seam from
    library._app_support_dir so a test can divert the demo cache without touching the library."""
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def demo_cache_path() -> str:
    """Absolute path the demo recording is cached at (<app-support>/pacer/demo/<file>). Does NOT
    create the directory — the fetch makes it lazily."""
    return os.path.join(_app_support_dir(), "demo", _DEMO_FILENAME)


def _try_download_demo(dest: str, url: str | None = None) -> bool:
    """Best-effort download of the demo recording to `dest`: network/IO failures are swallowed and
    return False. Writes to a temp sibling then renames so a half-download never looks like a valid
    cache hit."""
    import urllib.request

    url = url or os.environ.get("PACER_DEMO_URL") or _DEMO_URL
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 (pinned GitHub release asset)
        os.replace(tmp, dest)
    except Exception as exc:  # network / IO — degrade gracefully to the empty welcome state
        print(f"demo: download failed ({exc}); launching the empty welcome state.", flush=True)
        if os.path.exists(tmp):
            os.remove(tmp)
        return False
    return os.path.isfile(dest)


def resolve_demo_recording(allow_download: bool = True) -> str | None:
    """Resolve a demo recording PATH for `--demo`, or None if unavailable (then the caller opens
    the normal empty state). Order: PACER_DEMO_MP4 env -> the local cache -> a one-time download
    (when `allow_download`). `allow_download=False` makes this a pure, offline path lookup (the test
    path)."""
    env = os.environ.get("PACER_DEMO_MP4")
    if env and os.path.isfile(env):
        return env
    cached = demo_cache_path()
    if os.path.isfile(cached):
        return cached
    if allow_download and _try_download_demo(cached):
        return cached
    return None
