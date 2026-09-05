# Helper: one Catch2 executable per test file, registered with CTest.
macro(add_pacer_test name)
    add_executable(${name} ${name}.cpp)
    target_link_libraries(${name} PRIVATE ${ARGN} Catch2::Catch2WithMain)
    add_test(NAME ${name} COMMAND ${name})
    set_property(TARGET ${name} PROPERTY FOLDER "tests")
endmacro()

add_pacer_test(test_coordinate_system pacer::geometry)
add_pacer_test(test_ops             pacer::geometry)
add_pacer_test(test_geometry        pacer::geometry)
add_pacer_test(test_laps            pacer::laps)
# SequentialGPSSource chapter-seam chaining (no payload dropped at the boundary) + the
# Seek-before-first-payload clamp, driven through in-memory stub RawGPSSources.
add_pacer_test(test_gps_source      pacer::gps-source)

# Pure-Python tests for the studio app (no telemetry file / no pacer build dependency for the
# logic they cover — scrub-conversion math + the F1/F3/F5 UI-feature logic). Registered with
# CTest so `pixi run test` runs them alongside the C++ suite.
# REQUIRED, not optional: the build needs the interpreter anyway (bindings codegen; the root
# CMakeLists pins it to the pixi env), so a machine where find_package fails should fail the
# configure loudly instead of silently dropping the 14 Python tests from the suite.
find_package(Python REQUIRED COMPONENTS Interpreter)
add_test(NAME test_scrub_conversion
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_scrub_conversion.py)
add_test(NAME test_lap_timing
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_lap_timing.py)
add_test(NAME test_chapters
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_chapters.py)
# Import ergonomics (fix/import-ergonomics): the last-opened-folder pref round-trip (prefs.LAST_DIR)
# + File ▸ Open starting there and recording the picked folder, and the multi-recording drop guard
# (chapters.group_into_recordings groups distinct recordings; _open_recordings loads only the FIRST
# and never merges unrelated ones onto one clock). Builds a StudioWindow via __new__ so it imports
# Qt -> run offscreen. No telemetry file.
add_test(NAME test_import_ergonomics
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_import_ergonomics.py)
set_tests_properties(test_import_ergonomics PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Layering contract (test/layering-contract): ONLY session/load/ingest/tracks may import the pacer
# core — every view/controller/helper stays pacer-free. A pure `ast` walk of studio/*.py: no pacer,
# no Qt, no telemetry file, so it needs neither the offscreen env nor the PYTHONPATH block below.
add_test(NAME test_layering
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_layering.py)
# MAP-ONLY GPS-gap reconstruction (studio.gapfill): pure Python + numpy, no telemetry file.
add_test(NAME test_gapfill
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_gapfill.py)
# Timeline (E2): the cursor/plot/video-sync coordinate conversions extracted off Session into the
# Qt-free studio.timeline.Timeline — plot-x<->media-time (both modes), index/lap_at_time (half-open
# windows + invalidate), map nearest (whole-trace vs lap-scoped). Pure numpy on injected stubs.
add_test(NAME test_timeline
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_timeline.py)
# Data-quality signal: studio.data_quality.TimingQuality classifies the per-sample clock provenance
# (GPS9 true clock vs media-clock fallback) + the gate's dropped-fix fraction into the degraded
# verdict + banner concern lines. Pure value object — no Qt, no pacer.
add_test(NAME test_data_quality
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_data_quality.py)
# Demo-download resolution (fix/demo-download-timeout): the fetch passes a finite timeout (a stalled
# TCP must not hang the UI thread) + is atomic (stream to .part, rename; no leftover on failure).
# studio.demo is pacer-free AND Qt-free (urlopen stubbed) — no pacer, no Qt, no network.
add_test(NAME test_demo
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_demo.py)
# Reference-centerline closed-loop fit (studio.reference): pure Python + numpy, synthetic
# loops with known similarity transforms (incl. cyclic offset / reversal / reflection).
add_test(NAME test_reference
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_reference.py)
# RawGPSSource binding surface: Python<->C++ trampoline (read_accl/grav/cori) + the
# SequentialGPSSource chapter-offset chaining. Imports pacer only (no Qt).
add_test(NAME test_gps_source_bindings
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_gps_source_bindings.py)
# Laps bound-accessor bounds checks (P1.2): a bad index raises IndexError in Python
# (std::out_of_range translated by nanobind); the get_lap/sample_count/lap_columns
# empty-return trio is unchanged. Imports pacer only (no Qt, no telemetry file).
add_test(NAME test_laps_bindings
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_laps_bindings.py)
# Single-pass ingest (PR #40): read_recording's shared chain == the pre-#40 two-fresh-chains
# reads (bundled GoPro samples), + IMU-after-GPS-drain invariance on a Python stub source.
# Imports pacer + studio.ingest only (no Qt).
add_test(NAME test_ingest_equivalence
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_ingest_equivalence.py)
# Timing-line sidecar persistence: path resolution + JSON schema round-trip are pure
# Python; the lat/lon<->local export/apply + revert guard run on a synthetic pacer.Laps
# (imports pacer only — Session's chain has no Qt — and needs no telemetry file).
add_test(NAME test_sidecar
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_sidecar.py)
# Track database (E3): the named-track JSON store (schema round-trip / name-keyed upsert /
# corrupt-file self-heal / built-in Daytona MK seed) is pure Python; the detect→Track→segment
# adapter + precedence + MK no-regression + the Save-as-track guard run on a synthetic pacer.Laps
# (imports pacer; offscreen Qt for the guard half) and self-skip without the bindings.
add_test(NAME test_track_db
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_track_db.py)
# Pure-fake session analysis pack (PLAN §5): _signal._band_lap_ids on a local fake,
# load._clean on synthetic GPSSample traces, and the sector-splits / sector-plot-positions /
# delta()-endpoint invariants on bare Sessions seeded via tests/_synthetic. Imports pacer
# only (session/load chain has no Qt, verified), no telemetry file.
add_test(NAME test_session_pure
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_session_pure.py)
# Sector-boundary integrity (QA MAP-08-ESC / MAP-11): the collapse decision is SESSION-wide, so
# sweeping a second sector line across the tolerance can never leave two laps with a different
# number of boundaries (the state where an S column meant a different stretch of track per row and
# session_best_splits/theoretical_best summed incomparable pieces); plus suggest_sectors(n)'s even
# subdivision. Bare Sessions seeded via tests/_synthetic — no Qt, no telemetry file.
add_test(NAME test_sector_integrity
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_sector_integrity.py)
# Load-pipeline internals (test/load-pipeline-coverage): the clock-provenance decision
# (load._used_gps9_trueclock, behind the degraded-timing banner) and the no-average-across-a-gap
# smoothing contract (_signal._gap_segments / _smooth_segments) — pure functions that ran only
# through the manual D24 dump before. Imports pacer via studio.load; no Qt, no telemetry file.
add_test(NAME test_load_internals
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_load_internals.py)
# End-to-end load-pipeline integration (test/load-pipeline-integration): runs the assembled
# load_recording (read->gate->clean->gps9->smooth->segment->start-line) via Session.load on the
# committed 3rdparty/gpmf-parser/samples/hero6.mp4 and pins the whole Session fingerprint to a
# committed baseline (eps 1e-9) + determinism + the media-clock invariants. COMPLEMENTS the D24
# GPS9 gate (hero6 is media-clock-fallback). Imports Session -> Qt, so run offscreen.
add_test(NAME test_load_pipeline
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_load_pipeline.py)
set_tests_properties(test_load_pipeline PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Start-line widen decision (test/fit-start-line-widen): load._fit_start_line keeps a sufficient
# registry line but widens (1.15/1.3/1.5 about the midpoint) to recover band-laps a too-short line
# missed — it sets known-track lap segmentation. Both branches pinned on a synthetic circle Laps.
# Imports pacer (no Qt).
add_test(NAME test_fit_start_line
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_fit_start_line.py)
# F1 god-object decomposition (studio.corner_model.CornerModel + studio.driving_channels.
# DrivingChannels): Session composes + DELEGATES to the per-domain services, and the cache
# invalidation (set_timing_lines drives both invalidate()s; the reference set/clear drives the
# narrower CornerModel.invalidate_stats()) clears exactly what the old hand-clearing did. Pins
# the delegation + caching + invalidate-actually-clears contracts on a deterministic synthetic
# session (stadium loop + seeded g-meter). Imports Session -> Qt, so run offscreen.
add_test(NAME test_session_services
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_session_services.py)
set_tests_properties(test_session_services PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
add_test(NAME test_studio_features
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_studio_features.py)
set_tests_properties(test_studio_features PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# CI half of the Session equivalence gate (test/synthetic-golden-gate): automates the F1/E2/#50
# byte-identity machinery (studio.dev.golden_session_dump.fingerprint + golden_compare.walk, eps
# 1e-9) over the DETERMINISTIC synthetic session (test_session_services._synthetic_session — stadium
# loop + seeded g-meter, REAL corner/driving/delta/bests/consistency, no media file), across
# base/ref/ref_cleared phases, vs a committed baseline (golden_synthetic_baseline.json). Any
# Session-math leaf drift fails the build — with NO ~11.8 GB D24 load, so it runs in CI. The full,
# higher-coverage D24 golden dump stays the (unchanged) MANUAL gate. Imports Session -> Qt, offscreen.
add_test(NAME test_golden_synthetic
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_golden_synthetic.py)
set_tests_properties(test_golden_synthetic PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# The OTHER half of the golden gate's credibility: the manual D24 dump must be a pure function of
# the recording. It redirects every studio _app_support_dir seam; this fails the build if a new
# seam appears and is not redirected (track_db was missed for months, and made the gate report a
# 45% leaf diff for no code change). AST-only + one live seam check, no recording needed.
add_test(NAME test_golden_hermetic
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_golden_hermetic.py)
set_tests_properties(test_golden_hermetic PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# F3 rainbow track map: pure-numpy bucketization / NaN-break / Δ-resampling helpers + the
# MapView toggle invariants (OFF restores byte-identical items; zero rebuilds on the tick
# path) on a stub session. Imports map_view -> Qt, so run offscreen.
add_test(NAME test_rainbow_map
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_rainbow_map.py)
set_tests_properties(test_rainbow_map PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# map_render (E2): the Qt-free pure-numpy track-map math extracted from map_view — bucketize /
# bucket_polylines / resample_grid_to_points + the rainbow_channel per-channel value/bucket math
# (speed/Δ/grip, the Δ+grip negation, the grip fixed scale, the GPS-dropout NaN-mask, the None
# gates). Imports studio.theme -> Qt, so run offscreen; no telemetry file.
add_test(NAME test_map_render
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_map_render.py)
set_tests_properties(test_map_render PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Track-map view fit + the always-on session trace (QA MAP-01/03/04): real QWheelEvent + drag on
# the plot viewport and back via the app's Fit button / canvas double-click, the zero-lap state
# drawing the trace its placeholder tells you to drag onto, the fit taken over trace UNION laps
# (so the video marker stays on canvas), and the A16/#126 guard that the pyqtgraph "A" button and
# right-click menu stay gone. Real MapView widgets, offscreen; no telemetry file.
add_test(NAME test_map_fit
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_map_fit.py)
set_tests_properties(test_map_fit PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Track-map chrome (QA MAP-05/07/09/10 + MAP-11): the provisional callout clamped inside the plot
# at either edge, the colour ramp hidden when a channel has no gradient to label, the Elevation
# legend stated relative to the lap (absolute GPS altitude drifts between laps), the on-canvas
# notice the sector buttons post (and that grab_clean hides it), and Add sector re-spacing the
# whole set while — and only while — the user has not dragged a line. Real MapView widgets,
# offscreen; no telemetry file.
add_test(NAME test_map_chrome
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_map_chrome.py)
set_tests_properties(test_map_chrome PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Compare-video Δ-badge math: session.delta_between (vs an arbitrary lap, cross-checked
# against the hardcoded-vs-best delta_at_time). Imports Session -> Qt, so run offscreen.
add_test(NAME test_compare
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_compare.py)
set_tests_properties(test_compare PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Cross-recording compare + layout fixes (f7 phase B): REAL VideoView / PlayerPane widgets (the
# PR-#80 fakes hid the bugs) — the toggle-re-entrancy that put pane B on the primary source, the
# equal+draggable compare splitter, and the file-matched deferred-seek gate. PACER_NO_MEDIA builds
# the inert media triplet; the opt-in real-media proof self-skips without ~/Desktop/D24. Offscreen.
add_test(NAME test_video_view_compare
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_video_view_compare.py)
set_tests_properties(test_video_view_compare PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# F4 compare-mode map ghost: CompareController places lap B's kart on a REAL MapView at
# index_at_time(t_b) per tick (setPos-only, one lazy item); exit restores the item state
# byte-identically; zero ghost work outside compare. Imports map_view -> Qt, so run offscreen.
add_test(NAME test_map_ghost
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_map_ghost.py)
set_tests_properties(test_map_ghost PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Cross-recording reference lap (F7, studio.cross_reference + Session.load_reference seam):
# the Δ endpoint with a reference == cross-recording laptime diff (normalized-distance aligned),
# the track-mismatch / no-valid-laps guards, clear-reverts, the dormant byte-identity, and the
# map-overlay fit gate. Bare Sessions + synthetic loops; imports Session -> Qt, so run offscreen.
add_test(NAME test_cross_reference
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_cross_reference.py)
set_tests_properties(test_cross_reference PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Geometry track-match (studio.track_match): the haversine primitive + the two-check
# (location + footprint size) same/not-same verdict that admits an UNKNOWN-track reference by
# GPS geometry ("race a friend's GoPro off an unknown track"). Pure numpy/math — no pacer, no Qt.
add_test(NAME test_track_match
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_track_match.py)
# F2 Δ-engine dedup (studio.session LapCurve + project): pins the one alignment primitive the
# whole delta family now shares + the reference-baseline collapse (the reference and a local lap
# flow through ONE project()), from first principles on a synthetic session. No telemetry file;
# imports Session -> Qt, so run offscreen.
add_test(NAME test_delta_engine
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_delta_engine.py)
set_tests_properties(test_delta_engine PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# The extracted StudioWindow collaborators (studio.scrub_controller / studio.compare_controller):
# drive the scrub-coalescing + compare per-tick logic DIRECTLY on a bare Session + fake views
# (no full window / event loop). Imports Session -> Qt, so run offscreen.
add_test(NAME test_controllers
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_controllers.py)
set_tests_properties(test_controllers PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Controller<->view fan-out on the REAL CentralView (C3, the PR#80/#81 blind spot): builds the real
# CentralView + ScrubController/CompareController/PlaybackState as StudioWindow does, drives the
# production SIGNALS (compare_btn.click->compareToggled, plots.scrub*, video.positionChanged), lets the
# REAL ~30 Hz QTimer fire, and asserts no signal re-entrancy + consistent cross-widget state (C4
# single-source-of-truth). PACER_NO_MEDIA builds the inert media triplet; the two-lap session is the
# stadium-loop synthetic (no media file). Imports the studio widget tree -> Qt, so run offscreen.
add_test(NAME test_central_view_realqt
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_central_view_realqt.py)
set_tests_properties(test_central_view_realqt PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
add_test(NAME test_validate_wallclock
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_validate_wallclock.py)
set_tests_properties(test_validate_wallclock PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# The load ERROR paths (QA 2026-09-01: L10-01/L10-02/L10-04/L10-06/MAP-06): the failure-message
# classification table over five real malformed inputs (pure staticmethod, real Session.load
# exceptions), a failed RELOAD leaving the working session on screen instead of an endless loading
# card (real window + real worker QThread), drop-order/multi-drop-warning survival, and the untimed
# load notice being RE-decided when a timing edit changes the state it describes. Real Qt + a real
# worker thread, so run offscreen (reuses test_central_view_realqt's synthetic window fixture).
add_test(NAME test_load_failure
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_load_failure.py)
set_tests_properties(test_load_failure PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# What the app OFFERS while it loads (QA 2026-09-01: L10-03/L10-06/L10-08/L10-10): the demo fetch
# keeping the event loop alive + saying it is working (a slow resolver, a counted 16 ms heartbeat),
# the loading card's Cancel handing the session back with the in-flight result dropped, the
# zero-lap sentence matching ACROSS the status bar and the lap table's own empty state, and a
# dropped FOLDER of chapters being expanded and accepted. Real Qt + a real worker QThread.
add_test(NAME test_load_affordances
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_load_affordances.py)
set_tests_properties(test_load_affordances PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Corner model (studio.corners, F-corner): synthetic stadium-loop detection (count/
# positions/directions vs construction), distribution-derived threshold (+ noise
# stability), the corner/straight partition identity, and the CornerTable + map
# corner-marker overlay (offscreen Qt on stubs). Session wiring on a bare Session.
add_test(NAME test_corners
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_corners.py)
set_tests_properties(test_corners PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Data-export writers (studio.export_data, F11): laps/channels CSV schema + the
# float-repr round-trip exactness + the self-contained HTML report (XML-parsed, embedded
# PNGs decoded), on bare Sessions seeded via tests/_synthetic with a FakeLaps duck-type.
# Imports pacer transitively (the session chain), no Qt, no telemetry file.
add_test(NAME test_export_data
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_export_data.py)
# Consistency stats (studio.consistency, F6): σ / median-loss / ranking exactness vs direct
# numpy, the ⚠ dropout-lap exclusion on a bare Session, and the map corner-ring highlight
# (offscreen Qt). The old ConsistencyPanel UI moved into the Stats page — see test_stats.
add_test(NAME test_consistency
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_consistency.py)
set_tests_properties(test_consistency PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Driving channels (studio.driving, F5): synthetic-g-trace brake-pulse detection (+ hysteresis
# / short-blip rejection / flat-throttle zero false positives), coasting classification, the
# per-corner grip math, the distribution-derived thresholds, the Session accessors + caching
# (bare Session with a seeded g-meter), and the CornerTable Grip % column + map/plots brake
# overlays (offscreen Qt on stubs).
add_test(NAME test_driving
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_driving.py)
set_tests_properties(test_driving PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Auto coaching summary (studio.coaching / coaching_panel, F10): the per-corner median-time-loss
# ranking + the DETERMINISTIC dominant-reason selection (a planted apex-speed deficit ⇒ apex; a
# planted late-throttle coast the best lap lacks ⇒ coasting; an earlier/longer brake ⇒ braking;
# pure cross-lap spread ⇒ line), DETERMINISM (identical across calls / cache recomputes), the
# <MIN_LAPS friendly-excluded gate, the Session wiring on a bare Session (ranking + corner-entry
# projection onto the best lap + dropout exclusion), and the offscreen Opportunities dialog
# (populate, Go→jump_to(cid, entry), excluded state). Imports coaching_panel -> Qt, so run
# offscreen. No telemetry file.
add_test(NAME test_coaching
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_coaching.py)
set_tests_properties(test_coaching PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Video-overlay export PURE LOGIC (studio.export_video, F9): the lap-window trim math, the
# per-frame overlay-value lookup against a synthetic Session (same accessors the live readout
# reads), the ffmpeg decode/encode command construction, and the Renderer pump + cancel with the
# subprocess + ffprobe MOCKED — so NO ffmpeg is needed to run. The one real-render smoke is gated
# behind ffmpeg_available() + the D24 media file (skipped, not failed, on CI without them).
# Builds a QImage + a headless g-meter dial, so run offscreen.
add_test(NAME test_export_video
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_export_video.py)
set_tests_properties(test_export_video PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# g-meter frame-transform unit tests (synthetic IMU + GPS, no media file).
add_test(NAME test_gmeter
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_gmeter.py)
# g-meter OVERLAY display-layer tests (felt-force convention, shake filter, per-lap envelope).
add_test(NAME test_gmeter_overlay
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_gmeter_overlay.py)
set_tests_properties(test_gmeter_overlay PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Session library (F8, studio.library + studio.library_dialog): the pacer-free index — schema
# round-trip / float-repr exactness, the fingerprint upsert-not-duplicate rule, corruption →
# safe-empty self-heal, pb_series extraction — plus the offscreen Library dialog (list/sort,
# Open routes through a spy callback, missing-file greying, the PB best-vs-date mini-chart) on
# synthetic index dicts. Imports library_dialog -> Qt, so run offscreen. No telemetry file.
add_test(NAME test_library
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_library.py)
set_tests_properties(test_library PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Data safety (data-safety-undo-privacy): the timing-line UNDO history (Session pushes the prior
# lines before each edit + restores the latest through apply_timing_lines_latlon — segmentation +
# PB baseline recompute identically; no-op with no history; confirmed-state preserved; bounded
# stack) on a REAL pacer.Laps, and the library privacy controls (library.remove/clear + the guarded
# sidecar unlink + the offscreen LibraryDialog forget/clear DI wiring). Imports pacer + the dialog
# -> Qt, so run offscreen.
add_test(NAME test_data_safety
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_data_safety.py)
set_tests_properties(test_data_safety PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Speed-units toggle (studio.units / prefs, BUILD 5): the pure km/h↔mph conversion/label/format
# helpers, the persisted-choice round-trip + safe default, the theme readout formatters honouring
# the unit (km/h default byte-identical), coaching.reason_sentence + map_render's speed legend +
# the export OverlayConfig/readout using it, and the LapTable Entry header/value + CornerTable speed
# cells flipping to mph. Imports the studio widgets (LapTable/export) -> Qt, so run offscreen.
add_test(NAME test_units
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_units.py)
set_tests_properties(test_units PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Accessible cues + the "new personal best!" moment (feat/accessible-cues-pb-moment): the
# non-colour Δ arrow (▲/▼) + lap-table ★ best marks, theme's colour-blind PALETTE selector (one
# source: delta_colour / best_lap/best_sector colours / rainbow endpoints flip, default byte-
# identical) + its prefs round-trip, the LapTable best-cell recolour through the selector, and the
# library.pb_moment/pb_moment_for/pb_moment_text decision (beats prior PB on verified timing; NOT
# on provisional / first-ever / tie / slower) + the PBToast wording & progression-link routing.
# Imports the LapTable + the app toast -> Qt, so run offscreen. No telemetry file.
add_test(NAME test_accessible_cues
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_accessible_cues.py)
set_tests_properties(test_accessible_cues PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Theme colour SEMANTICS + legibility (fix/qa-theme-colour-semantics): an AST guard that no module
# constant in studio/ freezes a palette-swappable hue (the SERIES_BEST class of bug — the accessors
# are a CALL-TIME contract), the colour-blind map ramp clearing the CIE76 JND under a Machado-2009
# deuteranopia simulation and never scoring worse than the default ramp it replaces, WCAG AA on all
# 33 enabled text styles with C.text_muted confined to the WCAG-1.4.3-exempt disabled chrome, the
# failed-load message outranking the marketing subtitle (colour + size + ⚠), and the Δ dead-band
# clamp that stops '-0.00' reaching the screen or an exported frame. numpy + the CVD/Lab maths
# inline; builds the WelcomeView -> Qt, so run offscreen. No telemetry file.
add_test(NAME test_contrast
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_contrast.py)
set_tests_properties(test_contrast PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# The DIMENSIONAL half of the same guard (feat/design-tokens): every padding / margin /
# border-radius / min-height in theme._build_qss() is a SPACE_*/RADIUS_* step or a stated
# derivation of one (focus_pad for the ring compensation that produced `5px 11px`,
# ctrl_content_h for a min-height, which is checked by reconstructing the outer box it paints);
# an AST walk over studio/ for literal setContentsMargins/setSpacing/setFixedHeight/setFixedSize
# arguments, labelled by the class+method that owns each, with the not-yet-migrated view surfaces
# in a prose-justified EXEMPT list whose size is the progress metric; and the tokens' own
# consistency, including a LIVE check that a real button, combo and tab bar all paint at CTRL_H;
# and (feat/panel-header-toolbar) THE measurement the system exists to move — the four panel
# headers, on the real CentralView, at one declared PANEL_HDR_H instead of the shipped 32/38/43/43.
# Qt (offscreen), and that last check builds the production view over the synthetic session, so it
# IS in the PYTHONPATH foreach block at the bottom of this file (it was not while checks 1-3 were
# all it had).
add_test(NAME test_design_system
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_design_system.py)
set_tests_properties(test_design_system PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# The CONTROL half of the same guard (feat/control-vocabulary), i.e. what the app is BUILT from
# rather than what it measures: an AST walk over studio/ for setStyleSheet calls, labelled by the
# class+method that owns each, so that the only survivors are the per-datum colour merges a
# stylesheet cannot express (34 sites -> 7, each named in prose) and no new bare `color:` can creep
# back; every objectName and role property really having a rule in theme._build_qss (four had names
# and no rule at all); a LIVE check on the real CentralView that all eight icon buttons are one
# theme.ICON_BTN with one theme.ICON_PX glyph (two families shipped, neither painting its declared
# size); and setCheckable(True) belonging to widgets.ToggleButton alone (it was re-implemented at
# seven call sites, six of which disagreed). Qt (offscreen); the icon-button check builds the
# production view over the synthetic session, so it is in the PYTHONPATH foreach block below.
add_test(NAME test_inline_styles
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_inline_styles.py)
set_tests_properties(test_inline_styles PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Keyboard focus cues (fix/qa-theme-focus-cues): every one of the 15 tab stops must change PIXELS
# when it takes focus — four of them (the lap table, any CHECKED toggle, and the two pyqtgraph
# canvases) used to change none — the lap table's ring must paint on all FOUR edges rather than the
# 189px left-edge sliver a naive `:focus { border }` over a `border: none` base produces, and no
# control may change geometry / size hint / viewport / style contents rect between the two states
# (the ring is RESERVED, not added on focus). Real CentralView over the stadium synthetic, RGB
# frame diffs; needs the real theme QSS, so run offscreen. No telemetry file.
add_test(NAME test_focus_cues
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_focus_cues.py)
set_tests_properties(test_focus_cues PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Charts-panel honesty + legibility (fix/qa-charts-panel): the "Ideal lap" toggle live only where a
# click can draw (it was a 0-pixel no-op in the app's own default state), a dash pattern per identity
# slot so the legend still maps to a curve under deuteranopia WITHOUT recolouring the deliberately
# palette-independent CHART_SERIES, no km/h tick inside the ESTIMATED pedal strip (plus its on-chart
# caption), the legend anchored to the measured-emptiest corner with a stated drag affordance and a
# reachable hide threshold, and an empty state that names the next action while the three inert chart
# controls go dead. Real PlotsView + real pyqtgraph draw over a narrow stub Session; needs the real
# theme QSS, so run offscreen. No telemetry file.
add_test(NAME test_charts_panel
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_charts_panel.py)
set_tests_properties(test_charts_panel PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Window chrome / menus (fix/qa-app-chrome): Escape must restore a MAXIMIZED PANEL — the state four
# surfaces promise it restores and whose Escape branch was gated on isFullScreen() — at three window
# sizes × four panels, asserted on the splitter sizes, while still leaving video focus and window
# fullscreen; the welcome screen must not offer the four actions whose handlers early-return with no
# session (their menus had ZERO aboutToShow receivers) and must re-enable them at _build_ui, not on
# the next pull-down; a coaching Jump must mark + scroll to the corner row it landed on and must not
# persist its navigation as the user's tab preference; and the crash dialog must name APP_NAME
# (macOS gives it no window title). Real StudioWindow over the stadium synthetic; run offscreen.
add_test(NAME test_app_chrome
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_app_chrome.py)
set_tests_properties(test_app_chrome PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
# Shareable lap card (image) (feat/shareable-lap-card): the PURE card_data field extraction off
# Session accessors (best lap / Δ-to-ideal / top opportunity / track / date / unit), the km/h↔mph
# unit flip in the reason sentence, and the HONESTY verdict (provisional/no-lap ⇒ blocked;
# degraded ⇒ stamped, not blocked); render_card → a non-empty CARD_W×CARD_H QImage on both
# palettes, with/without a map thumbnail; and the app DI wiring (Export ▸ Lap card save through a
# monkeypatched QFileDialog, Copy lap card onto a monkeypatched clipboard, blocked-session greying,
# and the PBToast "Share your PB →" routing). Builds QImages, so run offscreen. No telemetry file.
add_test(NAME test_share_card
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_share_card.py)
set_tests_properties(test_share_card PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Session statistics (studio/stats.py, the Stats page): the pure reducers (moving time with
# the dropout-gap skip, path distance, wall-clock rendering, pace distribution, peak-g
# conventions, the half-open lap-window mask, sector medians) + the SessionStats service over
# fake DI callables (totals caching, per-lap speed/g/brake-coast reductions with the None-not-0
# no-signal rule, session Vmax, the g-g cloud's window restriction + stride cap, invalidate()
# dropping exactly the lap-level caches) + the Session.stats property wiring on a bare Session.
# Pure numpy on stubs; offscreen for the (future) panel tests' shared registration.
add_test(NAME test_stats
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_stats.py)
set_tests_properties(test_stats PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# LapTable COLUMN LAYOUT (fix/lap-table-columns, P5): the data columns are content-tight and one
# blank trailing SPACER column absorbs the panel's leftover width (the old stretched last section
# left a dead band with the Entry column pinned far right). Pins the sizing modes, the wide/narrow
# widths, the Lap column fitting its ▶/★/⚠ markers, the spacer staying LAST across sector-line
# adds/removes (holding no cell) and the unit flip, and sorting being untouched (both directions +
# the blank spacer header refusing to sort). Builds a real LapTable -> Qt, so run offscreen.
add_test(NAME test_lap_table_columns
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_lap_table_columns.py)
set_tests_properties(test_lap_table_columns PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# LapTable / CornerTable EMPTY + BASELINE + EXCLUDED states (fix/qa-lap-table-empty-states, QA
# sweep B11): the Corners page dashes the two Δ columns and names the lap when it is showing the Δ
# BASELINE itself (the model's documented ref=None self-zeros, not measurements — and a loaded
# cross-recording reference keeps every local Δ); a recording with no valid lap gets the Laps
# grid's wording instead of an unfollowable "Select a lap", both placeholders ending on one next
# action; the ⊘ excluded strip escalates to a warning voice above EXCLUDED_WARN_RATIO with the
# ratio in the words and both median distances on screen, reconciles its count against lap_count(),
# and expands to the COMPLETE list in a height-bounded scroll instead of 6 laps and a dead
# "+N more". Builds real LapTable/CornerTable widgets -> Qt, so run offscreen.
add_test(NAME test_lap_table_empty_states
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_lap_table_empty_states.py)
set_tests_properties(test_lap_table_empty_states PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Lap-panel AFFORDANCES (fix/qa-lap-table-affordances, QA sweep B12): sorting is reachable from the
# KEYBOARD — the header is a tab stop that rings the section Space would sort by, claims Space back
# from the window's play/pause shortcut while focused, and never walks onto the blank spacer, with
# the mouse route intact through the replaced header; the ★ carries its one convention as a tooltip
# on every cell and header that can wear it, without overwriting the dropout/provisional note
# underneath; the Corners rows declare the click that rings a corner on the map (pointing hand +
# a ROW-wide hover fill); and the Corners units are named on screen by a caption that costs the
# already-exhausted column budget nothing — the rejected in-cell "%" is measured here too. Real
# LapTable/CornerTable widgets under the REAL theme (the ring and the fill are pixels) -> Qt, so
# run offscreen.
add_test(NAME test_lap_table_affordances
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_lap_table_affordances.py)
set_tests_properties(test_lap_table_affordances PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Grid + timing-line write guards (fix/qa-grid-splitter-collapse, QA sweep B01): no splitter DRAG
# can collapse a panel to 0 px and no persisted zero can resurrect one, while the maximize gesture
# still collapses for real (it opts back in via _collapse_sizes — without that, non-collapsible
# splitters silently clamp setSizes([full, 0]) and a "maximized" panel keeps sharing the window);
# a timing-line placement that leaves no valid lap is written to NEITHER the sidecar (the loader's
# revert guard always rejects it, so the write only destroyed the last good line) NOR the undo
# stack (undo_timing_lines refuses to consume such a snapshot, which made Cmd+Z a permanent no-op);
# and a persisted mph preference reaches the Corners header tooltips through the CONSTRUCTOR seam,
# the one path where no unit-changed signal fires. Real CentralView -> Qt, so run offscreen.
add_test(NAME test_grid_and_timing_guards
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_grid_and_timing_guards.py)
set_tests_properties(test_grid_and_timing_guards PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# CHARTS/MAP header width budget (fix/qa-charts-header-budget, QA sweep B02): the bar must name
# the baseline the LOWER CHART draws at the app's own 1440x900 (the #125 regression hid it at every
# shipped width), no control may be centre-clipped at 1280x800 or at the column minimum (measured
# from the real QStyle content rects, never a padding estimate), the header's children may never
# overlap — the chip painting through the hero readout is the trap the naive fix walks into — the
# ⛶ buttons must clear 24x24, the map's sector buttons must name themselves on hover, and the hero
# must explain the Δideal zero it is structurally pinned to on the best lap. Real themed CentralView
# -> Qt (the QSS supplies the fonts every number depends on), so run offscreen.
add_test(NAME test_charts_header_budget
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_charts_header_budget.py)
set_tests_properties(test_charts_header_budget PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Coaching page layout + units (fix/qa-coaching-panel-layout, batch B24): the column budget at the
# app's own minimum (±σ yields before the prose column starves; the columns must FIT, so no
# horizontal scrollbar; the reason header must not paint hard-clipped — its QSS padding lives INSIDE
# the section, so a naive advance-vs-section test passes it), the row count as a viewport-driven
# floor rather than a literal 3 (the maximized page was 3 rows in 786 px against 11 ranked), every
# header aligned over its own column instead of centred 611 px away, the ±σ cell's missing unit, and
# the geometric gate on the ESTIMATED brake-point hint (an optimum a whole brake zone past turn-in
# is not a brake point). Real offscreen OpportunitiesPanel on the REAL theme — the QSS supplies the
# font metrics every width here is measured in. No pacer, no telemetry file.
add_test(NAME test_coaching_panel_layout
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_coaching_panel_layout.py)
set_tests_properties(test_coaching_panel_layout PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Help-menu dialogs (fix/qa-help-dialogs): the shortcut card's wrapping rows must get the height
# their text needs at ANY card width (the #119 regression painted a wrapped row's second line
# outside the row), every live binding on the window must have a card row (⌘O had none) with the
# glyphs Qt itself paints (⇧⌘S, not ⌘⇧S — asserted against the live QAction, so ⌃⌘F-vs-F11 is
# platform-agnostic), the About / Privacy cards must refuse to shrink below their own copy, and
# the privacy card must name every store the app writes. Real offscreen widgets on the REAL theme
# (font metrics decide the wrapped heights); no pacer, no telemetry file.
add_test(NAME test_help_dialog
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_help_dialog.py)
set_tests_properties(test_help_dialog PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# PB-toast hit targets + dismiss clock (fix/qa-toast-and-trackdb, QA sweep L10-07): every control
# on the "new personal best!" card must clear the 24x24 pointer-target floor — the ✕ stood 20x19
# and the progression link 19px tall, on the one card in the app that deletes itself while you aim
# at it — and the 6 s auto-dismiss must HOLD while the pointer is on the card. Real PBToast widgets
# on the REAL theme (the QSS padding is what every measured pixel comes from), so run offscreen.
add_test(NAME test_pb_toast
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_pb_toast.py)
set_tests_properties(test_pb_toast PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Export + library entry points (fix/qa-app-export-plumbing, QA sweep L12-02/L1-03/L12-04/L12-07/
# L12-08/L11-08): the overlay MP4 obeys the same timing-trust verdict as the lap card, a 0-valid-lap
# recording can no longer write a header-only CSV and reports success, the report's map grab keeps
# its key and loses the editing chrome, the options dialog quantifies both combos and persists them,
# Reveal-in-Finder reports either outcome, and Save-as-track asks before replacing another circuit.
# Real StudioWindow menus on the real theme (the tooltips are the assertion), so run offscreen; the
# prefs / library / track-DB / sidecar seams are redirected to a temp tree inside the test.
add_test(NAME test_export_gates
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_export_gates.py)
set_tests_properties(test_export_gates PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# The map's floating key (design wave 2, D5-03/D5-06/D1-10): one ordinary splitter drag took the
# map canvas to 72 px and Qt clipped 42 px off the TOP of the fixed 196x106 plate — the title row
# and the caret that are the only sign it collapses. The plate falls back to its title-only form
# and its corner is clamped now; the collapse is a preference rather than per-MapView state; and a
# labelled control in a panel toolbar carries a Phosphor glyph (chips exempt by name). The height
# claims are SWEPT 1 px at a time, real MapView + real CentralView on the real theme, so run
# offscreen; the prefs seam is redirected to a temp dir inside the test.
add_test(NAME test_map_key
         COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_map_key.py)
set_tests_properties(test_map_key PROPERTIES
                     ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# The Python tests must import the cmake-deployed bindings package (bindings/pacer/pacer,
# where the build drops the compiled module) WITHOUT relying on a pip/editable install of
# `pacer` in the interpreter's site-packages — on a fresh CI env there is none, and
# `import pacer` silently falls back to the C++ source dir pacer/ as an EMPTY namespace
# package (the tests sys.path.insert the repo root to reach `studio`), failing with
# AttributeError. PYTHONPATH makes the deployed package win deterministically in every
# install state; APPEND keeps the per-test QT_QPA_PLATFORM settings above intact.
foreach(pytest IN ITEMS test_scrub_conversion test_lap_timing test_chapters test_gapfill
                        test_import_ergonomics test_reference test_gps_source_bindings
                        test_laps_bindings test_ingest_equivalence test_sidecar test_track_db
                        test_session_pure test_sector_integrity test_load_internals
                        test_load_pipeline test_fit_start_line test_session_services
                        test_golden_synthetic test_studio_features test_rainbow_map test_map_render
                        test_map_chrome test_compare test_map_ghost test_cross_reference
                        test_delta_engine test_controllers test_central_view_realqt
                        test_load_failure test_load_affordances test_validate_wallclock
                        test_corners test_consistency test_gmeter test_gmeter_overlay
                        test_export_data test_driving test_coaching test_library test_export_video
                        test_data_safety test_units test_accessible_cues test_share_card test_stats
                        test_lap_table_columns test_help_dialog test_grid_and_timing_guards
                        test_lap_table_empty_states test_charts_header_budget
                        test_coaching_panel_layout test_focus_cues test_pb_toast test_app_chrome
                        test_charts_panel test_lap_table_affordances test_export_gates
                        test_golden_hermetic test_design_system test_inline_styles test_map_key)
    set_property(TEST ${pytest} APPEND PROPERTY ENVIRONMENT
                 "PYTHONPATH=${CMAKE_SOURCE_DIR}/bindings/pacer")
endforeach()
