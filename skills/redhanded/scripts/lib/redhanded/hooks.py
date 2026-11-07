"""Claude Code hook handlers. They read the hook's JSON from stdin and never fail the session (fail-open)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

from . import gitio, state
from . import settings as settings_mod
from .diffs import change_from_texts
from .engine import Policy, analyze
from .model import Finding, Report, Severity
from .transcript import _apply_replace, load_session, relpath

ANNOUNCE = (
    "redhanded is active in this repository. When this session stops, its changes are checked for skipped, deleted "
    "or weakened tests, hard-coded answers for test inputs, production code that detects the test runner, weakened "
    "test/lint/type/CI configuration and bypassed git hooks; the final reply is checked against the commands actually "
    "run in this session (for example, a claim that tests pass needs a passing test run after the last edit). "
    "Intended changes of these kinds are fine when the final reply states them plainly."
)
MAX_LISTED = 8


def read_input() -> Dict[str, Any]:
    try:
        raw = sys.stdin.buffer.read()
    except (OSError, AttributeError):
        raw = b""
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def emit(payload: Optional[Dict[str, Any]]) -> None:
    if payload:
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()


def _cwd(data: Dict[str, Any]) -> str:
    return os.path.abspath(data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _root(data: Dict[str, Any]) -> Optional[str]:
    return gitio.repo_root(_cwd(data))


def _line(f: Finding) -> str:
    where = (" " + f.location) if f.location else ""
    return "[%s] %s%s: %s" % (f.severity.label, f.rule, where, f.message)


# --------------------------------------------------------------------------- SessionStart


def on_session_start(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = _root(data)
    if not root:
        return None
    sid = str(data.get("session_id") or "")
    st = state.load(sid)
    cwd = st.get("cwd") or _cwd(data)
    if not state.get_baseline(st, cwd):
        tree = gitio.snapshot_tree(root, subdir=gitio.subdir_of(root, cwd))
        if tree:
            state.set_baseline(st, cwd, tree)
            st["cwd"] = cwd
            st.setdefault("started", time.time())
            state.save(sid, st)
    cfg = settings_mod.load(root)
    if not cfg.announce:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ANNOUNCE}}


# --------------------------------------------------------------------------- PostToolUse (Edit / Write / MultiEdit)


def _change_from_tool(data: Dict[str, Any], root: str):
    name = data.get("tool_name")
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    resp = data.get("tool_response") if isinstance(data.get("tool_response"), dict) else {}
    path = resp.get("filePath") or inp.get("file_path")
    if not path:
        return None
    rel = relpath(str(path), root)
    if not rel:
        return None
    original = resp.get("originalFile")
    if name == "Write":
        new = resp.get("content") if isinstance(resp.get("content"), str) else inp.get("content")
        old = original if isinstance(original, str) and resp.get("type") != "create" else None
        return change_from_texts(rel, old, new, origin="hook") if isinstance(new, str) else None
    if not isinstance(original, str):
        return None
    if name == "MultiEdit":
        new = original
        for e in inp.get("edits") or []:
            if not isinstance(e, dict) or new is None:
                return None
            new = _apply_replace(new, str(e.get("old_string") or ""), str(e.get("new_string") or ""), bool(e.get("replace_all")))
    else:
        new = _apply_replace(original, str(inp.get("old_string") or resp.get("oldString") or ""),
                             str(inp.get("new_string") if inp.get("new_string") is not None else resp.get("newString") or ""),
                             bool(inp.get("replace_all") or resp.get("replaceAll")))
    if new is None:
        disk = gitio.read_worktree(root, rel)
        new = disk
    if not isinstance(new, str) or new == original:
        return None
    return change_from_texts(rel, original, new, origin="hook")


def on_post_edit(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root = _root(data)
    if not root:
        return None
    cfg = settings_mod.load(root)
    if not cfg.post_edit:
        return None
    change = _change_from_tool(data, root)
    if change is None:
        return None
    sid = str(data.get("session_id") or "")
    st = state.load(sid)
    from .session_check import policy_for

    policy = Policy()
    tp = data.get("transcript_path")
    if tp and os.path.isfile(tp):
        try:
            policy = policy_for(load_session(tp, include_subagents=False))
        except Exception:
            policy = Policy()
    report = analyze([change], cfg, policy, root=root, corpus_rev=None, scope="edit")
    seen = set(st.get("edit_reported") or [])
    fresh = [f for f in report.findings if f.severity >= Severity.HIGH and f.fingerprint not in seen]
    if not fresh:
        return None
    st["edit_reported"] = sorted(seen | {f.fingerprint for f in fresh})[-500:]
    state.save(sid, st)
    lines = ["redhanded flagged the edit just made:"] + ["- " + _line(f) for f in fresh[:MAX_LISTED]]
    lines.append("These findings are reported to the user when the session stops unless the edit is changed back.")
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n".join(lines)}}


# --------------------------------------------------------------------------- Stop


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _wait_for_transcript(path: str, final_text: str, timeout: float = 2.5):
    """Re-read the transcript until it contains the final reply (it is written asynchronously)."""
    deadline = time.time() + timeout
    want = _norm(final_text)[:200]
    session = load_session(path)
    while True:
        have = _norm(session.final_text())
        if not want or (have and (want[:120] in have or have[:120] in want)):
            return session, True
        if time.time() >= deadline:
            return session, False
        time.sleep(0.25)
        session = load_session(path)


def block_reason(findings: List[Finding]) -> str:
    lines = ["redhanded found changes in this session that make checks pass without doing the work:", ""]
    for i, f in enumerate(findings[:MAX_LISTED], 1):
        lines.append("%d. %s" % (i, _line(f)))
        if f.snippet:
            lines.append("   " + f.snippet[:200])
    if len(findings) > MAX_LISTED:
        lines.append("...and %d more." % (len(findings) - MAX_LISTED))
    lines += [
        "",
        "Fix these by changing the implementation, not the tests or the checks, then re-run the checks. If a change is "
        "intended (for example the user asked for it), keep it and say so plainly in your final reply, with the reason. "
        "The same findings will not block again.",
    ]
    return "\n".join(lines)


def user_summary(report: Report, new: List[Finding], blocked: bool) -> str:
    c = report.counts()
    head = "redhanded: %d high, %d medium in this session" % (c["high"], c["medium"])
    foreign = sum(1 for f in new if f.foreign)
    if foreign:
        head += " (%d in files this session did not touch)" % foreign
    if blocked:
        return head + " - sent back to fix: " + "; ".join(_line(f) for f in new if not f.foreign)
    top = [f for f in new if f.severity >= Severity.MEDIUM][:3]
    return head + (" - " + "; ".join(_line(f) for f in top) if top else "")


def on_stop(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = str(data.get("session_id") or "")
    tp = data.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        return None
    st = state.load(sid)
    cwd = st.get("cwd") or _cwd(data)
    root = gitio.repo_root(cwd)
    cfg = settings_mod.load(root) if root else settings_mod.Settings()
    final_text = data.get("last_assistant_message") or ""
    session, caught_up = _wait_for_transcript(tp, final_text)
    session.cwd = cwd
    from .session_check import check_session

    baseline = state.get_baseline(st, cwd) if root else None
    report = check_session(session, root=root or cwd, settings=cfg, baseline=baseline,
                           final_text=final_text or None, only_last_turn=True)
    if not caught_up:
        report.findings = [f for f in report.findings if f.rule not in ("unverified-claim", "contradicted-claim",
                                                                       "unverified-completion")]
    reported = set(st.get("reported") or [])
    new = [f for f in report.findings if f.fingerprint not in reported and f.severity >= Severity.MEDIUM]
    if not new:
        return None
    blocks_used = int(st.get("blocks") or 0)
    # Only send Claude back for changes this session can be tied to, never for someone else's edits.
    to_block = [f for f in new if f.severity >= cfg.block_on and not f.foreign]
    blocked = bool(to_block) and blocks_used < cfg.max_blocks
    st["reported"] = sorted(reported | {f.fingerprint for f in new})[-1000:]
    if blocked:
        st["blocks"] = blocks_used + 1
    state.save(sid, st)
    out: Dict[str, Any] = {"systemMessage": user_summary(report, new, blocked)}
    if blocked:
        out["decision"] = "block"
        out["reason"] = block_reason(to_block)
    return out


HANDLERS = {
    "session-start": on_session_start,
    "SessionStart": on_session_start,
    "post-edit": on_post_edit,
    "PostToolUse": on_post_edit,
    "stop": on_stop,
    "Stop": on_stop,
}


def run(event: str) -> int:
    data = read_input()
    handler = HANDLERS.get(event) or HANDLERS.get(str(data.get("hook_event_name") or ""))
    if handler is None:
        return 0
    try:
        emit(handler(data))
    except Exception as exc:  # never break the user's session
        if os.environ.get("REDHANDED_DEBUG"):
            raise
        sys.stderr.write("redhanded hook error (ignored): %s\n" % exc)
    return 0
