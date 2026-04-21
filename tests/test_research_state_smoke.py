from __future__ import annotations

from pathlib import Path

from grados.research_state import manage_failure_cases, query_research_artifacts, save_research_artifact


def test_research_artifacts_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite3"

    receipt = save_research_artifact(
        db_path,
        kind="evidence_table",
        title="Composite Damping Grid",
        content={"topic": "composite damping", "rows": [{"doi": "10.1234/demo"}]},
        source_doi="10.1234/demo",
        metadata={"query": "composite damping"},
    )

    result = query_research_artifacts(db_path, kind="evidence_table", detail=True)

    assert receipt["artifact_id"].startswith("artifact_")
    assert result["count"] == 1
    assert result["items"][0]["content"]["topic"] == "composite damping"
    assert result["items"][0]["source_doi"] == "10.1234/demo"


def test_failure_memory_records_queries_and_suggests_retry(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite3"

    recorded = manage_failure_cases(
        db_path,
        mode="record",
        failure_type="fetch",
        doi="10.1234/demo",
        query_text="composite damping",
        source="Elsevier TDM",
        error_message="403 paywall",
        context={"stage": "extract"},
    )
    queried = manage_failure_cases(db_path, mode="query", failure_type="fetch")
    suggestion = manage_failure_cases(
        db_path,
        mode="suggest_retry",
        failure_type="fetch",
        doi="10.1234/demo",
        query_text="composite damping",
        source="Elsevier TDM",
        error_message="403 paywall",
    )

    assert recorded["failure_id"].startswith("failure_")
    assert queried["count"] == 1
    assert queried["items"][0]["context"]["stage"] == "extract"
    assert any("browser-assisted extraction" in item for item in suggestion["suggestions"])
