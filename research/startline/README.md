# Start-line derivation scripts (archived)

Working scripts behind the start/finish-line verification evidence in
`studio/docs/start-line-verification.md`. They lived in the gitignored scratch dir
`.startline_tmp/`, which is being deleted; the heavy artifacts there (~35 MB of PNGs and the
multi-MB `trace_*.json` dumps) are regenerable by re-running these scripts against the real
test sessions (`~/Desktop/D24` recordings), so only the scripts and the small result JSONs
(`satref*.json`, `plan_sf.json`, `georef_0060.json`, `cands_*.json`, `sweep_*.json`,
`val_*_current.json`) are kept. Copied verbatim — a historical record, not runnable tools
(this dir is excluded from ruff like the rest of `research/`).

How they fit together:

1. `extract_trace.py` dumps a recording's GPS trace + laps to `trace_*.json` (the cache every later step reads).
2. Georeferencing: `georef.py` (centerline-ICP, norm-image -> local), `satref.py`/`satref2.py`/`satref3.py` (iterations ending in the direct lon/lat -> satellite-pixel affine, `satref_direct.json`; checked by `verify_satref.py`), and `plan_georef.py` (official plan PDF -> lat/lon, locating its labelled "Start Finish" marker -> `plan_sf.json`).
3. Analysis: `analyze.py` (crossing-count geometry for candidate lines), `find_straight.py` (locates the main straight + the current line on it), `sweep.py` (re-segments laps per candidate line vs the transponder CSV -> `sweep_*.json`, `val_*_current.json`).
4. Rendering: `overlay.py`, `draw_on_sat.py`, `annotate_sat.py`, `plan_on_sat.py` draw trace/lines/plan onto the georeferenced satellite; `final_proof.py` produces the final 2-panel proof PNG.

The `_m3_*.py` scripts are unrelated to the start line: they are the behavior-identity
dump/compare harness used to verify the M3 studio refactor, archived here with the rest of
the scratch dir's contents.
