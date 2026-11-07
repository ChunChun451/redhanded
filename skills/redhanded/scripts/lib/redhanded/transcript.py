"""Parse Claude Code session transcripts (JSONL) into prompts, edits, commands and replies."""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .classify import norm_path
from .commands import NOT_RUN, Command, Outcome, exit_code_from_result, outcome, parse_command
from .diffs import change_from_texts, hunks_from_structured_patch
from .model import FileChange, Hunk

SHELL_TOOLS = {"Bash", "PowerShell", "BashTool", "bash", "shell", "local_shell"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_based_edit_tool", "apply_patch"}
SKIP_ORIGINS = {"task_notification", "peer"}
# Other tools (MCP servers, plugins) that look like they write a file at a given path.
WRITE_TOOL = re.compile(r"(?i)write|edit|create|delete|remove|move|rename|patch|replace|insert|save|apply")
NOT_FILE_WRITERS = {"TodoWrite", "TaskCreate", "TaskUpdate", "TaskStop", "EnterPlanMode", "ExitPlanMode",
                    "NotebookRead", "CronCreate", "CronDelete"}
PATH_KEYS = ("file_path", "path", "filePath", "target_file", "destination", "notebook_path", "filename")
TASK_NOTIFICATION = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
META_PREFIXES = ("<local-command-stdout>", "<local-command-stderr>", "<command-message>", "Caveat:",
                 "[Request interrupted", "<task-notification>", "<system-reminder>", "<bash-stdout>", "<bash-stderr>")


@dataclass
class Edit:
    seq: Tuple[str, int]
    path: str
    old: Optional[str]
    new: Optional[str]
    full: bool  # old/new are complete file texts
    hunks: List[Hunk] = field(default_factory=list)
    tool: str = "Edit"
    created: bool = False
    deleted: bool = False
    sidechain: bool = False


@dataclass
class Run:
    seq: Tuple[str, int]
    command: Command
    outcome: Outcome
    source: str = "agent"  # agent | subagent | user
    tool_use_id: str = ""

    @property
    def kinds(self):
        return self.command.kinds


@dataclass
class Turn:
    index: int
    prompt: str
    seq: Tuple[str, int]
    end: Tuple[str, int]
    final_text: str = ""
    texts: List[str] = field(default_factory=list)


@dataclass
class Session:
    id: str
    path: str
    cwd: Optional[str] = None
    branch: Optional[str] = None
    started: Optional[str] = None
    ended: Optional[str] = None
    prompts: List[str] = field(default_factory=list)
    turns: List[Turn] = field(default_factory=list)
    edits: List[Edit] = field(default_factory=list)
    runs: List[Run] = field(default_factory=list)
    commands: List[Tuple[Tuple[str, int], Command]] = field(default_factory=list)
    reads: Dict[str, str] = field(default_factory=dict)
    other_writes: List[Tuple[str, str]] = field(default_factory=list)  # (tool name, path) from non-built-in tools
    subagents: int = 0
    records: int = 0

    def final_text(self) -> str:
        return self.turns[-1].final_text if self.turns else ""


# --------------------------------------------------------------------------- helpers


def _iter_records(path: str) -> Iterator[dict]:
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _apply_replace(original: str, old: str, new: str, replace_all: bool) -> Optional[str]:
    if not old:
        return None
    if old not in original:
        return None
    return original.replace(old, new) if replace_all else original.replace(old, new, 1)


def apply_hunks(text: str, hunks: List[Hunk]) -> Optional[str]:
    """Apply hunks to text by matching their removed/context lines; None when they do not fit."""
    lines = text.splitlines()
    offset = 0
    for h in hunks:
        old_block = [ln.text for ln in h.lines if ln.op in (" ", "-")]
        new_block = [ln.text for ln in h.lines if ln.op in (" ", "+")]
        start = max(0, h.old_start - 1 + offset)
        pos = None
        for cand in [start] + [start + d for r in range(1, 50) for d in (r, -r)]:
            if 0 <= cand <= len(lines) - len(old_block) and lines[cand:cand + len(old_block)] == old_block:
                pos = cand
                break
        if pos is None:
            return None
        lines[pos:pos + len(old_block)] = new_block
        offset += len(new_block) - len(old_block)
    trailing = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + trailing


def _strip_tag(text: str, tag: str) -> Optional[str]:
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), text, re.S)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- parsing


class _Parser:
    def __init__(self, session: Session, sidechain_file: bool = False, order_base: int = 0):
        self.s = session
        self.sidechain_file = sidechain_file
        self.n = order_base
        self.pending: Dict[str, Tuple[str, dict, Tuple[str, int], bool]] = {}
        self.turn: Optional[Turn] = None
        self.after_tool = False
        self.user_cmd: Optional[Tuple[str, Tuple[str, int]]] = None
        self.background: Dict[str, Run] = {}  # background task id or tool_use id -> its run

    def seq(self, record: dict) -> Tuple[str, int]:
        self.n += 1
        return (str(record.get("timestamp") or ""), self.n)

    def _task_notification(self, rec: dict) -> None:
        """A background command finished: record how it went on the run it belongs to."""
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        text = _text_of(msg.get("content") if msg else rec.get("content"))
        for block in TASK_NOTIFICATION.findall(text):
            run = None
            for tag in ("task-id", "tool-use-id"):
                value = _strip_tag(block, tag)
                if value and value.strip() in self.background:
                    run = self.background[value.strip()]
                    break
            if run is None:
                continue
            status = (_strip_tag(block, "status") or "").strip().lower()
            code = re.search(r"exit code (-?\d+)", _strip_tag(block, "summary") or "")
            if code and int(code.group(1)) == 127:  # the runner was missing: nothing ran
                if run in self.s.runs:
                    self.s.runs.remove(run)
            elif code and int(code.group(1)) != 0:
                run.outcome = Outcome("fail", "background run: exit code %s" % code.group(1))
            elif status in ("failed", "killed", "stopped", "error"):
                run.outcome = Outcome("fail", "background run %s" % status)
            elif status == "completed" and code and not run.command.masked:
                run.outcome = Outcome("pass", "background run: exit code 0")

    def feed(self, rec: dict) -> None:
        self.s.records += 1
        t = rec.get("type")
        if self.background and "<task-notification>" in json.dumps(rec.get("message") or rec.get("content") or ""):
            self._task_notification(rec)
        if t not in ("user", "assistant"):
            return
        ts = rec.get("timestamp")
        if ts:
            self.s.started = self.s.started or ts
            self.s.ended = ts
        if not self.s.cwd and rec.get("cwd"):
            self.s.cwd = rec.get("cwd")
        if not self.s.branch and rec.get("gitBranch"):
            self.s.branch = rec.get("gitBranch")
        side = bool(rec.get("isSidechain")) or self.sidechain_file
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        content = msg.get("content")
        if t == "assistant":
            self._assistant(rec, content, side)
        else:
            self._user(rec, content, side)

    def _assistant(self, rec: dict, content, side: bool) -> None:
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text" and not side:
                text = str(block.get("text") or "")
                if self.turn is not None and text.strip():
                    if self.after_tool:
                        self.turn.final_text = ""
                        self.after_tool = False
                    self.turn.final_text = (self.turn.final_text + "\n\n" + text).strip()
                    self.turn.texts.append(text)
                    self.turn.end = self.seq(rec)
            elif bt == "tool_use":
                tid = str(block.get("id") or "")
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                self.pending[tid] = (str(block.get("name") or ""), inp, self.seq(rec), side)
                if not side:
                    self.after_tool = True
                    if self.turn is not None:
                        self.turn.end = self.pending[tid][2]

    def _user(self, rec: dict, content, side: bool) -> None:
        results = []
        if isinstance(content, list):
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            tur = rec.get("toolUseResult")
            for block in results:
                self._tool_result(rec, block, tur if len(results) == 1 else None, side)
            return
        if side or rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
            return
        if rec.get("turnOrigin") in SKIP_ORIGINS:
            return
        text = _text_of(content).strip()
        if not text:
            return
        if "<bash-input>" in text:
            cmd = _strip_tag(text, "bash-input")
            if cmd:
                self.user_cmd = (cmd.strip(), self.seq(rec))
            return
        if "<bash-stdout>" in text or "<bash-stderr>" in text:
            if self.user_cmd:
                out = (_strip_tag(text, "bash-stdout") or "") + "\n" + (_strip_tag(text, "bash-stderr") or "")
                parsed = parse_command(self.user_cmd[0])
                if parsed.is_verification:
                    res = outcome(parsed, out)
                    if res.reason != NOT_RUN:
                        self.s.runs.append(Run(self.user_cmd[1], parsed, res, source="user"))
                self.user_cmd = None
            return
        if text.startswith(META_PREFIXES):
            return
        if "<command-name>" in text:
            args = _strip_tag(text, "command-args") or ""
            name = _strip_tag(text, "command-name") or ""
            text = (name + " " + args).strip()
            if not args.strip():
                return
        seq = self.seq(rec)
        self.s.prompts.append(text)
        self.turn = Turn(index=len(self.s.turns), prompt=text, seq=seq, end=seq)
        self.s.turns.append(self.turn)
        self.after_tool = False

    def _tool_result(self, rec: dict, block: dict, tur, side: bool) -> None:
        tid = str(block.get("tool_use_id") or "")
        name, inp, call_seq, call_side = self.pending.pop(tid, ("", {}, self.seq(rec), side))
        side = side or call_side
        is_error = bool(block.get("is_error"))
        text = _text_of(block.get("content"))
        seq = self.seq(rec)
        if self.turn is not None and not side:
            self.turn.end = seq
        tur = tur if isinstance(tur, dict) else {}
        if name in SHELL_TOOLS:
            command = str(inp.get("command") or inp.get("cmd") or "")
            if isinstance(inp.get("command"), list):
                command = " ".join(str(c) for c in inp["command"])
            if not command:
                return
            parsed = parse_command(command)
            self.s.commands.append((call_seq, parsed))
            if inp.get("run_in_background") or tur.get("backgroundTaskId"):
                # The result arrives later in a <task-notification>; until then the outcome is unknown.
                if parsed.is_verification:
                    run = Run(seq, parsed, Outcome("unknown", "running in the background"),
                              source="subagent" if side else "agent", tool_use_id=tid)
                    self.s.runs.append(run)
                    task = str(tur.get("backgroundTaskId") or "")
                    m = re.search(r"with ID:\s*(\S+)", text)
                    for key in (task, m.group(1) if m else "", tid):
                        if key:
                            self.background[key] = run
            elif parsed.is_verification:
                out = text
                if not out and (tur.get("stdout") or tur.get("stderr")):
                    out = "%s\n%s" % (tur.get("stdout") or "", tur.get("stderr") or "")
                code = exit_code_from_result(text, is_error)
                interrupted = bool(tur.get("interrupted")) or bool(tur.get("timedOutAfterMs"))
                res = outcome(parsed, out, is_error=is_error, exit_code=code, interrupted=interrupted)
                if res.reason != NOT_RUN:
                    self.s.runs.append(Run(seq, parsed, res, source="subagent" if side else "agent", tool_use_id=tid))
            bd = tur.get("bashEditDiff")
            if isinstance(bd, dict):
                for f in bd.get("files") or []:
                    if isinstance(f, dict) and f.get("filePath"):
                        hunks = hunks_from_structured_patch(f.get("hunks") or [])
                        self.s.edits.append(Edit(seq, str(f["filePath"]), None, None, False, hunks, tool=name, sidechain=side))
            return
        if name == "Read" and not is_error:
            f = tur.get("file") if isinstance(tur.get("file"), dict) else {}
            path = f.get("filePath") or inp.get("file_path")
            content = f.get("content")
            if path and isinstance(content, str) and not inp.get("offset") and not inp.get("limit"):
                self.s.reads[str(path)] = content
            return
        if name not in EDIT_TOOLS:
            if not is_error and name not in NOT_FILE_WRITERS and WRITE_TOOL.search(name):
                for key in PATH_KEYS:
                    if isinstance(inp.get(key), str) and inp[key]:
                        self.s.other_writes.append((name, inp[key]))
                        break
            return
        if is_error:
            return
        path = str(tur.get("filePath") or inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "")
        if not path:
            return
        original = tur.get("originalFile")
        if original is None:
            original = tur.get("originalFileContents")
        hunks = hunks_from_structured_patch(tur.get("structuredPatch") or [])
        if name == "Write":
            new = tur.get("content") if isinstance(tur.get("content"), str) else inp.get("content")
            created = tur.get("type") == "create" or original is None
            old = None if created else (original if isinstance(original, str) else None)
            full = isinstance(new, str) and (created or isinstance(old, str))
            self.s.edits.append(Edit(seq, path, old, new if isinstance(new, str) else None, full, hunks, tool=name,
                                     created=created, sidechain=side))
            return
        if name == "MultiEdit":
            new = original if isinstance(original, str) else None
            for e in (inp.get("edits") or tur.get("edits") or []):
                if new is None or not isinstance(e, dict):
                    new = None
                    break
                new = _apply_replace(new, str(e.get("old_string") or ""), str(e.get("new_string") or ""),
                                     bool(e.get("replace_all")))
            full = isinstance(original, str) and isinstance(new, str)
            self.s.edits.append(Edit(seq, path, original if full else None, new if full else None, full, hunks,
                                     tool=name, sidechain=side))
            return
        if name == "NotebookEdit":
            return
        old_s = tur.get("oldString", inp.get("old_string"))
        new_s = tur.get("newString", inp.get("new_string"))
        replace_all = bool(tur.get("replaceAll", inp.get("replace_all")))
        new = None
        if isinstance(original, str) and isinstance(old_s, str) and isinstance(new_s, str):
            new = _apply_replace(original, old_s, new_s, replace_all)
            if new is None and hunks:
                new = apply_hunks(original, hunks)
        full = isinstance(original, str) and isinstance(new, str)
        created = isinstance(original, str) and original == "" and old_s == ""
        self.s.edits.append(Edit(seq, path, original if full else None, new if full else None, full, hunks, tool=name,
                                 created=created, sidechain=side))


def load_session(path: str, include_subagents: bool = True) -> Session:
    sid = os.path.splitext(os.path.basename(path))[0]
    session = Session(id=sid, path=path)
    parser = _Parser(session)
    for rec in _iter_records(path):
        parser.feed(rec)
    if include_subagents:
        sub_dir = os.path.join(os.path.dirname(path), sid, "subagents")
        for sub in sorted(glob.glob(os.path.join(sub_dir, "*.jsonl"))):
            session.subagents += 1
            sub_parser = _Parser(session, sidechain_file=True, order_base=10_000_000 * session.subagents)
            for rec in _iter_records(sub):
                sub_parser.feed(rec)
    session.edits.sort(key=lambda e: e.seq)
    session.runs.sort(key=lambda r: r.seq)
    session.commands.sort(key=lambda c: c[0])
    return session


# --------------------------------------------------------------------------- locating transcripts


def claude_home() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def project_key(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def transcripts_for(cwd: Optional[str] = None) -> List[str]:
    base = os.path.join(claude_home(), "projects")
    if cwd:
        dirs = [os.path.join(base, project_key(os.path.abspath(cwd)))]
    else:
        dirs = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    files: List[str] = []
    for d in dirs:
        files.extend(glob.glob(os.path.join(d, "*.jsonl")))
    files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    return files


def find_session(session_id: str) -> Optional[str]:
    for path in glob.glob(os.path.join(claude_home(), "projects", "*", session_id + ".jsonl")):
        return path
    return None


# --------------------------------------------------------------------------- reconstructing changes


# Not project files even when the session ran from the home folder: the temp folder (where Claude Code keeps its
# scratchpad) and Claude Code's own per-project data (transcripts, memory).
NOT_PROJECT = re.compile(r"(?i)^(?:appdata/local/temp|\.claude/projects)/")


def relpath(path: str, root: Optional[str]) -> Optional[str]:
    """Path relative to root, or None when it lies outside it."""
    p = path.replace("\\", "/")
    if not root:
        return norm_path(p.lstrip("/")) if not os.path.isabs(path) else None
    r = root.replace("\\", "/").rstrip("/")
    rel: Optional[str] = None
    if not os.path.isabs(path):
        rel = norm_path(p)
    elif os.name == "nt" or re.match(r"^[A-Za-z]:", p):
        if p.lower().startswith(r.lower() + "/"):
            rel = norm_path(p[len(r) + 1:])
    elif p.startswith(r + "/"):
        rel = norm_path(p[len(r) + 1:])
    return None if rel is None or NOT_PROJECT.match(rel) else rel


def file_changes(session: Session, root: Optional[str], include_reads_for_deletes: bool = True) -> List[FileChange]:
    """One FileChange per file the agent touched, from its first known state to its last."""
    by_path: Dict[str, List[Edit]] = {}
    order: List[str] = []
    for e in session.edits:
        rel = relpath(e.path, root)
        if rel is None:
            continue
        if rel not in by_path:
            order.append(rel)
            by_path[rel] = []
        by_path[rel].append(e)
    changes: List[FileChange] = []
    for rel in order:
        edits = by_path[rel]
        first = edits[0]
        start: Optional[str] = "" if first.created else first.old
        current: Optional[str] = start
        complete = first.full or first.created
        partial_hunks: List[Hunk] = []
        for e in edits:
            if e.full:
                if current is not None and e.old is not None and e.old != current:
                    pass  # the file changed outside the agent's edits; trust the edit's own view
                current = e.new
            elif current is not None and e.hunks:
                current = apply_hunks(current, e.hunks)
                if current is None:
                    complete = False
            else:
                complete = False
            partial_hunks.extend(e.hunks)
        if complete and current is not None and start is not None:
            old = None if first.created else start
            if old == current:
                continue
            ch = change_from_texts(rel, old, current, origin="transcript")
        else:
            ch = FileChange(path=rel, status="A" if first.created else "M", hunks=partial_hunks, origin="transcript",
                            new_text=current if complete else None)
        changes.append(ch)
    touched = set(by_path)

    def rel_of(target: str) -> Optional[str]:
        return relpath(target if os.path.isabs(target) else os.path.join(session.cwd or root or "", target), root)

    made = set()  # files a shell command created: a scratch test copied in, run and removed is not a deleted test
    for _seq, cmd in session.commands:
        made.update(r for r in (rel_of(w) for w in cmd.writes) if r)
        for target in cmd.deletes:
            rel = rel_of(target)
            if not rel or rel in touched or rel in made or any(ch in rel for ch in "*?["):
                continue
            touched.add(rel)
            old = None
            if include_reads_for_deletes:
                for read_path, content in session.reads.items():
                    if relpath(read_path, root) == rel:
                        old = content
            changes.append(FileChange(path=rel, status="D", old_text=old, origin="transcript"))
    return changes


def edits_between(session: Session, start: Tuple[str, int], end: Tuple[str, int]) -> List[Edit]:
    return [e for e in session.edits if start < e.seq < end]


def iter_sessions(paths: Iterable[str]) -> Iterator[Session]:
    for p in paths:
        yield load_session(p)
