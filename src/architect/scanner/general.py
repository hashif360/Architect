"""General-purpose file-structure scanner."""

from __future__ import annotations

import re
from pathlib import Path

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Layer, Node, NodeType
from .base import BaseScanner

console = Console()

LAYER_HINTS = {
    "frontend": {"src/pages", "src/app", "src/components", "src/views", "pages", "app", "components", "views", "public", "static", "templates", "frontend", "client", "web"},
    "backend": {"src/api", "src/routes", "src/controllers", "src/services", "api", "routes", "controllers", "services", "server", "backend", "handlers", "middleware"},
    "database": {"src/models", "src/schemas", "src/entities", "models", "schemas", "entities", "migrations", "prisma", "db", "database"},
    "shared": {"src/utils", "src/lib", "src/common", "src/shared", "utils", "lib", "common", "shared", "helpers", "config"},
}

FILE_TYPE_MAP = {
    ".tsx": NodeType.COMPONENT,
    ".jsx": NodeType.COMPONENT,
    ".vue": NodeType.COMPONENT,
    ".svelte": NodeType.COMPONENT,
    ".razor": NodeType.COMPONENT,
    ".py": NodeType.UTILITY,
    ".ts": NodeType.UTILITY,
    ".js": NodeType.UTILITY,
    ".mjs": NodeType.UTILITY,
    ".cjs": NodeType.UTILITY,
    ".php": NodeType.UTILITY,
    ".cs": NodeType.UTILITY,
    ".rb": NodeType.UTILITY,
    ".go": NodeType.UTILITY,
    ".rs": NodeType.UTILITY,
    ".java": NodeType.UTILITY,
    ".kt": NodeType.UTILITY,
    ".swift": NodeType.UTILITY,
    ".cshtml": NodeType.PAGE,
    ".sql": NodeType.DB_TABLE,
    ".prisma": NodeType.DB_TABLE,
}

IMPORT_PATTERNS = [
    re.compile(r'''from\s+['"](\.{1,2}/[^'"]+)['"]'''),
    re.compile(r'''import\s+.*?from\s+['"](\.{1,2}/[^'"]+)['"]'''),
    re.compile(r'''require\s*\(\s*['"](\.{1,2}/[^'"]+)['"]\s*\)'''),
    re.compile(r'''from\s+(\.\S+)\s+import'''),
]

# ── Content-based classification patterns ─────────────────────────

_RE_JS_ROUTE_HANDLER = re.compile(
    r'export\s+(?:async\s+)?function\s+(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b'
)
_RE_EXPRESS_MIDDLEWARE = re.compile(
    r'\(\s*(?:req|request)\s*,\s*(?:res|response)\s*,\s*next\s*\)'
)
_RE_PY_ROUTE = re.compile(
    r'@(?:\w+\.)?(?:route|get|post|put|delete|patch|api_view)\s*\('
)
_RE_PY_ORM_MODEL = re.compile(
    r'class\s+\w+\s*\([^)]*(?:models\.Model|Base|DeclarativeBase|db\.Model)[^)]*\)'
)
_RE_PY_PYDANTIC = re.compile(
    r'class\s+\w+\s*\([^)]*(?:BaseModel|BaseSchema)[^)]*\)'
)
_RE_JS_DEFAULT_EXPORT_OBJ = re.compile(
    r'(?:export\s+default|module\.exports)\s*=?\s*\{'
)


class GeneralScanner(BaseScanner):
    name = "general"

    def scan(self) -> GraphData:
        graph = GraphData()
        file_node_map: dict[str, str] = {}

        console.print("[cyan]Scanning file structure...[/cyan]")
        source_files = self._collect_files()
        console.print(f"  Found {len(source_files)} source files")

        processed = 0
        for chunk in self._iter_chunks(source_files, self.CHUNK_SIZE):
            for file_path in chunk:
                rel = self.relative(file_path)
                layer = self._guess_layer(rel)
                node_type = self._guess_type(file_path, rel)

                node = Node(
                    name=file_path.stem,
                    type=node_type,
                    layer=layer,
                    summary=f"Source file: {rel}",
                    file_path=rel,
                )
                graph.nodes.append(node)
                file_node_map[rel] = node.id
                self.storage.create_default_context(node)
            processed += len(chunk)
            if len(source_files) > self.CHUNK_SIZE:
                console.print(f"  Classified {processed}/{len(source_files)} files...")

        console.print("[cyan]Analyzing imports...[/cyan]")
        edge_count = 0
        for chunk in self._iter_chunks(source_files, self.CHUNK_SIZE):
            for file_path in chunk:
                rel = self.relative(file_path)
                source_id = file_node_map.get(rel)
                if not source_id:
                    continue

                imports = self._extract_imports(file_path)
                for imp in imports:
                    resolved = self._resolve_import(file_path, imp)
                    if resolved:
                        target_rel = self.relative(resolved)
                        target_id = file_node_map.get(target_rel)
                        if target_id and target_id != source_id:
                            edge = Edge(
                                source=source_id,
                                target=target_id,
                                label="imports",
                                type=EdgeType.DEPENDENCY,
                            )
                            graph.edges.append(edge)
                            edge_count += 1

        console.print(f"  Found {edge_count} import relationships")
        return graph

    def _collect_files(self) -> list[Path]:
        return self._find_files(
            "*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.cjs",
            "*.py", "*.vue", "*.svelte",
            "*.sql", "*.prisma", "*.php", "*.cs", "*.razor", "*.cshtml",
            "*.rb", "*.go", "*.rs", "*.java", "*.kt", "*.swift",
        )

    def _guess_layer(self, rel_path: str) -> Layer:
        rel_lower = rel_path.lower().replace("\\", "/")
        for layer_name, hints in LAYER_HINTS.items():
            for hint in hints:
                if rel_lower.startswith(hint + "/") or f"/{hint}/" in rel_lower:
                    return Layer(layer_name)
        return Layer.SHARED

    def _guess_type(self, file_path: Path, rel_path: str) -> NodeType:
        rel_lower = rel_path.lower()

        if any(p in rel_lower for p in ["pages/", "app/", "views/"]):
            return NodeType.PAGE
        if any(p in rel_lower for p in ["api/", "routes/", "controllers/", "handlers/"]):
            return NodeType.API_ENDPOINT
        if any(p in rel_lower for p in ["models/", "schemas/", "entities/", "prisma/"]):
            return NodeType.DB_TABLE
        if any(p in rel_lower for p in ["services/", "service/"]):
            return NodeType.SERVICE
        if any(p in rel_lower for p in ["middleware/"]):
            return NodeType.MIDDLEWARE
        if any(p in rel_lower for p in ["components/", "component/"]):
            return NodeType.COMPONENT
        if any(p in rel_lower for p in ["utils/", "lib/", "helpers/", "common/"]):
            return NodeType.UTILITY
        if any(p in rel_lower for p in ["config/", "config."]):
            return NodeType.CONFIG

        ext_type = FILE_TYPE_MAP.get(file_path.suffix)
        if ext_type and ext_type != NodeType.UTILITY:
            return ext_type

        content_type = self._classify_by_content(file_path)
        if content_type is not None:
            return content_type

        return ext_type or NodeType.UTILITY

    # ── Content-based classification ──────────────────────────────

    def _classify_by_content(self, file_path: Path) -> NodeType | None:
        """Inspect file content to refine the node type when path heuristics
        are ambiguous (i.e. the file is not in a well-known directory)."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")[:4096]
        except Exception:
            return None

        suffix = file_path.suffix.lower()

        if suffix in (".ts", ".tsx", ".js", ".jsx"):
            if _RE_JS_ROUTE_HANDLER.search(content):
                return NodeType.API_ENDPOINT
            if suffix in (".ts", ".js") and _RE_EXPRESS_MIDDLEWARE.search(content):
                return NodeType.MIDDLEWARE
            if file_path.stem.lower() in (
                "config", "settings", "constants", "env",
            ) and _RE_JS_DEFAULT_EXPORT_OBJ.search(content):
                return NodeType.CONFIG

        if suffix == ".py":
            if _RE_PY_ROUTE.search(content):
                return NodeType.API_ENDPOINT
            if _RE_PY_ORM_MODEL.search(content):
                return NodeType.DB_TABLE
            if _RE_PY_PYDANTIC.search(content):
                return NodeType.SERVICE

        return None

    # ── Import helpers ────────────────────────────────────────────

    def _extract_imports(self, file_path: Path) -> list[str]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        imports = []
        for pattern in IMPORT_PATTERNS:
            imports.extend(pattern.findall(content))
        return imports

    def _resolve_import(self, source: Path, import_path: str) -> Path | None:
        source_dir = source.parent
        import_path = import_path.replace(".", "/", 1) if not import_path.startswith(".") else import_path

        candidate = (source_dir / import_path).resolve()

        extensions = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".vue", ".svelte", ".php", ".cs", ".rb", ".go", ".rs", ".java", ".kt", ".swift"]
        for ext in extensions:
            check = candidate.with_suffix(ext) if ext else candidate
            if check.is_file():
                return check

        index_names = ["index.ts", "index.tsx", "index.js", "index.jsx"]
        if candidate.is_dir():
            for idx in index_names:
                check = candidate / idx
                if check.is_file():
                    return check

        return None
