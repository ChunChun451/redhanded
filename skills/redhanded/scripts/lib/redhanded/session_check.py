"""Check one agent session: its diff (from git or the transcript) plus what its transcript shows."""

from __future__ import annotations

import os
from typing import List, Optional

from . import gitio
from . import settings as settings_mod
from .attribution import Attribution
from .claims import read_intents
from .engine import Policy, analyze
from .model import FileChange, Report
from .rules.session import session_findings
from .settings import Settings
from .transcript import Session, file_changes


def policy_for(session: Session, extra_prompts: Optional[List[str]] = None) -> Policy:
    allowed, forbidden = read_intents(list(session.prompts) + list(extra_prompts or []))
    return Policy(intents=allowed, forbidden=forbidden)


def git_changes(root: str, baseline: str, subdir: Optional[str] = None) -> Optional[tuple]:
    current = gitio.snapshot_tree(root, subdir=subdir)
    if not current:
        return None
    changes = gitio.diff(root, baseline, current, paths=(subdir,) if subdir else ())
    gitio.load_contents(root, changes, baseline, current)
    return changes, current


def check_session(session: Session, root: Optional[str] = None, settings: Optional[Settings] = None,
                  baseline: Optional[str] = None, final_text: Optional[str] = None, only_last_turn: bool = False,
                  extra_prompts: Optional[List[str]] = None) -> Report:
    """Analyse a session.

    With `baseline` (a tree captured when the session started) the diff comes from git and includes changes made by
    shell commands. Without it, changes are rebuilt from the edits recorded in the transcript.
    """
    root = os.path.abspath(root) if root else (os.path.abspath(session.cwd) if session.cwd else None)
    if root and not os.path.isdir(root):
        repo = None
    else:
        repo = gitio.repo_root(root) if root else None
    # When the session ran in a subfolder of a larger repository (a monorepo package, or a home-directory dotfiles
    # repo), only look at that subfolder.
    subdir = gitio.subdir_of(repo, session.cwd or root) if repo else None
    settings = settings or settings_mod.load(repo or root)
    policy = policy_for(session, extra_prompts)
    notes: List[str] = []
    changes: List[FileChange] = []
    corpus_rev = None
    short_id = session.id[:8]
    scope = "session %s" % short_id
    used_git = False
    if repo and baseline:
        got = git_changes(repo, baseline, subdir)
        if got is not None:
            changes, corpus_rev = got
            used_git = True
            scope = "session %s (git diff since the session started)" % short_id
    if not used_git:
        changes = file_changes(session, repo or root)
        scope = "session %s (edits recorded in the transcript)" % short_id
        if repo is None and root and not os.path.isdir(root):
            notes.append("project folder no longer exists; checked the recorded edits only")
    extra = session_findings(session, repo or root, final_text=final_text, only_last_turn=only_last_turn,
                             project_dir=session.cwd or root)
    report = analyze(changes, settings, policy, root=repo, corpus_rev=corpus_rev, scope=scope, extra=extra, notes=notes,
                     subdir=subdir)
    if used_git:
        mark_foreign(report, session, repo)
    return report


FOREIGN_NOTE = ("This file was not changed by any edit or command recorded in this session; the change may come from "
                "another session, a tool, or a manual edit.")


def mark_foreign(report: Report, session: Session, root: Optional[str]) -> int:
    """Flag findings in files that this session's recorded edits and commands never touched."""
    attribution = Attribution(session, root)
    count = 0
    for f in report.findings:
        if f.path and not attribution.covers(f.path):
            f.foreign = True
            f.detail = (f.detail + " " if f.detail else "") + FOREIGN_NOTE
            count += 1
    if count:
        report.notes.append("%d finding(s) are in files this session did not touch" % count)
    return count
