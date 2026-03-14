"""
Generate platform-specific icons from the SVG source.

Requirements: pip install Pillow cairosvg

Usage: python installer/generate-icons.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

def main() -> None:
    try:
        import cairosvg
        from PIL import Image
    except ImportError:
        print("Install dependencies: pip install Pillow cairosvg")
        sys.exit(1)

    assets_dir = Path(__file__).parent / "assets"
    svg_path = assets_dir / "icon.svg"

    if not svg_path.exists():
        print(f"SVG not found at {svg_path}")
        sys.exit(1)

    svg_data = svg_path.read_bytes()

    sizes = [16, 32, 48, 64, 128, 256, 512]
    images = []

    for size in sizes:
        png_data = cairosvg.svg2png(bytestring=svg_data, output_width=size, output_height=size)
        img = Image.open(io.BytesIO(png_data))
        images.append(img)

        png_path = assets_dir / f"{size}x{size}.png"
        img.save(png_path, "PNG")
        print(f"  Created {png_path.name}")

    # Windows .ico (multi-size)
    ico_path = assets_dir / "icon.ico"
    images[0].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes[:6]])
    print(f"  Created {ico_path.name}")

    # macOS .icns - create a 512x512 PNG (iconutil on macOS converts the iconset)
    png_512 = assets_dir / "icon.png"
    images[-1].save(png_512, "PNG")
    print(f"  Created {png_512.name}")

    print("\nFor macOS .icns, run on a Mac:")
    print("  mkdir -p icon.iconset")
    print("  cp 512x512.png icon.iconset/icon_512x512.png")
    print("  cp 256x256.png icon.iconset/icon_256x256.png")
    print("  cp 128x128.png icon.iconset/icon_128x128.png")
    print("  iconutil -c icns icon.iconset -o icon.icns")
    print("\nDone!")


if __name__ == "__main__":
    main()
