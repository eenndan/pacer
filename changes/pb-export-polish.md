### Fixed
- Export names say which lap again (`GX010067_lap14_overlay.mp4`, `_session_overlay.mp4`,
  `_lap14_vs_lap15_compare.mp4`, `_lap14_card.png`); PNG frames get their own lap folder.
- A clip cut on the timing line now ends on its finish frame, which shows the lap time.
- All laps asks for a folder and confirms once before replacing files; a cancelled batch
  lists the files it kept.
- A cancelled PNG sequence removes its frames; re-exporting into a folder of frames confirms
  and replaces them instead of mixing two exports.
- The lap card's map is the best lap's own trace, drawn from data, not a grab of the live map.
- The disk-space refusal reads as one sentence and is not repeated behind Show Details.

### Changed
- Every export writes one line to the session log when it starts and one when it ends.
