#!/usr/bin/env python3
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file

APP_DIR = Path(__file__).resolve().parent
HTML = APP_DIR / "index.html"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "40"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def which(name):
    return shutil.which(name)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "Falha no subprocesso").strip())
    return p.stdout


def ocr_image(path):
    outbase = str(path.with_suffix(""))
    run(["tesseract", str(path), outbase, "-l", "por+eng", "--psm", "6"])
    txt = Path(outbase + ".txt")
    return txt.read_text(encoding="utf-8", errors="replace") if txt.exists() else ""


def extract_pdf_text(path):
    out = path.with_suffix(".txt")
    run(["pdftotext", "-layout", str(path), str(out)])
    return out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""


def ocr_pdf(path):
    with tempfile.TemporaryDirectory() as td:
        prefix = str(Path(td) / "page")
        run(["pdftoppm", "-png", "-r", "220", str(path), prefix])
        pages = sorted(Path(td).glob("page-*.png"))
        return "\n\n".join(ocr_image(page) for page in pages), len(pages)


def extract_document(data, filename, mimetype):
    suffix = Path(filename or "documento").suffix.lower()

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ("input" + (suffix or ".bin"))
        p.write_bytes(data)

        if suffix == ".pdf" or (mimetype and "pdf" in mimetype.lower()):
            direct = ""
            try:
                direct = extract_pdf_text(p).strip()
            except Exception:
                direct = ""

            if len(direct) >= 80:
                return {
                    "ok": True,
                    "mode": "PDF_TEXTUAL",
                    "pages": None,
                    "text": direct,
                }

            text, pages = ocr_pdf(p)
            return {
                "ok": True,
                "mode": "PDF_OCR",
                "pages": pages,
                "text": text,
            }

        if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"} or (
            mimetype and mimetype.lower().startswith("image/")
        ):
            return {
                "ok": True,
                "mode": "IMAGE_OCR",
                "pages": 1,
                "text": ocr_image(p),
            }

        if suffix == ".txt" or (mimetype and "text/plain" in mimetype.lower()):
            return {
                "ok": True,
                "mode": "TEXT",
                "pages": 1,
                "text": data.decode("utf-8", errors="replace"),
            }

        raise ValueError("Formato de arquivo não suportado para leitura documental.")


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.get("/")
def index():
    return send_file(HTML)


@app.get("/api/ocr/health")
def ocr_health():
    ready = bool(which("tesseract") and which("pdftotext") and which("pdftoppm"))
    return jsonify(
        {
            "ok": True,
            "ocr_ready": ready,
            "version": "Beta Cartório 1",
        }
    )


@app.post("/api/ocr/extract")
def ocr_extract():
    try:
        # Contrato principal da Plataforma: JSON com arquivo em Base64.
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            encoded = payload.get("data")
            if not encoded:
                return jsonify({"ok": False, "error": "Arquivo não recebido."}), 400

            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception:
                return jsonify({"ok": False, "error": "Conteúdo Base64 inválido."}), 400

            filename = payload.get("filename") or "documento"
            mimetype = payload.get("mime") or "application/octet-stream"
            return jsonify(extract_document(raw, filename, mimetype))

        # Compatibilidade adicional com multipart/form-data.
        f = request.files.get("file") or next(iter(request.files.values()), None)
        if not f:
            return jsonify({"ok": False, "error": "Arquivo não recebido."}), 400

        return jsonify(
            extract_document(
                f.read(),
                f.filename or "documento",
                f.mimetype or "application/octet-stream",
            )
        )

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 415
    except Exception as e:
        return jsonify({"ok": False, "error": f"Falha no serviço de leitura: {e}"}), 500


@app.get("/api/system/health")
def system_health():
    return jsonify(
        {
            "ok": True,
            "app": "Plataforma Notarial Integrada",
            "version": "Beta Cartório 1",
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
