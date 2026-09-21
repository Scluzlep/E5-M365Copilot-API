"""Atomic small-file writes: temp file + rename + fsync.

A plain write_text truncates the target first, so a crash, kill, or full
disk mid-write leaves the file empty or corrupted. rename is atomic on both
POSIX and Windows (Path.replace), so readers see either the whole old value
or the whole new one.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path


def _fsync_dir(directory: Path) -> None:
    """Flush the directory entry so a crash cannot resurrect the old entry."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_text_atomic(
    path: Path | str, text: str, *, mode: int | None = None, durable: bool = True
) -> None:
    """Replace ``path`` with ``text`` atomically, creating parents as needed.

    ``mode`` is applied to the temp file *before* the rename, so a secret is
    never briefly readable under the default umask.

    ``durable`` (default True) adds fsync on both the file before rename and
    the parent directory after, preventing corrupted/truncated writes upon power loss.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        if mode is not None:
            try:
                tmp.chmod(mode)
            except OSError:
                pass
        tmp.replace(p)
        if durable:
            _fsync_dir(p.parent)
    finally:
        tmp.unlink(missing_ok=True)
