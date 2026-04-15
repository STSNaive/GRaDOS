"""Document normalization pipeline: Docling first, then optional fallbacks."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class ParsePipelineResult:
    markdown: str | None
    parser_used: str = ""
    warnings: list[str] = field(default_factory=list)
    debug: list[str] = field(default_factory=list)


@dataclass
class _ParserAttemptResult:
    markdown: str | None
    warning: str = ""
    debug: str = ""


@dataclass(frozen=True)
class PdfParserContext:
    pdf_buffer: bytes
    filename: str
    marker_timeout: int


@dataclass(frozen=True)
class DocumentNormalizationContext:
    content: str
    filename: str
    normalized_format: str


class PdfParserStrategy(Protocol):
    name: str

    def parse(self, context: PdfParserContext) -> _ParserAttemptResult:
        ...


class DocumentNormalizerStrategy(Protocol):
    name: str
    parser_used: str

    def supports(self, normalized_format: str) -> bool:
        ...

    def normalize(self, context: DocumentNormalizationContext) -> _ParserAttemptResult:
        ...


@dataclass(frozen=True)
class _FunctionPdfParserStrategy:
    name: str
    runner: Callable[[PdfParserContext], _ParserAttemptResult]

    def parse(self, context: PdfParserContext) -> _ParserAttemptResult:
        return self.runner(context)


@dataclass(frozen=True)
class _FormatDocumentNormalizerStrategy:
    name: str
    parser_used: str
    formats: tuple[str, ...]
    runner: Callable[[DocumentNormalizationContext], _ParserAttemptResult]

    def supports(self, normalized_format: str) -> bool:
        return normalized_format in self.formats

    def normalize(self, context: DocumentNormalizationContext) -> _ParserAttemptResult:
        return self.runner(context)


def _run_docling_pdf_parser(context: PdfParserContext) -> _ParserAttemptResult:
    return _parse_docling_attempt(context.pdf_buffer, context.filename)


def _run_marker_pdf_parser(context: PdfParserContext) -> _ParserAttemptResult:
    return _parse_marker_attempt(
        context.pdf_buffer,
        context.filename,
        marker_timeout=context.marker_timeout,
    )


def _run_pymupdf_pdf_parser(context: PdfParserContext) -> _ParserAttemptResult:
    return _parse_pymupdf_attempt(context.pdf_buffer)


def _run_markdown_normalizer(context: DocumentNormalizationContext) -> _ParserAttemptResult:
    return _ParserAttemptResult(markdown=_normalize_markdown(context.content))


def _run_plain_text_normalizer(context: DocumentNormalizationContext) -> _ParserAttemptResult:
    return _ParserAttemptResult(markdown=_normalize_plain_text(context.content))


def _run_docling_html_normalizer(context: DocumentNormalizationContext) -> _ParserAttemptResult:
    return _normalize_with_docling_attempt(
        context.content.encode("utf-8"),
        suffix=".html",
        filename=context.filename,
        context_label="normalization",
        fallback=False,
    )


def _run_docling_xml_normalizer(context: DocumentNormalizationContext) -> _ParserAttemptResult:
    return _normalize_with_docling_attempt(
        context.content.encode("utf-8"),
        suffix=".xml",
        filename=context.filename,
        context_label="normalization",
        fallback=False,
    )


PDF_PARSER_REGISTRY: dict[str, PdfParserStrategy] = {
    "Docling": _FunctionPdfParserStrategy("Docling", _run_docling_pdf_parser),
    "Marker": _FunctionPdfParserStrategy("Marker", _run_marker_pdf_parser),
    "PyMuPDF": _FunctionPdfParserStrategy("PyMuPDF", _run_pymupdf_pdf_parser),
}

DOCUMENT_NORMALIZER_STRATEGIES: tuple[DocumentNormalizerStrategy, ...] = (
    _FormatDocumentNormalizerStrategy("Markdown", "Markdown", ("markdown", "md"), _run_markdown_normalizer),
    _FormatDocumentNormalizerStrategy("PlainText", "PlainText", ("text", "txt"), _run_plain_text_normalizer),
    _FormatDocumentNormalizerStrategy("DoclingHTML", "Docling", ("html", "htm"), _run_docling_html_normalizer),
    _FormatDocumentNormalizerStrategy(
        "DoclingXML",
        "Docling",
        ("xml", "jats", "jats_xml"),
        _run_docling_xml_normalizer,
    ),
)


def build_pdf_parser_strategies(order: list[str] | None = None) -> list[PdfParserStrategy]:
    resolved_order = order or ["Docling", "Marker", "PyMuPDF"]
    return [PDF_PARSER_REGISTRY[name] for name in resolved_order if name in PDF_PARSER_REGISTRY]


def resolve_document_normalizer(normalized_format: str) -> DocumentNormalizerStrategy:
    for strategy in DOCUMENT_NORMALIZER_STRATEGIES:
        if strategy.supports(normalized_format):
            return strategy
    return DOCUMENT_NORMALIZER_STRATEGIES[1]


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
    result = await parse_pdf_with_diagnostics(
        pdf_buffer,
        filename=filename,
        parse_order=parse_order,
        parse_enabled=parse_enabled,
        marker_timeout=marker_timeout,
    )
    return result.markdown


async def parse_pdf_with_diagnostics(
    pdf_buffer: bytes,
    filename: str = "paper.pdf",
    parse_order: list[str] | None = None,
    parse_enabled: dict[str, bool] | None = None,
    marker_timeout: int = 120000,
) -> ParsePipelineResult:
    """Parse a PDF buffer and preserve parser warnings/debug for caller-facing receipts."""
    strategies = build_pdf_parser_strategies(parse_order)
    enabled = parse_enabled or {"Docling": True, "Marker": False, "PyMuPDF": True}
    warnings: list[str] = []
    debug: list[str] = []
    context = PdfParserContext(pdf_buffer=pdf_buffer, filename=filename, marker_timeout=marker_timeout)

    for strategy in strategies:
        if not enabled.get(strategy.name, False):
            continue
        attempt = strategy.parse(context)

        if attempt.warning:
            warnings.append(attempt.warning)
        if attempt.debug:
            debug.append(attempt.debug)
        if attempt.markdown:
            return ParsePipelineResult(
                markdown=attempt.markdown,
                parser_used=strategy.name,
                warnings=warnings,
                debug=debug,
            )

    return ParsePipelineResult(markdown=None, warnings=warnings, debug=debug)


async def normalize_document_text(
    content: str,
    *,
    content_format: str,
    filename: str = "document.txt",
) -> str | None:
    """Normalize non-PDF document content into canonical markdown."""
    result = await normalize_document_text_with_diagnostics(
        content,
        content_format=content_format,
        filename=filename,
    )
    return result.markdown


async def normalize_document_text_with_diagnostics(
    content: str,
    *,
    content_format: str,
    filename: str = "document.txt",
) -> ParsePipelineResult:
    """Normalize non-PDF content and preserve parser warnings/debug."""
    normalized_format = _normalize_content_format(content_format)
    strategy = resolve_document_normalizer(normalized_format)
    attempt = strategy.normalize(
        DocumentNormalizationContext(
            content=content,
            filename=filename,
            normalized_format=normalized_format,
        )
    )
    return ParsePipelineResult(
        markdown=attempt.markdown,
        parser_used=strategy.parser_used if attempt.markdown else "",
        warnings=[attempt.warning] if attempt.warning else [],
        debug=[attempt.debug] if attempt.debug else [],
    )


def _normalize_content_format(content_format: str) -> str:
    return content_format.strip().lower().replace("-", "_")


def _normalize_markdown(markdown: str) -> str | None:
    cleaned = markdown.strip()
    return cleaned if len(cleaned) > 100 else None


def _normalize_plain_text(text: str) -> str | None:
    cleaned = text.strip()
    return cleaned if len(cleaned) > 100 else None


def _parse_pymupdf(pdf_buffer: bytes) -> str | None:
    return _parse_pymupdf_attempt(pdf_buffer).markdown


def _parse_pymupdf_attempt(pdf_buffer: bytes) -> _ParserAttemptResult:
    """Parse PDF using pymupdf4llm as a light fallback parser."""
    try:
        import pymupdf
        import pymupdf4llm

        doc = pymupdf.open(stream=pdf_buffer, filetype="pdf")  # type: ignore[no-untyped-call]
        md = pymupdf4llm.to_markdown(doc)
        doc.close()  # type: ignore[no-untyped-call]
        cleaned = md.strip() if md else ""
        if len(cleaned) > 100:
            return _ParserAttemptResult(markdown=cleaned)
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("PyMuPDF", f"returned insufficient content ({len(cleaned)} chars)", fallback=False),
            debug=_parser_debug("PyMuPDF", f"normalized output below acceptance threshold ({len(cleaned)} chars)"),
        )
    except ImportError as exc:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("PyMuPDF", "unavailable", fallback=False),
            debug=_parser_debug("PyMuPDF", _exception_summary(exc)),
        )
    except Exception as exc:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("PyMuPDF", "parse failed", fallback=False),
            debug=_parser_debug("PyMuPDF", _exception_summary(exc)),
        )


def _parse_marker(pdf_buffer: bytes, filename: str, *, marker_timeout: int) -> str | None:
    return _parse_marker_attempt(pdf_buffer, filename, marker_timeout=marker_timeout).markdown


def _parse_marker_attempt(
    pdf_buffer: bytes,
    filename: str,
    *,
    marker_timeout: int,
) -> _ParserAttemptResult:
    """Parse PDF using marker-pdf in an isolated subprocess with timeout."""
    request = {
        "fileName": Path(filename).name,
        "pdfBase64": base64.b64encode(pdf_buffer).decode("ascii"),
    }
    timeout_seconds = None if marker_timeout <= 0 else max(marker_timeout / 1000.0, 0.001)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from grados.extract.parse import _marker_subprocess_main; raise SystemExit(_marker_subprocess_main())",
            ],
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("Marker", f"timed out after {marker_timeout} ms", fallback=True),
            debug=_parser_debug("Marker", f"subprocess timed out after {marker_timeout} ms"),
        )
    except Exception as exc:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("Marker", "parse failed", fallback=True),
            debug=_parser_debug("Marker", _exception_summary(exc)),
        )

    if result.returncode != 0 and not result.stdout.strip():
        stderr = result.stderr.strip() if result.stderr else f"subprocess exited with code {result.returncode}"
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("Marker", "parse failed", fallback=True),
            debug=_parser_debug("Marker", stderr[:240]),
        )

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("Marker", "returned invalid JSON", fallback=True),
            debug=_parser_debug("Marker", _exception_summary(exc)),
        )

    if not response.get("ok"):
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("Marker", "parse failed", fallback=True),
            debug=_parser_debug("Marker", str(response.get("error") or "worker returned ok=false")[:240]),
        )

    markdown = str(response.get("markdown") or "").strip()
    if len(markdown) > 100:
        return _ParserAttemptResult(markdown=markdown)
    return _ParserAttemptResult(
        markdown=None,
        warning=_parser_warning("Marker", f"returned insufficient content ({len(markdown)} chars)", fallback=True),
        debug=_parser_debug("Marker", f"normalized output below acceptance threshold ({len(markdown)} chars)"),
    )


def _parse_docling(pdf_buffer: bytes, filename: str) -> str | None:
    """Parse PDF using Docling (default, structural, optional dependency)."""
    return _parse_docling_attempt(pdf_buffer, filename).markdown


def _normalize_with_docling(content: bytes, *, suffix: str, filename: str) -> str | None:
    return _normalize_with_docling_attempt(
        content,
        suffix=suffix,
        filename=filename,
        context_label="normalization",
        fallback=False,
    ).markdown


def _parse_docling_attempt(pdf_buffer: bytes, filename: str) -> _ParserAttemptResult:
    return _normalize_with_docling_attempt(
        pdf_buffer,
        suffix=".pdf",
        filename=filename,
        context_label="parse",
        fallback=True,
    )


def _normalize_with_docling_attempt(
    content: bytes,
    *,
    suffix: str,
    filename: str,
    context_label: str,
    fallback: bool,
) -> _ParserAttemptResult:
    try:
        from docling.document_converter import DocumentConverter

        with tempfile.NamedTemporaryFile(suffix=suffix, prefix=_safe_stem(filename), delete=True) as tmp:
            tmp.write(content)
            tmp.flush()
            converter = DocumentConverter()
            result = converter.convert(Path(tmp.name))
            md = result.document.export_to_markdown()
            cleaned = md.strip() if md else ""
            if len(cleaned) > 100:
                return _ParserAttemptResult(markdown=cleaned)
            return _ParserAttemptResult(
                markdown=None,
                warning=_parser_warning(
                    "Docling",
                    f"returned insufficient content ({len(cleaned)} chars)",
                    fallback=fallback,
                ),
                debug=_parser_debug("Docling", f"normalized output below acceptance threshold ({len(cleaned)} chars)"),
            )
    except ImportError as exc:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("Docling", "unavailable", fallback=fallback),
            debug=_parser_debug("Docling", _exception_summary(exc)),
        )
    except Exception as exc:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("Docling", f"{context_label} failed", fallback=fallback),
            debug=_parser_debug("Docling", _exception_summary(exc)),
        )


def _safe_stem(filename: str) -> str:
    raw = Path(filename).stem or "document"
    safe = "".join(ch if ch.isalnum() else "_" for ch in raw)
    return (safe[:40] or "document") + "_"


def _marker_subprocess_main() -> int:
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        raw = sys.stdin.read()
        if not raw:
            raise ValueError("Empty JSON input.")

        request = json.loads(raw)
        file_name = Path(str(request.get("fileName") or "document.pdf")).name
        if not file_name.lower().endswith(".pdf"):
            file_name = f"{file_name}.pdf"

        pdf_bytes = base64.b64decode(str(request["pdfBase64"]))
        with tempfile.NamedTemporaryFile(suffix=".pdf", prefix=_safe_stem(file_name), delete=True) as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            converter = PdfConverter(artifact_dict=create_model_dict())
            result = converter(tmp.name)
        markdown = result.markdown if hasattr(result, "markdown") else str(result)
        response = {
            "ok": True,
            "markdown": _compact_markdown(markdown),
        }
    except Exception as exc:
        response = {
            "ok": False,
            "error": str(exc) or exc.__class__.__name__,
        }

    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.flush()
    return 0


def _compact_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return "\n\n".join(part for part in text.split("\n\n") if part.strip()).strip()


def _parser_warning(parser: str, reason: str, *, fallback: bool) -> str:
    suffix = "; falling back to next parser" if fallback else ""
    return f"{parser}: {reason}{suffix}."


def _parser_debug(parser: str, detail: str) -> str:
    return f"{parser} debug: {detail}"


def _exception_summary(exc: Exception, *, limit: int = 240) -> str:
    message = f"{exc.__class__.__name__}: {exc}" if str(exc).strip() else exc.__class__.__name__
    compact = " ".join(message.split())
    return compact[:limit]


def prewarm_docling_models() -> ParsePipelineResult:
    """Trigger a tiny Docling PDF parse to download models during `grados setup`."""
    try:
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 550, 780),
            (
                "GRaDOS Docling Prewarm\n\n"
                "Abstract\n"
                + ("This warmup document primes Docling model downloads for later PDF parsing. " * 20)
                + "\n\nMethods\n"
                + ("This section exists to ensure the generated markdown is long enough to validate. " * 12)
            ),
            fontsize=11,
        )
        pdf_buffer = doc.tobytes()
        doc.close()
    except Exception as exc:
        return ParsePipelineResult(
            markdown=None,
            warnings=[_parser_warning("Docling", "prewarm failed", fallback=False)],
            debug=[_parser_debug("Docling", _exception_summary(exc))],
        )

    attempt = _parse_docling_attempt(pdf_buffer, "grados-docling-prewarm.pdf")
    return ParsePipelineResult(
        markdown=attempt.markdown,
        parser_used="Docling" if attempt.markdown else "",
        warnings=[attempt.warning] if attempt.warning else [],
        debug=[attempt.debug] if attempt.debug else [],
    )
