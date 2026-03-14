"""
Build script for creating a standalone Architect executable.

Usage:
    python build.py              # Build for current platform
    python build.py --no-upx    # Build without UPX compression
    python build.py --clean     # Clean build cache first (default: True)

Outputs to dist/architect[.exe]
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def check_pyinstaller() -> None:
    """Install PyInstaller if it isn't already available."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"],
            check=True,
        )
        print("PyInstaller installed.\n")


def check_upx() -> bool:
    """Return True if UPX is available on PATH (optional compressor)."""
    return shutil.which("upx") is not None


def build(clean: bool = True, use_upx: bool = True) -> Path:
    """Run PyInstaller and return the path to the produced binary."""
    check_pyinstaller()

    cmd = [sys.executable, "-m", "PyInstaller", "architect.spec"]

    if clean:
        cmd.append("--clean")

    if use_upx and not check_upx():
        print("UPX not found on PATH -- skipping compression. Install UPX for smaller binaries.")

    print(f"Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=Path(__file__).parent)

    if result.returncode != 0:
        print("\nBuild FAILED. Check the output above for errors.")
        sys.exit(result.returncode)

    system = platform.system()
    binary_name = "architect.exe" if system == "Windows" else "architect"
    binary_path = Path("dist") / binary_name

    if not binary_path.exists():
        print(f"\nBuild succeeded but binary not found at expected path: {binary_path}")
        sys.exit(1)

    size_mb = binary_path.stat().st_size / (1024 * 1024)
    print(f"\nBuild successful!")
    print(f"  Platform : {system} ({platform.machine()})")
    print(f"  Output   : {binary_path.resolve()}")
    print(f"  Size     : {size_mb:.1f} MB")
    print()
    print("Test it:")
    if system == "Windows":
        print(f"  .\\dist\\architect --help")
    else:
        print(f"  chmod +x dist/architect && ./dist/architect --help")

    return binary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Architect standalone executable")
    parser.add_argument("--no-clean", action="store_true", help="Skip cleaning build cache")
    parser.add_argument("--no-upx", action="store_true", help="Disable UPX compression")
    args = parser.parse_args()

    build(clean=not args.no_clean, use_upx=not args.no_upx)


if __name__ == "__main__":
    main()
