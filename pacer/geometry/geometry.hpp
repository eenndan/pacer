#pragma once

#include <cstdlib>
#include <optional>
#include <ostream>
#include <utility>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/datatypes/ops.hpp>

namespace pacer {

// A 3-axis geometry/coordinate vector (LOCAL metric space, used by CoordinateSystem and the
// local<->global conversions below). Lives here next to Point because it is a geometry vector,
// not a telemetry sample type. Keeps the full pointwise/linear vector algebra (Global() divides
// it element-wise by an axis-radius vector).
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

struct Point : LinearOperators<Point, double, 2> {
  double x = 0, y = 0;

  Point() = default;
  Point(double x, double y) : x(x), y(y) {}

  double operator[](size_t index) const { return index ? y : x; }
  double &operator[](size_t index) { return index ? y : x; }

  Point Rot() const { return Point{-(*this)[1], (*this)[0]}; }

  friend std::ostream &operator<<(std::ostream &os, const Point &p) {
    return os << "(" << p.x << ", " << p.y << ")";
  }
};

// Local-metres -> Point (drops z for the Vec3f overload). Both inputs are already in the LOCAL
// metric coordinate system, so the result is in metres.
Point ToPoint(Point x);
Point ToPoint(Vec3f v);

// GPS degrees -> Point{lon, lat}. Named distinctly from ToPoint so a degrees sample can never
// be silently mixed with a local-metres Point behind one overloaded name at a call site.
Point ToLonLat(GPSSample s);

// Epsilon comparison of two points (both coordinates within `eps`). The single source of the
// "approximately equal" notion for geometry: Segment::operator== is implemented in terms of it.
// (Point::operator== itself stays exact — it comes from the LinearOperators mixin and is not the
// Python-bound equality; only Segment exposes __eq__.)
bool ApproxEqual(const Point &a, const Point &b, double eps = 1e-6);

struct Segment {
  Point first, second;

  // Returns true if segments intersects, if ratio is non-null, it will satisfy:
  //   fst * (1 - ratio) + snd  lies  on present segment.
  bool Intersects(Point fst, Point snd, double *ratio) const;

  bool operator==(const Segment &other) const;
};

struct CoordinateSystem {
  // Coordinate system maps GPS coordinates to local coordinates.
  //  N.B. All local coordinates measured in meters.
  //
  // I employ following formulas:
  //   x = h_c * R_equator * cos(lat) * cos(lon)
  //   y = h_c * R_equator * cos(lat) * sin(lon)
  //   z = h_c * R_pole * sin(lat)
  //
  // Where h_c is the height compenstaion factor:
  //   h_c = 1 + altitude / R_equator
  //
  // Basis for resulting coordinate system is almost normalised gradients along
  // lon/lat/altiude coordinates: only altitude slightly differs to have
  // ortogonal system:
  //  dx = (-R_equator cos(lat) sin(lon), R_equator cos(lat) cos(lon), 0)
  //  dy = (-R_equator sin(lat) cos(lon), -R_equator sin(lat) sin(lon),
  //        R_pole cos(lat))
  //  dz = (R_pole cos(lat) cos(lon), R_pole cos(lat) sin(lon),
  //        R_equator sin(lat))
  //
  // This is most likely not the best way to do this, but it works for now.

  CoordinateSystem() = default;
  explicit CoordinateSystem(GPSSample origin);

  /// Converts point to local coordinate system.
  auto Local(GPSSample point) const -> Vec3f;

  /// Maps local-coordinate point back to gps sample.
  /// N.B. Speed is not preserved.
  auto Global(Vec3f point) const -> GPSSample;

  double Distance(const GPSSample &from, const GPSSample &to) const;

private:
  constexpr static double R_equator = 6'378'000;
  constexpr static double R_pole = 6'357'000;
  static Vec3f CanonicalLocal(GPSSample point);

  Vec3f local_origin, dx, dy, dz;
};

Point Interpolate(Point from, Point to, double ratio);
GPSSample Interpolate(GPSSample from, GPSSample to, double ratio);

template <class P>
std::optional<PointInTime<P>> Split(Segment start_line, PointInTime<P> first,
                                    PointInTime<P> second) {

  double ratio = 0.;
  if (!start_line.Intersects(ToLonLat(first.point), ToLonLat(second.point),
                             &ratio)) {
    return std::nullopt;
  }

  return PointInTime{
      .point = Interpolate(first.point, second.point, ratio),
      .time = first.time * (1 - ratio) + ratio * second.time,
  };
}
} // namespace pacer