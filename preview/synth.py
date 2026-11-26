"""合成樣本（測試與 `demo` 用）：一件「洋裝」NIF＋一個「身體」NIF＋RGBA8 DDS，全部程式生成，不含任何遊戲素材。

NIF 版面同 texedit／head_render 測試（BS 100、BSTriShape vertex desc＝位置 3f＋W＋UV 2×half），另加 NiAlphaProperty。
座標同 Skyrim：x＝角色右、y＝角色前方、z＝上。圓筒的 u＝0 在正前方（+y），u＝0.5 在正後方。

- `dress.nif`：Top（圓筒 r 11、z 62–92，UV v 0.02–0.46）、Skirt（圓錐 r 12→20、z 60→30，v 0.54–0.98），
  兩者用 `dress_d`；Lace（裙襬外的一圈，alpha test，`lace_d` 是 alpha 0／255 的直條紋）；
  Sash（腰帶，alpha blend，`sash_d` 半透明紅）。
- `body.nif`：Body（圓筒 r 10、z 0–110，`body_d` 膚色）。
- `dress_d`：上半（Top）正面淺灰格、背面深藍格；下半（Skirt）粉紅格。
"""
from __future__ import annotations

import math
import struct

from ai3dlib.nif import Nif

IDENT9 = (1, 0, 0, 0, 1, 0, 0, 0, 1)
SIZE = 128
TEX = {k: f"textures\\preview\\{k}.dds" for k in ("dress_d", "lace_d", "sash_d", "body_d")}
ALPHA_TEST, ALPHA_BLEND = 0x2EC, 0x0ED      # 0x200＝test、0x1＝blend；其餘是混合函數位，渲染不看
FRONT_RGB, BACK_RGB, SKIRT_RGB, SASH_RGB, BODY_RGB = (200, 200, 200), (60, 70, 140), (220, 120, 160), \
    (200, 30, 40), (225, 180, 160)


def _av(name_idx: int) -> bytes:
    return (struct.pack("<IIiI", name_idx, 0, -1, 14) + struct.pack("<3f", 0, 0, 0)
            + struct.pack("<9f", *IDENT9) + struct.pack("<f", 1.0) + struct.pack("<i", -1))


def _shape(name_idx: int, verts, uvs, tris, shader: int, alpha: int) -> bytes:
    vsize = 20
    desc = (vsize // 4) | ((16 // 4) << 8) | ((0x1 | 0x2 | 0x400) << 44)
    body = bytearray()
    for (x, y, z), (u, v) in zip(verts, uvs):
        body += struct.pack("<3fI", x, y, z, 0) + struct.pack("<ee", u, v)
    for t in tris:
        body += struct.pack("<3H", *t)
    return (_av(name_idx) + struct.pack("<4f", 0, 0, 0, 1) + struct.pack("<3i", -1, shader, alpha)
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


def _alpha(flags: int, threshold: int) -> bytes:
    return struct.pack("<IIi", 0xFFFFFFFF, 0, -1) + struct.pack("<HB", flags, threshold)


def make_nif(shapes) -> bytes:
    """shapes：[dict(name, verts, uvs, tris, diffuse, alpha=None|(flags, threshold))] → NIF bytes。"""
    strings = [b"root"] + [s["name"].encode() for s in shapes]
    types = [b"BSFadeNode", b"BSTriShape", b"BSLightingShaderProperty", b"BSShaderTextureSet", b"NiAlphaProperty"]
    blocks, tidx, kids = [None], [0], []
    for i, s in enumerate(shapes):
        si = len(blocks)
        kids.append(si)
        blocks.append(None)
        tidx.append(1)
        shader = len(blocks)
        blocks.append(_lighting(shader + 1))
        tidx.append(2)
        blocks.append(_texset([s["diffuse"], "", "", "", "", "", "", "", ""]))
        tidx.append(3)
        alpha = -1
        if s.get("alpha"):
            alpha = len(blocks)
            blocks.append(_alpha(*s["alpha"]))
            tidx.append(4)
        blocks[si] = _shape(i + 1, s["verts"], s["uvs"], s["tris"], shader, alpha)
    blocks[0] = _av(0) + struct.pack("<I", len(kids)) + struct.pack(f"<{len(kids)}i", *kids) + struct.pack("<I", 0)
    n = Nif(b"Gamebryo File Format, Version 20.2.0.7\n", 0x14020007, 1, 12, 100,
            [b"t\0", b"\0", b"\0"], types, tidx, strings, max(len(s) for s in strings), [], blocks, [0])
    return n.serialize()


def tube(r0, r1, z0, z1, v0, v1, seg=24, rings=2):
    """圓筒／圓錐：頂點 (r sin 2πu, r cos 2πu, z)，u 0→1 繞一圈（接縫多一欄頂點，UV 才連續）。"""
    verts, uvs, tris = [], [], []
    for j in range(rings + 1):
        t = j / rings
        r, z, v = r0 + (r1 - r0) * t, z0 + (z1 - z0) * t, v0 + (v1 - v0) * t
        for i in range(seg + 1):
            u = i / seg
            a = 2 * math.pi * u
            verts.append((r * math.sin(a), r * math.cos(a), z))
            uvs.append((u, v))
    for j in range(rings):
        for i in range(seg):
            a = j * (seg + 1) + i
            b, c, d = a + 1, a + seg + 1, a + seg + 2
            tris += [(a, b, d), (a, d, c)]
    return verts, uvs, tris


def dress_shapes():
    out = []
    for name, args, diffuse, alpha in (
            ("Top", (11, 11, 92, 62, 0.02, 0.46), "dress_d", None),
            ("Skirt", (12, 20, 60, 30, 0.54, 0.98), "dress_d", None),
            ("Lace", (20.5, 21.5, 30, 24, 0.0, 1.0), "lace_d", (ALPHA_TEST, 128)),
            ("Sash", (12.3, 12.3, 64, 59, 0.0, 1.0), "sash_d", (ALPHA_BLEND, 128))):
        v, uv, t = tube(*args)
        out.append({"name": name, "verts": v, "uvs": uv, "tris": t, "diffuse": TEX[diffuse], "alpha": alpha})
    return out


def body_shapes():
    v, uv, t = tube(10, 10, 110, 0, 0.0, 1.0, rings=4)
    return [{"name": "Body", "verts": v, "uvs": uv, "tris": t, "diffuse": TEX["body_d"]}]


def dress_nif() -> bytes:
    return make_nif(dress_shapes())


def body_nif() -> bytes:
    return make_nif(body_shapes())


def _checker(px, x, y, rgb, a=255):
    k = 0.8 if (x // 8 + y // 8) % 2 else 1.0
    px[x, y] = (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k), a)


def dress_image(size: int = SIZE):
    from PIL import Image
    im = Image.new("RGBA", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            u = (x + 0.5) / size
            if y < size // 2:
                _checker(px, x, y, FRONT_RGB if (u < 0.25 or u >= 0.75) else BACK_RGB)
            else:
                _checker(px, x, y, SKIRT_RGB)
    return im


def lace_image(size: int = SIZE):
    from PIL import Image
    im = Image.new("RGBA", (size, size))
    px = im.load()
    for y in range(size):
        for x in range(size):
            px[x, y] = (240, 240, 240, 255 if (x // 4) % 2 == 0 else 0)
    return im


def sash_image(size: int = SIZE):
    from PIL import Image
    return Image.new("RGBA", (size, size), SASH_RGB + (120,))


def body_image(size: int = SIZE):
    from PIL import Image
    return Image.new("RGBA", (size, size), BODY_RGB + (255,))


def dds_bytes(im) -> bytes:
    from ai3dlib.dds import write_rgba
    return write_rgba(im, mips=True)


def write_inputs(write, prefix: str = "") -> dict:
    """用呼叫端給的 write(rel, bytes) 寫一整組輸入；回 {名: 相對路徑}。"""
    files = {"dress.nif": dress_nif(), "body.nif": body_nif(),
             "dress_d.dds": dds_bytes(dress_image()), "lace_d.dds": dds_bytes(lace_image()),
             "sash_d.dds": dds_bytes(sash_image()), "body_d.dds": dds_bytes(body_image())}
    out = {}
    for k, b in files.items():
        write(prefix + k, b)
        out[k] = prefix + k
    return out
