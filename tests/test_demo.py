"""Demo-download resolution (fix/demo-download-timeout): the fetch must be bounded + atomic.

studio.demo is pacer-free and Qt-free (pure path resolution + a urllib fetch), so this stubs
urlopen and never touches the network. It pins: the fetch passes a FINITE TIMEOUT (a stalled TCP
must not hang the UI thread — the bug being fixed); a success streams to <dest>.part then renames to
<dest> with no leftover; and a failure (timeout) returns False, removes the .part, and leaves no
cache hit. Run:  python tests/test_demo.py
"""
import io
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
            assert demo._try_download_demo(dest, url="http://example/clip.mp4") is True
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


if __name__ == "__main__":
    test_download_passes_a_finite_timeout_and_is_atomic()
    test_download_timeout_degrades_and_leaves_no_partial()
    print("\n2 demo tests passed")
