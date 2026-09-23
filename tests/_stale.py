"""T16: the published tables no footage on this machine can re-measure, and the marks they carry.

WHY THIS EXISTS. Nine tables in the tree were measured on real footage, and each has a check that
re-measures it (tests/test_measured_figures.py, tests/test_ideal_sample_table.py). PR #339 ran
those checks after #335 changed corner matching, and eight of the nine no longer matched the app.
Then D24 and Sandown_09_05_2026 left the owner's machine, so none of them could be re-measured, and
#344 marked them. The rule is one: no figure the app or the docs present may read as current when
it is known stale or cannot be verified.

On 2026-09-23 the owner chose to RE-BASE them on the recordings on his Desktop, the working set
(T16b). Part A re-measured the seven in tests/test_measured_figures.py there; part B re-based
IdealSample's. A table whose footage check re-measured every row on the working set is `CURRENT`:
it names no `GONE` recording and must carry NO mark — a mark left on it would present a current
table as unverifiable — and its quotes drop their date. What it replaced is kept beside it as a
record that still carries its mark. The marks stay on what could not be re-measured.

The same day Q2 made Sandown Park a built-in track, on the owner's own saved start line, so a jailed
check stopped auto-fitting one there. Every table with a Sandown row was re-measured by its footage
check on the built-in line and republished; they stay `CURRENT`, and where a figure's conclusion moved
the publishing docstring says what it read on the loader's line before. Nothing here changed: no
recording came back and none left, so no mark moved.

This module is the vocabulary both test files share. It has no Qt, no pacer and no numpy:

  * `GONE` names the recordings that are no longer available. A recording comes off the list only
    when it is back where the footage checks look for it.
  * `TABLE_MARK` is the mark a TABLE carries in the comment, docstring or doc that publishes it.
    tests/test_measured_figures.py decides which tables need one: any whose rows name a `GONE`
    recording. `STALE` means #339 re-ran the table's check after #335 and it failed. `UNVERIFIED`
    means #339 re-measured it after #335 and it matched, and no later change can be checked.
  * `QUOTE_MARK` is what a SENTENCE quoting one of those tables must say on a surface a reader sees
    without the table beside it: an in-app string, README.md, docs/, studio/README.md, AGENTS.md,
    or the pacer/ and tests/ READMEs that took AGENTS.md's reference sections.
    Developer docs date it with the PR, "before #335". User-facing copy uses "before a September
    2026 change to corner matching", because a PR number means nothing to a driver. A code comment
    or docstring quoting a table is a note to a developer, and the table it names carries the mark.

`presented_unit` answers "would a reader see this quote without the table?" The existing quote
scans find every quote by search. Each scan hands a match to `unmarked` here, which returns a
failure when the quote's unit lacks its mark.
"""
from __future__ import annotations

import ast
import functools
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The footage folders (as test_measured_figures._LAP_SETS names them) that are NOT on the owner's
# machine. The owner moved both to an external drive on or before 2026-09-19 to free disk space;
# they are not searched for there. The working set T16b re-based the tables on (`Sandown 3h 2026`,
# `SD_19_09_26`, `SD_30_08_26` and `MK_18_09_26`, since 2026-09-23) is all still here.
GONE = ("D24", "Sandown_09_05_2026")

STALE = "STALE"
UNVERIFIED = "UNVERIFIED"
# Re-measured on the working set by its footage check (T16b). Not a mark: the status of a table
# that must not carry one.
CURRENT = "CURRENT"

# "⚠ STALE — NOT RE-MEASURABLE (T16)." at the head of the paragraph that marks a table. Any
# decoration around it (a comment's `#`, markdown's `>` or `**`) is ignored.
TABLE_MARK = re.compile(r"⚠ (STALE|UNVERIFIED) — NOT RE-MEASURABLE \(T16\)")

# What a presented sentence quoting a marked table has to carry: it dates itself to before #335.
QUOTE_MARK = re.compile(r"before #335|before a September 2026 change to corner matching", re.I)

# The docs a reader meets without the table beside them. CHANGELOG.md is history, and
# studio/docs/ holds dated design records, one of which is a marked table itself. The pacer/ and
# tests/ READMEs hold what AGENTS.md was trimmed of, so they keep the rule that text had there.
PRESENTED_DOCS = ("README.md", "AGENTS.md", os.path.join("studio", "README.md"),
                  os.path.join("pacer", "README.md"), os.path.join("tests", "README.md"))


def sentence_at(flat: str, start: int, end: int) -> str:
    """The sentence of `flat` (one flattened file) that holds `flat[start:end]`."""
    lo = max((m.end() for m in re.finditer(r"[.!?]\s+", flat[:start])), default=0)
    m = re.search(r"[.!?](?=\s|$)", flat[end:])
    return flat[lo:end + (m.end() if m else len(flat) - end)]


def _normal(s: str) -> str:
    return " ".join(s.split())


@functools.cache
def in_app_strings(rel: str) -> tuple[str, ...]:
    """Every string a studio module can show or write: each str constant that is not a docstring,
    whitespace-normalised. Adjacent literals arrive already joined, the way the parser joins them,
    so a tooltip wrapped over six source lines is one string here. An f-string contributes its
    literal parts, joined around its fields."""
    tree = ast.parse(open(os.path.join(_REPO, rel), encoding="utf-8").read())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            out.append(" {} ".join(v.value for v in node.values
                                    if isinstance(v, ast.Constant) and isinstance(v.value, str)))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings):
            out.append(node.value)
    return tuple(_normal(s) for s in out if s.strip())


def presented_unit(rel: str, fragment: str, sentence: str) -> str | None:
    """The text a reader sees `fragment` in, when they would see it WITHOUT its table: the
    sentence of a presented doc, or the whole in-app string it sits in (a tooltip is read as one
    unit). None for a code comment, a docstring, a test or a dated record."""
    if rel in PRESENTED_DOCS or rel.startswith("docs" + os.sep):
        return sentence
    parts = rel.split(os.sep)
    if rel.endswith(".py") and parts[0] == "studio" and parts[1] not in ("dev", "docs"):
        frag = _normal(fragment)
        return next((s for s in in_app_strings(rel) if frag in s), None)
    return None


def unmarked(rel: str, fragment: str, sentence: str, what: str) -> str | None:
    """A failure line when a presented quote of a marked table lacks `QUOTE_MARK`, else None."""
    unit = presented_unit(rel, fragment, sentence)
    if unit is None or QUOTE_MARK.search(unit):
        return None
    return (f"{rel}: quotes {what} ({fragment!r}) where a reader sees it without the table, and "
            f"does not say it was measured before #335 — the table is marked stale (T16). Unit: "
            f"{unit[:160]!r}…")
