"""Real-time file system watcher for live graph updates."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .analysis import analyze_impact
from .models import GraphData, Node, NodeType, Layer
from .storage import Storage

logger = logging.getLogger("architect.watcher")

SCANNABLE_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".pyw",
    ".vue", ".svelte",
    ".sql", ".prisma",
    ".php",
    ".cs", ".razor", ".cshtml",
    ".go", ".rs", ".java", ".rb",
    ".html", ".htm", ".css", ".scss", ".less",
    ".json", ".yaml", ".yml", ".toml", ".xml",
    ".graphql", ".gql",
    ".md", ".mdx",
    ".env", ".sh", ".bash",
}

EXCLUDED_DIRS = {
    ".architect", "node_modules", ".git", "__pycache__",
    "dist", "build", ".next", ".nuxt", ".svelte-kit",
    "venv", ".venv", "env", ".env",
    "vendor", "bin", "obj", "target",
    ".cache", ".turbo", "coverage",
}


class FileWatcher:
    """Watches the project directory for file changes and maintains touched state."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.touched_files: set[str] = set()
        self.deleted_files: set[str] = set()
        self.new_files: set[str] = set()
        self.event_log: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []
        self._task: Optional[asyncio.Task] = None

    def _should_ignore(self, path: Path) -> bool:
        parts = path.relative_to(self.storage.project_root).parts
        for part in parts:
            if part in EXCLUDED_DIRS:
                return True
        return False

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.storage.project_root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def _broadcast(self, event: dict) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def _match_file_to_node(self, rel_path: str) -> Optional[Node]:
        graph = self.storage.load_graph()
        node = graph.find_node_by_file(rel_path)
        if not node:
            normalized = rel_path.replace("\\", "/")
            for n in graph.nodes:
                if n.file_path and (
                    normalized.endswith(n.file_path)
                    or n.file_path.endswith(normalized)
                ):
                    node = n
                    break
        return node

    def get_touched_state(self) -> dict:
        graph = self.storage.load_graph()

        touched_node_ids: list[str] = []
        for fpath in self.touched_files:
            node = graph.find_node_by_file(fpath)
            if not node:
                normalized = fpath.replace("\\", "/")
                for n in graph.nodes:
                    if n.file_path and (
                        normalized.endswith(n.file_path)
                        or n.file_path.endswith(normalized)
                    ):
                        node = n
                        break
            if node and node.id not in touched_node_ids:
                touched_node_ids.append(node.id)

        impacted_nodes: dict[str, dict] = {}
        for nid in touched_node_ids:
            result = analyze_impact(graph, nid)
            if not result:
                continue
            for imp_node, depth in result.impacted:
                if imp_node.id in touched_node_ids:
                    continue
                existing = impacted_nodes.get(imp_node.id)
                if existing is None:
                    impacted_nodes[imp_node.id] = {
                        "depth": depth,
                        "touched_sources": [nid],
                        "name": imp_node.name,
                    }
                else:
                    if depth < existing["depth"]:
                        existing["depth"] = depth
                    if nid not in existing["touched_sources"]:
                        existing["touched_sources"].append(nid)

        return {
            "touched_nodes": touched_node_ids,
            "impacted_nodes": impacted_nodes,
            "new_files": sorted(self.new_files),
            "deleted_files": sorted(self.deleted_files),
            "event_count": len(self.event_log),
        }

    async def _handle_change(self, change_type: str, path_str: str) -> None:
        path = Path(path_str)

        if self._should_ignore(path):
            return

        rel_path = self._relative_path(path)
        ext = path.suffix.lower()

        event = {
            "type": "file_changed",
            "change_type": change_type,
            "file_path": rel_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "graph_changed": False,
            "node_id": None,
        }

        if change_type == "deleted":
            self.deleted_files.add(rel_path)
            self.touched_files.discard(rel_path)
            node = self._match_file_to_node(rel_path)
            if node:
                event["node_id"] = node.id
                graph = self.storage.load_graph()
                g_node = graph.get_node(node.id)
                if g_node and "stale" not in g_node.tags:
                    g_node.tags.append("stale")
                    g_node.touch()
                    self.storage.save_graph(graph)
                    event["graph_changed"] = True

        elif change_type == "added":
            self.new_files.add(rel_path)
            self.touched_files.add(rel_path)
            node = self._match_file_to_node(rel_path)
            if node:
                event["node_id"] = node.id
            elif ext in SCANNABLE_EXTENSIONS:
                new_node = self._create_node_for_file(rel_path, path)
                if new_node:
                    event["node_id"] = new_node.id
                    event["graph_changed"] = True

        else:
            self.touched_files.add(rel_path)
            node = self._match_file_to_node(rel_path)
            if node:
                event["node_id"] = node.id
                event["graph_changed"] = True
                graph = self.storage.load_graph()
                g_node = graph.get_node(node.id)
                if g_node:
                    g_node.touch()
                    self.storage.save_graph(graph)

        self.event_log.append(event)
        if len(self.event_log) > 500:
            self.event_log = self.event_log[-250:]

        await self._broadcast(event)

    def _create_node_for_file(self, rel_path: str, abs_path: Path) -> Optional[Node]:
        from .scanner.general import FILE_TYPE_MAP, LAYER_HINTS

        ext = abs_path.suffix.lower()
        node_type = FILE_TYPE_MAP.get(ext, NodeType.UTILITY)

        layer = Layer.SHARED
        rel_lower = rel_path.lower().replace("\\", "/")
        for layer_name, hints in LAYER_HINTS.items():
            for hint in hints:
                if hint in rel_lower:
                    layer = Layer(layer_name)
                    break

        name = abs_path.stem
        if ext in {".tsx", ".jsx", ".vue", ".svelte"}:
            name = name[0].upper() + name[1:] if name else name
            node_type = NodeType.COMPONENT

        parts = rel_path.replace("\\", "/").split("/")
        path_parts = parts[:-1]
        for part in path_parts:
            low = part.lower()
            if low in {"pages", "app", "views"}:
                node_type = NodeType.PAGE
            elif low in {"api", "routes", "controllers"}:
                node_type = NodeType.API_ENDPOINT
            elif low in {"services"}:
                node_type = NodeType.SERVICE
            elif low in {"middleware"}:
                node_type = NodeType.MIDDLEWARE
            elif low in {"models", "entities", "schemas"}:
                node_type = NodeType.DB_TABLE

        node = Node(
            name=name,
            type=node_type,
            layer=layer,
            summary=f"Auto-detected from {rel_path}",
            file_path=rel_path,
            status="planned",
        )

        graph = self.storage.load_graph()
        graph.nodes.append(node)
        self.storage.save_graph(graph)
        self.storage.create_default_context(node)

        logger.info("Created node %s for new file %s", node.id, rel_path)
        return node

    def _scan_snapshot(self, root: Path) -> dict[str, float]:
        """Build a dict of {relative_path: mtime} for all relevant files."""
        snapshot: dict[str, float] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if d not in EXCLUDED_DIRS
            ]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in SCANNABLE_EXTENSIONS:
                    continue
                full = Path(dirpath) / fname
                try:
                    snapshot[str(full)] = full.stat().st_mtime
                except OSError:
                    pass
        return snapshot

    async def _poll_watch(self) -> None:
        """Fallback polling watcher when watchfiles is unavailable."""
        root = self.storage.project_root
        poll_interval = 2.0
        logger.info("Starting POLLING file watcher on %s (interval=%.1fs)", root, poll_interval)

        prev = self._scan_snapshot(root)

        try:
            while True:
                await asyncio.sleep(poll_interval)
                curr = self._scan_snapshot(root)

                added = set(curr.keys()) - set(prev.keys())
                deleted = set(prev.keys()) - set(curr.keys())
                possibly_modified = set(curr.keys()) & set(prev.keys())

                for p in added:
                    try:
                        await self._handle_change("added", p)
                    except Exception:
                        logger.exception("Error handling polled add: %s", p)

                for p in deleted:
                    try:
                        await self._handle_change("deleted", p)
                    except Exception:
                        logger.exception("Error handling polled delete: %s", p)

                for p in possibly_modified:
                    if curr[p] != prev[p]:
                        try:
                            await self._handle_change("modified", p)
                        except Exception:
                            logger.exception("Error handling polled modify: %s", p)

                prev = curr
        except asyncio.CancelledError:
            logger.info("Polling file watcher stopped")
        except Exception:
            logger.exception("Polling file watcher crashed")

    async def watch(self) -> None:
        try:
            from watchfiles import awatch, Change
            _has_watchfiles = True
        except ImportError:
            _has_watchfiles = False

        if not _has_watchfiles:
            logger.warning("watchfiles not available; falling back to polling watcher")
            await self._poll_watch()
            return

        root = self.storage.project_root

        def _filter(change: Change, path: str) -> bool:
            try:
                p = Path(path)
                if self._should_ignore(p):
                    return False
                if p.suffix.lower() not in SCANNABLE_EXTENSIONS:
                    return False
                return True
            except Exception:
                return False

        logger.info("Starting native file watcher on %s", root)

        try:
            async for changes in awatch(root, watch_filter=_filter, debounce=800):
                for change_enum, path_str in changes:
                    if change_enum == Change.added:
                        change_type = "added"
                    elif change_enum == Change.deleted:
                        change_type = "deleted"
                    else:
                        change_type = "modified"

                    try:
                        await self._handle_change(change_type, path_str)
                    except Exception:
                        logger.exception("Error handling file change: %s %s", change_type, path_str)
        except asyncio.CancelledError:
            logger.info("File watcher stopped")
        except Exception:
            logger.exception("Native file watcher crashed; falling back to polling")
            await self._poll_watch()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self.watch())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
