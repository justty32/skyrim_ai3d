"""preview 的核心：NIF（衣服、可加身體）＋diffuse 貼圖 → 正面／背面（可加側面）PNG、合併圖、`preview.json`。

貼圖怎麼找（每個 shape 只畫 diffuse，也就是貼圖槽 0）：
1. `--tex KEY=FILE`，KEY 對到 shape（shape key `<nif 檔名>.<shape 名>` 或 shape 名，可用 glob）。
2. `--tex KEY=FILE`，KEY 對到該 shape 的 diffuse 槽（槽路徑的檔名主幹如 `dress_d`，或 Data 相對全路徑）；
   只給 `--tex FILE` 時 KEY＝FILE 的檔名主幹，所以 texedit `edit --out edit/dress_d.png` 的輸出可直接餵。
3. 有 `--data` 時照 diffuse 槽路徑去找（`ai3dlib.assets`，大小寫不敏感，依序找）。
4. 都沒有：平塗色，記在 `missing_textures`。
沒對到任何 shape 的 `--tex` 會被拒絕（打錯 KEY 時不默默畫錯圖）。

alpha：照 NiAlphaProperty。只有 alpha test（flags 0x200）→ alpha < threshold 丟掉；有 blend（0x1）→ 半透明疊色
（alpha ≥ 230 當不透明寫深度；同時有 test 時先 test）；沒有 NiAlphaProperty → 不看貼圖 alpha。

輸出（OUT 底下）：`<view>.png`、`sheet.png`（各視角橫排、左上標名）、`preview.json`（格式 `ai3d-preview/1`）。
OUT 要嘛不存在／空夾，要嘛是 preview 寫過的夾（有 `preview.json`）；別的非空夾拒絕。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ai3dlib import dds as ddslib
from ai3dlib.assets import AssetInputs, norm
from ai3dlib.geom import bounds, load_meshes, resolve_skinning
from ai3dlib.nif import NifError
from preview import raster
from preview.paths import OutDir

FORMAT = "ai3d-preview/1"
DEFAULT_VIEWS = "front,back"


class PreviewError(ValueError):
    pass


@dataclass
class Options:
    nifs: list
    out: str
    tex: list = field(default_factory=list)       # "KEY=FILE" 或 "FILE"
    data: list = field(default_factory=list)
    views: str = DEFAULT_VIEWS
    size: int = 512
    frame: tuple | None = None                    # (cx, cy, cz, r)
    tex_max: int = 1024
    hide: list = field(default_factory=list)
    skinned_transform: str = "ignore"
    bg: tuple = raster.BG
    frame_from: str | None = None                 # rerun 用：照記錄寫回 camera.frame_from（None＝依 frame 判）


# ---------------------------------------------------------------- 參數

def safe(s: str) -> str:
    """同 texedit 的 shape key 寫法。"""
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("._")
    return s or "x"


def parse_views(spec: str) -> list[tuple[str, float]]:
    out = []
    for v in (x.strip() for x in spec.split(",")):
        if not v:
            continue
        if v in raster.VIEWS:
            out.append((v, raster.VIEWS[v]))
            continue
        try:
            yaw = float(v)
        except ValueError:
            raise PreviewError(f"視角 {v!r} 不認得：用 {','.join(raster.VIEWS)} 或角度數字") from None
        out.append((f"yaw{v.replace('-', 'm').replace('.', 'p')}", yaw))
    if not out:
        raise PreviewError("至少要一個視角")
    names = [n for n, _ in out]
    if len(set(names)) != len(names):
        raise PreviewError(f"視角重複：{spec}")
    return out


def parse_tex(items) -> list[tuple[str, str, bool]]:
    """→ [(key, file, explicit_key)]。KEY=FILE 以第一個 `=` 切；沒有 `=` 時 key＝檔名主幹。
    整串本身就是一個存在的檔（路徑裡有 `=`）時當 FILE。"""
    out = []
    for it in items or []:
        key, sep, f = it.partition("=")
        if sep and os.path.isfile(it):
            out.append((Path(it).stem, it, False))
        elif sep and key and f:
            out.append((key.strip(), f, True))
        elif not sep:
            out.append((Path(it).stem, it, False))
        else:
            raise PreviewError(f"--tex 要 KEY=FILE 或 FILE：{it!r}")
    return out


def _slot_rel(slot: str) -> str:
    s = slot.replace("/", "\\").lstrip("\\")
    low = s.lower()
    if "\\data\\textures\\" in low:                   # 作者留下的絕對路徑（c:\...\data\textures\...）
        s = s[low.index("\\data\\textures\\") + 6:]
    return s if s.lower().startswith("textures\\") else "textures\\" + s


def _slot_stem(slot: str) -> str:
    n = PureWindowsPath(slot.replace("/", "\\")).name.lower()
    return n.rsplit(".", 1)[0] if "." in n else n


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- 讀入

def _read(p, assets) -> tuple[bytes, str]:
    pp = Path(p)
    if pp.is_file():
        return pp.read_bytes(), str(pp.resolve())
    if assets is None:
        raise PreviewError(f"找不到 {p}（Data 相對路徑要加 --data）")
    data = assets.try_get(str(p))
    if data is None:
        raise PreviewError(f"找不到 {p}（--data 夾都沒有）")
    return data, str(p)


@dataclass
class Shape:
    key: str
    nif: str
    mesh: object
    hidden: bool = False


def load_shapes(nifs, assets, skinned_transform="ignore") -> tuple[list[Shape], dict]:
    shapes, taken, inputs = [], set(), {}
    for p in nifs:
        data, src = _read(p, assets)
        inputs[src] = _sha(data)
        stem = Path(str(p).replace("\\", "/")).stem
        try:
            ms = load_meshes(src, data=data, resolve=False, skinned_transform=skinned_transform)
        except NifError as e:
            raise PreviewError(f"NIF 解析失敗：{p}：{e}") from None
        for m in ms:
            k, n = f"{safe(stem)}.{safe(m.name)}", 2
            base = k
            while k in taken:
                k, n = f"{base}~{n}", n + 1
            taken.add(k)
            shapes.append(Shape(k, src, m))
    resolve_skinning([s.mesh for s in shapes])
    return shapes, inputs


def _match(pattern: str, *names) -> bool:
    p = pattern.lower()
    return any(n and fnmatch.fnmatchcase(n.lower(), p) for n in names)


def assign_textures(shapes: list[Shape], tex_entries, assets) -> tuple[list[dict], dict, list]:
    """每個 shape 一份 {how, file|slot, entry}；回 (assignments, 用到的貼圖檔 {source: bytes}, missing)。"""
    used = [False] * len(tex_entries)
    out, files, missing = [], {}, []
    for s in shapes:
        slot = s.mesh.textures[0] if s.mesh.textures else ""
        a = {"slot": slot, "how": "none", "source": None}
        hit = None
        for i, (key, f, explicit) in enumerate(tex_entries):
            if explicit and _match(key, s.key, s.mesh.name):
                hit, a["how"] = i, "tex-shape"
                break
        if hit is None and slot:
            for i, (key, f, _e) in enumerate(tex_entries):
                if _match(key, _slot_stem(slot)) or norm(key) == norm(_slot_rel(slot)):
                    hit, a["how"] = i, "tex-slot"
                    break
        if hit is not None:
            used[hit] = True
            f = tex_entries[hit][1]
            if not Path(f).is_file():
                raise PreviewError(f"--tex 的檔不存在：{f}")
            src = str(Path(f).resolve())
            if src not in files:
                files[src] = Path(f).read_bytes()
            a["source"], a["tex"] = src, f"{tex_entries[hit][0]}={f}" if tex_entries[hit][2] else f
        elif slot and assets is not None and s.mesh.uvs:
            rel = _slot_rel(slot)
            data = assets.try_get(rel)
            if data is not None:
                src = f"data:{norm(rel)}"
                files.setdefault(src, data)
                a["how"], a["source"] = "data", src
                a["provider"] = assets.seen.get(norm(rel), {}).get("provider")
        if a["source"] is None and not s.hidden:
            missing.append({"shape": s.key, "slot": slot or "(無 diffuse 槽)"} if s.mesh.uvs else
                           {"shape": s.key, "slot": slot, "reason": "shape 沒有 UV"})
        out.append(a)
    unused = [tex_entries[i][1] if not tex_entries[i][2] else f"{tex_entries[i][0]}={tex_entries[i][1]}"
              for i, u in enumerate(used) if not u]
    if unused:
        keys = [f"{s.key}（槽 {_slot_stem(s.mesh.textures[0]) if s.mesh.textures and s.mesh.textures[0] else '-'}）"
                for s in shapes]
        raise PreviewError(f"--tex 沒對到任何 shape：{', '.join(unused)}；可用的 shape 與 diffuse 槽：{', '.join(keys)}")
    return out, files, missing


def mode_of(m) -> tuple[str, bool]:
    if m.blend:
        return "blend", m.alpha_test
    if m.alpha_test:
        return "test", True
    return "opaque", False


# ---------------------------------------------------------------- 輸出夾

def check_out_dir(out: Path) -> None:
    if out.exists():
        if not out.is_dir():
            raise PreviewError(f"--out {out} 不是資料夾")
        if any(out.iterdir()) and not (out / "preview.json").is_file():
            raise PreviewError(f"--out {out} 不是空夾，也不是 preview 寫過的夾（沒有 preview.json），拒絕")


def _sheet(images, names, bg):
    from PIL import Image, ImageDraw
    w, h = images[0].size
    sheet = Image.new("RGB", (w * len(images), h), bg)
    d = ImageDraw.Draw(sheet)
    for k, (im, n) in enumerate(zip(images, names)):
        sheet.paste(im, (k * w, 0))
        d.text((k * w + 6, 4), n, fill=(235, 235, 235))
    return sheet


# ---------------------------------------------------------------- 主流程

def run(opt: Options) -> dict:
    from PIL import Image, __version__ as pil_version
    views = parse_views(opt.views)
    if opt.size < 16 or opt.size > 4096:
        raise PreviewError("--size 要在 16–4096")
    if opt.tex_max < 1:
        raise PreviewError("--tex-max 要 ≥ 1")
    tex_entries = parse_tex(opt.tex)
    assets = AssetInputs(opt.data) if opt.data else None
    out_path = Path(opt.out).expanduser()
    check_out_dir(out_path)

    shapes, inputs = load_shapes(opt.nifs, assets, opt.skinned_transform)
    for s in shapes:
        s.hidden = any(_match(p, s.key, s.mesh.name, Path(s.nif).name) for p in opt.hide)
    assigns, files, missing = assign_textures(shapes, tex_entries, assets)
    images_cache, tex_info = {}, {}
    for src, data in files.items():
        try:
            im = ddslib.open_rgba(data, max_side=opt.tex_max, wrap_errors=True)
        except ddslib.DDSError as e:
            raise PreviewError(f"{src}：{e}") from None
        images_cache[src] = im
        tex_info[src] = {"sha256": _sha(data), "used_size": list(im.size)}

    layers, report, visible = [], [], []
    for i, (s, a) in enumerate(zip(shapes, assigns)):
        m = s.mesh
        mode, test = mode_of(m)
        info = {"key": s.key, "nif": s.nif, "shape": m.name, "kind": m.kind, "verts": len(m.positions),
                "tris": len(m.triangles), "mode": mode, "alpha_test": test, "alpha_threshold": m.alpha_threshold,
                "alpha_flags": m.alpha_flags, "shader_type": m.shader_type, "diffuse_slot": a["slot"],
                "texture": {k: v for k, v in a.items() if k != "slot"}}
        if m.dropped_triangles:
            info["dropped_tris"] = m.dropped_triangles
        if s.hidden:
            info["hidden"] = True
            report.append(info)
            continue
        color = raster.PALETTE[i % len(raster.PALETTE)]
        L = raster.Layer(m.positions, m.triangles, color, m.uvs, mode=mode, threshold=m.alpha_threshold, test=test)
        if a["source"] is not None and m.uvs:
            L.sampler = ddslib.sampler_from_image(images_cache[a["source"]])
        info["layer"] = len(layers)
        layers.append(L)
        visible.append(m)
        report.append(info)
    if not layers:
        raise PreviewError("沒有可畫的 shape")
    if opt.frame:
        cx, cy, cz, r = opt.frame
        if not r > 0:
            raise PreviewError("--frame 的半徑要 > 0")
        frame = raster.Frame((cx, cy, cz), r)
    else:
        _lo, _hi, c, r = bounds(visible)
        frame = raster.Frame(tuple(c), r or 1.0)

    od = OutDir(out_path)
    shots, ims = [], []
    for name, yaw in views:
        buf, cover = raster.render(layers, yaw, opt.size, frame, bg=tuple(opt.bg))
        im = Image.frombytes("RGB", (opt.size, opt.size), bytes(buf))
        p = od.write_png(f"{name}.png", im)
        ims.append(im)
        shots.append({"view": name, "yaw": yaw, "file": p.name, "sha256": _sha(p.read_bytes()),
                      "pixels_sha256": _sha(im.tobytes()),
                      "coverage": {report_k["key"]: cover[report_k["layer"]] for report_k in report
                                   if "layer" in report_k}})
    sp = od.write_png("sheet.png", _sheet(ims, [n for n, _ in views], tuple(opt.bg)))
    warnings = []
    for sh in shots:
        for k, n in sh["coverage"].items():
            if n == 0:
                warnings.append(f"{sh['view']}：{k} 一個像素都沒畫到（被擋住、全透明，或在畫面外）")
    doc = {
        "format": FORMAT,
        "args": {"nifs": [s if not Path(s).is_file() else str(Path(s).resolve()) for s in map(str, opt.nifs)],
                 "tex": [f"{k}={str(Path(f).resolve())}" if e else str(Path(f).resolve()) for k, f, e in tex_entries],
                 "data": [str(Path(d).resolve()) for d in opt.data], "views": opt.views, "size": opt.size,
                 "tex_max": opt.tex_max, "hide": list(opt.hide), "skinned_transform": opt.skinned_transform,
                 "bg": "%02X%02X%02X" % tuple(opt.bg)},
        "camera": {"projection": "orthographic", "frame": [*frame.center, frame.radius],
                   "frame_from": opt.frame_from or ("--frame" if opt.frame else "bounds"), "fill": raster.FILL,
                   "light_camera_space": list(raster.LIGHT), "ambient": raster.AMBIENT,
                   "yaw_convention": "0=正面（相機在 +y 看 -y）、180=背面、90=從角色右側",
                   "views": [{"view": n, "yaw": y} for n, y in views]},
        "inputs": {"nifs": inputs, "textures": tex_info},
        "shapes": report,
        "missing_textures": missing,
        "images": shots,
        "sheet": {"file": sp.name, "sha256": _sha(sp.read_bytes())},
        "pillow": pil_version,
        "warnings": warnings,
    }
    od.write_text("preview.json", json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return doc


def options_from_doc(doc: dict, out) -> Options:
    if doc.get("format") != FORMAT:
        raise PreviewError(f"不是 {FORMAT}：{doc.get('format')!r}")
    a = doc["args"]
    bg = a.get("bg", "%02X%02X%02X" % raster.BG)
    return Options(nifs=a["nifs"], out=str(out), tex=a["tex"], data=a["data"], views=a["views"], size=a["size"],
                   frame=tuple(doc["camera"]["frame"]), tex_max=a["tex_max"], hide=a["hide"],
                   skinned_transform=a["skinned_transform"], frame_from=doc["camera"].get("frame_from"),
                   bg=tuple(int(bg[i:i + 2], 16) for i in (0, 2, 4)))


def rerun(json_path, out=None) -> dict:
    """照 preview.json 重畫（固定用記錄的相機），比對每張圖的像素 sha256 與輸入檔 sha256。
    out 省略＝寫回 preview.json 所在夾。"""
    jp = Path(json_path)
    try:
        old = json.loads(jp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PreviewError(f"讀不了 {jp}：{e}") from None
    out = Path(out) if out else jp.parent
    new = run(options_from_doc(old, out))
    o_img = {s["view"]: s["pixels_sha256"] for s in old.get("images", [])}
    changed_views = [s["view"] for s in new["images"] if o_img.get(s["view"]) != s["pixels_sha256"]]
    changed_inputs = []
    for kind in ("nifs", "textures"):
        o, n = old.get("inputs", {}).get(kind, {}), new["inputs"][kind]
        for k in sorted(set(o) | set(n)):
            ov = o[k] if kind == "nifs" else o.get(k, {}).get("sha256") if k in o else None
            nv = n[k] if kind == "nifs" else n.get(k, {}).get("sha256") if k in n else None
            if ov != nv:
                changed_inputs.append(k)
    if new["pillow"] != old.get("pillow"):
        new["warnings"].append(f"Pillow 版本不同（記錄 {old.get('pillow')}，現在 {new['pillow']}），像素可能因此不同")
    return {"out": str(out), "same_images": not changed_views, "changed_views": changed_views,
            "changed_inputs": changed_inputs, "warnings": new["warnings"]}
