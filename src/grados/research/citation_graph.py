"""Local citation graph helpers over the canonical paper store."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from grados.research.models import (
    CitationGraphEdge,
    CitationGraphItem,
    CitationGraphNode,
    CitationGraphResult,
    CitationGraphSummary,
    CitingPaperItem,
    CommonReferenceItem,
    LocalCitationRecord,
    _LocalCitationCacheEntry,
)
from grados.storage.chunking import extract_reference_dois, normalize_doi
from grados.storage.papers import list_saved_papers, load_paper_record
from grados.storage.paths import resolve_papers_dir

_LOCAL_CITATION_RECORDS_CACHE: dict[str, _LocalCitationCacheEntry] = {}


def _citation_records_signature(papers_dir: Path) -> tuple[tuple[str, int, int], ...]:
    if not papers_dir.is_dir():
        return ()

    signature: list[tuple[str, int, int]] = []
    for path in sorted(papers_dir.glob("*.md")):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((path.name, stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


def _load_local_citation_records(chroma_dir: Path) -> list[LocalCitationRecord]:
    papers_dir = resolve_papers_dir(chroma_dir)
    cache_bucket = str(papers_dir.resolve())
    signature = _citation_records_signature(papers_dir)
    cached = _LOCAL_CITATION_RECORDS_CACHE.get(cache_bucket)
    if cached is not None and cached.signature == signature:
        return list(cached.records)

    records: list[LocalCitationRecord] = []
    for item in list_saved_papers(papers_dir):
        safe_doi = item.safe_doi.strip()
        if not safe_doi:
            continue
        record = load_paper_record(papers_dir, safe_doi=safe_doi)
        if not record or not record.doi.strip():
            continue
        records.append(LocalCitationRecord(paper=record, cites=extract_reference_dois(record.content_markdown)))
    _LOCAL_CITATION_RECORDS_CACHE[cache_bucket] = _LocalCitationCacheEntry(
        signature=signature,
        records=tuple(records),
    )
    return records


def get_citation_graph(
    chroma_dir: Path,
    *,
    mode: str,
    doi: str = "",
    dois: list[str] | None = None,
    max_hops: int = 1,
    limit: int = 20,
) -> CitationGraphResult:
    """Return lightweight local citation relationships."""
    documents = _load_local_citation_records(chroma_dir)
    if not documents:
        return CitationGraphResult(mode=mode, targets=[], message="No saved papers found.")

    doc_by_doi = {
        normalize_doi(record.paper.doi): record
        for record in documents
        if record.paper.doi.strip()
    }
    outgoing = {
        key: [normalize_doi(value) for value in record.cites]
        for key, record in doc_by_doi.items()
    }
    incoming: dict[str, list[str]] = defaultdict(list)
    local_cites_by_doi: dict[str, list[str]] = {}
    external_cites_by_doi: dict[str, list[str]] = {}
    for src, cited in outgoing.items():
        local_cites: list[str] = []
        external_cites: list[str] = []
        for target in cited:
            incoming[target].append(src)
            if target in doc_by_doi:
                local_cites.append(target)
            else:
                external_cites.append(target)
        local_cites_by_doi[src] = local_cites
        external_cites_by_doi[src] = external_cites

    requested = [normalize_doi(value) for value in ([doi] + (dois or [])) if value and value.strip()]
    requested = list(dict.fromkeys(requested))
    resolved_targets = [value for value in requested if value in doc_by_doi]

    if mode == "common_references":
        if len(resolved_targets) < 2:
            return CitationGraphResult(
                mode=mode,
                targets=resolved_targets,
                common_references=[],
                message="Provide at least two locally saved DOIs for common reference analysis.",
            )
        common = set(outgoing.get(resolved_targets[0], []))
        for target in resolved_targets[1:]:
            common &= set(outgoing.get(target, []))
        items: list[CommonReferenceItem] = []
        for ref in sorted(common)[: max(1, min(limit, 100))]:
            saved = doc_by_doi.get(ref)
            items.append(
                CommonReferenceItem(
                    doi=ref,
                    title=saved.paper.title if saved else "",
                    is_saved_locally=saved is not None,
                    cited_by=resolved_targets,
                )
            )
        return CitationGraphResult(
            mode=mode,
            targets=resolved_targets,
            common_references=items,
        )

    if mode == "citing_papers":
        citing_items: list[CitingPaperItem] = []
        for target in requested:
            for src in incoming.get(target, []):
                record = doc_by_doi.get(src)
                if not record:
                    continue
                citing_items.append(
                    CitingPaperItem(
                        target_doi=target,
                        doi=record.paper.doi,
                        title=record.paper.title,
                        year=record.paper.year,
                        safe_doi=record.paper.safe_doi,
                    )
                )
        return CitationGraphResult(
            mode=mode,
            targets=requested,
            count=len(citing_items),
            items=citing_items[: max(1, min(limit, 100))],
        )

    seed_targets = resolved_targets or requested[:1]
    visited = set(seed_targets)
    frontier = list(seed_targets)
    edges: list[CitationGraphEdge] = []
    hops = 0
    while frontier and hops < max(1, min(max_hops, 3)):
        next_frontier: list[str] = []
        for current in frontier:
            for neighbor in outgoing.get(current, []):
                edges.append(CitationGraphEdge(source=current, target=neighbor, relation="cites"))
                if neighbor in doc_by_doi and neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
            for neighbor in incoming.get(current, []):
                edges.append(CitationGraphEdge(source=neighbor, target=current, relation="cites"))
                if neighbor in doc_by_doi and neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
        hops += 1

    nodes: list[CitationGraphNode] = []
    for node_doi in sorted(visited):
        record = doc_by_doi.get(node_doi)
        if not record:
            continue
        nodes.append(
            CitationGraphNode(
                doi=record.paper.doi,
                title=record.paper.title,
                year=record.paper.year,
                safe_doi=record.paper.safe_doi,
                cites_local_count=len(local_cites_by_doi.get(node_doi, [])),
                cited_by_local_count=len(incoming.get(node_doi, [])),
                cites_external_count=len(external_cites_by_doi.get(node_doi, [])),
            )
        )

    if seed_targets:
        target = seed_targets[0]
        cited_local = [doc_by_doi[value] for value in local_cites_by_doi.get(target, [])]
        cited_external = external_cites_by_doi.get(target, [])[:limit]
        cited_by = [doc_by_doi[value] for value in incoming.get(target, []) if value in doc_by_doi]
    else:
        cited_local = []
        cited_external = []
        cited_by = []

    return CitationGraphResult(
        mode="neighbors",
        targets=seed_targets,
        nodes=nodes[: max(1, min(limit * 2, 200))],
        edges=edges[: max(1, min(limit * 4, 400))],
        summary=CitationGraphSummary(
            cited_local=[
                CitationGraphItem(
                    doi=item.paper.doi,
                    title=item.paper.title,
                    safe_doi=item.paper.safe_doi,
                )
                for item in cited_local[:limit]
            ],
            cited_external=cited_external,
            cited_by_local=[
                CitationGraphItem(
                    doi=item.paper.doi,
                    title=item.paper.title,
                    safe_doi=item.paper.safe_doi,
                )
                for item in cited_by[:limit]
            ],
        ),
    )
