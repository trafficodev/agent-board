"""Resolve the exact Git revision reviewed by a caller."""

import re
import subprocess


_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")


class GitRevisionError(ValueError):
    pass


def current_commit(cwd: str) -> str:
    result = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    commit_sha = result.stdout.strip()
    if result.returncode or not _SHA_PATTERN.fullmatch(commit_sha):
        raise GitRevisionError("Current Git revision is unavailable")
    return commit_sha
