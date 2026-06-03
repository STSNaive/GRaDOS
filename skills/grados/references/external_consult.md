# Optional ChatGPT Pro External Consult

Use this protocol only after `grados external-consult is-enabled --quiet` (or `uvx grados external-consult is-enabled --quiet` when using the plugin launcher) exits with code 0 under the same `GRADOS_HOME` as the active server. If the command is unavailable, fails, or exits nonzero, do not use external consult.

Config shape:

```json
{
  "research": {
    "external_consult": {
      "enabled": true,
      "response_wait_total_seconds": 300
    }
  }
}
```

`enabled=true` activates GRaDOS-native ChatGPT Pro browser consult mode. `response_wait_total_seconds` is the total response wait budget counted from initial prompt submission; GRaDOS derives the per-attempt wait and reattach count from it. ChatGPT output is always advisory: it may be saved as a consult result or pack-linked external consult result, but final claims still require GRaDOS canonical rereading or current-valid evidence-pack audit.

First-time setup:

1. Run `grados external-consult setup-browser`.
2. Sign in to ChatGPT in the GRaDOS private Chrome profile.
3. Rerun `grados external-consult doctor --live` if you need a live login check.

The private profile is separate from the user's normal Chrome profile. GRaDOS does not copy cookies from the normal profile and does not require an OpenAI API key for this browser route.

Default workflow:

1. Prefer `consult_chatgpt_pro` for enabled external consult. It requires `prompt`; `pack_id`, `packet_id`, `context_artifact_ids`, and `context_paths` are optional bounded context sources.
2. GRaDOS opens the private ChatGPT profile, verifies login, starts a fresh conversation, applies the requested model strategy (`select`, `current`, or `ignore`) and thinking strategy (`highest`, `current`, or `ignore`), and sends the prompt once.
3. GRaDOS persists the session id, prompt hash, context manifest, strategy results, recoverable `/c/<id>` conversation URL when available, transcript/snapshot/capture paths, status, and recovery metadata. Home/project shell URLs are retained only as `last_observed_url` diagnostics.
4. Default `wait_policy="auto"` uses the configured total response wait budget, split across the initial send and bounded reattach/capture attempts inside the same operation before returning pending. If generation is still running, the receipt includes `operation_id`, `browser_session_id`, `conversation_url` or `last_observed_url`, recent attempts, and `next_action=get_operation_status`.
5. For pending runs, call `get_operation_status(operation_id=..., detail=true)` to continue bounded reattach and capture the final response without resending the prompt. `detail=false` remains read-only; detail recovery uses the configured wait budget for that recovery call.
6. If automatic capture fails but the assistant answer is visible, copy the answer manually and call `consult_chatgpt_pro` again with the original `recover_session_id` plus `manual_response`. GRaDOS saves that pasted response as `manual_copy`, keeps the session/conversation metadata, and writes transcript/snapshot artifacts without reopening the browser.
7. `run_external_consult` is the topic-or-pack packet-preparation route. Prompt-only callers should use `consult_chatgpt_pro` directly.
8. Use `preview_external_consult_packet`, `prepare_external_consult_from_topic`, `prepare_external_consult_packet`, `save_external_consult_result`, and `audit_external_consult_result` only for dry runs, lower-level recovery, explicit result save, or explicit audit. Lower-level packet preparation persists `research_artifacts(kind="external_consult_packet")`.

`chatgpt_pro_consult_result`, `external_consult_packet`, and `external_consult_result` artifacts are advisory, recovery, and audit material only. They are not final citation evidence.

Model and thinking strategies:

- `model_strategy="select"` opens the model picker and confirms the visible Pro target; `current` records the current visible label without switching; `ignore` skips inspection and writes a warning.
- `thinking_strategy="highest"` selects the highest visible thinking effort; `current` records the current raw label without switching; `ignore` skips inspection and writes a warning.
- Never treat an account-level Pro badge as evidence that the requested model is selected. In localized UIs, keep raw labels and warnings instead of inventing normalized success.

Context sent to ChatGPT Pro should be minimal and verified. Use optional artifacts, files, packs, or packets only when they are relevant. Do not send the full local paper library, cookies, browser pages, publisher HTML, download artifacts, unrelated full text, or unverified web content.

If the consult output will affect claims, explicitly audit or reread afterward. `audit_external_consult_result` treats structured `claims[].anchor_ids` as the primary handoff contract when an external consult result is linked to a packet; final citations may only use verified canonical paragraph windows.

This browser route is gated by `research.external_consult`. It does not remove the separate optional `codex` Chrome-extension download route for PDF acquisition.

MCP progress or cancellation is only UI polish. The timeout-safety contract is durable session state plus pending receipts and `get_operation_status`; do not solve long generations by resending the same prompt or only increasing a timeout.

Stop and report rather than silently degrading when the private profile is not initialized, the prompt/context is too large, selected strategy cannot be satisfied, the conversation cannot be recovered, ChatGPT adds outside evidence, or a context evidence pack is not current-valid.
