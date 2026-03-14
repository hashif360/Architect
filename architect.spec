# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for building a standalone Architect executable.
#
# Usage:
#   python build.py
# Or directly:
#   pyinstaller --clean architect.spec

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs

block_cipher = None

# watchfiles has a Rust native extension that PyInstaller may miss
watchfiles_binaries = collect_dynamic_libs("watchfiles")

# All modules that PyInstaller misses because they are loaded dynamically
HIDDEN_IMPORTS = [
    # uvicorn internals loaded via importlib at runtime
    "uvicorn",
    "uvicorn.main",
    "uvicorn.config",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    "uvicorn.middleware.wsgi",
    # anyio backends
    "anyio",
    "anyio._backends._asyncio",
    "anyio._backends._trio",
    # fastapi / starlette
    "fastapi",
    "fastapi.routing",
    "fastapi.middleware",
    "starlette",
    "starlette.routing",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.staticfiles",
    "starlette.responses",
    "starlette.requests",
    "starlette.applications",
    # pydantic v2
    "pydantic",
    "pydantic.deprecated",
    "pydantic.deprecated.class_validators",
    "pydantic_core",
    # markdown
    "markdown",
    "markdown.extensions",
    "markdown.extensions.extra",
    "markdown.extensions.fenced_code",
    # typer / click / rich
    "typer",
    "click",
    "rich",
    "rich.console",
    "rich.table",
    "rich.panel",
    "rich.markdown",
    # h11 (http protocol)
    "h11",
    "h11._connection",
    "h11._events",
    # watchfiles (live file tracking)
    "watchfiles",
    "watchfiles.main",
    # email (used internally by some deps)
    "email.mime.text",
    "email.mime.multipart",
]

a = Analysis(
    ["src/architect/__main__.py"],
    pathex=[str(Path(".").resolve())],
    binaries=watchfiles_binaries,
    # Bundle the web viewer static files into architect_static/ inside the exe
    datas=[
        ("src/architect/viewer/static", "architect_static"),
    ],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy packages we definitely don't use
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "cv2",
        "torch",
        "tensorflow",
        "pytest",
        "IPython",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="architect",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,          # compress with UPX if available (reduces size ~30%)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,      # CLI tool -- keep console window
    disable_windowed_traceback=False,
    target_arch=None,  # native arch of the build machine
    codesign_identity=None,
    entitlements_file=None,
    # Windows: embed a version resource
    version=None,
    icon=None,
)
