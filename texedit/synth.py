"""合成樣本（測試與 `demo` 子命令用）：BSFadeNode＋BSTriShape＋BSLightingShaderProperty＋BSShaderTextureSet 的 NIF，
與 RGBA8 DDS。NIF 版面同 outfit／head_render 測試（BS 100、vertex desc＝位置 3f＋W＋UV 2×half）。

「洋裝」：貼圖 128×128。
- Top：兩個 island（正面 u 0.05–0.45、v 0.05–0.45；背面 u 0.55–0.95、同 v），各是 2×2 格、頂點共用。
- Skirt：一個 island（u 0.05–0.95、v 0.55–0.95），左右兩半是**分開的頂點**但 UV 接縫重合 → 照 UV 焊成同一塊。
- Ribbon：一個三角形，蓋到 Top 正面的一角（共用 texel，layout 要報重疊）；另一個三角形 UV 在 u 1.1–1.3（越界，包回 0.1–0.3）。
"""
from __future__ import annotations

import struct
from pathlib import Path

from ai3dlib.nif import Nif

IDENT9 = (1, 0, 0, 0, 1, 0, 0, 0, 1)
DRESS_D, DRESS_N = "textures\\texedit\\dress_d.dds", "textures\\texedit\\dress_n.dds"
SIZE = 128


def _av(name_idx: int) -> bytes:
    return (struct.pack("<IIiI", name_idx, 0, -1, 14) + struct.pack("<3f", 0, 0, 0)
            + struct.pack("<9f", *IDENT9) + struct.pack("<f", 1.0) + struct.pack("<i", -1))


def _shape(name_idx: int, verts, uvs, tris, shader: int) -> bytes:
    vsize = 20
    desc = (vsize // 4) | ((16 // 4) << 8) | ((0x1 | 0x2 | 0x400) << 44)
    body = bytearray()
    for (x, y, z), (u, v) in zip(verts, uvs):
        body += struct.pack("<3fI", x, y, z, 0) + struct.pack("<ee", u, v)
    for t in tris:
        body += struct.pack("<3H", *t)
    return (_av(name_idx) + struct.pack("<4f", 0, 0, 0, 1) + struct.pack("<3i", -1, shader, -1)
            + struct.pack("<Q", desc) + struct.pack("<HHI", len(tris), len(verts), vsize * len(verts))
            + bytes(body) + struct.pack("<I", 0))


def _lighting(texset: int) -> bytes:
    return (struct.pack("<IIIi", 1, 0xFFFFFFFF, 0, -1) + struct.pack("<2I", 0, 0)
            + struct.pack("<4f", 0, 0, 1, 1) + struct.pack("<i", texset) + b"\0" * 8)


def _texset(slots) -> bytes:
    b = struct.pack("<I", len(slots))
    for s in slots:
        raw = s.encode("ascii")
        b += struct.pack("<I", len(raw)) + raw
    return b


def make_nif(shapes) -> bytes:
    """shapes：[dict(name, uvs, tris, textures, verts=None)] → NIF bytes。verts 省略時用 (u, 0, -v)。"""
    strings = [b"root"] + [s["name"].encode() for s in shapes]
    types = [b"BSFadeNode", b"BSTriShape", b"BSLightingShaderProperty", b"BSShaderTextureSet"]
    blocks, tidx, kids = [None], [0], []
    for i, s in enumerate(shapes):
        si = len(blocks)
        kids.append(si)
        blocks.append(None)
        tidx.append(1)
        shader = len(blocks)
        blocks.append(_lighting(shader + 1))
        tidx.append(2)
        blocks.append(_texset(s["textures"]))
        tidx.append(3)
        verts = s.get("verts") or [(u, 0.0, -v) for u, v in s["uvs"]]
        blocks[si] = _shape(i + 1, verts, s["uvs"], s["tris"], shader)
    blocks[0] = _av(0) + struct.pack("<I", len(kids)) + struct.pack(f"<{len(kids)}i", *kids) + struct.pack("<I", 0)
    n = Nif(b"Gamebryo File Format, Version 20.2.0.7\n", 0x14020007, 1, 12, 100,
            [b"t\0", b"\0", b"\0"], types, tidx, strings, max(len(s) for s in strings), [], blocks, [0])
    return n.serialize()


def grid(u0, v0, u1, v1, nu=2, nv=2, base=0):
    """(u0,v0)–(u1,v1) 的 nu×nv 格，頂點共用；回 (uvs, tris)，tris 的 index 從 base 起。"""
    uvs, tris = [], []
    for j in range(nv + 1):
        for i in range(nu + 1):
            uvs.append((u0 + (u1 - u0) * i / nu, v0 + (v1 - v0) * j / nv))
    for j in range(nv):
        for i in range(nu):
            a = base + j * (nu + 1) + i
            b, c, d = a + 1, a + nu + 1, a + nu + 2
            tris += [(a, b, d), (a, d, c)]
    return uvs, tris


def dress_shapes(textures=None):
    tex = textures or [DRESS_D, DRESS_N, "", "", "", "", "", "", ""]
    f_uv, f_t = grid(0.05, 0.05, 0.45, 0.45)
    b_uv, b_t = grid(0.55, 0.05, 0.95, 0.45, base=len(f_uv))
    l_uv, l_t = grid(0.05, 0.55, 0.50, 0.95, nu=1, nv=2)
    r_uv, r_t = grid(0.50, 0.55, 0.95, 0.95, nu=1, nv=2, base=len(l_uv))
    rib_uv = [(0.30, 0.30), (0.52, 0.30), (0.30, 0.52), (1.10, 0.60), (1.30, 0.60), (1.10, 0.80)]
    return [
        {"name": "Top", "uvs": f_uv + b_uv, "tris": f_t + b_t, "textures": tex},
        {"name": "Skirt", "uvs": l_uv + r_uv, "tris": l_t + r_t, "textures": tex},
        {"name": "Ribbon", "uvs": rib_uv, "tris": [(0, 1, 2), (3, 4, 5)], "textures": tex},
    ]


def dress_nif() -> bytes:
    return make_nif(dress_shapes())


def diffuse_image(size: int = SIZE):
    """決定性的漫反射：灰階明暗格紋（讓「保留明暗換色」看得出差別）＋alpha 由左到右 255→128。"""
    from PIL import Image
    im = Image.new("RGBA", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            shade = 90 + ((x // 8 + y // 8) % 2) * 80 + (x * 40) // size
            px[x, y] = (shade, shade, min(255, shade + 20), 255 - (x * 127) // max(1, size - 1))
    return im


def normal_image(size: int = SIZE):
    from PIL import Image
    return Image.new("RGBA", (size, size), (128, 128, 255, 200))


def dds_bytes(im) -> bytes:
    from ai3dlib.dds import write_rgba
    return write_rgba(im, mips=True)
