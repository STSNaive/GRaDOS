import base64
import json
import subprocess
import sys
from pathlib import Path


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_minimal_pdf(text: str) -> bytes:
    stream_text = f"BT /F1 18 Tf 36 96 Td ({pdf_escape(text)}) Tj ET"
    stream_bytes = stream_text.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length "
        + str(len(stream_bytes)).encode("ascii")
        + b" >>\nstream\n"
        + stream_bytes
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body = bytearray()
    offsets = [0]

    for index, obj in enumerate(objects, start=1):
        offsets.append(len(header) + len(body))
        body.extend(f"{index} 0 obj\n".encode("ascii"))
        body.extend(obj)
        body.extend(b"\nendobj\n")

    xref_offset = len(header) + len(body)
    xref = bytearray()
    xref.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    xref.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        xref.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")

    return header + bytes(body) + bytes(xref) + trailer


def main() -> int:
    root = Path(__file__).resolve().parent
    worker_path = root / "worker.py"

    request = {
        "fileName": "verify.pdf",
        "pdfBase64": base64.b64encode(build_minimal_pdf("Hello Marker")).decode("ascii"),
    }

    result = subprocess.run(
        [sys.executable, str(worker_path)],
        cwd=root,
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Worker exited with code {result.returncode}")

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Worker returned invalid JSON: {result.stdout[:200]!r}") from exc

    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "Worker reported an unknown error.")

    markdown = str(response.get("markdown") or "").strip()
    if not markdown:
        raise RuntimeError("Worker returned empty markdown for the verification PDF.")

    print(
        json.dumps(
            {
                "ok": True,
                "markdownPreview": markdown[:120],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
