"""Filesystem storage for reports — safe zip extraction."""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from fastapi import HTTPException, status

from app.config import get_settings

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}[a-z0-9]$")
DEFAULT_ENTRY_CANDIDATES = ["index.html", "index.htm"]


class StorageError(HTTPException):
    pass


def validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise StorageError(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "invalid slug — lowercase letters, digits, and dashes only "
                "(3-64 chars, must start/end with alnum)"
            ),
        )


def report_dir_for(slug: str) -> Path:
    settings = get_settings()
    base = settings.reports_dir_path
    target = (base / slug).resolve()
    # path traversal guard
    if base not in target.parents and target != base:
        raise StorageError(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid slug path"
        )
    return target


def extract_zip(slug: str, zip_path: Path) -> dict:
    """Safely extract `zip_path` to `<reports_dir>/<slug>/`.

    Returns: {entry_point, file_count, size_bytes}.
    """
    settings = get_settings()
    max_files = settings.storage.max_files_per_report
    max_extracted = settings.storage.max_extracted_mb * 1024 * 1024

    target = report_dir_for(slug)
    if target.exists():
        # caller decides overwrite; remove existing tree
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    total_size = 0
    file_count = 0
    found_files: list[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        if len(members) > max_files:
            raise StorageError(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"too many files: {len(members)} > {max_files}",
            )

        for info in members:
            # skip directories
            if info.is_dir():
                continue

            # path traversal guard via realpath comparison
            member_path = (target / info.filename).resolve()
            if target not in member_path.parents and member_path != target:
                raise StorageError(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"unsafe path in zip: {info.filename}",
                )

            # zip bomb guard via cumulative uncompressed size
            total_size += info.file_size
            if total_size > max_extracted:
                raise StorageError(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"extracted size exceeds {settings.storage.max_extracted_mb}MB",
                )

            member_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, member_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)

            file_count += 1
            rel = member_path.relative_to(target).as_posix()
            found_files.append(rel)

    return {
        "file_count": file_count,
        "size_bytes": total_size,
        "files": found_files,
    }


def detect_entry_point(slug: str, hint: str | None = None) -> str:
    """Pick an entry HTML file. Hint wins; else 'index.html' if present; else first .html."""
    target = report_dir_for(slug)
    if hint:
        candidate = (target / hint).resolve()
        if target in candidate.parents and candidate.is_file():
            return hint
        raise StorageError(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"entry_point not found: {hint}",
        )

    for c in DEFAULT_ENTRY_CANDIDATES:
        if (target / c).is_file():
            return c

    # fallback: first .html in root
    for f in sorted(target.iterdir()):
        if f.is_file() and f.suffix.lower() in {".html", ".htm"}:
            return f.name

    raise StorageError(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="no HTML entry point found in upload (expected index.html)",
    )


def remove_report(slug: str) -> None:
    target = report_dir_for(slug)
    if target.exists():
        shutil.rmtree(target)
