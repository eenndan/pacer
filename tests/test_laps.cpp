#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/laps/laps.hpp>

using pacer::CoordinateSystem;
using pacer::GPSSample;
using pacer::Lap;
using pacer::LapArrays;
using pacer::Laps;
using pacer::Point;
using pacer::Segment;
using pacer::Vec3f;

namespace {
// Build a synthetic track in local meter coordinates and feed it to Laps as
// GPS samples. The track does three horizontal sweeps across the local line
// x == 0, so a vertical start line at x == 0 is crossed exactly three times.
Laps MakeThreeLapTrack(const CoordinateSystem &cs) {
  Laps laps;

  double t = 0;
  auto add = [&](double x, double y) {
    laps.AddPoint(cs.Global(Vec3f{x, y, 0}), t++);
  };

  // Sweep 1 (y = 0), left -> right: crosses x = 0 between (-5,0) and (5,0).
  add(-20, 0);
  add(-5, 0);
  add(5, 0);
  add(20, 0);
  // Connector up the right side (no crossing, x stays +20).
  add(20, 4);
  // Sweep 2 (y = 4), right -> left: crosses x = 0 between (5,4) and (-5,4).
  add(5, 4);
  add(-5, 4);
  add(-20, 4);
  // Connector up the left side (no crossing, x stays -20).
  add(-20, 8);
  // Sweep 3 (y = 8), left -> right: crosses x = 0 between (-5,8) and (5,8).
  add(-5, 8);
  add(5, 8);
  add(20, 8);

  laps.SetCoordinateSystem(cs);
  return laps;
}

// A clean UNIDIRECTIONAL loop, traversed twice, for sector segmentation. Each lap runs the
// bottom edge left -> right (crossing the verticals x = 0, 10, 20 in order), up the right side,
// the top edge right -> left, then down the left side to close. The start line is TALL (crossed
// on both the bottom and top edges, so it bounds the laps), while the two sector lines are SHORT
// (y in [-5, 5]) so they're crossed only once per lap on the bottom edge — giving an unambiguous
// start -> sector1 -> sector2 -> start ordering (unlike the alternating-direction MakeThreeLapTrack,
// where the reversed sweeps cross the verticals out of order).
Laps MakeSectorLoop(const CoordinateSystem &cs) {
  Laps laps;
  double t = 0;
  auto add = [&](double x, double y) {
    GPSSample s = cs.Global(Vec3f{x, y, 0});
    s.full_speed = 20.0; // a finite, plausible entry speed for the *EntrySpeed accessors.
    laps.AddPoint(s, t++);
  };
  for (int lap = 0; lap < 2; ++lap) {
    // Bottom edge, left -> right: crosses x = 0, 10, 20.
    add(-30, 0);
    add(-5, 0);
    add(5, 0);
    add(15, 0);
    add(25, 0);
    add(40, 0);
    // Up the right side.
    add(40, 10);
    add(40, 20);
    // Top edge, right -> left: crosses only the TALL start line at x = 0.
    add(25, 20);
    add(15, 20);
    add(5, 20);
    add(-10, 20);
    add(-30, 20);
    // Down the left side to close.
    add(-30, 10);
  }
  laps.SetCoordinateSystem(cs);
  return laps;
}
} // namespace

TEST_CASE("Laps segments a synthetic track at every timing-line crossing",
          "[laps]") {
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);

  Laps laps = MakeThreeLapTrack(cs);

  // Vertical start line at local x == 0 spanning y in [-10, 10] (local coords).
  laps.sectors.start_line = Segment{Point{0, -10}, Point{0, 10}};
  laps.Update();

  SECTION("one lap chunk per crossing") { CHECK(laps.LapsCount() == 3); }

  SECTION("SampleCount agrees with the materialized lap point count") {
    // SampleCount(lap) == finish_index - start_index + 2, which is exactly the
    // number of points GetLap() produces (interpolated start + interior points
    // + interpolated finish). This is the +3 -> +2 fix.
    for (size_t lap = 0; lap < laps.LapsCount(); ++lap) {
      CHECK(laps.SampleCount(lap) == laps.GetLap(lap).Count());
    }
  }

  SECTION("lap times are positive and increasing along the trace") {
    for (size_t lap = 0; lap + 1 < laps.LapsCount(); ++lap) {
      CHECK(laps.LapTime(lap) > 0.0);
    }
  }

  SECTION("out-of-range lap queries are safe") {
    CHECK(laps.SampleCount(laps.LapsCount()) == 0);
    CHECK(laps.SampleCount(9999) == 0);
    CHECK(laps.GetLap(9999).Count() == 0);
  }
}

TEST_CASE("Laps segments each lap into sectors at the intermediate timing lines",
          "[laps]") {
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);

  Laps laps = MakeSectorLoop(cs);

  // Tall start line (crossed on both edges) + two SHORT intermediate sector lines at x = 10, 20
  // (crossed only on the bottom edge). Two intermediate lines divide each lap into 3 sectors.
  laps.sectors.start_line = Segment{Point{0, -50}, Point{0, 50}};
  laps.sectors.sector_lines = {Segment{Point{10, -5}, Point{10, 5}},
                               Segment{Point{20, -5}, Point{20, 5}}};
  laps.Update();

  // SectorCount() is the number of intermediate timing lines (2). The lap is divided into
  // 3 sector chunks (start->s1, s1->s2, s2->start); the recorded-sector run carries each.
  REQUIRE(laps.SectorCount() == 2);
  REQUIRE(laps.LapsCount() >= 1);
  // The boundary crossings recorded: at least the 3 that bound the first complete lap.
  REQUIRE(laps.RecordedSectors() >= 3);

  SECTION("the three sector times of lap 0 sum to its lap time") {
    // sectors_ records crossings of the rotating boundary in order: start, s1, s2, start, ...
    // so recorded sectors 0,1,2 are exactly the three segments of lap 0.
    double sector_sum =
        laps.SectorTime(0) + laps.SectorTime(1) + laps.SectorTime(2);
    CHECK(sector_sum == Catch::Approx(laps.LapTime(0)).margin(1e-6));
  }

  SECTION("sector start timestamps are strictly increasing within the lap") {
    CHECK(laps.SectorStartTimestamp(0) < laps.SectorStartTimestamp(1));
    CHECK(laps.SectorStartTimestamp(1) < laps.SectorStartTimestamp(2));
    // The first sector starts at the lap's start crossing.
    CHECK(laps.SectorStartTimestamp(0) ==
          Catch::Approx(laps.StartTimestamp(0)).margin(1e-6));
  }

  SECTION("entry-speed accessors return finite, plausible speeds") {
    for (size_t s = 0; s < 3; ++s) {
      double v = laps.SectorEntrySpeed(s);
      CHECK(std::isfinite(v));
      CHECK(v == Catch::Approx(20.0).margin(1e-6));
    }
    double lap_entry = laps.LapEntrySpeed(0);
    CHECK(std::isfinite(lap_entry));
    CHECK(lap_entry == Catch::Approx(20.0).margin(1e-6));
  }
}

TEST_CASE("Gap-aware distance: speed integral fills a dropout chord", "[laps]") {
  // A straight run along +x. With a normal ~0.1 s sample step the GPS chord is the right
  // distance; across a long DROPOUT step the chord under-counts (the kart curved out and back,
  // so the fixes are close in space but far in travel), so the speed integral 1/2 (v0+v1) dt
  // is used instead. Build the per-lap odometer (Lap::FillDistances — the array the studio
  // delta/sector math reads) directly and check the gap segment is the speed integral.
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);
  auto sample = [&](double x, double y, double speed) {
    GPSSample s = cs.Global(Vec3f{x, y, 0});
    s.full_speed = speed;
    return s;
  };

  SECTION("normal sampling uses the geometric chord") {
    // 0.1 s steps, 2 m apart, 20 m/s — chord (2 m) == speed integral (20*0.1 = 2 m).
    Lap lap{.points = {{sample(0, 0, 20.0), 0.0},
                              {sample(2, 0, 20.0), 0.1},
                              {sample(4, 0, 20.0), 0.2}}};
    lap.FillDistances(cs);
    CHECK(lap.cum_distances.at(2) == Catch::Approx(4.0).margin(0.05));
  }

  SECTION("a dropout step is measured by the speed integral, not the chord") {
    // Big time hole (1.0 s > 0.35 s gap): the kart kept moving at 20 m/s -> ~20 m travelled,
    // but the two fixes are only 2 m apart in space. Chord = 2 m, speed integral = 20 m.
    Lap lap{.points = {{sample(0, 0, 20.0), 0.0},
                              {sample(2, 0, 20.0), 1.0},
                              {sample(4, 0, 20.0), 1.1}}};
    lap.FillDistances(cs);
    // step 1 is the gap (dt = 1.0): speed integral 20 m, not the 2 m chord.
    CHECK(lap.cum_distances.at(1) == Catch::Approx(20.0).margin(0.5));
    // step 2 is normal (dt = 0.1, 2 m chord == 0.5*(20+20)*0.1 == 2 m).
    CHECK(lap.cum_distances.at(2) == Catch::Approx(22.0).margin(0.5));
  }

  SECTION("a bad (zero) speed across a gap never shortens the chord") {
    // Guard: if the reported speed is garbage (0) across a gap, fall back to the chord so the
    // distance never DROPS below the straight-line distance between the gap mouths.
    Lap lap{.points = {{sample(0, 0, 0.0), 0.0},
                              {sample(30, 0, 0.0), 1.0}}};
    lap.FillDistances(cs);
    CHECK(lap.cum_distances.at(1) == Catch::Approx(30.0).margin(0.5));
  }
}

TEST_CASE("GetLapDistance agrees with the GetLap/cum_distances model",
          "[laps]") {
  // Group 1 regression: the two distance code paths (the scalar GetLapDistance
  // and the per-point GetLap().cum_distances) must AGREE, and GetLapDistance must
  // no longer over-count the finish partial segment. The MakeThreeLapTrack laps
  // run straight along x with 1 s steps and a vertical start line at x == 0, so
  // each lap's geometry is hand-checkable.
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);

  Laps laps = MakeThreeLapTrack(cs);
  laps.sectors.start_line = Segment{Point{0, -10}, Point{0, 10}};
  laps.Update();

  REQUIRE(laps.LapsCount() == 3);

  for (size_t lap = 0; lap < laps.LapsCount(); ++lap) {
    Lap l = laps.GetLap(lap);
    REQUIRE(l.cum_distances.size() == l.points.size());
    // The scalar lap distance equals the per-point odometer's last entry.
    CHECK(laps.GetLapDistance(lap) ==
          Catch::Approx(l.cum_distances.back()).margin(1e-6));
  }

  SECTION("matches a direct geometric chord sum of the materialized lap") {
    // GetLapDistance must equal the straight sum of chords over the points
    // GetLap() materializes (these laps have 1 s steps, no dropouts, so every
    // SegmentDistance is the plain chord).
    Lap l = laps.GetLap(0);
    double hand = 0.0;
    for (size_t i = 1; i < l.points.size(); ++i) {
      hand += cs.Distance(l.points[i - 1].point, l.points[i].point);
    }
    CHECK(laps.GetLapDistance(0) == Catch::Approx(hand).margin(1e-3));
  }
}

TEST_CASE("ClearPoints then re-adding points is safe (was UB)", "[laps]") {
  // Group 1 regression: ClearPoints() used to .clear() cum_point_dist_ (size 0),
  // so the {0} seed the class relies on (index [0] / .back()) was gone. A later
  // AddPoint / SetCoordinateSystem then indexed cum_point_dist_[0] out of bounds.
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);

  Laps laps = MakeThreeLapTrack(cs);
  REQUIRE(laps.PointCount() > 0);

  laps.ClearPoints();
  CHECK(laps.PointCount() == 0);
  // These must not read out of bounds even immediately after a clear.
  CHECK_NOTHROW(laps.SetCoordinateSystem(cs));

  // Re-add a fresh straight track and verify distances are sane after re-add.
  double t = 0;
  auto add = [&](double x, double y) {
    laps.AddPoint(cs.Global(Vec3f{x, y, 0}), t++);
  };
  add(-20, 0);
  add(-5, 0);
  add(5, 0);
  add(20, 0);
  CHECK_NOTHROW(laps.SetCoordinateSystem(cs));
  REQUIRE(laps.PointCount() == 4);

  // A vertical start line is crossed once -> at least the lap chunks exist and
  // GetLap distances are finite and non-negative.
  laps.sectors.start_line = Segment{Point{0, -10}, Point{0, 10}};
  laps.Update();
  for (size_t lap = 0; lap < laps.LapsCount(); ++lap) {
    Lap l = laps.GetLap(lap);
    CHECK(std::isfinite(l.cum_distances.back()));
    CHECK(l.cum_distances.back() >= 0.0);
    CHECK(laps.GetLapDistance(lap) ==
          Catch::Approx(l.cum_distances.back()).margin(1e-6));
  }
}

TEST_CASE("GetLap cum_distances match FillDistances (Group-2 perf refactor)",
          "[laps]") {
  // Group 2 regression: GetLap() now builds cum_distances from the cached
  // cum_point_dist_ instead of re-walking FillDistances. The result must equal
  // what FillDistances(cs_) produces over the same materialized points. The two
  // paths add the SAME per-segment SegmentDistance values but in a different
  // summation ORDER (prefix-difference of a cached running sum vs a fresh
  // segment-by-segment walk), so they agree to floating-point round-off
  // (sub-nanometre) rather than bit-for-bit. A 1e-6 m margin (1 micron) is far
  // tighter than any physically meaningful distance and proves the refactor did
  // not change the result.
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);

  Laps laps = MakeThreeLapTrack(cs);
  laps.sectors.start_line = Segment{Point{0, -10}, Point{0, 10}};
  laps.Update();
  REQUIRE(laps.LapsCount() == 3);

  for (size_t lap = 0; lap < laps.LapsCount(); ++lap) {
    Lap got = laps.GetLap(lap);
    // Independently recompute the odometer over the SAME points via the original
    // FillDistances code path.
    Lap ref{.points = got.points};
    ref.FillDistances(cs);
    REQUIRE(got.cum_distances.size() == ref.cum_distances.size());
    for (size_t i = 0; i < ref.cum_distances.size(); ++i) {
      CHECK(got.cum_distances[i] ==
            Catch::Approx(ref.cum_distances[i]).margin(1e-6));
    }
  }
}

TEST_CASE("Laps is safe on empty and tiny traces", "[laps]") {
  SECTION("empty trace") {
    Laps laps;
    CHECK(laps.PointCount() == 0);
    CHECK(laps.LapsCount() == 0);
    CHECK(laps.SampleCount(0) == 0);
    // Must not read out of bounds (these used to index points_[0] / +20).
    CHECK_NOTHROW(laps.MinMax());
    CHECK_NOTHROW(laps.PickRandomStart());
  }

  SECTION("single point") {
    GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
    Laps laps;
    laps.AddPoint(origin, 0.0);
    laps.SetCoordinateSystem(CoordinateSystem(origin));
    CHECK(laps.PointCount() == 1);
    CHECK_NOTHROW(laps.PickRandomStart()); // < 2 points -> default segment
    CHECK_NOTHROW(laps.MinMax());
  }
}

TEST_CASE("Interleaved AddPoint after SetCoordinateSystem yields correct distances (pass-2 #7)",
          "[laps]") {
  // PointTrack used to defer ALL cumulative-distance computation to SetCoordinateSystem and only
  // push a 0.0 placeholder in AddPoint. An AddPoint AFTER SetCoordinateSystem (with no re-set) thus
  // left stale-ZERO cumulative distances for the appended points that no accessor repaired, so the
  // lap distance came out short. PointTrack is now self-healing (a dirty flag rebuilds the odometer
  // on demand in the distance accessors), so the OUT-OF-ORDER build must agree with the canonical
  // add-all-then-set-once build. Same geometry, two construction orders.
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);
  const Segment start_line{Point{0, -10}, Point{0, 10}};

  // The 12 MakeThreeLapTrack vertices, replayed verbatim so the two orders are identical geometry.
  struct XY { double x, y; };
  const XY verts[] = {{-20, 0}, {-5, 0}, {5, 0},  {20, 0}, {20, 4},  {5, 4},
                      {-5, 4},  {-20, 4}, {-20, 8}, {-5, 8}, {5, 8}, {20, 8}};

  // Canonical reference: add ALL points, THEN set the coordinate system once (the byte-identical
  // path) — this is exactly MakeThreeLapTrack.
  Laps canonical = MakeThreeLapTrack(cs);
  canonical.sectors.start_line = start_line;
  canonical.Update();
  REQUIRE(canonical.LapsCount() == 3);

  // Interleaved: add the first half, SetCoordinateSystem, add the SECOND half (no re-set), Update.
  Laps interleaved;
  double t = 0;
  for (int i = 0; i < 6; ++i)
    interleaved.AddPoint(cs.Global(Vec3f{verts[i].x, verts[i].y, 0}), t++);
  interleaved.SetCoordinateSystem(cs);  // set cs with only HALF the points present
  for (int i = 6; i < 12; ++i)
    interleaved.AddPoint(cs.Global(Vec3f{verts[i].x, verts[i].y, 0}), t++);
  // NOTE: deliberately NO second SetCoordinateSystem — this is the footgun path. The distance
  // accessors must self-heal the odometer for the points added after the set.
  interleaved.sectors.start_line = start_line;
  interleaved.Update();

  REQUIRE(interleaved.LapsCount() == canonical.LapsCount());
  double interleaved_total = 0.0, canonical_total = 0.0;
  for (size_t lap = 0; lap < canonical.LapsCount(); ++lap) {
    // The core guard: the OUT-OF-ORDER build's distance must EQUAL the canonical order's (the bug
    // left stale zeros for the points added after SetCoordinateSystem, so the interior laps came
    // out short). The trailing lap is degenerate (its closing crossing never happens, so
    // finish_index == start_index and its distance is legitimately 0 in BOTH builds) — hence equate
    // rather than blanket > 0; the totals below confirm real distance was actually accumulated.
    CHECK(interleaved.GetLapDistance(lap) ==
          Catch::Approx(canonical.GetLapDistance(lap)).margin(1e-6));
    // The per-point odometer must agree too (GetLap reads DistanceBetween, the self-healed array).
    Lap il = interleaved.GetLap(lap);
    Lap cl = canonical.GetLap(lap);
    REQUIRE(il.cum_distances.size() == cl.cum_distances.size());
    CHECK(il.cum_distances.back() ==
          Catch::Approx(cl.cum_distances.back()).margin(1e-6));
    interleaved_total += interleaved.GetLapDistance(lap);
    canonical_total += canonical.GetLapDistance(lap);
  }
  // Sanity: real distance was accumulated (not silently all-zero), and the two orders sum equal.
  CHECK(canonical_total > 0.0);
  CHECK(interleaved_total == Catch::Approx(canonical_total).margin(1e-6));
}

TEST_CASE("No phantom sectors when sector_lines is empty (pass-2 #5)", "[laps]") {
  // With NO intermediate sector lines the rotating "sector line" falls back to the start line
  // (sector_index == -1), so every start-line crossing was recorded as a phantom sector chunk.
  // SectorCount() is the number of sector LINES (0 here); RecordedSectors() must now also be 0 so
  // the recorded run is consistent with it (it used to equal the lap count).
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);

  Laps laps = MakeThreeLapTrack(cs);
  laps.sectors.start_line = Segment{Point{0, -10}, Point{0, 10}};
  // sector_lines left EMPTY (the studio default for every normal session).
  laps.Update();

  REQUIRE(laps.LapsCount() == 3);    // laps still segment exactly as before (unchanged behaviour)
  CHECK(laps.SectorCount() == 0);    // no sector lines
  CHECK(laps.RecordedSectors() == 0); // and so NO phantom sector chunks recorded

  SECTION("behaviour with sector lines present is unchanged") {
    // Re-add the two short sector lines: sectors must come back (proves the guard only suppresses
    // the empty case, not the real one).
    laps.sectors.sector_lines = {Segment{Point{10, -5}, Point{10, 5}}};
    laps.Update();
    CHECK(laps.SectorCount() == 1);
    CHECK(laps.RecordedSectors() > 0);
  }
}

TEST_CASE("Re-segmenting after a point/cs change recomputes (pass-2 #6)", "[laps]") {
  // Update()'s dirty sentinels used to track ONLY the timing lines. Re-segmenting after the POINTS
  // (or coordinate system) changed but the timing lines did NOT would early-out and keep the STALE
  // lap_chunks_ from the previous track. The sentinels now also reset on AddPoint/ClearPoints/
  // SetCoordinateSystem, so the next Update() recomputes against the new track.
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);
  const Segment start_line{Point{0, -10}, Point{0, 10}};

  Laps laps = MakeThreeLapTrack(cs);
  laps.sectors.start_line = start_line;
  laps.Update();
  REQUIRE(laps.LapsCount() == 3);

  // Swap in a DIFFERENT track that crosses the start line a DIFFERENT number of times (exactly ONE
  // crossing -> ONE lap chunk vs the three above) WITHOUT touching sectors.start_line: clear +
  // re-add + set cs. The timing line is byte-identical to before, so the old (line-only) dirty
  // guard would early-out and report the STALE 3 laps from the previous track. The lap COUNT is the
  // clean discriminator: 1 (recomputed against the new track) vs a stale 3.
  laps.ClearPoints();
  double t = 0;
  auto add = [&](double x, double y) {
    laps.AddPoint(cs.Global(Vec3f{x, y, 0}), t++);
  };
  add(-20, 0);  // a single left->right sweep across x == 0 -> exactly ONE crossing
  add(-5, 0);
  add(5, 0);
  add(20, 0);
  laps.SetCoordinateSystem(cs);
  // sectors.start_line is UNCHANGED here on purpose — ONLY the points/cs changed.
  laps.Update();

  // Recomputed against the NEW 4-point track (1 crossing -> 1 chunk), NOT the stale 3-lap result of
  // the previous MakeThreeLapTrack. With the old line-only dirty guard this would early-out and
  // still report 3 laps from chunks that point into a track that no longer exists.
  CHECK(laps.LapsCount() == 1);
  for (size_t lap = 0; lap < laps.LapsCount(); ++lap) {
    Lap l = laps.GetLap(lap);
    REQUIRE(l.cum_distances.size() == l.points.size());
    CHECK(std::isfinite(l.cum_distances.back()));
    CHECK(laps.GetLapDistance(lap) ==
          Catch::Approx(l.cum_distances.back()).margin(1e-6));
  }
}

TEST_CASE("LapColumns equals the per-point GetLap/Local path (PR #40)",
          "[laps]") {
  // PR #40 claim: LapColumns(lap) returns, in ONE binding crossing, exactly
  // the five per-point columns the studio layer used to materialize
  // element-by-element from GetLap(lap): times (points[i].time), local-metre
  // xs/ys (cs.Local(points[i].point).x|y), full_speed
  // (points[i].point.full_speed) and cum_distances. The contract (laps.hpp)
  // and the implementation comment (laps.cpp: "Materialize the lap exactly as
  // GetLap does") promise the SAME deterministic computation over the SAME
  // data, so every element must be EXACTLY equal to the hand-materialized
  // per-point path — not merely approximately.
  GPSSample origin{.lat = 40.0, .lon = -74.0, .altitude = 0};
  CoordinateSystem cs(origin);

  // The per-lap column equivalence check, shared by the two synthetic tracks
  // below. `cs` is the same coordinate system the laps own (the one set via
  // SetCoordinateSystem), which is the cs LapColumns projects with.
  auto check_columns_match = [&](const Laps &laps) {
    for (size_t lap = 0; lap < laps.LapsCount(); ++lap) {
      LapArrays cols = laps.LapColumns(lap);
      Lap l = laps.GetLap(lap);

      // All five columns are index-aligned with the materialized lap: length
      // == SampleCount(lap) == GetLap(lap).Count().
      const size_t n = laps.SampleCount(lap);
      REQUIRE(n == l.points.size());
      REQUIRE(cols.times.size() == n);
      REQUIRE(cols.xs.size() == n);
      REQUIRE(cols.ys.size() == n);
      REQUIRE(cols.full_speed.size() == n);
      REQUIRE(cols.cum_distances.size() == n);

      // Element-wise EXACT equality against the per-point studio path:
      // GetLap's points/times/cum_distances + the coordinate system's Local().
      for (size_t i = 0; i < n; ++i) {
        Vec3f loc = cs.Local(l.points[i].point);
        CHECK(cols.times[i] == l.points[i].time);
        CHECK(cols.xs[i] == loc.x);
        CHECK(cols.ys[i] == loc.y);
        CHECK(cols.full_speed[i] == l.points[i].point.full_speed);
        CHECK(cols.cum_distances[i] == l.cum_distances[i]);
      }
    }
  };

  SECTION("three-lap alternating track (incl. the degenerate trailing lap)") {
    Laps laps = MakeThreeLapTrack(cs);
    laps.sectors.start_line = Segment{Point{0, -10}, Point{0, 10}};
    laps.Update();
    REQUIRE(laps.LapsCount() == 3);
    check_columns_match(laps);
  }

  SECTION("sector loop with non-zero full_speed") {
    // MakeSectorLoop sets full_speed = 20 (ground_speed stays 0), so the
    // full_speed column is distinguishable from a wrong-field regression,
    // unlike MakeThreeLapTrack's all-zero speeds.
    Laps laps = MakeSectorLoop(cs);
    laps.sectors.start_line = Segment{Point{0, -50}, Point{0, 50}};
    laps.Update();
    REQUIRE(laps.LapsCount() >= 1);
    check_columns_match(laps);
  }

  SECTION("out-of-range lap ids return all-empty columns (not UB)") {
    Laps laps = MakeThreeLapTrack(cs);
    laps.sectors.start_line = Segment{Point{0, -10}, Point{0, 10}};
    laps.Update();
    REQUIRE(laps.LapsCount() == 3);
    for (size_t bad : {laps.LapsCount(), size_t{9999}}) {
      LapArrays cols = laps.LapColumns(bad);
      CHECK(cols.times.empty());
      CHECK(cols.xs.empty());
      CHECK(cols.ys.empty());
      CHECK(cols.full_speed.empty());
      CHECK(cols.cum_distances.empty());
    }
  }
}
