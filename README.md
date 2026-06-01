# DOCX → PDF 変換ツール

WordファイルをLibreOffice Headlessで高品質PDFに変換するWebアプリ。

## 機能

- `.docx` / `.doc` をドラッグ＆ドロップでアップロード
- 複数ファイル同時変換
- Wordの「名前を付けて保存→PDF」と同等のレイアウト再現
- 1時間後に自動削除

---

## Renderへのデプロイ手順

### 1. GitHubにpush

```bash
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/docx2pdf.git
git push -u origin main
```

### 2. Renderでサービス作成

1. [render.com](https://render.com) にサインアップ（GitHub連携）
2. ダッシュボード → **New → Web Service**
3. リポジトリを選択
4. 以下を確認：
   - **Runtime**: Docker（自動検出）
   - **Plan**: Free
5. **Create Web Service** をクリック

### 3. デプロイ完了

- 初回ビルドは **5〜10分** かかります（LibreOfficeのインストールのため）
- 完了後、`https://docx2pdf-XXXX.onrender.com` のURLが発行されます

> ⚠️ **無料プランの注意点**  
> 15分間アクセスがないとサービスがスリープします。  
> 次のアクセス時に起動まで30〜60秒かかります。

---

## ローカル実行

```bash
pip install -r requirements.txt
python3 app.py
# → http://localhost:5000
```

## Dockerでローカル実行

```bash
docker build -t docx2pdf .
docker run -p 5000:10000 docx2pdf
# → http://localhost:5000
```
