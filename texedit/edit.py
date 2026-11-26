"""edit：照操作清單（`texedit-ops/1`）改一張 unpack 出來的貼圖 PNG；每一步限制在選區遮罩內，alpha 一律不動。

操作清單（JSON；相對路徑以清單檔所在夾為準）：

    {"format": "texedit-ops/1", "layout": "out/layout.json", "texture": "dress_d",
     "base": "（可省；預設 layout 裡這張貼圖的 PNG）",
     "ops": [{"op": "fill", "target": {"shape": "dress.Top", "island": 0}, "color": "#1E90C8"}, ...]}

target（選區）：`{"shape": KEY 或名, "island": n 或 [n…]}`、`{"mask": "x.png"}`、`{"rect_px": [x0,y0,x1,y1]}`、
`{"rect_uv": [u0,v0,u1,v1]}`、`{"all": true}`；也可以是 list（聯集）。另可加 `exclude`（同格式，扣掉）、
`grow`（像素，負數＝內縮）、`feather`（高斯模糊半徑）。

op：
- `fill`：`color`。
- `gradient`：`stops` [[位置 0–1, 色], …] 或 `from`／`to`；`angle` 度（0＝左→右、90＝上→下），範圍是選區包圍盒。
- `pattern`：`image`（小圖，平鋪）、`scale`、`offset` [x, y]；縮放用 `resample`（lanczos 預設／bilinear／nearest，像素圖用 nearest）。
- `decal`：`image`、`center` [x, y] 像素或 `center_uv`、`scale` 或 `size` [w, h]、`rotate` 度（逆時針）。
- `recolor`：`color`；`blend` 預設 `color`（保留原圖明暗只換色相與飽和度）。

每個 op 都可加 `opacity`（0–1）與 `blend`：normal（預設；recolor 預設 color）、multiply、screen、overlay、
colorize（原圖灰階經「黑→色→白」上色）、color（色相＋飽和度取新色、明度取原圖）、hue（只換色相）。

輸出：OUT PNG＋`OUT.record.json`（清單全文、每個輸入的 sha256、每步改了幾個像素、各 shape／island 被改的像素數、
輸出像素 sha256），`replay` 用它重放比對。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from texedit import paths
from texedit.pack import changed, load_rgba, rgb_changed

FORMAT = "texedit-ops/1"
RECORD = "texedit-record/1"
BLENDS = ("normal", "multiply", "screen", "overlay", "colorize", "color", "hue")
OPS = ("fill", "gradient", "pattern", "decal", "recolor")


class EditError(ValueError):
    pass


def sha_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sha_pixels(im) -> str:
    h = hashlib.sha256(f"{im.mode} {im.size[0]}x{im.size[1]}\n".encode())
    h.update(im.tobytes())
    return h.hexdigest()


def parse_color(c):
    from PIL import ImageColor
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return tuple(int(x) for x in c[:3])
    try:
        return ImageColor.getrgb(str(c))[:3]
    except ValueError:
        raise EditError(f"看不懂的顏色：{c!r}") from None


# ---------------------------------------------------------------- 選區

class Ctx:
    def __init__(self, ops_dir: Path, layout: dict | None, layout_dir: Path | None, texture: str | None, size):
        self.ops_dir, self.layout, self.layout_dir, self.texture, self.size = ops_dir, layout, layout_dir, texture, size
        self.inputs: dict[str, str] = {}

    def path(self, rel) -> Path:
        p = Path(rel)
        p = p if p.is_absolute() else self.ops_dir / p
        if not p.is_file():
            raise EditError(f"找不到 {p}")
        self.inputs.setdefault(str(rel), sha_file(p))
        return p

    def image(self, rel, mode="RGBA"):
        from PIL import Image
        with Image.open(self.path(rel)) as im:
            im.load()
            return im.convert(mode)

    def shape(self, who: str) -> dict:
        if self.layout is None:
            raise EditError("用 shape 選區要有 layout")
        hits = [s for s in self.layout["shapes"] if s["key"] == who] or \
               [s for s in self.layout["shapes"] if s["name"] == who]
        if len(hits) != 1:
            raise EditError(f"shape {who!r} 對到 {len(hits)} 個（有 {[s['key'] for s in self.layout['shapes']]}）")
        m = hits[0]["maps"].get(self.texture)
        if m is None:
            raise EditError(f"shape {hits[0]['key']} 沒用到貼圖 {self.texture}（用到 {sorted(hits[0]['maps'])}）")
        return m

    def layout_mask(self, rel):
        from PIL import Image
        p = self.layout_dir / rel
        self.inputs.setdefault(f"layout:{rel}", sha_file(p))
        with Image.open(p) as im:
            im.load()
            return im.convert("L")


def _check_size(ctx: Ctx, im, what: str):
    if im.size != ctx.size:
        raise EditError(f"{what} 尺寸 {im.size} 跟貼圖 {ctx.size} 不同")
    return im


def target_mask(ctx: Ctx, t) -> "object":
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    if isinstance(t, list):
        if not t:
            raise EditError("target 是空 list")
        out = Image.new("L", ctx.size, 0)
        for x in t:
            out = ImageChops.lighter(out, target_mask(ctx, x))
    elif not isinstance(t, dict):
        raise EditError(f"看不懂的 target：{t!r}")
    elif "shape" in t:
        m = ctx.shape(t["shape"])
        isl = t.get("island")
        if isl is None:
            out = ctx.layout_mask(m["mask"])
        else:
            want = isl if isinstance(isl, list) else [isl]
            by = {e["index"]: e for e in m["islands"]}
            out = Image.new("L", ctx.size, 0)
            for k in want:
                if k not in by:
                    raise EditError(f"shape {t['shape']} 沒有 island {k}（有 0–{len(by) - 1}）")
                out = ImageChops.lighter(out, ctx.layout_mask(by[k]["mask"]))
        out = _check_size(ctx, out, f"shape {t['shape']} 遮罩")
    elif "mask" in t:
        out = _check_size(ctx, ctx.image(t["mask"], "L"), f"遮罩 {t['mask']}")
    elif "rect_px" in t or "rect_uv" in t:
        if "rect_px" in t:
            x0, y0, x1, y1 = t["rect_px"]
        else:
            w, h = ctx.size
            u0, v0, u1, v1 = t["rect_uv"]
            x0, y0, x1, y1 = u0 * w, v0 * h, u1 * w, v1 * h
        out = Image.new("L", ctx.size, 0)
        ImageDraw.Draw(out).rectangle([round(x0), round(y0), round(x1) - 1, round(y1) - 1], fill=255)
    elif t.get("all"):
        out = Image.new("L", ctx.size, 255)
    else:
        raise EditError(f"target 要有 shape／mask／rect_px／rect_uv／all 之一：{t!r}")
    if isinstance(t, dict):
        if t.get("exclude") is not None:
            out = ImageChops.subtract(out, target_mask(ctx, t["exclude"]))
        g = int(t.get("grow", 0))
        if g:
            f = ImageFilter.MaxFilter(3) if g > 0 else ImageFilter.MinFilter(3)
            for _ in range(abs(g)):
                out = out.filter(f)
        if t.get("feather"):
            out = out.filter(ImageFilter.GaussianBlur(float(t["feather"])))
    return out


# ---------------------------------------------------------------- 畫

def blend(mode: str, base, paint):
    """兩張 RGB：回混合後的 RGB。"""
    from PIL import Image, ImageChops
    if mode == "normal":
        return paint
    if mode == "multiply":
        return ImageChops.multiply(base, paint)
    if mode == "screen":
        return ImageChops.screen(base, paint)
    if mode == "overlay":
        return ImageChops.overlay(base, paint)
    if mode == "colorize":
        gray = base.convert("L").convert("RGB")
        return ImageChops.hard_light(paint, gray)
    if mode in ("color", "hue"):
        bh, bs, bv = base.convert("HSV").split()
        ph, ps, _pv = paint.convert("HSV").split()
        return Image.merge("HSV", (ph, ps if mode == "color" else bs, bv)).convert("RGB")
    raise EditError(f"blend 要是 {BLENDS} 之一，收到 {mode!r}")


def _lut(stops):
    """[(t, (r,g,b))…] → 三條 256 項查表。"""
    stops = sorted((float(t), c) for t, c in stops)
    luts = ([], [], [])
    for i in range(256):
        x = i / 255
        if x <= stops[0][0]:
            c = stops[0][1]
        elif x >= stops[-1][0]:
            c = stops[-1][1]
        else:
            for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
                if t0 <= x <= t1:
                    f = 0 if t1 == t0 else (x - t0) / (t1 - t0)
                    c = tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
                    break
        for k in range(3):
            luts[k].append(c[k])
    return luts


def paint_gradient(op, size, bbox):
    from PIL import Image
    if "stops" in op:
        stops = [(t, parse_color(c)) for t, c in op["stops"]]
    else:
        stops = [(0.0, parse_color(op["from"])), (1.0, parse_color(op["to"]))]
    if len(stops) < 2:
        raise EditError("gradient 至少兩個色標")
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    a = math.radians(float(op.get("angle", 90)))
    dx, dy = math.cos(a), math.sin(a)
    half = (abs(dx) * w + abs(dy) * h) / 2 or 1
    gw, gh = min(w, 256), min(h, 256)
    t = Image.new("L", (gw, gh))
    t.putdata([max(0, min(255, round(255 * (((((i + 0.5) / gw - 0.5) * w) * dx + (((j + 0.5) / gh - 0.5) * h) * dy)
                                             / half + 1) / 2)))
               for j in range(gh) for i in range(gw)])
    if (gw, gh) != (w, h):
        t = t.resize((w, h), Image.BILINEAR)
    r, g, b = _lut(stops)
    tile = Image.merge("RGB", (t.point(r), t.point(g), t.point(b))).convert("RGBA")
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(tile, (x0, y0))
    return out


def _scaled(img, op):
    from PIL import Image
    if "size" in op:
        w, h = (int(x) for x in op["size"])
    else:
        s = float(op.get("scale", 1.0))
        w, h = max(1, round(img.size[0] * s)), max(1, round(img.size[1] * s))
    rs = {"nearest": Image.NEAREST, "bilinear": Image.BILINEAR, "lanczos": Image.LANCZOS}
    mode = op.get("resample", "lanczos")
    if mode not in rs:
        raise EditError(f"resample 要是 {sorted(rs)} 之一，收到 {mode!r}")
    return img.resize((w, h), rs[mode]) if (w, h) != img.size else img


def paint_pattern(ctx, op, size, bbox):
    from PIL import Image
    tile = _scaled(ctx.image(op["image"]), op)
    tw, th = tile.size
    ox, oy = (int(v) for v in op.get("offset", [0, 0]))
    x0, y0, x1, y1 = bbox
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    sx = x0 - ((x0 - ox) % tw)
    sy = y0 - ((y0 - oy) % th)
    for y in range(sy, y1, th):
        for x in range(sx, x1, tw):
            out.paste(tile, (x, y))
    return out


def paint_decal(ctx, op, size):
    from PIL import Image
    img = _scaled(ctx.image(op["image"]), op)
    if op.get("rotate"):
        img = img.rotate(float(op["rotate"]), resample=Image.BICUBIC, expand=True)
    if "center_uv" in op:
        cx, cy = op["center_uv"][0] * size[0], op["center_uv"][1] * size[1]
    elif "center" in op:
        cx, cy = op["center"]
    else:
        raise EditError("decal 要給 center 或 center_uv")
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(img, (round(cx - img.size[0] / 2), round(cy - img.size[1] / 2)), img)
    return out


def apply_op(ctx: Ctx, cur, op: dict):
    """cur：RGB。回新的 RGB。"""
    from PIL import Image, ImageChops
    kind = op.get("op")
    if kind not in OPS:
        raise EditError(f"op 要是 {OPS} 之一，收到 {kind!r}")
    if "target" not in op:
        raise EditError(f"{kind}：缺 target")
    mask = target_mask(ctx, op["target"])
    bbox = mask.getbbox()
    if bbox is None:
        return cur
    size = cur.size
    if kind in ("fill", "recolor"):
        paint = Image.new("RGBA", size, parse_color(op["color"]) + (255,))
    elif kind == "gradient":
        paint = paint_gradient(op, size, bbox)
    elif kind == "pattern":
        paint = paint_pattern(ctx, op, size, bbox)
    else:
        paint = paint_decal(ctx, op, size)
    mode = op.get("blend", "color" if kind == "recolor" else "normal")
    mixed = blend(mode, cur, paint.convert("RGB"))
    eff = ImageChops.multiply(mask, paint.getchannel("A"))
    op_ = float(op.get("opacity", 1.0))
    if not 0 <= op_ <= 1:
        raise EditError(f"opacity 要在 0–1：{op_}")
    if op_ < 1:
        eff = eff.point(lambda v: round(v * op_))
    return Image.composite(mixed, cur, eff)


# ---------------------------------------------------------------- 清單

def load_ops(path) -> dict:
    path = Path(path)
    doc = json.loads(path.read_text("utf-8"))
    if doc.get("format") != FORMAT:
        raise EditError(f"{path}：format 要是 {FORMAT}")
    if not isinstance(doc.get("ops"), list):
        raise EditError(f"{path}：ops 要是 list")
    return doc


def run_ops(doc: dict, ops_dir: Path):
    """回 (結果 RGBA, 原圖 RGBA, ctx, 每步統計, layout)。"""
    layout = layout_dir = None
    if doc.get("layout"):
        lp = Path(doc["layout"])
        lp = lp if lp.is_absolute() else ops_dir / lp
        layout = json.loads(lp.read_text("utf-8"))
        layout_dir = lp.parent
    tex = doc.get("texture")
    if doc.get("base"):
        bp = Path(doc["base"])
        bp = bp if bp.is_absolute() else ops_dir / bp
    elif layout is not None and tex in layout.get("textures", {}):
        bp = layout_dir / layout["textures"][tex]["png"]
    else:
        raise EditError("要給 base，或 layout＋texture（layout 裡有的貼圖 key）")
    base = load_rgba(bp)
    ctx = Ctx(ops_dir, layout, layout_dir, tex, base.size)
    ctx.inputs["base"] = sha_file(bp)
    cur = base.convert("RGB")
    steps = []
    for i, op in enumerate(doc["ops"]):
        if not isinstance(op, dict):
            raise EditError(f"ops[{i}] 不是物件")
        try:
            nxt = apply_op(ctx, cur, op)
        except EditError as e:
            raise EditError(f"ops[{i}]（{op.get('op')}）：{e}") from None
        except KeyError as e:
            raise EditError(f"ops[{i}]（{op.get('op')}）：缺欄位 {e}") from None
        except (TypeError, ValueError) as e:
            raise EditError(f"ops[{i}]（{op.get('op')}）：欄位值不對：{e}") from None
        steps.append({"i": i, "op": op.get("op"), "changed_pixels": rgb_changed(cur, nxt)})
        cur = nxt
    out = cur.convert("RGBA")
    out.putalpha(base.getchannel("A"))
    return out, base, ctx, steps, layout


def per_shape_changes(layout, texture, layout_dir, before, after) -> dict:
    """各 shape／island 遮罩內被改的像素數（看有沒有溢到別的部件、共用 texel）。"""
    from PIL import Image, ImageChops
    if layout is None:
        return {}
    d = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    r, g, b = d.split()
    ch = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
    out = {}
    for s in layout["shapes"]:
        m = s["maps"].get(texture)
        if not m:
            continue
        rows = {}
        for key, rel in [("shape", m["mask"])] + [(f"i{e['index']}", e["mask"]) for e in m["islands"]]:
            with Image.open(layout_dir / rel) as mk:
                n = ImageChops.multiply(ch, mk.convert("L")).histogram()[255]
            if n:
                rows[key] = n
        if rows:
            out[s["key"]] = rows
    return out


def edit(ops_path, out_png) -> dict:
    ops_path = Path(ops_path)
    doc = load_ops(ops_path)
    out_img, base, ctx, steps, layout = run_ops(doc, ops_path.parent)
    paths.write_png(out_png, out_img)
    rec = {"format": RECORD, "ops_file": str(ops_path), "ops": doc, "inputs": ctx.inputs, "steps": steps,
           "changed_pixels": rgb_changed(base, out_img),
           "by_shape": per_shape_changes(layout, ctx.texture, ctx.layout_dir, base, out_img),
           "output": str(out_png), "output_pixels_sha256": sha_pixels(out_img)}
    paths.write_text(Path(str(out_png) + ".record.json"), json.dumps(rec, ensure_ascii=False, indent=1) + "\n")
    return rec


def replay(record_path) -> dict:
    """照 record 裡的清單全文重跑（相對路徑以原清單檔的夾為準），比輸入與輸出像素 sha256。不寫檔。"""
    rec = json.loads(Path(record_path).read_text("utf-8"))
    if rec.get("format") != RECORD:
        raise EditError(f"{record_path}：format 要是 {RECORD}")
    out_img, _base, ctx, steps, _l = run_ops(rec["ops"], Path(rec["ops_file"]).parent)
    got = sha_pixels(out_img)
    changed_inputs = sorted(k for k, v in rec["inputs"].items() if ctx.inputs.get(k) != v)
    return {"same_output": got == rec["output_pixels_sha256"], "output_pixels_sha256": got,
            "recorded": rec["output_pixels_sha256"], "changed_inputs": changed_inputs,
            "steps": steps}


def diff(a_png, b_png, layout_path=None, texture=None, out_png=None) -> dict:
    """兩張圖比：RGB 變動像素數、包圍盒、alpha 變動、（有 layout 時）各 shape／island 的變動；
    out_png：把變動處標紅疊在 A 上。"""
    from PIL import Image, ImageChops
    a, b = load_rgba(a_png), load_rgba(b_png)
    if a.size != b.size:
        raise EditError(f"尺寸不同：{a.size} vs {b.size}")
    n_alpha, _bb = changed(a.getchannel("A"), b.getchannel("A"))
    d = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    r, g, bl = d.split()
    ch = ImageChops.lighter(ImageChops.lighter(r, g), bl).point(lambda v: 255 if v else 0)
    bb = ch.getbbox()
    rep = {"a": str(a_png), "b": str(b_png), "rgb_changed_pixels": ch.histogram()[255],
           "changed_bbox": list(bb) if bb else None, "alpha_changed_pixels": n_alpha}
    if layout_path:
        lp = Path(layout_path)
        rep["by_shape"] = per_shape_changes(json.loads(lp.read_text("utf-8")), texture, lp.parent, a, b)
    if out_png:
        base = Image.blend(a.convert("RGB"), Image.new("RGB", a.size, (0, 0, 0)), 0.6)
        vis = Image.composite(Image.new("RGB", a.size, (255, 0, 0)), base, ch)
        paths.write_png(out_png, vis)
        rep["image"] = str(out_png)
    return rep
