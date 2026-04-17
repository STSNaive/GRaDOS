"""Shared path-resolution helpers for storage and research layers."""

from __future__ import annotations

from pathlib import Path
from typing import overload

from grados.config import GRaDOSPaths

__all__ = ["resolve_papers_dir"]


@overload
def resolve_papers_dir(chroma_dir: Path, paths: GRaDOSPaths | None = None) -> Path: ...


@overload
def resolve_papers_dir(chroma_dir: None, paths: GRaDOSPaths) -> Path: ...


@overload
def resolve_papers_dir(chroma_dir: None, paths: None = None) -> None: ...


def resolve_papers_dir(chroma_dir: Path | None, paths: GRaDOSPaths | None = None) -> Path | None:
    """Resolve the canonical `papers/` directory from a Chroma location.

    Prefer explicit `GRaDOSPaths` when available. Otherwise, fall back to the
    conventional sibling layout used by tests and local installs.
    """
    if paths is not None:
        return paths.papers
    if chroma_dir is None:
        return None
    if chroma_dir.name == "papers":
        return chroma_dir
    if chroma_dir.parent.name == "database":
        return chroma_dir.parent.parent / "papers"
    return chroma_dir.parent / "papers"
