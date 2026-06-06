#pragma once

#include <cstdint>
#include <iomanip>
#include <ostream>

#include "ops.hpp"

namespace pacer {

struct GPSSample {
  double lat, lon, altitude, full_speed, ground_speed;
  int64_t timestamp_ms;
  // GPS9 quality fields (GoPro): dilution-of-precision and fix type (0=none, 2=2D, 3=3D).
  // Sources without these (the legacy GPS5 stream) leave the sentinels below, which downstream
  // treats as "quality unknown -> keep". DOP is always positive in real fixes, so a negative
  // sentinel is unambiguous (and -- unlike NaN -- renders as a plain literal in the generated
  // python stub). Defaulted so existing aggregate inits stay valid.
  double dop = -1.0;
  int fix = -1;
};

template <class P> struct PointInTime {
  P point;
  double time;

  template <class F, class U> PointInTime<U> Map(F f) const {
    return PointInTime<U>{.point = f(point), .time = time};
  }
};

inline std::ostream &operator<<(std::ostream &os, const GPSSample &s) {
  return os << "GPS(lat: " << std::setprecision(4) << std::fixed << s.lat
            << ", lon: " << s.lon << ", alt: " << s.altitude
            << ", full: " << s.full_speed << ", ground: " << s.ground_speed
            << ", dop: " << s.dop << ", fix: " << s.fix << ")";
}

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

} // namespace pacer
