"""Search-side retrieval helpers for canonical storage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from grados.storage.chroma_client import filter_query_result, query_collection
from grados.storage.chunking import DOI_PATTERN, split_paragraphs


@dataclass(frozen=True)
class PaperSearchResult:
    doi: str
    safe_doi: str
    title: str
    authors: list[str]
    year: str = ""
    journal: str = ""
    source: str = ""
    score: float = 0.0
    dense_score: float = 0.0
    doc_dense_score: float = 0.0
    chunk_dense_score: float = 0.0
    lexical_score: float = 0.0
    section_name: str = ""
    section_level: int = 0
    paragraph_start: int = 0
    paragraph_count: int = 0
    snippet: str = ""


@dataclass(frozen=True)
class ChunkWindowCandidate:
    paragraph_start: int
    paragraph_count: int
    score: float
    dense_score: float
    doc_dense_score: float
    chunk_dense_score: float
    lexical_score: float
    section_name: str = ""
    section_level: int = 0


@dataclass(frozen=True)
class MergedChunkWindow:
    paragraph_start: int
    paragraph_count: int
    score: float
    dense_score: float
    doc_dense_score: float
    chunk_dense_score: float
    lexical_score: float
    section_name: str = ""
    section_level: int = 0


def merge_chunk_windows(candidates: list[ChunkWindowCandidate]) -> MergedChunkWindow | None:
    if not candidates:
        return None

    sorted_candidates = sorted(candidates, key=lambda item: (item.paragraph_start, item.paragraph_count))
    clusters: list[list[ChunkWindowCandidate]] = []
    current_cluster: list[ChunkWindowCandidate] = []
    current_end = -1

    for candidate in sorted_candidates:
        start = max(0, candidate.paragraph_start)
        end = start + max(0, candidate.paragraph_count)
        if not current_cluster:
            current_cluster = [candidate]
            current_end = end
            continue
        if start <= current_end:
            current_cluster.append(candidate)
            current_end = max(current_end, end)
            continue
        clusters.append(current_cluster)
        current_cluster = [candidate]
        current_end = end

    if current_cluster:
        clusters.append(current_cluster)

    def build_cluster(cluster: list[ChunkWindowCandidate]) -> MergedChunkWindow:
        start = min(max(0, item.paragraph_start) for item in cluster)
        end = max(max(0, item.paragraph_start) + max(0, item.paragraph_count) for item in cluster)
        best = max(cluster, key=lambda item: item.score)
        return MergedChunkWindow(
            paragraph_start=start,
            paragraph_count=max(0, end - start),
            score=max(item.score for item in cluster),
            dense_score=max(item.dense_score for item in cluster),
            doc_dense_score=max(item.doc_dense_score for item in cluster),
            chunk_dense_score=max(item.chunk_dense_score for item in cluster),
            lexical_score=max(item.lexical_score for item in cluster),
            section_name=best.section_name,
            section_level=best.section_level,
        )

    merged = [build_cluster(cluster) for cluster in clusters]
    return max(merged, key=lambda item: (item.score, item.paragraph_count))


def build_search_result(
    *,
    record: dict[str, Any],
    safe_doi: str,
    score: float,
    dense_score: float,
    doc_dense_score: float,
    chunk_dense_score: float,
    lexical_score: float,
    section_name: str = "",
    section_level: int = 0,
    paragraph_start: int = 0,
    paragraph_count: int = 0,
    snippet: str = "",
) -> PaperSearchResult:
    return PaperSearchResult(
        doi=str(record.get("doi", "")),
        safe_doi=safe_doi,
        title=str(record.get("title", "")),
        authors=[str(value) for value in record.get("authors", []) if str(value)],
        year=str(record.get("year", "")),
        journal=str(record.get("journal", "")),
        source=str(record.get("source", "")),
        score=score,
        dense_score=dense_score,
        doc_dense_score=doc_dense_score,
        chunk_dense_score=chunk_dense_score,
        lexical_score=lexical_score,
        section_name=section_name,
        section_level=section_level,
        paragraph_start=paragraph_start,
        paragraph_count=paragraph_count,
        snippet=snippet,
    )


def select_doc_candidates(
    *,
    docs_collection: Any,
    filtered_documents: list[dict[str, Any]],
    candidate_ids: set[str],
    query_embedding: list[float],
    anchor_phrase: str,
    limit: int,
) -> dict[str, float]:
    total_docs = len(filtered_documents)
    if total_docs <= 0:
        return {}

    doc_limit = min(max(limit * 8, 30), total_docs)
    result = _query_docs(
        collection=docs_collection,
        query_embedding=query_embedding,
        n_results=doc_limit,
        candidate_doc_ids=list(candidate_ids),
        anchor_phrase=anchor_phrase,
    )
    if anchor_phrase and not result.get("documents", [[]])[0]:
        result = _query_docs(
            collection=docs_collection,
            query_embedding=query_embedding,
            n_results=doc_limit,
            candidate_doc_ids=list(candidate_ids),
        )

    scores: dict[str, float] = {}
    distances = result.get("distances", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]

    for dist, metadata in zip(distances, metadatas):
        safe_doi = str(metadata.get("safe_doi", ""))
        if safe_doi not in candidate_ids:
            continue
        scores[safe_doi] = max(scores.get(safe_doi, 0.0), dense_score(dist))

    for record in filtered_documents:
        if len(scores) >= doc_limit:
            break
        safe_doi = record["safe_doi"]
        if safe_doi in scores:
            continue
        scores[safe_doi] = 0.0

    return scores


def query_chunks(
    *,
    collection: Any,
    query_embedding: list[float],
    n_results: int,
    candidate_doc_ids: list[str],
    anchor_phrase: str = "",
) -> dict[str, Any]:
    where = {"safe_doi": {"$in": candidate_doc_ids}} if candidate_doc_ids else None
    result = query_collection(
        collection=collection,
        query_embedding=query_embedding,
        n_results=n_results,
        where=where,
        where_document={"$contains": anchor_phrase} if anchor_phrase else None,
    )

    metadatas = result.get("metadatas", [[]])[0]
    if not candidate_doc_ids or not metadatas:
        return result

    allowed = set(candidate_doc_ids)
    filtered_positions = [
        index
        for index, metadata in enumerate(metadatas)
        if str(metadata.get("safe_doi", "")) in allowed
    ]
    if len(filtered_positions) == len(metadatas):
        return result
    return filter_query_result(result, filtered_positions)


def matches_filters(
    document: dict[str, Any],
    doi: str,
    authors: str,
    year_from: int | None,
    year_to: int | None,
    journal: str,
    source: str,
) -> bool:
    if doi and document.get("doi", "").lower() != doi.lower():
        return False

    if authors:
        author_query = authors.lower()
        author_values = [str(author).lower() for author in document.get("authors", [])]
        if not any(author_query in author for author in author_values):
            return False

    if journal and journal.lower() not in str(document.get("journal", "")).lower():
        return False

    if source and source.lower() not in str(document.get("source", "")).lower():
        return False

    year_value = _coerce_year(document.get("year"))
    if year_from is not None and (year_value is None or year_value < year_from):
        return False
    if year_to is not None and (year_value is None or year_value > year_to):
        return False

    return True


def extract_anchor_phrase(query: str) -> str:
    quoted: list[str] = re.findall(r'"([^"]+)"', query)
    if quoted:
        return quoted[0].strip()
    match = DOI_PATTERN.search(query)
    if match:
        return match.group(0).strip()
    return ""


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "about",
    "study",
    "paper",
}


def query_terms(query: str) -> list[str]:
    terms = re.findall(r"[a-zA-Z0-9./_-]+", query.lower())
    return [term for term in terms if len(term) >= 3 and term not in _STOPWORDS][:8]


def lexical_score(text: str, query_terms: list[str], anchor_phrase: str) -> float:
    haystack = text.lower()
    score = 0.0
    if anchor_phrase and anchor_phrase.lower() in haystack:
        score += 1.5
    for term in query_terms:
        if term in haystack:
            score += 0.3
    return score


def dense_score(distance: Any) -> float:
    try:
        return max(0.0, 1.0 - float(distance))
    except (TypeError, ValueError):
        return 0.0


def combine_scores(dense_score_value: float, lexical_score_value: float, use_reranking: bool) -> float:
    if not use_reranking:
        return dense_score_value
    return dense_score_value + lexical_score_value


def make_snippet(text: str, query_terms: list[str], anchor_phrase: str, max_chars: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""

    anchors = [anchor_phrase.lower()] if anchor_phrase else []
    anchors.extend(query_terms)
    lower = compact.lower()
    for anchor in anchors:
        if not anchor:
            continue
        position = lower.find(anchor)
        if position == -1:
            continue
        start = max(0, position - 60)
        end = min(len(compact), position + max_chars)
        snippet = compact[start:end].strip()
        return ("..." if start > 0 else "") + snippet + ("..." if end < len(compact) else "")

    return compact[:max_chars] + ("..." if len(compact) > max_chars else "")


def paragraph_window_for_query(text: str, query_terms: list[str], anchor_phrase: str) -> tuple[int, int]:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return 0, 0

    paragraph_scores = [lexical_score(paragraph, query_terms, anchor_phrase) for paragraph in paragraphs]
    best_index = max(range(len(paragraphs)), key=lambda index: paragraph_scores[index])
    best_score = paragraph_scores[best_index]
    if best_score <= 0:
        return 0, 0

    start = best_index
    end = best_index

    while start > 0 and paragraph_scores[start - 1] > 0:
        start -= 1
    while end + 1 < len(paragraphs) and paragraph_scores[end + 1] > 0:
        end += 1

    if start > 0 and re.match(r"^#{1,6}\s+", paragraphs[start - 1]):
        start -= 1

    return start, (end - start) + 1


def _coerce_year(value: Any) -> int | None:
    raw = str(value or "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _query_docs(
    *,
    collection: Any,
    query_embedding: list[float],
    n_results: int,
    candidate_doc_ids: list[str],
    anchor_phrase: str = "",
) -> dict[str, Any]:
    return query_collection(
        collection=collection,
        query_embedding=query_embedding,
        n_results=n_results,
        where={"safe_doi": {"$in": candidate_doc_ids}} if candidate_doc_ids else None,
        where_document={"$contains": anchor_phrase} if anchor_phrase else None,
    )
