# skyrim_ai3d — AI 編輯 Skyrim 3D 素材

使用者許願，AI 動手：使用者給一張喜歡的衣服範例圖或一句描述，agent 用這裡的工具改貼圖、改模型，產出能裝進 MO2 的檔案。

開 repo 緣由（使用者 2026-11-26）：「我更想要能夠直接編輯衣服紋理、模型」「我說你改，可能會是我從網上找一個我喜歡的衣服樣式範例圖，直接跟你許願說我要這個」「乾脆就是AI 3d編輯相關的都放這邊」。

## 範圍

| 部分 | 內容 | 狀態 |
|---|---|---|
| `texedit/` | 貼圖編輯：DDS 拆成 PNG、各 shape 的 UV 疊圖與選區遮罩、照操作清單改圖、裝回 DDS | 2026-11-26 起做（階段 0） |
| 預覽 | 把衣服連貼圖畫成圖，改完不進遊戲先看 | 下一步 |
| 網格編輯 | Blender 無頭腳本（`blender -b --python`）＋ PyNifly、Outfit Studio 6.2 的 MCP：無袖、貼身、修穿模、加配件 | 規劃中，工具只裝在家裡那台 |
| AI 生成 | 在我們自己的 UV 上畫紋理（StableGen 一類）、配件草模（TripoSG 一類） | 規劃中，模型權重要使用者同意才下載 |

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
