#pragma once

#include <cstdlib>
#include <optional>
#include <ostream>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/datatypes/ops.hpp>

namespace pacer {

// A 3-component vector in the LOCAL metric frame, used by CoordinateSystem and
// the local<->global conversions. It lives beside Point because it is a
// geometry vector rather than a telemetry sample, and it keeps the full linear
// + pointwise algebra (Global() divides one elementwise by a per-axis radius
// vector).
struct Vec3f : public VectorOperators<Vec3f, double, 3> {
  double x = 0, y = 0, z = 0;

  Vec3f() = default;
  Vec3f(double x, double y, double z) : x{x}, y{y}, z{z} {}

  double &operator[](size_t index) {
    return (index == 0) ? x : (index == 1) ? y : z;
  }
  double operator[](size_t index) const {
    return (index == 0) ? x : (index == 1) ? y : z;
  }
};

// A 2D point / vector (local metres, or lon-lat degrees — the caller decides).
struct Point : LinearOperators<Point, double, 2> {
  double x = 0, y = 0;

  Point() = default;
  Point(double x, double y) : x(x), y(y) {}

  double operator[](size_t index) const { return index ? y : x; }
  double &operator[](size_t index) { return index ? y : x; }

  // 90-degree left rotation: (x, y) -> (-y, x). Used to build the perpendicular
  // for the segment-crossing side test.
  Point Rot() const { return Point{-(*this)[1], (*this)[0]}; }

  friend std::ostream &operator<<(std::ostream &os, const Point &p) {
    return os << "(" << p.x << ", " << p.y << ")";
  }
};

// Local metres -> Point (the Vec3f form keeps x/y and drops z). Both inputs are
// already in the local metric frame, so the result is metres.
Point ToPoint(Point x);
Point ToPoint(Vec3f v);

// GPS degrees -> Point{lon, lat}. Deliberately a different name from ToPoint so
// a degrees sample can never be mixed with a local-metres point behind one
// overload set at a call site.
Point ToLonLat(GPSSample s);

// Per-coordinate approximate equality (both within `eps`). This is the one
// place the geometry layer defines "close enough"; Segment::operator== builds
// on it. Point's own operator== (from the LinearOperators mixin) stays exact
// and is not the Python-bound equality — only Segment exposes __eq__.
bool ApproxEqual(const Point &a, const Point &b, double eps = 1e-6);

struct Segment {
  Point first, second;

  // True iff this segment and fst->snd cross PROPERLY. Both straddle tests use
  // strict signs, so a touch — an endpoint of either segment lying exactly on
  // the other's supporting line — is NOT a crossing (pinned by
  // tests/test_geometry, including that a trace vertex sitting exactly on a
  // timing line produces no crossing from either adjacent segment). On a true
  // return, if `ratio` is non-null it gets the crossing's fraction along
  // fst->snd, so fst*(1-ratio) + snd*ratio is the intersection point (Split
  // uses it to interpolate the crossing sample/time). `ratio` is untouched on
  // false.
  bool Intersects(Point fst, Point snd, double *ratio) const;

  bool operator==(const Segment &other) const;
};

struct CoordinateSystem {
  // Maps GPS coordinates to a local metric frame (metres). The forward map is
  // an ECEF-style projection with a height-compensation factor h_c = 1 +
  // alt/R_eq:
  //
  //   x = h_c * R_eq * cos(lat) * cos(lon)
  //   y = h_c * R_eq * cos(lat) * sin(lon)
  //   z = h_c * R_pole * sin(lat)
  //
  // The local basis is the (almost) normalised gradient of that map along
  // lon / lat / altitude, orthogonalised by the radius choice. Good enough for
  // a single track; not a survey-grade datum.

  // The default frame is the IDENTITY basis (dx/dy/dz = the ECEF unit axes,
  // origin at the geocentre), so Local() of a default-constructed system
  // returns the raw ECEF position in metres and Distance() the true 3D chord —
  // rather than the silent zeros an all-zero basis used to yield for code that
  // forgot to install a real CoordinateSystem(origin). Every real pipeline
  // still sets a track-centred origin before any distance is read (verified
  // byte-identical on a real session), so this only changes what FORGOTTEN
  // initialisation produces.
  CoordinateSystem() = default;
  explicit CoordinateSystem(GPSSample origin);

  /// GPS sample -> local-frame point.
  auto Local(GPSSample point) const -> Vec3f;

  /// Local-frame point -> GPS sample. N.B. speed is not recovered.
  auto Global(Vec3f point) const -> GPSSample;

  double Distance(const GPSSample &from, const GPSSample &to) const;

private:
  constexpr static double R_equator = 6'378'000;
  constexpr static double R_pole = 6'357'000;
  static Vec3f CanonicalLocal(GPSSample point);

  // Identity (ECEF) defaults — see the default-constructor note above.
  Vec3f local_origin, dx{1, 0, 0}, dy{0, 1, 0}, dz{0, 0, 1};
};

Point Interpolate(Point from, Point to, double ratio);
GPSSample Interpolate(GPSSample from, GPSSample to, double ratio);

// If `start_line` crosses the segment first->second, return the interpolated
// crossing (point + time); otherwise nullopt. Templated on the spatial type so
// it serves both the GPS trace and local-metres traces.
template <class P>
std::optional<PointInTime<P>> Split(Segment start_line, PointInTime<P> first,
                                    PointInTime<P> second) {

  double ratio = 0.;
  if (!start_line.Intersects(ToLonLat(first.point), ToLonLat(second.point),
                             &ratio)) {
    return std::nullopt;
  }

  return PointInTime<P>{
      .point = Interpolate(first.point, second.point, ratio),
      .time = first.time * (1 - ratio) + ratio * second.time,
  };
}
} // namespace pacer
