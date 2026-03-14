"""Auto-detect project type and run the appropriate scanner."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Node, NodeStatus, NodeType
from ..storage import Storage
from .general import GeneralScanner
from .javascript import JavaScriptScanner
from .python import PythonScanner
from .dotnet import DotNetScanner
from .php import PHPScanner
from .edges import EdgeInferrer
from .database import DatabaseScanner

console = Console()


def detect_project_type(root: Path) -> str:
    """Detect the primary project type based on marker files."""
    markers = {
        "package.json": "javascript",
        "tsconfig.json": "javascript",
        "angular.json": "javascript",
        "requirements.txt": "python",
        "pyproject.toml": "python",
        "setup.py": "python",
        "manage.py": "python",
        "Pipfile": "python",
        "composer.json": "php",
        "artisan": "php",
        "wp-config.php": "php",
        "*.csproj": "dotnet",
        "*.sln": "dotnet",
        "Cargo.toml": "rust",
        "go.mod": "go",
        "pom.xml": "java",
        "build.gradle": "java",
    }

    for marker, ptype in markers.items():
        if "*" in marker:
            if list(root.glob(marker)):
                return ptype
        elif (root / marker).exists():
            return ptype

    php_files = list(root.glob("*.php"))
    if php_files:
        return "php"

    return "general"


# ── Monorepo / workspace detection ────────────────────────────────


def detect_workspaces(root: Path) -> list[dict]:
    """Detect monorepo workspaces from package.json, pnpm-workspace.yaml,
    or lerna.json.

    Returns a list of ``{"path": Path, "name": str}`` dicts, one per
    workspace that actually exists on disk.
    """
    workspaces: list[dict] = []

    pkg_path = root / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            ws_globs = pkg.get("workspaces", [])
            if isinstance(ws_globs, dict):
                ws_globs = ws_globs.get("packages", [])
            for pattern in ws_globs:
                pattern_clean = pattern.rstrip("/")
                for match in sorted(root.glob(pattern_clean)):
                    if match.is_dir() and (match / "package.json").exists():
                        try:
                            ws_pkg = json.loads((match / "package.json").read_text(encoding="utf-8"))
                            name = ws_pkg.get("name", match.name)
                        except Exception:
                            name = match.name
                        workspaces.append({"path": match, "name": name})
        except Exception:
            pass

    pnpm_path = root / "pnpm-workspace.yaml"
    if pnpm_path.exists() and not workspaces:
        try:
            content = pnpm_path.read_text(encoding="utf-8")
            import re as _re
            patterns = _re.findall(r"['\"]?-\s*['\"]?([^'\"#\n]+)", content)
            for pattern in patterns:
                pattern = pattern.strip().rstrip("/")
                if not pattern:
                    continue
                for match in sorted(root.glob(pattern)):
                    if match.is_dir() and (match / "package.json").exists():
                        try:
                            ws_pkg = json.loads((match / "package.json").read_text(encoding="utf-8"))
                            name = ws_pkg.get("name", match.name)
                        except Exception:
                            name = match.name
                        workspaces.append({"path": match, "name": name})
        except Exception:
            pass

    lerna_path = root / "lerna.json"
    if lerna_path.exists() and not workspaces:
        try:
            lerna = json.loads(lerna_path.read_text(encoding="utf-8"))
            patterns = lerna.get("packages", ["packages/*"])
            for pattern in patterns:
                pattern_clean = pattern.rstrip("/")
                for match in sorted(root.glob(pattern_clean)):
                    if match.is_dir() and (match / "package.json").exists():
                        try:
                            ws_pkg = json.loads((match / "package.json").read_text(encoding="utf-8"))
                            name = ws_pkg.get("name", match.name)
                        except Exception:
                            name = match.name
                        workspaces.append({"path": match, "name": name})
        except Exception:
            pass

    return workspaces


def _diff_merge(
    existing: GraphData,
    scanned: GraphData,
    storage: Storage,
    auto_remove: bool = False,
    workspace: Optional[str] = None,
) -> dict:
    """Diff-based merge: add new, update changed, flag/remove stale nodes."""
    now = datetime.now(timezone.utc)
    stats = {"added": 0, "updated": 0, "stale": 0, "removed": 0, "edges_added": 0, "edges_removed": 0}

    # Map scanned node IDs -> final node IDs (handles ID remapping when
    # a scanned node matches an existing node by file_path)
    id_remap: dict[str, str] = {}

    scanned_files: dict[str, Node] = {}
    for n in scanned.nodes:
        if n.file_path:
            scanned_files[n.file_path] = n

    existing_by_file: dict[str, Node] = {}
    for n in existing.nodes:
        if n.file_path:
            if workspace and n.workspace and n.workspace != workspace:
                continue
            existing_by_file[n.file_path] = n

    for file_path, new_node in scanned_files.items():
        old_node = existing_by_file.get(file_path)
        if old_node is None:
            new_node.status = NodeStatus.IMPLEMENTED
            new_node.last_scanned = now
            if workspace:
                new_node.workspace = workspace
            existing.nodes.append(new_node)
            storage.create_default_context(new_node)
            id_remap[new_node.id] = new_node.id
            stats["added"] += 1
        else:
            changed = False
            if old_node.name != new_node.name:
                old_node.name = new_node.name
                changed = True
            if old_node.type != new_node.type:
                old_node.type = new_node.type
                changed = True
            if old_node.layer != new_node.layer:
                old_node.layer = new_node.layer
                changed = True
            if old_node.summary != new_node.summary:
                old_node.summary = new_node.summary
                changed = True
            if old_node.status == NodeStatus.PLANNED:
                old_node.status = NodeStatus.IMPLEMENTED
                changed = True
            old_node.last_scanned = now
            if changed:
                old_node.touch()
                stats["updated"] += 1
            id_remap[new_node.id] = old_node.id

    # Also map IDs for scanned nodes without file_path (e.g. manually added)
    for n in scanned.nodes:
        if n.id not in id_remap:
            id_remap[n.id] = n.id

    stale_nodes: list[Node] = []
    for file_path, old_node in existing_by_file.items():
        if file_path not in scanned_files:
            if old_node.last_scanned is None:
                old_node.last_scanned = now
            else:
                stale_nodes.append(old_node)

    if stale_nodes:
        if auto_remove:
            for node in stale_nodes:
                existing.remove_node(node.id)
                storage.delete_context(node.id)
                stats["removed"] += 1
        else:
            for node in stale_nodes:
                if "stale" not in node.tags:
                    node.tags.append("stale")
                stats["stale"] += 1

    # Remap scanned edge IDs and validate before merging
    valid_ids = {n.id for n in existing.nodes}
    existing_pairs = {(e.source, e.target) for e in existing.edges}

    for edge in scanned.edges:
        remapped_source = id_remap.get(edge.source, edge.source)
        remapped_target = id_remap.get(edge.target, edge.target)

        if remapped_source not in valid_ids or remapped_target not in valid_ids:
            continue

        key = (remapped_source, remapped_target)
        if key not in existing_pairs:
            edge.source = remapped_source
            edge.target = remapped_target
            existing.edges.append(edge)
            existing_pairs.add(key)
            stats["edges_added"] += 1

    return stats


def detect_and_scan(
    storage: Storage,
    scan_path: Path,
    auto_remove: bool = False,
    diff_mode: bool = True,
    workspace: Optional[str] = None,
    enable_db: bool = True,
    enable_git: bool = True,
    db_url: Optional[str] = None,
    changed_files: Optional[list[str]] = None,
    enable_lighthouse: bool = True,
    lighthouse_base_url: Optional[str] = None,
) -> None:
    """Detect project type and run the scanner, merging results into existing graph.

    For monorepo projects (detected via package.json workspaces,
    pnpm-workspace.yaml, or lerna.json), each workspace is scanned
    independently with its own detected project type and the results
    are merged into a single graph.

    Args:
        enable_db: If True (default), discover DB credentials and introspect live databases.
        db_url: Manual database connection URL. Overrides auto-discovery.
        enable_lighthouse: If True (default), run Lighthouse audits on page nodes.
        lighthouse_base_url: Manual dev server URL for Lighthouse.
    """
    # ── Monorepo detection ─────────────────────────────────────────
    workspaces = detect_workspaces(scan_path)
    if workspaces:
        console.print(f"[bold]Detected monorepo with {len(workspaces)} workspaces[/bold]")
        for ws in workspaces:
            console.print(f"  - {ws['name']} ({ws['path'].relative_to(scan_path)})")
        console.print()

    project_type = detect_project_type(scan_path)
    console.print(f"[bold]Detected project type:[/bold] {project_type}")

    config = storage.load_config()
    config.project_type = project_type
    storage.save_config(config)

    scanners = {
        "javascript": JavaScriptScanner,
        "python": PythonScanner,
        "dotnet": DotNetScanner,
        "php": PHPScanner,
    }

    changed_set = {f.replace("\\", "/") for f in changed_files} if changed_files else None
    new_graph = GraphData()

    if workspaces:
        def _scan_workspace(ws_info: dict) -> tuple[str, GraphData]:
            ws_path = ws_info["path"]
            ws_type = detect_project_type(ws_path)
            ws_scanner_cls = scanners.get(ws_type, GeneralScanner)
            ws_scanner = ws_scanner_cls(storage, ws_path, changed_files=changed_set)
            return ws_info["name"], ws_scanner.scan()

        with ThreadPoolExecutor(max_workers=min(len(workspaces), 4)) as pool:
            futures = {pool.submit(_scan_workspace, ws): ws for ws in workspaces}
            for future in as_completed(futures):
                ws_name, ws_graph = future.result()
                console.print(f"  [cyan]Scanned workspace:[/cyan] {ws_name} ({len(ws_graph.nodes)} nodes)")
                new_graph.nodes.extend(ws_graph.nodes)
                new_graph.edges.extend(ws_graph.edges)

        root_scanner_cls = scanners.get(project_type, GeneralScanner)
        root_scanner = root_scanner_cls(storage, scan_path, changed_files=changed_set)
        root_graph = root_scanner.scan()
        existing_files = {n.file_path for n in new_graph.nodes if n.file_path}
        for node in root_graph.nodes:
            if node.file_path and node.file_path not in existing_files:
                new_graph.nodes.append(node)
                existing_files.add(node.file_path)
        new_graph.edges.extend(root_graph.edges)
    else:
        scanner_cls = scanners.get(project_type, GeneralScanner)
        scanner = scanner_cls(storage, scan_path, changed_files=changed_set)
        new_graph = scanner.scan()

    if changed_set:
        console.print(f"  [cyan]Incremental mode:[/cyan] scanned only {len(changed_set)} changed files")

    # ── Database introspection + SQL code analysis ─────────────
    db_stats = {"tables": 0, "fkeys": 0, "sql_usage_edges": 0}
    if enable_db or db_url:
        console.print()
        db_scanner = DatabaseScanner(scan_path, manual_url=db_url)
        db_graph = db_scanner.scan()

        if db_graph.nodes:
            existing_table_names = {
                n.metadata.get("table_name", n.name.removeprefix("Table: ")).lower()
                for n in new_graph.nodes
                if n.type.value == "db_table"
            }
            for node in db_graph.nodes:
                tname = node.metadata.get("table_name", node.name.removeprefix("Table: ")).lower()
                if tname not in existing_table_names:
                    new_graph.nodes.append(node)
                    storage.create_default_context(node)
                    existing_table_names.add(tname)
                    db_stats["tables"] += 1

            new_graph.edges.extend(db_graph.edges)
            db_stats["fkeys"] = len([e for e in db_graph.edges if e.type == EdgeType.FOREIGN_KEY])

        table_usage = getattr(db_graph, "metadata", {}).get("table_usage", {})
        if table_usage:
            db_table_index: dict[str, str] = {}
            for n in new_graph.nodes:
                if n.type == NodeType.DB_TABLE:
                    tname = n.metadata.get("table_name", n.name.removeprefix("Table: "))
                    db_table_index[tname.lower()] = n.id

            file_node_index: dict[str, str] = {}
            for n in new_graph.nodes:
                if n.file_path:
                    file_node_index[n.file_path] = n.id

            existing_edge_pairs = {(e.source, e.target) for e in new_graph.edges}

            for file_path, tables in table_usage.items():
                src_id = file_node_index.get(file_path)
                if not src_id:
                    continue
                for tname in tables:
                    tgt_id = db_table_index.get(tname.lower())
                    if tgt_id and src_id != tgt_id and (src_id, tgt_id) not in existing_edge_pairs:
                        new_graph.edges.append(Edge(
                            source=src_id,
                            target=tgt_id,
                            label=f"queries {tname}",
                            type=EdgeType.DATA_FLOW,
                        ))
                        existing_edge_pairs.add((src_id, tgt_id))
                        db_stats["sql_usage_edges"] += 1

    # ── Cross-cutting edge inference ─────────────────────────────
    console.print("\n[bold]Inferring cross-cutting relationships...[/bold]")
    edge_cache = storage.load_edge_cache() if diff_mode else {}
    inferrer = EdgeInferrer(scan_path, new_graph, edge_cache=edge_cache, changed_files=changed_set)
    inferred_count = inferrer.infer_all()
    storage.save_edge_cache(inferrer.get_updated_cache())
    console.print(f"  [green]Inferred {inferred_count} additional edges[/green] (API calls, renders, data-flow, imports)")

    # ── Merge into existing graph ────────────────────────────────
    existing_graph = storage.load_graph()

    if diff_mode:
        stats = _diff_merge(existing_graph, new_graph, storage, auto_remove=auto_remove, workspace=workspace)
        storage.save_graph(existing_graph)

        console.print(f"\n[green]Scan complete (diff mode):[/green]")
        console.print(f"  Added {stats['added']} new nodes")
        console.print(f"  Updated {stats['updated']} existing nodes")
        if stats["stale"]:
            console.print(f"  [yellow]Flagged {stats['stale']} stale nodes[/yellow] (run with --auto-remove to delete)")
        if stats["removed"]:
            console.print(f"  [red]Removed {stats['removed']} stale nodes[/red]")
        console.print(f"  Added {stats['edges_added']} new edges")
        if db_stats["tables"] or db_stats["sql_usage_edges"]:
            parts = []
            if db_stats["tables"]:
                parts.append(f"{db_stats['tables']} tables")
            if db_stats["fkeys"]:
                parts.append(f"{db_stats['fkeys']} foreign keys")
            if db_stats["sql_usage_edges"]:
                parts.append(f"{db_stats['sql_usage_edges']} code→table edges")
            console.print(f"  [cyan]Database:[/cyan] {', '.join(parts)}")
        console.print(f"  Total: {len(existing_graph.nodes)} nodes, {len(existing_graph.edges)} edges")
    else:
        existing_by_file = {n.file_path: n for n in existing_graph.nodes if n.file_path}
        id_remap: dict[str, str] = {}
        added = 0
        for node in new_graph.nodes:
            if node.file_path and node.file_path in existing_by_file:
                id_remap[node.id] = existing_by_file[node.file_path].id
                continue
            existing_graph.nodes.append(node)
            id_remap[node.id] = node.id
            added += 1

        valid_ids = {n.id for n in existing_graph.nodes}
        existing_pairs = {(e.source, e.target) for e in existing_graph.edges}
        edges_added = 0
        for edge in new_graph.edges:
            remapped_source = id_remap.get(edge.source, edge.source)
            remapped_target = id_remap.get(edge.target, edge.target)

            if remapped_source not in valid_ids or remapped_target not in valid_ids:
                continue

            key = (remapped_source, remapped_target)
            if key not in existing_pairs:
                edge.source = remapped_source
                edge.target = remapped_target
                existing_graph.edges.append(edge)
                existing_pairs.add(key)
                edges_added += 1

        storage.save_graph(existing_graph)

        console.print(f"\n[green]Scan complete:[/green]")
        console.print(f"  Added {added} new nodes ({len(new_graph.nodes)} discovered, {len(new_graph.nodes) - added} already existed)")
        console.print(f"  Added {edges_added} new edges")
        if db_stats["tables"] or db_stats["sql_usage_edges"]:
            parts = []
            if db_stats["tables"]:
                parts.append(f"{db_stats['tables']} tables")
            if db_stats["fkeys"]:
                parts.append(f"{db_stats['fkeys']} foreign keys")
            if db_stats["sql_usage_edges"]:
                parts.append(f"{db_stats['sql_usage_edges']} code→table edges")
            console.print(f"  [cyan]Database:[/cyan] {', '.join(parts)}")
        console.print(f"  Total: {len(existing_graph.nodes)} nodes, {len(existing_graph.edges)} edges")

    # ── Git-powered insights (hotspots + co-change edges) ─────────
    if enable_git:
        try:
            from ..git_integration import enrich_graph_with_git_insights, is_git_repo
            if is_git_repo(scan_path):
                console.print("\n[bold]Analyzing git history...[/bold]")
                git_stats = enrich_graph_with_git_insights(storage)
                if git_stats["hotspots"] or git_stats["co_change_edges"]:
                    parts = []
                    if git_stats["hotspots"]:
                        parts.append(f"{git_stats['hotspots']} nodes with change-frequency data")
                    if git_stats["co_change_edges"]:
                        parts.append(f"{git_stats['co_change_edges']} co-change edges")
                    console.print(f"  [magenta]Git insights:[/magenta] {', '.join(parts)}")
        except Exception:
            pass

    # ── Lighthouse performance auditing ────────────────────────────
    if enable_lighthouse:
        try:
            from .lighthouse import LighthouseScanner
            lh = LighthouseScanner(scan_path, base_url=lighthouse_base_url)
            lh.audit(existing_graph)
            storage.save_graph(existing_graph)
        except Exception as exc:
            console.print(f"[yellow]Lighthouse audit error: {exc}[/yellow]")
