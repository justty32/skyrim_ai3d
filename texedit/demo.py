"""demo：合成「洋裝」（synth.py）走一遍 unpack → edit → diff → pack，給人看圖、也給家裡對照流程。

OUT 底下：`in/`（合成 NIF／DDS）、`unpack/`（layout＋疊圖＋遮罩）、`ops/demo.ops.json`（＋小圖）、
`edit/dress_d.png`（＋record、diff.png）、`staging/textures/texedit/dress_d.dds`。
"""
from __future__ import annotations

import json
from pathlib import Path

from texedit import edit, pack, paths, synth, unpack

DEMO_OPS = [
    # 上衣正面：保留明暗換成初音綠
    {"op": "recolor", "target": {"shape": "Top", "island": 0}, "color": "#39C5BB", "blend": "colorize"},
    # 上衣背面：上→下漸層，扣掉被髮帶共用的 texel
    {"op": "gradient", "target": {"shape": "Top", "island": 1, "exclude": {"shape": "Ribbon"}},
     "stops": [[0, "#202020"], [1, "#39C5BB"]], "angle": 90, "blend": "multiply"},
    # 裙子：平鋪格紋（保留原圖明暗）
    {"op": "pattern", "target": {"shape": "Skirt"}, "image": "tile.png", "scale": 2, "resample": "nearest",
     "blend": "overlay"},
    # 上衣正面中央貼一個標誌，轉 15 度
    {"op": "decal", "target": {"shape": "Top", "island": 0}, "image": "logo.png", "center_uv": [0.25, 0.25],
     "scale": 1.5, "rotate": 15},
]


def _tile():
    from PIL import Image
    t = Image.new("RGBA", (8, 8), (40, 40, 40, 255))
    for y in range(8):
        for x in range(8):
            if (x // 4 + y // 4) % 2:
                t.putpixel((x, y), (230, 40, 90, 255))
    return t


def _logo():
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([1, 1, 14, 14], fill=(250, 250, 250, 255), outline=(20, 20, 20, 255))
    d.text((5, 2), "3", fill=(200, 0, 60, 255))
    return im


def run(out) -> dict:
    out = Path(out)
    inp = out / "in"
    paths.write_bytes(inp / "dress.nif", synth.dress_nif())
    paths.write_bytes(inp / "dress_d.dds", synth.dds_bytes(synth.diffuse_image()))
    paths.write_bytes(inp / "dress_n.dds", synth.dds_bytes(synth.normal_image()))
    layout = unpack.unpack([inp / "dress.nif"], [inp / "dress_d.dds", inp / "dress_n.dds"], out / "unpack")
    ops_dir = out / "ops"
    paths.write_png(ops_dir / "tile.png", _tile())
    paths.write_png(ops_dir / "logo.png", _logo())
    doc = {"format": edit.FORMAT, "layout": "../unpack/layout.json", "texture": "dress_d", "ops": DEMO_OPS}
    paths.write_text(ops_dir / "demo.ops.json", json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    rec = edit.edit(ops_dir / "demo.ops.json", out / "edit" / "dress_d.png")
    d = edit.diff(out / "unpack" / "textures" / "dress_d.png", out / "edit" / "dress_d.png",
                  out / "unpack" / "layout.json", "dress_d", out / "edit" / "diff.png")
    rep = pack.pack(out / "edit" / "dress_d.png", inp / "dress_d.dds", out / "staging" / "textures" / "texedit" /
                    "dress_d.dds")
    return {"out": str(out), "overlay_all": str(out / "unpack" / layout["textures"]["dress_d"]["overlay_all"]),
            "edited": str(out / "edit" / "dress_d.png"), "diff": d.get("image"),
            "steps": rec["steps"], "by_shape": rec["by_shape"],
            "pack": {k: rep.get(k) for k in ("ok", "out", "alpha_changed_pixels", "rgb_changed_pixels", "sha256")}}
