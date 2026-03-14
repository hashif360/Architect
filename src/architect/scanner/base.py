"""Base scanner interface with git-first file discovery."""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional, TypeVar

from ..models import GraphData
from ..storage import Storage

_T = TypeVar("_T")


class BaseScanner(ABC):
    """Abstract base for project scanners.

    Provides git-first file discovery: when the project is a git repository,
    file enumeration uses ``git ls-files`` instead of filesystem walking.
    This automatically respects .gitignore and avoids scanning build artifacts,
    generated code, and vendored dependencies.
    """

    name: str = "base"
    CHUNK_SIZE: int = 200

    SKIP_DIRS = frozenset({
        "node_modules", ".git", ".github", ".architect", "__pycache__",
        ".next", ".nuxt", "dist", "build", ".venv", "venv",
        "env", ".env", ".idea", ".vscode", ".cursor",
        "coverage", ".nyc_output", ".turbo", ".cache",
    })

    def __init__(
        self,
        storage: Storage,
        root: Path,
        changed_files: Optional[set[str]] = None,
    ):
        self.storage = storage
        self.root = root.resolve()
        self._changed_files = changed_files
        self._tracked_files: Optional[set[str]] = self._load_git_files()

    def _load_git_files(self) -> Optional[set[str]]:
        """Return git-tracked and untracked-but-not-ignored relative paths.

        Returns ``None`` when the project is not inside a git repo or git
        is not installed.  Files under SKIP_DIRS (e.g. node_modules) are
        always excluded regardless of git tracking state.
        """
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "-z"],
                capture_output=True,
                cwd=str(self.root),
                timeout=30,
            )
            if tracked.returncode != 0:
                return None

            files: set[str] = set()
            for f in tracked.stdout.decode("utf-8", errors="ignore").split("\0"):
                if f:
                    files.add(f.replace("\\", "/"))

            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                capture_output=True,
                cwd=str(self.root),
                timeout=30,
            )
            if untracked.returncode == 0:
                for f in untracked.stdout.decode("utf-8", errors="ignore").split("\0"):
                    if f:
                        files.add(f.replace("\\", "/"))

            skip = self.SKIP_DIRS
            files = {f for f in files if not (set(f.split("/")) & skip)}

            return files
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    @abstractmethod
    def scan(self) -> GraphData:
        """Scan the project and return discovered nodes and edges."""
        ...

    def relative(self, path: Path) -> str:
        """Return a forward-slash relative path from the project root."""
        try:
            return str(path.resolve().relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    # ── File discovery ─────────────────────────────────────────────

    def _find_files(self, *patterns: str, root_dir: Path | None = None) -> list[Path]:
        """Find files matching glob patterns, using git when available.

        When ``changed_files`` was provided at construction time, only
        those files are considered -- this turns a full scan into an
        incremental one that skips parsing untouched files entirely.

        When the project is a git repo, filters the tracked-file set rather
        than walking the filesystem.  Falls back to ``rglob`` with
        directory-skip filtering otherwise.

        Args:
            *patterns: Glob patterns (e.g. ``"*.py"``, ``"*.component.ts"``,
                       ``"urls.py"``, ``"views/*.py"``).
            root_dir:  Restrict results to a subdirectory.
        """
        scan_root = root_dir or self.root

        if self._changed_files is not None:
            return self._find_files_changed(patterns, scan_root)

        if self._tracked_files is not None:
            return self._find_files_git(patterns, scan_root)
        return self._find_files_walk(patterns, scan_root)

    def _find_files_changed(
        self, patterns: tuple[str, ...], scan_root: Path,
    ) -> list[Path]:
        """Only return files that are in the changed-files set."""
        scan_prefix = ""
        scan_root_resolved = scan_root.resolve()
        if scan_root_resolved != self.root:
            try:
                scan_prefix = (
                    str(scan_root_resolved.relative_to(self.root)).replace("\\", "/")
                    + "/"
                )
            except ValueError:
                return []

        result: list[Path] = []
        for rel_path in self._changed_files:
            if scan_prefix and not rel_path.startswith(scan_prefix):
                continue

            name = rel_path.rsplit("/", 1)[-1]

            for p in patterns:
                if "/" in p:
                    sub_rel = rel_path[len(scan_prefix):] if scan_prefix else rel_path
                    if PurePosixPath(sub_rel).match(p):
                        full = self.root / rel_path
                        if full.is_file():
                            result.append(full)
                        break
                elif fnmatch(name, p):
                    full = self.root / rel_path
                    if full.is_file():
                        result.append(full)
                    break

        return sorted(result)

    def _find_files_git(
        self, patterns: tuple[str, ...], scan_root: Path,
    ) -> list[Path]:
        scan_prefix = ""
        scan_root_resolved = scan_root.resolve()
        if scan_root_resolved != self.root:
            try:
                scan_prefix = (
                    str(scan_root_resolved.relative_to(self.root)).replace("\\", "/")
                    + "/"
                )
            except ValueError:
                return []

        result: list[Path] = []
        for rel_path in self._tracked_files:
            if scan_prefix and not rel_path.startswith(scan_prefix):
                continue

            name = rel_path.rsplit("/", 1)[-1]

            for p in patterns:
                if "/" in p:
                    sub_rel = rel_path[len(scan_prefix):] if scan_prefix else rel_path
                    if PurePosixPath(sub_rel).match(p):
                        result.append(self.root / rel_path)
                        break
                elif fnmatch(name, p):
                    result.append(self.root / rel_path)
                    break

        return sorted(result)

    def _find_files_walk(
        self, patterns: tuple[str, ...], scan_root: Path,
    ) -> list[Path]:
        result: set[Path] = set()
        for pattern in patterns:
            for f in scan_root.rglob(pattern):
                if f.is_file() and not self._should_skip(f):
                    result.add(f)
        return sorted(result)

    def _should_skip(self, path: Path) -> bool:
        """Check whether *path* falls inside a directory that should be skipped.

        Used as the fallback filter for non-git repos.
        """
        parts = set(path.parts)
        return bool(parts & self.SKIP_DIRS)

    @staticmethod
    def _iter_chunks(items: list[_T], size: int = 200) -> Iterator[list[_T]]:
        """Yield successive chunks of *size* from *items*."""
        for i in range(0, len(items), size):
            yield items[i : i + size]
