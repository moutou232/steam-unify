"""Runtime paths that work both from source and from a PyInstaller exe."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_DATA_DIR: Path | None = None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory containing the exe (frozen) or the project root (source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Read-only bundled resources (PyInstaller extraction dir in onefile)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def resource_data_dir() -> Path:
    return resource_dir() / "data"


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Writable data directory next to the exe, with a LocalAppData fallback."""
    global _DATA_DIR
    if _DATA_DIR is not None:
        return _DATA_DIR
    primary = app_dir() / "data"
    if _writable_dir(primary):
        _DATA_DIR = primary
        return primary
    fallback = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Steam统译"
    fallback.mkdir(parents=True, exist_ok=True)
    _DATA_DIR = fallback
    return fallback


def ensure_data() -> Path:
    """Bootstrap bundled default data into the writable data directory."""
    target = data_dir()
    source = resource_data_dir()
    if source != target and source.is_dir():
        for src in source.iterdir():
            if not src.is_file():
                continue
            dst = target / src.name
            if dst.exists():
                continue
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass
    return target
