from __future__ import annotations

from pathlib import Path

from grados.research_state import (
    append_research_run_event,
    create_research_run_manifest,
    manage_failure_cases,
    query_research_artifacts,
    read_research_run_manifest,
    save_research_artifact,
)


def test_research_artifacts_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite3"

    receipt = save_research_artifact(
        db_path,
        kind="evidence_grid",
        title="Composite Damping Grid",
        content={"topic": "composite damping", "rows": [{"doi": "10.1234/demo"}]},
        source_doi="10.1234/demo",
        metadata={"query": "composite damping"},
    )

    result = query_research_artifacts(db_path, kind="evidence_grid", detail=True)

    assert receipt["artifact_id"].startswith("artifact_")
    assert result["count"] == 1
    assert result["items"][0]["content"]["topic"] == "composite damping"
    assert result["items"][0]["source_doi"] == "10.1234/demo"


def test_research_run_manifest_links_artifacts_and_redacts_config(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite3"

    config_lock = {
        "search": {"order": ["Crossref"]},
        "api_keys": {"ELSEVIER_API_KEY": "secret-value"},
        "extract": {"fetchStrategy": {"order": ["api"]}, "auth": {"bearer": "extract-token"}},
        "research": {"authors": ["Smith", "Lee"]},
        "extra": {
            "headers": {
                "Authorization": "Bearer must-not-persist",
                "auth_header": "Bearer split-auth",
                "authorizationHeader": "Bearer camel-auth",
                "bearer_header": "Bearer split-bearer",
                "csrfToken": "csrf-token",
                "csrftoken": "csrftoken",
                "proxyAuthorization": "Bearer proxy-token",
                "sessionid": "session-id",
                "x_csrftoken": "x-csrf-token",
            }
        },
    }
    created = create_research_run_manifest(
        db_path,
        user_question="How do composites damp vibration?",
        search_queries=["composite damping"],
        config_lock=config_lock,
    )
    run_id = str(created["research_run_id"])
    artifact = save_research_artifact(
        db_path,
        kind="evidence_checkpoint",
        title="Claim anchors",
        content={"claim": "Composite damping improves attenuation."},
        source_doi="10.1234/demo",
        metadata={
            "research_run_id": run_id,
            "research_run_role": "evidence_checkpoint",
            "auth": {"bearer": "metadata-token"},
            "auth_header": "Bearer metadata-token",
            "authorizationHeader": "Bearer metadata-token",
            "bearer_header": "Bearer metadata-token",
            "sessionId": "session-id",
            "authors": ["Smith", "Lee"],
        },
    )
    appended = append_research_run_event(
        db_path,
        research_run_id=run_id,
        event_type="audit_run",
        source="audit_draft_support",
        artifact_id=str(artifact["artifact_id"]),
        payload={
            "token": "must-not-persist",
            "auth": {"bearer": "event-token"},
            "verdict_counts": {"verified": 1},
        },
    )
    loaded = read_research_run_manifest(db_path, research_run_id=run_id)

    assert appended["event"]["payload"]["token"] == "<redacted>"
    assert artifact["research_run"]["research_run_id"] == run_id
    assert artifact["metadata"]["auth"] == "<redacted>"
    assert artifact["metadata"]["auth_header"] == "<redacted>"
    assert artifact["metadata"]["authorizationHeader"] == "<redacted>"
    assert artifact["metadata"]["bearer_header"] == "<redacted>"
    assert artifact["metadata"]["sessionId"] == "<redacted>"
    assert artifact["metadata"]["authors"] == ["Smith", "Lee"]
    assert loaded["found"] is True
    manifest = loaded["manifest"]
    assert manifest["search_queries"] == ["composite damping"]
    assert manifest["config_lock"]["api_keys"] == "<redacted>"
    assert manifest["config_lock"]["extract"]["auth"] == "<redacted>"
    assert manifest["config_lock"]["research"]["authors"] == ["Smith", "Lee"]
    assert manifest["config_lock"]["extra"]["headers"]["Authorization"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["auth_header"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["authorizationHeader"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["bearer_header"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["csrfToken"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["csrftoken"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["proxyAuthorization"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["sessionid"] == "<redacted>"
    assert manifest["config_lock"]["extra"]["headers"]["x_csrftoken"] == "<redacted>"
    assert manifest["artifact_index"][0]["artifact_id"] == artifact["artifact_id"]
    assert manifest["artifact_index"][0]["metadata"]["auth"] == "<redacted>"
    assert manifest["artifact_index"][0]["metadata"]["auth_header"] == "<redacted>"
    assert manifest["artifact_index"][0]["metadata"]["authorizationHeader"] == "<redacted>"
    assert manifest["artifact_index"][0]["metadata"]["bearer_header"] == "<redacted>"
    assert manifest["artifact_index"][0]["metadata"]["sessionId"] == "<redacted>"
    assert manifest["artifact_index"][0]["metadata"]["authors"] == ["Smith", "Lee"]
    assert appended["event"]["payload"]["auth"] == "<redacted>"
    assert [event["event_type"] for event in manifest["event_ledger"]] == [
        "run_started",
        "artifact_saved",
        "audit_run",
    ]

    saved = query_research_artifacts(db_path, artifact_id=str(artifact["artifact_id"]), detail=True)
    assert saved["items"][0]["metadata"]["auth"] == "<redacted>"
    assert saved["items"][0]["metadata"]["auth_header"] == "<redacted>"
    assert saved["items"][0]["metadata"]["authorizationHeader"] == "<redacted>"
    assert saved["items"][0]["metadata"]["bearer_header"] == "<redacted>"
    assert saved["items"][0]["metadata"]["sessionId"] == "<redacted>"
    assert saved["items"][0]["metadata"]["authors"] == ["Smith", "Lee"]


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
