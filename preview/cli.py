"""preview CLI。全部離線；只寫 `--out` 底下（見 paths.py）。

  python3 -m preview --nif X.nif [--nif Y.nif …] [--tex KEY=FILE|FILE …] [--data DIR …] --out DIR
  python3 -m preview rerun DIR/preview.json [--out DIR2]
  python3 -m preview demo --out DIR

rc：0 完成、1 rerun 的圖對不上、2 拒絕（輸入錯、NIF／貼圖解不開、寫入白名單擋下）。
"""
from __future__ import annotations

import argparse
import json
import sys

from ai3dlib.nif import NifError
from preview import paths, raster, render

REFUSE = (render.PreviewError, paths.WriteGuardError, NifError, FileNotFoundError)


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def parse_rgb(s: str) -> tuple:
    s = s.strip().lstrip("#")
    try:
        if len(s) != 6:
            raise ValueError
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise argparse.ArgumentTypeError(f"顏色要 RRGGBB：{s!r}") from None


def parse_frame(s: str) -> tuple:
    try:
        vals = tuple(float(v) for v in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--frame 要 cx,cy,cz,r：{s!r}") from None
    if len(vals) != 4 or not vals[3] > 0:
        raise argparse.ArgumentTypeError(f"--frame 要 cx,cy,cz,r 且 r > 0：{s!r}")
    return vals


def summary(doc: dict, out) -> dict:
    return {"out": str(out), "images": [i["file"] for i in doc["images"]], "sheet": doc["sheet"]["file"],
            "frame": doc["camera"]["frame"],
            "shapes": {s["key"]: {"mode": s["mode"], "texture": s["texture"]["how"]} for s in doc["shapes"]},
            "missing_textures": doc["missing_textures"], "warnings": doc["warnings"]}


def render_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="preview", description="NIF＋diffuse 貼圖 → 正面／背面 PNG＋preview.json",
                                epilog="另有子命令：`preview rerun DIR/preview.json [--out DIR2]`、`preview demo --out DIR`")
    p.add_argument("--nif", action="append", required=True, help="NIF（可多次；衣服、身體疊在同一空間）")
    p.add_argument("--tex", action="append", default=[],
                   help="KEY=FILE（KEY＝shape key／shape 名／diffuse 槽檔名主幹或 Data 相對路徑，可 glob）或 FILE"
                        "（KEY＝檔名主幹）；PNG 或 DDS")
    p.add_argument("--data", action="append", default=[], help="Data 根（可多次，依序找）：NIF 與沒給 --tex 的 diffuse 槽")
    p.add_argument("--out", required=True, help="輸出夾（白名單內；新夾、空夾或 preview 寫過的夾）")
    p.add_argument("--views", default=render.DEFAULT_VIEWS,
                   help=f"逗號分隔：{','.join(raster.VIEWS)} 或角度數字（預設 {render.DEFAULT_VIEWS}）")
    p.add_argument("--size", type=int, default=512, help="每張圖邊長（預設 512）")
    p.add_argument("--frame", type=parse_frame, help="cx,cy,cz,r：固定取景（前後兩輪比對時用同一個）")
    p.add_argument("--tex-max", type=int, default=1024, help="貼圖最長邊上限（縮圖加速，預設 1024）")
    p.add_argument("--hide", action="append", default=[], help="不畫的 shape key／shape 名／NIF 檔名 glob")
    p.add_argument("--skinned-transform", choices=("ignore", "apply"), default="ignore",
                   help="蒙皮 shape 的局部 transform（同 head_render；預設 ignore）")
    p.add_argument("--bg", type=parse_rgb, default=raster.BG, help="背景色 RRGGBB")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == "demo":
            a = argparse.ArgumentParser(prog="preview demo")
            a.add_argument("--out", required=True)
            ns = a.parse_args(argv[1:])
            from preview import demo
            _print(demo.run(ns.out))
            return 0
        if argv and argv[0] == "rerun":
            a = argparse.ArgumentParser(prog="preview rerun", description="照 preview.json 重畫並比對像素 sha256")
            a.add_argument("json")
            a.add_argument("--out", help="輸出夾（省略＝寫回原夾）")
            ns = a.parse_args(argv[1:])
            rep = render.rerun(ns.json, ns.out)
            _print(rep)
            return 0 if rep["same_images"] else 1
        ns = render_parser().parse_args(argv)
        doc = render.run(render.Options(nifs=ns.nif, out=ns.out, tex=ns.tex, data=ns.data, views=ns.views,
                                        size=ns.size, frame=ns.frame, tex_max=ns.tex_max, hide=ns.hide,
                                        skinned_transform=ns.skinned_transform, bg=ns.bg))
        _print(summary(doc, ns.out))
        return 0
    except REFUSE as e:
        print(f"preview: 拒絕：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
