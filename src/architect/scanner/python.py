"""Python project scanner with Django, Flask, and FastAPI detection."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Layer, Node, NodeType
from .base import BaseScanner

console = Console()

PYTHON_IMPORT_PATTERNS = [
    re.compile(r'^from\s+([\w.]+)\s+import\s+(.+)$', re.MULTILINE),
    re.compile(r'^import\s+([\w.]+)', re.MULTILINE),
]

# Django URL routing patterns
DJANGO_URL_PATTERNS = [
    re.compile(r'''path\s*\(\s*['"]([^'"]*)['"]\s*,\s*(?:views\.)?(\w+)'''),
    re.compile(r'''url\s*\(\s*r?['"]([^'"]*)['"]\s*,\s*(?:views\.)?(\w+)'''),
    re.compile(r'''re_path\s*\(\s*r?['"]([^'"]*)['"]\s*,\s*(?:views\.)?(\w+)'''),
]

# Flask/FastAPI route decorator patterns
ROUTE_DECORATOR_PATTERNS = [
    re.compile(r'''@(?:\w+\.)?route\s*\(\s*['"]([^'"]+)['"](?:\s*,\s*methods\s*=\s*\[([^\]]+)\])?\s*\)'''),
    re.compile(r'''@(?:\w+\.)?(?:get|post|put|delete|patch)\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
    re.compile(r'''@(?:\w+\.)?api_view\s*\(\s*\[([^\]]+)\]\s*\)'''),
]

# Django model field pattern
DJANGO_MODEL_PATTERN = re.compile(
    r'class\s+(\w+)\s*\([^)]*(?:models\.Model|AbstractUser|AbstractBaseUser)[^)]*\)\s*:', re.MULTILINE
)

# SQLAlchemy model pattern
SQLALCHEMY_MODEL_PATTERN = re.compile(
    r'class\s+(\w+)\s*\([^)]*(?:db\.Model|Base|DeclarativeBase)[^)]*\)\s*:', re.MULTILINE
)

# Pydantic model pattern
PYDANTIC_MODEL_PATTERN = re.compile(
    r'class\s+(\w+)\s*\([^)]*(?:BaseModel|BaseSchema)[^)]*\)\s*:', re.MULTILINE
)

# Django ForeignKey / relationship
DJANGO_FK_PATTERN = re.compile(
    r'''(\w+)\s*=\s*models\.(?:ForeignKey|OneToOneField|ManyToManyField)\s*\(\s*['"]?(\w+)['"]?'''
)

SQLALCHEMY_FK_PATTERN = re.compile(
    r'''(\w+)\s*=\s*(?:db\.)?relationship\s*\(\s*['"](\w+)['"]'''
)


class PythonScanner(BaseScanner):
    name = "python"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.framework: str = "generic"

    def scan(self) -> GraphData:
        graph = GraphData()
        self._detect_framework()
        console.print(f"[cyan]Detected Python framework:[/cyan] {self.framework}")

        if self.framework == "django":
            self._scan_django(graph)
        elif self.framework in ("flask", "fastapi"):
            self._scan_flask_fastapi(graph)
        else:
            self._scan_generic_python(graph)

        self._scan_models(graph)
        self._infer_import_edges(graph)

        return graph

    def _detect_framework(self) -> None:
        req_files = [
            self.root / "requirements.txt",
            self.root / "Pipfile",
            self.root / "pyproject.toml",
            self.root / "setup.py",
            self.root / "setup.cfg",
        ]

        all_text = ""
        for f in req_files:
            if f.exists():
                try:
                    all_text += f.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception:
                    pass

        if (self.root / "manage.py").exists() or "django" in all_text:
            self.framework = "django"
        elif "fastapi" in all_text:
            self.framework = "fastapi"
        elif "flask" in all_text:
            self.framework = "flask"

    # ── AST-based import extraction ───────────────────────────────

    def _extract_imports_ast(self, file_path: Path) -> list[str]:
        """Extract import module names using Python's ast module.

        More reliable than regex: handles multi-line imports, ignores
        imports inside comments/strings, and correctly parses relative
        import levels.
        """
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, Exception):
            return self._extract_imports_regex(file_path)

        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    level = node.level or 0
                    prefix = "." * level
                    imports.append(f"{prefix}{node.module}")
                elif node.level:
                    imports.append("." * node.level)
        return imports

    def _extract_imports_regex(self, file_path: Path) -> list[str]:
        """Regex fallback for files that fail to parse with ast."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []
        imports: list[str] = []
        for pattern in PYTHON_IMPORT_PATTERNS:
            for match in pattern.finditer(content):
                imports.append(match.group(1))
        return imports

    # ── Django scanning ──────────────────────────────────────────────

    def _scan_django(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Django project...[/cyan]")
        self._scan_django_urls(graph)
        self._scan_django_views(graph)
        self._scan_django_templates(graph)
        self._scan_django_forms(graph)
        self._scan_django_serializers(graph)
        self._scan_django_admin(graph)
        self._scan_django_middleware(graph)

    def _scan_django_urls(self, graph: GraphData) -> None:
        url_files = self._find_files("urls.py")
        count = 0
        for f in url_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for pattern in DJANGO_URL_PATTERNS:
                for match in pattern.finditer(content):
                    route = match.group(1)
                    view_name = match.group(2)
                    rel = self.relative(f)
                    node = Node(
                        name=f"URL: /{route}",
                        type=NodeType.API_ENDPOINT,
                        layer=Layer.BACKEND,
                        summary=f"Django URL pattern: /{route} -> {view_name}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    self.storage.create_default_context(node)
                    count += 1

        console.print(f"  Found {count} URL patterns")

    def _scan_django_views(self, graph: GraphData) -> None:
        view_files = self._find_files("views.py", "views/*.py")
        existing = {n.file_path for n in graph.nodes if n.file_path}
        count = 0

        for f in view_files:
            if f.name == "__init__.py":
                continue
            rel = self.relative(f)
            if rel in existing:
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            class_views = re.findall(r'class\s+(\w+View\w*)\s*\(', content)
            func_views = re.findall(r'^def\s+(\w+)\s*\(\s*request', content, re.MULTILINE)

            view_names = class_views + func_views
            if view_names:
                for vname in view_names:
                    node = Node(
                        name=vname,
                        type=NodeType.API_ENDPOINT,
                        layer=Layer.BACKEND,
                        summary=f"Django view: {vname} in {rel}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    self.storage.create_default_context(node)
                    count += 1
            else:
                node = Node(
                    name=f.stem,
                    type=NodeType.API_ENDPOINT,
                    layer=Layer.BACKEND,
                    summary=f"Django views: {rel}",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                count += 1

        console.print(f"  Found {count} views")

    def _scan_django_templates(self, graph: GraphData) -> None:
        template_dirs = list(self.root.rglob("templates"))
        count = 0

        for tdir in template_dirs:
            if not tdir.is_dir() or self._should_skip(tdir):
                continue
            for f in self._find_files("*.html", "*.htm", "*.txt", "*.xml", root_dir=tdir):
                rel = self.relative(f)
                node = Node(
                    name=f"Template: {f.stem}",
                    type=NodeType.PAGE,
                    layer=Layer.FRONTEND,
                    summary=f"Django template: {rel}",
                    file_path=rel,
                )
                graph.nodes.append(node)
                self.storage.create_default_context(node)
                count += 1

        console.print(f"  Found {count} templates")

    def _scan_django_forms(self, graph: GraphData) -> None:
        form_files = self._find_files("forms.py", "forms/*.py")
        count = 0
        for f in form_files:
            if f.name == "__init__.py":
                continue
            rel = self.relative(f)
            node = Node(
                name=f.stem,
                type=NodeType.COMPONENT,
                layer=Layer.BACKEND,
                summary=f"Django forms: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1
        if count:
            console.print(f"  Found {count} form modules")

    def _scan_django_serializers(self, graph: GraphData) -> None:
        ser_files = self._find_files("serializers.py", "serializers/*.py")
        count = 0
        for f in ser_files:
            if f.name == "__init__.py":
                continue
            rel = self.relative(f)
            node = Node(
                name=f.stem,
                type=NodeType.SERVICE,
                layer=Layer.BACKEND,
                summary=f"Django REST serializers: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1
        if count:
            console.print(f"  Found {count} serializer modules")

    def _scan_django_admin(self, graph: GraphData) -> None:
        admin_files = self._find_files("admin.py")
        count = 0
        for f in admin_files:
            rel = self.relative(f)
            node = Node(
                name=f"Admin: {f.parent.name}",
                type=NodeType.CONFIG,
                layer=Layer.BACKEND,
                summary=f"Django admin config: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1
        if count:
            console.print(f"  Found {count} admin modules")

    def _scan_django_middleware(self, graph: GraphData) -> None:
        mw_files = self._find_files("middleware.py", "middleware/*.py")
        count = 0
        for f in mw_files:
            if f.name == "__init__.py":
                continue
            rel = self.relative(f)
            node = Node(
                name=f.stem,
                type=NodeType.MIDDLEWARE,
                layer=Layer.BACKEND,
                summary=f"Django middleware: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1
        if count:
            console.print(f"  Found {count} middleware modules")

    # ── Flask / FastAPI scanning ─────────────────────────────────────

    def _scan_flask_fastapi(self, graph: GraphData) -> None:
        label = "Flask" if self.framework == "flask" else "FastAPI"
        console.print(f"[cyan]Scanning {label} project...[/cyan]")

        py_files = self._find_files("*.py")
        route_count = 0
        file_count = 0

        existing_files: set[str] = set()

        for f in py_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel = self.relative(f)
            routes_found = []

            for pattern in ROUTE_DECORATOR_PATTERNS:
                for match in pattern.finditer(content):
                    route_path = match.group(1)
                    routes_found.append(route_path)

            if routes_found:
                for route_path in routes_found:
                    node = Node(
                        name=f"Route: {route_path}",
                        type=NodeType.API_ENDPOINT,
                        layer=Layer.BACKEND,
                        summary=f"{label} route: {route_path} in {rel}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    self.storage.create_default_context(node)
                    route_count += 1
                existing_files.add(rel)
            else:
                if rel not in existing_files:
                    layer = self._guess_layer(rel)
                    node_type = self._guess_type(rel)
                    node = Node(
                        name=f.stem,
                        type=node_type,
                        layer=layer,
                        summary=f"Source: {rel}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    self.storage.create_default_context(node)
                    file_count += 1
                    existing_files.add(rel)

        console.print(f"  Found {route_count} routes, {file_count} source files")

    # ── Generic Python scanning ──────────────────────────────────────

    def _scan_generic_python(self, graph: GraphData) -> None:
        console.print("[cyan]Scanning Python source files...[/cyan]")
        py_files = self._find_files("*.py")
        count = 0

        for f in py_files:
            if f.name == "__init__.py":
                continue
            rel = self.relative(f)
            layer = self._guess_layer(rel)
            node_type = self._guess_type(rel)

            node = Node(
                name=f.stem,
                type=node_type,
                layer=layer,
                summary=f"Source: {rel}",
                file_path=rel,
            )
            graph.nodes.append(node)
            self.storage.create_default_context(node)
            count += 1

        console.print(f"  Found {count} Python source files")

    # ── Model scanning (all frameworks) ──────────────────────────────

    def _scan_models(self, graph: GraphData) -> None:
        model_files = self._find_files("models.py", "models/*.py")
        existing = {n.file_path for n in graph.nodes if n.file_path}
        model_nodes: dict[str, str] = {}
        count = 0

        for f in model_files:
            if f.name == "__init__.py":
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel = self.relative(f)

            for pattern in [DJANGO_MODEL_PATTERN, SQLALCHEMY_MODEL_PATTERN]:
                for match in pattern.finditer(content):
                    model_name = match.group(1)
                    if model_name in model_nodes:
                        continue
                    node = Node(
                        name=f"Model: {model_name}",
                        type=NodeType.DB_TABLE,
                        layer=Layer.DATABASE,
                        summary=f"Database model: {model_name} in {rel}",
                        file_path=rel,
                    )
                    graph.nodes.append(node)
                    model_nodes[model_name] = node.id
                    self.storage.create_default_context(node)
                    count += 1

            for fk_match in DJANGO_FK_PATTERN.finditer(content):
                source_model = None
                for m in DJANGO_MODEL_PATTERN.finditer(content):
                    if m.start() < fk_match.start():
                        source_model = m.group(1)
                related_model = fk_match.group(2)
                if source_model and source_model in model_nodes and related_model in model_nodes:
                    graph.edges.append(Edge(
                        source=model_nodes[source_model],
                        target=model_nodes[related_model],
                        label=f"{source_model} -> {related_model}",
                        type=EdgeType.FOREIGN_KEY,
                    ))

            for rel_match in SQLALCHEMY_FK_PATTERN.finditer(content):
                source_model = None
                for m in SQLALCHEMY_MODEL_PATTERN.finditer(content):
                    if m.start() < rel_match.start():
                        source_model = m.group(1)
                related_model = rel_match.group(2)
                if source_model and source_model in model_nodes and related_model in model_nodes:
                    graph.edges.append(Edge(
                        source=model_nodes[source_model],
                        target=model_nodes[related_model],
                        label=f"{source_model} -> {related_model}",
                        type=EdgeType.FOREIGN_KEY,
                    ))

        if count:
            console.print(f"  Found {count} database models")

    # ── Import edge inference (AST-based) ─────────────────────────────

    def _infer_import_edges(self, graph: GraphData) -> None:
        file_node_map: dict[str, str] = {}
        for node in graph.nodes:
            if node.file_path:
                file_node_map[node.file_path] = node.id

        existing_pairs: set[tuple[str, str]] = {(e.source, e.target) for e in graph.edges}
        edge_count = 0

        console.print("[cyan]Analyzing Python imports...[/cyan]")

        for node in graph.nodes:
            if not node.file_path or not node.file_path.endswith(".py"):
                continue
            file_path = self.root / node.file_path.replace("/", "\\")
            if not file_path.is_file():
                file_path = self.root / node.file_path
            if not file_path.is_file():
                continue

            source_id = file_node_map.get(node.file_path)
            if not source_id:
                continue

            modules = self._extract_imports_ast(file_path)
            for module in modules:
                resolved = self._resolve_import(file_path, module)
                if resolved:
                    target_rel = self.relative(resolved)
                    target_id = file_node_map.get(target_rel)
                    if target_id and target_id != source_id and (source_id, target_id) not in existing_pairs:
                        graph.edges.append(Edge(
                            source=source_id,
                            target=target_id,
                            label="imports",
                            type=EdgeType.DEPENDENCY,
                        ))
                        existing_pairs.add((source_id, target_id))
                        edge_count += 1

        console.print(f"  Found {edge_count} import relationships")

    def _resolve_import(self, source: Path, module: str) -> Path | None:
        if module.startswith("."):
            level = len(module) - len(module.lstrip("."))
            module_path = module.lstrip(".").replace(".", "/")
            base = source.parent
            for _ in range(level - 1):
                base = base.parent
            candidate = base / module_path
        else:
            module_path = module.replace(".", "/")
            candidate = self.root / module_path
            if not candidate.with_suffix(".py").is_file():
                candidate = self.root / "src" / module_path

        if candidate.with_suffix(".py").is_file():
            return candidate.with_suffix(".py")
        init = candidate / "__init__.py"
        if init.is_file():
            return init
        return None

    # ── Helpers ──────────────────────────────────────────────────────

    def _guess_layer(self, rel: str) -> Layer:
        rel_lower = rel.lower()
        if any(p in rel_lower for p in ["templates/", "static/", "frontend/"]):
            return Layer.FRONTEND
        if any(p in rel_lower for p in ["models/", "schemas/", "migrations/", "db/", "database/"]):
            return Layer.DATABASE
        if any(p in rel_lower for p in [
            "views/", "api/", "routes/", "controllers/", "endpoints/",
            "services/", "handlers/", "middleware/", "backend/",
        ]):
            return Layer.BACKEND
        if any(p in rel_lower for p in ["utils/", "lib/", "helpers/", "common/", "shared/", "config/"]):
            return Layer.SHARED
        return Layer.BACKEND

    def _guess_type(self, rel: str) -> NodeType:
        rel_lower = rel.lower()
        if any(p in rel_lower for p in ["views/", "views.py"]):
            return NodeType.API_ENDPOINT
        if any(p in rel_lower for p in ["api/", "routes/", "controllers/", "endpoints/", "handlers/"]):
            return NodeType.API_ENDPOINT
        if any(p in rel_lower for p in ["models/", "models.py", "schemas/"]):
            return NodeType.DB_TABLE
        if any(p in rel_lower for p in ["services/", "service.py"]):
            return NodeType.SERVICE
        if any(p in rel_lower for p in ["middleware/", "middleware.py"]):
            return NodeType.MIDDLEWARE
        if any(p in rel_lower for p in ["templates/"]):
            return NodeType.PAGE
        if any(p in rel_lower for p in ["utils/", "lib/", "helpers/", "common/"]):
            return NodeType.UTILITY
        if any(p in rel_lower for p in ["config/", "config.py", "settings.py", "settings/"]):
            return NodeType.CONFIG
        if any(p in rel_lower for p in ["forms/", "forms.py", "serializers/", "serializers.py"]):
            return NodeType.COMPONENT
        if any(p in rel_lower for p in ["admin.py"]):
            return NodeType.CONFIG
        return NodeType.UTILITY
