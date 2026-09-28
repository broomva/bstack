"""git_env_policy.py — which GIT_* variables may reach a git child process (BRO-2542).

One rule, imported by every bstack script that runs git (agent_evals, plan_drift,
test_lock), so the three cannot drift apart.

A GIT_* variable passes through unless it does one of two things:
  - points git at ANOTHER repository. Run from inside a hook or a CI step that exports
    GIT_DIR, a "repo-scoped" call would read, and a hook would write, the wrong
    repository without any error;
  - injects configuration or a program. GIT_CONFIG_PARAMETERS and GIT_CONFIG_COUNT with
    GIT_CONFIG_KEY_n/VALUE_n set config that outranks every file. GIT_CONFIG_GLOBAL and
    GIT_CONFIG_SYSTEM point git at an arbitrary config file, which can set core.hooksPath
    (so `test-lock commit` would run a planted hook). GIT_EXTERNAL_DIFF and GIT_EXEC_PATH
    name programs git runs.

GIT_CONFIG_NOSYSTEM passes: it only removes config. The global config git reads is the
one under HOME, which every caller keeps; a test that wants none sets HOME to a temp dir.
"""
from __future__ import annotations

GIT_ENV_DENY = frozenset({
    # another repository
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    "GIT_PREFIX", "GIT_CEILING_DIRECTORIES",
    # injected configuration or programs
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_EXTERNAL_DIFF", "GIT_EXEC_PATH",
})
GIT_ENV_DENY_PREFIX = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def git_var_allowed(name: str) -> bool:
    """True for a GIT_* variable that may reach a git child process."""
    return (name.startswith("GIT_") and name not in GIT_ENV_DENY
            and not name.startswith(GIT_ENV_DENY_PREFIX))
