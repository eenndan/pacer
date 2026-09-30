"""Demo recording resolution for `python -m studio --demo`.

The clips bundled inside the .app (3rdparty/gpmf-parser/samples) are tiny GoPro test clips with NO
real laps (Session reports 0 valid laps on them) — fine as a "the app launched" smoke fixture, but
they show nothing in the lap table / delta plots, so a first-run user sees an empty studio.

`--demo` instead loads the DEMO SESSION: a SYNTHETIC recording — generated, not filmed — of a
simulated kart lapping a fictional circuit, written by `studio/dev/make_demo.py` (`pixi run
make-demo`). No person, kart, place or camera footage is in it, and its picture says so on every
frame. The circuit is a built-in track, so the session opens with VERIFIED timing: laps, corners,
the ideal lap and a ranked Coaching page with nobody's footage but its own. The file (about 11 MB,
`_DEMO_BYTES`) is deliberately NOT committed to the repo, so it is resolved at runtime in this order:

  1. PACER_DEMO_MP4 env var      — an explicit path (a dev who already has a recording; also the
     test seam).
  2. a cached copy under <app-support>/pacer/demo/<_DEMO_TAG>/ — downloaded once, reused forever.
     The cache is per TAG: a copy of an older demo (fetched from an older pre-release) is never
     found again, so a re-pinned demo cannot hide behind one.
  3. a one-time download of the pinned asset on the pinned demo-data pre-release (`_DEMO_TAG`)
     into that cache (best-effort), kept only if its sha256 is `_DEMO_SHA256`. PACER_DEMO_URL
     overrides the URL (a mirror of the same file: the checksum still applies).

If none resolve (offline first run, no env, download failed) `resolve_demo_recording` returns None
and the caller falls back to the normal empty welcome state — the app still launches.

PACER-FREE: pure path resolution + a best-effort urllib fetch. No Qt, no pacer, so it is unit
testable with the network stubbed.
"""

from __future__ import annotations

import hashlib
import logging
import os

from . import app_support

_log = logging.getLogger(__name__)

# The pinned demo asset: `pixi run make-demo`'s single-chapter synthetic session, attached to the
# `_DEMO_TAG` pre-release — kept OUT of the git tree on purpose (see docs/PACKAGING.md "Demo
# data"). Override with PACER_DEMO_URL for a local mirror. A download is kept only if it is THIS
# file, byte for byte: a truncated fetch, or anything else answering at the URL, must never open
# as the demo. Re-publishing a changed demo means a NEW tag (an older one stays up: older clones
# pin it) and moving the three pins below together, with `make_demo.PUBLISHED_LAP_MS`. Since v2
# the demo carries one planted habit at C1 (`make_demo.DEMO_HABIT`): its debrief has a call to make.
_DEMO_TAG = "demo-data-v2"
_DEMO_URL = f"https://github.com/eenndan/pacer/releases/download/{_DEMO_TAG}/pacer-demo.mp4"
_DEMO_SHA256 = "d769608b1d8a4f48e93829ce024af9beb4ab9a2d84c15322d4ce5735676069ca"
# ...and its size, which the sha fixes: what the download line tells a first `--demo` it is waiting
# for, and the "about N MB" the public pages quote (tests/test_landing_page.py holds them to it).
_DEMO_BYTES = 11_130_046
# The CACHE's name, which is what the window title shows for a non-GoPro file: it says what the
# recording is, so the title bar keeps saying so while the video pane is collapsed.
_DEMO_FILENAME = "pacer-synthetic-demo.mp4"
# Bound every socket op of the demo fetch so a stalled/half-open TCP connection can't hang the UI
# thread forever (urlretrieve took no timeout; urlopen does). Applies per connect/read, not total.
_DEMO_TIMEOUT_S = 15.0


def _app_support_dir() -> str:
    """macOS app-support dir for pacer (~/Library/Application Support/pacer). A separate seam from
    library._app_support_dir so a test can divert the demo cache without touching the library.
    Resolves through ``app_support.resolve`` like every store."""
    return app_support.resolve()


def demo_cache_path() -> str:
    """Absolute path the demo recording is cached at (<app-support>/pacer/demo/<_DEMO_TAG>/<file>).
    Per tag, because the cache is trusted by EXISTENCE (`_resolve_local` hashes nothing): one
    shared path would keep opening whichever demo the machine fetched first. An older tag's copy
    is left where it is, unused. Does NOT create the directory — the fetch makes it lazily."""
    return os.path.join(_app_support_dir(), "demo", _DEMO_TAG, _DEMO_FILENAME)


def _try_download_demo(dest: str, url: str | None = None, sha256: str | None = None) -> bool:
    """Best-effort download of the demo recording to `dest`: network/IO failures are swallowed and
    return False. Writes to a temp sibling then renames so a half-download never looks like a valid
    cache hit — and renames only a file whose sha256 is `sha256` (default: the pinned asset's)."""
    import urllib.request

    url = url or os.environ.get("PACER_DEMO_URL") or _DEMO_URL
    want = sha256 or _DEMO_SHA256
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    try:
        # A first `--demo` blocks here before any window exists, so say what is happening and how
        # much of it there is — and this is the app's one network fetch, so the session log
        # records where it went.
        _log.info("downloading the synthetic demo session, once (%s MB) from %s …",
                  f"{_DEMO_BYTES / 1e6:.0f}", url)
        # urlopen (unlike urlretrieve) takes a timeout, so a stalled connection fails instead of
        # hanging the UI thread; stream to a temp sibling then rename so a partial/failed download
        # never looks like a valid cache hit.
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=_DEMO_TIMEOUT_S) as resp, open(tmp, "wb") as out:  # noqa: S310
            for chunk in iter(lambda: resp.read(1 << 16), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != want:
            raise ValueError(f"not the published demo (sha256 {digest.hexdigest()}, expected {want})")
        os.replace(tmp, dest)
    except Exception as exc:  # network / IO / timeout — degrade gracefully to the empty welcome state
        _log.warning("demo download failed (%s); launching the empty welcome state", exc)
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

      * THE GATE WAS BORN WHEN THE THIRD STEP WAS A PROMISE NOBODY KEPT. `_DEMO_URL` pointed at a
        release asset that was never published, so on a machine with neither the env var nor a
        cache the button could only ever end at "Demo clip unavailable…" — the FIRST thing a
        portfolio reviewer who builds from source would see. The synthetic demo is published now
        (`_DEMO_TAG`), but an offline machine is still exactly that machine.
      * A REACHABILITY PROBE IS NOT FREE AND NOT HONEST EITHER. Asking the network whether the asset
        exists means a blocking HEAD (or a worker + a button that changes its mind a second after
        the window opens), and the app's promise is that it reaches the network only when asked
        to. The offline answer is exact, instant, and true.

    `--demo` on the CLI is that request — it tries the download and says plainly if it can't (the
    README's `pixi run studio -- --demo`); once it has, the cache offers the button on every later
    launch. This only decides whether the UI OFFERS it.
    """
    return _resolve_local() is not None
