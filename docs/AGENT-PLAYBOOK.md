# Agent playbook — how a change is made here

Pacer's implementation is written by LLM coding agents working from briefs, one package at a time.
[AGENTS.md](../AGENTS.md) is their map of the repo: layout, tasks, layering. This page is the
working agreement: how to run things, what counts as evidence, and what "done" means. Every rule
below was paid for by a defect, a wasted day or a near miss. It applies to any contributor, human
or not. The measured stories behind several of them are in [ENGINEERING.md](ENGINEERING.md).

## 1. Hard rules

- **Recordings are read-only inputs.** Before invoking any tool that writes, read its argument
  contract. Never pass a recording's path, or a folder that holds footage, as an output. A dev tool
  whose first positional argument was its output once cost a recording
  ([ENGINEERING.md §8](ENGINEERING.md#8-the-argument-that-was-an-output)). Record the size and
  mtime of any user file a run reads, before and after.
- **The user's app data is out of bounds.** Every CTest registration runs jailed. A probe or
  harness outside `tests/` is not: call `studio/dev/_jail.py`'s `divert_app_support(...)` before
  building a window, or set `PACER_APP_SUPPORT_JAIL=1`. A new harness that boots the real app must
  jail its log too. If user data changes during your run and you cannot show it was you, stop and
  report it with timestamps. Never "fix" it by hand.
- **App code never writes into the repo** (`tests/test_repo_write_safety.py`), and nothing here
  opens a network connection.
- **Generated bindings are regenerated, never edited** (`pixi run gen-bindings`).
- **The machine's guardrails** (a deny list, the sandbox, a command hook) live in untracked
  config. [AGENT-GUARDRAILS.md](AGENT-GUARDRAILS.md) is their reference copy and restore recipe;
  where the two differ, the live rules win.
- **Temporary files come from `TemporaryDirectory()` or `mkdtemp()`, never a fixed name.** Tests
  run four at a time. One test's cleanup deleting another's half-read clip surfaces inside
  `ffprobe`, and reads exactly like an export defect.

## 2. Running things

- **Every gate is its own command, and you read its exit code:** `pixi run build`,
  `pixi run test-fast`, `pixi run golden`, `pixi run lint`, `pixi run fmt-check`,
  `pixi run typecheck`. Add `pixi run test-footage` when a change can move a published figure, a
  render or the map band.
  **Never pipe a gate into `tail` or `head`.** A pipeline returns its last command's status, so a
  failed build followed by `| tail` exits 0 (or use `set -o pipefail`).
- **One test:** `pixi run ctest --test-dir build/Release -R '^test_x$' --output-on-failure`. CTest
  injects the environment. A bare `python tests/test_x.py` needs `QT_QPA_PLATFORM=offscreen`, and
  no `PYTHONPATH`: `pixi run build` deploys the whole bindings package into the env, and a regular
  package outranks the C++ `pacer/` directory. Tests are plain scripts under CTest, and
  many run their tests from an explicit list. `tests/test_layering.py` fails a test that is defined
  but never run. **One test function:** `pixi run python -m pytest tests/test_x.py -k <part>` —
  `tests/conftest.py` jails it; CTest remains the gate.
- **Read a timeout before you hunt a hang.** A `Timeout` far past `--timeout` is the machine
  sleeping (the ctest tasks run under `caffeinate -si`). One exactly at the limit while other work
  is running is contention: check `uptime` and re-run the test alone. Four tests at a time is the
  ceiling, and `-j1` rules an interaction between tests in or out.
- **One ctest at a time per build directory.** Run `pixi run build` after any edit to
  `tests/CMakeLists.txt`, because a broken edit fails only at configure.
- **Load a whole recording.** `Session.load([one_file])` does not expand chapters. Call
  `chapters.discover_siblings` first, or no lap can cross a seam and a seam test tests nothing.
- **Headless means themed.** Use `QT_QPA_PLATFORM=offscreen PACER_NO_MEDIA=1`, and call
  `theme.apply_theme` before the first widget. Without it everything renders in Qt's default light
  palette, which is a harness artefact, not a product bug.
- **Exports depend on the machine.** CI has no VideoToolbox, and a Mac usually does. Pin the
  encoder in any test whose expected value depends on it, and cover both paths. Before pushing, run
  `test_export_video` once with `PACER_FFMPEG` pointing at a wrapper that hides VideoToolbox.
- **A skip is not a pass.** A real-footage check (`footage.<check>`) without its recording is
  reported Skipped by name. Name every one that skipped when you report gates, and call a figure you
  could not re-measure unverified. The mechanics are in [tests/README.md](../tests/README.md).

## 3. Measurement discipline

- **Measure before you build.** A brief's proposed approach is a hypothesis. Briefs, backlogs and
  reviews here have been wrong repeatedly, so verify every premise against `main`. Shipping nothing
  is a valid result when the numbers say so. A refusal is written into
  [refused-2026-09.md](../studio/docs/refused-2026-09.md) as the next free section on your base,
  with the number that decided it.
- **Every fix gets a regression test you watched fail on the unfixed tree,** and the PR quotes the
  failure. Tests have shipped here that passed both ways: an asserted sentence that had gone false,
  a test never listed in its file's runner, a substring match against HTML-escaped text.
- **Fail fast and by name.** A test that can only fail by hanging is indistinguishable from a
  sleeping machine. Assert the failing condition directly.
- **Drive the real thing.** Use the real widget and the real load path. A probe that re-implements
  the rule under test carries a copy of its defect. A stand-in must define every signal its real
  widget has, because a missing one hangs rather than fails. A `StudioWindow.__new__` fixture that
  builds UI must attach `win.library_ctl` and stop any timer it starts.
- **More than one recording, and different ones.** Vary laps, chapters and direction. A whole
  defect class once reproduced on only one recording of a pair.
- **Distributions, not anecdotes.** "Real but zero in practice" is a note, not a defect. When two
  surfaces show one quantity, they agree or say which is which. Pin any state you measure: some
  tests read the developer's stored preferences.
- **A review's diagnosis is a premise.** Measure the proposed fix, not only the defect. A
  reviewer's proposed fix has failed its own measurement here before.
- **Golden gates.** A gate is only as good as its fixture, and a fixture that cannot reach a defect
  stays green through it. So prove a new gate can fail, by reverting a real fix or planting one. A
  new dependency tends to delete a leaf rather than move it: look for an accessor reaching for
  state the fixture never had. A re-cut is its own commit, with a leaf-by-leaf diff, and the moved
  set must be exactly the one you predicted, as the re-cut's family report names it: paste that
  report in the commit message. The session dump never imports `app.py`, so a
  window-level change needs a real `StudioWindow` probe that you have seen fail on a planted defect.

## 4. Delivering

- **One package, one branch (`<id>/<slug>`), one pull request.** Aim for at most about 800 changed
  lines, mechanical moves aside. If it is bigger, ship the first coherent slice and write the rest
  down. Touch as few modules as the change needs, because cost tracks how far a change spreads.
- **Commit after every coherent step, and push early.** Open the pull request as a draft if it is
  not ready. An interrupted session loses only uncommitted work.
- **If `main` moved, merge it in** (no rebase, no force-push) and re-run the gates. Pull requests
  that are each green have broken `main` together, so the merge tree is what counts.
- **Changelog and registration:** a user-visible change adds its own fragment,
  `changes/<branch-slug>.md`, and never edits `CHANGELOG.md`. A new `tests/test_<name>.py` registers
  itself. `tests/CMakeLists.txt` is edited only for its exceptions table, and its jail loop stays
  last.
- **The evidence goes in the pull request:** the measurements, the negative control you watched
  fail, the golden diff if any, each gate's exit code, and every skipped `footage.*` check by name.
  Run the gates locally, then open the pull request. Do not sit waiting for CI.
- **Report briefly.** Give the pull request (or "shipped nothing", and why), the gates, what other
  work must know, and a line headed **"Premises that turned out to be wrong"**, which says "none"
  when there were none.

## 5. Waves, QA, releases and measures

Packages go out in waves: dispatched together, merged, then checked by QA. These rules are the
2026-09-28 review's decisions on that cadence, each with the reason it was adopted.

- **Wave start.** The owner's checkout has `HEAD` and `AGENTS.md` equal to `origin/main`, or it
  is fast-forwarded before anything is dispatched: agents read `AGENTS.md` from that checkout.
- **Wave content.** A fix wave carries P0/P1 findings only, because a QA round's P1 regressions
  came from P2/P3 fixes. At most 8 pull requests; packages that share a surface run in sequence;
  at most 3 heavy lanes (footage, export, full-suite or manual golden runs) at once; at most 2
  findings per package on one surface, so one agent run can finish a package.
- **Hand-off.** At ~100 tool calls an agent commits, pushes, writes "State at hand-off" in the
  pull request body (what is done, what is left, the next command) and stops. There is
  no call-count target: cost is the context each call re-reads, so it is judged by the context
  per merged pull request.
- **Briefs** quote the one QA or review paragraph a package needs, never a report's path: finding
  the paragraph costs every agent several calls.
- **Pre-push**, on a built tree, run these four checks. They fail on a forgotten doc or copy edit,
  not on code, and cost seconds rather than a CI round:

  ```bash
  pixi run ctest --test-dir build/Release -R '^(test_landing_page|test_inline_styles|test_plural_copy|test_layering)$'
  ```

- **QA rounds.** No owner-journey round (a simulated first open or new recording) until the owner
  has used a build: those rounds kept finding the previous wave's own work. After any wave, and
  before the showcase tag, run REG (regressions) plus an EVAL front-door pass, nothing else; a
  JOURNEY-style run comes once per capability release. Report rework (fix PRs whose defect a PR of
  the previous 7 days introduced) as Y-only beside Y+P (partly so), because partial verdicts on few
  PRs carried its rise. Do not use a "stop when every P0/P1 is a regression" rule: it would never
  have fired.
- **Tags** only on (a) the owner's use of the previous loop, (b) a capability an evaluator can
  see, or (c) a P0/P1 on an evaluator's path. At most one per race day, never one per QA wave: a
  release list that moves daily reads as churn. The next tag is the showcase release: five-bullet
  notes, cut after REG and EVAL on the candidate, named by RUL-12 in [DECISIONS.md](DECISIONS.md).
- **Product.** Until the race-day experiment reports, a product pull request needs a defect the
  owner hit or a ruling he made, and holds `studio/*.py` net lines at 0 or fewer: the code had
  grown faster than its use. The first-open freeze runs until his next new recording or
  2026-11-09: [DECISIONS.md](DECISIONS.md#the-first-open-freeze) names what it freezes and allows.
- **Tests.** A fix's test extends the existing test file of the surface it fixes. A new
  `tests/test_<name>.py` is for a surface with none, and the pull request says why: after the
  board review, test lines grew more than twice as fast as product lines.
- **CI** is judged on the median of at least 10 pull-request runs, never one: push runs carry the
  soak, and one day's pull-request runs differed by minutes. The target, a median of at most
  300 s once the truth matrix lands, is paid for by building the Stats view once in
  `test_stats_ideal` (package GATES-3). A pull request adding over 20 s of CI names what it buys.
- **Measures** are set on outcomes, not on counts that drift with the data, and compared only
  across waves of the same type. Pre-register a measure the app cannot satisfy by itself.
- **Decisions** (owner rulings, the defaults applied for him, the freeze window) live in a tracked
  file, [DECISIONS.md](DECISIONS.md), never only in a brief: one once lived in an untracked log.
