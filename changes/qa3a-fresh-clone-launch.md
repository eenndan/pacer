### Fixed

- `pixi run studio` loads recordings and `--demo` on a fresh clone: the build now installs the whole
  bindings package into the environment, so no run needs `PYTHONPATH` (#436)
- A load that fails on Pacer's own error says so and names the log, instead of calling your file
  corrupt and sending you back to the SD card (#436)
