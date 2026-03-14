"""FastAPI web server for the Architect graph viewer."""

from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import markdown
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..models import ADR, ADRStatus, Edge, EdgeType, GraphData, Layer, Node, NodeStatus, NodeType
from ..storage import Storage
from ..watcher import FileWatcher

logger = logging.getLogger("architect.viewer")


def _get_static_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "architect_static"  # type: ignore[attr-defined]
    return Path(__file__).parent / "static"


STATIC_DIR = _get_static_dir()


class NodeCreate(BaseModel):
    name: str
    type: str = "custom"
    layer: str = "shared"
    summary: str = ""
    file_path: Optional[str] = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    position_x: float = 0
    position_y: float = 0


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    layer: Optional[str] = None
    type: Optional[str] = None
    file_path: Optional[str] = None
    tags: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None


class EdgeCreate(BaseModel):
    source: str
    target: str
    label: str = ""
    type: str = "dependency"


class EdgeUpdate(BaseModel):
    label: Optional[str] = None
    type: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None


class ContextUpdate(BaseModel):
    content: str


class FileUpdate(BaseModel):
    content: str


class ChatRequest(BaseModel):
    message: str
    referenced_files: list[str] = []
    node_id: Optional[str] = None
    history: list[dict] = []


class ChatStopRequest(BaseModel):
    session_id: Optional[str] = None


class ADRCreate(BaseModel):
    title: str
    status: str = "proposed"
    context: str = ""
    decision: str = ""
    consequences: str = ""
    linked_nodes: list[str] = []


class ADRUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    context: Optional[str] = None
    decision: Optional[str] = None
    consequences: Optional[str] = None
    linked_nodes: Optional[list[str]] = None


def create_app(storage: Storage) -> FastAPI:
    watcher = FileWatcher(storage)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        watcher.start()
        yield
        watcher.stop()

    api = FastAPI(title="Architect Viewer", lifespan=lifespan)

    @api.get("/", response_class=HTMLResponse)
    async def index():
        html_path = STATIC_DIR / "index.html"
        return html_path.read_text(encoding="utf-8")

    @api.get("/app.js")
    async def app_js():
        js_path = STATIC_DIR / "app.js"
        return HTMLResponse(
            content=js_path.read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @api.get("/style.css")
    async def style_css():
        css_path = STATIC_DIR / "style.css"
        return HTMLResponse(
            content=css_path.read_text(encoding="utf-8"),
            media_type="text/css",
        )

    @api.get("/api/graph")
    async def get_graph():
        graph = storage.load_graph()
        nodes = []
        for n in graph.nodes:
            nodes.append({
                "data": {
                    "id": n.id,
                    "label": n.name,
                    "type": n.type.value,
                    "layer": n.layer.value,
                    "status": n.status.value,
                    "summary": n.summary,
                    "file_path": n.file_path or "",
                    "tags": n.tags,
                    "metadata": n.metadata,
                    "workspace": n.workspace or "",
                    "parent": n.layer.value,
                },
                "position": {"x": n.position.x, "y": n.position.y},
            })
        edges = []
        for e in graph.edges:
            edges.append({
                "data": {
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "label": e.label,
                    "type": e.type.value,
                    "deprecated": e.deprecated,
                }
            })

        layers = []
        used_layers = {n.layer.value for n in graph.nodes}
        for layer_val in used_layers:
            layers.append({
                "data": {"id": layer_val, "label": layer_val.replace("_", " ").title()},
            })

        return {"nodes": layers + nodes, "edges": edges}

    @api.get("/api/nodes")
    async def list_nodes(
        layer: Optional[str] = None,
        type: Optional[str] = None,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        workspace: Optional[str] = None,
    ):
        graph = storage.load_graph()
        nodes = graph.nodes
        if layer:
            nodes = [n for n in nodes if n.layer.value == layer]
        if type:
            nodes = [n for n in nodes if n.type.value == type]
        if status:
            nodes = [n for n in nodes if n.status.value == status]
        if tag:
            nodes = [n for n in nodes if tag in n.tags]
        if workspace:
            nodes = [n for n in nodes if n.workspace == workspace]
        return [n.model_dump(mode="json") for n in nodes]

    @api.get("/api/node/{node_id}")
    async def get_node(node_id: str):
        graph = storage.load_graph()
        node = graph.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        ctx_raw = storage.load_context(node_id)
        ctx_html = markdown.markdown(ctx_raw) if ctx_raw else ""
        return {
            **node.model_dump(mode="json"),
            "context_raw": ctx_raw,
            "context_html": ctx_html,
        }

    @api.post("/api/node")
    async def create_node(body: NodeCreate):
        graph = storage.load_graph()
        from ..models import Position
        node = Node(
            name=body.name,
            type=NodeType(body.type),
            layer=Layer(body.layer),
            summary=body.summary,
            file_path=body.file_path,
            tags=body.tags,
            metadata=body.metadata,
            position=Position(x=body.position_x, y=body.position_y),
        )
        graph.nodes.append(node)
        storage.save_graph(graph)
        storage.create_default_context(node)
        return node.model_dump(mode="json")

    @api.put("/api/node/{node_id}")
    async def update_node(node_id: str, body: NodeUpdate):
        graph = storage.load_graph()
        node = graph.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        if body.name is not None:
            node.name = body.name
        if body.summary is not None:
            node.summary = body.summary
        if body.status is not None:
            node.status = NodeStatus(body.status)
        if body.layer is not None:
            node.layer = Layer(body.layer)
        if body.type is not None:
            node.type = NodeType(body.type)
        if body.file_path is not None:
            node.file_path = body.file_path
        if body.tags is not None:
            node.tags = body.tags
        if body.metadata is not None:
            node.metadata.update(body.metadata)
        if body.position_x is not None:
            node.position.x = body.position_x
        if body.position_y is not None:
            node.position.y = body.position_y
        node.touch()
        storage.save_graph(graph)
        return node.model_dump(mode="json")

    @api.delete("/api/node/{node_id}")
    async def delete_node(node_id: str):
        graph = storage.load_graph()
        node = graph.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        if node.status == NodeStatus.PLANNED:
            graph.remove_node(node_id)
            storage.save_graph(graph)
            storage.delete_context(node_id)
            return {"action": "hard_deleted", "node_id": node_id}
        else:
            graph.soft_delete_node(node_id)
            storage.save_graph(graph)
            return {"action": "soft_deleted", "node_id": node_id, "status": "deprecated"}

    @api.delete("/api/node/{node_id}/permanent")
    async def permanent_delete_node(node_id: str):
        graph = storage.load_graph()
        if not graph.remove_node(node_id):
            raise HTTPException(404, f"Node {node_id} not found")
        storage.save_graph(graph)
        storage.delete_context(node_id)
        return {"action": "hard_deleted", "node_id": node_id}

    @api.post("/api/node/{node_id}/restore")
    async def restore_node(node_id: str):
        graph = storage.load_graph()
        if not graph.restore_node(node_id):
            raise HTTPException(404, f"Node {node_id} not found or not deprecated")
        storage.save_graph(graph)
        return {"action": "restored", "node_id": node_id}

    @api.get("/api/context/{node_id}")
    async def get_context(node_id: str):
        graph = storage.load_graph()
        if not graph.get_node(node_id):
            raise HTTPException(404, f"Node {node_id} not found")
        raw = storage.load_context(node_id)
        html = markdown.markdown(raw) if raw else ""
        return {"node_id": node_id, "raw": raw, "html": html}

    @api.put("/api/context/{node_id}")
    async def update_context(node_id: str, body: ContextUpdate):
        graph = storage.load_graph()
        node = graph.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        storage.save_context(node_id, body.content)
        node.touch()
        storage.save_graph(graph)
        return {"node_id": node_id, "updated": True}

    @api.post("/api/edge")
    async def create_edge(body: EdgeCreate):
        graph = storage.load_graph()
        if not graph.get_node(body.source):
            raise HTTPException(400, f"Source node {body.source} not found")
        if not graph.get_node(body.target):
            raise HTTPException(400, f"Target node {body.target} not found")
        edge = Edge(
            source=body.source,
            target=body.target,
            label=body.label,
            type=EdgeType(body.type),
        )
        graph.edges.append(edge)
        storage.save_graph(graph)
        return edge.model_dump(mode="json")

    @api.put("/api/edge/{edge_id}")
    async def update_edge(edge_id: str, body: EdgeUpdate):
        graph = storage.load_graph()
        edge = graph.get_edge(edge_id)
        if not edge:
            raise HTTPException(404, f"Edge {edge_id} not found")
        if body.label is not None:
            edge.label = body.label
        if body.type is not None:
            edge.type = EdgeType(body.type)
        if body.source is not None:
            if not graph.get_node(body.source):
                raise HTTPException(400, f"Source node {body.source} not found")
            edge.source = body.source
        if body.target is not None:
            if not graph.get_node(body.target):
                raise HTTPException(400, f"Target node {body.target} not found")
            edge.target = body.target
        storage.save_graph(graph)
        return edge.model_dump(mode="json")

    @api.delete("/api/edge/{edge_id}")
    async def delete_edge(edge_id: str):
        graph = storage.load_graph()
        edge = graph.get_edge(edge_id)
        if not edge:
            raise HTTPException(404, f"Edge {edge_id} not found")
        src = graph.get_node(edge.source)
        tgt = graph.get_node(edge.target)
        both_planned = (
            (src and src.status == NodeStatus.PLANNED) and
            (tgt and tgt.status == NodeStatus.PLANNED)
        )
        if both_planned or (not src and not tgt):
            graph.remove_edge(edge_id)
            storage.save_graph(graph)
            return {"action": "hard_deleted", "edge_id": edge_id}
        else:
            graph.soft_delete_edge(edge_id)
            storage.save_graph(graph)
            return {"action": "soft_deleted", "edge_id": edge_id}

    @api.delete("/api/edge/{edge_id}/permanent")
    async def permanent_delete_edge(edge_id: str):
        graph = storage.load_graph()
        if not graph.remove_edge(edge_id):
            raise HTTPException(404, f"Edge {edge_id} not found")
        storage.save_graph(graph)
        return {"action": "hard_deleted", "edge_id": edge_id}

    @api.post("/api/edge/{edge_id}/restore")
    async def restore_edge(edge_id: str):
        graph = storage.load_graph()
        if not graph.restore_edge(edge_id):
            raise HTTPException(404, f"Edge {edge_id} not found or not deprecated")
        storage.save_graph(graph)
        return {"action": "restored", "edge_id": edge_id}

    @api.get("/api/stats")
    async def get_stats():
        graph = storage.load_graph()
        config = storage.load_config()
        return {
            "project_name": config.name,
            "project_description": config.description,
            "project_type": config.project_type,
            "total_nodes": len(graph.nodes),
            "total_edges": len(graph.edges),
            "by_layer": {l.value: len(graph.find_nodes(layer=l)) for l in Layer},
            "by_type": {t.value: len(graph.find_nodes(node_type=t)) for t in NodeType if graph.find_nodes(node_type=t)},
            "by_status": {s.value: len(graph.find_nodes(status=s)) for s in NodeStatus if graph.find_nodes(status=s)},
            "workspaces": graph.workspaces(),
            "stale_count": len([n for n in graph.nodes if "stale" in n.tags]),
        }

    @api.post("/api/scan")
    async def trigger_scan():
        from ..scanner.detector import detect_and_scan
        detect_and_scan(storage, storage.project_root)
        graph = storage.load_graph()
        return {"nodes": len(graph.nodes), "edges": len(graph.edges)}

    @api.put("/api/node/{node_id}/position")
    async def update_position(node_id: str, x: float, y: float):
        graph = storage.load_graph()
        node = graph.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        node.position.x = x
        node.position.y = y
        storage.save_graph(graph)
        return {"ok": True}

    @api.get("/api/impact/{node_id}")
    async def get_impact(node_id: str, depth: int = 10):
        graph = storage.load_graph()
        from ..analysis import analyze_impact
        result = analyze_impact(graph, node_id, max_depth=depth)
        if not result:
            raise HTTPException(404, f"Node {node_id} not found")
        return {
            "source": result.source_node.model_dump(mode="json"),
            "impacted": [
                {"node": n.model_dump(mode="json"), "depth": d}
                for n, d in result.impacted
            ],
            "total": result.total_impacted,
        }

    # ── ADR endpoints ───────────────────────────────────────────

    @api.get("/api/adrs")
    async def list_adrs():
        adrs = storage.list_adrs()
        return [a.model_dump(mode="json") for a in adrs]

    @api.get("/api/adr/{adr_id}")
    async def get_adr(adr_id: str):
        adr = storage.load_adr(adr_id)
        if not adr:
            raise HTTPException(404, f"ADR {adr_id} not found")
        return adr.model_dump(mode="json")

    @api.post("/api/adr")
    async def create_adr(body: ADRCreate):
        adr = ADR(
            title=body.title,
            status=ADRStatus(body.status),
            context=body.context,
            decision=body.decision,
            consequences=body.consequences,
            linked_nodes=body.linked_nodes,
        )
        storage.save_adr(adr)
        return adr.model_dump(mode="json")

    @api.put("/api/adr/{adr_id}")
    async def update_adr(adr_id: str, body: ADRUpdate):
        adr = storage.load_adr(adr_id)
        if not adr:
            raise HTTPException(404, f"ADR {adr_id} not found")
        if body.title is not None:
            adr.title = body.title
        if body.status is not None:
            adr.status = ADRStatus(body.status)
        if body.context is not None:
            adr.context = body.context
        if body.decision is not None:
            adr.decision = body.decision
        if body.consequences is not None:
            adr.consequences = body.consequences
        if body.linked_nodes is not None:
            adr.linked_nodes = body.linked_nodes
        adr.touch()
        storage.save_adr(adr)
        return adr.model_dump(mode="json")

    @api.delete("/api/adr/{adr_id}")
    async def delete_adr(adr_id: str):
        if not storage.delete_adr(adr_id):
            raise HTTPException(404, f"ADR {adr_id} not found")
        return {"deleted": adr_id}

    # ── File viewer endpoint ───────────────────────────────────

    EXT_LANG_MAP = {
        ".py": "python", ".pyw": "python",
        ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx",
        ".html": "html", ".htm": "html",
        ".css": "css", ".scss": "scss", ".less": "less",
        ".json": "json", ".jsonc": "json",
        ".md": "markdown", ".mdx": "markdown",
        ".sql": "sql",
        ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".sh": "bash", ".bash": "bash", ".zsh": "bash",
        ".ps1": "powershell",
        ".go": "go", ".rs": "rust", ".java": "java",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
        ".rb": "ruby", ".php": "php",
        ".vue": "html", ".svelte": "html",
        ".prisma": "graphql", ".graphql": "graphql", ".gql": "graphql",
        ".env": "bash", ".gitignore": "bash",
        ".dockerfile": "docker", ".ini": "ini", ".cfg": "ini",
    }

    @api.get("/api/file/{node_id}")
    async def get_file(node_id: str):
        graph = storage.load_graph()
        node = graph.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        if not node.file_path:
            raise HTTPException(404, f"Node {node_id} has no associated file")

        normalized = node.file_path.replace("\\", "/").lstrip("/")
        resolved = (storage.project_root / normalized).resolve()

        if not resolved.is_file():
            resolved = (storage.project_root / node.file_path).resolve()
        if not resolved.is_file():
            raise HTTPException(
                404,
                f"File not found: {node.file_path} "
                f"(looked in {storage.project_root})",
            )

        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(500, f"Cannot read file: {exc}")

        ext = resolved.suffix.lower()
        language = EXT_LANG_MAP.get(ext, "plain")
        if ext == "" and resolved.name.lower() == "dockerfile":
            language = "docker"

        return {
            "file_path": node.file_path,
            "language": language,
            "content": content,
        }

    @api.put("/api/file/{node_id}")
    async def update_file(node_id: str, body: FileUpdate):
        graph = storage.load_graph()
        node = graph.get_node(node_id)
        if not node:
            raise HTTPException(404, f"Node {node_id} not found")
        if not node.file_path:
            raise HTTPException(404, f"Node {node_id} has no associated file")

        normalized = node.file_path.replace("\\", "/").lstrip("/")
        resolved = (storage.project_root / normalized).resolve()

        if not resolved.is_file():
            resolved = (storage.project_root / node.file_path).resolve()
        if not resolved.is_file():
            raise HTTPException(
                404,
                f"File not found: {node.file_path} "
                f"(looked in {storage.project_root})",
            )

        project_root = storage.project_root.resolve()
        if not str(resolved).startswith(str(project_root)):
            raise HTTPException(403, "Cannot write to files outside the project root")

        try:
            resolved.write_text(body.content, encoding="utf-8")
        except Exception as exc:
            raise HTTPException(500, f"Cannot write file: {exc}")

        return {"ok": True, "file_path": node.file_path}

    # ── Git dirty-nodes endpoint ─────────────────────────────

    @api.get("/api/git/dirty")
    async def get_git_dirty():
        from ..git_integration import is_git_repo, get_changed_files
        from ..analysis import analyze_impact

        root = storage.project_root
        if not is_git_repo(root):
            return {
                "is_git_repo": False,
                "dirty_nodes": [],
                "dirty_files": [],
                "impacted_nodes": {},
                "untracked_files": [],
            }

        changed = get_changed_files(root)
        graph = storage.load_graph()

        dirty_node_ids: list[str] = []
        dirty_files: list[str] = []
        untracked: list[str] = []

        for fpath in changed:
            node = graph.find_node_by_file(fpath)
            if not node:
                normalized = fpath.replace("\\", "/")
                for n in graph.nodes:
                    if n.file_path and (
                        normalized.endswith(n.file_path) or n.file_path.endswith(normalized)
                    ):
                        node = n
                        break

            if node:
                if node.id not in dirty_node_ids:
                    dirty_node_ids.append(node.id)
                dirty_files.append(fpath)
            else:
                untracked.append(fpath)

        impacted_nodes: dict[str, dict] = {}
        for nid in dirty_node_ids:
            result = analyze_impact(graph, nid)
            if not result:
                continue
            for imp_node, depth in result.impacted:
                if imp_node.id in dirty_node_ids:
                    continue
                existing = impacted_nodes.get(imp_node.id)
                if existing is None:
                    impacted_nodes[imp_node.id] = {
                        "depth": depth,
                        "dirty_sources": [nid],
                        "name": imp_node.name,
                    }
                else:
                    if depth < existing["depth"]:
                        existing["depth"] = depth
                    if nid not in existing["dirty_sources"]:
                        existing["dirty_sources"].append(nid)

        return {
            "is_git_repo": True,
            "dirty_nodes": dirty_node_ids,
            "dirty_files": dirty_files,
            "impacted_nodes": impacted_nodes,
            "untracked_files": untracked,
        }

    # ── Git changed-only subgraph endpoint ──────────────────

    @api.get("/api/git/changed-graph")
    async def get_git_changed_graph():
        """Return a Cytoscape-formatted subgraph containing only nodes whose
        files have uncommitted changes plus their 1-hop neighbors and the
        edges connecting them."""
        from ..git_integration import is_git_repo, get_changed_files

        root = storage.project_root
        if not is_git_repo(root):
            return {"nodes": [], "edges": [], "is_git_repo": False,
                    "dirty_node_ids": [], "neighbor_node_ids": []}

        changed = get_changed_files(root)
        graph = storage.load_graph()

        dirty_node_ids: list[str] = []
        for fpath in changed:
            node = graph.find_node_by_file(fpath)
            if not node:
                normalized = fpath.replace("\\", "/")
                for n in graph.nodes:
                    if n.file_path and (
                        normalized.endswith(n.file_path) or n.file_path.endswith(normalized)
                    ):
                        node = n
                        break
            if node and node.id not in dirty_node_ids:
                dirty_node_ids.append(node.id)

        dirty_set = set(dirty_node_ids)

        neighbor_ids: set[str] = set()
        for edge in graph.edges:
            if edge.deprecated:
                continue
            if edge.source in dirty_set and edge.target not in dirty_set:
                neighbor_ids.add(edge.target)
            elif edge.target in dirty_set and edge.source not in dirty_set:
                neighbor_ids.add(edge.source)

        visible_ids = dirty_set | neighbor_ids

        nodes = []
        used_layers: set[str] = set()
        for n in graph.nodes:
            if n.id not in visible_ids:
                continue
            used_layers.add(n.layer.value)
            nodes.append({
                "data": {
                    "id": n.id,
                    "label": n.name,
                    "type": n.type.value,
                    "layer": n.layer.value,
                    "status": n.status.value,
                    "summary": n.summary,
                    "file_path": n.file_path or "",
                    "tags": n.tags,
                    "metadata": n.metadata,
                    "workspace": n.workspace or "",
                    "parent": n.layer.value,
                },
                "position": {"x": n.position.x, "y": n.position.y},
            })

        layers = []
        for layer_val in used_layers:
            layers.append({
                "data": {"id": layer_val, "label": layer_val.replace("_", " ").title()},
            })

        edges = []
        for e in graph.edges:
            if e.source in visible_ids and e.target in visible_ids:
                edges.append({
                    "data": {
                        "id": e.id,
                        "source": e.source,
                        "target": e.target,
                        "label": e.label,
                        "type": e.type.value,
                        "deprecated": e.deprecated,
                    }
                })

        return {
            "nodes": layers + nodes,
            "edges": edges,
            "is_git_repo": True,
            "dirty_node_ids": dirty_node_ids,
            "neighbor_node_ids": sorted(neighbor_ids),
        }

    # ── WebSocket for live file watching ─────────────────────

    @api.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        queue = watcher.subscribe()
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            watcher.unsubscribe(queue)
        except Exception:
            watcher.unsubscribe(queue)

    @api.get("/api/touched")
    async def get_touched():
        return watcher.get_touched_state()

    @api.delete("/api/touched")
    async def clear_touched():
        watcher.touched_files.clear()
        watcher.deleted_files.clear()
        watcher.new_files.clear()
        watcher.event_log.clear()
        return {"cleared": True}

    @api.get("/api/export/{fmt}")
    async def export_graph(fmt: str, direction: str = "TD"):
        graph = storage.load_graph()
        if fmt == "mermaid":
            from ..exporters import to_mermaid
            return HTMLResponse(content=to_mermaid(graph, direction=direction), media_type="text/plain")
        elif fmt == "plantuml":
            from ..exporters import to_plantuml
            return HTMLResponse(content=to_plantuml(graph), media_type="text/plain")
        elif fmt == "json":
            import json as json_mod
            return HTMLResponse(
                content=json_mod.dumps(graph.model_dump(mode="json"), indent=2, default=str),
                media_type="application/json",
            )
        raise HTTPException(400, f"Unsupported format: {fmt}")

    # ── Vibe Code chat endpoints ──────────────────────────────

    _active_chat_procs: dict[str, asyncio.subprocess.Process] = {}

    SKIP_DIRS = {
        ".git", "node_modules", "__pycache__", ".architect", "venv",
        ".venv", "env", ".env", "dist", "build", ".next", ".cache",
        ".idea", ".vscode", ".cursor", "coverage", ".tox", "egg-info",
    }

    def _build_graph_summary(graph: GraphData) -> str:
        lines = ["Nodes:"]
        node_map: dict[str, str] = {}
        for n in graph.nodes:
            node_map[n.id] = n.name
            lines.append(
                f"- {n.name} ({n.type.value}, {n.layer.value}) "
                f"[{n.status.value}] -> {n.file_path or '(no file)'}"
            )
        lines.append("\nEdges:")
        for e in graph.edges:
            src = node_map.get(e.source, e.source)
            tgt = node_map.get(e.target, e.target)
            lines.append(f"- {src} --[{e.label or e.type.value}]--> {tgt} ({e.type.value})")
        return "\n".join(lines)

    def _read_file_safe(rel_path: str) -> Optional[str]:
        normalized = rel_path.replace("\\", "/").lstrip("/")
        resolved = (storage.project_root / normalized).resolve()
        project_root = storage.project_root.resolve()
        if not str(resolved).startswith(str(project_root)):
            return None
        if not resolved.is_file():
            return None
        try:
            return resolved.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def _detect_lang(fpath: str) -> str:
        ext = Path(fpath).suffix.lower()
        return EXT_LANG_MAP.get(ext, "plain")

    _login_proc: dict[str, Any] = {}

    async def _get_agent_status() -> dict:
        """Return CLI install/auth status without raising exceptions."""
        import shutil

        agent_cmd = shutil.which("agent")
        if not agent_cmd:
            return {"installed": False, "authenticated": False, "user": None, "agent_cmd": None}

        try:
            proc = await asyncio.create_subprocess_exec(
                agent_cmd, "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            combined = (stdout + stderr).decode("utf-8", errors="replace")
            combined_lower = combined.lower()

            import re

            # Check positive signals first (these appear even alongside "authentication status")
            if "logged in as" in combined_lower or "\u2713" in combined:
                email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", combined)
                user = email_match.group(0) if email_match else "authenticated user"
                return {"installed": True, "authenticated": True, "user": user, "agent_cmd": agent_cmd}

            # Check negative signals
            if "not logged in" in combined_lower or "please log in" in combined_lower or "not authenticated" in combined_lower:
                return {"installed": True, "authenticated": False, "user": None, "agent_cmd": agent_cmd}

            # No clear signal -- check for email as a heuristic
            email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", combined)
            if email_match:
                return {"installed": True, "authenticated": True, "user": email_match.group(0), "agent_cmd": agent_cmd}

            return {"installed": True, "authenticated": False, "user": None, "agent_cmd": agent_cmd}
        except asyncio.TimeoutError:
            return {"installed": True, "authenticated": False, "user": None, "agent_cmd": agent_cmd}
        except Exception:
            return {"installed": True, "authenticated": False, "user": None, "agent_cmd": agent_cmd}

    async def _check_agent_cli() -> str:
        """Verify Cursor CLI is installed and authenticated. Returns the
        command path or raises HTTPException with a helpful message."""
        status = await _get_agent_status()

        if not status["installed"]:
            raise HTTPException(503, "cursor_cli_not_installed")
        if not status["authenticated"]:
            raise HTTPException(401, "cursor_cli_not_authenticated")

        return status["agent_cmd"]

    @api.get("/api/chat/status")
    async def chat_status():
        status = await _get_agent_status()
        return {
            "installed": status["installed"],
            "authenticated": status["authenticated"],
            "user": status["user"],
        }

    @api.post("/api/chat/login")
    async def chat_login():
        import shutil

        agent_cmd = shutil.which("agent")
        if not agent_cmd:
            raise HTTPException(503, "Cursor CLI not found. Install from https://cursor.com/cli")

        if _login_proc.get("active"):
            return {"status": "already_in_progress"}

        try:
            proc = await asyncio.create_subprocess_exec(
                agent_cmd, "login",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _login_proc["active"] = True
            _login_proc["proc"] = proc

            async def _wait_login():
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=120)
                except asyncio.TimeoutError:
                    proc.terminate()
                finally:
                    _login_proc["active"] = False
                    _login_proc.pop("proc", None)

            asyncio.create_task(_wait_login())
            return {"status": "login_started"}
        except Exception as exc:
            _login_proc["active"] = False
            raise HTTPException(500, f"Failed to start login: {exc}")

    @api.post("/api/chat")
    async def chat_stream(body: ChatRequest, request: Request):
        import json as json_mod
        import uuid

        session_id = str(uuid.uuid4())
        agent_cmd = await _check_agent_cli()

        config = storage.load_config()
        graph_file = storage.project_root / ".architect" / "graph.json"

        prompt_parts = [
            f'You are an AI coding assistant for "{config.name}" ({config.description}).',
            f"The architecture graph is at {graph_file.as_posix()} -- read it if you need project structure or node details.",
        ]

        if body.node_id:
            graph = storage.load_graph()
            node = graph.get_node(body.node_id)
            if node and node.file_path:
                prompt_parts.append(f"The user is viewing: {node.file_path}")

        if body.referenced_files:
            paths = ", ".join(body.referenced_files)
            prompt_parts.append(f"Referenced files: {paths}")

        if body.history:
            prompt_parts.append("Previous conversation:")
            for msg in body.history[-10:]:
                role = msg.get("role", "user").upper()
                text = msg.get("content", "")
                prompt_parts.append(f"{role}: {text}")

        prompt_parts.append(f"USER: {body.message}")
        full_prompt = "\n".join(prompt_parts)

        def _resolve_agent_binary(agent_cmd: str) -> list[str]:
            """Resolve the actual node binary + index.js to bypass
            Windows .cmd/.ps1 command-line length limits (~8 KB)."""
            from pathlib import Path as P

            script_dir = P(agent_cmd).resolve().parent
            if (script_dir / "node.exe").exists() and (script_dir / "index.js").exists():
                return [str(script_dir / "node.exe"), str(script_dir / "index.js")]
            versions_dir = script_dir / "versions"
            if versions_dir.is_dir():
                candidates = sorted(
                    [d for d in versions_dir.iterdir() if d.is_dir()],
                    key=lambda d: d.name,
                    reverse=True,
                )
                for ver_dir in candidates:
                    node = ver_dir / "node.exe"
                    entry = ver_dir / "index.js"
                    if node.exists() and entry.exists():
                        return [str(node), str(entry)]
            return []

        resolved = _resolve_agent_binary(agent_cmd)
        import os as _os

        prompt_file_path: Optional[str] = None
        use_temp_file = len(full_prompt.encode("utf-8")) > 6000

        if use_temp_file:
            import tempfile
            pf = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8",
            )
            pf.write(full_prompt)
            pf.close()
            prompt_file_path = pf.name
            prompt_arg = f"Read and follow all instructions in the file {prompt_file_path}"
        else:
            prompt_arg = full_prompt

        base_args = [
            "-p", "--force", "--trust",
            "--output-format", "stream-json",
            "--stream-partial-output",
            "--workspace", str(storage.project_root),
            prompt_arg,
        ]

        if resolved:
            env = _os.environ.copy()
            env.setdefault("CURSOR_INVOKED_AS", "agent")
            local_app = _os.environ.get("LOCALAPPDATA", "")
            if local_app:
                env.setdefault("NODE_COMPILE_CACHE", _os.path.join(local_app, "cursor-compile-cache"))
            cmd = [*resolved, *base_args]
            cmd_env = env
        else:
            cmd = [agent_cmd, *base_args]
            cmd_env = None

        async def event_stream():
            proc = None
            stderr_task = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=cmd_env,
                )
                _active_chat_procs[session_id] = proc

                stderr_chunks: list[bytes] = []

                async def _drain_stderr():
                    try:
                        while True:
                            chunk = await proc.stderr.read(4096)
                            if not chunk:
                                break
                            stderr_chunks.append(chunk)
                    except Exception:
                        pass

                stderr_task = asyncio.create_task(_drain_stderr())

                yield f"event: session\ndata: {json_mod.dumps({'session_id': session_id})}\n\n"

                got_any_event = False

                while True:
                    if await request.is_disconnected():
                        proc.terminate()
                        break

                    try:
                        line = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=120,
                        )
                    except asyncio.TimeoutError:
                        sse_data = json_mod.dumps({
                            "type": "error",
                            "message": "Agent timed out (no output for 120s).",
                        })
                        yield f"event: error\ndata: {sse_data}\n\n"
                        proc.terminate()
                        break

                    if not line:
                        break

                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue

                    try:
                        event = json_mod.loads(text)
                    except json_mod.JSONDecodeError:
                        continue

                    got_any_event = True
                    evt_type = event.get("type", "")
                    evt_sub = event.get("subtype", "")

                    if evt_type == "assistant":
                        content_parts = event.get("message", {}).get("content", [])
                        for part in content_parts:
                            if part.get("type") == "text":
                                sse_data = json_mod.dumps({
                                    "type": "assistant",
                                    "text": part["text"],
                                })
                                yield f"event: assistant\ndata: {sse_data}\n\n"

                    elif evt_type == "tool_call":
                        tc = event.get("tool_call", {})
                        if evt_sub == "started":
                            tool_info = _extract_tool_info(tc, started=True)
                            sse_data = json_mod.dumps({
                                "type": "tool_start",
                                "call_id": event.get("call_id", ""),
                                **tool_info,
                            })
                            yield f"event: tool_start\ndata: {sse_data}\n\n"
                        elif evt_sub == "completed":
                            tool_info = _extract_tool_info(tc, started=False)
                            sse_data = json_mod.dumps({
                                "type": "tool_done",
                                "call_id": event.get("call_id", ""),
                                **tool_info,
                            })
                            yield f"event: tool_done\ndata: {sse_data}\n\n"

                    elif evt_type == "result":
                        sse_data = json_mod.dumps({
                            "type": "done",
                            "result": event.get("result", ""),
                            "duration_ms": event.get("duration_ms", 0),
                            "is_error": event.get("is_error", False),
                        })
                        yield f"event: done\ndata: {sse_data}\n\n"

                await proc.wait()
                await stderr_task

                stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()

                if proc.returncode and proc.returncode != 0:
                    if "authentication" in stderr_text.lower() or "login" in stderr_text.lower():
                        err_msg = (
                            "Cursor CLI authentication required. "
                            "Run 'agent login' in your terminal, then try again."
                        )
                    elif stderr_text:
                        err_msg = stderr_text
                    else:
                        err_msg = f"Agent exited with code {proc.returncode}"

                    sse_data = json_mod.dumps({"type": "error", "message": err_msg})
                    yield f"event: error\ndata: {sse_data}\n\n"

                elif not got_any_event:
                    err_msg = stderr_text or "Agent produced no output. Check that Cursor CLI is working."
                    sse_data = json_mod.dumps({"type": "error", "message": err_msg})
                    yield f"event: error\ndata: {sse_data}\n\n"

            except Exception as exc:
                sse_data = json_mod.dumps({
                    "type": "error",
                    "message": f"Failed to start agent: {exc}",
                })
                yield f"event: error\ndata: {sse_data}\n\n"
            finally:
                _active_chat_procs.pop(session_id, None)
                if proc is not None and proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                if stderr_task is not None:
                    stderr_task.cancel()
                if prompt_file_path:
                    try:
                        _os.unlink(prompt_file_path)
                    except Exception:
                        pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    def _extract_tool_info(tc: dict, started: bool) -> dict:
        if "readToolCall" in tc:
            r = tc["readToolCall"]
            args = r.get("args", {})
            info = {"tool": "read", "path": args.get("path", "")}
            if not started:
                result = r.get("result", {})
                success = result.get("success", {})
                info["lines"] = success.get("totalLines", 0)
            return info
        if "writeToolCall" in tc:
            w = tc["writeToolCall"]
            args = w.get("args", {})
            info = {"tool": "write", "path": args.get("path", "")}
            if not started:
                result = w.get("result", {})
                success = result.get("success", {})
                info["lines"] = success.get("linesCreated", 0)
                info["size"] = success.get("fileSize", 0)
            return info
        if "function" in tc:
            f = tc["function"]
            return {"tool": f.get("name", "unknown"), "path": ""}
        return {"tool": "unknown", "path": ""}

    @api.post("/api/chat/stop")
    async def chat_stop(body: ChatStopRequest):
        sid = body.session_id
        if sid and sid in _active_chat_procs:
            proc = _active_chat_procs.pop(sid)
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            return {"stopped": True}
        return {"stopped": False, "reason": "no active session"}

    @api.get("/api/chat/files")
    async def list_chat_files():
        import os as _os

        files: list[str] = []
        root = storage.project_root.resolve()
        count = 0
        max_files = 2000

        for dirpath_str, dirnames, filenames in _os.walk(str(root)):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".")
            ]
            for fname in filenames:
                if count >= max_files:
                    break
                full = Path(dirpath_str) / fname
                try:
                    rel = full.relative_to(root)
                except ValueError:
                    continue
                files.append(str(rel).replace("\\", "/"))
                count += 1
            if count >= max_files:
                break

        files.sort()
        return files

    return api


def _port_in_use(port: int) -> bool:
    """Return True if something is already listening on the given port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _kill_process_on_port(port: int) -> bool:
    """Find and terminate whatever process is listening on *port*.

    Returns True if a process was killed.
    """
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "TCP"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = int(line.strip().split()[-1])
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True
        else:
            out = subprocess.check_output(
                ["lsof", "-ti", f":{port}"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for pid_str in out.strip().splitlines():
                if pid_str.strip():
                    subprocess.run(
                        ["kill", "-9", pid_str.strip()],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            return True
    except (subprocess.CalledProcessError, ValueError, OSError):
        pass
    return False


def run_server(storage: Storage, port: int = 8742, open_browser: bool = True) -> None:
    if _port_in_use(port):
        print(f"Port {port} is already in use — stopping the previous viewer...")
        if _kill_process_on_port(port):
            time.sleep(0.8)
        if _port_in_use(port):
            print(f"Warning: port {port} is still occupied. Try a different port with --port.")

    api = create_app(storage)
    url = f"http://localhost:{port}"
    print(f"Architect viewer running at {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="warning")
