#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/laps/laps.hpp>

using pacer::CoordinateSystem;
using pacer::GPSSample;
using pacer::Lap;
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
