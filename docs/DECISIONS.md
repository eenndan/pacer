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
- FIRST-OPEN-LOOP-n, TRUTH-n, COACHING-n, GATES-n, PROCESS-n, LONGEVITY-n: the work packages of
  the 2026-09-28 fix plan. A package that has merged is the pull request whose title starts with
  its id.

## 1. Rulings

| Id | Ruling | Default | Status | What follows |
|---|---|---|---|---|
| RUL-1 | North-star: adopt "a craft showcase whose proof is that its author races with it", or keep July's | keep July's "portfolio / craft showcase" | default applied 2026-09-28 on the owner's instruction not to be asked | The README and landing page keep today's wording. |
| RUL-2 | A circuit results sheet as an optional input (one DATA TRUST line, plus his kart number) | no | default applied 2026-09-28 on the owner's instruction not to be asked | No circuit-timing row is built. |
| RUL-3 | Premises, not choices: a sprint is arrive-and-drive (a different fleet kart each time), and the Sandown 3h recording's camera stayed in one kart across its driver changes | none — premises | unconfirmed 2026-09-28; nothing further is built on them | The kart-number record and the pre-promoted focus rest on the arrive-and-drive premise, still unconfirmed (section 4). |
| RUL-4 | A per-stint driver label on an endurance recording | no | default applied 2026-09-28 on the owner's instruction not to be asked | No stint labels. |
| RUL-5 | The race-day agreement: a 10-minute talk, plus two counts he pastes from the app's log | the store listing only: no talk, no counts | default applied 2026-09-28 on the owner's instruction not to be asked | The race-day read reports S2 and S4 as not observable, and holds no talk (S6); section 3. |
| RUL-6 | The focus loop: keep it, demote it, or turn it off | frozen as is until the freeze end; then keep it | default applied 2026-09-28 on the owner's instruction not to be asked: frozen as is; not re-asked, its freeze-end default applies then | At the freeze end the loop's held fixes build. |
| RUL-7 | Lead the debrief with the lap ("Export best lap" and "Compare with your PB" on any day) | frozen as is until the freeze end; then no | default applied 2026-09-28 on the owner's instruction not to be asked: frozen as is; not re-asked, its freeze-end default applies then | The lap-first debrief buttons are not built. |
| RUL-8 | The theme sentence names the one corner that decides it | frozen as is until the freeze end; then build it | default applied 2026-09-28 on the owner's instruction not to be asked: frozen as is; not re-asked, its freeze-end default applies then | The sentence changes after the freeze. |
| RUL-9 | Footage consent: publish a telemetry slice, a 6-8 minute sample, or neither | neither; the demo stays synthetic | default applied 2026-09-28 on the owner's instruction not to be asked | No recording or telemetry is published beyond the approved stills and the 20 s clip (§4 item 6); the next demo is synthetic too. |
| RUL-10 | Track the agents' `.claude/settings.json` in git, or keep it untracked | untracked | default applied 2026-09-28 on the owner's instruction not to be asked | The settings stay out of git; [AGENT-GUARDRAILS.md](AGENT-GUARDRAILS.md) is their deny rules' reference copy. |
| RUL-11 | Packaging: keep the macOS `.app` (then CI would launch it on every tag) or drop it (source only) | drop the step and every claim of a working `.app` | default applied 2026-09-28 on the owner's instruction not to be asked: drop | Done 2026-09-30 (LONGEVITY-2, #497): the tag job no longer builds an `.app`, and no page claims one works. `packaging/` stays as an ungated local recipe that no CI job builds or launches ([PACKAGING.md](PACKAGING.md)). |
| RUL-12 | The showcase release: 0.6 or 1.0 | 0.6 | default applied 2026-09-28 on the owner's instruction not to be asked | The next release is 0.6.0. |

RUL-6 to RUL-8 are not re-asked (his instruction of 2026-09-28). At the freeze end the race-day
read goes to him as information, and each freeze-end default applies unless he replies (section 3).

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
