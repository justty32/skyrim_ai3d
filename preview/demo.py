"""demo：合成洋裝＋身體（synth.py）走一圈「拆開 → 改圖 → 預覽」，出改前／改後的對照圖。

OUT 底下：`in/`（合成 NIF／DDS）、`before/`（原貼圖的預覽）、`unpack/`（texedit 拆開）、`ops/demo.ops.json`、
`edit/dress_d.png`（texedit 改好的貼圖）、`after/`（改後預覽，相機沿用 before 的 frame）、
`compare.png`（上排改前、下排改後）、`demo.json`（摘要）。
OUT 要嘛不存在／空夾，要嘛是 demo 寫過的夾（有 `demo.json`）。
"""
from __future__ import annotations

import json
from pathlib import Path

from preview import render, synth
from preview.paths import OutDir

DEMO_OPS = [
    # 上衣：保留明暗換成初音綠
    {"op": "recolor", "target": {"shape": "dress.Top"}, "color": "#39C5BB", "blend": "colorize"},
    # 裙子：上深下綠的漸層，乘在原圖上
    {"op": "gradient", "target": {"shape": "dress.Skirt"}, "stops": [[0, "#303030"], [1, "#39C5BB"]],
     "angle": 90, "blend": "multiply"},
]
VIEWS = "front,back,side"
SIZE = 256


def _compare(before: Path, after: Path):
    from PIL import Image, ImageDraw
    with Image.open(before) as a, Image.open(after) as b:
        a.load()
        b.load()
        im = Image.new("RGB", (max(a.width, b.width), a.height + b.height))
        im.paste(a, (0, 0))
        im.paste(b, (0, a.height))
    d = ImageDraw.Draw(im)
    d.text((im.width - 60, 4), "before", fill=(255, 220, 120))
    d.text((im.width - 60, a.height + 4), "after", fill=(255, 220, 120))
    return im


def run(out) -> dict:
    from texedit import edit, unpack                      # 改圖那一步用 texedit（同 repo）

    out = Path(out).expanduser()
    if out.exists() and out.is_dir() and any(out.iterdir()) and not (out / "demo.json").is_file():
        raise render.PreviewError(f"--out {out} 不是空夾，也不是 demo 寫過的夾（沒有 demo.json），拒絕")
    od = OutDir(out)
    inp = {k: str(out / v) for k, v in synth.write_inputs(od.write_bytes, "in/").items()}
    nifs = [inp["body.nif"], inp["dress.nif"]]
    others = [inp["lace_d.dds"], inp["sash_d.dds"], inp["body_d.dds"]]
    before = render.run(render.Options(nifs=nifs, out=str(out / "before"), tex=[inp["dress_d.dds"]] + others,
                                       views=VIEWS, size=SIZE))
    unpack.unpack([inp["dress.nif"]], [inp["dress_d.dds"]], out / "unpack")
    doc = {"format": edit.FORMAT, "layout": "../unpack/layout.json", "texture": "dress_d", "ops": DEMO_OPS}
    od.write_text("ops/demo.ops.json", json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    rec = edit.edit(out / "ops" / "demo.ops.json", out / "edit" / "dress_d.png")
    # texedit 的輸出 PNG 直接當 --tex（檔名主幹 dress_d 對到 diffuse 槽），相機沿用改前的 frame
    after = render.run(render.Options(nifs=nifs, out=str(out / "after"), tex=[str(out / "edit" / "dress_d.png")] + others,
                                      views=VIEWS, size=SIZE, frame=tuple(before["camera"]["frame"])))
    od.write_png("compare.png", _compare(out / "before" / "sheet.png", out / "after" / "sheet.png"))
    summary = {"out": str(out), "compare": "compare.png", "before": "before/sheet.png", "after": "after/sheet.png",
               "edited_texture": "edit/dress_d.png", "edit_by_shape": rec["by_shape"],
               "frame": before["camera"]["frame"],
               "images": {"before": {i["view"]: i["sha256"] for i in before["images"]},
                          "after": {i["view"]: i["sha256"] for i in after["images"]}}}
    od.write_text("demo.json", json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    return summary
