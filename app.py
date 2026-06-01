import os
import sys
import uuid
import shutil
import threading
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from office.soffice import run_soffice

app = Flask(__name__)

UPLOAD_DIR = Path(__file__).parent / "uploads"
CONVERTED_DIR = Path(__file__).parent / "converted"
UPLOAD_DIR.mkdir(exist_ok=True)
CONVERTED_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXT = {".docx", ".doc"}

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

# 一括変換キュー: (batch_id, [(job_id, src_path, original_name), ...])
batch_queue: list = []
batch_lock = threading.Lock()
batch_event = threading.Event()


def cleanup_old_files():
    while True:
        now = time.time()
        for d in [UPLOAD_DIR, CONVERTED_DIR]:
            for f in d.iterdir():
                try:
                    if now - f.stat().st_mtime > 3600:
                        if f.is_file():
                            f.unlink(missing_ok=True)
                        elif f.is_dir():
                            shutil.rmtree(f, ignore_errors=True)
                except Exception:
                    pass
        time.sleep(300)


threading.Thread(target=cleanup_old_files, daemon=True).start()


def batch_worker():
    """
    キューに溜まったファイルをまとめて1回のLibreOffice起動で変換する。
    200ms待って新規ファイルをまとめてからバッチ実行。
    """
    while True:
        batch_event.wait()
        batch_event.clear()

        # 200ms待って追加ファイルをまとめる
        time.sleep(0.2)

        with batch_lock:
            if not batch_queue:
                continue
            batch = batch_queue[:]
            batch_queue.clear()

        # 作業ディレクトリ作成
        batch_id = uuid.uuid4().hex
        work_dir = CONVERTED_DIR / batch_id
        work_dir.mkdir(exist_ok=True)

        # job_id → original_name のマッピング
        job_map = {}  # filename_in_workdir → (job_id, original_name)

        # ファイルをwork_dirにコピー（ファイル名衝突を避けるためjob_idをprefixに）
        src_files = []
        for job_id, src_path, original_name in batch:
            ext = src_path.suffix
            dest_name = f"{job_id}{ext}"
            dest = work_dir / dest_name
            try:
                shutil.copy2(src_path, dest)
                src_files.append(str(dest))
                job_map[dest_name] = (job_id, original_name)
                with jobs_lock:
                    jobs[job_id]["status"] = "converting"
            except Exception as e:
                with jobs_lock:
                    jobs[job_id].update({"status": "error", "error": str(e)})

        if not src_files:
            continue

        # 1回のLibreOffice起動で全ファイルを変換
        try:
            result = run_soffice(
                ["--headless", "--convert-to", "pdf", "--outdir", str(work_dir)]
                + src_files,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except Exception as e:
            # LibreOffice自体が失敗した場合は全ジョブをエラーに
            for job_id, src_path, _ in batch:
                with jobs_lock:
                    jobs[job_id].update({"status": "error", "error": str(e)})
                src_path.unlink(missing_ok=True)
            continue

        # 結果を各ジョブに反映
        for dest_name, (job_id, original_name) in job_map.items():
            pdf_name = Path(dest_name).stem + ".pdf"
            pdf_path = work_dir / pdf_name
            src_path = next((s for _, s, _ in batch if job_id in s.name or f"{job_id}" in Path(s).stem), None)

            if pdf_path.exists():
                output_name = Path(original_name).stem + ".pdf"
                with jobs_lock:
                    jobs[job_id].update({
                        "status": "done",
                        "pdf_path": str(pdf_path),
                        "output_name": output_name,
                        "file_size": pdf_path.stat().st_size,
                    })
            else:
                with jobs_lock:
                    jobs[job_id].update({
                        "status": "error",
                        "error": "変換後のPDFが見つかりません",
                    })

        # アップロードファイルを削除
        for job_id, src_path, _ in batch:
            src_path.unlink(missing_ok=True)


threading.Thread(target=batch_worker, daemon=True).start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "ファイルが選択されていません"}), 400

    results = []
    new_batch = []

    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            results.append({"name": f.filename, "error": "DOCX/DOCファイルのみ対応しています"})
            continue

        job_id = uuid.uuid4().hex
        save_path = UPLOAD_DIR / f"{job_id}{ext}"
        f.save(save_path)

        if save_path.stat().st_size > MAX_FILE_SIZE:
            save_path.unlink(missing_ok=True)
            results.append({"name": f.filename, "error": "ファイルサイズが50MBを超えています"})
            continue

        with jobs_lock:
            jobs[job_id] = {"status": "queued", "original_name": f.filename}

        new_batch.append((job_id, save_path, f.filename))
        results.append({"name": f.filename, "job_id": job_id})

    if new_batch:
        with batch_lock:
            batch_queue.extend(new_batch)
        batch_event.set()

    return jsonify({"jobs": results})


@app.route("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "不明なジョブID"}), 404
    return jsonify(job)


@app.route("/download/<job_id>")
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job or job.get("status") != "done":
        return jsonify({"error": "PDFが見つかりません"}), 404

    pdf_path = Path(job["pdf_path"])
    if not pdf_path.exists():
        return jsonify({"error": "ファイルが期限切れです"}), 410

    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=job["output_name"],
        mimetype="application/pdf",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
