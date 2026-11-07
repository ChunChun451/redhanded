"""Run every rule over a set of changes and turn the results into a Report."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import gitio
from .classify import classify, is_related_test, norm_path
from .diffs import hunks_from_texts
from .model import FileChange, Finding, Report, Severity
from .rules import BY_ID, Context
from .rules import config as config_rules
from .rules import impl as impl_rules
from .rules import tests as test_rules
from .settings import Settings

FILE_RULES = test_rules.FILE_RULES + impl_rules.FILE_RULES + config_rules.FILE_RULES
SET_RULES = test_rules.SET_RULES + impl_rules.SET_RULES + config_rules.SET_RULES

IGNORE_LINE = re.compile(r"redhanded-ignore(?![-\w])(?:\[([\w\s,*-]+)\])?")
IGNORE_FILE = re.compile(r"redhanded-ignore-file(?:\[([\w\s,*-]+)\])?")
CATEGORY_ORDER = {"session": 0, "tests": 1, "implementation": 2, "config": 3}

MAX_CORPUS_FILES = 40
SMALL_REPO_TESTS = 150


@dataclass
class Policy:
    """Context that legitimately raises or lowers severities."""

    intents: Dict[str, str] = field(default_factory=dict)  # intent -> the user's words asking for it
    forbidden: Dict[str, str] = field(default_factory=dict)  # intent -> the user's words ruling it out
    allow: Dict[str, str] = field(default_factory=dict)  # rule id (or "*") -> reason, e.g. from a PR description


class Corpus:
    """Finds the test files that a production file's tests most likely live in, lazily and with caching."""

    def __init__(self, changes: List[FileChange], root: Optional[str], rev: Optional[str], settings: Settings,
                 subdir: Optional[str] = None):
        self.changes = changes
        self.root = root
        self.rev = rev
        self.settings = settings
        self.subdir = subdir
        self._repo_tests: Optional[List[str]] = None
        self._cache: Dict[str, List[Tuple[str, List[str]]]] = {}
        self._text: Dict[str, Optional[str]] = {}
        self._changed = {c.path: c for c in changes if c.info.is_test and c.new_text is not None}

    def repo_tests(self) -> List[str]:
        if self._repo_tests is None:
            paths: List[str] = []
            if self.root:
                for p in gitio.tracked_files(self.root, self.subdir):
                    info = classify(p, self.settings.test_globs, self.settings.ignore)
                    if info.is_test and not info.is_generated and info.is_code:
                        paths.append(p)
            self._repo_tests = paths
        return self._repo_tests

    def _read(self, paths: Sequence[str]) -> None:
        missing = [p for p in paths if p not in self._text and p not in self._changed]
        if not missing or not self.root:
            for p in missing:
                self._text[p] = None
            return
        if self.rev:
            blobs = gitio.read_blobs(self.root, ["%s:%s" % (self.rev, p) for p in missing])
            for p in missing:
                self._text[p] = blobs.get("%s:%s" % (self.rev, p))
        else:
            for p in missing:
                self._text[p] = gitio.read_worktree(self.root, p)

    def __call__(self, change: FileChange) -> List[Tuple[str, List[str]]]:
        if change.path in self._cache:
            return self._cache[change.path]
        chosen: List[str] = []
        changed = list(self._changed)
        related_changed = [p for p in changed if is_related_test(p, change.path)]
        chosen.extend(related_changed)
        try:
            repo = self.repo_tests()
        except gitio.GitError:
            repo = []
        related_repo = [p for p in repo if p not in chosen and is_related_test(p, change.path)]
        chosen.extend(related_repo[:12])
        chosen.extend(p for p in changed if p not in chosen)
        if not related_changed and not related_repo and len(repo) <= SMALL_REPO_TESTS:
            chosen.extend(p for p in repo if p not in chosen)
        chosen = chosen[:MAX_CORPUS_FILES]
        self._read(chosen)
        out: List[Tuple[str, List[str]]] = []
        for p in chosen:
            text = self._changed[p].new_text if p in self._changed else self._text.get(p)
            if text:
                out.append((p, text.splitlines()))
        self._cache[change.path] = out
        return out


def prepare(changes: Iterable[FileChange], settings: Settings) -> List[FileChange]:
    ready: List[FileChange] = []
    for ch in changes:
        ch.path = norm_path(ch.path)
        ch.info = classify(ch.path, settings.test_globs, settings.ignore)
        if ch.binary or ch.info.is_generated:
            continue
        if not ch.hunks and (ch.old_text is not None or ch.new_text is not None) and ch.old_text != ch.new_text:
            ch.hunks = hunks_from_texts(ch.old_text or "", ch.new_text or "")
        ready.append(ch)
    return ready


def run_rules(changes: List[FileChange], ctx: Context, notes: List[str]) -> List[Finding]:
    debug = bool(os.environ.get("REDHANDED_DEBUG"))
    findings: List[Finding] = []
    for ch in changes:
        for rule in FILE_RULES:
            try:
                findings.extend(rule(ch, ctx))
            except Exception as exc:  # one broken rule must never take the whole check down
                if debug:
                    raise
                notes.append("internal error in %s on %s: %s" % (rule.__name__, ch.path, exc))
    for set_rule in SET_RULES:
        try:
            findings.extend(set_rule(changes, ctx))
        except Exception as exc:
            if debug:
                raise
            notes.append("internal error in %s: %s" % (set_rule.__name__, exc))
    return findings


def analyze(changes: Iterable[FileChange], settings: Optional[Settings] = None, policy: Optional[Policy] = None,
            root: Optional[str] = None, corpus_rev: Optional[str] = None, scope: str = "",
            extra: Sequence[Finding] = (), notes: Optional[List[str]] = None, subdir: Optional[str] = None) -> Report:
    settings = settings or Settings()
    policy = policy or Policy()
    notes = list(notes or [])
    ready = prepare(changes, settings)
    ctx = Context(changes=ready, test_corpus=Corpus(ready, root, corpus_rev, settings, subdir))
    findings = run_rules(ready, ctx, notes)
    findings.extend(extra)
    findings = finalize(findings, ready, settings, policy)
    return Report(findings=findings, scope=scope, files_checked=len(ready), notes=notes)


# --------------------------------------------------------------------------- post-processing


def _rules_in(match: "re.Match[str]") -> Optional[set]:
    if not match.group(1):
        return None
    names = {r.strip() for r in match.group(1).split(",") if r.strip()}
    return None if "*" in names or "all" in names else names


def _ignored(f: Finding, by_path: Dict[str, FileChange]) -> bool:
    ch = by_path.get(f.path or "")
    if ch is None:
        return False
    head = (ch.new_text or ch.old_text or "").splitlines()[:12]
    for text in head:
        m = IGNORE_FILE.search(text)
        if m:
            names = _rules_in(m)
            if names is None or f.rule in names:
                return True
    if not f.line:
        return False
    lines = ch.old_lines() if (f.snippet or "").startswith("-") else ch.new_lines()
    for n in (f.line, f.line - 1):
        if 1 <= n <= len(lines):
            m = IGNORE_LINE.search(lines[n - 1])
            if m:
                names = _rules_in(m)
                if names is None or f.rule in names:
                    return True
    return False


def apply_policy(f: Finding, settings: Settings, policy: Policy) -> Optional[Finding]:
    override = settings.rules.get(f.rule)
    if override is not None:
        if override == Severity.OFF:
            return None
        f.severity = override
    meta = BY_ID.get(f.rule)
    intent = meta.intent if meta else None
    allow_reason = policy.allow.get(f.rule) or policy.allow.get("*")
    if allow_reason:
        f.lowered(allow_reason, Severity.LOW)
    elif intent and intent in policy.forbidden:
        quote = policy.forbidden[intent]
        if f.severity < Severity.HIGH:
            f.original_severity = f.original_severity or f.severity
            f.severity = Severity(int(f.severity) + 1)
        f.detail = ((f.detail + " ") if f.detail else "") + 'The user said: "%s"' % quote
    elif intent and intent in policy.intents:
        f.lowered('Requested by the user: "%s"' % policy.intents[intent], Severity.LOW)
    return f


def finalize(findings: List[Finding], changes: List[FileChange], settings: Settings, policy: Policy) -> List[Finding]:
    by_path = {c.path: c for c in changes}
    best: Dict[str, Finding] = {}
    for f in findings:
        if _ignored(f, by_path):
            continue
        f2 = apply_policy(f, settings, policy)
        if f2 is None or f2.severity == Severity.OFF:
            continue
        key = f2.fingerprint
        if key not in best or f2.severity > best[key].severity:
            best[key] = f2
    return sort_findings(best.values())


def sort_findings(findings: Iterable[Finding]) -> List[Finding]:
    def key(f: Finding):
        meta = BY_ID.get(f.rule)
        return (-int(f.severity), CATEGORY_ORDER.get(meta.category if meta else "", 9), f.path or "", f.line or 0, f.rule)

    return sorted(findings, key=key)


# --------------------------------------------------------------------------- git scopes


@dataclass
class Scope:
    base: str
    head: Optional[str]  # commit or tree; None means the index (staged)
    staged: bool
    label: str
    corpus_rev: Optional[str]
    subdir: Optional[str] = None  # limit everything to this part of the repository
    head_commit: Optional[str] = None  # HEAD when the scope was resolved
    head_tree: Optional[str] = None

    @property
    def paths(self) -> Tuple[str, ...]:
        return (self.subdir,) if self.subdir else ()


def short(sha: Optional[str]) -> str:
    return (sha or "")[:8]


def resolve_scope(root: str, base: Optional[str] = None, head: Optional[str] = None, staged: bool = False,
                  branch: bool = False, last_commit: bool = False, subdir: Optional[str] = None) -> Scope:
    """Work out what to compare. Raises gitio.GitError with a readable message when a revision is unknown."""
    where = (" in %s/" % subdir) if subdir else ""
    if head and not base:
        raise gitio.GitError("--head needs --base")
    if base and not gitio.resolve(root, base) and base != gitio.EMPTY_TREE:
        raise gitio.GitError("unknown revision %r" % base)
    if head:
        if not gitio.resolve(root, head):
            raise gitio.GitError("unknown revision %r" % head)
        return Scope(base, head, False, "%s..%s%s" % (base, head, where), head, subdir)
    head_commit, head_tree = gitio.head_info(root)
    if staged:
        b = base or head_commit or gitio.EMPTY_TREE
        return Scope(b, None, True, "staged changes vs %s%s" % (base or "HEAD", where), None, subdir)
    if last_commit:
        if not head_commit:
            raise gitio.GitError("the repository has no commits")
        parent = gitio.resolve(root, head_commit + "~1") or gitio.EMPTY_TREE
        return Scope(parent, head_commit, False, "last commit (%s)%s" % (short(head_commit), where), head_commit,
                     subdir)
    if branch and not base:
        mb = gitio.default_branch_base(root)
        if not mb:
            raise gitio.GitError("could not find the main branch to compare with; pass --base")
        label = "this branch incl. uncommitted changes vs main (merge-base %s)" % short(mb)
        base = mb
    elif base:
        label = "working tree vs %s" % base
    elif head_commit:
        label = "uncommitted changes vs HEAD"
    else:
        label = "all files (no commits yet)"
    current = gitio.snapshot_tree(root, subdir=subdir)
    if not current:
        raise gitio.GitError("could not snapshot the working tree")
    return Scope(base or head_commit or gitio.EMPTY_TREE, current, False, label + where, current, subdir,
                 head_commit=head_commit, head_tree=head_tree)


def changes_for(root: str, scope: Scope, paths: Sequence[str] = ()) -> List[FileChange]:
    changes = gitio.diff(root, scope.base, scope.head, staged=scope.staged, paths=paths or scope.paths)
    gitio.load_contents(root, changes, scope.base, scope.head, staged=scope.staged)
    return changes


def scan_git(root: str, scope: Scope, settings: Optional[Settings] = None, policy: Optional[Policy] = None,
             paths: Sequence[str] = ()) -> Report:
    changes = changes_for(root, scope, paths)
    return analyze(changes, settings, policy, root=root, corpus_rev=scope.corpus_rev, scope=scope.label,
                   subdir=scope.subdir)


def auto_scope(root: str, subdir: Optional[str] = None) -> Tuple[Scope, Optional[str]]:
    """Uncommitted changes if there are any, else the branch vs main, else the last commit."""
    scope = resolve_scope(root, subdir=subdir)
    head = scope.head_commit
    if not head:
        return scope, None
    if subdir:
        unchanged = not gitio.has_diff(root, head, scope.head or head, scope.paths)
    else:
        unchanged = scope.head == scope.head_tree  # the snapshot is HEAD's tree: nothing uncommitted
    if unchanged:
        mb = gitio.default_branch_base(root)
        if mb and mb != head:
            return (resolve_scope(root, branch=True, subdir=subdir),
                    "No uncommitted changes, so checking this branch against main.")
        return resolve_scope(root, last_commit=True, subdir=subdir), "No uncommitted changes, so checking the last commit."
    return scope, None


