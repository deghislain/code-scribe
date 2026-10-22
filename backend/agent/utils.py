"""Shared file-system utilities used across agent modules."""

from __future__ import annotations

from pathlib import Path


def read_safe(path: Path, max_chars: int = 4096) -> str:
    """Read *path* and return up to *max_chars* characters; return '' on error."""
    try:
        return path.read_text(errors="replace")[:max_chars]
    except OSError:
        return ""


def tree_listing(root: Path, max_files: int = 200) -> str:
    """Return a compact directory tree string, skipping hidden/cache dirs."""
    lines: list[str] = []
    for i, p in enumerate(sorted(root.rglob("*"))):
        if i >= max_files:
            lines.append("... (truncated)")
            break
        parts = p.relative_to(root).parts
        if any(
            part.startswith(".") or part in {"__pycache__", "node_modules", ".git"}
            for part in parts
        ):
            continue
        lines.append(str(p.relative_to(root)))
    return "\n".join(lines)
