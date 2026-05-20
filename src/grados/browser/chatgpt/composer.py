"""Oracle-aligned ChatGPT composer operations."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from grados.browser.chatgpt.assistant_response import read_assistant_snapshot
from grados.browser.chatgpt.errors import ChatGPTBrowserError
from grados.browser.chatgpt.selectors import (
    ASSISTANT_ROLE_SELECTOR,
    CHATGPT_URL,
    CONVERSATION_TURN_SELECTOR,
    FINISHED_ACTIONS_SELECTOR,
    INPUT_SELECTORS,
    PROMPT_FALLBACK_SELECTOR,
    PROMPT_PRIMARY_SELECTOR,
    SEND_BUTTON_SELECTORS,
    STOP_BUTTON_SELECTOR,
)


async def open_new_chat(page: Any) -> None:
    await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_load_state("domcontentloaded")
    await wait_for_dom_ready(page, timeout_ms=45_000)


async def read_conversation_turn_count(page: Any) -> int | None:
    try:
        value = await page.evaluate(
            "document.querySelectorAll(__TURN_SELECTOR__).length".replace(
                "__TURN_SELECTOR__", json.dumps(CONVERSATION_TURN_SELECTOR)
            )
        )
    except Exception:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def clear_prompt_composer(page: Any) -> None:
    value = await page.evaluate(_clear_composer_expression())
    if not isinstance(value, dict) or not value.get("cleared") or value.get("remaining"):
        raise ChatGPTBrowserError(
            code="composer_clear_failed",
            stage="composer",
            message="Failed to clear ChatGPT prompt composer.",
            details={"result": value if isinstance(value, dict) else None},
        )
    await page.wait_for_timeout(250)


async def paste_prompt(page: Any, prompt: str) -> None:
    await wait_for_dom_ready(page)
    focus = await page.evaluate(_focus_composer_expression())
    if not isinstance(focus, dict) or not focus.get("focused"):
        raise ChatGPTBrowserError(
            code="composer_unavailable",
            stage="composer",
            message="Failed to focus ChatGPT prompt composer.",
        )

    await page.keyboard.insert_text(prompt)
    await page.wait_for_timeout(500)

    verification = await page.evaluate(_composer_value_expression())
    observed_length = _observed_prompt_length(verification)
    if observed_length <= 0:
        await page.evaluate(_force_prompt_expression(prompt))
        await page.wait_for_timeout(250)
        verification = await page.evaluate(_composer_value_expression())
        observed_length = _observed_prompt_length(verification)

    prompt_length = len(prompt)
    if observed_length <= 0:
        raise ChatGPTBrowserError(
            code="composer_insert_failed",
            stage="composer",
            message="Unable to insert the ChatGPT prompt into the composer.",
        )
    if prompt_length >= 50_000 and observed_length < prompt_length - 2_000:
        raise ChatGPTBrowserError(
            code="prompt_too_large",
            stage="composer",
            message="Prompt appears truncated in the ChatGPT composer.",
            details={"prompt_length": prompt_length, "observed_length": observed_length},
        )


async def submit_prompt(
    page: Any,
    prompt: str = "",
    *,
    baseline_turns: int | None = None,
) -> int | None:
    clicked = await _attempt_send_button(page)
    if not clicked:
        await page.keyboard.press("Enter")
    if prompt:
        return await _verify_prompt_committed(page, prompt, baseline_turns=baseline_turns)
    return None


async def wait_for_assistant_done(
    page: Any,
    *,
    timeout_seconds: float = 900.0,
    min_turn_index: int | None = None,
) -> None:
    deadline = time.monotonic() + max(10.0, timeout_seconds)
    latest_length = 0
    stable_cycles = 0
    latest_snapshot: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        snapshot = await read_assistant_snapshot(page, min_turn_index=min_turn_index)
        if snapshot:
            latest_snapshot = snapshot
            text_length = len(str(snapshot.get("text") or ""))
            if text_length > latest_length:
                latest_length = text_length
                stable_cycles = 0
            else:
                stable_cycles += 1

            stop_visible, finished_visible = await asyncio.gather(
                _is_stop_button_visible(page),
                _is_completion_visible(page),
            )
            if finished_visible or (not stop_visible and stable_cycles >= _stable_target(text_length)):
                return
        await asyncio.sleep(0.4)

    raise ChatGPTBrowserError(
        code="assistant_timeout",
        stage="assistant-wait",
        message="Timed out waiting for ChatGPT response generation to finish.",
        details={
            "timeout_seconds": timeout_seconds,
            "conversation_url": getattr(page, "url", ""),
            "last_observed_length": latest_length,
            "had_snapshot": latest_snapshot is not None,
        },
    )


async def wait_for_dom_ready(page: Any, *, timeout_ms: int = 10_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            value = await page.evaluate(_dom_ready_expression())
            if isinstance(value, dict) and value.get("ready") and value.get("composer"):
                return
        except Exception:
            pass
        await asyncio.sleep(0.15)


async def _attempt_send_button(page: Any) -> bool:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = await page.evaluate(_send_button_expression())
        if value == "clicked":
            return True
        if value == "missing":
            break
        await asyncio.sleep(0.1)
    return False


async def _verify_prompt_committed(
    page: Any,
    prompt: str,
    *,
    baseline_turns: int | None = None,
    timeout_ms: int = 60_000,
) -> int | None:
    deadline = time.monotonic() + timeout_ms / 1000
    baseline = baseline_turns if isinstance(baseline_turns, int) and baseline_turns >= 0 else -1
    expression = _prompt_commit_expression(prompt, baseline)
    last_value: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        value = await page.evaluate(expression)
        last_value = value if isinstance(value, dict) else None
        if last_value:
            turns_count = last_value.get("turnsCount")
            matches_prompt = bool(
                last_value.get("lastMatched")
                or last_value.get("userMatched")
                or last_value.get("prefixMatched")
            )
            baseline_unknown = int(last_value.get("baseline", baseline)) < 0
            if matches_prompt and (baseline_unknown or last_value.get("hasNewTurn")):
                return int(turns_count) if isinstance(turns_count, int) else None
            fallback_commit = (
                last_value.get("composerCleared")
                and last_value.get("hasNewTurn")
                and (
                    last_value.get("stopVisible")
                    or last_value.get("assistantVisible")
                    or last_value.get("inConversation")
                )
            )
            if fallback_commit:
                return int(turns_count) if isinstance(turns_count, int) else None
        await asyncio.sleep(0.1)

    code = "prompt_too_large" if len(prompt.strip()) >= 50_000 else "prompt_commit_failed"
    raise ChatGPTBrowserError(
        code=code,
        stage="composer",
        message="Prompt did not appear in the ChatGPT conversation before timeout.",
        details={"last_state": last_value, "prompt_length": len(prompt.strip())},
    )


async def _is_stop_button_visible(page: Any) -> bool:
    try:
        return bool(await page.evaluate("Boolean(document.querySelector(__STOP_SELECTOR__))".replace(
            "__STOP_SELECTOR__", json.dumps(STOP_BUTTON_SELECTOR)
        )))
    except Exception:
        return False


async def _is_completion_visible(page: Any) -> bool:
    try:
        return bool(await page.evaluate(
            """(() => {
              const turns = Array.from(document.querySelectorAll(__TURN_SELECTOR__));
              let lastAssistantTurn = null;
              const ASSISTANT_SELECTOR = __ASSISTANT_SELECTOR__;
              const isAssistantTurn = (node) => {
                if (!(node instanceof HTMLElement)) return false;
                const turnAttr = (node.getAttribute("data-turn") || node.dataset?.turn || "").toLowerCase();
                if (turnAttr === "assistant") return true;
                const role = (
                  node.getAttribute("data-message-author-role") ||
                  node.dataset?.messageAuthorRole ||
                  ""
                ).toLowerCase();
                if (role === "assistant") return true;
                const testId = (node.getAttribute("data-testid") || "").toLowerCase();
                if (testId.includes("assistant")) return true;
                return Boolean(
                  node.querySelector(ASSISTANT_SELECTOR) ||
                  node.querySelector('[data-testid*="assistant"]')
                );
              };
              for (let i = turns.length - 1; i >= 0; i--) {
                if (isAssistantTurn(turns[i])) {
                  lastAssistantTurn = turns[i];
                  break;
                }
              }
              if (!lastAssistantTurn) return false;
              if (lastAssistantTurn.querySelector(__FINISHED_SELECTOR__)) return true;
              const markdowns = lastAssistantTurn.querySelectorAll(".markdown");
              return Array.from(markdowns).some((n) => (n.textContent || "").trim() === "Done");
            })()"""
            .replace("__TURN_SELECTOR__", json.dumps(CONVERSATION_TURN_SELECTOR))
            .replace("__ASSISTANT_SELECTOR__", json.dumps(ASSISTANT_ROLE_SELECTOR))
            .replace("__FINISHED_SELECTOR__", json.dumps(FINISHED_ACTIONS_SELECTOR))
        ))
    except Exception:
        return False


def _stable_target(text_length: int) -> int:
    if 0 < text_length < 16:
        return 6
    if text_length < 40:
        return 3
    if text_length < 500:
        return 5
    return 6


def _observed_prompt_length(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    return max(
        len(str(value.get("editorText") or "")),
        len(str(value.get("fallbackValue") or "")),
        len(str(value.get("activeValue") or "")),
    )


def _click_dispatcher_js() -> str:
    return """
function dispatchClickSequence(target) {
  if (!target || !(target instanceof EventTarget)) return false;
  const types = ["pointerdown", "mousedown", "pointerup", "mouseup", "click"];
  for (const type of types) {
    const common = { bubbles: true, cancelable: true, view: window };
    let event;
    if (type.startsWith("pointer") && "PointerEvent" in window) {
      event = new PointerEvent(type, { ...common, pointerId: 1, pointerType: "mouse" });
    } else {
      event = new MouseEvent(type, common);
    }
    target.dispatchEvent(event);
  }
  return true;
}
"""


def _dom_ready_expression() -> str:
    return """(() => {
      const ready = document.readyState === "complete";
      const composer = document.querySelector('[data-testid*="composer"]') || document.querySelector("form");
      const fileInput = document.querySelector('input[type="file"]');
      return { ready, composer: Boolean(composer), fileInput: Boolean(fileInput) };
    })()"""


def _focus_composer_expression() -> str:
    template = r"""
(() => {
  __CLICK_DISPATCHER__
  const SELECTORS = __INPUT_SELECTORS__;
  const isVisible = (node) => {
    if (!node || typeof node.getBoundingClientRect !== "function") return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const focusNode = (node) => {
    if (!node) return false;
    dispatchClickSequence(node);
    if (typeof node.focus === "function") node.focus();
    const doc = node.ownerDocument;
    const selection = doc?.getSelection?.();
    if (selection) {
      const range = doc.createRange();
      range.selectNodeContents(node);
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    return true;
  };
  const candidates = [];
  for (const selector of SELECTORS) {
    const node = document.querySelector(selector);
    if (node) candidates.push(node);
  }
  const preferred = candidates.find((node) => isVisible(node)) || candidates[0];
  if (preferred && focusNode(preferred)) return { focused: true };
  return { focused: false };
})()
"""
    return template.replace("__CLICK_DISPATCHER__", _click_dispatcher_js()).replace(
        "__INPUT_SELECTORS__", json.dumps(INPUT_SELECTORS)
    )


def _composer_value_expression() -> str:
    template = r"""
(() => {
  const editor = document.querySelector(__PRIMARY_SELECTOR__);
  const fallback = document.querySelector(__FALLBACK_SELECTOR__);
  const inputSelectors = __INPUT_SELECTORS__;
  const readValue = (node) => {
    if (!node) return "";
    if (node instanceof HTMLTextAreaElement) return node.value ?? "";
    return node.innerText ?? "";
  };
  const isVisible = (node) => {
    if (!node || typeof node.getBoundingClientRect !== "function") return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const candidates = inputSelectors
    .map((selector) => document.querySelector(selector))
    .filter((node) => Boolean(node));
  const active = candidates.find((node) => isVisible(node)) || candidates[0] || null;
  return {
    editorText: editor?.innerText ?? "",
    fallbackValue: fallback?.value ?? "",
    activeValue: active ? readValue(active) : "",
  };
})()
"""
    return (
        template.replace("__PRIMARY_SELECTOR__", json.dumps(PROMPT_PRIMARY_SELECTOR))
        .replace("__FALLBACK_SELECTOR__", json.dumps(PROMPT_FALLBACK_SELECTOR))
        .replace("__INPUT_SELECTORS__", json.dumps(INPUT_SELECTORS))
    )


def _force_prompt_expression(prompt: str) -> str:
    template = r"""
(() => {
  const fallback = document.querySelector(__FALLBACK_SELECTOR__);
  const prompt = __PROMPT__;
  if (fallback) {
    fallback.value = prompt;
    fallback.dispatchEvent(new InputEvent("input", { bubbles: true, data: prompt, inputType: "insertFromPaste" }));
    fallback.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const editor = document.querySelector(__PRIMARY_SELECTOR__);
  if (editor) {
    editor.textContent = prompt;
    editor.dispatchEvent(new InputEvent("input", { bubbles: true, data: prompt, inputType: "insertFromPaste" }));
  }
})()
"""
    return (
        template.replace("__FALLBACK_SELECTOR__", json.dumps(PROMPT_FALLBACK_SELECTOR))
        .replace("__PRIMARY_SELECTOR__", json.dumps(PROMPT_PRIMARY_SELECTOR))
        .replace("__PROMPT__", json.dumps(prompt))
    )


def _clear_composer_expression() -> str:
    template = r"""
(() => {
  const SELECTORS = __INPUT_SELECTORS__;
  const fallback = document.querySelector(__FALLBACK_SELECTOR__);
  const editor = document.querySelector(__PRIMARY_SELECTOR__);
  const readValue = (node) => {
    if (!node) return "";
    if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) return node.value ?? "";
    return node.innerText ?? node.textContent ?? "";
  };
  const dispatchClearEvents = (node) => {
    try {
      node.dispatchEvent(
        new InputEvent("beforeinput", {
          bubbles: true,
          cancelable: true,
          data: null,
          inputType: "deleteContentBackward",
        })
      );
    } catch {}
    try {
      node.dispatchEvent(new InputEvent("input", { bubbles: true, data: "", inputType: "deleteByCut" }));
    } catch {
      node.dispatchEvent(new Event("input", { bubbles: true }));
    }
    node.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const clearEditable = (node) => {
    if (!node) return false;
    try { node.focus?.(); } catch {}
    if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
      node.value = "";
      dispatchClearEvents(node);
      return true;
    }
    if (node.isContentEditable || node.getAttribute("contenteditable") === "true") {
      try {
        const selection = node.ownerDocument?.getSelection?.();
        const range = node.ownerDocument?.createRange?.();
        if (selection && range) {
          range.selectNodeContents(node);
          selection.removeAllRanges();
          selection.addRange(range);
          node.ownerDocument?.execCommand?.("delete", false);
        }
      } catch {}
      node.textContent = "";
      dispatchClearEvents(node);
      return true;
    }
    return false;
  };
  let cleared = false;
  const nodes = SELECTORS.map((selector) => document.querySelector(selector)).filter((node) => Boolean(node));
  for (const node of Array.from(new Set([fallback, editor, ...nodes])).filter(Boolean)) {
    cleared = clearEditable(node) || cleared;
  }
  const remaining = Array.from(new Set([fallback, editor, ...nodes]))
    .filter(Boolean)
    .map((node) => readValue(node).trim())
    .filter(Boolean);
  return { cleared, remaining };
})()
"""
    return (
        template.replace("__INPUT_SELECTORS__", json.dumps(INPUT_SELECTORS))
        .replace("__FALLBACK_SELECTOR__", json.dumps(PROMPT_FALLBACK_SELECTOR))
        .replace("__PRIMARY_SELECTOR__", json.dumps(PROMPT_PRIMARY_SELECTOR))
    )


def _send_button_expression() -> str:
    template = r"""
(() => {
  __CLICK_DISPATCHER__
  const selectors = __SEND_SELECTORS__;
  const isVisible = (node) => {
    if (!(node instanceof HTMLElement)) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden";
  };
  const isEnabled = (node) => {
    const ariaDisabled = node.getAttribute("aria-disabled");
    const dataDisabled = node.getAttribute("data-disabled");
    const style = window.getComputedStyle(node);
    return !(
      node.hasAttribute("disabled") ||
      ariaDisabled === "true" ||
      dataDisabled === "true" ||
      style.pointerEvents === "none" ||
      style.display === "none"
    );
  };
  const candidates = [];
  for (const selector of selectors) {
    candidates.push(...Array.from(document.querySelectorAll(selector)));
  }
  const button = candidates.find((node) => isVisible(node) && isEnabled(node)) || null;
  if (!button) return "missing";
  dispatchClickSequence(button);
  return "clicked";
})()
"""
    return template.replace("__CLICK_DISPATCHER__", _click_dispatcher_js()).replace(
        "__SEND_SELECTORS__", json.dumps(SEND_BUTTON_SELECTORS)
    )


def _prompt_commit_expression(prompt: str, baseline: int) -> str:
    template = r"""
(() => {
  const editor = document.querySelector(__PRIMARY_SELECTOR__);
  const fallback = document.querySelector(__FALLBACK_SELECTOR__);
  const inputSelectors = __INPUT_SELECTORS__;
  const normalize = (value) => {
    let text = value?.toLowerCase?.() ?? "";
    text = text.replace(/```[^\n]*\n([\s\S]*?)```/g, " $1 ");
    text = text.replace(/```/g, " ");
    text = text.replace(/`([^`]*)`/g, "$1");
    return text.replace(/\s+/g, " ").trim();
  };
  const normalizedPrompt = normalize(__PROMPT__);
  const normalizedPromptPrefix = normalizedPrompt.slice(0, 120);
  const CONVERSATION_SELECTOR = __TURN_SELECTOR__;
  const articles = Array.from(document.querySelectorAll(CONVERSATION_SELECTOR));
  const normalizedTurns = articles.map((node) => normalize(node?.innerText));
  const readValue = (node) => {
    if (!node) return "";
    if (node instanceof HTMLTextAreaElement) return node.value ?? "";
    return node.innerText ?? "";
  };
  const isVisible = (node) => {
    if (!node || typeof node.getBoundingClientRect !== "function") return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const inputs = inputSelectors
    .map((selector) => document.querySelector(selector))
    .filter((node) => Boolean(node));
  const visibleInputs = inputs.filter((node) => isVisible(node));
  const activeInputs = visibleInputs.length > 0 ? visibleInputs : inputs;
  const userMatched = normalizedPrompt.length > 0 && normalizedTurns.some((text) => text.includes(normalizedPrompt));
  const prefixMatched =
    normalizedPromptPrefix.length > 30 &&
    normalizedTurns.some((text) => text.includes(normalizedPromptPrefix));
  const lastTurn = normalizedTurns[normalizedTurns.length - 1] ?? "";
  const lastMatched =
    normalizedPrompt.length > 0 &&
    (lastTurn.includes(normalizedPrompt) ||
      (normalizedPromptPrefix.length > 30 && lastTurn.includes(normalizedPromptPrefix)));
  const baseline = __BASELINE__;
  const hasNewTurn = baseline < 0 ? false : normalizedTurns.length > baseline;
  const stopVisible = Boolean(document.querySelector(__STOP_SELECTOR__));
  const assistantVisible = Boolean(
    document.querySelector(__ASSISTANT_SELECTOR__) ||
    document.querySelector('[data-testid*="assistant"]')
  );
  const editorValue = editor?.innerText ?? "";
  const fallbackValue = fallback?.value ?? "";
  const activeEmpty =
    activeInputs.length === 0 ? null : activeInputs.every((node) => !String(readValue(node)).trim());
  const composerCleared = activeEmpty ?? !(String(editorValue).trim() || String(fallbackValue).trim());
  const href = typeof location === "object" && location.href ? location.href : "";
  const inConversation = /\/c\//.test(href);
  return {
    baseline,
    userMatched,
    prefixMatched,
    lastMatched,
    hasNewTurn,
    stopVisible,
    assistantVisible,
    composerCleared,
    inConversation,
    href,
    fallbackValue,
    editorValue,
    lastTurn,
    turnsCount: normalizedTurns.length,
  };
})()
"""
    return (
        template.replace("__PRIMARY_SELECTOR__", json.dumps(PROMPT_PRIMARY_SELECTOR))
        .replace("__FALLBACK_SELECTOR__", json.dumps(PROMPT_FALLBACK_SELECTOR))
        .replace("__INPUT_SELECTORS__", json.dumps(INPUT_SELECTORS))
        .replace("__PROMPT__", json.dumps(prompt.strip()))
        .replace("__TURN_SELECTOR__", json.dumps(CONVERSATION_TURN_SELECTOR))
        .replace("__BASELINE__", str(int(baseline)))
        .replace("__STOP_SELECTOR__", json.dumps(STOP_BUTTON_SELECTOR))
        .replace("__ASSISTANT_SELECTOR__", json.dumps(ASSISTANT_ROLE_SELECTOR))
    )
