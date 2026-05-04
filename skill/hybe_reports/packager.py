"""Directory → zip with gitignore-style exclusion."""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

DEFAULT_EXCLUDES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".DS_Store",
    ".venv",
    "venv",
    "env",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".swp", ".swo")


def make_zip(src: Path) -> Path:
    """Create a temp zip of `src` (a directory). Returns the zip path.

    Caller is responsible for unlinking when done.
    """
    src = src.resolve()
    if not src.is_dir():
        raise NotADirectoryError(f"not a directory: {src}")

    fd = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    fd.close()
    out = Path(fd.name)

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in _iter_files(src):
            arcname = path.relative_to(src).as_posix()
            zf.write(path, arcname)
    return out


def _iter_files(root: Path):
    """Yield files under `root`, skipping any excluded directory or suffix."""
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        for entry in sorted(cur.iterdir()):
            if entry.name in DEFAULT_EXCLUDES:
                continue
            if entry.is_symlink():
                # avoid following symlinks (path traversal + cycles)
                continue
            if entry.is_dir():
                stack.append(entry)
                continue
            if entry.suffix in EXCLUDED_SUFFIXES:
                continue
            yield entry
