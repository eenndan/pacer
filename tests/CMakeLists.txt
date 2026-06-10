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
endif()