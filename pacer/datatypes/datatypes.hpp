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

// A timestamped 3-axis IMU sample (used for ACCL accelerometer m/s^2 and GRAV gravity
// vector). `time` is on the MEDIA clock (seconds, same basis as the GPS payload spans, so
// it syncs to the video; chapter offsets are applied by the SequentialGPSSource chain just
// like GPS). The three axes are carried in the GoPro stream's native element order
// (ACCL: Z,X,Y in m/s^2; GRAV: a unit gravity-direction vector). The studio layer resolves
// the camera->kart frame transform on top of these raw axes.
struct IMUSample {
  double x = 0, y = 0, z = 0;
  double time = 0;
};

inline std::ostream &operator<<(std::ostream &os, const IMUSample &s) {
  return os << "IMU(t: " << std::setprecision(4) << std::fixed << s.time
            << ", x: " << s.x << ", y: " << s.y << ", z: " << s.z << ")";
}

// A timestamped orientation quaternion (used for CORI camera-orientation, w,x,y,z).
// `time` is on the MEDIA clock (seconds), same basis as IMUSample / GPS.
struct QuatSample {
  double w = 1, x = 0, y = 0, z = 0;
  double time = 0;
};

inline std::ostream &operator<<(std::ostream &os, const QuatSample &s) {
  return os << "Quat(t: " << std::setprecision(4) << std::fixed << s.time
            << ", w: " << s.w << ", x: " << s.x << ", y: " << s.y
            << ", z: " << s.z << ")";
}

} // namespace pacer
