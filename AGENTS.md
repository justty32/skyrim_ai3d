# skyrim_ai3d — agent 備忘

AI 編輯 Skyrim 3D 素材（貼圖、網格、AI 生成管線）。人看的總覽在 [README.md](README.md)；本檔只寫 agent 要守的。

## 鐵律

1. **公開 repo**：不 commit 遊戲或 mod 的原始模型、貼圖、plugin，也不 commit 用它們產出的檔；測試只用合成樣本。不寫機器名、帳號、絕對路徑。
2. **行為不變**：重構時行為不變，驗證是測試全綠（`python3 -m unittest discover -s tests -v`）。
3. **不可逆或對外的動作**（push、刪檔、下載模型權重、雲端服務）要使用者當場同意。push 照母工作區的時程統一做。
4. **寫入白名單**：只寫指定輸出目錄；拒 MO2 mods／overwrite／profiles、遊戲 Data、symlink、硬連結；temp 檔＋`os.replace`。

## 相關

- 格式互轉：同層 [model-converter](../model-converter/AGENTS.md)。
- 母工作區的計畫：`wf/workflows/plans/miku-outfit/`（初音那套是第一個用例）。
