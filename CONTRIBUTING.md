# Contributing

Thanks for helping. The most useful contributions are new ways agents game checks (with a real example if you have one) and false positives on honest changes.

## Ground rules

- Standard library only, Python 3.9 or newer. The skill and the plugin run straight from the repository with no install step, so there is nowhere to put a dependency.
- Every rule needs two kinds of test: the hack it catches, and an honest change that looks similar but must stay quiet. Honest look-alikes go in `tests/test_false_positives.py`.
- A false positive in the Stop hook costs the user a wasted agent turn, so rules that guess should be `medium` or `low`. Only flag `high` when the change is very hard to explain honestly.

## Running the tests

```bash
python -m unittest discover -s tests -t .
```

Set `REDHANDED_DEBUG=1` to make rule errors raise instead of being reported as notes. The tests create throwaway git repositories in your temp folder and need `git` on `PATH`.

## Adding or changing a rule

1. Rules live in `skills/redhanded/scripts/lib/redhanded/rules/` (`tests.py`, `impl.py`, `config.py`, `session.py`). Shared regular expressions per language are in `patterns.py`.
2. Each rule has an id, title, severity, summary, why and fix text. The docs are generated from them:

   ```bash
   python tools/gen_docs.py
   ```

   CI fails if `docs/rules.md` or `skills/redhanded/references/rules.md` is out of date.
3. Try it end to end with `python skills/redhanded/scripts/redhanded.py demo` and on a real repository with `redhanded check`.

## Releasing

1. Update the version in `skills/redhanded/scripts/lib/redhanded/__init__.py`, `pyproject.toml`, `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` (a test checks they agree), and add a `CHANGELOG.md` entry.
2. Push a `vX.Y.Z` tag, and move the `v0` tag to the same commit so `uses: ChunChun451/redhanded@v0` picks up the release.
3. Publish a GitHub release for the tag. The `release` workflow runs the tests, builds the package and publishes it to PyPI. The first time, add a trusted publisher for this repository on pypi.org (workflow `release.yml`, environment `pypi`).
