"""unpack：NIF（一個或多個）＋DDS → 貼圖 PNG、UV 疊圖、選區遮罩（shape／island）、layout.json。

輸出（OUT 底下）：
- `textures/<tex>.png`：RGBA 原尺寸（只取最上層 mip；法線圖照原樣轉，不做色彩處理）。
- `overlays/<tex>.all.png`：全部 shape 的 UV 線框疊在貼圖上（每 shape 一色、圖例）；
  `overlays/<tex>.<shape>.png`：單一 shape，island 中心寫編號（法線圖不出單 shape 疊圖）。
- `masks/<W>x<H>/<shape>.png`、`<shape>.i<k>.png`：shape 與各 island 的遮罩（'L'，255＝選中）；同尺寸的貼圖共用。
- `layout.json`：格式 `texedit-layout/1`，欄位見 README。

貼圖對 shape：DDS 檔名（不分大小寫）等於 shape 某個貼圖槽路徑的檔名就算用到；對不上的用 `assign` 補
（shape key 或 shape 名 → 貼圖 key）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath

from ai3dlib.assets import norm
from ai3dlib.geom import load_meshes
from ai3dlib import dds as ddslib
from texedit import paths, uvmap
from texedit.pack import dds_info

FORMAT = "texedit-layout/1"
SLOT_NAMES = ["diffuse", "normal", "glow/skin", "height/parallax", "environment", "env_mask", "inner/multilayer",
              "specular/backlight", "slot8"]


class UnpackError(ValueError):
    pass


def safe(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("._")
    return s or "x"


def _unique(base: str, taken: set) -> str:
    k, n = base, 2
    while k in taken:
        k, n = f"{base}~{n}", n + 1
    taken.add(k)
    return k


def _read(p, assets) -> tuple[bytes, str]:
    """檔案系統路徑先找；找不到而有 assets（`--data`）就當 Data 相對路徑找。回 (bytes, 來源)。"""
    pp = Path(p)
    if pp.is_file():
        return pp.read_bytes(), str(pp)
    if assets is None:
        raise UnpackError(f"找不到 {p}（Data 相對路徑要加 --data）")
    try:
        data = assets.get(str(p))
    except LookupError as e:
        raise UnpackError(str(e)) from None
    return data, f"{p} ← {assets.seen.get(norm(str(p)), {}).get('provider', '?')}"


def _slot_rel(slot: str) -> str:
    s = slot.replace("/", "\\").lstrip("\\")
    return s if s.lower().startswith("textures\\") else "textures\\" + s


def load_shapes(nif_paths, assets=None) -> list[uvmap.Shape]:
    out, taken = [], set()
    for p in nif_paths:
        data, _src = _read(p, assets)
        p = Path(str(p).replace("\\", "/"))
        for m in load_meshes(p, data=data, resolve=False):
            if not m.uvs:
                continue
            key = _unique(f"{safe(p.stem)}.{safe(m.name)}", taken)
            s = uvmap.Shape(key, str(p), m.name, m.kind, list(m.uvs), [tuple(t) for t in m.triangles],
                            list(m.textures))
            s.islands = uvmap.find_islands(s.uvs, s.tris)
            s.out_of_range_tris = uvmap.out_of_range(s)
            out.append(s)
    return out


def _slot_base(slot: str) -> str:
    return PureWindowsPath(slot.replace("/", "\\")).name.lower()


def is_normal(key: str, slot_idx) -> bool:
    return slot_idx == 1 or key.lower().endswith(("_n", "_msn"))


def unpack(nifs, textures, out, assign=None, overlay_lo: int = 512, overlay_hi: int = 2048,
           assets=None, auto_tex: bool = False) -> dict:
    """回 layout dict（也寫到 OUT/layout.json）。assign：{shape key 或名: 貼圖 key}。
    assets：`ai3dlib.assets.AssetInputs`（--data），NIF 與貼圖可給 Data 相對路徑；auto_tex：
    再把 shape 貼圖槽列的貼圖全找來（找不到的記在 layout 的 missing_textures，不中斷）。"""
    out = Path(out)
    shapes = load_shapes(nifs, assets)
    if not shapes:
        raise UnpackError("NIF 裡沒有帶 UV 的 shape")
    taken: set = set()
    texs = {}
    missing: list[str] = []
    todo = [(t, True) for t in textures]
    if auto_tex:
        seen = {Path(str(t).replace("\\", "/")).name.lower() for t in textures}
        for s in shapes:
            for slot in s.textures:
                if slot and _slot_base(slot) not in seen:
                    seen.add(_slot_base(slot))
                    todo.append((_slot_rel(slot), False))
    for t, must in todo:
        name = PureWindowsPath(str(t).replace("/", "\\")).name
        try:
            data, src = _read(t, assets)
        except UnpackError:
            if must:
                raise
            missing.append(str(t))
            continue
        key = _unique(safe(Path(name).stem), taken)
        try:
            im = ddslib.read_image(data)
        except ddslib.DDSError as e:
            raise UnpackError(f"{t}: {e}") from None
        texs[key] = {"name": name, "source": src, "im": im, "dds": dds_info(data)}
    # 對應：(shape key) → {tex key: slot index 或 None（assign）}
    use: dict[str, dict] = {s.key: {} for s in shapes}
    for s in shapes:
        for si, slot in enumerate(s.textures):
            if not slot:
                continue
            for k, t in texs.items():
                if _slot_base(slot) == t["name"].lower():
                    use[s.key].setdefault(k, si)
    for who, tk in (assign or {}).items():
        if tk not in texs:
            raise UnpackError(f"assign：沒有貼圖 key {tk}（有 {sorted(texs)}）")
        hit = [s for s in shapes if who in (s.key, s.name)]
        if not hit:
            raise UnpackError(f"assign：沒有 shape {who}（有 {[s.key for s in shapes]}）")
        for s in hit:
            use[s.key].setdefault(tk, None)

    paths.make_dirs(out)
    layout = {"format": FORMAT,
              "uv_convention": "x=u*W, y=v*H（v=0 在圖頂）；UV 越界照 repeat 包回",
              "nifs": [str(p) for p in nifs], "textures": {}, "missing_textures": missing, "shapes": [],
              "overlaps": []}
    mask_cache: dict[tuple, object] = {}

    def mask_of(s, size, isl=None):
        k = (s.key, size, None if isl is None else isl.index)
        if k not in mask_cache:
            tris = range(len(s.tris)) if isl is None else isl.tris
            mask_cache[k] = uvmap.rasterize(s, tris, size)
        return mask_cache[k]

    written_sizes: set = set()
    shape_maps: dict[str, dict] = {s.key: {} for s in shapes}
    for tk, t in texs.items():
        im, size = t["im"], t["im"].size
        users = [(s, use[s.key][tk]) for s in shapes if tk in use[s.key]]
        normal = any(is_normal(tk, si) for _s, si in users) or is_normal(tk, None)
        rel_png = f"textures/{tk}.png"
        paths.write_png(out / rel_png, im)
        scale = uvmap.view_scale(size, overlay_lo, overlay_hi)
        entries = [(s, uvmap.color_for(i), f"{s.key} ({len(s.islands)} islands)") for i, (s, _si) in enumerate(users)]
        rel_all = f"overlays/{tk}.all.png"
        paths.write_png(out / rel_all, uvmap.overlay(im, entries, f"{tk} {size[0]}x{size[1]} all shapes",
                                                    scale=scale))
        sz = f"{size[0]}x{size[1]}"
        for i, (s, si) in enumerate(users):
            rel_mask = f"masks/{sz}/{s.key}.png"
            m = mask_of(s, size)
            if (sz, s.key) not in written_sizes:
                paths.write_png(out / rel_mask, m)
            entry = {"slot": si, "slot_name": SLOT_NAMES[si] if si is not None and si < len(SLOT_NAMES) else None,
                     "mask": rel_mask, **uvmap.mask_stats(m), "islands": []}
            for isl in s.islands:
                rel_im = f"masks/{sz}/{s.key}.i{isl.index}.png"
                mi = mask_of(s, size, isl)
                if (sz, s.key) not in written_sizes:
                    paths.write_png(out / rel_im, mi)
                entry["islands"].append({"index": isl.index, "mask": rel_im, **uvmap.mask_stats(mi)})
            written_sizes.add((sz, s.key))
            if not normal:
                rel_ov = f"overlays/{tk}.{s.key}.png"
                paths.write_png(out / rel_ov, uvmap.overlay(im, [entries[i]], f"{tk} / {s.key}: island index at centre",
                                                            labels=True, scale=scale))
                entry["overlay"] = rel_ov
            shape_maps[s.key][tk] = entry
        # 重疊：同一張貼圖上 shape 兩兩；同一 shape 內 island 兩兩（包圍盒有交才算）
        for a in range(len(users)):
            for b in range(a + 1, len(users)):
                n = uvmap.overlap_pixels(mask_of(users[a][0], size), mask_of(users[b][0], size))
                if n:
                    layout["overlaps"].append({"texture": tk, "a": users[a][0].key, "b": users[b][0].key, "pixels": n})
        for s, _si in users:
            stats = {e["index"]: e for e in shape_maps[s.key][tk]["islands"]}
            for x in s.islands:
                for y in s.islands:
                    if y.index <= x.index or not _bbox_meet(stats[x.index]["bbox_px"], stats[y.index]["bbox_px"]):
                        continue
                    n = uvmap.overlap_pixels(mask_of(s, size, x), mask_of(s, size, y))
                    if n:
                        layout["overlaps"].append({"texture": tk, "a": f"{s.key}#i{x.index}",
                                                   "b": f"{s.key}#i{y.index}", "pixels": n})
        layout["textures"][tk] = {"source": t["source"], "png": rel_png, "size": list(size),
                                  "dds": t["dds"], "normal_map": normal, "overlay_all": rel_all,
                                  "overlay_scale": scale,
                                  "users": [{"shape": s.key, "slot": si} for s, si in users]}
    for s in shapes:
        used = {si for si in use[s.key].values() if si is not None}
        layout["shapes"].append({
            "key": s.key, "nif": s.nif, "name": s.name, "kind": s.kind, "verts": len(s.uvs), "tris": len(s.tris),
            "slots": s.textures, "unmatched_slots": [i for i, p in enumerate(s.textures) if p and i not in used],
            "uv_out_of_range_tris": s.out_of_range_tris,
            "islands": [{"index": i.index, "tris": len(i.tris), "uv_area": round(i.uv_area, 6),
                         "bbox_uv": [round(x, 6) for x in i.bbox_uv],
                         "centroid_uv": [round(x, 6) for x in i.centroid_uv]} for i in s.islands],
            "maps": shape_maps[s.key]})
    paths.write_text(out / "layout.json", json.dumps(layout, ensure_ascii=False, indent=1) + "\n")
    return layout


def _bbox_meet(a, b) -> bool:
    if not a or not b:
        return False
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def parse_assign(items) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise UnpackError(f"--assign 要寫成 SHAPE=TEXKEY：{it}")
        k, v = it.split("=", 1)
        out[k] = v
    return out

