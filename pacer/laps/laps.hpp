#pragma once

#include <vector>

#include <pacer/datatypes/datatypes.hpp>
#include <pacer/geometry/geometry.hpp>
#include <pacer/laps/point-track.hpp>

namespace pacer {

struct Lap {
  float width;

  std::vector<PointInTime<GPSSample>> points;
  std::vector<double> cum_distances;

  void FillDistances(const CoordinateSystem &cs);

  double LapTime() const;

  size_t Count() const;
};

// The INPUT timing-line geometry: the start line + the intermediate sector lines, in local
// metres. NOTE (confusion trap): the studio does NOT compute per-lap sector SPLITS from the C++
// crossing list these lines produce — it projects each sector line onto the lap's odometer by
// DISTANCE in Python (studio/session.py, lap_sector_splits), because a short line can miss a
// geometric crossing on some laps.
struct Sectors {
  Segment start_line;
  std::vector<Segment> sector_lines;
};

// A lap's per-point columns as parallel arrays, for a SINGLE Python<->C++ crossing per lap. The
// studio layer used to cross the binding once PER POINT (cs.local(p.point), p.point.full_speed,
// p.time, cum_distances[i] in loops over hundreds of points) to build the map/plot/g-meter
// arrays; LapColumns returns them all at once. Every member has the SAME length as the
// materialized lap (Lap::Count(): the interpolated start crossing + the interior track points +
// the interpolated finish crossing), so the columns are mutually index-aligned.
//   times          media-clock seconds of each point (== Lap::points[i].time)
//   xs, ys         LOCAL metres (CoordinateSystem::Local of each point, x/y) in the laps' OWN
//                  coordinate system (the one set via SetCoordinateSystem)
//   full_speed     raw 3D GPS speed (m/s) of each point (the studio layer scales to km/h)
//   cum_distances  the lap's per-point odometer (== Lap::cum_distances), gap-aware
struct LapArrays {
  std::vector<double> times;
  std::vector<double> xs;
  std::vector<double> ys;
  std::vector<double> full_speed;
  std::vector<double> cum_distances;
};

struct Laps {
  /// Updates all laps given updated start_line and sector_lines
  void Update();

  /// Picks a starting point for start_line.
  /// Default implementation builds segment perpendicular to median segment.
  Segment PickRandomStart() const;

  //---------------------------- PRESENTATION -------------------------------//

  void SetCoordinateSystem(CoordinateSystem coordinate_system);

  /// Gets bounding box for entire thing, might be cached
  /// as depends on points only.
  auto MinMax() const -> std::pair<Point, Point>;

  //-------------------------------- LAPS -----------------------------------//

  Sectors sectors;

  size_t LapsCount() const;
  /// Throws std::out_of_range (Python: IndexError) if lap >= LapsCount().
  double LapEntrySpeed(size_t lap) const;
  /// Throws std::out_of_range (Python: IndexError) if lap >= LapsCount().
  double LapTime(size_t lap) const;
  /// Out-of-range lap -> 0 (documented empty-return contract, like GetLap).
  size_t SampleCount(size_t lap) const;
  /// Throws std::out_of_range (Python: IndexError) if lap >= LapsCount().
  double StartTimestamp(size_t lap) const;
  /// Throws std::out_of_range (Python: IndexError) if index >= LapsCount().
  double GetLapDistance(size_t index) const;

  Lap GetLap(size_t lap) const;

  /// A lap's per-point columns (times, local-metre xs/ys, full_speed, cum_distances) as parallel
  /// arrays, so the studio layer builds its map/plot/g-meter arrays in ONE binding crossing
  /// instead of one per point. Identical (to float round-off) to materializing GetLap(lap) and
  /// then taking p.time / cs.Local(p.point).x|y / p.point.full_speed / cum_distances[i] per point,
  /// where cs is the laps' own coordinate system. Out-of-range lap -> all-empty arrays.
  LapArrays LapColumns(size_t lap) const;

  //------------------------------- SECTORS ---------------------------------//

  size_t SectorCount() const;
  size_t RecordedSectors() const;
  void ClearSectors();
  /// Throws std::out_of_range (Python: IndexError) if sector >= RecordedSectors().
  double SectorTime(size_t sector) const;
  /// Throws std::out_of_range (Python: IndexError) if sector >= RecordedSectors().
  double SectorStartTimestamp(size_t sector) const;
  /// Throws std::out_of_range (Python: IndexError) if sector >= RecordedSectors().
  double SectorEntrySpeed(size_t sector) const;

  //------------------------------ RAW POINTS -------------------------------//

  void AddPoint(GPSSample s, double t);
  size_t PointCount() const;
  /// Throws std::out_of_range (Python: IndexError) if row >= PointCount().
  PointInTime<GPSSample> GetPoint(size_t row) const;
  void ClearPoints();

private:
  struct LapChunk {
    PointInTime<GPSSample> start, finish;
    size_t start_index, finish_index;

    double Time() const;
  };

  // The raw point track + coordinate system + cumulative-distance machinery, extracted into a
  // cohesive internal value type. Laps DELEGATES all point/distance operations to it and keeps
  // lap/sector segmentation on top.
  PointTrack track_;

  // Computed LapChunks (start/finish crossings) for the laps and the sectors. Named *_chunks_ to
  // distinguish them from the INPUT geometry in the public `sectors` member (start_line +
  // sector_lines), which is what gets segmented into these.
  // NOTE: sector_chunks_ (read by SectorTime/SectorStartTimestamp/SectorEntrySpeed) is the raw
  // GEOMETRIC crossing list. The studio's per-lap sector splits are NOT built from it — they are
  // computed in Python by distance projection (studio/session.py, lap_sector_splits); see the
  // `Sectors` comment above.
  std::vector<LapChunk> lap_chunks_;
  std::vector<LapChunk> sector_chunks_;

  // Update() re-segments only when an input changed. The two sentinels below detect a timing-line
  // edit (start_line / sector_lines); segmentation_dirty_ detects the OTHER input — the point track
  // / coordinate system. It starts true (an empty/fresh Laps has never been segmented) and is set
  // by AddPoint/ClearPoints/SetCoordinateSystem so a re-segment after the points changed but the
  // timing lines did NOT still recomputes (otherwise Update() early-outs with stale lap_chunks_).
  Segment dirty_start_line_ = {};
  std::vector<Segment> dirty_sector_lines_ = {};
  bool segmentation_dirty_ = true;
};

} // namespace pacer
