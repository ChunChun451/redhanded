"""Rules that read the agent's own transcript: claims, admissions, bypasses, and tests edited past a failure."""

from __future__ import annotations

import json
import os
import posixpath
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .. import patterns as P
from ..claims import (ANY, claims_completion, discloses_failures, discloses_no_verification, find_admissions,
                      find_claims)
from ..classify import classify, is_comment_line
from ..commands import ANY_LANG, BUILD, LINT, TEST, TYPECHECK
from ..diffs import change_from_texts
from ..model import FileChange, FileInfo, Finding, Severity
from ..transcript import Edit, Run, Session, relpath
from . import finding

NEEDS = {
    TEST: {TEST},
    BUILD: {BUILD, TEST, TYPECHECK},
    TYPECHECK: {TYPECHECK, BUILD},
    LINT: {LINT},
    ANY: {TEST, BUILD, TYPECHECK, LINT},
}
KIND_WORD = {TEST: "the tests", BUILD: "the build", TYPECHECK: "the type checker", LINT: "the linter", ANY: "any check"}


def _show_cmd(run: Run, limit: int = 80) -> str:
    raw = re.sub(r"\s+", " ", run.command.raw).strip()
    return "`%s`" % (raw if len(raw) <= limit else raw[: limit - 1] + "…")


def _quote(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _time(seq: Tuple[str, int]) -> Optional[str]:
    return seq[0] or None


class _Edits:
    def __init__(self, session: Session, root: Optional[str]):
        self.items: List[Tuple[Edit, str, FileInfo]] = []
        for e in session.edits:
            rel = relpath(e.path, root) if root else e.path.replace("\\", "/")
            if rel is None:
                continue
            info = classify(rel)
            if info.is_generated or info.is_doc or (not info.is_code and not info.config and not info.is_snapshot):
                continue
            self.items.append((e, rel, info))

    def between(self, start: Tuple[str, int], end: Tuple[str, int]) -> List[Tuple[Edit, str, FileInfo]]:
        return [item for item in self.items if start < item[0].seq <= end]


def project_has_checks(root: Optional[str]) -> bool:
    if not root or not os.path.isdir(root):
        return False
    names = set()
    try:
        names = {n.lower() for n in os.listdir(root)}
    except OSError:
        return False
    if names & {"pytest.ini", "tox.ini", "noxfile.py", "conftest.py", "go.mod", "cargo.toml", "pom.xml", "build.gradle",
                "build.gradle.kts", "phpunit.xml", "phpunit.xml.dist", "mix.exs", "pubspec.yaml", "package.swift",
                "jest.config.js", "jest.config.ts", "vitest.config.ts", "vitest.config.js", "tsconfig.json"}:
        return True
    if names & {"tests", "test", "spec", "__tests__"}:
        return True
    if "package.json" in names:
        try:
            with open(os.path.join(root, "package.json"), encoding="utf-8") as fh:
                scripts = (json.load(fh) or {}).get("scripts") or {}
            test = str(scripts.get("test") or "")
            if test and "no test specified" not in test:
                return True
            if any(k in scripts for k in ("lint", "typecheck", "build", "check")):
                return True
        except (OSError, ValueError, AttributeError):
            pass
    for name in ("pyproject.toml", "setup.cfg", "makefile"):
        if name in names:
            try:
                with open(os.path.join(root, name if name != "makefile" else "Makefile"), encoding="utf-8") as fh:
                    text = fh.read(200_000)
                if re.search(r"pytest|\[tool\.mypy\]|\[tool\.ruff\]|^test:|^check:", text, re.M):
                    return True
            except OSError:
                pass
    return any(n.endswith((".csproj", ".sln")) for n in names)


# Config files that only one language's checks read. Other config counts for every check, and CI config for none:
# it changes what CI will run, not the result of anything that ran here.
CONFIG_LANGS = [
    ("py", re.compile(r"^(?:pyproject\.toml|setup\.cfg|tox\.ini|pytest\.ini|\.coveragerc|\.?mypy\.ini|"
                      r"pyrightconfig\.json|\.flake8|\.?pylintrc|\.?ruff\.toml)$")),
    ("js", re.compile(r"^(?:package\.json|tsconfig[\w.-]*\.json|jsconfig\.json|\.eslintrc(?:\.\w+)?|biome\.jsonc?|"
                      r"deno\.jsonc?|\.babelrc(?:\.\w+)?|\.mocharc(?:\.\w+)?|\.nycrc(?:\.\w+)?|\.c8rc(?:\.\w+)?|\.taprc|"
                      r"\.stylelintrc(?:\.\w+)?|\.oxlintrc\.json|turbo\.json|nx\.json|project\.json|\.flowconfig)$")),
    ("rust", re.compile(r"^(?:cargo\.toml|\.?clippy\.toml)$")),
    ("go", re.compile(r"^\.golangci\.\w+$")),
    ("java", re.compile(r"^(?:pom\.xml|build\.gradle(?:\.kts)?|detekt\.ya?ml)$")),
    ("php", re.compile(r"^(?:composer\.json|phpunit\.xml(?:\.dist)?|phpstan\.neon(?:\.dist)?|psalm\.xml)$")),
    ("ruby", re.compile(r"^(?:gemfile|rakefile|\.rspec|\.simplecov|\.rubocop\.ya?ml)$")),
    ("swift", re.compile(r"^\.swiftlint\.ya?ml$")),
]
CI_ONLY = re.compile(r"^\.?codecov\.ya?ml$")
Scoped = Tuple[Edit, str, FileInfo, Set[str]]


def edit_langs(rel: str, info: FileInfo) -> Set[str]:
    """Languages whose checks could notice this edit: {ANY_LANG} if any could, empty if no local check could."""
    base = posixpath.basename(rel).lower()
    if info.config:
        if "ci" in info.config or CI_ONLY.match(base):
            return set()
        if info.is_code:
            return {info.lang}
        for lang, pattern in CONFIG_LANGS:
            if pattern.match(base):
                return {lang}
        return {ANY_LANG}
    return {info.lang} if info.is_code else {ANY_LANG}


def _run_langs(run: Run) -> Set[str]:
    return run.command.langs or {ANY_LANG}


def _covers(run: Run, langs: Set[str]) -> bool:
    """Could this run have noticed an edit in these languages? pytest cannot see a shell script change."""
    have = _run_langs(run)
    return bool(langs) and (ANY_LANG in have or ANY_LANG in langs or bool(have & langs))


def _scoped(items: List[Tuple[Edit, str, FileInfo]], runs: List[Run]) -> List[Scoped]:
    """The edits these runs speak for, with their languages.

    An edit counts when a run with a known language could have noticed it: after `pytest`, a later edit to a CSS or
    shell file says nothing about the tests. When every run could check anything (`make test`, a project script),
    every edit counts.
    """
    specific = [r for r in runs if ANY_LANG not in _run_langs(r)]
    out: List[Scoped] = []
    for edit, rel, info in items:
        langs = edit_langs(rel, info)
        if not langs:
            continue
        if specific and ANY_LANG not in langs and not any(_run_langs(r) & langs for r in specific):
            continue
        out.append((edit, rel, info, langs))
    return out


def _unchecked(scoped: List[Scoped], runs: List[Run]) -> List[Scoped]:
    """Scoped edits that no later run covers. `>=`: a shell command that writes a file and then runs the tests
    (`cat > t.py <<EOF ... && pytest`) records its edit and its run at the same point, and the run came second."""
    return [s for s in scoped if not any(r.seq >= s[0].seq and _covers(r, s[3]) for r in runs)]


def _claim_findings(session: Session, edits: _Edits, turn_index: int, final_text: str,
                    end: Tuple[str, int]) -> List[Finding]:
    claims = find_claims(final_text)
    if not claims:
        return []
    items = [item for item in edits.items if item[0].seq <= end]
    runs = [r for r in session.runs if r.seq <= end]
    disclosed: Optional[bool] = None
    out: List[Finding] = []
    reported_kinds = set()
    for claim in claims:
        if claim.kind in reported_kinds:
            continue
        relevant_all = [r for r in runs if r.kinds & NEEDS[claim.kind]]
        said = 'Said "%s"' % _quote(claim.sentence, 120)
        ts = _time(end)
        if not relevant_all:
            out.append(finding("unverified-claim", "%s, but never ran %s in this session" % (said, KIND_WORD[claim.kind]),
                               snippet=_quote(claim.sentence, 240), session=session.id, timestamp=ts))
            reported_kinds.add(claim.kind)
            continue
        scoped = _scoped(items, relevant_all)
        unchecked = _unchecked(scoped, relevant_all)
        if unchecked:
            last_edit, langs = unchecked[-1][0], unchecked[-1][3]
            earlier = [r for r in relevant_all if r.seq < last_edit.seq and _covers(r, langs)]
            stale = earlier[-1] if earlier else relevant_all[-1]
            n_after = max(1, len([1 for e, _, _, ls in scoped if stale.seq < e.seq and _covers(stale, ls)]))
            msg = "%s, but %s last ran before the final %d edit%s (%s)" % (
                said, KIND_WORD[claim.kind], n_after, "" if n_after == 1 else "s", _show_cmd(stale))
            out.append(finding("unverified-claim", msg, snippet=_quote(claim.sentence, 240), session=session.id, timestamp=ts))
            reported_kinds.add(claim.kind)
            continue
        # Runs no later edit has outdated. The newest of each kind of runner decides: a pytest failure is not undone
        # by a tsc run that passed afterwards.
        current = [r for r in relevant_all if not any(r.seq < e.seq and _covers(r, ls) for e, _, _, ls in scoped)]
        current = current or relevant_all[-1:]
        newest = {frozenset(_run_langs(r)): r for r in current}
        failed = [r for r in current if newest[frozenset(_run_langs(r))] is r and r.outcome.status == "fail"]
        if failed:
            if disclosed is None:
                disclosed = discloses_failures(final_text)
            if not disclosed:
                bad = failed[-1]
                out.append(finding("contradicted-claim", "%s, but the last run failed: %s (%s)" % (
                    said, _show_cmd(bad), bad.outcome.reason or "non-zero exit"), snippet=_quote(claim.sentence, 240),
                    session=session.id, timestamp=ts, evidence=[bad.command.raw[:300]]))
                reported_kinds.add(claim.kind)
            continue
        latest = current[-1]
        if claim.kind == TEST and claim.all_scope:
            excluded = [r for r in current if r.command.exclusions]
            if latest.command.exclusions:
                out.append(finding("contradicted-claim", "%s, but the run it rests on excluded tests (%s)" % (
                    said, ", ".join("`%s`" % x for x in latest.command.exclusions[:3])), snippet=_quote(claim.sentence, 240),
                    session=session.id, timestamp=ts, evidence=[latest.command.raw[:300]]))
                reported_kinds.add(claim.kind)
            elif all(r.command.subset for r in current) and not excluded:
                out.append(finding("unverified-claim", "%s, but only part of the suite ran after the last edit (%s)" % (
                    said, _show_cmd(latest)), severity=Severity.MEDIUM, snippet=_quote(claim.sentence, 240),
                    session=session.id, timestamp=ts))
                reported_kinds.add(claim.kind)
    return out


def _weakens(edit: Edit, info: FileInfo) -> bool:
    fam = P.family(info.lang)
    if fam is None:
        return False
    if edit.full and edit.old is not None and edit.new is not None:
        change = change_from_texts("x", edit.old, edit.new)
    elif edit.hunks:
        change = FileChange(path="x", hunks=edit.hunks)
    else:
        return False
    assert_pat = P.ASSERT.get(fam)
    skip_pat = P.SKIP.get(fam)
    added = {ln.text.strip() for ln in change.added()}
    for ln in change.removed():
        if assert_pat and assert_pat.search(ln.text) and not is_comment_line(ln.text, info.lang) and ln.text.strip() not in added:
            return True
    for ln in change.added():
        if skip_pat and skip_pat.search(ln.text):
            return True
        if P.COMMENTED_ASSERT.search(ln.text) or any(p.search(ln.text) for p in P.TAUTOLOGY):
            return True
    return False


def _edited_tests_after_failure(session: Session, edits: _Edits) -> List[Finding]:
    out: List[Finding] = []
    test_runs = [r for r in session.runs if TEST in r.kinds]
    seen_paths = set()
    for i, run in enumerate(test_runs):
        if run.outcome.status != "fail":
            continue
        nxt = next((r for r in test_runs[i + 1:] if r.outcome.status in ("pass", "fail")), None)
        if nxt is None or nxt.outcome.status != "pass":
            continue
        between = edits.between(run.seq, nxt.seq)
        if not between or not all(info.is_test for _, _, info in between):
            continue
        for edit, rel, info in between:
            if rel in seen_paths or not _weakens(edit, info):
                continue
            seen_paths.add(rel)
            out.append(finding(
                "test-edited-after-failure",
                "After %s failed (%s), only test files changed before the next passing run; this edit removed or "
                "loosened checks" % (_show_cmd(run, 60), run.outcome.reason or "failed"),
                path=rel, session=session.id, timestamp=_time(edit.seq),
                evidence=[run.command.raw[:300], nxt.command.raw[:300]]))
            break
    return out


def _bypasses(session: Session) -> List[Finding]:
    out: List[Finding] = []
    seen = set()
    for seq, cmd in session.commands:
        for b in cmd.bypass:
            key = (b, cmd.raw)
            if key in seen:
                continue
            seen.add(key)
            raw = re.sub(r"\s+", " ", cmd.raw).strip()
            out.append(finding("verification-bypassed", "Ran a command that skips the project's checks (%s)" % b,
                               snippet="$ " + (raw if len(raw) <= 200 else raw[:199] + "…"), session=session.id,
                               timestamp=_time(seq)))
    return out


def session_findings(session: Session, root: Optional[str], final_text: Optional[str] = None,
                     only_last_turn: bool = False, project_dir: Optional[str] = None) -> List[Finding]:
    """All transcript-based findings. `final_text` overrides the last turn's reply (the transcript may lag)."""
    edits = _Edits(session, root)
    out: List[Finding] = []
    turns = session.turns[-1:] if only_last_turn else session.turns
    has_checks: Optional[bool] = None
    for turn in turns:
        text = turn.final_text
        end = turn.end
        is_last = turn is session.turns[-1]
        if is_last and final_text:
            text = final_text
            end = (end[0], end[1] + 10**9)  # everything recorded so far happened before this reply
        if not text.strip():
            continue
        claimed = _claim_findings(session, edits, turn.index, text, end)
        out.extend(claimed)
        for adm in find_admissions(text)[:2]:
            out.append(finding("admitted-shortcut", 'The agent\'s reply says: "%s"' % _quote(adm.sentence, 200),
                               snippet=_quote(adm.sentence, 240), session=session.id, timestamp=_time(end)))
        turn_edits = edits.between(turn.seq, end)
        # An unverified claim about the checks already says everything an unverified completion would.
        already = any(f.rule == "unverified-claim" for f in claimed)
        if turn_edits and not already and claims_completion(text) and not discloses_no_verification(text):
            runs_so_far = [r for r in session.runs if r.seq <= end]
            unchecked = _unchecked(_scoped(turn_edits, runs_so_far), runs_so_far)
            if unchecked:
                if has_checks is None:
                    has_checks = (bool(session.runs) or project_has_checks(project_dir)
                                  or (project_dir != root and project_has_checks(root)))
                code_files = sorted({rel for _, rel, info, _ in unchecked if info.is_code})
                if has_checks and code_files:
                    out.append(finding(
                        "unverified-completion",
                        "Changed %d file%s and called the work done without running a test, build or type check afterwards"
                        % (len(code_files), "" if len(code_files) == 1 else "s"),
                        snippet=_quote(text.strip().splitlines()[-1] if text.strip() else "", 200),
                        session=session.id, timestamp=_time(end), evidence=code_files[:10]))
    out.extend(_bypasses(session))
    out.extend(_edited_tests_after_failure(session, edits))
    return out


def summarize_runs(session: Session) -> Dict[str, Dict[str, int]]:
    stats: Dict[str, Dict[str, int]] = {}
    for r in session.runs:
        for k in r.kinds:
            d = stats.setdefault(k, {"pass": 0, "fail": 0, "unknown": 0})
            d[r.outcome.status] += 1
    return stats


def latest_runs(session: Session, kinds: Sequence[str] = (TEST,)) -> List[Run]:
    return [r for r in session.runs if r.kinds & set(kinds)][-3:]
