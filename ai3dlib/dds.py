# 複製自 agentctl `tools/lib/dds.py` @ `0e29b583`，手動同步；只改了 docstring 裡一條私有路徑。
"""DDS：header 解析、FaceTint gate、Pillow 解碼、無損 RGBA8 寫出（tools-lib-refactor B3 收攏）。

來源：`npc_face/dds.py`（read_dds、DdsInfo、top_mip_bytes、facetint_gate）與 `outfit/dds.py`（read_image、describe、
mip_chain、write_rgba）原封搬來，兩個舊路徑都留 `sys.modules` 別名（`npc_face.dds is outfit.dds is lib.dds`）；
head_render `assets.load_image`／`sampler_from_image` 併成 `open_rgba`／`sampler_from_image`，jslot_render 的 DXT1
嗅探是 `is_dxt1_sniff`。兩份 header 解析語意不同（`describe` 不驗 header size、不看 DDPF_FOURCC 旗標、size 讀的是
offset 16 起的兩個 u32 反過來），各自原樣保留，差異表見 `lib/test_diff_tables.py`。例外類別兩個名字都有：
`DdsError`（原 npc_face）、`DDSError`（原 outfit），彼此不同類。

## 原 npc_face/dds.py

讀 DDS header（唯讀），並提供 FaceTint 用的格式 gate。

header 版面（Microsoft DDS_HEADER，124 bytes，緊接 magic `DDS `）：
height@12、width@16、mipMapCount@28、DDS_PIXELFORMAT@76（size、flags@80、fourCC@84、
RGBBitCount@88、R/G/B/A mask@92..104）；FourCC 為 `DX10` 時，DDS_HEADER_DXT10 在 @128，
dxgiFormat 是第一個 u32。

FaceTint 可用格式依本機實證（工作區私有交接）：
- 原版：512² DXT5、9 mip；
- 現役 Malryn 層：1024² 未壓縮 32-bit（pf_flags 0x41）無 mip，實機正常；
- RaceMenu SaveCharacter：512² 未壓縮 32-bit、1 mip。
DXT1／BC7 屬引擎一般可吃的貼圖格式，但沒有 FaceTint 實證 → 判 WARN 而非 PASS。

## 原 outfit/dds.py

DDS 讀寫：讀交給 Pillow（DXT1/3/5、BC4/5/7、未壓縮），寫只做無損 RGBA8（A8R8G8B8，BGRA 位元組序）。

寫出的格式：DDS_HEADER（124 bytes）＋DDPF_RGB|DDPF_ALPHAPIXELS、32 bpp、遮罩 R=00FF0000 G=0000FF00 B=000000FF
A=FF000000；`mips=True` 時帶完整 mip 鏈（BOX 縮到 1×1）、DDSCAPS_COMPLEX|MIPMAP。Skyrim SE 讀得到這種未壓縮格式；
要 BC7／BC3 壓縮得用外部 texconv，不在本工具。
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass

DDPF_ALPHAPIXELS = 0x1
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DXGI_NAMES = {
    71: "BC1", 72: "BC1", 74: "BC2", 75: "BC2", 77: "BC3", 78: "BC3",
    80: "BC4", 83: "BC5", 98: "BC7", 99: "BC7",
    28: "RGBA32", 29: "RGBA32", 87: "RGBA32", 91: "RGBA32",
}
FOURCC_NAMES = {b"DXT1": "DXT1", b"DXT3": "DXT3", b"DXT5": "DXT5",
                b"ATI1": "BC4", b"ATI2": "BC5", b"BC4U": "BC4", b"BC5U": "BC5"}
ALLOWED_SIZES = (256, 512, 1024, 2048, 4096)
PROVEN_FORMATS = {"DXT5", "RGBA32"}               # 有 FaceTint 實證
ACCEPTED_FORMATS = PROVEN_FORMATS | {"DXT1", "BC1", "BC3", "BC7"}   # BC1＝DXT1、BC3＝DXT5（DX10 header）
BLOCK_BYTES = {"DXT1": 8, "BC1": 8, "BC4": 8, "DXT3": 16, "DXT5": 16, "BC2": 16, "BC3": 16,
               "BC5": 16, "BC7": 16}


class DdsError(ValueError):
    pass


@dataclass
class DdsInfo:
    width: int
    height: int
    mips: int            # header 的 mipMapCount，0 視為 1
    fourcc: str          # 原始 FourCC（可能為空字串）
    pf_flags: int
    rgb_bits: int
    dxgi: int | None
    format: str          # DXT1／DXT5／BC7／RGBA32／RGB24…／UNKNOWN
    size: int            # 檔案總長

    def as_dict(self) -> dict:
        return dict(width=self.width, height=self.height, mips=self.mips, format=self.format,
                    fourcc=self.fourcc, pf_flags=f"0x{self.pf_flags:X}", rgb_bits=self.rgb_bits,
                    dxgi=self.dxgi, size=self.size)


def read_dds(data: bytes) -> DdsInfo:
    if len(data) < 128 or data[:4] != b"DDS " or struct.unpack_from("<I", data, 4)[0] != 124:
        raise DdsError("不是 DDS（magic／header size 不符或檔案太短）")
    height, width = struct.unpack_from("<II", data, 12)
    mips = struct.unpack_from("<I", data, 28)[0] or 1
    pf_flags, fourcc_raw, bits = struct.unpack_from("<I4sI", data, 80)
    alpha_mask = struct.unpack_from("<I", data, 104)[0]
    dxgi = None
    fourcc = ""
    if pf_flags & DDPF_FOURCC:
        fourcc = fourcc_raw.decode("latin-1")
        if fourcc_raw == b"DX10":
            if len(data) < 148:
                raise DdsError("DX10 header 截斷")
            dxgi = struct.unpack_from("<I", data, 128)[0]
            fmt = DXGI_NAMES.get(dxgi, f"DXGI{dxgi}")
        else:
            fmt = FOURCC_NAMES.get(fourcc_raw, "UNKNOWN")
    elif pf_flags & DDPF_RGB:
        if bits == 32 and (pf_flags & DDPF_ALPHAPIXELS or alpha_mask):
            fmt = "RGBA32"
        else:
            fmt = f"RGB{bits}"
    else:
        fmt = "UNKNOWN"
    return DdsInfo(width, height, mips, fourcc, pf_flags, bits, dxgi, fmt, len(data))


def top_mip_bytes(info: DdsInfo) -> int | None:
    if info.format in BLOCK_BYTES:
        return max(1, (info.width + 3) // 4) * max(1, (info.height + 3) // 4) * BLOCK_BYTES[info.format]
    if info.format == "RGBA32":
        return info.width * info.height * 4
    return None


def facetint_gate(info: DdsInfo) -> tuple[str, str]:
    """回 (PASS|WARN|FAIL, 細節)。"""
    detail = (f"{info.width}x{info.height} {info.format} mips={info.mips} "
              f"pf_flags=0x{info.pf_flags:X} bytes={info.size}")
    problems = []
    for name, v in (("寬", info.width), ("高", info.height)):
        if v not in ALLOWED_SIZES:
            problems.append(f"{name} {v} 不是 {'/'.join(map(str, ALLOWED_SIZES))} 之一")
    if info.format not in ACCEPTED_FORMATS:
        problems.append(f"格式 {info.format} 不在已知可用清單")
    need = top_mip_bytes(info)
    header = 148 if info.dxgi is not None else 128
    if need is not None and info.size < header + need:
        problems.append(f"資料截斷：頂層 mip 需 {need} bytes，檔案只有 {info.size - header}")
    if problems:
        return "FAIL", detail + "；" + "；".join(problems)
    warns = []
    if info.width != info.height:
        warns.append("非正方形（實證樣本皆正方形）")
    if info.format not in PROVEN_FORMATS:
        warns.append(f"{info.format} 沒有 FaceTint 實證（實證只有 DXT5、未壓縮 RGBA32）")
    if warns:
        return "WARN", detail + "；" + "；".join(warns)
    return "PASS", detail


# ---------------------------------------------------------------- 原 outfit/dds.py

DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH, DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT = 0x1, 0x2, 0x4, 0x8, 0x1000, 0x20000
DDPF_ALPHAPIXELS, DDPF_RGB = 0x1, 0x40
DDSCAPS_COMPLEX, DDSCAPS_TEXTURE, DDSCAPS_MIPMAP = 0x8, 0x1000, 0x400000


class DDSError(ValueError):
    pass


def read_image(data: bytes):
    """DDS／PNG bytes → RGBA Image（原尺寸，只取最上層 mip）；解不開丟 DDSError。"""
    return open_rgba(data, max_side=None, wrap_errors=True)


def describe(data: bytes) -> dict:
    """原檔的格式摘要（fourcc／mip 數）；不是 DDS 就回 {}。"""
    if len(data) < 128 or data[:4] != b"DDS ":
        return {}
    fourcc = data[84:88]
    return {"fourcc": fourcc.decode("latin1") if fourcc.strip(b"\0") else "uncompressed",
            "mips": max(1, struct.unpack_from("<I", data, 28)[0]),
            "size": list(struct.unpack_from("<II", data, 16))[::-1]}


def mip_chain(im):
    from PIL import Image
    levels = [im]
    w, h = im.size
    while w > 1 or h > 1:
        w, h = max(1, w // 2), max(1, h // 2)
        levels.append(levels[-1].resize((w, h), Image.BOX))
    return levels


def write_rgba(im, mips: bool = True) -> bytes:
    """RGBA Image → 未壓縮 A8R8G8B8 DDS bytes。"""
    if im.mode != "RGBA":
        raise DDSError(f"只寫 RGBA，收到 {im.mode}")
    w, h = im.size
    levels = mip_chain(im) if mips else [im]
    hdr = bytearray(128)
    hdr[0:4] = b"DDS "
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT
    if len(levels) > 1:
        flags |= DDSD_MIPMAPCOUNT
    struct.pack_into("<IIIIIII", hdr, 4, 124, flags, h, w, w * 4, 0, len(levels))
    struct.pack_into("<II4sIIIII", hdr, 76, 32, DDPF_RGB | DDPF_ALPHAPIXELS, b"\0\0\0\0", 32,
                     0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    caps = DDSCAPS_TEXTURE | ((DDSCAPS_COMPLEX | DDSCAPS_MIPMAP) if len(levels) > 1 else 0)
    struct.pack_into("<I", hdr, 108, caps)
    return bytes(hdr) + b"".join(level.tobytes("raw", "BGRA") for level in levels)


# ---------------------------------------------------------------- 原 head_render/assets.py、jslot_render/cli.py

def open_rgba(data: bytes, max_side: int | None = None, wrap_errors: bool = False):
    """DDS／PNG bytes → RGBA Image（Pillow 解，只取最上層 mip）。

    max_side：最長邊超過就用 LANCZOS 等比縮（決定性；原 head_render `load_image`）；None＝不縮（原 outfit `read_image`）。
    wrap_errors：True 時 Pillow 開檔／解碼的例外包成 DDSError（原 outfit）；False 時原例外往上丟（原 head_render）。
    """
    from PIL import Image
    if wrap_errors:
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as e:  # Pillow 對壞檔丟的例外型別不一
            raise DDSError(f"貼圖解不開：{e}") from None
    else:
        im = Image.open(io.BytesIO(data))
        im.load()
    im = im.convert("RGBA")
    if max_side is not None and max(im.size) > max_side:
        f = max(im.size) / max_side
        im = im.resize((max(1, round(im.size[0] / f)), max(1, round(im.size[1] / f))), Image.LANCZOS)
    return im


def sampler_from_image(im):
    """最近鄰取樣，UV 以 repeat 包回。回傳 (u, v) -> (r, g, b, a)。"""
    w, h = im.size
    buf = im.tobytes()

    def sample(u, v):
        x = int(u * w) % w
        y = int(v * h) % h
        o = (y * w + x) * 4
        return buf[o], buf[o + 1], buf[o + 2], buf[o + 3]
    return sample


def is_dxt1_sniff(blob: bytes) -> bool:
    """jslot_render 的 DXT1 嗅探：只看 magic 與 offset 84–88 是不是 `DXT1`（不驗 header size、不看 DDPF_FOURCC）。"""
    return blob[:4] == b'DDS ' and len(blob) >= 88 and blob[84:88] == b'DXT1'
