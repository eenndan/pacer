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
# Bound every socket op of the demo fetch so a stalled/half-open TCP connection can't hang the UI
# thread forever (urlretrieve took no timeout; urlopen does). Applies per connect/read, not total.
_DEMO_TIMEOUT_S = 15.0


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
    import shutil
    import urllib.request

    url = url or os.environ.get("PACER_DEMO_URL") or _DEMO_URL
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        # urlopen (unlike urlretrieve) takes a timeout, so a stalled connection fails instead of
        # hanging the UI thread; stream to a temp sibling then rename so a partial/failed download
        # never looks like a valid cache hit.
        with urllib.request.urlopen(url, timeout=_DEMO_TIMEOUT_S) as resp, open(tmp, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
        os.replace(tmp, dest)
    except Exception as exc:  # network / IO / timeout — degrade gracefully to the empty welcome state
        print(f"demo: download failed ({exc}); launching the empty welcome state.", flush=True)
        if os.path.exists(tmp):
            os.remove(tmp)
        return False
    return os.path.isfile(dest)


def _resolve_local() -> str | None:
    """The OFFLINE half of the resolution order — the env var, then the cache — or None.

    Its own function because two callers want exactly this and only this: `resolve_demo_recording`
    before it considers the network, and `demo_available` (which must never reach it). Keeping it
    separate also means a test that stands in for the WORKER's resolve — the suite monkeypatches
    `resolve_demo_recording` to make a fetch slow, or to fail it — does not thereby decide whether
    the UI offers the button, which is a different question answered from the real filesystem."""
    env = os.environ.get("PACER_DEMO_MP4")
    if env and os.path.isfile(env):
        return env
    cached = demo_cache_path()
    return cached if os.path.isfile(cached) else None


def resolve_demo_recording(allow_download: bool = True) -> str | None:
    """Resolve a demo recording PATH for `--demo`, or None if unavailable (then the caller opens
    the normal empty state). Order: PACER_DEMO_MP4 env -> the local cache -> a one-time download
    (when `allow_download`). `allow_download=False` makes this a pure, offline path lookup (the test
    path)."""
    local = _resolve_local()
    if local is not None:
        return local
    cached = demo_cache_path()
    if allow_download and _try_download_demo(cached):
        return cached
    return None


def demo_available() -> bool:
    """Is there a demo recording ON THIS MACHINE, RIGHT NOW, that a click could open?

    This is what the welcome screen's second button is gated on, and it is deliberately the OFFLINE
    half of `resolve_demo_recording` — the env var or the cache, no network. Two reasons, and both
    are about not lying to the first-touch screen:

      * THE THIRD STEP IS A PROMISE NOBODY KEPT. `_DEMO_URL` points at a release asset that was
        never published (docs/FIRST_LAP.md says so in as many words, and distribution is an
        explicit non-goal), so on a machine with neither the env var nor a cache the button could
        only ever end at "Demo clip unavailable…" — the FIRST thing a portfolio reviewer who builds
        from source would see, from the obvious low-commitment click. A button whose only outcome
        is an apology should not be on screen.
      * A REACHABILITY PROBE IS NOT FREE AND NOT HONEST EITHER. Asking the network whether the asset
        exists means a blocking HEAD (or a worker + a button that changes its mind a second after
        the window opens) to answer a question about a file that is not there. The offline answer is
        exact, instant, and true.

    `--demo` on the CLI still tries the download — an explicit request gets an explicit attempt, and
    an explicit "couldn't fetch it" if that fails. This only decides whether the UI OFFERS it.
    """
    return _resolve_local() is not None
