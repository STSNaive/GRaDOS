"""GRaDOS ChatGPT assistant response extraction helpers."""

from __future__ import annotations

import json
from typing import Any

from grados.browser.chatgpt.selectors import (
    ASSISTANT_ROLE_SELECTOR,
    CONVERSATION_TURN_SELECTOR,
    COPY_BUTTON_SELECTOR,
    FINISHED_ACTIONS_SELECTOR,
)


async def read_assistant_snapshot(page: Any, min_turn_index: int | None = None) -> dict[str, Any] | None:
    value = await page.evaluate(_assistant_snapshot_expression(min_turn_index))
    if isinstance(value, dict) and str(value.get("text") or "").strip():
        return value
    return None


async def capture_assistant_markdown(page: Any, meta: dict[str, Any] | None = None) -> str:
    value = await page.evaluate(_copy_expression(meta or {}))
    if isinstance(value, dict) and value.get("success") and isinstance(value.get("markdown"), str):
        return str(value["markdown"]).strip()
    return ""


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


def _assistant_snapshot_expression(min_turn_index: int | None = None) -> str:
    min_turn_literal = (
        str(int(min_turn_index))
        if isinstance(min_turn_index, int) and min_turn_index >= 0
        else "-1"
    )
    template = r"""
(() => {
  __CLICK_DISPATCHER__
  const MIN_TURN_INDEX = __MIN_TURN_INDEX__;
  __ASSISTANT_EXTRACTOR__
  const extracted = extractAssistantTurn();
  const isPlaceholder = (snapshot) => {
    const normalized = String(snapshot?.text ?? "").toLowerCase().trim();
    if (normalized === "chatgpt said:" || normalized === "chatgpt said") return true;
    if (
      normalized.includes("file upload request") &&
      (normalized.includes("pro thinking") || normalized.includes("chatgpt said"))
    ) {
      return true;
    }
    return (
      normalized.includes("answer now") &&
      (normalized.includes("pro thinking") || normalized.includes("chatgpt said"))
    );
  };
  if (extracted && extracted.text && !isPlaceholder(extracted)) {
    return extracted;
  }
  const fallback = __MARKDOWN_FALLBACK__;
  if (fallback && fallback.text && !isPlaceholder(fallback)) {
    return fallback;
  }
  return extracted;
})()
"""
    return (
        template.replace("__CLICK_DISPATCHER__", _click_dispatcher_js())
        .replace("__MIN_TURN_INDEX__", min_turn_literal)
        .replace("__ASSISTANT_EXTRACTOR__", _assistant_extractor_js("extractAssistantTurn"))
        .replace("__MARKDOWN_FALLBACK__", _markdown_fallback_js("MIN_TURN_INDEX"))
    )


def _assistant_extractor_js(function_name: str) -> str:
    return f"""
const {function_name} = () => {{
  const CONVERSATION_SELECTOR = {json.dumps(CONVERSATION_TURN_SELECTOR)};
  const ASSISTANT_SELECTOR = {json.dumps(ASSISTANT_ROLE_SELECTOR)};
  const isAssistantTurn = (node) => {{
    if (!(node instanceof HTMLElement)) return false;
    const turnAttr = (node.getAttribute("data-turn") || node.dataset?.turn || "").toLowerCase();
    if (turnAttr === "assistant") return true;
    const role = (node.getAttribute("data-message-author-role") || node.dataset?.messageAuthorRole || "").toLowerCase();
    if (role === "assistant") return true;
    const testId = (node.getAttribute("data-testid") || "").toLowerCase();
    if (testId.includes("assistant")) return true;
    return Boolean(node.querySelector(ASSISTANT_SELECTOR) || node.querySelector('[data-testid*="assistant"]'));
  }};
  const expandCollapsibles = (root) => {{
    const buttons = Array.from(root.querySelectorAll("button"));
    for (const button of buttons) {{
      const label = (button.textContent || "").toLowerCase();
      const testid = (button.getAttribute("data-testid") || "").toLowerCase();
      if (
        label.includes("more") ||
        label.includes("expand") ||
        label.includes("show") ||
        testid.includes("markdown") ||
        testid.includes("toggle")
      ) {{
        dispatchClickSequence(button);
      }}
    }}
  }};
  const turns = Array.from(document.querySelectorAll(CONVERSATION_SELECTOR));
  for (let index = turns.length - 1; index >= 0; index -= 1) {{
    if (MIN_TURN_INDEX >= 0 && index < MIN_TURN_INDEX) continue;
    const turn = turns[index];
    if (!isAssistantTurn(turn)) continue;
    const messageRoot = turn.querySelector(ASSISTANT_SELECTOR) ?? turn;
    expandCollapsibles(messageRoot);
    const preferred =
      (messageRoot.matches?.(".markdown") || messageRoot.matches?.("[data-message-content]") ? messageRoot : null) ||
      messageRoot.querySelector(".markdown") ||
      messageRoot.querySelector("[data-message-content]") ||
      messageRoot.querySelector('[data-testid*="message"]') ||
      messageRoot.querySelector('[data-testid*="assistant"]') ||
      messageRoot.querySelector(".prose") ||
      messageRoot.querySelector('[class*="markdown"]');
    const contentRoot = preferred ?? messageRoot;
    if (!contentRoot) continue;
    const innerText = contentRoot?.innerText ?? "";
    const textContent = contentRoot?.textContent ?? "";
    const text = innerText.trim().length > 0 ? innerText : textContent;
    const html = contentRoot?.innerHTML ?? "";
    const messageId = messageRoot.getAttribute("data-message-id");
    const turnId = messageRoot.getAttribute("data-testid");
    if (text.trim()) {{
      return {{ text, html, messageId, turnId, turnIndex: index }};
    }}
  }}
  return null;
}};
"""


def _markdown_fallback_js(min_turn_name: str) -> str:
    return f"""(() => {{
  const __minTurn = {min_turn_name} >= 0 ? {min_turn_name} : null;
  const roots = [
    document.querySelector('section[data-testid="screen-threadFlyOut"]'),
    document.querySelector('[data-testid="chat-thread"]'),
    document.querySelector("main"),
    document.querySelector('[role="main"]'),
  ].filter(Boolean);
  if (roots.length === 0) return null;
  const markdownSelector = '.markdown,[data-message-content],[data-testid*="message"],.prose,[class*="markdown"]';
  const excludedSelector =
    'nav, aside, [data-testid*="sidebar"], [data-testid*="chat-history"], ' +
    '[data-testid*="composer"], form';
  const isExcluded = (node) => Boolean(node?.closest?.(excludedSelector));
  const scoreRoot = (node) => {{
    const actions = node.querySelectorAll({json.dumps(FINISHED_ACTIONS_SELECTOR)}).length;
    const assistants = node.querySelectorAll('[data-message-author-role="assistant"], [data-turn="assistant"]').length;
    const markdowns = node.querySelectorAll(markdownSelector).length;
    return actions * 10 + assistants * 5 + markdowns;
  }};
  let root = roots[0];
  let bestScore = scoreRoot(root);
  for (let i = 1; i < roots.length; i += 1) {{
    const candidate = roots[i];
    const score = scoreRoot(candidate);
    if (score > bestScore) {{
      bestScore = score;
      root = candidate;
    }}
  }}
  const CONVERSATION_SELECTOR = {json.dumps(CONVERSATION_TURN_SELECTOR)};
  const turnNodes = Array.from(document.querySelectorAll(CONVERSATION_SELECTOR));
  const hasTurns = turnNodes.length > 0;
  const resolveTurnIndex = (node) => {{
    const turn = node?.closest?.(CONVERSATION_SELECTOR);
    if (!turn) return null;
    const idx = turnNodes.indexOf(turn);
    return idx >= 0 ? idx : null;
  }};
  const isAfterMinTurn = (node) => {{
    if (__minTurn === null) return true;
    if (!hasTurns) return true;
    const idx = resolveTurnIndex(node);
    return idx !== null && idx >= __minTurn;
  }};
  const normalize = (value) => String(value || "").toLowerCase().replace(/\\s+/g, " ").trim();
  const collectUserText = (scope) => {{
    if (!scope?.querySelectorAll) return "";
    const userTurns = Array.from(scope.querySelectorAll('[data-message-author-role="user"], [data-turn="user"]'));
    const lastUser = userTurns[userTurns.length - 1];
    return lastUser ? normalize(lastUser.innerText || lastUser.textContent || "") : "";
  }};
  const userText = collectUserText(root) || collectUserText(document);
  const isUserEcho = (text) => {{
    if (!userText) return false;
    const normalized = normalize(text);
    return Boolean(normalized) && (normalized === userText || normalized.startsWith(userText));
  }};
  const markdowns = Array.from(root.querySelectorAll(markdownSelector))
    .filter((node) => !isExcluded(node))
    .filter((node) => {{
      const container = node.closest("[data-message-author-role], [data-turn]");
      if (!container) return true;
      const role = (
        container.getAttribute("data-message-author-role") ||
        container.getAttribute("data-turn") ||
        ""
      ).toLowerCase();
      return role !== "user";
    }});
  if (markdowns.length === 0) return null;
  const assistantMarkdowns = markdowns.filter((node) => {{
    const container = node.closest('[data-message-author-role], [data-turn], [data-testid*="assistant"]');
    if (!container) return false;
    const role = (
      container.getAttribute("data-message-author-role") ||
      container.getAttribute("data-turn") ||
      ""
    ).toLowerCase();
    if (role === "assistant") return true;
    const testId = (container.getAttribute("data-testid") || "").toLowerCase();
    return testId.includes("assistant");
  }});
  const candidates = assistantMarkdowns.length > 0 ? assistantMarkdowns : markdowns;
  for (let i = candidates.length - 1; i >= 0; i -= 1) {{
    const node = candidates[i];
    if (!node || !isAfterMinTurn(node)) continue;
    const text = (node.innerText || node.textContent || "").trim();
    if (!text || isUserEcho(text)) continue;
    const html = node.innerHTML ?? "";
    const turnIndex = resolveTurnIndex(node);
    return {{ text, html, messageId: null, turnId: null, turnIndex }};
  }}
  return null;
}})()"""


def _copy_expression(meta: dict[str, Any]) -> str:
    template = r"""
(() => {
  __CLICK_DISPATCHER__
  const BUTTON_SELECTOR = __COPY_BUTTON_SELECTOR__;
  const CONVERSATION_SELECTOR = __CONVERSATION_SELECTOR__;
  const ASSISTANT_SELECTOR = __ASSISTANT_SELECTOR__;
  const HINT = __HINT__;
  const TIMEOUT_MS = 10000;

  const isAssistantTurn = (node) => {
    if (!(node instanceof HTMLElement)) return false;
    const turnAttr = (node.getAttribute("data-turn") || node.dataset?.turn || "").toLowerCase();
    if (turnAttr === "assistant") return true;
    const role = (node.getAttribute("data-message-author-role") || node.dataset?.messageAuthorRole || "").toLowerCase();
    if (role === "assistant") return true;
    const testId = (node.getAttribute("data-testid") || "").toLowerCase();
    if (testId.includes("assistant")) return true;
    return Boolean(node.querySelector(ASSISTANT_SELECTOR) || node.querySelector('[data-testid*="assistant"]'));
  };
  const locateButton = () => {
    if (HINT?.messageId) {
      const node = document.querySelector('[data-message-id="' + HINT.messageId + '"]');
      const button = node ? Array.from(node.querySelectorAll(BUTTON_SELECTOR)).at(-1) : null;
      if (button) return button;
    }
    if (HINT?.turnId) {
      const node = document.querySelector('[data-testid="' + HINT.turnId + '"]');
      const button = node ? Array.from(node.querySelectorAll(BUTTON_SELECTOR)).at(-1) : null;
      if (button) return button;
    }
    const turns = Array.from(document.querySelectorAll(CONVERSATION_SELECTOR));
    for (let i = turns.length - 1; i >= 0; i -= 1) {
      const turn = turns[i];
      if (!isAssistantTurn(turn)) continue;
      const button = turn.querySelector(BUTTON_SELECTOR);
      if (button) return button;
    }
    const all = Array.from(document.querySelectorAll(BUTTON_SELECTOR));
    for (let i = all.length - 1; i >= 0; i -= 1) {
      const button = all[i];
      const turn = button?.closest?.(CONVERSATION_SELECTOR);
      if (turn && isAssistantTurn(turn)) return button;
    }
    return null;
  };
  const interceptClipboard = () => {
    const clipboard = navigator.clipboard;
    const state = { text: "", updatedAt: 0 };
    if (!clipboard) return { state, restore: () => {} };
    const originalWriteText = clipboard.writeText;
    const originalWrite = clipboard.write;
    clipboard.writeText = (value) => {
      state.text = typeof value === "string" ? value : "";
      state.updatedAt = Date.now();
      return Promise.resolve();
    };
    clipboard.write = async (items) => {
      try {
        const list = Array.isArray(items) ? items : items ? [items] : [];
        for (const item of list) {
          if (!item) continue;
          const types = Array.isArray(item.types) ? item.types : [];
          if (types.includes("text/plain") && typeof item.getType === "function") {
            const blob = await item.getType("text/plain");
            const text = await blob.text();
            state.text = text ?? "";
            state.updatedAt = Date.now();
            break;
          }
        }
      } catch {
        state.text = "";
        state.updatedAt = Date.now();
      }
      return Promise.resolve();
    };
    return {
      state,
      restore: () => {
        clipboard.writeText = originalWriteText;
        clipboard.write = originalWrite;
      },
    };
  };
  return new Promise((resolve) => {
    const deadline = Date.now() + TIMEOUT_MS;
    const waitForButton = () => {
      const button = locateButton();
      if (button) {
        const interception = interceptClipboard();
        let settled = false;
        let pollId = null;
        let timeoutId = null;
        const finish = (payload) => {
          if (settled) return;
          settled = true;
          if (pollId) clearInterval(pollId);
          if (timeoutId) clearTimeout(timeoutId);
          button.removeEventListener("copy", handleCopy, true);
          interception.restore?.();
          resolve(payload);
        };
        const readIntercepted = () => {
          const markdown = interception.state.text ?? "";
          const updatedAt = interception.state.updatedAt ?? 0;
          return { success: Boolean(markdown.trim()), markdown, updatedAt };
        };
        let lastText = "";
        let stableTicks = 0;
        const maybeFinish = () => {
          const payload = readIntercepted();
          if (!payload.success) return;
          if (payload.markdown !== lastText) {
            lastText = payload.markdown;
            stableTicks = 0;
            return;
          }
          stableTicks += 1;
          const ageMs = Date.now() - (payload.updatedAt || 0);
          if (stableTicks >= 3 && ageMs >= 250) {
            finish(payload);
          }
        };
        const handleCopy = () => maybeFinish();
        button.addEventListener("copy", handleCopy, true);
        button.scrollIntoView({ block: "center", behavior: "instant" });
        dispatchClickSequence(button);
        pollId = setInterval(maybeFinish, 120);
        timeoutId = setTimeout(() => {
          button.removeEventListener("copy", handleCopy, true);
          finish({ success: false, status: "timeout" });
        }, TIMEOUT_MS);
        return;
      }
      if (Date.now() > deadline) {
        resolve({ success: false, status: "missing-button" });
        return;
      }
      setTimeout(waitForButton, 120);
    };
    waitForButton();
  });
})()
"""
    return (
        template.replace("__CLICK_DISPATCHER__", _click_dispatcher_js())
        .replace("__COPY_BUTTON_SELECTOR__", json.dumps(COPY_BUTTON_SELECTOR))
        .replace("__CONVERSATION_SELECTOR__", json.dumps(CONVERSATION_TURN_SELECTOR))
        .replace("__ASSISTANT_SELECTOR__", json.dumps(ASSISTANT_ROLE_SELECTOR))
        .replace("__HINT__", json.dumps(meta))
    )
