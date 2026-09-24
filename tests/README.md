# `tests/` — the suite, the equivalence gates and the real-footage checks

Every test here is a CTest registration in [CMakeLists.txt](CMakeLists.txt); the pixi tasks that
run them, and the measurements behind their levels, exclusions and timeouts, are in
[pyproject.toml](../pyproject.toml). [AGENTS.md](../AGENTS.md) has the everyday commands. Its
timings were each measured once on the M1 Pro dev Mac: `test` 206.0 s with all 14 footage checks
running (2026-09-24, load ~5, no cost data yet — as in CI; 254.1 s the same way on the commit
before COST ordering and the soak split), `test-fast` 142.9 s (2026-09-23, quiet machine; 420 s
serial on the same commit), `test-footage` 139.1 s (2026-09-23, warm page cache;
`footage.test_real_render_quality_levels_if_media` is the slowest, at 67 s) and `test-soak` 146.1 s.

**Adding a test** edits no shared file. Write `tests/test_<name>.py` as a plain script whose
`__main__` runs its tests (nothing runs under pytest; `test_layering` fails a `def test_…` no
runner calls), and put its "why" in the module docstring. CMake registers every `tests/test_*.py`
by itself, as `python tests/<file>.py` with `QT_QPA_PLATFORM=offscreen` and the bindings on
`PYTHONPATH`. Edit [CMakeLists.txt](CMakeLists.txt) only to name the test in its exceptions table
(another environment, or a `COST` so a slow suite starts first), or to add a footage or soak
registration. The per-test notes that file carried until 2026-09-24 are history, kept verbatim in
[registration-notes.md](registration-notes.md).

- **C++ Catch2 (5):** `test_ops`, `test_geometry`, `test_coordinate_system`, `test_laps`,
  `test_gps_source`.
- **Python studio (the rest):** pure-Python and registered with CTest — offscreen Qt where they
  import the studio widget tree: the studio-feature / controller / analytics suites incl.
  `test_scrub_conversion`, `test_lap_timing`, `test_chapters`, `test_gapfill`,
  `test_gps_source_bindings`, `test_ingest_equivalence`, `test_studio_features`, `test_compare`,
  `test_controllers`, `test_validate_wallclock`, `test_gmeter`, `test_gmeter_overlay`,
  `test_session_services`, `test_corners`, `test_driving`, `test_consistency`, `test_coaching`, ….

## The Session equivalence gate (two halves)

Every Session refactor (F1 god-object decomposition, E2, the #50 delta-engine dedup) is held to
WHOLE-public-API numerical equivalence via
[studio/dev/golden_session_dump.py](../studio/dev/golden_session_dump.py) (a dense fingerprint of
a Session's whole public analysis API) + [studio/dev/golden_compare.py](../studio/dev/golden_compare.py)
(leaf-by-leaf compare):
- **MANUAL, full-coverage half** — the dump loads one real recording, the chapter
  `PACER_GOLDEN_MP4` names, by default `MK_18_09_26/GX010067.MP4` (a working-set recording, below),
  and compares at eps 0: 156,659 leaves on that default in ~4 s, 147,184 on
  `Sandown 3h 2026/GX010064.MP4` and 153,014 on `SD_19_09_26/GX010068.MP4` (measured 2026-09-23,
  once Sandown Park became a built-in track; a Sandown dump taken before that is on the auto-fitted
  line and will not compare with one taken after, while the MK default did not move a leaf).
  It is a dev-Desktop-only gate; it does NOT run in CI.
- **CI half** — `test_golden_synthetic` automates the SAME machinery
  (`fingerprint(strict=False)` + `golden_compare.walk`, eps 1e-9) over the deterministic SYNTHETIC
  session (`test_session_services._synthetic_session`: stadium loop + seeded g-meter, REAL
  corner/driving/delta/bests/consistency, no media file), across base/ref/ref_cleared phases, plus a
  `drift_noise` phase over `tests/_synthetic.drift_noise_session` (GPS speed noise at the measured
  sigma, one lap drifted 1 % with an unmatched corner boundary — the stadium laps have neither, so
  an alignment-driven or noise-driven change could not move them) and a `drift_median` phase over
  `tests/_synthetic.drift_median_session` (the same three geometries with the drift on the
  MEDIAN-time lap — coaching reads the median lap, so with the drift on the slowest one every
  coaching corner-window projection was the identity and #289's coaching-alignment fix moved 15
  real leaves and 0 synthetic ones), and a `drift_band` phase over
  `tests/_synthetic.drift_band_session` (a LADDER of three laps at 0.118 / 0.289 / 0.460 %
  line-length drift — the band the removed 0.5 % drift gate governed, which no lap of any other
  fixture sits in, so #300's removal of that gate moved 0 of all 24,859 leaves; restoring it moves
  68 of this phase's 15,451), vs a
  committed baseline (`golden_synthetic_baseline.json`). It runs with no big file, so it
  gates every future Session-math change in CI. Regenerate the baseline only after an intentional,
  reviewed change: `python tests/test_golden_synthetic.py --write-baseline` — it prints the leaf /
  `__unsupported__` / null / NaN counts before vs after and the leaves that moved.

Both halves count a leaf that is NaN on one side only as a difference, and the comparator's summary
line gives each side's NaN-leaf count. NaN compares false with everything, so until 2026-09 a value
that turned into NaN (0/0, the mean of an empty slice) passed the gate as equal.

Run the **manual real-footage gate** around a core-math change, on the **working set**: the
recordings on the dev Desktop the owner chose on 2026-09-23 (T16b) — `Sandown 3h 2026` (`GX0*0064`,
the primary), `SD_19_09_26` (`GX0*0068`), `SD_30_08_26` (`GX0*0065`) and `MK_18_09_26`
(`GX0*0067`, the only anticlockwise one, on the same Daytona Milton Keynes circuit as D24). Each is
~12 GB a chapter, **not committed**, and strictly read-only; CI never sees them and runs the
synthetic gate above instead. The dump defaults to chapter 1 of `MK_18_09_26` (G2): the
recording on D24's own circuit, so it fingerprints the 12 corners the D24 gate did, not Sandown's 7.
To use another, point `PACER_GOLDEN_MP4` at ONE chapter, and use the same one for both dumps of a
comparison. D24 left the dev machine on 2026-09-19 (the owner keeps it on an external drive — never
search for it or mount it). The dump's positional argument is its OUTPUT, and it refuses anything
but a new `.json`; the recording comes only from the variable or its default:

```bash
# export PACER_GOLDEN_MP4="$HOME/Desktop/Sandown 3h 2026/GX010064.MP4"  # optional: the INPUT, read-only
pixi run python -m studio.dev.golden_session_dump /tmp/before.json   # BEFORE the change
# … make the change, then: pixi run build …
pixi run python -m studio.dev.golden_session_dump /tmp/after.json    # AFTER
pixi run python -m studio.dev.golden_compare /tmp/before.json /tmp/after.json   # expect max|Δ| = 0
```

No `PYTHONPATH` is needed above — the dump puts the built `bindings/pacer` on `sys.path` itself.
It used to be needed and was not documented, so the command as written **exited 2 on intact
footage**: from the repo root `import pacer` resolved to the C++ `pacer/` source directory (a
namespace package with no `GPMFSource`), and the tool reported that AttributeError as
"not readable as GPMF … a partial copy, or a file some tool overwrote". A refusal now names only
what it measured — missing, unreadable, not an MP4 container, not parseable as GPMF, or bindings
that are not importable in this run — and never guesses at a cause
(`studio.dev.golden_session_dump.preflight`, held by `test_golden_hermetic.py`).

## The synthetic GoPro recording (real loader, known truth)

[studio/dev/synth_gopro.py](../studio/dev/synth_gopro.py) writes a HERO13-shaped chaptered
recording (`GX019001.MP4`, `GX029001.MP4`) of a fictional ~971 m, 7-corner clockwise circuit in the
open Atlantic: an ffmpeg H.264 video trak plus a `GoPro MET` gpmd trak carrying GPS9 (10 Hz, UTC
clock, DOP/fix, noise, two teleport glitches), ACCL/GYRO (ZXY) and GRAV/CORI (XZY) from one
rigid-body motion, the 0.46 s GPS lag and 27 ppm media clock measured on the owner's cameras. No
person, kart or real place is in it. It returns the `Truth` it was built from: `Truth.lap_times(line)`
times the laps at ANY line (pass the app's own, `Session.timing_lines_latlon()[0]`),
`Truth.line_at(s)` makes one, and `build()` gives the telemetry bytes without writing files.
[test_synth_gopro.py](test_synth_gopro.py) (~9 s) generates it into a temp dir and runs the real
`Session.load` on it — the only CI fixture where the real loader segments laps (hero6 has none).
Measured on the fixed seed: noise-free at a mid-straight line every lap is within **0.41 ms** of
truth; with the default noise at the app's own line, max **23.4 ms**, mean +2.6 ms.

```bash
pixi run python -m studio.dev.synth_gopro --out /path/to/new-dir   # refuses a non-empty dir
```

Knobs: `--seed`, `--laps` (14), `--chapters` (2), `--gps-noise` (1.0; 0 is noise-free),
`--mirror` (anticlockwise); from Python also `gps_lag_s` and `media_ppm`.

## Real-footage checks

Fifteen checks re-measure something on a real recording, and each is its own CTest registration,
`footage.<check>`. Without its recording CTest lists it under *"The following tests did not run: …
(Skipped)"* — a skip, never a pass, and never a failure (CI has no footage at all). **When you
report gates, name every `footage.*` that skipped.** Until 2026-09-19 each of them printed a skip
line inside its file and the file reported `Passed`, so when `~/Desktop/D24` went every real-footage
check in the repo became a green no-op. [_footage.py](_footage.py) has the mechanism;
[test_footage_checks.py](test_footage_checks.py) holds it, including the negative control.

| Variable | Points at | Checks | Default (G2), and why |
|---|---|---|---|
| `PACER_GOLDEN_MP4` | THE recording (any real one) | the golden dump; `footage.test_real_render_smoke_if_ffmpeg_and_media`, `…_real_chaptered_non_first_chapter_render_if_media`, `…_real_render_quality_levels_if_media`; `footage.test_pedal_mode_paints_the_chart_band`, `footage.test_pedal_band_holds_each_braking_zone_whole`; the primary of `footage.test_real_media_pane_b_is_reference_at_lap_start` | `~/Desktop/MK_18_09_26/GX010067.MP4`: chaptered (the chaptered render needs a lap inside chapter 2), D24's own circuit (the dump covers what it covered on D24), and the smallest chaptered recording. The compare proof's primary defaults to `~/Desktop/SD_19_09_26/GX010068.MP4` instead: MK has no second recording of its track |
| `PACER_GOLDEN_REF_MP4` | a second, DIFFERENT recording of the SAME track (a reference from another track is refused before pane B opens) | the reference of `footage.test_real_media_pane_b_is_reference_at_lap_start` | `~/Desktop/Sandown 3h 2026/GX010064.MP4`: SD_19_09 and Sandown 3h stand where D24 0060 and 0062 stood, and the three-chapter reference makes pane B resolve a later chapter |
| `PACER_IDEAL_TABLE_MP4` | comma-separated chapters of ONE recording, re-measured alone (how a new row is written) | `footage.test_the_table_still_matches_the_app` | every row of the ideal-lap table, on exactly the chapter files `tests/test_ideal_sample_table.ROW_RECORDINGS` names, under `~/Desktop` — the whole table in one run (~10 s) |
| `PACER_MEASURED_FIGURES_DIR` | a folder holding the working set's folders | the seven `footage.test_the_*_footage` in `test_measured_figures`; `footage.accuracy_mk` in `test_validate_wallclock` | `~/Desktop`, where `_LAP_SETS` says each table's recordings are: each check loads exactly the lap sets its table names |
| `PACER_TIMING_DIR` | a folder of official timing sheets (lap CSVs `studio/dev/clubspeed.py` writes from a circuit's heat pages) | `footage.accuracy_mk`: docs/ACCURACY.md's row C against `mk-2026-09-18.csv` | the main checkout's gitignored `.claude/reference/timing/`, found from any linked worktree: the sheets list other drivers, so they are never committed and no other machine has one |

The recordings and the reasons live in [studio/dev/footage.py](../studio/dev/footage.py), shared by
the dump and the tests; `_footage.py` does the lookup. The table checks keep their own variables
because their rows are named recordings (and chapter selections sibling discovery cannot express),
not "a recording". A default that is absent is a skip — for a table, if ANY of its recordings is
absent — and **a variable you set that names something absent is a failure.** Until G2
(2026-09-23) every default still named D24, deliberately, so all fourteen skipped until T16b had
re-measured the published tables on the working set; now all of them run and pass on this Mac.

Where they run: `pixi run test-footage` runs just these (by their `footage` CTest label);
`pixi run test` runs them with everything else, one at a time under a shared `RESOURCE_LOCK`;
`pixi run test-fast` sets `PACER_FOOTAGE_DEFAULTS=off` and reports each **Skipped by name**, with a
reason naming `test-footage`, rather than excluding them — an excluded test vanishes from the
report. A footage variable you set still runs its checks there. The recordings are the owner's and strictly read-only: record sizes and mtimes around a run.

```bash
pixi run test-footage                                           # all sixteen, on the working set
PACER_GOLDEN_MP4="$HOME/Desktop/SD_30_08_26/GX010065.MP4" \
  pixi run ctest --test-dir build/Release -R '^footage\.test_real_render' --output-on-failure
```

A new real-footage check goes in its file's `FOOTAGE_CHECKS`, finds its recording through
`_footage.py` (which raises `FootageMissing` rather than printing a skip), and gets an
`add_footage_test(<file> <check>)` line in [CMakeLists.txt](CMakeLists.txt)'s footage block.
`test_footage_checks` fails the build when a declaration and a registration disagree or a declared
check passes without its recording, and a check left in its file's ordinary run fails that run
with `FootageMissing`.

## Soaks

A soak is a probabilistic check too slow for every pull request, kept beside a fast deterministic
guard that does run there. It is its own registration, `soak.<check>` (`<file>.py --soak <check>`,
`add_soak_test` in [CMakeLists.txt](CMakeLists.txt), `LABELS soak`), and it runs only where
`PACER_SOAK=1`: `pixi run test-soak`, and CI on every push to main and every tag. Everywhere else
it is reported *Skipped* by name. There is one: the compare-toggle crash soak in
[test_compare_lifecycle.py](test_compare_lifecycle.py) — 107 s on the dev Mac and 184 s in CI
(2026-09-23/24), for a SIGSEGV whose root cause that file's AST guard catches in 22 ms.
