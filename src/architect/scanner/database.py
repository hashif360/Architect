"""Database credential discovery and live schema introspection.

Searches the project for database connection information (env files, config
files, connection strings in code, Azure KeyVault, AWS Secrets Manager),
connects to the database, and introspects tables, columns, and foreign keys
to create graph nodes and edges.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

from rich.console import Console

from ..models import Edge, EdgeType, GraphData, Layer, Node, NodeType

console = Console()

# ── Credential patterns ──────────────────────────────────────────────

CONNECTION_STRING_PATTERNS = [
    re.compile(r'(?:DATABASE_URL|DB_URL|DATABASE_URI|DB_URI|SQLALCHEMY_DATABASE_URI|CONN_STRING|CONNECTION_STRING)\s*[=:]\s*["\']?([^\s"\']+)', re.IGNORECASE),
    re.compile(r'(?:postgresql|postgres|mysql|mssql|sqlserver|sqlite|mongodb)(?:\+\w+)?://[^\s"\'`]+'),
    re.compile(r'Server\s*=\s*[^;]+;\s*(?:Database|Initial Catalog)\s*=\s*[^;]+', re.IGNORECASE),
    re.compile(r'Data Source\s*=\s*[^;]+;\s*(?:Database|Initial Catalog)\s*=\s*[^;]+', re.IGNORECASE),
]

ENV_DB_KEYS = {
    "DATABASE_URL", "DB_URL", "DATABASE_URI", "DB_URI",
    "SQLALCHEMY_DATABASE_URI", "CONN_STRING", "CONNECTION_STRING",
    "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_DATABASE",
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD",
    "MSSQL_HOST", "MSSQL_PORT", "MSSQL_DATABASE", "MSSQL_USER", "MSSQL_PASSWORD",
    "SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD",
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "AZURE_SQL_CONNECTION_STRING", "AZURE_POSTGRESQL_CONNECTION_STRING",
    "AZURE_MYSQL_CONNECTION_STRING",
}

KEYVAULT_URL_KEYS = {
    "AZURE_KEY_VAULT_URL", "KEY_VAULT_URL", "KEYVAULT_URL",
    "AZURE_KEY_VAULT_URI", "KEY_VAULT_URI", "KEYVAULT_URI",
    "AZURE_KEYVAULT_URL", "AZURE_KEYVAULT_URI",
    "KEY_VAULT_NAME", "KEYVAULT_NAME",
}

KEYVAULT_SECRET_KEYS = {
    "DB_CONNECTION_STRING_SECRET", "DATABASE_URL_SECRET",
    "SQL_CONNECTION_SECRET", "DB_PASSWORD_SECRET",
}

# Secret names commonly used in KeyVault for DB credentials
KEYVAULT_DB_SECRET_NAMES = [
    "database-connection-string", "db-connection-string",
    "database-url", "db-url", "sql-connection-string",
    "postgres-connection-string", "mysql-connection-string",
    "mssql-connection-string", "connectionstring",
    "db-password", "database-password", "sql-password",
]

AWS_SECRET_PATTERNS = [
    re.compile(r'''(?:secretsmanager|secrets_manager).*?SecretId['":\s]+([^'"}\s,]+)''', re.IGNORECASE),
    re.compile(r'AWS_SECRET_NAME\s*[=:]\s*["\']?([^\s"\']+)', re.IGNORECASE),
]


@dataclass
class DBCredentials:
    """Parsed database connection info."""
    db_type: str = ""          # postgresql, mysql, mssql, sqlite, mongodb
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""
    url: str = ""              # full connection string if available
    source: str = ""           # where the creds were found
    extra: dict = field(default_factory=dict)

    @property
    def masked_display(self) -> str:
        if self.url:
            masked = re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', self.url)
            return f"{self.db_type} | {masked}"
        return f"{self.db_type} | {self.host}:{self.port}/{self.database} (user: {self.username})"

    @property
    def is_complete(self) -> bool:
        if self.url:
            return True
        if self.db_type == "sqlite":
            return bool(self.database)
        return bool(self.host and self.database)


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env-style file into a dict, ignoring comments and blanks.

    Properly handles quoted values so that special characters like ``#``
    inside single- or double-quoted strings are preserved verbatim.
    Unquoted values treat ``#`` preceded by whitespace as an inline comment.
    """
    result = {}
    if not path.is_file():
        return result
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            else:
                comment_pos = value.find(" #")
                if comment_pos != -1:
                    value = value[:comment_pos].rstrip()

            result[key] = value
    except Exception:
        pass
    return result


def _parse_url_credentials(url: str) -> DBCredentials:
    """Parse a database URL into DBCredentials.

    Handles special characters (especially ``#``) in the credentials
    portion of the URL by percent-encoding them before handing the URL
    to ``urlparse``, which otherwise treats ``#`` as a fragment delimiter.
    """
    creds = DBCredentials(url=url)

    url_clean = url.strip()

    type_map = {
        "postgresql": "postgresql", "postgres": "postgresql",
        "mysql": "mysql", "mariadb": "mysql",
        "mssql": "mssql", "sqlserver": "mssql",
        "sqlite": "sqlite",
        "mongodb": "mongodb", "mongodb+srv": "mongodb",
    }

    scheme = url_clean.split("://")[0].split("+")[0].lower() if "://" in url_clean else ""
    creds.db_type = type_map.get(scheme, scheme)

    if creds.db_type == "sqlite":
        creds.database = url_clean.split("///")[-1] if "///" in url_clean else ""
        return creds

    url_for_parse = url_clean
    if "://" in url_clean and "@" in url_clean:
        scheme_end = url_clean.index("://") + 3
        at_pos = url_clean.rindex("@")
        creds_part = url_clean[scheme_end:at_pos]
        if "#" in creds_part:
            encoded_creds = creds_part.replace("#", "%23")
            url_for_parse = url_clean[:scheme_end] + encoded_creds + url_clean[at_pos:]

    try:
        parsed = urlparse(url_for_parse)
        creds.host = parsed.hostname or ""
        creds.port = parsed.port or 0
        creds.database = parsed.path.lstrip("/") if parsed.path else ""
        creds.username = parsed.username or ""
        creds.password = parsed.password or ""
    except Exception:
        pass

    return creds


def _parse_ado_connection_string(conn: str) -> DBCredentials:
    """Parse an ADO.NET-style connection string (Server=...;Database=...;)."""
    creds = DBCredentials(url=conn, db_type="mssql")
    parts = {}
    for segment in conn.split(";"):
        segment = segment.strip()
        if "=" not in segment:
            continue
        key, _, val = segment.partition("=")
        parts[key.strip().lower()] = val.strip()

    creds.host = parts.get("server", parts.get("data source", parts.get("host", "")))
    creds.database = parts.get("database", parts.get("initial catalog", ""))
    creds.username = parts.get("user id", parts.get("uid", parts.get("user", "")))
    creds.password = parts.get("password", parts.get("pwd", ""))

    port_str = parts.get("port", "")
    if "," in creds.host:
        host_parts = creds.host.split(",")
        creds.host = host_parts[0]
        try:
            creds.port = int(host_parts[1])
        except ValueError:
            pass
    elif port_str:
        try:
            creds.port = int(port_str)
        except ValueError:
            pass

    return creds


class CredentialDiscovery:
    """Discovers database credentials from project files and secret stores."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.env_vars: dict[str, str] = {}
        self._project_db_type: str | None = None

    # ── Project-level DB type detection ─────────────────────────────────

    def _detect_db_type_from_project(self) -> str:
        """Detect database type by inspecting actual project code & config.

        Checks (in priority order):
         1. Prisma schema ``provider``
         2. package.json dependencies (JS/TS driver packages)
         3. requirements.txt / Pipfile / pyproject.toml (Python driver packages)
         4. composer.json (PHP driver packages)
         5. .csproj NuGet references (.NET driver packages)
         6. Source-code imports for database drivers
         7. Default port as a last-resort hint
        """
        if self._project_db_type is not None:
            return self._project_db_type

        db_type = (
            self._detect_from_prisma()
            or self._detect_from_package_json()
            or self._detect_from_python_deps()
            or self._detect_from_composer()
            or self._detect_from_csproj()
            or self._detect_from_code_imports()
        )
        self._project_db_type = db_type
        return db_type

    def _detect_from_prisma(self) -> str:
        for p in (self.root / "prisma" / "schema.prisma", self.root / "schema.prisma"):
            if not p.is_file():
                continue
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            m = re.search(r'provider\s*=\s*["\'](\w+)["\']', content)
            if m:
                provider = m.group(1).lower()
                mapping = {"mysql": "mysql", "postgresql": "postgresql", "postgres": "postgresql",
                           "sqlserver": "mssql", "sqlite": "sqlite", "mongodb": "mongodb"}
                if provider in mapping:
                    return mapping[provider]
        return ""

    def _detect_from_package_json(self) -> str:
        pkg_path = self.root / "package.json"
        if not pkg_path.is_file():
            return ""
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        all_deps: set[str] = set()
        all_deps.update(data.get("dependencies", {}).keys())
        all_deps.update(data.get("devDependencies", {}).keys())

        mysql_pkgs = {"mysql", "mysql2", "mysqljs", "promise-mysql", "mysql2-promise", "serverless-mysql"}
        pg_pkgs = {"pg", "pg-promise", "postgres", "pg-native", "pg-pool", "slonik", "massive"}
        mssql_pkgs = {"mssql", "tedious"}
        sqlite_pkgs = {"better-sqlite3", "sqlite3", "sql.js"}

        if all_deps & mysql_pkgs:
            return "mysql"
        if all_deps & pg_pkgs:
            return "postgresql"
        if all_deps & mssql_pkgs:
            return "mssql"
        if all_deps & sqlite_pkgs:
            return "sqlite"
        return ""

    def _detect_from_python_deps(self) -> str:
        dep_files = {
            "requirements.txt": self._read_flat_deps,
            "requirements-dev.txt": self._read_flat_deps,
            "Pipfile": self._read_flat_deps,
            "pyproject.toml": self._read_flat_deps,
            "setup.cfg": self._read_flat_deps,
        }
        all_deps: set[str] = set()
        for name, reader in dep_files.items():
            fp = self.root / name
            if fp.is_file():
                all_deps.update(reader(fp))

        mysql_pkgs = {"pymysql", "mysqlclient", "mysql-connector-python", "mysql-connector",
                      "aiomysql", "asyncmy", "mysql"}
        pg_pkgs = {"psycopg2", "psycopg2-binary", "psycopg", "asyncpg", "aiopg", "pg8000"}
        mssql_pkgs = {"pyodbc", "pymssql", "aioodbc"}

        if all_deps & mysql_pkgs:
            return "mysql"
        if all_deps & pg_pkgs:
            return "postgresql"
        if all_deps & mssql_pkgs:
            return "mssql"
        return ""

    @staticmethod
    def _read_flat_deps(path: Path) -> set[str]:
        """Return lowercased package names from a requirements-style or TOML file."""
        names: set[str] = set()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return names
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            pkg = re.split(r"[=<>!~;\s]", line)[0].strip().strip('"').strip("'").lower()
            if pkg and not pkg.startswith("-"):
                names.add(pkg)
        return names

    def _detect_from_composer(self) -> str:
        fp = self.root / "composer.json"
        if not fp.is_file():
            return ""
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            return ""
        all_deps: set[str] = set()
        all_deps.update(data.get("require", {}).keys())
        all_deps.update(data.get("require-dev", {}).keys())

        if any("mysql" in d for d in all_deps) or "ext-mysqli" in all_deps:
            return "mysql"
        if any("pgsql" in d or "postgres" in d for d in all_deps) or "ext-pgsql" in all_deps:
            return "postgresql"
        if any("sqlsrv" in d or "mssql" in d for d in all_deps):
            return "mssql"
        return ""

    def _detect_from_csproj(self) -> str:
        for csproj in self.root.rglob("*.csproj"):
            try:
                content = csproj.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            content_lower = content.lower()
            if "mysql" in content_lower or "pomelo.entityframeworkcore.mysql" in content_lower:
                return "mysql"
            if "npgsql" in content_lower:
                return "postgresql"
            if "microsoft.entityframeworkcore.sqlserver" in content_lower:
                return "mssql"
        return ""

    def _detect_from_code_imports(self) -> str:
        """Scan a sample of source files for database driver imports."""
        skip_dirs = {"node_modules", ".git", "__pycache__", ".next", ".nuxt",
                     "dist", "build", ".venv", "venv", "env"}

        mysql_markers = re.compile(
            r"(?:import\s+pymysql|import\s+mysqlclient|import\s+mysql\.connector"
            r"|from\s+pymysql|require\s*\(\s*['\"]mysql2?['\"]"
            r"|import\s+['\"]mysql2?['\"]"
            r"|from\s+['\"]mysql2?['\"]"
            r"|using\s+MySql\.Data"
            r"|Pomelo\.EntityFrameworkCore\.MySql)",
            re.IGNORECASE,
        )
        pg_markers = re.compile(
            r"(?:import\s+psycopg2|import\s+asyncpg|from\s+psycopg2"
            r"|require\s*\(\s*['\"]pg['\"]"
            r"|import\s+['\"]pg['\"]"
            r"|from\s+['\"]pg['\"]"
            r"|using\s+Npgsql)",
            re.IGNORECASE,
        )
        mssql_markers = re.compile(
            r"(?:import\s+pyodbc|import\s+pymssql|from\s+pyodbc|from\s+pymssql"
            r"|require\s*\(\s*['\"]mssql['\"]"
            r"|require\s*\(\s*['\"]tedious['\"]"
            r"|using\s+Microsoft\.Data\.SqlClient"
            r"|using\s+System\.Data\.SqlClient)",
            re.IGNORECASE,
        )

        counts = {"mysql": 0, "postgresql": 0, "mssql": 0}
        files_checked = 0
        max_files = 200

        for ext in (".py", ".ts", ".js", ".cs", ".java", ".go", ".rb", ".php"):
            for f in self.root.rglob(f"*{ext}"):
                if any(skip in f.parts for skip in skip_dirs):
                    continue
                if files_checked >= max_files:
                    break
                files_checked += 1
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if mysql_markers.search(content):
                    counts["mysql"] += 1
                if pg_markers.search(content):
                    counts["postgresql"] += 1
                if mssql_markers.search(content):
                    counts["mssql"] += 1

        if max(counts.values()) > 0:
            return max(counts, key=counts.get)  # type: ignore[arg-type]
        return ""

    def discover(self) -> list[DBCredentials]:
        """Search all sources and return discovered credentials."""
        all_creds: list[DBCredentials] = []

        console.print("[cyan]Searching for database credentials...[/cyan]")

        env_creds = self._search_env_files()
        all_creds.extend(env_creds)

        config_creds = self._search_config_files()
        all_creds.extend(config_creds)

        code_creds = self._search_code_files()
        all_creds.extend(code_creds)

        docker_creds = self._search_docker_compose()
        all_creds.extend(docker_creds)

        kv_creds = self._search_keyvault()
        all_creds.extend(kv_creds)

        aws_creds = self._search_aws_secrets()
        all_creds.extend(aws_creds)

        seen_urls: set[str] = set()
        unique: list[DBCredentials] = []
        for c in all_creds:
            key = c.url or f"{c.host}:{c.port}/{c.database}"
            if key not in seen_urls and c.is_complete:
                seen_urls.add(key)
                unique.append(c)

        if unique:
            console.print(f"  [green]Found {len(unique)} database connection(s):[/green]")
            for c in unique:
                console.print(f"    {c.masked_display}  [dim](from {c.source})[/dim]")
        else:
            console.print("  [dim]No database credentials found.[/dim]")

        return unique

    def _search_env_files(self) -> list[DBCredentials]:
        """Search .env files for DB credentials."""
        creds: list[DBCredentials] = []
        env_files = [
            ".env", ".env.local", ".env.development", ".env.dev",
            ".env.production", ".env.staging", ".env.test",
        ]

        for env_name in env_files:
            env_path = self.root / env_name
            env_data = _parse_env_file(env_path)
            if not env_data:
                continue

            self.env_vars.update(env_data)

            for key in ENV_DB_KEYS:
                val = env_data.get(key, "")
                if not val:
                    continue

                if "://" in val:
                    c = _parse_url_credentials(val)
                    c.source = env_name
                    creds.append(c)
                elif "Server=" in val or "Data Source=" in val:
                    c = _parse_ado_connection_string(val)
                    c.source = env_name
                    creds.append(c)

            host = env_data.get("DB_HOST", env_data.get("POSTGRES_HOST", env_data.get("MYSQL_HOST", env_data.get("PGHOST", env_data.get("MSSQL_HOST", "")))))
            db_name = env_data.get("DB_NAME", env_data.get("DB_DATABASE", env_data.get("POSTGRES_DB", env_data.get("MYSQL_DATABASE", env_data.get("PGDATABASE", env_data.get("MSSQL_DATABASE", ""))))))
            if host and db_name:
                db_type = ""

                if any(k.startswith("MYSQL") for k in env_data):
                    db_type = "mysql"
                elif any(k.startswith("POSTGRES") or k.startswith("PG") for k in env_data):
                    db_type = "postgresql"
                elif any(k.startswith("MSSQL") or k.startswith("SQL_") for k in env_data):
                    db_type = "mssql"

                if not db_type:
                    port_str = env_data.get("DB_PORT", "0")
                    try:
                        port_val = int(port_str)
                    except ValueError:
                        port_val = 0
                    port_hints = {3306: "mysql", 5432: "postgresql", 1433: "mssql"}
                    db_type = port_hints.get(port_val, "")

                if not db_type:
                    db_type = self._detect_db_type_from_project()

                if not db_type:
                    db_type = "unknown"

                port_raw = env_data.get("DB_PORT", env_data.get("PGPORT", env_data.get("MYSQL_PORT", env_data.get("MSSQL_PORT", "0"))))
                try:
                    port_int = int(port_raw) if port_raw else 0
                except ValueError:
                    port_int = 0

                c = DBCredentials(
                    db_type=db_type,
                    host=host,
                    port=port_int,
                    database=db_name,
                    username=env_data.get("DB_USER", env_data.get("POSTGRES_USER", env_data.get("MYSQL_USER", env_data.get("PGUSER", env_data.get("MSSQL_USER", ""))))),
                    password=env_data.get("DB_PASSWORD", env_data.get("POSTGRES_PASSWORD", env_data.get("MYSQL_PASSWORD", env_data.get("PGPASSWORD", env_data.get("SQL_PASSWORD", ""))))),
                    source=env_name,
                )
                creds.append(c)

        return creds

    def _search_config_files(self) -> list[DBCredentials]:
        """Search config/settings files for DB credentials."""
        creds: list[DBCredentials] = []

        for pattern in ["appsettings.json", "appsettings.*.json"]:
            for f in self.root.glob(pattern):
                creds.extend(self._parse_appsettings(f))

        db_config_paths = [
            "config/database.json", "config/database.yml", "config/database.yaml",
            "config/config.json", "config/default.json",
        ]
        for p in db_config_paths:
            fp = self.root / p
            if fp.is_file() and fp.suffix == ".json":
                creds.extend(self._parse_json_config(fp))

        prisma_paths = [self.root / "prisma" / "schema.prisma", self.root / "schema.prisma"]
        for pp in prisma_paths:
            if pp.is_file():
                creds.extend(self._parse_prisma_datasource(pp))

        return creds

    def _parse_appsettings(self, path: Path) -> list[DBCredentials]:
        """Parse .NET appsettings.json for connection strings."""
        creds = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return creds

        conn_strings = data.get("ConnectionStrings", {})
        for name, val in conn_strings.items():
            if isinstance(val, str) and val:
                if "://" in val:
                    c = _parse_url_credentials(val)
                else:
                    c = _parse_ado_connection_string(val)
                c.source = f"{path.name} [{name}]"
                creds.append(c)

        return creds

    def _parse_json_config(self, path: Path) -> list[DBCredentials]:
        """Parse generic JSON config for DB settings."""
        creds = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return creds

        def walk(obj: Any, prefix: str = "") -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full_key = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, str) and ("://" in v or "Server=" in v):
                        if any(db in v.lower() for db in ("postgres", "mysql", "mssql", "sqlserver", "sqlite", "mongodb")):
                            if "://" in v:
                                c = _parse_url_credentials(v)
                            else:
                                c = _parse_ado_connection_string(v)
                            c.source = f"{path.name} [{full_key}]"
                            creds.append(c)
                    elif isinstance(v, (dict, list)):
                        walk(v, full_key)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{prefix}[{i}]")

        walk(data)
        return creds

    def _parse_prisma_datasource(self, path: Path) -> list[DBCredentials]:
        """Extract datasource URL from Prisma schema."""
        creds = []
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return creds

        ds_match = re.search(r'datasource\s+\w+\s*\{([^}]+)\}', content, re.DOTALL)
        if not ds_match:
            return creds

        body = ds_match.group(1)
        url_match = re.search(r'url\s*=\s*env\(\s*["\'](\w+)["\']\s*\)', body)
        if url_match:
            env_key = url_match.group(1)
            env_val = self.env_vars.get(env_key, os.environ.get(env_key, ""))
            if env_val and "://" in env_val:
                c = _parse_url_credentials(env_val)
                c.source = f"prisma/schema.prisma (env: {env_key})"
                creds.append(c)
        else:
            url_match = re.search(r'url\s*=\s*["\']([^"\']+)["\']', body)
            if url_match:
                c = _parse_url_credentials(url_match.group(1))
                c.source = "prisma/schema.prisma"
                creds.append(c)

        return creds

    def _search_code_files(self) -> list[DBCredentials]:
        """Search source code for embedded connection strings."""
        creds = []
        skip_dirs = {
            "node_modules", ".git", ".github", "__pycache__", ".next",
            ".nuxt", "dist", "build", ".venv", "venv", "env",
        }
        extensions = {".py", ".ts", ".js", ".cs", ".java", ".go", ".rb", ".php", ".yaml", ".yml", ".toml"}

        for ext in extensions:
            for f in self.root.rglob(f"*{ext}"):
                if any(skip in f.parts for skip in skip_dirs):
                    continue
                if not f.is_file():
                    continue
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                for pattern in CONNECTION_STRING_PATTERNS:
                    for match in pattern.finditer(content):
                        val = match.group(1) if match.lastindex else match.group(0)
                        if "env(" in val.lower() or "${" in val or "process.env" in val:
                            continue
                        if "://" in val:
                            c = _parse_url_credentials(val)
                            c.source = str(f.relative_to(self.root))
                            creds.append(c)
                        elif "Server=" in val or "Data Source=" in val:
                            c = _parse_ado_connection_string(val)
                            c.source = str(f.relative_to(self.root))
                            creds.append(c)
        return creds

    def _search_docker_compose(self) -> list[DBCredentials]:
        """Search docker-compose files for DB service definitions."""
        creds = []
        compose_files = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]

        for cf_name in compose_files:
            cf_path = self.root / cf_name
            if not cf_path.is_file():
                continue

            try:
                content = cf_path.read_text(encoding="utf-8")
            except Exception:
                continue

            db_images = {
                "postgres": "postgresql", "mysql": "mysql", "mariadb": "mysql",
                "mcr.microsoft.com/mssql": "mssql", "mongo": "mongodb",
            }

            for image_prefix, db_type in db_images.items():
                if image_prefix in content:
                    env_block = re.findall(r'environment:\s*\n((?:\s+-\s+.+\n?)+)', content)
                    for block in env_block:
                        env = {}
                        for line in block.splitlines():
                            line = line.strip().lstrip("- ").strip()
                            if "=" in line:
                                k, _, v = line.partition("=")
                                env[k.strip()] = v.strip()

                        host = "localhost"
                        port_match = re.search(r'"(\d+):\d+"', content)
                        port = int(port_match.group(1)) if port_match else 0

                        if db_type == "postgresql":
                            c = DBCredentials(
                                db_type=db_type, host=host, port=port or 5432,
                                database=env.get("POSTGRES_DB", ""),
                                username=env.get("POSTGRES_USER", "postgres"),
                                password=env.get("POSTGRES_PASSWORD", ""),
                                source=cf_name,
                            )
                        elif db_type == "mysql":
                            c = DBCredentials(
                                db_type=db_type, host=host, port=port or 3306,
                                database=env.get("MYSQL_DATABASE", ""),
                                username=env.get("MYSQL_USER", "root"),
                                password=env.get("MYSQL_PASSWORD", env.get("MYSQL_ROOT_PASSWORD", "")),
                                source=cf_name,
                            )
                        elif db_type == "mssql":
                            c = DBCredentials(
                                db_type=db_type, host=host, port=port or 1433,
                                database=env.get("MSSQL_DATABASE", "master"),
                                username="sa",
                                password=env.get("SA_PASSWORD", env.get("MSSQL_SA_PASSWORD", "")),
                                source=cf_name,
                            )
                        else:
                            continue

                        if c.is_complete:
                            creds.append(c)

        return creds

    def _search_keyvault(self) -> list[DBCredentials]:
        """Search for Azure KeyVault references and fetch DB secrets."""
        creds = []

        vault_url = ""
        for key in KEYVAULT_URL_KEYS:
            val = self.env_vars.get(key, os.environ.get(key, ""))
            if val:
                if not val.startswith("http"):
                    vault_url = f"https://{val}.vault.azure.net"
                else:
                    vault_url = val
                break

        if not vault_url:
            for pattern in ["appsettings.json", "appsettings.*.json"]:
                for f in self.root.glob(pattern):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        kv_url = data.get("KeyVault", {}).get("Url", "")
                        if not kv_url:
                            kv_url = data.get("AzureKeyVault", {}).get("VaultUrl", "")
                        if kv_url:
                            vault_url = kv_url
                            break
                    except Exception:
                        pass

        if not vault_url:
            return creds

        console.print(f"  [cyan]Found Azure KeyVault:[/cyan] {vault_url}")

        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            console.print("  [yellow]Install azure-identity and azure-keyvault-secrets to fetch KeyVault secrets:[/yellow]")
            console.print("    pip install azure-identity azure-keyvault-secrets")
            return creds

        try:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=vault_url, credential=credential)

            explicit_secret = ""
            for key in KEYVAULT_SECRET_KEYS:
                val = self.env_vars.get(key, os.environ.get(key, ""))
                if val:
                    explicit_secret = val
                    break

            secrets_to_try = []
            if explicit_secret:
                secrets_to_try.append(explicit_secret)
            secrets_to_try.extend(KEYVAULT_DB_SECRET_NAMES)

            for secret_name in secrets_to_try:
                try:
                    secret = client.get_secret(secret_name)
                    val = secret.value
                    if val and ("://" in val or "Server=" in val):
                        if "://" in val:
                            c = _parse_url_credentials(val)
                        else:
                            c = _parse_ado_connection_string(val)
                        c.source = f"KeyVault ({vault_url}) secret: {secret_name}"
                        creds.append(c)
                        console.print(f"  [green]Retrieved DB credentials from KeyVault secret:[/green] {secret_name}")
                        break
                except Exception:
                    continue

            if not creds:
                console.print("  [dim]No DB connection secrets found in KeyVault with known names.[/dim]")
                console.print("  [dim]Listing all secrets to search...[/dim]")
                try:
                    for prop in client.list_properties_of_secrets():
                        name_lower = prop.name.lower()
                        if any(kw in name_lower for kw in ("connection", "database", "db", "sql", "postgres", "mysql", "mongo", "conn")):
                            try:
                                secret = client.get_secret(prop.name)
                                val = secret.value
                                if val and ("://" in val or "Server=" in val or "Host=" in val):
                                    if "://" in val:
                                        c = _parse_url_credentials(val)
                                    else:
                                        c = _parse_ado_connection_string(val)
                                    c.source = f"KeyVault ({vault_url}) secret: {prop.name}"
                                    creds.append(c)
                                    console.print(f"  [green]Found DB credentials in KeyVault secret:[/green] {prop.name}")
                            except Exception:
                                continue
                except Exception as e:
                    console.print(f"  [yellow]Could not list KeyVault secrets:[/yellow] {e}")

        except Exception as e:
            console.print(f"  [yellow]Could not connect to KeyVault:[/yellow] {e}")
            console.print("  [dim]Make sure you're logged in with 'az login' or have AZURE_* env vars set.[/dim]")

        return creds

    def _search_aws_secrets(self) -> list[DBCredentials]:
        """Search for AWS Secrets Manager references."""
        creds = []

        secret_name = self.env_vars.get("AWS_SECRET_NAME", os.environ.get("AWS_SECRET_NAME", ""))
        if not secret_name:
            for ext in (".py", ".ts", ".js", ".cs"):
                for f in self.root.rglob(f"*{ext}"):
                    if any(skip in f.parts for skip in ("node_modules", ".git", "__pycache__", "dist", "build", ".venv")):
                        continue
                    try:
                        content = f.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    for pattern in AWS_SECRET_PATTERNS:
                        match = pattern.search(content)
                        if match:
                            secret_name = match.group(1)
                            break
                    if secret_name:
                        break

        if not secret_name:
            return creds

        console.print(f"  [cyan]Found AWS Secrets Manager reference:[/cyan] {secret_name}")

        try:
            import boto3
        except ImportError:
            console.print("  [yellow]Install boto3 to fetch AWS secrets:[/yellow]")
            console.print("    pip install boto3")
            return creds

        try:
            region = self.env_vars.get("AWS_REGION", os.environ.get("AWS_REGION", "us-east-1"))
            client = boto3.client("secretsmanager", region_name=region)
            response = client.get_secret_value(SecretId=secret_name)
            secret_val = response.get("SecretString", "")

            if secret_val:
                try:
                    secret_data = json.loads(secret_val)
                    if "host" in secret_data and "dbname" in secret_data:
                        engine = secret_data.get("engine", "postgresql")
                        c = DBCredentials(
                            db_type=engine,
                            host=secret_data.get("host", ""),
                            port=int(secret_data.get("port", 0)),
                            database=secret_data.get("dbname", secret_data.get("database", "")),
                            username=secret_data.get("username", secret_data.get("user", "")),
                            password=secret_data.get("password", ""),
                            source=f"AWS Secrets Manager: {secret_name}",
                        )
                        creds.append(c)
                except json.JSONDecodeError:
                    if "://" in secret_val:
                        c = _parse_url_credentials(secret_val)
                        c.source = f"AWS Secrets Manager: {secret_name}"
                        creds.append(c)

        except Exception as e:
            console.print(f"  [yellow]Could not fetch AWS secret:[/yellow] {e}")

        return creds


class DatabaseIntrospector:
    """Connects to a database and introspects its schema."""

    def __init__(self, creds: DBCredentials):
        self.creds = creds

    def introspect(self) -> GraphData:
        """Connect and return nodes/edges for discovered schema."""
        graph = GraphData()
        db_type = self.creds.db_type.lower()

        console.print(f"  [cyan]Connecting to {db_type} database...[/cyan]")

        try:
            if db_type in ("postgresql", "postgres"):
                self._introspect_postgresql(graph)
            elif db_type == "mysql":
                self._introspect_mysql(graph)
            elif db_type in ("mssql", "sqlserver"):
                self._introspect_mssql(graph)
            elif db_type == "sqlite":
                self._introspect_sqlite(graph)
            else:
                console.print(f"  [yellow]Unsupported DB type for introspection:[/yellow] {db_type}")
                return graph
        except Exception as e:
            console.print(f"  [red]Database connection failed:[/red] {e}")
            return graph

        console.print(f"  [green]Introspected {len(graph.nodes)} tables, {len(graph.edges)} foreign keys[/green]")
        return graph

    def _get_pg_connection(self):
        try:
            import psycopg2
            if self.creds.url:
                return psycopg2.connect(self.creds.url)
            return psycopg2.connect(
                host=self.creds.host,
                port=self.creds.port or 5432,
                dbname=self.creds.database,
                user=self.creds.username,
                password=self.creds.password,
            )
        except ImportError:
            console.print("  [yellow]Install psycopg2-binary for PostgreSQL:[/yellow]")
            console.print("    pip install psycopg2-binary")
            raise

    def _introspect_postgresql(self, graph: GraphData) -> None:
        conn = self._get_pg_connection()
        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            tables = [row[0] for row in cur.fetchall()]

            table_nodes: dict[str, str] = {}
            for table_name in tables:
                cur.execute("""
                    SELECT column_name, data_type, is_nullable,
                           column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = %s
                    ORDER BY ordinal_position
                """, (table_name,))
                columns = cur.fetchall()

                col_summary = ", ".join(
                    f"{c[0]} ({c[1]}{'?' if c[2] == 'YES' else ''})"
                    for c in columns[:10]
                )
                if len(columns) > 10:
                    col_summary += f" ... +{len(columns) - 10} more"

                node = Node(
                    name=f"Table: {table_name}",
                    type=NodeType.DB_TABLE,
                    layer=Layer.DATABASE,
                    summary=f"PostgreSQL table: {table_name} ({len(columns)} columns: {col_summary})",
                    tags=["live-db", "postgresql"],
                    metadata={
                        "db_type": "postgresql",
                        "table_name": table_name,
                        "columns": [
                            {"name": c[0], "type": c[1], "nullable": c[2] == "YES", "default": c[3]}
                            for c in columns
                        ],
                    },
                )
                graph.nodes.append(node)
                table_nodes[table_name] = node.id

            cur.execute("""
                SELECT
                    tc.table_name AS source_table,
                    kcu.column_name AS source_column,
                    ccu.table_name AS target_table,
                    ccu.column_name AS target_column,
                    tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND tc.table_schema = 'public'
            """)

            for row in cur.fetchall():
                src_table, src_col, tgt_table, tgt_col, constraint = row
                src_id = table_nodes.get(src_table)
                tgt_id = table_nodes.get(tgt_table)
                if src_id and tgt_id:
                    graph.edges.append(Edge(
                        source=src_id,
                        target=tgt_id,
                        label=f"{src_table}.{src_col} -> {tgt_table}.{tgt_col}",
                        type=EdgeType.FOREIGN_KEY,
                    ))

        finally:
            conn.close()

    def _introspect_mysql(self, graph: GraphData) -> None:
        try:
            import pymysql
        except ImportError:
            console.print("  [yellow]Install pymysql for MySQL:[/yellow]")
            console.print("    pip install pymysql")
            raise

        conn = pymysql.connect(
            host=self.creds.host,
            port=self.creds.port or 3306,
            database=self.creds.database,
            user=self.creds.username,
            password=self.creds.password,
        )
        try:
            cur = conn.cursor()

            cur.execute("SHOW TABLES")
            tables = [row[0] for row in cur.fetchall()]

            table_nodes: dict[str, str] = {}
            for table_name in tables:
                cur.execute(f"DESCRIBE `{table_name}`")
                columns = cur.fetchall()

                col_summary = ", ".join(
                    f"{c[0]} ({c[1]})"
                    for c in columns[:10]
                )
                if len(columns) > 10:
                    col_summary += f" ... +{len(columns) - 10} more"

                node = Node(
                    name=f"Table: {table_name}",
                    type=NodeType.DB_TABLE,
                    layer=Layer.DATABASE,
                    summary=f"MySQL table: {table_name} ({len(columns)} columns: {col_summary})",
                    tags=["live-db", "mysql"],
                    metadata={
                        "db_type": "mysql",
                        "table_name": table_name,
                        "columns": [
                            {"name": c[0], "type": c[1], "nullable": c[2] == "YES",
                             "key": c[3], "default": c[4]}
                            for c in columns
                        ],
                    },
                )
                graph.nodes.append(node)
                table_nodes[table_name] = node.id

            cur.execute("""
                SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL
            """, (self.creds.database,))

            for row in cur.fetchall():
                src_table, src_col, tgt_table, tgt_col = row
                src_id = table_nodes.get(src_table)
                tgt_id = table_nodes.get(tgt_table)
                if src_id and tgt_id:
                    graph.edges.append(Edge(
                        source=src_id,
                        target=tgt_id,
                        label=f"{src_table}.{src_col} -> {tgt_table}.{tgt_col}",
                        type=EdgeType.FOREIGN_KEY,
                    ))

        finally:
            conn.close()

    def _introspect_mssql(self, graph: GraphData) -> None:
        try:
            import pyodbc
        except ImportError:
            try:
                import pymssql
                self._introspect_mssql_pymssql(graph, pymssql)
                return
            except ImportError:
                console.print("  [yellow]Install pyodbc or pymssql for SQL Server:[/yellow]")
                console.print("    pip install pyodbc   # or: pip install pymssql")
                raise

        driver = None
        for d in pyodbc.drivers():
            if "ODBC Driver" in d and "SQL Server" in d:
                driver = d
                break
        if not driver:
            for d in pyodbc.drivers():
                if "SQL Server" in d:
                    driver = d
                    break

        if self.creds.url and ("Server=" in self.creds.url or "Data Source=" in self.creds.url):
            conn_str = self.creds.url
            if driver and "Driver=" not in conn_str:
                conn_str = f"Driver={{{driver}}};{conn_str}"
        else:
            host = self.creds.host
            if self.creds.port and self.creds.port != 1433:
                host = f"{host},{self.creds.port}"
            conn_str = f"Driver={{{driver}}};Server={host};Database={self.creds.database};UID={self.creds.username};PWD={self.creds.password}"

        conn = pyodbc.connect(conn_str)
        self._introspect_mssql_connection(graph, conn)

    def _introspect_mssql_pymssql(self, graph: GraphData, pymssql) -> None:
        conn = pymssql.connect(
            server=self.creds.host,
            port=str(self.creds.port or 1433),
            database=self.creds.database,
            user=self.creds.username,
            password=self.creds.password,
        )
        self._introspect_mssql_connection(graph, conn)

    def _introspect_mssql_connection(self, graph: GraphData, conn) -> None:
        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = 'dbo'
                ORDER BY TABLE_NAME
            """)
            tables = [row[0] for row in cur.fetchall()]

            table_nodes: dict[str, str] = {}
            for table_name in tables:
                cur.execute("""
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = ? AND TABLE_SCHEMA = 'dbo'
                    ORDER BY ORDINAL_POSITION
                """, (table_name,))
                columns = cur.fetchall()

                col_summary = ", ".join(
                    f"{c[0]} ({c[1]}{'?' if c[2] == 'YES' else ''})"
                    for c in columns[:10]
                )
                if len(columns) > 10:
                    col_summary += f" ... +{len(columns) - 10} more"

                node = Node(
                    name=f"Table: {table_name}",
                    type=NodeType.DB_TABLE,
                    layer=Layer.DATABASE,
                    summary=f"SQL Server table: {table_name} ({len(columns)} columns: {col_summary})",
                    tags=["live-db", "mssql"],
                    metadata={
                        "db_type": "mssql",
                        "table_name": table_name,
                        "columns": [
                            {"name": c[0], "type": c[1], "nullable": c[2] == "YES", "default": c[3]}
                            for c in columns
                        ],
                    },
                )
                graph.nodes.append(node)
                table_nodes[table_name] = node.id

            cur.execute("""
                SELECT
                    OBJECT_NAME(fk.parent_object_id) AS source_table,
                    COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS source_column,
                    OBJECT_NAME(fk.referenced_object_id) AS target_table,
                    COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS target_column,
                    fk.name AS constraint_name
                FROM sys.foreign_keys fk
                JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
            """)

            for row in cur.fetchall():
                src_table, src_col, tgt_table, tgt_col, constraint = row
                src_id = table_nodes.get(src_table)
                tgt_id = table_nodes.get(tgt_table)
                if src_id and tgt_id:
                    graph.edges.append(Edge(
                        source=src_id,
                        target=tgt_id,
                        label=f"{src_table}.{src_col} -> {tgt_table}.{tgt_col}",
                        type=EdgeType.FOREIGN_KEY,
                    ))

        finally:
            conn.close()

    def _introspect_sqlite(self, graph: GraphData) -> None:
        import sqlite3

        db_path = self.creds.database
        if not Path(db_path).is_absolute():
            db_path = str(Path(self.creds.extra.get("root", ".")) / db_path)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            tables = [row[0] for row in cur.fetchall()]

            table_nodes: dict[str, str] = {}
            for table_name in tables:
                cur.execute(f"PRAGMA table_info('{table_name}')")
                columns = cur.fetchall()

                col_summary = ", ".join(
                    f"{c[1]} ({c[2]}{'?' if not c[3] else ''})"
                    for c in columns[:10]
                )
                if len(columns) > 10:
                    col_summary += f" ... +{len(columns) - 10} more"

                node = Node(
                    name=f"Table: {table_name}",
                    type=NodeType.DB_TABLE,
                    layer=Layer.DATABASE,
                    summary=f"SQLite table: {table_name} ({len(columns)} columns: {col_summary})",
                    tags=["live-db", "sqlite"],
                    metadata={
                        "db_type": "sqlite",
                        "table_name": table_name,
                        "columns": [
                            {"name": c[1], "type": c[2], "nullable": not c[3],
                             "pk": bool(c[5]), "default": c[4]}
                            for c in columns
                        ],
                    },
                )
                graph.nodes.append(node)
                table_nodes[table_name] = node.id

            for table_name in tables:
                cur.execute(f"PRAGMA foreign_key_list('{table_name}')")
                for fk in cur.fetchall():
                    tgt_table = fk[2]
                    src_col = fk[3]
                    tgt_col = fk[4]
                    src_id = table_nodes.get(table_name)
                    tgt_id = table_nodes.get(tgt_table)
                    if src_id and tgt_id:
                        graph.edges.append(Edge(
                            source=src_id,
                            target=tgt_id,
                            label=f"{table_name}.{src_col} -> {tgt_table}.{tgt_col}",
                            type=EdgeType.FOREIGN_KEY,
                        ))

        finally:
            conn.close()


class SQLCodeAnalyzer:
    """Discovers database tables, columns, foreign keys, and query usage
    by parsing SQL statements and ORM definitions found in source code.

    This works without a live database connection, providing table/relationship
    information from CREATE TABLE statements, migration files, and inline SQL,
    plus data-flow edges linking backend files to the tables they reference.
    """

    _SKIP_DIRS = frozenset({
        "node_modules", ".git", ".github", "__pycache__", ".next",
        ".nuxt", "dist", "build", ".venv", "venv", "env",
    })
    _CODE_EXTENSIONS = frozenset({
        ".py", ".ts", ".js", ".cs", ".java", ".go", ".rb", ".php",
        ".sql", ".yaml", ".yml",
    })

    _CREATE_TABLE = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"[`\"\[]?(\w+)[`\"\]]?\s*\((.*?)\)\s*(?:ENGINE|;|\))",
        re.IGNORECASE | re.DOTALL,
    )
    _COLUMN_DEF = re.compile(
        r"^\s*[`\"\[]?(\w+)[`\"\]]?\s+"
        r"((?:TINY|SMALL|MEDIUM|BIG)?INT(?:EGER)?|VARCHAR|CHAR|TEXT|BOOLEAN|BOOL"
        r"|DATE(?:TIME)?|TIMESTAMP|FLOAT|DOUBLE|DECIMAL|NUMERIC|REAL"
        r"|BLOB|BINARY|VARBINARY|JSON|JSONB|UUID|SERIAL|BIGSERIAL|SMALLSERIAL"
        r"|MONEY|SMALLMONEY|NVARCHAR|NCHAR|NTEXT|XML|BIT|IMAGE|UNIQUEIDENTIFIER"
        r"|ENUM|SET|CLOB|LONG|NUMBER|TINYTEXT|MEDIUMTEXT|LONGTEXT)"
        r"(?:\s*\([^)]*\))?",
        re.IGNORECASE | re.MULTILINE,
    )
    _FK_INLINE = re.compile(
        r"FOREIGN\s+KEY\s*\(\s*[`\"\[]?(\w+)[`\"\]]?\s*\)"
        r"\s*REFERENCES\s+[`\"\[]?(\w+)[`\"\]]?\s*\(\s*[`\"\[]?(\w+)[`\"\]]?\s*\)",
        re.IGNORECASE,
    )
    _REFERENCES_INLINE = re.compile(
        r"[`\"\[]?(\w+)[`\"\]]?\s+\w+.*?REFERENCES\s+[`\"\[]?(\w+)[`\"\]]?"
        r"\s*\(\s*[`\"\[]?(\w+)[`\"\]]?\s*\)",
        re.IGNORECASE,
    )
    _NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
    _PRIMARY_KEY = re.compile(r"\bPRIMARY\s+KEY\b", re.IGNORECASE)

    _TABLE_REF = re.compile(
        r"\b(?:FROM|INTO|UPDATE|JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|INNER\s+JOIN"
        r"|OUTER\s+JOIN|CROSS\s+JOIN|FULL\s+JOIN)\s+"
        r"[`\"\[]?(\w+)[`\"\]]?",
        re.IGNORECASE,
    )

    _ALTER_FK = re.compile(
        r"ALTER\s+TABLE\s+[`\"\[]?(\w+)[`\"\]]?\s+ADD\s+(?:CONSTRAINT\s+\w+\s+)?"
        r"FOREIGN\s+KEY\s*\(\s*[`\"\[]?(\w+)[`\"\]]?\s*\)"
        r"\s*REFERENCES\s+[`\"\[]?(\w+)[`\"\]]?\s*\(\s*[`\"\[]?(\w+)[`\"\]]?\s*\)",
        re.IGNORECASE,
    )

    _SQL_NOISE = frozenset({
        "select", "from", "where", "set", "values", "into", "table",
        "index", "view", "database", "schema", "constraint", "primary",
        "foreign", "key", "references", "null", "not", "default",
        "true", "false", "exists", "cascade", "restrict", "action",
        "dual", "information_schema", "pg_catalog", "sys",
    })

    def __init__(self, root: Path):
        self.root = root.resolve()

    def analyze(self) -> GraphData:
        """Scan source files and return discovered tables, FKs, and usage edges."""
        graph = GraphData()

        console.print("[cyan]Analyzing SQL in source code...[/cyan]")

        table_defs: dict[str, dict] = {}
        fk_list: list[tuple[str, str, str, str, str]] = []
        table_usage: dict[str, set[str]] = {}

        for f in self._iter_source_files():
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            rel_path = str(f.relative_to(self.root)).replace("\\", "/")

            for m in self._CREATE_TABLE.finditer(content):
                table_name = m.group(1)
                body = m.group(2)
                cols, fks = self._parse_create_body(body)
                if table_name.lower() not in self._SQL_NOISE:
                    table_defs.setdefault(table_name, {"columns": [], "source": rel_path})
                    if cols:
                        table_defs[table_name]["columns"] = cols
                        table_defs[table_name]["source"] = rel_path
                    for src_col, ref_table, ref_col in fks:
                        fk_list.append((table_name, src_col, ref_table, ref_col, rel_path))

            for m in self._ALTER_FK.finditer(content):
                src_table, src_col, ref_table, ref_col = m.group(1), m.group(2), m.group(3), m.group(4)
                fk_list.append((src_table, src_col, ref_table, ref_col, rel_path))

            referenced_tables = set()
            for m in self._TABLE_REF.finditer(content):
                tname = m.group(1)
                if tname.lower() not in self._SQL_NOISE and not tname.startswith("__"):
                    referenced_tables.add(tname)

            if referenced_tables:
                table_usage[rel_path] = referenced_tables

        if not table_defs and not table_usage:
            console.print("  [dim]No SQL table definitions or queries found in code.[/dim]")
            return graph

        table_nodes: dict[str, str] = {}
        for table_name, info in table_defs.items():
            columns = info.get("columns", [])
            col_summary = ", ".join(
                f"{c['name']} ({c['type']}{'?' if c.get('nullable') else ''})"
                for c in columns[:10]
            )
            if len(columns) > 10:
                col_summary += f" ... +{len(columns) - 10} more"

            node = Node(
                name=f"Table: {table_name}",
                type=NodeType.DB_TABLE,
                layer=Layer.DATABASE,
                summary=f"SQL table: {table_name} ({len(columns)} columns: {col_summary})" if columns
                        else f"SQL table: {table_name} (from code)",
                tags=["code-sql"],
                metadata={
                    "table_name": table_name,
                    "source_file": info.get("source", ""),
                    "columns": columns,
                },
            )
            graph.nodes.append(node)
            table_nodes[table_name.lower()] = node.id

        for src_table, src_col, ref_table, ref_col, _ in fk_list:
            src_id = table_nodes.get(src_table.lower())
            tgt_id = table_nodes.get(ref_table.lower())
            if not tgt_id and ref_table.lower() not in table_nodes:
                fk_node = Node(
                    name=f"Table: {ref_table}",
                    type=NodeType.DB_TABLE,
                    layer=Layer.DATABASE,
                    summary=f"SQL table: {ref_table} (referenced via FK)",
                    tags=["code-sql"],
                    metadata={"table_name": ref_table, "columns": []},
                )
                graph.nodes.append(fk_node)
                table_nodes[ref_table.lower()] = fk_node.id
                tgt_id = fk_node.id

            if src_id and tgt_id:
                graph.edges.append(Edge(
                    source=src_id,
                    target=tgt_id,
                    label=f"{src_table}.{src_col} -> {ref_table}.{ref_col}",
                    type=EdgeType.FOREIGN_KEY,
                ))

        for rel_path, tables in table_usage.items():
            for tname in tables:
                tgt_id = table_nodes.get(tname.lower())
                if not tgt_id:
                    ref_node = Node(
                        name=f"Table: {tname}",
                        type=NodeType.DB_TABLE,
                        layer=Layer.DATABASE,
                        summary=f"SQL table: {tname} (referenced in queries)",
                        tags=["code-sql"],
                        metadata={"table_name": tname, "columns": []},
                    )
                    graph.nodes.append(ref_node)
                    table_nodes[tname.lower()] = ref_node.id

        graph.metadata = {"table_usage": {
            path: list(tables) for path, tables in table_usage.items()
        }}

        tables_found = len([n for n in graph.nodes if n.type == NodeType.DB_TABLE])
        fk_count = len([e for e in graph.edges if e.type == EdgeType.FOREIGN_KEY])
        usage_files = len(table_usage)
        console.print(
            f"  [green]Found {tables_found} tables, {fk_count} foreign keys "
            f"from code ({usage_files} files reference tables)[/green]"
        )

        return graph

    def _iter_source_files(self):
        for ext in self._CODE_EXTENSIONS:
            for f in self.root.rglob(f"*{ext}"):
                if any(skip in f.parts for skip in self._SKIP_DIRS):
                    continue
                if f.is_file():
                    yield f

    def _parse_create_body(self, body: str) -> tuple[list[dict], list[tuple[str, str, str]]]:
        """Parse the column definitions and FK constraints from a CREATE TABLE body."""
        columns: list[dict] = []
        fks: list[tuple[str, str, str]] = []

        for line in body.split(","):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            fk = self._FK_INLINE.search(line_stripped)
            if fk:
                fks.append((fk.group(1), fk.group(2), fk.group(3)))
                continue

            ref = self._REFERENCES_INLINE.search(line_stripped)
            if ref:
                col_name = ref.group(1)
                ref_table = ref.group(2)
                ref_col = ref.group(3)
                fks.append((col_name, ref_table, ref_col))

            col = self._COLUMN_DEF.match(line_stripped)
            if col:
                name = col.group(1)
                dtype = col.group(2)
                if name.upper() in ("PRIMARY", "UNIQUE", "CONSTRAINT", "CHECK",
                                     "INDEX", "KEY", "FOREIGN"):
                    continue
                nullable = not bool(self._NOT_NULL.search(line_stripped))
                is_pk = bool(self._PRIMARY_KEY.search(line_stripped))
                columns.append({
                    "name": name,
                    "type": dtype.upper(),
                    "nullable": nullable,
                    "pk": is_pk,
                })

        return columns, fks


class DatabaseScanner:
    """Orchestrates credential discovery, database introspection, and SQL code analysis."""

    def __init__(self, root: Path, manual_url: Optional[str] = None):
        self.root = root
        self.manual_url = manual_url

    def scan(self) -> GraphData:
        """Discover tables via live DB introspection and SQL-in-code analysis.

        Always scans source code for SQL statements to discover tables,
        columns, foreign keys, and which backend files query which tables.
        Live DB introspection runs when credentials are available; code
        analysis fills in when the connection fails or credentials are absent.
        """
        combined = GraphData()
        live_succeeded = False

        # ── Live DB introspection ─────────────────────────────────────
        if self.manual_url:
            if "://" in self.manual_url:
                creds = _parse_url_credentials(self.manual_url)
            else:
                creds = _parse_ado_connection_string(self.manual_url)
            creds.source = "manual --db-url"
            all_creds = [creds] if creds.is_complete else []
            if not all_creds:
                console.print("[red]Could not parse the provided --db-url[/red]")
        else:
            discovery = CredentialDiscovery(self.root)
            all_creds = discovery.discover()

        if all_creds:
            for creds in all_creds:
                introspector = DatabaseIntrospector(creds)
                db_graph = introspector.introspect()

                if db_graph.nodes:
                    live_succeeded = True

                existing_tables = {
                    n.metadata.get("table_name", "").lower()
                    for n in combined.nodes
                    if n.type == NodeType.DB_TABLE and n.metadata.get("table_name")
                }

                for node in db_graph.nodes:
                    table_name = node.metadata.get("table_name", "").lower()
                    if table_name not in existing_tables:
                        combined.nodes.append(node)
                        existing_tables.add(table_name)

                combined.edges.extend(db_graph.edges)

        # ── SQL-in-code analysis (always runs) ────────────────────────
        code_analyzer = SQLCodeAnalyzer(self.root)
        code_graph = code_analyzer.analyze()

        live_tables = {
            n.metadata.get("table_name", "").lower()
            for n in combined.nodes
            if n.type == NodeType.DB_TABLE and n.metadata.get("table_name")
        }

        code_id_remap: dict[str, str] = {}

        for node in code_graph.nodes:
            table_name = node.metadata.get("table_name", "").lower()
            if table_name and table_name not in live_tables:
                combined.nodes.append(node)
                live_tables.add(table_name)
                code_id_remap[node.id] = node.id
            elif table_name:
                for existing in combined.nodes:
                    if (existing.type == NodeType.DB_TABLE
                            and existing.metadata.get("table_name", "").lower() == table_name):
                        code_id_remap[node.id] = existing.id
                        break

        existing_fk_pairs = {
            (e.source, e.target) for e in combined.edges
            if e.type == EdgeType.FOREIGN_KEY
        }
        for edge in code_graph.edges:
            src = code_id_remap.get(edge.source, edge.source)
            tgt = code_id_remap.get(edge.target, edge.target)
            if (src, tgt) not in existing_fk_pairs:
                combined.edges.append(Edge(
                    source=src, target=tgt,
                    label=edge.label, type=edge.type,
                ))
                existing_fk_pairs.add((src, tgt))

        if hasattr(code_graph, "metadata") and code_graph.metadata:
            combined.metadata = {**getattr(combined, "metadata", {}), **code_graph.metadata}

        return combined
