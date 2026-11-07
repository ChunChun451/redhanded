"""Small per-session state for hooks: the starting snapshot and what has already been reported."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any, Dict, Optional

MAX_AGE_DAYS = 30


def state_dir() -> str:
    if os.environ.get("REDHANDED_STATE_DIR"):
        return os.environ["REDHANDED_STATE_DIR"]
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        return os.path.join(os.environ["CLAUDE_PLUGIN_DATA"], "sessions")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "redhanded", "sessions")
    xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(xdg, "redhanded", "sessions")


def _path(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:120]
    return os.path.join(state_dir(), safe + ".json")


def load(session_id: str) -> Dict[str, Any]:
    try:
        with open(_path(session_id), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(session_id: str, data: Dict[str, Any]) -> None:
    directory = state_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        data["updated"] = time.time()
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, _path(session_id))
    except OSError:
        pass
    _prune(directory)


def _prune(directory: str) -> None:
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        for name in os.listdir(directory):
            if not name.endswith(".json"):
                continue
            full = os.path.join(directory, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                continue
    except OSError:
        pass


def get_baseline(data: Dict[str, Any], root: str) -> Optional[str]:
    base = data.get("baselines") or {}
    return base.get(os.path.normcase(os.path.abspath(root))) if isinstance(base, dict) else None


def set_baseline(data: Dict[str, Any], root: str, tree: str) -> None:
    data.setdefault("baselines", {})[os.path.normcase(os.path.abspath(root))] = tree
