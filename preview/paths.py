"""preview 的寫檔白名單：兩層，都走 `ai3dlib/writeguard.py`。

1. `--out` 夾本身：拒的根見 `ai3dlib/paths.py`（MO2 整棵、遊戲 Data、`_layer-apply`、`_testlayer`），
   准的根是 `~/skyrim_mods/`、系統暫存夾、/tmp，另加 `$PREVIEW_WRITE_ROOTS`（os.pathsep 分隔）。
2. 每個輸出檔：只准寫在 `--out` 底下（`OutDir`），拒的根照判，逐檔拒 symlink、硬連結；同夾暫存檔＋`os.replace`。

每次呼叫才讀 env，測試可把根指到 tempdir。
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import ai3dlib.writeguard as _wg
from ai3dlib.paths import denied_roots, skyrim_mods

WRITE_ROOTS_ENV = "PREVIEW_WRITE_ROOTS"


class WriteGuardError(ValueError):
    """寫入目的地不在白名單或 `--out` 外，或落在 MO2／遊戲 Data 等拒寫的根底下，或是 symlink／硬連結。"""


def allowed_write_roots() -> list[Path]:
    roots = [skyrim_mods(), Path(tempfile.gettempdir()), Path("/tmp")]
    roots += [Path(x).expanduser() for x in os.environ.get(WRITE_ROOTS_ENV, "").split(os.pathsep) if x]
    return list(dict.fromkeys(Path(os.path.realpath(r)) for r in roots))


OUTER = _wg.Guard(allowed=lambda: allowed_write_roots(), denied=lambda: denied_roots(),
                  error=WriteGuardError, env=WRITE_ROOTS_ENV)


class OutDir:
    """只寫 `--out` 底下。建構時先用 OUTER 判 `--out` 本身並建夾。"""

    def __init__(self, out):
        self.path = OUTER.make_dirs(out, "輸出夾")
        real = Path(os.path.realpath(self.path))
        self.guard = _wg.Guard(allowed=lambda: [real], denied=lambda: denied_roots(), error=WriteGuardError,
                               env=WRITE_ROOTS_ENV,
                               allowed_msg="{what} {path} 不在 --out（{roots_csv}）底下，拒寫")

    def write_bytes(self, rel, data: bytes, what: str = "寫入") -> Path:
        return self.guard.write_bytes(self.path / rel, data, what)

    def write_text(self, rel, text: str, what: str = "寫入") -> Path:
        return self.write_bytes(rel, text.encode("utf-8"), what)

    def write_png(self, rel, im, what: str = "寫入") -> Path:
        """Pillow Image → PNG bytes（決定性：不寫時間戳）→ write_bytes。"""
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=False, compress_level=6)
        return self.write_bytes(rel, buf.getvalue(), what)
