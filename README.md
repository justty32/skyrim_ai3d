# skyrim_ai3d — AI 編輯 Skyrim 3D 素材

使用者許願，AI 動手：使用者給一張喜歡的衣服範例圖或一句描述，agent 用這裡的工具改貼圖、改模型，產出能裝進 MO2 的檔案。

開 repo 緣由（使用者 2026-11-26）：「我更想要能夠直接編輯衣服紋理、模型」「我說你改，可能會是我從網上找一個我喜歡的衣服樣式範例圖，直接跟你許願說我要這個」「乾脆就是AI 3d編輯相關的都放這邊」。

## 範圍

| 部分 | 內容 | 狀態 |
|---|---|---|
| [`texedit/`](texedit/README.md) | 貼圖編輯：DDS 拆成 PNG、各 shape 的 UV 疊圖與選區遮罩、照操作清單改圖、裝回 DDS。用法：repo 根 `python3 -m texedit demo --out DIR`，其餘見 [texedit/README](texedit/README.md) | 階段 0 靜態通過，待回家驗 |
| [`preview/`](preview/README.md) | 預覽：衣服（可加身體）連 diffuse 貼圖畫成正面／背面（可加側面）PNG，改完不進遊戲先看；吃 texedit `edit` 的輸出。用法：repo 根 `python3 -m preview demo --out DIR`，其餘見 [preview/README](preview/README.md) | 合成樣本測試通過，待回家驗 |
| 網格編輯 | Blender 無頭腳本（`blender -b --python`）＋ PyNifly、Outfit Studio 6.2 的 MCP：無袖、貼身、修穿模、加配件 | 規劃中，工具只裝在家裡那台 |
| AI 生成 | 在我們自己的 UV 上畫紋理（StableGen 一類）、配件草模（TripoSG 一類） | 規劃中，模型權重要使用者同意才下載 |

共用的小函式庫在 `ai3dlib/`（NIF 讀寫與幾何、DDS、寫入白名單、拒寫的根、`--data` 資產查找）；`nif`、`dds`、`writeguard`、`geom` 複製自私有工作區，各檔檔頭記來源 commit，手動同步。

不在這裡做的：
- NIF 與 glTF／OBJ 互轉用同層的 [model-converter](../model-converter/README.md)，不另寫。
- 寫 plugin 記錄（ARMO／ARMA／TXST）的工具與換色工具在私有的工作區，不搬來。

## 規矩

- **不放遊戲與 mod 的原始素材**：這個 repo 是公開的。測試只用合成的 NIF／DDS；真樣本只在家裡那台跑，輸出也不 commit。
- 不寫機器名、帳號、絕對路徑。
- 寫檔只准寫進指定的輸出目錄；拒寫 MO2 的 mods、overwrite、profiles 與遊戲 Data，也拒 symlink 和硬連結。
- 依賴盡量少：Python 標準庫＋ Pillow。

## 跑測試

```sh
python3 -m unittest discover -s tests -v
```
