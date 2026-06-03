# Live Contract Checks

This file separates offline contract fixtures from manually triggered live checks for the highest-risk external chains.

## Offline Contract Fixtures

Run these in normal CI or local regression loops:

```bash
uv run pytest -q tests/test_contract_fixtures.py
```

Coverage in this fixture suite:

- Elsevier API fallback from broken or changed XML to metadata-only receipts with preserved asset hints
- Springer waterfall fallback across OA JATS XML -> HTML -> PDF
- Browser challenge detection when a PDF-looking response is actually HTML or anti-bot markup
- Local PDF import over nested directories with parser warnings, QA warnings, and DOI normalization noise

## Manual Live Checks

Run these only when validating real third-party behavior changes.

### Elsevier

Preconditions:

- `ELSEVIER_API_KEY` configured
- choose one DOI that should still expose XML full text
- choose one DOI that should only return metadata

Suggested checks:

1. `uv run grados extract-paper-full-text --doi <doi>`
2. verify receipt shows either `native_full_text` or explicit `metadata_only` fallback path
3. verify asset hints still include ScienceDirect or object metadata links when applicable

### Springer

Preconditions:

- `SPRINGER_meta_API_KEY` configured
- `SPRINGER_OA_API_KEY` configured if OA JATS should be exercised

Suggested checks:

1. extract one OA DOI and confirm XML or HTML normalization still produces canonical markdown
2. extract one DOI that falls through to direct PDF and confirm receipt still succeeds

### Browser Fetch

Preconditions:

- browser runtime prepared with `grados setup`
- choose one direct-PDF DOI, one publisher landing-page DOI that reaches PDF after navigation, and one publisher challenge DOI where the user can complete verification manually
- access to the MCP tool surface that exposes `extract_paper_full_text`

Suggested checks:

1. call `extract_paper_full_text` for each DOI sample
2. confirm browser path either captures a real PDF or surfaces `publisher_challenge`
3. for a slow or pending DOI, poll `get_operation_status(operation_id="doi:<DOI>", detail=true)` and confirm it resolves the DOI-bound `extract_paper_full_text` operation
4. inspect `grados browser status --json` and confirm the publisher profile is separate from the ChatGPT profile
5. inspect the `browser/pdf-sessions/<session>/session.json` record and confirm capture source is `response`, `download`, or `backfill`, not a direct `papers/*.md` write
6. if a browser download capture was not preceded by a dispatched download click, confirm capture metadata marks it as possibly assisted
7. if a challenge is surfaced, confirm the receipt or session trace includes `publisher_challenge`, `manual_interactive_wait_started`, and a retained page titled with `GRaDOS ACTION REQUIRED`
8. during the bounded wait, complete publisher verification in the managed browser profile and open the publisher PDF tab
9. confirm the same operation records `manual_user_opened_pdf_page` and either captures the PDF from response/download/CDP or records `capture_source="pdf_url_backfill_after_manual"`
10. confirm a successful retained capture releases the profile lock, while an unresolved retained challenge keeps the manual page available for user action
11. if the bounded wait expires before capture, confirm `Manual Browser Resume` includes host, URL/profile when available, `resume_browser=true` retry guidance, and exact-path recovery via `ingest_codex_downloaded_pdf(downloaded_file_path=...)` or `parse_pdf_file(file_path=..., doi=..., copy_to_library=true)`
12. confirm no HTML challenge page is saved as a PDF and canonical `papers/*.md` is written only after PDF materialization plus parser/QA checks

### Local Import

Preconditions:

- prepare a representative nested PDF directory with duplicates, one malformed file, and one no-DOI paper

Suggested checks:

1. `uv run grados import-pdfs --from <dir> --recursive`
2. confirm duplicate hash skip, malformed-PDF failure, local fallback DOI generation, and partial-success warnings

### ChatGPT Pro External Consult

Preconditions:

- `research.external_consult.enabled=true`
- `research.external_consult.response_wait_total_seconds=300` unless validating a different wait budget
- private GRaDOS ChatGPT profile is logged in via `grados external-consult setup-browser`
- access to a ChatGPT Pro model that can produce a response longer than one browser wait round

Suggested checks:

1. run `grados external-consult doctor --live` and confirm it reports consult-route readiness without sending a prompt
2. call `consult_chatgpt_pro` with a long prompt and default `wait_policy=auto`
3. confirm the prompt is submitted once and recovery calls use the saved browser session rather than resending
4. confirm the receipt metadata records `response_wait_total_seconds`, `per_attempt_wait_seconds`, `max_browser_wait_attempts`, and `auto_reattach_attempts`
5. confirm the total configured wait budget is split across the initial wait and bounded reattach/capture attempts
6. confirm final capture/save succeeds, or a pending receipt points to `get_operation_status(operation_id=..., detail=true)` and manual copy fallback
7. if the profile is intentionally logged out before submitting, confirm the consult fails with setup/doctor guidance rather than returning a recoverable pending receipt

## Update Rule

When a real provider regression is found:

1. add or tighten an offline fixture first if the behavior can be captured statically
2. update this file if the manual live-check recipe needs a new scenario
3. only then close the related TODO item
