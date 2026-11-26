# preview — 衣服連貼圖畫成預覽圖

[ai3d](../README.md)｜狀態：合成樣本測試通過，待回家用真樣本驗

把一件或多件衣服（可加身體）連 diffuse 貼圖畫成正面、背面（可加側面）PNG。改完貼圖不用裝進遊戲，先看一眼對不對。只用 Pillow 與標準庫，全部離線，純 Python 軟體光柵化。

## 在循環裡的位置

```
許願（使用者給範例圖）→ 拆開（texedit unpack）→ 改圖（texedit edit）→ 預覽（preview）→ 調整（回改圖）→ 裝回（texedit pack）
```

- 改圖前先跑一次 preview，留一份「改前」，記下 `preview.json` 的 `camera.frame`。
- texedit `edit` 的輸出 PNG 直接當 `--tex`；改後用同一個 `--frame` 再跑一次，兩張圖才能逐像素比。
- 看預覽找問題：不該變色的部件變了（溢色）、接縫露出原色（texedit 加 `grow`）、正面背面對不上範例圖。看到就回去改操作清單，再預覽。
- 給使用者看 `sheet.png`（各視角橫排一張）。

## 指令

在 ai3d repo 根跑。rc 0 是完成，1 是 `rerun` 的圖對不上，2 是拒絕（輸入錯、NIF 或貼圖解不開、白名單擋下）。

```sh
python3 -m preview --nif X.nif [--nif BODY.nif …] [--tex KEY=FILE | --tex FILE …] [--data DIR …] --out DIR \
    [--views front,back,side] [--size 512] [--frame cx,cy,cz,r] [--hide GLOB] [--tex-max 1024] [--bg RRGGBB]
python3 -m preview rerun DIR/preview.json [--out DIR2]
python3 -m preview demo --out DIR
```

- `--nif`：可以給多個（衣服、身體疊在同一個空間）。給了 `--data` 時，也可以給 Data 相對路徑。
- `--views`：`front`、`back`、`side`（從角色右側看）、`lside`、`q45`、`lq45`、`bq45`，或直接寫角度。預設 `front,back`。
- `--frame`：固定取景。不給時取所有要畫的 shape 的包圍球。
- `--hide`：不畫的 shape（shape key、shape 名或 NIF 檔名，可用 glob）。
- `demo`：合成洋裝＋身體走一圈「改前預覽 → texedit unpack／edit → 改後預覽」，出 `compare.png`（上排改前、下排改後）。中段的 `unpack/`、`edit/` 由 texedit 寫，所以 `--out` 也要在 texedit 的准寫根裡（另加用 `$TEXEDIT_WRITE_ROOTS`）；不在的話開工前就拒。`--out` 底下有 symlink（檔或夾）也拒。

## 貼圖怎麼找

每個 shape 只畫 diffuse，也就是貼圖槽 0。照下面的順序找，先找到的贏：

1. `--tex KEY=FILE`，KEY 對到 shape：shape key（`<NIF 檔名主幹>.<shape 名>`，同 texedit）或 shape 名，可用 glob。
2. `--tex KEY=FILE`，KEY 對到該 shape 的 diffuse 槽：槽路徑的檔名主幹（例如 `dress_d`），或 Data 相對全路徑。只給 `--tex FILE` 時，KEY 就是 FILE 的檔名主幹（整串就是一個存在的檔時，路徑裡有 `=` 也當 FILE），所以 `edit/dress_d.png` 會自動對到 `dress_d.dds` 那個槽。
3. 有 `--data` 時，照 diffuse 槽的路徑去找（大小寫不敏感，依給的順序）。
4. 都沒有：平塗色，記在 `missing_textures`。

PNG 與 DDS 都吃（DDS 交給 Pillow 解，只取最上層 mip）。`--tex` 沒對到任何 shape 會被拒絕（rc 2），訊息會列出可用的 shape key 與槽名，免得打錯 KEY 卻默默畫成舊圖。

## 輸出

`--out` 要給新夾、空夾，或 preview 之前寫過的夾（裡面有 `preview.json`）。別的非空夾會被拒絕。

- `<view>.png`：每個視角一張，正方形。
- `sheet.png`：各視角橫排，左上角標視角名。
- `preview.json`（`ai3d-preview/1`）：
  - `args`：重跑要的參數（路徑都換成絕對路徑）。
  - `camera`：正交投影、`frame`（中心＋半徑）、打光方向、各視角的 yaw。
  - `inputs`：每個 NIF 與貼圖檔的 sha256。貼圖另記縮圖後的尺寸。
  - `shapes[]`：shape key、來源 NIF、頂點數與三角形數、alpha 模式（`opaque`／`test`／`blend`）、diffuse 槽，以及貼圖從哪來（`how`：`tex-shape`／`tex-slot`／`data`／`none`）。
  - `images[]`：檔名、檔案 sha256、像素 sha256、`coverage`（每個 shape 畫到幾個像素）。
  - `missing_textures`、`warnings`：某視角有 shape 一個像素都沒畫到時，會記在 `warnings`。
- `rerun`：照 `preview.json` 重畫，相機固定用記錄的 `frame`。比對像素 sha256，並列出哪些輸入檔變了。Pillow 版本不同時會提示。

同輸入、同 Pillow 版本，輸出的 bytes 相同（測試鎖 sha256）。

寫檔走 `ai3dlib/writeguard.py`，分兩層：
- `--out` 本身：要在 `~/skyrim_mods/`、暫存夾或 /tmp 底下，另可加 `$PREVIEW_WRITE_ROOTS`（os.pathsep 分隔）。拒 MO2（mods／overwrite／profiles）、遊戲 Data、`_layer-apply`、`_testlayer`（根在 `ai3dlib/paths.py`，env 可改）。
- 每個檔：只准寫在 `--out` 底下，逐檔拒 symlink 與硬連結。

## 簡化了什麼

- **打光**：Lambert，用幾何算出的頂點法線（不讀 NIF 存的法線，也不用法線圖），雙面（不做背面剔除）。光從畫面的左上前方來（front 時是角色的右上前方；相機座標 x' 正方向＝畫面左），ambient 0.3。沒有陰影、高光、環境貼圖、rim light。
- **貼圖**：只畫 diffuse。取樣是最近鄰，UV 照 repeat 包回，不用 mip。貼圖超過 `--tex-max` 會先等比縮小。SkinTint／HairTint／FaceTint 的乘色、頂點色、glow、多層 shader 都不做，所以身體顏色是貼圖原色。
- **alpha**：照 NiAlphaProperty 的兩個旗標。只有 test（0x200）時，alpha < threshold 的片段丟掉，test 函數一律當 GREATER_EQUAL。有 blend（0x1）時半透明疊色，alpha ≥ 230 當不透明寫深度；同時有 test 時先做 test。混合函數一律當 SRC_ALPHA／ONE_MINUS_SRC_ALPHA。blend 三角形每個視角在同一個 shape 內由遠到近排序，shape 之間照輸入順序，所以兩件半透明衣服交疊時可能前後顛倒。沒有 NiAlphaProperty 的 shape 不看貼圖 alpha。
- **姿勢**：綁定姿勢（T／A pose），不套骨架、不做 BodySlide 體型。蒙皮 shape 照 `ai3dlib/geom.resolve_skinning` 對齊：每根骨以頂點最多的 shape 為參考。衣服跟身體的 NIF 若存在同一個模型空間，就會對齊。
- **投影**：正交投影，沒有透視。
- **光柵化**：頂點吸到 1/256 像素的格子，邊函數用整數算，配 top-left 填充規則：兩個三角形的共用邊上的像素只畫一次，半透明 shape 不會在接縫疊兩次色，也不會漏畫。不做反鋸齒。

## 限制

- 純 Python：512² 一張要幾秒；衣服＋身體加起來幾萬個三角形時，要十幾秒到幾十秒（還沒在真樣本量過）。可以先用 `--size 256` 看。
- 幾何讀取靠 `ai3dlib/geom.py`。不支援 strips 版的 NiSkinPartition 與 particle data，遇到時拒絕。
- 重跑到同一夾時，舊的視角圖不會被刪（例如這次少給 `side`，舊的 `side.png` 還在）。以 `preview.json` 的 `images` 為準。

測試：`tests/test_preview.py`（合成 NIF 與 DDS，只用 tempdir），在 repo 根跑 `python3 -m unittest discover -s tests -v`。
