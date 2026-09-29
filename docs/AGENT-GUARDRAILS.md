# Agent guardrails — a reference copy

The coding agents that work on this repository run under two guardrails kept outside git: a deny
list with a sandbox block, and a hook that refuses certain shell commands. Both live in untracked
configuration on the owner's machine, so a fresh clone comes up without them and a change to either
leaves no trace. This page is their tracked reference copy, and the recipe that restores them.

**The live rules win.** Nothing reads this page as configuration. Where it and the live copy
differ, the live copy is what runs and this page is out of date: the orchestrator diffs the two at
every wave close, and before a change to this page merges. The data rules the guardrails back up
(recordings read-only, app data jailed) are the playbook's, and are not restated here:
[AGENT-PLAYBOOK.md §1](AGENT-PLAYBOOK.md#1-hard-rules).

## 1. The deny list and the sandbox block

Verbatim from the live copy, `.claude/settings.local.json` in the owner's checkout. Only these two
keys are copied: the file's allow list and extra directories are grants for one machine, not
guardrails.

```json
{
  "permissions": {
    "deny": [
      "Bash(rm)",
      "Bash(rm *)",
      "Bash(rm -rf *)",
      "Bash(rmdir *)",
      "Bash(sudo *)",
      "Bash(git push --force*)",
      "Bash(git push -f*)"
    ]
  },
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": true,
    "allowUnsandboxedCommands": true,
    "filesystem": {
      "allowWrite": [
        "/tmp"
      ]
    }
  }
}
```

- **No deletion from the shell** (`rm`, `rmdir`). A deletion cannot be undone, and a path computed
  wrong takes the owner's files with it: a recording was once lost to one argument read the wrong
  way ([ENGINEERING.md §8](ENGINEERING.md#8-the-argument-that-was-an-output)). Temporary files
  clean themselves (`TemporaryDirectory()`); an untracked file in a worktree goes with
  `git clean -f <path>`.
- **No `sudo`.** Nothing here needs more than the user's own rights.
- **No force-push.** `main` is protected, and a branch takes a moved `main` by merge, never by
  rewrite ([AGENT-PLAYBOOK.md §4](AGENT-PLAYBOOK.md#4-delivering)).
- **The sandbox** confines every shell command: it writes only inside the working directory and
  `/tmp`, and runs without a prompt while it stays there. A command that must leave it
  (`pixi install`, `gh`, a probe that decodes real footage in hardware) asks, and the permission
  prompt decides.

## 2. The command hook

A `PreToolUse` hook reads each shell command before it runs and refuses it on these patterns, as
recorded by the agents that hit them; the hook's own source is the authority.

| Pattern | Why |
|---|---|
| the `rm` command | §1's reason: a second net beside the deny list. |
| a heredoc (`<<`) | File contents go through the editor's Write tool, where each write is a visible call with its path, never text buried in a shell command. |
| `.pyi` anywhere in the command | The generated stubs are regenerated with `pixi run gen-bindings`, never edited ([AGENTS.md](../AGENTS.md), rule 3). |
| `bindings/pacer/pacer` anywhere | The generated package's directory, for the same reason. |
| `.so` anywhere | The compiled extension is build output: `pixi run build` redeploys it, git ignores it, and nobody copies, patches or commits it by hand. |
| `hooks` anywhere | A guardrail an agent can edit is not a guardrail: no command touches hook configuration. |

The patterns match a command's text, so a harmless command that names one is refused as well.
Stage regenerated bindings with `git add -A` and check `git status`; write any file whose text
holds a pattern with the Write tool.

## 3. Where the live copies are

- **The deny list and sandbox block:** `.claude/settings.local.json` in the owner's checkout.
  `.gitignore` ignores `.claude/` whole. Tracking `.claude/settings.json` instead would make a live
  permission file part of every merge, so it is the owner's option, not an agent's change: RUL-10
  in [DECISIONS.md](DECISIONS.md#1-rulings) keeps it untracked (default applied 2026-09-28).
- **The hook:** in the owner's Claude Code host configuration, outside this repository and outside
  both the project's and the user-level settings files. Its exact location is not recorded here.
- Agents may read the settings files, never write them: the sandbox denies writes to
  `.claude/settings*.json` and to hook directories, and an agent never goes looking for the hook.

## 4. Restore on a fresh machine

The board review's restore test (R13: a fresh clone plus one private pull brings back the agents'
working setup in under 30 minutes) times these steps. R13 is recommendation 13 of the board
review of 2026-09-23; like every id [DECISIONS.md](DECISIONS.md) glosses at its top, it names an
item in the maintainers' private notes, which are not in this repository.

1. `git clone https://github.com/eenndan/pacer.git` into a new directory, never over a live
   checkout.
2. `git submodule update --init --recursive`.
3. `pixi install`, then `pixi run build`. Both need the network, so they run outside the sandbox.
4. Pull the owner's private backup (choice recorded in [DECISIONS.md](DECISIONS.md#2-owner-acts))
   into a scratch directory, never over a live `.claude/` or memory folder: a stale backup would
   overwrite newer notes.
5. Put each file in place:
   - the agents' notes (briefs, reviews, execution logs, `ops/` scripts) into `.claude/` of the new
     checkout;
   - the agents' memory into Claude Code's per-project memory folder for the new checkout's path;
   - §1's JSON into `.claude/settings.local.json`, merged with any keys already there;
   - §2's patterns into a `PreToolUse` hook in the Claude Code host.
6. Check: `permissions.deny` and `sandbox` in `.claude/settings.local.json` equal §1's block (the
   wave-close diff does exactly this); a command that names `probe.pyi` is refused; and
   `pixi run golden` passes.
