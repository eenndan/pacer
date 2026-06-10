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

struct Sectors {
  Segment start_line;
  std::vector<Segment> sector_lines;
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
  double LapEntrySpeed(size_t lap) const;
  double LapTime(size_t lap) const;
  size_t SampleCount(size_t lap) const;
  double StartTimestamp(size_t lap) const;
  double GetLapDistance(size_t index) const;

  Lap GetLap(size_t lap) const;

  //------------------------------- SECTORS ---------------------------------//

  size_t SectorCount() const;
  size_t RecordedSectors() const;
  void ClearSectors();
  double SectorTime(size_t sector) const;
  double SectorStartTimestamp(size_t sector) const;
  double SectorEntrySpeed(size_t sector) const;

  //------------------------------ RAW POINTS -------------------------------//

  void AddPoint(GPSSample s, double t);
  size_t PointCount() const;
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
  std::vector<LapChunk> lap_chunks_;
  std::vector<LapChunk> sector_chunks_;

  Segment dirty_start_line_ = {};
  std::vector<Segment> dirty_sector_lines_ = {};
};

} // namespace pacer
