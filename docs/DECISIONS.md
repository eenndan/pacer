# Decisions — the owner's rulings, the defaults applied, and the freeze

Pacer is built by agents for one owner. Until this file, his rulings and the decisions taken for
him lived in untracked working logs, where "adopted in docs" could be written down and never
committed. This file is the tracked record. Three rules:

- **An agent never makes a ruling.** It records one only as the owner gave it, relayed and quoted
  with its date.
- **A default is recorded as "default applied <date>", never as his answer.** He can overrule any
  row at any time. The overrule is quoted here with its date, and what was built on the default is
  revisited.
- **A pull request that relies on a row cites its id** (for example RUL-11).

On 2026-09-28 the owner said "implement the plan … don't ask me questions, run until done". So no
ruling below was put to him: every default applied that day, not after the week the plan allowed.

**The ids this page cites.** RUL-n and S1-S6 are defined on this page. The others are kept so a
row can be traced to where it came from, and they name items in the maintainers' private notes,
which are not in this repository:

- R1-R14 (R11 here, R13 in [AGENT-GUARDRAILS.md](AGENT-GUARDRAILS.md)) and item 15-V1: the
  recommendations of the board review of 2026-09-23, and a part of item 15 on its list of owner
  acts.
- The FOLLOW ledger (called the board ledger in section 4) and PRODUCT-n: the ledger that tracked
  what came of the board review, and the product findings, both from the review of 2026-09-28.
- O5 and O6: two of the questions the 2026-09-28 review put to the owner; RUL-6 and RUL-7 here.
- ADV-n: the coaching-advice findings of the hands-on QA round of 2026-09-28.
- FIRST-OPEN-LOOP-n, TRUTH-n, COACHING-n, GATES-n, PROCESS-n, LONGEVITY-n, FRONT-DOOR-n: the work
  packages of the 2026-09-28 fix plan. A package that has merged is the pull request whose title
  starts with its id.
- D8: that plan's decision to check each input only the owner can give once, when the batch that
  needs it is dispatched (section 5).

## 1. Rulings

| Id | Ruling | Default | Status | What follows |
|---|---|---|---|---|
| RUL-1 | North-star: adopt "a craft showcase whose proof is that its author races with it", or keep July's | keep July's "portfolio / craft showcase" | default applied 2026-09-28 on the owner's instruction not to be asked | The README and landing page keep today's wording. |
| RUL-2 | A circuit results sheet as an optional input (one DATA TRUST line, plus his kart number) | no | default applied 2026-09-28 on the owner's instruction not to be asked | No circuit-timing row is built. |
| RUL-3 | Premises, not choices: a sprint is arrive-and-drive (a different fleet kart each time), and the Sandown 3h recording's camera stayed in one kart across its driver changes | none — premises | unconfirmed 2026-09-28; nothing further is built on them | The kart-number record and the pre-promoted focus rest on the arrive-and-drive premise, still unconfirmed (section 4). |
| RUL-4 | A per-stint driver label on an endurance recording | no | default applied 2026-09-28 on the owner's instruction not to be asked | No stint labels. |
| RUL-5 | The race-day agreement: a 10-minute talk, plus two counts he pastes from the app's log | the store listing only: no talk, no counts | default applied 2026-09-28 on the owner's instruction not to be asked | The race-day read reports S2 and S4 as not observable, and holds no talk (S6); section 3. |
| RUL-6 | The focus loop: keep it, demote it, or turn it off | frozen as is until the freeze end; then keep it | default applied 2026-11-12 on the owner's instruction not to be asked: keep (frozen as is from 2026-09-28 to the freeze end, 2026-11-09) | FIRST-OPEN-LOOP-15 changes nothing; FIRST-OPEN-LOOP-8 builds, and -10 waits for a race day (section 6). |
| RUL-7 | Lead the debrief with the lap ("Export best lap" and "Compare with your PB" on any day) | frozen as is until the freeze end; then no | default applied 2026-11-12 on the owner's instruction not to be asked: no (frozen as is from 2026-09-28 to the freeze end, 2026-11-09) | The lap-first debrief buttons are not built: FIRST-OPEN-LOOP-12 and -14 are recorded as not built. |
| RUL-8 | The theme sentence names the one corner that decides it | frozen as is until the freeze end; then build it | default applied 2026-11-12 on the owner's instruction not to be asked: build (frozen as is from 2026-09-28 to the freeze end, 2026-11-09) | COACHING-4 builds: the sentence changes after the freeze. |
| RUL-9 | Footage consent: publish a telemetry slice, a 6-8 minute sample, or neither | neither; the demo stays synthetic | default applied 2026-09-28 on the owner's instruction not to be asked | No recording or telemetry is published beyond the approved stills and the 20 s clip (§4 item 6); the next demo is synthetic too. |
| RUL-10 | Track the agents' `.claude/settings.json` in git, or keep it untracked | untracked | default applied 2026-09-28 on the owner's instruction not to be asked | The settings stay out of git; [AGENT-GUARDRAILS.md](AGENT-GUARDRAILS.md) is their deny rules' reference copy. |
| RUL-11 | Packaging: keep the macOS `.app` (then CI would launch it on every tag) or drop it (source only) | drop the step and every claim of a working `.app` | default applied 2026-09-28 on the owner's instruction not to be asked: drop | Done 2026-09-30 (LONGEVITY-2, #497): the tag job no longer builds an `.app`, and no page claims one works. `packaging/` stays as an ungated local recipe that no CI job builds or launches ([PACKAGING.md](PACKAGING.md)). |
| RUL-12 | The showcase release: 0.6 or 1.0 | 0.6 | default applied 2026-09-28 on the owner's instruction not to be asked | The next release is 0.6.0. |

RUL-6 to RUL-8 were not re-asked (his instruction of 2026-09-28). The freeze ended on 2026-11-09,
and their freeze-end defaults were applied 2026-11-12; any reply of his overrules them (section 6).

## 2. Owner acts

Only he can do these. None has a default, and the work goes ahead without them. This public page
names the acts; which of them are done, and any act that concerns only his own files, are tracked
in the maintainers' private notes, not here.

| Act | Note |
|---|---|
| Save the Sandown Club Speed heat pages of 19 Jul, 30 Aug and 19 Sep, whole days | Three accuracy rows at a second circuit wait on them. |
| Upload `docs/media/og.png` as the social preview, and pin the repository | The upload waits for the showcase release's final `og.png` and is asked after that release; the pin waits on nothing. The upload is a copy: re-upload whenever `og.png` changes. |
| A profile name and bio, or a stated choice to stay anonymous | |
| Turn off the unused desktop-app connectors for this project; remove gitnexus | |
| The clip's upload URL, when an agent asks | |
| The About line's headline number, when an agent asks | |
| The board review's real-screen pass (item 15-V1) | Nothing waits on it. |

Two permissions had no default either. Publishing the synthetic `demo-data-v2` beside v1 (a new
release asset, never replacing it) is taken as granted by his "implement the plan … run until
done" (2026-09-28), as `demo-data-v1` and the releases since v0.3.0 were published under his
2026-09-24 mandate. Deleting the stale remote branch `b4/media-reshoot` is **not** done: it holds
two unmerged commits, and nothing needs it gone.

## 3. Standing decisions

### The first-open freeze

**(a) Window.** From 2026-09-28 until the next new recording or Monday 2026-11-09, whichever comes first.
Until then the first-open, focus and PB surface below changes only through the allowed list (c).
It ended on its date, 2026-11-09 (section 6).

**(b) Frozen surface, function by function.** A changed line inside any of these, on either side of a diff:
- `studio/library_controller.py`, `LibraryController`: `update_library`, `_pb_decision`,
  `_land_deferred_verdict`, `_land_named_verdict`, `refresh_library_entry`, `pre_promote_focus`, `focus_add`,
  `focus_replace`, `focus_remove`, `degraded_notice`, `debrief_pb_line`, `offers_pb_compare`,
  `show_pb_moment`, `mark_sessions_dry`, `_records_changed`; the module function `previous_pb_missing_text`.
  (These hold the verdict, pre-promotion and PB paths: `opened_new`, `previous_pb`, `pb_standing`.)
- `studio/focus.py`: the whole module.
- `studio/app.py`, `StudioWindow`: `_land_on_debrief`, `_session_notice` (the first-open clauses),
  `_sync_coaching_menu` (the PB item), `_compare_with_previous_pb`.
- `studio/central_view.py`, `CentralView`: `show_debrief`, `is_debrief`, `_maximize_debrief`, `_end_debrief`.
- `studio/coaching_panel.py`: the class `DebriefBlock`, and `OpportunitiesPanel.set_debrief`.
- `studio/library.py`: `pb_moment`, `previous_pb`, `pb_standing_for`, `pb_moment_for`, `pb_moment_text`,
  `pb_standing_text`.
- `studio/share_card.py`: `pb_mark`. `studio/session_record.py`: `is_empty`, `comparable`,
  `put_if_blank_and_save`.

**(c) Allowed inside the freeze.** Each package may touch only the frozen functions named beside it.
- FIRST-OPEN-LOOP-1 (a sector edit no longer confirms the timing): none; its change sits in
  `CentralView._on_lines`, a new module helper and `studio/session.py` docstrings.
- FIRST-OPEN-LOOP-2 (degraded GPS at an unnamed circuit promises nothing): `update_library` (the
  `waiting_for_name` line) and `degraded_notice`.
- FIRST-OPEN-LOOP-3 (four INFO log lines): `_land_on_debrief`, `_compare_with_previous_pb`, `focus_add`,
  `focus_replace`, `focus_remove`, `mark_sessions_dry`; log calls only, no copy or behaviour change.
- FIRST-OPEN-LOOP-5 (a PB inside timing precision reads "level with your best"): `pb_moment_text`,
  `pb_standing_text`, `pb_mark`; text only.
- COACHING-3 (ADV-2), COACHING-1 (ADV-1, with ADV-4 as its necessary companion: the line row states the
  spread its gate reads), COACHING-2 (ADV-1, the phase clause): none; `DebriefBlock` and `set_debrief` stay
  as they are.
- GATES-4, GATES-5, GATES-6 (the type ratchet): any function above, annotations only (the code with its
  annotations removed is unchanged, and golden does not move).

**(d) The check.** Before a PR merges, its changed lines (`git diff -U0`, merge base to head, both sides) are
mapped to their enclosing function with Python's `ast` and compared with (b) and (c). A changed line inside
a frozen function, from a package not allowed there, holds the PR until the freeze ends. A file name alone
is not the check.

**(e) The race-day read, registered in advance.** Under RUL-5's default it is a read-only listing of the
app's store: file names and dates, with nothing opened. Each signal is reported on its own line. No single
signal counts as success, and none is inferred from another.
- S1: `session_records.json` exists.
- S2: the focus list edited by hand (the "by hand" log lines of FIRST-OPEN-LOOP-3) against the app's default
  left as it was. The log sits in the app's store, which no agent reads, so a listing cannot count it: under
  RUL-5's default (applied 2026-09-28: store listing only, no talk, no counts) it is reported
  "not observable". If FIRST-OPEN-LOOP-3 has not merged before the freeze ends, it is "not observable", never
  "no edits".
- S3: `marks.json` exists.
- S4: where the export was started from (today's "export started" log line; its entry field once
  FIRST-OPEN-LOOP-14 exists). A log count like S2; under RUL-5's default, "not observable".
- S5: a focus verdict. Reachable only with a second session at the same track and session records on both
  sides; otherwise it is reported "not reachable", never "not used".
- S6: a 10-minute talk, only if RUL-5 is answered yes. Under its default the read is the store listing alone.

**(f) What ends it, and what follows.** The first of the next new recording or 2026-11-09 ends the freeze.
The read (e) is then recorded and sent to him as information. RUL-6..RUL-8 are not re-asked (his
instruction of 2026-09-28); their re-ask defaults apply at the freeze end, and any reply of his overrules
them:
- RUL-6 (O5, the focus loop): keep it. FIRST-OPEN-LOOP-15 changes nothing; FIRST-OPEN-LOOP-8 and -10 build.
- RUL-7 (O6, lead the debrief with the lap): no. FIRST-OPEN-LOOP-12 and -14 are recorded as not built; this
  ruling holds them, not the freeze surface.
- RUL-8 (the theme sentence names the deciding corner): build. COACHING-4 builds after the freeze.
- Needing only the freeze end: FIRST-OPEN-LOOP-6, -7, -9, -11, -13, -16, -17, TRUTH-8, TRUTH-12,
  COACHING-5, and PROCESS-5 (two frozen-surface docstrings).
- Not bound by the freeze: TRUTH-9, TRUTH-10 and TRUTH-11 (outside (b)). TRUTH-9 and -10 change what the
  debrief shows, so they merge before the next new recording or wait until the read (e) is recorded.

### The board review's restraint pass (R11)

Closed 2026-09-28 on the FOLLOW ledger's count of the tile target, not re-verified independently
(PRODUCT-8 is unverified). No further restraint pass; the 13-section ceiling on the demo still binds.

## 4. Decided for the owner, 2026-09-24 to 09-28

Under his "act fully autonomously … act as CEO, CTO, COO, CPO" (2026-09-24), and "don't ask me
questions, run until done" (2026-09-28). Each is open to his overrule.

**Proxy decisions** (the board ledger's ten, in its order, plus one):

1. The original recordings write-locked (2026-09-24).
2. The owner's checkout moved to `main` (2026-09-24).
3. A branch ruleset on `main`: a pull request and green CI (2026-09-24).
4. Merged branches deleted on merge, and the old ones pruned (2026-09-24).
5. The repository's website field set to the landing page (2026-09-24).
6. The Sandown stills and a 20 s clip of the best lap approved for the public pages (#385; the
   stills re-shot in #441).
7. Footage consent: no recording or telemetry published; the demo and the CI fixture are synthetic
   (#371). Now RUL-9.
8. v0.3.0 approved and published (#390).
9. The engineering notes' candour kept at summary level (#388).
10. Exports stay beside the footage (2026-09-24).
11. Extra: the accuracy claim re-proven at MK from its public timing; Sandown left for him (#382).

**Two CPO calls**, both in #399: the "brake N m later" hint retired from Coaching, after the
measurement that refused it (#395); and the braking-direction line shown only where it survives a
per-recording Holm correction.

**Built on his mandate, on an unconfirmed premise**: the kart-number session record (#386) and the
focus list pre-promoted at a first open (#391) assume arrive-and-drive sprints, the premise RUL-3
records as unconfirmed.

**Two corrections**: the north-star was logged as "adopted in docs as a hierarchy" on 2026-09-24
but never committed (RUL-1 now records it); and no GitHub social preview was ever uploaded, though
#441's description assumed one (checked 2026-09-28).

## 5. Recorded before the 0.6.0 release, 2026-09-30

The showcase release (RUL-12) is cut with these outcomes, each recorded 2026-09-30. D8's checks
look for an input once; what is missing then rides the next release, so no act holds this one.
These are what each check found when its batch was dispatched, not a status of section 2's acts.

- **The Sandown timing sheets (D8, first check):** not in hand at their check, so FRONT-DOOR-12's
  three accuracy rows at a second circuit ride the next release, and 0.6.0 says "at one circuit".
- **The demo (D8, second check):** `demo-data-v2` was published 2026-09-30 as a pre-release beside
  `demo-data-v1`, which stays, under the permission section 2 records as taken: a decision taken
  for him, not his answer. The plan's demo measure, a corner worth at least 0.3 s, is met: the
  demo's debrief ranks its planted corner first, at 0.36 s.
- **The clip's upload URL (D8, third check):** not given, so FRONT-DOOR-9 (a player on the README's
  first screen) closed at its gate; the README keeps its text link to the clip.
- **The author box (D8, fourth check):** its wording was not approved, so FRONT-DOOR-13 (five lines
  on who built Pacer, and how) closed at its gate; the pages keep today's wording.
- **PROCESS-8** (R13's restore test): not run before this release; it is recorded here when it runs.
- **PROCESS-10** (the mutation teeth, re-run 2026-09-30): the core suites kill all 34 planted core
  defects, up from 31 at the 2026-09-28 review.
- **Owner acts and late replies:** none reached this record between 2026-09-28 and this release
  beyond the four checks above, and no reply overrules a default of section 1: every row stands.

## 6. Recorded after the freeze, 2026-11-12

Each item below is recorded 2026-11-12.

- **The freeze ended on its date, 2026-11-09**, with no new recording opened before it.
- **The race-day read (S1-S6, section 3 (e)) could not be taken:** no new recording was opened in
  the app, so there is no race day to read. No signal is reported, and none is inferred.
- **RUL-6 to RUL-8 were not re-asked** (his instruction of 2026-09-28), so their freeze-end
  defaults apply, each a default applied 2026-11-12 on the owner's instruction not to be asked:
  - RUL-6, the focus loop: keep it. FIRST-OPEN-LOOP-15 is then a no-op.
  - RUL-7, lead the debrief with the lap: no. FIRST-OPEN-LOOP-12 and -14 are not built.
  - RUL-8, the theme sentence names the deciding corner: build. COACHING-4 builds.
- **What came in since the 0.6.0 release:**
  - v0.6.1 was released 2026-11-12 (#509 to #525).
  - CI moved to macos-15 after GitHub stopped accepting macos-14 (#522).
  - FIRST-OPEN-LOOP-10 waits for a race day.
  - TRUTH-8 and TRUTH-12 wait for a race day, and for the recordings they measure on to be
    reachable again: they are not reachable at their configured paths.

## 7. Recorded at the 0.7.0 release, 2026-11-13

- **v0.7.0 was cut 2026-11-13** from #526 to #539: one first-open verdict with a state for a refused
  Library write, the PB line ordered by when sessions were recorded, a second camera's recording
  kept apart, no compare offered against footage known missing, one focus-list gate and a theme
  sentence that names its deciding corner. The real-footage checks and the manual half of the
  golden gate could not run for it: the recordings are not reachable at their configured paths.

## 8. Recorded at the start of the 2026-11-14 campaign

On 2026-11-14 the owner gave a new instruction for a long autonomous campaign: "The goal: make
the app much, much cooler." and "I will not be answering questions during the session. Decide,
write each decision down, and keep going." Each item below is recorded 2026-11-14.

- **What it changes.** The playbook's Product rule (section 5 of
  [AGENT-PLAYBOOK.md](AGENT-PLAYBOOK.md): a product pull request needs a defect the owner hit or
  a ruling he made, and holds `studio/*.py` net lines at 0 or fewer, until the race-day
  experiment reports) is lifted for this campaign by that instruction: product pull requests may
  add capability. Every other rule stands: measure first, honesty, refusals, one focused change
  per pull request, the gates. The first-open freeze had already ended (section 6).
- **Defaults applied 2026-11-14 on the owner's instruction not to be asked**, each open to his
  overrule:
  - The first-open debrief may gain a picture (the best lap and the track with the focus
    corners), but its copy does not change and no lap-first button is added: RUL-7's default
    stands.
  - A look that would change the app's or an export's identity ships as an option, with today's
    look kept as the default.
  - Nothing is published outside the repository beyond tags and GitHub releases of the app, at
    most one per wave that a user can see.
  - The tags rule of the playbook's section 5 ("only on the owner's use / a capability an
    evaluator can see") is read as met by each such wave's visible capability.
- **Not built, waiting on the owner:**
  - A track-surface (bump) map from the vertical accelerometer: a measured, repeatable signal on
    one recording; whether it earns a surface is his call.
  - A coaching "line" lever from the apex lateral offset: supported on one recording only, so it
    needs a second recording at the same circuit to replicate before it may ship.

## 9. Recorded at the 0.8.0 release, 2026-11-14

- **v0.8.0 was cut 2026-11-14**, the first-look release, from #541 to #550 (#549 waits for the
  next wave). #543 to #548 are Wave 1 of the campaign section 8 records: a picture on the
  first-open debrief (the best lap's time beside its speed-coloured line, the focus corners
  ringed), a Δbest column in the lap table, the map's lap line as a hero stroke with brake points
  and a start/finish mark of their own, CORNERS BY LAP as a heat-map, and corner labels on the
  speed chart with every Δ naming its baseline. #550 fixed a regression of #545 before it was
  released (the lap grid fits beside its sector columns again). #541 shortened the README's
  acknowledgement and #542 moved a CI export test onto software decode.
- **What could not run for it.** One recording the real-footage checks take is on this machine,
  and five checks ran on it: the three render checks and the two pedal-band checks. Eleven
  reported Skipped because their recordings are not here: the compare-reference check, the
  ideal-lap table check, the seven measured-figure table checks, the MK accuracy check and the
  two-Sandown-sessions verdict check. The manual half of the golden gate did not run; no change in
  this release touched the core, and the synthetic golden gate passed.
- **The media.** `debrief.png` was re-captured on the synthetic demo, now with the picture, and
  `accuracy.png` from its numbers. `hero.png`, `map.png` and `og.png` show the look before 0.8.0
  and are waived as stale: their recording is not on this machine, and re-shooting them from the
  one that is would publish stills of a recording RUL-9 did not approve. That waits for the
  owner's word; the pages say those pictures are v0.7's. The other five files are renewed:
  0.8.0 changed nothing they show.
- **Closed without a change.** A package of four reported window defects found none to fix:
  three were artefacts of the offscreen test screen and did not reproduce in the window itself,
  and the fourth was already fixed on main.

## 10. Recorded at the 0.9.0 release, 2026-11-15

- **v0.9.0 was cut 2026-11-15**, the it-moves release: 9 pull requests from #549 to #562, Wave 2
  of the campaign section 8 records. The Lap race (#549 its core, #553 the map and its card), a
  live compare Δ on the seam and gain shading between two speed traces (#552), a corner hover
  link between the speed chart and the map (#554), a tail behind the map's playhead (#555), a Δ
  readout that rolls on a seek and a Reduce motion switch (#557), and the keys that now say why
  they did nothing (#561). #559 and #562 are refusals only.
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **The Lap race shows packs, not a rank ladder** (#549, #553). On a real 36-lap session most
    adjacent laps sit inside the tie threshold for most of the race, so a pack is the laps within
    it of the pack's first lap, and only that lap has a position. On an unconfirmed start line the dots still run, but unranked, with
    no intervals and no gap to the best. On a recording timed on the video clock the threshold
    widens to 0.15 s. There is no label on each dot (and no follow-cam: superseded by the addendum
    below, #576).
  - **Motion is the Δ roll only** (#557): after a seek within one lap the Δ readout counts to its
    new value, never while playing and never across a lap or baseline change. View ▸ Reduce
    motion turns it off, and until the user chooses, it follows macOS's own Reduce Motion. The
    fade of the lap you left on the map and the slide of the race card's rows were measured and
    not built: [refused-2026-09.md](../studio/docs/refused-2026-09.md) §28 (#562). The fade can
    come back as a fade built on the map's own colour buckets whose added frame cost fits the
    ~2 ms transition budget #557 set; that section names it.
  - **Chart antialiasing stays off**: [refused-2026-09.md](../studio/docs/refused-2026-09.md)
    §23, measured in #552.
  - **Gain shading is drawn only on the distance axis** (#552): at the same time the two karts are
    at different places, so the time axis shades nothing and its legend says why. Where the speed
    gap is within twice the noise of two traces (±1.75 km/h), or the Δ-rate disagrees with it,
    the band stays unshaded.
  - **The clip and share-card ideas of the design pass were refused with their numbers** (#559):
    a predicted lap time, a scrolling Δ-trace band, sector chips and per-corner cells on the share
    card, sections 24 to 27 of the refusals record.
- **What could not run for it.** As at 0.8.0, one recording the real-footage checks take is on
  this machine, and five checks ran on it: the three render checks and the two pedal-band checks.
  The same eleven as at 0.8.0 reported Skipped because their recordings are not here. The manual half of the golden gate did
  not run; no change in this release touched the core, and the synthetic golden gate passed.
- **The media.** `accuracy.png` and `debrief.png` were re-captured, both byte-identical to
  0.8.0's: nothing they show changed. `hero.png`, `map.png` and `og.png` are waived again as
  stale, for 0.8.0's reasons, and they do not show 0.9.0's map either: the playhead tail, the
  hovered-corner ring and the Lap race. The other five are renewed: 0.9.0 changed no Stats-page
  module and not the core, and its one export change moved the map inset's comet tail from
  `export_video.py` to `timeline.py`, its body the same but for a typing cast. A Lap race still on the demo was tried
  and not published: its 14 laps run within about 0.9 s of each other, one clump of dots too
  small to read at the width the pages show it.
- **Open for the owner.** Re-shoot the landing stills from another recording, or keep the
  waivers? The one recording on this machine is not one RUL-9 approved for publishing.
- **Addendum, 2026-11-16: the Lap race may follow the field** (`cool/race-follow`), superseding
  "there is no follow-cam" above. That default rested on a range change per tick being a full
  repaint, and it is not one. Measured interleaved (the plain race, Follow and fixed 30, 60, 90
  and 150 m views each a window of their own, all advanced on the same tick in a random order;
  the demo at the 917×230 map of a 1440×900 window; 4,605 ticks each; offscreen raster at a load
  of 14 to 16), the main thread's CPU time per tick was 5.9 / 9.2 / 11.1 ms plain and
  9.1 / 12.9 / 15.0 ms with Follow (median / p95 / p99), and on the same tick Follow cost
  +3.1 / +5.9 / +7.6 ms over the plain race: the +8 ms budget holds at the median and p95 and is
  near its edge at p99, and a fixed width costs the same to within 0.5 ms. The wall-clock p95
  was 20.0 ms plain and 28.6 ms with Follow on that loaded machine; 141 of Follow's 156 ticks over
  33 ms used under 16 ms of CPU (descheduled, not working). Compositing on screen was not
  measured. It ships as an option, off by default and remembered once chosen (the race card's
  crosshair button, View ▸ Follow the race, ⇧R): off by default as the conservative default
  applied on the owner's instruction not to be asked, not for its cost. The view centres on the
  middle 80 % of the dots and the selected lap's dot at the narrowest of 30 to 150 m that holds
  them, eased in race time inside a dead-zone box (a pause or the finish completes the zoom), with
  a scale bar; with it off a race never touches the view, and leaving puts the view back exactly.
  Open for the owner: should it be on by default?

## 11. Recorded at the 0.10.0 release, 2026-11-16

- **v0.10.0 is the it-leaves-the-app release**: 6 pull requests from #556 to #571, Wave 3 of the
  campaign section 8 records. A ghost of the best lap on the export's minimap (#556) and corner
  cards on the clip (#558), both Extras of the overlay dialog; the lap card's track with the
  start/finish mark and the way the laps run, corner labels, the biggest-opportunity corner ringed
  and a footer that says how the time was taken, and the same start/finish mark on the debrief's
  picture (#560); a lap-strip card (#564); a Broadcast overlay Style with a timing tower, a Δ bar,
  a big speed and a bigger minimap with corner labels (#565); and the QA fixes before the release
  (#571): the ghost drawn as a ring round the kart dot when it is beside it, a "LAST" line with
  the finished lap's time on whole-recording Broadcast exports, and layouts that never fail an
  export (the honesty stamp falls back to the Classic stamp's place; a layout failure says so).
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **Classic stays the default Style, unchanged** (#565). With Style on Classic and no Extras
    ticked, the painter never reaches the new `studio/overlay/` package, and the export decodes frame-identical to
    0.9.0's: a 540p libx264 render of a demo lap from v0.9.0's `studio/` and from 0.10.0 had equal
    `ffmpeg -f framemd5` on all 1365 frames (and the files were byte-identical), and
    `tests/test_export_ghost.py` holds it at the painter level.
  - **The Extras are off in Classic and on in Broadcast, remembered per style** (#556, #558,
    #565): each style keeps its own two ticks, so nothing ticked in Broadcast carries into a
    Classic export.
  - **The Δ bar's scale is each lap's own** (#565): the first round step above that lap's
    largest |Δ|, printed at both ends, so one slow lap does not flatten the others on a
    whole-recording export and the bar never clips.
  - **Corner cards use the 0.07 s rule** (#558): inside the timing precision, decided on the
    printed Δ, a card reads "≈ best" in neutral white. Not the Stats ▼ step, under which the
    design lane measured that 95 % of cards would read "≈".
  - **The lap-strip card's heights are proportional to the gaps** (#564): the best lap is a tick
    and a ★ on the zero line, and a bar is as tall as its time off the best. A lap past the Laps
    table's bar scale is cut at a dashed line and named in the legend with its real gap.
  - **The LAST line names its baseline** (#571): its Δ is always to the best lap, so it reads
    "Δbest" even under a tower reading "Δref". Under provisional timing it shows the time only,
    as the Laps table blanks its Δbest column there.
- **The media.** `accuracy.png` was re-captured, byte-identical to 0.9.0's. `debrief.png` was
  re-captured on the synthetic demo: its picture gained the start/finish mark and its arrow
  (#560). `lapstrip.png` is new, the demo's lap-strip card through the app's own writer; two
  captures were byte-identical. `hero.png`, `map.png` and `og.png` are waived again as stale, for
  0.8.0's reasons. `data-trust.png` and `ideal-lap.png` are renewed: 0.10.0 changed no Stats-page
  module and nothing under `pacer/`. The export stills `overlay.jpg`, `best-lap.jpg` and
  `best-lap.mp4` are renewed with a different reason from 0.9.0's: 0.10.0 did change export code,
  but a Classic export with no Extras is frame-identical to 0.9.0's (above), so the pages still
  show the default look. No Broadcast still was published: the demo's picture is a busy title
  card, and a frame of real footage is the owner's call.
- **What could not run for it.** As at 0.9.0, one recording the real-footage checks take is on
  this machine, and five checks ran on it: the three render checks and the two pedal-band checks.
  The same eleven reported Skipped because their recordings are not here. The manual half of the
  golden gate did not run; no change in this release touched the core, and the synthetic golden
  gate passed.
- **Open for the owner.**
  - Should Broadcast be the default Style?
  - Should the Extras open ticked in Classic too?
  - The Classic lap strip colours a Δ of +0.06 s as lost, while the Laps table and Broadcast show
    it neutral inside the 0.07 s precision. Making Classic agree would change Classic's pixels, so
    it was left as it is.
  - Under provisional timing the overlay still shows "★ BEST" and the Δ, while the Laps table
    withholds both.
  - A Broadcast still from real footage, and re-shot landing stills: the one recording on this
    machine is not one RUL-9 approved for publishing.

## 12. Recorded at the 0.11.0 release, 2026-11-16

- **v0.11.0 is the it-tells-the-story release**: 6 pull requests from #566 to #573, Wave 4 of the
  campaign section 8 records. The story core, slow-lap notes and three session moments in a
  Qt-free module (#567); the middle half of your laps beside σ, a Corners σ hover and a row in the
  exported report (#568); the Laps table's ▼ cue naming the corner, and its tooltip, on a slow
  lap, whose click rings the corner on the map (#569); Stats ▸ MOMENTS, each moment a Jump (#570); four refusals, sections
  29 to 32 of [refused-2026-09.md](../studio/docs/refused-2026-09.md), with an addendum to §28
  (#566); and the QA fixes before the release (#573).
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **A slow-lap note has one baseline, your typical lap**, the median clean lap (#567). A lap is
    slow when it is more than the larger of the clean laps' middle half and 0.50 s over it: a
    default chosen on few sessions, and every tooltip states the rule and says so. Each corner's
    share is its time over its own typical pass, and only a corner beyond the CORNERS BY LAP ▼ step
    is named. The ▼ cue in the row appears only when one or two corners hold the loss (#569).
    Straights are never named: the grid has no noise gate for them yet.
  - **A lap after a left-out lap is labelled** "(after Lap N, left out)", or "(after a too-brief
    crossing)"; its numbers are the same with or without the label (#573).
  - **Three moments kept**: the best lap, the biggest single-corner loss and the best 5-lap run
    (#567, #570). A best 3-lap-run moment was not built: the PACE tile carries that figure, and 5
    of the demo's 12 runs sit within 0.07 s of the best. The best pass per corner and the longest
    run with no ▼ were refused (§32): the first is a tie of 6 to 11 passes at six of the demo's
    seven corners, and the second counts the grid's outlier cells, not its calm: 16 % of
    pure-noise grids run at least as long as the demo's 8 laps.
  - **The middle half is a width** (#568): "middle half spans 0.59 s", the upper quartile minus
    the lower over the clean laps, never a ± about the median, and shown only from 5 laps, below
    which one slow lap reaches a quartile. A lap width under 0.07 s prints "< 0.07 s"; a corner's, under 0.01 s, "< 0.01 s".
  - **An estimated clock widens a moment's ties to 0.15 s**, the Lap race's own bound, and says so
    (#567).
  - **MOMENTS sits under PACE**, above IDEAL LAP, and is absent when it would only repeat the best
    lap the PACE tile already prints (#570, #573).
  - **The PACE tile reads "race pace · best 3-lap mean"**, in the exported report too, so it is
    not read as the 5-lap run's median below it (#570).
- **The media.** `accuracy.png` and `lapstrip.png` were re-captured, byte-identical to 0.10.0's.
  `debrief.png` was re-captured on the synthetic demo and changed in two rows: the Coaching
  reason now reads "middle half of laps spans" where it read "within" (#573). `moments.png` is
  new: the demo's Stats page from PACE to the foot of MOMENTS; three captures were byte-identical.
  `hero.png`, `map.png` and `og.png` are waived again as stale, for 0.8.0's reasons.
  `data-trust.png` and `ideal-lap.png` are renewed with a new reason: 0.11.0 did change Stats-page
  modules (the σ caption, the race-pace caption, MOMENTS, the Corners σ hover), but none inside
  these crops; the modules that draw them are unchanged since v0.10.0, and a demo render of both
  shots from v0.10.0's `studio/` and from 0.11.0 is byte-identical. The export stills
  `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed: 0.11.0 changed no export-video
  code.
- **What could not run for it.** As at 0.10.0, one recording the real-footage checks take is on
  this machine; the rest report Skipped because their recordings are not here. The manual half of
  the golden gate did not run; the core is unchanged, and the synthetic golden gate passed.
- **Open for the owner.**
  - The CORNERS ▲ "erratic" mark against the middle half: on the demo the sixth corner carries ▲ with the
    smallest middle half, 0.03 s, its σ coming from one lap. A fix is in review for the next
    release (#574).
  - The Classic lap strip colours a Δ of +0.06 s as lost, inside the 0.07 s precision.
  - Under provisional timing the overlay still shows "★ BEST" and the Δ, while the Laps table
    withholds both.
  - Should Broadcast be the default Style?
  - The landing stills: re-shoot `hero.png`, `map.png`, `og.png` and the two Stats crops from
    another recording, or swap the Stats crops for demo captures, and a Broadcast still from real
    footage; the one recording on this machine is not one RUL-9 approved for publishing.

## 13. Recorded at the 0.12.0 release, 2026-11-16

- **v0.12.0 is the one-story release**: 13 pull requests from #574 to #587, Wave 5 of the campaign
  section 8 records. The CORNERS ◆ mark (#574); a lap pick that ends a comparison (#575); Follow
  in the Lap race (#576); the debrief's best-lap picture drawn once, and the export tests isolated
  (#578); two refusals, sections 33 and 34 of
  [refused-2026-09.md](../studio/docs/refused-2026-09.md) (#579), and section 29's figures
  corrected (#580); the session report that tells the Stats page's story (#581); the Laps table's
  slow-lap finder (#582); the export gates' order, tests only (#583); one ProRes probe per process
  (#584); plates under Broadcast's texts (#585); and the QA fixes before the release: one printed
  number per corner pass (#586) and Coaching's level step (#587).
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **◆ marks consistency, not ▲** (#574): ▲ means "ahead" in every Δ readout. Up to 3 corners
    and at most half the field, ranked by middle half × median loss, not σ × loss, so one unusual
    pass no longer earns it; none under 5 counted passes or with a middle half under 0.07 s, the
    level step. The σ hover names the one pass when leaving it out drops σ below a fraction of
    itself. On the demo the first and fifth corners carry ◆; the sixth (σ 0.22 s, middle half
    0.03 s, its σ carried by Lap 6) is unmarked. This closes section 12's open ▲ question.
  - **A lap pick ends a comparison** (#575): a pick, a MOMENTS Jump or a Coaching Jump leaves
    compare first and says so; the Laps table shows the pair.
  - **Follow is off by default** (#576), the conservative default, not for its cost: section 10's
    "no follow-cam" is superseded by its 2026-11-16 addendum, which carries the measurement (+3.1 /
    +5.9 / +7.6 ms of CPU per tick at the median / p95 / p99 over the plain race, offscreen raster
    only; compositing on screen not measured). View ▸ Follow the race, ⇧R or the race card's
    crosshair; remembered once chosen. The view fits the middle 80 % of the field plus the
    selected lap.
  - **The report** (#581): the app's dark tokens on screen, white on paper; the Stats page's order
    and words; no Grip column (the app reads grip lap to lap within a corner, not as a report
    figure); the lap strip embedded as the app's own PNG, so the report stays one self-contained
    file.
  - **The finder scrolls, it does not select** (#582): the charts, the video and a comparison
    stay; the outline clears on a key move or another pick (#586).
  - **Refused**: section 33, "N of M slow laps lost their time at one corner" (on the demo 1 of 1,
    p 1.00; a p ≤ 0.05 gate was refused too), and section 34, splitting a long corner window in
    two (it renumbers every corner, and the demo has no valley to split); section 29's figures
    were corrected to laps by number (#580).
  - **The ProRes probe** (#584): one in flight per process, shared by every dialog and export; a
    miss is never kept; a success, an unlisted encoder, a bent alpha or a timeout is; one export
    action pins the encoder once, so the estimate and the render agree.
  - **Broadcast plates** (#585): under the Δ bar's scale, the honesty stamp and the g readout, at
    alpha 230; the minimap's corner labels outlined heavier; the corner cards left at 3.2:1
    because they are shared with Classic, whose pixels stay identical.
  - **The printed-loss convention** (#586): a pass's gap is the printed pass minus the printed
    typical, and Med loss the printed Median minus the printed Best, so every surface prints one
    number per pass and the reader can check it. On the demo Lap 6 at the sixth corner reads
    +0.82 s in the Laps table, Stats and the report (Stats said +0.83 s); the fourth corner's Med
    loss reads +0.06 (was +0.07) and the CORNERS note "Summed, that is 0.77 s" (was 0.78); 19
    CORNERS BY LAP hovers move by 0.01 s. The ▼ rule now rounds the median too: 3 of 144,400
    swept cells are newly marked, none on the demo.
  - **Coaching's level step** (#587, the conservative subset): Coaching never names consistency as
    the reason at a corner whose passes are level with each other, a middle half under the 0.07 s
    step Stats ▸ CORNERS uses; it names another habit that clears its own floor, or says "no one
    habit explains it: your passes here are level with each other". The ranking, the focus corners
    and the weighing of reasons are unchanged: the demo's debrief still puts its first, second and
    seventh corners on the focus list. The synthetic golden baseline was re-cut in a reviewed
    change of 18 leaves (the reason's kind, its contribution and the theme's cause only), and one
    published cell of the theme table in `studio/coaching.py` was re-measured (a chapter row's
    "consistency 74 %" now reads "coasting 26 %"). Not ranking corners that lose under 0.07 s, and
    picking the reason on the middle half instead of σ, were measured and left for the owner,
    because both would change which corners the debrief shows.
- **The media.** `accuracy.png`, `debrief.png`, `lapstrip.png` and `moments.png` were re-captured
  on the synthetic demo at a clean commit, all four byte-identical to the files already committed:
  `debrief.png` had been re-captured by #587 (its rows' reasons now say the passes are level), and
  this release records it at a real commit; `moments.png` shows nothing #586 changed. `hero.png`,
  `map.png` and `og.png` are waived again as stale, for 0.8.0's reasons; they show neither the
  slow-lap footer nor Follow. `data-trust.png` and `ideal-lap.png` are renewed: 0.12.0 changed
  Stats-page modules (the ◆ mark, the printed losses, helpers moved for the report), and among the
  modules that draw these crops only comments and new stylesheet rules for the Laps footer; a demo
  render of both shots from v0.11.0's `studio/` and from 0.12.0 is byte-identical. The export
  stills `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed with a new reason: 0.12.0
  did change export code (#584, #585), but neither change reaches a Classic export: a
  painter-level render of 192 Classic configurations on the demo, 3,072 frames overlay-only and
  composited, from v0.11.0's `studio/` and from 0.12.0 is byte-identical.
- **What could not run for it.** As at 0.11.0, one recording the real-footage checks take is on
  this machine; the rest report Skipped by name because their recordings are not here. The
  published real-footage Coaching tables (`test_the_coaching_tables_match_the_footage`) cannot be
  re-verified offline. Follow's cost was measured offscreen only. The manual half of the golden
  gate did not run; the core is unchanged, and the synthetic golden gate passed on its reviewed
  re-cut.
- **Open for the owner.**
  - Should Coaching rank a corner whose time lost is under 0.07 s at all? On the demo only the
    first corner would remain, and 5 of 9 synthetic sessions would lose their debrief landing.
  - Should Coaching weigh the consistency reason on the middle half instead of σ?
  - The apex and braking reasons have no noise floor.
  - The habit planted in the demo is ranked only on the demo's own seed.
  - The Classic lap strip colours a Δ inside 0.07 s as lost; under provisional timing the overlay
    still shows "★ BEST" and the Δ while the Laps table withholds both.
  - Should Broadcast be the default Style, and should the Extras open ticked in Classic?
  - The ◆ glyph, and Follow's default (off).
  - The landing stills: re-shoot them from another recording, and a Broadcast still; the one
    recording on this machine is not one RUL-9 approved for publishing.
  - The keys 1-5 right after a load, on a real display.
  - Pull request #582's commit list still shows a comment, since removed, that cited a real
    session; main never carried it.

## 14. Recorded at the 0.13.0 release, 2026-11-17

- **v0.13.0 is the report release**: 4 pull requests from #589 to #592, Wave 6 of the campaign
  section 8 records. The lap card's baseline line and the one-column shortcuts card (#589);
  Coaching's shortlist rule and page strings moved into `coaching.py`, with one fix, no "+0.00 s"
  card opportunity (#590); the report's pinned chart and map, its Laps headers and two leak fixes
  (#591); and the report's front page (#592).
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **The report's figures are pinned, not grabbed** (#591). Chart: the one the app opens on, the
    best lap against the synthetic ideal lap, falling back to the two fastest laps against the
    best; a one-lap session gets Speed only. Map: the best lap coloured by speed, its brake glyphs
    and corner labels, the whole track framed, no drag cue or video marker. Both on distance, in
    the reader's unit, at fixed sizes (960×480 and 960×600), each captioned. Why: the live grab
    embedded whatever the window showed, and from the debrief an empty image. The copies are
    destroyed at once (about 14 MB per report leaked before). A figure that fails leaves a note
    rather than costing the document.
  - **The front page** (#592): the lap card beside COACHING, printed from the ordinary Coaching
    page's text (braking-direction lines included) even when exported from the debrief. No card
    on provisional timing. With too few laps, the page's own empty state. A closing note tells the
    headline's total from the ideal lap's gap; the scope note counts the shortlist the headline
    sums.
  - **The card's wording** (#589): "typical pass vs your best lap's" (`stats.TYPICAL_PASS`,
    `coaching.LOSS_BASELINE`). "median vs your best lap" was refused: it reads as the median lap.
  - **The shortcuts card is 640 px** (#589): one key column for every group, set by its widest
    cell; gesture rows are a bare verb and name their object in the description. At 560 px eight
    descriptions wrapped.
  - **Conservative choices**: the strings moved without rewording (a dump of the Stats page's
    observable state is equivalent to v0.12.0's); the card shows no opportunity rather than
    "+0.00 s"; nothing in the live app's look changed; no report screenshot was published.
- **The media.** `accuracy.png`, `debrief.png` and `moments.png` were re-captured on the synthetic
  demo in one run and `lapstrip.png` in a run of its own, at a clean commit, all four
  byte-identical to the files already committed. `hero.png`, `map.png` and `og.png` are waived
  again as stale, for 0.8.0's reasons: 0.13.0 changed nothing these pictures show (its map change
  is a report-only copy of the map), and a re-composition of `og.png` is byte-identical.
  `data-trust.png` and `ideal-lap.png` are renewed: a demo render of both shots from v0.12.0's
  `studio/` and from 0.13.0 is byte-identical. The export stills `overlay.jpg`, `best-lap.jpg` and
  `best-lap.mp4` are renewed: 0.13.0 changed no export-video code, and a painter-level render of
  96 Classic configurations on the demo, 1,536 frames overlay-only and composited, from v0.12.0's
  `studio/` and from 0.13.0 is byte-identical. The report and the share card have no published
  image.
- **What could not run for it.** As at 0.12.0, one recording the real-footage checks take is on
  this machine; the rest report Skipped by name because their recordings are not here. The
  transport keys (Space, ←/→, `,` / `.`) could not be confirmed offscreen. The manual half of the
  golden gate did not run; the core is unchanged.
- **Open for the owner** (new).
  - The report's Laps table prints lap times to 3 decimals, where the app shows 91.4.
  - The theme's "79 % of that time" is a share of all ranked corners, while the headline above it
    sums the top 3 (0.43 s).
  - A session reload can strand widgets.
  - The landing stills, and whether to add a report screenshot.
  - With nothing ranked, the lap card's "Drive a few more clean laps" sits beside COACHING's "no
    corner clears its spread".
  - A one-lap report says "Your ideal lap is 0.00 s under your best."
  - The map key in the report keeps its collapse chevron.
  - The capture tool writes different `lapstrip.png` bytes depending on what else the same run
    captures; this release shot it in a run of its own.
- **Open for the owner** (carried from section 13): ranking corners that lose under 0.07 s at all;
  weighing consistency on the middle half rather than σ; the apex and braking reasons have no
  noise floor; the demo habit is ranked only on its own seed; Classic colours a Δ inside 0.07 s;
  the overlay's ★/Δ under provisional timing; Broadcast as the default Style; Extras on in
  Classic; the ◆ glyph; Follow's default; re-shooting the landing stills and a Broadcast still;
  the keys 1-5 right after a load on a real display; pull request #582's commit list comment; the
  published real-footage Coaching tables cannot be re-verified offline.
- **The campaign ends with this release** (discovery round 3: one small wave, then stop).
  Leftovers, ranked: the open questions above; then the brake glyph colour map against the chart's,
  the "best rolling" note, one lap in two colours, the speed-panel chip, the welcome page, the
  "most of it on the apex" tail, and the fifth corner's label under the Broadcast minimap's kart
  dot.
