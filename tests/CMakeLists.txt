# THE TEST SUITE: every CTest registration, and the few properties that are not the default.
#
# ADDING A PYTHON TEST NEEDS NO EDIT HERE. Every tests/test_*.py is registered under its own name
# by the glob below and runs as `python tests/<file>.py`, with QT_QPA_PLATFORM=offscreen and the
# deployed bindings on PYTHONPATH. Name a test in THE EXCEPTIONS TABLE only when it needs something
# else. Its "why" belongs in its own module docstring; the per-test notes this file carried until
# 2026-09-24 are kept verbatim in registration-notes.md (history: nobody adding a test edits it).

# ---- C++: one Catch2 executable per test file --------------------------------------------------
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
# Seek-before-first-payload clamp, driven through in-memory stub RawGPSSources. Its hostile-input
# cases (a metadata track claiming decades, a damaged camera-name byte) mutate a COPY of a bundled
# clip at run time, so they get the samples directory.
add_pacer_test(test_gps_source      pacer::gps-source)
target_compile_definitions(test_gps_source PRIVATE
    PACER_GPMF_SAMPLES="${CMAKE_SOURCE_DIR}/3rdparty/gpmf-parser/samples")

# ---- Python ------------------------------------------------------------------------------------
# REQUIRED: the build needs the interpreter anyway (bindings codegen; the root CMakeLists pins it
# to the pixi env), so a machine without one fails the configure loudly instead of silently
# dropping every Python test from the suite.
find_package(Python REQUIRED COMPONENTS Interpreter)

# ================================== THE EXCEPTIONS TABLE ==================================
# ENVIRONMENT. The default suits a new test: QT_QPA_PLATFORM=offscreen, and PYTHONPATH pointing at
# the cmake-deployed bindings package — on a fresh env with no pip install, `import pacer` otherwise
# resolves the C++ source dir pacer/ as an EMPTY namespace package and fails with AttributeError.
# Neither costs a test that does not use it. These name the tests whose environment differs.
#
# No offscreen platform (the test imports no Qt):
set(_no_qt
    test_app_support_jail test_camera_support test_chapter_timeline test_chapters
    test_data_quality test_demo test_export_data test_export_disclosures test_fit_start_line
    test_gapfill test_gearing test_geometry_bindings test_gmeter test_gps_source_bindings
    test_imu_orientation test_ingest_equivalence test_landing_page test_lap_time_axis
    test_lap_timing test_laps_bindings test_layering test_load_internals test_parallel_suite
    test_prefs test_provenance test_reference test_rotation test_scrub_conversion
    test_sector_integrity test_session_pure test_sidecar test_temp_isolation test_timeline
    test_track_db test_track_match test_version)
# No PYTHONPATH (the test imports no pacer):
set(_no_bindings
    test_app_support_jail test_camera_support test_chart_instruments test_contrast
    test_data_quality test_demo test_export_pill_budget test_gearing test_glyph_vocabulary
    test_landing_page test_layering test_map_fit test_parallel_suite test_prefs
    test_temp_isolation test_timeline test_track_match test_version)
# + PACER_NO_MEDIA=1, the production widget tree over an inert media triplet (studio/player_pane.py):
set(_no_media
    test_chart_instruments test_command_palette test_demo_session test_load_lifecycle test_marks
    test_media_clock test_playback_rate test_provenance_panel test_quality_chip_trust
    test_quality_strip test_session_log test_synth_gopro test_welcome_first_touch)
# + PACER_CTEST_COMMAND / PACER_CTEST_DIR, for a test that lists this build's own registrations:
set(_ctest test_app_support_jail test_footage_checks)
#
# COST. ctest starts the highest COST first, but a FRESH build dir — every CI run, every new
# worktree — has no measured costs, so it ran in registration order and the slowest suites started
# last (CI's test step: 320 s against a 246 s floor, 2026-09-23). Seconds, measured on the dev Mac
# at -j4 on 2026-09-24. The footage checks (100) and the soaks (200) sit above these on purpose;
# their functions below say why, and where they are skipped, starting first costs a second.
set(_cost
    test_stats 60  test_export_video 56  test_central_view_realqt 40  test_pb_toast 35
    test_load_lifecycle 22  test_charts_header_budget 22  test_export_pill_budget 20
    test_first_run_path 18)
# ==========================================================================================

# A name in the table that is not a tests/test_*.py would silently configure nothing: fail instead.
foreach(_name IN LISTS _no_qt _no_bindings _no_media _ctest)
    if(NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/${_name}.py)
        message(FATAL_ERROR "tests/CMakeLists.txt names ${_name} in its exceptions table, but "
                            "there is no tests/${_name}.py — remove or rename the entry")
    endif()
endforeach()

file(GLOB _pytests CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/test_*.py)
foreach(_path IN LISTS _pytests)
    get_filename_component(_name ${_path} NAME_WE)
    add_test(NAME ${_name} COMMAND ${Python_EXECUTABLE} ${_path})
    set(_env "")
    if(NOT "${_name}" IN_LIST _no_qt)
        list(APPEND _env "QT_QPA_PLATFORM=offscreen")
    endif()
    if("${_name}" IN_LIST _no_media)
        list(APPEND _env "PACER_NO_MEDIA=1")
    endif()
    if(NOT "${_name}" IN_LIST _no_bindings)
        list(APPEND _env "PYTHONPATH=${CMAKE_SOURCE_DIR}/bindings/pacer")
    endif()
    if("${_name}" IN_LIST _ctest)
        list(APPEND _env "PACER_CTEST_COMMAND=${CMAKE_CTEST_COMMAND}"
                         "PACER_CTEST_DIR=${CMAKE_BINARY_DIR}")
    endif()
    if(_env)
        set_tests_properties(${_name} PROPERTIES ENVIRONMENT "${_env}")
    endif()
endforeach()
while(_cost)
    list(POP_FRONT _cost _name _secs)
    set_tests_properties(${_name} PROPERTIES COST ${_secs})
endwhile()

# ---- REAL-FOOTAGE CHECKS (G1) ------------------------------------------------------------------
# One registration per check, `footage.<check>`, running `<file>.py --footage <check>`: without its
# recording a check exits 77 and CTest reports it SKIPPED by name, not passed (CI has no footage).
# The mechanism is tests/_footage.py; a file's FOOTAGE_CHECKS and these lines are held one to one by
# test_footage_checks. LABELS: `pixi run test-footage` runs exactly these. TIMEOUT: one check loads
# several multi-GB recordings, far past the suite's --timeout. RESOURCE_LOCK: they are the
# ffmpeg-heavy ones, so they run one at a time, beside the light suites. COST: that one-at-a-time
# chain (~140-180 s on the dev Mac) is the suite's critical path unless it starts at once.
function(add_footage_test file check)
    add_test(NAME footage.${check}
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/${file}.py --footage ${check})
    set_tests_properties(footage.${check} PROPERTIES
                         SKIP_RETURN_CODE 77
                         LABELS footage
                         RESOURCE_LOCK footage
                         TIMEOUT 3600
                         COST 100
                         ENVIRONMENT "QT_QPA_PLATFORM=offscreen;PYTHONPATH=${CMAKE_SOURCE_DIR}/bindings/pacer")
endfunction()

add_footage_test(test_rainbow_map test_pedal_mode_paints_the_chart_band)
add_footage_test(test_video_view_compare test_real_media_pane_b_is_reference_at_lap_start)
add_footage_test(test_driving test_pedal_band_holds_each_braking_zone_whole)
add_footage_test(test_export_video test_real_render_smoke_if_ffmpeg_and_media)
add_footage_test(test_export_video test_real_chaptered_non_first_chapter_render_if_media)
add_footage_test(test_export_video test_real_render_quality_levels_if_media)
add_footage_test(test_ideal_sample_table test_the_table_still_matches_the_app)
add_footage_test(test_measured_figures test_the_coaching_tables_match_the_footage)
add_footage_test(test_measured_figures test_the_brake_habit_table_matches_the_footage)
add_footage_test(test_measured_figures test_the_braking_direction_table_matches_the_footage)
add_footage_test(test_measured_figures test_the_beat_rate_table_matches_the_footage)
add_footage_test(test_measured_figures test_the_focus_tables_match_the_footage)
add_footage_test(test_measured_figures test_the_floor_table_matches_the_footage)
add_footage_test(test_measured_figures test_the_refusal_record_matches_the_footage)
add_footage_test(test_validate_wallclock accuracy_mk)
add_footage_test(test_focus_list test_two_sandown_sessions_reach_a_verdict_from_the_debrief)

# ---- SOAKS -------------------------------------------------------------------------------------
# A probabilistic check too slow for every PR, whose root cause a fast deterministic guard in the
# same file already names. `soak.<check>` runs `<file>.py --soak <check>` and really runs only where
# PACER_SOAK=1 — `pixi run test-soak` (LABELS soak), and CI on pushes to main and on tags. Anywhere
# else it exits 77 and CTest reports it SKIPPED by name, not passed. COST: when one does run it is
# the longest test in the suite. TIMEOUT: a ceiling over its subprocesses' own 600 s bounds.
function(add_soak_test file check)
    add_test(NAME soak.${check}
             COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/${file}.py --soak ${check})
    set_tests_properties(soak.${check} PROPERTIES
                         SKIP_RETURN_CODE 77
                         LABELS soak
                         TIMEOUT 900
                         COST 200
                         ENVIRONMENT "QT_QPA_PLATFORM=offscreen;PYTHONPATH=${CMAKE_SOURCE_DIR}/bindings/pacer")
endfunction()

add_soak_test(test_compare_lifecycle test_toggle_compare_repeatedly_does_not_crash)

# KEEP THIS LAST. Every registration above runs with PACER_APP_SUPPORT_JAIL=1: the first store path
# any process in the test's tree resolves is a fresh temp dir (exported to its children, removed at
# exit), never ~/Library/Application Support/pacer — whether or not the test patched a seam. A test
# registered BELOW this loop misses it, and test_app_support_jail fails the build naming it.
get_property(_pacer_registered_tests DIRECTORY PROPERTY TESTS)
foreach(_pacer_test IN LISTS _pacer_registered_tests)
    set_property(TEST ${_pacer_test} APPEND PROPERTY ENVIRONMENT "PACER_APP_SUPPORT_JAIL=1")
endforeach()
