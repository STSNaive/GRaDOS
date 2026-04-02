from __future__ import annotations

from pathlib import Path

from grados.browser.manager import (
    VIEWPORTS,
    _get_managed_chromium_suffixes,
    random_viewport,
    resolve_browser_executable,
)
from grados.config import GRaDOSPaths, HeadlessBrowserConfig
from grados.publisher.common import classify_pdf_content, detect_bot_challenge


def test_resolve_browser_executable_prefers_managed_browser(tmp_path: Path) -> None:
    paths = GRaDOSPaths(tmp_path / "grados-home")
    suffix = _get_managed_chromium_suffixes()[0]
    executable = paths.browser_chromium / "chromium-1234" / suffix
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("stub", encoding="utf-8")

    resolution = resolve_browser_executable(HeadlessBrowserConfig(), paths)

    assert resolution is not None
    assert resolution.source == "managed"
    assert resolution.executable_path == str(executable)
    assert resolution.profile_directory == str(paths.browser_profile)


def test_random_viewport_uses_known_fingerprint_set() -> None:
    assert random_viewport() in VIEWPORTS


def test_pdf_classification_and_bot_detection() -> None:
    pdf_data = b"%PDF-1.4\n%stub"
    html_data = b"<html><title>Just a moment</title><body>captcha</body></html>"

    assert classify_pdf_content(pdf_data, "application/pdf") == {"is_pdf": True, "reason": "ok"}
    assert classify_pdf_content(html_data, "text/html")["reason"] == "html_or_challenge_page"
    assert detect_bot_challenge("Just a moment...", "<html>captcha</html>", "https://example.com") is True
