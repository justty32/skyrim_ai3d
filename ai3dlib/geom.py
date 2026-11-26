# 複製自 agentctl `tools/head_render/geom.py` @ `0e29b583`，手動同步；只改 import（`npc_face.nifio` 是 `lib.nif` 的別名，這裡直接用 `ai3dlib.nif`），去掉插 sys.path。
"""從 Skyrim SE NIF 抽出可渲染的三角網格（唯讀）。

建在 `npc_face/nifio.Nif`（header／block 表）之上，這裡只多解幾何相關 block。版面依 nif.xml（BS 100），
並以 Chloe FaceGen 頭 `facegeom/KuroneSoulTomb.esp/00000A0B.nif` 實測對過：

- BSTriShape 家族：NiAVObject（name、extra refs、controller、flags u32、translation 3f、rotation 9f、scale f、
  collision ref）＋bounding sphere 4f＋skin／shader／alpha ref＋vertex desc u64＋num triangles u16＋
  num vertices u16＋data size u32＋[vertex data＋triangles（u16×3）若 data size>0]＋particle data size u32
  （SSE；>0 時本工具拒絕）。BSDynamicTriShape 再接 dynamic data size u32＋Vector4×N（頂點位置，FaceGen 頭的
  真位置在這裡；其 vertex data 本身沒有位置）。
- vertex desc：低 4 bit＝每頂點 bytes/4；bit 8–11 UV offset、16–19 normal offset、28–31 skin offset（皆 ×4）；
  bit 44 起是屬性旗標（VF_VERTEX=1、UV=2、NORMAL=8、TANGENT=0x10、SKINNED=0x40、EYE=0x100、FULL_PREC=0x400）。
  SSE 的位置一律 3×float。
- 有 skin 的 BSTriShape 在 SSE 把 vertex data／triangles 放在 NiSkinPartition：num partitions u32、data size u32、
  vertex size u32、vertex desc u64、vertex data，接各 partition（num verts／tris／bones／strips／weights per
  vertex 各 u16、bones u16×、has vertex map u8＋map u16×、has weights u8＋float×、strip lengths u16×、
  has faces u8＋triangles u16×3（strips=0 時）、has bone indices u8＋u8×、LOD level u8＋global VB u8、
  vertex desc u64、triangles copy u16×3）。triangles copy＝經 vertex map 換成全域 index 的三角形，本工具用它。
- NiSkinInstance：data ref、partition ref、skeleton root ptr、num bones＋bone ptrs（BSDismember 再接 partitions）。
  NiSkinData：overall transform（rot 9f、trans 3f、scale f）、num bones u32、has weights u8，每骨
  transform＋bounding sphere 4f＋num vertices u16＋(u16 index, f weight)×。
- NiTriShape＋NiTriShapeData（舊式幾何）：見 `_tri_shape_data`。

座標：Skyrim 模型空間 x＝角色右、y＝角色前方、z＝上。蒙皮頂點以「骨節點世界矩陣 × 該骨 skin 矩陣 × overall」換到
模型空間，取權重最多的那根骨（FaceGen／頭部件在綁定姿勢下各骨結果相同，實測頭的 Spine2 與 Head 兩骨一致）。
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ai3dlib.nif import NODE_TYPES, Nif, NifError, TRISHAPE_TYPES

VF_VERTEX, VF_UV, VF_NORMAL, VF_SKINNED = 0x1, 0x2, 0x8, 0x40

# 3×3 矩陣以 row-major tuple(9) 表示，作用於行向量。NIF 檔內 9 個 float 照檔案順序直接當 row-major 用：
# 以 Chloe 頭實測，這樣頭（模型空間）經 Head 骨 skin 矩陣後中心 x≈0，與存在骨空間、左右對稱的眼睛一致；
# 轉置則差 0.3 單位（眼睛偏一邊）。見 `_xf_from_nif`。
IDENT = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


@dataclass(frozen=True)
class Xf:
    rot: tuple = IDENT          # row-major，作用於行向量：v' = rot·v·scale + trans
    trans: tuple = (0.0, 0.0, 0.0)
    scale: float = 1.0

    def apply(self, v):
        r, s, t = self.rot, self.scale, self.trans
        x, y, z = v
        return (s * (r[0] * x + r[1] * y + r[2] * z) + t[0],
                s * (r[3] * x + r[4] * y + r[5] * z) + t[1],
                s * (r[6] * x + r[7] * y + r[8] * z) + t[2])

    def inverse(self) -> "Xf":
        r, s = self.rot, self.scale
        rt = (r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8])
        inv = Xf(rt, (0.0, 0.0, 0.0), 1.0 / s)
        t = inv.apply(self.trans)
        return Xf(rt, (-t[0], -t[1], -t[2]), 1.0 / s)

    def __matmul__(self, o: "Xf") -> "Xf":
        """(self @ o).apply(v) == self.apply(o.apply(v))"""
        a, b = self.rot, o.rot
        rot = tuple(sum(a[3 * i + k] * b[3 * k + j] for k in range(3)) for i in range(3) for j in range(3))
        t = self.apply(o.trans)
        return Xf(rot, t, self.scale * o.scale)


def _xf_from_nif(rot9, trans, scale) -> Xf:
    return Xf(tuple(rot9), tuple(trans), scale)


@dataclass
class Mesh:
    name: str
    kind: str
    positions: list            # [(x, y, z)] 模型空間
    triangles: list            # [(a, b, c)]
    uvs: list | None = None    # [(u, v)]，與 positions 對齊
    textures: list = field(default_factory=list)   # BSShaderTextureSet 各 slot
    alpha_flags: int | None = None
    alpha_threshold: int = 128
    shader_flags2: int = 0
    shader_type: int | None = None     # BSLightingShaderProperty：4 FaceGen、5 SkinTint、6 HairTint
    source: str = ""
    skin_bone: str | None = None       # 蒙皮：權重最多的骨名
    skin_xf: Xf | None = None          # 該骨 skin 矩陣 @ overall（頂點→骨空間）
    dropped_triangles: int = 0         # 頂點索引越界而丟掉的三角形數（見 _finish）

    @property
    def blend(self) -> bool:
        return bool(self.alpha_flags is not None and self.alpha_flags & 0x1)

    @property
    def alpha_test(self) -> bool:
        return bool(self.alpha_flags is not None and self.alpha_flags & 0x200)


class _R:
    def __init__(self, b: bytes, pos: int = 0):
        self.b, self.pos = b, pos

    def f(self, fmt: str):
        v = struct.unpack_from("<" + fmt, self.b, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def skip(self, n: int) -> None:
        self.pos += n


def _half(h: int) -> float:
    return struct.unpack("<e", struct.pack("<H", h))[0]


def _avobject(r: _R) -> dict:
    name = r.f("I")
    r.skip(4 * r.f("I"))           # extra data refs
    r.f("i")                       # controller
    flags = r.f("I")
    trans = r.f("3f")
    rot = r.f("9f")
    scale = r.f("f")
    r.f("i")                       # collision
    return dict(name=name, flags=flags, xf=_xf_from_nif(rot, trans, scale))


def _decode_vertices(raw: bytes, count: int, vsize: int, desc: int):
    flags = desc >> 44
    uv_off = ((desc >> 8) & 0xF) * 4
    pos, uvs = [], []
    for k in range(count):
        base = k * vsize
        if flags & VF_VERTEX:
            pos.append(struct.unpack_from("<3f", raw, base))
        if flags & VF_UV:
            hu, hv = struct.unpack_from("<HH", raw, base + uv_off)
            uvs.append((_half(hu), _half(hv)))
    return (pos if flags & VF_VERTEX else None), (uvs if flags & VF_UV else None)


def _skin_partition(b: bytes):
    """回 (global vertex data 解出的 positions|None, uvs|None, triangles)。"""
    r = _R(b)
    nparts = r.f("I")
    dsize, vsize, desc = r.f("I"), r.f("I"), r.f("Q")
    count = dsize // vsize if vsize else 0
    raw = b[r.pos:r.pos + dsize]
    r.skip(dsize)
    pos, uvs = _decode_vertices(raw, count, vsize, desc)
    tris = []
    for _ in range(nparts):
        nv, nt, nb, ns, nw = r.f("5H")
        r.skip(2 * nb)
        if r.f("B"):
            r.skip(2 * nv)
        if r.f("B"):
            r.skip(4 * nv * nw)
        strip_lens = [r.f("H") for _ in range(ns)]
        if r.f("B"):
            if ns:
                r.skip(2 * sum(strip_lens))
            else:
                r.skip(6 * nt)
        if r.f("B"):
            r.skip(nv * nw)
        r.skip(2)                  # LOD level、global VB
        r.f("Q")                   # partition vertex desc
        if ns:
            raise NifError("NiSkinPartition 用 strips，本工具未支援")
        for _t in range(nt):
            tris.append(r.f("3H"))
    if r.pos != len(b):
        raise NifError(f"NiSkinPartition 解析後剩 {len(b) - r.pos} bytes，版面不符")
    return pos, uvs, tris


def _skin_data(b: bytes):
    r = _R(b)
    overall = _xf_from_nif(r.f("9f"), r.f("3f"), r.f("f"))
    nb = r.f("I")
    has_w = r.f("B")
    bones = []
    for _ in range(nb):
        xf = _xf_from_nif(r.f("9f"), r.f("3f"), r.f("f"))
        r.skip(16)
        nv = r.f("H")
        if has_w:
            r.skip(6 * nv)
        bones.append((xf, nv))
    return overall, bones


class NifGeometry:
    def __init__(self, data: bytes, source: str = "", skinned_transform: str = "ignore"):
        if skinned_transform not in {"ignore", "apply"}:
            raise ValueError("skinned_transform must be ignore or apply")
        self.skinned_transform = skinned_transform
        self.nif = Nif.parse(data)
        self.source = source
        self._world = self._node_worlds()

    def _node_worlds(self) -> dict[int, Xf]:
        n = self.nif
        local, parent = {}, {}
        for i in range(len(n.blocks)):
            if n.block_type(i) in NODE_TYPES:
                local[i] = _avobject(_R(n.blocks[i]))["xf"]
                for c in n.node(i).children:
                    if c >= 0:
                        parent[c] = i
        self._parent = parent
        out = {}

        def world(i: int) -> Xf:
            if i not in out:
                p = parent.get(i)
                out[i] = (world(p) @ local[i]) if p is not None and p in local else local[i]
            return out[i]
        for i in local:
            world(i)
        return out

    def _parent_world(self, i: int) -> Xf:
        p = self._parent.get(i)
        return self._world.get(p, Xf()) if p is not None else Xf()

    def meshes(self) -> list[Mesh]:
        out = []
        for i, kind, name in self.nif.shapes():
            if kind in TRISHAPE_TYPES:
                m = self._trishape(i, kind, name or "")
            elif kind == "NiTriShape":
                m = self._nitrishape(i, name or "")
            else:
                continue
            if m is not None and m.triangles:
                out.append(m)
        return out

    # ---------- BSTriShape 家族 ----------
    def _trishape(self, i: int, kind: str, name: str) -> Mesh:
        b = self.nif.blocks[i]
        r = _R(b)
        av = _avobject(r)
        r.skip(16)
        skin, shader, alpha = r.f("3i")
        desc = r.f("Q")
        nt, nv, dsize = r.f("H"), r.f("H"), r.f("I")
        pos = uvs = None
        tris = []
        if dsize:
            vsize = (desc & 0xF) * 4
            raw = b[r.pos:r.pos + vsize * nv]
            r.skip(vsize * nv)
            pos, uvs = _decode_vertices(raw, nv, vsize, desc)
            tris = [r.f("3H") for _ in range(nt)]
        if r.f("I"):
            raise NifError(f"{name}: particle data 未支援")
        if kind == "BSDynamicTriShape":
            dn = r.f("I") // 16
            pos = [r.f("4f")[:3] for _ in range(dn)]
        xf = self._parent_world(i) @ av["xf"]
        if skin >= 0:
            sk = self._skin(skin)
            if sk["partition"] is not None:
                ppos, puvs, ptris = _skin_partition(self.nif.blocks[sk["partition"]])
                pos = pos if pos is not None else ppos
                uvs = uvs if uvs is not None else puvs
                tris = tris or ptris
        if pos is None:
            raise NifError(f"{name}: 找不到頂點位置")
        if skin >= 0 and self.skinned_transform == "apply":
            pos = [av["xf"].apply(p) for p in pos]
        m = self._finish(name, kind, pos if skin >= 0 else [xf.apply(p) for p in pos], tris, uvs, shader, alpha)
        if skin >= 0:
            m.skin_bone, m.skin_xf = sk["bone"], sk["xf"]
        return m

    def _skin(self, i: int) -> dict:
        n = self.nif
        r = _R(n.blocks[i])
        data, part = r.f("i"), r.f("i")
        r.f("i")
        bones = [r.f("i") for _ in range(r.f("I"))]
        xf = bone = None
        if data >= 0 and n.block_type(data) == "NiSkinData":
            overall, sb = _skin_data(n.blocks[data])
            if sb:
                k = max(range(len(sb)), key=lambda j: sb[j][1])
                node = bones[k] if k < len(bones) else -1
                bone = n.block_name(node) if 0 <= node < len(n.blocks) else f"#bone{k}"
                xf = sb[k][0] @ overall
        return dict(partition=part if part >= 0 else None, xf=xf, bone=bone)

    # ---------- NiTriShape ----------
    def _nitrishape(self, i: int, name: str) -> Mesh | None:
        n = self.nif
        r = _R(n.blocks[i])
        av = _avobject(r)
        data = r.f("i")
        skin = r.f("i")
        r.skip(4 * r.f("I"))       # material names
        r.skip(4 * r.f("I"))       # material extra
        r.f("i")                   # active material
        r.skip(1)                  # material needs update
        shader, alpha = r.f("2i")
        if data < 0 or n.block_type(data) != "NiTriShapeData":
            return None
        pos, uvs, tris = _tri_shape_data(n.blocks[data])
        xf = self._parent_world(i) @ av["xf"]
        if skin >= 0:
            sk = self._skin(skin)
            if self.skinned_transform == "apply":
                pos = [av["xf"].apply(p) for p in pos]
            m = self._finish(name, "NiTriShape", pos, tris, uvs, shader, alpha)
            m.skin_bone, m.skin_xf = sk["bone"], sk["xf"]
            return m
        return self._finish(name, "NiTriShape", [xf.apply(p) for p in pos], tris, uvs, shader, alpha)

    def _finish(self, name, kind, pos, tris, uvs, shader, alpha) -> Mesh:
        n = self.nif
        kept = [t for t in tris if max(t) < len(pos)]
        dropped = len(tris) - len(kept)
        if dropped * 2 > len(tris):
            raise NifError(f"{name}: {dropped}/{len(tris)} 個三角形頂點索引越界（頂點 {len(pos)}），版面可能解析錯位")
        m = Mesh(name, kind, pos, kept,
                 uvs if uvs and len(uvs) == len(pos) else None, source=self.source)
        m.dropped_triangles = dropped
        if shader >= 0 and n.block_type(shader) == "BSLightingShaderProperty":
            r = _R(n.blocks[shader])
            m.shader_type = r.f("I")
            r.f("I")
            r.skip(4 * r.f("I"))
            r.f("i")
            _f1, f2 = r.f("2I")
            m.shader_flags2 = f2
            r.skip(16)
            ts = r.f("i")
            if ts >= 0:
                m.textures = n.texture_slots(ts)
        if alpha >= 0 and n.block_type(alpha) == "NiAlphaProperty":
            r = _R(n.blocks[alpha])
            r.f("I")
            r.skip(4 * r.f("I"))
            r.f("i")
            m.alpha_flags = r.f("H")
            m.alpha_threshold = r.f("B")
        return m


def _tri_shape_data(b: bytes):
    """NiTriShapeData（BS 100）：group id i32、num vertices u16、keep／compress u8×2、has vertices u8＋3f×、
    BS vector flags u16、material crc u32（BS>34）、has normals u8＋3f×（BS 向量旗標含 tangent 時另有 2×3f×）、
    center 3f＋radius f、has vertex colors u8＋4f×、UV 組數取自 vector flags 低 6 bit、UV 2f×、consistency u16、
    additional data ref、num triangles u16、num triangle points u32、has triangles u8＋u16×3、match groups。"""
    r = _R(b)
    r.f("i")
    nv = r.f("H")
    r.skip(2)
    pos = [r.f("3f") for _ in range(nv)] if r.f("B") else []
    vflags = r.f("H")
    r.f("I")
    if r.f("B"):
        r.skip(12 * nv)
        if vflags & 0x1000:
            r.skip(24 * nv)
    r.skip(16)
    if r.f("B"):
        r.skip(16 * nv)
    nuv = vflags & 0x3F
    uvs = [r.f("2f") for _ in range(nv)] if nuv else None
    r.skip(8 * nv * max(0, nuv - 1))
    r.f("H")
    r.f("i")
    nt = r.f("H")
    r.f("I")
    tris = [r.f("3H") for _ in range(nt)] if r.f("B") else []
    return pos, uvs, tris


def resolve_skinning(meshes: list[Mesh]) -> list[Mesh]:
    """把蒙皮 shape 的頂點換到共同的模型空間（原地改，回傳同一串）。

    每根骨的世界矩陣取「所有輸入裡、以這根骨為主骨且頂點最多的 shape」的 skin 矩陣之逆——
    也就是假設最大的那片（頭）本身就存在模型空間。FaceGen 頭內眼睛／眉／嘴的頂點存在 Head 骨空間
    （skin 平移 0），頭本身存在模型空間（skin 平移約 -120），這樣兩者對齊；單獨渲染一個髮型 NIF 時
    髮型自己就是參考，頂點照原樣。NIF 內的骨 NiNode 矩陣不用（FaceGen 存的是近似值，引擎會換成骨架的）。
    """
    ref: dict[str, tuple[int, Xf]] = {}
    for m in meshes:
        if m.skin_xf is not None:
            k = m.skin_bone or ""
            if k not in ref or len(m.positions) > ref[k][0]:
                ref[k] = (len(m.positions), m.skin_xf)
    for m in meshes:
        if m.skin_xf is not None:
            xf = ref[m.skin_bone or ""][1].inverse() @ m.skin_xf
            m.positions = [xf.apply(p) for p in m.positions]
            m.skin_xf = None
    return meshes


def load_meshes(path: str | Path, data: bytes | None = None, resolve: bool = True,
                skinned_transform: str = "ignore") -> list[Mesh]:
    """Apply optional local AV transforms before skin alignment; default preserves legacy geometry."""
    p = Path(path)
    ms = NifGeometry(data if data is not None else p.read_bytes(), str(p), skinned_transform).meshes()
    return resolve_skinning(ms) if resolve else ms


def bounds(meshes: list[Mesh]):
    xs = [v for m in meshes for v in m.positions]
    if not xs:
        raise NifError("沒有任何頂點")
    lo = tuple(min(v[k] for v in xs) for k in range(3))
    hi = tuple(max(v[k] for v in xs) for k in range(3))
    c = tuple((lo[k] + hi[k]) / 2 for k in range(3))
    rad = max(math.dist(c, v) for v in xs)
    return lo, hi, c, rad
