"""THE CAMERA-SUPPORT GUARD — no public document may promise GPS9 timing a camera cannot give.

WHY THIS FILE EXISTS. Four public surfaces said "GPS9 camera (Hero 9 and newer)" — the README's
accuracy showpiece, docs/ACCURACY.md, docs/FIRST_LAP.md, the landing page, and the map's
timing-quality tooltip in `studio/central_view.py`. GoPro's own metadata specification, vendored in
this repo at `3rdparty/gpmf-parser/README.md`, says otherwise:

  * GPS9 is introduced under "### HERO11 changes" — it does not exist on a Hero 9 or a Hero 10;
  * "### HERO12 changes" records `| GPS9 | removed | --- | --- | No GPS receiver in HERO12 |`,
    AND `| GPS5 | removed |` — that camera emits no GPS at all and cannot be lap-timed;
  * "### HERO13 changes" brings it back ("GPS returns for HERO13").

So true-clock timing is a Hero 11 or a Hero 13, and the claim overstated it by three whole models
in the flattering direction — including one that cannot produce a lap time at all. The CODE was
never wrong: `studio/load.py::_used_gps9_trueclock` looks for the stream at runtime and falls back
to the media clock, and `studio/data_quality.py` classifies the result. Only the prose lied, which
is exactly the class of defect nothing in this repo was checking.

THE FOUR CHECKS

  1. THE PINNED TABLE IS THE SPEC'S. `_MODELS` below is DERIVED from the vendored spec when the
     submodule is checked out (CI checks out `submodules: recursive`), by walking the per-model
     `### HERO<n>` sections in order and carrying GPS5/GPS9 state forward across the "Otherwise
     Supports All HERO<n-1> metadata" inheritance the spec is written in. When the submodule is
     absent — a bare worktree, which is the common local case — the check prints SKIP and the
     pinned table stands alone. So the guard runs everywhere and cannot rot where it matters.
  2. NO OPEN-ENDED CLAIM. "Hero 9 and newer", "Hero 9+", "Hero 11 or later" — an open-ended model
     range in a sentence that also makes a GPS9/true-clock claim is banned outright, whatever
     models it happens to cover today. It is the exact shape the original defect took, and it is
     wrong by construction: GPS9's history has a HOLE in it (the Hero 12), so no "and newer" form
     can ever be true of it.
  3. NO DOCUMENT NAMES A NON-GPS9 MODEL AS GPS9-CAPABLE. The direct negative: every model named in
     the clause immediately following a "GPS9 camera" claim must actually carry GPS9.
  4. THE DOCUMENTS SPELL THE DERIVED SET, AND THE HERO 12. Each public surface that makes the
     claim must name exactly the models check 1 derived, and must state the Hero 12's missing
     receiver — the one fact a reader holding that camera needs before buying anything.

Plus check 5, the anti-vacuity one: the scan must still FIND a claim in every file known to make
one (so a reword cannot silently empty the guard), and the guard must still FAIL the original
defect's exact wording, which is asserted directly against the string that shipped.

Pure stdlib, no Qt, no pacer, no telemetry file — it reads text. Needs neither the offscreen env
nor the bindings PYTHONPATH.
"""

from __future__ import annotations

import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = os.path.join(_REPO, "3rdparty", "gpmf-parser", "README.md")

# --- THE PINNED TRUTH TABLE ------------------------------------------------------------------
# model -> (has_gps5, has_gps9). Derived from 3rdparty/gpmf-parser/README.md, sections
# "### HERO5 Black with GPS Enabled Adds" (line ~536) through "### HERO13 changes" (line ~634).
# TO RE-DERIVE: check out the submodule (`git submodule update --init 3rdparty/gpmf-parser`) and
# run this file — check 1 recomputes the table from the spec and asserts it equals this literal.
# A new camera in an updated spec therefore fails HERE, not silently in the prose.
_MODELS: dict[str, tuple[bool, bool]] = {
    "Hero 5":  (True,  False),
    "Hero 6":  (True,  False),
    "Hero 7":  (True,  False),
    "Hero 8":  (True,  False),
    "Hero 9":  (True,  False),
    "Hero 10": (True,  False),
    "Hero 11": (True,  True),
    "Hero 12": (False, False),   # "No GPS receiver in HERO12" — cannot be lap-timed at all
    "Hero 13": (False, True),    # GPS9 returns; GPS5 stayed removed
    "Max":     (True,  False),   # "Otherwise Supports All HERO7 metadata"
}

_GPS9_MODELS = [m for m, (_, g9) in _MODELS.items() if g9]
_NO_GPS_MODELS = [m for m, (g5, g9) in _MODELS.items() if not g5 and not g9]

# --- the public surfaces that make (or must not make) the claim ------------------------------
# Every file a reader can see without opening the source: the three markdown documents, the
# landing page, and the one UI string that states camera support in the app itself.
_DOC_FILES = [
    "README.md",
    os.path.join("docs", "ACCURACY.md"),
    os.path.join("docs", "FIRST_LAP.md"),
    os.path.join("docs", "index.html"),
]
# The app's own timing-quality tooltip states camera support too, so checks 2 and 3 cover it —
# but NOT the Hero 12 clause of check 4. That tooltip explains why THIS recording is degraded,
# and a Hero 12 recording can never reach it: with no receiver there are no fixes, no laps, and
# the zero-lap surfaces answer instead. Requiring the caveat there would be noise in the one
# place the reader has already ruled it out.
_CLAIM_FILES = _DOC_FILES + [os.path.join("studio", "central_view.py")]

# A sentence makes a camera-support claim if it carries one of these.
_CLAIM_ANCHOR = re.compile(r"GPS9|true[- ]clock|true clock", re.I)
# The tight binding used by check 3: the models named right after "GPS9 camera(s)".
_GPS9_CAMERA = re.compile(r"GPS9\s+cameras?", re.I)
# Open-ended model ranges (check 2). "Hero 9+", "Hero 9 and newer", "Hero 11 or later", …
_OPEN_ENDED = re.compile(
    r"Hero\s*\d+\s*(?:\+|and\s+(?:newer|later|up)|or\s+(?:newer|later|up))", re.I)


def _read(rel: str) -> str:
    with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
        return f.read()


def _plain(text: str) -> str:
    """Strip HTML tags and markdown emphasis so the landing page and the markdown read alike."""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[*`]", "", text)


def _sentences(text: str) -> list[str]:
    """Split on sentence-ish boundaries. Markdown wraps lines mid-sentence, so newlines are
    whitespace, not breaks; `;` and `:` DO break, because they are where a support clause hands
    over to its exception clause ("…a Hero 13; GPS5 cameras fall back…")."""
    flat = re.sub(r"\s+", " ", text)
    return [s for s in re.split(r"(?<=[.!?;:])\s+", flat) if s.strip()]


def _models_in(fragment: str) -> set[str]:
    """Every camera model a fragment names, with ranges and open-ended forms expanded.

    Ordered alternation: the multi-model forms must be tried before the bare `Hero N`, or
    "Hero 5 through Hero 10" would read as two unrelated singles."""
    found: set[str] = set()
    nums = sorted(int(m.split()[-1]) for m in _MODELS if m.startswith("Hero "))
    hi = max(nums)

    def _name(n: int) -> str | None:
        return f"Hero {n}" if n in nums else None

    pattern = re.compile(
        r"Hero\s*(?P<open>\d+)\s*(?:\+|and\s+(?:newer|later|up)|or\s+(?:newer|later|up))"
        r"|Hero\s*(?P<lo>\d+)\s*(?:–|—|-|through|to)\s*(?:Hero\s*)?(?P<hi>\d+)"
        r"|Hero\s*(?P<one>\d+)"
        r"|(?P<max>\bMax\b)", re.I)
    for m in pattern.finditer(fragment):
        if m.group("open"):
            found |= {f"Hero {n}" for n in range(int(m.group("open")), hi + 1) if n in nums}
        elif m.group("lo"):
            found |= {f"Hero {n}" for n in range(int(m.group("lo")), int(m.group("hi")) + 1)
                      if n in nums}
        elif m.group("one"):
            name = _name(int(m.group("one")))
            if name:
                found.add(name)
        elif m.group("max"):
            found.add("Max")
    return found


# ------------------------------------------------------------------ 1. the table is the spec's
def _derive_from_spec() -> dict[str, tuple[bool, bool]]:
    """Walk the spec's per-model sections, carrying GPS5/GPS9 forward through its inheritance."""
    with open(_SPEC, encoding="utf-8") as f:
        spec = f.read()
    # Section headings: "### HERO9 Changes, …", "### GoPro MAX (v2.0) …". Body = up to the next ###.
    parts = re.split(r"^###\s+", spec, flags=re.M)[1:]
    sections: list[tuple[str, str]] = []
    for part in parts:
        head, _, body = part.partition("\n")
        hero = re.match(r"(?:GoPro\s+)?HERO\s*(\d+)", head, re.I)
        if hero:
            sections.append((f"Hero {int(hero.group(1))}", body))
        elif re.match(r"(?:GoPro\s+)?MAX\b", head, re.I):
            sections.append(("Max", body))

    def _row(body: str, fourcc: str) -> str | None:
        m = re.search(rf"^\|\s*{fourcc}\s*\|\s*([^|]*)\|", body, re.M)
        return m.group(1).strip() if m else None

    derived: dict[str, tuple[bool, bool]] = {}
    gps5 = gps9 = False           # nothing carries GPS before the HERO5-with-GPS section
    for model, body in sections:
        if model == "Max":        # "Otherwise Supports All HERO7 metadata" — a branch, not a step
            derived["Max"] = derived.get("Hero 7", (gps5, gps9))
            continue
        for fourcc in ("GPS5", "GPS9"):
            val = _row(body, fourcc)
            if val is None:
                continue                    # no row: this model inherits the state carried in
            state = not val.lower().startswith("removed")
            if fourcc == "GPS5":
                gps5 = state
            else:
                gps9 = state
        derived[model] = (gps5, gps9)
    return derived


def test_pinned_table_matches_vendored_spec():
    if not os.path.exists(_SPEC):
        print("test_pinned_table_matches_vendored_spec SKIP "
              "(3rdparty/gpmf-parser submodule not checked out; the pinned table stands)")
        return
    derived = _derive_from_spec()
    for model, expected in _MODELS.items():
        assert model in derived, (
            f"{model} is pinned in _MODELS but the vendored spec has no section for it — the "
            f"spec moved; re-derive the table")
        assert derived[model] == expected, (
            f"{model}: pinned (gps5, gps9)={expected} but the vendored spec derives "
            f"{derived[model]}. GoPro's spec changed — update _MODELS *and* every document "
            f"listed in _CLAIM_FILES.")
    extra = set(derived) - set(_MODELS)
    assert not extra, (
        f"the vendored spec documents camera models the pinned table has never heard of: "
        f"{sorted(extra)}. Add them to _MODELS and say what they support in the public docs.")
    print(f"test_pinned_table_matches_vendored_spec OK ({len(derived)} models derived from spec)")


# ------------------------------------------------------------------ 2. no open-ended claim
def test_no_open_ended_camera_claims():
    bad = []
    for rel in _CLAIM_FILES:
        for sentence in _sentences(_plain(_read(rel))):
            if _CLAIM_ANCHOR.search(sentence) and _OPEN_ENDED.search(sentence):
                bad.append(f"{rel}: {sentence.strip()[:160]}")
    assert not bad, (
        "open-ended camera range in a GPS9/true-clock claim. GPS9 support has a HOLE in it (the "
        "Hero 12 has no GPS receiver), so no 'and newer'/'+' form can be true — name the models:\n"
        + "\n".join(f"  - {b}" for b in bad))
    print(f"test_no_open_ended_camera_claims OK ({len(_CLAIM_FILES)} public surfaces)")


# ------------------------------------------------------------------ 3. only real GPS9 models
def test_no_document_claims_gps9_for_a_model_without_it():
    bad, claims = [], 0
    for rel in _CLAIM_FILES:
        text = re.sub(r"\s+", " ", _plain(_read(rel)))
        for m in _GPS9_CAMERA.finditer(text):
            # The clause right after the claim, cut at the first boundary that hands over to
            # another clause — so "…(a Hero 11 or a Hero 13) is validated…; GPS5 cameras (Hero 5
            # through Hero 10)…" cannot bleed its exception into the claim.
            window = text[m.end():m.end() + 140]
            cut = re.search(r"[.;:)]", window)
            if cut:
                window = window[:cut.start()]
            named = _models_in(window)
            if not named:
                continue
            claims += 1
            wrong = {n for n in named if not _MODELS[n][1]}
            if wrong:
                bad.append(f"{rel}: 'GPS9 camera{window}' names {sorted(wrong)}, which "
                           f"carr{'ies' if len(wrong) == 1 else 'y'} no GPS9")
    assert not bad, (
        f"a public document promises GPS9 timing to a camera that has none (GPS9 models per the "
        f"vendored spec: {_GPS9_MODELS}):\n" + "\n".join(f"  - {b}" for b in bad))
    assert claims >= 3, (
        f"only {claims} 'GPS9 camera …' claims found across {_CLAIM_FILES} — the check has gone "
        f"vacuous; the docs were reworded out from under it")
    print(f"test_no_document_claims_gps9_for_a_model_without_it OK ({claims} claims checked)")


# ------------------------------------------------------------------ 4. the docs spell the truth
def test_public_docs_name_the_derived_models_and_the_hero_12():
    missing, checked = [], 0
    for rel in _CLAIM_FILES:
        plain = re.sub(r"\s+", " ", _plain(_read(rel)))
        if not _GPS9_CAMERA.search(plain) and "GPS9 stream" not in plain:
            continue
        checked += 1
        for model in _GPS9_MODELS:
            if model not in plain:
                missing.append(f"{rel}: makes a GPS9 claim but never names {model}, which the "
                               f"spec says DOES carry GPS9")
        # The Hero 12 is the actionable fact: a reader holding one must not have to infer it from
        # an absence. Every public DOCUMENT enumerating support has to say the receiver is gone.
        if rel in _DOC_FILES:
            for model in _NO_GPS_MODELS:
                if model not in plain:
                    missing.append(f"{rel}: makes a GPS9 claim but never mentions {model}, which "
                                   f"has no GPS receiver at all and cannot be lap-timed")
    assert not missing, "\n".join(f"  - {m}" for m in missing)
    assert checked == len(_CLAIM_FILES), (
        f"only {checked} of {len(_CLAIM_FILES)} public surfaces still make a camera-support "
        f"claim the guard can see — a reword has emptied the check")
    print(f"test_public_docs_name_the_derived_models_and_the_hero_12 OK "
          f"(models {_GPS9_MODELS}; no-GPS {_NO_GPS_MODELS})")


# ------------------------------------------------------------------ 5. the guard is not vacuous
def test_guard_still_fails_the_wording_that_shipped():
    """The four sentences that were live in this repo must all be rejected — by the checks, on
    the strings themselves. Without this, a future refactor of the regexes could pass everything
    while catching nothing."""
    shipped = [
        "**Lap and sector timing you can audit.** True-clock timing on a GPS9 camera "
        "(Hero 9 and newer).",
        "**Trust, honestly labelled.** Timing from a GPS9 camera (Hero 9+) is validated against "
        "a real transponder.",
        "- **True-clock timing.** On a **GPS9 camera (Hero 9 and newer)**, every GPS sample "
        "carries its own timestamp.",
        "GPS9 cameras (Hero 9+) time on the camera clock; GPS5 cameras fall back to the video "
        "clock and are flagged.",
    ]
    for text in shipped:
        plain = re.sub(r"\s+", " ", _plain(text))
        open_ended = any(_CLAIM_ANCHOR.search(s) and _OPEN_ENDED.search(s)
                         for s in _sentences(plain))
        wrong_models = False
        for m in _GPS9_CAMERA.finditer(plain):
            window = plain[m.end():m.end() + 140]
            cut = re.search(r"[.;:)]", window)
            if cut:
                window = window[:cut.start()]
            wrong_models |= any(not _MODELS[n][1] for n in _models_in(window))
        assert open_ended and wrong_models, (
            f"the guard no longer rejects the wording that actually shipped — it has gone "
            f"vacuous (open_ended={open_ended}, wrong_models={wrong_models}):\n  {text}")
    print(f"test_guard_still_fails_the_wording_that_shipped OK ({len(shipped)} historic claims)")


# ------------------------------------------------------------------------------------- runner
def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} camera-support checks passed")


if __name__ == "__main__":
    sys.exit(_run_all())
