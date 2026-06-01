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


def cleanup_old_files():
    while True:
        now = time.time()
        for d in [UPLOAD_DIR, CONVERTED_DIR]:
            try:
                for f in d.iterdir():
                    try:
                        if now - f.stat().st_mtime > 3600:
                            if f.is_file():
                                f.unlink(missing_ok=True)
                            elif f.is_dir():
                                shutil.rmtree(f, ignore_errors=True)
                    except Exception:
                        pass
            except Exception:
                pass
        time.sleep(300)


threading.Thread(target=cleanup_old_files, daemon=True).start()


def convert_one(job_id: str, src_path: Path, original_name: str):
    """1ファイルを変換する。アップロード時に直接スレッドで呼ぶ。"""
    work_dir = CONVERTED_DIR / job_id
    work_dir.mkdir(exist_ok=True)

    dest = work_dir / f"{job_id}{src_path.suffix.lower()}"

    try:
        with jobs_lock:
            jobs[job_id]["status"] = "converting"

        shutil.copy2(src_path, dest)
        src_path.unlink(missing_ok=True)

        print(f"[START] {job_id} {original_name}", flush=True)

        result = run_soffice(
            ["--headless", "--convert-to", "pdf",
             "--outdir", str(work_dir), str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        print(f"[RC={result.returncode}] {job_id} stdout={result.stdout[:300]} stderr={result.stderr[:300]}", flush=True)

        pdf_path = dest.with_suffix(".pdf")
        if pdf_path.exists():
            print(f"[DONE] {job_id} {pdf_path.stat().st_size}bytes", flush=True)
            with jobs_lock:
                jobs[job_id].update({
                    "status": "done",
                    "pdf_path": str(pdf_path),
                    "output_name": Path(original_name).stem + ".pdf",
                    "file_size": pdf_path.stat().st_size,
                })
        else:
            msg = result.stderr or result.stdout or "PDFが生成されませんでした"
            print(f"[FAIL] {job_id} {msg}", flush=True)
            with jobs_lock:
                jobs[job_id].update({"status": "error", "error": msg})

    except Exception as e:
        print(f"[EXCEPTION] {job_id} {e}", flush=True)
        with jobs_lock:
            jobs[job_id].update({"status": "error", "error": str(e)})
        src_path.unlink(missing_ok=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "ファイルが選択されていません"}), 400

    results = []

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

        t = threading.Thread(
            target=convert_one,
            args=(job_id, save_path, f.filename),
            daemon=True,
        )
        t.start()

        results.append({"name": f.filename, "job_id": job_id})

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
