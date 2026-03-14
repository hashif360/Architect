"""Allow running as: python -m architect  OR  as a PyInstaller-frozen exe."""

try:
    from .cli import app
except ImportError:
    from architect.cli import app

app()
