"""Storage backends for the raw archive.

Only the local filesystem is implemented. The protocol exists so an
object-store backend (R2, B2) can be added later without touching callers:
everything above this layer speaks in string keys and bytes.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Protocol


class ArchiveBackend(Protocol):
    def write_bytes(self, key: str, data: bytes) -> None: ...
    def read_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str = "") -> List[str]: ...


class LocalBackend:
    """Keys map to paths under `root`. That mapping is this class's private
    business — no caller should ever see a Path."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _resolve(self, key: str) -> Path:
        path = (self._root / key).resolve()
        root = self._root.resolve()
        if not path.is_relative_to(root):
            raise ValueError("key escapes archive root: {}".format(key))
        return path

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read_bytes(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def list_keys(self, prefix: str = "") -> List[str]:
        root = self._root
        if not root.is_dir():
            return []
        keys = [
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.rglob("*.json.gz")
            if p.is_file()
        ]
        return sorted(k for k in keys if k.startswith(prefix))
