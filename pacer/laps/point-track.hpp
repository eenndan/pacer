#pragma once

#include <algorithm>
#include <span>
#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>

namespace pacer {

// The raw point track + its coordinate system, extracted out of the Laps god-struct. PointTrack
// OWNS {cs_, points_, cum_point_dist_} and is the single home of the point/cumulative-distance
// machinery (the gap-aware SegmentDistance + the CumulativeDistances prefix sum). Laps holds one
// PointTrack and delegates all point/distance operations to it, keeping lap/sector segmentation
// and presentation on top. This type is INTERNAL C++ (not part of the Python surface) — it must
// NOT be added to the litgen binding config.
struct PointTrack {
  // -------------------------------- mutation ----------------------------------//

  // Append a sample. Only grows points_ and pushes a placeholder so cum_point_dist_ stays the
  // same length as points_ (the seed {0} covers the first point). The real cumulative distances
  // are computed entirely in SetCoordinateSystem, which is the sole authority: filling them here
  // would use the still-default cs_ (garbage that SetCoordinateSystem later overwrites) and waste
  // a trig-heavy Distance call. cum_point_dist_ is only meaningful AFTER SetCoordinateSystem ran.
  void AddPoint(GPSSample s, double t) {
    if (!points_.empty())
      cum_point_dist_.push_back(0.0);
    points_.emplace_back(s, t);
  }

  // SetCoordinateSystem is the sole authority for cum_point_dist_ (AddPoint only reserves
  // placeholders). The accumulation loop is single-sourced in CumulativeDistances; the result has
  // size points_.size(), restoring the cum_point_dist_.size() == points_.size() invariant.
  void SetCoordinateSystem(CoordinateSystem coordinate_system) {
    cs_ = coordinate_system;
    // Guard: with no points keep the {0} seed (CumulativeDistances would also return {0}, but
    // bailing avoids rebuilding it and documents the invariant that index [0] / .back() stay
    // valid for a later AddPoint/SetCoordinateSystem).
    if (points_.empty())
      return;
    cum_point_dist_ = CumulativeDistances(cs_, points_);
  }

  void ClearPoints() {
    points_.clear();
    // Reseed the {0} sentinel: index [0] / .back() must stay valid so a later
    // AddPoint/SetCoordinateSystem is not UB. A bare .clear() (size 0) broke that invariant.
    cum_point_dist_.assign(1, 0.0);
  }

  // -------------------------------- access ------------------------------------//

  const CoordinateSystem &Cs() const { return cs_; }

  size_t PointCount() const { return points_.size(); }

  const PointInTime<GPSSample> &Point(size_t row) const { return points_[row]; }

  // Read-only view over the whole point track (for MinMax-style scans).
  std::span<const PointInTime<GPSSample>> Points() const { return points_; }

  // Cumulative distance from points_[0] to points_[i] (the gap-aware odometer).
  double CumulativeDistance(size_t i) const { return cum_point_dist_[i]; }

  // Distance along the interior odometer between two point indices [from, to]
  // (cum_point_dist_[to] - cum_point_dist_[from]); same gap-aware aggregation as the prefix sum.
  double DistanceBetween(size_t from, size_t to) const {
    return cum_point_dist_[to] - cum_point_dist_[from];
  }

  // Distance attributed to the step prev -> curr in THIS track's coordinate system: the GPS chord
  // normally, the speed integral across a dropout. The single boundary-chord helper Laps uses for
  // the start/finish partial segments so they agree with the cumulative odometer.
  double ChordDistance(const PointInTime<GPSSample> &prev,
                       const PointInTime<GPSSample> &curr) const {
    return SegmentDistance(cs_, prev, curr);
  }

  // -------------------------- distance machinery ------------------------------//

  // A normal GoPro GPS fix lands every ~0.1 s. An interior jump longer than this is a real
  // DROPOUT (a run of quality-gated / lost fixes), not jitter. Across such a hole the straight
  // GPS chord cuts the corner and UNDER-measures the lap by tens of metres (measured: a single
  // 6 s dropout cost ~100 m on the 0060 session). The vehicle's reported speed, however, is still
  // valid right up to each fix, so the distance actually travelled across the hole is well
  // approximated by the speed integral. We therefore measure each segment geometrically (the GPS
  // chord, which is the right thing for well-sampled track) EXCEPT across a gap, where we take the
  // trapezoidal speed integral 1/2 (v0+v1) * dt instead. Normal segments are untouched, so only
  // the dropout laps change — and they stop under-counting.
  static constexpr double kGapSeconds = 0.35;

  // Distance attributed to the step prev -> curr: the GPS chord normally, the speed integral when
  // the time step is a dropout (so a chord across a hole no longer cuts the lap short).
  static double SegmentDistance(const CoordinateSystem &cs,
                                const PointInTime<GPSSample> &prev,
                                const PointInTime<GPSSample> &curr) {
    double chord = cs.Distance(prev.point, curr.point);
    double dt = curr.time - prev.time;
    if (dt > kGapSeconds) {
      double speed_integral =
          0.5 * (prev.point.full_speed + curr.point.full_speed) * dt;
      // Never let the fill SHORTEN the measured chord (guards a bad speed reading); the gap arc
      // is at least the straight-line distance between its mouths.
      return std::max(chord, speed_integral);
    }
    return chord;
  }

  // Prefix sum of the gap-aware SegmentDistance over consecutive points: the single owner of the
  // cumulative-distance accumulation loop shared by PointTrack::SetCoordinateSystem (member
  // odometer) and Lap::FillDistances (per-lap odometer). Returns a vector of size points.size():
  // index 0 is 0, index i is the distance from points[0] to points[i]. Empty input -> {0},
  // matching the {0}-seed both call sites relied on.
  static std::vector<double>
  CumulativeDistances(const CoordinateSystem &cs,
                      std::span<const PointInTime<GPSSample>> points) {
    std::vector<double> cum;
    cum.reserve(points.empty() ? 1 : points.size());
    cum.push_back(0.0);
    for (size_t i = 1; i < points.size(); ++i) {
      cum.push_back(cum.back() + SegmentDistance(cs, points[i - 1], points[i]));
    }
    return cum;
  }

private:
  CoordinateSystem cs_;

  std::vector<PointInTime<GPSSample>> points_;
  std::vector<double> cum_point_dist_{0};
};

} // namespace pacer
