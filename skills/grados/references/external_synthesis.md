# Optional ChatGPT Pro External Synthesis

Use this protocol only after `grados external-synthesis is-enabled --quiet` (or `uvx grados external-synthesis is-enabled --quiet` when using the plugin launcher) exits with code 0 under the same `GRADOS_HOME` as the active server. If the command is unavailable, fails, or exits nonzero, do not open Chrome, call ChatGPT, change evidence reading, or follow this protocol.

Config shape:

```json
{
  "research": {
    "external_synthesis": {
      "enabled": true
    }
  }
}
```

`enabled=true` is a host-side orchestration protocol, not a GRaDOS server call to ChatGPT Pro. GRaDOS prepares and verifies evidence; ChatGPT Pro can only review or synthesize compact, current-valid evidence packets derived from evidence packs.

Responsibility split:

- GRaDOS produces the verified evidence payload: evidence pack id, canonical anchors, paragraph windows, short excerpts, candidate claims, limitations, and verification status.
- The Codex host agent composes the exact ChatGPT prompt from the user task, the verified GRaDOS payload, and this protocol. GRaDOS does not generate free-form ChatGPT UI prompts as a server-side model step.
- The Codex host agent and Chrome extension handle browser state: opening or resuming the ChatGPT conversation, selecting the model, sending the prompt, and reading the reply.
- GRaDOS receives ChatGPT output only when the host passes it back explicitly with `save_external_synthesis_result`, which audits by default, followed by canonical rereads before any final citation.

Minimal deterministic workflow:

1. If no evidence pack id exists yet and the task starts from a topic, use `prepare_external_synthesis_from_topic`; it prepares the pack and persists the verified packet in one route while returning both pack and packet ids.
2. If a pack id already exists, call `prepare_external_synthesis_packet`. It verifies the source pack internally, refuses stale or non-current packs, persists `research_artifacts(kind="external_synthesis_packet")` with packet payload and prompt hash, then returns the host prompt as a regenerable view plus packet metadata. Use `verify_evidence_pack` separately only when you need a standalone current-valid status report for a restored or handoff pack.
3. Optional: preview the external packet with `preview_external_synthesis_packet` when you need a dry run, size estimate, or sendability check. Preview does not save artifacts, open Chrome, call ChatGPT, or use browser state.
4. The host sends the returned prompt to the selected ChatGPT Pro conversation.
5. Save the response with `save_external_synthesis_result`, including the source `pack_id`, optional `packet_artifact_id`, conversation URL/session locator, model label, thinking label, raw response, and any structured claims or gaps copied from the response. Keep `audit=true` unless you are doing recovery/debug work and will run `audit_external_synthesis_result` yourself.
6. The audit is mandatory before using external output. When a packet id is linked, audit uses only that packet's items as the allowed reference scope; without a packet id it falls back to the current-valid source pack. Treat only `ready_for_canonical_reread=true` outputs as candidates for the next writing step, then reread accepted canonical windows with `read_saved_paper` before final citation.

`external_synthesis_packet` and `external_synthesis_result` artifacts are recovery and audit material only. They are not final citation evidence.

Model selection and thinking strength are fixed by protocol: choose the latest/highest-capability Pro model visible in the current ChatGPT UI and the highest available thinking-time option. In localized UIs, choose by semantic meaning rather than exact English strings. Stop and report if those choices cannot be confirmed before sending evidence.

Use one ChatGPT conversation per GRaDOS workflow. Send one English protocol prompt, then append evidence packs, outlines, and claim-review requests to that same conversation. Store a recoverable conversation URL or identifier. If the page, tab, or extension backend is lost, recover that same conversation; if recovery fails, stop and report.

When both this protocol and the optional `codex` Chrome-extension download route are enabled, the host must coordinate a single shared Chrome resource. Only one Chrome task may be active at a time. Prefer `chrome_acquisition` first (publisher/DOI/PDF download, `ingest_codex_downloaded_pdf` or `parse_pdf_file`, canonical read), then `chrome_synthesis` (ChatGPT Pro review). If interleaving is unavoidable, keep publisher/PDF tabs separate from the ChatGPT tab and resume the same ChatGPT conversation.

Evidence sent to ChatGPT Pro should be minimal and verified. Each item should include `anchor_id`, DOI or `safe_doi`, `canonical_uri`, `paragraph_start`, `paragraph_count`, a short excerpt, candidate claim, and limitations. `prepare_external_synthesis_from_topic` and `prepare_external_synthesis_packet` build these items only from current-valid evidence packs. Do not send the full local paper library, unrelated full text, publisher/PDF pages, login state, download artifacts, or unverified web content.

Request structured output with `claims`, `anchor_ids`, `confidence`, `caveat`, and `missing_evidence` / `gaps`. ChatGPT Pro must not add papers, DOIs, facts, or citations that were not in the provided packet. After receiving the response, the host must save and audit it; `save_external_synthesis_result` does this by default. `audit_external_synthesis_result` treats structured `claims[].anchor_ids` as the primary handoff contract, flags unknown anchors/locators and outside DOIs, and keeps prose audit output as a risk scan; final citations may only use verified canonical paragraph windows.

Stop and report rather than silently degrading when Chrome extension is unavailable, Chrome resource state is inconsistent, the target model cannot be confirmed, the ChatGPT conversation cannot be recovered, ChatGPT adds outside evidence, the evidence pack is too large, or `verify_evidence_pack` returns `current_valid=false`.
