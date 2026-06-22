#pragma once

#include <cstdint>

#include "ops.hpp"

namespace pacer {

// A single GPS fix. lat/lon are degrees, altitude is metres, and the two speeds
// are m/s — GPS9 reports both a 2D "ground" speed and a 3D "full" speed.
// timestamp_ms is the fix time as whole milliseconds on the recording clock.
struct GPSSample {
  double lat, lon, altitude, full_speed, ground_speed;
  int64_t timestamp_ms;
  // Fix-quality fields, present on GPS9 but not on the legacy GPS5 stream.
  // `dop` is the dilution of precision (strictly positive on a genuine fix) and
  // `fix` the solution type (0 = none, 2 = 2D, 3 = 3D). The negative sentinels
  // stand for "unknown", which downstream reads as "do not quality-filter" — so
  // GPS5 data is kept. A negative literal (unlike NaN) also round-trips cleanly
  // into the generated Python stub. The defaults keep pre-existing aggregate
  // initialisers (which omit these two) valid.
  double dop = -1.0;
  int fix = -1;
};

// A spatial value tagged with the time it was observed (seconds). `P` is the
// spatial type, so one template covers both the GPS trace (P = GPSSample) and
// the local-metres crossing interpolation (P = Point).
template <class P> struct PointInTime {
  P point;
  double time;
};

// One 3-axis IMU reading — ACCL (accelerometer, m/s^2) or GRAV (a unit gravity
// direction). `time` is on the MEDIA clock (seconds), the same basis as the GPS
// payload spans, so it lines up with the video; the SequentialGPSSource chain
// applies the per-chapter offset exactly as it does for GPS. Axes are stored in
// the GoPro stream's native order (ACCL: Z,X,Y); the studio layer applies the
// camera->kart frame transform on top.
struct IMUSample {
  double x = 0, y = 0, z = 0;
  double time = 0;
};

// One orientation quaternion — CORI (camera orientation), components w,x,y,z.
// `time` is media-clock seconds, matching IMUSample / GPS.
struct QuatSample {
  double w = 1, x = 0, y = 0, z = 0;
  double time = 0;
};

} // namespace pacer
