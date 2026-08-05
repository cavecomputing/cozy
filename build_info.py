"""Resolve the Git commit that identifies the running Cozy build."""

import os
import re
import subprocess
from dataclasses import dataclass


EMBEDDED_COMMIT_FILE = '.cozy-commit'
_COMMIT_RE = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z', re.IGNORECASE)


@dataclass(frozen=True)
class BuildInfo:
    commit: str | None = None
    dirty: bool = False

    @property
    def display(self):
        """Return the short user-facing build label."""
        if not self.commit:
            return 'unknown'
        suffix = '-dirty' if self.dirty else ''
        return f'{self.commit[:12]}{suffix}'


def _normalize_commit(value):
    commit = (value or '').strip().lower()
    return commit if _COMMIT_RE.fullmatch(commit) else None


def _read_text(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except (OSError, UnicodeError):
        return None


def _git_directories(base_dir):
    """Return the checkout's Git directory and shared Git directory."""
    marker = os.path.join(base_dir, '.git')
    if os.path.isdir(marker):
        git_dir = os.path.abspath(marker)
    elif os.path.isfile(marker):
        contents = _read_text(marker)
        if not contents or not contents.strip().lower().startswith('gitdir:'):
            return None, None
        path = contents.strip().split(':', 1)[1].strip()
        git_dir = os.path.abspath(os.path.join(base_dir, path))
        if not os.path.isdir(git_dir):
            return None, None
    else:
        return None, None

    common_dir = git_dir
    common_path = _read_text(os.path.join(git_dir, 'commondir'))
    if common_path:
        candidate = os.path.abspath(os.path.join(git_dir, common_path.strip()))
        if os.path.isdir(candidate):
            common_dir = candidate
    return git_dir, common_dir


def _safe_ref_path(git_dir, ref):
    candidate = os.path.abspath(os.path.join(git_dir, *ref.split('/')))
    try:
        if os.path.commonpath((git_dir, candidate)) != os.path.abspath(git_dir):
            return None
    except ValueError:
        return None
    return candidate


def _read_packed_ref(git_dir, ref):
    contents = _read_text(os.path.join(git_dir, 'packed-refs'))
    if not contents:
        return None
    for line in contents.splitlines():
        if not line or line.startswith(('#', '^')):
            continue
        fields = line.split(' ', 1)
        if len(fields) == 2 and fields[1] == ref:
            return _normalize_commit(fields[0])
    return None


def read_git_commit(base_dir):
    """Resolve HEAD from a normal, detached, or linked Git checkout."""
    git_dir, common_dir = _git_directories(base_dir)
    if not git_dir:
        return None

    head = _read_text(os.path.join(git_dir, 'HEAD'))
    if not head:
        return None
    head = head.strip()
    if not head.startswith('ref:'):
        return _normalize_commit(head)

    ref = head.split(':', 1)[1].strip()
    for directory in dict.fromkeys((git_dir, common_dir)):
        ref_path = _safe_ref_path(directory, ref)
        commit = _normalize_commit(_read_text(ref_path)) if ref_path else None
        if commit:
            return commit
        commit = _read_packed_ref(directory, ref)
        if commit:
            return commit
    return None


def _git_worktree_is_dirty(base_dir):
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain', '--untracked-files=normal'],
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def get_build_info(base_dir):
    """Resolve an embedded Docker revision, then fall back to the checkout."""
    embedded = _normalize_commit(
        _read_text(os.path.join(base_dir, EMBEDDED_COMMIT_FILE))
    )
    if embedded:
        return BuildInfo(embedded)

    commit = read_git_commit(base_dir)
    if not commit:
        return BuildInfo()
    return BuildInfo(commit, _git_worktree_is_dirty(base_dir))


if __name__ == '__main__':
    print(read_git_commit(os.path.dirname(os.path.abspath(__file__))) or 'unknown')
