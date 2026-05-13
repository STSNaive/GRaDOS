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

`enabled=true` is a host-side orchestration protocol, not a GRaDOS server call to ChatGPT Pro. GRaDOS prepares and verifies evidence; ChatGPT Pro can only review or synthesize compact evidence packs.

Responsibility split:

- GRaDOS produces the verified evidence payload: evidence pack id, canonical anchors, paragraph windows, short excerpts, candidate claims, limitations, and verification status.
- The Codex host agent composes the exact ChatGPT prompt from the user task, the verified GRaDOS payload, and this protocol. GRaDOS does not generate free-form ChatGPT UI prompts as a server-side model step.
- The Codex host agent and Chrome extension handle browser state: opening or resuming the ChatGPT conversation, selecting the model, sending the prompt, and reading the reply.
- GRaDOS receives ChatGPT output only when the host passes it back explicitly, for example with `save_research_artifact(kind="external_synthesis_review", ...)`, followed by `audit_answer_against_pack` or canonical rereads before any final citation.

Model selection and thinking strength are fixed by protocol: choose the latest/highest-capability Pro model visible in the current ChatGPT UI and the highest available thinking-time option. In localized UIs, choose by semantic meaning rather than exact English strings. Stop and report if those choices cannot be confirmed before sending evidence.

Use one ChatGPT conversation per GRaDOS workflow. Send one English protocol prompt, then append evidence packs, outlines, and claim-review requests to that same conversation. Store a recoverable conversation URL or identifier. If the page, tab, or extension backend is lost, recover that same conversation; if recovery fails, stop and report.

When both this protocol and the optional `codex` Chrome-extension download route are enabled, the host must coordinate a single shared Chrome resource. Only one Chrome task may be active at a time. Prefer `chrome_acquisition` first (publisher/DOI/PDF download, `ingest_codex_downloaded_pdf` or `parse_pdf_file`, canonical read), then `chrome_synthesis` (ChatGPT Pro review). If interleaving is unavoidable, keep publisher/PDF tabs separate from the ChatGPT tab and resume the same ChatGPT conversation.

Evidence sent to ChatGPT Pro should be minimal and verified. Each item should include `anchor_id`, DOI or `safe_doi`, `canonical_uri`, `paragraph_start`, `paragraph_count`, a short excerpt, candidate claim, and limitations. Do not send the full local paper library, unrelated full text, publisher/PDF pages, login state, download artifacts, or unverified web content.

Request structured output with `claims`, `anchor_ids`, `confidence`, `caveat`, and `missing_evidence` / `gaps`. ChatGPT Pro must not add papers, DOIs, facts, or citations that were not in the provided pack. After receiving the response, the host must verify every claim with `read_saved_paper` or `verify_evidence_pack`; final citations may only use verified canonical paragraph windows.

Stop and report rather than silently degrading when Chrome extension is unavailable, Chrome resource state is inconsistent, the target model cannot be confirmed, the ChatGPT conversation cannot be recovered, ChatGPT adds outside evidence, the evidence pack is too large, or `verify_evidence_pack` returns `current_valid=false`.
