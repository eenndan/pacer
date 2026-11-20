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

- **v0.18.0 is a minor release**: 8 pull requests from #629 to #636, Wave 13 of the campaign
  section 8 records, its theme "the driver's own sectors join the pass family, and a session that
  is not the demo reads true".
  - #629: six fixes from the 0.17.0 QA: a pass's status line goes when the pass is left, a walk
    from Quickest passes ends on "C7 is the last corner", Esc leaves a Coaching Compare, a slow lap
    narrower than its ▼ shows as a tick on the lap ruler, the map key folds while a pass is zoomed,
    and a tie's strip hover says what is true of each lap.
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
  - **A disabled button's key says the button's own reason** (C, G), rather than a second wording
    of it.
- **Refused this wave, with the numbers** (§36, §37, #630). Early or late apex: the rule is safe
  (0 to 1 of 48 null cells fire) but blind to what a driver would call early or late: a 2 m shift
  never reliably fires, and on the demo it fires once, at C7, a constant-radius arc with no
  minimum to place. The line offset at the apex: 1 m reads as 0.58 m after the de-drift, and the
  quick-against-typical split manufactures "tighter" on recordings with no line habit planted (3
  of 7 demo corners).
- **Process.** Discovery 7 ("the session that is not the demo", twelve synthetic sessions through
  the real loader) produced #634 to #636. The QA pass drove the combined tree in the real window,
  offscreen and jailed, at 1280×800 and 1440×900: the demo with three sector lines (every
  right-click menu on corner and sector rows after #633's deferred deletes, each menu item acting on
  the row chosen, the "not for sectors yet" refusals, the sector walk to both ends, Esc and a lap
  pick taking the pass's line down, C on a sector pass, the corner walk's end from Quickest
  passes, the widget census across menus, compares and ⌘K), synthetic sessions of zero, one, two
  and ten level laps (the no-laps body beside the Stats empty states, the keys' refusals), and the
  one real recording on this machine, read-only. A demo render of the ideal-lap and data-trust
  shots from v0.17.0's `studio/` and from 0.18.0 is byte-identical.
- **The media.** Re-captured on the synthetic demo at 0.18.0: `quickest.png` (the map key folds
  while the pass is zoomed, #629) and `report.png` (347 anti-aliased pixels along the drawn line
  differ from 0.17.0's; the same words and figures). `hero.png`, `og.png`, `map.png`,
  `accuracy.png`, `debrief.png`, `moments.png` and `lapstrip.png` (its own run) are byte-identical
  to the files already committed, so the social card did not change. The waivers of
  `data-trust.png` and `ideal-lap.png` are renewed: #634 changed Stats page code, in branches the
  captured session does not reach (its IDEAL LAP lists nine segments), and the demo A/B above is
  byte-identical. Those of `overlay.jpg`, `best-lap.jpg` and `best-lap.mp4` are renewed: 0.18.0
  changed no export-video code.
- **What could not run for it.** The real-footage checks whose recordings are not on this machine
  report Skipped by name. Real macOS fullscreen Spaces, a second monitor and hover could not be
  checked offscreen. The full gate, the soak and the smoke run on the release branch, not in the
  QA pass.
- **Resolved from section 19**: drawn sector lines in the pass family (#631); early or late apex
  and a line offset (refused, #630); the compare re-entry widgets (a counting artefact, #632); the
  strip's hover on a tie, the ruler's ▼ on narrow laps, the status line after leaving a pass, the
  end of a walk from Quickest passes and Esc on a Coaching Compare (#629).
- **Open for the owner** (new).
  - A sector pass's export and its compare with the typical pass say "not for sectors yet".
  - F (fill the screen) while comparing does nothing and says nothing.
  - On a session with no complete lap the map's empty-state card sits over the middle of the
    trace.
  - Section 13's question (should Coaching rank corners that lose under 0.07 s, and name a habit
    there?) has new synthetic evidence: on a ten-lap session of one driver with GPS noise only,
    every lap level, the debrief still ranks three corners at +0.01 to +0.02 s and names a braking
    cause at the first ("brake later / shorter").
  - With sector rows shown and the scrollbar hidden, the last sector row is cut at 1280×800
    docked (on the demo with three lines, 11 px of S4), and a session whose Quickest view carries
    its "laps not counted" line loses whole rows; there the strip is hidden anyway, so the hidden
    scrollbar buys nothing.
  - The overlay export dialog, "Export this pass…" included, is not freed when closed: each open
    leaves 9 top-level and 101 widgets behind on the demo (pre-existing; not one of #633's doors).
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
