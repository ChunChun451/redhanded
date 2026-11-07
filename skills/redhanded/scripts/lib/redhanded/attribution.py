"""Which changed files can be traced to a session's own edits and commands.

A git diff taken since the session started also contains edits made by the user in their editor, or by another agent
working in the same checkout. The Stop hook should not send Claude back to "fix" work it never did, so it only blocks
on files this session can be tied to. When the session ran a command whose effects cannot be bounded (a script, a
`git checkout .`, a formatter run over the tree), every file counts as the session's.
"""

from __future__ import annotations

import os
import posixpath
import re
from typing import List, Optional, Set

from .classify import match_glob, norm_path
from .commands import _exe, classify_tokens, split_segments, strip_heredocs, strip_wrappers, tokens
from .transcript import Session, relpath

# Commands that only read.
SAFE = {
    "ls", "dir", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag", "ack", "fd", "tree",
    "wc", "sort", "uniq", "cut", "tr", "column", "nl", "diff", "cmp", "comm", "stat", "file", "du", "df", "pwd", "cd",
    "pushd", "popd", "echo", "printf", "which", "where", "whereis", "command", "type", "env", "printenv", "export",
    "true", "false", "test", "[", "[[", "sleep", "date", "uname", "whoami", "hostname", "id", "ps", "kill", "pkill",
    "lsof", "netstat", "jq", "yq", "xxd", "od", "hexdump", "md5sum", "sha1sum", "sha256sum", "shasum", "basename",
    "dirname", "realpath", "readlink", "seq", "man", "history", "clear", "cls", "exit", "source", ".", "set", "unset",
    "nproc", "free", "uptime", "watch", "time", "wait", "jobs", "read", "awk", "sed", "find", "curl", "wget", "http",
    "get-content", "gc", "get-childitem", "gci", "select-string", "sls", "get-item", "gi", "test-path",
    "write-output", "write-host", "get-location", "gl", "set-location", "sl", "measure-object", "measure",
    "select-object", "select", "where-object", "where", "sort-object", "group-object", "format-table", "ft",
    "format-list", "fl", "out-string", "out-host", "get-process", "gps", "get-command", "gcm", "resolve-path",
    "split-path", "join-path", "get-date", "start-sleep", "get-filehash", "compare-object", "convertfrom-json",
    "convertto-json", "get-itemproperty", "get-acl", "mkdir", "md", "touch",
}
# git subcommands that leave tracked files alone.
GIT_SAFE = {
    "status", "diff", "log", "show", "branch", "rev-parse", "blame", "ls-files", "ls-tree", "cat-file", "describe",
    "remote", "fetch", "add", "commit", "push", "tag", "shortlog", "grep", "reflog", "config", "rev-list",
    "merge-base", "name-rev", "for-each-ref", "symbolic-ref", "show-ref", "version", "help", "init", "notes",
    "range-diff", "format-patch", "count-objects", "check-ignore", "check-attr", "ls-remote", "rm", "mv", "worktree",
}
# Commands whose path arguments are what they change.
MUTATORS = {
    "rm", "unlink", "del", "erase", "rmdir", "trash", "mv", "move", "cp", "copy", "tee", "truncate", "ln", "chmod",
    "chown", "install", "patch", "unzip", "remove-item", "ri", "set-content", "sc", "add-content", "ac", "out-file",
    "copy-item", "cpi", "move-item", "mi", "rename-item", "ren", "rni", "new-item", "ni", "clear-content", "clc",
}
PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun", "uv", "poetry", "pdm", "pipenv", "cargo", "go", "bundle", "composer",
                    "rye", "hatch", "pixi", "dotnet", "deno", "pip", "pip3"}
PACKAGE_VERBS = {"install", "i", "add", "remove", "rm", "uninstall", "un", "unlink", "link", "update", "up", "upgrade",
                 "pkg", "set-script", "get", "tidy", "require", "lock", "sync", "init", "audit", "mod", "pip"}
PACKAGE_SAFE_VERBS = {"ci", "ls", "list", "view", "info", "outdated", "why", "explain", "whoami", "doctor", "search",
                      "show", "freeze", "env", "version", "help", "config", "cache", "fetch", "tree", "metadata"}
READ_ONLY_MODULES = {"pip", "venv", "json.tool", "site", "ensurepip", "pydoc", "timeit", "platform", "sysconfig",
                     "http.server", "this", "tabnanny", "py_compile", "compileall", "pyclbr"}
FIX_FLAG = re.compile(r"(?:^|\s)--fix(?:-only|=\S+)?(?=\s|$)|(?:^|\s)\S*:fix(?=\s|$)|(?:^|\s)fix(?=\s|$)")
MANIFESTS = {"package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
             "bun.lock", "pyproject.toml", "uv.lock", "poetry.lock", "pdm.lock", "pipfile", "pipfile.lock",
             "cargo.toml", "cargo.lock", "go.mod", "go.sum", "gemfile", "gemfile.lock", "composer.json",
             "composer.lock", "deno.json", "deno.lock", "pixi.toml", "pixi.lock"}
INTERPRETERS = {"python", "python3", "py", "pypy3", "node", "bun", "deno", "ruby", "perl", "php", "pwsh",
                "powershell", "bash", "sh", "zsh", "dash", "fish", "cmd", "osascript", "tsx", "ts-node"}
INLINE_FLAGS = {"-c", "-e", "--eval", "-command", "/c", "-p", "--print", "-r", "-m"}
WRITES_IN_CODE = re.compile(
    r"""open\([^)]*,\s*(?:mode\s*=\s*)?['"][rwxab+t]*[wax]|write_text|write_bytes|writeFile|appendFile|"""
    r"""fs\.(?:rm|unlink|rename|copy|write|cp|truncate)|os\.(?:remove|unlink|rename|replace|rmdir|makedirs)|"""
    r"""shutil\.|\.unlink\(|\.rename\(|\.rmdir\(|\.write\(|Set-Content|Out-File|Remove-Item|Add-Content|"""
    r"""File\.(?:Write|Delete|Move|Copy)|\bsed\s+-i|>\s*[\w./\\-]+""")
SNAPSHOT_UPDATE = re.compile(r"(?:^|\s)(?:-u|--updateSnapshot|--update-snapshots?|--snapshot-update|--force-regen|"
                             r"--accept|--update-goldens?|-update)(?=\s|$|=)")
REDIRECT = re.compile(r"(?:^|[^<>&\d])\d?>{1,2}\s*([^\s;&|<>()]+)")
SAFE_REDIRECT = re.compile(r"^(?:&\d|/dev/null|\$null|nul|null)$", re.I)
GLOB_CHARS = set("*?[")


def _clean(token: str) -> str:
    t = token.strip().strip("'\"`()$")
    t = t.replace("\\", "/")
    while t.startswith("./"):
        t = t[2:]
    return t.rstrip("/")


class Attribution:
    """Answers: could this session have produced the change in `path`?"""

    def __init__(self, session: Session, root: Optional[str]):
        self.root = root
        self.cwd = session.cwd or root
        self.edited: Set[str] = set()
        self.targets: List[str] = []  # paths, directories and globs named by mutating commands
        self.manifests = False
        self.opaque: List[str] = []  # commands whose effects cannot be bounded
        for e in session.edits:
            rel = relpath(e.path, root)
            if rel:
                self.edited.add(rel)
        for _tool, path in session.other_writes:
            self._target(path)
        for _seq, cmd in session.commands:
            self._command(cmd.raw)

    # ------------------------------------------------------------------ reading commands

    def _target(self, token: str) -> None:
        t = _clean(token)
        if not t or t.startswith("-"):
            return
        if t in (".", "*", "**", "/", "~"):
            self.opaque.append(token)  # the whole tree
            return
        if os.path.isabs(t) or re.match(r"^[A-Za-z]:/", t):
            rel = relpath(t, self.root)
            if rel is None:
                return
            t = rel
        while t.startswith("../"):
            t = t[3:]
        if t:
            self.targets.append(norm_path(t))

    def _command(self, raw: str) -> None:
        body, heredocs = strip_heredocs(raw)
        for seg, _op in split_segments(body):
            self._segment(seg, heredocs)

    def _segment(self, seg: str, heredocs: List[str]) -> None:
        for m in REDIRECT.finditer(seg):
            target = m.group(1)
            if not SAFE_REDIRECT.match(target):
                self._target(target)
        toks = tokens(seg)
        core, _env = strip_wrappers(toks)
        if not core:
            return
        exe = _exe(core[0])
        args = core[1:]
        if exe in ("sed", "gsed", "perl") and any(re.match(r"^-[a-zA-Z]*i", a) or a == "--in-place" for a in args):
            for a in args[1:]:
                if not a.startswith("-") and ("/" in a or "." in a or any(c in a for c in GLOB_CHARS)):
                    self._target(a)
            return
        if exe == "find":
            if any(a in ("-delete", "-exec", "-execdir", "-ok") for a in args):
                self.opaque.append(seg)
            return
        if exe in ("curl", "wget"):
            for flag in ("-o", "--output", "-O", "--output-document"):
                if flag in args[:-1] and not (exe == "curl" and flag == "-O"):
                    self._target(args[args.index(flag) + 1])
            return
        if exe in ("xargs", "parallel"):
            self.opaque.append(seg)
            return
        if exe in ("foreach-object", "%"):
            if WRITES_IN_CODE.search(seg):
                self.opaque.append(seg)
            return
        if exe in SAFE:
            return
        if exe == "git":
            i = 0
            while i < len(args) and args[i].startswith("-"):
                i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
            sub, rest = (args[i], args[i + 1:]) if i < len(args) else ("", [])
            if sub in ("rm", "mv"):
                for a in rest:
                    self._target(a)
                return
            if sub in GIT_SAFE:
                return
            if sub in ("checkout", "restore") and "--" in rest:
                for a in rest[rest.index("--") + 1:]:
                    self._target(a)
                return
            if sub in ("checkout", "switch") and any(a in ("-b", "-B", "-c", "-C", "--orphan") for a in rest):
                return
            if sub == "stash" and rest[:1] in (["list"], ["show"]):
                return
            self.opaque.append(seg)
            return
        if exe in MUTATORS:
            for a in args:
                if not a.startswith("-"):
                    self._target(a)
            return
        if exe in INTERPRETERS:
            flags = [a for a in args if a.lower() in INLINE_FLAGS]
            if flags and flags[0] == "-m":
                module = args[args.index("-m") + 1] if args.index("-m") + 1 < len(args) else ""
                if module in READ_ONLY_MODULES:
                    return
                if classify_tokens(core) and not SNAPSHOT_UPDATE.search(" " + " ".join(args)) \
                        and not FIX_FLAG.search(" " + " ".join(args)):
                    return
                self.opaque.append(seg)
                return
            if flags:
                idx = [a.lower() for a in args].index(flags[0].lower())
                code = " ".join(args[idx + 1:])
                if exe in ("bash", "sh", "zsh", "dash", "fish", "cmd", "pwsh", "powershell"):
                    self._command(code)
                elif WRITES_IN_CODE.search(code):
                    self.opaque.append(seg)
                return
            if not [a for a in args if not a.startswith("-")] or args[:1] == ["-"]:
                # `python - <<EOF` or `node <<EOF`: the program is the heredoc
                if any(WRITES_IN_CODE.search(h) for h in heredocs):
                    self.opaque.append(seg)
                return
            self.opaque.append(seg)  # running a script file
            return
        if exe in PACKAGE_MANAGERS:
            verbs = [a for a in args if not a.startswith("-")]
            if verbs[:1] and verbs[0] in PACKAGE_VERBS:
                self.manifests = True
                return
            if verbs[:1] and verbs[0] in PACKAGE_SAFE_VERBS:
                return
        kinds = classify_tokens(core)
        if kinds:
            joined = " " + " ".join(args)
            if SNAPSHOT_UPDATE.search(joined) or FIX_FLAG.search(joined):
                self.opaque.append(seg)
            return
        self.opaque.append(seg)

    # ------------------------------------------------------------------ answering

    def covers(self, path: Optional[str]) -> bool:
        if not path or self.opaque:
            return True
        p = norm_path(path)
        if p in self.edited:
            return True
        base = posixpath.basename(p).lower()
        if self.manifests and base in MANIFESTS:
            return True
        low = p.lower()
        for t in self.targets:
            tl = t.lower()
            if any(c in tl for c in GLOB_CHARS):
                if match_glob(low, tl) or match_glob(base, posixpath.basename(tl)):
                    return True
                continue
            if low == tl or low.startswith(tl + "/") or low.endswith("/" + tl) or ("/" + tl + "/") in ("/" + low):
                return True
            if posixpath.basename(tl) == base and "/" not in tl:
                return True
        return False

    def describe(self) -> str:
        if self.opaque:
            return "any file (the session ran %s)" % self.opaque[0][:80]
        return "%d edited files, %d paths named in commands" % (len(self.edited), len(self.targets))
