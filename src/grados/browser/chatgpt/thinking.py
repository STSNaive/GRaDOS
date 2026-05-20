"""ChatGPT thinking-option helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from grados.browser.chatgpt.errors import ChatGPTBrowserError
from grados.browser.chatgpt.protocol import (
    ORACLE_PRO_THINKING_ALIAS,
    ORACLE_PRO_THINKING_LEVEL,
    ORACLE_THINKING_LEVEL_TOKENS,
)
from grados.browser.chatgpt.selectors import (
    MENU_CONTAINER_SELECTOR,
    MENU_ITEM_SELECTOR,
    MODEL_BUTTON_SELECTOR,
)
from grados.browser.chatgpt.types import ChatGPTThinkingSelection

_THINKING_RANKS: list[tuple[int, tuple[str, ...]]] = [
    (60, ORACLE_THINKING_LEVEL_TOKENS["heavy"]),
    (50, (*ORACLE_THINKING_LEVEL_TOKENS["extended"], "deep")),
    (40, ("high", "高")),
    (30, ORACLE_THINKING_LEVEL_TOKENS["standard"]),
    (20, ("medium", "中")),
    (10, (*ORACLE_THINKING_LEVEL_TOKENS["light"], "low", "低")),
]


def normalize_thinking_label(label: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", label.lower()),
    ).strip()


def rank_thinking_label(label: str) -> int:
    normalized = normalize_thinking_label(label)
    for rank, tokens in _THINKING_RANKS:
        if any(token in normalized for token in tokens):
            return rank
    return 0


async def ensure_oracle_pro_extended_thinking(page: Any) -> ChatGPTThinkingSelection:
    """Select Oracle's strict Pro Extended thinking effort."""
    result = await page.evaluate(_oracle_thinking_expression())
    if not isinstance(result, dict):
        raise ChatGPTBrowserError(
            code="thinking_unconfirmed",
            stage="thinking-selection",
            message="Unable to read ChatGPT thinking selection result.",
        )

    status = str(result.get("status") or "")
    available_labels = _available_labels_from_result(result)
    if status not in {"already-selected", "switched"}:
        raise ChatGPTBrowserError(
            code="thinking_unconfirmed",
            stage="thinking-selection",
            message="Unable to confirm Oracle Pro Extended thinking before submitting.",
            details={
                "status": status,
                "requested": ORACLE_PRO_THINKING_LEVEL,
                "available_labels": available_labels,
                "model_kind": result.get("modelKind"),
            },
        )

    resolved_label = str(result.get("label") or "").strip() or ORACLE_PRO_THINKING_LEVEL.title()
    resolved_rank = rank_thinking_label(resolved_label) or rank_thinking_label(ORACLE_PRO_THINKING_LEVEL)
    if resolved_rank <= 0:
        raise ChatGPTBrowserError(
            code="thinking_unconfirmed",
            stage="thinking-selection",
            message="ChatGPT thinking selector did not confirm the selected option.",
            details={
                "resolved_label": resolved_label,
                "available_labels": available_labels,
            },
        )
    return ChatGPTThinkingSelection(
        requested=ORACLE_PRO_THINKING_ALIAS,
        resolved_label=resolved_label,
        available_labels=available_labels,
        rank=resolved_rank,
        verified=True,
    )


def _available_labels_from_result(result: dict[str, Any]) -> list[str]:
    raw = result.get("availableOptions")
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        label = str(item).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _oracle_thinking_expression() -> str:
    replacements = {
        "__MENU_CONTAINER_SELECTOR__": json.dumps(MENU_CONTAINER_SELECTOR),
        "__MENU_ITEM_SELECTOR__": json.dumps(MENU_ITEM_SELECTOR),
        "__MODEL_BUTTON_SELECTOR__": json.dumps(MODEL_BUTTON_SELECTOR),
        "__TARGET_LEVEL__": json.dumps(ORACLE_PRO_THINKING_LEVEL),
        "__TARGET_MODEL_KIND__": json.dumps("pro"),
        "__LEVEL_TOKENS__": json.dumps(ORACLE_THINKING_LEVEL_TOKENS),
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

  const MENU_CONTAINER_SELECTOR = __MENU_CONTAINER_SELECTOR__;
  const MENU_ITEM_SELECTOR = __MENU_ITEM_SELECTOR__;
  const MODEL_BUTTON_SELECTOR = __MODEL_BUTTON_SELECTOR__;
  const TARGET_LEVEL = __TARGET_LEVEL__;
  const TARGET_MODEL_KIND = __TARGET_MODEL_KIND__;
  const LEVEL_TOKENS = __LEVEL_TOKENS__;
  const targetTokens = LEVEL_TOKENS[TARGET_LEVEL] || [TARGET_LEVEL];
  const INITIAL_WAIT_MS = 150;
  const STEP_WAIT_MS = 200;
  const MAX_WAIT_MS = 8000;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const normalize = (value) => (value || "")
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const matchesLevel = (text) => {
    const t = normalize(text);
    return targetTokens.some((tok) => t.includes(String(tok).toLowerCase()));
  };
  const hasToken = (text, token) => normalize(text).split(" ").includes(token);
  const optionIsSelected = (node) => {
    if (!(node instanceof HTMLElement)) return false;
    const ariaChecked = node.getAttribute("aria-checked");
    const dataState = (node.getAttribute("data-state") || "").toLowerCase();
    if (ariaChecked === "true") return true;
    return dataState === "checked" || dataState === "selected" || dataState === "on";
  };
  const closeOpenMenus = () => {
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
  const findOptionInMenu = (menu) => {
    for (const item of menu.querySelectorAll(MENU_ITEM_SELECTOR)) {
      if (matchesLevel(item.textContent ?? "") || matchesLevel(item.getAttribute?.("aria-label") ?? "")) {
        return item;
      }
    }
    return null;
  };
  const collectAvailableOptions = () => {
    const menuRoots = Array.from(document.querySelectorAll(MENU_CONTAINER_SELECTOR + ", [role=\"group\"]"));
    const nodes = menuRoots.length > 0
      ? menuRoots.flatMap((root) => Array.from(root.querySelectorAll(MENU_ITEM_SELECTOR)))
      : Array.from(document.querySelectorAll(MENU_ITEM_SELECTOR));
    const labels = nodes
      .map((node) => (node?.textContent ?? node?.getAttribute?.("aria-label") ?? "").trim())
      .filter(Boolean)
      .filter((label, index, arr) => arr.indexOf(label) === index);
    return labels.slice(0, 20);
  };

  const OLD_CHIP_SELECTORS = [
    '[data-testid="composer-footer-actions"] button[aria-haspopup="menu"]',
    '.__composer-pill-composite button[aria-haspopup="menu"]',
  ];
  const findOldChip = () => {
    for (const selector of OLD_CHIP_SELECTORS) {
      for (const btn of document.querySelectorAll(selector)) {
        if (btn.getAttribute?.("aria-haspopup") !== "menu") continue;
        if (btn.matches?.(MODEL_BUTTON_SELECTOR)) continue;
        const aria = normalize(btn.getAttribute?.("aria-label") ?? "");
        const text = normalize(btn.textContent ?? "");
        if (aria.includes("thinking") || text.includes("thinking")) return btn;
      }
    }
    return null;
  };
  const findOldEffortMenu = () => {
    const menus = document.querySelectorAll(MENU_CONTAINER_SELECTOR + ", [role=\"group\"]");
    for (const menu of menus) {
      const label = menu.querySelector?.(".__menu-label, [class*=\"menu-label\"]");
      if (normalize(label?.textContent ?? "").includes("thinking time")) return menu;
      const text = normalize(menu.textContent ?? "");
      if (text.includes("standard") && text.includes("extended")) return menu;
    }
    return null;
  };

  const oldChip = findOldChip();
  if (oldChip) {
    dispatchClickSequence(oldChip);
    const start = performance.now();
    while (performance.now() - start < MAX_WAIT_MS) {
      await sleep(100);
      const menu = findOldEffortMenu();
      if (!menu) continue;
      const opt = findOptionInMenu(menu);
      if (!opt) {
        closeOpenMenus();
        return { status: "option-not-found", availableOptions: collectAvailableOptions() };
      }
      const already = optionIsSelected(opt);
      const label = opt.textContent?.trim?.() || null;
      dispatchClickSequence(opt);
      await sleep(STEP_WAIT_MS);
      closeOpenMenus();
      return { status: already ? "already-selected" : "switched", label, availableOptions: collectAvailableOptions() };
    }
    closeOpenMenus();
    return { status: "menu-not-found", availableOptions: collectAvailableOptions() };
  }

  const TRAILING_SELECTOR = '[data-model-picker-thinking-effort-action="true"]';
  const findModelButton = () => document.querySelector(MODEL_BUTTON_SELECTOR);
  const findTrailingButtons = () => Array.from(document.querySelectorAll(TRAILING_SELECTOR));
  const KIND_NOT_FOUND = { kindNotFound: true };
  const findEffortRow = (node) => {
    let current = node instanceof HTMLElement ? node.parentElement : null;
    while (current && current !== document.body) {
      if (current.getAttribute?.("data-model-picker-thinking-effort-row") === "true") {
        return current;
      }
      current = current.parentElement;
    }
    return null;
  };
  const rowIsSelected = (row) => {
    if (!(row instanceof HTMLElement)) return false;
    const modelItem = row.querySelector('[data-model-picker-thinking-effort-menu-item="true"], [role="menuitemradio"]');
    if (optionIsSelected(modelItem)) return true;
    const selectedSelector = [
      '[aria-checked="true"]',
      '[aria-selected="true"]',
      '[aria-current="true"]',
      '[data-selected="true"]',
      '[data-state="checked"]',
      '[data-state="selected"]',
      '[data-state="on"]',
    ].join(", ");
    return Boolean(
      row.querySelector(selectedSelector)
    );
  };
  const rowForTrailing = (trailing) =>
    trailing.closest('[role="menuitem"], [role="menuitemradio"], [data-radix-collection-item]');
  const rowTextForTrailing = (trailing) => {
    const row = rowForTrailing(trailing) || findEffortRow(trailing);
    return normalize(
      (row?.getAttribute?.("aria-label") ?? "") + " " +
      (row?.getAttribute?.("data-testid") ?? "") + " " +
      (row?.textContent ?? "") + " " +
      (trailing.getAttribute?.("aria-label") ?? "") + " " +
      (trailing.getAttribute?.("data-testid") ?? "")
    );
  };
  const testIdTextForTrailing = (trailing) => {
    const row = rowForTrailing(trailing) || findEffortRow(trailing);
    return normalize((row?.getAttribute?.("data-testid") ?? "") + " " + (trailing.getAttribute?.("data-testid") ?? ""));
  };
  const modelKindFromTrailing = (trailing) => {
    const idText = testIdTextForTrailing(trailing);
    if (!idText.includes("model switcher")) return null;
    const modelPart = normalize(idText.replace(/\bthinking effort\b.*$/, ""));
    if (hasToken(modelPart, "pro")) return "pro";
    if (hasToken(modelPart, "thinking")) return "thinking";
    if (hasToken(modelPart, "instant")) return "instant";
    return null;
  };
  const trailingMatchesTargetModelKind = (trailing) => {
    const idKind = modelKindFromTrailing(trailing);
    if (idKind) return idKind === TARGET_MODEL_KIND;
    const text = rowTextForTrailing(trailing);
    if (TARGET_MODEL_KIND === "pro") {
      return hasToken(text, "pro") && !hasToken(text, "thinking");
    }
    if (TARGET_MODEL_KIND === "thinking") {
      return hasToken(text, "thinking") && !hasToken(text, "pro");
    }
    if (TARGET_MODEL_KIND === "instant") {
      return hasToken(text, "instant") && !hasToken(text, "thinking") && !hasToken(text, "pro");
    }
    return false;
  };
  const hasStableBox = (node) => {
    const r = node.getBoundingClientRect?.();
    return Boolean(r && r.width > 0 && r.height > 0 && node.getAttribute?.("aria-hidden") !== "true");
  };
  const pickSingleStableTrailing = (trailings) => {
    const visible = trailings.filter((t) => hasStableBox(t));
    return visible.length === 1 ? visible[0] : null;
  };
  const pickTrailingForCurrentModel = () => {
    const trailings = findTrailingButtons();
    if (trailings.length === 0) return null;
    if (trailings.length === 1) return trailings[0];
    for (const t of trailings) {
      const row = findEffortRow(t);
      if (rowIsSelected(row)) return t;
    }
    const targetTrailings = trailings.filter((t) => trailingMatchesTargetModelKind(t));
    return pickSingleStableTrailing(targetTrailings) || KIND_NOT_FOUND;
  };

  const modelBtn = findModelButton();
  if (!modelBtn) {
    return { status: "chip-not-found", availableOptions: collectAvailableOptions() };
  }
  if (modelBtn.getAttribute("aria-expanded") !== "true") {
    dispatchClickSequence(modelBtn);
    await sleep(INITIAL_WAIT_MS);
  }

  let trailing = null;
  const trailingDeadline = performance.now() + MAX_WAIT_MS;
  while (performance.now() < trailingDeadline) {
    trailing = pickTrailingForCurrentModel();
    if (trailing) break;
    await sleep(100);
  }
  if (!trailing) {
    closeOpenMenus();
    return { status: "chip-not-found", availableOptions: collectAvailableOptions() };
  }
  if (trailing.kindNotFound) {
    closeOpenMenus();
    return {
      status: "model-kind-not-found",
      modelKind: TARGET_MODEL_KIND,
      availableOptions: collectAvailableOptions(),
    };
  }

  dispatchClickSequence(trailing);
  await sleep(STEP_WAIT_MS);
  const resolveEffortMenu = () => {
    const id = trailing.getAttribute("aria-controls");
    if (id) {
      const node = document.getElementById(id);
      if (node) return node;
    }
    const menus = document.querySelectorAll(MENU_CONTAINER_SELECTOR + ", [role=\"group\"]");
    let best = null;
    for (const menu of menus) {
      if (menu === modelBtn || menu.contains(trailing)) continue;
      const text = normalize(menu.textContent ?? "");
      let hits = 0;
      for (const tokens of Object.values(LEVEL_TOKENS)) {
        if (tokens.some((tok) => text.includes(String(tok).toLowerCase()))) hits += 1;
      }
      if (hits >= 2 && (!best || hits > best.hits)) best = { menu, hits };
    }
    return best?.menu ?? null;
  };

  let effortMenu = null;
  const effortDeadline = performance.now() + MAX_WAIT_MS;
  while (performance.now() < effortDeadline) {
    effortMenu = resolveEffortMenu();
    if (effortMenu) break;
    await sleep(100);
  }
  if (!effortMenu) {
    closeOpenMenus();
    return { status: "menu-not-found", availableOptions: collectAvailableOptions() };
  }
  const targetOption = findOptionInMenu(effortMenu);
  if (!targetOption) {
    closeOpenMenus();
    return { status: "option-not-found", availableOptions: collectAvailableOptions() };
  }

  const already = optionIsSelected(targetOption);
  const label = targetOption.textContent?.trim?.() || null;
  dispatchClickSequence(targetOption);
  await sleep(STEP_WAIT_MS);
  closeOpenMenus();
  return { status: already ? "already-selected" : "switched", label, availableOptions: collectAvailableOptions() };
})()
"""
    for key, value in replacements.items():
        expression = expression.replace(key, value)
    return expression
