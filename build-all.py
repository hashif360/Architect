"""
Build the complete Architect IDE package.

Steps:
  1. Build the Python backend sidecar with PyInstaller
  2. Copy the sidecar into electron/resources/
  3. Install Electron dependencies
  4. Build the Electron app with electron-builder

Usage:
    python build-all.py                  # Build for current platform
    python build-all.py --platform win   # Build for Windows
    python build-all.py --platform mac   # Build for macOS
    python build-all.py --platform linux # Build for Linux
    python build-all.py --skip-sidecar   # Skip Python sidecar build
    python build-all.py --skip-electron  # Skip Electron build
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
ELECTRON_DIR = ROOT / "electron-app"
DIST_DIR = ROOT / "dist"
SIDECAR_DIR = ROOT / "electron-app" / "resources"


def step(msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}\n")


def build_sidecar() -> Path:
    """Build the Python backend as a standalone executable."""
    step("Building Python sidecar with PyInstaller")

    subprocess.run(
        [sys.executable, "build.py"],
        cwd=ROOT,
        check=True,
    )

    ext = ".exe" if platform.system() == "Windows" else ""
    src = DIST_DIR / f"architect{ext}"
    if not src.exists():
        print(f"Sidecar build failed — {src} not found")
        sys.exit(1)

    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    dst = SIDECAR_DIR / f"architect-sidecar{ext}"
    shutil.copy2(src, dst)
    size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"Sidecar: {dst} ({size_mb:.1f} MB)")
    return dst


def install_electron_deps() -> None:
    """Install npm dependencies for the Electron app."""
    step("Installing Electron dependencies")

    npm = "npm.cmd" if platform.system() == "Windows" else "npm"
    subprocess.run(
        [npm, "install"],
        cwd=ELECTRON_DIR,
        check=True,
    )


def build_electron(plat: str | None = None) -> None:
    """Build the Electron app for distribution."""
    step("Building Electron app")

    npm = "npm.cmd" if platform.system() == "Windows" else "npm"

    if plat:
        script = f"dist:{plat}"
    else:
        script = "dist"

    subprocess.run(
        [npm, "run", script],
        cwd=ELECTRON_DIR,
        check=True,
    )

    out_dir = ROOT / "dist-installers"
    if out_dir.exists():
        print(f"\nInstallers written to: {out_dir}")
        for f in sorted(out_dir.iterdir()):
            if f.is_file() and f.suffix in ('.exe', '.dmg', '.AppImage', '.deb', '.zip'):
                size_mb = f.stat().st_size / (1024 * 1024)
                print(f"  {f.name}  ({size_mb:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Architect IDE")
    parser.add_argument("--platform", choices=["win", "mac", "linux"], help="Target platform")
    parser.add_argument("--skip-sidecar", action="store_true", help="Skip Python sidecar build")
    parser.add_argument("--skip-electron", action="store_true", help="Skip Electron build")
    args = parser.parse_args()

    if not args.skip_sidecar:
        build_sidecar()

    if not args.skip_electron:
        install_electron_deps()
        build_electron(args.platform)

    step("Build complete!")


if __name__ == "__main__":
    main()
