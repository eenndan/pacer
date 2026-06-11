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
find_package(Python COMPONENTS Interpreter)
if(Python_Interpreter_FOUND)
    add_test(NAME test_scrub_conversion
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_scrub_conversion.py)
    add_test(NAME test_lap_timing
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_lap_timing.py)
    add_test(NAME test_chapters
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_chapters.py)
    # MAP-ONLY GPS-gap reconstruction (studio.gapfill): pure Python + numpy, no telemetry file.
    add_test(NAME test_gapfill
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_gapfill.py)
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
    add_test(NAME test_studio_features
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_studio_features.py)
    set_tests_properties(test_studio_features PROPERTIES
                         ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
    # Compare-video Δ-badge math: session.delta_between (vs an arbitrary lap, cross-checked
    # against the hardcoded-vs-best delta_at_time). Imports Session -> Qt, so run offscreen.
    add_test(NAME test_compare
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_compare.py)
    set_tests_properties(test_compare PROPERTIES
                         ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
    # The extracted StudioWindow collaborators (studio.scrub_controller / studio.compare_controller):
    # drive the scrub-coalescing + compare per-tick logic DIRECTLY on a bare Session + fake views
    # (no full window / event loop). Imports Session -> Qt, so run offscreen.
    add_test(NAME test_controllers
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_controllers.py)
    set_tests_properties(test_controllers PROPERTIES
                         ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
    add_test(NAME test_validate_wallclock
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_validate_wallclock.py)
    set_tests_properties(test_validate_wallclock PROPERTIES
                         ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
    # g-meter frame-transform unit tests (synthetic IMU + GPS, no media file).
    add_test(NAME test_gmeter
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_gmeter.py)
    # g-meter OVERLAY display-layer tests (felt-force convention, shake filter, per-lap envelope).
    add_test(NAME test_gmeter_overlay
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/test_gmeter_overlay.py)
    set_tests_properties(test_gmeter_overlay PROPERTIES
                         ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

    # The Python tests must import the cmake-deployed bindings package (bindings/pacer/pacer,
    # where the build drops the compiled module) WITHOUT relying on a pip/editable install of
    # `pacer` in the interpreter's site-packages — on a fresh CI env there is none, and
    # `import pacer` silently falls back to the C++ source dir pacer/ as an EMPTY namespace
    # package (the tests sys.path.insert the repo root to reach `studio`), failing with
    # AttributeError. PYTHONPATH makes the deployed package win deterministically in every
    # install state; APPEND keeps the per-test QT_QPA_PLATFORM settings above intact.
    foreach(pytest IN ITEMS test_scrub_conversion test_lap_timing test_chapters test_gapfill
                            test_gps_source_bindings test_laps_bindings test_ingest_equivalence
                            test_sidecar test_studio_features test_compare test_controllers
                            test_validate_wallclock test_gmeter test_gmeter_overlay)
        set_property(TEST ${pytest} APPEND PROPERTY ENVIRONMENT
                     "PYTHONPATH=${CMAKE_SOURCE_DIR}/bindings/pacer")
    endforeach()
endif()