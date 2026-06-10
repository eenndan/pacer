#include "geometry.hpp"

#include <cassert>
#include <cmath>

#include <pacer/datatypes/datatypes.hpp>

bool pacer::Segment::Intersects(Point fst, Point snd, double *ratio) const {
  Point n = (snd - fst).Rot();
  if (n.Scalar(second - fst) * n.Scalar(first - fst) >= 0) {
    return false;
  }

  Point norm = (second - first).Rot();
  double d1 = norm.Scalar(snd - first), d2 = norm.Scalar(fst - first);
  if (d1 * d2 >= 0) {
    return false;
  }

  if (ratio != nullptr) {
    d1 = std::abs(d1);
    d2 = std::abs(d2);

    *ratio = d2 / (d1 + d2);
  }

  return true;
}

pacer::Point pacer::ToPoint(Vec3f v) { return {v.x, v.y}; }

pacer::Point pacer::ToPoint(Point x) { return x; }

pacer::Point pacer::ToLonLat(GPSSample s) { return {s.lon, s.lat}; }

pacer::Point pacer::Interpolate(Point from, Point to, double ratio) {
  return from * (1 - ratio) + to * ratio;
}

pacer::GPSSample pacer::Interpolate(GPSSample from, GPSSample to,
                                    double ratio) {
  // ratio is an interpolation factor in [0,1]. A degenerate timing-line segment
  // can yield NaN; in that case we fall back to `from` (the double fields just
  // become NaN, but a NaN->int64_t cast for timestamp_ms would be UB).
  assert(std::isnan(ratio) || (ratio >= 0 && ratio <= 1));
  int64_t timestamp_ms =
      std::isnan(ratio)
          ? from.timestamp_ms
          : from.timestamp_ms +
                (int64_t)((to.timestamp_ms - from.timestamp_ms) * ratio);
  // Fields are in declaration order (avoids -Wreorder-init-list) and
  // timestamp_ms is interpolated too (previously dropped -> always 0).
  return {
      .lat = from.lat * (1 - ratio) + to.lat * ratio,
      .lon = from.lon * (1 - ratio) + to.lon * ratio,
      .altitude = from.altitude * (1 - ratio) + to.altitude * ratio,
      .full_speed = from.full_speed * (1 - ratio) + to.full_speed * ratio,
      .ground_speed = from.ground_speed * (1 - ratio) + to.ground_speed * ratio,
      .timestamp_ms = timestamp_ms,
  };
}

auto pacer::CoordinateSystem::Global(Vec3f point) const -> GPSSample {
  point = local_origin + dx * point[0] + dy * point[1] + dz * point[2];
  auto lon = 180 * atan2(point[1], point[0]) / M_PI;
  auto altitude =
      ((point / Vec3f{R_equator, R_equator, R_pole}).Norm() - 1) * R_equator;

  auto lat =
      180 *
      atan2(point[2] / R_pole,
            std::sqrt(point[0] * point[0] + point[1] * point[1]) / R_equator) /
      M_PI;

  return GPSSample{
      .lat = lat,
      .lon = lon,
      .altitude = altitude,
      .full_speed = 0,
      .ground_speed = 0,
  };
}

auto pacer::CoordinateSystem::Local(GPSSample point) const -> Vec3f {
  auto p = CanonicalLocal(point);

  p -= local_origin;

  return Vec3f{
      Scalar(p, dx),
      Scalar(p, dy),
      Scalar(p, dz),
  };
}

pacer::Vec3f pacer::CoordinateSystem::CanonicalLocal(GPSSample origin) {
  return Vec3f{R_equator * std::cos(origin.lat * M_PI / 180.) *
                   std::cos(origin.lon * M_PI / 180.),
               R_equator * std::cos(origin.lat * M_PI / 180.) *
                   std::sin(origin.lon * M_PI / 180.),
               R_pole * std::sin(origin.lat * M_PI / 180.)} *
         (1 + origin.altitude / R_equator);
}

pacer::CoordinateSystem::CoordinateSystem(GPSSample origin)
    : local_origin(CanonicalLocal(origin)),
      dx(Vec3f{
          -R_equator * std::cos(origin.lat * M_PI / 180.) *
              std::sin(origin.lon * M_PI / 180.),
          R_equator * std::cos(origin.lat * M_PI / 180.) *
              std::cos(origin.lon * M_PI / 180.),
          0,
      }),
      dy(Vec3f{
          -R_equator * std::sin(origin.lat * M_PI / 180.) *
              std::cos(origin.lon * M_PI / 180.),
          -R_equator * std::sin(origin.lat * M_PI / 180.) *
              std::sin(origin.lon * M_PI / 180.),
          R_pole * std::cos(origin.lat * M_PI / 180.),
      }),
      dz(Vec3f{
          R_pole * std::cos(origin.lat * M_PI / 180.) *
              std::cos(origin.lon * M_PI / 180.),
          R_pole * std::cos(origin.lat * M_PI / 180.) *
              std::sin(origin.lon * M_PI / 180.),
          R_equator * std::sin(origin.lat * M_PI / 180.),
      }) {
  dx /= dx.Norm();
  dy /= dy.Norm();
  dz /= dz.Norm();

  assert(std::abs(Scalar(dx, dy)) < 1e-6);
  assert(std::abs(Scalar(dx, dz)) < 1e-6);
  assert(std::abs(Scalar(dy, dz)) < 1e-6);
}
double pacer::CoordinateSystem::Distance(const GPSSample &from,
                                         const GPSSample &to) const {
  return (Local(from) - Local(to)).Norm();
}
bool pacer::ApproxEqual(const Point &a, const Point &b, double eps) {
  return std::abs(a.x - b.x) < eps && std::abs(a.y - b.y) < eps;
}

bool pacer::Segment::operator==(const Segment &other) const {
  return ApproxEqual(first, other.first) && ApproxEqual(second, other.second);
}
