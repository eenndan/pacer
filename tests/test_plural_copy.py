"""K2 — a count spliced into plural-only grammar cannot come back silently.

The Stats ▸ IDEAL LAP note printed "These 1 segments hold 0.28 s of the 0.31 s" (#403 found it).
A sweep of the shipped `studio/*.py` for the same class found it wherever a count in the app's copy
can legally be 1: "can't say: 1 clean laps through it today", "1 excluded of 1 laps found",
"N = 1 raw GPS fixes", "these 1 clean lap", "1 of 40 cells are muted", "One of the 1 most
erratic-and-slow corners", "over 1 closed laps" — each an f-string with nothing choosing the
singular. This walks every top-level `studio/*.py` with `ast` and flags the three shapes the class
takes:

  * NOUN — a count followed by a plural noun: `f"{n} laps"`, `f"{n} clean laps"`;
  * DETERMINER — a plural determiner in front of a count: `f"These {n} …"`;
  * VERB — a plural verb agreeing with a count: `f"the other {n} hold …"`, and the partitive
    `f"{a} of {b} cells are …"`, whose verb agrees with `a`.

A site is clean when:
  * the noun comes out of `_signal.plural` or an inline `'' if n == 1 else 's'`, so no plural
    noun follows the count in the source;
  * the function it sits in compares THAT count with 1 (or 2) — a singular branch exists;
  * the count is an UPPER_CASE constant whose value is not the integer 1 — resolved by importing
    its module, so a constant later set to 1 fails here;
  * or it is in `ALLOWED`, beside the invariant that keeps the count above 1 (or the reason it
    is not user-facing). An entry that no longer matches a site fails, so the list cannot rot
    into a blanket pass.

Logging calls and `print` are out of scope — the log is a developer surface. `str.format` and
`%` templates are scanned too (`"{laps} clean laps"`), keyed by their placeholder.

NOT SEEN: agreement at a distance — a pronoun or a second verb later in the sentence ("on the
other {n} … so they are left out", "… and are shown muted"). The sweep fixed three of those by
reading; `test_the_fixed_copy_reads_right_at_one` renders each at 1.

NEGATIVE CONTROL: `RETIRED` holds spellings the sweep replaced, verbatim, and every one must be
flagged — the scanner is shown to see the class before its silence on the tree means anything.
Run: python tests/test_plural_copy.py
"""
import ast
import importlib
import os
import re
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STUDIO = os.path.join(_REPO, "studio")
sys.path.insert(0, _REPO)

# The words a count's noun phrase can run through before its noun ("{n} clean laps", "{n} raw
# GPS fixes"): up to three, stopping at a function word or a unit, so "{t} s and resamples" and
# "{cid} to focus" are not read as a count of resamples or of focus.
_STOP = frozenset(
    "and or but nor to the a an of in on at for by with from into onto than as is are was were be "
    "been has had it its that which who whom not no do does did can cannot will would may might "
    "must should could per s ms m km g deg px hz fps x".split())
# Words that end in -s without being a plural noun.
_NOT_PLURAL = frozenset(
    "this his yes less across plus minus always perhaps towards thus unless whereas besides "
    "focus status axis basis lens series".split())
# Present-tense verbs whose plural form differs from the singular ("hold" / "holds").
_PLURAL_VERBS = frozenset(
    "are were have hold contain carry remain show need sit fall lie count differ agree match "
    "lead stay go come make take".split())
_DETERMINER = re.compile(r"\b(these|those)\s+$", re.IGNORECASE)
_PARTITIVE = re.compile(r"^\s+of\s+(?:the\s+|its\s+|your\s+)?$")
_TOKEN = re.compile(r"[A-Za-z][\w'-]*|\S")
_TEMPLATE = re.compile(r"\{(\w+)(?:![rsa])?(?::[^{}]*)?\}")
_PRINTF = re.compile(r"%(?:\((\w+)\))?[di]")
# A format spec with a precision or a percent is a MEASUREMENT (`{t:.2f} s`), not a count.
_MEASURE_SPEC = re.compile(r"\.\d|%")
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
_LOG_NAMES = {"_log", "log", "logger", "_logger", "logging"}
# Calls whose result is a WORD, not a count: whatever count they carry has already agreed.
_WORD_CALLS = {"plural", "_plural", "lap_label", "str", "join", "basename", "format", "upper",
               "lower", "title", "capitalize", "replace", "strip"}


def _plural_word(text: str) -> str | None:
    """The plural noun a count agrees with, read off the text that FOLLOWS it: the first -s word
    among the next three, or None at a function word, a unit or punctuation."""
    for tok in _TOKEN.findall(text)[:3]:
        word = tok.rstrip("'")
        if not word[:1].isalpha() or word.lower() in _STOP:
            return None
        if (word.endswith("s") and len(word) > 2 and not word.isupper()
                and word.lower() not in _NOT_PLURAL):
            return word
    return None


def _plural_verb(text: str, *, after_noun: bool) -> str | None:
    """A plural verb agreeing with the count before `text`: the very next word, or — for the
    partitive "{a} of {b} cells are" — the first verb after up to two noun-phrase words, stopping
    at a function word ("laps that count" is the relative clause's verb, not the count's)."""
    for k, tok in enumerate(_TOKEN.findall(text)[:3 if after_noun else 1]):
        word = tok.lower()
        if word in _PLURAL_VERBS:
            return word
        if not after_noun or not word[:1].isalpha() or word in _STOP or k == 2:
            return None
    return None


def _phrase(text: str, *, before: bool = False) -> str:
    """The three words either side of a count that name the site in ALLOWED — so two sentences
    in one function cannot share an entry, and an entry goes stale when its copy is rewritten."""
    words = text.split()
    return " ".join(words[-3:] if before else words[:3])


def _is_word(node: ast.AST) -> bool:
    """A formatted value that is TEXT, not a count: a string literal, a string-valued
    conditional, a call that returns text, or a value named as a label or a name."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.IfExp):
        return _is_word(node.body) and _is_word(node.orelse)
    if isinstance(node, ast.Call):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        return name in _WORD_CALLS or name.startswith("fmt") or "name" in name
    src = ast.unparse(node)
    return "label" in src or re.search(r"name\w*$", src) is not None


def _parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._k2_parent = node  # type: ignore[attr-defined]


def _enclosing_def(node: ast.AST) -> ast.AST | None:
    p = getattr(node, "_k2_parent", None)
    while p is not None and not isinstance(p, ast.FunctionDef | ast.AsyncFunctionDef):
        p = getattr(p, "_k2_parent", None)
    return p


def _where(node: ast.AST) -> str:
    """`Class.method` / `function` / "" (module level) for a site."""
    names, p = [], getattr(node, "_k2_parent", None)
    while p is not None:
        if isinstance(p, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(p.name)
        p = getattr(p, "_k2_parent", None)
    return ".".join(reversed(names))


def _out_of_scope(node: ast.AST) -> bool:
    """Inside a logging call or a `print` — the developer's surface, not the copy."""
    p = getattr(node, "_k2_parent", None)
    while p is not None:
        if isinstance(p, ast.Call):
            f = p.func
            if isinstance(f, ast.Name) and f.id == "print":
                return True
            if (isinstance(f, ast.Attribute) and f.attr in _LOG_METHODS
                    and isinstance(f.value, ast.Name) and f.value.id in _LOG_NAMES):
                return True
        p = getattr(p, "_k2_parent", None)
    return False


def _singular_branch(scope: ast.AST, count: str) -> bool:
    """Does `scope` compare the count with 1 (or 2) anywhere — `n == 1`, `len(x) > 1`, `n < 2`?
    Then a singular branch exists, whichever way round it is written."""
    for node in ast.walk(scope):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            sides = (node.left, node.comparators[0])
            if (count in (ast.unparse(s) for s in sides)
                    and any(isinstance(s, ast.Constant) and s.value in (1, 2) for s in sides)):
                return True
    return False


def _statement_strings(tree: ast.AST) -> set[int]:
    """Docstrings and other bare string statements — prose about the code, not copy."""
    return {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}


def scan_source(source: str, module: str) -> list[dict]:
    """Every count-agreement site in one module's source that no singular branch covers, as
    `{module, where, line, count, rule, word, phrase}` — `where` the enclosing `Class.function`
    ("" at module level), `phrase` the words beside the count (see `_phrase`). Constants and
    ALLOWED are NOT applied here — `violations` does that, so the negative control sees the raw
    scan."""
    tree = ast.parse(source)
    _parents(tree)
    found = []

    def hit(node, count, rule, word, phrase, template=False):
        if not template and _singular_branch(_enclosing_def(node) or tree, count):
            return
        found.append({"module": module, "where": _where(node), "line": node.lineno,
                      "count": count, "rule": rule, "word": word, "phrase": phrase})

    prose = _statement_strings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and not _out_of_scope(node):
            vals = node.values
            for i, v in enumerate(vals):
                if not isinstance(v, ast.FormattedValue) or _is_word(v.value):
                    continue
                if v.format_spec and _MEASURE_SPEC.search(ast.unparse(v.format_spec)):
                    continue
                count = ast.unparse(v.value)
                before = vals[i - 1] if i else None
                after = vals[i + 1] if i + 1 < len(vals) else None
                if isinstance(after, ast.Constant):
                    text = after.value
                    if word := _plural_word(text):
                        hit(v, count, "noun", word, _phrase(text))
                    if word := _plural_verb(text, after_noun=False):
                        hit(v, count, "verb", word, _phrase(text))
                    # "{a} of {b} cells are": the verb agrees with `a`, not with `b`.
                    if (_PARTITIVE.match(text) and i + 3 < len(vals)
                            and isinstance(vals[i + 2], ast.FormattedValue)
                            and isinstance(vals[i + 3], ast.Constant)
                            and (word := _plural_verb(vals[i + 3].value, after_noun=True))):
                        hit(v, count, "verb", word, _phrase(vals[i + 3].value))
                if isinstance(before, ast.Constant) and (m := _DETERMINER.search(before.value)):
                    hit(v, count, "determiner", m[1].lower(), _phrase(before.value, before=True))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in prose and not _out_of_scope(node)
              and not isinstance(getattr(node, "_k2_parent", None), ast.JoinedStr)):
            text = node.value
            for m in [*_TEMPLATE.finditer(text), *_PRINTF.finditer(text)]:
                name = m[1] or "%d"
                if "label" in name or "name" in name:
                    continue
                tail = text[m.end():].split("{")[0]
                if word := _plural_word(tail):
                    hit(node, name, "noun", word, _phrase(tail), template=True)
                if dm := _DETERMINER.search(text[:m.start()]):
                    hit(node, name, "determiner", dm[1].lower(),
                        _phrase(text[:m.start()], before=True), template=True)
    return found


def scan_tree(studio_dir: str = _STUDIO) -> list[dict]:
    out = []
    for name in sorted(os.listdir(studio_dir)):
        if name.endswith(".py"):
            with open(os.path.join(studio_dir, name), encoding="utf-8") as fh:
                out += scan_source(fh.read(), name[:-3])
    return out


def _constant_value(module: str, count: str):
    """The value of an UPPER_CASE constant count (`MAX_ITEMS`, `stats_service.TREND_MIN_LAPS`)
    in its module's own namespace, or a sentinel when the count is not a constant."""
    if not re.fullmatch(r"(?:\w+\.)*[A-Z][A-Z0-9_]*", count):
        return _NOT_CONSTANT
    return eval(count, vars(importlib.import_module(f"studio.{module}")))  # noqa: S307


_NOT_CONSTANT = object()


# Sites whose count can never be 1, each beside the invariant that keeps it above 1 — or the reason
# the string never reaches the copy. Keyed (module, Class.function, count, the three words after
# it), so two sentences in one function never share an entry and a rewritten one goes stale here.
ALLOWED = {
    # --- not a count: text that happens to stand before an -s word or a plural verb.
    ("chapters", "desync_notice", "scope", "carries"):
        "`scope` is the text \" in {where}\"; the count's own branch is `len(bad) == 1`",
    ("chapters", "desync_notice", "scope", "carry more or"):
        "`scope` is the text \" in {where}\"; the count's own branch is `len(bad) == 1`",
    ("coaching", "_tied_lead_sentence", "nums", "sit closer together"):
        "`nums` is text — the tied gains, \"+0.21 s and +0.19 s\" — and there are 2+ of them",
    # --- not the copy: the log, or a developer's exception.
    ("corners", "lap_yaw_rate", "len(kappa)", "samples, the lap"):
        "a developer's ValueError about mismatched arrays, never shown in the app",
    ("driving", "Thresholds.describe", "self.n_moving", "moving samples the"):
        "only ever written to the log (session.py)",
    ("logsetup", "_FileFormatter.format", "cut", "more characters not"):
        "the log formatter's own truncation marker — the log is a developer surface",
    ("rotation", "RotationCheck.summary", "self.n", "samples: r="):
        "only ever written to the log (session.py)",
    ("rotation", "RotationCheck.summary", "self.loop_n", "laps"):
        "only ever written to the log (session.py)",
    # --- a count that cannot be 1, and why.
    ("app", "StudioWindow._open_recordings", "len(offerable) + 1", "recordings — opened"):
        "inside `if offerable:` — one more than a non-empty list",
    ("app", "StudioWindow._session_notice", "subset[1]", "chapters — File"):
        "`_chapter_subset` answers only for a STRICT subset of a recording's chapters",
    ("central_view", "", "laps", "clean laps across"):
        "_IDEAL_CHIP_SAMPLE is filled only for a STITCHED ideal: two donor laps or more",
    ("coaching", "brake_direction_line", "d.n_laps", "laps)"):
        "`brake_directions` drops a corner under MIN_BRAKE_LAPS (3) laps",
    ("coaching", "theme_actions", "ev.n_laps", "laps"):
        "`lead` is ranked, so its evidence clears MIN_CORNER_LAPS (3)",
    ("coaching", "theme_actions", "ev.n_laps", "laps have matched"):
        "`lead` is ranked, so its evidence clears MIN_CORNER_LAPS (3)",
    ("coaching", "theme_actions", "ev.reach_laps", "laps have matched"):
        "`lead` is ranked, so it reached its target on MIN_REACH_LAPS (2) laps",
    ("coaching_panel", "_reach_tip", "of", "clean laps"):
        "the table is filled only when Opportunities.enough: `of` >= MIN_LAPS (3)",
    ("coaching_panel", "_reason_cell", "d.n_laps", "clean laps that"):
        "`brake_directions` drops a corner under MIN_BRAKE_LAPS (3) laps",
    ("corner_model", "IdealSample.caption", "self.laps", "laps"):
        "printed only for a STITCHED ideal: two donor laps or more",
    ("corner_model", "IdealSample.sentence", "self.laps", "clean laps, across"):
        "printed only for a STITCHED ideal: two donor laps or more",
    ("export_controller", "ExportController._export_size_hint", "frames", "frames to render"):
        "a valid lap is MIN_LAP_TIME (5 s) or longer — 150 frames at 30 fps — or the session",
    ("export_controller", "ExportController._render_progress_detail", "total",
     "frames rendered ·"):
        "a valid lap is MIN_LAP_TIME (5 s) or longer — 150 frames at 30 fps — or the session",
    ("export_video", "Renderer.run_chunk", "planned", "frames) — the"):
        "raised only when planned - produced > _TAIL_FRAME_SLACK (1), so planned >= 2",
    ("focus", "outcome_sentence", "o.now.n_laps", "laps"):
        "a verdict needs now.n_laps >= MIN_CORNER_LAPS (3); the noun follows that count",
    ("gmeter", "AxisCheck.summary", "self.n", "unloaded samples (limit"):
        "the measurable branch: _AXIS_MIN_QUIET (200) unloaded samples or more",
    ("gmeter", "CrossCheck.summary", "self.n", "moving samples: lateral"):
        "`_cross_check` returns None under 10 moving samples",
    ("map_view", "MapView._add_sector", "n + 2", "even sectors. Drag"):
        "n counts sector lines (>= 0), so n + 2 >= 2",
    ("provenance", "lap_time", "len(rows)", "rows;"):
        "every fix of a lap plus the bracketing one each side",
    ("provenance_panel", "", "total", "rows. Copy as"):
        "TRUNCATION is shown only when total > MAX_ROWS (400)",
    ("provenance_panel", "ProvenancePanel.copy_csv", "len(self.prov.samples.rows)", "rows"):
        "LapFixes.window pads one fix each side: the raw-fix table has 0 rows or 2+",
    ("share_card", "ideal_sublabel", "laps", "laps — not"):
        "read under the stitched-ideal gate: two donor laps or more",
    ("stats_corners", "_corner_count_tip", "of", "clean laps matched"):
        "1 <= n < of on this branch, so of >= 2",
    ("stats_ideal", "IdealSection.refresh", "row.n", "clean laps drove"):
        "a shown row's gain clears the floor, so its donor is another lap: n >= 2",
    ("stats_panel", "", "n", "clean laps (median"):
        "BAND_SPLIT_NOTE is shown only with a split: SPLIT_MIN_SIDE (3) laps a side",
    ("stats_panel", "", "n", "clean laps ×"):
        "SPLITS_NOTE: the grid is drawn from MATRIX_MIN_LAPS (5) laps",
    ("stats_panel", "", "c", "sectors. ★ is"):
        "SPLITS_NOTE: the grid is drawn from two sectors up (one line or more)",
    ("stats_panel", "StatsView._refresh_corner_grid", "matrix.n_resolved[c]",
     "laps matched on"):
        "a typical exists only over MATRIX_MIN_LAPS (5) matched laps",
    ("stats_straights", "_straight_count_tip", "of", "clean laps matched"):
        "1 <= n < of on this branch, so of >= 2",
    ("stats_trust", "TrustSection.refresh", "cross.n", "samples"):
        "gmeter's `_cross_check` returns None under 10 moving samples",
    ("stats_trust", "TrustSection.refresh", "rot.n", "samples."):
        "rotation returns no RotationCheck under _MIN_LAP_SAMPLES (16) samples",
}

# NEGATIVE CONTROL: spellings this sweep replaced, verbatim (wrapped in a def so the scan has the
# same scope it had in place), with the rule each must trip.
RETIRED = {
    "stats_ideal note": ('''
def note(shown, rest, shown_s, gap_s, rest_s):
    return (f"These {len(shown)} segments hold {shown_s:.2f} s of the {gap_s:.2f} s; the other "
            f"{len(rest)} hold {rest_s:.2f} s between them, under "
            f"{IDEAL_GAIN_FLOOR:.2f} s each.")
''', {("len(shown)", "determiner"), ("len(shown)", "noun"), ("len(rest)", "verb")}),
    "focus few laps": ('''
def verdict(now):
    return dict(detail=f"{now.n_laps if now else 0} clean laps")
''', {("now.n_laps if now else 0", "noun")}),
    "lap table strip": ('''
def _excluded_headline(self, n, found, warn):
    return f"{n} excluded of {found} laps found"
''', {("found", "noun")}),
    "provenance panel": ('''
def build(p):
    return f"N = {p.n} raw GPS fixes in that window"
''', {("p.n", "noun")}),
    "coast note": ('''
def coast_note(laps, lo):
    return f"between {lo:.2f} s of coasting a lap, and these {laps} cannot put them in order."
''', {("laps", "determiner")}),
    "corners note": ('''
def note(muted, cells):
    return f"{muted} of {len(cells)} cells are muted: that lap's corner edge was not matched"
''', {("muted", "verb")}),
    "corner count tip": ('''
def tip(of, n):
    return (f"On the other {of - n} the corner was interpolated, so they are left out."
            f" {of - n} were interpolated between matched points")
''', {("of - n", "verb")}),
    "ideal chip template": ('''
SAMPLE = "stitched from {donors} of your {laps} clean laps across {corners} corners."
''', {("corners", "noun")}),
    "trust card": ('''
def refresh(degraded, valid):
    return f" · {len(degraded)} of {len(valid)} laps contain a second below good"
''', {("len(degraded)", "verb"), ("len(valid)", "noun")}),
    "reach tip": ('''
def _reach_tip(ev, of):
    return (f"{ev.reach_laps} of the {ev.n_laps} laps that count here already matched. "
            f"{ev.n_laps} of your {of} clean laps count: on the other {of - ev.n_laps}")
''', {("ev.n_laps", "noun"), ("ev.n_laps", "verb")}),
    "worst-corner mark": ('''
def refresh(worst):
    return f"One of the {len(worst)} most erratic-and-slow corners — ranked by"
''', {("len(worst)", "noun")}),
}


def violations(findings: list[dict]) -> tuple[list[str], set]:
    """(the findings no rule clears, the ALLOWED keys nothing matched)."""
    bad, used = [], set()
    for f in findings:
        key = (f["module"], f["where"], f["count"], f["phrase"])
        if key in ALLOWED:
            used.add(key)
            continue
        value = _constant_value(f["module"], f["count"])
        if value is not _NOT_CONSTANT:
            if isinstance(value, int) and not isinstance(value, bool) and value == 1:
                bad.append(f"studio/{f['module']}.py:{f['line']}: {f['count']} is 1 and is printed "
                           f"beside “{f['word']}”")
            continue
        bad.append(f"studio/{f['module']}.py:{f['line']} ({f['where'] or 'module level'}): "
                   f"{{{f['count']}}} meets “{f['word']}” ({f['rule']}) with no singular branch — "
                   f"use _signal.plural / a `== 1` branch, or add it to ALLOWED with the "
                   f"invariant that keeps it above 1")
    return bad, set(ALLOWED) - used


def test_no_count_in_the_copy_meets_a_plural_it_cannot_agree_with():
    findings = scan_tree()
    bad, stale = violations(findings)
    assert not bad, "\n".join(bad)
    assert not stale, f"ALLOWED entries that match no site any more — delete them: {sorted(stale)}"
    print(f"test_no_count_in_the_copy_meets_a_plural_it_cannot_agree_with OK ({len(findings)} "
          f"sites scanned past the singular branches; {len(ALLOWED)} held by a stated invariant)")


def test_the_scan_sees_every_retired_spelling():
    """The negative control: each spelling the sweep replaced is flagged, by the rule it broke."""
    for name, (src, expected) in RETIRED.items():
        got = {(f["count"], f["rule"]) for f in scan_source(src, "retired")}
        assert expected <= got, f"{name}: the scan missed {expected - got} (saw {got})"
    # …and the idioms the fix uses are silent: `plural`, an inline suffix, a `== 1` branch.
    clean = '''
def ok(n, rest, shown, laps):
    a = f"{plural(n, 'lap')} and {rest} lap{'' if rest == 1 else 's'} over {laps} of them"
    b = f"This {n} segment holds" if n == 1 else f"These {n} segments hold"
    return a + b
'''
    assert scan_source(clean, "clean") == [], scan_source(clean, "clean")
    print(f"test_the_scan_sees_every_retired_spelling OK ({len(RETIRED)} retired spellings)")


def test_the_fixed_copy_reads_right_at_one():
    """The sweep's fixes, rendered through the real functions at a count of 1 — and at 2, where
    the plural must be untouched."""
    from studio import coaching, focus, session_record
    from studio._signal import plural
    from studio.coaching_panel import _reach_tip, empty_state_copy
    from studio.stats_coasting import coast_note
    from studio.stats_corners import _corner_count_tip
    from studio.stats_straights import _straight_count_tip

    assert plural(1, "raw GPS fix", "raw GPS fixes") == "1 raw GPS fix"
    assert plural(2, "raw GPS fix", "raw GPS fixes") == "2 raw GPS fixes"
    assert plural(2, "lap") == "2 laps"

    # Focus: the few-laps refusal. 0, 1 or 2 are the only counts it can print.
    item = focus.FocusItem(cid=4, direction=1, enter_frac=0.1, exit_frac=0.2, median_s=4.5,
                           iqr_s=0.1, n_laps=38, time_lost=0.2, reason="", reach="",
                           fingerprint="A", date="2026-05-23", lap_total=1066.2, verified=True,
                           degraded=False)
    now = {"list_track": "T", "track": "T", "fingerprint": "B", "date": "2026-05-24",
           "lap_total": 1066.2, "verified": True, "degraded": False}
    store = session_record.empty_store()
    for fp in ("A", "B"):     # two dry days on record, so only the lap count can refuse
        session_record.put(store, fp, {**session_record.blank_record(), "conditions": "dry"})
    for n, want in ((1, "can't say: 1 clean lap through it today."),
                    (2, "can't say: 2 clean laps through it today.")):
        o = focus.verdict([item], now, [focus.CornerSample(median=4.4, iqr=0.1, n_laps=n)],
                          store).outcomes[0]
        assert o.blocker == focus.BLOCK_FEW_LAPS, o
        assert focus.outcome_sentence(o).endswith(want), focus.outcome_sentence(o)

    # Coaching: one valid lap, and it had the dropout.
    opps = SimpleNamespace(enough=False, n_laps=0)
    one = SimpleNamespace(valid_lap_ids=lambda: [0], dropout_lap_ids=lambda: [0])
    assert "(1 of its 1 lap had a GPS dropout)" in empty_state_copy(opps, one)[1]
    # …and the "Done it?" hover of an ABSTAINED row (under MIN_CORNER_LAPS), which is on the table.
    thin = coaching.Evidence(n_laps=1, reach_laps=0, reach=coaching.REACH_NEVER, iqr=0.0,
                             abstain=coaching.ABSTAIN_FEW_LAPS)
    tip = _reach_tip(thin, 19)
    assert tip.startswith("0 of the 1 lap that counts here already matched"), tip
    assert "1 of your 19 clean laps counts: on the other 18" in tip, tip
    assert _reach_tip(thin, None).startswith("0 of your 1 clean lap already matched"), tip

    # COASTING: one clean lap and a tie it cannot break.
    place = SimpleNamespace(label="C3", s_per_lap=0.4, tied=True)
    report = SimpleNamespace(n_laps=1, places=[place, SimpleNamespace(label="C5", s_per_lap=0.3,
                                                                      tied=True)],
                             lead_separable=False)
    assert "and this 1 clean lap cannot put them in order." in coast_note(report), \
        coast_note(report)

    # CORNERS / STRAIGHTS hovers: one lap interpolated is "it"; two are "they".
    for n, of, pron in ((4, 5, "so it is left out"), (3, 5, "so they are left out")):
        assert pron in _corner_count_tip(SimpleNamespace(n=n, n_laps=of, cid=2))
        assert pron in _straight_count_tip(n, of, "both ends of this straight")
    assert "on none of the 1 clean lap could" in _corner_count_tip(
        SimpleNamespace(n=0, n_laps=1, cid=2))

    # Session record: a fresh set against a worn one.
    a = {**session_record.blank_record(), "tyre_laps": 30}
    b = {**session_record.blank_record(), "tyre_laps": 1}
    assert "tyre age 30 vs 1 lap" in session_record.comparable(a, b), \
        session_record.comparable(a, b)

    # The CORNERS table on a real StatsView: ONE lap × corner time interpolated, on a layout small
    # enough (three corners or fewer) that only ONE corner can carry the worst-corner mark.
    from test_stats import _fake_view_session

    from studio.stats import CornerReport
    from studio.stats_corners import WORST_LOSS_MARK
    from studio.stats_panel import StatsView
    c1 = CornerReport(cid=1, direction=1, n=37, best_s=2.48, median_s=2.69, sigma_s=0.4,
                      median_loss_s=0.21, apex_best_kmh=73.4, apex_median_kmh=67.4,
                      grip_median=0.72, score=0.08, n_laps=38)
    sess = _fake_view_session()
    sess.corner_report = lambda: [c1]
    sess.phase_report = lambda: None
    view = StatsView(sess)
    note = view.corners.note.text()
    assert ("the other 1 was interpolated between matched points, and is shown muted lap by lap"
            in note), note
    loss = view.corners.table.item(0, 4)
    assert loss.text().startswith(WORST_LOSS_MARK), loss.text()
    assert loss.toolTip().startswith("The most erratic-and-slow corner — ranked by"), \
        loss.toolTip()
    view.deleteLater()
    print("test_the_fixed_copy_reads_right_at_one OK")


if __name__ == "__main__":
    if len(sys.argv) > 1:            # scan another checkout's studio/: python … <studio dir>
        for f in scan_tree(sys.argv[1]):
            print(f"{f['module']}:{f['line']}\t{f['rule']}\t"
                  f"{(f['module'], f['where'], f['count'], f['phrase'])!r}")
        sys.exit(0)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PLURAL COPY TESTS PASSED")
