"""CI mode: check a pull request or push, annotate it, and write a GitHub job summary."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, Optional, Tuple

from . import gitio
from . import settings as settings_mod
from .claims import read_intents
from .engine import Policy, Scope, scan_git
from .model import Severity
from .report import render
from .rules import BY_ID
from .term import style_for

ZERO = "0" * 40
ALLOW_LINE = re.compile(r"(?im)^\s*(?:[-*]\s*)?redhanded-allow\s*:\s*([\w\s,*-]+?)(?:\s+(?:-|—|–|because|:)\s*(.*))?\s*$")


def parse_allow(body: str) -> Dict[str, str]:
    """`redhanded-allow: test-deleted, snapshot-rewritten - removing the v1 API` -> {rule: reason}."""
    allowed: Dict[str, str] = {}
    for m in ALLOW_LINE.finditer(body or ""):
        names = [n.strip() for n in re.split(r"[,\s]+", m.group(1)) if n.strip()]
        reason = (m.group(2) or "").strip()
        label = "Allowed in the PR description" + (": " + reason if reason else "")
        for name in names:
            if name in ("*", "all"):
                allowed["*"] = label
            elif name in BY_ID:
                allowed[name] = label
    return allowed


def _event() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _ensure(root: str, sha: str) -> bool:
    if gitio.rev_exists(root, sha):
        return True
    try:
        gitio.git(["fetch", "--no-tags", "--quiet", "origin", sha], root, timeout=180)
    except gitio.GitError:
        return False
    return gitio.rev_exists(root, sha)


def _merge_base(root: str, base: str, head: str) -> Optional[str]:
    mb = gitio.merge_base(root, base, head)
    if mb:
        return mb
    try:
        shallow = gitio.text(["rev-parse", "--is-shallow-repository"], root).strip() == "true"
    except gitio.GitError:
        shallow = False
    if shallow:
        try:
            gitio.git(["fetch", "--no-tags", "--quiet", "--unshallow", "origin"], root, timeout=600)
        except gitio.GitError:
            return None
        return gitio.merge_base(root, base, head)
    return None


def resolve(root: str, base: Optional[str], head: Optional[str]) -> Tuple[Scope, str, str]:
    """(scope, PR title, PR body) for the current CI event."""
    event = _event()
    title = body = ""
    label = ""
    pr = event.get("pull_request") if isinstance(event.get("pull_request"), dict) else None
    if pr:
        base = base or ((pr.get("base") or {}).get("sha"))
        head = head or ((pr.get("head") or {}).get("sha"))
        title, body = pr.get("title") or "", pr.get("body") or ""
        label = "pull request #%s" % pr.get("number", "?")
    elif event.get("before") or event.get("after"):
        before, after = event.get("before"), event.get("after")
        base = base or (before if before and before != ZERO else None)
        head = head or after
        label = "push"
    head = head or gitio.resolve(root, "HEAD")
    if not head:
        raise gitio.GitError("no commits to check")
    if not base:
        base = gitio.default_branch_base(root) or gitio.resolve(root, head + "~1") or gitio.EMPTY_TREE
    for rev in (base, head):
        if rev != gitio.EMPTY_TREE and not _ensure(root, rev):
            raise gitio.GitError("commit %s is not available; use actions/checkout with fetch-depth: 0" % rev[:12])
    mb = base if base == gitio.EMPTY_TREE else _merge_base(root, base, head)
    if not mb:
        raise gitio.GitError("could not find where %s branched from %s; use actions/checkout with fetch-depth: 0"
                             % (head[:12], base[:12]))
    label = "%s %s..%s" % (label or "changes", mb[:8], head[:8])
    return Scope(mb, head, False, label.strip(), head), title, body


def _gh_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("%s=%s\n" % (name, value))


def run_ci(args) -> int:
    root = gitio.repo_root(os.path.abspath(args.cwd))
    if not root:
        sys.stderr.write("redhanded: not inside a git repository\n")
        return 2
    cfg = settings_mod.load(root)
    try:
        scope, title, body = resolve(root, args.base, args.head)
    except gitio.GitError as exc:
        sys.stderr.write("redhanded: %s\n" % exc)
        return 2
    # A PR description can explain intended changes; it never raises severities the way a user prompt can.
    allowed = read_intents([title, body])[0] if (title or body) else {}
    policy = Policy(intents=allowed, forbidden={}, allow=parse_allow(body))
    report = scan_git(root, scope, cfg, policy)
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    sys.stdout.write(render(report, "text", style=style_for(sys.stdout, color=in_actions or None)))
    if in_actions:
        sys.stdout.write(render(report, "github"))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(render(report, "markdown"))
    if args.sarif:
        with open(args.sarif, "w", encoding="utf-8") as fh:
            fh.write(render(report, "sarif"))
    counts = report.counts()
    _gh_output("verdict", report.verdict)
    for key in ("high", "medium", "low"):
        _gh_output(key, str(counts[key]))
    threshold = args.fail_on if args.fail_on is not None else cfg.fail_on
    if threshold != Severity.OFF and report.max_severity() >= threshold:
        return 1
    return 0
