"""Demo-download resolution (fix/demo-download-timeout): the fetch must be bounded, atomic, and
keep only the published file.

studio.demo is pacer-free and Qt-free (pure path resolution + a urllib fetch), so this stubs
urlopen and never touches the network. It pins: the fetch passes a FINITE TIMEOUT (a stalled TCP
must not hang the UI thread — the bug being fixed); a success streams to <dest>.part then renames to
<dest> with no leftover; a failure (timeout, or bytes that are not the pinned sha256) returns False,
removes the .part, and leaves no cache hit; and CI's reachability step runs this same fetch rather
than a copy of its URL. The demo itself (synthetic, opened in the real window) is
tests/test_demo_session.py. Run:  python tests/test_demo.py
"""
import hashlib
import io
import os
import re
import sys
import tempfile
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from studio import demo  # noqa: E402


class _FakeResp(io.BytesIO):
    """A urlopen() context-manager stand-in over in-memory bytes."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def test_download_passes_a_finite_timeout_and_is_atomic():
    """Success path: urlopen is called WITH a finite timeout (the fix), the body streams to dest,
    and no .part is left behind."""
    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"], seen["timeout"] = url, timeout
        return _FakeResp(b"MP4-BYTES")

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "demo", "clip.mp4")
            assert demo._try_download_demo(dest, url="http://example/clip.mp4",
                                           sha256=hashlib.sha256(b"MP4-BYTES").hexdigest()) is True
            # the regression guard: a finite, positive timeout is always passed
            assert isinstance(seen["timeout"], (int, float)) and seen["timeout"] > 0, seen
            with open(dest, "rb") as f:
                assert f.read() == b"MP4-BYTES"
            assert not os.path.exists(dest + ".part")   # atomic: no leftover partial
    finally:
        urllib.request.urlopen = orig
    print("ok demo download: finite timeout passed + atomic rename, no .part")


def test_download_timeout_degrades_and_leaves_no_partial():
    """A stalled connection (socket.timeout) returns False, removes the .part, and creates no dest —
    so resolve_demo_recording falls back to the empty welcome instead of a bogus cache hit."""
    def fake_urlopen(url, timeout=None):
        raise TimeoutError("timed out")

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "demo", "clip.mp4")
            assert demo._try_download_demo(dest, url="http://example/clip.mp4") is False
            assert not os.path.exists(dest)
            assert not os.path.exists(dest + ".part")
    finally:
        urllib.request.urlopen = orig
    print("ok demo timeout: degrades to False, no partial file")


def test_a_download_that_is_not_the_published_demo_is_refused():
    """The fetch keeps ONLY the pinned file: bytes with another sha256 — a truncated body, an error
    page served as 200, a different upload at the URL — return False and leave no cache hit and no
    .part, exactly like a timeout. With no `sha256` argument the pin is `_DEMO_SHA256`."""
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda url, timeout=None: _FakeResp(b"NOT-THE-DEMO")
    try:
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "demo", "clip.mp4")
            for kw in ({}, {"sha256": hashlib.sha256(b"MP4-BYTES").hexdigest()}):
                assert demo._try_download_demo(dest, url="http://example/clip.mp4", **kw) is False, kw
                assert not os.path.exists(dest) and not os.path.exists(dest + ".part"), kw
    finally:
        urllib.request.urlopen = orig
    assert re.fullmatch(r"[0-9a-f]{64}", demo._DEMO_SHA256), demo._DEMO_SHA256
    print("ok demo download: a file that is not the pinned demo is refused, nothing cached")


def test_a_cached_older_demo_is_not_the_pinned_one():
    """The cache is trusted by EXISTENCE (`_resolve_local` hashes nothing — a hash of 11 MB on every
    launch would be the price), so it is versioned by the pinned tag instead. Until demo-data-v2 it
    was one path, `<app-support>/demo/pacer-synthetic-demo.mp4`: every machine that had run `--demo`
    on v1 would have gone on opening v1 after the re-pin, with the welcome button offering it. A copy
    at that old path, or under an older tag, must resolve to nothing; the pinned tag's copy is found,
    under the same file name (the window title says "synthetic" from it)."""
    orig_dir, orig_env = demo._app_support_dir, os.environ.pop("PACER_DEMO_MP4", None)
    try:
        with tempfile.TemporaryDirectory() as d:
            demo._app_support_dir = lambda: d
            older = [os.path.join(d, "demo", "pacer-synthetic-demo.mp4"),          # v1's cache
                     os.path.join(d, "demo", "demo-data-v1", "pacer-synthetic-demo.mp4")]
            for path in older:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(b"AN-OLDER-DEMO")
                got = demo.resolve_demo_recording(allow_download=False)
                assert got is None, f"an older pre-release's demo opened as the pinned one: {got}"
                assert demo.demo_available() is False, f"the welcome would offer {path}"
            pinned = demo.demo_cache_path()
            want = os.path.join(d, "demo", demo._DEMO_TAG, "pacer-synthetic-demo.mp4")
            assert pinned == want, (pinned, want)
            os.makedirs(os.path.dirname(pinned), exist_ok=True)
            with open(pinned, "wb") as f:
                f.write(b"THE-PINNED-DEMO")
            assert demo.resolve_demo_recording(allow_download=False) == pinned
            assert demo.demo_available() is True
    finally:
        demo._app_support_dir = orig_dir
        if orig_env is not None:
            os.environ["PACER_DEMO_MP4"] = orig_env
    print("ok demo cache: an older demo's copy is never the pinned one")


def test_ci_checks_the_fetch_the_app_makes():
    """CI's non-blocking "demo asset" step used to probe its OWN copy of the URL ("keep in sync"),
    which is how a check can go on passing for a URL the app no longer fetches. It now runs the
    app's own `_try_download_demo` — URL, timeout and checksum — so it cannot drift from `--demo`."""
    with open(os.path.join(_REPO, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
        ci = f.read()
    start = ci.index("- name: Demo asset reachable")
    step = ci[start:ci.index("- name:", start + 1)] if "- name:" in ci[start + 1:] else ci[start:]
    assert "continue-on-error: true" in step, "the demo check must stay non-blocking"
    assert "demo._try_download_demo(" in step, "the demo check does not run the app's own fetch"
    assert "releases/download" not in step, "the demo check carries its own copy of the URL"
    print("ok CI's demo check runs the app's own fetch")


if __name__ == "__main__":
    test_download_passes_a_finite_timeout_and_is_atomic()
    test_download_timeout_degrades_and_leaves_no_partial()
    test_a_download_that_is_not_the_published_demo_is_refused()
    test_a_cached_older_demo_is_not_the_pinned_one()
    test_ci_checks_the_fetch_the_app_makes()
    print("\n5 demo tests passed")
