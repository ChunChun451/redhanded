"""`redhanded init`: wire redhanded into Claude Code, GitHub Actions or pre-commit."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

from . import __version__, gitio
from .transcript import claude_home

REPO = "ChunChun451/redhanded"
EVENTS = [
    ("SessionStart", None, "session-start", 30),
    ("PostToolUse", "Edit|Write|MultiEdit", "post-edit", 30),
    ("Stop", None, "stop", 90),
]

WORKFLOW = """name: redhanded

on:
  pull_request:

permissions:
  contents: read

jobs:
  redhanded:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
      - uses: %(repo)s@v0
        with:
          fail-on: high
""" % {"repo": REPO}

PRE_COMMIT = """  - repo: https://github.com/%(repo)s
    rev: v%(version)s
    hooks:
      - id: redhanded
""" % {"repo": REPO, "version": __version__}


def launcher() -> List[str]:
    """[executable, args...] that runs this copy of redhanded."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(os.path.dirname(os.path.dirname(pkg_dir)), "redhanded.py")
    if os.path.isfile(script):
        return [sys.executable, script]
    return [sys.executable, "-m", "redhanded"]


def _is_ours(hook: Dict[str, Any]) -> bool:
    blob = json.dumps(hook)
    return "redhanded" in blob


def merge_hooks(settings: Dict[str, Any], command: List[str]) -> List[str]:
    """Add redhanded hooks to a Claude Code settings dict. Returns the events that were added."""
    hooks = settings.setdefault("hooks", {})
    added = []
    for event, matcher, name, timeout in EVENTS:
        groups = hooks.setdefault(event, [])
        if any(_is_ours(h) for g in groups if isinstance(g, dict) for h in g.get("hooks", []) if isinstance(h, dict)):
            continue
        entry: Dict[str, Any] = {"type": "command", "command": command[0], "args": command[1:] + ["hook", name],
                                 "timeout": timeout}
        group: Dict[str, Any] = {"hooks": [entry]}
        if matcher:
            group = {"matcher": matcher, "hooks": [entry]}
        groups.append(group)
        added.append(event)
    return added


def init_claude(args) -> int:
    if args.user:
        path = os.path.join(claude_home(), "settings.json")
    else:
        root = gitio.repo_root(os.path.abspath(args.cwd)) or os.path.abspath(args.cwd)
        path = os.path.join(root, ".claude", "settings.local.json")
    settings: Dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh) or {}
        except ValueError as exc:
            sys.stderr.write("redhanded: %s is not valid JSON (%s); fix it first\n" % (path, exc))
            return 2
    added = merge_hooks(settings, launcher())
    if not added:
        print("redhanded hooks are already set up in %s" % path)
        return 0
    text = json.dumps(settings, indent=2) + "\n"
    if args.dry_run:
        print("Would write %s:\n%s" % (path, text))
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("Added redhanded hooks (%s) to %s" % (", ".join(added), path))
    print("Start a new Claude Code session to load them. The plugin is the easier route for most people:")
    print("  /plugin marketplace add %s" % REPO)
    print("  /plugin install redhanded@redhanded")
    return 0


def init_github(args) -> int:
    root = gitio.repo_root(os.path.abspath(args.cwd)) or os.path.abspath(args.cwd)
    path = os.path.join(root, ".github", "workflows", "redhanded.yml")
    if os.path.exists(path):
        print("%s already exists; leaving it alone" % path)
        return 0
    if args.dry_run:
        print("Would write %s:\n%s" % (path, WORKFLOW))
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(WORKFLOW)
    print("Wrote %s. Commit it and open a pull request to see redhanded run." % path)
    return 0


def init_pre_commit(args) -> int:
    root = gitio.repo_root(os.path.abspath(args.cwd)) or os.path.abspath(args.cwd)
    path = os.path.join(root, ".pre-commit-config.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            if "redhanded" in fh.read():
                print("redhanded is already in %s" % path)
                return 0
        print("Add this under `repos:` in %s:\n\n%s" % (path, PRE_COMMIT))
        return 0
    text = "repos:\n" + PRE_COMMIT
    if args.dry_run:
        print("Would write %s:\n%s" % (path, text))
        return 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("Wrote %s. Run `pre-commit install` to enable it." % path)
    return 0


def run_init(args) -> int:
    return {"claude": init_claude, "github": init_github, "pre-commit": init_pre_commit}[args.target](args)
