# locked-session

Locked-session skill is for projects that outlive a single conversation. The state of the work lives in the repository's own
files, not in the model's memory: every session reads it, checks that it is still true, locks the topic it works on,
and leaves the state up to date for the next session.

## What it gives you

- **One next-session prompt per development topic** (`next_sessions/<topic>.md`), each with its own lock — the
  `Status: IN PROGRESS | WAITING | STOPPED — YYYY-MM-DD HH:MM` line. Two sessions can work in parallel on different
  topics without overwriting each other.
- **An index** (`NEXT_SESSION.md`) whose table of topics is rebuilt by the script, never edited by hand.
- **Locks on unmerged branches**: the newest state of a topic may sit on a branch that is not merged yet.
- **Stale-state check**: commits made after the date written in a `Status:` line mean the prompt may be out of date.
- **[V]/[?] rule**: every statement about the state says whether it was verified in this session or taken from a
  document.
- **End-of-session checklist**: which state documents the branch changed, and warnings when a lock is still
  `IN PROGRESS` or its date did not move.
- **Pull requests are never merged** without the user's go-ahead.
- **Automatic set-up**: on a project without the files, `session.py setup` creates the configuration, a handoff, the
  index, a first topic prompt, a decision log and a changelog from templates. It never overwrites an existing file.

## Install

In Claude Code 2.1.275 or later, from the repository that holds the marketplace:

```
/plugin install locked-session --marketplace SoftProjectsWork/locked-session
```

Or as a plain project skill: copy `skills/locked-session/` into `.claude/skills/` of your repository.

The `.plugin` file of this folder can also be added from the Claude app (Customize → Plugins).

## Use

Start a session with "let's resume" (or name a topic: "let's resume billing"); end it with "close the session". The
skill runs one script, `skills/locked-session/scripts/session.py`, with subcommands:

| Command | What it does |
|---|---|
| `setup [--topic N]` | creates the configuration and every missing file |
| `topic N [--title T]` | new topic prompt |
| `take N` | locks topic N (`IN PROGRESS` with the current time) |
| `index` | rebuilds the topic table |
| `branches` | locks on `main` and recent `claude/*` branches |
| `stale [--file F]` | commits after each `Status:` date |
| `close` | end-of-session checklist |

The files are declared in `.claude/locked-session.txt` (`path [tag] | when to update it`); change paths or add
documents there. Requirements: Python 3.9+ and git. No other dependency.

## License

MIT — see `LICENSE`.
