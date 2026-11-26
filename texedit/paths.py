"""texedit 的寫檔白名單：拒的根見 `ai3dlib/paths.py`（MO2 整棵含 mods／overwrite／profiles、遊戲 Data、`_layer-apply`、
`_testlayer`，env 可指到別處），准的根是 `~/skyrim_mods/`、系統暫存夾、/tmp，另加
`$TEXEDIT_WRITE_ROOTS`（os.pathsep 分隔）。逐檔拒 symlink、硬連結；寫法是同夾暫存檔＋`os.replace`
（實作在 `ai3dlib/writeguard.py`）。每次呼叫才讀 env，測試可把根指到 tempdir。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ai3dlib.writeguard as _wg
from ai3dlib.paths import denied_roots, skyrim_mods  # 拒的根同 agentctl outfit

WRITE_ROOTS_ENV = "TEXEDIT_WRITE_ROOTS"


class WriteGuardError(ValueError):
    """寫入目的地不在白名單，或落在 MO2／遊戲 Data／layer_apply 備份根／testlayer 租約根底下，或是 symlink／硬連結。"""


def allowed_write_roots() -> list[Path]:
    roots = [skyrim_mods(), Path(tempfile.gettempdir()), Path("/tmp")]
    roots += [Path(x).expanduser() for x in os.environ.get(WRITE_ROOTS_ENV, "").split(os.pathsep) if x]
    return list(dict.fromkeys(Path(os.path.realpath(r)) for r in roots))


GUARD = _wg.Guard(allowed=lambda: allowed_write_roots(), denied=lambda: denied_roots(),
                  error=WriteGuardError, env=WRITE_ROOTS_ENV)


def guard_write(path, what: str = "寫入") -> Path:
    return GUARD.guard_write(path, what)


def make_dirs(path, what: str = "建夾") -> Path:
    return GUARD.make_dirs(path, what)


def write_bytes(path, data: bytes, what: str = "寫入") -> Path:
    return GUARD.write_bytes(path, data, what)


def write_text(path, text: str, what: str = "寫入") -> Path:
    return GUARD.write_text(path, text, what)


def write_png(path, im, what: str = "寫入") -> Path:
    """Pillow Image → PNG bytes（決定性：不寫時間戳）→ write_bytes。"""
    import io
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=False)
    return write_bytes(path, buf.getvalue(), what)
