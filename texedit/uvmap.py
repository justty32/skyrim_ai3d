"""UV → 像素：shape 的三角形、UV island（連通塊）、遮罩光柵化、UV 線框疊圖。只用 Pillow。

慣例（同 head_render `sampler_from_image`）：像素 x＝u·W、y＝v·H（v＝0 在圖頂）。UV 越界照 repeat 包回：
每個三角形先平移整數格讓最小 u／v 落在 [0,1)，跨出右／下緣的部分另畫一份平移 −1 的副本。

island＝三角形經「共用頂點 index」或「UV 座標相同（量化到 1/65536）」連起來的連通塊——接縫上頂點被拆開
但 UV 重合的，算同一塊；鏡像而 UV 疊在一起、但不相連的，算兩塊（重疊另報）。編號照閱讀順序（包圍盒由上而下、
由左而右）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

QUANT = 65536


@dataclass
class Island:
    index: int
    tris: list                       # 三角形 index（指 Shape.tris）
    uv_area: float = 0.0             # UV 空間面積（包回後；重疊算兩次）
    bbox_uv: tuple = (0.0, 0.0, 0.0, 0.0)   # (u0, v0, u1, v1)，包回前的原值
    centroid_uv: tuple = (0.0, 0.0)  # 包回 [0,1) 後


@dataclass
class Shape:
    key: str                         # 檔名安全、全域唯一：<nif 檔名>.<shape 名>
    nif: str
    name: str
    kind: str
    uvs: list
    tris: list
    textures: list = field(default_factory=list)
    islands: list = field(default_factory=list)
    out_of_range_tris: int = 0       # 有頂點 UV 落在 [0,1] 外的三角形數


def _tri_area(a, b, c) -> float:
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2


def find_islands(uvs, tris) -> list[Island]:
    parent = list(range(len(tris)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    first_by_vert: dict[int, int] = {}
    first_by_uv: dict[tuple, int] = {}
    for ti, t in enumerate(tris):
        for vi in t:
            j = first_by_vert.setdefault(vi, ti)
            union(j, ti)
            u, v = uvs[vi]
            q = (round(u * QUANT), round(v * QUANT))
            j = first_by_uv.setdefault(q, ti)
            union(j, ti)
    groups: dict[int, list] = {}
    for ti in range(len(tris)):
        groups.setdefault(find(ti), []).append(ti)
    out = []
    for members in groups.values():
        pts = [uvs[vi] for ti in members for vi in tris[ti]]
        area = sum(_tri_area(*(uvs[vi] for vi in tris[ti])) for ti in members)
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        cu = sum(us) / len(us)
        cv = sum(vs) / len(vs)
        out.append(Island(0, members, area, (min(us), min(vs), max(us), max(vs)),
                          (cu - math.floor(cu), cv - math.floor(cv))))
    # 閱讀順序：包圍盒上緣（v）再左緣（u），量化到 1/100 免得 half float 誤差換位；全同再照最小三角形 index
    out.sort(key=lambda i: (round(i.bbox_uv[1] * 100), round(i.bbox_uv[0] * 100), min(i.tris)))
    for k, isl in enumerate(out):
        isl.index = k
    return out


def _copies(tri_uv):
    """一個三角形包回 [0,1) 後要畫的各份（UV 座標）。"""
    du = math.floor(min(p[0] for p in tri_uv))
    dv = math.floor(min(p[1] for p in tri_uv))
    base = [(u - du, v - dv) for u, v in tri_uv]
    shifts = [(0, 0)]
    if max(p[0] for p in base) > 1:
        shifts.append((-1, 0))
    if max(p[1] for p in base) > 1:
        shifts.append((0, -1))
    if len(shifts) == 3:
        shifts.append((-1, -1))
    return [[(u + su, v + sv) for u, v in base] for su, sv in shifts]


def tri_pixels(tri_uv, size):
    w, h = size
    return [[(u * w, v * h) for u, v in c] for c in _copies(tri_uv)]


def rasterize(shape: Shape, tri_indices, size):
    """選定三角形的遮罩（'L'，255＝選中）。邊上的像素也算（polygon 加 outline），細三角形至少一像素。"""
    from PIL import Image, ImageDraw
    im = Image.new("L", size, 0)
    d = ImageDraw.Draw(im)
    for ti in tri_indices:
        tri_uv = [shape.uvs[vi] for vi in shape.tris[ti]]
        for poly in tri_pixels(tri_uv, size):
            d.polygon(poly, fill=255, outline=255)
    return im


def mask_stats(mask) -> dict:
    """遮罩的像素數、占全圖比例、像素包圍盒 [x0, y0, x1, y1)（空遮罩為 None）。"""
    w, h = mask.size
    n = mask.histogram()[255]
    bb = mask.getbbox()
    return {"pixels": n, "area_frac": round(n / (w * h), 6), "bbox_px": list(bb) if bb else None}


def overlap_pixels(a, b) -> int:
    from PIL import ImageChops
    if a.getbbox() is None or b.getbbox() is None:
        return 0
    return ImageChops.multiply(a, b).histogram()[255]


def out_of_range(shape: Shape) -> int:
    return sum(1 for t in shape.tris
               if any(not (0.0 <= shape.uvs[vi][0] <= 1.0 and 0.0 <= shape.uvs[vi][1] <= 1.0) for vi in t))


# ---------------------------------------------------------------- 疊圖

PALETTE = [(255, 64, 64), (64, 200, 255), (255, 210, 0), (80, 230, 80), (255, 80, 255), (255, 140, 0),
           (150, 110, 255), (0, 220, 180), (255, 255, 255), (200, 80, 120)]


def color_for(i: int):
    return PALETTE[i % len(PALETTE)]


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # Pillow < 10.1
        return ImageFont.load_default()


def _base_for_overlay(tex):
    """貼圖壓暗一半做底，線框才看得清楚；alpha 丟掉（疊圖只給人／agent 看）。"""
    from PIL import Image
    rgb = tex.convert("RGB")
    return Image.blend(rgb, Image.new("RGB", rgb.size, (0, 0, 0)), 0.5)


def draw_wire(draw, shape: Shape, size, color, tri_indices=None, width=1):
    for ti in (range(len(shape.tris)) if tri_indices is None else tri_indices):
        tri_uv = [shape.uvs[vi] for vi in shape.tris[ti]]
        for poly in tri_pixels(tri_uv, size):
            draw.line(poly + [poly[0]], fill=color, width=width)


def view_scale(size, lo: int = 512, hi: int = 2048) -> float:
    """疊圖的縮放：最長邊小於 lo 放大到 lo（整數倍），大於 hi 縮到 hi；遮罩一律原尺寸。"""
    m = max(size)
    if m < lo:
        return float(max(1, lo // m))
    if m > hi:
        return hi / m
    return 1.0


def overlay(tex, entries, title: str, labels: bool = False, scale: float = 1.0):
    """entries：[(Shape, color, legend 文字)]。回 RGB 圖：上方貼圖＋線框（labels 時每個 island 中心寫編號），
    下方圖例。圖例只用 ASCII（預設字型沒有中日文字形），shape 原名在 layout.json。
    scale≠1 時貼圖先縮放（放大用最近鄰）；像素座標＝原座標×scale。"""
    from PIL import Image, ImageDraw
    if scale != 1.0:
        tex = tex.resize((max(1, round(tex.size[0] * scale)), max(1, round(tex.size[1] * scale))),
                         Image.NEAREST if scale > 1 else Image.BOX)
    w, h = tex.size
    fs = max(10, min(w, h) // 40)
    font = _font(fs)
    line_h = fs + 6
    legend_h = line_h * (len(entries) + 1) + 6
    out = Image.new("RGB", (w, h + legend_h), (24, 24, 24))
    out.paste(_base_for_overlay(tex), (0, 0))
    d = ImageDraw.Draw(out)
    lw = max(1, round(min(w, h) / 256))
    for shape, color, _text in entries:
        draw_wire(d, shape, (w, h), color, width=lw)
    if labels:
        for shape, color, _text in entries:
            for isl in shape.islands:
                cx, cy = isl.centroid_uv[0] * w, isl.centroid_uv[1] * h
                txt = str(isl.index)
                box = d.textbbox((cx, cy), txt, font=font, anchor="mm")
                d.rectangle([box[0] - 2, box[1] - 2, box[2] + 2, box[3] + 2], fill=(0, 0, 0))
                d.text((cx, cy), txt, fill=color, font=font, anchor="mm")
    y = h + 4
    d.text((6, y), _ascii(title), fill=(230, 230, 230), font=font)
    for shape, color, text in entries:
        y += line_h
        d.rectangle([6, y + 2, 6 + fs, y + 2 + fs - 2], fill=color)
        d.text((12 + fs, y), _ascii(text), fill=(230, 230, 230), font=font)
    return out


def _ascii(s: str) -> str:
    return s.encode("ascii", "backslashreplace").decode("ascii")
