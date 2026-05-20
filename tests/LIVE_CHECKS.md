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
- choose one ScienceDirect DOI known to require interactive browser flow
- access to the MCP tool surface that exposes `extract_paper_full_text`

Suggested checks:

1. call `extract_paper_full_text` for that DOI
2. confirm browser path either captures a real PDF or surfaces `publisher_challenge`
3. inspect `grados browser status --json` and confirm the publisher profile is separate from the ChatGPT profile
4. inspect the `browser/pdf-sessions/<session>/session.json` record and confirm capture source is `response`, `download`, or `backfill`, not a direct `papers/*.md` write
5. if a challenge is surfaced, confirm the receipt includes `Manual Browser Resume` with host, URL/profile when available, and `resume_browser=true` retry guidance
6. complete publisher verification in the managed browser profile, then call `extract_paper_full_text` again with `resume_browser=true`
7. confirm the resumed attempt starts at the browser path, uses the saved URL/profile when present, and does not save an HTML challenge page as a PDF

### Local Import

Preconditions:

- prepare a representative nested PDF directory with duplicates, one malformed file, and one no-DOI paper

Suggested checks:

1. `uv run grados import-pdfs --from <dir> --recursive`
2. confirm duplicate hash skip, malformed-PDF failure, local fallback DOI generation, and partial-success warnings

## Update Rule

When a real provider regression is found:

1. add or tighten an offline fixture first if the behavior can be captured statically
2. update this file if the manual live-check recipe needs a new scenario
3. only then close the related TODO item
