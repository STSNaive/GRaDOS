from __future__ import annotations

from grados.research.evidence_eligibility import (
    classify_evidence_rejection,
    is_backmatter_section,
    is_citation_fragment,
    is_evidence_eligible,
    is_non_evidence_section,
    is_title_only_or_empty,
)


def test_evidence_eligibility_rejects_backmatter_and_fragments() -> None:
    assert is_backmatter_section("Cited References")
    assert classify_evidence_rejection("References", "Smith et al. 2024. DOI 10.1234/demo") == "backmatter_section"
    assert classify_evidence_rejection("Results", "# Results") == "title_only"
    assert classify_evidence_rejection("", "Demo Paper", known_title="Demo Paper") == "title_only"
    assert classify_evidence_rejection("Results", "(Smith et al., 2024)") == "citation_fragment"
    assert is_citation_fragment("[12]")
    assert is_title_only_or_empty("## Methods", "Methods")
    assert is_non_evidence_section("Funding")
    assert classify_evidence_rejection("", "Authors: Alice Smith, Bob Lee") == "author_line"
    assert classify_evidence_rejection("", "DOI: 10.1234/demo") == "doi_only"
    assert classify_evidence_rejection("Journal", "Composite Structures") == "journal_only"
    assert (
        classify_evidence_rejection(
            "",
            "Title: Demo Paper\nAuthors: Alice Smith\nJournal: Composite Structures",
        )
        == "metadata_only"
    )


def test_evidence_eligibility_accepts_substantive_body_text() -> None:
    assert is_evidence_eligible(
        "Results",
        "Composite damping improved vibration attenuation by 18%.",
    )
