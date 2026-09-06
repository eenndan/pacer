"""THE PUBLIC-PAGES GUARD — docs/index.html and the markdown beside it.

WHY THIS FILE EXISTS. The landing page is the one surface in this repo that nothing checked. It
was written in one afternoon, and over the next 458 commits it accumulated exactly the four failure
modes a guard can catch mechanically:

  * A STALE NUMBER. It printed "1,150+ laps · compared across two recordings", from adding the
    "300+" and "850+" that used to be in docs/ACCURACY.md. Those were the transponder log's lap-ID
    RANGES, not counts — the real figure is 107 clean laps. Wrong by a factor of ten, in the
    flattering direction, on the page's headline statistic. (Prose; not checkable here — but it is
    why the rest of this file exists.)
  * A DEAD IMAGE. `docs/FIRST_LAP.md` pointed at `docs/screenshot.png` for seven weeks; the file
    had been deleted in the very commit that created `docs/media/`. The page also referenced
    `media/plots.png` and `media/coaching.png` after both were deleted.
  * A LIE ABOUT AN IMAGE'S SHAPE. `hero.png` was pinned `width="1600" height="1000"` and
    `accuracy.png` `width="1200" height="560"`. The files behind them became 2880x1800 and
    2400x1080 — the attributes are what the browser reserves before the bytes arrive, so both
    figures reflowed and the second was distorted.
  * A HARDCODED PALETTE. Eight hexes copied out of `studio/theme.py` with nothing tying them back,
    plus a `#1a1206` (text on the amber button) that was never a token at all.

And, found while fixing the above, a fifth that is the reason check 1 leads: a `*/` inside the
prose of a CSS comment — the sequence `SPACE_*/RADIUS_*`, written as ordinary English — terminated
that comment early. Everything after it became garbage, CSS error recovery swallowed the entire
`:root` block, and the page rendered with NO tokens at all: black on white, full-bleed, no max
width. It still looked like a web page in a screenshot thumbnail. A parser is the only reviewer
that catches that.

THE FIVE CHECKS

  1. THE STYLESHEET PARSES. After stripping comments, `*/` may not appear anywhere (outside a
     comment that sequence is impossible), and `:root` must sit where a rule prelude can sit.
  2. EVERY HUE IS A theme.C TOKEN. Every colour-valued custom property carries a `/* C.<name> */`
     annotation and must EQUAL that token, and no raw colour may appear anywhere else in the file
     — including url-encoded (`%23RRGGBB`) inside the inline-SVG favicon, which is where the app's
     icon colours would otherwise drift unseen.
  3. EVERY IMAGE RESOLVES AND THE PAGE DECLARES ITS REAL SIZE. Both `<img>` files and the
     Open Graph card, checked against the PNG's own IHDR.
  4. EVERY LINK RESOLVES. In-page anchors against the document's ids; relative paths and
     `github.com/eenndan/pacer/blob/main/...` links against the working tree.
  5. THE PAGE STAYS SELF-CONTAINED. No script, no @import, no external stylesheet, no subresource
     on another host. The page's whole design is that it is one file plus its own images.

Plus check 6, the one that would have caught the seven-week-broken image: every markdown image
under docs/ resolves on disk.

Pure stdlib apart from importing `studio.theme` for the token values (Pacer-free, no QApplication,
no telemetry file), so it needs neither the offscreen env nor the bindings PYTHONPATH.
"""

from __future__ import annotations

import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import theme  # noqa: E402  (after the sys.path bootstrap above)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS = os.path.join(_REPO, "docs")
_PAGE = os.path.join(_DOCS, "index.html")

# github.com/<owner>/<repo>/blob/<ref>/<path> — the form the page uses for every repo document, so
# a renamed or deleted doc fails here instead of 404ing for a reader.
_BLOB = re.compile(r"https://github\.com/eenndan/pacer/blob/main/([^\"'#\s]+)")


def _page() -> str:
    with open(_PAGE, encoding="utf-8") as f:
        return f.read()


def _style_block(html: str) -> str:
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    assert m, "docs/index.html has no <style> block — the page is supposed to carry its own CSS"
    return m.group(1)


def _strip_css_comments(css: str) -> str:
    """`css` with every /* … */ removed, scanning left to right exactly as a CSS parser does — so
    a `*/` inside comment PROSE ends the comment here too, and check 1 sees the wreckage."""
    out, i = [], 0
    while True:
        a = css.find("/*", i)
        if a < 0:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:a])
        b = css.find("*/", a + 2)
        assert b > 0, f"unterminated CSS comment at offset {a}: {css[a:a + 120]!r}"
        i = b + 2


def _png_size(path: str) -> tuple[int, int]:
    """(width, height) from a PNG's IHDR — 8-byte signature, 4-byte length, 4-byte 'IHDR', then
    two big-endian uint32. Stdlib only; no Pillow on this machine."""
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR", f"{path} is not a PNG"
    return struct.unpack(">II", head[16:24])


def _theme_hues() -> dict[str, str]:
    """Every colour token on `theme.C`, by its Python name."""
    return {k: v for k, v in vars(theme.C).items()
            if not k.startswith("_") and isinstance(v, str)}


def _rgb(value: str) -> tuple[int, int, int]:
    """(r, g, b) from `#RRGGBB` or `rgba(r,g,b,a)`; the alpha is deliberately dropped so a token
    used at a reduced opacity still has to be the token's HUE."""
    v = value.strip().replace(" ", "")
    if v.startswith("#"):
        return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    m = re.match(r"rgba?\((\d+),(\d+),(\d+)", v)
    assert m, f"not a colour: {value!r}"
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _is_colour(value: str) -> bool:
    return "#" in value or value.strip().startswith("rgb")


def _without_comments(html: str) -> str:
    """`html` with HTML comments and the stylesheet's CSS comments removed — what the browser
    actually acts on. Every check that looks for a FORBIDDEN string runs on this, because both this
    file and the page quote the things they forbid while explaining why."""
    css = _style_block(html)
    return re.sub(r"<!--.*?-->", "", html.replace(css, _strip_css_comments(css)), flags=re.S)


# ------------------------------------------------------------------ 1. the stylesheet parses
def test_stylesheet_parses():
    """No `*/` outside a comment, and `:root` still sits where a rule can start.

    THE BUG: `SPACE_*/RADIUS_*` in comment prose closed its comment 400 characters early. The rest
    of the sentence became a rule prelude, error recovery ate the `:root { … }` block whole, and
    every `var()` on the page fell back to its initial value. Chrome reported no error, the DOM was
    intact, and the page still rendered — as unstyled black-on-white."""
    css = _style_block(_page())
    stripped = _strip_css_comments(css)
    assert "*/" not in stripped, (
        "a `*/` survives outside any comment — some comment's PROSE contains the terminator "
        "sequence (write `SPACE_ / RADIUS_`, not `SPACE_*/RADIUS_*`). Everything after it is "
        f"parsed as CSS: {stripped[max(0, stripped.find('*/') - 80):stripped.find('*/') + 40]!r}")

    at = stripped.find(":root")
    assert at >= 0, ":root is gone from the stylesheet"
    before = stripped[:at].rstrip()
    assert before == "" or before.endswith("}"), (
        "`:root` does not begin a rule — the text in front of it is not a closed rule, which means "
        f"a preceding construct is still open: {before[-160:]!r}")
    print("test_stylesheet_parses OK")


# ------------------------------------------------------------------ 2. every hue is a token
def test_palette_is_derived_from_theme():
    """Every colour on the page equals a `theme.C` token, and says WHICH one.

    The page's eight hexes had not in fact drifted — measured, all eight still matched `theme.C`
    exactly. What had no source at all was `#1a1206`, the text colour on the amber button, where
    the design system's answer is `C.on_accent` (#15181E); and the inline-SVG favicon hardcoded the
    accent twice with the app icon's two chevrons in the wrong depth order. Neither was a drift a
    reader could have seen. Both are the kind only a comparison finds."""
    html = _page()
    css = _style_block(html)
    hues = _theme_hues()
    assert len(hues) >= 15 and "canvas" in hues and "accent" in hues, (
        f"theme.C parsed to {len(hues)} colour tokens — this check has gone vacuous")

    root = re.search(r":root\s*\{(.*?)\n  \}", css, re.S)
    assert root, ":root block not found in the stylesheet"
    body = root.group(1)

    unsourced, wrong = [], []
    for decl, value, note in re.findall(
            r"(--[\w-]+):\s*([^;]+);(?:\s*/\*\s*([^*]*?)\s*\*/)?", body):
        if not _is_colour(value):
            continue
        m = re.match(r"C\.(\w+)", note or "")
        if not m:
            unsourced.append(f"{decl}: {value.strip()}")
            continue
        token = m.group(1)
        if token not in hues:
            wrong.append(f"{decl} cites C.{token}, which does not exist")
        elif _rgb(value) != _rgb(hues[token]):
            wrong.append(f"{decl} is {value.strip()} but theme.C.{token} is {hues[token]}")
    assert not unsourced, (
        "colour custom properties with no `/* C.<token> */` annotation naming their source in "
        f"studio/theme.py: {unsourced}")
    assert not wrong, f"the page has drifted from studio/theme.py: {wrong}"

    # No colour anywhere else in the file — including url-encoded, which is how the favicon's
    # data: URI spells the two chevron colours. Comments are stripped first: this file's own prose
    # quotes the hexes it is arguing about, and so does the page's.
    css_nc = _strip_css_comments(css)
    outside = _without_comments(html)
    root_nc = re.search(r":root\s*\{.*?\n  \}", css_nc, re.S)
    assert root_nc, ":root block not found after comment stripping"
    outside = outside.replace(root_nc.group(0), "")
    strays = [h for h in re.findall(r"(?:#|%23)([0-9A-Fa-f]{6})\b", outside)]
    known = {_rgb("#" + h) for h in [v.lstrip("#") for v in hues.values() if v.startswith("#")]}
    bad = [f"#{h}" for h in strays if _rgb("#" + h) not in known]
    assert not bad, (
        f"raw colours outside the token block that are not theme.C values: {sorted(set(bad))} — "
        "use var(--token), or add the hue to studio/theme.py if it is genuinely new")

    # Both directions, the way this repo's other guards check their exemption sets: a token block
    # listing things nothing uses is the same "assembled rather than designed" smell the spatial
    # system was introduced to kill. Three tokens (--on-accent, --radius-l, --surface-hover) were
    # carried across from the app's set on the way in and used by nothing.
    declared = set(re.findall(r"(--[\w-]+)\s*:", body))
    used = set(re.findall(r"var\(\s*(--[\w-]+)\s*[,)]", html))
    dead = sorted(declared - used)
    assert not dead, f"custom properties declared in :root and used nowhere: {dead}"
    print(f"test_palette_is_derived_from_theme OK ({len(hues)} theme.C tokens, "
          f"{len(declared)} page tokens all used, {len(strays)} in-markup colours all theme.C)")


# ------------------------------------------------------------------ 3. images and their sizes
def test_images_resolve_and_declare_their_real_size():
    """Every `<img>` and the OG card exists, and the size the page reserves is the size on disk.

    `width`/`height` are the aspect ratio the browser lays out with BEFORE the bytes arrive. Pinned
    at 1600x1000 for a 2880x1800 file the reservation is merely wrong; pinned at 1200x560 for a
    2400x1080 one it is a different ratio, and the figure is squashed until it loads."""
    html = _page()
    checked = []
    for tag in re.findall(r"<img\b[^>]*>", html):
        src = re.search(r'src="([^"]+)"', tag)
        assert src, f"<img> with no src: {tag[:90]}"
        path = os.path.join(_DOCS, src.group(1))
        assert os.path.exists(path), f"missing image: docs/{src.group(1)}"
        w = re.search(r'width="(\d+)"', tag)
        h = re.search(r'height="(\d+)"', tag)
        assert w and h, (
            f"docs/{src.group(1)} has no width/height — the page must reserve its box, or the "
            "layout reflows when it loads")
        real = _png_size(path)
        assert real == (int(w.group(1)), int(h.group(1))), (
            f"docs/{src.group(1)} is {real[0]}x{real[1]} but the page declares "
            f"{w.group(1)}x{h.group(1)}")
        alt = re.search(r'alt="([^"]*)"', tag)
        assert alt and len(alt.group(1)) > 20, (
            f"docs/{src.group(1)} has no substantive alt text — it is one of the images the page's "
            "argument is made of, and it has to survive the image not loading")
        checked.append(src.group(1))

    og = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    assert og, "no og:image — a case study still wants to render when it is shared"
    og_path = os.path.join(_DOCS, og.group(1))
    assert os.path.exists(og_path), f"missing og:image: docs/{og.group(1)}"
    ow = re.search(r'<meta property="og:image:width" content="(\d+)"', html)
    oh = re.search(r'<meta property="og:image:height" content="(\d+)"', html)
    assert ow and oh, "og:image declares no width/height"
    assert _png_size(og_path) == (int(ow.group(1)), int(oh.group(1))), (
        f"og:image is {_png_size(og_path)} but the page declares {ow.group(1)}x{oh.group(1)}")
    assert len(checked) >= 5, f"only {len(checked)} images on the page — check has gone vacuous"
    print(f"test_images_resolve_and_declare_their_real_size OK ({len(checked)} + og)")


# ------------------------------------------------------------------ 4. every link resolves
def test_links_resolve():
    """In-page anchors resolve to ids; repo links resolve to files in the working tree."""
    html = _page()
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    hrefs = re.findall(r'href="([^"]+)"', html)

    dangling = sorted({h for h in hrefs if h.startswith("#") and h[1:] not in ids})
    assert not dangling, f"in-page links to ids that do not exist: {dangling}"

    missing = sorted({p for p in _BLOB.findall(html)
                      if not os.path.exists(os.path.join(_REPO, p))})
    assert not missing, f"links to repo files that do not exist: {missing}"

    rel = sorted({h for h in hrefs
                  if not h.startswith(("#", "http://", "https://", "mailto:", "data:"))
                  and not os.path.exists(os.path.join(_DOCS, h))})
    assert not rel, f"relative links that do not resolve from docs/: {rel}"

    # Both directions: every long-lived document should actually be reachable from the page, which
    # for most of this repo's life linked ACCURACY.md and nothing else.
    linked = set(_BLOB.findall(html))
    for want in ("README.md", "CHANGELOG.md", "docs/ACCURACY.md", "docs/FIRST_LAP.md"):
        assert want in linked, f"the landing page does not link {want}"
    print(f"test_links_resolve OK ({len(ids)} ids, {len(linked)} repo docs linked)")


# ------------------------------------------------------------------ 5. self-contained
def test_page_is_self_contained():
    """One file plus its own images: no script, no webfont, no CDN, no third-party subresource.

    This is the page's whole design, and it is the reason it can be read (and audited) as text.
    `href="https://…"` NAVIGATION is fine; a subresource on another host is not."""
    html = _without_comments(_page())
    assert "<script" not in html.lower(), "the landing page is deliberately JavaScript-free"
    assert "@import" not in html, "@import pulls in another stylesheet"
    assert not re.search(r'<link[^>]+rel="stylesheet"', html), "external stylesheet"
    assert "@font-face" not in html, "a webfont would be a third-party request"
    remote = re.findall(r'(?:src|url\()\s*=?\s*["\']?(https?://[^"\')\s]+)', html)
    assert not remote, f"subresources fetched from another host: {remote}"
    print("test_page_is_self_contained OK")


# ------------------------------------------------------------------ 6. markdown images
def test_markdown_images_resolve():
    """Every image referenced from a doc under docs/ exists.

    docs/FIRST_LAP.md served a broken image for seven weeks: it pointed at `screenshot.png`, which
    was deleted in the SAME commit that created `docs/media/`. Nobody opened the file."""
    broken, n = [], 0
    for name in sorted(os.listdir(_DOCS)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(_DOCS, name), encoding="utf-8") as f:
            text = f.read()
        for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
            if target.startswith(("http://", "https://")):
                continue
            n += 1
            if not os.path.exists(os.path.join(_DOCS, target)):
                broken.append(f"docs/{name} → {target}")
    assert not broken, f"markdown images that do not exist: {broken}"
    assert n >= 1, "no markdown images found — check has gone vacuous"
    print(f"test_markdown_images_resolve OK ({n} images)")


if __name__ == "__main__":
    test_stylesheet_parses()
    test_palette_is_derived_from_theme()
    test_images_resolve_and_declare_their_real_size()
    test_links_resolve()
    test_page_is_self_contained()
    test_markdown_images_resolve()
    print("ALL OK")
