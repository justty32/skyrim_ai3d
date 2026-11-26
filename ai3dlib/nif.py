# 複製自 agentctl `tools/lib/nif.py` @ `0e29b583`，手動同步。
"""Skyrim SE NIF（20.2.0.7／user 12／BS stream 100）header＋block 表的無損讀寫。

只解析 header、字串表、footer，以及少數小 block（NiNode 家族的 children、BSShaderTextureSet
的 slot）；幾何、skin 等其餘 block 一律當原始 bytes 保留。`Nif.parse(data).serialize()`
對支援的檔案逐位元組相同（測試以原版 Lydia FaceGen 驗）。

版面依據（皆以 Lydia `facegeom/skyrim.esm/000a2c8e.nif` 實測對過）：
- header：版本行（含 `\\n`）、version u32、endian u8、user version u32、num blocks u32、
  BS version u32、三個 ExportString（u8 長度＋含結尾 NUL 的 bytes：author／process／export，
  BS 100 < 131 所以是三個）、num block types u16、block types（u32 長度＋bytes）、
  type index u16×N、block sizes u32×N、num strings u32、max string length u32、
  strings（u32 長度＋bytes）、num groups u32、groups u32×G。
  同 `look_transplant/transplant_lib/nif.py::nif_texture_blocks`。
- footer：num roots u32＋root refs i32。
- NiObjectNET／NiAVObject／NiNode（BS 100）：name 字串 index u32、num extra data u32＋refs、
  controller ref、flags u32、translation 3f、rotation 9f、scale f、collision ref、
  num children u32＋refs、num effects u32＋refs。
- 各 shape 類型（BSTriShape 家族、NiTriShape、NiTriStrips）block 前 4 bytes 是 name 字串 index。
- BSShaderTextureSet：count u32＋count 個 SizedString（u32 長度＋bytes）。
- ref／ptr 欄位（`drop_shape` 重編號用，`_ref_slots`；以 2026-11-22 fp2-s3 真 RaceMenu 匯出頭逐塊對過，
  欄位版面同 nif.xml BS 100）：
  - BSTriShape 家族：NiAVObject（同上，到 collision ref）＋bounding sphere 4f＋skin ref＋shader ref＋alpha ref，
    其後幾何資料無 ref。
  - NiSkinInstance：data ref、skin partition ref、skeleton root ptr、num bones u32＋bone ptrs；
    BSDismemberSkinInstance 再接 num partitions u32＋每個 4 bytes。
  - BSLightingShaderProperty：shader type u32、name、num extra u32＋refs、controller ref、flags 2×u32、
    uv offset／scale 4f、texture set ref，其後無 ref。BSEffectShaderProperty：name、extra、controller，其後無 ref。
  - NiAlphaProperty：name、extra、controller、flags u16、threshold u8。
  - NiSkinData／NiSkinPartition／BSShaderTextureSet：此版本無 ref。
  其餘型別一律視為「版面未知」，`drop_shape` 遇到就拒絕（不猜）。

字串一律以 cp1252＋surrogateescape 解碼，寫回時同法編碼，所以讀寫無損。

tools-lib-refactor B1：本檔是 `npc_face/nifio.py` 原封搬來（舊路徑留 `sys.modules` 別名，
`npc_face.nifio is lib.nif`）；檔尾另收 custom_race `verify.nif_shape_names`，改名 `shape_names_any_bs`。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

SUPPORTED_VERSION = 0x14020007
SUPPORTED_USER = 12
SUPPORTED_BS = 100

SHAPE_TYPES = frozenset({
    "BSDynamicTriShape", "BSTriShape", "NiTriShape", "BSSubIndexTriShape", "NiTriStrips",
})
NODE_TYPES = frozenset({"NiNode", "BSFadeNode", "BSLeafAnimNode", "BSFaceGenNiNode"})
TRISHAPE_TYPES = frozenset({"BSTriShape", "BSDynamicTriShape", "BSSubIndexTriShape"})
SKIN_INSTANCE_TYPES = frozenset({"NiSkinInstance", "BSDismemberSkinInstance"})
NO_REF_TYPES = frozenset({"NiSkinData", "NiSkinPartition", "BSShaderTextureSet"})
TEXSET_TYPE = "BSShaderTextureSet"
NONE_REF = -1
NO_STRING = 0xFFFFFFFF
ENC = "cp1252"
ERR = "surrogateescape"


class NifError(ValueError):
    pass


def _dec(raw: bytes) -> str:
    return raw.decode(ENC, ERR)


def _enc(text: str) -> bytes:
    return text.encode(ENC, ERR)


class _Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = data, pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise NifError(f"NIF 截斷（位置 {self.pos} 要 {n} bytes，檔長 {len(self.data)}）")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def num(self, fmt: str):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def nums(self, fmt: str, count: int) -> list:
        return list(struct.unpack(f"<{count}{fmt}", self.take(struct.calcsize(f"<{count}{fmt}"))))

    def sized(self) -> bytes:
        return self.take(self.num("I"))


@dataclass
class NodeInfo:
    """NiNode 家族 block 的可讀欄位（只解到 children／effects）。"""
    index: int
    type: str
    name: str | None
    children: list[int]
    effects: list[int]


@dataclass
class Nif:
    header_line: bytes                 # 含結尾 \n
    version: int
    endian: int
    user_version: int
    bs_version: int
    export_strings: list[bytes]        # 三個 ExportString 的內容（含 NUL，不含長度 byte）
    block_types: list[bytes]
    type_index: list[int]              # 原始 u16（保留高位元）
    strings: list[bytes]
    max_string_len: int
    groups: list[int]
    blocks: list[bytes]
    roots: list[int]
    trailer: bytes = b""               # footer 之後若還有 bytes，原樣保留
    _source_len: int = field(default=0, repr=False)

    # ---------- 讀寫 ----------
    @classmethod
    def parse(cls, data: bytes) -> "Nif":
        nl = data.find(b"\n")
        if nl < 0 or not data.startswith(b"Gamebryo File Format"):
            raise NifError("不是 Gamebryo NIF（缺版本行）")
        r = _Reader(data, nl + 1)
        version, endian, user = r.num("I"), r.num("B"), r.num("I")
        if version != SUPPORTED_VERSION or endian != 1 or user != SUPPORTED_USER:
            raise NifError(f"不支援的 NIF 版本：0x{version:08X} endian={endian} user={user}")
        nblocks, bs = r.num("I"), r.num("I")
        if bs != SUPPORTED_BS:
            raise NifError(f"不是 Skyrim SE BS stream 100：{bs}")
        export = [r.take(r.num("B")) for _ in range(3)]
        types = [r.sized() for _ in range(r.num("H"))]
        index = r.nums("H", nblocks)
        sizes = r.nums("I", nblocks)
        nstrings, maxlen = r.num("I"), r.num("I")
        strings = [r.sized() for _ in range(nstrings)]
        groups = r.nums("I", r.num("I"))
        for i, t in enumerate(index):
            if (t & 0x7FFF) >= len(types):
                raise NifError(f"block {i} 的 type index {t} 超出 block types 表")
        blocks = [r.take(s) for s in sizes]
        roots = r.nums("i", r.num("I"))
        trailer = data[r.pos:]
        return cls(data[:nl + 1], version, endian, user, bs, export, types, index,
                   strings, maxlen, groups, blocks, roots, trailer, len(data))

    def serialize(self) -> bytes:
        out = bytearray(self.header_line)
        out += struct.pack("<IBIII", self.version, self.endian, self.user_version,
                           len(self.blocks), self.bs_version)
        for s in self.export_strings:
            if len(s) > 255:
                raise NifError("ExportString 超過 255 bytes")
            out += struct.pack("<B", len(s)) + s
        out += struct.pack("<H", len(self.block_types))
        for t in self.block_types:
            out += struct.pack("<I", len(t)) + t
        if len(self.type_index) != len(self.blocks):
            raise NifError("type index 數與 block 數不符")
        out += struct.pack(f"<{len(self.type_index)}H", *self.type_index)
        out += struct.pack(f"<{len(self.blocks)}I", *(len(b) for b in self.blocks))
        out += struct.pack("<II", len(self.strings), self.max_string_len)
        for s in self.strings:
            out += struct.pack("<I", len(s)) + s
        out += struct.pack("<I", len(self.groups))
        out += struct.pack(f"<{len(self.groups)}I", *self.groups)
        for b in self.blocks:
            out += b
        out += struct.pack("<I", len(self.roots))
        out += struct.pack(f"<{len(self.roots)}i", *self.roots)
        out += self.trailer
        return bytes(out)

    # ---------- 基本查詢 ----------
    def block_type(self, i: int) -> str:
        return _dec(self.block_types[self.type_index[i] & 0x7FFF])

    def string(self, idx: int) -> str | None:
        if idx == NO_STRING or idx < 0:
            return None
        if idx >= len(self.strings):
            raise NifError(f"字串 index {idx} 超出字串表（{len(self.strings)}）")
        return _dec(self.strings[idx])

    def block_name(self, i: int) -> str | None:
        """NiObjectNET 系 block 的 name（前 4 bytes 字串 index）。"""
        b = self.blocks[i]
        if len(b) < 4:
            raise NifError(f"block {i} 太短，讀不到 name")
        return self.string(struct.unpack_from("<I", b, 0)[0])

    def shape_indices(self) -> list[int]:
        return [i for i in range(len(self.blocks)) if self.block_type(i) in SHAPE_TYPES]

    def shapes(self) -> list[tuple[int, str, str | None]]:
        """[(block index, block type, shape 名)]，依 block 順序。"""
        return [(i, self.block_type(i), self.block_name(i)) for i in self.shape_indices()]

    def shape_names(self) -> list[str]:
        return [n if n is not None else "" for _, _, n in self.shapes()]

    def root(self) -> tuple[int, str, str | None] | None:
        if not self.roots or not 0 <= self.roots[0] < len(self.blocks):
            return None
        i = self.roots[0]
        return i, self.block_type(i), self.block_name(i)

    def node(self, i: int) -> NodeInfo:
        """解 NiNode 家族 block 到 children／effects。不在 NODE_TYPES 或版面不合就丟 NifError。"""
        kind = self.block_type(i)
        if kind not in NODE_TYPES:
            raise NifError(f"block {i} 是 {kind}，不是 NiNode 家族")
        r = _Reader(self.blocks[i])
        name = self.string(r.num("I"))
        r.nums("i", r.num("I"))            # extra data refs
        r.num("i")                          # controller
        r.num("I")                          # flags（BS > 26 為 u32）
        r.take(4 * (3 + 9 + 1))             # translation、rotation、scale
        r.num("i")                          # collision object
        children = r.nums("i", r.num("I"))
        effects = r.nums("i", r.num("I"))
        if kind in ("NiNode", "BSFadeNode") and r.pos != len(self.blocks[i]):
            raise NifError(f"block {i}（{kind}）解析後剩 {len(self.blocks[i]) - r.pos} bytes")
        return NodeInfo(i, kind, name, children, effects)

    # ---------- 貼圖 ----------
    def texture_set_indices(self) -> list[int]:
        return [i for i in range(len(self.blocks)) if self.block_type(i) == TEXSET_TYPE]

    def texture_slots(self, i: int) -> list[str]:
        return [_dec(s) for s in self._raw_slots(i)]

    def texture_sets(self) -> list[tuple[int, list[str]]]:
        return [(i, self.texture_slots(i)) for i in self.texture_set_indices()]

    def _raw_slots(self, i: int) -> list[bytes]:
        if self.block_type(i) != TEXSET_TYPE:
            raise NifError(f"block {i} 不是 {TEXSET_TYPE}")
        r = _Reader(self.blocks[i])
        slots = [r.sized() for _ in range(r.num("I"))]
        if r.pos != len(self.blocks[i]):
            raise NifError(f"block {i} BSShaderTextureSet 含未辨識資料")
        return slots

    def set_texture_slot(self, i: int, slot: int, path: str) -> None:
        """改一個 slot；block size 由 serialize 依新長度重算。"""
        slots = self._raw_slots(i)
        if not 0 <= slot < len(slots):
            raise NifError(f"block {i} 只有 {len(slots)} 個 slot，沒有 slot {slot}")
        slots[slot] = _enc(path)
        out = bytearray(struct.pack("<I", len(slots)))
        for s in slots:
            out += struct.pack("<I", len(s)) + s
        self.blocks[i] = bytes(out)

    # ---------- 字串表 ----------
    def find_string(self, text: str) -> list[int]:
        raw = _enc(text)
        return [k for k, s in enumerate(self.strings) if s == raw]

    def set_string(self, idx: int, text: str) -> None:
        """改字串表某格（所有引用這格的 block 一起改名），並重算 max string length。"""
        if not 0 <= idx < len(self.strings):
            raise NifError(f"字串 index {idx} 超出字串表")
        self.strings[idx] = _enc(text)
        self.max_string_len = max((len(s) for s in self.strings), default=0)

    def set_block_name(self, i: int, text: str) -> None:
        """把 block i 的 name 指到 text（字串表沒有就附加一格；不動其他引用同格的 block）。

        2026-11-22 真 RaceMenu 匯出物（skee ExportHead）的根 BSFadeNode 沒有名字（index 0xFFFFFFFF），
        CK FaceGen 則是 `<FormID>.NIF`；上架時用這個補名。
        """
        b = self.blocks[i]
        if len(b) < 4:
            raise NifError(f"block {i} 太短，寫不了 name")
        raw = _enc(text)
        idx = next((k for k, s in enumerate(self.strings) if s == raw), None)
        if idx is None:
            self.strings.append(raw)
            idx = len(self.strings) - 1
            self.max_string_len = max(self.max_string_len, len(raw))
        self.blocks[i] = struct.pack("<I", idx) + b[4:]

    def rename_shape(self, old: str, new: str) -> int:
        """把 shape 名 old 改成 new，回傳受影響的 shape 數。

        若該字串格同時被非 shape block 引用（例：根節點同名）就拒絕，避免誤改。
        """
        targets = {k for k in self.find_string(old)}
        if not targets:
            raise NifError(f"字串表裡沒有 {old!r}")
        users: dict[int, list[int]] = {k: [] for k in targets}
        for i, b in enumerate(self.blocks):
            if len(b) >= 4 and (self.block_type(i) in SHAPE_TYPES or self.block_type(i) in NODE_TYPES):
                k = struct.unpack_from("<I", b, 0)[0]
                if k in users:
                    users[k].append(i)
        hit = 0
        for k, blocks in users.items():
            if any(self.block_type(i) not in SHAPE_TYPES for i in blocks):
                raise NifError(f"字串 {old!r} 也被非 shape block 用作名稱，拒絕改名")
            if blocks:
                self.set_string(k, new)
                hit += len(blocks)
        if not hit:
            raise NifError(f"沒有 shape 叫 {old!r}")
        return hit


    # ---------- ref 欄位與移除 shape ----------
    def _ref_slots(self, i: int) -> list[tuple[int, bool]]:
        """block i 內所有 block ref／ptr 欄位：[(byte offset, 是否為擁有關係的 ref)]。

        ptr（skeleton root、bones）只需重編號，不構成擁有關係。版面未知的型別丟 NifError。
        """
        kind, b = self.block_type(i), self.blocks[i]
        r = _Reader(b)
        out: list[tuple[int, bool]] = []

        def refs(n: int, owned: bool = True) -> None:
            for _ in range(n):
                out.append((r.pos, owned))
                r.take(4)

        def net() -> None:                  # NiObjectNET：name、extra data、controller
            r.num("I")
            refs(r.num("I"))
            refs(1)

        def avobject() -> None:             # NiAVObject（BS 100）
            net()
            r.num("I")
            r.take(4 * 13)
            refs(1)

        exact = True
        if kind in ("NiNode", "BSFadeNode"):
            avobject()
            refs(r.num("I"))
            refs(r.num("I"))
        elif kind in TRISHAPE_TYPES:
            avobject()
            r.take(16)
            refs(3)
            exact = False
        elif kind in SKIN_INSTANCE_TYPES:
            refs(2)
            refs(1, owned=False)
            refs(r.num("I"), owned=False)
            if kind == "BSDismemberSkinInstance":
                r.take(4 * r.num("I"))
        elif kind == "BSLightingShaderProperty":
            r.num("I")
            net()
            r.take(4 * 2 + 4 * 4)
            refs(1)
            exact = False
        elif kind == "BSEffectShaderProperty":
            net()
            exact = False
        elif kind == "NiAlphaProperty":
            net()
            r.take(3)
        elif kind in NO_REF_TYPES:
            exact = False
        else:
            raise NifError(f"block {i} 型別 {kind} 的 ref 版面未支援，無法安全重編號")
        if exact and r.pos != len(b):
            raise NifError(f"block {i}（{kind}）ref 解析後剩 {len(b) - r.pos} bytes，版面不符")
        for off, _ in out:
            v = struct.unpack_from("<i", b, off)[0]
            if v != NONE_REF and not 0 <= v < len(self.blocks):
                raise NifError(f"block {i}（{kind}）offset {off} 的 ref {v} 超出範圍")
        return out

    def _refs_of(self, i: int, owned_only: bool = False) -> list[int]:
        b = self.blocks[i]
        return [v for off, own in self._ref_slots(i)
                if (own or not owned_only) and (v := struct.unpack_from("<i", b, off)[0]) != NONE_REF]

    def reachable_blocks(self) -> set[int]:
        """自 footer roots 沿全部 ref／ptr 可達的 block。"""
        seen: set[int] = set()
        stack = [x for x in self.roots if 0 <= x < len(self.blocks)]
        while stack:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            stack.extend(self._refs_of(i))
        return seen

    def drop_shape(self, name: str) -> dict:
        """把名為 name 的 shape 從檔案**真的刪掉**，回傳 {shapes, removed:[(舊 index, 型別)], kept_shared}。

        步驟：從所有 NiNode／BSFadeNode 的 children 陣列刪掉指向它的 ref（縮短 count）；
        刪 shape 及只被它擁有的子 block（skin instance／data／partition、shader、texture set、alpha…，
        仍被其他留存 block 引用者保留，例：兩個 shape 共用的 texture set）；重編號全檔 ref／ptr 與 footer roots。
        全檔任何 block 型別的 ref 版面不在 `_ref_slots` 支援表內就整個拒絕（NifError），不留半成品。
        shape 名字串留在字串表（無人引用，無害；省去重編號全檔字串 index）。
        """
        n = len(self.blocks)
        for i in range(n):                  # 先驗全檔版面，任何未知型別就不動
            self._ref_slots(i)
        targets = [i for i in self.shape_indices() if self.block_name(i) == name]
        if not targets:
            raise NifError(f"沒有 shape 叫 {name!r}")
        tset = set(targets)
        # 1. 斷 children 連結（縮短陣列）
        for i in range(n):
            if self.block_type(i) not in ("NiNode", "BSFadeNode"):
                continue
            info = self.node(i)
            if not tset & set(info.children):
                continue
            b = self.blocks[i]
            # node() 已驗 NiNode／BSFadeNode 恰好讀完：children 陣列緊接在 effects（最後一段）之前
            nkids, neff = len(info.children), len(info.effects)
            eff_count_off = len(b) - 4 * neff - 4
            kids_off = eff_count_off - 4 * nkids
            kept = [c for c in info.children if c not in tset]
            self.blocks[i] = (b[:kids_off - 4] + struct.pack("<I", len(kept))
                              + struct.pack(f"<{len(kept)}i", *kept) + b[eff_count_off:])
        # 2. 擁有閉包，扣掉仍被留存 block 引用者，迭代到固定點
        doomed: set[int] = set()
        stack = list(targets)
        while stack:
            i = stack.pop()
            if i in doomed:
                continue
            doomed.add(i)
            stack.extend(self._refs_of(i, owned_only=True))
        closure = set(doomed)
        while True:
            still = {v for i in range(n) if i not in doomed for v in self._refs_of(i)} & doomed
            still |= doomed & set(self.roots)
            if still & tset:
                raise NifError(f"shape {name!r} 仍被其他 block 引用（{sorted(still & tset)}），拒絕刪除")
            if not still:
                break
            keep = set(still)
            stack = list(still)
            while stack:                    # 被保留者擁有的子 block 一併保留
                for v in self._refs_of(stack.pop(), owned_only=True):
                    if v in doomed and v not in keep:
                        keep.add(v)
                        stack.append(v)
            doomed -= keep
        shared = sorted(closure - doomed)
        # 3. 重編號
        remap = {}
        for i in range(n):
            if i not in doomed:
                remap[i] = len(remap)
        removed = [(i, self.block_type(i)) for i in sorted(doomed)]
        new_blocks, new_index = [], []
        for i in range(n):
            if i in doomed:
                continue
            b = bytearray(self.blocks[i])
            for off, _ in self._ref_slots(i):
                v = struct.unpack_from("<i", b, off)[0]
                if v != NONE_REF:
                    if v not in remap:
                        raise NifError(f"block {i} 仍指向被刪的 block {v}")
                    struct.pack_into("<i", b, off, remap[v])
            new_blocks.append(bytes(b))
            new_index.append(self.type_index[i])
        self.roots = [remap.get(x, NONE_REF) if x != NONE_REF else x for x in self.roots]
        self.blocks, self.type_index = new_blocks, new_index
        return {"shapes": len(targets), "removed": removed, "kept_shared": shared}


# ---------------------------------------------------------------- 任何 BS 版本的 shape 名
# 原 `look_transplant/custom_race/verify.py::nif_shape_names`（tools-lib-refactor B1 原樣搬來）。
# 跟上面的 `Nif` 不同：只要版本 20.2.0.7、BS 不限（≥103 讀 4 個 export string），所以收 LE（BS 83）檔，
# `Nif.parse` 會拒；shape 型別含 BSSubIndexTriShape／BSDynamicTriShape、不含 NiTriStrips；
# block type 名用 UTF-8 嚴格解、字串用 cp1252 嚴格解；錯誤丟 ValueError（不是 NifError）。

ANY_BS_SHAPE_TYPES = ("BSDynamicTriShape", "BSTriShape", "NiTriShape", "BSSubIndexTriShape")


def shape_names_any_bs(path) -> list[str]:
    b = path.read_bytes()
    o = b.index(b"\n") + 1
    ver = struct.unpack_from("<I", b, o)[0]; o += 4
    if ver != 0x14020007:
        raise ValueError(f"{path}: 非 20.2.0.7")
    o += 1 + 4
    nblocks = struct.unpack_from("<I", b, o)[0]; o += 4
    bsver = struct.unpack_from("<I", b, o)[0]; o += 4
    for _ in range(3 if bsver < 103 else 4):
        ln = b[o]; o += 1 + ln
    ntypes = struct.unpack_from("<H", b, o)[0]; o += 2
    types = []
    for _ in range(ntypes):
        ln = struct.unpack_from("<I", b, o)[0]; o += 4
        types.append(b[o:o + ln].decode()); o += ln
    tidx = struct.unpack_from(f"<{nblocks}H", b, o); o += 2 * nblocks
    sizes = struct.unpack_from(f"<{nblocks}I", b, o); o += 4 * nblocks
    nstr = struct.unpack_from("<I", b, o)[0]; o += 8
    strings = []
    for _ in range(nstr):
        ln = struct.unpack_from("<I", b, o)[0]; o += 4
        strings.append(b[o:o + ln].decode("cp1252")); o += ln
    ngroups = struct.unpack_from("<I", b, o)[0]; o += 4 + 4 * ngroups
    names = []
    for i in range(nblocks):
        if types[tidx[i] & 0x7FFF] in ANY_BS_SHAPE_TYPES:
            si = struct.unpack_from("<i", b, o)[0]
            names.append(strings[si] if si >= 0 else "")
        o += sizes[i]
    return names
