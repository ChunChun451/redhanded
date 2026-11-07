#!/bin/sh
# Run a redhanded hook with the first Python 3.9+ found. Never fails the Claude Code session:
# without a usable Python it exits 0 and does nothing.
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
script="$here/../skills/redhanded/scripts/redhanded.py"
[ -f "$script" ] || exit 0

for py in python3 python "py -3"; do
  # `python3` can be the Microsoft Store stub on Windows; this probe rejects it.
  if $py -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    exec $py "$script" hook "$@"
  fi
done
exit 0
