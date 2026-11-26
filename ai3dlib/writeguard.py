# 複製自 agentctl `tools/lib/writeguard.py` @ `0e29b583`，手動同步。
"""寫檔白名單（tools-lib-refactor B5）：一份程式，每家照舊傳自己的根與訊息（決定 Q3 (a)，行為不變）。

來源是 `outfit/paths.py` 的寫檔段（guard_write、check_target、make_dirs、write_bytes、replace_into、write_text），
那份本身就是 custom_race `cli.guard_write`＋racetest `resolve_paths` 合併來的。四家原本的語意差異原樣保留，
差異表見 `lib/test_diff_tables.py`（准／拒矩陣）：

| 家 | 判法 | 拒的根 | 准的根 | 逐檔 symlink／硬連結 | 建夾後再判 |
|---|---|---|---|---|---|
| outfit | realpath（含 expanduser） | MO2 整棵、mods、Data、layer_apply、testlayer | ~/skyrim_mods、暫存夾、/tmp、handoffs、env | 有 | 有 |
| custom_race | `Path.resolve`＋`is_relative_to`（不 expanduser） | 配對表 mods、旁邊的 profiles、data | 同 outfit（env 名不同） | 有（只有文字檔） | 無 |
| racetest | realpath | MO2 mods、MO2 夾、layer_apply、testlayer | ~/skyrim_mods、暫存夾、env | 無（決定 Q4，本輪不補） | — |
| npc_face | abspath 與 realpath 兩邊都比 | 寫死的家機 MO2 與 Data | 無白名單（只拒） | 無 | — |

`Guard` 的 `allowed`／`denied` 是「每次呼叫才算」的函式：呼叫端傳 `lambda: 模組函式()`，env 與測試對模組全域的
patch（例如 `custom_race.cli.allowed_write_roots`、`racetest.build.MO2_MODS`）照舊生效。
"""
from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class WriteGuardError(ValueError):
    """寫入目的地不在白名單，或落在拒寫的根底下，或本身是 symlink／硬連結。"""


# ---------------------------------------------------------------- 判「在不在根底下」的兩種寫法

def under_realpath(path, root) -> bool:
    """outfit／racetest 的 `_under`：兩邊都 realpath，相等或 root 是 path 的上層。"""
    p, r = Path(os.path.realpath(path)), Path(os.path.realpath(root))
    return p == r or r in p.parents


def norm_realpath_expanduser(path) -> Path:
    """outfit `guard_write` 的正規化。"""
    return Path(os.path.realpath(Path(path).expanduser()))


def norm_realpath(path) -> Path:
    """racetest：不先 expanduser（呼叫端已做），回原路徑給訊息用；判斷交給 under_realpath。"""
    return Path(path)


def norm_resolve(path) -> Path:
    """custom_race `guard_write` 的正規化。"""
    return Path(path).resolve()


def denied_is_relative_to_resolved(path: Path, root: Path) -> bool:
    """custom_race 拒寫段：`path.is_relative_to(root.resolve())`。"""
    return path.is_relative_to(root.resolve())


def allowed_is_relative_to(path: Path, root: Path) -> bool:
    """custom_race 白名單段：`path.is_relative_to(r)`（r 由 allowed_write_roots 先 resolve 過）。"""
    return path.is_relative_to(root)


def forbidden_root(dest, roots) -> Path | None:
    """npc_face `_forbidden`：dest 與每個根各取 abspath、realpath，任一組合相等或在底下就回該根。"""
    cands = {Path(os.path.abspath(dest)), Path(os.path.realpath(dest))}
    for root in roots:
        r = {Path(os.path.abspath(root)), Path(os.path.realpath(root))}
        for c in cands:
            for rr in r:
                if c == rr or rr in c.parents:
                    return root
    return None


# ---------------------------------------------------------------- 寫法：同夾暫存檔＋os.replace

def replace_into(path: Path, data: bytes) -> None:
    """同一個夾裡開新的暫存檔（O_EXCL｜O_NOFOLLOW、0o644 照 umask）寫完再 os.replace 到 path：
    path 若是既有檔，換的是目錄項，原 inode 不被寫；失敗時刪掉暫存檔。呼叫端先做 check_target。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(100):
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
        try:
            fd = os.open(tmp, flags, 0o644)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"{path.parent} 裡開不出暫存檔")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- Guard

DeniedEntry = tuple  # (name, root) 或 (name, root, 這一條自己的訊息樣板)


@dataclass
class Guard:
    """一家的白名單設定。預設值＝outfit 的寫法。

    訊息樣板可用的欄位：what、path（正規化後）、orig（呼叫端給的原路徑）、name、root、roots（list[str]）、
    roots_csv（逗號串）、env、target（symlink 指向）、nlink。
    """
    allowed: Callable[[], Iterable[Path]] | None
    denied: Callable[[], Iterable[DeniedEntry]]
    error: type = WriteGuardError
    env: str = ""
    normalize: Callable[[object], Path] = norm_realpath_expanduser
    under_denied: Callable[[Path, Path], bool] = under_realpath
    under_allowed: Callable[[Path, Path], bool] = under_realpath
    expanduser: bool = True                # check_target／make_dirs 先 expanduser
    recheck_dir_after_mkdir: bool = True   # write_bytes 建夾後再判一次（outfit make_dirs）
    denied_msg: str = "{what} {path} 在 {name}（{root}）底下，拒寫"
    allowed_msg: str = "{what} {path} 不在可寫白名單 {roots}；要另加請設 ${env}"
    symlink_msg: str = "{what} {path} 是 symlink（→ {target}），拒寫；換一個新的目的地"
    hardlink_msg: str = ("{what} {path} 是硬連結（st_nlink={nlink}，可能連到 MO2／Data 裡的檔），拒寫；"
                         "換一個新的目的地")

    def _fmt(self, tpl: str, **kw) -> str:
        kw.setdefault("env", self.env)
        return tpl.format(**kw)

    def guard_write(self, path, what: str = "寫入", denied: Iterable[DeniedEntry] | None = None) -> Path:
        """回正規化後的路徑；先判拒寫的根（照順序），再判白名單；不准就 raise self.error。"""
        orig = path
        path = self.normalize(path)
        if denied is None:
            denied = self.denied()
        elif callable(denied):   # 呼叫端可傳函式，延到真的判根時才算（custom_race：先判 symlink 再讀配對表）
            denied = denied()
        for entry in denied:
            name, root = entry[0], entry[1]
            if self.under_denied(path, root):
                tpl = entry[2] if len(entry) > 2 else self.denied_msg
                raise self.error(self._fmt(tpl, what=what, path=path, orig=orig, name=name, root=root))
        if self.allowed is not None:
            roots = list(self.allowed())
            if not any(self.under_allowed(path, r) for r in roots):
                raise self.error(self._fmt(self.allowed_msg, what=what, path=path, orig=orig,
                                           roots=[str(r) for r in roots],
                                           roots_csv=", ".join(str(r) for r in roots)))
        return path

    def check_target(self, path, what: str = "寫入", denied=None) -> Path:
        """寫出前的檢查：最後一段本身是 symlink、或已存在且是硬連結（st_nlink > 1）就拒，再判白名單
        （中間的 symlink 夾子照它指到哪裡判）。回原路徑（不解開）。"""
        path = Path(path).expanduser() if self.expanduser else Path(path)
        if path.is_symlink():
            raise self.error(self._fmt(self.symlink_msg, what=what, path=path, target=os.readlink(path)))
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            st = None
        if st is not None and st.st_nlink > 1:
            raise self.error(self._fmt(self.hardlink_msg, what=what, path=path, nlink=st.st_nlink))
        self.guard_write(path, what, denied)
        return path

    def make_dirs(self, path, what: str = "建夾", denied=None) -> Path:
        """建夾（含上層）：判白名單，建完；recheck_dir_after_mkdir 時建完再判一次。"""
        path = Path(path).expanduser() if self.expanduser else Path(path)
        self.guard_write(path, what, denied)
        path.mkdir(parents=True, exist_ok=True)
        if self.recheck_dir_after_mkdir:
            self.guard_write(path, what, denied)
        return path

    def write_bytes(self, path, data: bytes, what: str = "寫入", denied=None) -> Path:
        path = self.check_target(path, what, denied)
        self.make_dirs(path.parent, what, denied)
        self.check_target(path, what, denied)
        replace_into(path, data)
        return path

    def write_text(self, path, text: str, what: str = "寫入", denied=None) -> Path:
        return self.write_bytes(path, text.encode("utf-8"), what, denied)
