"""texedit 測試：合成 NIF（texedit.synth 的「洋裝」三 shape）＋合成 RGBA8 DDS，只用 tempdir；不碰 MO2、Data、profile。

跑：在 repo 根 `python3 -m unittest discover -s tests -v`
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from texedit import cli, edit, synth, uvmap
from texedit.paths import WriteGuardError
from texedit.pack import pack
from texedit.unpack import UnpackError, unpack

S = synth.SIZE


def _img(path):
    with Image.open(path) as im:
        im.load()
        return im.copy()


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        # 拒寫的根全指到 tempdir 底下，測試才碰得到又不碰真的 MO2／Data
        env = {"MO2_DIR": str(self.tmp / "mo2"), "SKYRIM_DATA": str(self.tmp / "Data"),
               "LAYER_APPLY_ROOT": str(self.tmp / "_layer-apply"), "TESTLAYER_ROOT": str(self.tmp / "_testlayer")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        self.nif = self.tmp / "in" / "dress.nif"
        self.nif.parent.mkdir()
        self.nif.write_bytes(synth.dress_nif())
        self.d_im, self.n_im = synth.diffuse_image(), synth.normal_image()
        self.dds_d = self.tmp / "in" / "dress_d.dds"
        self.dds_n = self.tmp / "in" / "dress_n.dds"
        self.dds_d.write_bytes(synth.dds_bytes(self.d_im))
        self.dds_n.write_bytes(synth.dds_bytes(self.n_im))

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()

    def do_unpack(self, out="out", **kw):
        return unpack([self.nif], [self.dds_d, self.dds_n], self.tmp / out, **kw)


class IslandTest(unittest.TestCase):
    def test_islands_vertex_sharing_uv_weld_and_order(self):
        shapes = {s["name"]: s for s in synth.dress_shapes()}
        top = uvmap.find_islands(shapes["Top"]["uvs"], shapes["Top"]["tris"])
        self.assertEqual([len(i.tris) for i in top], [8, 8])
        self.assertLess(top[0].bbox_uv[0], top[1].bbox_uv[0])        # 閱讀順序：左（正面）先
        skirt = uvmap.find_islands(shapes["Skirt"]["uvs"], shapes["Skirt"]["tris"])
        self.assertEqual(len(skirt), 1)                                  # 拆開的頂點照 UV 焊回一塊
        rib = uvmap.find_islands(shapes["Ribbon"]["uvs"], shapes["Ribbon"]["tris"])
        self.assertEqual(len(rib), 2)
        self.assertAlmostEqual(rib[1].centroid_uv[0], 0.1667, places=3)  # 越界的 u 1.1–1.3 包回

    def test_wrap_copies(self):
        self.assertEqual(len(uvmap._copies([(0.1, 0.1), (0.2, 0.1), (0.1, 0.2)])), 1)
        self.assertEqual(len(uvmap._copies([(0.9, 0.5), (1.2, 0.5), (0.9, 0.6)])), 2)    # 跨右緣
        self.assertEqual(len(uvmap._copies([(0.9, 0.9), (1.2, 0.9), (0.9, 1.2)])), 4)    # 跨右下角
        c = uvmap._copies([(2.1, -0.9), (2.2, -0.9), (2.1, -0.8)])[0]
        self.assertAlmostEqual(c[0][0], 0.1, places=6)
        self.assertAlmostEqual(c[0][1], 0.1, places=6)


class UnpackTest(Base):
    def test_layout_shapes_islands_masks(self):
        L = self.do_unpack()
        out = self.tmp / "out"
        self.assertEqual(json.loads((out / "layout.json").read_text("utf-8")), L)
        self.assertEqual(L["format"], "texedit-layout/1")
        sh = {s["name"]: s for s in L["shapes"]}
        self.assertEqual([len(sh[n]["islands"]) for n in ("Top", "Skirt", "Ribbon")], [2, 1, 2])
        self.assertEqual(sh["Ribbon"]["uv_out_of_range_tris"], 1)
        top = sh["Top"]["maps"]["dress_d"]
        self.assertEqual(top["slot"], 0)
        self.assertEqual(sh["Top"]["maps"]["dress_n"]["slot"], 1)
        self.assertEqual(sh["Top"]["unmatched_slots"], [])
        # 正面 island：u,v 0.05–0.45 → 像素約 6–58
        i0 = top["islands"][0]
        x0, y0, x1, y1 = i0["bbox_px"]
        self.assertTrue(5 <= x0 <= 7 and 5 <= y0 <= 7 and 57 <= x1 <= 59 and 57 <= y1 <= 59, i0["bbox_px"])
        self.assertAlmostEqual(i0["area_frac"], 0.16, delta=0.02)
        # shape 遮罩＝各 island 的聯集
        m = _img(out / top["mask"])
        u = Image.new("L", m.size, 0)
        for isl in top["islands"]:
            u = Image.composite(Image.new("L", m.size, 255), u, _img(out / isl["mask"]))
        self.assertEqual(m.tobytes(), u.tobytes())
        self.assertEqual(m.mode, "L")
        # 越界三角形畫在包回的位置（x 約 12–39）
        r1 = sh["Ribbon"]["maps"]["dress_d"]["islands"][1]["bbox_px"]
        self.assertTrue(11 <= r1[0] <= 13 and 38 <= r1[2] <= 40, r1)

    def test_overlaps(self):
        L = self.do_unpack()
        pairs = {(o["texture"], o["a"], o["b"]) for o in L["overlaps"]}
        self.assertIn(("dress_d", "dress.Top", "dress.Ribbon"), pairs)
        self.assertIn(("dress_d", "dress.Skirt", "dress.Ribbon"), pairs)   # 包回的三角形落在裙上
        self.assertNotIn(("dress_d", "dress.Top", "dress.Skirt"), pairs)
        self.assertFalse(any("#i" in o["a"] for o in L["overlaps"]))      # 同 shape 的 island 不重疊

    def test_textures_png_lossless_and_normal_untouched(self):
        L = self.do_unpack()
        out = self.tmp / "out"
        d = _img(out / L["textures"]["dress_d"]["png"])
        self.assertEqual((d.mode, d.size), ("RGBA", (S, S)))
        self.assertEqual(d.tobytes(), self.d_im.tobytes())              # alpha 也原樣
        n = _img(out / L["textures"]["dress_n"]["png"])
        self.assertEqual(n.getpixel((5, 5)), (128, 128, 255, 200))
        self.assertTrue(L["textures"]["dress_n"]["normal_map"])
        self.assertFalse(L["textures"]["dress_d"]["normal_map"])
        self.assertEqual(L["textures"]["dress_d"]["dds"]["format"], "RGBA32")
        self.assertEqual(L["textures"]["dress_d"]["dds"]["width"], S)

    def test_overlays(self):
        L = self.do_unpack()
        out = self.tmp / "out"
        allp = _img(out / L["textures"]["dress_d"]["overlay_all"])
        self.assertEqual(allp.size[0], 512)                              # 128 放大 4 倍
        self.assertGreater(allp.size[1], 512)                            # 下方有圖例
        top = next(s for s in L["shapes"] if s["name"] == "Top")
        self.assertTrue((out / top["maps"]["dress_d"]["overlay"]).is_file())
        self.assertNotIn("overlay", top["maps"]["dress_n"])              # 法線圖不出單 shape 疊圖
        # 線框顏色：Top 是第 0 色
        px = set(_img(out / top["maps"]["dress_d"]["overlay"]).getdata())
        self.assertIn(uvmap.color_for(0), px)

    def test_assign_and_unmatched(self):
        other = self.tmp / "in" / "custom.dds"
        other.write_bytes(synth.dds_bytes(self.d_im))
        L = unpack([self.nif], [other], self.tmp / "o2", assign={"Skirt": "custom"})
        sh = {s["name"]: s for s in L["shapes"]}
        self.assertEqual(list(sh["Skirt"]["maps"]), ["custom"])
        self.assertIsNone(sh["Skirt"]["maps"]["custom"]["slot"])
        self.assertEqual(sh["Top"]["maps"], {})
        self.assertEqual(sh["Top"]["unmatched_slots"], [0, 1])
        with self.assertRaises(UnpackError):
            unpack([self.nif], [other], self.tmp / "o3", assign={"Nope": "custom"})
        with self.assertRaises(UnpackError):
            unpack([self.nif], [other], self.tmp / "o3", assign={"Skirt": "nope"})

    def test_data_relative_and_auto_tex(self):
        data = self.tmp / "gamedata"
        (data / "meshes" / "texedit").mkdir(parents=True)
        (data / "Textures" / "TexEdit").mkdir(parents=True)                # 大小寫不同也要找得到
        (data / "meshes" / "texedit" / "dress.nif").write_bytes(synth.dress_nif())
        (data / "Textures" / "TexEdit" / "Dress_D.dds").write_bytes(synth.dds_bytes(self.d_im))
        rc, out, err = run_cli(["unpack", "--data", str(data), "--nif", "meshes\\texedit\\dress.nif", "--auto-tex",
                                "--out", str(self.tmp / "auto")])
        self.assertEqual(rc, 0, err)
        r = json.loads(out)
        self.assertEqual(sorted(r["textures"]), ["dress_d"])                # key 取自貼圖槽路徑
        self.assertEqual(r["missing_textures"], ["textures\\texedit\\dress_n.dds"])       # 法線圖沒放，記下不中斷
        self.assertEqual(r["shapes"]["dress.Top"]["textures"], ["dress_d"])
        L = json.loads((self.tmp / "auto" / "layout.json").read_text("utf-8"))
        self.assertIn(str(data), L["textures"]["dress_d"]["source"])
        rc, _o, err = run_cli(["unpack", "--nif", "meshes/texedit/dress.nif", "--auto-tex", "--out", str(self.tmp / "a2")])
        self.assertEqual(rc, 2)                                             # 沒給 --data：相對路徑找不到
        rc, _o, err = run_cli(["unpack", "--nif", str(self.nif), "--out", str(self.tmp / "a3")])
        self.assertEqual(rc, 2)                                             # 沒 --tex 也沒 --auto-tex

    def test_writeguard(self):
        with self.assertRaises(WriteGuardError):
            self.do_unpack(out="mo2/mods/x")
        with self.assertRaises(WriteGuardError):
            self.do_unpack(out="Data/textures")
        with self.assertRaises(WriteGuardError):
            self.do_unpack(out="_testlayer/x")
        (self.tmp / "o").mkdir()
        (self.tmp / "o" / "layout.json").symlink_to(self.tmp / "elsewhere.json")
        with self.assertRaises(WriteGuardError):
            self.do_unpack(out="o")
        self.assertFalse((self.tmp / "elsewhere.json").exists())

    def test_cli_unpack(self):
        rc, out, err = run_cli(["unpack", "--nif", str(self.nif), "--tex", str(self.dds_d), "--out",
                                str(self.tmp / "c")])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["shapes"]["dress.Top"]["islands"], 2)
        rc, _o, err = run_cli(["unpack", "--nif", str(self.nif), "--tex", str(self.dds_d), "--out",
                               str(self.tmp / "mo2" / "x")])
        self.assertEqual(rc, 2)
        self.assertIn("拒絕", err)
        bad = self.tmp / "in" / "bad.dds"
        bad.write_bytes(b"DDS " + b"\0" * 10)
        rc, _o, _e = run_cli(["unpack", "--nif", str(self.nif), "--tex", str(bad), "--out", str(self.tmp / "c2")])
        self.assertEqual(rc, 2)


class PackTest(Base):
    def edited_png(self, name="e.png", alpha_box=None):
        im = self.d_im.copy()
        im.paste((255, 0, 0, im.getpixel((10, 10))[3]), (10, 10, 11, 11))     # 改一個像素的 RGB，不動 alpha
        if alpha_box:
            a = im.getchannel("A")
            a.paste(0, alpha_box)
            im.putalpha(a)
        p = self.tmp / name
        im.save(p)
        return p, im

    def test_pack_roundtrip_mips_and_report(self):
        from ai3dlib import dds as ddslib
        p, im = self.edited_png()
        rep = pack(p, self.dds_d, self.tmp / "st" / "dress_d.dds")
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["alpha_changed_pixels"], 0)
        self.assertEqual(rep["rgb_changed_pixels"], 1)
        data = (self.tmp / "st" / "dress_d.dds").read_bytes()
        info = ddslib.read_dds(data)
        self.assertEqual((info.width, info.height, info.mips, info.format), (S, S, 8, "RGBA32"))
        self.assertEqual((rep["out_dds"]["width"], rep["ref_dds"]["format"]), (S, "RGBA32"))
        self.assertEqual(ddslib.read_image(data).tobytes(), im.tobytes())
        self.assertEqual(data, ddslib.write_rgba(im, mips=True))

    def test_pack_alpha_changes(self):
        p, _im = self.edited_png(alpha_box=(0, 0, 4, 2))
        rep = pack(p, self.dds_d, self.tmp / "a.dds")
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["alpha_changed_pixels"], 8)
        self.assertEqual(rep["alpha_changed_bbox"], [0, 0, 4, 2])
        rep = pack(p, self.dds_d, self.tmp / "b.dds", fail_on_alpha=True)
        self.assertFalse(rep["ok"])
        self.assertFalse((self.tmp / "b.dds").exists())
        rep = pack(p, self.dds_d, self.tmp / "c.dds", restore_alpha=True)
        self.assertTrue(rep["alpha_restored"])
        from ai3dlib import dds as ddslib
        got = ddslib.read_image((self.tmp / "c.dds").read_bytes())
        self.assertEqual(got.getchannel("A").tobytes(), self.d_im.getchannel("A").tobytes())

    def test_pack_size_mismatch_and_cli(self):
        small = self.tmp / "small.png"
        self.d_im.resize((64, 64)).save(small)
        rep = pack(small, self.dds_d, self.tmp / "x.dds")
        self.assertFalse(rep["ok"])
        self.assertIn("尺寸不同", rep["error"])
        self.assertFalse((self.tmp / "x.dds").exists())
        rc, out, _e = run_cli(["pack", str(small), "--ref", str(self.dds_d), "--out", str(self.tmp / "x.dds")])
        self.assertEqual(rc, 1)
        p, _im = self.edited_png(alpha_box=(0, 0, 1, 1))
        rc, out, err = run_cli(["pack", str(p), "--ref", str(self.dds_d), "--out", str(self.tmp / "y.dds"),
                                "--report", str(self.tmp / "y.json")])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads((self.tmp / "y.json").read_text("utf-8"))["alpha_changed_pixels"], 1)
        rc, _o, _e = run_cli(["pack", str(p), "--ref", str(self.dds_d), "--out", str(self.tmp / "z.dds"),
                              "--fail-on-alpha"])
        self.assertEqual(rc, 1)
        rc, _o, err = run_cli(["pack", str(p), "--ref", str(self.dds_d), "--out", str(self.tmp / "mo2" / "m.dds")])
        self.assertEqual(rc, 2)
        self.assertIn("拒絕", err)


class EditTest(Base):
    def setUp(self):
        super().setUp()
        self.L = self.do_unpack()
        self.ops_dir = self.tmp / "ops"
        self.ops_dir.mkdir()

    def run_ops(self, ops, name="r.png", **extra):
        doc = {"format": "texedit-ops/1", "layout": "../out/layout.json", "texture": "dress_d", "ops": ops, **extra}
        p = self.ops_dir / (name + ".ops.json")
        p.write_text(json.dumps(doc), "utf-8")
        rec = edit.edit(p, self.tmp / "ed" / name)
        return rec, _img(self.tmp / "ed" / name)

    def mask(self, shape, island=None):
        sh = next(s for s in self.L["shapes"] if s["name"] == shape)["maps"]["dress_d"]
        rel = sh["mask"] if island is None else sh["islands"][island]["mask"]
        return _img(self.tmp / "out" / rel)

    def changed_px(self, im):
        return {(x, y) for y in range(S) for x in range(S)
                if im.getpixel((x, y))[:3] != self.d_im.getpixel((x, y))[:3]}

    def test_fill_island_only_alpha_kept_and_by_shape(self):
        rec, im = self.run_ops([{"op": "fill", "target": {"shape": "Top", "island": 0}, "color": "#ff0000"}])
        m = self.mask("Top", 0)
        ch = self.changed_px(im)
        self.assertTrue(ch)
        self.assertTrue(all(m.getpixel(p) == 255 for p in ch))
        self.assertEqual(im.getpixel((20, 20))[:3], (255, 0, 0))
        self.assertEqual(im.getchannel("A").tobytes(), self.d_im.getchannel("A").tobytes())
        self.assertEqual(rec["by_shape"]["dress.Top"]["i0"], rec["changed_pixels"])
        self.assertNotIn("i1", rec["by_shape"]["dress.Top"])
        self.assertIn("dress.Ribbon", rec["by_shape"])                 # 共用 texel 被波及，要看得到
        self.assertNotIn("dress.Skirt", rec["by_shape"])

    def test_exclude_grow_feather_union_rect(self):
        rec, _im = self.run_ops([{"op": "fill", "color": "#00ff00",
                                  "target": {"shape": "dress.Top", "island": [0, 1], "exclude": {"shape": "Ribbon"}}}])
        self.assertNotIn("dress.Ribbon", rec["by_shape"])
        self.assertEqual(sorted(rec["by_shape"]["dress.Top"]), ["i0", "i1", "shape"])
        base = edit.Ctx(self.ops_dir, self.L, self.tmp / "out", "dress_d", (S, S))
        m0 = edit.target_mask(base, {"shape": "Top", "island": 0})
        g = edit.target_mask(base, {"shape": "Top", "island": 0, "grow": 2})
        e = edit.target_mask(base, {"shape": "Top", "island": 0, "grow": -2})
        n = lambda m: m.histogram()[255]  # noqa: E731
        self.assertLess(n(e), n(m0))
        self.assertLess(n(m0), n(g))
        f = edit.target_mask(base, {"shape": "Top", "island": 0, "feather": 2})
        self.assertGreater(len(set(f.getdata())), 2)
        u = edit.target_mask(base, [{"rect_px": [0, 0, 10, 10]}, {"rect_uv": [0.5, 0.5, 0.75, 0.75]}])
        self.assertEqual(n(u), 100 + 32 * 32)
        self.assertEqual(n(edit.target_mask(base, {"all": True})), S * S)

    def test_gradient(self):
        _rec, im = self.run_ops([{"op": "gradient", "target": {"rect_px": [0, 0, 100, 10]},
                                  "from": "#000000", "to": "#ffffff", "angle": 0}])
        self.assertLess(im.getpixel((0, 5))[0], 5)
        self.assertGreater(im.getpixel((99, 5))[0], 250)
        self.assertAlmostEqual(im.getpixel((50, 5))[0], 128, delta=6)
        _rec, im = self.run_ops([{"op": "gradient", "target": {"rect_px": [0, 0, 10, 100]}, "angle": 90,
                                  "stops": [[0, "#ff0000"], [0.5, "#00ff00"], [1, "#0000ff"]]}], name="g2.png")
        self.assertEqual(max(range(3), key=lambda k: im.getpixel((5, 1))[k]), 0)
        self.assertEqual(max(range(3), key=lambda k: im.getpixel((5, 50))[k]), 1)
        self.assertEqual(max(range(3), key=lambda k: im.getpixel((5, 98))[k]), 2)

    def test_pattern_tiles_inside_mask(self):
        tile = Image.new("RGBA", (2, 2), (255, 255, 255, 255))
        tile.putpixel((0, 0), (0, 0, 0, 255))
        tile.putpixel((1, 1), (0, 0, 0, 255))
        tile.save(self.ops_dir / "tile.png")
        _rec, im = self.run_ops([{"op": "pattern", "target": {"shape": "Skirt"}, "image": "tile.png", "scale": 2,
                                  "resample": "nearest"}])
        # scale 2 → 4×4 一格，週期 4，原點在 (0,0)
        self.assertEqual(im.getpixel((40, 80))[:3], (0, 0, 0))
        self.assertEqual(im.getpixel((42, 80))[:3], (255, 255, 255))
        self.assertEqual(im.getpixel((44, 80))[:3], (0, 0, 0))
        self.assertTrue(all(self.mask("Skirt").getpixel(p) == 255 for p in self.changed_px(im)))

    def test_decal_position_scale_rotate_clipped(self):
        dec = Image.new("RGBA", (5, 5), (0, 0, 255, 255))
        dec.save(self.ops_dir / "logo.png")
        # 中心在 Top 正面右緣（x≈58）：右半邊落在 island 外，要被裁掉
        _rec, im = self.run_ops([{"op": "decal", "target": {"shape": "Top", "island": 0}, "image": "logo.png",
                                  "center": [57, 30], "scale": 2}])
        self.assertEqual(im.getpixel((55, 30))[:3], (0, 0, 255))
        self.assertNotEqual(im.getpixel((60, 30))[:3], (0, 0, 255))
        ch = self.changed_px(im)
        self.assertTrue(all(52 <= x <= 62 and 25 <= y <= 35 for x, y in ch))
        _rec, im = self.run_ops([{"op": "decal", "target": {"all": True}, "image": "logo.png",
                                  "center_uv": [0.5, 0.5], "size": [20, 2], "rotate": 90}], name="d2.png")
        self.assertEqual(im.getpixel((64, 56))[:3], (0, 0, 255))        # 轉 90 度後是直條
        self.assertNotEqual(im.getpixel((56, 64))[:3], (0, 0, 255))

    def test_recolor_keeps_shading_and_opacity(self):
        _rec, im = self.run_ops([{"op": "recolor", "target": {"shape": "Skirt"}, "color": "#ff0000"}])
        a, b = im.getpixel((10, 80)), im.getpixel((18, 80))               # 原圖這兩點明暗不同
        self.assertNotEqual(self.d_im.getpixel((10, 80))[:3], self.d_im.getpixel((18, 80))[:3])
        self.assertNotEqual(a[:3], b[:3])
        for p in (a, b):
            self.assertEqual(max(range(3), key=lambda k: p[k]), 0)       # 色相變紅
            self.assertEqual(p[1], p[2])
        self.assertEqual(max(a[:3]), max(self.d_im.getpixel((10, 80))[:3]))   # 明度（HSV V）照原圖
        for mode in ("multiply", "overlay", "colorize", "hue", "screen"):
            _r, im2 = self.run_ops([{"op": "recolor", "target": {"shape": "Skirt"}, "color": "#ff0000",
                                     "blend": mode}], name=f"{mode}.png")
            self.assertTrue(self.changed_px(im2), mode)
        _r, half = self.run_ops([{"op": "fill", "target": {"rect_px": [0, 0, 4, 4]}, "color": "#ffffff",
                                  "opacity": 0.5}], name="half.png")
        o = self.d_im.getpixel((1, 1))[0]
        self.assertAlmostEqual(half.getpixel((1, 1))[0], (o + 255) / 2, delta=1)

    def test_record_replay_and_changed_input(self):
        tile = Image.new("RGBA", (3, 3), (10, 200, 30, 255))
        tile.save(self.ops_dir / "t.png")
        rec, _im = self.run_ops([{"op": "fill", "target": {"shape": "Top"}, "color": "navy"},
                                 {"op": "pattern", "target": {"shape": "Skirt"}, "image": "t.png"}], name="rp.png")
        self.assertEqual([s["op"] for s in rec["steps"]], ["fill", "pattern"])
        self.assertTrue(all(s["changed_pixels"] > 0 for s in rec["steps"]))
        recp = self.tmp / "ed" / "rp.png.record.json"
        self.assertEqual(json.loads(recp.read_text("utf-8"))["output_pixels_sha256"], rec["output_pixels_sha256"])
        r = edit.replay(recp)
        self.assertTrue(r["same_output"])
        self.assertEqual(r["changed_inputs"], [])
        Image.new("RGBA", (3, 3), (200, 0, 0, 255)).save(self.ops_dir / "t.png")
        r = edit.replay(recp)
        self.assertFalse(r["same_output"])
        self.assertEqual(r["changed_inputs"], ["t.png"])
        rc, out, _e = run_cli(["replay", str(recp)])
        self.assertEqual(rc, 1)

    def test_edit_then_pack_alpha_untouched_and_diff(self):
        self.run_ops([{"op": "fill", "target": {"shape": "Skirt"}, "color": "#123456"}], name="p.png")
        rep = pack(self.tmp / "ed" / "p.png", self.dds_d, self.tmp / "st" / "dress_d.dds")
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["alpha_changed_pixels"], 0)
        d = edit.diff(self.tmp / "out" / "textures" / "dress_d.png", self.tmp / "ed" / "p.png",
                      self.tmp / "out" / "layout.json", "dress_d", self.tmp / "ed" / "diff.png")
        self.assertEqual(d["rgb_changed_pixels"], rep["rgb_changed_pixels"])
        self.assertIn("dress.Skirt", d["by_shape"])
        self.assertTrue((self.tmp / "ed" / "diff.png").is_file())

    def test_errors_and_cli(self):
        bad = [
            [{"op": "fill", "target": {"shape": "Nope"}, "color": "red"}],
            [{"op": "fill", "target": {"shape": "Top", "island": 9}, "color": "red"}],
            [{"op": "smudge", "target": {"all": True}}],
            [{"op": "fill", "target": {"all": True}}],                                # 缺 color
            [{"op": "fill", "target": {"all": True}, "color": "notacolor"}],
            [{"op": "fill", "target": {"all": True}, "color": "red", "blend": "dodge"}],
            [{"op": "fill", "target": {"all": True}, "color": "red", "opacity": 2}],
            [{"op": "decal", "target": {"all": True}, "image": "missing.png", "center": [0, 0]}],
        ]
        Image.new("L", (8, 8), 255).save(self.ops_dir / "small.png")
        bad.append([{"op": "fill", "target": {"mask": "small.png"}, "color": "red"}])  # 尺寸不同
        for i, ops in enumerate(bad):
            p = self.ops_dir / f"bad{i}.json"
            p.write_text(json.dumps({"format": "texedit-ops/1", "layout": "../out/layout.json",
                                     "texture": "dress_d", "ops": ops}), "utf-8")
            rc, _o, err = run_cli(["edit", str(p), "--out", str(self.tmp / "ed" / f"bad{i}.png")])
            self.assertEqual(rc, 2, (i, err))
            self.assertIn("ops[0]", err)
            self.assertFalse((self.tmp / "ed" / f"bad{i}.png").exists())
        p = self.ops_dir / "nofmt.json"
        p.write_text(json.dumps({"ops": []}), "utf-8")
        self.assertEqual(run_cli(["edit", str(p), "--out", str(self.tmp / "ed" / "x.png")])[0], 2)
        good = self.ops_dir / "good.json"
        good.write_text(json.dumps({"format": "texedit-ops/1", "layout": "../out/layout.json", "texture": "dress_d",
                                    "ops": [{"op": "fill", "target": {"shape": "Top"}, "color": "red"}]}), "utf-8")
        rc, out, err = run_cli(["edit", str(good), "--out", str(self.tmp / "ed" / "good.png")])
        self.assertEqual(rc, 0, err)
        self.assertGreater(json.loads(out)["changed_pixels"], 0)
        self.assertEqual(run_cli(["edit", str(good), "--out", str(self.tmp / "mo2" / "g.png")])[0], 2)
        rc, out, _e = run_cli(["diff", str(self.tmp / "out" / "textures" / "dress_d.png"),
                               str(self.tmp / "ed" / "good.png")])
        self.assertEqual(rc, 0)


class DemoTest(Base):
    def test_demo_end_to_end(self):
        rc, out, err = run_cli(["demo", "--out", str(self.tmp / "demo")])
        self.assertEqual(rc, 0, err)
        r = json.loads(out)
        self.assertTrue(r["pack"]["ok"])
        self.assertEqual(r["pack"]["alpha_changed_pixels"], 0)
        self.assertEqual(len(r["steps"]), 4)
        self.assertTrue(all(s["changed_pixels"] > 0 for s in r["steps"]))
        self.assertNotIn("dress.Ribbon", r["by_shape"].get("dress.Top", {}))
        self.assertTrue(edit.replay(self.tmp / "demo" / "edit" / "dress_d.png.record.json")["same_output"])


# ---------------------------------------------------------------- 審查意見（review-texedit）的修正，每條一測


def _quad(u0, v0, u1, v1, base=0):
    return [(u0, v0), (u1, v0), (u0, v1), (u1, v1)], [(base, base + 1, base + 3), (base, base + 3, base + 2)]


def _shape(uvs, tris):
    return uvmap.Shape("t.x", "t.nif", "x", "BSTriShape", uvs, tris)


class ReviewFixTest(Base):
    def setUp(self):
        super().setUp()
        self.L = self.do_unpack()
        self.ops_dir = self.tmp / "ops"
        self.ops_dir.mkdir()

    def ops_file(self, ops, name="r", **extra):
        doc = {"format": "texedit-ops/1", "layout": "../out/layout.json", "texture": "dress_d", "ops": ops, **extra}
        p = self.ops_dir / (name + ".ops.json")
        p.write_text(json.dumps(doc), "utf-8")
        return p

    def ctx(self):
        return edit.Ctx(self.ops_dir, self.L, self.tmp / "out", "dress_d", (S, S))

    # 擋 1：decal 半透明像素照 alpha 混合，不是 alpha 平方、RGB 壓暗
    def test_decal_half_alpha_blends_normally(self):
        Image.new("RGBA", (6, 6), (255, 255, 255, 128)).save(self.ops_dir / "half.png")
        p = self.ops_file([{"op": "decal", "target": {"all": True}, "image": "half.png", "center": [40, 40]}])
        edit.edit(p, self.tmp / "ed" / "half.png")
        im = _img(self.tmp / "ed" / "half.png")
        for x, y in ((38, 38), (41, 40)):
            o = self.d_im.getpixel((x, y))
            got = im.getpixel((x, y))
            for k in range(3):
                self.assertAlmostEqual(got[k], o[k] + (255 - o[k]) * 128 / 255, delta=1.5, msg=(x, y, got, o))

    def test_decal_rotated_edges_not_darkened(self):
        w = Image.new("RGBA", (16, 16), (0, 0, 0, 0))                    # 透明框裡一塊白：轉了以後邊是半透明
        w.paste((255, 255, 255, 255), (3, 3, 13, 13))
        w.save(self.ops_dir / "w.png")
        p = self.ops_file([{"op": "decal", "target": {"all": True}, "image": "w.png", "center": [64, 64],
                            "rotate": 15}])
        edit.edit(p, self.tmp / "ed" / "w.png")
        im = _img(self.tmp / "ed" / "w.png")
        darker = [(x, y) for y in range(48, 81) for x in range(48, 81)
                  if any(im.getpixel((x, y))[k] < self.d_im.getpixel((x, y))[k] for k in range(3))]
        self.assertEqual(darker, [])                                     # 白色疊上去只會變亮

    # 擋 2：空選區拒絕；不空卻沒改像素的步驟記 warnings，--strict 時 rc 1
    def test_empty_selection_refused(self):
        for i, tgt in enumerate([{"rect_px": [200, 200, 220, 220]}, {"shape": "Top", "exclude": {"shape": "Top"}}]):
            p = self.ops_file([{"op": "fill", "target": tgt, "color": "red"}], name=f"empty{i}")
            rc, _o, err = run_cli(["edit", str(p), "--out", str(self.tmp / "ed" / f"e{i}.png")])
            self.assertEqual(rc, 2, err)
            self.assertIn("選區是空的", err)
            self.assertFalse((self.tmp / "ed" / f"e{i}.png").exists())

    def test_noop_step_warns_and_strict(self):
        same = "#%02x%02x%02x" % self.d_im.getpixel((0, 0))[:3]
        p = self.ops_file([{"op": "fill", "target": {"rect_px": [0, 0, 1, 1]}, "color": same},
                           {"op": "fill", "target": {"rect_px": [4, 4, 6, 6]}, "color": "red"}])
        rc, out, err = run_cli(["edit", str(p), "--out", str(self.tmp / "ed" / "n.png")])
        self.assertEqual(rc, 0, err)
        r = json.loads(out)
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("ops[0]", r["warnings"][0])
        rec = json.loads((self.tmp / "ed" / "n.png.record.json").read_text("utf-8"))
        self.assertEqual(rec["warnings"], r["warnings"])
        rc, _o, err = run_cli(["edit", str(p), "--out", str(self.tmp / "ed" / "n2.png"), "--strict"])
        self.assertEqual(rc, 1, err)
        good = self.ops_file([{"op": "fill", "target": {"rect_px": [4, 4, 6, 6]}, "color": "red"}], name="g")
        self.assertEqual(run_cli(["edit", str(good), "--out", str(self.tmp / "ed" / "g.png"), "--strict"])[0], 0)

    # 擋 3：同 shape 裡 UV 疊在一起、3D 不相連的兩片算兩塊，重疊列進 overlaps；接縫照位置＋UV 焊回
    def test_islands_split_stacked_uv_by_position(self):
        uv_a, t_a = _quad(0.1, 0.1, 0.4, 0.4)
        uv_b, t_b = _quad(0.1, 0.1, 0.4, 0.4, base=4)
        pos = [(u, 0.0, -v) for u, v in uv_a] + [(u + 5.0, 0.0, -v) for u, v in uv_a]   # 右邊那片在 x+5
        self.assertEqual(len(uvmap.find_islands(uv_a + uv_b, t_a + t_b, pos)), 2)
        # 只在一個 UV 角點相碰、3D 不相連：兩塊
        uv_c, t_c = _quad(0.4, 0.4, 0.7, 0.7, base=4)
        pos_c = [(u, 0.0, -v) for u, v in uv_a] + [(u + 5.0, 0.0, -v) for u, v in uv_c]
        self.assertEqual(len(uvmap.find_islands(uv_a + uv_c, t_a + t_c, pos_c)), 2)
        # 接縫：頂點拆開但位置與 UV 都重合，焊回一塊
        uv_l, t_l = _quad(0.1, 0.1, 0.3, 0.4)
        uv_r, t_r = _quad(0.3, 0.1, 0.5, 0.4, base=4)
        pos_s = [(u, 0.0, -v) for u, v in uv_l + uv_r]
        self.assertEqual(len(uvmap.find_islands(uv_l + uv_r, t_l + t_r, pos_s)), 1)
        # 經 NIF 走 unpack：layout 有 2 個 island，且同 shape 重疊列出來
        nif = self.tmp / "in" / "bracers.nif"
        nif.write_bytes(synth.make_nif([{"name": "Bracers", "uvs": uv_a + uv_b, "tris": t_a + t_b,
                                         "verts": pos, "textures": [synth.DRESS_D] + [""] * 8}]))
        L = unpack([nif], [self.dds_d], self.tmp / "br")
        sh = L["shapes"][0]
        self.assertEqual(len(sh["islands"]), 2)
        ov = [o for o in L["overlaps"] if o["a"] == "bracers.Bracers#i0" and o["b"] == "bracers.Bracers#i1"]
        self.assertEqual(len(ov), 1)
        self.assertGreater(ov[0]["pixels"], 100)

    # 不擋 4：遮罩只涵蓋中心落在三角形內的 texel；對接的兩塊不重疊；細三角形至少一像素
    def test_mask_exact_texels_and_thin_triangles(self):
        uv, t = _quad(0.25, 0.25, 0.5, 0.5)
        m = uvmap.rasterize(_shape(uv, t), range(2), (8, 8))
        self.assertEqual(m.histogram()[255], 4)
        self.assertEqual(m.getbbox(), (2, 2, 4, 4))
        uv_l, t_l = _quad(0.0, 0.0, 0.5, 1.0)
        uv_r, t_r = _quad(0.5, 0.0, 1.0, 1.0)
        a = uvmap.rasterize(_shape(uv_l, t_l), range(2), (64, 64))
        b = uvmap.rasterize(_shape(uv_r, t_r), range(2), (64, 64))
        self.assertEqual(uvmap.overlap_pixels(a, b), 0)
        self.assertEqual(a.histogram()[255] + b.histogram()[255], 64 * 64)
        thin = _shape([(0.1, 0.5), (0.9, 0.5), (0.5, 0.501)], [(0, 1, 2)])
        self.assertGreater(uvmap.rasterize(thin, [0], (64, 64)).histogram()[255], 0)
        dot = _shape([(0.5, 0.5), (0.5001, 0.5), (0.5, 0.5001)], [(0, 1, 2)])
        self.assertGreater(uvmap.rasterize(dot, [0], (64, 64)).histogram()[255], 0)

    # 不擋 5：法線圖預設拒絕，清單寫明 allow_normal 才准
    def test_normal_map_refused_unless_allowed(self):
        p = self.ops_file([{"op": "fill", "target": {"shape": "Top"}, "color": "red"}], name="n", texture="dress_n")
        rc, _o, err = run_cli(["edit", str(p), "--out", str(self.tmp / "ed" / "nm.png")])
        self.assertEqual(rc, 2, err)
        self.assertIn("allow_normal", err)
        p = self.ops_file([{"op": "fill", "target": {"shape": "Top"}, "color": "red"}], name="n2", texture="dress_n",
                          allow_normal=True)
        rc, _o, err = run_cli(["edit", str(p), "--out", str(self.tmp / "ed" / "nm2.png")])
        self.assertEqual(rc, 0, err)

    # 不擋 6：先 grow／feather 再扣 exclude，被扣掉的區域不會長回去
    def test_exclude_applied_after_grow_and_feather(self):
        ctx = self.ctx()
        rib = edit.target_mask(ctx, {"shape": "Ribbon"})
        for extra in ({"grow": 2}, {"feather": 2}, {"grow": 1, "feather": 1}):
            m = edit.target_mask(ctx, {"shape": "Top", "island": 0, "exclude": {"shape": "Ribbon"}, **extra})
            hit = [p for p in range(S * S) if rib.getdata()[p] == 255 and m.getdata()[p] > 0]
            self.assertEqual(hit, [], extra)
        g = edit.target_mask(ctx, {"shape": "Top", "island": 0, "grow": 2, "exclude": {"shape": "Ribbon"}})
        self.assertGreater(g.histogram()[255], edit.target_mask(ctx, {"shape": "Top", "island": 0,
                                                                         "exclude": {"shape": "Ribbon"}}).histogram()[255])

    # 不擋 7：硬連結與 _layer-apply 根要擋；pack 的 mip 內容要對
    def test_writeguard_hardlink_and_layer_apply(self):
        with self.assertRaises(WriteGuardError):
            self.do_unpack(out="_layer-apply/x")
        (self.tmp / "h").mkdir()
        src = self.tmp / "keep.json"
        src.write_text("{}", "utf-8")
        os.link(src, self.tmp / "h" / "layout.json")
        with self.assertRaises(WriteGuardError):
            self.do_unpack(out="h")
        self.assertEqual(src.read_text("utf-8"), "{}")
        self.assertFalse((self.tmp / "h" / "textures").exists())        # 先檢查再寫：沒留下一半產物

    def test_pack_mip1_content(self):
        p = self.tmp / "e.png"
        self.d_im.save(p)
        pack(p, self.dds_d, self.tmp / "m.dds")
        data = (self.tmp / "m.dds").read_bytes()
        off = 128 + S * S * 4
        mip1 = data[off:off + (S // 2) * (S // 2) * 4]
        r, g, b, a = self.d_im.resize((S // 2, S // 2), Image.BOX).split()
        self.assertEqual(mip1, Image.merge("RGBA", (b, g, r, a)).tobytes())     # BGRA

    # 不擋 8：record 存清單檔絕對路徑（換 cwd 也能 replay）與 Pillow 版本
    def test_record_abs_ops_path_and_pillow_version(self):
        import PIL
        self.ops_file([{"op": "fill", "target": {"shape": "Top"}, "color": "red"}], name="rel")
        old = os.getcwd()
        try:
            os.chdir(self.ops_dir)
            rec = edit.edit("rel.ops.json", self.tmp / "ed" / "rel.png")
            os.chdir(self.tmp)
            self.assertTrue(Path(rec["ops_file"]).is_absolute())
            self.assertEqual(rec["pillow"], PIL.__version__)
            r = edit.replay(self.tmp / "ed" / "rel.png.record.json")
        finally:
            os.chdir(old)
        self.assertTrue(r["same_output"])
        self.assertEqual(r["warnings"], [])
        recp = self.tmp / "ed" / "rel.png.record.json"
        d = json.loads(recp.read_text("utf-8"))
        d["pillow"] = "0.0.0"
        recp.write_text(json.dumps(d), "utf-8")
        self.assertTrue(any("Pillow" in w for w in edit.replay(recp)["warnings"]))

    # 不擋 9：--out 是別的非空夾就拒；重跑同夾時舊的、這次沒產出的檔列在 stale_files
    def test_unpack_out_dir_rules_and_stale_files(self):
        (self.tmp / "busy").mkdir()
        (self.tmp / "busy" / "notes.txt").write_text("x", "utf-8")
        with self.assertRaises(UnpackError):
            self.do_unpack(out="busy")
        lp = self.tmp / "out" / "layout.json"
        L = json.loads(lp.read_text("utf-8"))
        self.assertEqual(L["stale_files"], [])
        top = next(s for s in L["shapes"] if s["name"] == "Top")["maps"]["dress_d"]
        top["islands"].append({"index": 9, "mask": "masks/128x128/dress.Top.i9.png"})
        lp.write_text(json.dumps(L), "utf-8")
        (self.tmp / "out" / "masks" / "128x128" / "dress.Top.i9.png").write_bytes(b"old")
        rc, out, err = run_cli(["unpack", "--nif", str(self.nif), "--tex", str(self.dds_d), "--tex", str(self.dds_n),
                                "--out", str(self.tmp / "out")])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["stale_files"], ["masks/128x128/dress.Top.i9.png"])


if __name__ == "__main__":
    unittest.main()
