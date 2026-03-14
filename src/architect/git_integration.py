"""Git integration for Architect: diff scanning, branch snapshots, blame,
co-change analysis, and change-frequency hotspot detection."""

from __future__ import annotations

import subprocess
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Optional

from rich.console import Console

from .storage import Storage

console = Console()


def _run_git(args: list[str], cwd: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def is_git_repo(root: Path) -> bool:
    return _run_git(["rev-parse", "--is-inside-work-tree"], root) == "true"


def get_current_branch(root: Path) -> Optional[str]:
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)


def get_changed_files(root: Path, since: Optional[str] = None) -> list[str]:
    """Get files changed since a ref (or since last commit if None)."""
    if since:
        output = _run_git(["diff", "--name-only", since], root)
    else:
        staged = _run_git(["diff", "--name-only", "--cached"], root) or ""
        unstaged = _run_git(["diff", "--name-only"], root) or ""
        untracked = _run_git(["ls-files", "--others", "--exclude-standard"], root) or ""
        all_files = set()
        for block in [staged, unstaged, untracked]:
            for line in block.splitlines():
                line = line.strip()
                if line:
                    all_files.add(line.replace("\\", "/"))
        return sorted(all_files)

    if output is None:
        return []
    return sorted(
        f.strip().replace("\\", "/")
        for f in output.splitlines()
        if f.strip()
    )


def get_blame_info(root: Path, file_path: str) -> Optional[dict[str, str]]:
    """Get the last author for a file using git blame --porcelain (summary)."""
    output = _run_git(["log", "-1", "--format=%an|%ae|%aI", "--", file_path], root)
    if not output:
        return None
    parts = output.split("|", 2)
    if len(parts) == 3:
        return {"author": parts[0], "email": parts[1], "date": parts[2]}
    return None


def get_file_authors(root: Path, file_path: str) -> list[dict[str, str]]:
    """Get all authors who touched a file."""
    output = _run_git(["log", "--format=%an|%ae", "--", file_path], root)
    if not output:
        return []
    seen = set()
    authors = []
    for line in output.splitlines():
        parts = line.strip().split("|", 1)
        if len(parts) == 2 and parts[0] not in seen:
            seen.add(parts[0])
            authors.append({"author": parts[0], "email": parts[1]})
    return authors


def scan_changed_files_only(storage: Storage, since: Optional[str] = None) -> list[str]:
    """Return list of changed file paths (relative, forward-slash) for targeted re-scan."""
    root = storage.project_root
    if not is_git_repo(root):
        console.print("[yellow]Not a git repository -- falling back to full scan.[/yellow]")
        return []
    return get_changed_files(root, since=since)


def snapshot_branch(storage: Storage) -> Optional[str]:
    """Save a graph snapshot for the current branch."""
    root = storage.project_root
    branch = get_current_branch(root)
    if not branch:
        return None
    storage.save_branch_snapshot(branch)
    return branch


def enrich_node_blame(storage: Storage, node_id: str) -> Optional[dict]:
    """Enrich a node's metadata with git blame info."""
    root = storage.project_root
    if not is_git_repo(root):
        return None

    graph = storage.load_graph()
    node = graph.get_node(node_id)
    if not node or not node.file_path:
        return None

    blame = get_blame_info(root, node.file_path)
    if blame:
        node.metadata["last_author"] = blame["author"]
        node.metadata["last_author_email"] = blame["email"]
        node.metadata["last_modified_date"] = blame["date"]

    authors = get_file_authors(root, node.file_path)
    if authors:
        node.metadata["contributors"] = [a["author"] for a in authors]

    node.touch()
    storage.save_graph(graph)
    return blame


# ── Co-change analysis ────────────────────────────────────────────


def get_change_frequency(root: Path, max_commits: int = 500) -> dict[str, int]:
    """Count how often each file appears in recent commits.

    Files with high counts are architectural hotspots -- they change
    frequently and are likely critical to the system.
    """
    output = _run_git(
        ["log", f"--max-count={max_commits}", "--name-only", "--pretty=format:"],
        root,
    )
    if not output:
        return {}

    freq: Counter[str] = Counter()
    for line in output.splitlines():
        path = line.strip().replace("\\", "/")
        if path and not path.startswith(".architect/"):
            freq[path] += 1
    return dict(freq)


def get_co_changed_files(
    root: Path,
    min_commits: int = 3,
    max_commits: int = 500,
    max_files_per_commit: int = 50,
) -> dict[tuple[str, str], int]:
    """Find file pairs that frequently appear together in commits.

    Returns ``{(file_a, file_b): count}`` for pairs that co-occur in at
    least *min_commits* commits.  Commits touching more than
    *max_files_per_commit* files are skipped (bulk operations / merges).
    """
    output = _run_git(
        ["log", f"--max-count={max_commits}", "--name-only", "--pretty=format:---COMMIT---"],
        root,
    )
    if not output:
        return {}

    pair_counts: Counter[tuple[str, str]] = Counter()
    current_files: list[str] = []

    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "---COMMIT---":
            unique = sorted(set(current_files))
            if 2 <= len(unique) <= max_files_per_commit:
                for a, b in combinations(unique, 2):
                    pair_counts[(a, b)] += 1
            current_files = []
        elif stripped:
            path = stripped.replace("\\", "/")
            if not path.startswith(".architect/"):
                current_files.append(path)

    unique = sorted(set(current_files))
    if 2 <= len(unique) <= max_files_per_commit:
        for a, b in combinations(unique, 2):
            pair_counts[(a, b)] += 1

    return {pair: count for pair, count in pair_counts.items() if count >= min_commits}


def enrich_graph_with_git_insights(storage: Storage) -> dict:
    """Add change-frequency metadata and co-change edges to the graph.

    Returns a stats dict with counts of enriched nodes and added edges.
    """
    root = storage.project_root
    if not is_git_repo(root):
        return {"hotspots": 0, "co_change_edges": 0}

    graph = storage.load_graph()
    file_node_map: dict[str, str] = {}
    for node in graph.nodes:
        if node.file_path:
            file_node_map[node.file_path] = node.id

    stats = {"hotspots": 0, "co_change_edges": 0}

    freq = get_change_frequency(root)
    for file_path, count in freq.items():
        node_id = file_node_map.get(file_path)
        if node_id:
            node = graph.get_node(node_id)
            if node:
                node.metadata["change_frequency"] = count
                stats["hotspots"] += 1

    from .models import Edge, EdgeType

    existing_pairs = {(e.source, e.target) for e in graph.edges}
    co_changes = get_co_changed_files(root)
    for (file_a, file_b), count in co_changes.items():
        id_a = file_node_map.get(file_a)
        id_b = file_node_map.get(file_b)
        if id_a and id_b and (id_a, id_b) not in existing_pairs and (id_b, id_a) not in existing_pairs:
            graph.edges.append(Edge(
                source=id_a,
                target=id_b,
                label=f"co-changes ({count} commits)",
                type=EdgeType.DATA_FLOW,
            ))
            existing_pairs.add((id_a, id_b))
            stats["co_change_edges"] += 1

    storage.save_graph(graph)
    return stats
