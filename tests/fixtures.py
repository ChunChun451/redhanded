"""A small shop project, and the same project after an agent gamed its checks (shared with `redhanded demo`)."""

from tests import helpers  # noqa: F401  (puts the library on sys.path)

from redhanded.demo import BASE, GAMED, HONEST  # noqa: E402

__all__ = ["BASE", "GAMED", "HONEST", "EXPECTED_RULES", "build", "apply"]

# Every rule the gamed version should trigger.
EXPECTED_RULES = {
    "special-cased-test-value", "test-environment-branch", "assertion-weakened", "test-skipped", "test-deleted",
    "assertion-swallowed", "test-script-weakened", "ci-weakened", "type-or-lint-config-weakened",
    "placeholder-implementation",
}


def build(repo, files=BASE, message="baseline"):
    for path, text in files.items():
        repo.write(path, text)
    return repo.commit(message)


def apply(repo, files):
    for path, text in files.items():
        if text is None:
            repo.remove(path)
        else:
            repo.write(path, text)
