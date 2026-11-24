# 參考資料站

私人用的參考資料頁面（PDF + 連結）。整個目錄以密碼加密，網頁只負責在本機瀏覽器解密。

## 加密方式

- 目錄與每份 PDF 都用 **AES-256-GCM** 加密，金鑰由密碼經 **PBKDF2-SHA256（310,000 次）** + **HKDF** 派生。
- 網站上只有密文（`data/catalog.sd`、`files/*.sd`），沒有密碼無法讀出任何檔名或內容。
- 另有 `robots.txt` 與 `noindex` meta，不被搜尋引擎收錄。
- 注意：前端加密代表「沒有密碼就解不開」，但網頁本身是公開可讀的；PDF 不會以明文形式上傳。

## 管理內容

需要 Python 3 與 `cryptography`（`pip3 install cryptography`）。密碼會用互動方式詢問。

```bash
# 加入 PDF
python3 tools/seal.py add-pdf ~/Downloads/paper.pdf --title "Kresling 摺紙機構" --note "結構設計參考"

# 加入連結
python3 tools/seal.py add-link --title "arXiv 論文" --url "https://arxiv.org/abs/xxxx" --note "延伸閱讀"

# 列出 / 移除
python3 tools/seal.py list
python3 tools/seal.py rm <id>

# 換密碼（會用新密碼重新加密所有檔案，並更新 data/vault.json）
python3 tools/seal.py passwd
```

改完後提交並推上 GitHub，網站約 1 分鐘後更新：

```bash
git add -A
git commit -m "update materials"
git push
```

## 部署到 Cloudflare Pages（含 Email 登入驗證）

需要一組 API Token（權限：Cloudflare Pages — Edit、Access: Apps and Policies — Edit、Access: Organizations, Identity Providers, and Groups — Edit、Account Settings — Read），存成 `~/.cf-scidata-token`。

```bash
./tools/deploy-cf.sh      # 上傳並確保 Access 規則存在（可重複執行）
```

完成後網址是 `https://scidata-refs.pages.dev`，未登入會被導向 Cloudflare Access 的 Email 驗證碼頁面。

## 結構

```
index.html          單頁介面
assets/app.js       解鎖與解密邏輯（WebCrypto）
assets/style.css    樣式
data/vault.json     KDF 參數 + 鹽（非機密）
data/catalog.sd     加密的項目清單
files/*.sd          加密的 PDF
tools/seal.py       本機管理工具
```
