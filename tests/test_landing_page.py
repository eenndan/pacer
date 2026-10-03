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
     `github.com/eenndan/pacer/blob/main/...` links against the working tree. And every
     `pixi run <task>` a public page quotes against pyproject.toml's tasks.
  5. THE PAGE STAYS SELF-CONTAINED. No script, no @import, no external stylesheet, no subresource
     on another host. The page's whole design is that it is one file plus its own images.

Plus check 6, the one that would have caught the seven-week-broken image: every markdown image
under docs/ resolves on disk. And check 7, the page's one clip: small, 720p, 20-30 s, still for a
reader who asked for reduced motion, and with no audio track in the file (with its own control).
And check 8, reach without hue or mouse: a link inside a sentence is underlined, not told apart by
hue alone, anything that scrolls takes keyboard focus, and the page has one <main> and nothing
outside a landmark (axe's rules) with the bar a named <nav> (the page's own).

Pure stdlib apart from importing `studio.theme` for the token values (Pacer-free, no QApplication,
no telemetry file), so it needs neither the offscreen env nor the bindings PYTHONPATH.
"""

from __future__ import annotations

import ast
import os
import re
import struct
import sys
from html import unescape as _unescape
from html.parser import HTMLParser

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


def _image_size(path: str) -> tuple[int, int]:
    """(width, height) of a PNG or a JPEG, by its extension: the page's one photograph, the export
    frame, is a JPEG, and every UI shot is a PNG. Each reader asserts the bytes are what the name
    says."""
    return _jpeg_size(path) if path.lower().endswith((".jpg", ".jpeg")) else _png_size(path)


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
        real = _image_size(path)
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
    # ABSOLUTE, on the page's own site. Open Graph and X cards want an absolute image URL: the page
    # said "media/og.png", so a link to it shared on LinkedIn, X or Slack showed no image (board
    # review 2026-09-23, CEO-4). The URL maps back onto docs/ to check the file behind it.
    site = re.search(r'<meta property="og:url" content="([^"]+)"', html)
    assert site and og.group(1).startswith(site.group(1)), (
        f"og:image is {og.group(1)!r}: a link preview needs an absolute URL on the page's own site "
        f"({site and site.group(1)!r})")
    og_rel = og.group(1)[len(site.group(1)):]
    og_path = os.path.join(_DOCS, og_rel)
    assert os.path.exists(og_path), f"missing og:image: docs/{og_rel}"
    tw = re.search(r'<meta name="twitter:image" content="([^"]+)"', html)
    assert tw and tw.group(1) == og.group(1), (
        f"twitter:image is {tw and tw.group(1)!r}, not og:image {og.group(1)!r}")
    for alt_tag in ('property="og:image:alt"', 'name="twitter:image:alt"'):
        alt = re.search(rf'<meta {alt_tag} content="([^"]+)"', html)
        assert alt and len(alt.group(1)) > 20, f"the shared card has no substantive <meta {alt_tag}>"
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


# The tools the pixi env ships, which `pixi run` runs as plain commands when no task has the name.
_ENV_COMMANDS = {"python", "ctest"}


def _unknown_pixi_commands(pages: dict[str, str], tasks: set[str]) -> list[str]:
    """The `pixi run <name>` commands `pages` tell a reader to run whose name is neither a task in
    pyproject.toml nor a tool of the env. A function of the text."""
    return sorted({f"{rel}: `pixi run {name}`" for rel, text in pages.items()
                   for name in re.findall(r"pixi run ([a-z][\w-]*)", text)
                   if name not in tasks and name not in _ENV_COMMANDS})


def test_every_quoted_pixi_command_exists():
    """The links check's twin for commands: a `pixi run <task>` on a public page names a real task.

    The accuracy pages end on `pixi run verify`, the one command a visitor runs to check the timing
    against a synthetic recording's known truth. A renamed or dropped task would leave them
    promising a command that fails before it measures anything."""
    import tomllib
    with open(os.path.join(_REPO, "pyproject.toml"), "rb") as f:
        tasks = set(tomllib.load(f)["tool"]["pixi"]["tasks"])
    rels = ["README.md", os.path.join("docs", "index.html")] + sorted(
        os.path.join("docs", n) for n in os.listdir(_DOCS) if n.endswith(".md"))
    pages = {}
    for rel in rels:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            pages[rel] = f.read()
    unknown = _unknown_pixi_commands(pages, tasks)
    assert not unknown, "public pages quote commands pyproject.toml has no task for:\n  " + \
        "\n  ".join(unknown)
    quoted = {n for t in pages.values() for n in re.findall(r"pixi run ([a-z][\w-]*)", t)}
    assert "verify" in quoted, "no public page offers `pixi run verify` any more: the check is vacuous"
    # Both directions, on planted text: a misspelt task fails, a real task and an env tool pass.
    assert _unknown_pixi_commands({"planted": "run `pixi run verfy`"}, tasks) == [
        "planted: `pixi run verfy`"]
    assert not _unknown_pixi_commands({"planted": "`pixi run golden`, `pixi run python -m x`"},
                                      tasks)
    print(f"test_every_quoted_pixi_command_exists OK ({len(quoted)} commands on {len(pages)} pages)")


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


# ------------------------------------------------------------------ 7. the counts the pages quote
# WHY THESE TWO AND NOT EVERY NUMBER ON THE PAGE. A count the pages state about THE REPO ITSELF is
# the only kind that goes stale with no edit to the sentence carrying it — every merge moves it and
# nothing points at the prose. "96 CTest registrations" was written when there were 96 and was read
# as true through 18 more; both public surfaces carried it. Measured numbers ABOUT A RECORDING
# (σ 0.0527 s, 107 clean laps) do not rot that way — they are facts about a fixture, and they are
# pinned where the fixture is.
_CMAKE = os.path.join(_REPO, "tests", "CMakeLists.txt")
_CORE = os.path.join(_REPO, "pacer")
# The page may round the core's size (the suite count is a floor instead: _SUITE_FLOOR_SLACK).
# 5 % is wide enough that an ordinary core edit does not fail the build over a stale digit, and
# narrow enough that the claim cannot quietly become a different order of thing.
_CORE_TOLERANCE = 0.05


def _ctest_registrations() -> int:
    """How many tests `ctest -N` would list, derived from tests/CMakeLists.txt and what it globs.

    Five registration forms: the `add_pacer_test` macro (one Catch2 executable each), every
    `tests/test_*.py` (registered by the file's glob, one each), `add_footage_test` (one real-footage
    check each, which CTest reports Skipped wherever its recording is absent — CI included),
    `add_videotoolbox_test` (one hardware-encoder check each, Skipped wherever VideoToolbox is
    absent — CI again) and `add_soak_test` (one soak each, reported Skipped wherever PACER_SOAK is
    not 1). Derived rather than pinned, so ADDING A TEST updates the expected number by itself and
    only the PROSE has to catch up. Until 2026-09-27 this counted four of the five, 170 against
    `ctest -N`'s 176, which a floor 25 wide never noticed."""
    with open(_CMAKE, encoding="utf-8") as f:
        text = f.read()
    assert re.search(r"file\(GLOB \w+ CONFIGURE_DEPENDS \S*/test_\*\.py\)", text), (
        "tests/CMakeLists.txt no longer registers tests/test_*.py by its glob — count the Python "
        "suites the way it registers them now")
    catch2 = len(re.findall(r"^add_pacer_test\(", text, re.M))
    python = len([n for n in os.listdir(os.path.dirname(_CMAKE))
                  if n.startswith("test_") and n.endswith(".py")])
    skipped = _skippable_registrations()
    assert catch2 and python, "a registration form found nothing — this check has gone vacuous"
    return catch2 + python + sum(skipped.values())


def _skippable_registrations() -> dict[str, int]:
    """The registrations that report Skipped by name where they cannot run, per form: `footage`,
    `videotoolbox` (CI runs 2 of those 6 and skips 4), `soak`."""
    with open(_CMAKE, encoding="utf-8") as f:
        text = f.read()
    got = {kind: len(re.findall(rf"^add_{kind}_test\(", text, re.M))
           for kind in ("footage", "videotoolbox", "soak")}
    assert got["footage"] and got["videotoolbox"], (
        f"tests/CMakeLists.txt registers {got}: a form found nothing, so this check has gone "
        "vacuous")
    return got


def _core_lines() -> int:
    """`wc -l` over the C++ core's own sources — the number the pages mean by "an N-line core"."""
    total = 0
    for root, _dirs, names in os.walk(_CORE):
        for name in sorted(names):
            if name.endswith((".cpp", ".hpp")):
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    total += sum(1 for _ in f)
    assert total > 0, "no C++ sources under pacer/ — this check has gone vacuous"
    return total


# A FLOOR, NOT A COUNT. The pages stated the exact number, so every pull request that added a test
# edited both of them, and two such PRs from one base each wrote the same, wrong, number: once
# tests registered themselves (#374), those two sentences were the only shared files a new test
# still touched. They now say "N+", and N is held from both sides: never above what CTest
# registers, and never so far below it that the floor stops describing the suite. Adding a test
# edits no page until the suite outgrows its floor by more than this slack.
_SUITE_FLOOR_SLACK = 25
_SUITE_CLAIM = re.compile(r"([\d,]+)(\+?)\s+CTest registrations", re.I)


def _suite_claim_problems(rel: str, text: str, registered: int) -> tuple[int, list[str]]:
    """(how many claims `text` makes, what is wrong with them): a function of the text, so the
    planted floors below exercise exactly the rule the real pages are held to."""
    found, problems = _SUITE_CLAIM.findall(text), []
    for got, plus in found:
        floor = int(got.replace(",", ""))
        if not plus:
            problems.append(f"{rel} states an exact count, '{got} CTest registrations': write a "
                            f"floor ('{got}+'), or every new test has to edit this page")
        elif floor > registered:
            problems.append(f"{rel} says {got}+ CTest registrations; tests/CMakeLists.txt "
                            f"registers {registered}")
        elif registered - floor > _SUITE_FLOOR_SLACK:
            problems.append(f"{rel} says {got}+ CTest registrations, {registered - floor} below "
                            f"the {registered} registered (at most {_SUITE_FLOOR_SLACK} is allowed): "
                            f"raise the floor to a round number at or below {registered}")
    return len(found), problems


def test_public_pages_quote_the_real_suite_size():
    """Every "N+ CTest registrations" in README.md and the landing page is a true floor on what
    CTest registers, and not a stale one.

    THE BUG: both said **96** while `ctest -N` reported **114**. The sentence was true when it was
    written and nothing in the repo connected it to the thing it counted, so eighteen suites were
    added under it without a word changing. The number is derived, and the claim is a floor (see
    _SUITE_FLOOR_SLACK for why)."""
    want = _ctest_registrations()
    checked, problems = 0, []
    for rel in ("README.md", os.path.join("docs", "index.html")):
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            n, found = _suite_claim_problems(rel, f.read(), want)
        assert n, (
            f"{rel} no longer states a CTest registration count — if the claim was deliberately "
            "removed, remove it from this check's file list too, so the guard cannot go vacuous")
        checked, problems = checked + n, problems + found
    assert not problems, "\n".join(problems)
    assert checked >= 2, f"only {checked} count claims found across both pages"
    # Both directions, on planted claims: a stale floor, a floor above the suite, and the exact
    # count this rule replaced each fail, and a floor one test below the suite passes.
    stale = want - _SUITE_FLOOR_SLACK - 1
    for text, why in ((f"{stale}+ CTest registrations", "below the"),
                      (f"{want + 1}+ CTest registrations", "tests/CMakeLists.txt registers"),
                      (f"{want} CTest registrations", "states an exact count")):
        n, found = _suite_claim_problems("planted", text, want)
        assert n == 1 and len(found) == 1 and why in found[0], (text, found)
    assert _suite_claim_problems("planted", f"{want - 1}+ CTest registrations", want)[1] == []
    print(f"test_public_pages_quote_the_real_suite_size OK ({want} registrations, "
          f"{checked} floor claims within {_SUITE_FLOOR_SLACK})")


def test_public_pages_quote_the_real_core_size():
    """Every "N-line C++23 core" is within 5 % of `wc -l` over pacer/**.{cpp,hpp}.

    THE BUG: both pages said **1,873** against a measured **2,199** — 17 % low, and falling further
    every time the core grows. A TOLERANCE rather than equality is deliberate: this number moves on
    ordinary core commits, and a guard that fails every one of them buys truth at the price of an
    unrelated edit in every C++ PR. It fails when the claim is MISLEADING, not when it is stale in
    the last digit."""
    want = _core_lines()
    pat = re.compile(r"([\d,]+)-line C\+\+23 core", re.I)
    checked = 0
    for rel in ("README.md", os.path.join("docs", "index.html")):
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            text = f.read()
        found = pat.findall(text)
        assert found, (
            f"{rel} no longer states a C++ core size — if the claim was deliberately removed, "
            "remove it from this check's file list too, so the guard cannot go vacuous")
        for got in found:
            n = int(got.replace(",", ""))
            assert abs(n - want) <= _CORE_TOLERANCE * want, (
                f"{rel} says a {got}-line C++23 core; pacer/**.{{cpp,hpp}} is {want} lines "
                f"({abs(n - want) / want:.0%} out, tolerance {_CORE_TOLERANCE:.0%})")
            checked += 1
    assert checked >= 2, f"only {checked} core-size claims found across both pages"
    print(f"test_public_pages_quote_the_real_core_size OK ({want} lines, {checked} claims)")


# ------------------------------------------------------------------ 7b. what CI skips, and the times
# QA ROUND 3 (EVAL-6, 2026-09-26) found four published numbers stale, and nothing held any of them:
#   * "the fifteen `footage.*` checks" CI skips, while tests/CMakeLists.txt registered sixteen, and
#     no page mentioned the six `videotoolbox.*` export checks, 4 of which CI also reports Skipped;
#   * CI "in about six minutes", while its job ran 7.5-9.7 min on `main`;
#   * `pixi run golden` "takes about a second", while AGENTS.md said ~8 s and the lane measured 32 s;
#   * "14 features measured and refused", held in tests/test_measured_figures.py.
# The two counts are derived here, like the suite floor above: a claim is a floor ("16+", within a
# slack) or a count that must be exact. A time cannot be derived from the tree, so it has to carry
# the date it was measured on, and where two documents quote one time they must agree.
_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
          "fifteen sixteen seventeen eighteen nineteen twenty").split()
_NUM = r"(\d+|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_TICK = r"(?:`|<code>|</code>)?"
_SKIP_CLAIMS = {
    "footage": (rf"\b{_NUM}(\+?)\s+{_TICK}footage\.\*{_TICK}\s+checks",
                rf"\b{_NUM}(\+?)\s+real-footage checks",
                rf"\b{_NUM}(\+?)\s+checks re-measure something on a real recording"),
    "videotoolbox": (rf"\b{_NUM}(\+?)\s+{_TICK}videotoolbox\.\*{_TICK}\s+(?:export\s+)?checks",),
}
_SKIP_SLACK = {"footage": 4, "videotoolbox": 2}
_SKIP_PAGES = ("README.md", os.path.join("docs", "index.html"), "AGENTS.md",
               os.path.join("tests", "README.md"), os.path.join("studio", "README.md"),
               *(os.path.join("docs", n) for n in sorted(os.listdir(_DOCS)) if n.endswith(".md")))


def _count_value(got: str) -> int:
    return int(got) if got.isdigit() else _WORDS.index(got.lower())


def _skip_claim_problems(rel: str, text: str, kind: str, registered: int) -> tuple[int, list[str]]:
    """(claims of `kind` in `text`, what is wrong with them). A floor ("16+") must be at most what
    is registered and within `_SKIP_SLACK`; a bare count, in digits or words, must be exact."""
    flat = " ".join(text.split())
    found, problems = 0, []
    for pattern in _SKIP_CLAIMS[kind]:
        for got, plus in re.findall(pattern, flat, re.I):
            found += 1
            n = _count_value(got)
            if plus and n > registered:
                problems.append(f"{rel} says {got}+ {kind}.* checks; tests/CMakeLists.txt "
                                f"registers {registered}")
            elif plus and registered - n > _SKIP_SLACK[kind]:
                problems.append(f"{rel} says {got}+ {kind}.* checks, {registered - n} below the "
                                f"{registered} registered: raise the floor")
            elif not plus and n != registered:
                problems.append(f"{rel} says {got} {kind}.* checks; tests/CMakeLists.txt registers "
                                f"{registered}: write that, or a floor ('{registered}+')")
    return found, problems


def test_the_pages_count_what_ci_skips():
    """Every count of the `footage.*` and `videotoolbox.*` checks, on the public pages and in the
    developer docs, is true of tests/CMakeLists.txt; both public pages state both."""
    registered = _skippable_registrations()
    problems, per_page = [], {}
    for rel in _SKIP_PAGES:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            text = f.read()
        if rel.endswith(".html"):
            text = _without_comments(text)
        for kind in _SKIP_CLAIMS:
            n, found = _skip_claim_problems(rel, text, kind, registered[kind])
            per_page[(rel, kind)] = n
            problems += found
    assert not problems, "\n".join(problems)
    for rel in ("README.md", os.path.join("docs", "index.html")):
        for kind in _SKIP_CLAIMS:
            assert per_page[(rel, kind)], (
                f"{rel} no longer says how many {kind}.* checks CI skips — if that was "
                "deliberate, "
                "take it out of this check too, so the check cannot go vacuous")
    # Both directions, on planted text: each stale shape EVAL-6 found fails, the true ones pass.
    fp, vt = registered["footage"], registered["videotoolbox"]
    for kind, text, want in (
            ("footage", "the fifteen `footage.*` checks", 1 if fp != 15 else 0),
            ("footage", f"the {fp + 1}+ `footage.*` checks", 1),
            ("footage", f"the {fp - _SKIP_SLACK['footage'] - 1}+ <code>footage.*</code> checks", 1),
            ("footage", f"with the {fp} real-footage checks", 0),
            ("footage", f"{_WORDS[fp].capitalize()} checks re-measure something on a real "
                        "recording", 0),
            ("videotoolbox", f"the {vt + 1} `videotoolbox.*` export checks", 1),
            ("videotoolbox", f"the {vt}+ <code>videotoolbox.*</code> export checks", 0)):
        n, found = _skip_claim_problems("planted", text, kind, registered[kind])
        assert n == 1 and len(found) == want, (text, found)
    print(f"test_the_pages_count_what_ci_skips OK ({registered}, "
          f"{sum(per_page.values())} claims across {len(_SKIP_PAGES)} files)")


_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MEASURED = re.compile(rf"measured \d{{1,2}} (?:{_MONTHS}) 20\d\d")


def _sentence(flat: str, anchor: str, rel: str) -> str:
    """The sentence of `flat` holding `anchor`, up to its closing parenthesis and full stop."""
    at = flat.find(anchor)
    assert at >= 0, f"{rel} no longer says {anchor!r} — if that was deliberate, update this check"
    end = flat.find(").", at)
    return flat[at:end + 2 if end >= 0 else None]


def test_the_pages_date_their_timings_and_agree_on_them():
    """A duration on the README carries the date it was measured, and agrees with its sources.

    The golden gate's range must hold AGENTS.md's figure for the same task. "N tests at a time" is
    the CTEST_PARALLEL_LEVEL of the `test` task that CI runs. The install size is dated, and the
    landing page quotes the same number of GB. (CI's own minutes were a fourth half: the README's
    word diet, FRONT-DOOR-11, deleted that per-run sentence, and only its half went with it.)"""
    with open(os.path.join(_REPO, "README.md"), encoding="utf-8") as f:
        readme = " ".join(f.read().split())
    at = readme.find("CI runs all of it on every pull request")
    assert at >= 0, "README.md no longer says how CI runs the suite — if deliberate, update this check"
    ci = readme[at:readme.find(". ", at) + 1]

    golden = _sentence(readme, "`pixi run golden`, the gate you actually run", "README.md")
    g = re.search(r"\((\d+)–(\d+) s on the development Mac, (measured [^)]*)\)", golden)
    assert g and _MEASURED.search(g.group(3)), f"README.md's golden time is undated: {golden!r}"
    with open(os.path.join(_REPO, "AGENTS.md"), encoding="utf-8") as f:
        row = re.search(r"^\| `pixi run golden` \|.*?~(\d+) s \|$", f.read(), re.M)
    assert row, "AGENTS.md's task table no longer times `pixi run golden`"
    assert int(g.group(1)) <= int(row.group(1)) <= int(g.group(2)), (
        f"README.md says golden takes {g.group(1)}–{g.group(2)} s, AGENTS.md ~{row.group(1)} s")

    with open(os.path.join(_REPO, "pyproject.toml"), encoding="utf-8") as f:
        level = re.search(r'^test = \{.*CTEST_PARALLEL_LEVEL = "(\d+)"', f.read(), re.M)
    at_a_time = re.search(r"\b(\w+) tests at a time", ci)
    assert level and at_a_time and _count_value(at_a_time.group(1)) == int(level.group(1)), (
        f"README.md says {at_a_time and at_a_time.group(0)!r}; the test task runs "
        f"CTEST_PARALLEL_LEVEL={level and level.group(1)}")

    size = _sentence(readme, "The environment `pixi install` puts on disk", "README.md")
    gb = re.search(r"about (\d+) GB \(", size)
    assert gb and _MEASURED.search(size), f"README.md's install size is undated: {size!r}"
    page = " ".join(_page().split())
    assert f"about {gb.group(1)} GB on disk" in page, (
        f"docs/index.html does not quote README.md's install size (about {gb.group(1)} GB)")
    print(f"test_the_pages_date_their_timings_and_agree_on_them OK (golden "
          f"{g.group(1)}–{g.group(2)} s vs AGENTS.md ~{row.group(1)} s, -j{level.group(1)}, "
          f"{gb.group(1)} GB)")


def test_the_pages_quote_the_demo_s_real_size():
    """"About 11 MB" is `studio/demo.py`'s pinned asset, rounded: that docstring said ~8 MB of an
    11,061,721-byte download (QA EVAL-9)."""
    tree = ast.parse(open(os.path.join(_REPO, "studio", "demo.py"), encoding="utf-8").read())
    size = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_DEMO_BYTES" for t in n.targets))
    want, found = round(size / 1e6), []
    for rel in (os.path.join("docs", "FIRST_LAP.md"), "README.md",
                os.path.join("docs", "index.html"), os.path.join("studio", "README.md"),
                os.path.join("studio", "demo.py")):
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            flat = " ".join(re.sub(r"\n>\s?", " ", f.read()).split())
        found += [(rel, int(n)) for n in re.findall(r"about (\d+) MB", flat)]
    assert len(found) >= 3, f"only {found} demo-size claims found — the check has gone vacuous"
    wrong = [f"{rel} says about {n} MB" for rel, n in found if n != want]
    assert not wrong, (f"the demo is {size:,} B (studio/demo.py _DEMO_BYTES), about {want} MB: "
                       f"{wrong}")
    print(f"test_the_pages_quote_the_demo_s_real_size OK ({len(found)} claims, {want} MB)")


# THE WAY IN SITS ON THE FIRST SCREEN, AND IT IS THE DEMO (SHOWCASE-4). README's first command was
# 256 lines down, under eight sections of case study, and the landing's build section asked for "a
# GoPro file" and ended on YOUR_RECORDING.MP4, with `--demo` in a callout below it: a visitor with
# no footage, which is most of them, read both as "not for me". So README's first code block sits
# above its first section and is the three commands ending on the demo, the landing's build block
# gives the same three before the own-recording line and its headline names the demo, and every
# in-page link of README's lands on a heading (the Try-it line links #run-it-from-source).
_TRY_IT = ("git clone --recursive https://github.com/eenndan/pacer", "pixi install",
           "pixi run studio -- --demo")


def _github_anchor(heading: str) -> str:
    """The id GitHub gives a markdown heading: lower case, every character but a word character, a
    '-' or a space dropped, each space a '-' ("Accuracy — the claim" → "accuracy--the-claim")."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def _commands(block: str) -> list[str]:
    """The shell commands of a code block: comment lines and trailing `  # …` comments dropped."""
    return [c for c in (re.sub(r"\s+#.*", "", ln).strip() for ln in block.splitlines())
            if c and not c.startswith("#")]


def _try_it_problems(readme: str, page: str) -> list[str]:
    """What keeps a first-time visitor from the demo: a function of the two texts, so the planted
    pages below exercise exactly the rule the real ones are held to."""
    problems = []
    prose = re.sub(r"^```.*?^```", "", readme, flags=re.S | re.M)
    fence = re.search(r"^```\w*\n(.*?)^```", readme, re.S | re.M)
    first_section = re.search(r"^## ", readme, re.M)
    readme_cmds = _commands(fence.group(1)) if fence else []
    if not fence or not first_section or fence.start() > first_section.start():
        problems.append("README.md's first code block is not above its first section")
    elif len(readme_cmds) != len(_TRY_IT) or not all(
            c.startswith(w) for c, w in zip(readme_cmds, _TRY_IT, strict=True)):
        problems.append(f"README.md's first code block is {readme_cmds}, not the three commands "
                        f"ending on the demo")
    elif "](#run-it-from-source)" not in readme[fence.end():first_section.start()]:
        problems.append("README.md's Try-it block does not link #run-it-from-source")
    headings = {_github_anchor(h) for h in re.findall(r"^#{1,6} (.+)$", prose, re.M)}
    problems += [f"README.md links #{a}, which no heading of it has"
                 for a in sorted(set(re.findall(r"\]\(#([^)\s]+)\)", prose)) - headings)]

    build = re.search(r'<section id="build">(.*?)</section>', page, re.S)
    if not build:
        return problems + ["docs/index.html has no build section"]
    h2 = re.search(r"<h2>(.*?)</h2>", build.group(1), re.S)
    if not h2 or "demo" not in h2.group(1).lower():
        problems.append(f"docs/index.html's build headline does not name the demo: "
                        f"{h2 and h2.group(1)!r}")
    pre = re.search(r"<pre\b[^>]*><code>(.*?)</code></pre>", build.group(1), re.S)
    page_cmds = _commands(_unescape(re.sub(r"<[^>]+>", "", pre.group(1)))) if pre else []
    if page_cmds[:len(_TRY_IT)] != readme_cmds[:len(_TRY_IT)] or len(readme_cmds) < len(_TRY_IT):
        problems.append(f"docs/index.html's build block opens {page_cmds[:len(_TRY_IT)]}, "
                        f"README.md's Try-it {readme_cmds}")
    return problems


def test_the_first_screen_offers_the_demo_in_three_commands():
    """README's first code block is the Try-it block above its first section, the landing's build
    block opens with the same three commands and its headline names the demo, and every in-page
    link of README's resolves. The same helper fails on each planted page: the block moved back
    down, one ending on a recording path, a renamed section, and the landing's old build block."""
    readme = open(os.path.join(_REPO, "README.md"), encoding="utf-8").read()
    page = _page()
    problems = _try_it_problems(readme, page)
    assert not problems, "the demo is not on the first screen:\n  " + "\n  ".join(problems)

    try_it = re.search(r"^```\w*\n.*?^```\n", readme, re.S | re.M).group(0)
    plants = (  # (what, readme, page, a fragment the problems must name)
        ("the block back at the bottom", readme.replace(try_it, "", 1), page,
         "not above its first section"),
        ("the block ending on a recording", readme.replace(
            "pixi run studio -- --demo", "pixi run studio -- /path/to/GX010062.MP4", 1), page,
         "not the three commands"),
        ("the section it links renamed", readme.replace("## Run it from source", "## Build it"),
         page, "links #run-it-from-source, which no heading"),
        ("the landing asking for a GoPro file", readme, page.replace(
            "An Apple Silicon Mac and three commands. The demo needs no GoPro.",
            "A Mac with Apple Silicon, a GoPro file, and three commands."), "does not name the demo"),
        ("the landing ending on YOUR_RECORDING", readme, page.replace(
            "pixi run studio -- --demo\n", "", 1), "build block opens"))
    for what, r, p, named in plants:
        assert (r, p) != (readme, page), f"{what!r}: a no-op"
        caught = _try_it_problems(r, p)
        assert any(named in c for c in caught), f"{what!r} not caught as {named!r}: {caught}"
    print(f"test_the_first_screen_offers_the_demo_in_three_commands OK (README line "
          f"{readme[:readme.index(try_it)].count(chr(10)) + 1}; {len(plants)} plants caught)")


# Words v0.5.0 retired from the UI (#425 named the ideal lap one thing everywhere, and the chart
# legend followed), and that the public pages kept using in prose and alt text until QA round 3
# (EVAL-3). Alt text counts: it is what a reader who cannot see the image is told the image says.
# CHANGELOG.md is history and exempt; code identifiers (`theoretical_best`) do not match.
_RETIRED = {
    "theoretical best": "the ideal lap (#425: one name on every surface)",
    "theoretical ideal": "the ideal lap (#425)",
    "as fast as this lap": "'Laps this fast', the IDEAL LAP table's column (#425)",
    "Δ to ideal (synthetic)": "'Δ to ideal lap', the chart legend's words",
}


def test_no_public_page_uses_a_retired_ui_word():
    pages = ["README.md", os.path.join("studio", "README.md"),
             *(os.path.join("docs", n) for n in sorted(os.listdir(_DOCS))
               if n.endswith((".md", ".html")))]
    hits = []
    for rel in pages:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            text = f.read()
        flat = " ".join((_without_comments(text) if rel.endswith(".html") else text).split()).lower()
        hits += [f"{rel}: {word!r} — the app says {now}" for word, now in _RETIRED.items()
                 if word.lower() in flat]
    assert not hits, "retired UI words on the public pages:\n  " + "\n  ".join(hits)
    assert "theoretical best" in " ".join(open(os.path.join(_REPO, "CHANGELOG.md"),
                                               encoding="utf-8").read().split()).lower(), (
        "CHANGELOG.md no longer holds the retired name either — this check has lost its control")
    print(f"test_no_public_page_uses_a_retired_ui_word OK ({len(pages)} pages, "
          f"{len(_RETIRED)} words)")


# ------------------------------------------------------------------ 7. the clip
# The page's one video (board review 2026-09-23, bet B4): the app's own overlay export of a best
# lap, cut by studio/dev/media_capture.py's `clip` shot. What can go wrong with it is what went
# wrong with the images, plus three things a video adds: its weight, its SOUND (the export carries
# the camera's audio; a `muted` attribute is only a request, so the file must have no audio track
# at all), and MOTION for a reader who asked the system for less of it. All of it is read off the
# files themselves — the MP4's own boxes, the JPEG's own frame header.
#
# THE WEIGHT IS THE FIRST VIEW'S. The clip autoplays in the hero, so a visitor pays for it before
# they scroll, with the page, every image not marked `loading="lazy"` and the poster: 9.1 MB when
# the clip's own budget was 8 MB (SHOWCASE-9). The first view is held to 5 MB, and the clip's
# budget is what that leaves the page room to grow in.
_CLIP_BUDGET = 3_600_000
_FIRST_VIEW_BUDGET = 5_000_000
_CLIP_SECONDS = (20.0, 30.0)
_REDUCED_MOTION = "(prefers-reduced-motion: no-preference)"
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(path: str) -> tuple[int, int]:
    """(width, height) from a JPEG's start-of-frame segment, walking the markers before it."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:2] == b"\xff\xd8", f"{path} is not a JPEG"
    i = 2
    while i + 4 <= len(data):
        assert data[i] == 0xFF, f"{path}: lost the marker chain at byte {i}"
        marker = data[i + 1]
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:        # markers with no length
            i += 2
            continue
        if marker in _SOF:
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    raise AssertionError(f"{path}: no start-of-frame marker")


def _boxes(data: bytes, start: int, end: int):
    """(type, payload start, end) for each ISO-BMFF box between `start` and `end`."""
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        head = 8
        if size == 1:
            size, head = struct.unpack(">Q", data[i + 8:i + 16])[0], 16
        elif size == 0:
            size = end - i
        assert size >= head, f"a corrupt {kind!r} box at byte {i}"
        yield kind.decode("latin-1"), i + head, i + size
        i += size


def _mp4(path: str) -> dict:
    """What the page needs to know about an MP4, from its boxes: the top-level box order, the
    movie's duration (moov/mvhd) and each track's handler and size (trak/mdia/hdlr, trak/tkhd)."""
    with open(path, "rb") as f:
        data = f.read()
    top = list(_boxes(data, 0, len(data)))
    out = {"order": [k for k, _s, _e in top], "duration": None, "tracks": []}
    for kind, s, e in top:
        if kind != "moov":
            continue
        for k, s1, e1 in _boxes(data, s, e):
            if k == "mvhd":
                scale, dur = (struct.unpack(">IQ", data[s1 + 20:s1 + 32]) if data[s1] == 1
                              else struct.unpack(">II", data[s1 + 12:s1 + 20]))
                out["duration"] = dur / scale
            elif k == "trak":
                track = {"handler": None, "size": None}
                for k2, s2, e2 in _boxes(data, s1, e1):
                    if k2 == "tkhd":
                        at = s2 + (88 if data[s2] == 1 else 76)     # 16.16 fixed-point w, h
                        w, h = struct.unpack(">II", data[at:at + 8])
                        track["size"] = (w >> 16, h >> 16)
                    elif k2 == "mdia":
                        for k3, s3, _e3 in _boxes(data, s2, e2):
                            if k3 == "hdlr":
                                track["handler"] = data[s3 + 8:s3 + 12].decode("latin-1")
                out["tracks"].append(track)
    return out


def _clip_problems(html: str, docs: str) -> list[str]:
    """Everything wrong with the page's clip: its markup, its poster, and the MP4 itself."""
    videos = re.findall(r"<video\b[^>]*>.*?</video>", _without_comments(html), re.S)
    if len(videos) != 1:
        return [f"expected exactly one <video> on the page, found {len(videos)}"]
    tag = re.match(r"<video\b[^>]*>", videos[0]).group(0)
    problems = [f"<video> lacks `{a}`" for a in ("autoplay", "muted", "loop", "playsinline")
                if not re.search(rf"\s{a}[\s>]", tag)]
    if not re.search(r'aria-label="[^"]{40,}"', tag):
        problems.append("<video> has no substantive aria-label — it is not decoration")
    w, h = re.search(r'width="(\d+)"', tag), re.search(r'height="(\d+)"', tag)
    if not (w and h):
        return problems + ["<video> declares no width/height, so the page reflows when it loads"]
    declared = (int(w.group(1)), int(h.group(1)))
    poster = re.search(r'poster="([^"]+)"', tag)
    if not poster or not os.path.exists(os.path.join(docs, poster.group(1))):
        problems.append(f"missing poster: {poster and poster.group(1)!r}")
    elif _jpeg_size(os.path.join(docs, poster.group(1))) != declared:
        problems.append(f"the poster is {_jpeg_size(os.path.join(docs, poster.group(1)))}, "
                        f"the page declares {declared}")
    sources = re.findall(r"<source\b[^>]*>", videos[0])
    if len(sources) != 1:
        return problems + [f"expected one <source>, found {len(sources)}"]
    if f'media="{_REDUCED_MOTION}"' not in sources[0]:
        problems.append(f'the <source> lacks media="{_REDUCED_MOTION}": a reader who asked for '
                        "reduced motion would get the loop instead of the poster")
    src = re.search(r'src="([^"]+)"', sources[0])
    path = src and os.path.join(docs, src.group(1))
    if not path or not os.path.exists(path):
        return problems + [f"missing clip: {src and src.group(1)!r}"]
    size = os.path.getsize(path)
    if size > _CLIP_BUDGET:
        problems.append(f"the clip is {size:,} B, over the {_CLIP_BUDGET:,} B budget")
    mp4 = _mp4(path)
    if "moov" not in mp4["order"] or "mdat" not in mp4["order"] or (
            mp4["order"].index("moov") > mp4["order"].index("mdat")):
        problems.append(f"the MP4's index is not up front (boxes {mp4['order']}): a browser has to "
                        "fetch the whole file before it can play a frame")
    handlers = [t["handler"] for t in mp4["tracks"]]
    if "soun" in handlers:
        problems.append("the clip has an AUDIO track — `muted` is a request to the browser; the "
                        "camera's sound must not be in the file at all")
    if handlers.count("vide") != 1:
        problems.append(f"expected one video track, found tracks {handlers}")
    else:
        frame = next(t["size"] for t in mp4["tracks"] if t["handler"] == "vide")
        if frame != declared:
            problems.append(f"the clip is {frame[0]}x{frame[1]}, the page declares {declared}")
        if frame[1] != 720:
            problems.append(f"the clip is {frame[1]}p, not the 720p it is cut to")
    lo, hi = _CLIP_SECONDS
    if not (mp4["duration"] and lo <= mp4["duration"] <= hi):
        problems.append(f"the clip runs {mp4['duration']} s, outside {lo:.0f}–{hi:.0f} s")
    return problems


def _first_view(html: str, docs: str) -> dict[str, int]:
    """Bytes a visitor fetches before scrolling, by file: the page itself, every `<img>` the page
    does not mark `loading="lazy"`, and the clip's poster and source (it autoplays)."""
    live = _without_comments(html)
    parts = {"index.html": len(html.encode("utf-8"))}
    eager = [t for t in re.findall(r"<img\b[^>]*>", live) if 'loading="lazy"' not in t]
    video = re.findall(r"<video\b.*?</video>", live, re.S)
    for src in ([re.search(r'src="([^"]+)"', t).group(1) for t in eager]
                + [m for v in video for m in re.findall(r'(?:poster|src)="(media/[^"]+)"', v)]):
        parts[src] = os.path.getsize(os.path.join(docs, src))
    return parts


def test_the_clip_is_silent_small_and_still_on_request():
    """The page's clip: one `<video>`, autoplaying muted in a loop, with a poster the page reserves
    the right box for, a reduced-motion `media` query on its only source, and an MP4 that is under
    budget, 720p, 20-30 s, indexed up front — and has NO audio track. The README links it too, and
    the page's first view — the page, its eager images, the poster and the clip — stays under 5 MB."""
    problems = _clip_problems(_page(), _DOCS)
    assert not problems, "docs/index.html's clip:\n  " + "\n  ".join(problems)
    tree = ast.parse(open(os.path.join(_REPO, "studio", "dev", "media_capture.py"),
                          encoding="utf-8").read())
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)}
    assert consts.get("CLIP_MAX_BYTES") == _CLIP_BUDGET, (
        f"media_capture.CLIP_MAX_BYTES is {consts.get('CLIP_MAX_BYTES')}, the page's budget "
        f"{_CLIP_BUDGET}: the tool that cuts the clip and the check on it must agree")
    with open(os.path.join(_REPO, "README.md"), encoding="utf-8") as f:
        assert "(docs/media/best-lap.mp4)" in f.read(), "README.md no longer links the clip"
    page = _page()
    # Beside the headline (SHOWCASE-3): below hero.png the clip started at y 1,233 on a 1440 x 900
    # screen, under the fold, and stacked above it in one column it still showed only part of
    # itself. So it sits in the hero's .split row, ahead of hero.png. Where that lands is measured
    # in a browser, not here; this pins the order the layout needs.
    live = _without_comments(page)
    hero = live.index('<section class="hero">')
    split, clip = live.find('<div class="split">', hero), live.find('<figure class="figure clip">')
    shot, hero_end = live.find('src="media/hero.png"'), live.index("</section>", hero)
    in_split = hero < split < clip and (live[split:clip].count("<div")
                                        - live[split:clip].count("</div>")) >= 1
    assert in_split and clip < min(shot, hero_end), (
        f"the clip figure (at {clip}) must sit in the hero's .split row (hero {hero}–{hero_end}, "
        f"first .split after it at {split}) and ahead of media/hero.png (at {shot})")
    first = _first_view(page, _DOCS)
    assert {"media/best-lap.mp4", "media/best-lap.jpg", "media/hero.png"} <= set(first), (
        f"the first view counts {sorted(first)}: the clip, its poster or the hero went uncounted")
    assert sum(first.values()) <= _FIRST_VIEW_BUDGET, (
        f"the landing page's first view is {sum(first.values()):,} B, over its "
        f"{_FIRST_VIEW_BUDGET:,} B budget: " + ", ".join(f"{k} {v:,}" for k, v in first.items()))
    # The control: an image below the fold loses its `loading="lazy"` and is counted.
    lazy = next(t for t in re.findall(r"<img\b[^>]*>", _without_comments(page))
                if 'loading="lazy"' in t)
    src = re.search(r'src="([^"]+)"', lazy).group(1)
    planted = _first_view(page.replace(lazy, lazy.replace(' loading="lazy"', "")), _DOCS)
    assert src not in first and src in planted, (src, sorted(first), sorted(planted))
    print(f"test_the_clip_is_silent_small_and_still_on_request OK "
          f"({os.path.getsize(os.path.join(_DOCS, 'media', 'best-lap.mp4')):,} B; first view "
          f"{sum(first.values()):,} B of {_FIRST_VIEW_BUDGET:,})")


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _planted_mp4(path: str, seconds: float, tracks, moov_first: bool = True) -> None:
    """A structurally real MP4 that carries only what `_mp4` reads: mvhd, and per track a tkhd +
    mdia/hdlr. Enough to plant each defect the check claims to catch without a video encoder."""
    mvhd = _box(b"mvhd", b"\0\0\0\0" + struct.pack(">IIII", 0, 0, 1000, int(seconds * 1000))
                + b"\0" * 80)
    traks = b""
    for handler, (w, h) in tracks:
        tkhd = _box(b"tkhd", b"\0\0\0\x03" + b"\0" * 72 + struct.pack(">II", w << 16, h << 16))
        hdlr = _box(b"hdlr", b"\0" * 8 + handler.encode() + b"\0" * 13)
        traks += _box(b"trak", tkhd + _box(b"mdia", hdlr))
    moov, mdat = _box(b"moov", mvhd + traks), _box(b"mdat", b"\0" * 64)
    body = moov + mdat if moov_first else mdat + moov
    with open(path, "wb") as f:
        f.write(_box(b"ftyp", b"isom\0\0\x02\0isom") + body)


def _planted_jpeg(path: str, w: int, h: int) -> None:
    """SOI, one APP0 to walk past, then a baseline SOF0 carrying (w, h)."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, 3) + b"\x01\x22\0\x02\x11\x01\x03\x11\x01"
    with open(path, "wb") as f:
        f.write(b"\xff\xd8" + app0 + sof0 + b"\xff\xd9")


def test_the_clip_check_fails_on_each_planted_defect():
    """The control on the check above: a clean planted page passes, and each defect it names —
    an audio track, the index at the end, the wrong length or frame, the missing reduced-motion
    query, a poster of the wrong size, a clip over budget — is caught, by name."""
    import tempfile

    page = _page()
    video = re.search(r"<video\b.*?</video>", _without_comments(page), re.S).group(0)
    good = {"tracks": [("vide", (1280, 720))], "seconds": 20.0, "moov_first": True}
    plants = {
        "AUDIO track": (dict(good, tracks=[("vide", (1280, 720)), ("soun", (0, 0))]), video, 0),
        "index is not up front": (dict(good, moov_first=False), video, 0),
        "outside 20–30 s": (dict(good, seconds=45.0), video, 0),
        "the page declares": (dict(good, tracks=[("vide", (1920, 1080))]), video, 0),
        "reduced-motion": (good, video.replace(f' media="{_REDUCED_MOTION}"', ""), 0),
        "the poster is": (good, video, 1),
        "over the": (good, video, 2),
    }
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "media"))
        clip, poster = (os.path.join(tmp, "media", n) for n in ("best-lap.mp4", "best-lap.jpg"))
        for want, (mp4, markup, other) in [("", (good, video, 0))] + list(plants.items()):
            _planted_mp4(clip, mp4["seconds"], mp4["tracks"], mp4["moov_first"])
            if other == 2:
                with open(clip, "ab") as f:
                    f.write(b"\0" * (_CLIP_BUDGET + 1))
            _planted_jpeg(poster, *((640, 360) if other == 1 else (1280, 720)))
            got = _clip_problems(page.replace(video, markup) if markup != video else page, tmp)
            if not want:
                assert not got, f"the clean plant was flagged: {got}"
            else:
                assert any(want in p for p in got), f"planted {want!r}, got {got}"
    print(f"test_the_clip_check_fails_on_each_planted_defect OK ({len(plants)} defects caught)")


# ------------------------------------------------------------------ 8. the decisions record
# docs/DECISIONS.md is the tracked record of the owner's rulings (#447), and it is public. The W1
# close-out QA (2026-09-29, EVAL-1 and EVAL-3) found it written for the maintainers instead:
#   * RUL-11 said in the present tense that the tag job no longer builds an `.app`, while
#     .github/workflows/ci.yml still ran PyInstaller on every tag;
#   * it cited ids (R11, PRODUCT-8, O5, ADV-1, the fix plan's packages) that only the maintainers'
#     private notes define, and so did docs/AGENT-GUARDRAILS.md (R13);
#   * its owner-acts table stated what exists of the owner's recordings, which is not ours to
#     publish.
# So RUL-11 read "Pending" exactly while CI still built the `.app`, every id family either page
# cites is glossed at the top of DECISIONS.md, and no public page says what exists of his
# recordings.
#
# RUL-11's default (drop) has since been applied (LONGEVITY-2, 2026-09-30), so its check is
# inverted: the build must be ABSENT. ci.yml names neither PyInstaller nor a package-smoke job,
# RUL-11 no longer reads "Pending", and no page still claims a working `.app` in the phrases the
# old docs used. Why absent rather than merely recorded: the tag job built the bundle and never
# launched it, and that bundle crashed at launch on every build from June until #450 while every
# tag went green (QA r4 REG2-1). A build that comes back must launch what it builds, on the
# owner's ruling ("keep"), with RUL-11 reworded; invert this check back in that pull request.
#
# The W2 close-out QA (2026-09-30, EVAL-2 and EVAL-4) found #473's fix partial: it dropped "each
# recording exists once" but kept the row, and the row's "open" status said the same thing, twice
# (the originals, and the agents' notes). RUL-3 asked "are his sprints arrive-and-drive", and
# ENGINEERING.md §8 closed on "the first had no second copy". So the owner-acts table carries no
# status column (what is done is tracked privately), and no public page states a backup's state or
# a person's habits in any of the shapes below. The mechanisms stay public: a freeze that ends at
# the next new recording, a read that lists store names and dates.
_PRIVATE = re.compile(r"\b(?:no|without a|without any) (?:second copy|backups?)\b"
                      r"|\bexists? once\b|\bnot backed up\b|\bhis sprints\b")
_ACTS = "## 2. Owner acts"
_DECISIONS = os.path.join(_DOCS, "DECISIONS.md")
_GLOSS_LEAD = "**The ids this page cites.**"
# An id is a family and a number: an upper-case word or hyphenated words and a hyphen
# ("RUL-11", "FIRST-OPEN-LOOP-3"), or one capital ("R13", "O5", "S1"; the V of "item 15-V1").
_ID = re.compile(r"\b([A-Z]+(?:-[A-Z]+)*-|[A-Z])\d+\b")


# The build, as ci.yml would name it: the pass criterion LONGEVITY-2 was held to, so even a comment
# naming PyInstaller brings the question back to this check.
_CI_APP_BUILD = re.compile(r"\bpyinstaller\b|\bpackage-smoke\b", re.I)
# The works-claims the docs made about the `.app` and its CI job, before RUL-11 dropped the job.
_APP_CLAIMS = re.compile(r"build-verif|package-smoke|double-clickable|locally-runnable"
                         r"|runnable-locally|runs locally immediately")
# Where a claim would be read: the public pages, the agents' map, the recipe itself, and the
# changelog fragments still to be folded (CHANGELOG.md's released sections are history).
_CLAIM_PAGES = ("README.md", "AGENTS.md", "docs", "packaging", "changes")


def _rul11_problem(follows: str, ci_builds_app: bool) -> str | None:
    """What is wrong with RUL-11's "What follows" cell, or with ci.yml, now that the ruling's
    default (drop the `.app` build) is applied."""
    if ci_builds_app:
        return (".github/workflows/ci.yml builds an `.app` again (PyInstaller, or a package-smoke "
                "job), but RUL-11 dropped that build: it built a bundle nothing launched, which "
                "crashed at launch from June until #450 with every tag green. Bring one back only "
                "on the owner's 'keep', with a step that launches it, and reword RUL-11")
    if follows.startswith("Pending"):
        return ("RUL-11 still says the `.app` build is pending, but .github/workflows/ci.yml no "
                "longer runs one: say what happened, and in which pull request")
    return None


def _app_claims(repo: str) -> list[str]:
    """Every line under `_CLAIM_PAGES` that still claims a working `.app` or a CI job for one."""
    files = []
    for rel in _CLAIM_PAGES:
        path = os.path.join(repo, rel)
        if os.path.isfile(path):
            files.append(rel)
        else:
            files += [os.path.join(rel, n) for n in sorted(os.listdir(path))
                      if n.endswith((".md", ".html", ".sh", ".spec", ".py"))]
    hits = []
    for rel in files:
        with open(os.path.join(repo, rel), encoding="utf-8") as f:
            hits += [f"{rel}:{i}: {m.group(0)!r}" for i, line in enumerate(f, 1)
                     for m in [_APP_CLAIMS.search(line)] if m]
    return hits


def _id_families(text: str) -> set[str]:
    """The id families `text` cites. Inline code is not prose: `git diff -U0` cites no id."""
    return {m.group(1) for m in _ID.finditer(re.sub(r"`[^`\n]*`", "", text))}


def _unglossed(text: str, gloss: str) -> list[str]:
    """The id families and named ledgers `text` cites that `gloss` does not name."""
    missing = [f"{fam}n" for fam in sorted(_id_families(text))
               if not re.search(rf"\b{re.escape(fam)}(?:n\b|\d)", gloss)]
    return missing + [f"the {w} ledger" for w in sorted(set(re.findall(r"\bthe (\w+) ledger\b",
                                                                        text, re.I)))
                      if f"the {w.lower()} ledger" not in gloss.lower()]


def _private_statements(text: str) -> list[str]:
    """The backup-state and owner-habit phrases `text` states, line breaks and case folded."""
    return sorted({m.group(0) for m in _PRIVATE.finditer(" ".join(text.split()).lower())})


def _acts_status_columns(decisions: str) -> list[str]:
    """The header cells of DECISIONS.md's owner-acts table that carry a status."""
    at = decisions.find(_ACTS)
    assert at >= 0, f"docs/DECISIONS.md lost its {_ACTS!r} section — if deliberate, update this"
    end = decisions.find("\n## ", at + len(_ACTS))
    header = next((ln for ln in decisions[at:end].splitlines() if ln.startswith("|")), "")
    assert header, f"docs/DECISIONS.md's {_ACTS!r} section has no table — if deliberate, update this"
    return [c.strip() for c in header.strip("|").split("|") if "status" in c.lower()]


def test_decisions_says_what_is_true_and_public():
    with open(_DECISIONS, encoding="utf-8") as f:
        decisions = f.read()
    with open(os.path.join(_REPO, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
        ci_builds_app = bool(_CI_APP_BUILD.search(f.read()))
    row = re.search(r"^\| RUL-11 \|(.*)\|[ \t]*$", decisions, re.M)
    assert row, "docs/DECISIONS.md has no RUL-11 row — if that was deliberate, update this check"
    follows = row.group(1).split("|")[-1].strip()
    problem = _rul11_problem(follows, ci_builds_app)
    assert problem is None, problem
    claims = _app_claims(_REPO)
    assert not claims, ("RUL-11 dropped the `.app` build, but a page still claims one works or "
                        "that CI builds it:\n  " + "\n  ".join(claims))

    at = decisions.find(_GLOSS_LEAD)
    assert at >= 0, f"docs/DECISIONS.md lost its {_GLOSS_LEAD!r} paragraph"
    end = decisions.find("\n## ", at)
    gloss, rest = decisions[at:end], decisions[:at] + decisions[end:]
    with open(os.path.join(_DOCS, "AGENT-GUARDRAILS.md"), encoding="utf-8") as f:
        guardrails = f.read()
    missing = [f"docs/DECISIONS.md: {m}" for m in _unglossed(rest, gloss)]
    missing += [f"docs/AGENT-GUARDRAILS.md: {m}" for m in _unglossed(guardrails, gloss)]
    assert not missing, ("ids a public reader cannot resolve — gloss each family in "
                         f"docs/DECISIONS.md's {_GLOSS_LEAD!r} list:\n  " + "\n  ".join(missing))

    pages = ["README.md", *(os.path.join("docs", n) for n in sorted(os.listdir(_DOCS))
                            if n.endswith((".md", ".html")))]
    said = []
    for rel in pages:
        with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
            said += [f"{rel}: {p!r}" for p in _private_statements(f.read())]
    assert not said, ("a public page states a backup's state or the owner's habits — say the "
                      "mechanism, not the person:\n  " + "\n  ".join(said))
    status = _acts_status_columns(decisions)
    assert not status, (f"docs/DECISIONS.md's owner-acts table has a status column {status}: which "
                        "acts are done is tracked privately (an open backup act says there is none)")

    # Both directions, on planted text: each shape the W2 QA found fails, and its fixed wording
    # passes; a status column fails, and the act/note table passes.
    for text in ("The other two chapters survived; the first had no second copy.",
                 "Until then each recording exists once.",
                 "The agents' notes have no backup yet.", "His notes are not backed up.",
                 "Facts: are his sprints arrive-and-drive?"):
        assert _private_statements(text), f"the planted {text!r} was not caught"
    for text in ("If a private copy of the agents' notes and memory is configured, pull it.",
                 "The other two chapters survived.", "A second copy of the originals.",
                 "Until the next new recording or 2026-11-09, whichever comes first.",
                 "Premise: a sprint is driven in a different fleet kart each time."):
        assert not _private_statements(text), f"the neutral {text!r} was flagged"
    assert _acts_status_columns(f"{_ACTS}\n\n| Act | Status (evidence) | Note |\n|---|---|---|\n")
    assert not _acts_status_columns(f"{_ACTS}\n\n| Act | What waits on it |\n|---|---|\n")

    # Both directions, on planted text: each shape EVAL-1 and EVAL-3 found fails, the fixed ones
    # pass, and the live page cites ids at all (or the gloss check is vacuous).
    done = "The tag job stops building an `.app`, and nothing claims one works."
    pending = "Pending: LONGEVITY-2 will stop the tag job building an `.app`."
    assert _rul11_problem(done, True) and not _rul11_problem(done, False)
    assert _rul11_problem(pending, True) and _rul11_problem(pending, False)
    # The build as the dropped job ran it, and as a job header, is caught; a CI file that builds
    # only the core is not. So are the old docs' claims, and the ungated wording is not.
    for text in ('run: pixi run pyinstaller --noconfirm --clean packaging/pacer.spec',
                 "  package-smoke:\n    name: package .app build-only smoke (tag / manual)"):
        assert _CI_APP_BUILD.search(text), f"the planted build {text!r} was not caught"
    assert not _CI_APP_BUILD.search("run: pixi run build\nrun: pixi run golden\n")
    for text in ("CI build-verifies the bundle on every release tag",
                 "This builds a standalone, double-clickable Pacer Studio.app",
                 "Produces a locally-runnable macOS app", "It runs locally immediately;"):
        assert _APP_CLAIMS.search(text), f"the planted claim {text!r} was not caught"
    assert not _APP_CLAIMS.search("an ungated local recipe: no CI job builds or launches it")
    for text, want in (("COACHING-4 builds after the freeze (ADV-5).", ["ADV-n", "COACHING-n"]),
                       ("Closed on the FOLLOW ledger's count (R11).", ["Rn", "the FOLLOW ledger"]),
                       ("item 15-V1", ["Vn"]),
                       ("RUL-6 (O5)", ["On"])):
        got = _unglossed(text, "RUL-n is defined here")
        assert got == want, (text, got)
    assert _unglossed(rest, "") and _unglossed(guardrails, ""), "no ids found: the check is vacuous"
    print(f"test_decisions_says_what_is_true_and_public OK (RUL-11 done: no `.app` build, "
          f"no claim of one, "
          f"{len(_id_families(rest + guardrails))} id families glossed, "
          f"{len(pages)} pages)")


# ------------------------------------------------------------------------------------ headline
# SHOWCASE-2 / FRONT-DOOR-5. The first screen carried no figure, and the share cards quoted a bound
# (±0.003 s) and a lap total (121) that lean on D24's rows A and B, which cannot be re-run from
# this repository. The headline is now derived from media_capture.ACCURACY by the rule its comment
# states, the rows a real-footage check re-measures, and every first-screen surface must carry it.
_MEDIA_CAPTURE = os.path.join(_REPO, "studio", "dev", "media_capture.py")
_META = re.compile(r'<meta (?:name|property)="((?:og:|twitter:)?description)" content="([^"]*)">')


def _accuracy_rows() -> list[dict]:
    """media_capture.ACCURACY, read with ast: that module imports Qt."""
    tree = ast.parse(open(_MEDIA_CAPTURE, encoding="utf-8").read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "ACCURACY" for t in n.targets))


def _headline(rows: list[dict]) -> tuple[str, list[str]]:
    """(the headline, the figures no headline surface may carry), by the rule over ACCURACY: σ of
    the best and worst rerun row at 3 dp, their summed clean laps, the circuits they span. Barred:
    each other row's σ at 4 and 3 dp, the retired ±0.003 s bound, and the all-rows lap total."""
    rerun = [r for r in rows if r["rerun"]]
    assert rerun, "no ACCURACY row has a rerun check: nothing may headline"
    lo, hi = (f"{f(r['sigma'] for r in rerun):.3f}" for f in (min, max))
    n = len({r["circuit"] for r in rerun})
    head = (f"σ {lo if lo == hi else f'{lo}–{hi}'} s over {sum(r['clean'] for r in rerun)} laps "
            f"at {_WORDS[n]} circuit{'s' if n > 1 else ''}")
    barred = [f"{r['sigma']:.{dp}f}" for r in rows if not r["rerun"] for dp in (4, 3)]
    return head, barred + ["±0.003", str(sum(r["clean"] for r in rows))]


_OG_CARD = "og.png's words (media_capture.OG_BODY, OG_CHIPS; re-draw it with --only og)"


def _og_card_text() -> str:
    """The words media_capture draws onto og.png, read with ast (that module imports Qt)."""
    tree = ast.parse(open(_MEDIA_CAPTURE, encoding="utf-8").read())
    found = {t.id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name) and t.id in ("OG_BODY", "OG_CHIPS")}
    assert set(found) == {"OG_BODY", "OG_CHIPS"}, f"media_capture lost its og words: {found}"
    return found["OG_BODY"] + "\n" + " · ".join(found["OG_CHIPS"])


def _headline_surfaces(readme: str, page: str, og_card: str) -> dict[str, str]:
    """Each first-screen surface as flat visible text: README above its first image, the landing
    hero before its first figure, the page's three share descriptions, and the social card's words
    — the image every one of those shares shows beside them."""
    hero = page[page.index('<section class="hero">'):]
    surfaces = {"README.md above its first <img": readme[:readme.index("<img")].replace("**", ""),
                "index.html hero before its first <figure":
                    _unescape(re.sub(r"<[^>]+>", " ", hero[:hero.index("<figure")]))}
    surfaces.update((f"index.html {k}", _unescape(v)) for k, v in _META.findall(page))
    surfaces[_OG_CARD] = og_card
    return {k: " ".join(v.split()) for k, v in surfaces.items()}


def _headline_problems(rows: list[dict], surfaces: dict[str, str], cmake: str) -> list[str]:
    head, barred = _headline(rows)
    problems = [f"{name}: no {head!r}" for name, text in surfaces.items() if head not in text]
    problems += [f"{name}: carries {b!r}, a figure that leans on the rows nothing re-measures"
                 for name, text in surfaces.items() for b in barred
                 if re.search(rf"(?<![\d.]){re.escape(b)}(?![\d])", text)]
    problems += [f"ACCURACY row {r['name']!r}: rerun {r['rerun']!r} is no add_footage_test"
                 for r in rows if r["rerun"]
                 and not re.search(rf"(?m)^add_footage_test\(\S+ {r['rerun']}\)", cmake)]
    return problems


def test_the_headline_is_the_re_runnable_rows():
    """README's first screen, the landing hero, the meta, og and twitter descriptions and the words
    drawn onto og.png carry the headline media_capture.ACCURACY derives, and no figure of a row
    nothing re-measures. The same helper fails on each planted defect: a re-measure that moved σ
    with the pages left stale, row A headlining, a surface quoting A's σ or the retired bound, the
    circuit count dropped, the card left on its old copy, and a rerun that names no registered
    footage check."""
    rows, cmake = _accuracy_rows(), open(_CMAKE, encoding="utf-8").read()
    readme = open(os.path.join(_REPO, "README.md"), encoding="utf-8").read()
    surfaces = _headline_surfaces(readme, _page(), _og_card_text())
    assert len(surfaces) == 6, f"expected 6 headline surfaces, found {sorted(surfaces)}"
    problems = _headline_problems(rows, surfaces, cmake)
    assert not problems, ("the first screen does not carry ACCURACY's headline:\n  "
                          + "\n  ".join(problems))

    def row(name: str, **kw) -> list[dict]:
        return [{**r, **kw} if r["name"] == name else r for r in rows]

    readme_key = "README.md above its first <img"
    head, _ = _headline(rows)
    plants = (  # (what, rows, surface edits, a fragment the problems must name)
        ("C re-measured to σ 0.0312 s, pages stale", row("Recording C", sigma=0.0312), {},
         "no 'σ 0.031 s over 14 laps"),
        ("row A headlining", row("Recording A", rerun="accuracy_mk"), {},
         "no 'σ 0.025–0.087 s over 62"),
        ("README quoting A's σ", rows, {readme_key: surfaces[readme_key] + " σ 0.0871 s"},
         "carries '0.0871'"),
        ("og quoting the retired bound", rows,
         {"index.html og:description": surfaces["index.html og:description"] + " ±0.003 s"},
         "og:description: carries '±0.003'"),
        ("the circuit count dropped", rows,
         {"index.html description": surfaces["index.html description"].replace(
             head, head.replace(" at one circuit", ""))}, "index.html description: no"),
        ("the card on its pre-headline copy", rows,
         {_OG_CARD: "Transponder-validated true-clock lap timing, a synthesised ideal lap, a "
                    "speed-coloured track map, synced video and corner-by-corner coaching."},
         f"{_OG_CARD}: no"),
        ("a rerun with no footage check", row("Recording C", rerun="accuracy_nowhere"), {},
         "'accuracy_nowhere' is no add_footage_test"))
    for what, planted_rows, edits, named in plants:
        assert not edits or any(surfaces[k] != v for k, v in edits.items()), f"{what!r}: a no-op"
        caught = _headline_problems(planted_rows, {**surfaces, **edits}, cmake)
        assert any(named in p for p in caught), f"{what!r} not caught as {named!r}: {caught}"
    print(f"test_the_headline_is_the_re_runnable_rows OK ({head!r} on {len(surfaces)} surfaces; "
          f"{len(plants)} plants caught)")


# ------------------------------------------------------------------ 8. reachable without hue or mouse
# FRONT-DOOR-14 measured the page in headless Chrome at 1440 and 390 px (no download: the DevTools
# protocol over a pipe): every text/background pair clears WCAG AA (lowest 5.9:1), every link,
# image and the clip is named, and Tab reaches every link in DOM order with a visible ring. What it
# found were the two things a stylesheet gets wrong that a contrast table cannot show — 15 links in
# running text told apart by hue alone, and a code block that scrolls sideways where a keyboard
# could not reach it. These two checks hold both from the markup and the CSS, with no browser.
_BLOCKS = {"p", "li", "div", "figcaption", "figure", "footer", "header", "main", "nav", "section",
           "td", "th", "dd", "dt", "blockquote", "pre", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
           "h6"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
         "track", "wbr"}
_COMPOUND = re.compile(r"([a-z][a-z0-9]*)?((?:\.[\w-]+)*)")


class _Outline(HTMLParser):
    """Every element as (tag, classes, attrs, parent), each link's text, and for each block the
    text outside and inside its links — the markup half of axe-core's is-in-text-block."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.nodes: list[tuple[str, set[str], dict, int]] = []
        self.stack: list[int] = []
        self.links: dict[int, str] = {}
        self.text: dict[int, list[str]] = {}
        self.hidden = 0
        self.feed(_without_comments(html))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.nodes.append((tag, set((a.get("class") or "").split()), a,
                           self.stack[-1] if self.stack else -1))
        if tag == "a" and "href" in a:
            self.links[len(self.nodes) - 1] = ""
        if tag not in _VOID:
            self.stack.append(len(self.nodes) - 1)
            self.hidden += tag == "svg" or a.get("aria-hidden") == "true"

    def handle_endtag(self, tag):
        while self.stack:
            t, _, a, _ = self.nodes[self.stack.pop()]
            self.hidden -= t == "svg" or a.get("aria-hidden") == "true"
            if t == tag:
                break

    def handle_data(self, data):
        link = next((i for i in reversed(self.stack) if i in self.links), None)
        if link is not None:
            self.links[link] += data
        block = next((i for i in reversed(self.stack) if self.nodes[i][0] in _BLOCKS), None)
        if block is not None and not self.hidden:
            self.text.setdefault(block, ["", ""])[link is not None] += data

    def chain(self, i: int) -> list[tuple[str, set[str]]]:
        """(tag, classes) of node `i`, then of each ancestor, innermost first."""
        out = []
        while i >= 0:
            out.append(self.nodes[i][:2])
            i = self.nodes[i][3]
        return out

    def in_running_text(self, i: int) -> bool:
        """axe-core's rule: the link's block has more text outside its links than inside them."""
        b = self.nodes[i][3]
        while b >= 0 and self.nodes[b][0] not in _BLOCKS:
            b = self.nodes[b][3]
        out, inside = (" ".join(s.split()) for s in self.text.get(b, ["", ""]))
        return len(out) > len(inside)


def _css_rules(css: str) -> list[tuple[list[str], str]]:
    """(selectors, declarations) of every style rule, those inside @media included."""
    return [([s.strip() for s in sel.split(",")], body)
            for sel, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", _strip_css_comments(css))]


def _decoration(body: str) -> str | None:
    m = re.search(r"text-decoration(?:-line)?\s*:\s*([^;]+)", body)
    return m.group(1).strip() if m else None


def _selects(selector: str, chain: list[tuple[str, set[str]]]) -> bool | None:
    """Does `selector` — tag/class compounds joined by the descendant combinator — select
    chain[0]? None for a selector this reader does not speak (`>`, ids, pseudo-classes), so a
    rule it cannot read is reported rather than passed."""
    comps = []
    for part in selector.split():
        m = _COMPOUND.fullmatch(part)
        if not m or not (m.group(1) or m.group(2)):
            return None
        comps.append((m.group(1), set(m.group(2).split(".")) - {""}))

    def fits(comp, node):
        return (comp[0] is None or node[0] == comp[0]) and comp[1] <= node[1]

    if not chain or not fits(comps[-1], chain[0]):
        return False
    j = 1
    for comp in reversed(comps[:-1]):
        while j < len(chain) and not fits(comp, chain[j]):
            j += 1
        if j == len(chain):
            return False
        j += 1
    return True


_STATE = re.compile(r":(?:hover|focus|focus-visible|focus-within|active|visited)\b")


def _hue_only_links(html: str) -> list[str]:
    """Links in running text that the stylesheet leaves told apart from it by hue alone."""
    rules = _css_rules(_style_block(html))
    problems = []
    if not any("underline" in (_decoration(b) or "") for sels, b in rules if "a" in sels):
        problems.append("the `a` rule does not underline links: amber on the page's dim prose is "
                        "1.28:1, so a link inside a sentence is told apart by hue alone (WCAG 1.4.1)")
    page = _Outline(html)
    for i, text in page.links.items():
        if not page.in_running_text(i):
            continue                    # a row of links (the bar, the footer), not a sentence
        for sels, body in rules:
            dec = _decoration(body)
            if dec is None or "underline" in dec:
                continue
            for s in sels:
                if _STATE.search(s):
                    continue            # hover/focus: not how a reader finds the link at rest
                hit = _selects(s, page.chain(i))
                if hit is None:
                    problems.append(f"cannot read the selector {s!r} to rule it out")
                elif hit:
                    problems.append(f"`{s} {{ text-decoration: {dec} }}` takes the underline off "
                                    f"a link in running text: {' '.join(text.split())!r}")
    return problems


def _unreachable_scrollers(html: str) -> list[str]:
    """Elements the stylesheet lets scroll that a keyboard cannot reach: no tabindex="0" and
    nothing focusable inside to scroll them by."""
    page = _Outline(html)
    problems = []
    for sels, body in _css_rules(_style_block(html)):
        if not re.search(r"overflow(?:-[xy])?\s*:\s*(?:auto|scroll)", body):
            continue
        for s in sels:
            for i, (tag, _, attrs, _) in enumerate(page.nodes):
                hit = _selects(s, page.chain(i))
                if hit is None:
                    problems.append(f"cannot read the selector {s!r} of a scrolling rule")
                    break
                inner = any(i in _ancestors(page, j) for j in page.links)
                if hit and attrs.get("tabindex") != "0" and not inner:
                    problems.append(f"<{tag}> (selected by {s!r}) can scroll, and nothing in it "
                                    "takes focus: give it tabindex=\"0\"")
    return problems


def _ancestors(page: _Outline, i: int):
    i = page.nodes[i][3]
    while i >= 0:
        yield i
        i = page.nodes[i][3]


def test_links_in_running_text_are_more_than_a_hue():
    """A link inside a sentence is underlined; only rows made of links (the bar, the footer) drop it.

    THE BUG: `a { text-decoration: none }`. The links are amber (C.accent), the prose around them
    is C.text_dim, and the two differ by 1.28:1 in luminance — so in 15 places ("three commands
    with pixi" in the hero's callout, the nine write-ups in "Built, measured, not shipped", the
    licence in the footer) a reader without full colour vision could not see there was a link.
    axe-core rates that "serious" (link-in-text-block, WCAG 1.4.1 Use of Color, level A)."""
    html = _page()
    problems = _hue_only_links(html)
    assert not problems, "docs/index.html:\n  " + "\n  ".join(problems)
    page = _Outline(html)
    inline = [t for i, t in page.links.items() if page.in_running_text(i)]
    rows = [t for i, t in page.links.items() if not page.in_running_text(i)]
    assert len(inline) >= 10 and any("pixi" in t for t in inline), (
        f"only {len(inline)} links read as running text — the block walk has gone vacuous")
    assert any("Source on GitHub" in t for t in rows), "the bar's button reads as running text"
    m = re.search(r"\n  a \{[^}]*\}", html)
    assert m, "the page has no top-level `a` rule"
    base = m.group(0)
    plants = {
        "the underline removed": html.replace(base, base.replace("underline", "none")),
        "a callout opting out": html.replace("</style>", "  .callout a { text-decoration: none; }\n"
                                                         "</style>"),
        "an unreadable opt-out": html.replace("</style>", "  .callout > a { text-decoration: none; }"
                                                          "\n</style>"),
        "a pseudo-class opt-out": html.replace("</style>", "  .callout a:not(.x) { text-decoration: "
                                                           "none; }\n</style>"),
    }
    for what, planted in plants.items():
        assert planted != html, f"{what!r} planted nothing"
        assert _hue_only_links(planted), f"{what!r} was not caught"
    print(f"test_links_in_running_text_are_more_than_a_hue OK ({len(inline)} links in running "
          f"text, {len(rows)} in rows; {len(plants)} plants caught)")


def test_what_scrolls_is_reachable_by_keyboard():
    """The build block scrolls sideways on a narrow screen, so it takes focus itself.

    THE BUG: at 390 px the `<pre>` of build commands is 523 px of text in a 340 px box. Chrome and
    Firefox make a scroller focusable on their own; Safari does not, so a keyboard reader — at
    400 % zoom a 1280 px window is 320 px — could not scroll it. axe-core rates that "serious"
    (scrollable-region-focusable, WCAG 2.1.1). A keyboard-only scroller needs `tabindex="0"`."""
    html = _page()
    problems = _unreachable_scrollers(html)
    assert not problems, "docs/index.html:\n  " + "\n  ".join(problems)
    pres = [n for n in _Outline(html).nodes if n[0] == "pre"]
    assert pres, "no <pre> on the page — the scrolling rule this check exists for has gone"
    planted = re.sub(r'<pre tabindex="0">', "<pre>", html)
    assert planted != html and _unreachable_scrollers(planted), "a <pre> without tabindex passed"
    print(f"test_what_scrolls_is_reachable_by_keyboard OK ({len(pres)} scrolling <pre>)")


_LANDMARKS = {"header", "nav", "main", "footer", "aside"}
# Never rendered as content (a script's text, a template's inert tree, a noscript block while
# scripting is on), so axe's region has nothing in them to flag.
_UNRENDERED = {"script", "noscript", "template"}


def _landmark_problems(html: str) -> list[str]:
    """The page's landmarks, read from the markup, each problem named for the rule it breaks.

    Two are axe-core's: one <main> (landmark-one-main when there is none, landmark-no-duplicate-
    main when there are two), and nothing outside a landmark (region). Region is read stricter
    than axe reads it: axe flags rendered CONTENT outside every landmark, this flags every child of
    <body> that is not a landmark, content or not (an empty `<a id="top">` too), bar the three
    that never render — and a page with no <body> tag is reported, not passed with nothing read.
    Two are house rules no axe run would report: the bar's links are a <nav> (inside <header>, the
    banner landmark, a <div> breaks no axe rule), and every <nav> is named (axe's landmark-unique
    fires only when two landmarks share a role and a name, never on a lone unnamed <nav>)."""
    page = _Outline(html)
    problems = []
    mains = sum(n[0] == "main" for n in page.nodes)
    if mains == 0:
        problems.append("no <main> element: a page has one (axe landmark-one-main)")
    elif mains > 1:
        problems.append(f"{mains} <main> elements: a page has at most one "
                        "(axe landmark-no-duplicate-main)")
    body = next((i for i, n in enumerate(page.nodes) if n[0] == "body"), None)
    if body is None:
        problems.append("no <body> tag: the region check has no children of <body> to read")
    for tag, _, attrs, parent in page.nodes:
        if body is not None and parent == body and tag not in _LANDMARKS | _UNRENDERED:
            what = attrs.get("id") or attrs.get("class") or ""
            problems.append(f"<{tag}> {what!r} sits outside every landmark "
                            "(axe region, read per element)")
    bar = [n for n in page.nodes if "nav-links" in n[1]]
    if not bar:
        problems.append("the bar's .nav-links is gone — if that was deliberate, update this check")
    for tag, _, _, _ in bar:
        if tag != "nav":
            problems.append(f"the bar's links sit in a <{tag}>, not a <nav> (house rule: without "
                            "it the landmark list has no navigation entry)")
    for tag, _, attrs, _ in page.nodes:
        if tag == "nav" and not (attrs.get("aria-label") or attrs.get("aria-labelledby") or "").strip():
            problems.append("a <nav> has no accessible name (house rule: the landmark list reads "
                            "a bare \"navigation\")")
    return problems


def test_the_page_has_one_main_and_a_named_nav():
    """Every section sits in one <main>, and the bar's links are a <nav> with a name.

    THE BUG (qa-w3 EVAL-7): FRONT-DOOR-14's probe counted the landmarks a screen reader jumps
    between — header 1, footer 1, main 0, nav 0. Everything between the bar and the footer, the
    whole page, was outside any landmark: axe-core rates that "moderate" (landmark-one-main,
    region), so FRONT-DOOR-14's "serious" scope left it. The bar's links were a bare <div>, which
    no axe rule flags inside <header>; a named <nav> for them is this page's own rule.

    THE FOLLOW-UP (#506's review): the check called its two house rules axe's, passed a page with
    no <body> tag having read nothing, and would have flagged a <script> under <body>. So each
    plant must be reported under the rule it breaks, and the three unrendered children must pass."""
    html = _page()
    problems = _landmark_problems(html)
    assert not problems, "docs/index.html:\n  " + "\n  ".join(problems)
    page = _Outline(html)
    navs = [i for i, n in enumerate(page.nodes) if n[0] == "nav"]
    in_nav = [i for i in page.links if any(a in navs for a in _ancestors(page, i))]
    assert len(in_nav) >= 3, f"only {len(in_nav)} links in the <nav> — the bar has gone vacuous"
    # Each defect with the rule that must name it: a house rule reported as axe's sends a reader to
    # an axe run that passes, so the name is checked as well as the catch.
    plants = {
        "no <main>": (html.replace("<main>", "").replace("</main>", ""),
                      "(axe landmark-one-main)"),
        "a second <main>": (html.replace('<section id="build">',
                                         '</main><main><section id="build">'),
                            "(axe landmark-no-duplicate-main)"),
        "a section after </main>": (html.replace("</main>", "").replace(
            '<section id="build">', '</main>\n<section id="build">'), "(axe region"),
        "the bar a <div> again": (re.sub(r"<nav ([^>]*)>(.*?)</nav>", r"<div \1>\2</div>", html,
                                         flags=re.S), "not a <nav> (house rule"),
        "a nameless <nav>": (re.sub(r'(<nav [^>]*?) aria-label="[^"]*"', r"\1", html),
                             "no accessible name (house rule"),
        "no <body> tag": (html.replace("<body>", "").replace("</body>", ""), "no <body> tag"),
    }
    missed = []
    for what, (planted, named) in plants.items():
        assert planted != html, f"{what!r} planted nothing"
        caught = _landmark_problems(planted)
        if not any(named in p for p in caught):
            missed.append(f"{what}: not reported as {named!r}, got {caught}")
    # What never renders is no content outside a landmark, so axe's region skips it.
    unrendered = (("script", "void 0"), ("noscript", "<p>Script is off.</p>"),
                  ("template", "<p>A row.</p>"))
    for tag, inner in unrendered:
        planted = html.replace("</body>", f"<{tag}>{inner}</{tag}>\n</body>")
        assert planted != html, f"a <{tag}> planted nothing"
        flagged = _landmark_problems(planted)
        if flagged:
            missed.append(f"a <{tag}> under <body>, which never renders, was flagged: {flagged}")
    assert not missed, "the landmark check on its plants:\n  " + "\n  ".join(missed)
    print(f"test_the_page_has_one_main_and_a_named_nav OK (1 <main>, {len(navs)} named <nav> "
          f"holding {len(in_nav)} links; {len(plants)} plants caught by name, "
          f"{len(unrendered)} unrendered children passed)")


if __name__ == "__main__":
    test_stylesheet_parses()
    test_palette_is_derived_from_theme()
    test_images_resolve_and_declare_their_real_size()
    test_links_resolve()
    test_every_quoted_pixi_command_exists()
    test_page_is_self_contained()
    test_markdown_images_resolve()
    test_public_pages_quote_the_real_suite_size()
    test_public_pages_quote_the_real_core_size()
    test_the_pages_count_what_ci_skips()
    test_the_pages_date_their_timings_and_agree_on_them()
    test_the_pages_quote_the_demo_s_real_size()
    test_the_first_screen_offers_the_demo_in_three_commands()
    test_no_public_page_uses_a_retired_ui_word()
    test_the_clip_is_silent_small_and_still_on_request()
    test_the_clip_check_fails_on_each_planted_defect()
    test_decisions_says_what_is_true_and_public()
    test_the_headline_is_the_re_runnable_rows()
    test_links_in_running_text_are_more_than_a_hue()
    test_what_scrolls_is_reachable_by_keyboard()
    test_the_page_has_one_main_and_a_named_nav()
    print("ALL OK")
