"""Cross-cutting edge inference engine.

Analyzes file contents to discover relationships between nodes that go
beyond simple import tracking: API calls, component rendering, service-to-DB
data flows, middleware chains, and more.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Layer, Node, NodeType

console = Console()

# ── Patterns for detecting cross-layer relationships ─────────────────

# Frontend calling API endpoints
API_CALL_PATTERNS = [
    re.compile(r'''fetch\s*\(\s*['"`](/api/[^'"`\s,)]+)['"`]'''),
    re.compile(r'''fetch\s*\(\s*['"`](/[^'"`\s,)]+)['"`]'''),
    re.compile(r'''axios\s*\.\s*(?:get|post|put|patch|delete|request|head|options)\s*\(\s*['"`](/api/[^'"`\s,)]+)['"`]'''),
    re.compile(r'''axios\s*\(\s*\{[^}]*url\s*:\s*['"`](/api/[^'"`\s,)]+)['"`]''', re.DOTALL),
    re.compile(r'''\.(?:get|post|put|patch|delete)\s*\(\s*['"`](/api/[^'"`\s,)]+)['"`]'''),
    re.compile(r'''useSWR\s*\(\s*['"`](/api/[^'"`\s,)]+)['"`]'''),
    re.compile(r'''useQuery\s*\([^)]*['"`](/api/[^'"`\s,)]+)['"`]'''),
    re.compile(r'''HttpClient\s*\.\s*(?:Get|Post|Put|Delete|Patch)(?:Async)?\s*\(\s*['"`](/api/[^'"`\s,)]+)['"`]'''),
    re.compile(r'''requests\s*\.\s*(?:get|post|put|patch|delete)\s*\(\s*['"`](?:https?://[^/]+)?(/api/[^'"`\s,)]+)['"`]'''),
]

# JSX component rendering: <ComponentName or <ComponentName>
JSX_COMPONENT_PATTERN = re.compile(r'<([A-Z][A-Za-z0-9_]+)[\s/>]')

# Python imports
PYTHON_IMPORT_PATTERNS = [
    re.compile(r'^from\s+([\w.]+)\s+import\s+(.+)$', re.MULTILINE),
    re.compile(r'^import\s+([\w.]+)', re.MULTILINE),
]

# JS/TS imports (comprehensive)
JS_IMPORT_PATTERNS = [
    re.compile(r'''from\s+['"]([^'"]+)['"]'''),
    re.compile(r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
    re.compile(r'''import\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
]

# ORM / DB usage patterns
DB_USAGE_PATTERNS = [
    re.compile(r'''prisma\.(\w+)\.'''),
    re.compile(r'''(\w+)\.(?:findAll|findOne|findByPk|create|update|destroy|bulkCreate|findAndCountAll)\s*\('''),
    re.compile(r'''(\w+)\.(?:find|findOne|findById|save|remove|delete|aggregate|countDocuments)\s*\('''),
    re.compile(r'''getRepository\s*\(\s*(\w+)\s*\)'''),
    re.compile(r'''(?:db|DB|database)\s*\.\s*(?:query|execute|run)\s*\(\s*['"`].*?(?:FROM|INTO|UPDATE|JOIN)\s+['"`]?(\w+)''', re.IGNORECASE),
    re.compile(r'''\.objects\.(?:all|filter|get|create|update|delete|exclude|values)\s*\('''),
    re.compile(r'''session\.query\s*\(\s*(\w+)\s*\)'''),
]

# Express/Fastify middleware patterns
MIDDLEWARE_PATTERN = re.compile(
    r'''(?:app|router)\s*\.\s*use\s*\(\s*(?:['"]([^'"]+)['"]\s*,\s*)?(\w+)'''
)

# C#/.NET controller patterns
CSHARP_INJECT_PATTERN = re.compile(
    r'''(?:private|readonly)\s+(?:readonly\s+)?I?(\w+(?:Service|Repository|Manager|Handler))\s+_\w+'''
)

# Django URL patterns
DJANGO_URL_PATTERN = re.compile(
    r'''path\s*\(\s*['"]([^'"]+)['"]\s*,\s*(\w+)'''
)

# PHP use/namespace imports
PHP_USE_PATTERN = re.compile(r'^use\s+([\w\\]+)\s*;', re.MULTILINE)

# Laravel route patterns
LARAVEL_ROUTE_PATTERNS = [
    re.compile(r'''Route\s*::\s*(get|post|put|patch|delete|any)\s*\(\s*['"]([^'"]+)['"]'''),
]


HASH_READ_SIZE = 8192


def _file_hash(path: Path) -> str:
    """Fast content hash (MD5 of first 8 KB)."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read(HASH_READ_SIZE)).hexdigest()
    except OSError:
        return ""


class EdgeInferrer:
    """Analyzes files and infers edges between existing graph nodes."""

    def __init__(
        self,
        root: Path,
        graph: GraphData,
        edge_cache: Optional[dict[str, str]] = None,
        changed_files: Optional[set[str]] = None,
    ):
        self.root = root.resolve()
        self.graph = graph
        self._edge_cache = edge_cache if edge_cache is not None else {}
        self._new_cache: dict[str, str] = {}
        self._changed_files = changed_files
        self._build_indexes()
        self._path_aliases: dict[str, str] = self._load_path_aliases()

    def _load_path_aliases(self) -> dict[str, str]:
        """Parse ``compilerOptions.paths`` from tsconfig/jsconfig."""
        for config_name in ("tsconfig.json", "jsconfig.json"):
            config_path = self.root / config_name
            if not config_path.exists():
                continue
            try:
                raw = config_path.read_text(encoding="utf-8")
                raw = re.sub(r'//[^\n]*', '', raw)
                raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
                raw = re.sub(r',\s*([}\]])', r'\1', raw)
                config = json.loads(raw)
                compiler_opts = config.get("compilerOptions", {})
                base_url = compiler_opts.get("baseUrl", ".")
                paths = compiler_opts.get("paths", {})

                aliases: dict[str, str] = {}
                for alias, targets in paths.items():
                    if not targets:
                        continue
                    alias_prefix = alias.replace("/*", "/").replace("*", "")
                    target_prefix = targets[0].replace("/*", "/").replace("*", "")
                    resolved = str((self.root / base_url / target_prefix).resolve())
                    aliases[alias_prefix] = resolved
                return aliases
            except Exception:
                continue
        return {}

    def _resolve_alias_import(self, import_path: str) -> Optional[Path]:
        """Resolve a path-aliased import (e.g. ``@/components/Button``)."""
        for alias_prefix, resolved_dir in self._path_aliases.items():
            if import_path.startswith(alias_prefix):
                remainder = import_path[len(alias_prefix):]
                candidate = Path(resolved_dir) / remainder
                for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs"):
                    check = candidate.with_suffix(ext) if ext else candidate
                    if check.is_file():
                        return check
                if candidate.is_dir():
                    for idx in ("index.ts", "index.tsx", "index.js", "index.jsx"):
                        check = candidate / idx
                        if check.is_file():
                            return check
        return None

    def _build_indexes(self) -> None:
        """Build lookup indexes for fast node resolution."""
        self.node_by_file: dict[str, Node] = {}
        self.node_by_name: dict[str, list[Node]] = {}
        self.api_nodes_by_route: dict[str, Node] = {}
        self.db_nodes_by_name: dict[str, Node] = {}
        self.component_nodes_by_name: dict[str, Node] = {}

        for node in self.graph.nodes:
            if node.file_path:
                self.node_by_file[node.file_path] = node

            name_key = node.name.lower()
            self.node_by_name.setdefault(name_key, []).append(node)

            if node.type == NodeType.API_ENDPOINT:
                route = self._extract_route(node)
                if route:
                    self.api_nodes_by_route[route] = node

            if node.type == NodeType.DB_TABLE:
                table_name = self._extract_table_name(node)
                if table_name:
                    self.db_nodes_by_name[table_name.lower()] = node

            if node.type == NodeType.COMPONENT:
                comp_name = node.name.split("/")[-1].split(".")[0]
                self.component_nodes_by_name[comp_name] = node

    def _extract_route(self, node: Node) -> Optional[str]:
        """Extract API route path from node name/summary."""
        name = node.name
        for prefix in ("API: ", "GET ", "POST ", "PUT ", "DELETE ", "PATCH "):
            if name.startswith(prefix):
                return name[len(prefix):]
        if name.startswith("/"):
            return name
        summary = node.summary
        for marker in ("API route: ", "Express route: ", "Next.js API route: "):
            if marker in summary:
                route = summary.split(marker)[-1].strip()
                return route.split()[0] if route else None
        return None

    def _extract_table_name(self, node: Node) -> Optional[str]:
        """Extract table/model name from a db_table node."""
        name = node.name
        if name.startswith("Table: "):
            return name[7:]
        return name

    def infer_all(self) -> int:
        """Run all inference passes and return count of edges added.

        Files whose content hash matches the edge cache are skipped
        (their existing edges are retained). Only new or modified files
        are re-inferred.
        """
        existing_pairs = {(e.source, e.target, e.type) for e in self.graph.edges}
        total = 0
        skipped = 0

        for node in list(self.graph.nodes):
            if not node.file_path:
                continue

            file_path = self.root / node.file_path.replace("/", "\\")
            if not file_path.is_file():
                file_path = self.root / node.file_path
            if not file_path.is_file():
                continue

            cur_hash = _file_hash(file_path)
            rel = node.file_path

            if self._changed_files is not None and rel not in self._changed_files:
                self._new_cache[rel] = self._edge_cache.get(rel, cur_hash)
                skipped += 1
                continue

            cached_hash = self._edge_cache.get(rel)
            if cached_hash and cached_hash == cur_hash:
                self._new_cache[rel] = cur_hash
                skipped += 1
                continue

            self._new_cache[rel] = cur_hash

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            edges: list[Edge] = []

            if node.layer in (Layer.FRONTEND, Layer.SHARED):
                edges.extend(self._infer_api_calls(node, content))
                edges.extend(self._infer_component_rendering(node, content))

            if node.layer in (Layer.BACKEND, Layer.SHARED):
                edges.extend(self._infer_db_usage(node, content))

            edges.extend(self._infer_imports(node, file_path, content))

            for edge in edges:
                key = (edge.source, edge.target, edge.type)
                if key not in existing_pairs and edge.source != edge.target:
                    self.graph.edges.append(edge)
                    existing_pairs.add(key)
                    total += 1

        if skipped:
            console.print(f"  [dim]Skipped {skipped} unchanged files (cached)[/dim]")

        return total

    def get_updated_cache(self) -> dict[str, str]:
        """Return the updated edge cache after inference."""
        return self._new_cache

    def _infer_api_calls(self, source_node: Node, content: str) -> list[Edge]:
        """Detect fetch/axios/HTTP calls to API endpoints."""
        edges: list[Edge] = []
        for pattern in API_CALL_PATTERNS:
            for match in pattern.finditer(content):
                api_path = match.group(1)
                target = self._resolve_api_route(api_path)
                if target and target.id != source_node.id:
                    edges.append(Edge(
                        source=source_node.id,
                        target=target.id,
                        label=f"calls {api_path}",
                        type=EdgeType.CALLS,
                    ))
        return edges

    def _resolve_api_route(self, path: str) -> Optional[Node]:
        """Find the API node that matches a given route path."""
        if path in self.api_nodes_by_route:
            return self.api_nodes_by_route[path]

        for route, node in self.api_nodes_by_route.items():
            normalized_path = re.sub(r':\w+', ':param', path)
            normalized_route = re.sub(r':\w+', ':param', route)
            normalized_route = re.sub(r'\[\w+\]', ':param', normalized_route)
            if normalized_path == normalized_route:
                return node

        for route, node in self.api_nodes_by_route.items():
            if path.rstrip('/') == route.rstrip('/'):
                return node

        return None

    def _infer_component_rendering(self, source_node: Node, content: str) -> list[Edge]:
        """Detect JSX component usage."""
        edges: list[Edge] = []
        seen = set()
        for match in JSX_COMPONENT_PATTERN.finditer(content):
            comp_name = match.group(1)
            if comp_name in seen:
                continue
            seen.add(comp_name)

            if comp_name in ("Fragment", "Suspense", "StrictMode", "Provider",
                             "Router", "Route", "Switch", "Link", "Head",
                             "Script", "Image", "Component"):
                continue

            target = self.component_nodes_by_name.get(comp_name)
            if target and target.id != source_node.id:
                edges.append(Edge(
                    source=source_node.id,
                    target=target.id,
                    label=f"renders <{comp_name}>",
                    type=EdgeType.RENDERS,
                ))
        return edges

    def _infer_db_usage(self, source_node: Node, content: str) -> list[Edge]:
        """Detect ORM/DB model usage patterns."""
        edges: list[Edge] = []
        seen_targets = set()

        for pattern in DB_USAGE_PATTERNS:
            for match in pattern.finditer(content):
                model_name = match.group(1) if match.lastindex else None
                if not model_name:
                    continue

                target = self.db_nodes_by_name.get(model_name.lower())
                if not target:
                    target = self.db_nodes_by_name.get(model_name.lower() + "s")
                if not target:
                    for name, node in self.db_nodes_by_name.items():
                        if name.rstrip("s") == model_name.lower().rstrip("s"):
                            target = node
                            break

                if target and target.id != source_node.id and target.id not in seen_targets:
                    seen_targets.add(target.id)
                    edges.append(Edge(
                        source=source_node.id,
                        target=target.id,
                        label=f"queries {model_name}",
                        type=EdgeType.DATA_FLOW,
                    ))
        return edges

    def _infer_imports(self, source_node: Node, file_path: Path, content: str) -> list[Edge]:
        """Infer dependency edges from import statements."""
        edges: list[Edge] = []
        suffix = file_path.suffix.lower()

        if suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            edges.extend(self._infer_js_imports(source_node, file_path, content))
        elif suffix == ".py":
            edges.extend(self._infer_python_imports(source_node, file_path, content))
        elif suffix in (".cs",):
            edges.extend(self._infer_csharp_deps(source_node, content))
        elif suffix == ".php":
            edges.extend(self._infer_php_imports(source_node, content))

        return edges

    def _infer_js_imports(self, source_node: Node, file_path: Path, content: str) -> list[Edge]:
        """Resolve JS/TS imports (relative and aliased) to graph nodes."""
        edges: list[Edge] = []
        for pattern in JS_IMPORT_PATTERNS:
            for match in pattern.finditer(content):
                import_path = match.group(1)

                if import_path.startswith("."):
                    resolved = self._resolve_js_import(file_path, import_path)
                else:
                    resolved = self._resolve_alias_import(import_path)

                if resolved:
                    rel = self._relative(resolved)
                    target_node = self.node_by_file.get(rel)
                    if target_node and target_node.id != source_node.id:
                        edges.append(Edge(
                            source=source_node.id,
                            target=target_node.id,
                            label="imports",
                            type=EdgeType.DEPENDENCY,
                        ))
        return edges

    def _resolve_js_import(self, source: Path, import_path: str) -> Optional[Path]:
        """Resolve a relative JS import to a file path."""
        source_dir = source.parent
        candidate = (source_dir / import_path).resolve()
        extensions = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs"]
        for ext in extensions:
            check = candidate.with_suffix(ext) if ext else candidate
            if check.is_file():
                return check
        if candidate.is_dir():
            for idx in ("index.ts", "index.tsx", "index.js", "index.jsx"):
                check = candidate / idx
                if check.is_file():
                    return check
        return None

    def _infer_python_imports(self, source_node: Node, file_path: Path, content: str) -> list[Edge]:
        """Resolve Python imports to graph nodes."""
        edges: list[Edge] = []
        for pattern in PYTHON_IMPORT_PATTERNS:
            for match in pattern.finditer(content):
                module = match.group(1)
                if module.startswith("."):
                    resolved = self._resolve_python_relative_import(file_path, module)
                else:
                    resolved = self._resolve_python_absolute_import(module)

                if resolved:
                    rel = self._relative(resolved)
                    target_node = self.node_by_file.get(rel)
                    if target_node and target_node.id != source_node.id:
                        edges.append(Edge(
                            source=source_node.id,
                            target=target_node.id,
                            label="imports",
                            type=EdgeType.DEPENDENCY,
                        ))
        return edges

    def _resolve_python_relative_import(self, source: Path, module: str) -> Optional[Path]:
        """Resolve a relative Python import."""
        level = len(module) - len(module.lstrip("."))
        module_path = module.lstrip(".").replace(".", "/")
        base = source.parent
        for _ in range(level - 1):
            base = base.parent

        candidate = base / module_path
        if candidate.with_suffix(".py").is_file():
            return candidate.with_suffix(".py")
        init = candidate / "__init__.py"
        if init.is_file():
            return init
        return None

    def _resolve_python_absolute_import(self, module: str) -> Optional[Path]:
        """Try to resolve an absolute Python import within the project."""
        module_path = module.replace(".", "/")
        candidate = self.root / module_path
        if candidate.with_suffix(".py").is_file():
            return candidate.with_suffix(".py")
        candidate = self.root / "src" / module_path
        if candidate.with_suffix(".py").is_file():
            return candidate.with_suffix(".py")
        init = candidate / "__init__.py"
        if init.is_file():
            return init
        return None

    def _infer_csharp_deps(self, source_node: Node, content: str) -> list[Edge]:
        """Detect C# constructor-injected services."""
        edges: list[Edge] = []
        for match in CSHARP_INJECT_PATTERN.finditer(content):
            service_name = match.group(1)
            for name_key, nodes in self.node_by_name.items():
                for target in nodes:
                    stem = target.name.split("/")[-1].split(".")[0]
                    if stem.lower() == service_name.lower() or stem.lower() == f"i{service_name.lower()}":
                        if target.id != source_node.id:
                            edges.append(Edge(
                                source=source_node.id,
                                target=target.id,
                                label=f"injects {service_name}",
                                type=EdgeType.DEPENDENCY,
                            ))
        return edges

    def _infer_php_imports(self, source_node: Node, content: str) -> list[Edge]:
        """Detect PHP use statements and resolve to graph nodes."""
        edges: list[Edge] = []
        for match in PHP_USE_PATTERN.finditer(content):
            fqcn = match.group(1)
            class_name = fqcn.split("\\")[-1]

            for name_key, nodes in self.node_by_name.items():
                for target in nodes:
                    stem = target.name.split(": ")[-1] if ": " in target.name else target.name
                    stem = stem.split("/")[-1].split(".")[0]
                    if stem.lower() == class_name.lower():
                        if target.id != source_node.id:
                            edges.append(Edge(
                                source=source_node.id,
                                target=target.id,
                                label=f"uses {class_name}",
                                type=EdgeType.DEPENDENCY,
                            ))
        return edges

    def _relative(self, path: Path) -> str:
        """Return a forward-slash relative path from the project root."""
        try:
            return str(path.resolve().relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")
