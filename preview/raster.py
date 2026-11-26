# 改寫自 agentctl `tools/head_render/raster.py` @ `111f240f`，手動同步。與原版的差異：
# alpha test＋blend 可並用（先 test 再 blend）、blend 三角形每個視角由遠到近排序、背景色可給、
# 另回每個 layer 實際畫到的像素數（`coverage`），其餘（投影、邊函數、Gouraud Lambert）照原版。
"""純 Python 軟體光柵化：正交投影＋深度緩衝＋Gouraud（Lambert）＋可選貼圖。

沒有 numpy；內層迴圈只做加法與比較。輸出 RGB bytearray，由呼叫端交給 Pillow 寫 PNG。
完全決定性：同輸入同 bytes（不用亂數、不用時間、遍歷順序固定；排序用穩定排序加三角形序號）。

座標：Skyrim 模型空間 x＝角色右、y＝角色前方、z＝上。視角 yaw 0＝正面（相機在 +y 看 -y），
180＝背面，90＝從角色右側看，-90＝左側。畫面 u＝−x'（角色右手在畫面左），v＝−z。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

VIEWS = {"front": 0.0, "back": 180.0, "side": 90.0, "lside": -90.0, "q45": 45.0, "lq45": -45.0, "bq45": 135.0}

# 沒有貼圖時每個 shape 的平塗色：依 shape 在輸入中的序號循環
PALETTE = [(205, 190, 180), (120, 160, 210), (220, 140, 140), (130, 190, 140),
           (200, 180, 110), (170, 140, 200), (110, 190, 190), (190, 150, 120)]
BG = (40, 40, 46)
# blend 模式下 alpha ≥ 這個值的片段當不透明畫（寫深度），其餘半透明疊色、不寫深度。
BLEND_SOLID = 230
LIGHT = (0.35, 0.85, 0.40)     # 相機座標（x' 右、y' 朝相機、z 上）：右上前方
AMBIENT = 0.30
FILL = 0.92


@dataclass
class Frame:
    center: tuple      # 模型空間
    radius: float


@dataclass
class Layer:
    """一個要畫的 shape。sampler(u, v) -> (r, g, b, a) 0–255；None 表示用 color 平塗。

    mode：opaque（不看 alpha）｜test（alpha < threshold 丟掉）｜blend（半透明疊色；test=True 時先做 alpha test）。"""
    positions: list
    triangles: list
    color: tuple = (200, 200, 200)
    uvs: list | None = None
    sampler: object = None
    mode: str = "opaque"
    threshold: int = 128
    test: bool = False


def vertex_normals(positions: list, triangles: list) -> list:
    acc = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c in triangles:
        pa, pb, pc = positions[a], positions[b], positions[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for k in (a, b, c):
            n = acc[k]
            n[0] += nx
            n[1] += ny
            n[2] += nz
    out = []
    for n in acc:
        ln = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
        out.append((n[0] / ln, n[1] / ln, n[2] / ln))
    return out


def project(point, yaw_deg: float, size: int, frame: Frame, fill: float = FILL) -> tuple[float, float]:
    """模型空間的點 → 畫面像素座標（與 render 同一套投影；測試與標註用）。"""
    cs, sn = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    k = (size / 2) * fill / frame.radius
    cx, cy, cz = frame.center
    x, y, z = point
    return size / 2 - ((x * cs - y * sn) - (cx * cs - cy * sn)) * k, size / 2 - (z - cz) * k


def render(layers: list[Layer], yaw_deg: float, size: int, frame: Frame,
           light=LIGHT, ambient: float = AMBIENT, bg=BG, fill: float = FILL):
    """回 (RGB bytearray, 每個 layer 畫到的像素數 list)。像素數＝最後畫面上屬於該 layer 的不透明像素，
    加上它疊過色的半透明像素（同一像素可能同時算進兩個 layer）。"""
    W = H = size
    cs, sn = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    ln = math.sqrt(sum(c * c for c in light))
    lx, ly, lz = (c / ln for c in light)
    k = (size / 2) * fill / frame.radius
    cx, cy, cz = frame.center
    rcx = cx * cs - cy * sn
    img = bytearray(bytes(bg) * (W * H))
    zbuf = [-1e30] * (W * H)
    owner = [-1] * (W * H)
    blended = [0] * len(layers)
    order = [i for i, L in enumerate(layers) if L.mode != "blend"] + \
            [i for i, L in enumerate(layers) if L.mode == "blend"]
    for li in order:
        L = layers[li]
        P = L.positions
        normals = vertex_normals(P, L.triangles)
        sx, sy, sz, inten = [], [], [], []
        for (x, y, z), (nx, ny, nz) in zip(P, normals):
            xr = x * cs - y * sn
            yr = x * sn + y * cs
            sx.append(W / 2 - (xr - rcx) * k)
            sy.append(H / 2 - (z - cz) * k)
            sz.append(yr)
            nxr = nx * cs - ny * sn
            nyr = nx * sn + ny * cs
            d = abs(nxr * lx + nyr * ly + nz * lz)        # 雙面打光：不分正反面
            inten.append(ambient + (1.0 - ambient) * d)
        tris = L.triangles
        if L.mode == "blend":                              # 由遠到近（sz 大＝近）
            tris = [t for _k, t in sorted(((sz[a] + sz[b] + sz[c], n), (a, b, c))
                                          for n, (a, b, c) in enumerate(tris))]
        blended[li] = _raster_layer(L, li, tris, sx, sy, sz, inten, img, zbuf, owner, W, H)
    cover = [0] * len(layers)
    for o in owner:
        if o >= 0:
            cover[o] += 1
    return img, [c + b for c, b in zip(cover, blended)]


def _raster_layer(L: Layer, li: int, tris, sx, sy, sz, inten, img, zbuf, owner, W, H) -> int:
    textured = L.sampler is not None and L.uvs is not None
    uvs = L.uvs
    sampler = L.sampler
    blend = L.mode == "blend"
    test = L.mode == "test" or (blend and L.test)
    thr = L.threshold
    base = L.color
    nblend = 0
    for a, b, c in tris:
        x0, y0, x1, y1, x2, y2 = sx[a], sy[a], sx[b], sy[b], sx[c], sy[c]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if area == 0.0:
            continue
        if area < 0:
            b, c = c, b
            x1, y1, x2, y2 = x2, y2, x1, y1
            area = -area
        inv = 1.0 / area
        minx = max(0, int(math.floor(min(x0, x1, x2))))
        maxx = min(W - 1, int(math.ceil(max(x0, x1, x2))))
        miny = max(0, int(math.floor(min(y0, y1, y2))))
        maxy = min(H - 1, int(math.ceil(max(y0, y1, y2))))
        if minx > maxx or miny > maxy:
            continue
        # 邊函數 w_i(px,py) = A_i*px + B_i*py + C_i，對頂點 i 的對邊
        A0, B0 = (y1 - y2), (x2 - x1)
        A1, B1 = (y2 - y0), (x0 - x2)
        A2, B2 = (y0 - y1), (x1 - x0)
        C0 = x1 * y2 - x2 * y1
        C1 = x2 * y0 - x0 * y2
        C2 = x0 * y1 - x1 * y0
        za, zb, zc = sz[a], sz[b], sz[c]
        ia, ib, ic = inten[a], inten[b], inten[c]
        if textured:
            ua, va = uvs[a]
            ub, vb = uvs[b]
            uc, vc = uvs[c]
        for py in range(miny, maxy + 1):
            fy = py + 0.5
            fx = minx + 0.5
            w0 = A0 * fx + B0 * fy + C0
            w1 = A1 * fx + B1 * fy + C1
            w2 = A2 * fx + B2 * fy + C2
            row = py * W
            for px in range(minx, maxx + 1):
                if w0 >= 0.0 and w1 >= 0.0 and w2 >= 0.0:
                    l0, l1, l2 = w0 * inv, w1 * inv, w2 * inv
                    z = l0 * za + l1 * zb + l2 * zc
                    idx = row + px
                    if z > zbuf[idx]:
                        s = l0 * ia + l1 * ib + l2 * ic
                        if textured:
                            r, g, bl, al = sampler(l0 * ua + l1 * ub + l2 * uc, l0 * va + l1 * vb + l2 * vc)
                        else:
                            r, g, bl = base
                            al = 128 if blend else 255     # 沒貼圖的 blend shape 畫成半透明
                        if (test and al < thr) or (blend and al < 4):
                            pass
                        else:
                            r = r * s
                            g = g * s
                            bl = bl * s
                            o = idx * 3
                            if blend and al < BLEND_SOLID:
                                f = al / 255.0
                                img[o] = min(255, int(img[o] * (1 - f) + r * f))
                                img[o + 1] = min(255, int(img[o + 1] * (1 - f) + g * f))
                                img[o + 2] = min(255, int(img[o + 2] * (1 - f) + bl * f))
                                nblend += 1
                            else:
                                img[o] = min(255, int(r))
                                img[o + 1] = min(255, int(g))
                                img[o + 2] = min(255, int(bl))
                                zbuf[idx] = z
                                owner[idx] = li
                w0 += A0
                w1 += A1
                w2 += A2
    return nblend
