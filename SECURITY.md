# Security

## Reporting a problem

Please report security problems privately through [GitHub's private vulnerability reporting](https://github.com/ChunChun451/redhanded/security/advisories/new) rather than in a public issue. You should get a reply within a week.

Examples of what counts:

- a crafted repository, diff, transcript or pull request description that makes redhanded run code, write outside its state folder, or hang a hook;
- a way for an agent to make redhanded report a gamed change as clean that is cheap and general (a single rule's blind spot is a normal bug; please open an issue for those).

## What redhanded does on your machine

- It runs locally and makes no network requests. It has no dependencies outside the Python standard library.
- It runs `git`. Snapshots use `git add` and `git write-tree` against a temporary index file: they add objects to `.git/objects` (which `git gc` cleans up) but never touch your real index, branches, stash or working tree.
- The Claude Code hooks and `redhanded session` / `redhanded audit` read session transcripts under `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR`).
- Hooks keep a small JSON file per session (the starting snapshot id and what was already reported) in `$CLAUDE_PLUGIN_DATA/sessions`, `%LOCALAPPDATA%\redhanded\sessions` or `~/.local/state/redhanded/sessions`. Files older than 30 days are deleted.
- Hooks fail open: if anything goes wrong, they exit quietly and never block your session.
- `redhanded audit -f html` writes a self-contained report that can include prompts and code from your sessions. Use `--redact` before sharing one.
