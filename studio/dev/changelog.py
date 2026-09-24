"""Changelog fragments: each PR writes its own file under `changes/`, and the release folds them in.

WHY. CHANGELOG.md was edited by 54 % of September's PRs (board review 2026-09-23, ARCH-2): every
parallel lane appended to the same lines under `[Unreleased]`, which made it the repo's commonest
merge collision, and nothing said stop, so the median entry grew from 2 lines (0.1.0) to 5 (0.2.0)
to 7 (OPS-2). A PR now adds `changes/<branch-slug>.md` (branch `f1/foo` -> `changes/f1-foo.md`),
a file no other PR touches, and the release step folds every fragment into the changelog at once.

A FRAGMENT is a piece of changelog in the changelog's own shape:

    ### Fixed

    - A lift is no longer read as a brake: strings of one-sample blips counted as braking

One or more `### Added` / `### Changed` / `### Fixed` groups of `- ` bullets. A bullet continues
on lines indented by two spaces and is at most 2 lines of at most 100 characters; the long form
belongs in the PR description. End it with `(#PR)` if you know the number. If you do not, the fold
finds it: on `main`, the first-parent commit that added the fragment is "Merge pull request #N".

Every section after 0.2.0 keeps that short shape (`changelog_problems`): only Highlights / Added /
Changed / Fixed groups, entries of at most 3 lines (a fragment's 2 plus room for the PR number the
fold appends) of at most 100 characters, each Added / Changed / Fixed entry ending in `(#N)`, and
an intro of at most 3 lines. tests/test_version.py holds CHANGELOG.md and every fragment to it.

Run from the repo root (`pixi run changelog` is the first line; extra arguments pass through):
    python -m studio.dev.changelog                       dry run: print the section the fold writes
    python -m studio.dev.changelog --write               fold into [Unreleased], delete the fragments
    python -m studio.dev.changelog --write --release 0.3.0 [--date 2026-10-01]
                                                          ...under `## [0.3.0] — date` instead, with
                                                          its compare link and a fresh [Unreleased]
The fold writes nothing unless the result passes `changelog_problems`. Pure stdlib (plus `git`
for the PR lookup), so tests/test_version.py can drive it in a temporary directory.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import os
import re
import subprocess
import sys
import textwrap

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIDTH = 100             # every line of a shaped section or a fragment
ENTRY_LINES = 3         # a changelog entry: a fragment bullet plus the PR number the fold appends
FRAGMENT_LINES = 2      # a fragment bullet
INTRO_LINES = 3         # a section's prose before its first group
GROUPS = ("Highlights", "Added", "Changed", "Fixed")   # in this order, each at most once
FRAGMENT_GROUPS = GROUPS[1:]                           # Highlights are written at release, by hand
SHAPED_AFTER = (0, 2, 0)   # sections newer than this release keep the short shape

_SECTION = re.compile(r"^## \[([^\]]+)\]")
_GROUP = re.compile(r"^### (.*?)\s*$")
_LINK_DEF = re.compile(r"^\[[^\]]+\]:\s*\S")
_PR_REF = re.compile(r"\(#\d+(?:, #\d+)*\)$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_UNRELEASED_LINK = re.compile(r"^\[Unreleased\]:\s*(\S+)/compare/v(\S+)\.\.\.HEAD\s*$")


# ------------------------------------------------------------------------------------ parsing
def _parse(lines, first_lineno):
    """Split a section body or a fragment into (intro, groups, problems).

    intro  = [(lineno, line)] of prose before the first `### ` group;
    groups = [(name, lineno, [(lineno, [line, ...]), ...])], one entry per `- ` bullet with its
             indented continuation lines (a blank line does not end an entry: in Markdown an
             indented paragraph after one still belongs to the list item, so it counts too).
    Link definitions (`[x]: url`) belong to no section and are skipped."""
    intro, groups, problems = [], [], []
    entry = None
    for i, line in enumerate(lines):
        n = first_lineno + i
        if not line.strip() or _LINK_DEF.match(line):
            continue
        m = _GROUP.match(line)
        if m:
            groups.append((m.group(1), n, []))
            entry = None
        elif line.startswith("- "):
            if not groups:
                problems.append(f"{n}: a bullet before any `### ` group")
                continue
            entry = (n, [line])
            groups[-1][2].append(entry)
        elif line[0].isspace():
            if entry is None:
                problems.append(f"{n}: an indented line that belongs to no `- ` entry")
            else:
                entry[1].append(line)
        elif groups:
            problems.append(f"{n}: prose inside `### {groups[-1][0]}` — an entry needs a `- ` bullet")
            entry = None
        else:
            intro.append((n, line))
    return intro, groups, problems


def _shape_problems(groups, allowed, max_lines, need_ref):
    problems, seen = [], []
    for name, n, entries in groups:
        if name not in allowed:
            problems.append(f"{n}: `### {name}` — the groups are {', '.join(allowed)}")
        elif name in seen:
            problems.append(f"{n}: a second `### {name}` group")
        elif seen and allowed.index(name) < allowed.index(seen[-1]):
            problems.append(f"{n}: `### {name}` after `### {seen[-1]}` — keep {', '.join(allowed)}")
        seen.append(name)
        if not entries:
            problems.append(f"{n}: `### {name}` has no entries")
        for m, text in entries:
            if len(text) > max_lines:
                problems.append(f"{m}: an entry of {len(text)} lines (at most {max_lines}): "
                                f"{text[0][:60]!r}… — the long form belongs in the PR description")
            for k, line in enumerate(text):
                if len(line) > WIDTH:
                    problems.append(f"{m + k}: {len(line)} characters (at most {WIDTH})")
            if need_ref and name in FRAGMENT_GROUPS and not _PR_REF.search(text[-1].rstrip()):
                problems.append(f"{m}: an entry that does not end in its PR number, `(#N)`: "
                                f"{text[0][:60]!r}…")
    return problems


def parse_fragment(text, where="fragment"):
    """{group: [[line, ...], ...]} for one `changes/*.md` file; ValueError naming every problem."""
    intro, groups, problems = _parse(text.splitlines(), 1)
    problems += [f"{n}: text before the first `### ` group" for n, _line in intro]
    problems += _shape_problems(groups, FRAGMENT_GROUPS, FRAGMENT_LINES, need_ref=False)
    if not groups:
        problems.append("no `### Added` / `### Changed` / `### Fixed` group")
    if problems:
        raise ValueError(f"{where}:\n  " + "\n  ".join(f"line {p}" for p in problems))
    return {name: [text for _n, text in entries] for name, _n, entries in groups}


def sections(text):
    """[(name, heading_index, end_index)] for every `## [name]` in the changelog, in file order."""
    lines = text.splitlines()
    heads = [(m.group(1), i) for i, line in enumerate(lines) if (m := _SECTION.match(line))]
    return [(name, i, heads[k + 1][1] if k + 1 < len(heads) else len(lines))
            for k, (name, i) in enumerate(heads)]


def is_shaped(name):
    """True for the sections that keep the short shape: [Unreleased] and every release after 0.2.0."""
    if name == "Unreleased":
        return True
    m = _SEMVER.match(name)
    return bool(m) and tuple(int(g) for g in m.groups()) > SHAPED_AFTER


def section_problems(lines, first_lineno=1):
    """Everything wrong with one section body (the lines under its `## ` heading)."""
    intro, groups, problems = _parse(lines, first_lineno)
    if len(intro) > INTRO_LINES:
        problems.append(f"{intro[0][0]}: an intro of {len(intro)} lines (at most {INTRO_LINES})")
    problems += [f"{n}: {len(line)} characters (at most {WIDTH})"
                 for n, line in intro if len(line) > WIDTH]
    return problems + _shape_problems(groups, GROUPS, ENTRY_LINES, need_ref=True)


def changelog_problems(text):
    """Every shape problem in every section after 0.2.0, as `line N: …` strings."""
    lines = text.splitlines()
    out = []
    for name, head, end in sections(text):
        if is_shaped(name):
            out += [f"[{name}] line {p}" for p in section_problems(lines[head + 1:end], head + 2)]
    return out


# ---------------------------------------------------------------------------------- folding
def merged_pr(repo, path):
    """The PR whose merge added `path` to this branch: on `main`, the first-parent commit that
    added a fragment is its "Merge pull request #N from …". None off `main`, or without git."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, "log", "--first-parent", "--diff-filter=A", "--format=%s", "--",
             os.path.relpath(path, repo)], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"Merge pull request #(\d+)\b", out)
    return int(m.group(1)) if m else None


def _entry(lines, pr):
    """One fragment bullet as changelog lines: its PR number appended, re-wrapped to WIDTH."""
    text = " ".join(line.strip() for line in lines)[2:].strip()
    if pr is not None and not _PR_REF.search(text):
        text += f" (#{pr})"
    return textwrap.wrap(text, WIDTH, initial_indent="- ", subsequent_indent="  ",
                         break_long_words=False, break_on_hyphens=False)


def _regroup(body, new):
    """The section body with `new[group]` entries placed at the top of each group (newest first,
    as the file already reads), a missing group created in GROUPS order, blank lines normalised."""
    intro, rest = [], list(body)
    while rest and not _GROUP.match(rest[0]):
        intro.append(rest.pop(0))
    blocks = []                      # [name, [line, ...]] in file order
    for line in rest:
        m = _GROUP.match(line)
        if m:
            blocks.append([m.group(1), []])
        else:
            blocks[-1][1].append(line)
    for name in GROUPS:
        if not new.get(name):
            continue
        block = next((b for b in blocks if b[0] == name), None)
        if block is None:
            after = [k for k, b in enumerate(blocks) if b[0] in GROUPS
                     and GROUPS.index(b[0]) > GROUPS.index(name)]
            block = [name, []]
            blocks.insert(after[0] if after else len(blocks), block)
        block[1] = [line for entry in new[name] for line in entry] + block[1]

    def trim(ls):
        ls = list(ls)
        while ls and not ls[0].strip():
            ls.pop(0)
        while ls and not ls[-1].strip():
            ls.pop()
        return ls

    out = [""] + (trim(intro) + [""] if trim(intro) else [])
    for name, ls in blocks:
        out += [f"### {name}", ""] + trim(ls) + [""]
    return out


def fold(repo=_REPO, release=None, date=None, write=False):
    """Fold every `changes/*.md` into the changelog. Returns (new text, target section, problems).

    Writes CHANGELOG.md and deletes the fragments only with `write=True` AND no problems; a
    malformed fragment raises ValueError before anything is read further."""
    changelog = os.path.join(repo, "CHANGELOG.md")
    with open(changelog, encoding="utf-8") as fh:
        text = fh.read()
    paths = sorted(glob.glob(os.path.join(repo, "changes", "*.md")))
    parsed, errors = [], []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            try:
                groups = parse_fragment(fh.read(), os.path.relpath(path, repo))
            except ValueError as err:
                errors.append(str(err))
                continue
        parsed.append((merged_pr(repo, path), os.path.basename(path), groups))
    if errors:
        raise ValueError("malformed changelog fragment(s), nothing folded:\n" + "\n".join(errors))
    parsed.sort(key=lambda t: (-(t[0] or 0), t[1]))           # newest PR first, as the file reads
    new = {name: [] for name in FRAGMENT_GROUPS}
    for pr, _name, groups in parsed:
        for name, entries in groups.items():
            new[name] += [_entry(lines, pr) for lines in entries]

    lines = text.splitlines()
    heads = {name: (head, end) for name, head, end in sections(text)}
    if "Unreleased" not in heads:
        raise ValueError("CHANGELOG.md has no `## [Unreleased]` section to fold into")
    head, end = heads["Unreleased"]
    while end > head + 1 and (not lines[end - 1].strip() or _LINK_DEF.match(lines[end - 1])):
        end -= 1                     # keep the foot's link definitions (and blanks) outside
    body = _regroup(lines[head + 1:end], new)
    lines[head + 1:end] = body
    target = [lines[head]] + body

    if release is not None:
        if not _SEMVER.match(release):
            raise ValueError(f"--release wants x.y.z, not {release!r}")
        if release in heads:
            raise ValueError(f"CHANGELOG.md already has a `## [{release}]` section")
        date = date or datetime.date.today().isoformat()
        lines[head] = f"## [{release}] — {date}"
        target[0] = lines[head]
        lines[head:head] = ["## [Unreleased]", ""]
        link = next((k for k, line in enumerate(lines) if _UNRELEASED_LINK.match(line)), None)
        if link is None:
            raise ValueError("CHANGELOG.md has no `[Unreleased]: …/compare/vX...HEAD` link to move")
        base, prev = _UNRELEASED_LINK.match(lines[link]).groups()
        lines[link:link + 1] = [f"[Unreleased]: {base}/compare/v{release}...HEAD",
                                f"[{release}]: {base}/compare/v{prev}...v{release}"]

    result = "\n".join(lines) + "\n"
    problems = changelog_problems(result)
    if write and not problems:
        with open(changelog, "w", encoding="utf-8") as fh:
            fh.write(result)
        for path in paths:
            os.remove(path)
    return result, "\n".join(target), problems


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m studio.dev.changelog",
                                 description="Fold changes/*.md into CHANGELOG.md (a dry run "
                                             "unless --write).")
    ap.add_argument("--write", action="store_true",
                    help="write CHANGELOG.md and delete the folded fragments")
    ap.add_argument("--release", metavar="X.Y.Z",
                    help="fold under a new `## [X.Y.Z] — date` heading instead of [Unreleased]")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="the release date (default: today)")
    ap.add_argument("--repo", default=_REPO, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        _result, target, problems = fold(args.repo, args.release, args.date, args.write)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 2
    n = len(glob.glob(os.path.join(args.repo, "changes", "*.md")))
    print(target)
    for p in problems:
        print(f"PROBLEM {p}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s): nothing written.", file=sys.stderr)
        return 1
    print("\n" + (f"Folded; {n} fragment(s) left in changes/." if args.write else
                  f"Dry run: {n} fragment(s) in changes/, nothing written (--write folds them)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
