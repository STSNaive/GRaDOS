"""ChatGPT DOM selectors aligned with GRaDOS ChatGPT browser constants."""

from __future__ import annotations

CHATGPT_URL = "https://chatgpt.com/"

INPUT_SELECTORS = [
    'textarea[data-id="prompt-textarea"]',
    'textarea[placeholder*="Send a message"]',
    'textarea[aria-label="Chat with ChatGPT"]',
    'textarea[aria-label="Message ChatGPT"]',
    "textarea:not([disabled])",
    'textarea[name="prompt-textarea"]',
    "#prompt-textarea",
    ".ProseMirror",
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"][data-virtualkeyboard="true"]',
]

ANSWER_SELECTORS = [
    'article[data-testid^="conversation-turn"][data-message-author-role="assistant"]',
    'article[data-testid^="conversation-turn"][data-turn="assistant"]',
    'article[data-testid^="conversation-turn"] [data-message-author-role="assistant"]',
    'article[data-testid^="conversation-turn"] [data-turn="assistant"]',
    'article[data-testid^="conversation-turn"] .markdown',
    '[data-message-author-role="assistant"] .markdown',
    '[data-turn="assistant"] .markdown',
    '[data-message-author-role="assistant"]',
    '[data-turn="assistant"]',
]

CONVERSATION_TURN_SELECTOR = (
    'article[data-testid^="conversation-turn"], div[data-testid^="conversation-turn"], '
    'section[data-testid^="conversation-turn"], article[data-message-author-role], '
    'div[data-message-author-role], section[data-message-author-role], article[data-turn], '
    'div[data-turn], section[data-turn]'
)
ASSISTANT_ROLE_SELECTOR = '[data-message-author-role="assistant"], [data-turn="assistant"]'

PROMPT_PRIMARY_SELECTOR = "#prompt-textarea"
PROMPT_FALLBACK_SELECTOR = 'textarea[name="prompt-textarea"]'
MENU_CONTAINER_SELECTOR = '[role="menu"], [data-radix-collection-root]'
MENU_ITEM_SELECTOR = 'button, [role="menuitem"], [role="menuitemradio"], [data-testid*="model-switcher-"]'
STOP_BUTTON_SELECTOR = '[data-testid="stop-button"]'
SEND_BUTTON_SELECTORS = [
    'button[data-testid="send-button"]',
    'button[data-testid*="composer-send"]',
    'form button[type="submit"]',
    'button[type="submit"][data-testid*="send"]',
    'button[aria-label*="Send"]',
]
MODEL_BUTTON_SELECTOR = '[data-testid="model-switcher-dropdown-button"], button.__composer-pill[aria-haspopup="menu"]'
COMPOSER_MODEL_SIGNAL_SELECTOR = '[data-testid="composer-footer-actions"]'
COPY_BUTTON_SELECTOR = 'button[data-testid="copy-turn-action-button"]'
FINISHED_ACTIONS_SELECTOR = (
    'button[data-testid="copy-turn-action-button"], '
    'button[data-testid="good-response-turn-action-button"], '
    'button[data-testid="bad-response-turn-action-button"], '
    'button[aria-label="Share"]'
)

CHATGPT_BROWSER_CHROME_FLAGS = [
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-default-apps",
    "--disable-hang-monitor",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-sync",
    "--disable-translate",
    "--metrics-recording-only",
    "--no-first-run",
    "--safebrowsing-disable-auto-update",
    "--disable-features=TranslateUI,AutomationControlled",
    "--mute-audio",
    "--window-size=1280,720",
    "--lang=en-US",
    "--accept-lang=en-US,en",
    "--password-store=basic",
    "--use-mock-keychain",
    "--new-window",
]
