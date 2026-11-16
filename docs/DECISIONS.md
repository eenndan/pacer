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
    widens to 0.15 s. There is no follow-cam and no label on each dot.
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
  repaint; measured, it is not: at the 917×230 map of a 1440×900 window, a whole race at 30 Hz
  took a median 22.1 ms a tick plain and 23.1 ms with Follow on a 40-lap synthetic (18.9 and 20.0
  ms on the demo), on a Mac loaded past 20 (the discovery round's 11.8 ms against 12.3 ms ran at
  load 5). It ships as an option, off by default and
  remembered once chosen (the race card's crosshair button, View ▸ Follow the race, ⇧R): the view
  centres on the median dot at the narrowest of 30 to 150 m that holds the whole field, eased in
  race time inside a dead-zone box, with a scale bar; with it off a race never touches the view,
  and leaving puts the view back exactly. Open for the owner: should it be on by default?

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
