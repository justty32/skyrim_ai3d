"""pack：改好的 PNG → 未壓縮 RGBA8 DDS（帶完整 mip，`lib.dds.write_rgba`）。

檢查（對照原圖 REF，DDS 或 PNG）：
- 尺寸要相同，不同就不寫（rc 1）。
- alpha：列出 alpha 變動的像素數與包圍盒（Skyrim 衣服的 alpha 常是透明遮罩／高光，誤改會破洞）；
  `fail_on_alpha` 時有變動就不寫；`restore_alpha` 時先把 REF 的 alpha 抄回來再寫。
- 寫完讀回最上層 mip，跟要寫的像素逐 byte 比（不合就丟 PackError）。
BC7 等壓縮不在這裡做，回家用 texconv。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ai3dlib import dds as ddslib
from texedit import paths


class PackError(ValueError):
    pass


def load_rgba(path) -> "object":
    data = Path(path).read_bytes()
    try:
        return ddslib.read_image(data)
    except ddslib.DDSError as e:
        raise PackError(f"{path}: {e}") from None


def dds_info(data: bytes) -> dict:
    """DDS header 摘要（`lib.dds.read_dds`；不用 `describe`，它的 size 讀錯 offset，見 lib/test_diff_tables）；非 DDS 回 {}。"""
    try:
        return ddslib.read_dds(data).as_dict()
    except ddslib.DdsError:
        return {}


def changed(a, b) -> tuple[int, list | None]:
    """兩張同尺寸單通道圖：不同的像素數、包圍盒。"""
    from PIL import ImageChops
    diff = ImageChops.difference(a, b)
    n = diff.size[0] * diff.size[1] - diff.histogram()[0]
    bb = diff.getbbox()
    return n, list(bb) if bb else None


def rgb_changed(a, b) -> int:
    from PIL import ImageChops
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    r, g, bl = d.split()
    m = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    return m.size[0] * m.size[1] - m.histogram()[0]


def pack(png, ref, out, fail_on_alpha: bool = False, restore_alpha: bool = False) -> dict:
    """回報告 dict；ok=False 時沒寫檔。"""
    im = load_rgba(png)
    base = load_rgba(ref)
    rep = {"png": str(png), "ref": str(ref), "out": str(out), "size": list(im.size), "ref_size": list(base.size),
           "ref_dds": dds_info(Path(ref).read_bytes()), "ok": False}
    if im.size != base.size:
        rep["error"] = f"尺寸不同：PNG {im.size[0]}x{im.size[1]}，原圖 {base.size[0]}x{base.size[1]}"
        return rep
    n_alpha, bb_alpha = changed(im.getchannel("A"), base.getchannel("A"))
    rep.update({"alpha_changed_pixels": n_alpha, "alpha_changed_bbox": bb_alpha,
                "rgb_changed_pixels": rgb_changed(im, base)})
    if n_alpha and restore_alpha:
        im.putalpha(base.getchannel("A"))
        rep["alpha_restored"] = True
    elif n_alpha and fail_on_alpha:
        rep["error"] = f"alpha 被改了 {n_alpha} 個像素（--fail-on-alpha）"
        return rep
    data = ddslib.write_rgba(im, mips=True)
    back = ddslib.read_image(data)
    if back.tobytes() != im.tobytes():
        raise PackError("寫出的 DDS 讀回來跟 PNG 不一樣")
    paths.write_bytes(out, data)
    rep.update({"ok": True, "out_dds": dds_info(data), "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()})
    return rep
