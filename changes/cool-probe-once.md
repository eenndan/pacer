### Fixed

- Export dialogs opened in quick succession share one ProRes VideoToolbox probe: 14 quick opens
  ran 14 probes at once before, and now run 1.
- A ProRes VideoToolbox session that fails to open is asked again on the next dialog or render
  instead of sending every overlay-only export to prores_ks for the rest of the run.
