# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-08

First public release.

### Added

- 29 rules in four groups: tests (skipped, focused, deleted or weakened tests, swallowed or tautological assertions), implementation (hard-coded answers for test inputs, test-runner detection, placeholders, swallowed errors), config (neutered test scripts, test/type/lint configs, CI and git hooks) and session (claims the transcript contradicts, bypassed checks, tests edited past a failure, admitted shortcuts).
- Test and skip detection for Python, JavaScript/TypeScript, Go, Rust, Java, Kotlin, Groovy, Scala, C#, Ruby, PHP, Swift, Dart, Elixir, C/C++ and shell (bats).
- `redhanded check` for uncommitted changes, staged changes, a branch, the last commit or any range.
- Claude Code hooks: a snapshot at session start, a heads-up after risky edits, and a Stop check that sends the agent back (at most 3 times) when it gamed the checks. Findings in files the session never touched never block.
- `redhanded session` and `redhanded audit` for past Claude Code sessions, with text, JSON, Markdown and self-contained HTML reports.
- Claim checks that match each "tests pass" to the runs that could back it. Background runs, commands that write a file and test it in one go, project scripts (`./scripts/check`, `run_tests.sh`) and compilers all count. A later edit only outdates the checks that could have caught it: a pytest run still counts after a CSS change. A check that could not start (missing tool) is not a failure, and a reply that says some tests still fail is not a contradiction.
- `redhanded ci` and a GitHub Action with annotations, a job summary, outputs and optional SARIF upload.
- `redhanded demo`: a throwaway repo and a pretend agent session, gamed or honest.
- Claude Code plugin and marketplace, Agent Skill, pre-commit hook and `redhanded init claude|github|pre-commit`.
- Settings in `.redhanded.toml` or `[tool.redhanded]`, line and file suppressions, and `redhanded-allow:` lines in pull request descriptions.
- User intent: changes the user asked for ("skip the flaky test") are lowered to low severity; changes the user forbade ("don't touch the tests") are raised.

[Unreleased]: https://github.com/ChunChun451/redhanded/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ChunChun451/redhanded/releases/tag/v0.1.0
