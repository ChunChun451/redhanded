---
name: redhanded
description: Checks code changes for gamed verification - skipped, deleted or weakened tests, hard-coded answers for test inputs, production code that detects the test runner, loosened test/lint/type/CI config, bypassed git hooks - and for "tests pass" claims no passing run backs up. Use before saying tests pass or a coding task is done, before committing or opening a pull request, and when asked to review or audit AI-written changes.
---

# redhanded

Before you report a coding task as done, check your changes:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/redhanded.py" check
```

Use `python` or `py -3` if `python3` is missing. Outside Claude Code, run `scripts/redhanded.py` from this skill's folder. Needs Python 3.9+ and git; nothing to install.

With no options it checks uncommitted changes, or this branch against main when nothing is uncommitted. Exit code 1 means at least one high-severity finding.

## When it reports findings

- Fix the implementation, not the test or the check. Restore skipped, deleted or loosened tests and configuration.
- If a change is intended (the user asked for it, or a test really is obsolete), keep it and say so plainly in your reply, with the reason. For a lasting exception, add `redhanded-ignore[rule-id] reason` in a comment on or above the line.
- Do not suppress or work around a finding just to make this check pass. That is the behaviour it exists to catch.
- Only say tests pass if a passing run happened after your last edit. Name the command you ran.

`explain <rule-id>` describes a rule, why it matters and how to fix it. All rules: [references/rules.md](references/rules.md).

## Other commands

- `check --staged`: staged changes only (pre-commit). `check --branch`, `check --base <rev>`, `check --last-commit`.
- `session`: the latest Claude Code session in this project, including whether its final reply's claims match the commands that ran.
- `audit --days 30 -f html -o redhanded-audit.html`: scan past Claude Code sessions on this machine.
- `-f json|sarif|markdown|github` for machine-readable output, `-v` for evidence.
