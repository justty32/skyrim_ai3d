"""Data 相對路徑的資產查找（`--data DIR`，可多個，依序找，大小寫不敏感）。

取自 agentctl `tools/head_render/assets.py`（`norm`、`_ci_find`、`Assets.get` 的 `--data` 段）與
`tools/outfit/inputs.py`（`AssetInputs` 的 `get`／`try_get`／`seen`）@ `0e29b583`，手動同步。
只帶 `--data`：原本的 `--live`（查 MO2 現役 VFS／BSA）要私有工具，不在這裡。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def norm(rel: str) -> str:
    r = rel.replace("\\", "/").lstrip("/").lower()
    return r[5:] if r.startswith("data/") else r


def _ci_find(root: Path, rel: str) -> Path | None:
    cur = root
    for part in rel.split("/"):
        if not cur.is_dir():
            return None
        hit = None
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if e.name.lower() == part:
                        hit = Path(e.path)
                        break
        except OSError:
            return None
        if hit is None:
            return None
        cur = hit
    return cur if cur.is_file() else None


class InputError(LookupError):
    pass


class AssetInputs:
    """`--data` 夾依序找；讀到的每個檔記 sha256 與提供者（`seen`）。"""

    def __init__(self, data_dirs=()):
        self.data_dirs = [Path(d) for d in data_dirs]
        self.provenance: dict[str, str] = {}
        self.seen: dict[str, dict] = {}

    def _get(self, key: str) -> bytes | None:
        for d in self.data_dirs:
            p = _ci_find(d, key)
            if p is not None:
                self.provenance[key] = str(p)
                return p.read_bytes()
        return None

    def get(self, rel: str) -> bytes:
        data = self.try_get(rel)
        if data is None:
            raise InputError(f"資產找不到：{rel}（--data 夾都沒有）")
        return data

    def try_get(self, rel: str) -> bytes | None:
        key = norm(rel)
        data = self._get(key)
        if data is not None:
            self.seen[key] = {"sha256": hashlib.sha256(data).hexdigest(),
                              "provider": self.provenance.get(key, "?")}
        return data
