"""Thin, defensive wrappers around the git CLI."""

from __future__ import annotations

import os
import subprocess
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .classify import norm_path
from .diffs import parse_unified_diff
from .model import FileChange

MAX_FILE_BYTES = 1_000_000
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(RuntimeError):
    pass


def _env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.pop("GIT_EXTERNAL_DIFF", None)
    if extra:
        env.update(extra)
    return env


def git(args: Sequence[str], cwd: str, timeout: float = 30.0, input_bytes: Optional[bytes] = None,
        check: bool = True, env: Optional[Dict[str, str]] = None) -> bytes:
    cmd = ["git", "-c", "core.quotepath=false", "-c", "diff.noprefix=false", "-c", "color.ui=never"] + list(args)
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=_env(env),
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise GitError("git %s timed out" % " ".join(args[:2]))
    if check and proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(msg or "git %s failed" % " ".join(args[:2]))
    return proc.stdout


def text(args: Sequence[str], cwd: str, **kw) -> str:
    return git(args, cwd, **kw).decode("utf-8", "replace")


_GIT_DIRS: Dict[str, str] = {}
_TRACKED: Dict[Tuple[str, Optional[str]], Tuple[Tuple[int, int], List[str]]] = {}


def repo_root(cwd: str) -> Optional[str]:
    try:
        lines = text(["rev-parse", "--show-toplevel", "--absolute-git-dir"], cwd, timeout=10).splitlines()
    except (GitError, OSError):
        return None
    root = lines[0].strip() if lines else ""
    if not root:
        return None
    if len(lines) > 1 and lines[1].strip():
        _GIT_DIRS[root] = lines[1].strip()
    return root


def git_dir(root: str) -> Optional[str]:
    if root not in _GIT_DIRS:
        try:
            _GIT_DIRS[root] = text(["rev-parse", "--absolute-git-dir"], root, timeout=10).strip()
        except GitError:
            return None
    return _GIT_DIRS[root]


def _index_stamp(root: str) -> Tuple[int, int]:
    d = git_dir(root)
    try:
        st = os.stat(os.path.join(d, "index")) if d else None
    except OSError:
        st = None
    return (st.st_mtime_ns, st.st_size) if st else (0, 0)


def has_commits(root: str) -> bool:
    return head_info(root)[0] is not None


def head_info(root: str) -> Tuple[Optional[str], Optional[str]]:
    """(HEAD commit, HEAD tree), or (None, None) before the first commit. One git call."""
    try:
        lines = text(["rev-parse", "HEAD^{commit}", "HEAD^{tree}"], root, timeout=10).split()
    except GitError:
        return None, None
    return (lines[0], lines[1]) if len(lines) == 2 else (None, None)


def rev_exists(root: str, rev: str) -> bool:
    try:
        git(["cat-file", "-e", rev + "^{commit}"], root, timeout=10)
        return True
    except GitError:
        return False


def resolve(root: str, rev: str) -> Optional[str]:
    try:
        return text(["rev-parse", "--verify", "--quiet", rev + "^{commit}"], root, timeout=10).strip() or None
    except GitError:
        return None


def merge_base(root: str, a: str, b: str) -> Optional[str]:
    try:
        return text(["merge-base", a, b], root, timeout=20).strip() or None
    except GitError:
        return None


MAIN_REFS = ("refs/remotes/origin/HEAD", "refs/remotes/origin/main", "refs/remotes/origin/master", "refs/heads/main",
             "refs/heads/master")


def default_branch_base(root: str) -> Optional[str]:
    """Best-effort merge-base with the repository's main branch."""
    try:
        existing = set(text(["for-each-ref", "--format=%(refname)"] + list(MAIN_REFS), root, timeout=10).split())
    except GitError:
        return None
    for ref in MAIN_REFS:
        if ref in existing:
            base = merge_base(root, "HEAD", ref)
            if base:
                return base
    return None


def stash_snapshot(root: str) -> Optional[str]:
    """A commit object for the current working tree (tracked files) without touching it."""
    try:
        sha = text(["stash", "create"], root, timeout=30).strip()
    except GitError:
        sha = ""
    if sha:
        return sha
    return resolve(root, "HEAD")


MAX_UNTRACKED_BYTES = 256_000
MAX_UNTRACKED_FILES = 2000
MAX_UNTRACKED_TOTAL = 40_000_000


def subdir_of(root: str, cwd: Optional[str]) -> Optional[str]:
    """cwd relative to root when it is a proper subdirectory, else None."""
    if not cwd:
        return None
    try:
        rel = os.path.relpath(os.path.abspath(cwd), os.path.abspath(root))
    except ValueError:
        return None
    rel = norm_path(rel)
    if rel in (".", "") or rel.startswith(".."):
        return None
    return rel


MAX_EXCLUDES = 5000


def snapshot_tree(root: str, timeout: float = 90.0, subdir: Optional[str] = None) -> Optional[str]:
    """Tree object for the working tree: tracked files plus small untracked ones (ignored files excluded).

    Uses a throwaway index seeded from the real one, so the user's index and working tree are never touched.
    With `subdir`, only that part of the tree is refreshed (the rest stays as in the index).
    """
    gdir = git_dir(root)
    if not gdir:
        return None
    import shutil
    import tempfile

    fd, tmp_index = tempfile.mkstemp(prefix="redhanded-index-")
    os.close(fd)
    try:
        try:
            # copy2 keeps the index's mtime, which git needs to spot "racily clean" entries (files modified within
            # the same second the index was written). A fresh mtime would make such edits invisible.
            shutil.copy2(os.path.join(gdir, "index"), tmp_index)
        except OSError:
            os.remove(tmp_index)  # no index yet: git starts from an empty one
        env = {"GIT_INDEX_FILE": tmp_index}
        untracked = listing(root, subdir)[1]  # also caches the tracked list for the test corpus
        small: List[str] = []
        skip: List[str] = []
        total = 0
        for path in untracked:
            try:
                size = os.path.getsize(os.path.join(root, path))
            except OSError:
                continue
            if size > MAX_UNTRACKED_BYTES or len(small) >= MAX_UNTRACKED_FILES or total + size > MAX_UNTRACKED_TOTAL:
                skip.append(path)
                continue
            total += size
            small.append(path)
        if len(skip) <= MAX_EXCLUDES:
            # One pass: stage everything under the scope except the untracked files that are too big.
            specs = [":(literal)" + (subdir or ".")] + [":(exclude,literal)" + p for p in skip]
            git(["add", "-A", "--ignore-errors", "--pathspec-from-file=-", "--pathspec-file-nul"], root,
                timeout=timeout, env=env, check=False, input_bytes="\0".join(specs).encode("utf-8"))
        else:
            lit = dict(env, GIT_LITERAL_PATHSPECS="1")
            git(["add", "-u", "--ignore-errors", "--", subdir or "."], root, timeout=timeout, env=lit, check=False)
            if small:
                git(["add", "--ignore-errors", "--pathspec-from-file=-", "--pathspec-file-nul"], root,
                    timeout=timeout, env=lit, check=False, input_bytes="\0".join(small).encode("utf-8"))
        tree = text(["write-tree"], root, timeout=30, env=env).strip()
        return tree or None
    except GitError:
        return None
    finally:
        for path in (tmp_index, tmp_index + ".lock"):
            try:
                os.remove(path)
            except OSError:
                pass


def head_or_empty(root: str) -> str:
    """HEAD's commit, or the empty tree in a repository without commits."""
    return head_info(root)[0] or EMPTY_TREE


def listing(root: str, subdir: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """(tracked, untracked) paths in one `git ls-files` call. Untracked excludes ignored files."""
    args = ["ls-files", "-z", "-t", "--cached", "--others", "--exclude-standard"] + (["--", subdir] if subdir else [])
    stamp = _index_stamp(root)
    try:
        out = git(args, root, timeout=60, env={"GIT_LITERAL_PATHSPECS": "1"})
    except GitError:
        return [], []
    tracked: List[str] = []
    untracked: List[str] = []
    seen = set()
    for item in out.decode("utf-8", "replace").split("\0"):
        if len(item) < 3 or item[1] != " ":
            continue
        path = norm_path(item[2:])
        if item[0] == "?":
            untracked.append(path)
        elif path not in seen:  # unmerged files are listed once per stage
            seen.add(path)
            tracked.append(path)
    _TRACKED[(root, subdir)] = (stamp, tracked)
    return tracked, untracked


def untracked_files(root: str, limit: int = 5000, subdir: Optional[str] = None) -> List[str]:
    return listing(root, subdir)[1][:limit]


def tracked_files(root: str, subdir: Optional[str] = None) -> List[str]:
    """Tracked paths; reuses the listing taken by the last snapshot while the index is unchanged."""
    hit = _TRACKED.get((root, subdir))
    if hit and hit[0] != (0, 0) and hit[0] == _index_stamp(root):
        return list(hit[1])
    args = ["ls-files", "-z"] + (["--", subdir] if subdir else [])
    try:
        out = git(args, root, timeout=30, env={"GIT_LITERAL_PATHSPECS": "1"})
    except GitError:
        return []
    paths = list(dict.fromkeys(norm_path(p) for p in out.decode("utf-8", "replace").split("\0") if p))
    _TRACKED[(root, subdir)] = (_index_stamp(root), paths)
    return paths


def has_diff(root: str, a: str, b: str, paths: Sequence[str] = ()) -> bool:
    try:
        proc = subprocess.run(
            ["git", "diff", "--quiet", "--no-ext-diff", a, b, "--"] + (list(paths) or ["."]),
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, env=_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return proc.returncode == 1


def read_worktree(root: str, path: str) -> Optional[str]:
    full = os.path.join(root, path)
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return None
        with open(full, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", "replace")


def read_blobs(root: str, specs: Iterable[str]) -> Dict[str, Optional[str]]:
    """Fetch many "<rev>:<path>" blobs with one `git cat-file --batch` process."""
    specs = [s for s in dict.fromkeys(specs) if s]
    result: Dict[str, Optional[str]] = {s: None for s in specs}
    if not specs:
        return result
    payload = "".join(s + "\n" for s in specs).encode("utf-8")
    try:
        out = git(["cat-file", "--batch"], root, input_bytes=payload, timeout=60)
    except GitError:
        return result
    pos = 0
    for spec in specs:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        header = out[pos:nl].decode("utf-8", "replace")
        pos = nl + 1
        if header.endswith((" missing", " ambiguous")):
            continue
        parts = header.split()
        if len(parts) != 3 or not parts[2].isdigit():
            break  # unexpected output; stop rather than mis-assign blobs
        size = int(parts[2])
        data = out[pos:pos + size]
        pos += size + 1
        if parts[1] == "blob" and size <= MAX_FILE_BYTES and b"\0" not in data[:8000]:
            result[spec] = data.decode("utf-8", "replace")
    return result


def diff(root: str, base: Optional[str], head: Optional[str] = None, staged: bool = False,
         paths: Sequence[str] = ()) -> List[FileChange]:
    """Changes from base to head (or to the working tree / index when head is None)."""
    args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", "-U3"]
    if staged:
        args.append("--cached")
    if base:
        args.append(base)
    if head:
        args.append(head)
    args.append("--")
    if paths:
        args.extend(paths)
    else:
        args.append(".")
        args.extend(":(exclude,glob)**/%s" % name for name in _NOISY_FILES)
    patch = text(args, root, timeout=120)
    return parse_unified_diff(patch)


_NOISY_FILES = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "Cargo.lock", "poetry.lock", "uv.lock",
    "go.sum", "Gemfile.lock", "composer.lock", "Pipfile.lock", "pdm.lock", "*.min.js", "*.min.css", "*.map",
)


def load_contents(root: str, changes: List[FileChange], base: Optional[str], head: Optional[str],
                  staged: bool = False, want=None) -> None:
    """Attach old/new full text to changes (only for files where `want(change)` is true)."""
    old_specs: Dict[str, str] = {}
    new_specs: Dict[str, str] = {}
    for ch in changes:
        if ch.binary or (want is not None and not want(ch)):
            continue
        if ch.status != "A":
            old_rev = base or "HEAD"
            old_specs[ch.path] = "%s:%s" % (old_rev, ch.old_path or ch.path)
        if ch.status != "D":
            if head:
                new_specs[ch.path] = "%s:%s" % (head, ch.path)
            elif staged:
                new_specs[ch.path] = ":%s" % ch.path
    blobs = read_blobs(root, list(old_specs.values()) + list(new_specs.values()))
    for ch in changes:
        if ch.path in old_specs:
            ch.old_text = blobs.get(old_specs[ch.path])
        if ch.status == "D":
            continue
        if ch.path in new_specs:
            ch.new_text = blobs.get(new_specs[ch.path])
        elif not head and not staged and (want is None or want(ch)):
            ch.new_text = read_worktree(root, ch.path)


def changed_paths_between(root: str, base: str, head: Optional[str] = None) -> List[Tuple[str, str]]:
    args = ["diff", "--name-status", "-z", "-M", base]
    if head:
        args.append(head)
    out = git(args, root, timeout=60).decode("utf-8", "replace").split("\0")
    result: List[Tuple[str, str]] = []
    i = 0
    while i < len(out) and out[i]:
        status = out[i]
        if status.startswith(("R", "C")):
            result.append((status[0], norm_path(out[i + 2])))
            i += 3
        else:
            result.append((status[0], norm_path(out[i + 1])))
            i += 2
    return result
