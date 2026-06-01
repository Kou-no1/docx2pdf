import os
import sys
import uuid
import shutil
import threading
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template

# Inject skill scripts path for soffice helper
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from office.soffice import run_soffice

app = Flask(__name__)

UPLOAD_DIR = Path(__file__).parent / "uploads"
CONVERTED_DIR = Path(__file__).parent / "converted"
UPLOAD_DIR.mkdir(exist_ok=True)
CONVERTED_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXT = {".docx", ".doc"}

# Track job statuses
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def cleanup_old_files():
    """Remove files older than 1 hour."""
    while True:
        now = time.time()
        for d in [UPLOAD_DIR, CONVERTED_DIR]:
            for f in d.iterdir():
                try:
                    if now - f.stat().st_mtime > 3600:
                        f.unlink(missing_ok=True)
                except Exception:
                    pass
        time.sleep(300)


threading.Thread(target=cleanup_old_files, daemon=True).start()


def convert_docx_to_pdf(job_id: str, src_path: Path, original_name: str):
    """Run LibreOffice conversion in a dedicated temp dir."""
    work_dir = CONVERTED_DIR / job_id
    work_dir.mkdir(exist_ok=True)

    try:
        with jobs_lock:
            jobs[job_id]["status"] = "converting"

        # Copy source into work dir to avoid path issues
        tmp_src = work_dir / src_path.name
        shutil.copy2(src_path, tmp_src)

        result = run_soffice(
            [
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(work_dir),
                str(tmp_src),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr or "LibreOffice conversion failed")

        # Find the output PDF
        pdf_files = list(work_dir.glob("*.pdf"))
        if not pdf_files:
            raise RuntimeError("LibreOffice produced no PDF output")

        pdf_path = pdf_files[0]
        output_name = Path(original_name).stem + ".pdf"

        with jobs_lock:
            jobs[job_id].update(
                {
                    "status": "done",
                    "pdf_path": str(pdf_path),
                    "output_name": output_name,
                    "file_size": pdf_path.stat().st_size,
                }
            )

    except Exception as e:
        with jobs_lock:
            jobs[job_id].update({"status": "error", "error": str(e)})
    finally:
        # Clean up uploaded source
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
            jobs[job_id] = {
                "status": "queued",
                "original_name": f.filename,
            }

        t = threading.Thread(
            target=convert_docx_to_pdf,
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
