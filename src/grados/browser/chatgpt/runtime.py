"""ChatGPT browser-mode runner for external synthesis."""

from __future__ import annotations

from typing import Any

from grados.browser.chatgpt.capture import capture_final_response
from grados.browser.chatgpt.composer import (
    clear_prompt_composer,
    open_new_chat,
    paste_prompt,
    read_conversation_turn_count,
    submit_prompt,
    wait_for_assistant_done,
)
from grados.browser.chatgpt.errors import ChatGPTBrowserError
from grados.browser.chatgpt.lock import chatgpt_profile_lock
from grados.browser.chatgpt.login import (
    ensure_chatgpt_logged_in,
    probe_chatgpt_login,
    wait_for_chatgpt_login,
)
from grados.browser.chatgpt.model_selection import ensure_latest_pro_model
from grados.browser.chatgpt.profile import ensure_chatgpt_profile_ready
from grados.browser.chatgpt.selectors import CHATGPT_URL, ORACLE_CHROME_FLAGS
from grados.browser.chatgpt.session_store import (
    ChatGPTSessionStore,
    is_valid_chatgpt_session_id,
    new_session_id,
)
from grados.browser.chatgpt.thinking import ensure_oracle_pro_extended_thinking
from grados.browser.chatgpt.types import (
    BROWSER_MODE_VERSION,
    DEFAULT_PROMPT_CHAR_LIMIT,
    ChatGPTBrowserResult,
    ChatGPTCapture,
    ChatGPTModelSelection,
    ChatGPTThinkingSelection,
)
from grados.browser.manager import (
    launch_browser_session,
    random_viewport,
    resolve_browser_executable,
)
from grados.config import GRaDOSPaths, HeadlessBrowserConfig


async def run_chatgpt_browser_session(
    paths: GRaDOSPaths,
    browser_config: HeadlessBrowserConfig,
    *,
    prompt: str,
    pack_id: str,
    packet_artifact_id: str,
    prompt_hash: str,
    mode: str,
    metadata: dict[str, Any] | None = None,
    recover_session_id: str = "",
    prompt_char_limit: int = DEFAULT_PROMPT_CHAR_LIMIT,
) -> ChatGPTBrowserResult:
    """Run or recover one ChatGPT browser session."""
    store = ChatGPTSessionStore(paths.chatgpt_browser_sessions)
    session_id = recover_session_id or new_session_id()
    if recover_session_id and not is_valid_chatgpt_session_id(recover_session_id):
        return _error_result(
            session_id,
            ChatGPTBrowserError(
                code="invalid_browser_session_id",
                stage="session",
                message="ChatGPT browser session id is invalid.",
                details={"recover_session_id": recover_session_id},
            ),
            status="failed",
        )
    if not recover_session_id and len(prompt) > prompt_char_limit:
        return _error_result(
            session_id,
            ChatGPTBrowserError(
                code="prompt_too_large",
                stage="prompt-size",
                message="External synthesis packet is too large for inline ChatGPT browser submission.",
                details={
                    "estimated_chars": len(prompt),
                    "prompt_char_limit": prompt_char_limit,
                    "next_action": "reduce max_items or max_excerpt_chars",
                },
            ),
            status="failed",
        )

    record = store.read(session_id) if recover_session_id else None
    if recover_session_id:
        if not record:
            return _error_result(
                session_id,
                ChatGPTBrowserError(
                    code="browser_session_not_found",
                    stage="session",
                    message="No saved ChatGPT browser session was found for recovery.",
                    details={"recover_session_id": recover_session_id},
                ),
                status="failed",
            )
        pack_id = str(record.get("pack_id") or pack_id)
        packet_artifact_id = str(record.get("packet_artifact_id") or packet_artifact_id)
        prompt_hash = str(record.get("prompt_hash") or prompt_hash)
    else:
        record = store.create(
            session_id=session_id,
            pack_id=pack_id,
            packet_artifact_id=packet_artifact_id,
            prompt_hash=prompt_hash,
            prompt=prompt,
            mode=mode,
            metadata={**(metadata or {}), "browser_mode_version": BROWSER_MODE_VERSION},
        )

    try:
        ensure_chatgpt_profile_ready(paths.chatgpt_browser_profile, setup_mode=False)
        async with chatgpt_profile_lock(
            paths.chatgpt_browser_profile,
            purpose="external_synthesis",
            session_id=session_id,
        ):
            browser_session = await _launch_private_profile(paths, browser_config)
            try:
                page = browser_session.root_page
                await _run_page_flow(
                    page,
                    store=store,
                    session_id=session_id,
                    prompt=prompt,
                    recover=bool(recover_session_id),
                    record=record,
                )
                final_record = store.read(session_id) or record
                response_text = str(final_record.get("response_text") or "")
                model = _model_from_record(final_record)
                thinking = _thinking_from_record(final_record)
                capture = ChatGPTCapture(
                    response_text=response_text,
                    method=str(final_record.get("capture_method") or ""),
                    warnings=list(final_record.get("capture_warnings") or []),
                )
                return ChatGPTBrowserResult(
                    ok=True,
                    status="captured",
                    session_id=session_id,
                    response_text=response_text,
                    conversation_url=str(final_record.get("conversation_url") or ""),
                    model=model,
                    thinking=thinking,
                    capture=capture,
                    session_record_path=str(store.session_json(session_id)),
                    metadata={
                        "browser_mode_version": BROWSER_MODE_VERSION,
                        "session_record": str(store.session_json(session_id)),
                        "prompt_path": str(store.prompt_path(session_id)),
                        "response_path": str(store.response_path(session_id)),
                        "pack_id": str(final_record.get("pack_id") or ""),
                        "packet_artifact_id": str(final_record.get("packet_artifact_id") or ""),
                        "prompt_hash": str(final_record.get("prompt_hash") or ""),
                        "mode": str(final_record.get("mode") or ""),
                    },
                )
            finally:
                await browser_session.cleanup()
    except ChatGPTBrowserError as exc:
        status = "incomplete_capture" if exc.code in {"assistant_timeout", "capture_failed"} else "failed"
        store.update(session_id, status=status, error=exc.to_dict())
        return _error_result(session_id, exc, status=status, session_record_path=str(store.session_json(session_id)))
    except Exception as exc:
        error = ChatGPTBrowserError(
            code="browser_run_failed",
            stage="browser",
            message=str(exc),
        )
        store.update(session_id, status="failed", error=error.to_dict())
        return _error_result(
            session_id,
            error,
            status="failed",
            session_record_path=str(store.session_json(session_id)),
        )


async def _run_page_flow(
    page: Any,
    *,
    store: ChatGPTSessionStore,
    session_id: str,
    prompt: str,
    recover: bool,
    record: dict[str, Any],
) -> None:
    if recover:
        conversation_url = str(record.get("conversation_url") or "")
        if not conversation_url:
            raise ChatGPTBrowserError(
                code="conversation_url_missing",
                stage="recovery",
                message="Saved ChatGPT browser session has no conversation URL to recover.",
                details={"session_id": session_id},
            )
        await page.goto(conversation_url, wait_until="domcontentloaded", timeout=60_000)
        await ensure_chatgpt_logged_in(page)
    else:
        await open_new_chat(page)
        await ensure_chatgpt_logged_in(page)
        model = await ensure_latest_pro_model(page)
        store.update(session_id, model_selection=model.to_dict())
        thinking = await ensure_oracle_pro_extended_thinking(page)
        store.update(session_id, thinking_selection=thinking.to_dict())
        await clear_prompt_composer(page)
        baseline_turns = await read_conversation_turn_count(page)
        await paste_prompt(page, prompt)
        min_turn_index = await submit_prompt(page, prompt, baseline_turns=baseline_turns)
        store.update(session_id, conversation_url=str(getattr(page, "url", "")))
        await wait_for_assistant_done(page, min_turn_index=min_turn_index)

    capture = await capture_final_response(page)
    response_path = store.save_response(session_id, capture.response_text)
    store.update(
        session_id,
        status="captured",
        conversation_url=str(getattr(page, "url", "")),
        response_text=capture.response_text,
        response_path=response_path,
        capture_method=capture.method,
        capture_warnings=capture.warnings,
    )


async def open_chatgpt_login_setup(
    paths: GRaDOSPaths,
    browser_config: HeadlessBrowserConfig,
    *,
    timeout_seconds: float,
    keep_open: bool,
) -> dict[str, object]:
    """Open the private profile so the user can sign in once."""
    paths.chatgpt_browser_profile.mkdir(parents=True, exist_ok=True)
    browser_session = await _launch_private_profile(paths, browser_config)
    try:
        page = browser_session.root_page
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60_000)
        result = await wait_for_chatgpt_login(page, timeout_seconds=timeout_seconds)
        if keep_open:
            return {**result, "profile": str(paths.chatgpt_browser_profile)}
        return {**result, "profile": str(paths.chatgpt_browser_profile)}
    finally:
        if not keep_open:
            try:
                await browser_session.cleanup()
            except Exception:
                pass


async def check_chatgpt_login(
    paths: GRaDOSPaths,
    browser_config: HeadlessBrowserConfig,
) -> dict[str, object]:
    ensure_chatgpt_profile_ready(paths.chatgpt_browser_profile, setup_mode=False)
    browser_session = await _launch_private_profile(paths, browser_config)
    try:
        page = browser_session.root_page
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60_000)
        result = await probe_chatgpt_login(page)
        return {**result, "profile": str(paths.chatgpt_browser_profile)}
    finally:
        await browser_session.cleanup()


async def _launch_private_profile(paths: GRaDOSPaths, browser_config: HeadlessBrowserConfig) -> Any:
    resolution = resolve_browser_executable(browser_config, paths)
    if resolution is None:
        raise ChatGPTBrowserError(
            code="browser_executable_not_found",
            stage="browser-launch",
            message="No Chrome/Chromium executable was found for ChatGPT browser mode.",
        )
    paths.chatgpt_browser_profile.mkdir(parents=True, exist_ok=True)
    paths.chatgpt_browser_sessions.mkdir(parents=True, exist_ok=True)
    return await launch_browser_session(
        executable_path=resolution.executable_path,
        viewport=random_viewport(),
        user_data_dir=str(paths.chatgpt_browser_profile),
        headless=False,
        extra_args=ORACLE_CHROME_FLAGS,
    )


def _model_from_record(record: dict[str, Any]) -> ChatGPTModelSelection | None:
    raw = record.get("model_selection")
    if not isinstance(raw, dict):
        return None
    return ChatGPTModelSelection(
        requested=str(raw.get("requested") or ""),
        resolved_label=str(raw.get("resolved_label") or ""),
        available_labels=[str(item) for item in raw.get("available_labels") or []],
        strategy=str(raw.get("strategy") or ""),
        verified=bool(raw.get("verified")),
    )


def _thinking_from_record(record: dict[str, Any]) -> ChatGPTThinkingSelection | None:
    raw = record.get("thinking_selection")
    if not isinstance(raw, dict):
        return None
    return ChatGPTThinkingSelection(
        requested=str(raw.get("requested") or ""),
        resolved_label=str(raw.get("resolved_label") or ""),
        available_labels=[str(item) for item in raw.get("available_labels") or []],
        rank=int(raw.get("rank") or 0),
        verified=bool(raw.get("verified")),
    )


def _error_result(
    session_id: str,
    error: ChatGPTBrowserError,
    *,
    status: str,
    session_record_path: str = "",
) -> ChatGPTBrowserResult:
    return ChatGPTBrowserResult(
        ok=False,
        status=status,  # type: ignore[arg-type]
        session_id=session_id,
        error=error.message,
        error_code=error.code,
        session_record_path=session_record_path,
        metadata={"browser_mode_version": BROWSER_MODE_VERSION, **error.to_dict()},
    )
