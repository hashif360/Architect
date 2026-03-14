"""Change impact analysis for the architecture graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import GraphData, Node


@dataclass
class ImpactResult:
    """Result of an impact analysis."""
    source_node: Node
    impacted: list[tuple[Node, int]] = field(default_factory=list)

    @property
    def total_impacted(self) -> int:
        return len(self.impacted)

    def by_depth(self) -> dict[int, list[Node]]:
        result: dict[int, list[Node]] = {}
        for node, depth in self.impacted:
            result.setdefault(depth, []).append(node)
        return result


def analyze_impact(
    graph: GraphData,
    node_id: str,
    max_depth: int = 10,
    include_forward: bool = False,
) -> Optional[ImpactResult]:
    """Analyze what nodes are affected when a given node changes.

    Walks the edge graph in reverse (dependents) to find all nodes
    that transitively depend on the changed node.

    If include_forward is True, also walks forward dependencies.
    """
    source = graph.get_node(node_id)
    if not source:
        return None

    visited = graph.walk_impact(node_id, max_depth=max_depth)

    if include_forward:
        forward: dict[str, int] = {}
        queue = [(node_id, 0)]
        while queue:
            current, depth = queue.pop(0)
            if current in forward or depth > max_depth:
                continue
            forward[current] = depth
            for dep_id in graph.get_dependencies(current):
                if dep_id not in forward:
                    queue.append((dep_id, depth + 1))
        for nid, depth in forward.items():
            if nid not in visited:
                visited[nid] = depth

    impacted = []
    for nid, depth in sorted(visited.items(), key=lambda x: x[1]):
        if nid == node_id:
            continue
        node = graph.get_node(nid)
        if node:
            impacted.append((node, depth))

    return ImpactResult(source_node=source, impacted=impacted)


def analyze_file_impact(
    graph: GraphData,
    file_path: str,
    max_depth: int = 10,
) -> Optional[ImpactResult]:
    """Analyze impact starting from a file path."""
    node = graph.find_node_by_file(file_path)
    if not node:
        normalized = file_path.replace("\\", "/")
        for n in graph.nodes:
            if n.file_path and (
                normalized.endswith(n.file_path) or n.file_path.endswith(normalized)
            ):
                node = n
                break
    if not node:
        return None
    return analyze_impact(graph, node.id, max_depth=max_depth)
