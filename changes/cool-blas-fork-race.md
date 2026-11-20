### Fixed
- A load or a calculation can no longer freeze for good when an export starts ffmpeg at the same
  moment: numpy's maths now runs on one thread, which a new process can never strand.
