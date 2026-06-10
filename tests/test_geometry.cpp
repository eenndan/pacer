#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>

using namespace pacer;

// ---------------------------------------------------------------------------
// Segment::Intersects
// ---------------------------------------------------------------------------

TEST_CASE("Segment::Intersects — vertical timing line at x=0",
          "[geometry][segment]") {
  // Segment along x=0, from (0,-1) to (0,1).
  Segment s{.first = {0, -1}, .second = {0, 1}};

  SECTION("crossing pair writes ratio ~0.5") {
    double ratio = -1.0;
    bool hit = s.Intersects(Point{-1, 0}, Point{1, 0}, &ratio);
    REQUIRE(hit);
    // norm = (second-first).Rot() = {0,2}.Rot() = {-2,0}
    // d1 = norm . (snd - first) = {-2,0}.{1,1}  = -2
    // d2 = norm . (fst - first) = {-2,0}.{-1,1} =  2
    // ratio = |d2| / (|d1| + |d2|) = 2 / 4 = 0.5
    CHECK(ratio == Catch::Approx(0.5));
  }

  SECTION("non-crossing pair (both on same side, x > 0) returns false") {
    double ratio = -1.0;
    bool hit = s.Intersects(Point{1, 0}, Point{2, 0}, &ratio);
    REQUIRE_FALSE(hit);
  }

  SECTION("crossing closer to fst end writes ratio ~0.75") {
    double ratio = -1.0;
    bool hit = s.Intersects(Point{-3, 0}, Point{1, 0}, &ratio);
    REQUIRE(hit);
    // d1 = {-2,0}.{1,1}  = -2
    // d2 = {-2,0}.{-3,1} =  6
    // ratio = |d2| / (|d1| + |d2|) = 6 / 8 = 0.75
    CHECK(ratio == Catch::Approx(0.75));

    // Verify crossing point lies on the segment:
    // crossing = fst*(1-ratio) + snd*ratio
    //          = {-3,0}*0.25 + {1,0}*0.75 = {-0.75+0.75, 0} = {0, 0}
    // x=0 is indeed on s.
    Point fst{-3, 0}, snd{1, 0};
    Point crossing = fst * (1 - ratio) + snd * ratio;
    CHECK(crossing.x == Catch::Approx(0.0));
  }

  SECTION("null ratio pointer is safe (no crash)") {
    bool hit = s.Intersects(Point{-1, 0}, Point{1, 0}, nullptr);
    REQUIRE(hit);
  }
}

// ---------------------------------------------------------------------------
// Interpolate(Point, Point, ratio)
// ---------------------------------------------------------------------------

TEST_CASE("Interpolate Point — midpoint and endpoints",
          "[geometry][interpolate]") {
  Point from{0, 0};
  Point to{10, 20};

  SECTION("ratio 0.5 gives midpoint") {
    auto mid = Interpolate(from, to, 0.5);
    CHECK(mid.x == Catch::Approx(5.0));
    CHECK(mid.y == Catch::Approx(10.0));
  }

  SECTION("ratio 0 gives from") {
    auto p = Interpolate(from, to, 0.0);
    CHECK(p.x == Catch::Approx(from.x));
    CHECK(p.y == Catch::Approx(from.y));
  }

  SECTION("ratio 1 gives to") {
    auto p = Interpolate(from, to, 1.0);
    CHECK(p.x == Catch::Approx(to.x));
    CHECK(p.y == Catch::Approx(to.y));
  }
}

// ---------------------------------------------------------------------------
// Interpolate(GPSSample, GPSSample, ratio)  — timestamp_ms bug regression
// ---------------------------------------------------------------------------

TEST_CASE("Interpolate GPSSample — all fields including timestamp_ms",
          "[geometry][interpolate][regression]") {
  GPSSample from{
      .lat = 0,
      .lon = 0,
      .altitude = 0,
      .full_speed = 0,
      .ground_speed = 0,
      .timestamp_ms = 0,
  };
  GPSSample to{
      .lat = 10,
      .lon = 20,
      .altitude = 100,
      .full_speed = 30,
      .ground_speed = 25,
      .timestamp_ms = 1000,
  };

  SECTION("ratio 0.5 interpolates all fields, timestamp_ms must NOT be 0") {
    auto mid = Interpolate(from, to, 0.5);
    CHECK(mid.lat == Catch::Approx(5.0));
    CHECK(mid.lon == Catch::Approx(10.0));
    CHECK(mid.altitude == Catch::Approx(50.0));
    CHECK(mid.full_speed == Catch::Approx(15.0));
    CHECK(mid.ground_speed == Catch::Approx(12.5));
    // Regression: timestamp_ms was previously always 0 (dropped).
    CHECK(mid.timestamp_ms == 500);
  }

  SECTION("ratio 0 returns from (including timestamp_ms == 0)") {
    auto p = Interpolate(from, to, 0.0);
    CHECK(p.lat == Catch::Approx(from.lat));
    CHECK(p.lon == Catch::Approx(from.lon));
    CHECK(p.altitude == Catch::Approx(from.altitude));
    CHECK(p.full_speed == Catch::Approx(from.full_speed));
    CHECK(p.ground_speed == Catch::Approx(from.ground_speed));
    CHECK(p.timestamp_ms == from.timestamp_ms);
  }

  SECTION("ratio 1 returns to (including timestamp_ms == 1000)") {
    auto p = Interpolate(from, to, 1.0);
    CHECK(p.lat == Catch::Approx(to.lat));
    CHECK(p.lon == Catch::Approx(to.lon));
    CHECK(p.altitude == Catch::Approx(to.altitude));
    CHECK(p.full_speed == Catch::Approx(to.full_speed));
    CHECK(p.ground_speed == Catch::Approx(to.ground_speed));
    CHECK(p.timestamp_ms == to.timestamp_ms);
  }
}

// ---------------------------------------------------------------------------
// ToPoint overloads
// ---------------------------------------------------------------------------

TEST_CASE("ToPoint overloads", "[geometry][topoint]") {
  SECTION("ToLonLat(GPSSample) returns Point{lon, lat}") {
    // geometry.cpp: return {s.lon, s.lat}  =>  x=lon, y=lat
    GPSSample s{.lat = 5,
                .lon = 7,
                .altitude = 0,
                .full_speed = 0,
                .ground_speed = 0,
                .timestamp_ms = 0};
    auto p = ToLonLat(s);
    CHECK(p.x == Catch::Approx(7.0));
    CHECK(p.y == Catch::Approx(5.0));
  }

  SECTION("ToPoint(Point) is identity") {
    Point orig{3, 4};
    auto p = ToPoint(orig);
    CHECK(p.x == Catch::Approx(3.0));
    CHECK(p.y == Catch::Approx(4.0));
  }

  SECTION("ToPoint(Vec3f) takes x and y, drops z") {
    Vec3f v{1, 2, 3};
    auto p = ToPoint(v);
    CHECK(p.x == Catch::Approx(1.0));
    CHECK(p.y == Catch::Approx(2.0));
  }
}

// ---------------------------------------------------------------------------
// CoordinateSystem — round-trip and Distance
// ---------------------------------------------------------------------------

TEST_CASE("CoordinateSystem round-trip Global(Local(p)) == p",
          "[geometry][coordinate_system]") {
  GPSSample origin{
      .lat = 40.0,
      .lon = -74.0,
      .altitude = 10.0,
      .full_speed = 0,
      .ground_speed = 0,
      .timestamp_ms = 0,
  };
  CoordinateSystem cs(origin);

  SECTION("origin maps to local zero then back") {
    auto local = cs.Local(origin);
    CHECK(local.x == Catch::Approx(0.0).margin(1e-4));
    CHECK(local.y == Catch::Approx(0.0).margin(1e-4));
    CHECK(local.z == Catch::Approx(0.0).margin(1e-4));
  }

  SECTION("round-trip Global(Local(p)) recovers p within 1e-6 relative") {
    auto local = cs.Local(origin);
    auto recovered = cs.Global(local);
    CHECK_THAT(recovered.lat,
               Catch::Matchers::WithinRelMatcher(origin.lat, 1e-6));
    CHECK_THAT(recovered.lon,
               Catch::Matchers::WithinRelMatcher(origin.lon, 1e-6));
    CHECK_THAT(recovered.altitude,
               Catch::Matchers::WithinRelMatcher(origin.altitude, 1e-6));
  }

  SECTION("round-trip holds for a nearby point") {
    GPSSample nearby{
        .lat = 40.001,
        .lon = -73.999,
        .altitude = 20.0,
        .full_speed = 0,
        .ground_speed = 0,
        .timestamp_ms = 0,
    };
    auto local = cs.Local(nearby);
    auto recovered = cs.Global(local);
    CHECK_THAT(recovered.lat,
               Catch::Matchers::WithinRelMatcher(nearby.lat, 1e-6));
    CHECK_THAT(recovered.lon,
               Catch::Matchers::WithinRelMatcher(nearby.lon, 1e-6));
    CHECK_THAT(recovered.altitude,
               Catch::Matchers::WithinRelMatcher(nearby.altitude, 1e-6));
  }
}

TEST_CASE(
    "CoordinateSystem::Distance is symmetric and zero for identical points",
    "[geometry][coordinate_system]") {
  GPSSample a{.lat = 40.0,
              .lon = -74.0,
              .altitude = 10.0,
              .full_speed = 0,
              .ground_speed = 0,
              .timestamp_ms = 0};
  GPSSample b{.lat = 40.01,
              .lon = -73.99,
              .altitude = 50.0,
              .full_speed = 0,
              .ground_speed = 0,
              .timestamp_ms = 0};

  CoordinateSystem cs(a);

  SECTION("distance from a point to itself is zero") {
    CHECK(cs.Distance(a, a) == Catch::Approx(0.0).margin(1e-6));
    CHECK(cs.Distance(b, b) == Catch::Approx(0.0).margin(1e-6));
  }

  SECTION("distance is symmetric") {
    double d_ab = cs.Distance(a, b);
    double d_ba = cs.Distance(b, a);
    CHECK(d_ab == Catch::Approx(d_ba));
  }

  SECTION("distance between distinct points is positive") {
    CHECK(cs.Distance(a, b) > 0.0);
  }
}
