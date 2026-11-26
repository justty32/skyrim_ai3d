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
from preview.paths import WriteGuardError

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


if __name__ == "__main__":
    unittest.main()
