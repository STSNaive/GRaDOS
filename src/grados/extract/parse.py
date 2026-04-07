"""PDF parsing pipeline: pymupdf4llm → Marker (optional) → Docling (optional)."""

from __future__ import annotations

import tempfile


async def parse_pdf(
    pdf_buffer: bytes,
    filename: str = "paper.pdf",
    parse_order: list[str] | None = None,
    parse_enabled: dict[str, bool] | None = None,
    marker_timeout: int = 120000,
) -> str | None:
    """Parse a PDF buffer into markdown text using the configured pipeline.

    Returns markdown string or None if all parsers fail.
    """
    order = parse_order or ["PyMuPDF", "Marker", "Docling"]
    enabled = parse_enabled or {"PyMuPDF": True, "Marker": False, "Docling": False}

    for parser in order:
        if not enabled.get(parser, False):
            continue

        if parser == "PyMuPDF":
            result = _parse_pymupdf(pdf_buffer)
            if result:
                return result

        elif parser == "Marker":
            result = _parse_marker(pdf_buffer, filename)
            if result:
                return result

        elif parser == "Docling":
            result = _parse_docling(pdf_buffer, filename)
            if result:
                return result

    return None


def _parse_pymupdf(pdf_buffer: bytes) -> str | None:
    """Parse PDF using pymupdf4llm (default, fast, in-process)."""
    try:
        import pymupdf
        import pymupdf4llm

        doc = pymupdf.open(stream=pdf_buffer, filetype="pdf")  # type: ignore[no-untyped-call]
        md = pymupdf4llm.to_markdown(doc)
        doc.close()  # type: ignore[no-untyped-call]
        return md if md and len(md) > 100 else None
    except Exception:
        return None


def _parse_marker(pdf_buffer: bytes, filename: str) -> str | None:
    """Parse PDF using marker-pdf (heavy ML, optional)."""
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(pdf_buffer)
            tmp.flush()
            models = create_model_dict()
            converter = PdfConverter(artifact_dict=models)
            result = converter(tmp.name)
            md = result.markdown if hasattr(result, "markdown") else str(result)
            return md if md and len(md) > 100 else None
    except ImportError:
        return None
    except Exception:
        return None


def _parse_docling(pdf_buffer: bytes, filename: str) -> str | None:
    """Parse PDF using docling (IBM, optional)."""
    try:
        from docling.document_converter import DocumentConverter

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(pdf_buffer)
            tmp.flush()
            converter = DocumentConverter()
            result = converter.convert(tmp.name)
            md = result.document.export_to_markdown()
            return md if md and len(md) > 100 else None
    except ImportError:
        return None
    except Exception:
        return None
