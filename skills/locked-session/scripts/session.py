#!/usr/bin/env python3
"""Session state kept in the repository: set-up, per-topic locks, branch states, stale state, end-of-session checklist.

    python3 <this script> [--repo .] <command> ...      (python3 <this script> --help lists the commands)

Commands, options, file names and messages come from the JSON file with the same name next to this script, so the
code is the same for every language. Commands that write files: set-up and new topic (they never overwrite a file),
index (rewrites only the table between its markers), take (rewrites only the state line). The others only read.
Exit codes: 0 ok · 1 something to look at (stale state, a lock held by another branch) · 2 usage or set-up error.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
T = json.loads((HERE / f"{Path(__file__).stem}.json").read_text(encoding="utf-8"))
M = T["m"]
DATE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ---------------------------------------------------------------- configuration
class Entry:
    def __init__(self, path: str, kind: str | None, when: str):
        self.path, self.kind, self.when = path, kind, when


def read_config(repo: Path) -> tuple[list[Entry], str | None]:
    """Lines `path [tag] | when`; `<tz_key>: X` sets the time zone; `#` starts a comment."""
    cfg = repo / T["config"]
    if not cfg.exists():
        return [], None
    entries, zone = [], None
    rev = {v: k for k, v in T["tags"].items()}
    for raw in cfg.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line and line.lower().startswith(T["tz_key"] + ":"):
            zone = line.split(":", 1)[1].strip() or None
            continue
        left, _, when = (p.strip() for p in line.partition("|"))
        kind = next((rev[t] for t in rev if t in left), None)
        for t in rev:
            left = left.replace(t, "")
        entries.append(Entry(left.strip(), kind, when))
    return entries, zone


def write_default_config(repo: Path) -> Path:
    cfg = repo / T["config"]
    cfg.parent.mkdir(parents=True, exist_ok=True)
    head = "".join(line + "\n" for line in T["config_head"])
    body = "".join(f"{p}{' ' + T['tags'][k] if k else ''} | {w}\n" for p, k, w in T["defaults"])
    cfg.write_text(head + body, encoding="utf-8")
    return cfg


def first(entries: list[Entry], kind: str) -> Entry | None:
    return next((e for e in entries if e.kind == kind), None)


def expand(repo: Path, entries: list[Entry]) -> list[tuple[Entry, str]]:
    """Every entry with its real files (globs expanded on disk)."""
    out = []
    for e in entries:
        if any(c in e.path for c in "*?["):
            out += [(e, p.relative_to(repo).as_posix()) for p in sorted(repo.glob(e.path))]
        else:
            out.append((e, e.path))
    return out


# ---------------------------------------------------------------- helpers
def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def state_line(text: str) -> str | None:
    return next((ln for ln in text.splitlines() if ln.startswith(T["state"])), None)


def field(text: str, key: str) -> str:
    line = next((ln for ln in text.splitlines() if ln.startswith(key)), "")
    return line[len(key):].strip()


def state_time(line: str | None, zone: str | None) -> datetime | None:
    m = DATE.search(line or "")
    if not m:
        return None
    when = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
    if zone:
        from zoneinfo import ZoneInfo
        when = when.replace(tzinfo=ZoneInfo(zone))
    else:
        when = when.astimezone()
    return when + timedelta(seconds=59)        # the whole minute written in the line belongs to the state


def now_text(zone: str | None) -> str:
    if zone:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def template(name: str) -> str | None:
    if not name:
        return None
    p = HERE.parent / T["templates"] / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def fill(text: str, **values: str) -> str:
    """Replace {{name}} in a template; the names used in the templates come from the JSON ("placeholders")."""
    for k, v in values.items():
        text = text.replace("{{" + T["placeholders"][k] + "}}", v)
    return text


# ---------------------------------------------------------------- commands
def cmd_setup(repo: Path, topic: str | None) -> int:
    entries, zone = read_config(repo)
    if not entries:
        print(M["cfg_new"], write_default_config(repo).relative_to(repo).as_posix())
        entries, zone = read_config(repo)
    else:
        print(M["cfg_read"], T["config"])
    stamp = now_text(zone)
    index = first(entries, "index")
    for e in entries:
        if e.kind == "lock":
            continue
        target = repo / e.path
        if target.exists():
            print(" ", M["exists"], e.path)
            continue
        tpl = template(T["tpl"].get(e.kind or "", "") or T["tpl"].get(e.path, ""))
        if tpl is None:
            print(" ", M["missing"], e.path, M["no_tpl"])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fill(tpl, date=stamp), encoding="utf-8")
        print(" ", M["created"], e.path)
    lock = first(entries, "lock")
    if lock and not [p for _, p in expand(repo, [lock])]:
        cmd_topic(repo, topic or T["first_topic"], quiet=True)
    elif lock and topic and not (repo / lock.path.replace("*", topic)).exists():
        cmd_topic(repo, topic, quiet=True)
    if index:
        cmd_index(repo, quiet=True)
    handoff = first(entries, "handoff")
    locks = [p for _, p in expand(repo, [lock])] if lock else []
    print(M["next"].format(h=handoff.path if handoff else "-", p=locks[0] if locks else "-"))
    return 0


def cmd_topic(repo: Path, name: str, title: str | None = None, quiet: bool = False) -> int:
    entries, zone = read_config(repo)
    lock = first(entries, "lock")
    if lock is None:
        print(M["no_lock"].format(c=T["config"]))
        return 2
    if not NAME.match(name):
        print(M["topic_bad"])
        return 2
    rel = lock.path.replace("*", name) if "*" in lock.path else lock.path
    target = repo / rel
    if target.exists():
        print(M["topic_exists"].format(p=rel))
        return 2
    stamp = now_text(zone)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fill(template(T["tpl"]["lock"]) or "", date=stamp, topic=title or name, timezone=zone or ""),
                      encoding="utf-8")
    print(" ", M["created"], rel)
    if not quiet and first(entries, "index"):
        cmd_index(repo)
    return 0


def cmd_take(repo: Path, name: str) -> int:
    """Write the busy word with the current time on the topic's state line. Nothing else changes."""
    entries, zone = read_config(repo)
    lock = first(entries, "lock")
    if lock is None:
        print(M["no_lock"].format(c=T["config"]))
        return 2
    rel = lock.path.replace("*", name) if "*" in lock.path else lock.path
    path = repo / rel
    if not path.exists():
        print(M["no_topic"].format(p=rel))
        return 2
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    value = f"{T['words']['busy']} — {now_text(zone)}"
    for i, ln in enumerate(lines):
        if ln.startswith(T["state"]):
            lines[i] = f"{T['state']} {value}\n"
            break
    else:
        lines.insert(0, f"{T['state']} {value}\n")
    path.write_text("".join(lines), encoding="utf-8")
    print(M["taken"].format(p=rel, s=value))
    if first(entries, "index"):
        cmd_index(repo, quiet=True)
    return 0


def topic_rows(repo: Path, entries: list[Entry]) -> list[tuple[str, str, str, str]]:
    lock = first(entries, "lock")
    rows = []
    for _, rel in expand(repo, [lock] if lock else []):
        text = (repo / rel).read_text(encoding="utf-8")
        name = field(text, T["topic_key"]) or Path(rel).stem
        rows.append((name, state_line(text) or M["no_state"], field(text, T["resume"]) or "—", rel))
    return rows


def cmd_index(repo: Path, quiet: bool = False) -> int:
    entries, _ = read_config(repo)
    index = first(entries, "index")
    if index is None:
        print(M["no_cfg"].format(c=T["config"]))
        return 2
    path = repo / index.path
    text = path.read_text(encoding="utf-8")
    a, b = T["marks"]
    if a not in text or b not in text:
        print(M["index_nomark"].format(p=index.path, a=a, b=b))
        return 2
    base = str(Path(index.path).parent)
    head = T["table_head"] + "\n|---|---|---|---|\n"
    rows = topic_rows(repo, entries)
    table = head + "".join(
        f"| {n} | {s.replace(T['state'], '').strip()} | {r} | [`{p}`]({Path(os.path.relpath(p, base)).as_posix()}) |\n"
        for n, s, r, p in rows)
    new = text[:text.index(a) + len(a)] + "\n" + table + text[text.index(b):]
    path.write_text(new, encoding="utf-8")
    if not quiet:
        print(M["index_done"].format(n=len(rows), p=index.path))
    return 0


def cmd_branches(repo: Path, prefix: str, days: int, also: list[str]) -> int:
    entries, _ = read_config(repo)
    lock = first(entries, "lock")
    if lock is None:
        print(M["no_cfg"].format(c=T["config"]))
        return 2
    heads = [ln.split("refs/heads/", 1)[1] for ln in git(repo, "ls-remote", "--heads", "origin").stdout.splitlines()]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows, busy = [], False
    mine = git(repo, "branch", "--show-current", check=False).stdout.strip()
    for h in [h for h in heads if h.startswith(prefix) or h in also]:
        ref = f"refs/remotes/origin/{h}"
        if git(repo, "fetch", "-q", "origin", f"+refs/heads/{h}:{ref}", check=False).returncode:
            continue
        when = datetime.fromisoformat(git(repo, "log", "-1", "--format=%cI", ref).stdout.strip())
        if when < since and h not in also:
            continue
        files = git(repo, "ls-tree", "-r", "--name-only", ref).stdout.split()
        for f in [f for f in files if fnmatch.fnmatch(f, lock.path)]:
            line = state_line(git(repo, "show", f"{ref}:{f}").stdout) or M["no_state"]
            rows.append((when, h, f, line))
            busy |= T["words"]["busy"] in line and h != mine
    rows.sort(key=lambda r: (r[0], r[2]), reverse=True)
    print(M["head_br"])
    for when, h, f, line in rows:
        mark = ("  ◀ " + M["mine"] if h == mine else "  ◀ " + M["busy"]) if T["words"]["busy"] in line else ""
        print(f"  {when.strftime('%Y-%m-%d %H:%M')}  {h:34} {f:38} {line}{mark}")
    if not rows:
        print(" ", M["none_br"])
    return 1 if busy else 0


def cmd_stale(repo: Path, ref: str, only: str | None) -> int:
    entries, zone = read_config(repo)
    lock = first(entries, "lock")
    if lock is None:
        print(M["no_cfg"].format(c=T["config"]))
        return 2
    tree = git(repo, "ls-tree", "-r", "--name-only", ref).stdout.split()
    locks = [f for f in tree if fnmatch.fnmatch(f, lock.path) and (only is None or f == only)]
    quiet_files = set(locks) | {e.path for e in entries if e.kind == "index"}
    worst = 0
    for f in locks:
        when = state_time(state_line(git(repo, "show", f"{ref}:{f}").stdout), zone)
        if when is None:
            print(f"{f}: {M['no_state']}")
            worst = max(worst, 2)
            continue
        later = []
        log = git(repo, "log", "--no-merges", f"--since={when.isoformat()}", "--format=%h%x09%cI%x09%s", ref).stdout
        for line in log.splitlines():
            sha, date, subject = line.split("\t", 2)
            changed = set(git(repo, "show", "--name-only", "--format=", sha).stdout.split())
            if changed and changed <= quiet_files:
                continue                       # only state files changed: not news
            later.append(f"{sha} {date[:16].replace('T', ' ')} {subject}")
        if not later:
            print(f"{f}: {M['stale_ok']} ({when.strftime('%Y-%m-%d %H:%M')})")
            continue
        worst = max(worst, 1)
        print(f"{f}: {M['stale_bad'].format(n=len(later))} ({when.strftime('%Y-%m-%d %H:%M')})")
        for c in later[:8]:
            print(f"    {c}")
        if len(later) > 8:
            print(f"    … +{len(later) - 8}")
        print(f"    {M['stale_hint']}")
    return worst


def cmd_close(repo: Path, base: str) -> int:
    entries, zone = read_config(repo)
    if not entries:
        print(M["no_cfg"].format(c=T["config"]))
        return 2
    try:
        mb = git(repo, "merge-base", base, "HEAD").stdout.strip()
        changed = set(git(repo, "diff", "--name-only", mb, "HEAD").stdout.split())
        changed |= set(git(repo, "diff", "--name-only", "HEAD").stdout.split())
        changed |= set(git(repo, "ls-files", "--others", "--exclude-standard").stdout.split())
    except subprocess.CalledProcessError as e:
        print(M["git_fail"].format(b=base, e=(e.stderr or "").strip() or "merge-base"))
        return 2
    rows = expand(repo, entries)
    width = max((len(p) for _, p in rows), default=0)
    print(M["close_head"].format(b=base))
    for e, p in rows:
        mark = M["changed"] if p in changed else M["todo"]
        print(f"  [{mark}] {p.ljust(width)}  {e.when}")
        if e.kind == "lock" and (repo / p).exists():
            line = state_line((repo / p).read_text(encoding="utf-8")) or M["no_state"]
            print(f"             {line}")
            if T["words"]["busy"] in line:
                print(M["still_busy"])
            if p in changed:
                old = git(repo, "show", f"{mb}:{p}", check=False)
                if old.returncode == 0 and state_time(state_line(old.stdout), zone) == state_time(line, zone):
                    print(M["date_old"])
    print(M["others"].format(n=len(changed - {p for _, p in rows})))
    return 0


def main(argv: list[str] | None = None) -> int:
    c = T["cmd"]
    ap = argparse.ArgumentParser(description=T["description"])
    o = T["opt"]
    ap.add_argument("--repo", default=".", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser(c["setup"]); s.add_argument("--" + c["topic"], dest="topic")
    s = sub.add_parser(c["topic"]); s.add_argument("name"); s.add_argument(o["title"], dest="title")
    sub.add_parser(c["index"])
    s = sub.add_parser(c["take"]); s.add_argument("name")
    s = sub.add_parser(c["branches"]); s.add_argument(o["prefix"], dest="prefix", default="claude/")
    s.add_argument(o["days"], dest="days", type=int, default=30)
    s.add_argument(o["also"], dest="also", nargs="*", default=["main"])
    s = sub.add_parser(c["stale"]); s.add_argument(o["ref"], dest="ref", default="origin/main"); s.add_argument(o["file"], dest="only")
    s = sub.add_parser(c["close"]); s.add_argument(o["base"], dest="base", default="origin/main")
    a = ap.parse_args(argv)
    repo = a.repo.resolve()
    if a.cmd == c["setup"]:
        return cmd_setup(repo, a.topic)
    if a.cmd == c["topic"]:
        return cmd_topic(repo, a.name, a.title)
    if a.cmd == c["index"]:
        return cmd_index(repo)
    if a.cmd == c["take"]:
        return cmd_take(repo, a.name)
    if a.cmd == c["branches"]:
        return cmd_branches(repo, a.prefix, a.days, a.also)
    if a.cmd == c["stale"]:
        return cmd_stale(repo, a.ref, a.only)
    return cmd_close(repo, a.base)


if __name__ == "__main__":
    sys.exit(main())
