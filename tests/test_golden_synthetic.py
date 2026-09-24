"""CI half of the Session equivalence gate — a SYNTHETIC golden regression (no big file needed).

The byte-identity gate that guards every Session refactor (F1 god-object decomposition, E2, the
#50 delta-engine dedup, ...) is the pair studio.dev.golden_session_dump.fingerprint (a dense
whole-public-API fingerprint of a Session's state) + studio.dev.golden_compare.walk (leaf-by-leaf
compare at eps 1e-9). But the canonical dump loads the real ~11.9 GB ``~/Desktop/D24`` recording,
so that FULL gate is MANUAL and dev-Desktop-only — it never runs in CI.

This test automates the SAME machinery over the deterministic SYNTHETIC session the studio test
suite already uses (tests/test_session_services._synthetic_session — a bare Session with the
stadium() loop + a seeded g-meter, driving REAL corner detection / driving channels / delta /
session bests / consistency, with NO media file). It fingerprints that session with the real
``fingerprint`` (in ``strict=False`` mode, which guards the pure pacer-``Laps`` passthroughs the
bare session can't serve and records a sentinel for them, while fingerprinting every Python
Session-math leaf in full — see golden_session_dump), across three phases mirroring the real dump:

  * ``base``        — the freshly built synthetic session;
  * ``ref``         — after ``set_reference_session`` (a second synthetic, same track, adopted),
                      exercising the reference / delta baseline paths + the ``invalidate_stats()``
                      seam;
  * ``ref_cleared`` — after ``clear_reference()``; asserted byte-identical to ``base`` (the
                      reference clear must revert the per-lap Δ baseline exactly);
  * ``drift_noise`` — a SEPARATE session (tests/_synthetic.drift_noise_session): three laps with
                      GPS speed noise at the measured sigma, one of them drifted 1.0 % with a
                      corner boundary its spatial match cannot find. The stadium fixture above
                      has 0 % drift and noise-free speed, so the spatial projection and every
                      noise-sensitive detector were dead code to this gate: reverting #228's
                      one-frame warp or #275's coast window left it green. This phase goes red
                      on both (see that fixture's block for why each ingredient is needed).
  * ``drift_median``— the SAME three geometries with the drift on the MEDIAN-time lap
                      (tests/_synthetic.drift_median_session). ``drift_noise`` drifts the SLOWEST
                      lap and the whole coaching model reads the MEDIAN one, so every coaching
                      corner-window projection was the identity in that phase and a coaching-path
                      defect could not move a leaf of it: #289 moved 15 of 168,664 leaves on the
                      D24 0060 pair and 0 synthetic ones. Measured negative control — reverting
                      #289's `_project_window` wiring moves 2 of this phase's 12,101 leaves (a
                      coaching row's `reason.brake_extra_s` by 10.6 ms, 3.909945 -> 3.899391 s,
                      and the `contribution` it feeds) and 0 of `drift_noise`'s 12,115, or of any
                      leaf in the three phases above it.
  * ``drift_band``  — a LADDER across the SUB-GATE DRIFT BAND
                      (tests/_synthetic.drift_band_session). Every lap of the two phases above is
                      either 0.0000 % line-length drift or 0.995 %, so NONE sits in (0 %, 0.5 %] —
                      the band `corners.NORMALIZED_DRIFT_MAX` governed until #300 removed it. That
                      is why removing it moved 0 of all 24,859 leaves while the real boundary
                      residual fell from a median 1.96 m to 0.10 m on D24. Four laps: lap 0 on the
                      reference line, then rungs at 0.118 / 0.289 / 0.460 % drift, each running
                      wide round turn 1 and tight round turn 2 so 2.4-4.2 m of odometer offset
                      survives the near-cancelling length change. Measured negative control —
                      restoring the 0.5 % gate moves 68 of this phase's 15,451 leaves (coaching
                      rows, per-lap corner stats and segment times; max |Δ| 5.84 on an entry
                      speed) and 0 of the five phases above it.
  * ``gopro``       — THE REAL LOADER (B1b; board review RISK-7). Every phase above is a SEEDED
                      Session: no GPMF, no ``pacer.Laps`` crossing interpolation, no valid-lap band,
                      2-4 laps against lap-count gates of 5-8 (`MATRIX_MIN_LAPS`, `TREND_MIN_LAPS`,
                      `MIN_SPLIT_LAPS`, `corners.ANCHOR_MIN_LAPS`), so 116 of their leaves were the
                      placeholder and coaching was fingerprinted as two abstaining rows. This phase
                      writes the synthetic GoPro recording (studio/dev/synth_gopro.py: a HERO13-shaped
                      two-chapter .MP4, GPS9 with noise/glitches/an acquisition period, IMU, the
                      measured clock offsets; fixed seed) into a temp dir and loads it through
                      ``chapters.discover_siblings`` -> ``Session.load``, jailed, then fingerprints it
                      STRICT: 14 valid laps, one across the chapter seam, every gate crossed, ranked
                      coaching rows, a stitched ideal lap — and not one placeholder.
  * ``gopro_sectors``— the same session after the user places two sector lines (mid-straight, via
                      the sidecar path ``apply_timing_lines_latlon``): a real re-segmentation, and the
                      lap table's S-columns, session-best splits, the SPLITS grid and the sector
                      provenance, which a session with no sector line prints none of.

It then compares the whole fingerprint against a COMMITTED baseline
(tests/golden_synthetic_baseline.json, generated on main in the pixi env) via golden_compare.walk
at eps 1e-9 — so ANY drift in a Session-math leaf FAILS the build. This is the CI-runnable
equivalence gate for every future Session refactor.

Scope note: this COMPLEMENTS, it does NOT replace, the manual real-footage gate. The seeded phases
have no pacer ``Laps`` object, so the C++ Laps passthroughs (lap_count, sector geometry,
session_date, lap_rows, ...) fall to the sentinel there and only the two ``gopro`` phases carry
them; the dump over a real recording (``python -m studio.dev.golden_session_dump`` +
``golden_compare``) stays the canonical, eps-0 gate on real receiver noise.

Tolerance: eps 1e-9 (not exact 0) — the synthetic delta/cross-lap math produces last-bit float
noise (e.g. |Δ| ~1e-14 on a self-referential lap); 1e-9 tolerates that while catching any real
regression. Determinism: the seeded fixtures are pure numpy, and the GoPro recording is generated
from a fixed seed (its telemetry bytes are pinned by digest in the baseline, and ffmpeg's picture
reaches no fingerprinted value), so two builds must produce an identical fingerprint — asserted
below before the baseline compare, the GoPro phases byte for byte across two generations.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_golden_synthetic.py
Regenerate the baseline (only after an INTENTIONAL, reviewed Session-math change):
      python tests/test_golden_synthetic.py --write-baseline
It prints the leaf / __unsupported__ / null / NaN counts before vs after and how many leaves moved:
a leaf that became NaN or fell to a placeholder shows there, not only in a diff nobody runs.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

# The fingerprint must not depend on the machine's timezone: SessionStats renders the first and last
# fix as LOCAL wall-clock "HH:MM" (`stats.totals.start_clock`), so a baseline cut in BST read 11:00
# where the CI runner (UTC) read 10:00 — four leaves that differed only by where the test ran.
os.environ["TZ"] = "UTC"
import time  # noqa: E402

time.tzset()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ — for the sibling fixture

from _synthetic import (  # noqa: E402  (the drift + noise fixtures)
    DN_SPEED_SIGMA_MPS,
    drift_band_laps,
    drift_band_session,
    drift_median_laps,
    drift_median_session,
    drift_noise_laps,
    drift_noise_session,
)
from test_session_services import _synthetic_session  # noqa: E402  (the shared stadium fixture)

from studio import chapters, coaching, corners, data_quality  # noqa: E402
from studio import stats as stats_service  # noqa: E402
from studio.dev import golden_compare, golden_session_dump  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.dev._jail import divert_app_support  # noqa: E402
from studio.dev.golden_compare import EPS, census, walk  # noqa: E402
from studio.dev.golden_session_dump import fingerprint  # noqa: E402
from studio.session import Session  # noqa: E402

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_synthetic_baseline.json")

# The real-loader phases' recording: studio/dev/synth_gopro.py's default, with the seed spelled out
# so a change of the generator's default cannot quietly re-cut this gate — it would show up as
# `gopro_telemetry_sha256` first (test_gopro_recording_is_the_baselined_one).
GOPRO_ARGS = {"seed": 20260924, "laps": 14, "chapters": 2}
GOPRO_PHASES = ("gopro", "gopro_sectors")
# `gopro_sectors` places a sector line across the middle of these two straights (indices into the
# generator's `Circuit.straights`): 150 m and 80 m of straight, so every lap crosses each line once,
# at speed, and the app's own start line near the end of the main straight makes three sectors of
# 212 / 370 / 389 m.
GOPRO_SECTOR_STRAIGHTS = (2, 5)
# The recordings this process wrote, each with the TemporaryDirectory it lives in (kept alive here).
_RECORDINGS: dict = {}


def _gopro_recording(video=None):
    """(chapter paths, truth) of the synthetic GoPro recording, written into a TemporaryDirectory
    and found back through `discover_siblings`, as the app opens a chapter. The default recording
    is written ONCE per process (3.4 MB, ~1.7 s of ffmpeg); passing `video` writes another,
    independent one with that picture."""
    if video is None and "default" in _RECORDINGS:
        return _RECORDINGS["default"][1]
    tmp = tempfile.TemporaryDirectory(prefix="golden_gopro_")
    rec = sg.generate(os.path.join(tmp.name, "rec"), video=video, **GOPRO_ARGS)
    paths = chapters.discover_siblings(rec.paths[0])
    assert paths == rec.paths, f"sibling discovery found {paths}, generated {rec.paths}"
    got = (paths, rec.truth)
    _RECORDINGS["default" if video is None else f"other{len(_RECORDINGS)}"] = (tmp, got)
    return got


def _other_picture(path, truth, first_payload, n_payloads, ffmpeg):
    """A DIFFERENT picture for the same telemetry (another colour, so other H.264 bytes): the
    determinism test writes its second recording with it, which also proves no fingerprinted value
    reads the video — CI's ffmpeg need not encode what this Mac's did. Same frame count, rate and
    timescale as `synth_gopro._encode_video`, which is all the loader takes from the picture."""
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=0x6a2d1f:s=64x36:r=30000/1001",
                    "-frames:v", str(n_payloads * sg.FRAMES_PER_PAYLOAD), "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-video_track_timescale", "30000",
                    "-an", "-n", path], check=True, capture_output=True)


def _gopro_session(paths):
    """The recording through the REAL loader. Jailed first: the load reads the track DB, and this
    must never be the owner's (a test file is jailed already; this also covers an importer)."""
    divert_app_support("pacer-golden-gopro-")
    return Session.load(paths)


def _gopro_sector_lines(truth) -> list:
    return [truth.line_at(sum(truth.circuit.straights[j]) / 2.0) for j in GOPRO_SECTOR_STRAIGHTS]


def _place_sectors(s, truth) -> bool:
    """The user places two sector lines, keeping the app's own start line — through the sidecar
    path (`apply_timing_lines_latlon`), which re-segments and marks the timing confirmed."""
    start, _ = s.timing_lines_latlon()
    return s.apply_timing_lines_latlon(start, _gopro_sector_lines(truth), confirmed=True)


def gopro_fingerprint(recording=None) -> dict:
    """The two real-loader phases, STRICT (an accessor that raises fails the gate by name rather
    than leaving a placeholder), plus whether the sector lines took."""
    paths, truth = recording or _gopro_recording()
    s = _gopro_session(paths)
    out = {"gopro": fingerprint(s, strict=True)}
    out["gopro_sectors_applied"] = _place_sectors(s, truth)
    out["gopro_sectors"] = fingerprint(s, strict=True)
    return out


_DIGEST: list = []


def _gopro_telemetry_sha256() -> str:
    """SHA-256 of the recording's GPMF payloads (`synth_gopro.build`, the bytes `generate` writes
    beside the picture). Pinned in the baseline, so a gate that goes red on the GoPro phases says
    first whether the GENERATOR moved (numpy, synth_gopro) or the Session math did."""
    if not _DIGEST:
        _truth, per_chapter = sg.build(**GOPRO_ARGS)
        _DIGEST.append(hashlib.sha256(b"".join(b"".join(ch) for ch in per_chapter)).hexdigest())
    return _DIGEST[0]


def _build():
    """The deterministic synthetic session, with a fixed track name so the fingerprint is stable
    (the bare fixture leaves track_name unset). Same fixture test_session_services /
    test_central_view_realqt drive — reused, not re-derived."""
    s = _synthetic_session()
    s.track_name = "Stadium"
    return s


def _build_drift_noise():
    """The drift + noise session, named like `_build` so its fingerprint is stable too."""
    s = drift_noise_session()
    s.track_name = "Stadium"
    return s


def _build_drift_median():
    """The median-drift session (same geometry, the drift on the lap coaching reads)."""
    s = drift_median_session()
    s.track_name = "Stadium"
    return s


def _build_drift_band():
    """The drift-ladder session (the same reference line, three laps inside the sub-gate band)."""
    s = drift_band_session()
    s.track_name = "Stadium"
    return s


def synthetic_fingerprint(recording=None, *, gopro: bool = True) -> dict:
    """The whole CI fingerprint: the seeded phases (base / ref / ref_cleared, the three drift
    fixtures) and the two real-loader GoPro phases — the CI-runnable analogue of
    golden_session_dump.main()'s multi-phase dump. The seeded phases have no ``reseg``
    (``set_timing_lines`` clears the seeded _cols_cache/_dist_cache that ARE their only data, so a
    re-segment degrades rather than re-derives on a bare session); ``gopro_sectors`` is that phase
    on a real one. `recording` is a `_gopro_recording()` pair (default: this process's)."""
    s = _build()
    result: dict = {}
    result["base"] = fingerprint(s, strict=False)

    # A data-only reference (a second synthetic, same track) — adopted, then cleared.
    ref = _build()
    result["ref_set_reason"] = s.set_reference_session(ref, source_label="ci-ref")
    result["ref"] = fingerprint(s, strict=False)

    s.clear_reference()
    result["ref_cleared"] = fingerprint(s, strict=False)

    # The drift + noise session: the paths the stadium laps cannot reach (see the module docstring).
    result["drift_noise"] = fingerprint(_build_drift_noise(), strict=False)
    # The same geometry with the drift on the MEDIAN lap: the coaching path, which reads that lap
    # and only that lap, and which drift_noise therefore exercises in its identity projection.
    result["drift_median"] = fingerprint(_build_drift_median(), strict=False)
    # The sub-gate drift BAND, as a ladder: the two phases above jump from 0 % drift to 0.995 %,
    # so every lap the removed 0.5 % gate would have kept on the normalized projection is missing
    # from this fingerprint entirely, and a regression in that band moves no leaf of it.
    result["drift_band"] = fingerprint(_build_drift_band(), strict=False)
    if not gopro:   # the seeded phases alone (test_reference_clear_reverts_to_base needs no more)
        return result
    # The real loader, on a recording that laps (see the module docstring).
    result["gopro_telemetry_sha256"] = _gopro_telemetry_sha256()
    result.update(gopro_fingerprint(recording))
    return result


def _compare(a: dict, b: dict) -> tuple[list[str], dict]:
    diffs: list[str] = []
    stats = {"n": 0, "max": 0.0, "max_path": ""}
    walk(a, b, "root", diffs, stats)
    return diffs, stats


def _census_line(tree) -> str:
    c = census(tree)
    return (f"{c['leaves']} leaves, {c['unsupported']} {golden_compare.UNSUPPORTED}, "
            f"{c['null']} null, {c['nan']} NaN")


def _float_leaves(o, keys=()):
    """Key paths to every float leaf of a fingerprint tree, in the order `walk` visits them."""
    if isinstance(o, dict):
        for k in sorted(o):
            yield from _float_leaves(o[k], (*keys, k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _float_leaves(v, (*keys, i))
    elif isinstance(o, float):
        yield keys


def test_nan_leaf_is_a_mismatch():
    """NaN compares False with everything, so the comparator's `|a - b| > EPS` let a leaf that
    TURNED INTO NaN — 0/0, the mean of an empty slice, the commonest numeric regression — pass as
    EQUIVALENT: here, in test_load_pipeline, and in the manual real-footage dump. Put one NaN into
    the committed baseline at leaves spread across every phase and expect exactly that leaf back,
    from either side; NaN on both sides stays a match. Reads the baseline file only."""
    nan = float("nan")
    assert golden_compare.UNSUPPORTED == golden_session_dump._UNSUPPORTED
    for a, b, n_diffs in (({"x": 1.0}, {"x": nan}, 1), ({"x": nan}, {"x": 2.5}, 1),
                          ({"x": [nan, 3]}, {"x": [nan, 3]}, 0)):
        diffs, _ = _compare(a, b)
        assert len(diffs) == n_diffs, f"{a} vs {b}: {diffs}"

    with open(BASELINE) as f:
        baseline = json.load(f)
    # A NaN baked into the baseline is a regression a re-cut accepted: a comparison cannot see it.
    assert census(baseline)["nan"] == 0, f"the baseline carries NaN: {_census_line(baseline)}"
    broken = copy.deepcopy(baseline)
    leaves = list(_float_leaves(broken))
    targets = leaves[::max(1, len(leaves) // 5)]
    for keys in targets:
        *parents, last = keys
        holder = broken
        for k in parents:
            holder = holder[k]
        was, holder[last] = holder[last], nan
        leaf = "root" + "".join(f"[{k}]" if isinstance(k, int) else f".{k}" for k in keys)
        for a, b in ((baseline, broken), (broken, baseline)):
            diffs, _ = _compare(a, b)
            assert len(diffs) == 1 and diffs[0].startswith(leaf + ":"), (
                f"one NaN at {leaf} should be exactly one differing leaf, got {diffs[:3]}")
        diffs, _ = _compare(broken, copy.deepcopy(broken))
        assert not diffs, f"NaN on both sides of {leaf} should match, got {diffs[:3]}"
        holder[last] = was

    # The CLI the manual real-footage gate runs: exit 1, and the NaN is counted in its summary.
    holder[last] = nan
    with tempfile.TemporaryDirectory() as tmp:
        paths = [os.path.join(tmp, "golden.json"), os.path.join(tmp, "candidate.json")]
        for path, tree in zip(paths, (baseline, broken), strict=True):
            with open(path, "w") as f:
                json.dump(tree, f)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                golden_compare.main(paths)
            code = 0
        except SystemExit as e:
            code = e.code
    text = out.getvalue()
    assert code == 1 and "MISMATCH: 1 differing leaves" in text, f"exit {code}:\n{text}"
    assert "NaN leaves: golden 0, candidate 1" in text, text
    print(f"ok NaN: one leaf turned NaN is one MISMATCH either way round at {len(targets)} leaves "
          f"across the phases; NaN on both sides matches; baseline {_census_line(baseline)}")


def test_synthetic_fingerprint_is_deterministic():
    """The fixtures are pure (no clock; the one RNG is seeded), so two independent builds must
    fingerprint identically (well within eps) — a flaky gate is worse than none. The second build
    loads a SECOND GoPro recording, generated afresh into its own directory with a different
    picture: its two phases must then match byte for byte, which also proves no fingerprinted
    value depends on where the files were written or on what ffmpeg encoded."""
    one = synthetic_fingerprint()
    two = synthetic_fingerprint(_gopro_recording(video=_other_picture))
    diffs, stat = _compare(one, two)
    assert not diffs, f"non-deterministic synthetic fingerprint: {diffs[:5]}"
    assert stat["max"] <= EPS, f"two builds drifted by {stat['max']:g} at {stat['max_path']}"
    for phase in GOPRO_PHASES:
        a, b = (json.dumps(fp[phase], sort_keys=True) for fp in (one, two))
        assert a == b, f"{phase}: two generations of the recording are not byte-identical"
    print(f"ok determinism: two builds match ({stat['n']} leaves, max |Δ|={stat['max']:g}); the "
          f"GoPro phases byte-identical across two generations with different pictures")


def test_gopro_recording_is_the_baselined_one():
    """The GoPro phases fingerprint a recording this test GENERATES, so a red gate there has two
    possible causes, and this names the first: the generator's telemetry bytes (synth_gopro, or a
    numpy that rounds differently) are not the ones the baseline was cut on. If this passes and the
    baseline compare fails, the recording is the same and the Session math moved."""
    with open(BASELINE) as f:
        pinned = json.load(f).get("gopro_telemetry_sha256")
    now = _gopro_telemetry_sha256()
    assert pinned == now, (
        f"the synthetic GoPro telemetry changed: sha256 {now} vs the baseline's {pinned}. Every "
        f"leaf of the gopro phases will move with it — this is the GENERATOR, not Session math.")
    print(f"ok recording: GPMF payload sha256 {now[:16]}… matches the baseline's")


def test_gopro_phases_reach_what_the_seeded_phases_cannot():
    """A golden phase is only as good as its fixture (the drift tests above pin theirs), so pin
    what makes the real-loader phases able to fail where the seeded ones cannot — each is a
    property a plausible change to the generator or to the loader would quietly remove:

      1. the REAL loader built it: a `pacer.Laps` segmentation on the GPS9 true clock, two
         chapters, and a valid lap whose window spans the seam between them;
      2. every lap-count gate is crossed, and the value it gates exists: the corners-by-lap grid
         (`MATRIX_MIN_LAPS`), the pace trend (`TREND_MIN_LAPS`), the fast/slow band split
         (`MIN_SPLIT_LAPS`) and the consensus-line anchor (`corners.ANCHOR_MIN_LAPS`);
      3. coaching RANKS corners (the seeded phases' two rows both abstain), the ideal lap is
         stitched from more than one lap and beats the best one;
      4. `gopro_sectors` has three sectors on every valid lap, each split finite and summing to
         its lap time, and a SPLITS grid;
      5. neither phase holds a placeholder — while the seeded phases hold them at exactly the
         leaves the board review named (lap rows, session-best splits, sector σ, the best lap's
         channels, the sector count), which here are numbers."""
    paths, truth = _gopro_recording()
    s = _gopro_session(paths)
    assert hasattr(s.laps, "lap_columns"), "no pacer.Laps behind this session"
    assert s.timing_quality.clock == data_quality.GPS9_TRUECLOCK, s.timing_quality.clock
    cmap = s.chapters
    assert len(cmap.chapters) == 2, [c.path for c in cmap.chapters]
    # A chapter boundary is a footage STAMP, not an event in the picture: the stamp map, no lag
    # (the rule tests/test_media_clock.py::CROSSINGS holds studio/ to).
    seam = s.media_clock.without_gps_lag().to_telemetry(cmap.chapters[0].duration)
    windows = {i: s.lap_window(i) for i in s.valid_lap_ids()}
    across = [i for i, w in windows.items() if w[0] < seam < w[1]]
    assert len(across) == 1, f"no valid lap spans the chapter seam at {seam:.2f} s: {windows}"

    clean = s.consistency_lap_ids()
    gates = {"MATRIX_MIN_LAPS": stats_service.MATRIX_MIN_LAPS,
             "TREND_MIN_LAPS": stats_service.TREND_MIN_LAPS,
             "MIN_SPLIT_LAPS": stats_service.MIN_SPLIT_LAPS, "ANCHOR_MIN_LAPS": corners.ANCHOR_MIN_LAPS}
    assert len(clean) >= max(gates.values()), f"{len(clean)} clean laps vs the gates {gates}"
    assert s.corner_matrix() is not None, "no corners-by-lap grid"
    assert s.stats.pace_trend() is not None, "no pace trend"
    assert s.stats.speed_bands().fast is not None, "no fast/slow split of the speed bands"
    geometry = s.corners.geometry()
    assert geometry is not None and geometry.n_laps >= corners.ANCHOR_MIN_LAPS, geometry

    ops = s.coaching_opportunities()
    ranked = ops.ranked_rows()
    assert ops.enough and ranked and ranked[0].time_lost > 0, (
        f"coaching ranked {len(ranked)} of {len(ops.rows)} rows")
    best = s.best_lap_id()
    assert s.ideal_donor_lap_id() is None and s.ideal_total() < s.lap_time(best), (
        s.ideal_sample(), s.ideal_total(), s.lap_time(best))

    assert _place_sectors(s, truth), "the sector lines left no valid lap"
    assert s.effective_sector_count() == 2, s.effective_sector_count()
    for i in s.valid_lap_ids():
        splits = s.lap_sector_splits(i)
        assert len(splits) == 3 and all(np.isfinite(splits)) and min(splits) > 0, (i, splits)
        assert abs(sum(splits) - s.lap_time(i)) < 1e-3, (i, splits, s.lap_time(i))
    assert golden_session_dump._split_matrix(s) is not None, "no SPLITS grid"

    fp = gopro_fingerprint()
    named = ("lap_rows", "session_best_splits", "sector_sigmas", "lap_channels_best",
             "sector_count")
    with open(BASELINE) as f:
        seeded = json.load(f)["base"]
    for phase in GOPRO_PHASES:
        c = census(fp[phase])
        assert c["unsupported"] == 0 and c["nan"] == 0, f"{phase}: {c}"
    held = [k for k in named if seeded[k] == golden_session_dump._UNSUPPORTED]
    assert held == list(named), f"the seeded base phase now serves {set(named) - set(held)}"
    assert all(fp["gopro_sectors"][k] not in (None, [], 0) for k in named), (
        {k: fp["gopro_sectors"][k] for k in named if fp["gopro_sectors"][k] in (None, [], 0)})
    print(f"ok gopro fixture: {len(clean)} clean laps (gates {max(gates.values())}), lap "
          f"{across[0]} spans the seam, {len(ranked)} ranked coaching rows, ideal "
          f"{s.ideal_total():.3f} s vs best {s.lap_time(best):.3f} s, 3 sectors on every lap, "
          f"{census(fp['gopro'])['leaves']} + {census(fp['gopro_sectors'])['leaves']} leaves, "
          f"no placeholder")


def test_reference_clear_reverts_to_base():
    """clear_reference() must revert the per-lap Δ baseline byte-for-byte — ref_cleared == base
    (the invalidate_stats() seam, pinned here at the fingerprint level)."""
    fp = synthetic_fingerprint(gopro=False)
    diffs, stats = _compare(fp["base"], fp["ref_cleared"])
    assert not diffs, f"ref_cleared drifted from base: {diffs[:5]}"
    assert stats["max"] <= EPS
    print(f"ok revert: ref_cleared == base (max |Δ|={stats['max']:g})")


def test_drift_noise_fixture_reaches_the_paths_it_exists_for():
    """A golden phase is only as good as its fixture, so pin the properties that make this one
    able to fail — each is one a plausible edit to the fixture would quietly remove:

      1. the seeded best lap IS the fastest (the memo is not lying to every best-derived leaf);
      2. exactly one lap drifts past 0.5 % of line length, at 0.9-1.1 % — the drift that made this
         fixture's projection non-trivial back when a 0.5 % gate decided whether to warp at all;
      3. on that lap exactly one interior corner boundary has no spatial match, so its warp
         INTERPOLATES a knot — the case #228 repaired; with every boundary matched, the old
         per-boundary projection and the warp agree and reverting #228 moves nothing;
      4. the GPS speed column carries the measured noise (sample sd within 15 % of
         DN_SPEED_SIGMA_MPS on every lap) — the coast detector differentiates exactly that column;
      5. the ideal lap is not just the best lap (some segment is donated by another lap)."""
    s = _build_drift_noise()
    ids = s.valid_lap_ids()
    times = [s.lap_time(i) for i in ids]
    assert ids[int(np.argmin(times))] == s.best_lap_id() == 0, (times, s.best_lap_id())

    best_total = s.best_lap_total_distance()
    totals = {i: float(s._dist_cache[i][1][-1]) for i in ids}
    drift = {i: corners.line_length_drift(totals[i], best_total) for i in ids}
    over = [i for i in ids if drift[i] > 0.005]
    assert over == [1], f"drift per lap {drift} — exactly lap 1 must be the drifted one"
    assert 0.009 <= drift[1] <= 0.011, f"lap 1 drift {drift[1]:.4%}"

    # Asked of the spatial MATCHER, not of the warp built from it: this pins a property of the
    # fixture, so it must hold whatever a projection does with the miss (reverting #228 changes that,
    # and it is the golden baseline's job, not this test's, to notice).
    interior = [b for c in s.corners.corner_list() for b in (c.enter, c.exit) if 0 < b < best_total]
    ref_cols, lap_cols = s._cols_cache[0], s._cols_cache[1]
    matched = corners._spatial_matches(np.asarray(interior), best_total,
                                       ref_cols[1], ref_cols[2], ref_cols[4],
                                       lap_cols[1], lap_cols[2], lap_cols[4])
    unmatched = [b for b, m in zip(interior, matched, strict=True) if not np.isfinite(m)]
    assert len(unmatched) == 1, (
        f"{len(unmatched)} unmatched interior boundaries on lap 1 (want exactly 1): "
        f"boundaries {interior}, matches {matched.tolist()}")
    assert s.corners.lap_alignment(1, totals[1]) is not None, "lap 1 kept the normalized projection"

    for i, lap in enumerate(drift_noise_laps()):
        sd = float(np.std(lap["cols"][3] - lap["clean_speed"]))
        assert abs(sd - DN_SPEED_SIGMA_MPS) <= 0.15 * DN_SPEED_SIGMA_MPS, f"lap {i} speed sd {sd}"

    # THE COMPOSITE, AND WHAT C5 CHANGED ABOUT IT. This used to assert that some segment was
    # donated by a lap other than the best — "the ideal is not a duplicate of the best lap". It
    # was true here for exactly one reason: lap 1 was the quickest through C1→C2, and C1→C2 is one
    # of the two segments bounded by the unmatched boundary above. The fixture's whole composite
    # advantage was a window nobody matched, which is the defect C5 exists for, in miniature.
    #
    # So the property pinned is now the SHARP one, and it still fails both ways: lap 1 must still
    # be the fastest through that segment on the clock (or the mask below is masking nothing), and
    # it must not donate it. A multi-donor composite over MATCHED cells is covered by the stadium
    # phase — three of this fingerprint's six — whose donors are [0, 1, 0, 1, None] with every
    # cell resolved.
    sb = s.corners.segment_bests()
    seg = next(j for j, lbl in enumerate(sb.labels) if lbl == "C1-C2")
    row = sb.lap_ids.index(1)
    assert not sb.resolved[row][seg], "lap 1's C1→C2 is no longer the interpolated segment"
    assert sb.times[row, seg] == sb.times[:, seg].min(), (
        f"lap 1 is no longer the quickest through C1→C2 ({sb.times[:, seg].tolist()}) — the "
        f"resolution mask would then be excluding a cell that was losing anyway")
    assert sb.donors[seg] not in (None, 1), (
        f"C1→C2 is donated by lap {sb.donors[seg]}, whose window was interpolated")
    assert sb.bests[seg] > sb.times[row, seg], (sb.bests[seg], sb.times[row, seg])
    print(f"ok drift+noise fixture: lap 1 drift {drift[1]:.3%}, unmatched boundary "
          f"{unmatched[0]:.1f} m, its quickest C1→C2 ({sb.times[row, seg]:.3f} s) refused the "
          f"composite in favour of {sb.bests[seg]:.3f} s, segment donors {sb.donors}")


def test_drift_median_fixture_puts_the_drift_where_coaching_reads():
    """The median-drift phase exists because `drift_noise` drifts the SLOWEST lap while the whole
    coaching model reads the MEDIAN one, so pin the properties that make this fixture able to fail
    where that one cannot — each is one a plausible speed tweak would quietly remove:

      1. the lap coaching reads (`coaching.median_lap_id` over the consistency laps) is the
         DRIFTING one, and is not the best lap (whose window projection is the identity by
         definition — projecting the corner basis onto the lap it was built from);
      2. it is the ONLY lap past 0.5 % of line-length drift, at 0.9-1.1 %, and it has a real warp
         (`lap_alignment` is not None) rather than the normalized projection;
      3. exactly one interior corner boundary on it has no spatial match, so its warp INTERPOLATES
         a knot — the case a bare normalized scale cannot reproduce;
      4. the two projections actually SEPARATE: the gated window and the un-gated
         `lap_total/corner_dist_total` scale sit >= 1 m apart at some corner edge. This is the
         magnitude of the defect #289 fixed (6.5 m on the D24 0060 pair) and the reason a coaching
         window defect can move a leaf here;
      5. coaching runs (`enough`) on the median lap and its rows carry the window-sensitive
         evidence (brake/coast extra seconds) that the moved window feeds."""
    s = _build_drift_median()
    ids = s.valid_lap_ids()
    best = s.best_lap_id()
    best_total = s.best_lap_total_distance()
    totals = {i: float(s._dist_cache[i][1][-1]) for i in ids}
    cons = s.consistency_lap_ids()
    med = coaching.median_lap_id(cons, [s.lap_time(i) for i in cons])
    assert med is not None and med != best, (
        f"median lap {med} is the best lap {best} — its window projection is the identity")

    drift = {i: corners.line_length_drift(totals[i], best_total) for i in ids}
    over = [i for i in ids if drift[i] > 0.005]
    assert over == [med], f"drift per lap {drift} — exactly the median lap {med} must be drifted"
    assert 0.009 <= drift[med] <= 0.011, f"median lap drift {drift[med]:.4%}"

    align = s.corners.lap_alignment(med, totals[med])
    assert align is not None, "the median lap kept the normalized projection — nothing to see"

    corner_list, corner_total = s.corners.basis()
    interior = [b for c in corner_list for b in (c.enter, c.exit) if 0 < b < best_total]
    ref_cols, med_cols = s._cols_cache[best], s._cols_cache[med]
    matched = corners._spatial_matches(np.asarray(interior), best_total,
                                       ref_cols[1], ref_cols[2], ref_cols[4],
                                       med_cols[1], med_cols[2], med_cols[4])
    unmatched = [b for b, m in zip(interior, matched, strict=True) if not np.isfinite(m)]
    assert len(unmatched) == 1, (
        f"{len(unmatched)} unmatched interior boundaries on the median lap (want exactly 1): "
        f"boundaries {interior}, matches {matched.tolist()}")

    # The gated window vs the bare normalized scale the un-gated projection used, on the SAME lap.
    traces = (ref_cols[1], ref_cols[2], ref_cols[4], med_cols[1], med_cols[2], med_cols[4])
    frame = [b for c in corner_list for b in (float(c.enter), float(c.exit))]
    scale = totals[med] / corner_total
    gaps = []
    for c in corner_list:
        w0, w1 = coaching._project_window(float(c.enter), float(c.exit), corner_total, totals[med],
                                          traces=traces, frame=frame, alignment=align)
        gaps += [abs(w0 - float(c.enter) * scale), abs(w1 - float(c.exit) * scale)]
    assert max(gaps) >= 1.0, (
        f"gated and normalized coaching windows are only {max(gaps):.3f} m apart — a window "
        f"defect of the size #289 fixed would not move a leaf of this phase")

    opp = s.coaching_opportunities()
    assert opp.enough and opp.median_lap_id == med, (opp.enough, opp.median_lap_id, med)
    assert any(r.reason.brake_extra_s > 0 or r.reason.coast_extra_s > 0 for r in opp.rows), (
        "no coaching row carries brake/coast evidence — the window feeds nothing measurable")

    for i, lap in enumerate(drift_median_laps()):
        sd = float(np.std(lap["cols"][3] - lap["clean_speed"]))
        assert abs(sd - DN_SPEED_SIGMA_MPS) <= 0.15 * DN_SPEED_SIGMA_MPS, f"lap {i} speed sd {sd}"
    print(f"ok median-drift fixture: coaching reads lap {med} at {drift[med]:.3%} drift, unmatched "
          f"boundary {unmatched[0]:.1f} m, window gap {max(gaps):.2f} m")


def test_drift_band_fixture_covers_the_sub_gate_band():
    """The band phase exists because the two phases above jump straight from 0.0000 % drift to
    0.995 %, leaving the (0 %, 0.5 %] band `corners.NORMALIZED_DRIFT_MAX` governed empty — so pin
    the properties that make a LADDER across it able to fail, each one a plausible tweak would
    quietly remove:

      1. the seeded best lap IS the fastest, and it is the UNDRIFTED reference line — the rungs are
         measured against a lap that is not one of them;
      2. every comparison lap sits strictly inside the band (0 < drift <= 0.005) and the rungs are
         SPREAD across it (lowest under 0.15 %, highest over 0.4 %), so a gate reintroduced anywhere
         at or above the lowest rung moves at least one lap of this phase — which a single rung
         could not promise;
      3. every comparison lap has a REAL warp (`lap_alignment` is not None): precisely what the old
         gate switched off for laps like these;
      4. every interior corner boundary MATCHES spatially on every lap, so this phase's warp is
         measured end to end — deliberately unlike `drift_noise` / `drift_median`, whose one
         unmatched boundary makes the warp interpolate a knot;
      5. warp and normalized projection SEPARATE by >= 1 m on every comparison lap. That is what
         makes the gate visible at all: line-length drift is a weak predictor of odometer
         misalignment (r = +0.38 on D24), so a lap that drifts in band but projects identically
         would pin nothing;
      6. the GPS speed column carries the family's measured noise."""
    s = _build_drift_band()
    ids = s.valid_lap_ids()
    times = [s.lap_time(i) for i in ids]
    assert ids[int(np.argmin(times))] == s.best_lap_id() == 0, (times, s.best_lap_id())

    best_total = s.best_lap_total_distance()
    totals = {i: float(s._dist_cache[i][1][-1]) for i in ids}
    drift = {i: corners.line_length_drift(totals[i], best_total) for i in ids}
    assert drift[0] == 0.0, f"the reference lap drifts {drift[0]:.4%} from itself"
    rungs = [drift[i] for i in ids if i != 0]
    assert all(0.0 < d <= 0.005 for d in rungs), (
        f"drift per lap {drift} — every rung must sit inside the (0, 0.5 %] band")
    assert min(rungs) < 0.0015 and max(rungs) > 0.004, (
        f"rungs {[f'{d:.4%}' for d in rungs]} do not span the band — a gate between them would be "
        f"invisible to this phase")

    corner_list, total_ref = s.corners.basis()
    frame = np.array([b for c in corner_list for b in (float(c.enter), float(c.exit))])
    interior = frame[(frame > 0) & (frame < total_ref)]
    ref_cols = s._cols_cache[s.best_lap_id()]
    seps = {}
    for i in (i for i in ids if i != 0):
        align = s.corners.lap_alignment(i, totals[i])
        assert align is not None, f"lap {i} kept the normalized projection — the gate's own case"
        lap_cols = s._cols_cache[i]
        matched = corners._spatial_matches(interior, total_ref, ref_cols[1], ref_cols[2],
                                           ref_cols[4], lap_cols[1], lap_cols[2], lap_cols[4])
        assert np.all(np.isfinite(matched)), (
            f"lap {i} has an unmatched interior boundary ({matched.tolist()}) — this phase's warp "
            f"is meant to be measured at every knot")
        warped = corners.project_boundaries(frame, total_ref, totals[i], frame=frame,
                                            alignment=align)
        seps[i] = float(np.max(np.abs(warped - frame * (totals[i] / total_ref))))
        assert seps[i] >= 1.0, (
            f"lap {i} projects only {seps[i]:.3f} m from the normalized frame — putting it back on "
            f"the normalized projection would barely move a leaf")

    for i, lap in enumerate(drift_band_laps()):
        sd = float(np.std(lap["cols"][3] - lap["clean_speed"]))
        assert abs(sd - DN_SPEED_SIGMA_MPS) <= 0.15 * DN_SPEED_SIGMA_MPS, f"lap {i} speed sd {sd}"
    print("ok drift-band ladder: " + ", ".join(
        f"lap {i} {drift[i]:.3%} drift ({seps[i]:.2f} m off normalized)" for i in seps))


def test_synthetic_fingerprint_matches_baseline():
    """The equivalence gate: the synthetic Session-math fingerprint must match the committed
    baseline within eps 1e-9. Any drift in a corner / driving / delta / consistency / bests leaf
    FAILS CI — an automated Session-refactor guard needing no big file."""
    assert os.path.exists(BASELINE), (
        f"missing baseline {BASELINE} — regenerate with "
        f"`python tests/test_golden_synthetic.py --write-baseline`")
    with open(BASELINE) as f:
        baseline = json.load(f)
    fp = synthetic_fingerprint()
    diffs, stats = _compare(baseline, fp)
    if diffs:
        msg = "\n".join("  " + d for d in diffs[:40])
        raise AssertionError(
            f"synthetic golden MISMATCH: {len(diffs)} differing leaves "
            f"(max |Δ|={stats['max']:g} at {stats['max_path']}):\n{msg}\n"
            f"If this is an INTENTIONAL Session-math change, regenerate the baseline with "
            f"`python tests/test_golden_synthetic.py --write-baseline` and review the diff.")
    print(f"ok baseline: {stats['n']} leaves match within eps {EPS} (max |Δ|={stats['max']:g}); "
          f"{_census_line(fp)}")


def _write_baseline():
    """Re-cut the baseline and say what moved. It used to rewrite silently, so a leaf that had
    turned NaN or fallen to a placeholder could be accepted by a re-cut without anyone seeing it."""
    old = None
    if os.path.exists(BASELINE):
        with open(BASELINE) as f:
            old = json.load(f)
    fp = synthetic_fingerprint()
    with open(BASELINE, "w") as f:
        json.dump(fp, f, sort_keys=True, indent=1)
        f.write("\n")
    print(f"wrote {BASELINE}")
    print(f"  before: {'(none)' if old is None else _census_line(old)}")
    print(f"  after:  {_census_line(fp)}")
    if old is not None:
        # Compared on the OLD tree's keys, so a re-cut that only ADDS leaves reports 0 moved rather
        # than one key mismatch at the root that hides whether anything under it changed.
        kept = _project(fp, old)
        diffs, stats = _compare(old, kept)
        print(f"  {len(diffs)} of the old baseline's leaves moved or vanished (max |Δ|="
              f"{stats['max']:g} at {stats['max_path'] or '-'}); "
              f"{census(fp)['leaves'] - census(kept)['leaves']} leaves are new; showing up to 40:")
        for d in diffs[:40]:
            print("    " + d)


def _project(new, old):
    """`new` cut down to the keys `old` has, recursively — what a re-cut kept of the old tree."""
    if isinstance(new, dict) and isinstance(old, dict):
        return {k: _project(new[k], old[k]) for k in old if k in new}
    if isinstance(new, list) and isinstance(old, list) and len(new) == len(old):
        return [_project(n, o) for n, o in zip(new, old, strict=True)]
    return new


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        _write_baseline()
        sys.exit(0)
    tests = [
        test_nan_leaf_is_a_mismatch,
        test_synthetic_fingerprint_is_deterministic,
        test_reference_clear_reverts_to_base,
        test_drift_noise_fixture_reaches_the_paths_it_exists_for,
        test_drift_median_fixture_puts_the_drift_where_coaching_reads,
        test_drift_band_fixture_covers_the_sub_gate_band,
        test_gopro_recording_is_the_baselined_one,
        test_gopro_phases_reach_what_the_seeded_phases_cannot,
        test_synthetic_fingerprint_matches_baseline,
    ]
    for t in tests:
        t()
    with open(BASELINE) as f:   # the fingerprint matched it leaf for leaf just above
        leaves = census(json.load(f))["leaves"]
    print(f"\nALL {len(tests)} SYNTHETIC-GOLDEN TESTS PASSED ({leaves} fingerprint leaves)")
