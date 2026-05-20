"""ChatGPT model picker helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from grados.browser.chatgpt.errors import ChatGPTBrowserError
from grados.browser.chatgpt.protocol import (
    ORACLE_CHATGPT_PRO_MODEL,
    ORACLE_CURRENT_PRO_TEST_ID_TOKENS,
    ORACLE_CURRENT_PRO_TEXT_TOKENS,
    ORACLE_LEGACY_PRO_TOKENS,
    ORACLE_MODEL_SELECTION_STRATEGY,
    ORACLE_PRO_LABEL_TOKENS,
    ORACLE_PRO_TEST_ID_TOKENS,
    ORACLE_PRO_VISIBLE_ALIASES,
)
from grados.browser.chatgpt.selectors import (
    COMPOSER_MODEL_SIGNAL_SELECTOR,
    MENU_CONTAINER_SELECTOR,
    MENU_ITEM_SELECTOR,
    MODEL_BUTTON_SELECTOR,
)
from grados.browser.chatgpt.types import ChatGPTModelSelection


def normalize_model_label(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", label.lower())).strip()


def is_legacy_pro_label(label: str) -> bool:
    normalized = normalize_model_label(label)
    compact = normalized.replace(" ", "")
    return any(token in normalized or token in compact for token in ORACLE_LEGACY_PRO_TOKENS)


def rank_model_label(label: str) -> tuple[int, int, str]:
    """Rank labels so Oracle's current Pro alias wins over legacy Pro labels."""
    normalized = normalize_model_label(label)
    words = normalized.split()
    if "pro" not in words and not normalized.endswith(" pro"):
        return (0, 0, normalized)
    if "thinking" in words:
        return (0, 0, normalized)
    if is_legacy_pro_label(label):
        return (1, 0, normalized)
    if normalized in ORACLE_PRO_VISIBLE_ALIASES:
        return (3, 1, normalized)
    if words.count("5") >= 2:
        return (3, 1, normalized)
    if "pro" in words:
        return (3, 0, normalized)
    return (2, 0, normalized)


def select_latest_pro_label(labels: list[str]) -> str:
    candidates = [(rank_model_label(label), label.strip()) for label in labels if label.strip()]
    candidates = [item for item in candidates if item[0][0] > 0]
    if not candidates:
        raise ChatGPTBrowserError(
            code="model_unavailable",
            stage="model-selection",
            message="No visible ChatGPT Pro model option was found.",
            details={"available_labels": labels},
        )
    candidates.sort(reverse=True)
    best_rank, best_label = candidates[0]
    if best_rank[0] < 3:
        raise ChatGPTBrowserError(
            code="model_unavailable",
            stage="model-selection",
            message="Only legacy or ambiguous Pro model options were visible.",
            details={"available_labels": labels, "best_label": best_label},
        )
    return best_label


async def ensure_latest_pro_model(page: Any) -> ChatGPTModelSelection:
    """Select Oracle's current ChatGPT Pro target and verify the selected UI label."""
    result = await page.evaluate(_oracle_model_selection_expression())
    if not isinstance(result, dict):
        raise ChatGPTBrowserError(
            code="model_picker_unavailable",
            stage="model-selection",
            message="Unable to read ChatGPT model picker result.",
        )

    status = str(result.get("status") or "")
    available_labels = _available_labels_from_result(result)
    if status not in {"already-selected", "switched", "switched-best-effort"}:
        code = "model_picker_unavailable" if status == "button-missing" else "model_unavailable"
        raise ChatGPTBrowserError(
            code=code,
            stage="model-selection",
            message=f'Unable to select Oracle ChatGPT Pro target "{ORACLE_CHATGPT_PRO_MODEL}".',
            details={
                "status": status,
                "available_labels": available_labels,
                "hint": result.get("hint"),
            },
        )

    resolved_label = str(result.get("label") or "").strip() or ORACLE_CHATGPT_PRO_MODEL
    if rank_model_label(resolved_label)[0] < 3:
        raise ChatGPTBrowserError(
            code="model_unconfirmed",
            stage="model-selection",
            message="ChatGPT model picker did not confirm the selected Pro model.",
            details={
                "requested": ORACLE_CHATGPT_PRO_MODEL,
                "resolved_label": resolved_label,
                "available_labels": available_labels,
                "status": status,
            },
        )

    return ChatGPTModelSelection(
        requested=ORACLE_CHATGPT_PRO_MODEL,
        resolved_label=resolved_label,
        available_labels=available_labels,
        strategy=ORACLE_MODEL_SELECTION_STRATEGY,
        verified=True,
    )


def _available_labels_from_result(result: dict[str, Any]) -> list[str]:
    hint = result.get("hint")
    raw = hint.get("availableOptions") if isinstance(hint, dict) else result.get("availableOptions")
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        label = str(item).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _oracle_model_selection_expression() -> str:
    replacements = {
        "__MODEL_BUTTON_SELECTOR__": json.dumps(MODEL_BUTTON_SELECTOR),
        "__COMPOSER_MODEL_SIGNAL_SELECTOR__": json.dumps(COMPOSER_MODEL_SIGNAL_SELECTOR),
        "__MENU_CONTAINER_SELECTOR__": json.dumps(MENU_CONTAINER_SELECTOR),
        "__MENU_ITEM_SELECTOR__": json.dumps(MENU_ITEM_SELECTOR),
        "__PRIMARY_LABEL__": json.dumps(ORACLE_CHATGPT_PRO_MODEL),
        "__LABEL_TOKENS__": json.dumps(ORACLE_PRO_LABEL_TOKENS),
        "__TEST_ID_TOKENS__": json.dumps(ORACLE_PRO_TEST_ID_TOKENS),
        "__LEGACY_PRO_TOKENS__": json.dumps(ORACLE_LEGACY_PRO_TOKENS),
        "__CURRENT_PRO_TEXT_TOKENS__": json.dumps(ORACLE_CURRENT_PRO_TEXT_TOKENS),
        "__CURRENT_PRO_TEST_ID_TOKENS__": json.dumps(ORACLE_CURRENT_PRO_TEST_ID_TOKENS),
    }
    expression = r"""
(async () => {
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

  const BUTTON_SELECTOR = __MODEL_BUTTON_SELECTOR__;
  const COMPOSER_MODEL_SIGNAL_SELECTOR = __COMPOSER_MODEL_SIGNAL_SELECTOR__;
  const MENU_CONTAINER_SELECTOR = __MENU_CONTAINER_SELECTOR__;
  const MENU_ITEM_SELECTOR = __MENU_ITEM_SELECTOR__;
  const PRIMARY_LABEL = __PRIMARY_LABEL__;
  const LABEL_TOKENS = __LABEL_TOKENS__;
  const TEST_IDS = __TEST_ID_TOKENS__;
  const LEGACY_PRO_VERSION_TOKENS = __LEGACY_PRO_TOKENS__;
  const CURRENT_PRO_TEXT_TOKENS = __CURRENT_PRO_TEXT_TOKENS__;
  const CURRENT_PRO_TEST_ID_TOKENS = __CURRENT_PRO_TEST_ID_TOKENS__;
  const INITIAL_WAIT_MS = 150;
  const REOPEN_INTERVAL_MS = 400;
  const MAX_WAIT_MS = 20000;
  const SETTLE_WAIT_MS = 1500;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const normalizeText = (value) => (value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const hasToken = (value, token) => normalizeText(value).split(" ").includes(token);
  const normalizedTarget = normalizeText(PRIMARY_LABEL);
  const normalizedTokens = Array.from(new Set([normalizedTarget, ...LABEL_TOKENS]))
    .map((token) => normalizeText(token))
    .filter(Boolean);
  const labelHasProWord = (label) =>
    label === "pro" || label.startsWith("pro ") || label.includes(" pro ") || label.endsWith(" pro");
  const labelHasLegacyProVersion = (value) => {
    const label = normalizeText(value);
    return LEGACY_PRO_VERSION_TOKENS.some((token) => label.includes(token));
  };
  const isTargetGpt55VisibleAlias = (value) => {
    const label = normalizeText(value);
    return (label === "pro" || label === "pro extended" || label === "extended pro") && !label.includes("thinking");
  };
  const hasProComposerPill = () => Boolean(
    Array.from(document.querySelectorAll("button.__composer-pill, button[aria-label]"))
      .filter((node) => {
        const label = normalizeText(node.getAttribute?.("aria-label") ?? "");
        return node.matches?.("button.__composer-pill") || label.includes("click to remove");
      })
      .some((node) => {
        const label = normalizeText(
          (node.getAttribute?.("aria-label") ?? "") + " " + (node.textContent ?? "")
        );
        return hasToken(label, "pro") && !hasToken(label, "thinking");
      })
  );

  const button = document.querySelector(BUTTON_SELECTOR);
  if (!button) {
    return { status: "button-missing" };
  }

  const closeMenu = () => {
    try {
      if (dispatchClickSequence(button)) return;
    } catch {}
    try {
      document.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape",
        code: "Escape",
        keyCode: 27,
        which: 27,
        bubbles: true,
      }));
    } catch {}
  };
  const getButtonLabel = () => (button.textContent ?? "").trim();
  const getComposerModelLabel = () =>
    (document.querySelector(COMPOSER_MODEL_SIGNAL_SELECTOR)?.textContent ?? "").trim();
  const readComposerModelSignal = () => normalizeText(getComposerModelLabel());
  const withProPillSignal = (label) => {
    const resolved = label || "";
    if (!hasProComposerPill()) return resolved;
    const normalized = normalizeText(resolved);
    if (!normalized) return resolved;
    if (normalized.includes("thinking")) return "Pro";
    if (normalized.includes("pro")) return resolved;
    return resolved + " + Pro";
  };
  const getResolvedLabel = (fallback) =>
    withProPillSignal(getComposerModelLabel() || getButtonLabel() || fallback);

  const buttonMatchesTarget = () => {
    const normalizedLabel = normalizeText(getButtonLabel());
    if (!normalizedLabel) return false;
    if (isTargetGpt55VisibleAlias(normalizedLabel)) return true;
    if (
      hasProComposerPill() &&
      (normalizedLabel === "chatgpt" ||
        normalizedLabel === "extended" ||
        normalizedLabel === "standard" ||
        normalizedLabel === "heavy" ||
        normalizedLabel === "light")
    ) {
      return true;
    }
    if (labelHasLegacyProVersion(normalizedLabel)) return false;
    return labelHasProWord(normalizedLabel) && !normalizedLabel.includes("thinking");
  };
  const buttonHasGenericLabel = () => {
    const normalizedLabel = normalizeText(getButtonLabel());
    return !normalizedLabel || normalizedLabel === "chatgpt";
  };
  const composerSignalMatchesTarget = () => {
    const signal = readComposerModelSignal();
    if (!signal) return false;
    if (labelHasLegacyProVersion(signal)) return false;
    return signal.includes("pro") && !signal.includes("thinking");
  };
  const activeSelectionMatchesTarget = () => {
    if (buttonMatchesTarget()) return true;
    if (!buttonHasGenericLabel()) return false;
    return composerSignalMatchesTarget();
  };
  const selectionStateChanged = (previousButtonLabel, previousComposerSignal) => {
    const currentButtonLabel = normalizeText(getButtonLabel());
    const currentComposerSignal = readComposerModelSignal();
    if (currentButtonLabel && currentButtonLabel !== previousButtonLabel && !buttonHasGenericLabel()) {
      return true;
    }
    return currentComposerSignal !== previousComposerSignal;
  };

  if (activeSelectionMatchesTarget()) {
    return { status: "already-selected", label: getResolvedLabel(PRIMARY_LABEL) };
  }

  let lastPointerClick = 0;
  const pointerClick = () => {
    if (dispatchClickSequence(button)) {
      lastPointerClick = performance.now();
    }
  };
  const getOptionLabel = (node) => node?.textContent?.trim() ?? "";
  const isThinkingEffortControl = (node) =>
    node instanceof HTMLElement &&
    (node.getAttribute("data-model-picker-thinking-effort-action") === "true" ||
      Boolean(node.closest('[data-model-picker-thinking-effort-action="true"]')));
  const scoreOption = (normalizedText, testid) => {
    if (!normalizedText && !testid) return 0;
    const normalizedTestId = (testid ?? "").toLowerCase();
    const candidateGpt55VisibleAlias = isTargetGpt55VisibleAlias(normalizedText);
    const candidateHasThinking =
      normalizedText.includes("thinking") || normalizedTestId.includes("thinking");
    const candidateHasLegacyProVersion = labelHasLegacyProVersion(normalizedText);
    const candidateHasPro =
      candidateGpt55VisibleAlias ||
      labelHasProWord(normalizedText) ||
      normalizedText.includes("proresearch") ||
      normalizedTestId.includes("pro");
    if (candidateHasThinking) return 0;
    if (candidateHasLegacyProVersion) return 0;
    if (!candidateHasPro) return 0;

    let score = 0;
    const candidateHasVersion =
      CURRENT_PRO_TEXT_TOKENS.some((token) => normalizedText.includes(token)) ||
      CURRENT_PRO_TEST_ID_TOKENS.some((token) => normalizedTestId.includes(token));
    const versionLikeLabel = /(?:^|\s)5\s+[0-9](?:\s|$)/.test(normalizedText) || normalizedText.includes("gpt");
    if (versionLikeLabel && !candidateHasVersion && !candidateGpt55VisibleAlias) return 0;
    if (candidateGpt55VisibleAlias) score += 900;
    for (const id of TEST_IDS) {
      if (id && normalizedTestId === id) score += 1500;
      else if (id && normalizedTestId.includes(id)) score += 200 + Math.min(900, id.length * 25);
    }
    if (normalizedText === normalizedTarget) score += 500;
    else if (normalizedText.startsWith(normalizedTarget)) score += 420;
    else if (normalizedText.includes(normalizedTarget)) score += 380;
    for (const token of normalizedTokens) {
      if (token && normalizedText.includes(token)) {
        score += Math.min(120, Math.max(10, token.length * 4));
      }
    }
    if (!labelHasProWord(normalizedText)) score -= 80;
    return Math.max(score, 0);
  };
  const findBestOption = () => {
    let bestMatch = null;
    const menus = Array.from(document.querySelectorAll(MENU_CONTAINER_SELECTOR));
    for (const menu of menus) {
      const buttons = Array.from(menu.querySelectorAll(MENU_ITEM_SELECTOR));
      for (const option of buttons) {
        if (isThinkingEffortControl(option)) continue;
        const text = option.textContent ?? "";
        const normalizedText = normalizeText(text);
        const testid = option.getAttribute("data-testid") ?? "";
        const score = scoreOption(normalizedText, testid);
        if (score <= 0) continue;
        const label = getOptionLabel(option);
        if (!bestMatch || score > bestMatch.score) {
          bestMatch = { node: option, label, score, testid, normalizedText };
        }
      }
    }
    return bestMatch;
  };
  const collectAvailableOptions = () => {
    const menuRoots = Array.from(document.querySelectorAll(MENU_CONTAINER_SELECTOR));
    const nodes = menuRoots.length > 0
      ? menuRoots.flatMap((root) => Array.from(root.querySelectorAll(MENU_ITEM_SELECTOR)))
      : Array.from(document.querySelectorAll(MENU_ITEM_SELECTOR));
    const labels = nodes
      .map((node) => (node?.textContent ?? "").trim())
      .filter(Boolean)
      .filter((label, index, arr) => arr.indexOf(label) === index);
    return labels.slice(0, 12);
  };
  const waitForTargetSelection = (previousButtonLabel, previousComposerSignal) => new Promise((resolve) => {
    const waitStart = performance.now();
    const check = () => {
      if (activeSelectionMatchesTarget()) {
        resolve("target");
        return;
      }
      if (selectionStateChanged(previousButtonLabel, previousComposerSignal)) {
        resolve("changed");
        return;
      }
      if (performance.now() - waitStart > SETTLE_WAIT_MS) {
        resolve("timeout");
        return;
      }
      setTimeout(check, 100);
    };
    check();
  });
  const detectTemporaryChat = () => {
    try {
      const url = new URL(window.location.href);
      const flag = (url.searchParams.get("temporary-chat") ?? "").toLowerCase();
      if (flag === "true" || flag === "1" || flag === "yes") return true;
    } catch {}
    const title = (document.title || "").toLowerCase();
    if (title.includes("temporary chat")) return true;
    const body = (document.body?.innerText || "").toLowerCase();
    return body.includes("temporary chat");
  };
  const ensureMenuOpen = () => {
    const menuOpen = document.querySelector(MENU_CONTAINER_SELECTOR);
    if (!menuOpen && performance.now() - lastPointerClick > REOPEN_INTERVAL_MS) {
      pointerClick();
    }
  };

  pointerClick();
  await sleep(INITIAL_WAIT_MS);
  const start = performance.now();
  while (performance.now() - start <= MAX_WAIT_MS) {
    ensureMenuOpen();
    const match = findBestOption();
    if (match) {
      if (activeSelectionMatchesTarget()) {
        closeMenu();
        return {
          status: "already-selected",
          label: getResolvedLabel(match.label),
          availableOptions: collectAvailableOptions(),
        };
      }
      const previousButtonLabel = normalizeText(getButtonLabel());
      const previousComposerSignal = readComposerModelSignal();
      dispatchClickSequence(match.node);
      if ((match.testid ?? "").toLowerCase().includes("submenu")) {
        await sleep(REOPEN_INTERVAL_MS / 2);
        continue;
      }
      const selectionSettled = await waitForTargetSelection(previousButtonLabel, previousComposerSignal);
      if (selectionSettled === "target") {
        closeMenu();
        return {
          status: "switched",
          label: getResolvedLabel(match.label),
          availableOptions: collectAvailableOptions(),
        };
      }
    }
    await sleep(REOPEN_INTERVAL_MS / 2);
  }
  return {
    status: "option-not-found",
    hint: { temporaryChat: detectTemporaryChat(), availableOptions: collectAvailableOptions() },
  };
})()
"""
    for key, value in replacements.items():
        expression = expression.replace(key, value)
    return expression
