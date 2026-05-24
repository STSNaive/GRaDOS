# Optional ChatGPT Pro External Synthesis

Use this protocol only after `grados external-synthesis is-enabled --quiet` (or `uvx grados external-synthesis is-enabled --quiet` when using the plugin launcher) exits with code 0 under the same `GRADOS_HOME` as the active server. If the command is unavailable, fails, or exits nonzero, do not use external synthesis.

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

`enabled=true` activates GRaDOS-native ChatGPT Pro browser mode for external synthesis. GRaDOS still sends only compact, current-valid evidence packets derived from evidence packs; ChatGPT output is advisory until GRaDOS saves, audits, and rereads accepted canonical windows.

First-time setup:

1. Run `grados external-synthesis setup-browser`.
2. Sign in to ChatGPT in the GRaDOS private Chrome profile.
3. Rerun `grados external-synthesis doctor --live` if you need a live login check.

The private profile is separate from the user's normal Chrome profile. GRaDOS does not copy cookies from the normal profile and does not require an OpenAI API key for this browser route.

Default workflow:

1. Prefer `run_external_synthesis` for enabled external synthesis. From a topic, it prepares a fresh evidence pack and packet; from a pack id, it verifies and packets that pack.
2. GRaDOS opens the private ChatGPT profile, verifies that the page is signed in, opens a fresh conversation for the workflow, opens the model picker, confirms GRaDOS-validated Pro model route (`gpt-5.5-pro`), confirms the Pro Extended thinking route, and only then sends the packet.
3. GRaDOS captures the final response, saves it with `save_external_synthesis_result(audit=true)`, and returns the audit result plus the canonical reread next action.
4. Use `preview_external_synthesis_packet`, `prepare_external_synthesis_from_topic`, `prepare_external_synthesis_packet`, `save_external_synthesis_result`, and `audit_external_synthesis_result` only for dry runs, recovery, and explicit reruns. Lower-level packet preparation persists `research_artifacts(kind="external_synthesis_packet")`.

`external_synthesis_packet` and `external_synthesis_result` artifacts are recovery and audit material only. They are not final citation evidence.

Model selection and thinking strength are fixed by protocol: GRaDOS follows the GRaDOS ChatGPT browser route by confirming the visible Pro model route (`gpt-5.5-pro`) and Pro Extended thinking before sending evidence. In localized UIs, GRaDOS records the raw UI labels it confirmed. Stop and report if those choices cannot be confirmed before sending evidence.

Evidence sent to ChatGPT Pro should be minimal and verified. Each item should include `anchor_id`, DOI or `safe_doi`, `canonical_uri`, `paragraph_start`, `paragraph_count`, a short excerpt, candidate claim, and limitations. Do not send the full local paper library, unrelated full text, publisher/PDF pages, login state, download artifacts, or unverified web content.

Request structured output with `claims`, `anchor_ids`, `confidence`, `caveat`, and `missing_evidence` / `gaps`. ChatGPT Pro must not add papers, DOIs, facts, or citations that were not in the provided packet. `audit_external_synthesis_result` treats structured `claims[].anchor_ids` as the primary handoff contract, flags unknown anchors/locators and outside DOIs, and keeps prose audit output as a risk scan; final citations may only use verified canonical paragraph windows.

This browser route replaces the old Codex Chrome extension/manual ChatGPT path for `research.external_synthesis`. It does not remove the separate optional `codex` Chrome-extension download route for PDF acquisition.

Stop and report rather than silently degrading when the private profile is not initialized, ChatGPT login is missing, the GRaDOS-validated Pro model route cannot be confirmed, Pro Extended thinking cannot be confirmed, the conversation cannot be recovered, ChatGPT adds outside evidence, the evidence packet is too large, or `verify_evidence_pack` returns `current_valid=false`.
