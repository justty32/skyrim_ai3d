# texedit — 衣服貼圖直接編輯（階段 0）

[ai3d](../README.md)｜狀態：階段 0 靜態通過（合成樣本），待回家用真樣本驗

使用者的用法是「從網上找一張喜歡的衣服範例圖，跟 agent 說我要這個」。許願的是使用者，**操作工具的是 agent**。這套工具讓 agent 看懂貼圖上哪一塊是哪個部件，再精準改那一塊：

1. `unpack`：拆開。NIF＋DDS → 貼圖 PNG、UV 線框疊圖、每個 shape 與 UV island 的遮罩、`layout.json`。
2. `edit`：照操作清單改 PNG，每一步限制在遮罩內。清單存檔，可以重放、比對。
3. `pack`：裝回。PNG → 未壓縮 RGBA8 DDS（帶 mip），並檢查尺寸與 alpha。

只用 Pillow 與標準庫，全部離線。寫檔走 `ai3dlib/writeguard.py`，拒 MO2（mods／overwrite／profiles）、遊戲 Data、`_layer-apply`、`_testlayer`（根在 `ai3dlib/paths.py`，`$MO2_DIR`、`$MO2_MODS_DIR`、`$SKYRIM_DATA`、`$LAYER_APPLY_ROOT`、`$TESTLAYER_ROOT` 可改），也拒 symlink 與硬連結；准寫 `~/skyrim_mods/`、暫存夾、/tmp，另可加 `$TEXEDIT_WRITE_ROOTS`（os.pathsep 分隔）。不裝層、不改 NIF、不開遊戲。

## 指令

在 ai3d repo 根跑 `python3 -m texedit <子命令>`。rc 0 是完成，1 是檢查沒過，2 是拒絕（輸入錯、白名單擋下）。

| 子命令 | 做什麼 |
|---|---|
| `unpack --nif N [--nif …] (--tex T … \| --auto-tex) --out DIR [--data D …] [--assign SHAPE=TEXKEY]` | 拆開。給 `--data` 時 NIF 與貼圖可給 Data 相對路徑（依序找，大小寫不敏感）；`--auto-tex` 照 NIF 的貼圖槽去找貼圖，找不到的記在 `missing_textures` |
| `edit OPS.json --out X.png` | 照清單改，另寫 `X.png.record.json` |
| `replay X.png.record.json` | 重跑並比對輸出像素的 sha256；也列出哪個輸入檔變了（rc 1＝對不上） |
| `diff A.png B.png [--layout L --texture K] [--out d.png]` | 列出變動像素、各 shape／island 被改了多少，並出一張把變動處標紅的圖 |
| `pack X.png --ref 原圖 --out Y.dds [--fail-on-alpha \| --restore-alpha] [--report R.json]` | 裝回。尺寸不同就不寫；報告列出 alpha 變動的像素數 |
| `demo --out DIR` | 用合成洋裝（`synth.py`）把整條流程走一遍 |

## unpack 的輸出

- `textures/<tex>.png`：RGBA 原尺寸，只取最上層 mip。法線圖照原樣轉，不做色彩處理。
- `overlays/<tex>.all.png`：所有 shape 的 UV 線框疊在壓暗的貼圖上，每個 shape 一色，附圖例。`overlays/<tex>.<shape>.png` 只畫一個 shape，並在每個 island 中心標編號（法線圖不出這種單 shape 圖）。疊圖最長邊會縮放到 512–2048；遮罩維持原尺寸。
- `masks/<W>x<H>/<shape>.png`、`<shape>.i<k>.png`：shape 與各 island 的遮罩（灰階 L，255＝選中）。尺寸相同的貼圖共用同一份。
- `layout.json`（`texedit-layout/1`）：
  - `textures.<key>`：來源、尺寸、DDS header、是不是法線圖、哪些 shape 用到哪個槽。
  - `shapes[]`：`key`（`<nif 檔名>.<shape 名>`）、原名、貼圖槽、對不到貼圖的槽、UV 越界的三角形數。`islands[]` 有三角形數、UV 面積、`bbox_uv`、`centroid_uv`。`maps.<tex>` 是該貼圖上的遮罩路徑、像素數、`area_frac`、`bbox_px`，每個 island 各一份。
  - `overlaps[]`：同一張貼圖上 shape 兩兩共用的 texel 數，以及同一 shape 內 island 兩兩共用的 texel 數。鏡像或共用 UV 時，改一邊另一邊也會跟著變，看這裡。

慣例：像素 x＝u·W、y＝v·H（v＝0 在圖頂）；UV 越界照 repeat 包回。island 是三角形經共用頂點、或 UV 座標相同而連起來的連通塊（接縫兩邊拆開的頂點算同一塊）。編號照閱讀順序：由上而下、由左而右。

## 操作清單（`texedit-ops/1`）

完整欄位寫在 `edit.py` 開頭的 docstring。例子：

```json
{"format": "texedit-ops/1", "layout": "../unpack/layout.json", "texture": "skirt",
 "ops": [
  {"op": "recolor", "target": {"shape": "Skirt_1.Skirt", "island": 0}, "color": "#39C5BB", "blend": "colorize"},
  {"op": "gradient", "target": {"shape": "Skirt_1.Skirt", "exclude": {"shape": "Skirt_1.Belt"}, "grow": 1},
   "stops": [[0, "#202020"], [1, "#39C5BB"]], "angle": 90, "blend": "multiply"},
  {"op": "pattern", "target": {"mask": "pleats.png"}, "image": "plaid.png", "scale": 0.5, "blend": "overlay"},
  {"op": "decal", "target": {"shape": "Top", "island": 2}, "image": "logo.png", "center_uv": [0.3, 0.2],
   "scale": 0.8, "rotate": 10, "opacity": 0.9}]}
```

- 選區：`shape`＋`island`、`mask`、`rect_px`／`rect_uv`、`all`；也可以寫成 list 取聯集。另有 `exclude`（扣掉）、`grow`（往外擴幾個像素，負數往內縮）、`feather`（模糊邊緣）。
- op：`fill`、`gradient`、`pattern`、`decal`、`recolor`。
- 混合 `blend`：normal、multiply、screen、overlay、colorize、color、hue。要保留原圖明暗只換顏色，用 `colorize`（把原圖灰階上色：中灰變成指定色）或 `color`（色相與飽和度換掉，明度不動）。
- alpha 一律不動。

## agent 怎麼用：使用者給範例圖之後

1. **拆開**：家裡跑 `unpack --data <Data 根> --nif <該件的 NIF 的 Data 相對路徑> --auto-tex --out ~/skyrim_mods/_staging-<日期>-texedit/<件>/unpack`（找資產的辦法見下一節）。
2. **看 layout**：先看 `overlays/<tex>.all.png`，認出每個 shape 在貼圖上的位置；再看單 shape 疊圖上的 island 編號，對照 `layout.json` 的 `bbox_uv`、`area_frac`，說得出「裙子正面是 shape X 的 island 2」。先看 `overlaps`：有共用 texel 的部件改不開，要嘛 `exclude` 掉，要嘛接受兩邊一起變。
3. **對應範例圖**：把範例圖的部件對到 shape／island，挑出顏色（hex）。要用的花紋或標誌從範例圖裁成小 PNG，放在清單旁邊。
4. **寫操作清單**：一個部件一兩步，先用 `colorize`／`color` 保留布紋明暗。邊緣有接縫露底色時，加 `grow: 1–2`。
5. **edit**，然後看 `record.json` 的 `by_shape`。不該動的 shape 出現在裡面，就是溢色了，回第 4 步。`diff --out` 的標紅圖可以給使用者看。
6. **pack** 到 staging：`pack edit/X.png --ref unpack/textures/X.png --out <staging>/textures/.../X.dds --fail-on-alpha`。要壓 BC7 的話，回家用 texconv，這裡不做。
7. **預覽**：把衣服穿到模型上畫出來，**不在本線**，是下一步（`ai3dlib/geom.py` 已經會讀 NIF 幾何與 UV，可以擴充成貼圖渲染）。裝進遊戲走 alternate texture 路線（新 TXST，不蓋原檔），寫 plugin 的工具在私有工作區，也是下一步。

## 資產從哪裡來（沒有 `--live`）

私有工作區的版本有 `--live`（查 MO2 現役 VFS 與 BSA）；那要私有工具，這裡拿掉了。改用下面三種：

1. **`--tex` 明給**：`--nif` 與 `--tex` 都給檔案系統路徑（MO2 某個 mod 夾裡的檔、或先解出來的檔）。最穩，也最清楚用的是哪一份。
2. **`--data` 加 `--auto-tex`**：`--data` 給一個或多個「長得像 Data」的根（MO2 單一 mod 夾、解好 BSA 的夾、遊戲 Data 本身只讀也行），依給的順序找，先找到的贏。`--auto-tex` 照 NIF 的貼圖槽在這些根底下找貼圖；找不到的記在 `missing_textures`，不中斷。
3. **兩輪**：先只給 NIF 所在 mod 夾跑一次 `--auto-tex`，看 `missing_textures` 缺什麼，查出那些貼圖在哪個 mod（用 MO2 或你自己的查詢工具），把那個 mod 夾也加進 `--data` 再跑。

`--data` 的順序就是優先序，不會照 MO2 的載入序；同一個貼圖在多個 mod 都有時，要自己把贏家的夾放前面。BSA 裡的資產要先解出來。layout.json 的 `textures.<key>.source` 會記實際讀的是哪個檔。

## 限制

- 只改漫反射（diffuse）的顏色。法線圖、alpha、網格都不動。改輪廓、無袖剪裁屬於網格的事（ai3d 的網格編輯部分）。
- 遮罩是「三角形加邊框」的硬邊光柵化，貼圖的 mip 會從邊外取色，所以接縫可能露出原色，用 `grow` 補。
- island 編號是照幾何算出來的，換一個 NIF 版本編號可能不同；操作清單寫的是 shape key 加 island 編號，換版本要重新 unpack，再核對一次。

測試：`tests/test_texedit.py`（合成 NIF 與 DDS，只用 tempdir），在 repo 根跑 `python3 -m unittest discover -s tests -v`。
