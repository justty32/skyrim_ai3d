"""拒寫的根：內容同 agentctl `tools/outfit/paths.py` 的 `denied_roots`（@ `0e29b583`），手動同步。

就算在准寫的根底下也拒：MO2 整棵（mods、overwrite、profiles；`$MO2_DIR`、`$MO2_MODS_DIR`）、遊戲 Data（`$SKYRIM_DATA`）、
layer_apply 備份根（`$LAYER_APPLY_ROOT`，預設 `~/skyrim_mods/_layer-apply`）、testlayer 租約根（`$TESTLAYER_ROOT`，
預設 `~/skyrim_mods/_testlayer`）。每次呼叫才讀環境變數，測試可把這些根指到 tempdir。
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or str(default)).expanduser()


def skyrim_mods() -> Path:
    return Path.home() / "skyrim_mods"


def mo2_dir() -> Path:
    return _env_path("MO2_DIR", Path.home() / "games/mod-organizer-2-skyrimspecialedition/modorganizer2")


def denied_roots() -> list[tuple[str, Path]]:
    mo2 = mo2_dir()
    return [
        ("MO2（mods／overwrite／profiles）", mo2),
        ("MO2 mods", _env_path("MO2_MODS_DIR", mo2 / "mods")),
        ("遊戲 Data", _env_path("SKYRIM_DATA", Path.home() / ".local/share/Steam/steamapps/common/"
                                                         "Skyrim Special Edition/Data")),
        ("layer_apply 備份根", _env_path("LAYER_APPLY_ROOT", skyrim_mods() / "_layer-apply")),
        ("testlayer 租約根", _env_path("TESTLAYER_ROOT", skyrim_mods() / "_testlayer")),
    ]
