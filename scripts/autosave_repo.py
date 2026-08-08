#!/usr/bin/env python3
"""Autosave a git repo: optional mirror-in of an external directory, then
commit + push everything the repo tracks. The durability loop for living
documents that change all day but only get committed when someone remembers.

  autosave_repo.py --repo <path> [--mirror SRC DEST] [--dry-run]

  --repo    the working copy to commit and push (must have a remote upstream)
  --mirror  copy SRC (a directory outside the repo, e.g. an agent-memory dir)
            into <repo>/DEST before committing, so it rides the same backup.
            Mirroring is exact (deletions propagate) but refuses to run from
            an unreadable or empty SRC — a permissions failure must never
            wipe the last good copy.

Design notes:
  - `git add -A` respects .gitignore: whatever the repo deliberately excludes
    stays excluded. The gitignore is the opt-out surface, not this script.
  - Push failure is LOUD but non-fatal (offline is normal on a laptop); the
    commit is durable locally and the next run — or the chief-of-staff brief's
    "repo loose ends" section — surfaces what's still unpushed.
  - No pull, no rebase, no force: this loop only ever appends its own commits.
    Run it from ONE machine per repo; a second machine autosaving the same
    remote will diverge and the push warning is the tell.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import subprocess
import sys


def run(args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def mirror(src: pathlib.Path, dest: pathlib.Path, dry: bool) -> str:
    if not src.is_dir():
        return f"mirror: SKIP — source {src} is not a readable directory"
    entries = [p for p in src.iterdir() if p.name != ".DS_Store"]
    if not entries:
        return f"mirror: SKIP — source {src} is empty (refusing to mirror emptiness)"
    if dry:
        return f"mirror: would sync {len(entries)} entr(ies) {src} -> {dest}"
    dest.mkdir(parents=True, exist_ok=True)
    # rsync -a --delete: an exact mirror, so retired memory files retire in the
    # backup too instead of accumulating forever. Trailing slash = contents-of.
    r = run(["rsync", "-a", "--delete", "--exclude", ".DS_Store",
             f"{src}/", str(dest)], check=False)
    if r.returncode != 0:
        return f"mirror: FAILED (rsync {r.returncode}: {r.stderr.strip()[:200]})"
    return f"mirror: synced {src.name}/ ({len(entries)} entr(ies)) -> {dest}"


def autosave(repo: pathlib.Path, dry: bool) -> int:
    if not (repo / ".git").exists():
        print(f"autosave: {repo} is not a git repo — nothing done", file=sys.stderr)
        return 1
    run(["git", "-C", str(repo), "add", "-A"])
    staged = run(["git", "-C", str(repo), "diff", "--cached", "--name-only"]).stdout
    files = [f for f in staged.splitlines() if f.strip()]
    if not files:
        print("autosave: clean — nothing to commit")
    else:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = f"autosave: {stamp} ({len(files)} file(s))"
        if dry:
            print(f"autosave: would commit {len(files)} file(s): {msg}")
            run(["git", "-C", str(repo), "reset"], check=False)
            return 0
        run(["git", "-C", str(repo), "commit", "-m", msg])
        print(f"autosave: committed {len(files)} file(s)")
    push = run(["git", "-C", str(repo), "push"], check=False)
    if push.returncode != 0:
        print(f"autosave: PUSH FAILED (non-fatal, commit is safe locally): "
              f"{(push.stderr or push.stdout).strip()[:300]}", file=sys.stderr)
        return 0   # the loose-ends report carries it from here; don't alarm launchd
    print("autosave: pushed")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--mirror", nargs=2, metavar=("SRC", "DEST"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).expanduser().resolve()
    print(f"— autosave {datetime.datetime.now():%Y-%m-%d %H:%M} — {repo}")
    if a.mirror:
        src = pathlib.Path(a.mirror[0]).expanduser()
        dest = (repo / a.mirror[1]).resolve()
        if repo not in dest.parents and dest != repo:
            sys.exit(f"mirror DEST {dest} escapes the repo — refusing")
        print(mirror(src, dest, a.dry_run))
    sys.exit(autosave(repo, a.dry_run))


if __name__ == "__main__":
    main()
