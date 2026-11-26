"""preview 測試：合成洋裝＋身體（preview.synth），只用 tempdir；不碰 MO2、Data、profile。

跑：在 repo 根 `python3 -m unittest discover -s tests -v`
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from preview import cli, raster, render, synth
from preview.paths import OutDir, WriteGuardError

SZ = 96


def _img(path):
    with Image.open(path) as im:
        im.load()
        return im.convert("RGB")


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        env = {"MO2_DIR": str(self.tmp / "mo2"), "SKYRIM_DATA": str(self.tmp / "Data"),
               "LAYER_APPLY_ROOT": str(self.tmp / "_layer-apply"), "TESTLAYER_ROOT": str(self.tmp / "_testlayer")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        (self.tmp / "in").mkdir()
        self.inp = {k: self.tmp / v for k, v in
                    synth.write_inputs(lambda r, b: (self.tmp / r).write_bytes(b), "in/").items()}
        self.others = [str(self.inp[k]) for k in ("lace_d.dds", "sash_d.dds", "body_d.dds")]

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()

    def opts(self, out="out", tex=None, **kw):
        kw.setdefault("nifs", [str(self.inp["body.nif"]), str(self.inp["dress.nif"])])
        kw.setdefault("size", SZ)
        return render.Options(out=str(self.tmp / out),
                              tex=[str(self.inp["dress_d.dds"])] + self.others if tex is None else tex, **kw)

    def px(self, out, view, point, frame):
        x, y = raster.project(point, raster.VIEWS[view], SZ, raster.Frame(tuple(frame[:3]), frame[3]))
        return _img(self.tmp / out / f"{view}.png").getpixel((int(x), int(y)))


class RasterTest(unittest.TestCase):
    QUAD = [(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)]
    TRIS = [(0, 1, 2), (0, 2, 3)]
    UVS = [(0, 1), (1, 1), (1, 0), (0, 0)]
    F = raster.Frame((0, 0, 0), 2.0)

    def _one(self, mode, alpha, test=False, behind=None):
        sampler = lambda u, v: (250, 10, 10, alpha)  # noqa: E731
        layers = []
        if behind:
            layers.append(raster.Layer([(x, -1, z) for x, _y, z in self.QUAD], self.TRIS, behind))
        layers.append(raster.Layer(self.QUAD, self.TRIS, uvs=self.UVS, sampler=sampler, mode=mode, test=test))
        buf, cover = raster.render(layers, 0, 32, self.F, bg=(0, 0, 0))
        o = (16 * 32 + 16) * 3
        return tuple(buf[o:o + 3]), cover

    def test_deterministic_and_coverage(self):
        a, ca = self._one("opaque", 255)
        b, cb = self._one("opaque", 255)
        self.assertEqual((a, ca), (b, cb))
        self.assertGreater(a[0], 100)
        self.assertGreater(ca[0], 100)

    def test_alpha_modes(self):
        self.assertEqual(self._one("opaque", 0)[0][0] > 100, True)           # 沒 alpha property：不看 alpha
        self.assertEqual(self._one("test", 100)[0], (0, 0, 0))               # < threshold 丟掉
        self.assertGreater(self._one("test", 200)[0][0], 100)
        mixed, cover = self._one("blend", 128, behind=(0, 0, 200))            # 半透明疊在後面的藍上
        self.assertGreater(mixed[0], 40)
        self.assertGreater(mixed[2], 40)
        self.assertGreater(cover[1], 0)                                       # 疊色像素也算進 coverage
        self.assertEqual(self._one("blend", 100, test=True)[0], (0, 0, 0))   # blend＋test：先 test

    def test_project_matches_render(self):
        x, y = raster.project((0, 0, 0), 0, 32, self.F)
        self.assertEqual((x, y), (16, 16))
        x, y = raster.project((1, 0, 0), 0, 32, self.F)                      # 角色右手在畫面左
        self.assertLess(x, 16)
        x, _ = raster.project((1, 0, 0), 180, 32, self.F)
        self.assertGreater(x, 16)

    # ---- fix-preview：左右、深度、blend 排序、top-left、打光方向都拿 render() 的像素鎖

    def _px(self, buf, size, x, y):
        o = (int(y) * size + int(x)) * 3
        return tuple(buf[o:o + 3])

    def test_render_left_right_asymmetric(self):
        """只在角色右側（+x）放紅片：front 在畫面左半、back 在右半；只在角色前方（+y）放綠片：side 在畫面右半。"""
        right = raster.Layer([(0.3, 0, -0.5), (1, 0, -0.5), (1, 0, 0.5), (0.3, 0, 0.5)], self.TRIS, (250, 0, 0))
        front = raster.Layer([(0, 0.3, -0.5), (0, 1, -0.5), (0, 1, 0.5), (0, 0.3, 0.5)], self.TRIS, (0, 250, 0))
        for yaw, layer, pt, want_left in ((0, right, (0.65, 0, 0), True), (180, right, (0.65, 0, 0), False),
                                          (90, front, (0, 0.65, 0), False), (-90, front, (0, 0.65, 0), True)):
            buf, _ = raster.render([layer], yaw, 32, self.F, bg=(0, 0, 0))
            x, y = raster.project(pt, yaw, 32, self.F)
            self.assertEqual(x < 16, want_left, (yaw, x))
            hit = self._px(buf, 32, x, y)
            self.assertGreater(max(hit), 150, (yaw, hit))                     # render 與 project 同一點
            mx = 31 - int(x)                                                  # 左右鏡像的那一點是背景
            self.assertEqual(self._px(buf, 32, mx, y), (0, 0, 0), (yaw, mx))
            half = range(0, 16) if want_left else range(16, 32)
            lit = {px for px in range(32) if self._px(buf, 32, px, 16) != (0, 0, 0)}
            self.assertTrue(lit and lit <= set(half), (yaw, sorted(lit)))

    def test_depth_near_wins_regardless_of_order(self):
        """前方不透明片先畫、後方片後畫：深度測試要讓前方留下。"""
        near = raster.Layer(self.QUAD, self.TRIS, (250, 0, 0))
        far = raster.Layer([(x, -1, z) for x, _y, z in self.QUAD], self.TRIS, (0, 0, 250))
        for layers in ([near, far], [far, near]):
            buf, cover = raster.render(layers, 0, 32, self.F, bg=(0, 0, 0))
            r, _g, b = self._px(buf, 32, 16, 16)
            self.assertGreater(r, 150)
            self.assertEqual(b, 0)
        self.assertEqual(cover[0], 0)                                         # 最後一輪 far 在前：全被擋

    def test_blend_far_to_near(self):
        """同一個 blend shape 裡前紅後藍兩片（輸入順序前片在先）：由遠到近疊，紅要壓過藍。"""
        quad = [(-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1)] + [(x, -1, z) for x, _y, z in self.QUAD]
        tris = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)]
        uvs = [(0.25, 0.5)] * 4 + [(0.75, 0.5)] * 4
        sampler = lambda u, v: (250, 0, 0, 128) if u < 0.5 else (0, 0, 250, 128)  # noqa: E731
        layer = raster.Layer(quad, tris, uvs=uvs, sampler=sampler, mode="blend")
        for yaw, near_red in ((0, True), (180, False)):
            buf, _ = raster.render([layer], yaw, 32, self.F, bg=(0, 0, 0))
            r, _g, b = self._px(buf, 32, 16, 16)
            self.assertEqual(r > b * 1.5, near_red, (yaw, r, b))
            self.assertEqual(b > r * 1.5, not near_red, (yaw, r, b))

    def _grid(self, n, z0=-1.0, z1=1.0):
        pos, tris = [], []
        for j in range(n + 1):
            for i in range(n + 1):
                pos.append((-1 + 2 * i / n, 0, z0 + (z1 - z0) * j / n))
        for j in range(n):
            for i in range(n):
                a = j * (n + 1) + i
                b, c, d = a + 1, a + n + 1, a + n + 2
                tris += [(a, b, d), (a, d, c)] if (i + j) % 2 else [(a, b, c), (b, d, c)]
        return pos, tris

    def test_blend_shared_edges_drawn_once(self):
        """top-left 規則：半透明網格的共用邊不疊兩次色，coverage 也不多算。"""
        sampler = lambda u, v: (250, 250, 250, 100)  # noqa: E731
        for n, size, yaw, fill in ((1, 32, 0, raster.FILL), (2, 64, 0, raster.FILL), (4, 128, 0, raster.FILL),
                                   (1, 256, 0, raster.FILL), (4, 32, 0, 1.0), (3, 97, 30, raster.FILL),
                                   (5, 64, 0, 1.0), (6, 128, -45, 0.77)):
            pos, tris = self._grid(n)
            layer = raster.Layer(pos, tris, uvs=[(0.5, 0.5)] * len(pos), sampler=sampler, mode="blend")
            buf, cover = raster.render([layer], yaw, size, self.F, bg=(0, 0, 0), fill=fill)
            vals = {}
            for i in range(size * size):
                p = tuple(buf[i * 3:i * 3 + 3])
                if p != (0, 0, 0):
                    vals[p] = vals.get(p, 0) + 1
            self.assertEqual(len(vals), 1, (n, size, yaw, fill, vals))
            self.assertEqual(cover[0], sum(vals.values()), (n, size, yaw, fill))

    def test_opaque_shared_edges_no_gaps(self):
        """top-left 規則不能讓共用邊漏掉像素：不透明網格內部每個像素都畫到。"""
        for n, size, fill in ((4, 32, 1.0), (5, 64, 1.0), (3, 97, raster.FILL)):
            pos, tris = self._grid(n)
            buf, cover = raster.render([raster.Layer(pos, tris, (250, 250, 250))], 0, size, self.F,
                                       bg=(0, 0, 0), fill=fill)
            x0, y0 = raster.project((-1, 0, 1), 0, size, self.F, fill)
            x1, y1 = raster.project((1, 0, -1), 0, size, self.F, fill)
            xs, ys = sorted((x0, x1)), sorted((y0, y1))
            for py in range(int(ys[0]) + 1, int(ys[1]) - 1):
                for px in range(int(xs[0]) + 1, int(xs[1]) - 1):
                    self.assertNotEqual(self._px(buf, size, px, py), (0, 0, 0), (n, size, px, py))

    def test_light_from_screen_left(self):
        """光在相機座標 x' 正方向＝畫面左（front 時是角色右側）：朝畫面左前的面比朝畫面右前的亮。"""
        def lum(nx, ny, yaw):
            # 過原點、法線 (nx, ny, 0) 的直立方片；面內水平方向是 (ny, -nx, 0)
            ex, ey = ny, -nx
            quad = [(-ex, -ey, -1), (ex, ey, -1), (ex, ey, 1), (-ex, -ey, 1)]
            buf, _ = raster.render([raster.Layer(quad, self.TRIS, (200, 200, 200))], yaw, 32, self.F, bg=(0, 0, 0))
            return self._px(buf, 32, 16, 16)[0]
        s = 2 ** -0.5
        self.assertGreater(lum(s, s, 0), lum(-s, s, 0) + 40)                 # front：朝角色右（畫面左）較亮
        self.assertGreater(lum(-s, -s, 180), lum(s, -s, 180) + 40)           # back：朝角色左（畫面左）較亮

class ParseTest(unittest.TestCase):
    def test_views(self):
        self.assertEqual(render.parse_views("front,back"), [("front", 0.0), ("back", 180.0)])
        self.assertEqual(render.parse_views("side,-30")[1], ("yawm30", -30.0))
        for bad in ("nope", "", "front,front"):
            with self.assertRaises(render.PreviewError):
                render.parse_views(bad)

    def test_tex(self):
        self.assertEqual(render.parse_tex(["a/b/dress_d.png", "dress.Top=x.dds"]),
                         [("dress_d", "a/b/dress_d.png", False), ("dress.Top", "x.dds", True)])
        with self.assertRaises(render.PreviewError):
            render.parse_tex(["=x.png"])

    def test_tex_file_with_equals(self):
        """FILE 路徑裡有 `=`、整串就是一個存在的檔：當 FILE，不切成 KEY=FILE。"""
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "a=b" / "dress_d.png"
            f.parent.mkdir()
            f.write_bytes(b"x")
            self.assertEqual(render.parse_tex([str(f)]), [("dress_d", str(f), False)])
            self.assertEqual(render.parse_tex([f"dress.Top={f}"]), [("dress.Top", str(f), True)])

class RenderTest(Base):
    def test_front_back_colors_and_json(self):
        doc = render.run(self.opts(views="front,back,side"))
        fr = doc["camera"]["frame"]
        front = self.px("out", "front", (0, 11, 77), fr)
        back = self.px("out", "back", (0, -11, 77), fr)
        self.assertLess(max(front) - min(front), 30)                          # 正面淺灰
        self.assertGreater(back[2], back[0] + 40)                             # 背面深藍
        self.assertEqual(sorted(p.name for p in (self.tmp / "out").iterdir()),
                         ["back.png", "front.png", "preview.json", "sheet.png", "side.png"])
        j = json.loads((self.tmp / "out" / "preview.json").read_text(encoding="utf-8"))
        self.assertEqual(j["format"], render.FORMAT)
        self.assertEqual([s["key"] for s in j["shapes"]], ["body.Body", "dress.Top", "dress.Skirt", "dress.Lace",
                                                          "dress.Sash"])
        modes = {s["key"]: (s["mode"], s["alpha_test"]) for s in j["shapes"]}
        self.assertEqual(modes["dress.Lace"], ("test", True))
        self.assertEqual(modes["dress.Sash"], ("blend", False))
        self.assertEqual(j["missing_textures"], [])
        self.assertEqual({s["texture"]["how"] for s in j["shapes"]}, {"tex-slot"})
        self.assertEqual(len(j["inputs"]["nifs"]), 2)
        self.assertEqual([i["view"] for i in j["images"]], ["front", "back", "side"])
        self.assertTrue(all(n > 0 for i in j["images"] for n in i["coverage"].values()))
        self.assertEqual(j["warnings"], [])

    def test_deterministic_same_sha(self):
        a = render.run(self.opts("a"))
        b = render.run(self.opts("b"))
        self.assertEqual([i["sha256"] for i in a["images"]], [i["sha256"] for i in b["images"]])
        self.assertEqual(a["sheet"]["sha256"], b["sheet"]["sha256"])
        self.assertEqual(_sha(self.tmp / "a" / "preview.json"), _sha(self.tmp / "b" / "preview.json"))

    def test_png_and_dds_same_pixels(self):
        png = self.tmp / "dress_d.png"
        synth.dress_image().save(png)
        a = render.run(self.opts("a"))
        b = render.run(self.opts("b", tex=[str(png)] + self.others))
        self.assertEqual([i["pixels_sha256"] for i in a["images"]], [i["pixels_sha256"] for i in b["images"]])

    def test_shape_key_override_only_that_shape(self):
        red = self.tmp / "red.png"
        Image.new("RGBA", (16, 16), (250, 0, 0, 255)).save(red)
        base = render.run(self.opts("a"))
        fr = base["camera"]["frame"]
        render.run(self.opts("b", tex=[str(self.inp["dress_d.dds"]), f"dress.Top={red}"] + self.others))
        top = self.px("b", "front", (0, 11, 77), fr)
        self.assertGreater(top[0], 150)
        self.assertLess(top[1], 30)
        skirt = (0, 17, 40)
        self.assertEqual(self.px("a", "front", skirt, fr), self.px("b", "front", skirt, fr))
        j = json.loads((self.tmp / "b" / "preview.json").read_text(encoding="utf-8"))
        how = {s["key"]: s["texture"]["how"] for s in j["shapes"]}
        self.assertEqual((how["dress.Top"], how["dress.Skirt"]), ("tex-shape", "tex-slot"))

    def test_unused_tex_refused_and_missing_listed(self):
        with self.assertRaises(render.PreviewError) as cm:
            render.run(self.opts(tex=[str(self.tmp / "in" / "body.nif"), "nosuch=x.png"]))
        self.assertIn("nosuch", str(cm.exception))
        self.assertIn("dress.Top", str(cm.exception))                         # 列出可用的 key
        doc = render.run(self.opts(tex=[]))
        self.assertEqual({m["shape"] for m in doc["missing_textures"]},
                         {"body.Body", "dress.Top", "dress.Skirt", "dress.Lace", "dress.Sash"})

    def test_data_lookup_and_hide(self):
        data = self.tmp / "Mod"
        (data / "Textures" / "Preview").mkdir(parents=True)
        for k in ("dress_d", "lace_d", "sash_d", "body_d"):
            (data / "Textures" / "Preview" / f"{k}.DDS").write_bytes(self.inp[f"{k}.dds"].read_bytes())
        a = render.run(self.opts("a"))
        b = render.run(self.opts("b", tex=[], data=[str(data)]))
        self.assertEqual({s["texture"]["how"] for s in b["shapes"]}, {"data"})
        self.assertEqual([i["pixels_sha256"] for i in a["images"]], [i["pixels_sha256"] for i in b["images"]])
        c = render.run(self.opts("c", hide=["body.*"]))
        self.assertNotIn("body.Body", c["images"][0]["coverage"])
        self.assertTrue(c["shapes"][0].get("hidden"))

    def test_rerun_same_and_changed_texture(self):
        render.run(self.opts("a"))
        rep = render.rerun(self.tmp / "a" / "preview.json", self.tmp / "r")
        self.assertTrue(rep["same_images"], rep)
        self.assertEqual(rep["changed_inputs"], [])
        im = synth.dress_image()
        im.paste((0, 200, 0, 255), (0, 0, 128, 64))
        self.inp["dress_d.dds"].write_bytes(synth.dds_bytes(im))
        rep = render.rerun(self.tmp / "a" / "preview.json")                   # 寫回原夾
        self.assertFalse(rep["same_images"])
        self.assertIn("front", rep["changed_views"])
        self.assertEqual(rep["changed_inputs"], [str(self.inp["dress_d.dds"].resolve())])

    def test_render_left_right_with_asymmetric_texture(self):
        """Top 換成左右不對稱的貼圖（u<0.5＝角色右側紅，u≥0.5＝角色左側綠），拿 render 的 PNG 鎖左右。"""
        asym = self.tmp / "asym.png"
        im = Image.new("RGBA", (64, 64), (0, 220, 0, 255))
        im.paste((230, 0, 0, 255), (0, 0, 32, 64))
        im.save(asym)
        doc = render.run(self.opts(tex=[str(self.inp["dress_d.dds"]), f"dress.Top={asym}"] + self.others,
                                   views="front,back,side"))
        fr = doc["camera"]["frame"]
        f = raster.Frame(tuple(fr[:3]), fr[3])
        red = lambda p: p[0] > 120 and p[1] < 60       # noqa: E731
        green = lambda p: p[1] > 120 and p[0] < 60     # noqa: E731
        for view, y, right_on_left in (("front", 9.2, True), ("back", -9.2, False)):
            img = _img(self.tmp / "out" / f"{view}.png")
            for x, want in ((6, red), (-6, green)):                            # 角色右（+x）紅、左（−x）綠
                px, py = raster.project((x, y, 77), raster.VIEWS[view], SZ, f)
                self.assertEqual(px < SZ / 2, (x > 0) == right_on_left, (view, x, px))
                self.assertTrue(want(img.getpixel((int(px), int(py)))), (view, x, img.getpixel((int(px), int(py)))))
            _x, row = raster.project((0, 0, 77), 0, SZ, f)
            left = [img.getpixel((c, int(row))) for c in range(SZ // 2 - 5, SZ // 2 - 2)]
            right = [img.getpixel((c, int(row))) for c in range(SZ // 2 + 2, SZ // 2 + 5)]
            self.assertTrue(all(map(red if right_on_left else green, left)), (view, left))
            self.assertTrue(all(map(green if right_on_left else red, right)), (view, right))
        side = _img(self.tmp / "out" / "side.png")                             # 從角色右側看：只看得到紅
        _x, row = raster.project((0, 0, 77), 90, SZ, f)
        self.assertTrue(all(red(side.getpixel((c, int(row)))) for c in range(SZ // 2 - 5, SZ // 2 + 5)))

    def test_front_off_center_not_back(self):
        """偏離圓筒接縫的點也要是正面色（深度測試；接縫那兩欄三角形最後才畫，只看 x=0 會漏）。"""
        doc = render.run(self.opts(views="front,back"))
        fr = doc["camera"]["frame"]
        for x in (-6, -3, 3, 6):
            y = (11 ** 2 - x * x) ** 0.5
            p = self.px("out", "front", (x, y, 77), fr)
            self.assertLess(max(p) - min(p), 30, (x, p))                      # 正面淺灰，不是背面深藍
            b = self.px("out", "back", (x, -y, 77), fr)
            self.assertGreater(b[2], b[0] + 40, (x, b))

    def test_rerun_in_place_keeps_frame_from(self):
        render.run(self.opts("a"))
        before = json.loads((self.tmp / "a" / "preview.json").read_text(encoding="utf-8"))
        rep = render.rerun(self.tmp / "a" / "preview.json")
        self.assertTrue(rep["same_images"])
        after = json.loads((self.tmp / "a" / "preview.json").read_text(encoding="utf-8"))
        self.assertEqual(before["camera"]["frame_from"], "bounds")
        self.assertEqual(after, before)

    def test_tex_max_lower_bound(self):
        with self.assertRaises(render.PreviewError):
            render.run(self.opts(tex_max=0))
        self.assertFalse((self.tmp / "out").exists())

class GuardTest(Base):
    def test_denied_roots_and_foreign_dir(self):
        for bad in (self.tmp / "mo2" / "mods" / "x", self.tmp / "Data" / "x"):
            with self.assertRaises(WriteGuardError):
                render.run(self.opts(str(bad)))
        (self.tmp / "busy").mkdir()
        (self.tmp / "busy" / "keep.txt").write_text("x")
        with self.assertRaises(render.PreviewError):
            render.run(self.opts("busy"))
        self.assertFalse((self.tmp / "mo2").exists())

    def test_outside_allowlist_refused(self):
        with mock.patch("preview.paths.allowed_write_roots", return_value=[self.tmp / "only"]):
            with self.assertRaises(WriteGuardError):
                render.run(self.opts("out"))

    def test_symlink_target_refused(self):
        render.run(self.opts("a"))
        victim = self.tmp / "victim.png"
        victim.write_bytes(b"keep")
        (self.tmp / "a" / "front.png").unlink()
        (self.tmp / "a" / "front.png").symlink_to(victim)
        with self.assertRaises(WriteGuardError):
            render.run(self.opts("a"))
        self.assertEqual(victim.read_bytes(), b"keep")

    def test_hardlink_target_refused(self):
        render.run(self.opts("a"))
        victim = self.tmp / "victim.png"
        victim.write_bytes(b"keep")
        (self.tmp / "a" / "front.png").unlink()
        os.link(victim, self.tmp / "a" / "front.png")
        with self.assertRaises(WriteGuardError) as cm:
            render.run(self.opts("a"))
        self.assertIn("硬連結", str(cm.exception))
        self.assertEqual(victim.read_bytes(), b"keep")

    def test_outdir_refuses_outside_out(self):
        """第二層守門：OutDir 只准寫 --out 底下（../ 與指到外面的 symlink 夾都拒）。"""
        od = OutDir(self.tmp / "o")
        with self.assertRaises(WriteGuardError):
            od.write_bytes("../escape.bin", b"x")
        self.assertFalse((self.tmp / "escape.bin").exists())
        (self.tmp / "elsewhere").mkdir()
        (self.tmp / "o" / "sub").symlink_to(self.tmp / "elsewhere", target_is_directory=True)
        with self.assertRaises(WriteGuardError):
            od.write_bytes("sub/x.bin", b"x")
        self.assertEqual(list((self.tmp / "elsewhere").iterdir()), [])
        self.assertTrue(od.write_bytes("ok/x.bin", b"x").is_file())

class CliTest(Base):
    def test_cli_render_and_refuse(self):
        argv = ["--nif", str(self.inp["dress.nif"]), "--tex", str(self.inp["dress_d.dds"]), "--size", str(SZ)]
        argv += sum((["--tex", t] for t in self.others[:2]), [])
        rc, out, err = run_cli(argv + ["--out", str(self.tmp / "c"), "--views", "front,back,side"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["images"], ["front.png", "back.png", "side.png"])
        rc, _out, err = run_cli(argv + ["--out", str(self.tmp / "d"), "--tex", "zzz=nope.png"])
        self.assertEqual(rc, 2)
        self.assertIn("拒絕", err)
        rc, out, _err = run_cli(["rerun", str(self.tmp / "c" / "preview.json"), "--out", str(self.tmp / "e")])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["same_images"])

    def test_demo_uses_texedit_output(self):
        rc, out, err = run_cli(["demo", "--out", str(self.tmp / "demo")])
        self.assertEqual(rc, 0, err)
        s = json.loads(out)
        self.assertTrue((self.tmp / "demo" / "compare.png").is_file())
        self.assertEqual(set(s["images"]["before"]), {"front", "back", "side"})
        self.assertNotEqual(s["images"]["before"]["front"], s["images"]["after"]["front"])
        j = json.loads((self.tmp / "demo" / "after" / "preview.json").read_text(encoding="utf-8"))
        top = next(x for x in j["shapes"] if x["key"] == "dress.Top")
        self.assertTrue(top["texture"]["source"].endswith(os.path.join("edit", "dress_d.png")))
        rc, out2, _ = run_cli(["demo", "--out", str(self.tmp / "demo")])      # 重跑同夾：同圖
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out2)["images"], s["images"])

    def test_demo_refuses_symlinked_subdir(self):
        """demo 寫過的夾裡 `unpack` 被換成指到別的准寫根的 symlink：先拒，不寫到 --out 外。"""
        rc, _out, err = run_cli(["demo", "--out", str(self.tmp / "demo")])
        self.assertEqual(rc, 0, err)
        other = self.tmp / "other"
        other.mkdir()
        for sub in ("unpack", "edit", "before"):
            d = self.tmp / "demo" / sub
            moved = self.tmp / f"moved-{sub}"
            d.rename(moved)
            d.symlink_to(other, target_is_directory=True)
            rc, _out, err = run_cli(["demo", "--out", str(self.tmp / "demo")])
            self.assertEqual(rc, 2, (sub, err))
            self.assertIn("symlink", err)
            self.assertEqual(list(other.iterdir()), [], sub)
            d.unlink()
            moved.rename(d)

    def test_demo_needs_texedit_roots_up_front(self):
        """--out 在 preview 的准寫根、不在 texedit 的：開工前就拒，什麼都不寫。"""
        with mock.patch("texedit.paths.allowed_write_roots", return_value=[self.tmp / "only"]):
            rc, _out, err = run_cli(["demo", "--out", str(self.tmp / "demo")])
        self.assertEqual(rc, 2, err)
        self.assertIn("TEXEDIT_WRITE_ROOTS", err)
        self.assertFalse((self.tmp / "demo").exists())

if __name__ == "__main__":
    unittest.main()
