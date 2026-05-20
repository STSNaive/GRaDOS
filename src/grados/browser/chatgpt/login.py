"""ChatGPT login probes."""

from __future__ import annotations

import asyncio
from typing import Any

from grados.browser.chatgpt.errors import ChatGPTBrowserError
from grados.browser.chatgpt.selectors import CHATGPT_URL


async def ensure_chatgpt_logged_in(page: Any, *, timeout_ms: int = 5000) -> dict[str, object]:
    result = await probe_chatgpt_login(page, timeout_ms=timeout_ms)
    if result.get("ok"):
        return result
    raise ChatGPTBrowserError(
        code="chatgpt_login_required",
        stage="login",
        message="The GRaDOS private ChatGPT browser profile is not signed in.",
        details=result,
    )


async def probe_chatgpt_login(page: Any, *, timeout_ms: int = 5000) -> dict[str, object]:
    try:
        if not str(page.url).startswith(CHATGPT_URL):
            await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        result = await page.evaluate(_login_probe_expression(timeout_ms))
        if isinstance(result, dict):
            return {str(key): value for key, value in result.items()}
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "error": str(exc),
            "page_url": getattr(page, "url", ""),
        }
    return {
        "ok": False,
        "status": 0,
        "error": "invalid_login_probe_result",
        "page_url": getattr(page, "url", ""),
    }


async def wait_for_chatgpt_login(page: Any, *, timeout_seconds: float) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + max(1.0, timeout_seconds)
    last: dict[str, object] = {}
    while asyncio.get_running_loop().time() < deadline:
        last = await probe_chatgpt_login(page, timeout_ms=5000)
        if last.get("ok"):
            return last
        await asyncio.sleep(1.0)
    return {
        **last,
        "ok": False,
        "error": str(last.get("error") or "chatgpt_login_timeout"),
    }


def _login_probe_expression(timeout_ms: int) -> str:
    return f"""(async () => {{
      const pageUrl = typeof location === 'object' && location?.href ? location.href : null;
      const onAuthPage =
        typeof location === 'object' &&
        typeof location.pathname === 'string' &&
        /^\\/(auth|login|signin)/i.test(location.pathname);

      const hasLoginCta = () => {{
        const candidates = Array.from(
          document.querySelectorAll(
            [
              'a[href*="/auth/login"]',
              'a[href*="/auth/signin"]',
              'button[type="submit"]',
              'button[data-testid*="login"]',
              'button[data-testid*="log-in"]',
              'button[data-testid*="sign-in"]',
              'button[data-testid*="signin"]',
              'button',
              'a',
            ].join(','),
          ),
        );
        const textMatches = (text) => {{
          if (!text) return false;
          const normalized = text.toLowerCase().trim();
          return (
            ['log in', 'login', 'sign in', 'signin', 'sign up for free'].includes(normalized) ||
            normalized.startsWith('continue with') ||
            normalized.includes('get responses tailored to you') ||
            normalized.includes('log in to get answers')
          );
        }};
        for (const node of candidates) {{
          if (!(node instanceof HTMLElement)) continue;
          const rect = node.getBoundingClientRect();
          const style = window.getComputedStyle(node);
          if (
            rect.width <= 0 ||
            rect.height <= 0 ||
            style.display === 'none' ||
            style.visibility === 'hidden'
          ) {{
            continue;
          }}
          const label =
            node.textContent?.trim() ||
            node.getAttribute('aria-label') ||
            node.getAttribute('title') ||
            '';
          if (textMatches(label)) {{
            return true;
          }}
        }}
        return false;
      }};

      const readBackendStatus = async () => {{
        try {{
          if (typeof fetch === 'function') {{
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), {timeout_ms});
            try {{
              const response = await fetch('/backend-api/me', {{
                cache: 'no-store',
                credentials: 'include',
                signal: controller.signal,
              }});
              return {{ status: response.status || 0, error: null }};
            }} finally {{
              clearTimeout(timeout);
            }}
          }}
        }} catch (err) {{
          return {{ status: 0, error: err ? String(err) : 'unknown' }};
        }}
        return {{ status: 0, error: null }};
      }};

      let {{ status, error }} = await readBackendStatus();
      let domLoginCta = hasLoginCta();
      const settleDeadline = Date.now() + Math.min({timeout_ms}, 2500);
      while (!domLoginCta && Date.now() < settleDeadline) {{
        await new Promise((resolve) => setTimeout(resolve, 100));
        domLoginCta = hasLoginCta();
        if (status === 0 || status === 401 || status === 403) {{
          const next = await readBackendStatus();
          status = next.status;
          error = next.error;
        }}
      }}

      const loginSignals = domLoginCta || onAuthPage;
      return {{
        ok: !loginSignals && status === 200,
        status,
        redirected: false,
        url: pageUrl,
        page_url: pageUrl,
        dom_login_cta: domLoginCta,
        on_auth_page: onAuthPage,
        error,
      }};
    }})()"""
