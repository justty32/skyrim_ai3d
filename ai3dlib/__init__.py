"""ai3dlib —— ai3d 各工具共用的小函式庫（只用標準庫；DDS 解碼另要 Pillow）。

- `nif`、`dds`、`writeguard`、`geom`：複製自私有工作區 agentctl 的同名模組（各檔檔頭記來源與 commit），手動同步，不改行為。
- `paths`：拒寫的根（MO2、遊戲 Data、`_layer-apply`、`_testlayer`；環境變數可改）。
- `assets`：`--data` 的 Data 相對路徑查找（大小寫不敏感）。
"""
