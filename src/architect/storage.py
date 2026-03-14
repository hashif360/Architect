"""Storage layer for reading/writing .architect/ directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import ADR, GraphData, Node, ProjectConfig

ARCHITECT_DIR = ".architect"
CONFIG_FILE = "config.json"
GRAPH_FILE = "graph.json"
EDGE_CACHE_FILE = "edge_cache.json"
CONTEXTS_DIR = "contexts"
ADRS_DIR = "adrs"
BRANCHES_DIR = "branches"

DEFAULT_CONTEXT_TEMPLATE = """# {name}

## Requirements


## Design Decisions


## Comments


## Implementation Notes

"""


class Storage:
    """Manages the .architect/ directory for a project."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = (project_root or Path.cwd()).resolve()
        self.architect_dir = self.project_root / ARCHITECT_DIR
        self.config_path = self.architect_dir / CONFIG_FILE
        self.graph_path = self.architect_dir / GRAPH_FILE
        self.edge_cache_path = self.architect_dir / EDGE_CACHE_FILE
        self.contexts_dir = self.architect_dir / CONTEXTS_DIR
        self.adrs_dir = self.architect_dir / ADRS_DIR
        self.branches_dir = self.architect_dir / BRANCHES_DIR
        self._graph_cache: Optional[GraphData] = None
        self._graph_mtime: float = 0.0

    @property
    def is_initialized(self) -> bool:
        return self.architect_dir.is_dir() and self.config_path.is_file()

    def initialize(self, name: str = "", description: str = "") -> None:
        self.architect_dir.mkdir(exist_ok=True)
        self.contexts_dir.mkdir(exist_ok=True)
        self.adrs_dir.mkdir(exist_ok=True)
        self.branches_dir.mkdir(exist_ok=True)

        if not name:
            name = self.project_root.name

        config = ProjectConfig(name=name, description=description)
        self._write_json(self.config_path, config.model_dump(mode="json"))

        if not self.graph_path.exists():
            graph = GraphData()
            self._write_json(self.graph_path, graph.model_dump(mode="json"))

    def load_config(self) -> ProjectConfig:
        data = self._read_json(self.config_path)
        return ProjectConfig(**data)

    def save_config(self, config: ProjectConfig) -> None:
        self._write_json(self.config_path, config.model_dump(mode="json"))

    def load_graph(self) -> GraphData:
        if not self.graph_path.exists():
            return GraphData()
        try:
            mtime = self.graph_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._graph_cache is not None and mtime == self._graph_mtime:
            return self._graph_cache
        data = self._read_json(self.graph_path)
        graph = GraphData(**data)
        self._graph_cache = graph
        self._graph_mtime = mtime
        return graph

    def save_graph(self, graph: GraphData) -> None:
        valid_ids = {n.id for n in graph.nodes}
        graph.edges = [
            e for e in graph.edges
            if e.source in valid_ids and e.target in valid_ids
        ]
        self._write_json(self.graph_path, graph.model_dump(mode="json"))
        self._graph_cache = graph
        try:
            self._graph_mtime = self.graph_path.stat().st_mtime
        except OSError:
            self._graph_mtime = 0.0

    # ── Edge inference cache ────────────────────────────────────────

    def load_edge_cache(self) -> dict[str, str]:
        """Load ``{file_path: content_hash}`` from edge_cache.json."""
        if self.edge_cache_path.exists():
            try:
                return self._read_json(self.edge_cache_path)
            except Exception:
                return {}
        return {}

    def save_edge_cache(self, cache: dict[str, str]) -> None:
        self._write_json(self.edge_cache_path, cache)

    def clear_edge_cache(self) -> None:
        if self.edge_cache_path.exists():
            self.edge_cache_path.unlink()

    # ── Context management ──────────────────────────────────────────

    def load_context(self, node_id: str) -> str:
        ctx_path = self.contexts_dir / f"{node_id}.md"
        if ctx_path.exists():
            return ctx_path.read_text(encoding="utf-8")
        return ""

    def save_context(self, node_id: str, content: str) -> None:
        self.contexts_dir.mkdir(exist_ok=True)
        ctx_path = self.contexts_dir / f"{node_id}.md"
        ctx_path.write_text(content, encoding="utf-8")

    def create_default_context(self, node: Node) -> None:
        ctx_path = self.contexts_dir / f"{node.id}.md"
        if not ctx_path.exists():
            content = DEFAULT_CONTEXT_TEMPLATE.format(name=node.name)
            self.save_context(node.id, content)

    def delete_context(self, node_id: str) -> None:
        ctx_path = self.contexts_dir / f"{node_id}.md"
        if ctx_path.exists():
            ctx_path.unlink()

    def append_to_context_section(
        self, node_id: str, section: str, text: str
    ) -> None:
        content = self.load_context(node_id)
        if not content:
            content = f"# Node {node_id}\n\n## {section}\n\n"

        section_header = f"## {section}"
        if section_header in content:
            lines = content.split("\n")
            insert_idx = None
            next_section_idx = None
            for i, line in enumerate(lines):
                if line.strip() == section_header:
                    insert_idx = i + 1
                elif insert_idx is not None and line.startswith("## "):
                    next_section_idx = i
                    break

            if insert_idx is not None:
                target = next_section_idx if next_section_idx else len(lines)
                while target > insert_idx and not lines[target - 1].strip():
                    target -= 1
                lines.insert(target, text)
                content = "\n".join(lines)
        else:
            content = content.rstrip() + f"\n\n## {section}\n{text}\n"

        self.save_context(node_id, content)

    # ── ADR management ──────────────────────────────────────────────

    def list_adrs(self) -> list[ADR]:
        self.adrs_dir.mkdir(exist_ok=True)
        adrs = []
        for p in sorted(self.adrs_dir.glob("*.json")):
            data = self._read_json(p)
            adrs.append(ADR(**data))
        return adrs

    def load_adr(self, adr_id: str) -> Optional[ADR]:
        p = self.adrs_dir / f"{adr_id}.json"
        if p.exists():
            return ADR(**self._read_json(p))
        return None

    def save_adr(self, adr: ADR) -> None:
        self.adrs_dir.mkdir(exist_ok=True)
        p = self.adrs_dir / f"{adr.id}.json"
        self._write_json(p, adr.model_dump(mode="json"))

    def delete_adr(self, adr_id: str) -> bool:
        p = self.adrs_dir / f"{adr_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    # ── Branch snapshot management ──────────────────────────────────

    def save_branch_snapshot(self, branch_name: str) -> None:
        """Snapshot the current graph for a branch."""
        self.branches_dir.mkdir(exist_ok=True)
        safe_name = branch_name.replace("/", "__").replace("\\", "__")
        dest = self.branches_dir / f"{safe_name}.json"
        graph = self.load_graph()
        self._write_json(dest, graph.model_dump(mode="json"))

    def load_branch_snapshot(self, branch_name: str) -> Optional[GraphData]:
        safe_name = branch_name.replace("/", "__").replace("\\", "__")
        p = self.branches_dir / f"{safe_name}.json"
        if p.exists():
            return GraphData(**self._read_json(p))
        return None

    def list_branch_snapshots(self) -> list[str]:
        self.branches_dir.mkdir(exist_ok=True)
        return [p.stem.replace("__", "/") for p in sorted(self.branches_dir.glob("*.json"))]

    def delete_branch_snapshot(self, branch_name: str) -> bool:
        safe_name = branch_name.replace("/", "__").replace("\\", "__")
        p = self.branches_dir / f"{safe_name}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    # ── Internal helpers ────────────────────────────────────────────

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, data: dict) -> None:
        path.write_text(
            json.dumps(data, indent=2, default=str, sort_keys=False) + "\n",
            encoding="utf-8",
        )
