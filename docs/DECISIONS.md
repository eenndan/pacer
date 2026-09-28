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

## 1. Rulings

| Id | Ruling | Default | Status | What follows |
|---|---|---|---|---|
| RUL-1 | North-star: adopt "a craft showcase whose proof is that its author races with it", or keep July's | keep July's "portfolio / craft showcase" | default applied 2026-09-28 on the owner's instruction not to be asked | The README and landing page keep today's wording. |
| RUL-2 | A circuit results sheet as an optional input (one DATA TRUST line, plus his kart number) | no | default applied 2026-09-28 on the owner's instruction not to be asked | No circuit-timing row is built. |
| RUL-3 | Facts: are his sprints arrive-and-drive (a different fleet kart each time)? Did the Sandown 3h camera stay in one kart across driver changes? | none — facts | unanswered 2026-09-28 — nothing is built on them | The kart-number record and the pre-promoted focus rest on the arrive-and-drive premise, still unconfirmed (section 4). |
| RUL-4 | A per-stint driver label on an endurance recording | no | default applied 2026-09-28 on the owner's instruction not to be asked | No stint labels. |
| RUL-5 | The race-day agreement: a 10-minute talk, plus two counts he pastes from the app's log | the store listing only: no talk, no counts | default applied 2026-09-28 on the owner's instruction not to be asked | The race-day read reports S2, S4 and S6 as not observable (section 3). |
| RUL-6 | The focus loop: keep it, demote it, or turn it off | frozen as is until the freeze end; then keep it | default applied 2026-09-28 on the owner's instruction not to be asked: frozen as is; re-asked at the freeze end | At the freeze end the loop's held fixes build. |
| RUL-7 | Lead the debrief with the lap ("Export best lap" and "Compare with your PB" on any day) | frozen as is until the freeze end; then no | default applied 2026-09-28 on the owner's instruction not to be asked: frozen as is; re-asked at the freeze end | The lap-first debrief buttons are not built. |
| RUL-8 | The theme sentence names the one corner that decides it | frozen as is until the freeze end; then build it | default applied 2026-09-28 on the owner's instruction not to be asked: frozen as is; re-asked at the freeze end | The sentence changes after the freeze. |
| RUL-9 | Footage consent: publish a telemetry slice, a 6-8 minute sample, or neither | neither; the demo stays synthetic | default applied 2026-09-28 on the owner's instruction not to be asked | Nothing real is published; the next demo is synthetic too. |
| RUL-10 | Track the agents' `.claude/settings.json` in git, or keep it untracked | untracked | default applied 2026-09-28 on the owner's instruction not to be asked | The settings stay out of git. |
| RUL-11 | Packaging: keep the macOS `.app` (CI launches it on every tag) or drop it (source only) | drop the step and every claim of a working `.app` | default applied 2026-09-28 on the owner's instruction not to be asked | The tag job stops building an `.app`, and nothing claims one works. |
| RUL-12 | The showcase release: 0.6 or 1.0 | 0.6 | default applied 2026-09-28 on the owner's instruction not to be asked | The next release is 0.6.0. |

"Re-asked at the freeze end": he is then told what the race day showed, and the Default column's
freeze-end choice applies unless he answers.

## 2. Owner acts

Only he can do these. They have no default and stay open until he does them; the work goes ahead
without them. Status as checked read-only on 2026-09-28, with its evidence: `gh` for what GitHub
shows, "his word" for what only he can report.

| Act | Status (evidence) | Note |
|---|---|---|
| A second copy of every original recording, wherever it lives, plus the transponder CSV | open (his word: none yet) | Until then each recording exists once. |
| Save the Sandown Club Speed heat pages of 19 Jul, 30 Aug and 19 Sep, whole days | open (no page saved yet) | Three accuracy rows at a second circuit wait on them. |
| Upload `docs/media/og.png` as the social preview, and pin the repository | open (`gh`: no custom preview, nothing pinned) | The upload is a copy: re-upload whenever `og.png` changes. |
| A profile name and bio, or a stated choice to stay anonymous | open (`gh`: no name, no bio) | |
| A private backup target for the agents' notes and memory | open (his word: none yet) | Never named here. |
| Turn off the unused desktop-app connectors for this project; remove gitnexus | open (his word: none yet) | |
| The clip's upload URL, when an agent asks | open (not asked yet) | |
| The About line's headline number, when an agent asks | open (not asked yet) | |
| The board review's real-screen pass (item 15-V1) | open (backlog) | Nothing waits on it. |

Two permissions had no default either. Publishing the synthetic `demo-data-v2` beside v1 (a new
release asset, never replacing it) is taken as granted by his "implement the plan … run until
done" (2026-09-28), as `demo-data-v1` and the releases since v0.3.0 were published under his
2026-09-24 mandate. Deleting the stale remote branch `b4/media-reshoot` is **not** done: it holds
two unmerged commits, and nothing needs it gone.

## 3. Standing decisions

### The first-open and focus freeze

**Window.** From 2026-09-28 until the first of: his next new recording opened in the app, or
Monday 2026-11-09. The first-open loop (the debrief landing, the pre-promoted focus list, the PB
line and compare) has not yet met one of his race days; it stays as it is until one has used it.

**Frozen**, function by function. A change inside one of these waits for the freeze end:

- `studio/library_controller.py`: `update_library`, `_pb_decision`, `_land_deferred_verdict`,
  `_land_named_verdict`, `refresh_library_entry`, `degraded_notice`, `debrief_pb_line`,
  `offers_pb_compare`, `show_pb_moment`, `pre_promote_focus`, the `focus_*` methods,
  `mark_sessions_dry`, `_records_changed`, and the `opened_new` and `previous_pb` state.
- `studio/focus.py`, all of it.
- `studio/app.py`: `_land_on_debrief`, the first-open clauses of `_session_notice`, the PB item of
  `_sync_coaching_menu`, `_compare_with_previous_pb`.
- `studio/central_view.py`: `show_debrief`, `_end_debrief`.
- `studio/coaching_panel.py`: `DebriefBlock`, `OpportunitiesPanel.set_debrief`.
- `studio/library.py`: `pb_moment`, `previous_pb`, `pb_standing_for`, `pb_moment_for`,
  `pb_moment_text`, `pb_standing_text`.
- `studio/share_card.py`: `pb_mark`. `studio/session_record.py`: `is_empty`, `comparable`,
  `put_if_blank_and_save`.

**Allowed inside**, each touching only what it names (the id is its pull request's title):

1. Only a start line that moved confirms the timing; a sector edit no longer does
   (FIRST-OPEN-LOOP-1). No frozen function.
2. Degraded GPS at an unnamed circuit promises nothing it cannot bring (FIRST-OPEN-LOOP-2):
   `update_library`, `degraded_notice`.
3. Four INFO log lines for the loop's gestures (FIRST-OPEN-LOOP-3): one log call each in
   `_land_on_debrief`, the `focus_*` methods, `mark_sessions_dry` and `_compare_with_previous_pb`;
   no copy or behaviour changes.
4. A PB inside timing precision reads "level with your best" (FIRST-OPEN-LOOP-5): `pb_moment_text`,
   `pb_standing_text`, `pb_mark`; text only.
5. The coaching advice read from the laps' habit, with the spread its line row gates on, the
   phase bar from the median over laps, and the "Start with …" tie floor (COACHING-1, -2, -3):
   `coaching.py` and `session.py`. No frozen function.
6. Annotation-only typing of the stores (GATES-4): `library.py`, `focus.py`, `session_record.py`,
   with the same behaviour.

**The check.** Before a merge, each changed line in these files is mapped to its enclosing
function, on both sides of the diff. A change inside a frozen function by anything not listed
above waits for the freeze end.

**The race-day read**, registered before the race. Each signal is reported on its own line, and no
single one counts as success. His stores are read for names, sizes and dates only. A focus store
alone proves nothing: a first open writes one with no click.

- S1: the session-record store (`session_records.json`) exists.
- S2: the focus list edited by hand, not left as the app's default: not observable (RUL-5, no
  counts).
- S3: the marks store (`marks.json`) exists.
- S4: where an export was started from: not observable (RUL-5, no counts).
- S5: a focus verdict. It is reachable only with a second session at the same track and records
  on both sides; otherwise it is reported "not reachable", never "not used".
- S6: a 10-minute talk: not held (RUL-5).

**What ends it and what follows.** At the freeze end the read above goes to him, and RUL-6 to
RUL-8's freeze-end defaults apply unless he answers: the loop stays, no lap-first buttons, and the
theme sentence names its deciding corner. The held work on the first-open verdict and the focus
loop then builds, one change at a time. The lap-first buttons are held by RUL-7, not by the freeze.

### The board review's restraint pass (R11)

Closed 2026-09-28: the tile target is met. No further restraint pass; the 13-section ceiling on the
demo still binds.

## 4. Decided for the owner, 2026-09-24 to 09-28

Under his "act fully autonomously … act as CEO, CTO, COO, CPO" (2026-09-24), and "don't ask me
questions, run until done" (2026-09-28). Each is open to his overrule.

**Proxy decisions** (the board ledger's ten, in its order, plus one):

1. The original recordings write-locked (2026-09-24).
2. The owner's checkout moved to `main` (2026-09-24).
3. A branch ruleset on `main`: a pull request and green CI (2026-09-24).
4. Merged branches deleted on merge, and the old ones pruned (2026-09-24).
5. The repository's website field set to the landing page (2026-09-24).
6. The Sandown stills approved for the public pages (#385; re-shot in #441).
7. Footage consent: nothing real published; the demo and the CI fixture are synthetic (#371). Now
   RUL-9.
8. v0.3.0 approved and published (#390).
9. The engineering notes' candour kept at summary level (#388).
10. Exports stay beside the footage (2026-09-24).
11. Extra: the accuracy claim re-proven at MK from its public timing; Sandown left for him (#382).

**Two CPO calls**, both in #399: the "brake N m later" hint retired from Coaching, after the
measurement that refused it (#395); and the braking-direction line shown only where it survives a
per-recording Holm correction.

**Built on his mandate, on an unconfirmed premise**: the kart-number session record (#386) and the
focus list pre-promoted at a first open (#391) assume arrive-and-drive sprints, which RUL-3 asks.

**Two corrections**: the north-star was logged as "adopted in docs as a hierarchy" on 2026-09-24
but never committed (RUL-1 now records it); and no GitHub social preview was ever uploaded, though
#441's description assumed one (checked 2026-09-28).
