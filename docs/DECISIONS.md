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

## 15. Recorded at the 0.13.1 release, 2026-11-17

- **v0.13.1 is a patch release**: 4 pull requests from #594 to #597, Waves 7 and 8 of the campaign
  section 8 records, run after the owner said to continue past 0.13.0. Discovery round 4's verdict
  was two truth fixes, then stop. The report's edge states (#594); Coaching says only what the page
  shows (#595); the report's Laps table at the app's precision (#596); and `fmt_time` carrying the
  minute (#597). No capability was added and nothing in the live app's look changed except the
  words of two Coaching lines.
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **When nothing ranks, the lap card repeats Coaching's headline** (#594), word for word, where
    it asked for "a few more clean laps"; that line is kept for too few clean laps, the one state it
    is true of. On a synthetic session of 4 laps that lists five corners and ranks none, the card
    and the report's COACHING section now say the same thing.
  - **No 0.00 is printed as a measurement** (#594). The ideal-gap sentence ("Your ideal lap is
    … under your best") is withheld whenever one lap won every segment, the gate the IDEAL LAP
    group already reads, which a one-lap session always meets; Med loss under two counted passes is
    a dash, and with every cell a dash the CORNERS note prints no sum.
  - **The theme's share keeps its number and names its base** (#595): on the demo, "79% of the
    time your 4 ranked corners lose is in" its first corner, under "0.43 s across your top 3
    corners", where it read "79% of that time". When the ranked count equals the shortlist the sentence is unchanged. Re-basing the
    share onto the shortlist (84 % on the demo) stays open.
  - **A level row takes no phase tail** (#595): "no one habit explains it: your passes here are
    level with each other (…, under 0.07 s)" no longer ends "— most of it on the apex" (the demo's
    seventh corner: thirds of 0.011, 0.020 and 0.001 s). The "How to find it" hover names the
    measured braking-direction line, not the estimated brake-point line retired earlier, and the
    debrief, whose rows never carry that line, has a hover of its own.
  - **The report's Laps cells print at the app's precision** (#596): whole metres, speeds to one
    decimal in the reader's unit, splits and corner times to two, a missing number as a dash. The
    lap time keeps three decimals, in seconds under the "Time (s)" header, so two laps a thousandth
    apart never read level. `laps.csv` is byte-identical to 0.13.0's.
  - **`fmt_time` carries only at the boundary** (#597): a time within half a millisecond under a
    minute prints "1:00.000", not "0:60.000"; every other printed time is byte-identical.
- **The media.** `debrief.png` was re-captured on the synthetic demo: its seventh corner's row lost
  its tail and its theme line names its base, so its bytes changed. `accuracy.png` and
  `moments.png` were re-captured in the same run and `lapstrip.png` in a run of its own, at a clean
  commit, all three byte-identical to the files already committed. The waivers granted at 0.13.0
  hold through 0.13.x (`tests/test_version.py` lapses a waiver at the next minor release), so none
  was renewed. The export stills' reason stands: 0.13.1 changed one function the overlay's clock
  calls, `fmt_time`, at the minute boundary only, and a painter-level render on the demo of 24
  Classic configurations, 384 frames overlay-only and composited, from 0.13.0's `studio/` and from
  0.13.1 is byte-identical.
- **What could not run for it.** As at 0.13.0, one recording the real-footage checks take is on
  this machine; the rest report Skipped by name. The manual half of the golden gate did not run;
  the core is unchanged.
- **Resolved from section 14**: the report's Laps table precision; the lap card's line beside
  COACHING when nothing ranks; the one-lap report's "0.00 s under your best"; the map key's collapse
  chevron in the report; the theme's base, partly (named, not re-based).
- **Open for the owner** (new).
  - The theme's share is computed from unrounded losses: the demo prints 79 %, while its printed
    cells (0.36 of 0.46 s) give 78 %.
  - A ranked corner whose loss is under the display floor would still be counted in "your N ranked
    corners".
  - A row that is not level can still say "most of it on …" over thirds that are all under 0.07 s.
  - With sector lines, the report's Laps card scrolls sideways at 1100 px.
  - `fmt_time` prints −30 s as "-1:30.000" (minus one minute, plus 30 s), a convention nothing
    exercises: no caller passes a negative.
  - One lap drawn in two colours in a race over a pick; a note when the best rolling lap is within
    0.07 s of the best lap; the welcome screen.
- **Open for the owner** (carried from section 14): a session reload stranding widgets; the
  landing stills, and whether to add a report screenshot; the capture tool's `lapstrip.png` bytes
  depending on what else the run captures; ranking corners that lose under 0.07 s at all; weighing
  consistency on the middle half rather than σ; the apex and braking reasons have no noise floor;
  the demo habit is ranked only on its own seed; Classic colours a Δ inside 0.07 s; the overlay's
  ★/Δ under provisional timing; Broadcast as the default Style; Extras on in Classic; the ◆ glyph;
  Follow's default; re-shooting the landing stills and a Broadcast still; the keys 1-5 right after
  a load on a real display; pull request #582's commit list comment; the published real-footage
  Coaching tables cannot be re-verified offline.
- **The campaign ends with this release.** Discovery round 4 found no unfinished first impression
  and no performance cliff, and asked for these truth fixes and then a stop.

## 16. Recorded at the 0.14.0 release, 2026-11-17

- **v0.14.0 is a minor release**: 5 pull requests from #599 to #603, Wave 9 of the campaign
  section 8 records, "Close the race-day loop". Discovery round 5 looked through two lenses, race
  day and a visitor's first minute, and found one truth defect (the focus list said "0 clean laps
  through it today" for corners driven 14 times) and two things the app could do that nobody would
  find. The focus list measures a window with one edge on a corner boundary (#599); the timing
  step in three figures (#600); a Coaching row's Compare (#601); the focus verdict's layout and
  timing-step gates (#602); and a Lap race button on the map toolbar (#603).
- **Process.** Each pull request's gate was CI; one local full gate ran on the combined tree
  before the release, and a QA pass drove all five together in one window for the first time.
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **A focus window with one edge snapped onto a corner boundary is measured** (#599): the
    snapped edge joins the alignment the other edge builds, so every clean lap through it counts.
    At the demo's circuit its first corner now reads "0.36 s faster than 1 Jun (3.38 → 3.02 s, 14
    → 14 laps)".
  - **The layout check reads the corner's direction** (#602): the stored corner and today's corner
    over the same window must turn the same way, else "can't say: the corner there today turns the
    other way — a different layout?", and a list refused whole says "the layout looks different
    today" in its headline. An item stored without a direction skips the check, as does a session
    with no detected corner, rather than being refused on a guess.
  - **No faster or slower under 0.07 s** (#602): a difference under the timing step is "no change
    you can act on: under 0.07 s apart, within timing precision", its medians printed to the
    millisecond so the two never read 0.07 s apart beside it.
  - **The phase tail has a floor** (#600): "— most of it on …" only when the third it names is
    0.07 s or more.
  - **The theme's share is the ratio of its printed cells** (#600), with a guard that keeps the
    unrounded share when rounding would carry the printed ratio across the wording's line: the
    demo reads 78 %, from +0.36 of +0.46 s, where it read 79 %.
  - **"best rolling · level"** (#600): the Stats ▸ PACE tile, and the report's PACE row, when the
    best rolling lap is within the timing step of the best lap; the hover says why.
  - **Compare is the typical pass against the best lap** (#601): the lap whose pass is nearest the
    corner's median as printed, ties broken on the raw times, never the best lap itself, beside
    the best lap's pass, both videos at the corner's entry, the charts framed on it, the corner
    ringed. The status line prints the row's own Time lost, never a recomputed difference.
  - **Compare is reachable where Jump is** (#601): its own column on the maximised page; under
    Jump on a docked tab wide enough for Jump (about 557 px); absent where Jump is, at the default
    docked width and on the debrief.
  - **The Lap race button sits outside the toolbar's row** (#603), in the stretch the row leaves,
    and hides when the map panel is under about 660 px, so the window's minimum width is unchanged.
    It is View ▸ Lap race's twin: same check, enabled state and reason.
- **The media.** `debrief.png` (78 %) and `moments.png` ("best rolling · level") were re-captured
  on the synthetic demo and their bytes changed; `accuracy.png` in the same run and `lapstrip.png`
  in a run of its own are byte-identical to the files already committed. The waivers are renewed
  at 0.14.0. `hero.png` now differs from the app in one more visible way: the default window shows
  a Lap race button on the map toolbar that the picture lacks; it stays as a labelled v0.7 picture
  and re-shooting it is the owner's call, as is `map.png` (the button is on the panel it is cut
  from) and `og.png` (a re-composition is byte-identical). `data-trust.png` and `ideal-lap.png`
  render byte-identically on the demo from v0.13.1's `studio/` and from 0.14.0. The export stills
  are renewed: no export-video code changed, and 96 Classic configurations, 1,536 frames, render
  byte-identically from v0.13.1's `studio/` and from 0.14.0.
- **What could not run for it.** The two focus real-footage checks need a second recording of one
  track, which is not on this machine, and report Skipped by name with the rest whose recordings
  are not here. The transport keys (Space, ←/→, `,` / `.`) could not be confirmed offscreen. The
  manual half of the golden gate did not run; the core is unchanged.
- **Resolved from section 15**: a row that is not level saying "most of it on …" over thirds under
  0.07 s (the phase tail's floor); the theme's share from unrounded losses (now the printed cells);
  a note when the best rolling lap is within 0.07 s of the best lap.
- **Open for the owner** (new).
  - The default docked Coaching tab shows neither Jump nor Compare: drop "Done it?" there, or add a
    header action that acts on the selected row?
  - Re-shooting the window and landing pictures, now that the default window shows a Lap race
    button they lack.
  - The layout check has never seen a real track driven in reverse, or a corner reprofiled.
  - A marginal corner near the 30° detection limit could read "there's no corner there today".
  - The mixed headline's "N without enough evidence" counts layout refusals with the rest.
  - A Compare started from the maximised page returns to the grid and stays there.
  - The older "No single theme: 100% … 0% …" split on some synthetic sessions.
  - The focus verdict's spread-based line prints medians to two decimals, so "0.01 s apart (2.67 →
    2.67 s)" can show two equal medians; the timing-step line already prints three.
  - FIRST_LAP's loop paragraph does not name the two new verdicts (within timing precision, a
    different layout), and its step 8 names Jump, not Compare or the Lap race button.
  - The report's Laps card scroll with sector lines; `fmt_time` negatives; Classic's Δ colour inside
    0.07 s; Broadcast as the default Style; the welcome screen.
- **Open for the owner** (carried from section 15): a ranked corner under the display floor counted
  in "your N ranked corners"; one lap drawn in two colours in a race over a pick; a session reload
  stranding widgets; whether to add a report screenshot; the capture tool's `lapstrip.png` bytes
  depending on what else the run captures; ranking corners that lose under 0.07 s at all; weighing
  consistency on the middle half rather than σ; the apex and braking reasons have no noise floor;
  the demo habit is ranked only on its own seed; the overlay's ★/Δ under provisional timing; Extras
  on in Classic; the ◆ glyph; Follow's default; a Broadcast still; the keys 1-5 right after a load
  on a real display; pull request #582's commit list comment; the published real-footage Coaching
  tables cannot be re-verified offline.
- **The campaign ends with this release** unless the owner's next race day says otherwise: the
  question left is whether the focus loop holds on a second real session of one track.

## 17. Recorded at the 0.15.0 release, 2026-11-18

- **v0.15.0 is a minor release**: 7 pull requests from #606 to #612, Wave 10 of the campaign
  section 8 records, built to three designs the owner approved and two of his rulings.
  - #606: a track driven the other way round is "can't say" for every stored corner, and
    `synth_gopro --reverse` drives the demo's circuit backwards for the tests.
  - #607: Coaching's theme counts the corners its headline sums (the demo: 84 % of that time in
    its first corner).
  - #608: a personal best per direction; the Library names each row's way round.
  - #609: a Jump and a Compare, named for their corner, on the docked Coaching tab's focus-list
    line.
  - #610: the welcome screen says what Pacer does, leads with the demo, and lists five recent
    sessions.
  - #611: Classic and compare exports draw a Δ under 0.07 s white; the compare Δ loses its arrow.
  - #612: tests only; six slow test files trimmed, and refusal section 35 (CI tiering).
- **Owner rulings of 2026-11-18.** He approved design 1A with its question answered (a) (Jump and Compare share the
  focus-list line; at 1440×900 the focus paragraph folds to its chip while they are shown), 2A with
  (a) (one centred welcome column; for a returning user the demo is a small link) and 3A with
  (a) (the neutral colour for a gap under 0.07 s in Classic and in the compare export, with no
  hold). He approved personal bests per direction, the theme's share over the shortlist, re-shooting
  the README and landing screenshots from the demo, and a report picture. He asked for faster
  delivery: CI is the per-pull-request gate, one combined local gate runs per wave, and the full
  gate and the soak run at the release. No check was removed.
- **Defaults applied on the owner's instruction not to be asked**, each open to his overrule:
  - **A row's direction is measured, never guessed** (#608): `rotation` is stored only from a
    session's own corners; a row or focus item without it is compared as before and is never named
    by a way round.
  - **The pair aims at the selected row, else the top ranked one** (#609), selects nothing itself,
    and hides wherever the rows carry their own Jump; a list with verdicts keeps its block when the
    page has room, else the chip says how many were re-measured.
  - **"Recent" means the newest recording date** (#610): the Library stores no last-opened time.
  - **The level rule is Broadcast's own** (#611): decided on the printed value, so "+0.07" is
    always coloured.
- **#612's record.** The six files run 25 % faster locally (351.6 → 262.8 s, each alone under
  ctest). 84 planted defects were run one at a time against each file before and after the trim:
  71 were killed before and 71 after, none lost. Section 35 of `studio/docs/refused-2026-09.md`
  refuses running the export tests on a pull request only when export code changes (built in #605,
  closed): the export tests import 91 of the app's 93 modules, so a sound path list skips the tier
  on 25 of the last 200 merged pull requests and on none of the last 60.
- **The media.** Re-captured on the synthetic demo at 0.15.0: `hero.png` and `map.png`, which had
  been labelled v0.7 pictures of Sandown 3h; `og.png`, composed from the new hero; `debrief.png`,
  for its theme line (the docked page's Jump and Compare are not on the debrief, by design); and
  `accuracy.png`, `moments.png` and `lapstrip.png` (its own run), whose bytes are unchanged. New:
  `report.png`, the demo report's first screen at 2x, beside the pages' report text. The waivers of
  `data-trust.png` and `ideal-lap.png` are renewed (a demo render from v0.14.0's `studio/` and from
  0.15.0 is byte-identical), as are those of `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4`: a
  painter-level A/B of 90 configurations on the demo is byte-identical on the best lap and on
  Broadcast, and differs on Classic laps 6 and 7 only, as #611 intends. `overlay.jpg` still shows a
  red "Δ +0.05" that a current export draws white; re-shooting it needs real footage that is not
  on this machine.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name, the two focus checks among them (they need a second recording of one
  track). The transport keys could not be confirmed offscreen. The manual half of the golden gate
  did not run as a release gate; the core is unchanged.
- **Resolved from section 16**: the docked Coaching tab's missing Jump and Compare (#609);
  re-shooting the window and map pictures (now the demo's); the layout check on a track driven in
  reverse (#606, on synthetic sessions); Classic's Δ colour inside 0.07 s (#611); the welcome
  screen (#610); a report screenshot; the theme's share counted over every ranked corner (#607).
- **Open for the owner** (new).
  - With verdicts on the list, the docked chip says "3 of 3 re-measured" and the verdict lines are
    one click away at 1280 and 1440; the alternative is to hide the pair there or shrink the theme.
  - A session with no laps is left out of the welcome's list, and an unreadable library shows the
    first-visit screen.
  - The overlays use the 0.07 s step even on an estimated clock, where the Lap race and the story
    use 0.15 s.
  - A re-profiled corner that keeps its direction is still compared.
  - Focus items and Library rows stored before 0.15.0 carry no direction until their recording is
    opened again.
  - A one-corner shortlist's theme reads oddly: on a synthetic session (seed 1, 6 laps, a planted
    habit) it is "No single theme: 100% of the time on offer is in corners you have already
    driven at this pace, 0% in corners you have rarely reached", followed by that corner's name
    and "sits on the line: one lap fewer at its target and it would not count.", and one corner
    can read "No single cause dominates these corners".
  - After the docked pair's Jump the lap panel shows Corners, as every Jump does, so its Compare is
    one tab away.
  - A missing file's name in the welcome is cut to "GX01….MP4", losing its number.
  - `overlay.jpg` needs real footage to re-shoot; `og.png` changed, and it is also the repository's
    social preview on GitHub, which is uploaded by hand.
  - 13 planted defects no test catches (#612): the Export action enabled with no clip, the report's
    map, the lap card one pixel low, a hovered label, and gaps in the Broadcast, ghost and gate
    tests.
  - Two slow product paths noticed in #612: the export painter walks every frame twice, and
    windows are kept alive in `test_central_view_realqt`.
- **Open for the owner** (carried from section 16): a marginal corner near the 30° detection limit
  could read "there's no corner there today"; the mixed headline's "N without enough evidence"
  counts layout refusals with the rest; a Compare started from the maximised page returns to the
  grid and stays there; the focus verdict's spread-based line prints medians to two decimals;
  FIRST_LAP's loop paragraph does not name the two newer verdicts, and its step 8 names Jump, not
  Compare or the Lap race button; the report's Laps card scroll
  with sector lines; `fmt_time` negatives; Broadcast as the default Style; a ranked corner under the
  display floor counted in "your N ranked corners"; one lap drawn in two colours in a race over a
  pick; a session reload stranding widgets; the capture tool's `lapstrip.png` bytes depending on
  what else the run captures; ranking corners that lose under 0.07 s at all; weighing consistency
  on the middle half rather than σ; the apex and braking reasons have no noise floor; the demo
  habit is ranked only on its own seed; the overlay's ★/Δ under provisional timing; Extras on in
  Classic; the ◆ glyph; Follow's default; a Broadcast still; the keys 1-5 right after a load on a
  real display; pull request #582's commit list comment; the published real-footage Coaching
  tables cannot be re-verified offline.

## 18. Recorded at the 0.16.0 release, 2026-11-19

- **v0.16.0 is a minor release**: one pull request, #614, Wave 11 of the campaign section 8
  records. The Corners tab gets a second view, Quickest passes: per corner the quickest time, its
  lap, how many laps are level with it and a play button; a row click plays that pass and pauses
  after it; ⌘K lists one "Quickest" row per corner, naming its lap. It also carries a garbage
  collection on the GUI thread before a load worker starts (below).
- **Owner ruling of 2026-11-18/19.** He asked to see the fastest time through each corner with
  the lap it happened on, and to jump quickly to that moment in the footage. He was shown three
  options (a view on the Corners tab, a strip of corner chips under the video, a reel of the passes
  played back to back) and delegated the choice: "decide yourself… Make it very logical and simple
  to use".
- **What was chosen for him**, each open to his overrule:
  - **The Corners tab view**, behind two chips ("Lap 13 corners", "Quickest passes"), the choice
    remembered; the tab reads "Corners · quickest" while it shows.
  - **The lead-in is 2 s before the corner's entry, or the previous corner's exit when that is
    later** (on the demo the straights before three of its seven corners take 1.3 to 1.8 s).
  - **Play, then pause 1 s after the exit.** Any seek, play or pause of the driver's own drops that
    pause, so a scrub or a lap pick is never stopped by a clip he has left.
  - **Name the quickest and count the level laps**, never "fastest" for a tie: the ★ and the
    purple go only on a quickest no other lap matches within the timing step (0.07 s; 0.15 s on an
    estimated clock), never while lap timing is provisional. On the demo only its fifth corner stands clear,
    "3.52 ★" on lap 10, "none · next +0.071"; the hover of a tie says why it has no ★.
  - **Click the same row again for the next level lap**, wrapping; the row names the lap playing
    with its own time ("playing lap 7 (2.35) · 2 of 6"), and the Lap cell keeps the quickest's.
    ⌘K always plays the lap it names.
- **Refused or deferred, with the reasons.** The strip under the video is deferred: it costs
  32 px, 12 to 14 % of the video's height at 1440×900 and 1280×800, and leaves no room for the
  times the request asked for. The back-to-back reel is refused: seven passes in a row read as a
  lap that was never driven, the reason the ideal-lap film was refused
  (`studio/docs/refused-2026-09.md` §31). The word "fastest" for a tie is refused: six of the
  demo's seven corners are ties within 0.07 s, and which lap comes out on top there is timing noise
  (`studio/docs/refused-2026-09.md` §32).
- **The garbage collection, and what stays exposed.** Python's cyclic collector frees Qt widgets
  held only by reference cycles on whichever thread trips its threshold; in the test suite that
  thread was once a load worker's, and deleting a table there crashed. `gc.collect()` now runs on
  the GUI thread before a load worker starts, pinned by `tests/test_load_worker_gc.py`. It is a
  mitigation, not a cure: garbage made while a load runs, and the export and demo-resolve worker
  threads, stay exposed; the cure is fewer cycles. The crash was only seen in the test suite.
- **Process.** CI was the pull request's gate; the QA pass drove the view in the real window on
  the demo (every row, again, the keys, ⌘K, three window sizes, a reload, a start-line drag and its
  undo, both speed units, Compare, a Lap race, the maximised panel and the debrief) and with the
  real player on a recording of two chapters (a jump across the chapter seam, a clip that straddles
  it, 1× and 2×, rapid clicks, sound on, software decoding). The Stats page and the session report
  are unchanged on the demo: a page dump from v0.15.0's `studio/` and from 0.16.0 is equivalent,
  and the report's HTML is byte-identical.
- **The media.** Re-captured on the synthetic demo at 0.16.0: `hero.png`, `map.png`, `og.png`,
  `debrief.png`, `accuracy.png`, `moments.png` and `lapstrip.png` (its own run), all
  byte-identical to the files already committed; `report.png`, whose HTML is byte-identical and
  whose picture differs in 347 anti-aliased pixels of the map line. New: `quickest.png`, the window
  at 1440×900 with a tied corner's row playing, beside the pages' sentence on the view. The
  waivers of `data-trust.png` and `ideal-lap.png` are renewed (a demo render from v0.15.0's `studio/` and from
  0.16.0 is byte-identical), as are those of `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4`: no
  export-video code changed.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. The transport keys could not be confirmed offscreen. The full gate, the
  soak and the smoke run on the release branch, not in the QA pass.
- **Open for the owner** (new).
  - The default Corners view stays the per-lap table, and Quickest passes is one remembered click
    away; he may prefer it as the default.
  - Sector lines he draws are not listed in the view yet; the Stats page's SPLITS grid marks each
    sector's best.
  - The Stats page's CORNERS BY LAP cells still do not play their pass when clicked.
  - The strip under the video stays available as a later option.
  - During a pass the chart shows the whole lap; it does not zoom to the corner.
  - A corner that follows the previous one with no straight between them starts its clip at that
    corner's exit, so its approach is barely shown.
  - A long corner window, which `studio/docs/refused-2026-09.md` §34 keeps whole, plays a long
    clip.
  - On one tab the ★ means two things: in the Lap view a time that prints as the quickest, in
    Quickest passes a quickest no other lap matches; the hover of a tie says so.
  - At the window's minimum size (1059×546 on the demo) the view's rows scroll; the play buttons
    stay reachable.
  - The status line clears after 6 s, before a long clip ends; the row keeps saying "playing".
  - The first time costs three clicks from the debrief (the Corners tab, the chip, a row); after
    that, two.
- **Open for the owner** (carried from section 17): with verdicts on the list, the docked chip
  says "3 of 3 re-measured" and the verdict lines are one click away at 1280 and 1440; a session
  with no laps is left out of the welcome's list, and an unreadable library shows the first-visit
  screen; the overlays use the 0.07 s step even on an estimated clock; a re-profiled corner that
  keeps its direction is still compared; focus items and Library rows stored before 0.15.0 carry no
  direction until their recording is opened again; a one-corner shortlist's theme reads oddly;
  after the docked pair's Jump its Compare is one tab away; a missing file's name in the welcome is
  cut, losing its number; `overlay.jpg` needs real footage to re-shoot, and `og.png` is also the
  repository's social preview, uploaded by hand; 13 planted defects no test catches; two slow
  product paths (the export painter walks every frame twice, windows kept alive in
  `test_central_view_realqt`); a marginal corner near the 30° detection limit could read "there's
  no corner there today"; the mixed headline's "N without enough evidence" counts layout refusals
  with the rest; a Compare started from the maximised page returns to the grid and stays there; the
  focus verdict's spread-based line prints medians to two decimals; FIRST_LAP's loop paragraph does
  not name the two newer verdicts, and its step 8 names Jump, not Compare or the Lap race button;
  the report's Laps card scroll with sector lines; `fmt_time` negatives; Broadcast as the default
  Style; a ranked corner under the display floor counted in "your N ranked corners"; one lap drawn
  in two colours in a race over a pick; a session reload stranding widgets; the capture tool's
  `lapstrip.png` bytes depending on what else the run captures; ranking corners that lose under
  0.07 s at all; weighing consistency on the middle half rather than σ; the apex and braking
  reasons have no noise floor; the demo habit is ranked only on its own seed; the overlay's ★/Δ
  under provisional timing; Extras on in Classic; the ◆ glyph; Follow's default; a Broadcast still;
  the keys 1-5 right after a load on a real display; pull request #582's commit list comment; the
  published real-footage Coaching tables cannot be re-verified offline.

## 19. Recorded at the 0.17.0 release, 2026-11-20

- **v0.17.0 is a minor release**: 12 pull requests from #616 to #627, Wave 12 of the campaign
  section 8 records, its theme "every number leads to the footage".
  - #616: the first-open debrief leads with the best lap's card.
  - #617: the big Δ readout is neutral under the 0.07 s timing step; the speed beside it is
    secondary.
  - #618: a played pass zooms the charts onto its corner and the map onto its stretch of track.
  - #619: export one corner pass as a clip.
  - #620: the Library's PB chart spreads one day's sessions in the order driven, and its summary
    reads them in that order.
  - #621: Loop (L) and Slow (0.25×) for a pass.
  - #622: the session report shows its chart and map right after the front page, its Stats in two
    columns on a wide screen.
  - #623: even Stats heading gaps, the export dialog's "Measuring…" hint with a 30 s probe limit,
    and the shortcuts card's key caps on their baseline, "Comma" and "Period".
  - #624: every corner time plays its pass (a Corners lap row, a CORNERS BY LAP cell, the slow-lap
    cue), ⌥← / ⌥→ walk the corners, and a right-click plays or exports a pass.
  - #625: a live readout over the fullscreen video (I hides it), and ★ / ▼ on the lap ruler.
  - #626: compare a pass with the typical pass through the same corner; a compare at a corner,
    Coaching's included, counts its readout from the corner's entry.
  - #627: an Every lap strip on each Quickest passes row.
- **Owner rulings of 2026-11-19.** He asked for comprehensive research into further features and
  for the work to be planned and implemented autonomously ("you are the CTO", "don't ask",
  "continue and don't come back to me"), and delegated the design: "very logical and simple".
- **What was decided for him**, each open to his overrule:
  - **The theme, "every number leads to the footage"**: the three discovery lanes agreed that a
    number naming a moment should play it, and that the pass deserved a closer look.
  - **One click on a row or cell that names one pass plays it, everywhere** (Quickest passes, a
    Corners lap row, a CORNERS BY LAP cell, the slow-lap cue); a ⌘- or ⇧-click is a selection and
    never plays.
  - **Loop and Slow are light switches**: they stay on for the next pass picked from any row, and
    every pass is still one deliberate click (`studio/docs/refused-2026-09.md` §31: no reel).
  - **One Esc means back to the whole lap**: it ends the zoom and the pass together (Loop and Slow
    off, the speed his again).
  - **The pass being studied lives from its play until it is left** (a seek away, a lap pick, a
    Jump, Compare, a Lap race, ▶ past it, Esc, a reload, a re-segment); the clip pausing itself
    does not leave it, so it can still be exported, looped or walked on from.
  - **A pass is compared with the typical pass only where the two printed times are not level
    within the timing step**, quicker or slower, both directions: on the demo at four of its seven
    corners.
  - **A compare at a corner shows no number before the entry and holds the passes' printed gap
    after the exit**, so the frame both panes pause on shows the gap the status line names.
  - **C keeps its whole-lap meaning** while a pass is studied: a key that changed meaning with a
    state the driver cannot see would disagree with the Compare button beside it.
  - **The strip uses each row's own time scale** with the level band at its true width, hollow
    rings for passes outside the band, and hides when it cannot fit beside the row's words or
    while Loop is on (its words then need the room).
  - **Stats headings take one 12 px gap everywhere**, at the cost of three tile labels below the
    fold when the panel is docked at 1440×900.
  - **The debrief's headline stays the shared panel header** under the picture rather than a
    larger line of its own.
  - **The Library chart spreads a day's sessions evenly in the order driven**, not at their time of
    day: two heats 40 minutes apart would sit on one line again at any span past a day.
  - **The fullscreen readout uses the 0.07 s step on every clock**, as the compare seam and the
    exports do.
  - **The garbage collection before a load worker starts is unchanged** (section 18).
- **Refused or deferred this wave, with the numbers** (discovery 6 and the pull requests).
  - A session time-loss heat map against the best lap: a per-bin sign test marks 32 of the demo's
    97 bins with losses as small as 0.002 s per 10 m; with a 0.01 s floor, 12 remain, among them a
    corner Coaching refuses to rank, while the planted habit's apex bins, slow on 8 of 13 laps, are
    hidden.
  - Two laps' racing lines compared at close zoom: 10 Hz GPS lateral noise would be drawn as a
    line difference; the zoomed map keeps one line.
  - Apex, entry and exit speed columns for the quickest pass against the typical one: on real
    footage the apex speed rarely clears the ±1.75 km/h noise of two traces, and on the demo only
    the planted habit's corners do.
  - Quickest-pass marks on the lap ruler: six of the demo's seven corners have 5 to 10 laps level
    with the quickest, and which tops a tie is noise (§32).
  - A per-corner season trend: one recording per track on this machine; on the demo and a second
    synthetic session the focus list says "can't say" for all three corners until a session record
    exists.
  - Frame-step keys: one 60 fps frame is 17 ms against 100 ms GPS samples and the 0.07 s step, and
    `,` and `.` are the marks' keys; Slow shows every source frame.
  - The strip of corner chips under the video, again: its 32 px would be 12 to 14 % of the video's
    height at 1440×900 and 1280×800.
  - The back-to-back reel, again (§31). A light theme: the owner's call. Tiering CI by changed
    paths: already refused (§35).
- **Process.** Three discovery lanes (market, workflow, craft) fed the plan. A usage-limit
  interruption on 2026-11-19 paused the wave; work resumed and nothing was left half-merged. CI
  caught two failures before merge: an undeclared telemetry-to-media clock crossing in #619, and a
  Stats scroll-guard fixture that #623's spacing changed. The QA pass drove the combined tree in the
  real window on the demo (every pass entry point, Loop and Slow across rows, the walk pressed and
  held, every right-click item, the compare at a corner and back by C and by Esc, the fullscreen
  readout and I, the Esc order with a maximised panel and video focus, a Lap race, the report, the
  Library with two same-day sessions, three window sizes) and with the real player on a recording
  of two chapters (the zoom, ten loops, Slow, a loop across the chapter seam, the walk, the
  slow-lap cue's picture, a pass export, the compare, the readout over the footage, five minutes of
  use, and once with software decoding). A Stats page dump from v0.16.0's `studio/` and from 0.17.0
  differs only in geometry, the CORNERS BY LAP cells' play hint and one hidden spacer, on the demo
  and on that recording; the report holds the same text tokens in its new order with byte-identical
  images; a painter-level render of 98 lap, session and compare export configurations, 16 frames
  each, is byte-identical; the golden gate passes, and its manual half on that recording is
  equivalent.
- **The media.** Re-captured on the synthetic demo at 0.17.0: `debrief.png` (picture first),
  `quickest.png` (the zoom, the strip, Loop and Slow, the longer status line), `hero.png` (the
  neutral readout and its secondary speed, ★ and ▼ on the ruler), `og.png` (composed from the new
  hero; it is also the repository's social preview on GitHub, uploaded by hand) and `moments.png`
  (the Stats spacing). `map.png`, `accuracy.png`, `report.png` and `lapstrip.png` (its own run) are
  byte-identical to the files already committed. The waivers of `data-trust.png` and
  `ideal-lap.png` are renewed: a demo render of both from 0.17.0 differs from v0.16.0's in spacing
  only, so the published stills show the old spacing. Those of `overlay.jpg`, `best-lap.jpg` and
  `best-lap.mp4` are renewed: the export A/B above is byte-identical.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. Real macOS fullscreen Spaces, a second monitor and hover could not be
  checked offscreen. The full gate, the soak and the smoke run on the release branch, not in the
  QA pass.
- **Resolved from section 18**: the chart zoom during a pass (#618); the CORNERS BY LAP cells play
  their pass (#624).
- **Open for the owner** (new).
  - Quickest passes is still not the Corners tab's default view.
  - Sector lines he draws are still not in the pass family.
  - Early or late apex and a line offset are measure-first packages, not started.
  - Exporting a compare started at a corner renders the two whole laps.
  - Each compare re-entry leaves seven top-level widgets (pre-existing, measured in #626's review).
  - The readout and the overlays on real macOS fullscreen Spaces and a second monitor are
    unverified offscreen.
  - The strip's hover calls each level pass "level with the quickest" while the Quickest column
    still names one lap.
  - The strip hides at 1280×800 docked for sessions of about 60 laps or more, and for the real
    recording here; it shows at 1440×900 and maximised.
  - At 1440×900 docked the Stats page's even gaps put three tile labels below the fold.
  - On the docked video the lap ruler has no room for ▼ once laps are narrower than the glyph; a
    session of 30 to 40 laps shows only the ★ there.
  - After Esc, a lap pick or C, the status line still reads "Esc: whole lap · ⌘K: export this
    pass" for up to 6 s although the pass is left.
  - At the end of a walk that started from Quickest passes the line says "the last one with a pass
    on lap N", though that walk crosses laps.
  - Esc does not leave a Coaching Compare (C does), while a compare with the typical pass names
    "C or Esc".
- **Open for the owner** (carried from section 18): the default Corners view; drawn sectors in the
  view; the strip under the video stays available as a later option; a corner straight after
  another starts its clip at the previous exit; a long corner window plays a long clip; the ★'s two
  meanings on one tab; the rows scroll at the window's minimum size; the status line clears after
  6 s while the row keeps saying "playing"; the first time costs three clicks from the debrief;
  with verdicts on the list, the docked chip says "3 of 3 re-measured"; a session with no laps is
  left out of the welcome's list, and an unreadable library shows the first-visit screen; the
  overlays use the 0.07 s step even on an estimated clock; a re-profiled corner that keeps its
  direction is still compared; focus items and Library rows stored before 0.15.0 carry no
  direction until reopened; a one-corner shortlist's theme reads oddly; after the docked pair's
  Jump its Compare is one tab away; a missing file's name in the welcome is cut; `overlay.jpg`
  needs real footage to re-shoot; 13 planted defects no test catches; two slow product paths; a
  marginal corner near the 30° detection limit; the mixed headline's count of refusals; a Compare
  started from the maximised page stays in the grid; the focus verdict's medians to two decimals;
  FIRST_LAP's loop paragraph and step 8; the report's Laps card scroll with sector lines;
  `fmt_time` negatives; Broadcast as the default Style; a ranked corner under the display floor;
  one lap drawn in two colours in a race over a pick; a session reload stranding widgets; the
  capture tool's `lapstrip.png` bytes; ranking corners that lose under 0.07 s; consistency on the
  middle half rather than σ; the apex and braking reasons' noise floor; the demo habit ranked only
  on its own seed; the overlay's ★/Δ under provisional timing; Extras on in Classic; the ◆ glyph;
  Follow's default; a Broadcast still; the keys 1-5 right after a load on a real display; pull
  request #582's commit list comment; the published real-footage Coaching tables cannot be
  re-verified offline.

## 20. Recorded at the 0.18.0 release, 2026-11-20

- **v0.18.0 is a minor release**: 10 pull requests, #629 to #636, #638 and #640, Wave 13 of the
  campaign section 8 records, its theme "the driver's own sectors join the pass family, and a
  session that is not the demo reads true".
  - #629: six fixes from the 0.17.0 QA: a pass's status line goes when the pass is left, a walk
    from Quickest passes ends on the last corner rather than on a lap, Esc leaves a Coaching
    Compare, a slow lap narrower than its ▼ shows as a tick on the lap ruler, the map key folds
    while a pass is zoomed, and a tie's strip hover says what is true of each lap.
  - #630: early or late apex and the line offset at the apex, measured and refused
    (`studio/docs/refused-2026-09.md` §36 and §37).
  - #631: the driver's own sector lines in Corners ▸ Quickest passes, each sector row playing,
    looping, slowing and walking like a corner row.
  - #632: a test that a compare re-entry leaves nothing behind.
  - #633: ⌘K, the Library, the saved-tracks manager and three right-click menus free themselves
    when closed.
  - #634: no Stats claim printed from nothing (an empty IDEAL LAP table, a STRAIGHTS slow exit
    under the timing step, an unlisted COASTING place, one lap's 0.0 spreads).
  - #635: ⌥← / ⌥→, C and G say why when they cannot act.
  - #636: the few-laps wording: a tie "each", a cause for a trace that crosses the start/finish
    line only once, no "reload".
  - #638: File ▸ Export overlay video… and its ⌘K command open the export options again; since
    0.17.0 the action's `triggered(bool)` landed in the export's pass argument and crashed it.
  - #640: F and ⌘K "Make the video fill the screen" while comparing say why they do nothing.
- **No new owner ruling.** The wave ran under section 19's of 2026-11-19 ("continue and don't come
  back to me"), with the design delegated to "very logical and simple".
- **What was decided for him**, each open to his overrule:
  - **A sector is level with the quickest within 0.30 s**, the house rule for two figures each
    carrying the published split σ (2·√2 × 0.106 s, rounded), the same gate refusal §26 used,
    rather than the corners' 0.07 s. At that step no measured session earns a sector ★: the view
    names the quickest split and how many laps are level with it.
  - **With sector rows shown, the Quickest table hides its vertical scrollbar**, so the Every lap
    strip keeps its width beside the rows; the wheel and the arrow keys still reach every row. A
    reversible judgement call: see the open list.
  - **A sector pass plays, zooms, loops, slows and walks**, and its walk stays among the sectors;
    its export and its compare with the typical pass are greyed out with "Not for sectors yet",
    because the clip's corner-time readout and the compare's anchor are corner-shaped.
  - **The compare re-entry "leak" is closed as a counting artefact** (#632): the widgets a
    re-entry seemed to leave were already handed to `deleteLater()`, and a probe pumping
    `processEvents()` outside the event loop never delivers the deferred delete; with it delivered,
    each of the three doors leaves nothing over 30 cycles, the real player included, and a test
    holds it. Section 19's item is closed.
  - **"The GPS trace crosses the start/finish line only once" is said only on a trace whose GPS
    quality is known and not low**: dropped fixes hide real crossings, so a low- or unknown-quality
    trace keeps the plain "The GPS may not have locked…" body.
  - **A disabled button's key says the button's own reason** (C, G, and F while comparing),
    rather than a second wording of it.
- **Refused this wave, with the numbers** (§36, §37, #630). Early or late apex: the rule is safe
  (0 to 1 of 48 null cells fire) but blind to what a driver would call early or late: a 2 m shift
  never reliably fires, and on the demo it fires once, at its last corner, a constant-radius arc
  with no minimum to place. The line offset at the apex: 1 m reads as 0.58 m after the de-drift,
  and the quick-against-typical split manufactures "tighter" on recordings with no line habit
  planted (3 of 7 demo corners).
- **Process.** Discovery 7 ("the session that is not the demo", twelve synthetic sessions through
  the real loader) produced #634 to #636. The QA pass drove the combined tree in the real window,
  offscreen and jailed, at 1280×800 and 1440×900: the demo with three sector lines (every
  right-click menu on corner and sector rows after #633's deferred deletes, each menu item acting on
  the row chosen, the "not for sectors yet" refusals, the sector walk to both ends, Esc and a lap
  pick taking the pass's line down, C on a sector pass, the corner walk's end from Quickest
  passes, the widget census across menus, compares and ⌘K), synthetic sessions of zero, one, two
  and ten level laps (the no-laps body beside the Stats empty states, the keys' refusals), and the
  one real recording on this machine, read-only. A demo render of the ideal-lap and data-trust
  shots from v0.17.0's `studio/` and from 0.18.0 is byte-identical. The QA's first reading that
  the export dialogs are never freed was a probe artefact (its stand-in for `QDialog.exec()`; a
  parented dialog is freed only after the real one): with the real `exec()` every export door
  measures +0 widgets per open. The QA did not fire the File menu's export action; its crash
  (#638) was found afterwards by the lane that re-measured the dialog.
- **The media.** Re-captured on the synthetic demo at 0.18.0: `quickest.png` (the map key folds
  while the pass is zoomed, #629) and `report.png` (347 anti-aliased pixels along the drawn line
  differ from 0.17.0's; the same words and figures). `hero.png`, `og.png`, `map.png`,
  `accuracy.png`, `debrief.png`, `moments.png` and `lapstrip.png` (its own run) are byte-identical
  to the files already committed, so the social card did not change. The waivers of
  `data-trust.png` and `ideal-lap.png` are renewed: #634 changed Stats page code, in branches the
  captured session does not reach (its IDEAL LAP lists nine segments), and the demo A/B above is
  byte-identical. Those of `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed: 0.18.0
  changed no rendering code (`export_video.py`, `studio/overlay/` and `theme.py` are untouched);
  `export_controller.py` gained a guard that refuses a sector pass's export (#631) and made the
  overlay export's pass argument keyword-only (#638), both before any render.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. Real macOS fullscreen Spaces, a second monitor and hover could not be
  checked offscreen. The full gate, the soak and the smoke run on the release branch, not in the
  QA pass.
- **Resolved from section 19**: drawn sector lines in the pass family (#631); early or late apex
  and a line offset (refused, #630); the compare re-entry widgets (a counting artefact, #632); the
  strip's hover on a tie, the ruler's ▼ on narrow laps, the status line after leaving a pass, the
  end of a walk from Quickest passes and Esc on a Coaching Compare (#629). Found and shipped
  during this release: F while comparing says why (#640).
- **Open for the owner** (new).
  - A sector pass's export and its compare with the typical pass say "not for sectors yet".
  - On a session with no complete lap the map's empty-state card sits over the middle of the
    trace.
  - Section 13's question (should Coaching rank corners that lose under 0.07 s, and name a habit
    there?) has new synthetic evidence: on a ten-lap session of one driver with GPS noise only,
    every lap level, the debrief still ranks three corners at +0.01 to +0.02 s and names a braking
    cause at the first ("brake later / shorter").
  - With sector rows shown and the scrollbar hidden, the last sector row is cut at 1280×800
    docked (on the demo with three lines, 11 px of S4), and a session whose Quickest view carries
    its "laps not counted" line loses whole rows; there the strip is hidden anyway, so the hidden
    scrollbar buys nothing. A fix is in review (#639), not in this release.
- **Open for the owner** (carried from section 19, each still true on 0.18.0's code): Quickest
  passes is still not the Corners tab's default view; exporting a compare started at a corner
  renders the two whole laps; the readout and the overlays on real macOS fullscreen Spaces and a
  second monitor are unverified offscreen; the strip hides at 1280×800 docked for sessions of
  about 60 laps or more and for the real recording here; at 1440×900 docked the Stats page's even
  gaps put three tile labels below the fold.
- **Open for the owner** (carried from sections 18 and 19, untouched by this wave's changes): the
  strip under the video stays available as a later option; a corner straight after another starts
  its clip at the previous exit; a long corner window plays a long clip; the ★'s two meanings on
  one tab; the rows scroll at the window's minimum size; the status line clears after 6 s while
  the row keeps saying "playing"; the first time costs three clicks from the debrief; with verdicts
  on the list, the docked chip says "3 of 3 re-measured"; a session with no laps is left out of the
  welcome's list, and an unreadable library shows the first-visit screen; the overlays use the
  0.07 s step even on an estimated clock; a re-profiled corner that keeps its direction is still
  compared; focus items and Library rows stored before 0.15.0 carry no direction until reopened; a
  one-corner shortlist's theme reads oddly; after the docked pair's Jump its Compare is one tab
  away; a missing file's name in the welcome is cut; `overlay.jpg` needs real footage to re-shoot;
  13 planted defects no test catches; two slow product paths; a marginal corner near the 30°
  detection limit; the mixed headline's count of refusals; a Compare started from the maximised
  page stays in the grid; the focus verdict's medians to two decimals; FIRST_LAP's loop paragraph
  and step 8; the report's Laps card scroll with sector lines; `fmt_time` negatives; Broadcast as
  the default Style; a ranked corner under the display floor; one lap drawn in two colours in a
  race over a pick; a session reload stranding widgets; the capture tool's `lapstrip.png` bytes;
  consistency on the middle half rather than σ; the apex and braking reasons' noise floor; the demo
  habit ranked only on its own seed; the overlay's ★/Δ under provisional timing; Extras on in
  Classic; the ◆ glyph; Follow's default; a Broadcast still; the keys 1-5 right after a load on a
  real display; pull request #582's commit list comment; the published real-footage Coaching
  tables cannot be re-verified offline.

## 21. Recorded at the 0.19.0 release, 2026-11-20

- **v0.19.0 is a minor release**: 6 pull requests, #639 and #641 to #645, Wave 14 of the campaign
  section 8 records, its theme "a sector pass does everything a corner pass does, and a compare at
  one pass exports as that pass".
  - #639: Corners ▸ Quickest passes shows a scrollbar over the ▶ column's right edge whenever its
    rows run past the panel; no ▶ is cut and the Every lap strip keeps its width.
  - #641: "Compare with my typical pass" on a sector row, past the split's 0.30 s step; corners and
    sectors share one rule (`stats.pass_vs_typical_of`, `PassAnchor`).
  - #642: "Export this pass…" and its ⌘K row on a sector row: the clip from 2 s before the first
    line to 1 s after the last, its plate "LAP 2   S4", then "· 11.12 s" from the last line on (the
    demo after three clicks of "Add sector").
  - #643: the gap held after the exit in a typical-pass compare is printed as timed, neutral; the
    status line says "as timed", and a refused row "within 0.07 s of your typical pass as timed".
  - #644: on a session with no complete lap, the map's card sits beside the track, not on it.
  - #645: Export comparison video… offers "This pass — S1, lap 13 against lap 5" when the compare
    was started from a pass: the two pass clips side by side, the shorter holding its last frame,
    the Δ plate blank, then live, then held.
- **No new owner ruling.** The wave ran under section 19's of 2026-11-19 ("continue and don't come
  back to me"), with the design delegated to "very logical and simple".
- **What was decided for him**, each open to his overrule and each reversible:
  - **The held gap after the exit in a typical-pass compare is neutral** (#643): printed as
    measured, with no colour and no ▲/▼, on the seam, both pane badges and the exported Δ plate.
    The quickest of many noisy passes is partly a lucky one, so the true gap behind an offered row
    is often under the step. Synthetic sessions (the demo at GPS noise 1, 2 and 4.5, two
    generator seeds at 2 and 4.5; sectors with 2, 3 and 4 lines), offered quickest rows and how
    many of them have a TRUE gap under the step:

    | | offered | true gap under the step |
    |---|---|---|
    | corners, noise 1 (the demo as published) | 4 | 0 |
    | corners, noise 2 | 11 | 5 |
    | corners, noise 4.5 | 17 | 11 |
    | corners, the seeds (no planted habit), noise 2 and 4.5 | 17 | 13 |
    | sectors, noise 1 (the demo as published) | 3 | 0 |
    | sectors, noise 2 | 6 | 3 |
    | sectors, noise 4.5 | 9 | 6 |
    | sectors, the seeds, noise 2 and 4.5 | 9 | 9 |

    The live gap inside the pass keeps the step's colour rule; a Coaching row's Compare and a plain
    C compare are unchanged. Reverting is one flag (`PassAnchor.held_neutral`).
  - **The Quickest rows' scrollbar is laid over the table** (#639), on the ▶ column's spare right
    edge, with a mask so its first pixels pass clicks to the ▶ under them, and shown only while
    the rows overflow; a laid-out bar would take its width from the Every lap strip (75 → 63 px at
    1280×800 docked, under the strip's minimum). Section 20's hidden bar is reversed.
  - **"This pass" is the default row of the comparison export when the compare was started from a
    pass** (#645), never remembered; "Both whole laps" is one row down. Each pane plays its own
    pass clip at real speed, as on screen, rather than the track lock, which would stretch pane B.
  - **A sector pass's export has the corner cards off**, with their reason in the dialog (#642): a
    sector clip shows the sector's time, and the cards are for a corner's pass.
  - **The no-laps map card is pinned beside the track** (#644), in a column on the right or a band
    along the top, whichever leaves the trace the larger scale; on synthetic sessions the centred
    card had hidden 48 to 57 % of the trace and the start/finish line it asks him to drag.
- **Process.** The QA pass drove the merged tree in the real window, offscreen and jailed, at
  1280×800 and 1440×900, with every dialog and menu through its real `exec()` closed by a timer
  and every File-menu export fired through `.trigger()` after the menu's own `aboutToShow`: on the
  demo with three sector lines placed mid-straight (not the even split "Add sector" makes, so its
  figures below differ from the changelog's), the right-click menu on the last sector row scrolled to under the
  overlaid bar (Play, Export this pass…, Compare with my typical pass), File ▸ Export overlay
  video… and Export comparison video… in five states (no pass, a corner pass, a sector pass, a
  lap compare, a pass compare), the ⌘K rows in each, and a zero-lap synthetic session (the map
  card beside the track, the Corners and Stats empty states, both exports greyed with the reason).
  The seam's held gap on screen and the exported Δ plate were read at the same five instants of
  a corner and a sector compare: the same figure, neutral after the exit in both. Three small
  demo renders were checked frame by frame: the S4 pass (491 frames, as the dialog said, plate
  "LAP 2   S4 · 13.36 s"), the S1 pass compare (335 frames, the longer clip; pane A holds its
  last frame from frame 321 exactly) and a whole-lap compare (1,349 frames). A widget census
  across the menus, dialogs and compares came back level.
- **The media.** Re-captured on the synthetic demo at 0.19.0: `quickest.png` (the ▶ buttons sit at
  their cells' top-left beside the rows' bar lane, #639; 15,254 pixels in the ▶ column differ, the
  same words and figures). `hero.png`, `og.png`, `map.png`, `accuracy.png`, `debrief.png`,
  `moments.png`, `report.png` and `lapstrip.png` (its own run) are byte-identical to the files
  already committed, so the social card did not change. The waivers of `data-trust.png` and
  `ideal-lap.png` are renewed: #641 changed `stats.py` only to share the pass-against-typical rule,
  and a demo render of both shots from v0.18.0's `studio/` and from 0.19.0 is byte-identical. Those
  of `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed although 0.19.0 changed export
  code (#642: a sector pass's clip; #645: a compare at one pass): a painter-level render on the
  demo of 96 lap, best-lap, run-up and whole-recording configurations and 2 whole-lap compares,
  1,568 frames, from v0.18.0's `studio/` and from 0.19.0 is byte-identical, so none of it reaches
  the lap clip those stills show.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. Real macOS fullscreen Spaces, a second monitor and hover could not be
  checked offscreen. The full gate, the soak and the smoke run on the release branch, not in the
  QA pass.
- **Resolved from section 20**: a sector pass's export and its compare with the typical pass
  (#642, #641); the map's card over the trace (#644); the last sector row cut at 1280×800 docked
  (#639); and, carried from section 19, exporting a compare started at a corner renders the two
  whole laps (#645: it now opens on "This pass").
- **Open for the owner** (new).
  - The selection effect itself is not corrected: rows are still OFFERED on a lucky quickest pass,
    and only the held gap's colour is withheld. Correcting it needs a rule the app does not have.
  - A Coaching row's Compare still colours its held gap: #643 changed only the typical-pass
    compare.
  - The first frame of an export can repeat: frames 0 and 1 show the same source frame when a
    clip's first frame snaps forward. It predates this release and no test catches it.
  - The free-space estimate sizes a pass comparison by pane A's clip, up to 6.5 % low on the demo.
  - A double-click on the video while comparing does nothing and says nothing.
  - While a compare started from a Quickest row's menu runs, the row highlighted is still the pass
    played before it (S4 while S1 is compared, on the demo).
  - Each video export leaves its progress dialog and its "export finished" box parented to the
    window and never freed (a few widgets per export; it predates this release).
- **Open for the owner** (carried from section 20, each re-checked on 0.19.0's code): section 13's
  question (should Coaching rank corners that lose under 0.07 s, and name a habit there?);
  Quickest passes is still not the Corners tab's default view; the readout and the overlays on
  real macOS fullscreen Spaces and a second monitor are unverified offscreen; the strip hides at
  1280×800 docked for sessions of about 60 laps or more and for the real recording here; at
  1440×900 docked the Stats page's even gaps put three tile labels below the fold.
- **Open for the owner** (carried from sections 18 to 20, untouched by this wave's changes): the
  strip under the video stays available as a later option; a corner straight after another starts
  its clip at the previous exit; a long corner window plays a long clip; the ★'s two meanings on
  one tab; the rows scroll at the window's minimum size; the status line clears after 6 s while
  the row keeps saying "playing"; the first time costs three clicks from the debrief; with verdicts
  on the list, the docked chip says "3 of 3 re-measured"; a session with no laps is left out of the
  welcome's list, and an unreadable library shows the first-visit screen; the overlays use the
  0.07 s step even on an estimated clock; a re-profiled corner that keeps its direction is still
  compared; focus items and Library rows stored before 0.15.0 carry no direction until reopened; a
  one-corner shortlist's theme reads oddly; after the docked pair's Jump its Compare is one tab
  away; a missing file's name in the welcome is cut; `overlay.jpg` needs real footage to re-shoot;
  13 planted defects no test catches; two slow product paths; a marginal corner near the 30°
  detection limit; the mixed headline's count of refusals; a Compare started from the maximised
  page stays in the grid; the focus verdict's medians to two decimals; FIRST_LAP's loop paragraph
  and step 8; the report's Laps card scroll with sector lines; `fmt_time` negatives; Broadcast as
  the default Style; a ranked corner under the display floor; one lap drawn in two colours in a
  race over a pick; a session reload stranding widgets; the capture tool's `lapstrip.png` bytes;
  consistency on the middle half rather than σ; the apex and braking reasons' noise floor; the demo
  habit ranked only on its own seed; the overlay's ★/Δ under provisional timing; Extras on in
  Classic; the ◆ glyph; Follow's default; a Broadcast still; the keys 1-5 right after a load on a
  real display; pull request #582's commit list comment; the published real-footage Coaching
  tables cannot be re-verified offline.

## 22. Recorded at the 0.20.0 release, 2026-11-20

- **v0.20.0 is a minor release**: 6 pull requests, #647, #648 and #650 to #653, Wave 15 of the
  campaign section 8 records, its theme "every exported frame is the frame showing at its
  overlay's instant, and 0.19.0's loose ends are tied".
  - #647: every video export (a lap, a corner or sector pass, the whole recording, both panes of
    a compare) makes output frame i the source frame showing at the clip's start plus i frames:
    the latest source frame that starts at or before that instant. Before, on synthetic sources,
    a clip starting less than half a source frame in opened on a repeat of its second frame, one
    starting past half a frame ran one frame (33 ms) ahead on every frame, a clip across a chapter
    seam ran one frame behind or opened on the next frame, depending on its start, and a 59.94
    fps source exported at 30 fps ran one source frame (16.7 ms) ahead on nearly every frame.
    The decode and the sound now seek on the source's own clock, so a chapter span that reports its audio priming as its start (-21.3 ms on the
    synthetic chapters) no longer starts picture and sound early.
  - #648: Corners ▸ Quickest passes lays each Every lap strip out once per strip and cell size,
    not on every paint; on a 70-lap synthetic session a full repaint of the table went from 77 to
    12 ms, a splitter drag step from 180 to 95 ms and the first Quickest view from 423 to 125 ms.
    The pixels are identical at every strip width.
  - #650: each video export's progress window is freed when its queue ends (it was 1 more
    top-level window and 4 more widgets per export, for the rest of the session).
  - #651: while a pass is compared with the typical pass, the highlighted Quickest row is that
    pass's row, also for a compare started from ⌘K after the arrow keys moved the highlight.
  - #652: a double-click on the video while comparing, on either pane, puts on the status line
    the sentence F and ⌘K give: "Make the video fill the screen (F) — not while comparing two laps".
  - #653: a Coaching row's Compare holds the gap after the corner's exit as timed, neutral, on the
    seam, both pane badges and the exported Δ plate, as "Compare with my typical pass" has since
    #643. On eight synthetic sessions with no planted habit, 12 of the 36 Compares offered held a
    coloured gap; 8 of those 12 had a true gap under the 0.07 s step and 1 had the wrong sign.
- **No new owner ruling.** The wave ran under section 19's of 2026-11-19 ("continue and don't come
  back to me").
- **What was decided for him**, each open to his overrule and each reversible:
  - **An export's frame is the latest source frame at or before its instant**, with 0.1 ms of
    tolerance for the seek's own rounding (#647). A source whose time base is no finer than a
    frame (1/25, 1/30, 1/50) keeps the old decode, ffmpeg's accurate seek and the fps filter's
    nearest-frame rounding, because the new seek snaps to a whole frame on such a clock; GoPro
    chapters and ffmpeg's own files run on fine clocks. The old decode comes back by reverting
    `build_decode_cmd`'s branch.
  - **A Coaching row's Compare holds its gap neutral after the exit, as the typical-pass compare
    does** (#653): the best lap's pass, on a row ranked by its loss, is partly a lucky one. The
    live gap inside the corner keeps the step's colour rule; a plain C compare is unchanged.
    Reverting is the flag `CentralView.compare_at_corner` passes (`PassAnchor.held_neutral`).
  - **The highlighted Quickest row follows the pass being compared** (#651), and stays on it when
    the compare ends; a compared lap that is no row's quickest or level lap leaves no row
    highlighted.
- **Measured and not shipped.**
  - #649 (the first export click opens at once: ~0.6 s to 23 ms on this machine, by probing the
    H.264 encoder in the background) is parked, open and unmerged. Its CI run timed out
    `test_export_pass` at 480 s once; the hang did not reproduce locally and the log could not
    place it. It waits for hang diagnostics in the test harness.
  - The wave's timing round on synthetic sessions found nothing a driver repeats that reaches
    100 ms. A sector-line edit takes ~0.45 s at 70 laps; it is deferred because the work is in the
    core's caches. Holding ↓ in a table does not coalesce its steps; left as it is.
- **Process.** The QA pass drove the merged tree in the real window, offscreen and jailed, at
  1280×800 and 1440×900, every dialog, menu and the ⌘K palette through its real `exec()` closed by
  a timer, every menu action through `.trigger()`, on the demo with three sector lines placed
  mid-straight (not the even split "Add sector" makes, so its figures differ from the changelog's).
  ⌘K's "Compare lap 13 through S1 with typical lap 4", fired after S1 was played and ↓ ↓ moved the
  highlight to S3, highlighted S1 with the rows' overlay scrollbar showing (1280×800); a
  double-click on each pane's video then gave the F sentence; the rows were scrolled and both
  splitters dragged under the compare, and after a sector line was moved through the map's own
  signal the Every lap strips matched, pixel for pixel, a fresh window's in the same state. Three
  demo exports in a row, the sector pass, a lap export cancelled through its progress window's
  real Cancel button (no file left) and the S1 pass compare, left the widget census level, with no
  progress window alive. A Coaching row's Compare at the demo's first corner (lap 4's 3.39 s against
  lap 13's 3.02 s) was exported on its "This pass" row: the seam and the exported Δ plate read
  the same at five instants, "Δ —" in the lead-in, "+0.20 ▼" coloured inside the corner, "+0.37"
  neutral at the exit frame and after it. Each demo render was checked frame by frame against the
  source decoded on its own timestamps: the S4 pass (491 frames, starting 0.53 of a source frame
  in), both panes of the S1 pass compare (335 frames, pane A holding its last frame from frame
  321) and both panes of that corner compare (192 frames) show the source frame showing at every
  instant checked, and the S4 plate's time appears on the first frame at or after the last line.
  The coded-picture renders of `test_export_pass`, `test_export_pass_compare`, `test_export_seam`
  and `test_export_e2e` passed, exact from frame 0 with no steady +1 allowed.
- **The media.** Re-captured on the synthetic demo at 0.20.0: `hero.png`, `og.png`, `map.png`,
  `accuracy.png`, `debrief.png`, `moments.png`, `report.png`, `quickest.png` and `lapstrip.png`
  (its own run) are byte-identical to the files already committed, so the social card did not
  change; no captured shot shows a compare, so #651 and #653 reach none of them. The waivers of
  `data-trust.png` and `ideal-lap.png` are renewed: 0.20.0 changed no Stats page code. Those of
  `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed with a new sentence: #647 changed
  which source frame every export shows, so they come from exports made before the frame fix; a
  current export's picture can differ from theirs by at most one source frame, and the overlay
  drawn over it is unchanged. Re-shooting them needs the Sandown 3h recording, not on this machine.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. The QA renders decoded in software: hardware-decode exports were checked
  by #647's own sweep and by the release gate, not by an independent reviewer. Real macOS
  fullscreen Spaces, a second monitor and hover could not be checked offscreen. The full gate, the
  soak and the smoke run on the release branch, not in the QA pass.
- **Resolved from section 21**: the first frame of an export repeating (#647); each video export
  leaving its progress window behind (#650; the "export finished" box was already freed); the
  stale row highlight during a compare started from a Quickest row's menu or ⌘K (#651); the silent
  double-click on the video while comparing (#652); a Coaching row's Compare colouring its held
  gap (#653).
- **Open for the owner** (new).
  - Sound after a chapter seam on the synthetic chapters still runs 16.7 ms early (38 ms before
    #647).
  - At a 30.000 fps output over a 59.94 source, about 3 frames in 300 show the next source frame:
    a source frame starting within the 0.1 ms tolerance after the output's instant.
  - Hardware-decode exports were verified by the author's sweep and the release gate, not by an
    independent reviewer.
  - #649, the background encoder probe, is parked until the test harness can say where a hang is.
- **Open for the owner** (carried from section 21, each re-checked against 0.20.0's changes): the
  selection effect is not corrected (rows are still offered on a lucky quickest pass; only the held
  gap's colour is withheld); the free-space estimate sizes a pass comparison by pane A's clip, up
  to 6.5 % low on the demo (`guard_free_space` is untouched); section 13's question (should
  Coaching rank corners that lose under 0.07 s, and name a habit there?); Quickest passes is still
  not the Corners tab's default view; the readout and the overlays on real macOS fullscreen Spaces
  and a second monitor are unverified offscreen; the strip hides at 1280×800 docked for sessions
  of about 60 laps or more and for the real recording here (#648 kept its minimum width and hide
  rule); at 1440×900 docked the Stats page's even gaps put three tile labels below the fold.
- **Open for the owner** (carried from sections 18 to 21, untouched by this wave's changes): the
  strip under the video stays available as a later option; a corner straight after another starts
  its clip at the previous exit; a long corner window plays a long clip; the ★'s two meanings on
  one tab; the rows scroll at the window's minimum size; the status line clears after 6 s while
  the row keeps saying "playing"; the first time costs three clicks from the debrief; with verdicts
  on the list, the docked chip says "3 of 3 re-measured"; a session with no laps is left out of the
  welcome's list, and an unreadable library shows the first-visit screen; the overlays use the
  0.07 s step even on an estimated clock; a re-profiled corner that keeps its direction is still
  compared; focus items and Library rows stored before 0.15.0 carry no direction until reopened; a
  one-corner shortlist's theme reads oddly; after the docked pair's Jump its Compare is one tab
  away; a missing file's name in the welcome is cut; `overlay.jpg` needs real footage to re-shoot;
  13 planted defects no test catches; two slow product paths; a marginal corner near the 30°
  detection limit; the mixed headline's count of refusals; a Compare started from the maximised
  page stays in the grid; the focus verdict's medians to two decimals; FIRST_LAP's loop paragraph
  and step 8; the report's Laps card scroll with sector lines; `fmt_time` negatives; Broadcast as
  the default Style; a ranked corner under the display floor; one lap drawn in two colours in a
  race over a pick; a session reload stranding widgets; the capture tool's `lapstrip.png` bytes;
  consistency on the middle half rather than σ; the apex and braking reasons' noise floor; the demo
  habit ranked only on its own seed; the overlay's ★/Δ under provisional timing; Extras on in
  Classic; the ◆ glyph; Follow's default; a Broadcast still; the keys 1-5 right after a load on a
  real display; pull request #582's commit list comment; the published real-footage Coaching
  tables cannot be re-verified offline.

## 23. Recorded at the 0.20.1 release, 2026-11-20

- **v0.20.1 is a patch release**: 1 user-visible pull request, #660, and three internal ones
  (#654, #656, #657). No capability was added and nothing on screen changed.
- **The hang.** A recording could fail to finish loading, and the app then wait on that load when
  quitting, if it was opened while the app was starting a helper process (ffprobe or ffmpeg: the
  export dialog's background probes, an export's render). numpy's maths library, OpenBLAS, shuts
  its worker threads down in the parent before every new process is started, and Python starts
  processes that way on macOS; a matrix product that was running on those workers in another
  thread lost them and spun forever. The one Pacer runs on every load large enough to use workers
  is the g-meter's rotation in `Session.load` (147,800 rows on the demo).
- **How it was found.** #654 made a Python test that runs into its CTest timeout print the running
  test's name and every thread's stack first. The next CI hang of `test_export_pass` (on a tree
  with #649) printed the load's main thread inside that matrix product, with no other Python
  thread alive. A minimal numpy-and-subprocess script then hung 10 runs of 10, and the app's own
  probes against a demo load, with an instant stand-in for ffmpeg, 12 of 20.
- **Default applied for the owner, 2026-11-20** (open to his overrule, reversible by deleting
  `_single_threaded_blas` in `studio/__init__.py`): **numpy's maths runs on one thread for the
  whole process**, set when `studio` is imported, before any window or load. With one thread there
  are no workers to strand, and every place the app starts a process is covered at once. A lock
  between process starts and numpy calls cannot be complete (any numpy call can use the workers),
  and starting processes another way would touch about 15 call sites. Measured on the demo
  (medians, 8 threads → 1), all within noise:

  | | 8 threads | 1 thread |
  |---|---|---|
  | `Session.load` | 256 ms | 253 ms |
  | window to loaded | 1105–1200 ms | 1097–1120 ms |
  | lap switch | 13.4–13.9 ms | 13.6–14.9 ms |
  | `rebuild_derived_views` | 62.0 ms | 62.5–62.9 ms |

  The g-meter arrays are bit-identical at 1 and 8 threads, and the golden gate's maximum |Δ| is 0.
  `tests/test_export_thread_safety.py` holds the thread count and races a forking thread against
  that matrix product in a child process under a timeout.
- **What v0.20.0 exposes, and how likely.** The race is in every release that starts ffmpeg off
  the main thread, v0.20.0 included: a recording opened within about a second of the export
  dialog's probes, or while an export starts ffmpeg, can stay on its loading screen, with the
  window still responsive, and the quit then waits on it. With the real ffmpeg the app-level
  reproduction hung 0 times in 6: the window is a matrix product of about half a millisecond
  inside a load of about 250 ms. It is rare in use, and it does not end on its own.
- **Not in 0.20.1.** #649 (the first export click opens at once, by probing the H.264 encoder in
  the background) was merged after 0.20.0 and reverted (#658) before any release: it added process
  starts beside the next load, which made the race likely in CI. Its probe logic was not the cause,
  and it will re-land unchanged.
- **The CI job limit** went from 20 to 35 minutes (#656). Five runs in a day were cancelled at 20
  minutes, two of them after every step had passed; 35 clears the slowest green run measured plus
  one test running out its 480 s timeout. The suite's time fell about 5 % (#657); its one product
  edit, measuring each distinct string once when an overlay sizes its widest text, gives the same
  widths exactly.
- **The media.** `hero.png`, `og.png`, `map.png`, `accuracy.png`, `debrief.png`, `moments.png`,
  `report.png`, `quickest.png` and `lapstrip.png` (its own run) were re-captured on the synthetic
  demo at 0.20.1 and are byte-identical to the files already committed. The waivers section 22
  renewed at 0.20.0 hold through 0.20.x and were not renewed.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. The release lane's app-level check (ten reloads straight after opening
  the export dialog, with real ffmpeg and with a forking stand-in) finished every load on 0.20.1,
  but with the fix undone in-process it did not hang either, so it shows no regression rather than
  the fix; the unit test's race does fail with the fix disabled.
- **Open for the owner**: section 22's lists stand, re-checked against this release, which touched
  none of their code. Its item on #649 now reads: re-land it unchanged.

## 24. Recorded at the 0.21.0 release, 2026-11-21

- **v0.21.0 is a minor release**: 9 pull requests, #659, #661 and #663 to #670, Waves 16 and 17 of
  the campaign section 8 records, its theme "every 'level' is read at one reference and says what
  that covers, and the quickest of many passes is allowed its luck".
  - #659: the reference centerline (the map's last-resort gap-fill donor) is fitted only for a
    session with a GPS gap long enough to need it; a session without one no longer spends ~0.2 s
    on it, and a session with one draws its map as before.
  - #661: the Quickest passes window tests wait on their condition instead of a fixed 0.3 s.
  - #663: #649 re-landed unchanged: the H.264 encoder is probed in the background, so the first
    File ▸ Export overlay video… of a session opens at once; the hint says it is measuring the
    encoder until it knows.
  - #664: the Library's summary counts a PB only where the debrief would (`library.pb_steps`): a
    best lap within 0.07 s of the earlier PB reads "level with your PB".
  - #665: Corners ▸ Lap N leaves a Δbest or Δref under 0.07 s, as printed, in the neutral colour.
  - #666: the Library's chart is titled "Best lap by session" and rings the sessions that set a PB.
  - #667: the focus verdict's cross-session step, measured on 40 synthetic days per GPS noise (two
    days' corner typicals each carry a bias that more laps do not remove), and both medians of a
    level line printed with digits enough that their printed gap stays under the step.
  - #668: the reference-lap corner step, measured the same way, for Δref and the "vs ref" cards.
  - #669: one reference level for every step, `library.REFERENCE_RECORDING` (published recording C),
    and a hover where the driver reads "level" saying what the step covers and what a noisier
    recording would need. #667 and #668 had each set its step at recording B's noise (0.10 s and
    0.12 s); at C's both rules fall under the timing step, so both read 0.07 s at release.
  - #670: the ★ and a quickest row's "Compare with my typical pass" must clear the step plus what
    the quickest of n passes reads quick by luck alone (`stats.quickest_luck_s`, about 0.02 s at
    14 passes). The demo's one ★ (its fifth corner, 0.071 s clear) went, and `quickest.png` was re-captured.
- **No new owner ruling.** The waves ran under section 19's of 2026-11-19 ("continue and don't
  come back to me").
- **What was decided for him**, each open to his overrule and each reversible:
  - **One reference level, recording C's**: the cleanest of the three recordings
    docs/ACCURACY.md times against official timing, the GPS9 camera's, and the level the timing
    step `library.PB_PRECISION_S` was always derived at. Every other step is measured
    at the synthetic noise whose per-lap σ matches C's, and the app applies the wider of that rule
    and 0.07 s. Changing it is one constant, `REFERENCE_RECORDING`: every step follows by itself.
  - **The luck allowance is added only where a gap is read off the quickest pass** (the ★ and a
    quickest row's typical offer), never to the step itself. Measured on 40 habit-free synthetic
    days × 7 corners at C's noise: 0.07 s alone offered the typical compare on 95 of 280 quickest
    rows, 55 of them truly under 0.07 s, and wore 6 stars, 4 false by their own claim; with the
    allowance, 50 offers, 11 truly under 0.07 s, and no star. A planted 0.10 / 0.15 / 0.20 s gain
    is still offered on 5 / 8 / 8 of 8 days (7 / 8 / 8 before), starred on 1 / 4 / 8 (2 / 7 / 8).
  - **The Library counts PBs by the debrief's rule** (#664); the bar a later session must beat is
    the fastest earlier best, level steps included, as the debrief's.
  - **A corner Δ's colour is decided on its printed value** (#665), as the corner cards decide.
- **Process.** The QA pass drove the merged tree in the real window, offscreen and jailed, at
  1440×900, with the theme applied: on the demo (Corners ▸ Lap N on all 14 laps, 98 Δ cells, 40
  coloured and none under 0.07 s as printed; Quickest passes with and without three mid-straight
  sector lines; every lap × stretch pass menu, 154 of them; each Coaching Jump's status line) and
  on five synthetic race days at one track, opened one after another with the focus list and the
  dry records written through the app's own gestures. On every surface the printed gap stayed
  under the bound it is said to be within: the luck reasons print theirs to the millisecond
  ("within 0.090 s"), the focus lines theirs to three decimals ("2.316 → 2.353 s"). The Library's
  summary and chart read "1 PB · level with your PB" and one ring where the debrief of each day
  said "level" or "New personal best". A reference recording set and cleared turned the header to
  Δref and back with the same colouring, and the cards to "vs ref" and back. The first export
  click opened its dialog 44-47 ms after the menu action, with the hint measuring the encoder; 20
  more opens of the export dialog and 20 of the Library through their real `exec()` left the
  top-level and widget counts level (78 and 1,090; 78 and 1,079).
- **The media.** `hero.png`, `og.png`, `map.png`, `accuracy.png`, `debrief.png`, `moments.png`,
  `report.png` (its own run), `quickest.png` (as #670 re-captured it) and `lapstrip.png` (its own
  run) were re-captured on the synthetic demo at 0.21.0 and are byte-identical to the files already
  committed, so the social card did not change. A report captured in the same run as the other
  shots differed from the committed one by a few hundred anti-aliased pixels round two corner
  labels; the same tree captured alone reproduces it byte for byte, as v0.20.1's studio/ does. The
  waivers of `data-trust.png` and `ideal-lap.png` are renewed: a demo render of both from v0.20.1's
  studio/ and from 0.21.0 is byte-identical. Those of `overlay.jpg`, `best-lap.jpg` and
  `best-lap.mp4` are renewed: 0.21.0 changed no export painter.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. A session with a long GPS gap (the side of #659 that still fits the
  reference line) was not opened in the window; `tests/test_reference.py` holds it. Hover timing,
  real macOS fullscreen and a second monitor cannot be checked offscreen. The full gate, the soak
  and the smoke run on the release branch, not in the QA pass.
- **Resolved from sections 22 and 23**: #649, the background encoder probe, re-landed unchanged
  (#663); the selection effect on the quickest row's typical offer and on the ★ (#670).
- **Open for the owner** (new).
  - **Which recording every step is referenced to.** Today it is C, the cleanest recording
    measured: lap, corner pass, two days' corner typicals and a corner against another recording's
    lap all read level under 0.07 s, and the hover says a noisier recording can differ by more. The
    alternative is B, the middle recording: lap 0.15 s, corner 0.12 s, cross-session 0.10 s, so
    more differences read "level" and fewer are claimed, and a best lap 0.10 s under the PB would
    read level too. The measured table is docs/ACCURACY.md's "What the 0.07 s step covers" (C 0.07 / 0.06 / 0.05 /
    0.06 s, B 0.15 / 0.12 / 0.10 / 0.12 s, A 0.25 / 0.20 / 0.16 / 0.21 s). In this release's QA a
    habit-free synthetic day at B's noise read one focus corner "0.08 s slower" than a habit-free
    day at C's: a change a B reference would have called level.
  - The luck allowance is fitted at C's noise only; at a noisier recording's the quickest pass is
    luckier than it allows.
  - Surfaces not yet luck-corrected: Coaching's time lost (against the best lap's pass), the story
    moments, the share card, the Stats sector hovers and the ideal lap's gap.
  - The split σ's re-measurement at the golden placement on the 40-day grid is deferred to its own
    package, with refused-2026-09 §26, whose new-evidence clause it is; `stats.SECTOR_LEVEL_S`
    (0.30 s) stands until then.
  - With a reference recording loaded, the Δref header's hover gives the bound twice: "reads up to
    that far apart" of the 0.07 s step, then "can differ by up to 0.06 s".
  - The Library counts no PB for a run of sessions that each beat the last by less than 0.07 s,
    however far they add up to (the debrief's own rule, now on both surfaces).
- **Open for the owner** (carried from section 22, re-checked against 0.21.0's changes): the
  selection effect stays on Coaching's ranking (the best lap's pass is partly a lucky one); the
  free-space estimate sizes a pass comparison by pane A's clip, up to 6.5 % low on the demo;
  section 13's question (should Coaching rank corners that lose under 0.07 s, and name a habit
  there?); Quickest passes is still not the Corners tab's default view; the readout and the
  overlays on real macOS fullscreen Spaces and a second monitor are unverified offscreen; the strip
  hides at 1280×800 docked for sessions of about 60 laps or more; at 1440×900 docked the Stats
  page's even gaps put three tile labels below the fold; sound after a chapter seam on the
  synthetic chapters runs 16.7 ms early; at a 30.000 fps output over a 59.94 source about 3 frames
  in 300 show the next source frame; hardware-decode exports were verified by the author's sweep
  and the release gate, not by an independent reviewer.
- **Open for the owner** (carried from sections 18 to 22, untouched by this wave's changes): every
  item of section 22's last list stands as written there.

## 25. Recorded at the 0.22.0 release, 2026-11-21

- **v0.22.0 is a minor release**: 5 pull requests, #672 and #674 to #677, Wave 18 of the campaign
  section 8 records, its theme "sector times get the receiver-drift correction corner times
  already had, and the ideal lap says in words that GPS noise lowers it too".
  - #672: the Δref header hover states its bound once (section 24's open item); Coaching's Jump
    line names its typical pass's lap and time beside "your typical pass".
  - #674: the ideal lap's sample sentence (the Stats line, the report, laps.csv) ends in a
    figure-free clause, `corner_model.IDEAL_NOISE_CLAUSE`; refused-2026-09 §39.
  - #675: the split σ re-measured at the one reference on the 40-day grid, at the worst of 13 line
    placements: the sector step 0.30 → 0.25 s, and the quickest sector's luck moved with it.
  - #676: each lap's trace is moved by its own robust receiver shift onto the session's consensus
    line before the sector lines are crossed; the step 0.25 → 0.15 s; the golden gate's sector
    leaves re-cut. A lap's start and finish do not move, so no lap time moved.
  - #677: a lap whose shift is refused is timed as measured, still counted, and its SPLITS cells,
    lap-table cells and a Quickest row it leads say so; a sector-line edit that leaves the start
    line where it was keeps the session's geometry instead of refitting it.
- **No new owner ruling.** The wave ran under section 19's of 2026-11-19.
- **What was decided for him**, each open to his overrule and each reversible:
  - **#673 was closed, not merged.** It printed the ideal lap's selection bias as a figure ("about
    0.17 s", scaled by piece and lap count). No one figure holds across recordings, piece counts
    and lap counts (0.17 s at C's noise, 0.57 s at B's, a fifth of it on a coarser cut), and a
    synthetic bias may not be printed on a surface (falsification-2026-09 §1, its last paragraph).
    The words shipped instead (#674).
  - **Refusal §39**: no luck allowance on Coaching's time lost, the biggest-loss moment, the share
    card's opportunity or the Stats sector hover; their selection bias is +0.008 to +0.019 s, under
    the timing step, and each is arithmetic on figures printed beside it.
  - **The sector step was re-derived, then de-drifted**: 0.30 s (truth matrix row 2) → 0.25 s
    (#675, the 40-day grid's worst placement, splits as timed) → 0.15 s (#676, de-drifted).
  - **The split column rests on an ASSUMPTION**: a receiver drift that decorrelates over about 30 s
    (`stats.SPLIT_DRIFT_TAU_S`). No recording has measured it; at 10 s the worst sector reads
    0.079 s, a 0.22 s rule, and the 0.15 s step under-covers.
  - **0.02 s of sampling headroom** (`stats.SPLIT_SAMPLING_HEADROOM_S`) on the house rule's 0.13 s,
    said as such; it covers every ten-day set measured but the noisiest (0.16 s).
  - **A refused lap stays counted and says so** (#677), rather than leaving the SPLITS grid and
    Quickest passes on different lap sets.
- **Resolved from section 24**: the split σ's re-measurement (#675, then de-drifted by #676, which
  meets refusal §26's first new-evidence clause; the chips stay refused until re-run); the
  surfaces not luck-corrected (refused, §39; the ideal lap's gap carries words instead); the Δref
  hover's two bounds (#672).
- **Process.** The QA pass drove the merged tree in the real window, offscreen and jailed, at
  1440×900 with the theme applied, on the demo and on four synthetic sessions (5 laps; 12 laps in
  three chapters; 14 laps with a planted 4 s GPS gap in two laps; the demo with a planted 20 m ×
  400 m excursion on one lap). On each it added 1 to 4 sector lines with the map's Add sector,
  dragged sector lines ten times through the handles' own release, the start line three times (by
  0.1 mm, then ±3 m), cleared and re-added lines, pressed Edit ▸ Undo six times, loaded and cleared
  a reference recording, and opened another session (driven the other way round) and came back.
  After each of the 30 steps every lap time, split, SPLITS matrix, Quickest sector row, drift
  status and split provenance equalled a fresh load of the same session with the same lines
  (16,948 checks, no difference); splits summed to their lap to 1 µs; no lap time moved on a
  sector-only edit; the lap table, the SPLITS grid and Quickest passes printed the session's
  values. The planted lap's cells, its Quickest row and its provenance said "timed as measured",
  and no other lap's did. A two-lap session's splits were timed as measured and its provenance
  says why. Quickest passes on six placements (2, 3 and 4 lines, mid-straight and evenly spaced):
  the legend reads "sectors 0.15 s", no ★, "Compare with my typical pass" on S1 alone, every
  greyed reason's printed gap under the bound it names (bounds to the millisecond with the luck);
  a sector compare's Δ is neutral below a printed 0.15 s. Twenty opens of the export dialog on a
  sector pass through its real `exec()` and twenty sector compares left the top-level and widget
  counts level.
- **The media.** `hero.png`, `og.png`, `map.png`, `accuracy.png`, `debrief.png`, `moments.png`,
  `quickest.png`, `report.png` (its own run) and `lapstrip.png` (its own run) were re-captured on
  the synthetic demo at 0.22.0 and are byte-identical to the files already committed. The waiver
  of `data-trust.png` is renewed: a demo render from v0.21.0's studio/ and from 0.22.0 is
  byte-identical. The waiver of `ideal-lap.png` is renewed with a new reason: the still predates
  #674's noise clause, a demo render now differs by it, and the Sandown 3h recording it is of is
  not on this machine. Those of `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed:
  0.22.0 changed no export code.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. The de-drift's 30 s assumption cannot be checked without a recording
  whose receiver drift has been measured. A sector line at a hairpin slower than the synthetic
  track's slowest point (13.5 m/s) is unmeasured. Hover timing, real macOS fullscreen and a second
  monitor cannot be checked offscreen. The full gate, the soak and the smoke run on the release
  branch, not in the QA pass.
- **Found in this release's QA, not fixed** (each minor; only the third comes from this wave):
  Quickest passes' sector "Level with" hover and ★ tip call the 0.15 s split step "the timing
  step", which the legend beside them gives as 0.07 s; the SPLITS grid's hover gives its ▼ floor
  as "0.3 s" without saying it is a display floor, not the 0.15 s level step; refusal §39's points
  1 and 3 and its new-evidence list still read the sector step as 0.30 / 0.25 s, measured on splits
  as timed before #676; each Inspect of a number (the provenance panel) leaves one closed dialog of
  about 50 widgets alive until the window closes; `tests/test_coaching.py` leaves a full-window
  Coaching page shown, so under one pytest process (not CTest's alphabetical script run) the debrief
  hero's cursor test hovers that page instead of the hero.
- **Open for the owner** (new, most important first).
  - **Which recording every step is referenced to**: still C, the cleanest recording measured, as
    section 24 set out; B would read lap 0.15 s, corner 0.12 s and sector split 0.28 s level.
  - **The sector step's drift assumption and its headroom.** 0.15 s rests on a 30 s drift and
    0.02 s of headroom. On the 30 s grid it names a planted 0.15 s gain on 6 of 8 days against 2 of
    8 at 0.25 s on splits as timed, about twice the sensitivity, for a pass menu that offers 5 and 7
    compares truly within 0.03 s where 0.25 s offered 1 and 3 (`stats.SECTOR_LEVEL_S`'s block,
    which docs/ACCURACY.md's split column rests on). Keeping 0.15 s takes that trade. A step of
    0.18 s would sit between the two, closer to the earlier false-offer record with fewer true
    offers; it was not run on the grid, so how many of each is unmeasured. A faster drift (10 s)
    would need about 0.22 s.
  - **Whether a figure may ever be printed on the ideal lap.** Today words only (§39); a figure
    needs a way to read a recording's own noise and its piece count's effect.
  - **"Compare with my typical pass" on a NON-quickest pass carries no luck allowance.** On the
    demo with 2, 3 and 4 lines on six placements, 52 of 336 lap × sector menus offer it.
  - **A sector line at a hairpin slower than the synthetic track's slowest point** (13.5 m/s) is
    unmeasured; the split's error is largest at a slow line.
- **Open for the owner** (carried from section 24, re-checked against 0.22.0's changes): the luck
  allowance is fitted at C's noise only; the Library counts no PB for a run of sessions that each
  beat the last by less than 0.07 s; the selection effect stays on Coaching's ranking; the
  free-space estimate sizes a pass comparison by pane A's clip, up to 6.5 % low on the demo;
  section 13's question (should Coaching rank corners that lose under 0.07 s, and name a habit
  there?); Quickest passes is still not the Corners tab's default view; the readout and the
  overlays on real macOS fullscreen Spaces and a second monitor are unverified offscreen; the strip
  hides at 1280×800 docked for sessions of about 60 laps or more; at 1440×900 docked the Stats
  page's even gaps put three tile labels below the fold; sound after a chapter seam on the
  synthetic chapters runs 16.7 ms early; at a 30.000 fps output over a 59.94 source about 3 frames
  in 300 show the next source frame; hardware-decode exports were verified by the author's sweep
  and the release gate, not by an independent reviewer.
- **Open for the owner** (carried from sections 18 to 22, untouched by this wave's changes): every
  item of section 22's last list stands as written there.

## 26. Recorded at the 0.23.0 release, 2026-11-21

- **v0.23.0 is a minor release**: 9 pull requests, #679 to #687, Wave 19 of the campaign section 8
  records, its theme "any Library session can be the reference, picked and named by its day, and a
  race day's sessions are told apart and kept in order".
  - #679: section 25's three copy and leak findings: a sector row's hovers name "the step for
    sector splits", the SPLITS hover prints its ▼ floor as 0.30 s and says what it marks, and
    "Inspect this number…" frees its panel on close; refusal §39's sector figures re-read at the
    de-drifted 0.15 s step.
  - #680 (tests only): a Coaching page a test shows is closed by that test; a σ is never used as a
    step; a missing sample says so.
  - #681: a reference recording driven the other way round on the same track is refused, with
    that reason.
  - #682: Library rows, the welcome list and Open Recent print each session's local start time
    after its ISO date and sort on date then start, newest first.
  - #683: a focus verdict refused for want of a session record between sessions of one day names
    and counts them.
  - #684: Corners ▸ Lap N's Δapex is coloured only from `chart_stats.GAIN_GATE_KMH`, 1.75 km/h,
    the gate the speed chart's gain shading uses; docs/ACCURACY.md "Two speeds".
  - #685: the PB card and the status line's short confirmations end with the session they were
    about.
  - #686: the Library's row menu and a "Use as reference" button load a row as the open session's
    reference; a reference is named by its date, start time and best lap on every surface.
  - #687: the release QA's findings on #686 and its neighbours (below).
- **No new owner ruling.** The wave ran under section 19's of 2026-11-19.
- **What was decided for him**, each open to his overrule and each reversible:
  - **One speed gate for the table and the chart**: 1.75 km/h, the house rule (2√2σ) on the
    per-sample speed noise measured on a real recording (σ 0.62 km/h, recording A, median of 24
    laps). A synthetic 1.02 km/h step was refused on review: it depended on the assumed apex shape
    and coloured 7.5–17 % of truly level cells on curved and V-shaped apexes.
  - **A reversed reference is refused**; a way round the loader cannot read, or a pair where either
    track is unnamed, is left to the loader, which decides by location first.
  - **Rows keep the ISO date, with the start time after it** ("2026-11-15 16:00"), so the column
    still sorts and reads as before for a day with one session.
  - **The Library's entry reuses the one reference loader and its reasons**: a row is greyed only
    with a phrase out of the loader's own refusal, in the loader's order, so the Library never
    gives a reason the loader would not (#687's matrix, `tests/test_cross_reference.py`).
  - **No best-of-the-day mark was built in the Library**: ★ already means a session's best lap,
    and a second meaning for it would make the column ambiguous.
- **Resolved from section 25**: the sector "timing step" wording, the ▼ floor's hover and refusal
  §39's sector figures (#679); the provenance panel kept alive after close (#679); the Coaching
  page left shown by `tests/test_coaching.py` (#680).
- **Process.** The release QA drove every unreleased pull request (#679 to #686) in the real
  window, offscreen and jailed, at 1440×900 with the theme applied, on a synthetic season of 19
  recordings: four race days at the demo circuit (one wet, one driven the other way round, one
  session with a planted GPS gap), two sessions at an unnamed circuit about 160 km away, and a
  no-laps recording. It found one finding that held the release (the Library greyed an unnamed
  other-circuit row as "driven
  the other way round", where the loader refuses it for location) and six minor items, all fixed
  in #687. A re-check on the merged tree, through the real dialogs: every row's reason matched what
  the loader then said, in both directions between the circuits, before and after "Save as track"
  named the second; a refused pick kept the loaded reference and said so by its date and best lap;
  the button's no-selection and no-session states, the welcome's transients and the five same-day
  wordings matched #687, and each "Mark … dry" wrote exactly the sessions its hover named.
- **The media.** `hero.png`, `og.png`, `map.png`, `accuracy.png`, `debrief.png`, `moments.png`,
  `quickest.png`, `report.png` (its own run) and `lapstrip.png` (its own run) were re-captured on
  the pinned synthetic demo at 0.23.0 and are byte-identical to the files already committed. The
  waivers of `data-trust.png` and `ideal-lap.png` are renewed: a demo render of each from 0.23.0
  is byte-identical to 0.22.0's. Those of `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are
  renewed: 0.23.0 changed no export code.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. A second circuit's second recording could be checked as a reference
  only through Coaching's loader until "Save as track" named the circuit: the Library lists an
  unnamed recording only once its start line is trusted. Hover timing, real macOS fullscreen and
  a second monitor cannot be checked offscreen. The full gate, the soak and the smoke run on the
  release branch, not in the QA pass.
- **Found in this release's QA, not fixed** (each minor): a 0-lap row on an unnamed far-away track
  is greyed "no valid laps", true of the row, where the loader's first words would be "different
  location"; the command palette cannot reach a row action; a different-day focus refusal with
  baselines on several days names only the first baseline's day in its collapsed line; beside
  earlier days the open session is called "today" even when it was recorded on another day.
- **Open for the owner** (new, most important first).
  - **Should the focus list be kept per direction, as the PB is?** Today a day driven the other
    way round reads "can't say" on every debrief line and promotes nothing.
  - **Coaching's apex-speed reason needs its own measured step.** It names gaps of 0.5–0.9 km/h
    that sit within 1–2× that statistic's own noise (sd 0.37–0.61 km/h on synthetic days).
  - **A best-of-the-day mark in the Library** needs a new meaning for ★ or a new mark.
  - **A wet day draws as an unmarked hump on "Best lap by session".**
  - **A recording whose first chapter is missing opens as whole**, with no notice.
  - **In mph the 1.75 km/h gate prints as 1.09** and colour starts at a printed 1.1 (a real
    1.69 km/h): consistent as printed, a hair under the chart's gate.
- **Open for the owner** (carried from section 25, re-checked against 0.23.0's changes): which
  recording every step is referenced to (still C); the sector step's 30 s drift assumption and its
  0.02 s headroom; whether a figure may ever be printed on the ideal lap; "Compare with my typical
  pass" on a non-quickest pass carries no luck allowance; a sector line at a hairpin slower than
  the synthetic track's slowest point is unmeasured; and section 25's list carried from section 24
  stands as written there.
- **Open for the owner** (carried from sections 18 to 22, untouched by this wave's changes): every
  item of section 22's last list stands as written there.

## 27. Recorded at the 0.24.0 release, 2026-11-21

- **v0.24.0 is a minor release**: 13 pull requests, #689 to #696, #698 and #700 to #703, Wave 21
  of the campaign section 8 records, its theme "recordings that went wrong: each says what happened, and none
  decides a best lap it should not".
  - #689 (docs and a probe): refusal §40, no apex-speed step of its own for Coaching's reason;
    every step that could be measured withheld more true reasons than it removed false ones.
  - #690: a session whose record says wet is a hollow point on "Best lap by session" and hovers
    "Wet"; the title adds "○ wet" while one is plotted.
  - #691: a missing first chapter is named in the session notice; a missing middle chapter is
    named and the recording decides no PB, focus list or Library row until it is in the folder.
  - #692: Corners ▸ Lap N's Δapex is coloured only when every gap its figure rounds from is past
    the gate; in mph colour starts at 1.2.
  - #693: the open session is "today" only on the day it was recorded, another day by its date,
    an undated one "this session".
  - #694: a recording with no GPS says so in its lap, chart and map panels, and the map's line
    tools are off.
  - #695: a lap left out with a long stop in it says "the kart stopped during it", not "off the
    session median"; which laps are left out is unchanged.
  - #696: a refused reference says why in the main open's plain words, and one with no GPS says
    so.
  - #698: a lap that left the line the other laps drive is kept out of the bests, listed with its
    time and a ⚠ that says why; refusal §41 records the lap-distance rule of #697 (closed, not
    merged).
  - #700: a lap timed across a missing middle chapter wears its own ⚠, "timed across a missing
    chapter", is kept out of the bests, and laps.csv flags it `chapter-gap`.
  - #701: on a recording with no GPS, Load reference recording…, Compare with your previous PB and
    the Library's Use as reference are off, with that reason.
  - #702: a wet point's hover on "Best lap by session" names its start time, as the Library row
    does.
  - #703: one ⚠ set on every surface: the Lap Race, the export ghost, Quickest passes' hover, the
    Stats page's LAPS, CORNERS BY LAP and PACE, and compare's default partner, each lap's reason
    worded from its `data_quality` kind.
- **No new owner ruling.** The wave ran under section 19's of 2026-11-19.
- **What was decided for him**, each open to his overrule and each reversible:
  - **A missing first chapter gets a notice only, and keeps its verdict, PB and Library row.** Its
    laps are real, only truncated, and a camera's loop recording deletes early chapters. **A
    missing middle chapter waits** until it is in the folder: a lap timed across the gap is wrong
    (on a synthetic three-chapter recording it was 31.798 s and became the best lap).
  - **Cut laps: a spatial rule, not a distance rule.** The lap-distance rule was refused (§41): a
    tighter line shortens a lap about as much as a cut on a twisty layout. Shipped instead: a lap
    10 m off the session's consensus line, held for 10 m of track, is off the line; only ever a
    minority, under two-fifths of the laps (with 40–60 % on another line nothing is flagged).
    Synthetic margins: legitimate laps at most 6.99 m (a 3 m receiver jump), infield cuts from
    13.5 m; it misses a cut that saves under ~9 m, as it misses an honest off.
  - **The Δapex figure is coloured only when every gap that rounds to it is past the gate**, so a
    coloured cell never stands for a gap the speed chart leaves unshaded; in mph the first coloured
    figure is 1.2, and a printed +1.1 mph stays neutral even for gaps of 1.75–1.85 km/h.
  - **"Today" only on the recording's own day.** Another day is named by its date, with the year
    added when two different days share a label.
  - **Wet is a hollow point, and only the `wet` tag counts.** Damp and mixed are the driver's
    in-between words, and the chart does not guess how wet they were.
- **Resolved from section 26**: the wet day's unmarked hump (#690); the missing first chapter
  that opened as whole (#691); the mph gate's printed 1.1 (#692); Coaching's apex-speed step,
  refused with numbers (#689, §40); "today" for a session recorded on another day (#693).
- **Process.** The release QA drove the merged tree in the real window, offscreen and jailed, at
  1440×900 with the theme applied, on synthetic recordings only: the pinned demo, and imperfect
  recordings of the demo circuit (an infield cut, the same cut recorded as three chapters with the
  first or the middle one removed, a cut and a stop in one lap, no GPS). It read
  the Laps, Corners, Quickest passes, Stats and Coaching pages, the Lap Race, the reference loader,
  the Library and the exports (laps.csv, the report, the lap card and the lap strip). It found two
  findings that held the release and four minor ones, all fixed in #700 to #703 (below). A
  re-check on the merged tree repeated every repro and added one recording with all three ⚠ kinds
  (a GPS dropout in chapter 1, chapter 2 removed, an infield cut in chapter 3): each lap wears its
  own reason in the Laps tab, the Stats page, Quickest passes, the Lap Race card, laps.csv and the
  report, and none is best, raced or counted. The demo's laps, best lap (lap 13, 0:44.950) and
  ideal lap (44.631 s) read the same from v0.23.0's studio/ and from 0.24.0; no demo lap is ⚠.
- **The media.** `hero.png`, `og.png`, `map.png`, `accuracy.png`, `moments.png`, `quickest.png`
  and `report.png` (its own run) were re-captured on the pinned synthetic demo at 0.24.0 and are
  byte-identical to the files already committed. One earlier capture of `hero.png` in this
  release came out different in the Δ chart's axis gutter (0.37 % of pixels), as did one from
  v0.23.0's studio/ on the same day: the hero render is not deterministic in that gutter, and
  the committed pixels are the ones kept. `debrief.png` changed in one line, the note's "from
  today's top corners", now "from 1 Jun's top corners" (#693: the demo is dated 1 June).
  `lapstrip.png` moved its "median" and "best" labels by a fraction of a pixel (442 pixels, the
  strip's drawing changed in #698). The waivers of `data-trust.png` and `ideal-lap.png` are
  renewed: a demo render of each from 0.24.0 is byte-identical to 0.23.0's. Those of
  `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed: the one export change is the
  ghost's unavailable sentence (#703), which no published still draws.
- **What could not run for it.** No real footage was used, so every `footage.*` check reports
  Skipped by name, and whether any real lap is flagged off the line on Sandown 3h was not
  re-measured here (#698 measured none on its reference recording). The full gate, the soak and
  the smoke run on the release branch, not in the QA pass.
- **Found in this release's QA between its pull requests, and fixed before the tag:**
  - the Lap Race raced a cut lap as clean, so it led and finished first (#698 had left
    `Session.race_tracks` out of the ⚠ set): fixed in #703, the card now names lap 10 as not raced,
    off the line;
  - with a middle chapter missing, the lap timed across the gap was flagged "left the line your
    other laps drive by 59 m" and raced first at 31.799 s (#691 × #698): fixed in #700 (its own ⚠,
    "timed across a missing chapter (chapter 2, …)", laps.csv `chapter-gap`) and #703 (not raced);
  - Quickest passes' "not counted" hover gave dropout reasons for a cut lap, and the Stats page's
    LAPS tile, CORNERS BY LAP note and lap-time distribution hover counted only dropouts: fixed in
    #703, each lap with its own reason;
  - on a recording with no GPS, Load reference recording… stayed enabled and a refused pick said
    "different location; keeping the local best lap" (#694 × #696): fixed in #701, the entries
    are off with the reason;
  - two wet sessions on one day hovered the same text: fixed in #702.
- **Found in the re-check, not fixed** (each minor): a lap timed across a missing chapter whose
  join also reads as a GPS gap is counted twice in DATA TRUST ("GPS dropout: inside 2 of 9 laps"
  beside its "Missing chapter" row) and in the report's summary ("2 with a GPS dropout · … · 1
  across a missing chapter" over 3 ⚠ laps), while the LAPS tile and CORNERS BY LAP count it once;
  the Lap Race names a GPS-dropout lap "no on-track match" when its warp fails, as before this
  release, because that test runs before the ⚠ kind.
- **Open for the owner** (new, most important first).
  - **No "include this lap" override for a ⚠ lap.** A lap flagged off the line is out of the bests
    with no way to put it back, even when the driver knows it was an honest lap.
  - **A no-majority session flags nothing.** With 40–60 % of the laps on another line, the rule
    stands down, so a cut lap can still be the best lap.
  - **laps.csv drops an excluded lap** (stopped, or outside the band) with no row and no note.
  - **A 2D-fix stretch outside every timed lap downgrades the whole session's verdict** (discovery
    10, finding 2); unmeasured on real footage.
  - **Renamed chapter files are treated as separate recordings.**
  - **Legacy GOPR/GP chapter numbering** is named in the notice, but sibling discovery still never
    chains `GOPRnnnn` with its `GPccnnnn` chapters.
  - **The DATA TRUST "clock drifted … ppm" figure on a very short recording is fit noise.**
  - **The map's gap-fill can still borrow from an out-of-bests lap** (`render_cache.donors_for`):
    a GPS gap could be drawn from a cut lap's line.
  - **Coaching's lap account counts only dropouts** (`coaching._lap_account`); it cannot change
    what Coaching says today, but its empty-state reason would miss an off-line or gap lap.
  - **An explicitly chosen out-of-bests best lap is exempt from the race rule**, where a dropout
    best still sits out: an asymmetry between the ⚠ kinds.
  - **With a lap boundary within one fix of a chapter join, only the lap after the crossing is
    flagged** as timed across the missing chapter.
- **Open for the owner** (carried from section 26, re-checked against 0.24.0's changes): whether
  the focus list should be kept per direction, as the PB is; a best-of-the-day mark in the
  Library needs a new meaning for ★ or a new mark; and section 26's carried lists stand as
  written there.

## 28. Recorded at the 0.25.0 release, 2026-11-21

- **v0.25.0 is a minor release**: 10 pull requests, #704 to #712 and #714, Wave 22 of the campaign
  section 8 records, its theme "what leaves the app, and what a missing GPS fix does".
  - #704: each ⚠ lap is counted once, under the kind it wears, in DATA TRUST and the report's
    summary; the Lap Race names a GPS-dropout lap it leaves out "GPS dropout", not "no on-track
    match".
  - #705: laps.csv names each excluded lap in its trailer (`summary: excluded lap N`, its time,
    and `excluded-stopped`, `excluded-band` or `excluded-open` with its distance and reason); the
    report shows it as a muted row in lap order.
  - #706: the lap strip says a slow lap's bar is "clipped at" its scale line, not "cut at" it.
  - #707: an export whose folder is gone by render time says so, not "the encoder stopped".
  - #708: the export dialog's top line names the lap the Export row will render.
  - #709: the GPS-quality verdict judges the rejected-fix share over the timed laps, first to last
    crossing, with a guard band of 14 fixes beyond either edge; the status bar names rejected
    driving outside the laps when it alone would have read low.
  - #710: a 9:16 export keeps every overlay out of the top 8 % and bottom 18 % of the frame.
  - #711: a ⚠ lap's one-lap clip and compare pane say "⚠ not counted" with a neutral Δ, its
    minimap draws the best lap's line, the export dialogs name the reason, and the report's ⚠
    column is headed "Not counted" with a key.
  - #712: a lap with a GPS fix missing within the smoother's reach of its start/finish crossing,
    or with the trace starting or ending there, wears the GPS-dropout ⚠; when more than half the
    valid laps wear it the session reads GPS quality low.
  - #714 (from this release's QA, below): the off-line rule measures every lap whose trace is
    whole, skipping only a gap inside the lap or a lap timed across a missing chapter; when every
    lap is ⚠ the best lap is a dropout-only lap or none, never one off the line or timed across a
    missing chapter; the "outside your laps" notice stays when the lap vote made the session low;
    one dropout mark per hole.
- **No new owner ruling.** The wave ran under section 19's of 2026-11-19.
- **What was decided for him**, each open to his overrule and each reversible:
  - **Excluded laps go in laps.csv's trailer, not as rows.** The file promises one row per valid
    lap, and a reader who sums or averages `time_s` would take in an 81.7 s stopped lap.
  - **The GPS-quality verdict is judged inside the timed laps**, with a guard band of twice the
    smoother's reach beyond either edge (14 fixes, 1.4 s at 10 Hz). It is kept wide on purpose:
    rejected fixes ending 0.35–0.60 s before the first crossing moved that lap by up to 282 ms,
    and the band re-downgrades two synthetic sessions whose laps are exact (a 2D fix, or no lock,
    until 1.24 s before the first crossing).
  - **Timing is not changed for missing fixes; the laps are flagged instead.** A lap with a hole
    within the smoother's reach of a crossing wears the dropout ⚠. The time-centred smoother was
    measured (its largest change to an unflagged lap 4–12 ms) and deferred: it moves 25,665
    non-lap-time leaves of the synthetic golden and needs a reviewed re-cut.
  - **When more than half the valid laps wear the dropout ⚠, the session is low**: no PB, focus
    list or debrief, and a degraded Library row. Laps timed across a missing chapter do not vote.
  - **One-lap clips and compares carry "⚠ not counted" with a neutral Δ.** Full-session clips keep
    no per-lap tag (the refusal stands: a tag would appear and vanish mid-clip), but draw a ⚠
    lap's Δ neutral while it is on screen.
  - **The portrait safe area is 8 % at the top and 18 % at the bottom** of a 9:16 frame;
    landscape and square are untouched. Refusal §25 was re-measured on the row the overlay now
    uses, and still stands.
- **Resolved from section 27**: the lap counted twice in DATA TRUST and the report's summary, and
  the Lap Race's "no on-track match" for a GPS-dropout lap (#704); laps.csv dropping an excluded
  lap with no note (#705); a 2D-fix stretch outside every timed lap downgrading the whole session
  (#709, with the guard band's cost above).
- **Process.** The release QA drove the merged tree in the real window, offscreen and jailed, at
  1440×900 with the theme applied, on synthetic recordings only: the pinned demo, and the demo
  circuit with gate-rejected fixes scattered at 3 % and 5 %, single fixes missing just before 2 to
  11 of the start/finish crossings, rejected driving outside the laps with and without such
  holes, a 30 s block inside a lap, a red-flag stop and an infield cut, each with holes added at
  read time. It read the banner, the GPS LOW chip and its hover, the status bar, DATA TRUST, the
  Stats tiles, the Laps tab's hovers, the marks, the Lap Race card, the Library row, laps.csv,
  the report (rendered), the lap strip and the export dialogs, and looked at exported frames: a
  ⚠ lap's Classic and Broadcast clips in landscape and 9:16, the best lap's clip in a low
  session, and a compare. On the demo its laps, best lap (lap 13, 0:44.950) and ideal lap
  (44.631 s) read as in 0.24.0, no lap is ⚠ and the verdict is clean; its laps.csv, lap card and
  best-lap and lap-12 clips (decoded at 20 s and at the last frame) are byte-identical from
  v0.24.0's studio/ and from 0.25.0.
- **Round 2, after #714**, on the merged tree: the infield-cut recording with fixes missing at six
  crossings now names lap 10 "left the line … by 34 m" in the Laps tab, laps.csv (`off-line`),
  the report and the Lap Race card, and the best lap is lap 9 (0:45.190), a lap with no ⚠; with
  a hole at lap 10's own crossing it still reads off the line. With a fix missing at every
  crossing, lap 10 is off the line and the best is a dropout-only lap. A session in the lap-vote
  low state keeps its "outside your laps" notice, and one hole draws one mark. A forced state
  with every lap off the line (no dropout-only lap) shows no best on the Laps tab, Stats and
  report, decides no debrief or Library best, refuses the lap card and lap strip, and crashes
  nothing. The demo's figures and its laps.csv, lap card and clip frames are again
  byte-identical to v0.24.0's.
- **The media.** `hero.png`, `og.png`, `map.png`, `accuracy.png`, `moments.png`, `quickest.png`,
  `debrief.png`, `lapstrip.png` and `report.png` (its own run) were re-captured on the pinned
  synthetic demo at 0.25.0 and are byte-identical to the files already committed. One capture of
  `lapstrip.png` came out different in its "median" and "best" labels (442 pixels, the same two
  labels as at 0.24.0) and a second reproduced the committed file: that render is not
  deterministic in those labels, and the committed pixels are the ones kept. Re-checked after
  #714: `map.png`, `accuracy.png`, `moments.png`, `quickest.png`, `debrief.png` and `report.png`
  are byte-identical again; `hero.png` (and `og.png`, cut from it) came out different in the Δ
  chart's axis gutter only (0.37 % of pixels), the same output from the tree before #714 as from
  the one after it, as at 0.24.0; `lapstrip.png` flickered between its two states. The committed
  pixels are kept. The waivers of `data-trust.png`, `ideal-lap.png`, `overlay.jpg`, `best-lap.jpg`
  and `best-lap.mp4` are renewed: a demo render of the data-trust shot changed in its Timing
  row's words only (#709: "0% of fixes inside your laps rejected"), the ideal-lap shot is
  byte-identical, and the export changes (#710, #711) reach neither a landscape best-lap clip nor
  a frame of a lap with no ⚠; each was re-checked after #714 with the same result.
- **What could not run for it.** No real footage was used, so every `footage.*` check reports
  Skipped by name, and whether any lap of Sandown 3h wears the new dropout ⚠ was not re-measured
  here (#712's real-footage golden dump was unchanged). The full gate, the soak and the smoke run
  on the release branch, not in the QA pass.
- **Found in this release's QA between its pull requests, and fixed before the tag** (#712 ×
  #698): the off-line test measured only laps with no dropout, and #712 widened the dropout to
  near-line holes. On the infield-cut recording with single fixes missing at six crossings (11 of
  14 laps ⚠, the session low), lap 10, 929 m and 34 m off the line, was "★ Session best" at
  0:44.227 in the Laps tab, the lap strip, the lap card and the report, where 0.24.0 kept it out
  on the same input; and a cut lap with a near-line hole was named "GPS dropout". Fixed in #714,
  with two minor ones found beside it: the "outside your laps" notice dropped in the lap-vote low
  state, and two identical marks for one hole.
- **Found in the round-2 QA, not fixed.**
  - **A cut lap is still the best when fewer than four laps have a whole trace**, as in 0.24.0:
    on the infield-cut recording with a 1 s gap inside 11 of its laps, lap 10 (0:44.227) is
    unflagged and best, in a session that reads low; v0.24.0's studio/ gives the same on that
    input. The off-line rule needs four laps to fit its line.
  - **The lap-vote sentence says "GPS fixes are missing at the line on 11 of 14 laps"** when the
    laps' dropouts are gaps inside them, not at the line.
  - **With no best lap, File ▸ Export overlay video… warns that the timing is provisional** and the
    clip will carry a PROVISIONAL mark, on a verified start line; the lap card and lap strip say
    "no verified lap". Seen only in a forced state with every lap off the line.
  - **The lap strip's legend truncates** "quicker, GPS dropout or left the line (not …" when
    quicker ⚠ laps of two kinds share a line.
- **Open for the owner** (new, most important first).
  - **Sector splits and the ideal lap can move by about ±46 ms when a fix is missing near a
    sector line**, with nothing flagged: the new rule looks only at the start/finish crossings.
  - **The bests fallback still crowns a dropout-only ⚠ lap when every lap is flagged** (in the
    session only; the PB is refused), and the lap card shows it as "BEST LAP" with no ⚠.
  - **The time-centred smoother's re-cut**, which would fix the values instead of flagging them.
  - **A dropout shorter than 0.35 s in the last 0.7 s before the line counts as a whole trace** for
    the off-line rule.
  - **A ⚠ lap whose only hole sits just past its finish has its mark under the neighbouring lap.**
  - **The ghost's reason says a lap with one fix missing at its line has a "place on track [that]
    is a guess".**
  - **The report's "Not counted" cell wraps "gps-dropout" at its hyphen.**
  - **DATA TRUST's Timing row can read "0% of fixes inside your laps rejected — GPS fixes missing
    at the line on 8 of 14 laps".**
  - **The lap card and the lap strip stamp "estimated timing (est)"** on a true-clock session that
    is low by the lap vote, as they already did for a low share.
  - **laps.csv has no key row for `flag`.**
  - **A ⚠ lap's corner-pass clip is untagged.**
  - **An off-line lap has no Quality code** in the report or laps.csv.
  - **The burned stamp says "% of fixes rejected"** for the share inside the laps.
  - **The export box's header says "couldn't finish" when the render never started.**
  - **A hole is judged against the trace's median period**, so a recording whose rate changes
    mid-way would misread its steps.
- **Open for the owner** (carried from section 27, re-checked against 0.25.0's changes): no
  "include this lap" override for a ⚠ lap; a no-majority session flags nothing; renamed chapter
  files are treated as separate recordings; legacy GOPR/GP chapters are named but not chained; the
  DATA TRUST clock-drift figure on a very short recording is fit noise; the map's gap-fill can
  borrow from an out-of-bests lap; Coaching's lap account counts only dropouts; an explicitly
  chosen out-of-bests best lap is exempt from the race rule; with a lap boundary within one fix
  of a chapter join, only the lap after it is flagged; and section 27's carried lists stand as
  written there.

## 29. Recorded at the 0.26.0 release, 2026-11-27

- **v0.26.0 is a minor release**: 1 pull request, #715, Wave 23 of the campaign section 8
  records, its theme "a missing GPS fix no longer moves your lap".
  - #715: the load smoother is time-aware. A fix whose 13-fix window holds a missing fix, or is
    cut by a gap over 1 s inside the trace, takes the value of a straight line fitted in time
    through the fixes within ±6.5 periods of it; every other fix keeps the old value. The
    dropout ⚠ at the line now needs two or more fixes missing in a row within 3 periods of a
    crossing, or the trace starting or ending within 7 fixes of one. `EDGE_GUARD_FIXES` goes
    from 14 to 8. The synthetic golden baseline was re-cut (reviewed: no lap-time leaf moved),
    `corner_model.IDEAL_VERSION` goes from 1 to 2, and refusal sections 24, 27, 28 and 30 to 34
    had their figures re-run (no verdict changed).
- **The owner's ruling for this wave**: fix the smoother itself and re-cut the golden baseline,
  rather than flag the laps it moves (section 28 deferred exactly that re-cut).
- **What was decided for him**, each open to his overrule and each reversible:
  - **A local straight-line fit, chosen by measurement** over the symmetric-pair boxcar and the
    time-window boxcar. Over the synthetic matrix (10 laps × 5 seeds, 10 and 18 Hz, against the
    generator's true crossings), the largest move of a lap with no interior gap at native noise
    was 3.8 ms with 1–10 % of fixes rejected (pairs 7.4, time window 132.8, the old boxcar 83.8)
    and 20.9 ms beside holes of 0.3–1.5 s (33.1, 300.7 and 305.2), with no false-fast lap; at 3×
    and 6× noise it had the lowest maximum in every family.
  - **Bit-identity on a recording with no missing fix is a hard constraint.** Every fix away from
    a hole is computed by the very same operations as before; the bundled sample clips and the
    real-recording golden dump are identical (EQUIVALENT, max |Δ| = 0). For that reason the
    trace's own first and last fixes keep the old one-sided average, and a lap whose trace starts
    or ends within 7 fixes of its crossing still wears the ⚠ (such laps moved by up to a third of
    a second).
  - **The dropout ⚠ at the line is re-scoped** to two or more fixes missing in a row within 3
    periods of a crossing, the size and reach set by a sweep on the matrix: with no hole rule,
    laps with a hole right at the line read up to 20.9 ms off at native noise (48 ms at 3×,
    111 ms at 6×). Every unflagged lap is now within 4.5 ms of its clean-trace time at native
    noise (14.5 ms at 3×, 28 ms at 6×). At 1, 2, 5 and 10 % rejected at random, 0, 0, 1 and 27 %
    of laps wear the ⚠ (37, 50, 78 and 99 % in 0.25.0), and 0, 0, 0 and 2 of 10 sessions read low
    by the lap vote (2, 4, 8 and 10).
  - **The GPS-quality guard band narrows from 14 fixes to 8** (1.4 s to 0.8 s at 10 Hz): rejected
    fixes from the start of the recording ending 0.35–0.75 s before the first crossing moved that
    lap by up to +308 ms, a trace end the fit does not reach; from 0.8 s the lap was exact. The
    1.24 s lock case section 28 named is now judged over the laps.
  - **The two demo conclusions that moved are accepted as corrections toward the truth.** The
    demo's quickest pass through corner 2 is lap 7 (2.6230 s, also the true quickest), where
    lap 11 read quickest only because a missing fix sat inside its pass, 50 ms quick of its truth; and the
    report's ideal-lap gap reads 0.30 s, not 0.32 (the demo's ideal lap 44.6315 → 44.6505 s, 19 ms
    closer to the truth of its own partition and still 29 ms under it).
  - **`IDEAL_VERSION` goes to 2**, so every Library row written before 0.26.0 shows its ideal lap
    muted, with the hover "Ideal lap measured by an older Pacer — re-open the recording to update
    it", until that recording is re-opened. Nothing is deleted or rewritten, and the best lap and
    the PB do not read the stamp. A stored row records nothing about holes, so no row could be
    declared unchanged; a recording with no missing fix re-measures the same ideal on re-opening.
  - **Removing the bias does not bring every single pass closer to the truth**, and the public
    text says so: of the four moved demo cells three are closer and one seeded pass (lap 11's,
    corner 2, on `DEMO_SEED+6`) went from 18 to 37 ms off, its own GPS noise now unmasked. The evidence for
    the fit is the matrix, not one pass.
- **Resolved from section 28**: the time-centred smoother's re-cut (#715); a missing fix near a
  sector line moving the splits by about ±46 ms, in its smoother part (on the QA's 14-lap
  synthetic recording, unflagged laps' splits moved by at most 2.3 ms at 2 % rejected and 4.4 ms
  at 5 %, where 0.25.0's moved by 65 and 74 ms; the distance-mapped part stays open, below).
- **Process.** The release QA drove the merged tree in the real window, offscreen and jailed, at
  1440×900 with the theme applied, on synthetic recordings only, and loaded each case through
  the real loader of both 0.26.0 and v0.25.0's studio/: the pinned demo (rendered locally,
  byte-identical to the published asset); a 14-lap two-chapter recording with 1, 2, 5 and 10 %
  of fixes rejected at random, one and two fixes missing before three crossings, the trace
  starting or ending 0.5 and 1.0 s from a crossing, a 1.5 s gap ending 0.5 and 0.15 s before a
  crossing, a 0.3 s hole ending 0.3 s before one, a 0.6 s hole mid-lap, every fix rejected, the
  infield cut with holes at all crossings but the cut lap's neighbours and at every crossing,
  and a three-chapter recording with its middle chapter missing and with line holes; a 63-minute
  recording with 5 % rejected; and a Library holding two rows written by v0.25.0's studio/. On
  the demo the Laps tab, Stats (IDEAL LAP 0:44.650, 0.30 s on the table), DATA TRUST, the
  debrief, the report, the lap card, the lap strip and laps.csv read 14 laps, best lap 13 at
  0:44.950, no ⚠ and a clean verdict, and no surface names lap 11 for corner 2, 44.63 or 0.32 s. With
  scattered rejections every unflagged lap stayed within 4.0 ms of the clean trace and the best
  lap was the clean one (0.25.0 flagged 8 of 14 laps at 2 % and read low); one fix before each of
  three crossings flags nothing (0.25.0: six laps), two fixes flag the two laps sharing each
  crossing; the cut lap reads off the line (28.6 m) and the best is lap 12, a lap with no ⚠, as
  in 0.25.0. The v1 Library rows were listed with their ideal muted and the hover, the best-lap
  chart and the track's best unaffected; re-opening each stamped it 2 and unmuted it, and the
  row 0.25.0 had marked degraded (8 of 14 laps ⚠ at 2 % rejected) became a trusted row with lap
  13 best. On the 63-minute recording the load took 2.0 s with 5 % rejected against 1.6 s in
  0.25.0 (1.3 s and 1.2 s clean), 3 laps wore the ⚠ (0.25.0: 65 of 83, low), and moving the
  start line or adding a sector line ran the smoother no times.
- **The media.** `hero.png` (and `og.png`, cut from it), `map.png`, `debrief.png`, `quickest.png`
  and `report.png` were re-captured on the pinned synthetic demo at 0.26.0 and changed, each
  because the demo's two missing fixes are now smoothed in time: the hero's and the quickest
  shot's Δideal reads +0.04 s where it read +0.05 (the README and landing-page alt text of the
  hero now say +0.04), the quickest shot's corner-2 row names lap 7 with 11 more laps level, the
  debrief's and the report's corner-2 row read "2 of 14 laps" and "middle half spans 0.03 s" where
  they read 3 and 0.04, the report's lap card reads +0.30 s to the ideal lap, and the map's line
  moved by under a pixel in 729 pixels. The same shots rendered from v0.25.0's studio/ in the
  same environment reproduce the committed `debrief.png`, `lapstrip.png`, `map.png` and
  `quickest.png` exactly. `accuracy.png`, `moments.png` and `lapstrip.png` are byte-identical
  (one `lapstrip.png` capture came out different in its "median" and "best" labels, 442 pixels,
  the known flicker, and a second reproduced the committed file). The waivers of
  `data-trust.png`, `ideal-lap.png`, `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are
  renewed: a demo render of the data-trust shot differs in 89 pixels of its g-g scatter with
  every row's words unchanged; the ideal-lap shot's demo render moved (0:44.631 → 0:44.650,
  0.32 → 0.30 s, 9 → 8 donor laps), and whether the published recording has a missing fix was
  not measured here; no export code changed.
- **What could not run for it.** No real footage was used, so every `footage.*` check reports
  Skipped by name. The full gate, the soak and the smoke run on the release branch, not in the
  QA pass. The Library's "Use as reference" was not driven; it reads no ideal-lap stamp.
- **Found in this release's QA, not fixed** (nothing that would hold the release).
  - **A selected Library row's muted ideal lap is drawn in the selection's full colour**, so the
    muting shows only on rows that are not selected; the hover still says it.
  - **Code comments still define `DROPOUT_AT_LINE` as "a fix missing beside its line"**
    (`session.py`, `marks.py`); it now takes two in a row.
  - **#715's media check named `debrief.png`, `quickest.png`, `hero.png` and `map.png`
    identical**; rendered here they changed, and they are re-captured above.
- **Open for the owner** (new, most important first).
  - **Sector splits still move by up to ~45 ms near a hole** at native noise in the matrix, with
    lap times within 4 ms: sector boundaries are mapped by distance, and a missing fix changes the
    cumulative distance.
  - **A hole longer than 0.40 s re-anchors the GPS9 clock** (`load.GPS9_MAX_DT_S`), so absolute
    times after it shift by tens of ms: the lap holding it is flagged, but video sync moves.
  - **One hole flags the lap finishing at a line but not the one starting there** when it ends
    0.3 s before the line.
  - **Holes move the auto-fitted start line by metres** (about 0.28 s up the straight on the
    synthetic cut-lap recording).
  - **The median fix period is one figure for the whole trace**, so a recording whose rate
    changes mid-way would misread its steps (section 28's item, carried).
  - **The smoother costs about 0.5 s** on a 70-minute recording with 5 % of fixes rejected.
- **Open for the owner** (carried from section 28, re-checked against 0.26.0's change): the bests
  fallback still crowns a dropout-only ⚠ lap when every lap is flagged, and the lap card shows
  it as "BEST LAP" with no ⚠; a dropout shorter than 0.35 s in the last 0.7 s before the line
  counts as a whole trace for the off-line rule; a ⚠ lap whose only hole sits just past its
  finish has its mark under the neighbouring lap; the ghost's reason says a lap with fixes
  missing at its line has a "place on track [that] is a guess"; the report's "Not counted" cell
  wraps "gps-dropout" at its hyphen; DATA TRUST's Timing row can read "0% of fixes inside your
  laps rejected — GPS fixes missing at the line on 13 of 14 laps"; the lap card and the lap
  strip stamp "estimated timing (est)" on a true-clock session that is low by the lap vote;
  laps.csv has no key row for `flag`; a ⚠ lap's corner-pass clip is untagged; an off-line lap
  has no Quality code; the burned stamp says "% of fixes rejected" for the share inside the
  laps; the export box's header says "couldn't finish" when the render never started; a cut lap
  is still the best when fewer than four laps have a whole trace; the lap-vote sentence says
  "at the line" when the laps' dropouts are gaps inside them; with no best lap the overlay
  export warns of provisional timing on a verified line; the lap strip's legend truncates when
  quicker ⚠ laps of two kinds share a line; and section 28's carried lists stand as written
  there.

## 30. Recorded at the 0.27.0 release, 2026-11-27

- **v0.27.0 is a minor release**: 9 pull requests, #717 to #725, Wave 24 of the campaign section 8
  records, its theme "your Library catches up by itself — when you ask".
  - #720: File ▸ Library… shows "N ideal laps were measured by an older Pacer." with **Update**
    when rows carry an older `IDEAL_VERSION` and all their files are on this Mac. Update
    re-measures those rows one at a time on a worker (`workers.RemeasureWorker`): chapter order,
    `Session.load`, the sidecar's saved lines READ only, `Session.library_entry`, then
    `library.remeasure_and_save` (one lock round the read and the write, and only while the row is
    still there and the measurement keys to it). The finish line counts what moved beyond the
    ideal and names it in its hover. `workers.start_load` is now the one door for the GUI-thread
    `gc.collect()` before a loading worker starts.
  - #717: the lap vote's sentence says where the laps lost their fixes — at the line, inside the
    laps, or both ("GPS dropped out in 14 of 14 laps, at the line on 9 of them") — and the PB
    refusal, the banner, DATA TRUST, the report and the burned stamp read the same words; DATA
    TRUST's Timing row joins a clean share and the vote's cost with ", but"; a dropout mark's
    hover is no longer doubled; the ghost's refusal names the lap's own kind of hole.
  - #718: a stale ideal stays muted on the selected Library row (`C.text_dim`, 4.80:1 on the
    selection); the lap card marks a ⚠ best lap (every lap flagged) with "⚠ GPS dropout — not
    counted in your bests".
  - #721: the report's flag cell does not wrap (`td.flag { white-space: nowrap; }`); the lap
    strip's legend gives each kind of quicker ⚠ lap its own line.
  - #719: refusal section 42, re-timing or flagging sector splits near a missing fix, with its
    probe. #722: a docs truth pass. #723: an unbuilt agent worktree gets clear reds (the bindings
    fallback, `tests/_samples.py`, a narrowed ghost-test mock, the env's `bin/` on PATH, a named
    free-space skip in a sandbox). #724: the modal watchdog in every Qt test. #725:
    `studio/dev/tests_for.py`.
- **The coordinator's rulings for this wave**, each open to the owner's overrule and each
  reversible:
  - **The Library Update is button-only and one recording at a time**, never automatic, and it
    mirrors a manual re-open: it writes each row exactly as re-opening that recording would, and
    nothing else. It raises no PB card; it names every change instead (a best lap, a lap count or
    a trust tag that moved, a row it could not measure, and "your <track> best is now …" when a
    track's best moved).
  - **Sector splits near a missing fix: refused** (refusal section 42). A split is already timed at
    the geometric crossing, exactly as a lap's own crossings are; what still moves it near a hole
    is the smoothed trace at the line, a symmetric noise redraw (148 splits quicker by over 10 ms
    and 134 slower in the matrix), and the de-drift frame, which is common to the column. The
    worst added σ keeps the published 0.15 s step.
  - **A silent skip-that-passes for a missing sample clip is a false green**: a test that needs
    the gpmf-parser samples fails fast, by name, when the submodule is empty (CI always has it).
  - **A dialog left open in a test fails in 20 s by name** (`PACER_MODAL_WATCHDOG_S`), instead of
    sitting to the CTest timeout.
  - **The docs say what is true at the release**: footage, VideoToolbox in CI, timings and
    `PYTHONPATH` (#722).
- **Process.** The release QA drove the merged tree offscreen and jailed, at 1440×900 with the
  theme applied, on synthetic recordings only. The Library Update ran end to end on a jailed
  Library whose rows v0.25.0's own `studio/` wrote (its `Session.library_entry`, `keyed` and
  `upsert`, `IDEAL_VERSION` 1), covering a clean recording, one with 2 % of fixes rejected in the
  file, a three-chapter one, a chapter subset, a recording missing its first chapter, a recording
  whose files left the Mac, a path now holding another outing, a wet session record, a 1 s gap in
  laps that 0.25.0 marked estimated, and the track renamed in every store; a second jail was
  re-opened row by row through the window's own load as the control. The line read "8 ideal laps
  were measured by an older Pacer." (the gone recording in its hover) and finished "7 updated · 1
  best lap changed · 1 could not be measured" (the other outing, "the file at that path is now a
  different recording"); every updated row equalled the control's byte for byte, the renamed track
  and the wet record held, the open session, its view, Open Recent and the PB card were untouched,
  and the only file written was `library.json`. Cancel mid-run, closing the Library, closing the
  window, Update then Cancel then Update again, Open and Use as reference during a run each left
  the store valid, every row either untouched or equal to the full run's, and nothing written
  beside the recordings. With the gone row forgotten the finish line added "your Synth Ring GP
  best is now 0:44.903". On rejected-fix cases (a 1 s gap inside every lap, two fixes before every
  crossing, both, 10 % scattered, a 0.33 s hole inside every lap) the banner, the chip, DATA TRUST,
  the report, the burned stamp and the Library row agreed, and nothing said "missing at the line"
  of a gap inside the laps. The demo read 14 laps, best lap 13 at 0:44.950, IDEAL LAP 0:44.650,
  0.30 s, nothing flagged, and a clean verdict. The 14 modal-driving test files ran twice each, two
  at a time, as registrations: none tripped the watchdog.
- **The media.** Every demo still was re-captured at 0.27.0 and came out byte-identical to the
  committed file: `hero.png`, `og.png`, `map.png`, `debrief.png`, `quickest.png`, `report.png`,
  `moments.png`, `accuracy.png` and `lapstrip.png` (one `lapstrip.png` capture differed in its
  "median" and "best" labels, 442 pixels, the known flicker, and a second reproduced the committed
  file). The waivers of `data-trust.png`, `ideal-lap.png`, `overlay.jpg`, `best-lap.jpg` and
  `best-lap.mp4` are renewed: demo renders of the data-trust and ideal-lap shots, and a best-lap
  Classic export of the demo, are byte-identical from v0.26.0's `studio/` and from 0.27.0.
- **What could not run for it.** No real footage was used, so every `footage.*` check reports
  Skipped by name. The full gate, the soak and the smoke run on the release branch, not in the
  QA pass.
- **Found in this release's QA, not fixed** (nothing that would hold the release).
  - **A row whose path now holds a different recording is offered again on every Library open**:
    Update fails it ("the file at that path is now a different recording") and leaves it as it was,
    so the line reads "1 ideal lap was measured by an older Pacer." for good.
  - **The Library's muted ideal cells un-mute all at once when the run ends**, not row by row, and
    the table shows the stored best laps until then.
  - **With every lap flagged, the export ghost's reason for each lap is "cannot be raced against
    your best lap"**, not the lap's own kind of hole.
  - **`tests/test_export_video.py`, `tests/test_session_log.py` and
    `studio/dev/golden_session_dump.py` still advise `PYTHONPATH=bindings/pacer`**, which
    tests/README now names as the cause of `No module named 'pacer._pacer'` in an unbuilt worktree.
- **Resolved from section 29**: a selected row's muted ideal lap; the code comments defining
  `DROPOUT_AT_LINE` as one missing fix; the lap card's "BEST LAP" with no ⚠ for a fallback best;
  the ghost's "place on track is a guess" for a hole at the line; the report's "Not counted" cell
  wrapping; DATA TRUST's Timing row "0% … rejected — GPS fixes missing at the line"; the lap-vote
  sentence saying "at the line" of gaps inside the laps; the doubled dropout-mark hover; the lap
  strip's legend truncating with quicker ⚠ laps of two kinds; and sector splits moving near a
  hole, refused with its reasons (section 42).
- **Open for the owner** (new, most important first).
  - **On an 18 Hz camera a 5-fix hole (0.33 s) sits under the 0.35 s dropout threshold**, so it
    wears neither the in-lap nor the line ⚠, and moved a hairpin split by up to 56 ms in the probe;
    at 10 Hz the same hole is flagged.
  - **The lap strip's headline has no ⚠ for an all-⚠ best** ("★ lap 10" over a fallback lap);
    File ▸ Export does not offer the strip for such a session (fewer than two clean laps), so it is
    drawn only when called directly.
  - **The reference-recording load still starts its worker without `workers.start_load`'s
    GUI-thread GC guard** (`StudioWindow._start_reference_worker`).
  - **Update waits while the main window's own load runs**, so a run started beside a long load
    sits on "Updating 1 of N…" until that load ends.
  - **`tests/_stale.py` still says the working recordings are all here**; one of its four folders
    was on the dev Mac on 2026-11-27.
  - **`tests_for.py`'s timings come from a stale CTest record** (the owner's checkout's was last
    written 2026-11-16), printed as a floor.
- **Open for the owner** (carried from section 29, re-checked against 0.27.0's changes): a hole
  longer than 0.40 s re-anchors the GPS9 clock; one hole flags the lap finishing at a line but not
  the one starting there when it ends 0.3 s before the line; holes move the auto-fitted start line
  by metres; the median fix period is one figure for the whole trace; the smoother costs about
  0.5 s on a 70-minute recording with 5 % rejected; the bests fallback still crowns a dropout-only
  ⚠ lap when every lap is flagged (the lap card now says so); a dropout shorter than 0.35 s in the
  last 0.7 s before the line counts as a whole trace for the off-line rule; a ⚠ lap whose only
  hole sits just past its finish has its mark under the neighbouring lap; the lap card and the lap
  strip stamp "estimated timing (est)" on a true-clock session that is low by the lap vote;
  laps.csv has no key row for `flag`; a ⚠ lap's corner-pass clip is untagged; an off-line lap has
  no Quality code; the burned stamp says "% of fixes rejected" for the share inside the laps; the
  export box's header says "couldn't finish" when the render never started; a cut lap is still the
  best when fewer than four laps have a whole trace; with no best lap the overlay export warns of
  provisional timing on a verified line; and section 28's carried lists stand as written there.
