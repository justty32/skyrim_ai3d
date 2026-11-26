"""texedit CLI：unpack／pack／edit／replay／diff／demo。全部離線；只寫白名單內的輸出（見 paths.py）。

rc：0 完成、1 檢查沒過（pack 尺寸不符、`--fail-on-alpha` 時 alpha 被改、replay 對不上、edit `--strict` 時有 warnings）、2 拒絕（輸入錯、寫入白名單擋下）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "texedit"  # noqa: A001

from texedit import edit as editmod, pack as packmod, paths, unpack as unpackmod  # noqa: E402
from ai3dlib.nif import NifError  # noqa: E402

REFUSE = (unpackmod.UnpackError, packmod.PackError, editmod.EditError, paths.WriteGuardError, NifError,
          FileNotFoundError, json.JSONDecodeError)


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def cmd_unpack(a) -> int:
    assets = None
    if a.data:
        from ai3dlib.assets import AssetInputs
        assets = AssetInputs(a.data)
    if not a.tex and not a.auto_tex:
        raise unpackmod.UnpackError("要給 --tex，或加 --auto-tex 照 NIF 的貼圖槽去找")
    layout = unpackmod.unpack(a.nif, a.tex or [], a.out, unpackmod.parse_assign(a.assign), assets=assets,
                              auto_tex=a.auto_tex)
    _print({"out": str(Path(a.out)), "layout": str(Path(a.out) / "layout.json"),
            "textures": {k: {"size": v["size"], "users": [u["shape"] for u in v["users"]],
                             "overlay_all": v["overlay_all"]} for k, v in layout["textures"].items()},
            "shapes": {s["key"]: {"islands": len(s["islands"]), "textures": sorted(s["maps"])}
                       for s in layout["shapes"]},
            "overlaps": len(layout["overlaps"]), "missing_textures": layout["missing_textures"],
            "stale_files": layout["stale_files"]})
    return 0


def cmd_pack(a) -> int:
    rep = packmod.pack(a.png, a.ref, a.out, fail_on_alpha=a.fail_on_alpha, restore_alpha=a.restore_alpha)
    if a.report:
        paths.write_text(a.report, json.dumps(rep, ensure_ascii=False, indent=1) + "\n")
    _print(rep)
    return 0 if rep["ok"] else 1


def cmd_edit(a) -> int:
    rec = editmod.edit(a.ops, a.out)
    _print({k: rec[k] for k in ("output", "changed_pixels", "steps", "by_shape", "output_pixels_sha256", "warnings")}
           | {"record": a.out + ".record.json"})
    return 1 if a.strict and rec["warnings"] else 0


def cmd_replay(a) -> int:
    rep = editmod.replay(a.record)
    _print(rep)
    return 0 if rep["same_output"] else 1


def cmd_diff(a) -> int:
    _print(editmod.diff(a.a, a.b, a.layout, a.texture, a.out))
    return 0


def cmd_demo(a) -> int:
    from texedit import demo
    _print(demo.run(a.out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="texedit", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unpack", help="NIF＋DDS → PNG、UV 疊圖、遮罩、layout.json")
    u.add_argument("--nif", action="append", required=True, help="NIF（可多次）")
    u.add_argument("--tex", action="append", help="DDS／PNG 貼圖（可多次）")
    u.add_argument("--auto-tex", action="store_true", help="再照 NIF 的貼圖槽把貼圖找來（要 --data）")
    u.add_argument("--data", action="append", default=[], help="Data 根（可多次，依序找）：--nif／--tex 可給 Data 相對路徑")
    u.add_argument("--out", required=True, help="輸出夾（白名單內）")
    u.add_argument("--assign", action="append", help="SHAPE=TEXKEY：檔名對不上時手動指定（可多次）")
    u.set_defaults(fn=cmd_unpack)
    k = sub.add_parser("pack", help="PNG → RGBA8 DDS（帶 mip）；對照原圖查尺寸與 alpha")
    k.add_argument("png", help="改好的 PNG")
    k.add_argument("--ref", required=True, help="原圖（DDS 或 unpack 出的 PNG）")
    k.add_argument("--out", required=True, help="輸出 DDS（白名單內）")
    k.add_argument("--report", help="另存報告 JSON")
    g = k.add_mutually_exclusive_group()
    g.add_argument("--fail-on-alpha", action="store_true", help="alpha 有變動就不寫（rc 1）")
    g.add_argument("--restore-alpha", action="store_true", help="alpha 照原圖抄回再寫")
    k.set_defaults(fn=cmd_pack)
    e = sub.add_parser("edit", help="照操作清單（texedit-ops/1）改貼圖 PNG，寫 OUT 與 OUT.record.json")
    e.add_argument("ops", help="操作清單 JSON")
    e.add_argument("--out", required=True, help="輸出 PNG（白名單內）")
    e.add_argument("--strict", action="store_true", help="有 warnings（某步一個像素都沒改）就 rc 1（輸出照寫）")
    e.set_defaults(fn=cmd_edit)
    r = sub.add_parser("replay", help="照 record 重跑，比輸出像素 sha256（rc 1＝對不上）")
    r.add_argument("record")
    r.set_defaults(fn=cmd_replay)
    d = sub.add_parser("diff", help="兩張圖比：變動像素、各 shape／island 的變動、標紅圖")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--layout")
    d.add_argument("--texture")
    d.add_argument("--out", help="標紅圖 PNG")
    d.set_defaults(fn=cmd_diff)
    m = sub.add_parser("demo", help="合成洋裝走一遍 unpack→edit→diff→pack")
    m.add_argument("--out", required=True)
    m.set_defaults(fn=cmd_demo)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except REFUSE as e:
        print(f"texedit: 拒絕：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
