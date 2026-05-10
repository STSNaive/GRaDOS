"""Document normalization pipeline: Docling first, then optional fallbacks."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import httpx

from grados.http_limits import (
    DEFAULT_MAX_MINERU_FULL_MD_BYTES,
    DEFAULT_MAX_MINERU_ZIP_BYTES,
    ensure_byte_limit,
    limited_sync_get,
)


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
    mineru_api_key: str = ""
    mineru_model_version: str = "vlm"
    mineru_language: str = "en"
    mineru_timeout: int = 300000
    mineru_poll_interval: float = 3.0
    mineru_enable_formula: bool = True
    mineru_enable_table: bool = True
    mineru_is_ocr: bool = False
    mineru_max_zip_bytes: int = DEFAULT_MAX_MINERU_ZIP_BYTES
    mineru_max_full_md_bytes: int = DEFAULT_MAX_MINERU_FULL_MD_BYTES


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


def _run_mineru_pdf_parser(context: PdfParserContext) -> _ParserAttemptResult:
    return _parse_mineru_attempt(
        context.pdf_buffer,
        context.filename,
        api_key=context.mineru_api_key,
        model_version=context.mineru_model_version,
        language=context.mineru_language,
        timeout_ms=context.mineru_timeout,
        poll_interval=context.mineru_poll_interval,
        enable_formula=context.mineru_enable_formula,
        enable_table=context.mineru_enable_table,
        is_ocr=context.mineru_is_ocr,
        max_zip_bytes=context.mineru_max_zip_bytes,
        max_full_md_bytes=context.mineru_max_full_md_bytes,
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
    "Docling": cast(PdfParserStrategy, _FunctionPdfParserStrategy("Docling", _run_docling_pdf_parser)),
    "MinerU": cast(PdfParserStrategy, _FunctionPdfParserStrategy("MinerU", _run_mineru_pdf_parser)),
    "Marker": cast(PdfParserStrategy, _FunctionPdfParserStrategy("Marker", _run_marker_pdf_parser)),
    "PyMuPDF": cast(PdfParserStrategy, _FunctionPdfParserStrategy("PyMuPDF", _run_pymupdf_pdf_parser)),
}

DOCUMENT_NORMALIZER_STRATEGIES: tuple[DocumentNormalizerStrategy, ...] = (
    cast(
        DocumentNormalizerStrategy,
        _FormatDocumentNormalizerStrategy("Markdown", "Markdown", ("markdown", "md"), _run_markdown_normalizer),
    ),
    cast(
        DocumentNormalizerStrategy,
        _FormatDocumentNormalizerStrategy("PlainText", "PlainText", ("text", "txt"), _run_plain_text_normalizer),
    ),
    cast(
        DocumentNormalizerStrategy,
        _FormatDocumentNormalizerStrategy("DoclingHTML", "Docling", ("html", "htm"), _run_docling_html_normalizer),
    ),
    cast(
        DocumentNormalizerStrategy,
        _FormatDocumentNormalizerStrategy(
            "DoclingXML",
            "Docling",
            ("xml", "jats", "jats_xml"),
            _run_docling_xml_normalizer,
        ),
    ),
)


def build_pdf_parser_strategies(order: list[str] | None = None) -> list[PdfParserStrategy]:
    resolved_order = order or ["Docling", "MinerU", "Marker", "PyMuPDF"]
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
    mineru_api_key: str = "",
    mineru_model_version: str = "vlm",
    mineru_language: str = "en",
    mineru_timeout: int = 300000,
    mineru_poll_interval: float = 3.0,
    mineru_enable_formula: bool = True,
    mineru_enable_table: bool = True,
    mineru_is_ocr: bool = False,
    mineru_max_zip_bytes: int = DEFAULT_MAX_MINERU_ZIP_BYTES,
    mineru_max_full_md_bytes: int = DEFAULT_MAX_MINERU_FULL_MD_BYTES,
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
        mineru_api_key=mineru_api_key,
        mineru_model_version=mineru_model_version,
        mineru_language=mineru_language,
        mineru_timeout=mineru_timeout,
        mineru_poll_interval=mineru_poll_interval,
        mineru_enable_formula=mineru_enable_formula,
        mineru_enable_table=mineru_enable_table,
        mineru_is_ocr=mineru_is_ocr,
        mineru_max_zip_bytes=mineru_max_zip_bytes,
        mineru_max_full_md_bytes=mineru_max_full_md_bytes,
    )
    return result.markdown


async def parse_pdf_with_diagnostics(
    pdf_buffer: bytes,
    filename: str = "paper.pdf",
    parse_order: list[str] | None = None,
    parse_enabled: dict[str, bool] | None = None,
    marker_timeout: int = 120000,
    mineru_api_key: str = "",
    mineru_model_version: str = "vlm",
    mineru_language: str = "en",
    mineru_timeout: int = 300000,
    mineru_poll_interval: float = 3.0,
    mineru_enable_formula: bool = True,
    mineru_enable_table: bool = True,
    mineru_is_ocr: bool = False,
    mineru_max_zip_bytes: int = DEFAULT_MAX_MINERU_ZIP_BYTES,
    mineru_max_full_md_bytes: int = DEFAULT_MAX_MINERU_FULL_MD_BYTES,
) -> ParsePipelineResult:
    """Parse a PDF buffer and preserve parser warnings/debug for caller-facing receipts."""
    strategies = build_pdf_parser_strategies(parse_order)
    enabled = parse_enabled or {"Docling": True, "MinerU": True, "Marker": False, "PyMuPDF": True}
    warnings: list[str] = []
    debug: list[str] = []
    context = PdfParserContext(
        pdf_buffer=pdf_buffer,
        filename=filename,
        marker_timeout=marker_timeout,
        mineru_api_key=mineru_api_key,
        mineru_model_version=mineru_model_version,
        mineru_language=mineru_language,
        mineru_timeout=mineru_timeout,
        mineru_poll_interval=mineru_poll_interval,
        mineru_enable_formula=mineru_enable_formula,
        mineru_enable_table=mineru_enable_table,
        mineru_is_ocr=mineru_is_ocr,
        mineru_max_zip_bytes=mineru_max_zip_bytes,
        mineru_max_full_md_bytes=mineru_max_full_md_bytes,
    )

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
        import pymupdf4llm  # type: ignore[import-untyped]

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


MINERU_API_BASE = "https://mineru.net/api/v4"


def _parse_mineru(
    pdf_buffer: bytes,
    filename: str,
    *,
    api_key: str = "",
    model_version: str = "vlm",
    language: str = "en",
    timeout_ms: int = 300000,
    poll_interval: float = 3.0,
    enable_formula: bool = True,
    enable_table: bool = True,
    is_ocr: bool = False,
    max_zip_bytes: int = DEFAULT_MAX_MINERU_ZIP_BYTES,
    max_full_md_bytes: int = DEFAULT_MAX_MINERU_FULL_MD_BYTES,
) -> str | None:
    return _parse_mineru_attempt(
        pdf_buffer,
        filename,
        api_key=api_key,
        model_version=model_version,
        language=language,
        timeout_ms=timeout_ms,
        poll_interval=poll_interval,
        enable_formula=enable_formula,
        enable_table=enable_table,
        is_ocr=is_ocr,
        max_zip_bytes=max_zip_bytes,
        max_full_md_bytes=max_full_md_bytes,
    ).markdown


def _parse_mineru_attempt(
    pdf_buffer: bytes,
    filename: str,
    *,
    api_key: str,
    model_version: str,
    language: str,
    timeout_ms: int,
    poll_interval: float,
    enable_formula: bool,
    enable_table: bool,
    is_ocr: bool,
    max_zip_bytes: int = DEFAULT_MAX_MINERU_ZIP_BYTES,
    max_full_md_bytes: int = DEFAULT_MAX_MINERU_FULL_MD_BYTES,
) -> _ParserAttemptResult:
    """Parse PDF through MinerU's authenticated cloud API."""
    token = (api_key or os.environ.get("MINERU_API_KEY", "")).strip()
    if not token:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("MinerU", "MINERU_API_KEY not configured", fallback=True),
            debug=_parser_debug("MinerU", "cloud parser skipped because no API token was available"),
        )

    timeout_seconds = max(timeout_ms / 1000.0, 1.0)
    poll_sleep = max(poll_interval, 0.5)
    deadline = time.monotonic() + timeout_seconds
    headers = {
        "Authorization": _mineru_auth_header(token),
        "Content-Type": "application/json",
        "Accept": "*/*",
    }

    try:
        with httpx.Client(
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=120.0, pool=15.0),
            follow_redirects=True,
        ) as client:
            batch_id, data_id, upload_url = _create_mineru_upload_task(
                client,
                pdf_buffer,
                filename,
                headers=headers,
                model_version=model_version,
                language=language,
                enable_formula=enable_formula,
                enable_table=enable_table,
                is_ocr=is_ocr,
            )
            upload = client.put(upload_url, content=pdf_buffer, headers={})
            upload.raise_for_status()
            while time.monotonic() < deadline:
                try:
                    result = _poll_mineru_batch_result(client, batch_id, data_id, headers=headers)
                except _MinerUApiError as exc:
                    if exc.code == -60012:
                        time.sleep(poll_sleep)
                        continue
                    raise
                state = str(result.get("state") or "")
                if state == "done":
                    zip_url = str(result.get("full_zip_url") or "")
                    if not zip_url:
                        raise ValueError("MinerU task completed without full_zip_url.")
                    markdown = _download_mineru_markdown(
                        client,
                        zip_url,
                        max_zip_bytes=max_zip_bytes,
                        max_full_md_bytes=max_full_md_bytes,
                    )
                    if len(markdown) > 100:
                        return _ParserAttemptResult(
                            markdown=markdown,
                            debug=_parser_debug("MinerU", f"batch_id={batch_id}, data_id={data_id}"),
                        )
                    return _ParserAttemptResult(
                        markdown=None,
                        warning=_parser_warning(
                            "MinerU",
                            f"returned insufficient content ({len(markdown)} chars)",
                            fallback=True,
                        ),
                        debug=_parser_debug("MinerU", f"batch_id={batch_id}, data_id={data_id}"),
                    )
                if state == "failed":
                    reason = str(result.get("err_msg") or result.get("err_code") or "extract failed")
                    return _ParserAttemptResult(
                        markdown=None,
                        warning=_parser_warning("MinerU", reason[:160], fallback=True),
                        debug=_parser_debug("MinerU", f"batch_id={batch_id}, data_id={data_id}, state=failed"),
                    )
                time.sleep(poll_sleep)
            return _ParserAttemptResult(
                markdown=None,
                warning=_parser_warning("MinerU", f"timed out after {timeout_ms} ms", fallback=True),
                debug=_parser_debug("MinerU", f"batch_id={batch_id}, data_id={data_id}"),
            )
    except Exception as exc:
        return _ParserAttemptResult(
            markdown=None,
            warning=_parser_warning("MinerU", "parse failed", fallback=True),
            debug=_parser_debug("MinerU", _exception_summary(exc)),
        )


def _create_mineru_upload_task(
    client: httpx.Client,
    pdf_buffer: bytes,
    filename: str,
    *,
    headers: dict[str, str],
    model_version: str,
    language: str,
    enable_formula: bool,
    enable_table: bool,
    is_ocr: bool,
) -> tuple[str, str, str]:
    file_name = Path(filename).name or "document.pdf"
    if not file_name.lower().endswith(".pdf"):
        file_name = f"{file_name}.pdf"
    data_id = hashlib.sha256(pdf_buffer).hexdigest()[:24]
    payload: dict[str, Any] = {
        "files": [
            {
                "name": file_name,
                "data_id": data_id,
                "is_ocr": is_ocr,
            }
        ],
        "model_version": model_version or "vlm",
        "enable_formula": enable_formula,
        "enable_table": enable_table,
    }
    if language:
        payload["language"] = language

    response = client.post(f"{MINERU_API_BASE}/file-urls/batch", headers=headers, json=payload)
    data = _mineru_response_data(response)
    batch_id = _required_mineru_string(data, "batch_id")
    file_urls = data.get("file_urls")
    if not isinstance(file_urls, list) or not file_urls:
        raise ValueError("MinerU did not return an upload URL.")
    upload_url = str(file_urls[0] or "")
    if not upload_url:
        raise ValueError("MinerU returned an empty upload URL.")
    return batch_id, data_id, upload_url


def _mineru_auth_header(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


class _MinerUApiError(ValueError):
    def __init__(self, code: object, message: str) -> None:
        self.code = code
        super().__init__(f"MinerU API returned code {code}: {message}")


def _poll_mineru_batch_result(
    client: httpx.Client,
    batch_id: str,
    data_id: str,
    *,
    headers: dict[str, str],
) -> dict[str, Any]:
    response = client.get(f"{MINERU_API_BASE}/extract-results/batch/{batch_id}", headers=headers)
    data = _mineru_response_data(response)
    extract_result = data.get("extract_result")
    if not isinstance(extract_result, list) or not extract_result:
        raise ValueError("MinerU batch result was empty.")
    for item in extract_result:
        if isinstance(item, dict) and str(item.get("data_id") or "") == data_id:
            return item
    first = extract_result[0]
    if not isinstance(first, dict):
        raise ValueError("MinerU batch result item was not an object.")
    return first


def _download_mineru_markdown(
    client: httpx.Client,
    zip_url: str,
    *,
    max_zip_bytes: int = DEFAULT_MAX_MINERU_ZIP_BYTES,
    max_full_md_bytes: int = DEFAULT_MAX_MINERU_FULL_MD_BYTES,
) -> str:
    response = limited_sync_get(
        client,
        zip_url,
        max_bytes=max_zip_bytes,
        label="MinerU result zip",
    )
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        markdown_names = [
            name for name in archive.namelist() if Path(name).name == "full.md" or name.endswith("/full.md")
        ]
        if not markdown_names:
            raise ValueError("MinerU result zip did not contain full.md.")
        info = archive.getinfo(markdown_names[0])
        ensure_byte_limit(
            info.file_size,
            max_bytes=max_full_md_bytes,
            label="MinerU full.md",
        )
        with archive.open(info) as handle:
            markdown_bytes = handle.read(max_full_md_bytes + 1)
            ensure_byte_limit(
                len(markdown_bytes),
                max_bytes=max_full_md_bytes,
                label="MinerU full.md",
            )
            return _compact_markdown(markdown_bytes.decode("utf-8", errors="replace"))


def _mineru_response_data(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("MinerU returned non-object JSON.")
    code = payload.get("code")
    if code != 0:
        message = str(payload.get("msg") or "unknown MinerU API error")
        raise _MinerUApiError(code, message)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("MinerU response missing data object.")
    return data


def _required_mineru_string(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "")
    if not value:
        raise ValueError(f"MinerU response missing {key}.")
    return value


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
        from marker.converters.pdf import PdfConverter  # type: ignore[import-not-found]
        from marker.models import create_model_dict  # type: ignore[import-not-found]

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

        doc = pymupdf.open()  # type: ignore[no-untyped-call]
        page = doc.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 550, 780),  # type: ignore[no-untyped-call]
            (
                "GRaDOS Docling Prewarm\n\n"
                "Abstract\n"
                + ("This warmup document primes Docling model downloads for later PDF parsing. " * 20)
                + "\n\nMethods\n"
                + ("This section exists to ensure the generated markdown is long enough to validate. " * 12)
            ),
            fontsize=11,
        )
        pdf_buffer = doc.tobytes()  # type: ignore[no-untyped-call]
        doc.close()  # type: ignore[no-untyped-call]
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
