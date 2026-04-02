"""ChromaDB vector storage: index and search saved papers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_COLLECTION_NAME = "grados_papers"
_MAX_CHUNK_CHARS = 1000  # chunk size for embedding


def _get_client(chroma_dir: Path) -> Any:
    """Return a persistent ChromaDB client."""
    import chromadb

    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_dir))


def _get_collection(client: Any) -> Any:
    """Get or create the papers collection with default ONNX embedding."""
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from markdown."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].strip()
    return text


def _chunk_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks by paragraphs, respecting max_chars."""
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current:
        chunks.append(current)

    return chunks


def index_paper(
    chroma_dir: Path,
    doi: str,
    safe_doi: str,
    title: str,
    markdown: str,
) -> int:
    """Index a single paper into ChromaDB. Returns number of chunks indexed."""
    client = _get_client(chroma_dir)
    collection = _get_collection(client)

    # Remove old chunks for this DOI
    _delete_paper(collection, safe_doi)

    body = _strip_frontmatter(markdown)
    if not body or len(body) < 100:
        return 0

    chunks = _chunk_text(body)
    if not chunks:
        return 0

    ids = [f"{safe_doi}__chunk_{i}" for i in range(len(chunks))]
    metadatas = [{"doi": doi, "safe_doi": safe_doi, "title": title, "chunk_index": i} for i in range(len(chunks))]

    collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
    return len(chunks)


def search_papers(
    chroma_dir: Path,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Semantic search over indexed papers.

    Returns list of dicts: doi, safe_doi, title, score, snippet.
    Deduplicates by DOI (keeps best score per paper).
    """
    client = _get_client(chroma_dir)
    collection = _get_collection(client)

    if collection.count() == 0:
        return []

    # Query more chunks than limit to allow dedup
    n_results = min(limit * 3, collection.count())
    results = collection.query(query_texts=[query], n_results=n_results)

    # Deduplicate by DOI, keep best score
    seen: dict[str, dict[str, Any]] = {}
    distances = results.get("distances", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    for dist, doc, meta in zip(distances, documents, metadatas):
        doi = meta.get("doi", "")
        safe_doi = meta.get("safe_doi", "")
        # ChromaDB cosine distance: lower is better; convert to similarity
        score = 1.0 - dist

        if safe_doi in seen:
            if score > seen[safe_doi]["score"]:
                seen[safe_doi]["score"] = score
                seen[safe_doi]["snippet"] = doc[:200]
        else:
            seen[safe_doi] = {
                "doi": doi,
                "safe_doi": safe_doi,
                "title": meta.get("title", ""),
                "score": score,
                "snippet": doc[:200] if doc else "",
            }

    ranked = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:limit]


def index_all_papers(
    chroma_dir: Path,
    papers_dir: Path,
) -> tuple[int, int]:
    """Batch index all papers in papers_dir. Returns (papers_indexed, total_chunks)."""
    if not papers_dir.is_dir():
        return 0, 0

    total_papers = 0
    total_chunks = 0

    for md_file in sorted(papers_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8", errors="replace")
        safe_doi = md_file.stem

        # Parse frontmatter for DOI and title
        doi = safe_doi
        title = ""
        if content.startswith("---"):
            for line in content.split("\n"):
                if line.startswith("doi:"):
                    doi = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                elif line == "---" and doi != safe_doi:
                    break

        n = index_paper(chroma_dir, doi, safe_doi, title, content)
        if n > 0:
            total_papers += 1
            total_chunks += n

    return total_papers, total_chunks


def get_index_stats(chroma_dir: Path) -> dict[str, int]:
    """Return basic index stats."""
    try:
        client = _get_client(chroma_dir)
        collection = _get_collection(client)
        count = collection.count()
        # Count unique DOIs
        if count == 0:
            return {"total_chunks": 0, "unique_papers": 0}
        # Sample all to count unique
        all_meta = collection.get(limit=count)
        unique = len({m.get("safe_doi", "") for m in (all_meta.get("metadatas") or [])})
        return {"total_chunks": count, "unique_papers": unique}
    except Exception:
        return {"total_chunks": 0, "unique_papers": 0}


def _delete_paper(collection: Any, safe_doi: str) -> None:
    """Remove all chunks for a given safe_doi from the collection."""
    try:
        existing = collection.get(where={"safe_doi": safe_doi})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass
