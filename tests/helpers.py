"""Shared test helpers: import path, temporary git repos, and Claude Code transcript builders."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "skills", "redhanded", "scripts", "lib")
SCRIPT = os.path.join(ROOT, "skills", "redhanded", "scripts", "redhanded.py")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

# Keep git from discovering a repository above the temp directory (some machines version their home folder).
os.environ["GIT_CEILING_DIRECTORIES"] = os.path.realpath(tempfile.gettempdir())
os.environ.setdefault("REDHANDED_STATE_DIR", tempfile.mkdtemp(prefix="redhanded-state-"))
# Identity and settings for throwaway repos, without a `git config` call per repo.
os.environ.update({
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_COUNT": "3",
    "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "commit.gpgsign", "GIT_CONFIG_VALUE_1": "false",
    "GIT_CONFIG_KEY_2": "init.defaultBranch", "GIT_CONFIG_VALUE_2": "main",
})

from redhanded.diffs import change_from_texts  # noqa: E402
from redhanded.engine import Policy, analyze  # noqa: E402
from redhanded.model import FileChange, Report  # noqa: E402
from redhanded.settings import Settings  # noqa: E402


def check_texts(files: Dict[str, tuple], policy: Optional[Policy] = None, settings: Optional[Settings] = None) -> Report:
    """files: path -> (old_text or None, new_text or None). Runs every rule without git."""
    changes: List[FileChange] = [change_from_texts(p, old, new, origin="test") for p, (old, new) in files.items()]
    return analyze(changes, settings or Settings(), policy or Policy())


def rules_of(report: Report) -> List[str]:
    return [f.rule for f in report.findings]


class TempRepo:
    """A throwaway git repository."""

    def __init__(self):
        self.path = os.path.realpath(tempfile.mkdtemp(prefix="redhanded-test-"))
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (" ".join(args), proc.stderr))
        return proc.stdout

    def write(self, rel: str, text: str) -> str:
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full) or self.path, exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return full

    def remove(self, rel: str) -> None:
        os.remove(os.path.join(self.path, rel))

    def commit(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, "--allow-empty", "--no-verify")
        return self.git("rev-parse", "HEAD").strip()

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.cleanup()


class TranscriptBuilder:
    """Writes Claude Code style JSONL: prompts, assistant text, tool calls and their results."""

    def __init__(self, cwd: str, session_id: str = "11111111-2222-3333-4444-555555555555"):
        self.cwd = cwd
        self.session_id = session_id
        self.records: List[dict] = []
        self.n = 0

    def _ts(self) -> str:
        self.n += 1
        return "2026-11-07T10:%02d:%02d.000Z" % (self.n // 60, self.n % 60)

    def _base(self, kind: str) -> dict:
        return {"type": kind, "timestamp": self._ts(), "cwd": self.cwd, "sessionId": self.session_id,
                "uuid": "u%d" % self.n, "isSidechain": False}

    def prompt(self, text: str) -> "TranscriptBuilder":
        rec = self._base("user")
        rec.update({"message": {"role": "user", "content": text}, "turnOrigin": "human"})
        self.records.append(rec)
        return self

    def say(self, text: str) -> "TranscriptBuilder":
        rec = self._base("assistant")
        rec["message"] = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        self.records.append(rec)
        return self

    def _call(self, name: str, inp: dict) -> str:
        tid = "toolu_%04d" % (self.n + 1)
        rec = self._base("assistant")
        rec["message"] = {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}
        self.records.append(rec)
        return tid

    def _result(self, tid: str, content: str, is_error: bool = False, tur: Optional[dict] = None) -> None:
        rec = self._base("user")
        rec["message"] = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content,
                                                       "is_error": is_error}]}
        if tur is not None:
            rec["toolUseResult"] = tur
        self.records.append(rec)

    def bash(self, command: str, output: str = "", exit_code: int = 0) -> "TranscriptBuilder":
        tid = self._call("Bash", {"command": command})
        if exit_code:
            content = "Exit code %d\n%s" % (exit_code, output)
        else:
            content = output
        self._result(tid, content, is_error=bool(exit_code),
                     tur={"stdout": output, "stderr": "", "interrupted": False, "isImage": False})
        return self

    def edit(self, rel: str, old_file: str, old: str, new: str) -> "TranscriptBuilder":
        path = os.path.join(self.cwd, rel)
        tid = self._call("Edit", {"file_path": path, "old_string": old, "new_string": new, "replace_all": False})
        self._result(tid, "The file has been updated.", tur={
            "filePath": path, "oldString": old, "newString": new, "originalFile": old_file,
            "structuredPatch": [], "userModified": False, "replaceAll": False})
        return self

    def write(self, rel: str, content: str, original: Optional[str] = None) -> "TranscriptBuilder":
        path = os.path.join(self.cwd, rel)
        tid = self._call("Write", {"file_path": path, "content": content})
        self._result(tid, "File written.", tur={
            "type": "create" if original is None else "update", "filePath": path, "content": content,
            "structuredPatch": [], "originalFile": original})
        return self

    def save(self, directory: str) -> str:
        path = os.path.join(directory, self.session_id + ".jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(json.dumps(rec) + "\n")
        return path


def run_cli(*args: str, cwd: Optional[str] = None, stdin: Optional[str] = None, env: Optional[dict] = None):
    full_env = dict(os.environ)
    for name in ("GITHUB_ACTIONS", "GITHUB_EVENT_PATH", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "CLAUDE_PROJECT_DIR",
                 "CLAUDE_PLUGIN_DATA", "CLAUDE_CONFIG_DIR", "REDHANDED_DEBUG"):
        full_env.pop(name, None)
    full_env["NO_COLOR"] = "1"
    full_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd, input=stdin, capture_output=True, text=True,
                          encoding="utf-8", env=full_env)
