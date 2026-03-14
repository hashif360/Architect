"""Pydantic data models for Architect graph structures."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    PAGE = "page"
    COMPONENT = "component"
    API_ENDPOINT = "api_endpoint"
    SERVICE = "service"
    DB_TABLE = "db_table"
    DB_COLUMN = "db_column"
    MIDDLEWARE = "middleware"
    UTILITY = "utility"
    CONFIG = "config"
    CUSTOM = "custom"


class Layer(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"
    SHARED = "shared"


class NodeStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    NEEDS_REVIEW = "needs_review"
    DEPRECATED = "deprecated"


class EdgeType(str, Enum):
    DEPENDENCY = "dependency"
    DATA_FLOW = "data_flow"
    FOREIGN_KEY = "foreign_key"
    RENDERS = "renders"
    CALLS = "calls"
    INHERITS = "inherits"


class ADRStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


def _generate_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Node(BaseModel):
    id: str = Field(default_factory=_generate_id)
    type: NodeType = NodeType.CUSTOM
    layer: Layer = Layer.SHARED
    name: str
    summary: str = ""
    status: NodeStatus = NodeStatus.PLANNED
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    position: Position = Field(default_factory=Position)
    file_path: Optional[str] = None
    created_by: str = "architect"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_scanned: Optional[datetime] = None
    workspace: Optional[str] = None

    def touch(self) -> None:
        self.updated_at = _now()


class Edge(BaseModel):
    id: str = Field(default_factory=_generate_id)
    source: str
    target: str
    label: str = ""
    type: EdgeType = EdgeType.DEPENDENCY
    deprecated: bool = False


class ADR(BaseModel):
    """Architecture Decision Record."""
    id: str = Field(default_factory=_generate_id)
    title: str
    status: ADRStatus = ADRStatus.PROPOSED
    context: str = ""
    decision: str = ""
    consequences: str = ""
    linked_nodes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()


class GraphData(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    def get_node(self, node_id: str) -> Optional[Node]:
        return next((n for n in self.nodes if n.id == node_id), None)

    def get_edge(self, edge_id: str) -> Optional[Edge]:
        return next((e for e in self.edges if e.id == edge_id), None)

    def remove_node(self, node_id: str) -> bool:
        before = len(self.nodes)
        self.nodes = [n for n in self.nodes if n.id != node_id]
        self.edges = [
            e for e in self.edges
            if e.source != node_id and e.target != node_id
        ]
        return len(self.nodes) < before

    def remove_edge(self, edge_id: str) -> bool:
        before = len(self.edges)
        self.edges = [e for e in self.edges if e.id != edge_id]
        return len(self.edges) < before

    def soft_delete_node(self, node_id: str) -> bool:
        """Mark a node as deprecated and ghost all its connected edges."""
        node = self.get_node(node_id)
        if not node:
            return False
        node.status = NodeStatus.DEPRECATED
        node.touch()
        for edge in self.edges:
            if edge.source == node_id or edge.target == node_id:
                edge.deprecated = True
        return True

    def soft_delete_edge(self, edge_id: str) -> bool:
        """Mark an edge as deprecated (ghost)."""
        edge = self.get_edge(edge_id)
        if not edge:
            return False
        edge.deprecated = True
        return True

    def restore_node(self, node_id: str) -> bool:
        """Restore a deprecated node back to implemented status."""
        node = self.get_node(node_id)
        if not node or node.status != NodeStatus.DEPRECATED:
            return False
        node.status = NodeStatus.IMPLEMENTED
        node.touch()
        for edge in self.edges:
            if edge.source == node_id or edge.target == node_id:
                src = self.get_node(edge.source)
                tgt = self.get_node(edge.target)
                if src and tgt and src.status != NodeStatus.DEPRECATED and tgt.status != NodeStatus.DEPRECATED:
                    edge.deprecated = False
        return True

    def restore_edge(self, edge_id: str) -> bool:
        """Restore a deprecated edge."""
        edge = self.get_edge(edge_id)
        if not edge or not edge.deprecated:
            return False
        edge.deprecated = False
        return True

    def find_nodes(
        self,
        layer: Optional[Layer] = None,
        node_type: Optional[NodeType] = None,
        status: Optional[NodeStatus] = None,
        tags: Optional[list[str]] = None,
        workspace: Optional[str] = None,
    ) -> list[Node]:
        results = self.nodes
        if layer:
            results = [n for n in results if n.layer == layer]
        if node_type:
            results = [n for n in results if n.type == node_type]
        if status:
            results = [n for n in results if n.status == status]
        if tags:
            results = [n for n in results if set(tags) & set(n.tags)]
        if workspace:
            results = [n for n in results if n.workspace == workspace]
        return results

    def find_node_by_file(self, file_path: str) -> Optional[Node]:
        normalized = file_path.replace("\\", "/")
        return next(
            (n for n in self.nodes if n.file_path and n.file_path.replace("\\", "/") == normalized),
            None,
        )

    def get_dependents(self, node_id: str) -> list[str]:
        """Return IDs of nodes that depend on (point to) this node."""
        return [e.source for e in self.edges if e.target == node_id]

    def get_dependencies(self, node_id: str) -> list[str]:
        """Return IDs of nodes this node depends on (points to)."""
        return [e.target for e in self.edges if e.source == node_id]

    def walk_impact(self, node_id: str, max_depth: int = 10) -> dict[str, int]:
        """BFS walk from node_id along reverse edges (dependents).
        Returns {node_id: depth} for every reachable node."""
        visited: dict[str, int] = {}
        queue = [(node_id, 0)]
        while queue:
            current, depth = queue.pop(0)
            if current in visited or depth > max_depth:
                continue
            visited[current] = depth
            for dep_id in self.get_dependents(current):
                if dep_id not in visited:
                    queue.append((dep_id, depth + 1))
        return visited

    def workspaces(self) -> list[str]:
        """Return a sorted list of distinct workspace names."""
        ws = {n.workspace for n in self.nodes if n.workspace}
        return sorted(ws)


class ProjectConfig(BaseModel):
    name: str = "Untitled Project"
    description: str = ""
    project_type: str = "unknown"
    scanner_settings: dict = Field(default_factory=dict)
    workspaces: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
