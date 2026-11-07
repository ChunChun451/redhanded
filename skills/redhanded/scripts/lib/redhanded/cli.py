"""Command-line interface."""

from __future__ import annotations

import argparse
import io
import os
import sys
from typing import List, Optional

from . import __version__, gitio
from . import settings as settings_mod
from .model import Report, Severity
from .report import FORMATS, render
from .term import style_for

COMMANDS = ("check", "session", "audit", "demo", "hook", "init", "rules", "explain", "ci", "version")
EXIT_OK, EXIT_FINDINGS, EXIT_ERROR = 0, 1, 2


def _severity(value: str) -> Severity:
    try:
        return Severity.parse(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _add_output(p: argparse.ArgumentParser, formats=FORMATS) -> None:
    p.add_argument("-f", "--format", choices=formats, default="text", help="output format (default: text)")
    p.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE instead of stdout")
    p.add_argument("--fail-on", type=_severity, metavar="SEVERITY",
                   help="exit 1 when a finding is at least this severe: low, medium, high (default: high) or off")
    p.add_argument("--no-color", action="store_true", help="disable colours")
    p.add_argument("-v", "--verbose", action="store_true", help="show evidence and details")
    p.add_argument("-q", "--quiet", action="store_true", help="hide low-severity findings in text output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redhanded",
        description="Catch AI coding agents gaming your tests: skipped or deleted tests, weakened assertions, "
                    "hard-coded answers, neutered CI, and 'all tests pass' claims their own transcript contradicts.",
    )
    parser.add_argument("--version", action="version", version="redhanded " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("check", help="check a git diff (default command)",
                       description="Check code changes. With no options: uncommitted changes, or this branch vs main "
                                   "when nothing is uncommitted, or the last commit.")
    p.add_argument("paths", nargs="*", help="limit the check to these paths")
    p.add_argument("--base", metavar="REV", help="compare against REV (working tree, or --head)")
    p.add_argument("--head", metavar="REV", help="compare --base..REV (committed changes only)")
    p.add_argument("--staged", action="store_true", help="check staged changes only (for pre-commit)")
    p.add_argument("--branch", action="store_true", help="this branch plus uncommitted changes vs main")
    p.add_argument("--last-commit", action="store_true", help="check the most recent commit")
    p.add_argument("-C", "--cwd", metavar="DIR", default=".", help="run as if started in DIR")
    _add_output(p)

    p = sub.add_parser("session", help="check a Claude Code session (diff plus transcript)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--transcript", metavar="FILE", help="path to a session .jsonl transcript")
    g.add_argument("--id", metavar="SESSION_ID", help="session id (looked up under ~/.claude/projects)")
    p.add_argument("--baseline", metavar="TREE", help="git tree or commit the session started from")
    p.add_argument("-C", "--cwd", metavar="DIR", default=".", help="project directory (default: current)")
    _add_output(p)

    p = sub.add_parser("audit", help="scan your past Claude Code sessions")
    p.add_argument("--days", type=int, default=30, help="look back this many days (default: 30)")
    p.add_argument("--all", action="store_true", help="every session, regardless of age")
    p.add_argument("--project", metavar="DIR", help="only sessions that ran in DIR")
    p.add_argument("--limit", type=int, default=0, help="at most this many sessions (newest first)")
    p.add_argument("--redact", action="store_true", help="leave prompts and code out of the report")
    p.add_argument("-f", "--format", choices=("text", "json", "html", "markdown"), default="text")
    p.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("demo", help="watch redhanded catch a pretend agent gaming a small project's tests",
                       description="Build a throwaway repo, replay a short agent session that games its failing "
                                   "tests, and check it. Needs git; changes nothing outside a temporary folder.")
    p.add_argument("--honest", action="store_true", help="replay an honest fix instead (it comes back clean)")
    _add_output(p)

    p = sub.add_parser("hook", help="entry point for Claude Code hooks (reads JSON on stdin)")
    p.add_argument("event", help="session-start | post-edit | stop")

    p = sub.add_parser("init", help="set up redhanded for Claude Code, GitHub Actions or pre-commit")
    p.add_argument("target", choices=("claude", "github", "pre-commit"))
    p.add_argument("--user", action="store_true", help="claude: install hooks for every project (~/.claude/settings.json)")
    p.add_argument("--dry-run", action="store_true", help="print what would change without writing")
    p.add_argument("-C", "--cwd", metavar="DIR", default=".")

    p = sub.add_parser("rules", help="list the rules")
    p.add_argument("-f", "--format", choices=("text", "markdown", "json"), default="text")
    p.add_argument("--no-color", action="store_true")

    p = sub.add_parser("explain", help="explain one rule")
    p.add_argument("rule")
    p.add_argument("--no-color", action="store_true")

    p = sub.add_parser("ci", help="CI mode: diff a pull request or push, annotate, and write a job summary")
    p.add_argument("--base", metavar="REV", help="override the base revision")
    p.add_argument("--head", metavar="REV", help="override the head revision")
    p.add_argument("--sarif", metavar="FILE", help="also write SARIF to FILE")
    p.add_argument("--fail-on", type=_severity, metavar="SEVERITY")
    p.add_argument("-C", "--cwd", metavar="DIR", default=".")

    sub.add_parser("version", help="print the version")
    return parser


def _write(text: str, output: Optional[str]) -> None:
    if output:
        with open(output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def emit_report(report: Report, args, title: Optional[str] = None) -> None:
    if args.format == "text":
        style = style_for(sys.stdout, color=False if (args.no_color or args.output) else None)
        text = render(report, "text", style=style, verbose=args.verbose, show_low=not args.quiet, title=title)
    else:
        text = render(report, args.format)
    _write(text, args.output)


def threshold(args, cfg) -> Severity:
    """--fail-on when given (`off` is a valid choice, and falsy), else the project setting."""
    return args.fail_on if args.fail_on is not None else cfg.fail_on


def exit_code(report: Report, level: Severity) -> int:
    if level == Severity.OFF:
        return EXIT_OK
    return EXIT_FINDINGS if report.max_severity() >= level else EXIT_OK


def _err(msg: str) -> int:
    sys.stderr.write("redhanded: %s\n" % msg)
    return EXIT_ERROR


# --------------------------------------------------------------------------- commands


def cmd_check(args) -> int:
    from . import engine

    cwd = os.path.abspath(args.cwd)
    root = gitio.repo_root(cwd)
    if not root:
        return _err("not inside a git repository (redhanded checks the changes git can see)")
    subdir = gitio.subdir_of(root, cwd)
    cfg = settings_mod.load(root)
    for w in cfg.warnings:
        sys.stderr.write("redhanded: warning: %s\n" % w)
    note = None
    try:
        if args.base or args.head or args.staged or args.branch or args.last_commit:
            scope = engine.resolve_scope(root, base=args.base, head=args.head, staged=args.staged, branch=args.branch,
                                         last_commit=args.last_commit, subdir=subdir)
        else:
            scope, note = engine.auto_scope(root, subdir=subdir)
    except gitio.GitError as exc:
        return _err(str(exc))
    paths = [os.path.relpath(os.path.join(cwd, p), root).replace("\\", "/") for p in args.paths]
    report = engine.scan_git(root, scope, cfg, paths=paths)
    if note:
        report.notes.insert(0, note)
    emit_report(report, args)
    return exit_code(report, threshold(args, cfg))


def cmd_session(args) -> int:
    from . import state, transcript
    from .session_check import check_session

    cwd = os.path.abspath(args.cwd)
    path = args.transcript
    if args.id:
        path = transcript.find_session(args.id)
        if not path:
            return _err("no transcript found for session %s" % args.id)
    if not path:
        candidates = transcript.transcripts_for(gitio.repo_root(cwd) or cwd) or transcript.transcripts_for(cwd)
        if not candidates:
            return _err("no Claude Code sessions found for %s (use --transcript FILE)" % cwd)
        path = candidates[-1]
    if not os.path.isfile(path):
        return _err("transcript not found: %s" % path)
    session = transcript.load_session(path)
    root = gitio.repo_root(session.cwd or cwd) if (session.cwd and os.path.isdir(session.cwd)) else gitio.repo_root(cwd)
    baseline = args.baseline
    if not baseline and root:
        # The SessionStart hook keys its snapshot by the directory the session started in.
        st = state.load(session.id)
        for key in (st.get("cwd"), session.cwd, root):
            baseline = state.get_baseline(st, key) if key else None
            if baseline:
                break
    cfg = settings_mod.load(root) if root else settings_mod.Settings()
    report = check_session(session, root=root or session.cwd, settings=cfg, baseline=baseline)
    emit_report(report, args)
    return exit_code(report, threshold(args, cfg))


def cmd_rules(args) -> int:
    from .rules import RULES

    if args.format == "json":
        import json

        data = [{"id": r.id, "title": r.title, "severity": r.severity.label, "category": r.category,
                 "summary": r.summary, "why": r.why, "fix": r.fix} for r in RULES]
        _write(json.dumps(data, indent=2) + "\n", None)
    elif args.format == "markdown":
        from .docs import rules_markdown

        _write(rules_markdown(), None)
    else:
        from .report.text import render_rules

        _write(render_rules(style_for(sys.stdout, color=False if args.no_color else None)), None)
    return EXIT_OK


def cmd_explain(args) -> int:
    from .report.text import render_explain
    from .rules import BY_ID

    if args.rule not in BY_ID:
        close = [r for r in BY_ID if args.rule.lower() in r]
        hint = " (did you mean %s?)" % ", ".join(close[:3]) if close else ""
        return _err("unknown rule %r%s; see `redhanded rules`" % (args.rule, hint))
    _write(render_explain(args.rule, style_for(sys.stdout, color=False if args.no_color else None)), None)
    return EXIT_OK


def cmd_demo(args) -> int:
    from . import demo

    try:
        report = demo.run(honest=args.honest)
    except gitio.GitError as exc:
        return _err("the demo needs git: %s" % exc)
    text_out = args.format == "text" and not args.output
    style = style_for(sys.stdout, color=False if args.no_color else None)
    if text_out:
        _write("".join(style(line, "grey") + "\n" for line in demo.story(args.honest)) + "\n", None)
    title = "demo: an honest fix" if args.honest else "demo: a pretend agent's \"fix\""
    emit_report(report, args, title=title)
    if text_out:
        _write("\n" + "".join(line + "\n" for line in demo.NEXT_STEPS), None)
    return exit_code(report, args.fail_on) if args.fail_on is not None else EXIT_OK


def cmd_audit(args) -> int:
    from .audit import run_audit

    return run_audit(args)


def cmd_ci(args) -> int:
    from .ci import run_ci

    return run_ci(args)


def cmd_init(args) -> int:
    from .install import run_init

    return run_init(args)


def cmd_hook(args) -> int:
    from .hooks import run

    return run(args.event)


def _fix_streams() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _fix_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv = ["check"] + argv
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "check": cmd_check, "session": cmd_session, "audit": cmd_audit, "demo": cmd_demo, "hook": cmd_hook,
        "init": cmd_init,
        "rules": cmd_rules, "explain": cmd_explain, "ci": cmd_ci,
    }
    if args.command == "version":
        _write("redhanded %s\n" % __version__, None)
        return EXIT_OK
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # Output was piped into something that closed early (e.g. `| head`). Point stdout at devnull so the
        # interpreter's final flush does not print a second traceback.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError, io.UnsupportedOperation):
            pass
        return EXIT_OK
    except gitio.GitError as exc:
        return _err(str(exc))


if __name__ == "__main__":
    sys.exit(main())
