#include "geometry.hpp"

#include <cassert>
#include <cmath>

#include <pacer/datatypes/datatypes.hpp>

bool pacer::Segment::Intersects(Point fst, Point snd, double *ratio) const {
  // Proper-crossing test. Two segments cross iff each one's endpoints fall on
  // strictly opposite sides of the other's supporting line. "Side" is the sign
  // of the line's perpendicular (Rot()) dotted with the offset to an endpoint;
  // if the two signs agree (product >= 0) the endpoints are on one side, or one
  // lies exactly on the line — either way, no proper crossing.

  // Test 1: do this segment's endpoints straddle the line through fst->snd?
  const Point perp_other = (snd - fst).Rot();
  if (perp_other.Scalar(second - fst) * perp_other.Scalar(first - fst) >= 0) {
    return false;
  }

  // Test 2: does fst->snd straddle this segment's supporting line?
  const Point perp_self = (second - first).Rot();
  double d_snd = perp_self.Scalar(snd - first);
  double d_fst = perp_self.Scalar(fst - first);
  if (d_snd * d_fst >= 0) {
    return false;
  }

  // Crossing fraction along fst->snd, by the ratio of perpendicular distances.
  if (ratio != nullptr) {
    d_snd = std::abs(d_snd);
    d_fst = std::abs(d_fst);
    *ratio = d_fst / (d_snd + d_fst);
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
  // `ratio` is the [0,1] blend factor. A degenerate timing-line segment can
  // hand us a NaN; we then keep `from`'s integer timestamp, because a NaN ->
  // int64_t cast is UB (the double fields are allowed to go NaN). Fields are
  // written in declaration order to avoid -Wreorder-init-list.
  assert(std::isnan(ratio) || (ratio >= 0 && ratio <= 1));
  int64_t timestamp_ms =
      std::isnan(ratio)
          ? from.timestamp_ms
          : from.timestamp_ms +
                (int64_t)((to.timestamp_ms - from.timestamp_ms) * ratio);
  return {
      .lat = from.lat * (1 - ratio) + to.lat * ratio,
      .lon = from.lon * (1 - ratio) + to.lon * ratio,
      .altitude = from.altitude * (1 - ratio) + to.altitude * ratio,
      .full_speed = from.full_speed * (1 - ratio) + to.full_speed * ratio,
      .ground_speed = from.ground_speed * (1 - ratio) + to.ground_speed * ratio,
      .timestamp_ms = timestamp_ms,
  };
}

pacer::Vec3f pacer::CoordinateSystem::CanonicalLocal(GPSSample origin) {
  const double rlat = origin.lat * M_PI / 180.;
  const double rlon = origin.lon * M_PI / 180.;
  const double height_comp = 1 + origin.altitude / R_equator;
  return Vec3f{R_equator * std::cos(rlat) * std::cos(rlon),
               R_equator * std::cos(rlat) * std::sin(rlon),
               R_pole * std::sin(rlat)} *
         height_comp;
}

auto pacer::CoordinateSystem::Global(Vec3f point) const -> GPSSample {
  point = local_origin + dx * point[0] + dy * point[1] + dz * point[2];

  const double lon = 180 * std::atan2(point[1], point[0]) / M_PI;
  const double altitude =
      ((point / Vec3f{R_equator, R_equator, R_pole}).Norm() - 1) * R_equator;
  const double lat =
      180 *
      std::atan2(point[2] / R_pole,
                 std::sqrt(point[0] * point[0] + point[1] * point[1]) /
                     R_equator) /
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
  Vec3f p = CanonicalLocal(point);
  p -= local_origin;
  return Vec3f{
      Scalar(p, dx),
      Scalar(p, dy),
      Scalar(p, dz),
  };
}

pacer::CoordinateSystem::CoordinateSystem(GPSSample origin)
    : local_origin(CanonicalLocal(origin)) {
  const double rlat = origin.lat * M_PI / 180.;
  const double rlon = origin.lon * M_PI / 180.;
  const double clat = std::cos(rlat), slat = std::sin(rlat);
  const double clon = std::cos(rlon), slon = std::sin(rlon);

  // Gradient of the ECEF map along lon / lat / altitude, then unit-normalised.
  dx = Vec3f{-R_equator * clat * slon, R_equator * clat * clon, 0};
  dy = Vec3f{-R_equator * slat * clon, -R_equator * slat * slon, R_pole * clat};
  dz = Vec3f{R_pole * clat * clon, R_pole * clat * slon, R_equator * slat};

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
